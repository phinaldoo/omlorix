from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.files import models as file_models
from app.tools import helper


def test_canvas_saved_event_carries_originating_tool_call_id(monkeypatch):
    """The browser must be able to finalize the exact streamed Canvas draft."""

    monkeypatch.setattr(helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helper,
        "stage_tool_audit_action",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        file_models,
        "get_file",
        lambda db, file_id, user_id: SimpleNamespace(meta={}),
    )
    captured = {}

    def fake_save_canvas_markdown(**kwargs):
        captured.update(kwargs)
        kwargs["before_commit"](
            {
                "file_id": "canvas-file-1",
                "created": False,
                "content_type": "html",
            }
        )
        return {
            "file_id": "canvas-file-1",
            "file_name": "website.html",
            "page_count": None,
            "created": False,
            "content": "<main>Updated</main>",
            "content_type": "html",
            "canvas_revision": 2,
        }

    monkeypatch.setattr(helper, "save_canvas_markdown", fake_save_canvas_markdown)

    stream = helper.resolve_tool_call(
        db=object(),
        tool_name="canvas",
        tool_arguments={
            "type": "html",
            "file_id": "canvas-file-1",
            "expected_revision": 1,
            "edits": [
                {
                    "start_snippet": "<main>",
                    "end_snippet": "</main>",
                    "content": "<main>Updated</main>",
                }
            ],
        },
        user_id="user-1",
        group_id=None,
        project_id=None,
        tool_call_id="call-canvas-1",
    )

    event = json.loads(next(stream))

    assert event["t"] == "canvas_evt"
    assert event["event"] == "saved"
    assert event["data"]["file_id"] == "canvas-file-1"
    assert event["data"]["tool_call_id"] == "call-canvas-1"
    assert captured["content"] is None
    assert captured["expected_revision"] == 1
    assert len(captured["edits"]) == 1


def test_canvas_update_requires_a_revision_before_saving(monkeypatch):
    save = MagicMock()
    monkeypatch.setattr(helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(helper, "save_canvas_markdown", save)

    stream = helper.resolve_tool_call(
        db=object(),
        tool_name="canvas",
        tool_arguments={
            "type": "markdown",
            "file_id": "canvas-file-1",
            "content": "unsafe overwrite",
        },
        user_id="user-1",
        group_id=None,
        project_id=None,
    )

    try:
        next(stream)
    except ValueError as exc:
        assert "expected_revision is required" in str(exc)
    else:
        raise AssertionError("An existing Canvas update must require a revision")
    save.assert_not_called()
