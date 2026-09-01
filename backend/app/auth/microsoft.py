"""Shared Microsoft identity-platform endpoint helpers.

Microsoft uses the tenant segment of its OAuth URLs to control which account
types may authenticate.  Keeping construction and validation here prevents
social login and Microsoft file connections from silently drifting apart.
"""

from __future__ import annotations

import re


MICROSOFT_DEFAULT_TENANT = "common"
MICROSOFT_TENANT_ALIASES = {"common", "organizations", "consumers"}
_MICROSOFT_TENANT_IDENTIFIER_RE = re.compile(
    r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"|(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)"
)


def normalize_microsoft_tenant(value: object) -> str:
    """Return a safe Microsoft OAuth tenant alias, GUID, or verified domain.

    The value becomes one path component on ``login.microsoftonline.com``.
    Strict validation therefore also prevents slashes or encoded URL material
    from changing the fixed identity-platform origin or endpoint path.
    """

    tenant = str(value or MICROSOFT_DEFAULT_TENANT).strip().lower()
    if tenant in MICROSOFT_TENANT_ALIASES:
        return tenant
    if len(tenant) <= 255 and _MICROSOFT_TENANT_IDENTIFIER_RE.fullmatch(tenant):
        return tenant
    raise ValueError(
        "Microsoft tenant must be common, organizations, consumers, a tenant GUID, or a verified tenant domain."
    )


def microsoft_oauth_endpoints(tenant: object) -> tuple[str, str]:
    """Build authorization and token endpoints for one validated tenant."""

    normalized_tenant = normalize_microsoft_tenant(tenant)
    base_url = f"https://login.microsoftonline.com/{normalized_tenant}/oauth2/v2.0"
    return f"{base_url}/authorize", f"{base_url}/token"
