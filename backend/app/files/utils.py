from sqlalchemy.dialects.postgresql import JSONB
from fastapi import UploadFile, HTTPException
from fastapi.responses import FileResponse
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy import cast, func, text
from pathlib import Path
from xml.etree.ElementTree import ParseError
import mimetypes
import datetime
import logging
import threading
import hashlib
import struct
import uuid
import os
import zipfile
import zlib

import anyio
import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from app.files.models import (
    FileArtifactShare,
    FileQuotaReservation,
    Files,
    create_file,
    get_file,
    list_files,
)
from app.file_folders.models import can_user_edit_folder
from app.files.access import get_accessible_file
from app.files.content_credentials import (
    apply_content_credentials,
    content_credentials_meta,
    is_supported_ai_generated_media,
)
from app.automations.models import remove_file_from_automations
from app.files.reference_cleanup import cleanup_file_references
from app.files.schemas import (
    allowed_audio_types,
    allowed_video_types,
    allowed_image_types,
    allowed_document_types,
    SUPPORTED_EXTRACT_TEXT_MIME_TYPES,
    MARKITDOWN_MIME_TYPES,
    SVG_MIME_TYPE,
    HTML_ATTACHMENT_MIME_TYPES,
    TEXT_EXTRACTED_DOCUMENT_MIME_TYPES,
    FileDeleteTimeOption,
)
from app.projects.models import get_project
from app.groups.init import get_user_group_setting_value
from app.files.storage import (
    build_storage_key,
    ensure_user_scoped_storage_key,
    delete_file_from_storage,
    download_file_from_storage,
    get_local_user_files_base_dir,
    resolve_local_storage_path,
    upload_file_to_storage,
)
from app.telemetry.metrics import record_file_upload_metric
from app.utils.blocking_io import run_blocking_io



logger = logging.getLogger(__name__)


# XLSX files are ZIP containers whose compressed size can be dramatically
# smaller than the XML that a browser must parse.  These limits bound both the
# central-directory allocation and the actual bytes expanded while validating
# a workbook.  The browser editor only receives snapshots that passed this
# validation, so a small ZIP bomb cannot move the resource-exhaustion problem
# from the server to every client that opens the file.
SPREADSHEET_ARCHIVE_MAX_ENTRIES = 4_096
SPREADSHEET_ARCHIVE_MAX_ENTRY_BYTES = 64 * 1024 * 1024
SPREADSHEET_ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
SPREADSHEET_ARCHIVE_IO_CHUNK_BYTES = 1024 * 1024
SPREADSHEET_ARCHIVE_ERROR_CODE = "spreadsheet_archive_too_complex"
_XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SpreadsheetArchiveValidationError(ValueError):
    """Safe rejection for a malformed or excessively complex XLSX package."""

    code = SPREADSHEET_ARCHIVE_ERROR_CODE


_USER_FILE_QUOTA_LOCKS: dict[str, threading.RLock] = {}
_USER_FILE_QUOTA_LOCKS_GUARD = threading.Lock()
_USER_FILE_QUOTA_LOCK_PREFIX = "omlorix:user-file-quota:"
_USER_FILE_QUOTA_RESERVATION_TTL = datetime.timedelta(hours=1)

USER_FILE_STORAGE_QUOTA_REACHED = "user_file_storage_quota_reached"
USER_FILE_COUNT_QUOTA_REACHED = "user_file_count_quota_reached"
USER_FILE_UPLOADS_DISABLED = "user_file_uploads_disabled"


class FileQuotaError(HTTPException):
    """A stable, user-safe file admission denial shared by every file path."""

    def __init__(self, *, code: str, message: str, status_code: int = 400) -> None:
        normalized_code = str(code or "").strip()
        normalized_message = str(message or "").strip()
        if not normalized_code or not normalized_message:
            raise ValueError("File quota errors require a stable code and message")
        self.code = normalized_code
        self.safe_message = normalized_message
        super().__init__(
            status_code=status_code,
            detail=normalized_message,
            headers={"X-Omlorix-Error-Code": normalized_code},
        )

    def as_safe_tool_error(self):
        """Convert this HTTP-oriented denial into the shared safe tool error."""

        from app.tools.errors import SafeToolExecutionError

        return SafeToolExecutionError(
            code=self.code,
            safe_message=self.safe_message,
            detail=self.safe_message,
            allow_same_response_retry=False,
        )


@dataclass(frozen=True)
class FileQuotaReservationContext:
    """A committed, short-lived reservation for pending durable file output."""

    reservation_id: str
    user_id: str
    reserved_files: int
    reserved_bytes: int
    purpose: str
    expires_at: datetime.datetime


CUSTOM_MIME_TYPE_MAP = {
    ".pages": "application/vnd.apple.pages",
    ".numbers": "application/vnd.apple.numbers",
    ".key": "application/vnd.apple.keynote",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ott": "application/vnd.oasis.opendocument.text-template",
    ".odm": "application/vnd.oasis.opendocument.text-master",
    ".oth": "application/vnd.oasis.opendocument.text-web",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".ots": "application/vnd.oasis.opendocument.spreadsheet-template",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".otp": "application/vnd.oasis.opendocument.presentation-template",
    ".odg": "application/vnd.oasis.opendocument.graphics",
    ".otg": "application/vnd.oasis.opendocument.graphics-template",
    ".odf": "application/vnd.oasis.opendocument.formula",
    ".odc": "application/vnd.oasis.opendocument.chart",
    ".odb": "application/vnd.oasis.opendocument.database",
    ".sxw": "application/vnd.sun.xml.writer",
    ".stw": "application/vnd.sun.xml.writer.template",
    ".sxc": "application/vnd.sun.xml.calc",
    ".sxi": "application/vnd.sun.xml.impress",
}

for extension, mime in CUSTOM_MIME_TYPE_MAP.items():
    mimetypes.add_type(mime, extension, strict=False)

_AVIF_BRANDS = {b"avif", b"avis"}
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx"}
_HEIF_BRANDS = {b"mif1", b"msf1"}



# -------------------
# Variables
# -------------------
TEMP_CLEANUP_INTERVAL_SECONDS = 3600
BASE_STORAGE_DIR = get_local_user_files_base_dir()
TEMP_DIR = BASE_STORAGE_DIR / "temp"
# Materialized files are temporary local copies for cloud-backed file records.
MATERIALIZED_TEMP_DIR = TEMP_DIR / "materialized"
# Create directories if they don't exist
BASE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
MATERIALIZED_TEMP_DIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_SIZE = 512 * 1024 * 1024  # 512MB
CHUNK_SIZE = 8192  # 8KB chunks for streaming
SVG_DANGEROUS_ELEMENT_LOCAL_NAMES = frozenset({
    "animate",
    "embed",
    "foreignobject",
    "iframe",
    "link",
    "object",
    "script",
    "set",
    "use",
})
SVG_DANGEROUS_URI_SCHEMES = (
    "javascript:",
    "vbscript:",
    "data:text/html",
    "data:application/javascript",
    "data:image/svg+xml",
)
INLINE_UNSAFE_MIME_TYPES = {
    *HTML_ATTACHMENT_MIME_TYPES,
    "image/svg+xml",
}
INLINE_SAME_ORIGIN_FRAME_MIME_TYPES = {
    "application/pdf",
}
INLINE_SAME_ORIGIN_FRAME_CSP = "frame-ancestors 'self'"


def _resolve_local_storage_path(storage_key: str, *, create_parent: bool = False) -> Path:
    """Resolve a local storage key and ensure it stays under the storage base."""
    return resolve_local_storage_path(BASE_STORAGE_DIR, storage_key, create_parent=create_parent)



# -------------------
# Get file category
# -------------------
def normalize_file_mime_type(file_type: str | None) -> str:
    """Return a lowercase MIME type without optional parameters.

    Attachment classification and security checks must agree for values such
    as ``image/svg+xml; charset=utf-8``. MIME parameters describe transport
    details and must not change the semantic file category.
    """
    return str(file_type or "").split(";", 1)[0].strip().lower()


def is_inline_unsafe_mime_type(file_type: str | None) -> bool:
    """Return whether a stored MIME type must never become an inline document.

    Keep alternate raw-content routes on the same active-content boundary as
    the ordinary download helper. Filename extensions and persisted metadata
    are useful format hints, but neither is allowed to override this result.
    """
    return normalize_file_mime_type(file_type) in INLINE_UNSAFE_MIME_TYPES


def get_file_category(file_type: str) -> str:
    """
    Determine file category based on MIME type.

    SVG deliberately belongs to the document category. Although its MIME type
    starts with ``image/``, the chat pipeline supplies its XML source to models
    as extracted text because provider vision APIs generally reject SVG.
    """
    normalized_file_type = normalize_file_mime_type(file_type)
    if normalized_file_type in allowed_audio_types:
        return "audio"
    elif normalized_file_type in allowed_video_types:
        return "video"
    elif normalized_file_type in allowed_image_types:
        return "image"
    elif (
        normalized_file_type in allowed_document_types
        or normalized_file_type in TEXT_EXTRACTED_DOCUMENT_MIME_TYPES
    ):
        return "document"
    else:
        return "unknown"



# -------------------
# Validate file type
# -------------------
def validate_file_type(file_type: str, *, allow_html_attachment: bool = False) -> bool:
    """Check whether a MIME type is allowed for the requested storage path.

    HTML remains excluded by default because agent assets, skills, imports, and
    other callers may expose files differently. The ordinary user-file upload
    path opts in only after guaranteeing attachment-only downloads and an inert
    source/text preview.
    """
    all_allowed_types = (
        allowed_audio_types +
        allowed_video_types +
        allowed_image_types +
        allowed_document_types
    )
    normalized_file_type = normalize_file_mime_type(file_type)
    if allow_html_attachment and normalized_file_type in HTML_ATTACHMENT_MIME_TYPES:
        return True
    return normalized_file_type in all_allowed_types



# -------------------
# Find duplicate file
# -------------------
def _find_duplicate_file(
    db: Session,
    user_id: str,
    original_filename: str,
    file_type: str,
    file_size: int,
    content_sha256: str | None = None,
    project_id: str | None = None,
    folder_id: str | None = None,
) -> Optional[Files]:
    """Find duplicate file within the same destination scope."""
    candidates = (
        db.query(Files)
        .filter(
            Files.user_id == user_id,
            Files.file_type == file_type,
            Files.file_size == file_size,
            Files.project_id == project_id,
            Files.folder_id == folder_id,
        )
        .all()
    )

    for candidate in candidates:
        meta = candidate.meta if isinstance(candidate.meta, dict) else {}
        stored_original = meta.get("original_filename") or ""
        if stored_original != original_filename:
            continue

        if content_sha256 is not None:
            stored_sha256 = meta.get("sha256") or meta.get("content_sha256")
            if not stored_sha256 or stored_sha256 != content_sha256:
                continue

        if stored_original == original_filename:
            return candidate

    return None


