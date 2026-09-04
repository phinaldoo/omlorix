from datetime import datetime, timezone, timedelta
import logging
import uuid
from enum import Enum
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy import Column, DateTime, String, Index, and_, func, or_
from sqlalchemy.orm import Session

from app.database import Base
from app.files.models import Files
from app.files.utils import materialize_file_record, persist_generated_file_path
from app.settings.utils import get_public_url
from app.notes.limits import (
    MAX_NOTE_CONTENT_LENGTH,
    MAX_NOTE_STORAGE_CHARS_PER_USER,
    MAX_NOTES_PER_USER,
)
from app.notes.file_references import validate_note_file_reference_changes
from app.notes.utils import parse_note_file_references, replace_note_file_references

logger = logging.getLogger(__name__)


class ShareType(str, Enum):
    """Types of note sharing."""
    CLONE = "clone"        # Recipient can clone the note as their own
    LIVE = "live"          # Recipient can view with live updates (read-only)
    COLLABORATE = "collaborate"  # Recipient can view and edit (not delete)


class Notes(Base):
    __tablename__ = "notes"
    __table_args__ = (
        Index('ix_notes_catalog_page', 'user_id', 'updated_at', 'id'),
        Index("ix_notes_user_updated", "user_id", "updated_at"),
    )

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    content = Column(String(MAX_NOTE_CONTENT_LENGTH), nullable=False)
    # Separate IDs for each share type
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


class SharedNoteSubscription(Base):
    """Tracks which users have subscribed to (accepted) shared notes."""
    __tablename__ = "shared_note_subscriptions"
    __table_args__ = (
        Index('ix_note_subscriber_access', 'subscriber_id', 'note_id', 'share_type'),
    )

    id = Column(String, primary_key=True, index=True)
    note_id = Column(String, nullable=False, index=True)
    subscriber_id = Column(String, nullable=False, index=True)
    share_type = Column(String, nullable=False, default="live")  # 'live' or 'collaborate'
    subscribed_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)


class NoteHistory(Base):
    """Stores edit history for notes - tracks who changed what and when."""
    __tablename__ = "note_history"

    id = Column(String, primary_key=True, index=True)
    note_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)  # User who made the change
    actor_type = Column(String, nullable=False, default="user")  # "user" vs "assistant"
    content = Column(String(MAX_NOTE_CONTENT_LENGTH), nullable=False)  # Full content snapshot at this version
    previous_content = Column(String(MAX_NOTE_CONTENT_LENGTH), nullable=True)  # Content before this change (for diff)
    change_summary = Column(String, nullable=True)  # Auto-generated summary of changes
    version_number = Column(String, nullable=False)  # Version identifier (e.g., "1", "2", etc.)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)


def _ensure_user_id(value: str, field_name: str = "user_id") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is required",
        )
    return value.strip()


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.isoformat()


def _parse_iso_datetime(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    raise ValueError("Datetime values must be ISO formatted strings or null")


def _coerce_expected_note_updated_at(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        return normalized
    return _parse_iso_datetime(value)


def _require_expected_note_updated_at(value) -> datetime:
    """Require callers to bind a mutation to a previously observed revision."""
    normalized = _coerce_expected_note_updated_at(value)
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "note_revision_required",
                "message": "View the latest note and provide expected_updated_at before changing it.",
            },
        )
    return normalized


def _normalize_note_timestamp_for_compare(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc)


def _get_user_note(db: Session, user_id: str, note_id: str) -> Notes:
    normalized_user_id = _ensure_user_id(user_id)
    normalized_note_id = _ensure_user_id(note_id, "note_id")
    note = db.query(Notes).filter(Notes.id == normalized_note_id).first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    if note.user_id != normalized_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note


def _normalize_note_content(content: str) -> str:
    # Allow empty content for new notes and cleared notes.
    return content.strip() if isinstance(content, str) else ""


def _ensure_note_content_size(content: str) -> None:
    if len(content) > MAX_NOTE_CONTENT_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Note content must be {MAX_NOTE_CONTENT_LENGTH} characters or fewer.",
        )


def _user_note_content_total(db: Session, user_id: str) -> int:
    total = (
        db.query(func.coalesce(func.sum(func.length(Notes.content)), 0))
        .filter(Notes.user_id == user_id)
        .scalar()
    )
    return int(total or 0)


def _ensure_user_note_quota(
    db: Session,
    user_id: str,
    content: str,
    *,
    existing_note: Notes | None = None,
) -> None:
    _ensure_note_content_size(content)

    if existing_note is None:
        _ensure_user_note_count(db, user_id)
        current_total = _user_note_content_total(db, user_id)
    else:
        current_total = _user_note_content_total(db, user_id) - len(existing_note.content or "")

    current_total = max(current_total, 0)
    if current_total + len(content) > MAX_NOTE_STORAGE_CHARS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                "Notes storage quota exceeded. "
                f"You can store up to {MAX_NOTE_STORAGE_CHARS_PER_USER} characters across notes."
            ),
        )


def _ensure_user_note_count(db: Session, user_id: str) -> None:
    note_count = db.query(Notes).filter(Notes.user_id == user_id).count()
    if note_count >= MAX_NOTES_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You can store up to {MAX_NOTES_PER_USER} notes.",
        )


def _get_editable_note(db: Session, user_id: str, note_id: str) -> Notes:
    normalized_user_id = _ensure_user_id(user_id)
    note = db.query(Notes).filter(Notes.id == _ensure_user_id(note_id, "note_id")).first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    if note.user_id == normalized_user_id:
        return note

    subscription = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note.id,
        SharedNoteSubscription.subscriber_id == normalized_user_id,
        SharedNoteSubscription.share_type == ShareType.COLLABORATE.value,
    ).first()
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this note",
        )
    return note


