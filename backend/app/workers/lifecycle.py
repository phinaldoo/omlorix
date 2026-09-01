from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import threading

from app.database import SessionLocal
from app.redis_client import new_lock_owner, release_lock, try_acquire_lock
from app.users.models import ACCOUNT_TYPE_TEMPORARY, User, normalize_utc_datetime
from app.workers.models import (
    QUEUE_LIFECYCLE,
    WorkerJobSnapshot,
    enqueue_worker_job,
    revive_worker_job_after_lease_expiry,
)
from app.workers.runtime import DurableQueueWorker, FatalJobError, WorkerContext, run_worker_cli


logger = logging.getLogger(__name__)
SCHEDULER_LOCK = "account_lifecycle_scheduler"
_ENABLE_VALUES = {"1", "true", "yes", "on", "external", "worker"}


def external_account_lifecycle_enabled() -> bool:
    return (
        str(os.getenv("ACCOUNT_LIFECYCLE_WORKER_MODE", "inline") or "inline")
        .strip()
        .lower()
        in _ENABLE_VALUES
    )


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat()


def enqueue_scheduled_hard_delete(
    db,
    *,
    user_id: str,
    scheduled_for: datetime,
    commit: bool = False,
):
    marker = _iso(scheduled_for)
    payload = {"user_id": str(user_id), "scheduled_for": marker}
    job = enqueue_worker_job(
        db,
        queue=QUEUE_LIFECYCLE,
        kind="hard_delete_user",
        user_id=user_id,
        payload=payload,
        idempotency_key=f"hard-delete:{user_id}:{marker}",
        priority=0,
        max_attempts=8,
        available_at=scheduled_for,
        commit=commit,
    )
    if revive_worker_job_after_lease_expiry(
        db,
        job_id=job.id,
        payload=payload,
        available_at=scheduled_for,
    ):
        if commit:
            db.commit()
            db.refresh(job)
    return job


def enqueue_temporary_account_expiry(
    db,
    *,
    user_id: str,
    expires_at: datetime,
    commit: bool = False,
):
    marker = _iso(expires_at)
    payload = {"user_id": str(user_id), "expires_at": marker}
    job = enqueue_worker_job(
        db,
        queue=QUEUE_LIFECYCLE,
        kind="expire_temporary_account",
        user_id=str(user_id),
        payload=payload,
        idempotency_key=f"temporary-expiry:{user_id}:{marker}",
        priority=10,
        max_attempts=8,
        available_at=expires_at,
        commit=commit,
    )
    if revive_worker_job_after_lease_expiry(
        db,
        job_id=job.id,
        payload=payload,
        available_at=expires_at,
    ):
        if commit:
            db.commit()
            db.refresh(job)
    return job


def _enqueue_due_lifecycle_jobs() -> int:
    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        batch_size = max(1, min(int(os.getenv("LIFECYCLE_SCHEDULER_BATCH_SIZE", "500") or "500"), 5000))
        due_deletions = (
            session.query(User.id, User.deletion_scheduled_for)
            .filter(
                User.deleted_at.isnot(None),
                User.deletion_scheduled_for.isnot(None),
                User.deletion_scheduled_for <= now,
            )
            .order_by(User.deletion_scheduled_for.asc(), User.id.asc())
            .limit(batch_size)
            .all()
        )
        count = 0
        for user_id, scheduled_for in due_deletions:
            enqueue_scheduled_hard_delete(
                session,
                user_id=str(user_id),
                scheduled_for=scheduled_for,
                commit=False,
            )
            count += 1

        remaining = max(0, batch_size - count)
        if remaining:
            expired_accounts = (
                session.query(User.id, User.temporary_expires_at)
                .filter(
                    User.account_type == ACCOUNT_TYPE_TEMPORARY,
                    User.deleted_at.is_(None),
                    User.temporary_expires_at.isnot(None),
                    User.temporary_expires_at <= now,
                )
                .order_by(User.temporary_expires_at.asc(), User.id.asc())
                .limit(remaining)
                .all()
            )
            for user_id, expires_at in expired_accounts:
                enqueue_temporary_account_expiry(
                    session,
                    user_id=str(user_id),
                    expires_at=expires_at,
                    commit=False,
                )
                count += 1
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _scheduled_marker_matches(value: datetime | None, marker: str) -> bool:
    return bool(value and marker and _iso(value) == marker)


