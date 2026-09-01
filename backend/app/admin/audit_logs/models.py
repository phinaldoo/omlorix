"""Database queries for administrator access to the general audit store."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from app.logging.models import Logs
from sqlalchemy import and_, or_
from sqlalchemy.orm import Query, Session


AUDIT_REFERENCE_DETAIL_KEYS = (
    "reference",
    "user_id",
    "target_user_id",
    "chat_id",
    "message_id",
    "provider_id",
    "model_id",
    "group_id",
    "project_id",
    "todo_id",
    "todo_list_id",
    "note_id",
    "prompt_id",
    "skill_id",
    "file_id",
    "folder_id",
    "agent_id",
    "automation_id",
    "export_job_id",
    "connection_id",
    "server_id",
    "share_id",
)


@dataclass(frozen=True)
class AuditLogFilters:
    from_timestamp: datetime
    to_timestamp: datetime
    category: str | None = None
    action: str | None = None
    actor_user_id: str | None = None
    reference: str | None = None


def _filtered_audit_query(db: Session, filters: AuditLogFilters) -> Query:
    query = db.query(Logs).filter(
        Logs.timestamp >= filters.from_timestamp,
        Logs.timestamp <= filters.to_timestamp,
    )
    if filters.category:
        query = query.filter(Logs.category == filters.category)
    if filters.action:
        query = query.filter(Logs.action == filters.action)
    if filters.actor_user_id:
        query = query.filter(Logs.user_id == filters.actor_user_id)
    if filters.reference:
        reference_checks = [
            Logs.id == filters.reference,
            Logs.user_id == filters.reference,
        ]
        reference_checks.extend(
            Logs.details[key].as_string() == filters.reference
            for key in AUDIT_REFERENCE_DETAIL_KEYS
        )
        query = query.filter(or_(*reference_checks))
    return query


def list_audit_logs(
    db: Session,
    *,
    filters: AuditLogFilters,
    limit: int,
    cursor: tuple[datetime, str] | None,
) -> tuple[list[Logs], bool]:
    query = _filtered_audit_query(db, filters)
    if cursor:
        cursor_timestamp, cursor_id = cursor
        query = query.filter(
            or_(
                Logs.timestamp < cursor_timestamp,
                and_(Logs.timestamp == cursor_timestamp, Logs.id < cursor_id),
            )
        )
    rows = query.order_by(Logs.timestamp.desc(), Logs.id.desc()).limit(limit + 1).all()
    return rows[:limit], len(rows) > limit


def get_audit_log(db: Session, *, row_id: str, timestamp: datetime) -> Logs | None:
    return db.query(Logs).filter(Logs.id == row_id, Logs.timestamp == timestamp).first()


def count_audit_logs_capped(
    db: Session,
    *,
    filters: AuditLogFilters,
    cap: int,
) -> int:
    """Count only far enough to enforce the export limit."""

    return int(
        _filtered_audit_query(db, filters).with_entities(Logs.id).limit(cap + 1).count()
    )


def iter_audit_logs(
    db: Session,
    *,
    filters: AuditLogFilters,
    batch_size: int,
    limit: int,
) -> Iterator[Logs]:
    query = _filtered_audit_query(db, filters).order_by(
        Logs.timestamp.desc(), Logs.id.desc()
    )
    query = query.limit(limit)
    if hasattr(query, "execution_options"):
        query = query.execution_options(stream_results=True)
    if hasattr(query, "yield_per"):
        query = query.yield_per(batch_size)
    yield from query