def create_user_note(
    db: Session,
    user_id: str,
    content: str = "",
    *,
    before_commit: Callable[[Notes], None] | None = None,
) -> Notes:
    normalized_user_id = _ensure_user_id(user_id)
    note_content = _normalize_note_content(content)
    validate_note_file_reference_changes(db, normalized_user_id, note_content)
    _ensure_user_note_quota(db, normalized_user_id, note_content)
    current_time = datetime.now(timezone.utc)

    note = Notes(
        id=str(uuid.uuid4()),
        user_id=normalized_user_id,
        content=note_content,
        created_at=current_time,
        updated_at=current_time,
    )
    try:
        db.add(note)
        db.flush()
        if before_commit is not None:
            before_commit(note)
        db.commit()
        db.refresh(note)
    except Exception:
        db.rollback()
        raise
    return note


def edit_user_note(
    db: Session,
    user_id: str,
    note_id: str,
    content: str,
    *,
    actor_type: str = "user",
    expected_updated_at=None,
    before_commit: Callable[[Notes], None] | None = None,
) -> Notes:
    normalized_user_id = _ensure_user_id(user_id)
    note_content = _normalize_note_content(content)
    note = _get_editable_note(db, normalized_user_id, note_id)
    db.refresh(note)
    expected_updated_at_value = _require_expected_note_updated_at(expected_updated_at)
    observed_updated_at = note.updated_at
    if (
        _normalize_note_timestamp_for_compare(observed_updated_at)
        != _normalize_note_timestamp_for_compare(expected_updated_at_value)
    ):
        _raise_note_revision_conflict()
    validate_note_file_reference_changes(
        db,
        normalized_user_id,
        note_content,
        previous_content=note.content,
    )
    _ensure_user_note_quota(db, note.user_id, note_content, existing_note=note)
    previous_content = note.content
    
    if note_content != previous_content:
        next_updated_at = datetime.now(timezone.utc)
        # The caller's loaded revision rejects long-lived stale edits above;
        # this compare-and-swap closes the smaller race between validation and
        # commit so exactly one concurrent writer can update the revision.
        updated_count = (
            db.query(Notes)
            .filter(
                Notes.id == note.id,
                Notes.updated_at == observed_updated_at,
            )
            .update(
                {
                    Notes.content: note_content,
                    Notes.updated_at: next_updated_at,
                },
                synchronize_session=False,
            )
        )
        if updated_count != 1:
            db.rollback()
            _raise_note_revision_conflict()
        try:
            if before_commit is not None:
                before_commit(note)
            db.commit()
            db.refresh(note)
        except Exception:
            db.rollback()
            raise
        
        try:
            create_note_history_entry(
                db=db,
                note_id=note.id,
                user_id=normalized_user_id,
                content=note_content,
                previous_content=previous_content,
                actor_type=actor_type or "user",
            )
        except Exception as exc:
            # Avoid breaking tool flow if history creation fails
            logger.warning("Failed to write note history for tool edit", exc_info=exc)
    return note


def _note_revision_matches(note: Notes, expected_updated_at) -> bool:
    expected = _require_expected_note_updated_at(expected_updated_at)
    return _normalize_note_timestamp_for_compare(note.updated_at) == _normalize_note_timestamp_for_compare(expected)


def _raise_note_revision_conflict() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "note_revision_conflict",
            "message": "Note changed before this action could be applied. View the latest note and try again.",
        },
    )


def delete_user_note(
    db: Session,
    user_id: str,
    note_id: str,
    *,
    expected_updated_at=None,
) -> dict[str, Any]:
    """Permanently delete one owned note and all dependent note records."""
    normalized_user_id = _ensure_user_id(user_id)
    normalized_note_id = _ensure_user_id(note_id, "note_id")
    note = db.query(Notes).filter(
        Notes.id == normalized_note_id,
        Notes.user_id == normalized_user_id,
    ).first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    expected = _require_expected_note_updated_at(expected_updated_at)
    if not _note_revision_matches(note, expected):
        _raise_note_revision_conflict()

    deleted_count = db.query(Notes).filter(
        Notes.id == note.id,
        Notes.user_id == normalized_user_id,
        Notes.updated_at == note.updated_at,
    ).delete(synchronize_session=False)
    if deleted_count != 1:
        db.rollback()
        _raise_note_revision_conflict()
    db.query(NoteHistory).filter(NoteHistory.note_id == note.id).delete(synchronize_session=False)
    db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note.id
    ).delete(synchronize_session=False)
    db.commit()
    return {"deleted": True, "note_id": normalized_note_id}


def _apply_pagination(query, *, limit: int | None = None, offset: int = 0):
    if isinstance(offset, int) and offset > 0:
        query = query.offset(offset)
    if isinstance(limit, int) and limit > 0:
        query = query.limit(limit)
    return query


def _note_search_pattern(query_text: str | None) -> str | None:
    """Build a literal contains pattern for case-insensitive note search."""
    normalized = str(query_text or "").strip()
    if not normalized:
        return None
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_user_notes(
    db: Session,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    query_text: str | None = None,
) -> list[Notes]:
    normalized_user_id = _ensure_user_id(user_id)
    query = (
        db.query(Notes)
        .filter(Notes.user_id == normalized_user_id)
        .order_by(Notes.updated_at.desc(), Notes.created_at.desc(), Notes.id.desc())
    )
    search_pattern = _note_search_pattern(query_text)
    if search_pattern:
        query = query.filter(Notes.content.ilike(search_pattern, escape="\\"))
    return _apply_pagination(query, limit=limit, offset=offset).all()


current_notes_export_version = 1.0

