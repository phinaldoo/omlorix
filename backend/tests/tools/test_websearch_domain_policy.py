import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.websearch import utils as websearch_utils
from app.tools.websearch.combined import perplexity_combined as perplexity_adapter
from app.tools.websearch.combined import utils as combined_utils
from app.tools.websearch.domain_filters import (
    filter_websearch_result_entries_by_domains,
    url_is_allowed_by_domains,
)
from app.tools.websearch.schemas import (
    WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS,
    WebSearchProviderSettingsPerplexity,
)
from app.tools.websearch.scrape import utils as scrape_utils


def _stub_websearch_bookkeeping(monkeypatch) -> None:
    """Remove pricing and persistence work that is unrelated to domain policy."""

    monkeypatch.setattr(
        websearch_utils,
        "build_websearch_usage_event",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        websearch_utils,
        "build_websearch_tool_meta",
        lambda *, base_meta, usage_events: {
            **base_meta,
            "usage_events": usage_events,
        },
    )
    monkeypatch.setattr(
        websearch_utils,
        "_build_web_result_content",
        lambda **kwargs: {
            "webpages": kwargs["webpages"],
            "documents": [],
            "videos": [],
            "audios": [],
            "images": [],
        },
    )


def _provider(provider_id: str, provider: str, **settings):
    """Build the minimal persisted-provider shape consumed by the workflow."""

    return SimpleNamespace(
        id=provider_id,
        provider=provider,
        settings=settings,
    )


def test_query_pipeline_filters_all_urls_before_type_detection_and_scraping(
    monkeypatch,
):
    """A scraper policy must run before classification performs network I/O."""

    search_provider = _provider("search", "duckduckgo")
    scrape_provider = _provider(
        "scrape",
        "aiohttp",
        allowed_domains=["allowed.example"],
        blocked_domains=["blocked.allowed.example"],
        respect_robots_txt=False,
    )
    providers = {"search": search_provider, "scrape": scrape_provider}
    classifier_calls: list[list[str]] = []
    scrape_calls: list[list[str]] = []

    monkeypatch.setattr(
        websearch_utils,
        "get_websearch_provider",
        lambda _db, provider_id: providers[provider_id],
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        websearch_utils,
        "search",
        lambda *_args, **_kwargs: {
            "result": [
                {"url": "https://allowed.example/page"},
                {"url": "https://blocked.allowed.example/page"},
                {"url": "https://outside.example/report.pdf"},
            ]
        },
    )
    monkeypatch.setattr(
        websearch_utils,
        "_split_allowed_web_urls",
        lambda _db, urls, **_kwargs: (list(urls), []),
    )
    monkeypatch.setattr(
        websearch_utils,
        "should_respect_robots_txt",
        lambda *_args, **_kwargs: False,
    )

    def fake_filter_urls(urls, **_kwargs):
        classifier_calls.append(list(urls))
        return {
            "websites": list(urls),
            "documents": [],
            "images": [],
            "videos": [],
            "audios": [],
        }

    def fake_scrape(urls, *_args, **_kwargs):
        scrape_calls.append(list(urls))
        return {
            "result": [{"url": url, "content": f"content for {url}"} for url in urls]
        }

    monkeypatch.setattr(websearch_utils, "filter_urls", fake_filter_urls)
    monkeypatch.setattr(websearch_utils, "scrape", fake_scrape)
    _stub_websearch_bookkeeping(monkeypatch)

    websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id="scrape",
        search_provider_id="search",
        project_id=None,
        queries=["test"],
    )

    expected = ["https://allowed.example/page"]
    assert classifier_calls == [expected]
    assert scrape_calls == [expected]


