from datetime import timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.logging.models import create_audit_log, get_audit_request_ip
from app.todos.models import (
    ShareType,
    SharedTodoListSubscription,
    TodoLists,
    create_todo,
    create_todo_list,
    delete_todo,
    delete_todo_lists,
    list_todo_lists,
    list_todos,
    search_todos,
    list_marked_todos,
    toggle_todo,
    toggle_mark_todo,
    update_todo,
    update_todo_list,
    create_todo_list_share,
    get_todo_list_share_status,
    delete_todo_list_share,
    get_shared_todo_list_by_share_id,
    get_shared_todo_list_preview,
    subscribe_to_shared_todo_list,
    unsubscribe_from_shared_todo_list,
    get_subscribed_todo_lists,
    get_todo_list_subscriber_count,
    clone_shared_todo_list,
    detect_share_type_from_id,
)
from app.todos.schemas import (
    ShareTypeEnum,
    TodoCreate,
    TodoUpdate,
    TodoListCreate,
    TodoListUpdate,
    TodoListPageResponse,
    TodoListResponse,
    SubscribedTodoListResponse,
    TodoPageResponse,
    TodoResponse,
    MarkedTodoPageResponse,
    MarkedTodoResponse,
    ShareTodoListRequest,
    ShareTodoListResponse,
    TodoListShareStatusResponse,
    DeleteTodoListShareRequest,
    SharedTodoListPreviewResponse,
    AcceptSharedTodoListResponse,
    CloneTodoListResponse,
    InviteUsersRequest,
    InviteUsersResponse,
)
from app.users.models import get_user
from app.users.sharing import resolve_invitable_users_for_sharing
from app.userNotifications.models import create_user_notification
from app.utils.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MAX_PAGE_OFFSET,
    merged_window_limit,
    page_from_limited_items,
    page_from_merged_window,
)
from app.groups.init import get_user_group_setting_value


def ensure_todo_enabled(user, db: Session):
    is_enabled = get_user_group_setting_value(user.id, "todo", "enabled_todo", db)
    if not is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Todo feature disabled for your group")


def ensure_todo_sharing_allowed(user, db: Session):
    is_allowed = get_user_group_setting_value(user.id, "todo", "allow_todo_list_share", db)
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Todo list sharing is disabled for your group",
        )


def todo_list_has_existing_share_state(db: Session, todo_list_id: str, user_id: str, share_type: ShareType) -> bool:
    """Return true when an owned todo list already has share state for this type."""
    todo_list = db.query(TodoLists).filter(
        TodoLists.id == todo_list_id,
        TodoLists.user_id == user_id,
    ).first()
    if not todo_list:
        return False
    share_id_attr = {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type)
    if share_id_attr and getattr(todo_list, share_id_attr, None):
        return True
    return (
        db.query(SharedTodoListSubscription)
        .filter(
            SharedTodoListSubscription.todo_list_id == todo_list_id,
            SharedTodoListSubscription.share_type == share_type.value,
        )
        .count()
        > 0
    )


def ensure_todo_sharing_allowed_or_existing(user, db: Session, todo_list_id: str, share_type: ShareType):
    """Allow new sharing only when enabled, but preserve the same share type."""
    if not todo_list_has_existing_share_state(db, todo_list_id, user.id, share_type):
        ensure_todo_sharing_allowed(user, db)


def _get_user_display_name(user_obj):
    if not user_obj:
        return "Unknown"
    first = getattr(user_obj, "first_name", None)
    last = getattr(user_obj, "last_name", None)
    if first or last:
        return " ".join(filter(None, [first, last])).strip()
    if getattr(user_obj, "email", None):
        return user_obj.email
    return "Unknown"


todo_router = APIRouter(prefix="/api/v1/todo", tags=["todo"])


def _serialize_sort_order(payload_sort):
    if not payload_sort:
        return None
    return [item.model_dump() for item in payload_sort]


def _todo_list_owner_response(todo_list, *, subscriber_count: int | None = None) -> TodoListResponse:
    return TodoListResponse(
        id=todo_list.id,
        user_id=todo_list.user_id,
        title=todo_list.title,
        description=todo_list.description,
        icon=todo_list.icon,
        clone_share_id=todo_list.clone_share_id,
        live_share_id=todo_list.live_share_id,
        collaborate_share_id=todo_list.collaborate_share_id,
        sort_order=todo_list.sort_order,
        order=todo_list.order,
        created_at=todo_list.created_at,
        updated_at=todo_list.updated_at,
        subscriber_count=subscriber_count,
    )


