import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils
from app.auth import router as auth_router
from app.auth import twofa_provider
from app.email import models as email_models
from app.users.defaults import DEFAULT_USER_SETTINGS


def test_setup_material_access_is_audited_without_secret(monkeypatch):
    user = SimpleNamespace(id="user-id", email="user@example.com")
    audit_calls = []
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/auth/twofa/setup-material",
            "headers": [],
            "client": ("203.0.113.10", 1234),
        }
    )

    monkeypatch.setattr(auth_router, "resolve_access_token", lambda _request: "access-token")
    monkeypatch.setattr(auth_router, "check_user_by_token", lambda *_args: user)
    monkeypatch.setattr(auth_router, "require_sensitive_action_auth", lambda *_args: None)
    monkeypatch.setattr(
        auth_router,
        "get_totp_setup_material",
        lambda *_args: {"provider": "totp", "secret": "JBSWY3DPEHPK3PXP"},
    )
    monkeypatch.setattr(
        auth_router,
        "_audit_auth_security_event",
        lambda *args: audit_calls.append(args),
    )

    result = auth_router.twofa_setup_material_route(
        request,
        Response(),
        db=object(),
        db_log=object(),
    )

    assert result["secret"] == "JBSWY3DPEHPK3PXP"
    assert audit_calls[0][4] == "TWOFA_SETUP_MATERIAL_ACCESSED"
    assert audit_calls[0][5] == {"flow": "authenticated_session"}
    assert "JBSWY3DPEHPK3PXP" not in repr(audit_calls)


