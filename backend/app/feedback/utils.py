"""Business logic and response transformations for model feedback."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.feedback import models as feedback_models
from app.feedback.schemas import (
    FeedbackReactionRequest,
    FeedbackResponse,
)
from app.groups.init import get_user_group_setting_value
from app.llm.models import get_model
from app.llm.utils import ensure_user_access_to_model


current_model_feedback_export_version = 1.0


def has_meaningful_feedback_comment(comment: str | None) -> bool:
    """Return whether a feedback comment contains non-whitespace text.

    Keeping this rule in one helper ensures persistence normalization and audit
    metadata cannot disagree about whitespace-only comments.
    """

    return bool(comment and comment.strip())


def _serialize_datetime_value(value: datetime | None) -> str | None:
    """Serialize a datetime as a normalized UTC ISO 8601 value."""

    if not value:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def submit_feedback_reaction(
    user_id: str,
    payload: FeedbackReactionRequest,
    db: Session,
) -> FeedbackResponse:
    """Create or update the authenticated user's reaction to a message."""

    if not bool(
        get_user_group_setting_value(
            user_id,
            "chat",
            "allow_rate_response",
            db,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Response rating is disabled for your group.",
        )

    # Only the model ID is needed from the source message. Keeping this query
    # in the persistence layer avoids loading large message content fields.
    model_id = feedback_models.get_message_model_id(db, payload.message_id)
    if not model_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    # Model lookup and access validation remain part of the business workflow;
    # their database implementation belongs to the respective model features.
    db_model = get_model(db, model_id)
    ensure_user_access_to_model(user_id, db_model.id, db)

    normalized_comment = None
    if has_meaningful_feedback_comment(payload.comment):
        normalized_comment = payload.comment.strip()

    feedback = feedback_models.upsert_feedback(
        db,
        model_id=db_model.id,
        message_id=payload.message_id,
        user_id=user_id,
        reaction=payload.reaction.value,
        comment=normalized_comment,
    )

    return FeedbackResponse.model_validate(feedback)


def delete_feedback_by_id(db: Session, feedback_id: str) -> dict:
    """Delete one feedback record or raise a not-found response."""

    result, _audit_context = delete_feedback_by_id_with_audit_context(db, feedback_id)
    return result


def delete_feedback_by_id_with_audit_context(
    db: Session,
    feedback_id: str,
) -> tuple[dict, dict]:
    """Delete feedback and return non-content metadata for an audit event.

    Audit metadata is captured before deletion so administrators can later
    identify the affected record without copying the potentially sensitive
    free-form comment into the audit database.
    """

    feedback = feedback_models.get_feedback_by_id(db, feedback_id)
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found",
        )

    audit_context = {
        "feedback_id": feedback.id,
        "model_id": feedback.model_id,
        "message_id": feedback.message_id,
        "feedback_user_id": feedback.user_id,
        "reaction": feedback.reaction,
        "has_comment": has_meaningful_feedback_comment(feedback.comment),
    }
    feedback_models.delete_feedback(db, feedback)
    return {"status": "success", "deleted_id": feedback_id}, audit_context


def export_model_feedback(db: Session) -> dict:
    """Serialize all feedback into the versioned export envelope."""

    rows = feedback_models.get_all_feedback(db)
    feedback_payload = [
        {
            "id": row.id,
            "model_id": row.model_id,
            "message_id": row.message_id,
            "user_id": row.user_id,
            "reaction": row.reaction,
            "comment": row.comment,
            "created_at": _serialize_datetime_value(row.created_at),
            "updated_at": _serialize_datetime_value(row.updated_at),
        }
        for row in rows
    ]

    return {
        "export_type": "model_feedback",
        "export_version": current_model_feedback_export_version,
        "exported_at": datetime.now(timezone.utc)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        ),
        "data": {
            "feedback": feedback_payload,
            "total_count": len(feedback_payload),
        },
    }
