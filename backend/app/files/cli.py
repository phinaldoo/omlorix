from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.files.models import Files
from app.files.storage import (
    build_storage_key,
    get_user_file_storage_adapter,
    get_user_file_storage_adapter_for_provider,
    get_user_file_storage_config,
    normalize_storage_provider,
)
from app.files.storage.base import UserFileStorageAdapter
from app.files.storage.paths import ensure_user_scoped_storage_key
from app.tools.deep_research.models import (
    DeepResearchRun,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_RUNNING,
)
from app.tools.deep_research.storage import build_deep_research_storage_prefix
from app.tools.slide_presentation.models import SlidePresentations
from app.tools.slide_presentation.storage import build_presentation_storage_prefix


class DestinationObjectConflictError(RuntimeError):
    """Raised when a destination key already contains different file bytes."""


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Dates must use YYYY-MM-DD format") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _iter_storage_records(
    db: Session,
    *,
    source_provider: str,
    user_id: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    batch_size: int,
):
    """Yield source-provider file rows in stable batches.

    The cursor is based on immutable creation time and primary key values. A
    successful migration changes the row's provider, but does not disturb the
    cursor or cause the same row to be selected again.
    """
    cursor_created = None
    cursor_id = None
    while True:
        query = db.query(Files).filter(Files.storage_provider == source_provider)
        if user_id:
            query = query.filter(Files.user_id == user_id)
        if created_after:
            query = query.filter(Files.created_at >= created_after)
        if created_before:
            query = query.filter(Files.created_at <= created_before)
        if cursor_created is not None and cursor_id is not None:
            query = query.filter(
                (Files.created_at > cursor_created)
                | ((Files.created_at == cursor_created) & (Files.id > cursor_id))
            )

        rows = (
            query.order_by(Files.created_at.asc(), Files.id.asc())
            .limit(max(1, int(batch_size)))
            .all()
        )
        if not rows:
            return

        for row in rows:
            yield row

        tail = rows[-1]
        cursor_created = tail.created_at
        cursor_id = tail.id


def _iter_ordered_artifact_records(
    query,
    *,
    created_column,
    id_column,
    batch_size: int = 100,
):
    """Yield artifact owners without keeping a server cursor across commits.

    Artifact migrations commit after each complete workspace or presentation.
    PostgreSQL invalidates a streaming/named cursor at that transaction
    boundary, so ``yield_per`` cannot safely drive this workflow. Keyset pages
    are fully materialized before processing and use immutable ordering fields
    to continue after provider metadata changes.
    """
    cursor_created = None
    cursor_id = None
    while True:
        page_query = query
        if cursor_created is not None and cursor_id is not None:
            page_query = page_query.filter(
                (created_column > cursor_created)
                | ((created_column == cursor_created) & (id_column > cursor_id))
            )

        rows = (
            page_query.order_by(created_column.asc(), id_column.asc())
            .limit(max(1, int(batch_size)))
            .all()
        )
        if not rows:
            return

        for row in rows:
            yield row

        tail = rows[-1]
        cursor_created = getattr(tail, created_column.key)
        cursor_id = getattr(tail, id_column.key)


def _storage_key_for_record(file_row: Files) -> str:
    """Return a validated, user-scoped storage key for a file record.

    Older local rows can have an empty storage key. Those rows use the same
    deterministic user/file layout as current uploads.
    """
    user_id = str(file_row.user_id)
    recorded_key = str(getattr(file_row, "storage_key", "") or "").strip()
    if not recorded_key:
        recorded_key = build_storage_key(user_id, str(file_row.file_name))
    return ensure_user_scoped_storage_key(user_id, recorded_key)


def _record_was_migrated_from(file_row: Files, source_provider: str) -> bool:
    """Return whether a row carries our explicit migration provenance marker.

    The marker lets operators safely select only the files moved by an earlier
    migration. This is important for round trips: a destination may already
    contain unrelated files that must not be moved or deleted accidentally.
    """
    metadata = getattr(file_row, "storage_meta", None)
    if not isinstance(metadata, dict):
        return False
    migration = metadata.get("migration")
    return (
        isinstance(migration, dict)
        and str(migration.get("source_provider") or "") == source_provider
    )


def _build_destination_metadata(
    upload_meta: dict[str, Any],
    *,
    source_provider: str,
    destination_provider: str,
    source_storage_key: str,
    destination_storage_key: str,
) -> dict[str, Any]:
    """Add auditable provenance to metadata stored after a successful copy."""
    destination_meta = dict(upload_meta)
    destination_meta["migration"] = {
        "source_provider": source_provider,
        "destination_provider": destination_provider,
        "source_storage_key": source_storage_key,
        "destination_storage_key": destination_storage_key,
        "migrated_at": datetime.now(timezone.utc).isoformat(),
    }
    return destination_meta


