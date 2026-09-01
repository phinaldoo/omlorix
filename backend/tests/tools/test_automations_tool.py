import sys
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.tools.automations import utils as automation_tool_utils
from app.tools import helper as tool_helper
from app.tools.schemas import tool_schemas


class FakeDb:
    """Minimal transaction-aware DB double for tool mutation tests."""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def refresh(self, _value):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _automation(**overrides):
    values = {
        "id": "automation-1",
        "user_id": "user-1",
        "title": "Morning brief",
        "icon": "folder",
        "icon_color": "#FF6B6B",
        "prompt": "Summarize the day",
        "model_id": "model-1",
        "schedule_rules": [],
        "schedule_timezone": None,
        "skill_id": None,
        "note_ids": [],
        "file_ids": [],
        "is_active": True,
        "last_triggered_at": None,
        "created_at": None,
        "last_updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _trigger(**overrides):
    values = {
        "id": "trigger-1",
        "automation_id": "automation-1",
        "name": None,
        "is_enabled": True,
        "token_prefix": "cuiwh_secretpref",
        "payload_mode": "append",
        "include_headers": False,
        "allowed_header_names": [],
        "max_body_bytes": 262144,
        "rate_limit_per_minute": 30,
        "last_triggered_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_automations_information_lists_available_choices(monkeypatch):
    monkeypatch.setattr(
        automation_tool_utils,
        "_list_accessible_model_options",
        lambda _db, _user_id: [{"id": "model-1", "name": "Useful Model"}],
    )
    monkeypatch.setattr(
        automation_tool_utils,
        "_list_model_eligible_mcp_options",
        lambda _db, _user_id, _models: {
            "model-1": [{"id": "server-1", "name": "Notion", "description": ""}],
        },
    )
    monkeypatch.setattr(
        automation_tool_utils,
        "list_skills",
        lambda _db, _user_id: [
            SimpleNamespace(id="skill-1", user_id="user-1", name="Reporter", description="Writes reports")
        ],
    )
    monkeypatch.setattr(automation_tool_utils, "get_subscribed_skills", lambda _db, _user_id: [])
    monkeypatch.setattr(automation_tool_utils, "get_user_group_setting_value", lambda *_args: [])
    monkeypatch.setattr(automation_tool_utils, "load_skill_markdown_fields", lambda *_args: {})

    result = automation_tool_utils.automations_tool(
        db=object(),
        user_id="user-1",
        type="information",
    )

    assert result["categories"]["information"]["required_inputs"] == ["type"]
    assert result["available_models"] == [{"id": "model-1", "name": "Useful Model"}]
    assert result["available_mcp_servers_by_model"]["model-1"][0]["id"] == "server-1"
    assert result["available_skills"][0]["id"] == "skill-1"
    assert "mcp_server_ids" in result["categories"]["create"]["optional_inputs"]
    assert "mcp_server_ids" in result["categories"]["edit"]["optional_inputs"]
    assert "webhook_trigger" not in result["categories"]["create"]["optional_inputs"]
    assert "webhook_trigger" not in result["categories"]["edit"]["optional_inputs"]
    assert "must manage" in result["webhook_policy"]
    assert result["icon_options"][0] == {"number": 1, "label": "Folder icon"}
    assert len(result["icon_options"]) == 17
    assert result["color_options"][1]["value"] == "#FF8A65"


def test_automations_schema_does_not_offer_webhook_mutation():
    """Models must not receive a webhook mutation argument in their tool schema."""
    automation_schema = tool_schemas["automations"]
    operation_schemas = automation_schema["parameters"]["anyOf"]

    assert all(
        "webhook_trigger" not in branch["properties"]
        for branch in operation_schemas
    )
    assert "user-managed" in automation_schema["description"]


def test_automations_schema_gives_read_operations_exact_minimal_payloads():
    """Read-only calls must not advertise any mutation-only default fields."""
    operation_schemas = tool_schemas["automations"]["parameters"]["anyOf"]
    branches = {
        branch["properties"]["type"]["enum"][0]: branch
        for branch in operation_schemas
    }

    assert set(branches) == {"information", "list", "create", "edit", "delete"}
    for operation in ("information", "list"):
        assert branches[operation]["required"] == ["type"]
        assert set(branches[operation]["properties"]) == {"type"}
        assert branches[operation]["additionalProperties"] is False
    assert {"type", "title", "prompt", "model_id"}.issubset(
        branches["create"]["required"]
    )
    assert branches["edit"]["required"] == ["type", "automation_id"]
    assert set(branches["delete"]["properties"]) == {"type", "automation_id"}


def test_automations_operation_schema_is_accepted_by_google_tool_declarations():
    """The discriminated schema must remain portable across provider adapters."""
    from app.llm.google_aistudio.utils import _build_aistudio_tools_payload

    tools = _build_aistudio_tools_payload([tool_schemas["automations"]])

    assert len(tools) == 1
    assert len(tools[0].function_declarations) == 1
    declaration = tools[0].function_declarations[0]
    assert declaration.name == "automations"
    assert len(declaration.parameters.any_of) == 5


@pytest.mark.parametrize("operation", ["information", "list"])
def test_automations_dispatch_strips_stale_generic_defaults_for_reads(monkeypatch, operation):
    """Provider calls created from the former broad schema remain harmless."""
    automation_dispatch = MagicMock(return_value={"automations": []})
    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_helper, "_ensure_feature_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_helper, "automations_tool", automation_dispatch)

    resolver = tool_helper.resolve_tool_call(
        db=MagicMock(),
        tool_name="automations",
        tool_arguments={
            "type": operation,
            "automation_id": "",
            "title": "",
            "prompt": "",
            "model_id": "",
            "icon": {"invalid": "but irrelevant"},
            "icon_color": [],
            "schedule_rules": [],
            "schedule_timezone": "",
            "skill_id": "",
            "note_ids": [],
            "file_ids": [],
            "mcp_server_ids": [],
            "is_active": False,
        },
        user_id="user-1",
        group_id=None,
        project_id=None,
    )

    with pytest.raises(StopIteration) as done:
        next(resolver)

    automation_dispatch.assert_called_once_with(
        db=ANY,
        user_id="user-1",
        type=operation,
    )
    assert json.loads(done.value.value["content"]) == {"automations": []}


def test_automations_dispatch_rejects_stale_webhook_arguments(monkeypatch):
    """Previously advertised webhook arguments cannot bypass the new schema."""
    automation_dispatch = MagicMock()
    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_helper, "_ensure_feature_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_helper, "automations_tool", automation_dispatch)

    resolver = tool_helper.resolve_tool_call(
        db=MagicMock(),
        tool_name="automations",
        tool_arguments={
            "type": "edit",
            "automation_id": "automation-1",
            "webhook_trigger": {"operation": "rotate"},
        },
        user_id="user-1",
        group_id=None,
        project_id=None,
    )

    error_event = json.loads(next(resolver))
    assert error_event["t"] == "t_e"
    assert error_event["d"]["error_code"] == "automations_webhook_user_managed"
    with pytest.raises(ValueError, match="must manage the webhook themselves"):
        next(resolver)

    automation_dispatch.assert_not_called()


def test_automations_disabled_error_is_actionable_and_terminal(monkeypatch):
    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tool_helper,
        "_ensure_feature_enabled",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("disabled")),
    )

    resolver = tool_helper.resolve_tool_call(
        db=MagicMock(),
        tool_name="automations",
        tool_arguments={"type": "list"},
        user_id="user-1",
        group_id=None,
        project_id=None,
        tool_call_id="call-1",
    )

    event = json.loads(next(resolver))
    assert event == {
        "t": "t_e",
        "d": {
            "name": "automations",
            "error": "Automations are disabled for your group.",
            "id": "call-1",
            "error_code": "automations_feature_disabled",
        },
    }
    with pytest.raises(tool_helper.SafeToolExecutionError):
        next(resolver)


