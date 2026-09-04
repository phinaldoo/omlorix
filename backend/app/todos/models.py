from copy import deepcopy
from enum import Enum
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, and_, or_
from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException, status

from app.database import Base
from app.settings.utils import get_public_url
from app.todos.limits import (
    MAX_TODO_CONTENT_LENGTH,
    MAX_TODO_LIST_DESCRIPTION_LENGTH,
    MAX_TODO_LISTS_PER_USER,
    MAX_TODO_METADATA_JSON_BYTES,
    MAX_TODO_NOTES_LENGTH,
    MAX_TODOS_PER_LIST,
    MAX_TODO_SORT_FIELD_LENGTH,
    MAX_TODO_SORT_FIELDS,
)
from app.utils.icon_security import sanitize_icon_input
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

class ShareType(str, Enum):
    """Types of todo list sharing."""
    CLONE = "clone"        # Recipient can clone the list as their own
    LIVE = "live"          # Recipient can view with live updates (read-only)
    COLLABORATE = "collaborate"  # Recipient can mutate tasks but cannot manage the source list.


DEFAULT_TODO_SORT_ORDER: List[Dict[str, str]] = [
    {"key": "priority", "direction": "desc"},
    {"key": "due_at", "direction": "asc"},
    {"key": "created_at", "direction": "desc"},
    {"key": "completed_at", "direction": "asc"},
    {"key": "content", "direction": "asc"},
]

TODO_STATUS_TODO = "todo"
TODO_STATUS_DOING = "doing"
TODO_STATUS_DONE = "done"
TODO_ALLOWED_STATUSES = {TODO_STATUS_TODO, TODO_STATUS_DOING, TODO_STATUS_DONE}


def _validate_text_length(value: str | None, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a string",
        )
    stripped = value.strip()
    if len(stripped) > max_length:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"{field_name} must be {max_length} characters or less",
        )
    return stripped


def _validate_json_payload(value: Any, field_name: str) -> Any:
    """Validate optional todo metadata JSON before storing it."""
    if value is None:
        return None
    # These fields are persisted as JSON arrays, so reject object/scalar payloads early.
    if field_name in {"subtasks", "links", "attachments"} and not isinstance(value, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a JSON array")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_TODO_METADATA_JSON_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"{field_name} must be {MAX_TODO_METADATA_JSON_BYTES} bytes or less",
        )
    return value


