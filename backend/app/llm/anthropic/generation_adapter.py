"""Anthropic implementation of the shared generation adapter contract."""

from __future__ import annotations

from typing import Any

from anthropic import APIStatusError

from app.llm.anthropic.request_settings import _apply_anthropic_simple_settings
from app.llm.anthropic.usage import (
    _usage_field,
    calculate_anthropic_token_costs,
    normalize_anthropic_usage_metadata,
)
from app.llm.generation.contracts import (
    GenerationErrorDetails,
    GenerationRequest,
    GenerationResult,
)


class AnthropicGenerationAdapter:
    """Translate shared generation requests to Anthropic's Messages API.

    The adapter owns only provider protocol behavior. Timing, fallback policy,
    error delivery, cost attachment, and statistic persistence are handled by
    the shared generation service.
    """

    def __init__(
        self,
        *,
        client: Any,
    ) -> None:
        """Create an adapter around a configured Anthropic client."""
        self._client = client

    def _request_kwargs(
        self,
        request: GenerationRequest,
    ) -> dict[str, Any]:
        """Build the Messages API payload for title generation."""
        request_kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": request.messages,
            "system": request.system_instruction,
        }
        _apply_anthropic_simple_settings(request_kwargs, request.settings)
        return request_kwargs

    def generate_once(self, request: GenerationRequest) -> GenerationResult:
        """Execute a non-streaming Anthropic Messages request."""
        response = self._client.messages.create(
            **self._request_kwargs(request)
        )
        text = response.content[0].text if response.content else ""
        usage = normalize_anthropic_usage_metadata(response.usage)
        metadata = {
            "stop_reason": response.stop_reason,
            "service_tier": _usage_field(
                response.usage,
                "service_tier",
                "",
            ),
        }
        return GenerationResult(text=text, usage=usage, metadata=metadata)

    def calculate_costs(
        self,
        model_name: str,
        usage: dict[str, Any],
    ) -> dict[str, float] | None:
        """Apply Anthropic's cache-aware token pricing to normalized usage."""
        return calculate_anthropic_token_costs(
            model_name=model_name,
            input_tokens=usage.get("input_tokens", 0),
            cached_input_tokens=usage.get("input_token_cached", 0),
            cache_write_tokens=usage.get("cache_write_tokens", 0),
            ephemeral_5m_input_tokens=usage.get("ephemeral_5m_input_tokens", 0),
            ephemeral_1h_input_tokens=usage.get("ephemeral_1h_input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            native_websearch_tool_calls_count=0,
        )

    def normalize_error(self, error: Exception) -> GenerationErrorDetails:
        """Extract Anthropic API error fields without exposing SDK internals."""
        if isinstance(error, APIStatusError):
            body = error.body if isinstance(error.body, dict) else {}
            error_payload = body.get("error")
            error_payload = error_payload if isinstance(error_payload, dict) else {}
            return GenerationErrorDetails(
                error_type=str(error_payload.get("type") or "unknown_error"),
                message=str(error_payload.get("message") or error),
                status_code=error.status_code,
            )
        return GenerationErrorDetails(
            error_type=error.__class__.__name__,
            message=str(error),
            status_code=getattr(error, "status_code", 0),
        )
