from __future__ import annotations

from datetime import datetime, timezone
from importlib import util
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
)


def test_migration_cancels_legacy_events_and_seeds_deleted_user_fences(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2]
        / "alembic_main/versions/audit_event_erasure_20260830.py"
    )
    spec = util.spec_from_file_location("audit_event_erasure_migration", module_path)
    assert spec is not None and spec.loader is not None
    migration = util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    metadata = MetaData()
    outbox = Table(
        "audit_event_outbox",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("user_id", String(64), nullable=False),
        Column("reason", Text, nullable=True),
        Column("details", JSON, nullable=True),
        Column("ip_address", Text, nullable=True),
        Column("user_agent", Text, nullable=True),
        Column("subjects_indexed", Boolean, nullable=False, default=False),
        Column("status", String(24), nullable=False),
        Column("error_code", String(64), nullable=True),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    jobs = Table(
        "durable_worker_jobs",
        metadata,
        Column("id", String, primary_key=True),
        Column("queue", String(32), nullable=False),
        Column("kind", String(64), nullable=False),
        Column("user_id", String, nullable=True),
        Column("payload", JSON, nullable=True),
        Column("result", JSON, nullable=True),
        Column("status", String(24), nullable=False),
        Column("idempotency_key", String(200), nullable=False),
        Column("cancel_requested", Boolean, nullable=False, default=False),
        Column("lease_owner", String(96), nullable=True),
        Column("leased_at", DateTime(timezone=True), nullable=True),
        Column("lease_expires_at", DateTime(timezone=True), nullable=True),
        Column("error_code", String(64), nullable=True),
        Column("finished_at", DateTime(timezone=True), nullable=True),
        Column("reconciled_at", DateTime(timezone=True), nullable=True),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    users = Table(
        "users",
        metadata,
        Column("id", String, primary_key=True),
        Column("deleted_at", DateTime(timezone=True), nullable=True),
    )
    states = Table(
        "audit_event_subject_states",
        metadata,
        Column("subject_fingerprint", String(64), primary_key=True),
        Column("erased_at", DateTime(timezone=True), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(bind=engine)
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        connection.execute(
            users.insert(),
            [
                {"id": "deleted-user", "deleted_at": now},
                {"id": "active-user", "deleted_at": None},
            ],
        )
        connection.execute(
            outbox.insert(),
            [
                {
                    "id": "legacy-active",
                    "user_id": "system",
                    "reason": "private",
                    "details": {"owner_id": "already-erased"},
                    "ip_address": "private-ip",
                    "user_agent": "private-device",
                    "subjects_indexed": False,
                    "status": "pending",
                    "updated_at": now,
                },
                {
                    "id": "legacy-delivered",
                    "user_id": "user-1",
                    "reason": "old",
                    "details": {"user_id": "user-1"},
                    "ip_address": None,
                    "user_agent": None,
                    "subjects_indexed": False,
                    "status": "delivered",
                    "updated_at": now,
                },
            ],
        )
        connection.execute(
            jobs.insert().values(
                id="legacy-job",
                queue="events",
                kind="audit_log",
                user_id="system",
                payload={"event_id": "legacy-active"},
                result={"private": True},
                status="processing",
                idempotency_key="audit:legacy-active",
                cancel_requested=False,
                lease_owner="old-worker",
                leased_at=now,
                lease_expires_at=now,
                updated_at=now,
            )
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

        migration._seed_deleted_user_states(None)
        migration._cancel_legacy_audit_events(None)

        rows = {
            row.id: row
            for row in connection.execute(outbox.select()).mappings().all()
        }
        assert rows["legacy-active"].status == "cancelled"
        assert rows["legacy-active"].error_code == "migration_privacy_fence"
        assert rows["legacy-delivered"].status == "delivered"
        assert rows["legacy-delivered"].error_code == "migration_privacy_fence"
        for row in rows.values():
            assert row.user_id == ""
            assert row.reason is None and row.details is None
            assert row.ip_address is None and row.user_agent is None
            assert row.subjects_indexed is True

        job = connection.execute(jobs.select()).mappings().one()
        assert job.status == "cancelled"
        assert job.error_code == "migration_privacy_fence"
        assert job.user_id is None and job.payload is None and job.result is None
        assert job.lease_owner is None and job.cancel_requested is True

        assert set(
            connection.execute(
                states.select().with_only_columns(states.c.subject_fingerprint)
            ).scalars()
        ) == {
            migration.hashlib.sha256(b"deleted-user").hexdigest(),
        }
