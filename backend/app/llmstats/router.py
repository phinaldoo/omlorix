import logging

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, case, cast, Float, Integer, or_, exists
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from app.dependencies import get_db, get_db_log, verified_admin, verified_user
from app.llmstats.models import (
    LLMGenerationStatistic,
    ToolCallStatistic,
    AVAILABLE_CATEGORIES,
    INTERACTION_TYPE_GENERATION,
    export_llm_generation_stats,
    export_tool_call_stats,
    invalidate_user_statistics_cache,
    coerce_bool,
    coerce_byok_stats_retention_days,
    sanitize_provider_error_message,
)
from app.logging.models import create_audit_log, get_audit_request_ip
from app.llm.models import LLMProvider, Models
from app.users.init import get_user_setting_value, update_user_settings

llmstats_router = APIRouter(prefix="/api/v1/llmstats", tags=["llm"])
logger = logging.getLogger(__name__)

BYOK_USAGE_STATS_EXPORT_VERSION = 1.0


def _admin_llm_query(
    db: Session,
    *,
    include_user_managed: bool = False,
):
    """Build the administrator statistics query with private models hidden.

    User-managed providers use the shared generation-statistics table, but
    their names and usage belong to a private per-user configuration and must not
    appear in global, user, or group administrator dashboards. Destructive
    maintenance operations can opt in so hidden rows are still deletable.
    """
    query = db.query(LLMGenerationStatistic).filter(
        LLMGenerationStatistic.is_byok.is_(False),
        LLMGenerationStatistic.interaction_type == INTERACTION_TYPE_GENERATION,
    )
    if include_user_managed:
        return query

    user_managed_provider_exists = exists().where(
        LLMProvider.id == LLMGenerationStatistic.provider_id,
        LLMProvider.settings["user_managed"].as_boolean().is_(True),
    )
    statistic_is_user_managed = (
        LLMGenerationStatistic.meta["user_managed"].as_boolean().is_(True)
    )
    return query.filter(~or_(user_managed_provider_exists, statistic_is_user_managed))


def _admin_tool_query(db: Session):
    return db.query(ToolCallStatistic).filter(ToolCallStatistic.is_byok.is_(False))


def _user_byok_llm_query(db: Session, user_id: str):
    return db.query(LLMGenerationStatistic).filter(
        LLMGenerationStatistic.user_id == user_id,
        LLMGenerationStatistic.is_byok.is_(True),
        LLMGenerationStatistic.interaction_type == INTERACTION_TYPE_GENERATION,
    )


def _user_byok_tool_query(db: Session, user_id: str):
    return db.query(ToolCallStatistic).filter(
        ToolCallStatistic.user_id == user_id,
        ToolCallStatistic.is_byok.is_(True),
    )


def _load_provider_name_map(db: Session, provider_ids: set[str] | list[str] | tuple[str, ...]) -> dict[str, str]:
    valid_ids = sorted(
        {
            provider_id
            for provider_id in provider_ids
            if isinstance(provider_id, str) and provider_id.strip()
        }
    )
    if not valid_ids:
        return {}

    rows = (
        db.query(LLMProvider.id, LLMProvider.name)
        .filter(LLMProvider.id.in_(valid_ids))
        .all()
    )
    return {row_id: row_name for row_id, row_name in rows if row_id}


def _load_model_display_name_map(
    db: Session,
    model_ids: set[str] | list[str] | tuple[str, ...],
) -> dict[str, str]:
    valid_ids = sorted(
        {
            model_id
            for model_id in model_ids
            if isinstance(model_id, str) and model_id.strip()
        }
    )
    if not valid_ids:
        return {}

    rows = (
        db.query(Models.id, Models.name)
        .filter(Models.id.in_(valid_ids))
        .all()
    )
    return {row_id: row_name for row_id, row_name in rows if row_id and row_name}


def _json_int_expr(json_column, key: str):
    return func.coalesce(cast(func.nullif(json_column[key].astext, ""), Integer), 0)


def _json_int_nullable_expr(json_column, key: str):
    """Return a nullable integer expression so aliases can be coalesced safely."""
    return cast(func.nullif(json_column[key].astext, ""), Integer)


def _cached_input_tokens_expr(json_column):
    """Read cached input tokens without double-counting provider aliases."""
    return func.coalesce(
        _json_int_nullable_expr(json_column, "input_token_cached"),
        _json_int_nullable_expr(json_column, "cached_input_tokens"),
        _json_int_nullable_expr(json_column, "input_tokens_cached"),
        0,
    )


def _json_float_expr(json_column, key: str):
    return func.coalesce(cast(func.nullif(json_column[key].astext, ""), Float), 0.0)


def _llm_total_cost_expr():
    """Resolve one generation cost from canonical and historical metadata.

    ``total_costs`` is authoritative. Older rows may only have component costs,
    while OpenRouter-compatible rows can contain only the upstream inference
    total, so the fallbacks are mutually exclusive rather than additive.
    """
    total_costs_expr = _json_float_expr(LLMGenerationStatistic.meta, "total_costs")
    input_cost_expr = _json_float_expr(LLMGenerationStatistic.meta, "input_tokens_cost")
    output_cost_expr = _json_float_expr(LLMGenerationStatistic.meta, "output_tokens_cost")
    websearch_cost_expr = _json_float_expr(LLMGenerationStatistic.meta, "native_websearch_costs")
    upstream_cost_expr = _json_float_expr(
        LLMGenerationStatistic.meta,
        "upstream_inference_cost",
    )
    component_cost_expr = input_cost_expr + output_cost_expr + websearch_cost_expr
    return case(
        (total_costs_expr > 0, total_costs_expr),
        (component_cost_expr > 0, component_cost_expr),
        else_=upstream_cost_expr,
    )


def _tool_total_cost_expr():
    direct_cost_expr = _json_float_expr(ToolCallStatistic.meta, "cost")
    total_costs_expr = _json_float_expr(ToolCallStatistic.meta, "total_costs")
    estimated_cost_expr = _json_float_expr(ToolCallStatistic.meta, "estimated_cost")
    nested_cost_expr = func.coalesce(
        cast(func.nullif(ToolCallStatistic.meta["cost_details"]["cost"].astext, ""), Float),
        0.0,
    )
    nested_total_costs_expr = func.coalesce(
        cast(func.nullif(ToolCallStatistic.meta["cost_details"]["total_costs"].astext, ""), Float),
        0.0,
    )
    nested_websearch_cost_expr = func.coalesce(
        cast(func.nullif(ToolCallStatistic.meta["cost_details"]["websearch_total_cost"].astext, ""), Float),
        0.0,
    )
    nested_upstream_cost_expr = func.coalesce(
        cast(func.nullif(ToolCallStatistic.meta["cost_details"]["upstream_inference_cost"].astext, ""), Float),
        0.0,
    )
    subtotal_expr = (
        _json_float_expr(ToolCallStatistic.meta, "input_tokens_cost")
        + _json_float_expr(ToolCallStatistic.meta, "output_tokens_cost")
        + _json_float_expr(ToolCallStatistic.meta, "native_websearch_costs")
    )
    return case(
        (direct_cost_expr > 0, direct_cost_expr),
        (total_costs_expr > 0, total_costs_expr),
        (estimated_cost_expr > 0, estimated_cost_expr),
        (nested_cost_expr > 0, nested_cost_expr),
        (nested_total_costs_expr > 0, nested_total_costs_expr),
        (nested_websearch_cost_expr > 0, nested_websearch_cost_expr),
        (nested_upstream_cost_expr > 0, nested_upstream_cost_expr),
        else_=subtotal_expr,
    )


def _llm_success_count_expr():
    return func.coalesce(
        func.sum(case((LLMGenerationStatistic.status["success"].astext == "true", 1), else_=0)),
        0,
    )


def _apply_llm_dimension_filters(
    query,
    *,
    provider: str | None = None,
    provider_id: str | None = None,
    model: str | None = None,
    category: str | None = None,
):
    if provider_id:
        query = query.filter(LLMGenerationStatistic.provider_id == provider_id)
    elif provider:
        query = query.filter(LLMGenerationStatistic.provider == provider)
    if model:
        query = query.filter(LLMGenerationStatistic.model_id == model)
    if category:
        query = query.filter(LLMGenerationStatistic.category == category)
    return query


def _session_dialect_name(db: Session) -> str:
    try:
        bind = db.get_bind()
    except Exception:
        return ""
    return str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()


def _timeline_bucket_expr(db: Session, column, granularity: str):
    if _session_dialect_name(db) == "sqlite":
        if granularity == "weekly":
            days_since_monday = (cast(func.strftime("%w", column), Integer) + 6) % 7
            return func.datetime(column, func.printf("-%s days", days_since_monday), "start of day")
        sqlite_formats = {
            "hourly": "%Y-%m-%d %H:00:00",
            "daily": "%Y-%m-%d 00:00:00",
            "monthly": "%Y-%m-01 00:00:00",
        }
        return func.strftime(sqlite_formats.get(granularity, "%Y-%m-%d 00:00:00"), column)

    date_trunc_unit = {
        "hourly": "hour",
        "daily": "day",
        "weekly": "week",
        "monthly": "month",
    }.get(granularity, "day")
    return func.date_trunc(date_trunc_unit, column)


