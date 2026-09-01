import logging
from collections.abc import Callable
from typing import Any

import requests
from app.tools.websearch.domain_filters import (
    filter_websearch_provider_response_by_domains,
    filter_websearch_urls_by_domains,
    websearch_provider_has_domain_filters,
    websearch_url_is_allowed,
)
from app.tools.websearch.models import WebSearchProvider
from app.tools.websearch.robots import should_respect_robots_txt
from app.tools.websearch.scrape.aiohttp_scrape import aiohttp_scrape_urls
from app.tools.websearch.scrape.crawl4ai_scrape import crawl4ai_scrape
from app.tools.websearch.scrape.custom_scrape import custom_scrape_urls
from app.tools.websearch.scrape.exa_scrape import exa_scrape_urls
from app.tools.websearch.scrape.firecrawl_scrape import firecrawl_scrape_urls
from app.tools.websearch.scrape.ollama_scrape import ollama_scrape_urls
from app.tools.websearch.scrape.tavily_scrape import tavily_scrape_urls
from app.tools.websearch.scrape.you_scrape import you_scrape_urls
from fastapi import HTTPException

ScrapeHandler = Callable[
    [
        list[str],
        str | None,
        str | None,
        dict[str, Any],
        bool,
        Callable[[str], None] | None,
        Callable[[str], None] | None,
    ],
    list[dict[str, Any]] | dict[str, Any],
]


logger = logging.getLogger(__name__)


def _aiohttp(
    urls: list[str],
    _country: str | None,
    _language: str | None,
    settings: dict[str, Any],
    view_raw: bool,
    url_validator: Callable[[str], None] | None,
    resolved_ip_validator: Callable[[str], None] | None = None,
):
    return aiohttp_scrape_urls(
        urls,
        verify_ssl=bool(settings.get("verify_ssl_certificate", True)),
        view_raw=view_raw,
        url_validator=url_validator,
        resolved_ip_validator=resolved_ip_validator,
    )


def _exa(
    urls: list[str],
    _country: str | None,
    _language: str | None,
    settings: dict[str, Any],
    _view_raw: bool,
    _url_validator=None,
    _resolved_ip_validator=None,
):
    return exa_scrape_urls(settings.get("api_key"), urls)


def _firecrawl(
    urls: list[str],
    country: str | None,
    _language: str | None,
    settings: dict[str, Any],
    view_raw: bool,
    _url_validator=None,
    _resolved_ip_validator=None,
):
    return firecrawl_scrape_urls(
        settings.get("api_key"),
        urls,
        country,
        settings.get("fallback_country", "US"),
        settings.get("proxy", "auto"),
        view_raw,
        settings.get("base_url"),
        enterprise_option=settings.get("enterprise_option"),
    )


def _tavily(
    urls: list[str],
    _country: str | None,
    _language: str | None,
    settings: dict[str, Any],
    view_raw: bool,
    _url_validator=None,
    _resolved_ip_validator=None,
):
    return tavily_scrape_urls(settings.get("api_key"), urls, view_raw=view_raw)


def _crawl4ai(
    urls: list[str],
    _country: str | None,
    _language: str | None,
    settings: dict[str, Any],
    view_raw: bool,
    _url_validator=None,
    _resolved_ip_validator=None,
):
    return crawl4ai_scrape(
        settings.get("base_url"),
        urls,
        settings.get("retry_count", 3),
        view_raw,
        api_token=settings.get("api_key"),
    )


def _custom(
    urls: list[str],
    country: str | None,
    language: str | None,
    settings: dict[str, Any],
    view_raw: bool,
    _url_validator=None,
    _resolved_ip_validator=None,
):
    return custom_scrape_urls(
        settings.get("scrape_base_url") or settings.get("base_url"),
        urls,
        country=country,
        fallback_country=settings.get("fallback_country", "US"),
        language=language,
        view_raw=view_raw,
        url_validator=_url_validator,
    )


def _ollama(
    urls: list[str],
    _country: str | None,
    _language: str | None,
    settings: dict[str, Any],
    _view_raw: bool,
    _url_validator=None,
    _resolved_ip_validator=None,
):
    return ollama_scrape_urls(
        settings.get("api_key"),
        urls,
        respect_robots_txt=should_respect_robots_txt(settings, provider="ollama"),
    )


def _you(
    urls: list[str],
    _country: str | None,
    _language: str | None,
    settings: dict[str, Any],
    view_raw: bool,
    _url_validator=None,
    _resolved_ip_validator=None,
):
    return you_scrape_urls(
        settings.get("api_key"),
        urls,
        view_raw=view_raw,
    )


SCRAPE_HANDLERS: dict[str, ScrapeHandler] = {
    "aiohttp": _aiohttp,
    "crawl4ai": _crawl4ai,
    "custom": _custom,
    "exa": _exa,
    "firecrawl": _firecrawl,
    "ollama": _ollama,
    "tavily": _tavily,
    "you": _you,
}


def scrape(
    urls: list[str],
    country: str | None,
    language: str | None,
    db_model_scrape_provider: WebSearchProvider,
    view_raw: bool = False,
    url_validator: Callable[[str], None] | None = None,
    resolved_ip_validator: Callable[[str], None] | None = None,
    target_url_validator: Callable[[str], None] | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Dispatch scraping with distinct provider-endpoint and target policies.

    The custom adapter uses ``url_validator`` for its configured service URL.
    AIOHTTP follows target redirects locally, so it receives the stricter
    ``target_url_validator`` when one is supplied. Remote scraping services do
    not expose their redirect chain to this dispatcher.
    """

    provider_key = (
        str(getattr(db_model_scrape_provider, "provider", "") or "").strip().lower()
    )
    handler = SCRAPE_HANDLERS.get(provider_key)
    if handler is None:
        raise HTTPException(status_code=404, detail="Scrape provider not found")

    settings = (
        db_model_scrape_provider.settings
        if isinstance(db_model_scrape_provider.settings, dict)
        else {}
    )
    policy_active = websearch_provider_has_domain_filters(db_model_scrape_provider)
    filtered_urls, _domain_filter_metadata = filter_websearch_urls_by_domains(
        urls,
        db_model_scrape_provider,
    )
    if policy_active and not filtered_urls:
        return []

    effective_target_url_validator = target_url_validator
    if policy_active:

        def validate_target_url(target: str) -> None:
            """Apply the provider boundary before any caller-supplied checks."""

            if not websearch_url_is_allowed(target, db_model_scrape_provider):
                raise HTTPException(
                    status_code=403,
                    detail={"code": "websearch_domain_policy_blocked"},
                )
            if target_url_validator is not None:
                target_url_validator(target)

        effective_target_url_validator = validate_target_url

    handler_url_validator = (
        effective_target_url_validator
        if provider_key == "aiohttp" and effective_target_url_validator is not None
        else url_validator
    )
    try:
        response = handler(
            filtered_urls,
            country,
            language,
            settings,
            view_raw,
            handler_url_validator,
            resolved_ip_validator,
        )
        return filter_websearch_provider_response_by_domains(
            response,
            db_model_scrape_provider,
        )
    except HTTPException:
        raise
    except (requests.RequestException, OSError) as exc:
        raise HTTPException(
            status_code=502, detail=str(exc) or "Scrape provider request failed"
        ) from exc
    except Exception:
        logger.exception("Unexpected scrape handler failure")
        raise
