from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import uuid
from typing import Any
import time

import anyio
from sqlalchemy import (
    and_,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    delete,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.utils.sqlalchemy_encryption import EncryptedJSON, EncryptedString


QUEUE_OPERATIONS = "operations"
QUEUE_GENERATION = "generation"
QUEUE_MEMORY = "memory"
QUEUE_RESEARCH = "research"
QUEUE_FILES = "files"
QUEUE_LIFECYCLE = "lifecycle"
QUEUE_MAINTENANCE = "maintenance"
QUEUE_RENDERING = "rendering"
QUEUE_MEDIA = "media"
QUEUE_INGESTION = "ingestion"
QUEUE_EVENTS = "events"
WORKER_QUEUES = frozenset(
    {
        QUEUE_OPERATIONS,
        QUEUE_GENERATION,
        QUEUE_MEMORY,
        QUEUE_RESEARCH,
        QUEUE_FILES,
        QUEUE_LIFECYCLE,
        QUEUE_MAINTENANCE,
        QUEUE_RENDERING,
        QUEUE_MEDIA,
        QUEUE_INGESTION,
        QUEUE_EVENTS,
    }
)

JOB_PENDING = "pending"
JOB_PROCESSING = "processing"
JOB_RETRY = "retry"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"
JOB_ACTIVE_STATUSES = (JOB_PENDING, JOB_PROCESSING, JOB_RETRY)
JOB_TERMINAL_STATUSES = (JOB_SUCCEEDED, JOB_FAILED, JOB_CANCELLED)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_payload_bytes() -> int:
    try:
        value = int(os.getenv("WORKER_JOB_MAX_PAYLOAD_BYTES", str(8 * 1024 * 1024)))
    except (TypeError, ValueError):
        return 8 * 1024 * 1024
    return max(64 * 1024, min(value, 32 * 1024 * 1024))


def _validate_json_size(value: Any, *, label: str) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Worker job {label} must be JSON serializable") from exc
    if len(encoded) > _bounded_payload_bytes():
        raise ValueError(f"Worker job {label} exceeds the configured size limit")


class DurableWorkerJob(Base):
    """Encrypted, leased unit of work shared by the dedicated worker services.

    ``user_id`` intentionally has no foreign key: account lifecycle jobs must
    remain claimable after their target row is deleted.  Payloads are erased at
    terminal state so prompts, provider keys and import metadata do not become
    a second long-term data store.
    """

    __tablename__ = "durable_worker_jobs"
    __table_args__ = (
        UniqueConstraint("queue", "idempotency_key", name="uq_worker_job_queue_idempotency"),
        CheckConstraint("priority >= 0", name="ck_worker_job_priority_nonnegative"),
        CheckConstraint("attempt_count >= 0", name="ck_worker_job_attempts_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="ck_worker_job_max_attempts_positive"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_worker_job_progress_range"),
        Index("ix_worker_job_claim", "queue", "status", "priority", "available_at", "created_at"),
        Index("ix_worker_job_lease", "queue", "status", "lease_expires_at"),
        Index("ix_worker_job_user", "user_id", "created_at"),
        Index("ix_worker_job_updated", "updated_at"),
        Index("ix_worker_job_expires", "expires_at"),
        Index("ix_worker_job_reconcile", "queue", "status", "kind", "reconciled_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    queue = Column(String(32), nullable=False)
    kind = Column(String(64), nullable=False)
    user_id = Column(String, nullable=True)
    payload = Column(EncryptedJSON, nullable=True)
    result = Column(EncryptedJSON, nullable=True)
    status = Column(String(24), nullable=False, default=JOB_PENDING)
    priority = Column(Integer, nullable=False, default=50)
    idempotency_key = Column(String(200), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    progress = Column(Integer, nullable=False, default=0)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    available_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    leased_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    lease_owner = Column(String(96), nullable=True)
    error_code = Column(String(64), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    reconciled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ImportStagingReservation(Base):
    """Operational quota reservation for one not-yet-consumed import file.

    Rows are created before the first staging byte is written and are removed
    only after the corresponding target/partial files are gone. They
    intentionally contain no imported content and are excluded from
    user/admin data archives.
    """

    __tablename__ = "import_staging_reservations"
    __table_args__ = (
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_import_staging_reservation_size_nonnegative",
        ),
        Index(
            "ix_import_staging_reservation_principal",
            "principal_id",
            "created_at",
        ),
        Index("ix_import_staging_reservation_expiry", "expires_at"),
    )

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    staged_name = Column(String(64), nullable=False, unique=True)
    principal_id = Column(String, nullable=False)
    import_kind = Column(String(64), nullable=False)
    size_bytes = Column(BigInteger, nullable=False, default=0)
    worker_job_id = Column(
        String,
        ForeignKey("durable_worker_jobs.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditEventOutbox(Base):
    """Durable, encrypted handoff from request transactions to the audit DB."""

    __tablename__ = "audit_event_outbox"
    __table_args__ = (
        CheckConstraint(
            "subjects_indexed OR ("
            "status = 'cancelled' AND user_id = '' AND reason IS NULL "
            "AND details IS NULL AND ip_address IS NULL AND user_agent IS NULL"
            ")",
            name="ck_audit_event_outbox_unindexed_safe",
        ),
        Index("ix_audit_event_outbox_delivery", "status", "created_at"),
        Index("ix_audit_event_outbox_user", "user_id", "created_at"),
    )

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(64), nullable=False)
    action = Column(String(128), nullable=False)
    reason = Column(EncryptedString, nullable=True)
    details = Column(EncryptedJSON, nullable=True)
    ip_address = Column(EncryptedString, nullable=True)
    user_agent = Column(EncryptedString, nullable=True)
    category = Column(String(64), nullable=False, default="admin")
    # False is a rolling-upgrade safety marker for writers that predate the
    # subject-reference index. Current writers always set it explicitly.
    subjects_indexed = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("false"),
    )
    status = Column(String(24), nullable=False, default=JOB_PENDING)
    error_code = Column(String(64), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditEventSubjectState(Base):
    """Privacy fence for subjects mentioned by durable audit events.

    Only a one-way fingerprint is retained.  The row also acts as the common
    transaction lock between event enqueue and policy-aware erasure, including
    when a subject is mentioned only in an administrator or system event.
    """

    __tablename__ = "audit_event_subject_states"
    __table_args__ = (
        Index("ix_audit_event_subject_state_erased", "erased_at"),
    )

    subject_fingerprint = Column(String(64), primary_key=True)
    erased_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditEventSubjectReference(Base):
    """Indexed, non-reversible subject membership for an encrypted event."""

    __tablename__ = "audit_event_subject_references"
    __table_args__ = (
        Index(
            "ix_audit_event_subject_reference_subject",
            "subject_fingerprint",
            "event_id",
        ),
    )

    event_id = Column(
        String(32),
        ForeignKey("audit_event_outbox.id", ondelete="CASCADE"),
        primary_key=True,
    )
    subject_fingerprint = Column(String(64), primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditEventErasureGuard(Base):
    """Hash-only row lock spanning main-DB and audit-DB erasure commits."""

    __tablename__ = "audit_event_erasure_guards"

    subject_fingerprint = Column(String(64), primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditErasureReconciliationCheckpoint(Base):
    """Durable completion marker for one-time legacy audit reconciliation."""

    __tablename__ = "audit_erasure_reconciliation_checkpoints"

    key = Column(String(96), primary_key=True)
    completed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


def audit_event_subject_fingerprint(user_id: str) -> str:
    """Return a stable, non-reversible lookup key for an internal user ID."""

    normalized = str(user_id or "").strip()
    if not normalized:
        raise ValueError("Audit event subject ID must not be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def lock_audit_event_erasure_guard(
    db: Session,
    *,
    user_id: str,
) -> AuditEventErasureGuard:
    """Create and lock the cross-database erasure guard for one user."""

    fingerprint = audit_event_subject_fingerprint(user_id)
    values = {
        "subject_fingerprint": fingerprint,
        "created_at": utcnow(),
    }
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

        statement = dialect_insert(AuditEventErasureGuard).values(values)
        db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[AuditEventErasureGuard.subject_fingerprint]
            )
        )
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        statement = dialect_insert(AuditEventErasureGuard).values(values)
        db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[AuditEventErasureGuard.subject_fingerprint]
            )
        )
    else:
        try:
            with db.begin_nested():
                db.add(AuditEventErasureGuard(**values))
                db.flush()
        except IntegrityError:
            pass

    row = (
        db.query(AuditEventErasureGuard)
        .filter(AuditEventErasureGuard.subject_fingerprint == fingerprint)
        .with_for_update()
        .first()
    )
    if row is None:
        raise RuntimeError("Could not establish audit erasure coordination guard")
    return row


def lock_audit_event_subject_states(
    db: Session,
    *,
    subject_fingerprints: set[str],
) -> dict[str, AuditEventSubjectState]:
    """Create and lock subject fences in a consistent order.

    The conflict-safe insert is the serialization point when a subject has not
    been seen before.  A concurrent enqueue and erasure therefore cannot both
    pass an absent-row check and commit in the wrong order.
    """

    fingerprints = sorted(
        {
            str(value or "").strip().lower()
            for value in subject_fingerprints
            if str(value or "").strip()
        }
    )
    if not fingerprints:
        return {}

    current = utcnow()
    values = [
        {
            "subject_fingerprint": fingerprint,
            "erased_at": None,
            "created_at": current,
            "updated_at": current,
        }
        for fingerprint in fingerprints
    ]
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

        statement = dialect_insert(AuditEventSubjectState).values(values)
        db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[AuditEventSubjectState.subject_fingerprint]
            )
        )
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        statement = dialect_insert(AuditEventSubjectState).values(values)
        db.execute(
            statement.on_conflict_do_nothing(
                index_elements=[AuditEventSubjectState.subject_fingerprint]
            )
        )
    else:
        for value in values:
            try:
                with db.begin_nested():
                    db.add(AuditEventSubjectState(**value))
                    db.flush()
            except IntegrityError:
                pass

    rows = (
        db.query(AuditEventSubjectState)
        .filter(AuditEventSubjectState.subject_fingerprint.in_(fingerprints))
        .order_by(AuditEventSubjectState.subject_fingerprint.asc())
        .with_for_update()
        .all()
    )
    if len(rows) != len(fingerprints):
        raise RuntimeError("Could not establish every audit event subject fence")
    return {str(row.subject_fingerprint): row for row in rows}


@dataclass(frozen=True)
class WorkerJobSnapshot:
    id: str
    queue: str
    kind: str
    user_id: str | None
    payload: dict[str, Any]
    attempt_count: int
    max_attempts: int
    expires_at: datetime | None

    @classmethod
    def from_row(cls, row: DurableWorkerJob) -> "WorkerJobSnapshot":
        return cls(
            id=str(row.id),
            queue=str(row.queue),
            kind=str(row.kind),
            user_id=str(row.user_id) if row.user_id is not None else None,
            payload=dict(row.payload or {}),
            attempt_count=int(row.attempt_count or 0),
            max_attempts=int(row.max_attempts or 1),
            expires_at=row.expires_at,
        )


class WorkerJobFailed(RuntimeError):
    def __init__(self, code: str, *, status: str):
        super().__init__(code)
        self.code = str(code or "worker_job_failed")
        self.status = str(status)


def _read_worker_job_result(job_id: str) -> dict[str, Any] | None:
    """Read one job state using a session local to the calling thread."""

    from app.database import SessionLocal

    session = SessionLocal()
    try:
        row = (
            session.query(DurableWorkerJob)
            .filter(DurableWorkerJob.id == str(job_id))
            .first()
        )
        if row is None:
            raise WorkerJobFailed("job_unavailable", status=JOB_FAILED)
        status = str(row.status)
        if status == JOB_SUCCEEDED:
            return dict(row.result or {})
        if status in (JOB_FAILED, JOB_CANCELLED):
            raise WorkerJobFailed(str(row.error_code or status), status=status)
        return None
    finally:
        session.close()


def wait_for_worker_job(
    job_id: str,
    *,
    timeout_seconds: float = 300,
    poll_seconds: float = 0.2,
) -> dict[str, Any]:
    """Wait for a compatibility endpoint while execution remains off-process."""

    deadline = time.monotonic() + max(1.0, min(float(timeout_seconds), 3600.0))
    delay = max(0.05, min(float(poll_seconds), 2.0))
    while True:
        result = _read_worker_job_result(job_id)
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError("Worker job did not complete before the request deadline")
        time.sleep(delay)
        delay = min(2.0, delay * 1.5)


async def wait_for_worker_job_async(
    job_id: str,
    *,
    timeout_seconds: float = 300,
    poll_seconds: float = 0.2,
) -> dict[str, Any]:
    """Wait for a job without reserving an AnyIO worker thread while idle."""

    deadline = time.monotonic() + max(1.0, min(float(timeout_seconds), 3600.0))
    delay = max(0.05, min(float(poll_seconds), 2.0))
    while True:
        result = await anyio.to_thread.run_sync(_read_worker_job_result, str(job_id))
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError("Worker job did not complete before the request deadline")
        await anyio.sleep(delay)
        delay = min(2.0, delay * 1.5)


def enqueue_worker_job(
    db: Session,
    *,
    queue: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    user_id: str | None = None,
    priority: int = 50,
    max_attempts: int = 5,
    available_at: datetime | None = None,
    expires_at: datetime | None = None,
    retry_terminal: bool = False,
    commit: bool = False,
) -> DurableWorkerJob:
    normalized_queue = str(queue or "").strip().lower()
    normalized_kind = str(kind or "").strip().lower()
    normalized_key = str(idempotency_key or "").strip()
    if normalized_queue not in WORKER_QUEUES:
        raise ValueError(f"Unsupported worker queue: {normalized_queue!r}")
    if not normalized_kind or len(normalized_kind) > 64:
        raise ValueError("Worker job kind is required and must be at most 64 characters")
    if not normalized_key:
        raise ValueError("Worker job idempotency key is required")
    _validate_json_size(payload, label="payload")

    truncated_key = normalized_key[:200]
    existing_query = (
        db.query(DurableWorkerJob)
        .filter(
            DurableWorkerJob.queue == normalized_queue,
            DurableWorkerJob.idempotency_key == truncated_key,
        )
    )
    if retry_terminal:
        existing_query = existing_query.with_for_update()
    existing = existing_query.first()
    if existing is not None:
        if retry_terminal and existing.status in (JOB_FAILED, JOB_CANCELLED):
            # Keep the deterministic key on the newest attempt so concurrent
            # callers still converge on one active job.  The terminal row keeps
            # a unique archival key (and therefore its history) instead of being
            # deleted. Keeping the tail also preserves revision information used
            # by rendering reconciliation.
            archive_prefix = f"archived:{existing.id}:"
            remaining = max(0, 200 - len(archive_prefix))
            current = utcnow()
            existing.idempotency_key = archive_prefix + truncated_key[-remaining:]
            # The explicit retry supersedes this terminal attempt.  Mark it as
            # reconciled before exposing the deterministic key to the new job,
            # otherwise a later rendering reconciler can overwrite successful
            # state from the retry with this archived failure.
            existing.reconciled_at = current
            existing.updated_at = current
            db.flush()
        else:
            if commit:
                db.commit()
                db.refresh(existing)
            return existing

    row = DurableWorkerJob(
        id=str(uuid.uuid4()),
        queue=normalized_queue,
        kind=normalized_kind,
        user_id=str(user_id) if user_id is not None else None,
        payload=dict(payload),
        priority=max(0, min(int(priority), 1000)),
        idempotency_key=truncated_key,
        max_attempts=max(1, min(int(max_attempts), 100)),
        available_at=available_at or utcnow(),
        expires_at=expires_at,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(DurableWorkerJob)
            .filter(
                DurableWorkerJob.queue == normalized_queue,
                DurableWorkerJob.idempotency_key == truncated_key,
            )
            .first()
        )
        if existing is None:
            raise
        row = existing
    if commit:
        db.commit()
        db.refresh(row)
    return row


def release_import_staging_reservations(
    db: Session,
    *,
    staged_names: set[str] | tuple[str, ...] | list[str] | None = None,
    commit: bool = False,
) -> int:
    """Release explicitly identified operational import reservations."""

    normalized_names = {
        str(value).strip().lower()
        for value in (staged_names or ())
        if str(value).strip()
    }
    if not normalized_names:
        return 0
    changed = db.execute(
        delete(ImportStagingReservation)
        .execution_options(synchronize_session=False)
        .where(ImportStagingReservation.staged_name.in_(normalized_names))
    )
    if commit:
        db.commit()
    return int(changed.rowcount or 0)


def claim_worker_jobs(
    db: Session,
    *,
    queue: str,
    worker_id: str,
    batch_size: int = 1,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> list[DurableWorkerJob]:
    current = now or utcnow()
    normalized_queue = str(queue or "").strip().lower()
    normalized_owner = str(worker_id or "worker")[:96]
    # A cancellation acknowledged only in process memory would otherwise leave
    # a multi-attempt job stuck forever if that process dies before finalizing
    # it. Once its lease is gone, cancellation wins over retry.
    db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.queue == normalized_queue,
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.cancel_requested.is_(True),
            DurableWorkerJob.lease_expires_at < current,
        )
        .values(
            status=JOB_CANCELLED,
            payload=None,
            result=None,
            progress=0,
            error_code="cancelled",
            leased_at=None,
            lease_expires_at=None,
            lease_owner=None,
            finished_at=current,
            updated_at=current,
        )
    )
    # A process may die after spending its final allowed attempt.  Such a row
    # must not remain "processing" forever and must not be executed again.
    db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.queue == normalized_queue,
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.lease_expires_at < current,
            DurableWorkerJob.attempt_count >= DurableWorkerJob.max_attempts,
        )
        .values(
            status=JOB_FAILED,
            payload=None,
            result=None,
            progress=0,
            error_code="lease_expired",
            leased_at=None,
            lease_expires_at=None,
            lease_owner=None,
            finished_at=current,
            updated_at=current,
        )
    )
    query = (
        select(DurableWorkerJob.id)
        .where(
            DurableWorkerJob.queue == normalized_queue,
            DurableWorkerJob.cancel_requested.is_(False),
            or_(
                (
                    DurableWorkerJob.status.in_((JOB_PENDING, JOB_RETRY))
                    & (DurableWorkerJob.available_at <= current)
                ),
                (
                    (DurableWorkerJob.status == JOB_PROCESSING)
                    & (DurableWorkerJob.lease_expires_at < current)
                    & (DurableWorkerJob.attempt_count < DurableWorkerJob.max_attempts)
                ),
            ),
            or_(DurableWorkerJob.expires_at.is_(None), DurableWorkerJob.expires_at > current),
        )
        .order_by(
            DurableWorkerJob.priority.asc(),
            DurableWorkerJob.available_at.asc(),
            DurableWorkerJob.created_at.asc(),
        )
        .limit(max(1, min(int(batch_size), 100)))
    )
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    job_ids = list(db.execute(query).scalars())
    if not job_ids:
        db.commit()
        return []

    lease_expiry = current + timedelta(seconds=max(30, int(lease_seconds)))
    db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(DurableWorkerJob.id.in_(job_ids))
        .values(
            status=JOB_PROCESSING,
            attempt_count=DurableWorkerJob.attempt_count + 1,
            leased_at=current,
            lease_expires_at=lease_expiry,
            lease_owner=normalized_owner,
            started_at=current,
            updated_at=current,
            error_code=None,
        )
    )
    db.commit()
    return (
        db.query(DurableWorkerJob)
        .filter(
            DurableWorkerJob.id.in_(job_ids),
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.lease_owner == normalized_owner,
        )
        .order_by(DurableWorkerJob.priority.asc(), DurableWorkerJob.created_at.asc())
        .all()
    )


