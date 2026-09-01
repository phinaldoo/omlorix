import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils
from app.auth.ldap import LDAPAuthenticatedUser


def _ldap_user(directory_user_id: str = "incoming-directory-id") -> LDAPAuthenticatedUser:
    return LDAPAuthenticatedUser(
        identifier="alice",
        dn="cn=alice,dc=example,dc=com",
        directory_user_id=directory_user_id,
        email="alice@example.com",
        first_name="Alice",
        last_name="Example",
        display_name="Alice Example",
        username="alice",
        groups=[],
        raw_attributes={},
    )


def test_linked_ldap_account_rejects_directory_identity_mismatch(monkeypatch):
    db = MagicMock()
    db_log = MagicMock()
    user = SimpleNamespace(id="user-1", email="alice@example.com", role="user", group_id="default")

    monkeypatch.setattr(
        auth_utils,
        "get_ldap_provider",
        lambda _db: SimpleNamespace(settings={"ldap_link_existing_users_by_email": True}),
    )
    monkeypatch.setattr(auth_utils, "_enforce_ldap_required_groups", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "_resolve_ldap_group_target", lambda *_args, **_kwargs: "default")
    monkeypatch.setattr(auth_utils, "_resolve_ldap_role", lambda *_args, **_kwargs: "user")
    monkeypatch.setattr(auth_utils, "_find_existing_user_for_ldap", lambda *_args, **_kwargs: user)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("ldap_login", "linked"): True,
            ("ldap_login", "directory_user_id"): "stored-directory-id",
            ("ldap_login", "directory_dn"): "cn=alice,dc=example,dc=com",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(
        auth_utils,
        "_sync_existing_user_from_ldap",
        lambda *_args, **_kwargs: pytest.fail("mismatched LDAP identity must not sync the existing user"),
    )
    monkeypatch.setattr(
        auth_utils,
        "_link_user_to_ldap",
        lambda *_args, **_kwargs: pytest.fail("mismatched LDAP identity must not overwrite the stored link"),
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_utils._provision_or_sync_ldap_user(db, db_log, _ldap_user(), SimpleNamespace(headers={}))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "The linked LDAP account does not match this user."


def test_linked_ldap_account_accepts_matching_directory_identity(monkeypatch):
    db = MagicMock()
    db_log = MagicMock()
    user = SimpleNamespace(id="user-1", email="alice@example.com", role="user", group_id="default")
    synced = MagicMock()
    linked = MagicMock()

    monkeypatch.setattr(
        auth_utils,
        "get_ldap_provider",
        lambda _db: SimpleNamespace(settings={"ldap_link_existing_users_by_email": True}),
    )
    monkeypatch.setattr(auth_utils, "_enforce_ldap_required_groups", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_utils, "_resolve_ldap_group_target", lambda *_args, **_kwargs: "default")
    monkeypatch.setattr(auth_utils, "_resolve_ldap_role", lambda *_args, **_kwargs: "user")
    monkeypatch.setattr(auth_utils, "_find_existing_user_for_ldap", lambda *_args, **_kwargs: user)
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db: {
            ("ldap_login", "linked"): True,
            ("ldap_login", "directory_user_id"): "stored-directory-id",
        }.get((page, key), ""),
    )
    monkeypatch.setattr(auth_utils, "_sync_existing_user_from_ldap", synced)
    monkeypatch.setattr(auth_utils, "_link_user_to_ldap", linked)

    result_user, is_new_user = auth_utils._provision_or_sync_ldap_user(
        db,
        db_log,
        _ldap_user("stored-directory-id"),
        SimpleNamespace(headers={}),
    )

    assert result_user is user
    assert is_new_user is False
    synced.assert_called_once()
    linked.assert_called_once_with(db, user, ANY)