NOTE_EXPORT_METADATA_POLICY = {
    "history_scope": "owned_note_history",
    "share_ids": "preserve_existing_share_ids_when_available",
    "subscriptions": "export_owned_note_subscriptions_for_reference_only",
}


def _serialize_note_history_entry(history_entry: "NoteHistory") -> dict[str, Any]:
    return {
        "id": history_entry.id,
        "user_id": history_entry.user_id,
        "actor_type": history_entry.actor_type,
        "content": history_entry.content,
        "previous_content": history_entry.previous_content,
        "change_summary": history_entry.change_summary,
        "version_number": history_entry.version_number,
        "created_at": _datetime_to_iso(history_entry.created_at),
    }


def _serialize_note_subscription(subscription: "SharedNoteSubscription") -> dict[str, Any]:
    return {
        "id": subscription.id,
        "subscriber_id": subscription.subscriber_id,
        "share_type": subscription.share_type,
        "subscribed_at": _datetime_to_iso(subscription.subscribed_at),
    }


def export_user_notes(db: Session, user_id: str):
    normalized_user_id = _ensure_user_id(user_id)
    notes = (
        db.query(Notes)
        .filter(Notes.user_id == normalized_user_id)
        .order_by(Notes.updated_at.desc(), Notes.created_at.desc(), Notes.id.desc())
        .all()
    )
    note_ids = [note.id for note in notes]

    history_by_note_id: dict[str, list[dict[str, Any]]] = {}
    subscriptions_by_note_id: dict[str, list[dict[str, Any]]] = {}

    if note_ids:
        history_entries = (
            db.query(NoteHistory)
            .filter(NoteHistory.note_id.in_(note_ids))
            .order_by(NoteHistory.note_id.asc(), NoteHistory.created_at.asc(), NoteHistory.id.asc())
            .all()
        )
        for history_entry in history_entries:
            history_by_note_id.setdefault(history_entry.note_id, []).append(
                _serialize_note_history_entry(history_entry)
            )

        subscriptions = (
            db.query(SharedNoteSubscription)
            .filter(SharedNoteSubscription.note_id.in_(note_ids))
            .order_by(
                SharedNoteSubscription.note_id.asc(),
                SharedNoteSubscription.share_type.asc(),
                SharedNoteSubscription.subscribed_at.asc(),
                SharedNoteSubscription.id.asc(),
            )
            .all()
        )
        for subscription in subscriptions:
            subscriptions_by_note_id.setdefault(subscription.note_id, []).append(
                _serialize_note_subscription(subscription)
            )

    export_data = []
    for note in notes:
        export_data.append(
            {
                "id": note.id,
                "content": note.content,
                "created_at": _datetime_to_iso(note.created_at),
                "updated_at": _datetime_to_iso(note.updated_at),
                "sharing": {
                    "clone_share_id": note.clone_share_id,
                    "live_share_id": note.live_share_id,
                    "collaborate_share_id": note.collaborate_share_id,
                    "subscriptions": subscriptions_by_note_id.get(note.id, []),
                },
                "history": history_by_note_id.get(note.id, []),
            }
        )

    return {
        "export_type": "notes",
        "export_version": current_notes_export_version,
        "data": {
            "user_id": normalized_user_id,
            "metadata_policy": NOTE_EXPORT_METADATA_POLICY,
            "notes": export_data,
        },
    }


def _is_share_id_in_use(db: Session, share_id: str) -> bool:
    normalized_share_id = str(share_id or "").strip()
    if not normalized_share_id:
        return False
    return (
        db.query(Notes)
        .filter(
            or_(
                Notes.clone_share_id == normalized_share_id,
                Notes.live_share_id == normalized_share_id,
                Notes.collaborate_share_id == normalized_share_id,
            )
        )
        .first()
        is not None
    )


def _parse_optional_datetime_with_warning(
    raw_value: Any,
    *,
    warnings: list[dict[str, Any]],
    warning_payload: dict[str, Any],
    warning_message: str,
) -> datetime | None:
    try:
        return _parse_iso_datetime(raw_value)
    except ValueError:
        warnings.append({**warning_payload, "warning": warning_message})
        return None


def _coerce_imported_share_id(
    db: Session,
    raw_share_id: Any,
    *,
    share_type: ShareType,
    warnings: list[dict[str, Any]],
    warning_payload: dict[str, Any],
) -> str | None:
    if raw_share_id is None:
        return None
    if not isinstance(raw_share_id, str):
        warnings.append(
            {
                **warning_payload,
                "warning": f"{_get_share_id_field(share_type)} must be a string when provided.",
            }
        )
        return None

    normalized_share_id = raw_share_id.strip()
    if not normalized_share_id:
        return None

    if _is_share_id_in_use(db, normalized_share_id):
        regenerated_share_id = str(uuid.uuid4())
        warnings.append(
            {
                **warning_payload,
                "share_type": share_type.value,
                "warning": "Share ID already exists. A new share ID was generated during import.",
            }
        )
        return regenerated_share_id

    return normalized_share_id


