from types import SimpleNamespace
from unittest.mock import MagicMock

import app.chats.models as chat_models
from app.llm.google_aistudio import utils as aistudio_utils
from app.llm.system_instruction import chat as system_instruction_utils


def test_custom_system_instruction_resolves_placeholders_before_runtime_sections(
    monkeypatch,
) -> None:
    """Google custom prompts must use the shared resolver before appending context."""
    usage = SimpleNamespace(
        prompt_token_count=1,
        prompt_tokens_details=[],
        tool_use_prompt_token_count=0,
        tool_use_prompt_tokens_details=[],
        cached_content_token_count=0,
        cache_tokens_details=[],
        candidates_token_count=1,
        thoughts_token_count=0,
        total_token_count=2,
    )
    part = SimpleNamespace(
        text="Hello",
        thought=False,
        thought_signature=None,
        tool_call=None,
        tool_response=None,
        function_call=None,
    )
    chunk = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[part]),
                finish_reason="STOP",
            )
        ],
        usage_metadata=usage,
    )
    client = SimpleNamespace(
        models=SimpleNamespace(generate_content_stream=lambda **_kwargs: [chunk]),
        files=SimpleNamespace(delete=lambda **_kwargs: None),
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        aistudio_utils,
        "get_aistudio_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        aistudio_utils,
        "get_user_setting_value",
        lambda *_args, **_kwargs: "en",
    )
    monkeypatch.setattr(
        aistudio_utils,
        "reformat_chat_history",
        lambda *_args, **_kwargs: {"formatted": [], "uploaded_cleanup": []},
    )
    monkeypatch.setattr(
        system_instruction_utils,
        "get_user",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        system_instruction_utils,
        "get_user_setting_value",
        lambda *_args, **_kwargs: None,
    )

    def _build_config(_settings, *, system_instruction, **_kwargs):
        captured["final_system_instruction"] = system_instruction

    monkeypatch.setattr(
        aistudio_utils,
        "build_aistudio_generate_content_config",
        _build_config,
    )
    monkeypatch.setattr(
        aistudio_utils,
        "create_llm_generation_statistic",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        chat_models,
        "create_chat_message",
        lambda *_args, **_kwargs: SimpleNamespace(id="assistant-1"),
    )

    custom_instruction = "Cutoff {knowledge_cutoff}; timezone {tz_display}."
    db_model = SimpleNamespace(
        id="model-1",
        model_name="gemini-3.5-flash-lite",
        provider_id="provider-1",
        settings={
            "knowledge_cutoff": "June 2024",
            "system_instruction": custom_instruction,
        },
        tools=[],
        capabilities=[],
    )

    events = list(
        aistudio_utils.aistudio_chat(
            "chat-1",
            [],
            MagicMock(),
            db_model=db_model,
            user_id="user-1",
            byok={
                "api_key": "test-key",
                "model_name": "gemini-3.5-flash-lite",
                "capabilities": [],
            },
            system_instruction_sections=[
                {"title": "Runtime Context", "content": "Request-specific rules."}
            ],
        )
    )

    assert events
    assert captured["final_system_instruction"] == (
        "Cutoff June 2024; timezone UTC."
        "\n\n---\n\n## Runtime Context\n\nRequest-specific rules."
    )
