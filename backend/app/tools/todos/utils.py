from typing import Any, Dict, List, Optional

from app.todos.models import (
    create_todo as db_create_todo,
    create_todo_list as db_create_todo_list,
    bulk_update_todos as db_bulk_update_todos,
    get_accessible_todo,
    get_accessible_todo_list,
    get_editable_todo,
    get_subscription_for_todo_list,
    get_subscribed_todo_lists,
    list_todos as db_list_todos,
    search_todos as db_search_todos,
    list_todo_lists as db_list_todo_lists,
    update_todo as db_update_todo,
    update_todo_list as db_update_todo_list,
)
from app.tools.audit import stage_tool_audit_action
from app.tools.text_edits import normalize_tool_text_query
from app.utils.helpers import datetime_to_iso, parse_datetime


TODO_TOOL_OPERATIONS = ("list", "view", "create", "edit", "bulk")
TODO_TOOL_BULK_ACTIONS = ("complete", "incomplete", "move", "tag")
DEFAULT_TODOS_TOOL_PAGE_LIMIT = 20
MAX_TODOS_TOOL_PAGE_LIMIT = 100
MAX_TODOS_TOOL_OFFSET = 10_000


def _serialize_todo_list(todo_list, *, summary: bool = False) -> Dict[str, Any]:
    description = str(todo_list.description or "")
    payload: Dict[str, Any] = {
        "id": todo_list.id,
        "title": todo_list.title,
        "description": description[:240] if summary else description,
        "icon": todo_list.icon,
        "order": todo_list.order,
        "created_at": datetime_to_iso(getattr(todo_list, "created_at", None)),
        "updated_at": datetime_to_iso(getattr(todo_list, "updated_at", None)),
    }
    if summary:
        payload["description_length"] = len(description)
    if not summary:
        payload["sort_order"] = todo_list.sort_order
    return payload


def _serialize_todo(todo) -> Dict[str, Any]:
    return {
        "id": todo.id,
        "todo_list": todo.todo_list,
        "content": todo.content,
        "notes": todo.notes,
        "priority": todo.priority,
        "due_at": datetime_to_iso(getattr(todo, "due_at", None)),
        "all_day": bool(getattr(todo, "all_day", False)),
        "status": getattr(todo, "status", "todo"),
        "subtasks": getattr(todo, "subtasks", None) or [],
        "links": getattr(todo, "links", None) or [],
        "attachments": getattr(todo, "attachments", None) or [],
        "tags": getattr(todo, "tags", None) or [],
        "is_done": bool(getattr(todo, "is_done", False)),
        "is_marked": bool(getattr(todo, "is_marked", False)),
        "completed_at": datetime_to_iso(getattr(todo, "completed_at", None)),
        "order": todo.order,
        "created_at": datetime_to_iso(getattr(todo, "created_at", None)),
        "updated_at": datetime_to_iso(getattr(todo, "updated_at", None)),
    }


def _serialize_todo_summary(todo) -> Dict[str, Any]:
    content = str(todo.content or "")
    return {
        "id": todo.id,
        "todo_list": todo.todo_list,
        "content": content[:500],
        "content_length": len(content),
        "priority": todo.priority,
        "due_at": datetime_to_iso(getattr(todo, "due_at", None)),
        "all_day": bool(getattr(todo, "all_day", False)),
        "status": getattr(todo, "status", "todo"),
        "is_done": bool(getattr(todo, "is_done", False)),
        "is_marked": bool(getattr(todo, "is_marked", False)),
        "has_notes": bool(str(getattr(todo, "notes", "") or "").strip()),
        "subtask_count": len(getattr(todo, "subtasks", None) or []),
        "link_count": len(getattr(todo, "links", None) or []),
        "attachment_count": len(getattr(todo, "attachments", None) or []),
        "updated_at": datetime_to_iso(getattr(todo, "updated_at", None)),
    }


