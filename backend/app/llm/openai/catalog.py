"""Provider-aware catalogs for OpenAI Responses-compatible integrations."""

from __future__ import annotations

from typing import Any

from app.llm.openai.model_list import OPENAI_MODEL_DICT, OPENAI_UNSUPPORTED_MODELS
from app.llm.openai.provider_types import (
    XAI_PROVIDER_TYPE,
    normalize_openai_provider_type,
)


def get_responses_model_catalog(provider_type: str | None) -> dict[str, dict[str, Any]]:
    """Return metadata owned by the effective Responses API provider.

    Reusing OpenAI's transport does not make an xAI model an OpenAI model.  A
    provider-aware boundary here prevents pricing and capability lookups from
    accidentally consulting the wrong vendor's catalog while preserving the
    historical OpenAI behavior for other compatible provider types.
    """

    if normalize_openai_provider_type(provider_type) == XAI_PROVIDER_TYPE:
        from app.llm.xai.model_list import XAI_MODEL_DICT

        return XAI_MODEL_DICT
    return OPENAI_MODEL_DICT


def get_responses_unsupported_models(provider_type: str | None) -> set[str]:
    """Return model identifiers that do not belong in the chat model picker."""

    if normalize_openai_provider_type(provider_type) == XAI_PROVIDER_TYPE:
        from app.llm.xai.model_list import XAI_UNSUPPORTED_MODELS

        return set(XAI_UNSUPPORTED_MODELS)
    return set(OPENAI_UNSUPPORTED_MODELS)


def get_responses_model_capabilities(
    model_name: str | None,
    provider_type: str | None,
) -> dict[str, Any] | None:
    """Resolve a model group from the catalog belonging to its provider."""

    identifier = str(model_name or "").strip()
    if not identifier:
        return None
    if normalize_openai_provider_type(provider_type) == XAI_PROVIDER_TYPE:
        from app.llm.xai.model_list import get_xai_model_capabilities

        return get_xai_model_capabilities(identifier)
    for group_name, capabilities in get_responses_model_catalog(provider_type).items():
        identifiers = capabilities.get("ids") or []
        if identifier == group_name or identifier in identifiers:
            return capabilities
    return None
