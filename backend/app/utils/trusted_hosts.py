import logging
import os
import re
from urllib.parse import urlparse
from typing import Any


logger = logging.getLogger(__name__)

TRUSTED_HOSTS_ENV = "TRUSTED_HOSTS"
PUBLIC_URL_ENV = "PUBLIC_URL"

_NON_PRODUCTION_MODES = frozenset({"dev", "development", "local", "test"})
_LOOPBACK_TRUSTED_HOSTS = ("localhost", "127.0.0.1")
_TEST_TRUSTED_HOSTS = ("testserver",)
_BOOTSTRAP_TRUSTED_HOSTS = (*_LOOPBACK_TRUSTED_HOSTS, *_TEST_TRUSTED_HOSTS)
_HOST_PATTERN_RE = re.compile(r"(?:\*\.)?[a-z0-9.-]+")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _split_trusted_hosts(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _normalize_host_pattern(value: str | None) -> str | None:
    raw_value = str(value or "").strip().lower()
    if not raw_value:
        return None
    if raw_value == "*":
        return raw_value

    try:
        if "://" in raw_value:
            parsed = urlparse(raw_value)
        else:
            parsed = urlparse(f"//{raw_value}")
    except ValueError:
        return None

    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        return None
    if parsed.username or parsed.password:
        return None

    host = parsed.hostname or raw_value
    host = host.strip().rstrip(".")
    if not host:
        return None
    if host.startswith("[") or ":" in host:
        logger.warning(
            "Ignoring IPv6 trusted host %r because Starlette TrustedHostMiddleware does not support IPv6 host patterns.",
            raw_value,
        )
        return None
    if "*" in host and not host.startswith("*."):
        return None
    if not _HOST_PATTERN_RE.fullmatch(host):
        return None
    if host.startswith("*.") and len(host) <= 2:
        return None
    return host


def load_trusted_hosts(
    *,
    public_url_candidates: list[Any] | None = None,
    mode: str | None = None,
    allow_any_if_unconfigured: bool = True,
) -> list[str]:
    """Build TrustedHostMiddleware allowed hosts from env and public URL settings.

    A confirmed empty configuration is the first-run bootstrap state, where
    every Host value must work so setup is reachable through any IP or domain.
    Callers that could not read settings pass ``allow_any_if_unconfigured=False``
    to keep the storage-failure path fail-closed.
    """

    candidates = _split_trusted_hosts(os.getenv(TRUSTED_HOSTS_ENV))
    candidates.append(os.getenv(PUBLIC_URL_ENV))
    for candidate in public_url_candidates or []:
        # Accept both the canonical flat list and legacy/nested values supplied
        # by callers during the scalar-to-list settings transition.
        if isinstance(candidate, (list, tuple, set)):
            candidates.extend(candidate)
        else:
            candidates.append(candidate)

    trusted_hosts = _dedupe(
        [host for host in (_normalize_host_pattern(candidate) for candidate in candidates) if host]
    )
    if trusted_hosts:
        # A configured list is an operator-defined security boundary. Keep it
        # exact: local/private access is handled separately by the middleware's
        # explicit ALLOW_LOCAL_OR_PRIVATE_ORIGINS opt-in.
        return trusted_hosts

    if allow_any_if_unconfigured:
        logger.warning(
            "No TRUSTED_HOSTS, PUBLIC_URL, or general.public_url configured; "
            "all HTTP hosts will be accepted until a public URL is configured."
        )
        return ["*"]

    normalized_mode = (
        str(mode if mode is not None else os.getenv("MODE", "production") or "production")
        .strip()
        .lower()
    )
    if normalized_mode in _NON_PRODUCTION_MODES:
        return list(_BOOTSTRAP_TRUSTED_HOSTS)

    logger.warning(
        "No TRUSTED_HOSTS, PUBLIC_URL, or general.public_url configured; all HTTP hosts will be rejected."
    )
    return []
