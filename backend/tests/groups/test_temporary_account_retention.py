"""Focused tests for temporary-account retention policy calculation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.groups import temporary_account_retention as retention
from app.users.models import User


def test_retention_index_orders_equality_columns_before_expiry_range():
    """Keep retained history outside the worker's expiry-range scan."""

    index = next(
        candidate
        for candidate in User.__table__.indexes
        if candidate.name == "ix_users_temporary_account_retention"
    )

    assert [column.name for column in index.columns] == [
        "account_type",
        "deleted_at",
        "temporary_expires_at",
    ]


def test_delete_after_days_is_anchored_to_expiry(monkeypatch):
    values = {
        "temporary_account_deletion_mode": "delete_after_days",
        "temporary_account_retention_days": 7,
    }
    monkeypatch.setattr(retention, "_configured_value", lambda _db, key: values[key])
    expired_at = datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc)

    policy = retention.get_temporary_account_retention_policy(
        SimpleNamespace(),
        lifecycle_at=expired_at,
    )

    assert policy["mode"] == "delete_after_days"
    assert policy["purge_scheduled_at"] == expired_at + timedelta(days=7)


def test_retain_mode_marks_account_without_scheduling_erasure(monkeypatch):
    values = {
        "temporary_account_deletion_mode": "retain",
        "temporary_account_retention_days": 30,
    }
    monkeypatch.setattr(retention, "_configured_value", lambda _db, key: values[key])
    expired_at = datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc)
    account = SimpleNamespace(account_type="temporary", deleted_at=None, deletion_scheduled_for=None)

    policy = retention.mark_temporary_account_for_retention(
        account,
        SimpleNamespace(),
        lifecycle_at=expired_at,
    )

    assert policy["mode"] == "retain"
    assert account.deleted_at == expired_at
    assert account.deletion_scheduled_for is None


def test_retention_marking_is_idempotent(monkeypatch):
    values = {
        "temporary_account_deletion_mode": "delete_instantly",
        "temporary_account_retention_days": 30,
    }
    monkeypatch.setattr(retention, "_configured_value", lambda _db, key: values[key])
    original = datetime(2026, 7, 1, tzinfo=timezone.utc)
    scheduled = datetime(2026, 7, 2, tzinfo=timezone.utc)
    account = SimpleNamespace(
        account_type="temporary",
        deleted_at=original,
        deletion_scheduled_for=scheduled,
    )

    policy = retention.mark_temporary_account_for_retention(
        account,
        SimpleNamespace(),
        lifecycle_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert account.deleted_at == original
    assert account.deletion_scheduled_for == scheduled
    assert policy["lifecycle_at"] == original
    assert policy["purge_scheduled_at"] == scheduled
