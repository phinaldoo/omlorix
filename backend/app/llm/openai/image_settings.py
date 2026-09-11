"""Validated output controls shared by native OpenAI image generation and edits."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.utils.schemas import FieldAttributes, FieldSchema, Option


class ImageOutputSettings(BaseModel):
    output_format: Literal["png", "jpeg", "webp"] = "png"
    output_compression: int | None = Field(default=None, ge=0, le=100)
    background: Literal["auto", "opaque", "transparent"] = "auto"
    moderation: Literal["auto", "low"] = "auto"

    @model_validator(mode="after")
    def validate_format(self):
        if self.background == "transparent" and self.output_format == "jpeg":
            raise ValueError("Transparent backgrounds require PNG or WebP.")
        if self.output_format == "png":
            self.output_compression = None
        return self


def image_output_fields() -> list[FieldSchema]:
    return [
        FieldSchema(
            key="settings.output_format", label="Output format", description="",
            i18n_label="openai_image_output_format", type="select", default="png",
            options=[Option(value=value, label=value.upper(), translatable=False) for value in ("png", "jpeg", "webp")],
        ),
        FieldSchema(
            key="settings.output_compression", label="Compression", description="JPEG and WebP compression (0–100).",
            i18n_label="openai_image_compression", i18n_description="openai_image_compression_help",
            type="number", attributes=FieldAttributes(min=0, max=100, step=1),
        ),
        FieldSchema(
            key="settings.background", label="Background", description="Transparent backgrounds require PNG or WebP.",
            i18n_label="openai_image_background", type="select", default="auto",
            options=[
                Option(value="auto", label="Auto", i18n_label="llm.shared.settings.quality.option.auto"),
                Option(value="opaque", label="Opaque", i18n_label="openai_image_opaque"),
                Option(value="transparent", label="Transparent", i18n_label="openai_image_transparent"),
            ],
        ),
        FieldSchema(
            key="settings.moderation", label="Moderation", description="",
            i18n_label="openai_image_moderation", type="select", default="auto",
            options=[
                Option(value="auto", label="Auto", i18n_label="llm.shared.settings.quality.option.auto"),
                Option(value="low", label="Low", i18n_label="llm.shared.settings.quality.option.low"),
            ],
        ),
        FieldSchema(
            key="settings.custom_size", label="Custom size", description="Optional WIDTHxHEIGHT; multiples of 16, at most 3840 pixels per edge, 655360–8294400 pixels total, aspect ratio 1:3–3:1.",
            i18n_label="openai_image_custom_size", i18n_description="openai_image_custom_size_help",
            type="string", default="", max_length=9,
        ),
    ]
