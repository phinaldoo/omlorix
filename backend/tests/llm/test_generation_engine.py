import json
from types import SimpleNamespace

import pytest

from app.llm.generation.context import ContextBudgetExceeded, ContextBuilder
from app.llm.generation.engine import GenerationEngine, ProviderCall, stream_tool_call
from app.tools.results import ToolResult


@pytest.mark.parametrize(
    "protocol",
    [
        "openai",
        "openrouter",
        "openai_chat_completions",
        "anthropic",
        "google_aistudio",
        "ollama",
    ],
)
def test_total_budget_preserves_current_turn_and_complete_tool_pairs(protocol):
    history = [
        {"role": "user", "content": "old" * 300},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "最新の質問"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call-1", "name": "notes"}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "result"}
            ],
        },
    ]
    key = (
        "contents"
        if protocol == "google_aistudio"
        else "input"
        if protocol in {"openai", "openrouter"}
        else "messages"
    )
    payload = {
        key: history,
        "system": "Keep these instructions verbatim.",
        "tools": [{"name": "notes"}],
    }
    request = {"json": payload} if protocol == "openrouter" else payload
    builder = ContextBuilder()
    builder.prepare(
        request,
        settings={"input_token_limit": 1000, "output_token_limit": 100},
        protocol=protocol,
    )
    assert payload[key] == history[2:]
    assert payload["system"] == "Keep these instructions verbatim."
    assert history[0]["content"] == "old" * 300
    assert (
        builder.last_report["estimated_input_tokens"]
        <= builder.last_report["input_budget"]
    )
    assert builder.last_report["removed_segments"] == 1


def test_optional_context_has_provenance_and_does_not_evict_instructions():
    builder = ContextBuilder()
    builder.prefix_count = 2
    builder.prefix_sections = [
        ("workspace", 0, 1, True, 90),
        ("notes", 1, 2, False, 60),
    ]
    system = {"role": "system", "content": "Always preserve this policy"}
    workspace = {"role": "user", "content": "Project instructions"}
    current = {"role": "user", "content": "Current question"}
    payload = {
        "messages": [
            system,
            workspace,
            {"role": "user", "content": "note" * 1000},
            current,
        ]
    }
    builder.prepare(
        payload,
        settings={"input_token_limit": 1000, "output_token_limit": 100},
        protocol="openai_chat_completions",
    )
    assert payload["messages"] == [system, workspace, current]
    assert [segment["source"] for segment in builder.last_report["segments"]] == [
        "instructions",
        "workspace",
        "current_turn",
    ]


def test_required_context_and_tool_schemas_fail_before_provider_io(monkeypatch):
    calls = []
    engine = GenerationEngine()

    def adapter():
        with pytest.raises(ContextBudgetExceeded):
            yield ProviderCall(
                lambda **kwargs: calls.append(kwargs),
                {
                    "messages": [{"role": "user", "content": "small"}],
                    "tools": [{"description": "large" * 1000}],
                },
                {"input_token_limit": 1000, "output_token_limit": 100},
                "anthropic",
            )
        yield "finished"

    assert list(engine.run(adapter())) == ["finished"]
    assert calls == []


def test_engine_delivers_tool_results_errors_and_closes_resources(monkeypatch):
    monkeypatch.setattr(
        "app.llm.provider_request.release_db_session_before_provider_io",
        lambda db: True,
    )
    closed = []
    response = SimpleNamespace(close=lambda: closed.append(True))
    engine = GenerationEngine()

    def tool(*args):
        yield "progress"
        return {
            "content": "body needed now",
            "result": {
                "note": {
                    "id": "note-1",
                    "content": "large body",
                    "updated_at": "revision-1",
                }
            },
        }

    def failed(*args):
        raise ValueError("tool failed")
        yield

    def adapter():
        received = yield ProviderCall(
            lambda **kwargs: response,
            {"input": [{"role": "user", "content": "hello"}]},
            {},
            "openai",
        )
        assert received is response
        result = yield from stream_tool_call(tool, None, "notes")
        assert isinstance(result, ToolResult)
        assert result.model_content == "body needed now"
        assert "content" not in result.history_receipt["note"]
        assert result.artifacts[0].revision == "revision-1"
        with pytest.raises(ValueError, match="tool failed"):
            yield from stream_tool_call(failed, None, "notes")
        yield json.dumps({"t": "d", "d": "f"})

    events = list(engine.run(adapter()))
    assert events[0] == "progress"
    assert closed == [True]


def test_closing_client_stream_closes_suspended_tool_generator():
    closed = []

    def tool(*args):
        try:
            yield "progress"
        finally:
            closed.append(True)

    def adapter():
        yield from stream_tool_call(tool, None, "notes")

    stream = GenerationEngine().run(adapter())
    assert next(stream) == "progress"
    stream.close()
    assert closed == [True]


def test_ollama_budget_respects_server_window_and_model_output_cap():
    builder = ContextBuilder()
    payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "options": {"num_ctx": 2048, "num_predict": 1000},
    }
    builder.prepare(
        payload,
        settings={"input_token_limit": 100000, "output_token_limit": 500},
        protocol="ollama",
    )
    assert payload["options"] == {"num_ctx": 2048, "num_predict": 500}
    assert builder.last_report["input_budget"] < 2048


def test_tool_receipt_retains_cursor_for_later_turns():
    result = ToolResult.from_payload(
        "notes",
        {
            "result": {
                "notes": [{"id": "note"}],
                "has_more": True,
                "next_cursor": "next-page",
            }
        },
    )
    assert result.history_receipt["next_cursor"] == "next-page"
    assert result.model_content["next_cursor"] == "next-page"