def test_email_delivery_otp_is_invalidated_when_security_epoch_rotates(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    email_models.EmailSecurityState.__table__.create(bind=engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(twofa_provider, "_get_hash_secret", lambda _db: "hash-secret")
    try:
        before = twofa_provider._hash_delivery_otp(
            "123456", "user-id", "email", "login", db
        )
        email_models.rotate_email_security_action_epoch(db)
        db.commit()
        after = twofa_provider._hash_delivery_otp(
            "123456", "user-id", "email", "login", db
        )

        assert before != after
    finally:
        db.close()


def test_totp_setup_response_does_not_include_secret_material(monkeypatch):
    settings = {}
    user = SimpleNamespace(id="user-id", email="user@example.com")

    monkeypatch.setattr(twofa_provider.pyotp, "random_base32", lambda: "JBSWY3DPEHPK3PXP")
    monkeypatch.setattr(twofa_provider, "_get_application_name", lambda db: "Omlorix")
    monkeypatch.setattr(
        twofa_provider,
        "update_user_settings",
        lambda user_id, page, key, value, db: settings.__setitem__((page, key), value),
    )

    result = twofa_provider._generate_totp_setup(user, object())

    assert result == {
        "status": "otp_setup",
        "provider": "totp",
        "setup_material_available": True,
        "resend_available_in_seconds": 0,
    }
    assert settings[("secret", "2fa_secret_pending")] == "JBSWY3DPEHPK3PXP"
    assert "secret" not in result
    assert "qrcode" not in result


def test_totp_setup_material_fetches_pending_secret(monkeypatch):
    user = SimpleNamespace(id="user-id", email="user@example.com")

    monkeypatch.setattr(
        twofa_provider,
        "get_user_setting_value",
        lambda user_id, page, key, db: "JBSWY3DPEHPK3PXP" if (page, key) == ("secret", "2fa_secret_pending") else "",
    )
    monkeypatch.setattr(twofa_provider, "_get_application_name", lambda db: "Omlorix")

    material = twofa_provider.get_totp_setup_material(user, object())

    assert material["provider"] == "totp"
    assert material["secret"] == "JBSWY3DPEHPK3PXP"
    assert material["qrcode"].startswith("otpauth://totp/")


def test_totp_setup_rejects_a_secret_replaced_before_user_lock(monkeypatch):
    rollback_calls = []
    user = SimpleNamespace(id="user-id", email="user@example.com")
    locked_user = SimpleNamespace(id="user-id", settings={})
    locked_settings = {
        "login_2fa": {"enable_2fa": False, "provider": ""},
        "secret": {"2fa_secret_pending": "replacement-secret"},
    }
    db = SimpleNamespace(rollback=lambda: rollback_calls.append(True))

    monkeypatch.setattr(
        twofa_provider,
        "get_user_setting_value",
        lambda *_args, **_kwargs: "original-secret",
    )
    monkeypatch.setattr(twofa_provider, "_totp_throttle_status", lambda *_args: None)
    monkeypatch.setattr(twofa_provider, "_verify_totp_code", lambda *_args: True)
    monkeypatch.setattr(
        twofa_provider,
        "_locked_user_settings",
        lambda *_args: (locked_user, locked_settings),
    )

    result = twofa_provider.verify_setup(
        user,
        "123456",
        None,
        None,
        db,
        provider="totp",
    )

    assert result == {"status": "otp_invalid"}
    assert rollback_calls == [True]
    assert locked_settings["login_2fa"]["enable_2fa"] is False
    assert "2fa_secret" not in locked_settings["secret"]


def test_email_otp_generation_uses_cryptographic_randomness(monkeypatch):
    user = SimpleNamespace(id="user-id", email="user@example.com")
    config = twofa_provider.TwoFAConfig(
        provider="email",
        otp_length=6,
        otp_ttl_seconds=300,
        otp_resend_cooldown_seconds=30,
        otp_max_attempts=5,
    )
    digits = iter("123456")
    queued_messages = []
    settings = deepcopy(DEFAULT_USER_SETTINGS)
    user.settings = settings

    class FakeDb:
        def commit(self):
            return None

        def rollback(self):
            return None

    db = FakeDb()
    cooldown_buckets = []

    monkeypatch.setattr(twofa_provider.secrets, "choice", lambda choices: next(digits))
    monkeypatch.setattr(twofa_provider, "_get_user_language", lambda user_id, db: "en")
    monkeypatch.setattr(twofa_provider, "_get_hash_secret", lambda db: "hash-secret")
    monkeypatch.setattr(twofa_provider, "get_email_security_action_epoch", lambda db: "test-epoch")
    monkeypatch.setattr(twofa_provider, "_locked_user_settings", lambda *_args: (user, settings))
    monkeypatch.setattr(twofa_provider, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        twofa_provider,
        "consume_email_security_cooldown",
        lambda _db, *, bucket, **_kwargs: cooldown_buckets.append(bucket) or 0,
    )
    monkeypatch.setattr(twofa_provider, "load_login_email_delivery_config", lambda *_args: object())
    monkeypatch.setattr(twofa_provider, "is_email_delivery_config_ready", lambda *_args: True)
    monkeypatch.setattr(email_models, "enqueue_email", lambda *args, **kwargs: queued_messages.append(kwargs))

    twofa_provider._issue_delivery_otp(
        user,
        "email",
        "login",
        "stale@example.com",
        config,
        db,
    )

    assert queued_messages[0]["payload"]["code"] == "123456"
    assert queued_messages[0]["template_type"] == "twofa_otp"
    assert queued_messages[0]["recipient"] == "user@example.com"
    assert cooldown_buckets == ["twofa-otp:user-id:email:login"]
    assert settings["secret"]["2fa_otp_hash"] == twofa_provider._hash_delivery_otp(
        "123456",
        user.id,
        "email",
        "login",
        db,
    )


def test_email_otp_cooldown_is_isolated_by_purpose(monkeypatch):
    settings = deepcopy(DEFAULT_USER_SETTINGS)
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        settings=settings,
    )
    config = twofa_provider.TwoFAConfig(
        provider="email",
        otp_length=6,
        otp_ttl_seconds=300,
        otp_resend_cooldown_seconds=30,
        otp_max_attempts=5,
    )
    queued_messages = []
    digits = iter("654321")

    class FakeDb:
        def commit(self):
            return None

        def rollback(self):
            return None

    db = FakeDb()
    monkeypatch.setattr(twofa_provider.secrets, "choice", lambda _choices: next(digits))
    monkeypatch.setattr(twofa_provider, "_get_user_language", lambda *_args: "en")
    monkeypatch.setattr(twofa_provider, "_get_hash_secret", lambda _db: "hash-secret")
    monkeypatch.setattr(
        twofa_provider,
        "get_email_security_action_epoch",
        lambda _db: "test-epoch",
    )
    monkeypatch.setattr(
        twofa_provider,
        "_locked_user_settings",
        lambda *_args: (user, settings),
    )
    monkeypatch.setattr(twofa_provider, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        twofa_provider,
        "load_login_email_delivery_config",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        twofa_provider,
        "is_email_delivery_config_ready",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        twofa_provider,
        "consume_email_security_cooldown",
        lambda _db, *, bucket, **_kwargs: 20 if bucket.endswith(":login") else 0,
    )
    monkeypatch.setattr(
        email_models,
        "enqueue_email",
        lambda *args, **kwargs: queued_messages.append(kwargs),
    )

    assert twofa_provider._issue_delivery_otp(
        user, "email", "login", user.email, config, db
    )[0] == 20
    assert twofa_provider._issue_delivery_otp(
        user, "email", "step_up", user.email, config, db
    )[0] == 0
    assert [row["payload"]["purpose"] for row in queued_messages] == ["step_up"]


def test_email_delivery_otp_is_consumed_once(monkeypatch):
    settings = deepcopy(DEFAULT_USER_SETTINGS)
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        settings=settings,
    )
    monkeypatch.setattr(twofa_provider, "_get_hash_secret", lambda db_arg: "hash-secret")
    monkeypatch.setattr(twofa_provider, "get_email_security_action_epoch", lambda db: "test-epoch")
    settings["secret"].update(
        {
            "2fa_otp_hash": twofa_provider._hash_delivery_otp(
                "123456",
                user.id,
                "email",
                "login",
                object(),
            ),
            "2fa_otp_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "2fa_otp_attempts": 0,
            "2fa_otp_purpose": "login",
            "2fa_otp_provider": "email",
            "2fa_otp_destination": "user@example.com",
        }
    )

    class FakeQuery:
        def __init__(self, db):
            self.db = db

        def populate_existing(self):
            return self

        def filter(self, *_args):
            return self

        def with_for_update(self):
            self.db.lock_count += 1
            return self

        def first(self):
            return self.db.user

    class FakeDb:
        def __init__(self, locked_user):
            self.user = locked_user
            self.lock_count = 0
            self.commit_count = 0

        def query(self, *_args):
            return FakeQuery(self)

        def flush(self):
            pass

        def commit(self):
            self.commit_count += 1

    db = FakeDb(user)
    monkeypatch.setattr(
        twofa_provider,
        "get_global_2fa_config",
        lambda db_arg: twofa_provider.TwoFAConfig(
            provider="email",
            otp_length=6,
            otp_ttl_seconds=300,
            otp_resend_cooldown_seconds=30,
            otp_max_attempts=5,
        ),
    )
    monkeypatch.setattr(twofa_provider, "flag_modified", lambda *_args: None)

    assert twofa_provider._consume_delivery_code(
        user,
        "email",
        "step_up",
        "123456",
        db,
    ) is False
    assert user.settings["secret"]["2fa_otp_hash"]

    assert twofa_provider._consume_delivery_code(user, "email", "login", "123456", db) is True
    assert user.settings["secret"]["2fa_otp_hash"] == ""

    assert twofa_provider._consume_delivery_code(user, "email", "login", "123456", db) is False
    assert db.lock_count == 3


