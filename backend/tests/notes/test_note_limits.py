import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError
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

from app.database import Base
from app.notes import models as note_models
from app.notes.limits import MAX_NOTE_CONTENT_LENGTH
from app.notes.models import Notes, create_user_note, edit_user_note
from app.notes.schemas import NoteCreate, NoteUpdate


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
    Base.metadata.create_all(bind=engine, tables=[Notes.__table__])
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_note_payload_schemas_reject_oversized_content():
    oversized_content = "x" * (MAX_NOTE_CONTENT_LENGTH + 1)

    with pytest.raises(ValidationError):
        NoteCreate(content=oversized_content)

    with pytest.raises(ValidationError):
        NoteUpdate(content=oversized_content)


def test_create_user_note_rejects_oversized_content(db_session):
    with pytest.raises(HTTPException) as exc_info:
        create_user_note(db_session, "user-1", "x" * (MAX_NOTE_CONTENT_LENGTH + 1))

    assert exc_info.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE
    assert db_session.query(Notes).count() == 0


def test_create_user_note_enforces_count_and_storage_quota(db_session, monkeypatch):
    monkeypatch.setattr(note_models, "MAX_NOTES_PER_USER", 2)
    monkeypatch.setattr(note_models, "MAX_NOTE_STORAGE_CHARS_PER_USER", 10)

    create_user_note(db_session, "user-1", "12345")
    create_user_note(db_session, "user-1", "67890")

    with pytest.raises(HTTPException) as count_exc:
        create_user_note(db_session, "user-1", "x")

    assert count_exc.value.status_code == status.HTTP_409_CONFLICT

    note = db_session.query(Notes).first()
    with pytest.raises(HTTPException) as quota_exc:
        edit_user_note(
            db_session,
            "user-1",
            note.id,
            "123456",
            expected_updated_at=note.updated_at,
        )

    assert quota_exc.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE
