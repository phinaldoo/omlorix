from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

from sqlalchemy import inspect, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified


logger = logging.getLogger(__name__)


ATTACHMENT_FIELDS = ("images", "videos", "audios", "documents")
FILE_ID_LIST_KEYS = {
    "file_ids",
    "input_file_ids",
    "group_context_file_ids",
}
FILE_ID_SCALAR_KEYS = {
    "file_id",
    "existing_file_id",
    "source_file_id",
}
FILE_METADATA_KEYS = {
    "file_name",
    "original_name",
    "original_filename",
    "source_file_name",
    "source_file_size",
    "source_file_category",
    "file_type",
    "mime_type",
    "file_size",
    "preview_url",
    "resolved_media_type",
}


class _RemoveValue:
    pass


REMOVE_VALUE = _RemoveValue()


def _matches_file_id(value: Any, file_id: str) -> bool:
    return str(value or "").strip() == file_id


def _item_file_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("file_id") or value.get("source_file_id") or "").strip()
    return str(value or "").strip()


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _remove_file_from_list(values: list[Any], file_id: str) -> tuple[list[Any], bool]:
    changed = False
    cleaned: list[Any] = []
    for value in values:
        if _item_file_id(value) == file_id:
            changed = True
            continue
        cleaned_value, value_changed = _scrub_json_value(value, file_id)
        changed = changed or value_changed
        if cleaned_value is REMOVE_VALUE:
            changed = True
            continue
        cleaned.append(cleaned_value)
    return cleaned, changed


def _remove_file_from_attachment_value(value: Any, file_id: str) -> tuple[Any, bool]:
    parsed = _parse_jsonish(value)
    if isinstance(parsed, list):
        cleaned, changed = _remove_file_from_list(parsed, file_id)
        if not isinstance(value, str):
            return cleaned, changed
        return _dump_json(cleaned), changed
    if isinstance(value, list):
        return _remove_file_from_list(value, file_id)
    if isinstance(value, dict):
        if _item_file_id(value) == file_id:
            return REMOVE_VALUE, True
        return _scrub_json_value(value, file_id)
    if _matches_file_id(value, file_id):
        return REMOVE_VALUE, True
    return value, False


def _scrub_json_value(value: Any, file_id: str) -> tuple[Any, bool]:
    if isinstance(value, list):
        return _remove_file_from_list(value, file_id)

    if not isinstance(value, dict):
        return value, False

    changed = False
    cleaned: dict[str, Any] = {}
    remove_metadata = any(_matches_file_id(value.get(key), file_id) for key in FILE_ID_SCALAR_KEYS)

    for key, raw_child in value.items():
        if key in ATTACHMENT_FIELDS:
            child, child_changed = _remove_file_from_attachment_value(raw_child, file_id)
            changed = changed or child_changed
            if child is REMOVE_VALUE or child in (None, [], ""):
                if child_changed:
                    continue
            cleaned[key] = child
            continue

        if key in FILE_ID_LIST_KEYS:
            parsed = _parse_jsonish(raw_child)
            if isinstance(parsed, list):
                child, child_changed = _remove_file_from_list(parsed, file_id)
                changed = changed or child_changed
                if isinstance(raw_child, str):
                    child = _dump_json(child)
                cleaned[key] = child
                continue

        if key in FILE_ID_SCALAR_KEYS and _matches_file_id(raw_child, file_id):
            changed = True
            continue

        if remove_metadata and key in FILE_METADATA_KEYS:
            changed = True
            continue

        child, child_changed = _scrub_json_value(raw_child, file_id)
        changed = changed or child_changed
        if child is REMOVE_VALUE:
            changed = True
            continue
        cleaned[key] = child

    return cleaned, changed


def _scrub_json_string(raw_value: Any, file_id: str) -> tuple[Any, bool]:
    parsed = _parse_jsonish(raw_value)
    if parsed is None:
        if _matches_file_id(raw_value, file_id):
            return None, True
        return raw_value, False
    cleaned, changed = _scrub_json_value(parsed, file_id)
    if not changed:
        return raw_value, False
    if isinstance(raw_value, str):
        return _dump_json(cleaned), True
    return cleaned, True


