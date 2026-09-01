import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.auth import utils as auth_utils
from app.auth import social as auth_social
from app.auth.social import GoogleAuthProvider, SocialAuthProviderFactory


class _GoogleUserInfoClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "sub": "google-user",
                "email": "google@example.test",
                "email_verified": True,
            },
        )


def _mock_google_callback_provider(monkeypatch, *, verified_nonce: str):
    provider = GoogleAuthProvider.__new__(GoogleAuthProvider)
    provider.exchange_code_for_tokens = AsyncMock(
        return_value={"access_token": "access", "id_token": "identity"}
    )
    provider.verify_id_token = AsyncMock(
        return_value={
            "sub": "google-user",
            "email_verified": True,
            "hd": "",
            "nonce": verified_nonce,
        }
    )
    monkeypatch.setattr(auth_social.httpx, "AsyncClient", _GoogleUserInfoClient)
    monkeypatch.setattr(
        SocialAuthProviderFactory,
        "get_provider",
        lambda *_args: provider,
    )
    return provider


@pytest.mark.parametrize("returned_nonce", ["", "different-nonce"])
def test_google_callback_rejects_missing_or_mismatched_nonce(
    monkeypatch, returned_nonce
):
    expected_nonce = "expected-google-nonce"
    provider = _mock_google_callback_provider(
        monkeypatch, verified_nonce=returned_nonce
    )

    with pytest.raises(HTTPException, match="nonce verification failed"):
        asyncio.run(
            auth_utils.verified_social_user_info_from_callback(
                "google",
                "code",
                "https://chat.example/api/v1/auth/social/google/callback",
                object(),
                expected_nonce_hash=hashlib.sha256(
                    expected_nonce.encode("utf-8")
                ).hexdigest(),
            )
        )
    provider.verify_id_token.assert_awaited_once_with("identity")


def test_google_callback_accepts_matching_nonce(monkeypatch):
    nonce = "matching-google-nonce"
    provider = _mock_google_callback_provider(monkeypatch, verified_nonce=nonce)

    user_info = asyncio.run(
        auth_utils.verified_social_user_info_from_callback(
            "google",
            "code",
            "https://chat.example/api/v1/auth/social/google/callback",
            object(),
            expected_nonce_hash=hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
        )
    )

    assert user_info["nonce"] == nonce
    provider.verify_id_token.assert_awaited_once_with("identity")


class AllowAllProvider:
    def validate_domain(self, email):
        return True

    def allows_signup(self):
        return True


