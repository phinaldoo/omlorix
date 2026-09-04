"""Feature-owned compact history representation of tool results."""

from typing import Any
from app.tools.results import _copy_result_fields, _content_metadata


def _compact_note_item(note: Any, *, include_summary: bool = True) -> Any:
    if not isinstance(note, dict):
        return note
    fields = (
        "id",
        "note_id",
        "title",
        "snippet",
        "created_at",
        "updated_at",
        "is_subscribed",
        "share_type",
        "can_edit",
        "selection",
        "truncated",
        "content_length",
        "content_sha256",
        "edit_count",
    )
    compact = _copy_result_fields(note, fields)
    content = note.get("content")
    if include_summary and isinstance(content, str):
        compact.update(_content_metadata(content))
        if "title" not in compact:
            for line in content.splitlines():
                title = line.strip().lstrip("#").strip()
                if title:
                    compact["title"] = title[:80]
                    break
    return compact


def _compact_notes_result(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    compact = _copy_result_fields(
        payload,
        (
            "status",
            "operation",
            "count",
            "limit",
            "offset",
            "has_more",
            "next_cursor",
            "code",
            "error",
            "message",
        ),
    )
    if isinstance(payload.get("note"), dict):
        compact_note = _compact_note_item(payload["note"])
        compact["note"] = compact_note
    if isinstance(payload.get("notes"), list):
        compact_notes = []
        for note in payload["notes"][:100]:
            compact_note = _compact_note_item(note, include_summary=False)
            compact_notes.append(compact_note)
        compact["notes"] = compact_notes
    return compact or {"status": "completed"}


compact_result = _compact_notes_result
