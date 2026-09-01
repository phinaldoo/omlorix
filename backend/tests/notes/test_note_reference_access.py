from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base  # noqa: E402
from app.notes import file_references  # noqa: E402
from app.notes.models import (  # noqa: E402
    NoteHistory,
    Notes,
    SharedNoteSubscription,
    create_user_note,
    edit_user_note,
)
from app.tools.notes.utils import edit_note_tool  # noqa: E402


PRIVATE_TOKEN = "{{note:file:owner-1:file-private|Owner plan.pdf}}"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Notes.__table__,
            SharedNoteSubscription.__table__,
            NoteHistory.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_shared_note(db_session, content: str = f"# Shared\n\n{PRIVATE_TOKEN}\n\nOriginal text") -> Notes:
    now = datetime.now(timezone.utc)
    note = Notes(
        id="note-1",
        user_id="owner-1",
        content=content,
        collaborate_share_id="share-1",
        created_at=now,
        updated_at=now,
    )
    db_session.add(note)
    db_session.add(
        SharedNoteSubscription(
            id="subscription-1",
            note_id=note.id,
            subscriber_id="collaborator-1",
            share_type="collaborate",
            subscribed_at=now,
        )
    )
    db_session.commit()
    return note


def test_collaborator_can_edit_text_while_preserving_inaccessible_reference(db_session, monkeypatch):
    note = _seed_shared_note(db_session)
    monkeypatch.setattr(file_references, "get_accessible_file", lambda *_args, **_kwargs: None)

    updated = edit_user_note(
        db_session,
        "collaborator-1",
        note.id,
        f"# Shared\n\n{PRIVATE_TOKEN}\n\nEdited unrelated text",
        expected_updated_at=note.updated_at,
    )

    assert updated.content.endswith("Edited unrelated text")
    assert PRIVATE_TOKEN in updated.content


def test_collaborator_can_remove_or_relabel_inaccessible_reference(db_session, monkeypatch):
    note = _seed_shared_note(db_session)
    monkeypatch.setattr(file_references, "get_accessible_file", lambda *_args, **_kwargs: None)

    relabeled = edit_user_note(
        db_session,
        "collaborator-1",
        note.id,
        note.content.replace("Owner plan.pdf", "Renamed label"),
        expected_updated_at=note.updated_at,
    )
    removed = edit_user_note(
        db_session,
        "collaborator-1",
        note.id,
        relabeled.content.replace("{{note:file:owner-1:file-private|Renamed label}}", ""),
        expected_updated_at=relabeled.updated_at,
    )

    assert "file-private" not in removed.content


@pytest.mark.parametrize(
    "next_content",
    [
        f"# Shared\n\n{PRIVATE_TOKEN}\n{PRIVATE_TOKEN}\n\nOriginal text",
        "# Shared\n\n{{note:file:owner-1:file-other|Other.pdf}}\n\nOriginal text",
    ],
)
def test_new_or_duplicated_inaccessible_reference_is_rejected_with_exact_detail(
    db_session,
    monkeypatch,
    next_content,
):
    note = _seed_shared_note(db_session)
    monkeypatch.setattr(file_references, "get_accessible_file", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        edit_user_note(
            db_session,
            "collaborator-1",
            note.id,
            next_content,
            expected_updated_at=note.updated_at,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "note_file_reference_unavailable"
    assert exc_info.value.detail["reference"]["raw_token"] in next_content
    assert exc_info.value.detail["reference"]["file_id"] in {"file-private", "file-other"}
    assert exc_info.value.detail["owner_action"] == "share_containing_folder_replace_or_remove"


def test_collaborator_can_add_an_accessible_reference(db_session, monkeypatch):
    note = _seed_shared_note(db_session, content="# Shared\n\nOriginal text")
    monkeypatch.setattr(
        file_references,
        "get_accessible_file",
        lambda _db, _user_id, file_id: SimpleNamespace(id=file_id, user_id="owner-1"),
    )

    updated = edit_user_note(
        db_session,
        "collaborator-1",
        note.id,
        f"# Shared\n\n{PRIVATE_TOKEN}\n\nOriginal text",
        expected_updated_at=note.updated_at,
    )

    assert PRIVATE_TOKEN in updated.content


def test_new_note_validates_all_references(db_session, monkeypatch):
    monkeypatch.setattr(file_references, "get_accessible_file", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        create_user_note(db_session, "collaborator-1", PRIVATE_TOKEN)

    assert exc_info.value.detail["code"] == "note_file_reference_unavailable"
    assert db_session.query(Notes).count() == 0


def test_notes_tool_partial_edit_preserves_inaccessible_reference(db_session, monkeypatch):
    note = _seed_shared_note(db_session)
    monkeypatch.setattr(file_references, "get_accessible_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.tools.notes.utils.stage_tool_audit_action",
        lambda *_args, **_kwargs: None,
    )

    result = edit_note_tool(
        db_session,
        "collaborator-1",
        note.id,
        "Updated text",
        start_snippet="Original text",
        end_snippet="Original text",
        expected_updated_at=note.updated_at.isoformat(),
    )

    assert result["content"].endswith("Updated text")
    assert PRIVATE_TOKEN in result["content"]
