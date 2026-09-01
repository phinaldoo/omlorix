"""OpenAI token-cost calculation and cost-breakdown helpers.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "calculate_openai_token_costs": (
        "coerce_token_count",
        "get_responses_model_capabilities",
    ),
    "merge_openai_cost_breakdown": ("_OPENAI_COST_KEYS",),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate dependencies before definitions so annotations and defaults retain
# exactly the same evaluation behavior as in the original module.
for _dependency_name in (
    "_OPENAI_COST_KEYS",
    "coerce_token_count",
    "get_responses_model_capabilities",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_calculate_openai_token_costs(
    model_name,
    service_tier,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    native_websearch_tool_calls_count: int,
    cache_write_tokens: int = 0,
    provider_type: str | None = None,
):
    """Calculate token costs from the effective provider's static catalog."""

    def _get_tier_pricing(pricing_dict: dict, tier: str | None) -> dict:
        return pricing_dict.get(tier) or pricing_dict.get("standard", {})

    def _normalize_pricing_service_tier(tier: str | None) -> str:
        """Map OpenAI API tier names to the keys used by Omlorix pricing data.

        OpenAI calls the ordinary service tier ``default`` (and accepts
        ``auto`` as a request value), while Omlorix's model catalog stores the
        corresponding rates under ``standard``. Normalizing these aliases is
        especially important for high-context pricing because that lookup must
        distinguish unsupported explicit tiers instead of blindly falling back.
        """
        normalized_tier = str(tier or "").strip().lower()
        if normalized_tier in {"", "auto", "default", "standard"}:
            return "standard"
        return normalized_tier

    def _cost_for_tokens(token_count: float, price_per_million: float) -> float:
        return (token_count / 1_000_000) * price_per_million

    model_info = get_responses_model_capabilities(model_name, provider_type)
    if not model_info:
        return None

    pricing = model_info.get("pricing", {})
    pricing_service_tier = _normalize_pricing_service_tier(service_tier)
    tier_pricing = _get_tier_pricing(pricing, pricing_service_tier)
    high_context_pricing = pricing.get("high_context_pricing")
    high_context_tier_pricing = None
    if high_context_pricing:
        high_context_tier_pricing = high_context_pricing.get(pricing_service_tier)

    # Usage can come from SDK objects or compatible proxy JSON. Treat malformed
    # and negative counters as zero instead of allowing accounting to crash at
    # the end of an otherwise successful generation.
    input_tokens = coerce_token_count(input_tokens)
    cached_input_tokens = min(
        coerce_token_count(cached_input_tokens),
        input_tokens,
    )
    cache_write_tokens = min(
        coerce_token_count(cache_write_tokens),
        input_tokens - cached_input_tokens,
    )
    output_tokens = coerce_token_count(output_tokens)
    reasoning_tokens = coerce_token_count(reasoning_tokens)

    ordinary_input_tokens = max(
        input_tokens - cached_input_tokens - cache_write_tokens, 0
    )

    input_tokens_cost = 0.0
    cached_input_tokens_cost = 0.0
    cache_write_tokens_cost = 0.0
    output_tokens_cost = 0.0

    effective_pricing = tier_pricing
    if high_context_pricing and high_context_tier_pricing:
        high_context_mark = max(int(high_context_pricing.get("mark", 0) or 0), 0)
        # Provider catalogs define whether the threshold is inclusive.  Once
        # reached, both OpenAI and xAI bill the full request at long-context
        # rates instead of applying a progressive surcharge above the mark.
        threshold_reached = (
            input_tokens >= high_context_mark
            if high_context_pricing.get("inclusive")
            else input_tokens > high_context_mark
        )
        if high_context_mark > 0 and threshold_reached:
            effective_pricing = high_context_tier_pricing

    input_tokens_cost = _cost_for_tokens(
        ordinary_input_tokens, effective_pricing.get("input", 0)
    )
    cached_input_tokens_cost = _cost_for_tokens(
        cached_input_tokens,
        effective_pricing.get("cached_input", 0),
    )
    cache_write_price = effective_pricing.get("cache_write")
    if cache_write_price is None:
        cache_write_price = float(effective_pricing.get("input", 0) or 0) * 1.25
    cache_write_tokens_cost = _cost_for_tokens(cache_write_tokens, cache_write_price)

    # Responses/Chat Completions output_tokens already includes the reasoning
    # subset reported in output_tokens_details.reasoning_tokens.
    output_tokens_cost = _cost_for_tokens(
        output_tokens, effective_pricing.get("output", 0)
    )

    native_websearch_price = pricing.get("native_web_search_tool_call", 0)
    native_websearch_costs = native_websearch_tool_calls_count * native_websearch_price

    total_costs = (
        input_tokens_cost
        + cached_input_tokens_cost
        + cache_write_tokens_cost
        + output_tokens_cost
        + native_websearch_costs
    )

    return {
        "input_tokens_cost": input_tokens_cost
        + cached_input_tokens_cost
        + cache_write_tokens_cost,
        "cached_input_tokens_cost": cached_input_tokens_cost,
        "cache_write_tokens_cost": cache_write_tokens_cost,
        "output_tokens_cost": output_tokens_cost,
        "native_websearch_costs": native_websearch_costs,
        "total_costs": total_costs,
    }


def _impl_merge_openai_cost_breakdown(
    target: dict[str, float],
    costs: dict | None,
) -> None:
    """Add one independently billed OpenAI request to a generation total.

    Long-context pricing is selected per API request. Keeping request costs
    separate until after tier selection prevents several sub-threshold tool
    rounds from being incorrectly priced as one high-context request.
    """
    if not costs:
        return
    for key in _OPENAI_COST_KEYS:
        try:
            value = float(costs.get(key, 0) or 0)
        except (TypeError, ValueError):
            value = 0.0
        target[key] = float(target.get(key, 0) or 0) + max(value, 0.0)
