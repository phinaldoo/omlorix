import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.todos import models as todo_models
from app.todos.models import SharedTodoListSubscription, TodoLists, Todos


def _db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            TodoLists.__table__,
            Todos.__table__,
            SharedTodoListSubscription.__table__,
        ],
    )
    Session = sessionmaker(bind=engine)
    return Session()


def _todo_list():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return TodoLists(
        id="list-1",
        user_id="user-1",
        title="Planning",
        description="",
        icon="checklist",
        order=0,
        sort_order=todo_models.DEFAULT_TODO_SORT_ORDER,
        created_at=now,
        updated_at=now,
    )


def test_todo_metadata_search_and_bulk_actions_roundtrip():
    db = _db_session()
    db.add(_todo_list())
    db.commit()

    due_at = datetime.now(timezone.utc) + timedelta(days=2)
    todo = todo_models.create_todo(
        db,
        user_id="user-1",
        todo_list_id="list-1",
        content="Prepare launch board",
        notes="Include links and checklist",
        priority=2,
        due_at=due_at,
        all_day=True,
        status_value="doing",
        subtasks=[{"title": "Draft", "is_done": False}],
        links=[{"url": "https://example.test/spec"}],
        attachments=[{"name": "brief.pdf"}],
        tags=["launch"],
    )

    updated = todo_models.update_todo(
        db,
        user_id="user-1",
        todo_id=todo.id,
        tags=["launch", "review"],
        subtasks=[{"title": "Draft", "is_done": True}],
    )
    matches = todo_models.search_todos(db, "user-1", view="high_priority", query_text="launch")

    assert updated.all_day is True
    assert updated.due_at.replace(tzinfo=timezone.utc) == due_at
    assert updated.status == "doing"
    assert updated.subtasks == [{"title": "Draft", "is_done": True}]
    assert updated.links == [{"url": "https://example.test/spec"}]
    assert updated.attachments == [{"name": "brief.pdf"}]
    assert updated.tags == ["launch", "review"]
    assert [item.id for item in matches] == [todo.id]

    bulk = todo_models.bulk_update_todos(db, "user-1", [todo.id], action="complete")
    completed = db.query(Todos).filter(Todos.id == todo.id).first()

    assert bulk == {"updated": [todo.id], "errors": []}
    assert completed.is_done is True
    assert completed.status == "done"


def test_todo_mutations_roll_back_when_audit_staging_fails():
    db = _db_session()
    db.add(_todo_list())
    db.commit()

    def fail_audit_staging(_todo):
        raise RuntimeError("audit outbox unavailable")

    with pytest.raises(RuntimeError, match="audit outbox unavailable"):
        todo_models.create_todo(
            db,
            user_id="user-1",
            todo_list_id="list-1",
            content="Must stay atomic",
            before_commit=fail_audit_staging,
        )

    assert db.query(Todos).count() == 0

    todo = todo_models.create_todo(db, "user-1", "list-1", "Original")
    with pytest.raises(RuntimeError, match="audit outbox unavailable"):
        todo_models.update_todo(
            db,
            user_id="user-1",
            todo_id=todo.id,
            content="Unaudited update",
            before_commit=fail_audit_staging,
        )

    db.expire_all()
    assert db.query(Todos).filter(Todos.id == todo.id).one().content == "Original"
