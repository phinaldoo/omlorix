from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import threading
from zoneinfo import ZoneInfo

from app.backups.models import BackupSchedule, list_backup_schedules
from app.backups.service import create_scheduled_backup_job, enqueue_backup_job
from app.database import SessionLocal
from app.redis_client import new_lock_owner, release_lock, try_acquire_lock


logger = logging.getLogger(__name__)


_backup_scheduler_thread: threading.Thread | None = None
_backup_scheduler_stop_event = threading.Event()

BACKUP_SCHEDULER_INTERVAL_SECONDS = max(10, int(os.getenv("BACKUP_SCHEDULER_INTERVAL_SECONDS", "60") or "60"))
BACKUP_SCHEDULER_LOCK_NAME = "backup_scheduler_lock"
BACKUP_SCHEDULER_LOCK_TTL_SECONDS = max(BACKUP_SCHEDULER_INTERVAL_SECONDS + 10, 90)

_DISABLE_VALUES = {"0", "false", "no", "off"}


def is_backup_scheduler_enabled() -> bool:
    """Check if backup scheduler is enabled."""
    raw = (os.getenv("BACKUP_SCHEDULER_ENABLED") or "true").strip().lower()
    return raw not in _DISABLE_VALUES


def start_backup_scheduler_worker() -> None:
    """Start backup scheduler worker thread."""
    global _backup_scheduler_thread

    if not is_backup_scheduler_enabled():
        logger.info("Backup scheduler worker is disabled (BACKUP_SCHEDULER_ENABLED=false)")
        return

    if _backup_scheduler_thread is not None and _backup_scheduler_thread.is_alive():
        logger.warning("Backup scheduler worker is already running")
        return

    _backup_scheduler_stop_event.clear()
    _backup_scheduler_thread = threading.Thread(
        target=_backup_scheduler_loop,
        name="BackupSchedulerWorker",
        daemon=True,
    )
    _backup_scheduler_thread.start()
    logger.info("Backup scheduler worker started")


def stop_backup_scheduler_worker() -> None:
    """Stop backup scheduler worker thread."""
    global _backup_scheduler_thread
    _backup_scheduler_stop_event.set()
    if _backup_scheduler_thread is not None:
        _backup_scheduler_thread.join(timeout=10)
        _backup_scheduler_thread = None
    logger.info("Backup scheduler worker stopped")


def run_backup_scheduler_forever() -> None:
    """Run backup scheduler in dedicated process."""
    stop_event = threading.Event()
    logger.info("Starting dedicated backup scheduler process")
    try:
        _backup_scheduler_loop(stop_event)
    except KeyboardInterrupt:
        logger.info("Backup scheduler interrupted; exiting")


def _backup_scheduler_loop(stop_event: threading.Event | None = None) -> None:
    """Run backup scheduler loop."""
    active_stop_event = stop_event or _backup_scheduler_stop_event
    logger.info("Backup scheduler loop starting (interval=%ss)", BACKUP_SCHEDULER_INTERVAL_SECONDS)

    while not active_stop_event.is_set():
        lock_owner = new_lock_owner()
        acquired = try_acquire_lock(BACKUP_SCHEDULER_LOCK_NAME, lock_owner, BACKUP_SCHEDULER_LOCK_TTL_SECONDS)
        if acquired:
            try:
                _run_schedule_cycle()
            except Exception:  # noqa: BLE001
                logger.exception("Error in backup scheduler loop")
            finally:
                release_lock(BACKUP_SCHEDULER_LOCK_NAME, lock_owner)

        active_stop_event.wait(timeout=BACKUP_SCHEDULER_INTERVAL_SECONDS)

    logger.info("Backup scheduler loop exiting")


def _schedule_now(schedule: BackupSchedule, now_utc: datetime) -> bool:
    """Check if schedule should run now."""
    try:
        tz = ZoneInfo((schedule.timezone or "UTC").strip() or "UTC")
    except Exception:
        tz = timezone.utc

    local_now = now_utc.astimezone(tz)
    last_run_at = schedule.last_run_at
    local_last_run = last_run_at.astimezone(tz) if last_run_at and last_run_at.tzinfo else None

    if schedule.frequency == "hourly":
        if local_now.minute != schedule.minute:
            return False
        if not local_last_run:
            return True
        return (local_last_run.year, local_last_run.month, local_last_run.day, local_last_run.hour) != (
            local_now.year,
            local_now.month,
            local_now.day,
            local_now.hour,
        )

    if schedule.frequency == "daily":
        if local_now.hour != schedule.hour or local_now.minute != schedule.minute:
            return False
        if not local_last_run:
            return True
        return local_last_run.date() != local_now.date()

    if schedule.frequency == "weekly":
        days = schedule.days_of_week if isinstance(schedule.days_of_week, list) else []
        if local_now.weekday() not in days:
            return False
        if local_now.hour != schedule.hour or local_now.minute != schedule.minute:
            return False
        if not local_last_run:
            return True
        current_marker = (local_now.isocalendar().year, local_now.isocalendar().week, local_now.weekday())
        last_marker = (local_last_run.isocalendar().year, local_last_run.isocalendar().week, local_last_run.weekday())
        return current_marker != last_marker

    return False


def _run_schedule_cycle() -> None:
    """Run one backup schedule cycle."""
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        schedules = [s for s in list_backup_schedules(db) if s.enabled]

        for schedule in schedules:
            try:
                if not _schedule_now(schedule, now_utc):
                    continue

                job = create_scheduled_backup_job(db, schedule)
                enqueue_backup_job(job.id)
                schedule.last_run_at = now_utc
                schedule.updated_at = now_utc
                db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Failed running backup schedule %s", schedule.id)
    finally:
        db.close()