def renew_worker_job_lease(
    db: Session,
    *,
    job_id: str,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    current = now or utcnow()
    result = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.id == job_id,
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.lease_owner == str(worker_id or "worker")[:96],
        )
        .values(
            lease_expires_at=current + timedelta(seconds=max(30, int(lease_seconds))),
            updated_at=current,
        )
    )
    db.commit()
    return int(result.rowcount or 0) == 1


def worker_job_cancel_requested(db: Session, *, job_id: str, worker_id: str | None = None) -> bool:
    row = (
        db.query(
            DurableWorkerJob.cancel_requested,
            DurableWorkerJob.status,
            DurableWorkerJob.lease_owner,
        )
        .filter(DurableWorkerJob.id == job_id)
        .first()
    )
    if row is None:
        return worker_id is not None
    if worker_id is not None and (
        row.status != JOB_PROCESSING
        or row.lease_owner != str(worker_id or "worker")[:96]
    ):
        # Losing the lease is equivalent to cancellation for the old process;
        # it must never continue performing side effects under a stale claim.
        return True
    return bool(row.cancel_requested)


def set_worker_job_progress(
    db: Session,
    *,
    job_id: str,
    worker_id: str,
    progress: int,
) -> bool:
    result = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.id == job_id,
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.lease_owner == str(worker_id or "worker")[:96],
        )
        .values(progress=max(0, min(int(progress), 99)), updated_at=utcnow())
    )
    db.commit()
    return int(result.rowcount or 0) == 1