def _legacy_local_source_path(
    adapter: UserFileStorageAdapter,
    storage_key: str,
) -> Path | None:
    """Find a legacy root-level local file for an otherwise missing key.

    Early local-storage deployments placed files directly under the base
    directory while newer records use ``<user-id>/<file-name>``. Migration is
    the right place to normalize that old layout: the copied destination uses
    the database key, and the old root-level source remains untouched unless
    the operator explicitly requests source cleanup.
    """
    base_path = getattr(adapter, "base_path", None)
    if base_path is None:
        return None
    base = Path(base_path).resolve()
    normalized_key = str(storage_key).strip().lstrip("/")
    current_path = (base / normalized_key).resolve()
    if current_path.parent == base or current_path.is_file():
        return None
    legacy_path = (base / Path(normalized_key).name).resolve()
    if legacy_path.parent != base or not legacy_path.is_file():
        return None
    return legacy_path


def _download_and_hash(
    adapter: UserFileStorageAdapter,
    storage_key: str,
    target_path: Path,
    *,
    legacy_local_source_path: Path | None = None,
) -> tuple[int, str, bool]:
    """Download one object and report size, digest, and legacy fallback use."""
    used_legacy_source = False
    try:
        adapter.download_file(storage_key, target_path)
    except FileNotFoundError:
        if legacy_local_source_path is None:
            raise
        # Copy the legacy source into the same temporary target used for every
        # other provider, so all subsequent checksum and upload code is shared.
        shutil.copy2(legacy_local_source_path, target_path)
        used_legacy_source = True
    if not target_path.exists() or not target_path.is_file():
        raise FileNotFoundError("storage adapter did not produce the downloaded file")
    return int(target_path.stat().st_size), _sha256(target_path), used_legacy_source


