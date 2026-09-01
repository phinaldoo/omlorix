"""Anthropic provider schemas, model settings, and shared constants."""

from enum import Enum
from typing import List, Literal

from pydantic import BaseModel, model_validator

from app.llm.anthropic.thinking import ANTHROPIC_REASONING_EFFORT_LEVELS
from app.llm.base_settings import BaseModelSettings
from app.llm.reasoning_effort_options import build_reasoning_effort_options
from app.utils.schemas import FieldAttributes, FieldSchema, Section, Sections


class CreateProviderAnthropic(BaseModel):
    name: str
    api_key: str
    settings: "AnthropicSettings"


class AnthropicSettings(BaseModel):
    base_url: str | None = None
    disable_background_sync: bool = False
    enable_auto_delete_missing_models: bool = False
    enable_notify_model_changes: bool = True


class AnthropicListModelsByok(BaseModel):
    anthropic_provider_id: str | None = None
    api_key: str | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.anthropic_provider_id:
            raise ValueError(
                "BYOK model listing requires an API key instead of a stored provider ID."
            )
        if not self.api_key:
            raise ValueError("Provide 'api_key'.")
        return self


class AnthropicBaseListModelsByok(BaseModel):
    anthropic_provider_id: str | None = None
    api_key: str | None = None
    base_url: str | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.anthropic_provider_id:
            raise ValueError(
                "BYOK model listing requires an API key instead of a stored provider ID."
            )
        if not self.api_key:
            raise ValueError("Provide 'api_key'.")
        return self


ANTHROPIC_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this Anthropic connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name for this Anthropic provider configuration.",
                    type="string",
                    placeholder="E.g. My Anthropic provider",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="API credentials",
            description="Configure the API key used to authenticate with Anthropic.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="Anthropic API key used for authenticating requests.",
                    type="string",
                    placeholder="E.g. sk-ant-xxxxxxxxxxxxxxxx",
                    # Native Anthropic requests always require a credential.
                    # Exposing that contract in the admin schema lets the
                    # browser attach validation to this field before POSTing.
                    required=True,
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="Model synchronization & alerts",
            description="Control how Omlorix reacts when Anthropic model availability changes.",
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
                    description="Automatically remove provider models that no longer exist on Anthropic.",
                    type="boolean",
                    default=False,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_notify_model_changes",
                    label="Notify model changes",
                    description="Send notifications when Anthropic model availability changes.",
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


ANTHROPIC_BASE_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this Anthropic connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name for this Anthropic provider configuration.",
                    type="string",
                    placeholder="E.g. My Anthropic provider",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="icon",
                    label="Provider icon",
                    description="Select a preset icon or provide a custom SVG for this provider.",
                    type="string",
                    default="anthropic",
                ),
            ],
        ),
        Section(
            title="API credentials",
            description="Configure the optional API key and custom base URL used to authenticate with Anthropic-compatible endpoints.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="Optional Anthropic-compatible API key used for authenticating requests. Leave empty when your custom endpoint does not require authentication.",
                    type="string",
                    placeholder="E.g. sk-ant-xxxxxxxxxxxxxxxx",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.base_url",
                    label="Base URL",
                    description="Optional base URL to target a compatible Anthropic endpoint.",
                    type="string",
                    placeholder="E.g. https://api.anthropic.com",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="Model synchronization & alerts",
            description="Control how Omlorix reacts when Anthropic model availability changes.",
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
                    description="Automatically remove provider models that no longer exist on Anthropic.",
                    type="boolean",
                    default=False,
                    dependency="settings.disable_background_sync",
                    dependency_value=False,
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_notify_model_changes",
                    label="Notify model changes",
                    description="Send notifications when Anthropic model availability changes.",
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


# -------------------
# Model Settings Enums
# -------------------
class InputFormatEnum(str, Enum):
    image = "image"
    text = "text"
    pdf = "pdf"
    text_document = "text_document"


class OutputFormatEnum(str, Enum):
    text = "text"


# -------------------
# Model Settings
# -------------------
class AnthropicModelSettings(BaseModelSettings[InputFormatEnum, OutputFormatEnum]):
    training_data: Literal["true", "false", "unknown"]

    max_tokens: int

    thinking: bool | None = None
    thinking_budget: int | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    thinking_adaptive: bool | None = None

    max_image_count: int = (
        -1
    )  # Per chat (chat history + current message), if none -> unlimited
    max_document_count: int = (
        -1
    )  # Per chat (chat history + current message), if none -> unlimited

    native_websearch: bool = False

    # Cache writes cost more than ordinary input, so this provider feature is
    # opt-in. Anthropic-compatible endpoints can use the same switch when they
    # implement Anthropic's top-level ``cache_control`` request field.
    prompt_cache_enabled: bool = False

    # Anthropic's remaining configurable generation parameters. Sampling
    # controls were removed after the API deprecated them for newer models.
    stop_sequences: List[str] | None = None


ANTHROPIC_MODEL_SCHEMA_THINKING_SECTION = Sections(
    sections=[
        Section(
            title="Thinking & reasoning",
            description="Configure Claude's thinking mode and related resource budgets.",
            fields=[
                FieldSchema(
                    key="settings.thinking",
                    label="Thinking",
                    description="Enable Claude thinking mode to produce intermediate reasoning.",
                    type="boolean",
                    required=False,
                ),
                FieldSchema(
                    key="settings.thinking_adaptive",
                    label="Adaptive thinking",
                    description="Let Claude decide when to think based on request complexity.",
                    type="boolean",
                    required=False,
                    dependency="settings.thinking",
                    dependency_value=True,
                    default=True,
                ),
                FieldSchema(
                    key="settings.thinking_budget",
                    label="Thinking budget",
                    description="Token budget allocated for thinking outputs.",
                    type="string",
                    input_type="int",
                    attributes=FieldAttributes(min=1024),
                    required=False,
                    dependency="settings.thinking",
                    dependency_value=True,
                    dependency2="settings.thinking_adaptive",
                    dependency2_value=False,
                ),
                FieldSchema(
                    key="settings.reasoning_effort",
                    label="Reasoning effort",
                    description="Select the reasoning effort level for models that support effort-based thinking.",
                    type="select",
                    options=build_reasoning_effort_options(
                        ANTHROPIC_REASONING_EFFORT_LEVELS
                    ),
                    required=False,
                    dependency="settings.thinking",
                    dependency_value=True,
                    dependency2="settings.thinking_adaptive",
                    dependency2_value=False,
                ),
            ],
        )
    ]
)


ANTHROPIC_MODEL_SCHEMA_PROMPT_CACHE_SECTION = Sections(
    sections=[
        Section(
            title="Prompt caching",
            description="Configure Anthropic's automatic five-minute prompt cache.",
            i18n_title="llm.anthropic.prompt_cache.section_title",
            i18n_description="llm.anthropic.prompt_cache.section_description",
            fields=[
                FieldSchema(
                    key="settings.prompt_cache_enabled",
                    label="Enable prompt caching",
                    description=(
                        "Automatically cache reusable prompt prefixes for five "
                        "minutes. Anthropic-compatible endpoints must support "
                        "the Anthropic cache_control request field."
                    ),
                    i18n_label="llm.anthropic.prompt_cache.enabled_label",
                    i18n_description="llm.anthropic.prompt_cache.enabled_description",
                    type="boolean",
                    default=False,
                    required=False,
                )
            ],
        )
    ]
)


anthropic_image_mime_types = ["image/png", "image/jpeg", "image/webp", "image/gif"]

anthropic_document_mime_types = ["application/pdf"]