def _todo_list_subscriber_response(todo_list, subscription, *, owner_name: str | None = None) -> SubscribedTodoListResponse:
    return SubscribedTodoListResponse(
        id=todo_list.id,
        title=todo_list.title,
        description=todo_list.description,
        icon=todo_list.icon,
        sort_order=todo_list.sort_order,
        order=todo_list.order,
        created_at=todo_list.created_at,
        updated_at=todo_list.updated_at,
        share_type=subscription.share_type,
        owner_name=owner_name,
    )


def _todo_list_item_permissions(db: Session, user_id: str, todo_list: TodoLists | None) -> dict:
    """Return canonical share metadata and task capabilities for a task row."""
    if not todo_list:
        return {
            "share_type": None,
            "can_delete": False,
            "is_subscribed": False,
        }
    if todo_list.user_id == user_id:
        return {
            "share_type": None,
            "can_delete": True,
            "is_subscribed": False,
        }
    subscription = (
        db.query(SharedTodoListSubscription)
        .filter(
            SharedTodoListSubscription.todo_list_id == todo_list.id,
            SharedTodoListSubscription.subscriber_id == user_id,
        )
        .first()
    )
    editable = bool(
        subscription
        and subscription.share_type == ShareType.COLLABORATE.value
        and todo_list.collaborate_share_id
    )
    return {
        "share_type": subscription.share_type if subscription else None,
        "can_delete": editable,
        "is_subscribed": bool(subscription),
    }


def _todo_response(
    todo,
    db: Session,
    user_id: str,
    permissions: dict | None = None,
) -> TodoResponse:
    """Serialize a task with capabilities from the backend permission policy."""
    response = TodoResponse.model_validate(todo)
    if permissions is None:
        todo_list = db.query(TodoLists).filter(TodoLists.id == todo.todo_list).first()
        effective_permissions = _todo_list_item_permissions(db, user_id, todo_list)
    else:
        effective_permissions = permissions
    return response.model_copy(update=effective_permissions)


@todo_router.get("/lists", response_model=TodoListPageResponse)
def list_todo_lists_route(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)

    fetch_limit = merged_window_limit(limit, offset)
    responses = []
    
    # Get user's own todo lists
    todo_lists = list_todo_lists(db, user.id, limit=fetch_limit)
    for tl in todo_lists:
        # Count subscribers for any active share
        has_share = tl.clone_share_id or tl.live_share_id or tl.collaborate_share_id
        subscriber_count = get_todo_list_subscriber_count(db, tl.id) if has_share else None
        responses.append(_todo_list_owner_response(tl, subscriber_count=subscriber_count))
    
    # Get subscribed todo lists from other users (returns tuples of (list, subscription))
    subscribed_data = get_subscribed_todo_lists(db, user.id, limit=fetch_limit)
    for tl, sub in subscribed_data:
        owner = get_user(db, tl.user_id)
        owner_name = _get_user_display_name(owner)
        responses.append(_todo_list_subscriber_response(tl, sub, owner_name=owner_name))
    
    items, has_more = page_from_merged_window(responses, limit=limit, offset=offset)
    return TodoListPageResponse(items=items, limit=limit, offset=offset, has_more=has_more)


