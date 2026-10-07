"""Read old Notes/Todo archives into Canvas; no retired runtime models required."""

from datetime import datetime, timezone
import json
import re
from pathlib import Path
import uuid

from app.files.models import CanvasHistory, Files


def _date(value, fallback):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return fallback


def _import_document(db, user_id, kind, entry, content, title, versions=()):
    from app.files.utils import (
        ensure_user_file_upload_capacity,
        resolve_user_file_upload_limits,
        serialized_user_file_quota_admission,
    )

    source_id = str(entry.get("id") or uuid.uuid4())
    file_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"omlorix/{kind}/{user_id}/{source_id}")
    )
    if db.get(Files, file_id):
        return file_id, False
    now = datetime.now(timezone.utc)
    created = _date(entry.get("created_at"), now)
    filename = (Path(title).name.strip()[:180] or "Canvas") + ".md"
    size = len(content.encode("utf-8"))
    history_bytes = sum(len(text.encode("utf-8")) for text, _, _ in versions)
    max_files, max_storage = resolve_user_file_upload_limits(db, user_id)
    with serialized_user_file_quota_admission(db, user_id):
        ensure_user_file_upload_capacity(
            db,
            user_id,
            size + history_bytes,
            max_files_limit=max_files,
            max_user_storage_limit_bytes=max_storage,
        )
        record = Files(
            id=file_id,
            user_id=user_id,
            file_name=filename,
            storage_provider="inline",
            storage_key=f"{user_id}/{file_id}.md",
            inline_content=content,
            file_category="document",
            file_type="text/markdown",
            file_size=size,
            created_at=created,
            last_updated_at=_date(entry.get("updated_at"), created),
            meta={
                "canvas": True,
                "canvas_type": "markdown",
                "canvas_revision": len(versions) + 1,
                "original_filename": filename,
                f"legacy_{kind}_id": source_id,
            },
        )
        db.add(record)
        db.flush()
        for revision, (text, date, actor) in enumerate(versions):
            db.add(
                CanvasHistory(
                    id=str(uuid.uuid4()),
                    file_id=file_id,
                    owner_id=user_id,
                    revision=revision,
                    actor_id=user_id,
                    edit_source=actor,
                    file_name=filename,
                    file_type="text/markdown",
                    content_type="markdown",
                    file_size=len(text.encode("utf-8")),
                    storage_provider="inline",
                    storage_key=f"{user_id}/{file_id}/{revision}.md",
                    inline_content=text,
                    created_at=_date(date, created),
                )
            )
        db.commit()
    return file_id, True


def import_user_notes(
    db, user_id, payload, *, restore_sharing_metadata=False, skip_existing_owned=False
):
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    entries = data.get("notes", []) if isinstance(data, dict) else []
    result = {"created": [], "skipped": [], "warnings": [], "errors": []}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = _canvas_links(entry.get("content"))
        title = next(
            (
                line.strip().lstrip("#").strip()
                for line in content.splitlines()
                if line.strip()
            ),
            "Canvas",
        )
        versions = []
        for row in entry.get("history", []):
            if not isinstance(row, dict):
                continue
            if not versions and row.get("previous_content") is not None:
                versions.append(
                    (
                        _canvas_links(row["previous_content"]),
                        entry.get("created_at"),
                        "user",
                    )
                )
            versions.append(
                (
                    _canvas_links(row.get("content")),
                    row.get("created_at"),
                    "assistant" if row.get("actor_type") == "assistant" else "user",
                )
            )
        file_id, created = _import_document(
            db, user_id, "note", entry, content, title, versions
        )
        result["created" if created else "skipped"].append(
            {"source_id": entry.get("id"), "file_id": file_id}
        )
    return result


def import_todo_lists(db, user_id, entries):
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "Canvas")
        lines = [f"# {title}", "", str(entry.get("description") or ""), ""]
        for task in entry.get("todos", []):
            if not isinstance(task, dict):
                continue
            lines.append(
                f"- [{'x' if task.get('is_done') else ' '}] {str(task.get('content') or '').replace(chr(10), ' ')}"
            )
            if task.get("notes"):
                lines.extend("  " + line for line in str(task["notes"]).splitlines())
            details = {
                k: v
                for k, v in task.items()
                if k not in {"id", "todo_list", "content", "notes", "is_done"}
                and v is not None
            }
            if details:
                lines.extend(
                    [
                        "",
                        "```json",
                        json.dumps(details, ensure_ascii=False, default=str, indent=2),
                        "```",
                        "",
                    ]
                )
        _import_document(db, user_id, "todo_list", entry, "\n".join(lines), title)


def _canvas_links(content):
    def replace(match):
        label = (
            (match.group(4) or match.group(3))
            .replace("\\", "\\\\")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )
        prefix = "!" if match.group(1) == "image" else ""
        return f"{prefix}[{label}](omlorix-file://{match.group(3)})"

    return re.sub(
        r"\{\{note:(image|audio|file):([^:|}]+):([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})(?:\|([^}]*?))?\}\}",
        replace,
        str(content or ""),
    )
