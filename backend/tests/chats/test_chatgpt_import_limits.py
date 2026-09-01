import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats import chatgpt_import


def _zip_bytes(entries: dict[str, bytes | str], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, payload in entries.items():
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            archive.writestr(name, payload)
    return buffer.getvalue()


def _open_archive(entries: dict[str, bytes | str], *, compression=zipfile.ZIP_DEFLATED) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(_zip_bytes(entries, compression=compression)))


def _assert_payload_too_large(exc_info):
    assert exc_info.value.status_code == 413


def test_chatgpt_import_rejects_oversized_upload_before_opening_archive(monkeypatch):
    payload = _zip_bytes({"conversations.json": "[]"})
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_ARCHIVE_BYTES", max(1, len(payload) - 1))

    with pytest.raises(HTTPException) as exc_info:
        chatgpt_import.import_chatgpt_export_archive(object(), "user-1", io.BytesIO(payload))

    _assert_payload_too_large(exc_info)


def test_chatgpt_import_rejects_too_many_archive_entries(monkeypatch):
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_ENTRIES", 2)

    with _open_archive(
        {
            "conversations.json": "[]",
            "assets/file_1.txt": "one",
            "assets/file_2.txt": "two",
        }
    ) as archive:
        with pytest.raises(HTTPException) as exc_info:
            chatgpt_import._validate_archive_limits(archive)

    _assert_payload_too_large(exc_info)


def test_chatgpt_import_rejects_oversized_total_uncompressed_content(monkeypatch):
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES", 20)
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_ASSET_BYTES", 100)
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_JSON_ENTRY_BYTES", 100)

    with _open_archive({"conversations.json": "[]", "assets/file_1.txt": "x" * 30}) as archive:
        with pytest.raises(HTTPException) as exc_info:
            chatgpt_import._validate_archive_limits(archive)

    _assert_payload_too_large(exc_info)


def test_chatgpt_import_rejects_high_compression_ratio_entry(monkeypatch):
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_COMPRESSION_RATIO", 2)
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES", 10_000)
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_ASSET_BYTES", 10_000)

    with _open_archive({"conversations.json": "[]", "assets/file_1.txt": "x" * 4000}) as archive:
        with pytest.raises(HTTPException) as exc_info:
            chatgpt_import._validate_archive_limits(archive)

    _assert_payload_too_large(exc_info)


def test_chatgpt_import_rejects_unsafe_archive_member_paths():
    with _open_archive({"../conversations.json": "[]"}) as archive:
        with pytest.raises(HTTPException) as exc_info:
            chatgpt_import._validate_archive_limits(archive)

    assert exc_info.value.status_code == 400


def test_chatgpt_import_rejects_oversized_json_while_reading(monkeypatch):
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_JSON_ENTRY_BYTES", 10)
    payload = json.dumps([{"id": "conversation-1", "title": "large enough"}])

    with _open_archive({"conversations.json": payload}) as archive:
        with pytest.raises(HTTPException) as exc_info:
            chatgpt_import._load_conversations(archive)

    _assert_payload_too_large(exc_info)


def test_chatgpt_import_rejects_oversized_asset_before_storage(monkeypatch):
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_ASSET_BYTES", 3)

    with _open_archive({"conversations.json": "[]", "assets/file_1.txt": "four"}) as archive:
        with pytest.raises(HTTPException) as exc_info:
            chatgpt_import._import_archive_asset(
                object(),
                archive=archive,
                archive_path="assets/file_1.txt",
                user_id="user-1",
                conversation_id="conversation-1",
                asset_id="file_1",
                asset_meta=None,
                imported_file_cache={},
                max_files_limit=100,
                max_user_storage_limit_bytes=None,
                max_upload_bytes=100,
                max_upload_mb=1,
            )

    _assert_payload_too_large(exc_info)


def test_chatgpt_import_rejects_archive_exceeding_user_upload_size(monkeypatch):
    payload = _zip_bytes({"conversations.json": "[]"})
    monkeypatch.setattr(chatgpt_import, "resolve_user_max_upload_size_bytes", lambda _db, _user_id: (1, 1))

    with pytest.raises(HTTPException) as exc_info:
        chatgpt_import.import_chatgpt_export_archive(object(), "user-1", io.BytesIO(payload))

    _assert_payload_too_large(exc_info)


def test_chatgpt_import_rejects_asset_exceeding_user_upload_size(monkeypatch):
    monkeypatch.setattr(chatgpt_import, "CHATGPT_IMPORT_MAX_ASSET_BYTES", 100)

    with _open_archive({"conversations.json": "[]", "assets/file_1.txt": "four"}) as archive:
        with pytest.raises(HTTPException) as exc_info:
            chatgpt_import._import_archive_asset(
                object(),
                archive=archive,
                archive_path="assets/file_1.txt",
                user_id="user-1",
                conversation_id="conversation-1",
                asset_id="file_1",
                asset_meta=None,
                imported_file_cache={},
                max_files_limit=100,
                max_user_storage_limit_bytes=None,
                max_upload_bytes=3,
                max_upload_mb=1,
            )

    _assert_payload_too_large(exc_info)
