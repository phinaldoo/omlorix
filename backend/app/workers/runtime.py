from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import logging
import os
from pathlib import Path
import random
import signal
import socket
import sys
import threading
import time
import uuid
from typing import Any

from app.database import SessionLocal
from app.telemetry.metrics import record_background_task_metric
from app.workers.models import (
    WorkerJobSnapshot,
    claim_worker_jobs,
    mark_worker_job_cancelled,
    mark_worker_job_failed,
    mark_worker_job_succeeded,
    renew_worker_job_lease,
    worker_job_cancel_requested,
)


logger = logging.getLogger(__name__)
WorkerResult = dict[str, Any] | None
WorkerHandler = Callable[
    [WorkerJobSnapshot, "WorkerContext"],
    WorkerResult | Awaitable[WorkerResult],
]
WorkerReconciler = Callable[[], int | None]


class RetryableJobError(RuntimeError):
    def __init__(self, code: str = "retryable", *, delay_seconds: int | None = None):
        super().__init__(code)
        self.code = str(code or "retryable")[:64]
        self.delay_seconds = delay_seconds


class FatalJobError(RuntimeError):
    def __init__(self, code: str = "invalid_job"):
        super().__init__(code)
        self.code = str(code or "invalid_job")[:64]


class JobCancelled(RuntimeError):
    pass


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _env_prefix(queue: str) -> str:
    return str(queue).upper().replace("-", "_")


def _worker_id(queue: str) -> str:
    return f"{queue}:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"[:96]


