import json
import logging
import re
import struct
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import httpx

from app.files.models import Files, get_file
from app.files.canvas_assets import (
    copy_canvas_asset_references,
    prepare_canvas_asset_files_payload,
)
from app.files.utils import (
    delete_file,
    ensure_user_file_upload_capacity,
    ensure_user_file_upload_size_limit,
    get_file_category,
    materialize_file_record,
    persist_generated_file_bytes,
    persist_generated_file_replacement_bytes,
    resolve_user_file_upload_limits,
)
from app.files.schemas import FileDeleteTimeOption
from app.logging.models import stage_audit_log_event
from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.service_connections.utils import (
    SERVICE_PURPOSE_LATEX_PDF,
    get_service_connection_candidates,
    record_service_connection_runtime_status,
)
from app.tools.code_execution.utils import (
    _CodeExecutionServiceUnavailableError,
    _check_service_health,
    _connection_headers,
    _is_retryable_code_service_error,
    _prepare_input_files_payload,
)
from app.tools.audit import stage_tool_audit_action


logger = logging.getLogger(__name__)


# Renderer responses cross a process and network boundary, so they must remain
# bounded even when the configured service is trusted. The limits below leave
# ample room for normal printable documents while preventing a small request or
# compressed archive from turning into an unbounded backend allocation.
LATEX_RENDER_IO_CHUNK_BYTES = 64 * 1024
LATEX_RENDER_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
LATEX_RENDER_MAX_ARCHIVE_ENTRIES = 16
LATEX_RENDER_MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
LATEX_RENDER_MAX_PDF_BYTES = 50 * 1024 * 1024
LATEX_RENDER_MAX_SOURCE_BYTES = 10 * 1024 * 1024
LATEX_RENDER_MAX_LOG_BYTES = 2 * 1024 * 1024
LATEX_RENDER_MAX_METADATA_BYTES = 64 * 1024


class LatexRenderOutputLimitError(RuntimeError):
    """Raised when renderer-controlled output exceeds a backend safety limit."""


@dataclass(frozen=True)
class _BoundedLatexRenderResponse:
    """Minimal buffered response created only after streamed size admission."""

    status_code: int
    headers: httpx.Headers
    content: bytes

    @property
    def text(self) -> str:
        """Decode bounded error bodies without depending on a live response."""
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """Parse a bounded JSON response body using the standard decoder."""
        return json.loads(self.content)


@dataclass(frozen=True)
class LatexCanvasAuditContext:
    """Authenticated request context for transactional Canvas render audits."""

    actor_user_id: str
    ip_address: str | None = None
    user_agent: str | None = None


