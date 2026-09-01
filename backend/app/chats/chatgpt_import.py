from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import tempfile
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.chats.models import ChatMessages, Chats, _meta_to_dict
from app.files.models import Files
from app.files.storage import upload_file_to_storage
from app.files.utils import (
    _detect_mime_from_content,
    _find_duplicate_file,
    _upload_is_valid_active_content,
    delete_storage_reference,
    ensure_upload_file_size_limits,
    ensure_user_file_upload_capacity,
    get_file_category,
    resolve_user_file_upload_limits,
    resolve_user_max_upload_size_bytes,
    serialized_user_file_quota_admission,
    validate_file_type,
)
from app.llm.helper import build_tool_call_block


logger = logging.getLogger(__name__)

SUPPORTED_CONVERSATION_EXPORTS = ("conversations.json", "group_chats.json")
CHATGPT_IMPORT_IO_CHUNK_SIZE = 1024 * 1024
CHATGPT_IMPORT_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
CHATGPT_IMPORT_MAX_ENTRIES = 20_000
CHATGPT_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
CHATGPT_IMPORT_MAX_JSON_ENTRY_BYTES = 256 * 1024 * 1024
CHATGPT_IMPORT_MAX_ASSET_BYTES = 512 * 1024 * 1024
CHATGPT_IMPORT_MAX_COMPRESSION_RATIO = 100
PRIVATE_USE_MARKERS_RE = re.compile(r"[\ue200-\ue206]")
FILE_ID_RE = re.compile(r"(file_[A-Za-z0-9]+|file-[A-Za-z0-9]+)")
TOOL_CALL_RE = re.compile(r"^\s*([A-Za-z_][\w]*)\((.*)\)\s*$", re.DOTALL)

TOOL_RESULT_TYPES = {
    "execution_output",
    "system_error",
    "sonic_webpage",
    "tether_browsing_display",
    "tether_quote",
}


def _too_large(detail: str) -> HTTPException:
    return HTTPException(status_code=413, detail=detail)


def _entry_display_name(entry_name: str) -> str:
    return PurePosixPath(entry_name).name or entry_name


def _validate_archive_member_path(raw_path: str) -> None:
    """Reject archive member paths that could be ambiguous or unsafe."""
    path = PurePosixPath(raw_path)
    if not raw_path or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=400, detail=f"Invalid archive entry path: {raw_path}")
    if "\x00" in raw_path:
        raise HTTPException(status_code=400, detail=f"Invalid archive entry path: {raw_path}")


def _validate_upload_size(
    archive_file: BinaryIO,
    *,
    max_upload_bytes: int | None = None,
    max_upload_mb: int | None = None,
) -> None:
    """Reject uploads whose compressed ZIP container is too large."""
    if not all(hasattr(archive_file, attr) for attr in ("tell", "seek")):
        return

    try:
        original_position = archive_file.tell()
        archive_file.seek(0, os.SEEK_END)
        archive_size = archive_file.tell()
        archive_file.seek(original_position)
    except (OSError, ValueError):
        return

    if max_upload_bytes is not None and max_upload_mb is not None:
        ensure_upload_file_size_limits(
            archive_size,
            max_upload_bytes=max_upload_bytes,
            max_upload_mb=max_upload_mb,
            global_limit_detail="ChatGPT import archive exceeds the maximum allowed size.",
        )

    if archive_size > CHATGPT_IMPORT_MAX_ARCHIVE_BYTES:
        raise _too_large("ChatGPT import archive exceeds the maximum allowed size.")


def _validate_archive_limits(archive: zipfile.ZipFile) -> None:
    """Validate archive member count, sizes, and compression ratios before import."""
    infos = archive.infolist()
    if len(infos) > CHATGPT_IMPORT_MAX_ENTRIES:
        raise _too_large("ChatGPT import archive contains too many entries.")

    total_uncompressed = 0
    for info in infos:
        _validate_archive_member_path(info.filename)
        if info.is_dir():
            continue

        file_size = max(0, int(info.file_size or 0))
        compressed_size = max(0, int(info.compress_size or 0))
        total_uncompressed += file_size
        if total_uncompressed > CHATGPT_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise _too_large("ChatGPT import archive uncompressed contents exceed the maximum allowed size.")

        name = PurePosixPath(info.filename).name
        if name in SUPPORTED_CONVERSATION_EXPORTS or name == "shared_conversations.json":
            max_entry_size = CHATGPT_IMPORT_MAX_JSON_ENTRY_BYTES
        else:
            max_entry_size = CHATGPT_IMPORT_MAX_ASSET_BYTES

        if file_size > max_entry_size:
            raise _too_large(f"ChatGPT import archive entry '{_entry_display_name(info.filename)}' is too large.")

        if compressed_size > 0 and file_size / compressed_size > CHATGPT_IMPORT_MAX_COMPRESSION_RATIO:
            raise _too_large(
                f"ChatGPT import archive entry '{_entry_display_name(info.filename)}' "
                "exceeds the compression ratio limit."
            )


