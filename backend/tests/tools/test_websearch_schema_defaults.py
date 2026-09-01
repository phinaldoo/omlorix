import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.websearch.schemas import WEBSEARCH_PROVIDER_REGISTRY, WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS
from app.tools.websearch.schemas import WebSearchProviderSettingsPerplexity
from app.tools.websearch.schemas import WebSearchProviderSettingsSerper
from app.tools.websearch.schemas import normalize_websearch_provider_settings
from app.tools.websearch.provider_url_suggestions import (
    PROVIDER_URL_SUGGESTIONS_METADATA_KEY,
    attach_provider_url_suggestions,
)
from app.tools.websearch.combined.utils import run_combined_provider
from app.tools.websearch.search.ddgs_search import get_locale_code
from app.tools.websearch.search.serper_search import serper_search_urls
from app.tools.websearch.utils import _resolve_provider_request_locale
from app.llm.model_schemas import get_model_schema_tools_section


def _get_field(provider: str, field_key: str):
    schema = WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS[provider]
    for section in schema.sections:
        for field in section.fields:
            if field.key == field_key:
                return field
    raise AssertionError(f"Field {field_key!r} not found for provider {provider!r}")


def test_locale_select_fields_default_to_english_and_us():
    language_field = _get_field("duckduckgo", "fallback_language")
    country_field = _get_field("duckduckgo", "fallback_country")

    assert language_field.default == "en"
    assert country_field.default == "US"


def test_exa_schema_exposes_current_types_and_locale_controls():
    """The generated Exa form omits neural and exposes country forwarding."""

    max_search_results_field = _get_field("exa", "max_search_results")
    type_field = _get_field("exa", "type")
    fallback_country_field = _get_field("exa", "fallback_country")
    forward_user_locale_field = _get_field("exa", "forward_user_locale")
    settings_model = WEBSEARCH_PROVIDER_REGISTRY["exa"].settings_model
    legacy_settings = settings_model.model_validate(
        {
            "api_key": "test-key",
            "type": "neural",
        }
    )

    assert max_search_results_field.default == 5
    assert [option.value for option in type_field.options] == [
        "auto",
        "fast",
        "instant",
    ]
    assert type_field.default == "auto"
    assert fallback_country_field.default == "US"
    assert forward_user_locale_field.default is False
    assert legacy_settings.type == "auto"


@pytest.mark.parametrize(
    ("request_country", "fallback_country", "expected_location"),
    [
        ("de", "US", "DE"),
        (None, "ca", "CA"),
    ],
)
def test_exa_combined_search_resolves_user_or_fallback_country(
    request_country,
    fallback_country,
    expected_location,
):
    """The combined dispatcher prefers an opted-in user country over fallback."""

    provider = SimpleNamespace(
        provider="exa",
        settings={
            "api_key": "test-key",
            "fallback_country": fallback_country,
            "type": "fast",
        },
    )

    with patch(
        "app.tools.websearch.combined.utils.exa_web_search_combined",
        return_value={"result": []},
    ) as mock_search:
        run_combined_provider(provider, "test query", country=request_country)

    assert mock_search.call_args.kwargs["user_location"] == expected_location


def test_aiohttp_verify_ssl_defaults_to_enabled_in_schema():
    verify_ssl_field = _get_field("aiohttp", "verify_ssl_certificate")

    assert verify_ssl_field.default is True


@pytest.mark.parametrize(
    "provider",
    ["aiohttp", "exa", "firecrawl", "tavily", "crawl4ai", "perplexity", "ollama", "custom"],
)
def test_robots_txt_respect_defaults_to_enabled(provider):
    field = _get_field(provider, "respect_robots_txt")
    settings_model = WEBSEARCH_PROVIDER_REGISTRY[provider].settings_model
    settings = settings_model.model_validate(
        {
            "api_key": "test-key",
            "base_url": "https://search.example",
        }
    )

    assert field.default is True
    assert field.value is True
    assert settings.respect_robots_txt is True


