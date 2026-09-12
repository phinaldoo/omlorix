import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from openai import PermissionDeniedError

from app.chats.io import (
    _sanitize_import_chat_meta,
    _strip_imported_openai_continuation_metadata,
)
from app.chats.models import ensure_chat_sendable, mark_chat_openai_safety_stop
from app.llm.generation.engine import GenerationEngine, ProviderCall
from app.llm.openai.chat import _validated_openai_responses_stream
from app.llm.openai.model_list import OPENAI_MODEL_DICT
from app.llm.openai.request_policy import (
    OpenAIRequestPolicyError,
    apply_openai_request_policy,
)
from app.llm.openai.safety import OpenAISafetyStop, get_openai_safety_stop
from app.llm.openai.schemas import (
    OPENAI_THINKING_MODEL_SCHEMA,
    _apply_openai_model_caps_to_schema,
    get_parameters_schema_filled,
)
from app.llm.openai.utils import (
    _apply_openai_prompt_cache_settings,
    _build_openai_reasoning_payload,
    calculate_openai_token_costs,
)


@pytest.mark.parametrize("protocol", ["openai", "openai_chat_completions"])
@pytest.mark.parametrize(
    "effort,expected",
    [
        (None, "medium"),
        ("none", "low"),
        ("minimal", "low"),
        ("high", "high"),
        ("max", "max"),
    ],
)
def test_astra_requests_repair_stale_settings(protocol, effort, expected):
    request = {
        "model": "gpt-6-astra",
        "temperature": 0.7,
        "top_p": 0.8,
        "top_logprobs": 3,
        "logprobs": True,
        "include": ["message.output_text.logprobs", "reasoning.encrypted_content"],
        "extra_body": {"temperature": 1.0, "prompt_cache_retention": "24h"},
    }
    if protocol == "openai":
        request["reasoning"] = _build_openai_reasoning_payload(
            {
                "reasoning": False,
                "reasoning_effort": effort,
                "reasoning_mode": "pro",
                "reasoning_context": "all_turns",
            },
            model_name="gpt-6-astra",
            provider_type=protocol,
        )
    else:
        request["reasoning_effort"] = effort
    apply_openai_request_policy(request, provider_type=protocol)
    assert not {"temperature", "top_p", "top_logprobs", "logprobs"}.intersection(
        request
    )
    assert request["extra_body"] == {}
    assert request["include"] == ["reasoning.encrypted_content"]
    if protocol == "openai":
        assert request["reasoning"] == {
            "effort": expected,
            "mode": "pro",
            "context": "all_turns",
        }
    else:
        assert request["reasoning_effort"] == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"tools": [{"type": "function", "function": {"name": "notes"}}]},
        {"messages": [{"role": "tool", "content": "result", "tool_call_id": "call-1"}]},
        {"extra_body": {"tools": [{"type": "function"}]}},
    ],
)
def test_astra_tool_calls_and_tool_history_require_responses(payload):
    request = {"model": "gpt-6-astra", **payload}
    with pytest.raises(OpenAIRequestPolicyError):
        apply_openai_request_policy(request, provider_type="openai_chat_completions")
    apply_openai_request_policy(request, provider_type="openai")


@pytest.mark.parametrize(
    "model,provider",
    [
        ("gpt-5.6-sol", "openai_responses"),
        ("custom-model", "openai_responses"),
        ("gpt-6-astra", "xai"),
    ],
)
def test_astra_policy_does_not_change_other_models_or_vendors(model, provider):
    request = {
        "model": model,
        "temperature": 0.4,
        "reasoning_effort": "none",
        "service_tier": "priority",
    }
    original = deepcopy(request)
    apply_openai_request_policy(request, provider_type=provider)
    assert request == original