def _detect_mime_from_sample(sample: bytes) -> str | None:
    """Infer MIME type from common binary signatures or text markers."""
    if not sample:
        return None

    if sample.startswith(b"%PDF-"):
        return "application/pdf"
    if sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if sample.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if sample.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if sample.startswith(b"BM"):
        return "image/bmp"
    if sample.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(sample) >= 12 and sample.startswith(b"RIFF") and sample[8:12] == b"WEBP":
        return "image/webp"
    if len(sample) >= 12 and sample[4:8] == b"ftyp":
        brands = {sample[8:12]}
        brands.update(sample[index:index + 4] for index in range(16, min(len(sample), 64), 4))
        if brands & _AVIF_BRANDS:
            return "image/avif"
        if brands & _HEIC_BRANDS:
            return "image/heic"
        if brands & _HEIF_BRANDS:
            return "image/heif"

    lowered = sample.decode("utf-8", errors="ignore").strip().lower()
    if lowered:
        if "<svg" in lowered:
            return "image/svg+xml"
        if "<!doctype html" in lowered or "<html" in lowered:
            return "text/html"

    return None


def _detect_mime_from_content(path: Path, fallback: str | None = None) -> str | None:
    """Detect MIME type from file content, optionally falling back when detection is inconclusive."""
    fallback_value = str(fallback or "").strip().lower() or None

    try:
        from magic import Magic

        magic_detector = Magic(mime=True)
        detected = magic_detector.from_file(str(path))
        if isinstance(detected, str):
            cleaned = detected.strip().lower()
            if cleaned:
                return cleaned
    except Exception:
        pass

    try:
        sample = path.read_bytes()[:8192]
        detected = _detect_mime_from_sample(sample)
        if detected:
            return detected
    except Exception:
        pass

    return fallback_value


def _upload_is_valid_active_content(
    upload_mime: str,
    payload_path: Path,
    *,
    allow_html_attachment: bool = False,
) -> bool:
    """Check whether uploaded markup is safe for the selected storage path.

    HTML is safe to retain as inert source text only on the user-file path,
    where download responses force ``attachment`` and previews do not execute
    the original response. Other upload surfaces keep the historical deny rule.
    """
    mime = normalize_file_mime_type(upload_mime)

    if mime in HTML_ATTACHMENT_MIME_TYPES:
        return allow_html_attachment

    if mime != "image/svg+xml":
        return True

    def local_name(qualified_name: str) -> str:
        """Return an XML element or attribute name without its namespace."""
        return str(qualified_name).rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()

    root_seen = False
    try:
        # Parse the complete document so namespace prefixes and payloads beyond
        # the former sample limit cannot hide active SVG content. DefusedXML
        # also rejects DTD/entity constructs before they reach ElementTree.
        for event, node in DefusedET.iterparse(payload_path, events=("start", "end")):
            if event == "end":
                node.clear()
                continue

            element_name = local_name(node.tag)
            if not root_seen:
                root_seen = True
                if element_name != "svg":
                    return False
            if element_name in SVG_DANGEROUS_ELEMENT_LOCAL_NAMES:
                return False

            for qualified_attribute_name, attribute_value in node.attrib.items():
                attribute_name = local_name(qualified_attribute_name)
                if attribute_name.startswith("on"):
                    return False

                # Historically xlink:href was rejected outright. Resolve the
                # namespace before comparing so alternate prefixes cannot evade
                # that rule while regular fragment hrefs remain compatible.
                is_namespaced = str(qualified_attribute_name).startswith("{")
                if attribute_name == "href" and is_namespaced:
                    return False

                compact_value = "".join(str(attribute_value).split()).lower()
                if any(scheme in compact_value for scheme in SVG_DANGEROUS_URI_SCHEMES):
                    return False
    except (DefusedXmlException, ParseError, OSError, TypeError, ValueError):
        return False

    return root_seen


def _spreadsheet_zip_entry_count(payload_path: Path) -> int | None:
    """Read the conventional ZIP end record without constructing member objects.

    Opening a ZIP with ``zipfile.ZipFile`` allocates one ``ZipInfo`` per member.
    Inspecting the small end record first lets an entry-flood archive be
    rejected before that attacker-controlled allocation. XLSX does not need
    multi-disk or ZIP64 containers within Omlorix's upload-size limits.
    """
    fixed_size = 22
    maximum_tail_size = fixed_size + 65_535
    file_size = payload_path.stat().st_size
    with payload_path.open("rb") as source:
        source.seek(max(0, file_size - maximum_tail_size))
        tail = source.read(maximum_tail_size)

    signature = b"PK\x05\x06"
    search_end = len(tail)
    while search_end > 0:
        offset = tail.rfind(signature, 0, search_end)
        if offset < 0:
            return None
        if len(tail) - offset >= fixed_size:
            record = struct.unpack_from("<4s4H2LH", tail, offset)
            comment_length = int(record[-1])
            if offset + fixed_size + comment_length == len(tail):
                (
                    _,
                    disk_number,
                    directory_disk,
                    entries_on_disk,
                    total_entries,
                    directory_size,
                    directory_offset,
                    _,
                ) = record
                if (
                    disk_number != 0
                    or directory_disk != 0
                    or entries_on_disk != total_entries
                    or total_entries == 0xFFFF
                    or directory_size == 0xFFFFFFFF
                    or directory_offset == 0xFFFFFFFF
                ):
                    raise SpreadsheetArchiveValidationError(
                        "Unsupported spreadsheet ZIP container"
                    )
                return int(total_entries)
        search_end = offset
    return None


