import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats.schemas import SendChatRequestModelSettings
from app.llm.helper import merge_settings
from app.llm.openrouter.schemas import OpenrouterModelSettings


def test_custom_settings_rejects_admin_control_overrides():
    with pytest.raises(ValidationError):
        SendChatRequestModelSettings.model_validate(
            {
                "settings": {
                    "temperature": 0.2,
                    "max_document_count": -1,
                    "native_youtube_video": True,
                    "input_formats": ["text", "documents"],
                }
            }
        )

    with pytest.raises(ValidationError):
        SendChatRequestModelSettings.model_validate(
            {
                "tool_settings": {
                    "image_generation": {"provider": "evil"},
                }
            }
        )


def test_custom_settings_system_instruction_replaces_admin_model_instruction():
    custom_settings = SendChatRequestModelSettings.model_validate(
        {
            "system_instruction": "Use the conversation-specific system instruction.",
        }
    ).as_override_dict()

    merged, _ = merge_settings(
        {
            "system_instruction": "Administrator default system instruction.",
        },
        custom_settings,
        getattr(OpenrouterModelSettings, "model_fields", None),
    )

    assert custom_settings == {
        "system_instruction": "Use the conversation-specific system instruction."
    }
    assert merged["system_instruction"] == "Use the conversation-specific system instruction."


def test_custom_settings_omitted_system_instruction_keeps_admin_value():
    custom_settings = SendChatRequestModelSettings.model_validate(
        {"settings": {"temperature": 0.2}}
    ).as_override_dict()

    merged, _ = merge_settings(
        {"system_instruction": "Administrator default system instruction."},
        custom_settings,
        getattr(OpenrouterModelSettings, "model_fields", None),
    )

    assert merged["system_instruction"] == "Administrator default system instruction."

    blank_override = SendChatRequestModelSettings.model_validate(
        {"system_instruction": "   "}
    ).as_override_dict()
    blank_merged, _ = merge_settings(
        {"system_instruction": "Administrator default system instruction."},
        blank_override,
        getattr(OpenrouterModelSettings, "model_fields", None),
    )
    assert "system_instruction" not in blank_override
    assert blank_merged["system_instruction"] == "Administrator default system instruction."


def test_custom_settings_validates_structured_logit_bias():
    parsed = SendChatRequestModelSettings.model_validate(
        {"settings": {"logit_bias": {"123": -1.5, "456": 2}}}
    ).as_override_dict()

    assert parsed["settings"]["logit_bias"] == {"123": -1.5, "456": 2.0}

    with pytest.raises(ValidationError, match="non-negative token IDs"):
        SendChatRequestModelSettings.model_validate(
            {"settings": {"logit_bias": {"not-a-token": 1}}}
        )

    with pytest.raises(ValidationError, match="-100 to 100"):
        SendChatRequestModelSettings.model_validate(
            {"settings": {"logit_bias": {"123": 101}}}
        )


def test_custom_settings_allowlisted_values_merge_without_admin_overrides():
    custom_settings = SendChatRequestModelSettings.model_validate(
        {
            "settings": {
                "temperature": 0.2,
                "top_p": 0.9,
                "enabled_tools": ["mcp"],
            }
        }
    ).as_override_dict()

    merged, merged_tools = merge_settings(
        {
            "temperature": 1.0,
            "max_document_count": 2,
            "native_youtube_video": False,
            "input_formats": ["text"],
        },
        custom_settings,
        getattr(OpenrouterModelSettings, "model_fields", None),
        ["mcp", "image_generation"],
    )

    assert merged["temperature"] == 0.2
    assert merged["top_p"] == 0.9
    assert merged["max_document_count"] == 2
    assert merged["native_youtube_video"] is False
    assert merged["input_formats"] == ["text"]
    assert merged_tools == ["mcp"]


def test_custom_settings_accepts_tool_search_override():
    custom_settings = SendChatRequestModelSettings.model_validate(
        {
            "settings": {
                "tool_search": True,
            }
        }
    ).as_override_dict()

    assert custom_settings == {"settings": {"tool_search": True}}


def test_custom_settings_accepts_gpt56_reasoning_and_prompt_cache_overrides():
    """GPT-5.6 controls rendered by the model schema must pass request validation."""
    custom_settings = SendChatRequestModelSettings.model_validate(
        {
            "settings": {
                "reasoning_context": "auto",
                "prompt_cache_override": False,
                "prompt_cache_ttl": "30m",
                "prompt_cache_key": "stable-cache-key",
            }
        }
    ).as_override_dict()

    assert custom_settings == {
        "settings": {
            "reasoning_context": "auto",
            "prompt_cache_override": False,
            "prompt_cache_ttl": "30m",
            "prompt_cache_key": "stable-cache-key",
        }
    }
