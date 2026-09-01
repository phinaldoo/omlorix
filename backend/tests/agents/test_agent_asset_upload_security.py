from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agents import utils as agents_utils  # noqa: E402
from app.files import utils as file_utils  # noqa: E402


class FakeDb:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.committed = True

    def refresh(self, _value):
        return None


def _allow_agent_edit(monkeypatch):
    monkeypatch.setattr(agents_utils, "can_user_edit_agent", lambda _db, _user_id, _agent_id: True)


def test_agent_asset_upload_rejects_mislabeled_active_content(monkeypatch):
    _allow_agent_edit(monkeypatch)
    monkeypatch.setattr(
        agents_utils,
        "upload_file_to_storage",
        lambda *_args, **_kwargs: pytest.fail("unsafe asset should not be stored"),
    )

    with pytest.raises(HTTPException) as exc_info:
        agents_utils.create_user_agent_asset(
            FakeDb(),
            user_id="user-1",
            agent_id="agent-1",
            filename="notes.txt",
            content=b"<!doctype html><html><body><script>alert(1)</script></body></html>",
        )

    assert exc_info.value.status_code == 400
    assert "text/html" in str(exc_info.value.detail)


def test_agent_asset_upload_validates_before_storage_and_persists_detected_mime(monkeypatch):
    _allow_agent_edit(monkeypatch)

    monkeypatch.setattr(file_utils, "_detect_mime_from_content", lambda _path, fallback=None: "text/plain")

    def fake_upload(path, user_id, file_name):
        assert Path(path).read_bytes() == b"plain text"
        return "local", f"{user_id}/{file_name}", {"size": Path(path).stat().st_size}

    monkeypatch.setattr(agents_utils, "upload_file_to_storage", fake_upload)

    response = agents_utils.create_user_agent_asset(
        FakeDb(),
        user_id="user-1",
        agent_id="agent-1",
        filename="image.png",
        content=b"plain text",
    )

    assert response["file_type"] == "text/plain"
    assert response["file_category"] == "document"
    assert response["file_size"] == len(b"plain text")
    assert response["file_name"] == "image.png"
    assert response["original_filename"] == "image.png"


def test_workspace_file_attachment_response_preserves_original_filename(monkeypatch, tmp_path):
    _allow_agent_edit(monkeypatch)
    source_path = tmp_path / "stored-source"
    source_path.write_text("workspace reference", encoding="utf-8")
    source_record = SimpleNamespace(
        id="file-1",
        file_name="user-1-opaque-storage-name.md",
        file_type="text/markdown",
        file_size=source_path.stat().st_size,
        meta={"original_filename": "workspace-e2e-renamed.md"},
    )
    db = FakeDb()

    monkeypatch.setattr(agents_utils, "get_file", lambda *_args: source_record)
    monkeypatch.setattr(agents_utils, "materialize_file_record", lambda *_args: source_path)
    monkeypatch.setattr(agents_utils, "validate_upload_file", lambda *_args, **_kwargs: "text/markdown")
    monkeypatch.setattr(
        agents_utils,
        "upload_file_to_storage",
        lambda _path, user_id, file_name: ("local", f"{user_id}/{file_name}", {}),
    )

    response = agents_utils.create_user_agent_asset_from_file(
        db,
        user_id="user-1",
        agent_id="agent-1",
        file_id="file-1",
    )

    stored_asset = db.added[0]
    assert stored_asset.file_name.startswith("agent-agent-1-")
    assert stored_asset.meta == {"original_filename": "workspace-e2e-renamed.md"}
    assert response["file_name"] == "workspace-e2e-renamed.md"
    assert response["original_filename"] == "workspace-e2e-renamed.md"
