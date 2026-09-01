from __future__ import annotations

import json
import logging
import os
import hashlib
import mimetypes
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
import uuid

from app.files.models import get_file
from app.files.storage import (
    delete_file_from_storage,
    download_file_from_storage,
    get_user_file_storage_config,
    get_user_file_storage_adapter,
)
from app.files.utils import (
    BASE_STORAGE_DIR,
    MATERIALIZED_TEMP_DIR,
    materialize_file_record,
)
from app.tools.deep_research.models import (
    DeepResearchArtifact,
    DeepResearchRun,
    get_deep_research_run,
    utc_now,
)


DEEP_RESEARCH_METADATA_FILE = "session.json"
MAX_RESEARCH_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_RESEARCH_ARTIFACT_TOTAL_BYTES = 200 * 1024 * 1024
MAX_RESEARCH_ARTIFACT_COUNT = 50
MAX_RESEARCH_IMAGE_PIXELS = 40_000_000

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_EMBEDDABLE_IMAGE_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    """Convert Pydantic and collection values into JSON-safe structures."""

    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _safe_filename(value: str, fallback: str = "artifact.bin") -> str:
    """Return a short basename that is safe inside a research workspace."""

    basename = Path(str(value or "")).name.strip()
    cleaned = _SAFE_FILENAME_RE.sub("-", basename).strip(".-")
    if not cleaned:
        cleaned = fallback
    stem = Path(cleaned).stem[:100].strip(".-") or "artifact"
    suffix = Path(cleaned).suffix[:20].lower()
    return f"{stem}{suffix}"


def _sha256_file(path: Path) -> str:
    """Hash a file without loading a potentially large artifact into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_kind(media_type: str) -> str:
    """Classify an artifact for report rendering and UI presentation."""

    if media_type in _EMBEDDABLE_IMAGE_TYPES:
        return "image"
    if media_type.startswith("text/") or media_type in {
        "application/json",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        return "document"
    return "other"


def _validated_raster_media_type(path: Path) -> str:
    """Verify a report image's bytes and dimensions, then return its real MIME."""

    from PIL import Image, UnidentifiedImageError

    format_types = {
        "AVIF": "image/avif",
        "GIF": "image/gif",
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }
    try:
        with Image.open(path) as probe:
            width, height = probe.size
            image_format = str(probe.format or "").upper()
            if (
                width <= 0
                or height <= 0
                or width * height > MAX_RESEARCH_IMAGE_PIXELS
                or image_format not in format_types
            ):
                raise ValueError(
                    "Generated image dimensions or format are not allowed."
                )
            probe.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Generated image artifact is corrupt.") from exc
    return format_types[image_format]


def build_deep_research_storage_prefix(user_id: str, session_id: str) -> str:
    safe_user_id = str(user_id or "").strip().strip("/\\")
    safe_session_id = str(session_id or "").strip().strip("/\\")
    if not safe_user_id or not safe_session_id:
        raise ValueError("user_id and session_id are required")
    if "/" in safe_user_id or "\\" in safe_user_id or ".." in safe_user_id:
        raise ValueError("user_id contains invalid path characters")
    if "/" in safe_session_id or "\\" in safe_session_id or ".." in safe_session_id:
        raise ValueError("session_id contains invalid path characters")
    return f"{safe_user_id}/deep_research/{safe_session_id}"


def build_deep_research_storage_key(
    user_id: str, session_id: str, relative_path: str
) -> str:
    raw_relative = str(relative_path or "").strip()
    if not raw_relative:
        raise ValueError("relative_path is required")
    if "\\" in raw_relative:
        raise ValueError("relative_path contains invalid path separators")
    normalized_relative = PurePosixPath(raw_relative)
    if normalized_relative.is_absolute():
        raise ValueError("relative_path must be relative")
    if any(part == ".." for part in normalized_relative.parts):
        raise ValueError("relative_path contains invalid traversal")
    relative = normalized_relative.as_posix().lstrip("/")
    if not relative or relative == ".":
        raise ValueError("relative_path is required")
    return f"{build_deep_research_storage_prefix(user_id, session_id)}/{relative}"


def get_deep_research_storage_provider() -> str:
    return (
        str(get_user_file_storage_config().provider or "local").strip().lower()
        or "local"
    )


