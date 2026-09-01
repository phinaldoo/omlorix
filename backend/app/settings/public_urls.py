"""Validation and normalization helpers for configured public application URLs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


PUBLIC_URL_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MISSING_SCHEME_COLON_RE = re.compile(r"^(https?)//", re.IGNORECASE)


def _normalize_origin_components(*, scheme: str, host: str, port: int | None) -> str:
    """Build a normalized origin while omitting each scheme's default port."""
    # ``urlparse().hostname`` removes brackets from IPv6 literals. Restore them
    # before rebuilding the origin so the result remains a valid URL.
    origin_host = f"[{host}]" if ":" in host else host
    if port and ((scheme == "https" and port != 443) or (scheme == "http" and port != 80)):
        return f"{scheme}://{origin_host}:{port}"
    return f"{scheme}://{origin_host}"


def normalize_public_url(value: Any) -> str:
    """Validate one public URL and return its scheme-and-host origin."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Public URL is required")

    raw_value = value.strip()
    # URL fields are commonly pasted as ``https//host`` with the scheme colon
    # omitted. This correction is narrow and unambiguous, and the repaired URL
    # still passes all normal scheme, host, port, and credential validation.
    raw_value = _MISSING_SCHEME_COLON_RE.sub(r"\1://", raw_value)
    if len(raw_value) > 2048:
        raise ValueError("Public URL must be 2048 characters or fewer.")

    try:
        parsed = urlparse(raw_value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Public URL must use a valid host and port.") from exc

    scheme = (parsed.scheme or "").strip().lower()
    host = (parsed.hostname or "").strip().lower()
    if scheme not in PUBLIC_URL_ALLOWED_SCHEMES or not host:
        raise ValueError("Public URL must be an absolute http(s) URL.")
    if parsed.username or parsed.password:
        raise ValueError("Public URL must not include credentials.")

    return _normalize_origin_components(scheme=scheme, host=host, port=port)


def normalize_public_urls(value: Any, *, allow_empty: bool = False) -> list[str]:
    """Normalize a public URL collection, accepting a legacy scalar string.

    The order is significant: the first URL remains the canonical base used for
    server-generated links when no request-specific configured origin is known.
    """
    if value is None or value == "":
        candidates: list[Any] = []
    elif isinstance(value, str):
        # Existing installations stored this setting as a scalar. Accepting it
        # here provides a safe, automatic transition to the list representation.
        candidates = [value]
    elif isinstance(value, (list, tuple)):
        candidates = list(value)
    else:
        raise ValueError("Public URLs must be a list of strings.")

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if not candidate:
                continue
        public_url = normalize_public_url(candidate)
        if public_url in seen:
            continue
        seen.add(public_url)
        normalized.append(public_url)

    if not normalized and not allow_empty:
        raise ValueError("At least one public URL is required")
    return normalized


def primary_public_url(value: Any, *, allow_empty: bool = False) -> str:
    """Return the primary (first) normalized public URL from a stored value."""
    public_urls = normalize_public_urls(value, allow_empty=allow_empty)
    return public_urls[0] if public_urls else ""
