from datetime import datetime, timezone
import uuid
from enum import Enum
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import Column, DateTime, Index, Integer, String, and_, or_
from sqlalchemy.orm import Session

from app.database import Base
from app.settings.utils import get_public_url


class ShareType(str, Enum):
    """Types of prompt sharing."""
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


class Prompts(Base):
    __tablename__ = "prompts"
    __table_args__ = (
        Index("ix_prompts_user_updated", "user_id", "updated_at"),
    )

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=False, default="")
    content = Column(String, nullable=False, default="")
    # Prompt content uses a dedicated monotonic revision instead of updated_at.
    # Sharing operations also touch updated_at and must not invalidate an editor
    # that is otherwise working from the latest prompt content.
    revision = Column(Integer, nullable=False, default=1, server_default="1")
    last_edited_by_user_id = Column(String, nullable=True, index=True)
    clone_share_id = Column(String, nullable=True, index=True, unique=True)
    live_share_id = Column(String, nullable=True, index=True, unique=True)
    collaborate_share_id = Column(String, nullable=True, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
        nullable=False,
    )


class SharedPromptSubscription(Base):
    """Tracks which users have subscribed to (accepted) shared prompts."""
    __tablename__ = "shared_prompt_subscriptions"

    id = Column(String, primary_key=True, index=True)
    prompt_id = Column(String, nullable=False, index=True)
    subscriber_id = Column(String, nullable=False, index=True)
    share_type = Column(String, nullable=False, default="live")
    subscribed_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)


def _ensure_user_id(value: str, field_name: str = "user_id") -> str:
    """Ensure user ID is a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is required",
        )
    return value.strip()


def _datetime_to_iso(value: datetime | None) -> str | None:
    """Convert datetime to UTC ISO format string."""
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.isoformat()


def _get_user_prompt(db: Session, user_id: str, prompt_id: str) -> Prompts:
    """Get a user's prompt by ID, raising 404 if not found."""
    normalized_user_id = _ensure_user_id(user_id)
    normalized_prompt_id = _ensure_user_id(prompt_id, "prompt_id")
    prompt = (
        db.query(Prompts)
        .filter(Prompts.id == normalized_prompt_id, Prompts.user_id == normalized_user_id)
        .first()
    )
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    return prompt


def create_user_prompt(
    db: Session,
    user_id: str,
    title: str,
    description: str = "",
    content: str = "",
) -> Prompts:
    """Create a new user prompt."""
    normalized_user_id = _ensure_user_id(user_id)
    prompt_title = (title or "").strip()
    if not prompt_title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Prompt title is required")

    current_time = datetime.now(timezone.utc)
    prompt = Prompts(
        id=str(uuid.uuid4()),
        user_id=normalized_user_id,
        title=prompt_title,
        description=(description or "").strip(),
        content=(content or "").strip(),
        revision=1,
        last_edited_by_user_id=normalized_user_id,
        created_at=current_time,
        updated_at=current_time,
    )
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return prompt


def update_user_prompt(
    db: Session,
    user_id: str,
    prompt_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    content: str | None = None,
    expected_revision: int,
    actor_user_id: str,
) -> Prompts:
    """Atomically update a prompt when the caller's revision is current.

    The revision comparison is part of the UPDATE statement. A Python-only
    pre-check would still allow two transactions to read the same revision and
    silently overwrite each other when they later commit.
    """
    prompt = _get_user_prompt(db, user_id, prompt_id)
    normalized_actor_user_id = _ensure_user_id(actor_user_id, "actor_user_id")

    observed_revision = int(getattr(prompt, "revision", 1) or 1)
    if expected_revision != observed_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "prompt_revision_conflict",
                "message": "This prompt changed while you were editing. Review the latest version before saving again.",
                "current_revision": observed_revision,
            },
        )

    next_title = prompt.title
    next_description = prompt.description or ""
    next_content = prompt.content or ""

    if title is not None:
        title_value = title.strip()
        if not title_value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Prompt title is required")
        next_title = title_value

    if description is not None:
        next_description = description.strip() if isinstance(description, str) else ""

    if content is not None:
        next_content = content.strip() if isinstance(content, str) else ""

    if (
        next_title == prompt.title
        and next_description == (prompt.description or "")
        and next_content == (prompt.content or "")
    ):
        return prompt

    next_revision = observed_revision + 1
    edited_at = datetime.now(timezone.utc)
    updated_count = (
        db.query(Prompts)
        .filter(
            Prompts.id == prompt.id,
            Prompts.user_id == prompt.user_id,
            Prompts.revision == expected_revision,
        )
        .update(
            {
                Prompts.title: next_title,
                Prompts.description: next_description,
                Prompts.content: next_content,
                Prompts.revision: next_revision,
                Prompts.last_edited_by_user_id: normalized_actor_user_id,
                Prompts.updated_at: edited_at,
            },
            synchronize_session=False,
        )
    )
    if updated_count != 1:
        db.rollback()
        current_revision = (
            db.query(Prompts.revision)
            .filter(Prompts.id == prompt.id)
            .scalar()
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "prompt_revision_conflict",
                "message": "This prompt changed while you were editing. Review the latest version before saving again.",
                "current_revision": current_revision,
            },
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Bulk compare-and-swap updates bypass the identity map. Expire cached ORM
    # rows so callers always receive the committed prompt revision.
    db.expire_all()
    return db.query(Prompts).filter(Prompts.id == prompt.id).first()


