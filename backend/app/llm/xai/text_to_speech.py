"""xAI native text-to-speech adapter and settings schemas."""

from __future__ import annotations

import base64
from typing import Any

import requests

from app.llm.audio_generation_pricing import (
    build_audio_generation_model_option,
    calculate_audio_generation_cost,
)
from app.llm.models import LLMProvider
from app.llm.xai.common import (
    require_xai_success,
    xai_base_url,
    xai_headers,
    xai_timeout,
)
from app.utils.schemas import FieldSchema, Option, Section, Sections


XAI_TTS_MODEL = "grok-tts"
XAI_TTS_FALLBACK_VOICES = [
    "carina",
    "zagan",
    "helix",
    "orion",
    "luna",
    "iris",
    "altair",
    "zenith",
    "perseus",
    "helios",
    "lux",
    "kepler",
    "rigel",
    "cosmo",
    "celeste",
    "ursa",
    "sirius",
    "lumen",
    "castor",
    "naksh",
    "atlas",
    "ara",
    "eve",
    "leo",
    "rex",
    "sal",
]
XAI_TTS_RESPONSE_FORMATS = ["mp3", "wav", "pcm", "mulaw", "alaw"]
XAI_TTS_LANGUAGES = [
    "auto",
    "en",
    "ar-EG",
    "ar-SA",
    "ar-AE",
    "bn",
    "zh",
    "fr",
    "de",
    "hi",
    "id",
    "it",
    "ja",
    "ko",
    "pt-BR",
    "pt-PT",
    "ru",
    "es-MX",
    "es-ES",
    "tr",
    "vi",
]
XAI_TTS_SAMPLE_RATES = [8_000, 16_000, 22_050, 24_000, 44_100, 48_000]
XAI_TTS_MP3_BIT_RATES = [32_000, 64_000, 96_000, 128_000, 192_000]
XAI_TTS_STREAMING_LATENCY_LEVELS = [0, 1, 2]
XAI_TTS_MIME_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "mulaw": "audio/basic",
    "alaw": "audio/alaw",
}
XAI_TTS_EXTENSIONS = {
    "mp3": "mp3",
    "wav": "wav",
    "pcm": "pcm",
    "mulaw": "ulaw",
    "alaw": "alaw",
}


def _normalize_voice_entry(item: Any, *, custom: bool = False) -> dict[str, Any] | None:
    """Normalize built-in and custom xAI voice records for Omlorix pickers."""
    if not isinstance(item, dict):
        return None
    voice_id = str(item.get("voice_id") or "").strip()
    if not voice_id:
        return None

    labels = {
        key: str(item.get(key) or "").strip()
        for key in ("gender", "age", "accent", "language", "use_case", "tone")
        if str(item.get(key) or "").strip()
    }
    # The picker already identifies the provider and does not need to render
    # the API's English-only "multilingual" category in every locale.
    if labels.get("language", "").casefold() == "multilingual":
        labels.pop("language", None)
    return {
        "id": voice_id,
        "name": str(item.get("name") or voice_id).strip() or voice_id,
        "description": str(item.get("description") or "").strip(),
        "category": "",
        "labels": labels,
        "language": labels.get("language"),
    }


def _voice_entries(provider: LLMProvider) -> list[dict[str, Any]]:
    """Fetch built-in xAI voices and normalize their IDs."""
    try:
        response = requests.get(
            f"{xai_base_url(provider)}/tts/voices",
            headers=xai_headers(provider, include_content_type=False),
            timeout=xai_timeout(),
        )
        require_xai_success(response, "voice listing")
        payload = response.json()
        entries = payload.get("voices") if isinstance(payload, dict) else []
        normalized = [
            entry for item in entries if (entry := _normalize_voice_entry(item))
        ]
        if normalized:
            return normalized
    except Exception:
        pass
    return [
        {
            "id": voice,
            "name": voice.title(),
            "description": "",
            "category": "",
            "labels": {},
            "language": "multilingual",
        }
        for voice in XAI_TTS_FALLBACK_VOICES
    ]


