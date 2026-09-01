"""Schemas for image-generation settings."""

from typing import Any, Dict

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, field_validator


class ImageGenerationSettings(BaseModel):
    provider_id: str | None = None
    model_name: str | None = None
    settings: Dict[str, Any] | None = Field(default_factory=dict)

    @field_validator("provider_id", "model_name", mode="before")
    @classmethod
    def _strip_optional_image_generation_strings(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value).strip() or None

    @field_validator("settings", mode="before")
    @classmethod
    def _coerce_image_generation_settings(cls, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}


image_generation_schema = Sections(
    sections=[
        Section(
            title="Provider & Model",
            description="Choose which configured provider and model to use for image generation.",
            i18n_title="image_generation_wizard_title",
            i18n_description="image_generation_card_subtitle",
            fields=[
                FieldSchema(
                    key="provider_id",
                    label="Provider",
                    description="Select a configured image generation provider.",
                    i18n_label="image_generation_label_provider",
                    i18n_description="image_generation_label_provider_desc",
                    type="select",
                    options=[],
                    placeholder="Select a provider",
                    i18n_placeholder="image_generation_provider_select_default",
                ),
            ],
        ),
    ]
)


def build_image_generation_model_field(provider_id: str) -> FieldSchema:
    """Build the model step after an image provider is selected."""

    return FieldSchema(
        key="model_name",
        label="Model",
        description="Select the image model available for the chosen provider.",
        i18n_label="image_generation_label_model",
        i18n_description="image_generation_label_model_desc",
        type="select",
        options=[],
        placeholder="Select a model",
        i18n_placeholder="image_generation_model_select_default",
        dependency="provider_id",
        dependency_value=provider_id,
    )
