import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.memories import router as memories_router
from app.memories.runtime import MemoryPolicy
from app.memories.schemas import MemoryCreate
from app.memories.service import MemoryScope


def _user(user_id: str = "user-1"):
    return SimpleNamespace(id=user_id)


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": path,
            "headers": [(b"user-agent", b"pytest")],
            "client": ("203.0.113.10", 12345),
        }
    )


def _memory(**overrides):
    values = {
        "id": "memory-1",
        "user_id": "user-1",
        "project_id": None,
        "content": "Remember this",
        "source_date": None,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _policy(*, project_id=None, project_enabled=True):
    return MemoryPolicy(
        user_id="user-1",
        requested_project_id=project_id,
        feature_enabled=True,
        account_enabled=True,
        include_in_context_setting=True,
        auto_create_setting=True,
        project_enabled=project_enabled,
    )


def test_personal_memory_list_remains_available_when_feature_is_disabled():
    db = MagicMock()
    with patch.object(
        memories_router, "list_memories", return_value=[_memory()]
    ) as list_rows:
        response = memories_router.list_memories_route(
            project_id=None,
            limit=20,
            offset=0,
            db=db,
            user=_user(),
        )

    assert [item.id for item in response.items] == ["memory-1"]
    list_rows.assert_called_once_with(
        db, MemoryScope.personal("user-1"), limit=21, offset=0
    )


def test_project_list_uses_same_service_after_access_check():
    db = MagicMock()
    with (
        patch.object(
            memories_router,
            "get_project_with_access",
            return_value=SimpleNamespace(id="project-1"),
        ),
        patch.object(
            memories_router,
            "list_memories",
            return_value=[_memory(user_id=None, project_id="project-1")],
        ) as list_rows,
    ):
        response = memories_router.list_memories_route(
            project_id="project-1",
            limit=20,
            offset=0,
            db=db,
            user=_user(),
        )

    assert response.items[0].project_id == "project-1"
    list_rows.assert_called_once_with(
        db, MemoryScope.project("project-1"), limit=21, offset=0
    )


def test_delete_remains_available_when_creation_is_disabled():
    db = MagicMock()
    with (
        patch.object(
            memories_router,
            "delete_memory",
            return_value={"deleted": True, "memory_id": "memory-1"},
        ) as delete,
        patch.object(memories_router, "_audit"),
    ):
        response = memories_router.delete_memory_route(
            "memory-1",
            _request("/api/v1/memories/memory-1"),
            project_id=None,
            db=db,
            db_log=MagicMock(),
            user=_user(),
        )

    assert response["deleted"] is True
    delete.assert_called_once_with(db, MemoryScope.personal("user-1"), "memory-1")


def test_project_create_is_blocked_when_shared_scope_is_disabled():
    project = SimpleNamespace(
        id="project-1", settings={"separate_memory_enabled": False}
    )
    with (
        patch.object(memories_router, "get_project_with_access", return_value=project),
        patch.object(
            memories_router,
            "get_memory_policy",
            return_value=_policy(project_id="project-1", project_enabled=False),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            memories_router.create_memory_route(
                MemoryCreate(content="Remember this"),
                _request("/api/v1/memories"),
                project_id="project-1",
                db=MagicMock(),
                db_log=MagicMock(),
                user=_user(),
            )

    assert exc_info.value.status_code == 409


def test_personal_and_project_create_share_one_service():
    db = MagicMock()
    memory = _memory()
    with (
        patch.object(memories_router, "get_memory_policy", return_value=_policy()),
        patch.object(
            memories_router,
            "create_memory",
            return_value=(memory, True),
        ) as create,
        patch.object(memories_router, "_audit"),
    ):
        result = memories_router.create_memory_route(
            MemoryCreate(content="Remember this"),
            _request("/api/v1/memories"),
            project_id=None,
            db=db,
            db_log=MagicMock(),
            user=_user(),
        )

    assert result is memory
    create.assert_called_once_with(db, MemoryScope.personal("user-1"), "Remember this")
