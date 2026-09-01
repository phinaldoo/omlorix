import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


from app.file_folders import router as file_folders_router
from app.file_folders.schemas import (
    FILE_FOLDER_BULK_FILE_IDS_LIMIT,
    FILE_FOLDER_BULK_USER_IDS_LIMIT,
    FileFolderFileIds,
    InviteUsersRequest,
    MoveFileRequest,
)


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})


def _user(user_id="user-1"):
    return SimpleNamespace(id=user_id)


def test_file_folder_file_ids_rejects_more_than_bulk_limit():
    with pytest.raises(ValidationError):
        FileFolderFileIds(
            file_ids=[f"file-{index}" for index in range(FILE_FOLDER_BULK_FILE_IDS_LIMIT + 1)]
        )


def test_invite_users_request_rejects_more_than_bulk_limit():
    with pytest.raises(ValidationError):
        InviteUsersRequest(
            item_id="folder-1",
            user_ids=[f"user-{index}" for index in range(FILE_FOLDER_BULK_USER_IDS_LIMIT + 1)],
        )


def test_add_files_route_creates_audit_log(monkeypatch):
    audit_calls: list[dict] = []
    db_log = object()

    monkeypatch.setattr(
        file_folders_router,
        "add_files_to_folder",
        lambda db, user_id, folder_id, file_ids: {"ok": True, "updated": len(file_ids)},
    )
    monkeypatch.setattr(file_folders_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    payload = FileFolderFileIds(file_ids=["file-1", "file-2"])
    response = file_folders_router.add_files_route(
        "folder-1",
        payload,
        _request(),
        db=object(),
        db_log=db_log,
        user=_user(),
    )

    assert response == {"ok": True, "updated": 2}
    assert audit_calls == [
        {
            "db_log": db_log,
            "user_id": "user-1",
            "action": "FILE_FOLDER_FILES_ADDED",
            "details": {
                "folder_id": "folder-1",
                "file_ids": ["file-1", "file-2"],
                "requested_file_count": 2,
                "updated_file_count": 2,
            },
            "ip_address": "203.0.113.10",
            "user_agent": "pytest",
            "category": "files",
        }
    ]


def test_remove_files_route_creates_audit_log(monkeypatch):
    audit_calls: list[dict] = []
    db_log = object()

    monkeypatch.setattr(
        file_folders_router,
        "remove_files_from_folder",
        lambda db, user_id, folder_id, file_ids: {"ok": True, "updated": 1},
    )
    monkeypatch.setattr(file_folders_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    payload = FileFolderFileIds(file_ids=["file-3"])
    response = file_folders_router.remove_files_route(
        "folder-2",
        payload,
        _request(),
        db=object(),
        db_log=db_log,
        user=_user(),
    )

    assert response == {"ok": True, "updated": 1}
    assert audit_calls == [
        {
            "db_log": db_log,
            "user_id": "user-1",
            "action": "FILE_FOLDER_FILES_REMOVED",
            "details": {
                "folder_id": "folder-2",
                "file_ids": ["file-3"],
                "requested_file_count": 1,
                "updated_file_count": 1,
            },
            "ip_address": "203.0.113.10",
            "user_agent": "pytest",
            "category": "files",
        }
    ]


def test_move_file_route_creates_audit_log(monkeypatch):
    audit_calls: list[dict] = []
    db_log = object()

    monkeypatch.setattr(
        file_folders_router,
        "get_file",
        lambda db, file_id, user_id: SimpleNamespace(folder_id="folder-1"),
    )
    monkeypatch.setattr(
        file_folders_router,
        "move_file_to_folder",
        lambda db, user_id, file_id, folder_id: {"ok": True},
    )
    monkeypatch.setattr(file_folders_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    response = file_folders_router.move_file_route(
        MoveFileRequest(file_id="file-4", folder_id="folder-5"),
        _request(),
        db=object(),
        db_log=db_log,
        user=_user(),
    )

    assert response == {"ok": True}
    assert audit_calls == [
        {
            "db_log": db_log,
            "user_id": "user-1",
            "action": "FILE_FOLDER_FILE_MOVED",
            "details": {
                "file_id": "file-4",
                "source_folder_id": "folder-1",
                "destination_folder_id": "folder-5",
            },
            "ip_address": "203.0.113.10",
            "user_agent": "pytest",
            "category": "files",
        }
    ]
