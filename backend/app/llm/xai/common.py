"""Shared HTTP helpers for xAI's native media and voice endpoints."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from app.llm.base_settings import LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS
from app.llm.models import LLMProvider
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.llm.xai.schemas import XAI_DEFAULT_BASE_URL
from app.network.outbound_http import public_web_request

XAI_USD_TICKS_PER_DOLLAR = 10_000_000_000
XAI_RESULT_MAX_REDIRECTS = 5


def xai_base_url(provider: LLMProvider) -> str:
    """Return the configured xAI API root without a trailing slash."""
    settings = provider.settings if isinstance(provider.settings, dict) else {}
    return str(settings.get("base_url") or XAI_DEFAULT_BASE_URL).strip().rstrip("/")


def xai_timeout() -> int:
    """Return the fixed request timeout shared by all LLM providers."""

    return LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS


def xai_headers(
    provider: LLMProvider,
    *,
    include_content_type: bool = True,
) -> dict[str, str]:
    """Build xAI request headers while preserving configured custom headers."""
    headers: dict[str, str] = {
        "Authorization": f"Bearer {str(provider.api_key or '').strip()}",
    }
    if include_content_type:
        headers["Content-Type"] = "application/json"
    settings = provider.settings if isinstance(provider.settings, dict) else {}
    headers.update(custom_headers_to_dict(settings.get("custom_headers")))
    return headers


def xai_error_detail(response: Any) -> str:
    """Extract a useful provider error without including request credentials."""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "detail", "type"):
                value = str(error.get(key) or "").strip()
                if value:
                    return value
        for key in ("message", "detail"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return f"HTTP {response.status_code}"


def require_xai_success(response: Any, operation: str) -> None:
    """Raise a stable error for either requests or httpx responses."""
    success = getattr(response, "ok", None)
    if success is None:
        success = getattr(response, "is_success", False)
    if success:
        return
    raise RuntimeError(f"xAI {operation} failed: {xai_error_detail(response)}")


def download_xai_result_url(
    url: str,
    *,
    operation: str,
    expected_content_prefix: str,
    max_bytes: int,
    timeout: int,
    authorized_hosts: set[str] | None = None,
    authorized_headers: dict[str, str] | None = None,
) -> tuple[bytes, str, str]:
    """Securely download a temporary xAI media URL.

    xAI returns generated media through temporary URLs, so those URLs cross a
    second outbound trust boundary after the authenticated generation call.
    Every redirect is therefore validated independently and every connection
    uses Omlorix's public-peer-pinning transport. Authorization is sent only to
    explicitly named API hosts and is never forwarded to a CDN redirect.
    """

    if max_bytes <= 0:
        raise ValueError("xAI result download limit must be positive")

    current_url = str(url or "").strip()
    normalized_authorized_hosts = {
        str(host or "").strip().lower()
        for host in (authorized_hosts or set())
        if str(host or "").strip()
    }

    for _redirect_count in range(XAI_RESULT_MAX_REDIRECTS + 1):
        parsed = urlparse(current_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise RuntimeError(f"xAI returned an invalid {operation} URL")

        request_headers = None
        if parsed.hostname.lower() in normalized_authorized_hosts:
            request_headers = dict(authorized_headers or {}) or None

        response = public_web_request(
            "GET",
            current_url,
            feature=f"xAI {operation} download",
            allow_redirects=False,
            headers=request_headers,
            stream=True,
            timeout=timeout,
        )
        try:
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError(
                        f"xAI {operation} redirect did not include a destination"
                    )
                current_url = urljoin(current_url, location)
                continue

            require_xai_success(response, f"{operation} download")
            content_type = (
                str(response.headers.get("content-type") or "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if not content_type.startswith(expected_content_prefix):
                raise RuntimeError(
                    f"xAI {operation} download returned an unexpected content type"
                )

            declared_length = response.headers.get("content-length")
            if declared_length:
                try:
                    if int(declared_length) > max_bytes:
                        raise RuntimeError(
                            f"xAI {operation} download exceeded the size limit"
                        )
                except ValueError:
                    # A malformed Content-Length is not trusted. The streamed
                    # byte counter below remains authoritative.
                    pass

            chunks: list[bytes] = []
            downloaded_bytes = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                downloaded_bytes += len(chunk)
                if downloaded_bytes > max_bytes:
                    raise RuntimeError(
                        f"xAI {operation} download exceeded the size limit"
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content:
                raise RuntimeError(f"xAI {operation} download returned an empty payload")
            return content, content_type, current_url
        finally:
            response.close()

    raise RuntimeError(f"xAI {operation} download exceeded the redirect limit")


def _xai_usage_value(usage: Any, key: str) -> Any:
    """Read a usage field from REST dictionaries or OpenAI SDK models."""

    if isinstance(usage, dict):
        return usage.get(key)
    value = getattr(usage, key, None)
    if value is not None:
        return value
    model_extra = getattr(usage, "model_extra", None)
    if isinstance(model_extra, dict):
        return model_extra.get(key)
    return None


def xai_cost_from_usage_object(usage: Any) -> tuple[float, dict[str, Any]]:
    """Convert an xAI usage object's exact billed USD ticks to dollars."""

    try:
        ticks = max(int(_xai_usage_value(usage, "cost_in_usd_ticks") or 0), 0)
    except (TypeError, ValueError):
        ticks = 0
    if not ticks:
        return 0.0, {}
    cost = ticks / XAI_USD_TICKS_PER_DOLLAR
    return cost, {
        "cost_in_usd_ticks": ticks,
        "currency": "USD",
        "pricing_source": "provider_usage",
    }


def xai_cost_from_usage(payload: Any) -> tuple[float, dict[str, Any]]:
    """Read and convert xAI cost data from a complete REST response payload."""

    if not isinstance(payload, dict):
        return 0.0, {}
    return xai_cost_from_usage_object(payload.get("usage"))