def test_google_pse_provider_is_removed_from_the_registry_and_schema_catalog():
    assert "google_pse" not in WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS
    assert "google_pse" not in WEBSEARCH_PROVIDER_REGISTRY


def test_serper_schema_does_not_expose_location_hint():
    serper_schema = WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS["serper"]
    location_fields = [
        field
        for section in serper_schema.sections
        for field in section.fields
        if field.key == "location"
    ]

    assert not location_fields


def test_locale_forwarding_defaults_to_disabled_for_serper():
    settings = WebSearchProviderSettingsSerper.model_validate(
        {
            "api_key": "test-key",
        }
    )
    field = _get_field("serper", "forward_user_locale")

    assert settings.forward_user_locale is False
    assert field.type == "boolean"
    assert field.default is False


def test_provider_request_locale_does_not_read_user_settings_by_default():
    provider = SimpleNamespace(settings={})

    with patch("app.tools.websearch.utils.get_user_setting_value") as mock_get_setting:
        country, language = _resolve_provider_request_locale("user-1", object(), provider)

    assert country is None
    assert language is None
    mock_get_setting.assert_not_called()


def test_provider_request_locale_reads_user_settings_when_opted_in():
    provider = SimpleNamespace(settings={"forward_user_locale": True})

    with patch("app.tools.websearch.utils.get_user_setting_value", side_effect=["DE", "de"]) as mock_get_setting:
        country, language = _resolve_provider_request_locale("user-1", object(), provider)

    assert country == "DE"
    assert language == "de"
    assert mock_get_setting.call_count == 2


def test_serper_search_uses_provider_fallback_locale_without_request_locale():
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"organic": []}

    with patch("app.tools.websearch.search.serper_search.requests.post", return_value=_Response()) as mock_post:
        serper_search_urls(
            "test-key",
            "test query",
            language=None,
            country=None,
            fallback_language="fr",
            fallback_country="CA",
        )

    assert mock_post.call_args.kwargs["json"]["hl"] == "fr"
    assert mock_post.call_args.kwargs["json"]["gl"] == "ca"


def test_duckduckgo_locale_code_allows_missing_request_locale():
    assert get_locale_code(None, None) == "wt-wt"


def test_websearch_schema_fields_expose_i18n_metadata():
    for provider, schema in WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS.items():
        for section in schema.sections:
            for field in section.fields:
                assert field.i18n_label, f"{provider}.{field.key} is missing i18n_label"
                assert field.i18n_description, f"{provider}.{field.key} is missing i18n_description"


def test_locale_select_options_are_marked_for_runtime_localization():
    language_field = _get_field("duckduckgo", "fallback_language")
    country_field = _get_field("duckduckgo", "fallback_country")

    assert language_field.options
    assert country_field.options
    assert all(option.metadata and option.metadata.get("i18n_display_type") == "language" for option in language_field.options)
    assert all(option.metadata and option.metadata.get("i18n_display_type") == "region" for option in country_field.options)


def test_crawl4ai_base_url_field_exposes_provider_url_suggestions():
    schema = WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS["crawl4ai"].model_copy(deep=True)
    enriched = attach_provider_url_suggestions(schema, "crawl4ai")
    base_url_field = next(
        field
        for section in enriched.sections
        for field in section.fields
        if field.key == "base_url"
    )

    assert base_url_field.metadata
    assert base_url_field.metadata[PROVIDER_URL_SUGGESTIONS_METADATA_KEY] == [
        {"name": "Local Crawl4AI", "url": "http://localhost:11235"},
        {"name": "Local Crawl4AI (Docker Host)", "url": "http://host.docker.internal:11235"},
    ]


