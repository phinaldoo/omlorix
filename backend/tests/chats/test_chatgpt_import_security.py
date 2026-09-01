from __future__ import annotations

from io import BytesIO
from pathlib import Path
import hashlib
import sys
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats import chatgpt_import  # noqa: E402
from app.database import Base  # noqa: E402
from app.files.models import Files  # noqa: E402


# These tests exercise content validation and malware-scanning order, not the
# application's configurable per-user upload limit. Keep a small explicit
# allowance here so they do not depend on an unrelated implementation symbol.
TEST_MAX_UPLOAD_BYTES = 1024 * 1024
TEST_MAX_UPLOAD_MB = 1


class _EmptyQuery:
    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return []


class _EmptyDb:
    def query(self, *_args, **_kwargs):
        return _EmptyQuery()


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Files.__table__])
    return sessionmaker(bind=engine)()


def _zip_with_asset(path: str, payload: bytes) -> BytesIO:
    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr(path, payload)
    archive_bytes.seek(0)
    return archive_bytes


def test_chatgpt_import_rejects_asset_when_detected_type_disagrees_with_metadata(monkeypatch):
    db = _session()
    upload_calls = []

    monkeypatch.setattr(
        chatgpt_import,
        "_detect_mime_from_content",
        lambda _path, fallback=None: "application/x-msdownload",
    )
    monkeypatch.setattr(
        chatgpt_import,
        "upload_file_to_storage",
        lambda *args, **kwargs: upload_calls.append((args, kwargs)),
    )

    archive_bytes = _zip_with_asset("assets/file_abc.png", b"MZ executable payload")
    with zipfile.ZipFile(archive_bytes) as archive:
        with pytest.raises(HTTPException) as exc_info:
            chatgpt_import._import_archive_asset(
                db,
                archive=archive,
                archive_path="assets/file_abc.png",
                user_id="user-1",
                conversation_id="conversation-1",
                asset_id="file_abc",
                asset_meta={"mime_type": "image/png", "original_filename": "photo.png"},
                imported_file_cache={},
                max_files_limit=-1,
                max_user_storage_limit_bytes=None,
                max_upload_bytes=TEST_MAX_UPLOAD_BYTES,
                max_upload_mb=TEST_MAX_UPLOAD_MB,
            )

    assert exc_info.value.status_code == 400
    assert "unsupported file type" in exc_info.value.detail
    assert upload_calls == []
    assert db.query(Files).count() == 0


def test_chatgpt_import_persists_validated_asset_mime_and_hash(monkeypatch):
    db = _session()
    payload = b"plain imported attachment"

    monkeypatch.setattr(
        chatgpt_import,
        "_detect_mime_from_content",
        lambda _path, fallback=None: "text/plain",
    )

    def fake_upload(path, user_id, file_name):
        assert Path(path).read_bytes() == payload
        return "local", f"{user_id}/{file_name}", {}

    monkeypatch.setattr(chatgpt_import, "upload_file_to_storage", fake_upload)

    archive_bytes = _zip_with_asset("assets/file_text.txt", payload)
    with zipfile.ZipFile(archive_bytes) as archive:
        file_record, is_new = chatgpt_import._import_archive_asset(
            db,
            archive=archive,
            archive_path="assets/file_text.txt",
            user_id="user-1",
            conversation_id="conversation-1",
            asset_id="file_text",
            asset_meta={"mime_type": "image/png", "original_filename": "notes.txt"},
            imported_file_cache={},
            max_files_limit=-1,
            max_user_storage_limit_bytes=None,
            max_upload_bytes=TEST_MAX_UPLOAD_BYTES,
            max_upload_mb=TEST_MAX_UPLOAD_MB,
        )

    assert is_new is True
    assert file_record is not None
    assert file_record.file_type == "text/plain"
    assert file_record.file_category == "document"
    assert file_record.meta["sha256"] == hashlib.sha256(payload).hexdigest()


def test_chatgpt_import_defers_file_upload_policy_until_archive_has_assets(monkeypatch):
    def fail_resolve(*_args, **_kwargs):
        raise AssertionError("file upload policy should not be resolved for asset-free imports")

    monkeypatch.setattr(chatgpt_import, "resolve_user_file_upload_limits", fail_resolve)

    archive_bytes = _zip_with_asset("conversations.json", b"[]")

    result = chatgpt_import.import_chatgpt_export_archive(_EmptyDb(), "user-1", archive_bytes)

    assert result["imported_chats"] == 0
    assert result["imported_files"] == 0
