from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any, BinaryIO

from app.admin.user_exports.files.models import (
    export_admin_user_files_bundle,
    import_admin_user_files_archive,
)
from app.users.models import get_user
from app.users.utils import (
    ADMIN_USER_EXPORT_VERSION,
    import_users_admin,
    iter_admin_export_users,
    iter_user_data_export_json,
    reconnect_imported_user_archive_file_references,
)
from app.utils.email import normalize_email
from fastapi import HTTPException
from sqlalchemy.orm import Session

ADMIN_USERS_ARCHIVE_EXPORT_TYPE = "admin_users_bundle"
ADMIN_USERS_ARCHIVE_EXPORT_VERSION = 1.0
ADMIN_USERS_ARCHIVE_MANIFEST_NAME = "manifest.json"
ADMIN_USERS_ARCHIVE_INDEX_NAME = "users-index.json"
ADMIN_USERS_ARCHIVE_USERS_DIR = "users"
ADMIN_USERS_ARCHIVE_FILES_DIR = "user-files"

# This registry is the explicit portability contract for canonical user
# archives. Export-only and security-sensitive server state is intentionally
# described beside restorable data so the manifest never overclaims coverage.
CANONICAL_USER_ARCHIVE_SECTIONS = {
    "restorable": [
        "account",
        "settings",
        "chats",
        "notes",
        "todos",
        "memories",
        "files",
        "file_folders",
        "shared_file_folder_subscriptions",
        "projects",
        "automations",
        "skills",
        "skill_files",
        "shared_skill_subscriptions",
        "agents",
        "agent_assets",
        "prompts",
        "shared_prompt_subscriptions",
        "user_connections",
        "mcp_servers",
        "model_setting_presets",
        "slide_presentations",
        "deep_research_runs",
    ],
    "export_only": [
        "group_reference",
        "auth_metadata",
        "activity_logs",
        "feedback",
        "usage_stats",
        "shared_agent_subscriptions",
    ],
    "instance_owned": ["notifications", "groups", "provider_configuration"],
    "excluded_security_material": [
        "password_hashes",
        "session_credentials",
        "oauth_states",
        "social_auth_identities",
        "scim_links",
    ],
}

ARCHIVE_IO_CHUNK_SIZE = 1024 * 1024
# Large entries may be streamed to disk for checksum verification, but JSON
# parsing still creates an in-memory Python object graph. Keep those limits
# separate so a valid checksum cannot authorize an unsafe parser allocation.
ARCHIVE_MANIFEST_MAX_BYTES = 2 * 1024 * 1024
ARCHIVE_USER_INDEX_MAX_BYTES = 128 * 1024 * 1024
ARCHIVE_PARSED_JSON_ENTRY_MAX_BYTES = 128 * 1024 * 1024
EXPORT_ARCHIVE_SPOOL_THRESHOLD_BYTES = 8 * 1024 * 1024
NESTED_ARCHIVE_SPOOL_THRESHOLD_BYTES = 8 * 1024 * 1024
NESTED_ARCHIVE_MAX_BYTES = 1024 * 1024 * 1024


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, default=str, separators=(",", ":"))


