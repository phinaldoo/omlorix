from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, BinaryIO

from app.files.models import FileArtifactShare, Files
from app.files.sharing import (
    ARTIFACT_SHARE_DEFAULT_EXPIRES_IN_HOURS,
    ARTIFACT_SHARE_MAX_EXPIRES_IN_HOURS,
)
from app.files.utils import (
    MAX_FILE_SIZE,
    TEMP_DIR,
    _detect_mime_from_content,
    _upload_is_valid_active_content,
    get_file_category,
    materialize_file_record,
    persist_generated_file_bytes,
    validate_file_type,
)
from app.users.models import User, build_user_email_match
from app.utils.email import normalize_email
from fastapi import HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


ADMIN_USER_FILES_EXPORT_TYPE = "admin_user_files"
ADMIN_USER_FILES_EXPORT_VERSION = 1.0
ADMIN_USER_FILES_MANIFEST_NAME = "manifest.json"
ADMIN_USER_INLINE_FILE_CONTENT_KEY = "content_base64"
ARCHIVE_IO_CHUNK_SIZE = 1024 * 1024
INLINE_BASE64_CHUNK_SIZE = 768 * 1024
EXPORT_ARCHIVE_SPOOL_THRESHOLD_BYTES = 8 * 1024 * 1024
IMPORT_ARCHIVE_ENTRY_SPOOL_THRESHOLD_BYTES = 8 * 1024 * 1024
# Manifests contain metadata only. Sixteen MiB leaves several KiB of metadata
# for each of the 5,000 supported file entries while bounding the input passed
# to the UTF-8 decoder and JSON parser.
MAX_IMPORT_MANIFEST_SIZE = 16 * 1024 * 1024
MAX_IMPORT_FILE_COUNT = max(
    1, int(os.getenv("ADMIN_USER_FILES_IMPORT_MAX_COUNT", "5000"))
)
MAX_IMPORT_TOTAL_SIZE = max(
    1, int(os.getenv("ADMIN_USER_FILES_IMPORT_MAX_TOTAL_SIZE", str(512 * 1024 * 1024)))
)
EXPORT_QUERY_BATCH_SIZE = max(
    1, int(os.getenv("USER_DATA_EXPORT_QUERY_BATCH_SIZE", "500"))
)


def _normalize_user_email_reference(value: Any) -> str | None:
    """
    Normalize an Omlorix user email reference for file-transfer manifests.

    Args:
    value (Any): The user email/reference value to normalize.

    Returns:
    str | None: The normalized value, or None when the input is empty.
    """
    # File-transfer packages use the users.email column as a stable local
    # account reference, not as proof that the address is deliverable on the
    # public internet. Temporary accounts intentionally use values such as
    # "...@temporary.local", which Pydantic's EmailStr rejects because ".local"
    # is reserved, but those values are valid Omlorix user references.
    return normalize_email(value)


def _sanitize_filename_part(value: Any, fallback: str = "user") -> str:
    """
    Sanitize a string for use in a filename.

    Args:
    value (Any): The string to sanitize.
    fallback (str): The fallback value to use if the input is invalid. Defaults to "user".

    Returns:
    str: The sanitized string.
    """
    normalized = str(value or "").strip()
    normalized = "".join("-" if char in '\\/:*?"<>|' else char for char in normalized)
    normalized = "-".join(part for part in normalized.split())
    return normalized or fallback


def _safe_original_filename(value: Any, fallback: str) -> str:
    """
    Get a safe original filename.

    Args:
    value (Any): The original filename.
    fallback (str): The fallback value to use if the input is invalid.

    Returns:
    str: The safe original filename.
    """
    safe_name = Path(str(value or "").strip() or fallback).name
    return safe_name or fallback


def _json_dumps(value: Any) -> str:
    """Serialize compact JSON for streamed export entries."""
    return json.dumps(value, ensure_ascii=True, default=str, separators=(",", ":"))


def _write_json_chunk(handle: BinaryIO, value: str) -> None:
    handle.write(value.encode("utf-8"))


def _strip_nulls(data: Any) -> Any:
    """Recursively remove null values from exported JSON data."""
    if isinstance(data, dict):
        return {
            key: _strip_nulls(value) for key, value in data.items() if value is not None
        }
    if isinstance(data, list):
        return [_strip_nulls(value) for value in data if value is not None]
    return data


def _revoke_imported_canvas_asset_approvals(meta: dict[str, Any]) -> None:
    """Keep reference provenance while refusing to import security authority.

    User IDs and file IDs can be remapped or belong to another Omlorix instance.
    The destination therefore requires references to be attached/approved again
    instead of trusting portable JSON as an access-control record.
    """

    references = meta.get("canvas_asset_references")
    if not isinstance(references, list):
        return
    # Only the first 20 references are supported by Canvas consumers. Truncate
    # the untrusted package before sanitizing so no attacker-controlled approval
    # provenance survives in stored metadata for a future or alternate reader.
    if len(references) > 20:
        del references[20:]
    for reference in references:
        if not isinstance(reference, dict):
            continue
        reference["status"] = "revoked"
        reference["authorized_by_user_id"] = ""
        reference["authorized_at"] = None
        reference["public_status"] = "revoked"
        reference["public_request_id"] = ""
        reference["public_authorized_by_user_id"] = ""
        reference["public_authorized_at"] = None


