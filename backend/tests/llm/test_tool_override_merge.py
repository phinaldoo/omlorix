import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.helper import merge_settings


def test_tool_overrides_cannot_enable_tools_without_model_allowlist():
    _, merged_tools = merge_settings(
        {},
        {"enabled_tools": ["web_search", "url_content"]},
        [],
        db_tools=[],
    )

    assert merged_tools == []


def test_tool_overrides_are_filtered_to_model_allowlist():
    _, merged_tools = merge_settings(
        {},
        {"enabled_tools": ["web_search", "generate_qrcode"]},
        [],
        db_tools=[{"name": "generate_qrcode", "description": "Generate QR codes"}],
    )

    assert merged_tools == [{"name": "generate_qrcode", "description": "Generate QR codes"}]


def test_request_tool_settings_do_not_override_admin_tool_settings():
    settings, _ = merge_settings(
        {
            "temperature": 0.2,
            "tool_settings": {
                "audio_generation": {
                    "voice": "admin_voice",
                    "response_format": "mp3",
                }
            },
        },
        {
            "temperature": 0.9,
            "tool_settings": {
                "audio_generation": {
                    "voice": "attacker_private_voice",
                    "response_format": "wav",
                },
                "image_generation": {
                    "settings": {"extra_body": {"attacker_marker": True}},
                },
            },
        },
        ["temperature"],
        db_tools=[],
    )

    assert settings["temperature"] == 0.9
    assert settings["tool_settings"] == {
        "audio_generation": {
            "voice": "admin_voice",
            "response_format": "mp3",
        }
    }


def test_request_tool_settings_are_ignored_without_admin_tool_settings():
    settings, _ = merge_settings(
        {"temperature": 0.2},
        {
            "tool_settings": {
                "video_generation": {
                    "duration_seconds": 30,
                    "timeout_seconds": 3600,
                }
            }
        },
        ["temperature"],
        db_tools=[],
    )

    assert settings == {"temperature": 0.2}