def _hard_delete(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    from app.logging.worker import _write_scheduled_user_deletion_audit_event
    from app.users.models import hard_delete_user

    user_id = str(job.payload.get("user_id") or "").strip()
    marker = str(job.payload.get("scheduled_for") or "").strip()
    if not user_id or user_id != str(job.user_id or "") or not marker:
        raise FatalJobError("invalid_payload")
    session = SessionLocal()
    try:
        context.raise_if_cancelled()
        user = session.query(User).filter(User.id == user_id).with_for_update().first()
        if user is None:
            return {"user_id": user_id, "status": "already_deleted"}
        now = datetime.now(timezone.utc)
        scheduled_for = normalize_utc_datetime(user.deletion_scheduled_for)
        if (
            user.deleted_at is None
            or scheduled_for is None
            or scheduled_for > now
            or not _scheduled_marker_matches(scheduled_for, marker)
        ):
            return {"user_id": user_id, "status": "schedule_changed"}
        deleted_at = normalize_utc_datetime(user.deleted_at)
        _write_scheduled_user_deletion_audit_event(
            "SCHEDULED_HARD_DELETE_USER_STARTED",
            user_id=user_id,
            deleted_at=deleted_at,
            scheduled_for=scheduled_for,
        )
        deleted = hard_delete_user(
            session,
            user_id,
            allow_administrative_target=True,
        )
        if not deleted:
            return {"user_id": user_id, "status": "already_deleted"}
        try:
            _write_scheduled_user_deletion_audit_event(
                "SCHEDULED_HARD_DELETE_USER_COMPLETED",
                user_id=user_id,
                deleted_at=deleted_at,
                scheduled_for=scheduled_for,
            )
        except Exception:
            logger.exception("Failed writing scheduled hard-delete completion audit user=%s", user_id)
        return {"status": "deleted"}
    finally:
        session.close()


def _expire_temporary(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    from app.auth.models import delete_authentication_all
    from app.auth.session_store import revoke_user_sessions
    from app.groups.temporary_account_retention import mark_temporary_account_for_retention
    from app.logging.worker import _write_temporary_account_expiry_audit_event

    user_id = str(job.payload.get("user_id") or "").strip()
    marker = str(job.payload.get("expires_at") or "").strip()
    if not user_id or user_id != str(job.user_id or "") or not marker:
        raise FatalJobError("invalid_payload")
    session = SessionLocal()
    try:
        context.raise_if_cancelled()
        account = session.query(User).filter(User.id == user_id).with_for_update().first()
        if account is None:
            return {"status": "already_deleted"}
        expires_at = normalize_utc_datetime(account.temporary_expires_at)
        if (
            account.account_type != ACCOUNT_TYPE_TEMPORARY
            or account.deleted_at is not None
            or expires_at is None
            or expires_at > datetime.now(timezone.utc)
            or not _scheduled_marker_matches(expires_at, marker)
        ):
            return {"status": "expiry_changed"}
        policy = mark_temporary_account_for_retention(
            account,
            session,
            lifecycle_at=expires_at,
        )
        _write_temporary_account_expiry_audit_event(
            user_id=account.id,
            group_id=account.group_id,
            expires_at=expires_at,
            retention_mode=policy["mode"],
            scheduled_for=policy["purge_scheduled_at"],
        )
        delete_authentication_all(
            session,
            account.id,
            commit=False,
            revoke_cached=False,
        )
        if policy.get("purge_scheduled_at"):
            enqueue_scheduled_hard_delete(
                session,
                user_id=account.id,
                scheduled_for=policy["purge_scheduled_at"],
                commit=False,
            )
        session.commit()
        try:
            revoke_user_sessions(account.id)
        except Exception:
            logger.exception("Failed revoking cached sessions for expired temporary account %s", account.id)
        return {"status": "expired", "retention_mode": policy["mode"]}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class AccountLifecycleWorker(DurableQueueWorker):
    def __init__(self) -> None:
        super().__init__(
            queue=QUEUE_LIFECYCLE,
            handlers={
                "hard_delete_user": _hard_delete,
                "expire_temporary_account": _expire_temporary,
            },
            default_lease_seconds=180,
        )
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: threading.Thread | None = None

    def request_stop(self, *_args) -> None:
        super().request_stop(*_args)
        self._scheduler_stop.set()

    def _scheduler_loop(self) -> None:
        interval = max(10, min(int(os.getenv("LIFECYCLE_SCHEDULER_INTERVAL_SECONDS", "60") or "60"), 3600))
        while not self._scheduler_stop.is_set():
            owner = new_lock_owner()
            try:
                if try_acquire_lock(SCHEDULER_LOCK, owner, max(90, interval + 30)):
                    _enqueue_due_lifecycle_jobs()
            except Exception:
                logger.exception("Account lifecycle scheduler pass failed")
            finally:
                release_lock(SCHEDULER_LOCK, owner)
            self._scheduler_stop.wait(interval)

    def run_forever(self) -> None:
        self._scheduler_stop.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="account-lifecycle-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()
        try:
            super().run_forever()
        finally:
            self._scheduler_stop.set()
            self._scheduler_thread.join(timeout=10)


def build_worker() -> AccountLifecycleWorker:
    return AccountLifecycleWorker()


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
