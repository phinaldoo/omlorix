from typing import Any, Dict, List, Optional

from app.notes.models import (
    Notes,
    SharedNoteSubscription,
    create_user_note as db_create_note,
    edit_user_note as db_edit_note,
    list_user_notes as db_list_notes,
    get_subscribed_notes as db_get_subscribed_notes,
)
from app.tools.audit import stage_tool_audit_action
from app.tools.text_edits import (
    DEFAULT_TOOL_TEXT_READ_CHARS,
    MAX_TOOL_TEXT_READ_CHARS,
    apply_atomic_text_edits,
    apply_single_text_edit,
    normalize_tool_text_query,
    select_text_content,
)
from app.utils.helpers import datetime_to_iso


DEFAULT_NOTES_TOOL_PAGE_LIMIT = 20
MAX_NOTES_TOOL_PAGE_LIMIT = 100
MAX_NOTES_TOOL_OFFSET = 10_000
MAX_NOTES_TOOL_BATCH_READ = 20
MAX_NOTES_TOOL_BATCH_TOTAL_CHARS = 120_000


def _normalize_optional_snippet(snippet: str | None) -> str | None:
    """Treat omitted or blank snippet arguments as absent so full overwrites stay explicit."""
    if snippet is None:
        return None
    text = str(snippet)
    return text if text.strip() else None


def _apply_snippet_update(
    existing_content: str,
    *,
    start_snippet: str | None,
    end_snippet: str | None,
    replacement_content: str,
) -> str:
    """Replace the inclusive range from start_snippet through end_snippet with replacement_content."""
    return apply_single_text_edit(
        existing_content,
        start_snippet=start_snippet,
        end_snippet=end_snippet,
        replacement_content=replacement_content,
        artifact_label="note",
    )


def _serialize_note(
    note,
    *,
    is_subscribed: bool = False,
    share_type: Optional[str] = None,
) -> Dict[str, Any]:
    can_edit = not is_subscribed or str(share_type or "") == "collaborate"
    return {
        "id": note.id,
        "content": note.content,
        "created_at": datetime_to_iso(getattr(note, "created_at", None)),
        "updated_at": datetime_to_iso(getattr(note, "updated_at", None)),
        "is_subscribed": is_subscribed,
        "share_type": share_type,
        "can_edit": can_edit,
    }


def _note_title(content: str | None) -> str:
    for line in str(content or "").splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned[:80]
    return "Untitled note"


def _note_snippet(content: str | None) -> str:
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    return " ".join(lines[1:])[:240]


def _serialize_note_summary(
    note,
    *,
    is_subscribed: bool = False,
    share_type: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": note.id,
        "title": _note_title(note.content),
        "snippet": _note_snippet(note.content),
        "content_length": len(str(note.content or "")),
        "created_at": datetime_to_iso(getattr(note, "created_at", None)),
        "updated_at": datetime_to_iso(getattr(note, "updated_at", None)),
        "is_subscribed": is_subscribed,
        "share_type": share_type,
        "can_edit": not is_subscribed or str(share_type or "") == "collaborate",
    }


