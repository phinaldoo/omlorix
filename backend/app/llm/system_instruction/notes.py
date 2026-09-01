# Notes context for chat messages
# Uses existing notes infrastructure from app.notes.models


def get_notes_context_start(notes_content: list[dict]) -> str:
    """
    Generate the context start text for notes.
    
    Args:
        notes_content: List of dicts with 'id' and 'content' keys
    
    Returns:
        Context start string to prepend to chat history
    """
    if not notes_content:
        return ""
    
    note_count = len(notes_content)
    plural = "notes" if note_count > 1 else "note"
    
    context_start = f"""
The user has attached {note_count} {plural} to this conversation for additional context. Please consider the following notes as background knowledge:

"""
    
    for i, note in enumerate(notes_content, 1):
        note_text = note.get("content", "").strip()
        if note_text:
            context_start += f"--- Note {i} ---\n{note_text}\n\n"
    
    return context_start


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
    Fetch notes by their IDs for a user. 
    User can access their own notes or notes they are subscribed to.
    Uses existing notes infrastructure from app.notes.models.
    
    Args:
        db: Database session
        user_id: The user ID requesting the notes
        note_ids: List of note IDs to fetch
    
    Returns:
        List of dicts with 'id' and 'content' keys for valid, accessible notes
    """
    if not note_ids or not user_id:
        return []
    
    from app.notes.models import Notes, get_subscription_for_note
    
    results = []
    seen_ids = set()
    
    for note_id in note_ids:
        if not note_id or note_id in seen_ids:
            continue
        seen_ids.add(note_id)
        
        note_id_clean = str(note_id).strip()
        if not note_id_clean:
            continue
        
        # Try to find the note
        note = db.query(Notes).filter(
            Notes.id == note_id_clean,
        ).first()
        if not note:
            continue
        
        # Check if user owns the note
        if note.user_id == user_id:
            if note.content:
                results.append({
                    "id": note.id,
                    "content": note.content,
                })
            continue
        
        # Check if user is subscribed to the note using existing function
        subscription = get_subscription_for_note(db, user_id, note_id_clean)
        
        if subscription:
            # Verify the share is still active
            is_active = False
            if subscription.share_type == "live" and note.live_share_id:
                is_active = True
            elif subscription.share_type == "collaborate" and note.collaborate_share_id:
                is_active = True
            
            if is_active and note.content:
                results.append({
                    "id": note.id,
                    "content": note.content,
                })
    
    return results
