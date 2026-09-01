import asyncio
import sys
from pathlib import Path

import aiohttp
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.websearch.schemas import DEFAULT_USER_AGENT
from app.tools.websearch.scrape import aiohttp_scrape


def test_aiohttp_scrape_uses_default_websearch_user_agent(monkeypatch):
    captured = {}

    class FakeClientSession:
        def __init__(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(aiohttp_scrape.aiohttp, "ClientSession", FakeClientSession)

    result = asyncio.run(aiohttp_scrape._aiohttp_scrape_urls([]))

    assert result == []
    assert captured["headers"] == {"User-Agent": DEFAULT_USER_AGENT}


def test_aiohttp_scrape_keeps_successes_and_reports_failed_pages(monkeypatch):
    """One remote HTTP rejection must not discard other scraped pages."""

    class FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_fetch(_session, url, **_kwargs):
        if url.endswith("blocked"):
            raise aiohttp.ClientResponseError(
                request_info=None,
                history=(),
                status=403,
                message="Forbidden",
            )
        return {"url": url, "content": "available page"}

    monkeypatch.setattr(aiohttp_scrape.aiohttp, "ClientSession", FakeClientSession)
    monkeypatch.setattr(aiohttp_scrape, "fetch", fake_fetch)

    result = asyncio.run(
        aiohttp_scrape._aiohttp_scrape_urls(
            ["https://example.test/available", "https://example.test/blocked"]
        )
    )

    assert result == [
        {
            "url": "https://example.test/available",
            "content": "available page",
        },
        {
            "url": "https://example.test/blocked",
            "content": None,
            "title": None,
            "error": "HTTP 403: Forbidden",
            "failed": True,
            "status_code": 403,
        },
    ]


def test_aiohttp_scrape_does_not_convert_policy_failures(monkeypatch):
    """Security-policy exceptions must remain fatal instead of looking remote."""

    async def blocked_fetch(_session, _url, **_kwargs):
        raise HTTPException(status_code=403, detail="Blocked by outbound policy")

    monkeypatch.setattr(aiohttp_scrape, "fetch", blocked_fetch)

    with pytest.raises(HTTPException, match="Blocked by outbound policy"):
        asyncio.run(
            aiohttp_scrape._fetch_with_failure_result(
                object(),
                "https://blocked.example",
                verify_ssl=True,
                timeout=10,
                view_raw=False,
                url_validator=None,
            )
        )
