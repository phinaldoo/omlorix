"""Operational persistence for long-lived realtime sessions.

This module deliberately contains no analytics facts. Provider-response usage
is recorded through :mod:`app.llmstats.models`; this table exists only so
session ownership, provider termination, quota deadlines, and reconnect state
survive process restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from fastapi import HTTPException
from sqlalchemy import Column, DateTime, Index, String, and_, or_
from sqlalchemy.dialects.postgresql import JSON

from app.database import Base


_ACTIVE_REALTIME_SESSION_DEFAULT_LIMIT = 100
_ACTIVE_REALTIME_SESSION_MAX_LIMIT = 1000


class RealtimeSession(Base):
    """Authoritative operational state for one realtime call."""

    __tablename__ = "realtime_sessions"
    __table_args__ = (
        Index("ix_realtime_sessions_user_id", "user_id"),
        Index("ix_realtime_sessions_chat_id", "chat_id"),
        Index("ix_realtime_sessions_created_at", "created_at"),
        Index("ix_realtime_sessions_status", "status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, nullable=False, unique=True, index=True)
    user_id = Column(String, nullable=True)
    chat_id = Column(String, nullable=False)
    model_id = Column(String, nullable=True)
    model_name = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    provider_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active")
    runtime_state = Column(JSON, nullable=False, default=dict)
    stop_reason = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)
    last_updated_at = Column(DateTime, nullable=False)


def create_realtime_session(
    db,
    *,
    session_id: str,
    user_id: str | None,
    chat_id: str,
    model_id: str | None,
    model_name: str,
    provider: str,
    provider_id: str,
    started_at: datetime | None = None,
    runtime_state: dict[str, Any] | None = None,
) -> RealtimeSession:
    """Create the operational row before exposing a session to the browser."""
    now = datetime.now(timezone.utc)
    record = RealtimeSession(
        session_id=session_id,
        user_id=user_id,
        chat_id=chat_id,
        model_id=model_id,
        model_name=model_name,
        provider=provider,
        provider_id=provider_id,
        status="active",
        runtime_state=runtime_state or {},
        started_at=started_at or now,
        created_at=now,
        last_updated_at=now,
    )
    try:
        db.add(record)
        db.commit()
        db.refresh(record)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to create realtime session",
        ) from exc
    return record


def update_realtime_session(
    db,
    session_record_id: str,
    *,
    status: str | None = None,
    ended_at: datetime | None = None,
    runtime_state: dict[str, Any] | None = None,
    stop_reason: str | None = None,
    commit: bool = True,
) -> RealtimeSession | None:
    """Update lifecycle state without accepting arbitrary analytics fields."""
    record = (
        db.query(RealtimeSession)
        .filter(RealtimeSession.id == session_record_id)
        .first()
    )
    if record is None:
        return None
    if status is not None:
        record.status = status
    if ended_at is not None:
        record.ended_at = ended_at
    if runtime_state is not None:
        record.runtime_state = dict(runtime_state)
    if stop_reason is not None:
        record.stop_reason = str(stop_reason)[:256]
    record.last_updated_at = datetime.now(timezone.utc)
    db.add(record)
    if commit:
        db.commit()
        db.refresh(record)
    else:
        db.flush()
    return record


def get_realtime_session_by_session_id(
    db,
    *,
    session_id: str,
) -> RealtimeSession | None:
    """Return one public session identifier's operational row."""
    return (
        db.query(RealtimeSession)
        .filter(RealtimeSession.session_id == session_id)
        .first()
    )


def list_expired_realtime_sessions(
    db,
    *,
    last_updated_before: datetime,
    created_before: datetime,
    limit: int = 100,
) -> list[RealtimeSession]:
    """Return active rows exceeding either operational freshness boundary."""
    return (
        db.query(RealtimeSession)
        .filter(RealtimeSession.status == "active")
        .filter(
            or_(
                RealtimeSession.last_updated_at <= last_updated_before,
                RealtimeSession.created_at <= created_before,
            )
        )
        .order_by(RealtimeSession.last_updated_at.asc())
        .limit(max(1, int(limit or 100)))
        .all()
    )


def list_active_realtime_sessions_for_user(
    db,
    *,
    user_id: str,
    limit: int | None = _ACTIVE_REALTIME_SESSION_DEFAULT_LIMIT,
) -> list[RealtimeSession]:
    """Return active sessions that may carry one user's quota reservation."""
    normalized_limit = (
        _ACTIVE_REALTIME_SESSION_DEFAULT_LIMIT if limit is None else int(limit)
    )
    normalized_limit = min(
        max(normalized_limit, 0),
        _ACTIVE_REALTIME_SESSION_MAX_LIMIT,
    )
    return (
        db.query(RealtimeSession)
        .filter(
            RealtimeSession.status == "active",
            RealtimeSession.user_id == user_id,
        )
        .order_by(RealtimeSession.created_at.asc())
        .limit(normalized_limit)
        .all()
    )


def list_active_realtime_sessions(
    db,
    *,
    limit: int | None = _ACTIVE_REALTIME_SESSION_MAX_LIMIT,
    after_created_at: datetime | None = None,
    after_id: str | None = None,
) -> list[RealtimeSession]:
    """Page through active sessions for authoritative deadline enforcement."""
    normalized_limit = (
        _ACTIVE_REALTIME_SESSION_MAX_LIMIT if limit is None else int(limit)
    )
    normalized_limit = min(
        max(normalized_limit, 0),
        _ACTIVE_REALTIME_SESSION_MAX_LIMIT,
    )
    query = db.query(RealtimeSession).filter(RealtimeSession.status == "active")
    if after_created_at is not None and after_id:
        query = query.filter(
            or_(
                RealtimeSession.created_at > after_created_at,
                and_(
                    RealtimeSession.created_at == after_created_at,
                    RealtimeSession.id > after_id,
                ),
            )
        )
    return (
        query.order_by(RealtimeSession.created_at.asc(), RealtimeSession.id.asc())
        .limit(normalized_limit)
        .all()
    )
