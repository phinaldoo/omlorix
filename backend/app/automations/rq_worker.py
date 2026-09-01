from __future__ import annotations

import logging

from app.telemetry.bootstrap import bootstrap_telemetry

try:
    from rq import Worker
except ImportError as exc:  # pragma: no cover - exercised in deployed worker image
    raise RuntimeError("RQ is required to run the automation worker") from exc


logger = logging.getLogger(__name__)


class TelemetryWorker(Worker):
    """RQ worker that initializes Omlorix telemetry before processing jobs."""

    def __init__(self, *args, **kwargs):
        self._omlorix_telemetry = bootstrap_telemetry()
        super().__init__(*args, **kwargs)

    def main_work_horse(self, job, queue):
        from app.telemetry import shutdown_telemetry

        # RQ forks work horses; reset inherited providers so exporters and
        # processor threads are initialized in the child process that runs jobs.
        shutdown_telemetry()
        bootstrap_telemetry()
        try:
            return super().main_work_horse(job, queue)
        finally:
            try:
                shutdown_telemetry()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to shutdown OpenTelemetry in RQ work horse")

    def work(self, *args, **kwargs):
        try:
            return super().work(*args, **kwargs)
        finally:
            try:
                from app.telemetry import shutdown_telemetry

                shutdown_telemetry()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to shutdown OpenTelemetry in RQ worker")
