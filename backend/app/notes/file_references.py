from __future__ import annotations

from collections import Counter

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.files.access import get_accessible_file
from app.notes.utils import NoteFileReference, parse_note_file_references


NOTE_FILE_REFERENCE_UNAVAILABLE = "note_file_reference_unavailable"


def _reference_access_key(reference: NoteFileReference) -> tuple[str, str]:
    """Return only the fields that select the protected file target."""
    return (
        str(reference.owner_id or "").strip(),
        str(reference.file_id or "").strip(),
    )


def can_user_access_note_file_reference(
    db: Session,
    acting_user_id: str,
    owner_id: str,
    file_id: str,
) -> bool:
    file_record = get_accessible_file(db, acting_user_id, file_id)
    if not file_record:
        return False
    normalized_owner_id = str(owner_id or "").strip()
    return not normalized_owner_id or str(file_record.user_id) == normalized_owner_id


def added_note_file_references(
    previous_content: str | None,
    next_content: str | None,
) -> list[tuple[int, NoteFileReference]]:
    """Return reference occurrences not preserved from the previous content.

    A multiset comparison is intentional: retaining or moving an existing
    inaccessible reference is allowed, but adding a second copy still counts as
    a new reference. Labels and presentation kinds do not change the protected
    file target and therefore do not require reauthorization.
    """
    preserved_counts = Counter(
        _reference_access_key(reference)
        for reference in parse_note_file_references(previous_content)
    )
    added: list[tuple[int, NoteFileReference]] = []
    for occurrence, reference in enumerate(parse_note_file_references(next_content), start=1):
        key = _reference_access_key(reference)
        if preserved_counts[key] > 0:
            preserved_counts[key] -= 1
            continue
        added.append((occurrence, reference))
    return added


def _unavailable_reference_detail(
    reference: NoteFileReference,
    *,
    occurrence: int,
) -> dict:
    return {
        "code": NOTE_FILE_REFERENCE_UNAVAILABLE,
        "message": "A newly added file reference is unavailable to you.",
        "reference": {
            "kind": reference.kind,
            "owner_id": reference.owner_id,
            "file_id": reference.file_id,
            "label": reference.label,
            "raw_token": reference.raw_token,
            "occurrence": occurrence,
        },
        "owner_action": "share_containing_folder_replace_or_remove",
    }


def validate_note_file_reference_changes(
    db: Session,
    acting_user_id: str,
    next_content: str | None,
    *,
    previous_content: str | None = None,
) -> None:
    """Authorize only file-reference occurrences introduced by this write."""
    for occurrence, reference in added_note_file_references(previous_content, next_content):
        if can_user_access_note_file_reference(
            db,
            acting_user_id,
            reference.owner_id,
            reference.file_id,
        ):
            continue
        # Missing and inaccessible files deliberately use the same response so
        # this validation does not become a file-existence oracle.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_unavailable_reference_detail(reference, occurrence=occurrence),
        )
