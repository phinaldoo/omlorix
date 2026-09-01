"""Scope-aware persistence and import/export operations for memories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Sequence
import uuid

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.memories.models import Memory
from app.memories.schemas import (
    MEMORY_IMPORT_LIMIT_MESSAGE,
    MAX_MEMORY_IMPORT_ITEMS,
    MemoryExportData,
    MemoryExportItem,
    MemoryExportPayload,
    MemoryImportItem,
)


CURRENT_MEMORIES_EXPORT_VERSION = 1.0


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Identify exactly one personal or project memory collection."""

    user_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        normalized_user_id = str(self.user_id or "").strip() or None
        normalized_project_id = str(self.project_id or "").strip() or None
        if (normalized_user_id is None) == (normalized_project_id is None):
            raise ValueError("MemoryScope requires exactly one user_id or project_id")
        object.__setattr__(self, "user_id", normalized_user_id)
        object.__setattr__(self, "project_id", normalized_project_id)

    @classmethod
    def personal(cls, user_id: str) -> "MemoryScope":
        """Build a personal-memory scope."""

        return cls(user_id=user_id)

    @classmethod
    def project(cls, project_id: str) -> "MemoryScope":
        """Build a shared project-memory scope."""

        return cls(project_id=project_id)

    @property
    def is_project(self) -> bool:
        """Return whether this is a project scope."""

        return self.project_id is not None

    def filter_expression(self):
        """Return the SQLAlchemy expression restricting rows to this scope."""

        if self.project_id is not None:
            return Memory.project_id == self.project_id
        return Memory.user_id == self.user_id

    def owner_values(self) -> dict[str, str | None]:
        """Return owner columns for a new row."""

        return {"user_id": self.user_id, "project_id": self.project_id}


def normalize_memory_content(content: str) -> str:
    """Collapse whitespace and validate memory content."""

    normalized = " ".join(content.strip().split())
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="content is required"
        )
    if len(normalized) > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="content must be 500 characters or fewer",
        )
    return normalized


def memory_content_key(content: str) -> str:
    """Return the stable, case-insensitive key used for deduplication."""

    return normalize_memory_content(content).casefold()


def normalize_memory_source_date(source_date: date | str | None) -> date | None:
    """Normalize an optional source date from API or archive input."""

    if source_date is None or isinstance(source_date, date):
        return source_date
    normalized = source_date.strip().lower()
    if not normalized or normalized == "unknown":
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date must be 'unknown' or a YYYY-MM-DD string",
        ) from exc


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an optional archive timestamp as a timezone-aware datetime."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datetime values must be ISO formatted strings or null",
        )
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datetime values must be ISO formatted strings or null",
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _scope_query(db: Session, scope: MemoryScope):
    """Create the base query shared by all scoped operations."""

    return db.query(Memory).filter(scope.filter_expression())


def list_memories(
    db: Session,
    scope: MemoryScope,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Memory]:
    """List a scope's memories from most recently updated to oldest."""

    query = _scope_query(db, scope).order_by(
        Memory.updated_at.desc(), Memory.created_at.desc()
    )
    if offset > 0:
        query = query.offset(offset)
    if limit is not None and limit > 0:
        query = query.limit(limit)
    return query.all()


def _get_memory(db: Session, scope: MemoryScope, memory_id: str) -> Memory:
    """Get a memory only when it belongs to the requested scope."""

    normalized_id = str(memory_id or "").strip()
    if not normalized_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="memory_id is required"
        )
    memory = _scope_query(db, scope).filter(Memory.id == normalized_id).first()
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    return memory


def create_memory(
    db: Session,
    scope: MemoryScope,
    content: str,
    *,
    source_date: date | str | None = None,
    before_commit: Callable[[Memory, bool], None] | None = None,
) -> tuple[Memory, bool]:
    """Create or refresh one deduplicated memory using an indexed lookup."""

    normalized_content = normalize_memory_content(content)
    content_key = normalized_content.casefold()
    normalized_source_date = normalize_memory_source_date(source_date)

    def commit_deduplicated_memory(existing: Memory) -> tuple[Memory, bool]:
        existing.updated_at = datetime.now(timezone.utc)
        if normalized_source_date is not None and existing.source_date is None:
            existing.source_date = normalized_source_date
        try:
            if before_commit is not None:
                before_commit(existing, False)
            db.commit()
            db.refresh(existing)
        except Exception:
            db.rollback()
            raise
        return existing, False

    existing = _scope_query(db, scope).filter(Memory.content_key == content_key).first()
    if existing is not None:
        return commit_deduplicated_memory(existing)

    now = datetime.now(timezone.utc)
    memory = Memory(
        id=str(uuid.uuid4()),
        content=normalized_content,
        content_key=content_key,
        source_date=normalized_source_date,
        created_at=now,
        updated_at=now,
        **scope.owner_values(),
    )
    db.add(memory)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent creator may win after the indexed lookup. Treat that as
        # normal deduplication instead of surfacing a database error.
        db.rollback()
        existing = (
            _scope_query(db, scope).filter(Memory.content_key == content_key).first()
        )
        if existing is None:
            raise
        return commit_deduplicated_memory(existing)
    except Exception:
        db.rollback()
        raise

    try:
        if before_commit is not None:
            before_commit(memory, True)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(memory)
    return memory, True


