from __future__ import annotations

import logging
from typing import Any

import requests

from app.llm.models import LLMProvider
from app.llm.openrouter.common import build_openrouter_api_url, build_openrouter_headers
from app.utils.schemas import FieldSchema, Option, Section, Sections


logger = logging.getLogger(__name__)


OPENROUTER_TTS_DEFAULT_VOICE = ""
OPENROUTER_TTS_RESPONSE_FORMATS = ["mp3", "pcm"]
OPENROUTER_TTS_DEFAULT_FORMAT = "mp3"
# Voice identifiers are provider-specific. Advertising OpenAI's built-in voice
# names for every routed TTS model makes non-OpenAI models fail validation.
OPENROUTER_TTS_COMMON_VOICES: list[str] = []
_OPENROUTER_AUDIO_MIME_TYPES = {
    "mp3": "audio/mpeg",
    "pcm": "audio/pcm",
}
_OPENROUTER_AUDIO_EXTENSIONS = {
    "pcm": "pcm",
}


def _normalize_output_modalities(item: dict[str, Any]) -> set[str]:
    architecture = item.get("architecture")
    if not isinstance(architecture, dict):
        return set()

    raw_values = architecture.get("output_modalities")
    if not isinstance(raw_values, list):
        return set()

    normalized: set[str] = set()
    for raw_value in raw_values:
        value = str(raw_value or "").strip().lower()
        if value:
            normalized.add(value)
    return normalized


def _make_openrouter_headers(provider: LLMProvider, *, include_content_type: bool = True) -> dict[str, str]:
    """Build headers shared by OpenRouter speech discovery and generation."""
    provider_settings = provider.settings if isinstance(provider.settings, dict) else None
    return build_openrouter_headers(
        provider.api_key,
        provider_settings,
        include_content_type=include_content_type,
    )


def _normalize_voice(voice: str | None) -> str:
    requested = str(voice or "").strip()
    if not requested:
        raise ValueError(
            "voice is required for OpenRouter text-to-speech; configure a provider-specific voice identifier"
        )
    return requested


def _normalize_response_format(response_format: str | None) -> str:
    requested = str(response_format or "").strip().lower()
    if requested == "pcm16":
        requested = "pcm"
    if requested in OPENROUTER_TTS_RESPONSE_FORMATS:
        return requested
    return OPENROUTER_TTS_DEFAULT_FORMAT


def _build_stream_error(response: requests.Response) -> RuntimeError:
    message = response.text.strip()
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or message)
        elif error:
            message = str(error)
        elif payload.get("message"):
            message = str(payload.get("message"))

    return RuntimeError(
        f"OpenRouter text-to-speech failed ({response.status_code}): {message or 'Unknown error'}"
    )


def _build_usage_cost_details(
    usage: dict[str, Any] | None,
    *,
    model_name: str,
    voice: str,
    response_format: str,
) -> tuple[float | None, dict[str, Any]]:
    if not isinstance(usage, dict):
        return None, {
            "model": model_name,
            "voice": voice,
            "response_format": response_format,
        }

    cost: float | None = None
    raw_cost = usage.get("cost")
    try:
        if raw_cost not in (None, ""):
            cost = float(raw_cost)
    except (TypeError, ValueError):
        cost = None

    def _maybe_int(value: Any) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    details = {
        "model": model_name,
        "voice": voice,
        "response_format": response_format,
        "provider": usage.get("provider"),
        "is_byok": usage.get("is_byok"),
        "prompt_tokens": _maybe_int(usage.get("prompt_tokens")),
        "completion_tokens": _maybe_int(usage.get("completion_tokens")),
        "total_tokens": _maybe_int(usage.get("total_tokens")),
        "prompt_audio_tokens": _maybe_int(prompt_details.get("audio_tokens")),
        "completion_audio_tokens": _maybe_int(completion_details.get("audio_tokens")),
    }
    return cost, {key: value for key, value in details.items() if value not in (None, "", [])}


def openrouter_text_to_speech_models_list(provider: LLMProvider) -> list[dict[str, Any]]:
    headers = _make_openrouter_headers(provider, include_content_type=False)
    url = build_openrouter_api_url("/models/user", provider.settings if isinstance(provider.settings, dict) else None)
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()

    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []

    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id == "openrouter/auto":
            continue
        output_modalities = _normalize_output_modalities(item)
        if "speech" not in output_modalities:
            continue

        supports_openai_instructions = model_id.lower().startswith("openai/")

        models.append(
            {
                "id": model_id,
                "name": str(item.get("name") or model_id).strip() or model_id,
                "voices": list(OPENROUTER_TTS_COMMON_VOICES),
                "response_formats": list(OPENROUTER_TTS_RESPONSE_FORMATS),
                "support_custom_instructions": supports_openai_instructions,
                "voice_required": True,
                "supports_custom_voice": True,
            }
        )

    return models