def _copy_storage_object(
    *,
    source_adapter: UserFileStorageAdapter,
    destination_adapter: UserFileStorageAdapter,
    storage_key: str,
    force: bool,
    legacy_local_source_path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    """Copy and verify one object between storage adapters.

    Returns provider metadata and whether an already-correct destination was
    resumed. Existing destination objects are never trusted solely on an
    existence check: their bytes must match the source before the database
    reference can move.
    """
    with tempfile.TemporaryDirectory(prefix="omlorix-file-migrate-") as temp_dir:
        temp_root = Path(temp_dir)
        source_path = temp_root / "source"
        destination_check_path = temp_root / "destination"
        source_size, source_hash, used_legacy_source = _download_and_hash(
            source_adapter,
            storage_key,
            source_path,
            legacy_local_source_path=legacy_local_source_path,
        )

        destination_exists = destination_adapter.exists(storage_key)
        if destination_exists and not force:
            destination_size, destination_hash, _ = _download_and_hash(
                destination_adapter,
                storage_key,
                destination_check_path,
            )
            if destination_size != source_size or destination_hash != source_hash:
                raise DestinationObjectConflictError(
                    "destination object already exists with different content; "
                    "inspect it or rerun with --force"
                )
            metadata = {
                "size_bytes": source_size,
                "sha256": source_hash,
                "resume": True,
            }
            if used_legacy_source:
                metadata["_legacy_local_source_used"] = True
            return metadata, True

        upload_meta = dict(
            destination_adapter.upload_file(source_path, storage_key) or {}
        )
        if not destination_adapter.exists(storage_key):
            raise RuntimeError(
                "uploaded object did not pass the destination existence check"
            )

        destination_size, destination_hash, _ = _download_and_hash(
            destination_adapter,
            storage_key,
            destination_check_path,
        )
        if destination_size != source_size or destination_hash != source_hash:
            raise RuntimeError("checksum verification failed after destination upload")

        upload_meta.setdefault("size_bytes", source_size)
        upload_meta.setdefault("sha256", source_hash)
        if used_legacy_source:
            upload_meta["_legacy_local_source_used"] = True
        else:
            upload_meta.pop("_legacy_local_source_used", None)
        return upload_meta, False


def _run_with_retries(
    operation: Callable[[], tuple[dict[str, Any], bool]],
    *,
    retries: int,
) -> tuple[dict[str, Any], bool]:
    """Run one storage copy with bounded retries for transient failures."""
    attempts = max(1, int(retries))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except DestinationObjectConflictError:
            # A conflicting destination is deterministic and should never be
            # hammered repeatedly as though it were a transient network error.
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < attempts:
                time.sleep(0.5)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("storage migration failed without an error")


def _update_storage_reference(
    db: Session,
    *,
    file_row: Files,
    source_provider: str,
    destination_provider: str,
    destination_storage_key: str,
    destination_meta: dict[str, Any],
) -> None:
    """Atomically move a database row if its source reference is unchanged."""
    recorded_source_key = str(getattr(file_row, "storage_key", "") or "").strip()
    updated_rows = (
        db.query(Files)
        .filter(
            Files.id == file_row.id,
            Files.storage_provider == source_provider,
            Files.storage_key == recorded_source_key,
            Files.last_updated_at == file_row.last_updated_at,
        )
        .update(
            {
                Files.storage_provider: destination_provider,
                Files.storage_key: destination_storage_key,
                Files.storage_meta: destination_meta,
            },
            synchronize_session=False,
        )
    )
    if updated_rows != 1:
        db.rollback()
        raise RuntimeError(
            "file record changed while it was being migrated; destination copy was retained for resume"
        )
    db.commit()


def _restore_source_reference(
    db: Session,
    *,
    file_id: str,
    last_updated_at: datetime,
    source_provider: str,
    source_storage_key: str,
    source_storage_meta: Any,
    destination_provider: str,
    destination_storage_key: str,
) -> bool:
    """Restore a source reference when post-commit deletion safely failed.

    The verified destination object is intentionally retained. A later run can
    detect that matching object, skip the upload, and retry the database move
    plus source cleanup.
    """
    restored_rows = (
        db.query(Files)
        .filter(
            Files.id == file_id,
            Files.storage_provider == destination_provider,
            Files.storage_key == destination_storage_key,
            Files.last_updated_at == last_updated_at,
        )
        .update(
            {
                Files.storage_provider: source_provider,
                Files.storage_key: source_storage_key,
                Files.storage_meta: source_storage_meta,
            },
            synchronize_session=False,
        )
    )
    if restored_rows != 1:
        db.rollback()
        return False
    db.commit()
    return True


def _delete_source_with_retries(
    adapter: UserFileStorageAdapter,
    storage_key: str,
    *,
    retries: int,
) -> None:
    """Delete an old source object after its database reference has moved."""
    attempts = max(1, int(retries))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            adapter.delete_file(storage_key)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < attempts:
                time.sleep(0.5)
    if last_exc is not None:
        raise last_exc


def _delete_legacy_local_source_with_retries(
    source_path: Path,
    *,
    retries: int,
) -> bool:
    """Delete a verified legacy flat-layout source and report actual removal."""
    attempts = max(1, int(retries))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            source_path.unlink()
            return True
        except FileNotFoundError:
            # Another process may already have removed the legacy source. It
            # is clean, but this invocation must not count it as a deletion.
            return False
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < attempts:
                time.sleep(0.5)
    if last_exc is not None:
        raise last_exc
    return False


def _normalize_artifact_relative_path(value: Any) -> str:
    """Return a traversal-safe relative artifact path."""
    raw = str(value or "").strip()
    if not raw or "\\" in raw:
        raise ValueError("artifact path must be a non-empty POSIX relative path")
    normalized = PurePosixPath(raw)
    if normalized.is_absolute() or any(
        part in {"", ".", ".."} for part in normalized.parts
    ):
        raise ValueError("artifact path contains invalid traversal")
    return normalized.as_posix()


def _migration_marker(
    *,
    source_provider: str,
    destination_provider: str,
) -> dict[str, str]:
    """Build one consistent provenance marker for every storage-backed record."""
    return {
        "source_provider": source_provider,
        "destination_provider": destination_provider,
        "migrated_at": datetime.now(timezone.utc).isoformat(),
    }


def _metadata_was_migrated_from(metadata: Any, source_provider: str) -> bool:
    """Check migration provenance stored directly in a metadata mapping."""
    if not isinstance(metadata, dict):
        return False
    migration = metadata.get("migration")
    return (
        isinstance(migration, dict)
        and str(migration.get("source_provider") or "") == source_provider
    )


def _local_artifact_paths(
    adapter: UserFileStorageAdapter,
    storage_prefix: str,
) -> list[str]:
    """Enumerate every file beneath a validated local artifact prefix."""
    base_path = getattr(adapter, "base_path", None)
    if base_path is None:
        raise RuntimeError("local artifact migration requires a local storage adapter")
    base = Path(base_path).resolve()
    prefix = _normalize_artifact_relative_path(storage_prefix)
    root = (base / prefix).resolve()
    try:
        root.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            "artifact storage prefix escapes the local storage root"
        ) from exc
    if not root.exists():
        return []
    if not root.is_dir():
        raise ValueError("artifact storage prefix is not a directory")
    return [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _manifest_relative_paths(metadata: Any) -> list[str]:
    """Read a persisted artifact manifest while rejecting unsafe entries."""
    if not isinstance(metadata, dict):
        return []
    raw_paths = metadata.get("uploaded_files")
    if not isinstance(raw_paths, list):
        objects = metadata.get("objects")
        if isinstance(objects, list):
            raw_paths = [
                item.get("relative_path") for item in objects if isinstance(item, dict)
            ]
    if not isinstance(raw_paths, list):
        return []
    return list(
        dict.fromkeys(_normalize_artifact_relative_path(value) for value in raw_paths)
    )


def _deep_research_storage_metadata(run: DeepResearchRun) -> dict[str, Any]:
    """Return the persisted storage document for one Deep Research run."""
    result_meta = run.result_meta if isinstance(run.result_meta, dict) else {}
    storage = result_meta.get("storage")
    return dict(storage) if isinstance(storage, dict) else {}


def _deep_research_storage_provider(run: DeepResearchRun) -> str | None:
    """Resolve a run's provider when its persisted metadata is authoritative.

    Completed legacy runs predate external workspace persistence and are local.
    A terminal interrupted run can have verified checkpoint objects but no
    final storage document (for example, when its final upload failed). The
    provider of those older checkpoints is unknown, so callers must probe the
    explicitly selected source rather than silently classifying it as local.
    """
    storage = _deep_research_storage_metadata(run)
    if storage.get("provider"):
        return normalize_storage_provider(storage["provider"])
    status = str(getattr(run, "status", RUN_STATUS_COMPLETED) or "").strip().lower()
    if status == RUN_STATUS_COMPLETED:
        return "local"
    return None


def _known_deep_research_paths(run: DeepResearchRun) -> list[str]:
    """Build a bounded fallback manifest for older external run records."""
    result_meta = run.result_meta if isinstance(run.result_meta, dict) else {}
    candidates: list[Any] = [
        run.final_report_path,
        run.final_html_path,
        run.manifest_path,
        result_meta.get("archive_path"),
        "artifacts.json",
        "citations.json",
        "session.json",
        "workspace.zip",
    ]
    checkpoints = result_meta.get("checkpoints")
    if isinstance(checkpoints, dict):
        for checkpoint in checkpoints.values():
            if isinstance(checkpoint, dict) and isinstance(
                checkpoint.get("files"), list
            ):
                candidates.extend(checkpoint["files"])
    for artifact in run.artifacts or []:
        if isinstance(artifact, dict):
            candidates.append(artifact.get("relative_path"))

    normalized: list[str] = []
    for value in candidates:
        if not value:
            continue
        try:
            normalized.append(_normalize_artifact_relative_path(value))
        except ValueError:
            # Older run metadata can contain one malformed artifact path. It
            # must not hide the remaining valid fallback-manifest entries.
            continue
    return list(dict.fromkeys(normalized))


def _external_existing_paths(
    adapter: UserFileStorageAdapter,
    storage_prefix: str,
    candidates: list[str],
) -> list[str]:
    """Keep only fallback-manifest objects confirmed present remotely."""
    existing: list[str] = []
    for relative_path in candidates:
        storage_key = f"{storage_prefix}/{relative_path}"
        if adapter.exists(storage_key):
            existing.append(relative_path)
    return existing


def _copy_artifact_set(
    *,
    source_adapter: UserFileStorageAdapter,
    destination_adapter: UserFileStorageAdapter,
    storage_prefix: str,
    relative_paths: list[str],
    force: bool,
    retries: int,
) -> tuple[list[dict[str, Any]], int]:
    """Copy and verify every object in one database-owned artifact set.

    No owning database record is changed until this function has verified the
    complete set. Partial destination copies are intentionally retained so a
    later run can checksum and resume them without uploading again.
    """
    objects: list[dict[str, Any]] = []
    resumed_objects = 0
    for relative_path in relative_paths:
        storage_key = f"{storage_prefix}/{relative_path}"
        upload_meta, resumed = _run_with_retries(
            lambda storage_key=storage_key: _copy_storage_object(
                source_adapter=source_adapter,
                destination_adapter=destination_adapter,
                storage_key=storage_key,
                force=force,
            ),
            retries=retries,
        )
        objects.append(
            {
                "relative_path": relative_path,
                "size_bytes": int(upload_meta.get("size_bytes") or 0),
                "sha256": str(upload_meta.get("sha256") or ""),
            }
        )
        if resumed:
            resumed_objects += 1
    return objects, resumed_objects


def _delete_artifact_set_sources(
    adapter: UserFileStorageAdapter,
    *,
    storage_prefix: str,
    relative_paths: list[str],
    retries: int,
) -> tuple[int, list[dict[str, str]]]:
    """Delete an artifact set after its owner points at the destination.

    Multi-object sets remain destination-authoritative if one cleanup fails;
    reverting after some source objects were already deleted would create a
    broken source record. Failures are returned for explicit operator retry.
    """
    deleted = 0
    failures: list[dict[str, str]] = []
    for relative_path in relative_paths:
        storage_key = f"{storage_prefix}/{relative_path}"
        try:
            _delete_source_with_retries(adapter, storage_key, retries=retries)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"storage_key": storage_key, "error": str(exc)})
    return deleted, failures


