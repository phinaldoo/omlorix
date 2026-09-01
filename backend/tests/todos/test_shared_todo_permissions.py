import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


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

from app.database import Base
from app.todos import models as todo_models
from app.todos.models import SharedTodoListSubscription, TodoLists, Todos
from app.tools.todos import utils as todo_tools


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


def _todo_list(**overrides) -> TodoLists:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = {
        "id": "list-1",
        "user_id": "owner-1",
        "title": "Shared Tasks",
        "description": "Collaborative checklist",
        "icon": "checklist",
        "order": 0,
        "sort_order": todo_models.DEFAULT_TODO_SORT_ORDER,
        "live_share_id": "live-share-1",
        "collaborate_share_id": "collab-share-1",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return TodoLists(**values)


def _todo(**overrides) -> Todos:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = {
        "id": "todo-1",
        "todo_list": "list-1",
        "order": 0,
        "content": "Ship shared todo permissions",
        "notes": "Regression coverage",
        "priority": 1,
        "is_done": False,
        "is_marked": True,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Todos(**values)


def _subscription(subscriber_id: str, share_type: str) -> SharedTodoListSubscription:
    return SharedTodoListSubscription(
        id=f"{subscriber_id}-{share_type}",
        todo_list_id="list-1",
        subscriber_id=subscriber_id,
        share_type=share_type,
        subscribed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _seed_shared_list(db):
    db.add(_todo_list())
    db.add(_todo())
    db.add_all(
        [
            _subscription("live-user", "live"),
            _subscription("collab-user", "collaborate"),
        ]
    )
    db.commit()


def test_shared_subscribers_can_read_todos_and_marked_items():
    db = _db_session()
    _seed_shared_list(db)

    live_todos = todo_models.list_todos(db, "live-user", "list-1")
    collab_todos = todo_models.list_todos(db, "collab-user", "list-1")
    live_marked = todo_models.list_marked_todos(db, "live-user")
    collab_marked = todo_models.list_marked_todos(db, "collab-user")
    live_tool_view = todo_tools.view_todo_list_tool(db, "live-user", "list-1")

    assert [todo.id for todo in live_todos] == ["todo-1"]
    assert [todo.id for todo in collab_todos] == ["todo-1"]
    assert [todo.id for todo in live_marked] == ["todo-1"]
    assert [todo.id for todo in collab_marked] == ["todo-1"]
    assert [todo["id"] for todo in live_tool_view["todos"]] == ["todo-1"]


def test_effective_task_capabilities_cover_owner_live_and_collaborate_users():
    db = _db_session()
    _seed_shared_list(db)

    owner = todo_models.get_effective_todo_list_permissions(db, "owner-1", "list-1")
    live = todo_models.get_effective_todo_list_permissions(db, "live-user", "list-1")
    collaborator = todo_models.get_effective_todo_list_permissions(db, "collab-user", "list-1")

    assert owner == {"can_delete": True}
    assert live == {"can_delete": False}
    assert collaborator == {"can_delete": True}


def test_collaborators_can_create_edit_toggle_and_mark_shared_todos(monkeypatch):
    db = _db_session()
    _seed_shared_list(db)
    monkeypatch.setattr(todo_tools, "stage_tool_audit_action", lambda *args, **kwargs: None)

    created = todo_models.create_todo(
        db,
        user_id="collab-user",
        todo_list_id="list-1",
        content="Add a collaborator item",
        notes="Created from collaborator session",
    )
    toggled = todo_models.toggle_todo(db, "collab-user", "todo-1")
    marked = todo_models.toggle_mark_todo(db, "collab-user", "todo-1")
    edited = todo_tools.edit_todo_tool(
        db,
        user_id="collab-user",
        todo_id="todo-1",
        content="Ship enforced shared todo permissions",
        notes="Edited through the tool path",
    )

    assert created.todo_list == "list-1"
    assert created.content == "Add a collaborator item"
    assert toggled.is_done is True
    assert marked.is_marked is False
    assert edited["content"] == "Ship enforced shared todo permissions"
    assert edited["notes"] == "Edited through the tool path"
    assert "can_edit" not in edited
    assert "can_delete" not in edited


def test_live_subscribers_remain_read_only_for_todo_mutations():
    db = _db_session()
    _seed_shared_list(db)

    with pytest.raises(HTTPException) as create_exc:
        todo_models.create_todo(
            db,
            user_id="live-user",
            todo_list_id="list-1",
            content="Should fail",
        )

    with pytest.raises(HTTPException) as toggle_exc:
        todo_models.toggle_todo(db, "live-user", "todo-1")

    with pytest.raises(HTTPException) as mark_exc:
        todo_models.toggle_mark_todo(db, "live-user", "todo-1")

    with pytest.raises(HTTPException) as tool_edit_exc:
        todo_tools.edit_todo_tool(db, user_id="live-user", todo_id="todo-1", content="Nope")

    assert create_exc.value.status_code == 403
    assert toggle_exc.value.status_code == 403
    assert mark_exc.value.status_code == 403
    assert tool_edit_exc.value.status_code == 403


def test_no_op_tool_edit_creates_no_audit(monkeypatch):
    db = _db_session()
    _seed_shared_list(db)
    audit_calls = []
    monkeypatch.setattr(
        todo_tools,
        "stage_tool_audit_action",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)),
    )

    result = todo_tools.edit_todo_tool(
        db,
        user_id="owner-1",
        todo_id="todo-1",
        content="Ship shared todo permissions",
        is_done=False,
        is_marked=True,
    )

    assert result["content"] == "Ship shared todo permissions"
    assert audit_calls == []


def test_collaborators_still_cannot_manage_shared_list_metadata():
    db = _db_session()
    _seed_shared_list(db)

    with pytest.raises(HTTPException) as exc:
        todo_models.update_todo_list(
            db,
            user_id="collab-user",
            todo_list_id="list-1",
            title="Renamed by collaborator",
        )

    assert exc.value.status_code == 404


def test_todo_tool_hides_share_tokens_for_subscribed_lists():
    db = _db_session()
    _seed_shared_list(db)

    listed_payload = next(
        item for item in todo_tools.list_todo_lists_tool(db, "live-user") if item["id"] == "list-1"
    )
    viewed_payload = todo_tools.view_todo_list_tool(db, "live-user", "list-1")["todo_list"]

    for payload in (listed_payload, viewed_payload):
        assert payload["is_subscribed"] is True
        assert payload["share_type"] == "live"
        assert "can_edit" not in payload
        assert "can_delete" not in payload
        assert "share_id" not in payload
        assert "clone_share_id" not in payload
        assert "live_share_id" not in payload
        assert "collaborate_share_id" not in payload

    viewed_todo = todo_tools.view_todo_list_tool(db, "live-user", "list-1")["todos"][0]
    assert "can_edit" not in viewed_todo
    assert "can_delete" not in viewed_todo


def test_todo_tool_retains_share_tokens_for_owned_lists():
    db = _db_session()
    _seed_shared_list(db)

    listed_payload = next(
        item for item in todo_tools.list_todo_lists_tool(db, "owner-1") if item["id"] == "list-1"
    )
    viewed_payload = todo_tools.view_todo_list_tool(db, "owner-1", "list-1")["todo_list"]

    for payload in (listed_payload, viewed_payload):
        assert payload["is_subscribed"] is False
        assert payload["live_share_id"] == "live-share-1"
        assert payload["collaborate_share_id"] == "collab-share-1"


def test_clone_shared_todo_list_preserves_todo_metadata():
    db = _db_session()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    todo_list = _todo_list(id="list-clone", user_id="owner-1", clone_share_id="clone-share-1")
    todo = _todo(
        id="todo-clone",
        todo_list="list-clone",
        all_day=True,
        status="done",
        subtasks=[{"title": "Outline", "is_done": True}],
        links=[{"url": "https://example.test/spec"}],
        attachments=[{"name": "brief.pdf"}],
        tags=["launch"],
        is_done=True,
        completed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(todo_list)
    db.add(todo)
    db.commit()

    cloned_list = todo_models.clone_shared_todo_list(db, "viewer-1", "clone-share-1")
    cloned_todo = db.query(Todos).filter(Todos.todo_list == cloned_list.id).one()

    assert cloned_list.user_id == "viewer-1"
    assert cloned_todo.all_day is True
    assert cloned_todo.status == "done"
    assert cloned_todo.subtasks == [{"title": "Outline", "is_done": True}]
    assert cloned_todo.links == [{"url": "https://example.test/spec"}]
    assert cloned_todo.attachments == [{"name": "brief.pdf"}]
    assert cloned_todo.tags == ["launch"]
    assert cloned_todo.is_done is True
    assert cloned_todo.completed_at.replace(tzinfo=timezone.utc) == now
