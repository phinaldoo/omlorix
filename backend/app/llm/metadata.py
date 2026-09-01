"""Shared helpers for stable, provider-independent LLM metadata."""

from typing import Any


def resolve_model_metadata_id(*candidates: Any) -> str | None:
    """Return the first non-empty model identifier from ordered candidates.

    Provider compatibility layers do not always echo a model identifier in
    their streaming events. Callers should therefore pass the provider value
    first and the configured/requested value second. A non-empty provider value
    remains authoritative, while ``None`` and blank values preserve the local
    fallback needed for persistence and UI diagnostics.
    """
    for candidate in candidates:
        if candidate is None:
            continue
        normalized = str(candidate).strip()
        if normalized:
            return normalized
    return None
