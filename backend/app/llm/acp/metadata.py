"""ACP completion metadata and usage normalization.

The historical ``runtime`` module remains the public facade. Dependencies are
synchronized before calls to preserve its established patching surface.
"""

from __future__ import annotations

# Extracted implementations retain intentional diagnostic assignments.
# ruff: noqa: F821, F841

from app.llm.acp import runtime as _compat_source

_COMPAT_DEPENDENCIES = {
    "normalize_acp_completion_metadata": ("_protocol_payload", "_usage_int")
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


for _dependency_name in ("_protocol_payload", "_usage_int"):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_normalize_acp_completion_metadata(
    response: Any,
    *,
    selected_model_id: str | None = None,
    generation_time: float | None = None,
    time_to_first_token: float | None = None,
) -> dict[str, Any]:
    """Normalize ACP prompt usage into Omlorix's provider metadata vocabulary.

    ACP reports cached and thought tokens separately from ordinary input and
    output tokens. Omlorix's canonical input/output totals include those
    subsets, while retaining the subset fields for the diagnostics UI.
    """
    payload = _protocol_payload(response)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    private_meta = (
        payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    )
    quota = (
        private_meta.get("quota") if isinstance(private_meta.get("quota"), dict) else {}
    )
    quota_tokens = (
        quota.get("token_count") if isinstance(quota.get("token_count"), dict) else {}
    )

    # The standard response usage is authoritative. Codex ACP also mirrors the
    # same values in `_meta.quota.token_count`, which provides a useful fallback
    # for agents/SDK versions that omit the standard usage member.
    def read(*keys: str) -> int:
        reported = _usage_int(usage, *keys)
        if reported is not None:
            return reported
        fallback = _usage_int(quota_tokens, *keys)
        return fallback if fallback is not None else 0

    ordinary_input = read("inputTokens", "input_tokens")
    cached_input = read("cachedReadTokens", "cached_read_tokens", "cachedInputTokens")
    cache_write = read("cachedWriteTokens", "cached_write_tokens", "cacheWriteTokens")
    ordinary_output = read("outputTokens", "output_tokens")
    reasoning_output = read(
        "thoughtTokens",
        "thought_tokens",
        "reasoningOutputTokens",
        "reasoning_output_tokens",
    )
    standard_total = _usage_int(usage, "totalTokens", "total_tokens")
    quota_total = _usage_int(quota_tokens, "totalTokens", "total_tokens")
    reported_total_value = standard_total if standard_total is not None else quota_total
    has_reported_total = reported_total_value is not None
    reported_total = reported_total_value if reported_total_value is not None else 0

    expanded_input = ordinary_input + cached_input + cache_write
    expanded_output = ordinary_output + reasoning_output
    expanded_total = expanded_input + expanded_output

    # Avoid double-counting agents that already include cached/reasoning tokens
    # inside inputTokens/outputTokens. An exact total tells us which convention
    # that agent used; without a total, ACP's separate-field semantics apply.
    if reported_total and expanded_total != reported_total:
        if ordinary_input + expanded_output == reported_total:
            expanded_input = ordinary_input
        elif expanded_input + ordinary_output == reported_total:
            expanded_output = ordinary_output
        elif ordinary_input + ordinary_output == reported_total:
            expanded_input = ordinary_input
            expanded_output = ordinary_output

    total_tokens = (
        reported_total if has_reported_total else expanded_input + expanded_output
    )
    model_id = str(selected_model_id or "").strip()
    if not model_id:
        model_usage = quota.get("model_usage")
        if not isinstance(model_usage, list):
            model_usage = quota.get("modelUsage")
        for entry in model_usage if isinstance(model_usage, list) else []:
            if not isinstance(entry, dict):
                continue
            model_id = str(entry.get("model") or entry.get("modelId") or "").strip()
            if model_id:
                break

    metadata: dict[str, Any] = {}
    if model_id:
        metadata["model_id"] = model_id
    if expanded_input or any(key in usage for key in ("inputTokens", "input_tokens")):
        metadata["input_tokens"] = expanded_input
    if cached_input:
        metadata["input_token_cached"] = cached_input
    if cache_write:
        metadata["cache_write_tokens"] = cache_write
    if expanded_output or any(
        key in usage for key in ("outputTokens", "output_tokens")
    ):
        metadata["output_tokens"] = expanded_output
    if reasoning_output:
        metadata["reasoning_tokens"] = reasoning_output
    if total_tokens or has_reported_total:
        metadata["total_tokens"] = total_tokens

    stop_reason = str(
        payload.get("stopReason") or payload.get("stop_reason") or ""
    ).strip()
    if stop_reason:
        metadata["stop_reason"] = stop_reason
    if generation_time is not None and generation_time >= 0:
        metadata["generation_time"] = round(generation_time, 4)
        metadata["total_duration"] = round(generation_time, 4)
        if expanded_output > 0 and generation_time > 0:
            metadata["tokens_per_second"] = round(expanded_output / generation_time, 4)
    if time_to_first_token is not None and time_to_first_token >= 0:
        metadata["time_to_first_token"] = round(time_to_first_token, 4)
    return metadata
