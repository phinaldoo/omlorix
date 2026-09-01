from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging
import os

from app.database import SessionLocal
from app.automations.models import mark_automation_execution_enqueue_failed, reserve_automation_execution
from app.redis_client import get_redis_client
from app.automations.jobs import execute_automation_job, execute_scheduled_automation_job


logger = logging.getLogger(__name__)


AUTOMATION_QUEUE_NAME = (os.getenv("AUTOMATION_QUEUE_NAME") or "omlorix-automations").strip() or "omlorix-automations"


@dataclass(frozen=True)
class EnqueueAutomationResult:
    status: str

    @property
    def accepted(self) -> bool:
        return self.status in {"queued", "duplicate"}


def _normalize_slot(slot: str | None) -> str:
    value = (slot or "adhoc").strip() or "adhoc"
    return "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_", ":"})[:128] or "adhoc"


def _automation_job_id(automation_id: str, slot_key: str) -> str:
    """Build a deterministic job ID using only characters accepted by RQ."""

    identity = f"{automation_id}\0{slot_key}".encode("utf-8")
    return f"automation-{hashlib.sha256(identity).hexdigest()}"


def enqueue_automation_execution(
    automation_id: str,
    user_id: str,
    scheduled_slot: str | None = None,
    trigger_context: dict | None = None,
) -> EnqueueAutomationResult:
    """Enqueue an automation execution job in Redis queue.

    Returns queued, duplicate, or failed so callers can distinguish retries from errors.
    """

    slot_key = _normalize_slot(scheduled_slot)
    job_id = _automation_job_id(automation_id, slot_key)
    execution_id: str | None = None

    if slot_key != "adhoc":
        db = SessionLocal()
        try:
            execution, reservation_status = reserve_automation_execution(
                db,
                automation_id=automation_id,
                user_id=user_id,
                scheduled_slot=slot_key,
                trigger_context=trigger_context,
            )
            if reservation_status == "duplicate":
                return EnqueueAutomationResult("duplicate")
            if execution is None:
                return EnqueueAutomationResult("failed")
            execution_id = execution.id
        finally:
            db.close()

    redis_client = get_redis_client()
    if redis_client is None:
        logger.warning("Redis unavailable. Executing automation %s inline.", automation_id)
        executed = execute_automation_job(
            automation_id,
            user_id,
            scheduled_slot=slot_key,
            trigger_context=trigger_context,
            execution_id=execution_id,
        )
        return EnqueueAutomationResult("queued" if executed else "failed")

    try:
        from rq import Queue

        queue = Queue(name=AUTOMATION_QUEUE_NAME, connection=redis_client)
        queue.enqueue(
            "app.automations.jobs.execute_automation_job",
            automation_id,
            user_id,
            slot_key,
            trigger_context,
            execution_id,
            job_id=job_id,
            result_ttl=3600,
            failure_ttl=24 * 60 * 60,
            job_timeout=30 * 60,
        )
        return EnqueueAutomationResult("queued")
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "already exists" in message:
            return EnqueueAutomationResult("duplicate")
        if execution_id:
            db = SessionLocal()
            try:
                mark_automation_execution_enqueue_failed(db, execution_id, str(exc))
            finally:
                db.close()
        logger.exception("Failed to enqueue automation %s: %s", automation_id, exc)
        return EnqueueAutomationResult("failed")


def enqueue_scheduled_automation_execution(
    automation_id: str,
    user_id: str,
    scheduled_for: datetime,
    scheduled_slot: str | None = None,
) -> EnqueueAutomationResult:
    """Enqueue or execute a scheduler-owned automation run."""

    slot_key = _normalize_slot(scheduled_slot)
    job_id = _automation_job_id(automation_id, slot_key)
    trigger_context = {"type": "schedule"}

    redis_client = get_redis_client()
    if redis_client is None:
        logger.warning("Redis unavailable. Executing scheduled automation %s inline.", automation_id)
        executed = execute_scheduled_automation_job(
            automation_id,
            user_id,
            scheduled_for,
            scheduled_slot=slot_key,
            trigger_context=trigger_context,
        )
        return EnqueueAutomationResult("executed" if executed else "failed")

    try:
        from rq import Queue

        queue = Queue(name=AUTOMATION_QUEUE_NAME, connection=redis_client)
        existing = queue.fetch_job(job_id)
        if existing is not None:
            status = existing.get_status(refresh=True)
            if status in {"queued", "started", "deferred", "scheduled"}:
                return EnqueueAutomationResult("duplicate")
            try:
                existing.delete()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to clear terminal scheduled job %s for automation %s",
                    job_id,
                    automation_id,
                )
                return EnqueueAutomationResult("failed")

        queue.enqueue(
            "app.automations.jobs.execute_scheduled_automation_job",
            automation_id,
            user_id,
            scheduled_for,
            slot_key,
            trigger_context,
            job_id=job_id,
            result_ttl=3600,
            failure_ttl=24 * 60 * 60,
            job_timeout=30 * 60,
        )
        return EnqueueAutomationResult("queued")
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "already exists" in message:
            return EnqueueAutomationResult("duplicate")
        logger.exception("Failed to enqueue scheduled automation %s: %s", automation_id, exc)
        return EnqueueAutomationResult("failed")
