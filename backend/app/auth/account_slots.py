from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import logging
import os
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
import jwt
from jwt import InvalidTokenError as JWTError

from app.auth.jwt_material import get_jwt_material
from app.auth.models import (
    delete_authentication,
    get_authentication_by_token,
    get_authentication_user_id_by_token,
)
from app.groups.access_windows import is_group_accessible_now
from app.logging.models import get_audit_request_ip, stage_audit_log_event
from app.settings.utils import coerce_bool, get_value_by_page_and_key
from app.settings.public_urls import normalize_public_url, normalize_public_urls
from app.utils.client_ip import resolve_configured_trusted_proxy_networks
from app.users.init import get_user_setting_value
from app.users.models import evaluate_user_lock, get_user, normalize_utc_datetime
from app.users.roles import is_admin_role


logger = logging.getLogger(__name__)


MAX_ACCOUNT_SLOTS = 5
ACTIVE_SLOT_COOKIE = "omlorix_active_slot"
ACCESS_COOKIE = "omlorix_access_token"
LEGACY_REFRESH_COOKIE = "refresh_token"
REFRESH_SLOT_COOKIE_TEMPLATE = "omlorix_refresh_slot_{slot}"
SOCIAL_FLOW_COOKIE = "omlorix_social_flow"
SSO_FLOW_COOKIE = "omlorix_sso_flow"
SOCIAL_LINK_FLOW_COOKIE = "omlorix_social_link_flow"
_INSECURE_COOKIE_MODES = {"dev", "development", "local", "test"}


@dataclass
class BrowserAccountSlot:
    """Represents a browser-stored account slot for the current user agent."""

    slot: int
    user_id: str
    refresh_token: str
    display_name: str
    has_custom_profile_picture: bool
    has_profile_picture: bool
    last_active_at: datetime | None
    legacy: bool = False


@dataclass
class SlotAssignment:
    slot: int
    replaced_refresh_token: str | None = None
    replaced_user_id: str | None = None
    replacement_reason: str | None = None


@dataclass(frozen=True)
class AuthCookieSettings:
    """Validated cookie policy captured before durable session publication."""

    refresh_secure: bool
    refresh_samesite: str
    refresh_max_age: int
    access_secure: bool
    access_samesite: str
    access_max_age: int


def get_refresh_slot_cookie_name(slot: int) -> str:
    """Get cookie name for refresh slot."""
    return REFRESH_SLOT_COOKIE_TEMPLATE.format(slot=slot)


def _parse_slot(value: Any) -> int | None:
    """Parse slot number from value."""
    try:
        slot = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= slot <= MAX_ACCOUNT_SLOTS:
        return slot
    return None


def get_active_slot(request: Request) -> int | None:
    """Get active account slot from cookies."""
    return _parse_slot(request.cookies.get(ACTIVE_SLOT_COOKIE))


def _allows_insecure_cookies_in_current_mode() -> bool:
    """Return whether local-mode deployments may issue insecure auth cookies."""
    mode = str(os.getenv("MODE", "production") or "production").strip().lower()
    return mode in _INSECURE_COOKIE_MODES


def _public_url_requires_secure_cookie(db, request: Request | None = None) -> bool:
    """Return whether the public URL selected for this request is HTTPS."""
    return get_external_auth_scheme(db, request) == "https"


