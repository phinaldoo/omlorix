import json
from typing import Any, Dict, Optional

from app.skills.models import Skills, list_skills as db_list_skills
from app.skills.utils import build_skill_draft_payload
from app.tools.text_edits import (
    DEFAULT_TOOL_TEXT_READ_CHARS,
    normalize_tool_text_query,
    select_text_content,
)
from app.utils.helpers import datetime_to_iso


DEFAULT_SKILLS_TOOL_PAGE_LIMIT = 20
MAX_SKILLS_TOOL_PAGE_LIMIT = 100
MAX_SKILLS_TOOL_OFFSET = 10_000


def _serialize_skill_summary(skill: Skills) -> Dict[str, Any]:
    description = str(skill.description or "")
    return {
        "id": skill.id,
        "name": skill.name,
        "description": description[:500],
        "description_length": len(description),
        "icon": skill.icon,
        "content_length": len(str(skill.content or "")),
        "created_at": datetime_to_iso(getattr(skill, "created_at", None)),
        "updated_at": datetime_to_iso(getattr(skill, "updated_at", None)),
    }


def _normalize_page(limit: int | None, offset: int | None) -> tuple[int, int]:
    try:
        normalized_limit = int(
            DEFAULT_SKILLS_TOOL_PAGE_LIMIT if limit is None else limit
        )
        normalized_offset = int(0 if offset is None else offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers.") from exc
    if normalized_limit < 1 or normalized_limit > MAX_SKILLS_TOOL_PAGE_LIMIT:
        raise ValueError(
            f"limit must be between 1 and {MAX_SKILLS_TOOL_PAGE_LIMIT}."
        )
    if normalized_offset < 0 or normalized_offset > MAX_SKILLS_TOOL_OFFSET:
        raise ValueError(f"offset must be between 0 and {MAX_SKILLS_TOOL_OFFSET}.")
    return normalized_limit, normalized_offset


def skills_tool(
    db,
    user_id: str,
    type: str,
    skill_id: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    content: Optional[str] = None,
    icon: Optional[str] = None,
    compatibility: Optional[str] = None,
    license_value: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    files: Optional[list[dict[str, Any]]] = None,
    query: Optional[str] = None,
    heading: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    max_chars: Optional[int] = DEFAULT_TOOL_TEXT_READ_CHARS,
    limit: Optional[int] = DEFAULT_SKILLS_TOOL_PAGE_LIMIT,
    offset: Optional[int] = 0,
    cursor: str | None = None,
) -> Dict[str, Any]:
    operation = str(type or "").strip().lower()

    if operation == "list":
        page_limit, page_offset = _normalize_page(limit, offset)
        normalized_query = normalize_tool_text_query(query)
        from app.skills.queries import list_skill_summaries
        return list_skill_summaries(db, user_id, query=normalized_query, limit=page_limit, offset=page_offset, cursor=cursor)

    if operation == "read":
        skill_id_value = str(skill_id or "").strip()
        if not skill_id_value:
            raise ValueError("skill_id is required for read")
        from app.skills.queries import skill_access
        access, _ = skill_access(user_id)
        skill = (
            db.query(Skills)
            .filter(Skills.id == skill_id_value, access)
            .first()
        )
        if not skill:
            raise ValueError("Skill not found")
        content_value, selection = select_text_content(
            str(skill.content or ""),
            heading=heading,
            query=query,
            start_line=start_line,
            end_line=end_line,
            max_chars=max_chars,
        )
        payload = _serialize_skill_summary(skill)
        payload["content"] = content_value
        payload["selection"] = selection
        payload["truncated"] = bool(selection.get("truncated"))
        return {"operation": "read", "skill": payload}

    if operation == "draft":
        if files is not None and not isinstance(files, list):
            raise ValueError("files must be an array")
        if isinstance(files, list) and len(files) > 20:
            raise ValueError("files may contain at most 20 entries")
        draft_payload = build_skill_draft_payload(
            db,
            user_id=user_id,
            name=str(name or "").strip(),
            description=str(description or "").strip(),
            content=str(content or ""),
            icon=str(icon).strip() if isinstance(icon, str) else None,
            compatibility=str(compatibility or "").strip() or None,
            license_value=str(license_value or "").strip() or None,
            metadata=metadata or None,
            files=files or None,
        )
        draft_data = {
            "name": draft_payload.get("name"),
            "description": draft_payload.get("description"),
            "file_count": draft_payload.get("file_count"),
        }

        return {
            "operation": "draft",
            "status": "draft_ready",
            "message": (
                "A skill draft proposal has been prepared for the user. "
                "It is not saved yet; the user must review and confirm it in the skill editor sidebar."
            ),
            "draft": draft_data,
            "widget": {
                "type": "skill_draft",
                # The provider adapters persist the widget content slot as a
                # string. It contains JSON for frontend-rendered widgets and is
                # parsed as data by the trusted chat component.
                "html": json.dumps(
                    draft_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "render_mode": "frontend",
                "model_context": {
                    "status": "draft_ready",
                    "draft": draft_data,
                },
            },
        }

    raise ValueError("Invalid type. Allowed values are: list, read, draft.")
