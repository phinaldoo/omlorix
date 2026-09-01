import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.workers.models import (
    AuditEventOutbox,
    DurableWorkerJob,
    ImportStagingReservation,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RETRY,
    JOB_SUCCEEDED,
    QUEUE_FILES,
    QUEUE_EVENTS,
    QUEUE_GENERATION,
    QUEUE_LIFECYCLE,
    QUEUE_RENDERING,
    WorkerJobSnapshot,
    claim_worker_jobs,
    enqueue_worker_job,
    mark_worker_job_cancelled,
    mark_worker_job_failed,
    mark_worker_job_succeeded,
    purge_terminal_worker_jobs,
    request_worker_job_cancellation,
    revive_worker_job_after_lease_expiry,
    worker_job_cancel_requested,
)
from app.workers import generation as generation_worker
from app.workers import files as file_worker
from app.workers import models as worker_models
from app.workers import operations as operations_worker
from app.workers import research as research_worker
from app.workers.runtime import DurableQueueWorker, _resolve_handler_result


def _database_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[DurableWorkerJob.__table__, ImportStagingReservation.__table__],
    )
    return sessionmaker(bind=engine)()


def test_worker_runtime_resolves_async_handler_result():
    async def result():
        await asyncio.sleep(0)
        return {"ok": True}

    assert _resolve_handler_result(result()) == {"ok": True}


def test_async_worker_wait_does_not_reserve_a_thread_while_pending(monkeypatch):
    poll_results = iter([None, {"ok": True}])
    worker_calls = []
    sleeps = []

    monkeypatch.setattr(
        worker_models,
        "_read_worker_job_result",
        lambda job_id: next(poll_results),
    )

    async def fake_run_sync(func, *args):
        worker_calls.append(args)
        return func(*args)

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(worker_models.anyio.to_thread, "run_sync", fake_run_sync)
    monkeypatch.setattr(worker_models.anyio, "sleep", fake_sleep)

    result = asyncio.run(
        worker_models.wait_for_worker_job_async(
            "job-1",
            timeout_seconds=5,
            poll_seconds=0.1,
        )
    )

    assert result == {"ok": True}
    assert worker_calls == [("job-1",), ("job-1",)]
    assert sleeps == [0.1]


def test_sync_worker_wait_backs_off_between_polls(monkeypatch):
    poll_results = iter([None, None, {"ok": True}])
    sleeps = []

    monkeypatch.setattr(
        worker_models,
        "_read_worker_job_result",
        lambda _job_id: next(poll_results),
    )
    monkeypatch.setattr(worker_models.time, "sleep", sleeps.append)

    result = worker_models.wait_for_worker_job(
        "job-1",
        timeout_seconds=5,
        poll_seconds=0.1,
    )

    assert result == {"ok": True}
    assert [round(delay, 2) for delay in sleeps] == [0.1, 0.15]


def test_generation_worker_keeps_sync_session_and_stream_on_bounded_thread(
    monkeypatch,
):
    from app.chats import utils as chat_utils

    loop_thread = threading.get_ident()
    calls = []

    class FakeSession:
        def __init__(self):
            calls.append(("session", threading.get_ident()))

        def close(self):
            calls.append(("close", threading.get_ident()))

    def fake_send_message(*args, **_kwargs):
        calls.append(("stream", threading.get_ident(), args[13]))
        yield '{"t":"n_c","d":"chat-1"}\n'
        yield '{"t":"d","d":"f"}\n'

    monkeypatch.setattr(generation_worker, "SessionLocal", FakeSession)
    monkeypatch.setattr(generation_worker, "require_shared_generation_stream", lambda: None)
    monkeypatch.setattr(generation_worker.cancel_registry, "is_cancelled", lambda _id: False)
    monkeypatch.setattr(
        generation_worker,
        "_active_user",
        lambda _db, _user_id: SimpleNamespace(
            id="user-1",
            group_id="group-1",
            role="user",
        ),
    )
    monkeypatch.setattr(generation_worker, "_publish_worker_only_line", lambda *_args: None)
    monkeypatch.setattr(chat_utils, "send_message", fake_send_message)

    context = SimpleNamespace(raise_if_cancelled=lambda: None)
    job = WorkerJobSnapshot(
        id="job-1",
        queue=QUEUE_GENERATION,
        kind="send",
        user_id="user-1",
        payload={
            "generation_id": "generation-1",
            "request": {"message": "hello"},
        },
        attempt_count=1,
        max_attempts=1,
        expires_at=None,
    )

    assert asyncio.run(generation_worker._consume_send(job, context)) == {
        "generation_id": "generation-1"
    }
    producer_threads = {entry[1] for entry in calls}
    assert len(producer_threads) == 1
    assert producer_threads != {loop_thread}
    assert next(entry[2] for entry in calls if entry[0] == "stream") is not None


def test_file_worker_bounds_and_whitelists_processing_parameters():
    assert file_worker._normalize_operation_params("pdf_page", {"page": "12", "ignored": "x"}) == {
        "page": 12
    }
    assert file_worker._normalize_operation_params("pdf_inspect", {"ignored": "x"}) == {}
    for invalid in (None, True, 0, 1.5, file_worker.PDF_PREVIEW_MAX_PAGES + 1, "not-a-page"):
        try:
            file_worker._normalize_operation_params("pdf_page_image", {"page": invalid})
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"accepted invalid page: {invalid!r}")