def _normalize_scheme(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if candidate in {"http", "https"}:
        return candidate
    return None


def _get_configured_public_urls(db) -> list[str]:
    """Return normalized configured origins while tolerating bootstrap failures."""
    try:
        return normalize_public_urls(
            get_value_by_page_and_key("general", "public_url", db),
            allow_empty=True,
        )
    except Exception as exc:
        logger.debug(
            "Unable to retrieve or normalize configured public URLs; using an empty fallback: %s",
            exc,
            exc_info=True,
        )
        return []


def _get_configured_public_url(db) -> str:
    """Return the primary configured public URL for canonical link fallbacks."""
    public_urls = _get_configured_public_urls(db)
    return public_urls[0] if public_urls else ""


def _normalize_request_origin(value: str | None) -> str | None:
    """Normalize a request Origin/Referer value to a comparable origin."""
    if not value:
        return None
    try:
        # Use the shared origin normalizer so request origins and stored origins
        # have the same representation. In particular, ``urlsplit().hostname``
        # removes the brackets required when reconstructing an IPv6 URL.
        return normalize_public_url(value)
    except ValueError:
        return None


def _get_request_public_url(db, request: Request | None) -> str | None:
    """Select the configured public URL through which the current request arrived."""
    if request is None:
        return None
    configured = set(_get_configured_public_urls(db))
    if not configured:
        return None

    for candidate in (
        request.headers.get("origin"),
        request.headers.get("referer"),
        str(request.url),
    ):
        normalized = _normalize_request_origin(candidate)
        if normalized in configured:
            return normalized
    return None


def _get_forwarded_proto(request: Request) -> str | None:
    forwarded = request.headers.get("forwarded")
    if forwarded:
        for entry in forwarded.split(","):
            for part in entry.split(";"):
                key, _, value = part.partition("=")
                if key.strip().lower() != "proto":
                    continue
                normalized = _normalize_scheme(value.strip().strip('"'))
                if normalized:
                    return normalized

    for header_name in ("x-forwarded-proto", "x-forwarded-protocol"):
        raw_value = request.headers.get(header_name)
        if not raw_value:
            continue
        normalized = _normalize_scheme(raw_value.split(",", 1)[0])
        if normalized:
            return normalized
    return None


def _request_came_through_trusted_proxy(request: Request | None, db) -> bool:
    if request is None:
        return False

    client_host = getattr(getattr(request, "client", None), "host", None)
    if not client_host:
        return False

    trusted_networks = resolve_configured_trusted_proxy_networks(
        db,
        "AUTH_TRUSTED_PROXIES",
        "RATE_LIMIT_TRUSTED_PROXIES",
    )
    if not trusted_networks:
        return False

    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False

    return any(client_ip in network for network in trusted_networks)


def get_external_auth_scheme(db, request: Request | None = None) -> str | None:
    """Resolve the externally visible auth scheme for redirects and cookies."""
    public_url = _get_request_public_url(db, request) or _get_configured_public_url(db)
    if public_url:
        public_scheme = _normalize_scheme(urlsplit(public_url).scheme)
        if public_scheme:
            return public_scheme

    if request is not None and _request_came_through_trusted_proxy(request, db):
        forwarded_scheme = _get_forwarded_proto(request)
        if forwarded_scheme:
            return forwarded_scheme

    if request is not None:
        return _normalize_scheme(request.url.scheme)
    return None


def build_auth_redirect_base_url(db, request: Request | None = None) -> str:
    """Build the external base URL for auth callback redirects."""
    public_url = _get_request_public_url(db, request) or _get_configured_public_url(db)
    if public_url:
        return public_url

    if request is None:
        raise HTTPException(status_code=500, detail="general.public_url must be configured.")

    scheme = get_external_auth_scheme(db, request) or "http"
    return f"{scheme}://{request.url.netloc}"


def should_secure_auth_cookie(db, request: Request | None = None) -> bool:
    """Return whether auth cookies should be marked Secure for this request."""
    secure_cookie = coerce_bool(get_value_by_page_and_key("security", "refresh_cookie_secure", db))
    if secure_cookie:
        return True
    if not _allows_insecure_cookies_in_current_mode() or _public_url_requires_secure_cookie(db, request):
        return True
    return get_external_auth_scheme(db, request) == "https"


def _get_cookie_security_settings(
    db,
    request: Request | None = None,
    *,
    ttl_setting_key: str = "refresh_token_expire_minutes",
) -> tuple[bool, str, int]:
    """Get cookie security settings."""
    secure_cookie = should_secure_auth_cookie(db, request)
    samesite_policy = str(get_value_by_page_and_key("security", "refresh_cookie_samesite", db) or "lax").lower()
    if samesite_policy not in {"lax", "strict", "none"}:
        samesite_policy = "lax"
    elif samesite_policy == "none" and not secure_cookie:
        # Browsers reject SameSite=None cookies that are not marked Secure.
        samesite_policy = "lax"
    max_age = int(get_value_by_page_and_key("security", ttl_setting_key, db) or 60) * 60
    return secure_cookie, samesite_policy, max_age


def resolve_auth_cookie_settings(
    db,
    request: Request | None = None,
) -> AuthCookieSettings:
    """Resolve and validate every session-cookie attribute without mutating a response."""

    refresh_secure, refresh_samesite, refresh_max_age = _get_cookie_security_settings(
        db,
        request,
    )
    access_secure, access_samesite, access_max_age = _get_cookie_security_settings(
        db,
        request,
        ttl_setting_key="access_token_expire_minutes",
    )
    return AuthCookieSettings(
        refresh_secure=refresh_secure,
        refresh_samesite=refresh_samesite,
        refresh_max_age=refresh_max_age,
        access_secure=access_secure,
        access_samesite=access_samesite,
        access_max_age=access_max_age,
    )


def set_refresh_slot_cookie(
    response: Response,
    slot: int,
    refresh_token: str,
    db,
    request: Request | None = None,
    *,
    cookie_settings: AuthCookieSettings | None = None,
) -> None:
    """Set refresh token cookie for a slot."""
    settings = cookie_settings or resolve_auth_cookie_settings(db, request)
    response.set_cookie(
        key=get_refresh_slot_cookie_name(slot),
        value=refresh_token,
        httponly=True,
        samesite=settings.refresh_samesite,
        secure=settings.refresh_secure,
        max_age=settings.refresh_max_age,
    )


def clear_refresh_slot_cookie(response: Response, slot: int, db, request: Request | None = None) -> None:
    """Clear refresh token cookie for a slot."""
    secure_cookie, samesite_policy, _ = _get_cookie_security_settings(db, request)
    response.delete_cookie(
        key=get_refresh_slot_cookie_name(slot),
        httponly=True,
        samesite=samesite_policy,
        secure=secure_cookie,
    )


def set_active_slot_cookie(
    response: Response,
    slot: int,
    db,
    request: Request | None = None,
    *,
    cookie_settings: AuthCookieSettings | None = None,
) -> None:
    """Set active slot cookie."""
    settings = cookie_settings or resolve_auth_cookie_settings(db, request)
    response.set_cookie(
        key=ACTIVE_SLOT_COOKIE,
        value=str(slot),
        httponly=True,
        samesite=settings.refresh_samesite,
        secure=settings.refresh_secure,
        max_age=settings.refresh_max_age,
    )


def clear_active_slot_cookie(response: Response, db, request: Request | None = None) -> None:
    """Clear active slot cookie."""
    secure_cookie, samesite_policy, _ = _get_cookie_security_settings(db, request)
    response.delete_cookie(
        key=ACTIVE_SLOT_COOKIE,
        httponly=True,
        samesite=samesite_policy,
        secure=secure_cookie,
    )


def set_access_token_cookie(
    response: Response,
    access_token: str,
    db,
    request: Request | None = None,
    *,
    cookie_settings: AuthCookieSettings | None = None,
) -> None:
    """Set the active access token cookie."""
    settings = cookie_settings or resolve_auth_cookie_settings(db, request)
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        httponly=True,
        samesite=settings.access_samesite,
        secure=settings.access_secure,
        max_age=settings.access_max_age,
    )


