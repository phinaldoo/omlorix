import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.auth.social as social_module
from app.auth.github_email import resolve_github_email_verification
from app.auth.social import GitHubAuthProvider


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, headers=None):
        assert url == GitHubAuthProvider.USERINFO_URL
        return FakeResponse(
            200,
            {
                "id": 123,
                "login": "octocat",
                "email": "Octo@example.com",
                "name": "Octo Cat",
                "avatar_url": "https://example.com/avatar.png",
            },
        )


def test_resolve_github_email_verification_prefers_matching_profile_email():
    email, verified = resolve_github_email_verification(
        [
            {"email": "other@example.com", "primary": True, "verified": True},
            {"email": "octo@example.com", "primary": False, "verified": True},
        ],
        preferred_email="Octo@example.com",
    )

    assert email == "octo@example.com"
    assert verified is True


def test_github_user_info_fetches_verification_for_profile_email(monkeypatch):
    provider = GitHubAuthProvider.__new__(GitHubAuthProvider)
    provider.settings = {}
    seen = {}

    async def fake_get_primary_email(access_token, preferred_email=None):
        seen["access_token"] = access_token
        seen["preferred_email"] = preferred_email
        return preferred_email, True

    monkeypatch.setattr(social_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(provider, "_get_primary_email", fake_get_primary_email)

    user_info = asyncio.run(provider.get_user_info("token-123"))

    assert seen == {
        "access_token": "token-123",
        "preferred_email": "octo@example.com",
    }
    assert user_info["email"] == "octo@example.com"
    assert user_info["email_verified"] is True
