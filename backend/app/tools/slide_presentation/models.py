from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Index, Integer, JSON, String
from sqlalchemy.exc import IntegrityError

from app.database import Base


class SlidePresentations(Base):
    __tablename__ = "slide_presentations"
    __table_args__ = (
        Index("ix_slide_presentations_user_id", "user_id"),
        Index("ix_slide_presentations_user_created", "user_id", "created_at"),
        Index("ix_slide_presentations_file_id", "file_id"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False)
    user_id = Column(String, nullable=False)
    title = Column(String, nullable=False, default="Presentation")
    slide_count = Column(Integer, nullable=False, default=0)
    storage_provider = Column(String, nullable=False, default="local")
    storage_prefix = Column(String, nullable=False)
    # Provider-specific details and migration provenance live beside the
    # presentation index, just as ordinary user files retain storage metadata.
    storage_meta = Column(JSON, nullable=False, default=dict)
    file_id = Column(String, nullable=True)
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_updated_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


def get_slide_presentation(
    db, presentation_id: str, user_id: str
) -> SlidePresentations | None:
    return (
        db.query(SlidePresentations)
        .filter(
            SlidePresentations.id == str(presentation_id),
            SlidePresentations.user_id == str(user_id),
        )
        .first()
    )


def get_slide_presentation_by_file_id(
    db, file_id: str, user_id: str
) -> SlidePresentations | None:
    return (
        db.query(SlidePresentations)
        .filter(
            SlidePresentations.file_id == str(file_id),
            SlidePresentations.user_id == str(user_id),
        )
        .first()
    )


def resolve_slide_presentation_by_file_id(db, file_id: str, user_id: str) -> SlidePresentations | None:
    """Resolve either the canonical HTML source ID or its rendered PPTX ID."""
    return get_slide_presentation(db, file_id, user_id) or get_slide_presentation_by_file_id(
        db, file_id, user_id
    )


def upsert_slide_presentation(
    db,
    *,
    presentation_id: str,
    user_id: str,
    title: str,
    slide_count: int,
    storage_provider: str,
    storage_prefix: str,
    file_id: str | None,
    storage_meta: dict[str, Any] | None = None,
    commit: bool = True,
) -> SlidePresentations:
    """Create or update a presentation index and its portable storage manifest."""
    now = datetime.now(timezone.utc)
    normalized_title = str(title or "Presentation")
    normalized_slide_count = max(0, int(slide_count or 0))
    normalized_storage_provider = (
        str(storage_provider or "local").strip().lower() or "local"
    )
    normalized_storage_prefix = str(storage_prefix or "").strip()
    normalized_file_id = str(file_id).strip() if file_id else None
    normalized_storage_meta = (
        dict(storage_meta) if isinstance(storage_meta, dict) else {}
    )

    record = get_slide_presentation(db, presentation_id, user_id)

    def apply_updates(target: SlidePresentations) -> None:
        target.title = normalized_title
        target.slide_count = normalized_slide_count
        target.storage_provider = normalized_storage_provider
        target.storage_prefix = normalized_storage_prefix
        target.file_id = normalized_file_id
        # Callers that do not know the manifest must not erase migration
        # provenance already recorded on an existing presentation.
        if storage_meta is not None:
            target.storage_meta = normalized_storage_meta
        target.last_updated_at = now

    if record is None:
        record = SlidePresentations(
            id=str(presentation_id),
            user_id=str(user_id),
            title=normalized_title,
            slide_count=normalized_slide_count,
            storage_provider=normalized_storage_provider,
            storage_prefix=normalized_storage_prefix,
            storage_meta=normalized_storage_meta,
            file_id=normalized_file_id,
            created_at=now,
            last_updated_at=now,
        )
        try:
            # Keep a racing insert failure inside a savepoint. In particular,
            # commit=False callers may hold row locks and other pending changes
            # that must survive while this function retries as an update.
            with db.begin_nested():
                db.add(record)
                db.flush()
        except IntegrityError:
            record = get_slide_presentation(db, presentation_id, user_id)
            if record is None:
                raise
            apply_updates(record)
        if commit:
            db.commit()
    else:
        apply_updates(record)
        if commit:
            db.commit()

    if commit:
        db.refresh(record)
    else:
        db.flush()
    return record
