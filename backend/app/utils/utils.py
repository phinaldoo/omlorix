"""Shared utility functions for the backend."""
from typing import Any, Iterable
import json
from datetime import datetime, timezone
import threading
import requests
import logging
from collections.abc import Mapping

import bleach
from bleach.sanitizer import (
    ALLOWED_TAGS as BLEACH_ALLOWED_TAGS,
    ALLOWED_ATTRIBUTES as BLEACH_ALLOWED_ATTRIBUTES,
    ALLOWED_PROTOCOLS as BLEACH_ALLOWED_PROTOCOLS,
)
try:  # Bleach >= 6 keeps CSSSanitizer in bleach.css_sanitizer
    from bleach.css_sanitizer import CSSSanitizer  # type: ignore[attr-defined]
except ImportError:
    try:  # Bleach < 6 exposed CSSSanitizer from bleach.sanitizer
        from bleach.sanitizer import CSSSanitizer  # type: ignore[attr-defined]
    except ImportError:
        CSSSanitizer = None  # type: ignore[assignment]
from app.database import SessionLocal
from app.logging.models import create_admin_notification
from app.settings.utils import coerce_bool, get_value_by_page_and_key, update_page_key_value_by_page_and_key
from app.utils.background import worker_manager
from app.utils.privacy_policy_template import PRIVACY_POLICY_TEMPLATE
from app.utils.terms_of_service_template import TERMS_OF_SERVICE_TEMPLATE
from app.utils.versioning import compare_semantic_versions, is_beta_version
from app.version import APP_VERSION_TAG


logger = logging.getLogger(__name__)

_VERSION_CHECK_URL = "https://api.github.com/repos/phinaldoo/omlorix/releases"
_VERSION_CHECK_TIMEOUT = 5.0
_VERSION_CHECK_RELEASE_LIMIT = 100
_CURRENT_VERSION = APP_VERSION_TAG
_latest_available_version: str | None = None
_version_lock = threading.Lock()


def _get_persisted_notified_version() -> str | None:
    """Return the last version tag we already announced (persisted in settings)."""

    db = SessionLocal()
    try:
        try:
            value = get_value_by_page_and_key("status", "latest_version_notified", db)
        except Exception:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass


def _persist_notified_version(tag: str) -> None:
    """Persist the last announced version tag so we do not spam admins."""

    db = SessionLocal()
    try:
        update_page_key_value_by_page_and_key("status", "latest_version_notified", tag, db)
    except Exception:
        logger.warning("[Version] Failed to persist latest_version_notified", exc_info=True)
    finally:
        try:
            db.close()
        except Exception:
            pass


CHAT_MESSAGE_SANITIZE_ROLES = {"user", "system"}
CHAT_ALLOWED_TAGS = BLEACH_ALLOWED_TAGS.union(
    {
        "p",
        "pre",
        "code",
        "span",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "hr",
        "br",
        # Allow inline SVG snippets (e.g., icons shared in chat prompts)
        "svg",
        "path",
        "title",
        "g",
        "circle",
        "rect",
        "line",
        "polyline",
        "polygon",
        "ellipse",
    }
)

SVG_ALLOWED_ATTRIBUTES = {
    "svg": [
        "xmlns",
        "viewBox",
        "width",
        "height",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "style",
        "role",
        "focusable",
        "aria-hidden",
    ],
    "path": [
        "d",
        "fill",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "opacity",
        "transform",
    ],
    "g": ["fill", "stroke", "stroke-width", "opacity", "transform"],
    "circle": ["cx", "cy", "r", "fill", "stroke", "stroke-width", "opacity", "transform"],
    "rect": [
        "x",
        "y",
        "width",
        "height",
        "rx",
        "ry",
        "fill",
        "stroke",
        "stroke-width",
        "opacity",
        "transform",
    ],
    "line": ["x1", "y1", "x2", "y2", "stroke", "stroke-width", "opacity", "transform"],
    "polyline": ["points", "fill", "stroke", "stroke-width", "opacity", "transform"],
    "polygon": ["points", "fill", "stroke", "stroke-width", "opacity", "transform"],
    "ellipse": [
        "cx",
        "cy",
        "rx",
        "ry",
        "fill",
        "stroke",
        "stroke-width",
        "opacity",
        "transform",
    ],
    "title": ["lang"],
}

