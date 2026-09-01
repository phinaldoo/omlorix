"""Schemas for admin operations adjacent to settings categories."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, constr


class AdminFileStorageSummary(BaseModel):
    total_files: int = 0
    total_storage_bytes: int = 0
    users_with_files: int = 0


class AdminFileStorageUserUsage(BaseModel):
    user_id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    file_count: int = 0
    storage_bytes: int = 0
    latest_file_at: datetime | None = None
    uploads_allowed: bool = True
    file_count_limit: int | None = None
    storage_bytes_limit: int | None = None
    file_count_percent: float | None = None
    storage_percent: float | None = None


class AdminFileStorageStatisticsResponse(BaseModel):
    summary: AdminFileStorageSummary = Field(default_factory=AdminFileStorageSummary)
    items: list[AdminFileStorageUserUsage] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
    has_more: bool = False
    sort_field: str = "storage_bytes"
    sort_direction: Literal["asc", "desc"] = "desc"


class AdminModelOption(BaseModel):
    """One selectable provider model exposed to the admin settings UI."""

    value: str
    label: str


class AdminModelOptionsResponse(BaseModel):
    """Bound the response contract shared by dynamic model selectors."""

    provider_id: str | None = None
    options: list[AdminModelOption] = Field(default_factory=list)


# -------------------
# Admin: Settings Schema Query
# -------------------
class AdminSettingsSchemaQuery(BaseModel):
    page: constr(min_length=1, max_length=64)
    include_values: bool = False


# -------------------
# Admin: Privacy Policy Update
# -------------------
class AdminPrivacyPolicyUpdate(BaseModel):
    content: constr(min_length=1)
    notice_mode: Literal["none", "modal"] = "none"
    notice_message_html: str = ""


# -------------------
# Admin: Terms of Service Update
# -------------------
class AdminTermsOfServiceUpdate(BaseModel):
    content: constr(min_length=1)
