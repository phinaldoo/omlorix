from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import re
import uuid
from typing import Any

from app.database import AuditSessionLocal, SessionLocal
from app.workers.models import (
    AuditEventOutbox,
    AuditEventSubjectReference,
    DurableWorkerJob,
    JOB_ACTIVE_STATUSES,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_PROCESSING,
    JOB_SUCCEEDED,
    QUEUE_EVENTS,
    WorkerJobSnapshot,
    audit_event_subject_fingerprint,
    enqueue_worker_job,
    lock_audit_event_subject_states,
    lock_unreconciled_terminal_jobs,
    utcnow,
)
from app.workers.runtime import (
    DurableQueueWorker,
    FatalJobError,
    JobCancelled,
    RetryableJobError,
    WorkerContext,
    run_worker_cli,
)


logger = logging.getLogger(__name__)
_ENABLE_VALUES = {"1", "true", "yes", "on", "external", "worker"}
_USER_SUBJECT_KEYS = frozenset(
    {
        "actor",
        "actors",
        "invitee",
        "invitees",
        "invitee_id",
        "invitee_ids",
        "member",
        "members",
        "member_id",
        "member_ids",
        "owner",
        "owners",
        "owner_id",
        "owner_ids",
        "recipient",
        "recipients",
        "recipient_id",
        "recipient_ids",
        "subject",
        "subjects",
        "target",
        "targets",
        "user",
        "users",
    }
)
_USER_SUBJECT_KEY_PATTERN = re.compile(r"(^|_)user(_ids?|s)?$")


def _is_user_subject_key(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in _USER_SUBJECT_KEYS or bool(
        _USER_SUBJECT_KEY_PATTERN.search(normalized)
    )


def _audit_event_subject_values(payload: dict[str, Any]) -> set[str]:
    """Extract exact, structured user references without indexing free text."""

    values: set[str] = set()
    actor = str(payload.get("user_id") or "").strip()
    if actor:
        values.add(actor)

    def visit(value: Any, *, parent_key: str | None = None) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, parent_key=str(key))
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item, parent_key=parent_key)
            return
        if isinstance(value, str) and _is_user_subject_key(parent_key):
            normalized = value.strip()
            if normalized and not normalized.startswith("deleted-user:"):
                values.add(normalized)

    visit(payload.get("details"))
    return values


def _pseudonymize_erased_subjects(
    payload: dict[str, Any],
    *,
    subject_ids: set[str],
) -> dict[str, Any]:
    if not subject_ids:
        return payload

    from app.logging.models import pseudonymize_deleted_user_details

    prepared = dict(payload)
    details = prepared.get("details")
    for subject_id in sorted(subject_ids):
        details = pseudonymize_deleted_user_details(details, subject_id)
    prepared["details"] = details
    return prepared


def external_audit_event_enabled() -> bool:
    return (
        str(os.getenv("AUDIT_EVENT_WORKER_MODE", "inline") or "inline")
        .strip()
        .lower()
        in _ENABLE_VALUES
    )


def _prepare_audit_event_payload(
    session,
    *,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], set[str], bool]:
    """Apply the shared subject-erasure fence to either delivery mode.

    The returned subject IDs are safe to index: erased details-only subjects
    have already been pseudonymized.  The transaction must remain open until
    the caller has either persisted or suppressed the event so policy-aware
    erasure serializes with both inline and outbox-backed audit writes.
    """

    prepared_payload = dict(payload)
    subject_ids = _audit_event_subject_values(prepared_payload)
    fingerprints_by_subject = {
        subject_id: audit_event_subject_fingerprint(subject_id)
        for subject_id in subject_ids
    }
    subject_states = lock_audit_event_subject_states(
        session,
        subject_fingerprints=set(fingerprints_by_subject.values()),
    )
    erased_subjects = {
        subject_id
        for subject_id, fingerprint in fingerprints_by_subject.items()
        if subject_states[fingerprint].erased_at is not None
    }
    actor_id = str(prepared_payload.get("user_id") or "").strip()
    actor_erased = bool(actor_id and actor_id in erased_subjects)
    if not actor_erased:
        prepared_payload = _pseudonymize_erased_subjects(
            prepared_payload,
            subject_ids=erased_subjects,
        )
    return (
        prepared_payload,
        _audit_event_subject_values(prepared_payload),
        actor_erased,
    )


