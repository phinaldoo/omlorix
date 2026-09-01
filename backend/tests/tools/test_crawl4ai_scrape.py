import sys
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.websearch.scrape.utils import _crawl4ai


class _FakeCrawl4AIResponse:
    """Return the smallest successful response accepted by the Omlorix adapter."""

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "success": True,
            "results": [
                {
                    "url": "https://example.com",
                    "success": True,
                    "markdown": {"raw_markdown": "Example"},
                }
            ],
        }


def _run_crawl4ai(settings: dict):
    """Dispatch through the provider-settings boundary used by web scraping."""

    return _crawl4ai(
        ["https://example.com"],
        None,
        None,
        settings,
        False,
    )


def test_crawl4ai_dispatch_sends_configured_api_token_as_bearer_auth():
    """A stored Crawl4AI credential must reach the protected /crawl endpoint."""

    with patch(
        "app.tools.websearch.scrape.crawl4ai_scrape.requests.post",
        return_value=_FakeCrawl4AIResponse(),
    ) as mock_post:
        result = _run_crawl4ai(
            {
                "base_url": "https://crawl4ai.example",
                "api_key": " crawl4ai-token ",
                "retry_count": 0,
            }
        )

    assert result["result"] == [
        {"url": "https://example.com", "markdown": "Example"}
    ]
    assert mock_post.call_args.args[0] == "https://crawl4ai.example/crawl"
    assert mock_post.call_args.kwargs["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer crawl4ai-token",
    }
    assert "crawl4ai-token" not in str(mock_post.call_args.kwargs["json"])


def test_crawl4ai_dispatch_keeps_authorization_optional_for_restricted_proxies():
    """A blank token preserves legacy and authentication-injecting proxy setups."""

    with patch(
        "app.tools.websearch.scrape.crawl4ai_scrape.requests.post",
        return_value=_FakeCrawl4AIResponse(),
    ) as mock_post:
        _run_crawl4ai(
            {
                "base_url": "https://crawl4ai-proxy.example",
                "api_key": "   ",
                "retry_count": 0,
            }
        )

    assert mock_post.call_args.kwargs["headers"] == {
        "Content-Type": "application/json"
    }