def delete_user_prompt(db: Session, user_id: str, prompt_id: str) -> dict[str, str | bool]:
    """Delete a user prompt and its subscriptions."""
    prompt = _get_user_prompt(db, user_id, prompt_id)

    db.query(SharedPromptSubscription).filter(
        SharedPromptSubscription.prompt_id == prompt_id
    ).delete()
    db.delete(prompt)
    db.commit()
    return {"deleted": True, "prompt_id": prompt_id.strip()}


def _apply_pagination(query, *, limit: int | None = None, offset: int = 0):
    if isinstance(offset, int) and offset > 0:
        query = query.offset(offset)
    if isinstance(limit, int) and limit > 0:
        query = query.limit(limit)
    return query


def list_user_prompts(db: Session, user_id: str, *, limit: int | None = None, offset: int = 0) -> List[Prompts]:
    """List all prompts for a user."""
    normalized_user_id = _ensure_user_id(user_id)
    query = (
        db.query(Prompts)
        .filter(Prompts.user_id == normalized_user_id)
        .order_by(Prompts.updated_at.desc(), Prompts.created_at.desc())
    )
    return _apply_pagination(query, limit=limit, offset=offset).all()


# ============================================================================
# Prompt Sharing Functions
# ============================================================================

def _get_owner_display_name(db: Session, user_id: str) -> str:
    """Get display name for a user."""
    from app.users.models import User

    owner = db.query(User).filter(User.id == user_id).first()
    if not owner:
        return "Unknown"
    if owner.first_name or owner.last_name:
        return " ".join(filter(None, [owner.first_name, owner.last_name])).strip()
    return "Unknown"


def _get_share_id_field(share_type: ShareType) -> str:
    """Get the share ID field name for a share type."""
    return {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type, "live_share_id")


def _get_share_url_prefix(share_type: ShareType) -> str:
    """Get the URL prefix for a share type."""
    return {
        ShareType.CLONE: "/prompts/clone",
        ShareType.LIVE: "/prompts/live",
        ShareType.COLLABORATE: "/prompts/collaborate",
    }.get(share_type, "/prompts/live")


def create_prompt_share(db: Session, user_id: str, prompt_id: str, share_type: ShareType = ShareType.LIVE) -> dict:
    """Create a share link for a prompt."""
    prompt = _get_user_prompt(db, user_id, prompt_id)

    share_id_attr = _get_share_id_field(share_type)
    existing_share_id = getattr(prompt, share_id_attr, None)
    url_prefix = _get_share_url_prefix(share_type)
    base_url = get_public_url(db)

    if existing_share_id:
        return {
            "share_id": existing_share_id,
            "share_type": share_type.value,
            "share_url": f"{base_url}{url_prefix}/{existing_share_id}",
        }

    new_share_id = str(uuid.uuid4())
    setattr(prompt, share_id_attr, new_share_id)
    prompt.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "share_id": new_share_id,
        "share_type": share_type.value,
        "share_url": f"{base_url}{url_prefix}/{new_share_id}",
    }


def get_prompt_share_status(db: Session, user_id: str, prompt_id: str) -> dict:
    """Get the share status for a prompt."""
    prompt = _get_user_prompt(db, user_id, prompt_id)

    live_count = db.query(SharedPromptSubscription).filter(
        SharedPromptSubscription.prompt_id == prompt_id,
        SharedPromptSubscription.share_type == "live",
    ).count()

    collaborate_count = db.query(SharedPromptSubscription).filter(
        SharedPromptSubscription.prompt_id == prompt_id,
        SharedPromptSubscription.share_type == "collaborate",
    ).count()

    return {
        "clone_share_id": prompt.clone_share_id,
        "live_share_id": prompt.live_share_id,
        "collaborate_share_id": prompt.collaborate_share_id,
        "live_subscriber_count": live_count,
        "collaborate_subscriber_count": collaborate_count,
    }


