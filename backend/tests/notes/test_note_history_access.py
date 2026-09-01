from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "app.files.utils" not in sys.modules:
    fake_files_utils = ModuleType("app.files.utils")
    fake_files_utils.materialize_file_record = lambda *args, **kwargs: None
    fake_files_utils.persist_generated_file_path = lambda *args, **kwargs: None
    sys.modules["app.files.utils"] = fake_files_utils

if "app.settings.utils" not in sys.modules:
    fake_settings_utils = ModuleType("app.settings.utils")
    fake_settings_utils.get_public_url = lambda db: "https://omlorix.test"
    sys.modules["app.settings.utils"] = fake_settings_utils

from app.database import Base  # noqa: E402
from app.notes.models import (  # noqa: E402
    NoteHistory,
    Notes,
    SharedNoteSubscription,
    can_user_view_history,
    get_note_history,
    get_visible_history_entry,
    restore_note_from_history,
)


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
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _seed_note_with_history(db_session):
    created_at = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    subscribed_at = created_at + timedelta(hours=2)
    note = Notes(
        id="note-1",
        user_id="owner-1",
        content="Sanitized shared content",
        live_share_id="live-share-1",
        collaborate_share_id="collab-share-1",
        created_at=created_at,
        updated_at=subscribed_at,
    )
    db_session.add(note)
    db_session.add_all(
        [
            NoteHistory(
                id="private-history",
                note_id=note.id,
                user_id="owner-1",
                actor_type="user",
                content="Sanitized shared content",
                previous_content="SECRET_PRE_SHARE_TOKEN=alpha-should-not-be-shared",
                change_summary="removed sensitive text",
                version_number="1",
                created_at=created_at + timedelta(hours=1),
            ),
            NoteHistory(
                id="post-share-history",
                note_id=note.id,
                user_id="owner-1",
                actor_type="user",
                content="Sanitized shared content plus public edit",
                previous_content="Sanitized shared content",
                change_summary="public edit",
                version_number="2",
                created_at=subscribed_at + timedelta(minutes=5),
            ),
        ]
    )
    db_session.add_all(
        [
            SharedNoteSubscription(
                id="live-subscription",
                note_id=note.id,
                subscriber_id="readonly-subscriber",
                share_type="live",
                subscribed_at=subscribed_at,
            ),
            SharedNoteSubscription(
                id="collab-subscription",
                note_id=note.id,
                subscriber_id="collaborator-1",
                share_type="collaborate",
                subscribed_at=subscribed_at,
            ),
        ]
    )
    db_session.commit()
    return note


def test_read_only_shared_note_subscriber_cannot_view_history(db_session):
    _seed_note_with_history(db_session)

    assert can_user_view_history(db_session, "readonly-subscriber", "note-1") is False

    with pytest.raises(HTTPException) as exc_info:
        get_note_history(db_session, "note-1", "readonly-subscriber")

    assert exc_info.value.status_code == 403


def test_collaborator_history_is_limited_to_versions_after_subscription(db_session):
    _seed_note_with_history(db_session)

    assert can_user_view_history(db_session, "collaborator-1", "note-1") is True

    history = get_note_history(db_session, "note-1", "collaborator-1")

    assert history["total_count"] == 1
    assert [entry["id"] for entry in history["entries"]] == ["post-share-history"]
    assert "SECRET_PRE_SHARE_TOKEN" not in history["entries"][0]["previous_content"]
    assert get_visible_history_entry(
        db_session,
        "collaborator-1",
        "note-1",
        "private-history",
    ) is None


def test_collaborator_cannot_restore_hidden_pre_subscription_history(db_session):
    _seed_note_with_history(db_session)

    with pytest.raises(HTTPException) as exc_info:
        restore_note_from_history(
            db_session,
            "note-1",
            "private-history",
            "collaborator-1",
        )

    assert exc_info.value.status_code == 404


def test_history_restore_rejects_a_stale_note_revision(db_session):
    note = _seed_note_with_history(db_session)
    stale_revision = note.updated_at
    note.content = "Newer collaborator content"
    note.updated_at = stale_revision + timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        restore_note_from_history(
            db_session,
            note.id,
            "post-share-history",
            "collaborator-1",
            expected_updated_at=stale_revision,
        )

    assert exc_info.value.status_code == 409
    assert db_session.query(Notes).filter(Notes.id == note.id).one().content == "Newer collaborator content"


def test_owner_can_view_full_note_history(db_session):
    _seed_note_with_history(db_session)

    history = get_note_history(db_session, "note-1", "owner-1")

    assert history["total_count"] == 2
    assert [entry["id"] for entry in history["entries"]] == [
        "post-share-history",
        "private-history",
    ]


def test_owner_can_page_through_complete_history_without_duplicates(db_session):
    note = _seed_note_with_history(db_session)
    base_time = datetime(2026, 5, 2, 9, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            NoteHistory(
                id=f"paged-history-{index:03d}",
                note_id=note.id,
                user_id="owner-1",
                actor_type="user",
                content=f"content {index}",
                previous_content=f"content {index - 1}",
                change_summary="paged edit",
                version_number=str(index + 3),
                created_at=base_time + timedelta(minutes=index),
            )
            for index in range(55)
        ]
    )
    db_session.commit()

    first_page = get_note_history(db_session, "note-1", "owner-1", limit=50, offset=0)
    second_page = get_note_history(db_session, "note-1", "owner-1", limit=50, offset=50)
    ids = [entry["id"] for entry in first_page["entries"] + second_page["entries"]]

    assert first_page["has_more"] is True
    assert second_page["has_more"] is False
    assert first_page["total_count"] == 57
    assert len(ids) == 57
    assert len(set(ids)) == 57
