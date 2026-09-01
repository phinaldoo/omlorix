from datetime import datetime, timezone
from typing import List, Sequence
import logging
import uuid

from fastapi import HTTPException
from starlette import status
from sqlalchemy import Boolean, Column, DateTime, Index, JSON, String, Text, or_
from sqlalchemy.orm import Session

from app.database import Base
from app.logging.models import send_notification_webhook
from app.users.models import User, get_user


TYPE_CHOICES = {"info", "warning", "error"}
logger = logging.getLogger(__name__)


def _serialize_recipients(values: Sequence[str] | None) -> str | None:
    if not values:
        return None
    normalized = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        trimmed = value.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        normalized.append(trimmed)
    if not normalized:
        return None
    return "|" + "|".join(normalized) + "|"


def _deserialize_recipients(value: str | None) -> List[str]:
    if not value:
        return []
    return [item for item in value.split("|") if item]


class UserNotifications(Base):
    __tablename__ = "user_notifications"
    __table_args__ = (
        Index("ix_user_notifications_category", "category"),
        Index("ix_user_notifications_timestamp", "timestamp"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    everyone = Column(Boolean, nullable=False, default=False)
    user_ids = Column(Text, nullable=True)
    group_ids = Column(Text, nullable=True)
    category = Column(String(64), nullable=False, default="general")
    type = Column(String(16), nullable=False, default="info")
    message = Column(String(255), nullable=False)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    def user_id_list(self) -> List[str]:
        return _deserialize_recipients(self.user_ids)

    def group_id_list(self) -> List[str]:
        return _deserialize_recipients(self.group_ids)


def create_user_notification(
    db: Session,
    *,
    message: str,
    category: str | None = None,
    notification_type: str | None = None,
    everyone: bool = False,
    user_ids: Sequence[str] | None = None,
    group_ids: Sequence[str] | None = None,
    details: dict | None = None,
) -> UserNotifications:
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message is required")

    normalized_category = (category or "general").strip() or "general"
    normalized_type = (notification_type or "info").strip().lower()
    if normalized_type not in TYPE_CHOICES:
        normalized_type = "info"

    serialized_users = _serialize_recipients(user_ids)
    serialized_groups = _serialize_recipients(group_ids)
    if not everyone and not serialized_users and not serialized_groups:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide everyone=True or at least one user_id/group_id",
        )

    notification = UserNotifications(
        everyone=bool(everyone),
        user_ids=serialized_users,
        group_ids=serialized_groups,
        category=normalized_category[:64],
        type=normalized_type,
        message=message.strip()[:255],
        details=details if isinstance(details, dict) else None,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    # Mark targeted users as having new notifications
    try:
        _mark_users_have_new_notifications(db, notification)
    except Exception:
        logger.exception("Failed to mark users as having new notifications for %s", notification.id)

    payload = {
        "id": notification.id,
        "category": notification.category,
        "type": notification.type,
        "message": notification.message,
        "details": notification.details,
        "timestamp": notification.timestamp.isoformat() if notification.timestamp else None,
        "everyone": notification.everyone,
        "user_ids": notification.user_id_list(),
        "group_ids": notification.group_id_list(),
    }
    try:
        send_notification_webhook(payload)
    except Exception:
        # Webhook failures should not block the primary notification persistence path.
        pass

    return notification


def get_user_notifications(
    db: Session,
    *,
    user_id: str,
    group_id: str | None,
    limit: int = 20,
    offset: int = 0,
    category: str | None = None,
) -> tuple[List[UserNotifications], int]:
    """Return paginated notifications and total count."""
    if limit <= 0:
        return [], 0

    filters = [UserNotifications.everyone.is_(True)]
    if user_id:
        filters.append(UserNotifications.user_ids.like(f"%|{user_id}|%"))
    if group_id:
        filters.append(UserNotifications.group_ids.like(f"%|{group_id}|%"))

    query = db.query(UserNotifications).filter(or_(*filters))
    if category:
        query = query.filter(UserNotifications.category == category.strip())

    total = query.count()

    notifications = (
        query.order_by(UserNotifications.timestamp.desc())
        .offset(max(offset, 0))
        .limit(min(limit, 100))
        .all()
    )

    return notifications, total


def _mark_users_have_new_notifications(db: Session, notification: UserNotifications) -> None:
    """Set has_new_notifications=True for all users targeted by this notification."""
    if notification.everyone:
        # For everyone notifications, update all active users
        users = db.query(User).filter(User.is_active.is_(True)).all()
    else:
        filters = []
        user_ids = notification.user_id_list()
        group_ids = notification.group_id_list()
        if user_ids:
            filters.append(User.id.in_(user_ids))
        if group_ids:
            filters.append(User.group_id.in_(group_ids))
        
        if not filters:
            return
        
        users = db.query(User).filter(or_(*filters)).all()
    
    for user in users:
        settings = user.settings if isinstance(user.settings, dict) else {}
        states = settings.get("states", {})
        if not isinstance(states, dict):
            states = {}
        states["has_new_notifications"] = True
        settings["states"] = states
        user.settings = settings
    
    db.commit()


def clear_user_new_notifications_flag(db: Session, user_id: str) -> None:
    """Set has_new_notifications=False for the specified user."""
    user = get_user(db, user_id)
    if not user:
        return
    
    settings = user.settings if isinstance(user.settings, dict) else {}
    states = settings.get("states", {})
    if not isinstance(states, dict):
        states = {}
    states["has_new_notifications"] = False
    settings["states"] = states
    user.settings = settings
    db.commit()


def get_all_user_notifications(
    db: Session,
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[List[UserNotifications], int]:
    """Return all notifications (admin only) with pagination."""
    if limit <= 0:
        return [], 0

    query = db.query(UserNotifications)
    total = query.count()

    notifications = (
        query.order_by(UserNotifications.timestamp.desc())
        .offset(max(offset, 0))
        .limit(min(limit, 100))
        .all()
    )

    return notifications, total


def remove_user_references_from_notifications(db: Session, user_id: str) -> int:
    """Delete or scrub notifications that directly reference a user ID."""
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return 0

    removed = 0
    notifications = (
        db.query(UserNotifications)
        .filter(UserNotifications.user_ids.like(f"%|{normalized_user_id}|%"))
        .all()
    )

    for notification in notifications:
        remaining_user_ids = [
            candidate for candidate in notification.user_id_list() if candidate != normalized_user_id
        ]
        notification.user_ids = _serialize_recipients(remaining_user_ids)
        if not notification.everyone and not notification.user_ids and not notification.group_ids:
            db.delete(notification)
        removed += 1

    return removed


def update_user_notification(
    db: Session,
    *,
    notification_id: str,
    message: str | None = None,
    category: str | None = None,
    notification_type: str | None = None,
    everyone: bool | None = None,
    user_ids: Sequence[str] | None = None,
    group_ids: Sequence[str] | None = None,
    details: dict | None = None,
) -> UserNotifications | None:
    """Update an existing notification."""
    notification = db.query(UserNotifications).filter(
        UserNotifications.id == notification_id
    ).first()

    if not notification:
        return None

    if message is not None:
        notification.message = message.strip()[:255]

    if category is not None:
        notification.category = (category.strip() or "general")[:64]

    if notification_type is not None:
        normalized_type = notification_type.strip().lower()
        if normalized_type in TYPE_CHOICES:
            notification.type = normalized_type

    if everyone is not None:
        notification.everyone = bool(everyone)

    if user_ids is not None:
        notification.user_ids = _serialize_recipients(user_ids)

    if group_ids is not None:
        notification.group_ids = _serialize_recipients(group_ids)

    if details is not None:
        notification.details = details if isinstance(details, dict) else None

    # Validate recipients
    if not notification.everyone and not notification.user_ids and not notification.group_ids:
        from starlette import status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide everyone=True or at least one user_id/group_id",
        )

    db.commit()
    db.refresh(notification)
    return notification


def delete_user_notification(db: Session, *, notification_id: str) -> bool:
    """Delete a notification by ID. Returns True if deleted, False if not found."""
    notification = db.query(UserNotifications).filter(
        UserNotifications.id == notification_id
    ).first()

    if not notification:
        return False

    db.delete(notification)
    db.commit()
    return True