def _validate_string_list(value: Any, field_name: str) -> List[str]:
    """Normalize JSON list fields that are exposed as simple string collections."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a list")
    normalized: List[str] = []
    for item in value:
        item_value = _validate_text_length(str(item), field_name, MAX_TODO_SORT_FIELD_LENGTH)
        if item_value:
            normalized.append(item_value)
    return normalized


def _validate_todo_status(value: Any, *, is_done: bool | None = None) -> str:
    """Keep board status aligned with completion while accepting explicit status edits."""
    if value is None:
        return TODO_STATUS_DONE if is_done else TODO_STATUS_TODO
    normalized = str(value).strip().lower()
    if normalized not in TODO_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be one of: todo, doing, done",
        )
    return normalized


def _apply_done_status(todo: "Todos", is_done: bool) -> None:
    """Set completion fields and the kanban status together."""
    todo.is_done = is_done
    todo.completed_at = datetime.now(timezone.utc) if is_done else None
    if is_done:
        todo.status = TODO_STATUS_DONE
    elif getattr(todo, "status", TODO_STATUS_TODO) == TODO_STATUS_DONE:
        todo.status = TODO_STATUS_TODO


def _validate_sort_order_payload(sort_order: Optional[List[Dict[str, str]]]) -> Optional[List[Dict[str, str]]]:
    if sort_order is None:
        return None
    if not isinstance(sort_order, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sort_order must be a list")
    if len(sort_order) > MAX_TODO_SORT_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"sort_order must include {MAX_TODO_SORT_FIELDS} fields or less",
        )
    normalized = []
    for item in sort_order:
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sort_order entries must be objects",
            )
        key = _validate_text_length(item.get("key"), "sort_order.key", MAX_TODO_SORT_FIELD_LENGTH)
        direction = _validate_text_length(
            item.get("direction"),
            "sort_order.direction",
            MAX_TODO_SORT_FIELD_LENGTH,
        )
        if not key or not direction:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sort_order entries require key and direction",
            )
        normalized.append({"key": key, "direction": direction})
    return normalized


def _ensure_user_todo_list_quota(db: Session, user_id: str) -> None:
    current_count = db.query(TodoLists.id).filter(TodoLists.user_id == user_id).count()
    if current_count >= MAX_TODO_LISTS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Todo list limit of {MAX_TODO_LISTS_PER_USER} reached",
        )


def _ensure_todo_quota(db: Session, todo_list_id: str) -> None:
    current_count = db.query(Todos.id).filter(Todos.todo_list == todo_list_id).count()
    if current_count >= MAX_TODOS_PER_LIST:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Todo limit of {MAX_TODOS_PER_LIST} reached for this list",
        )


def _normalize_user_id(value: str, field_name: str = "user_id") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is required",
        )
    return value.strip()


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.isoformat()


def _parse_iso_datetime(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    raise ValueError("Datetime values must be ISO formatted strings or null")



class TodoLists(Base):
    __tablename__ = "todo_lists"
    __table_args__ = (
        Index('ix_todo_lists_catalog_page', 'user_id', 'order', 'id'),
        Index("ix_todo_lists_user_order_created", "user_id", "order", "created_at"),
    )

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False)
    order = Column(Integer, nullable=False, default=0)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True, default="")
    icon = Column(String, nullable=False)
    # Each share mode has an independent token so owners can revoke one mode
    # without invalidating links created for the other modes.
    clone_share_id = Column(String, nullable=True, index=True, unique=True)
    live_share_id = Column(String, nullable=True, index=True, unique=True)
    collaborate_share_id = Column(String, nullable=True, index=True, unique=True)
    sort_order = Column(JSON, nullable=False, default=lambda: deepcopy(DEFAULT_TODO_SORT_ORDER))
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))


class SharedTodoListSubscription(Base):
    """Tracks which users have subscribed to (accepted) shared todo lists."""
    __tablename__ = "shared_todo_list_subscriptions"
    __table_args__ = (
        Index('ix_todo_subscriber_access', 'subscriber_id', 'todo_list_id', 'share_type'),
        UniqueConstraint(
            "todo_list_id",
            "subscriber_id",
            name="uq_shared_todo_list_subscriptions_todo_list_subscriber",
        ),
    )

    id = Column(String, primary_key=True, index=True)
    todo_list_id = Column(String, ForeignKey("todo_lists.id", ondelete="CASCADE"), nullable=False, index=True)
    subscriber_id = Column(String, nullable=False, index=True)
    share_type = Column(String, nullable=False, default="live")  # 'live' or 'collaborate'
    subscribed_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)


def _get_user_todo_list(db: Session, user_id: str, todo_list_id: str) -> "TodoLists":
    normalized_user_id = _normalize_user_id(user_id)
    normalized_todo_list_id = _normalize_user_id(todo_list_id, "todo_list_id")
    todo_list = (
        db.query(TodoLists)
        .filter(TodoLists.id == normalized_todo_list_id, TodoLists.user_id == normalized_user_id)
        .first()
    )
    if not todo_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo list not found")
    return todo_list


def get_user_todo_list(db: Session, user_id: str, todo_list_id: str) -> "TodoLists":
    """
    Public wrapper around the internal todo list lookup helper so other modules
    can safely reuse the ownership validation logic.
    """
    return _get_user_todo_list(db, user_id, todo_list_id)


def get_subscription_for_todo_list(
    db: Session,
    user_id: str,
    todo_list_id: str,
) -> Optional["SharedTodoListSubscription"]:
    normalized_user_id = _normalize_user_id(user_id)
    normalized_todo_list_id = _normalize_user_id(todo_list_id, "todo_list_id")
    return (
        db.query(SharedTodoListSubscription)
        .filter(
            SharedTodoListSubscription.todo_list_id == normalized_todo_list_id,
            SharedTodoListSubscription.subscriber_id == normalized_user_id,
        )
        .first()
    )


def get_todo_list_subscription(
    db: Session,
    user_id: str,
    todo_list_id: str,
) -> Optional["SharedTodoListSubscription"]:
    return get_subscription_for_todo_list(db, user_id, todo_list_id)


def _subscription_grants_view(
    todo_list: "TodoLists",
    subscription: Optional["SharedTodoListSubscription"],
) -> bool:
    if not subscription:
        return False
    if subscription.share_type == ShareType.LIVE.value:
        return bool(todo_list.live_share_id)
    if subscription.share_type == ShareType.COLLABORATE.value:
        return bool(todo_list.collaborate_share_id)
    return False


def _subscription_grants_edit(
    todo_list: "TodoLists",
    subscription: Optional["SharedTodoListSubscription"],
) -> bool:
    return bool(
        subscription
        and subscription.share_type == ShareType.COLLABORATE.value
        and todo_list.collaborate_share_id
    )


def _get_accessible_todo_list(
    db: Session,
    user_id: str,
    todo_list_id: str,
    *,
    require_edit: bool = False,
) -> tuple["TodoLists", Optional["SharedTodoListSubscription"]]:
    normalized_user_id = _normalize_user_id(user_id)
    normalized_todo_list_id = _normalize_user_id(todo_list_id, "todo_list_id")
    todo_list = db.query(TodoLists).filter(TodoLists.id == normalized_todo_list_id).first()
    if not todo_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo list not found")

    if todo_list.user_id == normalized_user_id:
        return todo_list, None

    subscription = get_subscription_for_todo_list(db, normalized_user_id, normalized_todo_list_id)
    if not _subscription_grants_view(todo_list, subscription):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo list not found")
    if require_edit and not _subscription_grants_edit(todo_list, subscription):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this todo list",
        )
    return todo_list, subscription


def get_accessible_todo_list(db: Session, user_id: str, todo_list_id: str) -> "TodoLists":
    todo_list, _subscription = _get_accessible_todo_list(db, user_id, todo_list_id)
    return todo_list


def can_user_view_todo_list(db: Session, user_id: str, todo_list_id: str) -> bool:
    try:
        _get_accessible_todo_list(db, user_id, todo_list_id)
    except HTTPException:
        return False
    return True


def can_user_edit_todo_list(db: Session, user_id: str, todo_list_id: str) -> bool:
    try:
        _get_accessible_todo_list(db, user_id, todo_list_id, require_edit=True)
    except HTTPException:
        return False
    return True


def get_effective_todo_list_permissions(db: Session, user_id: str, todo_list_id: str) -> Dict[str, bool]:
    """Return non-share-type task capabilities for an accessible list."""
    todo_list, subscription = _get_accessible_todo_list(db, user_id, todo_list_id)
    normalized_user_id = _normalize_user_id(user_id)
    editable = (
        todo_list.user_id == normalized_user_id
        or _subscription_grants_edit(todo_list, subscription)
    )
    return {"can_delete": editable}


def get_editable_todo_list(db: Session, user_id: str, todo_list_id: str) -> "TodoLists":
    todo_list, _subscription = _get_accessible_todo_list(
        db,
        user_id,
        todo_list_id,
        require_edit=True,
    )
    return todo_list


def _get_accessible_todo(
    db: Session,
    user_id: str,
    todo_id: str,
    *,
    require_edit: bool = False,
) -> tuple["Todos", "TodoLists", Optional["SharedTodoListSubscription"]]:
    normalized_todo_id = _normalize_user_id(todo_id, "todo_id")
    todo = db.query(Todos).filter(Todos.id == normalized_todo_id).first()
    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    todo_list, subscription = _get_accessible_todo_list(
        db,
        user_id,
        todo.todo_list,
        require_edit=require_edit,
    )
    return todo, todo_list, subscription


def get_editable_todo(db: Session, user_id: str, todo_id: str) -> "Todos":
    todo, _todo_list, _subscription = _get_accessible_todo(
        db,
        user_id,
        todo_id,
        require_edit=True,
    )
    return todo


def get_accessible_todo(db: Session, user_id: str, todo_id: str) -> "Todos":
    """Return one todo when the user has read access to its current list."""
    todo, _todo_list, _subscription = _get_accessible_todo(
        db,
        user_id,
        todo_id,
    )
    return todo


def create_todo_list(
    db: Session,
    user_id: str,
    title: str,
    description: str | None,
    icon: str,
    sort_order: Optional[List[Dict[str, str]]] = None,
    order: int | None = None,
    *,
    before_commit: Callable[["TodoLists"], None] | None = None,
):
    """
    Create a todo list for the given user and persist it.
    """
    for field_name, value in (
        ("user_id", user_id),
        ("title", title),
        ("icon", icon),
    ):
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} is required",
            )
    normalized_user_id = user_id.strip()
    _ensure_user_todo_list_quota(db, normalized_user_id)
    description_value = _validate_text_length(
        description,
        "description",
        MAX_TODO_LIST_DESCRIPTION_LENGTH,
    ) or ""
    sort_order_value = _validate_sort_order_payload(sort_order)
    next_order = order if isinstance(order, int) else None
    if next_order is None:
        existing = (
            db.query(TodoLists.order)
            .filter(TodoLists.user_id == normalized_user_id)
            .order_by(TodoLists.order.desc())
            .first()
        )
        next_order = (existing[0] + 1) if existing else 0

    todo_list = TodoLists(
        id=str(uuid.uuid4()),
        user_id=normalized_user_id,
        order=next_order,
        title=title.strip(),
        description=description_value,
        icon=sanitize_icon_input(icon, fallback="checklist"),
        sort_order=sort_order_value if sort_order_value else deepcopy(DEFAULT_TODO_SORT_ORDER),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(todo_list)
    try:
        db.flush()
        if before_commit is not None:
            before_commit(todo_list)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(todo_list)
    return todo_list


def _apply_pagination(query, *, limit: int | None = None, offset: int = 0):
    if isinstance(offset, int) and offset > 0:
        query = query.offset(offset)
    if isinstance(limit, int) and limit > 0:
        query = query.limit(limit)
    return query


def list_todo_lists(db: Session, user_id: str, *, limit: int | None = None, offset: int = 0):
    """
    Return all todo lists that belong to the provided user ordered by creation time.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id is required",
        )
    normalized_user_id = user_id.strip()
    query = (
        db.query(TodoLists)
        .filter(TodoLists.user_id == normalized_user_id)
        .order_by(TodoLists.order.asc(), TodoLists.created_at.desc())
    )
    return _apply_pagination(query, limit=limit, offset=offset).all()