def test_jobs_are_encrypted_idempotent_and_claimed_by_priority():
    db = _database_session()
    try:
        later = enqueue_worker_job(
            db,
            queue=QUEUE_GENERATION,
            kind="send_message",
            user_id="user-1",
            payload={"secret": "provider-key", "generation_id": "gen-1"},
            idempotency_key="generation:gen-1",
            priority=50,
            commit=True,
        )
        duplicate = enqueue_worker_job(
            db,
            queue=QUEUE_GENERATION,
            kind="send_message",
            user_id="user-1",
            payload={"secret": "different"},
            idempotency_key="generation:gen-1",
            priority=50,
            commit=True,
        )
        first = enqueue_worker_job(
            db,
            queue=QUEUE_GENERATION,
            kind="send_message",
            user_id="user-1",
            payload={"generation_id": "gen-2"},
            idempotency_key="generation:gen-2",
            priority=1,
            commit=True,
        )

        assert duplicate.id == later.id
        raw_payload = db.execute(
            text("SELECT payload FROM durable_worker_jobs WHERE id = :id"),
            {"id": later.id},
        ).scalar_one()
        assert "provider-key" not in str(raw_payload)

        claimed = claim_worker_jobs(
            db,
            queue=QUEUE_GENERATION,
            worker_id="generation:test",
            batch_size=1,
        )
        assert [row.id for row in claimed] == [first.id]
        snapshot = WorkerJobSnapshot.from_row(claimed[0])
        assert snapshot.payload == {"generation_id": "gen-2"}
        assert snapshot.attempt_count == 1
    finally:
        db.close()


def test_explicit_retry_replaces_only_failed_or_cancelled_idempotent_job():
    db = _database_session()
    try:
        for terminal_status in (JOB_FAILED, JOB_CANCELLED):
            key = f"canvas-latex:user-1:file-{terminal_status}:7"
            original = enqueue_worker_job(
                db,
                queue=QUEUE_RENDERING,
                kind="canvas_latex",
                user_id="user-1",
                payload={"expected_revision": 7},
                idempotency_key=key,
                max_attempts=1,
                retry_terminal=True,
                commit=True,
            )
            duplicate_active = enqueue_worker_job(
                db,
                queue=QUEUE_RENDERING,
                kind="canvas_latex",
                user_id="user-1",
                payload={"expected_revision": 7},
                idempotency_key=key,
                max_attempts=1,
                retry_terminal=True,
                commit=True,
            )
            assert duplicate_active.id == original.id

            original.status = terminal_status
            original.payload = None
            db.commit()
            retried = enqueue_worker_job(
                db,
                queue=QUEUE_RENDERING,
                kind="canvas_latex",
                user_id="user-1",
                payload={"expected_revision": 7},
                idempotency_key=key,
                max_attempts=1,
                retry_terminal=True,
                commit=True,
            )

            assert retried.id != original.id
            assert retried.idempotency_key == key
            assert original.idempotency_key.startswith(f"archived:{original.id}:")
            assert original.idempotency_key.endswith(key)
            assert original.reconciled_at is not None

            duplicate_retry = enqueue_worker_job(
                db,
                queue=QUEUE_RENDERING,
                kind="canvas_latex",
                user_id="user-1",
                payload={"expected_revision": 7},
                idempotency_key=key,
                max_attempts=1,
                retry_terminal=True,
                commit=True,
            )
            assert duplicate_retry.id == retried.id

            retried.status = JOB_SUCCEEDED
            retried.payload = None
            db.commit()
            duplicate_success = enqueue_worker_job(
                db,
                queue=QUEUE_RENDERING,
                kind="canvas_latex",
                user_id="user-1",
                payload={"expected_revision": 7},
                idempotency_key=key,
                max_attempts=1,
                retry_terminal=True,
                commit=True,
            )
            assert duplicate_success.id == retried.id
    finally:
        db.close()


def test_configured_worker_batch_executes_claimed_jobs_concurrently(monkeypatch):
    worker = DurableQueueWorker(queue=QUEUE_FILES, handlers={})
    barrier = threading.Barrier(2)
    completed: list[str] = []

    def fake_process(job):
        barrier.wait(timeout=2)
        completed.append(job.id)

    monkeypatch.setattr(worker, "_process", fake_process)
    snapshots = [
        WorkerJobSnapshot(
            id=f"job-{index}",
            queue=QUEUE_FILES,
            kind="extract_text",
            user_id="user-1",
            payload={},
            attempt_count=1,
            max_attempts=2,
            expires_at=None,
        )
        for index in (1, 2)
    ]

    worker._process_batch(snapshots)

    assert set(completed) == {"job-1", "job-2"}


