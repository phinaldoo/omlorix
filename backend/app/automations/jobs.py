from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
from types import SimpleNamespace

from fastapi import HTTPException

from app.auth.token import ensure_user_runtime_auth_allowed
from app.chats.models import create_chat, create_chat_message
from app.database import SessionLocal
from app.groups.init import get_user_group_setting_value
from app.llm.models import get_model
from app.llm.provider_request import ProviderRequest, REQUEST_TYPE_CHAT, call_provider_chat
from app.llm.schemas import normalize_provider_value
from app.automations.models import (
    Automation,
    attach_automation_execution_chat,
    complete_automation_execution,
    complete_automation_schedule_for_slot,
    fail_automation_execution,
    release_automation_claim_for_slot,
    start_automation_execution,
    update_webhook_delivery,
)
from app.userNotifications.models import create_user_notification
from app.users.models import get_user


logger = logging.getLogger(__name__)


def _automation_error_message(detail) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        if isinstance(message, str) and message.strip():
            return message.strip()
        try:
            return json.dumps(detail, ensure_ascii=False)[:255]
        except Exception:
            return "Automation execution was rejected"
    return str(detail or "Automation execution was rejected")


class AutomationExecutionRejected(Exception):
    """Raised when queued automation execution no longer matches current authorization."""

    def __init__(self, message: str, *, status_code: int, notify_user: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.notify_user = notify_user


@dataclass(frozen=True)
class AutomationRuntimeContext:
    """Authorized context material resolved immediately before an automation run."""

    system_instruction_sections: list[dict[str, str]]
    note_ids: list[str]
    image_ids: list[str]
    video_ids: list[str]
    audio_ids: list[str]
    document_ids: list[str]


def _get_webhook_delivery_id(trigger_context: dict | None) -> str | None:
    if not isinstance(trigger_context, dict):
        return None
    return str(trigger_context.get("delivery_id") or "").strip() or None


def _load_automation_execution_context(
    db,
    automation_id: str,
    queued_user_id: str,
    *,
    event_source: str,
) -> tuple[Automation, object]:
    automation = db.query(Automation).filter(Automation.id == automation_id).first()
    if not automation:
        raise AutomationExecutionRejected("Automation not found", status_code=404)

    if str(automation.user_id) != str(queued_user_id):
        raise AutomationExecutionRejected("Automation owner mismatch", status_code=409)

    if not bool(getattr(automation, "is_active", False)):
        raise AutomationExecutionRejected("Automation is inactive", status_code=409)

    try:
        user = get_user(db, automation.user_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise AutomationExecutionRejected("User not found", status_code=404) from exc
        raise

    try:
        ensure_user_runtime_auth_allowed(user, db, event_source=event_source)
    except HTTPException as exc:
        raise AutomationExecutionRejected(
            _automation_error_message(exc.detail),
            status_code=exc.status_code,
        ) from exc

    try:
        automations_enabled = bool(
            get_user_group_setting_value(user.id, "automations", "enabled_automations", db)
        )
    except Exception:
        if getattr(user, "group_id", None) is None:
            automations_enabled = True
        else:
            raise
    if not automations_enabled:
        raise AutomationExecutionRejected("Automations are disabled for this user", status_code=403)

    return automation, user


def _normalize_reference_ids(raw_ids) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw_ids, list):
        return normalized
    for raw_id in raw_ids:
        reference_id = str(raw_id or "").strip()
        if not reference_id or reference_id in seen:
            continue
        seen.add(reference_id)
        normalized.append(reference_id)
    return normalized


def _resolve_automation_runtime_context(
    db,
    automation: Automation,
    user,
    model,
) -> AutomationRuntimeContext:
    """Resolve saved references through the same access and context rules as chat."""

    from app.chats.utils import (
        _build_system_instruction_sections,
        _collect_skill_file_attachment_ids,
        _compose_skill_content,
        _extract_model_skill_ids,
        _merge_attachment_ids,
        _resolve_generation_skill_ids,
        _resolve_trusted_admin_skill_ids,
    )
    from app.files.access import get_accessible_file
    from app.llm.system_instruction.personality import get_user_personality_system_instruction_section
    from app.notes.models import can_user_view_note
    from app.skills.models import _resolve_accessible_skill_for_user

    requested_skill_id = str(getattr(automation, "skill_id", None) or "").strip() or None
    if requested_skill_id and not _resolve_accessible_skill_for_user(db, user.id, requested_skill_id):
        raise AutomationExecutionRejected(
            "The configured skill is no longer accessible",
            status_code=404,
            notify_user=True,
        )

    model_settings = model.settings if isinstance(getattr(model, "settings", None), dict) else {}
    model_skill_ids = _extract_model_skill_ids(model_settings)
    trusted_admin_skill_ids = _resolve_trusted_admin_skill_ids(model_skill_ids=model_skill_ids)
    try:
        effective_skill_ids = _resolve_generation_skill_ids(
            requested_skill_ids=[requested_skill_id] if requested_skill_id else [],
            model_skill_ids=model_skill_ids,
        )
    except HTTPException as exc:
        raise AutomationExecutionRejected(
            _automation_error_message(exc.detail),
            status_code=exc.status_code,
            notify_user=True,
        ) from exc

    skill_content = _compose_skill_content(
        db,
        user.id,
        effective_skill_ids,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    skill_file_attachments = _collect_skill_file_attachment_ids(
        db,
        user.id,
        effective_skill_ids,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    personality_section = get_user_personality_system_instruction_section(user.id, db)
    system_instruction_sections = _build_system_instruction_sections(
        personality_section=personality_section,
        skill_content=skill_content,
    )

    note_ids = _normalize_reference_ids(getattr(automation, "note_ids", None))
    for note_id in note_ids:
        if not can_user_view_note(db, user.id, note_id):
            raise AutomationExecutionRejected(
                "A configured note is no longer accessible",
                status_code=404,
                notify_user=True,
            )

    file_ids_by_field: dict[str, list[str]] = {
        "images": [],
        "videos": [],
        "audios": [],
        "documents": [],
    }
    category_fields = {
        "image": "images",
        "video": "videos",
        "audio": "audios",
        "document": "documents",
    }
    for file_id in _normalize_reference_ids(getattr(automation, "file_ids", None)):
        file_record = get_accessible_file(db, user.id, file_id)
        if not file_record:
            raise AutomationExecutionRejected(
                "A configured file is no longer accessible",
                status_code=404,
                notify_user=True,
            )
        attachment_field = category_fields.get(str(getattr(file_record, "file_category", "") or "").lower())
        if not attachment_field:
            raise AutomationExecutionRejected(
                "A configured file has an unsupported attachment type",
                status_code=400,
                notify_user=True,
            )
        file_ids_by_field[attachment_field].append(file_id)

    return AutomationRuntimeContext(
        system_instruction_sections=system_instruction_sections,
        note_ids=note_ids,
        image_ids=_merge_attachment_ids(file_ids_by_field["images"], skill_file_attachments.get("images")),
        video_ids=_merge_attachment_ids(file_ids_by_field["videos"], skill_file_attachments.get("videos")),
        audio_ids=_merge_attachment_ids(file_ids_by_field["audios"], skill_file_attachments.get("audios")),
        document_ids=_merge_attachment_ids(file_ids_by_field["documents"], skill_file_attachments.get("documents")),
    )


def execute_automation_job(
    automation_id: str,
    user_id: str,
    scheduled_slot: str | None = None,
    trigger_context: dict | None = None,
    execution_id: str | None = None,
) -> bool:
    """Execute a scheduled automation by creating a chat and generating a response."""

    db = SessionLocal()
    automation = None
    user = None
    automation_title = "Unknown"
    prompt_text = ""
    model_id = ""
    trigger_context = trigger_context if isinstance(trigger_context, dict) else {}
    trigger_type = str(trigger_context.get("type") or "schedule").strip().lower() or "schedule"
    webhook_delivery_id = _get_webhook_delivery_id(trigger_context)
    try:
        automation, user = _load_automation_execution_context(
            db,
            automation_id,
            user_id,
            event_source=f"automation_{trigger_type}",
        )
        if execution_id:
            execution = start_automation_execution(db, execution_id)
            if execution is None:
                logger.info("Skipping duplicate or stale automation execution %s", execution_id)
                return True
            trigger_context = execution.trigger_context if isinstance(execution.trigger_context, dict) else trigger_context
            trigger_type = str(trigger_context.get("type") or trigger_type).strip().lower() or trigger_type
            webhook_delivery_id = _get_webhook_delivery_id(trigger_context)
            scheduled_slot = execution.scheduled_slot
            automation_title = execution.automation_title
            prompt_text = execution.prompt_snapshot
            model_id = execution.model_id_snapshot
        else:
            automation_title = str(getattr(automation, "title", "Unknown") or "Unknown")
            prompt_text = str(getattr(automation, "prompt", "") or "")
            model_id = str(getattr(automation, "model_id", "") or "")

        try:
            from app.llm.utils import ensure_user_access_to_model

            ensure_user_access_to_model(user.id, model_id, db)
            model = get_model(db, model_id)
        except HTTPException as exc:
            logger.warning(
                "User %s cannot access model %s for automation %s",
                user.id,
                model_id,
                automation_id,
            )
            _create_automation_failure_notification(
                db,
                user.id,
                automation_title,
                str(exc.detail or "Model not accessible"),
            )
            if execution_id:
                fail_automation_execution(db, execution_id, str(exc.detail or "Model not accessible"))
            if webhook_delivery_id:
                update_webhook_delivery(
                    db,
                    webhook_delivery_id,
                    status="failed",
                    status_code=exc.status_code,
                    error=str(exc.detail or "Model not accessible"),
                )
            return False

        if not model:
            logger.error("Model %s not found for automation %s", model_id, automation_id)
            _create_automation_failure_notification(db, user.id, automation_title, "Model not found")
            if execution_id:
                fail_automation_execution(db, execution_id, "Model not found")
            if webhook_delivery_id:
                update_webhook_delivery(
                    db,
                    webhook_delivery_id,
                    status="failed",
                    status_code=404,
                    error="Model not found",
                )
            return False

        runtime_context = _resolve_automation_runtime_context(db, automation, user, model)
        webhook_context = trigger_context.get("webhook") if isinstance(trigger_context.get("webhook"), dict) else None
        webhook_trigger_id = str(trigger_context.get("trigger_id") or "").strip() or None
        logger.info(
            "Executing automation %s (%s) [slot=%s trigger=%s]",
            automation_id,
            automation_title,
            scheduled_slot,
            trigger_type,
        )

        chat = create_chat(
            user_id=user.id,
            db=db,
            project_id=None,
            meta={
                "status": "normal",
                "source": "automation",
                "automation_id": automation_id,
                "automation_title": automation_title,
                "scheduled_slot": scheduled_slot,
                "trigger_type": trigger_type,
                "webhook_trigger_id": webhook_trigger_id,
                "webhook_delivery_id": webhook_delivery_id,
            },
        )

        # Keep the stored chat title clean and use chat metadata for the automation badge in the UI.
        chat.title = automation_title
        db.commit()
        if execution_id:
            attach_automation_execution_chat(db, execution_id, chat.id)

        user_content = _build_automation_user_prompt(
            prompt_text,
            trigger_context=trigger_context,
            webhook_context=webhook_context,
        )
        user_block = {"type": "user", "content": user_content}
        for field, values in (
            ("images", runtime_context.image_ids),
            ("videos", runtime_context.video_ids),
            ("audios", runtime_context.audio_ids),
            ("documents", runtime_context.document_ids),
        ):
            if values:
                user_block[field] = values
        user_blocks = [user_block]
        create_chat_message(
            db=db,
            chat_id=chat.id,
            model_id=model_id,
            role="user",
            content=user_blocks,
        )

        execution_config = SimpleNamespace(
            id=automation_id,
            title=automation_title,
            prompt=prompt_text,
            model_id=model_id,
            mcp_server_ids=list(getattr(automation, "mcp_server_ids", None) or []),
        )
        _generate_automation_response(
            db,
            chat.id,
            execution_config,
            user,
            runtime_context=runtime_context,
        )
        if webhook_delivery_id:
            update_webhook_delivery(db, webhook_delivery_id, status="completed", status_code=200, chat_id=chat.id)
        if execution_id:
            complete_automation_execution(db, execution_id, chat.id)
        _create_automation_success_notification(db, user.id, automation_title, chat.id)

        logger.info("Automation %s executed successfully, chat_id=%s", automation_id, chat.id)
        return True
    except AutomationExecutionRejected as exc:
        logger.warning("Skipping automation %s execution: %s", automation_id, exc.message)
        if exc.notify_user and user is not None:
            _create_automation_failure_notification(
                db,
                user.id,
                automation_title,
                exc.message,
            )
        if execution_id:
            fail_automation_execution(db, execution_id, exc.message)
        if webhook_delivery_id:
            try:
                update_webhook_delivery(
                    db,
                    webhook_delivery_id,
                    status="failed",
                    status_code=exc.status_code,
                    error=exc.message,
                )
            except Exception:
                logger.exception("Failed to update webhook delivery for rejected automation %s", automation_id)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error executing automation %s", automation_id)
        try:
            if webhook_delivery_id:
                update_webhook_delivery(db, webhook_delivery_id, status="failed", status_code=500, error=str(exc))
            if execution_id:
                fail_automation_execution(db, execution_id, str(exc))
            if automation and automation_title == "Unknown":
                automation_title = str(getattr(automation, "title", "Unknown") or "Unknown")
            _create_automation_failure_notification(db, user_id, automation_title, str(exc))
        except Exception:
            logger.exception("Failed to create automation failure notification")
        return False
    finally:
        db.close()


def execute_scheduled_automation_job(
    automation_id: str,
    user_id: str,
    scheduled_for: datetime | str | None,
    scheduled_slot: str | None = None,
    trigger_context: dict | None = None,
) -> bool:
    """Execute a scheduled automation and persist the slot outcome."""

    executed = execute_automation_job(
        automation_id,
        user_id,
        scheduled_slot=scheduled_slot,
        trigger_context=trigger_context,
    )

    db = SessionLocal()
    try:
        if executed:
            completed = complete_automation_schedule_for_slot(
                db,
                automation_id,
                scheduled_for=scheduled_for,
                scheduled_slot=scheduled_slot,
                mark_triggered=True,
            )
            if not completed:
                logger.warning(
                    "Scheduled automation %s completed but its due slot no longer matched %s",
                    automation_id,
                    scheduled_slot,
                )
            return True

        released = release_automation_claim_for_slot(
            db,
            automation_id,
            scheduled_for=scheduled_for,
            scheduled_slot=scheduled_slot,
        )
        if not released:
            logger.warning(
                "Failed to release scheduler claim for automation %s slot %s after execution failure",
                automation_id,
                scheduled_slot,
            )
        return False
    finally:
        db.close()


def _build_automation_user_prompt(
    prompt: str,
    *,
    trigger_context: dict | None = None,
    webhook_context: dict | None = None,
) -> str:
    base_prompt = str(prompt or "").strip()
    trigger_context = trigger_context if isinstance(trigger_context, dict) else {}
    if not webhook_context:
        return base_prompt

    payload_mode = str(trigger_context.get("payload_mode") or "append").strip().lower()
    if payload_mode == "ignore":
        return base_prompt

    if payload_mode == "template":
        rendered = _render_webhook_template(base_prompt, webhook_context)
        if rendered != base_prompt:
            return rendered

    webhook_json = json.dumps(webhook_context, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        f"{base_prompt}\n\n"
        "Webhook context:\n"
        "```json\n"
        f"{webhook_json}\n"
        "```"
    )


def _render_webhook_template(template: str, webhook_context: dict) -> str:
    """Render simple {{webhook.path.to.value}} placeholders without executing code."""

    import re

    def resolve(path: str) -> str:
        parts = [part for part in path.split(".") if part]
        value = webhook_context
        if parts and parts[0] == "webhook":
            parts = parts[1:]
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
                continue
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        if value is None:
            return ""
        return str(value)

    return re.sub(r"\{\{\s*(webhook(?:\.[A-Za-z0-9_-]+)*)\s*\}\}", lambda match: resolve(match.group(1)), template)


def _generate_automation_response(
    db,
    chat_id: str,
    automation: Automation,
    user,
    *,
    runtime_context: AutomationRuntimeContext,
):
    """Generate a response with the automation's explicit MCP allowlist."""

    from app.chats.models import get_chat_messages as db_get_chat_messages

    model = get_model(db, automation.model_id)
    if not model:
        logger.error("Model %s not found", automation.model_id)
        return

    chat_history = db_get_chat_messages(db, chat_id)
    provider = normalize_provider_value(model.provider)

    try:
        # Background jobs have no model-settings sidebar. Use the ordinary
        # request override channel so selected connectors are enabled for this
        # automation run only and all unselected MCP servers remain disabled.
        settings_override = {
            "enabled_mcp_servers": list(getattr(automation, "mcp_server_ids", None) or []),
        }
        stream_generator = call_provider_chat(
            ProviderRequest(
                request_type=REQUEST_TYPE_CHAT,
                db=db,
                provider=provider,
                model=model,
                chat_history=chat_history,
                user_id=user.id,
                generation_id=None,
                temp_request_flag=False,
                settings_override=settings_override,
                system_instruction_sections=runtime_context.system_instruction_sections,
                note_ids=runtime_context.note_ids,
                user_role=getattr(user, "role", None),
                extra={"chat_id": chat_id},
            )
        )
    except HTTPException:
        logger.warning("Unsupported provider %s for automation execution", provider)
        return
    except ValueError:
        logger.warning("Unsupported provider %s for automation execution", provider)
        return

    chunk_count = 0
    for line in stream_generator or []:
        if not line or not line.strip():
            continue
        try:
            data = json.loads(line.strip())
            if data.get("t") == "c" and data.get("d"):
                chunk_count += 1
        except Exception:
            continue

    logger.info("Automation %s response generated with %s chunks", automation.id, chunk_count)


def _create_automation_success_notification(db, user_id: str, automation_title: str, chat_id: str):
    try:
        create_user_notification(
            db=db,
            message=f"Automation '{automation_title}' completed successfully",
            category="automations",
            notification_type="info",
            everyone=False,
            user_ids=[user_id],
            details={
                "automation_title": automation_title,
                "chat_id": chat_id,
                "action": "view_chat",
            },
        )
    except Exception:
        logger.exception("Failed to create automation success notification")


def _create_automation_failure_notification(db, user_id: str, automation_title: str, error: str):
    try:
        create_user_notification(
            db=db,
            message=f"Automation '{automation_title}' failed: {error[:100]}",
            category="automations",
            notification_type="error",
            everyone=False,
            user_ids=[user_id],
            details={
                "automation_title": automation_title,
                "error": error,
            },
        )
    except Exception:
        logger.exception("Failed to create automation failure notification")