def test_email_delivery_otp_is_invalidated_when_current_email_changes(monkeypatch):
    settings = deepcopy(DEFAULT_USER_SETTINGS)
    settings["secret"].update(
        {
            "2fa_otp_hash": "stored-hash",
            "2fa_otp_expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat(),
            "2fa_otp_attempts": 0,
            "2fa_otp_purpose": "login",
            "2fa_otp_provider": "email",
            "2fa_otp_destination": "old@example.com",
        }
    )
    locked_user = SimpleNamespace(
        id="user-id",
        email="new@example.com",
        settings=settings,
    )

    class FakeDb:
        commit_count = 0

        def commit(self):
            self.commit_count += 1

    db = FakeDb()
    monkeypatch.setattr(
        twofa_provider,
        "_locked_user_settings",
        lambda *_args: (locked_user, settings),
    )
    monkeypatch.setattr(twofa_provider, "flag_modified", lambda *_args: None)

    assert twofa_provider._consume_delivery_code(
        locked_user,
        "email",
        "login",
        "123456",
        db,
    ) is False
    assert settings["secret"]["2fa_otp_hash"] == ""
    assert db.commit_count == 1


def test_pending_login_token_settings_are_declared_in_defaults():
    required_keys = {
        "secret": {
            "signin_pending_token",
            "signin_pending_token_expires_at",
            "signin_pending_setup_material_allowed",
            "passkey_pending_token",
            "passkey_pending_token_expires_at",
            "passkey_pending_setup_material_allowed",
        },
        "social_login": {
            "pending_social_token",
            "pending_social_token_expires",
            "pending_provider",
            "pending_setup_material_allowed",
        },
        "sso_login": {
            "pending_sso_token",
            "pending_sso_token_expires",
            "pending_provider_type",
            "pending_setup_material_allowed",
        },
    }

    for page, keys in required_keys.items():
        assert keys <= set(DEFAULT_USER_SETTINGS[page])


