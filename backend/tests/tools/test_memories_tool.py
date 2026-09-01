import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.memories.service import MemoryScope
from app.tools.memories.utils import _serialize_tool_memory, memories_tool


def _memory_fixture():
    return SimpleNamespace(
        id="memory-123",
        content="Prefers concise answers",
        source_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 2, 10, 30, tzinfo=timezone.utc),
    )


def _policy(*, active=True, auto_create=True, project=False):
    return SimpleNamespace(
        active=active,
        auto_create=auto_create,
        scope=MemoryScope.project("project-123")
        if project
        else MemoryScope.personal("user-123"),
    )


def test_serialize_tool_memory_omits_internal_ids():
    serialized = _serialize_tool_memory(_memory_fixture())

    assert serialized == {
        "content": "Prefers concise answers",
        "source_date": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-02T10:30:00+00:00",
    }
    assert "id" not in serialized


def test_memories_tool_creates_in_effective_scope():
    memory = _memory_fixture()

    def create_memory(_db, _scope, _content, *, before_commit):
        before_commit(memory, True)
        return memory, True

    with (
        patch(
            "app.tools.memories.utils._get_memory_policy",
            return_value=_policy(project=True),
        ),
        patch(
            "app.tools.memories.utils.create_memory",
            side_effect=create_memory,
        ) as create,
        patch("app.tools.memories.utils.stage_tool_audit_action"),
    ):
        result = memories_tool(
            db=object(),
            user_id="user-123",
            content="Prefers concise answers",
            project_id="project-123",
        )

    assert create.call_args.args == (
        create.call_args.args[0],
        MemoryScope.project("project-123"),
        "Prefers concise answers",
    )
    assert callable(create.call_args.kwargs["before_commit"])
    assert result["created"] is True
    assert result["memory"]["content"] == "Prefers concise answers"


@pytest.mark.parametrize(
    ("project", "created", "expected_action", "expected_details"),
    [
        (False, True, "MEMORY_CREATED", {"memory_id": "memory-123"}),
        (False, False, "MEMORY_DEDUPED", {"memory_id": "memory-123"}),
        (
            True,
            True,
            "PROJECT_MEMORY_CREATED",
            {"memory_id": "memory-123", "project_id": "project-123"},
        ),
        (
            True,
            False,
            "PROJECT_MEMORY_DEDUPED",
            {"memory_id": "memory-123", "project_id": "project-123"},
        ),
    ],
)
def test_memories_tool_audits_effective_scope_without_content(
    project,
    created,
    expected_action,
    expected_details,
):
    memory = _memory_fixture()
    db = object()

    def create_memory(_db, _scope, _content, *, before_commit):
        before_commit(memory, created)
        return memory, created

    with (
        patch(
            "app.tools.memories.utils._get_memory_policy",
            return_value=_policy(project=project),
        ),
        patch(
            "app.tools.memories.utils.create_memory",
            side_effect=create_memory,
        ),
        patch("app.tools.memories.utils.stage_tool_audit_action") as audit,
    ):
        memories_tool(
            db=db,
            user_id="user-123",
            content="Private memory content",
            project_id="project-123" if project else None,
        )

    audit.assert_called_once_with(
        db,
        "user-123",
        expected_action,
        category="memories",
        details=expected_details,
    )
    assert "Private memory content" not in repr(audit.call_args)


def test_memories_tool_audits_concurrent_deduplication():
    existing = _memory_fixture()
    previous_updated_at = existing.updated_at

    class RaceDb:
        def __init__(self):
            self.query_results = [None, existing]
            self.commits = 0
            self.rollbacks = 0
            self.refreshed = []

        def query(self, *_args):
            return self

        def filter(self, *_args):
            return self

        def first(self):
            return self.query_results.pop(0)

        def add(self, _memory):
            return None

        def flush(self):
            raise IntegrityError("insert memory", {}, RuntimeError("duplicate"))

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def refresh(self, memory):
            self.refreshed.append(memory)

    db = RaceDb()
    with (
        patch(
            "app.tools.memories.utils._get_memory_policy",
            return_value=_policy(),
        ),
        patch("app.tools.memories.utils.stage_tool_audit_action") as audit,
    ):
        result = memories_tool(
            db=db,
            user_id="user-123",
            content="Prefers concise answers",
        )

    assert result["created"] is False
    assert db.rollbacks == 1
    assert db.commits == 1
    assert db.refreshed == [existing]
    assert existing.updated_at > previous_updated_at
    audit.assert_called_once_with(
        db,
        "user-123",
        "MEMORY_DEDUPED",
        category="memories",
        details={"memory_id": "memory-123"},
    )


def test_memories_tool_does_not_treat_audit_integrity_error_as_deduplication():
    class NewMemoryDb:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def query(self, *_args):
            return self

        def filter(self, *_args):
            return self

        def first(self):
            return None

        def add(self, _memory):
            return None

        def flush(self):
            return None

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    db = NewMemoryDb()
    audit_error = IntegrityError(
        "insert audit event",
        {},
        RuntimeError("audit constraint failed"),
    )
    with (
        patch(
            "app.tools.memories.utils._get_memory_policy",
            return_value=_policy(),
        ),
        patch(
            "app.tools.memories.utils.stage_tool_audit_action",
            side_effect=audit_error,
        ),
    ):
        with pytest.raises(IntegrityError) as exc_info:
            memories_tool(
                db=db,
                user_id="user-123",
                content="Prefers concise answers",
            )

    assert exc_info.value is audit_error
    assert db.commits == 0
    assert db.rollbacks == 1


def test_memories_tool_respects_auto_create_opt_out():
    with patch(
        "app.tools.memories.utils._get_memory_policy",
        return_value=_policy(auto_create=False),
    ):
        with pytest.raises(ValueError, match="Automatic memory creation is disabled"):
            memories_tool(object(), "user-123", content="Remember this")