def validate_spreadsheet_archive(
    payload_path: Path,
    *,
    file_type: str,
) -> None:
    """Validate the real expanded size and structure of an XLSX snapshot.

    CSV, TSV, and legacy XLS files are not ZIP containers and are already
    bounded by the ordinary upload and preview byte limits. XLSX members are
    streamed instead of accumulated so validation itself has constant memory
    use and stops as soon as either per-entry or total expansion exceeds the
    browser-safe budget.
    """
    if normalize_file_mime_type(file_type) != _XLSX_MIME_TYPE:
        return

    try:
        claimed_entry_count = _spreadsheet_zip_entry_count(payload_path)
        if (
            claimed_entry_count is not None
            and claimed_entry_count > SPREADSHEET_ARCHIVE_MAX_ENTRIES
        ):
            raise SpreadsheetArchiveValidationError(
                "Spreadsheet archive contains too many entries"
            )

        with zipfile.ZipFile(payload_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > SPREADSHEET_ARCHIVE_MAX_ENTRIES:
                raise SpreadsheetArchiveValidationError(
                    "Spreadsheet archive contains too many entries"
                )

            entry_names: set[str] = set()
            total_declared_size = 0
            for info in infos:
                if info.filename in entry_names:
                    raise SpreadsheetArchiveValidationError(
                        "Spreadsheet archive contains duplicate entries"
                    )
                entry_names.add(info.filename)
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise SpreadsheetArchiveValidationError(
                        "Encrypted spreadsheet archives are unsupported"
                    )
                declared_size = max(0, int(info.file_size or 0))
                if declared_size > SPREADSHEET_ARCHIVE_MAX_ENTRY_BYTES:
                    raise SpreadsheetArchiveValidationError(
                        "Spreadsheet archive entry is too large"
                    )
                total_declared_size += declared_size
                if total_declared_size > SPREADSHEET_ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise SpreadsheetArchiveValidationError(
                        "Spreadsheet archive expands beyond the safe limit"
                    )

            required_entries = {"[Content_Types].xml", "xl/workbook.xml"}
            if not required_entries.issubset(entry_names):
                raise SpreadsheetArchiveValidationError(
                    "Spreadsheet archive is missing required workbook data"
                )

            total_expanded_size = 0
            for info in infos:
                if info.is_dir():
                    continue
                declared_size = max(0, int(info.file_size or 0))
                entry_expanded_size = 0
                entry_crc = 0
                # ZipExtFile normally stops at the central directory's claimed
                # uncompressed size. A hostile package can understate that
                # field while retaining a large valid deflate stream. Give the
                # reader a bounded artificial ceiling, count its real output,
                # and independently verify both size and CRC afterwards.
                info.file_size = max(
                    SPREADSHEET_ARCHIVE_MAX_ENTRY_BYTES,
                    SPREADSHEET_ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES,
                ) + 1
                try:
                    with archive.open(info, "r") as source:
                        while True:
                            chunk = source.read(SPREADSHEET_ARCHIVE_IO_CHUNK_BYTES)
                            if not chunk:
                                break
                            entry_crc = zlib.crc32(chunk, entry_crc)
                            entry_expanded_size += len(chunk)
                            total_expanded_size += len(chunk)
                            if (
                                entry_expanded_size
                                > SPREADSHEET_ARCHIVE_MAX_ENTRY_BYTES
                                or total_expanded_size
                                > SPREADSHEET_ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES
                            ):
                                raise SpreadsheetArchiveValidationError(
                                    "Spreadsheet archive expands beyond the safe limit"
                                )
                finally:
                    info.file_size = declared_size

                if entry_expanded_size != declared_size or (
                    entry_crc & 0xFFFFFFFF
                ) != int(info.CRC):
                    raise SpreadsheetArchiveValidationError(
                        "Spreadsheet archive entry metadata is inconsistent"
                    )
    except SpreadsheetArchiveValidationError:
        raise
    except (
        EOFError,
        OSError,
        RuntimeError,
        ValueError,
        NotImplementedError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ) as exc:
        raise SpreadsheetArchiveValidationError(
            "Spreadsheet archive could not be validated"
        ) from exc


def guess_file_mime_from_name(filename: str | None) -> str | None:
    """Guess MIME type from a filename using the same fallbacks as uploads."""
    guessed_mime, _ = mimetypes.guess_type(filename or "")
    if guessed_mime:
        return guessed_mime
    if filename:
        return CUSTOM_MIME_TYPE_MAP.get(Path(filename).suffix.lower())
    return None


def detect_and_validate_upload_mime(
    payload_path: Path,
    *,
    fallback_mime: str | None = None,
    allow_html_attachment: bool = False,
) -> str:
    """Detect and validate an uploaded file's MIME type from its content."""
    detected_mime = _detect_mime_from_content(
        payload_path,
        fallback=fallback_mime or "application/octet-stream",
    )

    if not validate_file_type(
        detected_mime,
        allow_html_attachment=allow_html_attachment,
    ):
        raise HTTPException(status_code=400, detail=f"File type {detected_mime} is not allowed")

    if not _upload_is_valid_active_content(
        detected_mime,
        payload_path,
        allow_html_attachment=allow_html_attachment,
    ):
        raise HTTPException(status_code=400, detail=f"File type {detected_mime} is not allowed")

    try:
        validate_spreadsheet_archive(payload_path, file_type=detected_mime)
    except SpreadsheetArchiveValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc

    return detected_mime


def validate_upload_file(payload_path: Path, *, fallback_mime: str | None = None) -> str:
    """Detect and validate an uploaded file before storage."""
    return detect_and_validate_upload_mime(payload_path, fallback_mime=fallback_mime)


def _resolve_file_upload_permission(db: Session, user_id: str) -> None:
    """Fail closed when manual file uploads are disabled or misconfigured."""

    try:
        allow_uploads_config = get_user_group_setting_value(user_id, "files", "allow_file_uploads", db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "[Files] Failed to load allow file uploads configuration",
            extra={
                "event": "file_upload_permission_lookup_failed",
                "user_id": user_id,
            },
        )
        raise HTTPException(status_code=500, detail="Failed to validate file upload permission") from exc

    if isinstance(allow_uploads_config, str):
        normalized_allow = allow_uploads_config.strip().lower()
        if normalized_allow in {"true", "1", "yes", "on"}:
            allow_uploads = True
        elif normalized_allow in {"false", "0", "no", "off"}:
            allow_uploads = False
        else:
            logger.error(
                "[Files] Invalid allow file uploads configuration",
                extra={
                    "event": "file_upload_permission_invalid",
                    "user_id": user_id,
                    "allow_uploads_config": allow_uploads_config,
                },
            )
            raise HTTPException(status_code=500, detail="File upload permission misconfigured")
    elif isinstance(allow_uploads_config, bool) or allow_uploads_config is None:
        allow_uploads = True if allow_uploads_config is None else allow_uploads_config
    else:
        logger.error(
            "[Files] Unsupported allow file uploads configuration type",
            extra={
                "event": "file_upload_permission_type_invalid",
                "user_id": user_id,
                "allow_uploads_config": allow_uploads_config,
            },
        )
        raise HTTPException(status_code=500, detail="File upload permission misconfigured")

    if not allow_uploads:
        raise FileQuotaError(
            code=USER_FILE_UPLOADS_DISABLED,
            message="File uploads are disabled for your group",
            status_code=403,
        )


def resolve_user_owned_file_limits(db: Session, user_id: str) -> tuple[int, int | None]:
    """Return strict count and byte limits for any new user-owned file.

    Generated output and manual uploads consume the same owned-file capacity,
    but disabling the manual upload UI does not implicitly disable generation.
    Keeping those policies separate prevents generated files from bypassing
    quota without changing the existing feature-permission contract.
    """

    try:
        max_files_config = get_user_group_setting_value(user_id, "files", "max_files_upload_count", db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "[Files] Failed to load max files upload count",
            extra={
                "event": "file_upload_limit_lookup_failed",
                "user_id": user_id,
            },
        )
        raise HTTPException(status_code=500, detail="Failed to validate file upload limit") from exc

    try:
        max_files_limit = int(max_files_config)
    except (TypeError, ValueError):
        logger.error(
            "[Files] Invalid max files upload count configuration",
            extra={
                "event": "file_upload_limit_invalid",
                "user_id": user_id,
                "max_files_config": max_files_config,
            },
        )
        raise HTTPException(status_code=500, detail="File upload limit misconfigured")

    try:
        max_user_storage_config = get_user_group_setting_value(user_id, "files", "max_user_files_size_gb", db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "[Files] Failed to load max user files size",
            extra={
                "event": "file_upload_storage_limit_lookup_failed",
                "user_id": user_id,
            },
        )
        raise HTTPException(status_code=500, detail="Failed to validate file storage limit") from exc

    max_user_storage_limit_bytes = None
    if max_user_storage_config not in (None, ""):
        try:
            max_user_storage_gb = float(max_user_storage_config)
        except (TypeError, ValueError):
            logger.error(
                "[Files] Invalid max user files size configuration",
                extra={
                    "event": "file_upload_storage_limit_invalid",
                    "user_id": user_id,
                    "max_user_storage_config": max_user_storage_config,
                },
            )
            raise HTTPException(status_code=500, detail="File storage limit misconfigured")
        if max_user_storage_gb >= 0:
            max_user_storage_limit_bytes = int(max_user_storage_gb * (1024 ** 3))

    return max_files_limit, max_user_storage_limit_bytes


def resolve_user_file_upload_limits(db: Session, user_id: str) -> tuple[int, int | None]:
    """Validate manual-upload permission and return owned-file quota limits."""

    _resolve_file_upload_permission(db, user_id)
    return resolve_user_owned_file_limits(db, user_id)


def _session_dialect_name(db: Session) -> str:
    try:
        bind = db.get_bind()
    except Exception:
        return ""
    dialect = getattr(bind, "dialect", None)
    return str(getattr(dialect, "name", "") or "").lower()


def _user_file_quota_lock_key(user_id: str) -> int:
    digest = hashlib.sha256(f"{_USER_FILE_QUOTA_LOCK_PREFIX}{user_id}".encode("utf-8")).digest()
    unsigned_value = int.from_bytes(digest[:8], byteorder="big", signed=False)
    if unsigned_value >= 2**63:
        return unsigned_value - 2**64
    return unsigned_value


def _local_user_file_quota_lock(user_id: str) -> threading.RLock:
    with _USER_FILE_QUOTA_LOCKS_GUARD:
        lock = _USER_FILE_QUOTA_LOCKS.get(user_id)
        if lock is None:
            lock = threading.RLock()
            _USER_FILE_QUOTA_LOCKS[user_id] = lock
        return lock


@contextmanager
def serialized_user_file_quota_admission(db: Session, user_id: str):
    """Serialize quota checks and file row changes for one user.

    PostgreSQL uses a transaction-scoped advisory lock so other app workers and
    processes cooperate. Non-PostgreSQL runtimes are local/dev only here, so an
    in-process lock keeps tests and single-process SQLite runs deterministic.
    """
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        yield
        return

    if _session_dialect_name(db) == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _user_file_quota_lock_key(normalized_user_id)},
        )
        yield
        return

    lock = _local_user_file_quota_lock(normalized_user_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def resolve_user_max_upload_size_bytes(db: Session, user_id: str) -> tuple[int, int]:
    """Return the configured per-file upload size limit in bytes and MB."""
    try:
        max_mb_setting = get_user_group_setting_value(user_id, "chat", "max_upload_size", db)
        max_upload_mb = int(max_mb_setting) if max_mb_setting is not None else 1024
    except Exception:
        max_upload_mb = 1024

    effective_max_upload_mb = max(1, max_upload_mb)
    return effective_max_upload_mb * 1024 * 1024, effective_max_upload_mb


def ensure_user_file_upload_size_limit(db: Session, user_id: str, file_size: int) -> None:
    """Enforce the group-configured per-file upload size limit."""
    max_upload_bytes, max_upload_mb = resolve_user_max_upload_size_bytes(db, user_id)
    ensure_resolved_upload_file_size_limit(
        file_size,
        max_upload_bytes=max_upload_bytes,
        max_upload_mb=max_upload_mb,
    )


def ensure_resolved_upload_file_size_limit(
    file_size: int,
    *,
    max_upload_bytes: int,
    max_upload_mb: int,
):
    if file_size > max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File size exceeds limit of {max_upload_mb} MB",
        )


def ensure_upload_file_size_limits(
    file_size: int,
    *,
    max_upload_bytes: int,
    max_upload_mb: int,
    global_limit_detail: str | None = None,
) -> None:
    """Enforce both the group-configured limit and the global upload ceiling."""
    ensure_resolved_upload_file_size_limit(
        file_size,
        max_upload_bytes=max_upload_bytes,
        max_upload_mb=max_upload_mb,
    )
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=global_limit_detail or f"File size {file_size} exceeds maximum allowed size {MAX_FILE_SIZE}",
        )


def ensure_user_file_upload_capacity(
    db: Session,
    user_id: str,
    file_size: int,
    *,
    max_files_limit: int,
    max_user_storage_limit_bytes: int | None,
    existing_file_id: str | None = None,
    pending_file_count: int | None = None,
    exclude_reservation_id: str | None = None,
) -> None:
    """Enforce owned-file quotas including active provider-work reservations."""

    if max_files_limit < 0 and max_user_storage_limit_bytes is None:
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    reservation_query = db.query(
        func.coalesce(func.sum(FileQuotaReservation.reserved_files), 0),
        func.coalesce(func.sum(FileQuotaReservation.reserved_bytes), 0),
    ).filter(
        FileQuotaReservation.user_id == user_id,
        FileQuotaReservation.expires_at > now,
    )
    if exclude_reservation_id:
        reservation_query = reservation_query.filter(
            FileQuotaReservation.id != exclude_reservation_id
        )
    reserved_file_count, reserved_storage_bytes = reservation_query.one()
    reserved_file_count = int(reserved_file_count or 0)
    reserved_storage_bytes = int(reserved_storage_bytes or 0)

    if max_user_storage_limit_bytes is not None:
        storage_query = db.query(func.coalesce(func.sum(Files.file_size), 0)).filter(Files.user_id == user_id)
        if existing_file_id:
            storage_query = storage_query.filter(Files.id != existing_file_id)
        existing_total_size = storage_query.scalar()
        if existing_total_size + reserved_storage_bytes + file_size > max_user_storage_limit_bytes:
            raise FileQuotaError(
                code=USER_FILE_STORAGE_QUOTA_REACHED,
                message="Maximum storage quota reached",
            )

    count_increment = (
        max(int(pending_file_count), 0)
        if pending_file_count is not None
        else (0 if existing_file_id else 1)
    )
    if max_files_limit >= 0 and count_increment:
        existing_files_count = db.query(Files).filter(Files.user_id == user_id).count()
        if existing_files_count + reserved_file_count + count_increment > max_files_limit:
            raise FileQuotaError(
                code=USER_FILE_COUNT_QUOTA_REACHED,
                message="Maximum number of uploaded files reached",
            )


def _delete_expired_file_quota_reservations_locked(
    db: Session,
    user_id: str,
    *,
    now: datetime.datetime,
) -> int:
    """Remove abandoned reservations while the user's quota lock is held."""

    return (
        db.query(FileQuotaReservation)
        .filter(
            FileQuotaReservation.user_id == user_id,
            FileQuotaReservation.expires_at <= now,
        )
        .delete(synchronize_session=False)
    )


