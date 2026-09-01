"""Shared lifecycle orchestration for provider generation adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.llm.generation.contracts import (
    GenerationAdapter,
    GenerationErrorDetails,
    GenerationRequest,
    GenerationResult,
)
from app.llmstats.models import create_llm_generation_statistic


_NO_FALLBACK = object()


@dataclass(slots=True)
class GenerationRunContext:
    """Mutable state shared by one generation operation and its finalizer."""

    db: Any
    model_name: str
    model_id: str
    provider: str
    provider_id: str | None
    category: str
    user_id: str | None = None
    is_byok: bool | None = None
    record_statistic: Callable[..., Any] = create_llm_generation_statistic
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    meta: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error: bool = False
    error_status_code: int | str = 0
    error_message: str = ""
    error_type: str = ""

    def apply_result(self, result: GenerationResult) -> None:
        """Merge normalized provider usage and metadata into the run state."""
        self.meta.update(result.usage)
        self.meta.update(result.metadata)

    def mark_success(self) -> None:
        """Mark completion and retain the existing successful timing behavior."""
        self.success = True
        self.meta["generation_time"] = (
            datetime.now(timezone.utc) - self.started_at
        ).total_seconds()

    def mark_error(self, details: GenerationErrorDetails) -> None:
        """Store normalized error details for the statistic finalizer."""
        self.error = True
        self.error_status_code = details.status_code
        self.error_message = details.message
        self.error_type = details.error_type

    def finalize(self, adapter: GenerationAdapter) -> None:
        """Calculate costs and persist exactly one generation statistic."""
        costs = adapter.calculate_costs(self.model_name, self.meta)
        if costs:
            self.meta.update(costs)

        statistic_kwargs = {
            "provider": self.provider,
            "provider_id": self.provider_id,
            "success": self.success,
            "error": self.error,
            "error_status_code": self.error_status_code,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "category": self.category,
            "meta": self.meta,
            "user_id": self.user_id,
        }
        # ``None`` means "let the statistics layer infer BYOK", matching older
        # callers that omitted the keyword entirely.
        if self.is_byok is not None:
            statistic_kwargs["is_byok"] = self.is_byok

        self.record_statistic(
            self.db,
            self.model_name,
            self.model_id,
            **statistic_kwargs,
        )


def _resolve_request(
    request: GenerationRequest | Callable[[], GenerationRequest],
) -> GenerationRequest:
    """Build deferred requests inside the lifecycle's error boundary."""
    return request() if callable(request) else request


def run_generation_once(
    adapter: GenerationAdapter,
    request: GenerationRequest | Callable[[], GenerationRequest],
    context: GenerationRunContext,
    *,
    fallback_on_error: str | object = _NO_FALLBACK,
    empty_text_fallback: str | object = _NO_FALLBACK,
    empty_text_error: Callable[[], Exception] | None = None,
    error_factory: Callable[[Exception, GenerationErrorDetails], Exception]
    | None = None,
) -> str:
    """Run a one-shot request with shared timing, errors, costs, and statistics."""
    try:
        result = adapter.generate_once(_resolve_request(request))
        context.apply_result(result)
        text = result.text
        if not text:
            if empty_text_error is not None:
                raise empty_text_error()
            if empty_text_fallback is not _NO_FALLBACK:
                text = str(empty_text_fallback)
        context.mark_success()
        return text
    except Exception as error:
        details = adapter.normalize_error(error)
        context.mark_error(details)
        if fallback_on_error is not _NO_FALLBACK:
            return str(fallback_on_error)
        if error_factory is not None:
            raise error_factory(error, details) from error
        raise
    finally:
        context.finalize(adapter)
