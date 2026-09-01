"""Database model for personal and shared project memories."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
)

from app.database import Base


class Memory(Base):
    """A durable memory owned by exactly one user or one project.

    Keeping both scopes in one table lets the service, API, imports, tools, and
    context builder share one persistence path.  The database constraint keeps
    an invalid owner combination from being persisted even if a caller bypasses
    the service layer.
    """

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND project_id IS NULL) OR "
            "(user_id IS NULL AND project_id IS NOT NULL)",
            name="ck_memories_exactly_one_scope",
        ),
        # PostgreSQL and SQLite both allow repeated NULL values in unique
        # indexes, so these two indexes enforce uniqueness independently for
        # personal and project rows without dialect-specific partial indexes.
        Index("uq_memories_user_content_key", "user_id", "content_key", unique=True),
        Index(
            "uq_memories_project_content_key", "project_id", "content_key", unique=True
        ),
        Index("ix_memories_user_updated", "user_id", "updated_at", "created_at"),
        Index("ix_memories_project_updated", "project_id", "updated_at", "created_at"),
    )

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    project_id = Column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    content = Column(String, nullable=False)
    content_key = Column(String, nullable=False)
    source_date = Column(Date, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
