from __future__ import annotations

import re
import uuid
import logging
from functools import partial
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

from app.file_folders.models import (
    FILE_FOLDER_SYSTEM_KIND_CANVAS,
    FileFolders,
    SharedFileFolderSubscription,
    can_user_edit_folder,
)
from app.files.models import Files, get_file
from app.files.schemas import HTML_ATTACHMENT_MIME_TYPES
from app.files.utils import (
    MAX_FILE_SIZE,
    SpreadsheetArchiveValidationError,
    TEMP_DIR,
    _upload_is_valid_active_content,
    delete_storage_reference,
    ensure_user_file_upload_capacity,
    ensure_user_file_upload_size_limit,
    get_file_category,
    invalidate_materialized_file_cache,
    materialize_file_record,
    normalize_file_mime_type,
    overwrite_existing_file_bytes,
    persist_generated_file_bytes,
    resolve_accessible_file_record,
    resolve_user_file_upload_limits,
    serialized_user_file_quota_admission,
    validate_spreadsheet_archive,
    validate_file_type,
)
from app.tools.errors import SafeToolExecutionError
from app.utils.blocking_io import run_blocking_io
from fastapi import HTTPException


logger = logging.getLogger(__name__)

_MARKDOWN_FILE_TYPES = {
    "text/markdown",
    "text/x-markdown",
    "text/plain",
}

_HTML_FILE_TYPES = frozenset(HTML_ATTACHMENT_MIME_TYPES)

_HTML_FILE_EXTENSIONS = (
    ".html",
    ".htm",
    ".xhtml",
    ".xht",
    ".xhtm",
    ".shtml",
    ".shtm",
)

_CANVAS_FILE_TYPES = {
    "text/markdown",
    "text/x-markdown",
    "text/plain",
    "text/csv",
    *_HTML_FILE_TYPES,
    "text/x-mermaid",
    "text/x-tex",
    "text/x-latex",
    "application/x-latex",
}

_CANVAS_UPLOAD_SAFE_FILE_TYPES = {
    "text/markdown",
    "text/x-markdown",
    "text/plain",
    "text/csv",
    "text/x-mermaid",
    "text/x-tex",
}

