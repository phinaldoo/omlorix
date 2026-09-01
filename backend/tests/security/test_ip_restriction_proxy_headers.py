from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import HTTPConnection, Request

import app.settings.utils as settings_utils
from app.middleware import ip_restriction


_PROXY_ENV_NAMES = (
    "TRUSTED_PROXIES",
    "OMLORIX_TRUSTED_PROXIES",
    "AUTH_TRUSTED_PROXIES",
    "RATE_LIMIT_TRUSTED_PROXIES",
    "TRUST_PROXY_HEADERS",
    "OMLORIX_TRUST_PROXY_HEADERS",
)


def _request(client_host: str, headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": (client_host, 12345),
            "path": "/",
            "headers": headers,
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )


def _trusted_proxy_settings(page_name: str, key_name: str, db):
    values = {
        ("security", "trust_proxy_headers"): True,
        ("security", "trusted_proxies"): ["10.0.0.10"],
    }
    return values.get((page_name, key_name))


@pytest.fixture(autouse=True)
def trusted_proxy_settings(monkeypatch):
    for name in _PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings_utils, "get_value_by_page_and_key", _trusted_proxy_settings)
    monkeypatch.setattr(ip_restriction, "get_value_by_page_and_key", _trusted_proxy_settings)


def test_get_client_ip_uses_valid_forwarded_chain_from_trusted_proxy():
    request = _request("10.0.0.10", [(b"x-forwarded-for", b"203.0.113.5")])

    assert ip_restriction.get_client_ip(request, db=object()) == "203.0.113.5"


def test_get_client_ip_uses_valid_forwarded_chain_for_websocket():
    connection = HTTPConnection(
        {
            "type": "websocket",
            "scheme": "ws",
            "server": ("127.0.0.1", 8000),
            "client": ("10.0.0.10", 12345),
            "path": "/api/v1/realtime/transcription/live",
            "headers": [(b"x-forwarded-for", b"203.0.113.5")],
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )

    assert ip_restriction.get_client_ip(connection, db=object()) == "203.0.113.5"


def test_get_client_ip_rejects_malformed_forwarded_chain_from_trusted_proxy():
    request = _request("10.0.0.10", [(b"x-forwarded-for", b"203.0.113.5, not-an-ip")])

    assert ip_restriction.get_client_ip(request, db=object()) is None


def test_get_client_ip_rejects_untrusted_intermediate_forwarded_hop():
    request = _request("10.0.0.10", [(b"x-forwarded-for", b"203.0.113.5, 198.51.100.99")])

    assert ip_restriction.get_client_ip(request, db=object()) is None
