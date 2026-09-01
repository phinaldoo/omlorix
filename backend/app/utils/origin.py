import ipaddress
import logging
import os
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.settings.utils import coerce_bool, get_value_by_page_and_key
from app.settings.public_urls import normalize_public_urls


logger = logging.getLogger(__name__)
ALLOW_LOCAL_OR_PRIVATE_ORIGINS_ENV = "ALLOW_LOCAL_OR_PRIVATE_ORIGINS"


def _normalize_origin(value: str | None) -> str | None:
    """Normalize an absolute HTTP(S) URL to the origin used for comparisons."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value.strip())
        port = parsed.port
    except ValueError:
        return None
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()
    if scheme not in {"http", "https"} or not host:
        return None
    # ``urlparse().hostname`` removes the brackets from an IPv6 literal. Put
    # them back before rebuilding the origin so it remains a valid URL.
    origin_host = f"[{host}]" if ":" in host else host
    if (scheme == "https" and (port is None or port == 443)) or (
        scheme == "http" and (port is None or port == 80)
    ):
        return f"{scheme}://{origin_host}"
    if port:
        return f"{scheme}://{origin_host}:{port}"
    return f"{scheme}://{origin_host}"


def _is_local_or_internal_origin(
    origin: str | None, *, allow_local_or_private_origins: bool
) -> bool:
    """Allow localhost or literal private/loopback IP origins only."""
    if not allow_local_or_private_origins:
        return False

    normalized_origin = _normalize_origin(origin)
    if not normalized_origin:
        return False

    hostname = urlparse(normalized_origin).hostname
    if not hostname:
        return False

    if hostname in ("localhost", "127.0.0.1", "::1"):
        return True

    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False

    return parsed_ip.is_private or parsed_ip.is_loopback


def allow_local_or_private_origins_from_env() -> bool:
    """Return whether explicit private-network browser access is enabled."""

    return coerce_bool(os.getenv(ALLOW_LOCAL_OR_PRIVATE_ORIGINS_ENV), default=False)


def enforce_same_origin(request: Request, db: Session) -> None:
    """Require an approved browser origin for sensitive authentication calls."""
    expected_origins: set[str] = set()

    allow_local_or_private_origins = allow_local_or_private_origins_from_env()

    public_url_settings_loaded = False
    try:
        configured_public_urls = normalize_public_urls(
            get_value_by_page_and_key("general", "public_url", db),
            allow_empty=True,
        )
        public_url_settings_loaded = True
    except Exception:
        # Keep CSRF protection active even if settings storage is unavailable
        # in a narrow test harness or during a transient settings read failure.
        logger.debug(
            "Unable to read configured public URL while enforcing same-origin checks",
            exc_info=True,
        )
        configured_public_urls = []
    for configured_public_url in configured_public_urls:
        normalized_public_origin = _normalize_origin(configured_public_url)
        if normalized_public_origin:
            expected_origins.add(normalized_public_origin)

    env_public_origin = _normalize_origin(os.getenv("PUBLIC_URL"))
    if env_public_origin:
        expected_origins.add(env_public_origin)

    # Until an administrator configures a public URL, there is no canonical
    # origin against which a request can be checked. Disable this restriction
    # during that unconfigured state so first-run access works through any IP or
    # domain. A settings read failure does not enter this mode: it remains
    # fail-closed because the server cannot prove that the setting is empty.
    if public_url_settings_loaded and not expected_origins:
        return

    origin_header = _normalize_origin(request.headers.get("origin"))
    if origin_header and origin_header in expected_origins:
        return

    referer_header = _normalize_origin(request.headers.get("referer"))
    if referer_header and referer_header in expected_origins:
        return

    # Allow localhost and internal IP addresses only when explicitly configured.
    if _is_local_or_internal_origin(
        origin_header, allow_local_or_private_origins=allow_local_or_private_origins
    ) or _is_local_or_internal_origin(
        referer_header,
        allow_local_or_private_origins=allow_local_or_private_origins,
    ):
        return

    local_or_private_origin = _is_local_or_internal_origin(
        origin_header, allow_local_or_private_origins=True
    ) or _is_local_or_internal_origin(
        referer_header,
        allow_local_or_private_origins=True,
    )
    if local_or_private_origin and not allow_local_or_private_origins:
        raise HTTPException(
            status_code=403,
            detail=(
                "Cross-site request blocked: localhost/private-network origins are disallowed for sensitive auth "
                f"endpoints. Set {ALLOW_LOCAL_OR_PRIVATE_ORIGINS_ENV}=true only if this deployment intentionally "
                "serves Omlorix from localhost or private IP browser origins."
            ),
        )

    raise HTTPException(status_code=403, detail="Cross-site request blocked")
