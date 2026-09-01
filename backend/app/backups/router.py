from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.backups.errors import (
    BackupArchivePolicyError,
    classify_backup_destination_test_error,
)
from app.backups.models import (
    BACKUP_FIELD_UNSET,
    create_backup_destination,
    create_backup_job,
    create_backup_schedule,
    decrypt_destination_config,
    delete_backup_destination,
    delete_backup_schedule,
    get_backup_schedule,
    list_backup_artifacts_by_job_ids,
    list_backup_destinations,
    list_backup_schedules,
    paginate_backup_jobs,
    redact_destination_config,
    update_backup_destination,
    update_backup_schedule,
)
from app.backups.schemas import (
    BackupCreateRequest,
    BackupDestinationCreate,
    BackupDestinationResponse,
    BackupDestinationUpdate,
    BackupJobPageResponse,
    BackupJobResponse,
    BackupRuntimeCapabilities,
    BackupScheduleCreate,
    BackupScheduleResponse,
    BackupScheduleUpdate,
    OperationStatus,
)
from app.backups.service import (
    backup_archive_download_filename,
    build_backup_job_response,
    create_scheduled_backup_job,
    delete_backup_job_and_artifacts,
    enqueue_backup_job,
    ensure_backup_archive_policy,
    get_backup_runtime_capabilities,
    materialize_backup_job_artifact,
    sanitize_backup_response_metadata,
    test_backup_destination,
    verify_backup_job,
)
from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip


backups_router = APIRouter(prefix="/api/v1/admin/backups", tags=["backups"])
logger = logging.getLogger(__name__)


def _backup_error_detail(exc: Exception) -> str:
    return sanitize_backup_response_metadata({"detail": str(exc)}).get("detail") or "Backup operation failed"


def _audit(
    *,
    db_log: Session,
    request: Request,
    admin_user_id: str,
    action: str,
    details: dict,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=admin_user_id,
        action=action,
        details=details,
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )


@backups_router.post("/create", response_model=BackupJobResponse)
def create_backup_route(
    payload: BackupCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    try:
        ensure_backup_archive_policy(payload.encryption_enabled)
    except BackupArchivePolicyError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code},
        ) from exc

    job = create_backup_job(
        db,
        trigger_type="manual",
        destination_id=payload.destination_id,
        requested_by_user_id=admin_user.id,
        options={"encryption_enabled": payload.encryption_enabled},
    )
    enqueue_backup_job(job.id)

    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_CREATE",
        details={
            "backup_job_id": job.id,
            "destination_id": payload.destination_id,
            "encryption_enabled": payload.encryption_enabled,
        },
    )

    return BackupJobResponse(**build_backup_job_response(db, job))


@backups_router.get("/capabilities", response_model=BackupRuntimeCapabilities)
def backup_capabilities_route(
    admin_user=Depends(verified_admin),
):
    return get_backup_runtime_capabilities()


@backups_router.get("/jobs", response_model=BackupJobPageResponse)
def list_backup_jobs_route(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    status: str | None = Query(default=None, max_length=20),
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
):
    """Return one server-filtered page of backup history."""
    jobs, total, total_pages, resolved_page = paginate_backup_jobs(
        db,
        page=page,
        page_size=page_size,
        status=status,
    )
    artifacts_by_job = list_backup_artifacts_by_job_ids(db, [job.id for job in jobs])
    return BackupJobPageResponse(
        items=[
            BackupJobResponse(
                **build_backup_job_response(
                    db,
                    job,
                    artifacts=artifacts_by_job.get(job.id, []),
                )
            )
            for job in jobs
        ],
        page=resolved_page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@backups_router.post("/jobs/{job_id}/verify", response_model=OperationStatus)
def verify_backup_job_route(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    try:
        result = verify_backup_job(db, job_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_backup_error_detail(exc)) from exc

    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_VERIFY",
        details={"backup_job_id": job_id, "ok": bool(result.get("ok"))},
    )

    return OperationStatus(status="success" if result.get("ok") else "failed", details=result)


@backups_router.delete("/jobs/{job_id}", response_model=OperationStatus)
def delete_backup_job_route(
    job_id: str,
    request: Request,
    delete_remote: bool = Query(default=False),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    try:
        result = delete_backup_job_and_artifacts(db, job_id, delete_remote=delete_remote)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_backup_error_detail(exc)) from exc

    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_DELETE",
        details={"backup_job_id": job_id, "delete_remote": delete_remote},
    )

    return OperationStatus(status="success", details=sanitize_backup_response_metadata(result))


