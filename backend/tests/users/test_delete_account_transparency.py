import sys
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

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

from app.users import utils as user_utils
from app.users import deletion_policy
from app.groups import management as group_management
from app.settings.defaults import DEFAULT_SETTINGS
from app.auth import twofa_provider
from app.email import models as email_models
from app.email import service as email_service


class _FakeQuery:
    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return []

    def delete(self, *args, **kwargs):
        return 0

    def update(self, *args, **kwargs):
        return 0


class _FakeDb:
    def __init__(self):
        self.commits = 0

    def query(self, model):
        return _FakeQuery()

    def commit(self):
        self.commits += 1

    def flush(self):
        return None


def _patch_settings(monkeypatch, values):
    def fake_get_value(page, key, db):
        return values.get((page, key))

    monkeypatch.setattr(user_utils, "get_value_by_page_and_key", fake_get_value)
    monkeypatch.setattr(deletion_policy, "get_value_by_page_and_key", fake_get_value)


def _patch_delete_user_dependencies(monkeypatch):
    monkeypatch.setattr(user_utils, "get_user_group_setting_value", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        user_utils,
        "get_user",
        lambda *args, **kwargs: SimpleNamespace(
            id="user-1",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(user_utils, "delete_authentication_all", lambda *args, **kwargs: None)
    monkeypatch.setattr(user_utils, "cancel_auth_log_deletions_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(user_utils, "delete_authentication_logs_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(user_utils, "schedule_auth_log_deletion", lambda *args, **kwargs: None)
    monkeypatch.setattr(user_utils, "cancel_audit_log_deletions_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(user_utils, "delete_audit_logs_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(user_utils, "delete_admin_notifications_for_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(user_utils, "schedule_audit_log_deletion", lambda *args, **kwargs: None)
    monkeypatch.setattr(email_models, "cancel_user_email", lambda *args, **kwargs: 0)
    monkeypatch.setattr(email_service, "enqueue_security_event", lambda *args, **kwargs: None)


def test_user_deletion_policy_defaults_to_scheduled_soft_delete(monkeypatch):
    _patch_settings(monkeypatch, {})
    now = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)

    policy = user_utils.get_user_deletion_policy(_FakeDb(), now=now)

    assert policy["mode"] == "delete_after_days"
    assert policy["effect"] == "scheduled_deletion"
    assert policy["restorable"] is True
    assert policy["retention_days"] == 30
    assert policy["purge_scheduled_at"] == now + timedelta(days=30)


def test_delete_user_response_discloses_scheduled_erasure(monkeypatch):
    _patch_settings(
        monkeypatch,
        {
            ("users", "user_deletion_mode"): "delete_after_days",
            ("users", "user_deletion_retention_days"): 7,
            ("security", "auth_logs_retention_after_user_delete_mode"): "retain",
            ("security", "audit_logs_retention_after_user_delete_mode"): "retain",
        },
    )
    _patch_delete_user_dependencies(monkeypatch)
    captured = {}

    def fake_soft_delete_user(
        db,
        user_id,
        scheduled_for=None,
        *,
        allow_administrative_target=False,
        commit=True,
    ):
        captured["scheduled_for"] = scheduled_for
        captured["allow_administrative_target"] = allow_administrative_target
        captured["commit"] = commit
        return SimpleNamespace(
            id=user_id,
            email="user@example.com",
            account_type="regular",
            deleted_at=datetime.now(timezone.utc),
            deletion_scheduled_for=scheduled_for,
        )

    monkeypatch.setattr(user_utils, "soft_delete_user", fake_soft_delete_user)
    monkeypatch.setattr(user_utils, "hard_delete_user", lambda *args, **kwargs: captured.setdefault("hard", True))

    result = user_utils.delete_user(_FakeDb(), _FakeDb(), "user-1")

    assert result["status"] == "success"
    assert result["account_deletion"]["effect"] == "scheduled_deletion"
    assert result["account_deletion"]["restorable"] is True
    assert result["account_deletion"]["retention_days"] == 7
    assert result["account_deletion"]["purge_scheduled_at"] == captured["scheduled_for"]
    assert captured["allow_administrative_target"] is False
    assert captured["commit"] is False
    assert captured.get("hard") is None


def test_delete_user_response_discloses_immediate_erasure(monkeypatch):
    _patch_settings(
        monkeypatch,
        {
            ("users", "user_deletion_mode"): "delete_instantly",
            ("security", "auth_logs_retention_after_user_delete_mode"): "retain",
            ("security", "audit_logs_retention_after_user_delete_mode"): "retain",
        },
    )
    _patch_delete_user_dependencies(monkeypatch)
    captured = {}

    monkeypatch.setattr(user_utils, "hard_delete_user", lambda *args, **kwargs: captured.setdefault("hard", True))
    monkeypatch.setattr(user_utils, "soft_delete_user", lambda *args, **kwargs: captured.setdefault("soft", True))

    result = user_utils.delete_user(_FakeDb(), _FakeDb(), "user-1")

    assert result["status"] == "success"
    assert result["account_deletion"] == {
        "mode": "delete_instantly",
        "effect": "erasure",
        "restorable": False,
        "retention_days": None,
        "purge_scheduled_at": None,
    }
    assert captured["hard"] is True
    assert captured.get("soft") is None


def test_delete_user_invalid_auth_log_retention_falls_back_to_default_schedule(monkeypatch):
    _patch_settings(
        monkeypatch,
        {
            ("users", "user_deletion_mode"): "delete_instantly",
            ("security", "auth_logs_retention_after_user_delete_mode"): "unexpected",
            ("security", "auth_logs_retention_delete_after_days"): "not-a-number",
            ("security", "audit_logs_retention_after_user_delete_mode"): "retain",
        },
    )
    _patch_delete_user_dependencies(monkeypatch)
    captured = {}

    monkeypatch.setattr(user_utils, "hard_delete_user", lambda *args, **kwargs: captured.setdefault("hard", True))
    monkeypatch.setattr(user_utils, "soft_delete_user", lambda *args, **kwargs: captured.setdefault("soft", True))
    monkeypatch.setattr(
        user_utils,
        "schedule_auth_log_deletion",
        lambda _db_log, user_id, days: captured.setdefault(
            "auth_log_schedule",
            {"user_id": user_id, "days": days},
        ),
    )

    user_utils.delete_user(_FakeDb(), _FakeDb(), "user-1")

    assert captured["hard"] is True
    assert captured["auth_log_schedule"] == {
        "user_id": "user-1",
        "days": DEFAULT_SETTINGS["security"]["auth_logs_retention_delete_after_days"],
    }


def test_immediate_audit_retention_does_not_erase_a_concurrently_restored_user(
    monkeypatch,
):
    query = SimpleNamespace()
    query.filter = lambda *_args, **_kwargs: query
    query.with_for_update = lambda: query
    query.populate_existing = lambda: query
    query.first = lambda: SimpleNamespace(deleted_at=None)
    main_db = SimpleNamespace(
        query=lambda _model: query,
        get_bind=lambda: object(),
        rollback_calls=0,
    )

    def rollback():
        main_db.rollback_calls += 1

    main_db.rollback = rollback
    deleted = []
    notifications = []
    monkeypatch.setattr(
        user_utils,
        "cancel_audit_log_deletions_for_user",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        user_utils,
        "audit_log_erasure_guard",
        lambda *_args, **_kwargs: nullcontext(object()),
    )
    monkeypatch.setattr(
        user_utils,
        "delete_audit_logs_for_user",
        lambda *_args, **_kwargs: deleted.append(True),
    )
    monkeypatch.setattr(
        user_utils,
        "delete_admin_notifications_for_user",
        lambda *_args, **_kwargs: notifications.append(True),
    )

    user_utils._apply_audit_log_retention(
        object(),
        "restored-user",
        {"mode": "delete_instantly", "delete_immediately": True},
        main_db=main_db,
    )

    assert deleted == []
    assert notifications == []
    assert main_db.rollback_calls == 1


def test_immediate_audit_retention_holds_guard_through_cross_database_cleanup(
    monkeypatch,
):
    events = []
    query = SimpleNamespace()
    query.filter = lambda *_args, **_kwargs: query
    query.with_for_update = lambda: query
    query.populate_existing = lambda: query
    query.first = lambda: SimpleNamespace(deleted_at=datetime.now(timezone.utc))
    main_db = SimpleNamespace(
        query=lambda _model: query,
        get_bind=lambda: object(),
    )

    @contextmanager
    def guard(*_args, **_kwargs):
        events.append("guard_enter")
        yield "locked-guard"
        events.append("guard_exit")

    def delete_logs(_db_log, _user_id, **kwargs):
        assert kwargs["main_db"] is main_db
        assert kwargs["erasure_guard_db"] == "locked-guard"
        events.append("delete_logs")

    monkeypatch.setattr(
        user_utils,
        "cancel_audit_log_deletions_for_user",
        lambda *_args, **_kwargs: events.append("cancel_queue"),
    )
    monkeypatch.setattr(user_utils, "audit_log_erasure_guard", guard)
    monkeypatch.setattr(user_utils, "delete_audit_logs_for_user", delete_logs)
    monkeypatch.setattr(
        user_utils,
        "delete_admin_notifications_for_user",
        lambda *_args, **_kwargs: events.append("delete_notifications"),
    )

    user_utils._apply_audit_log_retention(
        object(),
        "deleted-user",
        {"mode": "delete_instantly", "delete_immediately": True},
        main_db=main_db,
    )

    assert events == [
        "cancel_queue",
        "guard_enter",
        "delete_logs",
        "delete_notifications",
        "guard_exit",
    ]


def test_user_settings_init_includes_user_deletion_policy(monkeypatch):
    _patch_settings(monkeypatch, {})
    monkeypatch.setattr(
        user_utils,
        "get_user",
        lambda *args, **kwargs: SimpleNamespace(
            id="user-1",
            account_type="regular",
            temporary_expires_at=None,
        ),
    )
    monkeypatch.setattr(user_utils, "get_user_settings", lambda *args, **kwargs: {})
    monkeypatch.setattr(user_utils, "get_user_setting_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(user_utils, "get_user_group_setting_value", lambda *args, **kwargs: True)
    monkeypatch.setattr(user_utils, "get_effective_pinned_model_ids_for_user", lambda *args, **kwargs: [])
    monkeypatch.setattr(user_utils, "get_login_passkey_policy", lambda *args, **kwargs: {})
    monkeypatch.setattr(user_utils, "coerce_bool", lambda value, default=False: default if value is None else bool(value))
    monkeypatch.setattr(user_utils, "normalize_utc_datetime", lambda value: value)
    monkeypatch.setattr(group_management, "managed_groups_for_user", lambda *args, **kwargs: [])
    monkeypatch.setattr(twofa_provider, "resolve_user_2fa_provider", lambda *args, **kwargs: "totp")

    payload = user_utils.user_settings_init("user-1", _FakeDb())

    assert payload["user_deletion_policy"]["effect"] == "scheduled_deletion"
    assert payload["user_deletion_policy"]["restorable"] is True


def test_user_settings_init_coerces_sidebar_feature_group_flags(monkeypatch):
    _patch_settings(monkeypatch, {})
    monkeypatch.setattr(
        user_utils,
        "get_user",
        lambda *args, **kwargs: SimpleNamespace(
            id="user-1",
            account_type="regular",
            temporary_expires_at=None,
        ),
    )
    monkeypatch.setattr(user_utils, "get_user_settings", lambda *args, **kwargs: {})
    monkeypatch.setattr(user_utils, "get_user_setting_value", lambda *args, **kwargs: None)

    def fake_group_setting(_user_id, page, key, _db):
        if (page, key) == ("projects", "enable_projects"):
            return "false"
        if (page, key) == ("automations", "enabled_automations"):
            return "false"
        return True

    monkeypatch.setattr(user_utils, "get_user_group_setting_value", fake_group_setting)
    monkeypatch.setattr(user_utils, "get_effective_pinned_model_ids_for_user", lambda *args, **kwargs: [])
    monkeypatch.setattr(user_utils, "get_login_passkey_policy", lambda *args, **kwargs: {})
    monkeypatch.setattr(user_utils, "normalize_utc_datetime", lambda value: value)
    monkeypatch.setattr(group_management, "managed_groups_for_user", lambda *args, **kwargs: [])
    monkeypatch.setattr(twofa_provider, "resolve_user_2fa_provider", lambda *args, **kwargs: "totp")

    payload = user_utils.user_settings_init("user-1", _FakeDb())

    assert payload["enable_projects"] is False
    assert payload["enable_automations"] is False
