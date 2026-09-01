from dataclasses import dataclass
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator, field_validator, create_model
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple, Type
from enum import Enum
import json
from pathlib import Path

from app.utils.schemas import FieldSchema, FieldAttributes, Section, Sections, Option
from app.tools.websearch.combined.perplexity_combined import (
    build_perplexity_search_domain_filter,
)
from app.tools.websearch.domain_filters import normalize_domain_list
from app.tools.websearch.firecrawl_proxy import (
    FIRECRAWL_HOSTED_BASE_URL,
    FirecrawlProxyMode,
    normalize_firecrawl_base_url,
    normalize_firecrawl_proxy_mode,
)
from app.tools.websearch.search.ddgs_search import SUPPORTED_DDG_LOCALES

# Load ISO data from JSON files
_CURRENT_DIR = Path(__file__).parent
with open(_CURRENT_DIR / "iso_3166_1_countries.json", "r") as f:
    ISO_COUNTRIES: Dict[str, str] = json.load(f)

with open(_CURRENT_DIR / "iso_639_1_languages.json", "r") as f:
    ISO_LANGUAGES: Dict[str, str] = json.load(f)

# Build reverse mapping for flexible country lookup (name -> code)
_COUNTRY_NAME_TO_CODE: Dict[str, str] = {
    name.lower(): code for code, name in ISO_COUNTRIES.items()
}