def test_retry_keeps_payload_but_success_erases_it_and_encrypts_result():
    db = _database_session()
    try:
        job = enqueue_worker_job(
            db,
            queue=QUEUE_FILES,
            kind="extract_text",
            user_id="user-1",
            payload={"artifact_id": "artifact-1"},
            idempotency_key="file:artifact-1",
            max_attempts=2,
            commit=True,
        )
        first_claim = claim_worker_jobs(
            db,
            queue=QUEUE_FILES,
            worker_id="files:test",
            lease_seconds=60,
        )[0]
        status = mark_worker_job_failed(
            db,
            job_id=job.id,
            worker_id="files:test",
            error_code="temporary",
            retryable=True,
            retry_delay_seconds=1,
        )
        assert status == JOB_RETRY
        db.refresh(job)
        assert job.payload == {"artifact_id": "artifact-1"}

        second_claim = claim_worker_jobs(
            db,
            queue=QUEUE_FILES,
            worker_id="files:test-2",
            now=first_claim.available_at + timedelta(seconds=2),
        )[0]
        assert mark_worker_job_succeeded(
            db,
            job_id=job.id,
            worker_id="files:test-2",
            result={"text": "sensitive derived text"},
        )
        db.refresh(job)
        assert job.status == JOB_SUCCEEDED
        assert job.payload is None
        assert job.result == {"text": "sensitive derived text"}
        raw_result = db.execute(
            text("SELECT result FROM durable_worker_jobs WHERE id = :id"),
            {"id": job.id},
        ).scalar_one()
        assert "sensitive derived text" not in str(raw_result)
        assert second_claim.attempt_count == 2
    finally:
        db.close()


def test_cancellation_is_owner_scoped_and_redacts_queued_payload():
    db = _database_session()
    try:
        job = enqueue_worker_job(
            db,
            queue=QUEUE_GENERATION,
            kind="send_message",
            user_id="user-1",
            payload={"prompt": "private"},
            idempotency_key="generation:cancel-me",
            commit=True,
        )
        assert not request_worker_job_cancellation(
            db,
            job_id=job.id,
            user_id="user-2",
        )
        assert request_worker_job_cancellation(
            db,
            job_id=job.id,
            user_id="user-1",
        )
        db.refresh(job)
        assert job.status == JOB_CANCELLED
        assert job.cancel_requested is True
        assert job.payload is None
    finally:
        db.close()


def test_processing_cancellation_wins_over_success_publication():
    db = _database_session()
    try:
        job = enqueue_worker_job(
            db,
            queue=QUEUE_GENERATION,
            kind="send_message",
            user_id="user-1",
            payload={"prompt": "private"},
            idempotency_key="generation:cancel-before-success",
            commit=True,
        )
        claim_worker_jobs(
            db,
            queue=QUEUE_GENERATION,
            worker_id="generation:test",
            lease_seconds=60,
        )
        assert request_worker_job_cancellation(
            db,
            job_id=job.id,
            user_id="user-1",
        )

        assert not mark_worker_job_succeeded(
            db,
            job_id=job.id,
            worker_id="generation:test",
            result={"content": "must not be published"},
        )
        assert mark_worker_job_cancelled(
            db,
            job_id=job.id,
            worker_id="generation:test",
        )
        db.refresh(job)
        assert job.status == JOB_CANCELLED
        assert job.result is None
        assert job.payload is None
    finally:
        db.close()


def test_cancelled_processing_job_becomes_terminal_after_worker_lease_loss():
    db = _database_session()
    try:
        started = datetime.now(timezone.utc)
        job = enqueue_worker_job(
            db,
            queue=QUEUE_GENERATION,
            kind="send_message",
            user_id="user-1",
            payload={"prompt": "private"},
            idempotency_key="generation:cancel-running",
            max_attempts=3,
            available_at=started,
            commit=True,
        )
        assert claim_worker_jobs(
            db,
            queue=QUEUE_GENERATION,
            worker_id="generation:dead",
            lease_seconds=60,
            now=started,
        )
        assert request_worker_job_cancellation(
            db,
            job_id=job.id,
            user_id="user-1",
        )
        db.refresh(job)
        assert job.status == "processing"
        assert job.payload is None
        assert worker_job_cancel_requested(
            db,
            job_id=job.id,
            worker_id="generation:dead",
        )

        assert claim_worker_jobs(
            db,
            queue=QUEUE_GENERATION,
            worker_id="generation:replacement",
            now=started + timedelta(seconds=61),
        ) == []
        db.refresh(job)
        assert job.status == JOB_CANCELLED
        assert job.error_code == "cancelled"
        assert job.lease_owner is None
    finally:
        db.close()


def test_expired_final_lease_is_failed_without_replaying_the_job():
    db = _database_session()
    try:
        started = datetime.now(timezone.utc)
        job = enqueue_worker_job(
            db,
            queue=QUEUE_GENERATION,
            kind="send_message",
            user_id="user-1",
            payload={"generation_id": "gen-once"},
            idempotency_key="generation:gen-once",
            max_attempts=1,
            available_at=started,
            commit=True,
        )
        assert claim_worker_jobs(
            db,
            queue=QUEUE_GENERATION,
            worker_id="generation:dead",
            lease_seconds=60,
            now=started,
        )

        assert claim_worker_jobs(
            db,
            queue=QUEUE_GENERATION,
            worker_id="generation:replacement",
            now=started + timedelta(seconds=61),
        ) == []
        db.refresh(job)
        assert job.status == JOB_FAILED
        assert job.error_code == "lease_expired"
        assert job.payload is None
    finally:
        db.close()