def _materialize_backup_download_response(db: Session, job_id: str) -> tuple[FileResponse, str]:
    """Build the shared file response used by download preflight and transfer."""
    try:
        local_path, artifact_id = materialize_backup_job_artifact(db, job_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_backup_error_detail(exc)) from exc

    response = FileResponse(
        path=str(local_path),
        media_type="application/octet-stream",
        filename=backup_archive_download_filename(job_id, local_path),
        headers={
            # Backup archives can contain the entire application state. Keep
            # them out of browser/proxy caches and prevent content sniffing.
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
        # Supplying the stat result lets FileResponse send Content-Length with
        # its initial headers, so native download UIs can show determinate
        # progress from the first streamed bytes.
        stat_result=local_path.stat(),
    )
    return response, artifact_id


@backups_router.head("/jobs/{job_id}/download")
def prepare_backup_job_download_route(
    job_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
):
    """Authenticate and materialize an artifact before native navigation.

    The frontend can inspect this response for authentication or storage
    failures without buffering the archive. Returning the same headers as the
    GET also gives clients the final filename and size during preflight.
    """
    file_response, _artifact_id = _materialize_backup_download_response(db, job_id)
    return Response(
        status_code=200,
        headers=dict(file_response.headers),
        media_type="application/octet-stream",
    )


@backups_router.get("/jobs/{job_id}/download")
def download_backup_job_route(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    file_response, artifact_id = _materialize_backup_download_response(db, job_id)

    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_DOWNLOAD",
        details={"backup_job_id": job_id, "artifact_id": artifact_id},
    )

    return file_response


@backups_router.get("/destinations", response_model=list[BackupDestinationResponse])
def list_destinations_route(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
):
    rows = list_backup_destinations(db)
    payload: list[BackupDestinationResponse] = []
    for row in rows:
        config = redact_destination_config(decrypt_destination_config(row.config_encrypted))
        payload.append(
            BackupDestinationResponse(
                id=row.id,
                name=row.name,
                provider=row.provider,
                config=config,
                enabled=row.enabled,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return payload


@backups_router.post("/destinations", response_model=BackupDestinationResponse)
def create_destination_route(
    payload: BackupDestinationCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    row = create_backup_destination(
        db,
        name=payload.name,
        provider=payload.provider,
        config=payload.config,
        enabled=payload.enabled,
    )
    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_DESTINATION_CREATE",
        details={"destination_id": row.id, "provider": row.provider},
    )
    return BackupDestinationResponse(
        id=row.id,
        name=row.name,
        provider=row.provider,
        config=redact_destination_config(decrypt_destination_config(row.config_encrypted)),
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@backups_router.put("/destinations/{destination_id}", response_model=BackupDestinationResponse)
def update_destination_route(
    destination_id: str,
    payload: BackupDestinationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    row = update_backup_destination(
        db,
        destination_id=destination_id,
        name=payload.name,
        provider=payload.provider,
        config=payload.config,
        enabled=payload.enabled,
    )
    config = redact_destination_config(decrypt_destination_config(row.config_encrypted))
    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_DESTINATION_UPDATE",
        details={"destination_id": destination_id, "provider": row.provider},
    )
    return BackupDestinationResponse(
        id=row.id,
        name=row.name,
        provider=row.provider,
        config=config,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@backups_router.delete("/destinations/{destination_id}", response_model=OperationStatus)
def delete_destination_route(
    destination_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    delete_backup_destination(db, destination_id)
    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_DESTINATION_DELETE",
        details={"destination_id": destination_id},
    )
    return OperationStatus(status="success")


@backups_router.post("/destinations/{destination_id}/test", response_model=OperationStatus)
def test_destination_route(
    destination_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    try:
        result = test_backup_destination(db, destination_id)
    except Exception as exc:  # noqa: BLE001
        error_code = classify_backup_destination_test_error(exc)
        _audit(
            db_log=db_log,
            request=request,
            admin_user_id=admin_user.id,
            action="BACKUP_DESTINATION_TEST",
            details={
                "destination_id": destination_id,
                "status": "error",
                "error_code": error_code,
            },
        )
        # A destination test is an operation whose result can legitimately be
        # a failed connection. Return a structured, user-safe result rather
        # than leaking a provider exception through an HTTP error string.
        return OperationStatus(
            status="error",
            details={"error_code": error_code},
        )

    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_DESTINATION_TEST",
        details={"destination_id": destination_id, "status": result.get("status")},
    )

    # Provider test adapters return implementation details such as internal
    # URLs and temporary probe paths. The admin UI only needs the outcome, so
    # keep those diagnostics server-side instead of exposing them to clients.
    return OperationStatus(status="success")


@backups_router.get("/schedules", response_model=list[BackupScheduleResponse])
def list_schedules_route(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
):
    rows = list_backup_schedules(db)
    return [BackupScheduleResponse.model_validate(row) for row in rows]


@backups_router.post("/schedules", response_model=BackupScheduleResponse)
def create_schedule_route(
    payload: BackupScheduleCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    row = create_backup_schedule(
        db,
        name=payload.name,
        enabled=payload.enabled,
        timezone_name=payload.timezone,
        frequency=payload.frequency,
        minute=payload.minute,
        hour=payload.hour,
        days_of_week=payload.days_of_week,
        retention_count=payload.retention_count,
        retention_days=payload.retention_days,
        destination_id=payload.destination_id,
    )
    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_SCHEDULE_CREATE",
        details={"schedule_id": row.id},
    )
    return BackupScheduleResponse.model_validate(row)


@backups_router.put("/schedules/{schedule_id}", response_model=BackupScheduleResponse)
def update_schedule_route(
    schedule_id: str,
    payload: BackupScheduleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    provided_fields = payload.model_fields_set
    row = update_backup_schedule(
        db,
        schedule_id=schedule_id,
        name=payload.name,
        enabled=payload.enabled,
        timezone_name=payload.timezone,
        frequency=payload.frequency,
        minute=payload.minute,
        hour=payload.hour,
        days_of_week=payload.days_of_week,
        retention_count=payload.retention_count if "retention_count" in provided_fields else BACKUP_FIELD_UNSET,
        retention_days=payload.retention_days if "retention_days" in provided_fields else BACKUP_FIELD_UNSET,
        destination_id=payload.destination_id if "destination_id" in provided_fields else BACKUP_FIELD_UNSET,
    )
    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_SCHEDULE_UPDATE",
        details={"schedule_id": schedule_id},
    )
    return BackupScheduleResponse.model_validate(row)


@backups_router.delete("/schedules/{schedule_id}", response_model=OperationStatus)
def delete_schedule_route(
    schedule_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    delete_backup_schedule(db, schedule_id)
    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_SCHEDULE_DELETE",
        details={"schedule_id": schedule_id},
    )
    return OperationStatus(status="success")


@backups_router.post("/schedules/{schedule_id}/run-now", response_model=BackupJobResponse)
def run_schedule_now_route(
    schedule_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    schedule = get_backup_schedule(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Backup schedule not found")

    job = create_scheduled_backup_job(db, schedule)
    enqueue_backup_job(job.id)

    _audit(
        db_log=db_log,
        request=request,
        admin_user_id=admin_user.id,
        action="BACKUP_SCHEDULE_RUN_NOW",
        details={"schedule_id": schedule_id, "backup_job_id": job.id},
    )

    return BackupJobResponse(**build_backup_job_response(db, job))
