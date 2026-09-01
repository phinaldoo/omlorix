"""Focused audit-logging coverage for model-feedback API operations."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.feedback import router as feedback_router
from app.feedback.schemas import FeedbackReactionRequest, FeedbackResponse


def _request():
    """Return the minimal request shape needed by feedback audit logging."""

    return SimpleNamespace(headers={"user-agent": "pytest"})


def _install_audit_capture(monkeypatch):
    """Capture audit calls while keeping client-IP resolution deterministic."""

    audit_calls: list[dict] = []
    monkeypatch.setattr(
        feedback_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    monkeypatch.setattr(
        feedback_router,
        "get_audit_request_ip",
        lambda _request, _db: "203.0.113.10",
    )
    return audit_calls


def test_react_to_message_audits_metadata_without_comment_text(monkeypatch):
    audit_calls = _install_audit_capture(monkeypatch)
    response = FeedbackResponse(
        id="feedback-1",
        model_id="model-1",
        message_id="message-1",
        user_id="user-1",
        reaction="thumbs_down",
        comment="Sensitive free-form explanation",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        feedback_router,
        "submit_feedback_reaction",
        lambda *_args: response,
    )

    result = feedback_router.react_to_message(
        payload=FeedbackReactionRequest(
            message_id="message-1",
            reaction="thumbs_down",
            comment="Sensitive free-form explanation",
        ),
        request=_request(),
        db=object(),
        db_log="audit-db",
        user=SimpleNamespace(id="user-1"),
    )

    assert result is response
    assert audit_calls == [
        {
            "db_log": "audit-db",
            "user_id": "user-1",
            "action": "SUBMIT_MODEL_FEEDBACK",
            "details": {
                "feedback_id": "feedback-1",
                "model_id": "model-1",
                "message_id": "message-1",
                "reaction": "thumbs_down",
                "has_comment": True,
            },
            "ip_address": "203.0.113.10",
            "user_agent": "pytest",
            "category": "model_feedback",
        }
    ]
    assert "Sensitive free-form explanation" not in str(audit_calls)


def test_react_to_message_audits_whitespace_only_comment_as_absent(monkeypatch):
    """The request schema permits whitespace, but audit normalization rejects it."""

    audit_calls = _install_audit_capture(monkeypatch)
    payload = FeedbackReactionRequest(
        message_id="message-1",
        reaction="thumbs_up",
        comment="   \t",
    )
    assert payload.comment == "   \t"

    response = FeedbackResponse(
        id="feedback-1",
        model_id="model-1",
        message_id="message-1",
        user_id="user-1",
        reaction="thumbs_up",
        comment=payload.comment,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        feedback_router,
        "submit_feedback_reaction",
        lambda *_args: response,
    )

    feedback_router.react_to_message(
        payload=payload,
        request=_request(),
        db=object(),
        db_log="audit-db",
        user=SimpleNamespace(id="user-1"),
    )

    assert audit_calls[0]["details"]["has_comment"] is False


def test_admin_feedback_reads_each_write_scoped_audit_metadata(monkeypatch):
    audit_calls = _install_audit_capture(monkeypatch)
    monkeypatch.setattr(
        feedback_router,
        "get_admin_feedback_overview",
        lambda _db, *, days: {"total_feedback": 7, "period_days": days},
    )
    monkeypatch.setattr(
        feedback_router,
        "get_admin_feedback_by_model",
        lambda _db, *, days: {"models": [{"id": "a"}, {"id": "b"}]},
    )
    monkeypatch.setattr(
        feedback_router,
        "get_admin_feedback_timeline",
        lambda _db, *, days: {"timeline": [{"date": "2026-08-16"}]},
    )
    monkeypatch.setattr(
        feedback_router,
        "get_admin_feedback_list",
        lambda *_args, **_kwargs: {
            "feedback": [{"comment": "Must not enter audit details"}],
            "total": 9,
        },
    )
    common = {
        "request": _request(),
        "db": object(),
        "db_log": "audit-db",
        "admin_user": SimpleNamespace(id="admin-1"),
    }

    feedback_router.admin_feedback_overview(days=14, **common)
    feedback_router.admin_feedback_by_model(days=14, **common)
    feedback_router.admin_feedback_timeline(days=14, **common)
    feedback_router.admin_feedback_list(
        days=14,
        page=2,
        per_page=25,
        model_id="model-1",
        reaction="thumbs_up",
        has_comment=True,
        **common,
    )

    assert [call["action"] for call in audit_calls] == [
        "VIEW_MODEL_FEEDBACK_OVERVIEW",
        "VIEW_MODEL_FEEDBACK_BY_MODEL",
        "VIEW_MODEL_FEEDBACK_TIMELINE",
        "LIST_MODEL_FEEDBACK",
    ]
    assert audit_calls[-1]["details"] == {
        "days": 14,
        "page": 2,
        "per_page": 25,
        "model_id": "model-1",
        "reaction": "thumbs_up",
        "has_comment": True,
        "returned_count": 1,
        "total_count": 9,
    }
    assert "Must not enter audit details" not in str(audit_calls)


def test_admin_feedback_export_and_deletes_are_audited(monkeypatch):
    audit_calls = _install_audit_capture(monkeypatch)
    monkeypatch.setattr(
        feedback_router,
        "export_model_feedback",
        lambda _db: {
            "export_version": 1.0,
            "data": {"feedback": [], "total_count": 4},
        },
    )
    monkeypatch.setattr(
        feedback_router,
        "delete_feedback_by_id_with_audit_context",
        lambda _db, feedback_id: (
            {"status": "success", "deleted_id": feedback_id},
            {
                "feedback_id": feedback_id,
                "model_id": "model-1",
                "message_id": "message-1",
                "feedback_user_id": "user-1",
                "reaction": "thumbs_up",
                "has_comment": True,
            },
        ),
    )
    monkeypatch.setattr(
        feedback_router,
        "delete_feedback_for_last_days",
        lambda _db, days: {
            "status": "success",
            "deleted_count": 3,
            "days": days,
        },
    )
    common = {
        "request": _request(),
        "db": object(),
        "db_log": "audit-db",
        "admin_user": SimpleNamespace(id="admin-1"),
    }

    feedback_router.admin_export_feedback(**common)
    feedback_router.admin_delete_feedback(feedback_id="feedback-1", **common)
    feedback_router.admin_delete_feedback_bulk(days=30, **common)

    assert [call["action"] for call in audit_calls] == [
        "EXPORT_MODEL_FEEDBACK",
        "DELETE_MODEL_FEEDBACK",
        "BULK_DELETE_MODEL_FEEDBACK",
    ]
    assert audit_calls[0]["details"] == {
        "export_version": 1.0,
        "total_count": 4,
        "sensitivity_category": "user_feedback",
    }
    assert audit_calls[1]["details"]["feedback_user_id"] == "user-1"
    assert audit_calls[2]["details"] == {"days": 30, "deleted_count": 3}