def _import_note_history_entries(
    db: Session,
    *,
    note_id: str,
    note_payload: dict[str, Any],
    warnings: list[dict[str, Any]],
    warning_payload: dict[str, Any],
) -> None:
    history_payload = note_payload.get("history")
    if history_payload is None:
        return
    if not isinstance(history_payload, list):
        warnings.append({**warning_payload, "warning": "'history' must be a list when provided."})
        return

    for history_index, history_entry in enumerate(history_payload):
        entry_warning_payload = {**warning_payload, "history_index": history_index}
        if not isinstance(history_entry, dict):
            warnings.append({**entry_warning_payload, "warning": "History entry must be an object."})
            continue

        content = history_entry.get("content")
        if not isinstance(content, str):
            warnings.append({**entry_warning_payload, "warning": "History content must be a string."})
            continue
        content = _normalize_note_content(content)
        if len(content) > MAX_NOTE_CONTENT_LENGTH:
            warnings.append({**entry_warning_payload, "warning": "History content exceeds the note size limit."})
            continue

        history_user_id = history_entry.get("user_id")
        if not isinstance(history_user_id, str) or not history_user_id.strip():
            warnings.append({**entry_warning_payload, "warning": "History user_id must be a non-empty string."})
            continue

        actor_type = history_entry.get("actor_type")
        if not isinstance(actor_type, str) or not actor_type.strip():
            actor_type = "user"
        version_number = history_entry.get("version_number")
        if not isinstance(version_number, str) or not version_number.strip():
            version_number = str(history_index + 1)

        previous_content = history_entry.get("previous_content")
        if previous_content is not None and not isinstance(previous_content, str):
            warnings.append(
                {**entry_warning_payload, "warning": "History previous_content must be a string or null."}
            )
            previous_content = None
        elif previous_content is not None:
            previous_content = _normalize_note_content(previous_content)
            if len(previous_content) > MAX_NOTE_CONTENT_LENGTH:
                warnings.append(
                    {**entry_warning_payload, "warning": "History previous_content exceeds the note size limit."}
                )
                previous_content = None

        change_summary = history_entry.get("change_summary")
        if change_summary is not None and not isinstance(change_summary, str):
            warnings.append(
                {**entry_warning_payload, "warning": "History change_summary must be a string or null."}
            )
            change_summary = None

        created_at = _parse_optional_datetime_with_warning(
            history_entry.get("created_at"),
            warnings=warnings,
            warning_payload=entry_warning_payload,
            warning_message="Invalid history created_at. Using current time.",
        ) or datetime.now(timezone.utc)

        db.add(
            NoteHistory(
                id=str(uuid.uuid4()),
                note_id=note_id,
                user_id=history_user_id.strip(),
                actor_type=actor_type.strip(),
                content=content,
                previous_content=previous_content,
                change_summary=change_summary,
                version_number=version_number.strip(),
                created_at=created_at,
            )
        )


def _import_note_subscriptions(
    db: Session,
    *,
    note_id: str,
    note_payload: dict[str, Any],
    warnings: list[dict[str, Any]],
    warning_payload: dict[str, Any],
) -> None:
    sharing_payload = note_payload.get("sharing")
    if sharing_payload is None:
        return
    if not isinstance(sharing_payload, dict):
        warnings.append({**warning_payload, "warning": "'sharing' must be an object when provided."})
        return

    subscriptions_payload = sharing_payload.get("subscriptions")
    if subscriptions_payload is None:
        return
    if not isinstance(subscriptions_payload, list):
        warnings.append({**warning_payload, "warning": "'sharing.subscriptions' must be a list when provided."})
        return
    if subscriptions_payload:
        warnings.append(
            {
                **warning_payload,
                "warning": (
                    "Imported note subscriptions were skipped. Shared-note subscriptions must be "
                    "accepted by each subscriber."
                ),
            }
        )
    return


