"""Regression coverage for the provider-wide tool-call budget."""

import inspect

import pytest

from app.llm.anthropic.chat import anthropic_chat
from app.llm.google_aistudio.chat import _impl_aistudio_chat
from app.llm.ollama.chat import _impl_ollama_chat
from app.llm.openai_chat_completions.chat import _impl_openai_chat_completions_chat
from app.llm.openrouter.chat import _impl_openrouter_chat
from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION


@pytest.mark.parametrize(
    "chat_function",
    (
        anthropic_chat,
        _impl_aistudio_chat,
        _impl_ollama_chat,
        _impl_openai_chat_completions_chat,
        _impl_openrouter_chat,
    ),
)
def test_every_counter_based_provider_uses_the_shared_tool_call_budget(chat_function):
    assert MAX_TOOL_CALLS_PER_GENERATION == 200
    assert "max_calls = MAX_TOOL_CALLS_PER_GENERATION" in inspect.getsource(chat_function)
