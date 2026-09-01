from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.llm.openai.custom_headers import normalize_custom_header_entries

from app.utils.schemas import (
    FieldSchema,
    Section,
    Sections,
)


class AzureOpenAISettings(BaseModel):
    azure_endpoint: str
    api_version: str | None = None
    custom_headers: list[str] = Field(default_factory=list)
    disable_background_sync: bool = False
    enable_auto_delete_missing_models: bool = False
    enable_notify_model_changes: bool = True

    @field_validator("custom_headers", mode="before")
    @classmethod
    def _normalize_custom_headers(cls, value: Any) -> list[str]:
        headers = normalize_custom_header_entries(value)
        if len(headers) > 10:
            raise ValueError("Azure OpenAI supports at most 10 custom headers.")
        return headers


class AzureOpenAIListModelsByok(AzureOpenAISettings):
    api_key: str


AZURE_OPENAI_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this Azure OpenAI connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name for this Azure OpenAI provider configuration.",
                    type="string",
                    placeholder="E.g. My Azure OpenAI provider",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="API credentials & endpoints",
            description="Configure the Azure OpenAI resource endpoint and credentials used for requests.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="Azure OpenAI resource key used for authenticating requests.",
                    type="string",
                    placeholder="E.g. xxxxxxxx",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.azure_endpoint",
                    label="Azure endpoint",
                    description="Azure OpenAI resource URL, for example https://my-resource.openai.azure.com.",
                    type="string",
                    placeholder="E.g. https://my-resource.openai.azure.com",
                ),
                FieldSchema(
                    key="settings.api_version",
                    label="API version",
                    description="Optional Azure API version appended as api-version for requests that require it.",
                    type="string",
                    placeholder="E.g. preview or 2025-04-01-preview",
                ),
                FieldSchema(
                    key="settings.custom_headers",
                    label="Custom HTTP headers",
                    description="Optional headers sent with every Azure OpenAI request. Add one entry per header in the format Header-Name: value. Azure currently supports at most 10 custom headers.",
                    type="string_list",
                    placeholder="Add Header-Name: value and press Enter",
                    default=[],
                ),
            ],
        ),
        Section(
            title="Request handling",
            description="Set operational controls applied to every Azure OpenAI API call.",
            fields=[
                FieldSchema(
                    key="settings.disable_background_sync",
                    label="Disable regular provider requests",
                    description="Skip recurring background requests to this provider, such as periodic model list synchronization.",
                    type="boolean",
                    default=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_auto_delete_missing_models",
                    label="Auto-delete missing models",
                    description="Automatically remove provider models that no longer appear in Azure model listings.",
                    type="boolean",
                    default=False,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_notify_model_changes",
                    label="Notify model changes",
                    description="Send notifications when Azure OpenAI model availability changes.",
                    type="boolean",
                    default=True,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
            ],
        ),
    ]
)
