# Shared aiohttp scraper. Keep the return contract stable and verify callers before changing behavior.
import asyncio
from collections.abc import Callable
import logging
import socket
from typing import Any

import aiohttp
from bs4 import BeautifulSoup
from fastapi import HTTPException
import ssl
import certifi
from yarl import URL

from app.network.outbound_http import resolve_public_tcp_addresses
from app.network.policy import OutboundRequestBlockedError
from app.tools.websearch.schemas import DEFAULT_USER_AGENT


logger = logging.getLogger(__name__)


def get_ssl_context(verify_ssl: bool = True) -> ssl.SSLContext | bool:
    """Return an SSL context based on the verify_ssl flag."""
    if verify_ssl:
        # Secure SSL context using certifi CA bundle
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        return ssl_context
    else:
        # Disable SSL verification (insecure)
        return False


class PublicWebResolver(aiohttp.abc.AbstractResolver):
    def __init__(
        self,
        *,
        feature: str,
        resolved_ip_validator: Callable[[str], None] | None = None,
    ) -> None:
        self._feature = feature
        self._resolved_ip_validator = resolved_ip_validator

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        try:
            addresses = await loop.run_in_executor(
                None,
                lambda: resolve_public_tcp_addresses(
                    host,
                    port,
                    feature=self._feature,
                    family=family,
                ),
            )
        except OutboundRequestBlockedError as exc:
            raise exc.to_http_exception() from exc

        records = []
        for af, _socktype, proto, _canonname, sockaddr in addresses:
            resolved_ip = str(sockaddr[0] or "").strip()
            if resolved_ip and self._resolved_ip_validator is not None:
                self._resolved_ip_validator(resolved_ip)
            records.append(
                {
                    "hostname": host,
                    "host": resolved_ip,
                    "port": sockaddr[1] if len(sockaddr) > 1 else port,
                    "family": af,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        return records

    async def close(self) -> None:
        return None


class PolicyCheckedResolver(PublicWebResolver):
    def __init__(self, resolved_ip_validator: Callable[[str], None]) -> None:
        super().__init__(
            feature="Direct web scrape",
            resolved_ip_validator=resolved_ip_validator,
        )


async def fetch(
    session: aiohttp.ClientSession,
    url: str,
    *,
    verify_ssl: bool = True,
    timeout: int = 10,
    view_raw: bool = False,
    url_validator: Callable[[str], None] | None = None,
    max_redirects: int = 10,
) -> dict[str, Any]:
    """Fetch *url* returning visible text or raising on failure."""

    ssl_context = get_ssl_context(verify_ssl)
    current_url = url
    for _ in range(max_redirects + 1):
        if url_validator is not None:
            url_validator(current_url)

        try:
            response_context = session.get(
                current_url,
                allow_redirects=False,
                ssl=ssl_context,
                timeout=timeout,
            )
            async with response_context as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        response.raise_for_status()
                        break
                    current_url = str(response.url.join(URL(location)))
                    continue

                response.raise_for_status()
                html = await response.text()

                if view_raw:
                    return {"url": url, "html": html}

                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                clean_text = soup.get_text(separator=" ", strip=True)
                return {"url": url, "content": clean_text}
        except OutboundRequestBlockedError as exc:
            raise exc.to_http_exception() from exc

    raise HTTPException(status_code=400, detail="Too many redirects")


def _failed_page_result(url: str, exc: BaseException) -> dict[str, Any]:
    """Build a model-visible result for one page that could not be fetched."""

    status_code = exc.status if isinstance(exc, aiohttp.ClientResponseError) else None
    if isinstance(exc, asyncio.TimeoutError):
        error = "Request timed out"
    elif status_code is not None:
        message = str(getattr(exc, "message", "") or "Request failed").strip()
        error = f"HTTP {status_code}: {message}"
    else:
        error = str(exc).strip() or type(exc).__name__

    result: dict[str, Any] = {
        "url": url,
        "content": None,
        "title": None,
        "error": error,
        "failed": True,
    }
    if status_code is not None:
        result["status_code"] = status_code
    return result


def _find_policy_http_exception(exc: BaseException) -> HTTPException | None:
    """Return a wrapped policy exception so it remains fatal to the request."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HTTPException):
            return current
        current = current.__cause__ or current.__context__
    return None


async def _fetch_with_failure_result(
    session: aiohttp.ClientSession,
    url: str,
    *,
    verify_ssl: bool,
    timeout: int,
    view_raw: bool,
    url_validator: Callable[[str], None] | None,
) -> dict[str, Any]:
    """Fetch one URL while converting expected remote failures into result entries."""

    try:
        return await fetch(
            session,
            url,
            verify_ssl=verify_ssl,
            timeout=timeout,
            view_raw=view_raw,
            url_validator=url_validator,
        )
    except HTTPException:
        # URL and resolved-IP policy failures must still stop the request.
        raise
    except asyncio.CancelledError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        policy_exception = _find_policy_http_exception(exc)
        if policy_exception is not None:
            raise policy_exception from exc
        failure_result = _failed_page_result(url, exc)
        logger.warning("Aiohttp scrape failed for %s: %s", url, failure_result["error"])
        return failure_result


async def _aiohttp_scrape_urls(
    urls: list[str],
    verify_ssl: bool = True,
    timeout: int = 10,
    view_raw: bool = False,
    url_validator: Callable[[str], None] | None = None,
    resolved_ip_validator: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    connector = aiohttp.TCPConnector(
        resolver=PublicWebResolver(
            feature="Direct web scrape",
            resolved_ip_validator=resolved_ip_validator,
        )
    )
    async with aiohttp.ClientSession(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        connector=connector,
    ) as session:
        tasks = [
            _fetch_with_failure_result(
                session,
                u,
                verify_ssl=verify_ssl,
                timeout=timeout,
                view_raw=view_raw,
                url_validator=url_validator,
            )
            for u in urls
        ]
        return await asyncio.gather(*tasks)


def aiohttp_scrape_urls(
    urls: list[str],
    verify_ssl: bool = True,
    timeout: int = 10,
    view_raw: bool = False,
    url_validator: Callable[[str], None] | None = None,
    resolved_ip_validator: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    has_running_loop = False
    try:
        asyncio.get_running_loop()
        has_running_loop = True
    except RuntimeError:
        has_running_loop = False

    if has_running_loop:
        import threading

        result = None
        exception = None

        def _worker():
            nonlocal result, exception
            try:
                result = asyncio.run(
                    _aiohttp_scrape_urls(
                        urls,
                        verify_ssl=verify_ssl,
                        timeout=timeout,
                        view_raw=view_raw,
                        url_validator=url_validator,
                        resolved_ip_validator=resolved_ip_validator,
                    )
                )
            except Exception as e:
                exception = e

        t = threading.Thread(target=_worker)
        t.start()
        t.join()

        if exception:
            raise exception

        return result or []

    return asyncio.run(
        _aiohttp_scrape_urls(
            urls,
            verify_ssl=verify_ssl,
            timeout=timeout,
            view_raw=view_raw,
            url_validator=url_validator,
            resolved_ip_validator=resolved_ip_validator,
        )
    )
