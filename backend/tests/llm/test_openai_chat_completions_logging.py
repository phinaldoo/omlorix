from pathlib import Path


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
