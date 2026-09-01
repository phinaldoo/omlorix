import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llmstats.models import (
    BYOK_STATS_DEFAULT_RETENTION_DAYS,
    BYOK_STATS_MAX_RETENTION_DAYS,
    BYOK_STATS_MIN_RETENTION_DAYS,
    ERROR_MESSAGE_MAX_LENGTH,
    LLMGenerationStatistic,
    ToolCallStatistic,
    coerce_byok_stats_retention_days,
    export_llm_generation_stats,
    export_tool_call_stats,
    sanitize_provider_error_message,
)


def test_byok_error_sanitizer_redacts_common_secret_shapes():
    message = (
        "Provider failed api_key=sk-testsecret123456 token=abc.defghijklmnop.qrstuvwxyz "
        "authorization: Bearer live-token-value for admin@example.com"
    )

    sanitized = sanitize_provider_error_message(message)

    assert "sk-testsecret123456" not in sanitized
    assert "abc.defghijklmnop.qrstuvwxyz" not in sanitized
    assert "live-token-value" not in sanitized
    assert "admin@example.com" not in sanitized
    assert "api_key=[redacted]" in sanitized
    assert "authorization: Bearer [redacted]" in sanitized


def test_byok_error_sanitizer_truncates_long_provider_errors():
    sanitized = sanitize_provider_error_message("x" * (ERROR_MESSAGE_MAX_LENGTH + 50))

    assert sanitized.endswith("...")
    assert len(sanitized) == ERROR_MESSAGE_MAX_LENGTH + 3


def test_byok_retention_days_are_bounded():
    assert coerce_byok_stats_retention_days(None) == BYOK_STATS_DEFAULT_RETENTION_DAYS
    assert coerce_byok_stats_retention_days("0") == BYOK_STATS_MIN_RETENTION_DAYS
    assert coerce_byok_stats_retention_days("9999") == BYOK_STATS_MAX_RETENTION_DAYS
    assert coerce_byok_stats_retention_days("14") == 14


def test_byok_export_sanitizes_existing_error_messages():
    llm_stat = LLMGenerationStatistic(
        id="llm-1",
        model_name="Model",
        model_id="model",
        provider="openai",
        provider_id="provider-1",
        category="chat",
        status={"error": True, "error_message": "api_key=sk-exportsecret123456"},
        meta={},
        user_id="user-1",
        is_byok=True,
    )
    tool_stat = ToolCallStatistic(
        id="tool-1",
        tool_name="search",
        success=False,
        error_message="authorization: Bearer live-export-token",
        user_id="user-1",
        is_byok=True,
        meta={},
    )

    class QueryStub:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return self.rows

    db = MagicMock()
    db.query.side_effect = [QueryStub([llm_stat]), QueryStub([tool_stat])]

    llm_export = export_llm_generation_stats(db, user_id="user-1", is_byok=True)
    tool_export = export_tool_call_stats(db, user_id="user-1", is_byok=True)

    exported_status = llm_export["data"]["statistics"][0]["status"]
    exported_tool_error = tool_export["data"]["statistics"][0]["error_message"]
    assert "sk-exportsecret123456" not in exported_status["error_message"]
    assert exported_status["error_message"] == "api_key=[redacted]"
    assert "live-export-token" not in exported_tool_error
    assert exported_tool_error == "authorization: Bearer [redacted]"


def test_byok_export_audits_only_counts_and_version(monkeypatch):
    import app.llmstats.router as llmstats_router

    audit_calls = []
    monkeypatch.setattr(
        llmstats_router,
        "export_llm_generation_stats",
        lambda *_args, **_kwargs: {
            "data": {
                "total_count": 2,
                "statistics": [{"status": {"error_message": "private"}}],
            }
        },
    )
    monkeypatch.setattr(
        llmstats_router,
        "export_tool_call_stats",
        lambda *_args, **_kwargs: {
            "data": {
                "total_count": 3,
                "statistics": [{"error_message": "private"}],
            }
        },
    )
    monkeypatch.setattr(
        llmstats_router,
        "get_audit_request_ip",
        lambda *_args: "audit-ip",
    )
    monkeypatch.setattr(
        llmstats_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    response = llmstats_router.export_user_byok_stats(
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
    )

    assert response["data"]["llm_generation_stats"]["data"]["total_count"] == 2
    assert audit_calls[0]["action"] == "EXPORT_BYOK_USAGE_STATS"
    assert audit_calls[0]["details"] == {
        "export_version": llmstats_router.BYOK_USAGE_STATS_EXPORT_VERSION,
        "llm_stats_count": 2,
        "tool_stats_count": 3,
    }
    assert "statistics" not in audit_calls[0]["details"]


def test_retention_worker_rolls_back_and_closes_session_on_failure(monkeypatch):
    import app.llmstats.worker as worker

    db = MagicMock()
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        worker,
        "purge_expired_byok_statistics",
        MagicMock(side_effect=RuntimeError("boom")),
    )

    try:
        worker.run_byok_stats_retention_once()
        assert False, "expected retention worker failure"
    except RuntimeError:
        pass

    db.rollback.assert_called_once()
    db.close.assert_called_once()
