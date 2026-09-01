from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from enum import Enum
from typing import Any, List, Literal, Set

from app.utils.helpers import _mask_api_key_preview

class UpdateProviderPayload(BaseModel):
    name: str
    icon: str | None = None
    api_key: str | None = None
    settings: dict | BaseModel


from app.llm.anthropic.schemas import (
    AnthropicSettings,
    AnthropicListModelsByok,
    AnthropicBaseListModelsByok,
    ANTHROPIC_PROVIDER_SCHEMA,
    ANTHROPIC_BASE_PROVIDER_SCHEMA,
    AnthropicModelSettings,
)
from app.llm.google_aistudio.schemas import (
    GoogleAistudioSettings,
    GoogleAiStudioModelSettings,
    GoogleAiStudioListModelsByok,
    GOOGLE_AISTUDIO_PROVIDER_SCHEMA,
)
from app.llm.ollama.schemas import (
    OllamaSettings,
    OllamaModelSettings,
    OllamaListModelByok,
    OLLAMA_PROVIDER_SCHEMA,
)
from app.llm.lmstudio.schemas import (
    LMStudioSettings,
    LMStudioModelSettings,
    LMStudioListModelByok,
    LMSTUDIO_PROVIDER_SCHEMA,
)
from app.llm.openai.schemas import (
    OpenAICustomBaseURLModelSettings,
    OpenaiSettings,
    OpenAIModelSettings,
    OpenAIListModelsByok,
    OPENAI_PROVIDER_SCHEMA,
)
from app.llm.azure_openai.schemas import (
    AzureOpenAISettings,
    AzureOpenAIListModelsByok,
    AZURE_OPENAI_PROVIDER_SCHEMA,
)
from app.llm.openai_responses.schemas import (
    OpenaiResponsesSettings,
    OpenaiResponsesListModelsByok,
    OPENAI_RESPONSES_PROVIDER_SCHEMA,
)
from app.llm.openrouter.schemas import (
    OpenrouterSettings,
    OpenrouterModelSettings,
    ListOpenrouterModelsRequest,
    OPENROUTER_PROVIDER_SCHEMA,
)
from app.llm.elevenlabs.schemas import (
    ElevenlabsSettings,
    ELEVENLABS_PROVIDER_SCHEMA,
)
from app.llm.xai.schemas import (
    XAIListModelsByok,
    XAIModelSettings,
    XAISettings,
    XAI_PROVIDER_SCHEMA,
)
from app.llm.openai.custom_headers import redact_custom_headers_for_display_settings
from app.llm.base_settings import remove_custom_provider_timeout



# -------------------
# All Providers
# -------------------
class ProviderEnum(str, Enum):
    openai = "openai"
    openai_responses = "openai_responses"
    openai_chat_completions = "openai_chat_completions"
    microsoft_azure = "microsoft_azure"
    anthropic = "anthropic"
    anthropic_base = "anthropic_base"
    google_aistudio = "google_aistudio"
    openrouter = "openrouter"
    ollama = "ollama"
    lmstudio = "lmstudio"
    elevenlabs = "elevenlabs"
    xai = "xai"


def normalize_provider_value(provider: ProviderEnum | str | None) -> str:
    if isinstance(provider, ProviderEnum):
        raw_value = provider.value
    elif provider is None:
        raw_value = ""
    else:
        raw_value = str(provider).strip()
    return raw_value


# Only provider protocols that are deliberately designed for third-party
# compatible endpoints may choose their own provider icon. Native adapters
# keep their brand icon even when they expose an endpoint setting (for example
# Ollama, LM Studio, or an approved xAI gateway).
CUSTOM_ICON_PROVIDER_VALUES = frozenset(
    {
        ProviderEnum.openai_responses.value,
        ProviderEnum.openai_chat_completions.value,
        ProviderEnum.anthropic_base.value,
    }
)

