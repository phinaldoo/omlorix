from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base  # noqa: E402
from app.notes.models import Notes, list_user_notes  # noqa: E402
from app.notes.router import _build_note_list_item, _note_sort_key  # noqa: E402


def test_note_list_contract_exposes_titles_and_snippets_for_owned_and_subscribed_notes():
    note = SimpleNamespace(
        id="note-1",
        user_id="owner-1",
        content="# E2E Markdown\n\n- First item\n\nSecond paragraph",
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        clone_share_id=None,
        live_share_id="share-1",
        collaborate_share_id=None,
    )

    owned = _build_note_list_item(note, viewer_user_id="owner-1")
    subscribed = _build_note_list_item(
        note,
        viewer_user_id="subscriber-1",
        owner_name="Owner",
        share_type="live",
    )

    for item in (owned, subscribed):
        payload = item.model_dump()
        assert payload["title"] == "E2E Markdown"
        assert payload["snippet"] == "First item Second paragraph"
        assert "content" not in payload

    assert owned.is_subscribed is False
    assert subscribed.is_subscribed is True


def test_note_sort_key_normalizes_naive_timestamps_to_utc():
    timestamp, note_id = _note_sort_key(
        SimpleNamespace(
            id="note-1",
            updated_at=datetime(2026, 8, 11, 12, 0),
            created_at=None,
        )
    )

    assert timestamp.tzinfo is timezone.utc
    assert note_id == "note-1"


def test_owned_note_search_is_applied_before_the_page_window():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
    Base.metadata.create_all(bind=engine, tables=[Notes.__table__])
    db = sessionmaker(bind=engine)()
    try:
        for index in range(60):
            marker = "needle" if index == 59 else "ordinary"
            db.add(Notes(id=index + 1, user_id="user-1", content=f"# Note {index}\n{marker}"))
        db.commit()

        first_page = list_user_notes(db, "user-1", limit=20, offset=0)
        matching_page = list_user_notes(db, "user-1", limit=20, offset=0, query_text="needle")

        assert len(first_page) == 20
        assert len(matching_page) == 1
        assert "needle" in matching_page[0].content
    finally:
        db.close()


def test_note_search_treats_sql_wildcards_as_literal_text():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
    Base.metadata.create_all(bind=engine, tables=[Notes.__table__])
    db = sessionmaker(bind=engine)()
    try:
        db.add_all([
            Notes(id=1, user_id="user-1", content="# Percent\n100% complete"),
            Notes(id=2, user_id="user-1", content="# Plain\ncomplete"),
        ])
        db.commit()

        matches = list_user_notes(db, "user-1", query_text="100%")

        assert [note.content for note in matches] == ["# Percent\n100% complete"]
    finally:
        db.close()
