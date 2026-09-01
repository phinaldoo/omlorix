"""Bounded, privacy-preserving concurrency metrics for the admin dashboard.

The request path records at most one presence row per user and fixed five-minute
bucket. A compact bucket table is updated in the same savepoint, which keeps the
dashboard query proportional to the roughly 2,016 buckets in a week rather than
to every active user/bucket pair.

Presence metrics are deliberately best effort. Authentication and the user's
``last_active_at`` timestamp must remain available if this optional analytics
write fails. Retention work therefore runs in an isolated background worker,
never in an authenticated request.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from app.database import Base, SessionLocal
from app.auth.jwt_material import get_jwt_material
from app.users.models import User
from app.utils.background import start_named_worker, stop_named_worker
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CONCURRENCY_WINDOW_MINUTES = 5
REPORTING_WINDOW_DAYS = 7
RETENTION_WINDOW_DAYS = 8
MAINTENANCE_INTERVAL_SECONDS = 3600

# PostgreSQL transaction-scoped advisory locks ensure that only one API replica
# performs retention work. SQLite is a development fallback, so a process-local
# lock is sufficient there and avoids database-specific lock emulation.
_MAINTENANCE_ADVISORY_LOCK_KEY = 740_091_231
_LOCAL_MAINTENANCE_LOCK = threading.Lock()
_WORKER_NAME = "concurrency_metrics_maintenance"


class ConcurrencyMetricsResult(TypedDict):
    """Stable result contract returned to the admin dashboard service."""

    max_concurrent_users_last_week: int
    tracking_started_at: str | None
    is_partial_window: bool
    window_minutes: int


class UserActivityPresence(Base):
    """Deduplicate a pseudonymous user within one fixed activity bucket."""

    __tablename__ = "user_activity_presence"

    # The composite primary-key index already begins with ``bucket_start`` and
    # therefore serves retention scans; a second single-column index is wasteful.
    bucket_start = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    user_fingerprint = Column(String(64), primary_key=True, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), nullable=False)


class ConcurrencyBucketMetric(Base):
    """Store the incrementally maintained unique-user count for one bucket."""

    __tablename__ = "concurrency_bucket_metrics"
    __table_args__ = (
        CheckConstraint(
            "unique_users >= 0",
            name="ck_concurrency_bucket_metrics_nonnegative",
        ),
    )

    bucket_start = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    unique_users = Column(Integer, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class ConcurrencyMetricsState(Base):
    """Persist tracking provenance independently from rolling retention data."""

    __tablename__ = "concurrency_metrics_state"
    __table_args__ = (
        CheckConstraint(
            "singleton_id = 1", name="ck_concurrency_metrics_state_singleton"
        ),
    )

    singleton_id = Column(
        Integer,
        primary_key=True,
        nullable=False,
        autoincrement=False,
    )
    tracking_started_at = Column(DateTime(timezone=True), nullable=False)


def normalize_utc_datetime(value: datetime) -> datetime:
    """Return an aware UTC datetime and reject non-datetime caller input."""

    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    if value.tzinfo is None:
        # SQLAlchemy's SQLite adapter returns naive values even for timezone-aware
        # columns. Application timestamps are defined as UTC, so attach UTC here.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def floor_to_five_minute_bucket(dt: datetime) -> datetime:
    """Floor a datetime to the nearest UTC five-minute bucket boundary."""

    normalized = normalize_utc_datetime(dt)
    floored_minute = normalized.minute - (
        normalized.minute % CONCURRENCY_WINDOW_MINUTES
    )
    return normalized.replace(minute=floored_minute, second=0, microsecond=0)


def build_user_activity_fingerprint(user_id: str, secret: str) -> str:
    """Build a domain-separated HMAC-SHA256 fingerprint for a user ID."""

    if not isinstance(user_id, str) or not user_id:
        raise ValueError("user_id must be a non-empty string")
    if not isinstance(secret, str) or not secret:
        raise ValueError("secret must be a non-empty string")
    message = f"dashboard-concurrency:{user_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _get_tracking_secret(db: Session) -> str:
    """Load signing material used only to pseudonymize short-lived presence rows."""
    secret, _algorithm = get_jwt_material()
    return secret


def _dialect_insert(db: Session, table):
    """Return an INSERT builder with native conflict handling for supported databases."""

    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        return postgresql_insert(table)
    if dialect_name == "sqlite":
        return sqlite_insert(table)
    raise RuntimeError(
        f"Unsupported concurrency metrics database dialect: {dialect_name}"
    )


def _insert_presence_row(
    db: Session,
    *,
    bucket_start: datetime,
    user_fingerprint: str,
    first_seen_at: datetime,
) -> bool:
    """Insert a presence row atomically, returning False for an existing user/bucket."""

    table = UserActivityPresence.__table__
    statement = (
        _dialect_insert(db, table)
        .values(
            bucket_start=bucket_start,
            user_fingerprint=user_fingerprint,
            first_seen_at=first_seen_at,
        )
        .on_conflict_do_nothing(
            index_elements=[table.c.bucket_start, table.c.user_fingerprint]
        )
    )
    result = db.execute(statement)
    return result.rowcount == 1


def _increment_bucket_count(
    db: Session, *, bucket_start: datetime, updated_at: datetime
) -> None:
    """Atomically add one unique user to a compact bucket aggregate."""

    table = ConcurrencyBucketMetric.__table__
    statement = (
        _dialect_insert(db, table)
        .values(bucket_start=bucket_start, unique_users=1, updated_at=updated_at)
        .on_conflict_do_update(
            index_elements=[table.c.bucket_start],
            set_={
                "unique_users": table.c.unique_users + 1,
                "updated_at": updated_at,
            },
        )
    )
    db.execute(statement)


def record_user_activity_presence(
    db: Session, user: User, now: datetime | None = None
) -> bool:
    """Best-effort record of one user's presence in the current fixed bucket.

    Every metric statement, including the secret lookup, runs inside a savepoint.
    Any metrics-specific failure rolls back only that savepoint and is logged; it
    cannot poison the surrounding authentication transaction.
    """

    try:
        current_time = normalize_utc_datetime(now or datetime.now(timezone.utc))
        current_bucket = floor_to_five_minute_bucket(current_time)
        previous_last_active = getattr(user, "last_active_at", None)
        if previous_last_active is not None:
            previous_bucket = floor_to_five_minute_bucket(previous_last_active)
            if previous_bucket == current_bucket:
                return False

        with db.begin_nested():
            fingerprint = build_user_activity_fingerprint(
                user.id, _get_tracking_secret(db)
            )
            inserted = _insert_presence_row(
                db,
                bucket_start=current_bucket,
                user_fingerprint=fingerprint,
                first_seen_at=current_time,
            )
            if not inserted:
                return False
            _increment_bucket_count(
                db, bucket_start=current_bucket, updated_at=current_time
            )
        return True
    except Exception:  # noqa: BLE001 - optional analytics must not break authentication
        logger.exception("Failed to record optional user concurrency metrics")
        return False


def initialize_concurrency_metrics(db: Session, now: datetime | None = None) -> None:
    """Initialize tracking provenance and repair an empty aggregate after upgrade.

    Migrations perform the same backfill for PostgreSQL. This startup guard also
    supports SQLite's metadata-bootstrap path and makes a partially initialized
    development database deterministic.
    """

    current_time = normalize_utc_datetime(now or datetime.now(timezone.utc))
    source = select(
        UserActivityPresence.bucket_start,
        func.count(UserActivityPresence.user_fingerprint),
        func.max(UserActivityPresence.first_seen_at),
    ).group_by(UserActivityPresence.bucket_start)
    aggregate_table = ConcurrencyBucketMetric.__table__
    backfill_statement = (
        _dialect_insert(db, aggregate_table)
        .from_select(
            ["bucket_start", "unique_users", "updated_at"],
            source,
        )
        .on_conflict_do_nothing(index_elements=[aggregate_table.c.bucket_start])
    )
    # Multiple API replicas can initialize simultaneously after migrations. A
    # conflict-safe backfill makes startup idempotent and repairs missing buckets
    # without risking a duplicate-key startup failure.
    db.execute(backfill_statement)

    earliest_presence = db.query(func.min(UserActivityPresence.bucket_start)).scalar()
    tracking_started_at = (
        normalize_utc_datetime(earliest_presence)
        if earliest_presence is not None
        else current_time
    )
    state_table = ConcurrencyMetricsState.__table__
    statement = (
        _dialect_insert(db, state_table)
        .values(singleton_id=1, tracking_started_at=tracking_started_at)
        .on_conflict_do_nothing(index_elements=[state_table.c.singleton_id])
    )
    db.execute(statement)
    db.commit()


def cleanup_expired_concurrency_metrics(
    db: Session, now: datetime | None = None
) -> tuple[int, int]:
    """Delete expired detail and aggregate rows, returning both deletion counts."""

    current_time = normalize_utc_datetime(now or datetime.now(timezone.utc))
    cutoff = floor_to_five_minute_bucket(
        current_time - timedelta(days=RETENTION_WINDOW_DAYS)
    )
    presence_deleted = (
        db.query(UserActivityPresence)
        .filter(UserActivityPresence.bucket_start < cutoff)
        .delete(synchronize_session=False)
    )
    buckets_deleted = (
        db.query(ConcurrencyBucketMetric)
        .filter(ConcurrencyBucketMetric.bucket_start < cutoff)
        .delete(synchronize_session=False)
    )
    return int(presence_deleted or 0), int(buckets_deleted or 0)


def _try_acquire_maintenance_lock(db: Session) -> tuple[bool, bool]:
    """Return whether maintenance was claimed and whether a local lock was used."""

    if db.get_bind().dialect.name == "postgresql":
        acquired = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": _MAINTENANCE_ADVISORY_LOCK_KEY},
        ).scalar()
        return bool(acquired), False
    acquired = _LOCAL_MAINTENANCE_LOCK.acquire(blocking=False)
    return acquired, acquired


def run_concurrency_metrics_maintenance_once(
    now: datetime | None = None,
) -> tuple[int, int]:
    """Run one isolated, globally serialized retention pass."""

    db = SessionLocal()
    local_lock_acquired = False
    try:
        acquired, local_lock_acquired = _try_acquire_maintenance_lock(db)
        if not acquired:
            db.rollback()
            return 0, 0
        deleted = cleanup_expired_concurrency_metrics(db, now=now)
        db.commit()
        if any(deleted):
            logger.info(
                "Pruned concurrency metrics (presence_rows=%s, bucket_rows=%s)",
                deleted[0],
                deleted[1],
            )
        return deleted
    except Exception:
        db.rollback()
        logger.exception("Concurrency metrics retention pass failed")
        return 0, 0
    finally:
        if local_lock_acquired:
            _LOCAL_MAINTENANCE_LOCK.release()
        db.close()


def _concurrency_metrics_maintenance_worker(stop_event: threading.Event) -> None:
    """Periodically prune expired metrics outside latency-sensitive requests."""

    while not stop_event.is_set():
        run_concurrency_metrics_maintenance_once()
        if stop_event.wait(MAINTENANCE_INTERVAL_SECONDS):
            break


def start_concurrency_metrics_maintenance_worker():
    """Start the process-local retention poller."""

    return start_named_worker(
        _WORKER_NAME,
        _concurrency_metrics_maintenance_worker,
        logger,
        start_message="[Admin] Concurrency metrics maintenance worker started",
        already_running_message="[Admin] Concurrency metrics maintenance worker already running",
        failure_message="[Admin] Failed to start concurrency metrics maintenance worker",
    )


def stop_concurrency_metrics_maintenance_worker(timeout: float = 5.0) -> None:
    """Stop the process-local retention poller."""

    stop_named_worker(
        _WORKER_NAME,
        logger,
        timeout=timeout,
        stopped_message="[Admin] Concurrency metrics maintenance worker stopped",
        not_running_message="[Admin] Concurrency metrics maintenance worker was not running",
        failure_message="[Admin] Failed to stop concurrency metrics maintenance worker",
    )


def get_peak_concurrent_users_last_week(
    db: Session,
    now: datetime | None = None,
) -> ConcurrencyMetricsResult:
    """Return the peak compact bucket count in the rolling seven-day window."""

    current_time = normalize_utc_datetime(now or datetime.now(timezone.utc))
    state = db.get(ConcurrencyMetricsState, 1)
    tracking_started_at = None
    if state is not None:
        tracking_started_at = normalize_utc_datetime(state.tracking_started_at)
    else:
        # This read-only fallback keeps the dashboard available during a narrowly
        # timed rolling deployment before startup initialization has completed.
        earliest_presence = db.query(
            func.min(UserActivityPresence.bucket_start)
        ).scalar()
        if earliest_presence is not None:
            tracking_started_at = normalize_utc_datetime(earliest_presence)

    reporting_cutoff = floor_to_five_minute_bucket(
        current_time - timedelta(days=REPORTING_WINDOW_DAYS)
    )
    peak_value = (
        db.query(func.max(ConcurrencyBucketMetric.unique_users))
        .filter(ConcurrencyBucketMetric.bucket_start >= reporting_cutoff)
        .scalar()
    )
    is_partial_window = (
        tracking_started_at is None
        or current_time - tracking_started_at < timedelta(days=REPORTING_WINDOW_DAYS)
    )

    return {
        "max_concurrent_users_last_week": int(peak_value or 0),
        "tracking_started_at": tracking_started_at.isoformat()
        if tracking_started_at
        else None,
        "is_partial_window": is_partial_window,
        "window_minutes": CONCURRENCY_WINDOW_MINUTES,
    }