def delete_todo_lists(db: Session, user_id: str, todo_list_id: str):
    """
    Delete a todo list by id ensuring it belongs to the user.
    Also removes all subscriptions to this todo list.
    """
    for field_name, value in (("user_id", user_id), ("todo_list_id", todo_list_id)):
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} is required",
            )

    todo_list = _get_user_todo_list(db, user_id, todo_list_id)
    
    # Remove all subscriptions to this todo list
    db.query(SharedTodoListSubscription).filter(
        SharedTodoListSubscription.todo_list_id == todo_list_id
    ).delete(synchronize_session=False)
    
    # Delete associated todos before removing the list.
    (
        db.query(Todos)
        .filter(Todos.todo_list == todo_list.id)
        .delete(synchronize_session=False)
    )
    db.delete(todo_list)
    db.commit()
    return {"deleted": True, "todo_list_id": todo_list_id.strip()}


def update_todo_list(
    db: Session,
    user_id: str,
    todo_list_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    sort_order: Optional[List[Dict[str, str]]] = None,
    order: Optional[int] = None,
    *,
    before_commit: Callable[["TodoLists"], None] | None = None,
):
    """
    Update a todo list by id ensuring it belongs to the user.
    Only provided fields will be updated.
    """
    for field_name, value in (("user_id", user_id), ("todo_list_id", todo_list_id)):
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} is required",
            )

    todo_list = _get_user_todo_list(db, user_id, todo_list_id)

    if title is not None:
        if not isinstance(title, str) or not title.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="title cannot be empty",
            )
        todo_list.title = title.strip()

    if description is not None:
        todo_list.description = _validate_text_length(
            description,
            "description",
            MAX_TODO_LIST_DESCRIPTION_LENGTH,
        ) or ""

    if icon is not None:
        if not isinstance(icon, str) or not icon.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="icon cannot be empty",
            )
        todo_list.icon = sanitize_icon_input(icon, fallback="checklist")

    if sort_order is not None:
        todo_list.sort_order = _validate_sort_order_payload(sort_order) or deepcopy(DEFAULT_TODO_SORT_ORDER)

    if order is not None and isinstance(order, int):
        todo_list.order = order

    todo_list.updated_at = datetime.now(timezone.utc)
    try:
        db.flush()
        if before_commit is not None:
            before_commit(todo_list)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(todo_list)
    return todo_list


def create_todo(
    db: Session,
    user_id: str,
    todo_list_id: str,
    content: str,
    notes: str | None = None,
    priority: int = 0,
    due_at: datetime | None = None,
    all_day: bool = False,
    status_value: str | None = None,
    subtasks: Any = None,
    links: Any = None,
    attachments: Any = None,
    tags: Any = None,
    order: int | None = None,
    *,
    before_commit: Callable[["Todos"], None] | None = None,
):
    for field_name, value in (("user_id", user_id), ("todo_list_id", todo_list_id), ("content", content)):
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} is required",
            )
    if not isinstance(priority, int):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="priority must be an integer")
    if not isinstance(all_day, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="all_day must be a boolean")

    content_value = _validate_text_length(content, "content", MAX_TODO_CONTENT_LENGTH)
    if not content_value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content is required")
    notes_value = _validate_text_length(notes, "notes", MAX_TODO_NOTES_LENGTH)
    if notes_value == "":
        notes_value = None

    todo_list = get_editable_todo_list(db, user_id, todo_list_id)
    _ensure_todo_quota(db, todo_list.id)

    next_order = order if isinstance(order, int) else None
    if next_order is None:
        existing = (
            db.query(Todos.order)
            .filter(Todos.todo_list == todo_list.id)
            .order_by(Todos.order.desc())
            .first()
        )
        next_order = (existing[0] + 1) if existing else 0

    normalized_due_at = due_at
    if normalized_due_at and normalized_due_at.tzinfo is None:
        normalized_due_at = normalized_due_at.replace(tzinfo=timezone.utc)

    todo = Todos(
        id=str(uuid.uuid4()),
        todo_list=todo_list.id,
        order=next_order,
        content=content_value,
        notes=notes_value,
        priority=priority,
        due_at=normalized_due_at,
        all_day=all_day,
        status=_validate_todo_status(status_value),
        subtasks=_validate_json_payload(subtasks, "subtasks") or [],
        links=_validate_json_payload(links, "links") or [],
        attachments=_validate_json_payload(attachments, "attachments") or [],
        tags=_validate_string_list(tags, "tags"),
    )
    db.add(todo)
    try:
        db.flush()
        if before_commit is not None:
            before_commit(todo)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(todo)
    return todo