def test_direct_pipeline_filters_before_classification_and_rechecks_results(
    monkeypatch,
):
    """Direct URLs and remote redirect results must obey the same provider policy."""

    provider = _provider(
        "scrape",
        "aiohttp",
        allowed_domains=["allowed.example"],
        blocked_domains=[],
        respect_robots_txt=False,
    )
    classifier_calls: list[list[str]] = []
    scrape_calls: list[list[str]] = []
    captured_classifier_validator = None
    captured_target_validator = None

    monkeypatch.setattr(
        websearch_utils,
        "_resolve_direct_url_scrape_provider",
        lambda *_args, **_kwargs: provider,
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        websearch_utils,
        "_split_allowed_web_urls",
        lambda _db, urls, **_kwargs: (list(urls), []),
    )
    monkeypatch.setattr(
        websearch_utils,
        "should_respect_robots_txt",
        lambda *_args, **_kwargs: False,
    )

    def fake_filter_urls(urls, **kwargs):
        nonlocal captured_classifier_validator
        classifier_calls.append(list(urls))
        captured_classifier_validator = kwargs.get("target_url_validator")
        return {
            "websites": list(urls),
            "documents": [],
            "images": [],
            "videos": [],
            "audios": [],
        }

    def fake_scrape(urls, *_args, **kwargs):
        nonlocal captured_target_validator
        scrape_calls.append(list(urls))
        captured_target_validator = kwargs.get("target_url_validator")
        return {
            "result": [
                {
                    "url": "https://outside.example/redirect-target",
                    "content": "must not cross the policy boundary",
                },
                {
                    "url": "https://allowed.example/page",
                    "content": "allowed",
                },
            ]
        }

    monkeypatch.setattr(websearch_utils, "filter_urls", fake_filter_urls)
    monkeypatch.setattr(websearch_utils, "scrape", fake_scrape)
    _stub_websearch_bookkeeping(monkeypatch)

    response = websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id="scrape",
        search_provider_id=None,
        project_id=None,
        urls=[
            "https://allowed.example/page",
            "https://outside.example/page",
        ],
    )

    expected = ["https://allowed.example/page"]
    assert classifier_calls == [expected]
    assert scrape_calls == [expected]
    assert captured_classifier_validator is not None
    with pytest.raises(HTTPException):
        captured_classifier_validator("https://outside.example/classifier-redirect")
    assert captured_target_validator is not None
    with pytest.raises(HTTPException) as exc_info:
        captured_target_validator("https://outside.example/redirect-target")
    assert exc_info.value.detail["code"] == "websearch_domain_policy_blocked"
    assert [
        entry["url"] for entry in response["result"][0]["content"]["webpages"]
    ] == expected


def test_combined_pipeline_locally_rechecks_provider_results(monkeypatch):
    """Native provider filters must be backed by a local response check."""

    provider = _provider(
        "combined",
        "exa",
        allowed_domains=["allowed.example"],
        blocked_domains=[],
        respect_robots_txt=False,
    )
    monkeypatch.setattr(
        websearch_utils,
        "get_websearch_provider",
        lambda _db, _provider_id: provider,
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        websearch_utils,
        "run_combined_provider",
        lambda *_args, **_kwargs: {
            "result": [
                {"url": "https://outside.example/page", "content": "blocked"},
                {"url": "https://allowed.example/page", "content": "allowed"},
            ],
            "metadata": {},
        },
    )
    monkeypatch.setattr(
        websearch_utils,
        "_assert_websearch_url_allowed",
        lambda *_args, **_kwargs: None,
    )
    _stub_websearch_bookkeeping(monkeypatch)

    response = websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id=None,
        search_provider_id="combined",
        project_id=None,
        queries=["test"],
    )

    webpages = response["result"][0]["content"]["webpages"]
    assert [entry["url"] for entry in webpages] == ["https://allowed.example/page"]


def test_ollama_combined_policy_filters_returned_urls(monkeypatch):
    """Ollama runs with a domain policy and returned URLs are filtered locally."""

    provider = _provider(
        "combined",
        "ollama",
        allowed_domains=["allowed.example"],
        blocked_domains=[],
        respect_robots_txt=False,
    )
    provider_called = False

    monkeypatch.setattr(
        websearch_utils,
        "get_websearch_provider",
        lambda _db, _provider_id: provider,
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        websearch_utils,
        "_assert_websearch_url_allowed",
        lambda *_args, **_kwargs: None,
    )

    def fake_run_combined_provider(*_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        return {
            "result": [
                {"url": "https://allowed.example/page", "content": "allowed"},
                {"url": "https://outside.example/page", "content": "blocked"},
            ]
        }

    monkeypatch.setattr(
        websearch_utils,
        "run_combined_provider",
        fake_run_combined_provider,
    )
    _stub_websearch_bookkeeping(monkeypatch)

    response = websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id=None,
        search_provider_id="combined",
        project_id=None,
        queries=["test"],
    )

    assert provider_called is True
    assert response["result"][0]["content"]["webpages"] == [
        {"id": 1, "url": "https://allowed.example/page", "content": "allowed"}
    ]


