import importlib
import json

import pytest
from fastapi.encoders import jsonable_encoder


@pytest.mark.parametrize(
    "provider",
    ["openai", "openai_chat_completions", "google_aistudio", "openrouter", "ollama"],
)
def test_selected_references_stay_with_the_latest_prompt_across_providers(provider, monkeypatch):
    adapter = importlib.import_module(f"app.llm.{provider}.utils")
    monkeypatch.setattr(adapter, "get_user_group_setting_value", lambda *_args, **_kwargs: False)
    result = adapter.reformat_chat_history(
        [
            {"role": "user", "content": "first prompt"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "latest prompt"},
        ],
        user_id=None,
        db=None,
        reference_parts=["  selected canvas text  ", "", None, "second excerpt"],
        chat_reference_context="  Referenced chat transcript  ",
        use_group_context=False,
        use_project_context=False,
    )

    formatted = jsonable_encoder(result["formatted"])
    first = json.dumps(formatted[0])
    latest = json.dumps(formatted[-1])
    assert "first prompt" in first
    assert "selected canvas text" not in first
    assert latest.index("selected canvas text") < latest.index("second excerpt")
    assert latest.index("second excerpt") < latest.index("Referenced chat transcript")
    assert latest.index("Referenced chat transcript") < latest.index("latest prompt")