def test_social_login_rejects_explicitly_unverified_email(monkeypatch):
    logs = []
    monkeypatch.setattr(
        SocialAuthProviderFactory,
        "get_provider",
        staticmethod(lambda provider, db: AllowAllProvider()),
    )
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.7")
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: False)
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: logs.append(args))

    result = asyncio.run(
        auth_utils.social_login_from_user_info(
            provider="github",
            user_info={
                "email": "victim@example.com",
                "email_verified": False,
                "sub": "attacker-github-id",
            },
            request=SimpleNamespace(headers={}, cookies={}),
            response=Response(),
            db=object(),
            db_log=object(),
            flow_context={"account_mode": "primary", "replace_slot": None, "return_url": ""},
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == "/login?error=email_not_verified"
    assert logs
    assert logs[0][1:4] == (
        "social_login",
        "warning",
        "Missing or unverified email signal rejected for github signup: victim@example.com",
    )


def test_social_login_api_rejects_explicitly_unverified_email(monkeypatch):
    monkeypatch.setattr(
        SocialAuthProviderFactory,
        "get_provider",
        staticmethod(lambda provider, db: AllowAllProvider()),
    )
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.7")
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: False)
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            auth_utils.social_login_from_user_info(
                provider="github",
                user_info={"email": "victim@example.com", "email_verified": "false", "sub": "attacker-github-id"},
                request=SimpleNamespace(headers={}, cookies={}),
                response=Response(),
                db=object(),
                db_log=object(),
                flow_context={"account_mode": "primary", "replace_slot": None, "return_url": ""},
                api_mode=True,
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Your email address is not verified with the social login provider."


def test_social_auto_link_rejects_missing_verified_email_signal_for_unknown_provider(monkeypatch):
    user = SimpleNamespace(
        id="user-1",
        email="victim@example.com",
        role="user",
        is_active=True,
        deleted_at=None,
    )
    logs = []

    monkeypatch.setattr(
        SocialAuthProviderFactory,
        "get_provider",
        staticmethod(lambda provider, db: AllowAllProvider()),
    )
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.8")
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: True)
    monkeypatch.setattr(auth_utils, "get_user", lambda db, email=None: user)
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda user, db: None)
    monkeypatch.setattr(auth_utils, "get_user_setting_value", lambda user_id, page, key, db: "")
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda db_log, event, level, message, user_id, user_agent, client_ip: logs.append(
            (event, level, message, user_id)
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "update_user_settings",
        lambda *_args, **_kwargs: pytest.fail("unverified auto-link should not persist a provider link"),
    )

    result = asyncio.run(
        auth_utils.social_login_from_user_info(
            provider="generic",
            user_info={
                "email": "victim@example.com",
                "sub": "generic-subject",
            },
            request=SimpleNamespace(headers={"User-Agent": "pytest-browser"}, cookies={}),
            response=Response(),
            db=object(),
            db_log=object(),
            flow_context={"account_mode": "primary", "replace_slot": None, "return_url": ""},
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == "/login?error=email_not_verified"
    assert logs == [
        (
            "social_login",
            "warning",
            "Missing or unverified email signal rejected for generic auto-link: victim@example.com",
            "user-1",
        )
    ]


def test_social_signup_rejects_missing_verified_email_signal_for_unknown_provider(monkeypatch):
    logs = []

    monkeypatch.setattr(
        SocialAuthProviderFactory,
        "get_provider",
        staticmethod(lambda provider, db: AllowAllProvider()),
    )
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.9")
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: False)
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda db_log, event, level, message, user_id, user_agent, client_ip: logs.append(
            (event, level, message, user_id)
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "create_user",
        lambda **_kwargs: pytest.fail("signup without a positive verified-email signal should not create a user"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            auth_utils.social_login_from_user_info(
                provider="generic",
                user_info={
                    "email": "new@example.com",
                    "sub": "generic-subject",
                },
                request=SimpleNamespace(headers={"User-Agent": "pytest-browser"}, cookies={}),
                response=Response(),
                db=object(),
                db_log=object(),
                flow_context={"account_mode": "primary", "replace_slot": None, "return_url": ""},
                api_mode=True,
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Your email address is not verified with the social login provider."
    assert logs == [
        (
            "social_login",
            "warning",
            "Missing or unverified email signal rejected for generic signup: new@example.com",
            None,
        )
    ]


def test_microsoft_signup_allows_verified_oid_tid_without_email_verified_claim(monkeypatch):
    """Allow Microsoft signup with verified oid and tid even without email_verified."""
    created = {}

    monkeypatch.setattr(
        SocialAuthProviderFactory,
        "get_provider",
        staticmethod(lambda provider, db: AllowAllProvider()),
    )
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: False)
    monkeypatch.setattr(auth_utils, "_is_new_account_registration_enabled", lambda db: True)
    monkeypatch.setattr(auth_utils, "_require_terms_ready_for_self_service_signup", lambda db, flow_context: None)
    monkeypatch.setattr(auth_utils, "hash_password", lambda value: f"hashed:{value}")
    monkeypatch.setattr(
        auth_utils,
        "get_settings_page_data",
        lambda db, page: {"default_user_role": "user", "default_user_group": "default"},
    )
    monkeypatch.setattr(
        auth_utils,
        "create_user",
        lambda **kwargs: created.setdefault(
            "user",
            SimpleNamespace(id="new-user", email=kwargs["email"], role="user", group_id="default"),
        ),
    )
    monkeypatch.setattr(auth_utils, "update_user_settings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "_validate_or_store_provider_subject", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "create_admin_notification", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda user, db: None)
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: None)

    async def fake_sync_social_profile_picture(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_utils, "_sync_social_profile_picture", fake_sync_social_profile_picture)
    monkeypatch.setattr(auth_utils, "ensure_provider_alignment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "evaluate_login_2fa", lambda *_args, **_kwargs: None)

    async def fake_complete_social_login_api(user, request, response, db, db_log, flow_context=None):
        return {"status": "ok", "user_id": user.id}

    monkeypatch.setattr(auth_utils, "_complete_social_login_api", fake_complete_social_login_api)

    result = asyncio.run(
        auth_utils.social_login_from_user_info(
            provider="microsoft",
            user_info={
                "email": "new@example.com",
                "sub": "microsoft-subject",
                "tenant_id": "tenant-id",
                "microsoft_identity_verified": True,
            },
            request=SimpleNamespace(headers={"User-Agent": "pytest-browser"}, cookies={}),
            response=Response(),
            db=object(),
            db_log=object(),
            flow_context={"account_mode": "primary", "replace_slot": None, "return_url": ""},
            api_mode=True,
        )
    )

    assert result == {"status": "ok", "user_id": "new-user"}
    assert created["user"].email == "new@example.com"


def test_pending_terms_signup_preserves_verified_microsoft_identity():
    """Keep Microsoft's signed identity proof across Terms confirmation."""

    sanitized = auth_utils._sanitize_pending_federated_user_info(
        {
            "email": "new@example.com",
            "sub": "microsoft-subject",
            "tenant_id": "tenant-id",
            "microsoft_identity_verified": True,
            "nonce": "one-time-callback-value",
        }
    )

    assert sanitized == {
        "email": "new@example.com",
        "sub": "microsoft-subject",
        "tenant_id": "tenant-id",
        "microsoft_identity_verified": True,
    }
    assert auth_utils._has_verified_social_signup_identity("microsoft", sanitized) is True
