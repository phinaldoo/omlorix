from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))



from app.database import Base  # noqa: E402
from app.notes import models as note_models  # noqa: E402
from app.notes.models import (  # noqa: E402
    NoteHistory,
    Notes,
    SharedNoteSubscription,
    edit_user_note,
)
from app.tools.notes.utils import notes_tool  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Notes.__table__,
            SharedNoteSubscription.__table__,
            NoteHistory.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def _seed_shared_note(db):
    now = datetime.now(timezone.utc)
    note = Notes(
        id="note-1",
        user_id="owner-1",
        content="Original content",
        collaborate_share_id="share-1",
        created_at=now,
        updated_at=now,
    )
    subscription = SharedNoteSubscription(
        id="subscription-1",
        note_id=note.id,
        subscriber_id="collaborator-1",
        share_type="collaborate",
        subscribed_at=now,
    )
    db.add(note)
    db.add(subscription)
    db.commit()
    return note


def test_edit_user_note_allows_collaborator_with_edit_permission():
    db = _session()
    _seed_shared_note(db)

    note = edit_user_note(
        db=db,
        user_id="collaborator-1",
        note_id="note-1",
        content="Updated by collaborator",
        expected_updated_at=db.query(Notes).filter(Notes.id == "note-1").one().updated_at,
    )

    history_entries = db.query(NoteHistory).filter(NoteHistory.note_id == "note-1").all()

    assert note.content == "Updated by collaborator"
    assert len(history_entries) == 1
    assert history_entries[0].user_id == "collaborator-1"
    assert history_entries[0].actor_type == "user"


def test_notes_tool_edit_allows_collaborator_and_records_assistant_history(monkeypatch):
    db = _session()
    _seed_shared_note(db)
    audit_calls = []
    monkeypatch.setattr(
        "app.tools.notes.utils.stage_tool_audit_action",
        lambda db, user_id, action, **kwargs: audit_calls.append(
            {"db": db, "user_id": user_id, "action": action, **kwargs}
        ),
    )

    result = notes_tool(
        db=db,
        user_id="collaborator-1",
        type="edit",
        note_id="note-1",
        content="Updated by tool",
        expected_updated_at=db.query(Notes).filter(Notes.id == "note-1").one().updated_at.isoformat(),
    )

    history_entries = db.query(NoteHistory).filter(NoteHistory.note_id == "note-1").all()

    assert result["note"]["content"] == "Updated by tool"
    assert len(history_entries) == 1
    assert history_entries[0].user_id == "collaborator-1"
    assert history_entries[0].actor_type == "assistant"
    assert audit_calls == [
        {
            "db": db,
            "user_id": "collaborator-1",
            "action": "NOTE_UPDATED",
            "category": "notes",
            "details": {"note_id": "note-1", "is_collaborator": True},
        }
    ]
    assert "Updated by tool" not in repr(audit_calls)


def test_notes_tool_create_audits_without_note_content(monkeypatch):
    db = _session()
    audit_calls = []
    monkeypatch.setattr(
        "app.tools.notes.utils.stage_tool_audit_action",
        lambda db, user_id, action, **kwargs: audit_calls.append(
            {"db": db, "user_id": user_id, "action": action, **kwargs}
        ),
    )

    result = notes_tool(
        db=db,
        user_id="owner-1",
        type="create",
        content="Private note content",
    )

    assert result["note"]["content"] == "Private note content"
    assert audit_calls == [
        {
            "db": db,
            "user_id": "owner-1",
            "action": "NOTE_CREATED",
            "category": "notes",
            "details": {
                "note_id": result["note"]["id"],
                "is_collaborator": False,
            },
        }
    ]
    assert "Private note content" not in repr(audit_calls)


def test_notes_tool_view_returns_accessible_note_content():
    db = _session()
    _seed_shared_note(db)

    result = notes_tool(db=db, user_id="collaborator-1", type="view", note_id="note-1")

    assert result["note"]["id"] == "note-1"
    assert result["note"]["content"] == "Original content"
    assert result["note"]["is_subscribed"] is True
    assert result["note"]["share_type"] == "collaborate"


def test_notes_tool_partial_edit_replaces_exact_snippet_range(monkeypatch):
    db = _session()
    note = _seed_shared_note(db)
    note.content = "Alpha\nBeta old\nGamma old\nDelta"
    db.commit()
    monkeypatch.setattr(
        "app.tools.notes.utils.stage_tool_audit_action",
        lambda *_args, **_kwargs: None,
    )

    result = notes_tool(
        db=db,
        user_id="collaborator-1",
        type="edit",
        note_id="note-1",
        start_snippet="Beta old",
        end_snippet="Gamma old",
        content="Beta new\nGamma new",
        expected_updated_at=note.updated_at.isoformat(),
    )

    assert result["note"]["content"] == "Alpha\nBeta new\nGamma new\nDelta"


def test_notes_tool_batches_multiple_edits_into_one_saved_version(monkeypatch):
    db = _session()
    note = _seed_shared_note(db)
    note.content = "# Plan\nAlpha old\nMiddle\nOmega old"
    db.commit()
    monkeypatch.setattr(
        "app.tools.notes.utils.stage_tool_audit_action",
        lambda *_args, **_kwargs: None,
    )

    result = notes_tool(
        db=db,
        user_id="collaborator-1",
        type="edit",
        note_id="note-1",
        edits=[
            {
                "start_snippet": "Alpha old",
                "end_snippet": "Alpha old",
                "content": "Alpha new",
            },
            {
                "start_snippet": "Omega old",
                "end_snippet": "Omega old",
                "content": "Omega new",
            },
        ],
        expected_updated_at=note.updated_at.isoformat(),
    )

    assert result["note"]["content"] == "# Plan\nAlpha new\nMiddle\nOmega new"
    assert result["note"]["edit_count"] == 2
    assert db.query(NoteHistory).filter(NoteHistory.note_id == "note-1").count() == 1


