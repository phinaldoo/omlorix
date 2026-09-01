import sys
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.websearch.firecrawl_proxy import (  # noqa: E402
    FIRECRAWL_HOSTED_BASE_URL,
    normalize_firecrawl_proxy_mode,
)
from app.tools.websearch.schemas import (  # noqa: E402
    WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS,
    WebSearchProviderSettingsFirecrawl,
)
from app.tools.websearch.scrape.firecrawl_scrape import firecrawl_scrape_urls  # noqa: E402


@pytest.mark.parametrize(
    ("proxy", "expected_proxy"),
    [
        ("auto", "auto"),
        ("BASIC", "basic"),
        ("enhanced", "enhanced"),
        (None, "auto"),
        ("unsupported", "auto"),
    ],
)
def test_firecrawl_proxy_modes_are_normalized_to_supported_values(proxy, expected_proxy):
    """Firecrawl requests use only the supported public proxy modes."""

    assert normalize_firecrawl_proxy_mode(proxy) == expected_proxy


def test_firecrawl_settings_and_schema_expose_supported_proxy_modes():
    """Settings and admin metadata expose the same supported modes."""

    hosted = WebSearchProviderSettingsFirecrawl.model_validate(
        {"api_key": "test-key", "proxy": "enhanced"}
    )
    custom = WebSearchProviderSettingsFirecrawl.model_validate(
        {
            "api_key": "test-key",
            "base_url": "https://firecrawl.example/",
            "proxy": "BASIC",
        }
    )
    malformed = WebSearchProviderSettingsFirecrawl.model_validate(
        {"api_key": "test-key", "proxy": "unsupported"}
    )

    firecrawl_fields = {
        field.key: field
        for section in WEBSEARCH_PROVIDER_SETTINGS_SCHEMAS["firecrawl"].sections
        for field in section.fields
    }
    base_url_field = firecrawl_fields["base_url"]
    proxy_field = firecrawl_fields["proxy"]

    assert hosted.base_url == FIRECRAWL_HOSTED_BASE_URL
    assert hosted.proxy == "enhanced"
    assert custom.base_url == "https://firecrawl.example"
    assert custom.proxy == "basic"
    assert malformed.proxy == "auto"
    assert base_url_field.default == FIRECRAWL_HOSTED_BASE_URL
    assert base_url_field.value == FIRECRAWL_HOSTED_BASE_URL
    assert proxy_field.default == "auto"
    assert proxy_field.value == "auto"
    assert proxy_field.metadata is None
    assert [option.value for option in proxy_field.options] == [
        "auto",
        "basic",
        "enhanced",
    ]


class _FirecrawlResponse:
    """Minimal requests response used to exercise Firecrawl payload creation."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.parametrize(
    ("base_url", "proxy", "expected_base_url", "expected_proxy"),
    [
        (FIRECRAWL_HOSTED_BASE_URL, "enhanced", FIRECRAWL_HOSTED_BASE_URL, "enhanced"),
        (
            "https://api.firecrawl.dev./",
            "basic",
            "https://api.firecrawl.dev.",
            "basic",
        ),
        ("https://firecrawl.example/", "unsupported", "https://firecrawl.example", "auto"),
    ],
)
def test_firecrawl_batch_scrape_sends_the_effective_proxy_literal(
    base_url,
    proxy,
    expected_base_url,
    expected_proxy,
):
    """The final HTTP payload uses only supported proxy modes."""

    completed = _FirecrawlResponse(
        {
            "status": "completed",
            "data": [
                {
                    "metadata": {"url": "https://example.com", "title": "Example"},
                    "markdown": "Example body",
                }
            ],
            "creditsUsed": 1,
        }
    )

    with (
        patch(
            "app.tools.websearch.scrape.firecrawl_scrape.requests.post",
            return_value=_FirecrawlResponse({"id": "job-1"}),
        ) as mock_post,
        patch(
            "app.tools.websearch.scrape.firecrawl_scrape.requests.get",
            return_value=completed,
        ),
    ):
        result = firecrawl_scrape_urls(
            "test-key",
            ["https://example.com"],
            proxy=proxy,
            base_url=base_url,
        )

    assert mock_post.call_args.args[0] == f"{expected_base_url}/v2/batch/scrape"
    assert mock_post.call_args.kwargs["json"]["proxy"] == expected_proxy
    assert result["result"][0]["markdown"] == "Example body"
