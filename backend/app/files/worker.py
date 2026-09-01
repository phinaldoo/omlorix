"""Background worker responsible for pruning temporary uploaded files."""
from datetime import datetime, timezone
from pathlib import Path
import threading
import logging
import os

from app.database import SessionLocal
from app.files.sharing import delete_expired_artifact_shares
from app.utils.background import start_named_worker, stop_named_worker
from app.files.storage import get_local_user_files_base_dir



TEMP_CLEANUP_INTERVAL_SECONDS = max(30, int(os.getenv("FILE_TEMP_CLEANUP_INTERVAL_SECONDS", "300") or "300"))
ARTIFACT_SHARE_CLEANUP_INTERVAL_SECONDS = max(
    60,
    int(os.getenv("ARTIFACT_SHARE_CLEANUP_INTERVAL_SECONDS", "3600") or "3600"),
)
TEMP_FILE_MAX_AGE_SECONDS = max(60, int(os.getenv("FILE_TEMP_MAX_AGE_SECONDS", "3600") or "3600"))
MATERIALIZED_FILE_MAX_AGE_SECONDS = max(
    60,
    int(os.getenv("FILE_MATERIALIZED_TEMP_MAX_AGE_SECONDS", "900") or "900"),
)
BASE_STORAGE_DIR = get_local_user_files_base_dir()
TEMP_DIR = BASE_STORAGE_DIR / "temp"
MATERIALIZED_TEMP_DIR = TEMP_DIR / "materialized"
BASE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
MATERIALIZED_TEMP_DIR.mkdir(parents=True, exist_ok=True)



logger = logging.getLogger(__name__)



# -------------------
# Cleanup temp files
# -------------------
def cleanup_temp_files():
    """Remove stale temporary files and prune empty temp subdirectories."""
    try:
        current_time = datetime.now(timezone.utc)
        for file_path in TEMP_DIR.rglob("*"):
            if not file_path.is_file():
                continue

            max_age_seconds = TEMP_FILE_MAX_AGE_SECONDS
            try:
                relative_parts = file_path.relative_to(TEMP_DIR).parts
            except ValueError:
                relative_parts = ()
            if relative_parts and relative_parts[0] == "materialized":
                max_age_seconds = MATERIALIZED_FILE_MAX_AGE_SECONDS

            file_mtime = datetime.fromtimestamp(
                file_path.stat().st_mtime,
                tz=timezone.utc,
            )
            if (current_time - file_mtime).total_seconds() > max_age_seconds:
                file_path.unlink()

        # Remove empty directories under temp (keep the root and materialized dir).
        for dir_path in sorted((p for p in TEMP_DIR.rglob("*") if p.is_dir()), reverse=True):
            if dir_path in {TEMP_DIR, MATERIALIZED_TEMP_DIR}:
                continue
            try:
                dir_path.rmdir()
            except OSError:
                continue
    except Exception as exc:
        logger.debug("Temp file cleanup failed", exc_info=exc)


def cleanup_expired_artifact_share_rows(db=None) -> int:
    """Delete expired artifact share rows."""
    own_session = db is None
    session = db or SessionLocal()
    try:
        deleted = delete_expired_artifact_shares(session)
        if deleted:
            logger.info("[Files] Deleted %s expired artifact share rows", deleted)
        return deleted
    finally:
        if own_session:
            try:
                session.close()
            except Exception:
                pass



# -------------------
# Temp cleanup worker
# -------------------
def _temp_cleanup_worker(stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            cleanup_temp_files()
        except Exception:
            logger.exception("[Files] Temporary file cleanup failed")
        if stop_event.wait(TEMP_CLEANUP_INTERVAL_SECONDS):
            break


def _artifact_share_cleanup_worker(stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            cleanup_expired_artifact_share_rows()
        except Exception:
            logger.exception("[Files] Canvas share cleanup failed")
        if stop_event.wait(ARTIFACT_SHARE_CLEANUP_INTERVAL_SECONDS):
            break



def start_temp_file_cleanup_worker():
    """Start background worker that periodically prunes temporary uploaded files."""
    return start_named_worker(
        "files_temp_cleanup",
        _temp_cleanup_worker,
        logger,
        start_message="[Files] Temporary file cleanup worker started",
        already_running_message="[Files] Temporary file cleanup worker already running",
        failure_message="[Files] Failed to start temporary file cleanup worker",
    )


def start_artifact_share_cleanup_worker():
    """Start background worker that periodically prunes expired artifact share rows."""
    return start_named_worker(
        "artifact_share_cleanup",
        _artifact_share_cleanup_worker,
        logger,
        start_message="[Files] Canvas share cleanup worker started",
        already_running_message="[Files] Canvas share cleanup worker already running",
        failure_message="[Files] Failed to start canvas share cleanup worker",
    )



def stop_temp_file_cleanup_worker(timeout: float = 5.0):
    """Stop the temporary file cleanup worker."""
    stop_named_worker(
        "files_temp_cleanup",
        logger,
        timeout=timeout,
        stopped_message="[Files] Temporary file cleanup worker stopped",
        not_running_message="[Files] Temporary file cleanup worker was not running",
        failure_message="[Files] Failed to stop temporary file cleanup worker",
    )


def stop_artifact_share_cleanup_worker(timeout: float = 5.0):
    """Stop the canvas share cleanup worker."""
    stop_named_worker(
        "artifact_share_cleanup",
        logger,
        timeout=timeout,
        stopped_message="[Files] Canvas share cleanup worker stopped",
        not_running_message="[Files] Canvas share cleanup worker was not running",
        failure_message="[Files] Failed to stop canvas share cleanup worker",
    )