def clear_access_token_cookie(response: Response, db, request: Request | None = None) -> None:
    """Clear the active access token cookie."""
    secure_cookie, samesite_policy, _ = _get_cookie_security_settings(
        db,
        request,
        ttl_setting_key="access_token_expire_minutes",
    )
    response.delete_cookie(
        key=ACCESS_COOKIE,
        httponly=True,
        samesite=samesite_policy,
        secure=secure_cookie,
    )


def clear_legacy_refresh_cookie(
    response: Response,
    db,
    request: Request | None = None,
    *,
    cookie_settings: AuthCookieSettings | None = None,
) -> None:
    """Clear legacy refresh token cookie."""
    settings = cookie_settings or resolve_auth_cookie_settings(db, request)
    response.delete_cookie(
        key=LEGACY_REFRESH_COOKIE,
        httponly=True,
        samesite=settings.refresh_samesite,
        secure=settings.refresh_secure,
    )


def _decode_refresh_slot_token(refresh_token: str, db) -> dict[str, Any] | None:
    """Decode a refresh token if it is still usable for a browser slot."""
    try:
        secret, algorithm = get_jwt_material()
        payload = jwt.decode(refresh_token, secret, algorithms=[algorithm])
    except Exception:
        return None

    if payload.get("type") != "refresh" or payload.get("sub") is None or payload.get("exp") is None:
        return None
    return payload


