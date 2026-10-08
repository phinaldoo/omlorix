"""Canvas revisions using immutable storage objects and existing ACLs."""

from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.files.access import resolve_file_for_edit
from app.files.models import CanvasHistory, FileMember, Files


def capture_canvas_history(db: Session, record: Files) -> None:
    """Stage the previous version in the same transaction as a source update."""
    from app.tools.canvas_markdown.utils import _content_type_from_file_record

    meta = dict(record.meta or {})
    revision = int(meta.get("canvas_revision") or 0)
    existing = (
        db.query(CanvasHistory.id)
        .filter(
            CanvasHistory.file_id == record.id,
            CanvasHistory.revision == revision,
        )
        .first()
    )
    if existing:
        return
    db.add(
        CanvasHistory(
            id=str(uuid.uuid4()),
            file_id=record.id,
            owner_id=record.user_id,
            revision=revision,
            actor_id=meta.get("canvas_last_edited_by") or record.user_id,
            edit_source=meta.get("canvas_last_edit_source") or "user",
            file_name=meta.get("original_filename") or record.file_name,
            file_type=record.file_type,
            content_type=meta.get("canvas_type")
            or (
                "spreadsheet"
                if Path(
                    meta.get("original_filename") or record.file_name
                ).suffix.lower()
                in {".xlsx", ".xls", ".tsv"}
                else _content_type_from_file_record(record)
            ),
            file_size=record.file_size,
            storage_provider=record.storage_provider,
            storage_key=record.storage_key,
            storage_meta=record.storage_meta,
            inline_content=record.inline_content,
            created_at=record.last_updated_at,
        )
    )
    db.flush()


def history_storage_bytes(db: Session, user_id: str) -> int:
    return int(
        db.query(func.coalesce(func.sum(CanvasHistory.file_size), 0))
        .filter(
            CanvasHistory.owner_id == str(user_id),
        )
        .scalar()
        or 0
    )


def _history_query(db: Session, user_id: str, file_id: str):
    access = resolve_file_for_edit(db, user_id, file_id)
    if not access:
        raise HTTPException(status_code=404, detail="File not found")
    query = db.query(CanvasHistory).filter(CanvasHistory.file_id == str(file_id))
    if access.storage_owner_user_id != str(user_id):
        member = db.get(FileMember, (str(file_id), str(user_id)))
        query = query.filter(CanvasHistory.created_at >= member.granted_at)
    return access, query


def list_canvas_history(
    db: Session, user_id: str, file_id: str, *, limit: int, offset: int
):
    access, query = _history_query(db, user_id, file_id)
    rows = (
        query.order_by(CanvasHistory.revision.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "revision": row.revision,
                "created_at": row.created_at,
                "edit_source": row.edit_source,
                "file_name": row.file_name,
                "file_size": row.file_size,
                "content_type": row.content_type,
            }
            for row in rows[:limit]
        ],
        "has_more": len(rows) > limit,
        "current_revision": int((access.record.meta or {}).get("canvas_revision") or 0),
    }


def get_canvas_history(db: Session, user_id: str, file_id: str, version_id: str):
    access, query = _history_query(db, user_id, file_id)
    version = query.filter(CanvasHistory.id == str(version_id)).first()
    if not version:
        raise HTTPException(status_code=404, detail="File not found")
    return access, version


def materialize_canvas_history(version: CanvasHistory) -> Path:
    from app.files.utils import materialize_file_record

    record = SimpleNamespace(
        id=version.id,
        user_id=version.owner_id,
        file_name=version.file_name,
        file_size=version.file_size,
        storage_provider=version.storage_provider,
        storage_key=version.storage_key,
        storage_meta=version.storage_meta,
        inline_content=version.inline_content,
    )
    return materialize_file_record(record, version.owner_id)


def restore_canvas_history(
    db: Session, user_id: str, file_id: str, version_id: str, expected_revision: int
):
    from app.tools.canvas_markdown.utils import (
        save_canvas_markdown,
        save_canvas_spreadsheet,
    )

    access, version = get_canvas_history(db, user_id, file_id, version_id)
    source = materialize_canvas_history(version)
    common = dict(
        db=db,
        user_id=access.storage_owner_user_id,
        file_id=file_id,
        expected_revision=expected_revision,
        edited_by=user_id,
        edit_source="restore",
        filename=version.file_name,
    )
    if version.content_type == "spreadsheet":
        return save_canvas_spreadsheet(
            **common,
            file_bytes=source.read_bytes(),
            file_format=Path(version.file_name).suffix.lstrip("."),
        )
    return save_canvas_markdown(
        **common,
        content=source.read_text(encoding="utf-8"),
        content_type=version.content_type,
        allow_html_attachment=True,
        force_canvas_asset_reconciliation=True,
    )


def stage_history_deletion(db: Session, file_id: str):
    """Remove revision rows now and return physical cleanup for after commit."""
    from functools import partial
    from app.files.utils import delete_storage_reference

    cleanups = []
    for version in (
        db.query(CanvasHistory).filter(CanvasHistory.file_id == file_id).yield_per(100)
    ):
        cleanups.append(
            partial(
                delete_storage_reference,
                storage_provider=version.storage_provider,
                storage_key=version.storage_key,
                user_id=version.owner_id,
                file_name=version.file_name,
            )
        )
    db.query(CanvasHistory).filter(CanvasHistory.file_id == file_id).delete(
        synchronize_session=False
    )
    return cleanups
