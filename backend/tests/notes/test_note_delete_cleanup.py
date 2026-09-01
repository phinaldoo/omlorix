from __future__ import annotations

from datetime import datetime, timezone
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.database import Base  # noqa: E402
from app.notes.models import (  # noqa: E402
    NoteHistory,
    Notes,
    SharedNoteSubscription,
    delete_user_note,
)


def _session():
    """Create the minimal isolated database needed for note deletion tests."""
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


def test_delete_user_note_permanently_removes_note_history_and_subscriptions():
    """A user deletion must leave no recoverable note-specific database state."""
    db = _session()
    now = datetime.now(timezone.utc)
    note = Notes(
        id="note-1",
        user_id="user-1",
        content="secret note content",
        created_at=now,
        updated_at=now,
    )
    db.add(note)
    db.add(
        SharedNoteSubscription(
            id="subscription-1",
            note_id=note.id,
            subscriber_id="user-2",
            share_type="live",
            subscribed_at=now,
        )
    )
    db.add(
        NoteHistory(
            id="history-1",
            note_id=note.id,
            user_id=note.user_id,
            content=note.content,
            previous_content="older content",
            version_number="1",
            created_at=now,
        )
    )
    db.commit()
    note_id = note.id

    result = delete_user_note(
        db,
        note.user_id,
        note_id,
        expected_updated_at=note.updated_at,
    )

    assert result == {"deleted": True, "note_id": note_id}
    assert db.query(Notes).count() == 0
    assert db.query(NoteHistory).count() == 0
    assert db.query(SharedNoteSubscription).count() == 0
