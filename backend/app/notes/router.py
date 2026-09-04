from typing import Annotated
from datetime import datetime, timezone
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.files.access import get_accessible_file
from app.files.models import Files
from app.files.utils import download_file
from app.groups.init import get_user_group_setting_value
from app.logging.models import create_audit_log, get_audit_request_ip
from app.notes.models import (
    Notes,
    ShareType,
    SharedNoteSubscription,
    can_user_view_note,
    create_user_note,
    delete_user_note,
    edit_user_note,
    list_user_notes,
    create_note_share,
    get_note_share_status,
    delete_note_share,
    get_shared_note_by_share_id,
    get_shared_note_preview,
    subscribe_to_shared_note,
    unsubscribe_from_shared_note,
    get_subscribed_notes,
    get_note_subscriber_count,
    clone_shared_note,
    can_user_edit_note,
    get_subscription_for_note,
    detect_share_type_from_id,
    get_note_history,
    restore_note_from_history,
    can_user_view_history,
    get_visible_history_entry,
)
from app.notes.schemas import (
    NoteCreate,
    NoteListResponse,
    NoteReferencedFile,
    NoteResponse,
    NoteListItem,
    OwnedNoteListItem,
    OwnedNoteResponse,
    NoteContentResponse,
    NoteUpdate,
    ShareNoteRequest,
    ShareNoteResponse,
    NoteShareStatusResponse,
    DeleteNoteShareRequest,
    SharedNotePreviewResponse,
    AcceptSharedNoteResponse,
    CloneNoteResponse,
    ShareTypeEnum,
    InviteUsersRequest,
    InviteUsersResponse,
    NoteHistoryResponse,
    SubscribedNoteListItem,
    SubscribedNoteResponse,
    RestoreNoteRequest,
    RestoreNoteResponse,
    NoteRevisionRequest,
)
from app.utils.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MAX_PAGE_OFFSET,
    merged_window_limit,
    page_from_merged_window,
)
from app.notes.utils import note_content_contains_file_reference, note_content_to_plain_text, parse_note_file_references
from app.notes.file_references import can_user_access_note_file_reference
from app.tools.canvas_markdown.pdf import render_canvas_markdown_pdf
from app.utils.attachments import attachment_headers
from app.automations.models import remove_note_from_automations
from app.users.models import get_user
from app.users.sharing import resolve_invitable_users_for_sharing
from app.userNotifications.models import create_user_notification

logger = logging.getLogger(__name__)


def _safe_note_download_filename(title: str | None, extension: str) -> str:
    """Build a conservative attachment filename for a downloaded note."""
    clean_extension = str(extension or "md").strip().lstrip(".").lower() or "md"
    raw_title = str(title or "").replace("\x00", "").strip() or "note"
    raw_title = re.sub(r"[\r\n\t]+", " ", raw_title)
    raw_title = "".join("-" if char in '/\\:*?"<>|' else char for char in raw_title)
    raw_title = re.sub(r"\s+", " ", raw_title).strip(" .") or "note"
    suffix = f".{clean_extension}"
    if raw_title.lower().endswith(suffix):
        return raw_title[:255] or f"note{suffix}"
    base = re.sub(r"\.(md|markdown|pdf|txt)$", "", raw_title, flags=re.IGNORECASE).strip(" .") or "note"
    return f"{base[: max(1, 255 - len(suffix))]}{suffix}"


def _attachment_headers(filename: str) -> dict[str, str]:
    """Return safe attachment headers for note downloads."""
    return attachment_headers(filename, fallback="note")


def ensure_notes_enabled(user, db: Session):
    is_enabled = get_user_group_setting_value(user.id, "notes", "enabled_notes", db)
    if not is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Notes feature disabled for your group")


def ensure_notes_sharing_allowed(user, db: Session):
    is_allowed = get_user_group_setting_value(user.id, "notes", "allow_notes_share", db)
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Note sharing is disabled for your group",
        )


