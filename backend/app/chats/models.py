from sqlalchemy import JSON, BigInteger, Column, String, Boolean, ForeignKey, or_
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy import Integer as SAInteger
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import DateTime
from copy import deepcopy
import uuid
import json
import logging

from app.database import Base
from app.chats.streaming import cancel_registry, stream_hub
from app.files.models import Files
from app.groups.init import get_group_setting_value
from app.projects.models import Project, ProjectMember
from app.telemetry.metrics import (
    record_chat_created_metric,
    record_chat_deleted_metric,
    record_chat_message_metric,
)
from app.utils.utils import sanitize_chat_text


logger = logging.getLogger(__name__)


def _meta_to_dict(meta_value):
    """Convert a meta value (dict, str, or None) to a dict, parsing JSON strings."""
    if isinstance(meta_value, dict):
        return meta_value
    if meta_value is None:
        return {}
    if isinstance(meta_value, str):
        try:
            parsed = json.loads(meta_value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def _is_temp_meta(meta_dict):
    """Check if a meta dict has status 'temp'."""
    return meta_dict.get("status") == "temp"


def _is_shadow_deleted_meta(meta_dict):
    """Check if a meta dict has the shadow_deleted flag set."""
    return bool(meta_dict.get("shadow_deleted"))


def _ensure_not_shadow_deleted(chat):
    """Raise HTTPException 404 if the chat is shadow-deleted."""
    meta = _meta_to_dict(getattr(chat, "meta", None))
    if _is_shadow_deleted_meta(meta):
        raise HTTPException(status_code=404, detail="Chat not found!")
    return meta


def can_send_messages_to_chat(chat, *, allow_archived: bool = False) -> bool:
    """Return True when a chat can accept new messages."""
    if not chat:
        return False

    meta = _meta_to_dict(getattr(chat, "meta", None))
    if _is_shadow_deleted_meta(meta) or _is_temp_meta(meta):
        return False
    if not allow_archived and bool(getattr(chat, "archived", False)):
        return False
    return True


def ensure_chat_sendable(chat, *, allow_archived: bool = False, detail: str = "Chat not found!"):
    """Raise HTTPException 404 when a chat cannot accept new messages."""
    if not can_send_messages_to_chat(chat, allow_archived=allow_archived):
        raise HTTPException(status_code=404, detail=detail)
    return chat


def is_chat_hidden_from_default_list(chat, include_temp: bool = False) -> bool:
    """Return True when a chat should be hidden from normal chat listings."""
    meta = _meta_to_dict(getattr(chat, "meta", None))
    if _is_shadow_deleted_meta(meta):
        return True
    if not include_temp and _is_temp_meta(meta):
        return True
    return False


def _mark_chat_shadow_deleted(chat):
    """Mark a chat as shadow-deleted, clearing share/pin info. Returns False if already shadow-deleted."""
    # ``meta`` is stored in a plain SQLAlchemy JSON column rather than a
    # MutableDict-backed column.  Mutating the dictionary returned from a
    # loaded model and assigning that same object back does not produce a
    # scalar attribute change event, so the JSON update can be omitted from
    # the flush.  Work on a copy before changing it so ``chat.meta = meta``
    # reliably records the new value for persistence.
    meta = deepcopy(_meta_to_dict(getattr(chat, "meta", None)))
    if _is_shadow_deleted_meta(meta):
        return False
    now = datetime.now(timezone.utc)
    meta["shadow_deleted"] = True
    meta["shadow_deleted_at"] = now.isoformat()
    chat.meta = meta
    chat.share = None
    chat.share_id = None
    chat.pinned_position = None
    chat.last_updated_at = now
    return True


def _ensure_current_project_access(user_id: str, chat, db) -> None:
    """Require current access to a project-scoped chat before mutating its transcript."""
    project_id = str(getattr(chat, "project_id", None) or "").strip()
    if not project_id:
        return

    from app.projects.models import has_project_access

    if not has_project_access(db, user_id, project_id):
        raise HTTPException(status_code=404, detail="Chat not found!")



# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------
class Chats(Base):
    __tablename__ = "chats"
    __table_args__ = (
        Index("ix_chats_user_id", "user_id"),
        Index("ix_chats_user_last_updated", "user_id", "last_updated_at"),
        Index("ix_chats_project_id", "project_id"),
        Index("ix_chats_share_id", "share_id"),
        Index("ix_chats_created_at", "created_at"),
        Index("ix_chats_last_updated_at", "last_updated_at"),
        UniqueConstraint("share_id", name="uq_chats_share_id"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    share = Column(JSON, nullable=True)
    share_id = Column(String, nullable=True)
    archived = Column(Boolean, default = False)
    pinned_position = Column(SAInteger, nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)
    last_updated_at = Column(DateTime, nullable=False)
    # This monotonic counter is advanced exactly once for each successfully
    # completed assistant generation. It intentionally starts at zero so
    # existing chat history does not become unread when the feature is added.
    response_version = Column(BigInteger, nullable=False, default=0, server_default="0")
    # Retaining the last generation ID makes completion recording idempotent
    # across reconnects, retries, and worker cleanup paths.
    last_completed_generation_id = Column(String, nullable=True)


class ChatReadState(Base):
    """Store a user's read position for a chat across browsers and devices."""

    __tablename__ = "chat_read_states"
    __table_args__ = (
        Index("ix_chat_read_states_chat_id", "chat_id"),
        Index("ix_chat_read_states_user_id", "user_id"),
    )

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    chat_id = Column(String, ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    read_response_version = Column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


def apply_chat_unread_state(db, user_id: str, chats: list[Chats]) -> list[Chats]:
    """Annotate loaded chat rows with the authenticated user's unread state.

    The annotation is deliberately transient. The durable source of truth is
    the response counter plus the receipt table, while API serializers can
    consume ``has_unread_response`` without exposing either internal counter.
    """
    normalized_user_id = str(user_id or "").strip()
    chat_ids = [str(chat.id) for chat in chats if getattr(chat, "id", None)]
    if not normalized_user_id or not chat_ids:
        for chat in chats:
            chat.has_unread_response = False
        return chats

    receipt_rows = (
        db.query(ChatReadState)
        .filter(
            ChatReadState.user_id == normalized_user_id,
            ChatReadState.chat_id.in_(chat_ids),
        )
        .all()
    )
    read_versions = {
        str(receipt.chat_id): int(receipt.read_response_version or 0)
        for receipt in receipt_rows
    }
    for chat in chats:
        response_version = int(getattr(chat, "response_version", 0) or 0)
        read_version = read_versions.get(str(chat.id), 0)
        chat.has_unread_response = response_version > read_version
    return chats


def record_successful_generation_completion(
    db,
    chat_id: str,
    generation_id: str,
) -> int:
    """Advance a chat's response version once for a successful generation.

    The row lock and generation-ID guard make this safe when a stream is
    replayed or multiple cleanup paths observe the same terminal event.
    """
    normalized_chat_id = str(chat_id or "").strip()
    normalized_generation_id = str(generation_id or "").strip()
    if not normalized_chat_id or not normalized_generation_id or normalized_chat_id == "temp":
        return 0

    chat = (
        db.query(Chats)
        .filter(Chats.id == normalized_chat_id)
        .with_for_update()
        .first()
    )
    if not chat:
        return 0
    if str(chat.last_completed_generation_id or "") == normalized_generation_id:
        return int(chat.response_version or 0)

    chat.response_version = int(chat.response_version or 0) + 1
    chat.last_completed_generation_id = normalized_generation_id
    db.commit()
    db.refresh(chat)
    return int(chat.response_version or 0)


def mark_chat_read_for_user(db, user_id: str, chat: Chats) -> ChatReadState:
    """Atomically move a user's receipt through the chat's current response."""
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id or not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")

    receipt = (
        db.query(ChatReadState)
        .filter(
            ChatReadState.user_id == normalized_user_id,
            ChatReadState.chat_id == str(chat.id),
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    if receipt is None:
        receipt = ChatReadState(
            user_id=normalized_user_id,
            chat_id=str(chat.id),
            read_response_version=int(chat.response_version or 0),
            updated_at=now,
        )
        db.add(receipt)
    else:
        receipt.read_response_version = int(chat.response_version or 0)
        receipt.updated_at = now
    db.commit()
    db.refresh(receipt)
    return receipt


def get_accessible_chat_attention(db, user_id: str, chat_ids: list[str]) -> dict[str, bool]:
    """Return unread flags for a bounded set of chats visible to one user."""
    normalized_user_id = str(user_id or "").strip()
    normalized_chat_ids = list(dict.fromkeys(str(chat_id).strip() for chat_id in chat_ids if str(chat_id).strip()))
    if not normalized_user_id or not normalized_chat_ids:
        return {}

    owned_project_ids = db.query(Project.id).filter(Project.user_id == normalized_user_id)
    member_project_ids = db.query(ProjectMember.project_id).filter(ProjectMember.user_id == normalized_user_id)
    chats = (
        db.query(Chats)
        .filter(
            Chats.id.in_(normalized_chat_ids),
            or_(
                Chats.user_id == normalized_user_id,
                Chats.project_id.in_(owned_project_ids),
                Chats.project_id.in_(member_project_ids),
            ),
        )
        .all()
    )
    visible_chats = [chat for chat in chats if not is_chat_hidden_from_default_list(chat)]
    apply_chat_unread_state(db, normalized_user_id, visible_chats)
    return {
        str(chat.id): bool(getattr(chat, "has_unread_response", False))
        for chat in visible_chats
    }
    


# -------------------
# Create Chat
# -------------------
def create_chat(user_id: str, db, project_id: str | None = None, meta: dict | None = None):
    """Create a new chat record in the database for the given user."""
    # Default meta status to "normal" if not provided
    if meta is None:
        meta = {"status": "normal"}
    chat = Chats(
        user_id=user_id,
        project_id=project_id,
        meta=meta,
        created_at=datetime.now(timezone.utc),
        last_updated_at=datetime.now(timezone.utc)
    )
    db.add(chat)
    db.commit()
    record_chat_created_metric(user_id=user_id)
    return chat



# -------------------
# Check Chat Exists
# -------------------
def check_chat_exists(chat_id: str, db):
    """Check whether a chat with the given ID exists in the database."""
    chat = db.query(Chats).filter(Chats.id == chat_id).first()
    if not chat:
        return False
    return True



# -------------------
# Get Chat
# -------------------
def get_chat(db, chat_id: str, user_id: str):
    """Retrieve a single chat by ID and user_id, raising 404 if not found or shadow-deleted."""
    if not chat_id or not user_id:
        raise HTTPException(status_code=404, detail="Cannot get chat!")
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first() # TODO
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_not_shadow_deleted(chat)
    return chat



def _apply_chat_meta_list_filters(query, include_temp: bool = False):
    shadow_deleted = Chats.meta["shadow_deleted"].as_boolean()
    query = query.filter(or_(Chats.meta.is_(None), shadow_deleted.isnot(True)))
    if not include_temp:
        status = Chats.meta["status"].as_string()
        query = query.filter(or_(Chats.meta.is_(None), status.is_(None), status != "temp"))
    return query


def get_visible_chats_query(
    db,
    user_id: str,
    project_id: str | None = None,
    include_temp: bool = False,
    include_archived: bool = False,
    include_shared_project: bool = False,
):
    """
    Build the default visible chat query for a user.
    
    If include_shared_project is True and project_id is provided, returns ALL chats
    in that project (not just user's own) - for shared project viewing.
    """
    if include_shared_project and project_id:
        # For shared projects, get all chats in the project regardless of owner
        query = db.query(Chats).filter(Chats.project_id == project_id)
    else:
        # Default: only user's own chats
        query = db.query(Chats).filter(Chats.user_id == user_id)
        if project_id:
            query = query.filter(Chats.project_id == project_id)
    
    if not include_archived:
        query = query.filter((Chats.archived == False) | (Chats.archived == None))
    return _apply_chat_meta_list_filters(query, include_temp=include_temp)


# -------------------
# Get Chats
# -------------------
def get_chats(db, user_id: str, project_id: str | None = None, include_temp: bool = False, include_archived: bool = False, include_shared_project: bool = False):
    """Get all visible chats for a user."""
    query = get_visible_chats_query(
        db,
        user_id,
        project_id=project_id,
        include_temp=include_temp,
        include_archived=include_archived,
        include_shared_project=include_shared_project,
    )
    chats = query.order_by(Chats.last_updated_at.desc()).all()
    filtered: list[Chats] = []
    for chat in chats:
        if is_chat_hidden_from_default_list(chat, include_temp=include_temp):
            continue
        filtered.append(chat)
    return filtered



# -------------------
# Duplicate Chat
# -------------------
def clone_chat_message_for_new_chat(
    message: "ChatMessages",
    new_chat_id: str,
    id_map: dict[str, str],
) -> "ChatMessages":
    """Clone one stored message into a newly created chat.

    A copied conversation gets independent message IDs, so references to rows
    already copied into the new chat are remapped through ``id_map``. Historical
    transcript data is retained, including structured attachment/tool metadata,
    generation metadata, reasoning, retry count, model ID, role, and timestamp.

    Bookmark and realtime-session state are deliberately not copied. A bookmark
    belongs to the exact source message, while realtime identifiers describe the
    source transport session rather than the historical transcript.
    """
    cloned_generation = (
        deepcopy(message.generation) if message.generation is not None else None
    )
    new_id = str(uuid.uuid4())
    new_reference_id = id_map.get(message.reference_id, message.reference_id)
    cloned_message = ChatMessages(
        id=new_id,
        chat_id=new_chat_id,
        model_id=message.model_id,
        role=message.role,
        content=message.content,
        reference_id=new_reference_id,
        generation=cloned_generation,
        thinking=message.thinking,
        retry_count=message.retry_count,
        bookmarked=False,
        created_at=message.created_at or datetime.now(timezone.utc),
    )
    id_map[message.id] = new_id
    return cloned_message


def duplicate_chat(user_id: str, chat_id: str, db):
    """
    Duplicate a chat owned by ``user_id`` and every stored message row.

    The new chat retains the source title (with a Copy suffix) and project
    association. It starts as an unshared, unpinned, active normal chat with a
    fresh activity timestamp. Message bookmarks and realtime-session state are
    reset by :func:`clone_chat_message_for_new_chat`.

    Returns: {"status": "success"}
    """
    # Ensure the chat exists and belongs to the user
    src_chat = get_chat(db, chat_id, user_id)

    # Create the new chat (copy basic fields)
    new_chat = Chats(
        user_id=user_id,
        title=(src_chat.title + " (Copy)") if src_chat.title else None,
        project_id=src_chat.project_id,
        share=None,
        share_id=None,
        archived=False,
        pinned_position=None,
        meta=None,
        created_at=datetime.now(timezone.utc),
        last_updated_at=datetime.now(timezone.utc),
        response_version=0,
        last_completed_generation_id=None,
    )
    db.add(new_chat)
    db.flush()  # get new_chat.id before inserting messages

    # Copy messages ordered by time (and id for stability)
    messages = (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == src_chat.id)
        .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        .all()
    )

    id_map: dict[str, str] = {}
    for m in messages:
        db.add(clone_chat_message_for_new_chat(m, new_chat.id, id_map))

    # Always mark duplicated chat as updated "now" so it surfaces to top
    new_chat.last_updated_at = datetime.now(timezone.utc)

    db.commit()
    # Return updated list of chats for the user
    return {"status": "success"}



# -------------------
# Update Chat Title
# -------------------
def update_chat_title(db, chat_id: str, title: str, user_id: str | None = None):
    """Update the title of a chat, optionally filtering by user_id."""
    if not chat_id:
        raise HTTPException(status_code=404, detail="Cannot get chat!")
    query = db.query(Chats).filter(Chats.id == chat_id)
    if user_id:
        query = query.filter(Chats.user_id == user_id)
    chat = query.first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_not_shadow_deleted(chat)
    
    chat.title = sanitize_chat_text(title)
    db.commit()
    return True
    


# -------------------
# Rename Chat
# -------------------
def rename_chat(user_id: str, chat_id: str, title: str, db):
    """Rename a chat owned by the user."""
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_not_shadow_deleted(chat)
    chat.title = sanitize_chat_text(title)
    db.commit()
    return chat



# -------------------
# Update Chat Project
# -------------------
def update_chat_project(user_id: str, chat_id: str, project_id: str | None, db):
    """Update the project association of a chat owned by the user."""
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_not_shadow_deleted(chat)

    if project_id:
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found!")

    chat.project_id = project_id
    db.commit()
    db.refresh(chat)
    return chat



# -------------------
# Archive Chat
# -------------------
def archive_chat(user_id: str, chat_id: str, db):
    """Archive a chat and clear its pinned position."""
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_not_shadow_deleted(chat)
    
    chat.archived = True
    chat.pinned_position = None
    db.commit()
    db.refresh(chat)
    return chat



# -------------------
# Unarchive Chat
# -------------------
def unarchive_chat(user_id: str, chat_id: str, db):
    """Unarchive a chat."""
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_not_shadow_deleted(chat)
    
    chat.archived = False
    db.commit()
    db.refresh(chat)
    return chat



# -------------------
# Get Archived Chats
# -------------------
def get_archived_chats(db, user_id: str):
    """Get all archived chats for a user, excluding shadow-deleted ones."""
    query = db.query(Chats).filter(Chats.user_id == user_id, Chats.archived == True)
    chats = query.order_by(Chats.last_updated_at.desc()).all()
    filtered: list[Chats] = []
    for chat in chats:
        meta = _meta_to_dict(getattr(chat, "meta", None))
        if _is_shadow_deleted_meta(meta):
            continue
        filtered.append(chat)
    return filtered



# -------------------
# Delete Chat
# -------------------
def delete_chat(user_id: str, group_id: str, chat_id: str, db):
    """Delete a chat, or shadow-delete it based on group settings."""
    allow_chat_deletion = get_group_setting_value(group_id, "chat", "allow_chat_deletion", db)
    if not allow_chat_deletion:
        raise HTTPException(status_code=409, detail="Chat deletion is disabled")
    shadow_chat_deletion = bool(get_group_setting_value(group_id, "chat", "shadow_chat_deletion", db))
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_not_shadow_deleted(chat)
    if not _cancel_active_generation_for_chat(chat_id):
        raise HTTPException(
            status_code=409,
            detail="The active generation is still stopping. Please try deleting the chat again in a moment.",
        )

    if shadow_chat_deletion:
        _mark_chat_shadow_deleted(chat)
        db.commit()
        record_chat_deleted_metric(user_id=user_id)
        return True

    messages_to_delete = db.query(ChatMessages).filter(ChatMessages.chat_id == chat_id).all()
    deleted_message_contents = _message_content_snapshot(messages_to_delete)
    deep_research_cleanup = _delete_deep_research_runs_for_chats(
        db,
        user_id,
        [chat_id],
    )

    # First delete all messages belonging to the chat to avoid orphans
    db.query(ChatMessages).filter(ChatMessages.chat_id == chat_id).delete(synchronize_session=False)
    # Then delete the chat itself
    db.delete(chat)
    db.commit()
    record_chat_deleted_metric(user_id=user_id)
    _cleanup_deep_research_artifacts_after_commit(deep_research_cleanup)
    _cleanup_orphaned_meeting_transcript_files_after_commit(db, user_id, deleted_message_contents)
    return True



# -------------------
# Delete All Chats
# -------------------
def delete_all_chats(user_id: str, group_id: str, db):
    """
    Delete all chats for the given user, including all their messages. If the
    group's shadow deletion setting is enabled, chats are only hidden.

    Returns True on success.
    """
    allow_chat_deletion = get_group_setting_value(group_id, "chat", "allow_chat_deletion", db)
    if not allow_chat_deletion:
        raise HTTPException(status_code=409, detail="Chat deletion is disabled")

    shadow_chat_deletion = bool(get_group_setting_value(group_id, "chat", "shadow_chat_deletion", db))

    user_chats = db.query(Chats).filter(Chats.user_id == user_id).all()
    if not user_chats:
        # Nothing to delete
        return True
    for chat in user_chats:
        if not _cancel_active_generation_for_chat(str(chat.id)):
            raise HTTPException(
                status_code=409,
                detail="An active generation is still stopping. Please try deleting chats again in a moment.",
            )

    if shadow_chat_deletion:
        deleted_count = 0
        changed = False
        for chat in user_chats:
            if _mark_chat_shadow_deleted(chat):
                changed = True
                deleted_count += 1
        if changed:
            db.commit()
            for _ in range(deleted_count):
                record_chat_deleted_metric(user_id=user_id)
        return True

    chat_ids = [c.id for c in user_chats]
    messages_to_delete = db.query(ChatMessages).filter(ChatMessages.chat_id.in_(chat_ids)).all()
    deleted_message_contents = _message_content_snapshot(messages_to_delete)

    try:
        deep_research_cleanup = _delete_deep_research_runs_for_chats(
            db,
            user_id,
            chat_ids,
        )
        # Delete messages first to avoid orphans
        db.query(ChatMessages).filter(ChatMessages.chat_id.in_(chat_ids)).delete(synchronize_session=False)
        # Then delete the chats
        db.query(Chats).filter(Chats.id.in_(chat_ids)).delete(synchronize_session=False)
        db.commit()
        for _ in chat_ids:
            record_chat_deleted_metric(user_id=user_id)
        _cleanup_deep_research_artifacts_after_commit(deep_research_cleanup)
        _cleanup_orphaned_meeting_transcript_files_after_commit(db, user_id, deleted_message_contents)
    except Exception:
        db.rollback()
        raise

    return True


def _delete_deep_research_runs_for_chats(
    db,
    user_id: str | None,
    chat_ids: list[str],
) -> list[dict]:
    """Delete chat-owned research rows and return post-commit storage work."""

    if not chat_ids:
        return []
    from app.tools.deep_research.models import DeepResearchRun
    from app.tools.deep_research.storage import deep_research_run_cleanup_descriptor

    query = db.query(DeepResearchRun).filter(DeepResearchRun.chat_id.in_(chat_ids))
    if user_id is not None:
        query = query.filter(DeepResearchRun.user_id == str(user_id))
    runs = query.all()
    cleanup = [deep_research_run_cleanup_descriptor(run) for run in runs]
    if runs:
        db.query(DeepResearchRun).filter(
            DeepResearchRun.id.in_([run.id for run in runs])
        ).delete(synchronize_session=False)
    return cleanup


def _delete_deep_research_runs_for_user(
    db,
    user_id: str,
) -> list[dict]:
    """Delete every research row owned by a user and return storage cleanup work.

    User deletion cannot limit this query to known chat IDs because a valid
    Deep Research run may have no persisted chat. Capturing those rows before
    the user foreign-key cascade runs preserves the provider and object paths
    required to remove their external artifacts after the transaction commits.
    """

    from app.tools.deep_research.models import DeepResearchRun
    from app.tools.deep_research.storage import deep_research_run_cleanup_descriptor

    runs = (
        db.query(DeepResearchRun)
        .filter(DeepResearchRun.user_id == str(user_id))
        .all()
    )
    cleanup = [deep_research_run_cleanup_descriptor(run) for run in runs]
    if runs:
        db.query(DeepResearchRun).filter(
            DeepResearchRun.id.in_([run.id for run in runs])
        ).delete(synchronize_session=False)
    return cleanup


def _cleanup_deep_research_artifacts_after_commit(
    cleanup_descriptors: list[dict],
) -> None:
    """Best-effort removal of blobs after their owning transaction commits."""

    from app.tools.deep_research.storage import delete_deep_research_run_artifacts

    for descriptor in cleanup_descriptors:
        try:
            delete_deep_research_run_artifacts(**descriptor)
        except Exception:
            # The database deletion is already durable. Keep the user request
            # successful while surfacing storage failures for operator repair.
            logger.exception(
                "Failed to clean up hard-deleted Deep Research artifacts",
                extra={"run_id": descriptor.get("run_id")},
            )



# ---------------------------------------------------------------------------
# Chat Messages
# ---------------------------------------------------------------------------
class ChatMessages(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_chat_created", "chat_id", "created_at"),
        UniqueConstraint(
            "chat_id",
            "realtime_session_id",
            "realtime_turn_id",
            "role",
            name="uq_chat_messages_realtime_turn_role",
        ),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    chat_id = Column(String, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    model_id = Column(String, nullable=False)
    content = Column(String, nullable=False)
    role = Column(String, nullable=False) # "user", "assistant"
    reference_id = Column(String, nullable=True) # The user message id
    realtime_session_id = Column(String, nullable=True)
    realtime_turn_id = Column(String, nullable=True)
    generation = Column(JSON, nullable=True, default={"generation_number": 1})
    thinking = Column(String, nullable=True)
    retry_count = Column(SAInteger, nullable=False, default=0)
    bookmarked = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False)


SCHEMA = [
    {
        "type": "reasoning", # "reasoning", "tool_call", "tool_call_result", "content", "file", "user", "widget",
        # Generic tool_call blocks omit content and derive their display label
        # from the canonical tool_name/arguments metadata below.
        "content": "", # str
        "meta": {
            "tool_name": "",
            "arguments": "{}",
            "tool_call_id": "",
            "tool_namespace": "",
        },
        "images": [],
        "videos": [],
        "audios": [],
        "documents": [],
        "youtube": [],
        "sources": [],
        "tool_name": "", # Legacy/result-block compatibility only.
    }
]

def _decode_message_content(raw):
    """Decode raw message content (JSON string, dict, list, or plain string) into its parsed form."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return deepcopy(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return ""
        try:
            return json.loads(stripped)
        except Exception:
            return raw
    return raw


ATTACHMENT_FIELDS = ("images", "videos", "audios", "documents")


def _extract_attachment_file_ids(raw_attachment_value) -> set[str]:
    """Collect normalized file IDs from a message attachment field."""
    file_ids: set[str] = set()
    if not isinstance(raw_attachment_value, list):
        return file_ids

    for attachment in raw_attachment_value:
        if isinstance(attachment, dict):
            raw_file_id = attachment.get("id") or attachment.get("file_id")
        else:
            raw_file_id = attachment
        normalized_file_id = str(raw_file_id or "").strip()
        if normalized_file_id:
            file_ids.add(normalized_file_id)
    return file_ids


def _collect_attachment_file_ids_from_message_content(raw_content) -> set[str]:
    """Collect attachment file IDs embedded in message content blocks."""
    decoded = _decode_message_content(raw_content)
    if isinstance(decoded, list):
        blocks = decoded
    elif isinstance(decoded, dict):
        blocks = [decoded]
    else:
        return set()

    file_ids: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for field in ATTACHMENT_FIELDS:
            file_ids.update(_extract_attachment_file_ids(block.get(field)))
    return file_ids


_MISSING_MESSAGE_CONTENT = object()


def _message_content_snapshot(messages) -> list:
    """Snapshot message content values before ORM rows are deleted or expired."""
    return [getattr(message, "content", None) for message in messages or []]


def _collect_attachment_file_ids_for_messages(messages) -> set[str]:
    """Collect all attachment file IDs referenced by message rows or content snapshots."""
    file_ids: set[str] = set()
    for message in messages or []:
        raw_content = getattr(message, "content", _MISSING_MESSAGE_CONTENT)
        if raw_content is _MISSING_MESSAGE_CONTENT:
            raw_content = message
        file_ids.update(_collect_attachment_file_ids_from_message_content(raw_content))
    return file_ids


def _collect_meeting_transcript_file_ids(db, user_id: str, messages) -> set[str]:
    """Resolve generated meeting transcript file IDs referenced by the given messages."""
    attachment_file_ids = _collect_attachment_file_ids_for_messages(messages)
    if not attachment_file_ids:
        return set()

    transcript_file_ids: set[str] = set()
    file_rows = db.query(Files).filter(Files.user_id == user_id, Files.id.in_(attachment_file_ids)).all()
    for file_row in file_rows:
        if _meta_to_dict(getattr(file_row, "meta", None)).get("meeting_transcript"):
            transcript_file_ids.add(str(file_row.id))
    return transcript_file_ids


def _collect_user_chat_attachment_file_ids(db, user_id: str) -> set[str]:
    """Collect attachment file IDs still referenced by the user's remaining chats."""
    rows = (
        db.query(ChatMessages)
        .join(Chats, Chats.id == ChatMessages.chat_id)
        .filter(Chats.user_id == user_id)
        .all()
    )
    return _collect_attachment_file_ids_for_messages(rows)


def _cleanup_orphaned_meeting_transcript_files(db, user_id: str, deleted_messages) -> None:
    """Delete generated meeting transcript files once no remaining chat message references them."""
    transcript_file_ids = _collect_meeting_transcript_file_ids(db, user_id, deleted_messages)
    if not transcript_file_ids:
        return

    still_referenced_file_ids = _collect_user_chat_attachment_file_ids(db, user_id)
    orphaned_file_ids = transcript_file_ids - still_referenced_file_ids
    if not orphaned_file_ids:
        return

    from app.files.utils import _delete_file_record

    file_rows = db.query(Files).filter(Files.user_id == user_id, Files.id.in_(orphaned_file_ids)).all()
    for file_row in file_rows:
        if _meta_to_dict(getattr(file_row, "meta", None)).get("meeting_transcript"):
            _delete_file_record(db, file_row.user_id, file_row)


def _cleanup_orphaned_meeting_transcript_files_after_commit(db, user_id: str, deleted_messages) -> None:
    try:
        _cleanup_orphaned_meeting_transcript_files(db, user_id, deleted_messages)
    except Exception:
        logger.exception("Failed to clean up orphaned meeting transcript files after chat deletion")


def _normalize_edit_attachment_ids(file_ids):
    """Normalize a list of file IDs by deduplicating and stripping whitespace. Returns None if input is None."""
    if file_ids is None:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_file_id in file_ids:
        file_id = str(raw_file_id or "").strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        normalized.append(file_id)
    return normalized


def _build_edited_user_message_content(
    existing_content,
    text: str,
    attachment_updates: dict[str, list[str] | None],
    chat_reference_updates: list[dict] | None = None,
):
    """Rebuild a user message's content blocks after an edit, applying text, files, and chat references."""
    decoded = _decode_message_content(existing_content)
    if isinstance(decoded, list):
        blocks = [deepcopy(block) if isinstance(block, dict) else {"type": "content", "content": str(block)} for block in decoded]
    elif isinstance(decoded, dict):
        blocks = [deepcopy(decoded)]
    elif isinstance(decoded, str):
        blocks = [{"type": "user", "content": decoded}]
    else:
        blocks = []

    if not blocks:
        blocks = [{"type": "user"}]

    primary_block = next(
        (
            block for block in blocks
            if isinstance(block, dict) and str(block.get("type") or "").lower() in {"user", "content"}
        ),
        None,
    )
    if primary_block is None:
        primary_block = next((block for block in blocks if isinstance(block, dict)), None)
    if primary_block is None:
        primary_block = {"type": "user"}
        blocks.append(primary_block)

    primary_block["type"] = "user"
    primary_block["content"] = text

    for field, ids in attachment_updates.items():
        if ids is None:
            continue
        if ids:
            primary_block[field] = ids
        else:
            primary_block.pop(field, None)

    if chat_reference_updates is not None:
        if chat_reference_updates:
            primary_block["chat_references"] = chat_reference_updates
        else:
            primary_block.pop("chat_references", None)

    return json.dumps(blocks, ensure_ascii=False)

# -------------------
# Create Chat Message
# -------------------
def create_chat_message(
    db,
    chat_id: str,
    model_id: str,
    role: str,
    reference_id: str | None = None,
    content: list | None = None,
    retry_count: int | None = None,
    realtime_session_id: str | None = None,
    realtime_turn_id: str | None = None,
    commit: bool = True,
):
    """Create a new chat message and update the chat's last_updated_at timestamp."""
    chat = db.query(Chats).filter(Chats.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_not_shadow_deleted(chat)

    serialized_content = content
    if content is not None and not isinstance(content, str):
        try:
            serialized_content = json.dumps(content)
        except (TypeError, ValueError):
            serialized_content = str(content)
    chat_message = ChatMessages(
        chat_id=chat_id,
        model_id=model_id,
        role=role,
        reference_id=reference_id,
        realtime_session_id=realtime_session_id,
        realtime_turn_id=realtime_turn_id,
        content=serialized_content,
        retry_count=retry_count if retry_count is not None else 0,
        created_at=datetime.now(timezone.utc)
    )
    db.add(chat_message)
    # Update chat's last_updated_at timestamp whenever a new message is added
    chat.last_updated_at = datetime.now(timezone.utc)
    if commit:
        db.commit()
        db.refresh(chat_message)
    else:
        db.flush()
    record_chat_message_metric(role, serialized_content, model=model_id, chat_id=chat_id)
    return chat_message



# -------------------
# Get Chat Messages
# -------------------
def get_chat_messages(db, chat_id: str):
    """Retrieve all messages for a chat, ordered by created_at ascending."""
    chat_history = (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == chat_id)
        .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        .all()
    )
    return chat_history


def _cancel_active_generation_for_chat(chat_id: str) -> bool:
    """Cancel an active generation for a chat without blocking the request path.

    Returns True only when no generation is active. If a generation is active,
    cancellation is requested and callers should return a retryable response so
    the DB session and request worker are not held while the stream winds down.
    """
    status = stream_hub.get_status(chat_id)
    if not status.get("active"):
        return True

    active_generation_id = str(status.get("generation_id") or "").strip()
    if active_generation_id:
        cancel_registry.cancel(active_generation_id)

    logger.info(
        "Requested active chat generation cancellation before deletion",
        extra={"chat_id": chat_id, "generation_id": active_generation_id or None},
    )
    return False


def _cleanup_chat_after_empty_transcript(chat, group_id: str | None, db) -> None:
    """Remove or shadow-delete a chat that no longer has any messages."""
    shadow_chat_deletion = False
    if group_id:
        try:
            shadow_chat_deletion = bool(get_group_setting_value(group_id, "chat", "shadow_chat_deletion", db))
        except Exception:
            shadow_chat_deletion = False

    if shadow_chat_deletion:
        _mark_chat_shadow_deleted(chat)
        db.commit()
        return

    deep_research_cleanup = _delete_deep_research_runs_for_chats(
        db,
        str(chat.user_id),
        [str(chat.id)],
    )
    db.delete(chat)
    db.commit()
    _cleanup_deep_research_artifacts_after_commit(deep_research_cleanup)



# -------------------
# Delete Chat Message
# -------------------
def delete_chat_message(user_id: str, group_id: str | None, message_id: str, db):
    """
    Delete a chat message by its ID for the authenticated user.

    Deleting a user message also removes every later message in the chat so
    the transcript stays consistent.

    Returns metadata describing the resulting chat state after deletion.
    """
    from app.groups.init import get_user_group_setting_value

    if not bool(get_user_group_setting_value(user_id, "chat", "allow_delete_messages", db)):
        raise HTTPException(status_code=403, detail="Message deletion is disabled for your group.")

    # Find the message first
    msg = db.query(ChatMessages).filter(ChatMessages.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found!")

    # Ensure the chat exists and belongs to the user
    chat = db.query(Chats).filter(Chats.id == msg.chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_current_project_access(user_id, chat, db)

    meta = _meta_to_dict(getattr(chat, "meta", None))
    if _is_shadow_deleted_meta(meta) or _is_temp_meta(meta):
        raise HTTPException(status_code=404, detail="Chat not found!")

    ordered_messages = get_chat_messages(db, chat.id)
    target_index = next((index for index, current in enumerate(ordered_messages) if current.id == message_id), None)
    if target_index is None:
        raise HTTPException(status_code=404, detail="Message not found!")

    should_truncate_chat = msg.role == "user"

    if should_truncate_chat and not _cancel_active_generation_for_chat(chat.id):
        raise HTTPException(
            status_code=409,
            detail="The active generation is still stopping. Please try deleting the message again in a moment.",
        )

    chat_id = str(chat.id)
    messages_to_delete = ordered_messages[target_index:] if should_truncate_chat else [ordered_messages[target_index]]
    deleted_message_contents = _message_content_snapshot(messages_to_delete)
    remaining_messages = ordered_messages[:target_index] if should_truncate_chat else [
        current for current in ordered_messages if current.id != message_id
    ]

    for current in messages_to_delete:
        db.delete(current)

    if not remaining_messages:
        _cleanup_chat_after_empty_transcript(chat, group_id, db)
        _cleanup_orphaned_meeting_transcript_files_after_commit(db, user_id, deleted_message_contents)
        return {
            "chat_id": chat_id,
            "chat_deleted": True,
            "messages": [],
        }

    # Update chat last_updated_at to the last remaining message timestamp if available
    last = remaining_messages[-1] if remaining_messages else None
    if last and getattr(last, "created_at", None):
        chat.last_updated_at = last.created_at
    else:
        chat.last_updated_at = datetime.now(timezone.utc)

    db.commit()
    _cleanup_orphaned_meeting_transcript_files_after_commit(db, user_id, deleted_message_contents)

    return {
        "chat_id": chat_id,
        "chat_deleted": False,
        "messages": [],
    }



# -------------------
# Edit Chat Message
# -------------------
def edit_chat_message(
    user_id: str,
    message_id: str,
    new_content: str,
    db,
    image_ids: list[str] | None = None,
    video_ids: list[str] | None = None,
    audio_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    chat_reference_ids: list[str] | None = None,
):
    """
    Edit a user chat message by its ID.
    Only user messages can be edited.

    Returns the updated message.
    """
    # Find the message first
    msg = db.query(ChatMessages).filter(ChatMessages.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found!")

    # Only allow editing user messages
    if msg.role != "user":
        raise HTTPException(status_code=400, detail="Only user messages can be edited!")

    # Ensure the chat exists and belongs to the user
    chat = db.query(Chats).filter(Chats.id == msg.chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    _ensure_current_project_access(user_id, chat, db)

    meta = _meta_to_dict(getattr(chat, "meta", None))
    if _is_shadow_deleted_meta(meta) or _is_temp_meta(meta):
        raise HTTPException(status_code=404, detail="Chat not found!")

    sanitized_content = sanitize_chat_text(new_content)
    attachment_updates = {
        "images": _normalize_edit_attachment_ids(image_ids),
        "videos": _normalize_edit_attachment_ids(video_ids),
        "audios": _normalize_edit_attachment_ids(audio_ids),
        "documents": _normalize_edit_attachment_ids(document_ids),
    }
    chat_reference_updates = None
    if chat_reference_ids is not None:
        from app.chats.utils import resolve_chat_reference_payload

        chat_reference_updates, _ = resolve_chat_reference_payload(
            user_id,
            db,
            chat_reference_ids,
            current_chat_id=str(chat.id),
            project_id=getattr(chat, "project_id", None),
        )

    decoded_existing = _decode_message_content(msg.content)
    if (
        any(value is not None for value in attachment_updates.values())
        or chat_reference_updates is not None
        or isinstance(decoded_existing, (list, dict))
    ):
        msg.content = _build_edited_user_message_content(
            msg.content,
            sanitized_content,
            attachment_updates,
            chat_reference_updates,
        )
    else:
        msg.content = sanitized_content

    # Update chat last_updated_at
    chat.last_updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(msg)

    return msg



# -------------------
# Toggle Message Bookmark
# -------------------
def toggle_message_bookmark(user_id: str, message_id: str, db):
    """
    Toggle the bookmark status of a message (user or assistant).

    Returns the updated message with bookmark status.
    """
    msg = db.query(ChatMessages).filter(ChatMessages.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found!")

    if msg.role not in ("user", "assistant"):
        raise HTTPException(status_code=400, detail="Only user and assistant messages can be bookmarked!")

    chat = db.query(Chats).filter(Chats.id == msg.chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")

    meta = _meta_to_dict(getattr(chat, "meta", None))
    if _is_shadow_deleted_meta(meta) or _is_temp_meta(meta):
        raise HTTPException(status_code=404, detail="Chat not found!")

    msg.bookmarked = not msg.bookmarked
    db.commit()
    db.refresh(msg)

    return {"message_id": msg.id, "bookmarked": msg.bookmarked, "role": msg.role}



# -------------------
# Get Bookmarked Messages
# -------------------
def get_bookmarked_messages(user_id: str, db):
    """
    Get all bookmarked messages (user and assistant) for a user.

    Returns a list of bookmarked messages with chat info.

    The bookmark workspace only needs message and chat metadata that is directly
    actionable in the UI. We intentionally omit assistant model metadata here so
    the bookmark payload stays focused on the bookmark itself.
    """
    bookmarked = (
        db.query(ChatMessages)
        .join(Chats, ChatMessages.chat_id == Chats.id)
        .filter(
            Chats.user_id == user_id,
            ChatMessages.bookmarked == True,
            ChatMessages.role.in_(["user", "assistant"]),
        )
        .order_by(ChatMessages.created_at.desc())
        .all()
    )

    result = []
    for msg in bookmarked:
        chat = db.query(Chats).filter(Chats.id == msg.chat_id).first()
        if not chat:
            continue
        meta = _meta_to_dict(getattr(chat, "meta", None))
        if _is_shadow_deleted_meta(meta) or _is_temp_meta(meta):
            continue
        result.append({
            "id": msg.id,
            "chat_id": msg.chat_id,
            "chat_title": chat.title,
            "content": msg.content,
            "role": msg.role,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "bookmarked": msg.bookmarked,
        })

    return result
