"""File storage usage and quota statistics helpers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.files.models import Files
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.init import get_group_setting_value, get_group_settings
from app.users.models import User


logger = logging.getLogger(__name__)
BYTES_PER_GB = 1024 ** 3
FILE_STORAGE_ADMIN_SORT_FIELDS = frozenset({
    "email",
    "file_count",
    "storage_bytes",
    "latest_file_at",
})


def _coerce_bool_setting(value: Any, *, default: bool = True) -> bool:
    """Normalize group boolean settings that may be stored as strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise HTTPException(status_code=500, detail="File upload permission misconfigured")


def _coerce_file_count_limit(value: Any) -> int | None:
    """Normalize the configured total file count limit.

    Negative values are treated as unlimited because quota enforcement already
    skips the count check for negative limits.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="File upload limit misconfigured") from exc
    return parsed if parsed >= 0 else None


def _coerce_storage_limit_bytes(value: Any) -> int | None:
    """Normalize the configured per-user storage limit into bytes."""
    if value in (None, ""):
        return None
    try:
        parsed_gb = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="File storage limit misconfigured") from exc
    if parsed_gb < 0:
        return None
    return int(parsed_gb * BYTES_PER_GB)


def _usage_percent(used: int, limit: int | None) -> float | None:
    """Return a rounded percentage for finite limits, capped for UI progress bars."""
    if limit is None:
        return None
    if limit <= 0:
        return 100.0 if used > 0 else 0.0
    return round(min((used / limit) * 100, 100.0), 2)


def _get_user_file_setting(db: Session, user_id: str, key_name: str) -> Any:
    """Read one effective file setting for a user, falling back for orphan rows."""
    group_id = db.query(User.group_id).filter(User.id == user_id).scalar()
    if not group_id:
        return DEFAULT_GROUP_SETTINGS["files"][key_name]
    return get_group_setting_value(str(group_id), "files", key_name, db)


def _get_group_file_settings(db: Session, group_id: str | None) -> dict[str, Any]:
    """Read all effective file settings for a group in one settings lookup."""
    if not group_id:
        return dict(DEFAULT_GROUP_SETTINGS["files"])
    try:
        settings = get_group_settings(str(group_id), db)
    except HTTPException:
        logger.exception("Falling back to default file settings for missing or invalid group", extra={"group_id": group_id})
        return dict(DEFAULT_GROUP_SETTINGS["files"])
    file_settings = settings.get("files")
    return dict(file_settings) if isinstance(file_settings, dict) else dict(DEFAULT_GROUP_SETTINGS["files"])


def _safe_coerce_file_setting(
    *,
    key_name: str,
    raw_value: Any,
    coerce,
    context: dict[str, Any],
    hard_fallback: Any,
) -> Any:
    """Coerce one file quota setting, logging and falling back on bad stored values."""
    try:
        return coerce(raw_value)
    except HTTPException:
        logger.exception("Invalid file storage quota setting; using default", extra={**context, "setting": key_name})
    except Exception:
        logger.exception("Unexpected file storage quota setting error; using default", extra={**context, "setting": key_name})

    try:
        return coerce(DEFAULT_GROUP_SETTINGS["files"][key_name])
    except Exception:
        logger.exception("Default file storage quota setting is invalid; using hard fallback", extra={"setting": key_name})
        return hard_fallback


def _resolve_file_storage_quota_from_settings(
    file_settings: dict[str, Any],
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Resolve quota settings from an already-loaded files settings dictionary."""
    uploads_allowed = _safe_coerce_file_setting(
        key_name="allow_file_uploads",
        raw_value=file_settings.get("allow_file_uploads"),
        coerce=lambda value: _coerce_bool_setting(value, default=True),
        context=context,
        hard_fallback=False,
    )
    file_count_limit = _safe_coerce_file_setting(
        key_name="max_files_upload_count",
        raw_value=file_settings.get("max_files_upload_count"),
        coerce=_coerce_file_count_limit,
        context=context,
        hard_fallback=None,
    )
    storage_bytes_limit = _safe_coerce_file_setting(
        key_name="max_user_files_size_gb",
        raw_value=file_settings.get("max_user_files_size_gb"),
        coerce=_coerce_storage_limit_bytes,
        context=context,
        hard_fallback=None,
    )
    return {
        "uploads_allowed": uploads_allowed,
        "file_count_limit": file_count_limit,
        "storage_bytes_limit": storage_bytes_limit,
    }


def resolve_user_file_storage_quota(db: Session, user_id: str) -> dict[str, Any]:
    """Resolve the current file-storage quota settings for a user."""
    return _resolve_file_storage_quota_from_settings(
        {
            "allow_file_uploads": _get_user_file_setting(db, user_id, "allow_file_uploads"),
            "max_files_upload_count": _get_user_file_setting(db, user_id, "max_files_upload_count"),
            "max_user_files_size_gb": _get_user_file_setting(db, user_id, "max_user_files_size_gb"),
        },
        context={"user_id": user_id},
    )