def _terminal_values(*, status: str, current: datetime, result: dict[str, Any] | None, error_code: str | None) -> dict:
    if result is not None:
        _validate_json_size(result, label="result")
    return {
        "status": status,
        "payload": None,
        "result": result,
        "progress": 100 if status == JOB_SUCCEEDED else 0,
        "error_code": str(error_code or "")[:64] or None,
        "leased_at": None,
        "lease_expires_at": None,
        "lease_owner": None,
        "finished_at": current,
        "updated_at": current,
    }


def mark_worker_job_succeeded(
    db: Session,
    *,
    job_id: str,
    worker_id: str,
    result: dict[str, Any] | None = None,
) -> bool:
    current = utcnow()
    changed = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.id == job_id,
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.lease_owner == str(worker_id or "worker")[:96],
            DurableWorkerJob.cancel_requested.is_(False),
        )
        .values(**_terminal_values(status=JOB_SUCCEEDED, current=current, result=result, error_code=None))
    )
    db.commit()
    return int(changed.rowcount or 0) == 1


def mark_worker_job_cancelled(
    db: Session,
    *,
    job_id: str,
    worker_id: str,
    error_code: str = "cancelled",
) -> bool:
    current = utcnow()
    changed = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.id == job_id,
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.lease_owner == str(worker_id or "worker")[:96],
        )
        .values(**_terminal_values(status=JOB_CANCELLED, current=current, result=None, error_code=error_code))
    )
    db.commit()
    return int(changed.rowcount or 0) == 1


