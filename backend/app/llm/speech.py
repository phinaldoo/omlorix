from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.llm.audio_generation_pricing import get_audio_generation_pricing_metadata
from app.llm.base_settings import LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS
from app.llm.elevenlabs.text_to_speech import (
    elevenlabs_generate_audio,
    elevenlabs_text_to_speech_models_list,
)
from app.llm.elevenlabs.transcription import (
    ELEVENLABS_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES,
    ELEVENLABS_TRANSCRIPTION_MODELS,
    ELEVENLABS_TRANSCRIPTION_SUPPORTED_FILE_FORMATS,
    transcribe_audio_bytes as transcribe_audio_bytes_elevenlabs,
)
from app.llm.google_aistudio.text_to_speech import (
    GOOGLE_AISTUDIO_TTS_RESPONSE_FORMATS,
    GOOGLE_AISTUDIO_TTS_VOICES,
    google_aistudio_generate_audio,
    google_aistudio_text_to_speech_models_list_for_provider,
)
from app.llm.google_aistudio.transcription import (
    GOOGLE_AISTUDIO_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES,
    GOOGLE_AISTUDIO_TRANSCRIPTION_SUPPORTED_FILE_FORMATS,
    get_google_aistudio_transcription_models,
    transcribe_audio_bytes as transcribe_audio_bytes_google_aistudio,
)
from app.llm.models import LLMProvider, get_llm_provider
from app.llm.openai.text_to_speech import (
    openai_generate_audio,
    openai_text_to_speech_models_list,
)
from app.llm.openrouter.audio_generation import (
    OPENROUTER_TTS_RESPONSE_FORMATS,
    openrouter_generate_audio,
    openrouter_text_to_speech_models_list,
)
from app.llm.openai.transcription import (
    OPENAI_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES,
    OPENAI_TRANSCRIPTION_SUPPORTED_FILE_FORMATS,
    get_openai_transcription_models,
    transcribe_audio_bytes as transcribe_audio_bytes_openai,
)
from app.llm.schemas import ProviderEnum, provider_api_key_is_optional
from app.llm.xai.text_to_speech import (
    XAI_TTS_RESPONSE_FORMATS,
    xai_generate_audio,
    xai_text_to_speech_models_list,
)
from app.llm.xai.transcription import (
    XAI_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES,
    XAI_TRANSCRIPTION_MODELS,
    XAI_TRANSCRIPTION_SUPPORTED_FILE_FORMATS,
    transcribe_audio_bytes as transcribe_audio_bytes_xai,
)


OPENAI_COMPATIBLE_TRANSCRIPTION_PROVIDER_TYPES = {
    ProviderEnum.openai.value,
    ProviderEnum.openai_responses.value,
    ProviderEnum.openai_chat_completions.value,
}

OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES = {
    ProviderEnum.openai.value,
    ProviderEnum.openai_responses.value,
    ProviderEnum.openai_chat_completions.value,
}

TRANSCRIPTION_PROVIDER_TYPES = set(OPENAI_COMPATIBLE_TRANSCRIPTION_PROVIDER_TYPES) | {
    ProviderEnum.google_aistudio.value,
    ProviderEnum.elevenlabs.value,
    ProviderEnum.xai.value,
}

TTS_PROVIDER_TYPES = set(OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES) | {
    ProviderEnum.openrouter.value,
    ProviderEnum.google_aistudio.value,
    ProviderEnum.elevenlabs.value,
    ProviderEnum.xai.value,
}


@dataclass(frozen=True)
class TranscriptionProviderSnapshot:
    """Provider fields needed after the request ORM transaction is released."""

    provider: str
    api_key: str | None
    settings: dict[str, Any]


def snapshot_transcription_provider(
    provider: LLMProvider,
) -> TranscriptionProviderSnapshot:
    settings = getattr(provider, "settings", None)
    return TranscriptionProviderSnapshot(
        provider=str(getattr(provider, "provider", "") or "").strip(),
        api_key=getattr(provider, "api_key", None),
        settings=dict(settings) if isinstance(settings, dict) else {},
    )

