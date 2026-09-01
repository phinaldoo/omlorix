"""OpenRouter usage normalization and aggregation.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openrouter import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "normalize_openrouter_usage": ("_as_float", "_as_int"),
    "merge_openrouter_usage": (
        "_OPENROUTER_ADDITIVE_USAGE_FIELDS",
        "normalize_openrouter_usage",
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
    "_OPENROUTER_ADDITIVE_USAGE_FIELDS",
    "_as_float",
    "_as_int",
    "normalize_openrouter_usage",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_normalize_openrouter_usage(usage: Any) -> dict[str, int | float | bool]:
    """Normalize one OpenRouter usage block into persisted Omlorix fields.

    OpenRouter is the billing authority because the routed upstream model and
    provider can vary per request. The response's reported costs are therefore
    retained instead of being reconstructed from catalog list prices.
    """
    if not isinstance(usage, dict):
        return {}

    prompt_details = usage.get("prompt_tokens_details") or usage.get(
        "input_tokens_details"
    )
    completion_details = usage.get("completion_tokens_details") or usage.get(
        "output_tokens_details"
    )
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    completion_details = (
        completion_details if isinstance(completion_details, dict) else {}
    )
    cost_details = usage.get("cost_details")
    cost_details = cost_details if isinstance(cost_details, dict) else {}

    input_tokens = max(
        _as_int(
            usage.get("prompt_tokens")
            if usage.get("prompt_tokens") is not None
            else usage.get("input_tokens")
        ),
        0,
    )
    output_tokens = max(
        _as_int(
            usage.get("completion_tokens")
            if usage.get("completion_tokens") is not None
            else usage.get("output_tokens")
        ),
        0,
    )
    total_tokens = max(_as_int(usage.get("total_tokens")), 0)
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens

    normalized: dict[str, int | float | bool] = {
        "input_tokens": input_tokens,
        "input_token_cached": max(_as_int(prompt_details.get("cached_tokens")), 0),
        "cache_write_tokens": max(
            _as_int(prompt_details.get("cache_write_tokens")),
            0,
        ),
        "input_token_image": max(_as_int(prompt_details.get("image_tokens")), 0),
        "input_token_audio": max(_as_int(prompt_details.get("audio_tokens")), 0),
        "input_token_video": max(_as_int(prompt_details.get("video_tokens")), 0),
        "output_tokens": output_tokens,
        "output_image_tokens": max(_as_int(completion_details.get("image_tokens")), 0),
        "output_audio_tokens": max(_as_int(completion_details.get("audio_tokens")), 0),
        "output_video_tokens": max(_as_int(completion_details.get("video_tokens")), 0),
        "reasoning_tokens": max(
            _as_int(completion_details.get("reasoning_tokens")),
            0,
        ),
        "total_tokens": total_tokens,
        "total_costs": max(_as_float(usage.get("cost")), 0.0),
        "upstream_inference_cost": max(
            _as_float(cost_details.get("upstream_inference_cost")),
            0.0,
        ),
        "input_tokens_cost": max(
            _as_float(cost_details.get("upstream_inference_prompt_cost")),
            0.0,
        ),
        "output_tokens_cost": max(
            _as_float(cost_details.get("upstream_inference_completions_cost")),
            0.0,
        ),
    }
    byok_value = usage.get("is_byok")
    if byok_value is None:
        byok_value = cost_details.get("is_byok")
    if byok_value is not None:
        normalized["meta_is_byok"] = bool(byok_value)
    return normalized


def _impl_merge_openrouter_usage(target: dict, usage: Any) -> dict:
    """Accumulate one routed request's usage without losing earlier rounds."""
    normalized = normalize_openrouter_usage(usage)
    for key in _OPENROUTER_ADDITIVE_USAGE_FIELDS:
        value = normalized.get(key, 0)
        target[key] = target.get(key, 0) + value
    if "meta_is_byok" in normalized:
        target["meta_is_byok"] = normalized["meta_is_byok"]
    return normalized
