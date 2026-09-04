"""Anthropic entry points for shared lightweight generation workflows."""

from __future__ import annotations

from typing import Any

from app.llm.anthropic.generation_adapter import AnthropicGenerationAdapter
from app.llm.anthropic.models import get_anthropic_client
from app.llm.anthropic.request_settings import _merge_anthropic_simple_settings
from app.llm.generation import (
    GenerationRequest,
    GenerationRunContext,
    run_generation_once,
)
from app.llmstats.models import create_llm_generation_statistic


def _resolve_model_identifiers(model: Any) -> tuple[str, str]:
    """Resolve catalog objects and raw model names to statistic identifiers."""
    model_name = model.model_name if hasattr(model, "model_name") else str(model)
    model_id = getattr(model, "id", None) if hasattr(model, "id") else None
    return model_name, model_id or model_name


def _user_message_request(
    *,
    model: str,
    prompt: str,
    system_instruction: str,
    max_tokens: int,
    settings: dict[str, Any],
) -> GenerationRequest:
    """Build a normalized Anthropic request containing one user text block."""
    return GenerationRequest(
        model=model,
        system_instruction=system_instruction,
        max_tokens=max_tokens,
        settings=settings,
        messages=[
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
    )


def _adapter(
    *,
    client: Any,
) -> AnthropicGenerationAdapter:
    """Create an Anthropic adapter from a configured client."""
    return AnthropicGenerationAdapter(client=client)


def anthropic_title_generation(
    db,
    model: str,
    prompt: str,
    system_instruction: str,
    anthropic_provider_id: str | None = None,
    byok: dict | None = None,
    user_id: str | None = None,
    model_settings: dict | None = None,
    settings_override: dict | None = None,
    generation_category: str = "title_generation",
    output_char_limit: int | None = None,
    max_output_tokens: int | None = None,
    response_schema: dict | None = None,
    raise_on_error: bool = False,
):
    """Generate a title through the shared non-streaming service."""
    fallback_limit = output_char_limit if output_char_limit is not None else 60
    fallback = prompt[: max(1, int(fallback_limit))]
    # Preserve the historical fast fallback: client configuration failures do
    # not create a generation statistic because no provider request was made.
    try:
        if byok:
            client = get_anthropic_client(db, api_key=byok.get("api_key"))
        else:
            client = get_anthropic_client(db, anthropic_provider_id)
    except Exception:
        if raise_on_error:
            raise
        return fallback

    model_name, model_id = _resolve_model_identifiers(model)
    adapter = _adapter(client=client)
    context = GenerationRunContext(
        db=db,
        model_name=model_name,
        model_id=model_id,
        provider="anthropic",
        provider_id=anthropic_provider_id,
        category=generation_category,
        user_id=user_id,
        is_byok=bool(byok),
        record_statistic=create_llm_generation_statistic,
    )

    def request() -> GenerationRequest:
        settings = _merge_anthropic_simple_settings(model_settings, settings_override)
        return _user_message_request(
            model=model_name,
            prompt=prompt,
            system_instruction=system_instruction,
            max_tokens=max(1, int(max_output_tokens or 100)),
            settings=settings,
        )

    if raise_on_error:
        result = run_generation_once(
            adapter,
            request,
            context,
            empty_text_error=lambda: RuntimeError("empty_model_output"),
        )
    else:
        result = run_generation_once(
            adapter,
            request,
            context,
            fallback_on_error=fallback,
            empty_text_fallback=fallback,
        )
    if output_char_limit is None:
        return result
    return result[: max(1, int(output_char_limit))]