def update_todo(
    db: Session,
    user_id: str,
    todo_id: str,
    content: Optional[str] = None,
    notes: Optional[str] = None,
    priority: Optional[int] = None,
    due_at: Optional[datetime] = None,
    clear_due_at: bool = False,
    all_day: Optional[bool] = None,
    status_value: Optional[str] = None,
    subtasks: Any = None,
    links: Any = None,
    attachments: Any = None,
    tags: Any = None,
    order: Optional[int] = None,
    is_done: Optional[bool] = None,
    is_marked: Optional[bool] = None,
    *,
    before_commit: Callable[["Todos"], None] | None = None,
):
    """Update an editable todo and return the persisted row."""
    todo = get_editable_todo(db, user_id, todo_id)

    if content is not None:
        content_value = _validate_text_length(content, "content", MAX_TODO_CONTENT_LENGTH)
        if not content_value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content cannot be empty")
        todo.content = content_value
    if notes is not None:
        notes_value = _validate_text_length(notes, "notes", MAX_TODO_NOTES_LENGTH)
        todo.notes = notes_value or None
    if priority is not None:
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="priority must be an integer")
        todo.priority = priority
    if clear_due_at:
        todo.due_at = None
    elif due_at is not None:
        todo.due_at = due_at.replace(tzinfo=timezone.utc) if due_at.tzinfo is None else due_at
    if all_day is not None:
        if not isinstance(all_day, bool):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="all_day must be a boolean")
        todo.all_day = all_day
    if status_value is not None:
        normalized_status = _validate_todo_status(status_value)
        todo.status = normalized_status
        if normalized_status == TODO_STATUS_DONE:
            todo.is_done = True
            todo.completed_at = todo.completed_at or datetime.now(timezone.utc)
        elif todo.is_done:
            todo.is_done = False
            todo.completed_at = None
    if subtasks is not None:
        todo.subtasks = _validate_json_payload(subtasks, "subtasks") or []
    if links is not None:
        todo.links = _validate_json_payload(links, "links") or []
    if attachments is not None:
        todo.attachments = _validate_json_payload(attachments, "attachments") or []
    if tags is not None:
        todo.tags = _validate_string_list(tags, "tags")
    if order is not None:
        if isinstance(order, bool) or not isinstance(order, int):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="order must be an integer")
        todo.order = order
    if is_done is not None:
        if not isinstance(is_done, bool):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="is_done must be a boolean")
        _apply_done_status(todo, is_done)
    if is_marked is not None:
        if not isinstance(is_marked, bool):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="is_marked must be a boolean")
        todo.is_marked = is_marked

    if db.is_modified(todo, include_collections=True):
        todo.updated_at = datetime.now(timezone.utc)
        try:
            db.flush()
            if before_commit is not None:
                before_commit(todo)
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(todo)
    return todo


def set_todo_completion_state(
    db: Session,
    user_id: str,
    todo_id: str,
    is_done: bool,
):
    """
    Set the completion state of a todo to the desired boolean value.
    """
    if not isinstance(todo_id, str) or not todo_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="todo_id is required")
    if not isinstance(is_done, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="is_done must be a boolean")

    todo = get_editable_todo(db, user_id, todo_id)

    if todo.is_done != is_done:
        _apply_done_status(todo, is_done)
        todo.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(todo)
    return todo


def list_todos(
    db: Session,
    user_id: str,
    todo_list_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    query_text: str | None = None,
    view: str | None = None,
    priority_min: int | None = None,
    no_due_date: bool | None = None,
    status_value: str | None = None,
    sort_value: str | None = None,
):
    if not isinstance(todo_list_id, str) or not todo_list_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="todo_list_id is required")
    todo_list = get_accessible_todo_list(db, user_id, todo_list_id)
    query = (
        db.query(Todos)
        .filter(Todos.todo_list == todo_list.id)
    )

    query = _apply_todo_filters(
        query,
        query_text=query_text,
        view=view,
        priority_min=priority_min,
        no_due_date=no_due_date,
        status_value=status_value,
    )
    normalized_sort = str(sort_value or "manual").strip().lower()
    sort_columns = {
        "date-asc": (Todos.created_at.asc(),),
        "date-desc": (Todos.created_at.desc(),),
        "alpha-asc": (Todos.content.asc(),),
        "alpha-desc": (Todos.content.desc(),),
        "priority": (Todos.priority.desc(), Todos.order.asc()),
        "due-date": (Todos.due_at.is_(None), Todos.due_at.asc(), Todos.order.asc()),
        "manual": (Todos.order.asc(),),
    }.get(normalized_sort, (Todos.order.asc(),))
    query = query.order_by(Todos.is_done.asc(), *sort_columns, Todos.created_at.asc(), Todos.id.asc())
    return _apply_pagination(query, limit=limit, offset=offset).all()