def delete_prompt_share(db: Session, user_id: str, prompt_id: str, share_type: Optional[ShareType] = None) -> dict:
    """Delete a prompt share."""
    prompt = _get_user_prompt(db, user_id, prompt_id)

    if share_type is None:
        db.query(SharedPromptSubscription).filter(
            SharedPromptSubscription.prompt_id == prompt_id
        ).delete()
        prompt.clone_share_id = None
        prompt.live_share_id = None
        prompt.collaborate_share_id = None
    else:
        share_id_attr = _get_share_id_field(share_type)
        setattr(prompt, share_id_attr, None)

        if share_type in (ShareType.LIVE, ShareType.COLLABORATE):
            db.query(SharedPromptSubscription).filter(
                SharedPromptSubscription.prompt_id == prompt_id,
                SharedPromptSubscription.share_type == share_type.value,
            ).delete()

    prompt.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"ok": True, "share_type": share_type.value if share_type else "all"}


def get_shared_prompt_by_share_id(
    db: Session,
    share_id: str,
    share_type: Optional[ShareType] = None,
) -> Optional[Prompts]:
    """Get a shared prompt by share ID."""
    if not share_id:
        return None

    cleaned_id = share_id.strip()

    if share_type == ShareType.CLONE:
        return db.query(Prompts).filter(Prompts.clone_share_id == cleaned_id).first()
    if share_type == ShareType.LIVE:
        return db.query(Prompts).filter(Prompts.live_share_id == cleaned_id).first()
    if share_type == ShareType.COLLABORATE:
        return db.query(Prompts).filter(Prompts.collaborate_share_id == cleaned_id).first()

    prompt = db.query(Prompts).filter(Prompts.clone_share_id == cleaned_id).first()
    if prompt:
        return prompt
    prompt = db.query(Prompts).filter(Prompts.live_share_id == cleaned_id).first()
    if prompt:
        return prompt
    return db.query(Prompts).filter(Prompts.collaborate_share_id == cleaned_id).first()


def detect_share_type_from_id(db: Session, share_id: str) -> Optional[ShareType]:
    """Detect the share type from a share ID."""
    if not share_id:
        return None
    cleaned_id = share_id.strip()

    if db.query(Prompts).filter(Prompts.clone_share_id == cleaned_id).first():
        return ShareType.CLONE
    if db.query(Prompts).filter(Prompts.live_share_id == cleaned_id).first():
        return ShareType.LIVE
    if db.query(Prompts).filter(Prompts.collaborate_share_id == cleaned_id).first():
        return ShareType.COLLABORATE
    return None


def get_shared_prompt_preview(
    db: Session,
    share_id: str,
    share_type: Optional[ShareType] = None,
    requesting_user_id: Optional[str] = None,
) -> dict:
    """Get a preview of a shared prompt."""
    if not share_id:
        raise HTTPException(status_code=400, detail="share_id is required")

    prompt = get_shared_prompt_by_share_id(db, share_id, share_type)
    if not prompt:
        raise HTTPException(status_code=404, detail="Shared prompt not found")

    detected_type = detect_share_type_from_id(db, share_id) or share_type or ShareType.LIVE

    if requesting_user_id and prompt.user_id == requesting_user_id:
        raise HTTPException(status_code=400, detail="You cannot open your own shared prompt")

    if requesting_user_id and detected_type in (ShareType.LIVE, ShareType.COLLABORATE):
        already_subscribed = db.query(SharedPromptSubscription).filter(
            SharedPromptSubscription.prompt_id == prompt.id,
            SharedPromptSubscription.subscriber_id == requesting_user_id,
        ).first()
        if already_subscribed:
            raise HTTPException(status_code=409, detail="You already added this shared prompt")

    owner_name = _get_owner_display_name(db, prompt.user_id)

    return {
        "share_id": share_id,
        "share_type": detected_type.value,
        "title": prompt.title,
        "description": prompt.description,
        "content_preview": (prompt.content[:260] + "...") if prompt.content and len(prompt.content) > 260 else prompt.content,
        "owner_name": owner_name,
        "created_at": _datetime_to_iso(prompt.created_at),
        "updated_at": _datetime_to_iso(prompt.updated_at),
    }


def clone_shared_prompt(db: Session, user_id: str, share_id: str) -> Prompts:
    """Clone a shared prompt for a user."""
    prompt = get_shared_prompt_by_share_id(db, share_id, ShareType.CLONE)
    if not prompt:
        raise HTTPException(status_code=404, detail="Shared prompt not found or not available for cloning")

    if prompt.user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot clone your own prompt")

    cloned_at = datetime.now(timezone.utc)
    cloned_prompt = Prompts(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=prompt.title,
        description=prompt.description,
        content=prompt.content,
        revision=1,
        last_edited_by_user_id=user_id,
        created_at=cloned_at,
        updated_at=cloned_at,
    )
    db.add(cloned_prompt)
    db.commit()
    db.refresh(cloned_prompt)
    return cloned_prompt


