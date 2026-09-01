"""Schemas for global model-picker defaults."""

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field


class ModelDefaultsSettings(BaseModel):
    """Validate the administrator's global model picker defaults."""

    default_model: str | None = None
    default_pinned_models: list[str] = Field(default_factory=list, max_length=8)


models_schema = Sections(
    sections=[
        Section(
            title="Default model",
            description="Choose the model defaults applied before a user customizes their own selection.",
            i18n_title="schema_models_sec0_title",
            i18n_description="schema_models_sec0_desc",
            fields=[
                FieldSchema(
                    key="default_model",
                    label="Default model",
                    description="Only models visible to everyone can be selected.",
                    type="select",
                    options=[],
                    placeholder="Choose a default model",
                    i18n_label="schema_models_default_model",
                    i18n_description="schema_models_default_model_desc",
                ),
                FieldSchema(
                    key="default_pinned_models",
                    label="Default pinned models",
                    description="Shown until a user customizes their pinned models. Only models visible to everyone can be selected.",
                    type="select",
                    options=[],
                    multiple=True,
                    placeholder="Choose up to 8 default pinned models",
                ),
            ],
        ),
    ]
)