def _apply_todo_filters(
    query,
    *,
    query_text: str | None = None,
    view: str | None = None,
    priority_min: int | None = None,
    no_due_date: bool | None = None,
    status_value: str | None = None,
):
    """Apply common search/view filters to a todo query."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)

    if query_text:
        like_value = f"%{query_text.strip()}%"
        query = query.filter(or_(Todos.content.ilike(like_value), Todos.notes.ilike(like_value)))
    if priority_min is not None:
        query = query.filter(Todos.priority >= priority_min)
    if no_due_date is True:
        query = query.filter(Todos.due_at.is_(None))
    if status_value:
        query = query.filter(Todos.status == _validate_todo_status(status_value))
    if view:
        normalized_view = view.strip().lower()
        if normalized_view == "today":
            query = query.filter(Todos.due_at >= today_start, Todos.due_at < tomorrow_start)
        elif normalized_view == "upcoming":
            query = query.filter(Todos.due_at >= tomorrow_start)
        elif normalized_view == "overdue":
            query = query.filter(Todos.is_done == False, Todos.due_at < now)
        elif normalized_view == "due_this_week":
            query = query.filter(Todos.due_at >= today_start, Todos.due_at < week_end)
        elif normalized_view == "high_priority":
            query = query.filter(Todos.priority >= 2)
        elif normalized_view == "no_due_date":
            query = query.filter(Todos.due_at.is_(None))
    return query


def search_todos(
    db: Session,
    user_id: str,
    *,
    query_text: str | None = None,
    view: str | None = None,
    priority_min: int | None = None,
    no_due_date: bool | None = None,
    status_value: str | None = None,
    limit: int | None = None,
    offset: int = 0,
):
    """Search across all owned and subscribed todo lists visible to a user."""
    normalized_user_id = _normalize_user_id(user_id)
    query = (
        db.query(Todos)
        .join(TodoLists, TodoLists.id == Todos.todo_list)
        .outerjoin(
            SharedTodoListSubscription,
            and_(
                SharedTodoListSubscription.todo_list_id == TodoLists.id,
                SharedTodoListSubscription.subscriber_id == normalized_user_id,
            ),
        )
        .filter(
            or_(
                TodoLists.user_id == normalized_user_id,
                and_(
                    SharedTodoListSubscription.share_type == ShareType.LIVE.value,
                    TodoLists.live_share_id.isnot(None),
                ),
                and_(
                    SharedTodoListSubscription.share_type == ShareType.COLLABORATE.value,
                    TodoLists.collaborate_share_id.isnot(None),
                ),
            ),
        )
    )
    query = _apply_todo_filters(
        query,
        query_text=query_text,
        view=view,
        priority_min=priority_min,
        no_due_date=no_due_date,
        status_value=status_value,
    )
    query = query.order_by(
        Todos.is_done.asc(),
        Todos.due_at.is_(None),
        Todos.due_at.asc(),
        Todos.updated_at.desc(),
        Todos.id.asc(),
    )
    return _apply_pagination(query, limit=limit, offset=offset).all()


def bulk_update_todos(
    db: Session,
    user_id: str,
    todo_ids: List[str],
    *,
    action: str,
    target_list_id: str | None = None,
    tags: List[str] | None = None,
    is_done: bool | None = None,
    before_commit: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, Any]:
    """Apply one bulk action to editable todos and report per-item errors."""
    if not isinstance(todo_ids, list) or not todo_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="todo_ids is required")

    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"complete", "incomplete", "move", "tag", "delete"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid bulk action")

    target_list = None
    if normalized_action == "move":
        if not target_list_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target_list_id is required")
        target_list = get_editable_todo_list(db, user_id, target_list_id)
        # Check the quota before any rows move so the bulk action stays all-or-nothing.
        current_target_memberships = [
            row[0]
            for row in db.query(Todos.todo_list).filter(Todos.id.in_(todo_ids)).all()
        ]
        incoming_count = sum(1 for todo_list_id in current_target_memberships if todo_list_id != target_list.id)
        count_current = db.query(Todos.id).filter(Todos.todo_list == target_list.id).count()
        if count_current + incoming_count > MAX_TODOS_PER_LIST:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target list would exceed max todos")

    normalized_tags = _validate_string_list(tags, "tags") if normalized_action == "tag" else []
    updated: List[str] = []
    errors: List[Dict[str, str]] = []

    for todo_id in todo_ids:
        try:
            todo = get_editable_todo(db, user_id, todo_id)
            if normalized_action == "delete":
                db.delete(todo)
            elif normalized_action in {"complete", "incomplete"}:
                done_value = is_done if is_done is not None else normalized_action == "complete"
                _apply_done_status(todo, bool(done_value))
                todo.updated_at = datetime.now(timezone.utc)
            elif normalized_action == "move" and target_list:
                todo.todo_list = target_list.id
                todo.updated_at = datetime.now(timezone.utc)
            elif normalized_action == "tag":
                existing_tags = set(_validate_string_list(todo.tags or [], "tags"))
                todo.tags = sorted(existing_tags.union(normalized_tags))
                todo.updated_at = datetime.now(timezone.utc)
            updated.append(str(todo_id))
        except HTTPException as exc:
            errors.append({"todo_id": str(todo_id), "error": str(exc.detail)})

    result = {"updated": updated, "errors": errors}
    try:
        db.flush()
        if before_commit is not None:
            before_commit(result)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def delete_todo(db: Session, user_id: str, todo_id: str):
    if not isinstance(todo_id, str) or not todo_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="todo_id is required")
    todo = get_editable_todo(db, user_id, todo_id)
    db.delete(todo)
    db.commit()
    return {"deleted": True, "todo_id": todo_id.strip()}


def toggle_todo(db: Session, user_id: str, todo_id: str):
    if not isinstance(todo_id, str) or not todo_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="todo_id is required")
    todo = get_editable_todo(db, user_id, todo_id)
    _apply_done_status(todo, not todo.is_done)
    todo.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(todo)
    return todo


def toggle_mark_todo(db: Session, user_id: str, todo_id: str):
    if not isinstance(todo_id, str) or not todo_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="todo_id is required")
    todo = get_editable_todo(db, user_id, todo_id)
    todo.is_marked = not todo.is_marked
    todo.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(todo)
    return todo


def list_marked_todos(db: Session, user_id: str, *, limit: int | None = None, offset: int = 0):
    normalized_user_id = _normalize_user_id(user_id)
    query = (
        db.query(Todos)
        .join(TodoLists, TodoLists.id == Todos.todo_list)
        .outerjoin(
            SharedTodoListSubscription,
            and_(
                SharedTodoListSubscription.todo_list_id == TodoLists.id,
                SharedTodoListSubscription.subscriber_id == normalized_user_id,
            ),
        )
        .filter(
            Todos.is_marked == True,
            or_(
                TodoLists.user_id == normalized_user_id,
                and_(
                    SharedTodoListSubscription.share_type == ShareType.LIVE.value,
                    TodoLists.live_share_id.isnot(None),
                ),
                and_(
                    SharedTodoListSubscription.share_type == ShareType.COLLABORATE.value,
                    TodoLists.collaborate_share_id.isnot(None),
                ),
            ),
        )
        .order_by(Todos.is_done.asc(), Todos.updated_at.desc(), Todos.id.asc())
    )
    return _apply_pagination(query, limit=limit, offset=offset).all()


current_todo_lists_export_version = 1.0


def _serialize_todo(todo):
    return {
        "id": todo.id,
        "content": todo.content,
        "notes": todo.notes,
        "priority": todo.priority,
        "due_at": _datetime_to_iso(todo.due_at),
        "all_day": todo.all_day,
        "status": todo.status,
        "subtasks": todo.subtasks or [],
        "links": todo.links or [],
        "attachments": todo.attachments or [],
        "tags": todo.tags or [],
        "is_done": todo.is_done,
        "is_marked": todo.is_marked,
        "completed_at": _datetime_to_iso(todo.completed_at),
        "order": todo.order,
        "created_at": _datetime_to_iso(todo.created_at),
        "updated_at": _datetime_to_iso(todo.updated_at),
    }


def export_user_todo_lists(db: Session, user_id: str):
    user_id_value = _normalize_user_id(user_id)
    todo_lists = list_todo_lists(db, user_id_value)
    todo_list_ids = [todo_list.id for todo_list in todo_lists]
    todos_by_list: Dict[str, List[Dict[str, Any]]] = {todo_list_id: [] for todo_list_id in todo_list_ids}

    if todo_list_ids:
        todos = (
            db.query(Todos)
            .filter(Todos.todo_list.in_(todo_list_ids))
            .order_by(Todos.todo_list.asc(), Todos.order.asc(), Todos.created_at.asc())
            .all()
        )
        for todo in todos:
            todos_by_list.setdefault(todo.todo_list, []).append(_serialize_todo(todo))

    export_data = []
    for todo_list in todo_lists:
        export_data.append(
            {
                "id": todo_list.id,
                "title": todo_list.title,
                "description": todo_list.description,
                "icon": todo_list.icon,
                "sort_order": todo_list.sort_order,
                "order": todo_list.order,
                "created_at": _datetime_to_iso(todo_list.created_at),
                "updated_at": _datetime_to_iso(todo_list.updated_at),
                "todos": todos_by_list.get(todo_list.id, []),
            }
        )

    return {
        "export_type": "todo_lists",
        "export_version": current_todo_lists_export_version,
        "data": {
            "user_id": user_id_value,
            "todo_lists": export_data,
        },
    }


def _create_todo_from_import_payload(db: Session, user_id: str, todo_list: "TodoLists", todo_payload: dict):
    _ensure_todo_quota(db, todo_list.id)
    content = todo_payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Todo content is required.")

    priority = todo_payload.get("priority", 0)
    if not isinstance(priority, int):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Todo priority must be an integer.")

    order_value = todo_payload.get("order")
    if not isinstance(order_value, int):
        existing = (
            db.query(Todos.order)
            .filter(Todos.todo_list == todo_list.id)
            .order_by(Todos.order.desc())
            .first()
        )
        order_value = (existing[0] + 1) if existing else 0

    try:
        due_at_value = _parse_iso_datetime(todo_payload.get("due_at"))
        completed_at_value = _parse_iso_datetime(todo_payload.get("completed_at"))
        created_at_value = _parse_iso_datetime(todo_payload.get("created_at")) or datetime.now(timezone.utc)
        updated_at_value = _parse_iso_datetime(todo_payload.get("updated_at")) or datetime.now(timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    content_value = _validate_text_length(content, "content", MAX_TODO_CONTENT_LENGTH)
    if not content_value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Todo content is required.")
    notes_value = _validate_text_length(todo_payload.get("notes"), "notes", MAX_TODO_NOTES_LENGTH)
    if notes_value == "":
        notes_value = None

    todo = Todos(
        id=str(uuid.uuid4()),
        todo_list=todo_list.id,
        order=order_value,
        content=content_value,
        notes=notes_value,
        priority=priority,
        due_at=due_at_value,
        all_day=bool(todo_payload.get("all_day", False)),
        status=_validate_todo_status(todo_payload.get("status"), is_done=bool(todo_payload.get("is_done", False))),
        subtasks=_validate_json_payload(todo_payload.get("subtasks"), "subtasks") or [],
        links=_validate_json_payload(todo_payload.get("links"), "links") or [],
        attachments=_validate_json_payload(todo_payload.get("attachments"), "attachments") or [],
        tags=_validate_string_list(todo_payload.get("tags"), "tags"),
        is_done=bool(todo_payload.get("is_done", False)),
        is_marked=bool(todo_payload.get("is_marked", False)),
        completed_at=completed_at_value,
        created_at=created_at_value,
        updated_at=updated_at_value,
    )
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return todo


def import_user_todo_lists(db: Session, user_id: str, payload: dict):
    user_id_value = _normalize_user_id(user_id)

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid import payload. Expected an object.",
        )

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")

    if export_type != "todo_lists":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export_type '{export_type}'.",
        )

    if export_version != current_todo_lists_export_version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported export_version '{export_version}'. "
                f"Expected '{current_todo_lists_export_version}'."
            ),
        )

    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export payload. Missing 'data' object.",
        )

    raw_todo_lists = data_block.get("todo_lists")
    if not isinstance(raw_todo_lists, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid export payload. 'todo_lists' must be a list.",
        )

    created = []
    errors = []

    for index, todo_list_entry in enumerate(raw_todo_lists):
        if not isinstance(todo_list_entry, dict):
            errors.append({"index": index, "error": "Todo list entry must be an object."})
            continue

        todos_payload = todo_list_entry.get("todos") or []
        if not isinstance(todos_payload, list):
            errors.append({"index": index, "error": "'todos' must be a list."})
            continue

        try:
            todo_list_obj = create_todo_list(
                db=db,
                user_id=user_id_value,
                title=todo_list_entry.get("title"),
                description=todo_list_entry.get("description"),
                icon=todo_list_entry.get("icon"),
                sort_order=todo_list_entry.get("sort_order"),
                order=todo_list_entry.get("order"),
            )
        except HTTPException as exc:
            errors.append({"index": index, "title": todo_list_entry.get("title"), "error": exc.detail})
            continue
        except Exception as exc:
            errors.append({"index": index, "title": todo_list_entry.get("title"), "error": str(exc)})
            continue

        todos_created = 0
        for todo_index, todo_payload in enumerate(todos_payload):
            if not isinstance(todo_payload, dict):
                errors.append(
                    {
                        "index": index,
                        "todo_index": todo_index,
                        "error": "Todo entry must be an object.",
                    }
                )
                continue
            try:
                _create_todo_from_import_payload(db, user_id_value, todo_list_obj, todo_payload)
                todos_created += 1
            except HTTPException as exc:
                errors.append(
                    {
                        "index": index,
                        "todo_index": todo_index,
                        "error": exc.detail,
                    }
                )
                continue
            except Exception as exc:
                errors.append(
                    {
                        "index": index,
                        "todo_index": todo_index,
                        "error": str(exc),
                    }
                )
                continue

        created.append(
            {
                "id": todo_list_obj.id,
                "title": todo_list_obj.title,
                "todos_created": todos_created,
            }
        )

    return {
        "created": created,
        "errors": errors,
    }


class Todos(Base):
    __tablename__ = "todos"
    __table_args__ = (
        Index('ix_todos_catalog_page', 'todo_list', 'is_done', 'order', 'created_at', 'id'),
        Index("ix_todos_list_order_created", "todo_list", "order", "created_at"),
        Index("ix_todos_marked_updated", "is_marked", "updated_at"),
    )

    id = Column(String, primary_key=True, index=True)
    todo_list = Column(String, ForeignKey("todo_lists.id"), nullable=False, index=True)
    order = Column(Integer, nullable=False, default=0)
    content = Column(String, nullable=False)
    notes = Column(String, nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    due_at = Column(DateTime(timezone=True), nullable=True)
    all_day = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False, default=TODO_STATUS_TODO)
    subtasks = Column(JSON, nullable=False, default=list)
    links = Column(JSON, nullable=False, default=list)
    attachments = Column(JSON, nullable=False, default=list)
    tags = Column(JSON, nullable=False, default=list)
    is_done = Column(Boolean, nullable=False, default=False)
    is_marked = Column(Boolean, nullable=False, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
        nullable=False,
    )


# ============================================================================
# Todo List Sharing Functions
# ============================================================================

def _get_owner_display_name(db: Session, user_id: str) -> str:
    """Get display name for a user."""
    from app.users.models import User
    try:
        owner = db.query(User).filter(User.id == user_id).first()
    except SQLAlchemyError:
        return "Unknown"
    if not owner:
        return "Unknown"
    if owner.first_name and owner.last_name:
        return f"{owner.first_name} {owner.last_name}"
    elif owner.first_name:
        return owner.first_name
    elif owner.email:
        return owner.email.split('@')[0]
    return "Unknown"


def _get_share_id_field(share_type: ShareType) -> str:
    """Get the column name for a share type."""
    return {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type, "live_share_id")


def _get_share_url_prefix(share_type: ShareType) -> str:
    """Get the URL prefix for a share type."""
    return {
        ShareType.CLONE: "/todos/clone",
        ShareType.LIVE: "/todos/live",
        ShareType.COLLABORATE: "/todos/collaborate",
    }.get(share_type, "/todos/live")


def create_todo_list_share(db: Session, user_id: str, todo_list_id: str, share_type: ShareType = ShareType.LIVE) -> dict:
    """Create or return existing share for a todo list with specified type."""
    todo_list = _get_user_todo_list(db, user_id, todo_list_id)
    
    # Get the appropriate share_id field
    share_id_attr = _get_share_id_field(share_type)
    existing_share_id = getattr(todo_list, share_id_attr, None)
    url_prefix = _get_share_url_prefix(share_type)
    base_url = get_public_url(db)
    
    if existing_share_id:
        return {
            "share_id": existing_share_id,
            "share_type": share_type.value,
            "share_url": f"{base_url}{url_prefix}/{existing_share_id}",
        }
    
    new_share_id = str(uuid.uuid4())
    setattr(todo_list, share_id_attr, new_share_id)
    todo_list.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {
        "share_id": new_share_id,
        "share_type": share_type.value,
        "share_url": f"{base_url}{url_prefix}/{new_share_id}",
    }


def get_todo_list_share_status(db: Session, user_id: str, todo_list_id: str) -> dict:
    """Get the current share status for all share types of a todo list."""
    todo_list = _get_user_todo_list(db, user_id, todo_list_id)
    
    # Count subscribers by type
    live_count = db.query(SharedTodoListSubscription).filter(
        SharedTodoListSubscription.todo_list_id == todo_list_id,
        SharedTodoListSubscription.share_type == "live"
    ).count()
    
    collaborate_count = db.query(SharedTodoListSubscription).filter(
        SharedTodoListSubscription.todo_list_id == todo_list_id,
        SharedTodoListSubscription.share_type == "collaborate"
    ).count()
    
    return {
        "clone_share_id": todo_list.clone_share_id,
        "live_share_id": todo_list.live_share_id,
        "collaborate_share_id": todo_list.collaborate_share_id,
        "live_subscriber_count": live_count,
        "collaborate_subscriber_count": collaborate_count,
    }


def delete_todo_list_share(db: Session, user_id: str, todo_list_id: str, share_type: Optional[ShareType] = None) -> dict:
    """Remove share info from a todo list. If share_type specified, only remove that type."""
    todo_list = _get_user_todo_list(db, user_id, todo_list_id)
    
    if share_type is None:
        # Delete all shares and subscriptions
        db.query(SharedTodoListSubscription).filter(
            SharedTodoListSubscription.todo_list_id == todo_list_id
        ).delete()
        todo_list.clone_share_id = None
        todo_list.live_share_id = None
        todo_list.collaborate_share_id = None
    else:
        # Delete only the specific share type
        share_id_attr = _get_share_id_field(share_type)
        setattr(todo_list, share_id_attr, None)
        
        if share_type in (ShareType.LIVE, ShareType.COLLABORATE):
            db.query(SharedTodoListSubscription).filter(
                SharedTodoListSubscription.todo_list_id == todo_list_id,
                SharedTodoListSubscription.share_type == share_type.value
            ).delete()
    
    todo_list.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {"ok": True, "share_type": share_type.value if share_type else "all"}


def get_shared_todo_list_by_share_id(db: Session, share_id: str, share_type: Optional[ShareType] = None) -> Optional["TodoLists"]:
    """Find a todo list by its share_id and optionally share_type."""
    if not share_id:
        return None
    
    cleaned_id = share_id.strip()
    
    if share_type == ShareType.CLONE:
        return db.query(TodoLists).filter(TodoLists.clone_share_id == cleaned_id).first()
    elif share_type == ShareType.LIVE:
        return db.query(TodoLists).filter(TodoLists.live_share_id == cleaned_id).first()
    elif share_type == ShareType.COLLABORATE:
        return db.query(TodoLists).filter(TodoLists.collaborate_share_id == cleaned_id).first()
    else:
        # Search all share types
        todo_list = db.query(TodoLists).filter(TodoLists.clone_share_id == cleaned_id).first()
        if todo_list:
            return todo_list
        todo_list = db.query(TodoLists).filter(TodoLists.live_share_id == cleaned_id).first()
        if todo_list:
            return todo_list
        todo_list = db.query(TodoLists).filter(TodoLists.collaborate_share_id == cleaned_id).first()
        if todo_list:
            return todo_list
        return None


def detect_share_type_from_id(db: Session, share_id: str) -> Optional[ShareType]:
    """Detect the share type from a share_id."""
    if not share_id:
        return None
    cleaned_id = share_id.strip()
    
    if db.query(TodoLists).filter(TodoLists.clone_share_id == cleaned_id).first():
        return ShareType.CLONE
    if db.query(TodoLists).filter(TodoLists.live_share_id == cleaned_id).first():
        return ShareType.LIVE
    if db.query(TodoLists).filter(TodoLists.collaborate_share_id == cleaned_id).first():
        return ShareType.COLLABORATE
    return None


def get_shared_todo_list_preview(
    db: Session,
    share_id: str,
    share_type: Optional[ShareType] = None,
    requesting_user_id: Optional[str] = None,
) -> dict:
    """Get a preview of a shared todo list (public endpoint)."""
    if not share_id:
        raise HTTPException(status_code=400, detail="share_id is required")
    
    todo_list = get_shared_todo_list_by_share_id(db, share_id, share_type)
    if not todo_list:
        raise HTTPException(status_code=404, detail="Shared todo list not found")

    if requesting_user_id and todo_list.user_id == requesting_user_id:
        raise HTTPException(status_code=400, detail="You cannot open your own shared todo list")
    
    # Detect actual share type
    detected_type = detect_share_type_from_id(db, share_id) or share_type or ShareType.LIVE
    
    # Get todos for preview
    todos = db.query(Todos).filter(Todos.todo_list == todo_list.id).order_by(Todos.created_at).limit(10).all()
    todos_preview = [{"content": t.content, "is_done": t.is_done} for t in todos]
    
    owner_name = _get_owner_display_name(db, todo_list.user_id)
    
    return {
        "share_id": share_id,
        "share_type": detected_type.value,
        "title": todo_list.title,
        "description": todo_list.description,
        "icon": todo_list.icon,
        "todo_count": len(todos),
        "todos": todos_preview,
        "owner_name": owner_name,
        "created_at": _datetime_to_iso(todo_list.created_at),
    }


def clone_shared_todo_list(db: Session, user_id: str, share_id: str) -> "TodoLists":
    """Clone a shared todo list for a user (creates a new independent copy)."""
    todo_list = get_shared_todo_list_by_share_id(db, share_id, ShareType.CLONE)
    if not todo_list:
        raise HTTPException(status_code=404, detail="Shared todo list not found or not available for cloning")
    
    if todo_list.user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot clone your own todo list")
    
    # Create a new todo list with the same content
    cloned_list = TodoLists(
        id=str(uuid.uuid4()),
        user_id=user_id,
        order=0,
        title=todo_list.title,
        description=todo_list.description,
        icon=todo_list.icon,
        sort_order=deepcopy(todo_list.sort_order) if todo_list.sort_order else deepcopy(DEFAULT_TODO_SORT_ORDER),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(cloned_list)
    db.flush()
    
    # Clone all todos from the original list
    original_todos = db.query(Todos).filter(Todos.todo_list == todo_list.id).all()
    for original_todo in original_todos:
        # Copy the richer todo metadata so cloned lists keep their full content model.
        cloned_todo = Todos(
            id=str(uuid.uuid4()),
            todo_list=cloned_list.id,
            order=original_todo.order,
            content=original_todo.content,
            notes=original_todo.notes,
            priority=original_todo.priority,
            due_at=original_todo.due_at,
            all_day=original_todo.all_day,
            status=_validate_todo_status(original_todo.status, is_done=original_todo.is_done),
            subtasks=deepcopy(original_todo.subtasks) if original_todo.subtasks else [],
            links=deepcopy(original_todo.links) if original_todo.links else [],
            attachments=deepcopy(original_todo.attachments) if original_todo.attachments else [],
            tags=deepcopy(original_todo.tags) if original_todo.tags else [],
            is_done=original_todo.is_done,
            is_marked=False,
            completed_at=original_todo.completed_at,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(cloned_todo)
    
    db.commit()
    db.refresh(cloned_list)
    return cloned_list


def subscribe_to_shared_todo_list(
    db: Session, 
    subscriber_id: str, 
    todo_list_id: str,
    share_type: ShareType = ShareType.LIVE,
) -> "SharedTodoListSubscription":
    """Subscribe a user to a shared todo list (live or collaborate)."""
    if share_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Clone shares don't support subscriptions")
    def _sync_subscription(existing_subscription: SharedTodoListSubscription) -> SharedTodoListSubscription:
        if existing_subscription.share_type != share_type.value:
            existing_subscription.share_type = share_type.value
            db.commit()
            db.refresh(existing_subscription)
        return existing_subscription
    
    existing = db.query(SharedTodoListSubscription).filter(
        SharedTodoListSubscription.todo_list_id == todo_list_id,
        SharedTodoListSubscription.subscriber_id == subscriber_id,
    ).first()
    
    if existing:
        return _sync_subscription(existing)
    
    subscription = SharedTodoListSubscription(
        id=str(uuid.uuid4()),
        todo_list_id=todo_list_id,
        subscriber_id=subscriber_id,
        share_type=share_type.value,
        subscribed_at=datetime.now(timezone.utc),
    )
    db.add(subscription)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        todo_list_exists = db.query(TodoLists.id).filter(TodoLists.id == todo_list_id).first()
        if todo_list_exists is None:
            raise HTTPException(status_code=404, detail="Shared todo list not found") from exc

        existing = db.query(SharedTodoListSubscription).filter(
            SharedTodoListSubscription.todo_list_id == todo_list_id,
            SharedTodoListSubscription.subscriber_id == subscriber_id,
        ).first()
        if existing:
            return _sync_subscription(existing)
        raise
    db.refresh(subscription)
    return subscription


def unsubscribe_from_shared_todo_list(db: Session, subscriber_id: str, todo_list_id: str) -> dict:
    """Unsubscribe a user from a shared todo list."""
    deleted = db.query(SharedTodoListSubscription).filter(
        SharedTodoListSubscription.todo_list_id == todo_list_id,
        SharedTodoListSubscription.subscriber_id == subscriber_id,
    ).delete()
    db.commit()
    return {"ok": True, "deleted": deleted > 0}


def get_subscribed_todo_lists(db: Session, user_id: str, *, limit: int | None = None, offset: int = 0) -> List[tuple]:
    """Get all todo lists that a user is subscribed to with subscription info."""
    normalized_user_id = _normalize_user_id(user_id)
    query = (
        db.query(TodoLists, SharedTodoListSubscription)
        .join(SharedTodoListSubscription, SharedTodoListSubscription.todo_list_id == TodoLists.id)
        .filter(SharedTodoListSubscription.subscriber_id == normalized_user_id)
        .filter(
            or_(
                and_(SharedTodoListSubscription.share_type == "live", TodoLists.live_share_id.isnot(None)),
                and_(SharedTodoListSubscription.share_type == "collaborate", TodoLists.collaborate_share_id.isnot(None)),
            )
        )
        .order_by(TodoLists.order.asc(), TodoLists.created_at.desc())
    )
    return _apply_pagination(query, limit=limit, offset=offset).all()


def get_todo_list_subscriber_count(db: Session, todo_list_id: str, share_type: Optional[str] = None) -> int:
    """Get the number of subscribers for a todo list."""
    query = db.query(SharedTodoListSubscription).filter(
        SharedTodoListSubscription.todo_list_id == todo_list_id
    )
    if share_type:
        query = query.filter(SharedTodoListSubscription.share_type == share_type)
    return query.count()
