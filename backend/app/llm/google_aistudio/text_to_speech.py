from __future__ import annotations

import base64
import io
import logging
import re
import wave
from typing import Any

from fastapi import HTTPException
from google.genai import errors as genai_errors
from google.genai import types

from app.llm.audio_generation_pricing import (
    build_audio_generation_model_option,
    calculate_audio_generation_cost,
)
from app.llm.google_aistudio.utils import build_aistudio_generate_content_config, get_aistudio_client
from app.llm.models import LLMProvider
from app.utils.schemas import FieldSchema, Option, Section, Sections


logger = logging.getLogger(__name__)


GOOGLE_AISTUDIO_TTS_FALLBACK_MODELS = [
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
]

GOOGLE_AISTUDIO_TTS_VOICES = [
    "Zephyr",
    "Puck",
    "Charon",
    "Kore",
    "Fenrir",
    "Leda",
    "Orus",
    "Aoede",
    "Callirrhoe",
    "Autonoe",
    "Enceladus",
    "Iapetus",
    "Umbriel",
    "Algieba",
    "Despina",
    "Erinome",
    "Algenib",
    "Rasalgethi",
    "Laomedeia",
    "Achernar",
    "Alnilam",
    "Schedar",
    "Gacrux",
    "Pulcherrima",
    "Achird",
    "Zubenelgenubi",
    "Vindemiatrix",
    "Sadachbia",
    "Sadaltager",
    "Sulafat",
]

GOOGLE_AISTUDIO_TTS_RESPONSE_FORMATS = ["wav"]
GOOGLE_AISTUDIO_TTS_DEFAULT_VOICE = "Kore"
GOOGLE_AISTUDIO_TTS_DEFAULT_FORMAT = "wav"
GOOGLE_AISTUDIO_TTS_DEFAULT_SECONDARY_VOICE = "Puck"
GOOGLE_AISTUDIO_TTS_MAX_SPEAKERS = 2

_PCM_SAMPLE_RATE = 24_000
_PCM_SAMPLE_WIDTH_BYTES = 2
_PCM_CHANNELS = 1
_PCM_MIME_TYPES = {
    "audio/pcm",
    "audio/l16",
    "audio/raw",
    "application/octet-stream",
}
_WAV_MIME_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
}
_DIALOGUE_SPEAKER_PATTERN = re.compile(r"^\s*([^:\n]{1,60})\s*:\s*.+$", re.MULTILINE)


def _normalize_supported_actions(raw: Any) -> set[str]:
    """Normalize supported actions."""
    values: set[str] = set()
    if not isinstance(raw, (list, tuple, set)):
        return values
    for item in raw:
        if isinstance(item, str):
            values.add(item.strip().lower())
        else:
            maybe_value = getattr(item, "value", None)
            if isinstance(maybe_value, str):
                values.add(maybe_value.strip().lower())
            else:
                values.add(str(item).strip().lower())
    return values