def _normalize_iso_country_code(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if len(trimmed) != 2 or not trimmed.isalpha():
        return None
    normalized = trimmed.upper()
    if normalized not in ISO_COUNTRIES:
        return None
    return normalized


def _build_country_options_from_codes(codes: Iterable[str]) -> List[Option]:
    normalized_codes = sorted(
        {code for code in ( _normalize_iso_country_code(code) for code in codes ) if code}
    )
    options: List[Option] = []
    for code in normalized_codes:
        label = ISO_COUNTRIES.get(code, code)
        options.append(
            Option(
                value=code,
                label=label,
                metadata={"i18n_display_type": "region"},
            )
        )
    return options


COUNTRY_SELECT_OPTIONS: List[Option] = _build_country_options_from_codes(
    ISO_COUNTRIES.keys()
)


def _normalize_iso_language_code(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if len(trimmed) != 2 or not trimmed.isalpha():
        return None
    return trimmed.lower()


_LANGUAGE_LABELS: Dict[str, str] = {}


def _language_label(code: str) -> str:
    normalized = _normalize_iso_language_code(code)
    if not normalized:
        return ""
    cached = _LANGUAGE_LABELS.get(normalized)
    if cached:
        return cached
    label = ISO_LANGUAGES.get(normalized)
    if not label:
        label = normalized.upper()
    _LANGUAGE_LABELS[normalized] = label
    return label


def _build_language_options_from_codes(codes: Iterable[str] | None = None) -> List[Option]:
    if codes is None:
        codes_iter = ISO_LANGUAGES.keys()
    else:
        codes_iter = (_normalize_iso_language_code(code) for code in codes)
    normalized_codes = sorted({code for code in codes_iter if code})
    return [
        Option(
            value=code,
            label=_language_label(code),
            metadata={"i18n_display_type": "language"},
        )
        for code in normalized_codes
    ]


LANGUAGE_SELECT_OPTIONS: List[Option] = _build_language_options_from_codes()


def _language_select_field(
    key: str,
    label: str,
    description: str,
    *,
    required: bool | None = None,
    allowed_codes: Iterable[str] | None = None,
    i18n_label: str | None = None,
    i18n_description: str | None = None,
) -> FieldSchema:
    options = LANGUAGE_SELECT_OPTIONS if not allowed_codes else _build_language_options_from_codes(allowed_codes)
    return FieldSchema(
        key=key,
        label=label,
        description=description,
        i18n_label=i18n_label or "websearch_schema_field_fallback_language",
        i18n_description=i18n_description or "websearch_schema_field_fallback_language_desc",
        type="select",
        multiple=False,
        options=options,
        required=required,
        default="en",
    )


def _forward_user_locale_field() -> FieldSchema:
    return FieldSchema(
        key="forward_user_locale",
        label="Forward User Locale",
        description=(
            "Send the user's profile language and country to this provider for each request. "
            "Leave disabled to use only the provider fallback locale."
        ),
        i18n_label="websearch_schema_field_forward_user_locale",
        i18n_description="websearch_schema_field_forward_user_locale_desc",
        type="boolean",
        default=False,
    )


def _scrape_domain_filter_fields() -> list[FieldSchema]:
    return [
        FieldSchema(
            key="allowed_domains",
            label="Allowed Domains",
            description=(
                "Optional hostname allowlist applied to submitted and discovered target URLs "
                "before Omlorix-controlled fetching, and to provider-reported URLs afterward. "
                "Remote providers may perform hidden requests or redirects outside this list; "
                "use egress controls for strict enforcement. Matching includes subdomains."
            ),
            i18n_label="websearch_schema_field_allowed_domains",
            i18n_description="websearch_schema_field_allowed_domains_desc",
            type="string_list",
            required=False,
        ),
        FieldSchema(
            key="blocked_domains",
            label="Blocked Domains",
            description=(
                "Optional hostname blocklist applied to submitted and discovered target URLs "
                "before Omlorix-controlled fetching, and to provider-reported URLs afterward. "
                "Remote providers may perform hidden requests or redirects outside this list; "
                "use egress controls for strict enforcement. Matching includes subdomains, and "
                "blocking overrides allowed domains."
            ),
            i18n_label="websearch_schema_field_blocked_domains",
            i18n_description="websearch_schema_field_blocked_domains_desc",
            type="string_list",
            required=False,
        ),
    ]


def _perplexity_domain_filter_fields() -> list[FieldSchema]:
    """Expose canonical fields while documenting Perplexity's API limits."""

    fields = _scrape_domain_filter_fields()
    description = (
        "Optional Perplexity domain policy enforced before retrieval and rechecked "
        "locally afterward. Configure either Allowed Domains or Blocked Domains, "
        "not both, with at most 20 entries."
    )
    for field in fields:
        field.description = description
        field.i18n_description = (
            "websearch_schema_field_perplexity_domain_policy_desc"
        )
    return fields


class _ScrapeDomainFilterSettingsMixin(BaseModel):
    allowed_domains: list[str] = []
    blocked_domains: list[str] = []

    @field_validator("allowed_domains", "blocked_domains", mode="before")
    @classmethod
    def _normalize_domains(cls, value: Any) -> list[str]:
        return normalize_domain_list(value)


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_int_validator(default: int):
    def _validator(value: Any) -> int:
        return _coerce_positive_int(value, default)

    return _validator


TAVILY_SUPPORTED_COUNTRY_CODES: set[str] = set(ISO_COUNTRIES.keys())

DUCKDUCKGO_SUPPORTED_COUNTRY_CODES: set[str] = {
    code for code in (
        _normalize_iso_country_code(locale.split("-", 1)[0]) for locale in SUPPORTED_DDG_LOCALES
    ) if code
}

DUCKDUCKGO_SUPPORTED_LANGUAGE_CODES: set[str] = {
    code for code in (
        _normalize_iso_language_code(locale.split("-", 1)[1]) for locale in SUPPORTED_DDG_LOCALES if "-" in locale
    ) if code
}


def _country_select_field(
    key: str,
    label: str,
    description: str,
    required: bool | None = None,
    allowed_codes: Iterable[str] | None = None,
    i18n_label: str | None = None,
    i18n_description: str | None = None,
) -> FieldSchema:
    options = COUNTRY_SELECT_OPTIONS if not allowed_codes else _build_country_options_from_codes(allowed_codes)
    return FieldSchema(
        key=key,
        label=label,
        description=description,
        i18n_label=i18n_label or "websearch_schema_field_fallback_country",
        i18n_description=i18n_description or "websearch_schema_field_fallback_country_desc",
        type="select",
        multiple=False,
        options=options,
        required=required,
        default="US",
    )


def _normalize_country_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for entry in value:
            normalized = _normalize_country_value(entry)
            if normalized:
                return normalized
        return None
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        # Try lookup by country name (flexible lookup)
        code = _COUNTRY_NAME_TO_CODE.get(trimmed.lower())
        if code:
            return code.upper()
        # Try direct alpha-2 code
        if len(trimmed) == 2 and trimmed.isalpha():
            normalized = trimmed.upper()
            if normalized in ISO_COUNTRIES:
                return normalized
        return None
    return None


def _country_field_validator(cls, value: Any):
    normalized = _normalize_country_value(value)
    if normalized is not None:
        return normalized
    return None


def _make_country_validator(allowed_codes: set[str] | None = None):
    def _validator(cls, value: Any):
        normalized = _normalize_country_value(value)
        if normalized is None:
            return None
        if allowed_codes and normalized.upper() not in allowed_codes:
            raise ValueError("Unsupported country for this provider.")
        return normalized

    return _validator



class WebSearchResult(BaseModel):
    link: str
    title: str
    preview: str



class WebSearchResults(BaseModel):
    results: List[WebSearchResult]


class WebSearchResponse(BaseModel):
    status: str
    results: WebSearchResults


class WebScrapeResults(BaseModel):
    url: str
    content: str | None = None
    title: str | None = None
    error: str | None = None

    model_config = ConfigDict(extra="ignore")



# -------------------
# AIoHTTP Settings
# -------------------
class WebSearchProviderSettingsAiohttp(_ScrapeDomainFilterSettingsMixin):
    verify_ssl_certificate: bool = True
    respect_robots_txt: bool = True
AIOHTTP_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="verify_ssl_certificate",
            label="Verify SSL Certificate",
            description="Enable SSL certificate validation for outgoing HTTP requests.",
            i18n_label="websearch_schema_field_verify_ssl_certificate",
            i18n_description="websearch_schema_field_verify_ssl_certificate_desc",
            type="boolean",
            default=True,
        ),
        FieldSchema(
            key="respect_robots_txt",
            label="Respect Robots.txt",
            description="Honor robots.txt directives when crawling target pages.",
            i18n_label="websearch_schema_field_respect_robots_txt",
            i18n_description="websearch_schema_field_respect_robots_txt_desc",
            type="boolean",
            default=True,
            value=True,
        ),
        *_scrape_domain_filter_fields(),
    ]
)


# -------------------
# Exa Settings
# -------------------  
class WebSearchProviderSettingsExa(_ScrapeDomainFilterSettingsMixin):
    """Validate persisted Exa search, locale, and direct-scrape settings."""

    api_key: str
    # Scrape
    respect_robots_txt: bool = True
    # Search
    fallback_country: str = "US"
    forward_user_locale: bool = False
    max_search_results: int = 5
    type: Literal["auto", "fast", "instant"] = "auto"

    _validate_fallback_country = field_validator("fallback_country", mode="before")(
        _country_field_validator
    )
    _validate_max_search_results = field_validator("max_search_results", mode="before")(
        _positive_int_validator(5)
    )

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_search_type(cls, value: Any) -> Any:
        """Map Exa's removed neural mode to auto and normalize current values."""

        if isinstance(value, str):
            candidate = value.strip().lower()
            if candidate == "neural":
                return "auto"
            return candidate
        return value


