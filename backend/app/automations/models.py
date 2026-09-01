from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
import hashlib
import hmac
import logging
import secrets
import uuid

from fastapi import HTTPException
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.utils.icon_security import require_safe_icon_input

from app.automations.schedule import compute_next_schedule_state, normalize_automation_datetime
from app.database import Base
from app.files.models import Files


logger = logging.getLogger(__name__)


def _set_automation_schedule_state(
    automation: "Automation",
    *,
    reference_time: datetime | None = None,
    include_reference: bool = True,
) -> None:
    if not automation.is_active:
        automation.next_run_at = None
        automation.next_run_slot = None
        automation.scheduler_claimed_at = None
        return

    schedule_state = compute_next_schedule_state(
        automation.schedule_rules,
        reference_time=reference_time,
        include_reference=include_reference,
        schedule_timezone=automation.schedule_timezone,
    )
    automation.next_run_at = schedule_state.run_at if schedule_state else None
    automation.next_run_slot = schedule_state.slot if schedule_state else None
    automation.scheduler_claimed_at = None


def _validate_automation_model_access(db: Session, user_id: str, model_id: str) -> str:
    """Validate and normalize the model selected for an automation."""
    normalized_model_id = str(model_id or "").strip()
    if not normalized_model_id:
        raise HTTPException(status_code=400, detail="Model ID is required")

    from app.llm.utils import ensure_user_access_to_model

    ensure_user_access_to_model(user_id, normalized_model_id, db)
    from app.llm.models import get_model

    model = get_model(db, normalized_model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return normalized_model_id


def _normalize_automation_skill_id(
    db: Session,
    user_id: str,
    model_id: str,
    skill_id: str | None,
) -> str | None:
    """Validate an explicitly selected automation skill with normal chat rules."""

    normalized_skill_id = str(skill_id or "").strip() or None
    if not normalized_skill_id:
        return None

    from app.chats.utils import _extract_model_skill_ids, _resolve_generation_skill_ids
    from app.llm.models import get_model
    from app.skills.models import _resolve_accessible_skill_for_user

    if not _resolve_accessible_skill_for_user(db, user_id, normalized_skill_id):
        raise HTTPException(status_code=404, detail="Skill not found or not accessible")

    model = get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    _resolve_generation_skill_ids(
        requested_skill_ids=[normalized_skill_id],
        model_skill_ids=_extract_model_skill_ids(model_settings),
    )
    return normalized_skill_id


# ---------------------------------------------------------------------------
# Automations Model
# ---------------------------------------------------------------------------
class Automation(Base):
    __tablename__ = "automations"
    __table_args__ = (
        Index("ix_automations_user_id", "user_id"),
        Index("ix_automations_is_active", "is_active"),
        Index("ix_automations_created_at", "created_at"),
        Index("ix_automations_last_updated_at", "last_updated_at"),
        Index("ix_automations_user_created", "user_id", "created_at"),
        Index("ix_automations_active_next_run", "is_active", "next_run_at"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    title = Column(String(255), nullable=False)
    icon = Column(String(10), nullable=True, default="folder")
    icon_color = Column(String(20), nullable=True, default="#FF6B6B")
    prompt = Column(Text, nullable=False)
    model_id = Column(String, nullable=False)
    schedule_rules = Column(JSON, nullable=True, default=list)
    schedule_timezone = Column(String(64), nullable=True)
    skill_id = Column(String, nullable=True)
    note_ids = Column(JSON, nullable=True, default=list)
    file_ids = Column(JSON, nullable=True, default=list)
    # MCP selection is automation-scoped and opt-in. Keeping the stable server
    # IDs here lets each background execution construct the same explicit
    # request allowlist as an interactive chat request.
    mcp_server_ids = Column(JSON, nullable=True, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_slot = Column(String(32), nullable=True)
    scheduler_claimed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class AutomationWebhookTrigger(Base):
    __tablename__ = "automation_webhook_triggers"
    __table_args__ = (
        Index("ix_automation_webhook_triggers_automation_id", "automation_id"),
        Index("ix_automation_webhook_triggers_user_id", "user_id"),
        Index("ix_automation_webhook_triggers_token_hash", "token_hash"),
        Index("ix_automation_webhook_triggers_is_enabled", "is_enabled"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    automation_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    name = Column(String(255), nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    token_hash = Column(String(64), nullable=False)
    token_prefix = Column(String(16), nullable=False)
    payload_mode = Column(String(24), nullable=False, default="append")
    include_headers = Column(Boolean, nullable=False, default=False)
    allowed_header_names = Column(JSON, nullable=True, default=list)
    max_body_bytes = Column(Integer, nullable=False, default=262144)
    rate_limit_per_minute = Column(Integer, nullable=False, default=30)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class AutomationWebhookDelivery(Base):
    __tablename__ = "automation_webhook_deliveries"
    __table_args__ = (
        Index("ix_automation_webhook_deliveries_trigger_id", "trigger_id"),
        Index("ix_automation_webhook_deliveries_automation_id", "automation_id"),
        Index("ix_automation_webhook_deliveries_user_id", "user_id"),
        Index("ix_automation_webhook_deliveries_created_at", "created_at"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    trigger_id = Column(String, nullable=False)
    automation_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    status = Column(String(32), nullable=False, default="accepted")
    status_code = Column(Integer, nullable=True)
    error = Column(String(255), nullable=True)
    request_ip = Column(String(128), nullable=True)
    user_agent = Column(String(255), nullable=True)
    payload_preview = Column(JSON, nullable=True)
    chat_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class AutomationExecution(Base):
    __tablename__ = "automation_executions"
    __table_args__ = (
        UniqueConstraint("automation_id", "scheduled_slot", name="uq_automation_executions_slot"),
        Index("ix_automation_executions_automation_id", "automation_id"),
        Index("ix_automation_executions_user_id", "user_id"),
        Index("ix_automation_executions_status", "status"),
        Index("ix_automation_executions_created_at", "created_at"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    automation_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    scheduled_slot = Column(String(128), nullable=False)
    trigger_type = Column(String(32), nullable=False, default="schedule")
    trigger_context = Column(JSON, nullable=True)
    automation_title = Column(String(255), nullable=False)
    prompt_snapshot = Column(Text, nullable=False)
    model_id_snapshot = Column(String, nullable=False)
    status = Column(String(32), nullable=False, default="queued")
    chat_id = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    queued_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# CRUD Operations
# ---------------------------------------------------------------------------
def create_automation(
    db: Session,
    user_id: str,
    title: str,
    prompt: str,
    model_id: str,
    icon: str = "folder",
    icon_color: str = "#FF6B6B",
    schedule_rules: List[dict] = None,
    schedule_timezone: str | None = None,
    skill_id: str = None,
    note_ids: List[str] = None,
    file_ids: List[str] = None,
    mcp_server_ids: List[str] = None,
    is_active: bool = True,
    *,
    commit: bool = True,
    ignore_inaccessible_mcp_servers: bool = False,
) -> Automation:
    """Create a new automation, optionally leaving commit control to the caller."""
    if not title or not title.strip():
        raise HTTPException(status_code=400, detail="Automation title is required")
    if not prompt or not prompt.strip():
        raise HTTPException(status_code=400, detail="Automation prompt is required")
    normalized_model_id = _validate_automation_model_access(db, user_id, model_id)

    normalized_skill_id = _normalize_automation_skill_id(db, user_id, normalized_model_id, skill_id)
    normalized_file_ids = _normalize_automation_file_ids(db, user_id, file_ids)
    normalized_note_ids = _normalize_automation_note_ids(db, user_id, note_ids)
    normalized_mcp_server_ids = _normalize_automation_mcp_server_ids(
        db,
        user_id,
        normalized_model_id,
        mcp_server_ids,
        reject_inaccessible=not ignore_inaccessible_mcp_servers,
    )
    now = datetime.now(timezone.utc)
    automation = Automation(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=title.strip()[:255],
        icon=require_safe_icon_input(icon or "folder", fallback="folder"),
        icon_color=icon_color or "#FF6B6B",
        prompt=prompt.strip(),
        model_id=normalized_model_id,
        schedule_rules=schedule_rules or [],
        schedule_timezone=schedule_timezone,
        skill_id=normalized_skill_id,
        note_ids=normalized_note_ids,
        file_ids=normalized_file_ids,
        mcp_server_ids=normalized_mcp_server_ids,
        is_active=is_active,
        last_triggered_at=None,
        created_at=now,
        last_updated_at=now,
    )
    _set_automation_schedule_state(automation, reference_time=now)
    db.add(automation)
    if commit:
        db.commit()
        db.refresh(automation)
    else:
        # Flush assigns and validates database state while keeping the automation
        # in the caller's transaction so related records can be created atomically.
        db.flush()
    return automation


def get_automation(db: Session, automation_id: str, user_id: str = None) -> Optional[Automation]:
    """Get an automation by ID, optionally filtering by user_id."""
    query = db.query(Automation).filter(Automation.id == automation_id)
    if user_id:
        query = query.filter(Automation.user_id == user_id)
    return query.first()


def list_automations(
    db: Session,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> List[Automation]:
    """List all automations for a user."""
    query = (
        db.query(Automation)
        .filter(Automation.user_id == user_id)
        .order_by(Automation.created_at.desc())
    )
    if isinstance(offset, int) and offset > 0:
        query = query.offset(offset)
    if isinstance(limit, int) and limit > 0:
        query = query.limit(limit)
    return query.all()


def update_automation(
    db: Session,
    user_id: str,
    automation_id: str,
    title: str = None,
    prompt: str = None,
    model_id: str = None,
    icon: str = None,
    icon_color: str = None,
    schedule_rules: List[dict] = None,
    schedule_timezone: str | None = None,
    skill_id: str = None,
    note_ids: List[str] = None,
    file_ids: List[str] = None,
    mcp_server_ids: List[str] = None,
    is_active: bool = None,
    *,
    commit: bool = True,
) -> Optional[Automation]:
    """Update an existing automation."""
    automation = get_automation(db, automation_id, user_id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")

    if title is not None:
        automation.title = title.strip()[:255]
    if prompt is not None:
        automation.prompt = prompt.strip()
    if model_id is not None:
        automation.model_id = _validate_automation_model_access(db, user_id, model_id)
    if icon is not None:
        automation.icon = require_safe_icon_input(icon, fallback="folder")
    if icon_color is not None:
        automation.icon_color = icon_color
    if schedule_rules is not None:
        automation.schedule_rules = schedule_rules
    if schedule_rules is not None or schedule_timezone is not None:
        automation.schedule_timezone = schedule_timezone
    if skill_id is not None or model_id is not None:
        effective_skill_id = skill_id if skill_id is not None else automation.skill_id
        automation.skill_id = _normalize_automation_skill_id(
            db,
            user_id,
            automation.model_id,
            effective_skill_id,
        )
    if note_ids is not None:
        automation.note_ids = _normalize_automation_note_ids(db, user_id, note_ids)
    if file_ids is not None:
        automation.file_ids = _normalize_automation_file_ids(db, user_id, file_ids)
    if mcp_server_ids is not None:
        automation.mcp_server_ids = _normalize_automation_mcp_server_ids(
            db,
            user_id,
            automation.model_id,
            mcp_server_ids,
            reject_inaccessible=True,
        )
    elif model_id is not None:
        # Model changes can narrow connector policy even when the caller is not
        # changing the connection selection. Revalidate the persisted IDs so the
        # updated automation always satisfies the newly selected model's policy.
        automation.mcp_server_ids = _normalize_automation_mcp_server_ids(
            db,
            user_id,
            automation.model_id,
            automation.mcp_server_ids,
            reject_inaccessible=False,
        )
    if is_active is not None:
        automation.is_active = is_active

    if schedule_rules is not None or is_active is not None or schedule_timezone is not None:
        _set_automation_schedule_state(automation, reference_time=datetime.now(timezone.utc))

    automation.last_updated_at = datetime.now(timezone.utc)
    if commit:
        db.commit()
        db.refresh(automation)
    else:
        db.flush()
    return automation


def migrate_automations_model(db: Session, source_model_id: str, target_model_id: str) -> int:
    """
    Update all automations that reference ``source_model_id`` to use ``target_model_id``.

    Returns the number of automations updated.
    """
    if (
        not source_model_id
        or not target_model_id
        or source_model_id == target_model_id
    ):
        return 0

    updated = (
        db.query(Automation)
        .filter(Automation.model_id == source_model_id)
        .update(
            {
                Automation.model_id: target_model_id,
                Automation.last_updated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )

    if updated:
        db.commit()

    return updated


def delete_automation(
    db: Session,
    user_id: str,
    automation_id: str,
    *,
    commit: bool = True,
) -> bool:
    """Delete an automation."""
    automation = get_automation(db, automation_id, user_id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")

    (
        db.query(AutomationExecution)
        .filter(AutomationExecution.automation_id == automation_id)
        .delete(synchronize_session=False)
    )
    (
        db.query(AutomationWebhookDelivery)
        .filter(AutomationWebhookDelivery.automation_id == automation_id)
        .delete(synchronize_session=False)
    )
    (
        db.query(AutomationWebhookTrigger)
        .filter(AutomationWebhookTrigger.automation_id == automation_id)
        .delete(synchronize_session=False)
    )
    db.delete(automation)
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def get_automation_execution(
    db: Session,
    automation_id: str,
    scheduled_slot: str,
) -> AutomationExecution | None:
    """Return the execution row for a scheduled automation slot."""

    return (
        db.query(AutomationExecution)
        .filter(
            AutomationExecution.automation_id == automation_id,
            AutomationExecution.scheduled_slot == scheduled_slot,
        )
        .first()
    )


def reserve_automation_execution(
    db: Session,
    *,
    automation_id: str,
    user_id: str,
    scheduled_slot: str,
    trigger_context: dict | None = None,
) -> tuple[AutomationExecution | None, str]:
    """Create or reuse the persisted execution record for a scheduled slot."""

    normalized_slot = str(scheduled_slot or "").strip()
    if not normalized_slot:
        return None, "failed"

    now = datetime.now(timezone.utc)
    trigger_context = trigger_context if isinstance(trigger_context, dict) else {}
    trigger_type = str(trigger_context.get("type") or "schedule").strip().lower() or "schedule"

    existing = get_automation_execution(db, automation_id, normalized_slot)
    if existing:
        if existing.status == "failed" and not existing.chat_id:
            existing.status = "queued"
            existing.error = None
            existing.failed_at = None
            existing.started_at = None
            existing.last_updated_at = now
            db.commit()
            db.refresh(existing)
            return existing, "queued"
        return existing, "duplicate"

    automation = get_automation(db, automation_id, user_id)
    if not automation:
        return None, "failed"

    execution = AutomationExecution(
        automation_id=automation.id,
        user_id=user_id,
        scheduled_slot=normalized_slot,
        trigger_type=trigger_type,
        trigger_context=trigger_context or None,
        automation_title=automation.title,
        prompt_snapshot=automation.prompt,
        model_id_snapshot=automation.model_id,
        status="queued",
        queued_at=now,
        created_at=now,
        last_updated_at=now,
    )
    db.add(execution)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return get_automation_execution(db, automation_id, normalized_slot), "duplicate"

    db.refresh(execution)
    return execution, "queued"


def mark_automation_execution_enqueue_failed(db: Session, execution_id: str, error: str) -> None:
    """Mark a queued execution as failed so the scheduler can retry enqueueing it."""

    execution = db.query(AutomationExecution).filter(AutomationExecution.id == execution_id).first()
    if not execution or execution.status != "queued":
        return

    now = datetime.now(timezone.utc)
    execution.status = "failed"
    execution.error = str(error or "")[:4000] or None
    execution.failed_at = now
    execution.last_updated_at = now
    db.commit()


def start_automation_execution(db: Session, execution_id: str) -> AutomationExecution | None:
    """Claim the execution row for processing, ignoring stale replays."""

    execution = db.query(AutomationExecution).filter(AutomationExecution.id == execution_id).first()
    if not execution:
        return None

    now = datetime.now(timezone.utc)
    claimed = db.execute(
        update(AutomationExecution)
        .where(
            AutomationExecution.id == execution_id,
            or_(
                AutomationExecution.status == "queued",
                and_(
                    AutomationExecution.status == "failed",
                    AutomationExecution.chat_id.is_(None),
                ),
            ),
        )
        .values(
            status="running",
            started_at=now,
            error=None,
            last_updated_at=now,
        )
    )
    db.commit()
    if claimed.rowcount != 1:
        return None

    execution = db.query(AutomationExecution).filter(AutomationExecution.id == execution_id).first()
    if not execution or execution.status != "running":
        return None
    db.refresh(execution)
    return execution


def attach_automation_execution_chat(db: Session, execution_id: str, chat_id: str) -> None:
    """Persist the chat ID as soon as the execution creates it."""

    execution = db.query(AutomationExecution).filter(AutomationExecution.id == execution_id).first()
    if not execution:
        return

    execution.chat_id = chat_id
    execution.last_updated_at = datetime.now(timezone.utc)
    db.commit()


def complete_automation_execution(db: Session, execution_id: str, chat_id: str) -> None:
    """Mark an execution completed."""

    execution = db.query(AutomationExecution).filter(AutomationExecution.id == execution_id).first()
    if not execution:
        return

    now = datetime.now(timezone.utc)
    execution.status = "completed"
    execution.chat_id = chat_id
    execution.completed_at = now
    execution.failed_at = None
    execution.error = None
    execution.last_updated_at = now
    db.commit()


def fail_automation_execution(db: Session, execution_id: str, error: str) -> None:
    """Mark an execution failed without permitting completed slots to regress."""

    execution = db.query(AutomationExecution).filter(AutomationExecution.id == execution_id).first()
    if not execution or execution.status == "completed":
        return

    now = datetime.now(timezone.utc)
    execution.status = "failed"
    execution.error = str(error or "")[:4000] or None
    execution.failed_at = now
    execution.last_updated_at = now
    db.commit()


def claim_due_automations(
    db: Session,
    *,
    due_before: datetime,
    batch_size: int,
    claim_timeout_seconds: int,
) -> List[Automation]:
    """Atomically claim a batch of due automations for the scheduler."""

    normalized_due_before = normalize_automation_datetime(due_before) or datetime.now(timezone.utc)
    stale_before = normalized_due_before - timedelta(seconds=max(1, claim_timeout_seconds))

    candidate_query = (
        select(Automation.id)
        .where(
            Automation.is_active.is_(True),
            Automation.next_run_at.is_not(None),
            Automation.next_run_at <= normalized_due_before,
            or_(
                Automation.scheduler_claimed_at.is_(None),
                Automation.scheduler_claimed_at < stale_before,
            ),
        )
        .order_by(Automation.next_run_at.asc(), Automation.id.asc())
        .limit(max(1, batch_size))
    )

    if (db.bind.dialect.name if db.bind is not None else "") == "postgresql":
        candidate_query = candidate_query.with_for_update(skip_locked=True)

    candidate_ids = list(db.execute(candidate_query).scalars())
    if not candidate_ids:
        return []

    db.execute(
        update(Automation)
        .where(
            Automation.id.in_(candidate_ids),
            or_(
                Automation.scheduler_claimed_at.is_(None),
                Automation.scheduler_claimed_at < stale_before,
            ),
        )
        .values(scheduler_claimed_at=normalized_due_before)
    )
    db.commit()

    return (
        db.query(Automation)
        .filter(
            Automation.id.in_(candidate_ids),
            Automation.scheduler_claimed_at == normalized_due_before,
        )
        .order_by(Automation.next_run_at.asc(), Automation.id.asc())
        .all()
    )


def release_automation_claim(db: Session, automation_id: str, claimed_at: datetime | None) -> None:
    """Release a scheduler claim so the automation can be retried."""

    if claimed_at is None:
        return

    normalized_claimed_at = normalize_automation_datetime(claimed_at)
    db.execute(
        update(Automation)
        .where(
            Automation.id == automation_id,
            Automation.scheduler_claimed_at == normalized_claimed_at,
        )
        .values(scheduler_claimed_at=None)
    )
    db.commit()


def release_automation_claim_for_slot(
    db: Session,
    automation_id: str,
    *,
    scheduled_for: datetime | None,
    scheduled_slot: str | None,
) -> bool:
    """Release a scheduler claim when the current due slot still matches."""

    normalized_scheduled_for = normalize_automation_datetime(scheduled_for)
    normalized_scheduled_slot = str(scheduled_slot or "").strip() or None
    if normalized_scheduled_for is None or normalized_scheduled_slot is None:
        return False

    result = db.execute(
        update(Automation)
        .where(
            Automation.id == automation_id,
            Automation.next_run_at == normalized_scheduled_for,
            Automation.next_run_slot == normalized_scheduled_slot,
        )
        .values(scheduler_claimed_at=None)
    )
    db.commit()
    return bool(result.rowcount)


def complete_automation_schedule_for_slot(
    db: Session,
    automation_id: str,
    *,
    scheduled_for: datetime | None,
    scheduled_slot: str | None,
    mark_triggered: bool,
) -> bool:
    """Advance a due automation only if it is still on the expected slot."""

    normalized_scheduled_for = normalize_automation_datetime(scheduled_for)
    normalized_scheduled_slot = str(scheduled_slot or "").strip() or None
    if normalized_scheduled_for is None or normalized_scheduled_slot is None:
        return False

    automation = (
        db.query(Automation)
        .filter(
            Automation.id == automation_id,
            Automation.next_run_at == normalized_scheduled_for,
            Automation.next_run_slot == normalized_scheduled_slot,
        )
        .first()
    )
    if not automation:
        return False

    if mark_triggered:
        automation.last_triggered_at = datetime.now(timezone.utc)

    schedule_state = compute_next_schedule_state(
        automation.schedule_rules,
        reference_time=normalized_scheduled_for,
        include_reference=False,
        schedule_timezone=automation.schedule_timezone,
    )
    automation.next_run_at = schedule_state.run_at if schedule_state else None
    automation.next_run_slot = schedule_state.slot if schedule_state else None
    automation.scheduler_claimed_at = None
    if schedule_state is None:
        automation.is_active = False
        automation.last_updated_at = datetime.now(timezone.utc)

    db.commit()
    return True


def update_automation_last_triggered(db: Session, automation_id: str) -> None:
    """Update the last_triggered_at timestamp for an automation."""
    automation = db.query(Automation).filter(Automation.id == automation_id).first()
    if automation:
        automation.last_triggered_at = datetime.now(timezone.utc)
        db.commit()


def remove_skill_from_automations(db: Session, user_id: str | None, skill_id: str | None) -> int:
    """
    Remove references to a skill from all automations owned by the user.

    Returns the number of automations updated.
    """
    if not skill_id:
        return 0

    query = db.query(Automation).filter(Automation.skill_id == skill_id)
    if user_id:
        query = query.filter(Automation.user_id == user_id)
    automations = query.all()

    updated = 0
    for automation in automations:
        automation.skill_id = None
        automation.last_updated_at = datetime.now(timezone.utc)
        updated += 1

    if updated:
        db.commit()
    return updated


def remove_file_from_automations(
    db: Session,
    user_id: str,
    file_id: str | None,
    *,
    commit: bool = True,
) -> int:
    """
    Remove a file ID from all automations owned by the user.

    Returns the number of automations updated.
    """
    if not file_id:
        return 0

    automations = (
        db.query(Automation)
        .filter(Automation.user_id == user_id)
        .filter(Automation.file_ids.isnot(None))
        .all()
    )

    updated = 0
    for automation in automations:
        automation_file_ids = automation.file_ids if isinstance(automation.file_ids, list) else []
        if file_id in automation_file_ids:
            automation.file_ids = [fid for fid in automation_file_ids if fid != file_id]
            automation.last_updated_at = datetime.now(timezone.utc)
            updated += 1

    if updated and commit:
        db.commit()

    return updated


def remove_mcp_server_from_automations(
    db: Session,
    server_id: str | None,
    *,
    commit: bool = True,
) -> int:
    """Remove a deleted MCP server from every automation selection.

    Admin servers may be selected by automations belonging to many users, so
    cleanup deliberately spans all owners. The caller can defer the commit to
    make this reference cleanup atomic with deletion of the MCP server itself.
    """
    normalized_server_id = str(server_id or "").strip()
    if not normalized_server_id:
        return 0

    automations = (
        db.query(Automation)
        .filter(Automation.mcp_server_ids.isnot(None))
        .yield_per(200)
    )
    updated = 0
    for automation in automations:
        selected_ids = automation.mcp_server_ids if isinstance(automation.mcp_server_ids, list) else []
        if normalized_server_id not in selected_ids:
            continue
        automation.mcp_server_ids = [
            selected_id
            for selected_id in selected_ids
            if selected_id != normalized_server_id
        ]
        automation.last_updated_at = datetime.now(timezone.utc)
        updated += 1

    if updated and commit:
        db.commit()
    return updated


def _normalize_automation_mcp_server_ids(
    db: Session,
    user_id: str,
    model_id: str,
    server_ids: Optional[List[str]],
    *,
    reject_inaccessible: bool,
) -> List[str]:
    """Validate an automation's connector allowlist against model policy.

    This calls the same authorization and discovery pipeline as the chat
    mention menu. It therefore covers managed connections, personal servers,
    administrator servers, group policy, and models without a settings sidebar.
    """
    if not server_ids:
        return []

    normalized: List[str] = []
    seen: set[str] = set()
    for server_id in server_ids:
        value = str(server_id or "").strip()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)

    from app.llm.models import get_model
    from app.mcp.utils import list_mcp_mention_connectors
    from app.tools.utils import resolve_enabled_tools

    model = get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    tool_resolution = resolve_enabled_tools(
        model.tools or [],
        db=db,
        model_settings=model_settings,
        user_id=user_id,
    )
    eligible_ids: set[str] = set()
    if tool_resolution.get("mcp_requested"):
        eligible_ids = {
            str(connector.get("id") or "").strip()
            for connector in list_mcp_mention_connectors(
                db,
                user_id,
                model_settings=model_settings,
            )
            if str(connector.get("id") or "").strip()
        }

    inaccessible = [server_id for server_id in normalized if server_id not in eligible_ids]
    if inaccessible and reject_inaccessible:
        raise HTTPException(
            status_code=400,
            detail="One or more selected connections are unavailable for this automation model.",
        )
    return [server_id for server_id in normalized if server_id in eligible_ids]


def _normalize_automation_file_ids(db: Session, user_id: str, file_ids: Optional[List[str]]) -> List[str]:
    """Validate provided file IDs belong to the user and return a de-duplicated list."""
    if not file_ids:
        return []

    normalized: List[str] = []
    seen: set[str] = set()
    for file_id in file_ids:
        if not file_id or not isinstance(file_id, str):
            continue
        trimmed = file_id.strip()
        if not trimmed or trimmed in seen:
            continue

        exists = (
            db.query(Files.id)
            .filter(Files.id == trimmed, Files.user_id == user_id)
            .first()
        )
        if not exists:
            raise HTTPException(status_code=404, detail=f"File '{trimmed}' not found or not accessible")

        seen.add(trimmed)
        normalized.append(trimmed)

    return normalized


def _normalize_automation_note_ids(db: Session, user_id: str, note_ids: Optional[List[str]]) -> List[str]:
    """Validate provided note IDs are visible to the user and return a de-duplicated list."""
    if not note_ids:
        return []

    from app.notes.models import can_user_view_note

    normalized: List[str] = []
    seen: set[str] = set()
    for note_id in note_ids:
        if not note_id or not isinstance(note_id, str):
            continue
        trimmed = note_id.strip()
        if not trimmed or trimmed in seen:
            continue

        if not can_user_view_note(db, user_id, trimmed):
            raise HTTPException(status_code=404, detail=f"Note '{trimmed}' not found or not accessible")

        seen.add(trimmed)
        normalized.append(trimmed)

    return normalized


def remove_note_from_automations(db: Session, user_id: str, note_id: str | None) -> int:
    """
    Remove a note ID from all automations owned by the user.

    Returns the number of automations updated.
    """
    if not note_id:
        return 0

    automations = (
        db.query(Automation)
        .filter(Automation.user_id == user_id)
        .filter(Automation.note_ids.isnot(None))
        .all()
    )

    updated = 0
    for automation in automations:
        note_ids = automation.note_ids if isinstance(automation.note_ids, list) else []
        if note_id in note_ids:
            automation.note_ids = [nid for nid in note_ids if nid != note_id]
            automation.last_updated_at = datetime.now(timezone.utc)
            updated += 1

    if updated:
        db.commit()
    return updated


# ---------------------------------------------------------------------------
# Webhook trigger helpers
# ---------------------------------------------------------------------------
WEBHOOK_PAYLOAD_MODES = {"append", "template", "ignore"}
DEFAULT_WEBHOOK_MAX_BODY_BYTES = 256 * 1024
DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE = 30
DEFAULT_WEBHOOK_ALLOWED_HEADERS = [
    "user-agent",
    "x-github-event",
    "x-github-delivery",
    "x-gitlab-event",
    "x-request-id",
]


def generate_webhook_secret() -> str:
    """Generate a one-time visible webhook secret."""

    return f"cuiwh_{secrets.token_urlsafe(32)}"


def hash_webhook_secret(secret: str) -> str:
    return hashlib.sha256(str(secret or "").encode("utf-8")).hexdigest()


def verify_webhook_secret(trigger: AutomationWebhookTrigger, secret: str | None) -> bool:
    if not secret:
        return False
    provided_hash = hash_webhook_secret(secret)
    return hmac.compare_digest(str(trigger.token_hash or ""), provided_hash)


def _normalize_payload_mode(value: str | None) -> str:
    mode = str(value or "append").strip().lower()
    return mode if mode in WEBHOOK_PAYLOAD_MODES else "append"


def _normalize_allowed_header_names(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip().lower()
        if not name or len(name) > 128 or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized[:50]


def _normalize_positive_int(value: int | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def create_webhook_trigger(
    db: Session,
    user_id: str,
    automation_id: str,
    *,
    name: str | None = None,
    is_enabled: bool = True,
    payload_mode: str | None = "append",
    include_headers: bool = False,
    allowed_header_names: list[str] | None = None,
    max_body_bytes: int | None = None,
    rate_limit_per_minute: int | None = None,
    commit: bool = True,
    trigger_id: str | None = None,
    secret: str | None = None,
) -> tuple[AutomationWebhookTrigger, str]:
    """Create a webhook trigger, optionally as part of a caller-owned transaction."""
    automation = get_automation(db, automation_id, user_id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")

    # Reserved credentials are generated and signed by the server before the
    # automation exists. Ordinary callers continue to receive fresh values.
    resolved_secret = str(secret or "").strip() or generate_webhook_secret()
    resolved_trigger_id = str(trigger_id or "").strip() or str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    trigger = AutomationWebhookTrigger(
        id=resolved_trigger_id,
        automation_id=automation_id,
        user_id=user_id,
        name=(name.strip()[:255] if isinstance(name, str) and name.strip() else None),
        is_enabled=bool(is_enabled),
        token_hash=hash_webhook_secret(resolved_secret),
        token_prefix=resolved_secret[:16],
        payload_mode=_normalize_payload_mode(payload_mode),
        include_headers=bool(include_headers),
        allowed_header_names=_normalize_allowed_header_names(allowed_header_names)
        or (DEFAULT_WEBHOOK_ALLOWED_HEADERS if include_headers else []),
        max_body_bytes=_normalize_positive_int(
            max_body_bytes,
            default=DEFAULT_WEBHOOK_MAX_BODY_BYTES,
            minimum=1024,
            maximum=1024 * 1024,
        ),
        rate_limit_per_minute=_normalize_positive_int(
            rate_limit_per_minute,
            default=DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE,
            minimum=1,
            maximum=300,
        ),
        created_at=now,
        last_updated_at=now,
    )
    db.add(trigger)
    automation.last_updated_at = now
    if commit:
        db.commit()
        db.refresh(trigger)
    else:
        db.flush()
    return trigger, resolved_secret


def get_webhook_trigger(db: Session, trigger_id: str, user_id: str | None = None) -> AutomationWebhookTrigger | None:
    query = db.query(AutomationWebhookTrigger).filter(AutomationWebhookTrigger.id == trigger_id)
    if user_id:
        query = query.filter(AutomationWebhookTrigger.user_id == user_id)
    return query.first()


def get_webhook_trigger_for_automation(
    db: Session,
    user_id: str,
    automation_id: str,
) -> AutomationWebhookTrigger | None:
    return (
        db.query(AutomationWebhookTrigger)
        .filter(
            AutomationWebhookTrigger.user_id == user_id,
            AutomationWebhookTrigger.automation_id == automation_id,
        )
        .order_by(AutomationWebhookTrigger.created_at.desc())
        .first()
    )


def list_webhook_triggers_for_user(db: Session, user_id: str) -> list[AutomationWebhookTrigger]:
    return (
        db.query(AutomationWebhookTrigger)
        .filter(AutomationWebhookTrigger.user_id == user_id)
        .order_by(AutomationWebhookTrigger.created_at.desc())
        .all()
    )


def update_webhook_trigger(
    db: Session,
    user_id: str,
    trigger_id: str,
    *,
    name: str | None = None,
    is_enabled: bool | None = None,
    payload_mode: str | None = None,
    include_headers: bool | None = None,
    allowed_header_names: list[str] | None = None,
    max_body_bytes: int | None = None,
    rate_limit_per_minute: int | None = None,
) -> AutomationWebhookTrigger:
    trigger = get_webhook_trigger(db, trigger_id, user_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found")

    if name is not None:
        trigger.name = name.strip()[:255] if name.strip() else None
    if is_enabled is not None:
        trigger.is_enabled = bool(is_enabled)
    if payload_mode is not None:
        trigger.payload_mode = _normalize_payload_mode(payload_mode)
    if include_headers is not None:
        trigger.include_headers = bool(include_headers)
        if trigger.include_headers and not trigger.allowed_header_names:
            trigger.allowed_header_names = DEFAULT_WEBHOOK_ALLOWED_HEADERS
    if allowed_header_names is not None:
        trigger.allowed_header_names = _normalize_allowed_header_names(allowed_header_names) or (
            DEFAULT_WEBHOOK_ALLOWED_HEADERS if (include_headers if include_headers is not None else trigger.include_headers) else []
        )
    if max_body_bytes is not None:
        trigger.max_body_bytes = _normalize_positive_int(
            max_body_bytes,
            default=DEFAULT_WEBHOOK_MAX_BODY_BYTES,
            minimum=1024,
            maximum=1024 * 1024,
        )
    if rate_limit_per_minute is not None:
        trigger.rate_limit_per_minute = _normalize_positive_int(
            rate_limit_per_minute,
            default=DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE,
            minimum=1,
            maximum=300,
        )
    trigger.last_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trigger)
    return trigger


def rotate_webhook_trigger_secret(
    db: Session,
    user_id: str,
    trigger_id: str,
) -> tuple[AutomationWebhookTrigger, str]:
    trigger = get_webhook_trigger(db, trigger_id, user_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found")
    secret = generate_webhook_secret()
    trigger.token_hash = hash_webhook_secret(secret)
    trigger.token_prefix = secret[:16]
    trigger.last_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trigger)
    return trigger, secret


def delete_webhook_trigger(db: Session, user_id: str, trigger_id: str) -> bool:
    trigger = get_webhook_trigger(db, trigger_id, user_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Webhook trigger not found")
    (
        db.query(AutomationWebhookDelivery)
        .filter(AutomationWebhookDelivery.trigger_id == trigger_id)
        .delete(synchronize_session=False)
    )
    db.delete(trigger)
    db.commit()
    return True


def update_webhook_trigger_last_triggered(db: Session, trigger_id: str) -> None:
    trigger = db.query(AutomationWebhookTrigger).filter(AutomationWebhookTrigger.id == trigger_id).first()
    if trigger:
        trigger.last_triggered_at = datetime.now(timezone.utc)
        db.commit()


def create_webhook_delivery(
    db: Session,
    *,
    trigger_id: str,
    automation_id: str,
    user_id: str,
    status: str,
    status_code: int | None = None,
    error: str | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
    payload_preview: dict | None = None,
    chat_id: str | None = None,
) -> AutomationWebhookDelivery:
    delivery = AutomationWebhookDelivery(
        id=str(uuid.uuid4()),
        trigger_id=trigger_id,
        automation_id=automation_id,
        user_id=user_id,
        status=str(status or "accepted").strip()[:32],
        status_code=status_code,
        error=str(error or "").strip()[:255] or None,
        request_ip=str(request_ip or "").strip()[:128] or None,
        user_agent=str(user_agent or "").strip()[:255] or None,
        payload_preview=payload_preview if isinstance(payload_preview, dict) else None,
        chat_id=chat_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


def update_webhook_delivery(
    db: Session,
    delivery_id: str | None,
    *,
    status: str | None = None,
    status_code: int | None = None,
    error: str | None = None,
    chat_id: str | None = None,
) -> None:
    if not delivery_id:
        return
    delivery = db.query(AutomationWebhookDelivery).filter(AutomationWebhookDelivery.id == delivery_id).first()
    if not delivery:
        return
    if status is not None:
        delivery.status = str(status).strip()[:32]
    if status_code is not None:
        delivery.status_code = status_code
    if error is not None:
        delivery.error = str(error).strip()[:255] or None
    if chat_id is not None:
        delivery.chat_id = chat_id
    db.commit()


def list_webhook_deliveries(
    db: Session,
    user_id: str,
    trigger_id: str,
    *,
    limit: int = 20,
) -> list[AutomationWebhookDelivery]:
    safe_limit = _normalize_positive_int(limit, default=20, minimum=1, maximum=100)
    return (
        db.query(AutomationWebhookDelivery)
        .filter(
            AutomationWebhookDelivery.user_id == user_id,
            AutomationWebhookDelivery.trigger_id == trigger_id,
        )
        .order_by(AutomationWebhookDelivery.created_at.desc())
        .limit(safe_limit)
        .all()
    )
