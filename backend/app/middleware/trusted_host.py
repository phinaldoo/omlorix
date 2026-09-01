"""Host-header validation with explicit private-network opt-in support."""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from starlette.datastructures import Headers, URL
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send


_DOMAIN_WILDCARD_ERROR = "Domain wildcard patterns must be like '*.example.com'."
_HEALTH_CHECK_PATHS = frozenset({"/health", "/healthz", "/ready"})


def _hostname_from_host_header(host_header: str) -> str | None:
    """Extract and normalize a hostname from an HTTP Host header.

    IPv6 literals must use the bracketed form required by HTTP, for example
    ``[fd00::1]:8443``. Ports are validated but intentionally discarded because
    host trust applies to the hostname or IP address, matching Starlette's
    TrustedHostMiddleware behavior.
    """

    value = str(host_header or "")
    if not value or value != value.strip():
        return None

    if value.startswith("["):
        closing_bracket = value.find("]")
        if closing_bracket <= 1:
            return None
        hostname = value[1:closing_bracket]
        port_suffix = value[closing_bracket + 1 :]
        if port_suffix:
            if not port_suffix.startswith(":") or not _is_valid_port(port_suffix[1:]):
                return None
    else:
        # Unbracketed Host values may contain at most one colon, which separates
        # the optional port. Multiple colons indicate a malformed IPv6 literal.
        if value.count(":") > 1:
            return None
        hostname, separator, port = value.partition(":")
        if separator and not _is_valid_port(port):
            return None

    # Whitespace is never valid inside the Host grammar. Reject it instead of
    # normalizing it away so malformed values cannot acquire a trusted form.
    if not hostname or hostname != hostname.strip() or any(character.isspace() for character in hostname):
        return None
    normalized = hostname.rstrip(".").lower()
    if any(character in normalized for character in ("/", "\\", "@", "%")):
        return None
    return normalized


def _is_valid_port(value: str) -> bool:
    """Return whether a Host-header port is a valid numeric TCP port."""

    # Host ports use the ASCII ``DIGIT`` grammar. Avoid ``int(value)`` here:
    # Python deliberately rejects extremely long decimal strings, and a Host
    # header is attacker-controlled input handled before authentication.
    if not value or not value.isascii() or not value.isdigit():
        return False

    # Compare the normalized decimal representation without integer parsing.
    # Leading zeroes remain compatible with the former behavior while a port
    # made entirely of zeroes is still rejected.
    normalized = value.lstrip("0")
    if not normalized:
        return False
    return len(normalized) < 5 or (
        len(normalized) == 5 and normalized <= "65535"
    )


def _is_local_or_private_hostname(hostname: str) -> bool:
    """Return whether a hostname is localhost or a literal private/loopback IP.

    Hostnames are deliberately not resolved through DNS. Trusting a hostname
    merely because it currently resolves to a private address would make the
    policy dependent on mutable DNS state and weaken protection against Host
    header and DNS-rebinding attacks.
    """

    if hostname == "localhost":
        return True

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


class LocalOrPrivateTrustedHostMiddleware:
    """Validate Host headers while optionally accepting private IP literals.

    Configured hostnames retain the exact and ``*.example.com`` matching
    behavior of Starlette's TrustedHostMiddleware. When
    ``allow_local_or_private_hosts`` is enabled, only localhost and literal
    private/loopback IP addresses receive the additional trust; arbitrary DNS
    names and public IP literals still require an explicit allowed-host entry.

    Container probes are the narrow exception: the status-only health endpoints
    accept local/private Host values without opening any other application
    route. Docker probes use ``localhost`` or a private container IP that cannot
    be known when the allowlist is built.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: Sequence[str] | None = None,
        allow_local_or_private_hosts: bool = False,
        www_redirect: bool = True,
    ) -> None:
        if allowed_hosts is None:
            allowed_hosts = ["*"]

        for pattern in allowed_hosts:
            assert "*" not in pattern[1:], _DOMAIN_WILDCARD_ERROR
            if pattern.startswith("*") and pattern != "*":
                assert pattern.startswith("*."), _DOMAIN_WILDCARD_ERROR

        self.app = app
        self.allowed_hosts = [pattern.lower().rstrip(".") for pattern in allowed_hosts]
        self.allow_any = "*" in self.allowed_hosts
        self.allow_local_or_private_hosts = allow_local_or_private_hosts
        self.www_redirect = www_redirect

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Validate HTTP and WebSocket Host headers before dispatching."""

        if self.allow_any or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        hostname = _hostname_from_host_header(Headers(scope=scope).get("host", ""))
        is_valid_host = bool(hostname) and self._matches_configured_host(hostname)
        is_internal_health_check = (
            scope["type"] == "http"
            and scope.get("path") in _HEALTH_CHECK_PATHS
            and bool(hostname)
            and _is_local_or_private_hostname(hostname)
        )
        if (
            not is_valid_host
            and hostname
            and (
                is_internal_health_check
                or (
                    self.allow_local_or_private_hosts
                    and _is_local_or_private_hostname(hostname)
                )
            )
        ):
            is_valid_host = True

        if is_valid_host:
            await self.app(scope, receive, send)
            return

        response: Response
        if hostname and self.www_redirect and self._matches_www_redirect(hostname):
            url = URL(scope=scope)
            response = RedirectResponse(url=str(url.replace(netloc="www." + url.netloc)))
        else:
            response = PlainTextResponse("Invalid host header", status_code=400)
        await response(scope, receive, send)

    def _matches_configured_host(self, hostname: str) -> bool:
        """Match a normalized hostname against exact and wildcard entries."""

        return any(
            hostname == pattern or (pattern.startswith("*.") and hostname.endswith(pattern[1:]))
            for pattern in self.allowed_hosts
        )

    def _matches_www_redirect(self, hostname: str) -> bool:
        """Return whether the configured host list requests a ``www`` redirect."""

        return f"www.{hostname}" in self.allowed_hosts
