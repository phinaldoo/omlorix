"""Provider-neutral contracts for one-shot generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class GenerationRequest:
    """A normalized request understood by a provider generation adapter.

    ``messages`` intentionally remains a list of dictionaries. Providers have
    different content-block schemas, and forcing them into one lossy universal
    message model would move protocol branching into the shared service.
    """

    model: str
    system_instruction: str
    messages: list[dict[str, Any]]
    max_tokens: int
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GenerationResult:
    """Normalized terminal output from an adapter execution."""

    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GenerationErrorDetails:
    """Provider-neutral error fields persisted in generation statistics."""

    error_type: str
    message: str
    status_code: int | str = 0


@runtime_checkable
class GenerationAdapter(Protocol):
    """Execution boundary implemented by each provider integration."""

    def generate_once(self, request: GenerationRequest) -> GenerationResult:
        """Execute one request and return its completed normalized response."""
        ...

    def calculate_costs(
        self,
        model_name: str,
        usage: dict[str, Any],
    ) -> dict[str, float] | None:
        """Calculate provider-specific costs from normalized usage."""
        ...

    def normalize_error(self, error: Exception) -> GenerationErrorDetails:
        """Convert an SDK or application exception into statistic fields."""
        ...
