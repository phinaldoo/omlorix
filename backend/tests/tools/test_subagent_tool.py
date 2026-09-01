import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base
from app.chats.schemas import SendChatRequest
from app.chats import utils as chat_utils
from app.files.models import Files
from app.llm.helper import merge_settings, should_persist_files_in_file_block
from app.llm.models import Models
from app.tools.helper import resolve_parallel_subagent_tool_calls
from app.tools.schemas import tool_schemas
from app.tools.subagents.runtime import execute_subagent_tool
from app.tools.utils import resolve_enabled_tools


def _session(tables):
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"app": None}},
    )
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _drain_return(generator):
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return done.value


def _drain_items_and_return(generator):
    items = []
    try:
        while True:
            items.append(next(generator))
    except StopIteration as done:
        return items, done.value


def _model(model_id, *, access, name=None, capabilities=None, settings=None, tools=None):
    """Build a model fixture with explicit persisted settings and tools."""
    return Models(
        id=model_id,
        name=name or model_id,
        description=f"{name or model_id} description"[:100],
        model_icon="bot",
        provider="openai",
        provider_id="",
        model_name=model_id,
        settings=settings or {},
        capabilities=capabilities or ["completion", "tools"],
        tools=tools or [],
        access=access,
        meta={},
        status="normal",
        is_active=True,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_external_run_uses_research_worker_stream_and_durable_result(monkeypatch):
    import app.tools.subagents.runtime as subagent_runtime
    import app.workers.models as worker_models
    import app.workers.research as research_worker

    captured = {}

    class FakeQueueSession:
        def close(self):
            captured["session_closed"] = True

    def fake_enqueue(_db, **kwargs):
        captured["enqueue"] = kwargs
        return SimpleNamespace(id="worker-job-1")

    monkeypatch.setattr(research_worker, "external_research_enabled", lambda: True)
    monkeypatch.setattr(research_worker, "enqueue_subagent_job", fake_enqueue)
    monkeypatch.setattr(subagent_runtime, "SessionLocal", FakeQueueSession)
    monkeypatch.setattr(
        subagent_runtime.stream_hub,
        "start",
        lambda stream_id, chat_id, metadata=None: captured.update(
            stream_id=stream_id,
            stream_chat_id=chat_id,
            stream_metadata=metadata,
        ),
    )
    def fake_subscribe(_stream_id, from_seq=0, *, heartbeat_seconds=None):
        captured["stream_from_seq"] = from_seq
        captured["stream_heartbeat_seconds"] = heartbeat_seconds
        return iter(
            (
                '{"t":"subagent_evt","event":"start","seq":1}\n',
                '{"type":"ping"}\n',
                '{"t":"subagent_evt","event":"complete","seq":2}\n',
            )
        )

    monkeypatch.setattr(subagent_runtime.stream_hub, "subscribe", fake_subscribe)
    monkeypatch.setattr(
        subagent_runtime.cancel_registry,
        "is_cancelled",
        lambda _generation_id: False,
    )
    durable_result = {
        "content": "completed",
        "result": {"status": "completed"},
        "documents": [],
        "images": [],
        "videos": [],
        "audios": [],
        "youtube": [],
        "webpages": [],
        "tool_meta": {"subagent": {"status": "completed"}},
    }
    monkeypatch.setattr(
        worker_models,
        "wait_for_worker_job",
        lambda job_id, **_kwargs: durable_result
        if job_id == "worker-job-1"
        else None,
    )

    streamed, result = _drain_items_and_return(
        execute_subagent_tool(
            object(),
            tool_arguments={"action": "run", "model_id": "model-1", "prompt": "work"},
            user_id="user-1",
            group_id="stale-group",
            project_id="project-1",
            model_settings={"temperature": 0.2},
            chat_id="chat-1",
            chat_history=[{"role": "user", "content": "context"}],
            generation_id="generation-1",
            user_role="stale-role",
        )
    )

    assert [json.loads(line) for line in streamed] == [
        {"t": "subagent_evt", "event": "start"},
        {"t": "subagent_evt", "event": "complete"},
    ]
    assert result == durable_result
    assert captured["enqueue"]["user_id"] == "user-1"
    assert captured["enqueue"]["parent_generation_id"] == "generation-1"
    assert captured["stream_id"].startswith("research-subagent:")
    assert captured["stream_from_seq"] == 0
    assert captured["stream_heartbeat_seconds"] == 0.5
    assert captured["session_closed"] is True


def test_external_run_cancels_when_research_worker_does_not_start(monkeypatch):
    import app.tools.subagents.runtime as subagent_runtime
    import app.workers.research as research_worker

    captured = {}

    class FakeQueueSession:
        def close(self):
            captured["session_closed"] = True

    class StalledSubscription:
        def __iter__(self):
            return self

        def __next__(self):
            return '{"type":"ping"}\n'

        def close(self):
            captured["subscription_closed"] = True

    monkeypatch.setenv("RESEARCH_SUBAGENT_QUEUE_START_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(research_worker, "external_research_enabled", lambda: True)
    monkeypatch.setattr(
        research_worker,
        "enqueue_subagent_job",
        lambda _db, **_kwargs: SimpleNamespace(id="stalled-research-job"),
    )
    monkeypatch.setattr(subagent_runtime, "SessionLocal", FakeQueueSession)
    monkeypatch.setattr(subagent_runtime.stream_hub, "start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        subagent_runtime.stream_hub,
        "subscribe",
        lambda *_args, **_kwargs: StalledSubscription(),
    )
    monkeypatch.setattr(
        subagent_runtime.stream_hub,
        "mark_done",
        lambda stream_id, status="done": captured.update(
            marked_stream_id=stream_id,
            marked_status=status,
        ),
    )
    monkeypatch.setattr(
        subagent_runtime,
        "_cancel_external_subagent_job",
        lambda job_id, user_id: captured.update(cancelled=(job_id, user_id)),
    )
    monotonic_values = iter((100.0, 106.0))
    monkeypatch.setattr(
        subagent_runtime.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    streamed, result = _drain_items_and_return(
        execute_subagent_tool(
            object(),
            tool_arguments={"action": "run", "model_id": "model-1", "prompt": "work"},
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings=None,
            chat_id="chat-1",
            chat_history=None,
            generation_id="generation-1",
            user_role="user",
        )
    )

    assert streamed == []
    assert result["result"]["code"] == "subagent_queue_unavailable"
    assert captured["cancelled"] == ("stalled-research-job", "user-1")
    assert captured["marked_status"] == "failed"
    assert captured["marked_stream_id"].startswith("research-subagent:")
    assert captured["session_closed"] is True
    assert captured["subscription_closed"] is True


def test_subagent_schema_is_compact_and_has_no_system_prompt_or_model_enum():
    schema = tool_schemas["subagent"]
    properties = schema["parameters"]["properties"]

    assert "action" in properties
    assert "system_prompt" not in properties
    assert "enum" not in properties["model_id"]


def test_subagent_runtime_target_policy_survives_provider_setting_merge():
    selected = [{"type": "agent", "id": "agent-1"}]

    merged, _tools = merge_settings(
        {"temperature": 0.2},
        {"_runtime_subagent_targets": selected},
        {"temperature": {}},
    )

    assert merged["temperature"] == 0.2
    assert merged["_runtime_subagent_targets"] == selected


def test_provider_override_discards_caller_supplied_subagent_policy():
    supplied = [{"type": "model", "id": "caller-choice"}]
    authorized = [{"type": "agent", "id": "authorized-agent"}]

    without_authorized_targets = chat_utils._build_provider_settings_override(
        {"temperature": 0.2, "_runtime_subagent_targets": supplied},
        allow_custom_generation_parameter=True,
        subagent_targets=None,
    )
    with_authorized_targets = chat_utils._build_provider_settings_override(
        {"temperature": 0.2, "_runtime_subagent_targets": supplied},
        allow_custom_generation_parameter=True,
        subagent_targets=authorized,
    )

    assert without_authorized_targets == {"temperature": 0.2}
    assert with_authorized_targets == {
        "temperature": 0.2,
        "_runtime_subagent_targets": authorized,
    }


def test_chat_request_normalizes_and_deduplicates_typed_subagent_targets():
    request = SendChatRequest(
        message="hello",
        subagent_targets=[
            {"type": "agent", "id": " agent-1 "},
            {"type": "agent", "id": "agent-1"},
            {"type": "model", "id": "model-1"},
        ],
    )

    assert [(target.type, target.id) for target in request.subagent_targets] == [
        ("agent", "agent-1"),
        ("model", "model-1"),
    ]


def test_resolve_enabled_tools_exposes_subagent_schema():
    resolved = resolve_enabled_tools(["canvas", "subagent"], db=None, user_id="user-1")

    assert resolved["tool_list"] == ["canvas", "subagent"]
    assert [schema["name"] for schema in resolved["tool_schemas"]] == ["canvas", "subagent"]


def test_subagent_attachments_use_durable_file_blocks():
    """Subagent outputs must reload through the normal assistant file path."""
    assert should_persist_files_in_file_block("subagent") is True


def test_list_models_returns_only_models_the_user_can_access(monkeypatch):
    db = _session([Models.__table__])
    import app.llm.utils as llm_utils

    monkeypatch.setattr(
        llm_utils,
        "get_user",
        lambda _db, user_id: type("UserStub", (), {"id": user_id, "group_id": "group-1", "role": "user"})(),
    )
    db.add_all(
        [
            _model("public-model", name="Public", access={"everyone": True, "users": [], "groups": []}),
            _model("group-model", name="Group", access={"everyone": False, "users": [], "groups": ["group-1"]}),
            _model("private-model", name="Private", access={"everyone": False, "users": ["other-user"], "groups": []}),
        ]
    )
    db.commit()

    payload = _drain_return(
        execute_subagent_tool(
            db,
            tool_arguments={"action": "list_models"},
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings={},
            chat_id="chat-1",
            chat_history=[],
            generation_id="gen-1",
            user_role="user",
        )
    )

    result_ids = {item["id"] for item in payload["result"]["models"]}
    assert result_ids == {"public-model", "group-model"}
    assert all("model_name" in item for item in payload["result"]["models"])


def test_list_targets_searches_authorized_agents_without_exposing_private_configuration(monkeypatch):
    db = _session([Models.__table__])
    db.add(_model("base-model", name="Base Model", access={"everyone": True, "users": [], "groups": []}))
    db.commit()

    import app.tools.subagents.runtime as subagent_runtime

    monkeypatch.setattr(subagent_runtime, "ensure_user_access_to_model", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(subagent_runtime, "_agents_enabled_for_user", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        subagent_runtime,
        "list_accessible_agents",
        lambda *_args, **_kwargs: [
            {
                "id": "agent-1",
                "name": "Research Agent",
                "description": "Base Model",
                "base_model_id": "base-model",
                "is_shared": True,
                "instruction": "PRIVATE INSTRUCTION",
                "assets": [{"id": "PRIVATE ASSET"}],
                "collaborate_share_id": "PRIVATE SHARE TOKEN",
            },
            {
                "id": "agent-2",
                "name": "Draft Agent",
                "base_model_id": "base-model",
                "is_shared": False,
                "instruction": "OTHER PRIVATE INSTRUCTION",
            },
        ],
    )

    tool_payload = _drain_return(
        execute_subagent_tool(
            db,
            tool_arguments={
                "action": "list_targets",
                "query": "research",
                "target_type": "agent",
                "limit": 10,
            },
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings={},
            chat_id="chat-1",
            chat_history=[],
            generation_id="gen-1",
            user_role="user",
        )
    )
    payload = tool_payload["result"]

    assert payload["count"] == 1
    assert payload["targets"] == [
        {
            "type": "agent",
            "id": "agent-1",
            "name": "Research Agent",
            "description": "Base Model",
            "provider": "openai",
            "base_model_id": "base-model",
            "base_model_name": "Base Model",
            "is_shared": True,
        }
    ]
    serialized = json.dumps(payload)
    assert "PRIVATE INSTRUCTION" not in serialized
    assert "PRIVATE ASSET" not in serialized
    assert "PRIVATE SHARE TOKEN" not in serialized

    restricted_payload = _drain_return(
        execute_subagent_tool(
            db,
            tool_arguments={"action": "list_targets", "target_type": "agent"},
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings={"_runtime_subagent_targets": [{"type": "agent", "id": "agent-1"}]},
            chat_id="chat-1",
            chat_history=[],
            generation_id="gen-1",
            user_role="user",
        )
    )
    assert [target["id"] for target in restricted_payload["result"]["targets"]] == ["agent-1"]


def test_run_rejects_target_outside_the_user_selected_allowlist():
    db = _session([])

    payload = _drain_return(
        execute_subagent_tool(
            db,
            tool_arguments={"action": "run", "model_id": "model-1", "prompt": "do work"},
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings={"_runtime_subagent_targets": [{"type": "agent", "id": "agent-1"}]},
            chat_id="chat-1",
            chat_history=[],
            generation_id="gen-1",
            user_role="user",
        )
    )

    assert payload["result"]["code"] == "subagent_target_not_selected"


def test_run_rejects_system_prompt_as_unknown_argument():
    schema = tool_schemas["subagent"]

    assert schema["parameters"]["additionalProperties"] is False
    assert "system_prompt" not in schema["parameters"]["properties"]


def test_run_uses_selected_model_tools_without_parent_tool_or_mcp_inheritance(monkeypatch):
    """A subagent's permissions come only from its selected model."""
    db = _session([Models.__table__])
    db.add(
        _model(
            "model-1",
            access={"everyone": True, "users": [], "groups": []},
            settings={"enabled_mcp_servers": ["model-server"]},
            tools=["web_search", "mcp", "subagent"],
        )
    )
    db.commit()

    import app.chats.utils as chat_utils
    import app.tools.subagents.runtime as subagent_runtime

    monkeypatch.setattr(subagent_runtime, "ensure_user_access_to_model", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_utils, "_assert_generation_provider_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_utils, "_admit_rate_limited_chat_action", lambda *_args, **_kwargs: None)

    captured: dict[str, object] = {}

    def fake_dispatch_nested_provider(**kwargs):
        captured["settings_override"] = kwargs["settings_override"]
        captured["nested_model"] = kwargs["db_model"]
        yield '{"t":"c","d":"Nested "}'
        yield '{"t":"c","d":"reply"}'
        yield '{"t":"d"}'

    monkeypatch.setattr(subagent_runtime, "_dispatch_nested_provider", fake_dispatch_nested_provider)

    payload = _drain_return(
        execute_subagent_tool(
            db,
            tool_arguments={"action": "run", "model_id": "model-1", "prompt": "do work"},
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings={
                "_runtime_enabled_tools": ["weather", "subagent"],
                "enabled_mcp_servers": ["parent-server"],
            },
            chat_id="chat-1",
            chat_history=[],
            generation_id="gen-1",
            user_role="user",
        )
    )

    settings_override = captured["settings_override"]
    nested_model = captured["nested_model"]
    assert settings_override["enabled_tools"] == ["web_search", "mcp"]
    assert settings_override["_runtime_enabled_tools"] == ["web_search", "mcp"]
    assert "enabled_mcp_servers" not in settings_override
    assert nested_model.tools == ["web_search", "mcp"]
    assert nested_model.settings["enabled_mcp_servers"] == ["model-server"]
    assert payload["result"]["status"] == "completed"
    embedded_run = payload["tool_meta"]["subagent"]
    assert embedded_run["result"] == "Nested reply"
    assert [event["type"] for event in embedded_run["events"]] == [
        "start",
        "message_delta",
        "message_delta",
        "done",
        "complete",
    ]
    assert embedded_run["events"][1]["raw"]["d"] == "Nested "
    assert embedded_run["events"][2]["raw"]["d"] == "reply"


def test_saved_agent_run_receives_instruction_and_reference_assets(monkeypatch):
    db = _session([Models.__table__])
    base_model = _model(
        "model-1",
        access={"everyone": True, "users": [], "groups": []},
    )
    db.add(base_model)
    db.commit()

    import app.chats.utils as chat_utils
    import app.tools.subagents.runtime as subagent_runtime

    resolved_agent = SimpleNamespace(
        model_kind="agent",
        agent=SimpleNamespace(id="agent-1"),
        base_model=base_model,
        agent_instruction="Use the saved Agent instructions.",
        agent_skill_ids=[],
        asset_descriptors_by_category={
            "image": ["agent_asset:image-1"],
            "video": [],
            "audio": [],
            "document": ["agent_asset:document-1"],
        },
    )
    monkeypatch.setattr(subagent_runtime, "_agents_enabled_for_user", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        subagent_runtime,
        "resolve_selected_model_for_user",
        lambda *_args, **_kwargs: resolved_agent,
    )
    monkeypatch.setattr(chat_utils, "_assert_generation_provider_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_utils, "_admit_rate_limited_chat_action", lambda *_args, **_kwargs: None)

    captured: dict[str, object] = {}

    def fake_dispatch_nested_provider(**kwargs):
        captured["chat_history"] = kwargs["chat_history"]
        captured["system_instruction_sections"] = kwargs["system_instruction_sections"]
        yield '{"t":"c","d":"Agent reply"}'
        yield '{"t":"d"}'

    monkeypatch.setattr(subagent_runtime, "_dispatch_nested_provider", fake_dispatch_nested_provider)

    payload = _drain_return(
        execute_subagent_tool(
            db,
            tool_arguments={"action": "run", "agent_id": "agent-1", "task": "review this"},
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings={"_runtime_subagent_targets": [{"type": "agent", "id": "agent-1"}]},
            chat_id="chat-1",
            chat_history=[],
            generation_id="gen-1",
            user_role="user",
        )
    )

    task_block = captured["chat_history"][-1]["content"][0]
    assert task_block["images"] == ["agent_asset:image-1"]
    assert task_block["documents"] == ["agent_asset:document-1"]
    assert captured["system_instruction_sections"] == [
        {"title": "Agent Instructions", "content": "Use the saved Agent instructions."}
    ]
    assert payload["result"]["status"] == "completed"
    assert payload["tool_meta"]["subagent"]["meta"]["reference_asset_count"] == 2


def test_run_forwards_and_persists_nested_generated_files(monkeypatch):
    """Nested generic and presentation files must persist on the parent."""
    db = _session(
        [
            Models.__table__,
            Files.__table__,
        ]
    )
    db.add(_model("model-1", access={"everyone": True, "users": [], "groups": []}))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.add(
        Files(
            id="file-1",
            user_id="user-1",
            file_name="file-1.html",
            storage_provider="local",
            storage_key="user-1/file-1.html",
            storage_meta=None,
            file_category="document",
            file_type="text/html",
            file_size=42,
            project_id=None,
            folder_id=None,
            share=None,
            share_id=None,
            meta={"origin": "assistant", "original_filename": "index.html", "canvas": True},
            created_at=now,
            last_updated_at=now,
        )
    )
    db.add(
        Files(
            id="file-2",
            user_id="user-1",
            file_name="file-2.pptx",
            storage_provider="local",
            storage_key="user-1/file-2.pptx",
            storage_meta=None,
            file_category="document",
            file_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            file_size=84,
            project_id=None,
            folder_id=None,
            share=None,
            share_id=None,
            meta={"origin": "assistant", "original_filename": "quarterly-review.pptx"},
            created_at=now,
            last_updated_at=now,
        )
    )
    db.commit()

    import app.chats.utils as chat_utils
    import app.tools.subagents.runtime as subagent_runtime

    monkeypatch.setattr(subagent_runtime, "ensure_user_access_to_model", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_utils, "_assert_generation_provider_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_utils, "_admit_rate_limited_chat_action", lambda *_args, **_kwargs: None)

    def fake_dispatch_nested_provider(**_kwargs):
        yield json.dumps(
            {
                "t": "canvas_evt",
                "event": "saved",
                "data": {
                    "file_id": "file-1",
                    "file_name": "index.html",
                    "content_type": "html",
                    "created": True,
                    "content": "<!doctype html>",
                },
            }
        )
        yield json.dumps({"t": "f", "d": "file-1", "n": "index.html"})
        # Slide presentations publish their file only through the rich complete
        # event and intentionally do not emit a generic ``t: f`` event.
        yield json.dumps(
            {
                "t": "slide_presentation_evt",
                "event": "complete",
                "data": {
                    "file_id": "file-2",
                    "presentation_id": "presentation-1",
                    "title": "Quarterly review",
                    "slide_count": 8,
                },
            }
        )
        yield json.dumps({"t": "c", "d": "Created the page."})
        yield json.dumps({"t": "d"})

    monkeypatch.setattr(subagent_runtime, "_dispatch_nested_provider", fake_dispatch_nested_provider)

    streamed, payload = _drain_items_and_return(
        execute_subagent_tool(
            db,
            tool_arguments={"action": "run", "model_id": "model-1", "prompt": "create a page"},
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings={"_runtime_enabled_tools": ["canvas", "code_execution", "subagent"]},
            chat_id="chat-1",
            chat_history=[],
            generation_id="gen-1",
            user_role="user",
        )
    )

    decoded_stream = [json.loads(item) for item in streamed]
    assert any(item.get("t") == "canvas_evt" for item in decoded_stream)
    assert any(item.get("t") == "f" and item.get("d") == "file-1" for item in decoded_stream)
    assert any(item.get("t") == "slide_presentation_evt" for item in decoded_stream)
    assert any(item.get("t") == "subagent_evt" for item in decoded_stream)
    assert payload["documents"] == ["file-1", "file-2"]
    assert payload["result"]["artifacts"] == [
        {
            "file_id": "file-1",
            "name": "index.html",
            "file_category": "document",
            "file_type": "text/html",
        },
        {
            "file_id": "file-2",
            "name": "quarterly-review.pptx",
            "file_category": "document",
            "file_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        },
    ]

    file_record = db.query(Files).filter(Files.id == "file-1").one()
    assert file_record.meta["generated_by"] == "subagent"
    assert file_record.meta["subagent_id"] == payload["result"]["run_id"]
    presentation_record = db.query(Files).filter(Files.id == "file-2").one()
    assert presentation_record.meta["generated_by"] == "subagent"
    assert presentation_record.meta["subagent_id"] == payload["result"]["run_id"]
    assert payload["tool_meta"]["subagent"]["artifacts"][0]["file_id"] == "file-1"


def test_parallel_subagent_tool_calls_run_concurrently(monkeypatch):
    import app.tools.helper as tool_helper

    barrier = threading.Barrier(2)
    entered_prompts: list[str] = []

    class FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(tool_helper, "SessionLocal", lambda: FakeSession())

    def fake_resolve_tool_call(db, tool_name, tool_arguments, *_args, **_kwargs):
        def stream():
            prompt = tool_arguments["prompt"]
            entered_prompts.append(prompt)
            barrier.wait(timeout=10)
            yield f"stream:{prompt}"
            return {
                "content": f"done:{prompt}",
                "result": {"prompt": prompt},
                "documents": [],
                "images": [],
                "videos": [],
                "audios": [],
                "youtube": [],
                "webpages": [],
                "tool_meta": {"prompt": prompt},
            }

        return stream()

    monkeypatch.setattr(tool_helper, "resolve_tool_call", fake_resolve_tool_call)

    streamed_items, results = _drain_items_and_return(
        resolve_parallel_subagent_tool_calls(
            [
                {"arguments": {"action": "run", "model_id": "model-1", "prompt": "first"}},
                {"arguments": {"action": "run", "model_id": "model-1", "prompt": "second"}},
            ],
            user_id="user-1",
            group_id="group-1",
            project_id=None,
            model_settings={},
            chat_id="chat-1",
            chat_history=[],
            generation_id="gen-1",
            user_role="user",
        )
    )

    assert set(entered_prompts) == {"first", "second"}
    assert set(streamed_items) == {"stream:first", "stream:second"}
    assert [item["helper_payload"]["content"] for item in results] == ["done:first", "done:second"]
    assert [item["tool_error_message"] for item in results] == [None, None]