def test_crawl4ai_exposes_an_optional_password_api_token_field():
    """Crawl4AI auth is supported without making proxy-managed tokens mandatory."""

    token_field = _get_field("crawl4ai", "api_key")
    settings_model = WEBSEARCH_PROVIDER_REGISTRY["crawl4ai"].settings_model

    assert token_field.input_type == "password"
    assert token_field.required is False
    assert token_field.i18n_label == "websearch_schema_field_crawl4ai_api_token"
    assert token_field.i18n_description == "websearch_schema_field_crawl4ai_api_token_desc"
    assert settings_model().api_key == ""

    stored_settings = settings_model.model_validate({"api_key": "crawl4ai-secret"})
    normalized_settings = normalize_websearch_provider_settings(
        "crawl4ai",
        stored_settings.model_dump(),
    )
    assert stored_settings.api_key == "crawl4ai-secret"
    assert "api_key" not in normalized_settings
    assert "crawl4ai-secret" not in str(normalized_settings)


def test_searxng_base_url_field_exposes_provider_url_suggestions():
    schema = WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS["searxng"].model_copy(deep=True)
    enriched = attach_provider_url_suggestions(schema, "searxng")
    base_url_field = next(
        field
        for section in enriched.sections
        for field in section.fields
        if field.key == "base_url"
    )

    assert base_url_field.metadata
    assert base_url_field.metadata[PROVIDER_URL_SUGGESTIONS_METADATA_KEY] == [
        {"name": "Local SearXNG", "url": "http://localhost:8080"},
        {"name": "Local SearXNG (Docker Host)", "url": "http://host.docker.internal:8080"},
    ]


def test_perplexity_is_not_marked_as_a_direct_scrape_provider():
    definition = WEBSEARCH_PROVIDER_REGISTRY["perplexity"]

    assert "combined" in definition.capabilities
    assert "scrape" not in definition.capabilities


def test_perplexity_fallback_country_uses_a_select_and_defaults_to_us():
    country_field = _get_field("perplexity", "fallback_country")

    assert country_field.type == "select"
    assert country_field.default == "US"
    assert country_field.options
    assert all(option.metadata and option.metadata.get("i18n_display_type") == "region" for option in country_field.options)


def test_perplexity_fallback_language_uses_a_select_and_defaults_to_en():
    language_field = _get_field("perplexity", "fallback_language")

    assert language_field.type == "select"
    assert language_field.default == "en"
    assert language_field.options
    assert all(option.metadata and option.metadata.get("i18n_display_type") == "language" for option in language_field.options)


def test_perplexity_settings_accept_legacy_default_country_input():
    settings = WebSearchProviderSettingsPerplexity.model_validate(
        {
            "api_key": "test-key",
            "default_country": "DE",
        }
    )

    assert settings.fallback_country == "DE"


def test_perplexity_settings_accept_legacy_search_language_filter_input():
    settings = WebSearchProviderSettingsPerplexity.model_validate(
        {
            "api_key": "test-key",
            "search_language_filter": ["de", "fr"],
        }
    )

    assert settings.fallback_language == "de"


def test_perplexity_combined_search_uses_fallback_country_when_request_country_is_missing():
    provider = type(
        "Provider",
        (),
        {
            "provider": "perplexity",
            "settings": {
                "api_key": "test-key",
                "default_country": "DE",
            },
        },
    )()

    with patch("app.tools.websearch.combined.utils.perplexity_combined_search", return_value={"result": []}) as mock_search:
        run_combined_provider(provider, "test query", country="")

    assert mock_search.call_args.kwargs["country"] == "DE"
    assert mock_search.call_args.kwargs["search_language_filter"] == ["en"]


def test_perplexity_combined_search_uses_fallback_language_select():
    provider = type(
        "Provider",
        (),
        {
            "provider": "perplexity",
            "settings": {
                "api_key": "test-key",
                "fallback_language": "fr",
            },
        },
    )()

    with patch("app.tools.websearch.combined.utils.perplexity_combined_search", return_value={"result": []}) as mock_search:
        run_combined_provider(provider, "test query", country="US")

    assert mock_search.call_args.kwargs["search_language_filter"] == ["fr"]


def test_perplexity_combined_search_uses_legacy_language_filter_list():
    provider = type(
        "Provider",
        (),
        {
            "provider": "perplexity",
            "settings": {
                "api_key": "test-key",
                "search_language_filter": ["de", "fr"],
            },
        },
    )()

    with patch("app.tools.websearch.combined.utils.perplexity_combined_search", return_value={"result": []}) as mock_search:
        run_combined_provider(provider, "test query", country="US")

    assert mock_search.call_args.kwargs["search_language_filter"] == ["de"]


