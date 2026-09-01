from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.connections import google as google_connection
from app.connections import github as github_connection
from app.connections import slack as slack_connection
from app.settings.defaults import DEFAULT_SETTINGS


def _query_scope_values(url: str) -> set[str]:
    params = parse_qs(urlparse(url).query)
    return set(str(params.get("scope", [""])[0]).split())


def _oauth_settings(**overrides):
    settings = {
        "enable_github_oauth": True,
        "github_client_id": "github-client",
        "github_client_secret": "github-secret",
        "enable_slack_oauth": True,
        "slack_client_id": "slack-client",
        "slack_client_secret": "slack-secret",
        "enable_microsoft_oauth": True,
        "microsoft_client_id": "microsoft-client",
        "microsoft_client_secret": "microsoft-secret",
    }
    settings.update(overrides)
    return settings


def test_login_social_defaults_include_configurable_connection_scope_tiers():
    login_social = DEFAULT_SETTINGS["login_social"]

    assert login_social["github_connection_scope_tier"] == "repository_access"
    assert login_social["slack_connection_scope_tier"] == "public_read"
    assert login_social["enable_github_oauth"] is False
    assert login_social["enable_slack_oauth"] is False
    assert login_social["enable_microsoft_oauth"] is False
    assert login_social["microsoft_tenant"] == "common"


@pytest.mark.parametrize(
    ("module", "is_configured", "settings"),
    [
        (
            google_connection,
            google_connection.google_oauth_is_configured,
            {
                "enable_google_oauth": False,
                "google_client_id": "google-client",
                "google_client_secret": "google-secret",
            },
        ),
        (
            github_connection,
            github_connection.github_oauth_is_configured,
            {
                "enable_github_oauth": False,
                "github_client_id": "github-client",
                "github_client_secret": "github-secret",
            },
        ),
        (
            slack_connection,
            slack_connection.slack_oauth_is_configured,
            {
                "enable_slack_oauth": False,
                "slack_client_id": "slack-client",
                "slack_client_secret": "slack-secret",
            },
        ),
    ],
)
def test_connection_oauth_configured_requires_provider_master_toggle(monkeypatch, module, is_configured, settings):
    monkeypatch.setattr(module, "get_settings_page_data", lambda _db, _page: settings)

    assert is_configured(object()) is False


def test_github_oauth_uses_repository_access_by_default(monkeypatch):
    monkeypatch.setattr(github_connection, "get_settings_page_data", lambda _db, _page: _oauth_settings())
    monkeypatch.setattr(github_connection, "save_connection_oauth_state", lambda *_args, **_kwargs: None)

    url = github_connection.start_github_oauth(
        object(),
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://omlorix.example.com/callback",
    )

    scopes = _query_scope_values(url)

    assert {"repo", "read:org", "read:user", "user:email"}.issubset(scopes)
    assert "notifications" not in scopes
    assert "gist" not in scopes
    assert "project" not in scopes


def test_github_oauth_extended_tier_adds_optional_scopes(monkeypatch):
    monkeypatch.setattr(
        github_connection,
        "get_settings_page_data",
        lambda _db, _page: _oauth_settings(github_connection_scope_tier="extended_access"),
    )
    monkeypatch.setattr(github_connection, "save_connection_oauth_state", lambda *_args, **_kwargs: None)

    url = github_connection.start_github_oauth(
        object(),
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://omlorix.example.com/callback",
    )

    scopes = _query_scope_values(url)

    assert {"repo", "notifications", "gist", "project"}.issubset(scopes)


def test_slack_oauth_uses_public_read_by_default(monkeypatch):
    monkeypatch.setattr(slack_connection, "get_settings_page_data", lambda _db, _page: _oauth_settings())
    monkeypatch.setattr(slack_connection, "save_connection_oauth_state", lambda *_args, **_kwargs: None)

    url = slack_connection.start_slack_oauth(
        object(),
        user_id="user-1",
        return_path="/workspace/connections",
        redirect_uri="https://omlorix.example.com/callback",
    )

    scopes = _query_scope_values(url)

    assert {"search:read.public", "search:read.users", "channels:history", "users:read", "users:read.email"} == scopes
