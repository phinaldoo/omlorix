from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Callable

from app.database import SessionLocal
from app.files.models import Files
from app.files.utils import (
    get_file_category,
    materialize_file_record,
    persist_generated_file_bytes,
    release_user_file_quota_reservation,
    reserve_user_file_quota,
)
from app.llm.google_aistudio.music_generation import (
    generate_music_google_aistudio,
    get_google_aistudio_music_model_capabilities,
)
from app.llm.models import LLMProvider
from app.settings.models import get_settings_page


logger = logging.getLogger(__name__)


MUSIC_GENERATION_PROVIDER_TYPES = {"google_aistudio"}

PROVIDER_GENERATORS: dict[
    str,
    Callable[..., dict[str, Any]],
] = {
    "google_aistudio": generate_music_google_aistudio,
}


def _parse_music_generation_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    from app.admin.settings.schema_categories.music_generation import (
        MusicGenerationSettings,
    )

    validated = MusicGenerationSettings(**payload)
    return validated.model_dump(exclude_none=False)


def _get_music_generation_config(db=None) -> dict[str, Any]:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        base = {
            "provider_id": "",
            "model_name": "",
            "response_format": "mp3",
            "enable_reference_images": False,
            "max_reference_images": 3,
        }
        record = get_settings_page(db, "music_generation")
        if record and isinstance(record.data, dict):
            base.update(record.data)
        return _parse_music_generation_config(base)
    finally:
        if close_db:
            db.close()


