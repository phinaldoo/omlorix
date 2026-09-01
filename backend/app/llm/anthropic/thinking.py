"""Provider-aware Anthropic thinking capability helpers."""

from typing import Any

from app.llm.anthropic.model_list import get_anthropic_thinking_override

ANTHROPIC_PROVIDER_TYPE = "anthropic"
ANTHROPIC_BASE_PROVIDER_TYPE = "anthropic_base"
ANTHROPIC_REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# A custom Anthropic-compatible endpoint cannot be matched against Omlorix's
# built-in model catalog. Expose the same effort-based controls as the current
# main Anthropic provider and let the compatible endpoint validate support.
ANTHROPIC_BASE_FALLBACK_THINKING_CAPABILITIES = {
    "thinking": True,
    "thinking_disabled_allowed": True,
    "thinking_budget_support": False,
    "reasoning_effort_support": True,
    "thinking_support_adaptive": True,
    "reasoning_effort": list(ANTHROPIC_REASONING_EFFORT_LEVELS),
}


def is_anthropic_base_provider_type(provider_type: Any) -> bool:
    """Return whether a provider value identifies Anthropic Base URL."""
    raw_value = getattr(provider_type, "value", provider_type)
    return str(raw_value or "").strip() == ANTHROPIC_BASE_PROVIDER_TYPE


def get_anthropic_thinking_capabilities(
    model_name: str | None,
    *,
    model_info: dict | None = None,
    allow_compatible_fallback: bool = False,
) -> dict:
    """Normalize API thinking metadata or return Base URL compatibility defaults."""
    normalized_name = str(model_name or "").strip()
    overrides = get_anthropic_thinking_override(normalized_name)
    reasoning = model_info.get("reasoning") if isinstance(model_info, dict) else None
    if isinstance(reasoning, dict):
        effort_supported = bool(reasoning.get("reasoning_efforts_supported"))
        capabilities = {
            "thinking": bool(reasoning.get("supported")),
            "thinking_disabled_allowed": True,
            "thinking_budget_support": bool(reasoning.get("enabled"))
            and not effort_supported,
            "reasoning_effort_support": effort_supported,
            "thinking_support_adaptive": bool(reasoning.get("adaptive")),
            "reasoning_effort": list(reasoning.get("efforts") or []),
        }
        capabilities.update(overrides)
        return capabilities

    if overrides:
        return overrides

    if allow_compatible_fallback:
        return {
            **ANTHROPIC_BASE_FALLBACK_THINKING_CAPABILITIES,
            "reasoning_effort": list(ANTHROPIC_REASONING_EFFORT_LEVELS),
        }
    return {}
