from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.llm.system_instruction.notes import (
    MAX_ATTACHED_NOTE_CHARS,
    MAX_ATTACHED_NOTES,
    MAX_ATTACHED_NOTES_TOTAL_CHARS,
    fetch_notes_for_chat,
)
from app.notes.models import Notes, SharedNoteSubscription


def test_attached_notes_use_two_queries_and_one_shared_context_budget():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[Notes.__table__, SharedNoteSubscription.__table__],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    note_ids = []
    for index in range(MAX_ATTACHED_NOTES + 5):
        note_id = f"note-{index}"
        note_ids.append(note_id)
        db.add(
            Notes(
                id=note_id,
                user_id="user-1",
                content=str(index) + ("x" * 49_999),
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()

    select_count = 0

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        notes = fetch_notes_for_chat(db, "user-1", note_ids)
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert select_count == 2
    assert len(notes) <= MAX_ATTACHED_NOTES
    assert all(len(note["content"]) <= MAX_ATTACHED_NOTE_CHARS for note in notes)
    assert sum(len(note["content"]) for note in notes) <= MAX_ATTACHED_NOTES_TOTAL_CHARS
    assert any(note["truncated"] for note in notes)
