import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.llm.system_instruction import memories as system_memories
from app.memories import router as memories_router
from app.memories import runtime as memory_runtime
from app.memories.runtime import MemoryPolicy
from app.memories.service import MemoryScope


def _policy(**overrides):
    values = {
        "user_id": "user-1",
        "requested_project_id": "project-1",
        "feature_enabled": True,
        "account_enabled": True,
        "include_in_context_setting": True,
        "auto_create_setting": True,
        "project_enabled": True,
    }
    values.update(overrides)
    return MemoryPolicy(**values)


def test_policy_falls_back_to_personal_scope_when_account_memory_is_off():
    policy = _policy(account_enabled=False)

    assert policy.active is False
    assert policy.use_project_memory is False
    assert policy.scope == MemoryScope.personal("user-1")


def test_runtime_loads_memory_settings_once():
    project = SimpleNamespace(settings={"separate_memory_enabled": True})
    with (
        patch.object(memory_runtime, "get_user_group_setting_value", return_value=True),
        patch.object(
            memory_runtime,
            "get_memory_settings",
            return_value={
                "enabled": True,
                "include_in_context": True,
                "auto_create": False,
            },
        ) as settings,
    ):
        policy = memory_runtime.get_memory_policy(
            object(), "user-1", "project-1", project=project
        )

    settings.assert_called_once()
    assert policy.use_project_memory is True
    assert policy.auto_create is False


def test_runtime_falls_back_to_personal_scope_after_project_access_loss():
    with (
        patch.object(memory_runtime, "get_user_group_setting_value", return_value=True),
        patch.object(
            memory_runtime,
            "get_memory_settings",
            return_value={
                "enabled": True,
                "include_in_context": True,
                "auto_create": True,
            },
        ),
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


def test_context_builder_honors_context_opt_out():
    with (
        patch.object(
            system_memories,
            "get_memory_policy",
            return_value=_policy(include_in_context_setting=False),
        ),
        patch.object(
            system_memories,
            "list_memories",
        ) as list_rows,
    ):
        context = system_memories.get_memories_context(object(), "user-1", "project-1")

    assert context == ""
    list_rows.assert_not_called()


def test_context_builder_emits_one_complete_block():
    rows = [SimpleNamespace(content="Prefers concise answers")]
    with (
        patch.object(system_memories, "get_memory_policy", return_value=_policy()),
        patch.object(
            system_memories,
            "list_memories",
            return_value=rows,
        ),
    ):
        context = system_memories.get_memories_context(object(), "user-1", "project-1")

    assert "1. Prefers concise answers" in context
    assert context.endswith("Continue with the main conversation.")


def test_memory_settings_update_uses_one_bulk_write():
    user = SimpleNamespace(id="user-1")
    request = SimpleNamespace(headers={})
    payload = memories_router.MemorySettingsUpdate(
        include_in_context=False, auto_create=False
    )
    with (
        patch.object(memories_router, "_ensure_feature_available"),
        patch.object(
            memories_router,
            "update_user_settings_bulk",
        ) as update_bulk,
        patch.object(
            memories_router,
            "get_memory_settings",
            return_value={
                "enabled": True,
                "include_in_context": False,
                "auto_create": False,
            },
        ),
        patch.object(memories_router, "_audit"),
    ):
        response = memories_router.update_memory_settings_route(
            payload,
            request,
            db=object(),
            db_log=object(),
            user=user,
        )

    assert response["include_in_context"] is False
    update_bulk.assert_called_once_with(
        "user-1",
        {"memory": {"include_in_context": False, "auto_create": False}},
        update_bulk.call_args.args[2],
    )


def test_write_scope_requires_account_memory_to_be_enabled():
    user = SimpleNamespace(id="user-1")
    with patch.object(
        memories_router,
        "get_memory_policy",
        return_value=_policy(account_enabled=False),
    ):
        with pytest.raises(HTTPException) as exc_info:
            memories_router._resolve_scope(
                db=object(), user=user, project_id=None, require_write=True
            )

    assert exc_info.value.status_code == 403