def _scrub_chat_message_content(db: Session, file_id: str) -> int:
    from app.chats.models import ChatMessages

    rows = db.query(ChatMessages).all()
    updated = 0
    for row in rows:
        cleaned, changed = _scrub_json_string(getattr(row, "content", None), file_id)
        if changed:
            row.content = cleaned if isinstance(cleaned, str) else _dump_json(cleaned)
            updated += 1
    return updated


def _scrub_group_context_settings(db: Session, file_id: str) -> int:
    from app.groups.models import Group

    groups = db.query(Group).all()
    updated = 0
    for group in groups:
        settings = group.settings if isinstance(group.settings, dict) else {}
        cleaned, changed = _scrub_json_value(settings, file_id)
        if changed and isinstance(cleaned, dict):
            group.settings = cleaned
            flag_modified(group, "settings")
            updated += 1
    return updated


def _scrub_project_attachment_fields(db: Session, file_id: str) -> int:
    from app.projects.models import Project

    projects = db.query(Project).all()
    updated = 0
    for project in projects:
        project_changed = False
        for field in ATTACHMENT_FIELDS:
            current = getattr(project, field, None)
            cleaned, changed = _remove_file_from_attachment_value(current, file_id)
            if changed:
                setattr(project, field, None if cleaned is REMOVE_VALUE else cleaned)
                project_changed = True
        if project_changed:
            project.last_updated_at = datetime.now(timezone.utc)
            updated += 1
    return updated


def _scrub_slide_presentation_file_ids(db: Session, user_id: str, file_id: str) -> int:
    try:
        from app.files.models import Files
        from app.files.utils import delete_storage_reference
        from app.tools.slide_presentation.models import SlidePresentations
        from app.tools.slide_presentation.storage import (
            build_presentation_storage_prefix,
            delete_slide_presentation_artifacts,
        )
    except Exception:
        return 0

    pptx_rows = (
        db.query(SlidePresentations)
        .filter(SlidePresentations.user_id == user_id, SlidePresentations.file_id == file_id)
        .all()
    )
    source_rows = (
        db.query(SlidePresentations)
        .filter(SlidePresentations.user_id == user_id, SlidePresentations.id == file_id)
        .all()
    )
    now = datetime.now(timezone.utc)
    updated = 0
    files_table_available = inspect(db.connection()).has_table(
        Files.__tablename__, schema=Files.metadata.schema
    )
    for row in pptx_rows:
        row.file_id = None
        row.last_updated_at = now
        source = (
            db.query(Files)
            .filter(Files.id == row.id, Files.user_id == user_id)
            .first()
        ) if files_table_available else None
        if source is not None:
            meta = dict(source.meta) if isinstance(source.meta, dict) else {}
            meta.pop("presentation_pptx_file_id", None)
            meta["presentation_render_status"] = "stale"
            source.meta = meta
        updated += 1

    for row in source_rows:
        # Remote adapters delete exact object keys rather than recursively
        # deleting a prefix, so remove the indexed immutable revision first.
        # This guarantees that the currently published HTML and slide images
        # are deleted for every provider.
        indexed_prefix = str(row.storage_prefix or "").strip()
        if indexed_prefix:
            delete_slide_presentation_artifacts(
                storage_provider=str(row.storage_provider or "local"),
                storage_prefix=indexed_prefix,
                slide_count=int(row.slide_count or 0),
            )

        # Also remove the stable local presentation subtree. It can contain a
        # legacy bundle, disposable render inputs, or materialized cloud
        # artifacts that are not represented by the current index prefix.
        stable_prefix = build_presentation_storage_prefix(user_id, str(row.id))
        if stable_prefix != indexed_prefix:
            delete_slide_presentation_artifacts(
                storage_provider=str(row.storage_provider or "local"),
                storage_prefix=stable_prefix,
                slide_count=int(row.slide_count or 0),
            )
        derivative = (
            db.query(Files)
            .filter(Files.id == row.file_id, Files.user_id == user_id)
            .first()
            if row.file_id and files_table_available
            else None
        )
        if derivative is not None and derivative.id != file_id:
            # The rendered PPTX is owned by the source presentation. Remove
            # ordinary attachment references before deleting that derivative
            # row, just as a direct file deletion would.
            _scrub_chat_message_content(db, str(derivative.id))
            _scrub_group_context_settings(db, str(derivative.id))
            _scrub_project_attachment_fields(db, str(derivative.id))
            _scrub_deep_research_artifact_file_ids(
                db, user_id, str(derivative.id)
            )
            _scrub_note_file_references(db, user_id, str(derivative.id))
            try:
                delete_storage_reference(
                    storage_provider=str(derivative.storage_provider or "local"),
                    storage_key=str(derivative.storage_key or ""),
                    user_id=user_id,
                    file_name=str(derivative.file_name or "presentation.pptx"),
                )
            except Exception:
                # Reference cleanup is best effort for external storage. The
                # database row must still be removed so a provider outage does
                # not abort deletion of the canonical presentation file.
                logger.warning(
                    "Could not remove rendered presentation file bytes during cleanup",
                    exc_info=True,
                )
            db.delete(derivative)
        db.delete(row)
        updated += 1
    return updated