def get_deep_research_run_storage_provider(run: DeepResearchRun) -> str:
    """Resolve the provider persisted by a completed run.

    Legacy completed runs predate storage metadata and therefore belong to
    local storage. Interrupted or active runs without a final manifest still
    follow the currently configured provider used by their checkpoint uploads.
    """
    result_meta = run.result_meta if isinstance(run.result_meta, dict) else {}
    storage = result_meta.get("storage")
    if isinstance(storage, dict) and storage.get("provider"):
        return str(storage["provider"]).strip().lower() or "local"
    if str(getattr(run, "status", "") or "").strip().lower() == "completed":
        return "local"
    return get_deep_research_storage_provider()


def _normalize_storage_provider_for_materialized_path(value: Any) -> str:
    """Return a provider name safe to use beneath the materialization root."""
    provider = str(value or "local").strip().lower() or "local"
    if "/" in provider or "\\" in provider or ".." in provider:
        raise ValueError("storage_provider contains invalid path characters")
    return provider


def get_deep_research_workspace_dir(user_id: str, session_id: str) -> Path:
    provider = get_deep_research_storage_provider()
    if provider == "local":
        target = BASE_STORAGE_DIR / build_deep_research_storage_prefix(
            user_id, session_id
        )
    else:
        target = (
            MATERIALIZED_TEMP_DIR
            / "deep_research_workspaces"
            / build_deep_research_storage_prefix(user_id, session_id)
        )
    target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_safe_workspace_path(workspace_dir: str | Path, relative_path: str) -> Path:
    workspace = Path(workspace_dir).resolve()
    normalized = PurePosixPath(str(relative_path or "").strip())
    if not str(normalized) or str(normalized) == ".":
        return workspace
    if normalized.is_absolute() or any(part == ".." for part in normalized.parts):
        raise ValueError("Invalid workspace path")
    resolved = (workspace / Path(normalized.as_posix())).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError:
        raise ValueError("Workspace path escapes session directory")
    return resolved


