import base64
from datetime import datetime, timezone
import json
import logging
import mimetypes
import os
import re
import shutil
import time
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import httpx
from PIL import Image

from app.database import SessionLocal
from app.files.models import get_file
from app.files.utils import (
    materialize_file_record,
    persist_generated_file_bytes,
    persist_generated_file_replacement_bytes,
    release_user_file_quota_reservation,
    reserve_user_file_quota,
)
from app.llmstats.models import create_tool_call_statistic
from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.service_connections.utils import (
    SERVICE_PURPOSE_SLIDE_RENDERER,
    get_service_connection_candidates,
    has_configured_service_connection,
    record_service_connection_runtime_status,
)

logger = logging.getLogger(__name__)

SLIDE_PRESENTATION_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_ASSET_FILENAME_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")
_KNOWN_RENDERING_VERSIONS = ("v1", "v2")
_MAX_RENDER_RESPONSE_BYTES = 300 * 1024 * 1024
_MAX_RENDER_ZIP_ENTRIES = 200
_MAX_RENDER_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_MAX_RENDER_PPTX_BYTES = 150 * 1024 * 1024
_MAX_RENDER_SLIDE_BYTES = 25 * 1024 * 1024
_MAX_RENDER_SLIDES = 50
_LEGACY_RENDERER_METADATA = {
    "available": False,
    "version": "",
    "tag": "",
    "api_contract_version": None,
    "beta": None,
    "active_rendering_version": "",
    "supported_rendering_versions": list(_KNOWN_RENDERING_VERSIONS),
    "available_rendering_versions": list(_KNOWN_RENDERING_VERSIONS),
    "features": {},
}


class _SlideRendererServiceUnavailableError(RuntimeError):
    """Raised only when the renderer's dedicated health probe fails."""


class _SlideRendererMetadataError(RuntimeError):
    """Raised when one renderer's version endpoint cannot be trusted."""


def _legacy_renderer_metadata() -> dict[str, Any]:
    metadata = dict(_LEGACY_RENDERER_METADATA)
    metadata["supported_rendering_versions"] = list(_KNOWN_RENDERING_VERSIONS)
    metadata["available_rendering_versions"] = list(_KNOWN_RENDERING_VERSIONS)
    metadata["features"] = {}
    return metadata


SLIDE_RENDERER_API_TIMEOUT_SECONDS = 180
SLIDE_RENDERER_HEALTH_TIMEOUT_SECONDS = 10


def is_render_slide_presentation_configured(db=None) -> bool:
    if db is not None:
        return has_configured_service_connection(db, SERVICE_PURPOSE_SLIDE_RENDERER)

    close_db = False
    try:
        db = SessionLocal()
        close_db = True
        return has_configured_service_connection(db, SERVICE_PURPOSE_SLIDE_RENDERER)
    finally:
        if close_db:
            db.close()


def _normalize_rendering_version(raw_value: Any) -> str:
    normalized = _coerce_rendering_version(raw_value)
    return normalized or "v1"


def _coerce_rendering_version(raw_value: Any) -> str | None:
    normalized = str(raw_value or "").strip().lower()
    if normalized in _KNOWN_RENDERING_VERSIONS:
        return normalized
    return None


def _ensure_filename(filename: Optional[str]) -> str:
    candidate = (filename or "presentation.pptx").strip() or "presentation.pptx"
    sanitized = Path(candidate).name
    if not sanitized.lower().endswith(".pptx"):
        sanitized = f"{sanitized}.pptx"
    return sanitized


def _resolve_render_endpoint(base_url: str) -> str:
    # Always target the canonical route. The root resolver removes historical
    # /api/v1 and /api/v1/render suffixes from saved service connections before
    # rebuilding the current endpoint.
    return _resolve_renderer_root_endpoint(base_url, "/api/render")


