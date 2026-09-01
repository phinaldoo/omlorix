"""Contract tests for provider compatibility facades after module extraction."""

from __future__ import annotations

import inspect
from importlib import import_module

import pytest


# Each focused module publishes the facade dependencies used by every moved
# function. Keeping this inventory explicit makes newly added provider modules
# opt in to the same compatibility contract instead of silently bypassing it.
PROVIDER_IMPLEMENTATION_MODULES = (
    ("app.llm.openai.utils", "app.llm.openai.usage"),
    ("app.llm.openai.utils", "app.llm.openai.models"),
    ("app.llm.openai.utils", "app.llm.openai.chat"),
    ("app.llm.openai.utils", "app.llm.openai.generation"),
    ("app.llm.openai.utils", "app.llm.openai.attachments"),
    ("app.llm.openai.utils", "app.llm.openai.messages"),
    ("app.llm.openrouter.utils", "app.llm.openrouter.usage"),
    ("app.llm.openrouter.utils", "app.llm.openrouter.models"),
    ("app.llm.openrouter.utils", "app.llm.openrouter.chat"),
    ("app.llm.openrouter.utils", "app.llm.openrouter.generation"),
    ("app.llm.openrouter.utils", "app.llm.openrouter.messages"),
    ("app.llm.google_aistudio.utils", "app.llm.google_aistudio.usage"),
    ("app.llm.google_aistudio.utils", "app.llm.google_aistudio.models"),
    ("app.llm.google_aistudio.utils", "app.llm.google_aistudio.chat"),
    ("app.llm.google_aistudio.utils", "app.llm.google_aistudio.generation"),
    ("app.llm.google_aistudio.utils", "app.llm.google_aistudio.attachments"),
    ("app.llm.google_aistudio.utils", "app.llm.google_aistudio.messages"),
    ("app.llm.ollama.utils", "app.llm.ollama.models"),
    ("app.llm.ollama.utils", "app.llm.ollama.chat"),
    ("app.llm.ollama.utils", "app.llm.ollama.generation"),
    ("app.llm.ollama.utils", "app.llm.ollama.messages"),
    (
        "app.llm.openai_chat_completions.utils",
        "app.llm.openai_chat_completions.chat",
    ),
    (
        "app.llm.openai_chat_completions.utils",
        "app.llm.openai_chat_completions.generation",
    ),
    (
        "app.llm.openai_chat_completions.utils",
        "app.llm.openai_chat_completions.attachments",
    ),
    (
        "app.llm.openai_chat_completions.utils",
        "app.llm.openai_chat_completions.messages",
    ),
    ("app.llm.lmstudio.utils", "app.llm.lmstudio.client"),
    ("app.llm.lmstudio.utils", "app.llm.lmstudio.models"),
    ("app.llm.openai.schemas", "app.llm.openai.generation_schema"),
    ("app.llm.openai.schemas", "app.llm.openai.model_schema"),
    ("app.llm.openai.schemas", "app.llm.openai.parameter_schema"),
    ("app.llm.openrouter.schemas", "app.llm.openrouter.generation_schema"),
    ("app.llm.openrouter.schemas", "app.llm.openrouter.model_schema"),
    ("app.llm.openrouter.schemas", "app.llm.openrouter.parameter_schema"),
    (
        "app.llm.google_aistudio.schemas",
        "app.llm.google_aistudio.generation_schema",
    ),
    ("app.llm.google_aistudio.schemas", "app.llm.google_aistudio.model_schema"),
    (
        "app.llm.google_aistudio.schemas",
        "app.llm.google_aistudio.parameter_schema",
    ),
    ("app.llm.ollama.schemas", "app.llm.ollama.generation_schema"),
    ("app.llm.ollama.schemas", "app.llm.ollama.model_schema"),
    ("app.llm.ollama.schemas", "app.llm.ollama.parameter_schema"),
    ("app.llm.lmstudio.schemas", "app.llm.lmstudio.model_schema"),
    ("app.llm.lmstudio.schemas", "app.llm.lmstudio.parameter_schema"),
)


@pytest.mark.parametrize(
    ("facade_name", "implementation_name"),
    PROVIDER_IMPLEMENTATION_MODULES,
)
@pytest.mark.asyncio
async def test_extracted_provider_functions_keep_facade_patch_seams(
    monkeypatch,
    facade_name: str,
    implementation_name: str,
) -> None:
    """Every moved callable delegates and refreshes dependencies from its facade."""
    facade = import_module(facade_name)
    implementation = import_module(implementation_name)

    for function_name, dependencies in implementation._COMPAT_DEPENDENCIES.items():
        wrapper = getattr(facade, function_name)
        marker = object()
        dependency_name = next(
            (name for name in dependencies if hasattr(facade, name)),
            None,
        )
        if dependency_name:
            monkeypatch.setattr(facade, dependency_name, marker)

        expected = (facade_name, implementation_name, function_name)

        if inspect.iscoroutinefunction(wrapper):

            async def fake_implementation(*args, _expected=expected, **kwargs):
                return _expected, args, kwargs

        else:

            def fake_implementation(*args, _expected=expected, **kwargs):
                return _expected, args, kwargs

        monkeypatch.setattr(
            implementation,
            f"_impl_{function_name}",
            fake_implementation,
        )

        delegated = wrapper("sentinel-argument", sentinel_keyword=True)
        if inspect.isawaitable(delegated):
            delegated = await delegated
        result, args, kwargs = delegated
        assert result == expected
        assert args == ("sentinel-argument",)
        assert kwargs == {"sentinel_keyword": True}
        if dependency_name:
            assert getattr(implementation, dependency_name) is marker
