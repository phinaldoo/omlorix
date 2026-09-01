from datetime import datetime, timezone
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_admin, verified_user
from app.groups.init import get_user_group_setting_value
from app.files.models import Files
from app.files.utils import (
    CHUNK_SIZE,
    ensure_upload_file_size_limits,
    guess_file_mime_from_name,
    resolve_user_file_upload_limits,
    resolve_user_owned_file_limits,
    resolve_user_max_upload_size_bytes,
    serialized_user_file_quota_admission,
    validate_upload_file,
)
from app.users.models import get_user
from app.users.sharing import resolve_invitable_users_for_sharing
from app.logging.models import create_audit_log, get_audit_request_ip
from app.skills.models import (
    ADMIN_SKILLS_USER_ID,
    SKILLS_ROOT,
    Skills,
    ShareType,
    SharedSkillSubscription,
    delete_skill,
    list_skills,
    update_skill,
    create_admin_skill as create_admin_skill_db,
    paginate_admin_skills,
    get_admin_skill,
    update_admin_skill,
    delete_admin_skill,
    list_admin_skills_by_ids,
    create_skill_share,
    get_skill_share_status,
    delete_skill_share,
    get_shared_skill_by_share_id,
    get_shared_skill_preview,
    subscribe_to_shared_skill,
    unsubscribe_from_shared_skill,
    get_subscribed_skills,
    get_skill_subscriber_count,
    get_skill_with_access,
    clone_shared_skill,
    detect_share_type_from_id,
)
from app.skills.schemas import (
    AdminSkillListItem,
    AdminSkillListResponse,
    AdminSkillImportResult,
    AdminSkillMarkdownImportRequest,
    ShareTypeEnum,
    SkillCreate,
    SkillFilesResponse,
    SkillResponse,
    SkillUpdate,
    ShareSkillRequest,
    ShareSkillResponse,
    SkillShareStatusResponse,
    DeleteSkillShareRequest,
    SharedSkillPreviewResponse,
    AcceptSharedSkillResponse,
    CloneSkillResponse,
    InviteUsersRequest,
    InviteUsersResponse,
    SaveSkillDraftRequest,
    SaveSkillDraftResponse,
    SkillMarkdownImportResult,
)
from app.userNotifications.models import create_user_notification
from app.automations.models import remove_skill_from_automations
from app.skills.utils import (
    SKILL_IMPORT_MAX_ARCHIVE_BYTES,
    SKILL_IMPORT_MAX_FILE_BYTES,
    SKILL_IMPORT_MAX_SKILL_MD_BYTES,
    SKILL_IMPORT_MAX_ENTRIES,
    create_skill,
    delete_skill_file,
    export_admin_skills_archive,
    get_all_skill_files,
    import_admin_skill_from_markdown,
    import_admin_skills_archive,
    import_skill_from_markdown,
    list_skill_files,
    load_skill_markdown_fields,
    save_skill_draft,
    upload_skill_file,
    write_skill_markdown_file,
)

logger = logging.getLogger(__name__)


def ensure_skills_enabled(user, db: Session):
    is_enabled = get_user_group_setting_value(user.id, "skills", "enabled_skills", db)
    if not is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Skills feature disabled for your group")


def ensure_skills_sharing_allowed(user, db: Session):
    is_allowed = get_user_group_setting_value(user.id, "skills", "allow_skill_share", db)
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Skill sharing is disabled for your group",
        )


def skill_has_existing_share_state(db: Session, skill_id: str, user_id: str, share_type: ShareType) -> bool:
    """Return true when an owned skill already has share state for this type."""
    skill = db.query(Skills).filter(
        Skills.id == skill_id,
        Skills.user_id == user_id,
    ).first()
    if not skill:
        return False
    share_id_attr = {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type)
    if share_id_attr and getattr(skill, share_id_attr, None):
        return True
    return (
        db.query(SharedSkillSubscription)
        .filter(
            SharedSkillSubscription.skill_id == skill_id,
            SharedSkillSubscription.share_type == share_type.value,
        )
        .count()
        > 0
    )


def ensure_skills_sharing_allowed_or_existing(user, db: Session, skill_id: str, share_type: ShareType):
    """Allow new sharing only when enabled, but preserve the same share type."""
    if not skill_has_existing_share_state(db, skill_id, user.id, share_type):
        ensure_skills_sharing_allowed(user, db)


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


skills_router = APIRouter(prefix="/api/v1/skills", tags=["skills"])

SKILL_IMPORT_INTERNAL_ERROR = "Skill import failed due to an internal error."


def _read_upload_with_size_limit(
    upload: UploadFile,
    *,
    limit_bytes: int,
    upload_label: str = "Archive",
) -> bytes:
    """Read a seekable upload without buffering more than its allowed size."""
    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(0)

    if size > limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"{upload_label} exceeds the allowed size of {limit_bytes // (1024 * 1024)} MB.",
        )

    payload = upload.file.read(limit_bytes + 1)
    if len(payload) > limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"{upload_label} exceeds the allowed size of {limit_bytes // (1024 * 1024)} MB.",
        )
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Uploaded {upload_label.lower()} is empty",
        )
    return payload


def _iter_user_skill_file_paths(user_id: str):
    user_skill_root = Path(SKILLS_ROOT) / user_id
    if not user_skill_root.exists():
        return
    for folder_name in ("scripts", "references", "assets"):
        for folder_path in user_skill_root.glob(f"*/{folder_name}"):
            yield from folder_path.rglob("*")


def _get_user_skill_file_usage(user_id: str, *, exclude_path: Path | None = None) -> tuple[int, int]:
    excluded = exclude_path.resolve() if exclude_path is not None else None
    file_count = 0
    total_size = 0
    for path in _iter_user_skill_file_paths(user_id):
        if not path.is_file():
            continue
        try:
            if excluded is not None and path.resolve() == excluded:
                continue
            total_size += path.stat().st_size
            file_count += 1
        except FileNotFoundError:
            continue
    return file_count, total_size