def _create_note_from_import_payload(
    db: Session,
    user_id: str,
    note_payload: dict,
    *,
    note_index: int,
    warnings: list[dict[str, Any]],
    restore_sharing_metadata: bool = False,
):
    content_raw = note_payload.get("content", "")
    if not isinstance(content_raw, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Note content must be a string.",
        )
    content_value = _normalize_note_content(content_raw)

    try:
        created_at_value = _parse_iso_datetime(note_payload.get("created_at")) or datetime.now(timezone.utc)
        updated_at_value = _parse_iso_datetime(note_payload.get("updated_at")) or datetime.now(timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    _ensure_user_note_quota(db, user_id, content_value)

    source_note_id = note_payload.get("id")
    target_note_id = str(uuid.uuid4())
    if isinstance(source_note_id, str) and source_note_id.strip():
        normalized_source_note_id = source_note_id.strip()
        if db.query(Notes).filter(Notes.id == normalized_source_note_id).first():
            warnings.append(
                {
                    "index": note_index,
                    "id": normalized_source_note_id,
                    "warning": "Note ID already exists. A new note ID was generated during import.",
                }
            )
        else:
            target_note_id = normalized_source_note_id
    elif source_note_id is not None:
        warnings.append(
            {
                "index": note_index,
                "warning": "Note ID must be a non-empty string when provided. A new note ID was generated.",
            }
        )

    sharing_payload = note_payload.get("sharing")
    if sharing_payload is not None and not isinstance(sharing_payload, dict):
        warnings.append({"index": note_index, "id": source_note_id, "warning": "'sharing' must be an object."})
        note_payload = {**note_payload, "sharing": None}
        sharing_payload = None

    warning_payload = {"index": note_index, "id": source_note_id or target_note_id}
    if sharing_payload and not restore_sharing_metadata:
        warnings.append(
            {
                **warning_payload,
                "warning": "Sharing metadata was skipped because note sharing restoration is not enabled.",
            }
        )
        note_payload = {**note_payload, "sharing": None}
        sharing_payload = None

    clone_share_id = None
    live_share_id = None
    collaborate_share_id = None
    if sharing_payload:
        clone_share_id = _coerce_imported_share_id(
            db,
            sharing_payload.get("clone_share_id"),
            share_type=ShareType.CLONE,
            warnings=warnings,
            warning_payload=warning_payload,
        )
        live_share_id = _coerce_imported_share_id(
            db,
            sharing_payload.get("live_share_id"),
            share_type=ShareType.LIVE,
            warnings=warnings,
            warning_payload=warning_payload,
        )
        collaborate_share_id = _coerce_imported_share_id(
            db,
            sharing_payload.get("collaborate_share_id"),
            share_type=ShareType.COLLABORATE,
            warnings=warnings,
            warning_payload=warning_payload,
        )

    note = Notes(
        id=target_note_id,
        user_id=user_id,
        content=content_value,
        clone_share_id=clone_share_id,
        live_share_id=live_share_id,
        collaborate_share_id=collaborate_share_id,
        created_at=created_at_value,
        updated_at=updated_at_value,
    )
    db.add(note)
    db.flush()

    # Version 1.0 is the only accepted format, so history and subscription
    # metadata always use the current import contract.
    _import_note_history_entries(
        db,
        note_id=note.id,
        note_payload=note_payload,
        warnings=warnings,
        warning_payload=warning_payload,
    )
    _import_note_subscriptions(
        db,
        note_id=note.id,
        note_payload=note_payload,
        warnings=warnings,
        warning_payload=warning_payload,
    )

    return note


def import_user_notes(
    db: Session,
    user_id: str,
    payload: dict,
    *,
    restore_sharing_metadata: bool = False,
    skip_existing_owned: bool = False,
):
    """Import notes, optionally making account restoration idempotent.

    Interactive imports retain the historical collision behavior and remap a
    globally occupied source ID. Canonical account restores set
    ``skip_existing_owned`` so replaying the same backup does not duplicate
    notes that already belong to the destination account. A collision owned by
    somebody else is still remapped, preserving portable cross-instance data.
    """
    normalized_user_id = _ensure_user_id(user_id)

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid import payload. Expected an object.",
        )

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")

    if export_type != "notes":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export_type '{export_type}'.",
        )

    if export_version != current_notes_export_version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported export_version '{export_version}'. "
                f"Expected '{current_notes_export_version}'."
            ),
        )

    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export payload. Missing 'data' object.",
        )

    raw_notes = data_block.get("notes")
    if not isinstance(raw_notes, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export payload. 'notes' must be a list.",
        )

    created = []
    skipped = []
    errors = []
    warnings = []

    for index, note_entry in enumerate(raw_notes):
        if not isinstance(note_entry, dict):
            errors.append({"index": index, "error": "Note entry must be an object."})
            continue

        source_note_id = str(note_entry.get("id") or "").strip()
        if skip_existing_owned and source_note_id:
            existing_note = db.query(Notes).filter(Notes.id == source_note_id).first()
            if (
                existing_note is not None
                and str(existing_note.user_id) == normalized_user_id
            ):
                skipped.append(
                    {
                        "id": source_note_id,
                        "source_id": source_note_id,
                        "reason": "already_exists",
                    }
                )
                continue

        note_savepoint = db.begin_nested()
        note_warnings: list[dict[str, Any]] = []
        try:
            note_obj = _create_note_from_import_payload(
                db,
                normalized_user_id,
                note_entry,
                note_index=index,
                warnings=note_warnings,
                restore_sharing_metadata=restore_sharing_metadata,
            )
            note_savepoint.commit()
        except HTTPException as exc:
            note_savepoint.rollback()
            errors.append({"index": index, "error": exc.detail})
            continue
        except Exception as exc:
            note_savepoint.rollback()
            errors.append({"index": index, "error": str(exc)})
            continue

        created.append(
            {
                "id": note_obj.id,
                "source_id": note_entry.get("id"),
            }
        )
        warnings.extend(note_warnings)

    if created:
        db.commit()

    return {
        "created": created,
        "skipped": skipped,
        "warnings": warnings,
        "errors": errors,
    }


# ============================================================================
# Note Sharing Functions
# ============================================================================

def _get_owner_display_name(db: Session, user_id: str) -> str:
    """Get display name for a user."""
    from app.users.models import User
    try:
        owner = db.query(User).filter(User.id == user_id).first()
    except Exception:
        return "Unknown"
    if not owner:
        return "Unknown"
    if owner.first_name and owner.last_name:
        return f"{owner.first_name} {owner.last_name}"
    elif owner.first_name:
        return owner.first_name
    elif owner.email:
        return owner.email.split('@')[0]
    return "Unknown"


def _get_share_id_field(share_type: ShareType) -> str:
    """Get the column name for a share type."""
    return {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type, "live_share_id")


def _get_share_url_prefix(share_type: ShareType) -> str:
    """Get the URL prefix for a share type."""
    return {
        ShareType.CLONE: "/notes/clone",
        ShareType.LIVE: "/notes/live",
        ShareType.COLLABORATE: "/notes/collaborate",
    }.get(share_type, "/notes/live")


def create_note_share(db: Session, user_id: str, note_id: str, share_type: ShareType = ShareType.LIVE) -> dict:
    """Create or return existing share for a note with specified type."""
    note = _get_user_note(db, user_id, note_id)
    
    # Get the appropriate share_id field
    share_id_attr = _get_share_id_field(share_type)
    existing_share_id = getattr(note, share_id_attr, None)
    url_prefix = _get_share_url_prefix(share_type)
    base_url = get_public_url(db)
    
    if existing_share_id:
        return {
            "share_id": existing_share_id,
            "share_type": share_type.value,
            "share_url": f"{base_url}{url_prefix}/{existing_share_id}",
        }
    
    new_share_id = str(uuid.uuid4())
    setattr(note, share_id_attr, new_share_id)
    note.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {
        "share_id": new_share_id,
        "share_type": share_type.value,
        "share_url": f"{base_url}{url_prefix}/{new_share_id}",
    }