def _normalize_page(limit: int | None, offset: int | None) -> tuple[int, int]:
    try:
        normalized_limit = int(
            DEFAULT_TODOS_TOOL_PAGE_LIMIT if limit is None else limit
        )
        normalized_offset = int(0 if offset is None else offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers.") from exc
    if normalized_limit < 1 or normalized_limit > MAX_TODOS_TOOL_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_TODOS_TOOL_PAGE_LIMIT}.")
    if normalized_offset < 0 or normalized_offset > MAX_TODOS_TOOL_OFFSET:
        raise ValueError(f"offset must be between 0 and {MAX_TODOS_TOOL_OFFSET}.")
    return normalized_limit, normalized_offset


def list_todo_lists_tool(
    db,
    user_id: str,
    *,
    limit: int | None = DEFAULT_TODOS_TOOL_PAGE_LIMIT,
    offset: int | None = 0,
    cursor: str | None = None,
) -> Dict[str, Any]:
    from app.todos.queries import list_todo_list_summaries
    page_limit, page_offset = _normalize_page(limit, offset)
    return list_todo_list_summaries(db, user_id, limit=page_limit, offset=page_offset, cursor=cursor)


def view_todo_list_tool(
    db,
    user_id: str,
    todo_list_id: str,
    *,
    limit: int | None = DEFAULT_TODOS_TOOL_PAGE_LIMIT,
    offset: int | None = 0,
    cursor: str | None = None,
) -> Dict[str, Any]:
    page_limit, page_offset = _normalize_page(limit, offset)
    normalized_user_id = str(user_id or "").strip()
    todo_list = get_accessible_todo_list(db, normalized_user_id, todo_list_id)
    subscription = None
    if todo_list.user_id != normalized_user_id:
        subscription = get_subscription_for_todo_list(db, normalized_user_id, todo_list.id)

    todo_list_payload = _serialize_todo_list(todo_list)
    todo_list_payload.update({
        "is_subscribed": subscription is not None,
        "share_type": subscription.share_type if subscription else None,
    })
    from app.todos.queries import list_todo_summaries
    result = list_todo_summaries(db, user_id, todo_list_id=todo_list_id, limit=page_limit, offset=page_offset, cursor=cursor)
    result["todo_list"] = todo_list_payload
    return result


def view_todo_tool(db, user_id: str, todo_id: str) -> Dict[str, Any]:
    todo_id_value = str(todo_id or "").strip()
    if not todo_id_value:
        raise ValueError("todo_id is required to view a todo")
    return {
        "operation": "view",
        "todo": _serialize_todo(get_accessible_todo(db, user_id, todo_id_value)),
    }


def search_todos_tool(
    db,
    user_id: str,
    query: Optional[str] = None,
    view: Optional[str] = None,
    priority_min: Optional[int] = None,
    no_due_date: Optional[bool] = None,
    status: Optional[str] = None,
    limit: int | None = DEFAULT_TODOS_TOOL_PAGE_LIMIT,
    offset: int | None = 0,
    cursor: str | None = None,
) -> Dict[str, Any]:
    page_limit, page_offset = _normalize_page(limit, offset)
    normalized_query = normalize_tool_text_query(query)
    from app.todos.queries import list_todo_summaries
    return list_todo_summaries(db, user_id, query_text=normalized_query, view=view, priority_min=priority_min, no_due_date=no_due_date, status_value=status, limit=page_limit, offset=page_offset, cursor=cursor)


def create_todo_tool(
    db,
    user_id: str,
    todo_list_id: str,
    content: str,
    notes: Optional[str] = None,
    priority: Optional[int] = None,
    due_at: Optional[Any] = None,
    all_day: Optional[bool] = None,
    status: Optional[str] = None,
    subtasks: Optional[List[Dict[str, Any]]] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[str]] = None,
    order: Optional[int] = None,
) -> Dict[str, Any]:
    due_at_dt = parse_datetime(due_at) if due_at is not None else None

    def stage_created(todo) -> None:
        stage_tool_audit_action(
            db,
            user_id,
            "TODO_CREATED",
            category="todo",
            details={"todo_id": todo.id, "todo_list_id": todo_list_id},
        )

    todo = db_create_todo(
        db=db,
        user_id=user_id,
        todo_list_id=todo_list_id,
        content=content,
        notes=notes,
        priority=priority if priority is not None else 0,
        due_at=due_at_dt,
        all_day=bool(all_day) if all_day is not None else False,
        status_value=status,
        subtasks=subtasks,
        links=links,
        attachments=attachments,
        tags=tags,
        order=order,
        before_commit=stage_created,
    )
    return _serialize_todo(todo)


