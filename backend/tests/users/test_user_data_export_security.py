import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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

from test_support import ensure_optional_dependency_stubs

ensure_optional_dependency_stubs()

from app.database import Base
from app.agents.models import SharedUserAgentSubscription, UserAgent, UserAgentAsset
from app.auth.models import Authentication
from app.chats.models import ChatMessages, Chats
from app.connections.models import UserConnection
from app.file_folders.models import FileFolders, SharedFileFolderSubscription
from app.mcp.models import TRANSPORT_STDIO
from app.prompts.models import Prompts
from app.projects.models import Project
from app.todos.models import TodoLists, Todos
from app.users import data_export as user_data_export
from app.users import data_import as user_utils
from app.users import utils as legacy_user_utils
from app.users.utils import (
    _export_user_chats,
    _get_skipped_export_only_sections,
    _hydrate_user_settings,
    _query_user_authentication_records,
    _sanitize_existing_user_import_settings,
    _sanitize_self_user_import_settings,
    _sanitize_user_archive_settings,
    _sanitize_user_profile_for_archive,
    _serialize_authentication_record,
)
from app.workers.models import AuditEventSubjectState


class _FakeQuery:
    def __init__(self, rows=None):
        self._rows = rows or []

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, chats=None, messages=None, rows_by_model=None):
        self.chats = chats or []
        self.messages = messages or []
        self.rows_by_model = rows_by_model or {}

    def query(self, model):
        if model is Chats:
            return _FakeQuery(self.chats)
        if model is ChatMessages:
            return _FakeQuery(self.messages)
        return _FakeQuery(self.rows_by_model.get(model, []))


def _user_export_payload(**sections):
    """Build the sole supported user-data import envelope for focused tests."""
    profile = sections.get("user") if isinstance(sections.get("user"), dict) else {}
    return {
        "export_type": user_utils.USER_DATA_EXPORT_TYPE,
        "export_version": user_utils.USER_DATA_EXPORT_VERSION,
        "email": profile.get("email"),
        **sections,
    }


def test_user_archive_import_never_reuses_a_permanently_erased_user_id():
    erased_user_id = "6a1f680a-46c5-4e8f-aaca-0ee7ae715650"
    db = _FakeDb(
        rows_by_model={
            AuditEventSubjectState: [
                SimpleNamespace(erased_at=datetime(2026, 8, 30, tzinfo=timezone.utc))
            ]
        }
    )

    assert (
        user_data_export._resolve_preferred_user_id(
            {"id": erased_user_id},
            {},
            db,
        )
        is None
    )


@pytest.mark.parametrize("version", [None, 0.9, 2.0, "1.0"])
@pytest.mark.parametrize("import_mode", ["admin", "self_service"])
def test_user_data_import_rejects_every_non_current_export_version(
    version, import_mode
):
    """Both wrappers use the same numeric-version archive validator."""
    payload = _user_export_payload(user={"email": "person@example.com"})
    payload["export_version"] = version

    with pytest.raises(HTTPException) as exc_info:
        if import_mode == "admin":
            user_utils.import_user_from_export(payload, {})
        else:
            user_utils.import_user_data_for_existing_user(
                "user-1", payload, {}, db_log=None
            )

    assert exc_info.value.status_code == 400
    assert "Expected '1.0'" in exc_info.value.detail


@pytest.mark.parametrize("import_mode", ["admin", "self_service"])
def test_user_data_import_rejects_removed_messaging_section(import_mode):
    """The 1.0 user-data contract cannot import removed messaging data."""
    payload = _user_export_payload(
        user={"email": "person@example.com"},
        messaging={"messages": []},
    )

    with pytest.raises(HTTPException) as exc_info:
        if import_mode == "admin":
            user_utils.import_user_from_export(payload, {})
        else:
            user_utils.import_user_data_for_existing_user(
                "user-1", payload, {}, db_log=None
            )

    assert exc_info.value.status_code == 400
    assert "Unsupported user-data section 'messaging'" in exc_info.value.detail


