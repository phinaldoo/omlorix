from __future__ import annotations

from datetime import datetime, timezone, timedelta
import asyncio
import threading
import logging

from sqlalchemy.orm import Session

from app.database import AuditSessionLocal, SessionLocal
from app.logging.models import (
    AuditLogDeletionQueue,
    AuthLogDeletionQueue,
    audit_log_erasure_guard,
    create_audit_log,
    delete_admin_notifications_for_user,
    delete_audit_logs_for_user,
    delete_authentication_logs_for_user,
    delete_authentication_logs_older_than,
    ensure_admin_notification_partitions,
    ensure_audit_log_partitions,
    prune_authentication_logs_to_max_count,
)
from app.redis_client import new_lock_owner, release_lock, try_acquire_lock
from app.settings.defaults import DEFAULT_SETTINGS
from app.settings.utils import coerce_bool, get_settings_page_data
from app.utils.background import start_named_worker, stop_named_worker


logger = logging.getLogger(__name__)

WORKER_NAME = "auth_log_retention"
SLEEP_INTERVAL_SECONDS = 300
MAX_BATCH_SIZE = 100
RETRY_DELAY_MINUTES = 30
WORKER_LOCK_NAME = "auth_log_retention_worker"
WORKER_LOCK_TTL_SECONDS = 10 * 60


def prepare_logging_partitions() -> int:
    created = 0

    audit_session: Session = AuditSessionLocal()
    try:
        created += ensure_audit_log_partitions(audit_session)
        audit_session.commit()
    except Exception:
        audit_session.rollback()
        logger.exception("[Retention] Failed preparing audit log partitions")
        raise
    finally:
        audit_session.close()

    main_session: Session = SessionLocal()
    try:
        created += ensure_admin_notification_partitions(main_session)
        main_session.commit()
    except Exception:
        main_session.rollback()
        logger.exception("[Retention] Failed preparing admin notification partitions")
        raise
    finally:
        main_session.close()

    if created:
        logger.info("[Retention] Prepared %s monthly log partitions", created)
    return created


