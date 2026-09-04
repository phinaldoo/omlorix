from sqlalchemy import BigInteger, Column, String, ForeignKey, Index, UniqueConstraint, delete, func, Boolean, Integer, desc, and_, or_
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import Literal, Any
import uuid
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar

from app.database import Base, AuditSessionLocal, SessionLocal

logger = logging.getLogger(__name__)

current_llm_generation_stats_export_version = 2.0
current_tool_call_stats_export_version = 2.0

# Error rate monitoring configuration
ERROR_RATE_THRESHOLD = 0.30  # 30%
MIN_GENERATIONS_FOR_CHECK = 5
RECENT_GENERATIONS_TO_CHECK = 20
MODEL_TPS_SAMPLE_LIMIT = 25
MODEL_TPS_SCAN_LIMIT = 250
MODEL_TPS_MAX_AGE_DAYS = 30
MODEL_PERFORMANCE_META_KEY = "performance"


AVAILABLE_CATEGORIES = [
    "unknown",
    "chat",
    "title_generation",
    "memory_consolidation",
    "realtime",
]

# Statistics from ordinary request/response generations and long-lived
# realtime calls share one fact table, but they must retain their grain.  A
# realtime row represents one terminal provider response, not an entire call or
# an Omlorix chat turn.  Explicit source fields prevent browser-relayed usage
# from being silently mixed with server-observed provider usage.
INTERACTION_TYPE_GENERATION = "generation"
INTERACTION_TYPE_REALTIME_RESPONSE = "realtime_response"
USAGE_SOURCE_PROVIDER_SERVER = "provider_server"
USAGE_SOURCE_PROVIDER_VIA_CLIENT = "provider_via_client"

AVAILABLE_TYPES = {
    "success": False,
    "error": True,
    "error_status_code": 400,
    "error_message": "", 
    "error_type": "",
}

AVAILABLE_META_KEYS = [
    "time_to_first_token",
    "request_count",
    "input_tokens",
    "tool_use_prompt_tokens",
    "input_token_cached",
    "cache_write_tokens",
    "ephemeral_5m_input_tokens",
    "ephemeral_1h_input_tokens",
    "input_token_text",
    "input_token_image",
    "input_token_audio",
    "input_token_video",
    "input_token_cached_text",
    "input_token_cached_image",
    "input_token_cached_audio",
    "input_token_cached_video",
    "output_tokens",
    "output_text_tokens",
    "output_image_tokens",
    "output_video_tokens",
    "output_audio_tokens",
    "reasoning_tokens",
    "total_tokens",
    "service_tier",
    "generation_time",
    "tokens_per_second",
    "input_tokens_cost",
    "cached_input_tokens_cost",
    "cache_write_tokens_cost",
    "upstream_inference_cost", # OpenRouter
    "meta_is_byok", # OpenRouter upstream BYOK routing; distinct from the Omlorix BYOK column.
    "output_tokens_cost",
    "native_websearch_costs",
    "total_costs",
    "base_url",
    "requested_provider_id",
    "provider_group_id",
    "provider_group_name",
    "selected_provider_id",
    "selected_provider_name",
    # Persist the privacy boundary on the statistic itself so it remains
    # enforceable after a private provider connection is deleted.
    "user_managed",
]

AVAILABLE_META = {
    "time_to_first_token": 0.00, # Seconds with two digits
    "request_count": 0,
    "input_tokens": 0,
    "tool_use_prompt_tokens": 0,
    "input_token_cached": 0,
    "cache_write_tokens": 0,
    "ephemeral_5m_input_tokens": 0,
    "ephemeral_1h_input_tokens": 0,
    "input_token_text": 0,
    "input_token_image": 0,
    "input_token_audio": 0,
    "input_token_video": 0,
    "input_token_cached_text": 0,
    "input_token_cached_image": 0,
    "input_token_cached_audio": 0,
    "input_token_cached_video": 0,
    "output_tokens": 0,
    "output_text_tokens": 0,
    "output_image_tokens": 0,
    "output_video_tokens": 0,
    "output_audio_tokens": 0,
    "reasoning_tokens": 0,
    "total_tokens": 0,
    "service_tier": "",
    "generation_time": 0.00, 
    "tokens_per_second": 0.00,
    "input_tokens_cost": 0.00, # USD
    "cached_input_tokens_cost": 0.00, # Included in input_tokens_cost; exposed for diagnostics.
    "cache_write_tokens_cost": 0.00, # Included in input_tokens_cost; exposed for diagnostics.
    "meta_is_byok": False,
    "output_tokens_cost": 0.00, # USD
    "native_websearch_costs": 0.00, # USD
    "total_costs": 0.00, # USD
    "base_url": "",
    "requested_provider_id": "",
    "provider_group_id": "",
    "provider_group_name": "",
    "selected_provider_id": "",
    "selected_provider_name": "",
    "user_managed": False,
}

# Cache for user statistics settings to avoid repeated DB lookups
_user_stats_settings_cache: dict | None = None
_user_stats_settings_cache_time: datetime | None = None
USER_STATS_CACHE_TTL_SECONDS = 30
_byok_user_stats_cache: dict[str, tuple[datetime, bool]] = {}
BYOK_USER_STATS_CACHE_TTL_SECONDS = 30
BYOK_STATS_DEFAULT_RETENTION_DAYS = 90
BYOK_STATS_MIN_RETENTION_DAYS = 1
BYOK_STATS_MAX_RETENTION_DAYS = 365
ERROR_MESSAGE_MAX_LENGTH = 500