PROVIDER_DISPLAY_LABELS = {
    ProviderEnum.openai.value: "OpenAI",
    ProviderEnum.openai_responses.value: "OpenAI Responses API",
    ProviderEnum.openai_chat_completions.value: "OpenAI Chat Completions API",
    ProviderEnum.lmstudio.value: "LM Studio",
    ProviderEnum.microsoft_azure.value: "Microsoft Azure",
    ProviderEnum.google_aistudio.value: "Google AI Studio",
    ProviderEnum.openrouter.value: "OpenRouter",
    ProviderEnum.elevenlabs.value: "ElevenLabs",
    ProviderEnum.xai.value: "xAI",
}


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def get_provider_display_label(provider_type: str | None, default: str = "Provider") -> str:
    normalized = str(provider_type or "").strip()
    if not normalized:
        return default
    return PROVIDER_DISPLAY_LABELS.get(
        normalized,
        normalized.replace("_", " ").title(),
    )


def provider_supports_tts_instructions(provider_type: str | None) -> bool:
    return str(provider_type or "").strip().lower() not in {
        ProviderEnum.elevenlabs.value,
        ProviderEnum.xai.value,
    }


def list_tts_models_for_provider(provider_row: LLMProvider) -> list[dict[str, Any]]:
    provider_type = str(provider_row.provider or "").strip()

    if provider_type in OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES:
        return openai_text_to_speech_models_list(provider_row.api_key, provider=provider_row)
    if provider_type == ProviderEnum.openrouter.value:
        return openrouter_text_to_speech_models_list(provider_row)
    if provider_type == ProviderEnum.google_aistudio.value:
        return google_aistudio_text_to_speech_models_list_for_provider(provider_row)
    if provider_type == ProviderEnum.elevenlabs.value:
        try:
            return elevenlabs_text_to_speech_models_list(provider_row.api_key)
        except Exception:
            return []
    if provider_type == ProviderEnum.xai.value:
        return xai_text_to_speech_models_list(provider_row)
    return []


def get_tts_model_ids_for_provider(provider_row: LLMProvider) -> list[str]:
    model_ids: list[str] = []
    for item in list_tts_models_for_provider(provider_row):
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id and model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids


