"""Model-specific wire constraints shared by both OpenAI API adapters."""

from app.llm.openai.catalog import get_responses_model_capabilities
from app.llm.openai.provider_types import is_openai_chat_completions_provider_type


class OpenAIRequestPolicyError(ValueError):
    code = "openai_tools_require_responses"
    i18n_key = "chat_openai_tools_require_responses"

    def __init__(self):
        super().__init__(
            "This model requires the OpenAI Responses provider for tools. "
            "Change the provider before continuing this conversation."
        )


def normalize_required_reasoning_effort(effort, caps):
    """Repair stale settings without changing optional-reasoning models."""
    if not caps or not caps.get("requires_reasoning"):
        return effort
    thinking = caps["thinking"]
    if effort in {"none", "minimal"}:
        return "low"
    if effort not in thinking["thinking_effort"]:
        return thinking["default_thinking_effort"]
    return effort


def apply_openai_request_policy(request, *, provider_type):
    """Apply constraints after settings/overrides and before provider I/O."""
    caps = get_responses_model_capabilities(request.get("model"), provider_type)
    if not caps or not caps.get("requires_reasoning"):
        return
    chat_completions = is_openai_chat_completions_provider_type(provider_type)
    bodies = [request]
    if isinstance(request.get("extra_body"), dict):
        bodies.append(request["extra_body"])
    for body in bodies:
        for key in ("temperature", "top_p", "top_logprobs", "logprobs"):
            body.pop(key, None)
        if isinstance(body.get("include"), list):
            body["include"] = [
                item
                for item in body["include"]
                if item != "message.output_text.logprobs"
            ]
        body.pop("prompt_cache_retention", None)
        if chat_completions and caps.get("tools_require_responses"):
            tool_history = any(
                isinstance(item, dict)
                and (
                    item.get("role") in {"tool", "function"}
                    or item.get("tool_calls")
                    or item.get("function_call")
                )
                for item in body.get("messages", [])
            )
            if body.get("tools") or body.get("functions") or tool_history:
                raise OpenAIRequestPolicyError()
            for key in (
                "tools",
                "functions",
                "tool_choice",
                "function_call",
                "parallel_tool_calls",
            ):
                body.pop(key, None)
    if chat_completions:
        for body in bodies:
            if body is request or "reasoning_effort" in body:
                body["reasoning_effort"] = normalize_required_reasoning_effort(
                    body.get("reasoning_effort"), caps
                )
    else:
        # extra_body replaces top-level keys in the SDK; normalize both copies.
        for body in bodies:
            if body is request or "reasoning" in body:
                reasoning = dict(body.get("reasoning") or {})
                reasoning["effort"] = normalize_required_reasoning_effort(
                    reasoning.get("effort"), caps
                )
                body["reasoning"] = reasoning
