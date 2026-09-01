OPENAI_PROVIDER_TYPE = "openai"
OPENAI_RESPONSES_PROVIDER_TYPE = "openai_responses"
OPENAI_CHAT_COMPLETIONS_PROVIDER_TYPE = "openai_chat_completions"
MICROSOFT_AZURE_PROVIDER_TYPE = "microsoft_azure"
LMSTUDIO_PROVIDER_TYPE = "lmstudio"
XAI_PROVIDER_TYPE = "xai"

OPENAI_AZURE_PROVIDER_TYPES = {
    MICROSOFT_AZURE_PROVIDER_TYPE,
}

OPENAI_RESPONSES_PROVIDER_TYPES = {
    OPENAI_PROVIDER_TYPE,
    OPENAI_RESPONSES_PROVIDER_TYPE,
    MICROSOFT_AZURE_PROVIDER_TYPE,
    LMSTUDIO_PROVIDER_TYPE,
    XAI_PROVIDER_TYPE,
}

OPENAI_CHAT_COMPLETIONS_PROVIDER_TYPES = {
    OPENAI_CHAT_COMPLETIONS_PROVIDER_TYPE,
}

OPENAI_CUSTOM_BASE_URL_PROVIDER_TYPES = {
    OPENAI_RESPONSES_PROVIDER_TYPE,
    OPENAI_CHAT_COMPLETIONS_PROVIDER_TYPE,
}

OPENAI_ALL_PROVIDER_TYPES = OPENAI_RESPONSES_PROVIDER_TYPES | OPENAI_CHAT_COMPLETIONS_PROVIDER_TYPES

OPENAI_MANUAL_MODEL_PROVIDER_TYPES = {
    OPENAI_RESPONSES_PROVIDER_TYPE,
    OPENAI_CHAT_COMPLETIONS_PROVIDER_TYPE,
    MICROSOFT_AZURE_PROVIDER_TYPE,
    LMSTUDIO_PROVIDER_TYPE,
    XAI_PROVIDER_TYPE,
}


def normalize_openai_provider_type(provider_type: str | None) -> str:
    """Normalize OpenAI provider type."""
    value = str(provider_type or "").strip()
    return value or OPENAI_PROVIDER_TYPE


def is_azure_openai_provider_type(provider_type: str | None) -> bool:
    """Check if Azure OpenAI provider type."""
    return normalize_openai_provider_type(provider_type) in OPENAI_AZURE_PROVIDER_TYPES


def is_openai_chat_completions_provider_type(provider_type: str | None) -> bool:
    """Check if OpenAI chat completions provider type."""
    return normalize_openai_provider_type(provider_type) in OPENAI_CHAT_COMPLETIONS_PROVIDER_TYPES


def is_openai_custom_base_url_provider_type(provider_type: str | None) -> bool:
    """Check if this is a generic OpenAI-compatible endpoint."""
    return normalize_openai_provider_type(provider_type) in OPENAI_CUSTOM_BASE_URL_PROVIDER_TYPES


def is_openai_responses_provider_type(provider_type: str | None) -> bool:
    """Check if OpenAI responses provider type."""
    return normalize_openai_provider_type(provider_type) in OPENAI_RESPONSES_PROVIDER_TYPES


def allows_manual_openai_model_entry(provider_type: str | None) -> bool:
    """Check if allows manual OpenAI model entry."""
    return normalize_openai_provider_type(provider_type) in OPENAI_MANUAL_MODEL_PROVIDER_TYPES


def is_lmstudio_provider_type(provider_type: str | None) -> bool:
    """Check if provider is LM Studio."""
    return normalize_openai_provider_type(provider_type) == LMSTUDIO_PROVIDER_TYPE
