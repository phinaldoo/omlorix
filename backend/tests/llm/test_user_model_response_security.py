import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.utils import (
    ensure_user_access_to_model,
    list_admin_models,
    list_user_models,
)


USER_MODEL_SUMMARY_FIELDS = {
    "model_id",
    "name",
    "description",
    "model_icon",
    "provider",
    "model_kind",
    "status",
    "is_last",
    "capabilities",
    "input_formats",
    "output_formats",
    "model_select_tools",
    "model_select_connections",
    "is_provider_group",
    "provider_recipients",
    "tokens_per_second",
    "increased_errors",
    "has_fixed_skill",
}


def _patch_common_model_dependencies(
    monkeypatch,
    *,
    user,
    models,
    agents_enabled=False,
):
    monkeypatch.setattr("app.llm.utils.get_user", lambda db, user_id: user)
    monkeypatch.setattr("app.llm.utils.list_models", lambda db: models)
    monkeypatch.setattr("app.llm.utils._is_provider_available_to_user", lambda db, provider_id: True)
    def _group_setting(_user_id, section, key, _db):
        if (section, key) == ("agents", "allow_agents"):
            return agents_enabled
        if (section, key) == ("tools_mcp", "enabled_connections"):
            return []
        return False

    monkeypatch.setattr(
        "app.llm.utils.get_user_group_setting_value", _group_setting
    )
    monkeypatch.setattr("app.llmstats.models.get_model_cached_tokens_per_second", lambda meta: None)
    monkeypatch.setattr("app.llmstats.models.get_model_cached_tokens_per_second_sample_count", lambda meta: 0)
    monkeypatch.setattr(
        "app.llmstats.models.get_model_performance_meta",
        lambda meta: {"sample_limit": None, "max_age_days": None},
    )


def _shared_model(**overrides):
    values = {
        "id": "model-1",
        "name": "Grouped model",
        "description": "Test model",
        "model_icon": "bot",
        "provider": "openrouter",
        "provider_id": "provider-group-1",
        "model_name": "openrouter/test",
        "capabilities": [],
        "status": "normal",
        "is_active": True,
        "settings": {},
        "tools": [],
        "access": {"groups": ["group-1"]},
        "meta": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_user_model_payload_does_not_expose_provider_group_member_ids(monkeypatch):
    user = SimpleNamespace(id="user-1", role="user", group_id="group-1", last_model=None)
    model = _shared_model()
    provider_group = SimpleNamespace(id="provider-group-1")
    member_provider = SimpleNamespace(id="provider-secret-1", provider="openrouter")

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])
    monkeypatch.setattr(
        "app.llm.utils.get_llm_provider",
        lambda db, provider_id: (_ for _ in ()).throw(HTTPException(status_code=404, detail="not found")),
    )
    monkeypatch.setattr("app.llm.utils.get_provider_group", lambda db, provider_id: provider_group)
    monkeypatch.setattr(
        "app.llm.provider_groups.get_group_member_providers",
        lambda db, provider_id: [member_provider],
    )

    payload = list_user_models(db=SimpleNamespace(), user_id=user.id)

    assert payload[0]["is_provider_group"] is True
    assert "provider_group_id" not in payload[0]
    assert payload[0]["provider_recipients"] == [{"provider": "openrouter"}]
    assert "id" not in payload[0]["provider_recipients"][0]


def test_user_model_payload_hides_fixed_skill_ids(monkeypatch):
    user = SimpleNamespace(id="user-1", role="user", group_id="group-1", last_model=None)
    model = _shared_model(
        settings={
            "skill_id": "admin-skill-secret",
            "skill_ids": ["admin-skill-secret"],
            "training_data": "docs",
            "input_formats": ["text"],
        },
    )

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])
    monkeypatch.setattr("app.llm.utils.get_llm_provider", lambda db, provider_id: SimpleNamespace(id=provider_id, availability="active"))

    payload = list_user_models(db=SimpleNamespace(), user_id=user.id)

    assert payload[0]["has_fixed_skill"] is True
    assert "skill_id" not in payload[0]
    assert "settings" not in payload[0]


