from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.todos.models import (
    DEFAULT_TODO_SORT_ORDER,
    ShareType,
    SharedTodoListSubscription,
    TodoLists,
    subscribe_to_shared_todo_list,
)


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    TodoLists.__table__.create(engine)
    SharedTodoListSubscription.__table__.create(engine)
    return sessionmaker(bind=engine)


def _todo_list():
    now = datetime.now(timezone.utc)
    return TodoLists(
        id="list-1",
        user_id="owner-1",
        order=0,
        title="Shared list",
        description="",
        icon="list",
        sort_order=deepcopy(DEFAULT_TODO_SORT_ORDER),
        created_at=now,
        updated_at=now,
    )


def test_shared_todo_subscription_enforces_uniqueness_and_cascades_delete():
    Session = _session_factory()
    db = Session()
    todo_list = _todo_list()
    db.add(todo_list)
    db.commit()

    db.add(
        SharedTodoListSubscription(
            id="sub-1",
            todo_list_id=todo_list.id,
            subscriber_id="viewer-1",
            share_type="live",
            subscribed_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    db.add(
        SharedTodoListSubscription(
            id="sub-2",
            todo_list_id=todo_list.id,
            subscriber_id="viewer-1",
            share_type="live",
            subscribed_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.delete(todo_list)
    db.commit()

    assert db.query(SharedTodoListSubscription).count() == 0


def test_subscribe_to_shared_todo_list_recovers_from_unique_constraint_race(monkeypatch):
    Session = _session_factory()
    seed_session = Session()
    todo_list = _todo_list()
    seed_session.add(todo_list)
    seed_session.commit()
    todo_list_id = todo_list.id
    seed_session.close()

    db = Session()
    real_commit = db.commit
    commit_attempts = {"count": 0}

    def flaky_commit():
        if commit_attempts["count"] == 0:
            commit_attempts["count"] += 1
            competing_session = Session()
            try:
                competing_session.add(
                    SharedTodoListSubscription(
                        id="race-winner",
                        todo_list_id=todo_list_id,
                        subscriber_id="viewer-1",
                        share_type="live",
                        subscribed_at=datetime.now(timezone.utc),
                    )
                )
                competing_session.commit()
            finally:
                competing_session.close()
            raise IntegrityError("insert", {}, Exception("duplicate"))
        return real_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)

    subscription = subscribe_to_shared_todo_list(
        db,
        "viewer-1",
        todo_list_id,
        ShareType.LIVE,
    )

    assert subscription.id == "race-winner"
    assert db.query(SharedTodoListSubscription).count() == 1
