from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)

from app.database import Base


RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_FAILED = "failed"

TERMINAL_RUN_STATUSES = {
    RUN_STATUS_COMPLETED,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_FAILED,
}


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for persisted run state."""

    return datetime.now(timezone.utc)


class DeepResearchRun(Base):
    """Durable state for one custom or native Deep Research execution."""

    __tablename__ = "deep_research_runs"
    __table_args__ = (
        Index("ix_deep_research_runs_user_id", "user_id"),
        Index("ix_deep_research_runs_chat_id", "chat_id"),
        Index("ix_deep_research_runs_generation_id", "generation_id"),
        Index("ix_deep_research_runs_status", "status"),
        Index("ix_deep_research_runs_phase", "phase"),
        Index("ix_deep_research_runs_updated_at", "updated_at"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # A run is part of its chat's durable transcript. Hard-deleting the chat
    # must therefore remove the database row as a final safety net; the chat
    # deletion helpers also remove the corresponding object-storage workspace.
    chat_id = Column(String, ForeignKey("chats.id", ondelete="CASCADE"), nullable=True)
    generation_id = Column(String, nullable=True)
    query = Column(Text, nullable=False)
    execution_mode = Column(String, nullable=False, default="custom")
    # Retained for import compatibility with development-era run records. New
    # runs always persist ``markdown`` and no longer expose an output choice.
    output_format = Column(String, nullable=False, default="markdown")
    status = Column(String, nullable=False, default=RUN_STATUS_RUNNING)
    phase = Column(String, nullable=False, default="starting")
    provider_id = Column(String, nullable=True)
    model_id = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    prompt_version = Column(String, nullable=False, default="v2")
    revision_round = Column(Integer, nullable=False, default=0)
    max_revision_rounds = Column(Integer, nullable=False, default=2)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    cancel_requested_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    updated_at = Column(DateTime, nullable=False, default=utc_now)
    final_report_path = Column(String, nullable=True)
    # Historical runs may still reference an HTML artifact. Keeping this
    # nullable column lets them remain readable without creating new HTML.
    final_html_path = Column(String, nullable=True)
    manifest_path = Column(String, nullable=True)
    error_code = Column(String, nullable=True)
    error_message_key = Column(String, nullable=True)
    config_snapshot = Column(JSON, nullable=False, default=dict)
    usage = Column(JSON, nullable=False, default=dict)
    quality_gate = Column(JSON, nullable=False, default=dict)
    result_meta = Column(JSON, nullable=False, default=dict)
    # Evidence and artifact metadata are bounded, run-local documents. Keeping
    # them on the run avoids two tables and repeated joins without changing the
    # external report/import format.
    evidence = Column(JSON, nullable=False, default=list)
    artifacts = Column(JSON, nullable=False, default=list)


@dataclass
class DeepResearchArtifact:
    """Typed view of one artifact stored in ``DeepResearchRun.artifacts``.

    This is deliberately not an ORM model. Callers keep convenient attribute
    access while persistence remains one JSON assignment on the parent run.
    """

    stable_id: str
    source_phase: str
    original_filename: str
    relative_path: str
    media_type: str = "application/octet-stream"
    kind: str = "other"
    file_id: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    caption: str | None = None
    alt_text: str | None = None
    source_url: str | None = None
    attribution: str | None = None
    license_name: str | None = None
    validation_status: str = "pending"
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DeepResearchArtifact:
        """Build a safe typed artifact from persisted JSON metadata."""

        return cls(
            stable_id=str(value.get("stable_id") or ""),
            source_phase=str(value.get("source_phase") or "unknown"),
            original_filename=str(value.get("original_filename") or "artifact"),
            relative_path=str(value.get("relative_path") or ""),
            media_type=str(value.get("media_type") or "application/octet-stream"),
            kind=str(value.get("kind") or "other"),
            file_id=str(value.get("file_id")) if value.get("file_id") else None,
            size_bytes=(
                int(value["size_bytes"])
                if value.get("size_bytes") is not None
                else None
            ),
            sha256=str(value.get("sha256")) if value.get("sha256") else None,
            caption=str(value.get("caption")) if value.get("caption") else None,
            alt_text=str(value.get("alt_text")) if value.get("alt_text") else None,
            source_url=(
                str(value.get("source_url")) if value.get("source_url") else None
            ),
            attribution=(
                str(value.get("attribution")) if value.get("attribution") else None
            ),
            license_name=(
                str(value.get("license_name"))
                if value.get("license_name")
                else None
            ),
            validation_status=str(value.get("validation_status") or "pending"),
            meta=dict(value.get("meta")) if isinstance(value.get("meta"), dict) else {},
            created_at=(
                str(value.get("created_at")) if value.get("created_at") else None
            ),
            updated_at=(
                str(value.get("updated_at")) if value.get("updated_at") else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe metadata for storage on the parent run."""

        return asdict(self)


def create_deep_research_run(
    db,
    *,
    user_id: str,
    query: str,
    chat_id: str | None,
    generation_id: str | None,
    execution_mode: str,
    output_format: str,
    provider_id: str | None,
    model_id: str | None,
    model_name: str | None,
    max_revision_rounds: int,
    config_snapshot: dict[str, Any],
) -> DeepResearchRun:
    """Create and commit one active inline Deep Research run."""

    now = utc_now()
    run = DeepResearchRun(
        user_id=str(user_id),
        query=query,
        chat_id=chat_id,
        generation_id=generation_id,
        execution_mode=execution_mode,
        output_format=output_format,
        provider_id=provider_id,
        model_id=model_id,
        model_name=model_name,
        max_revision_rounds=max_revision_rounds,
        config_snapshot=config_snapshot,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    try:
        db.commit()
        db.refresh(run)
    except Exception:
        # A flush/commit error leaves SQLAlchemy sessions unusable until an
        # explicit rollback. Restore the caller's session before propagating
        # the original database error so the tool pipeline can report it.
        db.rollback()
        raise
    return run


def get_deep_research_run(db, run_id: str) -> DeepResearchRun | None:
    """Return a run by ID without applying an ownership policy."""

    return db.query(DeepResearchRun).filter(DeepResearchRun.id == str(run_id)).first()


def get_user_deep_research_run(db, run_id: str, user_id: str) -> DeepResearchRun | None:
    """Return a run only when it belongs to the requested user."""

    return (
        db.query(DeepResearchRun)
        .filter(
            DeepResearchRun.id == str(run_id),
            DeepResearchRun.user_id == str(user_id),
        )
        .first()
    )


def get_user_deep_research_run_by_generation(
    db,
    generation_id: str,
    user_id: str,
) -> DeepResearchRun | None:
    """Resolve the Deep Research run attached to one user-owned chat stream."""

    return (
        db.query(DeepResearchRun)
        .filter(
            DeepResearchRun.generation_id == str(generation_id),
            DeepResearchRun.user_id == str(user_id),
        )
        .order_by(DeepResearchRun.created_at.desc())
        .first()
    )


def request_deep_research_cancellation(db, run: DeepResearchRun) -> DeepResearchRun:
    """Persist a cancellation request observed by the active chat generation."""

    if run.status in TERMINAL_RUN_STATUSES:
        return run
    now = utc_now()
    run.cancel_requested = True
    run.cancel_requested_at = now
    run.updated_at = now
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
