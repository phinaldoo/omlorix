import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.helper import _raise_if_tool_error_payload


def test_tool_error_payload_raises_for_top_level_failure_status():
    with pytest.raises(ValueError, match="failed"):
        _raise_if_tool_error_payload(
            {"status": "error", "message": "failed"},
            tool_name="example_tool",
        )


def test_tool_error_payload_raises_for_top_level_ok_false():
    with pytest.raises(ValueError, match="failed"):
        _raise_if_tool_error_payload(
            {"ok": False, "error": "failed"},
            tool_name="example_tool",
        )


def test_tool_error_payload_ignores_nested_item_errors():
    _raise_if_tool_error_payload(
        {
            "status": "success",
            "result": [
                {
                    "url": "https://example.com",
                    "error": "blocked by policy",
                }
            ],
        },
        tool_name="example_tool",
    )


def test_tool_error_payload_ignores_top_level_informational_error_without_failure_signal():
    _raise_if_tool_error_payload(
        {
            "status": "success",
            "error": "partial result details are available in result items",
        },
        tool_name="example_tool",
    )
