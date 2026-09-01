import logging

from app.admin.notifications.models import count_admin_notifications
from app.admin.notifications.schemas import AdminNotification, AdminNotificationPage
from app.admin.notifications.utils import (
    iter_admin_notifications_export_json,
    normalize_notification_types,
)
from app.database import SessionLocal
from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import (
    create_audit_log,
    delete_all_admin_notifications,
    get_audit_request_ip,
    list_admin_notifications_paginated,
)
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin_router.get("/notifications", response_model=AdminNotificationPage)
def admin_list_notifications(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, min_length=1, max_length=64),
    categories: list[str] | None = Query(None, alias="categories"),
    types: list[str] | None = Query(None, alias="types"),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List all admin notifications."""
    types = normalize_notification_types(types)

    items, total, all_categories, all_types = list_admin_notifications_paginated(
        db,
        page=page,
        page_size=page_size,
        category=category,
        categories=categories,
        types=types,
    )
    serialized_items = [AdminNotification.model_validate(item) for item in items]

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_ADMIN_NOTIFICATIONS",
        details={
            "page": page,
            "page_size": page_size,
            "category": category,
            "categories": categories,
            "types": types,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return {
        "items": serialized_items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": page * page_size < total,
        "available_categories": sorted(all_categories),
        "available_types": sorted(all_types),
    }


@admin_router.post("/notifications/export")
def admin_export_notifications(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Export all admin notifications as streamed JSON."""
    total_count = count_admin_notifications(db)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_ADMIN_NOTIFICATIONS",
        details={"count": total_count},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    def _stream_export():
        """Stream the export with a database session scoped to iteration."""
        stream_db = SessionLocal()
        try:
            yield from iter_admin_notifications_export_json(stream_db, total_count)
        finally:
            stream_db.close()

    return StreamingResponse(
        _stream_export(),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="admin-notifications-export.json"'
        },
    )


@admin_router.delete("/notifications")
def admin_delete_all_notifications(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Delete all admin notifications."""
    deleted_count = delete_all_admin_notifications(db)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DELETE_ALL_ADMIN_NOTIFICATIONS",
        details={"deleted_count": deleted_count},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return {
        "success": True,
        "deleted_count": deleted_count,
    }
