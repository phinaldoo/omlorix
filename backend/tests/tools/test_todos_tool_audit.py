import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.schemas import tool_schemas
from app.tools import helper as tool_helper
from app.tools.todos import utils as todo_utils
from app.tools.utils import resolve_enabled_tools


def test_create_todo_tool_writes_audit_log(monkeypatch):
    audit_calls = []
    db = MagicMock()

    monkeypatch.setattr(
        todo_utils,
        "stage_tool_audit_action",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)),
    )

    def fake_create_todo(**kwargs):
        todo = SimpleNamespace(
            id="todo-1",
            todo_list="list-1",
            content=kwargs["content"],
            notes=kwargs["notes"],
            priority=kwargs["priority"],
            due_at=None,
            is_done=False,
            is_marked=False,
            completed_at=None,
            order=kwargs["order"],
            created_at=None,
            updated_at=None,
        )
        kwargs["before_commit"](todo)
        return todo

    monkeypatch.setattr(
        todo_utils,
        "db_create_todo",
        fake_create_todo,
    )
    result = todo_utils.create_todo_tool(
        db=db,
        user_id="user-1",
        todo_list_id="list-1",
        content="Write audit coverage",
        priority=2,
        order=4,
    )

    assert result["id"] == "todo-1"
    assert audit_calls == [
        (
            (db, "user-1", "TODO_CREATED"),
            {
                "category": "todo",
                "details": {
                "todo_id": "todo-1",
                "todo_list_id": "list-1",
                },
            },
        )
    ]


def test_todo_tool_schema_excludes_destructive_actions_and_keeps_completion():
    properties = tool_schemas["todos"]["parameters"]["properties"]

    assert properties["type"]["enum"] == ["list", "view", "create", "edit", "bulk"]
    assert properties["action"]["enum"] == ["complete", "incomplete", "move", "tag"]
    assert properties["is_done"]["type"] == "boolean"


def test_retired_todo_delete_tool_names_are_not_enabled():
    resolved = resolve_enabled_tools(["remove_todo", "remove_todo_list"])

    assert resolved["tool_list"] == []
    assert resolved["tool_schemas"] == []


@pytest.mark.parametrize("entity", ["todo", "list"])
def test_todos_tool_rejects_delete_operations(entity):
    with pytest.raises(
        ValueError,
        match="Allowed values are: list, view, create, edit, bulk",
    ):
        todo_utils.todos_tool(
            db=MagicMock(),
            user_id="user-1",
            type="delete",
            entity=entity,
            todo_id="todo-1",
            todo_list_id="list-1",
        )


def test_tool_call_dispatch_rejects_model_delete_requests(monkeypatch):
    """A crafted model call cannot bypass the public todo-tool schema."""
    todo_dispatch = MagicMock()
    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_helper, "_ensure_feature_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_helper, "todos_tool", todo_dispatch)

    resolver = tool_helper.resolve_tool_call(
        db=MagicMock(),
        tool_name="todos",
        tool_arguments={"type": "delete", "entity": "todo", "todo_id": "todo-1"},
        user_id="user-1",
        group_id=None,
        project_id=None,
    )

    with pytest.raises(
        ValueError,
        match="type must be one of: list, view, create, edit, bulk",
    ):
        next(resolver)

    todo_dispatch.assert_not_called()


def test_tool_call_dispatch_keeps_todo_completion_available(monkeypatch):
    """Completion still works through the same top-level path used by models."""
    todo = SimpleNamespace(
        id="todo-1",
        todo_list="list-1",
        content="Finish the task",
        notes=None,
        priority=0,
        due_at=None,
        status="todo",
        is_done=False,
        is_marked=False,
        completed_at=None,
        order=0,
        created_at=None,
        updated_at=None,
    )

    def fake_update_todo(**kwargs):
        todo.is_done = kwargs["is_done"]
        todo.status = "done" if todo.is_done else "todo"
        kwargs["before_commit"](todo)
        return todo

    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_helper, "_ensure_feature_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(todo_utils, "get_editable_todo", lambda *args, **kwargs: todo)
    monkeypatch.setattr(todo_utils, "db_update_todo", fake_update_todo)
    monkeypatch.setattr(todo_utils, "stage_tool_audit_action", lambda *args, **kwargs: None)

    resolver = tool_helper.resolve_tool_call(
        db=MagicMock(),
        tool_name="todos",
        tool_arguments={"type": "edit", "todo_id": "todo-1", "is_done": "true"},
        user_id="user-1",
        group_id=None,
        project_id=None,
    )

    with pytest.raises(StopIteration) as completed:
        next(resolver)

    result = completed.value.value["result"]["todo"]
    assert result["is_done"] is True
    assert result["status"] == "done"


