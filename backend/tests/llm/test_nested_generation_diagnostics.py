from types import SimpleNamespace

import pytest

from app.llm import nested_generation
from app.tools.errors import ToolExecutionDiagnosticError


class _FakeQuery:
    """Minimal SQLAlchemy query stand-in for nested-generation unit tests."""

    def __init__(self, model):
        self.model = model

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.model


class _FakeDb:
    """Return one configured model without requiring a database fixture."""

    def __init__(self, model):
        self.model = model

    def query(self, _model_type):
        return _FakeQuery(self.model)


def test_nested_provider_failure_identifies_actual_phase_model_and_provider(monkeypatch):
    model = SimpleNamespace(
        id="presentation-model-id",
        name="Presentation Designer",
        model_name="claude-sonnet-4-5",
        provider="anthropic",
        provider_id="provider-id",
        settings={},
        capabilities=[],
        tools=[],
        access={},
        meta={},
        status="active",
        is_active=True,
    )
    monkeypatch.setattr(
        nested_generation,
        "call_provider_chat",
        lambda _request: ['{"t":"e","d":"peer closed connection"}\n'],
    )

    with pytest.raises(ToolExecutionDiagnosticError) as raised:
        nested_generation.run_nested_generation(
            _FakeDb(model),
            model_id=model.id,
            user_id="user-id",
            messages=[],
            instructions="Create the deck.",
            purpose="slide-presentation-generate",
            phase="slide presentation HTML generation",
        )

    message = str(raised.value)
    assert "slide presentation HTML generation" in message
    assert "Presentation Designer (claude-sonnet-4-5)" in message
    assert "anthropic" in message
    assert "peer closed connection" in message
    assert raised.value.tool_statistic_meta == {
        "nested_generation": {
            "phase": "slide presentation HTML generation",
            "purpose": "slide-presentation-generate",
            "model_id": "presentation-model-id",
            "model_name": "Presentation Designer",
            "provider": "anthropic",
        }
    }


def test_nested_generation_rejects_user_managed_model_before_provider_call(monkeypatch):
    """Stale global settings must never launch one user's private runtime."""

    model = SimpleNamespace(
        id="personal-model",
        name="Personal model",
        model_name="private-runtime-model",
        provider="openai",
        provider_id="personal-provider-id",
        settings={},
        capabilities=["completion"],
        tools=[],
        access={"everyone": False, "users": ["owner-id"], "groups": []},
        meta={"user_managed": True, "owner_user_id": "owner-id"},
        status="normal",
        is_active=True,
    )
    provider_called = False

    def fail_if_called(_request):
        nonlocal provider_called
        provider_called = True
        return []

    monkeypatch.setattr(nested_generation, "call_provider_chat", fail_if_called)

    with pytest.raises(ToolExecutionDiagnosticError) as raised:
        nested_generation.run_nested_generation(
            _FakeDb(model),
            model_id=model.id,
            user_id="owner-id",
            messages=[],
            instructions="Create the deck.",
            purpose="slide-presentation-generate",
            phase="slide presentation HTML generation",
        )

    assert provider_called is False
    assert "user-managed models are not available" in str(raised.value)
    assert raised.value.tool_statistic_meta["nested_generation"] == {
        "phase": "slide presentation HTML generation",
        "purpose": "slide-presentation-generate",
        "model_id": "personal-model",
        "model_name": "Personal model",
        "provider": "openai",
    }


def test_nested_generation_treats_cancellation_as_incomplete(monkeypatch):
    """A provider cancellation marker must not be accepted as completion."""

    model = SimpleNamespace(
        id="model-id",
        name="Model",
        model_name="upstream-model",
        provider="openai",
        provider_id="provider-id",
        settings={},
        capabilities=[],
        tools=[],
        access={},
        meta={},
        status="active",
        is_active=True,
    )
    monkeypatch.setattr(
        nested_generation,
        "call_provider_chat",
        lambda _request: ['{"t":"c","d":"partial"}\n', '{"t":"d","d":"c"}\n'],
    )

    with pytest.raises(ToolExecutionDiagnosticError, match="ended before completion"):
        nested_generation.run_nested_generation(
            _FakeDb(model),
            model_id=model.id,
            user_id="user-id",
            messages=[],
            instructions="Generate content.",
        )


def test_nested_generation_stream_exposes_normalized_text_and_terminal_result(monkeypatch):
    """Progressive feature UIs receive deltas without changing final semantics."""

    model = SimpleNamespace(
        id="model-id",
        name="Model",
        model_name="upstream-model",
        provider="openai",
        provider_id="provider-id",
        settings={},
        capabilities=[],
        tools=[],
        access={},
        meta={},
        status="active",
        is_active=True,
    )
    monkeypatch.setattr(
        nested_generation,
        "call_provider_chat",
        lambda _request: [
            '{"t":"c","d":"<!DOCTYPE html>"}\n',
            '{"t":"c","d":"<html></html>"}\n',
            '{"t":"d","d":"f"}\n',
        ],
    )

    stream = nested_generation.stream_nested_generation(
        _FakeDb(model),
        model_id=model.id,
        user_id="user-id",
        messages=[],
        instructions="Generate content.",
    )
    deltas = []
    while True:
        try:
            deltas.append(next(stream))
        except StopIteration as completed:
            result = completed.value
            break

    assert deltas == ["<!DOCTYPE html>", "<html></html>"]
    assert result.text == "<!DOCTYPE html><html></html>"