def test_non_admin_user_model_payload_redacts_hidden_model_configuration(monkeypatch):
    user = SimpleNamespace(id="user-1", role="user", group_id="group-1", last_model="model-1")
    model = _shared_model(
        settings={
            "training_data": "unknown",
            "skill_id": "admin-skill-secret",
            "skill_ids": ["admin-skill-secret"],
            "system_instruction": "SECRET system prompt",
            "custom_title_generation_instruction": "SECRET title prompt",
            "input_formats": ["text"],
            "output_formats": ["text"],
        },
        tools=[{"id": "internal-crm-tool"}],
        access={"everyone": True, "users": ["ceo-user"], "groups": ["group-1"]},
    )

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])
    monkeypatch.setattr("app.llm.utils.get_llm_provider", lambda db, provider_id: SimpleNamespace(id=provider_id))

    payload = list_user_models(db=SimpleNamespace(), user_id=user.id)

    assert set(payload[0]) == USER_MODEL_SUMMARY_FIELDS
    assert payload[0]["model_id"] == "model-1"
    assert payload[0]["is_last"] is True
    assert payload[0]["has_fixed_skill"] is True
    assert payload[0]["model_select_tools"] == []


def test_user_model_payload_exposes_only_model_and_group_supported_connections(monkeypatch):
    user = SimpleNamespace(id="user-1", role="user", group_id="group-1", last_model=None)
    model = _shared_model(
        settings={
            "allowed_mcp_servers": [
                "admin-server-secret",
                "__connection_provider__:github",
                "__connection_provider__:notion",
            ],
        },
        tools=["mcp"],
        access={"everyone": True},
    )

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])
    monkeypatch.setattr(
        "app.llm.utils.get_user_group_setting_value",
        lambda _user_id, section, key, _db: (
            ["github", "slack"]
            if (section, key) == ("tools_mcp", "enabled_connections")
            else False
        ),
    )
    monkeypatch.setattr(
        "app.connections.service.list_managed_connection_mcp_catalog",
        lambda *_args, **_kwargs: [
            {"provider": "github", "title": "GitHub"},
            {"provider": "notion", "title": "Notion"},
            {"provider": "slack", "title": "Slack"},
        ],
    )
    monkeypatch.setattr(
        "app.llm.utils.get_llm_provider",
        lambda db, provider_id: SimpleNamespace(id=provider_id),
    )

    payload = list_user_models(db=SimpleNamespace(), user_id=user.id)

    assert payload[0]["model_select_connections"] == [
        {"provider": "github", "title": "GitHub"},
    ]
    assert "settings" not in payload[0]
    assert "tools" not in payload[0]


def test_admin_user_model_payload_uses_the_same_strict_summary(monkeypatch):
    user = SimpleNamespace(id="admin-1", role="admin", group_id="group-1", last_model=None)
    model = _shared_model(
        settings={"system_instruction": "admin visible", "training_data": "true"},
        tools=["web_search"],
        access={"groups": ["group-1"]},
    )

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])
    monkeypatch.setattr("app.llm.utils.get_llm_provider", lambda db, provider_id: SimpleNamespace(id=provider_id))

    payload = list_user_models(db=SimpleNamespace(), user_id=user.id)

    assert set(payload[0]) == USER_MODEL_SUMMARY_FIELDS
    assert payload[0]["model_select_tools"] == ["web_search"]
    assert "admin visible" not in repr(payload)
    assert "group-1" not in repr(payload)


def test_dedicated_admin_model_payload_keeps_management_configuration(monkeypatch):
    user = SimpleNamespace(id="admin-1", role="admin", group_id="group-1", last_model=None)
    model = _shared_model(provider="openai_chat_completions", provider_id="provider-1")
    provider = SimpleNamespace(id="provider-1", name="My Internal Gateway")

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])
    monkeypatch.setattr("app.llm.utils.get_llm_provider", lambda db, provider_id: provider)

    payload = list_admin_models(db=SimpleNamespace(), user_id=user.id)

    assert payload[0]["provider"] == "openai_chat_completions"
    assert payload[0]["provider_name"] == "My Internal Gateway"
    assert payload[0]["provider_id"] == "provider-1"
    assert payload[0]["model_name"] == "openrouter/test"
    assert payload[0]["settings"] == {}
    assert payload[0]["access"] == {"groups": ["group-1"]}


def test_dedicated_admin_model_payload_rejects_non_admin_callers(monkeypatch):
    user = SimpleNamespace(id="user-1", role="user", group_id="group-1", last_model=None)
    _patch_common_model_dependencies(monkeypatch, user=user, models=[])

    try:
        list_admin_models(db=SimpleNamespace(), user_id=user.id)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("non-admin callers must not receive management records")


