"""Database models and persistence operations for response feedback."""

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    and_,
    case,
    cast,
    func,
)
from sqlalchemy.orm import Session

from app.chats.models import ChatMessages
from app.database import Base
from app.llm.models import Models
from app.users.models import User


class ModelFeedback(Base):
    """Persist one user's reaction to one assistant message."""

    __tablename__ = "model_feedback"
    __table_args__ = (
        Index("ix_model_feedback_model_id", "model_id"),
        Index("ix_model_feedback_message_id", "message_id"),
        Index("ix_model_feedback_user_id", "user_id"),
        UniqueConstraint(
            "message_id", "user_id", name="uq_model_feedback_message_user"
        ),
    )

    id = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    model_id = Column(String, nullable=False)
    message_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    reaction = Column(String, nullable=False)
    comment = Column(String, nullable=True)
    created_at = Column(
        DateTime, nullable=False, server_default=func.now(), default=func.now()
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
    )


def get_message_model_id(db: Session, message_id: str) -> str | None:
    """Return the model ID for a message without loading its large content fields."""

    return (
        db.query(ChatMessages.model_id).filter(ChatMessages.id == message_id).scalar()
    )


def get_feedback_by_id(db: Session, feedback_id: str) -> ModelFeedback | None:
    """Return feedback by primary key."""

    return db.query(ModelFeedback).filter(ModelFeedback.id == feedback_id).first()


def get_feedback_by_message_and_user(
    db: Session,
    message_id: str,
    user_id: str,
) -> ModelFeedback | None:
    """Return the unique feedback row for a message and user."""

    return (
        db.query(ModelFeedback)
        .filter(
            ModelFeedback.message_id == message_id,
            ModelFeedback.user_id == user_id,
        )
        .first()
    )


def upsert_feedback(
    db: Session,
    model_id: str,
    message_id: str,
    user_id: str,
    reaction: str,
    comment: str | None,
) -> ModelFeedback:
    """Create feedback or update the existing message/user reaction."""

    feedback = get_feedback_by_message_and_user(db, message_id, user_id)
    if feedback is None:
        feedback = ModelFeedback(
            model_id=model_id,
            message_id=message_id,
            user_id=user_id,
            reaction=reaction,
            comment=comment,
        )
        db.add(feedback)
    else:
        feedback.model_id = model_id
        feedback.reaction = reaction
        feedback.comment = comment

    db.commit()
    db.refresh(feedback)
    return feedback


def _feedback_cutoff(days: int) -> datetime:
    """Return the inclusive UTC cutoff for a rolling number of days."""

    return datetime.now(timezone.utc) - timedelta(days=days)


def _approval_percentage(thumbs_up: int, total: int) -> float:
    """Calculate the rounded approval percentage used by admin reports."""

    return round((thumbs_up / total * 100), 1) if total > 0 else 0


