from __future__ import annotations

from typing import Any

from openai import Client

from app.llm.audio_generation_pricing import (
    build_audio_generation_model_option,
    calculate_audio_generation_cost,
)
from app.llm.models import LLMProvider
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.utils.schemas import FieldSchema, Option, Section, Sections


OPENAI_TEXT_TO_SPEECH_MODELS: list[dict[str, Any]] = [
    {
        "name": "gpt-4o-mini-tts",
        "ids": ["gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15", "gpt-4o-mini-tts-2025-03-20"],
        "voices": ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse", "marin", "cedar"],
        "support_custom_instructions": True,
        "response_formats": ["mp3", "wav", "flac", "aac", "opus"],
    },
    {
        "name": "tts-1",
        "ids": ["tts-1", "tts-1-1106"],
        "voices": ["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
        "support_custom_instructions": False,
        "response_formats": ["mp3", "wav", "flac", "aac", "opus"],
    },
    {
        "name": "tts-1-hd",
        "ids": ["tts-1-hd", "tts-1-hd-1106"],
        "voices": ["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
        "support_custom_instructions": False,
        "response_formats": ["mp3", "wav", "flac", "aac", "opus"],
    },
]


_DEFAULT_RESPONSE_FORMAT = "mp3"
_DEFAULT_AUDIO_FORMATS = ["mp3", "wav", "flac", "aac", "opus"]

_OPENAI_AUDIO_FORMAT_MIME: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "aac": "audio/aac",
    "opus": "audio/opus",
}


def _find_model_config(model_name: str | None) -> dict[str, Any] | None:
    """Find model config by name."""
    needle = str(model_name or "").strip().lower()
    if not needle:
        return None

    for model in OPENAI_TEXT_TO_SPEECH_MODELS:
        if needle == str(model.get("name") or "").strip().lower():
            return model
        for model_id in model.get("ids", []):
            if needle == str(model_id or "").strip().lower():
                return model
    return None


def _model_default_voice(model_config: dict[str, Any] | None) -> str:
    """Get default voice from model config."""
    voices = model_config.get("voices") if isinstance(model_config, dict) else None
    if isinstance(voices, list) and voices:
        first_voice = str(voices[0] or "").strip()
        if first_voice:
            return first_voice
    return "alloy"


def _model_allowed_formats(model_config: dict[str, Any] | None) -> list[str]:
    """Get allowed formats from model config."""
    formats = model_config.get("response_formats") if isinstance(model_config, dict) else None
    if isinstance(formats, list) and formats:
        normalized: list[str] = []
        for item in formats:
            text = str(item or "").strip().lower()
            if text and text in _OPENAI_AUDIO_FORMAT_MIME and text not in normalized:
                normalized.append(text)
        if normalized:
            return normalized
    return list(_DEFAULT_AUDIO_FORMATS)


def normalize_openai_tts_voice(model_name: str, voice: str | None) -> str:
    """Normalize OpenAI TTS voice."""
    model_config = _find_model_config(model_name)
    default_voice = _model_default_voice(model_config)
    requested_voice_raw = str(voice or "").strip()
    if not requested_voice_raw:
        if model_config is None:
            raise ValueError(
                "voice is required for OpenAI-compatible text-to-speech models outside the built-in OpenAI catalog"
            )
        return default_voice

    requested_voice = requested_voice_raw.lower()
    voices = model_config.get("voices") if isinstance(model_config, dict) else None
    if isinstance(voices, list) and voices:
        allowed = {str(v).strip().lower() for v in voices if str(v or "").strip()}
        if requested_voice in allowed:
            return requested_voice
        return default_voice

    return requested_voice_raw


def normalize_openai_tts_response_format(model_name: str, response_format: str | None) -> str:
    """Normalize OpenAI TTS response format."""
    model_config = _find_model_config(model_name)
    allowed_formats = _model_allowed_formats(model_config)
    requested = str(response_format or "").strip().lower()
    if requested in allowed_formats:
        return requested
    if _DEFAULT_RESPONSE_FORMAT in allowed_formats:
        return _DEFAULT_RESPONSE_FORMAT
    return allowed_formats[0]


def _provider_has_custom_base_url(provider: LLMProvider | None) -> bool:
    """Check if provider has custom base URL."""
    if provider is None or not isinstance(provider.settings, dict):
        return False
    base_url = str(provider.settings.get("base_url") or "").strip()
    return bool(base_url)


def _provider_model_ids(provider: LLMProvider | None) -> list[str]:
    """Get provider model IDs."""
    if provider is None:
        return []
    status = provider.status if isinstance(provider.status, dict) else {}
    raw_values = status.get("model_list")
    if not isinstance(raw_values, list):
        return []

    model_ids: list[str] = []
    for raw_value in raw_values:
        model_id = str(raw_value or "").strip()
        if model_id and model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids


def _default_response_format_for_provider(provider: LLMProvider | None, model_config: dict[str, Any] | None) -> str:
    """Get default response format for provider."""
    if model_config is not None:
        return normalize_openai_tts_response_format(str(model_config.get("name") or ""), _DEFAULT_RESPONSE_FORMAT)

    if provider is not None and isinstance(provider.settings, dict):
        base_url = str(provider.settings.get("base_url") or "").strip().lower()
        if "groq.com" in base_url:
            return "wav"

    return _DEFAULT_RESPONSE_FORMAT


def _serialize_tts_model(model_name: str) -> dict[str, Any]:
    """Serialize TTS model."""
    model_config = _find_model_config(model_name)
    if model_config:
        normalized_name = str(model_config.get("name") or model_name).strip()
        return {
            "id": normalized_name,
            "name": normalized_name,
            "voices": list(model_config.get("voices") or []),
            "response_formats": _model_allowed_formats(model_config),
            "support_custom_instructions": bool(model_config.get("support_custom_instructions")),
            "voice_required": True,
            "supports_custom_voice": False,
        }

    normalized_name = str(model_name or "").strip()
    return {
        "id": normalized_name,
        "name": normalized_name,
        "voices": [],
        "response_formats": list(_DEFAULT_AUDIO_FORMATS),
        "support_custom_instructions": True,
        "voice_required": True,
        "supports_custom_voice": True,
    }


def openai_text_to_speech_models_list(
    api_key: str | None = None,
    provider: LLMProvider | None = None,
) -> list[dict[str, Any]]:
    """OpenAI text to speech models list."""
    del api_key
    if _provider_has_custom_base_url(provider):
        provider_model_ids = _provider_model_ids(provider)
        if provider_model_ids:
            return [_serialize_tts_model(model_id) for model_id in provider_model_ids]

    return [
        _serialize_tts_model(str(model.get("name") or "").strip())
        for model in OPENAI_TEXT_TO_SPEECH_MODELS
        if str(model.get("name") or "").strip()
    ]


def _read_audio_response_bytes(response: Any) -> bytes:
    """Read audio response bytes."""
    data: Any = None

    read_fn = getattr(response, "read", None)
    if callable(read_fn):
        data = read_fn()

    if not data:
        iter_bytes_fn = getattr(response, "iter_bytes", None)
        if callable(iter_bytes_fn):
            data = b"".join(iter_bytes_fn())

    if not data:
        data = getattr(response, "content", None)

    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")

    raise RuntimeError("OpenAI text-to-speech response did not contain audio bytes")


def openai_generate_audio(
    api_key: str,
    model: str,
    voice: str,
    input_text: str,
    instructions: str | None,
    response_format: str,
    base_url: str | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
) -> dict[str, Any]:
    """OpenAI generate audio."""
    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("model is required for OpenAI text-to-speech")

    normalized_input = str(input_text or "").strip()
    if not normalized_input:
        raise ValueError("input text is required for OpenAI text-to-speech")

    normalized_voice = normalize_openai_tts_voice(model_name, voice)
    normalized_format = normalize_openai_tts_response_format(model_name, response_format)

    payload: dict[str, Any] = {
        "model": model_name,
        "voice": normalized_voice,
        "input": normalized_input,
        "response_format": normalized_format,
    }

    model_config = _find_model_config(model_name)
    if model_config and model_config.get("support_custom_instructions"):
        normalized_instructions = str(instructions or "").strip()
        if normalized_instructions:
            payload["instructions"] = normalized_instructions

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    normalized_base_url = str(base_url or "").strip()
    if normalized_base_url:
        client_kwargs["base_url"] = normalized_base_url
    default_headers = custom_headers_to_dict(custom_headers)
    if default_headers:
        client_kwargs["default_headers"] = default_headers

    client = Client(**client_kwargs)
    response = client.audio.speech.create(**payload)
    audio_bytes = _read_audio_response_bytes(response)
    if not audio_bytes:
        raise RuntimeError("OpenAI text-to-speech returned an empty audio payload")

    cost_details = None
    usage = getattr(response, "usage", None)
    if usage is not None:
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(
            getattr(usage, "completion_tokens", 0)
            or getattr(usage, "output_tokens", 0)
            or 0
        )
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        if input_details is not None:
            input_tokens = int(getattr(input_details, "text_tokens", 0) or input_tokens)
        if output_details is not None:
            output_tokens = int(getattr(output_details, "audio_tokens", 0) or output_tokens)
        cost_details = calculate_audio_generation_cost(
            "openai",
            model_name,
            input_text=normalized_input,
            input_text_tokens=input_tokens,
            output_audio_tokens=output_tokens,
        )
    else:
        cost_details = calculate_audio_generation_cost(
            "openai",
            model_name,
            input_text=normalized_input,
        )

    file_type = _OPENAI_AUDIO_FORMAT_MIME.get(normalized_format, _OPENAI_AUDIO_FORMAT_MIME[_DEFAULT_RESPONSE_FORMAT])

    return {
        "audio_bytes": audio_bytes,
        "model": model_name,
        "voice": normalized_voice,
        "response_format": normalized_format,
        "file_type": file_type,
        "extension": normalized_format,
        "cost": cost_details.get("cost") if isinstance(cost_details, dict) else None,
        "cost_details": cost_details,
    }


def get_audio_generation_schema_part_1(db, provider_id: str):
    """Get audio generation schema part 1."""
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []

    if provider:
        for item in openai_text_to_speech_models_list(provider.api_key, provider=provider):
            model_id = str(item.get("id") or "").strip()
            if model_id:
                label, metadata = build_audio_generation_model_option(
                    "openai",
                    model_id,
                    label=model_id,
                )
                model_options.append(Option(value=model_id, label=label, metadata=metadata))

    return Sections(
        sections=[
            Section(
                title="OpenAI Audio Generation",
                description="",
                i18n_title="schema_audio_generation_openai_section_title",
                i18n_description="schema_audio_generation_openai_section_desc",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        description="Choose which OpenAI text-to-speech model to use.",
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
    """Get audio generation schema part 2."""
    model_config = _find_model_config(model_name)
    voices = model_config.get("voices", []) if model_config else []
    response_formats = _model_allowed_formats(model_config)

    voice_options = [Option(value=voice, label=voice) for voice in voices]
    format_options = [Option(value=fmt, label=fmt.upper()) for fmt in response_formats]

    default_voice = _model_default_voice(model_config) if model_config else None
    default_format = _default_response_format_for_provider(provider, model_config)
    use_custom_voice_field = model_config is None

    voice_field = FieldSchema(
        key="voice",
        label="Voice",
        description=(
            "Default voice used for generated speech."
            if not use_custom_voice_field
            else "Enter the provider-specific voice identifier for this OpenAI-compatible TTS model."
        ),
        i18n_label="schema_audio_generation_voice",
        i18n_description="schema_audio_generation_voice_desc",
        type="string" if use_custom_voice_field else "select",
        options=None if use_custom_voice_field else voice_options,
        default=default_voice,
        placeholder="Enter a voice ID" if use_custom_voice_field else "Select a voice",
        i18n_placeholder="llm.shared.voice.placeholder",
    )

    return Sections(
        sections=[
            Section(
                title="OpenAI Audio Generation",
                description="",
                i18n_title="schema_audio_generation_openai_section_title",
                i18n_description="schema_audio_generation_openai_section_desc",
                fields=[
                    voice_field,
                    FieldSchema(
                        key="response_format",
                        label="Audio Format",
                        description="Output format for generated speech files.",
                        i18n_label="schema_audio_generation_response_format",
                        i18n_description="schema_audio_generation_response_format_desc",
                        type="select",
                        options=format_options,
                        default=default_format,
                        placeholder="Select an audio format",
                    ),
                ],
            )
        ]
    )


supported_languages = [
    "af",  # Afrikaans
    "ar",  # Arabic
    "hy",  # Armenian
    "az",  # Azerbaijani
    "be",  # Belarusian
    "bs",  # Bosnian
    "bg",  # Bulgarian
    "ca",  # Catalan
    "zh",  # Chinese
    "hr",  # Croatian
    "cs",  # Czech
    "da",  # Danish
    "nl",  # Dutch
    "en",  # English
    "et",  # Estonian
    "fi",  # Finnish
    "fr",  # French
    "gl",  # Galician
    "de",  # German
    "el",  # Greek
    "he",  # Hebrew
    "hi",  # Hindi
    "hu",  # Hungarian
    "is",  # Icelandic
    "id",  # Indonesian
    "it",  # Italian
    "ja",  # Japanese
    "kn",  # Kannada
    "kk",  # Kazakh
    "ko",  # Korean
    "lv",  # Latvian
    "lt",  # Lithuanian
    "mk",  # Macedonian
    "ms",  # Malay
    "mr",  # Marathi
    "mi",  # Maori
    "ne",  # Nepali
    "no",  # Norwegian
    "fa",  # Persian
    "pl",  # Polish
    "pt",  # Portuguese
    "ro",  # Romanian
    "ru",  # Russian
    "sr",  # Serbian
    "sk",  # Slovak
    "sl",  # Slovenian
    "es",  # Spanish
    "sw",  # Swahili
    "sv",  # Swedish
    "tl",  # Tagalog
    "ta",  # Tamil
    "th",  # Thai
    "tr",  # Turkish
    "uk",  # Ukrainian
    "ur",  # Urdu
    "vi",  # Vietnamese
    "cy",  # Welsh
]
