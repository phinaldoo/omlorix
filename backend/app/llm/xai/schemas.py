"""Provider and request schemas for xAI."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.llm.openai.custom_headers import normalize_custom_header_entries
from app.llm.openai.schemas import OpenAIModelSettings
from app.utils.schemas import FieldSchema, Section, Sections


XAI_DEFAULT_BASE_URL = "https://api.x.ai/v1"


class XAISettings(BaseModel):
    """Connection settings shared by every xAI capability."""

    base_url: str = XAI_DEFAULT_BASE_URL
    custom_headers: list[str] = Field(default_factory=list)
    disable_background_sync: bool = False

    @field_validator("base_url", mode="before")
    @classmethod
    def _default_base_url(cls, value: Any) -> str:
        """Keep old or incomplete payloads pinned to xAI's public API."""

        normalized = str(value or "").strip().rstrip("/")
        return normalized or XAI_DEFAULT_BASE_URL

    @field_validator("custom_headers", mode="before")
    @classmethod
    def _normalize_custom_headers(cls, value: Any) -> list[str]:
        """Validate optional gateway headers with the shared safe parser."""

        return normalize_custom_header_entries(value)


class XAIListModelsByok(XAISettings):
    """BYOK model-list request for xAI."""

    api_key: str

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: str) -> str:
        """Reject an empty xAI credential before making an outbound request."""

        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("API key is required.")
        return normalized


# xAI chat models use the Responses API, so their per-model controls are the
# same controls Omlorix already applies to OpenAI-compatible Responses models.
XAIModelSettings = OpenAIModelSettings


XAI_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            i18n_title="schema_backend_provider_identity",
            description="Name how this xAI connection appears across the admin UI.",
            i18n_description="schema_xai_provider_identity_desc",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    i18n_label="schema_backend_provider_name",
                    description="Display name for this xAI provider configuration.",
                    i18n_description="schema_xai_provider_name_desc",
                    type="string",
                    placeholder="E.g. My xAI provider",
                    i18n_placeholder="schema_xai_provider_name_placeholder",
                ),
            ],
        ),
        Section(
            title="API credentials & endpoint",
            i18n_title="schema_backend_api_credentials_and_endpoint",
            description="Configure the credential and endpoint used for xAI requests.",
            i18n_description="schema_xai_credentials_desc",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    i18n_label="schema_backend_api_key",
                    description="xAI API key used for authenticating requests.",
                    i18n_description="schema_xai_api_key_desc",
                    type="string",
                    placeholder="E.g. xai-xxxxxxxxxxxxxxxx",
                    i18n_placeholder="schema_xai_api_key_placeholder",
                    required=True,
                ),
                FieldSchema(
                    key="settings.base_url",
                    label="Base URL",
                    i18n_label="schema_backend_base_url",
                    description="xAI API base URL. Change this only when using an approved gateway.",
                    i18n_description="schema_xai_base_url_desc",
                    type="string",
                    placeholder=XAI_DEFAULT_BASE_URL,
                    default=XAI_DEFAULT_BASE_URL,
                ),
                FieldSchema(
                    key="settings.custom_headers",
                    label="Custom HTTP headers",
                    i18n_label="schema_backend_custom_http_headers",
                    description="Optional headers sent with every provider request. Add one entry per header in the format Header-Name: value.",
                    i18n_description="schema_backend_optional_headers_sent_with_every_provider_request_add_one_entry_per_header",
                    type="string_list",
                    placeholder="Add Header-Name: value and press Enter",
                    i18n_placeholder="schema_backend_add_header_name_value_and_press_enter",
                    default=[],
                ),
            ],
        ),
        Section(
            title="Request handling",
            i18n_title="schema_backend_request_handling",
            description="Set operational controls applied to xAI API calls.",
            i18n_description="schema_xai_request_handling_desc",
            fields=[
                FieldSchema(
                    key="settings.disable_background_sync",
                    label="Disable regular provider requests",
                    i18n_label="schema_backend_disable_regular_provider_requests",
                    description="Skip recurring background requests to this provider, such as periodic model list synchronization.",
                    i18n_description="schema_backend_skip_recurring_background_requests_to_this_provider_such_as_periodic_model_list",
                    type="boolean",
                    default=False,
                ),
            ],
        ),
    ]
)