def mark_worker_job_failed(
    db: Session,
    *,
    job_id: str,
    worker_id: str,
    error_code: str,
    retryable: bool,
    retry_delay_seconds: int,
) -> str | None:
    row = (
        db.query(DurableWorkerJob)
        .filter(
            DurableWorkerJob.id == job_id,
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.lease_owner == str(worker_id or "worker")[:96],
        )
        .with_for_update()
        .first()
    )
    if row is None:
        db.rollback()
        return None
    current = utcnow()
    expires_at = row.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    terminal = (
        not retryable
        or bool(row.cancel_requested)
        or int(row.attempt_count or 0) >= int(row.max_attempts or 1)
        or bool(expires_at and expires_at <= current)
    )
    if terminal:
        values = _terminal_values(
            status=JOB_CANCELLED if row.cancel_requested else JOB_FAILED,
            current=current,
            result=None,
            error_code="cancelled" if row.cancel_requested else error_code,
        )
        for key, value in values.items():
            setattr(row, key, value)
    else:
        row.status = JOB_RETRY
        row.available_at = current + timedelta(seconds=max(1, int(retry_delay_seconds)))
        row.error_code = str(error_code or "internal")[:64]
        row.leased_at = None
        row.lease_expires_at = None
        row.lease_owner = None
        row.updated_at = current
    db.commit()
    return str(row.status)


