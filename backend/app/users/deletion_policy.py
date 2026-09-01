"""Canonical post-deletion log-retention policy resolution.

Keeping policy parsing outside the deletion workflow lets the administrator
preview endpoint, account-deletion code, and restore reconciliation all apply
the same validated fallback behavior.
"""

from __future__ import annotations

from typing import Any

from app.settings.defaults import DEFAULT_SETTINGS
from app.settings.utils import get_value_by_page_and_key


RETENTION_MODES = frozenset({"delete_instantly", "delete_after_days", "retain"})


def _normalized_mode(value: Any, *, default: str) -> str:
    """Return a supported retention mode or the supplied safe default."""

    normalized = str(value or "").strip().lower()
    return normalized if normalized in RETENTION_MODES else default


def _normalized_days(value: Any, *, default: int, maximum: int = 3650) -> int:
    """Return a bounded retention period with a stable fallback."""

    try:
        return min(max(int(value), 0), maximum)
    except (TypeError, ValueError):
        return min(max(int(default), 0), maximum)


def get_auth_log_user_deletion_retention_policy(db) -> dict[str, Any]:
    """Resolve the effective authentication-log policy after account deletion."""

    defaults = DEFAULT_SETTINGS.get("security", {})
    default_mode = _normalized_mode(
        defaults.get("auth_logs_retention_after_user_delete_mode"),
        default="delete_after_days",
    )
    mode = _normalized_mode(
        get_value_by_page_and_key(
            "security", "auth_logs_retention_after_user_delete_mode", db
        ),
        default=default_mode,
    )
    if mode == "retain":
        return {"mode": mode, "retention_days": None, "delete_immediately": False}
    if mode == "delete_instantly":
        return {"mode": mode, "retention_days": None, "delete_immediately": True}

    days = _normalized_days(
        get_value_by_page_and_key(
            "security", "auth_logs_retention_delete_after_days", db
        ),
        default=int(defaults.get("auth_logs_retention_delete_after_days", 30)),
    )
    return {
        "mode": mode,
        "retention_days": days,
        "delete_immediately": days <= 0,
    }


def get_audit_log_user_deletion_retention_policy(db) -> dict[str, Any]:
    """Resolve the coupled audit-log/admin-notification deletion policy."""

    defaults = DEFAULT_SETTINGS.get("security", {})
    default_mode = _normalized_mode(
        defaults.get("audit_logs_retention_after_user_delete_mode"),
        default="delete_after_days",
    )
    mode = _normalized_mode(
        get_value_by_page_and_key(
            "security", "audit_logs_retention_after_user_delete_mode", db
        ),
        default=default_mode,
    )
    if mode == "retain":
        return {"mode": mode, "retention_days": None, "delete_immediately": False}
    if mode == "delete_instantly":
        return {"mode": mode, "retention_days": None, "delete_immediately": True}

    days = _normalized_days(
        get_value_by_page_and_key(
            "security", "audit_logs_retention_delete_after_days", db
        ),
        default=int(defaults.get("audit_logs_retention_delete_after_days", 30)),
    )
    return {
        "mode": mode,
        "retention_days": days,
        "delete_immediately": days <= 0,
    }