def reserve_user_file_quota(
    db: Session,
    *,
    user_id: str,
    purpose: str,
    reserved_files: int = 1,
    reserved_bytes: int = 1,
) -> FileQuotaReservationContext | None:
    """Atomically reserve capacity before slow provider or renderer work.

    File-producing tools always know whether they will create a row, so count
    capacity is reserved exactly. Output bytes are not known until the provider
    responds; reserving one byte rejects already-full storage early, while the
    final persistence transaction expands the reservation to the exact result
    size without permitting concurrent overshoot.
    """

    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id is required for file quota reservation")
    normalized_files = max(int(reserved_files or 0), 0)
    normalized_bytes = max(int(reserved_bytes or 0), 0)
    normalized_purpose = str(purpose or "generated_file").strip()[:100] or "generated_file"
    max_files_limit, max_storage_bytes = resolve_user_owned_file_limits(
        db,
        normalized_user_id,
    )

    # Unlimited users do not need an operational row. Final persistence still
    # resolves policy again so a concurrent administrator limit change is
    # enforced before any durable file is created.
    if max_files_limit < 0 and max_storage_bytes is None:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = now + _USER_FILE_QUOTA_RESERVATION_TTL
    try:
        with serialized_user_file_quota_admission(db, normalized_user_id):
            _delete_expired_file_quota_reservations_locked(
                db,
                normalized_user_id,
                now=now,
            )
            db.flush()
            ensure_user_file_upload_capacity(
                db,
                normalized_user_id,
                normalized_bytes,
                max_files_limit=max_files_limit,
                max_user_storage_limit_bytes=max_storage_bytes,
                pending_file_count=normalized_files,
            )
            reservation = FileQuotaReservation(
                user_id=normalized_user_id,
                reserved_files=normalized_files,
                reserved_bytes=normalized_bytes,
                purpose=normalized_purpose,
                created_at=now,
                expires_at=expires_at,
            )
            db.add(reservation)
            db.commit()
            db.refresh(reservation)
            return FileQuotaReservationContext(
                reservation_id=str(reservation.id),
                user_id=normalized_user_id,
                reserved_files=normalized_files,
                reserved_bytes=normalized_bytes,
                purpose=normalized_purpose,
                expires_at=expires_at,
            )
    except Exception:
        db.rollback()
        raise


def release_user_file_quota_reservation(
    db: Session,
    reservation_id: str | None,
) -> None:
    """Best-effort release for failed work; consumed rows are a no-op.

    Cleanup must never hide the provider or persistence exception that caused
    it. A transient database failure can therefore leave the lease in place,
    but the expiry bound ensures that it stops counting automatically.
    """

    normalized_reservation_id = str(reservation_id or "").strip()
    if not normalized_reservation_id:
        return
    try:
        reservation = (
            db.query(FileQuotaReservation)
            .filter(FileQuotaReservation.id == normalized_reservation_id)
            .first()
        )
        if not reservation:
            # The lookup itself starts a transaction. End it even though no
            # advisory lock was needed, so this best-effort cleanup never
            # returns a session with an unexpected open transaction.
            db.rollback()
            return
        user_id = str(reservation.user_id)
        with serialized_user_file_quota_admission(db, user_id):
            deleted = (
                db.query(FileQuotaReservation)
                .filter(FileQuotaReservation.id == normalized_reservation_id)
                .delete(synchronize_session=False)
            )
            if deleted:
                db.commit()
            else:
                # PostgreSQL advisory quota locks are transaction-scoped. The
                # reservation may have been consumed after the first lookup,
                # but the lock still has to be released before returning.
                db.rollback()
    except Exception:
        db.rollback()
        logger.warning(
            "[Files] Failed to release file quota reservation; it will expire",
            extra={
                "event": "file_quota_reservation_release_failed",
                "reservation_id": normalized_reservation_id,
            },
            exc_info=True,
        )


@contextmanager
def reserved_user_file_quota(
    db: Session,
    *,
    user_id: str,
    purpose: str,
    reserved_files: int = 1,
    reserved_bytes: int = 1,
):
    """Reserve around provider work and release automatically on every failure."""

    reservation = reserve_user_file_quota(
        db,
        user_id=user_id,
        purpose=purpose,
        reserved_files=reserved_files,
        reserved_bytes=reserved_bytes,
    )
    reservation_id = reservation.reservation_id if reservation else None
    try:
        yield reservation_id
    finally:
        release_user_file_quota_reservation(db, reservation_id)


def _cleanup_unrecorded_storage_reference(
    *,
    storage_provider: str | None,
    storage_key: str | None,
    user_id: str,
    file_name: str | None,
) -> None:
    if not storage_provider or not storage_key:
        return
    try:
        delete_storage_reference(
            storage_provider=storage_provider,
            storage_key=storage_key,
            user_id=user_id,
            file_name=file_name,
        )
    except Exception:
        logger.warning(
            "[Files] Failed to clean up unrecorded storage object",
            extra={
                "event": "file_unrecorded_storage_cleanup_failed",
                "user_id": user_id,
                "storage_provider": storage_provider,
                "storage_key": storage_key,
            },
            exc_info=True,
        )


def _create_file_record_for_uploaded_storage(
    db: Session,
    *,
    user_id: str,
    file_category: str,
    file_type: str,
    file_size: int,
    project_id: str | None = None,
    share: dict | None = None,
    share_id: str | None = None,
    meta: dict | None = None,
    file_id: str | None = None,
    file_name: str | None = None,
    storage_provider: str,
    storage_key: str,
    storage_meta: dict | None = None,
    folder_id: str | None = None,
    commit: bool = True,
) -> Files:
    committed = False
    try:
        record = create_file(
            db,
            user_id=user_id,
            file_category=file_category,
            file_type=file_type,
            file_size=file_size,
            project_id=project_id,
            share=share,
            share_id=share_id,
            meta=meta,
            file_id=file_id,
            file_name=file_name,
            storage_provider=storage_provider,
            storage_key=storage_key,
            storage_meta=storage_meta,
            folder_id=folder_id,
            commit=False,
        )
        if not commit:
            return record
        db.commit()
        committed = True
        db.refresh(record)
        return record
    except Exception:
        if not committed:
            db.rollback()
            _cleanup_unrecorded_storage_reference(
                storage_provider=storage_provider,
                storage_key=storage_key,
                user_id=user_id,
                file_name=file_name,
            )
        raise



# -------------------
# Get file path
# -------------------
def get_file_path(user_id: str, file_name: str) -> Path:
    """
    Get the legacy local filesystem path for a user's file.

    This remains the canonical path for local provider deployments and for
    legacy rows before hybrid migration has moved objects.
    """
    return _resolve_local_storage_path(build_storage_key(user_id, file_name), create_parent=True)


def resolve_accessible_file_record(
    db: Session, user_id: str, file_id: str
) -> tuple[Files | None, str | None]:
    """Resolve a file the user can access and the owning user context for storage operations."""
    file_record = get_accessible_file(db, user_id, file_id)
    if not file_record:
        return None, None
    return file_record, str(file_record.user_id)


def _resolve_storage_reference(file_record: Files, user_id: str) -> tuple[str, str]:
    """Resolve storage provider and key from file record."""
    provider = str(getattr(file_record, "storage_provider", "") or "").strip().lower() or "local"
    storage_key = str(getattr(file_record, "storage_key", "") or "").strip()
    if not storage_key:
        storage_key = build_storage_key(user_id, file_record.file_name)
    return provider, storage_key


def _legacy_local_path_from_key(user_id: str, file_name: str) -> Path:
    """Get legacy local path from user ID and file name."""
    return get_file_path(user_id, file_name)


def materialize_file_record(file_record: Files, user_id: str) -> Path:
    """Materialize file from storage to local filesystem path."""
    try:
        provider, storage_key = _resolve_storage_reference(file_record, user_id)
        storage_key = ensure_user_scoped_storage_key(user_id, storage_key)
    except ValueError:
        logger.warning(
            "[Files] Rejected invalid user-scoped storage key",
            extra={"event": "file_storage_key_invalid_scope", "user_id": user_id, "file_id": file_record.id},
        )
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Local provider keeps files in place; no materialization copy needed.
    if provider == "local":
        try:
            local_path = _legacy_local_path_from_key(user_id, file_record.file_name)
            if local_path.exists():
                return local_path
        except ValueError:
            logger.warning(
                "[Files] Rejected invalid local file path",
                extra={"event": "file_local_path_invalid", "user_id": user_id, "file_id": file_record.id},
            )
        try:
            alt_target = _resolve_local_storage_path(storage_key)
            if alt_target.exists():
                return alt_target
        except ValueError:
            logger.warning(
                "[Files] Rejected invalid local storage key",
                extra={"event": "file_local_storage_key_invalid", "user_id": user_id, "file_id": file_record.id},
            )
        raise HTTPException(status_code=404, detail="File not found on disk")

    suffix = Path(file_record.file_name or "").suffix or ".bin"
    materialized = MATERIALIZED_TEMP_DIR / f"{file_record.id}{suffix}"
    if materialized.exists() and materialized.stat().st_size > 0:
        return materialized

    materialized_tmp = MATERIALIZED_TEMP_DIR / f"{file_record.id}.{uuid.uuid4().hex}.partial"
    try:
        download_file_from_storage(provider, storage_key, materialized_tmp)
        if not materialized_tmp.exists() or materialized_tmp.stat().st_size <= 0:
            raise FileNotFoundError("Materialized temp file is missing after download")
        os.replace(materialized_tmp, materialized)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found in object storage")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "[Files] Failed to materialize file",
            extra={
                "event": "file_materialize_failed",
                "user_id": user_id,
                "file_id": file_record.id,
                "provider": provider,
            },
        )
        raise HTTPException(status_code=500, detail="Failed to fetch file from storage") from exc
    finally:
        materialized_tmp.unlink(missing_ok=True)
    return materialized


def persist_generated_file_bytes(
    db: Session,
    *,
    user_id: str,
    original_filename: str,
    file_bytes: bytes,
    file_type: str,
    file_category: str | None = None,
    project_id: str | None = None,
    share: dict | None = None,
    share_id: str | None = None,
    meta: dict | None = None,
    file_id: str | None = None,
    file_name: str | None = None,
    folder_id: str | None = None,
    max_files_limit: int | None = None,
    max_user_storage_limit_bytes: int | None = None,
    quota_reservation_id: str | None = None,
    before_commit: Callable[[Files], None] | None = None,
) -> Files:
    """Persist generated bytes through mandatory owned-file quota admission.

    ``before_commit`` runs after the new file row has been flushed and before
    the transaction commits.  Callers can therefore create dependent rows
    that need the generated file ID without exposing a partially persisted
    file.  If the callback fails, the database work is rolled back and the
    uploaded storage object is compensated by the existing cleanup path.
    """
    if not file_bytes:
        raise ValueError("file_bytes must not be empty")

    effective_meta = dict(meta or {})
    if is_supported_ai_generated_media(file_type, effective_meta) and bool(
        get_user_group_setting_value(
            str(user_id),
            "compliance",
            "enable_content_credentials",
            db,
        )
    ):
        try:
            file_bytes, credential_status = apply_content_credentials(
                file_bytes=bytes(file_bytes),
                file_type=file_type,
                original_filename=original_filename,
            )
        except Exception:
            logger.exception(
                "[Files] Failed to embed C2PA Content Credentials",
                extra={
                    "event": "file_content_credentials_failed",
                    "user_id": str(user_id),
                    "file_type": str(file_type or ""),
                },
            )
            raise
        effective_meta["content_credentials"] = content_credentials_meta(
            credential_status
        )

    generated_file_id = file_id or str(uuid.uuid4())
    temp_target = TEMP_DIR / f"{generated_file_id}.tmp"
    try:
        temp_target.parent.mkdir(parents=True, exist_ok=True)
        with temp_target.open("wb") as handle:
            handle.write(file_bytes)
        return persist_generated_file_path(
            db,
            user_id=user_id,
            original_filename=original_filename,
            source_path=temp_target,
            file_type=file_type,
            file_category=file_category,
            project_id=project_id,
            share=share,
            share_id=share_id,
            meta=effective_meta,
            file_id=generated_file_id,
            file_name=file_name,
            folder_id=folder_id,
            max_files_limit=max_files_limit,
            max_user_storage_limit_bytes=max_user_storage_limit_bytes,
            quota_reservation_id=quota_reservation_id,
            before_commit=before_commit,
        )
    finally:
        temp_target.unlink(missing_ok=True)