CHAT_ALLOWED_ATTRIBUTES = {
    **BLEACH_ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "rel"],
    "code": ["class"],
    "pre": ["class"],
    "span": ["class"],
    "th": ["scope"],
    **SVG_ALLOWED_ATTRIBUTES,
}

CSS_SANITIZER = CSSSanitizer() if CSSSanitizer else None
PRIVACY_POLICY_NOTICE_ALLOWED_TAGS = frozenset(
    {"a", "b", "br", "code", "em", "i", "li", "ol", "p", "strong", "u", "ul"}
)
PRIVACY_POLICY_NOTICE_ALLOWED_ATTRIBUTES = {
    "a": ["href", "rel", "target", "title"],
}
PRIVACY_POLICY_NOTICE_ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto", "tel"})


def sanitize_html(
    value: str | None,
    *,
    allowed_tags=None,
    allowed_attributes=None,
    allowed_protocols=None,
    strip: bool = True,
) -> str | None:
    """Normalize input and sanitize it using Bleach with optional overrides."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)

    tags = allowed_tags if allowed_tags is not None else BLEACH_ALLOWED_TAGS
    attributes = allowed_attributes if allowed_attributes is not None else BLEACH_ALLOWED_ATTRIBUTES
    protocols = allowed_protocols if allowed_protocols is not None else BLEACH_ALLOWED_PROTOCOLS

    clean_kwargs = {
        "tags": tags,
        "attributes": attributes,
        "protocols": protocols,
        "strip": strip,
    }
    if CSS_SANITIZER:
        clean_kwargs["css_sanitizer"] = CSS_SANITIZER

    return bleach.clean(
        value,
        **clean_kwargs,
    )


def sanitize_chat_text(value: str | None) -> str | None:
    """Sanitize chat text using the shared chat-specific allow lists."""
    return sanitize_html(
        value,
        allowed_tags=CHAT_ALLOWED_TAGS,
        allowed_attributes=CHAT_ALLOWED_ATTRIBUTES,
        allowed_protocols=BLEACH_ALLOWED_PROTOCOLS,
        strip=True,
    )


def sanitize_policy_notice_html(value: str | None) -> str:
    return sanitize_html(
        value or "",
        allowed_tags=PRIVACY_POLICY_NOTICE_ALLOWED_TAGS,
        allowed_attributes=PRIVACY_POLICY_NOTICE_ALLOWED_ATTRIBUTES,
        allowed_protocols=PRIVACY_POLICY_NOTICE_ALLOWED_PROTOCOLS,
        strip=True,
    ) or ""


def coerce_to_dict(value) -> dict[str, Any]:
    """Return a plain dictionary for supported mapping-like values.

    This helper is intentionally tolerant because model settings may originate
    from JSON columns, Pydantic models, legacy model objects, or JSON strings.
    Normal conversions are not error conditions and must not pollute production
    logs.
    """
    if value is None:
        return {}

    if isinstance(value, Mapping):
        return dict(value)

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(exclude_none=False)
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        try:
            dumped = dict_method(exclude_none=False)
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass

    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
        return {}

    try:
        converted = dict(value)
        if isinstance(converted, dict):
            return converted
    except Exception:
        pass

    return {}


# -------------------
# Internet Connectivity Checker
# -------------------
def has_internet(timeout: float = 5.0, endpoints: Iterable[str] | None = None) -> bool:
    """Return True if outbound HTTP appears to work; False otherwise.

    Parameters
    - timeout: per-request timeout in seconds
    - endpoints: optional override of endpoints to probe

    Notes
    - Uses lightweight endpoints designed for connectivity checks.
    - Treats any 2xx-4xx response as evidence of connectivity.
    """
    urls = list(endpoints) if endpoints is not None else [
        "https://www.gstatic.com/generate_204",  # fast 204
        "https://www.google.com/generate_204",
        "https://1.1.1.1/cdn-cgi/trace",         # Cloudflare
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=timeout)
            if 200 <= r.status_code < 500:
                return True
        except Exception:
            continue
    return False


def _load_current_version() -> str | None:
    if isinstance(_CURRENT_VERSION, str):
        normalized = _CURRENT_VERSION.strip()
        if normalized:
            return normalized
    logger.warning("[Version] _CURRENT_VERSION constant is not set; skipping comparison")
    return None


def _github_version_check_headers() -> dict[str, str]:
    """Build anonymous GitHub release headers for the public repository."""

    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": "omlorix-version-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _select_github_release(
    releases: Any,
    *,
    include_beta: bool,
) -> dict[str, Any] | None:
    """Select the highest published Omlorix release for the requested channel."""

    if not isinstance(releases, list):
        return None

    selected: dict[str, Any] | None = None
    selected_tag: str | None = None
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") is True:
            continue

        raw_tag = release.get("tag_name")
        tag = raw_tag.strip() if isinstance(raw_tag, str) else ""
        if not tag or compare_semantic_versions(tag, tag) is None:
            # This also excludes server-launcher-vX.Y.Z and unrelated tags.
            continue

        beta_release = is_beta_version(tag)
        prerelease = bool(release.get("prerelease")) or "-" in tag
        if prerelease and not beta_release:
            # Alpha, release-candidate, and other prerelease channels are not
            # part of the admin version check.
            continue
        if not include_beta and prerelease:
            continue

        if selected_tag is None or compare_semantic_versions(tag, selected_tag) == 1:
            selected = release
            selected_tag = tag

    if selected is None or selected_tag is None:
        return None

    return {
        "tag": selected_tag,
        "release_date": selected.get("published_at") or selected.get("created_at"),
        "release_type": "beta" if is_beta_version(selected_tag) else "stable",
        "release_url": selected.get("html_url"),
    }


def _fetch_remote_version() -> dict[str, Any] | None:
    current_version = _load_current_version()
    include_beta = is_beta_version(current_version or "")
    try:
        response = requests.get(
            _VERSION_CHECK_URL,
            params={"per_page": _VERSION_CHECK_RELEASE_LIMIT},
            headers=_github_version_check_headers(),
            timeout=_VERSION_CHECK_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        logger.info(
            "[Version] Version endpoint timed out after %.1fs; will retry later",
            _VERSION_CHECK_TIMEOUT,
        )
        return None
    except requests.exceptions.RequestException as exc:
        logger.info(
            "[Version] Version endpoint unreachable (%s); will retry later",
            exc.__class__.__name__,
        )
        return None
    except Exception:
        logger.warning("[Version] Unexpected error while reaching version endpoint", exc_info=True)
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning("[Version] Version endpoint returned invalid JSON payload")
        return None
    except Exception:
        logger.warning("[Version] Failed to decode version response", exc_info=True)
        return None

    selected = _select_github_release(payload, include_beta=include_beta)
    if selected is not None:
        return selected

    logger.warning("[Version] GitHub releases did not contain a matching Omlorix release")
    return None


def _check_for_new_version_and_notify() -> None:
    logger.info("[Version] Starting version check...")
    remote = _fetch_remote_version()
    if not remote:
        logger.info("[Version] Version endpoint unavailable; skipping check")
        return

    remote_tag_value = remote.get("tag") or remote.get("version")
    remote_tag = remote_tag_value.strip() if isinstance(remote_tag_value, str) else None
    if not remote_tag:
        logger.warning("[Version] Remote response missing tag/version field: %s", remote)
        return

    current_version = _load_current_version()
    if not current_version:
        logger.warning("[Version] Current version not found locally; skipping comparison")
        return

    logger.info(
        "[Version] Retrieved versions: current=%s, channel=%s, remote=%s "
        "(build=%s release=%s type=%s)",
        current_version,
        "beta" if is_beta_version(current_version) else "stable",
        remote_tag,
        remote.get("build"),
        remote.get("release_date"),
        remote.get("release_type"),
    )

    version_comparison = compare_semantic_versions(remote_tag, current_version)
    if version_comparison is None:
        # Never turn a malformed or unexpected API value into a false update.
        # Keeping the cache empty also allows a later check to recover once the
        # endpoint returns a valid release tag.
        logger.warning(
            "[Version] Cannot compare current=%s with remote=%s; skipping update check",
            current_version,
            remote_tag,
        )
        return

    last_persisted_notification = _get_persisted_notified_version()
    should_notify = False
    with _version_lock:
        global _latest_available_version
        previous = _latest_available_version
        _latest_available_version = remote_tag
        already_announced = remote_tag == previous or remote_tag == last_persisted_notification
        if version_comparison > 0 and not already_announced:
            should_notify = True
            logger.info(
                "[Version] New version detected: %s (previously announced=%s)",
                remote_tag,
                previous,
            )
        elif version_comparison == 0:
            logger.info("[Version] Already running the latest version (%s)", current_version)
        elif version_comparison < 0:
            logger.info(
                "[Version] Current version %s is newer than remote version %s; "
                "skipping notification",
                current_version,
                remote_tag,
            )
        else:
            logger.info(
                "[Version] Remote version %s already announced; skipping notification",
                remote_tag,
            )

    if not should_notify:
        return

    db = SessionLocal()
    try:
        message = f"New Omlorix version available: {remote_tag} (current: {current_version})."
        details = {
            "current_version": current_version,
            "latest_version": remote_tag,
            "build": remote.get("build"),
            "release_date": remote.get("release_date"),
        }
        create_admin_notification(
            db,
            category="system",
            message=message,
            details=details,
            user_id="system",
            notification_type="info",
        )
        try:
            _persist_notified_version(remote_tag)
        except Exception:
            logger.warning("[Version] Failed to persist that %s was announced", remote_tag, exc_info=True)
        logger.info("[Version] Admin notification created for version %s", remote_tag)
    except Exception:
        logger.exception("[Version] Failed to create admin notification for new version")
    finally:
        try:
            db.close()
        except Exception:
            pass


def get_version_status(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return the current and latest available versions with update status."""
    current_version = _load_current_version()

    with _version_lock:
        latest_version = _latest_available_version

    if latest_version is None or force_refresh:
        try:
            _check_for_new_version_and_notify()
        except Exception:
            logger.exception("[Version] Failed to refresh latest version info")
        finally:
            with _version_lock:
                latest_version = _latest_available_version

    # Equality is insufficient here: the API can temporarily lag a development
    # or newly released installation. Only a strictly newer remote SemVer is an
    # available update.
    version_comparison = (
        compare_semantic_versions(latest_version, current_version)
        if current_version and latest_version
        else None
    )
    update_available = version_comparison is not None and version_comparison > 0

    return {
        "version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
    }


