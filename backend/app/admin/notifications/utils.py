"""Business transformations for administrator notifications."""

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app.admin.notifications.models import iter_admin_notifications
from sqlalchemy.orm import Session

ADMIN_NOTIFICATIONS_EXPORT_BATCH_SIZE = 500
VALID_NOTIFICATION_TYPES = {"info", "warning", "error"}


def normalize_notification_types(values: list[str] | None) -> list[str] | None:
    """Keep only supported case-normalized notification severity filters."""

    if not values:
        return None
    normalized = [
        value.lower() for value in values if value.lower() in VALID_NOTIFICATION_TYPES
    ]
    return normalized or None


def serialize_admin_notification_export_row(notification: Any) -> dict[str, Any]:
    """Convert one notification row into the stable export representation."""

    timestamp = notification.timestamp
    timestamp_iso = None
    if timestamp:
        aware_dt = (
            timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        )
        timestamp_iso = (
            aware_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        )

    return {
        "id": notification.id,
        "user_id": notification.user_id,
        "category": notification.category,
        "type": notification.type,
        "message": notification.message,
        "details": notification.details,
        "timestamp": timestamp_iso,
    }


def iter_admin_notifications_export_json(
    db: Session, total_count: int
) -> Iterable[str]:
    """Stream a versioned notification export envelope without buffering rows."""

    header = {
        "export_type": "admin_notifications",
        "export_version": 1.0,
        "total_count": total_count,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    header_json = json.dumps(header, separators=(",", ":"))
    yield f'{header_json[:-1]},"notifications":['

    first = True
    for notification in iter_admin_notifications(
        db,
        batch_size=ADMIN_NOTIFICATIONS_EXPORT_BATCH_SIZE,
    ):
        if not first:
            yield ","
        first = False
        yield json.dumps(
            serialize_admin_notification_export_row(notification),
            separators=(",", ":"),
            default=str,
        )
    yield "]}"