def _iter_query_rows(query, batch_size: int = EXPORT_QUERY_BATCH_SIZE):
    """Iterate query results without forcing SQLAlchemy to build a full list."""
    if hasattr(query, "execution_options"):
        query = query.execution_options(stream_results=True)
    if hasattr(query, "yield_per"):
        query = query.yield_per(batch_size)
    try:
        yield from query
    except TypeError:
        yield from query.all()


def _build_archive_name(original_filename: str, seen_names: set[str]) -> str:
    """
    Build a unique archive name avoiding duplicates.

    Args:
    original_filename (str): The original filename.
    seen_names (set[str]): A set of seen archive names.

    Returns:
    str: A unique archive name.
    """
    base_name = f"files/{_safe_original_filename(original_filename, 'file')}"
    candidate = base_name
    counter = 1
    while candidate in seen_names:
        name_path = Path(base_name)
        stem = name_path.stem or "file"
        suffix = name_path.suffix
        candidate = f"files/{stem}_{counter}{suffix}"
        counter += 1
    seen_names.add(candidate)
    return candidate


def _materialize_export_file_path(
    file_record: Files, user_id: str, original_filename: str
) -> Path:
    """
    Materialize a file path for export.

    Args:
    file_record (Files): The file record.
    user_id (str): The user ID.
    original_filename (str): The original filename.

    Returns:
    Path: The materialized file path.
    """
    try:
        file_path = materialize_file_record(file_record, user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to prepare file '{original_filename}' for export",
        ) from exc

    if not file_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Missing file content for '{original_filename}'",
        )

    return file_path




def _copy_export_file_to_zip_entry(
    archive: zipfile.ZipFile,
    archive_name: str,
    file_record: Files,
    user_id: str,
    original_filename: str,
) -> None:
    file_path = _materialize_export_file_path(file_record, user_id, original_filename)
    try:
        with (
            file_path.open("rb") as source_handle,
            archive.open(archive_name, "w") as target_handle,
        ):
            shutil.copyfileobj(
                source_handle, target_handle, length=ARCHIVE_IO_CHUNK_SIZE
            )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read file content for '{original_filename}'",
        ) from exc


def _iter_export_file_base64_chunks(
    file_record: Files, user_id: str, original_filename: str
):
    file_path = _materialize_export_file_path(file_record, user_id, original_filename)
    try:
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(INLINE_BASE64_CHUNK_SIZE)
                if not chunk:
                    break
                yield base64.b64encode(chunk).decode("ascii")
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read file content for '{original_filename}'",
        ) from exc


def _build_file_export_entry(
    *,
    file_record: Files,
    user_email: str,
    original_filename: str,
    artifact_shares: list[dict[str, Any]] | None = None,
    archive_name: str | None = None,
    content_base64: str | None = None,
) -> dict[str, Any]:
    """
    Build a file export entry.

    Args:
    file_record (Files): The file record.
    user_email (str): The user email.
    original_filename (str): The original filename.
    archive_name (str | None): The archive name. Defaults to None.
    content_base64 (str | None): The base64 encoded file content. Defaults to None.

    Returns:
    dict[str, Any]: The file export entry.
    """
    meta = dict(file_record.meta or {}) if isinstance(file_record.meta, dict) else {}
    meta.pop("origin", None)
    entry: dict[str, Any] = {
        "id": file_record.id,
        "email": user_email,
        "file_name": file_record.file_name,
        "original_filename": original_filename,
        "file_category": file_record.file_category,
        "file_type": file_record.file_type,
        "file_size": file_record.file_size,
        "project_id": file_record.project_id,
        "folder_id": file_record.folder_id,
        "share": file_record.share,
        "share_id": file_record.share_id,
        "artifact_shares": artifact_shares or [],
        "meta": meta,
        "created_at": file_record.created_at.isoformat()
        if file_record.created_at
        else None,
        "last_updated_at": file_record.last_updated_at.isoformat()
        if file_record.last_updated_at
        else None,
    }
    if archive_name:
        entry["archive_name"] = archive_name
    if content_base64 is not None:
        entry[ADMIN_USER_INLINE_FILE_CONTENT_KEY] = content_base64
    return entry


def _parse_iso_datetime(value: Any) -> datetime | None:
    """
    Parse an ISO datetime string to datetime object.

    Args:
    value (Any): The ISO datetime string.

    Returns:
    datetime | None: The parsed datetime object or None if the input is invalid.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    try:
        return datetime.fromisoformat(trimmed.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_nonnegative_int(value: Any, default: int = 0) -> int:
    """Parse a non-negative integer, falling back for malformed archive metadata."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _serialize_artifact_share_for_export(share: FileArtifactShare) -> dict[str, Any]:
    """Serialize artifact share metadata for export without secrets."""
    return {
        "id": share.id,
        "created_at": share.created_at.isoformat() if share.created_at else None,
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "last_accessed_at": share.last_accessed_at.isoformat()
        if share.last_accessed_at
        else None,
        "access_count": int(getattr(share, "access_count", 0) or 0),
        "has_password": bool(share.password_hash),
    }


def _normalize_imported_artifact_share_expiry(
    expires_at: datetime | None,
) -> tuple[datetime, bool]:
    """Return a safe imported share expiry and whether the import value had to be adjusted."""
    now = datetime.now(timezone.utc)
    default_expiry = now + timedelta(hours=ARTIFACT_SHARE_DEFAULT_EXPIRES_IN_HOURS)
    if expires_at is None:
        return default_expiry, True

    normalized = (
        expires_at
        if expires_at.tzinfo is not None
        else expires_at.replace(tzinfo=timezone.utc)
    )
    normalized = normalized.astimezone(timezone.utc)
    if normalized <= now:
        return default_expiry, True

    max_expiry = now + timedelta(hours=ARTIFACT_SHARE_MAX_EXPIRES_IN_HOURS)
    if normalized > max_expiry:
        return max_expiry, True
    return normalized, False


