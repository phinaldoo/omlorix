import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.schemas import tool_schemas
from app.tools.websearch import utils as websearch_utils
from app.tools.websearch.utils import normalize_web_search_call_args


def test_web_search_call_args_allow_queries_and_urls_together_in_web_mode():
    normalized = normalize_web_search_call_args(
        queries=["alpha"],
        urls=["https://example.com"],
        search_mode="web",
        view_raw=True,
    )

    assert normalized["queries"] == ["alpha"]
    assert normalized["urls"] == ["https://example.com"]
    assert normalized["search_mode"] == "web"
    assert normalized["view_raw"] is True


def test_web_search_call_args_reject_urls_in_image_mode():
    with pytest.raises(ValueError, match="search_mode"):
        normalize_web_search_call_args(
            queries=["alpha"],
            urls=["https://example.com"],
            search_mode="images",
        )


def test_web_search_tool_schema_allows_mixed_inputs():
    schema = tool_schemas["web_search"]
    assert "exactly one" not in schema["description"].lower()


def test_direct_url_fetch_reuses_exa_search_provider(monkeypatch):
    """Exa search configuration also supplies its direct URL scraper."""

    exa_provider = type("Provider", (), {"provider": "exa"})()
    requested_provider_ids = []

    def fake_get_provider(_db, provider_id):
        requested_provider_ids.append(provider_id)
        if provider_id == "exa-provider":
            return exa_provider
        raise AssertionError("The separate scrape provider must not be resolved for Exa")

    monkeypatch.setattr(websearch_utils, "get_websearch_provider", fake_get_provider)

    resolved = websearch_utils._resolve_direct_url_scrape_provider(
        None,
        search_provider_id="exa-provider",
        scrape_provider_id=None,
    )

    assert resolved is exa_provider
    assert requested_provider_ids == ["exa-provider"]


def test_direct_url_fetch_keeps_configured_scraper_for_non_exa_search(monkeypatch):
    """A direct URL uses its scraper without resolving an unrelated search provider."""

    scrape_provider = type("Provider", (), {"provider": "firecrawl"})()
    requested_provider_ids = []

    def fake_get_provider(_db, provider_id):
        requested_provider_ids.append(provider_id)
        if provider_id == "search-provider":
            raise RuntimeError("stale search provider")
        return scrape_provider

    monkeypatch.setattr(websearch_utils, "get_websearch_provider", fake_get_provider)

    resolved = websearch_utils._resolve_direct_url_scrape_provider(
        None,
        search_provider_id="search-provider",
        scrape_provider_id="scrape-provider",
    )

    assert resolved is scrape_provider
    assert requested_provider_ids == ["scrape-provider"]