def _timeline_period_from_bucket(bucket_start: datetime | str | None, granularity: str) -> tuple[str, str | None]:
    if isinstance(bucket_start, datetime):
        aware_dt = bucket_start if bucket_start.tzinfo else bucket_start.replace(tzinfo=timezone.utc)
    elif isinstance(bucket_start, str):
        try:
            aware_dt = datetime.fromisoformat(bucket_start.replace("Z", "+00:00"))
            if aware_dt.tzinfo is None:
                aware_dt = aware_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return bucket_start, bucket_start
    else:
        aware_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    aware_dt = aware_dt.astimezone(timezone.utc)
    if granularity == "hourly":
        period = aware_dt.strftime("%Y-%m-%d %H:00")
    elif granularity == "weekly":
        period = aware_dt.strftime("%Y-W%W")
    elif granularity == "monthly":
        period = aware_dt.strftime("%Y-%m")
    else:
        period = aware_dt.strftime("%Y-%m-%d")
    return period, aware_dt.isoformat().replace("+00:00", "Z")


def _provider_name_for_row(provider_name_map: dict[str, str], provider_id: str | None, provider: str | None) -> str:
    if provider_id:
        return provider_name_map.get(provider_id) or provider or "unknown"
    return provider or "unknown"


@llmstats_router.get("/admin/overview")
def get_llm_stats_overview(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get overview statistics for the LLM dashboard."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    base_query = _admin_llm_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff
    )

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    reasoning_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "reasoning_tokens")
    cached_tokens_expr = _cached_input_tokens_expr(LLMGenerationStatistic.meta)
    cache_write_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "cache_write_tokens")
    input_cost_expr = _json_float_expr(LLMGenerationStatistic.meta, "input_tokens_cost")
    output_cost_expr = _json_float_expr(LLMGenerationStatistic.meta, "output_tokens_cost")
    websearch_cost_expr = _json_float_expr(LLMGenerationStatistic.meta, "native_websearch_costs")
    total_cost_expr = _llm_total_cost_expr()
    generation_time_expr = _json_float_expr(LLMGenerationStatistic.meta, "generation_time")
    tokens_per_second_expr = _json_float_expr(LLMGenerationStatistic.meta, "tokens_per_second")
    time_to_first_token_expr = _json_float_expr(LLMGenerationStatistic.meta, "time_to_first_token")

    row = base_query.with_entities(
        func.count(LLMGenerationStatistic.id).label("total_requests"),
        _llm_success_count_expr().label("success_count"),
        func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
        func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
        func.coalesce(func.sum(reasoning_tokens_expr), 0).label("reasoning_tokens"),
        func.coalesce(func.sum(cached_tokens_expr), 0).label("cached_tokens"),
        func.coalesce(func.sum(cache_write_tokens_expr), 0).label("cache_write_tokens"),
        func.coalesce(func.sum(input_cost_expr), 0.0).label("input_cost"),
        func.coalesce(func.sum(output_cost_expr), 0.0).label("output_cost"),
        func.coalesce(func.sum(websearch_cost_expr), 0.0).label("websearch_cost"),
        func.coalesce(func.sum(total_cost_expr), 0.0).label("total_cost"),
        func.avg(case((generation_time_expr > 0, generation_time_expr))).label("avg_generation_time"),
        func.avg(case((tokens_per_second_expr > 0, tokens_per_second_expr))).label("avg_tokens_per_second"),
        func.avg(case((time_to_first_token_expr > 0, time_to_first_token_expr))).label("avg_time_to_first_token"),
    ).one()

    total_requests = int(row.total_requests or 0)
    success_count = int(row.success_count or 0)
    error_count = total_requests - success_count
    total_input_tokens = int(row.input_tokens or 0)
    total_output_tokens = int(row.output_tokens or 0)
    total_reasoning_tokens = int(row.reasoning_tokens or 0)
    total_cached_tokens = int(row.cached_tokens or 0)
    total_cache_write_tokens = int(row.cache_write_tokens or 0)
    total_input_cost = float(row.input_cost or 0.0)
    total_output_cost = float(row.output_cost or 0.0)
    total_websearch_cost = float(row.websearch_cost or 0.0)
    total_cost = float(row.total_cost or 0.0)
    avg_generation_time = round(float(row.avg_generation_time or 0.0), 2)
    avg_tokens_per_second = round(float(row.avg_tokens_per_second or 0.0), 2)
    avg_time_to_first_token = round(float(row.avg_time_to_first_token or 0.0), 2)
    
    return {
        "total_requests": total_requests,
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": round((success_count / total_requests * 100), 1) if total_requests > 0 else 0,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_reasoning_tokens": total_reasoning_tokens,
        "total_cached_tokens": total_cached_tokens,
        "total_cache_write_tokens": total_cache_write_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "estimated_input_cost": round(total_input_cost, 6),
        "estimated_output_cost": round(total_output_cost, 6),
        "estimated_websearch_cost": round(total_websearch_cost, 6),
        "estimated_total_cost": round(total_cost, 6),
        "avg_generation_time": avg_generation_time,
        "avg_tokens_per_second": avg_tokens_per_second,
        "avg_time_to_first_token": avg_time_to_first_token,
        "period_days": days,
    }


