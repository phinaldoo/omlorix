"""Feature-owned compact history representation of tool results."""

from typing import Any
from app.tools.results import _copy_result_fields, _content_metadata


def _compact_skill_result(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    def compact_skill(skill: Any) -> Any:
        if not isinstance(skill, dict):
            return skill
        compact = _copy_result_fields(
            skill,
            (
                "id",
                "name",
                "description_length",
                "icon",
                "created_at",
                "updated_at",
                "content_length",
                "content_sha256",
                "selection",
                "truncated",
            ),
        )
        description = skill.get("description")
        if isinstance(description, str):
            compact["description"] = description[:500]
            compact["description_length"] = len(description)
        content = skill.get("content")
        compact.update(_content_metadata(content))
        return compact

    compact = _copy_result_fields(
        payload,
        (
            "status",
            "operation",
            "message",
            "count",
            "limit",
            "offset",
            "has_more",
            "next_cursor",
        ),
    )
    if isinstance(payload.get("draft"), dict):
        compact["draft"] = _copy_result_fields(
            payload["draft"],
            ("draft_id", "name", "file_count"),
        )
        draft_description = payload["draft"].get("description")
        if isinstance(draft_description, str):
            compact["draft"]["description"] = draft_description[:500]
    if isinstance(payload.get("skill"), dict):
        compact["skill"] = compact_skill(payload["skill"])
    if isinstance(payload.get("skills"), list):
        compact["skills"] = [compact_skill(skill) for skill in payload["skills"][:100]]
    return compact or {"status": "completed"}


compact_result = _compact_skill_result
