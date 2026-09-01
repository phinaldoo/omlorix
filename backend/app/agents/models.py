from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
import uuid

from fastapi import HTTPException, status
from sqlalchemy import Column, DateTime, Index, JSON, String, UniqueConstraint, or_
from sqlalchemy import Integer as SAInteger
from sqlalchemy.orm import Session

from app.database import Base
from app.files.utils import delete_storage_reference


class ShareType(str, Enum):
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


class UserAgent(Base):
    __tablename__ = "user_agents"
    __table_args__ = (
        Index("ix_user_agents_user_id", "user_id"),
        Index("ix_user_agents_base_model_id", "base_model_id"),
        Index("ix_user_agents_created_at", "created_at"),
        Index("ix_user_agents_updated_at", "updated_at"),
    )

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    icon = Column(String, nullable=False, default="")
    base_model_id = Column(String, nullable=False, index=True)
    instruction = Column(String, nullable=False, default="")
    skill_id = Column(String, nullable=True)
    clone_share_id = Column(String, nullable=True, unique=True, index=True)
    live_share_id = Column(String, nullable=True, unique=True, index=True)
    collaborate_share_id = Column(String, nullable=True, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SharedUserAgentSubscription(Base):
    __tablename__ = "shared_user_agent_subscriptions"
    __table_args__ = (
        Index("ix_shared_user_agent_subscriptions_agent_id", "agent_id"),
        Index("ix_shared_user_agent_subscriptions_subscriber_id", "subscriber_id"),
        UniqueConstraint(
            "agent_id",
            "subscriber_id",
            name="uq_shared_user_agent_subscriptions_agent_subscriber",
        ),
    )

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String, nullable=False, index=True)
    subscriber_id = Column(String, nullable=False, index=True)
    share_type = Column(String, nullable=False, default=ShareType.LIVE.value)
    subscribed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class UserAgentAsset(Base):
    __tablename__ = "user_agent_assets"
    __table_args__ = (
        Index("ix_user_agent_assets_agent_id", "agent_id"),
        Index("ix_user_agent_assets_owner_user_id", "owner_user_id"),
        Index("ix_user_agent_assets_created_at", "created_at"),
    )

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String, nullable=False, index=True)
    owner_user_id = Column(String, nullable=False, index=True)
    file_name = Column(String, nullable=False)
    storage_provider = Column(String, nullable=False, default="local")
    storage_key = Column(String, nullable=False, default="")
    storage_meta = Column(JSON, nullable=True)
    file_category = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    file_size = Column(SAInteger, nullable=False)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


def _ensure_id(value: str, field_name: str) -> str:
    """Ensure ID is not empty and return normalized value."""
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} is required")
    return normalized


def get_user_agent_by_id(db: Session, agent_id: str) -> UserAgent | None:
    """Get user agent by ID."""
    normalized = _ensure_id(agent_id, "agent_id")
    return db.query(UserAgent).filter(UserAgent.id == normalized).first()


def get_owned_user_agent(db: Session, user_id: str, agent_id: str) -> UserAgent:
    """Get owned user agent by user ID and agent ID."""
    normalized_user_id = _ensure_id(user_id, "user_id")
    normalized_agent_id = _ensure_id(agent_id, "agent_id")
    agent = (
        db.query(UserAgent)
        .filter(UserAgent.id == normalized_agent_id, UserAgent.user_id == normalized_user_id)
        .first()
    )
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


def get_user_agent_subscription(db: Session, user_id: str, agent_id: str) -> SharedUserAgentSubscription | None:
    """Get user agent subscription by user ID and agent ID."""
    normalized_user_id = _ensure_id(user_id, "user_id")
    normalized_agent_id = _ensure_id(agent_id, "agent_id")
    return (
        db.query(SharedUserAgentSubscription)
        .filter(
            SharedUserAgentSubscription.agent_id == normalized_agent_id,
            SharedUserAgentSubscription.subscriber_id == normalized_user_id,
        )
        .first()
    )


def get_user_agent_by_share_id(
    db: Session,
    share_id: str,
    share_type: ShareType | None = None,
) -> UserAgent | None:
    """Get user agent by share ID and optional share type."""
    normalized_share_id = _ensure_id(share_id, "share_id")
    if share_type == ShareType.CLONE:
        return db.query(UserAgent).filter(UserAgent.clone_share_id == normalized_share_id).first()
    if share_type == ShareType.LIVE:
        return db.query(UserAgent).filter(UserAgent.live_share_id == normalized_share_id).first()
    if share_type == ShareType.COLLABORATE:
        return db.query(UserAgent).filter(UserAgent.collaborate_share_id == normalized_share_id).first()

    agent = db.query(UserAgent).filter(UserAgent.clone_share_id == normalized_share_id).first()
    if agent:
        return agent
    agent = db.query(UserAgent).filter(UserAgent.live_share_id == normalized_share_id).first()
    if agent:
        return agent
    return db.query(UserAgent).filter(UserAgent.collaborate_share_id == normalized_share_id).first()


