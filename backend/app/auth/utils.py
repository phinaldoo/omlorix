from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Response, Request
from typing import Any, Dict, Sequence
import base64
import json
import string
import logging
import hashlib
import hmac
import os
import secrets
import time
import uuid
import bcrypt
from urllib.parse import quote
from urllib.parse import urlsplit
import mimetypes

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import cast, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.auth.account_slots import (
    LEGACY_REFRESH_COOKIE,
    MAX_ACCOUNT_SLOTS,
    SOCIAL_FLOW_COOKIE,
    SSO_FLOW_COOKIE,
    clear_access_token_cookie,
    set_access_token_cookie,
    clear_active_slot_cookie,
    clear_flow_context_cookie,
    clear_legacy_refresh_cookie,
    clear_refresh_slot_cookie,
    ensure_active_slot_cookie,
    finalize_slot_assignment,
    get_active_slot,
    get_refresh_slot_cookie_name,
    list_accounts_payload,
    list_browser_accounts,
    read_flow_context_cookie,
    resolve_auth_cookie_settings,
    resolve_slot_assignment,
    should_secure_auth_cookie,
)
from app.auth.models import (
    create_authentication,
    get_authentication,
    delete_authentication_login_rows,
    list_authentication_login_metadata,
    minimize_session_device_info,
    minimize_session_ip_address,
    delete_authentication,
    create_password_reset_token,
    delete_pending_auth_action,
    get_password_reset_token_by_hash,
    get_active_pending_auth_action,
    invalidate_user_password_reset_tokens,
    replace_pending_auth_action,
    check_blocked_ip_address,
    record_ip_address_security_event,
)
from app.auth.twofa_provider import (
    build_login_2fa_session_claims,
    evaluate_login_2fa,
    ensure_provider_alignment,
    normalize_otp_action,
)
from app.auth.session_store import cache_session, revoke_token_digests
from app.auth.email_localization import resolve_email_language
from app.auth.jwt_material import get_jwt_material
from app.auth.password_policy import effective_minimum_password_length
from app.auth.ldap import LDAPAuthenticatedUser, get_ldap_provider
from app.database import AuditSessionLocal, SessionLocal
from app.email.models import (
    consume_email_security_rate_limit,
    hash_email_security_action,
)
from app.groups.models import get_group, get_group_by_name
from app.groups.access_windows import is_group_accessible_now
from app.logging.models import (
    create_authentication_log,
    create_admin_notification,
    create_audit_log,
    stage_audit_log_event,
)
from app.middleware.ip_restriction import get_client_ip as _get_hardened_client_ip
from app.settings.utils import get_value_by_page_and_key, coerce_bool, get_public_url, is_password_reset_ready
from app.telemetry.metrics import (
    record_auth_ip_block_metric,
    record_auth_login_attempt_metric,
    record_auth_logout_metric,
)
from app.users.models import (
    User,
    build_user_email_match,
    canonicalize_user_email,
    create_user,
    evaluate_user_lock,
    get_user,
    normalize_utc_datetime,
    lock_user,
    check_user_locked,
    user_exists_by_email,
    get_user_wrong_sign_in_attempts,
    increment_user_wrong_sign_in_attempts,
    reset_user_wrong_sign_in_attempts,
)
from app.users.roles import is_admin_role, normalize_external_role
from app.users.external_management import (
    is_externally_managed,
    mark_user_externally_managed,
    require_locally_managed_account,
)
from app.users.init import get_user_setting_value, update_user_settings, update_user_settings_bulk
from app.settings.models import get_settings_page_data
from app.redis_client import get_redis_client
from app.utils.client_ip import extract_client_ip_from_request, resolve_trusted_proxy_networks
from app.utils.utils import get_terms_of_service_policy



logger = logging.getLogger(__name__)


_PENDING_ACTION_SOCIAL_AUTH_CODE = "social_auth_code"
_PENDING_ACTION_SSO_AUTH_CODE = "sso_auth_code"
_PENDING_ACTION_SOCIAL_LOGIN = "social_login_token"
_PENDING_ACTION_PASSWORD_SIGNIN = "password_signin_token"
_PENDING_ACTION_PASSKEY_LOGIN = "passkey_login_token"
_PENDING_ACTION_SSO_LOGIN = "sso_login_token"


