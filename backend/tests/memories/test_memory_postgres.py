"""Opt-in migration and row-lock regression against an isolated PostgreSQL schema."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import threading
import time
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.memories import service
from app.memories.models import Memory, MemoryDeletion, MemoryProfile
from app.workers.models import DurableWorkerJob, enqueue_worker_job
from .test_memory_consolidation import _candidate, _session


pytestmark = pytest.mark.skipif(
    not os.getenv("MEMORY_TEST_DATABASE_URL"),
    reason="Set MEMORY_TEST_DATABASE_URL to a disposable PostgreSQL database",
)


def test_migration_preserves_jobs_and_deletion_serializes_against_inflight_merge(
    monkeypatch,
):
    schema = "memory_test_" + uuid.uuid4().hex
    engine = create_engine(os.environ["MEMORY_TEST_DATABASE_URL"])
    isolated = engine.execution_options(
        schema_translate_map={None: schema, Base.metadata.schema: schema}
    )
    db = None
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        db = _session(isolated)
        DurableWorkerJob.__table__.create(isolated)
        MemoryDeletion.__table__.drop(isolated)
        now = datetime.now(timezone.utc)
        old_job = enqueue_worker_job(
            db,
            queue="generation",
            kind="memory_consolidation",
            user_id="user-1",
            idempotency_key="memory:user-1:old",
            payload={"source_text": "private"},
            commit=True,
        )
        job_id = old_job.id
        enqueue_worker_job(
            db,
            queue="generation",
            kind="send",
            user_id="user-1",
            idempotency_key="chat",
            payload={},
            commit=True,
        )
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "alembic_main/versions/memory_isolation_20260904.py"
        )
        spec = importlib.util.spec_from_file_location(
            "memory_isolation_test", migration_path
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as conn:
            monkeypatch.setattr(
                migration,
                "op",
                Operations(
                    MigrationContext.configure(
                        conn,
                        opts={"version_table_schema": schema},
                    )
                ),
            )
            migration.upgrade()
        db.expire_all()
        moved = db.get(DurableWorkerJob, job_id)
        assert moved.queue == "memory"
        assert moved.payload == {"source_text": "private"}
        assert moved.idempotency_key == "memory:user-1:old"
        assert (
            db.query(DurableWorkerJob).filter_by(kind="send").one().queue
            == "generation"
        )
        db.commit()

        monkeypatch.setattr(
            "app.logging.models.stage_audit_log_event", lambda *a, **k: None
        )
        fact, _ = service.create_memory(
            db,
            service.MemoryScope.personal("user-1"),
            "Original fact",
            memory_key="preference.answer_length",
        )
        fact_id = fact.id
        db.query(MemoryProfile).one().next_transition_at = now - timedelta(seconds=1)
        db.commit()
        # Hold the same owner lock used by the delete endpoint, then launch an
        # older extraction and verify PostgreSQL actually blocks its merge.
        service._lock_scope_owner(db, service.MemoryScope.personal("user-1"))
        with sessionmaker(bind=isolated)() as maintenance:
            # Maintenance yields to an interactive deletion rather than
            # capturing a stale fact snapshot that could restore its profile.
            assert service.refresh_due_memory_profiles(maintenance) == 0
        ready = threading.Event()
        worker_pid = []

        def merge():
            with sessionmaker(bind=isolated)() as other:
                worker_pid.append(
                    other.execute(text("SELECT pg_backend_pid()")).scalar_one()
                )
                ready.set()
                return service.apply_memory_consolidation(
                    other,
                    user_id="user-1",
                    source_message_id="inflight",
                    source_at=now - timedelta(minutes=1),
                    source_text="I prefer concise answers",
                    candidates=[_candidate()],
                )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(merge)
            try:
                assert ready.wait(5)
                deadline = time.monotonic() + 5
                with engine.connect() as probe:
                    while True:
                        blocked = probe.execute(
                            text("SELECT cardinality(pg_blocking_pids(:pid))"),
                            {"pid": worker_pid[0]},
                        ).scalar_one()
                        if blocked:
                            break
                        assert time.monotonic() < deadline, (
                            "merge did not wait for the deletion lock"
                        )
                        time.sleep(0.01)
                service.delete_memory(
                    db, service.MemoryScope.personal("user-1"), fact_id
                )
            finally:
                db.rollback()  # Also release the lock if a test assertion failed.
            assert future.result(timeout=5)["stale_count"] == 1
        assert db.query(Memory).count() == 0
        assert db.query(MemoryProfile).one().content == ""
        assert db.query(MemoryDeletion).one().memory_id == fact_id
        db.commit()
        with engine.begin() as conn:
            monkeypatch.setattr(
                migration,
                "op",
                Operations(
                    MigrationContext.configure(
                        conn,
                        opts={"version_table_schema": schema},
                    )
                ),
            )
            migration.downgrade()
        db.expire_all()
        assert db.get(DurableWorkerJob, job_id).queue == "generation"
    finally:
        if db is not None:
            db.close()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_profile_state_migration_preserves_facts_and_processing_status(monkeypatch):
    schema = "memory_state_test_" + uuid.uuid4().hex
    engine = create_engine(os.environ["MEMORY_TEST_DATABASE_URL"])
    isolated = engine.execution_options(
        schema_translate_map={None: schema, Base.metadata.schema: schema}
    )
    db = None
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        db = _session(isolated)
        db.add(
            Memory(
                id="fact", user_id="user-1", content="Keep my fact", content_key="fact"
            )
        )
        db.flush()
        service.rebuild_memory_profile(db, "user-1")
        service.set_memory_run_status(
            db,
            "user-1",
            source_message_id="message",
            source_at=datetime.now(timezone.utc),
            run_status="failed",
            commit=True,
        )
        db.close()
        path = (
            Path(__file__).resolve().parents[2]
            / "alembic_main/versions/memory_state_20260904.py"
        )
        spec = importlib.util.spec_from_file_location("memory_state_test", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            monkeypatch.setattr(
                migration,
                "op",
                Operations(
                    MigrationContext.configure(
                        connection, opts={"version_table_schema": schema}
                    )
                ),
            )
            migration.downgrade()
            old_status = connection.execute(
                text(f'SELECT last_run_status FROM "{schema}".memory_profiles')
            ).scalar_one()
            assert old_status == "failed"
            migration.upgrade()
            state = connection.execute(
                text(
                    f'SELECT last_run_status, last_processed_message_id FROM "{schema}".memory_states'
                )
            ).one()
            assert tuple(state) == ("failed", "message")
            assert (
                connection.execute(
                    text(f'SELECT source_revision FROM "{schema}".memory_profiles')
                ).scalar_one()
                is None
            )
            assert (
                connection.execute(
                    text(f'SELECT content FROM "{schema}".memories')
                ).scalar_one()
                == "Keep my fact"
            )
    finally:
        if db is not None:
            db.close()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
