from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ShareTypeEnum(str, Enum):
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


class AgentAssetResponse(BaseModel):
    id: str
    agent_id: str
    file_name: str
    original_filename: str
    file_type: str
    file_category: str
    file_size: int
    created_at: str | None = None


class AttachAgentFilesRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list, min_length=1, max_length=20)


class AgentResponse(BaseModel):
    id: str
    user_id: str
    name: str
    icon: str
    base_model_id: str
    instruction: str = ""
    skill_id: str | None = None
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    owner_name: str | None = None
    is_subscribed: bool = False
    share_type: str | None = None
    is_shared: bool = False
    assets: list[AgentAssetResponse] = Field(default_factory=list)


class AgentListResponse(BaseModel):
    agents: list[AgentResponse] = Field(default_factory=list)


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field(..., min_length=1)
    base_model_id: str = Field(..., min_length=1)
    instruction: str = ""
    skill_id: str | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    icon: str | None = Field(default=None, min_length=1)
    base_model_id: str | None = Field(default=None, min_length=1)
    instruction: Optional[str] = None
    skill_id: str | None = None


class ShareAgentRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class ShareAgentResponse(BaseModel):
    share_id: str
    share_type: str
    share_url: str


class AgentShareStatusResponse(BaseModel):
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    live_subscriber_count: int = 0
    collaborate_subscriber_count: int = 0


class DeleteAgentShareRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    share_type: ShareTypeEnum | None = None


class SharedAgentPreviewResponse(BaseModel):
    share_id: str
    share_type: str
    name: str
    icon: str
    base_model_id: str
    base_model_accessible: bool
    can_complete_share_action: bool
    clone_skill_will_be_omitted: bool = False
    instruction_preview: str | None = None
    owner_name: str | None = None
    created_at: str | None = None


class AcceptSharedAgentResponse(BaseModel):
    agent_id: str
    name: str
    message: str


class CloneAgentResponse(BaseModel):
    agent_id: str
    name: str
    message: str


class InviteUsersRequest(BaseModel):
    item_id: str = Field(..., min_length=1)
    user_ids: List[str] = Field(default_factory=list)
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class InviteUsersResponse(BaseModel):
    invited_count: int
    message: str