def _slot_user_runtime_allowed(user, db) -> bool:
    """Return whether a slot user still satisfies normal account-state checks."""
    if getattr(user, "deleted_at", None) is not None or not getattr(user, "is_active", True):
        return False

    temporary_expires_at = normalize_utc_datetime(getattr(user, "temporary_expires_at", None))
    if getattr(user, "account_type", "regular") == "temporary":
        if temporary_expires_at is None or temporary_expires_at <= datetime.now(timezone.utc):
            return False

    lock = evaluate_user_lock(user, db)
    if isinstance(lock, dict) and lock.get("is_locked"):
        return False

    group_id = getattr(user, "group_id", None)
    if group_id:
        access_check = is_group_accessible_now(
            group_id,
            db,
            is_admin=is_admin_role(getattr(user, "role", None)),
        )
        if not access_check.get("accessible", True):
            return False

    return getattr(user, "role", None) != "pending"


def _resolve_slot_from_refresh_token(slot: int, refresh_token: str, db) -> BrowserAccountSlot | None:
    """Resolve account slot from a validated refresh token."""
    auth_entry = get_authentication_by_token(db, refresh_token, "refresh_token")
    if not auth_entry:
        return None

    payload = _decode_refresh_slot_token(refresh_token, db)
    if not payload or payload.get("sub") != auth_entry.user_id:
        return None

    try:
        user = get_user(db, auth_entry.user_id)
    except HTTPException:
        return None

    if not _slot_user_runtime_allowed(user, db):
        return None

    has_custom = bool(getattr(user, "custom_profile_picture", False))
    has_oauth = bool(get_user_setting_value(user.id, "social_login", "oauth_profile_picture_present", db))
    first_name = str(getattr(user, "first_name", "") or "").strip()
    last_name = str(getattr(user, "last_name", "") or "").strip()
    # Keep the browser account list privacy-light by sending only the name that
    # the current user already sees in the main profile UI.
    display_name = " ".join(part for part in (first_name, last_name) if part).strip() or first_name or "User"

    return BrowserAccountSlot(
        slot=slot,
        user_id=user.id,
        refresh_token=refresh_token,
        display_name=display_name,
        has_custom_profile_picture=has_custom,
        has_profile_picture=has_custom or has_oauth,
        last_active_at=getattr(auth_entry, "last_active_at", None),
    )


def resolve_browser_account_slot(slot: int, request: Request, db) -> BrowserAccountSlot | None:
    """Resolve a browser account slot from its cookie only if the refresh token is currently valid."""
    parsed_slot = _parse_slot(slot)
    if parsed_slot is None:
        return None

    refresh_token = request.cookies.get(get_refresh_slot_cookie_name(parsed_slot))
    if not refresh_token:
        return None

    return _resolve_slot_from_refresh_token(parsed_slot, refresh_token, db)


def list_browser_accounts(request: Request, db, response: Response | None = None, *, include_legacy: bool = True) -> list[BrowserAccountSlot]:
    """List all browser accounts from cookies."""
    accounts: list[BrowserAccountSlot] = []
    occupied_slots: set[int] = set()

    for slot in range(1, MAX_ACCOUNT_SLOTS + 1):
        refresh_token = request.cookies.get(get_refresh_slot_cookie_name(slot))
        if not refresh_token:
            continue
        account = _resolve_slot_from_refresh_token(slot, refresh_token, db)
        if not account:
            if response:
                delete_authentication(db, refresh_token=refresh_token)
                clear_refresh_slot_cookie(response, slot, db, request)
            continue
        accounts.append(account)
        occupied_slots.add(slot)

    if include_legacy and 1 not in occupied_slots:
        legacy_refresh_token = request.cookies.get(LEGACY_REFRESH_COOKIE)
        if legacy_refresh_token:
            legacy_account = _resolve_slot_from_refresh_token(1, legacy_refresh_token, db)
            if legacy_account:
                legacy_account.legacy = True
                accounts.append(legacy_account)
            elif response:
                delete_authentication(db, refresh_token=legacy_refresh_token)
                clear_legacy_refresh_cookie(response, db, request)

    accounts.sort(key=lambda account: account.slot)
    return accounts


def choose_fallback_slot(accounts: list[BrowserAccountSlot]) -> int | None:
    """Choose fallback slot based on last activity."""
    if not accounts:
        return None
    ordered = sorted(
        accounts,
        key=lambda account: (
            account.last_active_at or datetime.min.replace(tzinfo=timezone.utc),
            -account.slot,
        ),
        reverse=True,
    )
    return ordered[0].slot


