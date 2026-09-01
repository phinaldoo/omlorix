from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
from app.database import Base
from app.logging import models as logging_models
from app.users import erasure_ledger
from app.workers.models import (
    AuditErasureReconciliationCheckpoint,
    AuditEventSubjectState,
    audit_event_subject_fingerprint,
)


def test_erasure_ledger_round_trip_uses_private_append_only_file(monkeypatch, tmp_path):
    ledger_path = tmp_path / "erasure.jsonl"
    monkeypatch.setattr(erasure_ledger, "ERASURE_LEDGER_PATH", ledger_path)
    policy = {"mode": "retain", "retention_days": None, "delete_immediately": False}

    erasure_ledger.record_completed_user_erasure(
        "user-1",
        auth_policy=policy,
        audit_policy=policy,
        erased_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    records = erasure_ledger.load_completed_user_erasures()
    assert records["user-1"]["auth_policy"]["mode"] == "retain"
    assert records["user-1"]["erased_at"] == datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert records["user-1"]["retention_started_at"] == datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert ledger_path.stat().st_mode & 0o777 == 0o600


def test_erasure_ledger_two_phase_state_survives_commit_ack_crash(monkeypatch, tmp_path):
    ledger_path = tmp_path / "erasure.jsonl"
    monkeypatch.setattr(erasure_ledger, "ERASURE_LEDGER_PATH", ledger_path)
    policy = {"mode": "retain", "retention_days": None, "delete_immediately": False}
    erased_at = datetime(2026, 8, 30, tzinfo=timezone.utc)

    operation_id = erasure_ledger.record_user_erasure_intent(
        "user-intent",
        auth_policy=policy,
        audit_policy=policy,
        erased_at=erased_at,
    )
    assert erasure_ledger.erasure_pending_dir().stat().st_mode & 0o777 == 0o700
    assert next(erasure_ledger.erasure_pending_dir().iterdir()).stat().st_mode & 0o777 == 0o600
    assert set(erasure_ledger.load_pending_user_erasures()) == {"user-intent"}
    assert erasure_ledger.load_completed_user_erasures() == {}

    erasure_ledger.record_completed_user_erasure(
        "user-intent",
        operation_id=operation_id,
        auth_policy=policy,
        audit_policy=policy,
        erased_at=erased_at,
    )
    assert erasure_ledger.load_pending_user_erasures() == {}
    assert set(erasure_ledger.load_completed_user_erasures()) == {"user-intent"}

    cancelled_operation = erasure_ledger.record_user_erasure_intent(
        "user-rollback",
        auth_policy=policy,
        audit_policy=policy,
        erased_at=erased_at,
    )
    erasure_ledger.record_cancelled_user_erasure(
        "user-rollback",
        operation_id=cancelled_operation,
        auth_policy=policy,
        audit_policy=policy,
        erased_at=erased_at,
        retention_started_at=erased_at,
    )
    assert "user-rollback" not in erasure_ledger.load_pending_user_erasures()
    assert "user-rollback" not in erasure_ledger.load_completed_user_erasures()


def test_normal_pending_resolution_uses_sparse_index_not_historical_ledger(
    monkeypatch,
    tmp_path,
):
    ledger_path = tmp_path / "erasure.jsonl"
    monkeypatch.setattr(erasure_ledger, "ERASURE_LEDGER_PATH", ledger_path)
    policy = {"mode": "retain", "retention_days": None, "delete_immediately": False}
    erasure_ledger.record_user_erasure_intent(
        "user-sparse",
        auth_policy=policy,
        audit_policy=policy,
    )
    original_loader = erasure_ledger._load_erasure_operations

    def sparse_only(source_path=None):
        if source_path is None:
            raise AssertionError("historical ledger was scanned")
        return original_loader(source_path)

    monkeypatch.setattr(erasure_ledger, "_load_erasure_operations", sparse_only)
    assert set(erasure_ledger.load_pending_user_erasures()) == {"user-sparse"}


def test_restore_reconciliation_marker_is_private_and_fsync_backed(monkeypatch, tmp_path):
    marker = tmp_path / ".reconcile"
    monkeypatch.setattr(
        erasure_ledger,
        "ERASURE_RECONCILIATION_REQUIRED_PATH",
        marker,
    )

    erasure_ledger.mark_restore_erasure_reconciliation_required()

    assert erasure_ledger.restore_erasure_reconciliation_pending() is True
    assert marker.stat().st_mode & 0o777 == 0o600
    erasure_ledger.clear_restore_erasure_reconciliation_required()
    assert erasure_ledger.restore_erasure_reconciliation_pending() is False


def test_erasure_ledger_fails_closed_for_invalid_records(monkeypatch, tmp_path):
    ledger_path = tmp_path / "erasure.jsonl"
    ledger_path.write_text('{"version":1,"user_id":"user-1"}\n', encoding="utf-8")
    monkeypatch.setattr(erasure_ledger, "ERASURE_LEDGER_PATH", ledger_path)

    with pytest.raises(ValueError, match="line 1"):
        erasure_ledger.load_completed_user_erasures()


def test_restore_reconciliation_keeps_original_retention_deadline():
    scheduled = []
    erased_at = datetime.now(timezone.utc) - timedelta(days=2)
    result = erasure_ledger._apply_retention_policy_after_restore(
        object(),
        user_id="user-1",
        erased_at=erased_at,
        policy={"mode": "delete_after_days", "retention_days": 7, "delete_immediately": False},
        cancel_pending=lambda *args: None,
        delete_now=lambda *args: None,
        schedule=lambda _db, user_id, days, *, scheduled_for: scheduled.append(
            (user_id, days, scheduled_for)
        ),
    )

    assert result == "scheduled"
    assert scheduled[0][0:2] == ("user-1", 7)
    assert scheduled[0][2] == erased_at + timedelta(days=7)


def test_completed_immediate_and_due_audit_erasures_reseed_subject_fences(
    monkeypatch,
):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[AuditEventSubjectState.__table__])
    factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(
        erasure_ledger,
        "load_completed_user_erasures",
        lambda: {
            "immediate-user": {
                "erased_at": now - timedelta(days=5),
                "audit_policy": {
                    "mode": "delete_instantly",
                    "delete_immediately": True,
                },
            },
            "due-user": {
                "erased_at": now - timedelta(days=10),
                "retention_started_at": now - timedelta(days=10),
                "audit_policy": {
                    "mode": "delete_after_days",
                    "retention_days": 7,
                    "delete_immediately": False,
                },
            },
            "retained-user": {
                "erased_at": now - timedelta(days=5),
                "audit_policy": {
                    "mode": "retain",
                    "delete_immediately": False,
                },
            },
        },
    )

    assert erasure_ledger.seed_completed_audit_erasure_fences() == 2
    assert erasure_ledger.seed_completed_audit_erasure_fences() == 2

    db = factory()
    try:
        assert {
            row.subject_fingerprint
            for row in db.query(AuditEventSubjectState).all()
        } == {
            audit_event_subject_fingerprint("immediate-user"),
            audit_event_subject_fingerprint("due-user"),
        }
    finally:
        db.close()


