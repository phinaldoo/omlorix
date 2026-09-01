from __future__ import annotations

from pathlib import Path
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.files import utils as file_utils
from app.files.models import FileQuotaReservation, Files, create_file


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Files.__table__, FileQuotaReservation.__table__],
    )
    return sessionmaker(bind=engine)()


def _configure_quota(monkeypatch, *, max_files: int = -1, max_storage_bytes: int | None = None):
    def fake_group_setting(_user_id, _section, key, _db):
        if key == "allow_file_uploads":
            return True
        if key == "max_files_upload_count":
            return max_files
        if key == "max_user_files_size_gb":
            if max_storage_bytes is None:
                return None
            return str(max_storage_bytes / (1024 ** 3))
        return None

    monkeypatch.setattr(file_utils, "get_user_group_setting_value", fake_group_setting)


def test_generated_persistence_resolves_count_quota_when_callers_omit_limits(
    monkeypatch,
    tmp_path: Path,
):
    """Generated-file callers must not bypass quota by omitting optional limits."""

    db = _session()
    _configure_quota(monkeypatch, max_files=1)
    create_file(
        db,
        user_id="user-1",
        file_category="image",
        file_type="image/png",
        file_size=3,
        file_id="existing-file",
        file_name="existing.png",
        storage_provider="local",
        storage_key="user-1/existing.png",
    )
    storage_calls: list[str] = []

    monkeypatch.setattr(file_utils, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        file_utils,
        "upload_file_to_storage",
        lambda _path, user_id, file_name: (
            storage_calls.append(file_name) or "local",
            f"{user_id}/{file_name}",
            {},
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        file_utils.persist_generated_file_bytes(
            db,
            user_id="user-1",
            original_filename="generated.png",
            file_bytes=b"png",
            file_type="image/png",
            file_category="image",
        )

    assert exc_info.value.detail == "Maximum number of uploaded files reached"
    assert (
        exc_info.value.headers["X-Omlorix-Error-Code"]
        == file_utils.USER_FILE_COUNT_QUOTA_REACHED
    )
    assert storage_calls == []
    assert db.query(Files).count() == 1


def test_reservation_serializes_the_last_available_file_slot(monkeypatch):
    """Parallel provider work cannot reserve the same final file-count slot."""

    db = _session()
    _configure_quota(monkeypatch, max_files=1)

    first = file_utils.reserve_user_file_quota(
        db,
        user_id="user-1",
        purpose="image_generation",
    )
    assert first is not None

    with pytest.raises(file_utils.FileQuotaError) as exc_info:
        file_utils.reserve_user_file_quota(
            db,
            user_id="user-1",
            purpose="video_generation",
        )

    assert exc_info.value.code == file_utils.USER_FILE_COUNT_QUOTA_REACHED
    assert db.query(FileQuotaReservation).count() == 1

    file_utils.release_user_file_quota_reservation(db, first.reservation_id)
    replacement = file_utils.reserve_user_file_quota(
        db,
        user_id="user-1",
        purpose="video_generation",
    )
    assert replacement is not None


def test_reservation_release_ends_transaction_when_locked_delete_loses_race(monkeypatch):
    """A consumed reservation must not leave PostgreSQL's advisory lock held."""

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(user_id="user-1")

        def delete(self, **_kwargs):
            return 0

    class FakeSession:
        def __init__(self):
            self.rollbacks = 0
            self.commits = 0

        def query(self, *_args):
            return Query()

        def rollback(self):
            self.rollbacks += 1

        def commit(self):
            self.commits += 1

    db = FakeSession()
    monkeypatch.setattr(
        file_utils,
        "serialized_user_file_quota_admission",
        lambda *_args, **_kwargs: nullcontext(),
    )

    file_utils.release_user_file_quota_reservation(db, "reservation-1")

    assert db.commits == 0
    assert db.rollbacks == 1


def test_reservation_release_ends_lookup_transaction_when_already_consumed():
    """The common post-persistence no-op path must also return a clean session."""

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeSession:
        def __init__(self):
            self.rollbacks = 0

        def query(self, *_args):
            return Query()

        def rollback(self):
            self.rollbacks += 1

    db = FakeSession()

    file_utils.release_user_file_quota_reservation(db, "reservation-1")

    assert db.rollbacks == 1


def test_concurrent_reservations_admit_exactly_one_last_slot(monkeypatch, tmp_path: Path):
    """The local/SQLite lock mirrors PostgreSQL's per-user atomic admission."""

    engine = create_engine(
        f"sqlite:///{tmp_path / 'quota.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[Files.__table__, FileQuotaReservation.__table__],
    )
    session_factory = sessionmaker(bind=engine)
    _configure_quota(monkeypatch, max_files=1)
    barrier = threading.Barrier(2)
    result_lock = threading.Lock()
    results: list[str] = []

    def reserve() -> None:
        db = session_factory()
        try:
            barrier.wait()
            try:
                admitted = file_utils.reserve_user_file_quota(
                    db,
                    user_id="user-1",
                    purpose="image_generation",
                )
                outcome = "admitted" if admitted else "unlimited"
            except file_utils.FileQuotaError as exc:
                outcome = exc.code
            with result_lock:
                results.append(outcome)
        finally:
            db.close()

    workers = [threading.Thread(target=reserve) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert sorted(results) == sorted(
        ["admitted", file_utils.USER_FILE_COUNT_QUOTA_REACHED]
    )


def test_generated_persistence_consumes_reservation_with_exact_byte_admission(
    monkeypatch,
    tmp_path: Path,
):
    """Final persistence replaces the estimate with actual bytes atomically."""

    db = _session()
    _configure_quota(monkeypatch, max_files=1, max_storage_bytes=5)
    monkeypatch.setattr(file_utils, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        file_utils,
        "upload_file_to_storage",
        lambda _path, user_id, file_name: (
            "local",
            f"{user_id}/{file_name}",
            {},
        ),
    )

    reservation = file_utils.reserve_user_file_quota(
        db,
        user_id="user-1",
        purpose="audio_generation",
    )
    assert reservation is not None

    record = file_utils.persist_generated_file_bytes(
        db,
        user_id="user-1",
        original_filename="speech.mp3",
        file_bytes=b"12345",
        file_type="audio/mpeg",
        file_category="audio",
        quota_reservation_id=reservation.reservation_id,
    )

    assert record.file_size == 5
    assert db.query(Files).count() == 1
    assert db.query(FileQuotaReservation).count() == 0


def test_failed_exact_byte_admission_keeps_storage_clean_and_can_release(
    monkeypatch,
    tmp_path: Path,
):
    """An underestimated provider result is rejected before object storage."""

    db = _session()
    _configure_quota(monkeypatch, max_files=1, max_storage_bytes=5)
    monkeypatch.setattr(file_utils, "TEMP_DIR", tmp_path)
    storage_calls: list[str] = []
    monkeypatch.setattr(
        file_utils,
        "upload_file_to_storage",
        lambda _path, user_id, file_name: (
            storage_calls.append(file_name) or "local",
            f"{user_id}/{file_name}",
            {},
        ),
    )
    reservation = file_utils.reserve_user_file_quota(
        db,
        user_id="user-1",
        purpose="music_generation",
    )
    assert reservation is not None

    with pytest.raises(file_utils.FileQuotaError) as exc_info:
        file_utils.persist_generated_file_bytes(
            db,
            user_id="user-1",
            original_filename="song.mp3",
            file_bytes=b"123456",
            file_type="audio/mpeg",
            file_category="audio",
            quota_reservation_id=reservation.reservation_id,
        )

    assert exc_info.value.code == file_utils.USER_FILE_STORAGE_QUOTA_REACHED
    assert storage_calls == []
    assert db.query(Files).count() == 0
    file_utils.release_user_file_quota_reservation(db, reservation.reservation_id)
    assert db.query(FileQuotaReservation).count() == 0


def test_expired_reservation_is_reclaimed_before_new_admission(monkeypatch):
    """A crashed worker cannot strand the user's file quota indefinitely."""

    db = _session()
    _configure_quota(monkeypatch, max_files=1)
    expired = FileQuotaReservation(
        id="expired",
        user_id="user-1",
        reserved_files=1,
        reserved_bytes=1,
        purpose="image_generation",
        created_at=file_utils.datetime.datetime.now(file_utils.datetime.timezone.utc)
        - file_utils.datetime.timedelta(hours=2),
        expires_at=file_utils.datetime.datetime.now(file_utils.datetime.timezone.utc)
        - file_utils.datetime.timedelta(minutes=1),
    )
    db.add(expired)
    db.commit()

    admitted = file_utils.reserve_user_file_quota(
        db,
        user_id="user-1",
        purpose="image_generation",
    )

    assert admitted is not None
    assert db.query(FileQuotaReservation).filter(FileQuotaReservation.id == "expired").count() == 0


def test_generated_replacement_uses_size_delta_and_consumes_reservation(
    monkeypatch,
    tmp_path: Path,
):
    """Replacing a file excludes its old bytes but includes every other file."""

    db = _session()
    _configure_quota(monkeypatch, max_files=2, max_storage_bytes=7)
    existing = create_file(
        db,
        user_id="user-1",
        file_category="document",
        file_type="application/octet-stream",
        file_size=5,
        file_id="presentation",
        file_name="presentation.pptx",
        storage_provider="local",
        storage_key="user-1/presentation.pptx",
    )
    create_file(
        db,
        user_id="user-1",
        file_category="document",
        file_type="text/plain",
        file_size=2,
        file_id="other",
        file_name="other.txt",
        storage_provider="local",
        storage_key="user-1/other.txt",
    )
    storage_calls: list[dict] = []
    deleted_references: list[dict] = []

    def fake_upload(**kwargs):
        storage_calls.append(kwargs)
        return "local", f"user-1/{kwargs['file_name']}", {}

    monkeypatch.setattr(file_utils, "MATERIALIZED_TEMP_DIR", tmp_path)
    monkeypatch.setattr(file_utils, "overwrite_existing_file_bytes", fake_upload)
    monkeypatch.setattr(
        file_utils,
        "delete_storage_reference",
        lambda **kwargs: deleted_references.append(kwargs),
    )
    reservation = file_utils.reserve_user_file_quota(
        db,
        user_id="user-1",
        purpose="slide_presentation_render",
        reserved_files=0,
        reserved_bytes=0,
    )
    assert reservation is not None

    updated = file_utils.persist_generated_file_replacement_bytes(
        db,
        user_id="user-1",
        file_record=existing,
        original_filename="presentation.pptx",
        file_bytes=b"12345",
        file_type="application/octet-stream",
        file_category="document",
        quota_reservation_id=reservation.reservation_id,
    )

    assert updated.file_size == 5
    assert updated.storage_key != "user-1/presentation.pptx"
    assert storage_calls[0]["file_name"].startswith("presentation.replacement-")
    assert storage_calls[0]["file_name"].endswith(".pptx")
    assert storage_calls[0]["update_materialized_cache"] is False
    assert deleted_references == [{
        "storage_provider": "local",
        "storage_key": "user-1/presentation.pptx",
        "user_id": "user-1",
        "file_name": "presentation.pptx",
    }]
    assert (tmp_path / "presentation.pptx").read_bytes() == b"12345"
    assert db.query(FileQuotaReservation).count() == 0


def test_generated_replacement_commit_failure_preserves_previous_storage_reference(
    monkeypatch,
    tmp_path: Path,
):
    """A failed metadata commit cleans the staged object without touching the old one."""

    db = _session()
    _configure_quota(monkeypatch)
    existing = create_file(
        db,
        user_id="user-1",
        file_category="document",
        file_type="application/pdf",
        file_size=3,
        file_id="presentation",
        file_name="presentation.pptx",
        storage_provider="local",
        storage_key="user-1/presentation.pptx",
        meta={"version": "old"},
    )
    staged: list[dict] = []
    cleanup_calls: list[dict] = []

    def fake_upload(**kwargs):
        staged.append(kwargs)
        return "local", f"user-1/{kwargs['file_name']}", {"etag": "new"}

    monkeypatch.setattr(file_utils, "MATERIALIZED_TEMP_DIR", tmp_path)
    monkeypatch.setattr(file_utils, "overwrite_existing_file_bytes", fake_upload)
    monkeypatch.setattr(
        file_utils,
        "_cleanup_unrecorded_storage_reference",
        lambda **kwargs: cleanup_calls.append(kwargs),
    )
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))

    with pytest.raises(RuntimeError, match="commit failed"):
        file_utils.persist_generated_file_replacement_bytes(
            db,
            user_id="user-1",
            file_record=existing,
            original_filename="presentation.pptx",
            file_bytes=b"new-content",
            file_type="application/octet-stream",
            file_category="document",
            meta={"version": "new"},
        )

    persisted = db.get(Files, "presentation")
    assert persisted.storage_key == "user-1/presentation.pptx"
    assert persisted.file_size == 3
    assert persisted.file_type == "application/pdf"
    assert persisted.meta == {"version": "old"}
    assert staged[0]["update_materialized_cache"] is False
    assert cleanup_calls == [{
        "storage_provider": "local",
        "storage_key": f"user-1/{staged[0]['file_name']}",
        "user_id": "user-1",
        "file_name": None,
    }]


def test_generated_replacement_audit_failure_preserves_previous_storage_reference(
    monkeypatch,
    tmp_path: Path,
):
    """A failed audit stage rolls back metadata and cleans only staged bytes."""

    db = _session()
    _configure_quota(monkeypatch)
    existing = create_file(
        db,
        user_id="user-1",
        file_category="document",
        file_type="application/pdf",
        file_size=3,
        file_id="document",
        file_name="document.pdf",
        storage_provider="local",
        storage_key="user-1/document.pdf",
        meta={"version": "old"},
    )
    staged: list[dict] = []
    cleanup_calls: list[dict] = []

    def fake_upload(**kwargs):
        staged.append(kwargs)
        return "local", f"user-1/{kwargs['file_name']}", {"etag": "new"}

    def fail_audit(saved_file):
        assert saved_file.storage_key != "user-1/document.pdf"
        raise RuntimeError("audit outbox unavailable")

    monkeypatch.setattr(file_utils, "MATERIALIZED_TEMP_DIR", tmp_path)
    monkeypatch.setattr(file_utils, "overwrite_existing_file_bytes", fake_upload)
    monkeypatch.setattr(
        file_utils,
        "_cleanup_unrecorded_storage_reference",
        lambda **kwargs: cleanup_calls.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="audit outbox unavailable"):
        file_utils.persist_generated_file_replacement_bytes(
            db,
            user_id="user-1",
            file_record=existing,
            original_filename="document.pdf",
            file_bytes=b"new-content",
            file_type="application/pdf",
            file_category="document",
            meta={"version": "new"},
            before_commit=fail_audit,
        )

    persisted = db.get(Files, "document")
    assert persisted.storage_key == "user-1/document.pdf"
    assert persisted.file_size == 3
    assert persisted.meta == {"version": "old"}
    assert cleanup_calls == [{
        "storage_provider": "local",
        "storage_key": f"user-1/{staged[0]['file_name']}",
        "user_id": "user-1",
        "file_name": None,
    }]


def test_generated_replacement_rejects_growth_before_storage(monkeypatch):
    """A regenerated artifact cannot overwrite storage before exact admission."""

    db = _session()
    _configure_quota(monkeypatch, max_files=2, max_storage_bytes=7)
    existing = create_file(
        db,
        user_id="user-1",
        file_category="document",
        file_type="application/octet-stream",
        file_size=5,
        file_id="presentation",
        file_name="presentation.pptx",
        storage_provider="local",
        storage_key="user-1/presentation.pptx",
    )
    create_file(
        db,
        user_id="user-1",
        file_category="document",
        file_type="text/plain",
        file_size=2,
        file_id="other",
        file_name="other.txt",
        storage_provider="local",
        storage_key="user-1/other.txt",
    )
    storage_calls: list[int] = []
    monkeypatch.setattr(
        file_utils,
        "overwrite_existing_file_bytes",
        lambda **kwargs: storage_calls.append(len(kwargs["file_bytes"])),
    )
    reservation = file_utils.reserve_user_file_quota(
        db,
        user_id="user-1",
        purpose="slide_presentation_render",
        reserved_files=0,
        reserved_bytes=0,
    )
    assert reservation is not None

    with pytest.raises(file_utils.FileQuotaError) as exc_info:
        file_utils.persist_generated_file_replacement_bytes(
            db,
            user_id="user-1",
            file_record=existing,
            original_filename="presentation.pptx",
            file_bytes=b"123456",
            file_type="application/octet-stream",
            file_category="document",
            quota_reservation_id=reservation.reservation_id,
        )

    assert exc_info.value.code == file_utils.USER_FILE_STORAGE_QUOTA_REACHED
    assert storage_calls == []
    assert db.get(Files, "presentation").file_size == 5
