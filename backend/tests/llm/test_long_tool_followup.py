"""Regression coverage for assistant continuation after long-running tools."""

from pathlib import Path
import re


LLM_ROOT = Path(__file__).resolve().parents[2] / "app" / "llm"


def _tool_execution_window(relative_path: str) -> str:
    """Return the provider source around its streamed tool invocation."""

    source = (LLM_ROOT / relative_path).read_text(encoding="utf-8")
    start = source.index("helper_gen = stream_tool_call(resolve_tool_call,")
    end = source.index("if tool_error_message:", start)
    return source[start:end]


def test_stream_timeout_is_refreshed_after_long_running_tools():
    """Research completion must reach the next assistant model request."""

    for relative_path in (
        "openai/chat.py",
        "openai_chat_completions/chat.py",
        "openrouter/chat.py",
        "ollama/chat.py",
    ):
        assert "last_activity = time.monotonic()" in _tool_execution_window(
            relative_path
        )


def test_chat_completions_parallel_tools_refresh_stream_timeout():
    """Parallel subagent work also refreshes the follow-up request window."""

    source = (LLM_ROOT / "openai_chat_completions" / "chat.py").read_text(
        encoding="utf-8"
    )
    start = source.index("parallel_gen = resolve_parallel_subagent_tool_calls(")
    end = source.index("if len(parallel_results)", start)

    assert "last_activity = time.monotonic()" in source[start:end]


def test_openai_responses_sends_rich_output_to_the_active_model():
    """Bounded widget persistence must not replace the live tool result."""

    source = (LLM_ROOT / "openai" / "chat.py").read_text(encoding="utf-8")

    assert re.search(
        r"model_tool_output\s*=\s*\(?\s*stringify_tool_result_content_for_model\(",
        source,
    )
    assert "function_call_output_text = model_tool_output" in source


def test_all_chat_providers_use_safe_structured_tool_errors():
    """Provider adapters must not discard actionable, explicitly safe errors."""

    for relative_path in (
        "openai/chat.py",
        "openai_chat_completions/chat.py",
        "openrouter/chat.py",
        "ollama/chat.py",
        "anthropic/chat.py",
        "google_aistudio/chat.py",
    ):
        source = (LLM_ROOT / relative_path).read_text(encoding="utf-8")
        assert "ToolErrorTracker" in source
        assert ".record(" in source
        assert "stop_tool_calls" in source