def persist_generated_file_path(
    db: Session,
    *,
    user_id: str,
    original_filename: str,
    source_path: str | Path,
    file_type: str,
    file_category: str | None = None,
    project_id: str | None = None,
    share: dict | None = None,
    share_id: str | None = None,
    meta: dict | None = None,
    file_id: str | None = None,
    file_name: str | None = None,
    folder_id: str | None = None,
    max_files_limit: int | None = None,
    max_user_storage_limit_bytes: int | None = None,
    quota_reservation_id: str | None = None,
    before_commit: Callable[[Files], None] | None = None,
) -> Files:
    """Persist a generated path and optional dependent rows atomically."""
    source = Path(source_path)
    if not source.exists() or not source.is_file():
        raise ValueError("source_path must point to an existing file")
    file_size = int(source.stat().st_size)
    if file_size <= 0:
        raise ValueError("source_path must not be empty")

    safe_original_filename = Path(original_filename or "generated").name
    suffix = Path(safe_original_filename).suffix
    generated_file_id = file_id or str(uuid.uuid4())
    stored_name = file_name or (f"{generated_file_id}{suffix}" if suffix else generated_file_id)

    effective_meta = dict(meta or {})
    effective_meta.setdefault("original_filename", safe_original_filename)
    resolved_file_category = file_category or get_file_category(file_type)

    uploaded_reference: dict[str, str | None] = {}

    def persist_after_capacity_check(*, commit: bool) -> Files:
        provider, storage_key, upload_meta = upload_file_to_storage(source, user_id, stored_name)
        uploaded_reference.update(
            {
                "storage_provider": provider,
                "storage_key": storage_key,
            }
        )
        return _create_file_record_for_uploaded_storage(
            db,
            user_id=user_id,
            file_category=resolved_file_category,
            file_type=file_type,
            file_size=file_size,
            project_id=project_id,
            share=share,
            share_id=share_id,
            meta=effective_meta,
            file_id=generated_file_id,
            file_name=stored_name,
            storage_provider=provider,
            storage_key=storage_key,
            storage_meta=upload_meta,
            folder_id=folder_id,
            commit=commit,
        )

    policy_max_files, policy_max_storage = resolve_user_owned_file_limits(
        db,
        str(user_id),
    )
    # Some older callers pass limits they resolved before preparing the file.
    # Re-resolve here so an omitted or stale permissive value can never bypass
    # the policy in force at the authoritative persistence boundary. Preserve a
    # stricter caller-supplied limit for backwards-compatible fail-closed use.
    effective_max_files_limit = policy_max_files
    if max_files_limit is not None and (
        effective_max_files_limit < 0
        or (max_files_limit >= 0 and max_files_limit < effective_max_files_limit)
    ):
        effective_max_files_limit = max_files_limit
    effective_max_storage_limit = policy_max_storage
    if max_user_storage_limit_bytes is not None and (
        effective_max_storage_limit is None
        or max_user_storage_limit_bytes < effective_max_storage_limit
    ):
        effective_max_storage_limit = max_user_storage_limit_bytes
    try:
        with serialized_user_file_quota_admission(db, user_id):
            now = datetime.datetime.now(datetime.timezone.utc)
            _delete_expired_file_quota_reservations_locked(
                db,
                str(user_id),
                now=now,
            )
            active_reservation = None
            normalized_reservation_id = str(quota_reservation_id or "").strip()
            if normalized_reservation_id:
                reservation_owner = (
                    db.query(FileQuotaReservation.user_id)
                    .filter(FileQuotaReservation.id == normalized_reservation_id)
                    .scalar()
                )
                if reservation_owner and str(reservation_owner) != str(user_id):
                    raise ValueError("File quota reservation does not belong to this user")
                active_reservation = (
                    db.query(FileQuotaReservation)
                    .filter(
                        FileQuotaReservation.id == normalized_reservation_id,
                        FileQuotaReservation.user_id == str(user_id),
                        FileQuotaReservation.expires_at > now,
                    )
                    .first()
                )
            db.flush()
            ensure_user_file_upload_capacity(
                db,
                user_id,
                file_size,
                max_files_limit=effective_max_files_limit,
                max_user_storage_limit_bytes=effective_max_storage_limit,
                exclude_reservation_id=(
                    str(active_reservation.id) if active_reservation else None
                ),
            )
            record = persist_after_capacity_check(commit=False)
            if active_reservation:
                db.delete(active_reservation)
            if before_commit is not None:
                # ``create_file(..., commit=False)`` flushed ``record`` above,
                # so its generated ID is available to foreign-key dependants.
                before_commit(record)
            db.commit()
            db.refresh(record)
            return record
    except Exception:
        db.rollback()
        if uploaded_reference:
            _cleanup_unrecorded_storage_reference(
                storage_provider=uploaded_reference.get("storage_provider"),
                storage_key=uploaded_reference.get("storage_key"),
                user_id=str(user_id),
                file_name=stored_name,
            )
        raise


def _materialized_file_path(file_id: str, file_name: str) -> Path:
    """Return the local read-through cache path for a persisted file."""

    suffix = Path(file_name or "").suffix or ".bin"
    return MATERIALIZED_TEMP_DIR / f"{file_id}{suffix}"


def _write_materialized_file_bytes(
    *,
    file_id: str,
    file_name: str,
    file_bytes: bytes,
) -> None:
    """Atomically publish bytes to the local read-through cache."""

    materialized = _materialized_file_path(file_id, file_name)
    materialized_tmp = MATERIALIZED_TEMP_DIR / f"{file_id}.{uuid.uuid4().hex}.overwrite"
    try:
        with materialized_tmp.open("wb") as handle:
            handle.write(file_bytes)
        os.replace(materialized_tmp, materialized)
    finally:
        materialized_tmp.unlink(missing_ok=True)


def invalidate_materialized_file_cache(*, file_id: str, file_name: str) -> None:
    """Remove a staged read-through cache entry after a failed replacement.

    Object-storage writes cannot participate in the database transaction.  A
    Canvas edit stages its replacement under a new key and may populate this
    cache before grant reconciliation finishes.  Removing the cache on
    rollback guarantees the next read materializes the still-authoritative
    previous storage object.
    """

    _materialized_file_path(file_id, file_name).unlink(missing_ok=True)


def overwrite_existing_file_bytes(
    *,
    user_id: str,
    file_name: str,
    file_id: str,
    file_bytes: bytes,
    update_materialized_cache: bool = True,
) -> tuple[str, str, dict]:
    """Upload replacement bytes and optionally publish the local cache copy."""
    if not file_bytes:
        raise ValueError("file_bytes must not be empty")
    temp_target = TEMP_DIR / f"{file_id}.overwrite"
    temp_target.parent.mkdir(parents=True, exist_ok=True)
    temp_target.write_bytes(file_bytes)
    try:
        provider, storage_key, upload_meta = upload_file_to_storage(temp_target, user_id, file_name)
    finally:
        temp_target.unlink(missing_ok=True)
    if update_materialized_cache:
        _write_materialized_file_bytes(
            file_id=file_id,
            file_name=file_name,
            file_bytes=file_bytes,
        )
    return provider, storage_key, upload_meta


def persist_generated_file_replacement_bytes(
    db: Session,
    *,
    user_id: str,
    file_record: Files,
    original_filename: str,
    file_bytes: bytes,
    file_type: str,
    file_category: str,
    meta: dict | None = None,
    quota_reservation_id: str | None = None,
    folder_id: str | None = None,
    project_id: str | None = None,
    update_location: bool = False,
    before_commit: Callable[[Files], None] | None = None,
) -> Files:
    """Quota-check and atomically account for replacing an owned generated file.

    Replacements consume no additional file-count slot, but a larger result must
    fit after excluding the previous row size. The reservation is consumed in
    the same database transaction that publishes the new size and metadata.
    ``before_commit`` can stage dependent rows, including audit outbox events,
    in that transaction. Replacement bytes use a fresh storage key so rollback
    leaves the bytes referenced by the durable row unchanged.
    """

    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id or str(getattr(file_record, "user_id", "")) != normalized_user_id:
        raise ValueError("Generated replacement file must belong to the target user")
    if not file_bytes:
        raise ValueError("file_bytes must not be empty")

    max_files_limit, max_storage_bytes = resolve_user_owned_file_limits(
        db,
        normalized_user_id,
    )
    previous_storage_provider = (
        str(getattr(file_record, "storage_provider", "") or "local").strip().lower()
        or "local"
    )
    previous_storage_key = str(getattr(file_record, "storage_key", "") or "").strip()
    if not previous_storage_key:
        previous_storage_key = build_storage_key(
            normalized_user_id,
            str(file_record.file_name),
        )
    new_storage_reference: tuple[str, str] | None = None
    materialized_path = _materialized_file_path(
        str(file_record.id),
        str(file_record.file_name),
    )

    try:
        with serialized_user_file_quota_admission(db, normalized_user_id):
            now = datetime.datetime.now(datetime.timezone.utc)
            _delete_expired_file_quota_reservations_locked(
                db,
                normalized_user_id,
                now=now,
            )
            active_reservation = None
            normalized_reservation_id = str(quota_reservation_id or "").strip()
            if normalized_reservation_id:
                active_reservation = (
                    db.query(FileQuotaReservation)
                    .filter(
                        FileQuotaReservation.id == normalized_reservation_id,
                        FileQuotaReservation.user_id == normalized_user_id,
                        FileQuotaReservation.expires_at > now,
                    )
                    .first()
                )
            db.flush()
            ensure_user_file_upload_capacity(
                db,
                normalized_user_id,
                len(file_bytes),
                max_files_limit=max_files_limit,
                max_user_storage_limit_bytes=max_storage_bytes,
                existing_file_id=str(file_record.id),
                exclude_reservation_id=(
                    str(active_reservation.id) if active_reservation else None
                ),
            )

            # Storage providers are not transactional. Upload under a fresh key
            # so the database row remains a valid pointer to the previous bytes
            # until its metadata transaction commits successfully.
            replacement_suffix = Path(str(file_record.file_name)).suffix
            replacement_storage_name = (
                f"{file_record.id}.replacement-{uuid.uuid4().hex}{replacement_suffix}"
            )
            storage_provider, storage_key, storage_meta = overwrite_existing_file_bytes(
                user_id=normalized_user_id,
                file_name=replacement_storage_name,
                file_id=str(file_record.id),
                file_bytes=file_bytes,
                update_materialized_cache=False,
            )
            new_storage_reference = (storage_provider, storage_key)
            updated_meta = dict(file_record.meta or {})
            updated_meta.update(dict(meta or {}))
            updated_meta.setdefault(
                "original_filename",
                Path(original_filename or file_record.file_name).name,
            )

            file_record.file_type = file_type
            file_record.file_category = file_category
            file_record.file_size = len(file_bytes)
            file_record.storage_provider = storage_provider
            file_record.storage_key = storage_key
            file_record.storage_meta = storage_meta
            if update_location:
                file_record.folder_id = folder_id
                file_record.project_id = project_id
            file_record.last_updated_at = datetime.datetime.now(datetime.timezone.utc)
            file_record.meta = updated_meta
            db.add(file_record)
            if active_reservation:
                db.delete(active_reservation)
            if before_commit is not None:
                before_commit(file_record)
            # Do not let a pre-commit cache entry expose replacement bytes while
            # the durable row still points at the previous storage object.
            materialized_path.unlink(missing_ok=True)
            db.commit()
            db.refresh(file_record)
    except Exception:
        db.rollback()
        if new_storage_reference and (
            new_storage_reference[0] != previous_storage_provider
            or new_storage_reference[1] != previous_storage_key
        ):
            _cleanup_unrecorded_storage_reference(
                storage_provider=new_storage_reference[0],
                storage_key=new_storage_reference[1],
                user_id=normalized_user_id,
                # The staged key is deliberately not the canonical file name.
                # Supplying the legacy name here would also delete the previous
                # local object that the rolled-back database row still owns.
                file_name=None,
            )
        raise

    try:
        _write_materialized_file_bytes(
            file_id=str(file_record.id),
            file_name=str(file_record.file_name),
            file_bytes=file_bytes,
        )
    except Exception:
        # A missing cache is safe: the next reader rematerializes the committed
        # storage object. A stale cache is not, so remove it on publication error.
        materialized_path.unlink(missing_ok=True)
        logger.warning(
            "[Files] Failed to publish replacement file cache",
            extra={
                "event": "generated_file_replacement_cache_publish_failed",
                "user_id": normalized_user_id,
                "file_id": str(file_record.id),
            },
            exc_info=True,
        )

    if previous_storage_key and (
        previous_storage_provider != str(file_record.storage_provider)
        or previous_storage_key != str(file_record.storage_key)
    ):
        try:
            delete_storage_reference(
                storage_provider=previous_storage_provider,
                storage_key=previous_storage_key,
                user_id=normalized_user_id,
                file_name=str(file_record.file_name),
            )
        except Exception:
            logger.warning(
                "[Files] Failed to clean up replaced generated file storage object",
                extra={
                    "event": "generated_file_previous_storage_cleanup_failed",
                    "user_id": normalized_user_id,
                    "file_id": str(file_record.id),
                },
                exc_info=True,
            )
    return file_record


