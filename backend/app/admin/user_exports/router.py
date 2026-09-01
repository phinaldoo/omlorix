import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.admin.user_exports.jobs.utils import (
    create_and_enqueue_admin_user_export_job,
    delete_admin_user_export_job_artifact,
    get_admin_user_export_job_response,
    list_admin_user_export_job_responses,
    materialize_admin_user_export_job,
)
from app.admin.user_exports.schemas import (
    AdminUserExportJobCreateRequest,
    AdminUserExportJobResponse,
)
from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.users.models import get_user
from app.utils.schemas import OperationResult


logger = logging.getLogger(__name__)
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _build_admin_user_export_download_response(path, filename):
    """Build the canonical response for an archive download."""
    return FileResponse(
        path=str(path),
        media_type="application/zip",
        filename=filename,
        headers={
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
        stat_result=path.stat(),
    )


@admin_router.post("/users/export/jobs", response_model=AdminUserExportJobResponse)
def create_admin_user_export_job_route(
    request: Request,
    payload: AdminUserExportJobCreateRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Queue a canonical all-users or selected-users ZIP export job."""
    selected_user_ids = list(dict.fromkeys(payload.user_ids))
    for user_id in selected_user_ids:
        get_user(db, user_id)
    job = create_and_enqueue_admin_user_export_job(
        db,
        requested_by_user_id=admin_user.id,
        reason=payload.reason,
        user_ids=selected_user_ids,
    )
    response = get_admin_user_export_job_response(db, job.id)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_USERS_ADMIN_JOB_QUEUED",
        details={
            "export_job_id": job.id,
            "scope": "selected" if selected_user_ids else "all",
            "selected_user_count": len(selected_user_ids),
            "reason": payload.reason,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return AdminUserExportJobResponse(**response)


@admin_router.get("/users/export/jobs", response_model=list[AdminUserExportJobResponse])
def list_admin_user_export_jobs_route(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
):
    """List recent all-users ZIP export jobs."""
    return [
        AdminUserExportJobResponse(**job)
        for job in list_admin_user_export_job_responses(db, limit=limit)
    ]


@admin_router.get("/users/export/jobs/{job_id}/download")
def download_admin_user_export_job_route(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Download the completed ZIP artifact for an all-users export job."""
    path, filename = materialize_admin_user_export_job(db, job_id)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_USERS_ADMIN_JOB_DOWNLOAD",
        details={"export_job_id": job_id, "filename": filename},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return _build_admin_user_export_download_response(path, filename)


@admin_router.delete("/users/export/jobs/{job_id}", response_model=OperationResult)
def delete_admin_user_export_job_route(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Delete the ZIP artifact for an all-users export job."""
    delete_admin_user_export_job_artifact(db, job_id)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_USERS_ADMIN_JOB_DELETE",
        details={"export_job_id": job_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return {"status": "success", "detail": "User export job deleted."}
