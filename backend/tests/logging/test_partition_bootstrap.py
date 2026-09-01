from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.logging import models as logging_models
from app.logging import worker as logging_worker
from app.database import AuditBase, Base
from app.workers import events
from app.workers.models import AuditEventSubjectState


def test_ensure_audit_log_partitions_prepares_current_and_future_months(monkeypatch):
    calls: list[tuple[str, datetime]] = []

    def fake_ensure(session, table, instant):
        calls.append((table.name, instant))
        return True

    monkeypatch.setattr(logging_models, "_ensure_monthly_partition", fake_ensure)

    created = logging_models.ensure_audit_log_partitions(
        session=object(),
        months_ahead=2,
        instant=datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc),
    )

    assert created == 6
    assert [table_name for table_name, _ in calls] == [
        "logs",
        "authenticationlogs",
        "logs",
        "authenticationlogs",
        "logs",
        "authenticationlogs",
    ]
    assert [(instant.year, instant.month) for _, instant in calls] == [
        (2026, 5),
        (2026, 5),
        (2026, 6),
        (2026, 6),
        (2026, 7),
        (2026, 7),
    ]


def test_prepare_logging_partitions_commits_audit_and_main_sessions(monkeypatch):
    audit_session = MagicMock()
    main_session = MagicMock()

    monkeypatch.setattr(logging_worker, "AuditSessionLocal", lambda: audit_session)
    monkeypatch.setattr(logging_worker, "SessionLocal", lambda: main_session)
    monkeypatch.setattr(logging_worker, "ensure_audit_log_partitions", lambda session: 4)
    monkeypatch.setattr(logging_worker, "ensure_admin_notification_partitions", lambda session: 2)

    created = logging_worker.prepare_logging_partitions()

    assert created == 6
    audit_session.commit.assert_called_once()
    main_session.commit.assert_called_once()
    audit_session.close.assert_called_once()
    main_session.close.assert_called_once()


def test_lifespan_prepares_partitions_before_first_audit_event(monkeypatch):
    from app import main as app_main

    class StartupReachedDatabase(Exception):
        pass

    calls: list[str] = []
    monkeypatch.setattr(app_main, "log_startup_status", lambda: None)
    monkeypatch.setattr(app_main, "ensure_data_directories", lambda: None)
    monkeypatch.setattr(app_main, "ensure_backup_directories", lambda: None)
    monkeypatch.setattr(
        app_main,
        "prepare_logging_partitions",
        lambda: calls.append("prepare_partitions"),
    )
    monkeypatch.setattr(
        app_main,
        "_log_application_event",
        lambda action, _details: calls.append(action),
    )

    def stop_after_startup_audit():
        calls.append("open_main_database")
        raise StartupReachedDatabase

    monkeypatch.setattr(app_main, "SessionLocal", stop_after_startup_audit)

    async def enter_lifespan() -> None:
        async with app_main.lifespan(app_main.app):
            pass

    with pytest.raises(StartupReachedDatabase):
        asyncio.run(enter_lifespan())

    assert calls == [
        "prepare_partitions",
        "APPLICATION_START",
        "open_main_database",
    ]


def test_lifespan_fails_closed_when_partition_preparation_fails(monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main, "log_startup_status", lambda: None)
    monkeypatch.setattr(app_main, "ensure_data_directories", lambda: None)
    monkeypatch.setattr(app_main, "ensure_backup_directories", lambda: None)
    monkeypatch.setattr(
        app_main,
        "prepare_logging_partitions",
        MagicMock(side_effect=RuntimeError("partition DDL failed")),
    )
    audit_event = MagicMock()
    open_main_database = MagicMock()
    monkeypatch.setattr(app_main, "_log_application_event", audit_event)
    monkeypatch.setattr(app_main, "SessionLocal", open_main_database)

    async def enter_lifespan() -> None:
        async with app_main.lifespan(app_main.app):
            pass

    with pytest.raises(RuntimeError, match="partition DDL failed"):
        asyncio.run(enter_lifespan())

    audit_event.assert_not_called()
    open_main_database.assert_not_called()


def test_auth_log_cleanup_interval_uses_security_setting():
    assert logging_worker._auth_log_cleanup_interval_seconds(
        {"auth_logs_cleanup_interval_seconds": "120"}
    ) == 120
    assert logging_worker._worker_wait_seconds(
        {"auth_logs_cleanup_interval_seconds": "120"}
    ) == 120
    assert logging_worker._worker_wait_seconds(
        {"auth_logs_cleanup_interval_seconds": "3600"}
    ) == logging_worker.SLEEP_INTERVAL_SECONDS