PROVIDER_DEFAULT_ICON_VALUES = {
    ProviderEnum.openai.value: "openai",
    ProviderEnum.openai_responses.value: "openai",
    ProviderEnum.openai_chat_completions.value: "openai",
    ProviderEnum.microsoft_azure.value: "microsoft",
    ProviderEnum.anthropic.value: "anthropic",
    ProviderEnum.anthropic_base.value: "anthropic",
    ProviderEnum.google_aistudio.value: "google_aistudio",
    ProviderEnum.openrouter.value: "openrouter",
    ProviderEnum.ollama.value: "ollama",
    ProviderEnum.lmstudio.value: "lmstudio",
    ProviderEnum.elevenlabs.value: "elevenlabs",
    ProviderEnum.xai.value: "xai",
}


def provider_supports_custom_icon(provider: ProviderEnum | str | None) -> bool:
    """Return whether a provider's administrator icon can be customized."""

    return normalize_provider_value(provider) in CUSTOM_ICON_PROVIDER_VALUES


def get_default_provider_icon(provider: ProviderEnum | str | None) -> str:
    """Return the stable brand icon for a provider protocol."""

    normalized = normalize_provider_value(provider)
    return PROVIDER_DEFAULT_ICON_VALUES.get(normalized, normalized or "omlorix")


def resolve_provider_icon(
    provider: ProviderEnum | str | None,
    icon: str | None = None,
) -> str:
    """Apply the native-vs-custom provider icon policy."""

    default_icon = get_default_provider_icon(provider)
    if not provider_supports_custom_icon(provider):
        return default_icon
    if isinstance(icon, str) and icon.strip():
        return icon
    return default_icon


OPTIONAL_API_KEY_PROVIDERS: Set[ProviderEnum] = {
    ProviderEnum.ollama,
    ProviderEnum.lmstudio,
    ProviderEnum.anthropic_base,
}

# Model discovery has a narrower no-credential path than chat generation.
# Anthropic-compatible endpoints may accept anonymous chat requests, but their
# model-listing adapter intentionally requires a key.  Keep that distinction at
# the request boundary so callers receive the standard sealed-credential error
# instead of a later provider-specific validation failure.
BYOK_MODEL_DISCOVERY_OPTIONAL_CREDENTIAL_PROVIDERS: Set[ProviderEnum] = {
    ProviderEnum.ollama,
    ProviderEnum.lmstudio,
}


REQUIRED_BASE_URL_PROVIDERS: Set[ProviderEnum] = {
    ProviderEnum.ollama,
    ProviderEnum.lmstudio,
}


def provider_api_key_is_optional(provider: ProviderEnum | str | None) -> bool:
    if isinstance(provider, ProviderEnum):
        return provider in OPTIONAL_API_KEY_PROVIDERS
    try:
        return ProviderEnum(str(provider or "").strip()) in OPTIONAL_API_KEY_PROVIDERS
    except ValueError:
        return False


PROVIDER_SETTINGS_MODELS = {
    ProviderEnum.openai: OpenaiSettings,
    ProviderEnum.openai_responses: OpenaiResponsesSettings,  
    ProviderEnum.openai_chat_completions: OpenaiResponsesSettings,
    ProviderEnum.microsoft_azure: AzureOpenAISettings,
    ProviderEnum.anthropic: AnthropicSettings,
    ProviderEnum.anthropic_base: AnthropicSettings,
    ProviderEnum.google_aistudio: GoogleAistudioSettings,
    ProviderEnum.openrouter: OpenrouterSettings,
    ProviderEnum.ollama: OllamaSettings,
    ProviderEnum.lmstudio: LMStudioSettings,
    ProviderEnum.elevenlabs: ElevenlabsSettings,
    ProviderEnum.xai: XAISettings,
}


PROVIDER_SETTINGS_SCHEMAS = {
    ProviderEnum.openai: OPENAI_PROVIDER_SCHEMA,
    ProviderEnum.openai_responses: OPENAI_RESPONSES_PROVIDER_SCHEMA,
    ProviderEnum.openai_chat_completions: OPENAI_RESPONSES_PROVIDER_SCHEMA,
    ProviderEnum.microsoft_azure: AZURE_OPENAI_PROVIDER_SCHEMA,
    ProviderEnum.anthropic: ANTHROPIC_PROVIDER_SCHEMA,
    ProviderEnum.anthropic_base: ANTHROPIC_BASE_PROVIDER_SCHEMA,
    ProviderEnum.google_aistudio: GOOGLE_AISTUDIO_PROVIDER_SCHEMA,
    ProviderEnum.openrouter: OPENROUTER_PROVIDER_SCHEMA,
    ProviderEnum.ollama: OLLAMA_PROVIDER_SCHEMA,
    ProviderEnum.lmstudio: LMSTUDIO_PROVIDER_SCHEMA,
    ProviderEnum.elevenlabs: ELEVENLABS_PROVIDER_SCHEMA,
    ProviderEnum.xai: XAI_PROVIDER_SCHEMA,
}