def test_todos_tool_rejects_bulk_delete_before_database_mutation(monkeypatch):
    bulk_update = MagicMock()
    monkeypatch.setattr(todo_utils, "db_bulk_update_todos", bulk_update)

    with pytest.raises(ValueError, match="action must be one of: complete, incomplete, move, tag"):
        todo_utils.todos_tool(
            db=MagicMock(),
            user_id="user-1",
            type="bulk",
            todo_ids=["todo-1"],
            action="delete",
        )

    bulk_update.assert_not_called()


def test_edit_todo_tool_writes_update_and_toggle_audit_logs(monkeypatch):
    audit_calls = []
    todo = SimpleNamespace(
        id="todo-7",
        todo_list="list-3",
        content="Before",
        notes=None,
        priority=1,
        due_at=None,
        is_done=False,
        is_marked=False,
        completed_at=None,
        order=2,
        created_at=None,
        updated_at=None,
    )
    db = MagicMock()

    monkeypatch.setattr(
        todo_utils,
        "stage_tool_audit_action",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)),
    )
    mutation_calls = []

    def fake_update_todo(**kwargs):
        mutation_calls.append(kwargs)
        todo.content = kwargs["content"]
        todo.is_done = kwargs["is_done"]
        todo.is_marked = kwargs["is_marked"]
        kwargs["before_commit"](todo)
        return todo

    monkeypatch.setattr(
        todo_utils,
        "db_update_todo",
        fake_update_todo,
    )
    monkeypatch.setattr(todo_utils, "get_editable_todo", lambda *args, **kwargs: todo)
    result = todo_utils.edit_todo_tool(
        db=db,
        user_id="user-1",
        todo_id="todo-7",
        content="After",
        is_done=True,
        is_marked=True,
    )

    assert result["content"] == "After"
    assert "actor_type" not in mutation_calls[0]
    assert mutation_calls[0]["content"] == "After"
    assert mutation_calls[0]["is_done"] is True
    assert mutation_calls[0]["is_marked"] is True
    assert [call[0][2] for call in audit_calls] == [
        "TODO_UPDATED",
        "TODO_TOGGLED",
        "TODO_MARK_TOGGLED",
    ]
    assert audit_calls[0][1]["details"] == {
        "todo_id": "todo-7",
        "todo_list_id": "list-3",
        "updated_fields": ["content"],
    }
    assert audit_calls[1][1]["details"] == {
        "todo_id": "todo-7",
        "todo_list_id": "list-3",
        "is_done": True,
    }
    assert audit_calls[2][1]["details"] == {
        "todo_id": "todo-7",
        "todo_list_id": "list-3",
        "is_marked": True,
    }


def test_todo_list_and_bulk_tools_stage_audit_before_model_commit(monkeypatch):
    db = MagicMock()
    audit_calls = []
    todo_list = SimpleNamespace(
        id="list-9",
        user_id="user-1",
        title="Plan",
        description="",
        icon="list",
        sort_order=[],
        order=0,
        created_at=None,
        updated_at=None,
    )

    monkeypatch.setattr(
        todo_utils,
        "stage_tool_audit_action",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)),
    )

    def fake_create_list(**kwargs):
        kwargs["before_commit"](todo_list)
        return todo_list

    def fake_update_list(**kwargs):
        kwargs["before_commit"](todo_list)
        return todo_list

    def fake_bulk_update(**kwargs):
        result = {"updated": ["todo-1", "todo-2"], "errors": []}
        kwargs["before_commit"](result)
        return result

    monkeypatch.setattr(todo_utils, "db_create_todo_list", fake_create_list)
    monkeypatch.setattr(todo_utils, "db_update_todo_list", fake_update_list)
    monkeypatch.setattr(todo_utils, "db_bulk_update_todos", fake_bulk_update)

    todo_utils.create_todo_list_tool(db, "user-1", "Plan")
    todo_utils.edit_todo_list_tool(db, "user-1", "list-9", title="Updated")
    todo_utils.bulk_todos_tool(db, "user-1", ["todo-1", "todo-2"], "complete")

    assert [call[0][2] for call in audit_calls] == [
        "TODO_LIST_CREATED",
        "TODO_LIST_UPDATED",
        "TODO_BULK_UPDATED",
    ]
    assert all(call[0][:2] == (db, "user-1") for call in audit_calls)
    assert audit_calls[2][1]["details"] == {"action": "complete", "updated_count": 2}