def test_automations_information_excludes_agent_model_entries(monkeypatch):
    """Agent chat selections must not reach base-model MCP resolution."""
    model_lookups = []

    monkeypatch.setattr(
        "app.llm.utils.list_user_models",
        lambda _db, _user_id: [
            {
                "id": "model-1",
                "name": "Automation Model",
                "model_kind": "base",
                "is_custom_agent": False,
            },
            {
                "id": "agent-1",
                "name": "Chat Agent",
                "model_kind": "agent",
                "is_custom_agent": True,
                "base_model_id": "model-1",
            },
        ],
    )

    def fake_get_model(_db, model_id):
        # Recording the IDs proves the information response never tries to
        # dereference an agent ID through the base Models table.
        model_lookups.append(model_id)
        if model_id == "agent-1":
            raise AssertionError("agent IDs must not reach get_model()")
        return SimpleNamespace(settings={}, tools=[])

    monkeypatch.setattr("app.llm.models.get_model", fake_get_model)
    monkeypatch.setattr(
        "app.tools.utils.resolve_enabled_tools",
        lambda *_args, **_kwargs: {"mcp_requested": False},
    )
    monkeypatch.setattr(automation_tool_utils, "list_skills", lambda _db, _user_id: [])
    monkeypatch.setattr(automation_tool_utils, "get_subscribed_skills", lambda _db, _user_id: [])
    monkeypatch.setattr(automation_tool_utils, "get_user_group_setting_value", lambda *_args: [])

    result = automation_tool_utils.automations_tool(
        db=object(),
        user_id="user-1",
        type="information",
    )

    assert result["available_models"] == [
        {"id": "model-1", "name": "Automation Model"}
    ]
    assert result["available_mcp_servers_by_model"] == {"model-1": []}
    assert model_lookups == ["model-1"]


