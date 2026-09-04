from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.workers import generation, memory
from app.workers.models import DurableWorkerJob, claim_worker_jobs, enqueue_worker_job


@pytest.mark.parametrize("mode", ["inline", "external"])
def test_memory_jobs_have_separate_admission_claims_and_concurrency(monkeypatch, mode):
    monkeypatch.setenv("MEMORY_WORKER_MODE", mode)
    monkeypatch.setenv("GENERATION_WORKER_MODE", "inline")
    monkeypatch.setenv("REDIS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_WORKER_BATCH_SIZE", "3")
    monkeypatch.setenv("GENERATION_WORKER_BATCH_SIZE", "1")
    assert memory.external_memory_enabled() == (mode == "external")
    assert memory.build_worker().batch_size == 3
    assert generation.build_worker().batch_size == 1
    assert "memory_consolidation" not in generation.build_worker().handlers

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DurableWorkerJob.__table__])
    with sessionmaker(bind=engine)() as db:
        at = datetime.now(timezone.utc)
        payload = dict(
            user_id="user-1",
            source_message_id="message-1",
            source_at=at,
            source_text="Private source",
            byok={"api_key": "private-key"},
        )
        job = memory.enqueue_memory_consolidation_job(db, **payload)
        assert memory.enqueue_memory_consolidation_job(db, **payload).id == job.id
        assert job.expires_at.replace(tzinfo=timezone.utc) == at + timedelta(hours=24)
        enqueue_worker_job(
            db,
            queue="generation",
            kind="send",
            user_id="user-1",
            idempotency_key="chat",
            payload={},
            commit=True,
        )
        chat_jobs = claim_worker_jobs(
            db,
            queue="generation",
            worker_id="chat-worker",
            batch_size=10,
            lease_seconds=120,
        )
        assert [row.kind for row in chat_jobs] == ["send"]
        memory_jobs = claim_worker_jobs(
            db,
            queue="memory",
            worker_id="memory-worker",
            batch_size=10,
            lease_seconds=120,
        )
        assert [row.kind for row in memory_jobs] == ["memory_consolidation"]