def _renderer_headers(connection: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = str(connection.get("api_key") or "").strip()
    if api_key:
        # Send both supported forms so health and render requests work with
        # dedicated renderers and older code-execution gateways.
        headers["X-API-Key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _is_retryable_renderer_status(status_code: int) -> bool:
    return status_code in {401, 429, 500, 502, 503, 504}


def _resolve_health_endpoint(base_url: str) -> str:
    return _resolve_renderer_root_endpoint(base_url, "/health")


def _resolve_version_endpoint(base_url: str) -> str:
    return _resolve_renderer_root_endpoint(base_url, "/version")


def _resolve_renderer_root_endpoint(base_url: str, path: str) -> str:
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    if not normalized_base_url:
        return ""

    for suffix in ("/api/v1/render", "/api/render", "/api/v1", "/api"):
        if normalized_base_url.endswith(suffix):
            normalized_base_url = normalized_base_url[: -len(suffix)]
            break

    normalized_base_url = normalized_base_url.rstrip("/")
    if not normalized_base_url:
        return ""
    normalized_path = "/" + str(path or "").strip("/")
    return f"{normalized_base_url}{normalized_path}"


def _normalize_supported_rendering_versions(raw_value: Any) -> list[str]:
    if not isinstance(raw_value, (list, tuple, set)):
        return []

    supported: list[str] = []
    for item in raw_value:
        normalized = _coerce_rendering_version(item)
        if normalized and normalized not in supported:
            supported.append(normalized)
    return supported


def _parse_renderer_version_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _legacy_renderer_metadata()

    features = payload.get("features")
    if not isinstance(features, dict):
        features = {}
    supported = _normalize_supported_rendering_versions(
        payload.get("supported_rendering_versions") or features.get("rendering_versions")
    )
    available = _normalize_supported_rendering_versions(payload.get("available_rendering_versions"))
    active_rendering_version = _normalize_rendering_version(
        payload.get("active_rendering_version") or payload.get("default_rendering_version")
    )
    metadata = _legacy_renderer_metadata()
    metadata.update(
        {
            "available": True,
            "version": str(payload.get("version") or "").strip(),
            "tag": str(payload.get("tag") or "").strip(),
            "api_contract_version": payload.get("api_contract_version"),
            "beta": payload.get("beta") if isinstance(payload.get("beta"), bool) else None,
            "active_rendering_version": active_rendering_version,
            "supported_rendering_versions": supported or list(_KNOWN_RENDERING_VERSIONS),
            "available_rendering_versions": available or list(_KNOWN_RENDERING_VERSIONS),
            "features": features,
        }
    )
    return metadata


def _fetch_renderer_version_metadata(
    client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    version_endpoint = _resolve_version_endpoint(base_url)
    if not version_endpoint:
        return _legacy_renderer_metadata()

    try:
        response = client.get(
            version_endpoint,
            headers=headers,
            timeout=SLIDE_RENDERER_HEALTH_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        raise _SlideRendererMetadataError(
            "Slide renderer version check timed out. The renderer service is currently unavailable."
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("Failed to reach slide renderer version endpoint: %s", exc)
        return _legacy_renderer_metadata()

    if response.status_code == 404:
        return _legacy_renderer_metadata()
    if response.status_code >= 400:
        raise _SlideRendererMetadataError(
            f"Slide renderer version check failed with HTTP {response.status_code}. Please try again later."
        )

    try:
        return _parse_renderer_version_payload(response.json())
    except ValueError:
        logger.warning(
            "Slide renderer version endpoint returned non-JSON response from %s; "
            "continuing with legacy renderer metadata.",
            version_endpoint,
        )
        return _legacy_renderer_metadata()


def _assert_renderer_is_healthy(
    client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
) -> None:
    health_endpoint = _resolve_health_endpoint(base_url)
    if not health_endpoint:
        raise _SlideRendererServiceUnavailableError(
            "Slide presentation renderer is not configured. Please set the renderer API base URL in admin settings."
        )

    try:
        response = client.get(
            health_endpoint,
            headers=headers,
            timeout=SLIDE_RENDERER_HEALTH_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        raise _SlideRendererServiceUnavailableError(
            "Slide renderer health check timed out. The renderer service is currently unavailable."
        ) from exc
    except httpx.RequestError as exc:
        logger.error("Failed to reach slide renderer health endpoint: %s", exc)
        raise _SlideRendererServiceUnavailableError(
            "Slide renderer service is unavailable right now. Please try again later."
        ) from exc

    if response.status_code >= 400:
        raise _SlideRendererServiceUnavailableError(
            f"Slide renderer health check failed with HTTP {response.status_code}. Please try again later."
        )

    response_text = (response.text or "").strip()
    if not response_text:
        return

    if response_text.lower() in {"ok", "healthy", "ready"}:
        return

    try:
        payload = response.json()
    except ValueError as exc:
        raise _SlideRendererServiceUnavailableError(
            "Slide renderer health check returned an invalid response. Please try again later."
        ) from exc

    if isinstance(payload, dict):
        status = str(payload.get("status") or "").strip().lower()
        if status in {"ok", "healthy", "ready"}:
            return
        if payload.get("ok") is True or payload.get("healthy") is True or payload.get("ready") is True:
            return

    raise _SlideRendererServiceUnavailableError(
        "Slide renderer service is unavailable right now. Please try again later."
    )

def _sanitize_asset_filename(raw_name: str, fallback_base: str, mime_type: str | None = None) -> str:
    file_name = Path(str(raw_name or "")).name.strip()
    if not file_name:
        file_name = fallback_base

    if "." not in file_name and mime_type:
        guessed_suffix = mimetypes.guess_extension(mime_type) or ""
        if guessed_suffix:
            file_name = f"{file_name}{guessed_suffix}"

    if "." in file_name:
        stem, dot, suffix = file_name.rpartition(".")
        safe_stem = _ASSET_FILENAME_SANITIZER.sub("_", stem).strip("._-")
        safe_suffix = _ASSET_FILENAME_SANITIZER.sub("_", suffix).strip("._-")
        file_name = f"{safe_stem}.{safe_suffix}" if safe_stem and safe_suffix else ""
    else:
        file_name = _ASSET_FILENAME_SANITIZER.sub("_", file_name).strip("._-")

    if not file_name:
        file_name = fallback_base

    return file_name[:200]


def _build_input_files_payload(db, user_id: str, file_ids: list[str] | None) -> list[dict[str, str]]:
    if not file_ids:
        return []

    unique_file_ids: list[str] = []
    seen_file_ids: set[str] = set()
    for raw_file_id in file_ids:
        if not isinstance(raw_file_id, str):
            continue
        file_id = raw_file_id.strip()
        if not file_id or file_id in seen_file_ids:
            continue
        seen_file_ids.add(file_id)
        unique_file_ids.append(file_id)

    payload: list[dict[str, str]] = []
    used_names: set[str] = set()

    for index, file_id in enumerate(unique_file_ids, start=1):
        file_record = get_file(db, file_id, str(user_id))
        if not file_record:
            logger.warning("Skipping missing or unauthorized slide renderer input file id: %s", file_id)
            continue

        try:
            file_path = materialize_file_record(file_record, str(user_id))
            if not file_path.exists():
                logger.warning("Skipping non-existent slide renderer input file path: %s", file_path)
                continue

            file_bytes = file_path.read_bytes()
            if not file_bytes:
                logger.warning("Skipping empty input file for slide renderer: %s", file_id)
                continue

            meta = file_record.meta if isinstance(file_record.meta, dict) else {}
            original_name = (
                meta.get("original_filename")
                if isinstance(meta.get("original_filename"), str)
                else file_record.file_name
            )
            fallback_name = f"asset_{index}"
            safe_name = _sanitize_asset_filename(
                raw_name=original_name,
                fallback_base=fallback_name,
                mime_type=file_record.file_type,
            )

            if safe_name in used_names:
                stem, dot, suffix = safe_name.rpartition(".")
                if dot and stem:
                    base_name = stem
                    extension = f".{suffix}"
                else:
                    base_name = safe_name
                    extension = ""
                dedupe_idx = 2
                candidate = f"{base_name}_{dedupe_idx}{extension}"
                while candidate in used_names:
                    dedupe_idx += 1
                    candidate = f"{base_name}_{dedupe_idx}{extension}"
                safe_name = candidate

            used_names.add(safe_name)
            payload.append(
                {
                    "file_name": safe_name,
                    "base64_content": base64.b64encode(file_bytes).decode("ascii"),
                }
            )
        except Exception as exc:
            logger.warning("Failed to include slide renderer input file %s: %s", file_id, exc)

    return payload


def _extract_zip_bundle(bundle_bytes: bytes) -> tuple[bytes, list[bytes]]:
    """Read a bounded renderer bundle and preserve numeric slide order."""

    def slide_sort_key(name: str) -> tuple[int, str]:
        match = re.search(r"(\d+)(?=\.png$)", Path(name).name, re.IGNORECASE)
        return (int(match.group(1)) if match else 2**31, name.lower())

    try:
        with zipfile.ZipFile(BytesIO(bundle_bytes)) as zf:
            entries = [entry for entry in zf.infolist() if entry.filename and not entry.is_dir()]
            if len(entries) > _MAX_RENDER_ZIP_ENTRIES:
                raise RuntimeError("Renderer ZIP contained too many files.")
            if sum(max(0, entry.file_size) for entry in entries) > _MAX_RENDER_UNCOMPRESSED_BYTES:
                raise RuntimeError("Renderer ZIP expands beyond the allowed size.")
            names = [entry.filename for entry in entries]
            pptx_candidates = sorted(
                [name for name in names if name.lower().endswith(".pptx")]
            )
            if not pptx_candidates:
                raise RuntimeError("Renderer ZIP did not contain a .pptx file.")
            pptx_info = zf.getinfo(pptx_candidates[0])
            if pptx_info.file_size <= 0 or pptx_info.file_size > _MAX_RENDER_PPTX_BYTES:
                raise RuntimeError("Renderer PPTX exceeded the allowed size.")
            pptx_bytes = zf.read(pptx_candidates[0])

            slide_candidates = sorted(
                [
                    name
                    for name in names
                    if name.lower().startswith("slides/") and name.lower().endswith(".png")
                ],
                key=slide_sort_key,
            )
            if not slide_candidates:
                slide_candidates = sorted(
                    [name for name in names if name.lower().endswith(".png")],
                    key=slide_sort_key,
                )
            if not slide_candidates:
                raise RuntimeError("Renderer ZIP did not contain slide preview images.")
            if len(slide_candidates) > _MAX_RENDER_SLIDES:
                raise RuntimeError("Renderer returned more than 50 slide images.")
            slide_numbers = []
            for name in slide_candidates:
                match = re.search(r"(\d+)(?=\.png$)", Path(name).name, re.IGNORECASE)
                if not match:
                    raise RuntimeError("Renderer slide image names must include slide numbers.")
                slide_numbers.append(int(match.group(1)))
                info = zf.getinfo(name)
                if info.file_size <= 0 or info.file_size > _MAX_RENDER_SLIDE_BYTES:
                    raise RuntimeError("A rendered slide image exceeded the allowed size.")
            if slide_numbers != list(range(1, len(slide_candidates) + 1)):
                raise RuntimeError("Renderer slide images were missing or duplicated.")

            slide_images = [zf.read(name) for name in slide_candidates]
            return pptx_bytes, slide_images
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Renderer response was not a valid ZIP file.") from exc


def _stage_rendered_slide_images(
    presentation_dir: Path, slide_images: list[bytes]
) -> Path:
    """Validate all previews before making any of them visible."""
    if not slide_images:
        raise RuntimeError("Slide renderer returned no preview images.")

    staged_dir = presentation_dir / f".images-{uuid.uuid4().hex}.staged"
    staged_dir.mkdir(parents=True, exist_ok=False)
    try:
        for index, image_bytes in enumerate(slide_images, start=1):
            if not image_bytes:
                raise RuntimeError(f"Rendered slide {index} was empty.")
            try:
                with Image.open(BytesIO(image_bytes)) as image:
                    image.verify()
                with Image.open(BytesIO(image_bytes)) as image:
                    if image.format != "PNG" or image.size != (1920, 1080):
                        raise RuntimeError(
                            f"Rendered slide {index} was not a 1920 by 1080 PNG."
                        )
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(
                    f"Rendered slide {index} was not a valid PNG."
                ) from exc
            (staged_dir / f"slide_{index}.png").write_bytes(image_bytes)
        return staged_dir
    except Exception:
        shutil.rmtree(staged_dir, ignore_errors=True)
        raise


def _publish_staged_slide_images(presentation_dir: Path, staged_dir: Path) -> None:
    """Swap a complete preview set into place without a missing-image window."""
    images_dir = presentation_dir / "images"
    backup_dir = presentation_dir / f".images-{uuid.uuid4().hex}.backup"
    try:
        if images_dir.exists():
            os.replace(images_dir, backup_dir)
        os.replace(staged_dir, images_dir)
    except Exception:
        if not images_dir.exists() and backup_dir.exists():
            os.replace(backup_dir, images_dir)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.rmtree(staged_dir, ignore_errors=True)


def render_slide_presentation(
    html: str,
    user_id: str,
    filename: str,
    *,
    presentation_dir: str | Path,
    input_file_ids: list[str] | None = None,
    existing_file_id: str | None = None,
    artifact_presentation_id: str | None = None,
    db=None,
) -> dict[str, Any]:
    start_time = time.time()
    staged_images_dir: Path | None = None
    quota_reservation = None
    
    if not user_id:
        raise ValueError("user_id is required for PPTX rendering")
    if not html or not html.strip():
        raise ValueError("html content is required for PPTX rendering")

    close_db = False
    
    try:
        if db is None:
            db = SessionLocal()
            close_db = True

        connections = get_service_connection_candidates(db, SERVICE_PURPOSE_SLIDE_RENDERER)

        if not connections:
            raise RuntimeError(
                "Slide presentation renderer is not configured. Add a service connection in admin settings."
            )

        clean_filename = _ensure_filename(filename)
        try:
            input_files_payload = _build_input_files_payload(
                db=db,
                user_id=str(user_id),
                file_ids=input_file_ids,
            )

            payload: dict[str, Any] = {
                "html": html,
            }
            if input_files_payload:
                payload["input_files"] = input_files_payload

            bundle_bytes: bytes | None = None
            response_headers: dict[str, str] = {}
            renderer_metadata = _legacy_renderer_metadata()
            active_connection: dict[str, Any] | None = None
            active_base_url = ""
            last_service_error: Exception | None = None

            for connection in connections:
                if not connection:
                    continue
                active_base_url = str(connection.get("base_url") or "").strip().rstrip("/")
                render_endpoint = _resolve_render_endpoint(active_base_url)
                if not render_endpoint:
                    continue
                headers = _renderer_headers(connection)
                try:
                    try:
                        assert_url_allowed(db, url=active_base_url, feature="Slide renderer service")
                    except OutboundRequestBlockedError as exc:
                        raise RuntimeError(str(exc)) from exc

                    if quota_reservation is None:
                        quota_reservation = reserve_user_file_quota(
                            db,
                            user_id=str(user_id),
                            purpose="slide_presentation_render",
                            reserved_files=0 if existing_file_id else 1,
                            # An update may replace the old bytes without
                            # increasing usage. Its exact size delta is
                            # enforced at persistence time.
                            reserved_bytes=0 if existing_file_id else 1,
                        )

                    with httpx.Client(timeout=SLIDE_RENDERER_API_TIMEOUT_SECONDS) as client:
                        _assert_renderer_is_healthy(
                            client,
                            base_url=active_base_url,
                            headers=headers,
                        )
                        renderer_metadata = _fetch_renderer_version_metadata(
                            client,
                            base_url=active_base_url,
                            headers=headers,
                        )
                        record_service_connection_runtime_status(
                            db,
                            connection,
                            SERVICE_PURPOSE_SLIDE_RENDERER,
                            available=True,
                            message="Available",
                        )
                        with client.stream(
                            "POST", render_endpoint, json=payload, headers=headers
                        ) as candidate_response:
                            status_code = candidate_response.status_code
                            if status_code >= 400:
                                error_limit = 64 * 1024
                                bounded_error = bytearray()
                                for chunk in candidate_response.iter_bytes():
                                    remaining = error_limit - len(bounded_error)
                                    if remaining <= 0:
                                        break
                                    bounded_error.extend(chunk[:remaining])
                                    if len(bounded_error) >= error_limit:
                                        break
                                error_bytes = bytes(bounded_error)
                                detail = ""
                                try:
                                    detail_payload = json.loads(
                                        error_bytes.decode("utf-8", errors="replace")
                                    )
                                    detail = str(detail_payload.get("detail") or "").strip()
                                except Exception:
                                    detail = ""
                                if status_code == 400:
                                    raise RuntimeError(
                                        detail
                                        or "Slide renderer rejected the request payload (HTTP 400)."
                                    )
                                if status_code == 401:
                                    error = RuntimeError(
                                        detail
                                        or "Slide renderer authentication failed (HTTP 401)."
                                    )
                                elif status_code == 429:
                                    error = RuntimeError(
                                        detail
                                        or "Slide renderer is currently saturated (HTTP 429)."
                                    )
                                elif status_code == 504:
                                    error = RuntimeError(
                                        detail
                                        or "Slide renderer timed out while generating the deck (HTTP 504)."
                                    )
                                else:
                                    error = RuntimeError(
                                        detail
                                        or f"Slide renderer failed with HTTP {status_code}."
                                    )

                                if _is_retryable_renderer_status(status_code):
                                    last_service_error = error
                                    continue
                                raise error

                            raw_length = candidate_response.headers.get("Content-Length")
                            if raw_length:
                                try:
                                    declared_length = int(raw_length)
                                except ValueError as exc:
                                    raise RuntimeError(
                                        "Slide renderer returned an invalid content length."
                                    ) from exc
                                if declared_length > _MAX_RENDER_RESPONSE_BYTES:
                                    raise RuntimeError(
                                        "Slide renderer response exceeded the allowed size."
                                    )
                            chunks: list[bytes] = []
                            received = 0
                            for chunk in candidate_response.iter_bytes():
                                received += len(chunk)
                                if received > _MAX_RENDER_RESPONSE_BYTES:
                                    raise RuntimeError(
                                        "Slide renderer response exceeded the allowed size."
                                    )
                                chunks.append(chunk)
                            bundle_bytes = b"".join(chunks)
                            response_headers = dict(candidate_response.headers)

                    active_connection = connection
                    break
                except _SlideRendererMetadataError as exc:
                    # A broken version endpoint is specific to this candidate;
                    # try the next configured renderer without aborting failover.
                    last_service_error = exc
                    continue
                except _SlideRendererServiceUnavailableError as exc:
                    # This exception is reserved for the payload-independent
                    # /health probe, so it is safe to update shared health.
                    last_service_error = exc
                    record_service_connection_runtime_status(
                        db,
                        connection,
                        SERVICE_PURPOSE_SLIDE_RENDERER,
                        available=False,
                        message=str(exc),
                        failure_scope="service",
                    )
                    continue
                except httpx.TimeoutException:
                    last_service_error = RuntimeError(
                        "Slide renderer timed out. Try reducing slide complexity or using fewer embedded assets."
                    )
                    continue
                except httpx.RequestError as exc:
                    logger.error("Failed to connect to slide renderer API: %s", exc)
                    last_service_error = RuntimeError("Failed to connect to slide renderer service.")
                    continue
                except RuntimeError as exc:
                    if "slide renderer service blocked" in str(exc).lower() or "health check" in str(exc).lower():
                        last_service_error = exc
                        continue
                    raise

            if bundle_bytes is None:
                if last_service_error:
                    raise RuntimeError(str(last_service_error))
                raise RuntimeError("No available slide renderer service connection could handle the request.")

            if not bundle_bytes:
                raise RuntimeError("Slide renderer returned an empty response.")

            pptx_bytes, slide_images = _extract_zip_bundle(bundle_bytes)
            pres_path = Path(presentation_dir)
            pres_path.mkdir(parents=True, exist_ok=True)
            staged_images_dir = _stage_rendered_slide_images(
                pres_path, slide_images
            )
            rendered_version = _normalize_rendering_version(
                response_headers.get("X-Rendering-Version")
                or renderer_metadata.get("active_rendering_version")
            )
            renderer_app_version = str(
                response_headers.get("X-Renderer-Version") or renderer_metadata.get("version") or ""
            ).strip()
            renderer_version_tag = str(
                response_headers.get("X-Renderer-Version-Tag") or renderer_metadata.get("tag") or ""
            ).strip()

            meta = {
                "original_filename": clean_filename,
                "origin": "assistant",
                "render_slide_presentation": True,
                "presentation_id": artifact_presentation_id or pres_path.name,
                "rendering_version": rendered_version,
                "renderer_app_version": renderer_app_version,
                "renderer_version_tag": renderer_version_tag,
                "renderer_api_contract_version": renderer_metadata.get("api_contract_version"),
                "renderer_beta": renderer_metadata.get("beta"),
                "renderer_supported_rendering_versions": renderer_metadata.get(
                    "supported_rendering_versions",
                    [],
                ),
                "renderer_available_rendering_versions": renderer_metadata.get(
                    "available_rendering_versions",
                    [],
                ),
                "render_api_base_url": active_base_url,
                "service_connection_id": active_connection.get("id") if active_connection else "",
                "service_connection_name": active_connection.get("name") if active_connection else "",
                "service_connection_legacy": bool(active_connection.get("legacy")) if active_connection else False,
            }

            if existing_file_id:
                file_record = get_file(db, str(existing_file_id), str(user_id))
                if not file_record:
                    raise RuntimeError("Existing presentation file could not be found for update.")
                file_record = persist_generated_file_replacement_bytes(
                    db,
                    user_id=str(user_id),
                    file_record=file_record,
                    original_filename=clean_filename,
                    file_bytes=pptx_bytes,
                    file_type=SLIDE_PRESENTATION_MIME_TYPE,
                    file_category="document",
                    meta=meta,
                    quota_reservation_id=(
                        quota_reservation.reservation_id
                        if quota_reservation
                        else None
                    ),
                )
            else:
                stored_file_id = str(uuid.uuid4())
                stored_file_name = f"{stored_file_id}.pptx"
                file_record = persist_generated_file_bytes(
                    db=db,
                    user_id=str(user_id),
                    original_filename=clean_filename,
                    file_bytes=pptx_bytes,
                    file_type=SLIDE_PRESENTATION_MIME_TYPE,
                    file_category="document",
                    meta=meta,
                    file_id=stored_file_id,
                    file_name=stored_file_name,
                    quota_reservation_id=(
                        quota_reservation.reservation_id
                        if quota_reservation
                        else None
                    ),
                )

            slide_count = len(slide_images)
            _publish_staged_slide_images(pres_path, staged_images_dir)

            updated_meta = dict(file_record.meta or {})
            updated_meta["presentation_id"] = artifact_presentation_id or pres_path.name
            updated_meta["slide_count"] = slide_count
            file_record.last_updated_at = datetime.now(timezone.utc)
            file_record.meta = updated_meta
            db.commit()
            db.refresh(file_record)

            # Log success
            execution_time = time.time() - start_time
            create_tool_call_statistic(
                db=db,
                tool_name="slide_presentation_render",
                success=True,
                execution_time=execution_time,
                user_id=user_id,
                meta={
                    "phase": "rendering",
                    "slide_count": slide_count,
                    "rendering_version": rendered_version,
                    "renderer_app_version": renderer_app_version,
                    "renderer_version_tag": renderer_version_tag,
                    "service_connection_id": active_connection.get("id") if active_connection else "",
                    "service_connection_name": active_connection.get("name") if active_connection else "",
                },
            )

            return {
                "file_id": file_record.id,
                "slide_count": slide_count,
                "rendering_version": rendered_version,
                "service_connection_id": active_connection.get("id") if active_connection else "",
            }
        finally:
            if db is not None:
                release_user_file_quota_reservation(
                    db,
                    quota_reservation.reservation_id if quota_reservation else None,
                )
            if close_db:
                try:
                    db.close()
                except Exception:
                    logger.error("Failed to close DB session after PPTX rendering")
    except Exception as exc:
        if staged_images_dir is not None:
            shutil.rmtree(staged_images_dir, ignore_errors=True)
        # Log error
        execution_time = time.time() - start_time
        stats_db = db
        temp_stats_db = None
        if stats_db is None or close_db:
            temp_stats_db = SessionLocal()
            stats_db = temp_stats_db
        try:
            create_tool_call_statistic(
                db=stats_db,
                tool_name="slide_presentation_render",
                success=False,
                error_message=str(exc),
                execution_time=execution_time,
                user_id=user_id,
                meta={"phase": "rendering"},
            )
        except Exception:
            logger.exception("Failed to record slide_presentation_render failure statistics")
        finally:
            if temp_stats_db is not None:
                try:
                    temp_stats_db.close()
                except Exception:
                    logger.error("Failed to close temporary DB session after PPTX rendering failure")
        raise
