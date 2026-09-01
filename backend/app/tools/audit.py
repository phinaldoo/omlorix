"""Durable audit logging for model-facing tool mutations."""

from __future__ import annotations

from typing import Any, Mapping

from app.logging.models import stage_audit_log_event


def stage_tool_audit_action(
    db,
    user_id: str,
    action: str,
    *,
    category: str,
    details: Mapping[str, Any] | None = None,
):
    """Stage a model-tool event in the caller's mutation transaction."""

    return stage_audit_log_event(
        db,
        user_id=str(user_id),
        action=action,
        details={**dict(details or {}), "source": "tool"},
        user_agent="omlorix-tool",
        category=category,
    )
