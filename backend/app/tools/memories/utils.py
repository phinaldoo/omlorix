"""Model-facing tool for saving a durable memory."""

from __future__ import annotations

from typing import Any

from app.memories.runtime import get_memory_policy
from app.memories.service import create_memory
from app.tools.audit import stage_tool_audit_action
from app.utils.helpers import datetime_to_iso


_get_memory_policy = get_memory_policy


def _serialize_tool_memory(memory: Any) -> dict[str, Any]:
    """Return only fields useful to the model after saving a memory."""

    return {
        "content": memory.content,
        "source_date": memory.source_date.isoformat() if memory.source_date else None,
        "updated_at": datetime_to_iso(memory.updated_at),
    }


def memories_tool(
    db,
    user_id: str,
    content: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Save one memory in the effective personal or project scope."""

    policy = _get_memory_policy(db, user_id, project_id=project_id)
    if not policy.active:
        raise ValueError("Memories are not enabled for this user.")
    if not policy.auto_create:
        raise ValueError("Automatic memory creation is disabled for this user.")

    normalized_content = str(content or "").strip()
    if not normalized_content:
        raise ValueError("content is required")
    def stage_memory_audit(memory, created: bool) -> None:
        details = {"memory_id": memory.id}
        if policy.scope.project_id:
            details["project_id"] = policy.scope.project_id
        stage_tool_audit_action(
            db,
            user_id,
            (
                f"PROJECT_MEMORY_{'CREATED' if created else 'DEDUPED'}"
                if policy.scope.is_project
                else f"MEMORY_{'CREATED' if created else 'DEDUPED'}"
            ),
            category="memories",
            details=details,
        )

    memory, created = create_memory(
        db,
        policy.scope,
        normalized_content,
        before_commit=stage_memory_audit,
    )
    return {"memory": _serialize_tool_memory(memory), "created": created}