def get_note_share_status(db: Session, user_id: str, note_id: str) -> dict:
    """Get the current share status for all share types of a note."""
    note = _get_user_note(db, user_id, note_id)
    
    # Count subscribers by type
    live_count = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id,
        SharedNoteSubscription.share_type == "live"
    ).count()
    
    collaborate_count = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id,
        SharedNoteSubscription.share_type == "collaborate"
    ).count()
    
    return {
        "clone_share_id": note.clone_share_id,
        "live_share_id": note.live_share_id,
        "collaborate_share_id": note.collaborate_share_id,
        "live_subscriber_count": live_count,
        "collaborate_subscriber_count": collaborate_count,
    }


def delete_note_share(db: Session, user_id: str, note_id: str, share_type: ShareType | None = None) -> dict:
    """Remove share info from a note. If share_type specified, only remove that type."""
    note = _get_user_note(db, user_id, note_id)
    
    if share_type is None:
        # Delete all shares and subscriptions
        db.query(SharedNoteSubscription).filter(
            SharedNoteSubscription.note_id == note_id
        ).delete()
        note.clone_share_id = None
        note.live_share_id = None
        note.collaborate_share_id = None
    else:
        # Delete only the specific share type
        share_id_attr = _get_share_id_field(share_type)
        setattr(note, share_id_attr, None)
        
        if share_type in (ShareType.LIVE, ShareType.COLLABORATE):
            db.query(SharedNoteSubscription).filter(
                SharedNoteSubscription.note_id == note_id,
                SharedNoteSubscription.share_type == share_type.value
            ).delete()
    
    note.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {"ok": True, "share_type": share_type.value if share_type else "all"}


def get_shared_note_by_share_id(db: Session, share_id: str, share_type: ShareType | None = None) -> Notes | None:
    """Find a note by its share_id and optionally share_type."""
    if not share_id:
        return None
    
    cleaned_id = share_id.strip()
    
    if share_type == ShareType.CLONE:
        return db.query(Notes).filter(Notes.clone_share_id == cleaned_id).first()
    elif share_type == ShareType.LIVE:
        return db.query(Notes).filter(Notes.live_share_id == cleaned_id).first()
    elif share_type == ShareType.COLLABORATE:
        return db.query(Notes).filter(Notes.collaborate_share_id == cleaned_id).first()
    else:
        # Search all share types
        note = db.query(Notes).filter(Notes.clone_share_id == cleaned_id).first()
        if note:
            return note
        note = db.query(Notes).filter(Notes.live_share_id == cleaned_id).first()
        if note:
            return note
        return db.query(Notes).filter(Notes.collaborate_share_id == cleaned_id).first()


def detect_share_type_from_id(db: Session, share_id: str) -> ShareType | None:
    """Detect the share type from a share_id."""
    if not share_id:
        return None
    cleaned_id = share_id.strip()
    
    if db.query(Notes).filter(Notes.clone_share_id == cleaned_id).first():
        return ShareType.CLONE
    if db.query(Notes).filter(Notes.live_share_id == cleaned_id).first():
        return ShareType.LIVE
    if db.query(Notes).filter(Notes.collaborate_share_id == cleaned_id).first():
        return ShareType.COLLABORATE
    return None


def get_shared_note_preview(
    db: Session,
    share_id: str,
    share_type: ShareType | None = None,
    requesting_user_id: str | None = None,
) -> dict:
    """Get a preview of a shared note (public endpoint)."""
    if not share_id:
        raise HTTPException(status_code=400, detail="share_id is required")
    
    note = get_shared_note_by_share_id(db, share_id, share_type)
    if not note:
        raise HTTPException(status_code=404, detail="Shared note not found")

    detected_type = detect_share_type_from_id(db, share_id) or share_type or ShareType.LIVE

    if requesting_user_id and note.user_id == requesting_user_id:
        raise HTTPException(status_code=400, detail="You cannot open your own shared note")

    if requesting_user_id and detected_type in (ShareType.LIVE, ShareType.COLLABORATE):
        already_subscribed = db.query(SharedNoteSubscription).filter(
            SharedNoteSubscription.note_id == note.id,
            SharedNoteSubscription.subscriber_id == requesting_user_id,
        ).first()
        if already_subscribed:
            raise HTTPException(status_code=409, detail="You already added this shared note")
    
    owner_name = _get_owner_display_name(db, note.user_id)
    
    return {
        "share_id": share_id,
        "share_type": detected_type.value,
        "content": note.content,
        "owner_name": owner_name,
        "created_at": _datetime_to_iso(note.created_at),
        "updated_at": _datetime_to_iso(note.updated_at),
    }


def clone_shared_note(db: Session, user_id: str, share_id: str) -> Notes:
    """Clone a shared note for a user (creates a new independent copy)."""
    note = get_shared_note_by_share_id(db, share_id, ShareType.CLONE)
    if not note:
        raise HTTPException(status_code=404, detail="Shared note not found or not available for cloning")
    
    if note.user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot clone your own note")
    
    cloned_content = _clone_note_embedded_files(db, note.content, user_id, source_owner_id=note.user_id)
    _ensure_user_note_quota(db, user_id, cloned_content)

    # Create a new note with the same content
    cloned_note = Notes(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content=cloned_content,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(cloned_note)
    db.commit()
    db.refresh(cloned_note)
    return cloned_note


def subscribe_to_shared_note(
    db: Session, 
    subscriber_id: str, 
    note_id: str,
    share_type: ShareType = ShareType.LIVE,
) -> SharedNoteSubscription:
    """Subscribe a user to a shared note (live or collaborate)."""
    if share_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Clone shares don't support subscriptions")
    existing = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id,
        SharedNoteSubscription.subscriber_id == subscriber_id,
    ).first()
    
    if existing:
        # Update existing subscription if share type changed
        if existing.share_type != share_type.value:
            existing.share_type = share_type.value
            db.commit()
            db.refresh(existing)
        return existing
    
    subscription = SharedNoteSubscription(
        id=str(uuid.uuid4()),
        note_id=note_id,
        subscriber_id=subscriber_id,
        share_type=share_type.value,
        subscribed_at=datetime.now(timezone.utc),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def unsubscribe_from_shared_note(db: Session, subscriber_id: str, note_id: str) -> dict:
    """Unsubscribe a user from a shared note."""
    deleted = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id,
        SharedNoteSubscription.subscriber_id == subscriber_id,
    ).delete()
    db.commit()
    return {"ok": True, "deleted": deleted > 0}