def _artifact_category_stats() -> dict[str, int]:
    """Return counters shared by Deep Research and presentation migrations."""
    return {
        "scanned": 0,
        "would_migrate": 0,
        "migrated": 0,
        "failed": 0,
        "objects": 0,
        "resumed_objects": 0,
        "deleted_source_objects": 0,
        "source_cleanup_failed": 0,
    }


def _migrate_deep_research_records(
    db: Session,
    *,
    source_provider: str,
    destination_provider: str,
    source_adapter: UserFileStorageAdapter,
    destination_adapter: UserFileStorageAdapter,
    user_id: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    only_migrated_from: str | None,
    max_records: int,
    dry_run: bool,
    delete_source: bool,
    force: bool,
    retries: int,
) -> dict[str, int]:
    """Migrate complete Deep Research workspaces between providers."""
    stats = _artifact_category_stats()
    query = db.query(DeepResearchRun)
    if user_id:
        query = query.filter(DeepResearchRun.user_id == user_id)
    if created_after:
        query = query.filter(DeepResearchRun.created_at >= created_after)
    if created_before:
        query = query.filter(DeepResearchRun.created_at <= created_before)
    rows = _iter_ordered_artifact_records(
        query,
        created_column=DeepResearchRun.created_at,
        id_column=DeepResearchRun.id,
    )

    for run in rows:
        storage_meta = _deep_research_storage_metadata(run)
        # Do not race an actively changing workspace. Checkpoint manifests are
        # persisted as phases finish, so the run can be migrated safely after
        # it reaches a terminal state.
        if str(getattr(run, "status", "") or "").strip().lower() == RUN_STATUS_RUNNING:
            continue
        recorded_provider = _deep_research_storage_provider(run)
        if recorded_provider is not None and recorded_provider != source_provider:
            continue
        if only_migrated_from and not _metadata_was_migrated_from(
            storage_meta, only_migrated_from
        ):
            continue
        if max_records > 0 and stats["scanned"] >= max_records:
            break
        stats["scanned"] += 1
        if dry_run:
            stats["would_migrate"] += 1
            continue

        storage_prefix = build_deep_research_storage_prefix(run.user_id, run.id)
        try:
            if source_provider == "local":
                relative_paths = _local_artifact_paths(source_adapter, storage_prefix)
            else:
                relative_paths = _manifest_relative_paths(storage_meta)
                if not relative_paths:
                    relative_paths = _external_existing_paths(
                        source_adapter,
                        storage_prefix,
                        _known_deep_research_paths(run),
                    )
            if not relative_paths:
                raise RuntimeError(
                    "no Deep Research source artifacts were found; "
                    "the database reference was not changed"
                )
            objects, resumed_objects = _copy_artifact_set(
                source_adapter=source_adapter,
                destination_adapter=destination_adapter,
                storage_prefix=storage_prefix,
                relative_paths=relative_paths,
                force=force,
                retries=retries,
            )

            recorded_updated_at = run.updated_at
            recorded_result_meta = (
                dict(run.result_meta) if isinstance(run.result_meta, dict) else {}
            )
            destination_storage_meta = {
                **storage_meta,
                "provider": destination_provider,
                "storage_prefix": storage_prefix,
                "uploaded_files": relative_paths,
                "objects": objects,
                "migration": _migration_marker(
                    source_provider=source_provider,
                    destination_provider=destination_provider,
                ),
            }
            destination_result_meta = {
                **recorded_result_meta,
                "storage": destination_storage_meta,
            }
            updated_rows = (
                db.query(DeepResearchRun)
                .filter(
                    DeepResearchRun.id == run.id,
                    DeepResearchRun.updated_at == recorded_updated_at,
                )
                .update(
                    {DeepResearchRun.result_meta: destination_result_meta},
                    synchronize_session=False,
                )
            )
            if updated_rows != 1:
                db.rollback()
                raise RuntimeError(
                    "deep research run changed while it was being migrated; "
                    "destination copies were retained for resume"
                )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            stats["failed"] += 1
            print(
                json.dumps(
                    {
                        "artifact_type": "deep_research",
                        "run_id": run.id,
                        "user_id": run.user_id,
                        "error": str(exc),
                    }
                ),
                file=sys.stderr,
            )
            continue

        stats["migrated"] += 1
        stats["objects"] += len(relative_paths)
        stats["resumed_objects"] += resumed_objects
        if delete_source:
            deleted, failures = _delete_artifact_set_sources(
                source_adapter,
                storage_prefix=storage_prefix,
                relative_paths=relative_paths,
                retries=retries,
            )
            stats["deleted_source_objects"] += deleted
            stats["source_cleanup_failed"] += len(failures)
            for failure in failures:
                print(
                    json.dumps(
                        {
                            "artifact_type": "deep_research",
                            "run_id": run.id,
                            "user_id": run.user_id,
                            "cleanup_error": failure,
                        }
                    ),
                    file=sys.stderr,
                )
    return stats