PROVIDER_MODEL_SETTINGS_MODELS = {
    ProviderEnum.openai: OpenAIModelSettings,
    ProviderEnum.openai_responses: OpenAICustomBaseURLModelSettings,
    ProviderEnum.openai_chat_completions: OpenAICustomBaseURLModelSettings,
    ProviderEnum.microsoft_azure: OpenAIModelSettings,
    ProviderEnum.anthropic: AnthropicModelSettings,
    ProviderEnum.anthropic_base: AnthropicModelSettings,
    ProviderEnum.google_aistudio: GoogleAiStudioModelSettings,
    ProviderEnum.openrouter: OpenrouterModelSettings,
    ProviderEnum.ollama: OllamaModelSettings,
    ProviderEnum.lmstudio: LMStudioModelSettings,
    ProviderEnum.xai: XAIModelSettings,
}


MODEL_CAPABLE_PROVIDERS: Set[ProviderEnum] = {
    ProviderEnum.openai,
    ProviderEnum.openai_responses,
    ProviderEnum.openai_chat_completions,
    ProviderEnum.microsoft_azure,
    ProviderEnum.anthropic,
    ProviderEnum.anthropic_base,
    ProviderEnum.google_aistudio,
    ProviderEnum.openrouter,
    ProviderEnum.ollama,
    ProviderEnum.lmstudio,
    ProviderEnum.xai,
}


PROVIDER_BYOK_PAYLOAD_MODELS = {
    ProviderEnum.openai: OpenAIListModelsByok,
    ProviderEnum.openai_responses: OpenaiResponsesListModelsByok,
    ProviderEnum.openai_chat_completions: OpenaiResponsesListModelsByok,
    ProviderEnum.microsoft_azure: AzureOpenAIListModelsByok,
    ProviderEnum.anthropic: AnthropicListModelsByok,
    ProviderEnum.anthropic_base: AnthropicBaseListModelsByok,
    ProviderEnum.google_aistudio: GoogleAiStudioListModelsByok,
    ProviderEnum.openrouter: ListOpenrouterModelsRequest,
    ProviderEnum.ollama: OllamaListModelByok,
    ProviderEnum.lmstudio: LMStudioListModelByok,
    ProviderEnum.xai: XAIListModelsByok,
}



# -------------------
# Create Provider
# -------------------
class CreateProviderRequest(BaseModel):
    provider: ProviderEnum
    name: str
    icon: str | None = None
    api_key: str | None = None
    settings: dict | BaseModel

    @model_validator(mode="after")
    def validate_settings(self):
        settings_model = PROVIDER_SETTINGS_MODELS.get(self.provider)
        if settings_model is None:
            raise ValueError(f"Unsupported provider '{self.provider}'.")

        if isinstance(self.settings, settings_model):
            settings_obj = self.settings
        else:
            settings_obj = settings_model.model_validate(self.settings)

        self.settings = settings_obj
        return self

    @model_validator(mode="after")
    def validate_api_key(self):
        api_key_value = self.api_key

        if provider_api_key_is_optional(self.provider):
            if api_key_value is None:
                self.api_key = ""
            elif isinstance(api_key_value, str):
                self.api_key = api_key_value.strip()
            else:
                raise ValueError("Provider api_key must be a string when provided.")
        else:
            if not isinstance(api_key_value, str) or not api_key_value.strip():
                raise ValueError(f"Provider api_key is required for '{self.provider.value}'.")
            self.api_key = api_key_value.strip()
        return self

    @model_validator(mode="after")
    def normalize_icon(self):
        """Ignore custom icon input for native provider types."""

        self.icon = resolve_provider_icon(self.provider, self.icon)
        return self