def get_subscribed_notes(
    db: Session,
    user_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    query_text: str | None = None,
) -> list[tuple]:
    """Get all notes that a user is subscribed to with subscription info."""
    normalized_user_id = _ensure_user_id(user_id)
    query = (
        db.query(Notes, SharedNoteSubscription)
        .join(SharedNoteSubscription, SharedNoteSubscription.note_id == Notes.id)
        .filter(SharedNoteSubscription.subscriber_id == normalized_user_id)
        .filter(
            or_(
                and_(SharedNoteSubscription.share_type == "live", Notes.live_share_id.isnot(None)),
                and_(SharedNoteSubscription.share_type == "collaborate", Notes.collaborate_share_id.isnot(None)),
            )
        )
        .order_by(Notes.updated_at.desc(), Notes.created_at.desc(), Notes.id.desc())
    )
    search_pattern = _note_search_pattern(query_text)
    if search_pattern:
        query = query.filter(Notes.content.ilike(search_pattern, escape="\\"))
    return _apply_pagination(query, limit=limit, offset=offset).all()


def get_note_subscriber_count(db: Session, note_id: str, share_type: str | None = None) -> int:
    """Get the number of subscribers for a note."""
    query = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id
    )
    if share_type:
        query = query.filter(SharedNoteSubscription.share_type == share_type)
    return query.count()


def can_user_view_note(db: Session, user_id: str, note_id: str) -> bool:
    """Check whether a user can view a note."""
    note = db.query(Notes).filter(Notes.id == note_id).first()
    if not note:
        return False
    if note.user_id == user_id:
        return True
    subscription = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id,
        SharedNoteSubscription.subscriber_id == user_id,
    ).first()
    return subscription is not None


def can_user_edit_note(db: Session, user_id: str, note_id: str) -> bool:
    """Check if a user can edit a note (owner or collaborator with edit permission)."""
    # Check if user is owner
    note = db.query(Notes).filter(Notes.id == note_id).first()
    if not note:
        return False
    if note.user_id == user_id:
        return True
    
    # Check if user is a collaborator with edit permission
    subscription = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id,
        SharedNoteSubscription.subscriber_id == user_id,
        SharedNoteSubscription.share_type == "collaborate",
    ).first()
    
    return subscription is not None


def _clone_note_embedded_files(
    db: Session,
    source_content: str,
    target_user_id: str,
    *,
    source_owner_id: str | None = None,
) -> str:
    references = parse_note_file_references(source_content)
    if not references:
        return source_content

    replacements: dict[tuple[str, str, str], tuple[str, str]] = {}
    normalized_source_owner_id = str(source_owner_id or "").strip()
    for reference in references:
        owner_id = str(reference.owner_id or normalized_source_owner_id or "").strip()
        ref_key = (reference.kind, reference.owner_id, reference.file_id)
        if ref_key in replacements:
            continue
        if not owner_id or (normalized_source_owner_id and owner_id != normalized_source_owner_id):
            continue

        file_record = (
            db.query(Files)
            .filter(
                Files.id == reference.file_id,
                Files.user_id == owner_id,
            )
            .first()
        )
        if not file_record:
            continue

        materialized_path = materialize_file_record(file_record, owner_id)
        original_name = ""
        if isinstance(file_record.meta, dict):
            original_name = str(file_record.meta.get("original_filename") or "").strip()
        original_name = original_name or file_record.file_name

        cloned_file = persist_generated_file_path(
            db,
            user_id=target_user_id,
            original_filename=original_name,
            source_path=materialized_path,
            file_type=file_record.file_type,
            file_category=file_record.file_category,
            meta={
                **(file_record.meta or {}),
                "origin": "note_clone",
                "note_clone_source_user_id": owner_id,
                "note_clone_source_file_id": reference.file_id,
            },
        )
        replacements[ref_key] = (target_user_id, cloned_file.id)

    if not replacements:
        return source_content
    return replace_note_file_references(source_content, replacements)


def get_subscription_for_note(db: Session, user_id: str, note_id: str) -> SharedNoteSubscription | None:
    """Get subscription info for a user and note."""
    return db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id,
        SharedNoteSubscription.subscriber_id == user_id,
    ).first()


# ============================================================================
# Note History Functions
# ============================================================================

HISTORY_MERGE_WINDOW = timedelta(minutes=5)


def _generate_change_summary(previous_content: str, new_content: str) -> str:
    """Generate a human-readable summary of changes between two versions."""
    if not previous_content:
        return "Initial version"
    
    prev_lines = previous_content.split('\n')
    new_lines = new_content.split('\n')
    
    prev_len = len(previous_content)
    new_len = len(new_content)
    
    prev_line_count = len(prev_lines)
    new_line_count = len(new_lines)
    
    changes = []
    
    # Character count changes
    char_diff = new_len - prev_len
    if char_diff > 0:
        changes.append(f"+{char_diff} chars")
    elif char_diff < 0:
        changes.append(f"{char_diff} chars")
    
    # Line count changes  
    line_diff = new_line_count - prev_line_count
    if line_diff > 0:
        changes.append(f"+{line_diff} lines")
    elif line_diff < 0:
        changes.append(f"{line_diff} lines")
    
    if not changes:
        return "Minor edits"
    
    return ", ".join(changes)


