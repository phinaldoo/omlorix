import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.settings_merge import merge_settings_update


def test_merge_settings_update_removes_explicitly_cleared_top_level_values():
    merged = merge_settings_update(
        {
            "skill_id": "skill-123",
            "temperature": 0.7,
        },
        {
            "skill_id": None,
        },
    )

    assert merged == {
        "temperature": 0.7,
    }


def test_merge_settings_update_removes_explicitly_cleared_nested_values():
    merged = merge_settings_update(
        {
            "reasoning": {
                "effort": "high",
            },
            "temperature": 0.7,
        },
        {
            "reasoning": {
                "effort": None,
            },
        },
    )

    assert merged == {
        "temperature": 0.7,
    }


def test_merge_settings_update_preserves_untouched_nested_values():
    merged = merge_settings_update(
        {
            "reasoning": {
                "effort": "high",
                "budget": 1024,
            },
        },
        {
            "reasoning": {
                "effort": None,
            },
        },
    )

    assert merged == {
        "reasoning": {
            "budget": 1024,
        },
    }
