from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ShareTypeEnum(str, Enum):
    """Types of prompt sharing."""
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


class PromptCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=140)
    description: str = Field(default="", max_length=500)
    content: str = Field(default="")


class PromptUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=140)
    description: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None)
    expected_revision: int = Field(..., ge=1)


class PromptListItem(BaseModel):
    id: str
    user_id: str | None = None
    title: str
    description: str = ""
    content_preview: str = ""
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    revision: int = 1
    last_updated_by_name: str | None = None
    is_subscribed: bool = False
    share_type: str | None = None
    owner_name: str | None = None
    subscriber_count: int | None = None

    model_config = {"from_attributes": True}


class PromptListResponse(BaseModel):
    items: List[PromptListItem]
    limit: int
    offset: int
    has_more: bool = False


class PromptResponse(BaseModel):
    id: str
    user_id: str | None = None
    title: str
    description: str = ""
    content: str
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    revision: int = 1
    last_updated_by_name: str | None = None
    is_subscribed: bool = False
    share_type: str | None = None
    owner_name: str | None = None

    model_config = {"from_attributes": True}


class SharePromptRequest(BaseModel):
    prompt_id: str
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class SharePromptResponse(BaseModel):
    share_id: str
    share_type: str
    share_url: str


class PromptShareStatusResponse(BaseModel):
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    live_subscriber_count: int = 0
    collaborate_subscriber_count: int = 0


class DeletePromptShareRequest(BaseModel):
    prompt_id: str
    share_type: ShareTypeEnum | None = None


class SharedPromptPreviewResponse(BaseModel):
    share_id: str
    share_type: str
    title: str
    description: str | None = None
    content_preview: str | None = None
    owner_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AcceptSharedPromptResponse(BaseModel):
    prompt_id: str
    share_type: str
    message: str


class ClonePromptResponse(BaseModel):
    prompt_id: str
    message: str


class InviteUsersRequest(BaseModel):
    """Request to invite users to a shared prompt."""

    item_id: str
    user_ids: List[str]
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class InviteUsersResponse(BaseModel):
    """Response after sending invitations."""

    invited_count: int
    message: str
