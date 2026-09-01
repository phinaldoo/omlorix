from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("googleapiclient")

from app.connections import google_workspace_worker


def test_google_workspace_worker_registers_expected_tools_and_safety_annotations(monkeypatch):
    """The shipped launcher must expose both read and intentionally mutating tools."""
    monkeypatch.setenv("GOOGLE_WORKSPACE_ENABLED_CAPABILITIES", "gmail,calendar")
    tools = {
        tool.name: tool
        for tool in asyncio.run(google_workspace_worker.mcp.list_tools())
    }

    assert set(tools) == {
        "search_gmail_messages",
        "get_gmail_message",
        "create_gmail_draft",
        "send_gmail_message",
        "list_calendar_events",
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
    }
    assert tools["search_gmail_messages"].annotations.read_only_hint is True
    assert tools["list_calendar_events"].annotations.read_only_hint is True
    assert tools["send_gmail_message"].annotations.destructive_hint is True
    assert tools["delete_calendar_event"].annotations.destructive_hint is True


@pytest.mark.parametrize(
    ("enabled_capability", "expected_tools"),
    [
        (
            "gmail",
            {
                "search_gmail_messages",
                "get_gmail_message",
                "create_gmail_draft",
                "send_gmail_message",
            },
        ),
        (
            "calendar",
            {
                "list_calendar_events",
                "create_calendar_event",
                "update_calendar_event",
                "delete_calendar_event",
            },
        ),
    ],
)
def test_google_workspace_worker_only_advertises_enabled_capability(
    monkeypatch,
    enabled_capability,
    expected_tools,
):
    """A connection must not advertise tools for another Google product."""
    monkeypatch.setenv("GOOGLE_WORKSPACE_ENABLED_CAPABILITIES", enabled_capability)

    tool_names = {
        tool.name
        for tool in asyncio.run(google_workspace_worker.mcp.list_tools())
    }

    assert tool_names == expected_tools


def test_google_workspace_capabilities_require_exact_comma_delimited_tokens(monkeypatch):
    """Unknown values and substrings must not enable a Google capability."""
    monkeypatch.setenv(
        "GOOGLE_WORKSPACE_ENABLED_CAPABILITIES",
        " GMAIL, calendar , gmail-admin,notcalendar,unknown ",
    )

    assert google_workspace_worker._enabled_capabilities() == {"gmail", "calendar"}

    monkeypatch.setenv(
        "GOOGLE_WORKSPACE_ENABLED_CAPABILITIES",
        '["gmail", "calendar"]',
    )

    assert google_workspace_worker._enabled_capabilities() == set()
