from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScimUserLink(Base):
    __tablename__ = "scim_user_links"
    __table_args__ = (
        Index("ix_scim_user_links_external_id", "external_id"),
        UniqueConstraint("user_id", name="uq_scim_user_links_user_id"),
        UniqueConstraint("external_id", name="uq_scim_user_links_external_id"),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    external_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class ScimGroupLink(Base):
    __tablename__ = "scim_group_links"
    __table_args__ = (
        Index("ix_scim_group_links_external_id", "external_id"),
        UniqueConstraint("group_id", name="uq_scim_group_links_group_id"),
        UniqueConstraint("external_id", name="uq_scim_group_links_external_id"),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    group_id = Column(String, ForeignKey("groups.id"), nullable=False)
    external_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class ScimGroupMembership(Base):
    __tablename__ = "scim_group_memberships"
    __table_args__ = (
        Index("ix_scim_group_memberships_user", "user_id"),
        Index("ix_scim_group_memberships_group", "group_id"),
        Index("ix_scim_group_memberships_priority", "priority"),
        UniqueConstraint("user_id", "group_id", name="uq_scim_group_memberships_user_group"),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    group_id = Column(String, ForeignKey("groups.id"), nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