def resolve_group_file_storage_quota(
    db: Session,
    group_id: str | None,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve quota settings for an already-known group, with optional caching."""
    cache_key = str(group_id or "__default__")
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    file_settings = _get_group_file_settings(db, group_id)
    quota = _resolve_file_storage_quota_from_settings(
        file_settings,
        context={**(context or {}), "group_id": group_id},
    )
    if cache is not None:
        cache[cache_key] = quota
    return quota


def build_file_storage_usage_payload(
    *,
    user_id: str,
    file_count: int,
    storage_bytes: int,
    latest_file_at: datetime | None,
    quota: dict[str, Any],
) -> dict[str, Any]:
    """Combine raw owned-file usage with resolved quota limits."""
    file_count_limit = quota.get("file_count_limit")
    storage_bytes_limit = quota.get("storage_bytes_limit")
    return {
        "user_id": user_id,
        "file_count": int(file_count or 0),
        "storage_bytes": int(storage_bytes or 0),
        "latest_file_at": latest_file_at,
        "uploads_allowed": bool(quota.get("uploads_allowed", True)),
        "file_count_limit": file_count_limit,
        "storage_bytes_limit": storage_bytes_limit,
        "file_count_percent": _usage_percent(int(file_count or 0), file_count_limit),
        "storage_percent": _usage_percent(int(storage_bytes or 0), storage_bytes_limit),
    }


def get_user_file_storage_usage(db: Session, user_id: str) -> dict[str, Any]:
    """Return owned file-storage usage and limits for one user."""
    file_count, storage_bytes, latest_file_at = (
        db.query(
            func.count(Files.id),
            func.coalesce(func.sum(Files.file_size), 0),
            func.max(Files.created_at),
        )
        .filter(Files.user_id == user_id)
        .one()
    )
    quota = resolve_user_file_storage_quota(db, user_id)
    return build_file_storage_usage_payload(
        user_id=user_id,
        file_count=int(file_count or 0),
        storage_bytes=int(storage_bytes or 0),
        latest_file_at=latest_file_at,
        quota=quota,
    )


def get_admin_file_storage_statistics(
    db: Session,
    *,
    limit: int,
    offset: int,
    sort_field: str = "storage_bytes",
    sort_direction: str = "desc",
    search: str | None = None,
) -> dict[str, Any]:
    """Return aggregate file-storage statistics for admin reporting."""
    usage_subquery = (
        db.query(
            Files.user_id.label("user_id"),
            func.count(Files.id).label("file_count"),
            func.coalesce(func.sum(Files.file_size), 0).label("storage_bytes"),
            func.max(Files.created_at).label("latest_file_at"),
        )
        .group_by(Files.user_id)
        .subquery()
    )

    query = db.query(
        usage_subquery.c.user_id,
        usage_subquery.c.file_count,
        usage_subquery.c.storage_bytes,
        usage_subquery.c.latest_file_at,
        User.email,
        User.first_name,
        User.last_name,
        User.group_id,
    ).outerjoin(User, User.id == usage_subquery.c.user_id)

    normalized_search = str(search or "").strip().lower()
    if normalized_search:
        escaped_search = (
            normalized_search
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped_search}%"
        query = query.filter(
            or_(
                func.lower(usage_subquery.c.user_id).like(pattern, escape="\\"),
                func.lower(func.coalesce(User.email, "")).like(pattern, escape="\\"),
            )
        )

    total_users = query.order_by(None).count()
    totals = db.query(
        func.count(Files.id),
        func.coalesce(func.sum(Files.file_size), 0),
        func.count(func.distinct(Files.user_id)),
    ).one()

    sort_field = sort_field if sort_field in FILE_STORAGE_ADMIN_SORT_FIELDS else "storage_bytes"
    descending = str(sort_direction or "").lower() != "asc"
    sort_columns = {
        "email": func.lower(func.coalesce(User.email, usage_subquery.c.user_id)),
        "file_count": usage_subquery.c.file_count,
        "storage_bytes": usage_subquery.c.storage_bytes,
        "latest_file_at": usage_subquery.c.latest_file_at,
    }
    order_column = sort_columns[sort_field]
    order_expr = order_column.desc() if descending else order_column.asc()
    rows = (
        query.order_by(order_expr, func.lower(func.coalesce(User.email, usage_subquery.c.user_id)).asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items: list[dict[str, Any]] = []
    quota_cache: dict[str, dict[str, Any]] = {}
    for row in rows:
        quota = resolve_group_file_storage_quota(
            db,
            row.group_id,
            cache=quota_cache,
            context={"user_id": str(row.user_id)},
        )
        usage_payload = build_file_storage_usage_payload(
            user_id=str(row.user_id),
            file_count=int(row.file_count or 0),
            storage_bytes=int(row.storage_bytes or 0),
            latest_file_at=row.latest_file_at,
            quota=quota,
        )
        usage_payload.update({
            "email": row.email,
            "first_name": row.first_name,
            "last_name": row.last_name,
        })
        items.append(usage_payload)

    return {
        "summary": {
            "total_files": int(totals[0] or 0),
            "total_storage_bytes": int(totals[1] or 0),
            "users_with_files": int(totals[2] or 0),
        },
        "items": items,
        "total": int(total_users or 0),
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < int(total_users or 0),
        "sort_field": sort_field,
        "sort_direction": "desc" if descending else "asc",
    }