class LatexServiceRenderError(RuntimeError):
    """Raised when the external LaTeX renderer rejects a render request."""

    def __init__(self, *, status_code: int, detail: str, log_excerpt: str = ""):
        super().__init__(f"LaTeX PDF service returned status {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.log_excerpt = log_excerpt


class LatexCompileError(RuntimeError):
    """Raised after saving the failed LaTeX source for later repair."""

    def __init__(self, message: str, *, source_file_id: str = "", log_excerpt: str = ""):
        super().__init__(message)
        self.source_file_id = source_file_id
        self.log_excerpt = log_excerpt


class LatexSourceRevisionConflict(RuntimeError):
    """Raised when a render result targets an outdated Canvas source revision."""

    def __init__(self, *, expected_revision: int, current_revision: int):
        super().__init__(
            "The LaTeX source changed while the preview was rendering. Render the latest revision again."
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_stem(value: Any, default: str = "document") -> str:
    stem = Path(str(value or "").strip()).stem or str(value or "").strip()
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("._-")
    return (normalized or default)[:64]


def _pdf_filename(value: Any, default: str = "document.pdf") -> str:
    raw = str(value or "").strip() or default
    stem = _safe_stem(raw, default=Path(default).stem or "document")
    return f"{stem}.pdf"


def _tex_filename(value: Any, default: str = "document.tex") -> str:
    raw = str(value or "").strip() or default
    stem = _safe_stem(raw, default=Path(default).stem or "document")
    return f"{stem}.tex"


def _normalize_optional_snippet(snippet: str | None) -> str | None:
    """Treat omitted or blank snippets as absent so normal renders stay simple."""
    if snippet is None:
        return None
    text = str(snippet)
    return text if text.strip() else None


def _find_exact_snippet(content: str, snippet: str, label: str) -> int:
    """Find one exact snippet; ambiguous LaTeX source edits are rejected."""
    if not snippet:
        raise ValueError(f"{label} is required for a snippet update.")
    first_index = content.find(snippet)
    if first_index < 0:
        raise ValueError(f"{label} was not found in the existing LaTeX source.")
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
    """Replace the inclusive range from start_snippet through end_snippet."""
    if start_snippet is None and end_snippet is None:
        return replacement_content
    if start_snippet is None or end_snippet is None:
        raise ValueError("Both start_snippet and end_snippet are required for a LaTeX source update.")

    start_text = str(start_snippet)
    end_text = str(end_snippet)
    start_index = _find_exact_snippet(existing_content, start_text, "start_snippet")

    if start_text == end_text:
        end_index = start_index + len(end_text)
    else:
        end_start_index = existing_content.find(end_text, start_index + len(start_text))
        if end_start_index < 0:
            raise ValueError("end_snippet was not found after start_snippet in the existing LaTeX source.")
        end_index = end_start_index + len(end_text)

    return f"{existing_content[:start_index]}{replacement_content}{existing_content[end_index:]}"


def _is_latex_source_record(file_record: Files | None) -> bool:
    """Return True when a file record is safe to treat as editable LaTeX source."""
    if not file_record:
        return False
    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    if meta.get("latex_source") is True:
        return True
    mime_type = str(file_record.file_type or "").strip().lower()
    if mime_type in {"text/x-tex", "text/x-latex", "application/x-latex"}:
        return True
    original_name = str(meta.get("original_filename") or file_record.file_name or "").strip().lower()
    return original_name.endswith(".tex")


def _is_latex_pdf_record(file_record: Files | None) -> bool:
    """Return True when a file record is safe to overwrite with a rendered PDF."""
    if not file_record:
        return False
    if str(file_record.file_type or "").strip().lower() != "application/pdf":
        return False
    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    return meta.get("render_latex_pdf") is True or meta.get("latex_pdf") is True


def _read_latex_source_file(db, *, user_id: str, source_file_id: str) -> str:
    """Load an owned source file as UTF-8 text for model or user snippet edits."""
    file_record = get_file(db, str(source_file_id), str(user_id))
    if not _is_latex_source_record(file_record):
        raise ValueError("The target LaTeX source file was not found for this user.")
    file_path = materialize_file_record(file_record, str(user_id))
    return file_path.read_bytes().decode("utf-8", errors="replace")


def _canvas_revision(file_record: Files) -> int:
    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    return int(meta.get("canvas_revision") or 0)


def _stage_canvas_latex_audit(
    db,
    audit_context: LatexCanvasAuditContext,
    action: str,
    details: dict[str, Any],
    *,
    reason: str | None = None,
) -> None:
    """Stage one content-free Canvas event in the mutation transaction."""

    stage_audit_log_event(
        db,
        user_id=str(audit_context.actor_user_id),
        action=action,
        reason=reason,
        details=details,
        ip_address=audit_context.ip_address,
        user_agent=audit_context.user_agent,
        category="files",
    )


def _commit_latex_record(
    db,
    record: Files,
    *,
    before_commit: Callable[[Files], None] | None = None,
) -> Files:
    """Commit one LaTeX record and optional audit intent atomically."""

    db.add(record)
    try:
        if hasattr(db, "flush"):
            db.flush()
        if before_commit is not None:
            before_commit(record)
        db.commit()
    except Exception:
        if hasattr(db, "rollback"):
            db.rollback()
        raise
    if hasattr(db, "refresh"):
        db.refresh(record)
    return record


def _record_canvas_render_state(
    db,
    *,
    source_record: Files,
    expected_revision: int | None,
    status: str,
    log_excerpt: str = "",
    before_commit: Callable[[Files], None] | None = None,
) -> Files:
    """Update only derived render metadata without rewriting canonical source bytes."""
    if hasattr(db, "refresh"):
        db.refresh(source_record)
    meta = dict(source_record.meta) if isinstance(source_record.meta, dict) else {}
    current_revision = int(meta.get("canvas_revision") or 0)
    if expected_revision is not None and current_revision != int(expected_revision):
        raise LatexSourceRevisionConflict(
            expected_revision=int(expected_revision),
            current_revision=current_revision,
        )
    meta.update(
        {
            "latex_render_status": str(status or "not_rendered"),
            "latex_compile_failed": status == "failed",
            "latex_log_excerpt": str(log_excerpt or "")[-4000:],
        }
    )
    source_record.meta = meta
    return _commit_latex_record(
        db,
        source_record,
        before_commit=before_commit,
    )


def _build_latex_input_files(raw_files: list[dict[str, str]]) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_files or []):
        raw_name = str(item.get("name") or f"input_{index}").strip().replace("\\", "/")
        name = raw_name.rsplit("/", 1)[-1].strip() or f"input_{index}"
        name = re.sub(r"[\x00-\x1f\x7f]+", "-", name).strip() or f"input_{index}"
        if name in {".", ".."}:
            name = f"input_{index}"
        if len(name) > 180:
            stem = Path(name).stem or "input"
            suffix = Path(name).suffix
            name = f"{stem[: max(1, 180 - len(suffix))]}{suffix}"[:180]
        if name in seen:
            stem = Path(name).stem or "input"
            suffix = Path(name).suffix
            suffix_budget = len(suffix)
            marker = f"-{index}"
            name = f"{stem[: max(1, 180 - suffix_budget - len(marker))]}{marker}{suffix}"[:180]
        seen.add(name)
        files.append(
            {
                "file_name": name,
                "base64_content": str(item.get("content") or ""),
            }
        )
    return files


def _overwrite_generated_file_bytes(
    db,
    *,
    user_id: str,
    file_record: Files,
    original_filename: str,
    file_bytes: bytes,
    file_type: str,
    file_category: str | None,
    meta: dict[str, Any],
    folder_id: str | None = None,
    project_id: str | None = None,
    update_location: bool = False,
    before_commit: Callable[[Files], None] | None = None,
) -> Files:
    """Replace a generated artifact while preserving its stable file id.

    ``update_location`` is deliberately explicit because most source-file
    overwrites must preserve their existing folder. Derived artifacts, however,
    need to follow the source file's access boundary so collaborators who can
    edit the source can also open the regenerated output.

    The shared persistence helper stages replacement bytes under a fresh key and
    publishes the row, dependants, and audit outbox event together. The previous
    bytes remain authoritative if that transaction rolls back.
    """
    ensure_user_file_upload_size_limit(db, str(user_id), len(file_bytes))
    return persist_generated_file_replacement_bytes(
        db,
        user_id=str(user_id),
        file_record=file_record,
        original_filename=original_filename,
        file_bytes=file_bytes,
        file_type=file_type,
        file_category=file_category or get_file_category(file_type),
        meta=meta,
        folder_id=folder_id,
        project_id=project_id,
        update_location=update_location,
        before_commit=before_commit,
    )


def _persist_latex_source_attempt(
    db,
    *,
    user_id: str,
    source_record_for_update: Files | None,
    tex_name: str,
    title_text: str,
    source_bytes: bytes,
    pdf_file_id: str | None,
    file_ids: list[str] | None,
    input_file_names: list[str],
    max_files_limit: int,
    max_user_storage_limit_bytes: int | None,
    compile_failed: bool = False,
    log_excerpt: str = "",
    before_commit: Callable[[Files], None] | None = None,
) -> Files:
    """Persist the editable .tex source even when the PDF compile fails."""
    # Generated LaTeX sources are ordinary user-owned files. Apply the same
    # per-file size policy used by uploads and overwrites before touching either
    # the existing object or the fresh-file persistence path.
    ensure_user_file_upload_size_limit(db, str(user_id), len(source_bytes))

    source_meta = {
        "original_filename": tex_name,
        "origin": "assistant",
        "latex_source": True,
        "latex_display_title": title_text,
        "title": title_text,
        "latex_pdf_file_id": str(pdf_file_id or ""),
        "latex_asset_file_ids": [str(item) for item in (file_ids or []) if str(item or "").strip()],
        "latex_input_file_names": input_file_names,
        "latex_compile_failed": bool(compile_failed),
        "latex_log_excerpt": log_excerpt[-4000:] if log_excerpt else "",
        "generated_at": _utc_iso(),
    }
    if source_record_for_update:
        return _overwrite_generated_file_bytes(
            db,
            user_id=str(user_id),
            file_record=source_record_for_update,
            original_filename=tex_name,
            file_bytes=source_bytes,
            file_type="text/x-tex",
            file_category=get_file_category("text/x-tex"),
            meta=source_meta,
            before_commit=before_commit,
        )
    return persist_generated_file_bytes(
        db,
        user_id=str(user_id),
        original_filename=tex_name,
        file_bytes=source_bytes,
        file_type="text/x-tex",
        file_category=get_file_category("text/x-tex"),
        file_id=str(uuid.uuid4()),
        meta=source_meta,
        max_files_limit=max_files_limit,
        max_user_storage_limit_bytes=max_user_storage_limit_bytes,
        before_commit=before_commit,
    )


def _delete_unpaired_latex_source(db, *, user_id: str, source_record: Files) -> None:
    """Best-effort cleanup for a new source whose companion PDF was not saved.

    Successful compilation normally creates a source/PDF pair. If PDF policy
    admission or persistence fails after the source commit, retaining that
    source would consume file count and storage quota without returning an
    artifact to the caller. Cleanup errors are logged without replacing the
    original PDF failure that explains why the tool call failed.
    """
    try:
        delete_file(
            str(user_id),
            str(source_record.id),
            db,
            FileDeleteTimeOption.ALL,
        )
    except Exception:  # pragma: no cover - cleanup failure must not mask the primary error
        logger.exception(
            "Failed to clean up unpaired LaTeX source after PDF save failure",
            extra={
                "event": "latex_unpaired_source_cleanup_failed",
                "user_id": str(user_id),
                "source_file_id": str(source_record.id),
            },
        )


def _find_zip_end_record(content: bytes) -> tuple[Any, ...] | None:
    """Return a conventional ZIP end record without allocating member objects.

    ``zipfile.ZipFile`` constructs every ``ZipInfo`` while opening an archive.
    Reading the small end record first lets us reject archives claiming an
    excessive entry count before that allocation. ZIP64 is unnecessary for the
    deliberately small renderer bundle and is rejected later.
    """
    signature = b"PK\x05\x06"
    fixed_size = 22
    minimum_offset = max(0, len(content) - (fixed_size + 65535))
    search_end = len(content)

    while search_end > minimum_offset:
        offset = content.rfind(signature, minimum_offset, search_end)
        if offset < 0:
            return None
        if len(content) - offset >= fixed_size:
            record = struct.unpack_from("<4s4H2LH", content, offset)
            comment_length = int(record[-1])
            if offset + fixed_size + comment_length == len(content):
                return tuple(int(value) if not isinstance(value, bytes) else value for value in record)
        search_end = offset
    return None


def _validate_latex_zip_container(content: bytes) -> None:
    """Reject oversized or entry-heavy renderer ZIPs before opening them."""
    if len(content) > LATEX_RENDER_MAX_RESPONSE_BYTES:
        raise LatexRenderOutputLimitError("LaTeX renderer response exceeds the safe size limit.")

    end_record = _find_zip_end_record(content)
    if end_record is None:
        # Preserve ZipFile's established BadZipFile error for malformed input.
        return

    _, disk_number, directory_disk, entries_on_disk, total_entries, directory_size, directory_offset, _ = end_record
    if disk_number != 0 or directory_disk != 0 or entries_on_disk != total_entries:
        raise RuntimeError("LaTeX renderer returned an unsupported multi-disk ZIP bundle.")
    if total_entries == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise LatexRenderOutputLimitError("LaTeX renderer returned an unsupported ZIP64 bundle.")
    if total_entries > LATEX_RENDER_MAX_ARCHIVE_ENTRIES:
        raise LatexRenderOutputLimitError("LaTeX renderer ZIP contains too many entries.")


def _read_latex_archive_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    """Read one ZIP member incrementally and enforce its real expanded size."""
    if max(0, int(info.file_size or 0)) > max_bytes:
        raise LatexRenderOutputLimitError(f"LaTeX renderer {label} exceeds the safe size limit.")

    payload = bytearray()
    with archive.open(info, "r") as source:
        while True:
            chunk = source.read(LATEX_RENDER_IO_CHUNK_BYTES)
            if not chunk:
                break
            if len(payload) + len(chunk) > max_bytes:
                raise LatexRenderOutputLimitError(f"LaTeX renderer {label} exceeds the safe size limit.")
            payload.extend(chunk)
    return bytes(payload)


def _extract_latex_bundle(content: bytes) -> tuple[bytes, str, str, dict[str, Any]]:
    """Extract the expected renderer files under strict allocation limits."""
    _validate_latex_zip_container(content)

    with zipfile.ZipFile(BytesIO(content), "r") as archive:
        infos = archive.infolist()
        if len(infos) > LATEX_RENDER_MAX_ARCHIVE_ENTRIES:
            raise LatexRenderOutputLimitError("LaTeX renderer ZIP contains too many entries.")

        entries: dict[str, zipfile.ZipInfo] = {}
        total_uncompressed = 0
        for info in infos:
            if info.filename in entries:
                raise RuntimeError("LaTeX renderer ZIP contains duplicate entries.")
            entries[info.filename] = info
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise RuntimeError("LaTeX renderer ZIP contains an encrypted entry.")
            total_uncompressed += max(0, int(info.file_size or 0))
            if total_uncompressed > LATEX_RENDER_MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise LatexRenderOutputLimitError(
                    "LaTeX renderer ZIP uncompressed contents exceed the safe size limit."
                )

        pdf_infos = [
            info
            for info in infos
            if not info.is_dir() and info.filename.lower().endswith(".pdf")
        ]
        if not pdf_infos:
            raise RuntimeError("LaTeX renderer did not return a PDF file.")
        if len(pdf_infos) != 1:
            raise RuntimeError("LaTeX renderer returned more than one PDF file.")

        pdf_bytes = _read_latex_archive_member(
            archive,
            pdf_infos[0],
            max_bytes=LATEX_RENDER_MAX_PDF_BYTES,
            label="PDF",
        )

        tex_source = ""
        source_info = entries.get("source/main.tex")
        if source_info and not source_info.is_dir():
            source_bytes = _read_latex_archive_member(
                archive,
                source_info,
                max_bytes=LATEX_RENDER_MAX_SOURCE_BYTES,
                label="source",
            )
            tex_source = source_bytes.decode("utf-8", errors="replace")

        log_text = ""
        log_info = entries.get("logs/pdflatex.log")
        if log_info and not log_info.is_dir():
            log_bytes = _read_latex_archive_member(
                archive,
                log_info,
                max_bytes=LATEX_RENDER_MAX_LOG_BYTES,
                label="compile log",
            )
            log_text = log_bytes.decode("utf-8", errors="replace")

        metadata: dict[str, Any] = {}
        metadata_info = entries.get("metadata.json")
        if metadata_info and not metadata_info.is_dir():
            metadata_bytes = _read_latex_archive_member(
                archive,
                metadata_info,
                max_bytes=LATEX_RENDER_MAX_METADATA_BYTES,
                label="metadata",
            )
            try:
                parsed = json.loads(metadata_bytes.decode("utf-8", errors="replace"))
                if isinstance(parsed, dict):
                    metadata = parsed
            except (UnicodeDecodeError, json.JSONDecodeError):
                metadata = {}

        return pdf_bytes, tex_source, log_text, metadata


def _stream_latex_render_response(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    request_payload: dict[str, Any],
) -> _BoundedLatexRenderResponse:
    """POST to one renderer endpoint and buffer no more than the safe maximum."""
    with client.stream("POST", url, json=request_payload, headers=headers) as response:
        declared_length = response.headers.get("Content-Length")
        if declared_length:
            try:
                declared_bytes = int(declared_length)
            except (TypeError, ValueError):
                declared_bytes = None
            if declared_bytes is not None and declared_bytes > LATEX_RENDER_MAX_RESPONSE_BYTES:
                raise LatexRenderOutputLimitError("LaTeX renderer response exceeds the safe size limit.")

        content = bytearray()
        for chunk in response.iter_bytes():
            if not chunk:
                continue
            if len(content) + len(chunk) > LATEX_RENDER_MAX_RESPONSE_BYTES:
                raise LatexRenderOutputLimitError("LaTeX renderer response exceeds the safe size limit.")
            content.extend(chunk)

        return _BoundedLatexRenderResponse(
            status_code=int(response.status_code),
            headers=httpx.Headers(response.headers),
            content=bytes(content),
        )


def _post_latex_render(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    request_payload: dict[str, Any],
) -> _BoundedLatexRenderResponse:
    response = _stream_latex_render_response(
        client,
        f"{base_url.rstrip('/')}/api/latex/render",
        headers,
        request_payload,
    )
    if response.status_code == 404:
        response = _stream_latex_render_response(
            client,
            f"{base_url.rstrip('/')}/api/v1/latex/render",
            headers,
            request_payload,
        )
    if response.status_code >= 400:
        detail = response.text[:1200] if response.text else "Unknown error"
        log_excerpt = ""
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                payload = parsed.get("detail") if isinstance(parsed.get("detail"), dict) else parsed
                if isinstance(payload, dict):
                    detail = str(payload.get("message") or payload.get("detail") or payload)
                    log_excerpt = str(payload.get("log_excerpt") or payload.get("log") or "")
                else:
                    detail = str(parsed.get("detail") or parsed)
        except Exception:
            pass
        raise LatexServiceRenderError(status_code=response.status_code, detail=detail, log_excerpt=log_excerpt)
    return response


def render_latex_pdf(
    db,
    *,
    user_id: str,
    tex: str | None = None,
    title: str | None = None,
    filename: str | None = None,
    file_ids: list[str] | None = None,
    source_file_id: str | None = None,
    pdf_file_id: str | None = None,
    start_snippet: str | None = None,
    end_snippet: str | None = None,
    persist_source: bool = True,
    expected_source_revision: int | None = None,
    asset_actor_user_id: str | None = None,
    audit_tool_mutations: bool = False,
    canvas_audit_context: LatexCanvasAuditContext | None = None,
) -> dict[str, Any]:
    if not user_id:
        raise ValueError("user_id is required for LaTeX PDF rendering")
    if not persist_source and canvas_audit_context is None:
        raise ValueError("canvas_audit_context is required for a Canvas LaTeX render")

    start_snippet = _normalize_optional_snippet(start_snippet)
    end_snippet = _normalize_optional_snippet(end_snippet)
    existing_source = ""
    source_record_for_update = None
    pdf_record_for_update = None
    if source_file_id:
        source_record_for_update = get_file(db, str(source_file_id), str(user_id))
        if not _is_latex_source_record(source_record_for_update):
            raise ValueError("The target LaTeX source file was not found for this user.")
        source_meta_at_start = (
            dict(source_record_for_update.meta)
            if isinstance(source_record_for_update.meta, dict)
            else {}
        )
        source_revision_at_start = int(source_meta_at_start.get("canvas_revision") or 0)
        if (
            expected_source_revision is not None
            and source_revision_at_start != int(expected_source_revision)
        ):
            raise LatexSourceRevisionConflict(
                expected_revision=int(expected_source_revision),
                current_revision=source_revision_at_start,
            )
        if start_snippet is not None or end_snippet is not None or tex is None:
            existing_source = _read_latex_source_file(db, user_id=str(user_id), source_file_id=str(source_file_id))
    if pdf_file_id:
        pdf_record_for_update = get_file(db, str(pdf_file_id), str(user_id))
        if not _is_latex_pdf_record(pdf_record_for_update):
            raise ValueError("The target LaTeX PDF file was not found for this user.")

    if tex is None and (start_snippet is not None or end_snippet is not None):
        raise ValueError("tex is required when start_snippet or end_snippet is provided.")

    tex_source_input = _apply_snippet_update(
        existing_source,
        start_snippet=start_snippet,
        end_snippet=end_snippet,
        replacement_content=str(tex if tex is not None else existing_source),
    )
    if not tex_source_input or not tex_source_input.strip():
        raise ValueError("tex is required for LaTeX PDF rendering")

    attempted_source_bytes = str(tex_source_input).encode("utf-8")

    # Resolve the complete file policy before invoking the comparatively
    # expensive external renderer. This also enforces allow_file_uploads, which
    # must apply to generated artifacts just as it does to direct uploads.
    max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(
        db,
        str(user_id),
    )
    ensure_user_file_upload_size_limit(db, str(user_id), len(attempted_source_bytes))

    # A fresh render always tries to create at least the editable source file.
    # This early admission prevents known-over-quota failed compilations from
    # consuming renderer capacity. persist_generated_file_bytes performs the
    # authoritative serialized recheck immediately before storage.
    if source_record_for_update is None:
        ensure_user_file_upload_capacity(
            db,
            str(user_id),
            len(attempted_source_bytes),
            max_files_limit=max_files_limit,
            max_user_storage_limit_bytes=max_user_storage_limit_bytes,
        )

    pdf_name = _pdf_filename(filename or title or "document.pdf")
    tex_name = _tex_filename(pdf_name)
    existing_pdf_meta = pdf_record_for_update.meta if pdf_record_for_update and isinstance(pdf_record_for_update.meta, dict) else {}
    existing_source_meta = source_record_for_update.meta if source_record_for_update and isinstance(source_record_for_update.meta, dict) else {}
    title_text = str(
        title
        or existing_pdf_meta.get("latex_display_title")
        or existing_pdf_meta.get("title")
        or existing_source_meta.get("latex_display_title")
        or existing_source_meta.get("title")
        or Path(pdf_name).stem
        or "LaTeX PDF"
    ).strip()
    if file_ids is None and source_record_for_update:
        source_meta = source_record_for_update.meta if isinstance(source_record_for_update.meta, dict) else {}
        stored_asset_ids = source_meta.get("latex_asset_file_ids")
        if isinstance(stored_asset_ids, list):
            file_ids = [str(item) for item in stored_asset_ids if str(item or "").strip()]
    if source_record_for_update is not None:
        # Source/PDF persistence remains owner-scoped, but every dependency is
        # authorized as the authenticated actor who requested this render.
        raw_input_files = prepare_canvas_asset_files_payload(
            db,
            canvas_record=source_record_for_update,
            actor_user_id=str(asset_actor_user_id or user_id),
            asset_file_ids=file_ids,
        )
    else:
        raw_input_files = _prepare_input_files_payload(
            db=db,
            user_id=str(asset_actor_user_id or user_id),
            file_ids=file_ids,
        )
    input_files_payload = _build_latex_input_files(raw_input_files)
    input_file_names = [str(item.get("file_name") or "").strip() for item in input_files_payload if item.get("file_name")]

    connections = get_service_connection_candidates(db, SERVICE_PURPOSE_LATEX_PDF)

    if not connections:
        raise ValueError("LaTeX PDF service is not configured. Add a service connection in admin settings.")

    selected_connection: dict[str, Any] | None = None
    last_service_error: Exception | None = None
    response: _BoundedLatexRenderResponse | None = None

    request_payload = {
        "tex": str(tex_source_input),
        "job_name": _safe_stem(pdf_name),
        "input_files": input_files_payload,
    }

    for connection in connections:
        if not connection:
            continue
        base_url = str(connection.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            continue
        headers = _connection_headers(connection)
        try:
            try:
                assert_url_allowed(db, url=base_url, feature="LaTeX PDF service")
            except OutboundRequestBlockedError as exc:
                raise RuntimeError(str(exc)) from exc

            with httpx.Client(timeout=180) as client:
                _check_service_health(client=client, base_url=base_url, headers=headers)
                record_service_connection_runtime_status(
                    db,
                    connection,
                    SERVICE_PURPOSE_LATEX_PDF,
                    available=True,
                    message="Available",
                )
                response = _post_latex_render(
                    client=client,
                    base_url=base_url,
                    headers=headers,
                    request_payload=request_payload,
                )
            selected_connection = connection
            break
        except _CodeExecutionServiceUnavailableError as exc:
            last_service_error = RuntimeError(str(exc).replace("Code execution service", "LaTeX PDF service"))
            record_service_connection_runtime_status(
                db,
                connection,
                SERVICE_PURPOSE_LATEX_PDF,
                available=False,
                message=str(last_service_error),
                failure_scope="service",
            )
            continue
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            last_service_error = RuntimeError(f"Failed to reach LaTeX PDF service: {exc}")
            continue
        except LatexServiceRenderError as exc:
            if persist_source:
                saved_source = _persist_latex_source_attempt(
                    db,
                    user_id=str(user_id),
                    source_record_for_update=source_record_for_update,
                    tex_name=tex_name,
                    title_text=title_text,
                    source_bytes=attempted_source_bytes,
                    pdf_file_id=str(pdf_file_id or ""),
                    file_ids=file_ids,
                    input_file_names=input_file_names,
                    max_files_limit=max_files_limit,
                    max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                    compile_failed=True,
                    log_excerpt=exc.log_excerpt,
                    before_commit=(
                        lambda saved_record: stage_tool_audit_action(
                            db,
                            str(user_id),
                            (
                                "LATEX_SOURCE_UPDATED"
                                if source_record_for_update
                                else "LATEX_SOURCE_CREATED"
                            ),
                            category="files",
                            details={
                                "source_file_id": str(saved_record.id),
                                "compile_failed": True,
                            },
                        )
                        if audit_tool_mutations
                        else None
                    ),
                )
            elif source_record_for_update:
                saved_source = _record_canvas_render_state(
                    db,
                    source_record=source_record_for_update,
                    expected_revision=expected_source_revision,
                    status="failed",
                    log_excerpt=exc.log_excerpt,
                    before_commit=(
                        lambda saved_record: _stage_canvas_latex_audit(
                            db,
                            canvas_audit_context,
                            "CANVAS_LATEX_RENDER_FAILED",
                            {
                                "source_file_id": str(saved_record.id),
                                "source_revision": _canvas_revision(saved_record),
                                "asset_count": sum(
                                    1
                                    for item in (file_ids or [])
                                    if str(item or "").strip()
                                ),
                            },
                            reason="compile_failed",
                        )
                        if canvas_audit_context is not None
                        else None
                    ),
                )
            else:  # Defensive: non-persisting renders always require a source.
                raise ValueError("source_file_id is required for a Canvas LaTeX render.") from exc
            raise LatexCompileError(str(exc), source_file_id=str(saved_source.id), log_excerpt=exc.log_excerpt) from exc
        except RuntimeError as exc:
            if (
                _is_retryable_code_service_error(exc)
                or "code execution service blocked" in str(exc).lower()
                or "latex pdf service blocked" in str(exc).lower()
            ):
                last_service_error = exc
                continue
            raise

    if response is None:
        if last_service_error:
            raise RuntimeError(str(last_service_error))
        raise RuntimeError("No available LaTeX PDF service connection could handle the render request.")

    pdf_bytes, tex_source, log_text, metadata = _extract_latex_bundle(response.content)
    source_bytes = (tex_source or str(tex_source_input)).encode("utf-8")

    # Validate renderer-controlled PDF output before committing a new source.
    # The authoritative quota check still runs inside the persistence helper.
    ensure_user_file_upload_size_limit(db, str(user_id), len(pdf_bytes))
    service_connection_meta = {
        "id": selected_connection.get("id") if selected_connection else "",
        "name": selected_connection.get("name") if selected_connection else "",
        "base_url": selected_connection.get("base_url") if selected_connection else "",
        "legacy": bool(selected_connection.get("legacy")) if selected_connection else False,
    }

    if persist_source:
        source_record = _persist_latex_source_attempt(
            db,
            user_id=str(user_id),
            source_record_for_update=source_record_for_update,
            tex_name=tex_name,
            title_text=title_text,
            source_bytes=source_bytes,
            pdf_file_id=str(pdf_file_id or ""),
            file_ids=file_ids,
            input_file_names=input_file_names,
            max_files_limit=max_files_limit,
            max_user_storage_limit_bytes=max_user_storage_limit_bytes,
            compile_failed=False,
            before_commit=(
                lambda saved_record: stage_tool_audit_action(
                    db,
                    str(user_id),
                    (
                        "LATEX_SOURCE_UPDATED"
                        if source_record_for_update is not None
                        else "LATEX_SOURCE_CREATED"
                    ),
                    category="files",
                    details={
                        "source_file_id": str(saved_record.id),
                        "compile_failed": False,
                    },
                )
                if audit_tool_mutations
                else None
            ),
        )
    elif source_record_for_update:
        # The .tex file is the canonical Canvas artifact. The renderer may
        # echo source/main.tex in its bundle, but preview generation must never
        # rewrite user/model edits or increment their revision. Refresh and
        # validate immediately before PDF persistence, but deliberately avoid
        # committing an intermediate "rendering" state: persistence failures
        # and concurrent edits must leave the prior terminal state intact.
        if hasattr(db, "refresh"):
            db.refresh(source_record_for_update)
        refreshed_source_meta = (
            dict(source_record_for_update.meta)
            if isinstance(source_record_for_update.meta, dict)
            else {}
        )
        current_source_revision = int(
            refreshed_source_meta.get("canvas_revision") or 0
        )
        if (
            expected_source_revision is not None
            and current_source_revision != int(expected_source_revision)
        ):
            raise LatexSourceRevisionConflict(
                expected_revision=int(expected_source_revision),
                current_revision=current_source_revision,
            )
        source_record = source_record_for_update
    else:
        raise ValueError("source_file_id is required for a Canvas LaTeX render.")

    source_meta_for_pdf = source_record.meta if isinstance(source_record.meta, dict) else {}
    rendered_source_revision = int(source_meta_for_pdf.get("canvas_revision") or 0)

    pdf_meta = {
        "original_filename": pdf_name,
        "origin": "assistant",
        "render_latex_pdf": True,
        "latex_pdf": True,
        "latex_display_title": title_text,
        "title": title_text,
        "latex_source_file_id": source_record.id,
        "latex_source_revision": rendered_source_revision,
        "latex_compiler": metadata.get("compiler") or response.headers.get("X-LaTeX-Compiler") or "pdflatex",
        "latex_log_excerpt": log_text[-4000:] if log_text else "",
        "latex_asset_file_ids": [str(item) for item in (file_ids or []) if str(item or "").strip()],
        "canvas_asset_references": copy_canvas_asset_references(source_record),
        "latex_input_file_names": input_file_names,
        "service_connection": service_connection_meta,
        "generated_at": _utc_iso(),
    }

    def finalize_pdf_persistence(saved_pdf: Files) -> None:
        # Link the derivative to the exact rendered source revision in the same
        # transaction that publishes the PDF. A concurrent source edit aborts
        # the PDF write instead of leaving an older render attached to it.
        if hasattr(db, "refresh"):
            db.refresh(source_record)
        current_source_meta = (
            dict(source_record.meta)
            if isinstance(source_record.meta, dict)
            else {}
        )
        current_revision = int(current_source_meta.get("canvas_revision") or 0)
        if current_revision != rendered_source_revision:
            raise LatexSourceRevisionConflict(
                expected_revision=rendered_source_revision,
                current_revision=current_revision,
            )
        current_source_meta.update(
            {
                "latex_pdf_file_id": saved_pdf.id,
                "latex_pdf_file_name": pdf_name,
                "latex_display_title": title_text,
                "title": title_text,
                "latex_render_revision": rendered_source_revision,
                "latex_render_status": "ready",
                "latex_compile_failed": False,
                "latex_log_excerpt": log_text[-4000:] if log_text else "",
                "latex_asset_file_ids": [
                    str(item)
                    for item in (file_ids or [])
                    if str(item or "").strip()
                ],
                "latex_input_file_names": input_file_names,
            }
        )
        source_record.meta = current_source_meta
        db.add(source_record)

        if canvas_audit_context is not None:
            _stage_canvas_latex_audit(
                db,
                canvas_audit_context,
                "CANVAS_LATEX_RENDERED",
                {
                    "source_file_id": str(source_record.id),
                    "pdf_file_id": str(saved_pdf.id),
                    "source_revision": rendered_source_revision,
                    "asset_count": sum(
                        1
                        for item in (file_ids or [])
                        if str(item or "").strip()
                    ),
                },
            )

        if audit_tool_mutations:
            stage_tool_audit_action(
                db,
                str(user_id),
                "LATEX_PDF_UPDATED" if pdf_record_for_update else "LATEX_PDF_CREATED",
                category="files",
                details={
                    "source_file_id": str(source_record.id),
                    "pdf_file_id": str(saved_pdf.id),
                    "source_revision": rendered_source_revision,
                },
            )

    try:
        if pdf_record_for_update:
            pdf_record = _overwrite_generated_file_bytes(
                db,
                user_id=str(user_id),
                file_record=pdf_record_for_update,
                original_filename=pdf_name,
                file_bytes=pdf_bytes,
                file_type="application/pdf",
                file_category=get_file_category("application/pdf"),
                meta=pdf_meta,
                folder_id=getattr(source_record, "folder_id", None),
                project_id=getattr(source_record, "project_id", None),
                update_location=True,
                before_commit=finalize_pdf_persistence,
            )
        else:
            pdf_record = persist_generated_file_bytes(
                db,
                user_id=str(user_id),
                original_filename=pdf_name,
                file_bytes=pdf_bytes,
                file_type="application/pdf",
                file_category=get_file_category("application/pdf"),
                file_id=str(uuid.uuid4()),
                meta=pdf_meta,
                folder_id=getattr(source_record, "folder_id", None),
                project_id=getattr(source_record, "project_id", None),
                max_files_limit=max_files_limit,
                max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                before_commit=finalize_pdf_persistence,
            )
    except Exception:
        # A fresh source is not useful to the caller when successful
        # compilation cannot produce its companion PDF. Existing sources are
        # intentionally retained because deleting them would destroy user data.
        if source_record_for_update is None:
            _delete_unpaired_latex_source(
                db,
                user_id=str(user_id),
                source_record=source_record,
            )
        raise

    if hasattr(db, "refresh"):
        db.refresh(source_record)

    return {
        "file_id": pdf_record.id,
        "source_file_id": source_record.id,
        "file_name": pdf_name,
        "source_file_name": tex_name,
        "title": title_text,
        "mime_type": "application/pdf",
        "size": len(pdf_bytes),
        "compiler": metadata.get("compiler") or "pdflatex",
        "execution_time": metadata.get("execution_time"),
        "log_excerpt": log_text[-4000:] if log_text else "",
        "input_files_loaded": len(input_files_payload),
        "input_file_names": input_file_names,
        "asset_file_ids": [str(item) for item in (file_ids or []) if str(item or "").strip()],
        "source_revision": rendered_source_revision,
        "render_revision": rendered_source_revision,
        "render_status": "ready",
        "service_connection": service_connection_meta,
    }


def render_latex_canvas(
    db,
    *,
    user_id: str,
    asset_actor_user_id: str | None = None,
    source_file_id: str,
    expected_revision: int | None = None,
    audit_ip_address: str | None = None,
    audit_user_agent: str | None = None,
) -> dict[str, Any]:
    """Render one stored LaTeX Canvas without accepting a competing source body.

    The source record is the stable artifact identity. A PDF is an overwriteable
    derivative cached against ``canvas_revision`` and can always be regenerated.
    """
    source_record = get_file(db, str(source_file_id), str(user_id))
    if not _is_latex_source_record(source_record):
        raise ValueError("The target LaTeX Canvas was not found for this user.")
    meta = dict(source_record.meta) if isinstance(source_record.meta, dict) else {}
    if str(meta.get("canvas_type") or "").strip().lower() not in {"", "latex"}:
        raise ValueError("Only LaTeX Canvas files can be rendered as PDF.")

    audit_context = LatexCanvasAuditContext(
        actor_user_id=str(asset_actor_user_id or user_id),
        ip_address=audit_ip_address,
        user_agent=audit_user_agent,
    )

    if str(meta.get("canvas_type") or "").strip().lower() != "latex":
        # Opening a normal uploaded .tex file in Canvas should be useful on the
        # first try. Adopt it in place without moving folders or rewriting its
        # bytes; subsequent edits and renders then share the same revisioned
        # source-of-truth contract as model-created documents.
        meta.update(
            {
                "canvas": True,
                "canvas_type": "latex",
                "canvas_revision": max(1, int(meta.get("canvas_revision") or 0)),
                "canvas_last_edited_at": meta.get("canvas_last_edited_at") or _utc_iso(),
                "canvas_last_edited_by": meta.get("canvas_last_edited_by") or str(user_id),
                "canvas_last_edit_source": meta.get("canvas_last_edit_source") or "user",
                "latex_source": True,
                "latex_render_status": "not_rendered",
            }
        )
        source_record.meta = meta
        _commit_latex_record(
            db,
            source_record,
            before_commit=lambda saved_record: _stage_canvas_latex_audit(
                db,
                audit_context,
                "CANVAS_LATEX_ADOPTED",
                {
                    "source_file_id": str(saved_record.id),
                    "source_revision": int(meta["canvas_revision"]),
                },
            ),
        )
        if expected_revision is None:
            expected_revision = int(meta["canvas_revision"])

    pdf_file_id = str(meta.get("latex_pdf_file_id") or "").strip() or None
    if pdf_file_id and not _is_latex_pdf_record(get_file(db, pdf_file_id, str(user_id))):
        # A deleted or manually replaced derivative must not prevent a fresh
        # render. A new stable PDF record will be linked after compilation.
        pdf_file_id = None

    title = str(
        meta.get("latex_display_title")
        or meta.get("title")
        or Path(str(meta.get("original_filename") or source_record.file_name or "document.tex")).stem
        or "LaTeX document"
    )
    pdf_name = str(meta.get("latex_pdf_file_name") or f"{_safe_stem(title)}.pdf")
    asset_ids = meta.get("latex_asset_file_ids")
    return render_latex_pdf(
        db,
        user_id=str(user_id),
        tex=None,
        title=title,
        filename=pdf_name,
        file_ids=list(asset_ids) if isinstance(asset_ids, list) else [],
        source_file_id=str(source_file_id),
        pdf_file_id=pdf_file_id,
        persist_source=False,
        expected_source_revision=expected_revision,
        asset_actor_user_id=str(asset_actor_user_id or user_id),
        canvas_audit_context=audit_context,
    )