def test_migration_reconciliation_reapplies_due_ledger_erasure_to_existing_rows(
    monkeypatch,
):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            AuditErasureReconciliationCheckpoint.__table__,
            AuditEventSubjectState.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        erasure_ledger,
        "load_completed_user_erasures",
        lambda: {
            "immediate-user": {
                "erased_at": now - timedelta(days=2),
                "audit_policy": {
                    "mode": "delete_instantly",
                    "delete_immediately": True,
                },
            },
            "future-user": {
                "erased_at": now - timedelta(days=2),
                "retention_started_at": now - timedelta(days=2),
                "audit_policy": {
                    "mode": "delete_after_days",
                    "retention_days": 30,
                    "delete_immediately": False,
                },
            },
            "retained-user": {
                "erased_at": now - timedelta(days=2),
                "audit_policy": {
                    "mode": "retain",
                    "delete_immediately": False,
                },
            },
        },
    )
    calls = []

    class AuditSession:
        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(database, "AuditSessionLocal", AuditSession)
    monkeypatch.setattr(
        logging_models,
        "cancel_audit_log_deletions_for_user",
        lambda db, user_id: calls.append(("cancel", db, user_id)) or 1,
    )
    monkeypatch.setattr(
        logging_models,
        "delete_audit_logs_for_user",
        lambda db, user_id: calls.append(("logs", db, user_id)) or 3,
    )
    monkeypatch.setattr(
        logging_models,
        "delete_admin_notifications_for_user",
        lambda db, user_id: calls.append(("notifications", db, user_id)) or 2,
    )

    result = erasure_ledger.reconcile_completed_audit_erasures()

    assert result == {
        "subjects_reconciled": 1,
        "audit_logs_deleted": 3,
        "notifications_deleted": 2,
    }
    assert [call[0] for call in calls] == [
        "cancel",
        "logs",
        "notifications",
        "close",
    ]
    assert {call[2] for call in calls[:-1]} == {"immediate-user"}

    db = factory()
    try:
        assert db.get(
            AuditErasureReconciliationCheckpoint,
            erasure_ledger._AUDIT_ERASURE_RECONCILIATION_KEY,
        ) is not None
        assert {
            row.subject_fingerprint
            for row in db.query(AuditEventSubjectState).all()
        } == {audit_event_subject_fingerprint("immediate-user")}
    finally:
        db.close()

    assert erasure_ledger.audit_erasure_reconciliation_pending() is False
    calls.clear()
    assert erasure_ledger.reconcile_completed_audit_erasures() == {
        "subjects_reconciled": 0,
        "audit_logs_deleted": 0,
        "notifications_deleted": 0,
    }
    assert calls == []