def _normalize_page(limit: int | None, offset: int | None) -> tuple[int, int]:
    try:
        normalized_limit = int(
            DEFAULT_NOTES_TOOL_PAGE_LIMIT if limit is None else limit
        )
        normalized_offset = int(0 if offset is None else offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers.") from exc
    if normalized_limit < 1 or normalized_limit > MAX_NOTES_TOOL_PAGE_LIMIT:
        raise ValueError(
            f"limit must be between 1 and {MAX_NOTES_TOOL_PAGE_LIMIT}."
        )
    if normalized_offset < 0 or normalized_offset > MAX_NOTES_TOOL_OFFSET:
        raise ValueError(
            f"offset must be between 0 and {MAX_NOTES_TOOL_OFFSET}."
        )
    return normalized_limit, normalized_offset


def list_notes_tool(
    db,
    user_id: str,
    *,
    query: str | None = None,
    limit: int | None = DEFAULT_NOTES_TOOL_PAGE_LIMIT,
    offset: int | None = 0,
    cursor: str | None = None,
) -> Dict[str, Any]:
    from app.notes.queries import list_note_summaries
    page_limit, page_offset = _normalize_page(limit, offset)
    return list_note_summaries(db, user_id, query=normalize_tool_text_query(query), limit=page_limit, offset=page_offset, cursor=cursor)


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


def _load_accessible_note(db, user_id: str, note_id: str):
    note_id_value = str(note_id or "").strip()
    if not note_id_value:
        raise ValueError("note_id is required for view")
    from app.notes.queries import note_access
    access, _ = note_access(user_id)
    note = db.query(Notes).filter(Notes.id == note_id_value, access).first()
    if note is None:
        raise ValueError("Note not found or not accessible.")
    subscription = None
    if str(getattr(note, "user_id", "") or "") != str(user_id):
        subscription = (
            db.query(SharedNoteSubscription)
            .filter(
                SharedNoteSubscription.note_id == note_id_value,
                SharedNoteSubscription.subscriber_id == str(user_id),
            )
            .first()
        )
        share_type = str(getattr(subscription, "share_type", "") or "")
        share_is_active = (
            (share_type == "live" and bool(note.live_share_id))
            or (share_type == "collaborate" and bool(note.collaborate_share_id))
        )
        if not share_is_active:
            raise ValueError("Note not found or not accessible.")
    return note, subscription


def view_note_tool(
    db,
    user_id: str,
    note_id: str,
    *,
    heading: str | None = None,
    query: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int | None = DEFAULT_TOOL_TEXT_READ_CHARS,
) -> Dict[str, Any]:
    note, subscription = _load_accessible_note(db, user_id, note_id)
    payload = _serialize_note(
        note,
        is_subscribed=subscription is not None,
        share_type=getattr(subscription, "share_type", None) if subscription else None,
    )
    selected, selection = select_text_content(
        str(note.content or ""),
        heading=heading,
        query=query,
        start_line=start_line,
        end_line=end_line,
        max_chars=max_chars,
    )
    payload["content"] = selected
    payload["selection"] = selection
    payload["truncated"] = bool(selection.get("truncated"))
    return payload


def view_many_notes_tool(
    db,
    user_id: str,
    note_ids: list[str],
    *,
    heading: str | None = None,
    query: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int | None = DEFAULT_TOOL_TEXT_READ_CHARS,
) -> Dict[str, Any]:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for item in note_ids or []:
        note_id = str(item or "").strip()
        if note_id and note_id not in seen:
            seen.add(note_id)
            normalized_ids.append(note_id)
    if not normalized_ids:
        raise ValueError("note_ids must contain at least one note ID.")
    if len(normalized_ids) > MAX_NOTES_TOOL_BATCH_READ:
        raise ValueError(
            f"note_ids may contain at most {MAX_NOTES_TOOL_BATCH_READ} entries."
        )

    notes = db.query(Notes).filter(Notes.id.in_(normalized_ids)).all()
    notes_by_id = {str(note.id): note for note in notes}
    subscriptions = (
        db.query(SharedNoteSubscription)
        .filter(
            SharedNoteSubscription.note_id.in_(normalized_ids),
            SharedNoteSubscription.subscriber_id == str(user_id),
        )
        .all()
    )
    subscriptions_by_note = {
        str(subscription.note_id): subscription for subscription in subscriptions
    }

    try:
        requested_max_chars = int(
            max_chars
            if max_chars is not None
            else DEFAULT_TOOL_TEXT_READ_CHARS
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("max_chars must be an integer.") from exc
    if requested_max_chars < 1 or requested_max_chars > MAX_TOOL_TEXT_READ_CHARS:
        raise ValueError(
            f"max_chars must be between 1 and {MAX_TOOL_TEXT_READ_CHARS}."
        )

    accessible_notes: list[tuple[Any, Any, bool, str]] = []
    for note_id in normalized_ids:
        note = notes_by_id.get(note_id)
        if note is None:
            continue
        subscription = subscriptions_by_note.get(note_id)
        is_owner = str(note.user_id) == str(user_id)
        share_type = str(getattr(subscription, "share_type", "") or "")
        share_is_active = (
            (share_type == "live" and bool(note.live_share_id))
            or (share_type == "collaborate" and bool(note.collaborate_share_id))
        )
        if not is_owner and not share_is_active:
            continue
        accessible_notes.append((note, subscription, is_owner, share_type))

    per_note_budget = min(
        requested_max_chars,
        max(
            1,
            MAX_NOTES_TOOL_BATCH_TOTAL_CHARS // max(1, len(accessible_notes)),
        ),
    )
    payloads: list[dict[str, Any]] = []
    total_returned_chars = 0
    unmatched_count = 0
    for note, _subscription, is_owner, share_type in accessible_notes:
        payload = _serialize_note(
            note,
            is_subscribed=not is_owner,
            share_type=share_type or None,
        )
        try:
            selected, selection = select_text_content(
                str(note.content or ""),
                heading=heading,
                query=query,
                start_line=start_line,
                end_line=end_line,
                max_chars=per_note_budget,
            )
        except ValueError as exc:
            if str(exc) in {
                "heading was not found in the document.",
                "query was not found in the document.",
            }:
                unmatched_count += 1
                continue
            raise
        payload["content"] = selected
        payload["selection"] = selection
        payload["truncated"] = bool(selection.get("truncated"))
        total_returned_chars += len(selected)
        payloads.append(payload)
    return {
        "operation": "view_many",
        "notes": payloads,
        "count": len(payloads),
        "requested_count": len(normalized_ids),
        "unavailable_or_unmatched_count": (
            len(normalized_ids) - len(accessible_notes) + unmatched_count
        ),
        "max_chars_per_note": per_note_budget,
        "total_returned_chars": total_returned_chars,
        "batch_truncated": any(note.get("truncated") for note in payloads),
    }


def edit_note_tool(
    db,
    user_id: str,
    note_id: str,
    content: Optional[str] = None,
    *,
    start_snippet: Optional[str] = None,
    end_snippet: Optional[str] = None,
    edits: list[dict[str, Any]] | None = None,
    expected_updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    if not str(expected_updated_at or "").strip():
        raise ValueError("expected_updated_at is required for edit. View the latest note first.")
    normalized_start = _normalize_optional_snippet(start_snippet)
    normalized_end = _normalize_optional_snippet(end_snippet)
    if edits is not None and (normalized_start is not None or normalized_end is not None):
        raise ValueError("Use either edits or start_snippet/end_snippet, not both.")
    if edits is None and content is None:
        raise ValueError("content is required for an edit without edits.")
    next_content = content or ""
    if edits is not None or normalized_start is not None or normalized_end is not None:
        existing_note, _subscription = _load_accessible_note(db, user_id, note_id)
        existing_content = str(existing_note.content or "")
        if edits is not None:
            next_content = apply_atomic_text_edits(
                existing_content,
                edits,
                artifact_label="note",
            )
        else:
            next_content = _apply_snippet_update(
                existing_content,
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
    is_subscribed = str(getattr(note, "user_id", "") or "") != str(user_id)
    payload = _serialize_note(
        note,
        is_subscribed=is_subscribed,
        share_type="collaborate" if is_subscribed else None,
    )
    if edits is not None:
        payload["edit_count"] = len(edits)
    return payload


def notes_tool(
    db,
    user_id: str,
    type: str,
    note_id: Optional[str] = None,
    note_ids: Optional[List[str]] = None,
    content: Optional[str] = None,
    start_snippet: Optional[str] = None,
    end_snippet: Optional[str] = None,
    edits: Optional[List[Dict[str, Any]]] = None,
    expected_updated_at: Optional[str] = None,
    query: Optional[str] = None,
    heading: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    max_chars: Optional[int] = DEFAULT_TOOL_TEXT_READ_CHARS,
    limit: Optional[int] = DEFAULT_NOTES_TOOL_PAGE_LIMIT,
    offset: Optional[int] = 0,
    cursor: str | None = None,
) -> Dict[str, Any]:
    operation = str(type or "").strip().lower()
    if operation == "list":
        result = list_notes_tool(
            db,
            user_id,
            query=query,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
        result["operation"] = "list"
        return result
    if operation == "view":
        return {
            "operation": "view",
            "note": view_note_tool(
                db,
                user_id,
                str(note_id or "").strip(),
                heading=heading,
                query=query,
                start_line=start_line,
                end_line=end_line,
                max_chars=max_chars,
            )
        }
    if operation == "view_many":
        if not isinstance(note_ids, list):
            raise ValueError("note_ids must be an array for view_many")
        return view_many_notes_tool(
            db,
            user_id,
            list(note_ids or []),
            heading=heading,
            query=query,
            start_line=start_line,
            end_line=end_line,
            max_chars=max_chars,
        )
    if operation == "create":
        return {
            "operation": "create",
            "note": create_note_tool(db, user_id, content or ""),
        }
    if operation == "edit":
        note_id_value = str(note_id or "").strip()
        if not note_id_value:
            raise ValueError("note_id is required for edit")
        return {
            "operation": "edit",
            "note": edit_note_tool(
                db,
                user_id,
                note_id_value,
                content,
                start_snippet=start_snippet,
                end_snippet=end_snippet,
                edits=edits,
                expected_updated_at=expected_updated_at,
            )
        }
    raise ValueError("Invalid type. Allowed values are: list, view, view_many, create, edit.")
