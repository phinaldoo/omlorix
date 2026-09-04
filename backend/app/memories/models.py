"""Database model for personal and shared project memories."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _legacy_memory_key(context) -> str:
    """Keep direct ORM inserts compatible with the pre-lifecycle model."""

    parameters = context.get_current_parameters() if context is not None else {}
    memory_id = str(parameters.get("id") or uuid.uuid4().hex)
    return f"legacy.{memory_id[:113]}"


def _default_review_at() -> datetime:
    return _utcnow() + timedelta(days=180)


def _default_expires_at() -> datetime:
    return _utcnow() + timedelta(days=540)


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
        Index("uq_memories_user_memory_key", "user_id", "memory_key", unique=True),
        Index(
            "uq_memories_project_memory_key",
            "project_id",
            "memory_key",
            unique=True,
        ),
        Index("ix_memories_expiry", "status", "expires_at", "user_id"),
        Index("ix_memories_source_message", "source_message_id"),
    )

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    project_id = Column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    content = Column(String, nullable=False)
    content_key = Column(String, nullable=False)
    # ``memory_key`` is a semantic slot (for example ``identity.location``).
    # It lets consolidation update a fact in place instead of accumulating
    # paraphrases. Manually-created/imported facts receive a stable unique key.
    memory_key = Column(String(120), nullable=False, default=_legacy_memory_key)
    kind = Column(String(32), nullable=False, default="other", server_default="other")
    stability = Column(
        String(16), nullable=False, default="slow", server_default="slow"
    )
    importance = Column(Integer, nullable=False, default=3, server_default="3")
    confidence = Column(Float, nullable=False, default=1.0, server_default="1")
    sensitivity = Column(
        String(16), nullable=False, default="normal", server_default="normal"
    )
    status = Column(
        String(16), nullable=False, default="active", server_default="active"
    )
    version = Column(Integer, nullable=False, default=1, server_default="1")
    source_date = Column(Date, nullable=True)
    source_message_id = Column(String, nullable=True)
    source_excerpt = Column(String(500), nullable=True)
    # Evidence time is separate from ``updated_at`` so out-of-order background
    # jobs cannot let an older chat turn overwrite a newer fact.
    evidence_at = Column(DateTime(timezone=True), nullable=True)
    last_confirmed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    review_at = Column(DateTime(timezone=True), nullable=False, default=_default_review_at)
    expires_at = Column(DateTime(timezone=True), nullable=False, default=_default_expires_at)
    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    @property
    def lifecycle_state(self) -> str:
        now = datetime.now(timezone.utc)
        review_at = self.review_at
        if review_at is not None and review_at.tzinfo is None:
            review_at = review_at.replace(tzinfo=timezone.utc)
        return "review" if review_at is not None and review_at <= now else "fresh"

    @property
    def freshness(self) -> float:
        half_lives = {"stable": 540, "slow": 180, "changing": 45, "ephemeral": 7}
        confirmed = self.last_confirmed_at or self.updated_at or self.created_at
        if confirmed is None:
            return 0.0
        if confirmed.tzinfo is None:
            confirmed = confirmed.replace(tzinfo=timezone.utc)
        age_days = max(
            0.0,
            (datetime.now(timezone.utc) - confirmed).total_seconds() / 86_400,
        )
        half_life = half_lives.get(str(self.stability), 180)
        return round(math.exp(-math.log(2) * age_days / half_life), 4)


class MemoryDeletion(Base):
    """Content-free replay guard; excluded from portable memory exports.

    Retained longer than the maximum accepted source age, then swept. Full
    database backups preserve these operational records alongside worker jobs.
    """

    __tablename__ = "memory_deletions"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND project_id IS NULL) OR "
            "(user_id IS NULL AND project_id IS NOT NULL)",
            name="ck_memory_deletions_exactly_one_scope",
        ),
        Index("ix_memory_deletions_user_key", "user_id", "memory_key", "deleted_at"),
        Index("ix_memory_deletions_project_key", "project_id", "memory_key", "deleted_at"),
        Index("ix_memory_deletions_deleted", "deleted_at", "memory_id"),
    )

    # Use the original fact ID, or a new guard ID for forget-before-create.
    memory_id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    memory_key = Column(String(120), nullable=False)
    version = Column(Integer, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=False)


class MemoryProfile(Base):
    """One bounded materialized full-memory document per user.

    Atomic rows remain the source of truth. This row makes attaching the whole
    profile to every model request a single indexed lookup while retaining the
    exact fact versions from which it was built.
    """

    __tablename__ = "memory_profiles"
    __table_args__ = (Index("ix_memory_profiles_updated", "updated_at"),)

    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    content = Column(String, nullable=False, default="")
    version = Column(Integer, nullable=False, default=0)
    fact_versions = Column(JSON, nullable=False, default=list)
    active_fact_count = Column(Integer, nullable=False, default=0)
    review_fact_count = Column(Integer, nullable=False, default=0)
    source_revision = Column(Integer, nullable=True)
    # Earliest future review/expiry boundary represented by ``content``.
    # Readers can fall back to atomic facts if a maintenance cycle has not yet
    # rematerialized the profile after this timestamp.
    next_transition_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


class MemoryState(Base):
    """Operational state, independent of the disposable profile projection."""

    __tablename__ = "memory_states"
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    facts_revision = Column(Integer, nullable=False, default=0, server_default="0")
    last_processed_message_id = Column(String, nullable=True)
    last_source_at = Column(DateTime(timezone=True), nullable=True)
    last_run_status = Column(String(24), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