def test_astra_catalog_enables_cache_and_correct_cost_threshold():
    caps = OPENAI_MODEL_DICT["gpt-6-astra"]
    assert (caps["input_token_limit"], caps["output_token_limit"]) == (922000, 128000)
    request = {"model": "gpt-6-astra"}
    _apply_openai_prompt_cache_settings(
        request,
        {},
        model_name="gpt-6-astra",
        provider_id="p",
        user_id="u",
        provider_type="openai",
    )
    assert request["extra_body"]["prompt_cache_options"] == {
        "mode": "implicit",
        "ttl": "30m",
    }
    for input_tokens, expected in [(272000, 2.345), (272001, 4.44002)]:
        costs = calculate_openai_token_costs(
            model_name="gpt-6-astra",
            service_tier="standard",
            input_tokens=input_tokens,
            cached_input_tokens=100000,
            cache_write_tokens=10000,
            output_tokens=10000,
            reasoning_tokens=2000,
            native_websearch_tool_calls_count=0,
        )
        assert costs["total_costs"] == pytest.approx(expected)


@pytest.mark.parametrize("protocol", ["openai", "openai_chat_completions"])
def test_astra_schema_exposes_only_supported_reasoning_and_sampling(protocol):
    schema = OPENAI_THINKING_MODEL_SCHEMA.model_copy(deep=True)
    schema.sections += get_parameters_schema_filled(
        {}, "gpt-6-astra", protocol
    ).sections
    _apply_openai_model_caps_to_schema(
        schema, OPENAI_MODEL_DICT["gpt-6-astra"], openai_provider_type=protocol
    )
    fields = {f.key: f for s in schema.sections for f in s.fields}
    assert not {
        "settings.reasoning",
        "settings.temperature",
        "settings.top_p",
    }.intersection(fields)
    effort = fields["settings.reasoning_effort"]
    assert [o.value for o in effort.options] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert effort.dependency is None
    assert effort.value == "medium"
    assert ("settings.reasoning_mode" in fields) == (protocol == "openai")


def test_safety_stop_recognizes_http_and_stream_errors_without_sensitive_text():
    body = {
        "code": "misalignment_policy_violation",
        "message": "private reasoning",
        "type": "invalid_request_error",
    }
    error = PermissionDeniedError(
        "private reasoning",
        response=httpx.Response(
            403,
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            headers={"x-request-id": "req-1"},
        ),
        body=body,
    )
    stop = get_openai_safety_stop(error)
    assert stop.request_id == "req-1"
    assert "private reasoning" not in json.dumps(stop.event())
    for event in [
        SimpleNamespace(type="error", code=body["code"]),
        SimpleNamespace(
            type="response.failed", response=SimpleNamespace(id="resp-1", error=body)
        ),
    ]:
        with pytest.raises(OpenAISafetyStop):
            list(
                _validated_openai_responses_stream(
                    [
                        SimpleNamespace(
                            type="response.output_text.delta", delta="partial"
                        ),
                        event,
                    ]
                )
            )


def test_persisted_safety_stop_survives_import_and_blocks_retries_before_dispatch():
    stop = OpenAISafetyStop(request_id="req-1", response_id="resp-1")
    chat = SimpleNamespace(
        meta={"status": "normal", "other_setting": True}, archived=False
    )
    db = Mock()
    db.query.return_value.filter.return_value.populate_existing.return_value.with_for_update.return_value.first.return_value = chat
    mark_chat_openai_safety_stop(db, "chat-1", stop.metadata())
    assert chat.meta["other_setting"] is True
    db.commit.assert_called_once()
    chat.meta = _sanitize_import_chat_meta(chat.meta)
    with pytest.raises(HTTPException) as error:
        ensure_chat_sendable(chat)
    assert error.value.status_code == 403
    assert error.value.detail["code"] == stop.code

    engine = GenerationEngine()
    engine.chat_history = _strip_imported_openai_continuation_metadata(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "content", "content": "", "meta": stop.metadata()}
                ],
            }
        ]
    )
    provider = Mock()

    def adapter():
        yield ProviderCall(provider, {}, {}, "openai")

    events = [json.loads(item) for item in engine.run(adapter())]
    assert events[0]["code"] == stop.code
    assert events[0]["retryable"] is False
    provider.assert_not_called()