def write_session_metadata(workspace_dir: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(workspace_dir) / DEEP_RESEARCH_METADATA_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def write_workspace_text(
    workspace_dir: str | Path,
    relative_path: str,
    content: str,
) -> Path:
    """Write UTF-8 run output beneath a traversal-safe workspace path."""

    target = ensure_safe_workspace_path(workspace_dir, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(content or ""), encoding="utf-8")
    return target


def write_workspace_bytes(
    workspace_dir: str | Path,
    relative_path: str,
    content: bytes,
) -> Path:
    """Write bounded binary content below a traversal-safe run workspace."""

    payload = bytes(content)
    if len(payload) > MAX_RESEARCH_ARTIFACT_BYTES:
        raise ValueError("Deep Research artifact exceeds the workspace size limit.")
    target = ensure_safe_workspace_path(workspace_dir, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def write_workspace_json(
    workspace_dir: str | Path,
    relative_path: str,
    value: Any,
) -> Path:
    """Write stable, human-readable JSON beneath the run workspace."""

    return write_workspace_text(
        workspace_dir,
        relative_path,
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, default=str),
    )


def list_run_artifacts(db, run_id: str) -> list[DeepResearchArtifact]:
    """Return typed artifact metadata from the consolidated run document."""

    run = get_deep_research_run(db, str(run_id))
    if run is None or not isinstance(run.artifacts, list):
        return []
    artifacts = [
        DeepResearchArtifact.from_dict(item)
        for item in run.artifacts
        if isinstance(item, dict)
    ]
    return sorted(
        artifacts,
        key=lambda item: (item.created_at or "", item.stable_id),
    )


def save_run_artifacts(
    db,
    run: DeepResearchRun,
    artifacts: list[DeepResearchArtifact],
    *,
    commit: bool = True,
) -> None:
    """Replace a run's bounded artifact metadata with one JSON assignment."""

    run.artifacts = [
        artifact.to_dict()
        if isinstance(artifact, DeepResearchArtifact)
        else _jsonable(vars(artifact))
        for artifact in artifacts
    ]
    run.updated_at = utc_now()
    # The run is normally already attached to the active SQLAlchemy session.
    # Explicitly adding it also supports detached runs in import/test helpers.
    if hasattr(db, "add"):
        db.add(run)
    if commit:
        db.commit()


def artifact_manifest(db, run_id: str) -> list[dict[str, Any]]:
    """Build the secret-free artifact manifest supplied to audit phases."""

    return [
        {
            "stable_id": artifact.stable_id,
            "file_id": artifact.file_id,
            "original_filename": artifact.original_filename,
            "relative_path": artifact.relative_path,
            "media_type": artifact.media_type,
            "kind": artifact.kind,
            "source_phase": artifact.source_phase,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "caption": artifact.caption,
            "alt_text": artifact.alt_text,
            "source_url": artifact.source_url,
            "attribution": artifact.attribution,
            "license_name": artifact.license_name,
            "validation_status": artifact.validation_status,
        }
        for artifact in list_run_artifacts(db, run_id)
    ]


def persist_generated_files(
    db,
    *,
    run_id: str,
    user_id: str,
    phase: str,
    generated_files: list[dict[str, str]],
    workspace_dir: str | Path,
) -> list[DeepResearchArtifact]:
    """Copy normal Code Execution outputs into the durable research workspace.

    Code Execution remains the owner of sandbox execution and user-file creation.
    This function consumes only its public ``f`` stream events, verifies ownership,
    enforces an artifact size bound, and stores immutable report-local copies.
    """

    workspace = Path(workspace_dir)
    artifacts_dir = ensure_safe_workspace_path(workspace, "artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    saved: list[DeepResearchArtifact] = []
    run = get_deep_research_run(db, str(run_id))
    if run is None:
        raise ValueError("Deep Research run not found.")
    existing_artifacts = list_run_artifacts(db, run_id)
    known_digests = {
        artifact.sha256: artifact for artifact in existing_artifacts if artifact.sha256
    }
    total_bytes = sum(max(0, int(item.size_bytes or 0)) for item in existing_artifacts)

    for generated in generated_files:
        file_id = str(generated.get("file_id") or "").strip()
        if not file_id:
            continue
        file_record = get_file(db, file_id, str(user_id))
        if file_record is None:
            logger.warning(
                "Ignoring a Deep Research artifact that is not owned by the run user",
                extra={"run_id": run_id, "file_id": file_id},
            )
            continue

        source_path = materialize_file_record(file_record, str(user_id))
        size_bytes = source_path.stat().st_size
        if size_bytes <= 0 or size_bytes > MAX_RESEARCH_ARTIFACT_BYTES:
            logger.warning(
                "Ignoring an invalid-sized Deep Research artifact",
                extra={"run_id": run_id, "file_id": file_id, "size_bytes": size_bytes},
            )
            continue

        digest = _sha256_file(source_path)
        existing = known_digests.get(digest)
        if existing is not None:
            saved.append(existing)
            continue

        if (
            len(existing_artifacts) >= MAX_RESEARCH_ARTIFACT_COUNT
            or total_bytes + size_bytes > MAX_RESEARCH_ARTIFACT_TOTAL_BYTES
        ):
            logger.warning(
                "Ignoring a Deep Research artifact because the run budget is exhausted",
                extra={"run_id": run_id, "file_id": file_id},
            )
            continue

        ordinal = len(existing_artifacts) + 1
        stable_phase = _SAFE_FILENAME_RE.sub("-", str(phase or "phase").lower()).strip(
            "-"
        )
        stable_id = f"{stable_phase or 'phase'}-artifact-{ordinal:02d}"
        original_name = (
            str(generated.get("name") or "").strip()
            or str(getattr(file_record, "file_name", "") or "").strip()
            or source_path.name
        )
        safe_name = _safe_filename(original_name)
        destination = ensure_safe_workspace_path(
            workspace,
            f"artifacts/{stable_id}-{safe_name}",
        )

        declared_type = str(getattr(file_record, "file_type", "") or "").strip().lower()
        guessed_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        media_type = declared_type or guessed_type
        if media_type in _EMBEDDABLE_IMAGE_TYPES:
            try:
                media_type = _validated_raster_media_type(source_path)
            except ValueError:
                logger.warning(
                    "Ignoring an invalid generated image artifact",
                    extra={"run_id": run_id, "file_id": file_id},
                )
                continue
        shutil.copyfile(source_path, destination)
        file_meta = (
            dict(file_record.meta)
            if isinstance(getattr(file_record, "meta", None), dict)
            else {}
        )
        # SVG and HTML are retained only as downloads because both can contain
        # active content and must never be embedded in the standalone report.
        kind = _artifact_kind(media_type)
        now = utc_now().isoformat()
        artifact = DeepResearchArtifact(
            stable_id=stable_id,
            file_id=file_id,
            source_phase=str(phase),
            original_filename=original_name,
            relative_path=destination.relative_to(workspace).as_posix(),
            media_type=media_type,
            kind=kind,
            size_bytes=size_bytes,
            sha256=digest,
            caption=str(file_meta.get("caption") or "").strip() or None,
            alt_text=str(file_meta.get("alt_text") or "").strip() or None,
            source_url=str(file_meta.get("source_url") or "").strip() or None,
            attribution=str(file_meta.get("attribution") or "").strip() or None,
            license_name=str(file_meta.get("license_name") or "").strip() or None,
            validation_status="validated",
            meta={
                "origin": (
                    "web_image"
                    if file_meta.get("deep_research_web_image")
                    else "code_execution"
                ),
                "remote_image_url": file_meta.get("remote_image_url"),
            },
            created_at=now,
            updated_at=now,
        )
        existing_artifacts.append(artifact)
        known_digests[digest] = artifact
        total_bytes += size_bytes
        saved.append(artifact)

    if saved:
        save_run_artifacts(db, run, existing_artifacts)
    return saved


def create_workspace_archive(
    workspace_dir: str | Path, archive_name: str = "workspace.zip"
) -> Path:
    workspace = Path(workspace_dir)
    archive_path = workspace / archive_name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            if path == archive_path:
                continue
            handle.write(path, path.relative_to(workspace))
    return archive_path


def list_workspace_files(workspace_dir: str | Path) -> list[str]:
    workspace = Path(workspace_dir)
    result: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        result.append(path.relative_to(workspace).as_posix())
    return result


def deep_research_run_cleanup_descriptor(run: DeepResearchRun) -> dict[str, Any]:
    """Capture everything needed to delete a run's storage after DB commit.

    The descriptor is deliberately detached from the SQLAlchemy row because a
    successful hard delete expires or removes that row before object-storage
    cleanup begins. Persisted upload manifests provide the authoritative file
    list; checkpoint and artifact metadata cover interrupted older runs.
    """

    result_meta = run.result_meta if isinstance(run.result_meta, dict) else {}
    storage_meta = (
        result_meta.get("storage")
        if isinstance(result_meta.get("storage"), dict)
        else {}
    )
    relative_paths: set[str] = {
        "artifacts.json",
        "citations.json",
        "manifest.json",
        "session.json",
        "workspace.zip",
    }
    for value in (
        run.final_report_path,
        run.final_html_path,
        run.manifest_path,
        result_meta.get("archive_path"),
    ):
        if value:
            relative_paths.add(str(value))
    for value in storage_meta.get("uploaded_files") or []:
        if value:
            relative_paths.add(str(value))
    checkpoints = result_meta.get("checkpoints")
    if isinstance(checkpoints, dict):
        for checkpoint in checkpoints.values():
            if not isinstance(checkpoint, dict):
                continue
            for value in checkpoint.get("files") or []:
                if value:
                    relative_paths.add(str(value))
    for artifact in run.artifacts or []:
        if isinstance(artifact, dict) and artifact.get("relative_path"):
            relative_paths.add(str(artifact["relative_path"]))

    return {
        "user_id": str(run.user_id),
        "run_id": str(run.id),
        "storage_provider": get_deep_research_run_storage_provider(run),
        "relative_paths": sorted(relative_paths),
    }


def delete_deep_research_run_artifacts(
    *,
    user_id: str,
    run_id: str,
    storage_provider: str,
    relative_paths: list[str],
) -> None:
    """Delete one hard-deleted run's local, materialized, and cloud artifacts."""

    prefix = build_deep_research_storage_prefix(user_id, run_id)
    provider = _normalize_storage_provider_for_materialized_path(storage_provider)

    # Cloud adapters expose object deletion rather than recursive prefix
    # deletion, so remove the bounded manifest captured before the DB commit.
    if provider != "local":
        for relative_path in dict.fromkeys(relative_paths):
            try:
                storage_key = build_deep_research_storage_key(
                    user_id,
                    run_id,
                    relative_path,
                )
                delete_file_from_storage(provider, storage_key)
            except Exception:
                logger.exception(
                    "Failed to delete Deep Research object-storage artifact",
                    extra={"run_id": run_id, "relative_path": relative_path},
                )

    # Local storage keeps the complete workspace under one validated prefix.
    # Cloud mode also has workspace and download caches that may survive the
    # request which created them; remove both cache shapes for every provider.
    shutil.rmtree(BASE_STORAGE_DIR / prefix, ignore_errors=True)
    shutil.rmtree(
        MATERIALIZED_TEMP_DIR / "deep_research_workspaces" / prefix,
        ignore_errors=True,
    )
    shutil.rmtree(
        MATERIALIZED_TEMP_DIR / "deep_research_artifacts" / provider / prefix,
        ignore_errors=True,
    )


def upload_deep_research_artifacts(
    *,
    workspace_dir: str | Path,
    user_id: str,
    session_id: str,
    relative_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Persist and verify a complete or checkpoint-scoped workspace manifest.

    External storage failures are deliberately not swallowed: callers must
    never mark a run or checkpoint durable when one of its objects is missing
    or corrupt. The returned portable manifest also makes later provider
    migration and independent integrity audits deterministic.
    """
    workspace = Path(workspace_dir)
    provider = get_deep_research_storage_provider()
    paths = list(
        dict.fromkeys(relative_paths or list_workspace_files(workspace))
    )
    objects: list[dict[str, Any]] = []

    if provider == "local":
        uploaded: list[str] = []
        for relative_path in paths:
            local_path = ensure_safe_workspace_path(workspace, relative_path)
            if not local_path.exists() or not local_path.is_file():
                continue
            uploaded.append(relative_path)
            objects.append(
                {
                    "relative_path": relative_path,
                    "size_bytes": local_path.stat().st_size,
                    "sha256": _sha256_file(local_path),
                }
            )
        return {
            "provider": provider,
            "storage_prefix": build_deep_research_storage_prefix(user_id, session_id),
            "uploaded_files": uploaded,
            "objects": objects,
        }

    adapter = get_user_file_storage_adapter()
    uploaded: list[str] = []
    for relative_path in paths:
        local_path = ensure_safe_workspace_path(workspace, relative_path)
        if not local_path.exists() or not local_path.is_file():
            continue
        storage_key = build_deep_research_storage_key(
            user_id, session_id, relative_path
        )
        source_size = local_path.stat().st_size
        source_hash = _sha256_file(local_path)
        adapter.upload_file(local_path, storage_key)
        if not adapter.exists(storage_key):
            raise RuntimeError(
                f"Deep Research upload is missing after write: {relative_path}"
            )
        with tempfile.TemporaryDirectory(
            prefix="omlorix-deep-research-verify-"
        ) as temp_dir:
            verified_path = Path(temp_dir) / "artifact"
            adapter.download_file(storage_key, verified_path)
            if (
                verified_path.stat().st_size != source_size
                or _sha256_file(verified_path) != source_hash
            ):
                raise RuntimeError(
                    f"Deep Research upload checksum mismatch: {relative_path}"
                )
        uploaded.append(relative_path)
        objects.append(
            {
                "relative_path": relative_path,
                "size_bytes": source_size,
                "sha256": source_hash,
            }
        )
    return {
        "provider": provider,
        "storage_prefix": build_deep_research_storage_prefix(user_id, session_id),
        "uploaded_files": uploaded,
        "objects": objects,
    }


def materialize_deep_research_artifact(
    user_id: str,
    session_id: str,
    relative_path: str,
    *,
    storage_provider: str | None = None,
) -> Path:
    """Materialize one artifact from its persisted or configured provider."""
    storage_key = build_deep_research_storage_key(user_id, session_id, relative_path)
    provider = _normalize_storage_provider_for_materialized_path(
        storage_provider or get_deep_research_storage_provider()
    )
    if provider == "local":
        local_path = BASE_STORAGE_DIR / storage_key
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(
                f"Deep research artifact not found: {relative_path}"
            )
        return local_path

    target = MATERIALIZED_TEMP_DIR / "deep_research_artifacts" / provider / storage_key
    if target.exists() and target.is_file() and target.stat().st_size > 0:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_suffix(f"{target.suffix}.{uuid.uuid4().hex}.partial")
    try:
        download_file_from_storage(provider, storage_key, tmp_target)
        if not tmp_target.exists() or tmp_target.stat().st_size <= 0:
            raise FileNotFoundError(
                f"Deep research artifact missing in storage: {relative_path}"
            )
        os.replace(tmp_target, target)
    finally:
        tmp_target.unlink(missing_ok=True)
    return target


def load_deep_research_session_metadata(
    user_id: str,
    session_id: str,
    *,
    storage_provider: str | None = None,
) -> dict[str, Any]:
    """Load session metadata from the provider owning the run."""
    metadata_path = materialize_deep_research_artifact(
        user_id,
        session_id,
        DEEP_RESEARCH_METADATA_FILE,
        storage_provider=storage_provider,
    )
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
