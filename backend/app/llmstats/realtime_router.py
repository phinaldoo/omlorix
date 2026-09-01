"""Administrator analytics for shared realtime LLM interaction facts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Integer, case, cast, desc, func
from sqlalchemy.orm import Session

from app.database import AuditSessionLocal
from app.dependencies import get_db, get_db_log, verified_admin
from app.llmstats.models import (
    INTERACTION_TYPE_REALTIME_RESPONSE,
    LLMGenerationStatistic,
    ToolCallStatistic,
)
from app.logging.models import create_audit_log, get_audit_request_ip
from app.realtime.models import RealtimeSession


logger = logging.getLogger(__name__)

realtime_stats_router = APIRouter(
    prefix="/api/v1/llmstats/admin/realtime",
    tags=["llm"],
)

REALTIME_STATS_EXPORT_VERSION = 2.0


def _audit_realtime_export_completion(
    *,
    user_id: str,
    details: dict[str, Any],
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """Best-effort terminal audit after a streamed export is fully generated."""

    audit_db = None
    try:
        audit_db = AuditSessionLocal()
        create_audit_log(
            db_log=audit_db,
            user_id=user_id,
            action="EXPORT_REALTIME_STATS_COMPLETED",
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
            category="admin",
        )
    except Exception:
        # Response bytes may already be on the wire. Do not turn an otherwise
        # truthful completed download into a late transport failure.
        logger.exception("Could not persist realtime statistics export completion audit")
    finally:
        if audit_db is not None:
            audit_db.close()


def _interaction_query(db: Session):
    """Return only response-grain realtime facts from the shared table."""
    return db.query(LLMGenerationStatistic).filter(
        LLMGenerationStatistic.interaction_type
        == INTERACTION_TYPE_REALTIME_RESPONSE
    )


def _tool_query(db: Session):
    """Return tool facts correlated to realtime calls by the shared recorder."""
    return db.query(ToolCallStatistic).filter(
        ToolCallStatistic.interaction_type
        == INTERACTION_TYPE_REALTIME_RESPONSE
    )


def _json_int(column, key: str):
    return func.coalesce(cast(func.nullif(column[key].astext, ""), Integer), 0)


def _error_expr():
    return LLMGenerationStatistic.status["error"].astext == "true"


def _session_dialect_name(db: Session) -> str:
    try:
        return str(db.get_bind().dialect.name or "").lower()
    except Exception:
        return ""


def _daily_bucket(db: Session):
    if _session_dialect_name(db) == "sqlite":
        return func.strftime("%Y-%m-%d 00:00:00", LLMGenerationStatistic.created_at)
    return func.date_trunc("day", LLMGenerationStatistic.created_at)


def _period_label(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).strftime("%Y-%m-%d")
    if isinstance(value, str):
        return value[:10]
    return ""


def _distinct_turn_count(query) -> int:
    return int(
        query.filter(LLMGenerationStatistic.turn_id.isnot(None))
        .with_entities(
            LLMGenerationStatistic.session_id,
            LLMGenerationStatistic.turn_id,
        )
        .group_by(
            LLMGenerationStatistic.session_id,
            LLMGenerationStatistic.turn_id,
        )
        .count()
    )


def _distinct_tool_call_count(query) -> int:
    """Count provider tool calls without assuming call IDs are global."""
    return int(
        query.filter(ToolCallStatistic.tool_call_id.isnot(None))
        .with_entities(
            ToolCallStatistic.session_id,
            ToolCallStatistic.tool_call_id,
        )
        .group_by(
            ToolCallStatistic.session_id,
            ToolCallStatistic.tool_call_id,
        )
        .count()
    )


def _serialize_session(row: RealtimeSession) -> dict[str, Any]:
    """Export safe lifecycle fields and exclude runtime state and user content."""
    return {
        "session_id": row.session_id,
        "model_id": row.model_id,
        "model_name": row.model_name,
        "provider": row.provider,
        "provider_id": row.provider_id,
        "status": row.status,
        "stop_reason": row.stop_reason,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
    }


def _serialize_interaction(row: LLMGenerationStatistic) -> dict[str, Any]:
    """Export one allowlisted response fact without chat/runtime payloads."""
    return {
        "id": row.id,
        "session_id": row.session_id,
        "turn_id": row.turn_id,
        "provider_response_id": row.provider_response_id,
        "turn_index": row.turn_index,
        "model_id": row.model_id,
        "model_name": row.model_name,
        "provider": row.provider,
        "provider_id": row.provider_id,
        "status": row.status or {},
        "usage": row.meta or {},
        "usage_source": row.usage_source,
        "usage_verified": bool(row.usage_verified),
        "interrupted": bool(row.interrupted),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_tool_call(row: ToolCallStatistic) -> dict[str, Any]:
    """Export tool execution facts without arguments, outputs, or tool metadata."""
    return {
        "id": row.id,
        "session_id": row.session_id,
        "turn_id": row.turn_id,
        "tool_call_id": row.tool_call_id,
        "tool_name": row.tool_name,
        "success": bool(row.success),
        "error_message": row.error_message,
        "execution_time": row.execution_time,
        "model_id": row.model_id,
        "model_name": row.model_name,
        "provider": row.provider,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@realtime_stats_router.get("/overview")
def get_realtime_overview(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Return call-level and response-level realtime KPIs."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    session_query = db.query(RealtimeSession).filter(
        RealtimeSession.created_at >= cutoff
    )
    sessions = session_query.with_entities(
        RealtimeSession.started_at,
        RealtimeSession.ended_at,
        RealtimeSession.status,
    ).all()
    total_sessions = len(sessions)
    active_sessions = sum(
        1 for row in sessions if str(row.status or "").lower() == "active"
    )
    now = datetime.now(timezone.utc)
    call_seconds = 0.0
    for row in sessions:
        started = row.started_at
        if started is None:
            continue
        ended = row.ended_at or now
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        call_seconds += max((ended - started).total_seconds(), 0.0)

    interactions = _interaction_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff
    )
    aggregate = interactions.with_entities(
        func.count(LLMGenerationStatistic.id).label("responses"),
        func.coalesce(func.sum(_json_int(LLMGenerationStatistic.meta, "input_tokens")), 0).label("input_tokens"),
        func.coalesce(func.sum(_json_int(LLMGenerationStatistic.meta, "output_tokens")), 0).label("output_tokens"),
        func.coalesce(func.sum(_json_int(LLMGenerationStatistic.meta, "input_audio_tokens")), 0).label("input_audio_tokens"),
        func.coalesce(func.sum(_json_int(LLMGenerationStatistic.meta, "output_audio_tokens")), 0).label("output_audio_tokens"),
        func.coalesce(func.sum(case((LLMGenerationStatistic.usage_verified.is_(True), 1), else_=0)), 0).label("verified"),
    ).one()
    total_turns = _distinct_turn_count(interactions)
    interruptions = _distinct_turn_count(
        interactions.filter(LLMGenerationStatistic.interrupted.is_(True))
    )
    error_turns = _distinct_turn_count(interactions.filter(_error_expr()))
    responses = int(aggregate.responses or 0)
    verified = int(aggregate.verified or 0)
    realtime_tools = _tool_query(db).filter(ToolCallStatistic.created_at >= cutoff)
    tool_calls = _distinct_tool_call_count(realtime_tools)
    websearch_calls = _distinct_tool_call_count(
        realtime_tools.filter(ToolCallStatistic.tool_name == "web_search")
    )
    return {
        "period_days": days,
        "total_sessions": total_sessions,
        "active_sessions": active_sessions,
        "total_call_seconds": round(call_seconds, 2),
        "total_responses": responses,
        "verified_responses": verified,
        "unverified_responses": max(responses - verified, 0),
        "total_turns": total_turns,
        "avg_turns_per_session": round(total_turns / total_sessions, 2) if total_sessions else 0.0,
        "interruptions": interruptions,
        "interruption_rate": round(interruptions / total_turns * 100, 1) if total_turns else 0.0,
        "input_tokens": int(aggregate.input_tokens or 0),
        "output_tokens": int(aggregate.output_tokens or 0),
        "input_audio_tokens": int(aggregate.input_audio_tokens or 0),
        "output_audio_tokens": int(aggregate.output_audio_tokens or 0),
        "tool_calls": int(tool_calls),
        "websearch_calls": int(websearch_calls),
        "error_turns": error_turns,
    }


@realtime_stats_router.get("/timeline")
def get_realtime_timeline(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    bucket = _daily_bucket(db)
    rows = (
        _interaction_query(db)
        .filter(LLMGenerationStatistic.created_at >= cutoff)
        .with_entities(
            bucket.label("bucket"),
            func.count(func.distinct(LLMGenerationStatistic.turn_id)).label("turns"),
            func.coalesce(func.sum(case((LLMGenerationStatistic.interrupted.is_(True), 1), else_=0)), 0).label("interruptions"),
            func.coalesce(func.sum(case((_error_expr(), 1), else_=0)), 0).label("errors"),
        )
        .group_by(bucket)
        .order_by(bucket)
        .all()
    )
    return {
        "period_days": days,
        "timeline": [
            {
                "period": _period_label(row.bucket),
                "turns": int(row.turns or 0),
                "interruptions": int(row.interruptions or 0),
                "errors": int(row.errors or 0),
            }
            for row in rows
        ],
    }


@realtime_stats_router.get("/by-model")
def get_realtime_by_model(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        _interaction_query(db)
        .filter(LLMGenerationStatistic.created_at >= cutoff)
        .with_entities(
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            func.count(func.distinct(LLMGenerationStatistic.session_id)).label("sessions"),
            func.count(func.distinct(LLMGenerationStatistic.turn_id)).label("turns"),
            func.coalesce(func.sum(case((LLMGenerationStatistic.interrupted.is_(True), 1), else_=0)), 0).label("interruptions"),
        )
        .group_by(LLMGenerationStatistic.model_name, LLMGenerationStatistic.provider)
        .order_by(desc("turns"))
        .all()
    )
    return {
        "period_days": days,
        "models": [
            {
                "model_name": str(row.model_name or "unknown"),
                "provider": row.provider,
                "sessions": int(row.sessions or 0),
                "turns": int(row.turns or 0),
                "interruptions": int(row.interruptions or 0),
            }
            for row in rows
        ],
    }


@realtime_stats_router.get("/errors")
def get_realtime_errors(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base = _interaction_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff,
        _error_expr(),
    )
    total = base.count()
    rows = (
        base.order_by(LLMGenerationStatistic.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    row_session_ids = [row.session_id for row in rows if row.session_id]
    chat_by_session = {
        session_id: chat_id
        for session_id, chat_id in db.query(
            RealtimeSession.session_id,
            RealtimeSession.chat_id,
        )
        .filter(RealtimeSession.session_id.in_(row_session_ids))
        .all()
    } if row_session_ids else {}
    return {
        "errors": [
            {
                "id": row.id,
                "session_id": row.session_id,
                "chat_id": chat_by_session.get(row.session_id),
                "turn_index": row.turn_index,
                "error_message": (row.status or {}).get("error_message"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "period_days": days,
    }


@realtime_stats_router.get("/interruptions")
def get_realtime_interruptions(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base = _interaction_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff
    )
    total_turns = _distinct_turn_count(base)
    interrupted = base.filter(LLMGenerationStatistic.interrupted.is_(True))
    interrupted_turns = _distinct_turn_count(interrupted)
    examples = interrupted.order_by(LLMGenerationStatistic.created_at.desc()).limit(50).all()
    return {
        "period_days": days,
        "total_turns": total_turns,
        "interrupted_turns": interrupted_turns,
        "interruption_rate": round(interrupted_turns / total_turns * 100, 1) if total_turns else 0.0,
        "examples": [
            {
                "id": row.id,
                "session_id": row.session_id,
                "turn_index": row.turn_index,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in examples
        ],
    }


@realtime_stats_router.delete("/all")
def delete_realtime_statistics(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Delete analytics facts without terminating active operational calls."""
    query = _interaction_query(db)
    tool_query = _tool_query(db)
    affected_session_ids = {
        value for (value,) in query.with_entities(LLMGenerationStatistic.session_id).distinct().all() if value
    }
    affected_session_ids.update(
        value
        for (value,) in tool_query.with_entities(ToolCallStatistic.session_id).distinct().all()
        if value
    )
    turn_keys = {
        (session_id, turn_id)
        for session_id, turn_id in query.with_entities(
            LLMGenerationStatistic.session_id,
            LLMGenerationStatistic.turn_id,
        ).distinct().all()
        if turn_id
    }
    deleted_interactions = query.delete(synchronize_session=False)
    deleted_tool_calls = tool_query.delete(synchronize_session=False)
    # Completed rows are lifecycle summaries used only by this dashboard.
    # Active rows remain authoritative operational state and must never be
    # removed by an analytics maintenance action.
    deleted_sessions = (
        db.query(RealtimeSession)
        .filter(RealtimeSession.status != "active")
        .delete(synchronize_session=False)
    )
    db.commit()
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DELETE_REALTIME_STATS",
        details={
            "deleted_interactions": deleted_interactions,
            "deleted_tool_calls": deleted_tool_calls,
            "affected_turns": len(turn_keys),
            "affected_sessions": len(affected_session_ids),
            "deleted_session_summaries": deleted_sessions,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {
        "success": True,
        "deleted_interactions": int(deleted_interactions or 0),
        "deleted_tool_calls": int(deleted_tool_calls or 0),
        "deleted_turns": len(turn_keys),
        "deleted_sessions": int(deleted_sessions or 0),
    }


@realtime_stats_router.get("/export")
def export_realtime_statistics(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Stream safe lifecycle summaries and response-grain interaction facts."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    user_id = str(admin_user.id)
    audit_ip_address = get_audit_request_ip(request, db)
    audit_user_agent = request.headers.get("user-agent")

    # Record admission before returning a lazy response. A terminal event is
    # emitted only after every backing query and serializer has completed.
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action="EXPORT_REALTIME_STATS_STARTED",
        details={
            "export_version": REALTIME_STATS_EXPORT_VERSION,
            "period_days": days,
        },
        ip_address=audit_ip_address,
        user_agent=audit_user_agent,
        category="admin",
    )

    def iter_export():
        session_count = 0
        interaction_count = 0
        tool_call_count = 0
        header = json.dumps(
            {
                "export_type": "realtime_stats",
                "export_version": REALTIME_STATS_EXPORT_VERSION,
                "grain": "provider_response",
                "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "period_days": days,
            },
            separators=(",", ":"),
        )
        yield f'{header[:-1]},"data":{{"sessions":['
        first = True
        for row in (
            db.query(RealtimeSession)
            .filter(RealtimeSession.created_at >= cutoff)
            .order_by(desc(RealtimeSession.created_at))
            .yield_per(500)
        ):
            if not first:
                yield ","
            first = False
            serialized = json.dumps(_serialize_session(row), separators=(",", ":"))
            session_count += 1
            yield serialized

        yield '],"interactions":['
        first = True
        for row in (
            _interaction_query(db)
            .filter(LLMGenerationStatistic.created_at >= cutoff)
            .order_by(desc(LLMGenerationStatistic.created_at))
            .yield_per(1000)
        ):
            if not first:
                yield ","
            first = False
            serialized = json.dumps(
                _serialize_interaction(row), separators=(",", ":")
            )
            interaction_count += 1
            yield serialized

        yield '],"tool_calls":['
        first = True
        for row in (
            _tool_query(db)
            .filter(ToolCallStatistic.created_at >= cutoff)
            .order_by(desc(ToolCallStatistic.created_at))
            .yield_per(1000)
        ):
            if not first:
                yield ","
            first = False
            serialized = json.dumps(_serialize_tool_call(row), separators=(",", ":"))
            tool_call_count += 1
            yield serialized
        yield "]}}"

        _audit_realtime_export_completion(
            user_id=user_id,
            details={
                "export_version": REALTIME_STATS_EXPORT_VERSION,
                "period_days": days,
                "realtime_record_count": session_count,
                "interaction_count": interaction_count,
                "tool_call_count": tool_call_count,
            },
            ip_address=audit_ip_address,
            user_agent=audit_user_agent,
        )

    return StreamingResponse(iter_export(), media_type="application/json")
