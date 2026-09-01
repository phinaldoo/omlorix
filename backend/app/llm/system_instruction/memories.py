"""Build the single model-context block containing saved memories."""

from __future__ import annotations

from app.memories.runtime import get_memory_policy
from app.memories.service import list_memories


MAX_MEMORIES_IN_CONTEXT = 200


def get_memories_context(db, user_id: str, project_id: str | None = None) -> str:
    """Return a ready-to-attach memory context block for one model request."""

    if not db or not user_id:
        return ""
    policy = get_memory_policy(db, user_id, project_id=project_id)
    if not policy.include_in_context:
        return ""
    contents = [
        str(memory.content or "").strip()
        for memory in list_memories(db, policy.scope, limit=MAX_MEMORIES_IN_CONTEXT)
        if str(memory.content or "").strip()
    ]
    if not contents:
        return ""
    lines = [
        "The following saved memories may help personalize your response.",
        "Use them only when relevant and do not repeat them unnecessarily.",
        "",
        *(f"{index}. {content}" for index, content in enumerate(contents, start=1)),
        "",
        "End of saved memory context. Continue with the main conversation.",
    ]
    return "\n".join(lines)