class TestProviderPayload(BaseModel):
    """Describe either a new provider draft or edits to a saved provider."""

    provider: ProviderEnum
    provider_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    settings: dict | None = None

    @model_validator(mode="after")
    def validate_credentials(self):
        provider = self.provider

        base_url_value = None
        if isinstance(self.base_url, str):
            stripped = self.base_url.strip()
            base_url_value = stripped or None

        api_key_value = None
        if isinstance(self.api_key, str):
            stripped = self.api_key.strip()
            api_key_value = stripped or None

        provider_id_value = None
        if isinstance(self.provider_id, str):
            stripped = self.provider_id.strip()
            provider_id_value = stripped or None

        if provider in REQUIRED_BASE_URL_PROVIDERS and not base_url_value:
            raise ValueError(f"Provider base_url is required for '{provider.value}'.")

        # Edit tests may reuse the encrypted API key belonging to the referenced
        # provider. New-provider tests must continue to supply credentials.
        if not provider_api_key_is_optional(provider) and not api_key_value and not provider_id_value:
            raise ValueError(f"Provider api_key is required for '{provider.value}'.")

        self.provider_id = provider_id_value
        self.base_url = base_url_value
        self.api_key = api_key_value
        self.settings = self.settings if isinstance(self.settings, dict) else {}
        return self




class ListProviderModelsByokRequest(BaseModel):
    """Request model discovery using an opaque, server-sealed credential."""

    provider: ProviderEnum
    provider_id: str = Field(..., min_length=1, max_length=255)
    credential_token: str | None = Field(default=None, max_length=32768)
    config: dict[str, Any]

    @model_validator(mode="after")
    def validate_config(self):
        if self.provider not in PROVIDER_BYOK_PAYLOAD_MODELS:
            raise ValueError(f"Unsupported provider '{self.provider}'.")
        if not isinstance(self.config, dict):
            raise ValueError("Provider config must be an object.")
        if "api_key" in self.config:
            raise ValueError("Raw BYOK API keys are not accepted by model discovery.")
        if "anthropic_provider_id" in self.config or "openrouter_provider_id" in self.config:
            raise ValueError("BYOK model listing cannot reference a stored provider ID.")
        if (
            self.provider not in BYOK_MODEL_DISCOVERY_OPTIONAL_CREDENTIAL_PROVIDERS
            and not str(self.credential_token or "").strip()
        ):
            raise ValueError("A sealed BYOK credential is required for this provider.")
        self.provider_id = self.provider_id.strip()
        self.credential_token = str(self.credential_token or "").strip() or None
        self.config = dict(self.config)
        return self


class ByokCredentialTokenRequest(BaseModel):
    """Accept a raw API key only at the authenticated sealing boundary."""

    provider: ProviderEnum
    provider_id: str = Field(..., min_length=1, max_length=255)
    api_key: str = Field(..., min_length=1, max_length=16384)

    @field_validator("provider_id", "api_key")
    @classmethod
    def normalize_required_value(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Value must not be empty.")
        return normalized


class ByokCredentialTokenResponse(BaseModel):
    """Return only the opaque token and its absolute expiration time."""

    credential_token: str
    expires_at: datetime


class ByokModelSchemaRequest(BaseModel):
    provider: ProviderEnum
    model_name: str | None = None
    model_provider: str | None = None
    model_info: dict | None = None
    tools: list[str] | None = None




# -------------------
# Model detail 
# -------------------
class ModelStatusEnum(str, Enum):
    normal = "normal"
    alpha = "alpha"
    experimental = "experimental"


class UpdateModelPayload(BaseModel):
    model_name: str | None = None
    name: str
    description: str | None = Field(default=None, max_length=100)
    model_icon: str | None = None
    status: ModelStatusEnum | str = ModelStatusEnum.normal
    tools: list[str] | None = None
    access: dict | None = None
    settings: dict | None = None
    is_active: bool | None = None


class BulkUpdateModelsPayload(BaseModel):
    model_ids: List[str] = Field(min_length=1, max_length=100)
    model_name: str | None = None
    name: str | None = None
    description: str | None = Field(default=None, max_length=100)
    model_icon: str | None = None
    status: ModelStatusEnum | str | None = None
    tools: list[str] | None = None
    access: dict | None = None
    settings: dict | None = None
    is_active: bool | None = None




# -------------------
# Create Model
# -------------------
class CreateModel(BaseModel):
    name: str
    description: str = Field(max_length=100)
    model_icon: str
    model: str
    tools: List[str]
    status: "ModelStatusEnum"

class ModelAccess(BaseModel):
    everyone: bool = False
    users: List[str] = []
    groups: List[str] = []



class AdminModelListItem(BaseModel):
    id: str
    name: str
    description: str | None = None
    provider: str
    provider_name: str | None = None
    model_name: str

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value):
        return normalize_provider_value(value)


