import logging
import mimetypes
import json
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from app.chats.models import ChatMessages
from app.database import SessionLocal
from app.files.models import Files
from app.files.utils import (
    get_file_category,
    materialize_file_record,
    persist_generated_file_bytes,
    release_user_file_quota_reservation,
    reserve_user_file_quota,
)
from app.llm.google_aistudio.video_generation import generate_video as google_generate_video
from app.llm.models import LLMProvider
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.llm.openai_responses.video_generation import (
    generate_video as openai_compatible_generate_video,
    openai_compatible_video_base_url,
)
from app.llm.openrouter.video_generation import generate_video as openrouter_generate_video
from app.llm.xai.video_generation import generate_video as xai_generate_video
from app.llm.xai.common import download_xai_result_url, xai_headers
from app.llm.video_generation.shared import request_with_retries
from app.settings.models import get_settings_page


logger = logging.getLogger(__name__)


OPENAI_COMPATIBLE_VIDEO_PROVIDER_TYPES = {
    "openai_responses",
    "openai_chat_completions",
}

PROVIDER_GENERATORS: dict[
    str,
    Callable[[LLMProvider, str, str, dict[str, Any], list[dict[str, Any]] | None], dict[str, Any]],
] = {
    "openai_responses": openai_compatible_generate_video,
    "openai_chat_completions": openai_compatible_generate_video,
    "google_aistudio": google_generate_video,
    "openrouter": openrouter_generate_video,
    "xai": xai_generate_video,
}

DEFAULT_VIDEO_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/avi",
    ".mpeg": "video/mpeg",
    ".ogg": "video/ogg",
}
XAI_VIDEO_RESULT_MAX_BYTES = 512 * 1024 * 1024


VIDEO_REFERENCE_SUPPORTED_PROVIDER_TYPES = {
    *OPENAI_COMPATIBLE_VIDEO_PROVIDER_TYPES,
    "google_aistudio",
    "openrouter",
    "xai",
}
VIDEO_REFERENCE_ALLOWED_CATEGORIES_BY_PROVIDER = {
    "openai_responses": {"image"},
    "openai_chat_completions": {"image"},
    "google_aistudio": {"image"},
    "openrouter": {"image"},
    "xai": {"image"},
}


def _coerce_optional_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _extract_attachment_ids(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            return [stripped]
        return _extract_attachment_ids(parsed)
    if isinstance(raw, dict):
        file_id = raw.get("id") or raw.get("file_id")
        return [str(file_id)] if file_id else []
    if isinstance(raw, list):
        ids: list[str] = []
        for item in raw:
            ids.extend(_extract_attachment_ids(item))
        return ids
    return [str(raw)]


def _collect_file_ids_from_content(raw_content: Any) -> list[str]:
    if raw_content is None:
        return []
    if isinstance(raw_content, str):
        stripped = raw_content.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            return []
        return _collect_file_ids_from_content(parsed)
    if isinstance(raw_content, dict):
        collected: list[str] = []
        for field in ("images", "videos", "audios", "documents"):
            collected.extend(_extract_attachment_ids(raw_content.get(field)))
        return collected
    if isinstance(raw_content, list):
        collected: list[str] = []
        for block in raw_content:
            if not isinstance(block, dict):
                continue
            for field in ("images", "videos", "audios", "documents"):
                collected.extend(_extract_attachment_ids(block.get(field)))
        return collected
    return []


def _collect_file_ids_from_chat_history(chat_history: list | None) -> list[str]:
    if not isinstance(chat_history, list):
        return []
    collected: list[str] = []
    for message in chat_history:
        if isinstance(message, dict):
            for field in ("images", "videos", "audios", "documents"):
                collected.extend(_extract_attachment_ids(message.get(field)))
            collected.extend(_collect_file_ids_from_content(message.get("content")))
            continue
        for field in ("images", "videos", "audios", "documents"):
            collected.extend(_extract_attachment_ids(getattr(message, field, None)))
        collected.extend(_collect_file_ids_from_content(getattr(message, "content", None)))
    return collected


def _collect_file_ids_from_chat_db(db, chat_id: str | None) -> list[str]:
    if not chat_id:
        return []
    rows = (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == chat_id)
        .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        .all()
    )
    collected: list[str] = []
    for row in rows:
        collected.extend(_collect_file_ids_from_content(getattr(row, "content", None)))
    return collected


