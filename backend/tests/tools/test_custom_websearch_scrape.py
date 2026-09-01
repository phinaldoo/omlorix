import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.network.policy import OutboundAccessMode
from app.tools.websearch.scrape.custom_scrape import custom_scrape_urls
from app.tools.websearch.scrape.utils import scrape
from app.tools.websearch import models as websearch_models


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_custom_scrape_urls_raises_wrapped_result_errors():
    payload = {
        "results": [
            {
                "url": "https://example.com/a",
                "title": "Example A",
                "markdown": "# A",
            },
            {
                "link": "https://example.com/b",
                "html": "<html>B</html>",
                "error": "partial failure",
            },
        ]
    }

    with patch("app.tools.websearch.scrape.custom_scrape.requests.post", return_value=_FakeResponse(payload)) as mock_post:
        with pytest.raises(Exception, match="partial failure"):
            custom_scrape_urls(
                "https://custom.example/scrape",
                ["https://example.com/a", "https://example.com/b"],
                country="DE",
                language="de",
                view_raw=False,
            )

    assert mock_post.call_args.kwargs["json"] == {
        "urls": ["https://example.com/a", "https://example.com/b"],
        "country": "DE",
        "language": "de",
        "view_raw": False,
    }


def test_custom_scrape_urls_supports_raw_html_results():
    payload = [
        {
            "url": "https://example.com/raw",
            "rawHtml": "<html>raw</html>",
        }
    ]

    with patch("app.tools.websearch.scrape.custom_scrape.requests.post", return_value=_FakeResponse(payload)):
        response = custom_scrape_urls(
            "https://custom.example/scrape",
            ["https://example.com/raw"],
            country="US",
            language="en",
            view_raw=True,
        )

    assert response["result"] == [
        {
            "url": "https://example.com/raw",
            "title": "",
            "content": "<html>raw</html>",
        }
    ]


def test_custom_scrape_urls_validates_exact_request_url_before_dispatch():
    seen_urls: list[str] = []

    def validator(url):
        seen_urls.append(url)
        raise RuntimeError("blocked")

    with patch("app.tools.websearch.scrape.custom_scrape.requests.post") as mock_post:
        with pytest.raises(RuntimeError, match="blocked"):
            custom_scrape_urls(
                "https://custom.example/scrape",
                ["https://example.com/raw"],
                url_validator=validator,
            )

    assert seen_urls == ["https://custom.example/scrape"]
    mock_post.assert_not_called()


def test_scrape_dispatcher_uses_custom_scrape_endpoint_when_present():
    provider = SimpleNamespace(
        provider="custom",
        settings={
            "base_url": "https://custom.example/search",
            "scrape_base_url": "https://custom.example/scrape",
            "fallback_country": "US",
        },
    )

    with patch(
        "app.tools.websearch.scrape.utils.custom_scrape_urls",
        return_value={"result": []},
    ) as mock_custom_scrape:
        scrape(["https://example.com"], "FR", "fr", provider, view_raw=True)

    assert mock_custom_scrape.call_args.args[0] == "https://custom.example/scrape"
    assert mock_custom_scrape.call_args.args[1] == ["https://example.com"]
    assert mock_custom_scrape.call_args.kwargs["country"] == "FR"
    assert mock_custom_scrape.call_args.kwargs["language"] == "fr"
    assert mock_custom_scrape.call_args.kwargs["view_raw"] is True
    assert "url_validator" in mock_custom_scrape.call_args.kwargs


def test_custom_provider_create_validates_scrape_base_url_policy(monkeypatch):
    captured_settings: list[dict] = []

    def deny_if_scrape_base_url(_db, provider, *, feature, include_all_targets=False, **_kwargs):
        captured_settings.append(provider.settings)
        assert include_all_targets is True
        if provider.settings.get("scrape_base_url") == "http://127.0.0.1/scrape":
            raise websearch_models.OutboundRequestBlockedError(
                target=provider.settings["scrape_base_url"],
                feature=feature,
                policy_mode=OutboundAccessMode.allowlist_only,
                reason="blocked",
            )

    monkeypatch.setattr(websearch_models, "assert_websearch_provider_allowed", deny_if_scrape_base_url)

    with pytest.raises(HTTPException) as exc:
        websearch_models.create_websearch_provider(
            object(),
            "custom",
            "Custom",
            {
                "base_url": "https://custom.example/search",
                "scrape_base_url": "http://127.0.0.1/scrape",
            },
        )
    assert exc.value.status_code == 403
    assert "blocked" in exc.value.detail

    assert captured_settings == [
        {
            "base_url": "https://custom.example/search",
            "scrape_base_url": "http://127.0.0.1/scrape",
        }
    ]
