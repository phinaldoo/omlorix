"""Periodic lifecycle cleanup for expired long-term memories."""

from __future__ import annotations

import logging
import os
import threading

from app.database import SessionLocal
from app.memories.service import refresh_due_memory_profiles, sweep_expired_memories, sweep_memory_deletions
from app.redis_client import (
    new_lock_owner,
    refresh_lock,
    release_lock,
    try_acquire_lock,
)
from app.utils.background import start_named_worker, worker_manager


logger = logging.getLogger(__name__)
_WORKER_NAME = "memory_lifecycle"
_LOCK_NAME = "memory_lifecycle_worker"


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _memory_lifecycle_worker(stop_event: threading.Event) -> None:
    interval = _bounded_int(
        "MEMORY_RETENTION_INTERVAL_SECONDS",
        3_600,
        60,
        86_400,
    )
    batch_size = _bounded_int("MEMORY_RETENTION_BATCH_SIZE", 1_000, 10, 5_000)
    max_batches = _bounded_int("MEMORY_RETENTION_MAX_BATCHES", 20, 1, 1_000)
    lock_ttl = max(300, min(86_400, interval + 300))

    while not stop_event.is_set():
        owner = new_lock_owner()
        if not try_acquire_lock(_LOCK_NAME, owner, lock_ttl):
            if stop_event.wait(min(interval, 300)):
                break
            continue

        session = SessionLocal()
        removed = 0
        try:
            for _ in range(max_batches):
                if stop_event.is_set():
                    break
                count = sweep_expired_memories(
                    session,
                    batch_size=batch_size,
                    commit=True,
                )
                removed += count
                if count < batch_size:
                    break
                if not refresh_lock(_LOCK_NAME, owner, lock_ttl):
                    logger.warning("Memory lifecycle lock expired during cleanup")
                    break
            for _ in range(max_batches):
                if stop_event.is_set():
                    break
                refreshed = refresh_due_memory_profiles(
                    session,
                    batch_size=batch_size,
                    commit=True,
                )
                if refreshed < batch_size:
                    break
                if not refresh_lock(_LOCK_NAME, owner, lock_ttl):
                    logger.warning("Memory lifecycle lock expired during profile refresh")
                    break
            if removed:
                logger.info("Deleted %s expired memory facts", removed)
            for _ in range(max_batches):
                if stop_event.is_set() or not refresh_lock(_LOCK_NAME, owner, lock_ttl):
                    break
                if sweep_memory_deletions(session, batch_size=batch_size) < batch_size:
                    break
        except Exception:
            session.rollback()
            logger.exception("Memory lifecycle cleanup failed")
        finally:
            session.close()
            release_lock(_LOCK_NAME, owner)

        if stop_event.wait(interval):
            break


def start_memory_lifecycle_worker():
    return start_named_worker(
        _WORKER_NAME,
        _memory_lifecycle_worker,
        logger,
        start_message="Memory lifecycle worker started.",
        already_running_message="Memory lifecycle worker already running.",
        failure_message="Failed to start memory lifecycle worker",
    )


def stop_memory_lifecycle_worker(timeout: float = 10.0) -> None:
    worker_manager.stop_worker(_WORKER_NAME, timeout=timeout)
