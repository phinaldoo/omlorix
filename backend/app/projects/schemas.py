from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict


class ProjectSettings(BaseModel):
    icon: str = ""
    icon_color: str = ""
    system_instruction: str = ""
    separate_memory_enabled: bool = False


class Project(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    images: Optional[str] = None
    videos: Optional[str] = None
    audios: Optional[str] = None
    documents: Optional[str] = None
    created_at: datetime
    last_updated_at: datetime
    settings: ProjectSettings
    link_share_id: Optional[str] = None

class ProjectWithSharing(BaseModel):
    """Project with sharing metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    images: Optional[str] = None
    videos: Optional[str] = None
    audios: Optional[str] = None
    documents: Optional[str] = None
    created_at: datetime
    last_updated_at: datetime
    settings: ProjectSettings
    link_share_id: Optional[str] = None
    has_link_share: bool = False
    is_owner: bool = True
    is_shared: bool = False
    member_count: int = 0
    owner_name: Optional[str] = None

class ProjectListResponse(BaseModel):
    projects: list[ProjectWithSharing]


class ProjectCreateRequest(BaseModel):
    title: str
    icon: Optional[str] = None
    icon_color: Optional[str] = None
    system_instruction: Optional[str] = None
    separate_memory_enabled: Optional[bool] = None


class ProjectResponse(BaseModel):
    status: str
    project: Project


class ProjectUpdateRequest(BaseModel):
    project_id: str
    title: Optional[str] = None
    icon: Optional[str] = None
    icon_color: Optional[str] = None
    system_instruction: Optional[str] = None
    separate_memory_enabled: Optional[bool] = None


# ============================================================================
# Project Sharing Schemas
# ============================================================================

class CreateLinkShareRequest(BaseModel):
    project_id: str
    rotate: bool = False
    password: Optional[str] = None
    expires_at: Optional[datetime] = None


class CreateLinkShareResponse(BaseModel):
    share_id: str
    share_url: str
    has_password: bool = False
    created_at: Optional[str] = None
    expires_at: Optional[str] = None


class DeleteLinkShareRequest(BaseModel):
    project_id: str


class ProjectShareStatusResponse(BaseModel):
    link_share_id: Optional[str] = None
    share_url: Optional[str] = None
    member_count: int = 0
    has_password: bool = False
    created_at: Optional[str] = None
    expires_at: Optional[str] = None


class SharedProjectPreviewResponse(BaseModel):
    project_id: str
    title: str
    owner_name: str
    member_count: int
    settings: Optional[ProjectSettings] = None
    created_at: Optional[str] = None
    password_required: bool = False


class JoinProjectRequest(BaseModel):
    share_id: str


class JoinProjectByLinkRequest(BaseModel):
    password: Optional[str] = None


class JoinProjectResponse(BaseModel):
    project_id: str
    message: str


class ProjectMemberResponse(BaseModel):
    user_id: str
    display_name: str
    role: str
    joined_at: Optional[str] = None


class ProjectMembersResponse(BaseModel):
    members: List[ProjectMemberResponse]


class AddProjectMemberRequest(BaseModel):
    project_id: str
    user_id: str


class RemoveProjectMemberRequest(BaseModel):
    project_id: str
    user_id: str


class InviteUsersToProjectRequest(BaseModel):
    project_id: str
    user_ids: List[str]


class InviteUsersToProjectResponse(BaseModel):
    invited_count: int
    message: str


class LeaveProjectRequest(BaseModel):
    project_id: str
