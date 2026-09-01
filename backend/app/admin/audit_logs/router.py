"""Read-only administrator API for the general immutable audit store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.admin.audit_logs.models import (
    AuditLogFilters,
    count_audit_logs_capped,
    get_audit_log,
    iter_audit_logs,
    list_audit_logs,
)
from app.admin.audit_logs.schemas import (
    AuditLogDetail,
    AuditLogExportRequest,
    AuditLogPage,
)
from app.admin.audit_logs.utils import (
    AUDIT_LOG_EXPORT_BATCH_SIZE,
    AUDIT_LOG_EXPORT_MAX_ROWS,
    decode_audit_cursor,
    encode_audit_cursor,
    iter_audit_log_export_json,
    serialize_audit_log_item,
)
from app.database import AuditSessionLocal
from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.users.deletion_policy import get_audit_log_user_deletion_retention_policy
from app.utils.cache_headers import NO_STORE_HEADERS, apply_no_store_headers
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session


admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
AUDIT_LOG_DEFAULT_WINDOW_DAYS = 7
AUDIT_LOG_BROWSE_MAX_WINDOW_DAYS = 366
AUDIT_LOG_EXPORT_MAX_WINDOW_DAYS = 31


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _retention_summary(db: Session) -> dict:
    policy = get_audit_log_user_deletion_retention_policy(db)
    return {
        "global_cleanup_enabled": False,
        "post_user_deletion_mode": policy["mode"],
        "post_user_deletion_days": policy["retention_days"],
    }


def _validated_window(
    from_timestamp: datetime,
    to_timestamp: datetime,
    *,
    max_days: int,
) -> tuple[datetime, datetime]:
    normalized_from = _utc(from_timestamp)
    normalized_to = _utc(to_timestamp)
    if normalized_from > normalized_to:
        raise HTTPException(
            status_code=422,
            detail={"code": "audit_log_invalid_time_range"},
        )
    if normalized_to - normalized_from > timedelta(days=max_days):
        raise HTTPException(
            status_code=422,
            detail={"code": "audit_log_time_range_too_large", "max_days": max_days},
        )
    return normalized_from, normalized_to


def _filters_audit_details(
    filters: AuditLogFilters, *, limit: int | None = None
) -> dict:
    details = {
        "from": filters.from_timestamp.isoformat(),
        "to": filters.to_timestamp.isoformat(),
        "category": filters.category,
        "action": filters.action,
        "actor_filter_supplied": bool(filters.actor_user_id),
        "reference_filter_supplied": bool(filters.reference),
    }
    if limit is not None:
        details["count"] = limit
    return details


@admin_router.get("/audit-logs", response_model=AuditLogPage)
def list_audit_logs_route(
    request: Request,
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, min_length=1, max_length=512),
    snapshot_at: datetime | None = Query(None),
    from_timestamp: datetime | None = Query(None, alias="from"),
    to_timestamp: datetime | None = Query(None, alias="to"),
    category: str | None = Query(None, min_length=1, max_length=64),
    action: str | None = Query(None, min_length=1, max_length=128),
    actor_user_id: str | None = Query(None, min_length=1, max_length=64),
    reference: str | None = Query(None, min_length=1, max_length=128),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List a stable snapshot of sanitized general audit events."""

    now = datetime.now(timezone.utc)
    normalized_snapshot = _utc(snapshot_at) if snapshot_at else now
    if normalized_snapshot > now + timedelta(minutes=5):
        raise HTTPException(
            status_code=422,
            detail={"code": "audit_log_invalid_snapshot"},
        )
    effective_to = (
        min(_utc(to_timestamp), normalized_snapshot)
        if to_timestamp
        else normalized_snapshot
    )
    effective_from = (
        _utc(from_timestamp)
        if from_timestamp
        else effective_to - timedelta(days=AUDIT_LOG_DEFAULT_WINDOW_DAYS)
    )
    effective_from, effective_to = _validated_window(
        effective_from,
        effective_to,
        max_days=AUDIT_LOG_BROWSE_MAX_WINDOW_DAYS,
    )
    try:
        decoded_cursor = decode_audit_cursor(cursor) if cursor else None
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "audit_log_invalid_cursor"},
        ) from exc

    filters = AuditLogFilters(
        from_timestamp=effective_from,
        to_timestamp=effective_to,
        category=category,
        action=action,
        actor_user_id=actor_user_id,
        reference=reference,
    )
    rows, has_next = list_audit_logs(
        db_log,
        filters=filters,
        limit=limit,
        cursor=decoded_cursor,
    )
    next_cursor = (
        encode_audit_cursor(rows[-1].timestamp, rows[-1].id)
        if has_next and rows
        else None
    )
    items = [serialize_audit_log_item(row) for row in rows]
    retention = _retention_summary(db)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_AUDIT_LOGS",
        details=_filters_audit_details(filters, limit=len(items)),
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    apply_no_store_headers(response)
    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_next": has_next,
        "snapshot_at": normalized_snapshot,
        "from_timestamp": effective_from,
        "to_timestamp": effective_to,
        "retention": retention,
    }