# -------------------
# Background: Internet connectivity checker worker
# -------------------
def _internet_connectivity_checker_worker(stop_event: threading.Event):
    """
    Periodically checks if the server has internet connectivity.

    Settings (page "general"):
    - internet_connectivity_check_enabled: bool
    - internet_connectivity_check_interval_seconds: int

    If connectivity is lost, emits log messages via the module logger.
    """
    while not stop_event.is_set():
        interval_sec = 300
        enabled = True
        offline_mode = False
        db = SessionLocal()
        try:
            # Read settings with fallbacks
            try:
                enabled = bool(get_value_by_page_and_key("general", "internet_connectivity_check_enabled", db))
            except Exception:
                enabled = True

            # Optional: honor global offline mode (skip checks when offline mode is enabled)
            try:
                offline_mode = bool(get_value_by_page_and_key("general", "offline_mode", db))
            except Exception:
                offline_mode = False

        except Exception:
            logger.error("[Connectivity] Worker settings error", exc_info=True)
        finally:
            try:
                db.close()
            except Exception:
                pass

        if not enabled or offline_mode:
            # Sleep the configured interval and continue
            if stop_event.wait(max(30, int(interval_sec))):
                break
            continue

        try:
            _check_for_new_version_and_notify()
        except Exception:
            logger.exception("[Version] Unexpected error while checking latest version")

        # Perform check outside DB session
        online = has_internet()
        if online is True:
            ts = datetime.now(timezone.utc).isoformat()
            logger.info("[Connectivity] Internet connection OK at %s.", ts)
        if online is False:
            # Log only on transition to offline to avoid spamming
            ts = datetime.now(timezone.utc).isoformat()
            logger.warning("[Connectivity] No internet connection detected at %s.", ts)
        db_status = SessionLocal()
        try:
            try:
                update_page_key_value_by_page_and_key("status", "internet_connectivity", bool(online), db_status)
            except Exception:
                logger.error("[Connectivity] Failed to update internet connectivity status setting", exc_info=True)
        finally:
            try:
                db_status.close()
            except Exception:
                pass

        if stop_event.wait(max(30, int(interval_sec))):
            break

