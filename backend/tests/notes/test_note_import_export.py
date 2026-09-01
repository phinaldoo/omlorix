import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base
from app.notes.models import (
    NoteHistory,
    Notes,
    SharedNoteSubscription,
    current_notes_export_version,
    export_user_notes,
    import_user_notes,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
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


def test_export_user_notes_includes_history_and_sharing_metadata(db_session):
    created_at = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 5, 2, 10, 30, tzinfo=timezone.utc)
    note = Notes(
        id="note-1",
        user_id="user-1",
        content="Roadmap draft",
        clone_share_id="clone-share-1",
        live_share_id="live-share-1",
        collaborate_share_id="collab-share-1",
        created_at=created_at,
        updated_at=updated_at,
    )
    db_session.add(note)
    db_session.add(
        SharedNoteSubscription(
            id="sub-1",
            note_id=note.id,
            subscriber_id="user-2",
            share_type="collaborate",
            subscribed_at=updated_at,
        )
    )
    db_session.add(
        NoteHistory(
            id="history-1",
            note_id=note.id,
            user_id="user-2",
            actor_type="user",
            content="Roadmap draft v2",
            previous_content="Roadmap draft",
            change_summary="+3 chars",
            version_number="2",
            created_at=updated_at,
        )
    )
    db_session.commit()

    payload = export_user_notes(db_session, "user-1")

    assert payload["export_type"] == "notes"
    assert payload["export_version"] == current_notes_export_version
    assert payload["data"]["metadata_policy"]["history_scope"] == "owned_note_history"
    assert (
        payload["data"]["metadata_policy"]["subscriptions"]
        == "export_owned_note_subscriptions_for_reference_only"
    )
    assert len(payload["data"]["notes"]) == 1

    note_payload = payload["data"]["notes"][0]
    assert note_payload["id"] == note.id
    assert note_payload["sharing"] == {
        "clone_share_id": "clone-share-1",
        "live_share_id": "live-share-1",
        "collaborate_share_id": "collab-share-1",
        "subscriptions": [
            {
                "id": "sub-1",
                "subscriber_id": "user-2",
                "share_type": "collaborate",
                "subscribed_at": updated_at.isoformat(),
            }
        ],
    }
    assert note_payload["history"] == [
        {
            "id": "history-1",
            "user_id": "user-2",
            "actor_type": "user",
            "content": "Roadmap draft v2",
            "previous_content": "Roadmap draft",
            "change_summary": "+3 chars",
            "version_number": "2",
            "created_at": updated_at.isoformat(),
        }
    ]


def test_import_user_notes_restores_related_metadata_for_remapped_note_ids(db_session):
    db_session.add(
        Notes(
            id="note-1",
            user_id="someone-else",
            content="Existing conflicting note",
            created_at=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    payload = {
        "export_type": "notes",
        "export_version": current_notes_export_version,
        "data": {
            "user_id": "user-1",
            "metadata_policy": {
                "history_scope": "owned_note_history",
                "share_ids": "preserve_existing_share_ids_when_available",
                "subscriptions": "export_owned_note_subscriptions_for_reference_only",
            },
            "notes": [
                {
                    "id": "note-1",
                    "content": "Imported note",
                    "created_at": "2026-05-01T09:00:00+00:00",
                    "updated_at": "2026-05-02T09:00:00+00:00",
                    "sharing": {
                        "clone_share_id": "clone-share-1",
                        "live_share_id": "live-share-1",
                        "collaborate_share_id": "collab-share-1",
                        "subscriptions": [
                            {
                                "subscriber_id": "user-2",
                                "share_type": "live",
                                "subscribed_at": "2026-05-02T10:00:00+00:00",
                            }
                        ],
                    },
                    "history": [
                        {
                            "id": "history-1",
                            "user_id": "user-3",
                            "actor_type": "assistant",
                            "content": "Imported note",
                            "previous_content": "Imported",
                            "change_summary": "+5 chars",
                            "version_number": "4",
                            "created_at": "2026-05-02T11:00:00+00:00",
                        }
                    ],
                }
            ],
        },
    }

    result = import_user_notes(db_session, "user-1", payload, restore_sharing_metadata=True)

    assert result["errors"] == []
    assert len(result["created"]) == 1
    assert result["created"][0]["source_id"] == "note-1"
    imported_note_id = result["created"][0]["id"]
    assert imported_note_id != "note-1"
    assert any("Note ID already exists" in warning["warning"] for warning in result["warnings"])

    imported_note = db_session.query(Notes).filter(Notes.id == imported_note_id).first()
    assert imported_note is not None
    assert imported_note.user_id == "user-1"
    assert imported_note.live_share_id == "live-share-1"
    assert imported_note.collaborate_share_id == "collab-share-1"

    imported_history = db_session.query(NoteHistory).filter(NoteHistory.note_id == imported_note_id).all()
    assert len(imported_history) == 1
    assert imported_history[0].user_id == "user-3"
    assert imported_history[0].version_number == "4"

    imported_subscriptions = (
        db_session.query(SharedNoteSubscription)
        .filter(SharedNoteSubscription.note_id == imported_note_id)
        .all()
    )
    assert imported_subscriptions == []
    assert any("subscriptions were skipped" in warning["warning"] for warning in result["warnings"])


def test_account_restore_skips_note_ids_already_owned_by_destination(db_session):
    """Replaying a canonical account archive must not duplicate its notes."""
    now = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    db_session.add(
        Notes(
            id="owned-note",
            user_id="user-1",
            content="Already restored",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()
    payload = {
        "export_type": "notes",
        "export_version": current_notes_export_version,
        "data": {
            "user_id": "user-1",
            "notes": [{"id": "owned-note", "content": "Already restored"}],
        },
    }

    result = import_user_notes(
        db_session,
        "user-1",
        payload,
        skip_existing_owned=True,
    )

    assert result["created"] == []
    assert result["skipped"] == [
        {
            "id": "owned-note",
            "source_id": "owned-note",
            "reason": "already_exists",
        }
    ]
    assert db_session.query(Notes).filter(Notes.user_id == "user-1").count() == 1

def test_import_user_notes_skips_sharing_metadata_by_default(db_session):
    payload = {
        "export_type": "notes",
        "export_version": current_notes_export_version,
        "data": {
            "user_id": "user-1",
            "notes": [
                {
                    "id": "note-with-sharing",
                    "content": "Imported private copy",
                    "sharing": {
                        "live_share_id": "live-share-1",
                        "subscriptions": [
                            {
                                "subscriber_id": "victim-user-id",
                                "share_type": "live",
                            }
                        ],
                    },
                }
            ],
        },
    }

    result = import_user_notes(db_session, "user-1", payload)

    assert result["errors"] == []
    assert result["created"] == [
        {"id": "note-with-sharing", "source_id": "note-with-sharing"}
    ]
    assert any("Sharing metadata was skipped" in warning["warning"] for warning in result["warnings"])

    imported_note = db_session.query(Notes).filter(Notes.id == "note-with-sharing").first()
    assert imported_note is not None
    assert imported_note.live_share_id is None
    assert (
        db_session.query(SharedNoteSubscription)
        .filter(SharedNoteSubscription.note_id == "note-with-sharing")
        .count()
        == 0
    )
