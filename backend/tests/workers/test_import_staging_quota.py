from __future__ import annotations

from datetime import timedelta
import io
import os
import threading

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.workers import operations
from app.workers.models import (
    DurableWorkerJob,
    ImportStagingReservation,
    JOB_PENDING,
    JOB_PROCESSING,
    mark_worker_job_succeeded,
    purge_terminal_worker_jobs,
    request_worker_job_cancellation,
    utcnow,
)


_QUOTA_ENV_KEYS = (
    "OPERATIONS_IMPORT_MAX_BYTES",
    "OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_BYTES",
    "OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_BYTES",
    "OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_SLOTS",
    "OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_SLOTS",
)


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture
def staging_store(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(
        bind=engine,
        tables=[DurableWorkerJob.__table__, ImportStagingReservation.__table__],
    )
    factory = sessionmaker(bind=engine)
    imports = tmp_path / "operations-imports"
    monkeypatch.setattr(operations, "SessionLocal", factory)
    monkeypatch.setattr(operations, "OPERATIONS_IMPORT_DIR", imports)
    for key in _QUOTA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return factory, imports


def test_stream_reservation_is_linked_atomically_and_released_at_terminal_state(
    staging_store,
):
    factory, imports = staging_store
    staged_name = operations.stage_import_stream(
        io.BytesIO(b"private import"),
        extension="zip",
        principal_id="user-1",
        import_kind="import_chatgpt",
    )

    db = factory()
    try:
        reservation = db.query(ImportStagingReservation).one()
        assert reservation.staged_name == staged_name
        assert reservation.principal_id == "user-1"
        assert reservation.import_kind == "import_chatgpt"
        assert reservation.size_bytes == len(b"private import")
        assert reservation.worker_job_id is None

        job = operations.enqueue_import_job(
            db,
            kind="import_chatgpt",
            staged_name=staged_name,
            user_id="user-1",
        )
        reservation = db.query(ImportStagingReservation).one()
        assert reservation.worker_job_id == job.id

        job.status = JOB_PROCESSING
        job.lease_owner = "worker-1"
        db.commit()
        assert operations.discard_import_staging(staged_name)
        assert mark_worker_job_succeeded(
            db,
            job_id=job.id,
            worker_id="worker-1",
            result={"imported": 1},
        )
        assert db.query(ImportStagingReservation).count() == 0
        assert not (imports / staged_name).exists()
    finally:
        db.close()


def test_principal_bytes_and_slots_include_in_progress_reservations(
    staging_store,
    monkeypatch,
):
    factory, imports = staging_store
    monkeypatch.setenv("OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_BYTES", str(1024 * 1024))
    first = operations.stage_import_stream(
        io.BytesIO(b"a" * 700_000),
        extension="zip",
        principal_id="user-1",
        import_kind="import_chatgpt",
    )

    with pytest.raises(HTTPException) as bytes_error:
        operations.stage_import_stream(
            io.BytesIO(b"b" * 400_000),
            extension="zip",
            principal_id="user-1",
            import_kind="import_chatgpt",
        )
    assert bytes_error.value.status_code == 413
    assert bytes_error.value.detail == {"code": "import_staging_quota_exceeded"}

    monkeypatch.setenv("OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_SLOTS", "1")
    with pytest.raises(HTTPException) as slots_error:
        operations.stage_import_stream(
            io.BytesIO(b""),
            extension="zip",
            principal_id="user-1",
            import_kind="import_chatgpt",
        )
    assert slots_error.value.status_code == 413
    assert slots_error.value.detail == {"code": "import_staging_quota_exceeded"}

    db = factory()
    try:
        assert db.query(ImportStagingReservation).count() == 1
        assert db.query(ImportStagingReservation).one().staged_name == first
    finally:
        db.close()
    assert sorted(path.name for path in imports.iterdir()) == [first]


def test_global_byte_admission_is_serialized_across_concurrent_stagers(
    monkeypatch,
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'quota.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(
        bind=engine,
        tables=[DurableWorkerJob.__table__, ImportStagingReservation.__table__],
    )
    factory = sessionmaker(bind=engine)
    imports = tmp_path / "imports"
    monkeypatch.setattr(operations, "SessionLocal", factory)
    monkeypatch.setattr(operations, "OPERATIONS_IMPORT_DIR", imports)
    monkeypatch.setenv("OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_BYTES", str(1024 * 1024))
    monkeypatch.setenv("OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_BYTES", str(1024 * 1024))
    monkeypatch.setenv("OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_SLOTS", "10")

    barrier = threading.Barrier(2)
    results: list[str] = []
    results_lock = threading.Lock()

    def stage(principal_id: str) -> None:
        barrier.wait()
        try:
            operations.stage_import_stream(
                io.BytesIO(b"x" * 700_000),
                extension="zip",
                principal_id=principal_id,
                import_kind="import_chatgpt",
            )
        except HTTPException as exc:
            outcome = str(exc.detail["code"])
        else:
            outcome = "accepted"
        with results_lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=stage, args=("user-1",)),
        threading.Thread(target=stage, args=("user-2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results) == ["accepted", "import_staging_capacity_exceeded"]
    db = factory()
    try:
        assert db.query(ImportStagingReservation).count() == 1
        assert db.query(ImportStagingReservation.size_bytes).scalar() == 700_000
    finally:
        db.close()


def test_per_file_limit_keeps_streaming_and_releases_failed_reservation(
    staging_store,
    monkeypatch,
):
    factory, imports = staging_store
    monkeypatch.setenv("OPERATIONS_IMPORT_MAX_BYTES", str(1024 * 1024))
    with pytest.raises(HTTPException) as exc_info:
        operations.stage_import_stream(
            io.BytesIO(b"x" * ((1024 * 1024) + 1)),
            extension="zip",
            principal_id="user-1",
            import_kind="import_chatgpt",
        )
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == {"code": "import_file_too_large"}
    db = factory()
    try:
        assert db.query(ImportStagingReservation).count() == 0
    finally:
        db.close()
    assert list(imports.iterdir()) == []


def test_enqueue_failure_and_queued_cancellation_release_reservations(
    staging_store,
    monkeypatch,
):
    factory, imports = staging_store
    failed_name = operations.stage_import_json(
        {"secret": "failed"},
        principal_id="admin-1",
        import_kind="import_openwebui_bulk",
    )
    real_enqueue = operations.enqueue_worker_job
    monkeypatch.setattr(
        operations,
        "enqueue_worker_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )
    db = factory()
    try:
        with pytest.raises(RuntimeError, match="queue unavailable"):
            operations.enqueue_import_job(
                db,
                kind="import_openwebui_bulk",
                staged_name=failed_name,
                user_id="admin-1",
            )
    finally:
        db.close()
    assert not (imports / failed_name).exists()

    monkeypatch.setattr(operations, "enqueue_worker_job", real_enqueue)
    cancelled_name = operations.stage_import_json(
        {"secret": "cancelled"},
        principal_id="user-1",
        import_kind="import_user_self",
    )
    db = factory()
    try:
        job = operations.enqueue_import_job(
            db,
            kind="import_user_self",
            staged_name=cancelled_name,
            user_id="user-1",
        )
        assert job.status == JOB_PENDING
        assert request_worker_job_cancellation(db, job_id=job.id)
        # Pending cancellation cannot run the import handler. Keep charging
        # the bytes until maintenance removes the file first.
        assert db.query(ImportStagingReservation).count() == 1
        assert (imports / cancelled_name).is_file()
    finally:
        db.close()
    assert operations.cleanup_import_staging_reservations() == 1
    assert not (imports / cancelled_name).exists()
    db = factory()
    try:
        assert db.query(ImportStagingReservation).count() == 0
    finally:
        db.close()


def test_uncertain_enqueue_commit_never_discards_possibly_queued_bytes(
    staging_store,
):
    factory, imports = staging_store
    staged_name = operations.stage_import_json(
        {"secret": "commit outcome"},
        principal_id="user-1",
        import_kind="import_user_self",
    )

    class UncertainCommitSession:
        def __init__(self):
            self.inner = factory()

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def commit(self):
            self.inner.commit()
            raise ConnectionError("commit acknowledgement lost")

    db = UncertainCommitSession()
    try:
        with pytest.raises(ConnectionError, match="acknowledgement lost"):
            operations.enqueue_import_job(
                db,
                kind="import_user_self",
                staged_name=staged_name,
                user_id="user-1",
            )
    finally:
        db.close()

    verification_db = factory()
    try:
        reservation = verification_db.query(ImportStagingReservation).one()
        assert reservation.worker_job_id is not None
        assert verification_db.query(DurableWorkerJob).count() == 1
    finally:
        verification_db.close()
    assert (imports / staged_name).is_file()

    retry_db = factory()
    try:
        with pytest.raises(RuntimeError, match="does not match"):
            operations.enqueue_import_job(
                retry_db,
                kind="import_user_self",
                staged_name=staged_name,
                user_id="user-1",
            )
        assert retry_db.query(ImportStagingReservation).count() == 1
    finally:
        retry_db.close()
    assert (imports / staged_name).is_file()


def test_job_purge_keeps_reservation_until_staged_bytes_are_removed(
    staging_store,
):
    factory, imports = staging_store
    staged_name = operations.stage_import_json(
        {"secret": "cancelled then purged"},
        principal_id="user-1",
        import_kind="import_user_self",
    )
    db = factory()
    try:
        job = operations.enqueue_import_job(
            db,
            kind="import_user_self",
            staged_name=staged_name,
            user_id="user-1",
        )
        assert request_worker_job_cancellation(db, job_id=job.id)
        job.updated_at = utcnow() - timedelta(days=2)
        db.commit()

        assert purge_terminal_worker_jobs(
            db,
            retention_days=1,
            now=utcnow(),
        ) == 1
        reservation = db.query(ImportStagingReservation).one()
        assert reservation.worker_job_id is None
        assert (imports / staged_name).is_file()

        reservation.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    assert operations.cleanup_import_staging_reservations() == 1
    assert not (imports / staged_name).exists()
    db = factory()
    try:
        assert db.query(ImportStagingReservation).count() == 0
    finally:
        db.close()


def test_maintenance_removes_expired_part_reservation_and_filesystem_orphan(
    staging_store,
):
    factory, imports = staging_store
    imports.mkdir(parents=True)
    reserved_name = f"{'a' * 32}.zip"
    part = imports / f".{reserved_name}.123.part"
    part.write_bytes(b"partial")
    orphan = imports / f"{'b' * 32}.json"
    orphan.write_bytes(b"orphan")
    old = (utcnow() - timedelta(days=5)).timestamp()
    os.utime(part, (old, old))
    os.utime(orphan, (old, old))
    db = factory()
    try:
        db.add(
            ImportStagingReservation(
                id="reservation-1",
                staged_name=reserved_name,
                principal_id="user-1",
                import_kind="import_chatgpt",
                size_bytes=7,
                expires_at=utcnow() - timedelta(minutes=1),
                updated_at=utcnow() - timedelta(days=5),
            )
        )
        db.commit()
    finally:
        db.close()

    assert operations.cleanup_import_staging_reservations(
        cutoff=utcnow() - timedelta(days=4)
    ) == 2
    assert list(imports.iterdir()) == []
    db = factory()
    try:
        assert db.query(ImportStagingReservation).count() == 0
    finally:
        db.close()
