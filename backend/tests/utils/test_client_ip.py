import ipaddress
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.utils.client_ip import (
    extract_client_ip_from_request,
    resolve_audit_request_client_ip,
    resolve_configured_trusted_proxy_networks,
    resolve_trusted_proxy_networks,
)


class _Request:
    def __init__(self, client_host, headers=None):
        self.client = SimpleNamespace(host=client_host)
        self.headers = headers or {}


def test_env_trusted_proxy_resolves_forwarded_client(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXIES", "172.16.0.0/12")

    networks = resolve_configured_trusted_proxy_networks()
    request = _Request(
        "172.18.0.4",
        {"x-forwarded-for": "203.0.113.10, 172.18.0.2"},
    )

    assert extract_client_ip_from_request(request, trusted_proxy_networks=networks) == "203.0.113.10"


def test_global_trusted_proxy_env_resolves_dependency_client_networks(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "172.16.0.0/12")

    networks = resolve_trusted_proxy_networks(
        "AUTH_TRUSTED_PROXIES",
        "RATE_LIMIT_TRUSTED_PROXIES",
        "TRUSTED_PROXIES",
    )
    request = _Request(
        "172.18.0.4",
        {"x-forwarded-for": "203.0.113.10, 172.18.0.2"},
    )

    assert extract_client_ip_from_request(request, trusted_proxy_networks=networks) == "203.0.113.10"


def test_untrusted_docker_gateway_stops_attacker_supplied_forwarded_chain():
    """A NAT peer must remain the trust boundary for directly published traffic."""
    request = _Request(
        "172.31.250.10",
        {"x-forwarded-for": "203.0.113.66, 172.31.250.1"},
    )
    frontend_only = [ipaddress.ip_network("172.31.250.10/32")]

    assert (
        extract_client_ip_from_request(
            request,
            trusted_proxy_networks=frontend_only,
        )
        == "172.31.250.1"
    )


def test_unconfigured_proxy_headers_are_ignored(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    networks = resolve_configured_trusted_proxy_networks()
    request = _Request("172.18.0.4", {"x-forwarded-for": "203.0.113.10"})

    assert networks == []
    assert extract_client_ip_from_request(request, trusted_proxy_networks=networks) == "172.18.0.4"


def test_audit_ip_ignores_forwarded_headers_without_explicit_trust(monkeypatch):
    for name in (
        "TRUST_PROXY_HEADERS",
        "OMLORIX_TRUST_PROXY_HEADERS",
        "TRUSTED_PROXIES",
        "OMLORIX_TRUSTED_PROXIES",
        "AUTH_TRUSTED_PROXIES",
        "RATE_LIMIT_TRUSTED_PROXIES",
    ):
        monkeypatch.delenv(name, raising=False)

    request = _Request("127.0.0.1", {"x-forwarded-for": "203.0.113.66"})

    assert resolve_audit_request_client_ip(request) == "127.0.0.1"


def test_audit_ip_honors_db_configured_trusted_proxies(monkeypatch):
    for name in (
        "TRUST_PROXY_HEADERS",
        "OMLORIX_TRUST_PROXY_HEADERS",
        "TRUSTED_PROXIES",
        "OMLORIX_TRUSTED_PROXIES",
        "AUTH_TRUSTED_PROXIES",
        "RATE_LIMIT_TRUSTED_PROXIES",
    ):
        monkeypatch.delenv(name, raising=False)

    values = {
        ("security", "trust_proxy_headers"): True,
        ("security", "trusted_proxies"): ["10.0.0.0/8"],
    }

    def fake_get_value_by_page_and_key(page, key, db):
        return values.get((page, key))

    fake_settings_utils = ModuleType("app.settings.utils")
    fake_settings_utils.coerce_bool = lambda value, default=False: bool(value)
    fake_settings_utils.get_value_by_page_and_key = fake_get_value_by_page_and_key
    monkeypatch.setitem(__import__("sys").modules, "app.settings.utils", fake_settings_utils)
    request = _Request("10.0.0.5", {"x-forwarded-for": "198.51.100.77"})

    assert resolve_audit_request_client_ip(request, db=object()) == "198.51.100.77"
