from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.automations.models import Automation, create_automation, get_webhook_trigger_for_automation
from app.automations.schemas import AutomationCreate
from app.utils.export_versions import matches_export_version


AUTOMATIONS_EXPORT_TYPE = "omlorix_automations_export"
AUTOMATIONS_EXPORT_VERSION = 1.0


def _serialize_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _automation_to_export_payload(automation: Automation) -> dict[str, Any]:
    db = getattr(getattr(automation, "_sa_instance_state", None), "session", None)
    webhook_trigger = get_webhook_trigger_for_automation(db, automation.user_id, automation.id) if db is not None else None
    return {
        "id": automation.id,
        "title": automation.title,
        "icon": automation.icon,
        "icon_color": automation.icon_color,
        "prompt": automation.prompt,
        "model_id": automation.model_id,
        "schedule_rules": automation.schedule_rules or [],
        "schedule_timezone": automation.schedule_timezone,
        "skill_id": automation.skill_id,
        "note_ids": automation.note_ids or [],
        "file_ids": automation.file_ids or [],
        "mcp_server_ids": getattr(automation, "mcp_server_ids", None) or [],
        "is_active": automation.is_active,
        "webhook_trigger": _webhook_trigger_to_export_payload(webhook_trigger),
        "last_triggered_at": _serialize_datetime(automation.last_triggered_at),
        "created_at": _serialize_datetime(automation.created_at),
        "last_updated_at": _serialize_datetime(automation.last_updated_at),
    }


def _webhook_trigger_to_export_payload(trigger) -> dict[str, Any] | None:
    if not trigger:
        return None
    return {
        "name": trigger.name,
        "is_enabled": False,
        "payload_mode": trigger.payload_mode,
        "include_headers": trigger.include_headers,
        "allowed_header_names": trigger.allowed_header_names or [],
        "max_body_bytes": trigger.max_body_bytes,
        "rate_limit_per_minute": trigger.rate_limit_per_minute,
    }


def export_user_automations(db: Session, user_id: str) -> dict[str, Any]:
    automations = (
        db.query(Automation)
        .filter(Automation.user_id == user_id)
        .order_by(Automation.created_at.asc(), Automation.id.asc())
        .all()
    )
    return {
        "export_type": AUTOMATIONS_EXPORT_TYPE,
        "export_version": AUTOMATIONS_EXPORT_VERSION,
        "data": {
            "automations": [_automation_to_export_payload(automation) for automation in automations],
        },
    }


def _extract_automation_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return validated automation rows from the sole supported export shape."""
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid automation export payload. Missing 'data' object.",
        )
    entries = data.get("automations")
    if not isinstance(entries, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid automation export payload. 'automations' must be a list.",
        )
    if any(not isinstance(entry, dict) for entry in entries):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid automation export payload. Every automation must be an object.",
        )
    return entries


def import_user_automations(db: Session, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Import automations without restoring webhook trigger credentials or state.

    Webhook exports contain non-secret configuration only. Importing that
    configuration as a trigger would either require unavailable reservation
    credentials or silently create a new public endpoint. The import therefore
    restores the automation itself and explicitly reports that its webhook must
    be configured again by the user.
    """
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid import payload. Expected an object.",
        )
    if payload.get("export_type") != AUTOMATIONS_EXPORT_TYPE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export_type '{payload.get('export_type')}'.",
        )
    export_version = payload.get("export_version")
    if not matches_export_version(export_version, AUTOMATIONS_EXPORT_VERSION):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported export_version '{export_version}'. "
                f"Expected '{AUTOMATIONS_EXPORT_VERSION}'."
            ),
        )

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped_webhook_triggers = 0

    for index, automation_export in enumerate(_extract_automation_entries(payload)):
        try:
            # AutomationCreate intentionally requires server-reserved credentials
            # when a create request includes a webhook. Export files never contain
            # those secrets, so remove the webhook metadata before validating the
            # ordinary automation fields. This also guarantees imports can never
            # restore or select webhook credentials supplied by an archive.
            automation_payload = dict(automation_export)
            webhook_trigger_skipped = automation_payload.pop("webhook_trigger", None) is not None
            validated = AutomationCreate.model_validate(automation_payload)
            automation = create_automation(
                db=db,
                user_id=user_id,
                title=validated.title,
                prompt=validated.prompt,
                model_id=validated.model_id,
                icon=validated.icon or "folder",
                icon_color=validated.icon_color or "#FF6B6B",
                schedule_rules=[
                    rule.model_dump()
                    for rule in (validated.schedule_rules or [])
                ],
                schedule_timezone=validated.schedule_timezone,
                skill_id=validated.skill_id,
                note_ids=validated.note_ids or [],
                file_ids=validated.file_ids or [],
                mcp_server_ids=validated.mcp_server_ids or [],
                is_active=validated.is_active if validated.is_active is not None else True,
                # Connection IDs are installation-specific. Restore the ones
                # that are still eligible without rejecting the whole imported
                # automation when a connector is absent on the target server.
                ignore_inaccessible_mcp_servers=True,
            )
            if webhook_trigger_skipped:
                skipped_webhook_triggers += 1
            created.append({
                "id": automation.id,
                "title": automation.title,
                "webhook_trigger_created": False,
                "webhook_trigger_skipped": webhook_trigger_skipped,
            })
        except ValidationError as exc:
            errors.append({
                "index": index,
                "error": "Invalid automation payload",
                "details": exc.errors(),
            })
        except Exception as exc:
            db.rollback()
            errors.append({
                "index": index,
                "error": str(exc),
            })

    return {
        "status": "success" if not errors else "partial_success",
        "created": created,
        "errors": errors,
        "skipped_webhook_triggers": skipped_webhook_triggers,
    }