def _presentation_candidate_paths(slide_count: int) -> list[str]:
    """Return every standard file that can belong to a presentation."""
    paths = ["metadata.json", "title.txt", "presentation.html"]
    paths.extend(
        f"images/slide_{index}.png"
        for index in range(1, max(0, int(slide_count or 0)) + 1)
    )
    return paths


def _migrate_presentation_records(
    db: Session,
    *,
    source_provider: str,
    destination_provider: str,
    source_adapter: UserFileStorageAdapter,
    destination_adapter: UserFileStorageAdapter,
    user_id: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    only_migrated_from: str | None,
    max_records: int,
    dry_run: bool,
    delete_source: bool,
    force: bool,
    retries: int,
) -> dict[str, int]:
    """Migrate complete slide-presentation artifact sets and their indexes."""
    stats = _artifact_category_stats()
    query = db.query(SlidePresentations).filter(
        SlidePresentations.storage_provider == source_provider
    )
    if user_id:
        query = query.filter(SlidePresentations.user_id == user_id)
    if created_after:
        query = query.filter(SlidePresentations.created_at >= created_after)
    if created_before:
        query = query.filter(SlidePresentations.created_at <= created_before)
    rows = _iter_ordered_artifact_records(
        query,
        created_column=SlidePresentations.created_at,
        id_column=SlidePresentations.id,
    )

    for presentation in rows:
        storage_meta = (
            dict(presentation.storage_meta)
            if isinstance(presentation.storage_meta, dict)
            else {}
        )
        if only_migrated_from and not _metadata_was_migrated_from(
            storage_meta, only_migrated_from
        ):
            continue
        if max_records > 0 and stats["scanned"] >= max_records:
            break
        stats["scanned"] += 1
        if dry_run:
            stats["would_migrate"] += 1
            continue

        storage_prefix = build_presentation_storage_prefix(
            presentation.user_id, presentation.id
        )
        try:
            if source_provider == "local":
                relative_paths = _local_artifact_paths(source_adapter, storage_prefix)
            else:
                relative_paths = _manifest_relative_paths(storage_meta)
                if not relative_paths:
                    relative_paths = _external_existing_paths(
                        source_adapter,
                        storage_prefix,
                        _presentation_candidate_paths(presentation.slide_count),
                    )
            if not relative_paths:
                raise RuntimeError(
                    "no slide presentation source artifacts were found; "
                    "the database reference was not changed"
                )
            objects, resumed_objects = _copy_artifact_set(
                source_adapter=source_adapter,
                destination_adapter=destination_adapter,
                storage_prefix=storage_prefix,
                relative_paths=relative_paths,
                force=force,
                retries=retries,
            )

            destination_storage_meta = {
                **storage_meta,
                "uploaded_files": relative_paths,
                "objects": objects,
                "migration": _migration_marker(
                    source_provider=source_provider,
                    destination_provider=destination_provider,
                ),
            }
            updated_rows = (
                db.query(SlidePresentations)
                .filter(
                    SlidePresentations.id == presentation.id,
                    SlidePresentations.storage_provider == source_provider,
                    SlidePresentations.storage_prefix == presentation.storage_prefix,
                    SlidePresentations.last_updated_at == presentation.last_updated_at,
                )
                .update(
                    {
                        SlidePresentations.storage_provider: destination_provider,
                        SlidePresentations.storage_prefix: storage_prefix,
                        SlidePresentations.storage_meta: destination_storage_meta,
                    },
                    synchronize_session=False,
                )
            )
            if updated_rows != 1:
                db.rollback()
                raise RuntimeError(
                    "slide presentation changed while it was being migrated; "
                    "destination copies were retained for resume"
                )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            stats["failed"] += 1
            print(
                json.dumps(
                    {
                        "artifact_type": "slide_presentation",
                        "presentation_id": presentation.id,
                        "user_id": presentation.user_id,
                        "error": str(exc),
                    }
                ),
                file=sys.stderr,
            )
            continue

        stats["migrated"] += 1
        stats["objects"] += len(relative_paths)
        stats["resumed_objects"] += resumed_objects
        if delete_source:
            deleted, failures = _delete_artifact_set_sources(
                source_adapter,
                storage_prefix=storage_prefix,
                relative_paths=relative_paths,
                retries=retries,
            )
            stats["deleted_source_objects"] += deleted
            stats["source_cleanup_failed"] += len(failures)
            for failure in failures:
                print(
                    json.dumps(
                        {
                            "artifact_type": "slide_presentation",
                            "presentation_id": presentation.id,
                            "user_id": presentation.user_id,
                            "cleanup_error": failure,
                        }
                    ),
                    file=sys.stderr,
                )
    return stats