def ensure_active_slot_cookie(request: Request, response: Response, db) -> int | None:
    """Ensure active slot cookie is set correctly."""
    accounts = list_browser_accounts(request, db, response=response, include_legacy=True)
    if not accounts:
        clear_active_slot_cookie(response, db, request)
        return None

    active_slot = get_active_slot(request)
    active_account = next((account for account in accounts if account.slot == active_slot), None)
    if active_account:
        if active_account.legacy:
            set_refresh_slot_cookie(response, 1, active_account.refresh_token, db, request)
            set_active_slot_cookie(response, 1, db, request)
            clear_legacy_refresh_cookie(response, db, request)
            return 1
        return active_account.slot

    fallback_slot = choose_fallback_slot(accounts)
    if fallback_slot is not None:
        fallback_account = next((account for account in accounts if account.slot == fallback_slot), None)
        if fallback_account and fallback_account.legacy:
            set_refresh_slot_cookie(response, 1, fallback_account.refresh_token, db, request)
            set_active_slot_cookie(response, 1, db, request)
            clear_legacy_refresh_cookie(response, db, request)
            return 1
        set_active_slot_cookie(response, fallback_slot, db, request)
    else:
        clear_active_slot_cookie(response, db, request)
    return fallback_slot


def get_active_refresh_token(request: Request, response: Response, db) -> tuple[str | None, int | None]:
    """Get active refresh token and slot."""
    requested_active_slot = get_active_slot(request)
    if requested_active_slot:
        refresh_token = request.cookies.get(get_refresh_slot_cookie_name(requested_active_slot))
        if refresh_token and _decode_refresh_slot_token(refresh_token, db):
            return refresh_token, requested_active_slot

    active_slot = ensure_active_slot_cookie(request, response, db)
    if active_slot:
        refresh_token = request.cookies.get(get_refresh_slot_cookie_name(active_slot))
        if refresh_token:
            return refresh_token, active_slot
    legacy_refresh_token = request.cookies.get(LEGACY_REFRESH_COOKIE)
    if legacy_refresh_token and _resolve_slot_from_refresh_token(1, legacy_refresh_token, db):
        return legacy_refresh_token, 1
    if legacy_refresh_token:
        clear_legacy_refresh_cookie(response, db, request)
    return None, None


def _resolve_active_slot_for_listing(
    request: Request,
    accounts: list[BrowserAccountSlot],
    *,
    active_slot_override: int | None = None,
) -> int | None:
    """Resolve the active slot for read-only account listings."""
    if not accounts:
        return None

    visible_slots = {account.slot for account in accounts}
    if active_slot_override in visible_slots:
        return active_slot_override

    active_slot = get_active_slot(request)
    if active_slot in visible_slots:
        return active_slot

    return choose_fallback_slot(accounts)


def _serialize_accounts_payload(accounts: list[BrowserAccountSlot], active_slot: int | None) -> dict[str, Any]:
    """Serialize browser account slots with minimal fields for the client."""
    return {
        "accounts": [
            {
                "slot": account.slot,
                "display_name": account.display_name,
                "has_profile_picture": account.has_profile_picture,
                "active": account.slot == active_slot,
            }
            for account in accounts
        ],
        "active_slot": active_slot,
        "can_add_account": len(accounts) < MAX_ACCOUNT_SLOTS,
        "max_accounts": MAX_ACCOUNT_SLOTS,
    }


def resolve_slot_assignment(
    request: Request,
    response: Response,
    db,
    *,
    user_id: str,
    account_mode: str = "primary",
    replace_slot: int | None = None,
) -> SlotAssignment | None:
    """Resolve slot assignment for user login."""
    accounts = list_browser_accounts(request, db, response=response, include_legacy=True)
    by_slot = {account.slot: account for account in accounts}

    existing_account = next((account for account in accounts if account.user_id == user_id), None)
    if existing_account:
        return SlotAssignment(
            slot=existing_account.slot,
            replaced_refresh_token=existing_account.refresh_token,
            replaced_user_id=existing_account.user_id,
            replacement_reason="existing_account",
        )

    replace_slot = _parse_slot(replace_slot)
    if replace_slot:
        replaced = by_slot.get(replace_slot)
        return SlotAssignment(
            slot=replace_slot,
            replaced_refresh_token=replaced.refresh_token if replaced else None,
            replaced_user_id=replaced.user_id if replaced else None,
            replacement_reason="requested_slot" if replaced else None,
        )

    if account_mode == "add":
        for slot in range(1, MAX_ACCOUNT_SLOTS + 1):
            if slot not in by_slot:
                return SlotAssignment(slot=slot)
        return None

    active_slot = get_active_slot(request)
    if active_slot:
        replaced = by_slot.get(active_slot)
        return SlotAssignment(
            slot=active_slot,
            replaced_refresh_token=replaced.refresh_token if replaced else None,
            replaced_user_id=replaced.user_id if replaced else None,
            replacement_reason="active_slot" if replaced else None,
        )

    if accounts:
        fallback_slot = choose_fallback_slot(accounts) or 1
        replaced = by_slot.get(fallback_slot)
        return SlotAssignment(
            slot=fallback_slot,
            replaced_refresh_token=replaced.refresh_token if replaced else None,
            replaced_user_id=replaced.user_id if replaced else None,
            replacement_reason="fallback_slot" if replaced else None,
        )

    return SlotAssignment(slot=1)


