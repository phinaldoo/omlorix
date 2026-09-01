import pytest

from app.tools import audit as tool_audit


def test_stage_tool_audit_action_uses_mutation_session_and_forces_tool_source(
    monkeypatch,
):
    mutation_db = object()
    audit_calls = []
    monkeypatch.setattr(
        tool_audit,
        "stage_audit_log_event",
        lambda db, **kwargs: audit_calls.append({"db": db, **kwargs}),
    )

    tool_audit.stage_tool_audit_action(
        mutation_db,
        "user-1",
        "CANVAS_UPDATED",
        category="files",
        details={"file_id": "file-1", "source": "untrusted-override"},
    )

    assert audit_calls == [
        {
            "db": mutation_db,
            "user_id": "user-1",
            "action": "CANVAS_UPDATED",
            "details": {"file_id": "file-1", "source": "tool"},
            "user_agent": "omlorix-tool",
            "category": "files",
        }
    ]


def test_stage_tool_audit_action_propagates_failure_before_mutation_commit(
    monkeypatch,
):
    monkeypatch.setattr(
        tool_audit,
        "stage_audit_log_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        tool_audit.stage_tool_audit_action(
            object(),
            "user-1",
            "NOTE_UPDATED",
            category="notes",
            details={"note_id": "note-1"},
        )