@llmstats_router.get("/admin/timeline")
def get_llm_stats_timeline(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    granularity: str = Query(default="daily", pattern="^(hourly|daily|weekly|monthly)$"),
    provider: Optional[str] = None,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
    category: Optional[str] = None,
):
    """Get timeline data for charts."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = _admin_llm_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff
    )
    query = _apply_llm_dimension_filters(
        query,
        provider=provider,
        provider_id=provider_id,
        model=model,
        category=category,
    )

    bucket_expr = _timeline_bucket_expr(db, LLMGenerationStatistic.created_at, granularity)
    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    generation_time_expr = _json_float_expr(LLMGenerationStatistic.meta, "generation_time")
    rows = (
        query.with_entities(
            bucket_expr.label("bucket_start"),
            func.count(LLMGenerationStatistic.id).label("requests"),
            _llm_success_count_expr().label("success"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("cost"),
            func.avg(case((generation_time_expr > 0, generation_time_expr))).label("avg_generation_time"),
        )
        .group_by(bucket_expr)
        .order_by(bucket_expr)
        .all()
    )

    timeline = []
    for row in rows:
        period, bucket_start_iso = _timeline_period_from_bucket(row.bucket_start, granularity)
        requests = int(row.requests or 0)
        success = int(row.success or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        timeline.append({
            "period": period,
            "bucket_start": bucket_start_iso,
            "requests": requests,
            "success": success,
            "errors": requests - success,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": round(float(row.cost or 0.0), 4),
            "avg_generation_time": round(float(row.avg_generation_time or 0.0), 2),
        })

    return {"timeline": timeline, "granularity": granularity, "period_days": days}


@llmstats_router.get("/admin/by-provider")
def get_llm_stats_by_provider(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics grouped by provider."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    rows = (
        _admin_llm_query(db)
        .filter(LLMGenerationStatistic.created_at >= cutoff)
        .with_entities(
            LLMGenerationStatistic.provider_id,
            LLMGenerationStatistic.provider,
            func.count(LLMGenerationStatistic.id).label("requests"),
            _llm_success_count_expr().label("success"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("cost"),
        )
        .group_by(LLMGenerationStatistic.provider_id, LLMGenerationStatistic.provider)
        .order_by(desc("requests"))
        .all()
    )

    provider_name_map = _load_provider_name_map(
        db,
        {row.provider_id for row in rows if row.provider_id},
    )
    result = []
    for row in rows:
        requests = int(row.requests or 0)
        success = int(row.success or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        provider_type = row.provider or "unknown"
        result.append(
            {
                "provider_id": row.provider_id or None,
                "provider": provider_type,
                "provider_name": _provider_name_for_row(provider_name_map, row.provider_id, provider_type),
                "requests": requests,
                "success": success,
                "errors": requests - success,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": round(float(row.cost or 0.0), 4),
                "total_tokens": input_tokens + output_tokens,
            }
        )

    return {"providers": result, "period_days": days}


@llmstats_router.get("/admin/by-model")
def get_llm_stats_by_model(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    provider: Optional[str] = None,
    provider_id: Optional[str] = None,
):
    """Get statistics grouped by model."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = _admin_llm_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff
    )
    query = _apply_llm_dimension_filters(query, provider=provider, provider_id=provider_id)

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    generation_time_expr = _json_float_expr(LLMGenerationStatistic.meta, "generation_time")
    rows = (
        query.with_entities(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
            func.count(LLMGenerationStatistic.id).label("requests"),
            _llm_success_count_expr().label("success"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("cost"),
            func.avg(case((generation_time_expr > 0, generation_time_expr))).label("avg_generation_time"),
        )
        .group_by(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
        )
        .order_by(desc("requests"))
        .all()
    )

    provider_name_map = _load_provider_name_map(
        db,
        {row.provider_id for row in rows if row.provider_id},
    )
    result = []
    for row in rows:
        requests = int(row.requests or 0)
        success = int(row.success or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        provider_type = row.provider or "unknown"
        result.append(
            {
                "model_name": row.model_name or row.model_id,
                "model_id": row.model_id,
                "provider": provider_type,
                "provider_id": row.provider_id or None,
                "provider_name": _provider_name_for_row(provider_name_map, row.provider_id, provider_type),
                "requests": requests,
                "success": success,
                "errors": requests - success,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": round(float(row.cost or 0.0), 4),
                "avg_generation_time": round(float(row.avg_generation_time or 0.0), 2),
                "total_tokens": input_tokens + output_tokens,
            }
        )

    return {"models": result, "period_days": days}


@llmstats_router.get("/admin/by-category")
def get_llm_stats_by_category(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics grouped by category."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    rows = (
        _admin_llm_query(db)
        .filter(LLMGenerationStatistic.created_at >= cutoff)
        .with_entities(
            LLMGenerationStatistic.category,
            func.count(LLMGenerationStatistic.id).label("requests"),
            _llm_success_count_expr().label("success"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("cost"),
        )
        .group_by(LLMGenerationStatistic.category)
        .order_by(desc("requests"))
        .all()
    )

    result = []
    for row in rows:
        requests = int(row.requests or 0)
        success = int(row.success or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        result.append(
            {
                "category": row.category or "unknown",
                "requests": requests,
                "success": success,
                "errors": requests - success,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": round(float(row.cost or 0.0), 4),
                "total_tokens": input_tokens + output_tokens,
            }
        )

    return {"categories": result, "available_categories": AVAILABLE_CATEGORIES, "period_days": days}


@llmstats_router.get("/admin/errors")
def get_llm_errors(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    provider: Optional[str] = None,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
):
    """Get recent LLM errors for debugging."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    query = _admin_llm_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff,
        LLMGenerationStatistic.status["error"].astext == "true"
    )
    
    if provider_id:
        query = query.filter(LLMGenerationStatistic.provider_id == provider_id)
    elif provider:
        query = query.filter(LLMGenerationStatistic.provider == provider)
    if model:
        query = query.filter(LLMGenerationStatistic.model_id == model)
    
    total = query.count()
    
    errors = query.order_by(desc(LLMGenerationStatistic.created_at)).offset(
        (page - 1) * per_page
    ).limit(per_page).all()
    
    result = []
    for err in errors:
        created_at = err.created_at
        created_at_iso = None
        if created_at:
            aware_dt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            created_at_iso = aware_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        result.append({
            "id": err.id,
            "model_name": err.model_name,
            "model_id": err.model_id,
            "provider": err.provider,
            "category": err.category,
            "error_type": err.status.get("error_type", "") if err.status else "",
            "error_message": err.status.get("error_message", "") if err.status else "",
            "error_status_code": err.status.get("error_status_code", 0) if err.status else 0,
            "created_at": created_at_iso,
        })
    
    return {
        "errors": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "period_days": days,
    }


@llmstats_router.get("/admin/throughput-by-model")
def get_llm_throughput_by_model(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    provider: Optional[str] = None,
    provider_id: Optional[str] = None,
):
    """Get throughput (tokens per second) statistics grouped by model.
    
    A generation is only counted for throughput if:
    - generation_time > 2 seconds, OR
    - generation_time >= 1 second AND output_tokens > 100
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = _admin_llm_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff,
        LLMGenerationStatistic.status["success"].astext == "true"
    )
    query = _apply_llm_dimension_filters(query, provider=provider, provider_id=provider_id)

    generation_time_expr = _json_float_expr(LLMGenerationStatistic.meta, "generation_time")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    tps_expr = _json_float_expr(LLMGenerationStatistic.meta, "tokens_per_second")
    query = query.filter(
        tps_expr > 0,
        or_(
            generation_time_expr > 2,
            (generation_time_expr >= 1) & (output_tokens_expr > 100),
        ),
    )

    rows = (
        query.with_entities(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
            func.avg(tps_expr).label("avg_throughput"),
            func.min(tps_expr).label("min_throughput"),
            func.max(tps_expr).label("max_throughput"),
            func.count(LLMGenerationStatistic.id).label("sample_count"),
        )
        .group_by(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
        )
        .order_by(desc("avg_throughput"))
        .all()
    )

    provider_name_map = _load_provider_name_map(
        db,
        {row.provider_id for row in rows if row.provider_id},
    )
    model_display_name_map = _load_model_display_name_map(
        db,
        {row.model_id for row in rows if row.model_id},
    )
    result = []
    for row in rows:
        provider_type = row.provider or "unknown"
        result.append(
            {
                "display_name": model_display_name_map.get(row.model_id)
                or row.model_name
                or row.model_id,
                "model_name": row.model_name or row.model_id,
                "model_id": row.model_id,
                "provider": provider_type,
                "provider_id": row.provider_id or None,
                "provider_name": _provider_name_for_row(provider_name_map, row.provider_id, provider_type),
                "avg_throughput": round(float(row.avg_throughput or 0.0), 2),
                "min_throughput": round(float(row.min_throughput or 0.0), 2),
                "max_throughput": round(float(row.max_throughput or 0.0), 2),
                "sample_count": int(row.sample_count or 0),
            }
        )

    return {"models": result, "period_days": days}


@llmstats_router.get("/admin/error-rates-by-model")
def get_llm_error_rates_by_model(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    provider: Optional[str] = None,
    provider_id: Optional[str] = None,
    min_requests: int = Query(default=1, ge=1),
):
    """Get error rate statistics grouped by model."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = _admin_llm_query(db).filter(
        LLMGenerationStatistic.created_at >= cutoff
    )
    query = _apply_llm_dimension_filters(query, provider=provider, provider_id=provider_id)

    rows = (
        query.with_entities(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
            func.count(LLMGenerationStatistic.id).label("total_requests"),
            _llm_success_count_expr().label("success_count"),
        )
        .group_by(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
        )
        .having(func.count(LLMGenerationStatistic.id) >= min_requests)
        .all()
    )

    provider_name_map = _load_provider_name_map(
        db,
        {row.provider_id for row in rows if row.provider_id},
    )
    result = []
    for row in rows:
        total = int(row.total_requests or 0)
        success_count = int(row.success_count or 0)
        error_count = total - success_count
        provider_type = row.provider or "unknown"
        result.append(
            {
                "model_name": row.model_name or row.model_id,
                "model_id": row.model_id,
                "provider": provider_type,
                "provider_id": row.provider_id or None,
                "provider_name": _provider_name_for_row(provider_name_map, row.provider_id, provider_type),
                "total_requests": total,
                "success_count": success_count,
                "error_count": error_count,
                "success_rate": round((success_count / total) * 100, 1) if total else 0,
                "error_rate": round((error_count / total) * 100, 1) if total else 0,
            }
        )

    result.sort(key=lambda x: x["error_rate"], reverse=True)

    return {"models": result, "period_days": days}


@llmstats_router.delete("/admin/all")
def delete_all_llm_stats(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Delete all LLM generation statistics from the database."""
    try:
        count = _admin_llm_query(db, include_user_managed=True).count()
        _admin_llm_query(db, include_user_managed=True).delete()
        db.commit()
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="DELETE_ALL_LLM_GENERATION_STATS",
            details={"deleted_count": count},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="llm_stats",
        )
        return {"deleted_count": count, "success": True}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete statistics") from exc


@llmstats_router.get("/admin/export")
def export_llm_stats(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Export all LLM generation statistics as JSON with versioning."""
    result = export_llm_generation_stats(db, is_byok=False)
    
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_LLM_GENERATION_STATS",
        details={
            "export_version": result.get("export_version"),
            "total_count": result.get("data", {}).get("total_count", 0),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_stats",
    )
    
    return result


@llmstats_router.get("/admin/filters")
def get_llm_stats_filters(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
):
    """Get available filter options."""
    providers = (
        _admin_llm_query(db).with_entities(
            LLMGenerationStatistic.provider_id,
            LLMGenerationStatistic.provider,
        )
        .distinct()
        .all()
    )

    provider_name_map = _load_provider_name_map(
        db,
        {provider_id for provider_id, _provider_type in providers if provider_id},
    )

    def resolve_provider_name(provider_row_id: str | None, provider_type: str | None) -> str:
        if not provider_row_id:
            return provider_type or "unknown"
        return provider_name_map.get(provider_row_id) or provider_type or "unknown"

    provider_list = sorted(
        [
            {
                "provider_id": provider_id,
                "provider": provider_type,
                "provider_name": resolve_provider_name(provider_id, provider_type),
            }
            for provider_id, provider_type in providers
        ],
        key=lambda item: item["provider_name"].lower() if item["provider_name"] else "",
    )

    models = (
        _admin_llm_query(db).with_entities(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider_id,
            LLMGenerationStatistic.provider,
        )
        .distinct()
        .all()
    )

    model_list = []
    for model_id, model_name, provider_id, provider_type in models:
        if not model_id:
            continue
        model_list.append(
            {
                "model_id": model_id,
                "model_name": model_name or model_id,
                "provider": provider_type,
                "provider_id": provider_id,
                "provider_name": resolve_provider_name(provider_id, provider_type),
            }
        )

    model_list.sort(key=lambda x: (x["model_name"] or "").lower())

    return {
        "providers": provider_list,
        "models": model_list,
        "categories": AVAILABLE_CATEGORIES,
    }


# ---------------------------------------------------------------------------
# Tool Call Statistics Routes
# ---------------------------------------------------------------------------
@llmstats_router.get("/admin/tool-calls/overview")
def get_tool_call_stats_overview(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get overview statistics for tool calls."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    row = (
        _admin_tool_query(db)
        .filter(ToolCallStatistic.created_at >= cutoff)
        .with_entities(
            func.count(ToolCallStatistic.id).label("total_calls"),
            func.coalesce(func.sum(case((ToolCallStatistic.success.is_(True), 1), else_=0)), 0).label("success_count"),
            func.coalesce(func.sum(_tool_total_cost_expr()), 0.0).label("total_cost"),
        )
        .one()
    )

    total_calls = int(row.total_calls or 0)
    success_count = int(row.success_count or 0)
    error_count = total_calls - success_count
    total_cost = float(row.total_cost or 0.0)

    return {
        "total_calls": total_calls,
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": round((success_count / total_calls * 100), 1) if total_calls > 0 else 0,
        "estimated_total_cost": round(total_cost, 6),
        "period_days": days,
    }


@llmstats_router.get("/admin/tool-calls/by-tool")
def get_tool_call_stats_by_tool(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics grouped by tool name."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        _admin_tool_query(db)
        .filter(ToolCallStatistic.created_at >= cutoff)
        .with_entities(
            ToolCallStatistic.tool_name,
            func.count(ToolCallStatistic.id).label("total_calls"),
            func.coalesce(func.sum(case((ToolCallStatistic.success.is_(True), 1), else_=0)), 0).label("success_count"),
            func.coalesce(func.sum(_tool_total_cost_expr()), 0.0).label("cost"),
        )
        .group_by(ToolCallStatistic.tool_name)
        .order_by(desc("total_calls"))
        .all()
    )

    result = []
    for row in rows:
        total = int(row.total_calls or 0)
        success_count = int(row.success_count or 0)
        error_count = total - success_count
        result.append(
            {
                "tool_name": row.tool_name or "unknown",
                "total_calls": total,
                "success_count": success_count,
                "error_count": error_count,
                "cost": round(float(row.cost or 0.0), 6),
                "success_rate": round((success_count / total) * 100, 1) if total > 0 else 0,
                "error_rate": round((error_count / total) * 100, 1) if total > 0 else 0,
            }
        )

    return {"tools": result, "period_days": days}


@llmstats_router.get("/admin/tool-calls/errors")
def get_tool_call_errors(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    tool: Optional[str] = None,
):
    """Get recent tool call errors for debugging."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    query = _admin_tool_query(db).filter(
        ToolCallStatistic.created_at >= cutoff,
        ToolCallStatistic.success == False
    )
    
    if tool:
        query = query.filter(ToolCallStatistic.tool_name == tool)
    
    total = query.count()
    
    errors = query.order_by(desc(ToolCallStatistic.created_at)).offset(
        (page - 1) * per_page
    ).limit(per_page).all()
    
    result = []
    for err in errors:
        created_at = err.created_at
        created_at_iso = None
        if created_at:
            aware_dt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            created_at_iso = aware_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        result.append({
            "id": err.id,
            "tool_name": err.tool_name,
            "error_message": err.error_message,
            "model_name": err.model_name,
            "model_id": err.model_id,
            "provider": err.provider,
            "meta": err.meta if isinstance(err.meta, dict) else {},
            "created_at": created_at_iso,
        })
    
    return {
        "errors": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
        "period_days": days,
    }


@llmstats_router.get("/admin/tool-calls/filters")
def get_tool_call_filters(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
):
    """Get available filter options for tool calls."""
    tools = _admin_tool_query(db).with_entities(ToolCallStatistic.tool_name).distinct().all()
    tool_list = sorted([t[0] for t in tools if t[0]])
    
    return {
        "tools": tool_list,
    }


# ---------------------------------------------------------------------------
# User BYOK Statistics
# ---------------------------------------------------------------------------
@llmstats_router.get("/user/byok/settings")
def get_user_byok_stats_settings(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    enabled = coerce_bool(get_user_setting_value(user.id, "chat", "byok_statistics_enabled", db))
    retention_days = coerce_byok_stats_retention_days(
        get_user_setting_value(user.id, "chat", "byok_statistics_retention_days", db)
    )
    return {"enabled": enabled, "retention_days": retention_days}


@llmstats_router.post("/user/byok/settings")
def update_user_byok_stats_settings(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    if not isinstance(payload, dict) or "enabled" not in payload:
        raise HTTPException(status_code=400, detail="Missing required field 'enabled'.")

    enabled = coerce_bool(payload.get("enabled"))
    if "retention_days" in payload:
        retention_days = coerce_byok_stats_retention_days(payload.get("retention_days"))
    else:
        retention_days = coerce_byok_stats_retention_days(
            get_user_setting_value(user.id, "chat", "byok_statistics_retention_days", db)
        )
    regulatory_confirmed = coerce_bool(payload.get("regulatory_confirmed"))
    if enabled and not regulatory_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Regulatory confirmation is required to enable BYOK statistics.",
        )

    update_user_settings(user.id, "chat", "byok_statistics_enabled", enabled, db)
    update_user_settings(user.id, "chat", "byok_statistics_retention_days", retention_days, db)
    invalidate_user_statistics_cache()
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="BYOK_STATS_SETTINGS_UPDATED",
        details={"enabled": enabled, "retention_days": retention_days},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_stats",
    )
    return {"success": True, "enabled": enabled, "retention_days": retention_days}


@llmstats_router.get("/user/byok/overview")
def get_user_byok_stats_overview(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    llm_base_query = _user_byok_llm_query(db, user.id).filter(LLMGenerationStatistic.created_at >= cutoff)
    tool_base_query = _user_byok_tool_query(db, user.id).filter(ToolCallStatistic.created_at >= cutoff)

    input_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["input_tokens"].astext, Integer), 0)
    output_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["output_tokens"].astext, Integer), 0)
    reasoning_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["reasoning_tokens"].astext, Integer), 0)
    total_cost_expr = _llm_total_cost_expr()

    llm_row = llm_base_query.with_entities(
        func.count(LLMGenerationStatistic.id),
        func.coalesce(
            func.sum(case((LLMGenerationStatistic.status["success"].astext == "true", 1), else_=0)),
            0,
        ),
        func.coalesce(func.sum(input_tokens_expr), 0),
        func.coalesce(func.sum(output_tokens_expr), 0),
        func.coalesce(func.sum(reasoning_tokens_expr), 0),
        func.coalesce(func.sum(total_cost_expr), 0.0),
    ).one()

    tool_row = tool_base_query.with_entities(
        func.count(ToolCallStatistic.id),
        func.coalesce(func.sum(case((ToolCallStatistic.success.is_(True), 1), else_=0)), 0),
    ).one()

    total_requests = int(llm_row[0] or 0)
    success_count = int(llm_row[1] or 0)
    error_count = total_requests - success_count
    total_input_tokens = int(llm_row[2] or 0)
    total_output_tokens = int(llm_row[3] or 0)
    total_reasoning_tokens = int(llm_row[4] or 0)
    total_cost = float(llm_row[5] or 0.0)

    total_tool_calls = int(tool_row[0] or 0)
    tool_success_count = int(tool_row[1] or 0)
    tool_error_count = total_tool_calls - tool_success_count

    return {
        "period_days": days,
        "total_requests": total_requests,
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": round((success_count / total_requests * 100), 1) if total_requests > 0 else 0,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_reasoning_tokens": total_reasoning_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "estimated_total_cost": round(total_cost, 6),
        "tool_calls": {
            "total_calls": total_tool_calls,
            "success_count": tool_success_count,
            "error_count": tool_error_count,
            "success_rate": round((tool_success_count / total_tool_calls * 100), 1) if total_tool_calls > 0 else 0,
        },
    }


