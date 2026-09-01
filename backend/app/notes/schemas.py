from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.notes.limits import MAX_NOTE_CONTENT_LENGTH


class ShareTypeEnum(str, Enum):
    """Types of note sharing."""
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


class NoteCreate(BaseModel):
    content: str = Field(default="", max_length=MAX_NOTE_CONTENT_LENGTH)


class NoteUpdate(BaseModel):
    content: str = Field(default="", max_length=MAX_NOTE_CONTENT_LENGTH)
    expected_updated_at: datetime


class NoteRevisionRequest(BaseModel):
    """Bind a destructive action to one observed note revision."""

    expected_updated_at: datetime


class NoteListItemBase(BaseModel):
    """Shared note list fields that are safe for owners and subscribers."""
    id: str
    user_id: str | None = None
    title: str
    snippet: str
    created_at: datetime | None
    updated_at: datetime | None
    is_subscribed: bool = False
    share_type: str | None = None
    owner_name: str | None = None

    model_config = {"from_attributes": True}


class OwnedNoteListItem(NoteListItemBase):
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    subscriber_count: int | None = None
    is_subscribed: Literal[False] = False


class SubscribedNoteListItem(NoteListItemBase):
    user_id: None = None
    is_subscribed: Literal[True] = True
    share_type: str
    owner_name: str | None = None


NoteListItem = OwnedNoteListItem | SubscribedNoteListItem


class NoteListResponse(BaseModel):
    items: list[NoteListItem]
    limit: int
    offset: int
    has_more: bool = False


class NoteReferencedFile(BaseModel):
    owner_id: str
    file_id: str
    kind: str
    label: str | None = None
    file_name: str | None = None
    file_type: str | None = None
    file_category: str | None = None
    file_size: int | None = None
    available: bool = True


class NoteContentResponse(BaseModel):
    """Full note content for a single note."""
    id: str
    content: str
    updated_at: datetime | None
    share_type: str | None = None
    referenced_files: list[NoteReferencedFile] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class NoteResponseBase(BaseModel):
    """Shared note response fields that are safe for owners and subscribers."""
    id: str
    user_id: str | None = None
    content: str
    created_at: datetime | None
    updated_at: datetime | None
    is_subscribed: bool = False
    share_type: str | None = None
    owner_name: str | None = None

    model_config = {"from_attributes": True}


class OwnedNoteResponse(NoteResponseBase):
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    is_subscribed: Literal[False] = False


class SubscribedNoteResponse(NoteResponseBase):
    user_id: None = None
    is_subscribed: Literal[True] = True
    share_type: str
    owner_name: str | None = None


NoteResponse = OwnedNoteResponse | SubscribedNoteResponse


# ============================================================================
# Note Sharing Schemas
# ============================================================================

class ShareNoteRequest(BaseModel):
    note_id: str
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class ShareNoteResponse(BaseModel):
    share_id: str
    share_type: str
    share_url: str


class NoteShareStatusResponse(BaseModel):
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    live_subscriber_count: int = 0
    collaborate_subscriber_count: int = 0


class DeleteNoteShareRequest(BaseModel):
    note_id: str
    share_type: ShareTypeEnum | None = None


class SharedNotePreviewResponse(BaseModel):
    share_id: str
    share_type: str
    content: str | None = None
    owner_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CloneNoteResponse(BaseModel):
    note_id: str
    message: str


class AcceptSharedNoteResponse(BaseModel):
    note_id: str
    share_type: str
    message: str


class InviteUsersRequest(BaseModel):
    """Request to invite users to a shared note."""
    item_id: str
    user_ids: list[str]
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class InviteUsersResponse(BaseModel):
    """Response after sending invitations."""
    invited_count: int
    message: str


# ============================================================================
# Note History Schemas
# ============================================================================

class NoteHistoryEntry(BaseModel):
    """Single history entry for a note."""
    id: str
    note_id: str
    user_id: str
    actor_type: str
    user_display_name: str
    content: str
    previous_content: str | None = None
    change_summary: str | None = None
    version_number: str
    created_at: str | None = None

    model_config = {"from_attributes": True}


class NoteHistoryResponse(BaseModel):
    """Response containing note history entries."""
    entries: list[NoteHistoryEntry]
    total_count: int
    has_more: bool = False


class RestoreNoteRequest(BaseModel):
    """Request to restore a note to a previous version."""
    history_id: str
    expected_updated_at: datetime


class RestoreNoteResponse(BaseModel):
    """Response after restoring a note."""
    success: bool
    message: str
    note_id: str
    restored_version: str
