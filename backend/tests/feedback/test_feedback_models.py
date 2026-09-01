"""Focused tests for feedback persistence query construction."""

from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.chats.models import ChatMessages
from app.feedback import models as feedback_models


class _RecordedQuery:
    """Record selected columns while providing the query methods under test."""

    def __init__(self, columns):
        self.columns = columns

    def filter(self, *args):
        return self

    def group_by(self, *args):
        return self

    def order_by(self, *args):
        return self

    def scalar(self):
        return "model-1"

    def all(self):
        return []


class _RecordingDB:
    """Minimal session double that exposes the last constructed query."""

    def __init__(self):
        self.query_record = None

    def query(self, *columns):
        self.query_record = _RecordedQuery(columns)
        return self.query_record


class _PersistenceDB:
    """Record persistence calls made by the feedback upsert."""

    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = []

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.committed = True

    def refresh(self, value):
        self.refreshed.append(value)


def test_get_message_model_id_selects_only_the_model_id_column():
    db = _RecordingDB()

    result = feedback_models.get_message_model_id(db, "message-1")

    assert result == "model-1"
    assert db.query_record.columns == (ChatMessages.model_id,)


def test_upsert_feedback_creates_a_missing_record(monkeypatch):
    db = _PersistenceDB()
    monkeypatch.setattr(
        feedback_models,
        "get_feedback_by_message_and_user",
        lambda *args: None,
    )

    feedback = feedback_models.upsert_feedback(
        db,
        model_id="model-1",
        message_id="message-1",
        user_id="user-1",
        reaction="thumbs_up",
        comment="Useful",
    )

    assert db.added == [feedback]
    assert feedback.reaction == "thumbs_up"
    assert db.committed is True
    assert db.refreshed == [feedback]


def test_upsert_feedback_updates_an_existing_record(monkeypatch):
    db = _PersistenceDB()
    existing = SimpleNamespace(
        model_id="old-model",
        reaction="thumbs_down",
        comment=None,
    )
    monkeypatch.setattr(
        feedback_models,
        "get_feedback_by_message_and_user",
        lambda *args: existing,
    )

    feedback = feedback_models.upsert_feedback(
        db,
        model_id="model-1",
        message_id="message-1",
        user_id="user-1",
        reaction="thumbs_up",
        comment="Updated",
    )

    assert feedback is existing
    assert db.added == []
    assert feedback.model_id == "model-1"
    assert feedback.reaction == "thumbs_up"
    assert feedback.comment == "Updated"
    assert db.committed is True
    assert db.refreshed == [feedback]


def test_feedback_timeline_query_uses_daily_buckets():
    db = _RecordingDB()

    feedback_models.get_admin_feedback_timeline(
        db,
        days=30,
    )

    date_bucket = db.query_record.columns[0]
    compiled = str(
        date_bucket.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "CAST(model_feedback.created_at AS DATE)" in compiled
    assert "date_trunc" not in compiled
