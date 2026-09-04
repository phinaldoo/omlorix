import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.llm.system_instruction import memories as system_memories
from app.memories import router as memories_router
from app.memories import runtime as memory_runtime
from app.memories.runtime import MemoryPolicy
from app.memories.service import MemoryScope
from app.users.defaults import DEFAULT_USER_SETTINGS


def _policy(**overrides):
    values = {
        "user_id": "user-1",
        "requested_project_id": "project-1",
        "feature_enabled": True,
        "project_enabled": True,
    }
    values.update(overrides)
    return MemoryPolicy(**values)


def test_group_switch_is_the_only_personal_memory_mode():
    disabled = _policy(feature_enabled=False)

    assert disabled.active is False
    assert disabled.include_in_context is False
    assert disabled.use_project_memory is False
    assert disabled.scope == MemoryScope.personal("user-1")
    assert "memory" not in DEFAULT_USER_SETTINGS
    assert not hasattr(memories_router, "update_memory_settings_route")


def test_runtime_reads_group_switch_and_project_scope():
    db = object()
    project = SimpleNamespace(settings={"separate_memory_enabled": True})
    with patch.object(
        memory_runtime,
        "get_user_group_setting_value",
        return_value=True,
    ) as group_setting:
        policy = memory_runtime.get_memory_policy(
            db, "user-1", "project-1", project=project
        )

    group_setting.assert_called_once_with(
        "user-1", "memories", "enabled_memories", db
    )
    assert policy.active is True
    assert policy.include_in_context is True
    assert policy.use_project_memory is True


def test_runtime_falls_back_to_personal_scope_after_project_access_loss():
    with (
        patch.object(memory_runtime, "get_user_group_setting_value", return_value=True),
        patch.object(
            memory_runtime,
            "get_project_with_access",
            side_effect=HTTPException(status_code=404, detail="Project not found"),
        ),
    ):
        policy = memory_runtime.get_memory_policy(
            object(), "user-1", project_id="revoked-project"
        )

    assert policy.use_project_memory is False
    assert policy.scope == MemoryScope.personal("user-1")


def test_context_builder_honors_group_disable_without_database_reads():
    db = MagicMock()
    with (
        patch.object(
            system_memories,
            "get_memory_policy",
            return_value=_policy(feature_enabled=False),
        ),
        patch.object(system_memories, "list_memories") as list_rows,
    ):
        context = system_memories.get_memories_context(db, "user-1", "project-1")

    assert context == ""
    db.query.assert_not_called()
    list_rows.assert_not_called()


def test_context_builder_emits_complete_personal_and_project_profiles():
    now = datetime.now(timezone.utc)
    personal = SimpleNamespace(
        content="Prefers concise answers",
        kind="preference",
        last_confirmed_at=now,
        updated_at=now,
        review_at=now + timedelta(days=30),
    )
    project = SimpleNamespace(
        content="The release target is September",
        kind="project",
        last_confirmed_at=now,
        updated_at=now,
        review_at=now + timedelta(days=30),
    )
    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.first.return_value = None
    with (
        patch.object(system_memories, "get_memory_policy", return_value=_policy()),
        patch.object(
            system_memories,
            "list_memories",
            side_effect=[[personal], [project]],
        ),
    ):
        context = system_memories.get_memories_context(db, "user-1", "project-1")

    assert "<personal_memory>" in context
    assert "Prefers concise answers" in context
    assert "<project_memory>" in context
    assert "The release target is September" in context
    assert context.endswith("End of saved memory context.")


def test_write_scope_requires_group_memory_to_be_enabled():
    user = SimpleNamespace(id="user-1")
    with patch.object(
        memories_router,
        "get_memory_policy",
        return_value=_policy(feature_enabled=False),
    ):
        with pytest.raises(HTTPException) as exc_info:
            memories_router._resolve_scope(
                db=object(), user=user, project_id=None, require_write=True
            )

    assert exc_info.value.status_code == 403
