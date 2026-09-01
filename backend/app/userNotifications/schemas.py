from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


TYPE_CHOICES = Literal["info", "warning", "error"]


class UserNotificationCreate(BaseModel):
    message: str = Field(..., min_length=1, max_length=255)
    category: Optional[str] = Field(default="general", max_length=64)
    notification_type: TYPE_CHOICES = "info"
    everyone: bool = False
    user_ids: List[str] | None = None
    group_ids: List[str] | None = None
    details: dict | None = None

    @field_validator("user_ids", "group_ids")
    @classmethod
    def _clean_ids(cls, value):
        if value is None:
            return None
        normalized = []
        seen = set()
        for raw in value:
            if not isinstance(raw, str):
                continue
            trimmed = raw.strip()
            if not trimmed or trimmed in seen:
                continue
            normalized.append(trimmed)
            seen.add(trimmed)
        return normalized or None

    @field_validator("category")
    @classmethod
    def _default_category(cls, value):
        return (value or "general").strip() or "general"


class UserNotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message: str
    category: str
    type: TYPE_CHOICES
    everyone: bool
    user_ids: List[str]
    group_ids: List[str]
    details: dict | None = None
    timestamp: Optional[str] = None

class UserNotificationsPaginatedResponse(BaseModel):
    notifications: List[UserNotificationResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class UserNotificationUpdate(BaseModel):
    message: Optional[str] = Field(default=None, min_length=1, max_length=255)
    category: Optional[str] = Field(default=None, max_length=64)
    notification_type: Optional[TYPE_CHOICES] = None
    everyone: Optional[bool] = None
    user_ids: Optional[List[str]] = None
    group_ids: Optional[List[str]] = None
    details: Optional[dict] = None

    @field_validator("user_ids", "group_ids")
    @classmethod
    def _clean_ids(cls, value):
        if value is None:
            return None
        normalized = []
        seen = set()
        for raw in value:
            if not isinstance(raw, str):
                continue
            trimmed = raw.strip()
            if not trimmed or trimmed in seen:
                continue
            normalized.append(trimmed)
            seen.add(trimmed)
        return normalized or None
