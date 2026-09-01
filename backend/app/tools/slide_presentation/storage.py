from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any
import uuid

from app.files.storage import (
    delete_file_from_storage,
    download_file_from_storage,
    get_user_file_storage_adapter,
    get_user_file_storage_adapter_for_provider,
    get_user_file_storage_config,
)
from app.files.utils import BASE_STORAGE_DIR, MATERIALIZED_TEMP_DIR

logger = logging.getLogger(__name__)


def build_presentation_storage_prefix(user_id: str, presentation_id: str) -> str:
    safe_user_id = str(user_id or "").strip().strip("/\\")
    safe_presentation_id = str(presentation_id or "").strip().strip("/\\")
    if not safe_user_id or not safe_presentation_id:
        raise ValueError("user_id and presentation_id are required")

    if "/" in safe_user_id or "\\" in safe_user_id or ".." in safe_user_id:
        raise ValueError("user_id contains invalid path characters")
    if (
        "/" in safe_presentation_id
        or "\\" in safe_presentation_id
        or ".." in safe_presentation_id
    ):
        raise ValueError("presentation_id contains invalid path characters")

    return f"{safe_user_id}/presentations/{safe_presentation_id}"


def build_presentation_storage_key(
    user_id: str, presentation_id: str, relative_path: str
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

    return f"{build_presentation_storage_prefix(user_id, presentation_id)}/{relative}"


def get_presentation_storage_provider() -> str:
    return (
        str(get_user_file_storage_config().provider or "local").strip().lower()
        or "local"
    )


def _upload_single_artifact(
    local_path: Path,
    *,
    user_id: str,
    presentation_id: str,
    relative_path: str,
    storage_prefix: str | None = None,
) -> dict[str, Any]:
    """Upload one presentation file and return portable manifest metadata."""
    if not local_path.exists() or not local_path.is_file():
        raise FileNotFoundError(f"Presentation artifact not found: {local_path}")
    prefix = _normalize_presentation_storage_prefix(
        storage_prefix or build_presentation_storage_prefix(user_id, presentation_id)
    )
    storage_key = f"{prefix}/{relative_path}"
    digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
    adapter = get_user_file_storage_adapter()
    upload_meta = dict(adapter.upload_file(local_path, storage_key) or {})
    return {
        "relative_path": relative_path,
        "size_bytes": int(upload_meta.get("size_bytes") or local_path.stat().st_size),
        "sha256": digest,
    }


def _cleanup_cloud_local_artifacts(presentation_dir: Path, slide_count: int) -> None:
    for candidate in _presentation_artifact_paths(presentation_dir, slide_count):
        try:
            candidate.unlink(missing_ok=True)
        except Exception:
            logger.debug(
                "Failed to remove local presentation artifact: %s",
                candidate,
                exc_info=True,
            )

    images_dir = presentation_dir / "images"
    try:
        if images_dir.exists() and not any(images_dir.iterdir()):
            images_dir.rmdir()
    except Exception:
        logger.debug(
            "Failed to remove presentation images directory: %s",
            images_dir,
            exc_info=True,
        )

    try:
        if presentation_dir.exists() and not any(presentation_dir.iterdir()):
            presentation_dir.rmdir()
    except Exception:
        logger.debug(
            "Failed to remove presentation directory: %s",
            presentation_dir,
            exc_info=True,
        )


def _presentation_artifact_relative_paths(slide_count: int) -> list[str]:
    relative_paths = ["metadata.json", "title.txt", "presentation.html"]
    for index in range(1, max(0, int(slide_count)) + 1):
        relative_paths.append(f"images/slide_{index}.png")
    return relative_paths


def _presentation_artifact_paths(
    presentation_dir: Path, slide_count: int
) -> list[Path]:
    return [
        presentation_dir / relative_path
        for relative_path in _presentation_artifact_relative_paths(slide_count)
    ]


def _safe_child_path(base_dir: Path, relative_path: str) -> Path:
    target = (Path(base_dir) / relative_path).resolve()
    base = Path(base_dir).resolve()
    if base not in target.parents and target != base:
        raise ValueError("storage_prefix contains invalid traversal")
    return target


def _normalize_presentation_storage_prefix(storage_prefix: str) -> str:
    raw_prefix = str(storage_prefix or "").strip().strip("/\\")
    if not raw_prefix:
        return ""
    if "\\" in raw_prefix:
        raise ValueError("storage_prefix contains invalid path separators")

    normalized = PurePosixPath(raw_prefix)
    if normalized.is_absolute():
        raise ValueError("storage_prefix must be relative")
    if any(part == ".." for part in normalized.parts):
        raise ValueError("storage_prefix contains invalid traversal")

    prefix = normalized.as_posix().lstrip("/")
    if not prefix or prefix == ".":
        return ""
    return prefix


def _normalize_storage_provider_for_materialized_path(storage_provider: str) -> str:
    provider = str(storage_provider or "local").strip().lower() or "local"
    if "/" in provider or "\\" in provider or ".." in provider:
        raise ValueError("storage_provider contains invalid path characters")
    return provider


def delete_slide_presentation_artifacts(
    *, storage_provider: str, storage_prefix: str, slide_count: int
) -> None:
    normalized_provider = _normalize_storage_provider_for_materialized_path(
        storage_provider
    )
    normalized_prefix = _normalize_presentation_storage_prefix(storage_prefix)
    if not normalized_prefix:
        return

    for relative_path in _presentation_artifact_relative_paths(slide_count):
        storage_key = f"{normalized_prefix}/{relative_path}"
        if normalized_provider == "local":
            try:
                _safe_child_path(BASE_STORAGE_DIR, storage_key).unlink(missing_ok=True)
            except Exception:
                logger.debug(
                    "Failed to remove local presentation artifact: %s",
                    storage_key,
                    exc_info=True,
                )
            continue
        try:
            delete_file_from_storage(normalized_provider, storage_key)
        except Exception:
            logger.debug(
                "Failed to remove remote presentation artifact: %s",
                storage_key,
                exc_info=True,
            )

    shutil.rmtree(
        _safe_child_path(BASE_STORAGE_DIR, normalized_prefix), ignore_errors=True
    )
    materialized_base = MATERIALIZED_TEMP_DIR / "presentations" / normalized_provider
    shutil.rmtree(
        _safe_child_path(materialized_base, normalized_prefix), ignore_errors=True
    )


def upload_presentation_artifacts(
    *,
    presentation_dir: str | Path,
    user_id: str,
    presentation_id: str,
    slide_count: int,
    revision: int | None = None,
    cleanup_local_for_cloud: bool = True,
) -> dict[str, Any]:
    """Upload a complete deck and return its provider plus object manifest."""
    pres_dir = Path(presentation_dir)
    provider = get_presentation_storage_provider()
    objects: list[dict[str, Any]] = []
    base_prefix = build_presentation_storage_prefix(user_id, presentation_id)
    # Publish every complete render under an immutable prefix. The database row
    # is the single pointer swap for both local and remote providers.
    revision_label = max(0, int(revision or 0))
    storage_prefix = (
        f"{base_prefix}/revisions/r{revision_label}-{uuid.uuid4().hex}"
    )

    required_paths = [
        pres_dir / "metadata.json",
        pres_dir / "title.txt",
        pres_dir / "presentation.html",
        *[
            pres_dir / "images" / f"slide_{index}.png"
            for index in range(1, max(0, int(slide_count)) + 1)
        ],
    ]
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"Presentation artifact bundle is incomplete: {missing[0]}"
        )

    def upload_one(local_path: Path, relative_path: str) -> dict[str, Any]:
        """Upload one object and roll back the revision prefix on failure."""
        try:
            return _upload_single_artifact(
                local_path,
                user_id=user_id,
                presentation_id=presentation_id,
                relative_path=relative_path,
                storage_prefix=storage_prefix,
            )
        except Exception:
            for uploaded in objects:
                key = f"{storage_prefix}/{uploaded['relative_path']}"
                try:
                    delete_file_from_storage(provider, key)
                except Exception:
                    logger.debug(
                        "Could not roll back partial presentation upload: %s",
                        key,
                        exc_info=True,
                    )
            shutil.rmtree(
                _safe_child_path(BASE_STORAGE_DIR, storage_prefix),
                ignore_errors=True,
            )
            raise

    objects.append(
        upload_one(pres_dir / "metadata.json", "metadata.json")
    )
    objects.append(
        upload_one(pres_dir / "title.txt", "title.txt")
    )
    presentation_html_path = pres_dir / "presentation.html"
    if presentation_html_path.exists() and presentation_html_path.is_file():
        objects.append(
            upload_one(presentation_html_path, "presentation.html")
        )

    uploaded_slides = 0
    for index in range(1, max(0, int(slide_count)) + 1):
        objects.append(
            upload_one(
                pres_dir / "images" / f"slide_{index}.png",
                f"images/slide_{index}.png",
            )
        )
        uploaded_slides += 1

    if provider != "local" and cleanup_local_for_cloud:
        _cleanup_cloud_local_artifacts(pres_dir, slide_count)

    return {
        "provider": provider,
        "storage_prefix": storage_prefix,
        "uploaded_slides": uploaded_slides,
        "uploaded_files": [item["relative_path"] for item in objects],
        "objects": objects,
    }