def _dedupe_ids(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _resolve_reference_file_ids(
    *,
    db,
    chat_id: str | None,
    chat_history: list | None,
    explicit_file_ids: list[str] | None,
) -> list[str]:
    prioritized: list[str] = []
    if explicit_file_ids:
        prioritized.extend(explicit_file_ids)
    prioritized.extend(_collect_file_ids_from_chat_db(db, chat_id))
    prioritized.extend(_collect_file_ids_from_chat_history(chat_history))
    return _dedupe_ids(prioritized)


def _load_reference_files(
    db,
    user_id: str,
    file_ids: list[str],
    *,
    allowed_categories: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not file_ids:
        return []

    normalized_allowed = {
        str(item).strip().lower()
        for item in (allowed_categories or set())
        if str(item).strip()
    }

    rows = (
        db.query(Files)
        .filter(Files.user_id == str(user_id), Files.id.in_(file_ids))
        .all()
    )
    row_map = {str(row.id): row for row in rows if row and row.id}

    loaded: list[dict[str, Any]] = []
    for file_id in file_ids:
        row = row_map.get(str(file_id))
        if not row:
            continue
        category = str(row.file_category or "").strip().lower()
        mime_type = str(row.file_type or "").strip().lower()
        if normalized_allowed and category not in normalized_allowed:
            if not (category == "unknown" and mime_type.startswith("image/") and "image" in normalized_allowed):
                continue
        try:
            file_path = materialize_file_record(row, str(row.user_id))
            file_bytes = file_path.read_bytes()
        except Exception:
            logger.warning("Failed to read reference file %s", file_id, exc_info=True)
            continue
        if not file_bytes:
            continue
        loaded.append(
            {
                "file_id": str(file_id),
                "filename": row.file_name,
                "mime_type": row.file_type,
                "file_category": row.file_category,
                "bytes": file_bytes,
            }
        )
    return loaded


def _parse_video_generation_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    from app.admin.settings.schema_categories.video_generation import (
        VideoGenerationSettings,
    )

    validated = VideoGenerationSettings(**payload)
    return validated.model_dump(exclude_none=False)


def _is_video_reference_enabled(config: dict[str, Any]) -> bool:
    return _coerce_optional_bool((config or {}).get("enable_reference_files"), default=False)


def get_video_reference_tool_params(db=None) -> dict:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        config = _get_video_generation_config(db=db)
        provider_id = str(config.get("provider_id") or "").strip()
        model_name = str(config.get("model_name") or "").strip()
        if not provider_id or not model_name:
            return {}

        provider = _resolve_provider(db, provider_id)
        if not provider:
            return {}
        provider_type = str(provider.provider or "").strip().lower()
        if provider_type not in VIDEO_REFERENCE_SUPPORTED_PROVIDER_TYPES:
            return {}
        if not _is_video_reference_enabled(config):
            return {}

        return {
            "use_reference_files": {
                "type": "boolean",
                "description": (
                    "When true, reference image files from the chat conversation are passed "
                    "to video generation (for example as first/last frames)."
                ),
            }
        }
    except Exception:
        logger.exception("Failed to resolve video reference tool params")
        return {}
    finally:
        if close_db:
            db.close()


def _get_video_generation_config(db=None) -> dict[str, Any]:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        base = {
            "provider_id": "",
            "model_name": "",
            "duration_seconds": 8,
            "size": "720x1280",
            "enable_reference_files": False,
            "generate_audio": None,
            "seed": None,
            "timeout_seconds": 600,
            "poll_interval_seconds": 5,
            "max_retries": 2,
        }
        record = get_settings_page(db, "video_generation")
        if record and isinstance(record.data, dict):
            base.update(record.data)
        return _parse_video_generation_config(base)
    finally:
        if close_db:
            db.close()


def _merge_video_generation_config(
    base_config: dict[str, Any] | None,
    override_config: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(base_config) if isinstance(base_config, dict) else {}
    override = override_config if isinstance(override_config, dict) else {}
    if not override:
        return _parse_video_generation_config(base)

    # Intentionally ignore provider/model overrides. Chat model settings only
    # override generation parameters for the configured tool model.
    for key, value in override.items():
        if key in {"provider_id", "model_name"}:
            continue
        if value is not None:
            base[key] = value

    return _parse_video_generation_config(base)


def _resolve_provider(db, provider_id: str) -> LLMProvider | None:
    if not provider_id:
        return None
    return db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()


def _generate_video_payload(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provider_type = (provider.provider or "").strip().lower()
    generator = PROVIDER_GENERATORS.get(provider_type)
    if not generator:
        raise ValueError(f"Unsupported video generation provider type: {provider_type}")
    return generator(provider, model_name, prompt, config, reference_files=reference_files)


def _is_probable_video_content(content_type: str, source_url: str | None = None) -> bool:
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized.startswith("video/"):
        return True
    parsed = urlparse(source_url or "")
    suffix = Path(parsed.path).suffix.lower()
    if suffix in DEFAULT_VIDEO_MIME_BY_EXT:
        return True
    return False


def _download_video_from_url(
    source_url: str,
    provider: LLMProvider,
    provider_type: str,
    *,
    timeout_seconds: int,
    max_retries: int,
) -> tuple[bytes, str]:
    parsed_source = urlparse(source_url)
    if parsed_source.scheme not in {"http", "https"}:
        raise ValueError("Provider returned an unsupported video URL scheme.")

    headers: dict[str, str] = {}
    hostname = (parsed_source.hostname or "").lower()
    if provider_type in OPENAI_COMPATIBLE_VIDEO_PROVIDER_TYPES and provider.api_key:
        # Compatible providers commonly protect their result endpoint with the
        # same credentials as job submission. Forward those credentials only
        # when the returned URL is on the explicitly configured API host.
        configured_hostname = (
            urlparse(openai_compatible_video_base_url(provider)).hostname or ""
        ).lower()
        if hostname and hostname == configured_hostname:
            headers["Authorization"] = f"Bearer {provider.api_key}"
            provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
            headers.update(custom_headers_to_dict(provider_settings.get("custom_headers")))
    elif provider_type == "openrouter" and provider.api_key and hostname in {"openrouter.ai", "eu.openrouter.ai"}:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    if provider_type == "xai":
        # Temporary xAI result URLs are provider-controlled input. Download
        # them through the public-peer-pinning path and never forward the API
        # key to redirects outside the configured API host.
        provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
        provider_base_url = str(provider_settings.get("base_url") or "https://api.x.ai/v1")
        authorized_host = (urlparse(provider_base_url).hostname or "").lower()
        video_bytes, content_type, _final_url = download_xai_result_url(
            source_url,
            operation="video",
            expected_content_prefix="video/",
            max_bytes=XAI_VIDEO_RESULT_MAX_BYTES,
            timeout=min(180, max(30, timeout_seconds)),
            authorized_hosts={authorized_host} if authorized_host else set(),
            authorized_headers=xai_headers(provider, include_content_type=False),
        )
        return video_bytes, content_type

    response = request_with_retries(
        "GET",
        source_url,
        headers=headers or None,
        timeout_seconds=min(180, max(30, timeout_seconds)),
        max_retries=max_retries,
    )
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    video_bytes = response.content or b""
    if not video_bytes:
        raise RuntimeError("Provider video download returned an empty body.")
    if not _is_probable_video_content(content_type, source_url):
        raise RuntimeError(
            f"Provider returned a non-video payload (content-type '{content_type or 'unknown'}')."
        )
    return video_bytes, content_type or "video/mp4"


def _resolve_video_bytes(
    generation_result: dict[str, Any],
    provider: LLMProvider,
    *,
    timeout_seconds: int,
    max_retries: int,
) -> tuple[bytes, str, str | None]:
    inline_videos = generation_result.get("inline_videos") or []
    if inline_videos:
        video_bytes, mime = inline_videos[0]
        if not video_bytes:
            raise RuntimeError("Provider returned empty inline video bytes.")
        if not _is_probable_video_content(mime):
            raise RuntimeError(f"Provider returned unsupported inline mime type: {mime}")
        return video_bytes, mime, None

    urls = generation_result.get("urls") or []
    if not urls:
        raise RuntimeError("Provider did not return a downloadable video URL.")

    download_errors: list[str] = []
    for source_url in urls:
        try:
            video_bytes, mime = _download_video_from_url(
                source_url,
                provider,
                provider.provider or "",
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
            return video_bytes, mime, source_url
        except Exception as exc:
            download_errors.append(str(exc))
            continue

    raise RuntimeError("All provider video download URLs failed validation: " + "; ".join(download_errors))


def _safe_original_name(file_name: str | None, mime_type: str) -> str:
    base = Path(str(file_name).strip()).name if file_name else "generated_video"
    if not base:
        base = "generated_video"

    suffix = Path(base).suffix.lower()
    if suffix not in DEFAULT_VIDEO_MIME_BY_EXT:
        ext = mimetypes.guess_extension(mime_type or "video/mp4") or ".mp4"
        if ext not in DEFAULT_VIDEO_MIME_BY_EXT:
            ext = ".mp4"
        base = f"{Path(base).stem or 'generated_video'}{ext}"
    return base


def _mime_for_filename(file_name: str, fallback: str = "video/mp4") -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    if guessed and guessed.startswith("video/"):
        return guessed
    suffix = Path(file_name).suffix.lower()
    return DEFAULT_VIDEO_MIME_BY_EXT.get(suffix, fallback)


def video_generation(
    prompt: str,
    user_id: str,
    filename: str | None = None,
    config_override: dict[str, Any] | None = None,
    use_reference_files: bool = False,
    reference_file_ids: list[str] | None = None,
    chat_id: str | None = None,
    chat_history: list | None = None,
) -> dict[str, Any]:
    if not user_id:
        raise ValueError("user_id is required for video generation")
    if not prompt or not str(prompt).strip():
        raise ValueError("prompt is required for video generation")

    db = SessionLocal()
    quota_reservation = None
    try:
        quota_reservation = reserve_user_file_quota(
            db,
            user_id=str(user_id),
            purpose="video_generation",
        )
        config = _merge_video_generation_config(
            _get_video_generation_config(db=db),
            config_override,
        )
        provider_id = str(config.get("provider_id") or "").strip()
        model_name = str(config.get("model_name") or "").strip()

        if not provider_id:
            raise RuntimeError("Video generation provider is not configured in admin settings.")
        if not model_name:
            raise RuntimeError("Video generation model is not configured in admin settings.")

        provider = _resolve_provider(db, provider_id)
        if not provider:
            raise RuntimeError("Configured video generation provider no longer exists.")
        provider_type = str(provider.provider or "").strip().lower()

        prompt_text = str(prompt).strip()
        should_use_reference_files = bool(use_reference_files)
        reference_files: list[dict[str, Any]] = []
        if should_use_reference_files:
            if provider_type not in VIDEO_REFERENCE_SUPPORTED_PROVIDER_TYPES:
                supported = ", ".join(sorted(VIDEO_REFERENCE_SUPPORTED_PROVIDER_TYPES))
                raise ValueError(
                    f"Video reference files are not supported for provider '{provider_type}'. "
                    f"Supported providers: {supported}."
                )
            if not _is_video_reference_enabled(config):
                raise ValueError(
                    "Video reference files are disabled for the configured model in admin settings."
                )

            resolved_reference_ids = _resolve_reference_file_ids(
                db=db,
                chat_id=chat_id,
                chat_history=chat_history,
                explicit_file_ids=reference_file_ids,
            )
            allowed_categories = VIDEO_REFERENCE_ALLOWED_CATEGORIES_BY_PROVIDER.get(provider_type) or {"image"}
            reference_files = _load_reference_files(
                db,
                str(user_id),
                resolved_reference_ids,
                allowed_categories=set(allowed_categories),
            )
            if not reference_files:
                raise ValueError(
                    "No compatible reference files were found in this chat conversation. "
                    "Upload images first, then retry video generation with references."
                )

        generation_result = _generate_video_payload(
            provider,
            model_name,
            prompt_text,
            config,
            reference_files=reference_files,
        )
        cost_info = None
        if isinstance(generation_result, dict):
            raw_cost = generation_result.get("cost")
            raw_cost_details = generation_result.get("cost_details")
            if raw_cost not in (None, "") or isinstance(raw_cost_details, dict):
                cost_value = float(raw_cost or 0.0)
                cost_details = raw_cost_details if isinstance(raw_cost_details, dict) else {}
                if cost_value or cost_details:
                    cost_info = {"cost": cost_value, **cost_details}

        timeout_seconds = int(config.get("timeout_seconds") or 600)
        max_retries = int(config.get("max_retries") or 0)
        video_bytes, file_type, source_url = _resolve_video_bytes(
            generation_result,
            provider,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

        provider_job_id = generation_result.get("provider_job_id")

        original_name = _safe_original_name(filename, file_type)
        stored_file_id = str(uuid.uuid4())
        extension = Path(original_name).suffix.lower()
        if extension not in DEFAULT_VIDEO_MIME_BY_EXT:
            extension = ".mp4"
        stored_file_name = f"{stored_file_id}{extension}"
        resolved_file_type = _mime_for_filename(stored_file_name, fallback=file_type or "video/mp4")
        file_category = get_file_category(resolved_file_type)
        if file_category != "video":
            file_category = "video"
            if not resolved_file_type.startswith("video/"):
                resolved_file_type = "video/mp4"

        meta = {
            "original_filename": original_name,
            "origin": "assistant",
            "video_generation": True,
            "model": model_name,
            "provider_id": provider.id,
            "provider_type": provider.provider,
            "prompt": prompt_text,
            "used_reference_files": bool(reference_files),
        }
        if cost_info:
            meta["cost"] = cost_info.get("cost", 0.0)
            meta["cost_details"] = {
                key: value
                for key, value in cost_info.items()
                if key != "cost" and value not in (None, "", [], {})
            }
        if reference_files:
            meta["reference_file_count"] = len(reference_files)
        if provider_job_id:
            meta["provider_job_id"] = str(provider_job_id)
        if source_url:
            meta["source_url"] = source_url

        file_record = persist_generated_file_bytes(
            db,
            user_id=str(user_id),
            original_filename=original_name,
            file_bytes=video_bytes,
            file_type=resolved_file_type,
            file_category=file_category,
            meta=meta,
            file_id=stored_file_id,
            file_name=stored_file_name,
            quota_reservation_id=(
                quota_reservation.reservation_id if quota_reservation else None
            ),
        )

        return {
            "file_id": file_record.id,
            "provider_job_id": provider_job_id,
            "file_type": resolved_file_type,
            "source_url": source_url,
            "cost_info": cost_info,
        }
    finally:
        release_user_file_quota_reservation(
            db,
            quota_reservation.reservation_id if quota_reservation else None,
        )
        db.close()