def write_inline_audit_event(
    *,
    db_log,
    payload: dict[str, Any],
    occurred_at: datetime,
):
    """Persist an inline audit event while holding its main-DB privacy fence."""

    session = SessionLocal()
    event_id = uuid.uuid4().hex
    try:
        prepared_payload, _subject_ids, actor_erased = _prepare_audit_event_payload(
            session,
            payload=payload,
        )
        if actor_erased:
            # Match the external path's return contract without recreating any
            # audit-database row or persisting erased actor data in the main DB.
            from app.logging.models import Logs

            session.commit()
            return Logs(
                id=event_id,
                user_id="",
                action=str(prepared_payload.get("action") or "")[:128],
                reason=None,
                details=None,
                ip_address=None,
                user_agent=None,
                category=str(prepared_payload.get("category") or "admin")[:64],
                timestamp=occurred_at,
                share_refs_scrubbed=True,
            )

        # Keep every subject-state row locked through the audit DB commit. If
        # this write wins, erasure waits and subsequently removes it; if the
        # erasure fence wins, this path suppresses/pseudonymizes the payload.
        from app.logging.models import write_audit_log_record

        row = write_audit_log_record(
            db_log,
            log_id=event_id,
            timestamp=occurred_at,
            payload=prepared_payload,
        )
        session.commit()
        return row
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def stage_audit_event(
    session,
    *,
    payload: dict[str, Any],
    occurred_at: datetime,
) -> AuditEventOutbox:
    """Stage an encrypted audit outbox event in the caller's transaction.

    This helper deliberately never commits, rolls back, refreshes, or closes the
    supplied session.  A security-sensitive mutation can therefore make its
    state change and audit intent one atomic main-database transaction.
    """

    prepared_payload, subject_ids, actor_erased = _prepare_audit_event_payload(
        session,
        payload=payload,
    )
    event_id = uuid.uuid4().hex
    if actor_erased:
        # Preserve the enqueue contract without recreating erased actor data or
        # scheduling a delivery that policy requires us to suppress.
        row = AuditEventOutbox(
            id=event_id,
            user_id="",
            action=str(prepared_payload.get("action") or "")[:128],
            category=str(prepared_payload.get("category") or "admin")[:64],
            subjects_indexed=True,
            status=JOB_CANCELLED,
            error_code="subject_erased",
            occurred_at=occurred_at,
        )
        session.add(row)
        session.flush()
        return row

    row = AuditEventOutbox(
        id=event_id,
        user_id=str(prepared_payload.get("user_id") or "")[:64],
        action=str(prepared_payload.get("action") or "")[:128],
        reason=prepared_payload.get("reason"),
        details=prepared_payload.get("details"),
        ip_address=prepared_payload.get("ip_address"),
        user_agent=prepared_payload.get("user_agent"),
        category=str(prepared_payload.get("category") or "admin")[:64],
        subjects_indexed=True,
        status=JOB_PENDING,
        occurred_at=occurred_at,
    )
    session.add(row)
    session.flush()
    references = [
        AuditEventSubjectReference(
            event_id=event_id,
            subject_fingerprint=audit_event_subject_fingerprint(subject_id),
        )
        for subject_id in sorted(subject_ids)
    ]
    if references:
        session.add_all(references)
    enqueue_worker_job(
        session,
        queue=QUEUE_EVENTS,
        kind="audit_log",
        user_id=row.user_id,
        payload={"event_id": event_id},
        idempotency_key=f"audit:{event_id}",
        priority=0,
        max_attempts=100,
        expires_at=utcnow() + timedelta(days=30),
        commit=False,
    )
    session.flush()
    return row


