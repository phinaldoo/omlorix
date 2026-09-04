import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())


from app.todos import router as todos_router


def _todo_list(**overrides):
    values = {
        "id": "todo-list-1",
        "user_id": "owner-1",
        "title": "Shared todos",
        "description": "Description",
        "icon": '{"preset":"checklist","color":"#10b981"}',
        "clone_share_id": "clone-token",
        "live_share_id": "live-token",
        "collaborate_share_id": "collaborate-token",
        "sort_order": [{"key": "priority", "direction": "desc"}],
        "order": 0,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _subscription(**overrides):
    values = {
        "share_type": "live",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _todo(**overrides):
    values = {
        "id": "todo-1",
        "todo_list": "todo-list-1",
        "content": "Permission-aware task",
        "notes": None,
        "priority": 0,
        "due_at": None,
        "all_day": False,
        "status": "todo",
        "subtasks": [],
        "links": [],
        "attachments": [],
        "tags": [],
        "is_done": False,
        "is_marked": False,
        "completed_at": None,
        "order": 0,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_todo_response_uses_canonical_share_type_and_server_capabilities():
    response = todos_router._todo_response(
        _todo(),
        SimpleNamespace(),
        "live-viewer",
        {"share_type": "live", "can_delete": False},
    )

    assert response.share_type == "live"
    assert not hasattr(response, "can_edit")
    assert response.can_delete is False


def test_list_todos_resolves_share_metadata_once_per_list(monkeypatch):
    monkeypatch.setattr(todos_router, "ensure_todo_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        todos_router,
        "list_todos",
        lambda *_args, **_kwargs: [_todo(id="todo-1"), _todo(id="todo-2")],
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _todo_list(id="list-1", user_id="user-1")
    response = todos_router.list_todos_route(
        todo_list_id="list-1",
        q=None,
        view=None,
        priority_min=None,
        no_due_date=None,
        status_filter=None,
        sort_value=None,
        limit=50,
        offset=0,
        db=db,
        user=SimpleNamespace(id="user-1"),
    )

    assert len(response.items) == 2
    assert db.query.call_count == 1


def test_list_todo_lists_uses_owner_and_subscriber_response_shapes(monkeypatch):
    owner_list = _todo_list(id="own-list", user_id="viewer-1")
    subscribed_list = _todo_list(id="subscribed-list", user_id="owner-1")

    monkeypatch.setattr(todos_router, "ensure_todo_enabled", lambda user, db: None)
    def summary_page(*args, **kwargs):
        owned = vars(owner_list) | {"is_subscribed": False, "subscriber_count": 2}
        shared = vars(subscribed_list) | {"is_subscribed": True, "share_type": "live", "owner_name": "Owner"}
        return {"todo_lists": [owned, shared], "limit": 50, "offset": 0, "has_more": False}
    monkeypatch.setattr("app.todos.queries.list_todo_list_summaries", summary_page)

    response = todos_router.list_todo_lists_route(db=SimpleNamespace(), user=SimpleNamespace(id="viewer-1"))
    by_id = {item["id"]: item for item in response.model_dump()["items"]}

    assert by_id["own-list"]["clone_share_id"] == "clone-token"
    assert by_id["own-list"]["live_share_id"] == "live-token"
    assert by_id["own-list"]["collaborate_share_id"] == "collaborate-token"
    assert "share" not in by_id["own-list"]
    assert "share_id" not in by_id["own-list"]
    assert by_id["own-list"]["subscriber_count"] == 2

    subscribed_payload = by_id["subscribed-list"]
    assert subscribed_payload["is_subscribed"] is True
    assert subscribed_payload["share_type"] == "live"
    assert "can_edit" not in subscribed_payload
    assert subscribed_payload["owner_name"] == "Owner"
    assert "user_id" not in subscribed_payload
    assert "clone_share_id" not in subscribed_payload
    assert "live_share_id" not in subscribed_payload
    assert "collaborate_share_id" not in subscribed_payload
    assert "subscriber_count" not in subscribed_payload