def test_retention_keeps_failed_jobs_until_required_reconciliation():
    db = _database_session()
    try:
        old = datetime.now(timezone.utc) - timedelta(days=10)
        failed = DurableWorkerJob(
            id="failed-generation",
            queue=QUEUE_GENERATION,
            kind="send",
            status=JOB_FAILED,
            idempotency_key="generation:needs-reconciliation",
            error_code="lease_expired",
            updated_at=old,
            created_at=old,
        )
        succeeded = DurableWorkerJob(
            id="successful-generation",
            queue=QUEUE_GENERATION,
            kind="send",
            status=JOB_SUCCEEDED,
            idempotency_key="generation:already-complete",
            updated_at=old,
            created_at=old,
        )
        db.add_all((failed, succeeded))
        db.commit()

        assert purge_terminal_worker_jobs(db, retention_days=7) == 1
        assert db.query(DurableWorkerJob).one().id == failed.id

        failed.reconciled_at = datetime.now(timezone.utc)
        db.commit()
        assert purge_terminal_worker_jobs(db, retention_days=7) == 1
        assert db.query(DurableWorkerJob).count() == 0
    finally:
        db.close()


def test_generation_worker_forwards_only_the_pre_stream_new_chat_event(monkeypatch):
    published = []
    monkeypatch.setattr(
        generation_worker.stream_hub,
        "publish_line",
        lambda generation_id, line: published.append((generation_id, line)),
    )

    generation_worker._publish_worker_only_line(
        "generation-1",
        '{"t":"n_c","d":"chat-1"}\n',
    )
    generation_worker._publish_worker_only_line(
        "generation-1",
        '{"t":"c","d":"already published"}\n',
    )

    assert published == [("generation-1", '{"t":"n_c","d":"chat-1"}\n')]


def test_operations_reconciliation_closes_domain_job_after_terminal_worker_failure(
    monkeypatch,
):
    from app.backups.models import BackupDestination, BackupJob

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BackupDestination.__table__,
            BackupJob.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        domain_job = BackupJob(id="backup-1", status="running", trigger_type="manual")
        worker_job = DurableWorkerJob(
            id="worker-1",
            queue="operations",
            kind="backup",
            status=JOB_FAILED,
            idempotency_key="backup:backup-1",
            error_code="lease_expired",
            payload=None,
            result=None,
        )
        db.add_all((domain_job, worker_job))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(operations_worker, "SessionLocal", factory)
    assert operations_worker.reconcile_interrupted_operation_jobs() == 1

    db = factory()
    try:
        assert db.query(BackupJob).one().status == "failed"
        reconciled_worker_job = db.query(DurableWorkerJob).one()
        assert reconciled_worker_job.result is None
        assert reconciled_worker_job.reconciled_at is not None
    finally:
        db.close()


def test_generation_reconciliation_closes_failed_shared_stream(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[DurableWorkerJob.__table__])
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        db.add(
            DurableWorkerJob(
                id="generation-worker-1",
                queue=QUEUE_GENERATION,
                kind="send",
                status=JOB_FAILED,
                idempotency_key="generation:generation-1",
                error_code="lease_expired",
            )
        )
        db.commit()
    finally:
        db.close()

    failures = []
    monkeypatch.setattr(generation_worker, "SessionLocal", factory)
    monkeypatch.setattr(generation_worker, "redis_enabled", lambda: True)
    monkeypatch.setattr(generation_worker, "get_redis_client", lambda: object())
    monkeypatch.setattr(
        generation_worker,
        "_safe_failure",
        lambda generation_id, *, code: failures.append((generation_id, code)),
    )
    assert generation_worker.reconcile_terminal_generation_jobs() == 1
    assert failures == [("generation-1", "lease_expired")]

    db = factory()
    try:
        assert db.query(DurableWorkerJob).one().reconciled_at is not None
    finally:
        db.close()