def _read_archive_entry_limited(
    archive: zipfile.ZipFile,
    entry_name: str,
    *,
    max_bytes: int,
    oversize_detail: str,
) -> bytes:
    """Read an archive entry while enforcing an uncompressed byte limit."""
    payload = bytearray()
    total_size = 0
    try:
        with archive.open(entry_name) as source:
            while True:
                chunk = source.read(CHATGPT_IMPORT_IO_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_bytes:
                    raise _too_large(oversize_detail)
                payload.extend(chunk)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Missing archive entry: {entry_name}") from exc
    return bytes(payload)


def _read_json_archive_entry(archive: zipfile.ZipFile, entry_name: str) -> Any:
    """Read and parse a JSON archive entry with a strict uncompressed size limit."""
    try:
        raw_payload = _read_archive_entry_limited(
            archive,
            entry_name,
            max_bytes=CHATGPT_IMPORT_MAX_JSON_ENTRY_BYTES,
            oversize_detail=f"{_entry_display_name(entry_name)} exceeds the maximum allowed size.",
        )
        return json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{_entry_display_name(entry_name)} is not valid JSON.") from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{_entry_display_name(entry_name)} is not valid UTF-8 JSON.",
        ) from exc


def _safe_datetime(value, fallback: datetime | None = None) -> datetime:
    """Convert a value (datetime, numeric timestamp, or None) to a UTC datetime, using fallback if invalid."""
    if value is None:
        return fallback or datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        numeric = float(value)
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return fallback or datetime.now(timezone.utc)