def _custom_voice_entries(provider: LLMProvider) -> list[dict[str, Any]]:
    """List team-owned custom voices when the xAI account exposes that API."""
    try:
        response = requests.get(
            f"{xai_base_url(provider)}/custom-voices",
            headers=xai_headers(provider, include_content_type=False),
            params={"limit": 1000},
            timeout=xai_timeout(),
        )
        # Custom voices are region/plan gated. A provider that cannot access
        # them must still retain the built-in voice picker.
        if not response.ok:
            return []
        payload = response.json()
        entries = payload.get("voices") if isinstance(payload, dict) else []
        return [
            entry
            for item in entries
            if (entry := _normalize_voice_entry(item, custom=True))
        ]
    except Exception:
        return []


def list_xai_voice_entries(provider: LLMProvider) -> list[dict[str, Any]]:
    """Return de-duplicated built-in and account-owned xAI voice records."""
    voices = [*_voice_entries(provider), *_custom_voice_entries(provider)]
    # Prefer the later custom record if xAI ever returns the same identifier in
    # both endpoints, while retaining the provider's stable display order.
    by_id: dict[str, dict[str, Any]] = {}
    for voice in voices:
        voice_id = str(voice.get("id") or "").strip()
        if voice_id:
            by_id[voice_id] = voice
    return list(by_id.values())


