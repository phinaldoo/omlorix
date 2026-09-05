"""Immutable playback files with durable, bounded expiry cleanup."""

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import tempfile

from app.database import SessionLocal
from app.files.storage import (
    get_local_user_files_base_dir,
    get_user_file_storage_adapter_for_provider,
    get_user_file_storage_config,
)
from app.tools.slide_presentation.models import PresentationPlaybackDocument

logger = logging.getLogger(__name__)


def _temporary_path() -> Path:
    directory = get_local_user_files_base_dir() / "temp" / "materialized"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="playback-", suffix=".html", dir=directory, delete=False) as handle:
        return Path(handle.name)


def store_document(*, frame_id: str, owner_hash: str, html: str, ttl: int) -> dict:
    provider = get_user_file_storage_config().provider
    storage_key = f"_playback_frames/{owner_hash}/{frame_id}.html"
    # Register before uploading, so process crashes cannot orphan remote files.
    # Cleanup trails token expiry by 15 minutes to cover in-flight transfers.
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    with SessionLocal() as db:
        db.add(PresentationPlaybackDocument(
            id=frame_id, storage_provider=provider, storage_key=storage_key,
            expires_at=expires_at,
        ))
        db.commit()
    path = _temporary_path()
    try:
        path.write_text(html, encoding="utf-8")
        get_user_file_storage_adapter_for_provider(provider).upload_file(path, storage_key)
        with SessionLocal() as db:
            row = db.get(PresentationPlaybackDocument, frame_id)
            row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl + 900)
            db.commit()
    except Exception:
        # Keep the ledger if deletion fails; maintenance will retry it.
        discard_document(frame_id)
        raise
    finally:
        path.unlink(missing_ok=True)
    return {"storage_provider": provider, "storage_key": storage_key}


def materialize_document(document: dict) -> Path:
    path = _temporary_path()
    try:
        get_user_file_storage_adapter_for_provider(document["storage_provider"]).download_file(
            document["storage_key"], path,
        )
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _delete_stored_document(row) -> None:
    try:
        get_user_file_storage_adapter_for_provider(row.storage_provider).delete_file(row.storage_key)
    except Exception as exc:
        # GCS/Azure report missing objects rather than treating DELETE as a no-op.
        if not isinstance(exc, FileNotFoundError) and getattr(exc, "status_code", None) != 404 and getattr(exc, "code", None) != 404:
            raise


def discard_document(frame_id: str) -> None:
    try:
        with SessionLocal() as db:
            row = db.get(PresentationPlaybackDocument, frame_id)
            if row is not None:
                _delete_stored_document(row)
                db.delete(row)
                db.commit()
    except Exception:
        logger.warning("Playback document cleanup deferred")


def cleanup_expired_documents(*, batch_size: int = 100) -> int:
    """Run from existing inline/external file maintenance, even without Redis."""
    removed = 0
    with SessionLocal() as db:
        rows = (
            db.query(PresentationPlaybackDocument)
            .filter(PresentationPlaybackDocument.expires_at <= datetime.now(timezone.utc))
            .order_by(PresentationPlaybackDocument.expires_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .all()
        )
        for row in rows:
            try:
                _delete_stored_document(row)
            except Exception:
                # Back off failed objects so they cannot starve later batches.
                row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                logger.warning("Expired playback document cleanup deferred")
                continue
            db.delete(row)
            removed += 1
        db.commit()
    return removed
