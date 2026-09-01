"""Helpers for Firecrawl proxy-mode settings."""

from typing import Final, Literal, cast

FIRECRAWL_HOSTED_BASE_URL: Final = "https://api.firecrawl.dev"
FIRECRAWL_DEFAULT_PROXY_MODE: Final = "auto"
FIRECRAWL_PROXY_MODES: Final = frozenset({"auto", "basic", "enhanced"})

FirecrawlProxyMode = Literal["auto", "basic", "enhanced"]


def normalize_firecrawl_base_url(base_url: object) -> str:
    """Return a trimmed Firecrawl base URL, falling back to the hosted API."""

    candidate = str(base_url or "").strip()
    return (candidate or FIRECRAWL_HOSTED_BASE_URL).rstrip("/")


def normalize_firecrawl_proxy_mode(proxy: object) -> FirecrawlProxyMode:
    """Normalize unsupported proxy values to Firecrawl's safe default."""

    candidate = str(proxy or FIRECRAWL_DEFAULT_PROXY_MODE).strip().lower()
    if candidate not in FIRECRAWL_PROXY_MODES:
        return FIRECRAWL_DEFAULT_PROXY_MODE
    return cast(FirecrawlProxyMode, candidate)