def _social_debug_fingerprint(value: object) -> str:
    """Return a short correlation fingerprint without logging OAuth material."""
    text = str(value or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else "<empty>"


def _social_debug_email_domain(value: object) -> str:
    """Return only the email domain for temporary authentication diagnostics."""
    email = str(value or "").strip().lower()
    return email.rsplit("@", 1)[-1] if "@" in email else "<missing>"


def _auth_metric_method(log_event: str | None) -> str:
    event = str(log_event or "").strip().lower()
    if "ldap" in event:
        return "ldap"
    if "social" in event:
        return "social"
    if "sso" in event:
        return "sso"
    if "passkey" in event or "webauthn" in event:
        return "passkey"
    return "password"


_PASSWORD_RESET_TOKEN_TTL_SECONDS = 30 * 60
_PASSWORD_RESET_COOLDOWN_SECONDS = 60
_PASSWORD_RESET_MAX_PER_WINDOW = 5
_PASSWORD_RESET_WINDOW_SECONDS = 15 * 60
_PASSWORD_RESET_TOKEN_ATTEMPT_MAX_PER_WINDOW = 10
_PASSWORD_RESET_TOKEN_ATTEMPT_WINDOW_SECONDS = 15 * 60
_PASSWORD_RESET_RATE_LIMIT_DETAIL = "Too many password reset attempts. Please try again later."
_PASSWORD_RESET_METADATA_RETENTION_SECONDS = 30 * 60
_PASSWORD_RESET_RESPONSE_MIN_SECONDS = 0.25
_PASSWORD_RESET_RESPONSE_JITTER_MILLISECONDS = 75
_PASSWORD_RESET_IDENTIFIER_HASH_SALT = str(os.getenv("PASSWORD_RESET_IDENTIFIER_HASH_SALT") or "").strip()
_PENDING_SOCIAL_TOKEN_TTL_SECONDS = 10 * 60
_PENDING_SSO_TOKEN_TTL_SECONDS = 10 * 60
_PENDING_PASSKEY_TOKEN_TTL_SECONDS = 10 * 60
_PENDING_SIGNIN_TOKEN_TTL_SECONDS = 10 * 60
_SOCIAL_PROFILE_PICTURE_TIMEOUT_SECONDS = 5.0
_SOCIAL_PROFILE_PICTURE_MAX_BYTES = 5 * 1024 * 1024
_PASSWORD_RESET_ATTEMPT_LUA = """
if redis.call('GET', KEYS[2]) then
    return 0
end

local current_count = redis.call('INCR', KEYS[1])
if current_count == 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end

redis.call('SET', KEYS[2], '1', 'EX', tonumber(ARGV[1]))

if current_count > tonumber(ARGV[3]) then
    return 0
end

return 1
"""
_GENERATED_PASSWORD_RESET_IP_HASH_SALT: str | None = None
_GENERATED_PASSWORD_RESET_IDENTIFIER_HASH_SALT: str | None = None


def _password_reset_ip_hash_salt() -> str:
    global _GENERATED_PASSWORD_RESET_IP_HASH_SALT

    # IP pseudonyms have their own lifecycle and must never change merely
    # because an authentication signing key rotates.
    salt = str(os.getenv("LOG_IP_HASH_SALT") or "").strip()
    if salt:
        return salt

    if _GENERATED_PASSWORD_RESET_IP_HASH_SALT is None:
        _GENERATED_PASSWORD_RESET_IP_HASH_SALT = secrets.token_urlsafe(32)
    return _GENERATED_PASSWORD_RESET_IP_HASH_SALT


def _minimize_password_reset_ip(ip_address: str | None) -> str | None:
    normalized = str(ip_address or "").strip()
    if not normalized:
        return None
    salt = _password_reset_ip_hash_salt()
    digest = hashlib.sha256(f"password_reset_ip:{salt}:{normalized}".encode("utf-8")).hexdigest()
    return f"ip_{digest[:16]}"


def _minimize_password_reset_user_agent(user_agent: str | None) -> str | None:
    normalized = " ".join(str(user_agent or "").split())
    if not normalized:
        return None
    normalized = "".join(char for char in normalized if char in string.printable)
    lowered = normalized.lower()

    if "edg/" in lowered or "edge/" in lowered:
        browser = "Edge"
    elif "opr/" in lowered or "opera" in lowered:
        browser = "Opera"
    elif "firefox/" in lowered:
        browser = "Firefox"
    elif "chrome/" in lowered or "crios/" in lowered:
        browser = "Chrome"
    elif "safari/" in lowered:
        browser = "Safari"
    elif "msie " in lowered or "trident/" in lowered:
        browser = "Internet Explorer"
    elif any(marker in lowered for marker in ("curl/", "python-requests", "httpx/", "wget/")):
        browser = "Automated client"
    else:
        browser = "Unknown browser"

    if "android" in lowered:
        platform = "Android"
    elif "iphone" in lowered or "ipad" in lowered or "ios" in lowered:
        platform = "iOS"
    elif "windows" in lowered:
        platform = "Windows"
    elif "mac os x" in lowered or "macintosh" in lowered:
        platform = "macOS"
    elif "linux" in lowered:
        platform = "Linux"
    else:
        platform = "Unknown platform"

    return f"{browser} on {platform}"[:255]

def _normalize_account_mode(value: Any) -> str:
    """Normalize account mode to 'add' or 'primary'."""
    return "add" if str(value or "").strip().lower() == "add" else "primary"


def _set_one_time_browser_cookie(response, key: str, value: str, db, request, *, max_age: int = 300) -> None:
    """Set a one-time browser cookie with appropriate security settings."""
    secure_cookie = should_secure_auth_cookie(db, request)
    samesite_policy = str(get_value_by_page_and_key("security", "refresh_cookie_samesite", db) or "lax").lower()
    if secure_cookie and samesite_policy != "none":
        samesite_policy = "none"
    elif samesite_policy not in {"lax", "strict", "none"}:
        samesite_policy = "lax"

    response.set_cookie(
        key=key,
        value=value,
        httponly=True,
        samesite=samesite_policy,
        secure=secure_cookie,
        max_age=max_age,
    )


def _set_one_time_browser_cookie_strict(response, key: str, value: str, db, request, *, max_age: int = 300) -> None:
    """Set a one-time browser cookie with strict SameSite policy."""
    secure_cookie = should_secure_auth_cookie(db, request)

    response.set_cookie(
        key=key,
        value=value,
        httponly=True,
        samesite="strict",
        secure=secure_cookie,
        max_age=max_age,
    )


def _clear_one_time_browser_cookie(response, key: str) -> None:
    """Clear a one-time browser cookie."""
    response.delete_cookie(key)


SOCIAL_PENDING_SIGNUP_COOKIE = "omlorix_social_pending_signup"
SSO_PENDING_SIGNUP_COOKIE = "omlorix_sso_pending_signup"
_PENDING_FEDERATED_SIGNUP_TTL_SECONDS = 600
_PENDING_FEDERATED_SIGNUP_COOKIE_MAX_BYTES = 3500
_PENDING_FEDERATED_USER_INFO_KEYS = {
    "email",
    "email_verified",
    "sub",
    "id",
    "provider_user_id",
    "provider_id",
    "given_name",
    "family_name",
    "name",
    "picture",
    "avatar_url",
    "omlorix_role",
    "omlorix_group_id",
    # Microsoft does not reliably emit ``email_verified``. Preserve the
    # bounded, server-produced identity proof used by the second half of a
    # Terms-gated signup so confirmation does not discard the verified oid/tid
    # relationship established by the OAuth callback.
    "microsoft_identity_verified",
    "tenant_id",
}


def _sanitize_pending_federated_user_info(user_info: dict[str, Any]) -> dict[str, Any]:
    """Keep only the provider attributes needed to finish a pending signup."""
    sanitized: dict[str, Any] = {}
    for key in _PENDING_FEDERATED_USER_INFO_KEYS:
        value = user_info.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            sanitized[key] = value
            continue
        sanitized[key] = str(value)[:2048]
    return sanitized


def _sanitize_pending_federated_flow_context(flow_context: dict[str, Any]) -> dict[str, Any]:
    """Keep only bounded login-flow context needed after terms confirmation."""
    replace_slot = flow_context.get("replace_slot")
    try:
        normalized_replace_slot = int(replace_slot) if replace_slot is not None else None
    except (TypeError, ValueError):
        normalized_replace_slot = None
    if normalized_replace_slot is not None and not 1 <= normalized_replace_slot <= MAX_ACCOUNT_SLOTS:
        normalized_replace_slot = None

    result = {
        "account_mode": _normalize_account_mode(flow_context.get("account_mode")),
        "replace_slot": normalized_replace_slot,
        "return_url": _sanitize_return_url(str(flow_context.get("return_url") or "")[:2048]),
    }
    if flow_context.get("native_auth") is True:
        result.update(
            {
                "native_auth": True,
                "native_kind": str(flow_context.get("native_kind") or "")[:16],
                "native_provider": str(flow_context.get("native_provider") or "")[:64],
                "native_code_challenge": str(flow_context.get("native_code_challenge") or "")[:128],
                "native_state": str(flow_context.get("native_state") or "")[:128],
            }
        )
    return result


def _build_pending_federated_signup_payload(
    *,
    kind: str,
    user_info: dict[str, Any],
    flow_context: dict[str, Any],
    **metadata: Any,
) -> dict[str, Any]:
    """Build the bounded payload encrypted into a pending signup cookie."""
    return {
        "kind": kind,
        **metadata,
        "user_info": _sanitize_pending_federated_user_info(user_info),
        "flow_context": _sanitize_pending_federated_flow_context(flow_context),
    }


def _pending_federated_cookie_cipher(db) -> Fernet:
    """Derive a deterministic Fernet key from the configured JWT secret."""
    secret, _algorithm = get_jwt_material()
    key = base64.urlsafe_b64encode(hashlib.sha256(f"pending-federated-signup:{secret}".encode()).digest())
    return Fernet(key)


def _set_pending_federated_signup_cookie(
    response,
    *,
    db,
    request,
    cookie_name: str,
    payload: dict[str, Any],
) -> None:
    """Store a short-lived encrypted pending signup context in an HttpOnly cookie."""
    token = _pending_federated_cookie_cipher(db).encrypt(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    if len(token.encode("utf-8")) > _PENDING_FEDERATED_SIGNUP_COOKIE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="pending_signup_payload_too_large")
    _set_one_time_browser_cookie(
        response,
        cookie_name,
        token,
        db,
        request,
        max_age=_PENDING_FEDERATED_SIGNUP_TTL_SECONDS,
    )


def _read_pending_federated_signup_cookie(request, db, *, cookie_name: str) -> dict[str, Any] | None:
    """Read and decrypt a pending federated signup cookie."""
    token = request.cookies.get(cookie_name)
    if not token:
        return None
    try:
        raw_payload = _pending_federated_cookie_cipher(db).decrypt(
            token.encode("ascii"),
            ttl=_PENDING_FEDERATED_SIGNUP_TTL_SECONDS,
        )
        payload = json.loads(raw_payload.decode("utf-8"))
    except (InvalidToken, UnicodeError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _clear_pending_federated_signup_cookie(response, *, cookie_name: str) -> None:
    """Clear a pending federated signup cookie."""
    response.delete_cookie(cookie_name)


def _redirect_clearing_pending_federated_signup_cookie(url: str, *, cookie_name: str, status_code: int = 302):
    """Create a redirect response that also clears the pending signup cookie."""
    from fastapi.responses import RedirectResponse

    redirect = RedirectResponse(url=url, status_code=status_code)
    _clear_pending_federated_signup_cookie(redirect, cookie_name=cookie_name)
    return redirect


def _json_error_clearing_pending_federated_signup_cookie(
    *,
    detail: str,
    status_code: int,
    cookie_name: str,
):
    """Create a JSON error response that also clears the pending signup cookie."""
    from fastapi.responses import JSONResponse

    response = JSONResponse({"detail": detail}, status_code=status_code)
    _clear_pending_federated_signup_cookie(response, cookie_name=cookie_name)
    return response


def _hash_token_value(value: str | None) -> str:
    """Hash a token value using SHA256."""
    return hashlib.sha256((value or "").encode()).hexdigest()


def _hash_pending_action_value(value: str | None, db, *, purpose: str) -> str:
    """Hash a short-lived browser action against the restore-safe epoch."""

    return hash_email_security_action(
        db,
        purpose=purpose,
        secret_value=value,
    )


def _get_nested_setting_value(settings: Any, path: Sequence[str]) -> Any:
    """Get a nested value from settings dictionary by path."""
    current = settings
    for segment in path:
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _build_nested_settings_fragment(path: Sequence[str], value: str) -> dict[str, Any]:
    """Build a nested dictionary fragment for settings updates."""
    fragment: Any = value
    for segment in reversed(path):
        fragment = {segment: fragment}
    return fragment


def _constant_time_string_equals(left: str, right: str) -> bool:
    """Compare strings in constant time without rejecting non-ASCII input."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _find_user_by_settings_value(
    db: Session,
    path: Sequence[str],
    candidate_values: Sequence[str],
    *,
    use_constant_time: bool = False,
):
    """Find a user by a nested settings value.
    
    NOTE: DB-side JSONB comparisons are not constant-time. We keep
    use_constant_time for backward-compatible call sites but use indexed
    equality where possible, then fall back for encrypted/legacy payloads.
    """
    values = list(dict.fromkeys(value for value in candidate_values if isinstance(value, str) and value))
    if not values or not path:
        return None

    try:
        settings_jsonb = cast(User.settings, JSONB)
        conditions = [
            settings_jsonb.contains(_build_nested_settings_fragment(path, candidate))
            for candidate in values
        ]
        user = db.query(User).filter(or_(*conditions)).first()
        if user:
            return user
    except Exception:
        # Some environments still store encrypted settings payloads that cannot
        # be indexed/queryable as JSONB. Keep a compatibility fallback.
        pass

    try:
        users = db.query(User).yield_per(200)
    except Exception:
        return None

    for user in users:
        stored_value = _get_nested_setting_value(getattr(user, "settings", None), path)
        if not isinstance(stored_value, str):
            continue
        for candidate in values:
            if use_constant_time:
                if _constant_time_string_equals(stored_value, candidate):
                    return user
            elif stored_value == candidate:
                return user
    return None


def _find_user_by_pending_auth_action(
    db: Session,
    *,
    purpose: str,
    path: Sequence[str],
    raw_token: str,
):
    """Resolve a short-lived action without scanning encrypted user settings.

    The first indexed read discovers the owning user.  We then lock in the
    global user-first order and repeat the action lookup under a row lock so a
    concurrent replacement or redemption cannot make a stale token usable.
    """

    token_hash = _hash_pending_action_value(raw_token, db, purpose=purpose)
    current = datetime.now(timezone.utc)
    preliminary = get_active_pending_auth_action(
        db,
        purpose=purpose,
        token_hash=token_hash,
        now=current,
    )
    if preliminary is None:
        return None

    user = (
        db.query(User)
        .populate_existing()
        .filter(User.id == preliminary.user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        return None
    action = get_active_pending_auth_action(
        db,
        purpose=purpose,
        token_hash=token_hash,
        now=current,
        for_update=True,
    )
    if action is None or action.user_id != user.id:
        return None

    stored_value = _get_nested_setting_value(getattr(user, "settings", None), path)
    if not isinstance(stored_value, str):
        return None
    if not _constant_time_string_equals(stored_value, token_hash):
        return None
    return user


def _find_user_by_pending_social_auth_code(db: Session, auth_code: str):
    """Find user by pending social auth code."""
    return _find_user_by_pending_auth_action(
        db,
        purpose=_PENDING_ACTION_SOCIAL_AUTH_CODE,
        path=("social_login", "pending_auth_code"),
        raw_token=auth_code,
    )


def _find_user_by_pending_sso_auth_code(db: Session, auth_code: str):
    """Find user by pending SSO auth code."""
    return _find_user_by_pending_auth_action(
        db,
        purpose=_PENDING_ACTION_SSO_AUTH_CODE,
        path=("sso_login", "pending_auth_code"),
        raw_token=auth_code,
    )


def _find_user_by_pending_social_token(db: Session, social_token: str):
    """Find user by pending social token."""
    return _find_user_by_pending_auth_action(
        db,
        purpose=_PENDING_ACTION_SOCIAL_LOGIN,
        path=("social_login", "pending_social_token"),
        raw_token=social_token,
    )


def _find_user_by_pending_signin_token(db: Session, signin_token: str):
    """Find user by pending password signin token."""
    return _find_user_by_pending_auth_action(
        db,
        purpose=_PENDING_ACTION_PASSWORD_SIGNIN,
        path=("secret", "signin_pending_token"),
        raw_token=signin_token,
    )


def _commit_pending_auth_action(
    db: Session,
    *,
    user_id: str,
    purpose: str,
    token_hash: str,
    expires_at: datetime,
    settings_updates: dict[str, dict[str, Any]],
) -> None:
    """Atomically replace indexed action state and its settings context."""

    user = (
        db.query(User)
        .populate_existing()
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        replace_pending_auth_action(
            db,
            user_id=user.id,
            purpose=purpose,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        update_user_settings_bulk(
            user.id,
            settings_updates,
            db,
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def _commit_pending_auth_action_clear(
    db: Session,
    *,
    user_id: str,
    purpose: str,
    settings_updates: dict[str, dict[str, Any]],
    expected_token_hash: str | None = None,
) -> bool:
    """Atomically clear an indexed action and its settings context."""

    user = (
        db.query(User)
        .populate_existing()
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        deleted = delete_pending_auth_action(
            db,
            user_id=user.id,
            purpose=purpose,
            token_hash=expected_token_hash,
            commit=False,
        )
        if expected_token_hash is not None and deleted != 1:
            db.rollback()
            return False
        update_user_settings_bulk(
            user.id,
            settings_updates,
            db,
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True


def _set_pending_social_token(
    user_id: str,
    provider: str,
    db,
    *,
    allow_setup_material: bool = False,
) -> tuple[str, datetime]:
    """Set pending social login token for user."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PENDING_SOCIAL_TOKEN_TTL_SECONDS)
    token_hash = _hash_pending_action_value(
        token,
        db,
        purpose=_PENDING_ACTION_SOCIAL_LOGIN,
    )
    _commit_pending_auth_action(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_SOCIAL_LOGIN,
        token_hash=token_hash,
        expires_at=expires_at,
        settings_updates={
            "social_login": {
                "pending_social_token": token_hash,
                "pending_social_token_expires": expires_at.isoformat(),
                "pending_provider": provider,
                "pending_setup_material_allowed": bool(allow_setup_material),
            },
        },
    )
    return token, expires_at


def _clear_pending_social_token(
    user_id: str,
    db,
    *,
    raw_token: str | None = None,
) -> bool:
    """Clear pending social login token for user."""
    return _commit_pending_auth_action_clear(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_SOCIAL_LOGIN,
        expected_token_hash=(
            _hash_pending_action_value(
                raw_token,
                db,
                purpose=_PENDING_ACTION_SOCIAL_LOGIN,
            )
            if raw_token is not None
            else None
        ),
        settings_updates={
            "social_login": {
                "pending_social_token": "",
                "pending_social_token_expires": "",
                "pending_provider": "",
                "pending_setup_material_allowed": False,
            },
        },
    )


def _set_pending_signin_token(
    user_id: str,
    db,
    *,
    allow_setup_material: bool = False,
) -> tuple[str, datetime]:
    """Set pending password signin token for user."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PENDING_SIGNIN_TOKEN_TTL_SECONDS)
    token_hash = _hash_pending_action_value(
        token,
        db,
        purpose=_PENDING_ACTION_PASSWORD_SIGNIN,
    )
    _commit_pending_auth_action(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_PASSWORD_SIGNIN,
        token_hash=token_hash,
        expires_at=expires_at,
        settings_updates={
            "secret": {
                "signin_pending_token": token_hash,
                "signin_pending_token_expires_at": expires_at.isoformat(),
                "signin_pending_setup_material_allowed": bool(allow_setup_material),
            },
        },
    )
    return token, expires_at


def _clear_pending_signin_token(
    user_id: str,
    db,
    *,
    raw_token: str | None = None,
) -> bool:
    """Clear pending password signin token for user."""
    return _commit_pending_auth_action_clear(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_PASSWORD_SIGNIN,
        expected_token_hash=(
            _hash_pending_action_value(
                raw_token,
                db,
                purpose=_PENDING_ACTION_PASSWORD_SIGNIN,
            )
            if raw_token is not None
            else None
        ),
        settings_updates={
            "secret": {
                "signin_pending_token": "",
                "signin_pending_token_expires_at": "",
                "signin_pending_setup_material_allowed": False,
            },
        },
    )


def _guess_profile_picture_filename(provider: str, content_type: str | None = None) -> str | None:
    """Guess profile picture filename from content type."""
    extension = mimetypes.guess_extension(content_type or "") if content_type else None
    if extension not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return None
    return f"{provider or 'oauth'}_avatar{extension}"


def _oauth_profile_picture_import_key(provider: str) -> str | None:
    """Return the admin setting that controls OAuth avatar import for a provider."""
    normalized_provider = str(provider or "").strip().lower()
    provider_keys = {
        "google": "import_google_oauth_profile_picture",
        "github": "import_github_oauth_profile_picture",
        "slack": "import_slack_oauth_profile_picture",
        "microsoft": "import_microsoft_oauth_profile_picture",
    }
    return provider_keys.get(normalized_provider)


async def _download_social_profile_picture(url: str) -> tuple[bytes, str] | tuple[None, None]:
    """Download social profile picture from URL."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return None, None

    timeout = httpx.Timeout(_SOCIAL_PROFILE_PICTURE_TIMEOUT_SECONDS)
    headers = {"Accept": "image/*"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    return None, None
                content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                if not content_type.startswith("image/"):
                    return None, None

                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > _SOCIAL_PROFILE_PICTURE_MAX_BYTES:
                        return None, None
                    chunks.append(chunk)
        except httpx.HTTPError:
            return None, None

    if not chunks:
        return None, None
    return b"".join(chunks), content_type


async def _sync_social_profile_picture(user, *, provider: str, user_info: dict[str, Any], db) -> None:
    """Sync social profile picture for user."""
    if not user or bool(getattr(user, "custom_profile_picture", False)):
        return
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in {"google", "github", "slack", "microsoft"}:
        return
    import_setting_key = _oauth_profile_picture_import_key(normalized_provider)
    if not import_setting_key:
        return
    if not coerce_bool(get_value_by_page_and_key("login_social", import_setting_key, db), default=False):
        return
    if coerce_bool(get_user_setting_value(user.id, "social_login", "oauth_profile_picture_sync_disabled", db), default=False):
        return

    file_bytes = user_info.get("profile_picture_bytes")
    content_type = str(user_info.get("profile_picture_content_type") or "").strip().lower()
    original_filename = None

    if isinstance(file_bytes, bytes) and file_bytes:
        original_filename = _guess_profile_picture_filename(normalized_provider, content_type)
    else:
        picture_url = str(user_info.get("profile_picture_url") or "").strip()
        if not picture_url:
            return
        downloaded_bytes, downloaded_content_type = await _download_social_profile_picture(picture_url)
        if not downloaded_bytes:
            return
        file_bytes = downloaded_bytes
        content_type = downloaded_content_type or ""
        original_filename = _guess_profile_picture_filename(normalized_provider, content_type)

    try:
        from app.users.utils import save_oauth_profile_picture

        save_oauth_profile_picture(
            user.id,
            provider=normalized_provider,
            file_content=file_bytes,
            original_filename=original_filename,
            db=db,
        )
    except HTTPException as exc:
        logger.info("Skipping OAuth profile picture sync for user %s via %s: %s", user.id, normalized_provider, exc.detail)
    except Exception:
        logger.exception("OAuth profile picture sync failed for user %s via %s", user.id, normalized_provider)


def _set_pending_sso_token(
    user_id: str,
    provider_type: str,
    db,
    *,
    allow_setup_material: bool = False,
) -> tuple[str, datetime]:
    """Set pending SSO login token for user."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PENDING_SSO_TOKEN_TTL_SECONDS)
    token_hash = _hash_pending_action_value(
        token,
        db,
        purpose=_PENDING_ACTION_SSO_LOGIN,
    )
    _commit_pending_auth_action(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_SSO_LOGIN,
        token_hash=token_hash,
        expires_at=expires_at,
        settings_updates={
            "sso_login": {
                "pending_sso_token": token_hash,
                "pending_sso_token_expires": expires_at.isoformat(),
                "pending_provider_type": provider_type,
                "pending_setup_material_allowed": bool(allow_setup_material),
            },
        },
    )
    return token, expires_at


def _clear_pending_sso_token(
    user_id: str,
    db,
    *,
    raw_token: str | None = None,
) -> bool:
    """Clear pending SSO login token for user."""
    return _commit_pending_auth_action_clear(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_SSO_LOGIN,
        expected_token_hash=(
            _hash_pending_action_value(
                raw_token,
                db,
                purpose=_PENDING_ACTION_SSO_LOGIN,
            )
            if raw_token is not None
            else None
        ),
        settings_updates={
            "sso_login": {
                "pending_sso_token": "",
                "pending_sso_token_expires": "",
                "pending_provider_type": "",
                "pending_setup_material_allowed": False,
            },
        },
    )


def _set_pending_passkey_token(
    user_id: str,
    db,
    *,
    allow_setup_material: bool = False,
) -> tuple[str, datetime]:
    """Set pending passkey token for user."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PENDING_PASSKEY_TOKEN_TTL_SECONDS)
    token_hash = _hash_pending_action_value(
        token,
        db,
        purpose=_PENDING_ACTION_PASSKEY_LOGIN,
    )
    _commit_pending_auth_action(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_PASSKEY_LOGIN,
        token_hash=token_hash,
        expires_at=expires_at,
        settings_updates={
            "secret": {
                "passkey_pending_token": token_hash,
                "passkey_pending_token_expires_at": expires_at.isoformat(),
                "passkey_pending_setup_material_allowed": bool(allow_setup_material),
            },
        },
    )
    return token, expires_at


def _clear_pending_passkey_token(
    user_id: str,
    db,
    *,
    raw_token: str | None = None,
) -> bool:
    """Clear pending passkey token for user."""
    return _commit_pending_auth_action_clear(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_PASSKEY_LOGIN,
        expected_token_hash=(
            _hash_pending_action_value(
                raw_token,
                db,
                purpose=_PENDING_ACTION_PASSKEY_LOGIN,
            )
            if raw_token is not None
            else None
        ),
        settings_updates={
            "secret": {
                "passkey_pending_token": "",
                "passkey_pending_token_expires_at": "",
                "passkey_pending_setup_material_allowed": False,
            },
        },
    )


def _find_user_by_pending_passkey_token(db, passkey_token: str):
    """Find user by pending passkey token."""
    return _find_user_by_pending_auth_action(
        db,
        purpose=_PENDING_ACTION_PASSKEY_LOGIN,
        path=("secret", "passkey_pending_token"),
        raw_token=passkey_token,
    )


def _pending_token_is_active(user_id: str, page: str, expires_key: str, db) -> bool:
    """Return whether a pending login token is still within its TTL."""
    expires_str = str(
        get_user_setting_value(
            user_id,
            page,
            expires_key,
            db,
            commit=False,
        )
        or ""
    ).strip()
    if not expires_str:
        return False
    try:
        expires_at = datetime.fromisoformat(expires_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return datetime.now(timezone.utc) <= expires_at


def _pending_token_allows_setup_material(user_id: str, page: str, allow_key: str, db) -> bool:
    """Return whether a pending login token was created for a 2FA setup flow."""
    return coerce_bool(
        get_user_setting_value(
            user_id,
            page,
            allow_key,
            db,
            commit=False,
        ),
        default=False,
    )


def _sanitize_return_url(value: Any) -> str:
    """Sanitize return URL to prevent open redirect attacks."""
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    if not trimmed or "\\" in trimmed:
        return ""
    parsed = urlsplit(trimmed)
    if parsed.scheme or parsed.netloc:
        return ""
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return ""
    sanitized = parsed.path
    if parsed.query:
        sanitized += f"?{parsed.query}"
    if parsed.fragment:
        sanitized += f"#{parsed.fragment}"
    return sanitized


def _build_login_url(
    *,
    account_mode: str = "primary",
    return_url: str = "",
    query_params: dict[str, Any] | None = None,
    fragment: str = "",
) -> str:
    """Build login URL with optional parameters."""
    from urllib.parse import urlencode

    params: dict[str, Any] = {}
    if account_mode == "add":
        params["mode"] = "add"
    if return_url:
        params["return"] = return_url
    if query_params:
        params.update({key: value for key, value in query_params.items() if value not in (None, "")})

    url = "/login"
    if params:
        url += f"?{urlencode(params)}"
    if fragment:
        url += f"#{fragment}"
    return url


def _copy_response_set_cookies(source: Any, target: Response) -> None:
    """Copy set-cookie headers from source to target response."""
    headers = getattr(source, "headers", None)
    if not headers or not hasattr(headers, "getlist"):
        return
    for set_cookie in headers.getlist("set-cookie"):
        target.headers.append("set-cookie", set_cookie)


def _max_accounts_reached_response(request, response, db):
    """Return response when max accounts reached."""
    payload = list_accounts_payload(request, db)
    return {
        "status": "max_accounts_reached",
        "max_accounts": MAX_ACCOUNT_SLOTS,
        "accounts": payload.get("accounts", []),
    }


def _enforce_session_auth_authority(
    *,
    user,
    auth_method: str,
    db_log,
    log_event: str,
    user_agent: str,
    client_ip: str,
) -> None:
    if not is_externally_managed(user) or auth_method == "sso":
        return
    create_authentication_log(
        db_log,
        log_event,
        "warning",
        "Session issuance blocked because enterprise authentication is authoritative",
        user.id,
        user_agent,
        client_ip,
    )
    record_auth_login_attempt_metric(
        False,
        method=auth_method,
        reason="external_auth_required",
    )
    require_locally_managed_account(user)


def _lock_user_for_session_issuance(db, user):
    """Refresh auth authority under a row lock immediately before insertion."""

    if not isinstance(db, Session):
        # Focused unit tests use lightweight database doubles. Runtime FastAPI
        # dependencies always supply a SQLAlchemy Session.
        return user
    return (
        db.query(User)
        .populate_existing()
        .filter(User.id == user.id)
        .with_for_update()
        .first()
    )


def _current_user_row_login_eligibility(user) -> dict[str, object] | None:
    """Recheck mutable account state without releasing the issuance row lock."""

    if getattr(user, "deleted_at", None) is not None:
        return {"status": "deleted"}
    if getattr(user, "role", None) == "pending":
        return {"status": "pending"}
    if not getattr(user, "is_active", True):
        return {"status": "inactive"}
    if getattr(user, "account_type", "regular") == "temporary":
        expires_at = normalize_utc_datetime(
            getattr(user, "temporary_expires_at", None)
        )
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            return {"status": "temporary_expired"}
    lock = evaluate_user_lock(user)
    if isinstance(lock, dict) and bool(lock.get("is_locked")):
        lock_until = lock.get("lock_until")
        if lock_until is None or lock_until > datetime.now(timezone.utc):
            return {
                "status": "lock",
                "expires": (
                    max(
                        0,
                        int(
                            (lock_until - datetime.now(timezone.utc)).total_seconds()
                        ),
                    )
                    if lock_until is not None
                    else 0
                ),
                "type": lock.get("type") or "",
                "reason": lock.get("reason") or "",
            }
    return None


def _issue_authenticated_session(
    *,
    db,
    db_log,
    request,
    response,
    user,
    log_event: str,
    success_message: str,
    account_mode: str = "primary",
    replace_slot: int | None = None,
    twofa_satisfied: bool = False,
    password_proof_binding: tuple[str, str] | None = None,
):
    """Issue authenticated session with tokens."""
    client_ip = _client_ip_from_request(request, db)
    user_agent = request.headers.get("User-Agent", "Unknown Device")
    auth_method = _auth_metric_method(log_event)
    _enforce_session_auth_authority(
        user=user,
        auth_method=auth_method,
        db_log=db_log,
        log_event=log_event,
        user_agent=user_agent,
        client_ip=client_ip,
    )
    eligibility = validate_user_login_eligibility(user, db)
    if eligibility:
        create_authentication_log(
            db_log,
            log_event,
            "warning",
            f"Session issuance blocked by login eligibility: {eligibility.get('status')}",
            user.id,
            user_agent,
            client_ip,
        )
        record_auth_login_attempt_metric(False, method=auth_method, reason=str(eligibility.get("status") or "eligibility"))
        return eligibility

    from app.auth.token import create_access_token, create_refresh_token

    slot_assignment = resolve_slot_assignment(
        request,
        response,
        db,
        user_id=user.id,
        account_mode=account_mode,
        replace_slot=replace_slot,
    )
    if slot_assignment is None:
        record_auth_login_attempt_metric(False, method=auth_method, reason="max_accounts_reached")
        return _max_accounts_reached_response(request, response, db)

    # The stable session id ties every rotated token pair to exactly one
    # authentication row. It lets replay handling revoke the affected device
    # without touching the user's other signed-in devices.
    session_id = str(uuid.uuid4())
    session_claims = build_login_2fa_session_claims(user, db) if twofa_satisfied else {}
    refresh_token = create_refresh_token(
        data={"sub": user.id, "type": "refresh", "sid": session_id, **session_claims},
        db=db,
    )
    access_token = create_access_token(
        data={"sub": user.id, "type": "access", "sid": session_id, **session_claims},
        db=db,
    )

    user = _lock_user_for_session_issuance(db, user)
    if user is None:
        if isinstance(db, Session):
            db.rollback()
        raise HTTPException(status_code=401, detail="Authentication failed.")
    if password_proof_binding is not None:
        expected_email, expected_password_hash = password_proof_binding
        current_email = canonicalize_user_email(getattr(user, "email", None)) or ""
        current_password_hash = str(getattr(user, "hashed_password", "") or "")
        if not (
            hmac.compare_digest(current_email, expected_email)
            and hmac.compare_digest(current_password_hash, expected_password_hash)
        ):
            create_authentication_log(
                db_log,
                log_event,
                "warning",
                "Session issuance blocked because the verified password identity changed",
                user.id,
                user_agent,
                client_ip,
            )
            record_auth_login_attempt_metric(
                False,
                method=auth_method,
                reason="password_identity_changed",
            )
            if isinstance(db, Session):
                db.rollback()
            return {"status": "InvalidCredentials"}
    try:
        _enforce_session_auth_authority(
            user=user,
            auth_method=auth_method,
            db_log=db_log,
            log_event=log_event,
            user_agent=user_agent,
            client_ip=client_ip,
        )
    except Exception:
        if hasattr(db, "rollback"):
            db.rollback()
        raise
    current_eligibility = _current_user_row_login_eligibility(user)
    if current_eligibility:
        create_authentication_log(
            db_log,
            log_event,
            "warning",
            f"Session issuance blocked by current account state: {current_eligibility.get('status')}",
            user.id,
            user_agent,
            client_ip,
        )
        record_auth_login_attempt_metric(
            False,
            method=auth_method,
            reason=str(current_eligibility.get("status") or "eligibility"),
        )
        if isinstance(db, Session):
            db.rollback()
        return current_eligibility
    try:
        cookie_settings = resolve_auth_cookie_settings(db, request)
    except Exception:
        if hasattr(db, "rollback"):
            db.rollback()
        raise
    replaced_rows: list = []

    def stage_slot_replacement(deleted_rows: list) -> None:
        replaced_rows.extend(deleted_rows)
        stage_audit_log_event(
            db,
            user_id=user.id,
            action="ACCOUNT_SLOT_REPLACED",
            details={
                "slot": slot_assignment.slot,
                "replaced_user_id": slot_assignment.replaced_user_id,
                "replacement_reason": slot_assignment.replacement_reason,
                "same_account": slot_assignment.replaced_user_id == user.id,
            },
            ip_address=client_ip,
            user_agent=user_agent,
            category="auth_security",
        )

    try:
        new_auth = create_authentication(
            db,
            user.id,
            user_agent,
            client_ip,
            access_token,
            refresh_token,
            session_id=session_id,
            commit=False,
        )
        if not new_auth:
            raise HTTPException(status_code=500, detail="Authentication failed.")
        if slot_assignment.replaced_refresh_token:
            replacement_deleted = delete_authentication(
                db,
                refresh_token=slot_assignment.replaced_refresh_token,
                before_commit=stage_slot_replacement,
                commit=False,
            )
            if not replacement_deleted:
                raise HTTPException(
                    status_code=409,
                    detail="The selected account slot changed. Please retry sign-in.",
                )
        stage_audit_log_event(
            db,
            user_id=user.id,
            action="LOGIN_SUCCEEDED",
            details={
                "login_method": auth_method,
                "account_slot": slot_assignment.slot,
                "replaced_account_slot": bool(replaced_rows),
            },
            ip_address=client_ip,
            user_agent=user_agent,
            category="auth_security",
        )
        user.last_active_at = datetime.now(timezone.utc)
        db.add(user)
        db.commit()
    except Exception:
        if hasattr(db, "rollback"):
            db.rollback()
        raise

    for row in replaced_rows:
        revoke_token_digests(
            user_id=row.user_id,
            access_token_hash=row.access_token_hash,
            refresh_token_hash=row.refresh_token_hash,
        )
    cache_session(user.id, access_token, refresh_token)

    active_slot = finalize_slot_assignment(
        request,
        response,
        db,
        slot_assignment=slot_assignment,
        refresh_token=refresh_token,
        cookie_settings=cookie_settings,
    )

    try:
        create_authentication_log(
            db_log,
            log_event,
            "info",
            success_message,
            user.id,
            user_agent,
            client_ip,
        )
    except Exception:
        # The transactional LOGIN_SUCCEEDED event is already durable. A
        # secondary auth-log outage must not strand a committed session without
        # returning its cookies to the user.
        logger.exception("Unable to mirror successful login to authentication log")
    record_auth_login_attempt_metric(True, method=auth_method)
    set_access_token_cookie(
        response,
        access_token,
        db,
        request,
        cookie_settings=cookie_settings,
    )
    try:
        from app.email.devices import register_login_device

        register_login_device(
            db,
            request=request,
            response=response,
            user=user,
            client_ip=client_ip,
        )
    except Exception:
        db.rollback()
        # Device-notification availability must never invalidate an otherwise
        # successful authentication. The login audit remains authoritative.
        logger.exception("Unable to register login device for security notification")
    return {
        "session_authenticated": True,
        "active_account_slot": active_slot,
    }



def _coerce_positive_int(value: Any, default: int, *, setting_name: str | None = None) -> int:
    """Convert ``value`` to a positive integer or fall back to ``default``.

    Guards against invalid admin-configured values that would otherwise raise
    ``ValueError`` when cast via ``int()``.
    """

    name = setting_name or "value"
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("empty string")
        minutes = int(value)
        if minutes <= 0:
            raise ValueError("non-positive integer")
        return minutes
    except Exception:
        logger.warning(
            "Invalid %s %r; falling back to default %s",
            name,
            value,
            default,
        )
        return default


def _coerce_positive_float(value: Any, default: float, *, setting_name: str | None = None) -> float:
    """Coerce value to positive float or return default."""
    name = setting_name or "value"
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("empty string")
        minutes = float(value)
        if minutes <= 0:
            raise ValueError("non-positive float")
        return minutes
    except Exception:
        logger.warning(
            "Invalid %s %r; falling back to default %s",
            name,
            value,
            default,
        )
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    """Coerce value to integer or return default."""
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        if isinstance(value, str) and value.strip():
            return int(value.strip())
    except (TypeError, ValueError):
        return default
    return default


def _is_new_account_registration_enabled(db) -> bool:
    """Return whether creating new accounts is allowed globally."""
    return coerce_bool(get_value_by_page_and_key("login_general", "enable_signup", db))


class TermsOfServiceSignupError(Exception):
    """Raised when self-service account creation cannot satisfy terms requirements."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int,
        revision: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.revision = revision


def _extract_terms_acceptance(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        accepted = bool(source.get("accept_terms_of_service"))
        revision = source.get("terms_of_service_revision")
    else:
        accepted = bool(getattr(source, "accept_terms_of_service", False))
        revision = getattr(source, "terms_of_service_revision", None)

    try:
        normalized_revision = int(revision) if revision is not None else None
    except (TypeError, ValueError):
        normalized_revision = None

    return {
        "accept_terms_of_service": accepted,
        "terms_of_service_revision": normalized_revision,
    }


def _require_terms_ready_for_self_service_signup(db, source: Any) -> dict[str, Any]:
    policy = get_terms_of_service_policy(db)
    if not bool(policy.get("require_current_revision_for_signup")):
        return {}
    current_revision = int(policy.get("revision") or 1)

    acceptance = _extract_terms_acceptance(source)
    if not acceptance["accept_terms_of_service"]:
        raise TermsOfServiceSignupError(
            "terms_acceptance_required",
            "You must accept the current terms of service before creating an account.",
            status_code=400,
            revision=current_revision,
        )
    if acceptance["terms_of_service_revision"] != current_revision:
        raise TermsOfServiceSignupError(
            "terms_revision_mismatch",
            "The terms of service changed. Review the latest version and try again.",
            status_code=409,
            revision=current_revision,
        )

    return {
        "revision": current_revision,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }


def _record_terms_of_service_acceptance(
    *,
    db,
    db_log,
    request,
    user_id: str,
    revision: int,
    accepted_at: str,
    source: str,
) -> None:
    update_user_settings_bulk(
        user_id,
        {
            "states": {
                "terms_of_service_accepted_revision": revision,
                "terms_of_service_accepted_at": accepted_at,
            }
        },
        db,
    )
    try:
        create_audit_log(
            db_log=db_log,
            user_id=user_id,
            action="ACCEPT_TERMS_OF_SERVICE",
            details={
                "revision": revision,
                "accepted_at": accepted_at,
                "source": source,
            },
            ip_address=_client_ip_from_request(request, db) if request is not None else None,
            user_agent=request.headers.get("User-Agent") if request is not None else None,
            category="auth",
        )
    except Exception:
        logger.exception("Failed to create terms-of-service acceptance audit log")


def _normalize_ldap_group_token(value: Any) -> str:
    """Normalize LDAP group token."""
    if not isinstance(value, str):
        value = str(value or "")
    return value.strip().lower()


def _ldap_group_match_tokens(value: Any) -> set[str]:
    """Parse LDAP group tokens from value."""
    text = _normalize_ldap_group_token(value)
    if not text:
        return set()
    tokens = {text}
    if "," in text:
        for segment in text.split(","):
            segment = segment.strip()
            if segment.startswith("cn="):
                tokens.add(segment[3:].strip())
                break
    return {token for token in tokens if token}


def _parse_ldap_mapping_entries(entries: Any) -> list[tuple[str, str]]:
    """Parse LDAP mapping entries from configuration."""
    result: list[tuple[str, str]] = []
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if not isinstance(entry, str):
            continue
        left, separator, right = entry.partition("=")
        if not separator:
            continue
        key = _normalize_ldap_group_token(left)
        value = right.strip()
        if key and value:
            result.append((key, value))
    return result


def _resolve_group_id_from_setting(db: Session, raw_value: Any, default: str = "default") -> str:
    """Resolve group ID from setting value."""
    candidate = str(raw_value or "").strip() or default
    if get_group(db, candidate):
        return candidate
    by_name = get_group_by_name(db, candidate)
    if by_name:
        return by_name.id
    if get_group(db, default):
        return default
    raise HTTPException(status_code=500, detail="Configured Omlorix group does not exist.")


def _resolve_ldap_group_target(db: Session, settings: dict[str, Any], ldap_groups: list[Any]) -> str:
    """Resolve LDAP group target from LDAP groups."""
    group_tokens: set[str] = set()
    for group in ldap_groups:
        group_tokens.update(_ldap_group_match_tokens(group))

    for key, target in _parse_ldap_mapping_entries(settings.get("ldap_group_to_app_group")):
        if key in group_tokens:
            try:
                return _resolve_group_id_from_setting(db, target)
            except HTTPException:
                logger.warning("Ignoring LDAP Omlorix group mapping to unknown group %s", target)
    return _resolve_group_id_from_setting(db, settings.get("ldap_default_group"), "default")


def _resolve_ldap_role(settings: dict[str, Any], ldap_groups: list[Any]) -> str:
    """Resolve LDAP role from LDAP groups."""
    group_tokens: set[str] = set()
    for group in ldap_groups:
        group_tokens.update(_ldap_group_match_tokens(group))

    for key, target in _parse_ldap_mapping_entries(settings.get("ldap_group_to_role")):
        if key in group_tokens:
            normalized_target = str(target or "").strip().lower()
            if normalized_target in {"user", "pending"}:
                return normalized_target
    default_role = str(settings.get("ldap_default_role") or "user").strip().lower()
    return normalize_external_role(default_role)


def _ldap_groups_to_tokens(ldap_user: LDAPAuthenticatedUser) -> list[str]:
    """Convert LDAP groups to tokens."""
    tokens: list[str] = []
    for group in ldap_user.groups:
        tokens.extend(token for token in _ldap_group_match_tokens(group.dn) if token)
        tokens.extend(token for token in _ldap_group_match_tokens(group.name) if token)
    # preserve order while removing duplicates
    return list(dict.fromkeys(tokens))


def _enforce_ldap_required_groups(settings: dict[str, Any], ldap_user: LDAPAuthenticatedUser) -> None:
    """Enforce that LDAP user is a member of required groups."""
    if not coerce_bool(settings.get("ldap_enable_group_sync"), False):
        return
    required_tokens = {
        _normalize_ldap_group_token(entry)
        for entry in settings.get("ldap_required_groups", [])
        if isinstance(entry, str) and entry.strip()
    }
    if not required_tokens:
        return
    available = set(_ldap_groups_to_tokens(ldap_user))
    if not (required_tokens & available):
        raise HTTPException(status_code=403, detail="LDAP user is not a member of any required groups.")


def _audit_external_role_sync(
    *,
    user_id: str,
    old_role: str | None,
    new_role: str | None,
    source: str,
    request,
    db: Session | None,
    source_context: dict[str, Any] | None = None,
) -> None:
    if old_role == new_role:
        return

    audit_db = AuditSessionLocal()
    try:
        create_audit_log(
            db_log=audit_db,
            user_id=user_id,
            action="EXTERNAL_ROLE_SYNC_CHANGED",
            details={
                "target_user_id": user_id,
                "old_role": old_role,
                "new_role": new_role,
                "source": source,
                "source_context": source_context or {},
            },
            ip_address=_client_ip_from_request(request, db) if request is not None else None,
            user_agent=request.headers.get("User-Agent") if request is not None else None,
            category="auth",
        )
    except Exception:
        logger.exception(
            "Failed to audit %s role sync change for user %s",
            source,
            user_id,
        )


def _find_existing_user_for_ldap(db: Session, ldap_user: LDAPAuthenticatedUser):
    """Find existing user for LDAP authentication."""
    if ldap_user.directory_user_id:
        user = _find_user_by_settings_value(
            db,
            ("ldap_login", "directory_user_id"),
            [ldap_user.directory_user_id],
        )
        if user:
            return user
    if ldap_user.dn:
        user = _find_user_by_settings_value(
            db,
            ("ldap_login", "directory_dn"),
            [ldap_user.dn],
        )
        if user:
            return user
    if user_exists_by_email(db, ldap_user.email):
        return get_user(db, email=ldap_user.email)
    return None


def _validate_linked_ldap_identity(user_id: str, ldap_user: LDAPAuthenticatedUser, db: Session) -> None:
    """Ensure an LDAP-linked account is still authenticating as the same directory identity."""
    stored_directory_user_id = str(
        get_user_setting_value(user_id, "ldap_login", "directory_user_id", db) or ""
    ).strip()
    incoming_directory_user_id = str(ldap_user.directory_user_id or "").strip()
    if stored_directory_user_id:
        if incoming_directory_user_id and secrets.compare_digest(
            stored_directory_user_id,
            incoming_directory_user_id,
        ):
            return
        raise HTTPException(status_code=403, detail="The linked LDAP account does not match this user.")

    stored_dn = str(
        get_user_setting_value(user_id, "ldap_login", "directory_dn", db) or ""
    ).strip()
    incoming_dn = str(ldap_user.dn or "").strip()
    if stored_dn:
        if incoming_dn and secrets.compare_digest(stored_dn, incoming_dn):
            return
        raise HTTPException(status_code=403, detail="The linked LDAP account does not match this user.")

    raise HTTPException(status_code=403, detail="The linked LDAP account is missing a stored directory identity.")


def _sync_existing_user_from_ldap(
    db: Session,
    user,
    ldap_user: LDAPAuthenticatedUser,
    settings: dict[str, Any],
    *,
    resolved_group_id: str,
    resolved_role: str,
    request=None,
) -> None:
    """Sync existing user from LDAP data."""
    changed = False
    old_role = getattr(user, "role", None)
    role_changed = False
    # Normalize again at the mutation boundary. This keeps a future caller
    # from bypassing the directory-role resolver with a privileged value.
    resolved_role = normalize_external_role(resolved_role)

    if coerce_bool(settings.get("ldap_sync_profile_on_login"), True):
        if ldap_user.first_name and user.first_name != ldap_user.first_name:
            user.first_name = ldap_user.first_name
            changed = True
        if user.last_name != ldap_user.last_name:
            user.last_name = ldap_user.last_name
            changed = True

    if coerce_bool(settings.get("ldap_sync_email_on_login"), True) and ldap_user.email and user.email != ldap_user.email:
        email_owner = None
        try:
            email_owner = get_user(db, email=ldap_user.email)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        if not email_owner or email_owner.id == user.id:
            user.email = ldap_user.email
            changed = True

    group_sync_enabled = coerce_bool(settings.get("ldap_enable_group_sync"), False)
    if (
        group_sync_enabled
        and coerce_bool(settings.get("ldap_sync_app_group_on_login"), True)
        and user.group_id != resolved_group_id
    ):
        user.group_id = resolved_group_id
        changed = True

    if (
        group_sync_enabled
        and coerce_bool(settings.get("ldap_sync_role_on_login"), True)
        and not is_admin_role(user.role)
        and user.role != resolved_role
    ):
        user.role = resolved_role
        changed = True
        role_changed = True

    if changed:
        db.commit()
        db.refresh(user)
        if role_changed:
            _audit_external_role_sync(
                user_id=user.id,
                old_role=old_role,
                new_role=getattr(user, "role", None),
                source="ldap",
                request=request,
                db=db,
                source_context={
                    "directory_user_id_present": bool(ldap_user.directory_user_id),
                    "directory_dn_present": bool(ldap_user.dn),
                    "group_count": len(ldap_user.groups or []),
                },
            )


def _link_user_to_ldap(db: Session, user, ldap_user: LDAPAuthenticatedUser) -> None:
    """Link user to LDAP directory."""
    update_user_settings_bulk(
        user.id,
        {
            "ldap_login": {
                "linked": True,
                "directory_user_id": ldap_user.directory_user_id,
                "directory_dn": ldap_user.dn,
                "directory_username": ldap_user.username,
                "last_login_identifier": ldap_user.identifier,
                "last_synced_at": datetime.now(timezone.utc).isoformat(),
                "last_synced_groups": [group.dn or group.name for group in ldap_user.groups if group.dn or group.name],
            }
        },
        db,
    )


def _provision_or_sync_ldap_user(
    db: Session,
    db_log,
    ldap_user: LDAPAuthenticatedUser,
    request,
    *,
    terms_acceptance: dict[str, Any] | None = None,
):
    """Provision new user or sync existing user from LDAP."""
    settings = get_ldap_provider(db).settings
    _enforce_ldap_required_groups(settings, ldap_user)

    resolved_group_id = _resolve_ldap_group_target(db, settings, _ldap_groups_to_tokens(ldap_user))
    resolved_role = _resolve_ldap_role(settings, _ldap_groups_to_tokens(ldap_user))
    existing_user = _find_existing_user_for_ldap(db, ldap_user)

    if existing_user:
        already_linked = bool(get_user_setting_value(existing_user.id, "ldap_login", "linked", db))
        if is_admin_role(existing_user.role) and not already_linked:
            raise HTTPException(status_code=403, detail="Protected local admin accounts cannot be taken over by LDAP.")
        if already_linked:
            _validate_linked_ldap_identity(existing_user.id, ldap_user, db)
        elif not coerce_bool(settings.get("ldap_link_existing_users_by_email"), True):
            raise HTTPException(status_code=403, detail="LDAP sign-in is not allowed for existing local users.")

        _sync_existing_user_from_ldap(
            db,
            existing_user,
            ldap_user,
            settings,
            resolved_group_id=resolved_group_id,
            resolved_role=resolved_role,
            request=request,
        )
        _link_user_to_ldap(db, existing_user, ldap_user)
        return existing_user, False

    if not _is_new_account_registration_enabled(db):
        raise HTTPException(status_code=403, detail="LDAP provisioning is disabled because signup is disabled.")
    if not coerce_bool(settings.get("ldap_enable_jit_provisioning"), True):
        raise HTTPException(status_code=403, detail="LDAP Just-In-Time provisioning is disabled.")

    try:
        validated_terms = _require_terms_ready_for_self_service_signup(db, terms_acceptance or {})
    except TermsOfServiceSignupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc

    new_user = create_user(
        db=db,
        email=ldap_user.email,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        first_name=ldap_user.first_name or "User",
        last_name=ldap_user.last_name,
        role=resolved_role,
        group_id=resolved_group_id,
    )
    if validated_terms:
        _record_terms_of_service_acceptance(
            db=db,
            db_log=db_log,
            request=request,
            user_id=new_user.id,
            revision=int(validated_terms["revision"]),
            accepted_at=str(validated_terms["accepted_at"]),
            source="ldap_jit_signup",
        )
    _link_user_to_ldap(db, new_user, ldap_user)

    user_agent = request.headers.get("User-Agent", "Unknown Device")
    client_ip = _client_ip_from_request(request, db)
    create_authentication_log(
        db_log,
        "ldap_signup",
        "info",
        f"New user created via LDAP: {ldap_user.email}",
        new_user.id,
        user_agent,
        client_ip,
    )
    if new_user.role == "pending":
        try:
            create_admin_notification(
                db,
                "user_pending",
                f"New pending user signup via LDAP: {ldap_user.email}",
                details={"user_id": new_user.id, "email": ldap_user.email, "provider": "ldap"},
                user_id=new_user.id,
                notification_type="info",
            )
        except Exception:
            logger.exception("Failed to create pending-user notification for LDAP signup")
    return new_user, True


def _resolve_sso_role(sso_provider, user_info: dict[str, Any]) -> str:
    """Resolve SSO role from user info."""
    desired = str(user_info.get("omlorix_role") or "").strip().lower()
    if desired in {"user", "pending"}:
        return desired
    return normalize_external_role(sso_provider.get_default_role())


def _resolve_sso_group_id(db: Session, sso_provider, user_info: dict[str, Any]) -> str:
    """Resolve SSO group ID from user info."""
    desired = str(user_info.get("omlorix_group_id") or "").strip()
    if desired:
        group = get_group(db, desired)
        if group:
            return group.id
        by_name = get_group_by_name(db, desired)
        if by_name:
            return by_name.id
    return _resolve_group_id_from_setting(db, sso_provider.get_default_group(), "default")


def _sync_existing_user_from_sso(db: Session, user, user_info: dict[str, Any], sso_provider, request=None) -> None:
    """Sync existing user from SSO data."""
    changed = False
    old_role = getattr(user, "role", None)
    role_changed = False

    if sso_provider.sync_profile_on_login():
        first_name = str(user_info.get("given_name") or "").strip()
        last_name = str(user_info.get("family_name") or "").strip()
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            changed = True
        if user.last_name != last_name:
            user.last_name = last_name
            changed = True

    if sso_provider.sync_email_on_login():
        email = str(user_info.get("email") or "").strip().lower()
        if email and user.email != email:
            email_owner = None
            try:
                email_owner = get_user(db, email=email)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            if not email_owner or email_owner.id == user.id:
                user.email = email
                changed = True

    resolved_group_id = _resolve_sso_group_id(db, sso_provider, user_info)
    if sso_provider.sync_app_group_on_login() and user.group_id != resolved_group_id:
        user.group_id = resolved_group_id
        changed = True

    resolved_role = _resolve_sso_role(sso_provider, user_info)
    # Treat provider claims and configuration as untrusted at the final write
    # boundary as well as in the provider-specific resolver.
    resolved_role = normalize_external_role(resolved_role)
    if (
        sso_provider.sync_role_on_login()
        and not is_admin_role(user.role)
        and user.role != resolved_role
    ):
        user.role = resolved_role
        changed = True
        role_changed = True

    if changed:
        db.commit()
        db.refresh(user)
        if role_changed:
            _audit_external_role_sync(
                user_id=user.id,
                old_role=old_role,
                new_role=getattr(user, "role", None),
                source="sso",
                request=request,
                db=db,
                source_context={
                    "provider_type": getattr(sso_provider, "provider_type", None),
                    "provider_name": getattr(sso_provider, "provider_name", None),
                    "provider_subject_present": bool(user_info.get("sub")),
                },
            )


def validate_user_login_eligibility(user, db):
    """Validate user login eligibility."""
    signin_enabled = get_value_by_page_and_key("login_general", "enable_signin", db)
    if not signin_enabled and not is_admin_role(user.role):
        return {"status": "signin_disabled_for_users"}

    if user.deleted_at is not None:
        return {"status": "deleted"}

    if user.role == "pending":
        return {"status": "pending"}

    if not user.is_active:
        return {"status": "inactive"}

    temporary_expires_at = normalize_utc_datetime(getattr(user, "temporary_expires_at", None))
    if getattr(user, "account_type", "regular") == "temporary":
        if temporary_expires_at is None or temporary_expires_at <= datetime.now(timezone.utc):
            return {"status": "temporary_expired"}

    try:
        locked = check_user_locked(db, user.id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"status": "deleted"}
        raise
    if isinstance(locked, dict) and locked.get("is_locked"):
        lock_until = normalize_utc_datetime(locked.get("lock_until"))
        expires = 0
        if lock_until is not None:
            expires = max(0, int((lock_until - datetime.now(timezone.utc)).total_seconds()))
        return {
            "status": "lock",
            "expires": expires,
            "type": locked.get("type") or "",
            "reason": locked.get("reason") or "",
        }

    is_admin = is_admin_role(user.role)
    access_check = is_group_accessible_now(user.group_id, db, is_admin=is_admin)
    if not access_check.get("accessible", True):
        return {
            "status": "access_time_blocked",
            "reason": access_check.get("reason"),
            "next_allowed_at": access_check.get("next_allowed_at"),
            "blocked_message": access_check.get("blocked_message"),
        }

    return None


def _sso_login_eligibility_redirect_response(
    *,
    user,
    email: str,
    db,
    db_log,
    user_agent: str,
    client_ip: str,
):
    """Return an SSO login redirect response when the user is not eligible."""
    from app.auth.diagnostics import build_sso_failure_url
    from fastapi.responses import RedirectResponse

    eligibility = validate_user_login_eligibility(user, db)
    if not eligibility:
        return None

    status = str(eligibility.get("status") or "").strip().lower()
    if status == "signin_disabled_for_users":
        create_authentication_log(
            db_log,
            "sso_login",
            "warning",
            f"SSO signin blocked: signin disabled for non-admin user {email}",
            user.id,
            user_agent,
            client_ip,
        )
        return RedirectResponse(
            url=build_sso_failure_url("sso_login_failed", None), status_code=302
        )

    if status == "deleted":
        return RedirectResponse(
            url=build_sso_failure_url("account_deleted", None), status_code=302
        )

    if status == "pending":
        return RedirectResponse(
            url=build_sso_failure_url("account_pending", None), status_code=302
        )

    if status in {"inactive", "temporary_expired"}:
        return RedirectResponse(
            url=build_sso_failure_url("account_inactive", None), status_code=302
        )

    if status == "lock":
        create_authentication_log(
            db_log,
            "sso_login",
            "warning",
            f"SSO signin blocked: user {email} is locked",
            user.id,
            user_agent,
            client_ip,
        )
        return RedirectResponse(
            url=build_sso_failure_url("account_locked", None), status_code=302
        )

    if status == "access_time_blocked":
        create_authentication_log(
            db_log,
            "sso_login",
            "warning",
            f"SSO signin blocked by time restriction: {eligibility.get('reason')}",
            user.id,
            user_agent,
            client_ip,
        )
        return RedirectResponse(
            url=build_sso_failure_url("sso_login_failed", None), status_code=302
        )

    create_authentication_log(
        db_log,
        "sso_login",
        "warning",
        f"SSO signin blocked by login eligibility: {status or 'unknown'}",
        user.id,
        user_agent,
        client_ip,
    )
    return RedirectResponse(
        url=build_sso_failure_url("sso_login_failed", None), status_code=302
    )


def _complete_signin_for_user(
    db,
    db_log,
    request,
    response,
    user,
    *,
    otp_code: str | None,
    otp_action: str | None,
    otp_destination: str | None,
    log_event: str,
    success_message: str,
    account_mode: str = "primary",
    replace_slot: int | None = None,
    password_proof_binding: tuple[str, str] | None = None,
):
    """Complete sign-in process for user."""
    client_ip = _client_ip_from_request(request, db)
    user_agent = request.headers.get("User-Agent", "Unknown Device")

    eligibility = validate_user_login_eligibility(user, db)
    if eligibility:
        if eligibility.get("status") == "signin_disabled_for_users":
            create_authentication_log(db_log, log_event, "error", "Signin is disabled for non-admin users", user.id, user_agent, client_ip)
        elif eligibility.get("status") == "deleted":
            create_authentication_log(db_log, log_event, "warning", "Signin attempt for deleted user", user.id, user_agent, client_ip)
        elif eligibility.get("status") == "lock":
            create_authentication_log(db_log, log_event, "warning", "Signin attempt for locked user", user.id, user_agent, client_ip)
        elif eligibility.get("status") == "access_time_blocked":
            create_authentication_log(
                db_log,
                log_event,
                "warning",
                f"Access blocked by time restriction: {eligibility.get('reason')}",
                user.id,
                user_agent,
                client_ip,
            )
        return eligibility

    twofa_result = evaluate_login_2fa(
        user,
        otp_code=otp_code,
        otp_action=otp_action,
        otp_destination=otp_destination,
        db=db,
        client_ip=client_ip,
    )
    if twofa_result:
        if response is not None and twofa_result.get("status") == "otp_setup":
            signin_token, signin_token_expires = _set_pending_signin_token(user.id, db, allow_setup_material=True)
            _set_one_time_browser_cookie(
                response,
                "signin_login_token",
                signin_token,
                db,
                request,
                max_age=max(1, int((signin_token_expires - datetime.now(timezone.utc)).total_seconds())),
            )
        if twofa_result.get("status") == "otp_invalid":
            create_authentication_log(
                db_log,
                log_event,
                "warning",
                "OTP code invalid",
                user.id,
                user_agent,
                client_ip,
            )
        elif twofa_result.get("status") == "otp_locked":
            create_authentication_log(
                db_log,
                log_event,
                "warning",
                "OTP verification locked after repeated failures",
                user.id,
                user_agent,
                client_ip,
            )
        return twofa_result

    _clear_pending_signin_token(user.id, db)
    if response is not None:
        _clear_one_time_browser_cookie(response, "signin_login_token")

    issued = _issue_authenticated_session(
        db=db,
        db_log=db_log,
        request=request,
        response=response,
        user=user,
        log_event=log_event,
        success_message=success_message,
        account_mode=account_mode,
        replace_slot=replace_slot,
        twofa_satisfied=bool(otp_code),
        password_proof_binding=password_proof_binding,
    )
    if not issued.get("session_authenticated"):
        return issued

    reset_user_wrong_sign_in_attempts(db, user.id)

    needs_server_setup = False
    if is_admin_role(user.role):
        server_setup_complete = get_value_by_page_and_key("states", "server_setup", db)
        needs_server_setup = not server_setup_complete

    return {
        "status": "success",
        **issued,
        "needs_server_setup": needs_server_setup,
    }


# -------------------
# Notify Suspicious Auth Activity
# -------------------
def _notify_suspicious_auth_activity(
    event: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    notification_type: str = "warning",
) -> None:
    """Notify admin of suspicious authentication activity."""
    payload: dict[str, Any] = {"event": event}
    if details:
        payload.update(details)
    try:
        session = SessionLocal()
        try:
            create_admin_notification(
                session,
                "suspicious_auth",
                message,
                details=payload,
                notification_type=notification_type,
            )
        finally:
            session.close()
    except Exception:
        logger.exception("Failed to record admin notification for event %s", event)



_BCRYPT_PASSWORD_LIMIT_BYTES = 72


def _legacy_normalize_bcrypt_secret(password: str) -> bytes:
    """Legacy bcrypt password normalization."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    return password.encode("utf-8")[:_BCRYPT_PASSWORD_LIMIT_BYTES]


def _normalize_bcrypt_secret(password: str) -> bytes:
    """Normalize bcrypt secret using SHA256."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    normalized = base64.b64encode(digest)
    return normalized[:_BCRYPT_PASSWORD_LIMIT_BYTES]



# -------------------
# Trusted-proxy client IP helper
# -------------------
def _client_ip_from_request(request: Request, db: Session | None = None) -> str:
    """Extract client IP from request with trusted proxy support."""
    managed_db: Session | None = None
    try:
        if db is None:
            db_factory = getattr(getattr(request, "app", None), "state", None)
            db_factory = getattr(db_factory, "db", None)
            if callable(db_factory):
                managed_db = db_factory()
                db = managed_db

        if db is not None:
            resolved = _get_hardened_client_ip(request, db)
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip()
    except Exception:
        logger.exception("Failed to resolve trusted-proxy-aware client IP; falling back to env-based parsing")
    finally:
        if managed_db is not None:
            managed_db.close()

    trusted_networks = resolve_trusted_proxy_networks(
        "AUTH_TRUSTED_PROXIES",
        "RATE_LIMIT_TRUSTED_PROXIES",
        "TRUSTED_PROXIES",
    )
    return extract_client_ip_from_request(request, trusted_proxy_networks=trusted_networks, default="Unknown") or "Unknown"


# -------------------
# Parse date
# -------------------
def _parse_date(date_str: str | None):
    """Parse a date string into a datetime or return None.
    Accepts ISO8601 or YYYY-MM-DD. Stored as naive UTC datetime at 00:00.
    """
    if not date_str:
        return None
    s = date_str.strip()
    if not s:
        return None
    fmts = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ")
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            # If time missing, assume midnight
            return dt
        except ValueError:
            continue
    # Fallback: try fromisoformat (may handle more cases)
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None



# -------------------
# Hash password
# -------------------
def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    return bcrypt.hashpw(_normalize_bcrypt_secret(password), bcrypt.gensalt()).decode("utf-8")



# -------------------
# Verify password
# -------------------
def verify_password_with_migration(plain_password: str, hashed_password: str) -> tuple[bool, bool]:
    """Verify password with legacy hash migration support."""
    if not isinstance(hashed_password, str) or not hashed_password:
        return False, False
    try:
        encoded_hash = hashed_password.encode("utf-8")
        if bcrypt.checkpw(_normalize_bcrypt_secret(plain_password), encoded_hash):
            return True, False
        if bcrypt.checkpw(_legacy_normalize_bcrypt_secret(plain_password), encoded_hash):
            return True, True
    except ValueError:
        return False, False
    return False, False


def verify_password(plain_password: str, hashed_password: str):
    """Verify password against hash."""
    is_valid, _needs_rehash = verify_password_with_migration(plain_password, hashed_password)
    return is_valid


def _migrate_verified_legacy_password_hash(
    db: Session,
    *,
    user_id: str,
    verified_email: str,
    verified_password_hash: str,
    plain_password: str,
) -> str | None:
    """Replace a verified legacy hash only while its identity proof is current.

    Hashing happens before the compare-and-swap so the database row is locked
    for as little time as possible. The update predicates preserve the exact
    email and password authority observed before password verification; a
    concurrent reset or email change therefore wins instead of being silently
    overwritten by this opportunistic migration.
    """

    email_match = build_user_email_match(verified_email)
    if not user_id or email_match is None or not verified_password_hash:
        return None

    try:
        migrated_password_hash = hash_password(plain_password)
        changed = (
            db.query(User)
            .filter(
                User.id == user_id,
                email_match,
                User.hashed_password == verified_password_hash,
            )
            .update(
                {User.hashed_password: migrated_password_hash},
                synchronize_session=False,
            )
        )
        if int(changed or 0) != 1:
            db.rollback()
            return None
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to safely migrate legacy bcrypt hash for user %s",
            user_id,
        )
        return None
    return migrated_password_hash


def _find_user_for_signin_identifier(db: Session, identifier: str):
    """Resolve a sign-in identifier to a local user."""
    if not isinstance(identifier, str):
        return None

    normalized_identifier = identifier.strip()
    if not normalized_identifier:
        return None

    if "@" in normalized_identifier:
        normalized_identifier = normalized_identifier.lower()
        try:
            return get_user(db, email=normalized_identifier)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return None

    user = _find_user_by_settings_value(
        db,
        ("ldap_login", "last_login_identifier"),
        [normalized_identifier],
    )
    if not user:
        return None
    if not bool(get_user_setting_value(user.id, "ldap_login", "linked", db)):
        return None
    return user


def _find_user_for_password_reset_email(db: Session, identifier: str):
    """Resolve a password reset identifier using the email-only reset policy."""
    if not isinstance(identifier, str):
        return None

    normalized_identifier = identifier.strip().lower()
    if not normalized_identifier or "@" not in normalized_identifier:
        return None

    try:
        return get_user(db, email=normalized_identifier)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None


def _lock_password_reset_user_for_identifier(
    db: Session,
    user_id: str,
    normalized_identifier: str,
):
    """Rebind a reset request to the current mailbox under the user lock."""

    user = (
        db.query(User)
        .populate_existing()
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        return None
    if canonicalize_user_email(user.email) != normalized_identifier:
        return None
    return user



# -------------------
# Check failed signin attempts
# -------------------
def check_failed_signin_attempts(identifier, db):
    """Check and handle failed sign-in attempts."""
    if isinstance(identifier, str):
        identifier = identifier.strip()
        if "@" in identifier:
            identifier = identifier.lower()
    if not identifier:
        return False
    user = _find_user_for_signin_identifier(db, identifier)
    if not user:
        return False
    if is_admin_role(user.role):  # Administrators cannot be locked automatically.
        return False
    locked = check_user_locked(db, user.id)
    if isinstance(locked, dict) and locked.get("is_locked"):
        return locked

    raw_hours = _coerce_positive_float(
        get_value_by_page_and_key("security", "block_user_after_wrong_signin_attempts_time_hours", db),
        24,
        setting_name="block_user_after_wrong_signin_attempts_time_hours",
    )
    raw_threshold = _coerce_positive_int(
        get_value_by_page_and_key("security", "block_user_after_wrong_signin_attempts", db),
        5,
        setting_name="block_user_after_wrong_signin_attempts",
    )

    wrong_sign_in_attempts = get_user_wrong_sign_in_attempts(db, user.id)
    if wrong_sign_in_attempts >= raw_threshold:
        lock_until = datetime.now(timezone.utc) + timedelta(hours=raw_hours)
        lock_user(db, user.id, lock_until, "wrong_sign_in_attempts", "Too many failed sign-in attempts")
        _notify_suspicious_auth_activity(
            "user_locked_after_failed_signins",
            f"User {user.id} locked after too many failed sign-in attempts",
            details={
                "user_id": user.id,
                "identifier": identifier,
                "attempts": wrong_sign_in_attempts,
                "threshold": raw_threshold,
                "lock_until": lock_until.isoformat(),
            },
            notification_type="error",
        )
        return {
            "is_locked": True,
            "lock_until": lock_until,
            "type": "wrong_sign_in_attempts",
            "reason": "Too many failed sign-in attempts",
        }
    return False



# -------------------
# Normalize Email Domain
# -------------------
def _normalize_email_domain(value: str) -> str:
    """Normalize email domain by removing leading @."""
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    return value[1:] if value.startswith("@") else value


def _is_password_reset_enabled(db) -> bool:
    """Check if password reset is enabled."""
    return coerce_bool(get_value_by_page_and_key("login_general", "enable_password_reset", db))


def get_signin_options(db: Session, identifier: str) -> dict[str, Any]:
    """Get non-enumerating sign-in capabilities for a submitted identifier."""
    normalized_identifier = (identifier or "").strip()
    if not normalized_identifier:
        raise HTTPException(status_code=400, detail="Identifier is required")

    passkey_available = False
    try:
        from app.auth.passkeys import get_passkey_policy

        policy = get_passkey_policy(db)
        passkey_available = bool(policy.get("enable_passkeys"))
    except Exception:
        logger.exception("Failed to determine passkey availability for sign-in discovery")

    password_reset_enabled = _is_password_reset_enabled(db)
    password_reset_ready = False
    try:
        password_reset_ready = is_password_reset_ready(db)
    except Exception:
        logger.exception("Failed to evaluate password reset readiness for sign-in discovery")

    password_reset_available = password_reset_enabled and password_reset_ready

    server_methods = {
        "password": True,
        "passkey": passkey_available,
    }

    return {
        "identifier": normalized_identifier,
        "server_methods": server_methods,
        "identifier_methods": {
            "password": None,
            "passkey": None,
        },
        "password_reset_enabled": password_reset_enabled,
        "password_reset_ready": password_reset_ready,
        "password_reset_available": password_reset_available,
    }


def _password_reset_generic_success() -> dict[str, str]:
    """Return generic password reset success response."""
    return {
        "status": "ok",
        "message": "If an account exists, a reset link has been sent.",
    }


def _password_reset_response_delay_seconds() -> float:
    """Return the minimum visible password-reset response duration plus jitter."""
    jitter_ms = max(int(_PASSWORD_RESET_RESPONSE_JITTER_MILLISECONDS), 0)
    jitter_seconds = secrets.randbelow(jitter_ms + 1) / 1000 if jitter_ms else 0.0
    return max(float(_PASSWORD_RESET_RESPONSE_MIN_SECONDS), 0.0) + jitter_seconds


def _equalize_password_reset_response_timing(started_at: float) -> None:
    """Keep request timing comparable across password reset request branches."""
    try:
        remaining = _password_reset_response_delay_seconds() - (time.monotonic() - started_at)
        if remaining > 0:
            time.sleep(remaining)
    except Exception:
        logger.debug("Failed to equalize password reset response timing", exc_info=True)


def _normalize_reset_identifier(identifier: str | None) -> str | None:
    """Normalize password reset identifier."""
    if not isinstance(identifier, str):
        return None
    normalized = identifier.strip()
    if not normalized:
        return None
    if "@" in normalized:
        return normalized.lower()
    return normalized


def _get_password_reset_identifier_hash_salt(db=None) -> str:
    """Return the configured salt used for password-reset identifier fingerprints."""
    global _GENERATED_PASSWORD_RESET_IDENTIFIER_HASH_SALT

    configured_salt = str(_PASSWORD_RESET_IDENTIFIER_HASH_SALT or "").strip()
    if configured_salt:
        return configured_salt

    # Password reset identifiers and user-agent fingerprints are security
    # metadata, but they must not reuse the runtime JWT secret as a fallback.
    # The deployment-wide log pseudonymization salt is domain-separated so
    # database rate-limit buckets remain consistent across API replicas when
    # the dedicated reset salt is absent. Supported deployments generate this
    # secret; the process-local fallback only serves minimal development use.
    shared_pseudonymization_salt = str(os.getenv("LOG_IP_HASH_SALT") or "").strip()
    if shared_pseudonymization_salt:
        return hashlib.sha256(
            f"password-reset-identifiers:{shared_pseudonymization_salt}".encode(
                "utf-8"
            )
        ).hexdigest()
    if _GENERATED_PASSWORD_RESET_IDENTIFIER_HASH_SALT is None:
        _GENERATED_PASSWORD_RESET_IDENTIFIER_HASH_SALT = secrets.token_urlsafe(32)
    return _GENERATED_PASSWORD_RESET_IDENTIFIER_HASH_SALT


def _hash_password_reset_identifier(identifier: str | None, db=None) -> str | None:
    """Return a pseudonymous fingerprint for password reset identifiers."""
    normalized = _normalize_reset_identifier(identifier)
    if not normalized:
        return None
    digest = hashlib.sha256(
        f"password-reset-id:{_get_password_reset_identifier_hash_salt(db)}:{normalized}".encode("utf-8")
    ).hexdigest()
    return f"resetid_{digest}"


def _mark_password_reset_attempt_in_database(
    key: str,
    *,
    max_attempts: int,
    window_seconds: int,
    cooldown_seconds: int = 0,
) -> bool:
    """Use the primary database as the shared limiter when Redis is absent."""

    limiter_db = SessionLocal()
    try:
        allowed = consume_email_security_rate_limit(
            limiter_db,
            bucket=key,
            max_attempts=max_attempts,
            window_seconds=window_seconds,
            cooldown_seconds=cooldown_seconds,
        )
        limiter_db.commit()
        return bool(allowed)
    except Exception:
        limiter_db.rollback()
        logger.exception("Database-backed password reset throttling failed")
        return False
    finally:
        limiter_db.close()


def _mark_password_reset_attempt(key: str) -> bool:
    """Mark password reset attempt for rate limiting."""
    client = get_redis_client()
    if client is None:
        return _mark_password_reset_attempt_in_database(
            key,
            max_attempts=_PASSWORD_RESET_MAX_PER_WINDOW,
            window_seconds=_PASSWORD_RESET_WINDOW_SECONDS,
            cooldown_seconds=_PASSWORD_RESET_COOLDOWN_SECONDS,
        )

    now_ts = int(datetime.now(timezone.utc).timestamp())
    window_start = now_ts - (now_ts % _PASSWORD_RESET_WINDOW_SECONDS)
    window_key = f"omlorix:password_reset:window:{key}:{window_start}"
    cooldown_key = f"omlorix:password_reset:cooldown:{key}"
    try:
        result = client.eval(
            _PASSWORD_RESET_ATTEMPT_LUA,
            2,
            window_key,
            cooldown_key,
            _PASSWORD_RESET_COOLDOWN_SECONDS,
            _PASSWORD_RESET_WINDOW_SECONDS + 1,
            _PASSWORD_RESET_MAX_PER_WINDOW,
        )
        return bool(int(result))
    except Exception:
        logger.warning(
            "Redis password reset throttling failed; using the database fallback",
            exc_info=True,
        )
        return _mark_password_reset_attempt_in_database(
            key,
            max_attempts=_PASSWORD_RESET_MAX_PER_WINDOW,
            window_seconds=_PASSWORD_RESET_WINDOW_SECONDS,
            cooldown_seconds=_PASSWORD_RESET_COOLDOWN_SECONDS,
        )


def _is_password_reset_throttled(client_ip: str, identifier_hash: str | None) -> bool:
    """Check if password reset is throttled."""
    ip_key = f"reset:ip:{client_ip or 'unknown'}"
    if not _mark_password_reset_attempt(ip_key):
        return True
    if identifier_hash:
        id_key = f"reset:id:{identifier_hash}"
        if not _mark_password_reset_attempt(id_key):
            return True
    return False


def _password_reset_user_agent_from_request(request) -> str | None:
    headers = getattr(request, "headers", None)
    getter = getattr(headers, "get", None)
    if callable(getter):
        return getter("User-Agent") or getter("user-agent")
    return None


def _hash_password_reset_user_agent(user_agent: str | None, db=None) -> str:
    normalized = " ".join(str(user_agent or "").split())
    if not normalized:
        return "ua_unknown"
    digest = hashlib.sha256(
        f"password-reset-ua:{_get_password_reset_identifier_hash_salt(db)}:{normalized}".encode("utf-8")
    ).hexdigest()
    return f"ua_{digest[:16]}"


def _mark_password_reset_token_attempt(key: str) -> bool:
    """Mark a validation/confirmation token attempt for rate limiting."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    window_start = now_ts - (now_ts % _PASSWORD_RESET_TOKEN_ATTEMPT_WINDOW_SECONDS)
    window_key = f"omlorix:password_reset:token_window:{key}:{window_start}"
    client = get_redis_client()
    if client is None:
        return _mark_password_reset_attempt_in_database(
            window_key,
            max_attempts=_PASSWORD_RESET_TOKEN_ATTEMPT_MAX_PER_WINDOW,
            window_seconds=_PASSWORD_RESET_TOKEN_ATTEMPT_WINDOW_SECONDS,
        )

    try:
        current_count = int(client.incr(window_key))
        if current_count == 1:
            client.expire(window_key, _PASSWORD_RESET_TOKEN_ATTEMPT_WINDOW_SECONDS + 1)
        return current_count <= _PASSWORD_RESET_TOKEN_ATTEMPT_MAX_PER_WINDOW
    except Exception:
        logger.warning(
            "Redis password reset token throttling failed; using the database fallback",
            exc_info=True,
        )
        return _mark_password_reset_attempt_in_database(
            window_key,
            max_attempts=_PASSWORD_RESET_TOKEN_ATTEMPT_MAX_PER_WINDOW,
            window_seconds=_PASSWORD_RESET_TOKEN_ATTEMPT_WINDOW_SECONDS,
        )


def _is_password_reset_token_attempt_throttled(
    purpose: str,
    client_ip: str | None,
    user_agent: str | None,
    db=None,
) -> bool:
    """Check whether reset token validation/confirmation attempts are throttled."""
    normalized_purpose = "confirm" if purpose == "confirm" else "validate"
    normalized_ip = str(client_ip or "unknown").strip() or "unknown"
    user_agent_hash = _hash_password_reset_user_agent(user_agent, db)
    keys = (
        f"reset:token:{normalized_purpose}:ip:{normalized_ip}",
        f"reset:token:{normalized_purpose}:ipua:{normalized_ip}:{user_agent_hash}",
    )
    return any(not _mark_password_reset_token_attempt(key) for key in keys)


def _hash_password_reset_token(token: str) -> str:
    """Hash password reset token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _build_password_reset_link(public_url: str, token: str) -> str:
    return f"{public_url.rstrip('/')}/login#token={quote(token, safe='')}"


def _password_reset_token_is_consumed_or_expired(db_token, now: datetime | None = None) -> bool:
    consumed_at = normalize_utc_datetime(getattr(db_token, "consumed_at", None))
    expires_at = normalize_utc_datetime(getattr(db_token, "expires_at", None))
    if consumed_at is not None or expires_at is None:
        return True
    return expires_at < (now or datetime.now(timezone.utc))


def _password_reset_ineligible_status(user, db) -> str | None:
    """Return the runtime auth eligibility failure that should block password reset."""
    if is_externally_managed(user):
        return "externally_managed"

    eligibility = validate_user_login_eligibility(user, db)
    if eligibility:
        return str(eligibility.get("status") or "ineligible")

    try:
        locked = check_user_locked(db, user.id)
    except HTTPException as exc:
        if exc.status_code == 404:
            # Fail closed if the account disappears between the initial lookup
            # and the lock check, instead of surfacing a raw backend error.
            return "deleted"
        raise
    if isinstance(locked, dict) and locked.get("is_locked"):
        return "locked"

    return None


def _password_reset_locked_user_ineligible_status(user) -> str | None:
    """Recheck only mutable row-local eligibility without releasing its lock."""

    if is_externally_managed(user):
        return "externally_managed"
    if getattr(user, "deleted_at", None) is not None:
        return "deleted"
    if getattr(user, "role", None) == "pending":
        return "pending"
    if not bool(getattr(user, "is_active", False)):
        return "inactive"
    if getattr(user, "account_type", "regular") == "temporary":
        expires_at = normalize_utc_datetime(
            getattr(user, "temporary_expires_at", None)
        )
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            return "temporary_expired"
    # Passing no session deliberately stages expiration normalization on the
    # already locked User object without committing or releasing the row lock.
    locked = evaluate_user_lock(user)
    if isinstance(locked, dict) and locked.get("is_locked"):
        return "lock"
    return None


def _process_password_reset_request(
    db,
    db_log,
    normalized_identifier: str,
    client_ip: str,
    user_agent: str,
    accept_language: str | None,
) -> None:
    """Atomically persist a reset credential and its durable delivery job."""
    user = _find_user_for_password_reset_email(db, normalized_identifier)

    if not user or getattr(user, "deleted_at", None) is not None:
        create_authentication_log(
            db_log, "password_reset_request", "info", "Password reset requested for unknown account", None, user_agent, client_ip
        )
        return

    # Policy/configuration reads are done before taking the user lock. Mutable
    # user state is checked again after the current row has been rebound.
    ineligible_status = _password_reset_ineligible_status(user, db)
    if ineligible_status:
        create_authentication_log(
            db_log,
            "password_reset_request",
            "warning",
            f"Password reset requested for ineligible account: {ineligible_status}",
            user.id,
            user_agent,
            client_ip,
        )
        return

    public_url = get_public_url(db).rstrip("/")
    user = _lock_password_reset_user_for_identifier(
        db,
        user.id,
        normalized_identifier,
    )
    if user is None or getattr(user, "deleted_at", None) is not None:
        db.rollback()
        create_authentication_log(
            db_log,
            "password_reset_request",
            "info",
            "Password reset requested for unknown account",
            None,
            user_agent,
            client_ip,
        )
        return

    ineligible_status = _password_reset_locked_user_ineligible_status(user)
    if ineligible_status:
        db.rollback()
        create_authentication_log(
            db_log,
            "password_reset_request",
            "warning",
            f"Password reset requested for ineligible account: {ineligible_status}",
            user.id,
            user_agent,
            client_ip,
        )
        return

    raw_token = secrets.token_urlsafe(48)
    token_hash = _hash_password_reset_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PASSWORD_RESET_TOKEN_TTL_SECONDS)
    invalidate_user_password_reset_tokens(db, user.id, commit=False)
    db_token = create_password_reset_token(
        db,
        user_id=user.id,
        token_hash=token_hash,
        requested_ip=_minimize_password_reset_ip(client_ip),
        requested_user_agent=_minimize_password_reset_user_agent(user_agent),
        expires_at=expires_at,
        commit=False,
    )
    reset_link = _build_password_reset_link(public_url, raw_token)
    language_code = resolve_email_language(
        get_user_setting_value(
            user.id,
            "general",
            "language",
            db,
            commit=False,
        ),
        accept_language,
    )
    from app.email.models import enqueue_email

    enqueue_email(
        db,
        user_id=user.id,
        recipient=user.email,
        template_type="password_reset",
        language_code=language_code,
        priority=0,
        expires_at=expires_at,
        idempotency_key=f"password-reset:{db_token.id}",
        payload={
            "token_id": db_token.id,
            "reset_link": reset_link,
            "expires_in_minutes": _PASSWORD_RESET_TOKEN_TTL_SECONDS // 60,
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    create_authentication_log(
        db_log,
        "password_reset_request",
        "info",
        "Password reset request accepted (email queued)",
        user.id,
        user_agent,
        client_ip,
    )


def request_password_reset(
    db,
    db_log,
    request,
    email: str,
):
    """Request password reset for user."""
    if not _is_password_reset_enabled(db):
        raise HTTPException(status_code=409, detail="Password reset is not enabled.")
    if not is_password_reset_ready(db):
        raise HTTPException(status_code=409, detail="Password reset is not enabled.")

    started_at = time.monotonic()
    try:
        client_ip = _client_ip_from_request(request, db)
        user_agent = request.headers.get("User-Agent", "Unknown Device")
        accept_language = request.headers.get("Accept-Language")
        normalized_identifier = _normalize_reset_identifier(email)
        identifier_hash = _hash_password_reset_identifier(normalized_identifier, db)

        if _is_password_reset_throttled(client_ip, identifier_hash):
            create_authentication_log(
                db_log, "password_reset_request", "warning", "Password reset request throttled", None, user_agent, client_ip
            )
            _notify_suspicious_auth_activity(
                "password_reset_request_throttled",
                f"Password reset request throttled for IP {client_ip}",
                details={"identifier_hash": identifier_hash or "", "ip_address": client_ip},
            )
            return _password_reset_generic_success()

        if not normalized_identifier:
            create_authentication_log(
                db_log, "password_reset_request", "warning", "Password reset request missing identifier", None, user_agent, client_ip
            )
            return _password_reset_generic_success()

        try:
            _process_password_reset_request(
                db,
                db_log,
                normalized_identifier,
                client_ip,
                user_agent,
                accept_language,
            )
        except Exception:
            # Operational failures must not turn the generic response into an
            # account-existence oracle. The SQL transaction is rolled back, so
            # a reset token can never exist without its matching outbox job.
            try:
                db.rollback()
            except Exception:
                logger.debug(
                    "Unable to roll back failed password reset staging",
                    exc_info=True,
                )
            logger.exception("Failed to stage password reset request")
        return _password_reset_generic_success()
    finally:
        _equalize_password_reset_response_timing(started_at)


def _audit_password_reset_event(
    *,
    db,
    db_log=None,
    request=None,
    user_id: str | None,
    action: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    error_message: str = "Failed to audit password reset event",
) -> None:
    audit_db = db_log
    close_audit_db = False
    try:
        ip_address = _client_ip_from_request(request, db) if request is not None else None
        user_agent = _password_reset_user_agent_from_request(request) if request is not None else None
        if audit_db is None:
            audit_db = AuditSessionLocal()
            close_audit_db = True
        create_audit_log(
            db_log=audit_db,
            user_id=user_id,
            action=action,
            reason=reason,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
            category="auth",
        )
    except Exception:
        logger.exception(error_message)
    finally:
        if close_audit_db and audit_db is not None:
            try:
                audit_db.close()
            except Exception:
                logger.debug("Failed to close password reset audit session", exc_info=True)


def _audit_password_reset_validate(
    *,
    db,
    db_log=None,
    request=None,
    user_id: str | None,
    action: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if db_log is None and request is None:
        return
    _audit_password_reset_event(
        db=db,
        db_log=db_log,
        request=request,
        user_id=user_id,
        action=action,
        reason=reason,
        details=details,
        error_message="Failed to audit password reset validation",
    )


def _enforce_password_reset_token_attempt_rate_limit(
    *,
    db,
    db_log=None,
    request=None,
    purpose: str,
) -> None:
    if request is None:
        return

    client_ip = _client_ip_from_request(request, db)
    user_agent = _password_reset_user_agent_from_request(request)
    if not _is_password_reset_token_attempt_throttled(purpose, client_ip, user_agent, db):
        return

    action = "PASSWORD_RESET_CONFIRM_THROTTLED" if purpose == "confirm" else "PASSWORD_RESET_VALIDATE_THROTTLED"
    _audit_password_reset_event(
        db=db,
        db_log=db_log,
        request=request,
        user_id=None,
        action=action,
        reason="rate_limited",
        details={"status": "failure", "status_code": 429, "endpoint": purpose},
        error_message="Failed to audit password reset token throttling",
    )
    raise HTTPException(status_code=429, detail=_PASSWORD_RESET_RATE_LIMIT_DETAIL)


def validate_password_reset_token(db, token: str | None, *, db_log=None, request=None):
    """Validate password reset token."""
    if not _is_password_reset_enabled(db):
        exc = HTTPException(status_code=409, detail="Password reset is not enabled.")
        _audit_password_reset_validate(
            db=db,
            db_log=db_log,
            request=request,
            user_id=None,
            action="PASSWORD_RESET_VALIDATE_FAILED",
            reason="password_reset_disabled",
            details={"status": "failure", "status_code": exc.status_code},
        )
        raise exc

    _enforce_password_reset_token_attempt_rate_limit(
        db=db,
        db_log=db_log,
        request=request,
        purpose="validate",
    )

    raw_token = (token or "").strip()
    if not raw_token:
        _audit_password_reset_validate(
            db=db,
            db_log=db_log,
            request=request,
            user_id=None,
            action="PASSWORD_RESET_VALIDATE_FAILED",
            reason="missing_token",
            details={"status": "failure", "validation_result": "invalid"},
        )
        return {"valid": False}

    token_hash = _hash_password_reset_token(raw_token)
    db_token = get_password_reset_token_by_hash(db, token_hash)
    if not db_token:
        _audit_password_reset_validate(
            db=db,
            db_log=db_log,
            request=request,
            user_id=None,
            action="PASSWORD_RESET_VALIDATE_FAILED",
            reason="invalid_token",
            details={"status": "failure", "validation_result": "invalid"},
        )
        return {"valid": False}

    if _password_reset_token_is_consumed_or_expired(db_token):
        _audit_password_reset_validate(
            db=db,
            db_log=db_log,
            request=request,
            user_id=getattr(db_token, "user_id", None),
            action="PASSWORD_RESET_VALIDATE_FAILED",
            reason="expired_or_consumed_token",
            details={"status": "failure", "validation_result": "expired_or_consumed"},
        )
        return {"valid": False}
    return {"valid": True}


def _classify_password_reset_confirm_failure(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, str) else ""
    if detail == "Invalid or expired password reset token.":
        return "invalid_or_expired_token"
    if detail == "Password reset is not enabled.":
        return "password_reset_disabled"
    if detail == "New password must be different from the current password.":
        return "password_reuse"
    if "Password must" in detail:
        return "password_policy_rejected"
    if exc.status_code < 500:
        return "validation_error"
    return "server_error"


def _audit_password_reset_confirm(
    *,
    db,
    db_log=None,
    request,
    user_id: str | None,
    action: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    _audit_password_reset_event(
        db=db,
        db_log=db_log,
        request=request,
        user_id=user_id,
        action=action,
        reason=reason,
        details=details,
        error_message="Failed to audit password reset confirmation",
    )


def confirm_password_reset(db, db_log, request, token: str | None, new_password: str):
    """Confirm password reset with token."""
    if not _is_password_reset_enabled(db):
        exc = HTTPException(status_code=409, detail="Password reset is not enabled.")
        _audit_password_reset_confirm(
            db=db,
            db_log=db_log,
            request=request,
            user_id=None,
            action="PASSWORD_RESET_FAILED",
            reason=_classify_password_reset_confirm_failure(exc),
            details={"status": "failure", "status_code": exc.status_code},
        )
        raise exc

    _enforce_password_reset_token_attempt_rate_limit(
        db=db,
        db_log=db_log,
        request=request,
        purpose="confirm",
    )

    raw_token = (token or "").strip()
    if not raw_token:
        exc = HTTPException(status_code=400, detail="Invalid or expired password reset token.")
        _audit_password_reset_confirm(
            db=db,
            db_log=db_log,
            request=request,
            user_id=None,
            action="PASSWORD_RESET_FAILED",
            reason=_classify_password_reset_confirm_failure(exc),
            details={"status": "failure", "status_code": exc.status_code},
        )
        raise exc

    token_hash = _hash_password_reset_token(raw_token)
    db_token = get_password_reset_token_by_hash(db, token_hash)
    if not db_token or _password_reset_token_is_consumed_or_expired(db_token):
        exc = HTTPException(status_code=400, detail="Invalid or expired password reset token.")
        _audit_password_reset_confirm(
            db=db,
            db_log=db_log,
            request=request,
            user_id=getattr(db_token, "user_id", None),
            action="PASSWORD_RESET_FAILED",
            reason=_classify_password_reset_confirm_failure(exc),
            details={"status": "failure", "status_code": exc.status_code},
        )
        raise exc

    try:
        user = get_user(db, db_token.user_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            reset_exc = HTTPException(status_code=400, detail="Invalid or expired password reset token.")
            _audit_password_reset_confirm(
                db=db,
                db_log=db_log,
                request=request,
                user_id=None,
                action="PASSWORD_RESET_FAILED",
                reason=_classify_password_reset_confirm_failure(reset_exc),
                details={"status": "failure", "status_code": reset_exc.status_code},
            )
            raise reset_exc
        raise

    ineligible_status = _password_reset_ineligible_status(user, db)
    if ineligible_status:
        client_ip = _client_ip_from_request(request, db)
        user_agent = request.headers.get("User-Agent", "Unknown Device")
        _audit_password_reset_confirm(
            db=db,
            db_log=db_log,
            request=request,
            user_id=user.id,
            action="PASSWORD_RESET_FAILED",
            reason="ineligible_account",
            details={
                "status": "failure",
                "status_code": 400,
                "account_status": ineligible_status,
            },
        )
        create_authentication_log(
            db_log,
            "password_reset_confirm",
            "warning",
            f"Password reset blocked for ineligible account: {ineligible_status}",
            user.id,
            user_agent,
            client_ip,
        )
        raise HTTPException(status_code=400, detail="Invalid or expired password reset token.")

    from app.users.utils import (
        _assert_password_policy,
        _commit_password_change_transaction,
        _ensure_new_password_differs_from_current,
    )

    try:
        _ensure_new_password_differs_from_current(user, new_password or "")
        _assert_password_policy(new_password or "", db)
    except HTTPException as exc:
        _audit_password_reset_confirm(
            db=db,
            db_log=db_log,
            request=request,
            user_id=user.id,
            action="PASSWORD_RESET_FAILED",
            reason=_classify_password_reset_confirm_failure(exc),
            details={"status": "failure", "status_code": exc.status_code},
        )
        raise

    try:
        _commit_password_change_transaction(
            db,
            user=user,
            new_password_hash=hash_password(new_password),
            has_to_change_password=False,
            social_needs_password_setup=False,
            sso_needs_password_setup=False,
            reset_token=db_token,
            security_event_type="password_reset",
            security_context={
                "device": minimize_session_device_info(
                    request.headers.get("User-Agent", "Unknown Device")
                ),
                "network": minimize_session_ip_address(
                    _client_ip_from_request(request, db)
                ),
            },
            new_password_plaintext_for_reuse_check=new_password,
        )
    except HTTPException as exc:
        _audit_password_reset_confirm(
            db=db,
            db_log=db_log,
            request=request,
            user_id=user.id,
            action="PASSWORD_RESET_FAILED",
            reason=_classify_password_reset_confirm_failure(exc),
            details={"status": "failure", "status_code": exc.status_code},
        )
        raise
    except Exception:
        _audit_password_reset_confirm(
            db=db,
            db_log=db_log,
            request=request,
            user_id=user.id,
            action="PASSWORD_RESET_FAILED",
            reason="server_error",
            details={"status": "failure", "status_code": 500},
        )
        raise

    client_ip = _client_ip_from_request(request, db)
    user_agent = request.headers.get("User-Agent", "Unknown Device")
    _audit_password_reset_confirm(
        db=db,
        db_log=db_log,
        request=request,
        user_id=user.id,
        action="PASSWORD_RESET",
        details={"status": "success", "reauth_required": True},
    )
    create_authentication_log(
        db_log, "password_reset_confirm", "info", "Password reset completed", user.id, user_agent, client_ip
    )
    return {"status": "success", "reauth_required": True}





# -------------------
# Signup
# -------------------
def signup(db, db_log, request, user, response=None):
    """Handle user signup."""
    client_ip = _client_ip_from_request(request, db)
    user_agent = request.headers.get("User-Agent", "Unknown Device")

    # Check if ip address is blocked
    check = check_blocked_ip_address(client_ip, db)
    if check:
        record_auth_ip_block_metric("signup")
        try:
            record_ip_address_security_event(
                db,
                client_ip,
                "request_denied",
                event_source="signup",
                reason_code="active_ban",
                route_category="auth",
                reason="Blocked IP attempted to sign up",
                aggregate=True,
            )
        except Exception:
            db.rollback()
        _notify_suspicious_auth_activity(
            "blocked_ip_signup_attempt",
            f"Blocked IP {client_ip} attempted to sign up",
            details={"ip_address": client_ip, "seconds_remaining": check},
        )
        return{"status": "ipban", "expires": check}

    # Check if signup is enabled
    if not _is_new_account_registration_enabled(db):
        create_authentication_log(db_log, "signup", "error", "Signup is disabled", None, user_agent, client_ip)
        # The backend registration gate is authoritative. A stale or modified
        # client can submit the form, but the request is simply rejected and
        # never changes the caller's IP-ban state.
        _notify_suspicious_auth_activity(
            "signup_disabled_submission",
            "A client submitted signup while registration was disabled",
            details={"ip_address": client_ip, "user_agent": user_agent, "feature": "signup"},
        )
        return {"status": "error"}

    try:
        terms_acceptance = _require_terms_ready_for_self_service_signup(db, user)
    except TermsOfServiceSignupError as exc:
        create_authentication_log(
            db_log,
            "signup",
            "warning",
            f"Signup blocked by terms policy: {exc.code}",
            None,
            user_agent,
            client_ip,
        )
        status_map = {
            "terms_configuration_required": "termsConfigurationRequired",
            "terms_acceptance_required": "termsAcceptanceRequired",
            "terms_revision_mismatch": "termsRevisionMismatch",
        }
        return {
            "status": status_map.get(exc.code, "error"),
            "detail": exc.detail,
            "revision": exc.revision,
        }

    email = getattr(user, "email", None)
    if isinstance(email, str):
        email = email.strip().lower()

    # Check if the domain is allowed

    domains = get_value_by_page_and_key("login_general", "specific_signup_domain", db)
    if domains and email:
        allowed_domains = {
            normalized
            for normalized in (
                _normalize_email_domain(item) for item in domains if isinstance(item, str)
            )
            if normalized
        }
        domain = _normalize_email_domain(email.split("@")[-1])
        if allowed_domains and domain not in allowed_domains:
            create_authentication_log(db_log, "signup", "error", "Input is incomplete - domain", None, user_agent, client_ip)
            return {"status": "domainNotAllowed"}

    # Check if user already exists (existence query avoids pulling the full row)
    user_exists = user_exists_by_email(db, email)
    if user_exists:
        create_authentication_log(
            db_log,
            "signup",
            "info",
            "Signup request received for an unavailable email address",
            None,
            user_agent,
            client_ip,
        )
        return {"status": "success"}

    # Enforce password policy (global admin settings)
    pwd = getattr(user, "password", "") or ""
    min_len = effective_minimum_password_length(
        get_value_by_page_and_key("login_general", "minimum_password_length", db)
    )
    min_special = _coerce_int(get_value_by_page_and_key("login_general", "minimum_special_characters", db), default=0)
    min_upper = _coerce_int(get_value_by_page_and_key("login_general", "minimum_uppercase_characters", db), default=0)
    min_lower = _coerce_int(get_value_by_page_and_key("login_general", "minimum_lowercase_characters", db), default=0)
    min_num = _coerce_int(get_value_by_page_and_key("login_general", "minimum_number_characters", db), default=0)

    specials = sum(1 for c in pwd if c in string.punctuation)
    uppers = sum(1 for c in pwd if c.isupper())
    lowers = sum(1 for c in pwd if c.islower())
    digits = sum(1 for c in pwd if c.isdigit())

    violations: list[str] = []
    if len(pwd) < min_len:
        violations.append(f"Password must be at least {min_len} characters long")
    if specials < min_special:
        violations.append(f"Password must contain at least {min_special} special character(s)")
    if uppers < min_upper:
        violations.append(f"Password must contain at least {min_upper} uppercase letter(s)")
    if lowers < min_lower:
        violations.append(f"Password must contain at least {min_lower} lowercase letter(s)")
    if digits < min_num:
        violations.append(f"Password must contain at least {min_num} number(s)")

    if violations:
        msg = "; ".join(violations)
        create_authentication_log(db_log, "signup", "error", f"Password policy failed: {msg}", None, user_agent, client_ip)
        # Password rules are enforced again on the server even though the UI
        # validates them first. Invalid input is rejected without an IP ban.
        return {"status": "passwordPolicyFailed"}

    # Hash the password
    hashed_password = hash_password(user.password)

    user_role = normalize_external_role(
        get_value_by_page_and_key("login_general", "default_user_role", db)
    )

    # Get default group_id
    group_id = get_value_by_page_and_key("login_general", "default_user_group", db)


    user = create_user(
        db,
        email,
        hashed_password,
        user.first_name,
        user.last_name,
        user_role,
        group_id
    )
    if terms_acceptance:
        _record_terms_of_service_acceptance(
            db=db,
            db_log=db_log,
            request=request,
            user_id=user.id,
            revision=int(terms_acceptance["revision"]),
            accepted_at=str(terms_acceptance["accepted_at"]),
            source="password_signup",
        )
    if getattr(user, "role", None) == "pending":
        try:
            create_admin_notification(
                db,
                "user_pending",
                f"New pending user signup: {email or user.id}",
                details={
                    "user_id": user.id,
                    "email": email,
                    "first_name": getattr(user, "first_name", None),
                    "last_name": getattr(user, "last_name", None),
                },
                user_id=user.id,
                notification_type="info",
            )
        except Exception:
            logger.exception("Failed to record admin notification for pending signup")
    create_authentication_log(db_log, "signup", "info", "Signup was successful", user.id, user_agent, client_ip)

    return {"status": "success"}



# -------------------
# Signin
# -------------------
def signin(db, db_log, request, user, response):
    """Handle user sign-in."""
    client_ip = _client_ip_from_request(request, db)
    user_agent = request.headers.get("User-Agent", "Unknown Device")

    # Check if ip address is blocked
    check = check_blocked_ip_address(client_ip, db)
    if check:
        record_auth_ip_block_metric("signin")
        record_auth_login_attempt_metric(False, method="password", reason="ipban")
        try:
            record_ip_address_security_event(
                db,
                client_ip,
                "request_denied",
                event_source="signin",
                reason_code="active_ban",
                route_category="auth",
                reason="Blocked IP attempted to sign in",
                aggregate=True,
            )
        except Exception:
            db.rollback()
        _notify_suspicious_auth_activity(
            "blocked_ip_signin_attempt",
            f"Blocked IP {client_ip} attempted to sign in",
            details={"ip_address": client_ip, "seconds_remaining": check},
        )
        return{"status": "ipban", "expires": check}

    admin_only_mode = getattr(user, "admin_only", False)  # Flag from frontend for admin login

    # Check if user is locked
    signin_payload = user
    plain_password = getattr(signin_payload, "password", None)
    otp_code = getattr(signin_payload, "otp_code", None)
    otp_type = getattr(signin_payload, "otp_type", None)
    otp_action = getattr(signin_payload, "otp_action", None)
    otp_destination = getattr(signin_payload, "otp_destination", None)
    account_mode = _normalize_account_mode(getattr(signin_payload, "account_mode", None))
    replace_slot = getattr(signin_payload, "replace_slot", None)
    terms_acceptance = _extract_terms_acceptance(signin_payload)
    identifier = getattr(signin_payload, "email", None)
    if isinstance(identifier, str):
        identifier = identifier.strip()

    normalized_identifier = identifier
    if isinstance(identifier, str) and "@" in identifier:
        normalized_identifier = identifier.lower()

    if get_value_by_page_and_key("security", "enable_block_user_after_wrong_signin", db):
        lock = check_failed_signin_attempts(normalized_identifier, db)
        if lock:
            record_auth_login_attempt_metric(False, method="password", reason="locked")
            return {"status": "InvalidCredentials"}

    if not normalized_identifier:
        create_authentication_log(db_log, "signin", "warning", "Signin was unsuccessful - identifier missing", None, user_agent, client_ip)
        record_auth_login_attempt_metric(False, method="password", reason="missing_identifier")
        return {"status": "InvalidCredentials"}

    user = _find_user_for_signin_identifier(db, normalized_identifier)

    # Never verify the retained local hash for an organization-managed
    # account. Returning the normal invalid-credentials shape avoids exposing
    # account-management state through this unauthenticated endpoint.
    if user and is_externally_managed(user):
        create_authentication_log(
            db_log,
            "signin",
            "warning",
            "Local password sign-in blocked for externally managed account",
            user.id,
            user_agent,
            client_ip,
        )
        record_auth_login_attempt_metric(
            False,
            method="password",
            reason="externally_managed",
        )
        return {"status": "InvalidCredentials"}

    if isinstance(otp_code, str):
        otp_code = otp_code.strip()
    if isinstance(otp_destination, str):
        otp_destination = otp_destination.strip()
    otp_action = normalize_otp_action(otp_action, otp_type, otp_code)

    local_password_valid = False
    password_needs_rehash = False
    password_proof_binding: tuple[str, str] | None = None
    if user and plain_password:
        # Capture every authority input before verification. In particular, do
        # not reconstruct this proof from an ORM object after a commit, because
        # expire-on-commit could refresh a concurrent password or email change
        # and incorrectly make the stale proof look current.
        verified_user_id = str(getattr(user, "id", "") or "")
        verified_email = canonicalize_user_email(getattr(user, "email", None)) or ""
        verified_password_hash = str(getattr(user, "hashed_password", "") or "")
        local_password_valid, password_needs_rehash = verify_password_with_migration(
            plain_password,
            verified_password_hash,
        )
        if local_password_valid and password_needs_rehash:
            migrated_password_hash = _migrate_verified_legacy_password_hash(
                db,
                user_id=verified_user_id,
                verified_email=verified_email,
                verified_password_hash=verified_password_hash,
                plain_password=plain_password,
            )
            if migrated_password_hash is None:
                create_authentication_log(
                    db_log,
                    "signin",
                    "warning",
                    "Signin blocked because the verified password identity changed during legacy hash migration",
                    verified_user_id or None,
                    user_agent,
                    client_ip,
                )
                record_auth_login_attempt_metric(
                    False,
                    method="password",
                    reason="password_identity_changed",
                )
                return {"status": "InvalidCredentials"}
            verified_password_hash = migrated_password_hash
        if local_password_valid:
            password_proof_binding = (
                verified_email,
                verified_password_hash,
            )

    authenticated_user = user if local_password_valid else None
    login_event = "signin"
    success_message = "Signin was successful"

    if not authenticated_user and plain_password:
        ldap_provider = get_ldap_provider(db)
        ldap_enabled = ldap_provider.is_enabled()
        local_user_is_protected_admin = bool(
            user
            and is_admin_role(user.role)
            and not bool(get_user_setting_value(user.id, "ldap_login", "linked", db))
        )
        should_attempt_ldap = ldap_enabled and not local_user_is_protected_admin

        if should_attempt_ldap:
            try:
                ldap_user = ldap_provider.authenticate(str(normalized_identifier), plain_password)
                if ldap_user:
                    authenticated_user, is_new_ldap_user = _provision_or_sync_ldap_user(
                        db,
                        db_log,
                        ldap_user,
                        request,
                        terms_acceptance=terms_acceptance,
                    )
                    login_event = "ldap_signin"
                    success_message = "LDAP sign-in was successful"
                    if is_new_ldap_user:
                        logger.info("Provisioned new LDAP user %s", authenticated_user.id)
            except HTTPException as exc:
                logger.info("LDAP authentication rejected identifier %s: %s", normalized_identifier, exc.detail)
                if exc.detail == "terms_acceptance_required":
                    return {"status": "termsAcceptanceRequired", "revision": get_terms_of_service_policy(db).get("revision")}
                if exc.detail == "terms_revision_mismatch":
                    return {"status": "termsRevisionMismatch", "revision": get_terms_of_service_policy(db).get("revision")}
                if exc.detail == "terms_configuration_required":
                    return {"status": "termsConfigurationRequired", "revision": get_terms_of_service_policy(db).get("revision")}
                authenticated_user = None
            except Exception:
                logger.exception("Unexpected LDAP authentication failure for identifier %s", normalized_identifier)
                authenticated_user = None

    if not authenticated_user:
        if user:
            increment_user_wrong_sign_in_attempts(db, user.id)
            lock = check_failed_signin_attempts(user.email, db)
            if lock:
                record_auth_login_attempt_metric(False, method="password", reason="locked")
                return {"status": "InvalidCredentials"}
            create_authentication_log(db_log, "signin", "warning", "Signin was unsuccessful", user.id, user_agent, client_ip)
        else:
            create_authentication_log(db_log, "signin", "warning", "Signin was unsuccessful", None, user_agent, client_ip)
        record_auth_login_attempt_metric(False, method=_auth_metric_method(login_event), reason="invalid_credentials")
        return {"status": "InvalidCredentials"}

    signin_enabled = get_value_by_page_and_key("login_general", "enable_signin", db)
    if not is_admin_role(authenticated_user.role) and (admin_only_mode or not signin_enabled):
        if not signin_enabled:
            log_message = "Signin is disabled for non-admin users"
            metric_reason = "signin_disabled"
        else:
            log_message = "Non-admin tried to use admin login"
            metric_reason = "admin_only"
        create_authentication_log(db_log, login_event, "warning", log_message, authenticated_user.id, user_agent, client_ip)
        record_auth_login_attempt_metric(False, method=_auth_metric_method(login_event), reason=metric_reason)
        return {"status": "InvalidCredentials"}

    return _complete_signin_for_user(
        db,
        db_log,
        request,
        response,
        authenticated_user,
        otp_code=otp_code,
        otp_action=otp_action,
        otp_destination=otp_destination,
        log_event=login_event,
        success_message=success_message,
        account_mode=account_mode,
        replace_slot=replace_slot,
        password_proof_binding=password_proof_binding,
    )



# -------------------
# Logout
# -------------------
def _resolve_oidc_rp_logout_url(db, external_auth_provider: str | None) -> str | None:
    """Resolve optional OIDC logout without making local logout depend on the IdP."""

    if str(external_auth_provider or "").strip().lower() != "oidc":
        return None
    try:
        from app.auth.enterprise_sso import EnterpriseOIDCProvider

        return EnterpriseOIDCProvider(db).get_end_session_url()
    except Exception:
        logger.exception("Unable to resolve the OIDC RP-initiated logout endpoint")
        return None


def logout(
    db,
    db_log,
    request,
    user_id,
    token,
    response,
    *,
    token_type: str = "access",
    external_auth_provider: str | None = None,
):
    """Handle user logout."""
    token_column = "refresh_token" if token_type == "refresh" else "access_token"
    try:
        auth_entry = get_authentication(db, user_id, token, token_column)
        # ``delete_authentication`` commits the transaction, which expires this
        # ORM instance. Snapshot the value needed for cookie reconciliation
        # before the row is deleted so SQLAlchemy does not try to reload it.
        auth_refresh_token = auth_entry.refresh_token if auth_entry else None
        if token_type == "refresh":
            delete_authentication(db, refresh_token=token)
        else:
            delete_authentication(db, access_token=token)
        client_host = getattr(request, "client", None)
        client_host = client_host.host if client_host else "Unknown"
        create_authentication_log(
            db_log,
            "logout",
            "info",
            "Logout was successful",
            user_id,
            request.headers.get("User-Agent", "Unknown Device"),
            client_host,
        )
        slot_to_clear = get_active_slot(request)
        if auth_refresh_token:
            if slot_to_clear and request.cookies.get(get_refresh_slot_cookie_name(slot_to_clear)) != auth_refresh_token:
                slot_to_clear = None
            if slot_to_clear is None:
                for account in list_browser_accounts(request, db, response=response, include_legacy=True):
                    if account.refresh_token == auth_refresh_token:
                        slot_to_clear = account.slot
                        break

        if slot_to_clear:
            clear_refresh_slot_cookie(response, slot_to_clear, db, request)
        if request.cookies.get(LEGACY_REFRESH_COOKIE):
            clear_legacy_refresh_cookie(response, db, request)
        clear_access_token_cookie(response, db, request)

        remaining_accounts = [
            account
            for account in list_browser_accounts(request, db, response=response, include_legacy=False)
            if slot_to_clear is None or account.slot != slot_to_clear
        ]
        if remaining_accounts:
            from app.auth.account_slots import set_active_slot_cookie

            fallback_slot = max(
                remaining_accounts,
                key=lambda account: account.last_active_at or datetime.min.replace(tzinfo=timezone.utc),
            ).slot
            set_active_slot_cookie(response, fallback_slot, db, request)
        else:
            clear_active_slot_cookie(response, db, request)
        federated_logout_url = None
        if not remaining_accounts:
            federated_logout_url = _resolve_oidc_rp_logout_url(
                db, external_auth_provider
            )
        record_auth_logout_metric()
        result = {"status": "success"}
        if federated_logout_url:
            result["federated_logout_url"] = federated_logout_url
        return result
    except Exception:
        logger.exception("Logout cleanup failed for user %s", user_id)
        client = getattr(request, "client", None)
        client_host = getattr(client, "host", None) or "Unknown"
        create_authentication_log(
            db_log,
            "logout",
            "error",
            "Logout was unsuccessful",
            user_id,
            request.headers.get("User-Agent", "Unknown Device"),
            client_host,
        )
        raise HTTPException(status_code=500, detail="Logout was unsuccessful.")



# -------------------
# List current logins
# -------------------
def list_current_logins(user_id: str, db, token: str | None = None):
    """List current login sessions for user."""
    # Dont return access and refresh tokens
    auths = list_authentication_login_metadata(db, user_id)
    token_hash = _hash_token_value(token) if token else None
    logins = []
    for auth in auths:
        entry = {
            "id": auth.id,
            "device_info": minimize_session_device_info(auth.device_info),
            "ip_address": minimize_session_ip_address(auth.ip_address),
            "last_active_at": auth.last_active_at,
        }
        # Mark the current session if a token was provided
        if token_hash and getattr(auth, "access_token_hash", None) == token_hash:
            entry["current"] = True
        logins.append(entry)
    return logins



# -------------------
# Delete login(s)
# -------------------
def _refresh_hashes_from_rows(rows) -> set[str]:
    """Extract refresh-token hashes from lightweight authentication rows."""
    return {
        row.refresh_token_hash
        for row in rows
        if getattr(row, "refresh_token_hash", None)
    }


def _clear_deleted_legacy_refresh_cookie(request: Request, response: Response, db, deleted_refresh_hashes: set[str]) -> None:
    """Clear the legacy refresh cookie when it belonged to a deleted login."""
    if not deleted_refresh_hashes:
        return
    legacy_refresh_token = request.cookies.get(LEGACY_REFRESH_COOKIE)
    if legacy_refresh_token and _hash_token_value(legacy_refresh_token) in deleted_refresh_hashes:
        clear_legacy_refresh_cookie(response, db, request)


def _reconcile_browser_login_cookies(request: Request | None, response: Response | None, db, deleted_refresh_hashes: set[str]) -> None:
    """Remove stale auth cookies from the current browser after deleting login rows."""
    if request is None or response is None:
        return
    ensure_active_slot_cookie(request, response, db)
    _clear_deleted_legacy_refresh_cookie(request, response, db, deleted_refresh_hashes)


def delete_login(
    user_id: str,
    db,
    token: str | None = None,
    auth_id: str | None = None,
    request: Request | None = None,
    response: Response | None = None,
    *,
    before_commit=None,
):
    """Delete login session for user."""
    deleted_rows = delete_authentication_login_rows(
        db,
        user_id=user_id,
        auth_id=auth_id,
        before_commit=before_commit,
    )
    deleted_refresh_hashes = _refresh_hashes_from_rows(deleted_rows)
    if auth_id:
        _reconcile_browser_login_cookies(request, response, db, deleted_refresh_hashes)
        return list_current_logins(user_id, db, token)
    _reconcile_browser_login_cookies(request, response, db, deleted_refresh_hashes)
    return {"status": "success"}



# -------------------
# Social Login Callback
# -------------------
def _has_positive_verified_email_signal(email_verified: Any) -> bool:
    """Return True only when a provider positively reports a verified email."""
    if isinstance(email_verified, bool):
        return email_verified is True
    if isinstance(email_verified, str):
        return email_verified.strip().lower() in {"true", "1", "yes"}
    return False


def _has_verified_social_signup_identity(provider: str, user_info: dict[str, Any]) -> bool:
    """Accept a verified email or Microsoft's signed immutable identity.

    Ordinary Microsoft ID tokens do not reliably carry ``email_verified``.
    Their signed ``oid`` and ``tid`` claims are the supported durable identity
    instead.  This exception is deliberately limited to creating a new account;
    it never permits email-based linking to an existing Omlorix account.
    """

    if _has_positive_verified_email_signal(user_info.get("email_verified")):
        return True
    return (
        str(provider or "").strip().lower() == "microsoft"
        and user_info.get("microsoft_identity_verified") is True
        and bool(_provider_subject_id(user_info))
        and bool(str(user_info.get("tenant_id") or "").strip())
    )


def _provider_subject_id(user_info: dict[str, Any]) -> str:
    """Return the provider's immutable user subject from normalized user info."""
    for key in ("sub", "id", "provider_user_id"):
        value = str(user_info.get(key) or "").strip()
        if value:
            return value
    return ""


def _validate_or_store_provider_subject(
    user_id: str,
    section: str,
    provider: str,
    user_info: dict[str, Any],
    db,
    *,
    allow_store_if_missing: bool = True,
) -> bool:
    """Ensure a linked provider account keeps the same immutable upstream subject."""
    incoming_subject = _provider_subject_id(user_info)
    if not incoming_subject:
        return False

    setting_key = f"{provider}_user_id"
    stored_subject = str(get_user_setting_value(user_id, section, setting_key, db) or "").strip()
    if stored_subject:
        return secrets.compare_digest(stored_subject, incoming_subject)

    if not allow_store_if_missing:
        return False

    update_user_settings(user_id, section, setting_key, incoming_subject, db)
    return True


def _validate_or_store_sso_provider_identity(
    user_id: str,
    provider_type: str,
    user_info: dict[str, Any],
    db,
    *,
    allow_store_if_missing: bool = True,
) -> bool:
    """Ensure enterprise SSO remains bound to both the subject and provider config."""
    if not _validate_or_store_provider_subject(
        user_id,
        "sso_login",
        provider_type,
        user_info,
        db,
        allow_store_if_missing=allow_store_if_missing,
    ):
        return False

    incoming_provider_id = str(user_info.get("provider_id") or "").strip()
    if not incoming_provider_id:
        return False

    stored_provider_id = str(get_user_setting_value(user_id, "sso_login", "provider_id", db) or "").strip()
    if stored_provider_id:
        return secrets.compare_digest(stored_provider_id, incoming_provider_id)

    if not allow_store_if_missing:
        return False

    update_user_settings(user_id, "sso_login", "provider_id", incoming_provider_id, db)
    return True


def _has_required_sso_provider_identity(provider_type: str, user_info: dict[str, Any]) -> bool:
    """Return whether SSO user info has the immutable identifiers needed before account creation."""
    return bool(provider_type and _provider_subject_id(user_info) and str(user_info.get("provider_id") or "").strip())


def _find_user_by_linked_provider_subject(
    db: Session,
    *,
    section: str,
    provider: str,
    user_info: dict[str, Any],
):
    """Find an already-linked user by immutable provider subject before falling back to email."""
    provider_subject = _provider_subject_id(user_info)
    if not provider_subject:
        return None

    # Only social-login identities are stored in the normalized table.
    # Enterprise SSO providers use a separate settings schema and provider
    # namespace, so sending names such as ``oidc`` through the social provider
    # normalizer would reject otherwise valid SSO sessions.
    if section == "social_login" and hasattr(db, "query"):
        from app.auth.identities import find_user_by_social_identity

        normalized_user = find_user_by_social_identity(db, provider, user_info)
        if normalized_user is not None:
            return normalized_user
    return _find_user_by_settings_value(
        db,
        (section, f"{provider}_user_id"),
        [provider_subject],
    )


def _record_normalized_social_identity(
    user_id: str,
    provider: str,
    user_info: dict[str, Any],
    db,
    *,
    failure_handler=None,
) -> Any | None:
    """Synchronize verified login state into normalized identity storage."""
    if not hasattr(db, "query"):
        return None
    from app.auth.identities import record_social_identity

    try:
        record_social_identity(user_id, provider, user_info, db)
    except HTTPException as exc:
        if exc.status_code != 409 or failure_handler is None:
            raise
        return failure_handler(
            "social_account_conflict",
            "This provider account is already connected to another Omlorix user.",
            status_code=409,
            log_level="warning",
            log_message=f"Social signin blocked by provider account conflict for {provider}",
            user_id=user_id,
        )
    return None


async def social_login_from_user_info(
    provider: str,
    user_info: dict[str, Any],
    request,
    response,
    db,
    db_log,
    *,
    flow_context: dict[str, Any] | None = None,
    api_mode: bool = False,
):
    """Create or sign in a user after provider identity has been verified."""
    from fastapi.responses import RedirectResponse
    from app.auth.social import SocialAuthProviderFactory

    user_agent = request.headers.get("User-Agent", "Unknown Device")
    client_ip = _client_ip_from_request(request, db)
    flow_context = flow_context or read_flow_context_cookie(request, db, cookie_name=SOCIAL_FLOW_COOKIE)
    social_provider = SocialAuthProviderFactory.get_provider(provider, db)

    def fail(
        error_key: str,
        detail: str,
        *,
        status_code: int = 400,
        log_level: str | None = None,
        log_message: str | None = None,
        user_id: str | None = None,
        query_params: dict[str, str] | None = None,
    ):
        logger.warning(
            "SOCIAL_IDENTITY identity_fail provider=%s error_key=%s status_code=%s api_mode=%s "
            "detail=%s user_id_present=%s",
            provider,
            error_key,
            status_code,
            api_mode,
            str(detail)[:200],
            bool(user_id),
        )
        if log_level and log_message:
            create_authentication_log(
                db_log,
                "social_login",
                log_level,
                log_message,
                user_id,
                user_agent,
                client_ip,
            )
        if api_mode:
            raise HTTPException(status_code=status_code, detail=detail)
        return RedirectResponse(
            url=_build_login_url(
                query_params={"error": error_key, **(query_params or {})},
            ),
            status_code=302,
        )

    def fail_for_login_eligibility(eligibility_result):
        status_code = (eligibility_result or {}).get("status")
        if status_code == "signin_disabled_for_users":
            return fail(
                "social_login_failed",
                "Sign-in is currently disabled for users.",
                status_code=403,
                log_level="warning",
                log_message=f"Social signin blocked: signin disabled for non-admin user {email}",
                user_id=user.id,
            )
        if status_code == "deleted":
            return fail("account_deleted", "This account has been deleted.", status_code=403)
        if status_code == "pending":
            return fail(
                "account_pending",
                "Your account is pending approval. Please wait for an administrator to approve your account.",
                status_code=403,
            )
        if status_code == "inactive":
            return fail("account_inactive", "Your account is inactive. Please contact support.", status_code=403)
        if status_code == "lock":
            return fail(
                "account_locked",
                "Your account is temporarily locked.",
                status_code=403,
                log_level="warning",
                log_message=f"Social signin blocked: user {email} is locked",
                user_id=user.id,
            )
        if status_code == "access_time_blocked":
            return fail(
                "social_login_failed",
                "Sign-in is not available for your account right now.",
                status_code=403,
                log_level="warning",
                log_message=f"Social signin blocked by time restriction: {eligibility_result.get('reason')}",
                user_id=user.id,
            )
        return fail("social_login_failed", "Authentication failed. Please try again.", status_code=403)

    def apply_login_eligibility() -> Any | None:
        eligibility_result = validate_user_login_eligibility(user, db)
        if eligibility_result:
            return fail_for_login_eligibility(eligibility_result)
        return None

    email = str(user_info.get("email") or "").lower().strip()
    logger.debug(
        "SOCIAL_IDENTITY identity_processing provider=%s email_present=%s email_domain=%s "
        "subject_fp=%s workspace_present=%s email_verified=%s allow_signup=%s "
        "flow_context_keys=%s",
        provider,
        bool(email),
        _social_debug_email_domain(email),
        _social_debug_fingerprint(user_info.get("sub")),
        bool(user_info.get("workspace_id")),
        user_info.get("email_verified", "<missing>"),
        social_provider.allows_signup(),
        sorted(flow_context.keys()) if isinstance(flow_context, dict) else [],
    )
    if not email:
        logger.warning("SOCIAL_IDENTITY identity_rejected reason=no_email provider=%s", provider)
        return fail("no_email", "Could not retrieve email from your account.")

    if not social_provider.validate_domain(email):
        logger.warning(
            "SOCIAL_IDENTITY identity_rejected reason=domain_not_allowed provider=%s email_domain=%s",
            provider,
            _social_debug_email_domain(email),
        )
        return fail(
            "domain_not_allowed",
            "Your email domain is not allowed for this application.",
            status_code=403,
            log_level="warning",
            log_message=f"Domain not allowed for {provider} login: {email}",
        )

    # Keep compatibility with existing custom/test providers that predate the
    # optional provider-specific identity-policy hook.
    validate_identity = getattr(social_provider, "validate_identity", lambda _user_info: True)
    if not validate_identity(user_info):
        logger.warning(
            "SOCIAL_IDENTITY identity_rejected reason=workspace_not_allowed provider=%s workspace_present=%s",
            provider,
            bool(user_info.get("workspace_id")),
        )
        return fail(
            "workspace_not_allowed",
            "Your Slack workspace is not allowed for this application.",
            status_code=403,
            log_level="warning",
            log_message=f"Workspace not allowed for {provider} login: {user_info.get('workspace_id', '')}",
        )

    if not _provider_subject_id(user_info):
        logger.warning("SOCIAL_IDENTITY identity_rejected reason=provider_subject_missing provider=%s", provider)
        return fail(
            "provider_subject_missing",
            "Could not verify the social login provider account identity.",
            status_code=403,
            log_level="warning",
            log_message=f"Social login missing provider subject for {provider}: {email}",
        )

    existing_user = _find_user_by_linked_provider_subject(
        db,
        section="social_login",
        provider=provider,
        user_info=user_info,
    )
    matched_existing_provider_subject = existing_user is not None
    is_new_user = existing_user is None
    logger.debug(
        "SOCIAL_IDENTITY identity_lookup provider=%s linked_subject_match=%s",
        provider,
        matched_existing_provider_subject,
    )
    if is_new_user and user_exists_by_email(db, email):
        existing_user = get_user(db, email=email)
        is_new_user = False
        logger.debug(
            "SOCIAL_IDENTITY identity_lookup_email_match provider=%s is_new_user=%s",
            provider,
            is_new_user,
        )

    logger.debug(
        "SOCIAL_IDENTITY identity_account_resolution provider=%s is_new_user=%s existing_user_present=%s",
        provider,
        is_new_user,
        existing_user is not None,
    )

    if is_new_user:
        if not _has_verified_social_signup_identity(provider, user_info):
            logger.warning("SOCIAL_IDENTITY identity_rejected reason=email_not_verified provider=%s", provider)
            return fail(
                "email_not_verified",
                "Your email address is not verified with the social login provider.",
                status_code=403,
                log_level="warning",
                log_message=f"Missing or unverified email signal rejected for {provider} signup: {email}",
            )
        if not _is_new_account_registration_enabled(db):
            logger.warning("SOCIAL_IDENTITY identity_rejected reason=global_signup_disabled provider=%s", provider)
            return fail(
                "signup_not_allowed",
                "New account registration is not available with this login method.",
                status_code=403,
                log_level="warning",
                log_message=f"Social signup blocked globally via login_general.enable_signup for {provider}: {email}",
            )
        if not social_provider.allows_signup():
            logger.warning("SOCIAL_IDENTITY identity_rejected reason=provider_signup_disabled provider=%s", provider)
            return fail(
                "signup_not_allowed",
                "New account registration is not available with this login method.",
                status_code=403,
            )
        try:
            terms_acceptance = _require_terms_ready_for_self_service_signup(db, flow_context)
        except TermsOfServiceSignupError as exc:
            if exc.code == "terms_acceptance_required":
                pending_payload = _build_pending_federated_signup_payload(
                    kind="social",
                    provider=provider,
                    user_info=user_info,
                    flow_context=flow_context,
                )
                if api_mode:
                    _set_pending_federated_signup_cookie(
                        response,
                        db=db,
                        request=request,
                        cookie_name=SOCIAL_PENDING_SIGNUP_COOKIE,
                        payload=pending_payload,
                    )
                    return {
                        "status": "termsAcceptanceRequired",
                        "revision": exc.revision,
                        "pending_terms_signup": True,
                    }

                pending_response = RedirectResponse(
                    url=_build_login_url(
                        account_mode=flow_context.get("account_mode", "primary"),
                        return_url=_sanitize_return_url(flow_context.get("return_url")),
                        query_params={"social_terms_pending": "true", "provider": provider},
                    ),
                    status_code=302,
                )
                _set_pending_federated_signup_cookie(
                    pending_response,
                    db=db,
                    request=request,
                    cookie_name=SOCIAL_PENDING_SIGNUP_COOKIE,
                    payload=pending_payload,
                )
                return pending_response
            return fail(
                exc.code,
                exc.detail,
                status_code=exc.status_code,
                log_level="warning",
                log_message=f"Social signup blocked by terms policy ({exc.code}) for {provider}: {email}",
            )

        first_name = user_info.get("given_name", user_info.get("name", "").split()[0] if user_info.get("name") else "")
        last_name = user_info.get("family_name", " ".join(user_info.get("name", "").split()[1:]) if user_info.get("name") else "")

        temp_password = secrets.token_urlsafe(32)
        hashed_temp_password = hash_password(temp_password)

        login_settings = get_settings_page_data(db, "login_general")
        default_role = normalize_external_role(
            login_settings.get("default_user_role", "pending"),
            default="pending",
        )
        default_group = login_settings.get("default_user_group", "default")

        new_user = create_user(
            db=db,
            email=email,
            hashed_password=hashed_temp_password,
            first_name=first_name or "User",
            last_name=last_name or "",
            role=default_role,
            group_id=default_group,
        )

        if not new_user:
            return fail(
                "user_creation_failed",
                "Failed to create your account. Please try again.",
                status_code=500,
            )

        if terms_acceptance:
            _record_terms_of_service_acceptance(
                db=db,
                db_log=db_log,
                request=request,
                user_id=new_user.id,
                revision=int(terms_acceptance["revision"]),
                accepted_at=str(terms_acceptance["accepted_at"]),
                source=f"social_signup:{provider}",
            )

        update_user_settings(new_user.id, "social_login", "needs_password_setup", True, db)
        update_user_settings(new_user.id, "social_login", f"{provider}_linked", True, db)
        if not _validate_or_store_provider_subject(new_user.id, "social_login", provider, user_info, db):
            return fail(
                "provider_subject_missing",
                "Could not verify the social login provider account identity.",
                status_code=403,
                log_level="warning",
                log_message=f"Social signup missing provider subject for {provider}: {email}",
                user_id=new_user.id,
            )
        identity_failure = _record_normalized_social_identity(
            new_user.id,
            provider,
            user_info,
            db,
            failure_handler=fail,
        )
        if identity_failure is not None:
            return identity_failure

        user = new_user

        create_authentication_log(
            db_log,
            "social_signup",
            "info",
            f"New user created via {provider}: {email}",
            user.id,
            user_agent,
            client_ip,
        )

        if user.role == "pending":
            try:
                create_admin_notification(
                    db,
                    "user_pending",
                    f"New pending user signup via {provider}: {email}",
                    details={
                        "user_id": user.id,
                        "email": email,
                        "provider": provider,
                    },
                    user_id=user.id,
                    notification_type="info",
                )
            except Exception:
                pass
            return fail("account_pending", "Your account is pending approval. Please wait for an administrator to approve your account.", status_code=403)
    else:
        user = existing_user

        if is_externally_managed(user):
            return fail(
                "auth_failed",
                "This account is managed by your organization. Use enterprise sign-in.",
                status_code=403,
                log_level="warning",
                log_message=f"Social signin blocked for externally managed account: {email}",
                user_id=user.id,
            )

        eligibility_failure = apply_login_eligibility()
        if eligibility_failure is not None:
            return eligibility_failure

        provider_linked = get_user_setting_value(user.id, "social_login", f"{provider}_linked", db)
        if provider_linked or matched_existing_provider_subject:
            if not _validate_or_store_provider_subject(
                user.id,
                "social_login",
                provider,
                user_info,
                db,
                # A subject-first lookup has already proven the immutable
                # identity. It may safely repair missing compatibility state,
                # while an email-only match must never create that binding.
                allow_store_if_missing=matched_existing_provider_subject,
            ):
                return fail(
                    "provider_subject_mismatch",
                    "The linked social login account does not match this user.",
                    status_code=403,
                    log_level="warning",
                    log_message=f"Social signin blocked by provider subject mismatch for {provider}: {email}",
                    user_id=user.id,
                )
            if not provider_linked:
                update_user_settings(user.id, "social_login", f"{provider}_linked", True, db)
            identity_failure = _record_normalized_social_identity(
                user.id,
                provider,
                user_info,
                db,
                failure_handler=fail,
            )
            if identity_failure is not None:
                return identity_failure
        else:
            # Keep the generic verified-email diagnostic for providers that
            # issue such a claim. Microsoft does not; its oid/tid identity is
            # already verified, and an email match still never auto-links.
            if (
                str(provider or "").strip().lower() != "microsoft"
                and not _has_positive_verified_email_signal(user_info.get("email_verified"))
            ):
                return fail(
                    "email_not_verified",
                    "Your email address is not verified with the social login provider.",
                    status_code=403,
                    log_level="warning",
                    log_message=f"Missing or unverified email signal rejected for {provider} auto-link: {email}",
                    user_id=user.id,
                )
            from app.auth.identities import SOCIAL_PROVIDER_LABELS

            normalized_provider = str(provider or "").strip().lower()
            provider_label = SOCIAL_PROVIDER_LABELS.get(normalized_provider, normalized_provider.title())
            return fail(
                "social_account_not_linked",
                f"Your Omlorix account is not linked to {provider_label}. "
                f"Sign in with another method first, then link {provider_label} to this account.",
                status_code=403,
                log_level="warning",
                log_message=f"Social signin blocked: existing account is not linked to {provider}: {email}",
                user_id=user.id,
                query_params={"provider": normalized_provider},
            )

    eligibility_failure = apply_login_eligibility()
    if eligibility_failure is not None:
        return eligibility_failure

    lock = check_user_locked(db, user.id)
    if isinstance(lock, dict) and lock.get("is_locked"):
        return fail(
            "account_locked",
            "Your account is temporarily locked.",
            status_code=403,
            log_level="warning",
            log_message=f"Social signin blocked: user {email} is locked",
            user_id=user.id,
        )

    await _sync_social_profile_picture(
        user,
        provider=provider,
        user_info=user_info,
        db=db,
    )

    ensure_provider_alignment(user.id, db)
    twofa_result = evaluate_login_2fa(user, otp_code=None, otp_action=None, otp_destination=None, db=db, client_ip=client_ip)
    if twofa_result and twofa_result.get("status") in {"otp_setup", "otp_required_already_setup"}:
        social_token, social_token_expires = _set_pending_social_token(
            user.id,
            provider,
            db,
            allow_setup_material=twofa_result.get("status") == "otp_setup",
        )
        if api_mode:
            _set_one_time_browser_cookie(
                response,
                "social_login_token",
                social_token,
                db,
                request,
                max_age=max(1, int((social_token_expires - datetime.now(timezone.utc)).total_seconds())),
            )
            return twofa_result

        mode = "setup" if twofa_result.get("status") == "otp_setup" else "verify"
        fragment = f"social_2fa={mode}&provider={provider}"
        for key in ("delivery_hint", "resend_available_in_seconds", "setup_material_available"):
            value = twofa_result.get(key)
            if value is None:
                continue
            fragment += f"&{key}={quote(str(value), safe='')}"
        if twofa_result.get("provider"):
            fragment += f"&provider_2fa={quote(str(twofa_result.get('provider')), safe='')}"
        redirect_response = RedirectResponse(
            url=_build_login_url(
                account_mode=flow_context.get("account_mode", "primary"),
                return_url=_sanitize_return_url(flow_context.get("return_url")),
                fragment=fragment,
            ),
            status_code=302,
        )
        _set_one_time_browser_cookie(
            redirect_response,
            "social_login_token",
            social_token,
            db,
            request,
            max_age=max(1, int((social_token_expires - datetime.now(timezone.utc)).total_seconds())),
        )
        return redirect_response

    if api_mode:
        return await _complete_social_login_api(user, request, response, db, db_log, flow_context=flow_context)
    return await _complete_social_login(
        user,
        request,
        response,
        db,
        db_log,
        is_new_user,
        flow_context=flow_context,
    )


async def confirm_pending_social_terms_signup(
    *,
    request,
    response,
    db,
    db_log,
    terms_payload: Any,
):
    """Finish a pending social signup after the user accepts current terms."""
    pending = _read_pending_federated_signup_cookie(
        request,
        db,
        cookie_name=SOCIAL_PENDING_SIGNUP_COOKIE,
    )
    if not pending or pending.get("kind") != "social":
        return _json_error_clearing_pending_federated_signup_cookie(
            detail="pending_social_signup_missing",
            status_code=400,
            cookie_name=SOCIAL_PENDING_SIGNUP_COOKIE,
        )

    provider = str(pending.get("provider") or "").strip().lower()
    user_info = pending.get("user_info") if isinstance(pending.get("user_info"), dict) else {}
    flow_context = pending.get("flow_context") if isinstance(pending.get("flow_context"), dict) else {}
    flow_context = {
        **flow_context,
        **_extract_terms_acceptance(terms_payload),
    }

    _clear_pending_federated_signup_cookie(response, cookie_name=SOCIAL_PENDING_SIGNUP_COOKIE)
    result = await social_login_from_user_info(
        provider=provider,
        user_info=user_info,
        request=request,
        response=response,
        db=db,
        db_log=db_log,
        flow_context=flow_context,
        api_mode=(
            flow_context.get("native_auth") is True
            and flow_context.get("native_kind") == "social"
        ),
    )
    if hasattr(result, "delete_cookie"):
        result.delete_cookie(SOCIAL_PENDING_SIGNUP_COOKIE)
    return result


def cancel_pending_social_terms_signup(*, response) -> dict[str, str]:
    """Cancel a pending social signup and forget the short-lived provider context."""
    _clear_pending_federated_signup_cookie(response, cookie_name=SOCIAL_PENDING_SIGNUP_COOKIE)
    return {"status": "cancelled"}


def _merge_apple_form_post_user(user_info: dict[str, Any], raw_user_payload: str | None) -> dict[str, Any]:
    """Merge Apple's first-login form_post user payload into verified ID token claims."""
    if not raw_user_payload:
        return user_info

    try:
        payload = json.loads(raw_user_payload)
    except (TypeError, ValueError):
        return user_info
    if not isinstance(payload, dict):
        return user_info

    merged = dict(user_info)
    name_payload = payload.get("name")
    if isinstance(name_payload, dict):
        first_name = str(name_payload.get("firstName") or name_payload.get("givenName") or "").strip()
        last_name = str(name_payload.get("lastName") or name_payload.get("familyName") or "").strip()
        if first_name and not merged.get("given_name"):
            merged["given_name"] = first_name
        if last_name and not merged.get("family_name"):
            merged["family_name"] = last_name
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        if full_name and not merged.get("name"):
            merged["name"] = full_name

    return merged


async def verified_social_user_info_from_callback(
    provider: str,
    code: str,
    redirect_uri: str,
    db,
    *,
    expected_nonce_hash: str | None = None,
    apple_user_payload: str | None = None,
) -> dict[str, Any]:
    """Exchange an OAuth code and return a verified, normalized identity.

    This reusable verification boundary lets ordinary sign-in and authenticated
    account linking share the same provider token, nonce, and Apple form-post
    handling without allowing the linking path to fall back to email matching.
    """
    from app.auth.social import SocialAuthProviderFactory

    social_provider = SocialAuthProviderFactory.get_provider(provider, db)
    tokens = await social_provider.exchange_code_for_tokens(code, redirect_uri)
    logger.debug(
        "SOCIAL_IDENTITY business_callback_tokens provider=%s token_keys=%s "
        "access_token_present=%s id_token_present=%s refresh_token_present=%s token_type=%s",
        provider,
        sorted(tokens.keys()) if isinstance(tokens, dict) else [],
        bool(tokens.get("access_token")) if isinstance(tokens, dict) else False,
        bool(tokens.get("id_token")) if isinstance(tokens, dict) else False,
        bool(tokens.get("refresh_token")) if isinstance(tokens, dict) else False,
        tokens.get("token_type", "<missing>") if isinstance(tokens, dict) else "<missing>",
    )
    normalized_provider = provider.lower()
    token_for_user_info = (
        tokens.get("id_token")
        if normalized_provider in {"apple", "slack"}
        else tokens.get("access_token")
    )
    if not token_for_user_info:
        logger.warning(
            "SOCIAL_IDENTITY business_callback_identity_token_missing provider=%s token_keys=%s",
            provider,
            sorted(tokens.keys()) if isinstance(tokens, dict) else [],
        )
        raise HTTPException(status_code=400, detail="Provider identity token is missing.")

    user_info = await social_provider.get_user_info(token_for_user_info, tokens=tokens)
    if not isinstance(user_info, dict):
        raise HTTPException(status_code=400, detail="Provider identity response is invalid.")

    if normalized_provider in {"apple", "google", "slack", "microsoft"}:
        token_nonce = str(user_info.get("nonce") or "")
        computed_nonce_hash = hashlib.sha256(token_nonce.encode()).hexdigest() if token_nonce else ""
        logger.debug(
            "SOCIAL_IDENTITY business_callback_nonce_compare provider=%s token_nonce_present=%s "
            "computed_nonce_hash_fp=%s expected_nonce_hash_fp=%s match=%s",
            provider,
            bool(token_nonce),
            _social_debug_fingerprint(computed_nonce_hash),
            _social_debug_fingerprint(expected_nonce_hash),
            bool(
                expected_nonce_hash
                and computed_nonce_hash
                and secrets.compare_digest(computed_nonce_hash, expected_nonce_hash)
            ),
        )
        if (
            not expected_nonce_hash
            or not computed_nonce_hash
            or not secrets.compare_digest(computed_nonce_hash, expected_nonce_hash)
        ):
            logger.warning(
                "SOCIAL_IDENTITY business_callback_nonce_mismatch provider=%s token_nonce_present=%s "
                "expected_nonce_hash_present=%s",
                provider,
                bool(token_nonce),
                bool(expected_nonce_hash),
            )
            raise HTTPException(status_code=403, detail="Provider nonce verification failed.")

    if normalized_provider == "apple":
        user_info = _merge_apple_form_post_user(user_info, apple_user_payload)
    return user_info


async def social_login_callback(
    provider: str,
    code: str,
    state: str,
    redirect_uri: str,
    request,
    response,
    db,
    db_log,
    *,
    expected_nonce_hash: str | None = None,
    apple_user_payload: str | None = None,
):
    """Handle OAuth callback and create/link user account."""
    from fastapi.responses import RedirectResponse
    user_agent = request.headers.get("User-Agent", "Unknown Device")
    client_ip = _client_ip_from_request(request, db)
    flow_context = read_flow_context_cookie(request, db, cookie_name=SOCIAL_FLOW_COOKIE)

    logger.debug(
        "SOCIAL_IDENTITY business_callback_start provider=%s code_fp=%s code_length=%s "
        "state_fp=%s state_length=%s redirect_uri=%s expected_nonce_hash_present=%s "
        "flow_context_keys=%s api_mode=false",
        provider,
        _social_debug_fingerprint(code),
        len(str(code or "")),
        _social_debug_fingerprint(state),
        len(str(state or "")),
        redirect_uri,
        bool(expected_nonce_hash),
        sorted(flow_context.keys()) if isinstance(flow_context, dict) else [],
    )
    
    try:
        try:
            user_info = await verified_social_user_info_from_callback(
                provider,
                code,
                redirect_uri,
                db,
                expected_nonce_hash=expected_nonce_hash,
                apple_user_payload=apple_user_payload,
            )
        except HTTPException as exc:
            if exc.detail == "Provider identity token is missing.":
                create_authentication_log(
                    db_log, "social_login", "error",
                    f"Failed to get identity token from {provider}",
                    None, user_agent, client_ip
                )
                return RedirectResponse(url="/login?error=token_exchange_failed", status_code=302)
            if exc.detail == "Provider nonce verification failed.":
                create_authentication_log(
                    db_log, "social_login", "warning",
                    f"{provider.title()} social login nonce mismatch",
                    None, user_agent, client_ip
                )
                return RedirectResponse(url="/login?error=social_state_invalid", status_code=302)
            raise

        logger.debug(
            "SOCIAL_IDENTITY business_callback_user_info provider=%s user_info_keys=%s "
            "subject_fp=%s email_domain=%s email_verified=%s workspace_id=%s nonce_present=%s",
            provider,
            sorted(user_info.keys()) if isinstance(user_info, dict) else [],
            _social_debug_fingerprint(user_info.get("sub")) if isinstance(user_info, dict) else "<missing>",
            _social_debug_email_domain(user_info.get("email")) if isinstance(user_info, dict) else "<missing>",
            user_info.get("email_verified", "<missing>") if isinstance(user_info, dict) else "<missing>",
            "<present>" if isinstance(user_info, dict) and user_info.get("workspace_id") else "<missing>",
            bool(user_info.get("nonce")) if isinstance(user_info, dict) else False,
        )

        return await social_login_from_user_info(
            provider=provider,
            user_info=user_info,
            request=request,
            response=response,
            db=db,
            db_log=db_log,
            flow_context=flow_context,
            api_mode=False,
        )
        
    except HTTPException as e:
        logger.warning(
            "SOCIAL_IDENTITY business_callback_http_exception provider=%s status_code=%s detail=%s",
            provider,
            e.status_code,
            str(e.detail)[:200],
        )
        create_authentication_log(
            db_log, "social_login", "error",
            f"Social login error: {e.detail}",
            None, user_agent, client_ip
        )
        # Use a known error key, not raw exception detail
        return RedirectResponse(url="/login?error=social_login_failed", status_code=302)
    except Exception as e:
        logger.exception(
            "SOCIAL_IDENTITY business_callback_exception provider=%s error_type=%s error=%s",
            provider,
            type(e).__name__,
            str(e)[:200],
        )
        create_authentication_log(
            db_log, "social_login", "error",
            f"Social login exception: {str(e)}",
            None, user_agent, client_ip
        )
        logger.exception("[SOCIAL LOGIN ERROR] %s", str(e))
        return RedirectResponse(url="/login?error=social_login_failed", status_code=302)



# -------------------
# Complete Social Login (internal helper)
# -------------------
async def _complete_social_login(user, request, response, db, db_log, is_new_user=False, flow_context: dict[str, Any] | None = None):
    """Complete the social login process by issuing tokens.
    
    Uses a one-time auth code pattern to avoid leaking access tokens in URL
    query parameters. The frontend exchanges the code for the real token
    via a secure POST to /api/v1/auth/social/exchange.
    """
    from fastapi.responses import RedirectResponse
    flow_context = flow_context or {"account_mode": "primary", "replace_slot": None, "return_url": ""}
    
    # Clear pending social token
    _clear_pending_social_token(user.id, db)
    _clear_one_time_browser_cookie(response, "social_login_token")
    
    from app.auth.native import create_native_exchange_callback, is_native_flow

    if is_native_flow(flow_context, kind="social"):
        native_callback = create_native_exchange_callback(
            db,
            kind="social",
            provider=str(flow_context.get("native_provider") or "social"),
            user_id=user.id,
            flow_context=flow_context,
        )
        redirect_response = RedirectResponse(url=native_callback, status_code=302)
        _copy_response_set_cookies(response, redirect_response)
        _clear_one_time_browser_cookie(redirect_response, "social_login_token")
        clear_flow_context_cookie(redirect_response, db, request, cookie_name=SOCIAL_FLOW_COOKIE)
        return redirect_response
    
    # Generate a one-time authorization code instead of passing token in URL
    # This code can be exchanged once for the access token via a secure POST endpoint
    auth_code = secrets.token_urlsafe(32)
    auth_code_hash = _hash_pending_action_value(
        auth_code, db, purpose="social_auth_code"
    )
    auth_code_expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    
    # Store only the hash of the auth code and its indexed owner mapping.
    _commit_pending_auth_action(
        db,
        user_id=user.id,
        purpose=_PENDING_ACTION_SOCIAL_AUTH_CODE,
        token_hash=auth_code_hash,
        expires_at=auth_code_expires,
        settings_updates={
            "social_login": {
                "pending_auth_code": auth_code_hash,
                "pending_auth_code_expires": auth_code_expires.isoformat(),
            },
        },
    )
    
    # Redirect without embedding the auth code in the URL fragment.
    redirect_url = _build_login_url(
        account_mode=flow_context.get("account_mode", "primary"),
        return_url=_sanitize_return_url(flow_context.get("return_url")),
        fragment="social_success=true",
    )
    
    redirect_response = RedirectResponse(url=redirect_url, status_code=302)

    _copy_response_set_cookies(response, redirect_response)
    _clear_one_time_browser_cookie(redirect_response, "social_login_token")

    # Bind the auth code redemption to this browser via an HttpOnly cookie (5 min lifetime)
    _set_one_time_browser_cookie_strict(redirect_response, "social_auth_code", auth_code, db, request, max_age=300)

    return redirect_response



# -------------------
# Complete Social Login with 2FA
# -------------------
async def complete_social_login_with_2fa(
    provider: str,
    social_token: str | None,
    otp_code: str | None,
    otp_type: str | None,
    otp_action: str | None,
    otp_destination: str | None,
    request,
    response,
    db,
    db_log,
):
    """Complete social login flow after 2FA verification."""
    user_agent = request.headers.get("User-Agent", "Unknown Device")
    client_ip = _client_ip_from_request(request, db)
    flow_context = read_flow_context_cookie(request, db, cookie_name=SOCIAL_FLOW_COOKIE)
    if not social_token:
        social_token = request.cookies.get("social_login_token")
    if not social_token:
        return {"status": "error", "detail": "Invalid or expired social login token"}
    
    user = _find_user_by_pending_social_token(db, social_token)
    
    if not user:
        _clear_one_time_browser_cookie(response, "social_login_token")
        return {"status": "error", "detail": "Invalid or expired social login token"}
    if is_externally_managed(user):
        _clear_pending_social_token(
            user.id,
            db,
            raw_token=social_token,
        )
        _clear_one_time_browser_cookie(response, "social_login_token")
        return {"status": "error", "detail": "Invalid or expired social login token"}

    expires_str = get_user_setting_value(
        user.id,
        "social_login",
        "pending_social_token_expires",
        db,
        commit=False,
    )
    if not expires_str:
        _clear_pending_social_token(user.id, db)
        _clear_one_time_browser_cookie(response, "social_login_token")
        return {"status": "error", "detail": "Invalid or expired social login token"}
    try:
        expires_at = datetime.fromisoformat(expires_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            _clear_pending_social_token(user.id, db)
            _clear_one_time_browser_cookie(response, "social_login_token")
            return {"status": "error", "detail": "Invalid or expired social login token"}
    except ValueError:
        _clear_pending_social_token(user.id, db)
        _clear_one_time_browser_cookie(response, "social_login_token")
        return {"status": "error", "detail": "Invalid or expired social login token"}
    
    # Verify the provider matches
    pending_provider = get_user_setting_value(
        user.id,
        "social_login",
        "pending_provider",
        db,
        commit=False,
    )
    if pending_provider != provider:
        _clear_pending_social_token(user.id, db)
        _clear_one_time_browser_cookie(response, "social_login_token")
        return {"status": "error", "detail": "Provider mismatch"}
    
    action = normalize_otp_action(otp_action, otp_type, otp_code)
    result = evaluate_login_2fa(
        user,
        otp_code=otp_code,
        otp_action=action,
        otp_destination=otp_destination,
        db=db,
        client_ip=client_ip,
    )
    if result:
        if result.get("status") == "otp_invalid":
            create_authentication_log(
                db_log, "social_signin", "warning",
                "OTP code invalid during social login",
                user.id, user_agent, client_ip
            )
        elif result.get("status") == "otp_locked":
            create_authentication_log(
                db_log, "social_signin", "warning",
                "OTP verification locked after repeated failures during social login",
                user.id, user_agent, client_ip
            )
        return result
    
    # 2FA verified - complete login
    return await _complete_social_login_api(
        user,
        request,
        response,
        db,
        db_log,
        flow_context=flow_context,
        twofa_satisfied=True,
        pending_token=social_token,
    )



# -------------------
# Complete Social Login API Response
# -------------------
async def _complete_social_login_api(
    user,
    request,
    response,
    db,
    db_log,
    flow_context: dict[str, Any] | None = None,
    *,
    twofa_satisfied: bool = False,
    pending_token: str | None = None,
):
    """Complete social login and return API response (not redirect)."""
    flow_context = flow_context or {"account_mode": "primary", "replace_slot": None, "return_url": ""}
    
    # Clear pending social token
    if not _clear_pending_social_token(
        user.id,
        db,
        raw_token=pending_token,
    ):
        _clear_one_time_browser_cookie(response, "social_login_token")
        return {
            "status": "error",
            "detail": "Invalid or expired social login token",
        }
    _clear_one_time_browser_cookie(response, "social_login_token")

    from app.auth.native import create_native_exchange_callback, is_native_flow

    if is_native_flow(flow_context, kind="social"):
        native_callback = create_native_exchange_callback(
            db,
            kind="social",
            provider=str(flow_context.get("native_provider") or "social"),
            user_id=user.id,
            flow_context=flow_context,
            twofa_satisfied=twofa_satisfied,
        )
        clear_flow_context_cookie(response, db, request, cookie_name=SOCIAL_FLOW_COOKIE)
        return {
            "status": "success",
            "native_callback_url": native_callback,
        }
    
    issued = _issue_authenticated_session(
        db=db,
        db_log=db_log,
        request=request,
        response=response,
        user=user,
        log_event="social_signin",
        success_message="Social login with 2FA successful",
        account_mode=flow_context.get("account_mode", "primary"),
        replace_slot=flow_context.get("replace_slot"),
        twofa_satisfied=twofa_satisfied,
    )
    if not issued.get("session_authenticated"):
        return issued

    clear_flow_context_cookie(response, db, request, cookie_name=SOCIAL_FLOW_COOKIE)
    
    return {
        "status": "success",
        **issued,
    }



# -------------------
# SSO Login Callback
# -------------------
async def sso_login_callback(
    provider_type: str,
    request_data: Dict[str, Any],
    redirect_uri: str,
    request,
    response,
    db,
    db_log,
    security_data=None,
    upstream_request=None,
):
    """Handle enterprise SSO callback and create/link user account."""
    from fastapi.responses import RedirectResponse
    from app.auth.enterprise_sso import EnterpriseSSOProviderFactory
    from app.users.models import get_user, create_user
    from app.users.init import update_user_settings
    from app.admin.settings.utils import create_admin_notification
    import secrets
    import logging
    
    logger = logging.getLogger(__name__)
    
    user_agent = request.headers.get("User-Agent", "Unknown Device")
    client_ip = _client_ip_from_request(request, db)
    flow_context = read_flow_context_cookie(request, db, cookie_name=SSO_FLOW_COOKIE)
    
    try:
        logger.info(f"SSO login callback started for provider: {provider_type}")
        sso_provider = EnterpriseSSOProviderFactory.get_provider(provider_type, db)
        
        # Handle SSO callback with security data for nonce/request_id validation
        callback_request = upstream_request or request
        user_info = await sso_provider.handle_callback(
            request_data,
            redirect_uri,
            security_data,
            request=callback_request,
        )
        logger.info(f"SSO callback processed for provider: {user_info.get('provider', 'unknown')}")
        
        email = user_info.get("email", "").lower().strip()
        email_verified = user_info.get("email_verified", None)
        
        if not email:
            return RedirectResponse(url="/login?error=no_email", status_code=302)

        if not _has_positive_verified_email_signal(email_verified):
            return RedirectResponse(url="/login?error=email_not_verified", status_code=302)
        
        # Validate domain restrictions
        if not sso_provider.validate_domain(email):
            create_authentication_log(
                db_log, "sso_login", "warning",
                f"Domain not allowed for {provider_type} SSO: {email}",
                None, user_agent, client_ip
            )
            return RedirectResponse(url="/login?error=domain_not_allowed", status_code=302)

        if not _provider_subject_id(user_info):
            create_authentication_log(
                db_log, "sso_login", "warning",
                f"SSO login missing provider subject for {provider_type}: {email}",
                None, user_agent, client_ip
            )
            return RedirectResponse(url="/login?error=sso_login_failed", status_code=302)
        
        # Check if user exists
        existing_user = _find_user_by_linked_provider_subject(
            db,
            section="sso_login",
            provider=provider_type,
            user_info=user_info,
        )
        matched_existing_provider_subject = existing_user is not None
        is_new_user = existing_user is None
        if is_new_user and user_exists_by_email(db, email):
            existing_user = get_user(db, email=email)
            is_new_user = False
        if is_new_user:
            if not _is_new_account_registration_enabled(db):
                create_authentication_log(
                    db_log, "sso_signup", "warning",
                    f"SSO signup blocked globally via login_general.enable_signup for {provider_type}: {email}",
                    None, user_agent, client_ip
                )
                return RedirectResponse(url="/login?error=signup_not_allowed", status_code=302)
            # Check if JIT provisioning is allowed
            if not sso_provider.allows_jit_provisioning():
                return RedirectResponse(url="/login?error=signup_not_allowed", status_code=302)
            try:
                terms_acceptance = _require_terms_ready_for_self_service_signup(db, flow_context)
            except TermsOfServiceSignupError as exc:
                if exc.code == "terms_acceptance_required":
                    pending_response = RedirectResponse(
                        url=_build_login_url(
                            account_mode=flow_context.get("account_mode", "primary"),
                            return_url=_sanitize_return_url(flow_context.get("return_url")),
                            query_params={"sso_terms_pending": "true", "provider": provider_type},
                        ),
                        status_code=302,
                    )
                    _set_pending_federated_signup_cookie(
                        pending_response,
                        db=db,
                        request=request,
                        cookie_name=SSO_PENDING_SIGNUP_COOKIE,
                        payload=_build_pending_federated_signup_payload(
                            kind="sso",
                            provider_type=provider_type,
                            user_info=user_info,
                            flow_context=flow_context,
                        ),
                    )
                    create_authentication_log(
                        db_log,
                        "sso_signup",
                        "info",
                        f"SSO signup is waiting for terms acceptance for {provider_type}: {email}",
                        None,
                        user_agent,
                        client_ip,
                    )
                    return pending_response
                create_authentication_log(
                    db_log,
                    "sso_signup",
                    "warning",
                    f"SSO signup blocked by terms policy ({exc.code}) for {provider_type}: {email}",
                    None,
                    user_agent,
                    client_ip,
                )
                return RedirectResponse(url=f"/login?error={exc.code}", status_code=302)
            
            # Create new user with JIT provisioning
            first_name = user_info.get("given_name", user_info.get("name", "").split()[0] if user_info.get("name") else "")
            last_name = user_info.get("family_name", " ".join(user_info.get("name", "").split()[1:]) if user_info.get("name") else "")
            temp_password = secrets.token_urlsafe(32)
            hashed_temp_password = hash_password(temp_password)
            
            # Get default role and group from SSO provider settings
            default_role = _resolve_sso_role(sso_provider, user_info)
            default_group = _resolve_sso_group_id(db, sso_provider, user_info)

            if not _has_required_sso_provider_identity(provider_type, user_info):
                create_authentication_log(
                    db_log, "sso_signup", "warning",
                    f"SSO signup missing provider identity for {provider_type}: {email}",
                    None, user_agent, client_ip
                )
                return RedirectResponse(url="/login?error=sso_login_failed", status_code=302)
            
            try:
                new_user = create_user(
                    db=db,
                    email=email,
                    hashed_password=hashed_temp_password,
                    first_name=first_name or "User",
                    last_name=last_name or "",
                    role=default_role,
                    group_id=default_group,
                )
            except Exception as create_error:
                logger.exception("Step 6c ERROR creating user: %s", str(create_error))
                raise
            
            if not new_user:
                return RedirectResponse(url="/login?error=user_creation_failed", status_code=302)

            if terms_acceptance:
                _record_terms_of_service_acceptance(
                    db=db,
                    db_log=db_log,
                    request=request,
                    user_id=new_user.id,
                    revision=int(terms_acceptance["revision"]),
                    accepted_at=str(terms_acceptance["accepted_at"]),
                    source=f"sso_signup:{provider_type}",
                )
            
            # Mark user as SSO-provisioned
            update_user_settings(new_user.id, "sso_login", "needs_password_setup", False, db)
            update_user_settings(new_user.id, "sso_login", f"{provider_type}_linked", True, db)
            if not _validate_or_store_sso_provider_identity(new_user.id, provider_type, user_info, db):
                create_authentication_log(
                    db_log, "sso_signup", "warning",
                    f"SSO signup missing provider identity for {provider_type}: {email}",
                    new_user.id, user_agent, client_ip
                )
                return RedirectResponse(url="/login?error=sso_login_failed", status_code=302)
            mark_user_externally_managed(db, new_user, provider_type)
            
            user = new_user
            
            create_authentication_log(
                db_log, "sso_signup", "info",
                f"New user created via {provider_type} SSO: {email}",
                user.id, user_agent, client_ip
            )
            
            # Check if new user is pending (based on default role setting)
            if user.role == "pending":
                try:
                    create_admin_notification(
                        db,
                        "user_pending",
                        f"New pending user signup via {provider_type} SSO: {email}",
                        details={
                            "user_id": user.id,
                            "email": email,
                            "provider": provider_type,
                        },
                        user_id=user.id,
                        notification_type="info",
                    )
                except Exception:
                    pass
                return RedirectResponse(url="/login?error=account_pending", status_code=302)
        else:
            user = existing_user
            
            # Check if user is active
            if not user.is_active:
                return RedirectResponse(url="/login?error=account_inactive", status_code=302)
            
            # Check if user is deleted
            if user.deleted_at:
                return RedirectResponse(url="/login?error=account_deleted", status_code=302)
            
            # Check if user is pending
            if user.role == "pending":
                return RedirectResponse(url="/login?error=account_pending", status_code=302)
            
            # Link SSO provider if not already linked
            try:
                provider_linked = bool(
                    get_user_setting_value(
                        user.id,
                        "sso_login",
                        f"{provider_type}_linked",
                        db,
                    )
                )
                has_bound_provider_identity = bool(
                    provider_linked or matched_existing_provider_subject
                )

                # Email matching is an account-discovery mechanism, not proof
                # that an external identity owns a protected local account.
                # Without this guard, an IdP configured for email linking could
                # claim the owner/admin account and inherit its preserved role.
                # Existing subject-bound links remain valid and continue through
                # the strict provider-subject validation below.
                if is_admin_role(user.role) and not has_bound_provider_identity:
                    protected_role = str(user.role or "administrative").strip().lower()
                    create_authentication_log(
                        db_log,
                        "sso_login",
                        "warning",
                        f"SSO email linking blocked for protected {protected_role} account: {email}",
                        user.id,
                        user_agent,
                        client_ip,
                    )
                    return RedirectResponse(
                        url="/login?error=sso_login_failed",
                        status_code=302,
                    )

                if has_bound_provider_identity:
                    if not _validate_or_store_sso_provider_identity(
                        user.id,
                        provider_type,
                        user_info,
                        db,
                        allow_store_if_missing=False,
                    ):
                        create_authentication_log(
                            db_log, "sso_login", "warning",
                            f"SSO signin blocked by provider identity mismatch for {provider_type}: {email}",
                            user.id, user_agent, client_ip
                        )
                        return RedirectResponse(url="/login?error=sso_login_failed", status_code=302)
                    if not provider_linked:
                        update_user_settings(user.id, "sso_login", f"{provider_type}_linked", True, db)
                else:
                    if not sso_provider.link_existing_users_by_email():
                        return RedirectResponse(url="/login?error=signup_not_allowed", status_code=302)
                    if not _validate_or_store_sso_provider_identity(user.id, provider_type, user_info, db):
                        create_authentication_log(
                            db_log, "sso_login", "warning",
                            f"SSO signin missing provider identity for {provider_type}: {email}",
                            user.id, user_agent, client_ip
                        )
                        return RedirectResponse(url="/login?error=sso_login_failed", status_code=302)
                    update_user_settings(user.id, "sso_login", f"{provider_type}_linked", True, db)
                _sync_existing_user_from_sso(db, user, user_info, sso_provider, request=request)
                # Email linking is the administrator's explicit migration
                # decision for pre-SSO users. Protected owner/admin accounts
                # remain local break-glass identities.
                if not is_admin_role(user.role):
                    transitioned = mark_user_externally_managed(
                        db,
                        user,
                        provider_type,
                    )
                    if transitioned:
                        create_authentication_log(
                            db_log,
                            "sso_account_managed",
                            "info",
                            f"Existing account transitioned to externally managed via {provider_type}",
                            user.id,
                            user_agent,
                            client_ip,
                        )
            except Exception as link_error:
                logger.exception("Step 8 ERROR: %s", str(link_error))
                raise

        eligibility_redirect = _sso_login_eligibility_redirect_response(
            user=user,
            email=email,
            db=db,
            db_log=db_log,
            user_agent=user_agent,
            client_ip=client_ip,
        )
        if eligibility_redirect is not None:
            return eligibility_redirect
        
        # Check 2FA requirements
        ensure_provider_alignment(user.id, db)

        twofa_result = evaluate_login_2fa(user, otp_code=None, otp_action=None, otp_destination=None, db=db, client_ip=client_ip)
        if twofa_result and twofa_result.get("status") in {"otp_setup", "otp_required_already_setup"}:
            sso_token, sso_token_expires = _set_pending_sso_token(
                user.id,
                provider_type,
                db,
                allow_setup_material=twofa_result.get("status") == "otp_setup",
            )
            mode = "setup" if twofa_result.get("status") == "otp_setup" else "verify"
            query_params = {
                "sso_2fa": mode,
                "provider": provider_type,
            }
            for key in ("delivery_hint", "resend_available_in_seconds", "setup_material_available"):
                value = twofa_result.get(key)
                if value is None:
                    continue
                query_params[key] = str(value)
            if twofa_result.get("provider"):
                query_params["provider_2fa"] = str(twofa_result.get("provider"))
            redirect_response = RedirectResponse(
                url=_build_login_url(
                    account_mode=flow_context.get("account_mode", "primary"),
                    return_url=_sanitize_return_url(flow_context.get("return_url")),
                    query_params={key: value for key, value in query_params.items() if key != "sso_token"},
                ),
                status_code=302,
            )
            _set_one_time_browser_cookie(
                redirect_response,
                "sso_login_token",
                sso_token,
                db,
                request,
                max_age=max(1, int((sso_token_expires - datetime.now(timezone.utc)).total_seconds())),
            )
            return redirect_response
        
        # No 2FA required - complete login
        return await _complete_sso_login(
            user,
            request,
            response,
            db,
            db_log,
            is_new_user,
            flow_context=flow_context,
        )
        
    except HTTPException as e:
        from app.auth.diagnostics import (
            build_sso_failure_url,
            classify_sso_exception,
            new_auth_reference,
            record_sso_diagnostic,
        )
        reference = getattr(security_data, "correlation_id", None) or new_auth_reference()
        error_code, stage = classify_sso_exception(e)
        record_sso_diagnostic(
            db_log,
            reference=reference,
            provider=provider_type,
            error_code=error_code,
            stage=stage,
            user_agent=user_agent,
            ip_address=client_ip,
        )
        return RedirectResponse(
            url=build_sso_failure_url("sso_login_failed", reference),
            status_code=302,
        )
    except Exception as e:
        from app.auth.diagnostics import (
            build_sso_failure_url,
            classify_sso_exception,
            new_auth_reference,
            record_sso_diagnostic,
        )
        reference = getattr(security_data, "correlation_id", None) or new_auth_reference()
        error_code, stage = classify_sso_exception(e)
        record_sso_diagnostic(
            db_log,
            reference=reference,
            provider=provider_type,
            error_code=error_code,
            stage=stage,
            user_agent=user_agent,
            ip_address=client_ip,
        )
        logger.exception("SSO login exception: %s", str(e))
        return RedirectResponse(
            url=build_sso_failure_url("sso_login_failed", reference),
            status_code=302,
        )



# -------------------
# Complete SSO Login (internal helper)
# -------------------
async def _complete_sso_login(user, request, response, db, db_log, is_new_user=False, flow_context: dict[str, Any] | None = None):
    """Complete the SSO login process by issuing tokens.
    
    Security: Instead of passing access_token in URL (which gets logged), we use a
    one-time authorization code that the frontend exchanges for tokens via a secure endpoint.
    """
    from fastapi.responses import RedirectResponse
    flow_context = flow_context or {"account_mode": "primary", "replace_slot": None, "return_url": ""}
    
    # Clear pending SSO token
    _clear_pending_sso_token(user.id, db)
    _clear_one_time_browser_cookie(response, "sso_login_token")
    
    from app.auth.native import create_native_exchange_callback, is_native_flow

    if is_native_flow(flow_context, kind="sso"):
        native_callback = create_native_exchange_callback(
            db,
            kind="sso",
            provider=str(flow_context.get("native_provider") or "sso"),
            user_id=user.id,
            flow_context=flow_context,
        )
        redirect_response = RedirectResponse(url=native_callback, status_code=302)
        _copy_response_set_cookies(response, redirect_response)
        _clear_one_time_browser_cookie(redirect_response, "sso_login_token")
        clear_flow_context_cookie(redirect_response, db, request, cookie_name=SSO_FLOW_COOKIE)
        return redirect_response
    
    # Generate a one-time authorization code instead of passing token in URL
    # This code can be exchanged once for the access token via a secure POST endpoint
    auth_code = secrets.token_urlsafe(32)
    auth_code_hash = _hash_pending_action_value(
        auth_code, db, purpose="sso_auth_code"
    )
    auth_code_expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    
    # Store only the hash of the auth code and its indexed owner mapping.
    _commit_pending_auth_action(
        db,
        user_id=user.id,
        purpose=_PENDING_ACTION_SSO_AUTH_CODE,
        token_hash=auth_code_hash,
        expires_at=auth_code_expires,
        settings_updates={
            "sso_login": {
                "pending_auth_code": auth_code_hash,
                "pending_auth_code_expires": auth_code_expires.isoformat(),
            },
        },
    )
    
    # Redirect without embedding the auth code in the URL.
    redirect_url = _build_login_url(
        account_mode=flow_context.get("account_mode", "primary"),
        return_url=_sanitize_return_url(flow_context.get("return_url")),
        query_params={"sso_success": "true"},
    )
    
    redirect_response = RedirectResponse(url=redirect_url, status_code=302)
    _copy_response_set_cookies(response, redirect_response)
    _clear_one_time_browser_cookie(redirect_response, "sso_login_token")

    # Bind the auth code redemption to this browser via an HttpOnly cookie (5 min lifetime)
    _set_one_time_browser_cookie_strict(redirect_response, "sso_auth_code", auth_code, db, request, max_age=300)

    return redirect_response


async def confirm_pending_sso_terms_signup(
    *,
    request,
    response,
    db,
    db_log,
    terms_payload: Any,
):
    """Finish a pending SSO/JIT signup after terms acceptance."""
    from app.admin.settings.utils import create_admin_notification
    from app.auth.diagnostics import build_sso_failure_url
    from app.auth.enterprise_sso import EnterpriseSSOProviderFactory
    from app.users.init import update_user_settings
    from app.users.models import create_user
    from fastapi.responses import RedirectResponse

    pending = _read_pending_federated_signup_cookie(
        request,
        db,
        cookie_name=SSO_PENDING_SIGNUP_COOKIE,
    )
    if not pending or pending.get("kind") != "sso":
        return _json_error_clearing_pending_federated_signup_cookie(
            detail="pending_sso_signup_missing",
            status_code=400,
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )

    provider_type = str(pending.get("provider_type") or "").strip().lower()
    user_info = pending.get("user_info") if isinstance(pending.get("user_info"), dict) else {}
    flow_context = pending.get("flow_context") if isinstance(pending.get("flow_context"), dict) else {}
    flow_context = {
        **flow_context,
        **_extract_terms_acceptance(terms_payload),
    }

    user_agent = request.headers.get("User-Agent", "Unknown Device")
    client_ip = _client_ip_from_request(request, db)
    email = str(user_info.get("email") or "").lower().strip()
    sso_provider = EnterpriseSSOProviderFactory.get_provider(provider_type, db)

    try:
        terms_acceptance = _require_terms_ready_for_self_service_signup(db, flow_context)
    except TermsOfServiceSignupError as exc:
        return _json_error_clearing_pending_federated_signup_cookie(
            detail=exc.code,
            status_code=exc.status_code,
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )

    if not email:
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("no_email", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )
    if not _has_positive_verified_email_signal(user_info.get("email_verified", None)):
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("email_not_verified", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )
    if not sso_provider.validate_domain(email):
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("domain_not_allowed", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )
    if not _is_new_account_registration_enabled(db) or not sso_provider.allows_jit_provisioning():
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("signup_not_allowed", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )
    if not _has_required_sso_provider_identity(provider_type, user_info):
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("sso_login_failed", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )
    if _find_user_by_linked_provider_subject(
        db,
        section="sso_login",
        provider=provider_type,
        user_info=user_info,
    ) is not None or user_exists_by_email(db, email):
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("sso_login_failed", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )

    first_name = user_info.get("given_name", user_info.get("name", "").split()[0] if user_info.get("name") else "")
    last_name = user_info.get("family_name", " ".join(user_info.get("name", "").split()[1:]) if user_info.get("name") else "")
    temp_password = secrets.token_urlsafe(32)
    hashed_temp_password = hash_password(temp_password)
    default_role = _resolve_sso_role(sso_provider, user_info)
    default_group = _resolve_sso_group_id(db, sso_provider, user_info)

    new_user = create_user(
        db=db,
        email=email,
        hashed_password=hashed_temp_password,
        first_name=first_name or "User",
        last_name=last_name or "",
        role=default_role,
        group_id=default_group,
    )
    if not new_user:
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("user_creation_failed", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )

    _record_terms_of_service_acceptance(
        db=db,
        db_log=db_log,
        request=request,
        user_id=new_user.id,
        revision=int(terms_acceptance["revision"]),
        accepted_at=str(terms_acceptance["accepted_at"]),
        source=f"sso_signup:{provider_type}",
    )
    update_user_settings(new_user.id, "sso_login", "needs_password_setup", False, db)
    update_user_settings(new_user.id, "sso_login", f"{provider_type}_linked", True, db)
    if not _validate_or_store_sso_provider_identity(new_user.id, provider_type, user_info, db):
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("sso_login_failed", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )
    mark_user_externally_managed(db, new_user, provider_type)

    create_authentication_log(
        db_log,
        "sso_signup",
        "info",
        f"New user created via {provider_type} SSO: {email}",
        new_user.id,
        user_agent,
        client_ip,
    )

    _clear_pending_federated_signup_cookie(response, cookie_name=SSO_PENDING_SIGNUP_COOKIE)
    if new_user.role == "pending":
        try:
            create_admin_notification(
                db,
                "user_pending",
                f"New pending user signup via {provider_type} SSO: {email}",
                details={"user_id": new_user.id, "email": email, "provider": provider_type},
                user_id=new_user.id,
                notification_type="info",
            )
        except Exception:
            pass
        return _redirect_clearing_pending_federated_signup_cookie(
            build_sso_failure_url("account_pending", None),
            cookie_name=SSO_PENDING_SIGNUP_COOKIE,
        )

    eligibility_redirect = _sso_login_eligibility_redirect_response(
        user=new_user,
        email=email,
        db=db,
        db_log=db_log,
        user_agent=user_agent,
        client_ip=client_ip,
    )
    if eligibility_redirect is not None:
        eligibility_redirect.delete_cookie(SSO_PENDING_SIGNUP_COOKIE)
        return eligibility_redirect

    ensure_provider_alignment(new_user.id, db)
    twofa_result = evaluate_login_2fa(
        new_user,
        otp_code=None,
        otp_action=None,
        otp_destination=None,
        db=db,
        client_ip=client_ip,
    )
    if twofa_result and twofa_result.get("status") in {"otp_setup", "otp_required_already_setup"}:
        sso_token, sso_token_expires = _set_pending_sso_token(
            new_user.id,
            provider_type,
            db,
            allow_setup_material=twofa_result.get("status") == "otp_setup",
        )
        mode = "setup" if twofa_result.get("status") == "otp_setup" else "verify"
        query_params = {
            "sso_2fa": mode,
            "provider": provider_type,
        }
        for key in ("delivery_hint", "resend_available_in_seconds", "setup_material_available"):
            value = twofa_result.get(key)
            if value is not None:
                query_params[key] = str(value)
        if twofa_result.get("provider"):
            query_params["provider_2fa"] = str(twofa_result.get("provider"))
        redirect_response = RedirectResponse(
            url=_build_login_url(
                account_mode=flow_context.get("account_mode", "primary"),
                return_url=_sanitize_return_url(flow_context.get("return_url")),
                query_params={key: value for key, value in query_params.items() if key != "sso_token"},
            ),
            status_code=302,
        )
        _set_one_time_browser_cookie(
            redirect_response,
            "sso_login_token",
            sso_token,
            db,
            request,
            max_age=max(1, int((sso_token_expires - datetime.now(timezone.utc)).total_seconds())),
        )
        redirect_response.delete_cookie(SSO_PENDING_SIGNUP_COOKIE)
        return redirect_response

    if flow_context.get("native_auth") is True and flow_context.get("native_kind") == "sso":
        result = await _complete_sso_login_api(
            new_user,
            request,
            response,
            db,
            db_log,
            flow_context=flow_context,
        )
    else:
        result = await _complete_sso_login(
            new_user,
            request,
            response,
            db,
            db_log,
            is_new_user=True,
            flow_context=flow_context,
        )
    if hasattr(result, "delete_cookie"):
        result.delete_cookie(SSO_PENDING_SIGNUP_COOKIE)
    return result


def cancel_pending_sso_terms_signup(*, response) -> dict[str, str]:
    """Cancel a pending SSO signup and forget the short-lived provider context."""
    _clear_pending_federated_signup_cookie(response, cookie_name=SSO_PENDING_SIGNUP_COOKIE)
    return {"status": "cancelled"}



# -------------------
# Find User by SSO Token (efficient query)
# -------------------
def _find_user_by_sso_token(db, sso_token: str):
    """Find a user by an indexed, epoch-bound pending SSO token."""
    return _find_user_by_pending_auth_action(
        db,
        purpose=_PENDING_ACTION_SSO_LOGIN,
        path=("sso_login", "pending_sso_token"),
        raw_token=sso_token,
    )


def _clear_social_auth_exchange_state(
    user_id: str,
    db,
    *,
    raw_token: str | None = None,
) -> bool:
    """Clear social auth exchange state for user."""
    return _commit_pending_auth_action_clear(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_SOCIAL_AUTH_CODE,
        expected_token_hash=(
            _hash_pending_action_value(
                raw_token,
                db,
                purpose=_PENDING_ACTION_SOCIAL_AUTH_CODE,
            )
            if raw_token is not None
            else None
        ),
        settings_updates={
            "social_login": {
                "pending_auth_code": "",
                "pending_auth_code_expires": "",
            },
        },
    )


def _clear_sso_auth_exchange_state(
    user_id: str,
    db,
    *,
    raw_token: str | None = None,
) -> bool:
    """Clear SSO auth exchange state for user."""
    return _commit_pending_auth_action_clear(
        db,
        user_id=user_id,
        purpose=_PENDING_ACTION_SSO_AUTH_CODE,
        expected_token_hash=(
            _hash_pending_action_value(
                raw_token,
                db,
                purpose=_PENDING_ACTION_SSO_AUTH_CODE,
            )
            if raw_token is not None
            else None
        ),
        settings_updates={
            "sso_login": {
                "pending_auth_code": "",
                "pending_auth_code_expires": "",
            },
        },
    )


# -------------------
# Complete SSO Login with 2FA
# -------------------
async def complete_sso_login_with_2fa(
    provider_type: str,
    sso_token: str | None,
    otp_code: str | None,
    otp_type: str | None,
    otp_action: str | None,
    otp_destination: str | None,
    request,
    response,
    db,
    db_log,
):
    """Complete SSO login flow after 2FA verification."""
    user_agent = request.headers.get("User-Agent", "Unknown Device")
    client_ip = _client_ip_from_request(request, db)
    flow_context = read_flow_context_cookie(request, db, cookie_name=SSO_FLOW_COOKIE)
    if not sso_token:
        sso_token = request.cookies.get("sso_login_token")
    if not sso_token:
        return {"status": "error", "detail": "Invalid or expired SSO login token"}
    
    # Find user by SSO token using efficient database query
    user = _find_user_by_sso_token(db, sso_token)
    
    if not user:
        _clear_one_time_browser_cookie(response, "sso_login_token")
        return {"status": "error", "detail": "Invalid or expired SSO login token"}

    # Enforce SSO token expiration
    expires_str = get_user_setting_value(
        user.id,
        "sso_login",
        "pending_sso_token_expires",
        db,
        commit=False,
    )
    if not expires_str:
        _clear_pending_sso_token(user.id, db)
        _clear_one_time_browser_cookie(response, "sso_login_token")
        return {"status": "error", "detail": "SSO login session expired. Please try again."}
    try:
        expires_at = datetime.fromisoformat(expires_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            _clear_pending_sso_token(user.id, db)
            _clear_one_time_browser_cookie(response, "sso_login_token")
            return {"status": "error", "detail": "SSO login session expired. Please try again."}
    except Exception:
        _clear_pending_sso_token(user.id, db)
        _clear_one_time_browser_cookie(response, "sso_login_token")
        return {"status": "error", "detail": "SSO login session expired. Please try again."}
    
    # Verify the provider type matches
    pending_provider = get_user_setting_value(
        user.id,
        "sso_login",
        "pending_provider_type",
        db,
        commit=False,
    )
    if pending_provider != provider_type:
        _clear_pending_sso_token(user.id, db)
        _clear_one_time_browser_cookie(response, "sso_login_token")
        return {"status": "error", "detail": "Provider mismatch"}
    
    action = normalize_otp_action(otp_action, otp_type, otp_code)
    result = evaluate_login_2fa(
        user,
        otp_code=otp_code,
        otp_action=action,
        otp_destination=otp_destination,
        db=db,
        client_ip=client_ip,
    )
    if result:
        if result.get("status") == "otp_invalid":
            create_authentication_log(
                db_log, "sso_signin", "warning",
                "OTP code invalid during SSO login",
                user.id, user_agent, client_ip
            )
        elif result.get("status") == "otp_locked":
            create_authentication_log(
                db_log, "sso_signin", "warning",
                "OTP verification locked after repeated failures during SSO login",
                user.id, user_agent, client_ip
            )
        return result
    
    # 2FA verified - complete login
    return await _complete_sso_login_api(
        user,
        request,
        response,
        db,
        db_log,
        flow_context=flow_context,
        twofa_satisfied=True,
        pending_token=sso_token,
    )



# -------------------
# Complete SSO Login API Response
# -------------------
async def _complete_sso_login_api(
    user,
    request,
    response,
    db,
    db_log,
    flow_context: dict[str, Any] | None = None,
    *,
    twofa_satisfied: bool = False,
    pending_token: str | None = None,
):
    """Complete SSO login and return API response (not redirect)."""
    flow_context = flow_context or {"account_mode": "primary", "replace_slot": None, "return_url": ""}
    
    # Clear pending SSO token
    if not _clear_pending_sso_token(
        user.id,
        db,
        raw_token=pending_token,
    ):
        _clear_one_time_browser_cookie(response, "sso_login_token")
        return {
            "status": "error",
            "detail": "Invalid or expired SSO login token",
        }
    _clear_one_time_browser_cookie(response, "sso_login_token")

    from app.auth.native import create_native_exchange_callback, is_native_flow

    if is_native_flow(flow_context, kind="sso"):
        native_callback = create_native_exchange_callback(
            db,
            kind="sso",
            provider=str(flow_context.get("native_provider") or "sso"),
            user_id=user.id,
            flow_context=flow_context,
            twofa_satisfied=twofa_satisfied,
        )
        clear_flow_context_cookie(response, db, request, cookie_name=SSO_FLOW_COOKIE)
        return {
            "status": "success",
            "native_callback_url": native_callback,
        }
    
    issued = _issue_authenticated_session(
        db=db,
        db_log=db_log,
        request=request,
        response=response,
        user=user,
        log_event="sso_signin",
        success_message="SSO login with 2FA successful",
        account_mode=flow_context.get("account_mode", "primary"),
        replace_slot=flow_context.get("replace_slot"),
        twofa_satisfied=twofa_satisfied,
    )
    if not issued.get("session_authenticated"):
        return issued

    clear_flow_context_cookie(response, db, request, cookie_name=SSO_FLOW_COOKIE)
    
    return {
        "status": "success",
        **issued,
    }