# Internet Connectivity Checker Worker
# -------------------
def start_internet_connectivity_checker_worker():
    """Start the internet connectivity checker background worker (daemon thread)."""
    try:
        try:
            _check_for_new_version_and_notify()
        except Exception:
            logger.exception("[Version] Failed to perform startup version check")

        thread = worker_manager.start_worker(
            "internet_connectivity_checker",
            _internet_connectivity_checker_worker,
        )
        logger.info("[Connectivity] Background checker started.")
        return thread
    except Exception:
        logger.error("[Connectivity] Failed to start background checker", exc_info=True)
        return None


def stop_internet_connectivity_checker_worker(timeout: float = 5.0):
    """Signal the internet connectivity checker worker to stop and wait for the thread."""
    worker_manager.stop_worker("internet_connectivity_checker", timeout=timeout)






def get_privacy_policy(db):
    return get_value_by_page_and_key("about", "privacy_policy", db)


def is_default_privacy_policy(content: Any) -> bool:
    if not isinstance(content, str):
        return False
    return content.strip() == PRIVACY_POLICY_TEMPLATE.strip()


def is_default_terms_of_service(content: Any) -> bool:
    if not isinstance(content, str):
        return False
    return content.strip() == TERMS_OF_SERVICE_TEMPLATE.strip()