def materialize_presentation_artifact(
    user_id: str,
    presentation_id: str,
    relative_path: str,
    *,
    storage_provider: str | None = None,
    storage_prefix: str | None = None,
) -> Path:
    """Materialize one presentation artifact from its owning provider."""
    prefix = _normalize_presentation_storage_prefix(
        storage_prefix or build_presentation_storage_prefix(user_id, presentation_id)
    )
    normalized_relative = PurePosixPath(str(relative_path or "").strip())
    if (
        not str(relative_path or "").strip()
        or normalized_relative.is_absolute()
        or any(part == ".." for part in normalized_relative.parts)
    ):
        raise ValueError("relative_path contains invalid traversal")
    storage_key = f"{prefix}/{normalized_relative.as_posix()}"
    provider = _normalize_storage_provider_for_materialized_path(
        storage_provider or get_presentation_storage_provider()
    )

    if provider == "local":
        local_path = BASE_STORAGE_DIR / storage_key
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"Presentation artifact not found: {relative_path}")
        return local_path

    target = MATERIALIZED_TEMP_DIR / "presentations" / provider / storage_key
    if target.exists() and target.is_file() and target.stat().st_size > 0:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_suffix(f"{target.suffix}.{uuid.uuid4().hex}.partial")

    try:
        download_file_from_storage(provider, storage_key, tmp_target)
        if not tmp_target.exists() or tmp_target.stat().st_size <= 0:
            raise FileNotFoundError(
                f"Presentation artifact missing in storage: {relative_path}"
            )
        os.replace(tmp_target, target)
    finally:
        tmp_target.unlink(missing_ok=True)

    return target


