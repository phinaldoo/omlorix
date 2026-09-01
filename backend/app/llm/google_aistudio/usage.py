"""Google AI Studio usage normalization and token-cost calculation.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "normalize_aistudio_usage_metadata": (
        "_aistudio_modality_token_counts",
        "_aistudio_usage_field",
        "_fit_modality_counts",
        "coerce_token_count",
    ),
    "calculate_aistudio_token_costs": (
        "_calculate_priced_tokens",
        "_fit_modality_counts",
        "_get_aistudio_pricing_for_model",
        "coerce_token_count",
    ),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate dependencies before definitions so annotations and defaults retain
# exactly the same evaluation behavior as in the original module.
for _dependency_name in (
    "_aistudio_modality_token_counts",
    "_aistudio_usage_field",
    "_calculate_priced_tokens",
    "_fit_modality_counts",
    "_get_aistudio_pricing_for_model",
    "coerce_token_count",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_normalize_aistudio_usage_metadata(usage_metadata: Any) -> dict[str, int]:
    """Return Google usage using Omlorix's canonical total/subset contract.

    Google reports ``prompt_token_count`` as the complete prompt total and
    ``cached_content_token_count`` as a subset. Tool execution can add a second
    billed input bucket in ``tool_use_prompt_token_count``; it is deliberately
    combined with the ordinary prompt here so both statistics and pricing cover
    every input token represented by Google's ``total_token_count``.
    """
    prompt_details = _aistudio_modality_token_counts(
        _aistudio_usage_field(usage_metadata, "prompt_tokens_details", [])
    )
    tool_prompt_details = _aistudio_modality_token_counts(
        _aistudio_usage_field(
            usage_metadata,
            "tool_use_prompt_tokens_details",
            [],
        )
    )
    cache_details = _aistudio_modality_token_counts(
        _aistudio_usage_field(usage_metadata, "cache_tokens_details", [])
    )

    prompt_total = coerce_token_count(
        _aistudio_usage_field(usage_metadata, "prompt_token_count", 0)
    )
    if prompt_total <= 0:
        prompt_total = sum(prompt_details.values())
    tool_prompt_total = coerce_token_count(
        _aistudio_usage_field(
            usage_metadata,
            "tool_use_prompt_token_count",
            0,
        )
    )
    if tool_prompt_total <= 0:
        tool_prompt_total = sum(tool_prompt_details.values())

    complete_input_total = prompt_total + tool_prompt_total
    combined_input_details = {
        modality: prompt_details[modality] + tool_prompt_details[modality]
        for modality in ("text", "image", "audio", "video")
    }
    input_modalities = _fit_modality_counts(
        complete_input_total,
        combined_input_details,
    )

    cached_total = coerce_token_count(
        _aistudio_usage_field(usage_metadata, "cached_content_token_count", 0)
    )
    if cached_total <= 0:
        cached_total = sum(cache_details.values())
    # Cached content is a subset of the original prompt, never of the tool
    # result tokens that are fed back to the model during agentic execution.
    cached_total = min(cached_total, prompt_total)

    # First honor the provider's cache modality details, bounded by the prompt
    # modality totals. Then allocate any unclassified cached remainder only to
    # modalities that still have prompt capacity.
    cached_modalities = {"text": 0, "image": 0, "audio": 0, "video": 0}
    remaining_cached = cached_total
    for modality in ("text", "image", "audio", "video"):
        cached_modalities[modality] = min(
            coerce_token_count(cache_details.get(modality, 0)),
            input_modalities[modality],
            remaining_cached,
        )
        remaining_cached -= cached_modalities[modality]
    for modality in ("text", "image", "audio", "video"):
        available = input_modalities[modality] - cached_modalities[modality]
        allocated = min(available, remaining_cached)
        cached_modalities[modality] += allocated
        remaining_cached -= allocated
        if remaining_cached <= 0:
            break
    cached_total = sum(cached_modalities.values())

    output_tokens = coerce_token_count(
        _aistudio_usage_field(usage_metadata, "candidates_token_count", 0)
    )
    reasoning_tokens = coerce_token_count(
        _aistudio_usage_field(usage_metadata, "thoughts_token_count", 0)
    )
    total_tokens = coerce_token_count(
        _aistudio_usage_field(usage_metadata, "total_token_count", 0)
    )
    if total_tokens <= 0:
        total_tokens = complete_input_total + output_tokens + reasoning_tokens

    return {
        "input_tokens": complete_input_total,
        "tool_use_prompt_tokens": tool_prompt_total,
        "input_token_cached": cached_total,
        "input_token_text": input_modalities["text"],
        "input_token_image": input_modalities["image"],
        "input_token_audio": input_modalities["audio"],
        "input_token_video": input_modalities["video"],
        "input_token_cached_text": cached_modalities["text"],
        "input_token_cached_image": cached_modalities["image"],
        "input_token_cached_audio": cached_modalities["audio"],
        "input_token_cached_video": cached_modalities["video"],
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def _impl_calculate_aistudio_token_costs(
    model_name: str | None,
    *,
    input_tokens_total: int = 0,
    input_text_tokens: int = 0,
    input_image_tokens: int = 0,
    input_audio_tokens: int = 0,
    input_video_tokens: int = 0,
    cached_input_tokens: int = 0,
    cached_input_text_tokens: int = 0,
    cached_input_image_tokens: int = 0,
    cached_input_audio_tokens: int = 0,
    cached_input_video_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
):
    """Calculate Gemini costs from total input and cached-input subsets."""
    pricing = _get_aistudio_pricing_for_model(model_name)
    if not pricing:
        return None

    total_input = coerce_token_count(input_tokens_total)
    input_modalities = _fit_modality_counts(
        total_input,
        {
            "text": input_text_tokens,
            "image": input_image_tokens,
            "audio": input_audio_tokens,
            "video": input_video_tokens,
        },
    )
    cached_total = min(coerce_token_count(cached_input_tokens), total_input)
    supplied_cached_modalities = {
        "text": cached_input_text_tokens,
        "image": cached_input_image_tokens,
        "audio": cached_input_audio_tokens,
        "video": cached_input_video_tokens,
    }
    cached_modalities = {"text": 0, "image": 0, "audio": 0, "video": 0}
    remaining_cached = cached_total
    for modality in ("text", "image", "audio", "video"):
        cached_modalities[modality] = min(
            coerce_token_count(supplied_cached_modalities[modality]),
            input_modalities[modality],
            remaining_cached,
        )
        remaining_cached -= cached_modalities[modality]
    for modality in ("text", "image", "audio", "video"):
        available = input_modalities[modality] - cached_modalities[modality]
        allocated = min(available, remaining_cached)
        cached_modalities[modality] += allocated
        remaining_cached -= allocated

    # Google's 200k threshold is based on the complete prompt, and the selected
    # tier applies to every input modality, cached input, and output token.
    use_high_context_price = total_input > 200_000
    ordinary_input_cost = 0.0
    cached_input_cost = 0.0
    for modality in ("text", "image", "audio", "video"):
        ordinary_tokens = input_modalities[modality] - cached_modalities[modality]
        ordinary_input_cost += _calculate_priced_tokens(
            pricing,
            f"input_{modality}",
            ordinary_tokens,
            use_high_context_price=use_high_context_price,
        )
        cached_input_cost += _calculate_priced_tokens(
            pricing,
            f"cached_input_{modality}",
            cached_modalities[modality],
            use_high_context_price=use_high_context_price,
            fallback_price_key=f"input_{modality}",
            fallback_multiplier=0.1,
        )

    input_cost = ordinary_input_cost + cached_input_cost
    total_output_tokens = coerce_token_count(output_tokens) + coerce_token_count(
        reasoning_tokens
    )
    output_cost = _calculate_priced_tokens(
        pricing,
        "output",
        total_output_tokens,
        use_high_context_price=use_high_context_price,
    )

    total_costs = input_cost + output_cost
    return {
        "input_tokens_cost": input_cost,
        "cached_input_tokens_cost": cached_input_cost,
        "output_tokens_cost": output_cost,
        "total_costs": total_costs,
    }
