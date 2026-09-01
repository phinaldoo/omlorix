"""Schemas for audio-generation settings."""

from typing import Any, Literal

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, field_validator


class AudioGenerationSettings(BaseModel):
    provider_id: str | None = None
    model_name: str | None = None
    voice: str | None = None
    # Keep the shared settings envelope broad enough for every registered
    # provider. xAI names its raw codecs ``pcm``, ``mulaw``, and ``alaw``;
    # other providers continue to use the existing ``pcm16`` spelling.
    response_format: (
        Literal[
            "mp3",
            "wav",
            "flac",
            "aac",
            "opus",
            "pcm16",
            "pcm",
            "mulaw",
            "alaw",
        ]
        | None
    ) = "mp3"
    # xAI exposes these batch-synthesis controls in addition to the shared
    # voice and response format. They remain nullable so other TTS providers
    # do not inherit xAI-specific defaults in persisted configuration.
    language: (
        Literal[
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
        | None
    ) = None
    sample_rate: Literal[8000, 16000, 22050, 24000, 44100, 48000] | None = None
    bit_rate: Literal[32000, 64000, 96000, 128000, 192000] | None = None
    speed: float | None = Field(default=None, ge=0.7, le=1.5)
    optimize_streaming_latency: Literal[0, 1, 2] | None = None
    text_normalization: bool | None = None

    @field_validator(
        "provider_id",
        "model_name",
        "voice",
        "response_format",
        "language",
        mode="before",
    )
    @classmethod
    def _strip_optional_strings(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value).strip() or None

    @field_validator("text_normalization", mode="before")
    @classmethod
    def _coerce_optional_text_normalization(cls, value: Any) -> bool | None:
        """Accept checkbox-compatible boolean spellings while preserving null."""
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)


audio_generation_schema = Sections(
    sections=[
        Section(
            title="Provider & Model",
            description="Choose which configured provider and model to use for audio generation.",
            i18n_title="schema_audio_generation_sec0_title",
            i18n_description="schema_audio_generation_sec0_desc",
            fields=[
                FieldSchema(
                    key="provider_id",
                    label="Provider",
                    description="Select a configured text-to-speech provider.",
                    i18n_label="schema_audio_generation_provider_id",
                    i18n_description="schema_audio_generation_provider_id_desc_v2",
                    type="select",
                    options=[],
                    placeholder="Select a provider",
                ),
            ],
        ),
    ]
)


def build_audio_generation_model_field(provider_id: str) -> FieldSchema:
    """Build the model step after an audio provider is selected."""

    return FieldSchema(
        key="model_name",
        label="Model",
        description="Select the text-to-speech model available for the chosen provider.",
        i18n_label="schema_audio_generation_model_name",
        i18n_description="schema_audio_generation_model_name_desc",
        type="select",
        options=[],
        placeholder="Select a model",
        dependency="provider_id",
        dependency_value=provider_id,
    )
