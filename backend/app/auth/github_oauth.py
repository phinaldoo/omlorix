"""Shared GitHub OAuth endpoint derivation.

Omlorix supports either GitHub.com or one self-hosted GitHub Enterprise Server
through the same OAuth provider.  Keeping endpoint construction in one module
prevents social sign-in and managed workspace connections from drifting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_GITHUB_BASE_URL = "https://github.com"


def normalize_github_base_url(value: Any) -> str:
    """Validate and canonicalize a GitHub web origin.

    GitHub OAuth endpoints are rooted at the server origin, so paths, query
    strings, fragments, and embedded credentials are rejected.  An empty value
    intentionally selects GitHub.com to keep the default setup concise.
    """

    normalized = str(value or DEFAULT_GITHUB_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            "GitHub base URL must use http:// or https:// and include a host"
        )
    if parsed.username or parsed.password:
        raise ValueError("GitHub base URL must not include credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(
            "GitHub base URL must be a server origin without a path, query, or fragment"
        )

    # Preserve an explicitly configured port while removing an inconsequential
    # trailing slash.  Hostname casing is normalized by rebuilding the netloc.
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


@dataclass(frozen=True)
class GitHubOAuthEndpoints:
    """All OAuth and REST endpoints derived from one GitHub web origin."""

    base_url: str
    api_base_url: str
    authorization_url: str
    token_url: str
    user_url: str
    user_emails_url: str
    user_organizations_url: str
    application_grants_url: str
    is_github_dot_com: bool


def build_github_oauth_endpoints(base_url: Any) -> GitHubOAuthEndpoints:
    """Build the standard endpoint set for GitHub.com or GitHub Enterprise."""

    normalized_base_url = normalize_github_base_url(base_url)
    parsed = urlsplit(normalized_base_url)
    is_github_dot_com = (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and parsed.port is None
    )
    api_base_url = (
        "https://api.github.com"
        if is_github_dot_com
        else f"{normalized_base_url}/api/v3"
    )
    return GitHubOAuthEndpoints(
        base_url=normalized_base_url,
        api_base_url=api_base_url,
        authorization_url=f"{normalized_base_url}/login/oauth/authorize",
        token_url=f"{normalized_base_url}/login/oauth/access_token",
        user_url=f"{api_base_url}/user",
        user_emails_url=f"{api_base_url}/user/emails",
        user_organizations_url=f"{api_base_url}/user/orgs",
        application_grants_url=f"{api_base_url}/applications",
        is_github_dot_com=is_github_dot_com,
    )