def test_ollama_combined_without_domain_policy_preserves_existing_behavior(monkeypatch):
    """Ollama combined search remains available when no boundary is configured."""

    provider = _provider(
        "combined",
        "ollama",
        allowed_domains=[],
        blocked_domains=[],
        respect_robots_txt=False,
    )
    provider_called = False

    monkeypatch.setattr(
        websearch_utils,
        "get_websearch_provider",
        lambda _db, _provider_id: provider,
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        websearch_utils,
        "_assert_websearch_url_allowed",
        lambda *_args, **_kwargs: None,
    )

    def fake_run_combined_provider(*_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        return {
            "result": [{"url": "https://outside.example/page", "content": "available"}]
        }

    monkeypatch.setattr(
        websearch_utils,
        "run_combined_provider",
        fake_run_combined_provider,
    )
    _stub_websearch_bookkeeping(monkeypatch)

    response = websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id=None,
        search_provider_id="combined",
        project_id=None,
        queries=["test"],
    )

    assert provider_called is True
    assert response["result"][0]["content"]["webpages"][0]["url"] == (
        "https://outside.example/page"
    )


def test_domain_policy_matches_subdomains_and_block_rules_take_priority():
    """Central matching uses exact/subdomain semantics and block wins."""

    assert url_is_allowed_by_domains(
        "https://docs.allowed.example/page",
        allowed_domains=["allowed.example"],
        blocked_domains=["private.allowed.example"],
    )
    assert not url_is_allowed_by_domains(
        "https://private.allowed.example/page",
        allowed_domains=["allowed.example"],
        blocked_domains=["private.allowed.example"],
    )
    assert not url_is_allowed_by_domains(
        "not-a-url",
        allowed_domains=["allowed.example"],
    )
    assert url_is_allowed_by_domains(
        "https://unlisted.example/page",
        blocked_domains=["blocked.example"],
    )


def test_provider_domain_policy_blocks_redirect_before_following(monkeypatch):
    """A locally followed redirect is checked before its next network request."""

    class RedirectResponse:
        def __init__(self):
            self.status_code = 302
            self.headers = {"Location": "https://outside.example/target"}
            self.is_redirect = True
            self.closed = False

        def close(self):
            self.closed = True

    response = RedirectResponse()
    requested_urls: list[str] = []
    provider = _provider(
        "scrape",
        "aiohttp",
        allowed_domains=["allowed.example"],
        blocked_domains=[],
    )
    validator = websearch_utils._build_provider_domain_url_validator(provider)
    assert validator is not None

    monkeypatch.setattr(
        websearch_utils,
        "_assert_websearch_url_allowed",
        lambda *_args, **_kwargs: None,
    )

    def fake_request(_method, url, **_kwargs):
        requested_urls.append(url)
        return response

    monkeypatch.setattr(websearch_utils, "public_web_request", fake_request)

    with pytest.raises(HTTPException) as exc_info:
        websearch_utils._requests_request_with_policy_redirects(
            None,
            "GET",
            "https://allowed.example/start",
            feature="test",
            target_url_validator=validator,
        )

    assert exc_info.value.detail["code"] == "websearch_domain_policy_blocked"
    assert requested_urls == ["https://allowed.example/start"]
    assert response.closed is True


def test_aiohttp_dispatch_uses_target_policy_for_redirects(monkeypatch):
    """The local scraper receives target policy instead of endpoint policy."""

    captured_validator = None

    def fake_aiohttp_scrape_urls(
        urls,
        *,
        verify_ssl,
        view_raw,
        url_validator,
        resolved_ip_validator,
    ):
        nonlocal captured_validator
        captured_validator = url_validator
        return [{"url": urls[0], "content": "ok"}]

    monkeypatch.setattr(
        scrape_utils,
        "aiohttp_scrape_urls",
        fake_aiohttp_scrape_urls,
    )
    endpoint_validator = lambda _url: None
    target_validator = lambda _url: None
    provider = _provider("scrape", "aiohttp", verify_ssl_certificate=True)

    scrape_utils.scrape(
        ["https://allowed.example/page"],
        "US",
        "en",
        provider,
        url_validator=endpoint_validator,
        target_url_validator=target_validator,
    )

    assert captured_validator is target_validator


def test_scrape_dispatcher_filters_targets_and_returned_sources(monkeypatch):
    """The shared dispatcher enforces policy even outside the main workflow."""

    handler_urls: list[str] = []

    def fake_handler(
        urls,
        _country,
        _language,
        _settings,
        _view_raw,
        _url_validator,
        _resolved_ip_validator,
    ):
        handler_urls.extend(urls)
        return {
            "result": [
                {"url": "https://outside.example/redirect", "content": "blocked"},
                {"url": "https://allowed.example/page", "content": "allowed"},
            ],
            "metadata": {"provider": "test"},
        }

    monkeypatch.setitem(scrape_utils.SCRAPE_HANDLERS, "aiohttp", fake_handler)
    provider = _provider(
        "scrape",
        "aiohttp",
        allowed_domains=["allowed.example"],
        blocked_domains=[],
    )

    response = scrape_utils.scrape(
        [
            "https://allowed.example/page",
            "https://outside.example/page",
        ],
        "US",
        "en",
        provider,
    )

    assert handler_urls == ["https://allowed.example/page"]
    assert response["metadata"] == {"provider": "test"}
    assert response["result"] == [
        {"url": "https://allowed.example/page", "content": "allowed"}
    ]


