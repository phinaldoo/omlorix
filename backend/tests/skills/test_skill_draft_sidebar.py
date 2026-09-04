"""Regression tests for the structured skill-draft widget payload."""

import json
from types import SimpleNamespace

from app.tools import helper as tool_helper
from app.tools.skills import utils as skill_tool_utils


def test_skill_tool_summary_omits_content_and_all_share_identifiers():
    """A list result must stay small and must not expose capability tokens."""
    payload = skill_tool_utils._serialize_skill_summary(
        SimpleNamespace(
            id="skill-1",
            user_id="user-1",
            name="review",
            description="Review code",
            icon="sparkles",
            content="Be precise.",
            clone_share_id="clone-token",
            live_share_id=None,
            collaborate_share_id=None,
            created_at=None,
            updated_at=None,
        )
    )

    assert "share" not in payload
    assert "share_id" not in payload
    assert "clone_share_id" not in payload
    assert "live_share_id" not in payload
    assert "collaborate_share_id" not in payload
    assert "content" not in payload
    assert payload["content_length"] == len("Be precise.")


def test_skill_draft_tool_returns_data_only_frontend_widget(monkeypatch):
    """The backend sends draft state without generating chat-card markup."""

    draft = {
        "draft_id": "draft-123",
        "name": "release-notes",
        "description": "Prepare release notes",
        "skill_markdown": "---\nname: release-notes\n---\nBody",
        "files": [],
        "file_count": 0,
    }
    monkeypatch.setattr(
        skill_tool_utils,
        "build_skill_draft_payload",
        lambda *_args, **_kwargs: draft,
    )

    result = skill_tool_utils.skills_tool(
        db=object(),
        user_id="user-1",
        type="draft",
        name=draft["name"],
        description=draft["description"],
        content="Body",
    )

    widget = result["widget"]
    assert widget["type"] == "skill_draft"
    assert widget["render_mode"] == "frontend"
    assert json.loads(widget["html"]) == draft
    assert "<script" not in widget["html"]


def test_skill_draft_widget_is_streamed_for_transcript_persistence(monkeypatch):
    """The JSON widget content reaches the shared provider persistence path."""

    draft = {
        "draft_id": "draft-reload",
        "name": "reload-safe",
        "description": "Survives a page reload",
        "skill_markdown": "---\nname: reload-safe\n---\nBody",
        "files": [],
    }
    widget = {
        "type": "skill_draft",
        "html": json.dumps(draft, separators=(",", ":")),
        "render_mode": "frontend",
        "model_context": {"status": "draft_ready"},
    }

    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_helper, "_ensure_feature_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tool_helper,
        "skills_tool",
        lambda **_kwargs: {
            "status": "draft_ready",
            "message": "Draft prepared",
            "draft": {"name": draft["name"]},
            "widget": widget,
        },
    )

    stream = tool_helper.resolve_tool_call(
        db=object(),
        tool_name="skills",
        tool_arguments={
            "type": "draft",
            "name": draft["name"],
            "description": draft["description"],
        },
        user_id="user-1",
        group_id=None,
        project_id=None,
    )
    events = []
    while True:
        try:
            events.append(json.loads(next(stream)))
        except StopIteration as completed:
            resolved = completed.value
            break

    assert events[-1]["widget_type"] == "skill_draft"
    assert events[-1]["meta"]["render_mode"] == "frontend"
    assert resolved["widget"] == widget