def openrouter_generate_audio(
    *,
    provider: LLMProvider,
    model: str,
    voice: str | None,
    input_text: str,
    instructions: str | None,
    response_format: str | None,
) -> dict[str, Any]:
    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("model is required for OpenRouter text-to-speech")

    normalized_voice = _normalize_voice(voice)
    normalized_format = _normalize_response_format(response_format)

    normalized_input = str(input_text or "").strip()
    if not normalized_input:
        raise ValueError("input text is required for OpenRouter text-to-speech")
    normalized_instructions = str(instructions or "").strip()

    payload = {
        "model": model_name,
        "input": normalized_input,
        "response_format": normalized_format,
    }
    payload["voice"] = normalized_voice
    if normalized_instructions and model_name.lower().startswith("openai/"):
        # OpenRouter exposes OpenAI's tone controls through provider-specific
        # options rather than as a top-level Speech API parameter.
        payload["provider"] = {
            "options": {
                "openai": {"instructions": normalized_instructions},
            }
        }

    headers = _make_openrouter_headers(provider)
    url = build_openrouter_api_url("/audio/speech", provider.settings if isinstance(provider.settings, dict) else None)
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=300,
    )
    if response.status_code >= 400:
        raise _build_stream_error(response)

    audio_bytes = bytes(response.content or b"")
    if not audio_bytes:
        raise RuntimeError("OpenRouter text-to-speech returned an empty audio payload")
    cost, cost_details = _build_usage_cost_details(
        None,
        model_name=model_name,
        voice=normalized_voice,
        response_format=normalized_format,
    )

    return {
        "audio_bytes": audio_bytes,
        "model": model_name,
        "voice": normalized_voice,
        "response_format": normalized_format,
        "file_type": _OPENROUTER_AUDIO_MIME_TYPES.get(normalized_format, "audio/mpeg"),
        "extension": _OPENROUTER_AUDIO_EXTENSIONS.get(normalized_format, normalized_format),
        "transcript": None,
        "cost": cost,
        "cost_details": cost_details,
    }


def get_audio_generation_schema_part_1(db, provider_id: str):
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []

    if provider and provider.api_key:
        try:
            for item in openrouter_text_to_speech_models_list(provider):
                model_id = str(item.get("id") or "").strip()
                model_label = str(item.get("name") or model_id).strip() or model_id
                if model_id:
                    model_options.append(Option(value=model_id, label=model_label))
        except Exception:
            logger.exception(
                "Failed to fetch OpenRouter TTS models for provider '%s'",
                provider_id,
            )

    return Sections(
        sections=[
            Section(
                title="OpenRouter Audio Generation",
                i18n_title="llm.shared.section_openrouter_audio.title",
                description="Select the OpenRouter text-to-speech model.",
                i18n_description="llm.shared.section_select_the_openrouter_audio.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        description="Choose which OpenRouter audio output model to use.",
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


def get_audio_generation_schema_part_2(model_name: str, provider: LLMProvider | None = None):
    del model_name
    del provider
    format_options = [
        Option(value=response_format, label=response_format.upper())
        for response_format in OPENROUTER_TTS_RESPONSE_FORMATS
    ]

    return Sections(
        sections=[
            Section(
                title="OpenRouter Audio Generation",
                i18n_title="llm.shared.section_openrouter_audio.title",
                description="Choose the default voice and audio format for OpenRouter speech output.",
                i18n_description="llm.shared.section_choose_the_default.description",
                fields=[
                    FieldSchema(
                        key="voice",
                        label="Voice",
                        description="Enter the provider-specific voice identifier used for generated speech.",
                        i18n_label="schema_audio_generation_voice",
                        i18n_description="schema_audio_generation_voice_desc",
                        type="string",
                        default=OPENROUTER_TTS_DEFAULT_VOICE,
                        placeholder="Enter a voice ID",
                        i18n_placeholder="llm.shared.voice.placeholder",
                        required=True,
                    ),
                    FieldSchema(
                        key="response_format",
                        label="Audio Format",
                        description="Output format for generated speech files.",
                        i18n_label="schema_audio_generation_response_format",
                        i18n_description="schema_audio_generation_response_format_desc",
                        type="select",
                        options=format_options,
                        default=OPENROUTER_TTS_DEFAULT_FORMAT,
                        placeholder="Select an audio format",
                        i18n_placeholder="llm.shared.response_format.placeholder",
                    ),
                ],
            )
        ]
    )