def create_todo_list_tool(
    db,
    user_id: str,
    title: str,
    icon: Optional[str] = None,
    description: Optional[str] = None,
    sort_order: Optional[List[Dict[str, str]]] = None,
    order: Optional[int] = None,
) -> Dict[str, Any]:
    def stage_created(todo_list) -> None:
        stage_tool_audit_action(
            db,
            user_id,
            "TODO_LIST_CREATED",
            category="todo",
            details={"todo_list_id": todo_list.id},
        )

    todo_list = db_create_todo_list(
        db=db,
        user_id=user_id,
        title=title,
        description=description,
        icon=icon or "list",
        sort_order=sort_order,
        order=order,
        before_commit=stage_created,
    )
    return _serialize_todo_list(todo_list)


def edit_todo_tool(
    db,
    user_id: str,
    todo_id: str,
    content: Optional[str] = None,
    notes: Optional[str] = None,
    priority: Optional[int] = None,
    due_at: Optional[Any] = None,
    clear_due_at: bool = False,
    all_day: Optional[bool] = None,
    status: Optional[str] = None,
    subtasks: Optional[List[Dict[str, Any]]] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[str]] = None,
    order: Optional[int] = None,
    is_done: Optional[bool] = None,
    is_marked: Optional[bool] = None,
) -> Dict[str, Any]:
    existing_todo = get_editable_todo(db, user_id, todo_id)
    previous_payload = _serialize_todo(existing_todo)
    requested_fields = [
        field_name
        for field_name, field_value in (
            ("content", content),
            ("notes", notes),
            ("priority", priority),
            ("due_at", due_at),
            ("due_at", clear_due_at if clear_due_at else None),
            ("all_day", all_day),
            ("status", status),
            ("subtasks", subtasks),
            ("links", links),
            ("attachments", attachments),
            ("tags", tags),
            ("order", order),
        )
        if field_value is not None
    ]

    due_at_dt = parse_datetime(due_at) if due_at is not None else None

    def stage_updates(todo) -> None:
        updated_payload = _serialize_todo(todo)
        updated_fields = [
            field_name
            for field_name in dict.fromkeys(requested_fields)
            if previous_payload.get(field_name) != updated_payload.get(field_name)
        ]
        if updated_fields:
            stage_tool_audit_action(
                db,
                user_id,
                "TODO_UPDATED",
                category="todo",
                details={
                    "todo_id": todo_id,
                    "todo_list_id": todo.todo_list,
                    "updated_fields": updated_fields,
                },
            )
        if is_done is not None and previous_payload["is_done"] != updated_payload["is_done"]:
            stage_tool_audit_action(
                db,
                user_id,
                "TODO_TOGGLED",
                category="todo",
                details={"todo_id": todo_id, "todo_list_id": todo.todo_list, "is_done": todo.is_done},
            )
        if is_marked is not None and previous_payload["is_marked"] != updated_payload["is_marked"]:
            stage_tool_audit_action(
                db,
                user_id,
                "TODO_MARK_TOGGLED",
                category="todo",
                details={"todo_id": todo_id, "todo_list_id": todo.todo_list, "is_marked": todo.is_marked},
            )

    todo = db_update_todo(
        db=db,
        user_id=user_id,
        todo_id=todo_id,
        content=content,
        notes=notes,
        priority=priority,
        due_at=due_at_dt,
        clear_due_at=clear_due_at,
        all_day=all_day,
        status_value=status,
        subtasks=subtasks,
        links=links,
        attachments=attachments,
        tags=tags,
        order=order,
        is_done=is_done,
        is_marked=is_marked,
        before_commit=stage_updates,
    )
    return _serialize_todo(todo)