PRIVACY_POLICY_NOTICE_MODES = {"none", "modal"}


def _normalize_privacy_policy_notice_mode(value: Any) -> str:
    mode = str(value or "none").strip()
    if mode == "banner":
        return "modal"
    if mode == "required_opt_in":
        return "modal"
    if mode in PRIVACY_POLICY_NOTICE_MODES:
        return mode
    return "none"


def _coerce_positive_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
        return number if number > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _coerce_datetime_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_privacy_policy_notice_policy(db, user_id: str | None = None) -> dict[str, Any]:
    revision = _coerce_positive_int(
        get_value_by_page_and_key("about", "privacy_policy_revision", db),
        fallback=1,
    )

    stored_mode = _normalize_privacy_policy_notice_mode(
        get_value_by_page_and_key("about", "privacy_policy_notice_mode", db)
    )
    message_html = sanitize_policy_notice_html(
        str(get_value_by_page_and_key("about", "privacy_policy_notice_message_html", db) or "")
    )
    updated_at = str(get_value_by_page_and_key("about", "privacy_policy_notice_updated_at", db) or "")

    last_interacted_revision = None
    should_show = stored_mode != "none"

    if user_id:
        from app.users.init import get_user_setting_value

        raw_last_interacted_revision = get_user_setting_value(
            user_id,
            "states",
            "privacy_policy_last_interacted_revision",
            db,
        )
        last_interacted_revision = _coerce_positive_int(
            raw_last_interacted_revision,
            fallback=0,
        ) or None
        should_show = bool(stored_mode != "none" and (last_interacted_revision or 0) < revision)

    return {
        "revision": revision,
        "notice_mode": stored_mode,
        "stored_notice_mode": stored_mode,
        "notice_message_html": message_html,
        "notice_updated_at": updated_at,
        "should_show_notice": should_show,
        "privacy_policy_last_interacted_revision": last_interacted_revision,
    }


