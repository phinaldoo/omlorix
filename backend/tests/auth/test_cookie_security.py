import sys
from pathlib import Path
import ipaddress
import logging
from typing import Any

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.auth import account_slots
from app.auth import utils as auth_utils


def _request(
    scheme: str = "http",
    *,
    client_host: str = "127.0.0.1",
    headers: dict[str, str] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": scheme,
            "server": ("chat.example", 443 if scheme == "https" else 80),
            "path": "/api/v1/auth/login",
            "client": (client_host, 12345),
            "headers": Headers(headers or {}).raw,
        }
    )


def _settings(
    public_url: Any = "",
    refresh_cookie_secure=None,
    refresh_cookie_samesite: str = "lax",
    trust_proxy_headers: bool = False,
    trusted_proxies: list[str] | None = None,
):
    def fake_setting(page, key, _db):
        values = {
            ("general", "public_url"): public_url,
            ("security", "access_token_expire_minutes"): 5,
            ("security", "refresh_cookie_secure"): refresh_cookie_secure,
            ("security", "refresh_cookie_samesite"): refresh_cookie_samesite,
            ("security", "refresh_token_expire_minutes"): 60,
            ("security", "trust_proxy_headers"): trust_proxy_headers,
            ("security", "trusted_proxies"): trusted_proxies or [],
        }
        return values.get((page, key))

    return fake_setting


def _refresh_cookie_header(
    monkeypatch,
    *,
    public_url: str = "",
    mode: str | None = None,
    request: Request | None = None,
    trust_proxy_headers: bool = False,
    trusted_proxies: list[str] | None = None,
    refresh_cookie_samesite: str = "lax",
) -> str:
    if mode is None:
        monkeypatch.delenv("MODE", raising=False)
    else:
        monkeypatch.setenv("MODE", mode)
    monkeypatch.setattr(
        account_slots,
        "get_value_by_page_and_key",
        _settings(
            public_url=public_url,
            refresh_cookie_samesite=refresh_cookie_samesite,
            trust_proxy_headers=trust_proxy_headers,
            trusted_proxies=trusted_proxies,
        ),
    )

    response = Response()
    account_slots.set_refresh_slot_cookie(response, 1, "refresh-token", object(), request or _request("http"))
    return response.headers["set-cookie"].lower()


def _access_cookie_header(
    monkeypatch,
    *,
    public_url: str = "",
    mode: str | None = None,
    refresh_cookie_samesite: str = "lax",
) -> str:
    if mode is None:
        monkeypatch.delenv("MODE", raising=False)
    else:
        monkeypatch.setenv("MODE", mode)
    monkeypatch.setattr(
        account_slots,
        "get_value_by_page_and_key",
        _settings(public_url=public_url, refresh_cookie_samesite=refresh_cookie_samesite),
    )

    response = Response()
    account_slots.set_access_token_cookie(response, "access-token", object(), _request("http"))
    return response.headers["set-cookie"].lower()


def _one_time_cookie_header(
    monkeypatch,
    *,
    public_url: str = "",
    mode: str | None = None,
    request: Request | None = None,
    trust_proxy_headers: bool = False,
    trusted_proxies: list[str] | None = None,
) -> str:
    if mode is None:
        monkeypatch.delenv("MODE", raising=False)
    else:
        monkeypatch.setenv("MODE", mode)
    fake_settings = _settings(
        public_url=public_url,
        trust_proxy_headers=trust_proxy_headers,
        trusted_proxies=trusted_proxies,
    )
    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", fake_settings)
    monkeypatch.setattr(auth_utils, "get_value_by_page_and_key", fake_settings)

    response = Response()
    auth_utils._set_one_time_browser_cookie(
        response,
        "social_state",
        "state",
        object(),
        request or _request("http"),
    )
    return response.headers["set-cookie"].lower()


def test_configured_public_url_failure_logs_debug_and_falls_back(monkeypatch, caplog):
    """Bootstrap failures remain tolerated while leaving useful debug context."""
    def _raise_settings_error(*_args):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(account_slots, "get_value_by_page_and_key", _raise_settings_error)

    with caplog.at_level(logging.DEBUG, logger=account_slots.logger.name):
        assert account_slots._get_configured_public_urls(object()) == []

    assert "Unable to retrieve or normalize configured public URLs" in caplog.text
    assert "settings unavailable" in caplog.text


def test_refresh_cookie_defaults_to_secure_outside_dev(monkeypatch):
    cookie = _refresh_cookie_header(monkeypatch)

    assert "secure" in cookie


def test_refresh_cookie_can_remain_insecure_in_dev(monkeypatch):
    cookie = _refresh_cookie_header(monkeypatch, mode="dev")

    assert "secure" not in cookie


def test_refresh_cookie_downgrades_samesite_none_when_cookie_is_insecure(monkeypatch):
    cookie = _refresh_cookie_header(monkeypatch, mode="dev", refresh_cookie_samesite="none")

    assert "secure" not in cookie
    assert "samesite=lax" in cookie
    assert "samesite=none" not in cookie


