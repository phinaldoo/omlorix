from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.automations.models import (
    create_automation as db_create_automation,
    get_webhook_trigger_for_automation,
    list_automations as db_list_automations,
    update_automation as db_update_automation,
    delete_automation as db_delete_automation,
)
from app.groups.init import get_user_group_setting_value
from app.skills.models import (
    ADMIN_SKILLS_USER_ID,
    get_subscribed_skills,
    list_admin_skills_by_ids,
    list_skills,
)
from app.skills.utils import load_skill_markdown_fields
from app.tools.audit import stage_tool_audit_action
from app.users.init import get_user_setting_value
from app.utils.helpers import datetime_to_iso

AUTOMATION_PRESET_ICONS = [
    {"value": "folder", "label": "Folder icon"},
    {"value": "archive", "label": "Archive icon"},
    {"value": "document", "label": "Document icon"},
    {"value": "image", "label": "Image icon"},
    {"value": "video", "label": "Video icon"},
    {"value": "music", "label": "Music icon"},
    {"value": "code", "label": "Code icon"},
    {"value": "star", "label": "Star icon"},
    {"value": "heart", "label": "Heart icon"},
    {"value": "home", "label": "Home icon"},
    {"value": "briefcase", "label": "Briefcase icon"},
    {"value": "book", "label": "Book icon"},
    {"value": "camera", "label": "Camera icon"},
    {"value": "download", "label": "Download icon"},
    {"value": "lock", "label": "Lock icon"},
    {"value": "secure", "label": "Shield icon"},
    {"value": "quick", "label": "Lightning icon"},
]

AUTOMATION_ICON_COLORS = [
    {"value": "#FF6B6B", "label": "Coral red"},
    {"value": "#FF8A65", "label": "Warm orange"},
    {"value": "#FFB74D", "label": "Amber"},
    {"value": "#FFE082", "label": "Soft yellow"},
    {"value": "#F4FF81", "label": "Lime"},
    {"value": "#81C784", "label": "Green"},
    {"value": "#4DB6AC", "label": "Teal"},
    {"value": "#4FC3F7", "label": "Sky blue"},
    {"value": "#9575CD", "label": "Violet"},
    {"value": "#F06292", "label": "Pink"},
]


# Webhook credentials and public trigger behavior are security-sensitive. The
# model-facing tool may describe an existing trigger so the model can explain
# an automation accurately, but only the user-facing Automations interface may
# create, change, rotate, or remove a trigger.
WEBHOOK_MANAGEMENT_USER_MESSAGE = (
    "Webhook triggers cannot be created, changed, rotated, or deleted with the "
    "automations tool. Inform the user that they must manage the webhook "
    "themselves in the Automations interface."
)


def _serialize_automation(automation) -> Dict[str, Any]:
    db = getattr(getattr(automation, "_sa_instance_state", None), "session", None)
    webhook_trigger = get_webhook_trigger_for_automation(db, automation.user_id, automation.id) if db is not None else None
    return {
        "id": automation.id,
        "user_id": automation.user_id,
        "title": automation.title,
        "icon": automation.icon,
        "icon_color": automation.icon_color,
        "prompt": automation.prompt,
        "model_id": automation.model_id,
        "schedule_rules": automation.schedule_rules,
        "schedule_timezone": automation.schedule_timezone,
        "skill_id": automation.skill_id,
        "note_ids": automation.note_ids or [],
        "file_ids": automation.file_ids or [],
        "mcp_server_ids": getattr(automation, "mcp_server_ids", None) or [],
        "webhook_trigger": _serialize_webhook_trigger(webhook_trigger),
        "is_active": bool(automation.is_active),
        "last_triggered_at": datetime_to_iso(getattr(automation, "last_triggered_at", None)),
        "created_at": datetime_to_iso(getattr(automation, "created_at", None)),
        "last_updated_at": datetime_to_iso(getattr(automation, "last_updated_at", None)),
    }