def delete_storage_reference(
    *,
    storage_provider: str,
    storage_key: str,
    user_id: str | None = None,
    file_name: str | None = None,
) -> None:
    """Delete file from storage provider."""
    provider = str(storage_provider or "local").strip().lower() or "local"
    key = str(storage_key or "").strip()
    if user_id and key:
        try:
            key = ensure_user_scoped_storage_key(str(user_id), key)
        except ValueError:
            logger.warning(
                "[Files] Skipped delete for invalid user-scoped storage key",
                extra={"event": "file_delete_invalid_user_scoped_key", "user_id": user_id},
            )
            key = ""

    if provider == "local":
        if user_id and file_name:
            try:
                get_file_path(str(user_id), str(file_name)).unlink(missing_ok=True)
            except ValueError:
                logger.warning(
                    "[Files] Skipped delete for invalid local file path",
                    extra={"event": "file_local_delete_invalid_path", "user_id": user_id},
                )
        if key:
            try:
                _resolve_local_storage_path(key).unlink(missing_ok=True)
            except ValueError:
                logger.warning(
                    "[Files] Skipped delete for invalid local storage key",
                    extra={"event": "file_local_delete_invalid_key", "user_id": user_id},
                )
        return

    if key:
        delete_file_from_storage(provider, key)



# -------------------
# Get file info
# -------------------
def get_file_info(user_id: str, file_id: str):
    """Get file information for a chat file by user_id and file_id."""
    from app.agents.utils import get_agent_asset_info_for_user, parse_agent_asset_descriptor
    from app.skills.models import parse_skill_file_descriptor, resolve_skill_file_info_for_user
    from app.database import SessionLocal
    
    normalized_file_id = str(file_id or "").strip()
    if parse_agent_asset_descriptor(normalized_file_id):
        db = SessionLocal()
        try:
            info = get_agent_asset_info_for_user(
                db,
                user_id=user_id,
                descriptor_or_asset_id=normalized_file_id,
            )
            if isinstance(info, dict):
                info["source_descriptor"] = normalized_file_id
                info["requester_user_id"] = str(user_id)
            return info
        finally:
            db.close()

    if parse_skill_file_descriptor(normalized_file_id):
        db = SessionLocal()
        try:
            info = resolve_skill_file_info_for_user(
                db,
                user_id=user_id,
                descriptor=normalized_file_id,
            )
            if isinstance(info, dict):
                info["source_descriptor"] = normalized_file_id
                info["requester_user_id"] = str(user_id)
            return info
        finally:
            db.close()

    db = SessionLocal()
    try:
        file_record, owning_user_id = resolve_accessible_file_record(db, user_id, normalized_file_id)
        if not file_record or not owning_user_id:
            return None

        file_path = materialize_file_record(file_record, owning_user_id)
        storage_provider, storage_key = _resolve_storage_reference(file_record, owning_user_id)

        # Normalize the category at read time as well as at creation time. This
        # keeps SVG records created before SVG became a document type—and SVGs
        # previously persisted by MCP as images—on the text extraction path
        # without requiring a destructive data migration.
        stored_category = file_record.file_category
        effective_category = (
            "document"
            if normalize_file_mime_type(file_record.file_type) == SVG_MIME_TYPE
            else stored_category
        )

        return {
            "file_id": str(file_record.id),
            "owner_user_id": str(owning_user_id),
            "requester_user_id": str(user_id),
            "path": str(file_path),
            "file_name": file_record.file_name,
            "storage_provider": storage_provider,
            "storage_key": storage_key,
            "file_type": file_record.file_type,
            "file_category": effective_category,
            "file_size": file_record.file_size,
            "meta": file_record.meta or {},
        }
    finally:
        db.close()



# -------------------
# Upload file
# -------------------
async def upload_file(
    file: UploadFile,
    project_id: Optional[str],
    user_id: str,
    db: Session,
    folder_id: str | None = None,
    model_allowed_mime_types: set[str] | None = None,
) -> Dict[str, Any]:
    """
    Upload a file for the current user with validation and storage.
    """
    try:
        max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(db, user_id)
        max_upload_bytes, max_upload_mb = resolve_user_max_upload_size_bytes(db, user_id)

        # Ensure project ownership when provided
        if project_id:
            try:
                get_project(db, user_id, project_id)
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception(
                    "[Files] Project validation failed during upload",
                    extra={
                        "event": "file_upload_project_validation_failed",
                        "user_id": user_id,
                        "project_id": project_id,
                    },
                )
                raise HTTPException(status_code=500, detail="Failed to validate project") from exc

        # Prepare storage details
        original_filename = Path(file.filename or "uploaded").name

        await file.seek(0)
        file.file.seek(0, os.SEEK_END)
        file_size = file.file.tell()
        await file.seek(0)

        ensure_upload_file_size_limits(
            file_size,
            max_upload_bytes=max_upload_bytes,
            max_upload_mb=max_upload_mb,
        )

        ensure_user_file_upload_capacity(
            db,
            user_id,
            file_size,
            max_files_limit=max_files_limit,
            max_user_storage_limit_bytes=max_user_storage_limit_bytes,
        )

        file_extension = Path(original_filename).suffix
        stored_file_id = str(uuid.uuid4())
        stored_file_name = f"{stored_file_id}{file_extension}" if file_extension else stored_file_id
        temp_upload_path = TEMP_DIR / f"{stored_file_id}.upload"

        extension_mime = guess_file_mime_from_name(file.filename)


        # Save to temporary local file before uploading to configured storage backend.
        bytes_written = 0
        hasher = hashlib.sha256()
        try:
            with open(temp_upload_path, "wb") as buffer:
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    hasher.update(chunk)
                    ensure_upload_file_size_limits(
                        bytes_written,
                        max_upload_bytes=max_upload_bytes,
                        max_upload_mb=max_upload_mb,
                    )
                    buffer.write(chunk)
        except HTTPException:
            if temp_upload_path.exists():
                try:
                    temp_upload_path.unlink()
                except Exception:
                    pass
            raise
        except Exception:
            if temp_upload_path.exists():
                try:
                    temp_upload_path.unlink()
                except Exception:
                    pass
            logger.exception(
                "[Files] Upload file write error",
                extra={
                    "event": "file_upload_write_failed",
                    "user_id": user_id,
                    # ``filename`` is reserved by Python's LogRecord. Using it
                    # in ``extra`` raises KeyError and masks the upload error.
                    "uploaded_filename": getattr(file, "filename", None),
                },
            )
            raise HTTPException(status_code=500, detail="Upload failed due to internal error")

        # User files may contain HTML because they are retained as source-text
        # conversation context. The download route still forces active content
        # to an attachment, and the frontend preview uses the guarded Canvas
        # renderer rather than navigating to this stored response.
        file_type = detect_and_validate_upload_mime(
            temp_upload_path,
            fallback_mime=extension_mime,
            allow_html_attachment=True,
        )

        file_category = get_file_category(file_type)

        content_sha256 = hasher.hexdigest()
        duplicate_record = _find_duplicate_file(
            db,
            user_id,
            original_filename,
            file_type,
            bytes_written,
            content_sha256,
            project_id=project_id,
            folder_id=folder_id,
        )
        if duplicate_record:
            try:
                temp_upload_path.unlink(missing_ok=True)
            except Exception:
                pass
            record_file_upload_metric(bytes_written, file_type)
            return {
                "status": "success",
                "file_id": duplicate_record.id,
                "file_category": duplicate_record.file_category,
                "already_uploaded": True,
            }
        meta = {"original_filename": original_filename, "sha256": content_sha256}

        try:
            with serialized_user_file_quota_admission(db, user_id):
                duplicate_record = _find_duplicate_file(
                    db,
                    user_id,
                    original_filename,
                    file_type,
                    bytes_written,
                    content_sha256,
                    project_id=project_id,
                    folder_id=folder_id,
                )
                if duplicate_record:
                    duplicate_file_id = duplicate_record.id
                    duplicate_file_category = duplicate_record.file_category
                    db.rollback()
                    try:
                        temp_upload_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    record_file_upload_metric(bytes_written, file_type)
                    return {
                        "status": "success",
                        "file_id": duplicate_file_id,
                        "file_category": duplicate_file_category,
                        "already_uploaded": True,
                    }

                ensure_user_file_upload_capacity(
                    db,
                    user_id,
                    bytes_written,
                    max_files_limit=max_files_limit,
                    max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                )

                try:
                    storage_provider, storage_key, storage_meta = upload_file_to_storage(
                        temp_upload_path,
                        user_id,
                        stored_file_name,
                    )
                except Exception as storage_exc:
                    db.rollback()
                    logger.exception(
                        "[Files] Storage upload error",
                        extra={
                            "event": "file_upload_storage_failed",
                            "user_id": user_id,
                            # Keep upload context without colliding with the
                            # LogRecord field that contains the source module.
                            "uploaded_filename": getattr(file, "filename", None),
                        },
                    )
                    raise HTTPException(status_code=500, detail="Upload failed while storing file") from storage_exc
                finally:
                    temp_upload_path.unlink(missing_ok=True)

                file_record = _create_file_record_for_uploaded_storage(
                    db,
                    user_id=user_id,
                    file_category=file_category,
                    file_type=file_type,
                    file_size=bytes_written,
                    project_id=project_id,
                    meta=meta,
                    file_id=stored_file_id,
                    file_name=stored_file_name,
                    storage_provider=storage_provider,
                    storage_key=storage_key,
                    storage_meta=storage_meta,
                    folder_id=folder_id,
                )
        except HTTPException:
            db.rollback()
            raise
        record_file_upload_metric(bytes_written, file_type)
        return {
            "status": "success",
            "file_id": file_record.id,
            "file_category": file_category,
            "already_uploaded": False,
        }
    except HTTPException as http_exc:
        try:
            if 'temp_upload_path' in locals() and temp_upload_path.exists():
                temp_upload_path.unlink()
        except Exception:
            pass
        raise http_exc
    except Exception:
        logger.exception(
            "[Files] Upload failed with unexpected error",
            extra={
                "event": "file_upload_failed",
                "user_id": user_id,
                "uploaded_filename": getattr(file, "filename", None),
            },
        )
        # Best effort cleanup if file was partially written
        try:
            if 'temp_upload_path' in locals() and temp_upload_path.exists():
                temp_upload_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Upload failed due to internal error")


