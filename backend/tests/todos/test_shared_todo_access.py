from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base  # noqa: E402
from app.todos import models as todo_models  # noqa: E402
from app.todos.models import SharedTodoListSubscription, TodoLists, Todos  # noqa: E402
from app.tools.todos.utils import list_todo_lists_tool, view_todo_list_tool  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            TodoLists.__table__,
            Todos.__table__,
            SharedTodoListSubscription.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def _seed_shared_list(db, *, share_type: str):
    now = datetime.now(timezone.utc)
    todo_list = TodoLists(
        id=f"list-{share_type}",
        user_id="owner-1",
        order=0,
        title=f"{share_type.title()} list",
        description="",
        icon="list",
        live_share_id="live-share-1" if share_type == "live" else None,
        collaborate_share_id="collab-share-1" if share_type == "collaborate" else None,
        sort_order=[],
        created_at=now,
        updated_at=now,
    )
    todo = Todos(
        id=f"todo-{share_type}",
        todo_list=todo_list.id,
        order=0,
        content="Existing todo",
        priority=0,
        is_done=False,
        is_marked=False,
        created_at=now,
        updated_at=now,
    )
    subscription = SharedTodoListSubscription(
        id=f"sub-{share_type}",
        todo_list_id=todo_list.id,
        subscriber_id="viewer-1",
        share_type=share_type,
        subscribed_at=now,
    )
    db.add(todo_list)
    db.add(todo)
    db.add(subscription)
    db.commit()
    return todo_list, todo


def test_live_subscriber_can_open_shared_todo_list():
    db = _session()
    todo_list, todo = _seed_shared_list(db, share_type="live")

    todos = todo_models.list_todos(db, "viewer-1", todo_list.id)
    payload = view_todo_list_tool(db, "viewer-1", todo_list.id)

    assert [item.id for item in todos] == [todo.id]
    assert payload["todo_list"]["id"] == todo_list.id
    assert payload["todo_list"]["is_subscribed"] is True
    assert payload["todo_list"]["share_type"] == "live"
    assert "can_edit" not in payload["todo_list"]
    assert [item["id"] for item in payload["todos"]] == [todo.id]


def test_collaborate_subscriber_can_mutate_shared_todos():
    db = _session()
    todo_list, todo = _seed_shared_list(db, share_type="collaborate")

    created = todo_models.create_todo(db, "viewer-1", todo_list.id, "New todo")
    toggled = todo_models.toggle_todo(db, "viewer-1", todo.id)
    marked = todo_models.toggle_mark_todo(db, "viewer-1", todo.id)

    assert created.todo_list == todo_list.id
    assert toggled.is_done is True
    assert marked.is_marked is True


def test_live_subscriber_cannot_mutate_shared_todos():
    db = _session()
    todo_list, todo = _seed_shared_list(db, share_type="live")

    with pytest.raises(HTTPException) as create_error:
        todo_models.create_todo(db, "viewer-1", todo_list.id, "Blocked")
    with pytest.raises(HTTPException) as toggle_error:
        todo_models.toggle_todo(db, "viewer-1", todo.id)

    assert create_error.value.status_code == 403
    assert toggle_error.value.status_code == 403


def test_todo_tools_include_subscribed_lists():
    db = _session()
    now = datetime.now(timezone.utc)
    owned_list = TodoLists(
        id="owned-list",
        user_id="viewer-1",
        order=0,
        title="Owned list",
        description="",
        icon="list",
        sort_order=[],
        created_at=now,
        updated_at=now,
    )
    db.add(owned_list)
    db.commit()

    shared_list, _ = _seed_shared_list(db, share_type="live")

    payload = list_todo_lists_tool(db, "viewer-1")
    by_id = {item["id"]: item for item in payload}

    assert set(by_id) == {"owned-list", shared_list.id}
    assert by_id["owned-list"]["is_subscribed"] is False
    assert "can_edit" not in by_id["owned-list"]
    assert by_id[shared_list.id]["is_subscribed"] is True
    assert by_id[shared_list.id]["share_type"] == "live"
    assert "can_edit" not in by_id[shared_list.id]
