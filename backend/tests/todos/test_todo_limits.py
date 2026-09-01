from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base  # noqa: E402
from app.todos import models as todo_models  # noqa: E402
from app.todos.limits import (  # noqa: E402
    MAX_TODO_CONTENT_LENGTH,
    MAX_TODO_LIST_DESCRIPTION_LENGTH,
    MAX_TODO_NOTES_LENGTH,
)
from app.todos.models import SharedTodoListSubscription, TodoLists, Todos  # noqa: E402
from app.todos.schemas import TodoCreate, TodoListCreate  # noqa: E402


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


def test_todo_schemas_reject_oversized_payload_fields():
    with pytest.raises(ValidationError):
        TodoListCreate(title="List", icon="list", description="x" * (MAX_TODO_LIST_DESCRIPTION_LENGTH + 1))

    with pytest.raises(ValidationError):
        TodoCreate(content="x" * (MAX_TODO_CONTENT_LENGTH + 1))

    with pytest.raises(ValidationError):
        TodoCreate(content="Todo", notes="x" * (MAX_TODO_NOTES_LENGTH + 1))


def test_todo_model_enforces_payload_size_limits():
    db = _session()

    with pytest.raises(HTTPException) as description_error:
        todo_models.create_todo_list(
            db,
            "user-1",
            "List",
            "x" * (MAX_TODO_LIST_DESCRIPTION_LENGTH + 1),
            "list",
        )
    assert description_error.value.status_code == 413

    todo_list = todo_models.create_todo_list(db, "user-1", "List", "", "list")

    with pytest.raises(HTTPException) as content_error:
        todo_models.create_todo(
            db,
            "user-1",
            todo_list.id,
            "x" * (MAX_TODO_CONTENT_LENGTH + 1),
        )
    assert content_error.value.status_code == 413

    with pytest.raises(HTTPException) as notes_error:
        todo_models.create_todo(
            db,
            "user-1",
            todo_list.id,
            "Todo",
            notes="x" * (MAX_TODO_NOTES_LENGTH + 1),
        )
    assert notes_error.value.status_code == 413


@pytest.mark.parametrize("field_name", ["subtasks", "links", "attachments"])
def test_todo_model_rejects_non_array_json_payloads(field_name):
    db = _session()
    todo_list = todo_models.create_todo_list(db, "user-1", "List", "", "list")

    with pytest.raises(HTTPException) as payload_error:
        todo_models.create_todo(
            db,
            "user-1",
            todo_list.id,
            "Todo",
            **{field_name: {"unexpected": True}},
        )

    assert payload_error.value.status_code == 400
    assert payload_error.value.detail == f"{field_name} must be a JSON array"


def test_todo_model_enforces_list_and_todo_count_quotas(monkeypatch):
    db = _session()

    monkeypatch.setattr(todo_models, "MAX_TODO_LISTS_PER_USER", 1)
    first_list = todo_models.create_todo_list(db, "user-1", "List", "", "list")
    with pytest.raises(HTTPException) as list_quota_error:
        todo_models.create_todo_list(db, "user-1", "Another list", "", "list")
    assert list_quota_error.value.status_code == 409

    monkeypatch.setattr(todo_models, "MAX_TODOS_PER_LIST", 1)
    todo_models.create_todo(db, "user-1", first_list.id, "First todo")
    with pytest.raises(HTTPException) as todo_quota_error:
        todo_models.create_todo(db, "user-1", first_list.id, "Second todo")
    assert todo_quota_error.value.status_code == 409


def test_bulk_move_rejects_target_list_quota_overflow(monkeypatch):
    db = _session()
    monkeypatch.setattr(todo_models, "MAX_TODOS_PER_LIST", 2)

    source_list = todo_models.create_todo_list(db, "user-1", "Source", "", "list")
    target_list = todo_models.create_todo_list(db, "user-1", "Target", "", "list")
    existing_target = todo_models.create_todo(db, "user-1", target_list.id, "Already there")
    first_source = todo_models.create_todo(db, "user-1", source_list.id, "Move one")
    second_source = todo_models.create_todo(db, "user-1", source_list.id, "Move two")

    with pytest.raises(HTTPException) as move_error:
        todo_models.bulk_update_todos(
            db,
            "user-1",
            [first_source.id, second_source.id],
            action="move",
            target_list_id=target_list.id,
        )

    assert move_error.value.status_code == 400
    assert move_error.value.detail == "target list would exceed max todos"
    assert db.query(Todos).filter(Todos.todo_list == target_list.id).count() == 1
    assert db.query(Todos).filter(Todos.todo_list == source_list.id).count() == 2
    assert db.query(Todos.id).filter(Todos.id == existing_target.id).count() == 1


def test_todo_list_pages_use_the_requested_server_sort_order():
    db = _session()
    todo_list = todo_models.create_todo_list(db, "user-1", "List", "", "list")
    todo_models.create_todo(db, "user-1", todo_list.id, "Zulu")
    todo_models.create_todo(db, "user-1", todo_list.id, "Alpha")
    todo_models.create_todo(db, "user-1", todo_list.id, "Middle")

    first_page = todo_models.list_todos(
        db,
        "user-1",
        todo_list.id,
        limit=2,
        offset=0,
        sort_value="alpha-asc",
    )
    second_page = todo_models.list_todos(
        db,
        "user-1",
        todo_list.id,
        limit=2,
        offset=2,
        sort_value="alpha-asc",
    )

    assert [todo.content for todo in first_page] == ["Alpha", "Middle"]
    assert [todo.content for todo in second_page] == ["Zulu"]