class ModelProviderRecipientSummary(BaseModel):
    """Public provider type used for provider-group routing disclosure."""

    provider: str

    model_config = ConfigDict(extra="forbid")


class ModelConnectionSummary(BaseModel):
    """Public managed-connection label displayed by the model picker."""

    provider: str
    title: str

    model_config = ConfigDict(extra="forbid")


class UserModelSummary(BaseModel):
    """Strict public contract for a model visible to the requesting user.

    This schema intentionally excludes database IDs other than the selectable
    model ID, provider configuration, ACLs, prompts, share capabilities, raw
    tool configuration, and agent editor data.
    """

    model_id: str
    name: str
    description: str | None = None
    model_icon: str | None = None
    provider: str
    model_kind: Literal["base", "agent"]
    status: str = "normal"
    is_last: bool = False
    capabilities: list[str] = Field(default_factory=list)
    input_formats: list[str] = Field(default_factory=lambda: ["text"])
    output_formats: list[str] = Field(default_factory=lambda: ["text"])
    model_select_tools: list[str] = Field(default_factory=list)
    model_select_connections: list[ModelConnectionSummary] = Field(default_factory=list)
    is_provider_group: bool = False
    provider_recipients: list[ModelProviderRecipientSummary] = Field(default_factory=list)
    tokens_per_second: float | None = None
    increased_errors: bool = False
    has_fixed_skill: bool = False
    owner_name: str | None = None
    is_shared: bool | None = None

    model_config = ConfigDict(extra="forbid")


class ModelSettingsResponse(BaseModel):
    """Compact, user-facing model-settings contract.

    ``schema`` remains schema-driven provider data, but its serializer removes
    absent/default properties before this response model sees it.  Attachment
    MIME values are referenced through small group names and are fetched once
    from the separate static catalog endpoint.
    """

    supported: bool
    message: str | None = None
    schema_payload: dict[str, Any] | None = Field(default=None, alias="schema")
    supported_file_format_groups: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FileFormatCatalogResponse(BaseModel):
    """Static MIME allowlist groups expanded once by the frontend."""

    groups: dict[str, list[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")



class CreateProviderModelRequest(BaseModel):
    provider: ProviderEnum
    provider_id: str
    model: CreateModel
    settings: dict | BaseModel
    access: ModelAccess

    @model_validator(mode="after")
    def validate_settings(self):
        settings_model = PROVIDER_MODEL_SETTINGS_MODELS.get(self.provider)
        if settings_model is None:
            raise ValueError(f"Unsupported provider '{self.provider}'.")

        if isinstance(self.settings, settings_model):
            settings_obj = self.settings
        else:
            settings_obj = settings_model.model_validate(self.settings)

        self.settings = settings_obj
        return self



# -------------------
# Provider List Item
# -------------------
class LLMProviderListItem(BaseModel):
    id: str
    provider: str
    name: str
    icon: str | None = None
    status: dict

    model_config = ConfigDict(from_attributes=True)

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value):
        return normalize_provider_value(value)

    @model_validator(mode="after")
    def _normalize_icon(self):
        """Expose fixed native icons and preserve custom endpoint icons."""

        self.icon = resolve_provider_icon(self.provider, self.icon)
        return self



# -------------------
# Provider Detail
# -------------------
class LLMProviderDetail(BaseModel):
    id: str
    provider: str
    name: str
    icon: str | None = None
    has_api_key: bool = False
    api_key_preview: str | None = None
    settings: dict
    status: dict

    model_config = ConfigDict(from_attributes=True)

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value):
        return normalize_provider_value(value)

    @model_validator(mode="after")
    def _normalize_icon(self):
        """Expose fixed native icons and preserve custom endpoint icons."""

        self.icon = resolve_provider_icon(self.provider, self.icon)
        return self