_ERROR_PREFIX_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(token\s*[:=]\s*)[^\s,;]+"),
]
_ERROR_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE),
]


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def coerce_byok_stats_retention_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = BYOK_STATS_DEFAULT_RETENTION_DAYS
    return max(BYOK_STATS_MIN_RETENTION_DAYS, min(BYOK_STATS_MAX_RETENTION_DAYS, days))


def sanitize_provider_error_message(message: Any) -> str:
    text = "" if message is None else str(message)
    if not text:
        return ""
    text = text.replace("\r", " ").replace("\n", " ")
    for pattern in _ERROR_PREFIX_SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[redacted]", text)
    for pattern in _ERROR_SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    if len(text) > ERROR_MESSAGE_MAX_LENGTH:
        return text[:ERROR_MESSAGE_MAX_LENGTH].rstrip() + "..."
    return text


def _coerce_bool(value: Any) -> bool:
    # Backward-compatible alias for internal call sites.
    return coerce_bool(value)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_positive_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def resolve_tool_call_cost(meta: dict | None) -> float:
    if not isinstance(meta, dict):
        return 0.0

    direct_cost = _coerce_float(meta.get("cost"), default=0.0)
    if direct_cost > 0:
        return direct_cost

    total_costs = _coerce_float(meta.get("total_costs"), default=0.0)
    if total_costs > 0:
        return total_costs

    estimated_cost = _coerce_float(meta.get("estimated_cost"), default=0.0)
    if estimated_cost > 0:
        return estimated_cost

    cost_details = meta.get("cost_details")
    if isinstance(cost_details, dict):
        for key in (
            "cost",
            "total_costs",
            "websearch_total_cost",
            "upstream_inference_cost",
        ):
            nested_cost = _coerce_float(cost_details.get(key), default=0.0)
            if nested_cost > 0:
                return nested_cost

    subtotal = (
        _coerce_float(meta.get("input_tokens_cost"), default=0.0)
        + _coerce_float(meta.get("output_tokens_cost"), default=0.0)
        + _coerce_float(meta.get("native_websearch_costs"), default=0.0)
    )
    if subtotal > 0:
        return subtotal

    return 0.0


def _get_user_statistics_settings(db) -> dict:
    """Get user statistics settings with caching."""
    global _user_stats_settings_cache, _user_stats_settings_cache_time
    
    now = datetime.now(timezone.utc)
    if (
        _user_stats_settings_cache is not None
        and _user_stats_settings_cache_time is not None
        and (now - _user_stats_settings_cache_time).total_seconds() < USER_STATS_CACHE_TTL_SECONDS
    ):
        return _user_stats_settings_cache
    
    from app.settings.models import get_settings_page
    
    settings_page = get_settings_page(db, "user_statistics")
    if settings_page and isinstance(settings_page.data, dict):
        _user_stats_settings_cache = settings_page.data
    else:
        _user_stats_settings_cache = {
            "enabled": False,
            "regulatory_confirmed": False,
            "tracked_user_ids": [],
            "track_all_users": False,
        }
    _user_stats_settings_cache_time = now
    return _user_stats_settings_cache


def invalidate_user_statistics_cache():
    """Invalidate the user statistics settings cache."""
    global _user_stats_settings_cache, _user_stats_settings_cache_time
    _user_stats_settings_cache = None
    _user_stats_settings_cache_time = None
    _byok_user_stats_cache.clear()


def _is_byok_user_statistics_enabled(db, user_id: str | None) -> bool:
    if not user_id:
        return False

    now = datetime.now(timezone.utc)
    cache_entry = _byok_user_stats_cache.get(user_id)
    if cache_entry:
        cached_at, cached_value = cache_entry
        if (now - cached_at).total_seconds() < BYOK_USER_STATS_CACHE_TTL_SECONDS:
            return cached_value

    # Import here to avoid circular imports
    from app.users.init import get_user_setting_value

    enabled = False
    try:
        enabled = _coerce_bool(get_user_setting_value(user_id, "chat", "byok_statistics_enabled", db))
    except Exception:
        enabled = False

    _byok_user_stats_cache[user_id] = (now, enabled)
    return enabled


def _infer_is_byok(
    *,
    model_id: str | None = None,
    provider_identifier: str | None = None,
    meta: dict | None = None,
) -> bool:
    if isinstance(model_id, str) and model_id.strip().lower() == "byok":
        return True

    if isinstance(provider_identifier, str):
        normalized_provider = provider_identifier.strip().lower()
        if normalized_provider == "byok" or normalized_provider.startswith("byok_provider"):
            return True

    if isinstance(meta, dict) and _coerce_bool(meta.get("is_byok")):
        # ``meta_is_byok`` is OpenRouter's upstream routing diagnostic. It
        # deliberately does not classify the Omlorix request or database row as
        # BYOK; that remains controlled by the explicit argument and Omlorix's
        # own legacy ``is_byok`` metadata only.
        return True

    return False


def _resolve_tracked_user_id(db, user_id: str | None) -> str | None:
    """
    Determine if user_id should be stored based on user statistics settings.
    Returns the user_id if tracking is enabled for this user, None otherwise.
    """
    if not user_id:
        return None
    
    settings = _get_user_statistics_settings(db)
    
    if not settings.get("enabled", False):
        return None
    
    if not settings.get("regulatory_confirmed", False):
        return None
    
    if settings.get("track_all_users", False):
        return user_id
    
    tracked_user_ids = settings.get("tracked_user_ids", [])
    if isinstance(tracked_user_ids, list) and user_id in tracked_user_ids:
        return user_id
    
    return None

