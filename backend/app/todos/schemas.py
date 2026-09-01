from __future__ import annotations

from datetime import datetime
from enum import Enum
import json
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.todos.limits import (
    MAX_TODO_CONTENT_LENGTH,
    MAX_TODO_LIST_DESCRIPTION_LENGTH,
    MAX_TODO_METADATA_JSON_BYTES,
    MAX_TODO_NOTES_LENGTH,
    MAX_TODO_SORT_FIELD_LENGTH,
    MAX_TODO_SORT_FIELDS,
)


class ShareTypeEnum(str, Enum):
    """Types of todo list sharing."""
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


class TodoSortField(BaseModel):
    key: str = Field(..., min_length=1, max_length=MAX_TODO_SORT_FIELD_LENGTH)
    direction: str = Field(..., min_length=1, max_length=MAX_TODO_SORT_FIELD_LENGTH)


class TodoListBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_TODO_LIST_DESCRIPTION_LENGTH)
    icon: str = Field(..., min_length=1, max_length=255)
    sort_order: Optional[List[TodoSortField]] = Field(None, max_length=MAX_TODO_SORT_FIELDS)
    order: Optional[int] = None


class TodoListCreate(TodoListBase):
    """Validated fields accepted when an owner creates a todo list."""


class TodoListUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_TODO_LIST_DESCRIPTION_LENGTH)
    icon: Optional[str] = Field(None, min_length=1, max_length=255)
    sort_order: Optional[List[TodoSortField]] = Field(None, max_length=MAX_TODO_SORT_FIELDS)
    order: Optional[int] = None


class TodoListItemBase(BaseModel):
    id: str
    title: str
    description: str | None
    icon: str
    sort_order: List[TodoSortField]
    order: int
    created_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class TodoListResponse(TodoListItemBase):
    user_id: str | None = None
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    is_subscribed: Literal[False] = False
    subscriber_count: int | None = None

    model_config = {"from_attributes": True}


class SubscribedTodoListResponse(TodoListItemBase):
    is_subscribed: Literal[True] = True
    share_type: str | None = None  # 'live' or 'collaborate' for subscribed lists
    owner_name: str | None = None

    model_config = {"from_attributes": True}


class TodoListPageResponse(BaseModel):
    items: List[TodoListResponse | SubscribedTodoListResponse]
    limit: int
    offset: int
    has_more: bool = False


# ============================================================================
# Todo List Sharing Schemas
# ============================================================================

class ShareTodoListRequest(BaseModel):
    todo_list_id: str
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class ShareTodoListResponse(BaseModel):
    share_id: str
    share_type: str
    share_url: str


class TodoListShareStatusResponse(BaseModel):
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    live_subscriber_count: int = 0
    collaborate_subscriber_count: int = 0


class DeleteTodoListShareRequest(BaseModel):
    todo_list_id: str
    share_type: ShareTypeEnum | None = None  # If None, delete all shares


class TodoPreviewItem(BaseModel):
    content: str
    is_done: bool = False


class SharedTodoListPreviewResponse(BaseModel):
    share_id: str
    share_type: str
    title: str
    description: str | None = None
    icon: str | None = None
    todo_count: int = 0
    todos: list[TodoPreviewItem] = []
    owner_name: str | None = None
    created_at: str | None = None


class AcceptSharedTodoListResponse(BaseModel):
    todo_list_id: str
    title: str
    message: str


class CloneTodoListResponse(BaseModel):
    todo_list_id: str
    title: str
    message: str


class InviteUsersRequest(BaseModel):
    """Request to invite users to a shared item."""
    item_id: str
    user_ids: List[str]
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class InviteUsersResponse(BaseModel):
    """Response after sending invitations."""
    invited_count: int
    message: str


class TodoCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_TODO_CONTENT_LENGTH)
    notes: Optional[str] = Field(None, max_length=MAX_TODO_NOTES_LENGTH)
    priority: int = 0
    due_at: Optional[datetime] = None
    all_day: bool = False
    status: Literal["todo", "doing", "done"] = "todo"
    subtasks: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    order: Optional[int] = None

    @field_validator("subtasks", "links", "attachments", "tags")
    @classmethod
    def validate_metadata_size(cls, value):
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON serializable") from exc
        if len(encoded.encode("utf-8")) > MAX_TODO_METADATA_JSON_BYTES:
            raise ValueError(f"metadata must be {MAX_TODO_METADATA_JSON_BYTES} bytes or less")
        return value


class TodoUpdate(BaseModel):
    content: Optional[str] = Field(None, min_length=1, max_length=MAX_TODO_CONTENT_LENGTH)
    notes: Optional[str] = Field(None, max_length=MAX_TODO_NOTES_LENGTH)
    priority: Optional[int] = None
    due_at: Optional[datetime] = None
    clear_due_at: bool = False
    all_day: Optional[bool] = None
    status: Optional[Literal["todo", "doing", "done"]] = None
    subtasks: Optional[list[dict[str, Any]]] = None
    links: Optional[list[dict[str, Any]]] = None
    attachments: Optional[list[dict[str, Any]]] = None
    tags: Optional[list[str]] = None
    order: Optional[int] = None
    is_done: Optional[bool] = None
    is_marked: Optional[bool] = None

    @field_validator("subtasks", "links", "attachments", "tags")
    @classmethod
    def validate_metadata_size(cls, value):
        if value is None:
            return value
        return TodoCreate.validate_metadata_size(value)


class TodoResponse(BaseModel):
    id: str
    todo_list: str
    content: str
    notes: Optional[str]
    priority: int
    due_at: Optional[datetime]
    all_day: bool = False
    status: str = "todo"
    subtasks: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_done: bool
    is_marked: bool
    completed_at: Optional[datetime]
    order: int
    created_at: datetime | None
    updated_at: datetime | None
    share_type: str | None = None
    can_delete: bool = False

    model_config = {"from_attributes": True}


class TodoPageResponse(BaseModel):
    items: List[TodoResponse]
    limit: int
    offset: int
    has_more: bool = False


class MarkedTodoResponse(BaseModel):
    id: str
    todo_list: str
    content: str
    notes: Optional[str]
    priority: int
    due_at: Optional[datetime]
    all_day: bool = False
    status: str = "todo"
    subtasks: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_done: bool
    is_marked: bool
    completed_at: Optional[datetime]
    order: int
    created_at: datetime | None
    updated_at: datetime | None
    list_title: Optional[str] = None
    list_icon: Optional[str] = None
    share_type: str | None = None
    can_delete: bool = False
    is_subscribed: bool = False

    model_config = {"from_attributes": True}


class MarkedTodoPageResponse(BaseModel):
    items: List[MarkedTodoResponse]
    limit: int
    offset: int
    has_more: bool = False
