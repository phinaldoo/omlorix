from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager
from io import BytesIO
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

from app.database import Base  # noqa: E402
from app.files import utils as file_utils  # noqa: E402
from app.files.models import FileQuotaReservation, Files, create_file  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Files.__table__, FileQuotaReservation.__table__],
    )
    return sessionmaker(bind=engine)()


def _upload(payload: bytes, filename: str = "notes.txt") -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(payload))


def _configure_upload(monkeypatch, *, max_files=-1, max_user_storage_gb=None, max_upload_mb=None):
    def fake_group_setting(_user_id, _section, key, _db):
        if key == "allow_file_uploads":
            return True
        if key == "max_files_upload_count":
            return max_files
        if key == "max_user_files_size_gb":
            return max_user_storage_gb
        if key == "max_upload_size":
            return max_upload_mb
        return None

    monkeypatch.setattr(file_utils, "get_user_group_setting_value", fake_group_setting)
    monkeypatch.setattr(file_utils, "_detect_mime_from_content", lambda _path, fallback=None: fallback or "text/plain")
    monkeypatch.setattr(
        file_utils,
        "upload_file_to_storage",
        lambda _path, user_id, file_name: ("local", f"{user_id}/{file_name}", {}),
    )


def test_duplicate_upload_into_folder_creates_scoped_record_without_moving_private_file(monkeypatch):
    db = _session()
    _configure_upload(monkeypatch)

    payload = b"private content"
    private_record = create_file(
        db,
        "user-1",
        "document",
        "text/plain",
        len(payload),
        meta={
            "original_filename": "notes.txt",
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        file_id="private-file",
        file_name="private-file.txt",
        storage_provider="local",
        storage_key="user-1/private-file.txt",
    )

    result = asyncio.run(
        file_utils.upload_file(
            _upload(payload),
            project_id=None,
            user_id="user-1",
            db=db,
            folder_id="shared-folder",
        )
    )

    db.refresh(private_record)
    scoped_record = db.query(Files).filter(Files.id == result["file_id"]).one()

    assert result["status"] == "success"
    assert result["already_uploaded"] is False
    assert result["file_id"] != "private-file"
    assert private_record.folder_id is None
    assert scoped_record.folder_id == "shared-folder"


def test_duplicate_upload_reuses_record_in_same_folder(monkeypatch):
    db = _session()
    _configure_upload(monkeypatch)

    payload = b"shared content"
    create_file(
        db,
        "user-1",
        "document",
        "text/plain",
        len(payload),
        meta={
            "original_filename": "notes.txt",
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        file_id="shared-file",
        file_name="shared-file.txt",
        storage_provider="local",
        storage_key="user-1/shared-file.txt",
        folder_id="shared-folder",
    )

    result = asyncio.run(
        file_utils.upload_file(
            _upload(payload),
            project_id=None,
            user_id="user-1",
            db=db,
            folder_id="shared-folder",
        )
    )

    assert result == {
        "status": "success",
        "file_id": "shared-file",
        "file_category": "document",
        "already_uploaded": True,
    }
    assert db.query(Files).count() == 1


def test_upload_rechecks_file_count_during_serialized_admission(monkeypatch):
    db = _session()
    _configure_upload(monkeypatch, max_files=1)

    storage_calls = []

    @contextmanager
    def competing_admission(_db, _user_id):
        create_file(
            db,
            "user-1",
            "document",
            "text/plain",
            1,
            file_id="competing-file",
            file_name="competing-file.txt",
            storage_provider="local",
            storage_key="user-1/competing-file.txt",
        )
        yield

    def fake_upload(_path, user_id, file_name):
        storage_calls.append(file_name)
        return "local", f"{user_id}/{file_name}", {}

    monkeypatch.setattr(file_utils, "serialized_user_file_quota_admission", competing_admission)
    monkeypatch.setattr(file_utils, "upload_file_to_storage", fake_upload)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(file_utils.upload_file(_upload(b"new content"), None, "user-1", db))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Maximum number of uploaded files reached"
    assert storage_calls == []
    assert db.query(Files).count() == 1


def test_upload_rechecks_storage_quota_during_serialized_admission(monkeypatch):
    db = _session()
    max_storage_gb = str(10 / (1024 ** 3))
    _configure_upload(monkeypatch, max_files=-1, max_user_storage_gb=max_storage_gb)

    storage_calls = []

    @contextmanager
    def competing_admission(_db, _user_id):
        create_file(
            db,
            "user-1",
            "document",
            "text/plain",
            5,
            file_id="competing-file",
            file_name="competing-file.txt",
            storage_provider="local",
            storage_key="user-1/competing-file.txt",
        )
        yield

    def fake_upload(_path, user_id, file_name):
        storage_calls.append(file_name)
        return "local", f"{user_id}/{file_name}", {}

    monkeypatch.setattr(file_utils, "serialized_user_file_quota_admission", competing_admission)
    monkeypatch.setattr(file_utils, "upload_file_to_storage", fake_upload)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(file_utils.upload_file(_upload(b"123456"), None, "user-1", db))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Maximum storage quota reached"
    assert storage_calls == []
    assert db.query(Files).count() == 1


def test_upload_file_enforces_group_max_upload_size_for_all_callers(monkeypatch):
    db = _session()
    _configure_upload(monkeypatch, max_upload_mb=1)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(file_utils.upload_file(_upload(b"x" * (2 * 1024 * 1024)), None, "user-1", db))

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "File size exceeds limit of 1 MB"


def test_quota_admission_uses_postgres_advisory_lock():
    calls = []

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Db:
        def get_bind(self):
            return _Bind()

        def execute(self, statement, params):
            calls.append((str(statement), params))

    with file_utils.serialized_user_file_quota_admission(_Db(), "user-1"):
        pass

    assert len(calls) == 1
    assert "pg_advisory_xact_lock" in calls[0][0]
    assert isinstance(calls[0][1]["lock_key"], int)
