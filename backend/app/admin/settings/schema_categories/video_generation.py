"""Schemas for video-generation settings."""

from typing import Any

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, conint, field_validator


class VideoGenerationSettings(BaseModel):
    provider_id: str | None = None
    model_name: str | None = None
    # Providers clamp to their own narrower ranges. The shared envelope must
    # permit xAI's documented one-second minimum.
    duration_seconds: conint(ge=1, le=120) | None = None
    size: str | None = "720x1280"
    aspect_ratio: str | None = None
    resolution: str | None = None
    seed: int | None = None
    generate_audio: bool | None = None
    enable_reference_files: bool = False
    timeout_seconds: conint(ge=60, le=3600) = 1000
    poll_interval_seconds: conint(ge=1, le=30) = 5
    max_retries: conint(ge=0, le=10) = 2

    @field_validator(
        "provider_id",
        "model_name",
        "size",
        "aspect_ratio",
        "resolution",
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


video_generation_schema = Sections(
    sections=[
        Section(
            title="Provider & Model",
            description="Choose which configured provider and model to use for video generation.",
            i18n_title="schema_video_generation_sec0_title",
            i18n_description="schema_video_generation_sec0_desc",
            fields=[
                FieldSchema(
                    key="provider_id",
                    label="Provider",
                    description=(
                        "Select a custom OpenAI-compatible, OpenRouter, "
                        "Google AI Studio, or xAI provider."
                    ),
                    i18n_label="schema_video_generation_provider_id",
                    i18n_description="schema_video_generation_provider_id_desc",
                    type="select",
                    options=[],
                    placeholder="Select a provider",
                ),
            ],
        ),
    ]
)


def build_video_generation_model_field(provider_id: str) -> FieldSchema:
    """Build the model step after a video provider is selected."""

    return FieldSchema(
        key="model_name",
        label="Model",
        description="Select the video model available for the chosen provider.",
        i18n_label="schema_video_generation_model_name",
        i18n_description="schema_video_generation_model_name_desc",
        type="select",
        options=[],
        placeholder="Select a model",
        dependency="provider_id",
        dependency_value=provider_id,
    )