# ---------------------------------------------------------------------------
# LLM Generation Statistics
# ---------------------------------------------------------------------------
class LLMGenerationStatistic(Base):
    __tablename__ = "llm_generation_statistics"
    __table_args__ = (
        UniqueConstraint(
            "interaction_type",
            "session_id",
            "provider_response_id",
            name="uq_llm_generation_statistics_realtime_response",
        ),
        Index(
            "ix_llm_generation_statistics_perf_model_id",
            "is_byok",
            "provider_id",
            "model_id",
            "created_at",
        ),
        Index(
            "ix_llm_generation_statistics_perf_model_name",
            "is_byok",
            "provider_id",
            "model_name",
            "created_at",
        ),
    )
    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    model_name = Column(String, nullable=False)
    model_id = Column(String, nullable=False) 
    provider = Column(String, nullable=False)  # Provider type (e.g., "anthropic", "openai")
    provider_id = Column(String, nullable=False)  # Provider row ID
    status = Column(JSON, nullable=False)
    category = Column(String, nullable=False)
    meta = Column(JSON, nullable=False)
    user_id = Column(String, nullable=True, index=True)  # For user-based statistics tracking
    is_byok = Column(Boolean, nullable=False, default=False, index=True)
    rate_limit_admission_id = Column(String, nullable=True, index=True)
    counted_input_tokens = Column(BigInteger, nullable=False, default=0)
    counted_output_tokens = Column(BigInteger, nullable=False, default=0)
    counted_tokens = Column(BigInteger, nullable=False, default=0)
    interaction_type = Column(
        String,
        nullable=False,
        default=INTERACTION_TYPE_GENERATION,
        index=True,
    )
    session_id = Column(String, nullable=True, index=True)
    turn_id = Column(String, nullable=True, index=True)
    provider_response_id = Column(String, nullable=True)
    usage_source = Column(
        String,
        nullable=False,
        default=USAGE_SOURCE_PROVIDER_SERVER,
    )
    usage_verified = Column(Boolean, nullable=False, default=True)
    turn_index = Column(Integer, nullable=True)
    interrupted = Column(Boolean, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)



# ---------------------------------------------------------------------------
# Tool Call Statistics
# ---------------------------------------------------------------------------
class ToolCallStatistic(Base):
    __tablename__ = "tool_call_statistics"
    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    tool_name = Column(String, nullable=False)
    success = Column(Boolean, nullable=False, default=False)
    error_message = Column(String, nullable=True)
    execution_time = Column(String, nullable=True)  # Store as string for flexibility
    model_id = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    user_id = Column(String, nullable=True)
    is_byok = Column(Boolean, nullable=False, default=False, index=True)
    interaction_type = Column(String, nullable=False, default=INTERACTION_TYPE_GENERATION, index=True)
    session_id = Column(String, nullable=True, index=True)
    turn_id = Column(String, nullable=True, index=True)
    tool_call_id = Column(String, nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)


_tool_statistics_context: ContextVar[dict[str, str] | None] = ContextVar(
    "tool_statistics_context",
    default=None,
)


@contextmanager
def tool_statistics_context(
    *,
    interaction_type: str,
    session_id: str | None = None,
    turn_id: str | None = None,
    tool_call_id: str | None = None,
):
    """Attach interaction correlation to nested shared tool recorders."""
    token = _tool_statistics_context.set(
        {
            "interaction_type": str(interaction_type or INTERACTION_TYPE_GENERATION),
            "session_id": str(session_id or "").strip(),
            "turn_id": str(turn_id or "").strip(),
            "tool_call_id": str(tool_call_id or "").strip(),
        }
    )
    try:
        yield
    finally:
        _tool_statistics_context.reset(token)


def _valid_tps_sample(stat: LLMGenerationStatistic) -> float | None:
    status = stat.status if isinstance(stat.status, dict) else {}
    if status.get("success") is not True:
        return None

    meta = stat.meta if isinstance(stat.meta, dict) else {}
    tokens_per_second = _coerce_optional_positive_float(meta.get("tokens_per_second"))
    if tokens_per_second is None:
        return None

    generation_time = _coerce_float(meta.get("generation_time"), default=0.0)
    output_tokens = int(_coerce_float(meta.get("output_tokens"), default=0.0))
    if generation_time > 2 or (generation_time >= 1 and output_tokens > 100):
        return tokens_per_second
    return None


