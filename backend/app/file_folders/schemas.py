from pydantic import BaseModel, ConfigDict, Field, constr
from typing import List, Optional
from datetime import datetime
from enum import Enum


BulkIdentifierValue = constr(strip_whitespace=True, min_length=1, max_length=255)  # type: ignore
FILE_FOLDER_BULK_FILE_IDS_LIMIT = 20
FILE_FOLDER_BULK_USER_IDS_LIMIT = 20


class ShareTypeEnum(str, Enum):
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


# ---------------------------------------------------------------------------
# File Folder Schemas
# ---------------------------------------------------------------------------
class FileFolderCreate(BaseModel):
    name: str
    icon: str = "folder"
    icon_color: str = "#6366f1"


class FileFolderUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    icon_color: Optional[str] = None
    order: Optional[int] = None


class FileFolderResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    name: str
    icon: str
    icon_color: str
    order: int
    system_kind: Optional[str] = None
    clone_share_id: Optional[str] = None
    live_share_id: Optional[str] = None
    collaborate_share_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_subscribed: bool = False
    share_type: Optional[str] = None
    owner_name: Optional[str] = None
    subscriber_count: Optional[int] = None
    file_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class FileFolderFileIds(BaseModel):
    file_ids: List[BulkIdentifierValue] = Field(
        ...,
        min_length=1,
        max_length=FILE_FOLDER_BULK_FILE_IDS_LIMIT,
    )


class MoveFileRequest(BaseModel):
    file_id: BulkIdentifierValue
    folder_id: Optional[BulkIdentifierValue] = None


# ---------------------------------------------------------------------------
# Sharing Schemas
# ---------------------------------------------------------------------------
class ShareFolderRequest(BaseModel):
    folder_id: BulkIdentifierValue
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class ShareFolderResponse(BaseModel):
    share_id: str
    share_type: str
    share_url: str


class FolderShareStatusResponse(BaseModel):
    clone_share_id: Optional[str] = None
    live_share_id: Optional[str] = None
    collaborate_share_id: Optional[str] = None
    live_subscriber_count: int = 0
    collaborate_subscriber_count: int = 0


class DeleteFolderShareRequest(BaseModel):
    folder_id: BulkIdentifierValue
    share_type: Optional[ShareTypeEnum] = None


class SharedFolderPreviewResponse(BaseModel):
    share_id: str
    share_type: str
    name: str
    icon: Optional[str] = None
    icon_color: Optional[str] = None
    file_count: int = 0
    owner_name: Optional[str] = None
    created_at: Optional[str] = None


class AcceptSharedFolderResponse(BaseModel):
    folder_id: str
    name: str
    message: str


class CloneFolderResponse(BaseModel):
    folder_id: str
    name: str
    message: str


class InviteUsersRequest(BaseModel):
    item_id: BulkIdentifierValue
    user_ids: List[BulkIdentifierValue] = Field(
        ...,
        min_length=1,
        max_length=FILE_FOLDER_BULK_USER_IDS_LIMIT,
    )
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class InviteUsersResponse(BaseModel):
    invited_count: int
    message: str
