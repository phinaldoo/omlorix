"""Task catalog projections with live share checks and bounded SQL pages."""

from datetime import datetime, timezone
from sqlalchemy import Integer, cast, func, case, select
from app.todos.models import (
    TodoLists as L,
    Todos as T,
    SharedTodoListSubscription as S,
    _apply_todo_filters,
    get_accessible_todo_list,
)
from app.utils.helpers import datetime_to_iso
from app.utils.read_models import json_array_size, keyset_page, shared_access


def list_access(user_id):
    return shared_access(L, S, S.todo_list_id, user_id)


def _dates(items, fields):
    for item in items:
        for field in fields:
            item[field] = datetime_to_iso(item[field])


def list_todo_list_summaries(
    db, user_id, *, limit=20, offset=0, cursor=None, management=False
):
    access, share = list_access(user_id)
    extra = []
    if management:
        extra = [
            L.sort_order,
            *(
                case((L.user_id == user_id, getattr(L, field)), else_=None).label(field)
                for field in ("clone_share_id", "live_share_id", "collaborate_share_id")
            ),
        ]
        extra.append(
            select(func.count(S.id))
            .where(S.todo_list_id == L.id)
            .correlate(L)
            .scalar_subquery()
            .label("subscriber_count")
        )
    rows = db.query(
        L.id,
        L.user_id,
        L.title,
        L.icon,
        L.order,
        L.created_at,
        L.updated_at,
        share,
        (L.description if management else func.substr(L.description, 1, 240)).label(
            "description"
        ),
        func.length(L.description).label("description_length"),
        *extra,
    ).filter(access)
    items, page = keyset_page(
        rows,
        order=[(L.order, "order", False), (L.id, "id", False)],
        scope=["todo_lists", user_id],
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    _dates(items, ("created_at", "updated_at"))
    owners = {}
    if management:
        from app.users.models import User

        owner_ids = {item["user_id"] for item in items if item["user_id"] != user_id}
        if owner_ids:
            owners = {
                row.id: " ".join(
                    part for part in (row.first_name, row.last_name) if part
                )
                or row.email
                for row in db.query(
                    User.id, User.first_name, User.last_name, User.email
                )
                .filter(User.id.in_(owner_ids))
                .all()
            }
    for item in items:
        owner_id = item.pop("user_id")
        item["is_subscribed"] = owner_id != user_id
        if management:
            item["user_id"] = owner_id if not item["is_subscribed"] else None
            item["owner_name"] = owners.get(owner_id)
    return {"operation": "list", "todo_lists": items, **page}


def list_todo_summaries(
    db, user_id, *, todo_list_id=None, limit=20, offset=0, cursor=None, **filters
):
    access, _ = list_access(user_id)
    done_order = cast(T.is_done, Integer)
    due_order = func.coalesce(T.due_at, datetime(9999, 1, 1, tzinfo=timezone.utc))
    rows = (
        db.query(
            T.id,
            T.todo_list,
            T.priority,
            T.due_at,
            T.all_day,
            T.status,
            T.is_done,
            T.is_marked,
            T.updated_at,
            T.created_at,
            T.order,
            done_order.label("_done_order"),
            due_order.label("_due_order"),
            func.substr(T.content, 1, 500).label("content"),
            func.length(T.content).label("content_length"),
            (func.length(func.trim(func.coalesce(T.notes, ""))) > 0).label("has_notes"),
            *(
                func.coalesce(json_array_size(getattr(T, field)), 0).label(name)
                for field, name in (
                    ("subtasks", "subtask_count"),
                    ("links", "link_count"),
                    ("attachments", "attachment_count"),
                )
            ),
        )
        .join(L, L.id == T.todo_list)
        .filter(access)
    )
    if todo_list_id:
        get_accessible_todo_list(db, user_id, todo_list_id)
        rows = rows.filter(T.todo_list == todo_list_id)
    rows = _apply_todo_filters(rows, **filters)
    order = [(done_order, "_done_order", False)]
    order += (
        [(T.order, "order", False), (T.created_at, "created_at", False)]
        if todo_list_id
        else [(due_order, "_due_order", False), (T.updated_at, "updated_at", True)]
    )
    order.append((T.id, "id", False))
    items, page = keyset_page(
        rows,
        order=order,
        scope=["todos", user_id, todo_list_id, filters],
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    _dates(items, ("due_at", "updated_at", "created_at"))
    for item in items:
        item.pop("_done_order")
        item.pop("_due_order")
    return {"operation": "list", "todos": items, **page}