def enqueue_audit_event(
    *,
    payload: dict[str, Any],
    occurred_at: datetime,
) -> AuditEventOutbox:
    """Atomically create the encrypted audit outbox row and delivery job."""

    session = SessionLocal()
    try:
        row = stage_audit_event(
            session,
            payload=payload,
            occurred_at=occurred_at,
        )
        session.commit()
        session.refresh(row)
        return row
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def enqueue_audit_erasure(
    db,
    *,
    user_id: str,
    boundary_id: str,
    commit: bool = False,
):
    """Stage cross-database audit cleanup in the account-state transaction."""

    normalized_user_id = str(user_id or "").strip()
    normalized_boundary = str(boundary_id or "").strip()
    if not normalized_user_id or not normalized_boundary:
        raise ValueError("Audit erasure requires a user and deletion boundary")
    fingerprint = audit_event_subject_fingerprint(normalized_user_id)
    return enqueue_worker_job(
        db,
        queue=QUEUE_EVENTS,
        kind="audit_erasure",
        user_id=normalized_user_id,
        payload={"user_id": normalized_user_id},
        idempotency_key=f"audit-erasure:{fingerprint}:{normalized_boundary}",
        priority=0,
        max_attempts=100,
        commit=commit,
    )


def enqueue_ip_enrichment(db) -> None:
    """Coalesce overview-triggered geo enrichment into one short-lived job."""

    bucket = int(datetime.now(timezone.utc).timestamp() // 60)
    enqueue_worker_job(
        db,
        queue=QUEUE_EVENTS,
        kind="ip_geo_enrichment",
        payload={},
        idempotency_key=f"ip-geo:{bucket}",
        priority=60,
        max_attempts=3,
        expires_at=utcnow() + timedelta(hours=1),
        commit=True,
    )


def _handle_audit_log(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    event_id = str(job.payload.get("event_id") or "").strip()
    if not event_id:
        raise FatalJobError("invalid_payload")

    # Check queue ownership before taking the outbox lock.  No queue lookup is
    # performed while that lock is held, which keeps lock ordering compatible
    # with policy-aware erasure (job first, outbox second).
    context.raise_if_cancelled()
    session = SessionLocal()
    try:
        row = (
            session.query(AuditEventOutbox)
            .filter(AuditEventOutbox.id == event_id)
            .with_for_update()
            .first()
        )
        if row is None:
            raise FatalJobError("audit_event_unavailable")
        if row.status == "delivered":
            return {"event_id": event_id}
        if row.status == JOB_CANCELLED:
            raise FatalJobError(str(row.error_code or "audit_event_cancelled"))
        if not bool(row.subjects_indexed):
            # An old replica may have inserted this row during a rolling
            # upgrade without participating in the subject fence. Fail closed
            # rather than deliver encrypted details that erasure could miss.
            row.user_id = ""
            row.reason = None
            row.details = None
            row.ip_address = None
            row.user_agent = None
            row.subjects_indexed = True
            row.status = JOB_CANCELLED
            row.error_code = "subjects_unindexed"
            row.updated_at = utcnow()
            session.query(AuditEventSubjectReference).filter(
                AuditEventSubjectReference.event_id == event_id
            ).delete(synchronize_session=False)
            session.commit()
            return {"event_id": event_id}
        row.status = JOB_PROCESSING
        row.updated_at = utcnow()
        session.flush()
        occurred_at = row.occurred_at
        payload = {
            "user_id": row.user_id,
            "action": row.action,
            "reason": row.reason,
            "details": row.details,
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
            "category": row.category,
        }
        # Hold the outbox row lock across the idempotent audit write. A
        # policy-aware deletion either completes first (and this worker sees a
        # cancelled row) or waits, then deletes the just-written record. It can
        # no longer be recreated after the deletion boundary has passed.
        audit_session = AuditSessionLocal()
        try:
            from app.logging.models import write_audit_log_record

            write_audit_log_record(
                audit_session,
                log_id=event_id,
                timestamp=occurred_at,
                payload=payload,
            )
        except Exception as exc:
            raise RetryableJobError("audit_delivery_failed") from exc
        finally:
            audit_session.close()

        row.status = "delivered"
        row.reason = None
        row.details = None
        row.ip_address = None
        row.user_agent = None
        row.error_code = None
        row.delivered_at = utcnow()
        row.updated_at = utcnow()
        session.query(AuditEventSubjectReference).filter(
            AuditEventSubjectReference.event_id == event_id
        ).delete(synchronize_session=False)
        session.commit()
        return {"event_id": event_id}
    except FatalJobError:
        session.rollback()
        raise
    except RetryableJobError:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise RetryableJobError("audit_outbox_unavailable") from exc
    finally:
        session.close()


def _handle_ip_geo_enrichment(
    _job: WorkerJobSnapshot,
    context: WorkerContext,
) -> dict[str, Any]:
    context.raise_if_cancelled()
    try:
        from app.ip_analytics.service import enrich_pending_with_session_factory

        asyncio.run(enrich_pending_with_session_factory(SessionLocal))
        return {"completed": True}
    except Exception as exc:
        raise RetryableJobError("ip_geo_enrichment_failed") from exc


def _erase_audit_subject_history(
    user_id: str,
    *,
    context: WorkerContext | None = None,
) -> dict[str, Any]:
    """Idempotently finish one durable main-DB to audit-DB erasure handoff."""

    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise FatalJobError("invalid_payload")
    if context is not None:
        context.raise_if_cancelled()

    from app.logging.models import (
        audit_log_erasure_guard,
        cancel_audit_log_deletions_for_user,
        delete_admin_notifications_for_user,
        delete_audit_logs_for_user,
    )
    from app.users.models import User

    # Restoration and cleanup acquire locks in the same guard -> user order.
    # The guard remains held until both databases have crossed the destructive
    # boundary, so a restored account can never become active midway through.
    with audit_log_erasure_guard(normalized_user_id) as guard_db:
        main_db = SessionLocal()
        audit_db = AuditSessionLocal()
        try:
            user = (
                main_db.query(User)
                .filter(User.id == normalized_user_id)
                .with_for_update()
                .populate_existing()
                .first()
            )
            if user is not None and user.deleted_at is None:
                main_db.rollback()
                return {
                    "completed": False,
                    "account_active": True,
                    "audit_logs_deleted": 0,
                    "notifications_deleted": 0,
                }
            if context is not None:
                context.raise_if_cancelled()
            cancel_audit_log_deletions_for_user(
                audit_db,
                normalized_user_id,
            )
            deleted_logs = delete_audit_logs_for_user(
                audit_db,
                normalized_user_id,
                main_db=main_db,
                erasure_guard_db=guard_db,
            )
            deleted_notifications = delete_admin_notifications_for_user(
                audit_db,
                normalized_user_id,
            )
            return {
                "completed": True,
                "account_active": False,
                "audit_logs_deleted": int(deleted_logs or 0),
                "notifications_deleted": int(deleted_notifications or 0),
            }
        except Exception:
            main_db.rollback()
            audit_db.rollback()
            raise
        finally:
            audit_db.close()
            main_db.close()


def _handle_audit_erasure(
    job: WorkerJobSnapshot,
    context: WorkerContext,
) -> dict[str, Any]:
    user_id = str(job.payload.get("user_id") or job.user_id or "").strip()
    try:
        return _erase_audit_subject_history(user_id, context=context)
    except JobCancelled:
        raise
    except FatalJobError:
        raise
    except Exception as exc:
        raise RetryableJobError("audit_erasure_failed") from exc


def reconcile_pending_audit_erasures(*, batch_size: int = 1000) -> int:
    """Finish crash-surviving audit handoffs during offline startup.

    This is also the inline-mode fallback: no dedicated event worker is needed
    for correctness after a process dies between account commit and audit-DB
    cleanup.
    """

    session = SessionLocal()
    try:
        rows = (
            session.query(DurableWorkerJob)
            .filter(
                DurableWorkerJob.queue == QUEUE_EVENTS,
                DurableWorkerJob.kind == "audit_erasure",
                DurableWorkerJob.user_id.isnot(None),
                DurableWorkerJob.status.in_(tuple(JOB_ACTIVE_STATUSES) + (JOB_FAILED,)),
            )
            .order_by(DurableWorkerJob.created_at.asc())
            .limit(max(1, min(int(batch_size), 5000)))
            .all()
        )
        targets = [(str(row.id), str(row.user_id)) for row in rows]
        session.rollback()
    finally:
        session.close()

    reconciled = 0
    for job_id, user_id in targets:
        result = _erase_audit_subject_history(user_id)
        update_session = SessionLocal()
        try:
            row = (
                update_session.query(DurableWorkerJob)
                .filter(DurableWorkerJob.id == job_id)
                .with_for_update()
                .first()
            )
            if row is None:
                continue
            current = utcnow()
            row.status = JOB_CANCELLED if result["account_active"] else JOB_SUCCEEDED
            row.cancel_requested = bool(result["account_active"])
            row.user_id = None
            row.payload = None
            row.result = {"completed": bool(result["completed"])}
            row.progress = 0 if result["account_active"] else 100
            row.error_code = "account_restored" if result["account_active"] else None
            row.lease_owner = None
            row.leased_at = None
            row.lease_expires_at = None
            row.finished_at = current
            row.reconciled_at = current
            row.updated_at = current
            update_session.commit()
            reconciled += 1
        except Exception:
            update_session.rollback()
            raise
        finally:
            update_session.close()
    return reconciled


def reconcile_terminal_event_jobs(*, batch_size: int = 1000) -> int:
    """Never lose a pending audit event because its queue lease exhausted."""

    session = SessionLocal()
    try:
        rows = lock_unreconciled_terminal_jobs(
            session,
            queue=QUEUE_EVENTS,
            kinds=("audit_log", "audit_erasure"),
            batch_size=batch_size,
        )
        current = utcnow()
        for job in rows:
            if job.kind == "audit_erasure":
                user_id = str(job.user_id or "").strip()
                if job.status == JOB_FAILED and user_id:
                    # A cross-database outage must never permanently discard a
                    # privacy handoff. Recreate only its encrypted minimal
                    # payload and give the idempotent cleanup a new retry budget.
                    job.status = JOB_PENDING
                    job.payload = {"user_id": user_id}
                    job.result = None
                    job.attempt_count = 0
                    job.progress = 0
                    job.cancel_requested = False
                    job.available_at = current + timedelta(seconds=30)
                    job.error_code = None
                    job.finished_at = None
                    job.reconciled_at = None
                    job.updated_at = current
                else:
                    job.reconciled_at = current
                    job.updated_at = current
                continue
            prefix = "audit:"
            key = str(job.idempotency_key or "")
            event_id = key[len(prefix) :] if key.startswith(prefix) else ""
            outbox = (
                session.query(AuditEventOutbox)
                .filter(AuditEventOutbox.id == event_id)
                .first()
                if event_id
                else None
            )
            if (
                outbox is not None
                and outbox.status not in ("delivered", JOB_CANCELLED)
            ):
                outbox.status = JOB_PENDING
                outbox.error_code = str(job.error_code or "delivery_interrupted")[:64]
                outbox.updated_at = current
                job.status = JOB_PENDING
                job.payload = {"event_id": event_id}
                job.result = None
                job.attempt_count = 0
                job.progress = 0
                job.cancel_requested = False
                job.available_at = current + timedelta(seconds=30)
                job.expires_at = current + timedelta(days=30)
                job.error_code = None
                job.finished_at = None
                job.reconciled_at = None
                job.updated_at = current
                continue
            job.reconciled_at = current
            job.updated_at = current
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def build_worker() -> DurableQueueWorker:
    return DurableQueueWorker(
        queue=QUEUE_EVENTS,
        handlers={
            "audit_log": _handle_audit_log,
            "audit_erasure": _handle_audit_erasure,
            "ip_geo_enrichment": _handle_ip_geo_enrichment,
        },
        reconciler=reconcile_terminal_event_jobs,
        default_lease_seconds=120,
        env_prefix="audit_event",
    )


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