def subscribe_to_shared_prompt(
    db: Session,
    subscriber_id: str,
    prompt_id: str,
    share_type: ShareType = ShareType.LIVE,
) -> SharedPromptSubscription:
    """Subscribe a user to a shared prompt."""
    if share_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Clone shares don't support subscriptions")
    existing = db.query(SharedPromptSubscription).filter(
        SharedPromptSubscription.prompt_id == prompt_id,
        SharedPromptSubscription.subscriber_id == subscriber_id,
    ).first()

    if existing:
        if existing.share_type != share_type.value:
            existing.share_type = share_type.value
            db.commit()
            db.refresh(existing)
        return existing

    subscription = SharedPromptSubscription(
        id=str(uuid.uuid4()),
        prompt_id=prompt_id,
        subscriber_id=subscriber_id,
        share_type=share_type.value,
        subscribed_at=datetime.now(timezone.utc),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def unsubscribe_from_shared_prompt(db: Session, subscriber_id: str, prompt_id: str) -> dict:
    """Unsubscribe a user from a shared prompt."""
    subscription = get_subscription_for_prompt(db, subscriber_id, prompt_id)
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")

    db.delete(subscription)
    db.commit()
    return {"ok": True, "deleted": True}


def get_subscribed_prompts(db: Session, user_id: str, *, limit: int | None = None, offset: int = 0) -> List[tuple]:
    """Get all prompts a user is subscribed to."""
    normalized_user_id = _ensure_user_id(user_id)
    query = (
        db.query(Prompts, SharedPromptSubscription)
        .join(SharedPromptSubscription, SharedPromptSubscription.prompt_id == Prompts.id)
        .filter(SharedPromptSubscription.subscriber_id == normalized_user_id)
        .filter(
            or_(
                and_(SharedPromptSubscription.share_type == "live", Prompts.live_share_id.isnot(None)),
                and_(SharedPromptSubscription.share_type == "collaborate", Prompts.collaborate_share_id.isnot(None)),
            )
        )
        .order_by(Prompts.updated_at.desc(), Prompts.created_at.desc())
    )
    return _apply_pagination(query, limit=limit, offset=offset).all()


def get_prompt_subscriber_count(db: Session, prompt_id: str, share_type: Optional[str] = None) -> int:
    """Get the subscriber count for a prompt."""
    query = db.query(SharedPromptSubscription).filter(
        SharedPromptSubscription.prompt_id == prompt_id
    )
    if share_type:
        query = query.filter(SharedPromptSubscription.share_type == share_type)
    return query.count()


def can_user_edit_prompt(db: Session, user_id: str, prompt_id: str) -> bool:
    """Check if a user can edit a prompt."""
    prompt = db.query(Prompts).filter(Prompts.id == prompt_id).first()
    if not prompt:
        return False
    if prompt.user_id == user_id:
        return True

    subscription = db.query(SharedPromptSubscription).filter(
        SharedPromptSubscription.prompt_id == prompt_id,
        SharedPromptSubscription.subscriber_id == user_id,
        SharedPromptSubscription.share_type == "collaborate",
    ).first()

    return subscription is not None


def get_subscription_for_prompt(db: Session, user_id: str, prompt_id: str) -> Optional[SharedPromptSubscription]:
    """Get a user's subscription to a prompt."""
    return db.query(SharedPromptSubscription).filter(
        SharedPromptSubscription.prompt_id == prompt_id,
        SharedPromptSubscription.subscriber_id == user_id,
    ).first()


def get_prompt_content_for_user(db: Session, user_id: str, prompt_id: str) -> Optional[dict[str, str]]:
    """Get prompt payload by ID for a user (own or subscribed)."""
    if not prompt_id or not isinstance(prompt_id, str):
        return None

    prompt_id = prompt_id.strip()
    if not prompt_id:
        return None

    own_prompt = db.query(Prompts).filter(
        Prompts.id == prompt_id,
        Prompts.user_id == user_id,
    ).first()
    if own_prompt:
        return {
            "title": own_prompt.title,
            "content": own_prompt.content,
            "description": own_prompt.description,
        }

    subscription = db.query(SharedPromptSubscription).filter(
        SharedPromptSubscription.prompt_id == prompt_id,
        SharedPromptSubscription.subscriber_id == user_id,
    ).first()
    if subscription:
        shared_prompt = db.query(Prompts).filter(Prompts.id == prompt_id).first()
        if shared_prompt:
            return {
                "title": shared_prompt.title,
                "content": shared_prompt.content,
                "description": shared_prompt.description,
            }

    return None
