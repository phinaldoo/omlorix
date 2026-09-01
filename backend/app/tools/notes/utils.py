from typing import Any, Dict, List, Optional

from app.notes.models import (
    Notes,
    can_user_view_note,
    create_user_note as db_create_note,
    edit_user_note as db_edit_note,
    list_user_notes as db_list_notes,
    get_subscribed_notes as db_get_subscribed_notes,
)
from app.tools.audit import stage_tool_audit_action
from app.utils.helpers import datetime_to_iso


def _normalize_optional_snippet(snippet: str | None) -> str | None:
    """Treat omitted or blank snippet arguments as absent so full overwrites stay explicit."""
    if snippet is None:
        return None
    text = str(snippet)
    return text if text.strip() else None


def _find_exact_snippet(content: str, snippet: str, label: str) -> int:
    """Find exactly one snippet occurrence and reject ambiguous note edits."""
    if not snippet:
        raise ValueError(f"{label} is required for a snippet update.")
    first_index = content.find(snippet)
    if first_index < 0:
        raise ValueError(f"{label} was not found in the existing note.")
    if content.find(snippet, first_index + 1) >= 0:
        raise ValueError(f"{label} matched more than once. Provide a longer unique snippet.")
    return first_index


def _find_exact_snippet_after(content: str, snippet: str, label: str, start_index: int) -> int:
    """Find exactly one snippet occurrence after start_index."""
    if not snippet:
        raise ValueError(f"{label} is required for a snippet update.")
    first_index = content.find(snippet, start_index)
    if first_index < 0:
        raise ValueError(f"{label} was not found after start_snippet in the existing note.")
    if content.find(snippet, first_index + 1) >= 0:
        raise ValueError(f"{label} matched more than once after start_snippet. Provide a longer unique snippet.")
    return first_index


def _apply_snippet_update(
    existing_content: str,
    *,
    start_snippet: str | None,
    end_snippet: str | None,
    replacement_content: str,
) -> str:
    """Replace the inclusive range from start_snippet through end_snippet with replacement_content."""
    if start_snippet is None and end_snippet is None:
        return replacement_content
    if start_snippet is None or end_snippet is None:
        raise ValueError("Both start_snippet and end_snippet are required for a snippet update.")

    start_text = str(start_snippet)
    end_text = str(end_snippet)
    start_index = _find_exact_snippet(existing_content, start_text, "start_snippet")

    if start_text == end_text:
        end_index = start_index + len(end_text)
    else:
        end_start_index = _find_exact_snippet_after(
            existing_content,
            end_text,
            "end_snippet",
            start_index + len(start_text),
        )
        end_index = end_start_index + len(end_text)

    return f"{existing_content[:start_index]}{replacement_content}{existing_content[end_index:]}"


def _serialize_note(
    note,
    *,
    is_subscribed: bool = False,
    share_type: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": note.id,
        "user_id": note.user_id,
        "content": note.content,
        "clone_share_id": getattr(note, "clone_share_id", None),
        "live_share_id": getattr(note, "live_share_id", None),
        "collaborate_share_id": getattr(note, "collaborate_share_id", None),
        "created_at": datetime_to_iso(getattr(note, "created_at", None)),
        "updated_at": datetime_to_iso(getattr(note, "updated_at", None)),
        "is_subscribed": is_subscribed,
        "share_type": share_type,
    }


def list_notes_tool(db, user_id: str) -> List[Dict[str, Any]]:
    notes = [_serialize_note(note) for note in db_list_notes(db, user_id)]

    for note, subscription in db_get_subscribed_notes(db, user_id):
        notes.append(
            _serialize_note(
                note,
                is_subscribed=True,
                share_type=getattr(subscription, "share_type", None),
            )
        )

    notes.sort(
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    return notes


def create_note_tool(db, user_id: str, content: Optional[str] = "") -> Dict[str, Any]:
    note = db_create_note(
        db=db,
        user_id=user_id,
        content=content or "",
        before_commit=lambda saved_note: stage_tool_audit_action(
            db,
            user_id,
            "NOTE_CREATED",
            category="notes",
            details={"note_id": saved_note.id, "is_collaborator": False},
        ),
    )
    return _serialize_note(note)


def view_note_tool(db, user_id: str, note_id: str) -> Dict[str, Any]:
    note_id_value = str(note_id or "").strip()
    if not note_id_value:
        raise ValueError("note_id is required for view")
    if not can_user_view_note(db, user_id, note_id_value):
        raise ValueError("Note not found or not accessible.")
    note = db.query(Notes).filter(Notes.id == note_id_value).first()
    if note is None:
        raise ValueError("Note not found or not accessible.")
    subscription = None
    if str(getattr(note, "user_id", "") or "") != str(user_id):
        from app.notes.models import get_subscription_for_note

        subscription = get_subscription_for_note(db, user_id, note_id_value)
    return _serialize_note(
        note,
        is_subscribed=subscription is not None,
        share_type=getattr(subscription, "share_type", None) if subscription else None,
    )


def edit_note_tool(
    db,
    user_id: str,
    note_id: str,
    content: Optional[str] = "",
    *,
    start_snippet: Optional[str] = None,
    end_snippet: Optional[str] = None,
    expected_updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    if not str(expected_updated_at or "").strip():
        raise ValueError("expected_updated_at is required for edit. View the latest note first.")
    normalized_start = _normalize_optional_snippet(start_snippet)
    normalized_end = _normalize_optional_snippet(end_snippet)
    next_content = content or ""
    if normalized_start is not None or normalized_end is not None:
        existing_note = view_note_tool(db, user_id, note_id)
        next_content = _apply_snippet_update(
            str(existing_note.get("content") or ""),
            start_snippet=normalized_start,
            end_snippet=normalized_end,
            replacement_content=next_content,
        )
    note = db_edit_note(
        db=db,
        user_id=user_id,
        note_id=note_id,
        content=next_content,
        actor_type="assistant",
        # Bind both full replacements and targeted edits to the model's earlier
        # view. The internal snippet read is only for applying anchors and must
        # never silently advance a stale model-authored revision.
        expected_updated_at=expected_updated_at,
        before_commit=lambda saved_note: stage_tool_audit_action(
            db,
            user_id,
            "NOTE_UPDATED",
            category="notes",
            details={
                "note_id": saved_note.id,
                "is_collaborator": str(saved_note.user_id) != str(user_id),
            },
        ),
    )
    return _serialize_note(note)


def notes_tool(
    db,
    user_id: str,
    type: str,
    note_id: Optional[str] = None,
    content: Optional[str] = "",
    start_snippet: Optional[str] = None,
    end_snippet: Optional[str] = None,
    expected_updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    operation = str(type or "").strip().lower()
    if operation == "list":
        return {"notes": list_notes_tool(db, user_id)}
    if operation == "view":
        return {"note": view_note_tool(db, user_id, str(note_id or "").strip())}
    if operation == "create":
        return {"note": create_note_tool(db, user_id, content)}
    if operation == "edit":
        note_id_value = str(note_id or "").strip()
        if not note_id_value:
            raise ValueError("note_id is required for edit")
        return {
            "note": edit_note_tool(
                db,
                user_id,
                note_id_value,
                content,
                start_snippet=start_snippet,
                end_snippet=end_snippet,
                expected_updated_at=expected_updated_at,
            )
        }
    raise ValueError("Invalid type. Allowed values are: list, view, create, edit.")