def note_has_existing_share_state(db: Session, note_id: str, user_id: str, share_type: ShareType) -> bool:
    """Return true when an owned note already has share state for this type."""
    note = db.query(Notes).filter(
        Notes.id == note_id,
        Notes.user_id == user_id,
    ).first()
    if not note:
        return False
    share_id_attr = {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type)
    if share_id_attr and getattr(note, share_id_attr, None):
        return True
    return (
        db.query(SharedNoteSubscription)
        .filter(
            SharedNoteSubscription.note_id == note_id,
            SharedNoteSubscription.share_type == share_type.value,
        )
        .count()
        > 0
    )


def ensure_notes_sharing_allowed_or_existing(user, db: Session, note_id: str, share_type: ShareType):
    """Allow new sharing only when enabled, but preserve the same share type."""
    if not note_has_existing_share_state(db, note_id, user.id, share_type):
        ensure_notes_sharing_allowed(user, db)


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


def _extract_note_title(content: str, max_length: int = 50) -> str:
    """Extract title from note content (first line, truncated)."""
    plain_text = note_content_to_plain_text(content)
    if not plain_text:
        return "Untitled Note"
    first_line = plain_text.split("\n")[0].strip()
    if not first_line:
        return "Untitled Note"
    if len(first_line) <= max_length:
        return first_line
    return first_line[:max_length] + "…"


def _extract_note_snippet(content: str, max_length: int = 120) -> str:
    """Extract snippet from note content (after first line)."""
    plain_text = note_content_to_plain_text(content)
    if not plain_text:
        return ""
    lines = plain_text.split("\n")
    if len(lines) <= 1:
        return ""
    rest = " ".join(line.strip() for line in lines[1:] if line.strip())
    if not rest:
        return ""
    if len(rest) <= max_length:
        return rest
    return rest[:max_length] + "…"


def _build_note_list_item(
    note,
    *,
    viewer_user_id: str,
    owner_name: str | None = None,
    share_type: str | None = None,
    subscriber_count: int | None = None,
) -> NoteListItem:
    base_payload = {
        "id": note.id,
        "title": _extract_note_title(note.content),
        "snippet": _extract_note_snippet(note.content),
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }

    if note.user_id == viewer_user_id:
        return OwnedNoteListItem(
            **base_payload,
            user_id=note.user_id,
            clone_share_id=note.clone_share_id,
            live_share_id=note.live_share_id,
            collaborate_share_id=note.collaborate_share_id,
            subscriber_count=subscriber_count,
        )

    return SubscribedNoteListItem(
        **base_payload,
        share_type=share_type or ShareType.LIVE.value,
        owner_name=owner_name,
    )


def _build_note_response(
    note,
    *,
    viewer_user_id: str,
    share_type: str | None = None,
    owner_name: str | None = None,
) -> NoteResponse:
    base_payload = {
        "id": note.id,
        "content": note.content,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }

    if note.user_id == viewer_user_id:
        return OwnedNoteResponse(
            **base_payload,
            user_id=note.user_id,
            clone_share_id=note.clone_share_id,
            live_share_id=note.live_share_id,
            collaborate_share_id=note.collaborate_share_id,
        )

    return SubscribedNoteResponse(
        **base_payload,
        share_type=share_type or ShareType.LIVE.value,
        owner_name=owner_name,
    )


notes_router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


def _build_note_referenced_files_payload(db: Session, content: str, acting_user_id: str) -> list[NoteReferencedFile]:
    payloads: list[NoteReferencedFile] = []
    seen_refs: set[tuple[str, str, str]] = set()

    for reference in parse_note_file_references(content):
        ref_key = (reference.kind, reference.owner_id, reference.file_id)
        if ref_key in seen_refs:
            continue
        seen_refs.add(ref_key)

        file_record = None
        if reference.owner_id:
            file_record = (
                db.query(Files)
                .filter(
                    Files.id == reference.file_id,
                    Files.user_id == reference.owner_id,
                )
                .first()
            )
        else:
            file_record = get_accessible_file(db, acting_user_id, reference.file_id)
        owner_id = str(reference.owner_id or getattr(file_record, "user_id", "") or "").strip()

        if not file_record:
            payloads.append(
                NoteReferencedFile(
                    owner_id=owner_id,
                    file_id=reference.file_id,
                    kind=reference.kind,
                    label=reference.label or None,
                    available=False,
                )
            )
            continue

        can_access = _can_user_access_embedded_file(
            db,
            acting_user_id=acting_user_id,
            owner_id=owner_id,
            file_id=reference.file_id,
        )

        if not can_access:
            payloads.append(
                NoteReferencedFile(
                    owner_id=owner_id,
                    file_id=reference.file_id,
                    kind=reference.kind,
                    label=reference.label or None,
                    available=False,
                )
            )
            continue

        meta = file_record.meta if isinstance(file_record.meta, dict) else {}
        file_name = str(meta.get("original_filename") or file_record.file_name or "").strip() or None

        payloads.append(
            NoteReferencedFile(
                owner_id=owner_id,
                file_id=reference.file_id,
                kind=reference.kind,
                label=reference.label or file_name,
                file_name=file_name,
                file_type=file_record.file_type,
                file_category=file_record.file_category,
                file_size=file_record.file_size,
                available=True,
            )
        )

    return payloads