def serialize_llm_provider_detail(provider: Any) -> LLMProviderDetail:
    api_key = getattr(provider, "api_key", None)
    has_api_key = isinstance(api_key, str) and bool(api_key.strip())
    settings = redact_custom_headers_for_display_settings(
        remove_custom_provider_timeout(getattr(provider, "settings", None))
    )
    return LLMProviderDetail.model_validate(
        {
            "id": getattr(provider, "id", ""),
            "provider": getattr(provider, "provider", ""),
            "name": getattr(provider, "name", ""),
            "icon": resolve_provider_icon(
                getattr(provider, "provider", None),
                getattr(provider, "icon", None),
            ),
            "has_api_key": has_api_key,
            "api_key_preview": _mask_api_key_preview(api_key, visible_chars=7) if has_api_key else None,
            "settings": settings,
            "status": getattr(provider, "status", None) or {},
        }
    )



# -------------------
# Update Provider
# -------------------
class UpdateLLMProviderRequest(BaseModel):
    provider_id: str
    provider: ProviderEnum | None = None
    name: str | None = None
    api_key: str | None = None
    settings: dict | None = None
    status: dict | None = None





# -------------------
# List Models
# -------------------
class ProviderModelListItem(BaseModel):
    id: str
    model: str | None = None
    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    created: int | None = None
    max_input_tokens: int | None = None
    max_tokens: int | None = None
    reasoning: dict | None = None
    capabilities: dict | None = None


# -------------------
# Model Setting Presets
# -------------------
class ModelSettingPresetBase(BaseModel):
    id: str
    user_id: str
    model_id: str
    name: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ModelSettingPresetDetail(ModelSettingPresetBase):
    settings: dict | None = None


class ModelSettingPresetListItem(ModelSettingPresetBase):
    pass


class CreateModelSettingPresetRequest(BaseModel):
    name: str
    settings: dict


# -------------------
# Provider Groups
# -------------------
class ProviderGroupMember(BaseModel):
    provider_id: str
    weight: int = 1


class CreateProviderGroupRequest(BaseModel):
    name: str
    icon: str | None = None
    members: List[ProviderGroupMember]

    @model_validator(mode="after")
    def validate_members(self):
        if len(self.members) < 2:
            raise ValueError("Provider group must have at least 2 members")
        
        seen_ids = set()
        for member in self.members:
            if member.provider_id in seen_ids:
                raise ValueError(f"Duplicate provider '{member.provider_id}' in group")
            seen_ids.add(member.provider_id)
            if member.weight < 1:
                raise ValueError("Weight must be at least 1")
        return self


class UpdateProviderGroupRequest(BaseModel):
    name: str | None = None
    icon: str | None = None
    members: List[ProviderGroupMember] | None = None

    @model_validator(mode="after")
    def validate_members(self):
        if self.members is not None:
            if len(self.members) < 2:
                raise ValueError("Provider group must have at least 2 members")
            
            seen_ids = set()
            for member in self.members:
                if member.provider_id in seen_ids:
                    raise ValueError(f"Duplicate provider '{member.provider_id}' in group")
                seen_ids.add(member.provider_id)
                if member.weight < 1:
                    raise ValueError("Weight must be at least 1")
        return self


class ProviderGroupMemberDetail(BaseModel):
    provider_id: str
    weight: int
    name: str | None = None
    provider: str | None = None
    icon: str | None = None
    status: dict | None = None


class ProviderGroupListItem(BaseModel):
    id: str
    name: str
    icon: str | None = None
    member_count: int
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ProviderGroupDetail(BaseModel):
    id: str
    name: str
    icon: str | None = None
    members: List[ProviderGroupMemberDetail]
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# -------------------
# Rate Limits
# -------------------
class RateLimitPeriodEnum(str, Enum):
    day = "day"
    week = "week"
    month = "month"