def detect_agent_share_type_from_id(db: Session, share_id: str) -> ShareType | None:
    """Detect agent share type from share ID."""
    normalized_share_id = str(share_id or "").strip()
    if not normalized_share_id:
        return None
    if db.query(UserAgent).filter(UserAgent.clone_share_id == normalized_share_id).first():
        return ShareType.CLONE
    if db.query(UserAgent).filter(UserAgent.live_share_id == normalized_share_id).first():
        return ShareType.LIVE
    if db.query(UserAgent).filter(UserAgent.collaborate_share_id == normalized_share_id).first():
        return ShareType.COLLABORATE
    return None


def list_owned_user_agents(db: Session, user_id: str) -> list[UserAgent]:
    """List owned user agents by user ID."""
    normalized_user_id = _ensure_id(user_id, "user_id")
    return (
        db.query(UserAgent)
        .filter(UserAgent.user_id == normalized_user_id)
        .order_by(UserAgent.updated_at.desc(), UserAgent.created_at.desc())
        .all()
    )


def list_shared_user_agent_subscriptions(db: Session, user_id: str) -> list[SharedUserAgentSubscription]:
    """List shared user agent subscriptions by subscriber ID."""
    normalized_user_id = _ensure_id(user_id, "user_id")
    return (
        db.query(SharedUserAgentSubscription)
        .filter(SharedUserAgentSubscription.subscriber_id == normalized_user_id)
        .order_by(SharedUserAgentSubscription.subscribed_at.desc())
        .all()
    )


def list_user_agent_assets(db: Session, agent_id: str) -> list[UserAgentAsset]:
    """List user agent assets by agent ID."""
    normalized_agent_id = _ensure_id(agent_id, "agent_id")
    return (
        db.query(UserAgentAsset)
        .filter(UserAgentAsset.agent_id == normalized_agent_id)
        .order_by(UserAgentAsset.created_at.asc(), UserAgentAsset.id.asc())
        .all()
    )


def get_user_agent_asset(db: Session, asset_id: str) -> UserAgentAsset | None:
    """Get user agent asset by asset ID."""
    normalized_asset_id = _ensure_id(asset_id, "asset_id")
    return db.query(UserAgentAsset).filter(UserAgentAsset.id == normalized_asset_id).first()


def remove_skill_from_user_agents(db: Session, skill_id: str | None) -> int:
    """Clear a deleted skill from every saved custom agent that references it.

    Admin skills and user skills share the same ``skill_id`` column on saved
    agents.  The caller controls when this helper is used, so admin skill
    deletion can remove stale references without affecting unrelated deletes.
    """
    normalized_skill_id = str(skill_id or "").strip()
    if not normalized_skill_id:
        return 0

    agents = db.query(UserAgent).filter(UserAgent.skill_id == normalized_skill_id).all()
    for agent in agents:
        agent.skill_id = None
        agent.updated_at = datetime.now(timezone.utc)

    return len(agents)


def delete_user_linked_agents(
    db: Session,
    user_id: str,
    cleanup_actions: list[Callable[[], None]] | None = None,
) -> None:
    """Delete agents, assets, and subscriptions linked to a user."""
    normalized_user_id = _ensure_id(user_id, "user_id")
    owned_agent_ids = [
        agent_id for (agent_id,) in db.query(UserAgent.id).filter(UserAgent.user_id == normalized_user_id).all()
    ]

    asset_filters = [UserAgentAsset.owner_user_id == normalized_user_id]
    if owned_agent_ids:
        asset_filters.append(UserAgentAsset.agent_id.in_(owned_agent_ids))

    for asset in (
        db.query(UserAgentAsset)
        .filter(or_(*asset_filters))
        .all()
    ):
        cleanup_action = lambda storage_provider=asset.storage_provider, storage_key=asset.storage_key, owner_user_id=asset.owner_user_id, file_name=asset.file_name: delete_storage_reference(
            storage_provider=storage_provider,
            storage_key=storage_key,
            user_id=owner_user_id,
            file_name=file_name,
        )
        if cleanup_actions is None:
            cleanup_action()
        else:
            cleanup_actions.append(cleanup_action)
        db.delete(asset)

    if owned_agent_ids:
        (
            db.query(SharedUserAgentSubscription)
            .filter(SharedUserAgentSubscription.agent_id.in_(owned_agent_ids))
            .delete(synchronize_session=False)
        )
        for agent in db.query(UserAgent).filter(UserAgent.id.in_(owned_agent_ids)).all():
            db.delete(agent)

    (
        db.query(SharedUserAgentSubscription)
        .filter(SharedUserAgentSubscription.subscriber_id == normalized_user_id)
        .delete(synchronize_session=False)
    )


def migrate_user_agents_base_model(db: Session, source_model_id: str, target_model_id: str) -> int:
    """Update all user agents that reference ``source_model_id`` to use ``target_model_id``.

    Returns the number of agents updated.
    """
    if (
        not source_model_id
        or not target_model_id
        or source_model_id == target_model_id
    ):
        return 0

    updated = (
        db.query(UserAgent)
        .filter(UserAgent.base_model_id == source_model_id)
        .update(
            {
                UserAgent.base_model_id: target_model_id,
                UserAgent.updated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )
    return updated