EXA_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="api_key",
            label="API Key",
            description="Exa API key used to authenticate search and scrape requests.",
            i18n_label="websearch_schema_field_api_key",
            i18n_description="websearch_schema_field_api_key_desc",
            type="string",
            placeholder="e.g. exa_sk_your_key",
        ),
        FieldSchema(
            key="respect_robots_txt",
            label="Respect Robots.txt",
            description="Honor robots.txt directives when Exa fetches page content.",
            i18n_label="websearch_schema_field_respect_robots_txt",
            i18n_description="websearch_schema_field_respect_robots_txt_desc",
            type="boolean",
            default=True,
            value=True,
        ),
        FieldSchema(
            key="max_search_results",
            label="Max Search Results",
            description="Maximum number of Exa search results returned per query.",
            i18n_label="websearch_schema_field_max_search_results",
            i18n_description="websearch_schema_field_max_search_results_desc",
            type="number",
            default=5,
            value=5,
            attributes=FieldAttributes(min=1),
        ),
        _country_select_field(
            key="fallback_country",
            label="Fallback Country",
            description=(
                "Two-letter country code sent to Exa when user locale forwarding is disabled."
            ),
        ),
        _forward_user_locale_field(),
        FieldSchema(
            key="type",
            label="Type",
            description="Preferred Exa search strategy.",
            i18n_label="websearch_schema_field_search_type",
            i18n_description="websearch_schema_field_search_type_desc",
            type="select",
            options=[
                Option(value="auto", label="Auto", i18n_label="llm.shared.option.auto"),
                Option(value="fast", label="Fast", i18n_label="llm.shared.option.fast"),
                Option(value="instant", label="Instant", i18n_label="llm.shared.option.instant"),
            ],
            default="auto",
        ),
        *_scrape_domain_filter_fields(),
    ]
)



# -------------------
# Firecrawl Settings
# -------------------  
class WebSearchProviderSettingsFirecrawl(_ScrapeDomainFilterSettingsMixin):
    api_key: str
    base_url: str = FIRECRAWL_HOSTED_BASE_URL
    fallback_country: str = "US"
    forward_user_locale: bool = False
    proxy: FirecrawlProxyMode = "auto"
    respect_robots_txt: bool = True
    max_search_results: int = 5
    enterprise_option: str | None = None

    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_firecrawl_base_url
    )
    _normalize_proxy = field_validator("proxy", mode="before")(normalize_firecrawl_proxy_mode)
    _validate_fallback_country = field_validator("fallback_country", mode="before")(
        _country_field_validator
    )
    _validate_max_search_results = field_validator("max_search_results", mode="before")(
        _positive_int_validator(5)
    )

    @field_validator("enterprise_option", mode="before")
    @classmethod
    def _normalize_enterprise_option(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            trimmed = value.strip().lower()
            if not trimmed or trimmed == "none":
                return None
            if trimmed in {"zdr", "anon"}:
                return trimmed
        return None

FIRECRAWL_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="api_key",
            label="API Key",
            description="Firecrawl API key used for authenticated requests.",
            i18n_label="websearch_schema_field_api_key",
            i18n_description="websearch_schema_field_api_key_desc",
            type="string",
            placeholder="e.g. frc_sk_your_key",
        ),
        FieldSchema(
            key="base_url",
            label="Optional Base URL",
            description="Override the Firecrawl API base URL when targeting a custom deployment.",
            i18n_label="websearch_schema_field_base_url",
            i18n_description="websearch_schema_field_base_url_desc",
            type="string",
            placeholder="E.g. https://api.firecrawl.dev",
            required=False,
            default=FIRECRAWL_HOSTED_BASE_URL,
            value=FIRECRAWL_HOSTED_BASE_URL,
        ),
        _country_select_field(
            key="fallback_country",
            label="Fallback Country",
            description="ISO country code to use when no request-specific country is supplied.",
        ),
        _forward_user_locale_field(),
        FieldSchema(
            key="proxy",
            label="Proxy Mode",
            description="Auto, Basic, and Enhanced are supported Firecrawl proxy modes.",
            i18n_label="websearch_schema_field_proxy_mode",
            i18n_description="websearch_schema_field_proxy_mode_desc",
            type="select",
            options=[
                Option(value="auto", label="Auto", i18n_label="websearch.shared.proxy.option.auto"),
                Option(value="basic", label="Basic", i18n_label="websearch.shared.proxy.option.basic"),
                Option(value="enhanced", label="Enhanced", i18n_label="websearch.shared.proxy.option.enhanced"),
            ],
            default="auto",
            value="auto",
        ),
        FieldSchema(
            key="respect_robots_txt",
            label="Respect Robots.txt",
            description="Honor robots.txt directives during Firecrawl operations.",
            i18n_label="websearch_schema_field_respect_robots_txt",
            i18n_description="websearch_schema_field_respect_robots_txt_desc",
            type="boolean",
            default=True,
            value=True,
        ),
        FieldSchema(
            key="max_search_results",
            label="Max Search Results",
            description="Maximum number of Firecrawl search results returned per query.",
            i18n_label="websearch_schema_field_max_search_results",
            i18n_description="websearch_schema_field_max_search_results_desc",
            type="number",
            default=5,
            value=5,
            attributes=FieldAttributes(min=1),
        ),
        FieldSchema(
            key="enterprise_option",
            label="Enterprise Option",
            description="Firecrawl enterprise option for Zero Data Retention (ZDR). This feature must be enabled for your account to use it.",
            i18n_label="websearch_schema_field_enterprise_option",
            i18n_description="websearch_schema_field_enterprise_option_desc",
            type="select",
            required=False,
            default="none",
            value="none",
            options=[
                Option(value="none", label="None", i18n_label="llm.shared.option.none"),
                Option(value="zdr", label="Zero Data Retention", i18n_label="llm.shared.option.zero_data_retention"),
                Option(value="anon", label="Anonymized ZDR", i18n_label="llm.shared.option.anonymized_zdr"),
            ],
        ),
        *_scrape_domain_filter_fields(),
    ]
)



