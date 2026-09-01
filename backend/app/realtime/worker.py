"""Authoritative deadline worker for provider-backed realtime sessions."""
from __future__ import annotations

import logging
import os
import threading

from app.database import SessionLocal
from app.realtime.service import reconcile_expired_realtime_sessions
from app.redis_client import (
    new_lock_owner,
    refresh_lock,
    release_lock,
    try_acquire_lock,
)
from app.utils.background import start_named_worker, stop_named_worker


logger = logging.getLogger(__name__)

# Realtime quotas are user-visible hard deadlines, so enforce them much more
# frequently than generic cleanup jobs. The value remains configurable for
# unusually large deployments while retaining a safe lower bound.
REALTIME_ENFORCEMENT_INTERVAL_SECONDS = max(
    1,
    int(os.getenv("REALTIME_ENFORCEMENT_INTERVAL_SECONDS", "2") or "2"),
)
REALTIME_ENFORCEMENT_LOCK_NAME = "realtime_deadline_enforcement"
REALTIME_ENFORCEMENT_LOCK_TTL_SECONDS = max(
    30,
    REALTIME_ENFORCEMENT_INTERVAL_SECONDS * 4,
)
REALTIME_ENFORCEMENT_LOCK_REFRESH_SECONDS = max(
    1.0,
    REALTIME_ENFORCEMENT_LOCK_TTL_SECONDS / 3,
)


def run_realtime_enforcement_once() -> None:
    """Enforce deadlines and renew only server-observed provider sessions."""
    db = SessionLocal()
    try:
        reconcile_expired_realtime_sessions(db)
    except Exception:
        db.rollback()
        logger.exception("[Realtime] Deadline enforcement pass failed")
        raise
    finally:
        db.close()


def _renew_realtime_enforcement_lock(
    stop_event: threading.Event,
    lock_owner: str,
) -> None:
    """Refresh the distributed lease until the enforcement pass finishes."""

    while not stop_event.wait(REALTIME_ENFORCEMENT_LOCK_REFRESH_SECONDS):
        if refresh_lock(
            REALTIME_ENFORCEMENT_LOCK_NAME,
            lock_owner,
            REALTIME_ENFORCEMENT_LOCK_TTL_SECONDS,
        ):
            continue
        # The pass itself is synchronous and may be inside a provider request,
        # so it cannot be cancelled safely. Stop renewing and retain owner-safe
        # release semantics; a peer can recover after the current lease ends.
        logger.warning("[Realtime] Lost deadline enforcement worker lease")
        return


def _realtime_enforcement_worker(stop_event: threading.Event) -> None:
    """Continuously enforce persisted provider ownership and quota deadlines."""
    while not stop_event.is_set():
        lock_owner = new_lock_owner()
        acquired = try_acquire_lock(
            REALTIME_ENFORCEMENT_LOCK_NAME,
            lock_owner,
            REALTIME_ENFORCEMENT_LOCK_TTL_SECONDS,
        )
        if acquired:
            lease_stop_event = threading.Event()
            lease_thread = threading.Thread(
                target=_renew_realtime_enforcement_lock,
                args=(lease_stop_event, lock_owner),
                name="realtime_deadline_enforcement_lease",
                daemon=True,
            )
            lease_thread.start()
            try:
                run_realtime_enforcement_once()
            except Exception:
                # A transient database/provider failure must not kill deadline
                # enforcement. Persisted termination_pending state is retried.
                pass
            finally:
                lease_stop_event.set()
                lease_thread.join(timeout=2.0)
                release_lock(REALTIME_ENFORCEMENT_LOCK_NAME, lock_owner)

        if stop_event.wait(REALTIME_ENFORCEMENT_INTERVAL_SECONDS):
            break


def start_realtime_enforcement_worker():
    """Start the realtime provider deadline worker."""
    return start_named_worker(
        "realtime_deadline_enforcement",
        _realtime_enforcement_worker,
        logger,
        start_message="[Realtime] Deadline enforcement worker started",
        already_running_message="[Realtime] Deadline enforcement worker already running",
        failure_message="[Realtime] Failed to start deadline enforcement worker",
    )


def stop_realtime_enforcement_worker(timeout: float = 5.0) -> None:
    """Stop the realtime provider deadline worker."""
    stop_named_worker(
        "realtime_deadline_enforcement",
        logger,
        timeout=timeout,
        stopped_message="[Realtime] Deadline enforcement worker stopped",
        not_running_message="[Realtime] Deadline enforcement worker was not running",
        failure_message="[Realtime] Failed to stop deadline enforcement worker",
    )
