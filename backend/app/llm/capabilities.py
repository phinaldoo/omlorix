from __future__ import annotations

from typing import Any, Iterable, Sequence

from app.llm.schemas import ProviderEnum
from app.llm.google_aistudio.utils import is_aistudio_thinking_enforced
from app.utils.utils import coerce_to_dict

def determine_model_capabilities(
    provider: ProviderEnum,
    settings: dict | Sequence | None,
    tools: Any,
    *,
    model_name: str | None = None,
    existing_capabilities: Sequence[str] | None = None,
) -> list[str]:
    """Recompute capability flags for a model based on the provider, settings, tools, and model metadata."""
    settings_dict = coerce_to_dict(settings)
    tools_payload = tools

    match provider:
        case (
            ProviderEnum.openai
            | ProviderEnum.openai_responses
            | ProviderEnum.openai_chat_completions
            | ProviderEnum.microsoft_azure
            | ProviderEnum.lmstudio
            | ProviderEnum.xai
        ):
            capabilities = _capabilities_openai(settings_dict, tools_payload, provider=provider)
        case ProviderEnum.google_aistudio:
            capabilities = _capabilities_google_aistudio(settings_dict, tools_payload, model_name=model_name)
        case ProviderEnum.anthropic | ProviderEnum.anthropic_base:
            capabilities = _capabilities_anthropic(settings_dict, tools_payload)
        case ProviderEnum.openrouter:
            capabilities = _capabilities_openrouter(settings_dict, tools_payload)
        case ProviderEnum.ollama:
            capabilities = _capabilities_ollama(
                settings_dict,
                existing_capabilities=existing_capabilities,
            )
        case _:
            capabilities = list(existing_capabilities or [])

    return _dedupe_capabilities(capabilities, fallback=existing_capabilities)


def _capabilities_openai(settings: dict, tools: Any, *, provider: ProviderEnum | None = None) -> list[str]:
    """Determine capabilities for OpenAI providers."""
    input_formats = _normalize_modalities(settings.get("input_formats"))
    output_formats = _normalize_modalities(settings.get("output_formats"))

    caps: list[str] = ["completion"]
    if "image" in input_formats:
        caps.append("vision")
    if "audio" in input_formats or "audio" in output_formats:
        caps.append("audio")
    if "video" in input_formats:
        caps.append("video")
    if _has_document_input(input_formats):
        caps.append("documents")
    if provider == ProviderEnum.openai_chat_completions and settings.get("reasoning"):
        caps.append("thinking")
    elif _has_reasoning_effort(settings.get("reasoning_effort")):
        caps.append("thinking")
    if _has_tools(tools):
        caps.append("tools")
    return caps


def _capabilities_google_aistudio(settings: dict, tools: Any, model_name: str | None = None) -> list[str]:
    """Determine capabilities for Google AI Studio."""
    input_formats = _normalize_modalities(settings.get("input_formats"))

    caps: list[str] = ["completion"]
    if "image" in input_formats:
        caps.append("vision")
    if "audio" in input_formats:
        caps.append("audio")
    if "video" in input_formats:
        caps.append("video")
    if _has_document_input(input_formats):
        caps.append("documents")
    if settings.get("thinking") and settings.get("thinking_budget", 0) != 0:
        caps.append("thinking")
    elif model_name and is_aistudio_thinking_enforced(model_name):
        caps.append("thinking")
    if _has_tools(tools):
        caps.append("tools")
    return caps


def _capabilities_anthropic(settings: dict, tools: Any) -> list[str]:
    """Determine capabilities for Anthropic providers."""
    input_formats = _normalize_modalities(settings.get("input_formats"))

    caps: list[str] = ["completion"]
    if "image" in input_formats:
        caps.append("vision")
    if _has_document_input(input_formats):
        caps.append("documents")
    if "audio" in input_formats:
        caps.append("audio")
    if settings.get("thinking"):
        caps.append("thinking")
    if _has_tools(tools):
        caps.append("tools")
    return caps