# -------------------
# Tavily Settings
# -------------------  
class WebSearchProviderSettingsTavily(_ScrapeDomainFilterSettingsMixin):
    api_key: str
    respect_robots_txt: bool = True
    fallback_country: str = "US"
    forward_user_locale: bool = False

    _validate_fallback_country = field_validator("fallback_country", mode="before")(
        _make_country_validator(TAVILY_SUPPORTED_COUNTRY_CODES)
    )
TAVILY_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="api_key",
            label="API Key",
            description="Tavily API key used to authenticate search jobs.",
            i18n_label="websearch_schema_field_api_key",
            i18n_description="websearch_schema_field_api_key_desc",
            type="string",
            placeholder="e.g. tavily_sk_your_key",
        ),
        FieldSchema(
            key="respect_robots_txt",
            label="Respect Robots.txt",
            description="Honor robots.txt directives when Tavily scrapes pages.",
            i18n_label="websearch_schema_field_respect_robots_txt",
            i18n_description="websearch_schema_field_respect_robots_txt_desc",
            type="boolean",
            default=True,
            value=True,
        ),
        _country_select_field(
            key="fallback_country",
            label="Fallback Country",
            description="ISO country code applied to Tavily searches when none is provided.",
            allowed_codes=TAVILY_SUPPORTED_COUNTRY_CODES,
        ),
        _forward_user_locale_field(),
        *_scrape_domain_filter_fields(),
    ]
)



# -------------------
# Crawl4AI Settings
# -------------------  
class WebSearchProviderSettingsCrawl4AI(_ScrapeDomainFilterSettingsMixin):
    base_url: str = "http://localhost:11235"
    # Keep the credential under the shared ``api_key`` setting name so the
    # existing encrypted-at-rest, masked-edit, response-redaction, and export
    # controls apply without creating a second secret-storage path.
    api_key: str = ""
    retry_count: int = 3
    respect_robots_txt: bool = True
CRAWL4AI_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="base_url",
            label="Base URL",
            description="Root URL of the Crawl4AI service instance.",
            i18n_label="websearch_schema_field_base_url",
            i18n_description="websearch_schema_field_base_url_desc",
            type="string",
        ),
        FieldSchema(
            key="api_key",
            label="API Token",
            description=(
                "Bearer token configured as CRAWL4AI_API_TOKEN on Crawl4AI 0.9 "
                "or later. Leave blank only when a restricted proxy adds "
                "authentication or the server does not require it."
            ),
            i18n_label="websearch_schema_field_crawl4ai_api_token",
            i18n_description="websearch_schema_field_crawl4ai_api_token_desc",
            type="string",
            input_type="password",
            required=False,
        ),
        FieldSchema(
            key="retry_count",
            label="Retry Count",
            description="Number of retry attempts for failed Crawl4AI requests.",
            i18n_label="websearch_schema_field_retry_count",
            i18n_description="websearch_schema_field_retry_count_desc",
            type="number",
            default=5,
        ),
        FieldSchema(
            key="respect_robots_txt",
            label="Respect Robots.txt",
            description="Honor robots.txt directives when Crawl4AI fetches pages.",
            i18n_label="websearch_schema_field_respect_robots_txt",
            i18n_description="websearch_schema_field_respect_robots_txt_desc",
            type="boolean",
            default=True,
            value=True,
        ),
        *_scrape_domain_filter_fields(),
    ]
)



# -------------------
# DuckDuckGo Settings
# -------------------  
class WebSearchProviderSettingsDuckDuckGo(BaseModel):
    fallback_language: str = "en"
    fallback_country: str = "US"
    forward_user_locale: bool = False
    max_search_results: int = 5
    safesearch: str = "moderate"
    _validate_fallback_country = field_validator("fallback_country", mode="before")(
        _make_country_validator(DUCKDUCKGO_SUPPORTED_COUNTRY_CODES)
    )
    _validate_max_search_results = field_validator("max_search_results", mode="before")(
        _positive_int_validator(5)
    )