def update_privacy_policy(
    db,
    content: str,
    *,
    notice_mode: str = "none",
    notice_message_html: str | None = None,
):
    value = str(content)
    mode = _normalize_privacy_policy_notice_mode(notice_mode)

    current_revision = _coerce_positive_int(
        get_value_by_page_and_key("about", "privacy_policy_revision", db),
        fallback=1,
    )
    next_revision = current_revision + 1

    sanitized_notice_html = sanitize_policy_notice_html(notice_message_html or "")
    now_iso = datetime.now(timezone.utc).isoformat()

    update_page_key_value_by_page_and_key("about", "privacy_policy", value, db)
    update_page_key_value_by_page_and_key("about", "privacy_policy_revision", next_revision, db)
    update_page_key_value_by_page_and_key("about", "privacy_policy_notice_mode", mode, db)
    update_page_key_value_by_page_and_key("about", "privacy_policy_notice_message_html", sanitized_notice_html, db)
    update_page_key_value_by_page_and_key("about", "privacy_policy_notice_updated_at", now_iso, db)

    return {
        "revision": next_revision,
        "notice_mode": mode,
        "notice_message_html": sanitized_notice_html,
        "notice_updated_at": now_iso,
    }


def get_terms_of_service(db):
    return get_value_by_page_and_key("about", "terms_of_service", db)


def get_terms_of_service_policy(db, user_id: str | None = None) -> dict[str, Any]:
    content = str(get_terms_of_service(db) or "")
    revision = _coerce_positive_int(
        get_value_by_page_and_key("about", "terms_of_service_revision", db),
        fallback=1,
    )
    updated_at = str(get_value_by_page_and_key("about", "terms_of_service_updated_at", db) or "")
    show_link_on_login = coerce_bool(
        get_value_by_page_and_key("login_general", "show_terms_of_service_link", db),
        default=False,
    )
    enforce_signup_acceptance = coerce_bool(
        get_value_by_page_and_key("login_general", "enforce_terms_of_service_signup_acceptance", db),
        default=False,
    )
    enforce_access_acceptance = coerce_bool(
        get_value_by_page_and_key("login_general", "enforce_terms_of_service_access_acceptance", db),
        default=False,
    )
    is_default_template = is_default_terms_of_service(content)
    customization_required = is_default_template
    # Terms configuration no longer decides whether account registration is
    # available. The administrator's ``enable_signup`` setting owns that
    # decision, while ``require_current_revision_for_signup`` below tells the
    # client whether the optional consent step must be shown.
    signup_available = True
    access_available = bool(not customization_required and content.strip())
    signup_block_reason = None

    accepted_revision = None
    accepted_at = ""
    accepted_current_revision = False

    if user_id:
        from app.users.init import get_user_setting_value

        accepted_revision = _coerce_positive_int(
            get_user_setting_value(user_id, "states", "terms_of_service_accepted_revision", db),
            fallback=0,
        ) or None
        accepted_at = str(
            get_user_setting_value(user_id, "states", "terms_of_service_accepted_at", db) or ""
        )
        accepted_current_revision = bool(
            accepted_revision is not None and accepted_revision >= revision
        )

    return {
        "revision": revision,
        "updated_at": updated_at,
        "show_link_on_login": show_link_on_login,
        "is_default_template": is_default_template,
        "customization_required": customization_required,
        "signup_available": signup_available,
        "access_available": access_available,
        "signup_block_reason": signup_block_reason,
        "accepted_current_revision": accepted_current_revision,
        "terms_of_service_accepted_revision": accepted_revision,
        "terms_of_service_accepted_at": accepted_at,
        "require_current_revision_for_signup": bool(enforce_signup_acceptance),
        "require_current_revision_for_access": bool(enforce_access_acceptance and access_available),
    }


def update_terms_of_service(db, content: str):
    value = str(content)
    current_revision = _coerce_positive_int(
        get_value_by_page_and_key("about", "terms_of_service_revision", db),
        fallback=1,
    )
    next_revision = current_revision + 1
    now_iso = datetime.now(timezone.utc).isoformat()

    update_page_key_value_by_page_and_key("about", "terms_of_service", value, db)
    update_page_key_value_by_page_and_key("about", "terms_of_service_revision", next_revision, db)
    update_page_key_value_by_page_and_key("about", "terms_of_service_updated_at", now_iso, db)

    return {
        "revision": next_revision,
        "updated_at": now_iso,
        "is_default_template": is_default_terms_of_service(value),
    }
