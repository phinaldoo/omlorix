import json
from unittest.mock import MagicMock

import pytest
from jsonschema import Draft202012Validator

from app.tools import helper
from app.tools.canvas_markdown.schemas import (
    CanvasValidationError,
    parse_canvas_tool_arguments,
)
from app.tools.errors import ToolErrorTracker
from app.tools.schemas import tool_schemas
from app.tools.utils import get_tool_schemas


EDIT = {"start_snippet": "old", "end_snippet": "old", "content": "new"}


@pytest.mark.parametrize(
    "arguments",
    [
        {"type": "markdown", "content": "# New", "edits": None},
        {"content": "", "file_ids": []},
        {"type": "view", "file_id": "file-1", "max_chars": 100},
        {"file_id": "file-1", "expected_revision": 1, "content": "replacement"},
        {
            "file_id": "file-1",
            "expected_revision": 1,
            "content": "",
            "start_snippet": "old",
            "end_snippet": "old",
        },
        {"file_id": "file-1", "expected_revision": 1, "edits": [EDIT], "content": None},
    ],
)
def test_canvas_schema_and_backend_accept_each_operation(arguments):
    schema = get_tool_schemas(["canvas"])[0]
    assert schema["strict"] is False
    Draft202012Validator.check_schema(schema["parameters"])
    Draft202012Validator(schema["parameters"]).validate(arguments)
    parsed = parse_canvas_tool_arguments(arguments)
    for key, value in arguments.items():
        if value is not None:
            assert parsed[key] == value


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"content": "# New", "edits": [EDIT]}, "canvas_file_required"),
        ({"type": "view"}, "canvas_file_required"),
        ({"file_id": "file-1", "content": "overwrite"}, "canvas_revision_required"),
        (
            {"file_id": "file-1", "expected_revision": True, "edits": [EDIT]},
            "canvas_revision_required",
        ),
        ({"type": "markdown"}, "canvas_content_required"),
        (
            {
                "file_id": "file-1",
                "expected_revision": 1,
                "content": "ambiguous",
                "edits": [EDIT],
            },
            "canvas_invalid_arguments",
        ),
        (
            {"file_id": "file-1", "expected_revision": 1, "edits": []},
            "canvas_invalid_arguments",
        ),
        (
            {
                "file_id": "file-1",
                "expected_revision": 1,
                "edits": [{**EDIT, "content": 42}],
            },
            "canvas_invalid_arguments",
        ),
        (
            {"type": "view", "file_id": "file-1", "content": "must not be written"},
            "canvas_invalid_arguments",
        ),
    ],
)
def test_canvas_invalid_operations_are_rejected_by_schema_and_backend(arguments, code):
    assert not Draft202012Validator(tool_schemas["canvas"]["parameters"]).is_valid(
        arguments
    )
    with pytest.raises(CanvasValidationError) as error:
        parse_canvas_tool_arguments(arguments)
    assert error.value.code == code


def test_canvas_legacy_aliases_and_asset_preservation():
    assert parse_canvas_tool_arguments(
        {
            "markdown": "old caller",
            "file_id": "",
            "start_snippet": "",
            "end_snippet": "",
        }
    ) == {
        "type": "markdown",
        "content": "old caller",
    }
    assert parse_canvas_tool_arguments({"type": "view", "id": "file-1"}) == {
        "type": "view",
        "file_id": "file-1",
    }
    edit = {"file_id": "file-1", "expected_revision": 1, "edits": [EDIT]}
    assert "file_ids" not in parse_canvas_tool_arguments({**edit, "file_ids": None})
    assert parse_canvas_tool_arguments({**edit, "file_ids": []})["file_ids"] == []
    assert "type" not in parse_canvas_tool_arguments(edit)


def test_canvas_schema_survives_google_provider_conversion():
    from app.llm.google_aistudio.utils import _build_aistudio_tools_payload

    payload = _build_aistudio_tools_payload(get_tool_schemas(["canvas"]))
    declaration = payload[0].function_declarations[0]
    assert declaration.name == "canvas"
    assert len(declaration.parameters.any_of) == 5


def test_failed_creation_emits_safe_error_before_storage_and_allows_one_correction(
    monkeypatch,
):
    monkeypatch.setattr(
        helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None
    )
    save = MagicMock()
    monkeypatch.setattr(helper, "save_canvas_markdown", save)
    # Reproduce the actual call's conflicting create/edit fields and filler values.
    arguments = {
        "type": "markdown",
        "filename": "brief.md",
        "file_id": "",
        "id": "",
        "start_snippet": "",
        "end_snippet": "",
        "expected_revision": 0,
        "edits": [{"start_snippet": "x", "end_snippet": "x", "content": "x"}],
        "heading": "",
        "query": "",
        "start_line": 1,
        "end_line": 1,
        "max_chars": 1,
        "content": "private document text",
        "file_ids": [],
    }
    stream = helper.resolve_tool_call(
        db=object(),
        tool_name="canvas",
        tool_arguments=arguments,
        user_id="user-1",
        group_id=None,
        project_id=None,
        tool_call_id="call-1",
    )
    event = json.loads(next(stream))
    assert event["t"] == "t_e"
    assert event["d"]["error_code"] == "canvas_file_required"
    assert event["d"]["id"] == "call-1"
    with pytest.raises(CanvasValidationError) as error:
        next(stream)
    save.assert_not_called()
    tracker = ToolErrorTracker()
    first = tracker.record("canvas", error.value)
    assert first.retry_allowed is True
    assert "edits to null" in first.public_message
    assert "private document text" not in first.model_output
    second = tracker.record("canvas", error.value)
    assert second.retry_allowed is False
    assert second.stop_tool_calls is True


def test_corrected_creation_reaches_storage_with_no_edits(monkeypatch):
    monkeypatch.setattr(
        helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None
    )
    # Stop at storage to verify normalized arguments without producing an artifact.
    save = MagicMock(side_effect=RuntimeError("storage unavailable"))
    monkeypatch.setattr(helper, "save_canvas_markdown", save)
    stream = helper.resolve_tool_call(
        db=object(),
        tool_name="canvas",
        tool_arguments={"type": "markdown", "content": "# Brief", "edits": None},
        user_id="user-1",
        group_id=None,
        project_id=None,
    )
    with pytest.raises(RuntimeError, match="storage unavailable"):
        list(stream)
    assert save.call_args.kwargs["content"] == "# Brief"
    assert save.call_args.kwargs["edits"] is None
    assert save.call_args.kwargs["file_id"] is None
    assert (
        ToolErrorTracker()
        .record("canvas", RuntimeError("storage unavailable"))
        .error_code
        is None
    )
