import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException
import pytest

import app.auth.social as social_module
from app.auth import utils as auth_utils
from app.auth.microsoft import microsoft_oauth_endpoints, normalize_microsoft_tenant
from app.auth.social import MicrosoftAuthProvider


class FakeResponse:
    status_code = 200
    headers = {}
    content = b""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, headers):
        self.calls.append(url)
        return FakeResponse(self.payload)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, "common"),
        ("organizations", "organizations"),
        ("11111111-2222-3333-4444-555555555555", "11111111-2222-3333-4444-555555555555"),
        ("Contoso.OnMicrosoft.com", "contoso.onmicrosoft.com"),
    ],
)
def test_microsoft_tenant_normalization(configured, expected):
    assert normalize_microsoft_tenant(configured) == expected
    authorization_endpoint, token_endpoint = microsoft_oauth_endpoints(configured)
    assert authorization_endpoint == f"https://login.microsoftonline.com/{expected}/oauth2/v2.0/authorize"
    assert token_endpoint == f"https://login.microsoftonline.com/{expected}/oauth2/v2.0/token"


@pytest.mark.parametrize("configured", ["common/../consumers", "https://evil.example", "tenant%2fconsumers"])
def test_microsoft_tenant_rejects_url_material(configured):
    with pytest.raises(ValueError):
        normalize_microsoft_tenant(configured)


