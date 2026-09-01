import asyncio
import socket

import pytest

from app.network import outbound_http
from app.network.policy import OutboundRequestBlockedError


def test_resolve_public_tcp_addresses_blocks_connect_time_private_dns(monkeypatch):
    def fake_getaddrinfo(host, port, family, socktype, proto):
        assert host == "rebind.example"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", port),
            )
        ]

    monkeypatch.setattr(outbound_http.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(OutboundRequestBlockedError, match="connected peer IP"):
        outbound_http.resolve_public_tcp_addresses(
            "rebind.example",
            80,
            feature="Direct URL fetch",
            family=socket.AF_INET,
        )


def test_resolve_public_tcp_addresses_returns_checked_public_ips(monkeypatch):
    def fake_getaddrinfo(host, port, family, socktype, proto):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    monkeypatch.setattr(outbound_http.socket, "getaddrinfo", fake_getaddrinfo)

    assert outbound_http.resolve_public_tcp_addresses(
        "example.com",
        443,
        feature="Direct URL fetch",
        family=socket.AF_INET,
    ) == [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )
    ]


def test_policy_request_disables_environment_proxies_and_rechecks_redirects(monkeypatch):
    """The shared provider transport owns redirects and never trusts proxy env."""

    class _Response:
        def __init__(self, status_code, location=None):
            self.status_code = status_code
            self.headers = {"Location": location} if location else {}
            self.closed = False

        @property
        def is_redirect(self):
            return self.status_code in {301, 302, 303, 307, 308}

        @property
        def is_permanent_redirect(self):
            return self.status_code in {308}

        def close(self):
            self.closed = True

    redirect = _Response(302, "https://allowed.example/final")
    success = _Response(200)

    class _Session:
        def __init__(self):
            self.trust_env = True
            self.mounts = []
            self.calls = []
            self.responses = [redirect, success]
            self.closed = False

        def mount(self, prefix, adapter):
            self.mounts.append((prefix, adapter))

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return self.responses.pop(0)

        def close(self):
            self.closed = True

    session = _Session()
    checked_urls = []
    peer_checks = []
    adapter_config = {}
    monkeypatch.setattr(outbound_http.requests, "Session", lambda: session)

    def fake_adapter(**kwargs):
        adapter_config.update(kwargs)
        return object()

    monkeypatch.setattr(
        outbound_http,
        "OutboundPolicyHTTPAdapter",
        fake_adapter,
    )
    monkeypatch.setattr(
        outbound_http,
        "assert_http_url_allowed",
        lambda db, *, url, feature: checked_urls.append((db, url, feature)),
    )
    monkeypatch.setattr(
        outbound_http,
        "assert_outbound_peer_ip_allowed",
        lambda db, **kwargs: peer_checks.append((db, kwargs)),
    )

    database = object()
    response = outbound_http.outbound_policy_web_request(
        database,
        "GET",
        "https://allowed.example/start",
        feature="Provider request",
        params={"q": "images"},
        timeout=10,
    )

    assert response is success
    assert session.trust_env is False
    adapter_config["peer_ip_validator"]("93.184.216.34", "allowed.example", 443)
    assert peer_checks == [
        (
            database,
            {
                "host": "allowed.example",
                "ip_address": "93.184.216.34",
                "port": 443,
                "feature": "Provider request",
            },
        )
    ]
    assert [prefix for prefix, _adapter in session.mounts] == ["http://", "https://"]
    assert checked_urls == [
        (database, "https://allowed.example/start", "Provider request"),
        (database, "https://allowed.example/final", "Provider request"),
    ]
    assert [call[1] for call in session.calls] == [
        "https://allowed.example/start",
        "https://allowed.example/final",
    ]
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert session.calls[0][2]["params"] == {"q": "images"}
    assert "params" not in session.calls[1][2]
    assert redirect.closed is True
    assert session.closed is False

    response.close()
    assert success.closed is True
    assert session.closed is True