def _serialize_webhook_trigger(trigger) -> Dict[str, Any] | None:
    if not trigger:
        return None
    return {
        "id": trigger.id,
        "automation_id": trigger.automation_id,
        "name": trigger.name,
        "is_enabled": bool(trigger.is_enabled),
        "token_prefix": trigger.token_prefix,
        "payload_mode": trigger.payload_mode,
        "include_headers": bool(trigger.include_headers),
        "allowed_header_names": trigger.allowed_header_names or [],
        "max_body_bytes": trigger.max_body_bytes,
        "rate_limit_per_minute": trigger.rate_limit_per_minute,
        "last_triggered_at": datetime_to_iso(getattr(trigger, "last_triggered_at", None)),
    }


def _normalize_numbered_icon(value: Any) -> str | None:
    """Return the stored automation icon value for a tool-provided picker number."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        icon_number = value
    elif isinstance(value, str) and value.strip().isdigit():
        icon_number = int(value.strip())
    else:
        return str(value).strip() if str(value or "").strip() else None

    preset_count = len(AUTOMATION_PRESET_ICONS)
    if 1 <= icon_number <= preset_count:
        return AUTOMATION_PRESET_ICONS[icon_number - 1]["value"]
    raise ValueError(f"icon must be a number from 1 to {preset_count}")


def _normalize_numbered_color(value: Any) -> str | None:
    """Return a hex color from either a picker number or an existing hex value."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        color_number = value
    elif isinstance(value, str) and value.strip().isdigit():
        color_number = int(value.strip())
    else:
        normalized = str(value or "").strip()
        return normalized or None

    if 1 <= color_number <= len(AUTOMATION_ICON_COLORS):
        return AUTOMATION_ICON_COLORS[color_number - 1]["value"]
    raise ValueError(f"icon_color must be a number from 1 to {len(AUTOMATION_ICON_COLORS)}")


