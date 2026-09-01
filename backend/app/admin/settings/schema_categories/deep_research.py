"""Schemas for Deep Research settings."""

from typing import Any, Literal

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, field_validator


class DeepResearchSettings(BaseModel):
    """Persisted greenfield settings for the v2 Deep Research tool."""

    execution_mode: Literal["custom", "native"] = "custom"
    model_id: str | None = None
    native_provider_id: str | None = None
    native_model_name: str | None = None
    max_revision_rounds: int = Field(default=2, ge=1, le=3)
    websearch_search_provider: str | None = None
    websearch_scrape_provider: str | None = None

    @field_validator(
        "execution_mode",
        "model_id",
        "native_provider_id",
        "native_model_name",
        "websearch_search_provider",
        "websearch_scrape_provider",
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


deep_research_schema = Sections(
    sections=[
        Section(
            title="Mode",
            description="Choose the custom workflow or a provider-native research adapter.",
            i18n_title="schema_deep_research_mode_title",
            i18n_description="schema_deep_research_mode_desc_v2",
            fields=[
                FieldSchema(
                    key="execution_mode",
                    label="Execution mode",
                    description="The custom workflow reuses Omlorix Web Search and Code Execution; native mode delegates research to a supported provider.",
                    i18n_label="schema_deep_research_execution_mode",
                    i18n_description="schema_deep_research_execution_mode_desc_v2",
                    type="select",
                    options=[
                        {
                            "value": "custom",
                            "label": "Custom workflow",
                            "i18n_label": "schema_deep_research_execution_mode_custom",
                        },
                        {
                            "value": "native",
                            "label": "Native provider mode",
                            "i18n_label": "schema_deep_research_execution_mode_native",
                        },
                    ],
                    default="custom",
                ),
            ],
        ),
        Section(
            title="Native Provider & Model",
            description="Choose which configured provider and model should run native deep research.",
            i18n_title="schema_deep_research_sec0_title",
            i18n_description="schema_deep_research_sec0_desc",
            fields=[
                FieldSchema(
                    key="native_provider_id",
                    label="Provider",
                    description="Select a Google AI Studio provider.",
                    i18n_label="schema_deep_research_provider_id",
                    i18n_description="schema_deep_research_provider_id_desc",
                    type="select",
                    options=[],
                    placeholder="Select a provider",
                    dependency="execution_mode",
                    dependency_value="native",
                ),
                FieldSchema(
                    key="native_model_name",
                    label="Model",
                    description="Select a deep research capable model for the chosen provider.",
                    i18n_label="schema_deep_research_model_name",
                    i18n_description="schema_deep_research_model_name_desc",
                    type="select",
                    options=[],
                    placeholder="Select a model",
                    dependency="execution_mode",
                    dependency_value="native",
                ),
            ],
        ),
        Section(
            title="Custom workflow",
            description="Choose the phase model, quality gate, and the same Web Search providers used by normal chat.",
            i18n_title="schema_deep_research_custom_sec_title",
            i18n_description="schema_deep_research_custom_sec_desc",
            fields=[
                FieldSchema(
                    key="model_id",
                    label="Research model",
                    description="Select the LLM used for planning, research, review, and revision.",
                    i18n_label="schema_deep_research_model_id_v2",
                    i18n_description="schema_deep_research_model_id_desc_v2",
                    type="select",
                    options=[],
                    placeholder="Select a model",
                    dependency="execution_mode",
                    dependency_value="custom",
                ),
                FieldSchema(
                    key="max_revision_rounds",
                    label="Maximum revision rounds",
                    description="Stop and block publication when the independent release gate still fails after this many revisions.",
                    i18n_label="schema_deep_research_max_revision_rounds",
                    i18n_description="schema_deep_research_max_revision_rounds_desc",
                    type="number",
                    default=2,
                    min_value=1,
                    max_value=3,
                    dependency="execution_mode",
                    dependency_value="custom",
                ),
                FieldSchema(
                    key="websearch_search_provider",
                    label="Web search provider",
                    description="The normal Omlorix Web Search provider used during custom Deep Research.",
                    i18n_label="schema_deep_research_websearch_search_provider",
                    i18n_description="schema_deep_research_websearch_search_provider_desc_v2",
                    type="select",
                    options=[],
                    placeholder="Select a web search provider",
                    dependency="execution_mode",
                    dependency_value="custom",
                ),
                FieldSchema(
                    key="websearch_scrape_provider",
                    label="Web scrape provider",
                    description="The normal Omlorix scrape provider used during custom Deep Research.",
                    i18n_label="schema_deep_research_websearch_scrape_provider",
                    i18n_description="schema_deep_research_websearch_scrape_provider_desc_v2",
                    type="select",
                    options=[],
                    placeholder="Select a web scrape provider",
                    dependency="execution_mode",
                    dependency_value="custom",
                ),
            ],
        ),
    ]
)