def request_worker_job_cancellation(
    db: Session,
    *,
    job_id: str,
    user_id: str | None = None,
    commit: bool = True,
) -> bool:
    current = utcnow()
    base_filters = (
        DurableWorkerJob.id == job_id,
        DurableWorkerJob.status.in_(JOB_ACTIVE_STATUSES),
    )
    if user_id is not None:
        base_filters += (DurableWorkerJob.user_id == str(user_id),)
    queued = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            *base_filters,
            DurableWorkerJob.status.in_((JOB_PENDING, JOB_RETRY)),
        )
        .values(
            status=JOB_CANCELLED,
            cancel_requested=True,
            payload=None,
            result=None,
            error_code="cancelled",
            finished_at=current,
            updated_at=current,
        )
    )
    processing = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(*base_filters, DurableWorkerJob.status == JOB_PROCESSING)
        .values(cancel_requested=True, payload=None, result=None, updated_at=current)
    )
    if commit:
        db.commit()
    return int(queued.rowcount or 0) + int(processing.rowcount or 0) == 1


def cancel_user_worker_jobs(db: Session, *, user_id: str, commit: bool = False) -> int:
    current = utcnow()
    queued = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.user_id == str(user_id),
            DurableWorkerJob.status.in_((JOB_PENDING, JOB_RETRY)),
            DurableWorkerJob.queue.notin_((QUEUE_LIFECYCLE, QUEUE_EVENTS)),
        )
        .values(
            status=JOB_CANCELLED,
            cancel_requested=True,
            payload=None,
            result=None,
            error_code="account_state_changed",
            finished_at=current,
            updated_at=current,
        )
    )
    processing = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.user_id == str(user_id),
            DurableWorkerJob.status == JOB_PROCESSING,
            DurableWorkerJob.queue.notin_((QUEUE_LIFECYCLE, QUEUE_EVENTS)),
        )
        .values(cancel_requested=True, payload=None, result=None, updated_at=current)
    )
    if commit:
        db.commit()
    return int(queued.rowcount or 0) + int(processing.rowcount or 0)