def _safe_int_setting(value, *, default: int, minimum: int) -> int:
    """Coerce admin settings to bounded integers for unattended worker use."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, minimum)


def _load_security_settings() -> dict:
    """Load security settings with defaults so incomplete rows keep intended behavior."""
    defaults = dict(DEFAULT_SETTINGS.get("security", {}))
    session: Session = SessionLocal()
    try:
        current_values = get_settings_page_data(session, "security")
        if isinstance(current_values, dict):
            defaults.update(current_values)
    except Exception:
        logger.exception("[Retention] Failed loading security settings; using defaults")
    finally:
        try:
            session.close()
        except Exception:
            pass
    return defaults


def _auth_log_cleanup_interval_seconds(settings: dict | None = None) -> int:
    """Return the configured auth-log cleanup cadence."""
    settings = settings or _load_security_settings()
    default_interval = int(
        DEFAULT_SETTINGS.get("security", {}).get("auth_logs_cleanup_interval_seconds", SLEEP_INTERVAL_SECONDS)
    )
    return _safe_int_setting(
        settings.get("auth_logs_cleanup_interval_seconds"),
        default=default_interval,
        minimum=60,
    )


def _worker_wait_seconds(settings: dict | None = None) -> int:
    """Wake often enough for auth-log cleanup while preserving the base retention cadence."""
    return min(SLEEP_INTERVAL_SECONDS, _auth_log_cleanup_interval_seconds(settings))


def _auth_log_cleanup_due(
    last_cleanup_at: datetime | None,
    now: datetime,
    interval_seconds: int,
) -> bool:
    """Decide whether the general authentication-log cleanup should run."""
    if last_cleanup_at is None:
        return True
    return now >= last_cleanup_at + timedelta(seconds=interval_seconds)


def _process_authentication_log_auto_cleanup(
    audit_session: Session,
    settings: dict | None = None,
) -> bool:
    """Apply the general authentication-log retention policy from security settings."""
    settings = settings or _load_security_settings()
    if not coerce_bool(settings.get("auth_logs_auto_cleanup_enabled"), default=True):
        return False

    mode = str(
        settings.get(
            "auth_logs_cleanup_mode",
            DEFAULT_SETTINGS.get("security", {}).get("auth_logs_cleanup_mode", "age"),
        )
    ).strip().lower()

    if mode == "count":
        max_count = _safe_int_setting(
            settings.get("auth_logs_max_count"),
            default=int(DEFAULT_SETTINGS.get("security", {}).get("auth_logs_max_count", 100000)),
            minimum=0,
        )
        deleted = prune_authentication_logs_to_max_count(audit_session, max_count)
    else:
        max_age_days = _safe_int_setting(
            settings.get("auth_logs_max_age_days"),
            default=int(DEFAULT_SETTINGS.get("security", {}).get("auth_logs_max_age_days", 90)),
            minimum=1,
        )
        deleted = delete_authentication_logs_older_than(audit_session, max_age_days)

    if deleted:
        logger.info("[Retention] Deleted %s authentication log rows by %s policy", deleted, mode)
    return bool(deleted)


def _retention_worker(stop_event: threading.Event):
    """Continuously process log/security retention maintenance."""
    logger.info("[Retention] Worker started.")
    last_auth_log_cleanup_at: datetime | None = None
    while not stop_event.is_set():
        lock_owner = new_lock_owner()
        if not try_acquire_lock(WORKER_LOCK_NAME, lock_owner, WORKER_LOCK_TTL_SECONDS):
            if stop_event.wait(_worker_wait_seconds()):
                break
            continue

        try:
            processed_any = False
            security_settings = _load_security_settings()
            cleanup_interval_seconds = _auth_log_cleanup_interval_seconds(security_settings)
            try:
                processed_any = bool(prepare_logging_partitions()) or processed_any
            except Exception:
                logger.exception("[Retention] Partition preparation failed")

            # Process auth log deletion jobs
            audit_session: Session = AuditSessionLocal()
            try:
                now = datetime.now(timezone.utc)
                jobs = (
                    audit_session.query(AuthLogDeletionQueue)
                    .filter(AuthLogDeletionQueue.status.in_(("pending", "retry")))
                    .filter(AuthLogDeletionQueue.scheduled_for <= now)
                    .order_by(AuthLogDeletionQueue.scheduled_for.asc())
                    .limit(MAX_BATCH_SIZE)
                    .all()
                )

                for job in jobs:
                    if stop_event.is_set():
                        break
                    if not _acquire_auth_log_job(audit_session, job):
                        continue
                    processed_any = True
                    _process_auth_log_job(audit_session, job)

                audit_jobs = (
                    audit_session.query(AuditLogDeletionQueue)
                    .filter(AuditLogDeletionQueue.status.in_(("pending", "retry")))
                    .filter(AuditLogDeletionQueue.scheduled_for <= now)
                    .order_by(AuditLogDeletionQueue.scheduled_for.asc())
                    .limit(MAX_BATCH_SIZE)
                    .all()
                )

                for audit_job in audit_jobs:
                    if stop_event.is_set():
                        break
                    if not _acquire_audit_log_job(audit_session, audit_job):
                        continue
                    processed_any = True
                    _process_audit_log_job(audit_session, audit_job)

                now = datetime.now(timezone.utc)
                if _auth_log_cleanup_due(last_auth_log_cleanup_at, now, cleanup_interval_seconds):
                    last_auth_log_cleanup_at = now
                    processed_any = (
                        _process_authentication_log_auto_cleanup(audit_session, security_settings)
                        or processed_any
                    )
            except Exception:
                audit_session.rollback()
                logger.exception("[Retention] Auth log cleanup loop failed")
            finally:
                try:
                    audit_session.close()
                except Exception:
                    pass

            # Account expiry and hard deletion belong to the dedicated
            # account-lifecycle worker. This process retains only maintenance.
            main_session: Session = SessionLocal()
            try:
                processed_any = _process_password_reset_token_retention(main_session) or processed_any
                processed_any = _process_ip_address_security_statistics_retention(main_session) or processed_any
            except Exception:
                main_session.rollback()
                logger.exception("[Retention] Main-database retention loop failed")
            finally:
                try:
                    main_session.close()
                except Exception:
                    pass

            if not processed_any:
                if stop_event.wait(_worker_wait_seconds(security_settings)):
                    break
        finally:
            release_lock(WORKER_LOCK_NAME, lock_owner)

    logger.info("[Retention] Worker stopped.")


def _process_ip_address_security_statistics_retention(db: Session) -> bool:
    """Purge expired rows and enrich a bounded pending Geo-IP batch."""
    from app.auth.models import delete_expired_ip_address_security_statistics
    from app.ip_analytics.service import enrich_pending_country_codes

    deleted = delete_expired_ip_address_security_statistics(db)
    enriched = asyncio.run(enrich_pending_country_codes(db))
    if deleted:
        logger.info("[Retention] Deleted %s expired IP address security statistic rows", deleted)
    if enriched:
        logger.info("[Retention] Enriched %s IP address security statistic rows", enriched)
    return bool(deleted or enriched)


def _process_password_reset_token_retention(db: Session) -> bool:
    """Purge expired password reset tokens and their request metadata."""
    from app.auth.models import delete_expired_password_reset_tokens

    deleted = delete_expired_password_reset_tokens(db)
    if deleted:
        logger.info("[Retention] Deleted %s expired password reset token rows", deleted)
    return bool(deleted)


def _scheduled_user_deletion_audit_details(
    user_id: str,
    *,
    deleted_at: datetime | None,
    scheduled_for: datetime | None,
) -> dict[str, str | None]:
    return {
        "deleted_user_id": user_id,
        "deleted_at": deleted_at.isoformat() if deleted_at else None,
        "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
    }


def _write_scheduled_user_deletion_audit_event(
    action: str,
    *,
    user_id: str,
    deleted_at: datetime | None,
    scheduled_for: datetime | None,
    error: str | None = None,
) -> None:
    details = _scheduled_user_deletion_audit_details(
        user_id,
        deleted_at=deleted_at,
        scheduled_for=scheduled_for,
    )
    if error:
        details["error"] = error

    audit_session = AuditSessionLocal()
    try:
        create_audit_log(
            db_log=audit_session,
            user_id="system",
            action=action,
            details=details,
            category="system",
        )
    finally:
        try:
            audit_session.close()
        except Exception:
            pass


def _write_temporary_account_expiry_audit_event(
    *,
    user_id: str,
    group_id: str,
    expires_at: datetime,
    retention_mode: str,
    scheduled_for: datetime | None,
) -> None:
    """Record the durable transition from usable account to retained data."""

    audit_session = AuditSessionLocal()
    try:
        create_audit_log(
            db_log=audit_session,
            user_id="system",
            action="TEMPORARY_ACCOUNT_EXPIRED",
            details={
                "temporary_user_id": user_id,
                "group_id": group_id,
                "expired_at": expires_at.isoformat(),
                "retention_mode": retention_mode,
                "deletion_scheduled_for": (
                    scheduled_for.isoformat() if scheduled_for else None
                ),
            },
            category="system",
        )
    finally:
        try:
            audit_session.close()
        except Exception:
            pass


def _process_expired_temporary_accounts(
    db: Session,
    stop_event: threading.Event,
) -> bool:
    """Enter expired temporary users into the configured retention lifecycle."""

    from app.auth.models import delete_authentication_all
    from app.auth.session_store import revoke_user_sessions
    from app.groups.temporary_account_retention import mark_temporary_account_for_retention
    from app.users.models import User, normalize_utc_datetime

    now = datetime.now(timezone.utc)
    accounts = (
        db.query(User)
        .filter(User.account_type == "temporary")
        .filter(User.deleted_at.is_(None))
        .filter(User.temporary_expires_at.isnot(None))
        .filter(User.temporary_expires_at <= now)
        .order_by(User.temporary_expires_at.asc())
        .limit(MAX_BATCH_SIZE)
        .all()
    )
    processed_any = False

    for account in accounts:
        if stop_event.is_set():
            break
        expires_at = normalize_utc_datetime(account.temporary_expires_at)
        if expires_at is None:
            # The query excludes nulls, but remain fail-closed if a custom
            # dialect or malformed row violates that assumption.
            continue
        try:
            policy = mark_temporary_account_for_retention(
                account,
                db,
                lifecycle_at=expires_at,
            )
            try:
                _write_temporary_account_expiry_audit_event(
                    user_id=account.id,
                    group_id=account.group_id,
                    expires_at=expires_at,
                    retention_mode=policy["mode"],
                    scheduled_for=policy["purge_scheduled_at"],
                )
            except Exception:
                # Preserve the account and retry next pass if the audit store
                # cannot record the lifecycle transition.
                db.rollback()
                logger.exception(
                    "[Retention] Failed to audit temporary-account expiry for %s; leaving it pending",
                    account.id,
                )
                continue

            delete_authentication_all(
                db,
                account.id,
                commit=False,
                revoke_cached=False,
            )
            db.commit()
            processed_any = True
            try:
                revoke_user_sessions(account.id)
            except Exception:
                # Database-backed authentication is already removed and every
                # request still checks expiry, so a cache outage is safe to
                # retry opportunistically without rolling back lifecycle state.
                logger.exception(
                    "[Retention] Failed to revoke cached sessions for expired temporary account %s",
                    account.id,
                )
        except Exception:
            db.rollback()
            logger.exception(
                "[Retention] Failed to process expired temporary account %s",
                account.id,
            )

    return processed_any


def _process_scheduled_user_deletions(db: Session, stop_event: threading.Event) -> bool:
    """Process users scheduled for permanent deletion."""
    from app.users.models import User, hard_delete_user

    now = datetime.now(timezone.utc)
    processed_any = False

    # Find users with deletion_scheduled_for <= now
    users_to_delete = (
        db.query(User)
        .filter(User.deleted_at.isnot(None))
        .filter(User.deletion_scheduled_for.isnot(None))
        .filter(User.deletion_scheduled_for <= now)
        .limit(MAX_BATCH_SIZE)
        .all()
    )

    for user in users_to_delete:
        if stop_event.is_set():
            break
        user_id = user.id
        scheduled_for = user.deletion_scheduled_for
        deleted_at = user.deleted_at
        try:
            logger.info("[Retention] Hard deleting user %s (scheduled for %s)", user_id, scheduled_for)

            try:
                _write_scheduled_user_deletion_audit_event(
                    "SCHEDULED_HARD_DELETE_USER_STARTED",
                    user_id=user_id,
                    deleted_at=deleted_at,
                    scheduled_for=scheduled_for,
                )
            except Exception:
                logger.exception(
                    "[Retention] Failed to record scheduled hard-delete audit entry for user %s; leaving user pending",
                    user_id,
                )
                continue

            # The original soft deletion was authorized before this worker
            # received it. Explicitly carry that authorization so a scheduled
            # administrator deletion can reach its configured retention end.
            deleted = hard_delete_user(
                db,
                user_id,
                allow_administrative_target=True,
            )
            if not deleted:
                raise RuntimeError(f"user {user_id} disappeared before scheduled hard delete")
            processed_any = True
            try:
                _write_scheduled_user_deletion_audit_event(
                    "SCHEDULED_HARD_DELETE_USER_COMPLETED",
                    user_id=user_id,
                    deleted_at=deleted_at,
                    scheduled_for=scheduled_for,
                )
            except Exception:
                logger.exception(
                    "[Retention] Failed to record scheduled hard-delete completion audit entry for user %s",
                    user_id,
                )
            logger.info("[Retention] Successfully hard deleted user %s", user_id)
        except Exception as exc:
            db.rollback()
            try:
                _write_scheduled_user_deletion_audit_event(
                    "SCHEDULED_HARD_DELETE_USER_FAILED",
                    user_id=user_id,
                    deleted_at=deleted_at,
                    scheduled_for=scheduled_for,
                    error=str(exc),
                )
            except Exception:
                logger.exception(
                    "[Retention] Failed to record scheduled hard-delete failure audit entry for user %s",
                    user_id,
                )
            logger.exception("[Retention] Failed to hard delete user %s", user.id)

    return processed_any


def _acquire_auth_log_job(session: Session, job: AuthLogDeletionQueue) -> bool:
    """Mark the job as processing to avoid duplicate handling."""
    try:
        job.status = "processing"
        job.attempts = (job.attempts or 0) + 1
        job.last_error = None
        job.processed_at = None
        session.commit()
        session.refresh(job)
        return True
    except Exception:
        session.rollback()
        logger.exception("[AuthLogs] Failed to lock job %s", job.id)
        return False


def _process_auth_log_job(session: Session, job: AuthLogDeletionQueue) -> None:
    delete_session = AuditSessionLocal()
    try:
        delete_authentication_logs_for_user(delete_session, job.user_id)
    except Exception as exc:
        logger.exception("[AuthLogs] Failed deleting logs for user %s", job.user_id)
        _mark_job_retry(session, job, str(exc))
    else:
        _mark_job_completed(session, job)
    finally:
        try:
            delete_session.close()
        except Exception:
            pass


def _mark_job_completed(session: Session, job: AuthLogDeletionQueue) -> None:
    try:
        job.status = "completed"
        job.processed_at = datetime.now(timezone.utc)
        job.last_error = None
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("[AuthLogs] Failed marking job %s completed", job.id)


def _mark_job_retry(session: Session, job: AuthLogDeletionQueue, error_message: str | None) -> None:
    try:
        job.status = "retry"
        job.last_error = (error_message or "")[:2000]
        job.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=RETRY_DELAY_MINUTES)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("[AuthLogs] Failed scheduling retry for job %s", job.id)


def _acquire_audit_log_job(session: Session, job: AuditLogDeletionQueue) -> bool:
    try:
        locked_job = (
            session.query(AuditLogDeletionQueue)
            .filter(
                AuditLogDeletionQueue.id == job.id,
                AuditLogDeletionQueue.status.in_(("pending", "retry")),
            )
            .with_for_update()
            .populate_existing()
            .first()
        )
        if locked_job is None:
            session.rollback()
            return False
        locked_job.status = "processing"
        locked_job.attempts = (locked_job.attempts or 0) + 1
        locked_job.last_error = None
        locked_job.processed_at = None
        session.commit()
        session.refresh(locked_job)
        return True
    except Exception:
        session.rollback()
        logger.exception("[AuditLogs] Failed to lock job %s", job.id)
        return False


def _process_audit_log_job(session: Session, job: AuditLogDeletionQueue) -> None:
    locked_job = (
        session.query(AuditLogDeletionQueue)
        .filter(
            AuditLogDeletionQueue.id == job.id,
            AuditLogDeletionQueue.status == "processing",
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if locked_job is None:
        session.rollback()
        return

    main_session = SessionLocal()
    delete_session = AuditSessionLocal()
    try:
        # Serialize with account restoration on the authoritative user row. If
        # restoration committed first, the stale processing lease is cancelled
        # without re-establishing the audit-erasure fence. If deletion won the
        # row lock first, restoration subsequently clears that fence atomically.
        from app.users.models import User

        with audit_log_erasure_guard(
            locked_job.user_id,
            bind=main_session.get_bind(),
        ) as guard_db:
            user = (
                main_session.query(User)
                .filter(User.id == locked_job.user_id)
                .with_for_update()
                .populate_existing()
                .first()
            )
            if user is not None and user.deleted_at is None:
                locked_job.status = "cancelled"
                locked_job.processed_at = datetime.now(timezone.utc)
                locked_job.last_error = None
                session.commit()
                return

            delete_audit_logs_for_user(
                delete_session,
                locked_job.user_id,
                main_db=main_session,
                erasure_guard_db=guard_db,
            )
            delete_admin_notifications_for_user(delete_session, locked_job.user_id)
    except Exception as exc:
        logger.exception(
            "[AuditLogs] Failed deleting logs/notifications for user %s",
            locked_job.user_id,
        )
        _mark_audit_job_retry(session, locked_job, str(exc))
    else:
        _mark_audit_job_completed(session, locked_job)
    finally:
        try:
            main_session.close()
        except Exception:
            pass
        try:
            delete_session.close()
        except Exception:
            pass


def _mark_audit_job_completed(session: Session, job: AuditLogDeletionQueue) -> None:
    try:
        job.status = "completed"
        job.processed_at = datetime.now(timezone.utc)
        job.last_error = None
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("[AuditLogs] Failed marking job %s completed", job.id)


def _mark_audit_job_retry(session: Session, job: AuditLogDeletionQueue, error_message: str | None) -> None:
    try:
        job.status = "retry"
        job.last_error = (error_message or "")[:2000]
        job.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=RETRY_DELAY_MINUTES)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("[AuditLogs] Failed scheduling retry for job %s", job.id)


def start_auth_log_retention_worker():
    return start_named_worker(
        WORKER_NAME,
        _retention_worker,
        logger,
        start_message="[Retention] Worker starting...",
        already_running_message="[Retention] Worker already running.",
        failure_message="[Retention] Failed to start worker",
    )


def stop_auth_log_retention_worker(timeout: float = 5.0):
    stop_named_worker(
        WORKER_NAME,
        logger,
        timeout=timeout,
        stopped_message="[Retention] Worker stopped.",
        not_running_message="[Retention] Worker was not running.",
        failure_message="[Retention] Failed to stop worker",
    )