DUCKDUCKGO_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        _language_select_field(
            key="fallback_language",
            label="Fallback Language",
            description="Language code used when chat locale is unavailable.",
            allowed_codes=DUCKDUCKGO_SUPPORTED_LANGUAGE_CODES,
        ),
        _country_select_field(
            key="fallback_country",
            label="Fallback Country",
            description="Country code sent to DuckDuckGo when no country override is provided.",
            allowed_codes=DUCKDUCKGO_SUPPORTED_COUNTRY_CODES,
        ),
        _forward_user_locale_field(),
        FieldSchema(
            key="max_search_results",
            label="Max Search Results",
            description="Maximum number of DuckDuckGo results returned per query.",
            i18n_label="websearch_schema_field_max_search_results",
            i18n_description="websearch_schema_field_max_search_results_desc",
            type="number",
            default=5,
            value=5,
            attributes=FieldAttributes(min=1),
        ),
        FieldSchema(
            key="safesearch",
            label="Safesearch Level",
            description="Safesearch mode for DuckDuckGo results.",
            i18n_label="websearch_schema_field_safesearch_level",
            i18n_description="websearch_schema_field_safesearch_level_desc",
            type="select",
            default="moderate",
            value="moderate",
            options=[
                Option(value="on", label="On (Strict)", i18n_label="websearch.duckduckgo.safesearch.option.on"),
                Option(value="moderate", label="Moderate", i18n_label="websearch.duckduckgo.safesearch.option.moderate"),
                Option(value="off", label="Off", i18n_label="websearch.duckduckgo.safesearch.option.off"),
            ],
        ),
    ]
)



# -------------------
# Serper Settings
# -------------------  
class WebSearchProviderSettingsSerper(BaseModel):
    api_key: str
    fallback_language: str = "en"
    fallback_country: str = "US"
    forward_user_locale: bool = False
    num_results: int = 5

    _validate_fallback_country = field_validator("fallback_country", mode="before")(_country_field_validator)
    _validate_num_results = field_validator("num_results", mode="before")(
        _positive_int_validator(5)
    )


SERPER_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="api_key",
            label="API Key",
            description="Serper API key used for authenticated requests.",
            i18n_label="websearch_schema_field_api_key",
            i18n_description="websearch_schema_field_api_key_desc",
            type="string",
            placeholder="e.g. serper_api_key",
        ),
        _language_select_field(
            key="fallback_language",
            label="Fallback Language",
            description="ISO 639-1 language code applied when chat language is unavailable.",
        ),
        _country_select_field(
            key="fallback_country",
            label="Fallback Country",
            description="ISO 3166-1 alpha-2 country code used when no country is provided.",
        ),
        _forward_user_locale_field(),
        FieldSchema(
            key="num_results",
            label="Number of Results",
            description="Maximum number of Serper organic results returned per query (max 20).",
            i18n_label="websearch_schema_field_number_of_results",
            i18n_description="websearch_schema_field_number_of_results_desc",
            type="number",
            default=5,
            value=5,
            attributes=FieldAttributes(min=1, max=20),
        ),
    ]
)


# -------------------
# You.com Settings
# -------------------
class WebSearchProviderSettingsYou(_ScrapeDomainFilterSettingsMixin):
    api_key: str
    fallback_country: str = "US"
    forward_user_locale: bool = False
    count: int = 10

    _validate_fallback_country = field_validator("fallback_country", mode="before")(_country_field_validator)

    @field_validator("count")
    @classmethod
    def _validate_count(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("count must be greater than 0")
        return int(value)


YOU_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="api_key",
            label="API Key",
            description="You.com API key used for Search and Contents API requests.",
            i18n_label="websearch_schema_field_api_key",
            i18n_description="websearch_schema_field_api_key_desc",
            type="string",
            placeholder="e.g. ydc_your_key",
        ),
        _country_select_field(
            key="fallback_country",
            label="Fallback Country",
            description="ISO 3166-1 alpha-2 country code used when no user country is available.",
        ),
        _forward_user_locale_field(),
        FieldSchema(
            key="count",
            label="Result Count",
            description="Maximum number of You.com web and news results to request per query.",
            i18n_label="websearch_schema_field_result_count",
            i18n_description="websearch_schema_field_result_count_desc",
            type="number",
            default=5,
            value=5,
            attributes=FieldAttributes(min=1),
        ),
        *_scrape_domain_filter_fields(),
    ]
)


# -------------------
# Perplexity Settings
# -------------------  
class WebSearchProviderSettingsPerplexity(_ScrapeDomainFilterSettingsMixin):
    api_key: str
    respect_robots_txt: bool = True
    forward_user_locale: bool = False
    max_results: int = 5
    max_tokens_per_page: int = 2048
    max_tokens: int = 4096
    fallback_country: str = Field(
        default="US",
        validation_alias=AliasChoices("fallback_country", "default_country"),
    )
    fallback_language: str = Field(
        default="en",
        validation_alias=AliasChoices("fallback_language", "search_language_filter"),
    )

    @model_validator(mode="after")
    def _validate_domain_filters(self):
        """Reject canonical policies the Perplexity API cannot represent."""

        build_perplexity_search_domain_filter(
            self.allowed_domains,
            self.blocked_domains,
        )
        return self

    @field_validator("fallback_language", mode="before")
    @classmethod
    def _validate_fallback_language(cls, value: Any):
        normalized = _normalize_iso_language_code(value if isinstance(value, str) else None)
        if normalized:
            return normalized
        if isinstance(value, list):
            for entry in value:
                normalized = _normalize_iso_language_code(entry if isinstance(entry, str) else None)
                if normalized:
                    return normalized
        return "en"