def test_notes_tool_view_is_bounded_by_default():
    db = _session()
    note = _seed_shared_note(db)
    note.content = "x" * 50_000
    db.commit()

    result = notes_tool(
        db=db,
        user_id="collaborator-1",
        type="view",
        note_id="note-1",
    )["note"]

    assert len(result["content"]) == 20_000
    assert result["selection"]["total_chars"] == 50_000
    assert result["truncated"] is True


def test_notes_tool_partial_edit_rejects_ambiguous_start_snippet():
    db = _session()
    note = _seed_shared_note(db)
    note.content = "Repeat\nMiddle\nRepeat"
    db.commit()

    with pytest.raises(ValueError, match="start_snippet matched more than once"):
        notes_tool(
            db=db,
            user_id="collaborator-1",
            type="edit",
            note_id="note-1",
            start_snippet="Repeat",
            end_snippet="Middle",
            content="Replacement",
            expected_updated_at=note.updated_at.isoformat(),
        )


def test_notes_tool_partial_edit_rejects_ambiguous_end_snippet():
    db = _session()
    note = _seed_shared_note(db)
    note.content = "Alpha\nEnd\nMiddle\nEnd\nOmega"
    db.commit()

    with pytest.raises(ValueError, match="end_snippet matched more than once after start_snippet"):
        notes_tool(
            db=db,
            user_id="collaborator-1",
            type="edit",
            note_id="note-1",
            start_snippet="Alpha",
            end_snippet="End",
            content="Replacement",
            expected_updated_at=note.updated_at.isoformat(),
        )


def test_edit_user_note_rejects_stale_expected_updated_at():
    db = _session()
    note = _seed_shared_note(db)
    expected_updated_at = note.updated_at
    note.content = "Concurrent collaborator update"
    note.updated_at = expected_updated_at + timedelta(seconds=1)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        edit_user_note(
            db=db,
            user_id="collaborator-1",
            note_id="note-1",
            content="Tool update from stale content",
            actor_type="assistant",
            expected_updated_at=expected_updated_at,
        )

    assert exc_info.value.status_code == 409


def test_edit_user_note_rejects_concurrent_write_after_revision_check(monkeypatch):
    db = _session()
    note = _seed_shared_note(db)
    observed_updated_at = note.updated_at

    def simulate_concurrent_write(session, user_id, content, existing_note=None):
        """Change the stored revision after edit_user_note captured its snapshot."""
        del user_id, content, existing_note
        session.query(Notes).filter(Notes.id == "note-1").update(
            {
                Notes.content: "Concurrent write",
                Notes.updated_at: observed_updated_at + timedelta(seconds=1),
            },
            synchronize_session=False,
        )
        session.flush()

    monkeypatch.setattr(note_models, "_ensure_user_note_quota", simulate_concurrent_write)

    with pytest.raises(HTTPException) as exc_info:
        edit_user_note(
            db=db,
            user_id="collaborator-1",
            note_id="note-1",
            content="Stale full-note save",
            expected_updated_at=observed_updated_at,
        )

    assert exc_info.value.status_code == 409


def test_notes_tool_full_replacement_rejects_revision_from_stale_view():
    db = _session()
    note = _seed_shared_note(db)
    stale_revision = note.updated_at.isoformat()
    note.content = "Concurrent collaborator update"
    note.updated_at = note.updated_at + timedelta(seconds=1)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        notes_tool(
            db=db,
            user_id="collaborator-1",
            type="edit",
            note_id="note-1",
            content="Replacement based on stale view",
            expected_updated_at=stale_revision,
        )

    assert exc_info.value.status_code == 409
    assert db.query(Notes).one().content == "Concurrent collaborator update"


def test_notes_tool_edit_requires_a_view_revision():
    db = _session()
    _seed_shared_note(db)

    with pytest.raises(ValueError, match="expected_updated_at is required"):
        notes_tool(
            db=db,
            user_id="collaborator-1",
            type="edit",
            note_id="note-1",
            content="Unsafe replacement",
        )


def test_notes_tool_rejects_delete_operations_without_mutating_the_note():
    db = _session()
    _seed_shared_note(db)

    with pytest.raises(
        ValueError,
        match="Allowed values are: list, view, view_many, create, edit",
    ):
        notes_tool(
            db=db,
            user_id="owner-1",
            type="delete",
            note_id="note-1",
        )
    assert db.query(Notes).one().content == "Original content"


def test_notes_tool_list_includes_subscribed_collaborate_notes():
    db = _session()
    _seed_shared_note(db)

    result = notes_tool(db=db, user_id="collaborator-1", type="list")
    note = result["notes"][0]

    assert len(result["notes"]) == 1
    assert note["id"] == "note-1"
    assert note["title"] == "Original content"
    assert note["content_length"] == len("Original content")
    assert "user_id" not in note
    assert "content" not in note
    assert "collaborate_share_id" not in note
    assert note["is_subscribed"] is True
    assert note["share_type"] == "collaborate"
    assert note["can_edit"] is True
