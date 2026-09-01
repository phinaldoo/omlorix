from collections.abc import Callable
import logging
from typing import Any

from fastapi import HTTPException

from app.tools.websearch.models import WebSearchProvider
from app.tools.websearch.search.custom_search import custom_search_urls
from app.tools.websearch.search.ddgs_search import duckduckgo_search_urls
from app.tools.websearch.search.firecrawl_search import firecrawl_search_urls
from app.tools.websearch.search.searxng_search import searxng_search_urls
from app.tools.websearch.search.serper_search import serper_search_urls
from app.tools.websearch.search.tavily_search import tavily_search_urls
from app.tools.websearch.search.you_search import you_search_urls


SearchHandler = Callable[[str, str | None, str | None, dict[str, Any]], list[dict[str, str]] | dict[str, Any]]


logger = logging.getLogger(__name__)


def _duckduckgo(query: str, country: str | None, language: str | None, settings: dict[str, Any]):
    return duckduckgo_search_urls(
        query,
        language,
        country,
        settings.get("fallback_language", "en"),
        settings.get("fallback_country", "us"),
        settings.get("max_search_results", 5),
        settings.get("safesearch", "moderate"),
    )


def _firecrawl(query: str, country: str | None, _language: str | None, settings: dict[str, Any]):
    return firecrawl_search_urls(
        settings.get("api_key"),
        query,
        country,
        settings.get("fallback_country", "US"),
        settings.get("max_search_results", 5),
        settings.get("enterprise_option"),
        settings.get("base_url"),
    )


def _searxng(query: str, _country: str | None, language: str | None, settings: dict[str, Any]):
    return searxng_search_urls(
        settings.get("base_url"),
        query,
        language=language,
        fallback_language=settings.get("fallback_language", "en"),
        num_results=settings.get("num_results", 10),
    )


def _tavily(query: str, country: str | None, _language: str | None, settings: dict[str, Any]):
    return tavily_search_urls(
        settings.get("api_key"),
        query,
        country,
        settings.get("fallback_country", "US"),
    )


def _serper(query: str, country: str | None, language: str | None, settings: dict[str, Any]):
    return serper_search_urls(
        settings.get("api_key"),
        query,
        language=language,
        country=country,
        fallback_language=settings.get("fallback_language", "en"),
        fallback_country=settings.get("fallback_country", "US"),
        num_results=settings.get("num_results", 10),
    )


def _you(query: str, country: str | None, _language: str | None, settings: dict[str, Any]):
    return you_search_urls(
        settings.get("api_key"),
        query,
        country=country,
        fallback_country=settings.get("fallback_country", "US"),
        count=settings.get("count", 10),
    )


def _custom(query: str, country: str | None, _language: str | None, settings: dict[str, Any]):
    return custom_search_urls(
        settings.get("base_url"),
        query,
        country=country,
        fallback_country=settings.get("fallback_country", "US"),
        num_results=settings.get("num_results", 10),
    )


SEARCH_HANDLERS: dict[str, SearchHandler] = {
    "custom": _custom,
    "duckduckgo": _duckduckgo,
    "firecrawl": _firecrawl,
    "searxng": _searxng,
    "serper": _serper,
    "tavily": _tavily,
    "you": _you,
}


def search(
    query: str,
    country: str | None,
    language: str | None,
    db_model_search_provider: WebSearchProvider,
) -> list[dict[str, str]] | dict[str, Any]:
    provider_key = str(getattr(db_model_search_provider, "provider", "") or "").strip().lower()
    handler = SEARCH_HANDLERS.get(provider_key)
    if handler is None:
        raise HTTPException(status_code=404, detail="Search provider not found")

    settings = db_model_search_provider.settings if isinstance(db_model_search_provider.settings, dict) else {}
    try:
        return handler(query, country, language, settings)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error running handler %s", handler)
        raise HTTPException(status_code=500, detail=str(exc) or "Internal server error") from exc