def _scrub_deep_research_artifact_file_ids(
    db: Session,
    user_id: str,
    file_id: str,
) -> int:
    """Clear source-file links while retaining copied report artifacts.

    Deep Research keeps an immutable workspace copy, so deleting the original
    user file should mirror the former ``ON DELETE SET NULL`` behavior rather
    than removing the artifact from an already generated report.
    """

    from app.tools.deep_research.models import DeepResearchRun

    updated = 0
    runs = db.query(DeepResearchRun).filter(DeepResearchRun.user_id == user_id).all()
    for run in runs:
        # Preserve malformed or legacy entries verbatim. Cleanup should only
        # clear matching dictionary fields, not silently rewrite the JSON list.
        artifacts = list(run.artifacts or [])
        changed = False
        for artifact in artifacts:
            if isinstance(artifact, dict) and _matches_file_id(
                artifact.get("file_id"), file_id
            ):
                artifact["file_id"] = None
                changed = True
        if changed:
            run.artifacts = artifacts
            flag_modified(run, "artifacts")
            updated += 1
    return updated


def _remove_target_note_references(content: str | None, *, user_id: str, file_id: str) -> tuple[str, bool]:
    from app.notes.utils import parse_note_file_references

    source = str(content or "")
    cleaned = source
    for reference in parse_note_file_references(source):
        if reference.owner_id == user_id and reference.file_id == file_id:
            cleaned = cleaned.replace(reference.raw_token, "")
    return cleaned, cleaned != source


def _scrub_note_file_references(db: Session, user_id: str, file_id: str) -> int:
    try:
        from app.notes.models import NoteHistory, Notes
    except Exception:
        return 0

    updated = 0
    now = datetime.now(timezone.utc)
    for note in db.query(Notes).filter(Notes.content.contains(file_id)).all():
        cleaned, changed = _remove_target_note_references(note.content, user_id=user_id, file_id=file_id)
        if changed:
            note.content = cleaned
            note.updated_at = now
            updated += 1

    for history in (
        db.query(NoteHistory)
        .filter(or_(NoteHistory.content.contains(file_id), NoteHistory.previous_content.contains(file_id)))
        .all()
    ):
        cleaned, changed = _remove_target_note_references(history.content, user_id=user_id, file_id=file_id)
        if changed:
            history.content = cleaned
            updated += 1
        previous_cleaned, previous_changed = _remove_target_note_references(
            history.previous_content,
            user_id=user_id,
            file_id=file_id,
        )
        if previous_changed:
            history.previous_content = previous_cleaned
            updated += 1

    return updated


def cleanup_file_references(db: Session, user_id: str, file_id: str | None) -> dict[str, int]:
    """Remove persistent references to a file before its file row is deleted."""
    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id:
        return {}

    return {
        "chat_messages": _scrub_chat_message_content(db, normalized_file_id),
        "groups": _scrub_group_context_settings(db, normalized_file_id),
        "projects": _scrub_project_attachment_fields(db, normalized_file_id),
        "slide_presentations": _scrub_slide_presentation_file_ids(db, user_id, normalized_file_id),
        "deep_research_runs": _scrub_deep_research_artifact_file_ids(
            db,
            user_id,
            normalized_file_id,
        ),
        "notes": _scrub_note_file_references(db, user_id, normalized_file_id),
    }