def _can_user_access_embedded_file(db: Session, acting_user_id: str, owner_id: str, file_id: str) -> bool:
    return can_user_access_note_file_reference(db, acting_user_id, owner_id, file_id)


def _note_sort_key(item: NoteListItem) -> tuple[datetime, str]:
    """Keep merged owned/subscribed pages deterministic when timestamps tie."""
    timestamp = item.updated_at or item.created_at
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp, str(item.id)
    return datetime.min.replace(tzinfo=timezone.utc), str(item.id)


@notes_router.get("/", response_model=NoteListResponse)
def list_notes_route(
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """List all notes for the user (lightweight, no full content)."""
    ensure_notes_enabled(user, db)

    from app.notes.queries import list_note_summaries
    try:
        page = list_note_summaries(db, user.id, query=q, limit=limit, offset=offset, cursor=cursor, management=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_page_cursor"}) from exc
    return NoteListResponse(items=page.pop("notes"), **page)


@notes_router.get("/{note_id}/content", response_model=NoteContentResponse)
def get_note_content_route(
    note_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Get the full content of a single note."""
    ensure_notes_enabled(user, db)
    
    from app.notes.models import Notes
    note = db.query(Notes).filter(Notes.id == note_id).first()
    
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    
    if not can_user_view_note(db, user.id, note_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this note")

    # The canonical share type tells clients whether a subscribed note is
    # read-only (live) or editable (collaborate).
    is_owner = note.user_id == user.id
    subscription = get_subscription_for_note(db, user.id, note_id) if not is_owner else None
    
    return NoteContentResponse(
        id=note.id,
        content=note.content or "",
        updated_at=note.updated_at,
        share_type=subscription.share_type if subscription else None,
        referenced_files=_build_note_referenced_files_payload(db, note.content or "", user.id),
    )


@notes_router.get("/{note_id}/download")
def download_note_route(
    note_id: str,
    request: Request,
    format: str = Query("md", pattern="^(md|pdf)$"),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Download an accessible workspace note as Markdown or a rendered PDF."""
    ensure_notes_enabled(user, db)

    if not can_user_view_note(db, user.id, note_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    note = db.query(Notes).filter(Notes.id == note_id).first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    content = note.content or ""
    title = _extract_note_title(content, max_length=80)
    normalized_format = str(format or "md").lower().strip()

    if normalized_format == "pdf":
        filename = _safe_note_download_filename(title, "pdf")
        result = render_canvas_markdown_pdf(
            db,
            user_id=str(user.id),
            markdown_text=content,
            filename=filename,
        )
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="NOTE_DOWNLOADED",
            details={"note_id": note.id, "format": "pdf"},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="notes",
        )
        return Response(
            content=result.content,
            media_type="application/pdf",
            headers=_attachment_headers(result.filename),
        )

    filename = _safe_note_download_filename(title, "md")
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_DOWNLOADED",
        details={"note_id": note.id, "format": "md"},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers=_attachment_headers(filename),
    )


@notes_router.get("/{note_id}/files/{owner_id}/{file_id}")
def download_note_file_route(
    note_id: str,
    owner_id: str,
    file_id: str,
    request: Request,
    inline: bool = Query(False),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Download a file referenced by a note the current user can access."""
    ensure_notes_enabled(user, db)

    from app.notes.models import Notes

    note = db.query(Notes).filter(Notes.id == note_id).first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    if not can_user_view_note(db, user.id, note_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this note")

    if not note_content_contains_file_reference(note.content, owner_id=owner_id, file_id=file_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File is not referenced by this note")

    if not _can_user_access_embedded_file(db, user.id, owner_id, file_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this file")

    file_record = (
        db.query(Files)
        .filter(
            Files.id == file_id,
            Files.user_id == owner_id,
        )
        .first()
    )
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referenced file not found")

    response = download_file(owner_id, file_id, db, inline=inline)
    disposition = str(response.headers.get("content-disposition") or "")
    if disposition.split(";", 1)[0].strip().lower() == "attachment":
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="NOTE_ATTACHMENT_DOWNLOADED",
            details={
                "note_id": note_id,
                "file_id": file_id,
                "file_owner_user_id": owner_id,
                "is_collaborator": str(note.user_id) != str(user.id),
                "disposition": "attachment",
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="notes",
        )
    return response


@notes_router.post("/", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
def create_note_route(
    payload: NoteCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_notes_enabled(user, db)
    note = create_user_note(db=db, user_id=user.id, content=payload.content)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_CREATED",
        details={"note_id": note.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    return _build_note_response(
        note,
        viewer_user_id=user.id,
    )


@notes_router.patch("/{note_id}", response_model=NoteResponse)
def edit_note_route(
    note_id: str,
    payload: NoteUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_notes_enabled(user, db)
    note = edit_user_note(
        db=db,
        user_id=user.id,
        note_id=note_id,
        content=payload.content,
        expected_updated_at=payload.expected_updated_at,
    )
    
    # Get subscription info if user is a subscriber
    subscription = get_subscription_for_note(db, user.id, note_id)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_UPDATED",
        details={"note_id": note.id, "is_collaborator": subscription is not None},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    
    owner = get_user(db, note.user_id) if subscription else None
    return _build_note_response(
        note,
        viewer_user_id=user.id,
        share_type=subscription.share_type if subscription else None,
        owner_name=_get_user_display_name(owner) if owner else None,
    )


@notes_router.delete("/{note_id}", status_code=status.HTTP_200_OK)
def delete_note_route(
    note_id: str,
    payload: NoteRevisionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_notes_enabled(user, db)
    result = delete_user_note(
        db=db,
        user_id=user.id,
        note_id=note_id,
        expected_updated_at=payload.expected_updated_at,
    )
    try:
        remove_note_from_automations(db=db, user_id=user.id, note_id=note_id)
    except Exception as exc:
        logger.exception(
            "[Notes] Failed to remove note reference from automations",
            extra={
                "event": "note_automation_cleanup_failed",
                "user_id": user.id,
                "note_id": note_id,
                "error": str(exc),
            },
        )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_DELETED",
        details={"note_id": note_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    return result


# ============================================================================
# Note Sharing Endpoints
# ============================================================================

@notes_router.post("/share", response_model=ShareNoteResponse)
def share_note_route(
    payload: ShareNoteRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Create or get existing share link for a note with specified type."""
    ensure_notes_enabled(user, db)

    # Convert schema enum to model enum
    share_type_map = {
        ShareTypeEnum.CLONE: ShareType.CLONE,
        ShareTypeEnum.LIVE: ShareType.LIVE,
        ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
    }
    model_share_type = share_type_map.get(payload.share_type, ShareType.LIVE)
    ensure_notes_sharing_allowed_or_existing(user, db, payload.note_id, model_share_type)
    
    result = create_note_share(db, user.id, payload.note_id, model_share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_SHARED",
        details={"note_id": payload.note_id, "share_id": result["share_id"], "share_type": result["share_type"]},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    return ShareNoteResponse(**result)


@notes_router.get("/share/status", response_model=NoteShareStatusResponse)
def get_note_share_status_route(
    note_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Get the current share status for a note."""
    ensure_notes_enabled(user, db)
    return get_note_share_status(db, user.id, note_id)


@notes_router.post("/share/delete")
def delete_note_share_route(
    payload: DeleteNoteShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Remove sharing from a note. Optionally specify share_type to remove only that type."""
    ensure_notes_enabled(user, db)
    
    # Convert schema enum to model enum if provided
    model_share_type = None
    if payload.share_type:
        share_type_map = {
            ShareTypeEnum.CLONE: ShareType.CLONE,
            ShareTypeEnum.LIVE: ShareType.LIVE,
            ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
        }
        model_share_type = share_type_map.get(payload.share_type)
    
    result = delete_note_share(db, user.id, payload.note_id, model_share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_SHARE_DELETED",
        details={"note_id": payload.note_id, "share_type": result.get("share_type")},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    return result


@notes_router.get("/shared/{share_id}", response_model=SharedNotePreviewResponse)
def get_shared_note_preview_route(
    share_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Authenticated endpoint to get a preview of a shared note."""
    ensure_notes_enabled(user, db)
    return get_shared_note_preview(db, share_id, requesting_user_id=user.id)


@notes_router.post("/shared/{share_id}/accept", response_model=AcceptSharedNoteResponse)
def accept_shared_note_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Subscribe to a shared note (live or collaborate)."""
    ensure_notes_enabled(user, db)
    
    # Detect share type from the share_id
    detected_type = detect_share_type_from_id(db, share_id)
    if not detected_type:
        raise HTTPException(status_code=404, detail="Shared note not found")
    
    # Clone shares should use the clone endpoint
    if detected_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Use the clone endpoint for clone shares")
    
    shared_note = get_shared_note_by_share_id(db, share_id, detected_type)
    if not shared_note:
        raise HTTPException(status_code=404, detail="Shared note not found")
    
    if shared_note.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot subscribe to your own note")
    
    subscribe_to_shared_note(db, user.id, shared_note.id, detected_type)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_SUBSCRIBED",
        details={
            "share_id": share_id, 
            "note_id": shared_note.id, 
            "owner_id": shared_note.user_id,
            "share_type": detected_type.value,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    
    message = "Note added to your workspace"
    if detected_type == ShareType.COLLABORATE:
        message = "Note added to your workspace (you can edit)"
    else:
        message = "Note added to your workspace (live sync enabled)"
    
    return AcceptSharedNoteResponse(
        note_id=shared_note.id,
        share_type=detected_type.value,
        message=message,
    )


@notes_router.post("/clone/{share_id}", response_model=CloneNoteResponse)
def clone_shared_note_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Clone a shared note (creates an independent copy)."""
    ensure_notes_enabled(user, db)
    
    cloned_note = clone_shared_note(db, user.id, share_id)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_CLONED",
        details={"share_id": share_id, "cloned_note_id": cloned_note.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    
    return CloneNoteResponse(
        note_id=cloned_note.id,
        message="Note cloned successfully! It's now your own note.",
    )


@notes_router.post("/shared/{note_id}/unsubscribe")
def unsubscribe_note_route(
    note_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Unsubscribe from a shared note."""
    ensure_notes_enabled(user, db)
    result = unsubscribe_from_shared_note(db, user.id, note_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_UNSUBSCRIBED",
        details={"note_id": note_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    return result


@notes_router.post("/invite", response_model=InviteUsersResponse)
def invite_users_to_note(
    payload: InviteUsersRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Invite users to a shared note by creating notifications."""
    ensure_notes_enabled(user, db)

    note = db.query(Notes).filter(
        Notes.id == payload.item_id,
        Notes.user_id == user.id,
    ).first()
    
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    # Create or get share for this type
    share_type_map = {
        ShareTypeEnum.CLONE: ShareType.CLONE,
        ShareTypeEnum.LIVE: ShareType.LIVE,
        ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
    }
    model_share_type = share_type_map.get(payload.share_type, ShareType.LIVE)
    ensure_notes_sharing_allowed_or_existing(user, db, payload.item_id, model_share_type)
    share_result = create_note_share(db, user.id, payload.item_id, model_share_type)
    
    # Get inviter's display name
    inviter = get_user(db, user.id)
    inviter_name = ""
    if inviter.first_name and inviter.last_name:
        inviter_name = f"{inviter.first_name} {inviter.last_name}"
    elif inviter.first_name:
        inviter_name = inviter.first_name
    else:
        inviter_name = inviter.email.split('@')[0] if inviter.email else "Someone"
    
    # Get note title (first line or truncated content)
    note_title = note.content.split('\n')[0][:50] if note.content else "Untitled Note"
    if len(note_title) == 50:
        note_title += "..."
    
    invited_users = resolve_invitable_users_for_sharing(db, user, payload.user_ids)
    invited_count = 0
    for invited_user in invited_users:
        try:
            create_user_notification(
                db,
                message=f"{inviter_name} invited you to a note: {note_title}",
                category="share_invitation",
                notification_type="info",
                user_ids=[invited_user.id],
                details={
                    "type": "share_invitation",
                    "item_type": "note",
                    "item_id": payload.item_id,
                    "item_title": note_title,
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
        action="NOTE_USERS_INVITED",
        details={
            "note_id": payload.item_id,
            "invited_user_ids": [invited_user.id for invited_user in invited_users],
            "share_type": payload.share_type.value,
            "invited_count": invited_count,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    
    return InviteUsersResponse(
        invited_count=invited_count,
        message=f"Successfully invited {invited_count} user(s) to the note.",
    )


# ============================================================================
# Note History Endpoints
# ============================================================================

@notes_router.get("/{note_id}/history", response_model=NoteHistoryResponse)
def get_note_history_route(
    note_id: str,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Get the edit history for a note."""
    ensure_notes_enabled(user, db)
    
    # Check if user can view this note's history
    if not can_user_view_history(db, user.id, note_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this note's history"
        )
    
    history_data = get_note_history(db, note_id, user.id, limit, offset)
    
    return NoteHistoryResponse(
        entries=history_data["entries"],
        total_count=history_data["total_count"],
        has_more=history_data["has_more"],
    )


@notes_router.post("/{note_id}/restore", response_model=RestoreNoteResponse)
def restore_note_route(
    note_id: str,
    payload: RestoreNoteRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Restore a note to a previous version from history."""
    ensure_notes_enabled(user, db)
    
    # Check if user can edit this note
    if not can_user_edit_note(db, user.id, note_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to restore this note"
        )
    
    # Get the history entry to find the version number. Only restore entries visible to this user.
    history_entry = get_visible_history_entry(db, user.id, note_id, payload.history_id)

    if not history_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="History entry not found"
        )
    
    restore_note_from_history(
        db,
        note_id,
        payload.history_id,
        user.id,
        expected_updated_at=payload.expected_updated_at,
    )
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="NOTE_RESTORED",
        details={
            "note_id": note_id,
            "history_id": payload.history_id,
            "restored_version": history_entry.version_number,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="notes",
    )
    
    return RestoreNoteResponse(
        success=True,
        message=f"Note restored to version {history_entry.version_number}",
        note_id=note_id,
        restored_version=history_entry.version_number,
    )


@notes_router.get("/{note_id}/history/{history_id}")
def get_single_history_entry_route(
    note_id: str,
    history_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Get a single history entry with full content for preview."""
    ensure_notes_enabled(user, db)
    
    if not can_user_view_history(db, user.id, note_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this note's history"
        )
    
    from app.notes.models import _get_owner_display_name, _datetime_to_iso

    history_entry = get_visible_history_entry(db, user.id, note_id, history_id)

    if not history_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="History entry not found"
        )
    
    user_display_name = _get_owner_display_name(db, history_entry.user_id)
    actor_display_name = (
        "AI Assistant" if history_entry.actor_type == "assistant" else user_display_name
    )
    
    return {
        "id": history_entry.id,
        "note_id": history_entry.note_id,
        "user_id": history_entry.user_id,
        "user_display_name": actor_display_name,
        "actor_type": history_entry.actor_type,
        "content": history_entry.content,
        "previous_content": history_entry.previous_content,
        "change_summary": history_entry.change_summary,
        "version_number": history_entry.version_number,
        "created_at": _datetime_to_iso(history_entry.created_at),
    }