def _get_skill_context_file_usage(user_id: str, skill_id: str) -> tuple[int, int]:
    """Return the count and bytes of every supporting file in one skill."""
    skill_root = Path(SKILLS_ROOT) / user_id / skill_id
    file_count = 0
    total_size = 0
    for folder_name in ("scripts", "references", "assets"):
        folder_path = skill_root / folder_name
        if not folder_path.exists():
            continue
        for path in folder_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                total_size += path.stat().st_size
                file_count += 1
            except FileNotFoundError:
                continue
    return file_count, total_size


def _ensure_skill_clone_capacity(
    db: Session,
    *,
    recipient_id: str,
    source_owner_id: str,
    source_skill_id: str,
) -> None:
    """Apply the recipient's aggregate file quotas before deep cloning."""
    clone_file_count, clone_file_size = _get_skill_context_file_usage(
        source_owner_id,
        source_skill_id,
    )
    if clone_file_count == 0:
        return

    max_files_limit, max_storage_limit_bytes = resolve_user_owned_file_limits(db, recipient_id)
    existing_skill_count, existing_skill_size = _get_user_skill_file_usage(recipient_id)
    stored_file_count = db.query(Files).filter(Files.user_id == recipient_id).count()
    stored_file_size = (
        db.query(func.coalesce(func.sum(Files.file_size), 0))
        .filter(Files.user_id == recipient_id)
        .scalar()
    )

    if (
        max_storage_limit_bytes is not None
        and int(stored_file_size or 0) + existing_skill_size + clone_file_size > max_storage_limit_bytes
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum storage quota reached")
    if (
        max_files_limit >= 0
        and stored_file_count + existing_skill_count + clone_file_count > max_files_limit
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum number of uploaded files reached",
        )


def _skill_file_destination(user_id: str, skill_id: str, folder_type: str, filename: str) -> Path:
    return Path(SKILLS_ROOT) / user_id / skill_id / folder_type / filename


def _ensure_skill_file_upload_capacity(
    db: Session,
    user_id: str,
    file_size: int,
    *,
    max_files_limit: int,
    max_user_storage_limit_bytes: int | None,
    destination_path: Path,
) -> None:
    skill_file_count, skill_file_size = _get_user_skill_file_usage(user_id, exclude_path=destination_path)

    if max_user_storage_limit_bytes is not None:
        stored_file_size = (
            db.query(func.coalesce(func.sum(Files.file_size), 0))
            .filter(Files.user_id == user_id)
            .scalar()
        )
        if int(stored_file_size or 0) + skill_file_size + file_size > max_user_storage_limit_bytes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum storage quota reached")

    if max_files_limit >= 0:
        stored_file_count = db.query(Files).filter(Files.user_id == user_id).count()
        if stored_file_count + skill_file_count >= max_files_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum number of uploaded files reached",
            )


async def _spool_upload_to_temp_file(
    upload: UploadFile,
    *,
    max_upload_bytes: int,
    max_upload_mb: int,
) -> tuple[Path, int]:
    fd, temp_name = tempfile.mkstemp(prefix="skill-upload-", suffix=".upload")
    temp_path = Path(temp_name)
    bytes_written = 0
    try:
        with os.fdopen(fd, "wb") as buffer:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                ensure_upload_file_size_limits(
                    bytes_written,
                    max_upload_bytes=max_upload_bytes,
                    max_upload_mb=max_upload_mb,
                )
                buffer.write(chunk)

        if bytes_written == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

        validate_upload_file(temp_path, fallback_mime=guess_file_mime_from_name(upload.filename))
        return temp_path, bytes_written
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.seek(0)


async def _store_validated_skill_upload(
    upload: UploadFile,
    *,
    user_id: str,
    uploading_user_id: str | None = None,
    skill_id: str,
    folder_type: str,
    db: Session,
) -> dict[str, str | int]:
    original_filename = upload.filename or ""
    max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(db, user_id)
    max_upload_bytes, max_upload_mb = resolve_user_max_upload_size_bytes(db, user_id)
    if uploading_user_id and uploading_user_id != user_id:
        collaborator_files_limit, collaborator_storage_limit = resolve_user_file_upload_limits(
            db,
            uploading_user_id,
        )
        if collaborator_files_limit >= 0:
            max_files_limit = (
                collaborator_files_limit
                if max_files_limit < 0
                else min(max_files_limit, collaborator_files_limit)
            )
        if collaborator_storage_limit is not None:
            max_user_storage_limit_bytes = (
                collaborator_storage_limit
                if max_user_storage_limit_bytes is None
                else min(max_user_storage_limit_bytes, collaborator_storage_limit)
            )
        collaborator_upload_bytes, collaborator_upload_mb = resolve_user_max_upload_size_bytes(
            db,
            uploading_user_id,
        )
        if collaborator_upload_bytes < max_upload_bytes:
            max_upload_bytes = collaborator_upload_bytes
            max_upload_mb = collaborator_upload_mb
    temp_path, file_size = await _spool_upload_to_temp_file(
        upload,
        max_upload_bytes=max_upload_bytes,
        max_upload_mb=max_upload_mb,
    )
    try:
        destination_path = _skill_file_destination(
            user_id,
            skill_id,
            folder_type,
            Path(original_filename).name,
        )
        with serialized_user_file_quota_admission(db, user_id):
            _ensure_skill_file_upload_capacity(
                db,
                user_id,
                file_size,
                max_files_limit=max_files_limit,
                max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                destination_path=destination_path,
            )
            return upload_skill_file(
                user_id,
                skill_id,
                folder_type,
                original_filename,
                source_path=temp_path,
            )
    finally:
        temp_path.unlink(missing_ok=True)


