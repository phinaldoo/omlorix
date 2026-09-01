from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import HTTPException
from google.genai import errors as genai_errors
from sqlalchemy.orm import Session

from app.llm.google_aistudio.utils import (
    build_aistudio_generate_content_config,
    get_aistudio_client,
    list_models_google_aistudio,
)
from app.utils.async_cleanup import close_async_resource


logger = logging.getLogger(__name__)


GOOGLE_AISTUDIO_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES = 25 * 1024 * 1024  # 25MB
GOOGLE_AISTUDIO_TRANSCRIPTION_SUPPORTED_FILE_FORMATS = [
    "wav",
    "mp3",
    "mp4",
    "m4a",
    "webm",
    "mpeg",
    "mpga",
    "aiff",
    "aac",
    "ogg",
    "flac",
]

_AUDIO_EXTENSION_TO_MIME_TYPE = {
    "wav": "audio/wav",
    "mp3": "audio/mp3",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
    "webm": "audio/webm",
    "mpeg": "audio/mpeg",
    "mpga": "audio/mpeg",
    "aiff": "audio/aiff",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
}

_DEFAULT_TRANSCRIPTION_PROMPT = "Generate a transcript of the audio. Return only the transcript text."
_TRANSCRIPTION_MODEL_EXCLUDE_TOKENS = (
    "-image",
    "image-preview",
    "-tts",
    "tts-preview",
)


def _is_supported_gemini_transcription_model(model_name: str | None) -> bool:
    normalized = str(model_name or "").strip().lower()
    if not normalized or not normalized.startswith("gemini-"):
        return False
    return not any(token in normalized for token in _TRANSCRIPTION_MODEL_EXCLUDE_TOKENS)


def get_google_aistudio_transcription_models(
    *,
    db: Session,
    aistudio_provider_id: str | None,
) -> list[str]:
    provider_id = str(aistudio_provider_id or "").strip()
    if not provider_id:
        return []

    models = list_models_google_aistudio(
        db,
        aistudio_provider_id=provider_id,
        type="generateContent",
    )

    normalized: list[str] = []
    for item in models or []:
        model_id = str((item or {}).get("id") or "").strip()
        if not _is_supported_gemini_transcription_model(model_id):
            continue
        if model_id not in normalized:
            normalized.append(model_id)
    return normalized


def _get_audio_mime_type(filename: str) -> str:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_type = _AUDIO_EXTENSION_TO_MIME_TYPE.get(extension)
    if not mime_type:
        raise ValueError(
            f"Unsupported audio format '{extension}'. Supported formats: {', '.join(GOOGLE_AISTUDIO_TRANSCRIPTION_SUPPORTED_FILE_FORMATS)}"
        )
    return mime_type


def _build_inline_audio_part(audio_bytes: bytes, mime_type: str) -> dict[str, dict[str, str]]:
    return {
        "inline_data": {
            "mime_type": mime_type,
            "data": base64.b64encode(audio_bytes).decode("ascii"),
        }
    }


def _extract_text_from_response(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    fragments: list[str] = []
    candidates = getattr(response, "candidates", []) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                fragments.append(part_text.strip())

    joined = "\n".join(fragment for fragment in fragments if fragment)
    if joined.strip():
        return joined.strip()

    raise RuntimeError("Google AI Studio transcription response did not include text")


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.mp3",
    *,
    api_key: str,
    model: str,
    api_version: str | None = "v1",
    prompt: str | None = None,
) -> str:
    mime_type = _get_audio_mime_type(filename)
    client = None
    async_client = None
    try:
        client = get_aistudio_client(
            None,
            api_key=api_key,
            api_version=api_version,
        )
        async_client = client.aio
        response = await async_client.models.generate_content(
            model=model,
            contents=[
                str(prompt or _DEFAULT_TRANSCRIPTION_PROMPT),
                _build_inline_audio_part(audio_bytes, mime_type),
            ],
            config=build_aistudio_generate_content_config(),
        )
        return _extract_text_from_response(response)
    except HTTPException as exc:
        detail = str(exc.detail or "Unknown error")
        raise RuntimeError(f"Google AI Studio transcription failed: {detail}") from exc
    except genai_errors.ClientError as exc:
        detail = getattr(exc, "message", str(exc))
        raise RuntimeError(f"Google AI Studio transcription failed: {detail}") from exc
    except Exception as exc:
        logger.exception("Google AI Studio transcription failed")
        raise RuntimeError(f"Google AI Studio transcription failed: {exc}") from exc
    finally:
        if async_client is not None:
            try:
                await close_async_resource(async_client)
            except Exception:
                logger.exception("Could not close Google AI Studio async transport")
        if client is not None:
            try:
                await close_async_resource(client)
            except Exception:
                logger.exception("Could not close Google AI Studio client")
