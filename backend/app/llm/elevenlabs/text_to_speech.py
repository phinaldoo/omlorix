from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import requests

from app.llm.audio_generation_pricing import (
    build_audio_generation_model_option,
    calculate_audio_generation_cost,
)
from app.llm.models import LLMProvider
from app.utils.schemas import FieldSchema, Option, Section, Sections


logger = logging.getLogger(__name__)


ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"
ELEVENLABS_DEFAULT_AUDIO_FORMAT = "mp3"
ELEVENLABS_OUTPUT_FORMAT = "mp3_44100_128"
ELEVENLABS_DEFAULT_PAGE_SIZE = 24
ELEVENLABS_MAX_PAGE_SIZE = 100


def _clamp_page_size(page_size: int | None) -> int:
    if not isinstance(page_size, int):
        return ELEVENLABS_DEFAULT_PAGE_SIZE
    if page_size < 1:
        return 1
    if page_size > ELEVENLABS_MAX_PAGE_SIZE:
        return ELEVENLABS_MAX_PAGE_SIZE
    return page_size


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _extract_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text.strip() or f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return response.text.strip() or f"HTTP {response.status_code}"


def _normalize_voice_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    voice_id = str(entry.get("voice_id") or entry.get("id") or "").strip()
    if not voice_id:
        return None
    name = str(entry.get("name") or voice_id).strip() or voice_id
    preview_url = str(entry.get("preview_url") or "").strip() or None
    description = str(entry.get("description") or "").strip() or None
    category = str(entry.get("category") or "").strip() or None
    labels_raw = entry.get("labels")
    labels = labels_raw if isinstance(labels_raw, dict) else {}
    return {
        "id": voice_id,
        "name": name,
        "preview_url": preview_url,
        "description": description,
        "category": category,
        "labels": labels,
    }