PERPLEXITY_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="api_key",
            label="API Key",
            description="Perplexity Search API key used for authenticated requests.",
            i18n_label="websearch_schema_field_api_key",
            i18n_description="websearch_schema_field_api_key_desc",
            type="string",
            placeholder="e.g. pplx_sk_your_key",
        ),
        FieldSchema(
            key="respect_robots_txt",
            label="Respect Robots.txt",
            description="Honor robots.txt directives when Perplexity returns combined webpage content.",
            i18n_label="websearch_schema_field_respect_robots_txt",
            i18n_description="websearch_schema_field_respect_robots_txt_desc",
            type="boolean",
            default=True,
            value=True,
        ),
        FieldSchema(
            key="max_results",
            label="Max Results",
            description="Maximum number of ranked Perplexity results per query (1-20).",
            i18n_label="websearch_schema_field_max_results",
            i18n_description="websearch_schema_field_max_results_desc",
            type="number",
            attributes=FieldAttributes(min=1, max=20),
            default=5,
        ),
        FieldSchema(
            key="max_tokens_per_page",
            label="Max Tokens Per Result",
            description="Token budget for extracting content from each individual result.",
            i18n_label="websearch_schema_field_max_tokens_per_result",
            i18n_description="websearch_schema_field_max_tokens_per_result_desc",
            type="number",
        ),
        FieldSchema(
            key="max_tokens",
            label="Max Tokens (Total)",
            description="Total token budget allocated across all returned results.",
            i18n_label="websearch_schema_field_max_tokens_total",
            i18n_description="websearch_schema_field_max_tokens_total_desc",
            type="number",
        ),
        *_perplexity_domain_filter_fields(),
        FieldSchema(
            key="fallback_country",
            label="Fallback Country",
            description="Country code applied when request country is unavailable.",
            i18n_label="websearch_schema_field_fallback_country",
            i18n_description="websearch_schema_field_fallback_country_desc",
            type="select",
            multiple=False,
            options=COUNTRY_SELECT_OPTIONS,
            default="US",
        ),
        FieldSchema(
            key="fallback_language",
            label="Fallback Language",
            description="Language code applied when request language is unavailable.",
            i18n_label="websearch_schema_field_fallback_language",
            i18n_description="websearch_schema_field_fallback_language_desc",
            type="select",
            multiple=False,
            options=LANGUAGE_SELECT_OPTIONS,
            default="en",
        ),
        _forward_user_locale_field(),
    ]
)



# -------------------
# SearxNG Settings
# -------------------
class WebSearchProviderSettingsSearxNG(BaseModel):
    base_url: str
    fallback_language: str = "en"
    forward_user_locale: bool = False
    num_results: int = 5

    _validate_num_results = field_validator("num_results", mode="before")(
        _positive_int_validator(5)
    )

SEARXNG_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="base_url",
            label="Base URL",
            description="Root URL of the SearXNG instance to query.",
            i18n_label="websearch_schema_field_base_url",
            i18n_description="websearch_schema_field_base_url_desc",
            type="string",
        ),
        _language_select_field(
            key="fallback_language",
            label="Fallback Language",
            description="Language code applied when no language is provided for requests.",
        ),
        _forward_user_locale_field(),
        FieldSchema(
            key="num_results",
            label="Number of Results",
            description="Maximum number of SearXNG results returned per query.",
            i18n_label="websearch_schema_field_number_of_results",
            i18n_description="websearch_schema_field_number_of_results_desc",
            type="number",
            default=5,
            value=5,
            attributes=FieldAttributes(min=1),
        ),
    ]
)



# -------------------
# Ollama Settings
# -------------------  
class WebSearchProviderSettingsOllama(_ScrapeDomainFilterSettingsMixin):
    api_key: str
    respect_robots_txt: bool = True
    max_search_results: int = 5  # Default: 5

    _validate_max_search_results = field_validator("max_search_results", mode="before")(
        _positive_int_validator(5)
    )

OLLAMA_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="api_key",
            label="API Key",
            description="Ollama API key used for the combined search workflow.",
            i18n_label="websearch_schema_field_api_key",
            i18n_description="websearch_schema_field_api_key_desc",
            type="string",
            placeholder="e.g. ollama_sk_your_key",
        ),
        FieldSchema(
            key="respect_robots_txt",
            label="Respect Robots.txt",
            description="Honor robots.txt directives when Ollama performs scraping.",
            i18n_label="websearch_schema_field_respect_robots_txt",
            i18n_description="websearch_schema_field_respect_robots_txt_desc",
            type="boolean",
            default=True,
            value=True,
        ),
        FieldSchema(
            key="max_search_results",
            label="Max Search Results",
            description="Maximum number of Ollama-powered search results per query.",
            i18n_label="websearch_schema_field_max_search_results",
            i18n_description="websearch_schema_field_max_search_results_desc",
            type="number",
            default=5,
            value=5,
            attributes=FieldAttributes(min=1),
        ),
        *_scrape_domain_filter_fields(),
    ]
)



# -------------------
# Custom Search Provider Settings
# -------------------
class WebSearchProviderSettingsCustom(_ScrapeDomainFilterSettingsMixin):
    base_url: str
    scrape_base_url: str | None = None
    fallback_country: str = "US"
    forward_user_locale: bool = False
    num_results: int = 5
    respect_robots_txt: bool = True

    _validate_fallback_country = field_validator("fallback_country", mode="before")(_country_field_validator)
    _validate_num_results = field_validator("num_results", mode="before")(
        _positive_int_validator(5)
    )