def test_refresh_cookie_uses_secure_when_public_url_is_https(monkeypatch):
    cookie = _refresh_cookie_header(monkeypatch, public_url="https://chat.example", mode="dev")

    assert "secure" in cookie


def test_access_cookie_uses_same_security_defaults_as_refresh_cookie(monkeypatch):
    cookie = _access_cookie_header(monkeypatch)

    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_access_cookie_keeps_samesite_none_when_cookie_is_secure(monkeypatch):
    cookie = _access_cookie_header(monkeypatch, refresh_cookie_samesite="none")

    assert "secure" in cookie
    assert "samesite=none" in cookie


def test_one_time_cookie_defaults_to_secure_outside_dev(monkeypatch):
    cookie = _one_time_cookie_header(monkeypatch)

    assert "secure" in cookie
    assert "samesite=none" in cookie


def test_one_time_cookie_uses_secure_when_public_url_is_https(monkeypatch):
    cookie = _one_time_cookie_header(monkeypatch, public_url="https://chat.example", mode="dev")

    assert "secure" in cookie
    assert "samesite=none" in cookie


def test_refresh_cookie_uses_forwarded_https_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(
        account_slots,
        "resolve_configured_trusted_proxy_networks",
        lambda *_args, **_kwargs: [ipaddress.ip_network("10.0.0.0/8")],
    )
    request = _request(
        "http",
        client_host="10.0.0.2",
        headers={"X-Forwarded-Proto": "https"},
    )

    cookie = _refresh_cookie_header(
        monkeypatch,
        mode="dev",
        request=request,
        trust_proxy_headers=True,
        trusted_proxies=["10.0.0.0/8"],
    )

    assert "secure" in cookie


def test_one_time_cookie_uses_samesite_none_for_forwarded_https_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(
        account_slots,
        "resolve_configured_trusted_proxy_networks",
        lambda *_args, **_kwargs: [ipaddress.ip_network("10.0.0.0/8")],
    )
    request = _request(
        "http",
        client_host="10.0.0.2",
        headers={"Forwarded": 'for=203.0.113.10;proto="https"'},
    )

    cookie = _one_time_cookie_header(
        monkeypatch,
        mode="dev",
        request=request,
        trust_proxy_headers=True,
        trusted_proxies=["10.0.0.0/8"],
    )

    assert "secure" in cookie
    assert "samesite=none" in cookie


def test_redirect_base_url_uses_forwarded_https_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(
        account_slots,
        "resolve_configured_trusted_proxy_networks",
        lambda *_args, **_kwargs: [ipaddress.ip_network("10.0.0.0/8")],
    )
    monkeypatch.setattr(
        account_slots,
        "get_value_by_page_and_key",
        _settings(
            trust_proxy_headers=True,
            trusted_proxies=["10.0.0.0/8"],
        ),
    )
    request = _request(
        "http",
        client_host="10.0.0.2",
        headers={"X-Forwarded-Proto": "https"},
    )

    base_url = account_slots.build_auth_redirect_base_url(object(), request)

    assert base_url == "https://chat.example"


def test_redirect_base_url_uses_matching_secondary_public_origin(monkeypatch):
    """OAuth/SSO callbacks stay on the configured origin where the flow began."""
    monkeypatch.setattr(
        account_slots,
        "get_value_by_page_and_key",
        _settings(public_url=["https://primary.example", "https://secondary.example"]),
    )
    request = _request(
        "https",
        headers={"Origin": "https://secondary.example"},
    )

    assert account_slots.build_auth_redirect_base_url(object(), request) == "https://secondary.example"


def test_redirect_base_url_preserves_matching_secondary_ipv6_origin(monkeypatch):
    """IPv6 request origins retain brackets when matched against configured URLs."""
    monkeypatch.setattr(
        account_slots,
        "get_value_by_page_and_key",
        _settings(public_url=["https://primary.example", "https://[2001:db8::2]:8443"]),
    )
    request = _request(
        "https",
        headers={"Origin": "https://[2001:db8::2]:8443/path"},
    )

    assert account_slots.build_auth_redirect_base_url(object(), request) == "https://[2001:db8::2]:8443"


def test_untrusted_forwarded_https_does_not_override_request_scheme(monkeypatch):
    monkeypatch.setattr(
        account_slots,
        "resolve_configured_trusted_proxy_networks",
        lambda *_args, **_kwargs: [ipaddress.ip_network("10.0.0.0/8")],
    )
    request = _request(
        "http",
        client_host="198.51.100.5",
        headers={"X-Forwarded-Proto": "https"},
    )

    cookie = _one_time_cookie_header(
        monkeypatch,
        mode="dev",
        request=request,
        trust_proxy_headers=True,
        trusted_proxies=["10.0.0.0/8"],
    )

    assert "secure" not in cookie
    assert "samesite=lax" in cookie
