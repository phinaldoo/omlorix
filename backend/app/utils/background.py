from concurrent.futures import ThreadPoolExecutor
import threading
import time
from typing import Callable, Dict, Optional, Tuple, Any

from app.telemetry.metrics import record_background_task_metric


WorkerTarget = Callable[[threading.Event], None]


# Shared thread pool for long-lived ad-hoc background tasks like chat generation.
# Adjust max_workers as needed based on server capacity.
background_task_executor = ThreadPoolExecutor(max_workers=50)

# Keep title generation isolated so chat streaming workers cannot block title work
# they later wait on before completing the stream.
title_generation_executor = ThreadPoolExecutor(max_workers=10)


class BackgroundWorkerManager:
    """Manage lifecycle of background daemon threads with cooperative stop events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: Dict[str, Tuple[threading.Thread, threading.Event]] = {}

    def start_worker(
        self,
        name: str,
        target: WorkerTarget,
        *,
        args: Tuple[Any, ...] = (),
        kwargs: Optional[Dict[str, Any]] = None,
        daemon: bool = True,
        restart: bool = False,
    ) -> threading.Thread:
        """Start a background worker if not already running.

        The worker ``target`` must accept a ``threading.Event`` as first positional
        argument which is used to signal shutdown.
        """
        if kwargs is None:
            kwargs = {}

        with self._lock:
            existing = self._workers.get(name)
            if existing and existing[0].is_alive():
                if not restart:
                    return existing[0]
                # Stop the existing worker before restarting
                self._stop_locked(name, timeout=0.0)

            stop_event = threading.Event()
            thread_args = (stop_event, *args)
            thread = threading.Thread(
                target=target,
                args=thread_args,
                kwargs=kwargs,
                daemon=daemon,
                name=name,
            )
            thread.start()
            self._workers[name] = (thread, stop_event)
            return thread

    def get_worker(self, name: str) -> Optional[Tuple[threading.Thread, threading.Event]]:
        """Return the tracked worker tuple if present."""
        with self._lock:
            return self._workers.get(name)

    def is_running(self, name: str) -> bool:
        worker = self.get_worker(name)
        return bool(worker and worker[0].is_alive())

    def stop_worker(self, name: str, *, timeout: float = 5.0) -> None:
        """Signal a worker to stop and join its thread."""
        with self._lock:
            if name not in self._workers:
                return
            thread, stop_event = self._workers.pop(name)
            stop_event.set()

        if thread.is_alive():
            thread.join(timeout=timeout)

    def stop_all(self, *, timeout: float = 5.0) -> None:
        """Stop all tracked workers."""
        with self._lock:
            items = list(self._workers.items())
            self._workers.clear()

        for name, (thread, stop_event) in items:
            stop_event.set()
            if thread.is_alive():
                thread.join(timeout=timeout)

    def _stop_locked(self, name: str, *, timeout: float) -> None:
        thread, stop_event = self._workers.pop(name, (None, None))
        if not thread or not stop_event:
            return
        stop_event.set()
        if thread.is_alive():
            thread.join(timeout=timeout)


worker_manager = BackgroundWorkerManager()


def start_named_worker(
    name: str,
    target: WorkerTarget,
    logger,
    *,
    restart: bool = False,
    args: Tuple[Any, ...] = (),
    kwargs: Optional[Dict[str, Any]] = None,
    daemon: bool = True,
    start_message: str | None = None,
    already_running_message: str | None = None,
    failure_message: str | None = None,
) -> Optional[threading.Thread]:
    """Shared helper to start a named background worker with consistent logging."""

    if kwargs is None:
        kwargs = {}

    existing = worker_manager.get_worker(name)
    if not restart and existing and existing[0].is_alive():
        if already_running_message:
            logger.info(already_running_message)
        return existing[0]

    try:
        def _instrumented_target(stop_event: threading.Event, *target_args: Any, **target_kwargs: Any) -> None:
            started_at = time.monotonic()
            record_background_task_metric(name, "started")
            try:
                target(stop_event, *target_args, **target_kwargs)
            except Exception:
                record_background_task_metric(
                    name,
                    "failed",
                    duration_ms=(time.monotonic() - started_at) * 1000,
                )
                raise
            record_background_task_metric(
                name,
                "completed",
                duration_ms=(time.monotonic() - started_at) * 1000,
            )

        thread = worker_manager.start_worker(
            name,
            _instrumented_target,
            args=args,
            kwargs=kwargs,
            daemon=daemon,
            restart=restart,
        )
        if start_message:
            logger.info(start_message)
        return thread
    except Exception as exc:
        if failure_message:
            logger.error("%s (reason: %s)", failure_message, exc, exc_info=True)
        else:
            logger.error("[%s] Failed to start background worker: %s", name, exc, exc_info=True)
        return None


def stop_named_worker(
    name: str,
    logger,
    *,
    timeout: float = 5.0,
    stopped_message: str | None = None,
    not_running_message: str | None = None,
    failure_message: str | None = None,
) -> None:
    """Shared helper to stop a named background worker with consistent logging."""

    existing = worker_manager.get_worker(name)
    if not existing or not existing[0].is_alive():
        if not_running_message:
            logger.info(not_running_message)
        return

    try:
        worker_manager.stop_worker(name, timeout=timeout)
        if stopped_message:
            logger.info(stopped_message)
    except Exception as exc:
        if failure_message:
            logger.exception("%s (reason: %s)", failure_message, exc)
        else:
            logger.exception("[%s] Failed to stop background worker", name)
