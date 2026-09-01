from __future__ import annotations

import asyncio
from io import BytesIO
import sys
from pathlib import Path
from types import SimpleNamespace
import types

import pytest
from fastapi import HTTPException, UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if "zstandard" not in sys.modules:
    fake_zstandard = types.ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        compress=lambda data: data,
        stream_writer=lambda fh: fh,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        decompress=lambda data: data,
        stream_reader=lambda fh: fh,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.files import router as files_router  # noqa: E402
from app.files.schemas import FileDeleteTimeOption  # noqa: E402


def _upload(filename: str = "context.txt") -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(b"context"))


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})


def _user(group_id: str = "group-1"):
    return SimpleNamespace(id="user-1", group_id=group_id)


def _deny_group_capability(*_args, **_kwargs):
    raise HTTPException(status_code=403, detail="You do not have permission to manage this group")


def _allow_group_capability(*_args, **_kwargs):
    return {"role": "manager", "capabilities": ["manage_settings"]}


def test_group_context_upload_requires_group_management_permission_before_persisting(monkeypatch):
    upload_called = False

    async def fake_upload_file(*_args, **_kwargs):
        nonlocal upload_called
        upload_called = True
        return {"status": "success", "file_id": "file-1"}

    monkeypatch.setattr(files_router, "require_group_capability", _deny_group_capability)
    monkeypatch.setattr(files_router, "upload_file", fake_upload_file)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            files_router.upload_file_route(
                _request(),
                _upload(),
                project_id=None,
                folder_id=None,
                group_context_id="group-1",
                model_id=None,
                user=_user(group_id="group-1"),
                db=object(),
                db_log=object(),
            )
        )

    assert exc.value.status_code == 403
    assert upload_called is False


def test_group_context_upload_authorizes_target_before_persisting(monkeypatch):
    upload_called = False
    authorization_calls: list[tuple[object, object, str, str]] = []

    async def fake_upload_file(*_args, **_kwargs):
        nonlocal upload_called
        upload_called = True
        return {"status": "success", "file_id": "file-1"}

    def deny_target_group(db, user, group_id, capability):
        authorization_calls.append((db, user, group_id, capability))
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to manage this group",
        )

    monkeypatch.setattr(files_router, "require_group_capability", deny_target_group)
    monkeypatch.setattr(files_router, "upload_file", fake_upload_file)

    db = object()
    user = _user(group_id="group-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            files_router.upload_file_route(
                _request(),
                _upload(),
                project_id=None,
                folder_id=None,
                group_context_id="other-group",
                model_id=None,
                user=user,
                db=db,
                db_log=object(),
            )
        )

    assert exc.value.status_code == 403
    assert authorization_calls == [(db, user, "other-group", "manage_settings")]
    assert upload_called is False


def test_group_context_upload_deletes_persisted_file_when_group_update_fails(monkeypatch):
    deleted: list[tuple[str, str, FileDeleteTimeOption]] = []

    async def fake_upload_file(*_args, **_kwargs):
        return {"status": "success", "file_id": "file-1"}

    def fail_update(*_args, **_kwargs):
        raise RuntimeError("settings failed")

    def fake_delete_file(user_id, file_id, db, time_option):
        deleted.append((user_id, file_id, time_option))
        return {"deleted": True}

    monkeypatch.setattr(files_router, "require_group_capability", _allow_group_capability)
    monkeypatch.setattr(files_router, "get_group_setting_value", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(files_router, "upload_file", fake_upload_file)
    monkeypatch.setattr(files_router, "update_group_settings", fail_update)
    monkeypatch.setattr(files_router, "delete_file", fake_delete_file)

    with pytest.raises(RuntimeError, match="settings failed"):
        asyncio.run(
            files_router.upload_file_route(
                _request(),
                _upload(),
                project_id=None,
                folder_id=None,
                group_context_id="group-1",
                model_id=None,
                user=_user(group_id="group-1"),
                db=object(),
                db_log=object(),
            )
        )

    assert deleted == [("user-1", "file-1", FileDeleteTimeOption.ALL)]
