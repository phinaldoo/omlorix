from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.database import Base
from fastapi import HTTPException
from sqlalchemy import JSON, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Session

ADMIN_EXPORT_JOB_STATUSES = {
    "queued",
    "running",
    "success",
    "failed",
    "deleted",
    "expired",
}
ADMIN_USER_EXPORT_JOB_STATUSES = ADMIN_EXPORT_JOB_STATUSES


def utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


class AdminUserExportJob(Base):
    """Persisted background job for all-users admin export archives."""

    __tablename__ = "admin_user_export_job"
    __table_args__ = (
        Index("ix_admin_user_export_job_status", "status"),
        Index("ix_admin_user_export_job_requested_by_user_id", "requested_by_user_id"),
        Index("ix_admin_user_export_job_created_at", "created_at"),
        Index("ix_admin_user_export_job_expires_at", "expires_at"),
    )

    id = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    status = Column(String(20), nullable=False, default="queued")
    error = Column(Text, nullable=True)
    filename = Column(String(255), nullable=True)
    artifact_path = Column(Text, nullable=True)
    manifest_json = Column(JSON, nullable=True)
    # Scope is persisted with the job so any worker can reproduce the exact
    # requested selection without relying on process-local state.
    options_json = Column(JSON, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    requested_by_user_id = Column(String, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


def create_admin_user_export_job(
    db: Session,
    *,
    requested_by_user_id: str | None,
    options_json: dict[str, Any] | None = None,
    commit: bool = True,
) -> AdminUserExportJob:
    """Create a queued canonical user-archive export job."""
    now = utcnow()
    row = AdminUserExportJob(
        id=str(uuid.uuid4()),
        status="queued",
        requested_by_user_id=requested_by_user_id,
        options_json=options_json or {"scope": "all", "user_ids": []},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def commit_admin_user_export_job(
    db: Session,
    row: AdminUserExportJob,
) -> AdminUserExportJob:
    """Commit a catalog row and related work staged in the same transaction."""

    db.commit()
    db.refresh(row)
    return row

def get_admin_user_export_job(db: Session, job_id: str) -> AdminUserExportJob | None:
    """Return one all-users export job by ID."""
    return db.query(AdminUserExportJob).filter(AdminUserExportJob.id == job_id).first()


def list_admin_user_export_jobs(
    db: Session, *, limit: int = 50
) -> list[AdminUserExportJob]:
    """List recent all-users export jobs."""
    return (
        db.query(AdminUserExportJob)
        .order_by(AdminUserExportJob.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )


def list_expired_admin_user_export_jobs(
    db: Session,
    *,
    expired_at: datetime,
) -> list[AdminUserExportJob]:
    """Return successful user-export jobs whose artifacts have expired."""

    return (
        db.query(AdminUserExportJob)
        .filter(AdminUserExportJob.status == "success")
        .filter(AdminUserExportJob.expires_at.isnot(None))
        .filter(AdminUserExportJob.expires_at <= expired_at)
        .all()
    )


def update_admin_user_export_job(
    db: Session,
    *,
    job_id: str,
    status: str,
    error: str | None = None,
    filename: str | None = None,
    artifact_path: str | None = None,
    manifest_json: dict[str, Any] | None = None,
    size_bytes: int | None = None,
    expires_at: datetime | None = None,
) -> AdminUserExportJob:
    """Update status and artifact metadata for an all-users export job."""
    row = get_admin_user_export_job(db, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User export job not found")

    status_normalized = (status or "").strip().lower()
    if status_normalized not in ADMIN_USER_EXPORT_JOB_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"Unsupported user export job status '{status}'"
        )

    now = utcnow()
    row.status = status_normalized
    row.error = error
    row.updated_at = now

    if status_normalized == "running" and row.started_at is None:
        row.started_at = now
    if status_normalized in {"success", "failed", "deleted", "expired"}:
        row.finished_at = row.finished_at or now

    if filename is not None:
        row.filename = filename
    if artifact_path is not None:
        row.artifact_path = artifact_path
    if manifest_json is not None:
        row.manifest_json = manifest_json
    if size_bytes is not None:
        row.size_bytes = size_bytes
    if expires_at is not None:
        row.expires_at = expires_at

    db.commit()
    db.refresh(row)
    return row