def search_xai_voices(
    provider: LLMProvider,
    *,
    search: str | None = None,
    page_size: int = 24,
    next_page_token: str | None = None,
    voice_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Search built-in and accessible custom xAI voices with stable pagination."""
    voices = list_xai_voice_entries(provider)
    by_id = {str(voice.get("id") or "").strip(): voice for voice in voices}

    requested_ids = [
        str(value or "").strip()
        for value in (voice_ids or [])
        if str(value or "").strip()
    ]
    if requested_ids:
        selected = [by_id[voice_id] for voice_id in requested_ids if voice_id in by_id]
        return {
            "voices": selected,
            "has_more": False,
            "next_page_token": None,
        }

    query = str(search or "").strip().casefold()
    if query:
        voices = [
            voice
            for voice in voices
            if query
            in " ".join(
                [
                    str(voice.get("id") or ""),
                    str(voice.get("name") or ""),
                    str(voice.get("description") or ""),
                    *[str(value) for value in (voice.get("labels") or {}).values()],
                ]
            ).casefold()
        ]

    try:
        offset = max(0, int(str(next_page_token or "0")))
    except (TypeError, ValueError):
        offset = 0
    limit = max(1, min(int(page_size or 24), 100))
    page = voices[offset : offset + limit]
    next_offset = offset + len(page)
    has_more = next_offset < len(voices)
    return {
        "voices": page,
        "has_more": has_more,
        "next_page_token": str(next_offset) if has_more else None,
    }


def xai_text_to_speech_models_list(provider: LLMProvider) -> list[dict[str, Any]]:
    """Expose xAI TTS with built-in and accessible team-owned voices."""
    voices = list_xai_voice_entries(provider)
    label, pricing = build_audio_generation_model_option(
        "xai",
        XAI_TTS_MODEL,
        label="Grok Text to Speech",
    )
    return [
        {
            "id": XAI_TTS_MODEL,
            "name": label,
            "voices": [entry["id"] for entry in voices],
            "voice_options": voices,
            "response_formats": list(XAI_TTS_RESPONSE_FORMATS),
            "support_custom_instructions": False,
            "supports_custom_voice": True,
            "voice_required": True,
            "pricing": pricing,
        }
    ]


def xai_generate_audio(
    *,
    provider: LLMProvider,
    voice: str | None,
    input_text: str,
    response_format: str | None,
    language: str | None = None,
    sample_rate: int | None = None,
    bit_rate: int | None = None,
    speed: float | None = None,
    optimize_streaming_latency: int | None = None,
    text_normalization: bool | None = None,
) -> dict[str, Any]:
    """Generate spoken audio through xAI's batch TTS endpoint."""
    text = str(input_text or "").strip()
    if not text:
        raise ValueError("Input text is required for xAI text-to-speech")
    if len(text) > 15_000:
        raise ValueError("xAI text-to-speech accepts at most 15,000 characters")

    codec = str(response_format or "mp3").strip().lower()
    if codec not in XAI_TTS_RESPONSE_FORMATS:
        raise ValueError(f"Unsupported xAI text-to-speech codec: {codec}")
    voice_id = str(voice or "eve").strip() or "eve"
    normalized_language = str(language or "auto").strip()
    language_by_casefold = {value.casefold(): value for value in XAI_TTS_LANGUAGES}
    normalized_language = language_by_casefold.get(normalized_language.casefold(), "")
    if not normalized_language:
        raise ValueError("Unsupported xAI text-to-speech language")

    normalized_sample_rate = 24_000 if sample_rate is None else int(sample_rate)
    if normalized_sample_rate not in XAI_TTS_SAMPLE_RATES:
        raise ValueError("Unsupported xAI text-to-speech sample rate")
    normalized_bit_rate = None
    if codec == "mp3":
        normalized_bit_rate = 128_000 if bit_rate is None else int(bit_rate)
        if normalized_bit_rate not in XAI_TTS_MP3_BIT_RATES:
            raise ValueError("Unsupported xAI MP3 bit rate")
    normalized_speed = 1.0 if speed is None else float(speed)
    if not 0.7 <= normalized_speed <= 1.5:
        raise ValueError("xAI text-to-speech speed must be between 0.7 and 1.5")
    normalized_latency = (
        0 if optimize_streaming_latency is None else int(optimize_streaming_latency)
    )
    if normalized_latency not in XAI_TTS_STREAMING_LATENCY_LEVELS:
        raise ValueError("Unsupported xAI text-to-speech latency optimization level")

    output_format: dict[str, Any] = {
        "codec": codec,
        "sample_rate": normalized_sample_rate,
    }
    if codec == "mp3":
        output_format["bit_rate"] = normalized_bit_rate
    request_payload: dict[str, Any] = {
        "text": text,
        "voice_id": voice_id,
        "language": normalized_language,
        "output_format": output_format,
        "speed": normalized_speed,
        "optimize_streaming_latency": normalized_latency,
        "text_normalization": bool(text_normalization),
    }
    response = requests.post(
        f"{xai_base_url(provider)}/tts",
        headers=xai_headers(provider),
        json=request_payload,
        timeout=xai_timeout(),
    )
    require_xai_success(response, "text-to-speech")

    content_type = (
        str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
    )
    audio_bytes = response.content
    if content_type == "application/json":
        payload = response.json()
        encoded = str(payload.get("audio") or "") if isinstance(payload, dict) else ""
        audio_bytes = base64.b64decode(encoded) if encoded else b""
        if isinstance(payload, dict):
            content_type = str(payload.get("content_type") or "").strip().lower()
    if not audio_bytes:
        raise RuntimeError("xAI text-to-speech returned an empty audio payload")
    # Raw audio endpoints can legitimately answer with
    # application/octet-stream. Persist the format selected in the request
    # instead of exposing a generic binary MIME type to browser playback.
    if not content_type.startswith("audio/"):
        content_type = XAI_TTS_MIME_TYPES[codec]

    cost_details = calculate_audio_generation_cost(
        "xai",
        XAI_TTS_MODEL,
        input_text=text,
    )
    return {
        "audio_bytes": audio_bytes,
        "model": XAI_TTS_MODEL,
        "voice": voice_id,
        "response_format": codec,
        "language": normalized_language,
        "sample_rate": normalized_sample_rate,
        "bit_rate": normalized_bit_rate if codec == "mp3" else None,
        "speed": normalized_speed,
        "optimize_streaming_latency": normalized_latency,
        "text_normalization": bool(text_normalization),
        "file_type": content_type,
        "extension": XAI_TTS_EXTENSIONS[codec],
        "cost": cost_details.get("cost") if isinstance(cost_details, dict) else None,
        "cost_details": cost_details,
    }


def get_audio_generation_schema_part_1(db, provider_id: str) -> Sections:
    """Build the xAI TTS model picker."""
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    models = xai_text_to_speech_models_list(provider) if provider else []
    return Sections(
        sections=[
            Section(
                title="Audio Generation",
                i18n_title="schema_xai_audio_generation_title",
                description="Choose the xAI text-to-speech model.",
                i18n_description="schema_xai_audio_model_desc",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="admin.shared.model_name.label",
                        description="Choose the audio generation model.",
                        i18n_description="admin.shared.model_name.description",
                        type="select",
                        options=[
                            Option(value=item["id"], label=item["name"])
                            for item in models
                        ],
                        placeholder="Select a model",
                        i18n_placeholder="admin.shared.model_name.placeholder",
                    )
                ],
            )
        ]
    )


