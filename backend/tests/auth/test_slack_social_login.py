"""Targeted tests for Slack's identity-only OpenID Connect sign-in flow."""

import asyncio
import time
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import social as social_module
from app.auth.social import SlackAuthProvider, SocialAuthProviderFactory
from app.settings.defaults import DEFAULT_SETTINGS


def _enabled_provider(**overrides):
    """Build a provider without a database for focused protocol tests."""
    provider = SlackAuthProvider.__new__(SlackAuthProvider)
    provider.settings = {
        "enable_slack_oauth": True,
        "enable_slack_login": True,
        "slack_client_id": "slack-client-id",
        "slack_client_secret": "slack-client-secret",
        "slack_button_text": "",
        "slack_allowed_domains": [],
        "slack_allowed_workspace_ids": [],
        "slack_allow_signup": True,
        **overrides,
    }
    return provider


def test_slack_defaults_and_factory_registration():
    """Slack login is opt-in and registered alongside other providers."""
    settings = DEFAULT_SETTINGS["login_social"]

    assert settings["enable_slack_login"] is False
    assert settings["slack_button_text"] == ""
    assert settings["slack_allowed_domains"] == []
    assert settings["slack_allowed_workspace_ids"] == []
    assert settings["slack_allow_signup"] is True
    assert SocialAuthProviderFactory.PROVIDERS["slack"] is SlackAuthProvider


def test_slack_authorization_url_uses_openid_form_post_and_identity_scopes():
    """Sign-in never requests the channel or message scopes used by connections."""
    provider = _enabled_provider()

    authorization_url = provider.get_authorization_url(
        "https://chat.example/api/v1/auth/social/slack/callback",
        "state-value",
        "nonce-value",
    )
    parsed = urlparse(authorization_url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == provider.AUTHORIZATION_URL
    assert query["response_type"] == ["code"]
    assert query["response_mode"] == ["form_post"]
    assert query["scope"] == ["openid email profile"]
    assert query["state"] == ["state-value"]
    assert query["nonce"] == ["nonce-value"]


def test_slack_verified_id_token_is_normalized(monkeypatch):
    """Only signed Slack claims become the normalized Omlorix identity."""
    provider = _enabled_provider(slack_allowed_workspace_ids=["T01234567"])
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    id_token = jwt.encode(
        {
            "iss": provider.ISSUER,
            "aud": provider.get_client_id(),
            "exp": now + 300,
            "iat": now,
            "sub": "U01234567",
            "nonce": "nonce-value",
            "email": "Person@Example.com",
            "email_verified": True,
            "name": "Person Example",
            "given_name": "Person",
            "family_name": "Example",
            "picture": "https://avatars.slack-edge.com/person.png",
            "https://slack.com/user_id": "U01234567",
            "https://slack.com/team_id": "T01234567",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "slack-key"},
    )

    async def fake_public_key(_kid):
        return private_key.public_key()

    monkeypatch.setattr(provider, "_get_slack_public_key", fake_public_key)

    user_info = asyncio.run(provider.get_user_info(id_token, tokens={"access_token": "unused"}))

    assert user_info == {
        "sub": "U01234567",
        "email": "person@example.com",
        "email_verified": True,
        "name": "Person Example",
        "given_name": "Person",
        "family_name": "Example",
        "profile_picture_url": "https://avatars.slack-edge.com/person.png",
        "workspace_id": "T01234567",
        "nonce": "nonce-value",
    }
    assert provider.validate_identity(user_info) is True


def test_slack_workspace_and_domain_allowlists_are_enforced():
    """Workspace membership and verified email domain are independent policies."""
    provider = _enabled_provider(
        slack_allowed_domains=["company.example"],
        slack_allowed_workspace_ids=["T-ALLOWED"],
    )

    assert provider.validate_domain("person@company.example") is True
    assert provider.validate_domain("person@outside.example") is False
    assert provider.validate_identity({"workspace_id": "t-allowed"}) is True
    assert provider.validate_identity({"workspace_id": "T-OTHER"}) is False


def test_slack_token_exchange_uses_openid_endpoint(monkeypatch):
    """The login exchange does not call Slack's regular workspace OAuth endpoint."""
    provider = _enabled_provider()
    calls = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "access_token": "identity-access", "id_token": "signed-id-token"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, data, headers):
            calls.append((url, data, headers))
            return FakeResponse()

    monkeypatch.setattr(social_module.httpx, "AsyncClient", FakeClient)

    tokens = asyncio.run(
        provider.exchange_code_for_tokens(
            "temporary-code",
            "https://chat.example/api/v1/auth/social/slack/callback",
        )
    )

    assert tokens["id_token"] == "signed-id-token"
    assert calls[0][0] == provider.TOKEN_URL
    assert calls[0][1]["grant_type"] == "authorization_code"
    assert calls[0][1]["client_secret"] == "slack-client-secret"
