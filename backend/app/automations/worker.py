"""
Distributed Automation Scheduler Worker

This worker claims due automations in batches and enqueues matching executions
into Redis Queue (RQ). A Redis lock ensures only one scheduler instance runs the
poll cycle at a time across horizontally scaled FastAPI instances.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import threading

from app.automations.models import claim_due_automations, release_automation_claim
from app.database import SessionLocal
from app.redis_client import new_lock_owner, release_lock, try_acquire_lock
from app.automations.queue import enqueue_scheduled_automation_execution


logger = logging.getLogger(__name__)


_automation_worker_thread: threading.Thread | None = None
_automation_worker_stop_event = threading.Event()

WORKER_INTERVAL_SECONDS = max(10, int(os.getenv("AUTOMATION_SCHEDULER_INTERVAL_SECONDS", "60") or "60"))
SCHEDULER_BATCH_SIZE = max(1, int(os.getenv("AUTOMATION_SCHEDULER_BATCH_SIZE", "100") or "100"))
SCHEDULER_CLAIM_TTL_SECONDS = max(
    WORKER_INTERVAL_SECONDS * 3,
    int(os.getenv("AUTOMATION_SCHEDULER_CLAIM_TTL_SECONDS", "300") or "300"),
)
SCHEDULER_LOCK_NAME = "automation_scheduler"
SCHEDULER_LOCK_TTL_SECONDS = max(WORKER_INTERVAL_SECONDS + 10, 90)


_DISABLE_VALUES = {"0", "false", "no", "off"}


def is_automation_scheduler_enabled() -> bool:
    raw = (os.getenv("AUTOMATION_SCHEDULER_ENABLED") or "true").strip().lower()
    return raw not in _DISABLE_VALUES


def start_automation_scheduler_worker():
    """Start the background automation scheduler worker thread (if enabled)."""

    global _automation_worker_thread
    if not is_automation_scheduler_enabled():
        logger.info("Automation scheduler worker is disabled (AUTOMATION_SCHEDULER_ENABLED=false)")
        return

    if _automation_worker_thread is not None and _automation_worker_thread.is_alive():
        logger.warning("Automation scheduler worker is already running")
        return

    _automation_worker_stop_event.clear()
    _automation_worker_thread = threading.Thread(
        target=_automation_scheduler_loop,
        name="AutomationSchedulerWorker",
        daemon=True,
    )
    _automation_worker_thread.start()
    logger.info("Automation scheduler worker started")


def stop_automation_scheduler_worker():
    """Stop the background automation scheduler worker thread."""

    global _automation_worker_thread
    _automation_worker_stop_event.set()
    if _automation_worker_thread is not None:
        _automation_worker_thread.join(timeout=10)
        _automation_worker_thread = None
    logger.info("Automation scheduler worker stopped")


def run_automation_scheduler_forever() -> None:
    """Run scheduler loop in the current process (used by dedicated container)."""

    stop_event = threading.Event()
    logger.info("Starting dedicated automation scheduler process")
    try:
        _automation_scheduler_loop(stop_event)
    except KeyboardInterrupt:
        logger.info("Automation scheduler interrupted; exiting")


def _automation_scheduler_loop(stop_event: threading.Event | None = None):
    """Main scheduler loop that periodically enqueues due automations."""

    active_stop_event = stop_event or _automation_worker_stop_event
    logger.info("Automation scheduler loop starting (interval=%ss)", WORKER_INTERVAL_SECONDS)

    while not active_stop_event.is_set():
        lock_owner = new_lock_owner()
        acquired = try_acquire_lock(SCHEDULER_LOCK_NAME, lock_owner, SCHEDULER_LOCK_TTL_SECONDS)
        if acquired:
            try:
                _check_and_enqueue_automations()
            except Exception:  # noqa: BLE001
                logger.exception("Error in automation scheduler loop")
            finally:
                release_lock(SCHEDULER_LOCK_NAME, lock_owner)
        else:
            logger.debug("Automation scheduler lock held by another instance; skipping this cycle")

        active_stop_event.wait(timeout=WORKER_INTERVAL_SECONDS)

    logger.info("Automation scheduler loop exiting")


def _check_and_enqueue_automations():
    """Claim due automations in batches and enqueue their executions."""

    db = SessionLocal()
    try:
        while True:
            claimed_automations = claim_due_automations(
                db,
                due_before=datetime.now(timezone.utc),
                batch_size=SCHEDULER_BATCH_SIZE,
                claim_timeout_seconds=SCHEDULER_CLAIM_TTL_SECONDS,
            )
            if not claimed_automations:
                break

            released_retryable_claim = False
            for automation in claimed_automations:
                claimed_at = automation.scheduler_claimed_at
                scheduled_for = automation.next_run_at
                schedule_slot = automation.next_run_slot

                if scheduled_for is None or not schedule_slot:
                    release_automation_claim(db, automation.id, claimed_at)
                    released_retryable_claim = True
                    logger.warning("Released invalid claim for automation %s without a due slot", automation.id)
                    continue

                try:
                    enqueue_result = enqueue_scheduled_automation_execution(
                        automation.id,
                        automation.user_id,
                        scheduled_for,
                        schedule_slot,
                    )
                    if enqueue_result.status == "failed":
                        release_automation_claim(db, automation.id, claimed_at)
                        released_retryable_claim = True
                        logger.warning(
                            "Failed to enqueue automation %s (%s) for slot %s",
                            automation.id,
                            automation.title,
                            schedule_slot,
                        )
                    elif enqueue_result.status == "executed":
                        logger.info(
                            "Executed automation %s (%s) inline for slot %s",
                            automation.id,
                            automation.title,
                            schedule_slot,
                        )
                    elif enqueue_result.status == "queued":
                        logger.info(
                            "Queued automation %s (%s) for slot %s",
                            automation.id,
                            automation.title,
                            schedule_slot,
                        )
                    else:
                        logger.debug(
                            "Skipped duplicate enqueue for automation %s slot %s",
                            automation.id,
                            schedule_slot,
                        )
                except Exception:  # noqa: BLE001
                    release_automation_claim(db, automation.id, claimed_at)
                    released_retryable_claim = True
                    logger.exception("Error checking automation %s", automation.id)

            if released_retryable_claim:
                logger.debug(
                    "Stopping automation scheduler pass after releasing retryable claims; "
                    "they will be retried on the next scheduler interval"
                )
                break

            if len(claimed_automations) < SCHEDULER_BATCH_SIZE:
                break
    finally:
        db.close()