CUSTOM_WEBSEARCH_PROVIDER_SCHEMA = Section(
    fields=[
        FieldSchema(
            key="base_url",
            label="Base URL",
            description="Endpoint of the custom search provider returning aggregated results.",
            i18n_label="websearch_schema_field_base_url",
            i18n_description="websearch_schema_field_base_url_desc",
            type="string",
        ),
        FieldSchema(
            key="scrape_base_url",
            label="Scrape Base URL",
            description="Optional dedicated endpoint for custom scrape requests. When omitted, Omlorix reuses the Base URL.",
            i18n_label="websearch.custom.scrape_base_url.label",
            i18n_description="websearch.custom.scrape_base_url.description",
            type="string",
            required=False,
        ),
        _country_select_field(
            key="fallback_country",
            label="Fallback Country",
            description="Country code applied to requests when none is supplied.",
        ),
        _forward_user_locale_field(),
        FieldSchema(
            key="num_results",
            label="Number of Results",
            description="Maximum number of results expected from the custom provider per query.",
            i18n_label="websearch_schema_field_number_of_results",
            i18n_description="websearch_schema_field_number_of_results_desc",
            type="number",
            default=5,
            value=5,
            attributes=FieldAttributes(min=1),
        ),
        FieldSchema(
            key="respect_robots_txt",
            label="Respect Robots.txt",
            description="Honor robots.txt directives before custom scrape requests are sent.",
            i18n_label="websearch_schema_field_respect_robots_txt",
            i18n_description="websearch_schema_field_respect_robots_txt_desc",
            type="boolean",
            default=True,
            value=True,
        ),
        *_scrape_domain_filter_fields(),
    ]
)
#    "url": "URL",
#    "title": "Title of website",
#    "preview": "Preview of the website"
#  },


# -------------------
# Update Web Search Provider
# -------------------  
class UpdateWebSearchProvider(BaseModel):
    id: str
    name: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


# -------------------
# Default User Agent
# -------------------  
DEFAULT_USER_AGENT: str = "Omlorix-Search-Bot"


# -------------------
# Registry helpers
# -------------------
WEBSEARCH_PROVIDER_SETTINGS_MODELS: Dict[str, Type[BaseModel]] = {
    "aiohttp": WebSearchProviderSettingsAiohttp,
    "exa": WebSearchProviderSettingsExa,
    "firecrawl": WebSearchProviderSettingsFirecrawl,
    "tavily": WebSearchProviderSettingsTavily,
    "crawl4ai": WebSearchProviderSettingsCrawl4AI,
    "duckduckgo": WebSearchProviderSettingsDuckDuckGo,
    "serper": WebSearchProviderSettingsSerper,
    "you": WebSearchProviderSettingsYou,
    "perplexity": WebSearchProviderSettingsPerplexity,
    "searxng": WebSearchProviderSettingsSearxNG,
    "ollama": WebSearchProviderSettingsOllama,
    "custom": WebSearchProviderSettingsCustom,
}


WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS: Dict[str, Sections] = {
    "aiohttp": Sections(sections=[AIOHTTP_WEBSEARCH_PROVIDER_SCHEMA]),
    "exa": Sections(sections=[EXA_WEBSEARCH_PROVIDER_SCHEMA]),
    "firecrawl": Sections(sections=[FIRECRAWL_WEBSEARCH_PROVIDER_SCHEMA]),
    "tavily": Sections(sections=[TAVILY_WEBSEARCH_PROVIDER_SCHEMA]),
    "crawl4ai": Sections(sections=[CRAWL4AI_WEBSEARCH_PROVIDER_SCHEMA]),
    "duckduckgo": Sections(sections=[DUCKDUCKGO_WEBSEARCH_PROVIDER_SCHEMA]),
    "serper": Sections(sections=[SERPER_WEBSEARCH_PROVIDER_SCHEMA]),
    "you": Sections(sections=[YOU_WEBSEARCH_PROVIDER_SCHEMA]),
    "perplexity": Sections(sections=[PERPLEXITY_WEBSEARCH_PROVIDER_SCHEMA]),
    "searxng": Sections(sections=[SEARXNG_WEBSEARCH_PROVIDER_SCHEMA]),
    "ollama": Sections(sections=[OLLAMA_WEBSEARCH_PROVIDER_SCHEMA]),
    "custom": Sections(sections=[CUSTOM_WEBSEARCH_PROVIDER_SCHEMA]),
}


def _mark_required_fields_from_models():
    """
    Ensure schema metadata marks required inputs (e.g. api_key) so the admin UI
    can enforce non-empty values when creating/editing providers.
    """
    for provider_key, schema in WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS.items():
        settings_model = WEBSEARCH_PROVIDER_SETTINGS_MODELS.get(provider_key)
        if not settings_model or not schema or not getattr(schema, "sections", None):
            continue
        model_fields = getattr(settings_model, "model_fields", {}) or {}
        for section in schema.sections or []:
            for field in getattr(section, "fields", []) or []:
                if field.required is not None:
                    continue
                field_info = model_fields.get(field.key)
                is_required = bool(getattr(field_info, "is_required", lambda: False)())
                if is_required:
                    field.required = True


_mark_required_fields_from_models()


class WebSearchProviderEnum(str, Enum):
    aiohttp = "aiohttp"
    exa = "exa"
    firecrawl = "firecrawl"
    tavily = "tavily"
    crawl4ai = "crawl4ai"
    duckduckgo = "duckduckgo"
    serper = "serper"
    you = "you"
    perplexity = "perplexity"
    searxng = "searxng"
    ollama = "ollama"
    custom = "custom"


class CreateWebSearchProviderRequest(BaseModel):
    provider: "WebSearchProviderEnum"
    name: str
    settings: dict | BaseModel

    @model_validator(mode="after")
    def validate_settings(self):
        provider_key = self.provider.value if isinstance(self.provider, WebSearchProviderEnum) else str(self.provider).lower()
        settings_model = WEBSEARCH_PROVIDER_SETTINGS_MODELS.get(provider_key)
        if settings_model is None:
            raise ValueError(f"Unsupported provider '{self.provider}'.")

        if isinstance(self.settings, settings_model):
            settings_obj = self.settings
        else:
            settings_obj = settings_model.model_validate(self.settings)

        self.settings = settings_obj
        return self