def finalize_slot_assignment(
    request: Request,
    response: Response,
    db,
    *,
    slot_assignment: SlotAssignment,
    refresh_token: str,
    cookie_settings: AuthCookieSettings,
) -> int:
    """Finalize an already-persisted slot assignment by setting cookies."""
    if request.cookies.get(LEGACY_REFRESH_COOKIE):
        clear_legacy_refresh_cookie(
            response,
            db,
            request,
            cookie_settings=cookie_settings,
        )

    set_refresh_slot_cookie(
        response,
        slot_assignment.slot,
        refresh_token,
        db,
        request,
        cookie_settings=cookie_settings,
    )
    set_active_slot_cookie(
        response,
        slot_assignment.slot,
        db,
        request,
        cookie_settings=cookie_settings,
    )
    return slot_assignment.slot


def list_accounts_payload(
    request: Request,
    response_or_db=None,
    db=None,
    *,
    active_slot_override: int | None = None,
) -> dict[str, Any]:
    """Generate a read-only accounts list payload for API responses."""
    if db is None:
        db = response_or_db
    if db is None:
        raise ValueError("db is required")
    accounts = list_browser_accounts(request, db, include_legacy=False)
    active_slot = _resolve_active_slot_for_listing(
        request,
        accounts,
        active_slot_override=active_slot_override,
    )
    return _serialize_accounts_payload(accounts, active_slot)


def _slot_user_id_from_cookie(request: Request, db, slot: int | None) -> str | None:
    """Resolve a slot owner without loading or decrypting credential columns."""
    parsed_slot = _parse_slot(slot)
    if parsed_slot is None:
        return None
    refresh_token = request.cookies.get(get_refresh_slot_cookie_name(parsed_slot))
    if not refresh_token:
        return None
    return get_authentication_user_id_by_token(
        db,
        refresh_token,
        "refresh_token",
    )