def test_astra_messages_are_translated_in_all_locales():
    root = Path(__file__).resolve().parents[3] / "frontend" / "i18n"
    keys = {
        "chat_openai_safety_stop",
        "chat_openai_tools_require_responses",
        "llm.openai.tools_require_responses",
    }
    for locale in root.iterdir():
        if locale.is_dir():
            values = json.loads((locale / "index.json").read_text())
            assert all(values.get(key) for key in keys), locale.name


@pytest.mark.parametrize("streamed", [False, True])
def test_chat_safety_stop_persists_and_never_retries(monkeypatch, streamed):
    from app.chats import models as chat_models
    from app.llm.openai import chat

    body = {
        "code": "misalignment_policy_violation",
        "message": "sensitive upstream detail",
    }
    client = SimpleNamespace(
        base_url="https://eu.api.openai.com/v1",
        responses=SimpleNamespace(create=Mock()),
    )
    if streamed:

        def events():
            yield SimpleNamespace(
                type="response.output_text.delta", delta="Partial answer"
            )
            yield SimpleNamespace(
                type="response.failed",
                response=SimpleNamespace(id="resp-1", error=body),
            )
            pytest.fail("A safety stop must not consume further work")

        client.responses.create.return_value = events()
    else:
        client.responses.create.side_effect = PermissionDeniedError(
            "sensitive upstream detail",
            response=httpx.Response(
                403,
                request=httpx.Request("POST", "https://eu.api.openai.com/v1/responses"),
            ),
            body=body,
        )
    settings = {
        "reasoning_effort": "none",
        "temperature": 1,
        "priority_processing": "priority",
    }
    monkeypatch.setattr(chat, "OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(
        chat,
        "_resolve_openai_client_context",
        lambda *args, **kwargs: {"client_kwargs": {}, "request_options": {}},
    )
    monkeypatch.setattr(chat, "merge_settings", lambda *args: (settings, []))
    monkeypatch.setattr(
        chat,
        "reformat_chat_history",
        lambda *args, **kwargs: {"formatted": [{"role": "user", "content": "Hello"}]},
    )
    monkeypatch.setattr(
        chat, "get_default_system_instruction", lambda *args: "Help the user."
    )
    marker = Mock()
    monkeypatch.setattr(chat_models, "mark_chat_openai_safety_stop", marker)
    engine = GenerationEngine()
    engine.persist_message = Mock(return_value=SimpleNamespace(id="saved-1"))
    engine.events = lambda response, *args, **kwargs: response
    model = SimpleNamespace(
        id="model-1",
        model_name="gpt-6-astra",
        provider="openai",
        provider_id="p",
        settings=settings,
        tools=[],
        capabilities=[],
    )
    result = [
        json.loads(line)
        for line in engine.run(
            chat._impl_openai_chat.__wrapped__(
                "chat-1",
                [],
                Mock(),
                model,
                user_id="user-1",
                engine=engine,
            )
        )
    ]
    client.responses.create.assert_called_once()
    request = client.responses.create.call_args.kwargs
    assert request["reasoning"]["effort"] == "low"
    assert request["service_tier"] == "fast"
    assert "temperature" not in request
    marker.assert_called_once()
    engine.persist_message.assert_called_once()
    saved_content = engine.persist_message.call_args.kwargs["content"]
    assert any(block["meta"]["error_code"] == body["code"] for block in saved_content)
    if streamed:
        assert any(block["content"] == "Partial answer" for block in saved_content)
    assert next(event for event in result if event["t"] == "e")["retryable"] is False
    assert result[-1]["c"]["status"] == "error"
    assert "sensitive upstream detail" not in json.dumps(result)