def get_admin_feedback_overview(db: Session, days: int = 30) -> dict:
    """Return the feedback overview shown on the admin dashboard."""

    # Compute the complete overview in one database round trip. Conditional
    # sums replace the previous series of independent count queries.
    row = (
        db.query(
            func.count(ModelFeedback.id).label("total_feedback"),
            func.sum(case((ModelFeedback.reaction == "thumbs_up", 1), else_=0)).label(
                "thumbs_up"
            ),
            func.sum(case((ModelFeedback.reaction == "thumbs_down", 1), else_=0)).label(
                "thumbs_down"
            ),
            func.sum(
                case(
                    (
                        and_(
                            ModelFeedback.comment.isnot(None),
                            ModelFeedback.comment != "",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("with_comments"),
            func.count(func.distinct(ModelFeedback.user_id)).label("unique_users"),
            func.count(func.distinct(ModelFeedback.model_id)).label("unique_models"),
        )
        .filter(ModelFeedback.created_at >= _feedback_cutoff(days))
        .one()
    )

    total = row.total_feedback or 0
    thumbs_up = row.thumbs_up or 0
    return {
        "total_feedback": total,
        "thumbs_up": thumbs_up,
        "thumbs_down": row.thumbs_down or 0,
        "with_comments": row.with_comments or 0,
        "unique_users": row.unique_users or 0,
        "unique_models": row.unique_models or 0,
        "approval_rate": _approval_percentage(thumbs_up, total),
        "period_days": days,
    }


def get_admin_feedback_by_model(db: Session, days: int = 30) -> dict:
    """Return aggregate feedback statistics grouped by model."""

    # Join model metadata in the aggregate query to avoid one model lookup for
    # every row in the result set.
    rows = (
        db.query(
            ModelFeedback.model_id,
            Models.name.label("model_name"),
            Models.provider.label("provider"),
            func.count(ModelFeedback.id).label("total"),
            func.sum(case((ModelFeedback.reaction == "thumbs_up", 1), else_=0)).label(
                "thumbs_up"
            ),
            func.sum(case((ModelFeedback.reaction == "thumbs_down", 1), else_=0)).label(
                "thumbs_down"
            ),
            func.sum(case((ModelFeedback.comment.isnot(None), 1), else_=0)).label(
                "with_comments"
            ),
        )
        .outerjoin(Models, Models.id == ModelFeedback.model_id)
        .filter(ModelFeedback.created_at >= _feedback_cutoff(days))
        .group_by(ModelFeedback.model_id, Models.name, Models.provider)
        .order_by(func.count(ModelFeedback.id).desc())
        .all()
    )

    models = []
    for row in rows:
        total = row.total or 0
        thumbs_up = row.thumbs_up or 0
        models.append(
            {
                "model_id": row.model_id,
                "model_name": row.model_name or row.model_id,
                "provider": row.provider or "unknown",
                "total": total,
                "thumbs_up": thumbs_up,
                "thumbs_down": row.thumbs_down or 0,
                "with_comments": row.with_comments or 0,
                "approval_rate": _approval_percentage(thumbs_up, total),
            }
        )
    return {"models": models}


def get_admin_feedback_timeline(db: Session, days: int = 30) -> dict:
    """Return daily feedback statistics for the admin dashboard."""

    date_bucket = cast(ModelFeedback.created_at, Date)
    rows = (
        db.query(
            date_bucket.label("date"),
            func.count(ModelFeedback.id).label("total"),
            func.sum(case((ModelFeedback.reaction == "thumbs_up", 1), else_=0)).label(
                "thumbs_up"
            ),
            func.sum(case((ModelFeedback.reaction == "thumbs_down", 1), else_=0)).label(
                "thumbs_down"
            ),
        )
        .filter(ModelFeedback.created_at >= _feedback_cutoff(days))
        .group_by(date_bucket)
        .order_by(date_bucket)
        .all()
    )

    timeline = []
    for row in rows:
        total = row.total or 0
        thumbs_up = row.thumbs_up or 0
        timeline.append(
            {
                "date": row.date.isoformat() if row.date else None,
                "total": total,
                "thumbs_up": thumbs_up,
                "thumbs_down": row.thumbs_down or 0,
                "approval_rate": _approval_percentage(thumbs_up, total),
            }
        )
    return {"timeline": timeline}


def get_admin_feedback_list(
    db: Session,
    days: int = 30,
    page: int = 1,
    per_page: int = 20,
    model_id: str | None = None,
    reaction: str | None = None,
    has_comment: bool | None = None,
) -> dict:
    """Return a filtered and paginated admin feedback list."""

    # Fetch related display fields in the same query so the admin list does
    # not execute separate model and user queries for every feedback item.
    query = (
        db.query(
            ModelFeedback.id,
            ModelFeedback.model_id,
            Models.name.label("model_name"),
            Models.provider.label("provider"),
            ModelFeedback.user_id,
            User.email.label("user_email"),
            ModelFeedback.reaction,
            ModelFeedback.comment,
            ModelFeedback.created_at,
        )
        .outerjoin(Models, Models.id == ModelFeedback.model_id)
        .outerjoin(User, User.id == ModelFeedback.user_id)
        .filter(ModelFeedback.created_at >= _feedback_cutoff(days))
    )

    if model_id:
        query = query.filter(ModelFeedback.model_id == model_id)
    if reaction:
        query = query.filter(ModelFeedback.reaction == reaction)
    if has_comment is True:
        query = query.filter(
            ModelFeedback.comment.isnot(None),
            ModelFeedback.comment != "",
        )
    elif has_comment is False:
        query = query.filter(
            (ModelFeedback.comment.is_(None)) | (ModelFeedback.comment == "")
        )

    total = query.count()
    rows = (
        query.order_by(ModelFeedback.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    feedback = [
        {
            "id": row.id,
            "model_id": row.model_id,
            "model_name": row.model_name or row.model_id,
            "provider": row.provider or "unknown",
            "user_id": row.user_id,
            "user_email": row.user_email,
            "reaction": row.reaction,
            "comment": row.comment,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {
        "feedback": feedback,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


def delete_feedback(db: Session, feedback: ModelFeedback) -> None:
    """Delete one feedback row and commit the transaction."""

    db.delete(feedback)
    db.commit()


def delete_feedback_for_last_days(db: Session, days: int) -> dict:
    """Delete feedback created during the requested rolling period."""

    cutoff = _feedback_cutoff(days)
    deleted_count = (
        db.query(ModelFeedback)
        .filter(ModelFeedback.created_at >= cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {
        "status": "success",
        "deleted_count": deleted_count,
        "days": days,
    }


def get_all_feedback(db: Session) -> list[ModelFeedback]:
    """Return every feedback row in reverse chronological order for export."""

    return db.query(ModelFeedback).order_by(ModelFeedback.created_at.desc()).all()
