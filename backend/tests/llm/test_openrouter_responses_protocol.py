"""Focused protocol tests for OpenRouter's stateless Responses API."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from app.llm.openrouter.responses import (
    OpenRouterFunctionCallAccumulator,
    apply_openrouter_responses_settings,
    extract_openrouter_incomplete_reason,
    extract_openrouter_response_error,
    extract_openrouter_response_usage,
    openrouter_response_error_http_status,
)


def _run_openrouter_chat_with_events(
    monkeypatch,
    *,
    events: list[dict] | list[list[dict]],
    reasoning_exclude: bool,
    with_tool: bool = False,
) -> dict:
    """Exercise the real OpenRouter chat stream with deterministic SSE events.

    The provider request and database write are replaced at their external
    boundaries, while request construction, stream parsing, reasoning state,
    and assistant-message finalization all run through production code.
    """
    from app.chats import models as chat_models
    from app.llm.openrouter import utils as openrouter_utils

    event_batches = events if events and isinstance(events[0], list) else [events]

    class FakeResponse:
        """Provide a minimal successful streaming response for ``requests``."""

        status_code = 200
        text = ""

        def __init__(self, response_events):
            self.response_events = response_events

        def iter_lines(self):
            for event in self.response_events:
                yield f"data: {json.dumps(event)}".encode()
            yield b"data: [DONE]"

    posted_payloads: list[dict] = []
    posted_headers: list[dict] = []
    saved_messages: list[dict] = []

    def fake_post(_url, **kwargs):
        response_index = len(posted_payloads)
        posted_payloads.append(kwargs["json"])
        posted_headers.append(kwargs["headers"])
        return FakeResponse(event_batches[response_index])

    def fake_create_chat_message(_db, chat_id, model_id, role, **kwargs):
        saved_messages.append(
            {
                "chat_id": chat_id,
                "model_id": model_id,
                "role": role,
                **kwargs,
            }
        )
        return SimpleNamespace(id="assistant-1")

    # Keep the harness focused on the provider stream and persistence boundary.
    # File hydration, system-instruction assembly, cancellation, and statistics
    # are orthogonal to whether returned reasoning reaches the saved message.
    monkeypatch.setattr(
        openrouter_utils,
        "reformat_chat_history",
        lambda *_args, **_kwargs: {"formatted": []},
    )
    monkeypatch.setattr(
        openrouter_utils,
        "get_default_system_instruction",
        lambda *_args, **_kwargs: "system",
    )
    monkeypatch.setattr(
        openrouter_utils,
        "append_system_instruction_sections",
        lambda base, _sections: base,
    )
    monkeypatch.setattr(
        openrouter_utils,
        "interruptible_provider_stream",
        lambda iterable, *_args, **_kwargs: iterable,
    )
    monkeypatch.setattr(openrouter_utils.requests, "post", fake_post)
    monkeypatch.setattr(
        openrouter_utils,
        "create_llm_generation_statistic",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        chat_models,
        "create_chat_message",
        fake_create_chat_message,
    )

    if with_tool:
        from app.tools import utils as tool_utils

        monkeypatch.setattr(
            tool_utils,
            "resolve_enabled_tools",
            lambda *_args, **_kwargs: {
                "tool_schemas": [
                    {
                        "name": "weather",
                        "description": "Return deterministic test weather.",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
                "tool_list": ["weather"],
                "mcp_requested": False,
            },
        )

        def fake_resolve_tool_call(*_args, **_kwargs):
            """Return a completed tool result through the generator contract."""
            if False:
                yield None
            return {"result": "sunny", "content": "sunny"}

        monkeypatch.setattr(
            openrouter_utils,
            "resolve_tool_call",
            fake_resolve_tool_call,
        )

    chunks = list(
        openrouter_utils.openrouter_chat(
            "chat-1",
            [],
            object(),
            user_id="user-1",
            byok={
                "api_key": "test-key",
                "api_base_url": "https://openrouter.ai/api/v1",
                "model_name": "test/model",
                "capabilities": ["tools"] if with_tool else [],
                "settings": {},
            },
            settings_override={
                "reasoning_enabled": True,
                "reasoning_effort": "high",
                "reasoning_exclude": reasoning_exclude,
            },
            user_role="user",
        )
    )
    return {
        "chunks": [json.loads(chunk) for chunk in chunks],
        "payloads": posted_payloads,
        "headers": posted_headers,
        "saved_messages": saved_messages,
    }


def _reasoning_response_events() -> list[dict]:
    """Return reasoning events covering canonical and compatibility shapes."""
    return [
        {
            "type": "response.reasoning.delta",
            "delta": "SECRET live reasoning",
            "reasoning_details": [
                {
                    "index": 0,
                    "type": "reasoning.text",
                    "text": "SECRET structured reasoning",
                }
            ],
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "reasoning",
                "id": "rs_1",
                "encrypted_content": "opaque-secret-state",
                "summary": [
                    {
                        "type": "summary_text",
                        "text": "SECRET reasoning summary",
                    }
                ],
            },
        },
        {"type": "response.output_text.delta", "delta": "Final answer"},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 4,
                    "total_tokens": 7,
                },
            },
        },
    ]


def test_excluded_responses_reasoning_never_reaches_persisted_chat_content(
    monkeypatch,
):
    """Hidden reasoning must be transient and absent from every storage sink."""
    result = _run_openrouter_chat_with_events(
        monkeypatch,
        events=_reasoning_response_events(),
        reasoning_exclude=True,
    )

    # OpenRouter's Responses schema does not support reasoning.exclude, so the
    # application must enforce the persistence boundary locally.
    assert result["payloads"][0]["reasoning"] == {
        "enabled": True,
        "effort": "high",
    }
    assert result["headers"][0]["HTTP-Referer"] == "https://github.com/phinaldoo/omlorix"
    assert result["headers"][0]["X-OpenRouter-Title"] == "Omlorix"
    assert "X-Title" not in result["headers"][0]
    assert not any(chunk.get("t") == "r" for chunk in result["chunks"])

    saved_content = result["saved_messages"][0]["content"]
    assert [block["type"] for block in saved_content] == ["content"]
    assert "SECRET" not in json.dumps(saved_content)
    assert "opaque-secret-state" not in json.dumps(saved_content)


def test_visible_responses_reasoning_still_streams_and_persists(monkeypatch):
    """Users who do not hide reasoning retain the existing resumable behavior."""
    result = _run_openrouter_chat_with_events(
        monkeypatch,
        events=_reasoning_response_events(),
        reasoning_exclude=False,
    )

    assert any(
        chunk.get("t") == "r" and chunk.get("d") == "SECRET live reasoning"
        for chunk in result["chunks"]
    )
    saved_content = result["saved_messages"][0]["content"]
    assert [block["type"] for block in saved_content] == ["reasoning", "content"]
    assert saved_content[0]["content"] == "SECRET live reasoning"
    assert saved_content[0]["meta"]["openrouter_reasoning_details"][0]["text"] == (
        "SECRET structured reasoning"
    )
    assert (
        saved_content[0]["meta"]["openrouter_responses_reasoning_items"][0][
            "encrypted_content"
        ]
        == "opaque-secret-state"
    )


def test_excluded_reasoning_is_transiently_replayed_for_tool_continuation(
    monkeypatch,
):
    """Tool continuity may use hidden reasoning in memory but never in storage."""
    reasoning_item = {
        "type": "reasoning",
        "id": "rs_tool_1",
        "encrypted_content": "transient-tool-reasoning",
        "summary": [],
    }
    result = _run_openrouter_chat_with_events(
        monkeypatch,
        events=[
            [
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": reasoning_item,
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 1,
                    "item": {
                        "type": "function_call",
                        "id": "fc_item_1",
                        "call_id": "call_1",
                        "name": "weather",
                        "arguments": "{}",
                    },
                },
                {
                    "type": "response.completed",
                    "response": {"status": "completed", "usage": {}},
                },
            ],
            [
                {
                    "type": "response.output_text.delta",
                    "delta": "It is sunny.",
                },
                {
                    "type": "response.completed",
                    "response": {"status": "completed", "usage": {}},
                },
            ],
        ],
        reasoning_exclude=True,
        with_tool=True,
    )

    assert len(result["payloads"]) == 2
    assert reasoning_item in result["payloads"][1]["input"]

    saved_content = result["saved_messages"][0]["content"]
    assert "reasoning" not in {block["type"] for block in saved_content}
    assert "transient-tool-reasoning" not in json.dumps(saved_content)
    assert any(block["type"] == "content" for block in saved_content)


def test_responses_settings_translate_wire_names_and_drop_chat_only_fields():
    payload = {"model": "test/model", "input": []}
    settings = {
        "supported_parameters": [
            "temperature",
            "max_tokens",
            "response_format",
            "verbosity",
            "reasoning",
            "tool_choice",
            "seed",
            "stop",
        ],
        "temperature": 0.25,
        "max_tokens": 321,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "schema": {"type": "object"},
                "strict": True,
            },
        },
        "verbosity": "low",
        "reasoning_enabled": True,
        "reasoning_effort": "high",
        "reasoning_exclude": True,
        "tool_choice": {
            "type": "function",
            "function": {"name": "lookup"},
        },
        "seed": 7,
        "stop": ["END"],
    }

    apply_openrouter_responses_settings(payload, settings)

    assert payload == {
        "model": "test/model",
        "input": [],
        "temperature": 0.25,
        "max_output_tokens": 321,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "answer",
                "schema": {"type": "object"},
                "strict": True,
            },
            "verbosity": "low",
        },
        "reasoning": {"enabled": True, "effort": "high"},
        "tool_choice": {"type": "function", "name": "lookup"},
    }


def test_responses_settings_respect_model_capabilities():
    payload = {}

    apply_openrouter_responses_settings(
        payload,
        {
            "supported_parameters": ["top_p"],
            "temperature": 0.5,
            "top_p": 0.8,
            "max_tokens": 99,
            "reasoning_enabled": True,
        },
    )

    assert payload == {"top_p": 0.8}


def test_parallel_function_calls_keep_ids_names_and_arguments_isolated():
    accumulator = OpenRouterFunctionCallAccumulator()
    accumulator.register_output_event(
        {
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "fc_item_1",
                "call_id": "call_1",
                "name": "weather",
                "arguments": "",
            },
        }
    )
    accumulator.register_output_event(
        {
            "output_index": 1,
            "item": {
                "type": "function_call",
                "id": "fc_item_2",
                "call_id": "call_2",
                "name": "calendar",
                "arguments": "",
            },
        }
    )

    # OpenRouter may interleave deltas for parallel calls. The item ID, rather
    # than arrival order, is the stable association key.
    accumulator.append_delta(
        {"item_id": "fc_item_2", "output_index": 1, "delta": '{"day"'}
    )
    accumulator.append_delta(
        {"item_id": "fc_item_1", "output_index": 0, "delta": '{"city"'}
    )
    accumulator.append_delta(
        {"item_id": "fc_item_2", "output_index": 1, "delta": ':"Tue"}'}
    )
    accumulator.append_delta(
        {"item_id": "fc_item_1", "output_index": 0, "delta": ':"Berlin"}'}
    )
    accumulator.finalize_arguments(
        {
            "item_id": "fc_item_1",
            "output_index": 0,
            "name": "weather",
            "arguments": '{"city":"Berlin"}',
        }
    )
    accumulator.finalize_arguments(
        {
            "item_id": "fc_item_2",
            "output_index": 1,
            "name": "calendar",
            "arguments": '{"day":"Tue"}',
        }
    )

    assert accumulator.finalized_calls() == [
        {
            "item_id": "fc_item_1",
            "call_id": "call_1",
            "name": "weather",
            "arguments": '{"city":"Berlin"}',
            "output_index": 0,
            "finalized": True,
        },
        {
            "item_id": "fc_item_2",
            "call_id": "call_2",
            "name": "calendar",
            "arguments": '{"day":"Tue"}',
            "output_index": 1,
            "finalized": True,
        },
    ]
    assert accumulator.finalized_calls() == []


def test_terminal_response_helpers_read_nested_responses_fields():
    failed = {
        "type": "response.failed",
        "response": {
            "status": "failed",
            "error_type": "rate_limit_exceeded",
            "error": {"code": "rate_limit_exceeded", "message": "Slow down"},
            "usage": {"input_tokens": 5, "output_tokens": 1},
        },
    }
    incomplete = {
        "type": "response.incomplete",
        "response": {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
        },
    }

    assert extract_openrouter_response_usage(failed) == {
        "input_tokens": 5,
        "output_tokens": 1,
    }
    assert extract_openrouter_response_error(failed) == {
        "code": "rate_limit_exceeded",
        "error_type": "rate_limit_exceeded",
        "message": "Slow down",
        "status": "failed",
    }
    assert (
        openrouter_response_error_http_status(extract_openrouter_response_error(failed))
        == 429
    )
    assert extract_openrouter_incomplete_reason(incomplete) == "max_output_tokens"
