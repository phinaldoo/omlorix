from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import uuid

from sqlalchemy import (
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
from app.email.address import normalize_single_mailbox
from app.utils.sqlalchemy_encryption import EncryptedJSON, EncryptedString


OUTBOX_PENDING = "pending"
OUTBOX_PROCESSING = "processing"
OUTBOX_RETRY = "retry"
OUTBOX_SENT = "sent"
OUTBOX_DEAD = "dead"
OUTBOX_CANCELLED = "cancelled"
OUTBOX_TERMINAL_STATUSES = (OUTBOX_SENT, OUTBOX_DEAD, OUTBOX_CANCELLED)

EMAIL_CHANGE_PENDING = "pending"
EMAIL_CHANGE_COMPLETED = "completed"
EMAIL_CHANGE_CANCELLED = "cancelled"
EMAIL_CHANGE_EXPIRED = "expired"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_secret(value: str | None) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


class EmailOutbox(Base):
    """Encrypted, leased SMTP work item.

    ``user_id`` deliberately has no foreign key. Account-deletion notices must
    remain deliverable after the user row and all cascading data are gone.
    """

    __tablename__ = "email_delivery_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_email_outbox_idempotency_key"),
        UniqueConstraint("message_id", name="uq_email_outbox_message_id"),
        Index(
            "ix_email_outbox_claim",
            "status",
            "priority",
            "available_at",
            "created_at",
        ),
        Index("ix_email_outbox_lease_expiry", "status", "lease_expires_at"),
        Index("ix_email_outbox_user_id", "user_id"),
        Index("ix_email_outbox_expires_at", "expires_at"),
        Index("ix_email_outbox_updated_at", "updated_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=True)
    recipient = Column(EncryptedString, nullable=True)
    template_type = Column(String(64), nullable=False)
    template_version = Column(Integer, nullable=False, default=1)
    language_code = Column(String(8), nullable=False, default="en")
    payload = Column(EncryptedJSON, nullable=True)
    priority = Column(Integer, nullable=False, default=50)
    status = Column(String(24), nullable=False, default=OUTBOX_PENDING)
    idempotency_key = Column(String(160), nullable=False)
    message_id = Column(String(255), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=8)
    available_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    leased_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    lease_owner = Column(String(96), nullable=True)
    last_error_type = Column(String(64), nullable=True)
    last_error = Column(String(255), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class PendingEmailChange(Base):
    """Single-use, hashed proof for a pending canonical email change."""

    __tablename__ = "pending_email_changes"
    __table_args__ = (
        UniqueConstraint("verify_token_hash", name="uq_email_change_verify_token_hash"),
        UniqueConstraint("cancel_token_hash", name="uq_email_change_cancel_token_hash"),
        Index("ix_email_change_user_status", "user_id", "status"),
        Index("ix_email_change_user_created_at", "user_id", "created_at"),
        Index("ix_email_change_expires_at", "expires_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    new_email = Column(EncryptedString, nullable=False)
    old_email = Column(EncryptedString, nullable=False)
    verify_token_hash = Column(String(64), nullable=False)
    cancel_token_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default=EMAIL_CHANGE_PENDING)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)


class TrustedDeviceNotification(Base):
    """A per-user opaque browser device marker used only for new-device mail."""

    __tablename__ = "trusted_device_notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "device_token_hash",
            name="uq_trusted_device_user_token",
        ),
        Index("ix_trusted_device_user_id", "user_id"),
        Index("ix_trusted_device_last_seen_at", "last_seen_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_token_hash = Column(String(64), nullable=False)
    device_summary = Column(EncryptedString, nullable=True)
    network_summary = Column(EncryptedString, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_notified_at = Column(DateTime(timezone=True), nullable=True)


class EmailSecurityRateLimit(Base):
    """Shared database fallback for short-lived email-security throttles.

    Only a SHA-256 bucket fingerprint is stored. PostgreSQL advisory locks make
    updates for the same bucket atomic across API replicas when Redis is not
    configured or is temporarily unavailable.
    """

    __tablename__ = "email_security_rate_limits"
    __table_args__ = (
        Index("ix_email_security_rate_limit_expires_at", "expires_at"),
    )

    bucket_key = Column(String(64), primary_key=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    window_started_at = Column(DateTime(timezone=True), nullable=False)
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmailSecurityState(Base):
    """Singleton epoch used to invalidate restored one-time email secrets."""

    __tablename__ = "email_security_state"

    id = Column(Integer, primary_key=True)
    action_epoch = Column(String(64), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


def _stable_message_id(outbox_id: str) -> str:
    return f"<{outbox_id}@email.omlorix.local>"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _postgres_advisory_lock_id(bucket_key: str) -> int:
    value = int(bucket_key[:16], 16)
    return value - (1 << 64) if value >= (1 << 63) else value


def consume_email_security_rate_limit(
    db: Session,
    *,
    bucket: str,
    max_attempts: int,
    window_seconds: int,
    cooldown_seconds: int = 0,
    now: datetime | None = None,
) -> bool:
    """Atomically consume one shared rate-limit attempt.

    The caller owns the transaction and must commit even when this function
    returns ``False``. A database error is intentionally allowed to propagate
    so authentication callers can fail closed.
    """

    current = now or utcnow()
    current = _as_utc(current) or utcnow()
    bounded_window = max(1, int(window_seconds))
    bounded_cooldown = max(0, int(cooldown_seconds))
    bounded_max_attempts = max(1, int(max_attempts))
    bucket_key = hash_secret(f"email-security-rate-limit:{bucket}")

    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    dialect_name = str(getattr(getattr(bind, "dialect", None), "name", ""))
    if dialect_name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _postgres_advisory_lock_id(bucket_key)},
        )

    row = (
        db.query(EmailSecurityRateLimit)
        .filter(EmailSecurityRateLimit.bucket_key == bucket_key)
        .with_for_update()
        .first()
    )
    window_end = current + timedelta(seconds=bounded_window)
    cooldown_until = current + timedelta(seconds=bounded_cooldown)
    if row is None:
        row = EmailSecurityRateLimit(
            bucket_key=bucket_key,
            attempt_count=1,
            window_started_at=current,
            cooldown_until=cooldown_until if bounded_cooldown else None,
            expires_at=max(window_end, cooldown_until),
            updated_at=current,
        )
        db.add(row)
        db.flush()
        return True

    existing_cooldown = _as_utc(row.cooldown_until)
    if existing_cooldown is not None and existing_cooldown > current:
        return False

    window_started_at = _as_utc(row.window_started_at)
    if window_started_at is None or window_started_at + timedelta(seconds=bounded_window) <= current:
        row.window_started_at = current
        row.attempt_count = 1
        window_end = current + timedelta(seconds=bounded_window)
    else:
        row.attempt_count = int(row.attempt_count or 0) + 1
        window_end = window_started_at + timedelta(seconds=bounded_window)
    row.cooldown_until = cooldown_until if bounded_cooldown else None
    row.expires_at = max(window_end, cooldown_until)
    row.updated_at = current
    db.flush()
    return int(row.attempt_count) <= bounded_max_attempts


def consume_email_security_cooldown(
    db: Session,
    *,
    bucket: str,
    cooldown_seconds: int,
    now: datetime | None = None,
) -> int:
    """Atomically consume a cooldown bucket and return retry-after seconds.

    A return value of ``0`` means the caller acquired the bucket. Positive
    values are safe to expose as a resend delay. The bucket name is hashed by
    the shared rate-limit implementation before it is persisted.
    """

    current = _as_utc(now or utcnow()) or utcnow()
    bounded_cooldown = max(1, int(cooldown_seconds))
    allowed = consume_email_security_rate_limit(
        db,
        bucket=bucket,
        max_attempts=1,
        window_seconds=bounded_cooldown,
        cooldown_seconds=bounded_cooldown,
        now=current,
    )
    if allowed:
        return 0

    bucket_key = hash_secret(f"email-security-rate-limit:{bucket}")
    row = (
        db.query(EmailSecurityRateLimit)
        .filter(EmailSecurityRateLimit.bucket_key == bucket_key)
        .first()
    )
    cooldown_until = _as_utc(getattr(row, "cooldown_until", None))
    if cooldown_until is None or cooldown_until <= current:
        # The failed acquisition is authoritative even if a custom database
        # backend cannot expose its exact timestamp.
        return bounded_cooldown
    remaining = (cooldown_until - current).total_seconds()
    return max(1, min(bounded_cooldown, int(remaining + 0.999999)))


def get_email_security_action_epoch(db: Session) -> str:
    """Return the shared epoch included in one-time delivery-code hashes."""

    row = db.query(EmailSecurityState).filter(EmailSecurityState.id == 1).first()
    if row is not None:
        return str(row.action_epoch)

    epoch = secrets.token_hex(32)
    candidate = EmailSecurityState(id=1, action_epoch=epoch, updated_at=utcnow())
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
    except IntegrityError:
        row = db.query(EmailSecurityState).filter(EmailSecurityState.id == 1).first()
        if row is None:
            raise
        return str(row.action_epoch)
    return epoch


def rotate_email_security_action_epoch(db: Session) -> str:
    """Rotate the shared one-time-secret epoch in the caller's transaction."""

    epoch = secrets.token_hex(32)
    row = (
        db.query(EmailSecurityState)
        .filter(EmailSecurityState.id == 1)
        .with_for_update()
        .first()
    )
    if row is None:
        db.add(EmailSecurityState(id=1, action_epoch=epoch, updated_at=utcnow()))
    else:
        row.action_epoch = epoch
        row.updated_at = utcnow()
    db.flush()
    return epoch


def hash_email_security_action(
    db: Session,
    *,
    purpose: str,
    secret_value: str | None,
) -> str:
    """Hash a short-lived browser/email secret against the shared epoch."""

    epoch = get_email_security_action_epoch(db)
    return hash_secret(
        f"email-security-action:{str(purpose or '').strip()}:{epoch}:{secret_value or ''}"
    )


def enqueue_email(
    db: Session,
    *,
    recipient: str,
    template_type: str,
    payload: dict,
    idempotency_key: str,
    user_id: str | None = None,
    language_code: str = "en",
    priority: int = 50,
    available_at: datetime | None = None,
    expires_at: datetime | None = None,
    max_attempts: int = 16,
) -> EmailOutbox:
    """Stage one encrypted outbox row in the caller's SQL transaction."""

    normalized_recipient = normalize_single_mailbox(recipient).lower()
    normalized_key = str(idempotency_key or "").strip()
    if not normalized_recipient:
        raise ValueError("Email outbox recipient is required")
    if not normalized_key:
        raise ValueError("Email outbox idempotency key is required")

    existing = (
        db.query(EmailOutbox)
        .filter(EmailOutbox.idempotency_key == normalized_key[:160])
        .first()
    )
    if existing:
        return existing

    outbox_id = str(uuid.uuid4())
    row = EmailOutbox(
        id=outbox_id,
        user_id=user_id,
        recipient=normalized_recipient,
        template_type=str(template_type or "")[:64],
        language_code=str(language_code or "en")[:8],
        payload=dict(payload or {}),
        priority=max(0, int(priority)),
        idempotency_key=normalized_key[:160],
        message_id=_stable_message_id(outbox_id),
        max_attempts=max(1, int(max_attempts)),
        available_at=available_at or utcnow(),
        expires_at=expires_at,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(EmailOutbox)
            .filter(EmailOutbox.idempotency_key == normalized_key[:160])
            .first()
        )
        if existing:
            return existing
        raise
    return row


def claim_email_batch(
    db: Session,
    *,
    worker_id: str,
    batch_size: int = 50,
    lease_seconds: int = 90,
    now: datetime | None = None,
) -> list[EmailOutbox]:
    """Lease a bounded batch; PostgreSQL workers do not block each other."""

    current = now or utcnow()
    # A worker can die after SMTP accepted a message but before ``sent`` is
    # committed. Once that claim consumed the final configured attempt, its
    # expired lease must become terminal instead of being delivered again for
    # the remainder of the message lifetime.
    db.execute(
        update(EmailOutbox)
        .execution_options(synchronize_session=False)
        .where(
            EmailOutbox.status == OUTBOX_PROCESSING,
            EmailOutbox.lease_expires_at < current,
            EmailOutbox.attempt_count >= EmailOutbox.max_attempts,
        )
        .values(
            status=OUTBOX_DEAD,
            recipient=None,
            payload=None,
            leased_at=None,
            lease_expires_at=None,
            lease_owner=None,
            last_error_type="lease_expired",
            last_error="delivery outcome unknown after final lease expired",
            updated_at=current,
        )
    )
    candidate_query = (
        select(EmailOutbox.id)
        .where(
            or_(
                (
                    EmailOutbox.status.in_((OUTBOX_PENDING, OUTBOX_RETRY))
                    & (EmailOutbox.available_at <= current)
                ),
                (
                    (EmailOutbox.status == OUTBOX_PROCESSING)
                    & (EmailOutbox.lease_expires_at < current)
                ),
            ),
            EmailOutbox.attempt_count < EmailOutbox.max_attempts,
            or_(EmailOutbox.expires_at.is_(None), EmailOutbox.expires_at > current),
        )
        .order_by(
            EmailOutbox.priority.asc(),
            EmailOutbox.available_at.asc(),
            EmailOutbox.created_at.asc(),
        )
        .limit(max(1, min(int(batch_size), 500)))
    )
    if (db.bind.dialect.name if db.bind is not None else "") == "postgresql":
        candidate_query = candidate_query.with_for_update(skip_locked=True)

    candidate_ids = list(db.execute(candidate_query).scalars())
    if not candidate_ids:
        db.commit()
        return []

    lease_expiry = current + timedelta(seconds=max(10, int(lease_seconds)))
    db.execute(
        update(EmailOutbox)
        .where(EmailOutbox.id.in_(candidate_ids))
        .values(
            status=OUTBOX_PROCESSING,
            leased_at=current,
            lease_expires_at=lease_expiry,
            lease_owner=str(worker_id or "worker")[:96],
            attempt_count=EmailOutbox.attempt_count + 1,
            updated_at=current,
        )
    )
    db.commit()
    return (
        db.query(EmailOutbox)
        .filter(
            EmailOutbox.id.in_(candidate_ids),
            EmailOutbox.status == OUTBOX_PROCESSING,
            EmailOutbox.lease_owner == str(worker_id or "worker")[:96],
        )
        .order_by(EmailOutbox.priority.asc(), EmailOutbox.created_at.asc())
        .all()
    )


def renew_email_lease(
    db: Session,
    row: EmailOutbox,
    *,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Keep one claimed job owned while an earlier batch item is delivered."""

    current = now or utcnow()
    normalized_worker_id = str(worker_id or "worker")[:96]
    result = db.execute(
        update(EmailOutbox)
        .where(
            EmailOutbox.id == row.id,
            EmailOutbox.status == OUTBOX_PROCESSING,
            EmailOutbox.lease_owner == normalized_worker_id,
        )
        .values(
            lease_expires_at=current
            + timedelta(seconds=max(10, int(lease_seconds))),
            updated_at=current,
        )
    )
    db.commit()
    if int(result.rowcount or 0) != 1:
        return False
    db.refresh(row)
    return True


def lock_email_for_delivery(
    db: Session,
    row: EmailOutbox,
    *,
    worker_id: str,
) -> EmailOutbox | None:
    """Lock an owned job until its SMTP attempt reaches a terminal decision.

    Claiming and lease renewal intentionally commit so other workers can keep
    making progress. Immediately before delivery, however, the worker holds a
    row lock through validation and SMTP. Account erasure, credential
    invalidation, and lease recovery then serialize either before the send (the
    worker observes no owned row) or after it (the mutation can redact/delete
    the already-terminal row). This closes the stale-send race without a global
    queue lock.
    """

    return (
        db.query(EmailOutbox)
        .populate_existing()
        .filter(
            EmailOutbox.id == row.id,
            EmailOutbox.status == OUTBOX_PROCESSING,
            EmailOutbox.lease_owner == str(worker_id or "worker")[:96],
        )
        .with_for_update()
        .first()
    )


def _redact_terminal_row(row: EmailOutbox) -> None:
    row.recipient = None
    row.payload = None
    row.leased_at = None
    row.lease_expires_at = None
    row.lease_owner = None


def mark_email_sent(db: Session, row: EmailOutbox, *, now: datetime | None = None) -> None:
    current = now or utcnow()
    row.status = OUTBOX_SENT
    row.sent_at = current
    row.updated_at = current
    row.last_error = None
    row.last_error_type = None
    _redact_terminal_row(row)
    db.commit()


def mark_email_cancelled(
    db: Session,
    row: EmailOutbox,
    *,
    reason: str = "stale",
    now: datetime | None = None,
) -> None:
    row.status = OUTBOX_CANCELLED
    row.last_error_type = "cancelled"
    row.last_error = str(reason or "stale")[:255]
    row.updated_at = now or utcnow()
    _redact_terminal_row(row)
    db.commit()


def mark_email_failed(
    db: Session,
    row: EmailOutbox,
    *,
    error_type: str,
    retryable: bool,
    retry_delay_seconds: int,
    now: datetime | None = None,
) -> str:
    current = now or utcnow()
    expires_at = row.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    expired = bool(expires_at and expires_at <= current)
    terminal = (not retryable) or expired or row.attempt_count >= row.max_attempts
    row.updated_at = current
    row.last_error_type = str(error_type or "delivery_error")[:64]
    # Never persist exception text: SMTP responses can echo addresses or
    # provider details. The bounded category is sufficient for operations.
    row.last_error = "delivery failed"
    if terminal:
        row.status = OUTBOX_DEAD
        _redact_terminal_row(row)
    else:
        row.status = OUTBOX_RETRY
        row.available_at = current + timedelta(seconds=max(1, int(retry_delay_seconds)))
        row.leased_at = None
        row.lease_expires_at = None
        row.lease_owner = None
    db.commit()
    return row.status


def cancel_user_email(
    db: Session,
    user_id: str,
    *,
    preserve_template_types: tuple[str, ...] = (),
    commit: bool = False,
) -> int:
    current = utcnow()
    query = db.query(EmailOutbox).filter(
        EmailOutbox.user_id == user_id,
        EmailOutbox.status.in_((OUTBOX_PENDING, OUTBOX_RETRY, OUTBOX_PROCESSING)),
    )
    if preserve_template_types:
        query = query.filter(~EmailOutbox.template_type.in_(preserve_template_types))
    count = query.update(
        {
            EmailOutbox.status: OUTBOX_CANCELLED,
            EmailOutbox.recipient: None,
            EmailOutbox.payload: None,
            EmailOutbox.leased_at: None,
            EmailOutbox.lease_expires_at: None,
            EmailOutbox.lease_owner: None,
            EmailOutbox.last_error_type: "cancelled",
            EmailOutbox.last_error: "account state changed",
            EmailOutbox.updated_at: current,
        },
        synchronize_session=False,
    )
    if commit:
        db.commit()
    return int(count or 0)


def cancel_user_security_events(
    db: Session,
    user_id: str,
    *,
    event_types: tuple[str, ...],
    commit: bool = False,
) -> int:
    """Redact active state-bound notices while the owning user is locked."""

    normalized_types = {str(value or "").strip() for value in event_types}
    normalized_types.discard("")
    if not normalized_types:
        return 0
    rows = (
        db.query(EmailOutbox)
        .filter(
            EmailOutbox.user_id == user_id,
            EmailOutbox.template_type == "security_event",
            EmailOutbox.status.in_(
                (OUTBOX_PENDING, OUTBOX_RETRY, OUTBOX_PROCESSING)
            ),
        )
        .with_for_update()
        .all()
    )
    current = utcnow()
    cancelled = 0
    for row in rows:
        if str((row.payload or {}).get("event_type") or "") not in normalized_types:
            continue
        row.status = OUTBOX_CANCELLED
        row.recipient = None
        row.payload = None
        row.leased_at = None
        row.lease_expires_at = None
        row.lease_owner = None
        row.last_error_type = "cancelled"
        row.last_error = "account state changed"
        row.updated_at = current
        cancelled += 1
    if commit:
        db.commit()
    return cancelled


def erase_user_email_state(
    db: Session,
    user_id: str,
    *,
    commit: bool = False,
) -> int:
    """Remove every outbox trace linked to a permanently erased account."""

    count = (
        db.query(EmailOutbox)
        .filter(EmailOutbox.user_id == user_id)
        .delete(synchronize_session=False)
    )
    if commit:
        db.commit()
    return int(count or 0)


def reconcile_email_security_after_restore(db: Session) -> dict[str, int]:
    """Invalidate snapshot-replayable authentication and email state.

    A full-instance restore intentionally rewinds durable business data, but it
    must not rewind bearer-token consumption. The restore coordinator calls
    this while application writes and the email worker are stopped, before it
    declares the restored instance safe to restart.
    """

    from app.auth.models import (
        Authentication,
        NativeAuthGrant,
        PendingAuthAction,
        PasswordResetToken,
        WebAuthnChallenge,
    )

    current = utcnow()
    sessions = int(
        db.query(Authentication).delete(synchronize_session=False) or 0
    )
    reset_tokens = int(
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.consumed_at.is_(None))
        .update(
            {
                PasswordResetToken.consumed_at: current,
                PasswordResetToken.requested_ip: None,
                PasswordResetToken.requested_user_agent: None,
            },
            synchronize_session=False,
        )
        or 0
    )
    webauthn_challenges = int(
        db.query(WebAuthnChallenge).delete(synchronize_session=False) or 0
    )
    native_auth_grants = int(
        db.query(NativeAuthGrant).delete(synchronize_session=False) or 0
    )
    pending_auth_actions = int(
        db.query(PendingAuthAction).delete(synchronize_session=False) or 0
    )
    pending_email_changes = int(
        db.query(PendingEmailChange).delete(synchronize_session=False) or 0
    )
    trusted_devices = int(
        db.query(TrustedDeviceNotification).delete(synchronize_session=False) or 0
    )
    rate_limits = int(
        db.query(EmailSecurityRateLimit).delete(synchronize_session=False) or 0
    )
    queued_email = int(
        db.query(EmailOutbox)
        .filter(
            EmailOutbox.status.in_(
                (OUTBOX_PENDING, OUTBOX_RETRY, OUTBOX_PROCESSING)
            )
        )
        .update(
            {
                EmailOutbox.status: OUTBOX_CANCELLED,
                EmailOutbox.recipient: None,
                EmailOutbox.payload: None,
                EmailOutbox.leased_at: None,
                EmailOutbox.lease_expires_at: None,
                EmailOutbox.lease_owner: None,
                EmailOutbox.last_error_type: "restore_invalidated",
                EmailOutbox.last_error: "invalidated by restore",
                EmailOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
        or 0
    )
    rotate_email_security_action_epoch(db)
    db.commit()

    # Database rows are authoritative, but clear optional Redis acceleration
    # too so a restored instance never accepts an entry cached before restore.
    from app.auth.session_store import revoke_all_sessions

    revoke_all_sessions()
    return {
        "sessions": sessions,
        "password_reset_tokens": reset_tokens,
        "webauthn_challenges": webauthn_challenges,
        "native_auth_grants": native_auth_grants,
        "pending_auth_actions": pending_auth_actions,
        "pending_email_changes": pending_email_changes,
        "trusted_devices": trusted_devices,
        "rate_limits": rate_limits,
        "queued_email": queued_email,
    }


def expire_email_rows(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = 1000,
) -> int:
    current = now or utcnow()
    row_ids = list(
        db.execute(
            select(EmailOutbox.id)
            .where(
                EmailOutbox.expires_at.is_not(None),
                EmailOutbox.expires_at <= current,
                EmailOutbox.status.in_(
                    (OUTBOX_PENDING, OUTBOX_RETRY, OUTBOX_PROCESSING)
                ),
            )
            .order_by(EmailOutbox.expires_at.asc())
            .limit(max(1, min(int(batch_size), 5000)))
        ).scalars()
    )
    if row_ids:
        db.execute(
            update(EmailOutbox)
            .where(EmailOutbox.id.in_(row_ids))
            .values(
                status=OUTBOX_CANCELLED,
                recipient=None,
                payload=None,
                leased_at=None,
                lease_expires_at=None,
                lease_owner=None,
                last_error_type="expired",
                last_error="delivery expired",
                updated_at=current,
            )
        )
        db.commit()
    return len(row_ids)


def purge_terminal_email_rows(
    db: Session,
    *,
    retention_days: int = 7,
    now: datetime | None = None,
    batch_size: int = 1000,
) -> int:
    cutoff = (now or utcnow()) - timedelta(days=max(1, int(retention_days)))
    row_ids = list(
        db.execute(
            select(EmailOutbox.id)
            .where(
                EmailOutbox.status.in_(OUTBOX_TERMINAL_STATUSES),
                EmailOutbox.updated_at < cutoff,
            )
            .order_by(EmailOutbox.updated_at.asc())
            .limit(max(1, min(int(batch_size), 5000)))
        ).scalars()
    )
    if not row_ids:
        return 0
    result = db.execute(
        delete(EmailOutbox).where(EmailOutbox.id.in_(row_ids))
    )
    db.commit()
    return int(result.rowcount or 0)


def purge_stale_email_security_state(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = 1000,
) -> int:
    """Bound retention for transient device and email-change security state."""

    current = now or utcnow()
    bounded_size = max(1, min(int(batch_size), 5000))
    removed = 0

    expired_change_ids = list(
        db.execute(
            select(PendingEmailChange.id)
            .where(
                PendingEmailChange.status == EMAIL_CHANGE_PENDING,
                PendingEmailChange.expires_at <= current,
            )
            .order_by(PendingEmailChange.expires_at.asc())
            .limit(bounded_size)
        ).scalars()
    )
    if expired_change_ids:
        db.execute(
            update(PendingEmailChange)
            .where(PendingEmailChange.id.in_(expired_change_ids))
            .values(status=EMAIL_CHANGE_EXPIRED)
        )

    terminal_change_ids = list(
        db.execute(
            select(PendingEmailChange.id)
            .where(
                PendingEmailChange.status.in_(
                    (
                        EMAIL_CHANGE_COMPLETED,
                        EMAIL_CHANGE_CANCELLED,
                        EMAIL_CHANGE_EXPIRED,
                    )
                ),
                PendingEmailChange.expires_at < current - timedelta(days=30),
            )
            .order_by(PendingEmailChange.expires_at.asc())
            .limit(bounded_size)
        ).scalars()
    )
    if terminal_change_ids:
        result = db.execute(
            delete(PendingEmailChange).where(
                PendingEmailChange.id.in_(terminal_change_ids)
            )
        )
        removed += int(result.rowcount or 0)

    stale_device_ids = list(
        db.execute(
            select(TrustedDeviceNotification.id)
            .where(
                TrustedDeviceNotification.last_seen_at
                < current - timedelta(days=400)
            )
            .order_by(TrustedDeviceNotification.last_seen_at.asc())
            .limit(bounded_size)
        ).scalars()
    )
    if stale_device_ids:
        result = db.execute(
            delete(TrustedDeviceNotification).where(
                TrustedDeviceNotification.id.in_(stale_device_ids)
            )
        )
        removed += int(result.rowcount or 0)

    stale_rate_limit_keys = list(
        db.execute(
            select(EmailSecurityRateLimit.bucket_key)
            .where(EmailSecurityRateLimit.expires_at <= current)
            .order_by(EmailSecurityRateLimit.expires_at.asc())
            .limit(bounded_size)
        ).scalars()
    )
    if stale_rate_limit_keys:
        result = db.execute(
            delete(EmailSecurityRateLimit).where(
                EmailSecurityRateLimit.bucket_key.in_(stale_rate_limit_keys)
            )
        )
        removed += int(result.rowcount or 0)

    if expired_change_ids or terminal_change_ids or stale_device_ids or stale_rate_limit_keys:
        db.commit()
    return removed + len(expired_change_ids)


def create_email_change_secrets() -> tuple[str, str]:
    return secrets.token_urlsafe(32), secrets.token_urlsafe(32)