def elevenlabs_text_to_speech_models_list(
    api_key: str,
    *,
    base_url: str | None = None,
    timeout: int = 20,
) -> list[dict[str, Any]]:
    token = str(api_key or "").strip()
    if not token:
        raise ValueError("ElevenLabs api_key is required")

    resolved_base_url = (base_url or ELEVENLABS_BASE_URL).rstrip("/")
    endpoint = f"{resolved_base_url}/v1/models"

    try:
        response = requests.get(
            endpoint,
            headers={"xi-api-key": token},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to reach ElevenLabs API: {exc}") from exc

    if not response.ok:
        detail = _extract_error_detail(response)
        raise RuntimeError(f"Failed to list ElevenLabs models: {detail}")

    payload = response.json()
    raw_models = payload.get("models", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        return []

    normalized: list[dict[str, Any]] = []
    for model in raw_models:
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("model_id") or model.get("id") or "").strip()
        name = str(model.get("name") or model_id).strip()
        if not model_id:
            continue

        can_do_tts = model.get("can_do_text_to_speech")
        if isinstance(can_do_tts, bool) and not can_do_tts:
            continue

        normalized.append(
            {
                "id": model_id,
                "name": name or model_id,
                "description": str(model.get("description") or "").strip() or None,
                "voices": [],
                "response_formats": [ELEVENLABS_DEFAULT_AUDIO_FORMAT],
                "support_custom_instructions": False,
            }
        )

    return normalized


def search_elevenlabs_voices(
    api_key: str,
    *,
    search: str | None = None,
    next_page_token: str | None = None,
    page_size: int = ELEVENLABS_DEFAULT_PAGE_SIZE,
    base_url: str | None = None,
    timeout: int = 20,
    voice_ids: list[str] | None = None,
) -> dict[str, Any]:
    token = str(api_key or "").strip()
    if not token:
        raise ValueError("ElevenLabs api_key is required")

    resolved_base_url = (base_url or ELEVENLABS_BASE_URL).rstrip("/")
    params: dict[str, Any] = {
        "page_size": _clamp_page_size(page_size),
        "include_total_count": "false",
        "sort": "name",
        "sort_direction": "asc",
    }
    search_text = str(search or "").strip()
    if search_text:
        params["search"] = search_text

    token_value = str(next_page_token or "").strip()
    if token_value:
        params["next_page_token"] = token_value

    requested_voice_ids: list[str] = []
    for voice_id in voice_ids or []:
        value = str(voice_id or "").strip()
        if value:
            requested_voice_ids.append(value)
    if requested_voice_ids:
        params["voice_ids"] = requested_voice_ids[:100]

    headers = {"xi-api-key": token}
    payload: dict[str, Any] | None = None

    for endpoint_path in ("/v2/voices", "/v1/voices/search"):
        endpoint = f"{resolved_base_url}{endpoint_path}"
        try:
            response = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to reach ElevenLabs API: {exc}") from exc

        if response.status_code == 404:
            continue
        if not response.ok:
            detail = _extract_error_detail(response)
            raise RuntimeError(f"Failed to list ElevenLabs voices: {detail}")

        data = response.json()
        if isinstance(data, dict):
            payload = data
        elif isinstance(data, list):
            payload = {"voices": data, "has_more": False, "next_page_token": None}
        else:
            payload = {"voices": [], "has_more": False, "next_page_token": None}
        break

    if payload is None:
        raise RuntimeError("Failed to list ElevenLabs voices: unsupported endpoint.")

    raw_voices = payload.get("voices")
    if not isinstance(raw_voices, list):
        raw_voices = []

    voices: list[dict[str, Any]] = []
    for entry in raw_voices:
        if not isinstance(entry, dict):
            continue
        normalized = _normalize_voice_entry(entry)
        if normalized:
            voices.append(normalized)

    if requested_voice_ids:
        by_id = {voice["id"]: voice for voice in voices}
        ordered: list[dict[str, Any]] = []
        for voice_id in requested_voice_ids:
            if voice_id in by_id:
                ordered.append(by_id[voice_id])
        for voice in voices:
            if voice not in ordered:
                ordered.append(voice)
        voices = ordered

    has_more = bool(payload.get("has_more"))
    next_token = str(payload.get("next_page_token") or "").strip() or None

    return {
        "voices": voices,
        "has_more": has_more,
        "next_page_token": next_token,
    }


def normalize_elevenlabs_voice(voice: str | None) -> str:
    return str(voice or "").strip()


def elevenlabs_generate_audio(
    *,
    api_key: str,
    model: str,
    voice: str,
    input_text: str,
    enable_logging: bool = True,
    timeout: int = 30,
    base_url: str | None = None,
) -> dict[str, Any]:
    token = str(api_key or "").strip()
    if not token:
        raise ValueError("api_key is required for ElevenLabs text-to-speech")

    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("model is required for ElevenLabs text-to-speech")

    voice_id = normalize_elevenlabs_voice(voice)
    if not voice_id:
        raise ValueError("voice is required for ElevenLabs text-to-speech")

    text_value = str(input_text or "").strip()
    if not text_value:
        raise ValueError("input text is required for ElevenLabs text-to-speech")

    resolved_base_url = (base_url or ELEVENLABS_BASE_URL).rstrip("/")
    endpoint = f"{resolved_base_url}/v1/text-to-speech/{quote(voice_id, safe='')}"

    request_timeout = max(1, int(timeout))
    params = {"enable_logging": str(bool(enable_logging)).lower()}
    payload = {
        "text": text_value,
        "model_id": model_name,
        "output_format": ELEVENLABS_OUTPUT_FORMAT,
    }

    try:
        response = requests.post(
            endpoint,
            headers={
                "xi-api-key": token,
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
            params=params,
            json=payload,
            timeout=request_timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to reach ElevenLabs API: {exc}") from exc

    if not response.ok:
        detail = _extract_error_detail(response)
        raise RuntimeError(f"ElevenLabs text-to-speech failed: {detail}")

    audio_bytes = response.content
    if not audio_bytes:
        raise RuntimeError("ElevenLabs text-to-speech returned an empty audio payload")

    cost_details = calculate_audio_generation_cost(
        "elevenlabs",
        model_name,
        input_text=text_value,
    )

    return {
        "audio_bytes": audio_bytes,
        "model": model_name,
        "voice": voice_id,
        "response_format": ELEVENLABS_DEFAULT_AUDIO_FORMAT,
        "file_type": "audio/mpeg",
        "extension": ELEVENLABS_DEFAULT_AUDIO_FORMAT,
        "cost": cost_details.get("cost") if isinstance(cost_details, dict) else None,
        "cost_details": cost_details,
    }


def get_audio_generation_schema_part_1(db, provider_id: str):
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []

    if provider:
        try:
            for item in elevenlabs_text_to_speech_models_list(provider.api_key):
                model_id = str(item.get("id") or "").strip()
                label = str(item.get("name") or model_id).strip() or model_id
                if model_id:
                    option_label, metadata = build_audio_generation_model_option(
                        "elevenlabs",
                        model_id,
                        label=label,
                    )
                    model_options.append(Option(value=model_id, label=option_label, metadata=metadata))
        except Exception:
            logger.exception(
                "Failed to fetch ElevenLabs TTS models for provider '%s'",
                provider_id,
            )

    return Sections(
        sections=[
            Section(
                title="ElevenLabs Audio Generation",
                i18n_title="llm.shared.section_elevenlabs_audio.title",
                description="Select the ElevenLabs text-to-speech model.",
                i18n_description="llm.shared.section_select_the_elevenlabs.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        description="Choose which ElevenLabs text-to-speech model to use.",
                        i18n_label="schema_audio_generation_model_name",
                        i18n_description="schema_audio_generation_model_name_desc",
                        type="select",
                        options=model_options,
                        placeholder="Select a model",
                        i18n_placeholder="llm.shared.model_name.placeholder",
                    ),
                ],
            )
        ]
    )


def get_audio_generation_schema_part_2(
    *,
    api_key: str,
    model_name: str,
):
    model_value = str(model_name or "").strip()
    voice_options: list[Option] = []
    default_voice: str | None = None

    try:
        voices_payload = search_elevenlabs_voices(
            api_key=api_key,
            search=None,
            page_size=ELEVENLABS_DEFAULT_PAGE_SIZE,
        )
        for voice in voices_payload.get("voices", []):
            voice_id = str(voice.get("id") or "").strip()
            if not voice_id:
                continue
            voice_name = str(voice.get("name") or voice_id).strip() or voice_id
            labels = voice.get("labels") if isinstance(voice.get("labels"), dict) else {}
            description = voice.get("description")
            category = voice.get("category")
            voice_options.append(
                Option(
                    value=voice_id,
                    label=voice_name,
                    metadata={
                        "preview_url": voice.get("preview_url"),
                        "description": description,
                        "category": category,
                        "labels": labels,
                    },
                )
            )
            if default_voice is None:
                default_voice = voice_id
    except Exception:
        logger.exception("Failed to fetch initial ElevenLabs voices for model settings")

    return Sections(
        sections=[
            Section(
                title="ElevenLabs Audio Generation",
                i18n_title="llm.shared.section_elevenlabs_audio.title",
                description=f"Select a voice for model '{model_value}'.",
                fields=[
                    FieldSchema(
                        key="voice",
                        label="Voice",
                        description=(
                            "Select a default ElevenLabs voice. Use search and preview in the admin UI "
                            "to find the best voice."
                        ),
                        i18n_label="schema_audio_generation_voice",
                        i18n_description="schema_audio_generation_voice_desc",
                        type="select",
                        options=voice_options,
                        default=default_voice,
                        placeholder="Search and select a voice",
                        i18n_placeholder="llm.shared.voice.placeholder",
                        required=True,
                    ),
                ],
            )
        ]
    )
