import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


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

from app.notes import router as notes_router
from app.notes.schemas import NoteUpdate


def _note(**overrides):
    values = {
        "id": "note-1",
        "user_id": "owner-1",
        "content": "# Shared note\nBody",
        "clone_share_id": "clone-token",
        "live_share_id": "live-token",
        "collaborate_share_id": "collab-token",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(user_id):
    return SimpleNamespace(id=user_id)


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})


def test_download_note_route_returns_markdown_for_accessible_note(monkeypatch):
    note = _note(content="# Download me\nBody")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = note

    monkeypatch.setattr(notes_router, "ensure_notes_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(notes_router, "can_user_view_note", lambda *args, **kwargs: True)
    monkeypatch.setattr(notes_router, "create_audit_log", lambda *args, **kwargs: None)

    response = notes_router.download_note_route(
        note_id="note-1",
        request=_request(),
        format="md",
        db=db,
        db_log=SimpleNamespace(),
        user=_user("viewer-1"),
    )

    assert response.media_type == "text/markdown; charset=utf-8"
    assert response.body == b"# Download me\nBody"
    assert 'filename="Download me.md"' in response.headers["content-disposition"]
    assert "filename*=UTF-8''Download%20me.md" in response.headers["content-disposition"]


def test_download_note_route_uses_rfc6266_header_for_unicode_title(monkeypatch):
    note = _note(content="# Résumé 📄\nBody")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = note

    monkeypatch.setattr(notes_router, "ensure_notes_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(notes_router, "can_user_view_note", lambda *args, **kwargs: True)
    monkeypatch.setattr(notes_router, "create_audit_log", lambda *args, **kwargs: None)

    response = notes_router.download_note_route(
        note_id="note-1",
        request=_request(),
        format="md",
        db=db,
        db_log=SimpleNamespace(),
        user=_user("viewer-1"),
    )

    disposition = response.headers["content-disposition"]
    assert 'filename="Resume.md"' in disposition
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9%20%F0%9F%93%84.md" in disposition


def test_download_note_route_returns_rendered_pdf_for_accessible_note(monkeypatch):
    note = _note(content="# PDF note\nBody")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = note
    render_calls = []

    def fake_render_pdf(db_arg, **kwargs):
        render_calls.append((db_arg, kwargs))
        return SimpleNamespace(filename="PDF note.pdf", content=b"%PDF-test")

    monkeypatch.setattr(notes_router, "ensure_notes_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(notes_router, "can_user_view_note", lambda *args, **kwargs: True)
    monkeypatch.setattr(notes_router, "create_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(notes_router, "render_canvas_markdown_pdf", fake_render_pdf)

    response = notes_router.download_note_route(
        note_id="note-1",
        request=_request(),
        format="pdf",
        db=db,
        db_log=SimpleNamespace(),
        user=_user("viewer-1"),
    )

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-test"
    assert 'filename="PDF note.pdf"' in response.headers["content-disposition"]
    assert "filename*=UTF-8''PDF%20note.pdf" in response.headers["content-disposition"]
    assert render_calls[0][1]["markdown_text"] == "# PDF note\nBody"
    assert render_calls[0][1]["user_id"] == "viewer-1"


def test_download_note_route_rejects_inaccessible_note(monkeypatch):
    note = _note()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = note

    monkeypatch.setattr(notes_router, "ensure_notes_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(notes_router, "can_user_view_note", lambda *args, **kwargs: False)

    with pytest.raises(notes_router.HTTPException) as exc:
        notes_router.download_note_route(
            note_id="note-1",
            request=_request(),
            format="md",
            db=db,
            db_log=SimpleNamespace(),
            user=_user("viewer-1"),
        )

    assert exc.value.status_code == 404


def test_list_notes_redacts_owner_share_tokens_for_subscribers(monkeypatch):
    owner_note = _note(id="owned-note", user_id="viewer-1")
    shared_note = _note(id="shared-note")
    subscription = SimpleNamespace(share_type="live")

    monkeypatch.setattr(notes_router, "ensure_notes_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(notes_router, "list_user_notes", lambda *args, **kwargs: [owner_note])
    monkeypatch.setattr(notes_router, "get_subscribed_notes", lambda *args, **kwargs: [(shared_note, subscription)])
    monkeypatch.setattr(notes_router, "get_note_subscriber_count", lambda *args, **kwargs: 2)
    monkeypatch.setattr(
        notes_router,
        "get_user",
        lambda db, user_id: SimpleNamespace(first_name="Owner", last_name="User"),
    )
    response = notes_router.list_notes_route(db=SimpleNamespace(), user=_user("viewer-1"))

    items = {item.id: item for item in response.items}
    owned_payload = items["owned-note"].model_dump()
    shared_payload = items["shared-note"].model_dump()

    assert owned_payload["clone_share_id"] == "clone-token"
    assert owned_payload["live_share_id"] == "live-token"
    assert owned_payload["collaborate_share_id"] == "collab-token"
    assert "can_edit" not in owned_payload

    assert shared_payload["is_subscribed"] is True
    assert shared_payload["share_type"] == "live"
    assert "can_edit" not in shared_payload
    assert shared_payload["owner_name"] == "Owner User"
    assert "clone_share_id" not in shared_payload
    assert "live_share_id" not in shared_payload
    assert "collaborate_share_id" not in shared_payload
    assert shared_payload["user_id"] is None


def test_edit_note_redacts_share_tokens_for_collaborators(monkeypatch):
    note = _note()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = note
    subscription = SimpleNamespace(share_type="collaborate")
    expected_updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    captured_edit = {}

    monkeypatch.setattr(notes_router, "ensure_notes_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(notes_router, "can_user_edit_note", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        notes_router,
        "edit_user_note",
        lambda **kwargs: captured_edit.update(kwargs) or note,
    )
    monkeypatch.setattr(notes_router, "get_subscription_for_note", lambda *args, **kwargs: subscription)
    monkeypatch.setattr(
        notes_router,
        "get_user",
        lambda db, user_id: SimpleNamespace(first_name="Owner", last_name="User"),
    )
    monkeypatch.setattr(notes_router, "create_audit_log", lambda *args, **kwargs: None)
    response = notes_router.edit_note_route(
        note_id="note-1",
        payload=NoteUpdate(
            content="# Updated note\nBody",
            expected_updated_at=expected_updated_at,
        ),
        request=_request(),
        db=db,
        db_log=SimpleNamespace(),
        user=_user("viewer-1"),
    )

    payload = response.model_dump()

    assert captured_edit["expected_updated_at"] == expected_updated_at
    assert payload["is_subscribed"] is True
    assert payload["share_type"] == "collaborate"
    assert "can_edit" not in payload
    assert payload["owner_name"] == "Owner User"
    assert "clone_share_id" not in payload
    assert "live_share_id" not in payload
    assert "collaborate_share_id" not in payload
    assert payload["user_id"] is None