@llmstats_router.get("/user/byok/by-provider")
def get_user_byok_stats_by_provider(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    rows = (
        _user_byok_llm_query(db, user.id)
        .filter(LLMGenerationStatistic.created_at >= cutoff)
        .with_entities(
            LLMGenerationStatistic.provider_id,
            LLMGenerationStatistic.provider,
            func.count(LLMGenerationStatistic.id).label("requests"),
            _llm_success_count_expr().label("success"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("cost"),
        )
        .group_by(LLMGenerationStatistic.provider_id, LLMGenerationStatistic.provider)
        .order_by(desc("requests"))
        .all()
    )

    provider_name_map = _load_provider_name_map(
        db,
        {row.provider_id for row in rows if row.provider_id},
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        requests = int(row.requests or 0)
        success = int(row.success or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        provider_type = row.provider or "unknown"
        result.append(
            {
                "provider_id": row.provider_id or None,
                "provider": provider_type,
                "provider_name": _provider_name_for_row(provider_name_map, row.provider_id, provider_type),
                "requests": requests,
                "success": success,
                "errors": requests - success,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": round(float(row.cost or 0.0), 4),
                "total_tokens": input_tokens + output_tokens,
            }
        )
    return {"providers": result, "period_days": days}


@llmstats_router.get("/user/byok/by-model")
def get_user_byok_stats_by_model(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    generation_time_expr = _json_float_expr(LLMGenerationStatistic.meta, "generation_time")
    rows = (
        _user_byok_llm_query(db, user.id)
        .filter(LLMGenerationStatistic.created_at >= cutoff)
        .with_entities(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
            func.count(LLMGenerationStatistic.id).label("requests"),
            _llm_success_count_expr().label("success"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("cost"),
            func.avg(case((generation_time_expr > 0, generation_time_expr))).label("avg_generation_time"),
        )
        .group_by(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
        )
        .order_by(desc("requests"))
        .all()
    )

    provider_name_map = _load_provider_name_map(
        db,
        {row.provider_id for row in rows if row.provider_id},
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        requests = int(row.requests or 0)
        success = int(row.success or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        provider_type = row.provider or "unknown"
        result.append(
            {
                "model_name": row.model_name or row.model_id,
                "model_id": row.model_id,
                "provider": provider_type,
                "provider_id": row.provider_id or None,
                "provider_name": _provider_name_for_row(provider_name_map, row.provider_id, provider_type),
                "requests": requests,
                "success": success,
                "errors": requests - success,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": round(float(row.cost or 0.0), 4),
                "avg_generation_time": round(float(row.avg_generation_time or 0.0), 2),
                "total_tokens": input_tokens + output_tokens,
            }
        )
    return {"models": result, "period_days": days}


@llmstats_router.get("/user/byok/errors")
def get_user_byok_errors(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = _user_byok_llm_query(db, user.id).filter(
        LLMGenerationStatistic.created_at >= cutoff,
        LLMGenerationStatistic.status["error"].astext == "true",
    )
    total = query.count()
    errors = query.order_by(desc(LLMGenerationStatistic.created_at)).offset((page - 1) * per_page).limit(per_page).all()

    result = []
    for err in errors:
        created_at_iso = None
        if err.created_at:
            aware_dt = err.created_at if err.created_at.tzinfo else err.created_at.replace(tzinfo=timezone.utc)
            created_at_iso = aware_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        result.append(
            {
                "id": err.id,
                "model_name": err.model_name,
                "model_id": err.model_id,
                "provider": err.provider,
                "category": err.category,
                "error_type": err.status.get("error_type", "") if err.status else "",
                "error_message": sanitize_provider_error_message(
                    err.status.get("error_message", "") if err.status else ""
                ),
                "error_status_code": err.status.get("error_status_code", 0) if err.status else 0,
                "created_at": created_at_iso,
            }
        )

    return {
        "errors": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "period_days": days,
    }


@llmstats_router.get("/user/byok/tool-calls/overview")
def get_user_byok_tool_calls_overview(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    row = (
        _user_byok_tool_query(db, user.id)
        .filter(ToolCallStatistic.created_at >= cutoff)
        .with_entities(
            func.count(ToolCallStatistic.id).label("total_calls"),
            func.coalesce(func.sum(case((ToolCallStatistic.success.is_(True), 1), else_=0)), 0).label("success_count"),
            func.coalesce(func.sum(_tool_total_cost_expr()), 0.0).label("total_cost"),
        )
        .one()
    )

    total_calls = int(row.total_calls or 0)
    success_count = int(row.success_count or 0)
    error_count = total_calls - success_count
    total_cost = float(row.total_cost or 0.0)

    return {
        "total_calls": total_calls,
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": round((success_count / total_calls * 100), 1) if total_calls > 0 else 0,
        "estimated_total_cost": round(total_cost, 6),
        "period_days": days,
    }


@llmstats_router.get("/user/byok/tool-calls/by-tool")
def get_user_byok_tool_calls_by_tool(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
    days: int = Query(default=30, ge=1, le=365),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        _user_byok_tool_query(db, user.id)
        .filter(ToolCallStatistic.created_at >= cutoff)
        .with_entities(
            ToolCallStatistic.tool_name,
            func.count(ToolCallStatistic.id).label("total_calls"),
            func.coalesce(func.sum(case((ToolCallStatistic.success.is_(True), 1), else_=0)), 0).label("success_count"),
            func.coalesce(func.sum(_tool_total_cost_expr()), 0.0).label("cost"),
        )
        .group_by(ToolCallStatistic.tool_name)
        .order_by(desc("total_calls"))
        .all()
    )

    result: list[dict[str, Any]] = []
    for row in rows:
        total = int(row.total_calls or 0)
        success_count = int(row.success_count or 0)
        error_count = total - success_count
        result.append(
            {
                "tool_name": row.tool_name or "unknown",
                "total_calls": total,
                "success_count": success_count,
                "error_count": error_count,
                "cost": round(float(row.cost or 0.0), 6),
                "success_rate": round((success_count / total) * 100, 1) if total > 0 else 0,
                "error_rate": round((error_count / total) * 100, 1) if total > 0 else 0,
            }
        )
    return {"tools": result, "period_days": days}


@llmstats_router.get("/user/byok/export")
def export_user_byok_stats(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    llm_export = export_llm_generation_stats(db, user_id=user.id, is_byok=True)
    tool_export = export_tool_call_stats(db, user_id=user.id, is_byok=True)
    response = {
        "export_type": "byok_usage_stats",
        "export_version": BYOK_USAGE_STATS_EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "llm_generation_stats": llm_export,
            "tool_call_stats": tool_export,
        },
    }
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="EXPORT_BYOK_USAGE_STATS",
        details={
            "export_version": BYOK_USAGE_STATS_EXPORT_VERSION,
            "llm_stats_count": int(
                (llm_export.get("data") or {}).get("total_count") or 0
            ),
            "tool_stats_count": int(
                (tool_export.get("data") or {}).get("total_count") or 0
            ),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_stats",
    )
    return response


@llmstats_router.delete("/user/byok/all")
def delete_user_byok_stats(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    try:
        llm_deleted = _user_byok_llm_query(db, user.id).delete()
        tool_deleted = _user_byok_tool_query(db, user.id).delete()
        db.commit()
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="BYOK_STATS_DELETED",
            details={"llm_stats": llm_deleted, "tool_stats": tool_deleted},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="llm_stats",
        )
        return {
            "success": True,
            "deleted": {
                "llm_stats": llm_deleted,
                "tool_stats": tool_deleted,
            },
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete BYOK statistics.") from exc


# ---------------------------------------------------------------------------
# User-Based Statistics Settings
# ---------------------------------------------------------------------------
def _get_user_display_name(user_obj):
    """Return best-effort display name for a user."""
    if not user_obj:
        return "Unknown"
    parts = [getattr(user_obj, "first_name", ""), getattr(user_obj, "last_name", "")]
    full_name = " ".join(part for part in parts if part).strip()
    if full_name:
        return full_name
    return getattr(user_obj, "email", "Unknown")


@llmstats_router.get("/admin/user-statistics/settings")
def get_user_statistics_settings(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
):
    """Get user-based statistics settings."""
    from app.settings.models import get_settings_page
    from app.users.models import User
    
    settings_page = get_settings_page(db, "user_statistics")
    if not settings_page or not isinstance(settings_page.data, dict):
        return {
            "enabled": False,
            "regulatory_confirmed": False,
            "tracked_user_ids": [],
            "track_all_users": False,
            "tracked_users": [],
        }
    
    data = settings_page.data
    tracked_user_ids = data.get("tracked_user_ids", []) or []
    
    # Resolve user details for tracked users
    tracked_users = []
    if tracked_user_ids:
        users = db.query(User).filter(User.id.in_(tracked_user_ids)).all()
        for user in users:
            tracked_users.append({
                "id": user.id,
                "email": user.email,
                "name": _get_user_display_name(user),
            })
    
    return {
        "enabled": data.get("enabled", False),
        "regulatory_confirmed": data.get("regulatory_confirmed", False),
        "tracked_user_ids": tracked_user_ids,
        "track_all_users": data.get("track_all_users", False),
        "tracked_users": tracked_users,
    }


@llmstats_router.post("/admin/user-statistics/settings")
def update_user_statistics_settings(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update user-based statistics settings."""
    from app.settings.models import get_settings_page
    from sqlalchemy.orm.attributes import flag_modified
    
    settings_page = get_settings_page(db, "user_statistics")
    if not settings_page:
        raise HTTPException(status_code=404, detail="User statistics settings page not found")
    
    if not isinstance(settings_page.data, dict):
        settings_page.data = {}
    
    # Validate and update settings
    allowed_keys = {"enabled", "regulatory_confirmed", "tracked_user_ids", "track_all_users"}
    updates = {}
    
    for key in allowed_keys:
        if key in payload:
            value = payload[key]
            if key == "tracked_user_ids":
                if not isinstance(value, list):
                    raise HTTPException(status_code=400, detail="tracked_user_ids must be a list")
                value = [str(uid) for uid in value if uid]
            elif key in ("enabled", "regulatory_confirmed", "track_all_users"):
                value = bool(value)
            settings_page.data[key] = value
            updates[key] = value
    
    settings_page.updated_at = datetime.now(timezone.utc)
    flag_modified(settings_page, "data")
    db.commit()
    db.refresh(settings_page)
    
    # Invalidate the cache
    invalidate_user_statistics_cache()
    
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_USER_STATISTICS_SETTINGS",
        details={"updates": updates},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user_stats",
    )
    
    return {"success": True, "settings": settings_page.data}


@llmstats_router.post("/admin/user-statistics/add-user")
def add_user_to_tracking(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Add a user to statistics tracking."""
    from app.settings.models import get_settings_page
    from sqlalchemy.orm.attributes import flag_modified
    
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    
    settings_page = get_settings_page(db, "user_statistics")
    if not settings_page:
        raise HTTPException(status_code=404, detail="User statistics settings page not found")
    
    if not isinstance(settings_page.data, dict):
        settings_page.data = {"tracked_user_ids": []}
    
    tracked_user_ids = settings_page.data.get("tracked_user_ids", []) or []
    if not isinstance(tracked_user_ids, list):
        tracked_user_ids = []
    
    if user_id not in tracked_user_ids:
        tracked_user_ids.append(user_id)
        settings_page.data["tracked_user_ids"] = tracked_user_ids
        settings_page.updated_at = datetime.now(timezone.utc)
        flag_modified(settings_page, "data")
        db.commit()
        invalidate_user_statistics_cache()
        
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="ADD_USER_TO_STATISTICS_TRACKING",
            details={"tracked_user_id": user_id},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="user_stats",
        )
    
    return {"success": True, "tracked_user_ids": tracked_user_ids}


@llmstats_router.post("/admin/user-statistics/remove-user")
def remove_user_from_tracking(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Remove a user from statistics tracking."""
    from app.settings.models import get_settings_page
    from sqlalchemy.orm.attributes import flag_modified
    
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    
    settings_page = get_settings_page(db, "user_statistics")
    if not settings_page:
        raise HTTPException(status_code=404, detail="User statistics settings page not found")
    
    if not isinstance(settings_page.data, dict):
        return {"success": True, "tracked_user_ids": []}
    
    tracked_user_ids = settings_page.data.get("tracked_user_ids", []) or []
    if not isinstance(tracked_user_ids, list):
        tracked_user_ids = []
    
    if user_id in tracked_user_ids:
        tracked_user_ids.remove(user_id)
        settings_page.data["tracked_user_ids"] = tracked_user_ids
        settings_page.updated_at = datetime.now(timezone.utc)
        flag_modified(settings_page, "data")
        db.commit()
        invalidate_user_statistics_cache()
        
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="REMOVE_USER_FROM_STATISTICS_TRACKING",
            details={"removed_user_id": user_id},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="user_stats",
        )
    
    return {"success": True, "tracked_user_ids": tracked_user_ids}



@llmstats_router.get("/admin/user-statistics/overview/{user_id}")
def get_user_statistics_overview(
    user_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics overview for a specific user."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    llm_row = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id == user_id,
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            func.count(LLMGenerationStatistic.id).label("total_requests"),
            _llm_success_count_expr().label("success_count"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("total_cost"),
        )
        .one()
    )

    total_llm_requests = int(llm_row.total_requests or 0)
    llm_success_count = int(llm_row.success_count or 0)
    llm_error_count = total_llm_requests - llm_success_count
    total_input_tokens = int(llm_row.input_tokens or 0)
    total_output_tokens = int(llm_row.output_tokens or 0)
    total_cost = float(llm_row.total_cost or 0.0)

    tool_row = (
        _admin_tool_query(db)
        .filter(
            ToolCallStatistic.user_id == user_id,
            ToolCallStatistic.created_at >= cutoff,
        )
        .with_entities(
            func.count(ToolCallStatistic.id).label("total_calls"),
            func.coalesce(func.sum(case((ToolCallStatistic.success.is_(True), 1), else_=0)), 0).label("success_count"),
        )
        .one()
    )

    total_tool_calls = int(tool_row.total_calls or 0)
    tool_success_count = int(tool_row.success_count or 0)
    tool_error_count = total_tool_calls - tool_success_count

    return {
        "user_id": user_id,
        "period_days": days,
        "llm_stats": {
            "total_requests": total_llm_requests,
            "success_count": llm_success_count,
            "error_count": llm_error_count,
            "success_rate": round((llm_success_count / total_llm_requests * 100), 1) if total_llm_requests > 0 else 0,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "estimated_total_cost": round(total_cost, 6),
        },
        "tool_stats": {
            "total_calls": total_tool_calls,
            "success_count": tool_success_count,
            "error_count": tool_error_count,
            "success_rate": round((tool_success_count / total_tool_calls * 100), 1) if total_tool_calls > 0 else 0,
        },
    }


@llmstats_router.get("/admin/user-statistics/tracked-users-overview")
def get_tracked_users_statistics_overview(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Get statistics overview for all tracked users."""
    from app.settings.models import get_settings_page
    from app.users.models import User
    
    settings_page = get_settings_page(db, "user_statistics")
    if not settings_page or not isinstance(settings_page.data, dict):
        data = {
            "tracked_user_ids": [],
            "track_all_users": False,
        }
    else:
        data = settings_page.data
    logger.info(
        "tracked-users-overview settings loaded",
        extra={
            "has_page": bool(settings_page),
            "tracked_ids_count": len(data.get("tracked_user_ids", []) or []),
            "track_all_users": data.get("track_all_users", False),
            "days": days,
            "limit": limit,
            "offset": offset,
        },
    )
    tracked_user_ids = data.get("tracked_user_ids", []) or []
    track_all_users = data.get("track_all_users", False)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Build list of user IDs we should report on. Always respect explicit tracking order first.
    ordered_user_ids: list[str] = []
    seen_ids: set[str] = set()

    for user_id in tracked_user_ids:
        if user_id and user_id not in seen_ids:
            ordered_user_ids.append(user_id)
            seen_ids.add(user_id)
    logger.info(
        "tracked-users-overview initial ordered ids",
        extra={
            "ordered_count": len(ordered_user_ids),
            "ordered_ids": ordered_user_ids,
        },
    )

    # If "track all" is enabled or no explicit IDs exist, include every user that has
    # statistics within the requested timeframe. This covers legacy data and ensures
    # admins see activity even if the tracked list was cleared.
    if track_all_users or not ordered_user_ids:
        logger.info(
            "tracked-users-overview expanding ids",
            extra={
                "track_all_users": track_all_users,
                "reason": "track_all" if track_all_users else "no_tracked_ids",
                "cutoff": cutoff.isoformat(),
            },
        )
        llm_user_rows = (
            _admin_llm_query(db).with_entities(LLMGenerationStatistic.user_id)
            .filter(
                LLMGenerationStatistic.user_id.isnot(None),
                LLMGenerationStatistic.created_at >= cutoff,
            )
            .distinct()
            .all()
        )
        tool_user_rows = (
            _admin_tool_query(db).with_entities(ToolCallStatistic.user_id)
            .filter(
                ToolCallStatistic.user_id.isnot(None),
                ToolCallStatistic.created_at >= cutoff,
            )
            .distinct()
            .all()
        )

        for row in (*llm_user_rows, *tool_user_rows):
            user_id = row[0]
            if user_id and user_id not in seen_ids:
                ordered_user_ids.append(user_id)
                seen_ids.add(user_id)
        logger.info(
            "tracked-users-overview expanded ids",
            extra={
                "ordered_count": len(ordered_user_ids),
                "ordered_ids": ordered_user_ids,
            },
        )

    if not ordered_user_ids:
        logger.warning(
            "tracked-users-overview returning empty (no ordered ids)",
            extra={
                "track_all_users": track_all_users,
                "tracked_ids_count": len(tracked_user_ids),
            },
        )
        return {"users": [], "period_days": days}

    users = db.query(User).filter(User.id.in_(ordered_user_ids)).all()
    logger.info(
        "tracked-users-overview loaded user rows",
        extra={
            "requested_ids": ordered_user_ids,
            "db_user_count": len(users),
        },
    )
    user_map = {
        u.id: {
            "id": u.id,
            "email": u.email,
            "name": _get_user_display_name(u),
        }
        for u in users
    }

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    total_cost_expr = _llm_total_cost_expr()
    llm_rows = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id.in_(ordered_user_ids),
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            LLMGenerationStatistic.user_id,
            func.count(LLMGenerationStatistic.id).label("requests"),
            func.coalesce(func.sum(input_tokens_expr + output_tokens_expr), 0).label("tokens"),
            func.coalesce(func.sum(total_cost_expr), 0.0).label("cost"),
        )
        .group_by(LLMGenerationStatistic.user_id)
        .all()
    )
    llm_by_user = {
        row.user_id: {
            "requests": int(row.requests or 0),
            "tokens": int(row.tokens or 0),
            "cost": float(row.cost or 0.0),
        }
        for row in llm_rows
    }
    tool_rows = (
        _admin_tool_query(db)
        .filter(
            ToolCallStatistic.user_id.in_(ordered_user_ids),
            ToolCallStatistic.created_at >= cutoff,
        )
        .with_entities(ToolCallStatistic.user_id, func.count(ToolCallStatistic.id).label("calls"))
        .group_by(ToolCallStatistic.user_id)
        .all()
    )
    tool_by_user = {row.user_id: int(row.calls or 0) for row in tool_rows}

    results = []
    for user_id in ordered_user_ids:
        user_info = user_map.get(user_id, {"id": user_id, "email": "Unknown", "name": None})
        llm = llm_by_user.get(user_id, {"requests": 0, "tokens": 0, "cost": 0.0})
        total_llm_requests = llm["requests"]
        total_cost = llm["cost"]
        total_tokens = llm["tokens"]
        tool_count = tool_by_user.get(user_id, 0)
        logger.info(
            "tracked-users-overview per-user stats",
            extra={
                "user_id": user_id,
                "llm_requests": total_llm_requests,
                "tool_calls": tool_count,
                "estimated_cost": total_cost,
                "total_tokens": total_tokens,
            },
        )
        
        results.append({
            "user": user_info,
            "llm_requests": total_llm_requests,
            "tool_calls": tool_count,
            "total_tokens": total_tokens,
            "estimated_cost": round(total_cost, 6),
        })
    
    results.sort(key=lambda x: x["estimated_cost"], reverse=True)
    total = len(results)
    results = results[offset:offset + limit]
    logger.info(
        "tracked-users-overview final payload",
        extra={
            "total_users": total,
            "returned_users": len(results),
            "limit": limit,
            "offset": offset,
            "period_days": days,
        },
    )
    
    return {"users": results, "period_days": days, "total": total, "limit": limit, "offset": offset}


@llmstats_router.get("/admin/user-statistics/{user_id}/timeline")
def get_user_statistics_timeline(
    user_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    granularity: str = Query(default="daily", pattern="^(hourly|daily|weekly|monthly)$"),
):
    """Get timeline data for a specific user."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    bucket_expr = _timeline_bucket_expr(db, LLMGenerationStatistic.created_at, granularity)
    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    rows = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id == user_id,
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            bucket_expr.label("bucket_start"),
            func.count(LLMGenerationStatistic.id).label("requests"),
            _llm_success_count_expr().label("success"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("cost"),
        )
        .group_by(bucket_expr)
        .order_by(bucket_expr)
        .all()
    )

    timeline = []
    for row in rows:
        period, bucket_start_iso = _timeline_period_from_bucket(row.bucket_start, granularity)
        requests = int(row.requests or 0)
        success = int(row.success or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        timeline.append({
            "period": period,
            "bucket_start": bucket_start_iso,
            "requests": requests,
            "success": success,
            "errors": requests - success,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": round(float(row.cost or 0.0), 4),
        })

    return {"timeline": timeline, "granularity": granularity, "period_days": days, "user_id": user_id}


@llmstats_router.get("/admin/user-statistics/{user_id}/by-model")
def get_user_statistics_by_model(
    user_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics grouped by model for a specific user."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    rows = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id == user_id,
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
            func.count(LLMGenerationStatistic.id).label("total_requests"),
            _llm_success_count_expr().label("success_count"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("total_cost"),
        )
        .group_by(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
        )
        .order_by(desc("total_requests"))
        .all()
    )

    provider_ids = {row.provider_id for row in rows if row.provider_id}
    provider_name_map = _load_provider_name_map(db, provider_ids)

    models = []
    for row in rows:
        total = int(row.total_requests or 0)
        success_count = int(row.success_count or 0)
        error_count = total - success_count
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        models.append({
            "model_id": row.model_id,
            "model_name": row.model_name or row.model_id or "unknown",
            "provider": row.provider,
            "provider_id": row.provider_id,
            "provider_name": provider_name_map.get(row.provider_id, row.provider),
            "total_requests": total,
            "success_count": success_count,
            "error_count": error_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "total_cost": round(float(row.total_cost or 0.0), 6),
            "success_rate": round(success_count / total * 100, 1) if total > 0 else 0,
            "error_rate": round(error_count / total * 100, 1) if total > 0 else 0,
        })

    return {"models": models, "period_days": days, "user_id": user_id}


@llmstats_router.get("/admin/user-statistics/{user_id}/by-provider")
def get_user_statistics_by_provider(
    user_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics grouped by provider for a specific user."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    rows = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id == user_id,
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            LLMGenerationStatistic.provider_id,
            LLMGenerationStatistic.provider,
            func.count(LLMGenerationStatistic.id).label("total_requests"),
            _llm_success_count_expr().label("success_count"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("total_cost"),
        )
        .group_by(LLMGenerationStatistic.provider_id, LLMGenerationStatistic.provider)
        .order_by(desc("total_requests"))
        .all()
    )

    provider_ids = {row.provider_id for row in rows if row.provider_id}
    provider_name_map = _load_provider_name_map(db, provider_ids)

    providers = []
    for row in rows:
        total = int(row.total_requests or 0)
        success_count = int(row.success_count or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        providers.append({
            "provider": row.provider,
            "provider_id": row.provider_id,
            "provider_name": provider_name_map.get(row.provider_id, row.provider),
            "total_requests": total,
            "success_count": success_count,
            "error_count": total - success_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "total_cost": round(float(row.total_cost or 0.0), 6),
            "success_rate": round(success_count / total * 100, 1) if total > 0 else 0,
        })

    return {"providers": providers, "period_days": days, "user_id": user_id}


@llmstats_router.get("/admin/user-statistics/{user_id}/by-category")
def get_user_statistics_by_category(
    user_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics grouped by category for a specific user."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    rows = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id == user_id,
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            LLMGenerationStatistic.category,
            func.count(LLMGenerationStatistic.id).label("total_requests"),
            _llm_success_count_expr().label("success_count"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("total_cost"),
        )
        .group_by(LLMGenerationStatistic.category)
        .order_by(desc("total_requests"))
        .all()
    )

    categories = []
    for row in rows:
        total = int(row.total_requests or 0)
        success_count = int(row.success_count or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        categories.append({
            "category": row.category or "unknown",
            "total_requests": total,
            "success_count": success_count,
            "error_count": total - success_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "total_cost": round(float(row.total_cost or 0.0), 6),
            "success_rate": round(success_count / total * 100, 1) if total > 0 else 0,
        })

    return {"categories": categories, "period_days": days, "user_id": user_id}


@llmstats_router.get("/admin/user-statistics/{user_id}/errors")
def get_user_statistics_errors(
    user_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=100),
):
    """Get error details for a specific user."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    query = _admin_llm_query(db).filter(
        LLMGenerationStatistic.user_id == user_id,
        LLMGenerationStatistic.created_at >= cutoff,
        LLMGenerationStatistic.status["success"].astext != "true"
    ).order_by(LLMGenerationStatistic.created_at.desc())
    
    total = query.count()
    errors_raw = query.offset((page - 1) * per_page).limit(per_page).all()
    
    # Load provider names
    provider_ids = {e.provider_id for e in errors_raw if e.provider_id}
    provider_name_map = _load_provider_name_map(db, provider_ids)
    
    errors = []
    for err in errors_raw:
        errors.append({
            "id": err.id,
            "model_name": err.model_name,
            "model_id": err.model_id,
            "provider": err.provider,
            "provider_id": err.provider_id,
            "provider_name": provider_name_map.get(err.provider_id, err.provider),
            "category": err.category,
            "error_message": err.status.get("error_message") if err.status else None,
            "error_type": err.status.get("error_type") if err.status else None,
            "error_status_code": err.status.get("error_status_code") if err.status else None,
            "created_at": err.created_at.isoformat() if err.created_at else None,
        })
    
    return {
        "errors": errors,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
        "period_days": days,
        "user_id": user_id,
    }


@llmstats_router.get("/admin/user-statistics/{user_id}/tool-calls")
def get_user_statistics_tool_calls(
    user_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get tool call statistics for a specific user."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        _admin_tool_query(db)
        .filter(
            ToolCallStatistic.user_id == user_id,
            ToolCallStatistic.created_at >= cutoff,
        )
        .with_entities(
            ToolCallStatistic.tool_name,
            func.count(ToolCallStatistic.id).label("total_calls"),
            func.coalesce(func.sum(case((ToolCallStatistic.success.is_(True), 1), else_=0)), 0).label("success_count"),
            func.coalesce(func.sum(_tool_total_cost_expr()), 0.0).label("cost"),
        )
        .group_by(ToolCallStatistic.tool_name)
        .order_by(desc("total_calls"))
        .all()
    )

    tools = []
    total_calls = 0
    total_success = 0
    total_errors = 0
    total_cost = 0

    for row in rows:
        total = int(row.total_calls or 0)
        success_count = int(row.success_count or 0)
        error_count = total - success_count
        cost = float(row.cost or 0.0)
        total_calls += total
        total_success += success_count
        total_errors += error_count
        total_cost += cost

        tools.append({
            "tool_name": row.tool_name or "unknown",
            "total_calls": total,
            "success_count": success_count,
            "error_count": error_count,
            "cost": round(cost, 6),
            "success_rate": round(success_count / total * 100, 1) if total > 0 else 0,
            "error_rate": round(error_count / total * 100, 1) if total > 0 else 0,
        })

    return {
        "tools": tools,
        "overview": {
            "total_calls": total_calls,
            "success_count": total_success,
            "error_count": total_errors,
            "success_rate": round(total_success / total_calls * 100, 1) if total_calls > 0 else 0,
            "estimated_total_cost": round(total_cost, 6),
        },
        "period_days": days,
        "user_id": user_id,
    }


@llmstats_router.get("/admin/group-statistics/overview")
def get_group_statistics_overview(
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics overview for all groups."""
    from app.groups.models import Group
    from app.users.models import User
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    # Get all groups
    groups = db.query(Group).all()
    
    # Count users per group in a single query
    user_counts = dict(
        db.query(User.group_id, func.count(User.id))
        .group_by(User.group_id)
        .all()
    )
    
    # Aggregate LLM stats per group using SQL GROUP BY
    input_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["input_tokens"].astext, Integer), 0)
    output_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["output_tokens"].astext, Integer), 0)
    total_costs_expr = _llm_total_cost_expr()
    
    llm_rows = (
        db.query(
            User.group_id,
            func.count(LLMGenerationStatistic.id),
            func.coalesce(func.sum(input_tokens_expr + output_tokens_expr), 0),
            func.coalesce(func.sum(total_costs_expr), 0.0),
        )
        .join(User, User.id == LLMGenerationStatistic.user_id)
        .filter(
            LLMGenerationStatistic.is_byok.is_(False),
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .group_by(User.group_id)
        .all()
    )
    llm_by_group = {gid: {"requests": cnt, "tokens": tok, "cost": cost} for gid, cnt, tok, cost in llm_rows}
    
    # Aggregate tool stats per group using SQL GROUP BY
    tool_rows = (
        db.query(User.group_id, func.count(ToolCallStatistic.id))
        .join(User, User.id == ToolCallStatistic.user_id)
        .filter(
            ToolCallStatistic.is_byok.is_(False),
            ToolCallStatistic.created_at >= cutoff,
        )
        .group_by(User.group_id)
        .all()
    )
    tool_by_group = dict(tool_rows)
    
    results = []
    for group in groups:
        gid = group.id
        llm = llm_by_group.get(gid, {"requests": 0, "tokens": 0, "cost": 0})
        results.append({
            "group": {
                "id": gid,
                "name": group.name,
            },
            "user_count": user_counts.get(gid, 0),
            "llm_requests": llm["requests"],
            "tool_calls": tool_by_group.get(gid, 0),
            "total_tokens": llm["tokens"],
            "estimated_cost": round(llm["cost"], 6),
        })
    
    results.sort(key=lambda x: x["estimated_cost"], reverse=True)
    
    return {"groups": results, "period_days": days}


@llmstats_router.get("/admin/group-statistics/{group_id}/overview")
def get_group_statistics_detail(
    group_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get detailed statistics for a specific group."""
    from app.groups.models import Group
    from app.users.models import User
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    users_in_group = db.query(User).filter(User.group_id == group_id).all()
    user_ids = [u.id for u in users_in_group]
    
    if not user_ids:
        return {
            "group": {"id": group.id, "name": group.name},
            "user_count": 0,
            "llm_stats": {
                "total_requests": 0,
                "success_count": 0,
                "error_count": 0,
                "success_rate": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "estimated_total_cost": 0,
            },
            "tool_stats": {
                "total_calls": 0,
                "success_count": 0,
                "error_count": 0,
                "success_rate": 0,
            },
            "period_days": days,
        }
    
    input_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["input_tokens"].astext, Integer), 0)
    output_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["output_tokens"].astext, Integer), 0)
    total_costs_expr = _llm_total_cost_expr()
    
    llm_row = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id.in_(user_ids),
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            func.count(LLMGenerationStatistic.id),
            func.coalesce(func.sum(case((LLMGenerationStatistic.status["success"].astext == "true", 1), else_=0)), 0),
            func.coalesce(func.sum(input_tokens_expr), 0),
            func.coalesce(func.sum(output_tokens_expr), 0),
            func.coalesce(func.sum(total_costs_expr), 0.0),
        )
        .one()
    )
    
    total_llm_requests = int(llm_row[0] or 0)
    llm_success_count = int(llm_row[1] or 0)
    llm_error_count = total_llm_requests - llm_success_count
    total_input_tokens = int(llm_row[2] or 0)
    total_output_tokens = int(llm_row[3] or 0)
    total_cost = float(llm_row[4] or 0.0)
    
    tool_row = (
        _admin_tool_query(db)
        .filter(
            ToolCallStatistic.user_id.in_(user_ids),
            ToolCallStatistic.created_at >= cutoff,
        )
        .with_entities(
            func.count(ToolCallStatistic.id),
            func.coalesce(func.sum(case((ToolCallStatistic.success.is_(True), 1), else_=0)), 0),
        )
        .one()
    )
    
    total_tool_calls = int(tool_row[0] or 0)
    tool_success_count = int(tool_row[1] or 0)
    tool_error_count = total_tool_calls - tool_success_count
    
    return {
        "group": {"id": group.id, "name": group.name},
        "user_count": len(user_ids),
        "llm_stats": {
            "total_requests": total_llm_requests,
            "success_count": llm_success_count,
            "error_count": llm_error_count,
            "success_rate": round((llm_success_count / total_llm_requests * 100), 1) if total_llm_requests > 0 else 0,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "estimated_total_cost": round(total_cost, 6),
        },
        "tool_stats": {
            "total_calls": total_tool_calls,
            "success_count": tool_success_count,
            "error_count": tool_error_count,
            "success_rate": round((tool_success_count / total_tool_calls * 100), 1) if total_tool_calls > 0 else 0,
        },
        "period_days": days,
    }


@llmstats_router.get("/admin/group-statistics/{group_id}/timeline")
def get_group_statistics_timeline(
    group_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
    granularity: str = Query(default="daily", pattern="^(hourly|daily|weekly|monthly)$"),
):
    """Get timeline data for a specific group."""
    from app.groups.models import Group
    from app.users.models import User
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    users_in_group = db.query(User).filter(User.group_id == group_id).all()
    user_ids = [u.id for u in users_in_group]
    
    if not user_ids:
        return {"timeline": [], "granularity": granularity, "period_days": days, "group_id": group_id}

    bucket_expr = _timeline_bucket_expr(db, LLMGenerationStatistic.created_at, granularity)
    input_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "input_tokens")
    output_tokens_expr = _json_int_expr(LLMGenerationStatistic.meta, "output_tokens")
    rows = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id.in_(user_ids),
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            bucket_expr.label("bucket_start"),
            func.count(LLMGenerationStatistic.id).label("requests"),
            _llm_success_count_expr().label("success"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(_llm_total_cost_expr()), 0.0).label("cost"),
        )
        .group_by(bucket_expr)
        .order_by(bucket_expr)
        .all()
    )

    timeline = []
    for row in rows:
        period, bucket_start_iso = _timeline_period_from_bucket(row.bucket_start, granularity)
        requests = int(row.requests or 0)
        success = int(row.success or 0)
        input_tokens = int(row.input_tokens or 0)
        output_tokens = int(row.output_tokens or 0)
        timeline.append({
            "period": period,
            "bucket_start": bucket_start_iso,
            "requests": requests,
            "success": success,
            "errors": requests - success,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": round(float(row.cost or 0.0), 4),
        })

    return {"timeline": timeline, "granularity": granularity, "period_days": days, "group_id": group_id}


@llmstats_router.get("/admin/group-statistics/{group_id}/by-model")
def get_group_statistics_by_model(
    group_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics grouped by model for a specific group."""
    from app.groups.models import Group
    from app.users.models import User
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    users_in_group = db.query(User).filter(User.group_id == group_id).all()
    user_ids = [u.id for u in users_in_group]
    
    if not user_ids:
        return {"models": [], "period_days": days, "group_id": group_id}
    
    input_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["input_tokens"].astext, Integer), 0)
    output_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["output_tokens"].astext, Integer), 0)
    total_costs_expr = _llm_total_cost_expr()
    success_expr = func.coalesce(func.sum(case((LLMGenerationStatistic.status["success"].astext == "true", 1), else_=0)), 0)
    
    rows = (
        _admin_llm_query(db)
        .filter(
            LLMGenerationStatistic.user_id.in_(user_ids),
            LLMGenerationStatistic.created_at >= cutoff,
        )
        .with_entities(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
            func.count(LLMGenerationStatistic.id).label("total_requests"),
            success_expr.label("success_count"),
            func.coalesce(func.sum(input_tokens_expr), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(total_costs_expr), 0.0).label("total_cost"),
        )
        .group_by(
            LLMGenerationStatistic.model_id,
            LLMGenerationStatistic.model_name,
            LLMGenerationStatistic.provider,
            LLMGenerationStatistic.provider_id,
        )
        .all()
    )
    
    provider_ids = {r.provider_id for r in rows if r.provider_id}
    provider_name_map = _load_provider_name_map(db, provider_ids)
    
    models = []
    for r in rows:
        total = int(r.total_requests)
        sc = int(r.success_count)
        ec = total - sc
        inp = int(r.input_tokens)
        out = int(r.output_tokens)
        cost = float(r.total_cost)
        models.append({
            "model_id": r.model_id,
            "model_name": r.model_name or r.model_id or "unknown",
            "provider": r.provider,
            "provider_id": r.provider_id,
            "provider_name": provider_name_map.get(r.provider_id, r.provider),
            "total_requests": total,
            "success_count": sc,
            "error_count": ec,
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
            "total_cost": round(cost, 6),
            "success_rate": round(sc / total * 100, 1) if total > 0 else 0,
            "error_rate": round(ec / total * 100, 1) if total > 0 else 0,
        })
    
    models.sort(key=lambda x: x["total_requests"], reverse=True)
    
    return {"models": models, "period_days": days, "group_id": group_id}


@llmstats_router.get("/admin/group-statistics/{group_id}/by-user")
def get_group_statistics_by_user(
    group_id: str,
    db: Session = Depends(get_db),
    admin_user=Depends(verified_admin),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get statistics for each user in a specific group."""
    from app.groups.models import Group
    from app.users.models import User
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    users_in_group = db.query(User).filter(User.group_id == group_id).all()
    
    user_ids = [u.id for u in users_in_group]
    
    # Aggregate LLM stats per user using SQL GROUP BY
    llm_by_user = {}
    if user_ids:
        input_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["input_tokens"].astext, Integer), 0)
        output_tokens_expr = func.coalesce(cast(LLMGenerationStatistic.meta["output_tokens"].astext, Integer), 0)
        total_costs_expr = _llm_total_cost_expr()
        
        llm_rows = (
            _admin_llm_query(db)
            .filter(
                LLMGenerationStatistic.user_id.in_(user_ids),
                LLMGenerationStatistic.created_at >= cutoff,
            )
            .with_entities(
                LLMGenerationStatistic.user_id,
                func.count(LLMGenerationStatistic.id),
                func.coalesce(func.sum(input_tokens_expr + output_tokens_expr), 0),
                func.coalesce(func.sum(total_costs_expr), 0.0),
            )
            .group_by(LLMGenerationStatistic.user_id)
            .all()
        )
        llm_by_user = {uid: {"requests": cnt, "tokens": tok, "cost": cost} for uid, cnt, tok, cost in llm_rows}
    
    # Fetch all tool stats in one query
    tool_by_user = {}
    if user_ids:
        tool_rows = (
            _admin_tool_query(db)
            .filter(
                ToolCallStatistic.user_id.in_(user_ids),
                ToolCallStatistic.created_at >= cutoff
            )
            .with_entities(ToolCallStatistic.user_id, func.count(ToolCallStatistic.id))
            .group_by(ToolCallStatistic.user_id)
            .all()
        )
        tool_by_user = dict(tool_rows)
    
    results = []
    for user in users_in_group:
        display_name = None
        if user.first_name or user.last_name:
            display_name = " ".join(filter(None, [user.first_name, user.last_name]))
        
        llm = llm_by_user.get(user.id, {"requests": 0, "tokens": 0, "cost": 0})
        results.append({
            "user": {
                "id": user.id,
                "email": user.email,
                "name": display_name,
            },
            "llm_requests": llm["requests"],
            "tool_calls": tool_by_user.get(user.id, 0),
            "total_tokens": llm["tokens"],
            "estimated_cost": round(llm["cost"], 6),
        })
    
    results.sort(key=lambda x: x["estimated_cost"], reverse=True)
    
    return {"users": results, "group": {"id": group.id, "name": group.name}, "period_days": days}