def test_password_signin_2fa_setup_sets_pending_signin_cookie(monkeypatch):
    user = SimpleNamespace(id="user-id")
    response = object()
    cookie_calls = []

    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "evaluate_login_2fa",
        lambda *_args, **_kwargs: {
            "status": "otp_setup",
            "provider": "totp",
            "setup_material_available": True,
        },
    )
    monkeypatch.setattr(
        auth_utils,
        "_set_pending_signin_token",
        lambda user_id, db, allow_setup_material=False: (
            "signin-token",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
    )
    monkeypatch.setattr(
        auth_utils,
        "_set_one_time_browser_cookie",
        lambda response_obj, key, value, db, request, max_age=300: cookie_calls.append(
            (response_obj, key, value, max_age)
        ),
    )

    result = auth_utils._complete_signin_for_user(
        object(),
        object(),
        SimpleNamespace(headers={}, client=None),
        response,
        user,
        otp_code=None,
        otp_action=None,
        otp_destination=None,
        log_event="signin",
        success_message="ok",
    )

    assert result["status"] == "otp_setup"
    assert cookie_calls == [(response, "signin_login_token", "signin-token", pytest.approx(300, abs=2))]


def test_password_signin_existing_2fa_does_not_set_setup_material_cookie(monkeypatch):
    user = SimpleNamespace(id="user-id")
    response = object()
    cookie_calls = []

    monkeypatch.setattr(auth_utils, "validate_user_login_eligibility", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth_utils,
        "evaluate_login_2fa",
        lambda *_args, **_kwargs: {
            "status": "otp_required_already_setup",
            "provider": "totp",
        },
    )
    monkeypatch.setattr(
        auth_utils,
        "_set_pending_signin_token",
        lambda *_args, **_kwargs: pytest.fail("verify-only sign-in must not create setup material tokens"),
    )
    monkeypatch.setattr(
        auth_utils,
        "_set_one_time_browser_cookie",
        lambda *args, **kwargs: cookie_calls.append((args, kwargs)),
    )

    result = auth_utils._complete_signin_for_user(
        object(),
        object(),
        SimpleNamespace(headers={}, client=None),
        response,
        user,
        otp_code=None,
        otp_action=None,
        otp_destination=None,
        log_event="signin",
        success_message="ok",
    )

    assert result["status"] == "otp_required_already_setup"
    assert cookie_calls == []


def test_pending_token_is_active_accepts_naive_utc_timestamp(monkeypatch):
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
    monkeypatch.setattr(auth_utils, "get_user_setting_value", lambda *_args, **_kwargs: expires_at.isoformat())

    assert auth_utils._pending_token_is_active("user-id", "secret", "signin_pending_token_expires_at", object()) is True


def test_pending_token_is_active_rejects_expired_timestamp(monkeypatch):
    expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    monkeypatch.setattr(auth_utils, "get_user_setting_value", lambda *_args, **_kwargs: expires_at.isoformat())

    assert auth_utils._pending_token_is_active("user-id", "secret", "signin_pending_token_expires_at", object()) is False


def test_pending_token_allows_setup_material_requires_explicit_flag(monkeypatch):
    monkeypatch.setattr(auth_utils, "get_user_setting_value", lambda *_args, **_kwargs: "true")

    assert auth_utils._pending_token_allows_setup_material("user-id", "secret", "allow", object()) is True

    monkeypatch.setattr(auth_utils, "get_user_setting_value", lambda *_args, **_kwargs: "")

    assert auth_utils._pending_token_allows_setup_material("user-id", "secret", "allow", object()) is False
