from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.skills import router as skills_router  # noqa: E402


class _UnreadableUpload:
    filename = "payload.txt"

    async def read(self, _size=-1):
        raise AssertionError("upload body should not be read")

    async def seek(self, _offset):
        return None


class _ChunkedUpload:
    filename = "payload.txt"

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.read_sizes: list[int] = []

    async def read(self, size=-1):
        self.read_sizes.append(size)
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    async def seek(self, _offset):
        return None


@pytest.mark.anyio
async def test_skill_upload_rejects_unknown_skill_before_reading_body(monkeypatch):
    monkeypatch.setattr(skills_router, "ensure_skills_enabled", lambda *_args, **_kwargs: None)

    def fake_get_skill_with_access(*_args, **_kwargs):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")

    monkeypatch.setattr(skills_router, "get_skill_with_access", fake_get_skill_with_access)
    monkeypatch.setattr(
        skills_router,
        "_store_validated_skill_upload",
        lambda *_args, **_kwargs: pytest.fail("unknown skills must not store uploads"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await skills_router.upload_skill_files_endpoint(
            request=SimpleNamespace(headers={}, client=None),
            skill_id="missing-skill",
            folder_type="assets",
            files=[_UnreadableUpload()],
            user=SimpleNamespace(id="user-1"),
            db=object(),
            db_log=object(),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_skill_upload_spools_in_chunks_and_enforces_limit(monkeypatch):
    upload = _ChunkedUpload([b"1234", b"5678"])
    monkeypatch.setattr(skills_router, "validate_upload_file", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        await skills_router._spool_upload_to_temp_file(
            upload,
            max_upload_bytes=5,
            max_upload_mb=1,
        )

    assert exc_info.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE
    assert upload.read_sizes == [skills_router.CHUNK_SIZE, skills_router.CHUNK_SIZE]


@pytest.mark.anyio
async def test_collaborator_upload_uses_the_stricter_owner_and_collaborator_limits(monkeypatch, tmp_path):
    temp_path = tmp_path / "upload.tmp"
    temp_path.write_bytes(b"data")
    observed = {}

    monkeypatch.setattr(
        skills_router,
        "resolve_user_file_upload_limits",
        lambda _db, user_id: (10, 1000) if user_id == "owner-1" else (2, 100),
    )
    monkeypatch.setattr(
        skills_router,
        "resolve_user_max_upload_size_bytes",
        lambda _db, user_id: (100, 1) if user_id == "owner-1" else (5, 1),
    )

    async def fake_spool(_upload, *, max_upload_bytes, max_upload_mb):
        observed["upload"] = (max_upload_bytes, max_upload_mb)
        return temp_path, 4

    def fake_capacity(_db, user_id, file_size, **limits):
        observed["capacity"] = (user_id, file_size, limits)

    monkeypatch.setattr(skills_router, "_spool_upload_to_temp_file", fake_spool)
    monkeypatch.setattr(skills_router, "_ensure_skill_file_upload_capacity", fake_capacity)
    monkeypatch.setattr(skills_router, "serialized_user_file_quota_admission", lambda *_args: nullcontext())
    monkeypatch.setattr(skills_router, "upload_skill_file", lambda *_args, **_kwargs: {"name": "payload.txt"})

    await skills_router._store_validated_skill_upload(
        SimpleNamespace(filename="payload.txt"),
        user_id="owner-1",
        uploading_user_id="collaborator-1",
        skill_id="skill-1",
        folder_type="assets",
        db=object(),
    )

    assert observed["upload"] == (5, 1)
    assert observed["capacity"][0:2] == ("owner-1", 4)
    assert observed["capacity"][2]["max_files_limit"] == 2
    assert observed["capacity"][2]["max_user_storage_limit_bytes"] == 100
