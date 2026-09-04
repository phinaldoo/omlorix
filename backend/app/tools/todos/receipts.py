"""Feature-owned compact history representation of tool results."""

from typing import Any
from app.tools.results import _copy_result_fields, _content_metadata


def _compact_todo_result(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    compact = _copy_result_fields(
        payload,
        (
            "status",
            "operation",
            "message",
            "count",
            "updated",
            "failed",
            "limit",
            "offset",
            "has_more",
            "next_cursor",
            "code",
            "error",
        ),
    )
    operation = str(payload.get("operation") or "").strip().lower()
    todo = payload.get("todo")
    if isinstance(todo, dict):
        compact["todo"] = _copy_result_fields(
            todo,
            (
                "id",
                "todo_list",
                "content_length",
                "priority",
                "due_at",
                "all_day",
                "status",
                "is_done",
                "is_marked",
                "has_notes",
                "subtask_count",
                "link_count",
                "attachment_count",
                "updated_at",
            ),
        )
        content = todo.get("content")
        compact["todo"].update(_content_metadata(content))
        if operation == "list" and isinstance(content, str):
            compact["todo"]["content"] = content[:500]
    todo_list = payload.get("todo_list")
    if isinstance(todo_list, dict):
        compact["todo_list"] = _copy_result_fields(
            todo_list,
            (
                "id",
                "title",
                "description_length",
                "icon",
                "order",
                "updated_at",
                "is_subscribed",
                "share_type",
            ),
        )
        description = todo_list.get("description")
        if isinstance(description, str):
            compact["todo_list"]["description"] = description[:240]
            compact["todo_list"]["description_length"] = len(description)
    if isinstance(payload.get("todos"), list):
        compact_todos = []
        for item in payload["todos"][:100]:
            if not isinstance(item, dict):
                compact_todos.append(item)
                continue
            compact_item = _copy_result_fields(
                item,
                (
                    "id",
                    "todo_list",
                    "content_length",
                    "priority",
                    "due_at",
                    "all_day",
                    "status",
                    "is_done",
                    "is_marked",
                    "has_notes",
                    "subtask_count",
                    "link_count",
                    "attachment_count",
                    "updated_at",
                ),
            )
            item_content = item.get("content")
            if isinstance(item_content, str):
                compact_item["content"] = item_content[:500]
                compact_item["content_length"] = len(item_content)
            compact_todos.append(compact_item)
        compact["todos"] = compact_todos
    if isinstance(payload.get("todo_lists"), list):
        compact_lists = []
        for item in payload["todo_lists"][:100]:
            if not isinstance(item, dict):
                compact_lists.append(item)
                continue
            compact_item = _copy_result_fields(
                item,
                (
                    "id",
                    "title",
                    "description_length",
                    "icon",
                    "order",
                    "updated_at",
                    "is_subscribed",
                    "share_type",
                ),
            )
            description = item.get("description")
            if isinstance(description, str):
                compact_item["description"] = description[:240]
                compact_item["description_length"] = len(description)
            compact_lists.append(compact_item)
        compact["todo_lists"] = compact_lists
    return compact or {"status": "completed"}


compact_result = _compact_todo_result
