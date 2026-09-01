"""Safe audit snapshots for destructive login-session operations."""

from __future__ import annotations

import hashlib
from typing import Any

from app.auth.models import _token_hash


def login_session_audit_fingerprint(authentication: Any) -> str:
    """Return a stable, non-credential reference for one login row."""
    authentication_id = str(getattr(authentication, "id", "") or "")
    digest = hashlib.sha256(f"login:{authentication_id}".encode("utf-8")).hexdigest()
    return f"login_fp_{digest[:12]}"


def _login_is_current(authentication: Any, current_access_token_hash: str | None) -> bool:
    if not current_access_token_hash:
        return False
    return getattr(authentication, "access_token_hash", None) == current_access_token_hash


def _last_active_iso(authentication: Any) -> str | None:
    last_active_at = getattr(authentication, "last_active_at", None)
    if last_active_at is None:
        return None
    isoformat = getattr(last_active_at, "isoformat", None)
    return isoformat() if callable(isoformat) else str(last_active_at)


def _single_login_details(authentication: Any, current_access_token_hash: str | None) -> dict[str, Any]:
    """Serialize only non-credential metadata for a login being revoked."""
    return {
        "login_id": str(getattr(authentication, "id", "") or ""),
        "login_fingerprint": login_session_audit_fingerprint(authentication),
        # Authentication rows store privacy-minimized values at creation time.
        # Do not access either encrypted token column here.
        "device_info": getattr(authentication, "device_info", None),
        "ip_address": getattr(authentication, "ip_address", None),
        "last_active_at": _last_active_iso(authentication),
        "current": _login_is_current(authentication, current_access_token_hash),
    }


def build_login_revocation_audit_details(
    authentications,
    *,
    current_access_token: str | None,
    auth_id: str | None,
) -> dict[str, Any] | None:
    """Describe the exact rows returned by the atomic revocation delete.

    ``None`` means the operation removed no persisted login and
    therefore must not produce a successful-revocation audit event.
    """
    authentications = list(authentications or [])
    current_access_token_hash = (
        _token_hash(current_access_token) if current_access_token else None
    )

    if auth_id:
        target = next(
            (
                authentication
                for authentication in authentications
                if str(getattr(authentication, "id", "")) == str(auth_id)
            ),
            None,
        )
        if target is None:
            return None
        target_details = _single_login_details(target, current_access_token_hash)
        return {
            "revocation_scope": "single_session",
            "target_count": 1,
            "current_login_revoked": bool(target_details["current"]),
            "requested_login_id": str(auth_id),
            "target_login": target_details,
        }

    if not authentications:
        return None
    return {
        "revocation_scope": "all_sessions",
        "target_count": len(authentications),
        "current_login_revoked": any(
            _login_is_current(authentication, current_access_token_hash)
            for authentication in authentications
        ),
    }
