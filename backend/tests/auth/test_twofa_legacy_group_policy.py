from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import twofa_provider
from app.auth.email_localization import SUPPORTED_EMAIL_LANGUAGES, get_email_copy
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.settings_validation import normalize_group_settings
from app.users import utils as user_utils


def test_login_ignores_removed_group_2fa_policy(monkeypatch):
    user = SimpleNamespace(id="user-1", email="user@example.com")

    def fake_login_general(_db, key, default=None):
        return {
            "enable_2fa": True,
            "force_2fa": False,
            "twofa_provider": "totp",
        }.get(key, default)

    monkeypatch.setattr(twofa_provider, "_get_login_general_value", fake_login_general)
    monkeypatch.setattr(twofa_provider, "ensure_provider_alignment", lambda *args, **kwargs: None)
    monkeypatch.setattr(twofa_provider, "_requires_provider_migration", lambda *args, **kwargs: False)
    monkeypatch.setattr(twofa_provider, "_is_user_enrolled_for_provider", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        twofa_provider,
        "begin_setup",
        lambda *_args, **_kwargs: pytest.fail("relaxed global policy must not force 2FA setup"),
    )

    result = twofa_provider.evaluate_login_2fa(user, None, None, None, object())

    assert result is None


def test_disabled_global_2fa_does_not_block_deactivation_with_stale_force_flag(monkeypatch):
    settings = {
        "login_2fa": {"enable_2fa": True, "provider": "totp"},
        "secret": {"2fa_secret": "secret"},
    }
    user = SimpleNamespace(id="user-1", email="user@example.com", settings=settings)
    calls = []
    db = SimpleNamespace(
        commit=lambda: calls.append("commit"),
        rollback=lambda: calls.append("rollback"),
    )

    monkeypatch.setattr(
        twofa_provider,
        "_get_login_general_value",
        lambda _db, key, default=None: {
            "enable_2fa": False,
            "force_2fa": True,
        }.get(key, default),
    )
    monkeypatch.setattr(twofa_provider, "_locked_user_settings", lambda *_args: (user, settings))
    monkeypatch.setattr(twofa_provider, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        twofa_provider,
        "delete_user_transient_auth_state",
        lambda *_args, **_kwargs: calls.append("invalidate") or 0,
    )
    monkeypatch.setattr(twofa_provider, "_send_twofa_deactivated_email", lambda *_args: "queued")

    result = twofa_provider.deactivate(user, db)

    assert calls == ["invalidate", "commit"]
    assert settings["login_2fa"]["enable_2fa"] is False
    assert settings["secret"]["2fa_secret"] == ""
    assert result == {"status": "success", "security_notification": "queued"}


def test_enabled_and_forced_global_2fa_blocks_deactivation(monkeypatch):
    user = SimpleNamespace(id="user-1")

    monkeypatch.setattr(
        twofa_provider,
        "_get_login_general_value",
        lambda _db, key, default=None: {
            "enable_2fa": True,
            "force_2fa": True,
        }.get(key, default),
    )

    with pytest.raises(HTTPException) as exc_info:
        twofa_provider.deactivate(user, object())

    assert exc_info.value.status_code == 409


def test_deactivation_clears_twofa_before_sending_security_notification(monkeypatch):
    settings = {
        "login_2fa": {"enable_2fa": True, "provider": "totp"},
        "secret": {"2fa_secret": "secret"},
    }
    user = SimpleNamespace(id="user-1", email="user@example.com", settings=settings)
    calls = []
    db = SimpleNamespace(
        commit=lambda: calls.append("commit"),
        rollback=lambda: calls.append("rollback"),
    )

    monkeypatch.setattr(twofa_provider, "_get_login_general_value", lambda _db, _key, default=None: default)
    monkeypatch.setattr(twofa_provider, "_locked_user_settings", lambda *_args: (user, settings))
    monkeypatch.setattr(twofa_provider, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        twofa_provider,
        "delete_user_transient_auth_state",
        lambda *_args, **_kwargs: calls.append("invalidate") or 0,
    )

    def stage_notification(*_args):
        assert settings["login_2fa"]["enable_2fa"] is False
        assert settings["secret"]["2fa_secret"] == ""
        calls.append("notify")
        return "queued"

    monkeypatch.setattr(
        twofa_provider,
        "_send_twofa_deactivated_email",
        stage_notification,
    )

    result = twofa_provider.deactivate(user, db)

    assert calls == ["invalidate", "notify", "commit"]
    assert result == {"status": "success", "security_notification": "queued"}


def test_twofa_deactivation_email_is_staged_in_the_durable_outbox(monkeypatch):
    user = SimpleNamespace(id="user-1", email="user@example.com")
    staged = []

    monkeypatch.setattr(
        "app.email.service.enqueue_security_event",
        lambda db, **kwargs: staged.append((db, kwargs)) or object(),
    )

    db = object()
    assert twofa_provider._send_twofa_deactivated_email(user, db) == "queued"
    assert staged == [
        (
            db,
            {
                "user": user,
                "event_type": "twofa_disabled",
            },
        )
    ]

    monkeypatch.setattr(
        "app.email.service.enqueue_security_event",
        lambda *_args, **_kwargs: None,
    )
    assert twofa_provider._send_twofa_deactivated_email(user, object()) == "skipped"


def test_all_email_locales_include_twofa_deactivation_security_copy():
    required_keys = {
        "deactivated_subject",
        "deactivated_html_title",
        "deactivated_headline",
        "deactivated_body",
        "deactivated_action",
        "deactivated_plain_text_body",
    }

    for language_code in SUPPORTED_EMAIL_LANGUAGES:
        assert required_keys <= get_email_copy("twofa", language_code).keys()


def test_group_settings_normalization_removes_group_2fa_policy():
    changed, normalized = normalize_group_settings(
        {
            "login_2fa": {
                "enable_2fa": True,
                "force_2fa": True,
                "twofa_provider": "email",
            }
        }
    )

    assert changed is True
    assert "login_2fa" not in DEFAULT_GROUP_SETTINGS
    assert "login_2fa" not in normalized


def test_user_settings_policy_flags_use_only_global_2fa_settings(monkeypatch):
    values = {
        ("login_general", "enable_2fa"): False,
        ("login_general", "force_2fa"): True,
    }
    monkeypatch.setattr(
        user_utils,
        "get_value_by_page_and_key",
        lambda page, key, _db: values[(page, key)],
    )
    monkeypatch.setattr(
        user_utils,
        "get_user_group_setting_value",
        lambda *_args: pytest.fail("group 2FA settings must not be read"),
    )

    assert user_utils.get_effective_two_factor_enabled("user-1", object()) is False
    assert user_utils.get_effective_two_factor_forced("user-1", object()) is False


def test_login_settings_validation_clears_force_when_2fa_master_switch_is_disabled():
    from app.settings.validation import validate_settings_page_values

    settings = validate_settings_page_values(
        "login_general",
        {"enable_2fa": False},
        current_values={"enable_2fa": True, "force_2fa": True},
    )

    assert settings["enable_2fa"] is False
    assert settings["force_2fa"] is False
