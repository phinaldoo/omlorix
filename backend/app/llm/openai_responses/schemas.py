from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.llm.openai.custom_headers import normalize_custom_header_entries
from app.utils.schemas import (
    FieldSchema,
    Section,
    Sections,
)


class OpenaiResponsesSettings(BaseModel):
    base_url: str | None = None
    custom_headers: list[str] = Field(default_factory=list)
    disable_background_sync: bool = False

    @field_validator("custom_headers", mode="before")
    @classmethod
    def _normalize_custom_headers(cls, value: Any) -> list[str]:
        return normalize_custom_header_entries(value)


class OpenaiResponsesListModelsByok(OpenaiResponsesSettings):
    api_key: str

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("API key is required.")
        return value.strip()




OPENAI_RESPONSES_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this OpenAI connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name for this OpenAI provider configuration.",
                    type="string",
                    placeholder="E.g. My custom OpenAI Responses provider",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="icon",
                    label="Provider icon",
                    description="Select a preset icon or provide a custom SVG for this provider.",
                    type="string",
                    default="openai",
                ),
            ],
        ),
        Section(
            title="API credentials & endpoints",
            description="Configure the credentials and optional routing used for OpenAI requests.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="OpenAI API key used for authenticating requests.",
                    type="string",
                    placeholder="E.g. sk-openai-xxxxxxxxxxxxxxxx",
                    required=True,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.base_url",
                    label="Base URL",
                    description="Optional base URL to target a compatible OpenAI endpoint.",
                    type="string",
                    placeholder="E.g. https://api.openai.com/v1",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.custom_headers",
                    label="Custom HTTP headers",
                    description="Optional headers sent with every provider request. Add one entry per header in the format Header-Name: value.",
                    type="string_list",
                    placeholder="Add Header-Name: value and press Enter",
                    default=[],
                ),
            ],
        ),
        Section(
            title="Request handling",
            description="Set operational controls applied to every OpenAI API call.",
            fields=[
                FieldSchema(
                    key="settings.disable_background_sync",
                    label="Disable regular provider requests",
                    description="Skip recurring background requests to this provider, such as periodic model list synchronization.",
                    type="boolean",
                    default=False,
                    hide_on_byok=True,
                ),
            ],
        ),
    ]
)