def _retry_delay(attempt_count: int) -> int:
    base = min(60 * 60, 5 * (2 ** max(0, min(int(attempt_count) - 1, 10))))
    return base + random.SystemRandom().randint(0, max(1, base // 4))


def _resolve_handler_result(result: WorkerResult | Awaitable[WorkerResult]) -> WorkerResult:
    """Run an async handler without changing the durable worker lifecycle."""

    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


@dataclass
class WorkerContext:
    job_id: str
    worker_id: str
    lease_seconds: int
    lease_lost: threading.Event

    def cancelled(self) -> bool:
        if self.lease_lost.is_set():
            return True
        session = SessionLocal()
        try:
            return worker_job_cancel_requested(
                session,
                job_id=self.job_id,
                worker_id=self.worker_id,
            )
        finally:
            session.close()

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled()


class DurableQueueWorker:
    def __init__(
        self,
        *,
        queue: str,
        handlers: dict[str, WorkerHandler],
        heartbeat_path: str | Path | None = None,
        reconciler: WorkerReconciler | None = None,
        default_lease_seconds: int = 900,
        env_prefix: str | None = None,
    ) -> None:
        self.queue = str(queue).strip().lower()
        self.handlers = dict(handlers)
        self.reconciler = reconciler
        prefix = _env_prefix(env_prefix or self.queue)
        self.prefix = prefix
        self.worker_id = _worker_id(self.queue)
        lease_default = max(60, min(int(default_lease_seconds), 86400))
        self.lease_seconds = _bounded_env_int(
            f"{prefix}_WORKER_LEASE_SECONDS",
            lease_default,
            60,
            86400,
        )
        self.poll_seconds = _bounded_env_int(f"{prefix}_WORKER_POLL_SECONDS", 2, 1, 60)
        self.batch_size = _bounded_env_int(f"{prefix}_WORKER_BATCH_SIZE", 1, 1, 50)
        self.health_max_age_seconds = _bounded_env_int(
            f"{prefix}_WORKER_HEALTH_MAX_AGE_SECONDS",
            120,
            15,
            3600,
        )
        configured_path = heartbeat_path or os.getenv(
            f"{prefix}_WORKER_HEARTBEAT_PATH",
            f"/tmp/omlorix-{self.queue}-worker-heartbeat",
        )
        self.heartbeat_path = Path(configured_path)
        self.stop_event = threading.Event()
        self._last_heartbeat = 0.0
        self._next_reconciliation = 0.0

    def _reconcile_if_due(self) -> None:
        if self.reconciler is None or time.monotonic() < self._next_reconciliation:
            return
        interval = _bounded_env_int(
            f"{self.prefix}_WORKER_RECONCILE_SECONDS",
            30,
            10,
            3600,
        )
        self._next_reconciliation = time.monotonic() + interval
        metric_name = f"worker.{self.queue}.reconciliation"
        started = time.monotonic()
        try:
            count = int(self.reconciler() or 0)
            record_background_task_metric(
                metric_name,
                "succeeded",
                (time.monotonic() - started) * 1000,
            )
            if count:
                logger.warning(
                    "Reconciled terminal worker jobs queue=%s count=%s",
                    self.queue,
                    count,
                )
        except Exception:  # noqa: BLE001
            record_background_task_metric(
                metric_name,
                "failed",
                (time.monotonic() - started) * 1000,
            )
            logger.exception("Worker reconciliation failed queue=%s", self.queue)

    def request_stop(self, *_args) -> None:
        self.stop_event.set()

    def write_heartbeat(self, *, force: bool = False) -> None:
        monotonic_now = time.monotonic()
        if not force and monotonic_now - self._last_heartbeat < 5:
            return
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.heartbeat_path.with_name(
            f"{self.heartbeat_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(str(time.time()), encoding="ascii")
            os.replace(temporary, self.heartbeat_path)
        finally:
            temporary.unlink(missing_ok=True)
        self._last_heartbeat = monotonic_now

    def healthcheck(self) -> bool:
        try:
            age = time.time() - self.heartbeat_path.stat().st_mtime
        except OSError:
            return False
        return 0 <= age <= self.health_max_age_seconds

    def _renew_loop(self, job_id: str, stopped: threading.Event, lease_lost: threading.Event) -> None:
        # Keep container health fresh even with multi-hour provider or OCR
        # calls. A short cadence also leaves room to retry a transient database
        # failure without pretending the lease was lost immediately.
        interval = max(
            5,
            min(
                30,
                self.lease_seconds // 3,
                self.health_max_age_seconds // 3,
            ),
        )
        last_confirmed = time.monotonic()
        wait_seconds = interval
        while not stopped.wait(wait_seconds):
            session = SessionLocal()
            try:
                if not renew_worker_job_lease(
                    session,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                ):
                    lease_lost.set()
                    return
                last_confirmed = time.monotonic()
                wait_seconds = interval
                self.write_heartbeat()
            except Exception:  # noqa: BLE001
                try:
                    session.rollback()
                except Exception:
                    pass
                logger.exception("Worker lease renewal failed queue=%s job=%s", self.queue, job_id)
                remaining = self.lease_seconds - (time.monotonic() - last_confirmed)
                if remaining <= 5:
                    lease_lost.set()
                    return
                wait_seconds = max(1, min(10, int(remaining - 5)))
            finally:
                session.close()

    def _finish_failure(
        self,
        job: WorkerJobSnapshot,
        *,
        code: str,
        retryable: bool,
        delay_seconds: int | None = None,
    ) -> str | None:
        session = SessionLocal()
        try:
            return mark_worker_job_failed(
                session,
                job_id=job.id,
                worker_id=self.worker_id,
                error_code=code,
                retryable=retryable,
                retry_delay_seconds=delay_seconds or _retry_delay(job.attempt_count),
            )
        finally:
            session.close()

    def _process(self, job: WorkerJobSnapshot) -> None:
        handler = self.handlers.get(job.kind)
        if handler is None:
            self._finish_failure(job, code="unsupported_kind", retryable=False)
            logger.error("Unsupported worker job queue=%s kind=%s", self.queue, job.kind)
            return

        lease_stopped = threading.Event()
        lease_lost = threading.Event()
        renew_thread = threading.Thread(
            target=self._renew_loop,
            args=(job.id, lease_stopped, lease_lost),
            name=f"{self.queue}-lease-{job.id[:8]}",
            daemon=True,
        )
        renew_thread.start()
        context = WorkerContext(
            job_id=job.id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            lease_lost=lease_lost,
        )
        metric_name = f"worker.{self.queue}.{job.kind}"
        started = time.monotonic()
        record_background_task_metric(metric_name, "started")
        try:
            context.raise_if_cancelled()
            result = _resolve_handler_result(handler(job, context))
            if lease_lost.is_set():
                logger.warning("Worker lease lost before completion queue=%s job=%s", self.queue, job.id)
                record_background_task_metric(metric_name, "lease_lost")
                return
            session = SessionLocal()
            try:
                changed = mark_worker_job_succeeded(
                    session,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    result=result,
                )
                cancelled = False
                if not changed:
                    # Success and cancellation race at one compare-and-swap
                    # boundary.  If cancellation committed first, acknowledge
                    # it immediately instead of leaving the row processing
                    # until its lease expires.  A lost lease cannot be
                    # finalized by this worker and remains a lease-lost event.
                    cancelled = mark_worker_job_cancelled(
                        session,
                        job_id=job.id,
                        worker_id=self.worker_id,
                    )
            finally:
                session.close()
            record_background_task_metric(
                metric_name,
                "succeeded" if changed else ("cancelled" if cancelled else "lease_lost"),
                (time.monotonic() - started) * 1000,
            )
        except JobCancelled:
            session = SessionLocal()
            try:
                mark_worker_job_cancelled(session, job_id=job.id, worker_id=self.worker_id)
            finally:
                session.close()
            record_background_task_metric(metric_name, "cancelled", (time.monotonic() - started) * 1000)
        except FatalJobError as exc:
            self._finish_failure(job, code=exc.code, retryable=False)
            record_background_task_metric(metric_name, "failed", (time.monotonic() - started) * 1000)
            logger.warning("Worker job rejected queue=%s kind=%s code=%s", self.queue, job.kind, exc.code)
        except RetryableJobError as exc:
            status = self._finish_failure(
                job,
                code=exc.code,
                retryable=True,
                delay_seconds=exc.delay_seconds,
            )
            record_background_task_metric(metric_name, status or "lease_lost", (time.monotonic() - started) * 1000)
            logger.warning("Worker job deferred queue=%s kind=%s code=%s", self.queue, job.kind, exc.code)
        except Exception:  # noqa: BLE001
            status = self._finish_failure(job, code="internal", retryable=True)
            record_background_task_metric(metric_name, status or "lease_lost", (time.monotonic() - started) * 1000)
            logger.exception("Worker job failed queue=%s kind=%s", self.queue, job.kind)
        finally:
            lease_stopped.set()
            renew_thread.join(timeout=5)
            self.write_heartbeat()

    def _process_batch(self, snapshots: list[WorkerJobSnapshot]) -> None:
        """Execute every claimed lease concurrently up to the bounded batch."""

        if len(snapshots) == 1:
            self._process(snapshots[0])
            return
        with ThreadPoolExecutor(
            max_workers=len(snapshots),
            thread_name_prefix=f"{self.queue}-job",
        ) as executor:
            # Resolving every future propagates an unexpected runtime failure
            # to the worker process instead of silently abandoning its claim.
            futures = [executor.submit(self._process, snapshot) for snapshot in snapshots]
            for future in futures:
                future.result()

    def run_forever(self) -> None:
        logger.info("Durable worker started queue=%s worker=%s", self.queue, self.worker_id)
        self.write_heartbeat(force=True)
        while not self.stop_event.is_set():
            self._reconcile_if_due()
            session = SessionLocal()
            try:
                rows = claim_worker_jobs(
                    session,
                    queue=self.queue,
                    worker_id=self.worker_id,
                    batch_size=self.batch_size,
                    lease_seconds=self.lease_seconds,
                )
                snapshots = [WorkerJobSnapshot.from_row(row) for row in rows]
                self.write_heartbeat()
            except Exception:  # noqa: BLE001
                session.rollback()
                snapshots = []
                logger.exception("Worker queue claim failed queue=%s", self.queue)
            finally:
                session.close()

            if snapshots:
                # Once claimed, start the full batch even if SIGTERM races this
                # boundary. Docker's grace window lets short work finish; long
                # work remains protected by lease recovery after process exit.
                self._process_batch(snapshots)
            if not snapshots:
                self.stop_event.wait(self.poll_seconds)
        self.write_heartbeat(force=True)
        logger.info("Durable worker stopped queue=%s worker=%s", self.queue, self.worker_id)


def run_worker_cli(worker: DurableQueueWorker, argv: list[str] | None = None) -> int:
    command = (argv or sys.argv[1:] or ["run"])[0]
    if command == "healthcheck":
        return 0 if worker.healthcheck() else 1
    if command != "run":
        print("Usage: python -m <worker-module> [run|healthcheck]", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    from app.telemetry.bootstrap import bootstrap_telemetry

    bootstrap_telemetry()
    try:
        worker.run_forever()
    finally:
        from app.telemetry import shutdown_telemetry

        shutdown_telemetry()
    return 0