def calculate_model_tokens_per_second_summary(
    stats: list[LLMGenerationStatistic] | tuple[LLMGenerationStatistic, ...],
    *,
    sample_limit: int | None = MODEL_TPS_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Average recent valid throughput samples for model-select display."""
    if sample_limit is None:
        sample_limit = MODEL_TPS_SAMPLE_LIMIT
    sample_limit = int(sample_limit)
    if sample_limit == 0:
        raise ValueError("sample_limit must be greater than 0")
    sample_limit = max(1, sample_limit)
    samples: list[float] = []
    for stat in stats:
        tokens_per_second = _valid_tps_sample(stat)
        if tokens_per_second is None:
            continue
        samples.append(tokens_per_second)
        if len(samples) >= sample_limit:
            break

    if not samples:
        return {
            "tokens_per_second": None,
            "sample_count": 0,
        }

    return {
        "tokens_per_second": round(sum(samples) / len(samples), 2),
        "sample_count": len(samples),
    }


def get_model_performance_meta(model_meta: dict | None) -> dict[str, Any]:
    if not isinstance(model_meta, dict):
        return {}
    performance = model_meta.get(MODEL_PERFORMANCE_META_KEY)
    return performance if isinstance(performance, dict) else {}


def get_model_cached_tokens_per_second(model_meta: dict | None) -> float | None:
    performance = get_model_performance_meta(model_meta)
    value = _coerce_optional_positive_float(performance.get("tokens_per_second"))
    return round(value, 2) if value is not None else None


def get_model_cached_tokens_per_second_sample_count(model_meta: dict | None) -> int:
    performance = get_model_performance_meta(model_meta)
    try:
        return max(0, int(performance.get("sample_count") or 0))
    except (TypeError, ValueError):
        return 0


def _model_provider_filter(db, model):
    provider_id = getattr(model, "provider_id", None)
    provider = getattr(model, "provider", None)
    if isinstance(provider_id, str) and provider_id.strip():
        provider_id = provider_id.strip()
        clauses = [LLMGenerationStatistic.provider_id == provider_id]
        try:
            from app.llm.models import LLMProviderGroup

            is_group = db.query(LLMProviderGroup).filter(LLMProviderGroup.id == provider_id).first() is not None
        except Exception:
            is_group = False
        if is_group:
            clauses.extend(
                [
                    LLMGenerationStatistic.meta["requested_provider_id"].astext == provider_id,
                    LLMGenerationStatistic.meta["provider_group_id"].astext == provider_id,
                ]
            )
        return or_(*clauses)

    if isinstance(provider, str) and provider.strip():
        return LLMGenerationStatistic.provider == provider
    return None


def _model_stat_identifier_filter(db, model):
    identifier_values = {
        value
        for value in {
            getattr(model, "id", None),
            getattr(model, "model_name", None),
        }
        if isinstance(value, str) and value.strip()
    }
    name_values = {
        value
        for value in {
            getattr(model, "model_name", None),
        }
        if isinstance(value, str) and value.strip()
    }

    clauses = []
    if identifier_values:
        clauses.append(LLMGenerationStatistic.model_id.in_(identifier_values))
    if name_values:
        clauses.append(LLMGenerationStatistic.model_name.in_(name_values))
    if not clauses:
        clauses.append(LLMGenerationStatistic.model_id == getattr(model, "id", ""))

    model_clause = or_(*clauses)
    provider_clause = _model_provider_filter(db, model)
    if provider_clause is not None:
        return and_(model_clause, provider_clause)
    return model_clause


def refresh_model_tokens_per_second_cache(
    db,
    *,
    sample_limit: int = MODEL_TPS_SAMPLE_LIMIT,
    scan_limit: int = MODEL_TPS_SCAN_LIMIT,
    max_age_days: int = MODEL_TPS_MAX_AGE_DAYS,
    model_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> int:
    """Refresh cached model throughput metadata from recent generation statistics."""
    from app.llm.models import Models

    sample_limit = max(1, int(sample_limit or MODEL_TPS_SAMPLE_LIMIT))
    scan_limit = max(sample_limit, int(scan_limit or MODEL_TPS_SCAN_LIMIT))
    max_age_days = max(1, int(max_age_days or MODEL_TPS_MAX_AGE_DAYS))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    query = db.query(Models).filter(Models.is_active.is_(True))
    if model_ids:
        normalized_ids = [
            str(model_id).strip()
            for model_id in model_ids
            if str(model_id or "").strip()
        ]
        if not normalized_ids:
            return 0
        query = query.filter(Models.id.in_(normalized_ids))

    updated_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for model in query.all():
        stats = (
            db.query(LLMGenerationStatistic)
            .filter(
                LLMGenerationStatistic.is_byok.is_(False),
                LLMGenerationStatistic.interaction_type == INTERACTION_TYPE_GENERATION,
                LLMGenerationStatistic.created_at >= cutoff,
                _model_stat_identifier_filter(db, model),
            )
            .order_by(desc(LLMGenerationStatistic.created_at))
            .limit(scan_limit)
            .all()
        )
        summary = calculate_model_tokens_per_second_summary(
            stats,
            sample_limit=sample_limit,
        )
        performance = {
            "tokens_per_second": summary["tokens_per_second"],
            "sample_count": summary["sample_count"],
            "sample_limit": sample_limit,
            "max_age_days": max_age_days,
            "updated_at": now_iso,
        }

        model_meta = dict(model.meta) if isinstance(model.meta, dict) else {}
        current_performance = get_model_performance_meta(model_meta)
        comparable_current = {
            key: current_performance.get(key)
            for key in ("tokens_per_second", "sample_count", "sample_limit", "max_age_days")
        }
        comparable_next = {
            key: performance.get(key)
            for key in ("tokens_per_second", "sample_count", "sample_limit", "max_age_days")
        }
        if comparable_current == comparable_next:
            continue

        model_meta[MODEL_PERFORMANCE_META_KEY] = performance
        model.meta = model_meta
        flag_modified(model, "meta")
        updated_count += 1

    if updated_count:
        db.commit()
    return updated_count


def create_tool_call_statistic(
    db,
    tool_name: str,
    success: bool = True,
    error_message: str | None = None,
    execution_time: float | None = None,
    model_id: str | None = None,
    model_name: str | None = None,
    provider: str | None = None,
    user_id: str | None = None,
    meta: dict | None = None,
    is_byok: bool | None = None,
    interaction_type: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    tool_call_id: str | None = None,
):
    """Create a tool call statistic record."""
    meta = dict(meta) if isinstance(meta, dict) else {}
    resolved_cost = resolve_tool_call_cost(meta)
    if resolved_cost > 0:
        meta["cost"] = round(resolved_cost, 6)
    resolved_is_byok = bool(is_byok) if is_byok is not None else _infer_is_byok(
        model_id=model_id,
        provider_identifier=provider,
        meta=meta,
    )

    if resolved_is_byok:
        if not user_id or not _is_byok_user_statistics_enabled(db, user_id):
            return None
        tracked_user_id = user_id
    else:
        tracked_user_id = _resolve_tracked_user_id(db, user_id)

    correlation = _tool_statistics_context.get() or {}
    new_stat = ToolCallStatistic(
        tool_name=tool_name,
        success=success,
        error_message=sanitize_provider_error_message(error_message) or None,
        execution_time=str(round(execution_time, 3)) if execution_time else None,
        model_id=model_id,
        model_name=model_name,
        provider=provider,
        user_id=tracked_user_id,
        is_byok=resolved_is_byok,
        interaction_type=(
            interaction_type
            or correlation.get("interaction_type")
            or INTERACTION_TYPE_GENERATION
        ),
        session_id=session_id or correlation.get("session_id") or None,
        turn_id=turn_id or correlation.get("turn_id") or None,
        tool_call_id=tool_call_id or correlation.get("tool_call_id") or None,
        meta=meta or None,
        created_at=datetime.now(timezone.utc),
    )
    
    try:
        db.add(new_stat)
        db.commit()
        db.refresh(new_stat)
    except Exception as exc:
        db.rollback()
        logger.warning(f"Failed to create tool call statistic: {exc}")
        return None
    
    return new_stat


def create_llm_generation_statistic(
    db,
    model_name: str = "",
    model_id: str = "",
    provider: str = "",
    provider_id: str = "",
    success: bool = False,
    error: bool = False,
    error_status_code: int = 0,
    error_message: str = "",
    error_type: str = "",
    category: str = "unknown",
    meta: dict | None = None,
    *,
    source_model_id: str | None = None,
    user_id: str | None = None,
    is_byok: bool | None = None,
):
    meta = meta or {}
    status = {
        "success": success,
        "error": error,
        "error_status_code": error_status_code,
        "error_message": sanitize_provider_error_message(error_message),
        "error_type": error_type,
    }
    if category not in AVAILABLE_CATEGORIES:
        category = "unknown"
    db_meta = {}
    for key in AVAILABLE_META_KEYS:
        if key in meta:
            db_meta[key] = meta[key]
    resolved_model_id = source_model_id or model_id or model_name

    from app.llm.models import (
        RATE_LIMIT_ADMISSION_OPEN,
        RateLimitChatAdmission,
        RateLimitUsageWindow,
        get_current_rate_limit_admission_context,
        normalize_rate_limit_token_usage,
    )

    admission_context = get_current_rate_limit_admission_context()
    counted_usage = normalize_rate_limit_token_usage(meta)

    resolved_is_byok = bool(is_byok) if is_byok is not None else _infer_is_byok(
        model_id=resolved_model_id,
        provider_identifier=provider_id,
        meta=meta,
    )

    if resolved_is_byok:
        if not user_id or not _is_byok_user_statistics_enabled(db, user_id):
            return None
        tracked_user_id = user_id
    else:
        # Only store user_id if admin user statistics tracking is enabled
        tracked_user_id = _resolve_tracked_user_id(db, user_id)

    new_stat = LLMGenerationStatistic(
        model_name=model_name,
        model_id=resolved_model_id or "unknown",
        provider=provider,
        provider_id=provider_id,
        status=status,
        category=category,
        meta=db_meta,
        user_id=tracked_user_id,
        is_byok=resolved_is_byok,
        rate_limit_admission_id=admission_context.admission_id if admission_context else None,
        counted_input_tokens=counted_usage["counted_input_tokens"],
        counted_output_tokens=counted_usage["counted_output_tokens"],
        counted_tokens=counted_usage["counted_tokens"],
        created_at=datetime.now(timezone.utc),
    )
    
    try:
        db.add(new_stat)
        if admission_context:
            admission = (
                db.query(RateLimitChatAdmission)
                .filter(RateLimitChatAdmission.id == admission_context.admission_id)
                .first()
            )
            if admission and admission.status == RATE_LIMIT_ADMISSION_OPEN:
                usage_window = (
                    db.query(RateLimitUsageWindow)
                    .filter(
                        RateLimitUsageWindow.rate_limit_id == admission.rate_limit_id,
                        RateLimitUsageWindow.user_id == admission.user_id,
                        RateLimitUsageWindow.window_start == admission.window_start,
                    )
                    .first()
                )
                if not usage_window:
                    usage_window = RateLimitUsageWindow(
                        rate_limit_id=admission.rate_limit_id,
                        user_id=admission.user_id,
                        window_start=admission.window_start,
                        request_count=0,
                        token_count=0,
                        invocation_count=0,
                    )
                usage_window.token_count = int(getattr(usage_window, "token_count", 0) or 0) + counted_usage["counted_tokens"]
                if not bool(getattr(admission, "request_counted", False)):
                    usage_window.request_count = int(getattr(usage_window, "request_count", 0) or 0) + 1
                    admission.request_counted = True
                    db.add(admission)
                db.add(usage_window)
        db.commit()
        db.refresh(new_stat)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Failed to create llm generation statistic"
        ) from exc

    # Check for elevated error rates after creating the statistic
    if not resolved_is_byok:
        try:
            check_model_error_rate(db, resolved_model_id, provider)
        except Exception as e:
            # Don't fail the main operation if error rate check fails
            logger.warning(f"Error rate check failed for model {model_id}: {e}")

    return new_stat


def _bounded_usage_count(value: Any) -> int:
    """Return a non-negative integer for untrusted provider usage values."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, min(int(value or 0), 2_000_000_000))
    except (TypeError, ValueError, OverflowError):
        return 0


def normalize_realtime_interaction_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    """Normalize browser-relayed realtime usage into shared metric names.

    Provider top-level token counters include every modality.  Text counters
    are therefore read from detail objects when available and otherwise
    conservatively derived by subtracting the reported audio subset.  The
    provenance columns on the statistic still make clear that these values
    crossed the untrusted browser boundary.
    """
    payload = usage if isinstance(usage, dict) else {}
    input_details = payload.get("input_token_details")
    if not isinstance(input_details, dict):
        input_details = payload.get("input_tokens_details")
    if not isinstance(input_details, dict):
        input_details = {}
    output_details = payload.get("output_token_details")
    if not isinstance(output_details, dict):
        output_details = payload.get("output_tokens_details")
    if not isinstance(output_details, dict):
        output_details = {}

    input_tokens = _bounded_usage_count(payload.get("input_tokens"))
    output_tokens = _bounded_usage_count(payload.get("output_tokens"))
    input_audio_tokens = min(
        _bounded_usage_count(input_details.get("audio_tokens")),
        input_tokens,
    )
    output_audio_tokens = min(
        _bounded_usage_count(output_details.get("audio_tokens")),
        output_tokens,
    )
    explicit_input_text = input_details.get("text_tokens")
    explicit_output_text = output_details.get("text_tokens")
    input_text_tokens = (
        min(_bounded_usage_count(explicit_input_text), input_tokens)
        if explicit_input_text is not None
        else max(input_tokens - input_audio_tokens, 0)
    )
    output_text_tokens = (
        min(_bounded_usage_count(explicit_output_text), output_tokens)
        if explicit_output_text is not None
        else max(output_tokens - output_audio_tokens, 0)
    )
    cached_input_tokens = min(
        _bounded_usage_count(input_details.get("cached_tokens")),
        input_tokens,
    )
    reported_total = _bounded_usage_count(payload.get("total_tokens"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": reported_total or input_tokens + output_tokens,
        "input_text_tokens": input_text_tokens,
        "input_audio_tokens": input_audio_tokens,
        "input_cached_tokens": cached_input_tokens,
        "output_text_tokens": output_text_tokens,
        "output_audio_tokens": output_audio_tokens,
    }


def create_realtime_response_statistic(
    db,
    *,
    model_name: str,
    model_id: str,
    provider: str,
    provider_id: str,
    session_id: str,
    turn_id: str,
    provider_response_id: str,
    turn_index: int,
    usage: dict[str, Any] | None,
    provider_status: str | None = None,
    interrupted: bool = False,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    user_id: str | None = None,
    is_byok: bool | None = None,
    commit: bool = True,
) -> LLMGenerationStatistic | None:
    """Persist one terminal realtime provider response in the shared fact table.

    This recorder intentionally has no rate-limit side effects. Realtime uses
    duration admissions, while ordinary generations use token/request
    admissions. Keeping accounting outside the persistence primitive prevents
    a realtime response from being charged as a separate chat request.
    """
    normalized_usage = normalize_realtime_interaction_usage(usage)
    normalized_status = str(provider_status or "").strip().lower()
    sanitized_error = sanitize_provider_error_message(error_message)
    is_cancelled = normalized_status in {"cancelled", "canceled", "incomplete"}
    is_error = bool(sanitized_error) or normalized_status in {"failed", "error"}
    is_success = not is_error and not is_cancelled

    try:
        tracked_user_id = _resolve_tracked_user_id(db, user_id)
    except Exception:
        # Analytics attribution is optional. Failure to read its privacy
        # settings must fail closed rather than retaining an operational ID.
        tracked_user_id = None

    resolved_is_byok = bool(is_byok) if is_byok is not None else _infer_is_byok(
        model_id=model_id,
        provider_identifier=provider_id,
        meta=None,
    )
    if resolved_is_byok:
        # Match ordinary generation statistics: private-provider analytics are
        # opt-in and omitted entirely when their owner disables collection.
        if not user_id or not _is_byok_user_statistics_enabled(db, user_id):
            return None
        tracked_user_id = user_id

    now = datetime.now(timezone.utc)
    response_id = str(provider_response_id or "").strip()
    if not response_id:
        raise ValueError("provider_response_id is required")
    record = LLMGenerationStatistic(
        model_name=str(model_name or "unknown"),
        model_id=str(model_id or model_name or "unknown"),
        provider=str(provider or "unknown"),
        provider_id=str(provider_id or "unknown"),
        status={
            "success": is_success,
            "error": is_error,
            "error_status_code": 0,
            "error_message": sanitized_error,
            "error_type": "realtime_provider_error" if is_error else "",
            "provider_status": normalized_status,
        },
        category="realtime",
        meta=normalized_usage,
        user_id=tracked_user_id,
        is_byok=resolved_is_byok,
        counted_input_tokens=normalized_usage["input_tokens"],
        counted_output_tokens=normalized_usage["output_tokens"],
        counted_tokens=normalized_usage["total_tokens"],
        interaction_type=INTERACTION_TYPE_REALTIME_RESPONSE,
        session_id=str(session_id or "").strip(),
        turn_id=str(turn_id or "").strip(),
        provider_response_id=response_id,
        usage_source=USAGE_SOURCE_PROVIDER_VIA_CLIENT,
        usage_verified=False,
        turn_index=max(0, int(turn_index or 0)),
        interrupted=bool(interrupted),
        started_at=started_at,
        completed_at=completed_at or now,
        created_at=now,
    )
    try:
        db.add(record)
        if commit:
            db.commit()
            db.refresh(record)
        else:
            db.flush()
    except Exception as exc:
        if commit:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Failed to create realtime interaction statistic",
            ) from exc
        raise
    return record


def _byok_stats_user_ids(db) -> set[str]:
    user_ids: set[str] = set()
    for model in (LLMGenerationStatistic, ToolCallStatistic):
        try:
            rows = (
                db.query(model.user_id)
                .filter(model.is_byok.is_(True), model.user_id.isnot(None))
                .distinct()
                .all()
            )
        except Exception:
            continue
        for (user_id,) in rows:
            if user_id:
                user_ids.add(str(user_id))
    return user_ids


def purge_expired_byok_statistics(db, *, now: datetime | None = None) -> dict[str, int]:
    from app.users.init import get_user_setting_value

    reference = now or datetime.now(timezone.utc)
    deleted_llm = 0
    deleted_tools = 0
    for user_id in _byok_stats_user_ids(db):
        retention_days = coerce_byok_stats_retention_days(
            get_user_setting_value(user_id, "chat", "byok_statistics_retention_days", db)
        )
        cutoff = reference - timedelta(days=retention_days)
        deleted_llm += (
            db.query(LLMGenerationStatistic)
            .filter(
                LLMGenerationStatistic.user_id == user_id,
                LLMGenerationStatistic.is_byok.is_(True),
                LLMGenerationStatistic.created_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        deleted_tools += (
            db.query(ToolCallStatistic)
            .filter(
                ToolCallStatistic.user_id == user_id,
                ToolCallStatistic.is_byok.is_(True),
                ToolCallStatistic.created_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
    db.commit()
    return {"llm_deleted": int(deleted_llm), "tool_deleted": int(deleted_tools)}


def check_model_error_rate(db: Session, model_id: str, provider: str | None = None) -> None:
    """
    Check if a model has elevated error rates and update its meta/send notifications.
    
    Checks the last RECENT_GENERATIONS_TO_CHECK generations for the model.
    If there are at least MIN_GENERATIONS_FOR_CHECK generations and error rate > ERROR_RATE_THRESHOLD:
    - Sets model.meta["increased_errors"] = True
    - Sends an admin notification (if not already flagged)
    
    If error rate drops below threshold:
    - Sets model.meta["increased_errors"] = False
    """
    # Import here to avoid circular imports
    from app.llm.models import Models
    
    if not model_id and not provider:
        return

    # Get the model
    model = None
    base_query = db.query(Models).with_for_update()
    if model_id:
        model = (
            base_query.filter(
                or_(
                    Models.id == model_id,
                    Models.model_name == model_id,
                    Models.name == model_id,
                )
            )
            .first()
        )
    if not model:
        query = base_query
        if provider:
            query = query.filter(Models.provider == provider)
        model = query.first()
    if not model:
        return

    provider_filter = provider or model.provider
    identifier_values = {
        value
        for value in {model_id, getattr(model, "id", None), getattr(model, "model_name", None)}
        if value
    }
    name_identifiers = {value for value in {getattr(model, "name", None)} if value}
    
    # Get recent generations for this model
    stat_query = db.query(LLMGenerationStatistic).filter(
        LLMGenerationStatistic.provider == provider_filter,
        LLMGenerationStatistic.interaction_type == INTERACTION_TYPE_GENERATION,
    )
    identifier_clauses = []
    if identifier_values:
        identifier_clauses.append(LLMGenerationStatistic.model_id.in_(identifier_values))
    if name_identifiers:
        identifier_clauses.append(LLMGenerationStatistic.model_name.in_(name_identifiers))
    if identifier_clauses:
        stat_query = stat_query.filter(or_(*identifier_clauses))
    else:
        stat_query = stat_query.filter(LLMGenerationStatistic.model_id == model_id)

    recent_stats = (
        stat_query.order_by(desc(LLMGenerationStatistic.created_at))
        .limit(RECENT_GENERATIONS_TO_CHECK)
        .all()
    )
    
    total_count = len(recent_stats)
    
    # Need minimum generations to make a judgment
    if total_count < MIN_GENERATIONS_FOR_CHECK:
        return
    
    # Calculate error rate
    error_count = sum(
        1 for stat in recent_stats
        if stat.status and not stat.status.get("success", False)
    )
    error_rate = error_count / total_count
    
    # Get current model meta (copy to avoid SQLAlchemy change tracking issues)
    model_meta = dict(model.meta) if isinstance(model.meta, dict) else {}

    def _coerce_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    currently_flagged = _coerce_bool(model_meta.get("increased_errors", False))
    should_flag = error_rate > ERROR_RATE_THRESHOLD

    # Only act when the status changes to avoid duplicate notifications.
    if should_flag and not currently_flagged:
        # High error rate detected
        model_meta["increased_errors"] = True
        model_meta["error_rate_detected_at"] = datetime.now(timezone.utc).isoformat()
        model_meta["error_rate_value"] = round(error_rate * 100, 1)
        model.meta = model_meta
        flag_modified(model, "meta")
        db.commit()
        
        # Send admin notification
        _send_error_rate_notification(
            model_name=model.name,
            model_id=model_id,
            provider=model.provider,
            error_rate=error_rate,
            error_count=error_count,
            total_count=total_count,
        )
    elif not should_flag and currently_flagged:
        # Error rate recovered
        model_meta["increased_errors"] = False
        model_meta["error_rate_resolved_at"] = datetime.now(timezone.utc).isoformat()
        model_meta.pop("error_rate_detected_at", None)
        model_meta.pop("error_rate_value", None)
        model.meta = model_meta
        flag_modified(model, "meta")
        db.commit()
        
        # Notify once that the issue is resolved
        _send_error_rate_resolved_notification(
            model_name=model.name,
            model_id=model_id,
            provider=model.provider,
            error_rate=error_rate,
        )


def _send_error_rate_notification(
    model_name: str,
    model_id: str,
    provider: str,
    error_rate: float,
    error_count: int,
    total_count: int,
) -> None:
    """Send admin notification about elevated error rate."""
    from app.logging.models import create_admin_notification
    
    try:
        error_pct = round(error_rate * 100, 1)
        
        with SessionLocal() as session:
            create_admin_notification(
                session,
                category="model_health",
                message=f"Model '{model_name}' has elevated error rate: {error_pct}%",
                details={
                    "model_id": model_id,
                    "model_name": model_name,
                    "provider": provider,
                    "error_rate": error_pct,
                    "error_count": error_count,
                    "total_recent_requests": total_count,
                    "threshold": round(ERROR_RATE_THRESHOLD * 100, 1),
                },
                notification_type="warning",
            )
    except Exception as e:
        logger.error(f"Failed to send error rate notification: {e}")


def _send_error_rate_resolved_notification(
    model_name: str,
    model_id: str,
    provider: str,
    error_rate: float,
) -> None:
    """Send admin notification that error rate has normalized."""
    from app.logging.models import create_admin_notification
    
    try:
        error_pct = round(error_rate * 100, 1)
        
        with SessionLocal() as session:
            create_admin_notification(
                session,
                category="model_health",
                message=f"Model '{model_name}' error rate normalized: {error_pct}%",
                details={
                    "model_id": model_id,
                    "model_name": model_name,
                    "provider": provider,
                    "current_error_rate": error_pct,
                    "threshold": round(ERROR_RATE_THRESHOLD * 100, 1),
                },
                notification_type="info",
            )
    except Exception as e:
        logger.error(f"Failed to send error rate resolved notification: {e}")


def _serialize_datetime_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt_value = value
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return dt_value.isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value.strip():
        return value
    return None



def export_llm_generation_stats(
    db: Session,
    *,
    user_id: str | None = None,
    is_byok: bool | None = None,
    interaction_type: str = INTERACTION_TYPE_GENERATION,
) -> dict:
    """Export all LLM generation statistics."""
    query = db.query(LLMGenerationStatistic).filter(
        LLMGenerationStatistic.interaction_type == interaction_type
    )
    if user_id is not None:
        query = query.filter(LLMGenerationStatistic.user_id == user_id)
    if is_byok is not None:
        query = query.filter(LLMGenerationStatistic.is_byok.is_(bool(is_byok)))
    stats = query.order_by(desc(LLMGenerationStatistic.created_at)).all()
    
    export_data = []
    for stat in stats:
        status_payload = dict(stat.status or {}) if isinstance(stat.status, dict) else {}
        if "error_message" in status_payload:
            status_payload["error_message"] = sanitize_provider_error_message(status_payload.get("error_message"))
        export_data.append({
            "id": stat.id,
            "interaction_type": stat.interaction_type,
            "session_id": stat.session_id,
            "turn_id": stat.turn_id,
            "provider_response_id": stat.provider_response_id,
            "model_name": stat.model_name,
            "model_id": stat.model_id,
            "provider": stat.provider,
            "provider_id": stat.provider_id,
            "category": stat.category,
            "status": status_payload,
            "meta": stat.meta or {},
            "user_id": stat.user_id,
            "is_byok": bool(stat.is_byok),
            "usage_source": stat.usage_source,
            "usage_verified": bool(stat.usage_verified),
            "started_at": _serialize_datetime_value(stat.started_at),
            "completed_at": _serialize_datetime_value(stat.completed_at),
            "created_at": _serialize_datetime_value(stat.created_at),
        })
    
    return {
        "export_type": "llm_generation_stats",
        "export_version": current_llm_generation_stats_export_version,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "statistics": export_data,
            "total_count": len(export_data),
        },
    }




# ---------------------------------------------------------------------------
# Tool Call Statistics Export
# ---------------------------------------------------------------------------
def export_tool_call_stats(
    db: Session,
    *,
    user_id: str | None = None,
    is_byok: bool | None = None,
) -> dict:
    """Export all tool call statistics."""
    query = db.query(ToolCallStatistic)
    if user_id is not None:
        query = query.filter(ToolCallStatistic.user_id == user_id)
    if is_byok is not None:
        query = query.filter(ToolCallStatistic.is_byok.is_(bool(is_byok)))
    stats = query.order_by(desc(ToolCallStatistic.created_at)).all()
    
    export_data = []
    for stat in stats:
        export_data.append({
            "id": stat.id,
            "interaction_type": stat.interaction_type,
            "session_id": stat.session_id,
            "turn_id": stat.turn_id,
            "tool_call_id": stat.tool_call_id,
            "tool_name": stat.tool_name,
            "success": stat.success,
            "error_message": sanitize_provider_error_message(stat.error_message) or None,
            "execution_time": stat.execution_time,
            "model_id": stat.model_id,
            "model_name": stat.model_name,
            "provider": stat.provider,
            "user_id": stat.user_id,
            "is_byok": bool(stat.is_byok),
            "meta": stat.meta or {},
            "created_at": _serialize_datetime_value(stat.created_at),
        })
    
    return {
        "export_type": "tool_call_stats",
        "export_version": current_tool_call_stats_export_version,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "statistics": export_data,
            "total_count": len(export_data),
        },
    }