def update_memory(
    db: Session, scope: MemoryScope, memory_id: str, content: str | None
) -> Memory:
    """Update memory content while preserving per-scope uniqueness."""

    memory = _get_memory(db, scope, memory_id)
    if content is None:
        return memory
    normalized_content = normalize_memory_content(content)
    content_key = normalized_content.casefold()
    if content_key != memory.content_key:
        duplicate = (
            _scope_query(db, scope)
            .filter(Memory.content_key == content_key, Memory.id != memory.id)
            .first()
        )
        if duplicate is not None:
            # Editing one row into an existing value should preserve the same
            # deduplication contract as creation instead of surfacing a second
            # behavior for equivalent content.
            duplicate.updated_at = datetime.now(timezone.utc)
            db.delete(memory)
            db.commit()
            db.refresh(duplicate)
            return duplicate
    if normalized_content != memory.content:
        memory.content = normalized_content
        memory.content_key = content_key
        memory.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(memory)
    return memory


def delete_memory(
    db: Session, scope: MemoryScope, memory_id: str
) -> dict[str, str | bool]:
    """Delete one scoped memory."""

    memory = _get_memory(db, scope, memory_id)
    db.delete(memory)
    db.commit()
    return {"deleted": True, "memory_id": memory.id}


def export_memories(db: Session, scope: MemoryScope) -> dict[str, Any]:
    """Export a complete scope using the portable memory archive contract."""

    items = [
        MemoryExportItem(
            content=memory.content,
            source_date=memory.source_date.isoformat() if memory.source_date else None,
            created_at=memory.created_at.isoformat() if memory.created_at else None,
            updated_at=memory.updated_at.isoformat() if memory.updated_at else None,
        )
        for memory in list_memories(db, scope)
    ]
    # Scope identifiers and a redundant count are intentionally absent: the
    # importing endpoint or account archive already determines the target.
    data = MemoryExportData(memories=items)
    return MemoryExportPayload(
        export_type="memories",
        export_version=CURRENT_MEMORIES_EXPORT_VERSION,
        data=data,
    ).model_dump(mode="json")


def _entry_values(
    entry: MemoryImportItem | MemoryExportItem,
) -> tuple[str, str, date | None, datetime | None, datetime | None]:
    """Convert either supported import shape into one internal representation."""

    content = normalize_memory_content(entry.content)
    source_value = (
        entry.date if isinstance(entry, MemoryImportItem) else entry.source_date
    )
    created_at = (
        None
        if isinstance(entry, MemoryImportItem)
        else _parse_iso_datetime(entry.created_at)
    )
    updated_at = (
        None
        if isinstance(entry, MemoryImportItem)
        else _parse_iso_datetime(entry.updated_at)
    )
    return (
        content,
        content.casefold(),
        normalize_memory_source_date(source_value),
        created_at,
        updated_at,
    )


def import_memories(
    db: Session,
    scope: MemoryScope,
    entries: Sequence[MemoryImportItem | MemoryExportItem],
    *,
    _retry_on_conflict: bool = True,
) -> dict[str, Any]:
    """Import and deduplicate a bounded batch in one transaction.

    Existing rows and duplicate entries in the same request share one lookup.
    New UUIDs are assigned before the commit, and committed rows are reloaded
    together so response construction does not refresh every imported row.
    """

    if not entries:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one memory is required",
        )
    if len(entries) > MAX_MEMORY_IMPORT_ITEMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=MEMORY_IMPORT_LIMIT_MESSAGE
        )

    normalized_entries = [_entry_values(entry) for entry in entries]
    keys = {entry[1] for entry in normalized_entries}
    existing_rows = _scope_query(db, scope).filter(Memory.content_key.in_(keys)).all()
    lookup = {memory.content_key: memory for memory in existing_rows}
    now = datetime.now(timezone.utc)
    created_count = 0

    for content, content_key, source_date, created_at, updated_at in normalized_entries:
        memory = lookup.get(content_key)
        if memory is None:
            memory = Memory(
                id=str(uuid.uuid4()),
                content=content,
                content_key=content_key,
                source_date=source_date,
                created_at=created_at or now,
                updated_at=updated_at or created_at or now,
                **scope.owner_values(),
            )
            db.add(memory)
            lookup[content_key] = memory
            created_count += 1
        elif source_date is not None and memory.source_date is None:
            memory.source_date = source_date
            memory.updated_at = now
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if not _retry_on_conflict:
            raise
        # Retry once after a concurrent import establishes the unique rows.
        return import_memories(db, scope, entries, _retry_on_conflict=False)

    # SQLAlchemy expires ORM rows on commit by default. Reload every imported
    # key in one query so response serialization cannot degrade into one query
    # per item. Rebuild from the input keys to preserve request order and
    # repeated entries in the response.
    persisted_rows = _scope_query(db, scope).filter(Memory.content_key.in_(keys)).all()
    persisted_lookup = {memory.content_key: memory for memory in persisted_rows}
    imported_items = [persisted_lookup[entry[1]] for entry in normalized_entries]

    return {
        "total_received": len(entries),
        "created_count": created_count,
        "deduped_count": len(entries) - created_count,
        "items": imported_items,
    }


def import_memory_export(
    db: Session,
    scope: MemoryScope,
    payload: MemoryExportPayload,
) -> dict[str, Any]:
    """Validate and import the canonical memory archive envelope."""

    if payload.export_version != CURRENT_MEMORIES_EXPORT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported export_version '{payload.export_version}'. "
                f"Expected '{CURRENT_MEMORIES_EXPORT_VERSION}'."
            ),
        )
    return import_memories(db, scope, payload.data.memories)