def test_policy_connection_rechecks_the_connected_peer(monkeypatch):
    """A peer that differs from the approved DNS address is rejected and closed."""

    monkeypatch.setattr(
        outbound_http,
        "_resolve_tcp_addresses",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            )
        ],
    )

    class _Socket:
        def __init__(self):
            self.closed = False

        def setsockopt(self, *_args):
            return None

        def settimeout(self, _timeout):
            return None

        def bind(self, _source_address):
            return None

        def connect(self, _sockaddr):
            return None

        def getpeername(self):
            return ("169.254.169.254", 443)

        def close(self):
            self.closed = True

    sock = _Socket()
    monkeypatch.setattr(outbound_http.socket, "socket", lambda *_args: sock)

    def validate_peer(ip_address, _host, _port):
        if ip_address == "169.254.169.254":
            raise OutboundRequestBlockedError(
                target=ip_address,
                feature="Provider request",
                policy_mode=outbound_http.OutboundAccessMode.allowlist_only,
                reason="the connected peer is not in the configured allowlist",
            )

    with pytest.raises(OutboundRequestBlockedError, match="connected peer"):
        outbound_http._create_validated_web_connection(
            "allowed.example",
            443,
            feature="Provider request",
            peer_ip_validator=validate_peer,
        )

    assert sock.closed is True


class _FakeAsyncStream:
    def __init__(self, peer_ip: str) -> None:
        self.peer_ip = peer_ip
        self.closed = False

    def get_extra_info(self, name):
        if name == "peername":
            return (self.peer_ip, 443)
        return None

    async def aclose(self):
        self.closed = True


class _FakeAsyncBackend:
    def __init__(self, stream: _FakeAsyncStream) -> None:
        self.stream = stream
        self.connect_calls = []

    async def connect_tcp(self, host, port, **kwargs):
        self.connect_calls.append((host, port, kwargs))
        return self.stream

    async def sleep(self, seconds):
        return None


def test_public_async_network_backend_connects_to_validated_ip(monkeypatch):
    def fake_resolve(host, port, *, feature):
        assert host == "example.com"
        assert port == 443
        assert feature == "MCP HTTP transport"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    stream = _FakeAsyncStream("93.184.216.34")
    backend = _FakeAsyncBackend(stream)
    monkeypatch.setattr(outbound_http, "resolve_public_tcp_addresses", fake_resolve)

    result = asyncio.run(
        outbound_http.PublicAsyncNetworkBackend(
            feature="MCP HTTP transport",
            backend=backend,
        ).connect_tcp("example.com", 443)
    )

    assert result is stream
    assert backend.connect_calls == [
        (
            "93.184.216.34",
            443,
            {"timeout": None, "local_address": None, "socket_options": None},
        )
    ]


def test_public_async_network_backend_blocks_rebound_connected_peer(monkeypatch):
    def fake_resolve(host, port, *, feature):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    stream = _FakeAsyncStream("127.0.0.1")
    backend = _FakeAsyncBackend(stream)
    monkeypatch.setattr(outbound_http, "resolve_public_tcp_addresses", fake_resolve)

    with pytest.raises(OutboundRequestBlockedError, match="connected peer IP"):
        asyncio.run(
            outbound_http.PublicAsyncNetworkBackend(
                feature="MCP HTTP transport",
                backend=backend,
            ).connect_tcp("example.com", 443)
        )

    assert backend.connect_calls[0][0] == "93.184.216.34"
    assert stream.closed is True


def test_public_httpx2_transport_uses_matching_core_backend():
    """MCP v2's httpx2 pool receives a compatible SSRF-safe backend."""
    transport = outbound_http.public_async_httpx2_transport(feature="MCP HTTP transport")
    try:
        network_backend = transport._pool._network_backend
        assert isinstance(network_backend, outbound_http.PublicAsyncNetworkBackend)
        assert network_backend._backend_package == "httpcore2"
    finally:
        asyncio.run(transport.aclose())