def delete_account_slot(
    request: Request,
    response: Response,
    db,
    slot: int,
) -> dict[str, Any]:
    """Delete an account slot."""
    parsed_slot = _parse_slot(slot)
    if not parsed_slot:
        raise HTTPException(status_code=400, detail="Invalid account slot")

    refresh_token = request.cookies.get(get_refresh_slot_cookie_name(parsed_slot))
    if not refresh_token:
        raise HTTPException(status_code=404, detail="Account slot not found")

    active_slot = get_active_slot(request)
    removed_user_id = _slot_user_id_from_cookie(request, db, parsed_slot)
    active_user_id = _slot_user_id_from_cookie(request, db, active_slot)
    remaining = [
        account
        for account in list_browser_accounts(request, db, response=response, include_legacy=False)
        if account.slot != parsed_slot
    ]
    fallback_slot = active_slot
    if active_slot == parsed_slot or active_slot is None:
        fallback_slot = choose_fallback_slot(remaining)

    def stage_slot_deletion(_deleted_rows: list) -> None:
        if removed_user_id is None:
            return
        stage_audit_log_event(
            db,
            user_id=active_user_id or removed_user_id,
            action="ACCOUNT_SLOT_DELETED",
            details={
                "slot": parsed_slot,
                "removed_user_id": removed_user_id,
                "was_active": active_slot == parsed_slot,
                "fallback_slot": fallback_slot,
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="auth_security",
        )

    delete_authentication(
        db,
        refresh_token=refresh_token,
        before_commit=stage_slot_deletion,
    )
    clear_refresh_slot_cookie(response, parsed_slot, db, request)

    if active_slot == parsed_slot or active_slot is None:
        clear_access_token_cookie(response, db, request)
        if fallback_slot is None:
            clear_active_slot_cookie(response, db, request)
        else:
            set_active_slot_cookie(response, fallback_slot, db, request)

    return list_accounts_payload(request, db)


def switch_active_account_slot(
    request: Request,
    response: Response,
    db,
    slot: int,
) -> dict[str, Any]:
    """Switch to a different active account slot."""
    parsed_slot = _parse_slot(slot)
    if not parsed_slot:
        raise HTTPException(status_code=400, detail="Invalid account slot")

    refresh_token = request.cookies.get(get_refresh_slot_cookie_name(parsed_slot))
    if not refresh_token:
        raise HTTPException(status_code=404, detail="Account slot not found")

    account = _resolve_slot_from_refresh_token(parsed_slot, refresh_token, db)
    if not account:
        clear_refresh_slot_cookie(response, parsed_slot, db, request)
        raise HTTPException(status_code=404, detail="Account slot not found")

    previous_slot = get_active_slot(request)
    previous_user_id = _slot_user_id_from_cookie(request, db, previous_slot)
    identity_changed = previous_user_id != account.user_id

    clear_access_token_cookie(response, db, request)
    set_active_slot_cookie(response, parsed_slot, db, request)
    payload = list_accounts_payload(request, db, active_slot_override=parsed_slot)
    payload["switched_to_slot"] = parsed_slot

    if identity_changed:
        try:
            stage_audit_log_event(
                db,
                user_id=previous_user_id or account.user_id,
                action="ACTIVE_ACCOUNT_SWITCHED",
                details={
                    "from_user_id": previous_user_id,
                    "to_user_id": account.user_id,
                    "from_slot": previous_slot,
                    "to_slot": parsed_slot,
                    "identity_changed": True,
                },
                ip_address=get_audit_request_ip(request, db),
                user_agent=request.headers.get("user-agent"),
                category="auth_security",
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    return payload


def _get_flow_cookie_secret(db) -> str:
    """Use the operator-managed signing key for short-lived flow cookies."""
    secret, _algorithm = get_jwt_material()
    return secret


def set_flow_context_cookie(
    response: Response,
    db,
    request: Request | None,
    *,
    cookie_name: str,
    account_mode: str,
    replace_slot: int | None,
    return_url: str | None,
    accept_terms_of_service: bool = False,
    terms_of_service_revision: int | None = None,
    native_auth: bool = False,
    native_kind: str | None = None,
    native_provider: str | None = None,
    native_code_challenge: str | None = None,
    native_state: str | None = None,
    ttl_seconds: int = 600,
) -> None:
    """Set flow context cookie for OAuth/SSO flows."""
    payload = {
        "account_mode": "add" if account_mode == "add" else "primary",
        "replace_slot": _parse_slot(replace_slot),
        "return_url": return_url or "",
        "accept_terms_of_service": bool(accept_terms_of_service),
        "terms_of_service_revision": terms_of_service_revision,
        "native_auth": bool(native_auth),
        "native_kind": str(native_kind or "").strip().lower() if native_auth else "",
        "native_provider": str(native_provider or "").strip().lower()[:64] if native_auth else "",
        "native_code_challenge": str(native_code_challenge or "")[:128] if native_auth else "",
        "native_state": str(native_state or "")[:128] if native_auth else "",
        "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    }
    token = jwt.encode(payload, _get_flow_cookie_secret(db), algorithm="HS512")
    secure_cookie, samesite_policy, _ = _get_cookie_security_settings(db, request)
    response.set_cookie(
        key=cookie_name,
        value=token,
        httponly=True,
        samesite=samesite_policy,
        secure=secure_cookie,
        max_age=ttl_seconds,
    )


def read_flow_context_cookie(request: Request, db, *, cookie_name: str) -> dict[str, Any]:
    """Read flow context cookie."""
    token = request.cookies.get(cookie_name)
    if not token:
        return {
            "account_mode": "primary",
            "replace_slot": None,
            "return_url": "",
            "accept_terms_of_service": False,
            "terms_of_service_revision": None,
            "native_auth": False,
            "native_kind": "",
            "native_provider": "",
            "native_code_challenge": "",
            "native_state": "",
        }

    try:
        payload = jwt.decode(token, _get_flow_cookie_secret(db), algorithms=["HS512"])
    except JWTError:
        return {
            "account_mode": "primary",
            "replace_slot": None,
            "return_url": "",
            "accept_terms_of_service": False,
            "terms_of_service_revision": None,
            "native_auth": False,
            "native_kind": "",
            "native_provider": "",
            "native_code_challenge": "",
            "native_state": "",
        }

    return {
        "account_mode": "add" if payload.get("account_mode") == "add" else "primary",
        "replace_slot": _parse_slot(payload.get("replace_slot")),
        "return_url": str(payload.get("return_url") or ""),
        "accept_terms_of_service": bool(payload.get("accept_terms_of_service")),
        "terms_of_service_revision": payload.get("terms_of_service_revision"),
        "native_auth": bool(payload.get("native_auth")),
        "native_kind": str(payload.get("native_kind") or "")[:16],
        "native_provider": str(payload.get("native_provider") or "")[:64],
        "native_code_challenge": str(payload.get("native_code_challenge") or "")[:128],
        "native_state": str(payload.get("native_state") or "")[:128],
    }


def clear_flow_context_cookie(response: Response, db, request: Request | None, *, cookie_name: str) -> None:
    """Clear flow context cookie."""
    secure_cookie, samesite_policy, _ = _get_cookie_security_settings(db, request)
    response.delete_cookie(
        key=cookie_name,
        httponly=True,
        samesite=samesite_policy,
        secure=secure_cookie,
    )


def set_social_link_context_cookie(
    response: Response,
    db,
    request: Request | None,
    *,
    user_id: str,
    authentication_id: str,
    provider: str,
    state_hash: str,
    native_state: str | None = None,
    native_code_challenge: str | None = None,
    ttl_seconds: int = 600,
) -> None:
    """Bind a social-link callback to a recently stepped-up session.

    No reusable access token is placed in the browser flow cookie. The callback
    verifies that the referenced authentication row still exists and that its
    step-up timestamp remains fresh. Native flows additionally carry the PKCE
    challenge so the callback can defer mutation to a verifier-gated exchange.
    """
    payload = {
        "purpose": "social_link",
        "user_id": str(user_id),
        "authentication_id": str(authentication_id),
        "provider": str(provider).strip().lower(),
        "state_hash": str(state_hash).strip(),
        "native_state": str(native_state or "")[:128],
        "native_code_challenge": str(native_code_challenge or "")[:128],
        "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    }
    token = jwt.encode(payload, _get_flow_cookie_secret(db), algorithm="HS512")
    secure_cookie = should_secure_auth_cookie(db, request)
    # OAuth callbacks originate on another site. Match the existing social
    # state cookie so query and form_post callbacks retain this context.
    samesite_policy = "none" if secure_cookie else "lax"
    response.set_cookie(
        key=SOCIAL_LINK_FLOW_COOKIE,
        value=token,
        httponly=True,
        samesite=samesite_policy,
        secure=secure_cookie,
        max_age=ttl_seconds,
    )


def read_social_link_context_cookie(request: Request, db) -> dict[str, str] | None:
    """Return a validated social-link context, or ``None`` when absent/invalid."""
    token = request.cookies.get(SOCIAL_LINK_FLOW_COOKIE)
    if not token:
        return None
    try:
        payload = jwt.decode(token, _get_flow_cookie_secret(db), algorithms=["HS512"])
    except JWTError:
        return None
    if payload.get("purpose") != "social_link":
        return None
    user_id = str(payload.get("user_id") or "").strip()
    authentication_id = str(payload.get("authentication_id") or "").strip()
    provider = str(payload.get("provider") or "").strip().lower()
    state_hash = str(payload.get("state_hash") or "").strip()
    if not user_id or not authentication_id or not provider or not state_hash:
        return None
    result = {
        "user_id": user_id,
        "authentication_id": authentication_id,
        "provider": provider,
        "state_hash": state_hash,
    }
    native_state = str(payload.get("native_state") or "").strip()
    if native_state:
        result["native_state"] = native_state[:128]
    native_code_challenge = str(payload.get("native_code_challenge") or "").strip()
    if native_code_challenge:
        result["native_code_challenge"] = native_code_challenge[:128]
    return result


def clear_social_link_context_cookie(response: Response, db, request: Request | None) -> None:
    """Forget a completed, cancelled, or invalid social-link flow."""
    secure_cookie = should_secure_auth_cookie(db, request)
    response.delete_cookie(
        key=SOCIAL_LINK_FLOW_COOKIE,
        httponly=True,
        samesite="none" if secure_cookie else "lax",
        secure=secure_cookie,
    )