def bulk_todos_tool(
    db,
    user_id: str,
    todo_ids: List[str],
    action: str,
    target_list_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    is_done: Optional[bool] = None,
) -> Dict[str, Any]:
    """Apply a non-destructive bulk mutation requested through the LLM tool."""
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in TODO_TOOL_BULK_ACTIONS:
        allowed_actions = ", ".join(TODO_TOOL_BULK_ACTIONS)
        raise ValueError(f"action must be one of: {allowed_actions}")

    # Keep this allowlist at the tool boundary. The shared database helper also
    # supports deletion for the human-facing todo API, which LLMs must not reach.
    def stage_bulk_update(result: Dict[str, Any]) -> None:
        stage_tool_audit_action(
            db,
            user_id,
            "TODO_BULK_UPDATED",
            category="todo",
            details={"action": normalized_action, "updated_count": len(result.get("updated", []))},
        )

    result = db_bulk_update_todos(
        db=db,
        user_id=user_id,
        todo_ids=todo_ids,
        action=normalized_action,
        target_list_id=target_list_id,
        tags=tags,
        is_done=is_done,
        before_commit=stage_bulk_update,
    )
    return result


def edit_todo_list_tool(
    db,
    user_id: str,
    todo_list_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    sort_order: Optional[List[Dict[str, str]]] = None,
    order: Optional[int] = None,
) -> Dict[str, Any]:
    updated_fields = [
        field_name
        for field_name, field_value in (
            ("title", title),
            ("description", description),
            ("icon", icon),
            ("sort_order", sort_order),
            ("order", order),
        )
        if field_value is not None
    ]

    def stage_updated(todo_list) -> None:
        stage_tool_audit_action(
            db,
            user_id,
            "TODO_LIST_UPDATED",
            category="todo",
            details={"todo_list_id": todo_list.id, "updated_fields": updated_fields},
        )

    todo_list = db_update_todo_list(
        db=db,
        user_id=user_id,
        todo_list_id=todo_list_id,
        title=title,
        description=description,
        icon=icon,
        sort_order=sort_order,
        order=order,
        before_commit=stage_updated,
    )
    return _serialize_todo_list(todo_list)