async def _store_validated_admin_skill_upload(
    upload: UploadFile,
    *,
    skill_id: str,
    folder_type: str,
) -> dict[str, str | int]:
    temp_path, _file_size = await _spool_upload_to_temp_file(
        upload,
        max_upload_bytes=SKILL_IMPORT_MAX_FILE_BYTES,
        max_upload_mb=max(1, SKILL_IMPORT_MAX_FILE_BYTES // (1024 * 1024)),
    )
    try:
        return upload_skill_file(
            ADMIN_SKILLS_USER_ID,
            skill_id,
            folder_type,
            upload.filename,
            source_path=temp_path,
        )
    finally:
        temp_path.unlink(missing_ok=True)


def _build_files_response(user_id: str, skill_id: str) -> SkillFilesResponse:
    """Build a SkillFilesResponse from disk."""
    all_files = get_all_skill_files(user_id, skill_id)
    return SkillFilesResponse(
        scripts=all_files.get("scripts", []),
        references=all_files.get("references", []),
        assets=all_files.get("assets", []),
    )


def _serialize_admin_skill_detail(skill) -> SkillResponse:
    """
    Serialize one complete managed skill for the create/edit experience.

    This intentionally performs the filesystem-backed Markdown and bundled-file
    reads that are too expensive to repeat for every item in the list view.
    """
    markdown_fields = load_skill_markdown_fields(ADMIN_SKILLS_USER_ID, skill.id)
    return SkillResponse(
        id=skill.id,
        user_id=ADMIN_SKILLS_USER_ID,
        title=skill.name,
        description=markdown_fields.get("description") or skill.description,
        content=skill.content,
        icon=skill.icon,
        created_at=skill.created_at.isoformat(),
        updated_at=skill.updated_at.isoformat(),
        compatibility=markdown_fields.get("compatibility"),
        license=markdown_fields.get("license"),
        metadata=markdown_fields.get("metadata"),
        author=markdown_fields.get("author"),
        files=_build_files_response(ADMIN_SKILLS_USER_ID, skill.id),
    )


@skills_router.get("", response_model=List[SkillResponse])
async def get_skills(
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    """List all skills for the current user, including managed skills and subscribed shared skills."""
    ensure_skills_enabled(user, db)
    
    responses: list[SkillResponse] = []
    
    # Get user's own skills
    skills = list_skills(db, user.id)
    for s in skills:
        markdown_fields = load_skill_markdown_fields(user.id, s.id)
        has_share = s.clone_share_id or s.live_share_id or s.collaborate_share_id
        subscriber_count = get_skill_subscriber_count(db, s.id) if has_share else None
        responses.append(
            SkillResponse(
                id=s.id,
                user_id=s.user_id,
                title=s.name,
                description=markdown_fields.get("description") or s.description,
                content=s.content,
                icon=s.icon,
                created_at=s.created_at.isoformat(),
                updated_at=s.updated_at.isoformat(),
                compatibility=markdown_fields.get("compatibility"),
                license=markdown_fields.get("license"),
                metadata=markdown_fields.get("metadata"),
                author=markdown_fields.get("author"),
                files=_build_files_response(user.id, s.id),
                is_admin_skill=False,
                clone_share_id=s.clone_share_id,
                live_share_id=s.live_share_id,
                collaborate_share_id=s.collaborate_share_id,
                is_subscribed=False,
                subscriber_count=subscriber_count,
            )
        )
    
    # Get subscribed shared skills (returns tuples of (skill, subscription))
    subscribed_data = get_subscribed_skills(db, user.id)
    for s, sub in subscribed_data:
        markdown_fields = load_skill_markdown_fields(s.user_id, s.id)
        owner = get_user(db, s.user_id)
        owner_name = _get_user_display_name(owner)
        responses.append(
            SkillResponse(
                id=s.id,
                user_id=s.user_id,
                title=s.name,
                description=markdown_fields.get("description") or s.description,
                content=s.content,
                icon=s.icon,
                created_at=s.created_at.isoformat(),
                updated_at=s.updated_at.isoformat(),
                compatibility=markdown_fields.get("compatibility"),
                license=markdown_fields.get("license"),
                metadata=markdown_fields.get("metadata"),
                author=markdown_fields.get("author"),
                files=_build_files_response(s.user_id, s.id),
                is_admin_skill=False,
                clone_share_id=s.clone_share_id,
                live_share_id=s.live_share_id,
                collaborate_share_id=s.collaborate_share_id,
                is_subscribed=True,
                share_type=sub.share_type,
                owner_name=owner_name,
            )
        )
    
    # Get admin skills from user's group
    try:
        admin_skill_ids = get_user_group_setting_value(user.id, "skills", "admin_skill_ids", db)
        if admin_skill_ids and isinstance(admin_skill_ids, list):
            admin_skills = list_admin_skills_by_ids(db, admin_skill_ids)
            for s in admin_skills:
                markdown_fields = load_skill_markdown_fields(ADMIN_SKILLS_USER_ID, s.id)
                responses.append(
                    SkillResponse(
                        id=s.id,
                        user_id=ADMIN_SKILLS_USER_ID,
                        title=s.name,
                        description=markdown_fields.get("description") or s.description,
                        content=s.content,
                        icon=s.icon,
                        created_at=s.created_at.isoformat(),
                        updated_at=s.updated_at.isoformat(),
                        compatibility=markdown_fields.get("compatibility"),
                        license=markdown_fields.get("license"),
                        metadata=markdown_fields.get("metadata"),
                        author=markdown_fields.get("author"),
                        files=_build_files_response(ADMIN_SKILLS_USER_ID, s.id),
                        is_admin_skill=True,
                    )
                )
    except Exception:
        pass
    
    return responses


@skills_router.post("/import-markdown", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def import_markdown_skill_endpoint(
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Import a single skill from a SKILL.md markdown text (sent as JSON body with 'markdown' key)."""
    ensure_skills_enabled(user, db)
    try:
        body = await request.json()
        markdown_text = body.get("markdown", "")
        if not markdown_text or not markdown_text.strip():
            raise ValueError("No markdown content provided")
        skill = import_skill_from_markdown(db, user.id, markdown_text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="import_skill_markdown",
        details={"skill_id": skill.id, "title": skill.name},
        ip_address=get_audit_request_ip(request, db),
    )
    markdown_fields = load_skill_markdown_fields(user.id, skill.id)
    return SkillResponse(
        id=skill.id,
        user_id=skill.user_id,
        title=skill.name,
        description=markdown_fields.get("description") or skill.description,
        content=skill.content,
        icon=skill.icon,
        created_at=skill.created_at.isoformat(),
        updated_at=skill.updated_at.isoformat(),
        compatibility=markdown_fields.get("compatibility"),
        license=markdown_fields.get("license"),
        metadata=markdown_fields.get("metadata"),
        author=markdown_fields.get("author"),
        files=_build_files_response(user.id, skill.id),
    )


@skills_router.post(
    "/import-markdown-files",
    response_model=SkillMarkdownImportResult,
)
async def import_markdown_skill_files_endpoint(
    request: Request,
    files: List[UploadFile] = File(...),
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """
    Import multiple user skills from uploaded ``SKILL.md`` documents.

    Files are processed independently so a malformed document does not discard
    valid siblings from the same selection. The response identifies every
    source file that failed, allowing the modal to retain only those files for
    review and retry.
    """
    ensure_skills_enabled(user, db)
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files were uploaded")
    if len(files) > SKILL_IMPORT_MAX_ENTRIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many uploaded files. The maximum is {SKILL_IMPORT_MAX_ENTRIES}.",
        )

    created: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for upload_index, upload in enumerate(files):
        filename = Path(upload.filename or "").name or "(unnamed file)"
        try:
            if not filename.lower().endswith(".md"):
                raise ValueError("Only .md (Markdown) files are accepted.")
            markdown_payload = _read_upload_with_size_limit(
                upload,
                limit_bytes=SKILL_IMPORT_MAX_SKILL_MD_BYTES,
                upload_label="Skill Markdown file",
            )
            markdown_text = markdown_payload.decode("utf-8-sig")
            skill = import_skill_from_markdown(db, user.id, markdown_text)
            created.append({"id": skill.id, "name": skill.name})
        except HTTPException as exc:
            errors.append(
                {"source": filename, "error": str(exc.detail), "index": upload_index}
            )
        except (UnicodeError, ValueError) as exc:
            errors.append({"source": filename, "error": str(exc), "index": upload_index})
        except Exception:  # pylint: disable=broad-except
            # Keep one unexpected persistence or filesystem failure from
            # aborting the remaining files in this explicitly partial batch.
            logger.exception("Unexpected user skill import failure for %s", filename)
            db.rollback()
            errors.append(
                {
                    "source": filename,
                    "error": SKILL_IMPORT_INTERNAL_ERROR,
                    "index": upload_index,
                }
            )

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="import_skill_markdown_files",
        details={
            "uploaded_file_count": len(files),
            "created_count": len(created),
            "error_count": len(errors),
        },
        ip_address=get_audit_request_ip(request, db),
    )
    return {"created": created, "errors": errors}


@skills_router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill_endpoint(
    request: Request,
    skill_data: SkillCreate,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Create a new skill for the current user."""
    ensure_skills_enabled(user, db)
    try:
        skill = create_skill(
            db=db,
            user_id=user.id,
            name=skill_data.name,
            description=skill_data.description,
            content=skill_data.content,
            icon=skill_data.icon,
            compatibility=skill_data.compatibility,
            license=skill_data.license,
            metadata=skill_data.metadata,
        )
        write_skill_markdown_file(
            user_id=user.id,
            skill_id=skill.id,
            name=skill_data.name,
            description=skill_data.description,
            content=skill_data.content,
            license_value=skill_data.license,
            compatibility=skill_data.compatibility,
            metadata=skill_data.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="create_skill",
        details={"skill_id": skill.id, "title": skill.name},
        ip_address=get_audit_request_ip(request, db),
    )
    markdown_fields = load_skill_markdown_fields(user.id, skill.id)
    return SkillResponse(
        id=skill.id,
        user_id=skill.user_id,
        title=skill.name,
        description=markdown_fields.get("description") or skill.description,
        content=skill.content,
        icon=skill.icon,
        created_at=skill.created_at.isoformat(),
        updated_at=skill.updated_at.isoformat(),
        compatibility=markdown_fields.get("compatibility"),
        license=markdown_fields.get("license"),
        metadata=markdown_fields.get("metadata"),
        author=markdown_fields.get("author"),
        files=_build_files_response(user.id, skill.id),
    )


@skills_router.patch("/{skill_id}", response_model=SkillResponse)
async def update_skill_endpoint(
    request: Request,
    skill_id: str,
    skill_data: SkillUpdate,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Update an existing skill."""
    ensure_skills_enabled(user, db)
    skill = update_skill(
        db=db,
        user_id=user.id,
        skill_id=skill_id,
        title=skill_data.title,
        description=skill_data.description,
        content=skill_data.content,
        icon=skill_data.icon,
    )
    owner_id = skill.user_id
    markdown_fields = load_skill_markdown_fields(owner_id, skill_id)
    write_skill_markdown_file(
        user_id=owner_id,
        skill_id=skill_id,
        name=skill.name,
        description=(
            skill_data.description
            if skill_data.description is not None
            else (markdown_fields.get("description") or skill.description)
        ),
        content=skill.content,
        license_value=skill_data.license or markdown_fields.get("license"),
        compatibility=skill_data.compatibility or markdown_fields.get("compatibility"),
        metadata=skill_data.metadata or markdown_fields.get("metadata"),
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="update_skill",
        details={
            "skill_id": skill.id,
            "title": skill.name,
            "owner_id": owner_id,
            "collaborative_edit": owner_id != user.id,
        },
        ip_address=get_audit_request_ip(request, db),
    )
    updated_fields = load_skill_markdown_fields(owner_id, skill_id)
    return SkillResponse(
        id=skill.id,
        user_id=skill.user_id,
        title=skill.name,
        description=updated_fields.get("description") or skill.description,
        content=skill.content,
        icon=skill.icon,
        created_at=skill.created_at.isoformat(),
        updated_at=skill.updated_at.isoformat(),
        compatibility=updated_fields.get("compatibility"),
        license=updated_fields.get("license"),
        metadata=updated_fields.get("metadata"),
        author=updated_fields.get("author"),
        files=_build_files_response(owner_id, skill_id),
        clone_share_id=skill.clone_share_id,
        live_share_id=skill.live_share_id,
        collaborate_share_id=skill.collaborate_share_id,
        is_subscribed=owner_id != user.id,
        share_type="collaborate" if owner_id != user.id else None,
    )


@skills_router.post("/draft/save", response_model=SaveSkillDraftResponse, status_code=status.HTTP_201_CREATED)
async def save_skill_draft_endpoint(
    request: Request,
    payload: SaveSkillDraftRequest,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Persist a reviewed skill draft after explicit user confirmation."""
    ensure_skills_enabled(user, db)
    try:
        skill = save_skill_draft(
            db=db,
            user_id=user.id,
            skill_markdown=payload.skill_markdown,
            icon=payload.icon,
            files=[file.model_dump(exclude_none=True) for file in payload.files],
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="save_skill_draft",
        details={"skill_id": skill.id, "title": skill.name},
        ip_address=get_audit_request_ip(request, db),
    )
    return SaveSkillDraftResponse(
        skill_id=skill.id,
        title=skill.name,
        message="Skill added to your workspace.",
    )


@skills_router.delete("/{skill_id}")
async def delete_skill_endpoint(
    request: Request,
    skill_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Delete a skill."""
    ensure_skills_enabled(user, db)
    result = delete_skill(db=db, user_id=user.id, skill_id=skill_id)
    try:
        remove_skill_from_automations(db=db, user_id=user.id, skill_id=skill_id)
    except Exception as exc:
        logger.exception(
            "[Skills] Failed to remove skill reference from automations",
            extra={
                "event": "skill_automation_cleanup_failed",
                "user_id": user.id,
                "skill_id": skill_id,
                "error": str(exc),
            },
        )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="delete_skill",
        details={"skill_id": skill_id},
        ip_address=get_audit_request_ip(request, db),
    )
    return result


# ============================================================================
# Skill Sharing Endpoints
# ============================================================================

def _map_share_type(schema_type: ShareTypeEnum) -> ShareType:
    """Map schema ShareTypeEnum to model ShareType."""
    return {
        ShareTypeEnum.CLONE: ShareType.CLONE,
        ShareTypeEnum.LIVE: ShareType.LIVE,
        ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
    }.get(schema_type, ShareType.LIVE)


@skills_router.post("/share", response_model=ShareSkillResponse)
async def share_skill_endpoint(
    request: Request,
    payload: ShareSkillRequest,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Create or get existing share link for a skill with specified type."""
    ensure_skills_enabled(user, db)
    share_type = _map_share_type(payload.share_type)
    ensure_skills_sharing_allowed_or_existing(user, db, payload.skill_id, share_type)
    result = create_skill_share(db, user.id, payload.skill_id, share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="share_skill",
        details={
            "skill_id": payload.skill_id,
            "share_id": result["share_id"],
            "share_type": result["share_type"],
        },
        ip_address=get_audit_request_ip(request, db),
    )
    return ShareSkillResponse(**result)


@skills_router.get("/share/status", response_model=SkillShareStatusResponse)
async def get_share_status_endpoint(
    request: Request,
    skill_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Get the current share status for all share types of a skill."""
    ensure_skills_enabled(user, db)
    result = get_skill_share_status(db, user.id, skill_id)
    return SkillShareStatusResponse(**result)


@skills_router.post("/share/delete")
async def delete_share_endpoint(
    request: Request,
    payload: DeleteSkillShareRequest,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Remove sharing from a skill. Optionally specify share_type to remove only that type."""
    ensure_skills_enabled(user, db)
    share_type = _map_share_type(payload.share_type) if payload.share_type else None
    result = delete_skill_share(db, user.id, payload.skill_id, share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="unshare_skill",
        details={
            "skill_id": payload.skill_id,
            "share_type": payload.share_type.value if payload.share_type else "all",
        },
        ip_address=get_audit_request_ip(request, db),
    )
    return result


@skills_router.get("/shared/{share_id}", response_model=SharedSkillPreviewResponse)
async def get_shared_skill_preview_endpoint(
    request: Request,
    share_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Authenticated endpoint to get a preview of a shared skill."""
    ensure_skills_enabled(user, db)
    return get_shared_skill_preview(db, share_id, requesting_user_id=user.id)


@skills_router.post("/shared/{share_id}/accept", response_model=AcceptSharedSkillResponse)
async def accept_shared_skill_endpoint(
    request: Request,
    share_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Subscribe to a shared skill (live or collaborate sharing)."""
    ensure_skills_enabled(user, db)
    
    # Detect share type from the share_id
    detected_type = detect_share_type_from_id(db, share_id)
    if detected_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Clone shares should use the /clone endpoint")
    
    shared_skill = get_shared_skill_by_share_id(db, share_id)
    if not shared_skill:
        raise HTTPException(status_code=404, detail="Shared skill not found")
    
    if shared_skill.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot subscribe to your own skill")
    
    share_type = detected_type or ShareType.LIVE
    subscribe_to_shared_skill(db, user.id, shared_skill.id, share_type)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="subscribe_to_shared_skill",
        details={
            "share_id": share_id,
            "skill_id": shared_skill.id,
            "owner_id": shared_skill.user_id,
            "title": shared_skill.name,
            "share_type": share_type.value,
        },
        ip_address=get_audit_request_ip(request, db),
    )
    
    message = "Skill added to your workspace"
    if share_type == ShareType.COLLABORATE:
        message += " (you can edit)"
    else:
        message += " (view only, live sync enabled)"
    
    return AcceptSharedSkillResponse(
        skill_id=shared_skill.id,
        title=shared_skill.name,
        message=message,
    )


@skills_router.post("/clone/{share_id}", response_model=CloneSkillResponse)
async def clone_skill_endpoint(
    request: Request,
    share_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Clone a shared skill to create a new independent copy."""
    ensure_skills_enabled(user, db)

    source_skill = get_shared_skill_by_share_id(db, share_id, ShareType.CLONE)
    if not source_skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared skill not found or not available for cloning")
    if source_skill.user_id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot clone your own skill")

    with serialized_user_file_quota_admission(db, user.id):
        _ensure_skill_clone_capacity(
            db,
            recipient_id=user.id,
            source_owner_id=source_skill.user_id,
            source_skill_id=source_skill.id,
        )
        cloned_skill = clone_shared_skill(db, user.id, share_id)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="clone_shared_skill",
        details={"share_id": share_id, "cloned_skill_id": cloned_skill.id},
        ip_address=get_audit_request_ip(request, db),
    )
    
    return CloneSkillResponse(
        skill_id=cloned_skill.id,
        title=cloned_skill.name,
        message="Skill cloned successfully! You now have your own copy.",
    )


@skills_router.post("/shared/{skill_id}/unsubscribe")
async def unsubscribe_shared_skill_endpoint(
    request: Request,
    skill_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Unsubscribe from a shared skill (remove it from your workspace)."""
    ensure_skills_enabled(user, db)
    
    result = unsubscribe_from_shared_skill(db, user.id, skill_id)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="unsubscribe_from_shared_skill",
        details={"skill_id": skill_id},
        ip_address=get_audit_request_ip(request, db),
    )
    
    return result


@skills_router.post("/invite", response_model=InviteUsersResponse)
async def invite_users_to_skill(
    payload: InviteUsersRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Invite users to a shared skill by creating notifications."""
    ensure_skills_enabled(user, db)
    
    skill = db.query(Skills).filter(
        Skills.id == payload.item_id,
        Skills.user_id == user.id
    ).first()
    
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    # Create or get share for this type
    share_type = _map_share_type(payload.share_type)
    ensure_skills_sharing_allowed_or_existing(user, db, payload.item_id, share_type)
    share_result = create_skill_share(db, user.id, payload.item_id, share_type)
    
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
                message=f"{inviter_name} invited you to a skill: {skill.name}",
                category="share_invitation",
                notification_type="info",
                user_ids=[invited_user.id],
                details={
                    "type": "share_invitation",
                    "item_type": "skill",
                    "item_id": payload.item_id,
                    "item_title": skill.name,
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
        action="SKILL_USERS_INVITED",
        details={
            "skill_id": payload.item_id,
            "invited_user_ids": [invited_user.id for invited_user in invited_users],
            "share_type": payload.share_type.value,
            "invited_count": invited_count,
        },
        ip_address=get_audit_request_ip(request, db),
    )
    
    return InviteUsersResponse(
        invited_count=invited_count,
        message=f"Successfully invited {invited_count} user(s) to the skill.",
    )


# ============================================================================
# Agent Skills File Management Endpoints (scripts/, references/, assets/)
# ============================================================================

@skills_router.get("/{skill_id}/files/{folder_type}")
async def list_skill_files_endpoint(
    request: Request,
    skill_id: str,
    folder_type: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    """List files in a skill's folder (scripts, references, or assets)."""
    ensure_skills_enabled(user, db)
    skill, _subscription = get_skill_with_access(db, user.id, skill_id)
    try:
        files = list_skill_files(skill.user_id, skill_id, folder_type)
        return {"files": files}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@skills_router.post("/{skill_id}/files/{folder_type}")
async def upload_skill_files_endpoint(
    request: Request,
    skill_id: str,
    folder_type: str,
    files: List[UploadFile] = File(...),
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Upload one or more files to a skill's folder (scripts, references, or assets)."""
    ensure_skills_enabled(user, db)
    skill, _subscription = get_skill_with_access(
        db,
        user.id,
        skill_id,
        require_edit=True,
    )
    owner_id = skill.user_id
    
    uploaded = []
    errors = []
    
    for file in files:
        try:
            result = await _store_validated_skill_upload(
                file,
                user_id=owner_id,
                uploading_user_id=user.id,
                skill_id=skill_id,
                folder_type=folder_type,
                db=db,
            )
            uploaded.append(result)
        except HTTPException as exc:
            errors.append({"filename": file.filename, "error": str(exc.detail)})
        except ValueError as exc:
            errors.append({"filename": file.filename, "error": str(exc)})
        except Exception:
            logger.exception(
                "[Skills] Skill file upload failed",
                extra={
                    "event": "skill_file_upload_failed",
                    "user_id": user.id,
                    "skill_id": skill_id,
                    "folder_type": folder_type,
                    # ``filename`` is a reserved LogRecord attribute and
                    # cannot safely be supplied through logging ``extra``.
                    "uploaded_filename": getattr(file, "filename", None),
                },
            )
            errors.append({"filename": file.filename, "error": "Upload failed due to internal error"})
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="upload_skill_files",
        details={
            "skill_id": skill_id,
            "owner_id": owner_id,
            "collaborative_edit": owner_id != user.id,
            "folder_type": folder_type,
            "uploaded_count": len(uploaded),
            "error_count": len(errors),
        },
        ip_address=get_audit_request_ip(request, db),
    )
    
    return {"uploaded": uploaded, "errors": errors}


@skills_router.delete("/{skill_id}/files/{folder_type}/{filename}")
async def delete_skill_file_endpoint(
    request: Request,
    skill_id: str,
    folder_type: str,
    filename: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Delete a file from a skill's folder (scripts, references, or assets)."""
    ensure_skills_enabled(user, db)
    skill, _subscription = get_skill_with_access(
        db,
        user.id,
        skill_id,
        require_edit=True,
    )
    owner_id = skill.user_id
    try:
        delete_skill_file(owner_id, skill_id, folder_type, filename)
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="delete_skill_file",
            details={
                "skill_id": skill_id,
                "owner_id": owner_id,
                "collaborative_edit": owner_id != user.id,
                "folder_type": folder_type,
                "filename": filename,
            },
            ip_address=get_audit_request_ip(request, db),
        )
        return {"deleted": True, "filename": filename}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# ============================================================================
# Admin Skills Endpoints
# ============================================================================

@skills_router.get("/admin", response_model=AdminSkillListResponse)
async def get_admin_skills_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
):
    """
    Return one server-filtered page of lightweight managed-skill summaries.

    Search is performed before pagination, while full Markdown metadata and file
    manifests remain deferred until a single skill is opened for editing.
    """
    rows, total, total_pages, resolved_page = paginate_admin_skills(
        db,
        page=page,
        page_size=page_size,
        search=search,
    )
    return AdminSkillListResponse(
        items=[
            AdminSkillListItem(
                id=row.id,
                title=row.title,
                icon=row.icon,
                content_preview=row.content_preview or "",
            )
            for row in rows
        ],
        page=resolved_page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@skills_router.get("/admin/export")
async def export_admin_skills_endpoint(
    request: Request,
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Export every managed skill as a standards-compatible skill directory."""
    archive_buffer, total_skills = export_admin_skills_archive(db)
    filename = f"managed-skills-export-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip"
    response = StreamingResponse(
        iter([archive_buffer.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Exported-Skill-Count": str(total_skills),
        },
    )
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="export_admin_skills",
        details={"skill_count": total_skills, "format": "agent-skills-zip"},
        ip_address=get_audit_request_ip(request, db),
    )
    return response


@skills_router.post(
    "/admin/import-markdown",
    response_model=AdminSkillImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_admin_skill_markdown_endpoint(
    request: Request,
    payload: AdminSkillMarkdownImportRequest,
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Import one managed skill from pasted ``SKILL.md`` Markdown."""
    try:
        skill = import_admin_skill_from_markdown(db, payload.markdown)
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="import_admin_skill_markdown",
        details={"skill_id": skill.id, "title": skill.name},
        ip_address=get_audit_request_ip(request, db),
    )
    return {"created": [{"id": skill.id, "name": skill.name}], "errors": []}


@skills_router.post("/admin/import-files", response_model=AdminSkillImportResult)
async def import_admin_skill_files_endpoint(
    request: Request,
    files: List[UploadFile] = File(...),
    archive_selections: str | None = Form(default=None),
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """
    Import managed skills from multiple Markdown files and/or ZIP packages.

    Each uploaded file is handled independently so one malformed document does
    not prevent valid siblings from importing. ZIP members receive the archive
    security checks in ``import_admin_skills_archive`` before extraction.
    """
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files were uploaded")
    if len(files) > SKILL_IMPORT_MAX_ENTRIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many uploaded files. The maximum is {SKILL_IMPORT_MAX_ENTRIES}.",
        )

    parsed_archive_selections: list[list[str] | None] = [None] * len(files)
    if archive_selections:
        try:
            raw_selections = json.loads(archive_selections)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid archive selection payload.",
            ) from exc
        if not isinstance(raw_selections, list) or len(raw_selections) != len(files):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Archive selections must correspond to the uploaded files.",
            )
        parsed_archive_selections = []
        for selection in raw_selections:
            if selection is None:
                parsed_archive_selections.append(None)
                continue
            if not isinstance(selection, list) or not all(
                isinstance(folder, str) and folder.strip() for folder in selection
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Each archive selection must be a list of skill folders.",
                )
            parsed_archive_selections.append([folder.strip() for folder in selection])

    created: list[dict] = []
    errors: list[dict] = []
    source_types: set[str] = set()
    for upload_index, upload in enumerate(files):
        filename = Path(upload.filename or "").name
        normalized_name = filename.lower()
        try:
            if normalized_name.endswith(".zip"):
                source_types.add("zip")
                archive_payload = _read_upload_with_size_limit(
                    upload,
                    limit_bytes=SKILL_IMPORT_MAX_ARCHIVE_BYTES,
                )
                selected_folders = parsed_archive_selections[upload_index]
                result = import_admin_skills_archive(
                    db,
                    archive_payload,
                    selected_folder_prefixes=(
                        set(selected_folders) if selected_folders is not None else None
                    ),
                )
                created.extend(result.get("created", []))
                errors.extend(
                    {**entry, "source": filename or "(unnamed file)"}
                    for entry in result.get("errors", [])
                )
                continue

            if normalized_name.endswith(".md"):
                source_types.add("markdown")
                markdown_payload = _read_upload_with_size_limit(
                    upload,
                    limit_bytes=SKILL_IMPORT_MAX_SKILL_MD_BYTES,
                    upload_label="Skill Markdown file",
                )
                markdown_text = markdown_payload.decode("utf-8-sig")
                skill = import_admin_skill_from_markdown(db, markdown_text)
                created.append({"id": skill.id, "name": skill.name})
                continue

            errors.append(
                {
                    "source": filename or "(unnamed file)",
                    "error": "Only .md and .zip files are supported.",
                }
            )
        except HTTPException as exc:
            errors.append(
                {
                    "source": filename or "(unnamed file)",
                    "error": str(exc.detail),
                }
            )
        except (UnicodeError, ValueError) as exc:
            errors.append({"source": filename or "(unnamed file)", "error": str(exc)})
        except Exception:  # pylint: disable=broad-except
            # Preserve per-upload isolation for unanticipated database,
            # archive, or filesystem failures.
            logger.exception("Unexpected admin skill import failure for %s", filename)
            db.rollback()
            errors.append(
                {
                    "source": filename or "(unnamed file)",
                    "error": SKILL_IMPORT_INTERNAL_ERROR,
                }
            )

    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="import_admin_skill_files",
        details={
            "uploaded_file_count": len(files),
            "created_count": len(created),
            "error_count": len(errors),
            "source_types": sorted(source_types),
        },
        ip_address=get_audit_request_ip(request, db),
    )
    return {"created": created, "errors": errors}


@skills_router.get("/admin/{skill_id}", response_model=SkillResponse)
async def get_admin_skill_detail(
    request: Request,
    skill_id: str,
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
):
    """Return the complete data for one managed skill opened by an admin."""
    skill = get_admin_skill(db, skill_id)
    return _serialize_admin_skill_detail(skill)


@skills_router.post("/admin", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_admin_skill_endpoint(
    request: Request,
    skill_data: SkillCreate,
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Create a new managed skill."""
    try:
        skill = create_admin_skill_db(
            db=db,
            name=skill_data.name,
            description=skill_data.description,
            content=skill_data.content,
            icon=skill_data.icon,
        )
        write_skill_markdown_file(
            user_id=ADMIN_SKILLS_USER_ID,
            skill_id=skill.id,
            name=skill_data.name,
            description=skill_data.description,
            content=skill_data.content,
            license_value=skill_data.license,
            compatibility=skill_data.compatibility,
            metadata=skill_data.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="create_admin_skill",
        details={"skill_id": skill.id, "title": skill.name},
        ip_address=get_audit_request_ip(request, db),
    )
    return _serialize_admin_skill_detail(skill)


@skills_router.patch("/admin/{skill_id}", response_model=SkillResponse)
async def update_admin_skill_endpoint(
    request: Request,
    skill_id: str,
    skill_data: SkillUpdate,
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Update an existing managed skill."""
    skill = update_admin_skill(
        db=db,
        skill_id=skill_id,
        title=skill_data.title,
        description=skill_data.description,
        content=skill_data.content,
        icon=skill_data.icon,
    )
    markdown_fields = load_skill_markdown_fields(ADMIN_SKILLS_USER_ID, skill_id)
    write_skill_markdown_file(
        user_id=ADMIN_SKILLS_USER_ID,
        skill_id=skill_id,
        name=skill.name,
        description=(
            skill_data.description
            if skill_data.description is not None
            else (markdown_fields.get("description") or skill.description)
        ),
        content=skill.content,
        license_value=skill_data.license or markdown_fields.get("license"),
        compatibility=skill_data.compatibility or markdown_fields.get("compatibility"),
        metadata=skill_data.metadata or markdown_fields.get("metadata"),
    )
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="update_admin_skill",
        details={"skill_id": skill.id, "title": skill.name},
        ip_address=get_audit_request_ip(request, db),
    )
    return _serialize_admin_skill_detail(skill)


@skills_router.delete("/admin/{skill_id}")
async def delete_admin_skill_endpoint(
    request: Request,
    skill_id: str,
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Delete a managed skill."""
    result = delete_admin_skill(db=db, skill_id=skill_id)
    try:
        remove_skill_from_automations(db=db, user_id=None, skill_id=skill_id)
    except Exception as exc:
        logger.exception(
            "[Skills] Failed to remove admin skill reference from automations",
            extra={
                "event": "admin_skill_automation_cleanup_failed",
                "user_id": admin.id,
                "skill_id": skill_id,
                "error": str(exc),
            },
        )
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="delete_admin_skill",
        details={"skill_id": skill_id},
        ip_address=get_audit_request_ip(request, db),
    )
    return result


# ============================================================================
# Admin Skills File Management
# ============================================================================

@skills_router.get("/admin/{skill_id}/files/{folder_type}")
async def list_admin_skill_files_endpoint(
    request: Request,
    skill_id: str,
    folder_type: str,
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
):
    """List files in a managed skill's folder."""
    get_admin_skill(db, skill_id)
    try:
        files = list_skill_files(ADMIN_SKILLS_USER_ID, skill_id, folder_type)
        return {"files": files}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@skills_router.post("/admin/{skill_id}/files/{folder_type}")
async def upload_admin_skill_files_endpoint(
    request: Request,
    skill_id: str,
    folder_type: str,
    files: List[UploadFile] = File(...),
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Upload files to a managed skill's folder."""
    get_admin_skill(db, skill_id)
    
    uploaded = []
    errors = []
    
    for file in files:
        try:
            result = await _store_validated_admin_skill_upload(
                file,
                skill_id=skill_id,
                folder_type=folder_type,
            )
            uploaded.append(result)
        except HTTPException as exc:
            errors.append({"filename": file.filename, "error": str(exc.detail)})
        except ValueError as exc:
            errors.append({"filename": file.filename, "error": str(exc)})
        except Exception:
            logger.exception(
                "[Skills] Admin skill file upload failed",
                extra={
                    "event": "admin_skill_file_upload_failed",
                    "admin_id": admin.id,
                    "skill_id": skill_id,
                    "folder_type": folder_type,
                    # Match the regular skill-upload log context while avoiding
                    # Python logging's reserved ``filename`` attribute.
                    "uploaded_filename": getattr(file, "filename", None),
                },
            )
            errors.append({"filename": file.filename, "error": "Upload failed due to internal error"})
    
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="upload_admin_skill_files",
        details={
            "skill_id": skill_id,
            "folder_type": folder_type,
            "uploaded_count": len(uploaded),
            "error_count": len(errors),
        },
        ip_address=get_audit_request_ip(request, db),
    )
    
    return {"uploaded": uploaded, "errors": errors}


@skills_router.delete("/admin/{skill_id}/files/{folder_type}/{filename}")
async def delete_admin_skill_file_endpoint(
    request: Request,
    skill_id: str,
    folder_type: str,
    filename: str,
    admin=Depends(verified_admin),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Delete a file from a managed skill's folder."""
    get_admin_skill(db, skill_id)
    try:
        delete_skill_file(ADMIN_SKILLS_USER_ID, skill_id, folder_type, filename)
        create_audit_log(
            db_log=db_log,
            user_id=admin.id,
            action="delete_admin_skill_file",
            details={
                "skill_id": skill_id,
                "folder_type": folder_type,
                "filename": filename,
            },
            ip_address=get_audit_request_ip(request, db),
        )
        return {"deleted": True, "filename": filename}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