def download_slide_to_temp(
    user_id: str,
    presentation_id: str,
    slide_number: int,
    *,
    storage_provider: str | None = None,
    storage_prefix: str | None = None,
) -> Path:
    if int(slide_number) <= 0:
        raise ValueError("slide_number must be positive")
    kwargs = {"storage_provider": storage_provider} if storage_provider else {}
    if storage_prefix:
        kwargs["storage_prefix"] = storage_prefix
    return materialize_presentation_artifact(
        user_id,
        presentation_id,
        f"images/slide_{int(slide_number)}.png",
        **kwargs,
    )


def load_presentation_metadata(
    user_id: str,
    presentation_id: str,
    *,
    storage_provider: str | None = None,
    storage_prefix: str | None = None,
) -> dict[str, Any]:
    try:
        metadata_path = materialize_presentation_artifact(
            user_id,
            presentation_id,
            "metadata.json",
            storage_provider=storage_provider,
            storage_prefix=storage_prefix,
        )
    except FileNotFoundError:
        return {}

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    user_prompt = payload.get("user_prompt")
    edit_request = payload.get("edit_request")
    if user_prompt in (None, "") and edit_request not in (None, ""):
        payload["user_prompt"] = edit_request
    elif (
        user_prompt not in (None, "")
        and edit_request not in (None, "")
        and str(user_prompt) != str(edit_request)
    ):
        logger.warning(
            "Presentation metadata contains mismatched user_prompt and edit_request for %s/%s",
            user_id,
            presentation_id,
        )

    if "number_of_slides" in payload:
        payload.pop("number_of_slides", None)

    return payload