def create_note_history_entry(
    db: Session,
    note_id: str,
    user_id: str,
    content: str,
    previous_content: str | None = None,
    actor_type: str = "user",
) -> NoteHistory:
    """Create a new history entry for a note edit."""
    _ensure_note_content_size(content)
    if previous_content is not None:
        _ensure_note_content_size(previous_content)
    now = datetime.now(timezone.utc)
    normalized_actor = actor_type or "user"
    
    # Check last history entry to see if we should merge
    last_entry = db.query(NoteHistory).filter(
        NoteHistory.note_id == note_id
    ).order_by(NoteHistory.created_at.desc()).first()
    
    if last_entry:
        time_since_last = now - (last_entry.created_at or now)
        should_merge = (
            last_entry.user_id == user_id
            and time_since_last <= HISTORY_MERGE_WINDOW
        )
        if should_merge:
            anchor_content = last_entry.previous_content
            if anchor_content is None:
                anchor_content = previous_content or ""
            
            last_entry.content = content
            last_entry.change_summary = _generate_change_summary(anchor_content or "", content)
            last_entry.created_at = now
            last_entry.actor_type = normalized_actor
            
            db.commit()
            db.refresh(last_entry)
            return last_entry
    
    # Otherwise create a brand new version
    if last_entry:
        try:
            version_num = int(last_entry.version_number) + 1
        except ValueError:
            version_num = 1
    else:
        version_num = 1
    
    change_summary = _generate_change_summary(previous_content or "", content)
    
    history_entry = NoteHistory(
        id=str(uuid.uuid4()),
        note_id=note_id,
        user_id=user_id,
        actor_type=normalized_actor,
        content=content,
        previous_content=previous_content,
        change_summary=change_summary,
        version_number=str(version_num),
        created_at=datetime.now(timezone.utc),
    )
    
    db.add(history_entry)
    db.commit()
    db.refresh(history_entry)
    
    return history_entry


def _get_history_access_start(db: Session, user_id: str, note_id: str) -> datetime | None:
    """Return the earliest history timestamp visible to the user.

    Owners can view the full history. Collaborators with edit permission can view
    history created after they were granted access. Read-only subscribers cannot
    view history because history snapshots may contain content removed before the
    note was shared with them.
    """
    note = db.query(Notes).filter(Notes.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    if note.user_id == user_id:
        return None

    subscription = db.query(SharedNoteSubscription).filter(
        SharedNoteSubscription.note_id == note_id,
        SharedNoteSubscription.subscriber_id == user_id,
        SharedNoteSubscription.share_type == ShareType.COLLABORATE.value,
    ).first()

    if not subscription:
        raise HTTPException(status_code=403, detail="You don't have access to this note's history")

    return subscription.subscribed_at


def _visible_note_history_query(db: Session, note_id: str, visible_after: datetime | None):
    query = db.query(NoteHistory).filter(NoteHistory.note_id == note_id)
    if visible_after is not None:
        query = query.filter(NoteHistory.created_at >= visible_after)
    return query


def get_note_history(
    db: Session,
    note_id: str,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get the edit history for a note. User must have history access."""
    visible_after = _get_history_access_start(db, user_id, note_id)

    history_query = _visible_note_history_query(db, note_id, visible_after)
    history_entries = (
        history_query.order_by(NoteHistory.created_at.desc(), NoteHistory.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    total_count = _visible_note_history_query(db, note_id, visible_after).count()

    # Get user display names
    result = []
    for entry in history_entries:
        user_display_name = _get_owner_display_name(db, entry.user_id)

        result.append({
            "id": entry.id,
            "note_id": entry.note_id,
            "user_id": entry.user_id,
            "user_display_name": user_display_name,
            "actor_type": entry.actor_type,
            "content": entry.content,
            "previous_content": entry.previous_content,
            "change_summary": entry.change_summary,
            "version_number": entry.version_number,
            "created_at": _datetime_to_iso(entry.created_at),
        })

    return {
        "entries": result,
        "total_count": total_count,
        "has_more": (offset + limit) < total_count,
    }


def restore_note_from_history(
    db: Session,
    note_id: str,
    history_id: str,
    user_id: str,
    *,
    expected_updated_at=None,
) -> Notes:
    """Restore a note to a previous version from history."""
    # Check if user can edit this note
    if not can_user_edit_note(db, user_id, note_id):
        raise HTTPException(status_code=403, detail="You don't have permission to restore this note")
    
    # Get the history entry only if it is visible to this editor.
    history_entry = get_visible_history_entry(db, user_id, note_id, history_id)
    if not history_entry:
        raise HTTPException(status_code=404, detail="History entry not found")

    # Get the current note
    note = db.query(Notes).filter(Notes.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    # Reuse the same revision-bound compare-and-swap as every other content
    # update. Selecting a historical version must not overwrite a newer edit.
    return edit_user_note(
        db=db,
        note_id=note_id,
        user_id=user_id,
        content=history_entry.content,
        expected_updated_at=expected_updated_at,
        actor_type="user",
    )


def get_visible_history_entry(
    db: Session,
    user_id: str,
    note_id: str,
    history_id: str,
) -> NoteHistory | None:
    """Get a history entry only if it is visible to the requesting user."""
    visible_after = _get_history_access_start(db, user_id, note_id)
    return _visible_note_history_query(db, note_id, visible_after).filter(
        NoteHistory.id == history_id,
    ).first()


def can_user_view_history(db: Session, user_id: str, note_id: str) -> bool:
    """Check if a user can view a note's history."""
    try:
        _get_history_access_start(db, user_id, note_id)
    except HTTPException:
        return False
    return True
