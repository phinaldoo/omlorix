from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.feedback import utils as feedback_utils
from app.feedback.schemas import (
    FeedbackReaction,
    FeedbackReactionRequest,
)


def test_feedback_reaction_request_validates_required_message_and_comment_length():
    with pytest.raises(ValidationError):
        FeedbackReactionRequest(message_id="", reaction=FeedbackReaction.thumbs_up)

    with pytest.raises(ValidationError):
        FeedbackReactionRequest(
            message_id="msg-1", reaction=FeedbackReaction.thumbs_up, comment="x" * 2001
        )


def test_serialize_datetime_value_normalizes_naive_and_aware_values():
    naive = datetime(2026, 5, 31, 12, 30, 0)
    aware = datetime(2026, 5, 31, 14, 30, 0, tzinfo=timezone.utc)

    assert feedback_utils._serialize_datetime_value(naive) == "2026-05-31T12:30:00Z"
    assert feedback_utils._serialize_datetime_value(aware) == "2026-05-31T14:30:00Z"
    assert feedback_utils._serialize_datetime_value(None) is None


def test_submit_feedback_reaction_rejects_when_group_rating_disabled(monkeypatch):
    monkeypatch.setattr(
        feedback_utils, "get_user_group_setting_value", lambda *args, **kwargs: False
    )

    with pytest.raises(HTTPException) as exc:
        feedback_utils.submit_feedback_reaction(
            "user-1",
            FeedbackReactionRequest(
                message_id="msg-1", reaction=FeedbackReaction.thumbs_up
            ),
            object(),
        )

    assert exc.value.status_code == 403


def test_submit_feedback_reaction_creates_new_feedback_with_trimmed_comment(
    monkeypatch,
):
    db = object()
    created_at = datetime(2026, 5, 31, 12, tzinfo=timezone.utc)
    create_calls = []
    access_calls = []

    monkeypatch.setattr(
        feedback_utils, "get_user_group_setting_value", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        feedback_utils,
        "get_model",
        lambda db, model_id: SimpleNamespace(
            id=model_id, name="Model", provider="openai"
        ),
    )
    monkeypatch.setattr(
        feedback_utils,
        "ensure_user_access_to_model",
        lambda user_id, model_id, db: access_calls.append((user_id, model_id)),
    )
    monkeypatch.setattr(
        feedback_utils.feedback_models,
        "get_message_model_id",
        lambda db, message_id: "model-1",
    )

    def fake_upsert(db, *, model_id, message_id, user_id, reaction, comment):
        create_calls.append(
            {
                "model_id": model_id,
                "message_id": message_id,
                "user_id": user_id,
                "reaction": reaction,
                "comment": comment,
            }
        )
        return SimpleNamespace(
            id="feedback-1",
            model_id=model_id,
            message_id=message_id,
            user_id=user_id,
            reaction=reaction,
            comment=comment,
            created_at=created_at,
            updated_at=created_at,
        )

    monkeypatch.setattr(
        feedback_utils.feedback_models,
        "upsert_feedback",
        fake_upsert,
    )

    response = feedback_utils.submit_feedback_reaction(
        "user-1",
        FeedbackReactionRequest(
            message_id="msg-1",
            reaction=FeedbackReaction.thumbs_down,
            comment="  useful note  ",
        ),
        db,
    )

    assert access_calls == [("user-1", "model-1")]
    assert create_calls == [
        {
            "model_id": "model-1",
            "message_id": "msg-1",
            "user_id": "user-1",
            "reaction": "thumbs_down",
            "comment": "useful note",
        }
    ]
    assert response.id == "feedback-1"
    assert response.model_id == "model-1"
    assert response.message_id == "msg-1"
    assert response.user_id == "user-1"
    assert response.reaction == FeedbackReaction.thumbs_down
    assert response.comment == "useful note"
    assert response.created_at == created_at
    assert response.updated_at == created_at


def test_export_model_feedback_serializes_rows_in_export_envelope(monkeypatch):
    created_at = datetime(2026, 5, 31, 12, tzinfo=timezone.utc)
    db = object()
    rows = [
        SimpleNamespace(
            id="feedback-1",
            model_id="model-1",
            message_id="msg-1",
            user_id="user-1",
            reaction="thumbs_up",
            comment=None,
            created_at=created_at,
            updated_at=None,
        )
    ]
    monkeypatch.setattr(
        feedback_utils.feedback_models, "get_all_feedback", lambda db: rows
    )

    exported = feedback_utils.export_model_feedback(db)

    assert exported["export_type"] == "model_feedback"
    assert (
        exported["export_version"]
        == feedback_utils.current_model_feedback_export_version
    )
    assert exported["data"]["total_count"] == 1
    assert exported["data"]["feedback"][0]["created_at"] == "2026-05-31T12:00:00Z"
    assert exported["data"]["feedback"][0]["updated_at"] is None


def test_delete_feedback_audit_context_excludes_comment_text(monkeypatch):
    """Deletion retains useful identifiers without retaining comment content."""

    feedback = SimpleNamespace(
        id="feedback-1",
        model_id="model-1",
        message_id="message-1",
        user_id="user-1",
        reaction="thumbs_down",
        comment="Sensitive feedback comment",
    )
    monkeypatch.setattr(
        feedback_utils.feedback_models,
        "get_feedback_by_id",
        lambda _db, _feedback_id: feedback,
    )
    deleted: list[object] = []
    monkeypatch.setattr(
        feedback_utils.feedback_models,
        "delete_feedback",
        lambda _db, row: deleted.append(row),
    )

    result, audit_context = feedback_utils.delete_feedback_by_id_with_audit_context(
        object(),
        "feedback-1",
    )

    assert result == {"status": "success", "deleted_id": "feedback-1"}
    assert deleted == [feedback]
    assert audit_context == {
        "feedback_id": "feedback-1",
        "model_id": "model-1",
        "message_id": "message-1",
        "feedback_user_id": "user-1",
        "reaction": "thumbs_down",
        "has_comment": True,
    }
    assert "Sensitive feedback comment" not in str(audit_context)
