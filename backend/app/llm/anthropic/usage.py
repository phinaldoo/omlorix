"""Anthropic token normalization and provider-specific cost accounting."""

from typing import Any

from app.llm.token_usage import coerce_token_count


# Anthropic bills its automatic cache at the default five-minute write rate.
# One-hour accounting remains supported defensively for historical responses
# and Anthropic-compatible endpoints, even though Omlorix never requests it.
ANTHROPIC_CACHE_READ_INPUT_MULTIPLIER = 0.1
ANTHROPIC_5M_CACHE_WRITE_INPUT_MULTIPLIER = 1.25
ANTHROPIC_1H_CACHE_WRITE_INPUT_MULTIPLIER = 2.0


def _usage_field(obj: Any, key: str, default=0):
    """Get usage field from object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def normalize_anthropic_usage_metadata(usage: Any) -> dict[str, int]:
    """Normalize Anthropic's disjoint input counters into Omlorix totals.

    Anthropic does not include cache reads or cache writes in ``input_tokens``.
    Omlorix, on the other hand, uses ``input_tokens`` as the complete input total
    and stores cache reads/writes as subsets of that total.  Keeping this
    conversion in one helper prevents statistics, rate limits, and pricing from
    applying incompatible interpretations to the same provider response.
    """
    ordinary_input_tokens = coerce_token_count(_usage_field(usage, "input_tokens", 0))
    cache_read_tokens = coerce_token_count(
        _usage_field(usage, "cache_read_input_tokens", 0)
    )
    cache_write_tokens = coerce_token_count(
        _usage_field(usage, "cache_creation_input_tokens", 0)
    )
    output_tokens = coerce_token_count(_usage_field(usage, "output_tokens", 0))

    cache_creation = _usage_field(usage, "cache_creation", None)
    ephemeral_5m_tokens = coerce_token_count(
        _usage_field(cache_creation, "ephemeral_5m_input_tokens", 0)
    )
    ephemeral_1h_tokens = coerce_token_count(
        _usage_field(cache_creation, "ephemeral_1h_input_tokens", 0)
    )

    # Older Anthropic-compatible endpoints can report only the aggregate cache
    # creation count.  Anthropic's default TTL is five minutes, so assigning the
    # unclassified remainder there preserves correct default pricing.
    classified_cache_writes = min(
        ephemeral_5m_tokens + ephemeral_1h_tokens,
        cache_write_tokens,
    )
    ephemeral_5m_tokens = min(ephemeral_5m_tokens, cache_write_tokens)
    ephemeral_1h_tokens = min(
        ephemeral_1h_tokens,
        cache_write_tokens - ephemeral_5m_tokens,
    )
    if classified_cache_writes < cache_write_tokens:
        ephemeral_5m_tokens += cache_write_tokens - classified_cache_writes

    total_input_tokens = ordinary_input_tokens + cache_read_tokens + cache_write_tokens
    return {
        "input_tokens": total_input_tokens,
        "input_token_cached": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "ephemeral_5m_input_tokens": ephemeral_5m_tokens,
        "ephemeral_1h_input_tokens": ephemeral_1h_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_input_tokens + output_tokens,
    }


def calculate_anthropic_token_costs(
    model_name: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    native_websearch_tool_calls_count: int,
    *,
    cache_write_tokens: int = 0,
    ephemeral_5m_input_tokens: int = 0,
    ephemeral_1h_input_tokens: int = 0,
):
    """Calculate Anthropic costs from canonical total/subset token counters.

    ``input_tokens`` is Omlorix's total input count. Cache reads and writes are
    subsets and are removed before applying the ordinary input rate. Anthropic
    prices cache reads at 0.1x, five-minute writes at 1.25x, and one-hour writes
    at 2x the ordinary input price.
    """
    from app.llm.anthropic.model_list import get_anthropic_pricing

    pricing = get_anthropic_pricing(model_name)
    if not pricing:
        return None

    # Clamp provider values before deriving disjoint pricing buckets. This also
    # guarantees that malformed provider data can never create a negative cost.
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

    ephemeral_5m_input_tokens = min(
        coerce_token_count(ephemeral_5m_input_tokens),
        cache_write_tokens,
    )
    ephemeral_1h_input_tokens = min(
        coerce_token_count(ephemeral_1h_input_tokens),
        cache_write_tokens - ephemeral_5m_input_tokens,
    )
    unclassified_cache_writes = max(
        cache_write_tokens - ephemeral_5m_input_tokens - ephemeral_1h_input_tokens,
        0,
    )
    ephemeral_5m_input_tokens += unclassified_cache_writes

    input_price = float(pricing.get("input", 0) or 0)
    output_price = float(pricing.get("output", 0) or 0)
    ordinary_input_tokens = max(
        input_tokens - cached_input_tokens - cache_write_tokens,
        0,
    )

    ordinary_input_cost = (ordinary_input_tokens / 1_000_000) * input_price
    cached_input_tokens_cost = (
        (cached_input_tokens / 1_000_000)
        * input_price
        * ANTHROPIC_CACHE_READ_INPUT_MULTIPLIER
    )
    cache_write_tokens_cost = (
        ephemeral_5m_input_tokens / 1_000_000
    ) * input_price * ANTHROPIC_5M_CACHE_WRITE_INPUT_MULTIPLIER + (
        ephemeral_1h_input_tokens / 1_000_000
    ) * input_price * ANTHROPIC_1H_CACHE_WRITE_INPUT_MULTIPLIER
    output_tokens_cost = (output_tokens / 1_000_000) * output_price

    # 4. Calculate the native websearch tool calls costs
    native_websearch_price = pricing.get("native_web_search_tool_call", 0)
    native_websearch_costs = native_websearch_tool_calls_count * native_websearch_price

    # 5. Calculate the total costs together
    input_tokens_cost = (
        ordinary_input_cost + cached_input_tokens_cost + cache_write_tokens_cost
    )
    total_costs = input_tokens_cost + output_tokens_cost + native_websearch_costs

    # 6. Return all data
    return {
        "input_tokens_cost": input_tokens_cost,
        "cached_input_tokens_cost": cached_input_tokens_cost,
        "cache_write_tokens_cost": cache_write_tokens_cost,
        "output_tokens_cost": output_tokens_cost,
        "native_websearch_costs": native_websearch_costs,
        "total_costs": total_costs,
    }