def _read_archive_entry_to_spooled_file(
    archive: zipfile.ZipFile,
    entry_name: str,
    *,
    max_bytes: int,
    missing_detail: str,
    oversize_detail: str,
) -> BinaryIO:
    """
    Read an archive entry into a spooled file with size limit.

    Args:
    - archive: The ZIP archive to read from.
    - entry_name: The name of the entry to read.
    - max_bytes: The maximum size of the entry in bytes.
    - missing_detail: The error message to raise if the entry is missing.
    - oversize_detail: The error message to raise if the entry exceeds the maximum size.

    Returns:
    - A BinaryIO object containing the entry's contents.
    """
    try:
        source_handle = archive.open(entry_name)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=missing_detail) from exc

    staged_file = tempfile.SpooledTemporaryFile(
        max_size=NESTED_ARCHIVE_SPOOL_THRESHOLD_BYTES, mode="w+b"
    )
    total_size = 0
    try:
        with source_handle:
            while True:
                chunk = source_handle.read(ARCHIVE_IO_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_bytes:
                    raise HTTPException(status_code=400, detail=oversize_detail)
                staged_file.write(chunk)
    except Exception:
        staged_file.close()
        raise

    staged_file.seek(0)
    return staged_file


def _write_filelike_to_zip_entry(
    archive: zipfile.ZipFile, entry_name: str, file_obj: BinaryIO
) -> None:
    """
    Write a file-like object to a ZIP archive entry.

    Args:
    - archive: The ZIP archive to write to.
    - entry_name: The name of the entry to write.
    - file_obj: The file-like object to write.
    """
    file_obj.seek(0)
    with archive.open(entry_name, "w") as target_handle:
        shutil.copyfileobj(file_obj, target_handle, length=ARCHIVE_IO_CHUNK_SIZE)


def _write_json_chunks_to_zip_entry(
    archive: zipfile.ZipFile,
    entry_name: str,
    chunks,
    *,
    max_bytes: int | None = None,
) -> str:
    """Write bounded streamed JSON and return its SHA-256 digest.

    Applying the same entry bound while exporting and importing guarantees
    that a completed archive produced by this server is also readable by it.
    """
    digest = hashlib.sha256()
    total_size = 0
    with archive.open(entry_name, "w") as target_handle:
        for chunk in chunks:
            encoded = str(chunk).encode("utf-8")
            total_size += len(encoded)
            if max_bytes is not None and total_size > max_bytes:
                raise RuntimeError(
                    f"Archive entry '{entry_name}' exceeds the supported size. "
                    "Export a smaller user selection."
                )
            digest.update(encoded)
            target_handle.write(encoded)
    return digest.hexdigest()


def _sha256_filelike(file_obj: BinaryIO) -> str:
    """Hash a seekable file-like object without changing its final position."""
    digest = hashlib.sha256()
    file_obj.seek(0)
    while True:
        chunk = file_obj.read(ARCHIVE_IO_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    file_obj.seek(0)
    return digest.hexdigest()


def _verify_filelike_checksum(
    file_obj: BinaryIO,
    expected_checksum: Any,
    *,
    entry_name: str,
) -> None:
    """Reject a corrupted archive entry when its manifest declares a digest."""
    if expected_checksum is None:
        # The outer manifest is intentionally not self-checksummed. Every
        # canonical payload entry requires a digest before reaching this helper.
        return
    expected = str(expected_checksum).strip().lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid checksum metadata for '{entry_name}'.",
        )
    actual = _sha256_filelike(file_obj)
    if actual != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Archive integrity check failed for '{entry_name}'.",
        )


