"""Schemas for slide-presentation settings."""

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel


class SlidePresentationSettings(BaseModel):
    presentation_model_id: str | None = None


slide_presentation_schema = Sections(
    sections=[
        Section(
            title="LLM Models",
            description="Configure the model that creates and visually refines presentation HTML.",
            i18n_title="schema_slide_presentation_sec0_title",
            i18n_description="schema_slide_presentation_sec0_desc",
            fields=[
                FieldSchema(
                    key="presentation_model_id",
                    label="Presentation Model",
                    description="Model that creates, reviews, and refines the final presentation HTML.",
                    type="select",
                    options=[],
                    placeholder="Select a model",
                    i18n_label="schema_slide_presentation_presentation_model_id",
                    i18n_description="schema_slide_presentation_presentation_model_id_desc",
                ),
            ],
        ),
    ]
)