def erase_user_worker_state(db: Session, *, user_id: str, commit: bool = False) -> int:
    """Cancel/redact work owned by an account at its hard-erasure boundary."""

    current = utcnow()
    active = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.user_id == str(user_id),
            DurableWorkerJob.queue.notin_((QUEUE_LIFECYCLE, QUEUE_EVENTS)),
            DurableWorkerJob.status.in_(JOB_ACTIVE_STATUSES),
        )
        .values(
            status=JOB_CANCELLED,
            cancel_requested=True,
            user_id=None,
            payload=None,
            result=None,
            error_code="account_erased",
            lease_owner=None,
            leased_at=None,
            lease_expires_at=None,
            finished_at=current,
            updated_at=current,
        )
    )
    remaining = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.user_id == str(user_id),
            DurableWorkerJob.queue != QUEUE_EVENTS,
        )
        .values(user_id=None, payload=None, result=None, updated_at=current)
    )
    if commit:
        db.commit()
    return int(active.rowcount or 0) + int(remaining.rowcount or 0)


def erase_user_audit_event_state(
    db: Session,
    *,
    user_id: str,
    commit: bool = False,
) -> int:
    """Cancel and redact audit delivery state before user-scoped log erasure.

    Audit retention is independent from account retention, so the general user
    worker cleanup deliberately leaves event jobs alone.  The audit-log deletion
    boundary calls this helper only when that policy actually requires deletion.
    A hashed subject-state row fences concurrent and later enqueues. Jobs are
    then locked before outbox rows to match queue reconciliation lock order.
    The event consumer holds the outbox lock through audit persistence, making
    delivery and erasure serialize on either side of the audit-database delete.
    """

    normalized_user_id = str(user_id)
    current = utcnow()
    subject_fingerprint = audit_event_subject_fingerprint(normalized_user_id)
    subject_states = lock_audit_event_subject_states(
        db,
        subject_fingerprints={subject_fingerprint},
    )
    subject_state = subject_states[subject_fingerprint]
    subject_state.erased_at = current
    subject_state.updated_at = current

    from app.logging.models import pseudonymize_deleted_user_details

    # The migration backfills every existing row. This fallback covers an old
    # application replica that writes during a rolling upgrade: false-marked
    # events are inspected under lock and conservatively cancelled if they
    # mention the subject, so they cannot bypass the new reference index.
    legacy_event_ids: set[str] = set()
    legacy_rows = (
        db.query(AuditEventOutbox)
        .filter(
            AuditEventOutbox.subjects_indexed.is_(False),
            AuditEventOutbox.status.notin_(("delivered", JOB_CANCELLED)),
        )
        .order_by(AuditEventOutbox.id.asc())
        .with_for_update()
        .all()
    )
    for row in legacy_rows:
        redacted_details = pseudonymize_deleted_user_details(
            row.details,
            normalized_user_id,
        )
        if row.user_id == normalized_user_id or redacted_details != row.details:
            legacy_event_ids.add(str(row.id))

    referenced_event_ids = set(
        db.execute(
            select(AuditEventSubjectReference.event_id).where(
                AuditEventSubjectReference.subject_fingerprint
                == subject_fingerprint
            )
        ).scalars()
    )
    jobs = (
        db.query(DurableWorkerJob)
        .filter(
            DurableWorkerJob.queue == QUEUE_EVENTS,
            DurableWorkerJob.kind == "audit_log",
            or_(
                DurableWorkerJob.user_id == normalized_user_id,
                DurableWorkerJob.idempotency_key.in_(
                    {f"audit:{event_id}" for event_id in legacy_event_ids}
                ),
            ),
        )
        .with_for_update()
        .all()
    )
    for job in jobs:
        if job.status in JOB_ACTIVE_STATUSES:
            job.status = JOB_CANCELLED
            job.cancel_requested = True
            job.error_code = "account_erased"
            job.progress = 0
            job.finished_at = current
        job.user_id = None
        job.payload = None
        job.result = None
        job.lease_owner = None
        job.leased_at = None
        job.lease_expires_at = None
        if job.status in (JOB_FAILED, JOB_CANCELLED):
            job.reconciled_at = current
        job.updated_at = current

    outbox_rows = (
        db.query(AuditEventOutbox)
        .filter(
            or_(
                AuditEventOutbox.user_id == normalized_user_id,
                AuditEventOutbox.id.in_(referenced_event_ids | legacy_event_ids),
            )
        )
        .with_for_update()
        .all()
    )
    for row in outbox_rows:
        if row.user_id == normalized_user_id or row.id in legacy_event_ids:
            row.user_id = ""
            row.reason = None
            row.details = None
            row.ip_address = None
            row.user_agent = None
            row.status = JOB_CANCELLED
            row.error_code = "account_erased"
            row.subjects_indexed = True
        else:
            row.details = pseudonymize_deleted_user_details(
                row.details,
                normalized_user_id,
            )
        row.updated_at = current

    db.query(AuditEventSubjectReference).filter(
        AuditEventSubjectReference.subject_fingerprint == subject_fingerprint
    ).delete(synchronize_session=False)

    if commit:
        db.commit()
    return len(jobs) + len(outbox_rows)