def test_model_mcp_options_skip_a_model_removed_after_listing(monkeypatch):
    """A stale model-list snapshot must not break the information response."""

    def missing_model(_db, _model_id):
        raise HTTPException(status_code=404, detail="Model not found!")

    monkeypatch.setattr("app.llm.models.get_model", missing_model)

    result = automation_tool_utils._list_model_eligible_mcp_options(
        db=object(),
        user_id="user-1",
        model_options=[{"id": "deleted-model", "name": "Deleted Model"}],
    )

    assert result == {}


def test_direct_read_operation_does_not_normalize_mutation_fields(monkeypatch):
    monkeypatch.setattr(automation_tool_utils, "db_list_automations", lambda *_args: [])
    monkeypatch.setattr(
        automation_tool_utils,
        "_normalize_numbered_icon",
        lambda _value: (_ for _ in ()).throw(AssertionError("must not normalize read payload")),
    )

    assert automation_tool_utils.automations_tool(
        db=object(),
        user_id="user-1",
        type="list",
        icon={"irrelevant": True},
        is_active=False,
    ) == {"automations": []}


def test_create_normalizes_numbered_icon_and_color(monkeypatch):
    captured_create = {}

    def fake_create_automation(**kwargs):
        captured_create.update(kwargs)
        return _automation(icon=kwargs["icon"], icon_color=kwargs["icon_color"])

    monkeypatch.setattr(automation_tool_utils, "db_create_automation", fake_create_automation)
    monkeypatch.setattr(automation_tool_utils, "get_webhook_trigger_for_automation", lambda *_args: None)
    monkeypatch.setattr(
        automation_tool_utils,
        "stage_tool_audit_action",
        lambda *_args, **_kwargs: None,
    )

    result = automation_tool_utils.automations_tool(
        db=FakeDb(),
        user_id="user-1",
        type="create",
        title="Morning brief",
        prompt="Summarize the day",
        model_id="model-1",
        mcp_server_ids=["notion-server"],
        icon=1,
        icon_color=2,
    )

    assert captured_create["icon"] == "folder"
    assert captured_create["icon_color"] == "#FF8A65"
    assert captured_create["mcp_server_ids"] == ["notion-server"]
    assert result["status"] == "success"
    assert "webhook_trigger_result" not in result