def _get_export_user(db: Session, user_id: str) -> tuple[User, str]:
    """Load and validate the user for export."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_email = _normalize_user_email_reference(getattr(user, "email", None))
    if not user_email:
        raise HTTPException(
            status_code=400,
            detail="User is missing an email reference for file transfer",
        )
    return user, user_email


def _build_export_files_query(db: Session, user_id: str):
    return (
        db.query(Files)
        .filter(Files.user_id == user_id)
        .order_by(Files.created_at.asc(), Files.id.asc())
    )


def _iter_export_file_records(db: Session, user_id: str):
    yield from _iter_query_rows(_build_export_files_query(db, user_id))




def _export_artifact_shares_for_file(
    db: Session, user_id: str, file_id: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(FileArtifactShare)
        .filter(
            FileArtifactShare.user_id == user_id,
            FileArtifactShare.file_id == file_id,
        )
        .order_by(FileArtifactShare.created_at.asc(), FileArtifactShare.id.asc())
    )
    return [_serialize_artifact_share_for_export(row) for row in _iter_query_rows(rows)]




def _stream_file_entry_json(
    *,
    file_record: Files,
    user_email: str,
    user_id: str,
    original_filename: str,
    artifact_shares: list[dict[str, Any]],
    include_content: bool,
):
    entry = _strip_nulls(
        _build_file_export_entry(
            file_record=file_record,
            user_email=user_email,
            original_filename=original_filename,
            artifact_shares=artifact_shares,
        )
    )
    if not include_content:
        yield _json_dumps(entry)
        return

    yield "{"
    first = True
    for key, value in entry.items():
        if not first:
            yield ","
        first = False
        yield _json_dumps(key)
        yield ":"
        yield _json_dumps(value)

    if not first:
        yield ","
    yield _json_dumps(ADMIN_USER_INLINE_FILE_CONTENT_KEY)
    yield ':"'
    yield from _iter_export_file_base64_chunks(file_record, user_id, original_filename)
    yield '"}'


def stream_admin_user_file_entries_json_array(
    db: Session,
    user_id: str,
    *,
    include_content: bool = False,
):
    """Stream a JSON array of user file export entries."""
    user, user_email = _get_export_user(db, user_id)
    yield "["
    first = True
    for file_record in _iter_export_file_records(db, str(user.id)):
        meta = (
            dict(file_record.meta or {}) if isinstance(file_record.meta, dict) else {}
        )
        original_filename = _safe_original_filename(
            meta.get("original_filename") or file_record.file_name,
            f"{file_record.id}.bin",
        )
        if not first:
            yield ","
        first = False
        yield from _stream_file_entry_json(
            file_record=file_record,
            user_email=user_email,
            user_id=str(user.id),
            original_filename=original_filename,
            artifact_shares=_export_artifact_shares_for_file(
                db, str(user.id), str(file_record.id)
            ),
            include_content=include_content,
        )
    yield "]"




def _resolve_existing_user_by_email(db: Session, email: str) -> tuple[User, str]:
    """
    Resolve an existing user by email address.

    Args:
    db (Session): The database session.
    email (str): The user email.

    Returns:
    tuple[User, str]: The user and the action taken (updated).

    Raises:
    HTTPException: If user not found.
    """
    target_user = db.query(User).filter(build_user_email_match(email)).first()
    if not target_user:
        raise HTTPException(
            status_code=404,
            detail=f"User with email '{email}' not found. Import requires an existing user.",
        )

    return target_user, "updated"




def export_admin_user_files_bundle(
    db: Session, user_id: str
) -> tuple[BinaryIO, str, dict[str, Any]]:
    """
    Export user files as a ZIP bundle.

    Args:
    db (Session): The database session.
    user_id (str): The user ID.

    Returns:
    tuple[BinaryIO, str, dict[str, Any]]: The ZIP file buffer, filename, and manifest.
    """
    user, user_email = _get_export_user(db, user_id)
    from app.users.utils import _export_user_file_folders

    file_folder_export = _export_user_file_folders(str(user.id), db)
    exported_folders = file_folder_export.get("owned", [])
    exported_subscriptions = file_folder_export.get("subscriptions", [])
    zip_buffer = tempfile.SpooledTemporaryFile(
        max_size=EXPORT_ARCHIVE_SPOOL_THRESHOLD_BYTES, mode="w+b"
    )
    manifest_buffer = tempfile.SpooledTemporaryFile(
        max_size=EXPORT_ARCHIVE_SPOOL_THRESHOLD_BYTES, mode="w+b"
    )
    seen_archive_names: set[str] = set()
    generated_at = datetime.now(timezone.utc).isoformat()
    file_count = 0
    warnings: list[dict[str, Any]] = []

    try:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            _write_json_chunk(
                manifest_buffer,
                _json_dumps(
                    {
                        "export_type": ADMIN_USER_FILES_EXPORT_TYPE,
                        "export_version": ADMIN_USER_FILES_EXPORT_VERSION,
                        "generated_at": generated_at,
                        "package_structure": {
                            "manifest": ADMIN_USER_FILES_MANIFEST_NAME,
                            "files_directory": "files/",
                        },
                        "user": {
                            "id": user.id,
                            "email": user_email,
                        },
                    }
                )[:-1],
            )
            _write_json_chunk(manifest_buffer, ',"files":[')
            first_manifest_file = True

            for file_record in _iter_export_file_records(db, str(user.id)):
                meta = (
                    dict(file_record.meta or {})
                    if isinstance(file_record.meta, dict)
                    else {}
                )
                original_filename = _safe_original_filename(
                    meta.get("original_filename") or file_record.file_name,
                    f"{file_record.id}.bin",
                )
                archive_name = _build_archive_name(
                    original_filename, seen_archive_names
                )
                try:
                    _copy_export_file_to_zip_entry(
                        archive,
                        archive_name,
                        file_record,
                        str(user.id),
                        original_filename,
                    )
                except HTTPException as exc:
                    warnings.append(
                        {
                            "file_id": file_record.id,
                            "original_filename": original_filename,
                            "warning": str(
                                exc.detail or "File content could not be exported."
                            ),
                        }
                    )
                    continue

                manifest_entry = _strip_nulls(
                    _build_file_export_entry(
                        file_record=file_record,
                        user_email=user_email,
                        original_filename=original_filename,
                        artifact_shares=_export_artifact_shares_for_file(
                            db, str(user.id), str(file_record.id)
                        ),
                        archive_name=archive_name,
                    )
                )
                if not first_manifest_file:
                    _write_json_chunk(manifest_buffer, ",")
                first_manifest_file = False
                _write_json_chunk(manifest_buffer, _json_dumps(manifest_entry))
                file_count += 1

            _write_json_chunk(
                manifest_buffer,
                "],"
                + f'"file_count":{file_count},'
                + f'"folders":{_json_dumps(exported_folders)},'
                + f'"shared_file_folder_subscriptions":{_json_dumps(exported_subscriptions)},'
                + f'"warnings":{_json_dumps(warnings)}'
                + "}",
            )
            manifest_buffer.seek(0)
            with archive.open(ADMIN_USER_FILES_MANIFEST_NAME, "w") as target_handle:
                shutil.copyfileobj(
                    manifest_buffer, target_handle, length=ARCHIVE_IO_CHUNK_SIZE
                )

        zip_buffer.seek(0)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{_sanitize_filename_part(user_email)}-files-{timestamp}.zip"
        return (
            zip_buffer,
            filename,
            {
                "export_type": ADMIN_USER_FILES_EXPORT_TYPE,
                "export_version": ADMIN_USER_FILES_EXPORT_VERSION,
                "generated_at": generated_at,
                "user": {
                    "id": user.id,
                    "email": user_email,
                },
                "file_count": file_count,
                "folder_count": len(exported_folders),
                "shared_file_folder_subscription_count": len(exported_subscriptions),
                "warnings": warnings,
            },
        )
    except Exception:
        zip_buffer.close()
        raise
    finally:
        manifest_buffer.close()


def _read_bounded_manifest_bytes(
    archive: zipfile.ZipFile,
    entry_name: str,
    *,
    missing_detail: str,
    invalid_detail: str,
) -> bytes:
    """Read one ZIP manifest without allowing decompression past the limit.

    The central-directory ``file_size`` check rejects ordinary ZIP bombs before
    decompression starts. The capped read is a second, authoritative guard so
    the safety boundary does not rely solely on attacker-controlled metadata.

    Args:
        archive: ZIP archive supplied by an import caller.
        entry_name: Exact manifest member to read.
        missing_detail: Existing API error for an absent member.
        invalid_detail: Existing API error for an invalid or oversized member.

    Returns:
        The complete manifest bytes when they fit within the enforced limit.

    Raises:
        HTTPException: If the manifest is absent, a directory, or too large.
    """
    try:
        info = archive.getinfo(entry_name)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=missing_detail) from exc

    declared_size = max(0, int(getattr(info, "file_size", 0) or 0))
    if info.is_dir() or declared_size > MAX_IMPORT_MANIFEST_SIZE:
        raise HTTPException(status_code=400, detail=invalid_detail)

    try:
        with archive.open(info) as handle:
            # Reading one extra byte distinguishes an exact-boundary manifest
            # from truncated input without ever materializing the full entry.
            payload = handle.read(MAX_IMPORT_MANIFEST_SIZE + 1)
    except KeyError as exc:
        # Preserve the established missing-manifest response if the archive is
        # mutated between the directory lookup and opening the selected entry.
        raise HTTPException(status_code=400, detail=missing_detail) from exc

    if len(payload) > MAX_IMPORT_MANIFEST_SIZE:
        raise HTTPException(status_code=400, detail=invalid_detail)
    return payload


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    """
    Read the manifest from a ZIP archive.

    Args:
    archive (zipfile.ZipFile): The ZIP archive.

    Returns:
    dict[str, Any]: The manifest.
    """
    manifest_bytes = _read_bounded_manifest_bytes(
        archive,
        ADMIN_USER_FILES_MANIFEST_NAME,
        missing_detail="Invalid file package. Missing manifest.json",
        invalid_detail="Invalid file package manifest.",
    )
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid file package manifest."
        ) from exc

    if manifest.get("export_type") != ADMIN_USER_FILES_EXPORT_TYPE:
        raise HTTPException(
            status_code=400,
            detail="Invalid export file. Expected an admin user files package.",
        )
    if manifest.get("export_version") != ADMIN_USER_FILES_EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export version. Expected {ADMIN_USER_FILES_EXPORT_VERSION}.",
        )
    return manifest




def _manifest_list_or_empty(manifest: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = manifest.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file package. Manifest '{key}' must be a list.",
        )
    for entry in value:
        if not isinstance(entry, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file package. Manifest '{key}' entries must be objects.",
            )
    return value


def _build_existing_file_indexes(
    db: Session, user_id: str
) -> tuple[dict[str, str], dict[tuple[str, str, str, int], str]]:
    """
    Build indexes of existing files for a user.

    Args:
    db (Session): The database session.
    user_id (str): The user ID.

    Returns:
    Source IDs and duplicate signatures mapped to their current local file ID.
    """
    existing_files = db.query(Files).filter(Files.user_id == user_id).all()
    source_ids: dict[str, str] = {}
    duplicate_signatures: dict[tuple[str, str, str, int], str] = {}

    for file_record in existing_files:
        current_file_id = str(getattr(file_record, "id", "") or "").strip()
        if current_file_id:
            source_ids[current_file_id] = current_file_id
        meta = (
            dict(file_record.meta or {}) if isinstance(file_record.meta, dict) else {}
        )
        import_source_file_id = str(meta.get("import_source_file_id") or "").strip()
        if import_source_file_id:
            source_ids[import_source_file_id] = current_file_id

        original_filename = _safe_original_filename(
            meta.get("original_filename") or file_record.file_name,
            file_record.file_name or "file",
        )
        sha256 = str(meta.get("sha256") or "").strip()
        if sha256:
            duplicate_signatures[
                (
                    original_filename,
                    sha256,
                    str(file_record.file_type or "").strip(),
                    int(file_record.file_size or 0),
                )
            ] = current_file_id

    return source_ids, duplicate_signatures


def _validate_archive_name(value: Any) -> str:
    """
    Validate and return an archive name.

    Args:
    value (Any): The archive name.

    Returns:
    str: The validated archive name.
    """
    archive_name = str(value or "").strip()
    parts = Path(archive_name).parts
    if (
        not archive_name
        or Path(archive_name).is_absolute()
        or PureWindowsPath(archive_name).is_absolute()
        or ".." in parts
    ):
        raise HTTPException(
            status_code=400, detail="Invalid archive entry path in file package."
        )
    return archive_name


def _read_archive_entry_with_size_limit(
    archive: zipfile.ZipFile,
    entry_name: str,
    *,
    original_filename: str,
    max_bytes: int,
) -> BinaryIO:
    """
    Read an archive entry with size limit.

    Args:
    archive (zipfile.ZipFile): The ZIP archive.
    entry_name (str): The entry name.
    max_bytes (int): The maximum size in bytes.

    Returns:
    BinaryIO: The entry content.
    """
    try:
        with archive.open(entry_name) as source_handle:
            staged_file = tempfile.SpooledTemporaryFile(
                max_size=IMPORT_ARCHIVE_ENTRY_SPOOL_THRESHOLD_BYTES,
                mode="w+b",
            )
            total_size = 0

            try:
                while True:
                    chunk = source_handle.read(ARCHIVE_IO_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > max_bytes:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Archived file '{original_filename}' exceeds the maximum file size.",
                        )
                    staged_file.write(chunk)

                staged_file.seek(0)
                return staged_file.read()
            finally:
                staged_file.close()
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file package. Missing archived file '{entry_name}'.",
        ) from exc


def _validate_imported_file_bytes(
    *,
    file_bytes: bytes,
    original_filename: str,
    fallback_type: str,
) -> tuple[str, str]:
    """Validate imported file bytes and return file type and hash."""
    if not file_bytes:
        raise HTTPException(
            status_code=400, detail=f"Archived file '{original_filename}' is empty."
        )
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Archived file '{original_filename}' exceeds the maximum file size.",
        )

    temp_path = TEMP_DIR / f"{uuid.uuid4().hex}.admin-import"
    try:
        with temp_path.open("wb") as handle:
            handle.write(file_bytes)
        detected_file_type = _detect_mime_from_content(
            temp_path, fallback=fallback_type
        )
        # User-file archives must be able to restore HTML conversation
        # attachments accepted by the ordinary upload route. Imported HTML is
        # still constrained by the same attachment-only download and guarded
        # preview boundaries after its file record is recreated.
        if not validate_file_type(
            detected_file_type,
            allow_html_attachment=True,
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Archived file '{original_filename}' has an unsupported file type.",
            )
        if not _upload_is_valid_active_content(
            detected_file_type,
            temp_path,
            allow_html_attachment=True,
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Archived file '{original_filename}' contains disallowed active content.",
            )
    finally:
        temp_path.unlink(missing_ok=True)

    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    return detected_file_type, file_sha256


def _load_and_validate_file_entry(
    archive: zipfile.ZipFile,
    entry: dict[str, Any],
) -> tuple[bytes, str, str, str]:
    """Load and validate a file entry from archive."""
    archive_name = _validate_archive_name(entry.get("archive_name"))
    original_filename = _safe_original_filename(
        entry.get("original_filename") or entry.get("file_name"),
        Path(archive_name).name or "file",
    )

    file_bytes = _read_archive_entry_with_size_limit(
        archive,
        archive_name,
        original_filename=original_filename,
        max_bytes=MAX_FILE_SIZE,
    )

    fallback_type = (
        str(entry.get("file_type") or "").strip()
        or mimetypes.guess_type(original_filename)[0]
        or "application/octet-stream"
    )
    detected_file_type, file_sha256 = _validate_imported_file_bytes(
        file_bytes=file_bytes,
        original_filename=original_filename,
        fallback_type=fallback_type,
    )
    return file_bytes, original_filename, detected_file_type, file_sha256


def _load_and_validate_inline_file_entry(
    entry: dict[str, Any],
) -> tuple[bytes, str, str, str]:
    """Load and validate an inline file entry."""
    original_filename = _safe_original_filename(
        entry.get("original_filename") or entry.get("file_name"),
        "file",
    )

    encoded_content = str(entry.get(ADMIN_USER_INLINE_FILE_CONTENT_KEY) or "").strip()
    if not encoded_content:
        raise HTTPException(
            status_code=400,
            detail=f"Inline file entry '{original_filename}' is missing embedded content.",
        )

    max_base64_chars = ((MAX_FILE_SIZE + 2) // 3) * 4
    if len(encoded_content) > max_base64_chars:
        raise HTTPException(
            status_code=400,
            detail=f"Inline file entry '{original_filename}' exceeds the maximum file size.",
        )

    try:
        file_bytes = base64.b64decode(encoded_content, validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Inline file entry '{original_filename}' contains invalid base64 content.",
        ) from exc

    fallback_type = (
        str(entry.get("file_type") or "").strip()
        or mimetypes.guess_type(original_filename)[0]
        or "application/octet-stream"
    )
    detected_file_type, file_sha256 = _validate_imported_file_bytes(
        file_bytes=file_bytes,
        original_filename=original_filename,
        fallback_type=fallback_type,
    )
    return file_bytes, original_filename, detected_file_type, file_sha256


def _load_existing_artifact_share_ids(db: Session) -> set[str]:
    """Load existing artifact share IDs so imports can avoid collisions."""
    return {
        str(share_id)
        for (share_id,) in db.query(FileArtifactShare.id).all()
        if str(share_id or "").strip()
    }


def _restore_artifact_share_entries(
    db: Session,
    *,
    imported_file: Files,
    target_user: User,
    artifact_shares: Any,
    existing_share_ids: set[str],
    warnings: list[dict[str, Any]],
    index: int,
    source_file_id: str,
    original_filename: str,
) -> bool:
    """Restore exported artifact share metadata without password secrets."""
    if not isinstance(artifact_shares, list):
        return False

    restored_any = False
    skipped_password_protected_count = 0
    adjusted_expiry_count = 0
    regenerated_share_ids: list[str] = []
    for raw_share in artifact_shares:
        if not isinstance(raw_share, dict):
            continue

        if raw_share.get("has_password"):
            skipped_password_protected_count += 1
            continue

        requested_share_id = str(raw_share.get("id") or "").strip()
        share_id = requested_share_id or str(uuid.uuid4())
        if share_id in existing_share_ids:
            share_id = str(uuid.uuid4())
            regenerated_share_ids.append(requested_share_id or share_id)

        expires_at, expiry_adjusted = _normalize_imported_artifact_share_expiry(
            _parse_iso_datetime(raw_share.get("expires_at"))
        )
        if expiry_adjusted:
            adjusted_expiry_count += 1

        share_row = FileArtifactShare(
            id=share_id,
            file_id=imported_file.id,
            user_id=target_user.id,
            password_hash=None,
            created_at=_parse_iso_datetime(raw_share.get("created_at"))
            or datetime.now(timezone.utc),
            expires_at=expires_at,
            last_accessed_at=_parse_iso_datetime(raw_share.get("last_accessed_at")),
            access_count=_parse_nonnegative_int(raw_share.get("access_count")),
        )
        db.add(share_row)
        existing_share_ids.add(share_id)
        restored_any = True

    if skipped_password_protected_count:
        warnings.append(
            {
                "index": index,
                "source_file_id": source_file_id or None,
                "original_filename": original_filename,
                "warning": "Skipped password-protected artifact shares because exported passwords are redacted. Recreate them after import.",
                "skipped_password_protected_artifact_share_count": skipped_password_protected_count,
            }
        )

    if adjusted_expiry_count:
        warnings.append(
            {
                "index": index,
                "source_file_id": source_file_id or None,
                "original_filename": original_filename,
                "warning": "Reset one or more imported artifact share expirations to a safe bounded value.",
                "reset_artifact_share_expiry_count": adjusted_expiry_count,
            }
        )

    if regenerated_share_ids:
        warnings.append(
            {
                "index": index,
                "source_file_id": source_file_id or None,
                "original_filename": original_filename,
                "warning": "One or more artifact share IDs conflicted with existing records and were regenerated during import.",
                "conflicting_artifact_share_ids": regenerated_share_ids,
            }
        )

    return restored_any


def _import_file_entries_for_target_user(
    db: Session,
    *,
    target_user: User,
    package_email: str,
    user_action: str,
    files: list[dict[str, Any]],
    loader,
    folder_id_map: dict[str, str] | None = None,
    project_id_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Import file entries and reconnect their folder/project ownership."""
    existing_source_ids, existing_duplicate_signatures = _build_existing_file_indexes(
        db, target_user.id
    )
    existing_share_ids = _load_existing_artifact_share_ids(db)
    resolved_folder_id_map = folder_id_map or {}
    resolved_project_id_map = project_id_map or {}

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for index, entry in enumerate(files):
        source_file_id = str(entry.get("id") or "").strip()
        try:
            file_bytes, original_filename, detected_file_type, file_sha256 = loader(
                entry
            )
            duplicate_signature = (
                original_filename,
                file_sha256,
                detected_file_type,
                len(file_bytes),
            )

            if source_file_id and source_file_id in existing_source_ids:
                skipped.append(
                    {
                        "index": index,
                        "source_file_id": source_file_id,
                        "original_filename": original_filename,
                        "reason": "already_imported",
                        "file_id": existing_source_ids[source_file_id],
                    }
                )
                continue

            if duplicate_signature in existing_duplicate_signatures:
                skipped.append(
                    {
                        "index": index,
                        "source_file_id": source_file_id or None,
                        "original_filename": original_filename,
                        "reason": "duplicate_content",
                        "file_id": existing_duplicate_signatures[
                            duplicate_signature
                        ],
                    }
                )
                continue

            source_meta = (
                dict(entry.get("meta") or {})
                if isinstance(entry.get("meta"), dict)
                else {}
            )
            source_meta.pop("origin", None)
            source_meta["original_filename"] = original_filename
            _revoke_imported_canvas_asset_approvals(source_meta)
            source_meta["sha256"] = file_sha256
            if source_file_id:
                source_meta["import_source_file_id"] = source_file_id
            source_meta["import_source_email"] = package_email
            if entry.get("folder_id"):
                source_meta["import_source_folder_id"] = entry.get("folder_id")
            if entry.get("project_id"):
                source_meta["import_source_project_id"] = entry.get("project_id")

            source_project_id = str(entry.get("project_id") or "").strip()
            mapped_project_id = resolved_project_id_map.get(source_project_id)
            if source_project_id and not mapped_project_id:
                # Canonical admin ZIPs preserve unused project UUIDs before
                # their nested byte archives are restored.  Verify ownership
                # before accepting that direct reference.
                from app.projects.models import Project

                owned_project = (
                    db.query(Project)
                    .filter(
                        Project.id == source_project_id,
                        Project.user_id == target_user.id,
                    )
                    .first()
                )
                if owned_project is not None:
                    mapped_project_id = source_project_id

            # Preserve a canonical source ID when it is a valid, unused UUID.
            # Admin ZIPs intentionally restore their nested byte bundles after
            # the account payload, so retaining the ID keeps chat/project
            # attachment references valid without a fragile second archive.
            preferred_file_id = None
            if source_file_id:
                try:
                    preferred_file_id = str(uuid.UUID(source_file_id))
                except ValueError:
                    preferred_file_id = None
                if preferred_file_id and db.query(Files).filter(
                    Files.id == preferred_file_id
                ).first():
                    preferred_file_id = None

            imported_file = persist_generated_file_bytes(
                db,
                user_id=target_user.id,
                original_filename=original_filename,
                file_bytes=file_bytes,
                file_type=detected_file_type,
                file_category=get_file_category(detected_file_type),
                meta=source_meta,
                file_id=preferred_file_id,
                project_id=mapped_project_id,
            )
            source_folder_id = str(entry.get("folder_id") or "").strip()
            mapped_folder_id = resolved_folder_id_map.get(source_folder_id)
            if mapped_folder_id:
                imported_file.folder_id = mapped_folder_id

            created_at = _parse_iso_datetime(entry.get("created_at"))
            last_updated_at = _parse_iso_datetime(entry.get("last_updated_at"))
            if created_at:
                imported_file.created_at = created_at
            if last_updated_at:
                imported_file.last_updated_at = last_updated_at
            restored_shares = _restore_artifact_share_entries(
                db,
                imported_file=imported_file,
                target_user=target_user,
                artifact_shares=entry.get("artifact_shares"),
                existing_share_ids=existing_share_ids,
                warnings=warnings,
                index=index,
                source_file_id=source_file_id,
                original_filename=original_filename,
            )
            if created_at or last_updated_at or restored_shares or mapped_folder_id:
                db.commit()
                db.refresh(imported_file)

            created.append(
                {
                    "index": index,
                    "source_file_id": source_file_id or None,
                    "file_id": imported_file.id,
                    "original_filename": original_filename,
                }
            )
            if source_file_id:
                existing_source_ids[source_file_id] = str(imported_file.id)
            existing_duplicate_signatures[duplicate_signature] = str(imported_file.id)
        except HTTPException as exc:
            errors.append(
                {
                    "index": index,
                    "source_file_id": source_file_id or None,
                    "original_filename": entry.get("original_filename")
                    or entry.get("file_name"),
                    "error": exc.detail,
                }
            )
        except Exception:
            logger.exception("Failed to import file at index %s", index)
            errors.append(
                {
                    "index": index,
                    "source_file_id": source_file_id or None,
                    "original_filename": entry.get("original_filename")
                    or entry.get("file_name"),
                    "error": "Failed to import file due to an internal error.",
                }
            )

    return {
        "target_user_id": target_user.id,
        "target_user_email": target_user.email,
        "user_action": user_action,
        "created_files": created,
        "created_files_count": len(created),
        "skipped_files": skipped,
        "skipped_files_count": len(skipped),
        "warnings": warnings,
        "errors": errors,
    }


