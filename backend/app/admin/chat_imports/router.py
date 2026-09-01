import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.chats.schemas import (
    AdminOpenWebUIBulkImportRequest,
    AdminOpenWebUIBulkImportResult,
    AdminOpenWebUIChatImportRequest,
    OpenWebUIChatImportResult,
)
from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.users.models import get_user
from app.workers.operations import (
    enqueue_import_job,
    stage_import_json,
    wait_for_operations_result,
)

logger = logging.getLogger(__name__)
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin_router.post("/import/openwebui/chats", response_model=OpenWebUIChatImportResult)
def admin_import_openwebui_chats_route(
    payload: AdminOpenWebUIChatImportRequest,
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Import OpenWebUI chats for a single user."""
    user_id = payload.user_id
    # Validate the target before import work starts. Otherwise a missing user
    # becomes a foreign-key failure reported as a misleading skipped chat.
    get_user(db, user_id)
    chats = payload.chats
    force_archived = payload.force_archived

    staged_name = stage_import_json(
        chats,
        principal_id=admin_user.id,
        import_kind="import_openwebui_single",
    )
    job = enqueue_import_job(
        db,
        kind="import_openwebui_single",
        staged_name=staged_name,
        user_id=admin_user.id,
        options={"target_user_id": user_id, "force_archived": force_archived},
    )
    result = wait_for_operations_result(job)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="IMPORT_OPENWEBUI_CHATS",
        details={
            "target_user_id": user_id,
            "imported_chats": result["imported_chats"],
            "imported_messages": result["imported_messages"],
            "imported_branches": result.get("imported_branches", 0),
            "skipped_chats": result["skipped_chats"],
            "skipped_branches": result.get("skipped_branches", 0),
            "skipped_messages": result.get("skipped_messages", 0),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return result


@admin_router.post(
    "/import/openwebui/chats/bulk", response_model=AdminOpenWebUIBulkImportResult
)
def admin_import_openwebui_chats_bulk_route(
    payload: AdminOpenWebUIBulkImportRequest,
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Bulk-import OpenWebUI chats for all users."""
    users_csv = payload.users_csv
    chats = payload.chats

    staged_name = stage_import_json(
        {"users_csv": users_csv, "chats": chats},
        principal_id=admin_user.id,
        import_kind="import_openwebui_bulk",
    )
    job = enqueue_import_job(
        db,
        kind="import_openwebui_bulk",
        staged_name=staged_name,
        user_id=admin_user.id,
    )
    result = wait_for_operations_result(job)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="IMPORT_OPENWEBUI_CHATS_BULK",
        details={
            "imported_chats": result["imported_chats"],
            "imported_messages": result["imported_messages"],
            "imported_branches": result.get("imported_branches", 0),
            "skipped_chats": result["skipped_chats"],
            "skipped_branches": result.get("skipped_branches", 0),
            "skipped_messages": result.get("skipped_messages", 0),
            "matched_users": result["matched_users"],
            "skipped_users": result["skipped_users"],
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return result