def _collect_admin_export_user_references(
    db: Session,
    user_ids: list[str] | None = None,
) -> list[tuple[str, str | None]]:
    """Snapshot selected user IDs and email references before nested queries."""
    references: list[tuple[str, str | None]] = []
    if user_ids is None:
        users = iter_admin_export_users(db)
    else:
        # Resolve the explicit order supplied by the caller. Stable ordering is
        # important because nested file bundles refer to users by archive index.
        resolved_users = []
        seen_ids: set[str] = set()
        for raw_user_id in user_ids:
            user_id = str(raw_user_id or "").strip()
            if not user_id or user_id in seen_ids:
                continue
            seen_ids.add(user_id)
            user = get_user(db, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            resolved_users.append(user)
        users = iter(resolved_users)

    for user in users:
        user_id = str(getattr(user, "id", "") or "").strip()
        if not user_id:
            continue
        references.append((user_id, normalize_email(getattr(user, "email", None))))
    return references


def _sanitize_filename_part(value: Any, fallback: str = "user") -> str:
    """
    Sanitize a string for use in a filename.

    Args:
    - value: The string to sanitize.
    - fallback: The fallback value to use if the string is empty.

    Returns:
    - The sanitized string.
    """
    normalized = str(value or "").strip()
    normalized = "".join("-" if char in '\\/:*?"<>|' else char for char in normalized)
    normalized = "-".join(part for part in normalized.split())
    return normalized or fallback


def _read_json_archive_entry(
    archive: zipfile.ZipFile,
    entry_name: str,
    missing_detail: str,
    *,
    expected_checksum: Any = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """
    Read a JSON entry from a ZIP archive.

    Args:
    - archive: The ZIP archive to read from.
    - entry_name: The name of the entry to read.
    - missing_detail: The error message to raise if the entry is missing.

    Returns:
    - A dictionary containing the entry's contents.
    """
    resolved_max_bytes = (
        max_bytes if max_bytes is not None else ARCHIVE_PARSED_JSON_ENTRY_MAX_BYTES
    )
    try:
        with _read_archive_entry_to_spooled_file(
            archive,
            entry_name,
            max_bytes=resolved_max_bytes,
            missing_detail=missing_detail,
            oversize_detail=(
                f"Invalid archive. Parsed JSON entry '{entry_name}' exceeds the "
                "maximum allowed size. Export and import smaller user selections."
            ),
        ) as staged_file:
            # Verify the exact bytes that will be parsed. Reusing this staged
            # file avoids inflating and reading a large ZIP entry twice.
            _verify_filelike_checksum(
                staged_file,
                expected_checksum,
                entry_name=entry_name,
            )
            text_stream = io.TextIOWrapper(staged_file, encoding="utf-8")
            try:
                payload = json.load(text_stream)
            finally:
                # The surrounding context owns the staged binary file.
                text_stream.detach()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid JSON in '{entry_name}'."
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON in '{entry_name}'. Expected an object.",
        )
    return payload


def _read_admin_users_archive(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read and validate the manifest and user index from an admin archive."""
    manifest = _read_json_archive_entry(
        archive,
        ADMIN_USERS_ARCHIVE_MANIFEST_NAME,
        "Invalid users archive. Missing manifest.json.",
        max_bytes=ARCHIVE_MANIFEST_MAX_BYTES,
    )
    if manifest.get("export_type") != ADMIN_USERS_ARCHIVE_EXPORT_TYPE:
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive. Expected an admin users bundle.",
        )
    if manifest.get("export_version") != ADMIN_USERS_ARCHIVE_EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported users archive version. Expected {ADMIN_USERS_ARCHIVE_EXPORT_VERSION}.",
        )

    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive. 'checksums' must be an object.",
        )
    entries = manifest.get("entries")
    if not isinstance(entries, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive. 'entries' must be an object.",
        )

    user_index_name = str(entries.get("user_index") or "").strip()
    if not user_index_name:
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive. User index entry is required.",
        )
    expected_index_checksum = checksums.get(user_index_name)
    if expected_index_checksum is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive. User index checksum is required.",
        )

    index_payload = _read_json_archive_entry(
        archive,
        user_index_name,
        f"Invalid users archive. Missing '{user_index_name}'.",
        expected_checksum=expected_index_checksum,
        max_bytes=ARCHIVE_USER_INDEX_MAX_BYTES,
    )
    if index_payload.get("export_type") != "admin_user_index":
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive. Expected an admin user index.",
        )
    if index_payload.get("export_version") != ADMIN_USERS_ARCHIVE_EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported user index version. Expected "
                f"{ADMIN_USERS_ARCHIVE_EXPORT_VERSION}."
            ),
        )

    indexed_users = index_payload.get("users")
    if not isinstance(indexed_users, list):
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive. User index must contain a users array.",
        )
    if manifest.get("user_count") != len(indexed_users):
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive. User index count does not match manifest.",
        )

    seen_payload_paths: set[str] = set()
    seen_user_ids: set[str] = set()
    for index, entry in enumerate(indexed_users):
        payload_ref = entry.get("payload") if isinstance(entry, dict) else None
        payload_path = (
            str(payload_ref.get("path") or "").strip()
            if isinstance(payload_ref, dict)
            else ""
        )
        payload_checksum = (
            str(payload_ref.get("sha256") or "").strip().lower()
            if isinstance(payload_ref, dict)
            else ""
        )
        indexed_user_id = (
            str(entry.get("user_id") or "").strip()
            if isinstance(entry, dict)
            else ""
        )
        if (
            not isinstance(entry, dict)
            or entry.get("index") != index
            or not indexed_user_id
            or indexed_user_id in seen_user_ids
            or not payload_path
            or payload_path in seen_payload_paths
            or len(payload_checksum) != 64
            or any(char not in "0123456789abcdef" for char in payload_checksum)
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid users archive. User index entry is malformed.",
            )
        seen_user_ids.add(indexed_user_id)
        seen_payload_paths.add(payload_path)

    return manifest, index_payload
def _write_admin_users_archive(
    db: Session,
    db_log,
    archive: zipfile.ZipFile,
    *,
    user_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write a canonical archive with one independently readable entry per user.

    The index-and-shards layout keeps the manifest small and lets imports
    inflate only the selected account payloads.
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    user_references = _collect_admin_export_user_references(db, user_ids)
    user_count = len(user_references)
    user_index_entries: list[dict[str, Any]] = []
    user_files_count = 0
    user_file_warning_count = 0

    for index, (source_user_id, email) in enumerate(user_references):
        archive_base = _sanitize_filename_part(
            email or source_user_id, f"user-{index + 1}"
        )
        user_payload_path = (
            f"{ADMIN_USERS_ARCHIVE_USERS_DIR}/{index:06d}-{archive_base}.json"
        )
        user_payload_sha256 = _write_json_chunks_to_zip_entry(
            archive,
            user_payload_path,
            iter_user_data_export_json(
                source_user_id,
                db,
                db_log,
                include_file_contents=False,
                include_files_section=False,
                # Administrative full-user bundles are intended to preserve
                # every retained conversation for backup and migration. This
                # includes saved temporary chats and chats awaiting permanent
                # deletion after a soft/shadow delete.
                include_deleted_or_temp_chats=True,
            ),
            max_bytes=ARCHIVE_PARSED_JSON_ENTRY_MAX_BYTES,
        )
        index_entry: dict[str, Any] = {
            "index": index,
            "user_id": source_user_id,
            "email": email,
            "payload": {
                "path": user_payload_path,
                "sha256": user_payload_sha256,
            },
        }

        files_path = f"{ADMIN_USERS_ARCHIVE_FILES_DIR}/{index:06d}-{archive_base}.zip"

        files_buffer, _, files_manifest = export_admin_user_files_bundle(
            db, source_user_id
        )
        file_count = int(files_manifest.get("file_count") or 0)
        warnings = [
            warning
            for warning in files_manifest.get("warnings") or []
            if isinstance(warning, dict)
        ]
        if warnings:
            user_file_warning_count += len(warnings)
        if file_count <= 0:
            files_buffer.close()
            if warnings:
                index_entry["file_warnings"] = warnings
            user_index_entries.append(index_entry)
            continue

        try:
            file_sha256 = _sha256_filelike(files_buffer)
            _write_filelike_to_zip_entry(archive, files_path, files_buffer)
        finally:
            files_buffer.close()
        user_files_count += 1
        index_entry["files"] = {
            "path": files_path,
            "file_count": file_count,
            "sha256": file_sha256,
            "warnings": warnings,
        }
        user_index_entries.append(index_entry)

    index_payload = {
        "export_type": "admin_user_index",
        "export_version": ADMIN_USERS_ARCHIVE_EXPORT_VERSION,
        "generated_at": generated_at,
        "users": user_index_entries,
    }
    user_index_sha256 = _write_json_chunks_to_zip_entry(
        archive,
        ADMIN_USERS_ARCHIVE_INDEX_NAME,
        iter([_json_dumps(index_payload)]),
        max_bytes=ARCHIVE_USER_INDEX_MAX_BYTES,
    )

    manifest = {
        "export_type": ADMIN_USERS_ARCHIVE_EXPORT_TYPE,
        "export_version": ADMIN_USERS_ARCHIVE_EXPORT_VERSION,
        "generated_at": generated_at,
        "entries": {
            "user_index": ADMIN_USERS_ARCHIVE_INDEX_NAME,
        },
        "checksums": {ADMIN_USERS_ARCHIVE_INDEX_NAME: user_index_sha256},
        "portability": CANONICAL_USER_ARCHIVE_SECTIONS,
        "user_count": user_count,
        "user_files_count": user_files_count,
        "user_file_warning_count": user_file_warning_count,
    }
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=True)
    if len(manifest_json.encode("utf-8")) > ARCHIVE_MANIFEST_MAX_BYTES:
        # This should remain practically unreachable because per-user metadata
        # lives in the separately bounded index, but keep export/import bounds
        # symmetric if the manifest contract grows in the future.
        raise RuntimeError("Archive manifest exceeds the supported size.")
    archive.writestr(
        ADMIN_USERS_ARCHIVE_MANIFEST_NAME,
        manifest_json,
    )
    return manifest


def _admin_users_archive_filename() -> str:
    """Build a timestamped filename for an all-users admin export archive."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"admin-users-{timestamp}.zip"


def export_admin_users_archive(
    db: Session,
    db_log,
    *,
    user_ids: list[str] | None = None,
) -> tuple[BinaryIO, str, dict[str, Any]]:
    """
    Export all users as a ZIP archive.

    Args:
    - db: The database session to use.
    - db_log: The database log to use.

    Returns:
    - A tuple containing the ZIP archive, filename, and manifest.
    """
    zip_buffer = tempfile.SpooledTemporaryFile(
        max_size=EXPORT_ARCHIVE_SPOOL_THRESHOLD_BYTES, mode="w+b"
    )

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest = _write_admin_users_archive(
            db,
            db_log,
            archive,
            user_ids=user_ids,
        )

    zip_buffer.seek(0)
    return zip_buffer, _admin_users_archive_filename(), manifest


def export_admin_users_archive_to_path(
    db: Session,
    db_log,
    target_path,
    *,
    user_ids: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Export all users directly to a filesystem ZIP archive path."""
    with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest = _write_admin_users_archive(
            db,
            db_log,
            archive,
            user_ids=user_ids,
        )
    return _admin_users_archive_filename(), manifest


def import_admin_users_archive(
    db: Session,
    archive: zipfile.ZipFile,
    *,
    selected_indices: list[int] | None = None,
    import_options: dict[str, Any] | None = None,
    allow_administrative_targets: bool = False,
) -> dict[str, Any]:
    """
    Import users from an admin archive.

    Args:
    - db: The database session to use.
    - archive: The ZIP archive to import from.
    - selected_indices: The indices of the users to import.

    Returns:
    - A dictionary containing the import results.
    """
    manifest, index_payload = _read_admin_users_archive(archive)
    raw_users = index_payload.get("users", [])
    if not isinstance(raw_users, list):
        raise HTTPException(
            status_code=400,
            detail="Invalid users archive payload. 'users' must be a list.",
        )

    available_indices = list(range(len(raw_users)))
    if selected_indices is None:
        target_indices = available_indices
    else:
        normalized_indices: list[int] = []
        for value in selected_indices:
            if not isinstance(value, int):
                raise HTTPException(
                    status_code=400,
                    detail="selected_indices must contain only integers.",
                )
            if value < 0 or value >= len(raw_users):
                raise HTTPException(
                    status_code=400,
                    detail=f"selected_indices contains out-of-range index {value}.",
                )
            normalized_indices.append(value)
        target_indices = sorted(dict.fromkeys(normalized_indices))

    selected_users: list[dict[str, Any]] = []
    manifest_file_entries: list[dict[str, Any]] = []
    # Inflate and validate only the selected account shards. The checksummed
    # index is sufficient to preview and select accounts without allocating
    # every user's full payload in one Python object graph.
    for index in target_indices:
        index_entry = raw_users[index]
        payload_ref = index_entry.get("payload")
        payload_path = str(payload_ref.get("path") or "").strip()
        user_payload = _read_json_archive_entry(
            archive,
            payload_path,
            f"Invalid users archive. Missing '{payload_path}'.",
            expected_checksum=payload_ref.get("sha256"),
            max_bytes=ARCHIVE_PARSED_JSON_ENTRY_MAX_BYTES,
        )
        if (
            user_payload.get("export_type") != "user_data"
            or user_payload.get("export_version") != ADMIN_USER_EXPORT_VERSION
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid user payload in '{payload_path}'.",
            )
        profile = (
            user_payload.get("user")
            if isinstance(user_payload.get("user"), dict)
            else {}
        )
        payload_email = normalize_email(
            profile.get("email") or user_payload.get("email")
        )
        payload_user_id = str(
            profile.get("user_id") or user_payload.get("user_id") or ""
        ).strip()
        index_user_id = str(index_entry.get("user_id") or "").strip()
        if normalize_email(index_entry.get("email")) != payload_email or (
            index_user_id and index_user_id != payload_user_id
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid users archive. User index does not match account payload.",
            )
        selected_users.append(user_payload)

        files_ref = index_entry.get("files")
        if isinstance(files_ref, dict):
            files_path = str(files_ref.get("path") or "").strip()
            files_checksum = str(files_ref.get("sha256") or "").strip().lower()
            if (
                not files_path
                or len(files_checksum) != 64
                or any(char not in "0123456789abcdef" for char in files_checksum)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid users archive. File bundle index is malformed.",
                )
            manifest_file_entries.append(
                {
                    "index": index,
                    "email": index_entry.get("email"),
                    **files_ref,
                }
            )
    # Validate every selected nested bundle before mutating any account. This
    # prevents a corrupt archive from producing a half-restored user set.
    for file_entry in manifest_file_entries:
        if (
            not isinstance(file_entry, dict)
            or file_entry.get("index") not in target_indices
        ):
            continue
        archive_path = str(file_entry.get("path") or "").strip()
        if not archive_path:
            raise HTTPException(
                status_code=400,
                detail="Invalid users archive. A selected file bundle is missing its path.",
            )
        with _read_archive_entry_to_spooled_file(
            archive,
            archive_path,
            max_bytes=NESTED_ARCHIVE_MAX_BYTES,
            missing_detail=f"Invalid users archive. Missing '{archive_path}'.",
            oversize_detail=f"Invalid users archive. '{archive_path}' exceeds the maximum allowed size.",
        ) as nested_archive_file:
            _verify_filelike_checksum(
                nested_archive_file,
                file_entry.get("sha256"),
                entry_name=archive_path,
            )
            try:
                # Parse the central directory during preflight so a selected
                # corrupt nested package cannot be discovered only after its
                # account has already been changed.
                with zipfile.ZipFile(nested_archive_file) as nested_archive:
                    nested_archive.infolist()
            except zipfile.BadZipFile as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid users archive. '{archive_path}' is not a valid ZIP file.",
                ) from exc

    filtered_payload = {
        "export_type": "admin_user",
        "export_version": ADMIN_USER_EXPORT_VERSION,
        "generated_at": manifest.get("generated_at"),
        "data": {
            "user_reference_map": {
                str(entry.get("user_id")): normalize_email(entry.get("email"))
                for entry in (raw_users[index] for index in target_indices)
                if str(entry.get("user_id") or "").strip()
                and normalize_email(entry.get("email"))
            },
            "users": selected_users,
        },
    }
    for selected_user in selected_users:
        if isinstance(selected_user, dict):
            # Canonical ZIP archives store byte content in validated nested ZIP
            # bundles. Account shards contain metadata only and must not
            # be sent through the inline/base64 file importer as well.
            selected_user.pop("files", None)
    filtered_payload["data"]["users"] = selected_users
    if import_options is not None:
        filtered_payload["import_options"] = import_options

    result = import_users_admin(
        filtered_payload,
        db,
        allow_administrative_targets=allow_administrative_targets,
        include_internal_restore_maps=True,
    )
    successful_emails = {
        normalize_email(entry.get("email"))
        for collection in (result.get("created", []), result.get("updated", []))
        for entry in collection
        if isinstance(entry, dict) and normalize_email(entry.get("email"))
    }
    per_email_summary = {
        normalize_email(entry.get("email")): entry
        for collection in (result.get("created", []), result.get("updated", []))
        for entry in collection
        if isinstance(entry, dict) and normalize_email(entry.get("email"))
    }

    total_created_files = 0
    total_skipped_files = 0
    file_results: list[dict[str, Any]] = []

    for file_entry in manifest_file_entries:
        if not isinstance(file_entry, dict):
            continue
        index = file_entry.get("index")
        if index not in target_indices:
            continue

        email = normalize_email(file_entry.get("email"))
        if not email or email not in successful_emails:
            result.setdefault("warnings", []).append(
                {
                    "index": index,
                    "email": email,
                    "section": "files",
                    "warning": "Skipped file import because the user account import did not complete successfully",
                }
            )
            continue

        archive_path = str(file_entry.get("path") or "").strip()
        if not archive_path:
            result.setdefault("warnings", []).append(
                {
                    "index": index,
                    "email": email,
                    "section": "files",
                    "warning": "Skipped file import because the users archive is missing the nested file bundle path",
                }
            )
            continue

        try:
            with _read_archive_entry_to_spooled_file(
                archive,
                archive_path,
                max_bytes=NESTED_ARCHIVE_MAX_BYTES,
                missing_detail=f"Skipped file import because '{archive_path}' is missing from the archive",
                oversize_detail=f"Skipped file import because '{archive_path}' exceeds the maximum allowed size",
            ) as nested_archive_file:
                with zipfile.ZipFile(nested_archive_file) as nested_archive:
                    summary = per_email_summary.get(email) or {}
                    file_result = import_admin_user_files_archive(
                        db,
                        nested_archive,
                        expected_email=email,
                        project_id_map=summary.get("_project_id_map") or {},
                    )
        except HTTPException as exc:
            result.setdefault("warnings", []).append(
                {
                    "index": index,
                    "email": email,
                    "section": "files",
                    "warning": str(exc.detail),
                }
            )
            continue
        except zipfile.BadZipFile:
            result.setdefault("warnings", []).append(
                {
                    "index": index,
                    "email": email,
                    "section": "files",
                    "warning": f"Skipped file import because '{archive_path}' is not a valid ZIP file",
                }
            )
            continue

        total_created_files += int(file_result.get("created_files_count", 0))
        total_skipped_files += int(file_result.get("skipped_files_count", 0))
        file_results.append(file_result)

        summary = per_email_summary.get(email)
        if summary is not None:
            summary["created_files_count"] = int(
                file_result.get("created_files_count", 0)
            )
            summary["skipped_files_count"] = int(
                file_result.get("skipped_files_count", 0)
            )
            summary["file_errors"] = file_result.get("errors", []) or []
            file_id_map = {
                str(entry.get("source_file_id")): str(entry.get("file_id"))
                for entry in (
                    list(file_result.get("created_files", []))
                    + list(file_result.get("skipped_files", []))
                )
                if isinstance(entry, dict)
                and entry.get("source_file_id")
                and entry.get("file_id")
            }
            reconnect_imported_user_archive_file_references(
                db,
                project_id_map=summary.get("_project_id_map") or {},
                chat_id_map=summary.get("_chat_id_map") or {},
                file_id_map=file_id_map,
            )

        if file_result.get("errors"):
            result.setdefault("warnings", []).append(
                {
                    "index": index,
                    "email": email,
                    "section": "files",
                    "warning": "Some user files could not be imported",
                    "details": file_result.get("errors", []),
                }
            )

    for collection in (result.get("created", []), result.get("updated", [])):
        for summary in collection:
            if isinstance(summary, dict):
                summary.pop("_project_id_map", None)
                summary.pop("_chat_id_map", None)

    result["created_files_count"] = total_created_files
    result["skipped_files_count"] = total_skipped_files
    result["file_results"] = file_results
    return result