def import_admin_user_inline_files_for_user(
    db: Session,
    *,
    target_user: User,
    source_email: str,
    files: list[dict[str, Any]],
    user_action: str = "updated",
    folder_id_map: dict[str, str] | None = None,
    project_id_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Import inline files for a user."""
    normalized_source_email = _normalize_user_email_reference(source_email)
    if not normalized_source_email:
        raise HTTPException(
            status_code=400,
            detail="Inline file import is missing a source email reference.",
        )
    if not isinstance(files, list):
        raise HTTPException(
            status_code=400,
            detail="Inline file import payload must contain a 'files' list.",
        )

    for entry in files:
        if not isinstance(entry, dict):
            raise HTTPException(
                status_code=400, detail="Each inline file entry must be an object."
            )
        entry_email = _normalize_user_email_reference(entry.get("email"))
        if entry_email and entry_email != normalized_source_email:
            raise HTTPException(
                status_code=400,
                detail="Inline file entry email does not match the source email.",
            )

    return _import_file_entries_for_target_user(
        db,
        target_user=target_user,
        package_email=normalized_source_email,
        user_action=user_action,
        files=files,
        loader=_load_and_validate_inline_file_entry,
        folder_id_map=folder_id_map,
        project_id_map=project_id_map,
    )


def import_admin_user_files_archive(
    db: Session,
    archive: zipfile.ZipFile,
    *,
    expected_email: str | None = None,
    project_id_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Import a nested canonical file archive for its target account."""
    from app.users.utils import (
        _bulk_insert_file_folders,
        _bulk_insert_shared_file_folder_subscriptions,
    )

    manifest = _read_manifest(archive)
    user_block = manifest.get("user") if isinstance(manifest.get("user"), dict) else {}
    package_email = _normalize_user_email_reference(user_block.get("email"))
    if not package_email:
        raise HTTPException(
            status_code=400,
            detail="Invalid file package. Missing a user email reference.",
        )

    normalized_expected_email = _normalize_user_email_reference(expected_email)
    if normalized_expected_email and normalized_expected_email != package_email:
        raise HTTPException(
            status_code=400,
            detail="Invalid file package. Manifest user email does not match the expected user email.",
        )

    files = manifest.get("files")
    if not isinstance(files, list):
        raise HTTPException(
            status_code=400,
            detail="Invalid file package. Manifest 'files' must be a list.",
        )
    folders = _manifest_list_or_empty(manifest, "folders")
    shared_file_folder_subscriptions = _manifest_list_or_empty(
        manifest, "shared_file_folder_subscriptions"
    )

    if len(files) > MAX_IMPORT_FILE_COUNT:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file package. Too many files (max {MAX_IMPORT_FILE_COUNT}).",
        )

    declared_total_size = 0
    for entry in files:
        if isinstance(entry, dict):
            try:
                declared_total_size += int(entry.get("file_size") or 0)
            except (TypeError, ValueError):
                pass

    running_total_size = 0
    if declared_total_size > 0:
        running_total_size = declared_total_size
    else:
        for entry in files:
            if not isinstance(entry, dict):
                continue
            archive_name = entry.get("archive_name")
            if not archive_name:
                continue
            try:
                info = archive.getinfo(_validate_archive_name(archive_name))
            except Exception:
                continue
            running_total_size += int(getattr(info, "file_size", 0) or 0)

    if running_total_size > MAX_IMPORT_TOTAL_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file package. Total extracted size exceeds the limit ({MAX_IMPORT_TOTAL_SIZE} bytes).",
        )

    enforced_running_total = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise HTTPException(
                status_code=400,
                detail="Invalid file package. Each file entry must be an object.",
            )
        entry_email = _normalize_user_email_reference(entry.get("email"))
        if entry_email and entry_email != package_email:
            raise HTTPException(
                status_code=400,
                detail="Invalid file package. File email does not match manifest email.",
            )

        entry_size = 0
        try:
            entry_size = int(entry.get("file_size") or 0)
        except (TypeError, ValueError):
            entry_size = 0
        if entry_size <= 0:
            archive_name = entry.get("archive_name")
            if archive_name:
                try:
                    info = archive.getinfo(_validate_archive_name(archive_name))
                    entry_size = int(getattr(info, "file_size", 0) or 0)
                except Exception:
                    entry_size = 0

        if entry_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail="Invalid file package. One or more files exceed the maximum file size.",
            )

        enforced_running_total += max(0, entry_size)
        if enforced_running_total > MAX_IMPORT_TOTAL_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file package. Total extracted size exceeds the limit ({MAX_IMPORT_TOTAL_SIZE} bytes).",
            )

    target_user, user_action = _resolve_existing_user_by_email(db, package_email)
    folder_id_map: dict[str, str] = {}
    warnings: list[dict[str, Any]] = []
    if folders:
        folder_id_map, folder_warnings = _bulk_insert_file_folders(
            db, target_user.id, folders
        )
        warnings.extend(folder_warnings)
    if shared_file_folder_subscriptions:
        warnings.extend(
            _bulk_insert_shared_file_folder_subscriptions(
                db,
                target_user.id,
                shared_file_folder_subscriptions,
                folder_id_map=folder_id_map,
            )
        )

    result = _import_file_entries_for_target_user(
        db,
        target_user=target_user,
        package_email=package_email,
        user_action=user_action,
        files=files,
        loader=lambda entry: _load_and_validate_file_entry(archive, entry),
        folder_id_map=folder_id_map,
        project_id_map=project_id_map,
    )
    result["warnings"] = warnings + [
        warning for warning in result.get("warnings", []) if isinstance(warning, dict)
    ]
    result["restored_folder_count"] = len(folder_id_map)
    result["restored_shared_file_folder_subscription_count"] = max(
        0,
        len(shared_file_folder_subscriptions)
        - len(
            [
                warning
                for warning in warnings
                if warning.get("section") == "shared_file_folder_subscriptions"
            ]
        ),
    )
    return result