class RateLimitQuotaUnitEnum(str, Enum):
    requests = "requests"
    tokens = "tokens"
    invocations = "invocations"
    minutes = "minutes"


class RateLimitTargetTypeEnum(str, Enum):
    model = "model"
    tool = "tool"
    dictation = "dictation"
    realtime = "realtime"


class CreateRateLimitRequest(BaseModel):
    name: str
    target_type: RateLimitTargetTypeEnum = RateLimitTargetTypeEnum.model
    model_ids: List[str] = Field(default_factory=list)
    tool_keys: List[str] = Field(default_factory=list)
    user_ids: List[str] = Field(default_factory=list)
    group_ids: List[str] = Field(default_factory=list)
    period: RateLimitPeriodEnum
    timezone: str | None = None
    quota_unit: RateLimitQuotaUnitEnum
    quota_value: int = Field(gt=0)
    max_requests: int | None = Field(default=None, gt=0)
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_request_payload(cls, value):
        if isinstance(value, dict):
            payload = dict(value)
            payload["target_type"] = payload.get("target_type") or RateLimitTargetTypeEnum.model.value
            if payload.get("quota_unit") is None and payload.get("quota_value") is None and payload.get("max_requests") is not None:
                payload["quota_unit"] = RateLimitQuotaUnitEnum.requests.value
                payload["quota_value"] = payload.get("max_requests")
            return payload
        return value

    @model_validator(mode="after")
    def _validate_target_payload(self):
        if self.target_type == RateLimitTargetTypeEnum.model:
            if not self.model_ids:
                raise ValueError("Select at least one model.")
            if self.tool_keys:
                raise ValueError("tool_keys are only valid for tool rate limits.")
            if self.quota_unit == RateLimitQuotaUnitEnum.invocations:
                raise ValueError("Invocation quotas are only valid for tool rate limits.")
            if self.quota_unit == RateLimitQuotaUnitEnum.minutes:
                raise ValueError("Minute quotas are only valid for dictation and realtime limits.")
        elif self.target_type == RateLimitTargetTypeEnum.tool:
            if not self.tool_keys:
                raise ValueError("Select at least one tool.")
            if self.model_ids:
                raise ValueError("model_ids are only valid for model rate limits.")
            if self.quota_unit != RateLimitQuotaUnitEnum.invocations:
                raise ValueError("Tool rate limits must use invocation quotas.")
        else:
            if self.model_ids or self.tool_keys:
                raise ValueError("Dictation and realtime limits do not accept model_ids or tool_keys.")
            if self.quota_unit != RateLimitQuotaUnitEnum.minutes:
                raise ValueError("Dictation and realtime limits must use minute quotas.")
        return self


class UpdateRateLimitRequest(BaseModel):
    name: str | None = None
    target_type: RateLimitTargetTypeEnum | None = None
    model_ids: List[str] | None = None
    tool_keys: List[str] | None = None
    user_ids: List[str] | None = None
    group_ids: List[str] | None = None
    period: RateLimitPeriodEnum | None = None
    timezone: str | None = None
    quota_unit: RateLimitQuotaUnitEnum | None = None
    quota_value: int | None = Field(default=None, gt=0)
    max_requests: int | None = Field(default=None, gt=0)
    is_active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_request_payload(cls, value):
        if isinstance(value, dict):
            payload = dict(value)
            if payload.get("quota_unit") is None and payload.get("quota_value") is None and payload.get("max_requests") is not None:
                payload["quota_unit"] = RateLimitQuotaUnitEnum.requests.value
                payload["quota_value"] = payload.get("max_requests")
            return payload
        return value

    @model_validator(mode="after")
    def _validate_target_payload(self):
        target_type = self.target_type
        if target_type == RateLimitTargetTypeEnum.model:
            if self.tool_keys:
                raise ValueError("tool_keys are only valid for tool rate limits.")
            if self.quota_unit == RateLimitQuotaUnitEnum.invocations:
                raise ValueError("Invocation quotas are only valid for tool rate limits.")
            if self.quota_unit == RateLimitQuotaUnitEnum.minutes:
                raise ValueError("Minute quotas are only valid for dictation and realtime limits.")
        elif target_type == RateLimitTargetTypeEnum.tool:
            if self.model_ids:
                raise ValueError("model_ids are only valid for model rate limits.")
            if self.quota_unit and self.quota_unit != RateLimitQuotaUnitEnum.invocations:
                raise ValueError("Tool rate limits must use invocation quotas.")
        elif target_type in {RateLimitTargetTypeEnum.dictation, RateLimitTargetTypeEnum.realtime}:
            if self.model_ids or self.tool_keys:
                raise ValueError("Dictation and realtime limits do not accept model_ids or tool_keys.")
            if self.quota_unit and self.quota_unit != RateLimitQuotaUnitEnum.minutes:
                raise ValueError("Dictation and realtime limits must use minute quotas.")
        return self