def test_user_model_payload_includes_agents_by_default_when_enabled(monkeypatch):
    user = SimpleNamespace(id="user-1", role="user", group_id="group-1", last_model=None)
    model = _shared_model(access={"everyone": True})
    agent_payload = {
        "id": "agent-1",
        "model_id": "agent-1",
        "agent_id": "agent-1",
        "user_id": "owner-secret",
        "base_model_id": "model-1",
        "name": "Research agent",
        "description": "SECRET instruction prefix",
        "model_icon": "bot",
        "provider": "openrouter",
        "provider_type": "openrouter",
        "provider_id": "provider-secret",
        "model_name": "upstream-secret",
        "capabilities": ["completion", "tools"],
        "status": "normal",
        "settings": {"system_instruction": "SECRET base prompt"},
        "tools": ["web_search"],
        "access": {"agent_owner_id": "owner-secret"},
        "model_kind": "agent",
        "is_custom_agent": True,
        "skill_id": "skill-secret",
        "instruction": "SECRET complete instruction",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "clone_share_id": "clone-secret",
        "live_share_id": "live-secret",
        "collaborate_share_id": "collaborate-secret",
        "owner_name": "Agent Owner",
        "is_subscribed": True,
        "share_type": "collaborate",
        "is_shared": True,
        "assets": [],
    }

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model], agents_enabled=True)
    monkeypatch.setattr("app.llm.utils.get_llm_provider", lambda db, provider_id: SimpleNamespace(id=provider_id))
    monkeypatch.setattr("app.agents.utils.list_accessible_agents", lambda *args, **kwargs: [agent_payload])

    payload = list_user_models(db=SimpleNamespace(), user_id=user.id)

    assert [item["model_id"] for item in payload] == ["model-1", "agent-1"]
    assert set(payload[1]) == USER_MODEL_SUMMARY_FIELDS | {"owner_name", "is_shared"}
    assert payload[1]["model_kind"] == "agent"
    assert payload[1]["description"] == "Grouped model"
    assert payload[1]["owner_name"] == "Agent Owner"
    assert payload[1]["is_shared"] is True
    assert "SECRET" not in repr(payload[1])
    assert "share_id" not in repr(payload[1])


def test_user_model_payload_can_exclude_agents_for_admin_model_management(monkeypatch):
    user = SimpleNamespace(id="admin-1", role="admin", group_id="group-1", last_model="agent-1")
    model = _shared_model(access={"everyone": True})

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model], agents_enabled=True)
    monkeypatch.setattr("app.llm.utils.get_llm_provider", lambda db, provider_id: SimpleNamespace(id=provider_id))
    monkeypatch.setattr(
        "app.agents.utils.list_accessible_agents",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("agents should not be queried")),
    )

    payload = list_user_models(db=SimpleNamespace(), user_id=user.id, include_agents=False)

    assert [item["model_id"] for item in payload] == ["model-1"]
    assert payload[0]["model_kind"] == "base"
    assert payload[0]["is_last"] is False


def test_foreign_user_managed_model_is_hidden(monkeypatch):
    """A private model owned by another user must not leak into model lists."""
    user = SimpleNamespace(
        id="user-1", role="user", group_id="group-1", last_model="personal-model"
    )
    model = _shared_model(
        id="personal-model",
        provider="openai",
        access={},
        meta={
            "user_managed": True,
            "owner_user_id": "user-2",
        },
    )

    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])

    assert list_user_models(db=SimpleNamespace(), user_id=user.id) == []


def test_foreign_user_managed_model_access_is_rejected(monkeypatch):
    """Direct authorization rejects a model owned by another user."""
    user = SimpleNamespace(id="user-1", role="user", group_id="group-1")
    model = _shared_model(
        id="personal-model",
        provider="openai",
        access={"everyone": False, "users": [user.id], "groups": []},
        meta={
            "user_managed": True,
            "owner_user_id": "user-2",
        },
    )
    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])
    monkeypatch.setattr("app.llm.utils.get_model", lambda _db, _model_id: model)

    with pytest.raises(HTTPException) as exc_info:
        ensure_user_access_to_model(user.id, model.id, SimpleNamespace())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "You do not have access to this model"


def test_user_managed_model_access_preserves_owner_access(monkeypatch):
    """An owner can use their personal model."""
    user = SimpleNamespace(id="user-1", role="user", group_id="group-1")
    model = _shared_model(
        id="personal-model",
        provider="openai",
        provider_id="personal-provider",
        access={"everyone": False, "users": [user.id], "groups": []},
        meta={
            "user_managed": True,
            "owner_user_id": user.id,
        },
    )
    _patch_common_model_dependencies(monkeypatch, user=user, models=[model])
    monkeypatch.setattr("app.llm.utils.get_model", lambda _db, _model_id: model)

    assert ensure_user_access_to_model(user.id, model.id, SimpleNamespace()) is True