def restore_user_audit_event_subject(
    db: Session,
    *,
    user_id: str,
    commit: bool = False,
) -> bool:
    """Lift an audit privacy fence when a soft-deleted account is restored."""

    subject_fingerprint = audit_event_subject_fingerprint(str(user_id))
    subject_states = lock_audit_event_subject_states(
        db,
        subject_fingerprints={subject_fingerprint},
    )
    state = subject_states[subject_fingerprint]
    changed = state.erased_at is not None
    current = utcnow()
    queued = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.queue == QUEUE_EVENTS,
            DurableWorkerJob.kind == "audit_erasure",
            DurableWorkerJob.user_id == str(user_id),
            DurableWorkerJob.status.in_((JOB_PENDING, JOB_RETRY)),
        )
        .values(
            status=JOB_CANCELLED,
            cancel_requested=True,
            user_id=None,
            payload=None,
            result=None,
            error_code="account_restored",
            finished_at=current,
            reconciled_at=current,
            updated_at=current,
        )
    )
    processing = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(
            DurableWorkerJob.queue == QUEUE_EVENTS,
            DurableWorkerJob.kind == "audit_erasure",
            DurableWorkerJob.user_id == str(user_id),
            DurableWorkerJob.status == JOB_PROCESSING,
        )
        .values(
            cancel_requested=True,
            user_id=None,
            payload=None,
            result=None,
            error_code="account_restored",
            updated_at=current,
        )
    )
    state.erased_at = None
    state.updated_at = current
    if commit:
        db.commit()
    return changed or bool(int(queued.rowcount or 0) + int(processing.rowcount or 0))