def _normalize_inline_audio_bytes(value: Any) -> bytes:
    """Normalize inline audio bytes."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return b""
        try:
            return base64.b64decode(raw)
        except Exception:
            return b""
    return b""


def _extract_inline_data(part: Any) -> tuple[bytes, str | None]:
    """Extract inline data."""
    if isinstance(part, dict):
        inline_data = part.get("inline_data") or part.get("inlineData")
    else:
        inline_data = getattr(part, "inline_data", None)

    if inline_data is None:
        return b"", None

    if isinstance(inline_data, dict):
        data = inline_data.get("data")
        mime_type = inline_data.get("mime_type") or inline_data.get("mimeType")
    else:
        data = getattr(inline_data, "data", None)
        mime_type = getattr(inline_data, "mime_type", None)

    audio_bytes = _normalize_inline_audio_bytes(data)
    normalized_mime = str(mime_type or "").strip().lower() or None
    return audio_bytes, normalized_mime


def _extract_audio_from_response(response: Any) -> tuple[bytes, str | None]:
    """Extract audio from response."""
    candidates = getattr(response, "candidates", None)
    if candidates is None:
        candidates = []
    elif not isinstance(candidates, list):
        try:
            candidates = list(candidates)
        except Exception:
            candidates = []

    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None)
        if parts is None:
            continue
        if not isinstance(parts, list):
            try:
                parts = list(parts)
            except Exception:
                continue
        for part in parts:
            audio_bytes, mime_type = _extract_inline_data(part)
            if audio_bytes:
                return audio_bytes, mime_type

    raise RuntimeError("Google AI Studio text-to-speech did not return audio data")


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """Convert PCM to WAV."""
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_writer:
            wav_writer.setnchannels(_PCM_CHANNELS)
            wav_writer.setsampwidth(_PCM_SAMPLE_WIDTH_BYTES)
            wav_writer.setframerate(_PCM_SAMPLE_RATE)
            wav_writer.writeframes(pcm_bytes)
        return buffer.getvalue()


def _build_tts_prompt(input_text: str, instructions: str | None, multiple_speakers: bool) -> str:
    """Build TTS prompt."""
    text_value = str(input_text or "").strip()
    if not text_value:
        raise ValueError("input text is required for Google AI Studio text-to-speech")

    instructions_value = str(instructions or "").strip()
    if not instructions_value:
        return text_value

    if multiple_speakers:
        return (
            f"{instructions_value}\n\n"
            "Generate expressive dialogue audio for the script below while preserving speaker labels exactly:\n"
            f"{text_value}"
        )

    return f"{instructions_value}\n\nRead the following text aloud exactly as written:\n{text_value}"


def normalize_google_aistudio_tts_voice(voice: str | None) -> str:
    """Normalize Google AI Studio TTS voice."""
    requested = str(voice or "").strip()
    if requested in GOOGLE_AISTUDIO_TTS_VOICES:
        return requested
    return GOOGLE_AISTUDIO_TTS_DEFAULT_VOICE


def normalize_google_aistudio_tts_response_format(response_format: str | None) -> str:
    """Normalize Google AI Studio TTS response format."""
    requested = str(response_format or "").strip().lower()
    if requested in GOOGLE_AISTUDIO_TTS_RESPONSE_FORMATS:
        return requested
    return GOOGLE_AISTUDIO_TTS_DEFAULT_FORMAT


def _pick_secondary_voice(primary_voice: str) -> str:
    """Pick secondary voice."""
    preferred_order = [GOOGLE_AISTUDIO_TTS_DEFAULT_SECONDARY_VOICE] + list(GOOGLE_AISTUDIO_TTS_VOICES)
    for candidate in preferred_order:
        normalized = str(candidate or "").strip()
        if normalized and normalized != primary_voice:
            return normalized
    return primary_voice


def _extract_dialogue_speakers(input_text: str) -> list[str]:
    """Extract dialogue speakers."""
    text = str(input_text or "")
    speakers: list[str] = []
    seen: set[str] = set()
    for match in _DIALOGUE_SPEAKER_PATTERN.finditer(text):
        speaker = str(match.group(1) or "").strip()
        if not speaker or speaker in seen:
            continue
        seen.add(speaker)
        speakers.append(speaker)
    return speakers


def _build_multi_speaker_voice_config(
    speaker_names: list[str],
    primary_voice: str,
) -> Any:
    """Build multi-speaker voice config."""
    if len(speaker_names) != GOOGLE_AISTUDIO_TTS_MAX_SPEAKERS:
        raise ValueError("Google AI Studio multi-speaker mode requires exactly 2 speakers.")

    secondary_voice = _pick_secondary_voice(primary_voice)
    voice_names = [primary_voice, secondary_voice]
    speaker_voice_configs: list[Any] = []

    for idx, speaker_name in enumerate(speaker_names):
        voice_name = voice_names[idx]
        if hasattr(types, "SpeakerVoiceConfig"):
            speaker_voice_configs.append(
                types.SpeakerVoiceConfig(
                    speaker=speaker_name,
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                    ),
                )
            )
        else:
            speaker_voice_configs.append(
                {
                    "speaker": speaker_name,
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": voice_name},
                    },
                }
            )

    if hasattr(types, "MultiSpeakerVoiceConfig"):
        return types.MultiSpeakerVoiceConfig(speaker_voice_configs=speaker_voice_configs)
    return {"speaker_voice_configs": speaker_voice_configs}


def _get_provider_aistudio_connection_settings(provider: LLMProvider) -> dict[str, Any]:
    """Get provider AI Studio connection settings."""
    settings = provider.settings if isinstance(provider.settings, dict) else {}
    return {
        "api_key": str(provider.api_key or "").strip(),
        "api_version": str(settings.get("api_version") or "v1").strip() or "v1",
    }


def google_aistudio_text_to_speech_models_list_for_provider(
    provider: LLMProvider,
) -> list[dict[str, Any]]:
    """Google AI Studio text to speech models list for provider."""
    connection = _get_provider_aistudio_connection_settings(provider)
    return google_aistudio_text_to_speech_models_list(
        api_key=connection["api_key"],
        api_version=connection["api_version"],
    )


def google_aistudio_text_to_speech_models_list(
    *,
    api_key: str,
    api_version: str = "v1",
) -> list[dict[str, Any]]:
    """Google AI Studio text to speech models list."""
    token = str(api_key or "").strip()
    if not token:
        raise ValueError("Google AI Studio api_key is required")

    models: list[dict[str, Any]] = []
    model_ids_seen: set[str] = set()

    try:
        client = get_aistudio_client(
            None,
            api_key=token,
            api_version=api_version,
        )
        raw_models = client.models.list()
        for item in raw_models:
            model_path = str(getattr(item, "name", "")).strip()
            model_id = model_path.split("/")[-1].strip()
            if not model_id or model_id in model_ids_seen:
                continue

            supported_actions = _normalize_supported_actions(getattr(item, "supported_actions", None))
            if supported_actions and "generatecontent" not in supported_actions:
                continue

            if "tts" not in model_id.lower():
                continue

            model_ids_seen.add(model_id)
            display_name = str(getattr(item, "display_name", "") or model_id).strip() or model_id
            models.append(
                {
                    "id": model_id,
                    "name": display_name,
                    "voices": list(GOOGLE_AISTUDIO_TTS_VOICES),
                    "response_formats": list(GOOGLE_AISTUDIO_TTS_RESPONSE_FORMATS),
                    "support_custom_instructions": True,
                }
            )
    except Exception:
        logger.exception("Failed to fetch Google AI Studio TTS models; using fallback list.")

    if models:
        return models

    fallback: list[dict[str, Any]] = []
    for model_id in GOOGLE_AISTUDIO_TTS_FALLBACK_MODELS:
        fallback.append(
            {
                "id": model_id,
                "name": model_id,
                "voices": list(GOOGLE_AISTUDIO_TTS_VOICES),
                "response_formats": list(GOOGLE_AISTUDIO_TTS_RESPONSE_FORMATS),
                "support_custom_instructions": True,
            }
        )
    return fallback


def google_aistudio_generate_audio(
    *,
    provider: LLMProvider,
    model: str,
    voice: str | None,
    input_text: str,
    instructions: str | None,
    response_format: str | None,
    multiple_speakers: bool = False,
) -> dict[str, Any]:
    """Google AI Studio generate audio."""
    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("model is required for Google AI Studio text-to-speech")

    normalized_voice = normalize_google_aistudio_tts_voice(voice)
    normalized_format = normalize_google_aistudio_tts_response_format(response_format)

    prompt = _build_tts_prompt(input_text, instructions, bool(multiple_speakers))
    connection = _get_provider_aistudio_connection_settings(provider)
    speaker_names: list[str] = []
    if multiple_speakers:
        speaker_names = _extract_dialogue_speakers(input_text)
        if len(speaker_names) < GOOGLE_AISTUDIO_TTS_MAX_SPEAKERS:
            raise ValueError(
                "Google AI Studio multi-speaker mode requires dialogue input with exactly 2 speaker labels. "
                "Format each line like 'SpeakerName: text'."
            )
        if len(speaker_names) > GOOGLE_AISTUDIO_TTS_MAX_SPEAKERS:
            raise ValueError(
                "Google AI Studio multi-speaker mode supports exactly 2 unique speakers in one request."
            )

    try:
        if multiple_speakers:
            speech_config = types.SpeechConfig(
                multi_speaker_voice_config=_build_multi_speaker_voice_config(
                    speaker_names=speaker_names,
                    primary_voice=normalized_voice,
                )
            )
        else:
            speech_config = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=normalized_voice
                    )
                )
            )

        client = get_aistudio_client(
            None,
            api_key=connection["api_key"],
            api_version=connection["api_version"],
        )
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=build_aistudio_generate_content_config(
                None,
                response_modalities=["AUDIO"],
                speech_config=speech_config,
            ),
        )
    except HTTPException as exc:
        detail = str(exc.detail or "Unknown error")
        raise RuntimeError(f"Google AI Studio text-to-speech failed: {detail}") from exc
    except genai_errors.ClientError as exc:
        detail = getattr(exc, "message", str(exc))
        raise RuntimeError(f"Google AI Studio text-to-speech failed: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Google AI Studio text-to-speech failed: {exc}") from exc

    audio_bytes, mime_type = _extract_audio_from_response(response)
    if not audio_bytes:
        raise RuntimeError("Google AI Studio text-to-speech returned an empty audio payload")

    mime_type_value = (mime_type or "").lower()
    if mime_type_value in _WAV_MIME_TYPES or mime_type_value.startswith("audio/wav"):
        wav_bytes = audio_bytes
    elif (
        mime_type_value in _PCM_MIME_TYPES
        or mime_type_value.startswith("audio/pcm")
        or mime_type_value.startswith("audio/l16")
        or not mime_type_value
    ):
        wav_bytes = _pcm_to_wav(audio_bytes)
    else:
        wav_bytes = _pcm_to_wav(audio_bytes)

    usage_metadata = getattr(response, "usage_metadata", None)
    input_text_tokens = int(getattr(usage_metadata, "prompt_token_count", 0) or 0) if usage_metadata else 0
    output_audio_tokens = int(getattr(usage_metadata, "candidates_token_count", 0) or 0) if usage_metadata else 0
    cost_details = calculate_audio_generation_cost(
        "google_aistudio",
        model_name,
        input_text=input_text,
        input_text_tokens=input_text_tokens,
        output_audio_tokens=output_audio_tokens,
    )

    return {
        "audio_bytes": wav_bytes,
        "model": model_name,
        "voice": normalized_voice,
        "response_format": normalized_format,
        "file_type": "audio/wav",
        "extension": "wav",
        "multiple_speakers": bool(multiple_speakers),
        "speakers": speaker_names,
        "cost": cost_details.get("cost") if isinstance(cost_details, dict) else None,
        "cost_details": cost_details,
    }


def get_audio_generation_schema_part_1(db, provider_id: str):
    """Get audio generation schema part 1."""
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []

    if provider:
        try:
            for item in google_aistudio_text_to_speech_models_list_for_provider(provider):
                model_id = str(item.get("id") or "").strip()
                model_name = str(item.get("name") or model_id).strip() or model_id
                if model_id:
                    label, metadata = build_audio_generation_model_option(
                        "google_aistudio",
                        model_id,
                        label=model_name,
                    )
                    model_options.append(Option(value=model_id, label=label, metadata=metadata))
        except Exception:
            logger.exception(
                "Failed to fetch Google AI Studio TTS models for provider '%s'",
                provider_id,
            )

    return Sections(
        sections=[
            Section(
                title="Google AI Studio Audio Generation",
                i18n_title="llm.shared.section_google_ai_studio.title",
                description="Select the Gemini text-to-speech model.",
                i18n_description="llm.shared.section_select_the_gemini.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        description="Choose which Google AI Studio text-to-speech model to use.",
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


def get_audio_generation_schema_part_2(model_name: str):
    """Get audio generation schema part 2."""
    del model_name
    voice_options = [Option(value=voice, label=voice) for voice in GOOGLE_AISTUDIO_TTS_VOICES]
    format_options = [
        Option(value=response_format, label=response_format.upper())
        for response_format in GOOGLE_AISTUDIO_TTS_RESPONSE_FORMATS
    ]

    return Sections(
        sections=[
            Section(
                title="Google AI Studio Audio Generation",
                i18n_title="llm.shared.section_google_ai_studio.title",
                description="Choose a default Gemini voice and output format.",
                i18n_description="llm.shared.section_choose_a_default.description",
                fields=[
                    FieldSchema(
                        key="voice",
                        label="Voice",
                        description="Default Gemini voice used for generated speech.",
                        i18n_label="schema_audio_generation_voice",
                        i18n_description="schema_audio_generation_voice_desc",
                        type="select",
                        options=voice_options,
                        default=GOOGLE_AISTUDIO_TTS_DEFAULT_VOICE,
                        placeholder="Select a voice",
                        i18n_placeholder="llm.shared.voice.placeholder",
                        required=True,
                    ),
                    FieldSchema(
                        key="response_format",
                        label="Audio Format",
                        description="Google AI Studio returns PCM audio, stored as WAV files.",
                        i18n_label="schema_audio_generation_response_format",
                        i18n_description="schema_audio_generation_response_format_desc",
                        type="select",
                        options=format_options,
                        default=GOOGLE_AISTUDIO_TTS_DEFAULT_FORMAT,
                        placeholder="Select an audio format",
                        i18n_placeholder="llm.shared.response_format.placeholder",
                    ),
                ],
            )
        ]
    )
