"""Retention policy helpers for group-provisioned temporary accounts.

Temporary-account expiry is an authorization boundary: authentication code
blocks the account as soon as ``temporary_expires_at`` passes.  This module
handles the separate data-lifecycle decision by marking an account as deleted
and scheduling the existing comprehensive user-erasure worker when required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status

from app.settings.defaults import DEFAULT_SETTINGS


TEMPORARY_ACCOUNT_DELETION_MODES = {
    "delete_instantly",
    "delete_after_days",
    "retain",
}


def _as_utc(value: datetime | None) -> datetime:
    """Return an aware UTC timestamp, using the current time when omitted."""

    normalized = value or datetime.now(timezone.utc)
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc)


def _configured_value(db, key: str) -> Any:
    """Read a temporary-account retention setting with a stable fallback."""

    # Import lazily to keep this policy module lightweight for workers and
    # isolated tests; settings utilities initialize encryption-aware helpers.
    from app.settings.utils import get_value_by_page_and_key

    try:
        value = get_value_by_page_and_key("users", key, db)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        value = None
    if value is not None:
        return value
    return DEFAULT_SETTINGS.get("users", {}).get(key)


def get_temporary_account_retention_policy(
    db,
    *,
    lifecycle_at: datetime | None = None,
) -> dict[str, Any]:
    """Resolve when an expired or revoked temporary account should be erased."""

    anchor = _as_utc(lifecycle_at)
    mode = str(
        _configured_value(db, "temporary_account_deletion_mode")
        or "delete_after_days"
    ).strip().lower()
    if mode not in TEMPORARY_ACCOUNT_DELETION_MODES:
        mode = "delete_after_days"

    raw_days = _configured_value(db, "temporary_account_retention_days")
    try:
        retention_days = max(0, int(raw_days))
    except (TypeError, ValueError):
        retention_days = 30

    if mode == "retain":
        scheduled_for = None
        effective_days = None
    elif mode == "delete_instantly":
        scheduled_for = anchor
        effective_days = 0
    else:
        scheduled_for = anchor + timedelta(days=retention_days)
        effective_days = retention_days

    return {
        "mode": mode,
        "retention_days": effective_days,
        "lifecycle_at": anchor,
        "purge_scheduled_at": scheduled_for,
    }


def mark_temporary_account_for_retention(
    user,
    db,
    *,
    lifecycle_at: datetime | None = None,
) -> dict[str, Any]:
    """Mark one temporary user for retention without committing the session.

    ``deleted_at`` is the durable transition marker used to keep periodic
    worker passes idempotent.  ``deletion_scheduled_for`` plugs directly into
    Omlorix's existing scheduled hard-deletion worker.
    """

    if getattr(user, "account_type", "regular") != "temporary":
        raise ValueError("Only temporary accounts can enter temporary-account retention")

    policy = get_temporary_account_retention_policy(db, lifecycle_at=lifecycle_at)
    if getattr(user, "deleted_at", None) is None:
        user.deleted_at = policy["lifecycle_at"]
        user.deletion_scheduled_for = policy["purge_scheduled_at"]
    else:
        # Repeated expiry/revocation work must report the lifecycle that was
        # actually persisted rather than a newly calculated retention window.
        policy["lifecycle_at"] = user.deleted_at
        policy["purge_scheduled_at"] = user.deletion_scheduled_for
    return policy
