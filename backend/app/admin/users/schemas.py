from datetime import datetime
from typing import Any, Dict, Literal, Optional

from app.users.schemas import UserCreate
from pydantic import BaseModel, ConfigDict, EmailStr, Field, constr


class AdminUserIdRequest(BaseModel):
    user_id: constr(min_length=1, max_length=64)


class AdminUserActive(AdminUserIdRequest):
    value: bool


# -------------------
# Admin: User Chats Request
# -------------------
class AdminUserChatsRequest(BaseModel):
    user_id: constr(min_length=1, max_length=64)
    reason: constr(min_length=1, max_length=255)


# -------------------
# Admin: Pending Deletion User
# -------------------
class AdminPendingDeletionUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    group_id: str
    deleted_at: datetime | None = None
    deletion_scheduled_for: datetime | None = None

# -------------------
# Admin: Change User Role
# -------------------
class AdminChangeRoleRequest(AdminUserIdRequest):
    role: constr(min_length=1, max_length=64)
    reason: Optional[constr(max_length=255)] = None


# -------------------
# Admin: User Profile Update
# -------------------
class AdminUserLock(BaseModel):
    is_locked: bool = False
    lock_until: Optional[str] = None
    type: Optional[str] = ""
    reason: Optional[str] = ""


class AdminUserProfileUpdate(BaseModel):
    user_id: constr(min_length=1, max_length=64)
    email: Optional[EmailStr] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    group_id: Optional[str] = None
    password: Optional[str] = None
    wrong_sign_in_attempts: Optional[int] = None
    lock: Optional[AdminUserLock] = None
    reason: Optional[constr(max_length=255)] = None


class AdminUserProfileReadRequest(AdminUserIdRequest):
    include_sensitive_profile: bool = False
    include_security: bool = False
    include_activity: bool = False
    reason: Optional[constr(min_length=3, max_length=255)] = None


class AdminUserProfileResponse(BaseModel):
    id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    group_id: str | None = None
    group_name: str | None = None
    role: str | None = None
    is_active: bool = True
    externally_managed: bool = False
    external_auth_provider: str | None = None
    wrong_sign_in_attempts: Optional[int] = None
    lock: Optional[AdminUserLock] = None
    created_at: Optional[str] = None
    last_active_at: Optional[str] = None


# -------------------
# Admin: User Settings Bulk Update
# -------------------
class AdminUserSettingsBulkUpdate(BaseModel):
    user_id: constr(min_length=1, max_length=64)
    settings: Dict[str, Dict[str, Any]] | None = None


# -------------------
# Admin: User Settings Bulk Update Result
# -------------------
class AdminUserSettingsBulkUpdateResult(BaseModel):
    status: Literal["success", "noop"] = "success"
    updated: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class AdminUserSecurityActionRequest(AdminUserIdRequest):
    reason: constr(min_length=3, max_length=255)


# -------------------
# Admin: User Settings Schema Query
# -------------------
class AdminUserSettingsSchemaQuery(BaseModel):
    include_values: bool = False
    user_id: constr(min_length=1, max_length=64) | None = None


# -------------------
# Admin: Create User
# -------------------
class AdminUserCreate(UserCreate):
    group_id: Optional[str] = None
    # Security flag: user must change password on first signin
    has_to_change_password: bool = False


# -------------------
# Admin: User Summary
# -------------------
class AdminUserList(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    group_name: str
    is_active: bool
    externally_managed: bool = False
    external_auth_provider: str | None = None
    last_active_at: datetime

class AdminUserPickerPage(BaseModel):
    users: list[AdminUserList]
    total: int
    offset: int
    limit: int
    has_more: bool


# -------------------
# Admin: User Chat Messages Request
# -------------------
class AdminUserChatMessagesRequest(AdminUserChatsRequest):
    chat_id: constr(min_length=1, max_length=64)


# -------------------
