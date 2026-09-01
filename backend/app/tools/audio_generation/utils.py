from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.files.utils import (
    get_file_category,
    persist_generated_file_bytes,
    release_user_file_quota_reservation,
    reserve_user_file_quota,
)
from app.llm.models import LLMProvider
from app.llm.speech import (
    PROVIDER_AUDIO_GENERATORS,
    provider_supports_tts_instructions,
)
from app.settings.models import get_settings_page


logger = logging.getLogger(__name__)


def _parse_audio_generation_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    from app.admin.settings.schema_categories.audio_generation import (
        AudioGenerationSettings,
    )

    validated = AudioGenerationSettings(**payload)
    return validated.model_dump(exclude_none=False)


def _get_audio_generation_config(db=None) -> dict[str, Any]:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        base: dict[str, Any] = {
            "provider_id": "",
            "model_name": "",
            "voice": "",
            "response_format": "mp3",
            "language": None,
            "sample_rate": None,
            "bit_rate": None,
            "speed": None,
            "optimize_streaming_latency": None,
            "text_normalization": None,
        }

        record = get_settings_page(db, "audio_generation")
        if record and isinstance(record.data, dict):
            base.update(record.data)
        else:
            # Backward compatibility for legacy typo key in old defaults.
            legacy_record = get_settings_page(db, "audio__generation")
            if legacy_record and isinstance(legacy_record.data, dict):
                base.update(legacy_record.data)

        return _parse_audio_generation_config(base)
    finally:
        if close_db:
            db.close()


