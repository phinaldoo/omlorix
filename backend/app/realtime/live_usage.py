"""Provider-authoritative Live billing, separate from browser caption windows."""

import math
from contextlib import nullcontext

from app.database import SessionLocal
from app.llm.openai.live import is_openai_live_model
from app.llmstats.models import (
    LLMGenerationStatistic, create_realtime_response_statistic,
    USAGE_SOURCE_PROVIDER_SERVER,
)
from app.realtime.models import get_realtime_session_by_session_id


def record_live_usage(session_id: str, response: dict | None = None, *, seconds: float | None = None, finalized: bool = False, db=None):
    """Write terminal backend responses or a cumulative voice snapshot idempotently."""
    with (nullcontext(db) if db is not None else SessionLocal()) as db:
        session = get_realtime_session_by_session_id(db, session_id=session_id)
        if not session or not is_openai_live_model(session.model_name):
            return
        response = response or {}
        response_id = str(response.get("id") or "") if seconds is None else f"{session_id}:voice"
        if not response_id:
            return
        # Serialize updates to the cumulative voice fact across shutdown paths.
        existing = db.query(LLMGenerationStatistic).filter(
            LLMGenerationStatistic.session_id == session_id,
            LLMGenerationStatistic.provider_response_id == response_id,
        ).with_for_update().first()
        if existing and seconds is None:
            return
        usage = response.get("usage") or {}
        normalized_usage = {**usage, "input_token_details": usage.get("input_tokens_details") or {}, "output_token_details": usage.get("output_tokens_details") or {}}
        record = existing or create_realtime_response_statistic(
            db, model_name=str(response.get("model") or session.model_name),
            model_id=session.model_id or session.model_name, provider=session.provider,
            provider_id=session.provider_id, session_id=session_id, turn_id=session_id,
            provider_response_id=response_id, turn_index=0, usage=normalized_usage,
            provider_status=response.get("status") or "completed", user_id=session.user_id,
            started_at=session.started_at, commit=False,
        )
        if record is None:
            return
        record.usage_source = USAGE_SOURCE_PROVIDER_SERVER
        record.usage_verified = True
        if seconds is not None:
            normalized_seconds = float(seconds)
            if not math.isfinite(normalized_seconds) or normalized_seconds < 0:
                return
            duration = max(normalized_seconds, float((record.meta or {}).get("voice_seconds", 0)))
            record.meta = {
                **(record.meta or {}), "voice_seconds": duration,
                # WebRTC initialization is a credited 15-second minimum, not
                # an extra fee added to the measured call duration.
                "billable_voice_seconds": max(15, duration),
                "finalized": finalized or bool((record.meta or {}).get("finalized")),
                "billing_component": "live_voice",
                "total_costs": max(15, duration) * 0.05 / 60,
            }
        else:
            from app.llm.openai.utils import calculate_openai_token_costs
            usage = response.get("usage") or {}
            details = usage.get("input_tokens_details") or {}
            costs = calculate_openai_token_costs(
                record.model_name, response.get("service_tier"),
                usage.get("input_tokens", 0), details.get("cached_tokens", 0),
                usage.get("output_tokens", 0),
                (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0), 0,
            ) or {}
            record.meta = {**record.meta, **costs, "billing_component": "live_delegation"}
        db.commit()
