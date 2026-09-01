import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.chats import vega_preview
from app.chats.schemas import VegaPreviewResourceRequest
from app.network.policy import OutboundAccessMode, OutboundRequestBlockedError


def _run_fetch(monkeypatch, handler):
    """Run the fetcher with an in-memory transport and an allowed policy."""

    monkeypatch.setattr(
        vega_preview,
        "public_async_httpx_transport",
        lambda **_kwargs: httpx.MockTransport(handler),
    )
    monkeypatch.setattr(vega_preview, "assert_public_url_allowed", lambda *_args, **_kwargs: None)
    return asyncio.run(
        vega_preview.fetch_vega_preview_resource(
            "https://data.example/cars.json",
            object(),
        )
    )


def test_vega_preview_resource_returns_bounded_remote_data(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://data.example/cars.json"
        assert request.headers["user-agent"] == vega_preview.VEGA_PREVIEW_RESOURCE_USER_AGENT
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'[{"name":"car"}]',
        )

    content, content_type = _run_fetch(monkeypatch, handler)

    assert content == b'[{"name":"car"}]'
    assert content_type == "application/json"


def test_vega_preview_resource_rejects_redirects_and_large_responses(monkeypatch):
    def redirect_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://other.example/data.json"})

    with pytest.raises(HTTPException) as redirect_error:
        _run_fetch(monkeypatch, redirect_handler)
    assert redirect_error.value.status_code == 400
    assert redirect_error.value.detail == {"code": "vega_preview_resource_redirect_blocked"}

    def large_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": str(vega_preview.VEGA_PREVIEW_RESOURCE_MAX_BYTES + 1)},
            content=b"",
        )

    with pytest.raises(HTTPException) as size_error:
        _run_fetch(monkeypatch, large_handler)
    assert size_error.value.status_code == 413
    assert size_error.value.detail == {"code": "vega_preview_resource_too_large"}


def test_vega_preview_resource_maps_network_policy_failures(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise OutboundRequestBlockedError(
            target="http://127.0.0.1/private.json",
            feature=vega_preview.VEGA_PREVIEW_RESOURCE_FEATURE,
            policy_mode=OutboundAccessMode.allow_all,
            reason="resolved peer address is private",
        )

    monkeypatch.setattr(vega_preview, "assert_public_url_allowed", blocked)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            vega_preview.fetch_vega_preview_resource(
                "http://127.0.0.1/private.json",
                object(),
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"code": "vega_preview_resource_blocked"}


def test_vega_preview_resource_enforces_a_wall_clock_stream_deadline(monkeypatch):
    """A slow upstream cannot keep the bounded streaming request alive forever."""

    async def slow_handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=b"[]")

    monkeypatch.setattr(vega_preview, "VEGA_PREVIEW_RESOURCE_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(
        vega_preview,
        "public_async_httpx_transport",
        lambda **_kwargs: httpx.MockTransport(slow_handler),
    )
    monkeypatch.setattr(vega_preview, "assert_public_url_allowed", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(vega_preview.fetch_vega_preview_resource("https://data.example/slow.json", object()))

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == {"code": "vega_preview_resource_timeout"}


def test_vega_preview_proxy_route_returns_no_store_data(monkeypatch):
    from app.chats import router as chats_router

    async def fake_fetch(url, _db):
        assert url == "https://data.example/cars.json"
        return b"[]", "application/json"

    monkeypatch.setattr(chats_router, "fetch_vega_preview_resource", fake_fetch)
    response = asyncio.run(
        chats_router.proxy_vega_preview_resource(
            VegaPreviewResourceRequest(url="https://data.example/cars.json"),
            db=object(),
            _user=object(),
        )
    )

    assert response.body == b"[]"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"