def _capabilities_openrouter(settings: dict, tools: Any) -> list[str]:
    """Determine capabilities for OpenRouter."""
    input_formats = _normalize_modalities(settings.get("input_formats"))
    supported_parameters = _normalize_modalities(settings.get("supported_parameters"))

    caps: list[str] = []
    if "text" in input_formats:
        caps.append("completion")
    if "image" in input_formats:
        caps.append("vision")
    if "audio" in input_formats:
        caps.append("audio")
    if "video" in input_formats:
        caps.append("video")
    if _has_document_input(input_formats):
        caps.append("documents")
    if "tools" in supported_parameters or _has_tools(tools):
        caps.append("tools")
    if settings.get("reasoning_enabled"):
        caps.append("thinking")
    return caps


def _capabilities_ollama(
    settings: dict,
    *,
    existing_capabilities: Sequence[str] | None,
) -> list[str]:
    """Keep Ollama-native flags while deriving Omlorix document support.

    Ollama's API reports native model capabilities such as vision, tools, and
    thinking. Omlorix adds document support by extracting text or rendering PDF
    pages, but it does not add native audio or video input to Ollama requests.
    """
    input_formats = _normalize_modalities(settings.get("input_formats"))
    existing = [
        capability.strip()
        for capability in (existing_capabilities or [])
        if isinstance(capability, str) and capability.strip()
    ]
    existing_set = set(existing)
    caps: list[str] = []
    if "completion" in existing_set or not input_formats or "text" in input_formats:
        caps.append("completion")
    if "vision" in existing_set:
        caps.append("vision")
    if _has_document_input(input_formats):
        caps.append("documents")
    caps.extend(
        capability
        for capability in existing
        if capability
        not in {"completion", "vision", "audio", "video", "documents"}
    )
    return caps


def _normalize_modalities(value: Any) -> list[str]:
    """Normalize modality values to a list."""
    if value is None:
        return []
    if isinstance(value, dict):
        iterable = value.values()
    elif isinstance(value, (list, tuple, set)):
        iterable = value
    else:
        iterable = [value]

    result: list[str] = []
    for item in iterable:
        if item is None:
            continue
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                result.append(normalized)
        else:
            mapped = getattr(item, "value", None) or getattr(item, "name", None)
            if isinstance(mapped, str) and mapped.strip():
                result.append(mapped.strip())
    return result


def _has_reasoning_effort(value: Any) -> bool:
    """Check if reasoning effort is configured."""
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized or normalized == "none":
            return False
        return True
    return True


def _has_document_input(input_formats: Sequence[str]) -> bool:
    """Return whether configured formats include an effective document input."""
    return bool(
        {item.strip().lower() for item in input_formats}
        & {"pdf", "text_document", "document", "documents"}
    )


def _has_tools(tools: Any) -> bool:
    """Check if tools are configured."""
    if isinstance(tools, dict):
        return any(bool(value) for value in tools.values())
    if isinstance(tools, (list, tuple, set)):
        for item in tools:
            if isinstance(item, str) and item.strip():
                return True
            if isinstance(item, dict) and any(bool(v) for v in item.values()):
                return True
            if item:
                return True
        return False
    return bool(tools)



def _dedupe_capabilities(capabilities: Iterable[str], fallback: Sequence[str] | None) -> list[str]:
    """Deduplicate capabilities with fallback."""
    seen: set[str] = set()
    result: list[str] = []
    for cap in capabilities:
        if not isinstance(cap, str):
            continue
        normalized = cap.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)

    if result:
        return result
    if fallback:
        return list(fallback)
    return ["completion"]


def has_configured_tools(tools: Any) -> bool:
    """Public helper to determine whether a tools payload contains any entries."""
    return _has_tools(tools)


def model_has_capability(capabilities: Any, capability: str) -> bool:
    """Return whether persisted model capabilities contain one exact flag.

    Model capabilities are normally stored as a JSON list. The dictionary
    branch keeps the check safe for imported or provider-specific records
    without treating a merely present but false flag as supported.
    """

    required = str(capability or "").strip().lower()
    if not required:
        return False
    if isinstance(capabilities, dict):
        return any(
            str(key).strip().lower() == required and bool(value)
            for key, value in capabilities.items()
        )
    if isinstance(capabilities, (list, tuple, set)):
        return any(
            isinstance(value, str) and value.strip().lower() == required
            for value in capabilities
        )
    return (
        isinstance(capabilities, str)
        and capabilities.strip().lower() == required
    )
