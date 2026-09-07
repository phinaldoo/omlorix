from __future__ import annotations

from typing import Any

import requests

from app.llm.audio_generation_pricing import (
    build_audio_generation_model_option,
    calculate_audio_generation_cost,
)
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
from app.llm.models import LLMProvider
from app.utils.schemas import FieldSchema, Option, Section, Sections


DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY = "aura-2"
DEEPGRAM_DEFAULT_TTS_VOICE = "aura-2-thalia-en"
DEEPGRAM_TTS_RESPONSE_FORMATS = ["mp3", "wav", "opus"]
DEEPGRAM_TTS_RESPONSE_FORMAT_MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/ogg",
}
DEEPGRAM_TTS_RESPONSE_FORMAT_PARAMS = {
    "mp3": {"encoding": "mp3"},
    "wav": {"encoding": "linear16", "container": "wav"},
    "opus": {"encoding": "opus", "container": "ogg"},
}
DEEPGRAM_TTS_FALLBACK_VOICES = [
    {
        "id": DEEPGRAM_DEFAULT_TTS_VOICE,
        "name": "thalia",
        "architecture": DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY,
        "language": "en-us",
        "accent": "American",
    },
    {
        "id": "aura-2-zeus-en",
        "name": "zeus",
        "architecture": DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY,
        "language": "en-us",
        "accent": "American",
    },
]


def _display_family_name(model_family: str) -> str:
    return str(model_family or "").strip().replace("-", " ").title()


def _normalize_tts_voice_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None

    voice_id = ""
    for key in ("canonical_name", "name", "model", "id"):
        value = str(entry.get(key) or "").strip()
        if value:
            voice_id = value
            break
    if not voice_id:
        return None

    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    display_name = str(entry.get("display_name") or metadata.get("name") or voice_id).strip() or voice_id
    architecture = str(entry.get("architecture") or metadata.get("architecture") or "").strip()
    language = str(metadata.get("language") or "").strip() or None
    accent = str(metadata.get("accent") or "").strip() or None
    gender = str(metadata.get("gender") or "").strip() or None

    return {
        "id": voice_id,
        "name": display_name,
        "architecture": architecture or DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY,
        "language": language,
        "accent": accent,
        "gender": gender,
    }


