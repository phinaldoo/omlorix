from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import threading

from fastapi import HTTPException

from app.database import AuditSessionLocal, SessionLocal
from app.files.models import FileProcessingArtifact
from app.logging.models import scrub_share_capability_references_in_audit_logs
from app.workers.files import FILE_PROCESSING_CACHE_DIR, resolve_cached_preview_path
from app.workers.models import (
    AuditEventOutbox,
    JOB_TERMINAL_STATUSES,
    QUEUE_MAINTENANCE,
    expire_worker_jobs,
    purge_terminal_worker_jobs,
)
from app.workers.runtime import DurableQueueWorker, run_worker_cli


logger = logging.getLogger(__name__)
_ENABLE_VALUES = {"1", "true", "yes", "on", "external", "worker"}


def external_maintenance_enabled() -> bool:
    return str(os.getenv("MAINTENANCE_WORKER_MODE", "inline") or "inline").strip().lower() in _ENABLE_VALUES


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _clean_file_processing_cache() -> int:
    session = SessionLocal()
    removed = 0
    try:
        retention_days = _bounded_int("FILE_PROCESSING_CACHE_RETENTION_DAYS", 30, 1, 365)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        rows = (
            session.query(FileProcessingArtifact)
            .filter(
                FileProcessingArtifact.status.in_(JOB_TERMINAL_STATUSES),
                FileProcessingArtifact.updated_at < cutoff,
            )
            .order_by(FileProcessingArtifact.updated_at.asc())
            .limit(1000)
            .all()
        )
        for row in rows:
            if row.cache_path:
                try:
                    resolve_cached_preview_path(row.cache_path).unlink(missing_ok=True)
                except HTTPException:
                    pass
            session.delete(row)
            removed += 1
        session.commit()

        # Rows can disappear through source-file cascades. Sweep only bounded,
        # old cache files and retain those still referenced by an artifact.
        if FILE_PROCESSING_CACHE_DIR.exists():
            candidates = [
                path
                for path in FILE_PROCESSING_CACHE_DIR.glob("*/*")
                if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff
            ][:_bounded_int("FILE_PROCESSING_ORPHAN_SCAN_BATCH_SIZE", 1000, 10, 5000)]
            relative_paths = [str(path.relative_to(FILE_PROCESSING_CACHE_DIR)) for path in candidates]
            referenced = {
                value
                for (value,) in session.query(FileProcessingArtifact.cache_path)
                .filter(FileProcessingArtifact.cache_path.in_(relative_paths))
                .all()
                if value
            }
            for path, relative in zip(candidates, relative_paths, strict=True):
                if relative not in referenced:
                    path.unlink(missing_ok=True)
                    removed += 1
    except Exception:
        session.rollback()
        logger.exception("File processing cache maintenance failed")
    finally:
        session.close()
    return removed


def _durable_queue_maintenance() -> None:
    session = SessionLocal()
    try:
        expire_worker_jobs(session, batch_size=1000)
        purge_terminal_worker_jobs(
            session,
            retention_days=_bounded_int("WORKER_JOB_RETENTION_DAYS", 7, 1, 90),
            batch_size=1000,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=_bounded_int("AUDIT_EVENT_OUTBOX_RETENTION_DAYS", 7, 1, 90)
        )
        session.query(AuditEventOutbox).filter(
            AuditEventOutbox.status.in_(("delivered", "cancelled")),
            AuditEventOutbox.updated_at < cutoff,
        ).delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Durable worker queue maintenance failed")
    finally:
        session.close()


def _audit_scrub() -> None:
    session = AuditSessionLocal()
    try:
        scrubbed = scrub_share_capability_references_in_audit_logs(session, max_batches=20)
        if scrubbed:
            logger.info("Scrubbed share capability references from %s audit rows", scrubbed)
    except Exception:
        session.rollback()
        logger.exception("Audit share-capability scrub failed")
    finally:
        session.close()


def _clean_ephemeral_worker_staging() -> int:
    from app.backups.service import cleanup_stale_backup_work_files
    from app.workers.media import MEDIA_STAGING_DIR
    from app.workers.operations import (
        OPERATIONS_RESULT_DIR,
        cleanup_import_staging_reservations,
    )
    from app.workers.rendering import RENDERING_STAGING_DIR

    configured_default = _bounded_int(
        "OPERATIONS_STAGING_RETENTION_HOURS", 96, 72, 336
    )
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=_bounded_int(
            "WORKER_STAGING_RETENTION_HOURS", configured_default, 72, 336
        )
    )
    removed = cleanup_stale_backup_work_files(
        retention_hours=_bounded_int(
            "WORKER_STAGING_RETENTION_HOURS",
            configured_default,
            72,
            336,
        ),
        batch_size=1000,
    )
    removed += cleanup_import_staging_reservations(cutoff=cutoff, batch_size=5000)
    for root in (
        OPERATIONS_RESULT_DIR,
        MEDIA_STAGING_DIR,
        RENDERING_STAGING_DIR,
    ):
        if not root.exists():
            continue
        paths = list(root.iterdir())[:5000]
        for path in paths:
            try:
                if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                logger.debug("Could not inspect operations staging file %s", path)
    return removed


