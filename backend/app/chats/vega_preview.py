"""Fetch user-approved Vega preview resources through Omlorix's strict CSP.

The browser deliberately cannot connect to arbitrary origins because the main
application CSP uses a small, static ``connect-src`` allowlist. This module is
the matching server-side fetch boundary: it accepts only public HTTP(S) URLs,
uses the DNS-rebinding-safe transport, rejects redirects, and bounds both time
and response size before returning inert data to Vega's parser.
"""

from __future__ import annotations

import asyncio

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.network.outbound_http import public_async_httpx_transport
from app.network.policy import OutboundRequestBlockedError, assert_public_url_allowed


VEGA_PREVIEW_RESOURCE_MAX_BYTES = 10_000_000
VEGA_PREVIEW_RESOURCE_TIMEOUT_SECONDS = 15.0
VEGA_PREVIEW_RESOURCE_FEATURE = "Vega preview external resource"
VEGA_PREVIEW_RESOURCE_USER_AGENT = "Omlorix-Vega-Preview/1.0"


def _error(status_code: int, code: str) -> HTTPException:
    """Return a stable machine-readable error without exposing fetch details."""

    return HTTPException(status_code=status_code, detail={"code": code})


async def fetch_vega_preview_resource(url: str, db: Session) -> tuple[bytes, str]:
    """Fetch one public Vega resource and return its bytes and media type.

    The outbound policy check covers administrator offline/allowlist settings.
    The custom transport repeats public-IP validation at DNS resolution and
    connect time, closing DNS-rebinding and metadata-service SSRF paths.
    """

    normalized_url = str(url or "").strip()
    try:
        assert_public_url_allowed(
            db,
            url=normalized_url,
            feature=VEGA_PREVIEW_RESOURCE_FEATURE,
        )
    except OutboundRequestBlockedError as exc:
        raise _error(status.HTTP_403_FORBIDDEN, "vega_preview_resource_blocked") from exc

    transport = public_async_httpx_transport(feature=VEGA_PREVIEW_RESOURCE_FEATURE)
    timeout = httpx.Timeout(VEGA_PREVIEW_RESOURCE_TIMEOUT_SECONDS)
    try:
        # HTTPX applies timeouts to individual transport operations. The outer
        # deadline also bounds a slow stream that keeps yielding bytes before
        # each individual operation can time out.
        async with asyncio.timeout(VEGA_PREVIEW_RESOURCE_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "GET",
                    normalized_url,
                    headers={
                        "Accept": "application/json, text/csv, text/tab-separated-values, text/plain, */*;q=0.5",
                        "User-Agent": VEGA_PREVIEW_RESOURCE_USER_AGENT,
                    },
                ) as response:
                    if response.is_redirect:
                        raise _error(status.HTTP_400_BAD_REQUEST, "vega_preview_resource_redirect_blocked")
                    if response.status_code < 200 or response.status_code >= 300:
                        raise _error(status.HTTP_502_BAD_GATEWAY, "vega_preview_resource_upstream_failed")

                    declared_length = response.headers.get("content-length")
                    if declared_length:
                        try:
                            if int(declared_length) > VEGA_PREVIEW_RESOURCE_MAX_BYTES:
                                raise _error(status.HTTP_413_CONTENT_TOO_LARGE, "vega_preview_resource_too_large")
                        except ValueError:
                            # Invalid upstream metadata is ignored; the streamed
                            # byte counter below remains the authoritative limit.
                            pass

                    chunks: list[bytes] = []
                    received = 0
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > VEGA_PREVIEW_RESOURCE_MAX_BYTES:
                            raise _error(status.HTTP_413_CONTENT_TOO_LARGE, "vega_preview_resource_too_large")
                        chunks.append(chunk)

                    media_type = str(response.headers.get("content-type") or "application/octet-stream")
                    return b"".join(chunks), media_type
    except HTTPException:
        raise
    except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
        raise _error(status.HTTP_504_GATEWAY_TIMEOUT, "vega_preview_resource_timeout") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise _error(status.HTTP_502_BAD_GATEWAY, "vega_preview_resource_unavailable") from exc
