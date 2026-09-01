from fastapi import APIRouter, Depends, UploadFile, File, Query, Form, Body, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import cast, func, literal, or_, String
from sqlalchemy.orm import Session
from typing import Any, List
from pathlib import Path
import os
import tempfile
import json
import re
import logging
import inspect

from app.dependencies import get_db, get_db_log, verified_user, verified_admin
from app.users.roles import is_admin_role
from app.files.models import (
    AccessDeniedError,
    list_project_files,
    Files,
    FileArtifactShare,
    get_file,
)
from app.files.access import (
    accessible_files_query,
    get_accessible_file,
    resolve_file_for_edit,
    resolve_file_for_read,
    valid_shared_folder_subscription_filter,
)
from app.files.canvas_assets import (
    CanvasAssetAccessError,
    decide_canvas_asset_reference,
    get_canvas_source_for_artifact,
    is_canvas_artifact_dependency_snapshot_current,
    notify_canvas_asset_approval_requests,
    request_public_canvas_asset_access,
    resolve_canvas_asset_for_read,
)
from app.files.schemas import (
    ArtifactShareAccessRequest,
    ArtifactShareAccessResponse,
    ArtifactShareCreateRequest,
    ArtifactShareCreateResponse,
    CanvasMarkdownPdfRequest,
    CanvasLatexRenderRequest,
    CanvasLatexRenderResponse,
    CanvasFileSaveRequest,
    CanvasFileSaveResponse,
    CanvasAssetDecisionRequest,
    CanvasAssetDecisionResponse,
    CanvasSpreadsheetSaveResponse,
    ArtifactShareDeleteRequest,
    ArtifactShareDeleteResponse,
    ArtifactSharePasswordChangeRequest,
    ArtifactShareExpiryChangeRequest,
    ArtifactShareExpiryRemoveRequest,
    ArtifactShareExpiryResponse,
    ArtifactSharePasswordRemoveRequest,
    ArtifactSharePasswordResponse,
    FilesWorkspaceCounts,
    FilesWorkspaceResponse,
    GoogleDriveImportRequest,
    GoogleDriveImportResponse,
    GoogleDrivePickerSessionResponse,
    ArtifactShareStatusResponse,
    FileDeleteTimeOption,
    FileList,
    FileStorageUsageResponse,
    FileRenameRequest,
    PdfPreviewDocumentResponse,
    PdfPreviewPageResponse,
    minimize_shared_file_response,
)
from app.files.sharing import (
    change_artifact_share_password,
    change_artifact_share_expiry,
    create_artifact_share,
    delete_artifact_share,
    enforce_shared_artifact_access_rate_limit,
    get_artifact_share_status,
    remove_artifact_share_expiry,
    remove_artifact_share_password,
    resolve_shared_artifact_access,
)
from app.files.pdf_preview import (
    PDF_PREVIEW_MAX_PAGES,
    PdfPreviewError,
    extract_pdf_preview_page,
    inspect_pdf_preview_document,
    render_pdf_preview_page_png,
    resolve_pdf_preview_record,
    resolve_pdf_preview_path,
)
from app.files.html_preview import get_canvas_html_preview_proxy_payload
from app.files.statistics import get_user_file_storage_usage
from app.files.utils import (
    upload_file_off_event_loop as upload_file,
    download_file,
    delete_file,
    delete_all_files,
    delete_websearch_files,
    ensure_user_file_upload_size_limit,
    rename_file,
    materialize_file_record,
    is_inline_unsafe_mime_type,
    SpreadsheetArchiveValidationError,
    validate_spreadsheet_archive,
)
from app.utils.attachments import attachment_headers
from app.workers.files import (
    enqueue_text_extraction_if_supported,
    external_file_processing_enabled,
    process_file_and_wait,
    resolve_cached_preview_path,
)
from app.files.google_drive import (
    complete_google_drive_oauth,
    import_google_drive_files_off_event_loop,
    get_google_drive_picker_session_payload,
)
from app.connections.models import (
    PROVIDER_GOOGLE_DRIVE,
    consume_connection_oauth_audit_subject,
    resolve_connection_oauth_audit_subject,
)
from app.file_folders.models import (
    FileFolders,
    SharedFileFolderSubscription,
    can_user_edit_folder,
)
from app.users.models import get_user
from app.groups.init import get_group_setting_value, update_group_settings
from app.groups.management import require_group_capability
from app.agents.utils import resolve_selected_model_for_user
from app.llm.utils import ensure_user_access_to_model
from app.logging.models import (
    create_audit_log,
    get_audit_request_ip,
    stage_audit_log_event,
)
from app.userNotifications.models import UserNotifications, create_user_notification
from app.settings.utils import get_public_url
from app.tools.canvas_markdown.utils import (
    CanvasSpreadsheetInputError,
    CanvasSpreadsheetRevisionConflict,
    save_canvas_markdown,
    save_canvas_spreadsheet_off_event_loop as save_canvas_spreadsheet,
)
from app.tools.canvas_markdown.pdf import render_canvas_markdown_pdf
from app.tools.latex_pdf.utils import (
    LatexCompileError,
    LatexRenderOutputLimitError,
    LatexSourceRevisionConflict,
    render_latex_canvas,
)
from app.utils.client_ip import extract_client_ip_from_request, resolve_trusted_proxy_networks
from app.utils.cache_headers import apply_no_store_headers
from app.utils.db import release_db_session_before_long_wait



files_router = APIRouter(prefix="/api/v1/files", tags=["files"])
logger = logging.getLogger(__name__)

_SPREADSHEET_PREVIEW_MAX_BYTES = 25 * 1024 * 1024
_GOOGLE_DRIVE_OAUTH_OUTCOME_AUDIT = {
    "provider_denied": ("CONNECTION_OAUTH_DENIED", "denied"),
    "missing_code": ("CONNECTION_OAUTH_FAILED", "failed"),
    "completion_failed": ("CONNECTION_OAUTH_FAILED", "failed"),
}


def _json_for_inline_script(value: Any) -> str:
    """Serialize JSON without allowing data to terminate a script element.

    JSON permits literal ``<``, ``>``, and ``&`` characters, but HTML parses a
    literal ``</script`` before JavaScript sees the string.  Unicode escapes
    preserve the exact JavaScript value while making provider-controlled OAuth
    errors and exception messages inert inside the callback document.
    """

    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("&", r"\u0026")
    )


FILES_LIST_DEFAULT_LIMIT = 200
FILES_LIST_MAX_LIMIT = 500
_SHARED_ARTIFACT_ANONYMOUS_AUDIT_USER_ID = "anonymous"


def _audit_file_event(
    db_log: Session,
    request: Request,
    user_id: str,
    action: str,
    details: dict | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )


def _consume_google_drive_oauth_audit_subject_best_effort(
    db: Session,
    *,
    state: str | None,
) -> dict[str, str] | None:
    try:
        return consume_connection_oauth_audit_subject(
            db,
            state=state,
            provider=PROVIDER_GOOGLE_DRIVE,
        )
    except Exception:
        # State cleanup must never replace the callback's safe popup response.
        logger.exception("Unable to clear Google Drive OAuth callback state")
        return None