def load_presentation_title(
    user_id: str,
    presentation_id: str,
    *,
    storage_provider: str | None = None,
    storage_prefix: str | None = None,
) -> str:
    try:
        title_path = materialize_presentation_artifact(
            user_id,
            presentation_id,
            "title.txt",
            storage_provider=storage_provider,
            storage_prefix=storage_prefix,
        )
        title = title_path.read_text(encoding="utf-8").strip()
        if title:
            return title
    except Exception:
        pass

    metadata = load_presentation_metadata(
        user_id,
        presentation_id,
        storage_provider=storage_provider,
        storage_prefix=storage_prefix,
    )
    return str(metadata.get("title") or "").strip()


def load_presentation_html(
    user_id: str,
    presentation_id: str,
    *,
    storage_provider: str | None = None,
    storage_prefix: str | None = None,
) -> str:
    provider = (
        str(storage_provider or get_presentation_storage_provider()).strip().lower()
        or "local"
    )

    try:
        html_path = materialize_presentation_artifact(
            user_id,
            presentation_id,
            "presentation.html",
            storage_provider=provider,
            storage_prefix=storage_prefix,
        )
    except Exception:
        if provider != "local":
            local_path = BASE_STORAGE_DIR / build_presentation_storage_key(
                user_id, presentation_id, "presentation.html"
            )
            if local_path.exists() and local_path.is_file():
                try:
                    return local_path.read_text(encoding="utf-8")
                except Exception:
                    return ""
        return ""

    try:
        return html_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def save_presentation_text_artifacts(
    user_id: str,
    presentation_id: str,
    *,
    html: str,
    title: str,
    metadata: dict[str, Any],
    storage_provider: str | None = None,
) -> None:
    """Persist editor-owned text artifacts without re-uploading slide images.

    Source autosaves are intentionally much cheaper than a full render.  The
    HTML, title, and metadata are kept coherent here; a later render replaces
    the PPTX and PNG derivatives for the same stable presentation identity.
    """

    provider = _normalize_storage_provider_for_materialized_path(
        storage_provider or get_presentation_storage_provider()
    )
    presentation_dir = (
        BASE_STORAGE_DIR
        / build_presentation_storage_prefix(user_id, presentation_id)
    )
    presentation_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, str] = {
        "presentation.html": str(html or ""),
        "title.txt": str(title or "Presentation").strip() or "Presentation",
        "metadata.json": json.dumps(metadata, ensure_ascii=False, indent=2),
    }
    for relative_path, content in artifacts.items():
        target = presentation_dir / relative_path
        target.write_text(content, encoding="utf-8")

    if provider == "local":
        return

    adapter = get_user_file_storage_adapter_for_provider(provider)
    for relative_path in artifacts:
        storage_key = build_presentation_storage_key(
            user_id, presentation_id, relative_path
        )
        adapter.upload_file(presentation_dir / relative_path, storage_key)

        # Remote reads use a materialized cache. Replace it immediately so an
        # editor reopen cannot observe the pre-save HTML until the next render.
        materialized = (
            MATERIALIZED_TEMP_DIR
            / "presentations"
            / provider
            / storage_key
        )
        materialized.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(presentation_dir / relative_path, materialized)


def get_presentation_slide_count(
    user_id: str,
    presentation_id: str,
    *,
    storage_provider: str | None = None,
    storage_prefix: str | None = None,
) -> int:
    metadata = load_presentation_metadata(
        user_id,
        presentation_id,
        storage_provider=storage_provider,
        storage_prefix=storage_prefix,
    )
    raw_value = metadata.get("slide_count")
    try:
        count = int(raw_value)
    except Exception:
        return 0
    return max(0, count)