def _upload_file_with_thread_session(
    file: UploadFile,
    project_id: str | None,
    user_id: str,
    folder_id: str | None,
    model_allowed_mime_types: set[str] | None,
) -> Dict[str, Any]:
    """Run the synchronous upload/storage stack with a thread-owned session."""

    from app.database import SessionLocal

    session = SessionLocal()
    try:
        if folder_id and not can_user_edit_folder(session, user_id, folder_id):
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this folder",
            )

        async def _run() -> Dict[str, Any]:
            return await upload_file(
                file,
                project_id,
                user_id,
                session,
                folder_id=folder_id,
                model_allowed_mime_types=model_allowed_mime_types,
            )

        return anyio.run(_run)
    finally:
        session.close()


async def upload_file_off_event_loop(
    file: UploadFile,
    project_id: str | None,
    user_id: str,
    db: Session,
    folder_id: str | None = None,
    model_allowed_mime_types: set[str] | None = None,
) -> Dict[str, Any]:
    """Upload through a bounded worker thread while preserving the async API."""

    # SQLite's default in-memory pool and connection objects are thread-bound.
    # Keep the compatibility/test backend on its supplied session; production
    # PostgreSQL uploads use a worker-owned session below.
    try:
        dialect_name = db.get_bind().dialect.name
    except (AttributeError, TypeError):
        dialect_name = ""

    if dialect_name == "sqlite":
        return await upload_file(
            file,
            project_id,
            user_id,
            db,
            folder_id=folder_id,
            model_allowed_mime_types=model_allowed_mime_types,
        )

    return await run_blocking_io(
        _upload_file_with_thread_session,
        file,
        project_id,
        str(user_id),
        folder_id,
        model_allowed_mime_types,
    )



