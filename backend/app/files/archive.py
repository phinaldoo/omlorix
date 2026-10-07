"""Portable Canvas revisions and explicit access in the existing file archives."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.files.history import materialize_canvas_history
from app.files.models import CanvasHistory, FileMember
from app.users.models import User, build_user_email_match


def history_entries(db, record):
    rows = (
        db.query(CanvasHistory)
        .filter(CanvasHistory.file_id == record.id)
        .order_by(CanvasHistory.revision)
        .yield_per(100)
    )
    for row in rows:
        yield (
            row,
            {
                "revision": row.revision,
                "original_filename": row.file_name,
                "file_type": row.file_type,
                "content_type": row.content_type,
                "file_size": row.file_size,
                "edit_source": row.edit_source,
                "created_at": row.created_at.isoformat(),
            },
        )


def export_members(db, file_id):
    rows = (
        db.query(FileMember, User.email)
        .join(User, User.id == FileMember.user_id)
        .filter(FileMember.file_id == file_id)
        .yield_per(100)
    )
    return [
        {
            "email": email,
            "role": member.role,
            "granted_at": member.granted_at.isoformat(),
        }
        for member, email in rows
    ]


def stream_history_json(db, record):
    from app.users.data_export import _iter_file_content_base64_chunks

    yield "["
    first = True
    for version, entry in history_entries(db, record):
        if not first:
            yield ","
        first = False
        yield json.dumps(entry, ensure_ascii=False)[:-1] + ',"content_base64":"'
        yield from _iter_file_content_base64_chunks(materialize_canvas_history(version))
        yield '"}'
    yield "]"


def export_history_zip(db, record, archive):
    entries = []
    for version, entry in history_entries(db, record):
        name = f"files/history/{record.id}/{version.id}{Path(version.file_name).suffix}"
        archive.write(materialize_canvas_history(version), name)
        entries.append({**entry, "archive_name": name})
    return entries


def restore_file_history(db, record, entries, loader, cleanup_references):
    from fastapi import HTTPException
    from app.admin.user_exports.files.models import (
        MAX_IMPORT_TOTAL_SIZE,
        _parse_iso_datetime,
    )
    from app.files.utils import (
        ensure_user_file_upload_capacity,
        resolve_user_file_upload_limits,
        overwrite_existing_file_bytes,
    )

    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="Invalid Canvas history")
    total = 0
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=400, detail="Invalid Canvas history")
        revision = entry.get("revision")
        content_type = entry.get("content_type") or "markdown"
        if type(revision) is not int or content_type not in (
            "markdown",
            "mermaid",
            "csv",
            "html",
            "latex",
            "spreadsheet",
        ):
            raise HTTPException(status_code=400, detail="Invalid Canvas revision")
        if (
            revision < 0
            or revision in seen
            or revision >= int((record.meta or {}).get("canvas_revision") or 0)
        ):
            raise HTTPException(status_code=400, detail="Invalid Canvas revision")
        seen.add(revision)
        content, name, mime, _ = loader(entry)
        total += len(content)
        if total > MAX_IMPORT_TOTAL_SIZE:
            raise HTTPException(
                status_code=400, detail="Canvas history exceeds the import size limit"
            )
        max_files, max_storage = resolve_user_file_upload_limits(db, record.user_id)
        ensure_user_file_upload_capacity(
            db,
            record.user_id,
            record.file_size + len(content),
            max_files_limit=max_files,
            max_user_storage_limit_bytes=max_storage,
            existing_file_id=record.id,
        )
        version_id = str(uuid.uuid4())
        stored_name = f"{version_id}{Path(name).suffix}"
        provider, key, meta = overwrite_existing_file_bytes(
            user_id=record.user_id,
            file_name=stored_name,
            file_id=version_id,
            file_bytes=content,
        )
        cleanup_references.append((provider, key, stored_name))
        db.add(
            CanvasHistory(
                id=version_id,
                file_id=record.id,
                owner_id=record.user_id,
                revision=revision,
                actor_id=None,
                edit_source=entry.get("edit_source")
                if entry.get("edit_source") in {"user", "assistant", "restore"}
                else "user",
                file_name=name,
                file_type=mime,
                content_type=content_type,
                file_size=len(content),
                storage_provider=provider,
                storage_key=key,
                storage_meta=meta,
                created_at=_parse_iso_datetime(entry.get("created_at"))
                or datetime.now(timezone.utc),
            )
        )
        db.flush()


def restore_file_members(db, record, entries):
    from app.admin.user_exports.files.models import _parse_iso_datetime
    from app.files.sharing import ensure_artifact_file_sharing_allowed_for_user

    if not entries:
        return
    ensure_artifact_file_sharing_allowed_for_user(record.user_id, db)
    for entry in entries:
        role = entry.get("role")
        if role not in {"viewer", "editor"}:
            continue
        target = (
            db.query(User)
            .filter(
                build_user_email_match(entry.get("email")), User.is_active.is_(True)
            )
            .first()
        )
        if target and target.id != record.user_id:
            db.merge(
                FileMember(
                    file_id=record.id,
                    user_id=target.id,
                    role=role,
                    granted_at=_parse_iso_datetime(entry.get("granted_at"))
                    or datetime.now(timezone.utc),
                )
            )