def todos_tool(
    db,
    user_id: str,
    type: str,
    entity: Optional[str] = "todo",
    todo_list_id: Optional[str] = None,
    todo_id: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    sort_order: Optional[List[Dict[str, str]]] = None,
    content: Optional[str] = None,
    notes: Optional[str] = None,
    priority: Optional[int] = None,
    due_at: Optional[Any] = None,
    order: Optional[int] = None,
    is_done: Optional[bool] = None,
    is_marked: Optional[bool] = None,
    clear_due_at: bool = False,
    all_day: Optional[bool] = None,
    status: Optional[str] = None,
    subtasks: Optional[List[Dict[str, Any]]] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[str]] = None,
    query: Optional[str] = None,
    view: Optional[str] = None,
    priority_min: Optional[int] = None,
    no_due_date: Optional[bool] = None,
    todo_ids: Optional[List[str]] = None,
    action: Optional[str] = None,
    target_list_id: Optional[str] = None,
    limit: Optional[int] = DEFAULT_TODOS_TOOL_PAGE_LIMIT,
    offset: Optional[int] = 0,
    cursor: str | None = None,
) -> Dict[str, Any]:
    """Dispatch an LLM todo-tool request without exposing destructive actions."""
    operation = str(type or "").strip().lower()
    target = str(entity or "todo").strip().lower()
    if target not in {"todo", "list"}:
        raise ValueError("entity must be one of: todo, list")

    if operation == "list":
        if target == "list":
            return list_todo_lists_tool(
                db,
                user_id,
                limit=limit,
                offset=offset,
            cursor=cursor,
            )
        if query or view or priority_min is not None or no_due_date is not None or status:
            return search_todos_tool(
                db,
                user_id,
                query=query,
                view=view,
                priority_min=priority_min,
                no_due_date=no_due_date,
                status=status,
                limit=limit,
                offset=offset,
            cursor=cursor,
            )
        todo_list_id_value = str(todo_list_id or "").strip()
        if not todo_list_id_value:
            raise ValueError("todo_list_id is required to list todos")
        return view_todo_list_tool(
            db,
            user_id,
            todo_list_id_value,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )

    if operation == "view":
        if target == "list":
            todo_list_id_value = str(todo_list_id or "").strip()
            if not todo_list_id_value:
                raise ValueError("todo_list_id is required to view a todo list")
            normalized_user_id = str(user_id or "").strip()
            todo_list = get_accessible_todo_list(
                db,
                normalized_user_id,
                todo_list_id_value,
            )
            subscription = None
            if str(todo_list.user_id) != normalized_user_id:
                subscription = get_subscription_for_todo_list(
                    db,
                    normalized_user_id,
                    todo_list.id,
                )
            payload = _serialize_todo_list(todo_list)
            payload.update(
                {
                    "is_subscribed": subscription is not None,
                    "share_type": subscription.share_type if subscription else None,
                }
            )
            return {"operation": "view", "todo_list": payload}
        return view_todo_tool(db, user_id, str(todo_id or "").strip())

    if operation == "create":
        if target == "list":
            title_value = str(title or "").strip()
            if not title_value:
                raise ValueError("title is required to create a todo list")
            return {
                "operation": "create",
                "todo_list": create_todo_list_tool(
                    db=db,
                    user_id=user_id,
                    title=title_value,
                    icon=icon,
                    description=description,
                    sort_order=sort_order,
                    order=order,
                )
            }
        todo_list_id_value = str(todo_list_id or "").strip()
        content_value = str(content or "").strip()
        if not todo_list_id_value:
            raise ValueError("todo_list_id is required to create a todo")
        if not content_value:
            raise ValueError("content is required to create a todo")
        return {
            "operation": "create",
            "todo": create_todo_tool(
                db=db,
                user_id=user_id,
                todo_list_id=todo_list_id_value,
                content=content_value,
                notes=notes,
                priority=priority,
                due_at=due_at,
                all_day=all_day,
                status=status,
                subtasks=subtasks,
                links=links,
                attachments=attachments,
                tags=tags,
                order=order,
            )
        }

    if operation == "edit":
        if target == "list":
            todo_list_id_value = str(todo_list_id or "").strip()
            if not todo_list_id_value:
                raise ValueError("todo_list_id is required to edit a todo list")
            return {
                "operation": "edit",
                "todo_list": edit_todo_list_tool(
                    db=db,
                    user_id=user_id,
                    todo_list_id=todo_list_id_value,
                    title=title,
                    description=description,
                    icon=icon,
                    sort_order=sort_order,
                    order=order,
                )
            }
        todo_id_value = str(todo_id or "").strip()
        if not todo_id_value:
            raise ValueError("todo_id is required to edit a todo")
        return {
            "operation": "edit",
            "todo": edit_todo_tool(
                db=db,
                user_id=user_id,
                todo_id=todo_id_value,
                content=content,
                notes=notes,
                priority=priority,
                due_at=due_at,
                clear_due_at=clear_due_at,
                all_day=all_day,
                status=status,
                subtasks=subtasks,
                links=links,
                attachments=attachments,
                tags=tags,
                order=order,
                is_done=is_done,
                is_marked=is_marked,
            )
        }

    if operation == "bulk":
        todo_id_values = todo_ids or ([] if todo_id is None else [str(todo_id)])
        if not todo_id_values:
            raise ValueError("todo_ids is required for bulk actions")
        if not isinstance(todo_id_values, list):
            raise ValueError("todo_ids must be an array")
        if len(todo_id_values) > 100:
            raise ValueError("todo_ids may contain at most 100 entries")
        action_value = str(action or "").strip().lower()
        if not action_value:
            raise ValueError("action is required for bulk actions")
        result = bulk_todos_tool(
            db=db,
            user_id=user_id,
            todo_ids=todo_id_values,
            action=action_value,
            target_list_id=target_list_id,
            tags=tags,
            is_done=is_done,
        )
        result["operation"] = "bulk"
        return result

    allowed_operations = ", ".join(TODO_TOOL_OPERATIONS)
    raise ValueError(f"Invalid type. Allowed values are: {allowed_operations}.")
