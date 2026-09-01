from typing import Any

from app.tools.websearch.combined.exa_combined import exa_web_search_combined
from app.tools.websearch.combined.ollama_combined import ollama_web_search_combined
from app.tools.websearch.combined.perplexity_combined import (
    PerplexityDomainFilterError,
    perplexity_combined_search,
)
from app.tools.websearch.domain_filters import (
    filter_websearch_provider_response_by_domains,
    resolve_websearch_provider_domain_filters,
)
from fastapi import HTTPException


def _resolve_fallback_language(settings: dict[str, Any]) -> str:
    raw_value = settings.get("fallback_language", settings.get("search_language_filter"))
    if isinstance(raw_value, str):
        candidate = raw_value.strip().lower()
        if len(candidate) == 2 and candidate.isalpha():
            return candidate
    if isinstance(raw_value, list):
        for entry in raw_value:
            if isinstance(entry, str):
                candidate = entry.strip().lower()
                if len(candidate) == 2 and candidate.isalpha():
                    return candidate
    return "en"


def run_combined_provider(
    provider,
    query: str,
    country: str | None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Dispatch a query to a combined provider with its resolved country hint."""

    settings = provider.settings if isinstance(provider.settings, dict) else {}
    provider_key = str(getattr(provider, "provider", "") or "").strip().lower()

    try:
        if provider_key == "ollama":
            response = ollama_web_search_combined(
                settings.get("api_key"),
                query,
                int(settings.get("max_search_results", 5)),
            )
        elif provider_key == "perplexity":
            allowed_domains, blocked_domains = (
                resolve_websearch_provider_domain_filters(provider)
            )
            fallback_country = settings.get(
                "fallback_country",
                settings.get("default_country", "US"),
            )
            resolved_country = (
                str(country or "").strip().upper()
                or str(fallback_country or "US").strip().upper()
            )
            fallback_language = _resolve_fallback_language(settings)
            response = perplexity_combined_search(
                settings.get("api_key"),
                query,
                country=resolved_country,
                max_results=int(settings.get("max_results", 5)),
                max_tokens_per_page=int(settings.get("max_tokens_per_page", 2048)),
                max_tokens=int(settings.get("max_tokens", 4096)),
                allowed_domains=allowed_domains or None,
                blocked_domains=blocked_domains or None,
                search_language_filter=[fallback_language],
            )
        elif provider_key == "exa":
            include_domains, exclude_domains = (
                resolve_websearch_provider_domain_filters(provider)
            )
            # The request-specific country is present only when the admin opted
            # into profile-locale forwarding; otherwise use the fixed fallback.
            fallback_country = settings.get("fallback_country", "US")
            resolved_country = (
                str(country or "").strip().upper()
                or str(fallback_country or "US").strip().upper()
            )
            response = exa_web_search_combined(
                settings.get("api_key"),
                query,
                max_results=int(settings.get("max_search_results", 5)),
                search_type=settings.get("type"),
                user_location=resolved_country,
                include_domains=include_domains or None,
                exclude_domains=exclude_domains or None,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Combined provider '{provider_key}' not supported")
    except HTTPException:
        raise
    except PerplexityDomainFilterError as exc:
        # Persisted settings pass schema validation, but this adapter-level guard
        # also protects manually edited or otherwise malformed database rows.
        detail: dict[str, Any] = {
            "code": exc.code,
            "provider": "perplexity",
        }
        if exc.max_rules is not None:
            detail["max_rules"] = exc.max_rules
        raise HTTPException(
            status_code=422,
            detail=detail,
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{provider_key} failed: {exc}") from exc
    return filter_websearch_provider_response_by_domains(response, provider)
