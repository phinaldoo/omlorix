"""Regression coverage for the unified GitHub OAuth configuration."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings.schema_categories.login_social import LoginSocialSettings
from app.auth import social
from app.auth.github_oauth import build_github_oauth_endpoints
from app.auth.social import GitHubAuthProvider
from app.connections import github as github_connection


def test_self_hosted_github_endpoints_are_derived_from_one_base_url():
    """A self-hosted origin must consistently drive OAuth and REST requests."""

    endpoints = build_github_oauth_endpoints("https://GHE.Example/")

    assert endpoints.base_url == "https://ghe.example"
    assert endpoints.authorization_url == "https://ghe.example/login/oauth/authorize"
    assert endpoints.token_url == "https://ghe.example/login/oauth/access_token"
    assert endpoints.api_base_url == "https://ghe.example/api/v3"
    assert endpoints.user_url == "https://ghe.example/api/v3/user"
    assert endpoints.is_github_dot_com is False


@pytest.mark.parametrize(
    "base_url",
    (
        "file:///etc/passwd",
        "https://user:secret@ghe.example",
        "https://ghe.example/custom/path",
        "https://ghe.example?tenant=example",
    ),
)
def test_github_base_url_rejects_non_origin_values(base_url):
    """The administrator setting must remain an HTTP(S) server origin."""

    with pytest.raises(ValidationError):
        LoginSocialSettings(github_base_url=base_url)


def test_social_github_provider_uses_configured_server_origin(monkeypatch):
    """The standard social route must work for a self-hosted GitHub server."""

    monkeypatch.setattr(
        social,
        "get_settings_page_data",
        lambda _db, _page: {
            "enable_github_oauth": True,
            "enable_github_login": True,
            "github_base_url": "https://ghe.example",
            "github_client_id": "client-id",
            "github_client_secret": "client-secret",
            "github_allowed_organizations": ["engineering"],
        },
    )

    provider = GitHubAuthProvider(object())
    authorization_url = provider.get_authorization_url(
        "https://omlorix.example/api/v1/auth/social/github/callback",
        "state-value",
        "unused-nonce",
    )
    parsed = urlparse(authorization_url)
    params = parse_qs(parsed.query)

    assert parsed.netloc == "ghe.example"
    assert parsed.path == "/login/oauth/authorize"
    assert provider.TOKEN_URL == "https://ghe.example/login/oauth/access_token"
    assert provider.USERINFO_URL == "https://ghe.example/api/v3/user"
    assert "read:org" in params["scope"][0].split()


def test_managed_workspace_oauth_is_unavailable_for_self_hosted_github(monkeypatch):
    """Never send a self-hosted server token to GitHub's public MCP service."""

    monkeypatch.setattr(
        github_connection,
        "get_settings_page_data",
        lambda _db, _page: {
            "enable_github_oauth": True,
            "github_base_url": "https://ghe.example",
            "github_client_id": "client-id",
            "github_client_secret": "client-secret",
        },
    )

    assert github_connection.github_oauth_is_configured(object()) is False