# -------------------
# Download file
# -------------------
def download_file(user_id: str, file_id: str, db: Session, inline: bool = False) -> FileResponse:
    """
    Download a file for the authenticated user.
    """
    try:
        # Get file info from database
        file_info = get_accessible_file(db, user_id, file_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found")

        owning_user_id = file_info.user_id

        file_path = materialize_file_record(file_info, owning_user_id)

        # Determine content type
        content_type = file_info.file_type or "application/octet-stream"
        normalized_content_type = str(content_type).split(";", 1)[0].strip().lower()

        original_filename = None
        if file_info.meta and isinstance(file_info.meta, dict):
            original_filename = file_info.meta.get("original_filename")
        download_filename = original_filename or file_info.file_name
        download_filename_sanitized = (
            str(download_filename or "download")
            .replace("\r", "")
            .replace("\n", "")
            .replace('"', "")
            .strip()
        ) or "download"
        # Unsafe active content must always download as an attachment, even when
        # the caller requested an inline preview. FileResponse owns the header
        # encoding so Unicode names are emitted through RFC 5987's filename*
        # parameter instead of being inserted as raw, Latin-1-incompatible text.
        content_disposition_type = (
            "inline"
            if inline and normalized_content_type not in INLINE_UNSAFE_MIME_TYPES
            else "attachment"
        )

        response = FileResponse(
            path=file_path,
            filename=download_filename_sanitized,
            media_type=content_type,
            content_disposition_type=content_disposition_type,
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # File IDs are stable even when Canvas and other editable artifacts are
        # overwritten. Prevent browsers and intermediary caches from serving a
        # pre-edit body after the user refreshes and reopens the same file URL.
        # These are authenticated, user-specific responses, so retaining them
        # in a shared or persistent HTTP cache is undesirable in general.
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

        if inline and normalized_content_type not in INLINE_UNSAFE_MIME_TYPES:
            if normalized_content_type in INLINE_SAME_ORIGIN_FRAME_MIME_TYPES:
                response.headers["Content-Security-Policy"] = INLINE_SAME_ORIGIN_FRAME_CSP
                response.headers["X-Frame-Options"] = "SAMEORIGIN"

        return response

    except HTTPException:
        logger.warning(
            "[Files] Download failed",
            extra={
                "event": "file_download_failed",
                "user_id": user_id,
                "file_id": file_id,
            },
        )
        raise
    except Exception as e:
        logger.exception(
            "[Files] Download failed with unexpected error",
            extra={
                "event": "file_download_failed",
                "user_id": user_id,
                "file_id": file_id,
            },
        )
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")
    


def _calculate_cutoff(option: FileDeleteTimeOption) -> datetime.datetime | None:
    """Calculate cutoff datetime for file deletion based on time option."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if option == FileDeleteTimeOption.OLDER_THAN_1_DAY:
        return now - datetime.timedelta(days=1)
    if option == FileDeleteTimeOption.OLDER_THAN_1_WEEK:
        return now - datetime.timedelta(weeks=1)
    if option == FileDeleteTimeOption.OLDER_THAN_1_MONTH:
        return now - datetime.timedelta(days=30)
    if option == FileDeleteTimeOption.OLDER_THAN_1_YEAR:
        return now - datetime.timedelta(days=365)
    return None


def _delete_file_record(db: Session, user_id: str, file_info: Files) -> None:
    """Delete file record from storage and database."""
    try:
        storage_provider, storage_key = _resolve_storage_reference(file_info, user_id)
    except ValueError:
        logger.warning(
            "[Files] Rejected invalid storage reference during delete",
            extra={"event": "file_delete_invalid_storage_reference", "user_id": user_id, "file_id": file_info.id},
        )
        storage_provider = str(getattr(file_info, "storage_provider", "") or "").strip().lower() or "local"
        storage_key = ""
    try:
        delete_storage_reference(
            storage_provider=storage_provider,
            storage_key=storage_key,
            user_id=user_id,
            file_name=file_info.file_name,
        )
        materialized = MATERIALIZED_TEMP_DIR / f"{file_info.id}{Path(file_info.file_name or '').suffix or '.bin'}"
        materialized.unlink(missing_ok=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete file from storage: {str(exc)}")

    file_id = file_info.id
    try:
        db.query(FileArtifactShare).filter(FileArtifactShare.file_id == file_id).delete(synchronize_session=False)
        cleanup_file_references(db, user_id, file_id)
    except (OperationalError, ProgrammingError):
        db.rollback()
    except Exception:
        db.rollback()
        logger.exception(
            "[Files] Failed to clean up file references during file cleanup",
            extra={
                "event": "file_reference_cleanup_failed",
                "user_id": user_id,
                "file_id": file_id,
            },
        )
        raise HTTPException(status_code=500, detail="Failed to clean up file references")

    try:
        db.delete(file_info)
        db.commit()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete file from database: {str(exc)}")

    try:
        remove_file_from_automations(db, user_id, file_id)
    except Exception as exc:
        logger.exception(
            "[Files] Failed to remove file reference from automations",
            extra={
                "event": "file_automation_cleanup_failed",
                "user_id": user_id,
                "file_id": file_id,
                "error": str(exc),
            },
        )



# -------------------
# Delete file
# -------------------
def delete_file(user_id: str, file_id: str | None, db: Session, time_option: FileDeleteTimeOption) -> Dict[str, Any]:
    """
    Delete files for the authenticated user based on a specific file or time option.
    """
    try:
        if file_id:
            file_info = get_file(db, file_id, user_id)
            if not file_info:
                file_info = db.query(Files).filter(Files.id == file_id).first()
                if (
                    not file_info
                    or not file_info.folder_id
                    or not can_user_edit_folder(db, user_id, file_info.folder_id)
                ):
                    raise HTTPException(status_code=404, detail="File not found")

            owning_user_id = file_info.user_id

            _delete_file_record(db, owning_user_id, file_info)

            logger.info(
                "[Files] File deleted",
                extra={
                    "event": "file_delete",
                    "user_id": user_id,
                    "file_id": file_id,
                    "file_name": file_info.file_name,
                },
            )

            return {"status": "success", "deleted_count": 1}

        cutoff = _calculate_cutoff(time_option)
        query = db.query(Files).filter(Files.user_id == user_id)

        if time_option != FileDeleteTimeOption.ALL:
            if cutoff is None:
                raise HTTPException(status_code=400, detail="Invalid time option")
            query = query.filter(Files.created_at <= cutoff)

        files_to_delete = query.all()

        if not files_to_delete:
            return {"status": "success", "deleted_count": 0, "errors": []}

        deleted_count = 0
        errors = []
        for file_info in files_to_delete:
            try:
                _delete_file_record(db, user_id, file_info)
                deleted_count += 1
            except HTTPException as exc:
                db.rollback()
                errors.append(f"Failed to delete {file_info.file_name}: {exc.detail}")
            except Exception as exc:
                db.rollback()
                errors.append(f"Failed to delete {file_info.file_name}: {str(exc)}")

        total_count = len(files_to_delete)
        status = "success"
        message = f"Successfully deleted {deleted_count} files"
        if errors:
            status = "partial_failure" if deleted_count else "failure"
            message = (
                f"Deleted {deleted_count} of {total_count} files. "
                f"{len(errors)} files could not be deleted."
            )

        logger.info(
            "[Files] Bulk file delete",
            extra={
                "event": "file_delete_bulk",
                "user_id": user_id,
                "deleted_count": deleted_count,
                "time_option": time_option.value,
                "errors": bool(errors),
            },
        )

        return {
            "status": status,
            "message": message,
            "deleted_count": deleted_count,
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "[Files] Delete failed with unexpected error",
            extra={
                "event": "file_delete_failed",
                "user_id": user_id,
                "file_id": file_id,
                "time_option": getattr(time_option, "value", None),
            },
        )
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")



# -------------------
# Delete all files
# -------------------
def delete_all_files(user_id: str, db: Session) -> Dict[str, Any]:
    """
    Delete all files for the authenticated user.
    """
    try:
        # Get all user's files from database
        files = list_files(db, user_id)

        if not files:
            return {"message": "No files found to delete", "deleted_count": 0}

        deleted_count = 0
        errors = []

        for file_info in files:
            try:
                _delete_file_record(db, user_id, file_info)
                deleted_count += 1
            except HTTPException as exc:
                db.rollback()
                errors.append(f"Failed to delete {file_info.file_name}: {exc.detail}")
            except Exception as e:
                db.rollback()
                errors.append(f"Failed to delete {file_info.file_name}: {str(e)}")

        total_count = len(files)
        status = "success"
        message = f"Successfully deleted {deleted_count} files"
        if errors:
            status = "partial_failure" if deleted_count else "failure"
            message = (
                f"Deleted {deleted_count} of {total_count} files. "
                f"{len(errors)} files could not be deleted."
            )

        result = {
            "status": status,
            "message": message,
            "deleted_count": deleted_count,
            "errors": errors,
        }

        logger.info(
            "[Files] User deleted all files",
            extra={
                "event": "files_delete_all",
                "user_id": user_id,
                "deleted_count": deleted_count,
                "errors": bool(errors),
            },
        )

        return result

    except Exception as e:
        logger.exception(
            "[Files] Delete all files failed with unexpected error",
            extra={
                "event": "files_delete_all_failed",
                "user_id": user_id,
            },
        )
        raise HTTPException(status_code=500, detail=f"Delete all files failed: {str(e)}")



# -------------------
# Delete websearch files
# -------------------
def delete_websearch_files(user_id: str, db: Session) -> Dict[str, Any]:
    """
    Delete only the files that were downloaded by the websearch tool.

    is_from_websearch is in the meta of the file db row

    Returns the count of deleted data files (sidecars are not counted separately).
    """
    try:
        # Query files where meta contains is_from_websearch: true
        engine = db.get_bind()
        dialect_name = engine.dialect.name if engine is not None else None

        meta_filter = {"is_from_websearch": True}
        if dialect_name == "postgresql":
            meta_clause = cast(Files.meta, JSONB).contains(meta_filter)
        else:
            meta_clause = Files.meta.contains(meta_filter)

        files = (
            db.query(Files)
            .filter(
                Files.user_id == user_id,
                meta_clause,
            )
            .all()
        )

        if not files:
            return {"message": "No websearch files found to delete", "deleted_count": 0}

        deleted_count = 0
        errors = []

        for file_info in files:
            try:
                _delete_file_record(db, user_id, file_info)
                deleted_count += 1
            except HTTPException as exc:
                db.rollback()
                errors.append(f"Failed to delete websearch file {file_info.file_name}: {exc.detail}")
            except Exception as e:
                db.rollback()
                errors.append(f"Failed to delete websearch file {file_info.file_name}: {str(e)}")

        total_count = len(files)
        status = "success"
        message = f"Successfully deleted {deleted_count} websearch files"
        if errors:
            status = "partial_failure" if deleted_count else "failure"
            message = (
                f"Deleted {deleted_count} of {total_count} websearch files. "
                f"{len(errors)} files could not be deleted."
            )

        result = {
            "status": status,
            "message": message,
            "deleted_count": deleted_count,
            "errors": errors,
        }

        logger.info(
            "[Files] Websearch files deleted",
            extra={
                "event": "files_delete_websearch",
                "user_id": user_id,
                "deleted_count": deleted_count,
                "errors": bool(errors),
            },
        )
        return result




    except Exception as e:
        logger.exception(
            "[Files] Delete websearch files failed with unexpected error",
            extra={
                "event": "files_delete_websearch_failed",
                "user_id": user_id,
            },
        )
        raise HTTPException(status_code=500, detail=f"Delete websearch files failed: {str(e)}")



# -------------------
# Rename file
# -------------------
def rename_file(user_id: str, file_id: str, new_original_filename: str, db: Session) -> Files:
    """Rename a file by updating its original_filename metadata."""
    try:
        sanitized_name = Path(new_original_filename).name
        if not sanitized_name:
            raise HTTPException(status_code=400, detail="Filename cannot be empty")

        file_info = get_file(db, file_id, user_id)

        if not file_info:
            file_info = db.query(Files).filter(Files.id == file_id).first()
            if (
                not file_info
                or not file_info.folder_id
                or not can_user_edit_folder(db, user_id, file_info.folder_id)
            ):
                raise HTTPException(status_code=404, detail="File not found")

        meta = file_info.meta if isinstance(file_info.meta, dict) else {}
        meta = dict(meta) if meta is not None else {}
        meta["original_filename"] = sanitized_name

        file_info.meta = meta
        file_info.last_updated_at = datetime.datetime.now(datetime.timezone.utc)

        db.commit()
        db.refresh(file_info)

        logger.info(
            "[Files] File renamed",
            extra={
                "event": "file_rename",
                "user_id": user_id,
                "file_id": file_id,
                "original_filename": sanitized_name,
            },
        )

        return file_info
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "[Files] Rename file failed with unexpected error",
            extra={
                "event": "file_rename_failed",
                "user_id": user_id,
                "file_id": file_id,
            },
        )
        raise HTTPException(status_code=500, detail=f"Rename file failed: {str(e)}")



# -------------------
# Extract text from file
# -------------------
def extract_text_file(db, file_id: str):
    """Extract text content from a file based on its type."""
    file = db.query(Files).filter(Files.id == file_id).first()
    if not file:
        return None

    if file.file_type not in SUPPORTED_EXTRACT_TEXT_MIME_TYPES:
        return None

    from app.workers.files import external_file_processing_enabled, process_file_and_wait

    if external_file_processing_enabled():
        artifact = process_file_and_wait(
            db,
            user_id=file.user_id,
            file_id=file.id,
            operation="extract_text",
        )
        data = artifact.data if isinstance(artifact.data, dict) else {}
        file_content = data.get("content") if artifact.status == "succeeded" else None
        file_path = None
    else:
        try:
            file_path = materialize_file_record(file, file.user_id)
        except HTTPException:
            return None
        file_content = _extract_text_from_path_inline(
            {
                "path": str(file_path),
                "file_type": file.file_type,
            }
        )
    if file_content is None:
        return None

    def _format_dt(value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()

    last_modified = _format_dt(file.last_updated_at)
    if file_path is not None:
        try:
            stat = file_path.stat()
            last_modified = datetime.datetime.fromtimestamp(
                stat.st_mtime,
                timezone.utc,
            ).isoformat()
        except OSError:
            pass

    meta_dict = file.meta if isinstance(file.meta, dict) else {}
    original_filename = meta_dict.get("original_filename") if isinstance(meta_dict, dict) else None
    metadata = {
        "file_id": file.id,
        "user_id": file.user_id,
        "mime_type": file.file_type,
        "name": original_filename or file.file_name,
        "stored_name": file.file_name,
        "size": file.file_size,
        "last_modified": last_modified,
        "created_at": _format_dt(file.created_at),
        "updated_at": _format_dt(file.last_updated_at),
        "meta": meta_dict,
        # The Generation Worker does not need a storage path after the File
        # Processing Worker returned extracted text. Avoid rematerializing a
        # cloud object in the generation process solely for metadata.
        "path": str(file_path) if file_path is not None else None,
    }

    return {
        "content": file_content,
        "metadata": metadata,
    }


def _extract_text_from_path_inline(file_info: dict | None) -> str | None:
    """Perform local extraction; called only by inline mode or the file worker."""
    if not isinstance(file_info, dict):
        return None

    file_path_raw = file_info.get("path")
    file_type = str(file_info.get("file_type") or "").strip().lower()
    if not isinstance(file_path_raw, str) or not file_path_raw.strip():
        return None

    file_path = Path(file_path_raw)
    if not file_path.exists() or not file_path.is_file():
        return None

    file_content = None
    if file_type in MARKITDOWN_MIME_TYPES:
        try:
            # MarkItDown imports Magika, NumPy, and ONNX Runtime. Keep that
            # process-wide cost out of API and non-file worker startup.
            from markitdown import MarkItDown

            converter = MarkItDown(enable_plugins=True)
            result = converter.convert(str(file_path))
            file_content = getattr(result, "text_content", None) or getattr(result, "markdown", None)
        except Exception:
            file_content = None

    if file_content is None:
        try:
            file_content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                file_content = file_path.read_bytes().decode("utf-8", errors="replace")
            except Exception:
                file_content = None
        except Exception:
            file_content = None

    if not isinstance(file_content, str):
        return None

    stripped = file_content.strip()
    return stripped or None


def extract_text_from_file_info(file_info: dict | None) -> str | None:
    """Return cached worker extraction for a file, with local dev fallback."""
    if not isinstance(file_info, dict):
        return None

    from app.database import SessionLocal
    from app.workers.files import external_file_processing_enabled, process_file_and_wait

    file_id = str(file_info.get("file_id") or "").strip()
    requester_user_id = str(file_info.get("requester_user_id") or "").strip()
    source_descriptor = str(file_info.get("source_descriptor") or "").strip()
    if external_file_processing_enabled() and file_id and requester_user_id:
        db = SessionLocal()
        try:
            artifact = process_file_and_wait(
                db,
                user_id=requester_user_id,
                file_id=file_id,
                operation="extract_text",
            )
            data = artifact.data if isinstance(artifact.data, dict) else {}
            content = data.get("content")
            return str(content).strip() if isinstance(content, str) and content.strip() else None
        finally:
            db.close()
    if external_file_processing_enabled() and source_descriptor and requester_user_id:
        from app.workers.files import process_descriptor_text_and_wait

        db = SessionLocal()
        try:
            return process_descriptor_text_and_wait(
                db,
                user_id=requester_user_id,
                descriptor=source_descriptor,
                file_info=file_info,
            )
        finally:
            db.close()
    if external_file_processing_enabled():
        # Production workers must not parse untracked paths inside a generation
        # process. Callers without a durable file or validated descriptor get no
        # extracted attachment instead of silently crossing the process boundary.
        return None
    return _extract_text_from_path_inline(file_info)
