"""Bounded Notes context used by every provider adapter."""

from app.utils.helpers import datetime_to_iso


MAX_ATTACHED_NOTES = 20
MAX_ATTACHED_NOTE_CHARS = 40_000
MAX_ATTACHED_NOTES_TOTAL_CHARS = 120_000


def get_notes_context_start(notes_content: list[dict]) -> str:
    """
    Generate provider context with stable note IDs and revision receipts.
    """
    if not notes_content:
        return ""
    
    note_count = len(notes_content)
    plural = "notes" if note_count > 1 else "note"
    
    chunks = [
        (
            f"The user attached {note_count} {plural} as bounded background context. "
            "Each header includes the exact note_id and updated_at revision. A non-truncated "
            "attached snapshot is fresh enough to use directly; use a targeted Notes view only "
            "when a needed section was truncated or the tool reports a revision conflict."
        ),
        "",
    ]
    for i, note in enumerate(notes_content, 1):
        note_text = str(note.get("content") or "").strip()
        note_id = str(note.get("id") or "").strip()
        updated_at = str(note.get("updated_at") or "").strip()
        returned_chars = int(note.get("returned_chars") or len(note_text))
        total_chars = int(note.get("total_chars") or len(note_text))
        truncated = bool(note.get("truncated"))
        chunks.extend(
            [
                (
                    f"--- Note {i} | note_id={note_id} | updated_at={updated_at} | "
                    f"chars={returned_chars}/{total_chars} | truncated={str(truncated).lower()} ---"
                ),
                note_text,
                "",
            ]
        )

    return "\n".join(chunks)


def get_notes_context_end() -> str:
    """
    Generate the context end text for notes.
    
    Returns:
        Context end string to append after notes content
    """
    return """
--- End of Notes ---
Now the main chat conversation continues. Use the above notes as context where relevant.
"""


def fetch_notes_for_chat(db, user_id: str, note_ids: list[str]) -> list[dict]:
    """
    Fetch accessible notes in two queries and enforce one shared context budget.
    """
    if not note_ids or not user_id:
        return []
    
    from app.notes.models import Notes, SharedNoteSubscription

    normalized_ids: list[str] = []
    seen_ids: set[str] = set()
    for raw_note_id in note_ids:
        note_id = str(raw_note_id or "").strip()
        if not note_id or note_id in seen_ids:
            continue
        seen_ids.add(note_id)
        normalized_ids.append(note_id)
        if len(normalized_ids) >= MAX_ATTACHED_NOTES:
            break
    if not normalized_ids:
        return []

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
    subscriptions_by_note_id = {
        str(subscription.note_id): subscription for subscription in subscriptions
    }

    results: list[dict] = []
    remaining_chars = MAX_ATTACHED_NOTES_TOTAL_CHARS
    for note_id in normalized_ids:
        note = notes_by_id.get(note_id)
        if note is None:
            continue
        is_owner = str(note.user_id) == str(user_id)
        subscription = subscriptions_by_note_id.get(note_id)
        share_type = str(getattr(subscription, "share_type", "") or "")
        share_is_active = (
            (share_type == "live" and bool(note.live_share_id))
            or (share_type == "collaborate" and bool(note.collaborate_share_id))
        )
        if not is_owner and not share_is_active:
            continue
        if remaining_chars <= 0:
            break

        source_content = str(note.content or "")
        returned_content = source_content[
            : min(MAX_ATTACHED_NOTE_CHARS, remaining_chars)
        ]
        remaining_chars -= len(returned_content)
        results.append(
            {
                "id": str(note.id),
                "content": returned_content,
                "updated_at": datetime_to_iso(getattr(note, "updated_at", None)),
                "returned_chars": len(returned_content),
                "total_chars": len(source_content),
                "truncated": len(returned_content) < len(source_content),
            }
        )

    return results