def test_process_authentication_log_auto_cleanup_respects_disabled_setting(monkeypatch):
    age_cleanup = MagicMock()
    count_cleanup = MagicMock()
    monkeypatch.setattr(logging_worker, "delete_authentication_logs_older_than", age_cleanup)
    monkeypatch.setattr(logging_worker, "prune_authentication_logs_to_max_count", count_cleanup)

    processed = logging_worker._process_authentication_log_auto_cleanup(
        MagicMock(),
        {
            "auth_logs_auto_cleanup_enabled": False,
            "auth_logs_cleanup_mode": "age",
            "auth_logs_max_age_days": 90,
        },
    )

    assert processed is False
    age_cleanup.assert_not_called()
    count_cleanup.assert_not_called()


def test_process_authentication_log_auto_cleanup_uses_count_mode(monkeypatch):
    audit_session = MagicMock()
    count_cleanup = MagicMock(return_value=3)
    monkeypatch.setattr(logging_worker, "prune_authentication_logs_to_max_count", count_cleanup)

    processed = logging_worker._process_authentication_log_auto_cleanup(
        audit_session,
        {
            "auth_logs_auto_cleanup_enabled": True,
            "auth_logs_cleanup_mode": "count",
            "auth_logs_max_count": "42",
        },
    )

    assert processed is True
    count_cleanup.assert_called_once_with(audit_session, 42)


