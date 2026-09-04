import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chats.utils import _build_notes_user_edit_context
from app.database import Base
from app.notes.models import NoteHistory, Notes, SharedNoteSubscription


def test_note_update_context_reads_nested_tool_receipts_and_uses_latest_version():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Notes.__table__,
            SharedNoteSubscription.__table__,
            NoteHistory.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    assistant_at = now - timedelta(minutes=10)
    note = Notes(
        id="note-1",
        user_id="user-1",
        content="# Current title\nUpdated by the user",
        created_at=now - timedelta(days=1),
        updated_at=now,
    )
    db.add(note)
    db.add_all(
        [
            NoteHistory(
                id="history-1",
                note_id=note.id,
                user_id="user-1",
                actor_type="assistant",
                content="older",
                previous_content=None,
                version_number="1",
                created_at=now - timedelta(minutes=5),
            ),
            NoteHistory(
                id="history-2",
                note_id=note.id,
                user_id="user-1",
                actor_type="user",
                content=note.content,
                previous_content="older",
                version_number="2",
                created_at=now - timedelta(minutes=1),
            ),
        ]
    )
    db.commit()
    chat_history = [
        {
            "role": "assistant",
            "created_at": assistant_at.isoformat(),
            "content": [
                {
                    "type": "tool_call_result",
                    "tool_name": "notes()",
                    "content": json.dumps(
                        {
                            "operation": "edit",
                            "note": {"id": note.id, "updated_at": now.isoformat()},
                        }
                    ),
                }
            ],
        }
    ]

    context = _build_notes_user_edit_context(
        db,
        user_id="user-1",
        chat_history=chat_history,
    )

    assert context is not None
    assert "note_id=note-1" in context
    assert "version=2" in context
    assert "Updated by the user" not in context
