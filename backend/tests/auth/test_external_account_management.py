from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.auth import passkeys, twofa_provider
from app.users import external_management
from app.users.external_management import (
    is_externally_managed,
    is_externally_managed_setting_hidden,
    mark_user_externally_managed,
    require_externally_managed_settings_update_allowed,
    require_locally_managed_account,
)


def _user(**overrides):
    values = {
        "id": "user-1",
        "email": "managed@example.com",
        "auth_management_mode": "local",
        "external_auth_provider": None,
        "externally_managed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_mark_external_management_revokes_local_sessions(monkeypatch):
    """The local-to-enterprise transition is durable and invalidates bypass sessions."""

    from app.auth import models as auth_models
    from app.auth import session_store
    from app.email import change as email_change
    from app.email import models as email_models
    from app.users import init as user_settings

    update_settings = Mock(return_value={})
    delete_sessions = Mock()
    delete_transient_state = Mock()
    invalidate_resets = Mock()
    cancel_email_changes = Mock()
    cancel_email = Mock()
    revoke_cache = Mock()
    monkeypatch.setattr(
        external_management,
        "_lock_user_for_external_management",
        lambda _db, _user_id: user,
    )
    monkeypatch.setattr(user_settings, "update_user_settings_bulk", update_settings)
    monkeypatch.setattr(auth_models, "delete_authentication_all", delete_sessions)
    monkeypatch.setattr(
        auth_models,
        "delete_user_transient_auth_state",
        delete_transient_state,
    )
    monkeypatch.setattr(
        auth_models,
        "invalidate_user_password_reset_tokens",
        invalidate_resets,
    )
    monkeypatch.setattr(email_change, "cancel_pending_email_changes", cancel_email_changes)
    monkeypatch.setattr(email_models, "cancel_user_email", cancel_email)
    monkeypatch.setattr(session_store, "revoke_user_sessions", revoke_cache)

    db = Mock()
    user = _user()

    changed = mark_user_externally_managed(db, user, "OIDC")

    assert changed is True
    assert is_externally_managed(user) is True
    assert user.external_auth_provider == "oidc"
    assert user.externally_managed_at is not None
    update_settings.assert_called_once_with(
        user.id,
        {
            "security": {"has_to_change_password": False},
            "social_login": {"needs_password_setup": False},
            "sso_login": {"needs_password_setup": False},
        },
        db,
        commit=False,
    )
    delete_sessions.assert_called_once_with(
        db,
        user.id,
        commit=False,
        revoke_cached=False,
    )
    delete_transient_state.assert_called_once_with(db, user.id, commit=False)
    invalidate_resets.assert_called_once_with(db, user.id, commit=False)
    cancel_email_changes.assert_called_once_with(db, user.id)
    cancel_email.assert_called_once_with(
        db,
        user.id,
        preserve_template_types=("security_event",),
        commit=False,
    )
    revoke_cache.assert_called_once_with(user.id)


def test_scim_sync_preserves_concrete_enterprise_provider(monkeypatch):
    """SCIM lifecycle sync must not replace an already known OIDC sign-in label."""

    from app.users import init as user_settings

    monkeypatch.setattr(user_settings, "update_user_settings_bulk", Mock(return_value={}))
    db = Mock()
    user = _user(
        auth_management_mode="external",
        external_auth_provider="oidc",
        externally_managed_at=object(),
    )
    monkeypatch.setattr(
        external_management,
        "_lock_user_for_external_management",
        lambda _db, _user_id: user,
    )

    changed = mark_user_externally_managed(db, user, "scim")

    assert changed is False
    assert user.external_auth_provider == "oidc"


def test_managed_account_rejects_local_controls():
    with pytest.raises(HTTPException) as exc_info:
        require_locally_managed_account(_user(auth_management_mode="external"))

    assert exc_info.value.status_code == 403
    assert "managed by your organization" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    ("page", "field"),
    [
        ("login_2fa", None),
        ("social_login", None),
        ("sso_login", None),
        ("scim", None),
        ("ldap_login", None),
        ("security", "has_to_change_password"),
    ],
)
def test_managed_admin_auth_settings_are_hidden(page, field):
    assert is_externally_managed_setting_hidden(page, field) is True


def test_managed_admin_auth_settings_updates_are_rejected():
    with pytest.raises(HTTPException) as exc_info:
        require_externally_managed_settings_update_allowed(
            _user(auth_management_mode="external"),
            {"social_login": {"google_linked": True}},
        )

    assert exc_info.value.status_code == 409


@pytest.mark.parametrize(
    "field",
    ["email", "first_name", "last_name", "password", "wrong_sign_in_attempts"],
)
def test_admin_cannot_change_managed_identity_or_local_auth_fields(field):
    from app.users import utils as user_utils

    managed_user = _user(auth_management_mode="external")
    db = Mock()
    db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = managed_user
    values = {
        "user_id": "user-1",
        "email": None,
        "first_name": None,
        "last_name": None,
        "group_id": None,
        "password": None,
        "wrong_sign_in_attempts": None,
        "lock": None,
    }
    values[field] = "changed" if field != "wrong_sign_in_attempts" else 0
    payload = SimpleNamespace(**values)

    with pytest.raises(HTTPException) as exc_info:
        user_utils.admin_update_user_profile(payload, db)

    assert exc_info.value.status_code == 409


def test_managed_account_is_exempt_from_local_twofa_policy():
    user = _user(auth_management_mode="external")

    assert twofa_provider.evaluate_login_2fa(
        user,
        otp_code=None,
        otp_action=None,
        otp_destination=None,
        db=Mock(),
    ) is None
    assert twofa_provider.get_login_2fa_session_policy(user, Mock())["required"] is False


def test_managed_account_cannot_begin_passkey_registration(monkeypatch):
    monkeypatch.setattr(passkeys, "get_passkey_policy", lambda _db: {"enable_passkeys": True})
    # Supply a valid relying-party configuration so this focused policy test
    # reaches the externally-managed-account guard instead of failing earlier
    # on unrelated deployment settings.
    monkeypatch.setattr(
        passkeys,
        "_resolve_webauthn_config_for_origin",
        lambda _db, public_origin=None: (
            "example.com",
            "Omlorix",
            public_origin or "https://example.com",
        ),
    )
    monkeypatch.setattr(
        passkeys,
        "get_user",
        lambda _db, _user_id: _user(auth_management_mode="external"),
    )

    with pytest.raises(HTTPException) as exc_info:
        passkeys.begin_registration(Mock(), user_id="user-1")

    assert exc_info.value.status_code == 403
