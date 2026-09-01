from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings import router as admin_router
from app.admin.settings import utils as admin_utils
from app.auth.ldap_transport import (
    LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY,
    LDAP_TRANSPORT_SECURITY_ADMIN_ERROR_DETAIL,
)
from app.settings.defaults import DEFAULT_SETTINGS
from app.settings import utils as settings_utils


class _DummyDB:
    def commit(self) -> None:
        return None

    def refresh(self, _record) -> None:
        return None


def _install_login_ldap_settings_mocks(
    monkeypatch, settings_data: dict
) -> SimpleNamespace:
    settings_record = SimpleNamespace(data=settings_data, updated_at=None)

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record if page == "login_ldap" else None,
    )
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin_utils, "invalidate_settings_cache", lambda: None)

    return settings_record


def test_update_admin_settings_values_for_page_rejects_plaintext_ldap_without_override(
    monkeypatch,
):
    settings_record = _install_login_ldap_settings_mocks(
        monkeypatch,
        DEFAULT_SETTINGS["login_ldap"].copy(),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.update_admin_settings_values_for_page(
            page="login_ldap",
            payload={
                "enable_ldap": True,
                "ldap_server_uris": ["ldap://ldap.example.com"],
                "ldap_user_base_dn": "dc=example,dc=com",
            },
            db=_DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == LDAP_TRANSPORT_SECURITY_ADMIN_ERROR_DETAIL
    assert settings_record.data[LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY] is False


def test_update_admin_settings_values_for_page_allows_explicit_plaintext_override(
    monkeypatch,
):
    settings_record = _install_login_ldap_settings_mocks(
        monkeypatch,
        DEFAULT_SETTINGS["login_ldap"].copy(),
    )

    changed_keys = admin_utils.update_admin_settings_values_for_page(
        page="login_ldap",
        payload={
            "enable_ldap": True,
            "ldap_server_uris": ["ldap://ldap.example.com"],
            "ldap_user_base_dn": "dc=example,dc=com",
            LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY: True,
        },
        db=_DummyDB(),
    )

    assert LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY in changed_keys
    assert settings_record.data["enable_ldap"] is True
    assert settings_record.data[LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY] is True


def test_update_page_key_value_rejects_plaintext_ldap_without_override(monkeypatch):
    settings_record = SimpleNamespace(
        data=DEFAULT_SETTINGS["login_ldap"].copy(), page_name="login_ldap"
    )

    monkeypatch.setattr(
        settings_utils, "get_settings_page", lambda _db, page: settings_record
    )

    with pytest.raises(HTTPException) as exc_info:
        settings_utils.update_page_key_value_by_page_and_key(
            "login_ldap",
            "enable_ldap",
            True,
            _DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == LDAP_TRANSPORT_SECURITY_ADMIN_ERROR_DETAIL
    assert settings_record.data["enable_ldap"] is False


def test_update_page_key_value_allows_switching_endpoint_pool_to_starttls(monkeypatch):
    settings_record = SimpleNamespace(
        data={
            **DEFAULT_SETTINGS["login_ldap"],
            "enable_ldap": True,
            "ldap_server_uris": ["ldap://ldap.example.com"],
            LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY: False,
        },
        page_name="login_ldap",
    )

    monkeypatch.setattr(
        settings_utils, "get_settings_page", lambda _db, page: settings_record
    )
    monkeypatch.setattr(settings_utils, "flag_modified", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(settings_utils, "invalidate_settings_cache", lambda: None)

    result = settings_utils.update_page_key_value_by_page_and_key(
        "login_ldap",
        "ldap_server_uris",
        [
            "ldap+starttls://ldap.example.com",
            "ldap+starttls://ldap-backup.example.com:1389",
        ],
        _DummyDB(),
    )

    assert result.data["ldap_server_uris"] == [
        "ldap+starttls://ldap.example.com",
        "ldap+starttls://ldap-backup.example.com:1389",
    ]
    assert settings_record.data["enable_ldap"] is True


@pytest.mark.parametrize(
    "endpoints",
    [
        ["ldap://first.example.com", "ldaps://second.example.com"],
        ["https://ldap.example.com"],
        ["ldaps://user:password@ldap.example.com"],
        ["ldaps://ldap.example.com/dc=example,dc=com"],
    ],
)
def test_update_rejects_ambiguous_or_unsafe_ldap_endpoint_pools(monkeypatch, endpoints):
    settings_record = _install_login_ldap_settings_mocks(
        monkeypatch,
        DEFAULT_SETTINGS["login_ldap"].copy(),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.update_admin_settings_values_for_page(
            page="login_ldap",
            payload={"ldap_server_uris": endpoints},
            db=_DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert settings_record.data["ldap_server_uris"] == []


def test_update_rejects_out_of_range_ldap_timeout(monkeypatch):
    settings_record = _install_login_ldap_settings_mocks(
        monkeypatch,
        DEFAULT_SETTINGS["login_ldap"].copy(),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.update_admin_settings_values_for_page(
            page="login_ldap",
            payload={"ldap_connect_timeout_seconds": 0},
            db=_DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert settings_record.data["ldap_connect_timeout_seconds"] == 10


def test_update_admin_settings_values_audits_plaintext_override(monkeypatch):
    audit_calls: list[dict] = []

    monkeypatch.setattr(
        admin_router,
        "update_admin_settings_values_for_page",
        lambda page, payload, db, **_kwargs: [LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY],
    )
    monkeypatch.setattr(
        admin_router,
        "get_settings_page_data",
        lambda _db, _page: {LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY: False},
    )
    monkeypatch.setattr(
        admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs)
    )

    result = admin_router.update_admin_settings_values(
        page="login_ldap",
        request=SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"user-agent": "pytest"},
        ),
        payload={LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY: "false"},
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert result.status == "success"
    assert [call["action"] for call in audit_calls] == [
        "UPDATE_ADMIN_SETTINGS_VALUES",
        "LDAP_INSECURE_PLAINTEXT_BIND_OVERRIDE_UPDATED",
    ]
    assert audit_calls[1]["details"] == {"enabled": False}
