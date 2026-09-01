import json
from typing import Any, Dict, Optional

from app.skills.models import Skills, list_skills as db_list_skills
from app.skills.utils import build_skill_draft_payload
from app.utils.helpers import datetime_to_iso


def _serialize_skill_for_tool(skill: Skills) -> Dict[str, Any]:
    return {
        "id": skill.id,
        "user_id": skill.user_id,
        "name": skill.name,
        "description": skill.description,
        "icon": skill.icon,
        "content": skill.content,
        "clone_share_id": skill.clone_share_id,
        "live_share_id": skill.live_share_id,
        "collaborate_share_id": skill.collaborate_share_id,
        "created_at": datetime_to_iso(getattr(skill, "created_at", None)),
        "updated_at": datetime_to_iso(getattr(skill, "updated_at", None)),
    }


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
) -> Dict[str, Any]:
    operation = str(type or "").strip().lower()

    if operation == "list":
        skills = db_list_skills(db, user_id)
        return {
            "skills": [_serialize_skill_for_tool(skill) for skill in skills],
            "count": len(skills),
        }

    if operation == "read":
        skill_id_value = str(skill_id or "").strip()
        if not skill_id_value:
            raise ValueError("skill_id is required for read")
        skill = (
            db.query(Skills)
            .filter(Skills.id == skill_id_value, Skills.user_id == str(user_id).strip())
            .first()
        )
        if not skill:
            raise ValueError("Skill not found")
        return {"skill": _serialize_skill_for_tool(skill)}

    if operation == "draft":
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