@todo_router.post("/lists", response_model=TodoListResponse, status_code=status.HTTP_201_CREATED)
def create_todo_list_route(
    payload: TodoListCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    todo_list = create_todo_list(
        db=db,
        user_id=user.id,
        title=payload.title,
        description=payload.description,
        icon=payload.icon,
        sort_order=_serialize_sort_order(payload.sort_order),
        order=payload.order,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_CREATED",
        details={"todo_list_id": todo_list.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return todo_list


@todo_router.delete("/lists/{todo_list_id}", status_code=status.HTTP_200_OK)
def delete_todo_list_route(
    todo_list_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    result = delete_todo_lists(db, user.id, todo_list_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_DELETED",
        details={"todo_list_id": todo_list_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return result


@todo_router.patch("/lists/{todo_list_id}", response_model=TodoListResponse)
def update_todo_list_route(
    todo_list_id: str,
    payload: TodoListUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    todo_list = update_todo_list(
        db=db,
        user_id=user.id,
        todo_list_id=todo_list_id,
        title=payload.title,
        description=payload.description,
        icon=payload.icon,
        sort_order=_serialize_sort_order(payload.sort_order),
        order=payload.order,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_UPDATED",
        details={"todo_list_id": todo_list_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return todo_list


@todo_router.get("/lists/{todo_list_id}/todos", response_model=TodoPageResponse)
def list_todos_route(
    todo_list_id: str,
    q: Annotated[str | None, Query(max_length=200)] = None,
    view: Annotated[str | None, Query(max_length=32)] = None,
    priority_min: Annotated[int | None, Query(ge=0, le=10)] = None,
    no_due_date: bool | None = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=16)] = None,
    sort_value: Annotated[str | None, Query(alias="sort", max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    todos = list_todos(
        db,
        user.id,
        todo_list_id,
        limit=limit + 1,
        offset=offset,
        query_text=q,
        view=view,
        priority_min=priority_min,
        no_due_date=no_due_date,
        status_value=status_filter,
        sort_value=sort_value,
    )
    items, has_more = page_from_limited_items(todos, limit=limit)
    todo_list = db.query(TodoLists).filter(TodoLists.id == todo_list_id).first() if items else None
    permissions = _todo_list_item_permissions(db, user.id, todo_list) if items else None
    return TodoPageResponse(
        items=[_todo_response(todo, db, user.id, permissions) for todo in items],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@todo_router.post(
    "/lists/{todo_list_id}/todos",
    response_model=TodoResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_todo_route(
    todo_list_id: str,
    payload: TodoCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    due_at = payload.due_at
    if due_at and due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    todo = create_todo(
        db=db,
        user_id=user.id,
        todo_list_id=todo_list_id,
        content=payload.content,
        notes=payload.notes,
        priority=payload.priority,
        due_at=due_at,
        all_day=payload.all_day,
        status_value=payload.status,
        subtasks=payload.subtasks,
        links=payload.links,
        attachments=payload.attachments,
        tags=payload.tags,
        order=payload.order,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_CREATED",
        details={"todo_id": todo.id, "todo_list_id": todo_list_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return _todo_response(todo, db, user.id)


@todo_router.patch("/todos/{todo_id}", response_model=TodoResponse)
def update_todo_route(
    todo_id: str,
    payload: TodoUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    due_at = payload.due_at
    if due_at and due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    todo = update_todo(
        db=db,
        user_id=user.id,
        todo_id=todo_id,
        content=payload.content,
        notes=payload.notes,
        priority=payload.priority,
        due_at=due_at,
        clear_due_at=payload.clear_due_at,
        all_day=payload.all_day,
        status_value=payload.status,
        subtasks=payload.subtasks,
        links=payload.links,
        attachments=payload.attachments,
        tags=payload.tags,
        order=payload.order,
        is_done=payload.is_done,
        is_marked=payload.is_marked,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_UPDATED",
        details={"todo_id": todo_id, "todo_list_id": todo.todo_list},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return _todo_response(todo, db, user.id)


@todo_router.get("/todos/search", response_model=MarkedTodoPageResponse)
def search_todos_route(
    q: Annotated[str | None, Query(max_length=200)] = None,
    view: Annotated[str | None, Query(max_length=32)] = None,
    priority_min: Annotated[int | None, Query(ge=0, le=10)] = None,
    no_due_date: bool | None = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    todos = search_todos(
        db,
        user.id,
        query_text=q,
        view=view,
        priority_min=priority_min,
        no_due_date=no_due_date,
        status_value=status_filter,
        limit=limit + 1,
        offset=offset,
    )
    todos, has_more = page_from_limited_items(todos, limit=limit)
    result = []
    context_by_list = {}
    for todo in todos:
        context = context_by_list.get(todo.todo_list)
        if context is None:
            todo_list = db.query(TodoLists).filter(TodoLists.id == todo.todo_list).first()
            context = (todo_list, _todo_list_item_permissions(db, user.id, todo_list))
            context_by_list[todo.todo_list] = context
        todo_list, permissions = context
        result.append(MarkedTodoResponse(
            id=todo.id,
            todo_list=todo.todo_list,
            content=todo.content,
            notes=todo.notes,
            priority=todo.priority,
            due_at=todo.due_at,
            all_day=todo.all_day,
            status=todo.status,
            subtasks=todo.subtasks or [],
            links=todo.links or [],
            attachments=todo.attachments or [],
            tags=todo.tags or [],
            is_done=todo.is_done,
            is_marked=todo.is_marked,
            completed_at=todo.completed_at,
            order=todo.order,
            created_at=todo.created_at,
            updated_at=todo.updated_at,
            list_title=todo_list.title if todo_list else None,
            list_icon=todo_list.icon if todo_list else None,
            **permissions,
        ))
    return MarkedTodoPageResponse(items=result, limit=limit, offset=offset, has_more=has_more)


@todo_router.delete("/todos/{todo_id}", status_code=status.HTTP_200_OK)
def delete_todo_route(
    todo_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    result = delete_todo(db, user.id, todo_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_DELETED",
        details={"todo_id": todo_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return result


@todo_router.patch("/todos/{todo_id}/toggle", response_model=TodoResponse)
def toggle_todo_route(
    todo_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    todo = toggle_todo(db, user.id, todo_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_TOGGLED",
        details={"todo_id": todo_id, "is_done": todo.is_done},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return _todo_response(todo, db, user.id)


@todo_router.patch("/todos/{todo_id}/mark", response_model=TodoResponse)
def toggle_mark_todo_route(
    todo_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    todo = toggle_mark_todo(db, user.id, todo_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_MARK_TOGGLED",
        details={"todo_id": todo_id, "is_marked": todo.is_marked},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return _todo_response(todo, db, user.id)


@todo_router.get("/marked", response_model=MarkedTodoPageResponse)
def list_marked_todos_route(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_todo_enabled(user, db)
    from app.todos.models import TodoLists
    todos = list_marked_todos(db, user.id, limit=limit + 1, offset=offset)
    todos, has_more = page_from_limited_items(todos, limit=limit)
    result = []
    context_by_list = {}
    for todo in todos:
        context = context_by_list.get(todo.todo_list)
        if context is None:
            todo_list = db.query(TodoLists).filter(TodoLists.id == todo.todo_list).first()
            context = (todo_list, _todo_list_item_permissions(db, user.id, todo_list))
            context_by_list[todo.todo_list] = context
        todo_list, permissions = context
        result.append(MarkedTodoResponse(
            id=todo.id,
            todo_list=todo.todo_list,
            content=todo.content,
            notes=todo.notes,
            priority=todo.priority,
            due_at=todo.due_at,
            all_day=todo.all_day,
            status=todo.status,
            subtasks=todo.subtasks or [],
            links=todo.links or [],
            attachments=todo.attachments or [],
            tags=todo.tags or [],
            is_done=todo.is_done,
            is_marked=todo.is_marked,
            completed_at=todo.completed_at,
            order=todo.order,
            created_at=todo.created_at,
            updated_at=todo.updated_at,
            list_title=todo_list.title if todo_list else None,
            list_icon=todo_list.icon if todo_list else None,
            **permissions,
        ))
    return MarkedTodoPageResponse(items=result, limit=limit, offset=offset, has_more=has_more)


# ============================================================================
# Todo List Sharing Endpoints
# ============================================================================

def _map_share_type(schema_type: ShareTypeEnum) -> ShareType:
    """Map schema ShareTypeEnum to model ShareType."""
    return {
        ShareTypeEnum.CLONE: ShareType.CLONE,
        ShareTypeEnum.LIVE: ShareType.LIVE,
        ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
    }.get(schema_type, ShareType.LIVE)


@todo_router.post("/lists/share", response_model=ShareTodoListResponse)
def share_todo_list_route(
    payload: ShareTodoListRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Create or get existing share link for a todo list with specified type."""
    ensure_todo_enabled(user, db)
    share_type = _map_share_type(payload.share_type)
    ensure_todo_sharing_allowed_or_existing(user, db, payload.todo_list_id, share_type)
    result = create_todo_list_share(db, user.id, payload.todo_list_id, share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_SHARED",
        details={
            "todo_list_id": payload.todo_list_id,
            "share_id": result["share_id"],
            "share_type": result["share_type"],
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return ShareTodoListResponse(**result)


@todo_router.get("/lists/share/status", response_model=TodoListShareStatusResponse)
def get_todo_list_share_status_route(
    todo_list_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Get the current share status for all share types of a todo list."""
    ensure_todo_enabled(user, db)
    return get_todo_list_share_status(db, user.id, todo_list_id)


@todo_router.post("/lists/share/delete")
def delete_todo_list_share_route(
    payload: DeleteTodoListShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Remove sharing from a todo list. Optionally specify share_type to remove only that type."""
    ensure_todo_enabled(user, db)
    share_type = _map_share_type(payload.share_type) if payload.share_type else None
    result = delete_todo_list_share(db, user.id, payload.todo_list_id, share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_SHARE_DELETED",
        details={
            "todo_list_id": payload.todo_list_id,
            "share_type": payload.share_type.value if payload.share_type else "all",
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return result


@todo_router.get("/shared/{share_id}", response_model=SharedTodoListPreviewResponse)
def get_shared_todo_list_preview_route(
    share_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Authenticated endpoint to get a preview of a shared todo list."""
    ensure_todo_enabled(user, db)
    return get_shared_todo_list_preview(db, share_id, requesting_user_id=user.id)


@todo_router.post("/shared/{share_id}/accept", response_model=AcceptSharedTodoListResponse)
def accept_shared_todo_list_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Subscribe to a shared todo list (live or collaborate sharing)."""
    ensure_todo_enabled(user, db)
    
    # Detect share type from the share_id
    detected_type = detect_share_type_from_id(db, share_id)
    if detected_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Clone shares should use the /clone endpoint")
    
    shared_list = get_shared_todo_list_by_share_id(db, share_id)
    if not shared_list:
        raise HTTPException(status_code=404, detail="Shared todo list not found")
    
    if shared_list.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot subscribe to your own todo list")
    
    share_type = detected_type or ShareType.LIVE
    subscribe_to_shared_todo_list(db, user.id, shared_list.id, share_type)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_SUBSCRIBED",
        details={
            "share_id": share_id,
            "todo_list_id": shared_list.id,
            "owner_id": shared_list.user_id,
            "share_type": share_type.value,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    
    message = "Todo list added to your workspace"
    if share_type == ShareType.COLLABORATE:
        message += " (you can edit)"
    else:
        message += " (view only, live sync enabled)"
    
    return AcceptSharedTodoListResponse(
        todo_list_id=shared_list.id,
        title=shared_list.title,
        message=message,
    )


@todo_router.post("/clone/{share_id}", response_model=CloneTodoListResponse)
def clone_todo_list_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Clone a shared todo list to create a new independent copy."""
    ensure_todo_enabled(user, db)
    
    cloned_list = clone_shared_todo_list(db, user.id, share_id)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_CLONED",
        details={"share_id": share_id, "cloned_list_id": cloned_list.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    
    return CloneTodoListResponse(
        todo_list_id=cloned_list.id,
        title=cloned_list.title,
        message="Todo list cloned successfully! You now have your own copy.",
    )


@todo_router.post("/shared/{todo_list_id}/unsubscribe")
def unsubscribe_todo_list_route(
    todo_list_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Unsubscribe from a shared todo list."""
    ensure_todo_enabled(user, db)
    result = unsubscribe_from_shared_todo_list(db, user.id, todo_list_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_UNSUBSCRIBED",
        details={"todo_list_id": todo_list_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    return result


@todo_router.post("/lists/invite", response_model=InviteUsersResponse)
def invite_users_to_todo_list(
    payload: InviteUsersRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Invite users to a shared todo list by creating notifications."""
    ensure_todo_enabled(user, db)
    
    todo_list = db.query(TodoLists).filter(
        TodoLists.id == payload.item_id,
        TodoLists.user_id == user.id
    ).first()
    
    if not todo_list:
        raise HTTPException(status_code=404, detail="Todo list not found")
    
    # Create or get share for this type
    share_type_map = {
        ShareTypeEnum.CLONE: ShareType.CLONE,
        ShareTypeEnum.LIVE: ShareType.LIVE,
        ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
    }
    model_share_type = share_type_map.get(payload.share_type, ShareType.LIVE)
    ensure_todo_sharing_allowed_or_existing(user, db, payload.item_id, model_share_type)
    share_result = create_todo_list_share(db, user.id, payload.item_id, model_share_type)
    
    # Get inviter's display name
    inviter = get_user(db, user.id)
    inviter_name = ""
    if inviter.first_name and inviter.last_name:
        inviter_name = f"{inviter.first_name} {inviter.last_name}"
    elif inviter.first_name:
        inviter_name = inviter.first_name
    else:
        inviter_name = inviter.email.split('@')[0] if inviter.email else "Someone"
    
    invited_users = resolve_invitable_users_for_sharing(db, user, payload.user_ids)
    invited_count = 0
    for invited_user in invited_users:
        try:
            create_user_notification(
                db,
                message=f"{inviter_name} invited you to a todo list: {todo_list.title}",
                category="share_invitation",
                notification_type="info",
                user_ids=[invited_user.id],
                details={
                    "type": "share_invitation",
                    "item_type": "todo_list",
                    "item_id": payload.item_id,
                    "item_title": todo_list.title,
                    "share_id": share_result["share_id"],
                    "share_type": payload.share_type.value,
                    "inviter_id": user.id,
                    "inviter_name": inviter_name,
                },
            )
            invited_count += 1
        except Exception:
            pass
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="TODO_LIST_USERS_INVITED",
        details={
            "todo_list_id": payload.item_id,
            "invited_user_ids": [invited_user.id for invited_user in invited_users],
            "share_type": payload.share_type.value,
            "invited_count": invited_count,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="todo",
    )
    
    return InviteUsersResponse(
        invited_count=invited_count,
        message=f"Successfully invited {invited_count} user(s) to the todo list.",
    )