def test_cancelled_processing_audit_deletion_cannot_resume(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(
        bind=engine,
        tables=[logging_models.AuditLogDeletionQueue.__table__],
    )
    factory = sessionmaker(bind=engine)
    worker_session = factory()
    cancel_session = factory()
    try:
        job = logging_models.AuditLogDeletionQueue(
            id="audit-delete-1",
            user_id="user-1",
            scheduled_for=datetime.now(timezone.utc),
            status="pending",
        )
        worker_session.add(job)
        worker_session.commit()
        assert logging_worker._acquire_audit_log_job(worker_session, job) is True

        assert (
            logging_models.cancel_audit_log_deletions_for_user(
                cancel_session,
                "user-1",
            )
            == 1
        )
        monkeypatch.setattr(
            logging_worker,
            "delete_audit_logs_for_user",
            MagicMock(side_effect=AssertionError("cancelled deletion resumed")),
        )
        monkeypatch.setattr(
            logging_worker,
            "delete_admin_notifications_for_user",
            MagicMock(side_effect=AssertionError("cancelled deletion resumed")),
        )

        logging_worker._process_audit_log_job(worker_session, job)

        worker_session.expire_all()
        persisted = worker_session.query(logging_models.AuditLogDeletionQueue).one()
        assert persisted.status == "cancelled"
        logging_worker.delete_audit_logs_for_user.assert_not_called()
        logging_worker.delete_admin_notifications_for_user.assert_not_called()
    finally:
        cancel_session.close()
        worker_session.close()


def test_processing_audit_deletion_revalidates_restored_user(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(
        bind=engine,
        tables=[logging_models.AuditLogDeletionQueue.__table__],
    )
    factory = sessionmaker(bind=engine)
    worker_session = factory()

    class MainQuery:
        def filter(self, *_args):
            return self

        def with_for_update(self):
            return self

        def populate_existing(self):
            return self

        def first(self):
            return SimpleNamespace(deleted_at=None)

    class MainSession:
        def query(self, *_args):
            return MainQuery()

        def get_bind(self):
            return object()

        def close(self):
            return None

    try:
        job = logging_models.AuditLogDeletionQueue(
            id="audit-delete-restored",
            user_id="user-restored",
            scheduled_for=datetime.now(timezone.utc),
            status="processing",
        )
        worker_session.add(job)
        worker_session.commit()
        monkeypatch.setattr(logging_worker, "SessionLocal", MainSession)
        monkeypatch.setattr(logging_worker, "AuditSessionLocal", MagicMock)
        monkeypatch.setattr(
            logging_worker,
            "audit_log_erasure_guard",
            lambda *_args, **_kwargs: nullcontext(object()),
        )
        monkeypatch.setattr(
            logging_worker,
            "delete_audit_logs_for_user",
            MagicMock(side_effect=AssertionError("restored user was erased")),
        )
        monkeypatch.setattr(
            logging_worker,
            "delete_admin_notifications_for_user",
            MagicMock(side_effect=AssertionError("restored user was erased")),
        )

        logging_worker._process_audit_log_job(worker_session, job)

        worker_session.expire_all()
        assert worker_session.query(logging_models.AuditLogDeletionQueue).one().status == (
            "cancelled"
        )
        logging_worker.delete_audit_logs_for_user.assert_not_called()
        logging_worker.delete_admin_notifications_for_user.assert_not_called()
    finally:
        worker_session.close()


@pytest.mark.parametrize(
    ("factory_name", "kwargs"),
    [
        (
            "create_audit_log",
            {
                "db_log": MagicMock(),
                "user_id": "user-1",
                "action": "login",
                "reason": "ok",
            },
        ),
        (
            "create_authentication_log",
            {
                "db": MagicMock(),
                "auth_type": "password",
                "status": "success",
                "message": "ok",
                "user_id": "user-1",
                "device_info": "browser",
                "ip_address": "203.0.113.10",
            },
        ),
    ],
)
def test_log_write_paths_do_not_call_partition_creation(monkeypatch, factory_name, kwargs):
    def fail_if_called(*args, **unused_kwargs):
        raise AssertionError("request-time partition creation should not run")

    monkeypatch.setattr(logging_models, "_ensure_monthly_partition", fail_if_called)
    if factory_name == "create_audit_log":
        main_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            bind=main_engine,
            tables=[AuditEventSubjectState.__table__],
        )
        monkeypatch.setattr(events, "SessionLocal", sessionmaker(bind=main_engine))

    factory = getattr(logging_models, factory_name)
    factory(**kwargs)


def test_admin_notification_write_path_does_not_call_partition_creation(monkeypatch):
    db = MagicMock()

    def fail_if_called(*args, **unused_kwargs):
        raise AssertionError("request-time partition creation should not run")

    monkeypatch.setattr(logging_models, "_ensure_monthly_partition", fail_if_called)
    monkeypatch.setattr(logging_models, "_ensure_admin_notifications_user_id", lambda session: None)
    monkeypatch.setattr(logging_models, "_ensure_admin_notifications_type", lambda session: None)
    monkeypatch.setattr(logging_models, "_send_notification_webhook", lambda payload: None)

    notification = logging_models.create_admin_notification(
        db,
        "general",
        "scheduled maintenance for admin@example.com",
        details={"severity": "info"},
        user_id="user-1",
        notification_type="info",
    )

    assert notification.message == "scheduled maintenance for admin@example.com"


def test_retention_worker_survives_partition_preparation_failure(monkeypatch):
    class QueryStub:
        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def all(self):
            return []

    class StopAfterWait:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, *_args, **_kwargs):
            self.stopped = True
            return True

    audit_session = MagicMock()
    audit_session.query.return_value = QueryStub()
    main_session = MagicMock()

    monkeypatch.setattr(logging_worker, "new_lock_owner", lambda: "owner-1")
    monkeypatch.setattr(logging_worker, "try_acquire_lock", lambda *_args: True)
    release_lock = MagicMock()
    monkeypatch.setattr(logging_worker, "release_lock", release_lock)
    monkeypatch.setattr(
        logging_worker,
        "prepare_logging_partitions",
        MagicMock(side_effect=RuntimeError("partition DDL failed")),
    )
    monkeypatch.setattr(
        logging_worker,
        "_load_security_settings",
        lambda: {
            "auth_logs_auto_cleanup_enabled": False,
            "auth_logs_cleanup_interval_seconds": 3600,
        },
    )
    monkeypatch.setattr(logging_worker, "AuditSessionLocal", lambda: audit_session)
    monkeypatch.setattr(logging_worker, "SessionLocal", lambda: main_session)
    monkeypatch.setattr(
        logging_worker, "_process_scheduled_user_deletions", lambda *_args: False
    )
    monkeypatch.setattr(
        logging_worker, "_process_password_reset_token_retention", lambda *_args: False
    )
    monkeypatch.setattr(
        logging_worker,
        "_process_ip_address_security_statistics_retention",
        lambda *_args: False,
    )

    logging_worker._retention_worker(StopAfterWait())

    audit_session.close.assert_called_once()
    main_session.close.assert_called_once()
    release_lock.assert_called_once_with(logging_worker.WORKER_LOCK_NAME, "owner-1")
