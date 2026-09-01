from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from urllib.parse import urlparse
import uuid

from fastapi import HTTPException
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Session

from app.database import Base
from app.utils.encryption import decrypt_value, encrypt_value


BACKUP_PROVIDERS = {"local", "s3", "gcs", "azure", "webdav"}
BACKUP_FREQUENCIES = {"hourly", "daily", "weekly"}
BACKUP_TRIGGER_TYPES = {"manual", "scheduled", "pre_restore"}
BACKUP_JOB_STATUS = {"queued", "running", "success", "failed", "deleted"}
RESTORE_TARGET_MODES = {"empty", "in_place"}
RESTORE_STATUS = {"queued", "running", "success", "failed"}
BACKUP_FIELD_UNSET = object()
_SENSITIVE_CONFIG_KEYS = {
    "secret",
    "password",
    "token",
    "key",
    "credential",
    "connection_string",
    "access_key_id",
    "secret_access_key",
    "session_token",
    "private_key",
    "credentials_json",
}
REDACTED_CONFIG_VALUE = "***redacted***"


def utcnow() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


class BackupDestination(Base):
    __tablename__ = "backup_destination"
    __table_args__ = (
        Index("ix_backup_destination_enabled", "enabled"),
        Index("ix_backup_destination_provider", "provider"),
        Index("ix_backup_destination_updated_at", "updated_at"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    provider = Column(String(20), nullable=False)
    config_encrypted = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class BackupSchedule(Base):
    __tablename__ = "backup_schedule"
    __table_args__ = (
        Index("ix_backup_schedule_enabled", "enabled"),
        Index("ix_backup_schedule_destination_id", "destination_id"),
        Index("ix_backup_schedule_last_run_at", "last_run_at"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    timezone = Column(String(64), nullable=False, default="UTC")
    frequency = Column(String(16), nullable=False, default="daily")
    minute = Column(Integer, nullable=False, default=0)
    hour = Column(Integer, nullable=False, default=2)
    days_of_week = Column(JSON, nullable=False, default=list)
    retention_count = Column(Integer, nullable=True)
    retention_days = Column(Integer, nullable=True)
    destination_id = Column(String, ForeignKey("backup_destination.id", ondelete="SET NULL"), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class BackupJob(Base):
    __tablename__ = "backup_job"
    __table_args__ = (
        Index("ix_backup_job_status", "status"),
        Index("ix_backup_job_trigger_type", "trigger_type"),
        Index("ix_backup_job_destination_id", "destination_id"),
        Index("ix_backup_job_started_at", "started_at"),
        Index("ix_backup_job_created_at", "created_at"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    trigger_type = Column(String(24), nullable=False, default="manual")
    status = Column(String(20), nullable=False, default="queued")
    error = Column(Text, nullable=True)
    manifest_json = Column(JSON, nullable=True)
    options = Column(JSON, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    requested_by_user_id = Column(String, nullable=True)
    destination_id = Column(String, ForeignKey("backup_destination.id", ondelete="SET NULL"), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class BackupArtifact(Base):
    __tablename__ = "backup_artifact"
    __table_args__ = (
        Index("ix_backup_artifact_backup_job_id", "backup_job_id"),
        Index("ix_backup_artifact_storage_uri", "storage_uri"),
        Index("ix_backup_artifact_verified_at", "verified_at"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    backup_job_id = Column(String, ForeignKey("backup_job.id", ondelete="CASCADE"), nullable=False)
    storage_uri = Column(Text, nullable=False)
    checksum_sha256 = Column(String(64), nullable=False)
    bytes = Column(Integer, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class RestoreJob(Base):
    __tablename__ = "restore_job"
    __table_args__ = (
        Index("ix_restore_job_status", "status"),
        Index("ix_restore_job_target_mode", "target_mode"),
        Index("ix_restore_job_started_at", "started_at"),
        Index("ix_restore_job_created_at", "created_at"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    source_uri = Column(Text, nullable=False)
    target_mode = Column(String(20), nullable=False, default="empty")
    status = Column(String(20), nullable=False, default="queued")
    error = Column(Text, nullable=True)
    preflight_json = Column(JSON, nullable=True)
    options = Column(JSON, nullable=True)
    requested_by_user_id = Column(String, nullable=True)
    confirmed_by_user_id = Column(String, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


def encrypt_destination_config(config: dict[str, Any] | None) -> dict[str, str]:
    """Encrypt destination configuration."""
    payload = json.dumps(config or {}, separators=(",", ":"), ensure_ascii=True)
    return {"enc_v1": encrypt_value(payload)}


def decrypt_destination_config(config_encrypted: dict[str, Any] | None) -> dict[str, Any]:
    """Decrypt destination configuration."""
    if not isinstance(config_encrypted, dict):
        return {}

    encrypted = config_encrypted.get("enc_v1")
    if not isinstance(encrypted, str) or not encrypted.strip():
        return {}

    try:
        raw = decrypt_value(encrypted)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to decrypt destination config: {exc}") from exc

    if not raw:
        return {}

    try:
        value = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Invalid decrypted destination config: {exc}") from exc

    return value if isinstance(value, dict) else {}


def redact_destination_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Redact sensitive values from configuration."""
    def _mask(value: Any, parent_key: str | None = None) -> Any:
        if isinstance(value, dict):
            masked: dict[str, Any] = {}
            for key, item in value.items():
                key_lower = str(key).strip().lower()
                if key_lower in _SENSITIVE_CONFIG_KEYS or any(part in key_lower for part in ("secret", "password", "token")):
                    # Empty secret fields do not contain anything worth hiding.
                    # Returning the empty value also lets the admin form distinguish
                    # "not configured" from "a saved secret exists".
                    masked[key] = REDACTED_CONFIG_VALUE if item not in (None, "") else item
                else:
                    masked[key] = _mask(item, key_lower)
            return masked
        if isinstance(value, list):
            return [_mask(item, parent_key) for item in value]
        return value

    return _mask(config or {})


def _is_sensitive_config_key(key: Any) -> bool:
    """Return whether a destination-config key should be treated as a secret."""
    normalized = str(key).strip().lower()
    return normalized in _SENSITIVE_CONFIG_KEYS or any(
        part in normalized for part in ("secret", "password", "token")
    )


def merge_redacted_destination_config(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge an edited config without ever persisting the redaction marker.

    Destination responses intentionally replace secrets with a fixed marker.
    Clients may submit that response again when editing unrelated fields. The
    marker is display-only and must therefore resolve back to the existing
    plaintext value before the config is encrypted.

    Missing sensitive keys are also preserved. A client that deliberately wants
    to clear a secret can send ``null`` or an empty string; those explicit values
    are not treated as missing.
    """
    current = existing if isinstance(existing, dict) else {}
    submitted = incoming if isinstance(incoming, dict) else {}
    merged: dict[str, Any] = {}

    for key, submitted_value in submitted.items():
        current_value = current.get(key)
        if _is_sensitive_config_key(key) and submitted_value == REDACTED_CONFIG_VALUE:
            if key in current:
                merged[key] = current_value
            continue
        if isinstance(submitted_value, dict) and isinstance(current_value, dict):
            merged[key] = merge_redacted_destination_config(current_value, submitted_value)
        else:
            merged[key] = submitted_value

    # Secret controls are intentionally empty on edit. Preserve a saved value
    # when the client omits that key, while still allowing explicit clearing.
    for key, current_value in current.items():
        if key not in submitted and _is_sensitive_config_key(key):
            merged[key] = current_value

    return merged


def validate_backup_destination_config(provider: str, config: dict[str, Any]) -> None:
    """Validate destination settings before encrypting and persisting them.

    Storage adapters still validate at runtime, but rejecting structurally
    incomplete settings here gives API clients a deterministic field error and
    prevents unusable destinations from being saved.
    """
    normalized_provider = (provider or "").strip().lower()
    if not isinstance(config, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "destination_config_object_required", "field": "config"},
        )

    def required_text(key: str) -> str:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=422,
                detail={"code": "destination_config_field_required", "field": key},
            )
        return value.strip()

    if normalized_provider in {"s3", "gcs"}:
        required_text("bucket")
    elif normalized_provider == "azure":
        required_text("container")
        connection_string = config.get("connection_string")
        account_url = config.get("account_url")
        if not (
            isinstance(connection_string, str) and connection_string.strip()
        ) and not (
            isinstance(account_url, str) and account_url.strip()
        ):
            raise HTTPException(
                status_code=422,
                detail={"code": "destination_config_azure_auth_required", "field": "account_url"},
            )
    elif normalized_provider == "webdav":
        required_text("url")

    for key in ("endpoint_url", "account_url", "url"):
        value = config.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            raise HTTPException(
                status_code=422,
                detail={"code": "destination_config_url_invalid", "field": key},
            )
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(
                status_code=422,
                detail={"code": "destination_config_url_invalid", "field": key},
            )

    if "verify_ssl" in config and not isinstance(config["verify_ssl"], bool):
        raise HTTPException(
            status_code=422,
            detail={"code": "destination_config_boolean_invalid", "field": "verify_ssl"},
        )

    if "timeout" in config:
        timeout = config["timeout"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 3600:
            raise HTTPException(
                status_code=422,
                detail={"code": "destination_config_number_invalid", "field": "timeout"},
            )


def create_backup_destination(
    db: Session,
    *,
    name: str,
    provider: str,
    config: dict[str, Any],
    enabled: bool,
) -> BackupDestination:
    """Create backup destination."""
    provider_normalized = (provider or "").strip().lower()
    if provider_normalized not in BACKUP_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported backup provider '{provider}'")
    # A redaction marker is display-only. Treat one submitted during creation as
    # absent so the marker can never become an encrypted credential.
    safe_config = merge_redacted_destination_config({}, config)
    validate_backup_destination_config(provider_normalized, safe_config)

    now = utcnow()
    row = BackupDestination(
        id=str(uuid.uuid4()),
        name=(name or "").strip()[:255] or "Backup Destination",
        provider=provider_normalized,
        config_encrypted=encrypt_destination_config(safe_config),
        enabled=bool(enabled),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_backup_destination(
    db: Session,
    *,
    destination_id: str,
    name: str | None = None,
    provider: str | None = None,
    config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> BackupDestination:
    """Update backup destination."""
    row = get_backup_destination(db, destination_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup destination not found")

    if name is not None:
        row.name = name.strip()[:255] or row.name

    provider_normalized = row.provider
    if provider is not None:
        provider_normalized = provider.strip().lower()
        if provider_normalized not in BACKUP_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unsupported backup provider '{provider}'")

    provider_changed = provider_normalized != row.provider
    if config is not None or provider_changed:
        # Only merge saved secrets when the provider remains the same. Carrying
        # credentials from one provider type to another would retain unrelated
        # secrets that the admin can no longer see in the provider-specific form.
        decrypted_config = decrypt_destination_config(row.config_encrypted)
        existing_config = {} if provider_changed else decrypted_config
        submitted_config = config if config is not None else {}
        safe_config = merge_redacted_destination_config(existing_config, submitted_config)
        validate_backup_destination_config(provider_normalized, safe_config)
        row.config_encrypted = encrypt_destination_config(safe_config)
    row.provider = provider_normalized

    if enabled is not None:
        row.enabled = bool(enabled)

    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def list_backup_destinations(db: Session) -> list[BackupDestination]:
    """List all backup destinations."""
    return db.query(BackupDestination).order_by(BackupDestination.created_at.desc()).all()


def get_backup_destination(db: Session, destination_id: str) -> BackupDestination | None:
    """Get backup destination by ID."""
    return db.query(BackupDestination).filter(BackupDestination.id == destination_id).first()


def delete_backup_destination(db: Session, destination_id: str) -> bool:
    """Delete backup destination."""
    row = get_backup_destination(db, destination_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup destination not found")
    db.delete(row)
    db.commit()
    return True


def create_backup_schedule(
    db: Session,
    *,
    name: str,
    enabled: bool,
    timezone_name: str,
    frequency: str,
    minute: int,
    hour: int,
    days_of_week: list[int] | None,
    retention_count: int | None,
    retention_days: int | None,
    destination_id: str | None,
) -> BackupSchedule:
    """Create backup schedule."""
    frequency_normalized = (frequency or "").strip().lower()
    if frequency_normalized not in BACKUP_FREQUENCIES:
        raise HTTPException(status_code=400, detail=f"Unsupported backup frequency '{frequency}'")

    if destination_id:
        destination = get_backup_destination(db, destination_id)
        if destination is None:
            raise HTTPException(status_code=404, detail="Backup destination not found")

    now = utcnow()
    row = BackupSchedule(
        id=str(uuid.uuid4()),
        name=(name or "").strip()[:255] or "Backup Schedule",
        enabled=bool(enabled),
        timezone=(timezone_name or "UTC").strip() or "UTC",
        frequency=frequency_normalized,
        minute=max(0, min(59, int(minute))),
        hour=max(0, min(23, int(hour))),
        days_of_week=[int(day) for day in (days_of_week or []) if isinstance(day, int) and 0 <= int(day) <= 6],
        retention_count=retention_count,
        retention_days=retention_days,
        destination_id=destination_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_backup_schedule(
    db: Session,
    *,
    schedule_id: str,
    name: str | None = None,
    enabled: bool | None = None,
    timezone_name: str | None = None,
    frequency: str | None = None,
    minute: int | None = None,
    hour: int | None = None,
    days_of_week: list[int] | None = None,
    retention_count: Any = BACKUP_FIELD_UNSET,
    retention_days: Any = BACKUP_FIELD_UNSET,
    destination_id: Any = BACKUP_FIELD_UNSET,
) -> BackupSchedule:
    """Update backup schedule."""
    row = get_backup_schedule(db, schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup schedule not found")

    if name is not None:
        row.name = name.strip()[:255] or row.name
    if enabled is not None:
        row.enabled = bool(enabled)
    if timezone_name is not None:
        row.timezone = timezone_name.strip() or row.timezone
    if frequency is not None:
        frequency_normalized = frequency.strip().lower()
        if frequency_normalized not in BACKUP_FREQUENCIES:
            raise HTTPException(status_code=400, detail=f"Unsupported backup frequency '{frequency}'")
        row.frequency = frequency_normalized
    if minute is not None:
        row.minute = max(0, min(59, int(minute)))
    if hour is not None:
        row.hour = max(0, min(23, int(hour)))
    if days_of_week is not None:
        row.days_of_week = [int(day) for day in days_of_week if isinstance(day, int) and 0 <= int(day) <= 6]

    if retention_count is not BACKUP_FIELD_UNSET:
        row.retention_count = int(retention_count) if retention_count is not None else None
    if retention_days is not BACKUP_FIELD_UNSET:
        row.retention_days = int(retention_days) if retention_days is not None else None

    if destination_id is not BACKUP_FIELD_UNSET:
        if destination_id:
            destination = get_backup_destination(db, destination_id)
            if destination is None:
                raise HTTPException(status_code=404, detail="Backup destination not found")
            row.destination_id = destination_id
        else:
            row.destination_id = None

    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def list_backup_schedules(db: Session) -> list[BackupSchedule]:
    """List all backup schedules."""
    return db.query(BackupSchedule).order_by(BackupSchedule.created_at.desc()).all()


def get_backup_schedule(db: Session, schedule_id: str) -> BackupSchedule | None:
    """Get backup schedule by ID."""
    return db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()


def delete_backup_schedule(db: Session, schedule_id: str) -> bool:
    """Delete backup schedule."""
    row = get_backup_schedule(db, schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup schedule not found")
    db.delete(row)
    db.commit()
    return True


def create_backup_job(
    db: Session,
    *,
    trigger_type: str,
    destination_id: str | None,
    requested_by_user_id: str | None,
    options: dict[str, Any] | None = None,
) -> BackupJob:
    """Create backup job."""
    trigger = (trigger_type or "manual").strip().lower()
    if trigger not in BACKUP_TRIGGER_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported backup trigger type '{trigger_type}'")

    if destination_id:
        destination = get_backup_destination(db, destination_id)
        if destination is None:
            raise HTTPException(status_code=404, detail="Backup destination not found")

    now = utcnow()
    row = BackupJob(
        id=str(uuid.uuid4()),
        trigger_type=trigger,
        status="queued",
        options=options or {},
        requested_by_user_id=requested_by_user_id,
        destination_id=destination_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_backup_job(db: Session, job_id: str) -> BackupJob | None:
    """Get backup job by ID."""
    return db.query(BackupJob).filter(BackupJob.id == job_id).first()


def paginate_backup_jobs(
    db: Session,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
) -> tuple[list[BackupJob], int, int, int]:
    """
    Return one deterministic, bounded page of backup jobs.

    Counting and filtering happen before the page query. Stale page requests
    are clamped to the final available page so deleting the last row on the
    final page immediately produces a useful response.
    """
    query = db.query(BackupJob)
    normalized_status = (status or "").strip()
    if normalized_status:
        query = query.filter(BackupJob.status == normalized_status)

    total = int(query.count())
    total_pages = (total + page_size - 1) // page_size
    resolved_page = min(page, total_pages) if total_pages else 1
    offset = (resolved_page - 1) * page_size

    jobs = (
        query
        .order_by(BackupJob.created_at.desc(), BackupJob.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    return jobs, total, total_pages, resolved_page


def update_backup_job_status(
    db: Session,
    *,
    job_id: str,
    status: str,
    error: str | None = None,
    manifest_json: dict[str, Any] | None = None,
    size_bytes: int | None = None,
) -> BackupJob:
    """Update backup job status."""
    row = get_backup_job(db, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup job not found")

    status_normalized = (status or "").strip().lower()
    if status_normalized not in BACKUP_JOB_STATUS:
        raise HTTPException(status_code=400, detail=f"Unsupported backup job status '{status}'")

    row.status = status_normalized
    row.error = error
    if manifest_json is not None:
        row.manifest_json = manifest_json
    if size_bytes is not None:
        row.size_bytes = int(size_bytes)

    now = utcnow()
    row.updated_at = now
    if status_normalized == "running" and row.started_at is None:
        row.started_at = now
    if status_normalized in {"success", "failed", "deleted"}:
        row.finished_at = now

    db.commit()
    db.refresh(row)
    return row


def create_backup_artifact(
    db: Session,
    *,
    backup_job_id: str,
    storage_uri: str,
    checksum_sha256: str,
    bytes_count: int,
) -> BackupArtifact:
    """Create backup artifact."""
    if get_backup_job(db, backup_job_id) is None:
        raise HTTPException(status_code=404, detail="Backup job not found")

    row = BackupArtifact(
        id=str(uuid.uuid4()),
        backup_job_id=backup_job_id,
        storage_uri=storage_uri,
        checksum_sha256=checksum_sha256,
        bytes=int(bytes_count),
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_backup_artifacts(db: Session, backup_job_id: str) -> list[BackupArtifact]:
    """List backup artifacts newest-first with a deterministic tie-breaker."""
    return (
        db.query(BackupArtifact)
        .filter(BackupArtifact.backup_job_id == backup_job_id)
        .order_by(BackupArtifact.created_at.desc(), BackupArtifact.id.desc())
        .all()
    )


def list_backup_artifacts_by_job_ids(
    db: Session,
    backup_job_ids: list[str],
) -> dict[str, list[BackupArtifact]]:
    """
    Batch-load artifacts for a bounded set of backup jobs.

    The backup-history endpoint uses this to avoid one artifact query per card.
    """
    if not backup_job_ids:
        return {}

    artifacts = (
        db.query(BackupArtifact)
        .filter(BackupArtifact.backup_job_id.in_(backup_job_ids))
        .order_by(
            BackupArtifact.backup_job_id.asc(),
            BackupArtifact.created_at.desc(),
            BackupArtifact.id.desc(),
        )
        .all()
    )
    grouped = {job_id: [] for job_id in backup_job_ids}
    for artifact in artifacts:
        grouped.setdefault(artifact.backup_job_id, []).append(artifact)
    return grouped


def get_backup_artifact(db: Session, artifact_id: str) -> BackupArtifact | None:
    """Get backup artifact by ID."""
    return db.query(BackupArtifact).filter(BackupArtifact.id == artifact_id).first()


def mark_backup_artifact_verified(db: Session, artifact_id: str) -> BackupArtifact:
    """Mark backup artifact as verified."""
    row = get_backup_artifact(db, artifact_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup artifact not found")
    row.verified_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def delete_backup_job(db: Session, job_id: str) -> bool:
    """Delete backup job and its artifacts."""
    row = get_backup_job(db, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup job not found")

    artifacts = list_backup_artifacts(db, job_id)
    for artifact in artifacts:
        db.delete(artifact)

    db.delete(row)
    db.commit()
    return True


def create_restore_job(
    db: Session,
    *,
    source_uri: str,
    target_mode: str,
    requested_by_user_id: str | None,
    confirmed_by_user_id: str | None,
    options: dict[str, Any] | None = None,
) -> RestoreJob:
    """Create restore job."""
    normalized_target = (target_mode or "empty").strip().lower()
    if normalized_target not in RESTORE_TARGET_MODES:
        raise HTTPException(status_code=400, detail=f"Unsupported restore target mode '{target_mode}'")

    now = utcnow()
    row = RestoreJob(
        id=str(uuid.uuid4()),
        source_uri=source_uri,
        target_mode=normalized_target,
        status="queued",
        options=options or {},
        requested_by_user_id=requested_by_user_id,
        confirmed_by_user_id=confirmed_by_user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_restore_job(db: Session, restore_job_id: str) -> RestoreJob | None:
    """Get restore job by ID."""
    return db.query(RestoreJob).filter(RestoreJob.id == restore_job_id).first()


def list_restore_jobs(db: Session, *, limit: int = 100, status: str | None = None) -> list[RestoreJob]:
    """List restore jobs with optional filtering."""
    query = db.query(RestoreJob)
    if status:
        query = query.filter(RestoreJob.status == status)
    return query.order_by(RestoreJob.created_at.desc()).limit(max(1, min(limit, 500))).all()


def update_restore_job_status(
    db: Session,
    *,
    restore_job_id: str,
    status: str,
    error: str | None = None,
    preflight_json: dict[str, Any] | None = None,
) -> RestoreJob:
    """Update restore job status."""
    row = get_restore_job(db, restore_job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Restore job not found")

    status_normalized = (status or "").strip().lower()
    if status_normalized not in RESTORE_STATUS:
        raise HTTPException(status_code=400, detail=f"Unsupported restore status '{status}'")

    row.status = status_normalized
    row.error = error
    if preflight_json is not None:
        row.preflight_json = preflight_json

    now = utcnow()
    row.updated_at = now
    if status_normalized == "running" and row.started_at is None:
        row.started_at = now
    if status_normalized in {"success", "failed"}:
        row.finished_at = now

    db.commit()
    db.refresh(row)
    return row