def _merge_audio_generation_config(
    base_config: dict[str, Any] | None,
    override_config: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(base_config) if isinstance(base_config, dict) else {}
    override = override_config if isinstance(override_config, dict) else {}
    if not override:
        return _parse_audio_generation_config(base)

    # Intentionally ignore provider/model overrides. Chat model settings only
    # override generation parameters for the configured tool model.
    for key, value in override.items():
        if key in {"provider_id", "model_name"}:
            continue
        if value is not None:
            base[key] = value

    return _parse_audio_generation_config(base)


def _resolve_provider(db, provider_id: str) -> LLMProvider | None:
    if not provider_id:
        return None
    return db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
PROVIDER_GENERATORS = PROVIDER_AUDIO_GENERATORS


def get_configured_audio_generation_provider_type(db=None) -> str | None:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        config = _get_audio_generation_config(db=db)
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


def audio_generation_supports_instructions(db=None) -> bool:
    provider_type = get_configured_audio_generation_provider_type(db=db)
    return provider_supports_tts_instructions(provider_type)


def audio_generation_supports_multi_speakers(db=None) -> bool:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        config = _get_audio_generation_config(db=db)
        model_name = str(config.get("model_name") or "").strip()
        if not model_name:
            return False
        provider_type = get_configured_audio_generation_provider_type(db=db)
        return provider_type == "google_aistudio"
    finally:
        if close_db:
            db.close()


def _safe_original_name(file_name: str | None, extension: str) -> str:
    ext = str(extension or "").strip().lower().lstrip(".")
    if not ext:
        ext = "mp3"

    base = Path(str(file_name).strip()).name if file_name else "generated_audio"
    if not base:
        base = "generated_audio"

    current_ext = Path(base).suffix.lower().lstrip(".")
    if not current_ext:
        base = f"{base}.{ext}"
    elif current_ext != ext:
        base = f"{Path(base).stem or 'generated_audio'}.{ext}"

    return base


def _guess_file_type(file_name: str, fallback: str) -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    if guessed and guessed.startswith("audio/"):
        return guessed
    return fallback if fallback.startswith("audio/") else "audio/mpeg"


def audio_generation(
    input: str,
    user_id: str,
    instructions: str | None = None,
    multiple_speakers: bool = False,
    filename: str | None = None,
    config_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not user_id:
        raise ValueError("user_id is required for audio generation")

    input_text = str(input or "").strip()
    if not input_text:
        raise ValueError("input is required for audio generation")

    db = SessionLocal()
    quota_reservation = None
    try:
        quota_reservation = reserve_user_file_quota(
            db,
            user_id=str(user_id),
            purpose="audio_generation",
        )
        config = _merge_audio_generation_config(
            _get_audio_generation_config(db=db),
            config_override,
        )
        provider_id = str(config.get("provider_id") or "").strip()
        model_name = str(config.get("model_name") or "").strip()

        if not provider_id:
            raise RuntimeError("Audio generation provider is not configured in admin settings.")
        if not model_name:
            raise RuntimeError("Audio generation model is not configured in admin settings.")

        provider = _resolve_provider(db, provider_id)
        if not provider:
            raise RuntimeError("Configured audio generation provider no longer exists.")
        if not provider.api_key:
            raise RuntimeError("Audio generation provider API key is not configured.")

        provider_type = str(provider.provider or "").strip().lower()
        generator = PROVIDER_GENERATORS.get(provider_type)
        if not generator:
            raise ValueError(f"Unsupported audio generation provider type: {provider_type}")

        generation = generator(provider, model_name, input_text, instructions, bool(multiple_speakers), config)
        cost_info = None
        if isinstance(generation, dict):
            raw_cost = generation.get("cost")
            raw_cost_details = generation.get("cost_details")
            if raw_cost not in (None, "") or isinstance(raw_cost_details, dict):
                cost_details = raw_cost_details if isinstance(raw_cost_details, dict) else {}
                cost_info = dict(cost_details)
                if raw_cost not in (None, ""):
                    cost_info["cost"] = float(raw_cost or 0.0)
                if not cost_info:
                    cost_info = None
        audio_bytes = generation.get("audio_bytes", b"")
        if not isinstance(audio_bytes, (bytes, bytearray)):
            raise RuntimeError("Audio generation returned an invalid byte payload")
        audio_bytes = bytes(audio_bytes)
        if not audio_bytes:
            raise RuntimeError("Audio generation returned an empty audio payload")

        extension = str(generation.get("extension") or config.get("response_format") or "mp3").strip().lower().lstrip(".")
        file_type_fallback = str(generation.get("file_type") or "audio/mpeg").strip().lower()

        original_name = _safe_original_name(filename, extension)
        stored_file_id = str(uuid.uuid4())
        stored_extension = Path(original_name).suffix.lower().lstrip(".") or extension or "mp3"
        stored_file_name = f"{stored_file_id}.{stored_extension}"

        file_size = len(audio_bytes)
        resolved_file_type = _guess_file_type(stored_file_name, file_type_fallback)
        file_category = get_file_category(resolved_file_type)
        if file_category != "audio":
            file_category = "audio"
            if not resolved_file_type.startswith("audio/"):
                resolved_file_type = "audio/mpeg"

        meta = {
            "original_filename": original_name,
            "origin": "assistant",
            "audio_generation": True,
            "model": model_name,
            "provider_id": provider.id,
            "provider_type": provider.provider,
            "voice": generation.get("voice") or config.get("voice") or "",
            "response_format": generation.get("response_format") or config.get("response_format") or "",
            "language": generation.get("language") or config.get("language") or None,
            "sample_rate": generation.get("sample_rate") or config.get("sample_rate") or None,
            "bit_rate": (
                generation["bit_rate"]
                if "bit_rate" in generation
                else config.get("bit_rate")
            ),
            "speed": generation.get("speed") if generation.get("speed") is not None else config.get("speed"),
            "optimize_streaming_latency": (
                generation.get("optimize_streaming_latency")
                if generation.get("optimize_streaming_latency") is not None
                else config.get("optimize_streaming_latency")
            ),
            "text_normalization": (
                generation.get("text_normalization")
                if generation.get("text_normalization") is not None
                else config.get("text_normalization")
            ),
            "multiple_speakers": bool(multiple_speakers),
            "speakers": generation.get("speakers") or [],
        }
        if cost_info:
            if "cost" in cost_info:
                meta["cost"] = cost_info.get("cost", 0.0)
            cost_details = {
                key: value
                for key, value in cost_info.items()
                if key != "cost" and value not in (None, "", [], {})
            }
            if cost_details:
                meta["cost_details"] = cost_details

        file_record = persist_generated_file_bytes(
            db,
            user_id=str(user_id),
            original_filename=original_name,
            file_bytes=audio_bytes,
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
            "file_type": resolved_file_type,
            "voice": meta.get("voice") or None,
            "response_format": meta.get("response_format") or None,
            "model": model_name,
            "cost_info": cost_info,
        }
    finally:
        release_user_file_quota_reservation(
            db,
            quota_reservation.reservation_id if quota_reservation else None,
        )
        db.close()