# Spreadsheet files use the same revision and ownership model as textual
# Canvas artifacts, but they must remain binary.  Keep their format metadata
# separate from the Canvas tool's content-type enum so models cannot create an
# XLS/XLSX payload through the text-oriented Canvas tool.
_SPREADSHEET_FORMATS = {
    "csv": (".csv", "text/csv"),
    "tsv": (".tsv", "text/tab-separated-values"),
    "xlsx": (
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "xls": (".xls", "application/vnd.ms-excel"),
}
_SPREADSHEET_MIME_TYPES = frozenset(item[1] for item in _SPREADSHEET_FORMATS.values())


class CanvasValidationError(SafeToolExecutionError):
    """Expected Canvas rejection with a safe model-facing diagnostic."""


class CanvasSpreadsheetInputError(ValueError):
    """Expected spreadsheet request rejection whose message is safe for clients."""


class CanvasPersistenceError(RuntimeError):
    """Internal Canvas persistence failure that must not become a 4xx detail."""


class CanvasRevisionConflict(RuntimeError):
    """Raised when a Canvas save targets an outdated persisted revision."""

    code = "canvas_revision_conflict"

    def __init__(self, *, expected_revision: int, current_revision: int):
        """Retain both revisions so callers can return a structured conflict."""
        super().__init__(
            "The Canvas file changed after this editor loaded it. Reload the latest revision."
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class CanvasSpreadsheetRevisionConflict(CanvasRevisionConflict):
    """Raised when a spreadsheet save targets an outdated Canvas revision."""

    code = "spreadsheet_revision_conflict"

    def __init__(self, *, expected_revision: int, current_revision: int):
        """Retain both revisions so the route can return a structured 409 response."""
        super().__init__(
            expected_revision=expected_revision,
            current_revision=current_revision,
        )


_TYPE_TO_EXTENSION = {
    "markdown": ".md",
    "mermaid": ".mmd",
    "csv": ".csv",
    "html": ".html",
    "latex": ".tex",
}

_TYPE_TO_MIME = {
    "markdown": "text/markdown",
    "mermaid": "text/x-mermaid",
    "csv": "text/csv",
    "html": "text/html",
    "latex": "text/x-tex",
}

_DEFAULT_FILENAMES = {
    "markdown": "canvas.md",
    "mermaid": "diagram.mmd",
    "csv": "data.csv",
    "html": "website.html",
    "latex": "document.tex",
}

_CONTENT_TYPES = frozenset(_TYPE_TO_EXTENSION.keys())
_MIME_TO_CONTENT_TYPE = {
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "text/plain": "markdown",
    "text/x-mermaid": "mermaid",
    "text/csv": "csv",
    **{mime_type: "html" for mime_type in _HTML_FILE_TYPES},
    "text/x-tex": "latex",
    "text/x-latex": "latex",
    "application/x-latex": "latex",
}
_CANVAS_FOLDER_NAME = "Canvas"
_CANVAS_FOLDER_ICON = "folder"
_CANVAS_FOLDER_ICON_COLOR = "#6366f1"


def _utc_iso() -> str:
    """Return the current UTC time as an ISO string for canvas revision metadata."""
    return datetime.now(timezone.utc).isoformat()


def _next_canvas_revision(meta: dict) -> int:
    """Return the next monotonically increasing revision number for a canvas file."""
    try:
        current_revision = int(meta.get("canvas_revision") or 0)
    except (TypeError, ValueError):
        current_revision = 0
    return current_revision + 1


def _canvas_revision_meta(meta: dict, *, edit_source: str, edited_by: str | None) -> dict:
    """Build revision metadata used to tell later model turns that a canvas changed."""
    source = str(edit_source or "").strip().lower() or "assistant"
    if source not in {"assistant", "user", "system"}:
        source = "assistant"
    return {
        "canvas_revision": _next_canvas_revision(meta),
        "canvas_last_edited_at": _utc_iso(),
        "canvas_last_edited_by": str(edited_by or "").strip(),
        "canvas_last_edit_source": source,
    }


def _ensure_canvas_folder_id(db, user_id: str) -> str | None:
    """Return the user's private Canvas system folder, creating it if needed.

    The folder's persisted ``system_kind`` is the only authoritative identity.
    Display names are user-editable and may collide with ordinary shared
    folders, so name matching must never be used for automatic placement.
    """
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id is required")

    # Some existing unit tests pass tiny stand-ins instead of SQLAlchemy
    # sessions. Real request/tool paths always provide a DB session with query
    # and add, so those paths still get the Canvas folder behavior.
    if not hasattr(db, "query") or not hasattr(db, "add"):
        return None

    existing_folder = (
        db.query(FileFolders)
        .filter(
            FileFolders.user_id == normalized_user_id,
            FileFolders.system_kind == FILE_FOLDER_SYSTEM_KIND_CANVAS,
        )
        .first()
    )
    if existing_folder:
        # Defense in depth for hand-edited databases or unsafe imported state.
        # Application sharing endpoints reject system folders, but Canvas must
        # also fail closed if that invariant has already been violated.
        has_share_id = any(
            str(getattr(existing_folder, field, "") or "").strip()
            for field in ("clone_share_id", "live_share_id", "collaborate_share_id")
        )
        has_subscription = (
            db.query(SharedFileFolderSubscription.id)
            .filter(SharedFileFolderSubscription.folder_id == existing_folder.id)
            .first()
            is not None
        )
        if has_share_id or has_subscription:
            raise ValueError("Canvas system folder must be private")
        return str(existing_folder.id)

    folder_count = db.query(FileFolders).filter(FileFolders.user_id == normalized_user_id).count()
    now = datetime.now(timezone.utc)
    folder = FileFolders(
        id=str(uuid.uuid4()),
        user_id=normalized_user_id,
        name=_CANVAS_FOLDER_NAME,
        icon=_CANVAS_FOLDER_ICON,
        icon_color=_CANVAS_FOLDER_ICON_COLOR,
        order=folder_count,
        system_kind=FILE_FOLDER_SYSTEM_KIND_CANVAS,
        created_at=now,
        updated_at=now,
    )
    db.add(folder)
    db.flush()
    return str(folder.id)


def _validate_canvas_content_bytes(
    content_bytes: bytes,
    *,
    file_type: str,
    allow_html_attachment: bool = False,
) -> None:
    """Validate Canvas bytes before storing source as an attachment.

    Active HTML is intentionally preserved.  It is never served inline from
    the file-download route and every in-app/public execution path uses the
    nested opaque-origin preview proxy.  ``allow_html_attachment`` remains in
    the signature for callers compiled against the earlier static-only
    contract, but active source no longer needs a privileged bypass.
    """
    if len(content_bytes) > MAX_FILE_SIZE:
        raise CanvasValidationError(
            code="canvas_file_too_large",
            safe_message=(
                "The Canvas payload exceeds the configured file-size limit. Reduce "
                "the existing payload and retry once."
            ),
            detail=(
                f"File size {len(content_bytes)} exceeds maximum allowed size "
                f"{MAX_FILE_SIZE}"
            ),
        )

    if file_type != "text/html" and (
        file_type not in _CANVAS_UPLOAD_SAFE_FILE_TYPES
        and not validate_file_type(file_type)
    ):
        raise ValueError(f"File type {file_type} is not allowed")

    temp_path = TEMP_DIR / f"{uuid.uuid4()}.canvas"
    try:
        temp_path.write_bytes(content_bytes)
        if file_type != "text/html" and not _upload_is_valid_active_content(file_type, temp_path):
            raise ValueError(f"File type {file_type} is not allowed")
        validate_spreadsheet_archive(temp_path, file_type=file_type)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _normalize_filename(filename: str | None, content_type: str = "markdown") -> str:
    default_name = _DEFAULT_FILENAMES.get(content_type, "canvas.md")
    raw_name = Path(str(filename or default_name)).name.strip() or default_name
    lower = raw_name.lower()
    
    expected_ext = _TYPE_TO_EXTENSION.get(content_type, ".md")
    valid_extensions = {
        "markdown": (".md", ".markdown"),
        "mermaid": (".mmd", ".mermaid"),
        "csv": (".csv",),
        "html": _HTML_FILE_EXTENSIONS,
        "latex": (".tex", ".latex"),
    }
    
    exts = valid_extensions.get(content_type, (".md",))
    if not any(lower.endswith(ext) for ext in exts):
        raw_name = f"{raw_name}{expected_ext}"
    return raw_name


def _count_pages(markdown: str) -> int:
    text = str(markdown or "").replace("\r\n", "\n")
    # Page delimiters: markdown horizontal rule (standalone ---) or explicit comment marker.
    parts = re.split(r"\n(?:---\s*|<!--\s*pagebreak\s*-->\s*)\n", text, flags=re.IGNORECASE)
    page_count = len(parts) if parts else 1
    return max(1, page_count)


def _normalize_content_type(content_type: str | None, fallback: str = "markdown") -> str:
    """Return a supported canvas content type, falling back when the input is absent or invalid."""
    normalized = str(content_type or "").lower().strip()
    if normalized in _CONTENT_TYPES:
        return normalized
    return fallback if fallback in _CONTENT_TYPES else "markdown"


def _content_type_from_file_record(file_record) -> str:
    """Infer the canvas content type already stored on an existing file record."""
    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    meta_type = str(meta.get("canvas_type") or "").lower().strip()
    if meta_type in _CONTENT_TYPES:
        return meta_type

    # LaTeX files created by the former dedicated tool predate Canvas-backed
    # LaTeX. Treat them as Canvas-compatible sources so existing conversations
    # and files become editable without a migration or duplicate file.
    if meta.get("latex_source") is True:
        return "latex"

    mime_type = normalize_file_mime_type(file_record.file_type)
    if mime_type in _MIME_TO_CONTENT_TYPE:
        return _MIME_TO_CONTENT_TYPE[mime_type]

    original_name = str(meta.get("original_filename") or file_record.file_name or "").lower()
    if original_name.endswith(_HTML_FILE_EXTENSIONS):
        return "html"
    if original_name.endswith((".tex", ".latex")):
        return "latex"
    if original_name.endswith((".mmd", ".mermaid")):
        return "mermaid"
    if original_name.endswith(".csv"):
        return "csv"
    return "markdown"


def _read_existing_canvas_text(file_record, user_id: str) -> str:
    """Load the current canvas file content as UTF-8 text so a snippet edit can patch it."""
    file_path = materialize_file_record(file_record, str(user_id))
    return file_path.read_bytes().decode("utf-8", errors="replace")


def view_canvas_file(
    db,
    *,
    user_id: str,
    file_id: str | None,
) -> dict:
    """Return the current stored content for an accessible canvas-compatible file."""
    if not user_id:
        raise ValueError("user_id is required")
    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id:
        raise ValueError("file_id is required to view a canvas file.")

    file_record, owner_user_id = resolve_accessible_file_record(db, str(user_id), normalized_file_id)
    if not file_record or not owner_user_id:
        raise ValueError("The target canvas file was not found for this user.")

    existing_type = normalize_file_mime_type(file_record.file_type)
    content_type = _content_type_from_file_record(file_record)
    if (
        existing_type
        and existing_type not in _CANVAS_FILE_TYPES
        and content_type != "html"
    ):
        raise ValueError("Only canvas-compatible files (markdown/mermaid/csv/html/latex) can be viewed.")

    content_text = _read_existing_canvas_text(file_record, str(owner_user_id))
    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    file_name = str(meta.get("original_filename") or file_record.file_name or _DEFAULT_FILENAMES.get(content_type, "canvas.md"))

    result = {
        "file_id": str(file_record.id),
        "file_name": file_name,
        "stored_file_name": file_record.file_name,
        "content": content_text,
        "content_type": content_type,
        "page_count": _count_pages(content_text) if content_type == "markdown" else 1,
        "created": False,
        "viewed": True,
        "canvas_revision": meta.get("canvas_revision"),
        "canvas_last_edited_at": meta.get("canvas_last_edited_at"),
        "canvas_last_edited_by": meta.get("canvas_last_edited_by"),
        "canvas_last_edit_source": meta.get("canvas_last_edit_source"),
    }
    if content_type == "latex":
        result.update(
            {
                "pdf_file_id": str(meta.get("latex_pdf_file_id") or ""),
                "pdf_file_name": str(meta.get("latex_pdf_file_name") or ""),
                "asset_file_ids": [
                    str(item)
                    for item in (meta.get("latex_asset_file_ids") or [])
                    if str(item or "").strip()
                ],
                "render_revision": meta.get("latex_render_revision"),
                "render_status": str(meta.get("latex_render_status") or "not_rendered"),
                "render_log_excerpt": str(meta.get("latex_log_excerpt") or "")[-4000:],
            }
        )
    return result


def _normalize_latex_asset_file_ids(file_ids: list[str] | None) -> list[str] | None:
    """Normalize an optional LaTeX asset list while preserving omission semantics.

    ``None`` means an edit did not address assets and existing metadata should
    remain unchanged. An explicit empty list intentionally clears the bundle.
    """
    if file_ids is None:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for value in file_ids:
        file_id = str(value or "").strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        normalized.append(file_id)
        if len(normalized) >= 20:
            break
    return normalized


def _implicit_canvas_asset_ids(
    *,
    content_type: str,
    content_text: str,
    file_ids: list[str] | None,
    file_record: Files | None,
    canvas_asset_references: list[dict[str, Any]] | None,
    force_reconciliation: bool = False,
) -> list[str] | None:
    """Return the dependencies that this save must authorize and reconcile.

    Callers that explicitly supply structured references have already handled
    this boundary. Model and internal saves derive their own IDs here, while
    the browser route additionally forces empty-source reconciliation. ``None``
    preserves an omitted LaTeX bundle; an empty list revokes a complete bundle.
    """

    if canvas_asset_references is not None:
        return None
    if content_type == "latex":
        return _normalize_latex_asset_file_ids(file_ids)
    if content_type not in {"markdown", "html"}:
        return None

    from app.files.canvas_assets import (
        extract_canvas_asset_ids,
        get_canvas_asset_references,
    )

    discovered = extract_canvas_asset_ids(content_text)
    # The authenticated browser route forces reconciliation even when metadata
    # is damaged or missing, so an explicit empty source revokes all grants.
    # Internal no-asset saves keep the cheap path unless a mirror already shows
    # that dependencies must be reconciled.
    if force_reconciliation or discovered or (
        file_record and get_canvas_asset_references(file_record)
    ):
        return discovered
    return None


def _validate_implicit_canvas_asset_ids(
    db,
    *,
    actor_user_id: str,
    asset_file_ids: list[str] | None,
) -> None:
    """Preflight model/internal dependencies before source bytes are committed."""

    if asset_file_ids is None:
        return
    from app.files.canvas_assets import validate_canvas_asset_ids_for_actor

    validate_canvas_asset_ids_for_actor(
        db,
        actor_user_id=str(actor_user_id),
        asset_file_ids=asset_file_ids,
    )


def _persist_implicit_canvas_asset_grants(
    db,
    *,
    actor_user_id: str,
    file_record: Files,
    asset_file_ids: list[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Flush authoritative grants and their metadata mirror with the source.

    The source row has already been flushed because grants have a foreign key
    to it, but the caller has not committed.  Preflight validation ran before
    the storage write, so a concurrent asset revocation or deletion can still
    fail here without leaving either the source row or stale grants committed.
    """

    if asset_file_ids is None:
        return [], []

    from app.files.canvas_assets import build_canvas_asset_references

    references, pending = build_canvas_asset_references(
        db,
        canvas_record=file_record,
        actor_user_id=str(actor_user_id),
        asset_file_ids=asset_file_ids,
    )
    meta = (
        dict(file_record.meta)
        if isinstance(getattr(file_record, "meta", None), dict)
        else {}
    )
    meta["canvas_asset_references"] = [dict(reference) for reference in references][
        :20
    ]
    file_record.meta = meta
    db.add(file_record)
    # Flush both the metadata update and revoked grants.  The enclosing save
    # owns the only commit for source and grant state.
    db.flush()
    return references, pending


def _notify_implicit_canvas_asset_approvals(
    db,
    *,
    actor_user_id: str,
    file_record: Files,
    pending_references: list[dict[str, Any]],
) -> None:
    """Deliver best-effort approval notifications after the save commits."""

    if not pending_references:
        return
    from app.files.canvas_assets import notify_canvas_asset_approval_requests

    notify_canvas_asset_approval_requests(
        db,
        actor_user_id=str(actor_user_id),
        canvas_record=file_record,
        references=pending_references,
    )


def _compensate_staged_canvas_storage(
    *,
    user_id: str,
    file_id: str,
    file_name: str,
    storage_provider: str,
    storage_key: str,
    previous_storage_key: str,
) -> None:
    """Delete uncommitted replacement bytes without hiding the save failure."""

    if not storage_key or storage_key == previous_storage_key:
        return
    try:
        invalidate_materialized_file_cache(file_id=file_id, file_name=file_name)
    except Exception:
        logger.warning(
            "Failed to invalidate staged Canvas cache after rollback",
            extra={"file_id": file_id, "storage_key": storage_key},
            exc_info=True,
        )
    try:
        # The explicit staged key is sufficient. Omitting ``file_name`` is
        # important for local storage because that compatibility argument also
        # deletes the canonical previous path.
        delete_storage_reference(
            storage_provider=storage_provider,
            storage_key=storage_key,
            user_id=user_id,
        )
    except Exception:
        logger.warning(
            "Failed to delete staged Canvas object after rollback",
            extra={"file_id": file_id, "storage_key": storage_key},
            exc_info=True,
        )


def _latex_render_status_after_source_edit(meta: dict) -> str:
    """Return the terminal preview state appropriate after a source edit.

    A document cannot be stale until it has a rendered PDF derivative. New
    LaTeX sources therefore remain ``not_rendered`` until their first preview,
    while edits to an already-rendered source invalidate that existing PDF.
    """
    return (
        "stale"
        if str(meta.get("latex_pdf_file_id") or "").strip()
        else "not_rendered"
    )


def _normalize_optional_snippet(snippet: str | None) -> str | None:
    """Treat omitted or blank snippet arguments as absent so creates do not become snippet edits."""
    if snippet is None:
        return None
    text = str(snippet)
    return text if text.strip() else None


def save_canvas_spreadsheet(
    db,
    *,
    user_id: str,
    file_id: str,
    file_bytes: bytes,
    file_format: str,
    expected_revision: int,
    filename: str | None = None,
    edit_source: str = "user",
    edited_by: str | None = None,
    requires_recalculation: bool = False,
) -> dict:
    """Atomically replace an editable spreadsheet while retaining its file ID.

    Spreadsheet edits intentionally share Canvas revision metadata.  Later
    model turns can therefore detect that a user changed an attachment after
    the assistant's previous response, even though binary Excel files are not
    valid inputs to the text-only Canvas creation tool.
    """
    normalized_user_id = str(user_id or "").strip()
    normalized_file_id = str(file_id or "").strip()
    normalized_format = str(file_format or "").strip().lower()
    if not normalized_user_id:
        raise CanvasSpreadsheetInputError("user_id is required")
    if not normalized_file_id:
        raise CanvasSpreadsheetInputError("file_id is required")
    if normalized_format not in _SPREADSHEET_FORMATS:
        raise CanvasSpreadsheetInputError("Unsupported spreadsheet format")
    if not file_bytes:
        raise CanvasSpreadsheetInputError("Spreadsheet content must not be empty")
    try:
        normalized_expected_revision = int(expected_revision)
    except (TypeError, ValueError) as exc:
        raise CanvasSpreadsheetInputError("expected_revision must be an integer") from exc
    if normalized_expected_revision < 0:
        raise CanvasSpreadsheetInputError("expected_revision must not be negative")

    # Lock the canonical row for the entire read/check/write transaction. The
    # expected-revision comparison alone would still race if two collaborators
    # passed it concurrently before either transaction committed.
    if hasattr(db, "query"):
        file_record = (
            db.query(Files)
            .filter(
                Files.id == normalized_file_id,
                Files.user_id == normalized_user_id,
            )
            .with_for_update()
            .first()
        )
    else:
        # A few focused unit tests use deliberately tiny session stand-ins.
        # Production FastAPI requests always take the row-locking branch.
        file_record = get_file(db, normalized_file_id, normalized_user_id)
    if not file_record:
        raise CanvasSpreadsheetInputError(
            "The target spreadsheet was not found for this user."
        )

    extension, target_file_type = _SPREADSHEET_FORMATS[normalized_format]
    existing_meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    try:
        current_revision = int(existing_meta.get("canvas_revision") or 0)
    except (TypeError, ValueError):
        current_revision = 0
    if current_revision != normalized_expected_revision:
        raise CanvasSpreadsheetRevisionConflict(
            expected_revision=normalized_expected_revision,
            current_revision=current_revision,
        )
    existing_name = str(
        existing_meta.get("original_filename") or file_record.file_name or "spreadsheet"
    ).strip()
    existing_suffix = Path(existing_name).suffix.lower()
    existing_type = normalize_file_mime_type(file_record.file_type)
    if existing_suffix not in {item[0] for item in _SPREADSHEET_FORMATS.values()} and (
        existing_type not in _SPREADSHEET_MIME_TYPES
    ):
        raise CanvasSpreadsheetInputError(
            "Only CSV, TSV, XLSX, and XLS files can be edited as spreadsheets."
        )

    requested_name = Path(str(filename or existing_name or f"spreadsheet{extension}")).name
    if not requested_name.lower().endswith(extension):
        requested_name = f"{Path(requested_name).stem or 'spreadsheet'}{extension}"

    # Reuse the hardened Canvas validation path for size, type, active-content,
    # and malware checks before any existing object is replaced.
    try:
        _validate_canvas_content_bytes(file_bytes, file_type=target_file_type)
    except SpreadsheetArchiveValidationError as exc:
        raise CanvasSpreadsheetInputError(exc.code) from exc
    except CanvasValidationError as exc:
        raise CanvasSpreadsheetInputError(exc.safe_message) from exc
    except ValueError as exc:
        # Validation runs before any persistence work, so only this narrow
        # boundary may deliberately convert a ValueError into client input.
        # Storage/database ValueErrors below remain generic server failures.
        raise CanvasSpreadsheetInputError(str(exc)) from exc
    ensure_user_file_upload_size_limit(db, normalized_user_id, len(file_bytes))
    max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(
        db, normalized_user_id
    )
    previous_storage_provider = (
        str(getattr(file_record, "storage_provider", "") or "local").strip().lower()
        or "local"
    )
    previous_storage_key = str(getattr(file_record, "storage_key", "") or "").strip()

    try:
        with serialized_user_file_quota_admission(db, normalized_user_id):
            ensure_user_file_upload_capacity(
                db,
                normalized_user_id,
                len(file_bytes),
                max_files_limit=max_files_limit,
                max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                existing_file_id=normalized_file_id,
            )
            storage_provider, storage_key, storage_meta = overwrite_existing_file_bytes(
                user_id=normalized_user_id,
                file_name=file_record.file_name,
                file_id=file_record.id,
                file_bytes=file_bytes,
            )

            next_meta = dict(existing_meta)
            next_meta.update(
                {
                    "original_filename": requested_name,
                    "origin": existing_meta.get("origin") or "user",
                    "canvas": True,
                    # CSV remains directly viewable through the Canvas tool;
                    # binary and tabular variants use the spreadsheet reader.
                    "canvas_type": "csv" if normalized_format == "csv" else "spreadsheet",
                    "spreadsheet_format": normalized_format,
                    # SheetJS preserves formulas but does not calculate them.
                    # Persist this conservative marker so a remounted editor
                    # never presents cached formula results as current after a
                    # browser-authored workbook edit.
                    "spreadsheet_requires_recalculation": bool(
                        requires_recalculation
                        and normalized_format in {"xlsx", "xls"}
                    ),
                    **_canvas_revision_meta(
                        existing_meta,
                        edit_source=edit_source,
                        edited_by=edited_by or normalized_user_id,
                    ),
                }
            )
            file_record.file_type = target_file_type
            file_record.file_category = get_file_category(target_file_type)
            file_record.file_size = len(file_bytes)
            file_record.storage_provider = storage_provider
            file_record.storage_key = storage_key
            file_record.storage_meta = storage_meta
            file_record.last_updated_at = datetime.now(timezone.utc)
            file_record.meta = next_meta
            db.add(file_record)
            db.commit()
            db.refresh(file_record)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        # Validation failures are raised before this persistence block. Never
        # turn database or storage exceptions into client-visible ValueErrors;
        # the route logs/returns its generic internal-error response instead.
        raise

    if previous_storage_key and (
        previous_storage_provider != storage_provider or previous_storage_key != storage_key
    ):
        try:
            delete_storage_reference(
                storage_provider=previous_storage_provider,
                storage_key=previous_storage_key,
                user_id=normalized_user_id,
                file_name=file_record.file_name,
            )
        except Exception:
            # The newly committed object is authoritative. Old-object cleanup
            # remains best effort, matching textual Canvas saves.
            pass

    return {
        "file_id": str(file_record.id),
        "file_name": requested_name,
        "content_type": "csv" if normalized_format == "csv" else "spreadsheet",
        "spreadsheet_format": normalized_format,
        "file_size": len(file_bytes),
        "canvas_revision": next_meta.get("canvas_revision"),
        "spreadsheet_requires_recalculation": bool(
            next_meta.get("spreadsheet_requires_recalculation")
        ),
    }


def _save_canvas_spreadsheet_with_thread_session(**kwargs) -> dict:
    """Run spreadsheet validation/storage with a worker-owned ORM session."""

    from app.database import SessionLocal

    session = SessionLocal()
    try:
        owner_user_id = str(kwargs.get("user_id") or "")
        actor_user_id = str(kwargs.get("edited_by") or owner_user_id)
        if actor_user_id != owner_user_id:
            record = (
                session.query(Files)
                .filter(
                    Files.id == str(kwargs.get("file_id") or ""),
                    Files.user_id == owner_user_id,
                )
                .first()
            )
            if (
                record is None
                or not record.folder_id
                or not can_user_edit_folder(
                    session,
                    actor_user_id,
                    record.folder_id,
                )
            ):
                raise HTTPException(status_code=404, detail="File not found")
        return save_canvas_spreadsheet(session, **kwargs)
    finally:
        session.close()


async def save_canvas_spreadsheet_off_event_loop(
    db,
    **kwargs,
) -> dict:
    """Keep binary validation and storage I/O off the ASGI event loop."""

    try:
        if db.get_bind().dialect.name == "sqlite":
            return save_canvas_spreadsheet(db, **kwargs)
    except (AttributeError, TypeError):
        pass

    return await run_blocking_io(
        partial(_save_canvas_spreadsheet_with_thread_session, **kwargs)
    )


def _find_exact_snippet(content: str, snippet: str, label: str) -> int:
    """Find one exact snippet occurrence; ambiguous snippet edits are rejected instead of guessed."""
    if not snippet:
        raise ValueError(f"{label} is required for a snippet update.")
    first_index = content.find(snippet)
    if first_index < 0:
        raise ValueError(f"{label} was not found in the existing canvas file.")
    if content.find(snippet, first_index + 1) >= 0:
        raise ValueError(f"{label} matched more than once. Provide a longer unique snippet.")
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
        end_start_index = existing_content.find(end_text, start_index + len(start_text))
        if end_start_index < 0:
            raise ValueError("end_snippet was not found after start_snippet in the existing canvas file.")
        end_index = end_start_index + len(end_text)

    return f"{existing_content[:start_index]}{replacement_content}{existing_content[end_index:]}"


def save_canvas_markdown(
    db,
    *,
    user_id: str,
    content: str | None,
    content_type: str | None = "markdown",
    filename: str | None = None,
    file_id: str | None = None,
    project_id: str | None = None,
    start_snippet: str | None = None,
    end_snippet: str | None = None,
    edit_source: str = "assistant",
    edited_by: str | None = None,
    file_ids: list[str] | None = None,
    canvas_asset_references: list[dict[str, Any]] | None = None,
    allow_html_attachment: bool = False,
    content_validator: Callable[[str], object] | None = None,
    content_transformer: Callable[[str], str] | None = None,
    expected_revision: int | None = None,
    force_canvas_asset_reconciliation: bool = False,
    before_commit: Callable[[dict[str, Any]], None] | None = None,
) -> dict:
    """Create or update a Canvas-compatible source file.

    HTML source may contain interactive behavior.  Storage stays inert and
    attachment-only; execution is owned by the browser's isolated Canvas
    preview proxy rather than this persistence layer.
    """
    if not user_id:
        raise ValueError("user_id is required")

    # Imported locally to keep the Canvas tool usable without eagerly loading
    # the file-access feature graph during module import.
    from app.files.canvas_assets import CanvasAssetAccessError

    start_snippet = _normalize_optional_snippet(start_snippet)
    end_snippet = _normalize_optional_snippet(end_snippet)

    if file_id:
        file_record = get_file(db, str(file_id), str(user_id))
        if not file_record:
            raise ValueError("The target canvas file was not found for this user.")

        existing_type = normalize_file_mime_type(file_record.file_type)
        fallback_content_type = _content_type_from_file_record(file_record)
        if (
            existing_type
            and existing_type not in _CANVAS_FILE_TYPES
            and fallback_content_type != "html"
        ):
            raise ValueError("Only canvas-compatible files (markdown/mermaid/csv/html/latex) can be edited.")

        content_type = _normalize_content_type(content_type, fallback=fallback_content_type)
        existing_content = ""
        if start_snippet is not None or end_snippet is not None:
            existing_content = _read_existing_canvas_text(file_record, str(user_id))
        content_text = _apply_snippet_update(
            existing_content,
            start_snippet=start_snippet,
            end_snippet=end_snippet,
            replacement_content=str(content if content is not None else ""),
        )
        if content_transformer is not None:
            # Presentation Canvas edits use this hook to resolve authorized
            # omlorix-file references and sanitize the complete post-edit deck.
            # Applying it after snippet replacement keeps partial edits safe.
            content_text = str(content_transformer(content_text))
        if content_validator is not None:
            # Validate the complete post-edit document before storage changes.
            # This is used by presentation Canvas files to prevent an invalid
            # partial edit from replacing the last renderable source.
            content_validator(content_text)
        implicit_asset_ids = _implicit_canvas_asset_ids(
            content_type=content_type,
            content_text=content_text,
            file_ids=file_ids,
            file_record=file_record,
            canvas_asset_references=canvas_asset_references,
            force_reconciliation=force_canvas_asset_reconciliation,
        )
        _validate_implicit_canvas_asset_ids(
            db,
            actor_user_id=str(edited_by or user_id),
            asset_file_ids=implicit_asset_ids,
        )
        content_bytes = content_text.encode("utf-8")
        target_file_type = _TYPE_TO_MIME.get(content_type, "text/markdown")
        _validate_canvas_content_bytes(
            content_bytes,
            file_type=target_file_type,
            allow_html_attachment=allow_html_attachment,
        )
        ensure_user_file_upload_size_limit(db, str(user_id), len(content_bytes))
        max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(db, str(user_id))
        target_category = get_file_category(target_file_type)
        normalized_filename = _normalize_filename(filename, content_type) if filename else ""
        previous_storage_provider = "local"
        previous_storage_key = ""
        staged_storage_provider = ""
        staged_storage_key = ""
        staged_storage_name = ""
        implicit_pending: list[dict[str, Any]] = []
        normalized_expected_revision: int | None = None
        if expected_revision is not None:
            try:
                normalized_expected_revision = int(expected_revision)
            except (TypeError, ValueError) as exc:
                raise ValueError("expected_revision must be an integer") from exc
            if normalized_expected_revision < 0:
                raise ValueError("expected_revision must not be negative")

        try:
            with serialized_user_file_quota_admission(db, str(user_id)):
                # Lock and reload the canonical row after taking the
                # cross-worker admission lock. A refresh alone does not
                # serialize two concurrent revision checks.
                if normalized_expected_revision is not None:
                    if hasattr(db, "query"):
                        file_record = (
                            db.query(Files)
                            .filter(
                                Files.id == str(file_id),
                                Files.user_id == str(user_id),
                            )
                            .with_for_update()
                            .first()
                        )
                    else:
                        file_record = get_file(db, str(file_id), str(user_id))
                    if not file_record:
                        raise ValueError("The target canvas file was not found for this user.")
                    latest_meta = (
                        file_record.meta if isinstance(file_record.meta, dict) else {}
                    )
                    try:
                        current_revision = int(latest_meta.get("canvas_revision") or 0)
                    except (TypeError, ValueError):
                        # Persisted corruption must never accidentally satisfy
                        # a valid non-negative client revision.
                        current_revision = -1
                    if current_revision != normalized_expected_revision:
                        raise CanvasRevisionConflict(
                            expected_revision=normalized_expected_revision,
                            current_revision=current_revision,
                        )
                # Read cleanup metadata from the same canonical row used for
                # the write, including when the revision path reloaded it.
                previous_storage_provider = str(
                    getattr(file_record, "storage_provider", "") or "local"
                ).strip().lower() or "local"
                previous_storage_key = str(
                    getattr(file_record, "storage_key", "") or ""
                ).strip()
                ensure_user_file_upload_capacity(
                    db,
                    str(user_id),
                    len(content_bytes),
                    max_files_limit=max_files_limit,
                    max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                    existing_file_id=str(file_record.id),
                )
                # Storage providers cannot enlist in the SQL transaction. Use
                # a new object key so a failed grant reconciliation can delete
                # the staged bytes without destroying the still-current source.
                stored_suffix = Path(file_record.file_name or "").suffix
                staged_storage_name = (
                    f"{file_record.id}.{uuid.uuid4().hex}{stored_suffix}"
                )
                storage_provider, storage_key, storage_meta = overwrite_existing_file_bytes(
                    user_id=str(user_id),
                    file_name=staged_storage_name,
                    file_id=file_record.id,
                    file_bytes=content_bytes,
                )
                staged_storage_provider = storage_provider
                staged_storage_key = storage_key

                existing_meta = file_record.meta if isinstance(file_record.meta, dict) else {}
                existing_meta = dict(existing_meta) if existing_meta is not None else {}
                existing_original_name = str(existing_meta.get("original_filename") or "").strip()
                default_name = _DEFAULT_FILENAMES.get(content_type, "canvas.md")
                fallback_original_name = existing_original_name or str(file_record.file_name or "").strip() or default_name
                original_name = normalized_filename or _normalize_filename(fallback_original_name, content_type)

                meta = dict(existing_meta)
                meta.update(
                    {
                        "original_filename": original_name,
                        # Preserve the provenance of ordinary user uploads when
                        # they become editable Canvas-backed attachments.
                        "origin": existing_meta.get("origin") or (
                            "user" if edit_source == "user" else "assistant"
                        ),
                        "canvas": True,
                        "canvas_type": content_type,
                        **_canvas_revision_meta(
                            existing_meta,
                            edit_source=edit_source,
                            edited_by=edited_by or user_id,
                        ),
                    }
                )
                if content_type == "latex":
                    normalized_asset_ids = _normalize_latex_asset_file_ids(file_ids)
                    meta.update(
                        {
                            # Keep legacy keys during the transition because
                            # file previews in stored chats still recognize
                            # them, while Canvas metadata is authoritative.
                            "latex_source": True,
                            "latex_display_title": str(
                                existing_meta.get("latex_display_title")
                                or existing_meta.get("title")
                                or Path(original_name).stem
                                or "LaTeX document"
                            ),
                            "latex_render_status": _latex_render_status_after_source_edit(
                                existing_meta
                            ),
                            "latex_compile_failed": False,
                            "latex_log_excerpt": "",
                        }
                    )
                    if normalized_asset_ids is not None:
                        meta["latex_asset_file_ids"] = normalized_asset_ids
                if canvas_asset_references is not None:
                    # Structured references carry provenance and approval
                    # state.  They remain portable file metadata, while every
                    # consumer still revalidates the underlying file row.
                    meta["canvas_asset_references"] = [
                        dict(reference)
                        for reference in canvas_asset_references
                        if isinstance(reference, dict)
                    ][:20]

                file_record.file_type = target_file_type
                file_record.file_category = target_category
                file_record.file_size = len(content_bytes)
                file_record.storage_provider = storage_provider
                file_record.storage_key = storage_key
                file_record.storage_meta = storage_meta
                # Editing content must not silently relocate the file.  Its
                # current folder is an authorization boundary chosen by the
                # user, and changing it here could broaden or revoke access.
                file_record.last_updated_at = datetime.now(timezone.utc)
                file_record.meta = meta
                db.add(file_record)

                _, implicit_pending = _persist_implicit_canvas_asset_grants(
                    db,
                    actor_user_id=str(edited_by or user_id),
                    file_record=file_record,
                    asset_file_ids=implicit_asset_ids,
                )
                if before_commit is not None:
                    before_commit(
                        {
                            "file_id": str(file_record.id),
                            "created": False,
                            "content_type": content_type,
                            "canvas_revision": meta.get("canvas_revision"),
                            "asset_count": len(implicit_asset_ids or []),
                            "pending_asset_approval_count": len(implicit_pending),
                        }
                    )
                db.commit()
                db.refresh(file_record)
        except HTTPException:
            db.rollback()
            _compensate_staged_canvas_storage(
                user_id=str(user_id),
                file_id=str(file_record.id),
                file_name=str(file_record.file_name),
                storage_provider=staged_storage_provider,
                storage_key=staged_storage_key,
                previous_storage_key=previous_storage_key,
            )
            raise
        except CanvasRevisionConflict:
            db.rollback()
            _compensate_staged_canvas_storage(
                user_id=str(user_id),
                file_id=str(file_record.id),
                file_name=str(file_record.file_name),
                storage_provider=staged_storage_provider,
                storage_key=staged_storage_key,
                previous_storage_key=previous_storage_key,
            )
            raise
        except CanvasAssetAccessError:
            db.rollback()
            _compensate_staged_canvas_storage(
                user_id=str(user_id),
                file_id=str(file_record.id),
                file_name=str(file_record.file_name),
                storage_provider=staged_storage_provider,
                storage_key=staged_storage_key,
                previous_storage_key=previous_storage_key,
            )
            raise
        except Exception as exc:
            db.rollback()
            _compensate_staged_canvas_storage(
                user_id=str(user_id),
                file_id=str(file_record.id),
                file_name=str(file_record.file_name),
                storage_provider=staged_storage_provider,
                storage_key=staged_storage_key,
                previous_storage_key=previous_storage_key,
            )
            logger.exception("Failed to update Canvas file")
            raise CanvasPersistenceError("Failed to update canvas file") from exc

        if (
            previous_storage_key
            and (
                previous_storage_provider != storage_provider
                or previous_storage_key != storage_key
            )
        ):
            try:
                delete_storage_reference(
                    storage_provider=previous_storage_provider,
                    storage_key=previous_storage_key,
                    user_id=str(user_id),
                    file_name=file_record.file_name,
                )
            except Exception:
                # Keep write successful even if old object cleanup fails.
                pass

        _notify_implicit_canvas_asset_approvals(
            db,
            actor_user_id=str(edited_by or user_id),
            file_record=file_record,
            pending_references=implicit_pending,
        )
        if implicit_asset_ids is not None:
            meta = file_record.meta if isinstance(file_record.meta, dict) else meta

        result = {
            "file_id": file_record.id,
            "file_name": original_name,
            "stored_file_name": file_record.file_name,
            "content": content_text,
            "content_type": content_type,
            "page_count": _count_pages(content_text) if content_type == "markdown" else 1,
            "created": False,
            "canvas_revision": meta.get("canvas_revision"),
            "pending_asset_approval_count": len(implicit_pending),
        }
        if content_type == "latex":
            result.update(
                {
                    "pdf_file_id": str(meta.get("latex_pdf_file_id") or ""),
                    "pdf_file_name": str(meta.get("latex_pdf_file_name") or ""),
                    "asset_file_ids": list(meta.get("latex_asset_file_ids") or []),
                    "render_revision": meta.get("latex_render_revision"),
                    "render_status": str(
                        meta.get("latex_render_status")
                        or _latex_render_status_after_source_edit(meta)
                    ),
                }
            )
        return result

    if start_snippet is not None or end_snippet is not None:
        raise ValueError("Snippet updates require file_id for the existing canvas file.")

    content_type = _normalize_content_type(content_type, fallback="markdown")
    content_text = str(content if content is not None else "")
    if content_transformer is not None:
        content_text = str(content_transformer(content_text))
    if content_validator is not None:
        content_validator(content_text)
    implicit_asset_ids = _implicit_canvas_asset_ids(
        content_type=content_type,
        content_text=content_text,
        file_ids=file_ids,
        file_record=None,
        canvas_asset_references=canvas_asset_references,
        force_reconciliation=force_canvas_asset_reconciliation,
    )
    _validate_implicit_canvas_asset_ids(
        db,
        actor_user_id=str(edited_by or user_id),
        asset_file_ids=implicit_asset_ids,
    )
    content_bytes = content_text.encode("utf-8")
    target_file_type = _TYPE_TO_MIME.get(content_type, "text/markdown")
    _validate_canvas_content_bytes(content_bytes, file_type=target_file_type)
    ensure_user_file_upload_size_limit(db, str(user_id), len(content_bytes))
    max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(db, str(user_id))
    target_category = get_file_category(target_file_type)
    normalized_filename = _normalize_filename(filename, content_type) if filename else ""
    original_name = normalized_filename or _normalize_filename(None, content_type)
    file_extension = Path(original_name).suffix or _TYPE_TO_EXTENSION.get(content_type, ".md")
    stored_file_id = str(uuid.uuid4())
    stored_file_name = f"{stored_file_id}{file_extension}"
    implicit_pending: list[dict[str, Any]] = []

    def reconcile_new_canvas_grants(new_file_record: Files) -> None:
        """Attach dependent grants after the source row receives its ID."""

        nonlocal implicit_pending
        _, implicit_pending = _persist_implicit_canvas_asset_grants(
            db,
            actor_user_id=str(edited_by or user_id),
            file_record=new_file_record,
            asset_file_ids=implicit_asset_ids,
        )
        if before_commit is not None:
            new_meta = (
                new_file_record.meta
                if isinstance(new_file_record.meta, dict)
                else {}
            )
            before_commit(
                {
                    "file_id": str(new_file_record.id),
                    "created": True,
                    "content_type": content_type,
                    "canvas_revision": new_meta.get("canvas_revision") or 1,
                    "asset_count": len(implicit_asset_ids or []),
                    "pending_asset_approval_count": len(implicit_pending),
                }
            )

    try:
        # The system-folder lookup and insert must share the same per-user lock
        # as quota admission. Otherwise, two simultaneous first saves can both
        # observe a missing Canvas folder and race on its unique system kind.
        with serialized_user_file_quota_admission(db, str(user_id)):
            ensure_user_file_upload_capacity(
                db,
                str(user_id),
                len(content_bytes),
                max_files_limit=max_files_limit,
                max_user_storage_limit_bytes=max_user_storage_limit_bytes,
            )
            canvas_folder_id = _ensure_canvas_folder_id(db, str(user_id))

            # Capacity was checked while holding this lock, so omit the limits
            # here to avoid a redundant nested admission check. The persistence
            # helper commits both the pending folder and file record together.
            file_record = persist_generated_file_bytes(
                db=db,
                user_id=str(user_id),
                original_filename=original_name,
                file_bytes=content_bytes,
                file_type=target_file_type,
                file_category=target_category,
                meta={
                    "original_filename": original_name,
                    "origin": "assistant",
                    "canvas": True,
                    "canvas_type": content_type,
                    **_canvas_revision_meta({}, edit_source=edit_source, edited_by=edited_by or user_id),
                    **(
                        {
                            "latex_source": True,
                            "latex_display_title": Path(original_name).stem or "LaTeX document",
                            "latex_asset_file_ids": _normalize_latex_asset_file_ids(file_ids) or [],
                            "latex_render_status": "not_rendered",
                            "latex_compile_failed": False,
                            "latex_log_excerpt": "",
                        }
                        if content_type == "latex"
                        else {}
                    ),
                    **(
                        {
                            "canvas_asset_references": [
                                dict(reference)
                                for reference in canvas_asset_references
                                if isinstance(reference, dict)
                            ][:20]
                        }
                        if canvas_asset_references is not None
                        else {}
                    ),
                },
                file_id=stored_file_id,
                file_name=stored_file_name,
                folder_id=canvas_folder_id,
                # The helper flushes the source row, invokes this callback, and
                # commits both the row and authoritative grants together. Its
                # existing failure path also deletes the uploaded object.
                before_commit=reconcile_new_canvas_grants,
            )
    except HTTPException:
        db.rollback()
        raise
    except CanvasAssetAccessError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to create Canvas file record")
        raise CanvasPersistenceError("Failed to create canvas file record") from exc

    _notify_implicit_canvas_asset_approvals(
        db,
        actor_user_id=str(edited_by or user_id),
        file_record=file_record,
        pending_references=implicit_pending,
    )

    result = {
        "file_id": file_record.id,
        "file_name": original_name,
        "stored_file_name": file_record.file_name,
        "content": content_text,
        "content_type": content_type,
        "page_count": _count_pages(content_text) if content_type == "markdown" else 1,
        "created": True,
        "canvas_revision": (getattr(file_record, "meta", None) or {}).get("canvas_revision") if isinstance(getattr(file_record, "meta", None), dict) else 1,
        "pending_asset_approval_count": len(implicit_pending),
    }
    if content_type == "latex":
        meta = file_record.meta if isinstance(getattr(file_record, "meta", None), dict) else {}
        result.update(
            {
                "pdf_file_id": "",
                "pdf_file_name": "",
                "asset_file_ids": list(meta.get("latex_asset_file_ids") or []),
                "render_revision": None,
                "render_status": "not_rendered",
            }
        )
    return result