def _merge_music_generation_config(
    base_config: dict[str, Any] | None,
    override_config: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(base_config) if isinstance(base_config, dict) else {}
    override = override_config if isinstance(override_config, dict) else {}
    if not override:
        return _parse_music_generation_config(base)

    for key, value in override.items():
        if key in {"provider_id", "model_name"}:
            continue
        if value is not None:
            base[key] = value
    return _parse_music_generation_config(base)


def _resolve_provider(db, provider_id: str) -> LLMProvider | None:
    if not provider_id:
        return None
    return db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()


def get_configured_music_generation_provider_type(db=None) -> str | None:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        config = _get_music_generation_config(db=db)
        provider_id = str(config.get("provider_id") or "").strip()
        if not provider_id:
            return None
        provider = _resolve_provider(db, provider_id)
        if not provider:
            return None
        provider_type = str(provider.provider or "").strip().lower()
        return provider_type or None
    finally:
        if close_db:
            db.close()


def music_generation_supports_reference_images(db=None) -> bool:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        config = _get_music_generation_config(db=db)
        if not bool(config.get("enable_reference_images")):
            return False
        model_name = str(config.get("model_name") or "").strip()
        if not model_name:
            return False
        provider_type = get_configured_music_generation_provider_type(db=db)
        if provider_type != "google_aistudio":
            return False
        capabilities = get_google_aistudio_music_model_capabilities(model_name)
        return bool(capabilities.get("supports_reference_images"))
    finally:
        if close_db:
            db.close()


def get_music_reference_tool_params(db=None) -> dict[str, Any]:
    if not music_generation_supports_reference_images(db=db):
        return {}
    return {
        "use_reference_images": {
            "type": "boolean",
            "description": (
                "When true, recent chat images are passed to music generation as visual inspiration."
            ),
        }
    }


def _safe_original_name(file_name: str | None, extension: str) -> str:
    ext = str(extension or "").strip().lower().lstrip(".") or "mp3"
    base = Path(str(file_name).strip()).name if file_name else "generated_music"
    if not base:
        base = "generated_music"
    current_ext = Path(base).suffix.lower().lstrip(".")
    if not current_ext:
        return f"{base}.{ext}"
    if current_ext != ext:
        return f"{Path(base).stem or 'generated_music'}.{ext}"
    return base


def _guess_file_type(file_name: str, fallback: str) -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    if guessed and guessed.startswith("audio/"):
        return guessed
    return fallback if str(fallback).startswith("audio/") else "audio/mpeg"


def _extract_attachment_ids(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        stripped = raw.strip()
        return [stripped] if stripped else []
    if isinstance(raw, dict):
        value = raw.get("id") or raw.get("file_id")
        return [str(value)] if value else []
    if isinstance(raw, list):
        values: list[str] = []
        for item in raw:
            values.extend(_extract_attachment_ids(item))
        return values
    return []


def _collect_reference_image_ids(chat_history: list | None) -> list[str]:
    if not isinstance(chat_history, list):
        return []
    collected: list[str] = []
    for message in chat_history:
        payload = message if isinstance(message, dict) else None
        if payload is None:
            payload = {
                "images": getattr(message, "images", None),
                "content": getattr(message, "content", None),
            }
        collected.extend(_extract_attachment_ids(payload.get("images")))
        content = payload.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                collected.extend(_extract_attachment_ids(block.get("images")))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in collected:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _load_reference_images(
    db,
    *,
    user_id: str,
    file_ids: list[str],
    max_count: int,
) -> list[dict[str, Any]]:
    if not file_ids:
        return []
    rows = (
        db.query(Files)
        .filter(Files.user_id == str(user_id), Files.id.in_(file_ids))
        .all()
    )
    row_map = {str(row.id): row for row in rows if row and row.id}
    loaded: list[dict[str, Any]] = []
    for file_id in file_ids:
        if len(loaded) >= max_count:
            break
        row = row_map.get(str(file_id))
        if not row:
            continue
        mime_type = str(row.file_type or "").strip().lower()
        file_category = str(row.file_category or "").strip().lower()
        if file_category != "image" and not mime_type.startswith("image/"):
            continue
        try:
            file_path = materialize_file_record(row, str(row.user_id))
            image_bytes = file_path.read_bytes()
        except Exception:
            logger.warning("Failed to load music reference image %s", file_id, exc_info=True)
            continue
        if not image_bytes:
            continue
        loaded.append(
            {
                "file_id": str(file_id),
                "bytes": image_bytes,
                "mime_type": mime_type or "image/png",
            }
        )
    return loaded


def _compose_music_prompt(description: str, lyrics: str | None = None) -> str:
    prompt = str(description or "").strip()
    lyrics_text = str(lyrics or "").strip()
    if not lyrics_text:
        return prompt
    return (
        f"{prompt}\n\n"
        "Use the following lyrics as the vocal content when appropriate. "
        "Preserve section labels such as [Verse], [Chorus], and [Bridge] if present.\n\n"
        f"{lyrics_text}"
    ).strip()


def music_generation(
    *,
    description: str,
    user_id: str,
    lyrics: str | None = None,
    filename: str | None = None,
    config_override: dict[str, Any] | None = None,
    use_reference_images: bool = False,
    chat_history: list | None = None,
) -> dict[str, Any]:
    if not user_id:
        raise ValueError("user_id is required for music generation")
    description_text = str(description or "").strip()
    if not description_text:
        raise ValueError("description is required for music generation")

    db = SessionLocal()
    quota_reservation = None
    try:
        quota_reservation = reserve_user_file_quota(
            db,
            user_id=str(user_id),
            purpose="music_generation",
        )
        config = _merge_music_generation_config(
            _get_music_generation_config(db=db),
            config_override,
        )
        provider_id = str(config.get("provider_id") or "").strip()
        model_name = str(config.get("model_name") or "").strip()
        if not provider_id:
            raise RuntimeError("Music generation provider is not configured in admin settings.")
        if not model_name:
            raise RuntimeError("Music generation model is not configured in admin settings.")

        provider = _resolve_provider(db, provider_id)
        if not provider:
            raise RuntimeError("Configured music generation provider no longer exists.")
        if not provider.api_key:
            raise RuntimeError("Music generation provider API key is not configured.")

        provider_type = str(provider.provider or "").strip().lower()
        generator = PROVIDER_GENERATORS.get(provider_type)
        if not generator:
            raise RuntimeError(f"Unsupported music generation provider type: {provider_type}")

        reference_images: list[dict[str, Any]] = []
        if use_reference_images and bool(config.get("enable_reference_images")):
            max_reference_images = int(config.get("max_reference_images") or 3)
            max_reference_images = max(1, min(max_reference_images, 10))
            reference_ids = _collect_reference_image_ids(chat_history)
            reference_images = _load_reference_images(
                db,
                user_id=str(user_id),
                file_ids=reference_ids,
                max_count=max_reference_images,
            )

        generation = generator(
            provider,
            model_name,
            _compose_music_prompt(description_text, lyrics),
            config=config,
            reference_images=reference_images or None,
        )
        cost_info = None
        if isinstance(generation, dict):
            raw_cost = generation.get("cost")
            raw_cost_details = generation.get("cost_details")
            if raw_cost not in (None, "") or isinstance(raw_cost_details, dict):
                cost_value = float(raw_cost or 0.0)
                cost_details = raw_cost_details if isinstance(raw_cost_details, dict) else {}
                if cost_value or cost_details:
                    cost_info = {"cost": cost_value, **cost_details}

        audio_bytes = generation.get("audio_bytes", b"")
        if not isinstance(audio_bytes, (bytes, bytearray)):
            raise RuntimeError("Music generation returned an invalid audio payload")
        audio_bytes = bytes(audio_bytes)
        if not audio_bytes:
            raise RuntimeError("Music generation returned an empty audio payload")

        extension = str(generation.get("extension") or config.get("response_format") or "mp3").strip().lower().lstrip(".")
        original_name = _safe_original_name(filename, extension)
        stored_file_id = str(uuid.uuid4())
        stored_file_name = f"{stored_file_id}.{extension or 'mp3'}"
        resolved_file_type = _guess_file_type(
            stored_file_name,
            str(generation.get("file_type") or "audio/mpeg").strip().lower(),
        )
        file_meta = {
            "origin": "assistant",
            "music_generation": True,
            "prompt": description_text,
            "custom_lyrics": str(lyrics or "").strip() or None,
            "text_content": generation.get("text_content") or "",
            "text_blocks": generation.get("text_blocks") or [],
            "model": model_name,
            "provider_id": provider.id,
            "provider_type": provider.provider,
            "response_format": generation.get("response_format") or config.get("response_format") or "mp3",
            "reference_images_enabled": bool(use_reference_images and config.get("enable_reference_images")),
            "reference_image_count": int(generation.get("reference_image_count") or len(reference_images)),
            "original_filename": original_name,
        }
        if cost_info:
            file_meta["cost"] = cost_info.get("cost", 0.0)
            file_meta["cost_details"] = {
                key: value
                for key, value in cost_info.items()
                if key != "cost" and value not in (None, "", [], {})
            }

        file_record = persist_generated_file_bytes(
            db,
            user_id=str(user_id),
            original_filename=original_name,
            file_bytes=audio_bytes,
            file_type=resolved_file_type,
            file_category=get_file_category(resolved_file_type) or "audio",
            meta=file_meta,
            file_id=stored_file_id,
            file_name=stored_file_name,
            quota_reservation_id=(
                quota_reservation.reservation_id if quota_reservation else None
            ),
        )

        return {
            "file_id": file_record.id,
            "file_type": resolved_file_type,
            "model": model_name,
            "response_format": generation.get("response_format") or config.get("response_format") or "mp3",
            "text_content": generation.get("text_content") or "",
            "text_blocks": generation.get("text_blocks") or [],
            "cost_info": cost_info,
        }
    finally:
        release_user_file_quota_reservation(
            db,
            quota_reservation.reservation_id if quota_reservation else None,
        )
        db.close()