@admin_router.get("/audit-logs/{row_id}", response_model=AuditLogDetail)
def get_audit_log_route(
    request: Request,
    response: Response,
    row_id: str = Path(min_length=1, max_length=64),
    occurred_at: datetime = Query(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Return the bounded allowlisted details for one audit event."""

    normalized_timestamp = _utc(occurred_at)
    row = get_audit_log(db_log, row_id=row_id, timestamp=normalized_timestamp)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "audit_log_not_found"})
    item = serialize_audit_log_item(row, include_details=True)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="VIEW_AUDIT_LOG_DETAIL",
        details={
            "audit_event_id": row.id,
            "event_timestamp": row.timestamp.isoformat(),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    apply_no_store_headers(response)
    return item


@admin_router.post("/audit-logs/export")
def export_audit_logs_route(
    payload: AuditLogExportRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Stream a bounded snapshot of sanitized audit events as JSON."""

    exported_at = datetime.now(timezone.utc)
    effective_from, requested_to = _validated_window(
        payload.from_timestamp,
        payload.to_timestamp,
        max_days=AUDIT_LOG_EXPORT_MAX_WINDOW_DAYS,
    )
    effective_to = min(requested_to, exported_at)
    effective_from, effective_to = _validated_window(
        effective_from,
        effective_to,
        max_days=AUDIT_LOG_EXPORT_MAX_WINDOW_DAYS,
    )
    filters = AuditLogFilters(
        from_timestamp=effective_from,
        to_timestamp=effective_to,
        category=payload.category,
        action=payload.action,
        actor_user_id=payload.actor_user_id,
        reference=payload.reference,
    )
    total_count = count_audit_logs_capped(
        db_log,
        filters=filters,
        cap=AUDIT_LOG_EXPORT_MAX_ROWS,
    )
    if total_count > AUDIT_LOG_EXPORT_MAX_ROWS:
        rejected_details = _filters_audit_details(filters, limit=total_count)
        rejected_details.update(
            {
                "result": "rejected_row_limit",
                "max_count": AUDIT_LOG_EXPORT_MAX_ROWS,
            }
        )
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="EXPORT_AUDIT_LOGS_REJECTED",
            reason=payload.reason,
            details=rejected_details,
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="admin",
        )
        raise HTTPException(
            status_code=413,
            detail={
                "code": "audit_log_export_too_large",
                "max_rows": AUDIT_LOG_EXPORT_MAX_ROWS,
            },
        )
    retention = _retention_summary(db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_AUDIT_LOGS",
        reason=payload.reason,
        details=_filters_audit_details(filters, limit=total_count),
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    def _stream_export():
        stream_db = AuditSessionLocal()
        try:
            rows = iter_audit_logs(
                stream_db,
                filters=filters,
                batch_size=AUDIT_LOG_EXPORT_BATCH_SIZE,
                limit=AUDIT_LOG_EXPORT_MAX_ROWS,
            )
            yield from iter_audit_log_export_json(
                rows,
                total_count=total_count,
                exported_at=exported_at,
                from_timestamp=effective_from,
                to_timestamp=effective_to,
                retention=retention,
            )
        finally:
            stream_db.close()

    return StreamingResponse(
        _stream_export(),
        media_type="application/json",
        headers={
            **NO_STORE_HEADERS,
            "Content-Disposition": 'attachment; filename="audit-logs-export.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )
