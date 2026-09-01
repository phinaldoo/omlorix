"""Anthropic request-setting normalization and thinking payload construction."""

from fastapi import HTTPException

from app.llm.anthropic.prompt_caching import apply_anthropic_prompt_cache
from app.llm.anthropic.schemas import AnthropicModelSettings
from app.llm.anthropic.settings import remove_deprecated_anthropic_request_settings
from app.llm.anthropic.thinking import get_anthropic_thinking_capabilities
from app.llm.helper import merge_settings


def _merge_anthropic_simple_settings(
    model_settings: dict | None,
    settings_override: dict | None,
) -> dict:
    settings, _ = merge_settings(
        model_settings,
        settings_override,
        getattr(AnthropicModelSettings, "model_fields", None),
    )
    return remove_deprecated_anthropic_request_settings(settings)


def _apply_anthropic_simple_settings(
    request_kwargs: dict, settings: dict | None
) -> None:
    if not isinstance(settings, dict):
        return
    max_tokens = settings.get("max_tokens")
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    apply_anthropic_prompt_cache(request_kwargs, settings)


def _get_anthropic_thinking_capabilities(model_name: str | None) -> dict:
    """Return a safe capability shape even for retired or unknown model IDs.

    Catalog retirement should not turn a read-only capability probe into a
    ``KeyError``. Unknown models remain conservatively non-thinking unless the
    explicit Anthropic-compatible-provider fallback is requested elsewhere.
    """
    capabilities = get_anthropic_thinking_capabilities(model_name)
    return {
        "thinking": False,
        "thinking_disabled_allowed": False,
        **capabilities,
    }


def _resolve_anthropic_thinking_enabled(
    thinking_setting: bool | None,
    thinking_caps: dict,
) -> bool:
    """Resolve whether to enable Anthropic thinking for a request."""
    thinking_supported = bool(thinking_caps.get("thinking", False))
    thinking_disabled_allowed = thinking_caps.get("thinking_disabled_allowed", True)
    if (
        thinking_setting is None
        and thinking_supported
        and not thinking_disabled_allowed
    ):
        return True
    return bool(thinking_setting)


def _validate_anthropic_thinking_disabled_effort(
    thinking_setting: bool | None,
    reasoning_effort: str | None,
    thinking_caps: dict,
) -> None:
    """Reject effort levels that a model cannot use with disabled thinking.

    Newer Anthropic models can make thinking optional only for part of their
    effort ladder. Keeping this constraint in catalog metadata lets the UI
    expose the thinking switch while request construction still rejects the
    combinations that Anthropic documents as invalid.
    """
    if thinking_setting is not False or reasoning_effort is None:
        return

    forbidden_efforts = {
        str(value).strip().lower()
        for value in thinking_caps.get(
            "thinking_disabled_forbidden_efforts",
            [],
        )
        if str(value).strip()
    }
    normalized_effort = str(reasoning_effort).strip().lower()
    if normalized_effort in forbidden_efforts:
        raise HTTPException(
            status_code=422,
            detail=(
                "Thinking cannot be disabled for this Anthropic model at "
                f"reasoning effort '{normalized_effort}'."
            ),
        )


def _build_anthropic_thinking_params(
    settings: dict | None,
    model_name: str | None,
    *,
    allow_compatible_fallback: bool = False,
) -> dict | None:
    """Build the Messages API thinking payload for catalog or compatible models."""
    if not isinstance(settings, dict):
        return None

    thinking_setting = settings.get("thinking")
    thinking_budget = settings.get("thinking_budget")
    reasoning_effort = settings.get("reasoning_effort")
    thinking_adaptive = settings.get("thinking_adaptive")
    thinking_caps = get_anthropic_thinking_capabilities(
        model_name,
        allow_compatible_fallback=allow_compatible_fallback,
    )
    _validate_anthropic_thinking_disabled_effort(
        thinking_setting,
        reasoning_effort,
        thinking_caps,
    )
    thinking_disabled_allowed = thinking_caps.get("thinking_disabled_allowed", True)
    # Saved settings were offered by an API-backed schema, so they remain the
    # source of truth after a restart when no live model response is available.
    budget_supported = (
        thinking_caps.get("thinking_budget_support", False)
        or thinking_budget is not None
    )
    effort_supported = (
        thinking_caps.get("reasoning_effort_support", False)
        or reasoning_effort is not None
    )
    adaptive_supported = (
        thinking_caps.get("thinking_support_adaptive", False)
        or thinking_adaptive is True
    )
    thinking_configured = any(
        value is not None
        for value in (
            thinking_setting,
            thinking_budget,
            reasoning_effort,
            thinking_adaptive,
        )
    )
    thinking_enabled = _resolve_anthropic_thinking_enabled(
        thinking_setting, thinking_caps
    )

    if thinking_enabled:
        thinking_type = (
            "adaptive" if thinking_adaptive and adaptive_supported else "enabled"
        )
        thinking_params = {"type": thinking_type}
        if effort_supported and reasoning_effort is not None:
            thinking_params["effort"] = reasoning_effort
        if thinking_type != "adaptive":
            if thinking_budget is not None:
                thinking_params["budget_tokens"] = thinking_budget
            elif budget_supported and not (
                effort_supported and reasoning_effort is not None
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Thinking budget must be provided when thinking mode is enabled for this model.",
                )
        return thinking_params

    if thinking_configured and thinking_disabled_allowed:
        return {"type": "disabled"}
    return None
