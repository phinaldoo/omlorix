"""Schemas for music-generation settings."""

from typing import Any, Literal

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, conint, field_validator


class MusicGenerationSettings(BaseModel):
    provider_id: str | None = None
    model_name: str | None = None
    response_format: Literal["mp3", "wav"] | None = "mp3"
    enable_reference_images: bool = False
    max_reference_images: conint(ge=1, le=10) = 3

    @field_validator("provider_id", "model_name", "response_format", mode="before")
    @classmethod
    def _strip_optional_music_strings(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value).strip() or None


music_generation_schema = Sections(
    sections=[
        Section(
            title="Provider & Model",
            description="Choose which configured provider and Lyria model to use for music generation.",
            i18n_title="schema_music_generation_sec0_title",
            i18n_description="schema_music_generation_sec0_desc",
            fields=[
                FieldSchema(
                    key="provider_id",
                    label="Provider",
                    description="Select a Google AI Studio provider configured for Lyria music generation.",
                    i18n_label="schema_music_generation_provider_id",
                    i18n_description="schema_music_generation_provider_id_desc",
                    type="select",
                    options=[],
                    placeholder="Select a provider",
                ),
            ],
        ),
    ]
)


def build_music_generation_model_field(provider_id: str) -> FieldSchema:
    """Build the model step after a music provider is selected."""

    return FieldSchema(
        key="model_name",
        label="Model",
        description="Select the Lyria music model available for the chosen provider.",
        i18n_label="schema_music_generation_model_name",
        i18n_description="schema_music_generation_model_name_desc",
        type="select",
        options=[],
        placeholder="Select a model",
        dependency="provider_id",
        dependency_value=provider_id,
    )
