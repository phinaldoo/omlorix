import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.auth import utils as auth_utils
from app.auth import identities


class _SocialProviderFactory:
    provider = None

    @classmethod
    def get_provider(cls, provider_type, db):
        return cls.provider


class _SocialProvider:
    def validate_domain(self, email):
        return True

    def allows_signup(self):
        return True


def test_normalized_social_identity_conflict_uses_standard_failure_handler(monkeypatch):
    failures = []

    def record_conflict(*_args, **_kwargs):
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="identity conflict")

    def fail(error_key, detail, **kwargs):
        failures.append((error_key, detail, kwargs))
        return "rejected"

    monkeypatch.setattr(identities, "record_social_identity", record_conflict)

    result = auth_utils._record_normalized_social_identity(
        "user-id",
        "google",
        {"sub": "provider-subject"},
        SimpleNamespace(query=True),
        failure_handler=fail,
    )

    assert result == "rejected"
    assert failures[0][0] == "social_account_conflict"
    assert failures[0][2]["status_code"] == 409
    assert failures[0][2]["log_level"] == "warning"


def test_linked_social_subject_must_match(monkeypatch):
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: "provider-subject-1",
    )

    assert (
        auth_utils._validate_or_store_provider_subject(
            "user-id",
            "social_login",
            "google",
            {"sub": "provider-subject-2"},
            object(),
        )
        is False
    )


def test_missing_social_subject_is_rejected(monkeypatch):
    assert (
        auth_utils._validate_or_store_provider_subject(
            "user-id",
            "social_login",
            "google",
            {"email": "user@example.com"},
            object(),
        )
        is False
    )


def test_social_subject_is_stored_when_linking_account(monkeypatch):
    stored = {}

    monkeypatch.setattr(auth_utils, "get_user_setting_value", lambda user_id, page, key, db: "")
    monkeypatch.setattr(
        auth_utils,
        "update_user_settings",
        lambda user_id, page, key, value, db: stored.__setitem__((page, key), value),
    )

    assert auth_utils._validate_or_store_provider_subject(
        "user-id",
        "social_login",
        "google",
        {"sub": "provider-subject-1"},
        object(),
    )
    assert stored[("social_login", "google_user_id")] == "provider-subject-1"


def test_linked_social_account_with_missing_stored_subject_is_rejected(monkeypatch):
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        role="user",
        is_active=True,
        deleted_at=None,
    )
    logs = []

    _SocialProviderFactory.provider = _SocialProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.social",
        SimpleNamespace(SocialAuthProviderFactory=_SocialProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.11")
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: True)
    monkeypatch.setattr(auth_utils, "get_user", lambda db, email=None: user)
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda user, db: None)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("social_login", "google_linked"): True,
            ("social_login", "google_user_id"): "",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda db_log, event, level, message, user_id, user_agent, client_ip: logs.append(
            (event, level, message, user_id)
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "_sync_social_profile_picture",
        lambda *_args, **_kwargs: pytest.fail("linked social accounts without a stored subject must not continue"),
    )

    result = asyncio.run(
        auth_utils.social_login_from_user_info(
            "google",
            {
                "email": "user@example.com",
                "email_verified": True,
                "sub": "incoming-subject",
            },
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}),
            object(),
            object(),
            object(),
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == "/login?error=provider_subject_mismatch"
    assert logs == [
        (
            "social_login",
            "warning",
            "Social signin blocked by provider subject mismatch for google: user@example.com",
            "user-id",
        )
    ]


def test_social_subject_lookup_is_used_before_email_matching(monkeypatch):
    user = SimpleNamespace(
        id="subject-user",
        email="old@example.com",
        role="user",
        is_active=True,
        deleted_at=None,
    )

    async def _complete_social_login(user, *_args, **_kwargs):
        return SimpleNamespace(user_id=user.id)

    async def _sync_social_profile_picture(*_args, **_kwargs):
        return None

    _SocialProviderFactory.provider = _SocialProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.social",
        SimpleNamespace(SocialAuthProviderFactory=_SocialProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.12")
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        auth_utils,
        "_find_user_by_settings_value",
        lambda db, path, values, use_constant_time=False: user if path == ("social_login", "google_user_id") else None,
    )
    monkeypatch.setattr(
        auth_utils,
        "user_exists_by_email",
        lambda db, email: pytest.fail("subject-linked social accounts should be resolved before email matching"),
    )
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda user, db: None)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("social_login", "google_linked"): True,
            ("social_login", "google_user_id"): "provider-subject-1",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: {"is_locked": False})
    monkeypatch.setattr(auth_utils, "_sync_social_profile_picture", _sync_social_profile_picture)
    monkeypatch.setattr(auth_utils, "ensure_provider_alignment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "evaluate_login_2fa", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "_complete_social_login", _complete_social_login)

    result = asyncio.run(
        auth_utils.social_login_from_user_info(
            "google",
            {
                "email": "new-address@example.com",
                "email_verified": True,
                "sub": "provider-subject-1",
            },
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}),
            object(),
            object(),
            object(),
        )
    )

    assert result.user_id == "subject-user"


@pytest.mark.parametrize(
    "provider",
    [
        "google",
        "microsoft",
        "github",
        "apple",
        "slack",
    ],
)
def test_existing_social_email_match_requires_explicit_link(monkeypatch, provider):
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        role="user",
        is_active=True,
        deleted_at=None,
    )
    logs = []

    async def _sync_social_profile_picture(*_args, **_kwargs):
        pytest.fail("unlinked social email matches must not auto-link or continue")

    _SocialProviderFactory.provider = _SocialProvider()
    monkeypatch.setitem(
        sys.modules,
        "app.auth.social",
        SimpleNamespace(SocialAuthProviderFactory=_SocialProviderFactory),
    )
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.13")
    monkeypatch.setattr(auth_utils, "read_flow_context_cookie", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth_utils, "_find_user_by_linked_provider_subject", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_utils, "user_exists_by_email", lambda db, email: True)
    monkeypatch.setattr(auth_utils, "get_user", lambda db, email=None: user)
    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda user, db: None)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("social_login", f"{provider}_linked"): False,
            ("social_login", f"{provider}_user_id"): "",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(
        auth_utils,
        "create_authentication_log",
        lambda db_log, event, level, message, user_id, user_agent, client_ip: logs.append(
            (event, level, message, user_id)
        ),
    )
    monkeypatch.setattr(auth_utils, "_sync_social_profile_picture", _sync_social_profile_picture)

    result = asyncio.run(
        auth_utils.social_login_from_user_info(
            provider,
            {
                "email": "user@example.com",
                "email_verified": True,
                "sub": "incoming-subject",
            },
            SimpleNamespace(headers={"User-Agent": "pytest-browser"}),
            object(),
            object(),
            object(),
        )
    )

    assert result.status_code == 302
    assert result.headers["location"] == f"/login?error=social_account_not_linked&provider={provider}"
    assert logs == [
        (
            "social_login",
            "warning",
            f"Social signin blocked: existing account is not linked to {provider}: user@example.com",
            "user-id",
        )
    ]
