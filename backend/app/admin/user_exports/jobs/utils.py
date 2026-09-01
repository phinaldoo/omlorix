from __future__ import annotations

import logging
import os
import shutil
from datetime import timedelta
from pathlib import Path

from app.admin.export_jobs.models import (
    AdminUserExportJob,
    commit_admin_user_export_job,
    create_admin_user_export_job,
    get_admin_user_export_job,
    list_admin_user_export_jobs,
    list_expired_admin_user_export_jobs,
    update_admin_user_export_job,
    utcnow,
)
from app.database import AuditSessionLocal, SessionLocal
from app.logging.models import create_audit_log
from app.paths import DATA_DIR
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.workers.models import DurableWorkerJob, QUEUE_OPERATIONS, enqueue_worker_job

logger = logging.getLogger(__name__)


ADMIN_USER_EXPORT_DIR = Path(
    os.getenv("ADMIN_USER_EXPORT_DIR") or (DATA_DIR / "admin-user-exports")
)
ADMIN_USER_EXPORT_RETENTION_DAYS = max(
    1, int(os.getenv("ADMIN_USER_EXPORT_RETENTION_DAYS", "7") or "7")
)
def _normalize_selected_user_ids(values) -> list[str]:
    """Return non-empty, de-duplicated user IDs while preserving request order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        user_id = str(value or "").strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        normalized.append(user_id)
    return normalized


def export_admin_users_archive_to_path(
    db,
    db_log,
    target_path,
    *,
    user_ids: list[str] | None = None,
):
    """Resolve the archive writer lazily to keep job imports acyclic."""

    from app.admin.user_exports.utils import (
        export_admin_users_archive_to_path as write_archive,
    )

    return write_archive(db, db_log, target_path, user_ids=user_ids)


def ensure_admin_user_export_directory() -> None:
    """Ensure the local artifact directory for admin user exports exists."""
    ADMIN_USER_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _job_artifact_path(job_id: str) -> Path:
    """Return the final ZIP path for a job ID."""
    return ADMIN_USER_EXPORT_DIR / f"{job_id}.zip"


def _job_pending_path(job_id: str) -> Path:
    """Return the temporary ZIP path used while the export is still running."""
    return ADMIN_USER_EXPORT_DIR / f"{job_id}.zip.part"


def _build_response(job: AdminUserExportJob) -> dict:
    """Build a safe API response for an admin user export job."""
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "filename": job.filename,
        "manifest_json": job.manifest_json,
        "options_json": job.options_json,
        "size_bytes": job.size_bytes,
        "requested_by_user_id": job.requested_by_user_id,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "expires_at": job.expires_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "download_ready": bool(job.status == "success" and job.artifact_path),
    }


def _audit_export_job_completion(
    db_log: Session,
    *,
    job: AdminUserExportJob,
    action: str,
    details: dict,
) -> None:
    """Record background export job completion details without request-only metadata."""
    if not job.requested_by_user_id:
        return
    create_audit_log(
        db_log=db_log,
        user_id=job.requested_by_user_id,
        action=action,
        details={"export_job_id": job.id, **details},
        ip_address=None,
        user_agent=None,
        category="admin",
    )


def refresh_expired_admin_user_export_jobs(db: Session) -> None:
    """Mark expired completed jobs and remove their local artifacts."""
    now = utcnow()
    rows = list_expired_admin_user_export_jobs(db, expired_at=now)
    for row in rows:
        if row.artifact_path:
            Path(row.artifact_path).unlink(missing_ok=True)
        update_admin_user_export_job(
            db, job_id=row.id, status="expired", artifact_path="", error=None
        )


def create_and_enqueue_admin_user_export_job(
    db: Session,
    *,
    requested_by_user_id: str | None,
    reason: str,
    user_ids: list[str] | None = None,
) -> AdminUserExportJob:
    """Create and enqueue a canonical selected-user export job."""
    ensure_admin_user_export_directory()
    selected_user_ids = _normalize_selected_user_ids(user_ids)
    options_json = {
        "scope": "selected" if selected_user_ids else "all",
        "user_ids": selected_user_ids,
        "reason": reason.strip(),
    }
    try:
        job = create_admin_user_export_job(
            db,
            requested_by_user_id=requested_by_user_id,
            options_json=options_json,
            commit=False,
        )
        _enqueue_admin_user_export_job_in_session(
            db,
            export_job=job,
            commit=False,
        )
        # The catalog row and its executable queue row are one unit: after
        # this commit there is no observable queued export without durable
        # work behind it.
        return commit_admin_user_export_job(db, job)
    except Exception:
        db.rollback()
        raise


def _enqueue_admin_user_export_job_in_session(
    db: Session,
    *,
    export_job: AdminUserExportJob,
    commit: bool,
) -> DurableWorkerJob:
    return enqueue_worker_job(
        db,
        queue=QUEUE_OPERATIONS,
        kind="admin_user_export",
        user_id=export_job.requested_by_user_id,
        payload={"export_job_id": str(export_job.id)},
        idempotency_key=f"admin-user-export:{export_job.id}",
        # Archive publication is atomic, but a crashed process may have
        # completed sensitive reads. Keep execution at-most-once and let
        # operations reconciliation close an interrupted catalog row.
        max_attempts=1,
        priority=40,
        commit=commit,
    )


def enqueue_admin_user_export_job(job_id: str) -> DurableWorkerJob:
    """Submit an all-users export job to the durable operations queue."""
    ensure_admin_user_export_directory()
    db = SessionLocal()
    try:
        export_job = get_admin_user_export_job(db, str(job_id))
        if export_job is None:
            raise RuntimeError("User export job not found")
        try:
            return _enqueue_admin_user_export_job_in_session(
                db,
                export_job=export_job,
                commit=True,
            )
        except Exception:
            db.rollback()
            update_admin_user_export_job(
                db,
                job_id=str(job_id),
                status="failed",
                error=None,
            )
            raise
    finally:
        db.close()


def run_admin_user_export_job_sync(job_id: str) -> AdminUserExportJob:
    """Run an all-users export job using fresh database sessions."""
    ensure_admin_user_export_directory()
    db = SessionLocal()
    db_log = AuditSessionLocal()
    try:
        return _run_admin_user_export_job_with_sessions(db, db_log, job_id)
    finally:
        db_log.close()
        db.close()


def _run_admin_user_export_job_with_sessions(
    db: Session, db_log: Session, job_id: str
) -> AdminUserExportJob:
    """Build the archive for an all-users export job and publish it atomically."""
    job = get_admin_user_export_job(db, job_id)
    if job is None:
        raise RuntimeError("User export job not found")
    if job.status == "running":
        return job

    update_admin_user_export_job(db, job_id=job_id, status="running", error=None)
    pending_path = _job_pending_path(job_id)
    final_path = _job_artifact_path(job_id)
    pending_path.unlink(missing_ok=True)
    final_path.unlink(missing_ok=True)

    try:
        options = job.options_json if isinstance(job.options_json, dict) else {}
        # Normalize persisted options as a defensive boundary for jobs created
        # before request validation was tightened or inserted outside the API.
        raw_user_ids = options.get("user_ids")
        selected_user_ids = _normalize_selected_user_ids(raw_user_ids)
        requested_selected_scope = bool(selected_user_ids) or (
            str(options.get("scope") or "").strip().lower() == "selected"
        ) or bool(raw_user_ids)
        if requested_selected_scope and not selected_user_ids:
            raise RuntimeError("Selected user export contains no valid user IDs")
        filename, manifest = export_admin_users_archive_to_path(
            db,
            db_log,
            pending_path,
            user_ids=selected_user_ids if requested_selected_scope else None,
        )
        shutil.move(str(pending_path), str(final_path))
        size_bytes = final_path.stat().st_size
        expires_at = utcnow() + timedelta(days=ADMIN_USER_EXPORT_RETENTION_DAYS)
        completed = update_admin_user_export_job(
            db,
            job_id=job_id,
            status="success",
            filename=filename,
            artifact_path=str(final_path),
            manifest_json=manifest,
            size_bytes=size_bytes,
            expires_at=expires_at,
            error=None,
        )
        _audit_export_job_completion(
            db_log,
            job=completed,
            action="EXPORT_USERS_ADMIN_JOB_COMPLETED",
            details={
                "filename": filename,
                "size_bytes": size_bytes,
                "user_count": manifest.get("user_count"),
                "user_files_bundles": int(
                    manifest.get("user_files_count") or 0
                ),
            },
        )
        return completed
    except Exception as exc:  # noqa: BLE001
        logger.exception("Admin user export job %s failed", job_id)
        pending_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        failed = update_admin_user_export_job(
            db,
            job_id=job_id,
            status="failed",
            error=str(exc),
        )
        _audit_export_job_completion(
            db_log,
            job=failed,
            action="EXPORT_USERS_ADMIN_JOB_FAILED",
            details={"error": str(exc)[:500]},
        )
        return failed


def list_admin_user_export_job_responses(db: Session, *, limit: int = 50) -> list[dict]:
    """List recent all-users export jobs as API response dictionaries."""
    refresh_expired_admin_user_export_jobs(db)
    return [
        _build_response(job) for job in list_admin_user_export_jobs(db, limit=limit)
    ]


def get_admin_user_export_job_response(db: Session, job_id: str) -> dict:
    """Get one all-users export job as an API response dictionary."""
    refresh_expired_admin_user_export_jobs(db)
    job = get_admin_user_export_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="User export job not found")
    return _build_response(job)


def materialize_admin_user_export_job(db: Session, job_id: str) -> tuple[Path, str]:
    """Return the local artifact path and filename for a completed export job."""
    refresh_expired_admin_user_export_jobs(db)
    job = get_admin_user_export_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="User export job not found")
    if job.status != "success" or not job.artifact_path:
        raise HTTPException(
            status_code=400, detail="User export job is not ready to download"
        )

    path = Path(job.artifact_path)
    if not path.exists() or not path.is_file():
        update_admin_user_export_job(
            db, job_id=job_id, status="expired", artifact_path="", error=None
        )
        raise HTTPException(
            status_code=404, detail="User export artifact is no longer available"
        )

    return path, job.filename or f"admin-users-{job.id}.zip"


def delete_admin_user_export_job_artifact(db: Session, job_id: str) -> dict:
    """Delete a user export artifact and mark the job deleted."""
    job = get_admin_user_export_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="User export job not found")
    if job.status in {"queued", "running"}:
        raise HTTPException(status_code=400, detail="User export job is still running")

    if job.artifact_path:
        Path(job.artifact_path).unlink(missing_ok=True)
    update_admin_user_export_job(
        db, job_id=job_id, status="deleted", artifact_path="", error=None
    )
    return {"status": "success", "job_id": job_id}
