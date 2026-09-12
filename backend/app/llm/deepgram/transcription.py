from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import Any

import requests

from app.llm.deepgram.common import (
    DEEPGRAM_BASE_URL,
    DEEPGRAM_DEFAULT_TIMEOUT_SECONDS,
    DEEPGRAM_PRIVACY_OPT_OUT_QUERY_PARAM,
    DEEPGRAM_PRIVACY_OPT_OUT_QUERY_VALUE,
    deepgram_auth_headers,
    extract_deepgram_error_detail,
    list_deepgram_models_payload,
    normalize_deepgram_timeout,
)


logger = logging.getLogger(__name__)


DEEPGRAM_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
DEEPGRAM_TRANSCRIPTION_SUPPORTED_FILE_FORMATS = [
    "aac",
    "aiff",
    "amr",
    "flac",
    "m4a",
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "ogg",
    "opus",
    "wav",
    "webm",
    "wma",
]
DEEPGRAM_TRANSCRIPTION_FALLBACK_MODELS = [
    "nova-3",
    "nova-2",
]


def _normalize_deepgram_stt_model_entry(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    for key in ("canonical_name", "name", "model", "id"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return None


def get_deepgram_transcription_models(
    *,
    api_key: str | None = None,
    timeout: int = DEEPGRAM_DEFAULT_TIMEOUT_SECONDS,
) -> list[str]:
    if not str(api_key or "").strip():
        return list(DEEPGRAM_TRANSCRIPTION_FALLBACK_MODELS)

    try:
        payload = list_deepgram_models_payload(api_key, timeout=timeout)
    except Exception:
        logger.warning("Failed to fetch Deepgram transcription models; using fallback list.", exc_info=True)
        return list(DEEPGRAM_TRANSCRIPTION_FALLBACK_MODELS)

    raw_models = payload.get("stt")
    if not isinstance(raw_models, list):
        return list(DEEPGRAM_TRANSCRIPTION_FALLBACK_MODELS)

    normalized: list[str] = []
    for entry in raw_models:
        model_name = _normalize_deepgram_stt_model_entry(entry)
        if model_name and model_name not in normalized:
            normalized.append(model_name)

    return normalized or list(DEEPGRAM_TRANSCRIPTION_FALLBACK_MODELS)


def _get_audio_mime_type(filename: str) -> str:
    mime_type, _ = mimetypes.guess_type(filename or "")
    if isinstance(mime_type, str) and mime_type.strip():
        return mime_type
    return "application/octet-stream"


def _extract_text_from_response(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise RuntimeError("Deepgram transcription response was not a JSON object")

    results = payload.get("results")
    if not isinstance(results, dict):
        raise RuntimeError("Deepgram transcription response did not include results")

    channels = results.get("channels")
    if not isinstance(channels, list):
        raise RuntimeError("Deepgram transcription response did not include channels")

    fragments: list[str] = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        alternatives = channel.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            continue
        alternative = alternatives[0]
        if not isinstance(alternative, dict):
            continue
        transcript = str(alternative.get("transcript") or "").strip()
        if transcript:
            fragments.append(transcript)

    text = "\n".join(fragment for fragment in fragments if fragment).strip()
    if text:
        return text
    raise RuntimeError("Deepgram transcription response did not include transcript text")


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.mp3",
    *,
    api_key: str,
    model: str,
    timeout: int = DEEPGRAM_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    model_name = str(model or "").strip() or DEEPGRAM_TRANSCRIPTION_FALLBACK_MODELS[0]
    mime_type = _get_audio_mime_type(filename)
    endpoint = f"{DEEPGRAM_BASE_URL}/v1/listen"

    def _transcribe() -> str:
        try:
            response = requests.post(
                endpoint,
                headers=deepgram_auth_headers(
                    api_key,
                    accept="application/json",
                    content_type=mime_type,
                ),
                params={
                    "model": model_name,
                    "smart_format": "true",
                    "punctuate": "true",
                    DEEPGRAM_PRIVACY_OPT_OUT_QUERY_PARAM: DEEPGRAM_PRIVACY_OPT_OUT_QUERY_VALUE,
                },
                data=audio_bytes,
                timeout=normalize_deepgram_timeout(timeout),
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to reach Deepgram API: {exc}") from exc

        if not response.ok:
            detail = extract_deepgram_error_detail(response)
            raise RuntimeError(f"Deepgram transcription failed: {detail}")

        return _extract_text_from_response(response.json())

    return await asyncio.to_thread(_transcribe)