class UpdateWebSearchProviderRequest(BaseModel):
    name: Optional[str] = None
    settings: dict | BaseModel | None = None


class WebSearchProviderListItem(BaseModel):
    id: str
    provider: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class WebSearchProviderDetail(BaseModel):
    id: str
    provider: str
    name: str
    type: List[str] | None = None
    settings: Dict[str, Any]
    model_config = ConfigDict(from_attributes=True)


# -------------------
# Registry
# -------------------
@dataclass(frozen=True)
class WebSearchProviderDefinition:
    key: str
    display_name: str
    description: str
    capabilities: Tuple[str, ...]
    settings_model: Type[BaseModel]
    create_model: Type[BaseModel]
    update_model: Type[BaseModel]


def _build_create_model(key: str, settings_model: Type[BaseModel]) -> Type[BaseModel]:
    return create_model(  # type: ignore[call-overload]
        f"CreateWebSearchProvider{key.title()}",
        name=(str, ...),
        settings=(settings_model, ...),
    )


def _build_update_model(key: str, settings_model: Type[BaseModel]) -> Type[BaseModel]:
    return create_model(  # type: ignore[call-overload]
        f"UpdateWebSearchProvider{key.title()}",
        name=(Optional[str], None),
        settings=(Optional[settings_model], None),
    )


def _definition(
    key: str,
    display_name: str,
    description: str,
    capabilities: Tuple[str, ...],
    settings_model: Type[BaseModel],
) -> WebSearchProviderDefinition:
    return WebSearchProviderDefinition(
        key=key,
        display_name=display_name,
        description=description,
        capabilities=capabilities,
        settings_model=settings_model,
        create_model=_build_create_model(key, settings_model),
        update_model=_build_update_model(key, settings_model),
    )


WEBSEARCH_PROVIDER_REGISTRY: Dict[str, WebSearchProviderDefinition] = {
    "aiohttp": _definition(
        "aiohttp",
        "AIOHTTP",
        "Lightweight scraper built on aiohttp for fetching HTML content.",
        ("scrape",),
        WebSearchProviderSettingsAiohttp,
    ),
    "exa": _definition(
        "exa",
        "Exa",
        "Exa search, scrape, and combined provider offering direct page content in search results.",
        ("scrape", "combined"),
        WebSearchProviderSettingsExa,
    ),
    "firecrawl": _definition(
        "firecrawl",
        "Firecrawl",
        "Firecrawl service for large-scale scraping and search aggregation.",
        ("scrape", "search"),
        WebSearchProviderSettingsFirecrawl,
    ),
    "tavily": _definition(
        "tavily",
        "Tavily",
        "Tavily AI-powered search with scrape support and geo options.",
        ("scrape", "search"),
        WebSearchProviderSettingsTavily,
    ),
    "crawl4ai": _definition(
        "crawl4ai",
        "Crawl4AI",
        "Self-hosted Crawl4AI scraping runtime.",
        ("scrape",),
        WebSearchProviderSettingsCrawl4AI,
    ),
    "custom": _definition(
        "custom",
        "Custom",
        "Custom HTTP provider supporting normalized search results and direct URL scraping.",
        ("search", "scrape"),
        WebSearchProviderSettingsCustom,
    ),
    "duckduckgo": _definition(
        "duckduckgo",
        "DuckDuckGo",
        "DuckDuckGo search API wrapper.",
        ("search",),
        WebSearchProviderSettingsDuckDuckGo,
    ),
    "serper": _definition(
        "serper",
        "Serper",
        "Serper Google SERP API providing organic search results.",
        ("search",),
        WebSearchProviderSettingsSerper,
    ),
    "you": _definition(
        "you",
        "You.com",
        "You.com Search API and Contents API for search plus page scraping.",
        ("scrape", "search"),
        WebSearchProviderSettingsYou,
    ),
    "searxng": _definition(
        "searxng",
        "SearXNG",
        "Self-hosted SearXNG meta search provider.",
        ("search",),
        WebSearchProviderSettingsSearxNG,
    ),
    "ollama": _definition(
        "ollama",
        "Ollama",
        "Ollama combined search & scrape workflow.",
        ("scrape", "combined"),
        WebSearchProviderSettingsOllama,
    ),
    "perplexity": _definition(
        "perplexity",
        "Perplexity",
        "Perplexity Search API providing ranked results with extracted context. Direct URL scraping still requires a separate scrape provider.",
        ("combined",),
        WebSearchProviderSettingsPerplexity,
    ),
}


def get_websearch_provider_definition(provider: str) -> WebSearchProviderDefinition:
    key = provider.lower()
    if key not in WEBSEARCH_PROVIDER_REGISTRY:
        raise KeyError(provider)
    return WEBSEARCH_PROVIDER_REGISTRY[key]


def normalize_websearch_provider_settings(provider: str, settings: Any) -> dict[str, Any]:
    """Normalize stored provider settings for API responses and edit forms."""
    settings_dict = dict(settings or {}) if isinstance(settings, dict) else {}
    settings_for_validation = dict(settings_dict)
    settings_for_validation["api_key"] = str(settings_dict.get("api_key") or "")

    try:
        definition = get_websearch_provider_definition(provider)
    except KeyError:
        return settings_dict

    normalized = definition.settings_model.model_validate(settings_for_validation).model_dump()
    normalized.pop("api_key", None)
    return normalized