def expire_worker_jobs(db: Session, *, batch_size: int = 1000, now: datetime | None = None) -> int:
    current = now or utcnow()
    ids = list(
        db.execute(
            select(DurableWorkerJob.id)
            .where(
                DurableWorkerJob.status.in_(JOB_ACTIVE_STATUSES),
                DurableWorkerJob.expires_at.is_not(None),
                DurableWorkerJob.expires_at <= current,
            )
            .order_by(DurableWorkerJob.expires_at.asc())
            .limit(max(1, min(int(batch_size), 5000)))
        ).scalars()
    )
    if not ids:
        db.rollback()
        return 0
    changed = db.execute(
        update(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(DurableWorkerJob.id.in_(ids))
        .values(
            status=JOB_CANCELLED,
            payload=None,
            result=None,
            error_code="expired",
            lease_owner=None,
            leased_at=None,
            lease_expires_at=None,
            finished_at=current,
            updated_at=current,
        )
    )
    db.commit()
    return int(changed.rowcount or 0)


def worker_job_reconciliation_required():
    """Return the SQL predicate for jobs with dependent terminal state."""

    return or_(
        and_(
            DurableWorkerJob.queue == QUEUE_OPERATIONS,
            DurableWorkerJob.kind.in_(("backup", "restore", "admin_user_export")),
        ),
        and_(
            DurableWorkerJob.queue == QUEUE_GENERATION,
            DurableWorkerJob.kind.in_(("send", "regenerate")),
        ),
        and_(
            DurableWorkerJob.queue == QUEUE_RESEARCH,
            DurableWorkerJob.kind.in_(("deep_research", "subagent")),
        ),
        and_(
            DurableWorkerJob.queue == QUEUE_FILES,
            DurableWorkerJob.kind.in_(
                ("extract_text", "pdf_inspect", "pdf_page", "pdf_page_image")
            ),
        ),
        and_(
            DurableWorkerJob.queue == QUEUE_MEDIA,
            DurableWorkerJob.kind.in_(("transcribe", "meeting_transcript")),
        ),
        and_(
            DurableWorkerJob.queue == QUEUE_RENDERING,
            DurableWorkerJob.kind.in_(("canvas_markdown_pdf", "presentation_rerender")),
        ),
        and_(
            DurableWorkerJob.queue == QUEUE_EVENTS,
            DurableWorkerJob.kind.in_(("audit_log", "audit_erasure")),
        ),
    )


def purge_terminal_worker_jobs(
    db: Session,
    *,
    retention_days: int = 7,
    batch_size: int = 1000,
    now: datetime | None = None,
) -> int:
    cutoff = (now or utcnow()) - timedelta(days=max(1, int(retention_days)))
    reconciliation_required = worker_job_reconciliation_required()
    ids = list(
        db.execute(
            select(DurableWorkerJob.id)
            .where(
                DurableWorkerJob.status.in_(JOB_TERMINAL_STATUSES),
                DurableWorkerJob.updated_at < cutoff,
                or_(
                    DurableWorkerJob.status == JOB_SUCCEEDED,
                    DurableWorkerJob.reconciled_at.is_not(None),
                    ~reconciliation_required,
                ),
            )
            .order_by(DurableWorkerJob.updated_at.asc())
            .limit(max(1, min(int(batch_size), 5000)))
        ).scalars()
    )
    if not ids:
        db.rollback()
        return 0
    changed = db.execute(
        delete(DurableWorkerJob)
        .execution_options(synchronize_session=False)
        .where(DurableWorkerJob.id.in_(ids))
    )
    db.commit()
    return int(changed.rowcount or 0)


def lock_unreconciled_terminal_jobs(
    db: Session,
    *,
    queue: str,
    kinds: tuple[str, ...],
    batch_size: int = 1000,
) -> list[DurableWorkerJob]:
    """Lock a bounded batch of failed/cancelled jobs for domain reconciliation.

    The caller owns the transaction and must set ``reconciled_at`` before it
    commits. PostgreSQL replicas skip rows already held by another worker;
    SQLite keeps its normal single-writer behavior for local development.
    """

    if not kinds:
        return []
    query = (
        db.query(DurableWorkerJob)
        .filter(
            DurableWorkerJob.queue == str(queue),
            DurableWorkerJob.kind.in_(tuple(str(value) for value in kinds)),
            DurableWorkerJob.status.in_((JOB_FAILED, JOB_CANCELLED)),
            DurableWorkerJob.reconciled_at.is_(None),
        )
        .order_by(DurableWorkerJob.updated_at.asc())
        .limit(max(1, min(int(batch_size), 5000)))
    )
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    return query.all()


def revive_worker_job_after_lease_expiry(
    db: Session,
    *,
    job_id: str,
    payload: dict[str, Any],
    available_at: datetime | None = None,
) -> bool:
    """Safely requeue an idempotent job that exhausted attempts by crashing.

    This is intentionally limited to ``lease_expired``. Ordinary terminal
    failures stay visible for operator intervention instead of creating an
    unbounded retry loop.
    """

    _validate_json_size(payload, label="payload")
    query = db.query(DurableWorkerJob).filter(DurableWorkerJob.id == str(job_id))
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    row = query.first()
    if (
        row is None
        or row.status != JOB_FAILED
        or row.error_code != "lease_expired"
    ):
        return False
    current = utcnow()
    row.payload = dict(payload)
    row.result = None
    row.status = JOB_PENDING
    row.attempt_count = 0
    row.progress = 0
    row.cancel_requested = False
    row.available_at = available_at or current
    row.leased_at = None
    row.lease_expires_at = None
    row.lease_owner = None
    row.error_code = None
    row.started_at = None
    row.finished_at = None
    row.reconciled_at = None
    row.updated_at = current
    db.flush()
    return True