def get_audio_generation_schema_part_2(
    model_name: str,
    *,
    provider: LLMProvider,
) -> Sections:
    """Build the playback-relevant batch-synthesis defaults for xAI TTS."""
    del model_name
    model = xai_text_to_speech_models_list(provider)[0]
    return Sections(
        sections=[
            Section(
                title="Audio Generation",
                i18n_title="schema_xai_audio_generation_title",
                description="Configure xAI voice synthesis.",
                i18n_description="schema_xai_audio_settings_desc",
                fields=[
                    FieldSchema(
                        key="voice",
                        label="Voice",
                        i18n_label="llm.shared.voice.label",
                        description="Choose the voice to use.",
                        i18n_description="llm.shared.voice.description",
                        type="select",
                        options=[
                            Option(value=entry["id"], label=entry["name"])
                            for entry in model["voice_options"]
                        ],
                        default="eve",
                    ),
                    FieldSchema(
                        key="response_format",
                        label="Response Format",
                        i18n_label="llm.shared.response_format.label",
                        description="Choose the output response format.",
                        i18n_description="llm.shared.response_format.description",
                        type="select",
                        options=[
                            Option(value=value, label=value.upper())
                            for value in XAI_TTS_RESPONSE_FORMATS
                        ],
                        default="mp3",
                    ),
                    FieldSchema(
                        key="language",
                        label="Language",
                        i18n_label="schema_xai_tts_language",
                        description="Choose a BCP-47 language or let xAI detect it automatically.",
                        i18n_description="schema_xai_tts_language_desc",
                        type="select",
                        options=[
                            Option(value=value, label=value, translatable=False)
                            for value in XAI_TTS_LANGUAGES
                        ],
                        default="auto",
                    ),
                    FieldSchema(
                        key="sample_rate",
                        label="Sample rate",
                        i18n_label="schema_xai_tts_sample_rate",
                        description="Choose the output sample rate in hertz.",
                        i18n_description="schema_xai_tts_sample_rate_desc",
                        type="select",
                        options=[
                            Option(
                                value=str(value),
                                label=f"{value:,} Hz",
                                translatable=False,
                            )
                            for value in XAI_TTS_SAMPLE_RATES
                        ],
                        default="24000",
                    ),
                    FieldSchema(
                        key="bit_rate",
                        label="MP3 bit rate",
                        i18n_label="schema_xai_tts_bit_rate",
                        description="Choose MP3 quality. This setting is ignored for non-MP3 formats.",
                        i18n_description="schema_xai_tts_bit_rate_desc",
                        type="select",
                        options=[
                            Option(
                                value=str(value),
                                label=f"{value // 1000} kbps",
                                translatable=False,
                            )
                            for value in XAI_TTS_MP3_BIT_RATES
                        ],
                        default="128000",
                    ),
                    FieldSchema(
                        key="speed",
                        label="Speech speed",
                        i18n_label="schema_xai_tts_speed",
                        description="Set the speech speed multiplier from 0.7 to 1.5.",
                        i18n_description="schema_xai_tts_speed_desc",
                        type="number",
                        attributes={"min": 0.7, "max": 1.5, "step": 0.05},
                        default=1.0,
                    ),
                    FieldSchema(
                        key="optimize_streaming_latency",
                        label="Latency optimization",
                        i18n_label="schema_xai_tts_latency",
                        description="Reduce time to first audio with a small quality tradeoff at chunk boundaries.",
                        i18n_description="schema_xai_tts_latency_desc",
                        type="select",
                        options=[
                            Option(
                                value="0",
                                label="Best quality",
                                i18n_label="schema_xai_tts_latency_quality",
                            ),
                            Option(
                                value="1",
                                label="Lower latency",
                                i18n_label="schema_xai_tts_latency_fast",
                            ),
                            Option(
                                value="2",
                                label="Lowest latency",
                                i18n_label="schema_xai_tts_latency_fastest",
                            ),
                        ],
                        default="0",
                    ),
                    FieldSchema(
                        key="text_normalization",
                        label="Text normalization",
                        i18n_label="schema_xai_tts_text_normalization",
                        description="Convert numbers, abbreviations, and symbols into more natural spoken forms before synthesis.",
                        i18n_description="schema_xai_tts_text_normalization_desc",
                        type="boolean",
                        default=False,
                    ),
                ],
            )
        ]
    )