def test_perplexity_settings_normalization_maps_legacy_country_field():
    normalized = normalize_websearch_provider_settings(
        "perplexity",
        {
            "api_key": "test-key",
            "default_country": "DE",
        },
    )

    assert normalized["fallback_country"] == "DE"
    assert "default_country" not in normalized
    assert "api_key" not in normalized


def test_aiohttp_settings_normalization_defaults_verify_ssl_to_enabled():
    normalized = normalize_websearch_provider_settings(
        "aiohttp",
        {},
    )

    assert normalized["verify_ssl_certificate"] is True


def test_perplexity_settings_normalization_maps_legacy_language_filter_field():
    normalized = normalize_websearch_provider_settings(
        "perplexity",
        {
            "api_key": "test-key",
            "search_language_filter": ["de", "fr"],
        },
    )

    assert normalized["fallback_language"] == "de"
    assert "search_language_filter" not in normalized


def test_model_tools_schema_keeps_perplexity_out_of_scrape_options():
    provider_rows = [
        {
            "id": "provider-exa",
            "name": "Exa",
            "provider": "exa",
            "types": ["scrape", "combined"],
            "has_combined": True,
            "has_scrape": True,
            "has_search": False,
        },
        {
            "id": "provider-ollama",
            "name": "Ollama",
            "provider": "ollama",
            "types": ["scrape", "combined"],
            "has_combined": True,
            "has_scrape": True,
            "has_search": False,
        },
        {
            "id": "provider-perplexity",
            "name": "Perplexity",
            "provider": "perplexity",
            "types": ["combined"],
            "has_combined": True,
            "has_scrape": False,
            "has_search": False,
        },
    ]

    with patch("app.tools.utils.list_available_tool_options", return_value=[]), patch(
        "app.mcp.models.list_mcp_servers", return_value=[]
    ), patch(
        "app.tools.websearch.models.list_websearch_providers_with_types", return_value=provider_rows
    ):
        schema = get_model_schema_tools_section(db=None)

    fields = {
        field.key: field
        for section in schema.sections
        for field in section.fields
    }
    search_options = {option.value: option for option in fields["settings.websearch_search_provider"].options}
    scrape_options = {option.value: option for option in fields["settings.websearch_scrape_provider"].options}

    assert "provider-perplexity" in search_options
    assert search_options["provider-perplexity"].metadata["has_combined"] is True
    assert search_options["provider-perplexity"].metadata["has_scrape"] is False
    assert "provider-perplexity" not in scrape_options


def test_custom_provider_is_marked_for_search_and_scrape():
    definition = WEBSEARCH_PROVIDER_REGISTRY["custom"]

    assert "search" in definition.capabilities
    assert "scrape" in definition.capabilities


def test_custom_schema_exposes_scrape_fields():
    scrape_base_url_field = _get_field("custom", "scrape_base_url")
    robots_field = _get_field("custom", "respect_robots_txt")
    allowed_domains_field = _get_field("custom", "allowed_domains")
    blocked_domains_field = _get_field("custom", "blocked_domains")

    assert scrape_base_url_field.required is False
    assert robots_field.type == "boolean"
    assert allowed_domains_field.type == "string_list"
    assert blocked_domains_field.type == "string_list"


def test_scrape_domain_filter_descriptions_disclose_remote_provider_limits():
    """The UI must not present remote-provider filters as a strict egress boundary."""

    allowed_domains_field = _get_field("ollama", "allowed_domains")
    blocked_domains_field = _get_field("ollama", "blocked_domains")

    for field in (allowed_domains_field, blocked_domains_field):
        assert "provider-reported URLs afterward" in field.description
        assert "hidden requests or redirects" in field.description
        assert "egress controls for strict enforcement" in field.description
        assert "request is rejected" not in field.description
