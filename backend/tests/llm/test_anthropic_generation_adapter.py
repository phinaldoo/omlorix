"""End-to-end coverage for Anthropic's shared generation adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.anthropic import generation as anthropic_generation
from app.llm.anthropic import generation_adapter as anthropic_generation_adapter
from app.llm.anthropic.generation import (
    anthropic_title_generation,
)
from app.llm.anthropic.generation_adapter import AnthropicGenerationAdapter
from app.llm.generation import GenerationAdapter


class FakeMessages:
    """Capture Messages API payloads and return a configured response."""

    def __init__(self, response):
        self.response = response
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(messages=messages), messages


def _usage(**overrides):
    usage = {
        "input_tokens": 10,
        "cache_read_input_tokens": 2,
        "cache_creation_input_tokens": 3,
        "output_tokens": 4,
        "service_tier": "standard_only",
    }
    usage.update(overrides)
    return usage


def _response(text: str = "A useful title"):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=_usage(),
        stop_reason="end_turn",
    )


def _cost_calculator(**kwargs):
    assert kwargs["input_tokens"] == 15
    assert kwargs["cached_input_tokens"] == 2
    assert kwargs["cache_write_tokens"] == 3
    assert kwargs["output_tokens"] == 4
    return {
        "input_tokens_cost": 0.01,
        "output_tokens_cost": 0.02,
        "total_costs": 0.03,
    }


def test_anthropic_adapter_exposes_one_shot_contract():
    """Anthropic implements the one-shot shared generation protocol."""
    client, _messages = _client(_response())
    adapter = AnthropicGenerationAdapter(client=client)

    assert isinstance(adapter, GenerationAdapter)
    assert callable(adapter.generate_once)


def test_anthropic_title_generation_uses_shared_one_shot_lifecycle(monkeypatch):
    """One-shot output, usage, costs, and statistics retain their public behavior."""
    client, messages = _client(_response())
    statistics: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        anthropic_generation,
        "get_anthropic_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        anthropic_generation_adapter,
        "calculate_anthropic_token_costs",
        _cost_calculator,
    )
    monkeypatch.setattr(
        anthropic_generation,
        "create_llm_generation_statistic",
        lambda *args, **kwargs: statistics.append((args, kwargs)),
    )

    title = anthropic_title_generation(
        db=object(),
        model="claude-test",
        prompt="A long prompt that needs a title",
        system_instruction="Return a short title.",
        anthropic_provider_id="provider-1",
        user_id="user-1",
        model_settings={"max_tokens": 77},
    )

    assert title == "A useful title"
    assert messages.requests == [
        {
            "model": "claude-test",
            "max_tokens": 77,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "A long prompt that needs a title",
                        }
                    ],
                }
            ],
            "system": "Return a short title.",
        }
    ]
    statistic = statistics[0][1]
    assert statistic["category"] == "title_generation"
    assert statistic["success"] is True
    assert statistic["error"] is False
    assert statistic["meta"]["input_tokens"] == 15
    assert statistic["meta"]["total_costs"] == pytest.approx(0.03)
    assert statistic["meta"]["stop_reason"] == "end_turn"


def test_anthropic_title_generation_forwards_enabled_automatic_cache_setting(
    monkeypatch,
):
    """The shared non-streaming adapter sends the five-minute cache control."""
    client, messages = _client(_response())
    monkeypatch.setattr(
        anthropic_generation,
        "get_anthropic_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        anthropic_generation_adapter,
        "calculate_anthropic_token_costs",
        _cost_calculator,
    )
    monkeypatch.setattr(
        anthropic_generation,
        "create_llm_generation_statistic",
        lambda *_args, **_kwargs: None,
    )

    anthropic_title_generation(
        db=object(),
        model="claude-test",
        prompt="A repeated prompt",
        system_instruction="Return a short title.",
        model_settings={"max_tokens": 100, "prompt_cache_enabled": True},
    )

    assert messages.requests[0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_title_generation_keeps_client_failure_fast_fallback(monkeypatch):
    """Configuration failures return the legacy fallback without recording a call."""

    def fail_client(*_args, **_kwargs):
        raise RuntimeError("client configuration failed")

    def unexpected_statistic(*_args, **_kwargs):
        raise AssertionError("No provider request was made")

    monkeypatch.setattr(anthropic_generation, "get_anthropic_client", fail_client)
    monkeypatch.setattr(
        anthropic_generation,
        "create_llm_generation_statistic",
        unexpected_statistic,
    )

    title = anthropic_title_generation(
        db=object(),
        model="claude-test",
        prompt="x" * 80,
        system_instruction="Return a short title.",
    )

    assert title == "x" * 60