def _connectivity_worker_enabled() -> bool:
    """Mirror API inline-mode settings before the worker's startup network call."""

    from app.settings.utils import coerce_bool, get_value_by_page_and_key

    session = SessionLocal()
    try:
        try:
            offline = coerce_bool(
                get_value_by_page_and_key("general", "offline_mode", session),
                default=False,
            )
        except Exception:
            offline = False
        try:
            enabled = coerce_bool(
                get_value_by_page_and_key(
                    "general",
                    "internet_connectivity_check_enabled",
                    session,
                ),
                default=True,
            )
        except Exception:
            enabled = True
        return enabled and not offline
    finally:
        session.close()


class MaintenanceWorker(DurableQueueWorker):
    """Own all periodic retention, cleanup, provider-sync and statistics loops."""

    def __init__(self) -> None:
        super().__init__(
            queue=QUEUE_MAINTENANCE,
            handlers={},
            default_lease_seconds=300,
        )
        self._maintenance_stop = threading.Event()
        self._maintenance_thread: threading.Thread | None = None

    def request_stop(self, *_args) -> None:
        super().request_stop(*_args)
        self._maintenance_stop.set()

    def _housekeeping_loop(self) -> None:
        interval = _bounded_int("MAINTENANCE_HOUSEKEEPING_INTERVAL_SECONDS", 300, 30, 86400)
        while not self._maintenance_stop.is_set():
            _durable_queue_maintenance()
            _clean_file_processing_cache()
            _clean_ephemeral_worker_staging()
            _audit_scrub()
            self.write_heartbeat()
            self._maintenance_stop.wait(interval)

    def _start_component_workers(self) -> None:
        from app.admin.concurrency.models import start_concurrency_metrics_maintenance_worker
        from app.chats.read_aloud import start_read_aloud_cleanup_worker
        from app.chats.worker import start_auto_delete_chats_worker
        from app.files.worker import start_artifact_share_cleanup_worker, start_temp_file_cleanup_worker
        from app.llm.worker import start_llm_provider_worker
        from app.llmstats.worker import start_byok_stats_retention_worker
        from app.logging.worker import start_auth_log_retention_worker
        from app.realtime.worker import start_realtime_enforcement_worker
        from app.utils.utils import start_internet_connectivity_checker_worker

        start_llm_provider_worker()
        start_realtime_enforcement_worker()
        start_auto_delete_chats_worker()
        if _connectivity_worker_enabled():
            start_internet_connectivity_checker_worker()
        start_temp_file_cleanup_worker()
        start_artifact_share_cleanup_worker()
        start_auth_log_retention_worker()
        start_byok_stats_retention_worker()
        start_read_aloud_cleanup_worker()
        start_concurrency_metrics_maintenance_worker()

    def _stop_component_workers(self) -> None:
        from app.admin.concurrency.models import stop_concurrency_metrics_maintenance_worker
        from app.chats.read_aloud import stop_read_aloud_cleanup_worker
        from app.chats.worker import stop_auto_delete_chats_worker
        from app.files.worker import stop_artifact_share_cleanup_worker, stop_temp_file_cleanup_worker
        from app.llm.worker import stop_llm_provider_worker
        from app.llmstats.worker import stop_byok_stats_retention_worker
        from app.logging.worker import stop_auth_log_retention_worker
        from app.realtime.worker import stop_realtime_enforcement_worker
        from app.utils.utils import stop_internet_connectivity_checker_worker

        stoppers = (
            stop_internet_connectivity_checker_worker,
            stop_auto_delete_chats_worker,
            stop_artifact_share_cleanup_worker,
            stop_temp_file_cleanup_worker,
            stop_llm_provider_worker,
            stop_realtime_enforcement_worker,
            stop_auth_log_retention_worker,
            stop_byok_stats_retention_worker,
            stop_read_aloud_cleanup_worker,
            stop_concurrency_metrics_maintenance_worker,
        )
        for stopper in stoppers:
            try:
                stopper()
            except Exception:
                logger.exception("Failed stopping maintenance component %s", stopper.__name__)

    def run_forever(self) -> None:
        self._start_component_workers()
        self._maintenance_stop.clear()
        self._maintenance_thread = threading.Thread(
            target=self._housekeeping_loop,
            name="maintenance-housekeeping",
            daemon=True,
        )
        self._maintenance_thread.start()
        try:
            super().run_forever()
        finally:
            self._maintenance_stop.set()
            self._maintenance_thread.join(timeout=10)
            self._stop_component_workers()


def build_worker() -> MaintenanceWorker:
    return MaintenanceWorker()


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
