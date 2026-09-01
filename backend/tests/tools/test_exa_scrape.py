from app.tools.websearch.scrape import exa_scrape


class _FakeResponse:
    """Return a representative successful response without contacting Exa."""

    def raise_for_status(self):
        """Model a successful HTTP status."""

    def json(self):
        """Provide the fields consumed by the Omlorix scrape adapter."""

        return {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com/article",
                    "text": "Extracted article text",
                }
            ],
            "costDollars": {"total": 0.003},
        }


def test_exa_scrape_uses_top_level_text_option(monkeypatch):
    """The Contents endpoint must not receive Search's nested contents object."""

    captured = {}

    def fake_post(url, **kwargs):
        """Capture the outbound request and return a successful Exa response."""

        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(exa_scrape.requests, "post", fake_post)

    result = exa_scrape.exa_scrape_urls(
        api_key="exa-key",
        urls=["https://example.com/article"],
    )

    assert captured == {
        "url": exa_scrape.EXA_CONTENTS_URL,
        "headers": {
            "Authorization": "Bearer exa-key",
            "Content-Type": "application/json",
        },
        "json": {
            "urls": ["https://example.com/article"],
            "text": True,
        },
        "timeout": exa_scrape.REQUEST_TIMEOUT_SECONDS,
    }
    assert result == {
        "result": [
            {
                "title": "Example",
                "url": "https://example.com/article",
                "content": "Extracted article text",
            }
        ],
        "metadata": {
            "provider_scrape": "exa",
            "cost": 0.003,
        },
    }