def _serialize_model_option(model_payload: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a model picker entry to the fields the automation tool requires."""
    model_id = str(model_payload.get("id") or model_payload.get("model_id") or "").strip()
    if not model_id:
        return None
    return {
        "id": model_id,
        "name": str(model_payload.get("name") or model_id).strip() or model_id,
    }


def _list_accessible_model_options(db, user_id: str) -> list[dict[str, Any]]:
    """Return accessible base models that the automation runtime can execute.

    The shared chat model picker deliberately includes custom agents. Automation
    persistence and background execution, however, resolve ``model_id`` against
    the base ``Models`` table. Filtering at this boundary keeps the information
    response aligned with create/edit validation and prevents an agent ID from
    being dereferenced as a base-model record during MCP eligibility lookup.
    """
    from app.llm.utils import list_user_models

    model_options = []
    for model_payload in list_user_models(db, user_id) or []:
        if not isinstance(model_payload, dict):
            continue

        # Keep user-managed base models while excluding agent wrapper IDs,
        # which the background executor cannot resolve through Models.
        if model_payload.get("model_kind") == "agent":
            continue

        option = _serialize_model_option(model_payload)
        if option:
            model_options.append(option)
    return model_options


def _list_model_eligible_mcp_options(
    db,
    user_id: str,
    model_options: list[dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    """Return the MCP server IDs each accessible automation model may use."""
    from app.llm.models import get_model
    from app.mcp.utils import list_mcp_mention_connectors
    from app.tools.utils import resolve_enabled_tools

    options_by_model: dict[str, list[dict[str, str]]] = {}
    for model_option in model_options:
        model_id = str(model_option.get("id") or "").strip()
        if not model_id:
            continue

        try:
            model = get_model(db, model_id)
        except HTTPException as exc:
            # A model can be disabled or deleted after list_user_models() has
            # produced its snapshot. A stale record should not make the entire
            # information operation fail; create/edit will still perform their
            # authoritative access validation if the caller submits that ID.
            if exc.status_code == 404:
                continue
            raise
        model_settings = model.settings if isinstance(model.settings, dict) else {}
        tool_resolution = resolve_enabled_tools(
            model.tools or [],
            db=db,
            model_settings=model_settings,
            user_id=user_id,
        )
        connectors = []
        if tool_resolution.get("mcp_requested"):
            connectors = [
                {
                    "id": str(connector.get("id") or ""),
                    "name": str(connector.get("name") or "MCP Server"),
                    "description": str(connector.get("description") or ""),
                }
                for connector in list_mcp_mention_connectors(
                    db,
                    user_id,
                    model_settings=model_settings,
                )
                if str(connector.get("id") or "").strip()
            ]
        options_by_model[model_id] = connectors
    return options_by_model


def _serialize_skill_option(skill, owner_user_id: str, *, is_admin_skill: bool = False) -> dict[str, Any]:
    """Return the id/name/description triple for a skill visible to the user."""
    markdown_fields = load_skill_markdown_fields(owner_user_id, skill.id)
    return {
        "id": skill.id,
        "name": skill.name,
        "description": markdown_fields.get("description") or skill.description,
        "is_admin_skill": is_admin_skill,
    }


def _list_accessible_skill_options(db, user_id: str) -> list[dict[str, Any]]:
    """Mirror the skills picker access rules without returning skill files or content."""
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()

    for skill in list_skills(db, user_id):
        skills.append(_serialize_skill_option(skill, user_id))
        seen.add(skill.id)

    for skill, _subscription in get_subscribed_skills(db, user_id):
        if skill.id in seen:
            continue
        skills.append(_serialize_skill_option(skill, skill.user_id))
        seen.add(skill.id)

    admin_skill_ids = get_user_group_setting_value(user_id, "skills", "admin_skill_ids", db)
    if isinstance(admin_skill_ids, list):
        for skill in list_admin_skills_by_ids(db, admin_skill_ids):
            if skill.id in seen:
                continue
            skills.append(_serialize_skill_option(skill, ADMIN_SKILLS_USER_ID, is_admin_skill=True))
            seen.add(skill.id)

    return skills


def _build_information_response(db, user_id: str) -> dict[str, Any]:
    """Build detailed model-facing usage instructions for the automations tool."""
    available_models = _list_accessible_model_options(db, user_id)
    icon_options = [
        {"number": index + 1, "label": icon["label"]}
        for index, icon in enumerate(AUTOMATION_PRESET_ICONS)
    ]
    return {
        "tool": "automations",
        "instruction": (
            "Always call type='information' first before list, create, edit, or delete so you can use "
            "valid model IDs, model-eligible MCP server IDs, skill IDs, icon numbers, color numbers, "
            "and schedule settings. Webhook triggers are user-managed: never attempt to create, change, "
            "rotate, or delete one. If asked, tell the user to manage it themselves in the Automations interface."
        ),
        "webhook_policy": WEBHOOK_MANAGEMENT_USER_MESSAGE,
        "categories": {
            "information": {
                "description": "Returns these instructions plus user-accessible models, model-eligible MCP servers, and skills.",
                "required_inputs": ["type"],
            },
            "list": {
                "description": "Lists the user's automations and existing webhook trigger summaries.",
                "required_inputs": ["type"],
            },
            "create": {
                "description": (
                    "Creates an automation that runs a prompt on a selected model. The user must configure "
                    "any webhook trigger themselves in the Automations interface."
                ),
                "required_inputs": ["type", "title", "prompt", "model_id"],
                "optional_inputs": [
                    "icon",
                    "icon_color",
                    "schedule_rules",
                    "schedule_timezone",
                    "skill_id",
                    "note_ids",
                    "file_ids",
                    "mcp_server_ids",
                    "is_active",
                ],
            },
            "edit": {
                "description": "Updates an existing automation; omitted optional fields are left unchanged.",
                "required_inputs": ["type", "automation_id"],
                "optional_inputs": [
                    "title",
                    "prompt",
                    "model_id",
                    "icon",
                    "icon_color",
                    "schedule_rules",
                    "schedule_timezone",
                    "skill_id",
                    "note_ids",
                    "file_ids",
                    "mcp_server_ids",
                    "is_active",
                ],
            },
            "delete": {
                "description": (
                    "Deletes an automation only when it has no webhook trigger. The user must remove a webhook "
                    "trigger themselves in the Automations interface first."
                ),
                "required_inputs": ["type", "automation_id"],
            },
        },
        "inputs": {
            "model_id": "Use only an id from available_models. The tool call must provide the id, not the name.",
            "icon": "Provide a valid integer number from icon_options.",
            "icon_color": "Provide a valid integer number from color_options.",
            "schedule_rules": (
                "For recurring runs, provide objects with days (0=Mon..6=Sun) and times (HH:MM). "
                "For one-time runs, provide an object with run_at as an ISO datetime."
            ),
            "schedule_timezone": "IANA timezone for recurring rules, such as Europe/Berlin. If omitted for recurring rules, the user's timezone is used.",
            "skill_id": "Optional. Use only an id from available_skills.",
            "file_ids": "Optional. File IDs must be user-provided or agent-generated file IDs.",
            "mcp_server_ids": (
                "Optional. Use only server ids listed for the selected model in "
                "available_mcp_servers_by_model."
            ),
        },
        "icon_options": icon_options,
        "color_options": [
            {"number": index + 1, "label": color["label"], "value": color["value"]}
            for index, color in enumerate(AUTOMATION_ICON_COLORS)
        ],
        "available_models": available_models,
        "available_mcp_servers_by_model": _list_model_eligible_mcp_options(
            db,
            user_id,
            available_models,
        ),
        "available_skills": _list_accessible_skill_options(db, user_id),
    }


def automations_tool(
    db,
    user_id: str,
    type: str,
    automation_id: Optional[str] = None,
    title: Optional[str] = None,
    prompt: Optional[str] = None,
    model_id: Optional[str] = None,
    icon: Optional[str] = None,
    icon_color: Optional[str] = None,
    schedule_rules: Optional[List[Dict[str, Any]]] = None,
    schedule_timezone: Optional[str] = None,
    skill_id: Optional[str] = None,
    note_ids: Optional[List[str]] = None,
    file_ids: Optional[List[str]] = None,
    mcp_server_ids: Optional[List[str]] = None,
    # Kept as a defensive compatibility boundary for stale tool calls emitted
    # from an older schema. It is no longer advertised to models and any value
    # is rejected before an automation can be mutated.
    webhook_trigger: Optional[Dict[str, Any]] = None,
    is_active: Optional[bool] = None,
) -> Dict[str, Any]:
    operation = str(type or "").strip().lower()

    if webhook_trigger is not None:
        raise ValueError(WEBHOOK_MANAGEMENT_USER_MESSAGE)

    def resolve_schedule_timezone() -> Optional[str]:
        if not isinstance(schedule_rules, list):
            return schedule_timezone
        has_recurring = any(
            isinstance(rule, dict)
            and not str(rule.get("run_at") or "").strip()
            and isinstance(rule.get("days"), list)
            and isinstance(rule.get("times"), list)
            for rule in schedule_rules
        )
        if not has_recurring:
            return schedule_timezone
        normalized = str(schedule_timezone or "").strip()
        if normalized:
            return normalized
        user_timezone = get_user_setting_value(user_id, "general", "timezone", db)
        return str(user_timezone or "").strip() or "UTC"

    if operation == "information":
        return _build_information_response(db, user_id)

    if operation == "list":
        automations = db_list_automations(db, user_id)
        return {"automations": [_serialize_automation(automation) for automation in automations]}

    if operation == "create":
        normalized_icon = _normalize_numbered_icon(icon)
        normalized_icon_color = _normalize_numbered_color(icon_color)
        missing_fields = [
            field_name
            for field_name, field_value in (
                ("title", title),
                ("prompt", prompt),
                ("model_id", model_id),
            )
            if not str(field_value or "").strip()
        ]
        if missing_fields:
            raise ValueError(
                f"Missing required field(s) for create: {', '.join(missing_fields)}"
            )
        try:
            automation = db_create_automation(
                db=db,
                user_id=user_id,
                title=str(title).strip(),
                prompt=str(prompt).strip(),
                model_id=str(model_id).strip(),
                icon=normalized_icon or "folder",
                icon_color=normalized_icon_color or "#FF6B6B",
                schedule_rules=schedule_rules or [],
                schedule_timezone=resolve_schedule_timezone(),
                skill_id=skill_id,
                note_ids=note_ids or [],
                file_ids=file_ids or [],
                mcp_server_ids=mcp_server_ids or [],
                is_active=True if is_active is None else bool(is_active),
                commit=False,
            )
            stage_tool_audit_action(
                db,
                user_id,
                "AUTOMATION_CREATED",
                category="automations",
                details={
                    "automation_id": automation.id,
                    "model_id": automation.model_id,
                    "schedule_rule_count": len(automation.schedule_rules or []),
                    "schedule_timezone": automation.schedule_timezone,
                    "skill_id": automation.skill_id,
                    "note_count": len(automation.note_ids or []),
                    "file_count": len(automation.file_ids or []),
                    "connection_count": len(
                        getattr(automation, "mcp_server_ids", None) or []
                    ),
                    "is_active": bool(automation.is_active),
                },
            )
            db.commit()
            db.refresh(automation)
        except Exception:
            db.rollback()
            raise
        return {
            "status": "success",
            "message": "Automation created successfully",
            "automation": _serialize_automation(automation),
        }

    if operation == "edit":
        normalized_icon = _normalize_numbered_icon(icon)
        normalized_icon_color = _normalize_numbered_color(icon_color)
        automation_id_value = str(automation_id or "").strip()
        if not automation_id_value:
            raise ValueError("automation_id is required for edit")
        try:
            automation = db_update_automation(
                db=db,
                user_id=user_id,
                automation_id=automation_id_value,
                title=title,
                prompt=prompt,
                model_id=model_id,
                icon=normalized_icon,
                icon_color=normalized_icon_color,
                schedule_rules=schedule_rules,
                schedule_timezone=(
                    resolve_schedule_timezone()
                    if schedule_rules is not None or schedule_timezone is not None
                    else None
                ),
                skill_id=skill_id,
                note_ids=note_ids,
                file_ids=file_ids,
                mcp_server_ids=mcp_server_ids,
                is_active=is_active,
                commit=False,
            )
            stage_tool_audit_action(
                db,
                user_id,
                "AUTOMATION_UPDATED",
                category="automations",
                details={
                    "automation_id": automation.id,
                    "updated_fields": sorted(
                        field_name
                        for field_name, value in (
                            ("title", title),
                            ("prompt", prompt),
                            ("model_id", model_id),
                            ("icon", icon),
                            ("icon_color", icon_color),
                            ("schedule_rules", schedule_rules),
                            ("schedule_timezone", schedule_timezone),
                            ("skill_id", skill_id),
                            ("note_ids", note_ids),
                            ("file_ids", file_ids),
                            ("mcp_server_ids", mcp_server_ids),
                            ("is_active", is_active),
                        )
                        if value is not None
                    ),
                },
            )
            db.commit()
            db.refresh(automation)
        except Exception:
            db.rollback()
            raise
        return {
            "status": "success",
            "message": "Automation updated successfully",
            "automation": _serialize_automation(automation),
        }

    if operation == "delete":
        automation_id_value = str(automation_id or "").strip()
        if not automation_id_value:
            raise ValueError("automation_id is required for delete")
        # Deleting an automation cascades to its webhook trigger. Refuse that
        # indirect mutation and direct the user to the UI instead.
        if get_webhook_trigger_for_automation(db, user_id, automation_id_value):
            raise ValueError(
                "This automation has a webhook trigger and cannot be deleted with the automations tool. "
                "Inform the user that they must remove the webhook themselves in the Automations interface first."
            )
        try:
            db_delete_automation(
                db=db,
                user_id=user_id,
                automation_id=automation_id_value,
                commit=False,
            )
            stage_tool_audit_action(
                db,
                user_id,
                "AUTOMATION_DELETED",
                category="automations",
                details={"automation_id": automation_id_value},
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {
            "status": "success",
            "message": "Automation deleted successfully",
            "automation_id": automation_id_value,
        }

    raise ValueError("Invalid type. Allowed values are: information, list, create, edit, delete.")