def test_authentication_export_omits_live_tokens_and_hashes():
    auth_record = Authentication(
        id="auth-1",
        user_id="user-1",
        device_info="browser",
        ip_address="203.0.113.10",
        access_token="ACCESS_SENTINEL",
        refresh_token="REFRESH_SENTINEL",
        access_token_hash="ACCESS_HASH_SENTINEL",
        refresh_token_hash="REFRESH_HASH_SENTINEL",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_active_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    exported = _serialize_authentication_record(auth_record)

    assert "access_token" not in exported
    assert "refresh_token" not in exported
    assert "access_token_hash" not in exported
    assert "refresh_token_hash" not in exported
    assert exported["id"] == "auth-1"
    assert exported["device_info"] == "browser"
    assert exported["ip_address"] == "203.0.113.10"
    assert exported["created_at"] == "2026-01-01T00:00:00+00:00"
    assert exported["last_active_at"] == "2026-01-02T00:00:00+00:00"


def test_authentication_export_query_avoids_decrypting_redacted_tokens():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE authentication (
                    id VARCHAR PRIMARY KEY,
                    user_id VARCHAR NOT NULL,
                    device_info VARCHAR,
                    ip_address VARCHAR,
                    access_token VARCHAR NOT NULL,
                    refresh_token VARCHAR NOT NULL,
                    access_token_hash VARCHAR(64) NOT NULL,
                    refresh_token_hash VARCHAR(64) NOT NULL,
                    created_at DATETIME NOT NULL,
                    last_active_at DATETIME NOT NULL,
                    step_up_authenticated_at DATETIME,
                    step_up_method VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO authentication (
                    id,
                    user_id,
                    device_info,
                    ip_address,
                    access_token,
                    refresh_token,
                    access_token_hash,
                    refresh_token_hash,
                    created_at,
                    last_active_at,
                    step_up_authenticated_at,
                    step_up_method
                )
                VALUES (
                    'auth-1',
                    'user-1',
                    'browser',
                    '203.0.113.0/24',
                    'not-valid-fernet-ciphertext',
                    'also-not-valid-fernet-ciphertext',
                    'access-hash',
                    'refresh-hash',
                    '2026-01-01 00:00:00',
                    '2026-01-02 00:00:00',
                    NULL,
                    NULL
                )
                """
            )
        )

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        exported = [
            _serialize_authentication_record(row)
            for row in user_data_export._iter_query_rows(
                _query_user_authentication_records("user-1", db)
            )
        ]
    finally:
        db.close()

    assert exported == [
        {
            "id": "auth-1",
            "user_id": "user-1",
            "device_info": "browser",
            "ip_address": "203.0.113.0/24",
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_active_at": "2026-01-02T00:00:00+00:00",
            "step_up_authenticated_at": None,
            "step_up_method": None,
        }
    ]


def test_user_profile_archive_omits_reusable_auth_and_lock_material():
    profile = {
        "id": "user-1",
        "email": "person@example.com",
        "hashed_password": "PASSWORD_HASH_SENTINEL",
        "lock": {"is_locked": True, "reason": "failed_signins"},
        "is_active": False,
        "settings": {
            "secret": {
                "2fa_secret": "TOTP_SEED_SENTINEL",
                "passkey_pending_token": "PASSKEY_TOKEN_SENTINEL",
                "wrong_sign_in_attempts": 4,
            },
            "login_2fa": {"enable_2fa": True, "provider": "totp"},
            "social_login": {
                "google_linked": True,
                "google_user_id": "GOOGLE_USER_SENTINEL",
                "pending_auth_code": "SOCIAL_CODE_SENTINEL",
            },
            "general": {"language": "en"},
        },
    }

    exported = _sanitize_user_profile_for_archive(profile)

    assert exported["id"] == "user-1"
    assert exported["email"] == "person@example.com"
    assert "hashed_password" not in exported
    assert "lock" not in exported
    assert "is_active" not in exported
    assert "secret" not in exported["settings"]
    assert "login_2fa" not in exported["settings"]
    assert "pending_auth_code" not in exported["settings"]["social_login"]
    assert exported["settings"]["social_login"] == {}
    assert "SENTINEL" not in str(exported)


def test_user_settings_archive_omits_mfa_passkey_and_pending_auth_secrets():
    settings = {
        "secret": {
            "2fa_secret": "TOTP_SEED_SENTINEL",
            "passkey_pending_token": "PASSKEY_TOKEN_SENTINEL",
            "wrong_sign_in_attempts": 2,
        },
        "login_2fa": {"enable_2fa": True, "provider": "totp"},
        "social_login": {
            "google_linked": True,
            "google_user_id": "GOOGLE_USER_SENTINEL",
            "github_linked": True,
            "github_user_id": "GITHUB_USER_SENTINEL",
            "slack_linked": True,
            "slack_user_id": "SLACK_USER_SENTINEL",
            "microsoft_linked": True,
            "microsoft_user_id": "MICROSOFT_USER_SENTINEL",
            "apple_linked": True,
            "apple_user_id": "APPLE_USER_SENTINEL",
            "pending_social_token": "SOCIAL_TOKEN_SENTINEL",
            "pending_auth_code": "SOCIAL_CODE_SENTINEL",
            "oauth_profile_picture_sync_disabled": True,
        },
        "sso_login": {
            "oidc_linked": True,
            "pending_sso_token": "SSO_TOKEN_SENTINEL",
        },
        "general": {"language": "en"},
    }

    exported = _sanitize_user_archive_settings(settings)

    assert "secret" not in exported
    assert "login_2fa" not in exported
    assert exported["social_login"] == {"oauth_profile_picture_sync_disabled": True}
    assert exported["sso_login"] == {"oidc_linked": True}
    assert exported["general"] == {"language": "en"}
    assert "SENTINEL" not in str(exported)


def test_created_user_import_settings_force_password_reset_and_mfa_reenrollment():
    settings = _hydrate_user_settings(
        {
            "security": {
                "has_to_change_password": False,
                "profile_visibility": "public",
            },
            "secret": {
                "2fa_secret": "TOTP_SEED_SENTINEL",
                "passkey_pending_token": "PASSKEY_TOKEN_SENTINEL",
            },
            "login_2fa": {"enable_2fa": True, "provider": "totp"},
        },
        require_auth_reset=True,
    )

    assert settings["security"]["has_to_change_password"] is True
    assert settings["security"]["profile_visibility"] == "public"
    assert settings["login_2fa"] == {"enable_2fa": False, "provider": ""}
    assert settings["secret"]["2fa_secret"] == ""
    assert settings["secret"]["passkey_pending_token"] == ""


def test_existing_user_import_settings_do_not_apply_auth_reset_material():
    sanitized = _sanitize_existing_user_import_settings(
        {
            "secret": {
                "2fa_secret": "TOTP_SEED_SENTINEL",
                "passkey_pending_token": "PASSKEY_TOKEN_SENTINEL",
            },
            "login_2fa": {"enable_2fa": True, "provider": "totp"},
            "security": {"profile_visibility": "public"},
            "social_login": {
                "needs_password_setup": True,
                "pending_social_token": "SOCIAL_TOKEN_SENTINEL",
                "google_linked": True,
            },
            "sso_login": {
                "needs_password_setup": True,
                "pending_sso_token": "SSO_TOKEN_SENTINEL",
                "oidc_linked": True,
            },
        }
    )

    assert "secret" not in sanitized
    assert "login_2fa" not in sanitized
    assert sanitized["security"] == {"profile_visibility": "public"}
    assert sanitized["social_login"] == {}
    assert sanitized["sso_login"] == {"oidc_linked": True}
    assert "SENTINEL" not in str(sanitized)


def test_self_import_settings_only_keep_portable_preferences():
    sanitized = _sanitize_self_user_import_settings(
        {
            "security": {
                "has_to_change_password": False,
                "profile_visibility": "public",
                "allow_llm_to_access_personal_information_preset": "custom",
                "allow_llm_to_access_personal_information": {
                    "first_name": True,
                },
            },
            "general": {"language": "de"},
            "appearance": {"theme": "dark"},
            "chat": {"ctrl_enter_to_send": True},
            "memory": {"enabled": False},
            "notifications": {"webhook_url": "https://example.com/hook"},
            "states": {
                "welcome_card_dismissed": True,
                "terms_of_service_accepted_revision": 7,
            },
            "social_login": {
                "google_linked": True,
                "google_user_id": "GOOGLE_USER_SENTINEL",
            },
            "sso_login": {
                "oidc_linked": True,
                "provider_id": "SSO_PROVIDER_SENTINEL",
            },
            "scim": {"external_id": "SCIM_SENTINEL"},
            "ldap_login": {
                "linked": True,
                "directory_user_id": "LDAP_SENTINEL",
            },
        }
    )

    assert sanitized == {
        "security": {
            "profile_visibility": "public",
            "allow_llm_to_access_personal_information_preset": "custom",
            "allow_llm_to_access_personal_information": {
                "first_name": True,
            },
        },
        "general": {"language": "de"},
        "appearance": {"theme": "dark"},
        "chat": {"ctrl_enter_to_send": True},
        "memory": {"enabled": False},
        "states": {"welcome_card_dismissed": True},
    }
    assert "notifications" not in sanitized
    assert "SENTINEL" not in str(sanitized)


def test_profile_export_sanitizer_omits_password_hash():
    exported = user_data_export._sanitize_user_profile_export(
        {
            "email": "user@example.com",
            "hashed_password": "HASH_SENTINEL",
            "group_id": "group-1",
            "role": "admin",
            "is_active": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_active_at": "2026-01-02T00:00:00+00:00",
        }
    )

    assert exported == {"email": "user@example.com"}


def test_user_import_ignores_imported_hash_and_uses_default_password(monkeypatch):
    class EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, *args, **kwargs):
            return EmptyQuery()

    created_call = {}

    def fake_create_user_record(
        db,
        profile_data,
        group_id,
        *,
        hashed_password,
        force_password_change,
        preferred_user_id=None,
    ):
        created_call.update(
            {
                "profile_data": dict(profile_data),
                "group_id": group_id,
                "hashed_password": hashed_password,
                "force_password_change": force_password_change,
                "preferred_user_id": preferred_user_id,
            }
        )
        return SimpleNamespace(id="user-1", email=profile_data["email"])

    monkeypatch.setattr(
        user_utils, "get_value_by_page_and_key", lambda *args, **kwargs: "group-1"
    )
    monkeypatch.setattr(
        user_utils, "_assert_password_policy", lambda password, db: None
    )
    monkeypatch.setattr(
        user_utils, "hash_password", lambda password: f"hashed:{password}"
    )
    monkeypatch.setattr(user_utils, "_create_user_record", fake_create_user_record)

    summary = user_utils.import_user_from_export(
        _user_export_payload(
            user={
                "email": "imported@example.com",
                "first_name": "Imported",
                "last_name": "User",
                "hashed_password": "IMPORTED_HASH_SENTINEL",
            },
        ),
        FakeDb(),
        default_password="TempPass123!",
        force_password_change=False,
    )

    assert summary["action"] == "created"
    assert "hashed_password" not in created_call["profile_data"]
    assert created_call["hashed_password"] == "hashed:TempPass123!"
    assert created_call["force_password_change"] is False


def test_user_import_resets_existing_user_with_default_password(monkeypatch):
    existing_user = SimpleNamespace(id="user-1", email="imported@example.com")

    class ExistingQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return existing_user

    class FakeDb:
        def query(self, *args, **kwargs):
            return ExistingQuery()

    merge_call = {}

    def fake_merge_user_record(
        db,
        user,
        profile_data,
        warnings=None,
        new_password_hash=None,
        force_password_change=None,
    ):
        merge_call.update(
            {
                "profile_data": dict(profile_data),
                "new_password_hash": new_password_hash,
                "force_password_change": force_password_change,
            }
        )
        return user

    monkeypatch.setattr(
        user_utils, "get_value_by_page_and_key", lambda *args, **kwargs: "group-1"
    )
    monkeypatch.setattr(
        user_utils, "_assert_password_policy", lambda password, db: None
    )
    monkeypatch.setattr(
        user_utils, "hash_password", lambda password: f"hashed:{password}"
    )
    monkeypatch.setattr(user_utils, "_merge_user_record", fake_merge_user_record)

    summary = user_utils.import_user_from_export(
        _user_export_payload(
            user={
                "email": "imported@example.com",
                "hashed_password": "IMPORTED_HASH_SENTINEL",
            },
        ),
        FakeDb(),
        default_password="TempPass123!",
        force_password_change=True,
    )

    assert summary["action"] == "updated"
    assert "hashed_password" not in merge_call["profile_data"]
    assert merge_call["new_password_hash"] == "hashed:TempPass123!"
    assert merge_call["force_password_change"] is True


@pytest.mark.parametrize(
    ("protected_role", "expected_detail"),
    [
        ("admin", "Only the owner can modify administrator accounts."),
        (
            "owner",
            "The owner account cannot be modified by another administrator.",
        ),
    ],
)
def test_admin_import_cannot_reset_protected_account_password(
    monkeypatch,
    protected_role,
    expected_detail,
):
    existing_user = SimpleNamespace(
        id=f"{protected_role}-1",
        email=f"{protected_role}@example.com",
        role=protected_role,
    )

    class ExistingQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return existing_user

    class FakeDb:
        def query(self, *args, **kwargs):
            return ExistingQuery()

    monkeypatch.setattr(
        user_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: "group-1",
    )
    monkeypatch.setattr(
        user_utils,
        "_merge_user_record",
        lambda *args, **kwargs: pytest.fail(
            "protected accounts must be rejected before password mutation"
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        user_utils.import_user_from_export(
            _user_export_payload(user={"email": existing_user.email}),
            FakeDb(),
            default_password="AttackerPass123!",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == expected_detail


def test_admin_user_import_redacts_unexpected_entry_failures(monkeypatch):
    """Per-user admin import errors must not expose database or archive details."""
    private_detail = "duplicate key users_email_key: private@example.com"
    monkeypatch.setattr(
        user_utils,
        "import_user_from_export",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(private_detail)),
    )

    result = user_utils.import_users_admin(
        {
            "export_type": "admin_user",
            "export_version": user_utils.ADMIN_USER_EXPORT_VERSION,
            "data": {"users": [{"user": {"email": "person@example.com"}}]},
        },
        object(),
    )

    assert result["errors"] == [
        {
            "index": 0,
            "email": "person@example.com",
            "error": "User import failed. See server logs for details.",
        }
    ]
    assert private_detail not in str(result)


def test_owner_authorized_import_can_update_an_administrator(monkeypatch):
    existing_user = SimpleNamespace(
        id="admin-1",
        email="admin@example.com",
        role="admin",
    )

    class ExistingQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return existing_user

    class FakeDb:
        def query(self, *args, **kwargs):
            return ExistingQuery()

    monkeypatch.setattr(
        user_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: "group-1",
    )
    monkeypatch.setattr(user_utils, "_assert_password_policy", lambda *args: None)
    monkeypatch.setattr(user_utils, "hash_password", lambda value: f"hashed:{value}")
    monkeypatch.setattr(
        user_utils,
        "_merge_user_record",
        lambda _db, user, *_args, **_kwargs: user,
    )

    summary = user_utils.import_user_from_export(
        _user_export_payload(user={"email": existing_user.email}),
        FakeDb(),
        default_password="RecoveryPass123!",
        allow_administrative_target=True,
    )

    assert summary["action"] == "updated"
    assert summary["user_id"] == "admin-1"


def test_owner_authorized_import_can_update_the_owner(monkeypatch):
    existing_user = SimpleNamespace(
        id="owner-1",
        email="owner@example.com",
        role="owner",
    )

    class ExistingQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return existing_user

    class FakeDb:
        def query(self, *args, **kwargs):
            return ExistingQuery()

    monkeypatch.setattr(
        user_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: "group-1",
    )
    monkeypatch.setattr(user_utils, "_assert_password_policy", lambda *args: None)
    monkeypatch.setattr(user_utils, "hash_password", lambda value: f"hashed:{value}")
    monkeypatch.setattr(
        user_utils,
        "_merge_user_record",
        lambda _db, user, *_args, **_kwargs: user,
    )

    summary = user_utils.import_user_from_export(
        _user_export_payload(user={"email": existing_user.email}),
        FakeDb(),
        default_password="RecoveryPass123!",
        allow_administrative_target=True,
    )

    assert summary["action"] == "updated"
    assert summary["user_id"] == "owner-1"


def test_import_settings_sanitizer_strips_password_state():
    sanitized = user_utils._sanitize_existing_user_import_settings(
        {
            "security": {"has_to_change_password": False, "password_length": 12},
            "social_login": {
                "needs_password_setup": True,
                "pending_auth_code": "CODE_HASH",
                "pending_auth_code_expires": "2026-01-01T00:00:00+00:00",
                "google_linked": True,
            },
            "sso_login": {"needs_password_setup": True, "saml_linked": True},
        }
    )

    assert "has_to_change_password" not in sanitized["security"]
    assert sanitized["security"]["password_length"] == 12
    assert "needs_password_setup" not in sanitized["social_login"]
    assert "pending_auth_code" not in sanitized["social_login"]
    assert "pending_auth_code_expires" not in sanitized["social_login"]
    assert "google_linked" not in sanitized["social_login"]
    assert "needs_password_setup" not in sanitized["sso_login"]
    assert sanitized["sso_login"]["saml_linked"] is True


def test_self_import_only_merges_portable_settings(monkeypatch):
    user = SimpleNamespace(
        id="user-1",
        settings={
            "general": {"language": "en", "timezone": "UTC"},
            "chat": {"ctrl_enter_to_send": False},
            "social_login": {"google_linked": False, "google_user_id": ""},
            "sso_login": {"oidc_linked": False, "provider_id": ""},
            "states": {"welcome_card_dismissed": False},
        },
    )

    class FakeDb:
        def __init__(self):
            self.commit_calls = 0
            self.refresh_calls = 0

        def commit(self):
            self.commit_calls += 1

        def refresh(self, _user):
            self.refresh_calls += 1

        def rollback(self):
            raise AssertionError("rollback should not be called")

    db = FakeDb()

    monkeypatch.setattr(user_utils, "get_user", lambda _db, _user_id: user)
    monkeypatch.setattr(user_utils, "flag_modified", lambda *_args, **_kwargs: None)

    summary = user_utils.import_user_data_for_existing_user(
        "user-1",
        _user_export_payload(
            user={"email": "user@example.com"},
            settings={
                "general": {"language": "de"},
                "chat": {"ctrl_enter_to_send": True},
                "social_login": {
                    "google_linked": True,
                    "google_user_id": "GOOGLE_USER_SENTINEL",
                },
                "sso_login": {
                    "oidc_linked": True,
                    "provider_id": "SSO_PROVIDER_SENTINEL",
                },
                "states": {"welcome_card_dismissed": True},
            },
        ),
        db,
        db_log=None,
    )

    assert summary["imported"] == ["settings"]
    assert user.settings["general"]["language"] == "de"
    assert user.settings["chat"]["ctrl_enter_to_send"] is True
    assert user.settings["social_login"]["google_linked"] is False
    assert user.settings["social_login"]["google_user_id"] == ""
    assert user.settings["sso_login"]["oidc_linked"] is False
    assert user.settings["sso_login"]["provider_id"] == ""
    assert user.settings["states"]["welcome_card_dismissed"] is True
    assert db.commit_calls == 1
    assert db.refresh_calls == 1


def test_complete_self_import_restores_dormant_automations(monkeypatch):
    """A permitted account restore keeps data even when its feature is disabled."""
    user = SimpleNamespace(id="user-1", email="user@example.com", settings={})
    imported = []

    class FakeDb:
        def rollback(self):
            return None

    monkeypatch.setattr(user_utils, "get_user", lambda _db, _user_id: user)
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_automations",
        lambda _db, user_id, automations: imported.append((user_id, automations)),
    )
    summary = user_utils.import_user_data_for_existing_user(
        "user-1",
        _user_export_payload(
            user={"email": "user@example.com"},
            automations=[
                {
                    "id": "automation-1",
                    "title": "Imported automation",
                    "prompt": "Run this later",
                    "model_id": "model-1",
                    "schedule_rules": [{"type": "cron", "cron": "0 9 * * *"}],
                    "is_active": True,
                }
            ],
        ),
        FakeDb(),
        db_log=None,
    )

    assert summary["imported"] == ["automations"]
    assert imported[0][0] == "user-1"
    assert imported[0][1][0]["title"] == "Imported automation"


def test_complete_self_import_remaps_mcp_servers_before_automations(monkeypatch):
    user = SimpleNamespace(id="user-1", email="user@example.com", settings={})
    calls = []
    expected_warning = {
        "section": "automations",
        "code": "automation_mcp_servers_unavailable",
        "warning": "Some selected MCP servers could not be restored for this automation.",
        "automation_id": "automation-1",
        "automation_title": "Imported automation",
        "inaccessible_mcp_server_ids": ["missing-server"],
    }

    class FakeDb:
        def rollback(self):
            return None

    def fake_import_mcp_servers(_db, user_id, rows):
        calls.append(("mcp_servers", user_id, rows))
        return {"source-server": "restored-server"}

    def fake_import_automations(
        _db,
        user_id,
        rows,
        *,
        mcp_server_id_map=None,
    ):
        calls.append(("automations", user_id, rows, mcp_server_id_map))
        return [expected_warning]

    monkeypatch.setattr(user_utils, "get_user", lambda _db, _user_id: user)
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_user_mcp_servers",
        fake_import_mcp_servers,
    )
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_automations",
        fake_import_automations,
    )

    summary = user_utils.import_user_data_for_existing_user(
        "user-1",
        _user_export_payload(
            user={"email": "user@example.com"},
            mcp_servers=[
                {
                    "id": "source-server",
                    "name": "Portable server",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                }
            ],
            automations=[
                {
                    "id": "automation-1",
                    "title": "Imported automation",
                    "prompt": "Use selected connections",
                    "model_id": "model-1",
                    "mcp_server_ids": ["source-server", "missing-server"],
                }
            ],
        ),
        FakeDb(),
        db_log=None,
    )

    assert [call[0] for call in calls] == ["mcp_servers", "automations"]
    assert calls[1][3] == {"source-server": "restored-server"}
    assert summary["imported"] == ["mcp_servers", "automations"]
    assert summary["warnings"] == [expected_warning]


@pytest.mark.parametrize(
    "transport", [TRANSPORT_STDIO, TRANSPORT_STDIO.upper(), f" {TRANSPORT_STDIO} "]
)
def test_self_import_rejects_personal_stdio_mcp_servers(monkeypatch, transport):
    user = SimpleNamespace(id="user-1", settings={})
    merge_calls = []

    class FakeDb:
        def commit(self):
            return None

        def refresh(self, _obj):
            return None

        def merge(self, _obj):
            merge_calls.append(_obj)

        def rollback(self):
            return None

    monkeypatch.setattr(user_utils, "get_user", lambda db, user_id: user)

    payload = _user_export_payload(
        user={"email": "user@example.com"},
        mcp_servers=[
            {
                "id": "srv-1",
                "name": "Local shell",
                "transport": transport,
                "command": "/bin/sh",
                "args": ["-c", "echo should_not_run"],
            }
        ],
    )

    summary = user_utils.import_user_data_for_existing_user(
        "user-1", payload, FakeDb(), db_log=None
    )

    assert "mcp_servers" not in summary["imported"]
    assert len(summary["errors"]) == 1
    assert summary["errors"] == [
        "mcp_servers: import failed. See server logs for details."
    ]
    assert "transport" not in summary["errors"][0]
    assert merge_calls == []


def test_personal_mcp_import_validates_all_rows_before_create_and_discards_secrets(
    monkeypatch,
):
    created = []

    monkeypatch.setattr(
        "app.mcp.models.create_mcp_server", lambda _db, **kwargs: created.append(kwargs)
    )

    with pytest.raises(ValidationError):
        user_utils._bulk_insert_user_mcp_servers(
            object(),
            "user-1",
            [
                {
                    "name": "Valid remote",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                    "headers": {"Authorization": "Bearer injected"},
                },
                {
                    "name": "Invalid remote",
                    "transport": "streamable_http",
                    "url": "file:///etc/passwd",
                },
            ],
        )

    assert created == []

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        user_utils._bulk_insert_user_mcp_servers(
            object(),
            "user-1",
            [
                {
                    "name": "Retired local fields",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                    "env": {"TOKEN": "injected"},
                }
            ],
        )

    user_utils._bulk_insert_user_mcp_servers(
        object(),
        "user-1",
        [
            {
                "name": "Valid remote",
                "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp",
                "headers": {"Authorization": "Bearer injected"},
            }
        ],
    )

    assert created[0]["headers"] == {}
    assert created[0]["env"] == {}
    assert created[0]["owner_user_id"] == "user-1"


def test_personal_mcp_export_reference_maps_to_recreated_server(monkeypatch):
    from app.mcp.models import serialize_mcp_server_export

    source_server = SimpleNamespace(
        id="source-server",
        name="Portable remote",
        icon="link",
        description="Synthetic MCP server",
        namespace="portable",
        transport="streamable_http",
        enabled=True,
        url="https://mcp.example.com/mcp",
        headers={"Authorization": "Bearer source-secret"},
        auth_mode="headers",
        allowed_tools=["synthetic_tool"],
        timeout_seconds=30,
    )
    exported = serialize_mcp_server_export(source_server)
    created = []

    def fake_create(_db, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id="restored-server")

    monkeypatch.setattr("app.mcp.models.create_mcp_server", fake_create)
    server_id_map = user_utils._bulk_insert_user_mcp_servers(
        object(),
        "restored-user",
        [exported],
    )

    assert exported["id"] == "source-server"
    assert exported["headers"] == {}
    assert "id" not in created[0]
    assert server_id_map == {"source-server": "restored-server"}


def test_personal_mcp_import_rejects_non_object_rows_before_create(monkeypatch):
    """Malformed section rows abort the section instead of being skipped."""
    created = []
    monkeypatch.setattr(
        "app.mcp.models.create_mcp_server", lambda _db, **kwargs: created.append(kwargs)
    )

    with pytest.raises(ValueError, match="rows must be objects"):
        user_utils._bulk_insert_user_mcp_servers(
            object(),
            "user-1",
            [
                {
                    "name": "Valid remote",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.com/mcp",
                },
                "malformed-row",
            ],
        )

    assert created == []


def test_user_chat_export_redacts_share_secrets_and_identifiers():
    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="Shared chat",
        project_id=None,
        share_id="stable-share-id",
        share={
            "password": "HASH_SENTINEL",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2026-02-01T00:00:00+00:00",
            "access_mode": "invited",
            "owner_user_id": "user-1",
            "invited_user_ids": ["user-2"],
        },
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    exported = _export_user_chats("user-1", _FakeDb(chats=[chat]))

    assert len(exported) == 1
    exported_chat = exported[0]
    assert "share_id" not in exported_chat
    assert exported_chat["share"] == {
        "has_password": True,
        "access_mode": "invited",
        "expires_at": "2026-02-01T00:00:00+00:00",
    }
    assert "HASH_SENTINEL" not in str(exported_chat)
    assert "stable-share-id" not in str(exported_chat)
    assert "user-2" not in str(exported_chat)


def test_stream_user_chats_batches_message_export_query():
    chat_one = Chats(
        id="chat-1",
        user_id="user-1",
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    chat_two = Chats(
        id="chat-2",
        user_id="user-1",
        meta={},
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    messages = [
        ChatMessages(
            id="message-1",
            chat_id="chat-1",
            model_id="model-1",
            content="hello",
            role="user",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        ChatMessages(
            id="message-2",
            chat_id="chat-2",
            model_id="model-1",
            content="hi",
            role="assistant",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
    ]

    class CountingDb(_FakeDb):
        def __init__(self):
            super().__init__(chats=[chat_one, chat_two], messages=messages)
            self.query_counts = {Chats: 0, ChatMessages: 0}

        def query(self, model):
            self.query_counts[model] = self.query_counts.get(model, 0) + 1
            return super().query(model)

    db = CountingDb()
    exported = json.loads(
        "".join(user_data_export._stream_user_chats_json_array("user-1", db))
    )

    assert db.query_counts[Chats] == 1
    assert db.query_counts[ChatMessages] == 1
    assert [chat["id"] for chat in exported] == ["chat-1", "chat-2"]
    assert [chat["messages"][0]["id"] for chat in exported] == [
        "message-1",
        "message-2",
    ]


def test_skill_import_regenerates_ids_and_remaps_skill_files(tmp_path, monkeypatch):
    from app.skills import models as skill_models

    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, row):
            self.added.append(row)

        def commit(self):
            self.commits += 1

    monkeypatch.setattr(skill_models, "SKILLS_ROOT", tmp_path)

    content = base64.b64encode(b"skill file").decode("ascii")
    db = FakeDb()
    id_map = user_utils._bulk_insert_skills(
        db,
        "user-1",
        [
            {
                "id": "source-skill",
                "user_id": "source-user",
                "icon": "spark",
                "name": "Imported",
                "description": "Imported skill",
                "content": "Use care.",
            }
        ],
        [
            {
                "skill_id": "source-skill",
                "relative_path": "README.md",
                "content_base64": content,
            }
        ],
    )

    imported_skill = db.added[0]
    assert imported_skill.id != "source-skill"
    assert imported_skill.user_id == "user-1"
    assert id_map == {"source-skill": imported_skill.id}
    assert (
        tmp_path / "user-1" / imported_skill.id / "README.md"
    ).read_bytes() == b"skill file"


def test_todo_import_regenerates_ids_and_clears_share_metadata():
    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, row):
            self.added.append(row)

        def commit(self):
            self.commits += 1

    db = FakeDb()
    user_utils._bulk_insert_todos(
        db,
        "user-1",
        [
            {
                "id": "source-list",
                "title": "Inbox",
                "description": "Imported items",
                "icon": "checklist",
                "clone_share_id": "clone-share",
                "live_share_id": "live-share",
                "collaborate_share_id": "collab-share",
                "sort_order": [
                    {"key": "priority", "direction": "desc"},
                    {"key": "bogus", "direction": "sideways"},
                ],
                "todos": [
                    {
                        "id": "source-todo",
                        "content": "Check import path",
                        "priority": "high",
                        "is_done": False,
                        "completed_at": "2026-02-02T00:00:00+00:00",
                    }
                ],
            }
        ],
    )

    imported_list = next(row for row in db.added if isinstance(row, TodoLists))
    imported_todo = next(row for row in db.added if isinstance(row, Todos))

    assert imported_list.id != "source-list"
    assert imported_list.user_id == "user-1"
    assert imported_list.clone_share_id is None
    assert imported_list.live_share_id is None
    assert imported_list.collaborate_share_id is None
    assert imported_list.sort_order == [{"key": "priority", "direction": "desc"}]

    assert imported_todo.id != "source-todo"
    assert imported_todo.todo_list == imported_list.id
    assert imported_todo.priority == 0
    assert imported_todo.completed_at is None
    assert db.commits == 1


def test_agent_asset_import_cleans_uploaded_blob_on_commit_failure(monkeypatch):
    class FailingDb:
        def __init__(self):
            self.added = []
            self.rolled_back = False

        def add(self, row):
            self.added.append(row)

        def commit(self):
            raise RuntimeError("commit failed")

        def rollback(self):
            self.rolled_back = True

    deletes = []
    monkeypatch.setattr(
        user_utils,
        "_upload_inline_file_bytes",
        lambda *_args, **_kwargs: ("local", "user-1/assets/asset.txt", {}),
    )
    monkeypatch.setattr(
        user_utils,
        "_delete_uploaded_file_references",
        lambda user_id, references: deletes.append((user_id, list(references))),
    )

    db = FailingDb()
    with pytest.raises(RuntimeError, match="commit failed"):
        user_utils._bulk_insert_agent_assets(
            db,
            "user-1",
            [
                {
                    "id": "source-asset",
                    "agent_id": "source-agent",
                    "file_name": "asset.txt",
                    "file_category": "document",
                    "file_type": "text/plain",
                    "content_base64": base64.b64encode(b"asset").decode("ascii"),
                }
            ],
            agent_id_map={"source-agent": "imported-agent"},
        )

    assert db.rolled_back is True
    assert deletes == [("user-1", [("local", "user-1/assets/asset.txt", "asset.txt")])]


def test_slide_presentation_import_does_not_fallback_to_source_file_id(monkeypatch):
    class FakeDb:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(
        user_utils,
        "_write_slide_presentation_artifacts",
        lambda **_kwargs: ("local", "user-1/presentations/imported", []),
    )

    db = FakeDb()
    user_utils._bulk_insert_slide_presentations(
        db,
        "user-1",
        [
            {
                "id": "source-presentation",
                "title": "Deck",
                "slide_count": 1,
                "file_id": "source-file",
            }
        ],
        file_id_map={},
    )

    assert db.added[0].id != "source-presentation"
    assert db.added[0].file_id is None


def test_user_import_reports_only_populated_export_only_sections():
    payload = {
        "user": {"email": "person@example.com"},
        "group": {"id": "group-1"},
        "auth": {"active_tokens": [{"id": "auth-1"}]},
        "activity_logs": {"audit_logs": [{"id": "log-1"}]},
        "notes": {"notes": [{"id": "note-1"}], "history": []},
        "todos": [{"id": "list-1", "todos": []}],
        "memories": [{"id": "memory-1"}],
        "shared_agent_subscriptions": [{"id": "subscription-1", "agent_id": "agent-1"}],
        "chats": [{"id": "chat-1"}],
        "files": [],
        "projects": [],
        "tasks": [],
        "feedback": [{"id": "feedback-1"}],
        "usage_stats": {
            "llm_generation_stats": {"data": {"statistics": [{"id": "stat-1"}]}}
        },
    }

    skipped = _get_skipped_export_only_sections(payload)

    assert skipped == [
        {"section": "group", "reason": "export_only"},
        {"section": "auth", "reason": "export_only"},
        {"section": "activity_logs", "reason": "export_only"},
        {"section": "feedback", "reason": "export_only"},
        {"section": "usage_stats", "reason": "export_only"},
        {"section": "shared_agent_subscriptions", "reason": "export_only"},
    ]


def test_user_import_does_not_report_empty_export_only_sections():
    payload = {
        "user": {"email": "person@example.com"},
        "group": {},
        "auth": {"active_tokens": []},
        "activity_logs": {},
        "notes": {"notes": [], "history": []},
        "todos": [],
        "memories": [],
        "usage_stats": {},
    }

    assert _get_skipped_export_only_sections(payload) == []


def test_user_import_reports_deep_export_only_sections_without_recursion_error():
    nested_feedback = "feedback"
    for _ in range(12_000):
        nested_feedback = {"child": nested_feedback}

    skipped = _get_skipped_export_only_sections(
        {
            "user": {"email": "person@example.com"},
            "feedback": nested_feedback,
        }
    )

    assert skipped == [{"section": "feedback", "reason": "export_only"}]


def test_user_import_reports_oversized_empty_export_only_sections_as_skipped(
    monkeypatch,
):
    monkeypatch.setattr(user_data_export, "SKIPPED_SECTION_SCAN_NODE_LIMIT", 3)

    payload = {
        "user": {"email": "person@example.com"},
        "feedback": {"a": {"b": {"c": {"d": ""}}}},
    }

    assert _get_skipped_export_only_sections(payload) == [
        {"section": "feedback", "reason": "export_only"},
    ]


def test_user_import_existing_user_restores_todos(monkeypatch):
    captured = {}

    class FakeDb:
        def rollback(self):
            return None

    monkeypatch.setattr(
        user_utils,
        "get_user",
        lambda db, user_id: SimpleNamespace(
            id=user_id, email="person@example.com", settings={}
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_todos",
        lambda db, user_id, todo_lists: captured.update(
            {"user_id": user_id, "todo_lists": todo_lists}
        ),
    )

    summary = user_utils.import_user_data_for_existing_user(
        "user-1",
        _user_export_payload(
            user={"email": "person@example.com"},
            todos=[
                {
                    "id": "list-1",
                    "title": "Inbox",
                    "todos": [{"id": "todo-1", "content": "Restore me"}],
                }
            ],
        ),
        FakeDb(),
        db_log=None,
    )

    assert summary["imported"] == ["todos"]
    assert summary["skipped_sections"] == []
    assert captured["user_id"] == "user-1"
    assert captured["todo_lists"][0]["title"] == "Inbox"


def test_direct_self_userdata_import_restores_embedded_notes_and_memories(monkeypatch):
    """The paired userdata endpoints must round-trip feature-owned sections."""
    imported = []

    class FakeDb:
        def rollback(self):
            return None

    monkeypatch.setattr(
        user_utils,
        "get_user",
        lambda db, user_id: SimpleNamespace(
            id=user_id, email="person@example.com", settings={}
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_import_user_notes_archive",
        lambda *args, **kwargs: imported.append("notes") or {"created": []},
    )
    monkeypatch.setattr(
        user_utils,
        "_import_user_memories_archive",
        lambda *args, **kwargs: (
            imported.append("memories") or {"created_count": 0, "deduped_count": 0}
        ),
    )

    summary = user_utils.import_user_data_for_existing_user(
        "user-1",
        _user_export_payload(
            user={"email": "person@example.com"},
            notes={
                "export_type": "notes",
                "export_version": 1.0,
                "data": {"notes": [{"id": "note-1", "content": "Restore me"}]},
            },
            memories={
                "export_type": "memories",
                "export_version": 1.0,
                "data": {
                    "user_id": "user-1",
                    "memories": [{"content": "Remember this"}],
                    "count": 1,
                },
            },
        ),
        FakeDb(),
        db_log=None,
    )

    assert imported == ["notes", "memories"]
    assert "notes" in summary["imported"]
    assert "memories" in summary["imported"]
    assert not any(
        entry.get("section") in {"notes", "memories"}
        and entry.get("reason") == "dedicated_bundle_section"
        for entry in summary["skipped_sections"]
    )


def test_shared_restore_engine_applies_explicit_note_sharing_policy(monkeypatch):
    """Shared content code must not weaken the self/admin sharing boundary."""
    target_user = SimpleNamespace(
        id="user-1", email="person@example.com", role="user", settings={}
    )
    policy_calls = []

    class ExistingQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return target_user

    class FakeDb:
        def query(self, *args, **kwargs):
            return ExistingQuery()

        def rollback(self):
            return None

    def fake_import_notes(
        _db,
        user_id,
        _payload,
        *,
        restore_sharing_metadata,
        skip_existing_owned,
    ):
        policy_calls.append((user_id, restore_sharing_metadata, skip_existing_owned))
        return {"created": [], "skipped": [], "warnings": [], "errors": []}

    monkeypatch.setattr(user_utils, "get_user", lambda *_args: target_user)
    monkeypatch.setattr(
        user_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: "group-1",
    )
    monkeypatch.setattr(
        user_utils,
        "_merge_user_record",
        lambda _db, user, *_args, **_kwargs: user,
    )
    monkeypatch.setattr(user_utils, "_import_user_notes_archive", fake_import_notes)
    payload = _user_export_payload(
        user={"email": "person@example.com"},
        notes={
            "export_type": "notes",
            "export_version": 1.0,
            "data": {"notes": [{"id": "note-1", "content": "portable"}]},
        },
    )
    db = FakeDb()

    user_utils.import_user_data_for_existing_user("user-1", payload, db, db_log=None)
    user_utils.import_user_from_export(
        payload,
        db,
        restore_sharing_metadata=True,
    )

    assert policy_calls == [
        ("user-1", False, True),
        ("user-1", True, True),
    ]


@pytest.mark.parametrize("import_mode", ["admin", "self_service"])
def test_shared_restore_engine_isolates_section_failures(monkeypatch, import_mode):
    """One malformed section must not suppress later portable data."""
    target_user = SimpleNamespace(
        id="user-1", email="person@example.com", role="user", settings={}
    )
    restored_automations = []

    class ExistingQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return target_user

    class FakeDb:
        def query(self, *args, **kwargs):
            return ExistingQuery()

        def rollback(self):
            return None

    monkeypatch.setattr(user_utils, "get_user", lambda *_args: target_user)
    monkeypatch.setattr(
        user_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: "group-1",
    )
    monkeypatch.setattr(
        user_utils,
        "_merge_user_record",
        lambda _db, user, *_args, **_kwargs: user,
    )
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_chat_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("broken chat")),
    )
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_automations",
        lambda _db, user_id, rows: restored_automations.append((user_id, rows)),
    )
    payload = _user_export_payload(
        user={"email": "person@example.com"},
        chats=[{"id": "bad-chat"}],
        automations=[
            {
                "id": "automation-1",
                "title": "Restore after failure",
                "prompt": "Continue",
                "model_id": "model-1",
            }
        ],
    )
    db = FakeDb()

    if import_mode == "admin":
        summary = user_utils.import_user_from_export(payload, db)
    else:
        summary = user_utils.import_user_data_for_existing_user(
            "user-1", payload, db, db_log=None
        )

    assert summary["errors"] == ["chats: import failed. See server logs for details."]
    assert restored_automations[0][0] == "user-1"
    if import_mode == "self_service":
        assert "automations" in summary["imported"]


def test_user_import_new_user_restores_todos(monkeypatch):
    captured = {}

    class EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, *args, **kwargs):
            return EmptyQuery()

    monkeypatch.setattr(
        user_utils, "get_value_by_page_and_key", lambda *args, **kwargs: "group-1"
    )
    monkeypatch.setattr(
        user_utils, "_assert_password_policy", lambda password, db: None
    )
    monkeypatch.setattr(
        user_utils, "hash_password", lambda password: f"hashed:{password}"
    )
    monkeypatch.setattr(
        user_utils,
        "_create_user_record",
        lambda db, profile_data, group_id, **kwargs: SimpleNamespace(
            id="user-1", email=profile_data["email"]
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_todos",
        lambda db, user_id, todo_lists: captured.update(
            {"user_id": user_id, "todo_lists": todo_lists}
        ),
    )

    summary = user_utils.import_user_from_export(
        _user_export_payload(
            user={"email": "imported@example.com"},
            todos=[
                {
                    "id": "list-1",
                    "title": "Inbox",
                    "todos": [{"id": "todo-1", "content": "Restore me"}],
                }
            ],
        ),
        FakeDb(),
        default_password="TempPass123!",
        force_password_change=False,
    )

    assert summary["errors"] == []
    assert captured["user_id"] == "user-1"
    assert captured["todo_lists"][0]["todos"][0]["content"] == "Restore me"


def test_admin_user_import_restores_notes_and_memories_independently(monkeypatch):
    """A failure in another section must not skip notes or memories."""
    captured = {}

    class EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, *args, **kwargs):
            return EmptyQuery()

        def rollback(self):
            return None

    monkeypatch.setattr(
        user_utils, "get_value_by_page_and_key", lambda *args, **kwargs: "group-1"
    )
    monkeypatch.setattr(
        user_utils, "_assert_password_policy", lambda password, db: None
    )
    monkeypatch.setattr(
        user_utils, "hash_password", lambda password: f"hashed:{password}"
    )
    monkeypatch.setattr(
        user_utils,
        "_create_user_record",
        lambda db, profile_data, group_id, **kwargs: SimpleNamespace(
            id="user-1", email=profile_data["email"]
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_import_user_notes_archive",
        lambda db, user_id, payload, **kwargs: captured.setdefault(
            "notes", {"created": [{"id": "note-1"}], "warnings": [], "errors": []}
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_import_user_memories_archive",
        lambda db, user_id, payload: captured.setdefault(
            "memories", {"created_count": 1, "deduped_count": 0}
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_chat_data",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("broken chat")),
    )

    summary = user_utils.import_user_from_export(
        _user_export_payload(
            user={"email": "imported@example.com"},
            notes={
                "export_type": "notes",
                "export_version": 1.0,
                "data": {"notes": []},
            },
            memories={
                "export_type": "memories",
                "export_version": 1.0,
                "data": {"user_id": "source-user", "memories": [], "count": 0},
            },
            chats=[{"id": "bad-chat"}],
        ),
        FakeDb(),
        default_password="TempPass123!",
        force_password_change=False,
    )

    assert "notes" in captured
    assert "memories" in captured
    assert summary["created_notes_count"] == 1
    assert summary["created_memories_count"] == 1
    assert summary["errors"] == ["chats: import failed. See server logs for details."]


def test_user_data_export_excludes_deleted_and_temporary_chats():
    normal_chat = Chats(
        id="chat-normal",
        user_id="user-1",
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    shadow_deleted_chat = Chats(
        id="chat-deleted",
        user_id="user-1",
        meta={"shadow_deleted": True},
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    temp_chat = Chats(
        id="chat-temp",
        user_id="user-1",
        meta='{"status": "temp"}',
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    exported = _export_user_chats(
        "user-1",
        _FakeDb(chats=[normal_chat, shadow_deleted_chat, temp_chat]),
    )

    assert [chat["id"] for chat in exported] == ["chat-normal"]


def test_streamed_user_data_export_can_include_deleted_and_temporary_chats():
    """Complete account archives can preserve every retained chat by request."""
    normal_chat = Chats(id="chat-normal", user_id="user-1", meta={})
    shadow_deleted_chat = Chats(
        id="chat-deleted",
        user_id="user-1",
        meta={"shadow_deleted": True},
    )
    temp_chat = Chats(
        id="chat-temp",
        user_id="user-1",
        meta={"status": "temp"},
    )

    exported = json.loads(
        "".join(
            user_data_export._stream_user_chats_json_array(
                "user-1",
                _FakeDb(chats=[normal_chat, shadow_deleted_chat, temp_chat]),
                include_deleted_or_temp=True,
            )
        )
    )

    assert [chat["id"] for chat in exported] == [
        "chat-normal",
        "chat-deleted",
        "chat-temp",
    ]
    assert exported[1]["meta"] == {"shadow_deleted": True}
    assert exported[2]["meta"] == {"status": "temp"}


def test_user_data_import_preserves_hidden_chat_metadata():
    """Restoring a complete archive must not make its hidden chats visible."""

    class ImportDb:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

        def flush(self):
            return None

        def commit(self):
            return None

    db = ImportDb()

    user_utils._bulk_insert_chat_data(
        db,
        "target-user",
        [
            {"meta": {"shadow_deleted": True}, "messages": []},
            {"meta": {"status": "temp"}, "messages": []},
        ],
    )

    imported_chats = [row for row in db.added if isinstance(row, Chats)]
    assert [chat.meta for chat in imported_chats] == [
        {"shadow_deleted": True},
        {"status": "temp"},
    ]


def test_canonical_chat_stream_includes_deep_research_artifacts(monkeypatch):
    """A user bundle must replace the retired chat archive without data loss."""
    from app.chats import download as chat_download

    monkeypatch.setattr(
        chat_download,
        "_export_deep_research_runs_for_chat",
        lambda user_id, chat_id, _db: [
            {
                "id": "research-run-1",
                "user_id": user_id,
                "chat_id": chat_id,
                "artifacts": [
                    {
                        "relative_path": "report.md",
                        "content_base64": "cmVwb3J0",
                    }
                ],
            }
        ],
    )
    chat = Chats(id="chat-1", user_id="user-1", meta={"status": "normal"})

    exported = json.loads(
        "".join(user_data_export._stream_chat_export_json(chat, [], object()))
    )

    assert exported["deep_research_runs"][0]["id"] == "research-run-1"
    assert (
        exported["deep_research_runs"][0]["artifacts"][0]["content_base64"]
        == "cmVwb3J0"
    )


def test_materialized_user_chat_export_includes_deep_research_artifacts(monkeypatch):
    """The dict export must match the Deep Research coverage it advertises."""
    from app.chats import download as chat_download

    monkeypatch.setattr(
        chat_download,
        "_export_deep_research_runs_for_chat",
        lambda user_id, chat_id, _db: [
            {"id": "research-run-1", "user_id": user_id, "chat_id": chat_id}
        ],
    )
    chat = Chats(id="chat-1", user_id="user-1", meta={})

    exported = _export_user_chats("user-1", _FakeDb(chats=[chat]))

    assert exported[0]["deep_research_runs"] == [
        {"id": "research-run-1", "user_id": "user-1", "chat_id": "chat-1"}
    ]


def test_project_import_reuses_normalized_source_uuid():
    """Portable project IDs are stored and mapped in canonical UUID form."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Project.__table__])
    db = sessionmaker(bind=engine)()
    source_id = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
    try:
        id_map = user_utils._bulk_insert_projects(
            db,
            "user-1",
            [
                {
                    "id": source_id,
                    "title": "Imported project",
                    "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "last_updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                }
            ],
        )

        project = db.query(Project).one()
        assert project.id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        assert id_map[source_id] == project.id
    finally:
        db.close()


@pytest.mark.parametrize("source_project_exists", [False, True])
def test_project_import_drops_nonportable_link_share_state(source_project_exists):
    """Imported projects are private even when archives contain legacy share fields."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Project.__table__])
    db = sessionmaker(bind=engine)()
    source_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    source_payload = {
        "id": source_id,
        "user_id": "source-user",
        "title": "Shared source project",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "last_updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "link_share_id": "source-bearer-link",
        "link_share_password_hash": "source-password-hash",
        "link_share_expires_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "link_share_created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    try:
        if source_project_exists:
            db.add(Project(**source_payload))
            db.commit()

        id_map = user_utils._bulk_insert_projects(
            db,
            "target-user",
            [source_payload],
        )

        imported = (
            db.query(Project).filter(Project.user_id == "target-user").one()
        )
        assert imported.title == "Shared source project"
        assert imported.link_share_id is None
        assert imported.link_share_password_hash is None
        assert imported.link_share_expires_at is None
        assert imported.link_share_created_at is None
        assert id_map[source_id] == imported.id
        if source_project_exists:
            assert imported.id != source_id
            source = db.query(Project).filter(Project.user_id == "source-user").one()
            assert source.link_share_id == "source-bearer-link"
            assert source.link_share_password_hash == "source-password-hash"
    finally:
        db.close()


def test_delayed_message_reference_reconnect_chunks_queries_and_skips_unchanged_rows():
    """Large archive reconnects use bounded queries and update only changed messages."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[ChatMessages.__table__])
    db = sessionmaker(bind=engine)()
    chat_id_map = {f"source-{index}": f"chat-{index}" for index in range(501)}
    changed = ChatMessages(
        id="message-changed",
        chat_id="chat-0",
        model_id="model",
        role="user",
        content='{"documents": ["source-file"]}',
        generation={"generation_number": 1},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    unchanged = ChatMessages(
        id="message-unchanged",
        chat_id="chat-500",
        model_id="model",
        role="user",
        content="ordinary text",
        generation={"generation_number": 1},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([changed, unchanged])
    db.commit()

    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().upper())

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        user_utils.reconnect_imported_user_archive_file_references(
            db,
            project_id_map={},
            chat_id_map=chat_id_map,
            file_id_map={"source-file": "target-file"},
        )

        message_selects = [
            statement
            for statement in statements
            if statement.startswith("SELECT") and "CHAT_MESSAGES" in statement
        ]
        message_updates = [
            statement
            for statement in statements
            if statement.startswith("UPDATE") and "CHAT_MESSAGES" in statement
        ]
        assert len(message_selects) == 2
        assert len(message_updates) == 1
        assert json.loads(changed.content)["documents"] == ["target-file"]
        assert unchanged.content == "ordinary text"
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
        db.close()


def test_agent_export_only_includes_assets_for_owned_agents(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE user_agents (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                name TEXT,
                icon TEXT,
                base_model_id TEXT,
                instruction TEXT,
                skill_id TEXT,
                clone_share_id TEXT,
                live_share_id TEXT,
                collaborate_share_id TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE shared_user_agent_subscriptions (
                id TEXT PRIMARY KEY,
                agent_id TEXT,
                subscriber_id TEXT,
                share_type TEXT,
                subscribed_at TIMESTAMP
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE user_agent_assets (
                id TEXT PRIMARY KEY,
                agent_id TEXT,
                owner_user_id TEXT,
                file_name TEXT,
                storage_provider TEXT,
                storage_key TEXT,
                storage_meta TEXT,
                file_category TEXT,
                file_type TEXT,
                file_size INTEGER,
                meta TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
        )
    Session = sessionmaker(bind=engine)
    db = Session()

    db.add_all(
        [
            UserAgent(
                id="owned-agent",
                user_id="user-1",
                name="Owned",
                icon="bot",
                base_model_id="model-1",
                instruction="Owned instructions",
            ),
            UserAgent(
                id="shared-agent",
                user_id="owner-2",
                name="Shared",
                icon="bot",
                base_model_id="model-1",
                instruction="Shared instructions",
            ),
            SharedUserAgentSubscription(
                id="subscription-1",
                agent_id="shared-agent",
                subscriber_id="user-1",
                share_type="live",
            ),
            UserAgentAsset(
                id="owned-asset",
                agent_id="owned-agent",
                owner_user_id="owner-2",
                file_name="owned.txt",
                storage_provider="local",
                storage_key="owned.txt",
                storage_meta={},
                file_category="document",
                file_type="text/plain",
                file_size=5,
                meta={},
            ),
            UserAgentAsset(
                id="shared-asset",
                agent_id="shared-agent",
                owner_user_id="user-1",
                file_name="shared.txt",
                storage_provider="local",
                storage_key="shared.txt",
                storage_meta={},
                file_category="document",
                file_type="text/plain",
                file_size=6,
                meta={},
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(
        user_data_export,
        "_export_agent_asset_entry",
        lambda asset: {"id": asset.id, "agent_id": asset.agent_id},
    )

    exported = user_data_export._export_user_agents("user-1", db)

    assert [asset["id"] for asset in exported["assets"]] == ["owned-asset"]
    assert exported["subscriptions"][0]["agent_id"] == "shared-agent"


def test_agent_import_resets_share_ids():
    class FakeDb:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

        def commit(self):
            return None

    db = FakeDb()

    agent_id_map = user_utils._bulk_insert_agents(
        db,
        "user-1",
        [
            {
                "id": "source-agent",
                "name": "Reviewer",
                "icon": "bot",
                "base_model_id": "model-1",
                "instruction": "Review carefully",
                "clone_share_id": "clone-token",
                "live_share_id": "live-token",
                "collaborate_share_id": "collab-token",
            }
        ],
    )

    imported = db.added[0]
    assert agent_id_map["source-agent"] == imported.id
    assert imported.clone_share_id is None
    assert imported.live_share_id is None
    assert imported.collaborate_share_id is None


def test_user_import_skips_shared_agent_subscriptions(monkeypatch):
    class EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, *args, **kwargs):
            return EmptyQuery()

    skipped_calls = []

    monkeypatch.setattr(
        user_utils, "get_value_by_page_and_key", lambda *args, **kwargs: "group-1"
    )
    monkeypatch.setattr(
        user_utils, "_assert_password_policy", lambda password, db: None
    )
    monkeypatch.setattr(
        user_utils, "hash_password", lambda password: f"hashed:{password}"
    )
    monkeypatch.setattr(
        user_utils,
        "_create_user_record",
        lambda db, profile_data, group_id, *, hashed_password, force_password_change, preferred_user_id=None: (
            SimpleNamespace(
                id="user-1",
                email=profile_data["email"],
            )
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_bulk_insert_shared_agent_subscriptions",
        lambda *args, **kwargs: skipped_calls.append("called"),
    )

    summary = user_utils.import_user_from_export(
        _user_export_payload(
            user={"email": "imported@example.com"},
            shared_agent_subscriptions=[
                {"id": "subscription-1", "agent_id": "shared-agent"}
            ],
        ),
        FakeDb(),
        default_password="TempPass123!",
        force_password_change=False,
    )

    assert skipped_calls == []
    assert {
        "section": "shared_agent_subscriptions",
        "reason": "export_only",
    } in summary["skipped_sections"]


def test_prompt_revision_round_trips_through_user_data_import():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Prompts.__table__],
    )
    db = sessionmaker(bind=engine)()
    try:
        user_utils._bulk_insert_prompts(
            db,
            "target-user",
            [
                {
                    "id": "source-prompt",
                    "user_id": "source-user",
                    "title": "Current title",
                    "description": "Current description",
                    "content": "Current content",
                    "revision": 2,
                    "last_edited_by_user_id": "source-user",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                }
            ],
        )

        imported_prompt = db.query(Prompts).one()
        assert imported_prompt.id != "source-prompt"
        assert imported_prompt.user_id == "target-user"
        assert imported_prompt.last_edited_by_user_id == "target-user"
        assert imported_prompt.revision == 2
        assert imported_prompt.content == "Current content"
    finally:
        db.close()


@pytest.mark.parametrize(
    "share_field",
    ["clone_share_id", "live_share_id", "collaborate_share_id"],
)
@pytest.mark.parametrize("source_prompt_exists", [False, True])
def test_prompt_import_drops_nonportable_share_ids(
    share_field, source_prompt_exists
):
    """Imported prompts are private even when legacy archives contain share IDs."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Prompts.__table__])
    db = sessionmaker(bind=engine)()
    source_payload = {
        "id": "source-prompt",
        "user_id": "source-user",
        "title": "Shared source prompt",
        "description": "Portable description",
        "content": "Portable content",
        "revision": 3,
        "last_edited_by_user_id": "source-user",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        share_field: "source-bearer-link",
    }
    try:
        if source_prompt_exists:
            db.add(Prompts(**source_payload))
            db.commit()

        user_utils._bulk_insert_prompts(db, "target-user", [source_payload])

        imported = (
            db.query(Prompts).filter(Prompts.user_id == "target-user").one()
        )
        assert imported.id != "source-prompt"
        assert imported.title == "Shared source prompt"
        assert imported.content == "Portable content"
        assert imported.revision == 3
        assert imported.last_edited_by_user_id == "target-user"
        assert imported.clone_share_id is None
        assert imported.live_share_id is None
        assert imported.collaborate_share_id is None
        if source_prompt_exists:
            source = db.query(Prompts).filter(Prompts.user_id == "source-user").one()
            assert getattr(source, share_field) == "source-bearer-link"
    finally:
        db.close()


def test_canvas_system_folder_identity_round_trips_without_share_capabilities():
    """Archives preserve system identity but cannot make a system folder shared."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[FileFolders.__table__, SharedFileFolderSubscription.__table__],
    )
    db = sessionmaker(bind=engine)()
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    try:
        db.add(
            FileFolders(
                id="source-canvas-folder",
                user_id="source-user",
                name="Renamed Canvas",
                icon="folder",
                icon_color="#6366f1",
                order=0,
                system_kind="canvas",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

        exported = user_data_export._export_user_file_folders("source-user", db)[
            "owned"
        ]
        assert exported[0]["system_kind"] == "canvas"

        # A hand-edited archive must not be able to restore share capabilities
        # onto the private system container.
        exported[0]["clone_share_id"] = "clone-token"
        exported[0]["live_share_id"] = "live-token"
        exported[0]["collaborate_share_id"] = "collaborate-token"
        folder_id_map, warnings = user_utils._bulk_insert_file_folders(
            db, "target-user", exported
        )

        imported = (
            db.query(FileFolders)
            .filter(
                FileFolders.user_id == "target-user",
                FileFolders.system_kind == "canvas",
            )
            .one()
        )
        assert folder_id_map["source-canvas-folder"] == imported.id
        assert imported.name == "Renamed Canvas"
        assert imported.clone_share_id is None
        assert imported.live_share_id is None
        assert imported.collaborate_share_id is None
        assert warnings == []
    finally:
        db.close()


def test_streamed_user_connections_omit_oauth_secrets():
    connection = UserConnection(
        id="conn-1",
        user_id="user-1",
        provider="github",
        enabled=True,
        auth_mode="pat",
        secrets={"access_token": "token", "refresh_token": "refresh"},
        status={"state": "connected"},
        mcp_server_id=None,
        connected_at=datetime(2026, 1, 6, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 6, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 7, tzinfo=timezone.utc),
    )
    streamed = json.loads(
        "".join(
            user_data_export._stream_user_connections_json_array(
                "user-1",
                _FakeDb(rows_by_model={UserConnection: [connection]}),
            )
        )
    )

    assert streamed[0]["provider"] == "github"
    assert "secrets" not in streamed[0]


def test_retired_microsoft_file_connections_are_not_exported_or_imported():
    supported = UserConnection(
        id="conn-supported",
        user_id="user-1",
        provider="github",
        enabled=True,
        auth_mode="oauth",
        secrets={},
        status={},
    )
    retired = UserConnection(
        id="conn-retired",
        user_id="user-1",
        provider="onedrive",
        enabled=True,
        auth_mode="oauth",
        secrets={},
        status={},
    )
    noncanonical = UserConnection(
        id="conn-noncanonical",
        user_id="user-1",
        provider=" GitHub ",
        enabled=True,
        auth_mode="oauth",
        secrets={},
        status={},
    )
    db = _FakeDb(
        rows_by_model={UserConnection: [supported, retired, noncanonical]}
    )

    exported = user_data_export._export_user_connections("user-1", db)["connections"]
    streamed = json.loads(
        "".join(user_data_export._stream_user_connections_json_array("user-1", db))
    )
    assert [row["provider"] for row in exported] == ["github"]
    assert [row["provider"] for row in streamed] == ["github"]

    imported_rows = [
        {"id": "supported", "provider": "github"},
        {"id": "retired", "provider": "sharepoint"},
        {"id": "noncanonical", "provider": " GitHub "},
    ]
    with patch.object(user_utils, "_bulk_merge_serialized_models") as merge:
        user_utils._bulk_insert_user_connections(db, "user-2", imported_rows)
        assert [row["provider"] for row in merge.call_args.args[2]] == ["github"]

        user_utils._bulk_insert_connection_oauth_states(db, "user-2", imported_rows)
        assert [row["provider"] for row in merge.call_args.args[2]] == ["github"]


def test_streamed_complete_user_data_export_always_contains_owned_memories():
    user = SimpleNamespace(
        id="user-1",
        email="person@example.com",
    )
    usage_stats_stream = '{"llm_generation_stats":{"data":{"statistics":[]}},"tool_call_stats":{"data":{"statistics":[]}}}'

    with (
        patch(
            "app.users.utils._build_user_data_export_core",
            return_value=(user, {}, {}, None),
        ),
        patch(
            "app.users.utils._user_has_activity_logs",
            return_value=False,
        ),
        patch(
            "app.users.utils._stream_user_chats_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.users.utils._stream_user_notes_json",
            return_value=iter(['{"notes":[],"history":[]}']),
        ),
        patch(
            "app.users.utils._export_user_memories",
            return_value={"data": {"memories": [{"content": "Remember this"}]}},
        ),
        patch(
            "app.users.utils._stream_user_todos_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.users.utils._stream_model_query_json_array",
            side_effect=lambda *args, **kwargs: iter(["[]"]),
        ),
        patch(
            "app.users.utils._stream_user_skill_files_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.users.utils._stream_user_agent_assets_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.users.utils._stream_user_prompts_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.users.utils._stream_shared_prompt_subscriptions_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.users.utils._stream_user_connections_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.users.utils._export_user_mcp_servers",
            return_value=[],
        ),
        patch(
            "app.users.utils._stream_user_model_setting_presets_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.users.utils._stream_user_usage_stats_json",
            return_value=iter([usage_stats_stream]),
        ),
        patch(
            "app.users.utils._stream_user_slide_presentations_json_array",
            return_value=iter(["[]"]),
        ),
        patch(
            "app.admin.user_exports.files.models.stream_admin_user_file_entries_json_array",
            return_value=iter(["[]"]),
        ),
    ):
        exported = json.loads(
            "".join(
                legacy_user_utils.iter_user_data_export_json("user-1", _FakeDb(), None)
            )
        )

    assert exported["memories"]["data"]["memories"][0]["content"] == "Remember this"
    assert "memories" in exported["export_coverage"]["included_sections"]
    assert not any(
        item["section"] == "memories"
        for item in exported["export_coverage"]["excluded_sections"]
    )
