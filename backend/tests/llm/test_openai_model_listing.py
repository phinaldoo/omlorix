"""Regression coverage for filtering removed OpenAI models from discovery."""

import socket
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.llm.openai import utils as openai_utils
from app.network import outbound_http, policy as outbound_policy
from app.network.policy import OutboundAccessMode, OutboundPolicySnapshot


def _disable_openai_retries(monkeypatch):
    original_resolve_context = openai_utils._resolve_openai_client_context

    def resolve_without_retries(*args, **kwargs):
        context = original_resolve_context(*args, **kwargs)
        context["client_kwargs"]["max_retries"] = 0
        return context

    monkeypatch.setattr(
        openai_utils,
        "_resolve_openai_client_context",
        resolve_without_retries,
    )


def test_model_listing_excludes_removed_deep_research_models(monkeypatch):
    """Do not surface removed native Deep Research models from ``/models``."""
    client_closed = []

    class FakeModelsAPI:
        """Return a mix of supported and removed model identifiers."""

        @staticmethod
        def list(**_kwargs):
            return [
                SimpleNamespace(id="gpt-5.6", created=1, object="model", owned_by="openai"),
                SimpleNamespace(id="o3-deep-research", created=2, object="model", owned_by="openai"),
                SimpleNamespace(id="o4-mini-deep-research-2025-06-26", created=3, object="model", owned_by="openai"),
            ]

    class FakeOpenAI:
        """Minimal client surface used by ``list_models_openai``."""

        def __init__(self, **_kwargs):
            self.models = FakeModelsAPI()

        def close(self):
            client_closed.append(True)

    monkeypatch.setattr(
        openai_utils,
        "_resolve_openai_client_context",
        lambda *_args, **_kwargs: {"client_kwargs": {}, "request_options": {}},
    )
    monkeypatch.setattr(openai_utils, "OpenAI", FakeOpenAI)

    discovered_models = openai_utils.list_models_openai(db=object())

    assert [model["id"] for model in discovered_models] == ["gpt-5.6"]
    assert client_closed == [True]


def test_model_listing_maps_connection_failure_without_status_to_bad_gateway(monkeypatch):
    """An unreachable compatible provider must produce a stable HTTP error."""

    class FakeAPIConnectionError(Exception):
        status_code = None
        response = None

    class FakeModelsAPI:
        @staticmethod
        def list(**_kwargs):
            raise FakeAPIConnectionError("Connection refused")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.models = FakeModelsAPI()

    monkeypatch.setattr(
        openai_utils,
        "_resolve_openai_client_context",
        lambda *_args, **_kwargs: {"client_kwargs": {}, "request_options": {}},
    )
    monkeypatch.setattr(openai_utils, "APIConnectionError", FakeAPIConnectionError)
    monkeypatch.setattr(openai_utils, "OpenAI", FakeOpenAI)

    with pytest.raises(HTTPException) as exc_info:
        openai_utils.list_models_openai(db=object())

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Failed to list OpenAI models: Connection refused"


def test_byok_model_listing_blocks_dns_rebinding_before_connect(monkeypatch):
    """The real OpenAI SDK sink must not connect after DNS changes to loopback."""

    monkeypatch.setattr(
        outbound_policy,
        "get_outbound_policy_snapshot",
        lambda _db: OutboundPolicySnapshot(
            offline_mode=False,
            mode=OutboundAccessMode.allow_all,
            allowlist=(),
        ),
    )
    # The URL-level check sees a public address. The transport's independent
    # connection-time lookup below models the subsequent rebound response.
    monkeypatch.setattr(
        outbound_policy,
        "_resolve_host_ips",
        lambda hostname: ("93.184.216.34",) if hostname == "rebind.example" else (),
    )

    def rebound_getaddrinfo(host, port, family, socktype, proto):
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

    monkeypatch.setattr(outbound_http.socket, "getaddrinfo", rebound_getaddrinfo)
    connect_calls = []

    class _Backend:
        def connect_tcp(self, *args, **kwargs):
            connect_calls.append((args, kwargs))
            raise OSError("unexpected connection")

    monkeypatch.setattr(
        outbound_http.PolicySyncNetworkBackend,
        "_network_backend",
        lambda _self: _Backend(),
    )

    _disable_openai_retries(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        openai_utils.list_models_openai(
            object(),
            byok={
                "api_key": "sk-test",
                "base_url": "http://rebind.example:8000/v1",
            },
        )

    assert exc_info.value.status_code == 502
    assert connect_calls == []


def test_byok_model_listing_blocks_redirect_to_private_target(monkeypatch):
    """The OpenAI SDK must reapply BYOK policy before following a redirect."""
    import httpx2

    monkeypatch.setattr(
        outbound_policy,
        "get_outbound_policy_snapshot",
        lambda _db: OutboundPolicySnapshot(
            offline_mode=False,
            mode=OutboundAccessMode.allow_all,
            allowlist=(),
        ),
    )
    monkeypatch.setattr(
        outbound_policy,
        "_resolve_host_ips",
        lambda hostname: ("93.184.216.34",)
        if hostname == "attacker.example"
        else (),
    )

    sent_urls = []

    def redirect_to_internal(request):
        sent_urls.append(str(request.url))
        return httpx2.Response(
            307,
            headers={"location": "http://127.0.0.1:8000/v1/models"},
            request=request,
        )

    transport = httpx2.MockTransport(redirect_to_internal)
    transport._pool = SimpleNamespace(_network_backend=None)
    monkeypatch.setattr(httpx2, "HTTPTransport", lambda **_kwargs: transport)

    _disable_openai_retries(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        openai_utils.list_models_openai(
            object(),
            byok={
                "api_key": "sk-test",
                "base_url": "https://attacker.example/v1",
            },
        )

    assert exc_info.value.status_code == 502
    assert sent_urls == ["https://attacker.example/v1/models"]
