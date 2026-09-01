from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from _otel_test_stubs import install_otel_stubs

install_otel_stubs()

from app.admin.users import router as admin_router
from app.admin.users import settings_schema as admin_user_settings_schema
from app.groups import init as groups_init
from app.llm import utils as llm_utils
from app.users import init as users_init


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host="198.51.100.10"),
        headers={"user-agent": "pytest"},
    )


def test_build_user_settings_schema_omits_secret_page(monkeypatch):
    monkeypatch.setattr(
        groups_init, "get_user_group_setting_value", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(llm_utils, "list_user_models", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        users_init,
        "get_user_settings",
        lambda *_args, **_kwargs: {
            "login_2fa": {"enable_2fa": True, "provider": "totp"},
            "secret": {"2fa_secret": "should-not-leak"},
        },
    )

    schema = admin_user_settings_schema.build_user_settings_schema(
        object(), True, "user-1"
    )
    sections = schema.sections
    page_keys = [section.key for section in sections]

    assert isinstance(schema, admin_user_settings_schema.UserSettingsFormSchema)
    assert list(schema.model_dump()) == ["sections"]
    assert "secret" not in page_keys
    descriptions = {section.key: section.i18n_description for section in sections}
    assert descriptions["security"] == "admin_user_settings_page_security_desc"
    assert descriptions["general"] == "admin_user_settings_page_general_desc"
    assert descriptions["appearance"] == "admin_user_settings_page_appearance_desc"
    assert descriptions["chat"] == "admin_user_settings_page_chat_desc"
    login_2fa_section = next(
        section for section in sections if section.key == "login_2fa"
    )
    login_2fa_payload = login_2fa_section.model_dump()
    assert login_2fa_section.title == "Two-Factor Authentication"
    assert login_2fa_section.i18n_title == "us_security_2fa_title"
    assert "label" not in login_2fa_payload
    assert "i18n_label" not in login_2fa_payload
    values_by_key = {field.key: field.value for field in login_2fa_section.fields}
    assert values_by_key["enable_2fa"] is True
    assert values_by_key["provider"] == "totp"


def test_byok_retention_days_depends_on_statistics_toggle(monkeypatch):
    monkeypatch.setattr(
        groups_init, "get_user_group_setting_value", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(llm_utils, "list_user_models", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(users_init, "get_user_settings", lambda *_args, **_kwargs: {})

    schema = admin_user_settings_schema.build_user_settings_schema(
        object(), False, "user-1"
    )
    chat_section = next(section for section in schema.sections if section.key == "chat")
    retention_field = next(
        field
        for field in chat_section.fields
        if field.key == "byok_statistics_retention_days"
    )

    assert retention_field.dependency == "byok_statistics_enabled"
    assert retention_field.dependency_value is True


def test_managed_user_schema_omits_external_identity_and_auth_settings(monkeypatch):
    monkeypatch.setattr(
        groups_init, "get_user_group_setting_value", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(llm_utils, "list_user_models", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(users_init, "get_user_settings", lambda *_args, **_kwargs: {})

    schema = admin_user_settings_schema.build_user_settings_schema(
        object(),
        False,
        "user-1",
        externally_managed=True,
    )

    pages = {section.key: section for section in schema.sections}
    assert {"login_2fa", "social_login", "sso_login", "scim", "ldap_login"}.isdisjoint(pages)
    assert "has_to_change_password" not in {
        field.key for field in pages["security"].fields
    }


def test_update_user_settings_bulk_rejects_secret_page_when_disabled(monkeypatch):
    monkeypatch.setattr(
        users_init,
        "get_user_settings",
        lambda *_args, **_kwargs: {"secret": {"2fa_secret": ""}},
    )
    monkeypatch.setattr(users_init, "get_user", lambda *_args, **_kwargs: SimpleNamespace(settings={}))

    with pytest.raises(HTTPException) as excinfo:
        users_init.update_user_settings_bulk(
            "user-1",
            {"secret": {"2fa_secret": "new-secret"}},
            object(),
            allow_secret_page=False,
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "The 'secret' settings page cannot be updated via this endpoint."


def test_admin_update_user_settings_route_disables_secret_page_updates(monkeypatch):
    bulk_calls: list[dict] = []
    monkeypatch.setattr(
        admin_router,
        "get_user",
        lambda _db, user_id: SimpleNamespace(id=user_id, role="user", is_active=True),
    )
    monkeypatch.setattr(
        admin_router,
        "update_user_settings_bulk",
        lambda user_id, settings, db, **kwargs: bulk_calls.append(
            {
                "user_id": user_id,
                "settings": settings,
                "allow_secret_page": kwargs.get("allow_secret_page"),
            }
        ) or {"login_2fa": {"enable_2fa": True}},
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **_kwargs: None)

    response = admin_router.admin_update_user_settings(
        payload=SimpleNamespace(user_id="user-1", settings={"login_2fa": {"enable_2fa": True}}),
        request=_request(),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert response.status == "success"
    assert response.updated == {"login_2fa": {"enable_2fa": True}}
    assert bulk_calls == [
        {
            "user_id": "user-1",
            "settings": {"login_2fa": {"enable_2fa": True}},
            "allow_secret_page": False,
        }
    ]


def test_admin_update_user_settings_blocks_managed_auth_pages(monkeypatch):
    bulk_calls: list[dict] = []
    monkeypatch.setattr(
        admin_router,
        "get_user",
        lambda _db, user_id: SimpleNamespace(
            id=user_id,
            role="user",
            is_active=True,
            auth_management_mode="external",
        ),
    )
    monkeypatch.setattr(
        admin_router,
        "update_user_settings_bulk",
        lambda *_args, **_kwargs: bulk_calls.append({"called": True}),
    )

    with pytest.raises(HTTPException) as excinfo:
        admin_router.admin_update_user_settings(
            payload=SimpleNamespace(
                user_id="user-1",
                settings={"login_2fa": {"enable_2fa": True}},
            ),
            request=_request(),
            db=object(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )

    assert excinfo.value.status_code == 409
    assert bulk_calls == []


def test_admin_reset_user_twofa_route_clears_state_and_audits(monkeypatch):
    cleared: list[tuple[str, object]] = []
    audit_calls: list[dict] = []
    db = object()
    db_log = object()
    monkeypatch.setattr(admin_router, "get_user", lambda inner_db, user_id: SimpleNamespace(id=user_id, role="user"))
    monkeypatch.setattr(admin_router, "clear_user_twofa_state", lambda user_id, inner_db: cleared.append((user_id, inner_db)))
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    response = admin_router.admin_reset_user_twofa_route(
        payload=SimpleNamespace(user_id="user-1", reason="Investigating lockout"),
        request=_request(),
        db=db,
        db_log=db_log,
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert response == {"status": "success"}
    assert cleared == [("user-1", db)]
    assert audit_calls == [
        {
            "db_log": db_log,
            "user_id": "admin-1",
            "action": "RESET_USER_2FA",
            "reason": "Investigating lockout",
            "details": {"target_user": "user-1"},
            "ip_address": "198.51.100.10",
            "user_agent": "pytest",
            "category": "admin",
        }
    ]


def test_admin_reset_user_twofa_blocks_managed_accounts(monkeypatch):
    clear_calls: list[str] = []
    monkeypatch.setattr(
        admin_router,
        "get_user",
        lambda _db, user_id: SimpleNamespace(
            id=user_id,
            role="user",
            auth_management_mode="external",
        ),
    )
    monkeypatch.setattr(
        admin_router,
        "clear_user_twofa_state",
        lambda user_id, _db: clear_calls.append(user_id),
    )

    with pytest.raises(HTTPException) as excinfo:
        admin_router.admin_reset_user_twofa_route(
            payload=SimpleNamespace(user_id="user-1", reason="Test"),
            request=_request(),
            db=object(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )

    assert excinfo.value.status_code == 403
    assert clear_calls == []