def _clean_text(value) -> str:
    """Clean text by normalizing line endings, removing private-use Unicode markers, and stripping whitespace."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = PRIVATE_USE_MARKERS_RE.sub("", text)
    return text.strip()


def _extract_asset_id(value: str | None) -> str | None:
    """Extract a ChatGPT file/asset ID from a raw string value."""
    if not value:
        return None
    cleaned = str(value).strip().replace("sediment://", "")
    match = FILE_ID_RE.search(cleaned)
    if match:
        return match.group(1)
    return cleaned if cleaned.startswith(("file_", "file-")) else None


def _derive_asset_keys(path: str) -> set[str]:
    """Derive possible asset lookup keys from a zip archive file path."""
    basename = PurePosixPath(path).name
    stem = PurePosixPath(basename).stem
    keys = {stem}

    if stem.endswith("-sanitized"):
        keys.add(stem[: -len("-sanitized")])

    match = FILE_ID_RE.match(stem)
    if match:
        keys.add(match.group(1))

    return {key for key in keys if key}


def _build_archive_asset_index(archive: zipfile.ZipFile) -> dict[str, str]:
    """Build a lookup index mapping asset keys to zip archive paths."""
    index: dict[str, str] = {}

    for entry in archive.infolist():
        path = entry.filename
        name = PurePosixPath(path).name
        if entry.is_dir():
            continue
        if path.startswith("__MACOSX/") or "/__MACOSX/" in path:
            continue
        if name.startswith("._") or name == ".DS_Store":
            continue
        if name in SUPPORTED_CONVERSATION_EXPORTS or name == "shared_conversations.json":
            continue
        for key in _derive_asset_keys(path):
            index.setdefault(key, path)

    return index


def _find_export_member(archive: zipfile.ZipFile, target_name: str) -> str | None:
    """Find a zip archive member by filename, supporting nested paths."""
    for path in archive.namelist():
        if path.endswith(f"/{target_name}") or path == target_name:
            return path
    return None


def _load_conversations(archive: zipfile.ZipFile) -> list[dict]:
    """Load and deduplicate conversation entries from a ChatGPT export archive."""
    conversations_path = _find_export_member(archive, "conversations.json")
    if not conversations_path:
        raise HTTPException(
            status_code=400,
            detail="This archive does not look like a ChatGPT export. Missing conversations.json.",
        )

    try:
        conversations = _read_json_archive_entry(archive, conversations_path)
    except HTTPException as exc:
        if exc.status_code == 413:
            raise
        raise HTTPException(status_code=400, detail="conversations.json is not valid JSON.") from exc

    if not isinstance(conversations, list):
        raise HTTPException(status_code=400, detail="conversations.json must contain an array of conversations.")

    group_path = _find_export_member(archive, "group_chats.json")
    if group_path:
        try:
            group_payload = _read_json_archive_entry(archive, group_path)
        except HTTPException as exc:
            if exc.status_code == 413:
                raise
            group_payload = None
        chats = group_payload.get("chats") if isinstance(group_payload, dict) else None
        if isinstance(chats, list):
            conversations.extend(item for item in chats if isinstance(item, dict))

    deduped: list[dict] = []
    seen_ids: set[str] = set()
    for conversation in conversations:
        if not isinstance(conversation, dict):
            continue
        conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or "").strip()
        if conversation_id and conversation_id in seen_ids:
            continue
        if conversation_id:
            seen_ids.add(conversation_id)
        deduped.append(conversation)

    return deduped


def _build_selected_branch(mapping: dict, current_node: str | None) -> list[dict]:
    """Build an ordered list of message nodes from a conversation mapping, following the selected branch."""
    if not isinstance(mapping, dict) or not mapping:
        return []

    if current_node and current_node in mapping:
        ordered: list[dict] = []
        visited: set[str] = set()
        cursor = current_node
        while cursor and cursor not in visited:
            visited.add(cursor)
            node = mapping.get(cursor)
            if not isinstance(node, dict):
                break
            ordered.append(node)
            cursor = node.get("parent")
        return list(reversed(ordered))

    fallback_nodes = []
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        fallback_nodes.append(node)

    return sorted(
        fallback_nodes,
        key=lambda node: (
            (node.get("message") or {}).get("create_time") or 0,
            node.get("id") or "",
        ),
    )


def _extract_text_parts(content: dict) -> list[str]:
    """Extract text parts from a ChatGPT message content dict based on its content_type."""
    content_type = str(content.get("content_type") or "").strip().lower()
    parts: list[str] = []

    if content_type == "text":
        raw_parts = content.get("parts")
        if isinstance(raw_parts, list):
            for item in raw_parts:
                cleaned = _clean_text(item)
                if cleaned:
                    parts.append(cleaned)
        return parts

    if content_type == "multimodal_text":
        raw_parts = content.get("parts")
        if isinstance(raw_parts, list):
            for item in raw_parts:
                if isinstance(item, str):
                    cleaned = _clean_text(item)
                    if cleaned:
                        parts.append(cleaned)
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("content_type") or "").strip().lower()
                if item_type == "audio_transcription":
                    cleaned = _clean_text(item.get("text"))
                    if cleaned:
                        parts.append(cleaned)
                    continue
                if isinstance(item.get("text"), str):
                    cleaned = _clean_text(item.get("text"))
                    if cleaned:
                        parts.append(cleaned)
        return parts

    if content_type == "code":
        code_text = _clean_text(content.get("text"))
        if code_text:
            parts.append(code_text)
        return parts

    if content_type == "reasoning_recap":
        recap = _clean_text(content.get("content"))
        if recap:
            parts.append(recap)
        return parts

    if content_type in TOOL_RESULT_TYPES:
        for key in ("text", "summary", "snippet", "title"):
            cleaned = _clean_text(content.get(key))
            if cleaned:
                parts.append(cleaned)
                if key in {"text", "summary", "snippet"}:
                    break
        return parts

    if content_type == "thoughts":
        thoughts = content.get("thoughts")
        if isinstance(thoughts, list):
            for item in thoughts:
                if isinstance(item, dict):
                    cleaned = _clean_text(item.get("summary") or item.get("content") or item.get("text"))
                else:
                    cleaned = _clean_text(item)
                if cleaned:
                    parts.append(cleaned)
        return parts

    for key in ("text", "content"):
        cleaned = _clean_text(content.get(key))
        if cleaned:
            parts.append(cleaned)
            break

    return parts


def _parse_tool_call(code_text: str) -> tuple[str | None, str | None]:
    """Parse a tool call expression from code text, returning (tool_name, tool_args)."""
    match = TOOL_CALL_RE.match(code_text or "")
    if not match:
        return None, None
    tool_name = match.group(1)
    tool_args = _clean_text(match.group(2))
    return tool_name, tool_args


def _build_tool_result_meta(message: dict, content: dict) -> dict | None:
    """Build metadata dict for a tool result message, including citations."""
    meta: dict = {}
    message_meta = message.get("metadata")
    if isinstance(message_meta, dict):
        command = _clean_text(message_meta.get("command"))
        if command:
            meta["tool_name"] = command
        args = message_meta.get("args")
        if args is not None:
            meta["tool_args"] = args

    citations = []
    url = content.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        citations.append(
            {
                "url": url,
                "title": _clean_text(content.get("title")) or url,
                "domain": _clean_text(content.get("domain")) or None,
                "snippet": _clean_text(content.get("snippet") or content.get("summary") or content.get("text")),
            }
        )

    if citations:
        meta["citations"] = citations

    return meta or None


def _guess_mime_type(path: str, asset_meta: dict | None) -> str:
    """Guess the MIME type of a file from its path and optional metadata."""
    if isinstance(asset_meta, dict):
        for key in ("mime_type", "file_type"):
            mime_type = str(asset_meta.get(key) or "").strip()
            if mime_type:
                return mime_type

    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _normalize_original_filename(path: str, asset_meta: dict | None) -> str:
    """Determine the original filename from metadata or archive path."""
    if isinstance(asset_meta, dict):
        for key in ("original_filename", "original_name", "name", "title"):
            value = str(asset_meta.get(key) or "").strip()
            if value:
                return Path(value).name
    return PurePosixPath(path).name


def _build_attachment_stub(file_record: Files) -> dict:
    """Build an attachment dict stub from a Files database record."""
    original_filename = None
    if isinstance(file_record.meta, dict):
        original_filename = file_record.meta.get("original_filename")

    return {
        "id": file_record.id,
        "file_id": file_record.id,
        "file_name": file_record.file_name,
        "original_filename": original_filename or file_record.file_name,
        "original_name": original_filename or file_record.file_name,
        "mime_type": file_record.file_type,
        "file_type": file_record.file_type,
        "file_size": file_record.file_size,
    }


def _attachment_field_for_mime_type(mime_type: str | None) -> str:
    """Determine the attachment field name (images, audios, videos, documents) for a MIME type."""
    mime_type = str(mime_type or "").split(";", 1)[0].strip().lower()
    # SVG is extracted as XML text during generation, so keep imported SVGs in
    # the document collection even though their MIME type starts with image/.
    if mime_type == "image/svg+xml":
        return "documents"
    if mime_type.startswith("image/"):
        return "images"
    if mime_type.startswith("audio/"):
        return "audios"
    if mime_type.startswith("video/"):
        return "videos"
    return "documents"


def _collect_message_assets(message: dict) -> dict[str, dict]:
    """Collect all referenced file/asset IDs from a ChatGPT message."""
    assets: dict[str, dict] = {}
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        attachments = metadata.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                asset_id = _extract_asset_id(attachment.get("id"))
                if asset_id:
                    assets[asset_id] = {**attachment}

    def register_asset(asset_id: str | None, extra: dict | None = None):
        if not asset_id:
            return
        payload = assets.setdefault(asset_id, {})
        if extra:
            for key, value in extra.items():
                if value is not None and key not in payload:
                    payload[key] = value

    def walk(value):
        if isinstance(value, dict):
            item_type = str(value.get("content_type") or "").strip().lower()
            if item_type == "image_asset_pointer":
                register_asset(
                    _extract_asset_id(value.get("asset_pointer")),
                    {
                        "mime_type": "image/png",
                        "size": value.get("size_bytes"),
                        "width": value.get("width"),
                        "height": value.get("height"),
                    },
                )
            elif item_type == "audio_asset_pointer":
                audio_format = str(value.get("format") or "").strip().lower()
                register_asset(
                    _extract_asset_id(value.get("asset_pointer")),
                    {
                        "mime_type": f"audio/{audio_format}" if audio_format else "audio/wav",
                        "size": value.get("size_bytes"),
                    },
                )
            elif item_type == "real_time_user_audio_video_asset_pointer":
                walk(value.get("audio_asset_pointer"))

            if "asset_pointer" in value:
                register_asset(_extract_asset_id(value.get("asset_pointer")), None)
            if "url" in value:
                register_asset(_extract_asset_id(value.get("url")), None)

            for nested in value.values():
                walk(nested)
            return

        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(message.get("content") or {})
    return assets


def _import_archive_asset(
    db: Session,
    *,
    archive: zipfile.ZipFile,
    archive_path: str,
    user_id: str,
    conversation_id: str,
    asset_id: str,
    asset_meta: dict | None,
    imported_file_cache: dict[str, Files],
    max_files_limit: int,
    max_user_storage_limit_bytes: int | None,
    max_upload_bytes: int,
    max_upload_mb: int,
) -> tuple[Files | None, bool]:
    """Import a single asset from the archive into storage and the Files table. Return (file_record, is_new)."""
    if asset_id in imported_file_cache:
        return imported_file_cache[asset_id], False

    try:
        asset_info = archive.getinfo(archive_path)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Missing archive asset: {_entry_display_name(archive_path)}",
        ) from exc

    if asset_info.file_size > CHATGPT_IMPORT_MAX_ASSET_BYTES:
        raise _too_large(
            f"ChatGPT import asset '{_entry_display_name(archive_path)}' exceeds the maximum allowed size."
        )
    ensure_upload_file_size_limits(
        asset_info.file_size,
        max_upload_bytes=max_upload_bytes,
        max_upload_mb=max_upload_mb,
        global_limit_detail=(
            f"ChatGPT import asset '{_entry_display_name(archive_path)}' "
            "exceeds the maximum allowed size."
        ),
    )

    original_filename = _normalize_original_filename(archive_path, asset_meta)
    file_uuid = str(uuid.uuid4())
    suffix = Path(original_filename).suffix or Path(archive_path).suffix
    stored_name = f"{file_uuid}{suffix}" if suffix else file_uuid
    fallback_mime, _ = mimetypes.guess_type(original_filename or archive_path)
    if not fallback_mime:
        fallback_mime, _ = mimetypes.guess_type(archive_path)

    temp_file_path = None
    try:
        hasher = hashlib.sha256()
        with archive.open(archive_path) as source:
            with tempfile.NamedTemporaryFile(prefix="chatgpt-import-", suffix=suffix or ".bin", delete=False) as handle:
                temp_file_path = handle.name
                bytes_written = 0
                while True:
                    chunk = source.read(CHATGPT_IMPORT_IO_CHUNK_SIZE)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > CHATGPT_IMPORT_MAX_ASSET_BYTES:
                        raise _too_large(
                            f"ChatGPT import asset '{_entry_display_name(archive_path)}' "
                            "exceeds the maximum allowed size."
                        )
                    ensure_upload_file_size_limits(
                        bytes_written,
                        max_upload_bytes=max_upload_bytes,
                        max_upload_mb=max_upload_mb,
                        global_limit_detail=(
                            f"ChatGPT import asset '{_entry_display_name(archive_path)}' "
                            "exceeds the maximum allowed size."
                        ),
                    )
                    hasher.update(chunk)
                    handle.write(chunk)

        if not temp_file_path or not os.path.exists(temp_file_path):
            return None, False

        file_size = os.path.getsize(temp_file_path)
        ensure_upload_file_size_limits(
            file_size,
            max_upload_bytes=max_upload_bytes,
            max_upload_mb=max_upload_mb,
            global_limit_detail=(
                f"ChatGPT import asset '{_entry_display_name(archive_path)}' "
                "exceeds the maximum allowed size."
            ),
        )
        temp_path = Path(temp_file_path)
        mime_type = _detect_mime_from_content(temp_path, fallback=fallback_mime)
        if not validate_file_type(mime_type):
            raise HTTPException(
                status_code=400,
                detail=f"ChatGPT import asset '{_entry_display_name(archive_path)}' has an unsupported file type.",
            )
        if not _upload_is_valid_active_content(mime_type, temp_path):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"ChatGPT import asset '{_entry_display_name(archive_path)}' "
                    "contains disallowed active content."
                ),
            )

        content_sha256 = hasher.hexdigest()
        duplicate_record = _find_duplicate_file(
            db,
            user_id,
            original_filename,
            mime_type,
            file_size,
            content_sha256,
            project_id=None,
            folder_id=None,
        )
        if duplicate_record:
            imported_file_cache[asset_id] = duplicate_record
            return duplicate_record, False

        provider = None
        storage_key = None
        try:
            with serialized_user_file_quota_admission(db, user_id):
                ensure_user_file_upload_capacity(
                    db,
                    user_id,
                    file_size,
                    max_files_limit=max_files_limit,
                    max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                )

                provider, storage_key, upload_meta = upload_file_to_storage(Path(temp_file_path), user_id, stored_name)
                now = datetime.now(timezone.utc)
                file_record = Files(
                    id=file_uuid,
                    user_id=user_id,
                    file_name=stored_name,
                    storage_provider=provider,
                    storage_key=storage_key,
                    storage_meta=upload_meta,
                    file_category=get_file_category(mime_type),
                    file_type=mime_type,
                    file_size=file_size,
                    project_id=None,
                    folder_id=None,
                    share=None,
                    share_id=None,
                    meta={
                        "origin": "chatgpt_import",
                        "imported_from": "chatgpt",
                        "chatgpt_asset_id": asset_id,
                        "chatgpt_conversation_id": conversation_id,
                        "original_filename": original_filename,
                        "sha256": content_sha256,
                    },
                    created_at=now,
                    last_updated_at=now,
                )
                db.add(file_record)
                db.flush()
                imported_file_cache[asset_id] = file_record
                return file_record, True
        except Exception:
            if provider and storage_key:
                try:
                    delete_storage_reference(
                        storage_provider=provider,
                        storage_key=storage_key,
                        user_id=user_id,
                        file_name=stored_name,
                    )
                except Exception:
                    logger.warning(
                        "Failed to clean up ChatGPT import asset storage after DB failure",
                        exc_info=True,
                    )
            raise
    finally:
        if temp_file_path:
            try:
                os.unlink(temp_file_path)
            except FileNotFoundError:
                pass


def _build_message_blocks(
    *,
    raw_role: str,
    content: dict,
    attachments_by_field: dict[str, list[dict]],
    message_meta: dict | None,
) -> tuple[str, list[dict]]:
    """Convert a ChatGPT message into internal content blocks format. Return (role, blocks)."""
    content_type = str(content.get("content_type") or "").strip().lower()
    role = raw_role
    blocks: list[dict] = []

    def with_attachments(block: dict):
        for field, items in attachments_by_field.items():
            if items:
                block[field] = items
        return block

    if raw_role == "tool" or content_type in TOOL_RESULT_TYPES:
        tool_text = "\n\n".join(_extract_text_parts(content))
        block = {"type": "tool_call_result", "content": tool_text}
        meta = _build_tool_result_meta({"metadata": message_meta or {}}, content)
        if meta:
            block["meta"] = meta
        blocks.append(with_attachments(block))
        return "assistant", blocks

    if raw_role == "assistant" and content_type == "code":
        code_text = "\n\n".join(_extract_text_parts(content))
        tool_name, tool_args = _parse_tool_call(code_text)
        if tool_name:
            block = build_tool_call_block(tool_name, tool_args or "{}")
            blocks.append(with_attachments(block))
            return role, blocks
        blocks.append(with_attachments({"type": "content", "content": code_text}))
        return role, blocks

    if content_type in {"reasoning_recap", "thoughts"}:
        reasoning_text = "\n\n".join(_extract_text_parts(content))
        if reasoning_text:
            blocks.append({"type": "reasoning", "content": reasoning_text})
        if attachments_by_field:
            block_type = "user" if raw_role == "user" else "content"
            blocks.append(with_attachments({"type": block_type, "content": ""}))
        return role, blocks

    text_parts = _extract_text_parts(content)
    joined_text = "\n\n".join(part for part in text_parts if part)
    block_type = "user" if raw_role == "user" else "content"
    if joined_text or any(attachments_by_field.values()):
        blocks.append(with_attachments({"type": block_type, "content": joined_text}))

    return role, blocks


def _load_existing_imported_conversation_ids(db: Session, user_id: str) -> set[str]:
    """Load the set of ChatGPT conversation IDs already imported for a user."""
    existing: set[str] = set()
    chat_rows = db.query(Chats).filter(Chats.user_id == user_id).all()
    for chat in chat_rows:
        meta = _meta_to_dict(getattr(chat, "meta", None))
        conversation_id = str(meta.get("chatgpt_conversation_id") or "").strip()
        if conversation_id and meta.get("imported_from") == "chatgpt":
            existing.add(conversation_id)
    return existing


def import_chatgpt_export_archive(
    db: Session,
    user_id: str,
    archive_file: BinaryIO,
    *,
    archive_name: str | None = None,
) -> dict:
    """Import a full ChatGPT export zip archive, creating chats, messages, and file assets."""
    max_upload_bytes, max_upload_mb = resolve_user_max_upload_size_bytes(db, user_id)
    _validate_upload_size(
        archive_file,
        max_upload_bytes=max_upload_bytes,
        max_upload_mb=max_upload_mb,
    )
    if hasattr(archive_file, "seek"):
        archive_file.seek(0)

    try:
        archive = zipfile.ZipFile(archive_file)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid zip archive.") from exc

    with archive:
        _validate_archive_limits(archive)
        conversations = _load_conversations(archive)
        asset_index = _build_archive_asset_index(archive)
        existing_conversation_ids = _load_existing_imported_conversation_ids(db, user_id)
        imported_file_cache: dict[str, Files] = {}
        upload_limits: tuple[int, int | None, int, int] | None = None

        summary = {
            "imported_chats": 0,
            "imported_messages": 0,
            "imported_files": 0,
            "skipped_chats": 0,
            "skipped_duplicates": 0,
            "shared_index_entries": 0,
        }

        shared_path = _find_export_member(archive, "shared_conversations.json")
        if shared_path:
            try:
                shared_payload = _read_json_archive_entry(archive, shared_path)
                if isinstance(shared_payload, list):
                    summary["shared_index_entries"] = len(shared_payload)
            except HTTPException as exc:
                if exc.status_code == 413:
                    raise
                summary["shared_index_entries"] = 0

        for conversation in conversations:
            conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or "").strip()
            if conversation_id and conversation_id in existing_conversation_ids:
                summary["skipped_chats"] += 1
                summary["skipped_duplicates"] += 1
                continue

            created_storage_refs: list[tuple[str, str, str, str]] = []
            created_asset_ids: list[str] = []
            try:
                nodes = _build_selected_branch(
                    conversation.get("mapping") if isinstance(conversation.get("mapping"), dict) else {},
                    str(conversation.get("current_node") or "").strip() or None,
                )
                if not nodes:
                    summary["skipped_chats"] += 1
                    continue

                created_at = _safe_datetime(conversation.get("create_time"))
                updated_at = _safe_datetime(conversation.get("update_time"), created_at)
                title = _clean_text(conversation.get("title")) or "Untitled"

                chat = Chats(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    title=title,
                    project_id=None,
                    share=None,
                    share_id=None,
                    archived=bool(conversation.get("is_archived")),
                    pinned_position=None,
                    meta={
                        "status": "normal",
                        "imported_from": "chatgpt",
                        "chatgpt_conversation_id": conversation_id or None,
                        "chatgpt_default_model_slug": conversation.get("default_model_slug"),
                        "chatgpt_is_starred": bool(conversation.get("is_starred")),
                        "chatgpt_is_read_only": bool(conversation.get("is_read_only")),
                        "chatgpt_pinned_time": conversation.get("pinned_time"),
                        "chatgpt_archive_name": archive_name,
                    },
                    created_at=created_at,
                    last_updated_at=updated_at,
                )
                db.add(chat)

                last_user_message_id: str | None = None
                imported_message_count = 0

                for node in nodes:
                    message = node.get("message")
                    if not isinstance(message, dict):
                        continue
                    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
                    if metadata.get("is_visually_hidden_from_conversation"):
                        continue

                    author = message.get("author") if isinstance(message.get("author"), dict) else {}
                    raw_role = str(author.get("role") or "").strip().lower()
                    if raw_role == "system":
                        continue
                    if raw_role not in {"user", "assistant", "tool"}:
                        raw_role = "assistant"

                    content = message.get("content") if isinstance(message.get("content"), dict) else {}
                    asset_refs = _collect_message_assets(message)
                    attachments_by_field: dict[str, list[dict]] = defaultdict(list)

                    for asset_id, asset_meta in asset_refs.items():
                        archive_path = asset_index.get(asset_id)
                        if not archive_path:
                            logger.info("ChatGPT import missing asset %s in archive %s", asset_id, archive_name)
                            continue
                        if upload_limits is None:
                            max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(
                                db,
                                user_id,
                            )
                            upload_limits = (
                                max_files_limit,
                                max_user_storage_limit_bytes,
                                max_upload_bytes,
                                max_upload_mb,
                            )
                        (
                            max_files_limit,
                            max_user_storage_limit_bytes,
                            asset_max_upload_bytes,
                            asset_max_upload_mb,
                        ) = upload_limits
                        file_record, is_new = _import_archive_asset(
                            db,
                            archive=archive,
                            archive_path=archive_path,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            asset_id=asset_id,
                            asset_meta=asset_meta,
                            imported_file_cache=imported_file_cache,
                            max_files_limit=max_files_limit,
                            max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                            max_upload_bytes=asset_max_upload_bytes,
                            max_upload_mb=asset_max_upload_mb,
                        )
                        if not file_record:
                            continue
                        if is_new:
                            created_asset_ids.append(asset_id)
                            created_storage_refs.append(
                                (
                                    file_record.storage_provider,
                                    file_record.storage_key,
                                    file_record.user_id,
                                    file_record.file_name,
                                )
                            )
                            summary["imported_files"] += 1
                        attachments_by_field[_attachment_field_for_mime_type(file_record.file_type)].append(
                            _build_attachment_stub(file_record)
                        )

                    role, blocks = _build_message_blocks(
                        raw_role=raw_role,
                        content=content,
                        attachments_by_field=attachments_by_field,
                        message_meta=metadata,
                    )
                    if not blocks:
                        continue

                    model_id = str(
                        metadata.get("model_slug")
                        or conversation.get("default_model_slug")
                        or "chatgpt-import"
                    ).strip() or "chatgpt-import"

                    created_message_at = _safe_datetime(message.get("create_time"), created_at)
                    chat_message = ChatMessages(
                        id=str(uuid.uuid4()),
                        chat_id=chat.id,
                        model_id=model_id,
                        role=role,
                        reference_id=last_user_message_id if role == "assistant" else None,
                        generation={"generation_number": 1},
                        retry_count=0,
                        thinking=None,
                        content=json.dumps(blocks, ensure_ascii=False),
                        created_at=created_message_at,
                    )
                    db.add(chat_message)
                    if role == "user":
                        last_user_message_id = chat_message.id
                    imported_message_count += 1
                    if created_message_at > chat.last_updated_at:
                        chat.last_updated_at = created_message_at

                if imported_message_count <= 0:
                    db.rollback()
                    summary["imported_files"] = max(0, summary["imported_files"] - len(created_asset_ids))
                    for asset_id in created_asset_ids:
                        imported_file_cache.pop(asset_id, None)
                    for storage_provider, storage_key, storage_user_id, file_name in created_storage_refs:
                        delete_storage_reference(
                            storage_provider=storage_provider,
                            storage_key=storage_key,
                            user_id=storage_user_id,
                            file_name=file_name,
                        )
                    summary["skipped_chats"] += 1
                    continue

                db.commit()
                if conversation_id:
                    existing_conversation_ids.add(conversation_id)
                summary["imported_chats"] += 1
                summary["imported_messages"] += imported_message_count
            except HTTPException:
                db.rollback()
                summary["imported_files"] = max(0, summary["imported_files"] - len(created_asset_ids))
                for asset_id in created_asset_ids:
                    imported_file_cache.pop(asset_id, None)
                for storage_provider, storage_key, storage_user_id, file_name in created_storage_refs:
                    delete_storage_reference(
                        storage_provider=storage_provider,
                        storage_key=storage_key,
                        user_id=storage_user_id,
                        file_name=file_name,
                    )
                raise
            except Exception:
                logger.exception(
                    "Failed to import ChatGPT conversation %s from archive %s",
                    conversation_id or "<unknown>",
                    archive_name or "<upload>",
                )
                db.rollback()
                summary["imported_files"] = max(0, summary["imported_files"] - len(created_asset_ids))
                for asset_id in created_asset_ids:
                    imported_file_cache.pop(asset_id, None)
                for storage_provider, storage_key, storage_user_id, file_name in created_storage_refs:
                    delete_storage_reference(
                        storage_provider=storage_provider,
                        storage_key=storage_key,
                        user_id=storage_user_id,
                        file_name=file_name,
                    )
                summary["skipped_chats"] += 1

        return summary
