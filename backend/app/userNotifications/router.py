from typing import List

from fastapi import APIRouter, Depends, Query, Request, status, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_admin, verified_user
from app.logging.models import create_audit_log, get_audit_request_ip
from app.userNotifications.models import (
    UserNotifications,
    create_user_notification,
    get_user_notifications,
    get_all_user_notifications,
    update_user_notification,
    delete_user_notification,
    clear_user_new_notifications_flag,
)
from app.userNotifications.schemas import (
    UserNotificationCreate,
    UserNotificationResponse,
    UserNotificationsPaginatedResponse,
    UserNotificationUpdate,
)


user_notifications_router = APIRouter(
    prefix="/api/v1/user/notifications", tags=["user_notifications"]
)


def _serialize_notification(notification: UserNotifications) -> UserNotificationResponse:
    return UserNotificationResponse(
        id=notification.id,
        message=notification.message,
        category=notification.category,
        type=notification.type,  # type: ignore[arg-type]
        everyone=notification.everyone,
        user_ids=notification.user_id_list(),
        group_ids=notification.group_id_list(),
        details=notification.details,
        timestamp=notification.timestamp.isoformat() if notification.timestamp else None,
    )


@user_notifications_router.post(
    "",
    response_model=UserNotificationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user_notification_route(
    payload: UserNotificationCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    notification = create_user_notification(
        db=db,
        message=payload.message,
        category=payload.category,
        notification_type=payload.notification_type,
        everyone=payload.everyone,
        user_ids=payload.user_ids,
        group_ids=payload.group_ids,
        details=payload.details,
    )
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="USER_NOTIFICATION_CREATED",
        details={"notification_id": notification.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user_notifications",
    )
    return _serialize_notification(notification)


@user_notifications_router.get(
    "",
    response_model=UserNotificationsPaginatedResponse,
)
def list_user_notifications_route(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, min_length=1, max_length=64),
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    offset = (page - 1) * page_size
    notifications, total = get_user_notifications(
        db=db,
        user_id=user.id,
        group_id=getattr(user, "group_id", None),
        limit=page_size,
        offset=offset,
        category=category,
    )

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    return UserNotificationsPaginatedResponse(
        notifications=[_serialize_notification(n) for n in notifications],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@user_notifications_router.post(
    "/mark-seen",
    status_code=status.HTTP_204_NO_CONTENT,
)
def mark_user_notifications_seen_route(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    clear_user_new_notifications_flag(db, user.id)
    return None


@user_notifications_router.delete(
    "/share-invitations/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_share_invitation_notification_route(
    notification_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Allow users to delete their own share invitation notifications."""
    notification = (
        db.query(UserNotifications)
        .filter(UserNotifications.id == notification_id)
        .first()
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    details = notification.details if isinstance(notification.details, dict) else {}
    if details.get("type") != "share_invitation":
        raise HTTPException(status_code=403, detail="Only share invitations can be deleted")

    targeted_user_ids = notification.user_id_list()
    targeted_group_ids = notification.group_id_list()

    if user.id not in targeted_user_ids:
        raise HTTPException(status_code=403, detail="Not authorized to delete this notification")

    if notification.everyone or targeted_group_ids:
        raise HTTPException(
            status_code=403,
            detail="This invitation is shared with multiple recipients and cannot be deleted individually",
        )

    if len(targeted_user_ids) > 1:
        raise HTTPException(status_code=403, detail="Not authorized to delete this notification")

    db.delete(notification)
    db.commit()
    return None


# =============================================================================
# Admin Endpoints
# =============================================================================

@user_notifications_router.get(
    "/admin/all",
    response_model=UserNotificationsPaginatedResponse,
)
def list_all_notifications_route(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    """List all notifications (admin only)."""
    offset = (page - 1) * page_size
    notifications, total = get_all_user_notifications(
        db=db,
        limit=page_size,
        offset=offset,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    serialized_notifications = [_serialize_notification(n) for n in notifications]
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="LIST_ALL_USER_NOTIFICATIONS",
        details={
            "page": page,
            "page_size": page_size,
            "result_count": len(serialized_notifications),
            "total": total,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user_notifications",
    )
    return UserNotificationsPaginatedResponse(
        notifications=serialized_notifications,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@user_notifications_router.patch(
    "/{notification_id}",
    response_model=UserNotificationResponse,
)
def update_notification_route(
    notification_id: str,
    payload: UserNotificationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    """Update a notification (admin only)."""
    notification = update_user_notification(
        db=db,
        notification_id=notification_id,
        message=payload.message,
        category=payload.category,
        notification_type=payload.notification_type,
        everyone=payload.everyone,
        user_ids=payload.user_ids,
        group_ids=payload.group_ids,
        details=payload.details,
    )
    if not notification:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notification not found")

    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="USER_NOTIFICATION_UPDATED",
        details={"notification_id": notification_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user_notifications",
    )
    return _serialize_notification(notification)


@user_notifications_router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_notification_route(
    notification_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    """Delete a notification (admin only)."""
    deleted = delete_user_notification(db=db, notification_id=notification_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notification not found")

    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="USER_NOTIFICATION_DELETED",
        details={"notification_id": notification_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user_notifications",
    )
    return None