def _list_deepgram_tts_voice_entries(
    api_key: str,
    *,
    timeout: int = DEEPGRAM_DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    payload = list_deepgram_models_payload(api_key, timeout=timeout)
    raw_models = payload.get("tts")
    if not isinstance(raw_models, list):
        return list(DEEPGRAM_TTS_FALLBACK_VOICES)

    normalized: list[dict[str, Any]] = []
    for entry in raw_models:
        voice = _normalize_tts_voice_entry(entry)
        if voice:
            normalized.append(voice)

    if not normalized:
        return list(DEEPGRAM_TTS_FALLBACK_VOICES)

    normalized.sort(key=lambda item: (str(item.get("architecture") or "").casefold(), str(item.get("name") or "").casefold()))
    return normalized


def deepgram_text_to_speech_models_list(
    api_key: str,
    *,
    timeout: int = DEEPGRAM_DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    voices = _list_deepgram_tts_voice_entries(api_key, timeout=timeout)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for voice in voices:
        model_family = str(voice.get("architecture") or "").strip() or DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY
        grouped.setdefault(model_family, []).append(voice)

    models: list[dict[str, Any]] = []
    for model_family, family_voices in grouped.items():
        family_voices_sorted = sorted(
            family_voices,
            key=lambda item: str(item.get("name") or item.get("id") or "").casefold(),
        )
        models.append(
            {
                "id": model_family,
                "name": _display_family_name(model_family),
                "voices": [str(item.get("id") or "").strip() for item in family_voices_sorted if str(item.get("id") or "").strip()],
                "voice_options": family_voices_sorted,
                "response_formats": list(DEEPGRAM_TTS_RESPONSE_FORMATS),
                "support_custom_instructions": False,
                "voice_required": True,
            }
        )

    models.sort(key=lambda item: str(item.get("name") or item.get("id") or "").casefold())
    if models:
        return models

    return [
        {
            "id": DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY,
            "name": _display_family_name(DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY),
            "voices": [DEEPGRAM_DEFAULT_TTS_VOICE],
            "voice_options": list(DEEPGRAM_TTS_FALLBACK_VOICES),
            "response_formats": list(DEEPGRAM_TTS_RESPONSE_FORMATS),
            "support_custom_instructions": False,
            "voice_required": True,
        }
    ]


def normalize_deepgram_response_format(response_format: str | None) -> str:
    requested = str(response_format or "").strip().lower()
    if requested in DEEPGRAM_TTS_RESPONSE_FORMAT_PARAMS:
        return requested
    return "mp3"


def _resolve_deepgram_voice_value(voice: str | None) -> str:
    requested = str(voice or "").strip()
    return requested or DEEPGRAM_DEFAULT_TTS_VOICE


def resolve_deepgram_tts_model_name(model: str | None, voice: str | None) -> str:
    model_name = str(model or "").strip()
    requested_voice = str(voice or "").strip()
    if not requested_voice and model_name.startswith("aura-") and model_name.count("-") >= 2:
        return model_name

    voice_id = _resolve_deepgram_voice_value(requested_voice)

    if voice_id.startswith("aura-"):
        return voice_id
    if model_name.startswith("aura-") and model_name.count("-") >= 2:
        return model_name
    if model_name:
        return model_name
    return voice_id


def deepgram_generate_audio(
    *,
    api_key: str,
    model: str,
    voice: str | None,
    input_text: str,
    response_format: str | None,
    timeout: int = DEEPGRAM_DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    text_value = str(input_text or "").strip()
    if not text_value:
        raise ValueError("input text is required for Deepgram text-to-speech")

    effective_model = resolve_deepgram_tts_model_name(model, voice)
    normalized_voice = _resolve_deepgram_voice_value(voice)
    normalized_format = normalize_deepgram_response_format(response_format)
    endpoint = f"{DEEPGRAM_BASE_URL}/v1/speak"

    try:
        response = requests.post(
            endpoint,
            headers=deepgram_auth_headers(
                api_key,
                content_type="application/json",
            ),
            params={
                "model": effective_model,
                **DEEPGRAM_TTS_RESPONSE_FORMAT_PARAMS[normalized_format],
                DEEPGRAM_PRIVACY_OPT_OUT_QUERY_PARAM: DEEPGRAM_PRIVACY_OPT_OUT_QUERY_VALUE,
            },
            json={"text": text_value},
            timeout=normalize_deepgram_timeout(timeout),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to reach Deepgram API: {exc}") from exc

    if not response.ok:
        detail = extract_deepgram_error_detail(response)
        raise RuntimeError(f"Deepgram text-to-speech failed: {detail}")

    audio_bytes = response.content
    if not audio_bytes:
        raise RuntimeError("Deepgram text-to-speech returned an empty audio payload")

    file_type = response.headers.get("content-type") or DEEPGRAM_TTS_RESPONSE_FORMAT_MIME[normalized_format]
    file_type = file_type.split(";", 1)[0].strip().lower() or DEEPGRAM_TTS_RESPONSE_FORMAT_MIME[normalized_format]
    cost_details = calculate_audio_generation_cost(
        "deepgram",
        effective_model,
        input_text=text_value,
    )

    return {
        "audio_bytes": audio_bytes,
        "model": effective_model,
        "voice": normalized_voice,
        "response_format": normalized_format,
        "file_type": file_type,
        "extension": "ogg" if normalized_format == "opus" else normalized_format,
        "cost": cost_details.get("cost") if isinstance(cost_details, dict) else None,
        "cost_details": cost_details,
    }


def get_audio_generation_schema_part_1(db, provider_id: str):
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []

    if provider:
        provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
        timeout = provider_settings.get("timeout", DEEPGRAM_DEFAULT_TIMEOUT_SECONDS)
        try:
            for item in deepgram_text_to_speech_models_list(provider.api_key, timeout=timeout):
                model_id = str(item.get("id") or "").strip()
                label = str(item.get("name") or model_id).strip() or model_id
                if model_id:
                    option_label, metadata = build_audio_generation_model_option(
                        "deepgram",
                        model_id,
                        label=label,
                    )
                    model_options.append(Option(value=model_id, label=option_label, metadata=metadata))
        except Exception:
            option_label, metadata = build_audio_generation_model_option(
                "deepgram",
                DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY,
                label=_display_family_name(DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY),
            )
            model_options.append(
                Option(
                    value=DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY,
                    label=option_label,
                    metadata=metadata,
                )
            )

    return Sections(
        sections=[
            Section(
                title="Deepgram Audio Generation",
                i18n_title="llm.shared.section_deepgram_audio.title",
                description="Select the Deepgram text-to-speech model family.",
                i18n_description="llm.shared.section_select_the_deepgram.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        description="Choose which Deepgram Aura model family to use.",
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
    timeout: int = DEEPGRAM_DEFAULT_TIMEOUT_SECONDS,
):
    selected_model = str(model_name or "").strip() or DEEPGRAM_DEFAULT_TTS_MODEL_FAMILY
    response_format_options = [
        Option(value=response_format, label=response_format.upper())
        for response_format in DEEPGRAM_TTS_RESPONSE_FORMATS
    ]

    voice_options: list[Option] = []
    default_voice = DEEPGRAM_DEFAULT_TTS_VOICE
    use_text_voice_field = False

    try:
        models = deepgram_text_to_speech_models_list(api_key, timeout=timeout)
        match = next((item for item in models if str(item.get("id") or "").strip() == selected_model), None)
        matched_voices = list(match.get("voice_options") or []) if isinstance(match, dict) else []
        for voice in matched_voices:
            voice_id = str(voice.get("id") or "").strip()
            if not voice_id:
                continue
            label = str(voice.get("name") or voice_id).strip() or voice_id
            metadata = {
                key: value
                for key, value in {
                    "language": voice.get("language"),
                    "accent": voice.get("accent"),
                    "gender": voice.get("gender"),
                    "architecture": voice.get("architecture"),
                }.items()
                if value
            }
            voice_options.append(Option(value=voice_id, label=label, metadata=metadata or None))
        if voice_options:
            default_voice = str(voice_options[0].value)
    except Exception:
        use_text_voice_field = True

    voice_field = FieldSchema(
        key="voice",
        label="Voice",
        description=(
            "Select a Deepgram voice for the chosen model family."
            if not use_text_voice_field and voice_options
            else "Enter a Deepgram voice identifier such as 'aura-2-thalia-en'."
        ),
        i18n_label="schema_audio_generation_voice",
        i18n_description="schema_audio_generation_voice_desc",
        type="string" if use_text_voice_field or not voice_options else "select",
        options=None if use_text_voice_field or not voice_options else voice_options,
        default=default_voice,
        placeholder="Enter a Deepgram voice ID" if use_text_voice_field or not voice_options else "Select a voice",
        i18n_placeholder="llm.shared.voice.placeholder",
        required=True,
    )

    return Sections(
        sections=[
            Section(
                title="Deepgram Audio Generation",
                i18n_title="llm.shared.section_deepgram_audio.title",
                description="Choose a Deepgram voice and output format.",
                i18n_description="llm.shared.section_choose_a_deepgram.description",
                fields=[
                    voice_field,
                    FieldSchema(
                        key="response_format",
                        label="Audio Format",
                        description="Choose the stored output format for generated Deepgram speech.",
                        i18n_label="schema_audio_generation_response_format",
                        i18n_description="schema_audio_generation_response_format_desc",
                        type="select",
                        options=response_format_options,
                        default="mp3",
                        placeholder="Select an audio format",
                        i18n_placeholder="llm.shared.response_format.placeholder",
                    ),
                ],
            )
        ]
    )
