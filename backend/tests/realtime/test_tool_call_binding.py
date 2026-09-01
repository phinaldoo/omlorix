from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.realtime.service import (  # noqa: E402
    RealtimeSessionRuntime,
    consume_realtime_pending_tool_call,
    get_realtime_completed_tool_call_response,
    register_realtime_pending_tool_call,
    register_realtime_tool_result,
    realtime_allowed_tool_names,
    validate_realtime_tool_arguments,
)


def _runtime() -> RealtimeSessionRuntime:
    return RealtimeSessionRuntime(
        id="session-1",
        user_id="user-1",
        group_id="group-1",
        chat_id="chat-1",
        project_id=None,
        model_id="model-1",
        base_model_id="base-model-1",
        agent_id=None,
        model_settings={},
        skill_id=None,
        skill_content=None,
        agent_instruction=None,
        provider="openai",
        provider_id="provider-1",
        realtime_model="gpt-realtime",
        voice="alloy",
        settings={},
        tools=["web_search"],
        tool_schemas=[
            {
                "type": "function",
                "name": "custom_tool",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }
        ],
    )


def test_realtime_allowed_tool_names_include_runtime_tools_and_schemas():
    assert realtime_allowed_tool_names(_runtime()) == {"web_search", "custom_tool"}


def test_pending_tool_call_rejects_tool_outside_session_allowlist():
    with pytest.raises(HTTPException) as exc:
        register_realtime_pending_tool_call(_runtime(), call_id="call-1", tool_name="code_execution")

    assert exc.value.status_code == 403


def test_pending_tool_call_is_bound_and_consumed_once():
    runtime = _runtime()

    assert register_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search") == {"status": "registered"}
    consume_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search")

    assert "call-1" not in runtime.pending_tool_calls
    assert "call-1" in runtime.consumed_tool_call_ids
    with pytest.raises(HTTPException) as exc:
        consume_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search")
    assert exc.value.status_code == 409


def test_tool_call_id_cannot_be_consumed_for_different_tool():
    runtime = _runtime()
    register_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search")

    with pytest.raises(HTTPException) as exc:
        consume_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="custom_tool")

    assert exc.value.status_code == 403


def test_completed_tool_call_can_be_replayed_for_retry():
    runtime = _runtime()
    register_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search")
    consume_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search")
    register_realtime_tool_result(
        runtime,
        call_id="call-1",
        tool_name="web_search",
        arguments={"q": "weather"},
        output_string='{"status":"ok"}',
        events=[{"type": "tool.completed"}],
    )

    assert register_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search") == {"status": "completed"}
    assert get_realtime_completed_tool_call_response(runtime, call_id="call-1", tool_name="web_search") == {
        "output": '{"status":"ok"}',
        "events": [{"type": "tool.completed"}],
    }


def test_completed_tool_call_rejects_retry_for_different_tool():
    runtime = _runtime()
    register_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search")
    consume_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="web_search")
    register_realtime_tool_result(
        runtime,
        call_id="call-1",
        tool_name="web_search",
        arguments={},
        output_string="ok",
    )

    with pytest.raises(HTTPException) as exc:
        register_realtime_pending_tool_call(runtime, call_id="call-1", tool_name="custom_tool")

    assert exc.value.status_code == 409


def test_realtime_tool_arguments_must_match_advertised_schema():
    runtime = _runtime()

    validate_realtime_tool_arguments(
        runtime,
        tool_name="custom_tool",
        arguments={"query": "weather"},
    )

    with pytest.raises(HTTPException) as exc:
        validate_realtime_tool_arguments(
            runtime,
            tool_name="custom_tool",
            arguments={"unexpected": True},
        )

    assert exc.value.status_code == 422
    assert "invalid" in str(exc.value.detail).lower()


def test_realtime_tool_arguments_have_a_strict_size_limit():
    runtime = _runtime()

    with pytest.raises(HTTPException) as exc:
        validate_realtime_tool_arguments(
            runtime,
            tool_name="custom_tool",
            arguments={"query": "x" * (65 * 1024)},
        )

    assert exc.value.status_code == 413
