from pathlib import Path
from types import SimpleNamespace

from app.llm.openai_chat_completions import utils as chat_completions_utils


_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "llm"
    / "openai_chat_completions"
    / "utils.py"
)


def test_openai_chat_completions_does_not_log_full_request_payload():
    source = _SOURCE.read_text()

    assert "logger.error(request_kwargs)" not in source
    assert "logger.debug(request_kwargs)" not in source
    assert "logger.info(request_kwargs)" not in source


def test_openai_chat_completions_does_not_log_request_preparation_details():
    source = _SOURCE.read_text()

    assert "OpenAI Chat Completions request prepared" not in source
    assert 'logger.error(f"raw_tools: {raw_tools}")' not in source
    assert 'logger.error(f"resolved_tools: {resolved_tools}")' not in source
    assert 'logger.error(f"tool_schemas: {tool_schemas}")' not in source


def test_memory_generation_uses_native_chat_completions_json_schema(monkeypatch):
    requests = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content='{"candidates":[]}',
                    refusal=None,
                ),
            )
        ],
        model="memory-model",
        service_tier="standard",
        system_fingerprint=None,
        usage=None,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: requests.append(kwargs) or response
            )
        )
    )
    monkeypatch.setattr(
        chat_completions_utils,
        "_resolve_openai_client_context",
        lambda *_args, **_kwargs: {"client_kwargs": {}, "request_options": {}},
    )
    monkeypatch.setattr(chat_completions_utils, "OpenAI", lambda **_kwargs: client)
    monkeypatch.setattr(
        chat_completions_utils,
        "merge_settings",
        lambda *_args, **_kwargs: ({}, {}),
    )
    monkeypatch.setattr(
        chat_completions_utils,
        "_apply_openai_chat_completion_simple_settings",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        chat_completions_utils,
        "_record_openai_stat_with_costs",
        lambda *_args, **_kwargs: None,
    )
    schema = {
        "type": "object",
        "properties": {"candidates": {"type": "array"}},
        "required": ["candidates"],
        "additionalProperties": False,
    }

    result = chat_completions_utils.openai_chat_completions_title_generation(
        db=object(),
        model="memory-model",
        prompt="{}",
        system_instruction="Return JSON.",
        response_schema=schema,
        output_char_limit=None,
        max_output_tokens=4096,
        generation_category="memory_consolidation",
    )

    assert result == '{"candidates":[]}'
    assert requests[0]["max_completion_tokens"] == 4096
    assert requests[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "memory_consolidation",
            "strict": True,
            "schema": schema,
        },
    }