def test_file_reconciliation_closes_interrupted_artifact(monkeypatch):
    from app.files.models import FileProcessingArtifact

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[FileProcessingArtifact.__table__, DurableWorkerJob.__table__],
    )
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        db.add_all(
            (
                FileProcessingArtifact(
                    id="artifact-1",
                    file_id="file-1",
                    operation="extract_text",
                    cache_key="cache-1",
                    status="running",
                ),
                DurableWorkerJob(
                    id="file-worker-1",
                    queue=QUEUE_FILES,
                    kind="extract_text",
                    status=JOB_FAILED,
                    idempotency_key="file-artifact:artifact-1",
                    error_code="lease_expired",
                ),
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(file_worker, "SessionLocal", factory)
    assert file_worker.reconcile_terminal_file_jobs() == 1

    db = factory()
    try:
        artifact = db.query(FileProcessingArtifact).one()
        assert artifact.status == "failed"
        assert artifact.error_code == "lease_expired"
    finally:
        db.close()


def test_requester_authorization_failure_releases_global_file_artifact(monkeypatch):
    from app.files.models import FileProcessingArtifact

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[FileProcessingArtifact.__table__, DurableWorkerJob.__table__],
    )
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        db.add_all(
            (
                FileProcessingArtifact(
                    id="artifact-reader",
                    file_id="file-1",
                    operation="extract_text",
                    cache_key="shared-cache",
                    status="pending",
                ),
                DurableWorkerJob(
                    id="file-reader-job",
                    queue=QUEUE_FILES,
                    kind="extract_text",
                    status=JOB_FAILED,
                    idempotency_key="file-artifact:artifact-reader",
                    error_code="file_unavailable",
                ),
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(file_worker, "SessionLocal", factory)
    assert file_worker.reconcile_terminal_file_jobs() == 1

    db = factory()
    try:
        assert db.query(FileProcessingArtifact).count() == 0
        replacement = file_worker._get_or_create_artifact(
            db,
            file_record=SimpleNamespace(id="file-1"),
            operation="extract_text",
            cache_key="shared-cache",
        )
        db.commit()
        assert replacement.id != "artifact-reader"
        assert replacement.status == "pending"
    finally:
        db.close()


def test_file_processing_local_abort_releases_shared_artifact(monkeypatch):
    from app.files.models import FileProcessingArtifact

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[FileProcessingArtifact.__table__],
    )
    factory = sessionmaker(bind=engine)

    for artifact_id, abort in (
        ("artifact-cancelled", file_worker.JobCancelled()),
        (
            "artifact-authorization",
            file_worker.FatalJobError("authorization_changed"),
        ),
    ):
        db = factory()
        db.add(
            FileProcessingArtifact(
                id=artifact_id,
                file_id="file-1",
                operation="extract_text",
                cache_key=artifact_id,
                status="running",
            )
        )
        db.commit()
        artifact = db.query(FileProcessingArtifact).filter_by(id=artifact_id).one()
        monkeypatch.setattr(
            file_worker,
            "_artifact_and_file",
            lambda _job, _db=db, _artifact=artifact: (
                _db,
                _artifact,
                SimpleNamespace(id="file-1", user_id="owner-1"),
            ),
        )
        job = WorkerJobSnapshot(
            id=f"job-{artifact_id}",
            queue=QUEUE_FILES,
            kind="extract_text",
            user_id="requester-1",
            payload={"artifact_id": artifact_id, "file_id": "file-1"},
            attempt_count=1,
            max_attempts=2,
            expires_at=None,
        )

        try:
            file_worker._handle(
                job,
                SimpleNamespace(
                    raise_if_cancelled=lambda _abort=abort: (_ for _ in ()).throw(
                        _abort
                    )
                ),
            )
        except type(abort) as exc:
            assert getattr(exc, "code", None) == getattr(abort, "code", None)
        else:
            raise AssertionError("request-local file processing abort was swallowed")

        check = factory()
        try:
            assert (
                check.query(FileProcessingArtifact)
                .filter_by(id=artifact_id)
                .count()
                == 0
            )
        finally:
            check.close()


def test_research_reconciliation_unblocks_waiter_after_final_lease_loss(monkeypatch):
    from app.chats.models import Chats
    from app.tools.deep_research.models import DeepResearchRun

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Chats.__table__, DeepResearchRun.__table__, DurableWorkerJob.__table__],
    )
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        db.add_all(
            (
                DeepResearchRun(
                    id="research-1",
                    user_id="user-1",
                    query="Research this",
                    status="running",
                ),
                DurableWorkerJob(
                    id="research-worker-1",
                    queue="research",
                    kind="deep_research",
                    status=JOB_FAILED,
                    idempotency_key="deep-research:research-1",
                    error_code="lease_expired",
                ),
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(research_worker, "SessionLocal", factory)
    monkeypatch.setattr(research_worker, "redis_enabled", lambda: True)
    monkeypatch.setattr(research_worker, "get_redis_client", lambda: object())
    assert research_worker.reconcile_terminal_research_jobs() == 1

    db = factory()
    try:
        run = db.query(DeepResearchRun).one()
        assert run.status == "failed"
        assert run.phase == "failed"
        assert run.completed_at is not None
    finally:
        db.close()


def test_research_worker_owns_deep_research_and_long_running_subagents():
    assert set(research_worker.build_worker().handlers) == {
        "deep_research",
        "subagent",
    }


def test_research_worker_preserves_queue_cancellation_as_cancelled_run(monkeypatch):
    import app.tools.deep_research.utils as research_utils
    from app.tools.deep_research.providers import DeepResearchCancelled
    from app.workers.runtime import JobCancelled

    user = SimpleNamespace(
        id="user-1",
        group_id="group-1",
        role="user",
        is_active=True,
        deleted_at=None,
    )
    run = SimpleNamespace(
        id="run-1",
        user_id="user-1",
        status="running",
        generation_id="generation-1",
        config_snapshot={},
        result_meta={},
    )

    class FakeSession:
        def query(self, _model):
            return self

        def filter(self, *_args):
            return self

        def first(self):
            return user

        def refresh(self, _row):
            pass

        def close(self):
            pass

    def fake_execute(_db, _run, **kwargs):
        try:
            kwargs["callback"]({"event": "phase_started"})
        except DeepResearchCancelled:
            run.status = "cancelled"

    monkeypatch.setattr(research_worker, "SessionLocal", FakeSession)
    monkeypatch.setattr(research_worker, "get_deep_research_run", lambda *_args: run)
    monkeypatch.setattr(research_worker, "redis_enabled", lambda: True)
    monkeypatch.setattr(research_worker, "get_redis_client", lambda: object())
    monkeypatch.setattr(
        research_worker,
        "_validate_current_research_policy",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(research_utils, "execute_research_run", fake_execute)
    context = SimpleNamespace(cancelled=lambda: True)
    job = WorkerJobSnapshot(
        id="job-1",
        queue="research",
        kind="deep_research",
        user_id="user-1",
        payload={"run_id": "run-1"},
        attempt_count=1,
        max_attempts=3,
        expires_at=None,
    )

    try:
        research_worker._execute(job, context)
    except JobCancelled:
        pass
    else:
        raise AssertionError("cancelled research job was not propagated")


def test_research_worker_rechecks_current_tool_policy_before_execution(monkeypatch):
    import app.tools.deep_research.utils as research_utils
    import app.llm.utils as llm_utils
    import app.tools.utils as tool_utils

    user = SimpleNamespace(
        id="user-1",
        group_id="group-1",
        role="user",
        is_active=True,
        deleted_at=None,
    )
    run = SimpleNamespace(
        id="run-1",
        user_id="user-1",
        status="running",
        execution_mode="native",
        model_id=None,
        config_snapshot={
            "execution_authorization": {
                "schema_version": 1,
                "origin_kind": "model",
                "origin_model_id": "origin-model-1",
                "runtime_enabled_tools": ["deep_research"],
            }
        },
        result_meta={},
    )
    origin_model = SimpleNamespace(
        id="origin-model-1",
        is_active=True,
        capabilities=["tools"],
        tools=["deep_research"],
        settings={},
    )

    class FakeQuery:
        def __init__(self, model):
            self.model = model

        def filter(self, *_args):
            return self

        def first(self):
            if self.model is research_worker.User:
                return user
            if self.model is research_worker.Models:
                return origin_model
            raise AssertionError(f"unexpected policy query: {self.model!r}")

    class FakeSession:
        def query(self, model):
            return FakeQuery(model)

        def close(self):
            pass

    monkeypatch.setattr(research_worker, "SessionLocal", FakeSession)
    monkeypatch.setattr(research_worker, "get_deep_research_run", lambda *_args: run)
    monkeypatch.setattr(research_worker, "redis_enabled", lambda: True)
    monkeypatch.setattr(research_worker, "get_redis_client", lambda: object())
    monkeypatch.setattr(
        tool_utils,
        "list_enabled_custom_python_tool_names",
        lambda _db: [],
    )
    monkeypatch.setattr(llm_utils, "ensure_user_access_to_model", lambda *_args: True)
    monkeypatch.setattr(
        research_utils,
        "execute_research_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("research provider ran after tool authorization was revoked")
        ),
    )
    job = WorkerJobSnapshot(
        id="job-1",
        queue="research",
        kind="deep_research",
        user_id="user-1",
        payload={"run_id": "run-1"},
        attempt_count=1,
        max_attempts=3,
        expires_at=None,
    )

    # The persisted origin context is initially authorized. Revoking the tool
    # on the actual model row after enqueue must be observed by the worker.
    assert research_worker._validate_current_research_policy(
        FakeSession(),
        user=user,
        run=run,
    ) is None
    origin_model.tools = []

    try:
        research_worker._execute(
            job,
            SimpleNamespace(cancelled=lambda: False),
        )
    except research_worker.FatalJobError as exc:
        assert exc.code == "tool_no_longer_enabled"
    else:
        raise AssertionError("revoked Deep Research authorization was accepted")


def test_research_worker_rechecks_custom_model_access(monkeypatch):
    from fastapi import HTTPException
    import app.llm.utils as llm_utils
    import app.tools.utils as tool_utils

    monkeypatch.setattr(
        tool_utils,
        "resolve_enabled_tools",
        lambda *_args, **_kwargs: {"tool_list": ["deep_research"]},
    )
    def ensure_model_access(_user_id, model_id, _db):
        if model_id == "model-1":
            raise HTTPException(status_code=404, detail="Model not found")
        return True

    monkeypatch.setattr(llm_utils, "ensure_user_access_to_model", ensure_model_access)
    origin_model = SimpleNamespace(
        id="origin-model-1",
        is_active=True,
        capabilities=["tools"],
        tools=["deep_research"],
        settings={},
    )

    class FakeQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return origin_model

    class FakeSession:
        def query(self, _model):
            return FakeQuery()

    run = SimpleNamespace(
        execution_mode="custom",
        model_id="model-1",
        config_snapshot={
            "execution_authorization": {
                "schema_version": 1,
                "origin_kind": "model",
                "origin_model_id": "origin-model-1",
                "runtime_enabled_tools": ["deep_research"],
            }
        },
    )

    try:
        research_worker._validate_current_research_policy(
            FakeSession(),
            user=SimpleNamespace(id="user-1"),
            run=run,
        )
    except research_worker.FatalJobError as exc:
        assert exc.code == "authorization_changed"
    else:
        raise AssertionError("revoked Deep Research model access was accepted")


def test_research_worker_rechecks_project_access(monkeypatch):
    from fastapi import HTTPException
    import app.projects.models as project_models

    monkeypatch.setattr(
        project_models,
        "get_project_with_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=404, detail="Project not found")
        ),
    )
    run = SimpleNamespace(
        execution_mode="native",
        model_id=None,
        config_snapshot={"project_id": "project-1"},
    )

    try:
        research_worker._validate_current_research_policy(
            object(),
            user=SimpleNamespace(id="user-1"),
            run=run,
        )
    except research_worker.FatalJobError as exc:
        assert exc.code == "authorization_changed"
    else:
        raise AssertionError("revoked Deep Research project access was accepted")


def test_research_execution_audits_a_domain_cancellation_as_cancelled(monkeypatch):
    import app.tools.deep_research.utils as research_utils

    run = SimpleNamespace(
        id="run-1",
        user_id="user-1",
        execution_mode="custom",
        status="running",
    )
    audit_actions: list[str] = []

    class FakeSession:
        def refresh(self, _row):
            pass

    def fake_custom(_db, current_run, **_kwargs):
        current_run.status = "cancelled"

    monkeypatch.setattr(research_utils, "run_custom_research", fake_custom)
    monkeypatch.setattr(
        research_utils,
        "_audit_run",
        lambda _user_id, action, _run: audit_actions.append(action),
    )

    research_utils.execute_research_run(FakeSession(), run)

    assert audit_actions == [
        "DEEP_RESEARCH_STARTED",
        "DEEP_RESEARCH_CANCELLED",
    ]


def test_idempotent_lifecycle_job_can_revive_only_after_final_lease_expiry():
    db = _database_session()
    try:
        started = datetime.now(timezone.utc)
        job = enqueue_worker_job(
            db,
            queue=QUEUE_LIFECYCLE,
            kind="hard_delete_user",
            user_id="user-1",
            payload={"user_id": "user-1", "scheduled_for": started.isoformat()},
            idempotency_key=f"hard-delete:user-1:{started.isoformat()}",
            max_attempts=1,
            available_at=started,
            commit=True,
        )
        assert claim_worker_jobs(
            db,
            queue=QUEUE_LIFECYCLE,
            worker_id="lifecycle:dead",
            lease_seconds=60,
            now=started,
        )
        assert claim_worker_jobs(
            db,
            queue=QUEUE_LIFECYCLE,
            worker_id="lifecycle:replacement",
            now=started + timedelta(seconds=61),
        ) == []

        payload = {"user_id": "user-1", "scheduled_for": started.isoformat()}
        assert revive_worker_job_after_lease_expiry(
            db,
            job_id=job.id,
            payload=payload,
            available_at=started,
        )
        db.commit()
        db.refresh(job)
        assert job.status == JOB_PENDING
        assert job.attempt_count == 0
        assert job.payload == payload
    finally:
        db.close()


def test_restore_invalidates_replayed_jobs_and_terminalizes_domain_state():
    from app.admin.export_jobs.models import AdminUserExportJob
    from app.backups.models import BackupJob, RestoreJob
    from app.files.models import FileProcessingArtifact
    from app.llm.models import RateLimitDurationAdmission
    from app.tools.deep_research.models import DeepResearchRun
    from app.workers.restore import reconcile_worker_state_after_restore

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            DurableWorkerJob.__table__,
            BackupJob.__table__,
            RestoreJob.__table__,
            AdminUserExportJob.__table__,
            DeepResearchRun.__table__,
            FileProcessingArtifact.__table__,
            AuditEventOutbox.__table__,
            RateLimitDurationAdmission.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        db.add_all(
            (
                DurableWorkerJob(
                    id="generation-job",
                    queue=QUEUE_GENERATION,
                    kind="send",
                    status=JOB_PENDING,
                    idempotency_key="generation:restored",
                    payload={"prompt": "must not replay"},
                ),
                DurableWorkerJob(
                    id="lifecycle-job",
                    queue=QUEUE_LIFECYCLE,
                    kind="hard_delete_user",
                    status=JOB_PENDING,
                    idempotency_key="hard-delete:user-1:restored",
                    payload={"user_id": "user-1"},
                ),
                DurableWorkerJob(
                    id="audit-erasure-job",
                    queue=QUEUE_EVENTS,
                    kind="audit_erasure",
                    user_id="user-1",
                    status=JOB_PENDING,
                    idempotency_key="audit-erasure:fingerprint:restored",
                    payload={"user_id": "user-1"},
                ),
                BackupJob(id="backup-1", status="running", trigger_type="manual"),
                RestoreJob(id="restore-1", source_uri="local://backup", status="queued"),
                AdminUserExportJob(id="export-1", status="running"),
                DeepResearchRun(
                    id="research-1",
                    user_id="user-1",
                    query="old work",
                    status="running",
                ),
                FileProcessingArtifact(
                    id="artifact-1",
                    file_id="file-1",
                    operation="extract_text",
                    cache_key="restored",
                    status="running",
                ),
            )
        )
        db.commit()

        result = reconcile_worker_state_after_restore(db)

        assert result == {
            "active_jobs": 1,
            "lifecycle_jobs": 1,
            "backup_jobs": 1,
            "restore_jobs": 1,
            "admin_export_jobs": 1,
            "research_runs": 1,
            "file_artifacts": 1,
            "audit_events": 0,
            "duration_admissions": 0,
            "reconciled_jobs": 0,
        }
        queue_job = db.query(DurableWorkerJob).filter_by(id="generation-job").one()
        assert queue_job.status == JOB_CANCELLED
        assert queue_job.payload is None
        assert queue_job.error_code == "restore_invalidated"
        assert queue_job.reconciled_at is not None
        audit_erasure_job = (
            db.query(DurableWorkerJob).filter_by(id="audit-erasure-job").one()
        )
        assert audit_erasure_job.status == JOB_PENDING
        assert audit_erasure_job.payload == {"user_id": "user-1"}
        assert db.query(BackupJob).one().status == "failed"
        assert db.query(RestoreJob).one().status == "failed"
        assert db.query(AdminUserExportJob).one().status == "failed"
        assert db.query(DeepResearchRun).one().status == "failed"
        assert db.query(FileProcessingArtifact).count() == 0
    finally:
        db.close()


def test_restore_removes_operations_staging_files(monkeypatch, tmp_path):
    from app.workers import operations

    imports = tmp_path / "imports"
    results = tmp_path / "results"
    imports.mkdir()
    results.mkdir()
    staged_name = f"{'f' * 32}.json"
    (imports / staged_name).write_text("private", encoding="utf-8")
    nested = results / "unexpected"
    nested.mkdir()
    (nested / "result.json").write_text("result", encoding="utf-8")
    monkeypatch.setattr(operations, "OPERATIONS_IMPORT_DIR", imports)
    monkeypatch.setattr(operations, "OPERATIONS_RESULT_DIR", results)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[DurableWorkerJob.__table__, ImportStagingReservation.__table__],
    )
    db = sessionmaker(bind=engine)()
    try:
        db.add(
            ImportStagingReservation(
                id="restored-import-reservation",
                staged_name=staged_name,
                principal_id="user-1",
                import_kind="import_user_self",
                size_bytes=7,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        db.commit()

        assert operations.clear_operations_staging_after_restore(db=db) == 2
        assert db.query(ImportStagingReservation).count() == 0
        assert list(imports.iterdir()) == []
        assert list(results.iterdir()) == []
    finally:
        db.close()


def test_self_import_rechecks_data_control_before_reading_staged_payload(
    monkeypatch,
    tmp_path,
):
    staged_name = f"{'a' * 32}.json"
    staged_path = tmp_path / staged_name
    staged_path.write_text('{"users": []}', encoding="utf-8")

    class FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(operations_worker, "OPERATIONS_IMPORT_DIR", tmp_path)
    monkeypatch.setattr(operations_worker, "SessionLocal", FakeSession)
    monkeypatch.setattr(operations_worker, "AuditSessionLocal", FakeSession)
    monkeypatch.setattr(
        operations_worker,
        "discard_import_staging",
        lambda discarded_name: (tmp_path / discarded_name).unlink(missing_ok=True),
    )
    monkeypatch.setattr(operations_worker, "_require_active_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        operations_worker,
        "_require_user_data_control",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            operations_worker.FatalJobError("authorization_changed")
        ),
    )
    monkeypatch.setattr(
        operations_worker,
        "_load_staged_json",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("staged import was read after authorization was revoked")
        ),
    )
    job = WorkerJobSnapshot(
        id="self-import-job",
        queue="operations",
        kind="import_user_self",
        user_id="user-1",
        payload={"staged_name": staged_name},
        attempt_count=1,
        max_attempts=1,
        expires_at=None,
    )

    try:
        operations_worker._import(
            job,
            SimpleNamespace(raise_if_cancelled=lambda: None),
        )
    except operations_worker.FatalJobError as exc:
        assert exc.code == "authorization_changed"
    else:
        raise AssertionError("revoked self-import authorization was accepted")

    assert not staged_path.exists()


def test_self_export_rechecks_data_control_before_building_archive(
    monkeypatch,
    tmp_path,
):
    import app.users.utils as user_utils

    class FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(operations_worker, "OPERATIONS_RESULT_DIR", tmp_path)
    monkeypatch.setattr(operations_worker, "SessionLocal", FakeSession)
    monkeypatch.setattr(operations_worker, "AuditSessionLocal", FakeSession)
    monkeypatch.setattr(operations_worker, "_require_active_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        operations_worker,
        "_require_user_data_control",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            operations_worker.FatalJobError("authorization_changed")
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "build_user_data_export_json_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("user export was built after authorization was revoked")
        ),
    )
    job = WorkerJobSnapshot(
        id="self-export-job",
        queue="operations",
        kind="user_data_export",
        user_id="user-1",
        payload={"result_name": f"{'b' * 32}.json"},
        attempt_count=1,
        max_attempts=1,
        expires_at=None,
    )

    try:
        operations_worker._user_data_export(
            job,
            SimpleNamespace(raise_if_cancelled=lambda: None),
        )
    except operations_worker.FatalJobError as exc:
        assert exc.code == "authorization_changed"
    else:
        raise AssertionError("revoked self-export authorization was accepted")

    assert list(tmp_path.iterdir()) == []