def _cmd_storage_probe(_args: argparse.Namespace) -> int:
    config = get_user_file_storage_config()
    adapter = get_user_file_storage_adapter()
    result = adapter.test_connection()
    print(
        json.dumps(
            {
                "provider": config.provider,
                "local_base_path": str(config.local_base_path),
                "options_keys": sorted(config.options.keys()),
                "probe": result,
            },
            indent=2,
        )
    )
    return 0 if str(result.get("status") or "").lower() == "ok" else 1


def _validate_migration_options(args: argparse.Namespace) -> None:
    """Reject invalid limits before opening provider or database connections."""
    if int(args.batch_size) < 1:
        raise ValueError("--batch-size must be at least 1")
    if int(args.max_files) < 0:
        raise ValueError("--max-files must be 0 or greater")
    if int(args.retries) < 1:
        raise ValueError("--retries must be at least 1")
    scope = str(getattr(args, "scope", "files") or "files")
    if scope not in {"all", "files", "deep-research", "presentations"}:
        raise ValueError("--scope must be all, files, deep-research, or presentations")


def _cmd_migrate_files(args: argparse.Namespace) -> int:
    """Migrate file bytes and database references between two providers."""
    try:
        _validate_migration_options(args)
        source_provider = normalize_storage_provider(args.from_provider)
        if args.to_provider:
            destination_provider = normalize_storage_provider(args.to_provider)
        else:
            destination_provider = get_user_file_storage_config().provider
        only_migrated_from = getattr(args, "only_migrated_from", None)
        if only_migrated_from:
            only_migrated_from = normalize_storage_provider(only_migrated_from)
        scope = str(getattr(args, "scope", "files") or "files")
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if source_provider == destination_provider:
        print(
            "Source and destination storage providers must be different.",
            file=sys.stderr,
        )
        return 2

    try:
        source_adapter = get_user_file_storage_adapter_for_provider(source_provider)
        destination_adapter = get_user_file_storage_adapter_for_provider(
            destination_provider
        )
        created_after = _parse_date(args.created_after)
        created_before = _parse_date(args.created_before)
        if created_after and created_before and created_after > created_before:
            raise ValueError("--created-after must not be later than --created-before")
    except (RuntimeError, ValueError, argparse.ArgumentTypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    stats = {
        "source_provider": source_provider,
        "destination_provider": destination_provider,
        "scope": scope,
        "scanned": 0,
        "would_migrate": 0,
        "migrated": 0,
        "resumed": 0,
        "failed": 0,
        "deleted_source": 0,
        "source_cleanup_failed": 0,
        "objects": 0,
        "dry_run": bool(args.dry_run),
        "categories": {
            "files": _artifact_category_stats(),
            "deep_research": _artifact_category_stats(),
            "presentations": _artifact_category_stats(),
        },
    }

    db = SessionLocal()
    try:
        file_rows = (
            _iter_storage_records(
                db,
                source_provider=source_provider,
                user_id=args.user_id,
                created_after=created_after,
                created_before=created_before,
                batch_size=args.batch_size,
            )
            if scope in {"all", "files"}
            else ()
        )
        file_stats = stats["categories"]["files"]
        for file_row in file_rows:
            if only_migrated_from and not _record_was_migrated_from(
                file_row, only_migrated_from
            ):
                continue
            if args.max_files > 0 and stats["scanned"] >= args.max_files:
                break
            stats["scanned"] += 1
            file_stats["scanned"] += 1

            if args.dry_run:
                stats["would_migrate"] += 1
                file_stats["would_migrate"] += 1
                continue

            try:
                recorded_source_key = str(
                    getattr(file_row, "storage_key", "") or ""
                ).strip()
                recorded_source_meta = file_row.storage_meta
                recorded_last_updated_at = file_row.last_updated_at
                storage_key = _storage_key_for_record(file_row)
                legacy_local_source_path = None
                if source_provider == "local":
                    legacy_local_source_path = _legacy_local_source_path(
                        source_adapter, storage_key
                    )
                upload_meta, resumed = _run_with_retries(
                    lambda: _copy_storage_object(
                        source_adapter=source_adapter,
                        destination_adapter=destination_adapter,
                        storage_key=storage_key,
                        force=bool(args.force),
                        legacy_local_source_path=legacy_local_source_path,
                    ),
                    retries=args.retries,
                )
                legacy_source_was_used = bool(
                    upload_meta.pop("_legacy_local_source_used", False)
                )
                if not legacy_source_was_used:
                    legacy_local_source_path = None
                destination_meta = _build_destination_metadata(
                    upload_meta,
                    source_provider=source_provider,
                    destination_provider=destination_provider,
                    source_storage_key=storage_key,
                    destination_storage_key=storage_key,
                )
                _update_storage_reference(
                    db,
                    file_row=file_row,
                    source_provider=source_provider,
                    destination_provider=destination_provider,
                    destination_storage_key=storage_key,
                    destination_meta=destination_meta,
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                stats["failed"] += 1
                file_stats["failed"] += 1
                print(
                    json.dumps(
                        {
                            "file_id": file_row.id,
                            "user_id": file_row.user_id,
                            "file_name": file_row.file_name,
                            "error": str(exc),
                        }
                    ),
                    file=sys.stderr,
                )
                continue

            if args.delete_source:
                try:
                    if legacy_local_source_path is not None:
                        source_removed = _delete_legacy_local_source_with_retries(
                            legacy_local_source_path,
                            retries=args.retries,
                        )
                    else:
                        _delete_source_with_retries(
                            source_adapter,
                            storage_key,
                            retries=args.retries,
                        )
                        source_removed = True
                    if source_removed:
                        stats["deleted_source"] += 1
                        file_stats["deleted_source_objects"] += 1
                except Exception as exc:  # noqa: BLE001
                    stats["source_cleanup_failed"] += 1
                    file_stats["source_cleanup_failed"] += 1
                    source_confirmed_present = False
                    try:
                        if legacy_local_source_path is not None:
                            source_confirmed_present = (
                                legacy_local_source_path.is_file()
                            )
                        else:
                            source_confirmed_present = source_adapter.exists(
                                storage_key
                            )
                    except Exception:  # noqa: BLE001
                        # An indeterminate existence check must leave the
                        # verified destination authoritative. Reverting to a
                        # source that may have been deleted would break reads.
                        source_confirmed_present = False

                    reference_reverted = False
                    if source_confirmed_present:
                        try:
                            reference_reverted = _restore_source_reference(
                                db,
                                file_id=str(file_row.id),
                                last_updated_at=recorded_last_updated_at,
                                source_provider=source_provider,
                                source_storage_key=recorded_source_key,
                                source_storage_meta=recorded_source_meta,
                                destination_provider=destination_provider,
                                destination_storage_key=storage_key,
                            )
                        except Exception:  # noqa: BLE001
                            db.rollback()
                            reference_reverted = False

                    print(
                        json.dumps(
                            {
                                "file_id": file_row.id,
                                "user_id": file_row.user_id,
                                "source_provider": source_provider,
                                "source_storage_key": storage_key,
                                "cleanup_error": str(exc),
                                "database_reference_reverted": reference_reverted,
                            }
                        ),
                        file=sys.stderr,
                    )
                    if reference_reverted:
                        # The row still belongs to the source provider, so do
                        # not report it as migrated. The next run will resume
                        # the already-verified destination object.
                        continue

            stats["migrated"] += 1
            stats["objects"] += 1
            file_stats["migrated"] += 1
            file_stats["objects"] += 1
            if resumed:
                stats["resumed"] += 1
                file_stats["resumed_objects"] += 1

        def remaining_record_limit() -> int:
            """Return zero for unlimited or the unconsumed global record cap."""
            if args.max_files <= 0:
                return 0
            return max(0, int(args.max_files) - int(stats["scanned"]))

        def merge_artifact_stats(category_name: str, category: dict[str, int]) -> None:
            """Merge one artifact-family result into command-level counters."""
            stats["categories"][category_name] = category
            stats["scanned"] += category["scanned"]
            stats["would_migrate"] += category["would_migrate"]
            stats["migrated"] += category["migrated"]
            stats["failed"] += category["failed"]
            stats["objects"] += category["objects"]
            stats["resumed"] += category["resumed_objects"]
            stats["deleted_source"] += category["deleted_source_objects"]
            stats["source_cleanup_failed"] += category["source_cleanup_failed"]

        if scope in {"all", "deep-research"} and not (
            args.max_files > 0 and remaining_record_limit() == 0
        ):
            merge_artifact_stats(
                "deep_research",
                _migrate_deep_research_records(
                    db,
                    source_provider=source_provider,
                    destination_provider=destination_provider,
                    source_adapter=source_adapter,
                    destination_adapter=destination_adapter,
                    user_id=args.user_id,
                    created_after=created_after,
                    created_before=created_before,
                    only_migrated_from=only_migrated_from,
                    max_records=remaining_record_limit(),
                    dry_run=bool(args.dry_run),
                    delete_source=bool(args.delete_source),
                    force=bool(args.force),
                    retries=args.retries,
                ),
            )

        if scope in {"all", "presentations"} and not (
            args.max_files > 0 and remaining_record_limit() == 0
        ):
            merge_artifact_stats(
                "presentations",
                _migrate_presentation_records(
                    db,
                    source_provider=source_provider,
                    destination_provider=destination_provider,
                    source_adapter=source_adapter,
                    destination_adapter=destination_adapter,
                    user_id=args.user_id,
                    created_after=created_after,
                    created_before=created_before,
                    only_migrated_from=only_migrated_from,
                    max_records=remaining_record_limit(),
                    dry_run=bool(args.dry_run),
                    delete_source=bool(args.delete_source),
                    force=bool(args.force),
                    retries=args.retries,
                ),
            )

        print(json.dumps(stats, indent=2))
        return 0 if stats["failed"] == 0 and stats["source_cleanup_failed"] == 0 else 1
    finally:
        db.close()


def _cmd_migrate_local(args: argparse.Namespace) -> int:
    """Preserve the original local-to-configured-provider CLI command."""
    args.from_provider = "local"
    args.to_provider = None
    return _cmd_migrate_files(args)


def _add_common_migration_arguments(parser: argparse.ArgumentParser) -> None:
    """Register filters and safety controls shared by migration commands."""
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be migrated without copying files",
    )
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="Delete each source object after verification and database commit",
    )
    parser.add_argument(
        "--user-id", default=None, help="Only migrate files for a specific user"
    )
    parser.add_argument(
        "--only-migrated-from",
        default=None,
        help="Only process rows previously marked as migrated from this provider",
    )
    parser.add_argument(
        "--scope",
        choices=("all", "files", "deep-research", "presentations"),
        default="all",
        help="Artifact family to migrate (default: all)",
    )
    parser.add_argument(
        "--created-after",
        default=None,
        help="Only migrate files created on/after YYYY-MM-DD",
    )
    parser.add_argument(
        "--created-before",
        default=None,
        help="Only migrate files created on/before YYYY-MM-DD",
    )
    parser.add_argument(
        "--batch-size", type=int, default=200, help="Database fetch batch size"
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Maximum owning records to process across the selected scope (0 = no cap)",
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="Copy and cleanup retries per file"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a destination object even when it already exists",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Omlorix user file storage CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    probe_cmd = sub.add_parser(
        "storage-probe", help="Probe configured file storage provider"
    )
    probe_cmd.set_defaults(func=_cmd_storage_probe)

    migrate_files_cmd = sub.add_parser(
        "migrate-files",
        help="Migrate user files and generated artifacts between storage providers",
    )
    migrate_files_cmd.add_argument(
        "--from-provider",
        required=True,
        help="Source provider: local, s3, gcs, azure, or webdav",
    )
    migrate_files_cmd.add_argument(
        "--to-provider",
        default=None,
        help="Destination provider; defaults to FILE_STORAGE_PROVIDER",
    )
    _add_common_migration_arguments(migrate_files_cmd)
    migrate_files_cmd.set_defaults(func=_cmd_migrate_files)

    migrate_local_cmd = sub.add_parser(
        "migrate-local-files",
        help="Migrate local files to FILE_STORAGE_PROVIDER (compatibility command)",
    )
    _add_common_migration_arguments(migrate_local_cmd)
    migrate_local_cmd.set_defaults(func=_cmd_migrate_local)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