class RateLimitResolvedModel(BaseModel):
    id: str
    name: str


class RateLimitResolvedTool(BaseModel):
    key: str
    id: str
    name: str
    label: str
    description: str | None = None
    source: str | None = None
    label_key: str | None = None
    description_key: str | None = None
    available: bool = True


class RateLimitResolvedUser(BaseModel):
    id: str
    email: str
    first_name: str | None = None
    last_name: str | None = None


class RateLimitResolvedGroup(BaseModel):
    id: str
    name: str


class RateLimitListItem(BaseModel):
    id: str
    name: str
    target_type: str = "model"
    model_ids: List[str]
    tool_keys: List[str] = Field(default_factory=list)
    user_ids: List[str]
    group_ids: List[str]
    scope: str
    period: str
    timezone: str
    quota_unit: str
    quota_value: int
    current_usage: float | None = None
    remaining_usage: float | None = None
    current_usage_seconds: int | None = None
    remaining_usage_seconds: int | None = None
    max_requests: int | None = None
    is_active: bool
    created_at: datetime | None = None
    models: List[RateLimitResolvedModel] = Field(default_factory=list)
    tools: List[RateLimitResolvedTool] = Field(default_factory=list)
    users: List[RateLimitResolvedUser] = Field(default_factory=list)
    groups: List[RateLimitResolvedGroup] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class RateLimitDetail(RateLimitListItem):
    updated_at: datetime | None = None


class RateLimitConflict(BaseModel):
    rate_limit_id: str
    rate_limit_name: str
    target_type: str = "model"
    overlapping_model_ids: List[str]
    overlapping_tool_keys: List[str] = Field(default_factory=list)
    overlapping_user_ids: List[str]
    overlapping_group_ids: List[str]


class RateLimitCheckResult(BaseModel):
    exceeded: bool
    rate_limit_name: str | None = None
    period: str | None = None
    timezone: str | None = None
    quota_unit: str | None = None
    quota_value: int | None = None
    current_usage: float | None = None
    remaining_usage: float | None = None
    current_usage_seconds: int | None = None
    remaining_usage_seconds: int | None = None
    max_requests: int | None = None
    current_count: int | None = None
    resets_at: str | None = None


class RateLimitMutationResponse(BaseModel):
    created: RateLimitDetail | None = None
    updated: RateLimitDetail | None = None
    conflicts: List[RateLimitConflict] = Field(default_factory=list)


# -------------------
# Artificial Analysis leaderboard
# -------------------
class LLMLeaderboardModel(BaseModel):
    """One Omlorix model enriched with matching Artificial Analysis scores."""

    model_name: str
    provider_name: str | None = None
    evaluations: dict[str, Any] = Field(default_factory=dict)
    input_capabilities: list[str] = Field(default_factory=list)
    output_capabilities: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    training_data: Any = None
    tools: Any = None
    tool_categories: dict[str, Any] | None = None


class LLMLeaderboardResponse(BaseModel):
    """Tier-aware leaderboard response returned to the static frontend."""

    status: Literal["ok"] = "ok"
    data_level: Literal["free", "full"]
    provider_tier: Literal["free", "pro", "commercial"]
    intelligence_index_version: float
    models: list[LLMLeaderboardModel] = Field(default_factory=list)