def test_scrape_dispatcher_applies_ollama_policy_to_submitted_and_returned_urls(monkeypatch):
    """Ollama uses the same best-effort URL filtering as remote scrapers."""

    provider_called = False
    handler_urls: list[str] = []

    def fake_handler(urls, *_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        handler_urls.extend(urls)
        return {
            "result": [
                {
                    "url": "https://allowed.example/start",
                    "content": "allowed",
                },
                {"url": "https://outside.example/page", "content": "blocked"},
            ]
        }

    monkeypatch.setitem(scrape_utils.SCRAPE_HANDLERS, "ollama", fake_handler)
    provider = _provider(
        "scrape",
        "ollama",
        allowed_domains=["allowed.example"],
        blocked_domains=[],
    )

    response = scrape_utils.scrape(
        [
            "https://allowed.example/start",
            "https://outside.example/page",
        ],
        "US",
        "en",
        provider,
    )

    assert provider_called is True
    assert handler_urls == ["https://allowed.example/start"]
    assert response["result"] == [
        {"url": "https://allowed.example/start", "content": "allowed"}
    ]


def test_scrape_dispatcher_allows_ollama_without_domain_policy(monkeypatch):
    """Unfiltered Ollama Web Fetch remains available for compatible deployments."""

    provider_called = False

    def fake_handler(urls, *_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        return {"result": [{"url": urls[0], "content": "allowed"}]}

    monkeypatch.setitem(scrape_utils.SCRAPE_HANDLERS, "ollama", fake_handler)
    provider = _provider(
        "scrape",
        "ollama",
        allowed_domains=[],
        blocked_domains=[],
    )

    response = scrape_utils.scrape(
        ["https://outside.example/page"],
        "US",
        "en",
        provider,
    )

    assert provider_called is True
    assert response["result"] == [
        {"url": "https://outside.example/page", "content": "allowed"}
    ]


def test_active_policy_drops_provider_entries_without_a_verifiable_source_url():
    """Returned content cannot bypass the boundary by omitting its source URL."""

    provider = _provider(
        "scrape",
        "firecrawl",
        allowed_domains=["allowed.example"],
        blocked_domains=[],
    )

    kept, metadata = filter_websearch_result_entries_by_domains(
        [
            {"content": "missing source"},
            {"url": "https://allowed.example/page", "content": "allowed"},
        ],
        provider,
    )

    assert kept == [{"url": "https://allowed.example/page", "content": "allowed"}]
    assert metadata["filtered_count"] == 1


def test_exa_combined_forwards_generic_domain_policy_upstream(monkeypatch):
    """Exa receives the same rules that Omlorix verifies on returned sources."""

    provider = _provider(
        "combined",
        "exa",
        api_key="test-key",
        allowed_domains=["Allowed.Example"],
        blocked_domains=["blocked.allowed.example"],
    )
    captured: dict = {}

    def fake_exa_search(*_args, **kwargs):
        captured.update(kwargs)
        return {"result": []}

    monkeypatch.setattr(
        combined_utils,
        "exa_web_search_combined",
        fake_exa_search,
    )

    combined_utils.run_combined_provider(provider, "test", country="US")

    assert captured["include_domains"] == ["allowed.example"]
    assert captured["exclude_domains"] == ["blocked.allowed.example"]


def test_combined_dispatcher_filters_ollama_results_after_client_call(monkeypatch):
    """The combined dispatcher applies Ollama rules to reported result URLs."""

    provider = _provider(
        "combined",
        "ollama",
        api_key="test-key",
        allowed_domains=["allowed.example"],
    )
    provider_called = False

    def fake_ollama_search(*_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        return {
            "result": [
                {"url": "https://allowed.example/page", "content": "allowed"},
                {"url": "https://outside.example/page", "content": "blocked"},
            ]
        }

    monkeypatch.setattr(
        combined_utils,
        "ollama_web_search_combined",
        fake_ollama_search,
    )

    response = combined_utils.run_combined_provider(provider, "test", country="US")

    assert provider_called is True
    assert response["result"] == [
        {"url": "https://allowed.example/page", "content": "allowed"}
    ]


def test_perplexity_uses_normalized_canonical_domain_rules(monkeypatch):
    """Perplexity stores and dispatches the same canonical fields as its peers."""

    settings = WebSearchProviderSettingsPerplexity.model_validate(
        {
            "api_key": "test-key",
            "allowed_domains": [
                " Allowed.Example ",
                "Docs.Allowed.Example",
            ],
        }
    )
    assert settings.allowed_domains == [
        "allowed.example",
        "docs.allowed.example",
    ]
    assert settings.blocked_domains == []
    assert "search_domain_filter" not in settings.model_dump()

    denylist_settings = WebSearchProviderSettingsPerplexity.model_validate(
        {
            "api_key": "test-key",
            "blocked_domains": [" Blocked.Example ", "Ads.Example"],
        }
    )
    assert denylist_settings.blocked_domains == [
        "blocked.example",
        "ads.example",
    ]

    provider = _provider("combined", "perplexity", **settings.model_dump())
    captured: dict = {}

    def fake_perplexity_search(*_args, **kwargs):
        captured.update(kwargs)
        return {"result": []}

    monkeypatch.setattr(
        combined_utils,
        "perplexity_combined_search",
        fake_perplexity_search,
    )

    combined_utils.run_combined_provider(provider, "test", country="US")

    assert captured["allowed_domains"] == [
        "allowed.example",
        "docs.allowed.example",
    ]
    assert captured["blocked_domains"] is None
    assert "search_domain_filter" not in captured


def test_perplexity_adapter_translates_canonical_blocklist_to_signed_payload(
    monkeypatch,
):
    """Only the outbound adapter exposes Perplexity's signed request format."""

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    def fake_post(_url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(perplexity_adapter.requests, "post", fake_post)

    perplexity_adapter.perplexity_combined_search(
        "test-key",
        "test",
        blocked_domains=["Blocked.Example", "ads.example"],
    )

    assert captured["json"]["search_domain_filter"] == [
        "-blocked.example",
        "-ads.example",
    ]
    assert "allowed_domains" not in captured["json"]
    assert "blocked_domains" not in captured["json"]


def test_perplexity_rejects_mixed_domain_modes_before_provider_request(monkeypatch):
    """Perplexity cannot persist or send both canonical policy modes at once."""

    with pytest.raises(ValidationError):
        WebSearchProviderSettingsPerplexity.model_validate(
            {
                "api_key": "test-key",
                "allowed_domains": ["allowed.example"],
                "blocked_domains": ["blocked.example"],
            }
        )

    provider = _provider(
        "combined",
        "perplexity",
        api_key="test-key",
        allowed_domains=["allowed.example"],
        blocked_domains=["blocked.example"],
    )
    provider_called = False

    def fake_post(*_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        raise AssertionError("Perplexity must reject the policy before HTTP I/O")

    monkeypatch.setattr(
        perplexity_adapter.requests,
        "post",
        fake_post,
    )

    with pytest.raises(HTTPException) as exc_info:
        combined_utils.run_combined_provider(provider, "test", country="US")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "websearch_domain_policy_mixed_modes"
    assert provider_called is False


def test_provider_schemas_expose_one_effective_domain_policy_surface():
    """Every policy-bearing provider exposes the canonical allow/block fields."""

    generic_policy_providers = {
        "aiohttp",
        "crawl4ai",
        "custom",
        "exa",
        "firecrawl",
        "ollama",
        "perplexity",
        "tavily",
        "you",
    }
    for provider_key in generic_policy_providers:
        field_keys = {
            field.key
            for section in WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS[provider_key].sections
            for field in section.fields
        }
        assert {"allowed_domains", "blocked_domains"} <= field_keys

    assert all(
        field.key != "search_domain_filter"
        for section in WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS["perplexity"].sections
        for field in section.fields
    )


def test_perplexity_rule_limit_fails_before_provider_request(monkeypatch):
    """A policy rule must never disappear through provider-side truncation."""

    rules = [f"domain-{index}.example" for index in range(21)]
    with pytest.raises(ValidationError):
        WebSearchProviderSettingsPerplexity.model_validate(
            {"api_key": "test-key", "allowed_domains": rules}
        )

    provider = _provider(
        "combined",
        "perplexity",
        api_key="test-key",
        allowed_domains=rules,
        blocked_domains=[],
    )
    provider_called = False

    def fake_post(*_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        raise AssertionError("Perplexity must reject the policy before HTTP I/O")

    monkeypatch.setattr(
        perplexity_adapter.requests,
        "post",
        fake_post,
    )

    with pytest.raises(HTTPException) as exc_info:
        combined_utils.run_combined_provider(provider, "test", country="US")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "websearch_domain_policy_too_many_rules"
    assert provider_called is False