def test_microsoft_user_info_uses_oid_tid_without_fabricated_email_verification(monkeypatch):
    provider = MicrosoftAuthProvider.__new__(MicrosoftAuthProvider)
    provider.settings = {"import_microsoft_oauth_profile_picture": False}
    client = FakeClient(
        {
            "id": "microsoft-subject",
            "mail": "",
            "userPrincipalName": "person@example.com",
            "displayName": "Person Example",
            "givenName": "Person",
            "surname": "Example",
        }
    )

    async def fake_decode_id_token_verified(_id_token):
        return {
            "oid": "microsoft-subject",
            "tid": "tenant-id",
            "nonce": "request-nonce",
            "preferred_username": "person@example.com",
        }

    monkeypatch.setattr(social_module.httpx, "AsyncClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(provider, "_decode_id_token_verified", fake_decode_id_token_verified)

    user_info = asyncio.run(
        provider.get_user_info(
            "access-token",
            tokens={"id_token": "token-123"},
        )
    )

    assert client.calls == [provider.USERINFO_URL]
    assert user_info["email"] == "person@example.com"
    assert "email_verified" not in user_info
    assert user_info["microsoft_identity_verified"] is True
    assert user_info["sub"] == "microsoft-subject"
    assert user_info["tenant_id"] == "tenant-id"
    assert user_info["nonce"] == "request-nonce"


def test_microsoft_user_info_rejects_missing_id_token(monkeypatch):
    provider = MicrosoftAuthProvider.__new__(MicrosoftAuthProvider)
    provider.settings = {"import_microsoft_oauth_profile_picture": False}
    client = FakeClient(
        {
            "id": "microsoft-subject",
            "mail": "person@example.com",
            "userPrincipalName": "person@tenant.example",
            "displayName": "Person Example",
            "givenName": "Person",
            "surname": "Example",
        }
    )

    monkeypatch.setattr(social_module.httpx, "AsyncClient", lambda *args, **kwargs: client)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(provider.get_user_info("access-token"))

    assert exc_info.value.status_code == 400
    assert client.calls == []


def test_microsoft_user_info_rejects_id_token_verification_failure(monkeypatch):
    provider = MicrosoftAuthProvider.__new__(MicrosoftAuthProvider)
    provider.settings = {"import_microsoft_oauth_profile_picture": False}
    client = FakeClient(
        {
            "id": "microsoft-subject",
            "mail": "person@example.com",
            "userPrincipalName": "person@tenant.example",
            "displayName": "Person Example",
            "givenName": "Person",
            "surname": "Example",
        }
    )

    async def fake_decode_id_token_verified(_id_token):
        raise HTTPException(status_code=400, detail="Invalid Microsoft ID token")

    monkeypatch.setattr(social_module.httpx, "AsyncClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(provider, "_decode_id_token_verified", fake_decode_id_token_verified)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(provider.get_user_info("access-token", tokens={"id_token": "invalid-token"}))

    assert exc_info.value.detail == "Invalid Microsoft ID token"
    assert client.calls == []


def test_microsoft_user_info_rejects_verified_id_token_without_tenant(monkeypatch):
    provider = MicrosoftAuthProvider.__new__(MicrosoftAuthProvider)
    provider.settings = {"import_microsoft_oauth_profile_picture": False}
    client = FakeClient({"id": "microsoft-subject", "mail": "person@example.com"})

    async def fake_decode_id_token_verified(_id_token):
        return {"verified_primary_email": "person@example.com"}

    monkeypatch.setattr(social_module.httpx, "AsyncClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(provider, "_decode_id_token_verified", fake_decode_id_token_verified)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(provider.get_user_info("access-token", tokens={"id_token": "token-123"}))

    assert exc_info.value.detail == "Microsoft ID token tenant is missing."
    assert client.calls == []


def test_microsoft_user_info_rejects_graph_identity_mismatch(monkeypatch):
    provider = MicrosoftAuthProvider.__new__(MicrosoftAuthProvider)
    provider.settings = {"import_microsoft_oauth_profile_picture": False}
    client = FakeClient({"id": "different-object", "mail": "person@example.com"})

    async def fake_decode_id_token_verified(_id_token):
        return {
            "oid": "signed-object",
            "tid": "tenant-id",
            "nonce": "request-nonce",
        }

    monkeypatch.setattr(social_module.httpx, "AsyncClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(provider, "_decode_id_token_verified", fake_decode_id_token_verified)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(provider.get_user_info("access-token", tokens={"id_token": "token-123"}))

    assert exc_info.value.detail == "Microsoft profile identity does not match the ID token."


@pytest.mark.parametrize("returned_nonce", ["", "different-nonce"])
def test_microsoft_callback_rejects_missing_or_mismatched_nonce(monkeypatch, returned_nonce):
    class FakeProvider:
        async def exchange_code_for_tokens(self, _code, _redirect_uri):
            return {"access_token": "access-token", "id_token": "id-token"}

        async def get_user_info(self, _access_token, *, tokens=None):
            assert tokens == {"access_token": "access-token", "id_token": "id-token"}
            return {
                "sub": "microsoft-subject",
                "tenant_id": "tenant-id",
                "nonce": returned_nonce,
            }

    monkeypatch.setattr(
        social_module.SocialAuthProviderFactory,
        "get_provider",
        staticmethod(lambda _provider, _db: FakeProvider()),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            auth_utils.verified_social_user_info_from_callback(
                "microsoft",
                "authorization-code",
                "https://chat.example/api/v1/auth/social/microsoft/callback",
                object(),
                expected_nonce_hash=hashlib.sha256(b"request-nonce").hexdigest(),
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Provider nonce verification failed."


def test_microsoft_callback_accepts_matching_nonce(monkeypatch):
    class FakeProvider:
        async def exchange_code_for_tokens(self, _code, _redirect_uri):
            return {"access_token": "access-token", "id_token": "id-token"}

        async def get_user_info(self, _access_token, *, tokens=None):
            return {
                "sub": "microsoft-subject",
                "tenant_id": "tenant-id",
                "nonce": "request-nonce",
            }

    monkeypatch.setattr(
        social_module.SocialAuthProviderFactory,
        "get_provider",
        staticmethod(lambda _provider, _db: FakeProvider()),
    )

    result = asyncio.run(
        auth_utils.verified_social_user_info_from_callback(
            "microsoft",
            "authorization-code",
            "https://chat.example/api/v1/auth/social/microsoft/callback",
            object(),
            expected_nonce_hash=hashlib.sha256(b"request-nonce").hexdigest(),
        )
    )

    assert result["sub"] == "microsoft-subject"
