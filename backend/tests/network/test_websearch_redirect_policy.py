import asyncio
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.network.policy import OutboundAccessMode, OutboundRequestBlockedError
from app.tools.websearch import utils as websearch_utils
from app.tools.websearch.scrape import aiohttp_scrape
from app.tools.websearch.scrape import utils as scrape_utils
from yarl import URL


class _AsyncResponse:
    def __init__(self, status, headers, url):
        self.status = status
        self.headers = headers
        self.url = URL(url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def text(self):
        return "ok"


class _AsyncSession:
    def __init__(self):
        self.requested_urls = []

    def get(self, url, **kwargs):
        self.requested_urls.append(url)
        return _AsyncResponse(
            302, {"Location": "http://169.254.169.254/latest/meta-data/"}, url
        )


class _DummyResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in {301, 302, 303, 307, 308}

    def close(self):
        self.closed = True


def test_requests_redirect_target_is_checked_before_following(monkeypatch):
    calls = []
    redirect_response = _DummyResponse(
        302, {"Location": "http://169.254.169.254/latest/meta-data/"}
    )

    def fake_assert(_db, url, *, feature):
        calls.append((url, feature))
        if "169.254.169.254" in url:
            raise HTTPException(status_code=403, detail="blocked")

    def fake_request(method, url, *, feature, **kwargs):
        assert method == "GET"
        assert feature == "test fetch"
        assert url == "https://allowed.example/redirect"
        return redirect_response

    monkeypatch.setattr(websearch_utils, "_assert_websearch_url_allowed", fake_assert)
    monkeypatch.setattr(websearch_utils, "public_web_request", fake_request)

    with pytest.raises(HTTPException):
        websearch_utils._requests_request_with_policy_redirects(
            object(),
            "GET",
            "https://allowed.example/redirect",
            feature="test fetch",
        )

    assert calls == [
        ("https://allowed.example/redirect", "test fetch"),
        ("http://169.254.169.254/latest/meta-data/", "test fetch"),
    ]
    assert redirect_response.closed is True


def test_aiohttp_scrape_receives_redirect_validator(monkeypatch):
    captured = {}

    def fake_aiohttp_scrape_urls(urls, *, verify_ssl, view_raw, url_validator, resolved_ip_validator):
        captured["urls"] = urls
        captured["verify_ssl"] = verify_ssl
        captured["view_raw"] = view_raw
        captured["url_validator"] = url_validator
        captured["resolved_ip_validator"] = resolved_ip_validator
        return [{"url": urls[0], "content": "ok"}]

    monkeypatch.setattr(scrape_utils, "aiohttp_scrape_urls", fake_aiohttp_scrape_urls)
    provider = SimpleNamespace(
        provider="aiohttp", settings={"verify_ssl_certificate": True}
    )

    def validator(target):
        return None

    def resolved_ip_validator(ip_address):
        return None

    result = scrape_utils.scrape(
        ["https://allowed.example/page"],
        "US",
        "en",
        provider,
        url_validator=validator,
        resolved_ip_validator=resolved_ip_validator,
    )

    assert result == [{"url": "https://allowed.example/page", "content": "ok"}]
    assert captured == {
        "urls": ["https://allowed.example/page"],
        "verify_ssl": True,
        "view_raw": False,
        "url_validator": validator,
        "resolved_ip_validator": resolved_ip_validator,
    }


def test_aiohttp_resolver_validates_resolved_peer_ips(monkeypatch):
    calls = []

    def validate_ip(ip_address):
        calls.append(ip_address)

    def fake_resolve(host, port, *, feature, family):
        assert host == "example.com"
        assert port == 443
        assert feature == "Direct web scrape"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("151.101.1.69", port),
            ),
        ]

    monkeypatch.setattr(aiohttp_scrape, "resolve_public_tcp_addresses", fake_resolve)
    resolver = aiohttp_scrape.PolicyCheckedResolver(validate_ip)

    records = asyncio.run(resolver.resolve("example.com", 443))

    assert records == [
        {
            "hostname": "example.com",
            "host": "93.184.216.34",
            "port": 443,
            "family": socket.AF_INET,
            "proto": socket.IPPROTO_TCP,
            "flags": socket.AI_NUMERICHOST,
        },
        {
            "hostname": "example.com",
            "host": "151.101.1.69",
            "port": 443,
            "family": socket.AF_INET,
            "proto": socket.IPPROTO_TCP,
            "flags": socket.AI_NUMERICHOST,
        },
    ]
    assert calls == ["93.184.216.34", "151.101.1.69"]


def test_aiohttp_scrape_defaults_verify_ssl_to_enabled(monkeypatch):
    captured = {}

    def fake_aiohttp_scrape_urls(urls, *, verify_ssl, view_raw, url_validator, resolved_ip_validator):
        captured["urls"] = urls
        captured["verify_ssl"] = verify_ssl
        captured["view_raw"] = view_raw
        captured["url_validator"] = url_validator
        captured["resolved_ip_validator"] = resolved_ip_validator
        return [{"url": urls[0], "content": "ok"}]

    monkeypatch.setattr(scrape_utils, "aiohttp_scrape_urls", fake_aiohttp_scrape_urls)
    provider = SimpleNamespace(provider="aiohttp", settings={})

    result = scrape_utils.scrape(
        ["https://allowed.example/page"],
        "US",
        "en",
        provider,
    )

    assert result == [{"url": "https://allowed.example/page", "content": "ok"}]
    assert captured == {
        "urls": ["https://allowed.example/page"],
        "verify_ssl": True,
        "view_raw": False,
        "url_validator": None,
        "resolved_ip_validator": None,
    }


def test_detect_url_type_marks_blocked_redirect_target(monkeypatch):
    calls = []

    def fake_head(_db, url, *, feature, timeout, headers):
        calls.append((url, feature))
        raise HTTPException(status_code=403, detail="blocked")

    monkeypatch.setattr(
        websearch_utils, "_head_request_with_policy_redirects", fake_head
    )
    def allow_url(*args, **kwargs):
        return None

    monkeypatch.setattr(websearch_utils, "_assert_websearch_url_allowed", allow_url)

    assert (
        websearch_utils.detect_url_type("https://allowed.example/redirect", db=object())
        == "blocked"
    )
    assert calls == [("https://allowed.example/redirect", "URL content type detection")]


def test_aiohttp_fetch_checks_redirect_target_before_following():
    calls = []
    session = _AsyncSession()

    def validator(url):
        calls.append(url)
        if "169.254.169.254" in url:
            raise HTTPException(status_code=403, detail="blocked")

    with pytest.raises(HTTPException, match="blocked"):
        asyncio.run(
            aiohttp_scrape.fetch(
                session,
                "https://allowed.example/redirect",
                url_validator=validator,
            )
        )

    assert session.requested_urls == ["https://allowed.example/redirect"]
    assert calls == [
        "https://allowed.example/redirect",
        "http://169.254.169.254/latest/meta-data/",
    ]


def test_aiohttp_public_resolver_blocks_connect_time_private_dns(monkeypatch):
    def fake_resolve(host, port, *, feature, family):
        raise OutboundRequestBlockedError(
            target=f"{host}:{port} (127.0.0.1)",
            feature=feature,
            policy_mode=OutboundAccessMode.allow_all,
            reason="connected peer IP is not publicly routable",
        )

    monkeypatch.setattr(aiohttp_scrape, "resolve_public_tcp_addresses", fake_resolve)

    resolver = aiohttp_scrape.PublicWebResolver(feature="Direct web scrape")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(resolver.resolve("rebind.example", 80))

    assert "connected peer IP is not publicly routable" in exc_info.value.detail
