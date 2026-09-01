from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.llmstats import realtime_router

from app.llmstats.models import (
    INTERACTION_TYPE_REALTIME_RESPONSE,
    LLMGenerationStatistic,
    ToolCallStatistic,
    create_realtime_response_statistic,
    create_tool_call_statistic,
    normalize_realtime_interaction_usage,
    tool_statistics_context,
)


def _db(*tables):
    engine = create_engine("sqlite:///:memory:")
    for table in tables:
        table.create(bind=engine)
    return sessionmaker(bind=engine)()


def test_realtime_usage_keeps_total_and_modality_counts_distinct():
    usage = normalize_realtime_interaction_usage(
        {
            "input_tokens": 100,
            "output_tokens": 40,
            "input_token_details": {
                "audio_tokens": 70,
                "text_tokens": 30,
                "cached_tokens": 10,
            },
            "output_token_details": {
                "audio_tokens": 25,
                "text_tokens": 15,
            },
        }
    )

    assert usage == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
        "input_text_tokens": 30,
        "input_audio_tokens": 70,
        "input_cached_tokens": 10,
        "output_text_tokens": 15,
        "output_audio_tokens": 25,
    }


def test_realtime_response_uses_shared_fact_table_and_unverified_provenance():
    db = _db(LLMGenerationStatistic.__table__)

    create_realtime_response_statistic(
        db,
        model_name="gpt-realtime",
        model_id="model-1",
        provider="openai",
        provider_id="provider-1",
        session_id="session-1",
        turn_id="turn-1",
        provider_response_id="response-1",
        turn_index=1,
        usage={"input_tokens": 9, "output_tokens": 3},
        provider_status="completed",
        user_id="user-1",
    )

    row = db.query(LLMGenerationStatistic).one()
    assert row.interaction_type == INTERACTION_TYPE_REALTIME_RESPONSE
    assert row.category == "realtime"
    assert row.session_id == "session-1"
    assert row.turn_id == "turn-1"
    assert row.provider_response_id == "response-1"
    assert row.usage_source == "provider_via_client"
    assert row.usage_verified is False
    assert row.counted_tokens == 12
    # User attribution fails closed when statistics tracking is unavailable.
    assert row.user_id is None


def test_realtime_tool_context_correlates_shared_tool_statistic():
    db = _db(ToolCallStatistic.__table__)

    with tool_statistics_context(
        interaction_type=INTERACTION_TYPE_REALTIME_RESPONSE,
        session_id="session-1",
        turn_id="turn-1",
        tool_call_id="call-1",
    ):
        create_tool_call_statistic(
            db,
            tool_name="web_search",
            success=True,
        )

    row = db.query(ToolCallStatistic).one()
    assert row.interaction_type == INTERACTION_TYPE_REALTIME_RESPONSE
    assert row.session_id == "session-1"
    assert row.turn_id == "turn-1"
    assert row.tool_call_id == "call-1"


def test_realtime_stats_export_audits_admin_download(monkeypatch):
    audit_calls = []
    completion_session = SimpleNamespace(close=lambda: None)

    class EmptyQuery:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def yield_per(self, *_args):
            return self

        def __iter__(self):
            return iter(())

    monkeypatch.setattr(
        realtime_router,
        "get_audit_request_ip",
        lambda *_args: "audit-ip",
    )
    monkeypatch.setattr(
        realtime_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    monkeypatch.setattr(
        realtime_router,
        "AuditSessionLocal",
        lambda: completion_session,
    )

    response = realtime_router.export_realtime_statistics(
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=SimpleNamespace(query=lambda *_args: EmptyQuery()),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
        days=14,
    )

    assert response.media_type == "application/json"
    assert audit_calls[0]["action"] == "EXPORT_REALTIME_STATS_STARTED"
    assert audit_calls[0]["category"] == "admin"
    assert audit_calls[0]["details"] == {
        "export_version": realtime_router.REALTIME_STATS_EXPORT_VERSION,
        "period_days": 14,
    }

    async def consume_response():
        return b"".join(
            [
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                async for chunk in response.body_iterator
            ]
        )

    payload = asyncio.run(consume_response())

    assert payload.endswith(b"]}}")
    assert audit_calls[1]["action"] == "EXPORT_REALTIME_STATS_COMPLETED"
    assert audit_calls[1]["category"] == "admin"
    assert audit_calls[1]["details"] == {
        "export_version": realtime_router.REALTIME_STATS_EXPORT_VERSION,
        "period_days": 14,
        "realtime_record_count": 0,
        "interaction_count": 0,
        "tool_call_count": 0,
    }


def test_realtime_stats_export_does_not_audit_completion_on_stream_failure(
    monkeypatch,
):
    audit_calls = []

    class RaisingQuery:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def yield_per(self, *_args):
            return self

        def __iter__(self):
            raise RuntimeError("query failed")

    monkeypatch.setattr(
        realtime_router,
        "get_audit_request_ip",
        lambda *_args: "audit-ip",
    )
    monkeypatch.setattr(
        realtime_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    response = realtime_router.export_realtime_statistics(
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=SimpleNamespace(query=lambda *_args: RaisingQuery()),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
        days=14,
    )

    async def consume_response():
        return b"".join(
            [
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                async for chunk in response.body_iterator
            ]
        )

    with pytest.raises(RuntimeError, match="query failed"):
        asyncio.run(consume_response())

    assert [call["action"] for call in audit_calls] == [
        "EXPORT_REALTIME_STATS_STARTED"
    ]