def get_tts_model_capabilities_for_provider(
    model_name: str,
    *,
    provider_type: str | None,
    provider_row: LLMProvider | None,
) -> dict[str, Any]:
    def _with_pricing(payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        enriched.update(
            get_audio_generation_pricing_metadata(
                provider_type,
                model_id,
            )
        )
        return enriched

    model_id = str(model_name or "").strip()
    if not model_id:
        return _with_pricing(
            {"voices": [], "response_formats": [], "voice_required": False, "support_custom_instructions": True}
        )

    normalized_provider_type = str(provider_type or "").strip()
    if normalized_provider_type in OPENAI_COMPATIBLE_TTS_PROVIDER_TYPES:
        for model in openai_text_to_speech_models_list(provider=provider_row):
            if str(model.get("id") or "").strip() == model_id:
                return _with_pricing({
                    "voices": list(model.get("voices") or []),
                    "response_formats": list(model.get("response_formats") or []),
                    "voice_required": bool(model.get("voice_required", True)),
                    "support_custom_instructions": bool(model.get("support_custom_instructions")),
                })
        return _with_pricing({
            "voices": [],
            "response_formats": ["mp3", "wav", "flac", "aac", "opus"],
            "voice_required": True,
            "support_custom_instructions": True,
        })

    if normalized_provider_type == ProviderEnum.openrouter.value:
        models = []
        if provider_row is not None:
            models = openrouter_text_to_speech_models_list(provider_row)
        for model in models:
            if str(model.get("id") or "").strip() == model_id:
                return _with_pricing({
                    "voices": list(model.get("voices") or []),
                    "response_formats": list(model.get("response_formats") or []),
                    "voice_required": bool(model.get("voice_required", True)),
                    "support_custom_instructions": bool(model.get("support_custom_instructions", True)),
                    "supports_custom_voice": bool(model.get("supports_custom_voice", True)),
                })
        return _with_pricing({
            "voices": [],
            "response_formats": list(OPENROUTER_TTS_RESPONSE_FORMATS),
            "voice_required": True,
            "support_custom_instructions": model_id.lower().startswith("openai/"),
            "supports_custom_voice": True,
        })

    if normalized_provider_type == ProviderEnum.google_aistudio.value:
        return _with_pricing({
            "voices": list(GOOGLE_AISTUDIO_TTS_VOICES),
            "response_formats": list(GOOGLE_AISTUDIO_TTS_RESPONSE_FORMATS),
            "voice_required": True,
            "support_custom_instructions": True,
        })

    if normalized_provider_type == ProviderEnum.elevenlabs.value:
        return _with_pricing({
            "voices": [],
            "response_formats": ["mp3"],
            "voice_required": True,
            "support_custom_instructions": False,
        })

    if normalized_provider_type == ProviderEnum.xai.value:
        models = xai_text_to_speech_models_list(provider_row) if provider_row else []
        model = next(
            (
                item
                for item in models
                if str(item.get("id") or "").strip() == model_id
            ),
            None,
        )
        return _with_pricing({
            "voices": list((model or {}).get("voices") or []),
            "response_formats": list(XAI_TTS_RESPONSE_FORMATS),
            "voice_required": True,
            "support_custom_instructions": False,
            "supports_custom_voice": True,
        })

    return _with_pricing(
        {"voices": [], "response_formats": [], "voice_required": False, "support_custom_instructions": True}
    )


AudioGenerator = Callable[[LLMProvider, str, str, str | None, bool, dict[str, Any]], dict[str, Any]]


def _generate_via_openai(
    provider: LLMProvider,
    model_name: str,
    input_text: str,
    instructions: str | None,
    multiple_speakers: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    del multiple_speakers
    voice = str(config.get("voice") or "").strip()
    response_format = str(config.get("response_format") or "mp3").strip().lower()
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    return openai_generate_audio(
        api_key=provider.api_key,
        model=model_name,
        voice=voice,
        input_text=input_text,
        instructions=instructions,
        response_format=response_format,
        base_url=provider_settings.get("base_url"),
        custom_headers=provider_settings.get("custom_headers"),
    )


def _generate_via_openrouter(
    provider: LLMProvider,
    model_name: str,
    input_text: str,
    instructions: str | None,
    multiple_speakers: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    del multiple_speakers
    return openrouter_generate_audio(
        provider=provider,
        model=model_name,
        voice=str(config.get("voice") or "").strip() or None,
        input_text=input_text,
        instructions=instructions,
        response_format=str(config.get("response_format") or "mp3").strip().lower(),
    )


def _generate_via_elevenlabs(
    provider: LLMProvider,
    model_name: str,
    input_text: str,
    instructions: str | None,
    multiple_speakers: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    del instructions
    del multiple_speakers
    voice = str(config.get("voice") or "").strip()
    if not voice:
        raise ValueError("Voice is required for ElevenLabs audio generation.")

    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    enable_logging = _coerce_bool(provider_settings.get("enable_logging"), True)
    return elevenlabs_generate_audio(
        api_key=provider.api_key,
        model=model_name,
        voice=voice,
        input_text=input_text,
        enable_logging=enable_logging,
        timeout=LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS,
    )


def _generate_via_google_aistudio(
    provider: LLMProvider,
    model_name: str,
    input_text: str,
    instructions: str | None,
    multiple_speakers: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    voice = str(config.get("voice") or "").strip()
    response_format = str(config.get("response_format") or "wav").strip().lower()
    return google_aistudio_generate_audio(
        provider=provider,
        model=model_name,
        voice=voice,
        input_text=input_text,
        instructions=instructions,
        multiple_speakers=multiple_speakers,
        response_format=response_format,
    )


def _generate_via_xai(
    provider: LLMProvider,
    model_name: str,
    input_text: str,
    instructions: str | None,
    multiple_speakers: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Generate speech through xAI's native TTS endpoint."""
    del model_name
    del instructions
    del multiple_speakers
    return xai_generate_audio(
        provider=provider,
        voice=str(config.get("voice") or "").strip() or None,
        input_text=input_text,
        response_format=str(config.get("response_format") or "mp3").strip(),
        language=str(config.get("language") or "auto").strip(),
        sample_rate=config.get("sample_rate"),
        bit_rate=config.get("bit_rate"),
        speed=config.get("speed"),
        optimize_streaming_latency=config.get("optimize_streaming_latency"),
        text_normalization=config.get("text_normalization"),
    )


PROVIDER_AUDIO_GENERATORS: dict[str, AudioGenerator] = {
    ProviderEnum.openai.value: _generate_via_openai,
    ProviderEnum.openai_responses.value: _generate_via_openai,
    ProviderEnum.openai_chat_completions.value: _generate_via_openai,
    ProviderEnum.openrouter.value: _generate_via_openrouter,
    ProviderEnum.elevenlabs.value: _generate_via_elevenlabs,
    ProviderEnum.google_aistudio.value: _generate_via_google_aistudio,
    ProviderEnum.xai.value: _generate_via_xai,
}


def get_transcription_models_for_provider(db: Session, provider_row: LLMProvider) -> list[str]:
    provider_type = str(provider_row.provider or "").strip()

    if provider_type in OPENAI_COMPATIBLE_TRANSCRIPTION_PROVIDER_TYPES:
        return get_openai_transcription_models(
            db=db,
            openai_provider_id=provider_row.id,
            openai_provider_type=provider_type,
        )
    if provider_type == ProviderEnum.google_aistudio.value:
        return get_google_aistudio_transcription_models(
            db=db,
            aistudio_provider_id=provider_row.id,
        )
    if provider_type == ProviderEnum.elevenlabs.value:
        return list(ELEVENLABS_TRANSCRIPTION_MODELS)
    if provider_type == ProviderEnum.xai.value:
        return list(XAI_TRANSCRIPTION_MODELS)
    return []


def get_transcription_runtime_for_provider(
    db: Session,
    provider_id: str,
) -> dict[str, Any]:
    normalized_provider_id = str(provider_id or "").strip()
    if not normalized_provider_id:
        raise HTTPException(status_code=400, detail="Transcription provider is not configured")

    provider = get_llm_provider(db, normalized_provider_id)
    if not provider:
        raise HTTPException(status_code=400, detail="Transcription provider not found")
    if not provider.api_key and not provider_api_key_is_optional(provider.provider):
        raise HTTPException(status_code=400, detail="Transcription provider API key is missing")

    provider_type = str(provider.provider or "").strip()
    if provider_type not in TRANSCRIPTION_PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported transcription provider type")

    if provider_type in OPENAI_COMPATIBLE_TRANSCRIPTION_PROVIDER_TYPES:
        allowed_formats = OPENAI_TRANSCRIPTION_SUPPORTED_FILE_FORMATS
        upload_limit_bytes = OPENAI_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES
    elif provider_type == ProviderEnum.google_aistudio.value:
        allowed_formats = GOOGLE_AISTUDIO_TRANSCRIPTION_SUPPORTED_FILE_FORMATS
        upload_limit_bytes = GOOGLE_AISTUDIO_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES
    elif provider_type == ProviderEnum.xai.value:
        allowed_formats = XAI_TRANSCRIPTION_SUPPORTED_FILE_FORMATS
        upload_limit_bytes = XAI_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES
    else:
        allowed_formats = ELEVENLABS_TRANSCRIPTION_SUPPORTED_FILE_FORMATS
        upload_limit_bytes = ELEVENLABS_TRANSCRIPTION_FILE_UPLOAD_LIMIT_BYTES

    return {
        "provider": provider,
        "provider_type": provider_type,
        "models": get_transcription_models_for_provider(db, provider),
        "allowed_formats": list(allowed_formats),
        "upload_limit_bytes": int(upload_limit_bytes),
    }


async def transcribe_audio_bytes_for_provider(
    provider: LLMProvider | TranscriptionProviderSnapshot,
    *,
    model_name: str,
    audio_bytes: bytes,
    filename: str,
) -> str:
    provider_type = str(provider.provider or "").strip()
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}

    if provider_type in OPENAI_COMPATIBLE_TRANSCRIPTION_PROVIDER_TYPES:
        return await transcribe_audio_bytes_openai(
            audio_bytes,
            filename,
            api_key=provider.api_key,
            model=model_name,
            base_url=provider_settings.get("base_url"),
            custom_headers=provider_settings.get("custom_headers"),
        )
    if provider_type == ProviderEnum.google_aistudio.value:
        return await transcribe_audio_bytes_google_aistudio(
            audio_bytes,
            filename,
            api_key=provider.api_key,
            model=model_name,
            api_version=provider_settings.get("api_version", "v1"),
        )
    if provider_type == ProviderEnum.elevenlabs.value:
        enable_logging = _coerce_bool(provider_settings.get("enable_logging"), True)
        return await transcribe_audio_bytes_elevenlabs(
            audio_bytes,
            filename,
            api_key=provider.api_key,
            model=model_name,
            enable_logging=enable_logging,
        )
    if provider_type == ProviderEnum.xai.value:
        return await transcribe_audio_bytes_xai(
            provider,
            audio_bytes,
            filename,
        )
    raise RuntimeError(f"Unsupported transcription provider type: {provider_type}")
