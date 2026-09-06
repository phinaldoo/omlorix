"""Cache stability must not weaken context or tool-execution limits."""

from copy import deepcopy

import pytest

from app.llm.generation.context import ContextBudgetExceeded
from app.llm.generation.engine import GenerationEngine, ProviderCall, ToolCall
from app.tools.subagents.session import SubagentSession


@pytest.mark.parametrize(
    "protocol,choice",
    [
        ("openai", "none"),
        ("openai_chat_completions", "none"),
        ("openrouter", "none"),
        ("anthropic", {"type": "none"}),
        ("ollama", None),
    ],
)
def test_final_request_preserves_schemas_and_cannot_execute(protocol, choice):
    session = SubagentSession("cache-final", ("update_presentation",), max_calls=0)
    schemas = [{"name": "update_presentation", "parameters": {"type": "object"}}]
    payload = {"tools": schemas, "messages": ["last result"]}
    request = {"json": payload} if protocol == "openrouter" else payload
    session.prepare_request(request, protocol=protocol)
    if protocol == "ollama":
        assert "tools" not in payload and "tool_choice" not in payload
    else:
        assert payload["tools"] is schemas
        assert payload["tool_choice"] == choice

    def forbidden(tool_name, tool_arguments):
        pytest.fail("An exhausted session must never execute a tool")

    stream = session.execute(ToolCall(forbidden, ("update_presentation", {}), {}))
    with pytest.raises(StopIteration) as result:
        next(stream)
    assert '"calls_remaining": 0' in result.value.value.model_content
    with pytest.raises(RuntimeError, match="budget exhausted"):
        session.prepare_request(request, protocol=protocol)


def test_final_gemini_config_preserves_native_schema_and_other_settings():
    from google.genai import types

    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=[types.FunctionDeclaration(name="edit")])],
        temperature=0.5,
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode="ANY", allowed_function_names=["edit"]
            )
        ),
    )
    request = {"config": config}
    SubagentSession("gemini-final", (), max_calls=0).prepare_request(
        request, protocol="google_aistudio"
    )
    final = request["config"]
    assert final.tools == config.tools
    assert final.temperature == 0.5
    assert final.tool_config.function_calling_config.mode == "NONE"
    assert final.tool_config.function_calling_config.allowed_function_names is None
    assert config.tool_config.function_calling_config.mode == "ANY"


@pytest.mark.parametrize("exhausted", [False, True])
def test_context_pressure_prunes_once_and_does_not_resurrect_history(exhausted):
    session = SubagentSession("cache-history", (), max_calls=0 if exhausted else 12)
    session.attachments = {"old-review": {}, "new-review": {}}
    session.latest_attachment_ids = {"new-review"}
    history = [{"role": "user", "content": "Keep the entire original brief."}]
    for file_id in session.attachments:
        history.extend([
            {"type": "function_call", "call_id": file_id, "name": "edit", "arguments": "{}"},
            {"type": "function_call_output", "call_id": file_id, "output": "Rendered"},
            {"role": "user", "content": [
                {"type": "input_text", "text": f"Metadata {file_id}"},
                {"type": "input_image", "image_url": file_id},
            ]},
        ])
    engine = GenerationEngine()
    engine.session = session
    engine.context.preserve_history = True
    sent = []

    def send(window):
        request = {"input": deepcopy(history), "tools": [{"name": "edit"}]}
        engine._provider(ProviderCall(
            lambda **payload: sent.append(payload), request,
            {"input_token_limit": window}, "openai",
        ))
        return request

    # A final request can also need local budgeting twice, but sends only once.
    request = send(12000)
    assert len(sent) == 1
    assert session.pruned_attachment_ids == {"old-review"}
    assert request["input"][0:3] == history[0:3]
    assert len(request["input"][3]["content"]) == 1
    assert request["input"][-1] == history[-1]
    assert len(history[3]["content"]) == 2
    if exhausted:
        assert request["tool_choice"] == "none"
        assert request["tools"] == [{"name": "edit"}]
    else:
        # Even if more space becomes available, the already-sent prefix stays.
        assert send(100000)["input"] == request["input"]
        with pytest.raises(ContextBudgetExceeded):
            send(1000)
        assert len(sent) == 2
        assert engine.context_error


def test_without_context_pressure_screenshots_remain_append_only():
    session = SubagentSession("cache-retain", ())
    session.attachments = {"old-review": {}, "new-review": {}}
    session.latest_attachment_ids = {"new-review"}
    request = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "Metadata old-review"},
        {"type": "image", "source": {"data": "old-image"}},
    ]}]}
    before = deepcopy(request)
    session.prepare_request(request)
    assert request == before
    assert session.pruned_attachment_ids == set()