def test_automation_mutations_emit_content_free_tool_audits(monkeypatch):
    audit_calls = []
    automation = _automation(
        schedule_rules=[{"days": [0], "times": ["08:00"]}],
        schedule_timezone="Europe/Berlin",
        skill_id="skill-1",
        note_ids=["note-1"],
        file_ids=["file-1"],
        mcp_server_ids=["server-1"],
    )
    monkeypatch.setattr(
        automation_tool_utils,
        "stage_tool_audit_action",
        lambda db, user_id, action, **kwargs: audit_calls.append(
            {"db": db, "user_id": user_id, "action": action, **kwargs}
        ),
    )
    monkeypatch.setattr(
        automation_tool_utils,
        "db_create_automation",
        lambda **_kwargs: automation,
    )
    monkeypatch.setattr(
        automation_tool_utils,
        "db_update_automation",
        lambda **_kwargs: automation,
    )
    monkeypatch.setattr(
        automation_tool_utils,
        "get_webhook_trigger_for_automation",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        automation_tool_utils,
        "db_delete_automation",
        lambda **_kwargs: None,
    )

    automation_tool_utils.automations_tool(
        db=FakeDb(),
        user_id="user-1",
        type="create",
        title="Private title",
        prompt="Private prompt",
        model_id="model-1",
        schedule_rules=automation.schedule_rules,
        schedule_timezone=automation.schedule_timezone,
        skill_id="skill-1",
        note_ids=["note-1"],
        file_ids=["file-1"],
        mcp_server_ids=["server-1"],
    )
    automation_tool_utils.automations_tool(
        db=FakeDb(),
        user_id="user-1",
        type="edit",
        automation_id="automation-1",
        title="Changed private title",
        prompt="Changed private prompt",
        is_active=False,
    )
    automation_tool_utils.automations_tool(
        db=FakeDb(),
        user_id="user-1",
        type="delete",
        automation_id="automation-1",
    )

    assert [call["action"] for call in audit_calls] == [
        "AUTOMATION_CREATED",
        "AUTOMATION_UPDATED",
        "AUTOMATION_DELETED",
    ]
    assert all(isinstance(call["db"], FakeDb) for call in audit_calls)
    assert audit_calls[0]["details"] == {
        "automation_id": "automation-1",
        "model_id": "model-1",
        "schedule_rule_count": 1,
        "schedule_timezone": "Europe/Berlin",
        "skill_id": "skill-1",
        "note_count": 1,
        "file_count": 1,
        "connection_count": 1,
        "is_active": True,
    }
    assert audit_calls[1]["details"] == {
        "automation_id": "automation-1",
        "updated_fields": ["is_active", "prompt", "title"],
    }
    assert audit_calls[2]["details"] == {"automation_id": "automation-1"}
    assert "Private prompt" not in repr(audit_calls)
    assert "Changed private prompt" not in repr(audit_calls)


def test_webhook_mutation_is_rejected_before_automation_create(monkeypatch):
    create_called = False

    def fake_create_automation(**_kwargs):
        nonlocal create_called
        create_called = True
        return _automation()

    monkeypatch.setattr(automation_tool_utils, "db_create_automation", fake_create_automation)

    try:
        automation_tool_utils.automations_tool(
            db=FakeDb(),
            user_id="user-1",
            type="create",
            title="Morning brief",
            prompt="Summarize the day",
            model_id="model-1",
            webhook_trigger={"payload_mode": "template"},
        )
    except ValueError as exc:
        assert "must manage the webhook themselves" in str(exc)
    else:
        raise AssertionError("Webhook mutation should be rejected")

    assert create_called is False


def test_delete_rejects_automation_with_webhook_trigger(monkeypatch):
    delete_called = False

    monkeypatch.setattr(
        automation_tool_utils,
        "get_webhook_trigger_for_automation",
        lambda *_args: _trigger(),
    )

    def fake_delete_automation(**_kwargs):
        nonlocal delete_called
        delete_called = True

    monkeypatch.setattr(automation_tool_utils, "db_delete_automation", fake_delete_automation)

    try:
        automation_tool_utils.automations_tool(
            db=FakeDb(),
            user_id="user-1",
            type="delete",
            automation_id="automation-1",
        )
    except ValueError as exc:
        assert "must remove the webhook themselves" in str(exc)
    else:
        raise AssertionError("Webhook-backed automation deletion should be rejected")

    assert delete_called is False