def _audit_google_drive_oauth_outcome_best_effort(
    db_log: Session,
    request: Request,
    db: Session,
    subject: dict[str, str] | None,
    *,
    outcome: str,
) -> None:
    audit_event = _GOOGLE_DRIVE_OAUTH_OUTCOME_AUDIT.get(outcome)
    if subject is None or audit_event is None:
        return
    action, status = audit_event
    try:
        create_audit_log(
            db_log=db_log,
            user_id=subject["user_id"],
            action=action,
            details={
                "provider": subject["provider"],
                "status": status,
                "outcome": outcome,
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="connections",
        )
    except Exception:
        # Audit delivery is best effort on an already-failed OAuth callback.
        logger.exception("Unable to record Google Drive OAuth callback outcome")


def _get_user_display_name(user_obj):
    """Extract display name from a user object.
    
    Args:
        user_obj: A user model object with attributes like first_name, last_name, email.
        
    Returns:
        A string representation of the user's display name, or "Unknown" if not available.
    """
    if not user_obj:
        return "Unknown"
    first = getattr(user_obj, "first_name", None)
    last = getattr(user_obj, "last_name", None)
    if first or last:
        return " ".join(filter(None, [first, last])).strip()
    if getattr(user_obj, "email", None):
        return user_obj.email
    return "Unknown"


def _valid_shared_folder_subscription_filter():
    return valid_shared_folder_subscription_filter()


def _accessible_files_query(db: Session, user_id: str):
    return accessible_files_query(db, user_id)


def _file_meta_text_expr(key: str):
    meta_value = Files.meta[key]
    if hasattr(meta_value, "as_string"):
        return meta_value.as_string()
    return cast(meta_value, String)


def _workspace_name_expr():
    return func.lower(func.coalesce(_file_meta_text_expr("original_filename"), Files.file_name, Files.id))


def _like_contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _apply_workspace_filters(
    query,
    *,
    search: str | None = None,
    folder_id: str | None = None,
):
    active_folder_id = str(folder_id or "").strip()
    if active_folder_id and active_folder_id != "all":
        if active_folder_id == "uncategorized":
            query = query.filter(or_(Files.folder_id.is_(None), Files.folder_id == ""))
        else:
            query = query.filter(Files.folder_id == active_folder_id)

    normalized_query = re.sub(r"\s+", " ", str(search or "").strip().lower())
    if normalized_query:
        pattern = _like_contains_pattern(normalized_query)
        query = query.filter(
            or_(
                func.lower(func.coalesce(_file_meta_text_expr("original_filename"), literal(""))).like(pattern, escape="\\"),
                func.lower(func.coalesce(Files.file_name, literal(""))).like(pattern, escape="\\"),
                func.lower(func.coalesce(Files.id, literal(""))).like(pattern, escape="\\"),
                func.lower(func.coalesce(Files.file_type, literal(""))).like(pattern, escape="\\"),
                func.lower(func.coalesce(Files.file_category, literal(""))).like(pattern, escape="\\"),
            )
        )

    return query


def _apply_workspace_sort(query, sort_field: str, sort_direction: str):
    target_field = sort_field if sort_field in {"name", "size", "type", "category", "created_at", "timestamp"} else "name"
    descending = str(sort_direction or "").lower() == "desc"
    if target_field == "size":
        sort_expr = Files.file_size
    elif target_field in {"created_at", "timestamp"}:
        sort_expr = Files.created_at
    elif target_field == "type":
        sort_expr = func.lower(func.coalesce(Files.file_type, literal("")))
    elif target_field == "category":
        sort_expr = func.lower(func.coalesce(Files.file_category, literal("")))
    else:
        sort_expr = _workspace_name_expr()

    order = sort_expr.desc() if descending else sort_expr.asc()
    return query.order_by(order, _workspace_name_expr().asc(), Files.id.asc())


def _fetch_file_folders_by_id(db: Session, folder_ids: set[str]) -> dict[str, FileFolders]:
    if not folder_ids:
        return {}
    rows = db.query(FileFolders).filter(FileFolders.id.in_(sorted(folder_ids))).all()
    return {str(row.id): row for row in rows}


def _fetch_subscriptions_by_folder(
    db: Session,
    user_id: str,
    folder_ids: set[str],
) -> dict[str, tuple[FileFolders, SharedFileFolderSubscription]]:
    if not folder_ids:
        return {}
    rows = (
        db.query(FileFolders, SharedFileFolderSubscription)
        .join(SharedFileFolderSubscription, SharedFileFolderSubscription.folder_id == FileFolders.id)
        .filter(
            FileFolders.id.in_(sorted(folder_ids)),
            SharedFileFolderSubscription.subscriber_id == user_id,
            _valid_shared_folder_subscription_filter(),
        )
        .all()
    )
    return {str(folder.id): (folder, subscription) for folder, subscription in rows}


def _decorate_accessible_file_records(db: Session, user_id: str, file_records: list[Files]) -> list[FileList]:
    if not file_records:
        return []

    folder_ids = {
        str(file_record.folder_id)
        for file_record in file_records
        if str(getattr(file_record, "folder_id", "") or "").strip()
    }
    folders_by_id = _fetch_file_folders_by_id(db, folder_ids)
    owned_folders_by_id = {
        folder_id: folder
        for folder_id, folder in folders_by_id.items()
        if str(getattr(folder, "user_id", "")) == str(user_id)
    }
    subscribed_by_folder = _fetch_subscriptions_by_folder(db, user_id, folder_ids)
    display_name_cache: dict[str, str] = {}

    def display_name(target_user_id: str) -> str:
        normalized = str(target_user_id or "").strip()
        if not normalized:
            return "Unknown"
        if normalized not in display_name_cache:
            display_name_cache[normalized] = _get_user_display_name(get_user(db, normalized))
        return display_name_cache[normalized]

    current_user_name: str | None = None
    serialized_files: list[FileList] = []
    seen_ids: set[str] = set()

    for file_record in file_records:
        file_model = FileList.model_validate(file_record)
        file_id = str(file_model.file_id or "").strip()
        if not file_id or file_id in seen_ids:
            continue
        seen_ids.add(file_id)

        if str(getattr(file_record, "user_id", "")) == str(user_id):
            serialized_files.append(file_model)
            continue

        folder_id = str(getattr(file_record, "folder_id", "") or "").strip()
        if folder_id in owned_folders_by_id:
            folder = owned_folders_by_id.get(folder_id)
            if current_user_name is None:
                current_user_name = display_name(user_id)
            meta = dict(file_model.meta or {})
            meta.update({
                "shared": True,
                "shared_folder_id": folder_id,
                "shared_folder_name": folder.name if folder else None,
                "shared_owner_name": current_user_name,
                "shared_contributor_name": display_name(getattr(file_record, "user_id", "")),
            })
            file_model.meta = meta
            serialized_files.append(minimize_shared_file_response(file_model))
            continue

        folder_subscription = subscribed_by_folder.get(folder_id)
        if folder_subscription:
            folder, subscription = folder_subscription
            meta = dict(file_model.meta or {})
            meta.update({
                "shared": True,
                "shared_folder_id": folder_id,
                "shared_folder_name": folder.name,
                "shared_owner_name": display_name(getattr(folder, "user_id", "")),
                "shared_share_type": subscription.share_type,
            })
            file_model.meta = meta
            serialized_files.append(minimize_shared_file_response(file_model))

    return serialized_files


def _build_accessible_files_page_payloads(
    db: Session,
    user_id: str,
    *,
    limit: int | None,
    offset: int = 0,
) -> list[FileList]:
    rows = (
        _accessible_files_query(db, user_id)
        .order_by(Files.created_at.asc(), Files.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return _decorate_accessible_file_records(db, user_id, rows)


def _compute_workspace_counts_from_query(db: Session, user_id: str) -> FilesWorkspaceCounts:
    folder_counts: dict[str, int] = {}
    uncategorized = 0
    total = 0

    rows = (
        _accessible_files_query(db, user_id)
        .with_entities(Files.folder_id, func.count(Files.id))
        .group_by(Files.folder_id)
        .all()
    )
    for folder_id, count in rows:
        count_value = int(count or 0)
        total += count_value
        normalized_folder_id = str(folder_id or "").strip()
        if normalized_folder_id:
            folder_counts[normalized_folder_id] = count_value
        else:
            uncategorized += count_value

    return FilesWorkspaceCounts(all=total, uncategorized=uncategorized, folders=folder_counts)


def _list_workspace_file_payloads(
    db: Session,
    user_id: str,
    *,
    search: str | None,
    folder_id: str | None,
    sort_field: str,
    sort_direction: str,
    limit: int,
    offset: int,
) -> tuple[list[FileList], int]:
    query = _apply_workspace_filters(
        _accessible_files_query(db, user_id),
        search=search,
        folder_id=folder_id,
    )
    total = query.order_by(None).count()
    rows = (
        _apply_workspace_sort(query, sort_field, sort_direction)
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = _decorate_accessible_file_records(db, user_id, rows)
    return items, total


def _shared_artifact_audit_user_id(audit_subject: dict) -> str:
    owner_user_id = str(audit_subject.get("owner_user_id") or "").strip()
    return owner_user_id or _SHARED_ARTIFACT_ANONYMOUS_AUDIT_USER_ID


def _shared_artifact_audit_subject(db: Session, share_id: str | None) -> dict:
    cleaned_share_id = str(share_id or "").strip()
    if not cleaned_share_id:
        return {}
    row = (
        db.query(FileArtifactShare, Files)
        .join(Files, FileArtifactShare.file_id == Files.id)
        .filter(FileArtifactShare.id == cleaned_share_id)
        .first()
    )
    if not row:
        return {"share_id": cleaned_share_id}
    share, file_record = row
    return {
        "share_id": cleaned_share_id,
        "file_id": file_record.id,
        "owner_user_id": share.user_id,
        "has_password": bool(share.password_hash),
    }


def _resolve_download_audit_subject(db: Session, actor_user_id: str, file_id: str) -> dict:
    file_record = get_accessible_file(db, actor_user_id, file_id)
    if not file_record:
        return {
            "actor_user_id": actor_user_id,
            "file_id": file_id,
        }
    accessed_via_shared_folder = str(file_record.user_id) != str(actor_user_id)

    folder_record = None
    if file_record.folder_id:
        folder_record = db.query(FileFolders).filter(FileFolders.id == file_record.folder_id).first()

    return {
        "actor_user_id": actor_user_id,
        "owner_user_id": file_record.user_id,
        "file_id": file_record.id,
        "folder_id": file_record.folder_id,
        "folder_owner_user_id": folder_record.user_id if folder_record else None,
        "project_id": file_record.project_id,
        "access_via_shared_folder": accessed_via_shared_folder,
    }


# -------------------
# List Files
# -------------------
@files_router.get("/", response_model=list[FileList])
@files_router.get("", response_model=list[FileList])
def get_files_route(
    limit: int | None = Query(FILES_LIST_DEFAULT_LIMIT, ge=1, le=FILES_LIST_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    user = Depends(verified_user),
    db: Session = Depends(get_db),
):
    effective_limit = FILES_LIST_DEFAULT_LIMIT if limit is None else limit
    return _build_accessible_files_page_payloads(db, user.id, limit=effective_limit, offset=offset)


@files_router.get("/workspace", response_model=FilesWorkspaceResponse)
def get_workspace_files_route(
    search: str | None = Query(None),
    folder_id: str | None = Query(None),
    sort_field: str = Query("name"),
    sort_direction: str = Query("asc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    counts = _compute_workspace_counts_from_query(db, user.id)
    items, total = _list_workspace_file_payloads(
        db,
        user.id,
        search=search if isinstance(search, str) else None,
        folder_id=folder_id if isinstance(folder_id, str) else None,
        sort_field=sort_field,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )

    return FilesWorkspaceResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
        counts=counts,
    )


@files_router.get("/storage/usage", response_model=FileStorageUsageResponse)
def get_file_storage_usage_route(
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Return the current user's owned file-storage usage and limits."""
    return get_user_file_storage_usage(db, user.id)


@files_router.post("/google-drive/picker-session", response_model=GoogleDrivePickerSessionResponse)
def create_google_drive_picker_session_route(
    request: Request,
    response: Response,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Return ephemeral credentials used only to render the official Picker."""

    payload = get_google_drive_picker_session_payload(db, user_id=user.id)
    # OAuth access tokens must never be cached by the browser, reverse proxy, or
    # any intermediary. The frontend also keeps this value only in local scope.
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    _audit_file_event(
        db_log,
        request,
        user.id,
        "GOOGLE_DRIVE_PICKER_SESSION_CREATED",
        {
            "picker_ready": bool(payload.get("picker_ready")),
            "connected": bool(payload.get("connected")),
        },
    )
    return payload


@files_router.get("/google-drive/oauth/callback", response_class=HTMLResponse)
def complete_google_drive_oauth_route(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    popup_payload: dict[str, str] = {
        "type": "omlorix-google-drive-auth",
        "status": "error",
        "error": "",
    }
    message = "Google Drive connection failed. You can close this window."
    audit_subject = None
    oauth_completion_attempted = False
    oauth_completion_succeeded = False

    try:
        if error:
            audit_subject = _consume_google_drive_oauth_audit_subject_best_effort(
                db,
                state=state,
            )
            _audit_google_drive_oauth_outcome_best_effort(
                db_log,
                request,
                db,
                audit_subject,
                outcome="provider_denied",
            )
            popup_payload["error"] = error
            message = f"{error}. You can close this window."
        elif not code or not state:
            audit_subject = _consume_google_drive_oauth_audit_subject_best_effort(
                db,
                state=state,
            )
            _audit_google_drive_oauth_outcome_best_effort(
                db_log,
                request,
                db,
                audit_subject,
                outcome="missing_code",
            )
            popup_payload["error"] = "Missing callback parameters."
            message = "Missing callback parameters. You can close this window."
        else:
            oauth_completion_attempted = True
            audit_subject = resolve_connection_oauth_audit_subject(
                db,
                state=state,
                provider=PROVIDER_GOOGLE_DRIVE,
            )
            audit_ip_address = get_audit_request_ip(request, db)
            audit_user_agent = request.headers.get("user-agent")

            def stage_connection_audit(connection, connection_change: str) -> None:
                stage_audit_log_event(
                    db,
                    user_id=str(connection.user_id),
                    action="CONNECTION_OAUTH_COMPLETED",
                    details={
                        "connection_id": str(connection.id),
                        "provider": PROVIDER_GOOGLE_DRIVE,
                        "status": "connected",
                        "connection_change": connection_change,
                    },
                    ip_address=audit_ip_address,
                    user_agent=audit_user_agent,
                    category="connections",
                )

            complete_google_drive_oauth(
                db,
                state=state,
                code=code,
                before_connection_commit=stage_connection_audit,
            )
            oauth_completion_succeeded = True
            popup_payload = {
                "type": "omlorix-google-drive-auth",
                "status": "connected",
                "error": "",
            }
            message = "Google Drive connected. You can close this window."
    except HTTPException as exc:
        if oauth_completion_attempted and not oauth_completion_succeeded:
            cleared_subject = _consume_google_drive_oauth_audit_subject_best_effort(
                db,
                state=state,
            )
            _audit_google_drive_oauth_outcome_best_effort(
                db_log,
                request,
                db,
                audit_subject or cleared_subject,
                outcome="completion_failed",
            )
        popup_payload["error"] = str(exc.detail)
        message = f"{exc.detail}. You can close this window."
    except Exception:
        if oauth_completion_attempted and not oauth_completion_succeeded:
            cleared_subject = _consume_google_drive_oauth_audit_subject_best_effort(
                db,
                state=state,
            )
            _audit_google_drive_oauth_outcome_best_effort(
                db_log,
                request,
                db,
                audit_subject or cleared_subject,
                outcome="completion_failed",
            )
        logger.exception("Google Drive OAuth callback failed")
        popup_payload["error"] = "Google Drive connection failed."
        message = "Google Drive connection failed. You can close this window."

    safe_payload = _json_for_inline_script(popup_payload)
    safe_origin = _json_for_inline_script(get_public_url(db).rstrip("/"))
    safe_message = _json_for_inline_script(message)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Google Drive</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background: #0b1220;
      color: #f8fafc;
      font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .card {{
      width: min(440px, 100%);
      padding: 24px;
      border-radius: 18px;
      background: rgba(15, 23, 42, 0.96);
      border: 1px solid rgba(148, 163, 184, 0.18);
      box-shadow: 0 24px 48px rgba(0, 0, 0, 0.35);
    }}
    p {{
      margin: 0;
    }}
  </style>
</head>
<body>
  <div class="card">
    <p id="status"></p>
  </div>
  <script>
    (() => {{
      const payload = {safe_payload};
      const targetOrigin = {safe_origin};
      const message = {safe_message};
      const statusEl = document.getElementById('status');
      if (statusEl) {{
        statusEl.textContent = message;
      }}
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage(payload, targetOrigin);
        }}
      }} catch (_) {{}}
      window.setTimeout(() => window.close(), 250);
    }})();
  </script>
</body>
</html>"""
    )


@files_router.post("/google-drive/import", response_model=GoogleDriveImportResponse)
async def import_google_drive_files_route(
    payload: GoogleDriveImportRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    from app.workers.ingestion import (
        enqueue_google_drive_import_async,
        external_ingestion_enabled,
        wait_for_ingestion_job_async,
    )

    user_id = str(user.id)
    use_external_worker = external_ingestion_enabled()
    if use_external_worker:
        from app.workers.models import WorkerJobFailed

        audit_ip_address = get_audit_request_ip(request, db)
        release_db_session_before_long_wait(db)
        job = await enqueue_google_drive_import_async(
            user_id=user_id,
            file_ids=payload.file_ids,
            audit_ip_address=audit_ip_address,
            audit_user_agent=request.headers.get("user-agent"),
        )
        try:
            result = await wait_for_ingestion_job_async(job)
        except TimeoutError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "ingestion_still_processing", "job_id": job.id},
                headers={"Retry-After": "3"},
            ) from exc
        except WorkerJobFailed as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "job_id": job.id},
            ) from exc
    else:
        release_db_session_before_long_wait(db)
        result = await import_google_drive_files_off_event_loop(
            user_id=user_id,
            file_ids=payload.file_ids,
        )
    if not use_external_worker:
        _audit_file_event(
            db_log,
            request,
            user_id,
            "GOOGLE_DRIVE_FILES_IMPORTED",
            {"requested_file_count": len(payload.file_ids or [])},
        )
    return result


# -------------------
# List Project Files
# -------------------
@files_router.get("/project", response_model=list[FileList])
def get_project_files_route(project_id: str, db: Session = Depends(get_db), user = Depends(verified_user)):
    try:
        files = list_project_files(db, user.id, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return [
        FileList.model_validate(file)
        if str(getattr(file, "user_id", "")) == str(user.id)
        else minimize_shared_file_response(FileList.model_validate(file))
        for file in files
    ]




# -------------------
# Upload File
# -------------------
@files_router.post('/upload')
async def upload_file_route(
    request: Request,
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    folder_id: str | None = Form(None),
    group_context_id: str | None = Form(None),
    model_id: str | None = Form(None),
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    user_id = str(user.id)
    upload_file_obj = getattr(file, "file", None)
    if upload_file_obj is None:
        raise HTTPException(status_code=400, detail="Invalid upload payload")

    current_position = upload_file_obj.tell()
    upload_file_obj.seek(0, 2)
    file_size = upload_file_obj.tell()
    upload_file_obj.seek(current_position)

    ensure_user_file_upload_size_limit(db, user_id, file_size)
    if model_id:
        resolved_selection = resolve_selected_model_for_user(
            db,
            user_id=user_id,
            model_id=model_id,
        )
        ensure_user_access_to_model(user_id, resolved_selection.selected_model_id, db)
        if resolved_selection.base_model.id != resolved_selection.selected_model_id:
            ensure_user_access_to_model(user_id, resolved_selection.base_model.id, db)

    if folder_id and not can_user_edit_folder(db, user_id, folder_id):
        raise HTTPException(status_code=403, detail="You do not have access to this folder")

    group_context_existing_ids: list[str] | None = None
    if group_context_id:
        # Group-context authorization follows delegated hierarchy permissions,
        # not the uploader's own membership. This lets owners and managers
        # maintain context for child groups without joining each child first.
        require_group_capability(db, user, group_context_id, "manage_settings")

        existing_ids = get_group_setting_value(
            group_context_id,
            "context",
            "group_context_file_ids",
            db,
        )
        group_context_existing_ids = existing_ids if isinstance(existing_ids, list) else []

    release_db_session_before_long_wait(db)
    result = await upload_file(
        file,
        project_id,
        user_id,
        db,
        folder_id=folder_id,
    )

    if (
        group_context_id
        and result.get("status") == "success"
        and result.get("file_id")
    ):
        file_id_str = str(result["file_id"])
        existing_ids = list(group_context_existing_ids or [])
        try:
            if file_id_str not in existing_ids:
                update_group_settings(
                    group_context_id,
                    "context",
                    "group_context_file_ids",
                    existing_ids + [file_id_str],
                    db,
                )
        except Exception:
            try:
                delete_file(user_id, file_id_str, db, FileDeleteTimeOption.ALL)
            except Exception:
                pass
            raise

    if result.get("status") == "success":
        if result.get("file_id"):
            try:
                enqueue_text_extraction_if_supported(
                    db,
                    user_id=user_id,
                    file_id=str(result["file_id"]),
                )
                if external_file_processing_enabled():
                    result["processing_status"] = "queued"
            except Exception:
                # The uploaded source remains authoritative and extraction is
                # also enqueued lazily on first use. Do not roll back a safely
                # stored user file because an optional cache queue is down.
                logger.exception(
                    "[Files] Failed to pre-enqueue uploaded file processing",
                    extra={"event": "file_processing_enqueue_failed", "user_id": user_id},
                )
                result["processing_status"] = "deferred"
        _audit_file_event(
            db_log,
            request,
            user_id,
            "FILE_UPLOADED",
            {
                "file_id": result.get("file_id"),
                "filename": file.filename,
                "content_type": file.content_type,
                "file_size": file_size,
                "project_id": project_id,
                "folder_id": folder_id,
                "group_context_updated": bool(group_context_id),
            },
        )
    return result
    


# -------------------
# Download File
# -------------------
@files_router.get('/download')
def download_file_route(
    request: Request,
    file_id: str,
    inline: bool = Query(False),
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    response = download_file(user.id, file_id, db, inline=inline)
    audit_details = _resolve_download_audit_subject(db, user.id, file_id)
    audit_details["inline"] = inline
    _audit_file_event(
        db_log,
        request,
        user.id,
        "FILE_DOWNLOADED",
        audit_details,
    )
    return response


def _raise_pdf_preview_http_error(exc: PdfPreviewError) -> None:
    """Keep parser and renderer internals out of authenticated API responses."""
    raise HTTPException(status_code=422, detail="PDF preview is unavailable") from exc


@files_router.get('/pdf/preview', response_model=PdfPreviewDocumentResponse)
def get_pdf_preview_document_route(
    request: Request,
    response: Response,
    file_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Return page metadata for an accessible app-rendered PDF preview."""
    try:
        if external_file_processing_enabled():
            file_record, _owner_user_id = resolve_pdf_preview_record(
                db,
                user_id=str(user.id),
                file_id=file_id,
            )
            artifact = process_file_and_wait(
                db,
                user_id=str(user.id),
                file_id=file_id,
                operation="pdf_inspect",
            )
            if artifact.status != "succeeded" or not isinstance(artifact.data, dict):
                raise PdfPreviewError("PDF preview processing failed.")
            payload = artifact.data
        else:
            file_record, file_path = resolve_pdf_preview_path(
                db,
                user_id=str(user.id),
                file_id=file_id,
            )
            payload = inspect_pdf_preview_document(file_path)
    except PdfPreviewError as exc:
        _raise_pdf_preview_http_error(exc)

    apply_no_store_headers(response)
    _audit_file_event(
        db_log,
        request,
        user.id,
        "FILE_PREVIEWED",
        {
            "file_id": str(file_record.id),
            "content_type": "application/pdf",
            "page_count": payload["page_count"],
        },
    )
    return payload


@files_router.get('/pdf/preview/page', response_model=PdfPreviewPageResponse)
def get_pdf_preview_page_route(
    response: Response,
    file_id: str,
    page: int = Query(..., ge=1, le=PDF_PREVIEW_MAX_PAGES),
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Return selectable text geometry for one accessible PDF page."""
    try:
        if external_file_processing_enabled():
            resolve_pdf_preview_record(db, user_id=str(user.id), file_id=file_id)
            artifact = process_file_and_wait(
                db,
                user_id=str(user.id),
                file_id=file_id,
                operation="pdf_page",
                params={"page": page},
            )
            if artifact.status != "succeeded" or not isinstance(artifact.data, dict):
                raise PdfPreviewError("PDF preview processing failed.")
            payload = artifact.data
        else:
            _file_record, file_path = resolve_pdf_preview_path(
                db,
                user_id=str(user.id),
                file_id=file_id,
            )
            payload = extract_pdf_preview_page(file_path, page)
    except PdfPreviewError as exc:
        _raise_pdf_preview_http_error(exc)
    apply_no_store_headers(response)
    return payload


@files_router.get('/pdf/preview/page-image')
def get_pdf_preview_page_image_route(
    file_id: str,
    page: int = Query(..., ge=1, le=PDF_PREVIEW_MAX_PAGES),
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Return one inert page image for an accessible PDF preview."""
    try:
        if external_file_processing_enabled():
            resolve_pdf_preview_record(db, user_id=str(user.id), file_id=file_id)
            artifact = process_file_and_wait(
                db,
                user_id=str(user.id),
                file_id=file_id,
                operation="pdf_page_image",
                params={"page": page},
            )
            if artifact.status != "succeeded" or not artifact.cache_path:
                raise PdfPreviewError("PDF preview processing failed.")
            try:
                png_bytes = resolve_cached_preview_path(artifact.cache_path).read_bytes()
            except (OSError, HTTPException) as exc:
                raise PdfPreviewError("PDF preview cache is unavailable.") from exc
        else:
            _file_record, file_path = resolve_pdf_preview_path(
                db,
                user_id=str(user.id),
                file_id=file_id,
            )
            png_bytes = render_pdf_preview_page_png(file_path, page)
    except PdfPreviewError as exc:
        _raise_pdf_preview_http_error(exc)
    response = Response(content=png_bytes, media_type="image/png")
    apply_no_store_headers(response)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response



# -------------------
# Delete File
# -------------------
@files_router.get('/canvas/html-preview-proxy', response_class=HTMLResponse)
def get_canvas_html_preview_proxy_route():
    """Serve the trusted outer frame used by interactive Canvas HTML previews.

    The route is intentionally available to both authenticated Omlorix pages
    and public artifact-share pages.  Its HTTP security headers only permit it
    to be framed by this Omlorix origin, and it never reads files or user data.
    """
    payload = get_canvas_html_preview_proxy_payload()
    return HTMLResponse(
        content=str(payload["html"]),
        headers=dict(payload["headers"]),
    )


@files_router.delete('/')
@files_router.delete('')
def delete_file_route(
    request: Request,
    file_id: str | None = None,
    time: FileDeleteTimeOption = Query(FileDeleteTimeOption.ALL),
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    result = delete_file(user.id, file_id, db, time)

    errors = result.get("errors") or []
    audit_action = "FILE_DELETED" if not errors else "FILE_DELETE_FAILED"
    _audit_file_event(
        db_log,
        request,
        user.id,
        audit_action,
        {
            "file_id": file_id,
            "time": getattr(time, "value", str(time)),
            "deleted_count": result.get("deleted_count", 0),
            "error_count": len(errors),
            "errors": errors,
        },
    )
    if errors:
        return JSONResponse(status_code=500, content=result)
    return result


# -------------------
# Delete All Files
# -------------------
@files_router.delete('/all')
def delete_all_files_route(
    request: Request,
    delete_all: bool = Query(True, description="Delete all files when true; delete only websearch files when false."),
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    if delete_all:
        result = delete_all_files(user.id, db)
        action = "ALL_FILES_DELETED"
    else:
        result = delete_websearch_files(user.id, db)
        action = "WEBSEARCH_FILES_DELETED"

    errors = result.get("errors") or []
    audit_action = action
    if errors:
        audit_action = "ALL_FILES_DELETE_FAILED" if delete_all else "WEBSEARCH_FILES_DELETE_FAILED"

    _audit_file_event(
        db_log,
        request,
        user.id,
        audit_action,
        {
            "delete_all": delete_all,
            "deleted_count": result.get("deleted_count", 0),
            "error_count": len(errors),
            "errors": errors,
        },
    )
    if errors:
        return JSONResponse(status_code=500, content=result)
    return result


# -------------------
# Rename File
# -------------------
@files_router.post('/rename', response_model=FileList)
def rename_file_route(
    payload: FileRenameRequest,
    request: Request,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    updated_file = rename_file(user.id, payload.file_id, payload.original_filename, db)
    _audit_file_event(
        db_log,
        request,
        user.id,
        "FILE_RENAMED",
        {"file_id": payload.file_id, "filename": payload.original_filename},
    )
    return FileList.model_validate(updated_file)


@files_router.post('/canvas/save', response_model=CanvasFileSaveResponse)
def save_canvas_file_route(
    payload: CanvasFileSaveRequest,
    request: Request,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
):
    resolved_source = resolve_file_for_edit(db, str(user.id), str(payload.file_id))
    if not resolved_source:
        raise HTTPException(status_code=404, detail="File not found")
    target_user_id = resolved_source.storage_owner_user_id
    audit_ip_address = get_audit_request_ip(request, db)
    audit_user_agent = request.headers.get("user-agent")

    def stage_canvas_audit(snapshot: dict) -> None:
        stage_audit_log_event(
            db,
            user_id=str(user.id),
            action="CANVAS_EDITED",
            details={
                **snapshot,
                "is_collaborator": str(target_user_id) != str(user.id),
            },
            ip_address=audit_ip_address,
            user_agent=audit_user_agent,
            category="files",
        )

    try:
        result = save_canvas_markdown(
            db=db,
            user_id=str(target_user_id),
            content=str(payload.content or ""),
            content_type=str(payload.content_type or "markdown"),
            filename=payload.filename,
            file_id=payload.file_id,
            project_id=None,
            edit_source="user",
            edited_by=str(user.id),
            file_ids=payload.file_ids,
            # Active HTML remains attachment-only at rest.  In-app and public
            # execution is delegated to the nested opaque-origin preview host.
            allow_html_attachment=True,
            # A user save is the authoritative full dependency declaration.
            # Force an empty source to revoke grants even if its metadata mirror
            # was damaged or removed independently.
            force_canvas_asset_reconciliation=True,
            before_commit=stage_canvas_audit,
        )
    except HTTPException:
        raise
    except CanvasAssetAccessError as exc:
        # A generic stable code avoids revealing whether an attacker-supplied
        # UUID exists while still allowing every frontend locale to explain
        # the failure.
        raise HTTPException(status_code=403, detail=exc.code) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to save canvas file") from exc

    return CanvasFileSaveResponse(
        file_id=str(result.get("file_id") or payload.file_id),
        file_name=str(result.get("file_name") or payload.filename or "canvas.md"),
        content=str(result.get("content") or ""),
        content_type=str(result.get("content_type") or payload.content_type or "markdown"),
        page_count=int(result.get("page_count") or 1),
        created=bool(result.get("created")),
        canvas_revision=result.get("canvas_revision"),
        pdf_file_id=str(result.get("pdf_file_id") or ""),
        pdf_file_name=str(result.get("pdf_file_name") or ""),
        asset_file_ids=list(result.get("asset_file_ids") or []),
        render_revision=result.get("render_revision"),
        render_status=str(result.get("render_status") or ""),
        pending_asset_approval_count=int(
            result.get("pending_asset_approval_count") or 0
        ),
    )


@files_router.post(
    "/canvas/assets/decision",
    response_model=CanvasAssetDecisionResponse,
)
def decide_canvas_asset_route(
    payload: CanvasAssetDecisionRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Let the real asset owner approve or reject one scoped reference."""

    approve = payload.decision == "approve"
    try:
        canvas, reference = decide_canvas_asset_reference(
            db,
            canvas_file_id=payload.canvas_file_id,
            request_id=payload.request_id,
            asset_owner_user_id=str(user.id),
            approve=approve,
            public=payload.scope == "public",
        )
    except CanvasAssetAccessError as exc:
        # The same generic response covers stale, forged, and foreign requests.
        raise HTTPException(status_code=403, detail=exc.code) from exc

    # The decision payload comes from one actionable notification, so require
    # its stable ID and query that row directly. This avoids an unbounded JSON
    # scan and cannot miss a match hidden beyond an arbitrary result limit.
    notifications = (
        db.query(UserNotifications)
        .filter(
            UserNotifications.id == str(payload.notification_id),
            UserNotifications.category == "canvas_assets",
            UserNotifications.user_ids.like(f"%|{user.id}|%"),
        )
        .all()
    )
    for notification in notifications:
        details = notification.details if isinstance(notification.details, dict) else {}
        if (
            str(user.id) in notification.user_id_list()
            and str(details.get("request_id") or "") == str(payload.request_id)
        ):
            db.delete(notification)
    db.commit()

    requester_id = str(reference.get("added_by_user_id") or "").strip()
    if requester_id and requester_id != str(user.id):
        canvas_meta = canvas.meta if isinstance(canvas.meta, dict) else {}
        canvas_name = str(
            canvas_meta.get("original_filename") or canvas.file_name or "Canvas"
        )[:255]
        asset_name = str(reference.get("asset_name") or "The file")[:255]
        try:
            # This shared helper adds, commits, and refreshes the notification
            # before returning, so the request-scoped session cannot discard it.
            create_user_notification(
                db,
                message=(
                    f"{asset_name} was {'approved' if approve else 'rejected'} "
                    f"for {canvas_name}."
                ),
                category="canvas_assets",
                notification_type="info" if approve else "warning",
                user_ids=[requester_id],
                details={
                    "type": "canvas_asset_decision",
                    "scope": payload.scope,
                    "canvas_title": canvas_name,
                    "asset_name": asset_name,
                    "decision": payload.decision,
                },
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to notify requester about Canvas asset decision")

    _audit_file_event(
        db_log,
        request,
        str(user.id),
        "CANVAS_ASSET_ACCESS_DECIDED",
        {
            "canvas_file_id": str(canvas.id),
            "asset_file_id": str(reference["file_id"]),
            "decision": payload.decision,
            "scope": payload.scope,
        },
    )
    return CanvasAssetDecisionResponse(
        canvas_file_id=str(canvas.id),
        asset_file_id=str(reference["file_id"]),
        status="active" if approve else "rejected",
        scope=payload.scope,
    )


@files_router.get("/canvas/assets/content")
def get_canvas_asset_content_route(
    request: Request,
    canvas_file_id: str = Query(..., min_length=1, max_length=128),
    asset_file_id: str = Query(..., min_length=1, max_length=128),
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Serve an asset through its Canvas grant without granting global access."""

    canvas_access = resolve_file_for_read(db, str(user.id), canvas_file_id)
    if not canvas_access:
        raise HTTPException(status_code=404, detail="Canvas asset not found")
    try:
        asset_access = resolve_canvas_asset_for_read(
            db,
            canvas_record=canvas_access.record,
            actor_user_id=str(user.id),
            asset_file_id=asset_file_id,
        )
    except CanvasAssetAccessError as exc:
        raise HTTPException(status_code=404, detail="Canvas asset not found") from exc

    response = download_file(
        asset_access.storage_owner_user_id,
        str(asset_access.record.id),
        db,
        inline=True,
    )
    _audit_file_event(
        db_log,
        request,
        str(user.id),
        "CANVAS_ASSET_ACCESSED",
        {
            "canvas_file_id": str(canvas_access.record.id),
            "asset_file_id": str(asset_access.record.id),
            "asset_owner_user_id": asset_access.storage_owner_user_id,
        },
    )
    return response


@files_router.get('/canvas/spreadsheet/content')
def get_canvas_spreadsheet_content_route(
    file_id: str = Query(..., min_length=1),
    user = Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Return one validated spreadsheet snapshot for the browser editor.

    Validation and response creation use the same in-memory byte snapshot. A
    collaborator therefore cannot replace the backing object between a safety
    check and the bytes parsed by SheetJS in another user's browser.
    """
    normalized_file_id = str(file_id or "").strip()
    file_record = get_accessible_file(db, user.id, normalized_file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    original_filename = str(
        meta.get("original_filename") or file_record.file_name or ""
    ).strip()
    original_name = original_filename.lower()
    normalized_type = str(file_record.file_type or "").split(";", 1)[0].strip().lower()

    # User uploads may retain HTML or SVG as attachment-only source. Reject
    # those stored MIME types before consulting spreadsheet metadata or a file
    # extension, because neither hint is proof that active bytes are tabular.
    if is_inline_unsafe_mime_type(normalized_type):
        raise HTTPException(status_code=400, detail="Unsupported spreadsheet format")

    spreadsheet_format = str(meta.get("spreadsheet_format") or "").strip().lower()
    if spreadsheet_format not in {"csv", "tsv", "xlsx", "xls"}:
        mime_formats = {
            "text/csv": "csv",
            "text/tab-separated-values": "tsv",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/vnd.ms-excel": "xls",
        }
        spreadsheet_format = mime_formats.get(normalized_type, "")
    if not spreadsheet_format:
        spreadsheet_format = next(
            (
                extension[1:]
                for extension in (".csv", ".tsv", ".xlsx", ".xls")
                if original_name.endswith(extension)
            ),
            "",
        )
    if spreadsheet_format not in {"csv", "tsv", "xlsx", "xls"}:
        raise HTTPException(status_code=400, detail="Unsupported spreadsheet format")

    if int(file_record.file_size or 0) > _SPREADSHEET_PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="spreadsheet_preview_too_large")

    materialized_path = materialize_file_record(file_record, str(file_record.user_id))
    with Path(materialized_path).open("rb") as source:
        payload = source.read(_SPREADSHEET_PREVIEW_MAX_BYTES + 1)
    if len(payload) > _SPREADSHEET_PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="spreadsheet_preview_too_large")

    if spreadsheet_format == "xlsx":
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as temporary:
                temporary.write(payload)
                temporary_path = temporary.name
            validate_spreadsheet_archive(
                Path(temporary_path),
                file_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
            )
        except SpreadsheetArchiveValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.code) from exc
        finally:
            if temporary_path:
                try:
                    Path(temporary_path).unlink(missing_ok=True)
                except OSError:
                    pass

    # The editor consumes an ArrayBuffer and does not need browser content-type
    # interpretation. Always use an inert attachment response so a future MIME
    # alias or classification mistake cannot create same-origin active content.
    response = Response(
        content=payload,
        media_type="application/octet-stream",
        headers=attachment_headers(
            original_filename,
            fallback=f"spreadsheet.{spreadsheet_format}",
        ),
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Canvas-Revision"] = str(meta.get("canvas_revision") or 0)
    response.headers["X-Spreadsheet-Requires-Recalculation"] = (
        "true" if meta.get("spreadsheet_requires_recalculation") is True else "false"
    )
    return response


@files_router.post('/canvas/spreadsheet/save', response_model=CanvasSpreadsheetSaveResponse)
async def save_canvas_spreadsheet_route(
    request: Request,
    file: UploadFile = File(...),
    file_id: str = Form(...),
    file_format: str = Form(...),
    expected_revision: int = Form(..., ge=0),
    filename: str | None = Form(None),
    requires_recalculation: bool = Form(False),
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Persist an in-browser spreadsheet edit without changing its file ID."""
    normalized_file_id = str(file_id or "").strip()
    normalized_format = str(file_format or "").strip().lower()
    if not normalized_file_id:
        raise HTTPException(status_code=422, detail="file_id is required")
    if normalized_format not in {"csv", "tsv", "xlsx", "xls"}:
        raise HTTPException(status_code=422, detail="Unsupported spreadsheet format")

    actor_user_id = str(user.id)
    file_record = get_file(db, normalized_file_id, actor_user_id)
    target_user_id = actor_user_id
    if not file_record:
        candidate = db.query(Files).filter(Files.id == normalized_file_id).first()
        if (
            not candidate
            or not candidate.folder_id
            or not can_user_edit_folder(db, actor_user_id, candidate.folder_id)
        ):
            raise HTTPException(status_code=404, detail="File not found")
        target_user_id = candidate.user_id

    upload_file_obj = getattr(file, "file", None)
    if upload_file_obj is None:
        raise HTTPException(status_code=400, detail="Invalid spreadsheet payload")
    current_position = upload_file_obj.tell()
    upload_file_obj.seek(0, os.SEEK_END)
    file_size = upload_file_obj.tell()
    upload_file_obj.seek(current_position)
    ensure_user_file_upload_size_limit(db, str(target_user_id), file_size)
    release_db_session_before_long_wait(db)
    await file.seek(0)
    file_bytes = await file.read()

    try:
        save_result = save_canvas_spreadsheet(
            db,
            user_id=str(target_user_id),
            file_id=normalized_file_id,
            file_bytes=file_bytes,
            file_format=normalized_format,
            expected_revision=expected_revision,
            filename=filename,
            edit_source="user",
            edited_by=actor_user_id,
            requires_recalculation=requires_recalculation,
        )
        result = await save_result if inspect.isawaitable(save_result) else save_result
    except HTTPException:
        raise
    except CanvasSpreadsheetRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from exc
    except CanvasSpreadsheetInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to save spreadsheet") from exc

    _audit_file_event(
        db_log,
        request,
        actor_user_id,
        "SPREADSHEET_EDITED",
        {
            "file_id": normalized_file_id,
            "format": normalized_format,
            "bytes": len(file_bytes),
        },
    )
    return CanvasSpreadsheetSaveResponse(**result)


@files_router.post('/canvas/markdown/pdf')
def render_canvas_markdown_pdf_route(
    payload: CanvasMarkdownPdfRequest,
    request: Request,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Render a Markdown canvas document to an authenticated PDF download."""
    from app.workers.tool_jobs import external_rendering_enabled

    if external_rendering_enabled():
        from app.workers.models import WorkerJobFailed
        from app.workers.rendering import (
            enqueue_markdown_pdf,
            read_markdown_pdf_result,
            wait_for_rendering_job,
        )

        job = enqueue_markdown_pdf(
            user_id=str(user.id),
            markdown=payload.markdown,
            filename=payload.filename,
            source_file_id=payload.source_file_id,
        )
        try:
            queued_result = wait_for_rendering_job(job)
            rendered_filename, rendered_content = read_markdown_pdf_result(queued_result)
        except TimeoutError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "render_still_processing", "job_id": job.id},
                headers={"Retry-After": "3"},
            ) from exc
        except WorkerJobFailed as exc:
            status_code = 404 if exc.code in {"user_unavailable", "render_staging_unavailable"} else 422
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "job_id": job.id},
            ) from exc
    else:
        rendered = render_canvas_markdown_pdf(
            db,
            user_id=str(user.id),
            markdown_text=payload.markdown,
            filename=payload.filename,
            source_file_id=payload.source_file_id,
        )
        rendered_filename = rendered.filename
        rendered_content = rendered.content
    _audit_file_event(
        db_log,
        request,
        user.id,
        "CANVAS_MARKDOWN_PDF_DOWNLOADED",
        {
            "file_id": payload.source_file_id,
            "filename": rendered_filename,
            "content_type": "application/pdf",
        },
    )
    headers = attachment_headers(rendered_filename, fallback="canvas.pdf")
    return Response(content=rendered_content, media_type="application/pdf", headers=headers)


@files_router.post('/canvas/latex/render', response_model=CanvasLatexRenderResponse)
def render_canvas_latex_route(
    payload: CanvasLatexRenderRequest,
    request: Request,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Compile the stored revision of a LaTeX Canvas into a cached PDF.

    Source text is deliberately absent from this contract. The preceding
    Canvas save is authoritative, which prevents mismatches between editor,
    persisted source, and PDF preview.
    """
    source_access = resolve_file_for_edit(db, str(user.id), str(payload.file_id))
    if not source_access:
        raise HTTPException(status_code=404, detail="LaTeX Canvas not found")
    target_user_id = source_access.storage_owner_user_id

    # Rendering remains independently rate limited because editing a small
    # source file is cheap while compilation consumes an external service.
    from app.tools.helper import enforce_tool_rate_limit_or_raise

    enforce_tool_rate_limit_or_raise(
        db,
        user_id=str(user.id),
        group_id=getattr(user, "group_id", None),
        tool_name="latex_pdf",
    )
    from app.workers.tool_jobs import external_rendering_enabled

    rendering_is_external = external_rendering_enabled()
    try:
        if rendering_is_external:
            from app.workers.rendering import (
                enqueue_canvas_latex_render,
                wait_for_rendering_job,
            )

            job = enqueue_canvas_latex_render(
                actor_user_id=str(user.id),
                source_file_id=str(payload.file_id),
                expected_revision=payload.expected_revision,
                audit_ip_address=get_audit_request_ip(request, db),
                audit_user_agent=request.headers.get("user-agent"),
            )
            result = wait_for_rendering_job(job)
        else:
            result = render_latex_canvas(
                db,
                user_id=target_user_id,
                asset_actor_user_id=str(user.id),
                source_file_id=str(payload.file_id),
                expected_revision=payload.expected_revision,
                audit_ip_address=get_audit_request_ip(request, db),
                audit_user_agent=request.headers.get("user-agent"),
            )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "render_still_processing",
                "job_id": getattr(locals().get("job"), "id", None),
            },
            headers={"Retry-After": "3"},
        ) from exc
    except LatexSourceRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from exc
    except LatexRenderOutputLimitError as exc:
        raise HTTPException(
            status_code=502,
            detail="LaTeX renderer output exceeds the backend safety limits.",
        ) from exc
    except CanvasAssetAccessError as exc:
        raise HTTPException(status_code=403, detail=exc.code) from exc
    except LatexCompileError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "source_file_id": exc.source_file_id,
                "log_excerpt": exc.log_excerpt,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        from app.workers.models import WorkerJobFailed

        if isinstance(exc, WorkerJobFailed):
            status_by_code = {
                "latex_canvas_unavailable": 404,
                "user_unavailable": 404,
                "latex_revision_conflict": 409,
                "latex_output_limit": 502,
                "latex_asset_forbidden": 403,
                "latex_compile_failed": 422,
                "latex_invalid": 400,
            }
            raise HTTPException(
                status_code=status_by_code.get(exc.code, 500),
                detail={"code": exc.code, "job_id": getattr(locals().get("job"), "id", None)},
            ) from exc
        raise HTTPException(status_code=500, detail="Failed to render LaTeX Canvas") from exc

    if not is_admin_role(getattr(user, "role", None)):
        result["service_connection"] = None
    return CanvasLatexRenderResponse(**result)



@files_router.post("/canvas/share", response_model=ArtifactShareCreateResponse)
def create_artifact_share_route(
    payload: ArtifactShareCreateRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    artifact = (
        db.query(Files)
        .filter(Files.id == str(payload.file_id), Files.user_id == str(user.id))
        .first()
    )
    if artifact:
        source_canvas = get_canvas_source_for_artifact(db, artifact)
        if not is_canvas_artifact_dependency_snapshot_current(artifact, source_canvas):
            raise HTTPException(
                status_code=409,
                detail="canvas_asset_preview_stale",
            )
        try:
            pending_public = request_public_canvas_asset_access(
                db,
                canvas_record=source_canvas,
                sharing_user_id=str(user.id),
            )
        except CanvasAssetAccessError as exc:
            raise HTTPException(status_code=403, detail=exc.code) from exc
        if pending_public:
            notify_canvas_asset_approval_requests(
                db,
                actor_user_id=str(user.id),
                canvas_record=source_canvas,
                references=pending_public,
                public=True,
            )
            raise HTTPException(
                status_code=409,
                detail="canvas_asset_public_approval_required",
            )

    result = create_artifact_share(
        db=db,
        user_id=user.id,
        file_id=payload.file_id,
        password=payload.password,
        expires_in_hours=payload.expires_in_hours,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="CANVAS_SHARE_CREATED",
        details={
            "file_id": payload.file_id,
            "share_id": result.get("share_id"),
            "expires_in_hours": payload.expires_in_hours,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return ArtifactShareCreateResponse(**result)


@files_router.get("/canvas/share/status", response_model=ArtifactShareStatusResponse)
def get_artifact_share_status_route(
    file_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    result = get_artifact_share_status(db=db, user_id=user.id, file_id=file_id)
    return ArtifactShareStatusResponse(**result)


@files_router.post("/canvas/share/delete", response_model=ArtifactShareDeleteResponse)
def delete_artifact_share_route(
    payload: ArtifactShareDeleteRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    result = delete_artifact_share(db=db, user_id=user.id, share_id=payload.share_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="CANVAS_SHARE_DELETED",
        details={"share_id": payload.share_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return ArtifactShareDeleteResponse(**result)


@files_router.post("/canvas/share/password/change", response_model=ArtifactSharePasswordResponse)
def change_artifact_share_password_route(
    payload: ArtifactSharePasswordChangeRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    result = change_artifact_share_password(
        db=db,
        user_id=user.id,
        share_id=payload.share_id,
        password=payload.password,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="CANVAS_SHARE_PASSWORD_CHANGED",
        details={"share_id": payload.share_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return ArtifactSharePasswordResponse(**result)


@files_router.post("/canvas/share/password/remove", response_model=ArtifactSharePasswordResponse)
def remove_artifact_share_password_route(
    payload: ArtifactSharePasswordRemoveRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    result = remove_artifact_share_password(db=db, user_id=user.id, share_id=payload.share_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="CANVAS_SHARE_PASSWORD_REMOVED",
        details={"share_id": payload.share_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return ArtifactSharePasswordResponse(**result)


@files_router.post("/canvas/share/expiry/change", response_model=ArtifactShareExpiryResponse)
def change_artifact_share_expiry_route(
    payload: ArtifactShareExpiryChangeRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    result = change_artifact_share_expiry(
        db=db,
        user_id=user.id,
        share_id=payload.share_id,
        expires_at=payload.expires_at,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="CANVAS_SHARE_EXPIRY_CHANGED",
        details={"share_id": payload.share_id, "expires_at": result.get("expires_at")},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return ArtifactShareExpiryResponse(**result)


@files_router.post("/canvas/share/expiry/remove", response_model=ArtifactShareExpiryResponse)
def remove_artifact_share_expiry_route(
    payload: ArtifactShareExpiryRemoveRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    result = remove_artifact_share_expiry(db=db, user_id=user.id, share_id=payload.share_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="CANVAS_SHARE_EXPIRY_REMOVED",
        details={"share_id": payload.share_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return ArtifactShareExpiryResponse(**result)


@files_router.post("/canvas/shared/access", response_model=ArtifactShareAccessResponse)
def access_shared_artifact_route(
    payload: ArtifactShareAccessRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    apply_no_store_headers(response)

    client_ip = extract_client_ip_from_request(
        request,
        trusted_proxy_networks=resolve_trusted_proxy_networks("RATE_LIMIT_TRUSTED_PROXIES", "TRUSTED_PROXIES"),
        default=None,
    )
    enforce_shared_artifact_access_rate_limit(payload.share_id, client_ip)
    audit_subject = _shared_artifact_audit_subject(db, payload.share_id)
    try:
        result = resolve_shared_artifact_access(
            db=db,
            share_id=payload.share_id,
            password=payload.password,
            client_ip=client_ip,
        )
    except HTTPException as exc:
        create_audit_log(
            db_log=db_log,
            user_id=_shared_artifact_audit_user_id(audit_subject),
            action="CANVAS_SHARE_ACCESS_DENIED",
            details={
                **audit_subject,
                "status_code": exc.status_code,
                "reason": str(exc.detail or ""),
            },
            ip_address=client_ip or get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="share",
        )
        raise

    create_audit_log(
        db_log=db_log,
        user_id=_shared_artifact_audit_user_id(audit_subject),
        action="CANVAS_SHARE_ACCESSED",
        details={
            **audit_subject,
            "artifact_type": result.get("artifact_type"),
            "mime_type": result.get("mime_type"),
        },
        ip_address=client_ip or get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="share",
    )
    return ArtifactShareAccessResponse(**result)



# -------------------
# Get File by ID
# -------------------
@files_router.get('/{file_id}', response_model=FileList)
def get_file_route(file_id: str, user = Depends(verified_user), db: Session = Depends(get_db)):
    file = get_accessible_file(db, user.id, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    return _decorate_accessible_file_records(db, user.id, [file])[0]



# -------------------
# Get Files by IDs (Admin)
# -------------------
@files_router.post('/by-ids', response_model=list[FileList])
def get_files_by_ids_route(
    file_ids: List[str] = Body(..., embed=True),
    admin = Depends(verified_admin),
    db: Session = Depends(get_db)
):
    """Fetch file metadata for a list of file IDs. Admin only."""
    if not file_ids:
        return []
    files = db.query(Files).filter(Files.id.in_(file_ids)).all()
    return [FileList.model_validate(f) for f in files]
