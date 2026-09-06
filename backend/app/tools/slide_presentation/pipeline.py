"""Presentation specialist entry point and revision-safe editor rendering."""

from __future__ import annotations

import json
import logging
import re
import shutil
from typing import Any, Callable, Generator
import uuid

from app.files.models import Files, get_file
from app.files.utils import (
    BASE_STORAGE_DIR,
    delete_storage_reference,
)
from app.tools.slide_presentation.models import upsert_slide_presentation
from app.tools.slide_presentation.models import get_slide_presentation
from app.tools.slide_presentation.rendering.utils import render_slide_presentation
from app.tools.slide_presentation.sanitizer import (
    sanitize_slide_presentation_title,
    validate_slide_presentation_asset_file_ids,
    validate_slide_presentation_html,
)
from app.tools.slide_presentation.storage import (
    build_presentation_storage_prefix,
    delete_slide_presentation_artifacts,
    upload_presentation_artifacts,
)

logger = logging.getLogger(__name__)


class PresentationRevisionConflict(RuntimeError):
    """The source changed while an older derivative render was running."""


_EMBEDDED_REVIEW_ASSET_RE = re.compile(
    r"data:image/(?:gif|jpe?g|png|webp);base64,[A-Za-z0-9+/=\s]+",
    re.IGNORECASE,
)


def _mask_review_assets(html: str) -> tuple[str, dict[str, str]]:
    """Keep large embedded image bytes out of the visual-review prompt."""
    assets: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"__OMLORIX_EMBEDDED_IMAGE_{len(assets) + 1}__"
        assets[token] = match.group(0)
        return token

    return _EMBEDDED_REVIEW_ASSET_RE.sub(replace, html), assets


def _restore_review_assets(html: str, assets: dict[str, str]) -> str:
    """Restore only exact opaque tokens emitted by the review request."""
    restored = str(html or "")
    for token, data_uri in assets.items():
        restored = restored.replace(token, data_uri)
    return restored


def _sse(event: str, data: dict[str, Any]) -> str:
    """Encode one presentation event using Omlorix's normal stream protocol."""
    return json.dumps({"t": "slide_presentation_evt", "event": event, "data": data}, ensure_ascii=False) + "\n"


def _title_from_brief(markdown: str) -> str:
    """Use the first Markdown heading as the artifact name when available."""
    match = re.search(r"^\s*#\s+(.+?)\s*$", markdown, re.M)
    return sanitize_slide_presentation_title(
        match.group(1) if match else "Presentation",
        fallback="Presentation",
    )


def _asset_ids_from_markdown(markdown: str) -> list[str]:
    """Collect explicit Omlorix image references included in a presentation brief."""
    return list(dict.fromkeys(re.findall(r"omlorix-file://([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})", markdown)))




def run_presentation_pipeline(
    *,
    user_id: str,
    markdown_file_id: str,
    db,
    chat_id: str | None = None,
    project_id: str | None = None,
    user_role: str | None = None,
    input_file_ids: list[str] | None = None,
    generation_id: str | None = None,
) -> Generator[str, None, dict[str, Any]]:
    """Run one presentation specialist using the shared subagent runtime."""
    from app.tools.slide_presentation.specialist import run_specialist

    return (yield from run_specialist(
        user_id=user_id, markdown_file_id=markdown_file_id, db=db,
        chat_id=chat_id, project_id=project_id, user_role=user_role,
        input_file_ids=input_file_ids, parent_generation_id=generation_id,
    ))


def _delete_unpublished_render_file(db, user_id: str, file_id: str | None) -> None:
    """Best-effort cleanup for a derivative that never became current."""
    if not file_id:
        return
    try:
        record = get_file(db, str(file_id), str(user_id))
        if record is None:
            return
        try:
            delete_storage_reference(
                storage_provider=str(record.storage_provider or "local"),
                storage_key=str(record.storage_key or ""),
                user_id=str(user_id),
                file_name=str(record.file_name or "presentation.pptx"),
            )
        except Exception:
            logger.warning(
                "Could not remove unpublished presentation file bytes",
                exc_info=True,
            )
        db.delete(record)
        db.commit()
    except Exception:
        logger.warning(
            "Could not remove unpublished presentation file record",
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            logger.warning(
                "Could not roll back unpublished presentation cleanup",
                exc_info=True,
            )


def _mark_render_failed_if_current(
    db,
    user_id: str,
    html_file_id: str,
    revision: int,
    *,
    before_commit: Callable[[], None] | None = None,
) -> None:
    """Do not let an obsolete render overwrite a newer source's stale state."""
    try:
        db.rollback()
        source = get_file(db, str(html_file_id), str(user_id))
        meta = dict(source.meta) if source and isinstance(source.meta, dict) else {}
        if source is None or int(meta.get("canvas_revision") or 0) != revision:
            return
        meta["presentation_render_status"] = "failed"
        source.meta = meta
        db.add(source)
        if before_commit is not None:
            before_commit()
        db.commit()
    except Exception:
        # This helper runs while preserving a render exception. A second
        # database failure must be observable in logs but never replace it.
        logger.warning(
            "Could not persist failed presentation render status",
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            logger.warning(
                "Could not roll back failed presentation status update",
                exc_info=True,
            )


def _mark_rendering_if_current(
    db, user_id: str, html_file_id: str, revision: int
) -> None:
    """Atomically enter rendering state only for the requested revision."""
    source = (
        db.query(Files)
        .filter(Files.id == str(html_file_id), Files.user_id == str(user_id))
        .with_for_update()
        .first()
    )
    meta = dict(source.meta) if source and isinstance(source.meta, dict) else {}
    if source is None or int(meta.get("canvas_revision") or 0) != revision:
        db.rollback()
        raise PresentationRevisionConflict(
            "The presentation changed before rendering began."
        )
    meta["presentation_render_status"] = "rendering"
    source.meta = meta
    db.add(source)
    db.commit()


def rerender_presentation_source(
    *,
    db,
    user_id: str,
    html_file_id: str,
    html: str,
    expected_revision: int | None = None,
) -> Generator[str, None, dict[str, Any]]:
    """Render one immutable revision and publish it only while still current."""
    expected_slide_count = validate_slide_presentation_html(html)
    record = get_slide_presentation(db, str(html_file_id), str(user_id))
    if not record:
        raise ValueError("The presentation record for this HTML file was not found.")
    source_record = get_file(db, str(html_file_id), str(user_id))
    source_meta = (
        dict(source_record.meta)
        if source_record and isinstance(source_record.meta, dict)
        else {}
    )
    revision = int(expected_revision or source_meta.get("canvas_revision") or 1)
    if int(source_meta.get("canvas_revision") or 0) != revision:
        raise PresentationRevisionConflict(
            "The presentation changed before rendering began."
        )
    asset_file_ids = validate_slide_presentation_asset_file_ids(
        db,
        str(user_id),
        source_meta.get("slide_presentation_asset_file_ids") or [],
    )
    old_pptx_file_id = str(record.file_id or "") or None
    old_storage_provider = str(record.storage_provider or "local")
    old_storage_prefix = str(record.storage_prefix or "")
    old_slide_count = int(record.slide_count or 0)
    title = str(record.title or "Presentation")
    base_dir = BASE_STORAGE_DIR / build_presentation_storage_prefix(
        user_id, str(html_file_id)
    )
    base_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = base_dir.parent / f".{html_file_id}-render-{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=True, exist_ok=False)
    new_pptx_file_id: str | None = None
    manifest: dict[str, Any] | None = None

    try:
        # Keep the first progress yield inside the terminal-state boundary. A
        # client can close the generator while it is suspended at that yield,
        # which raises GeneratorExit rather than Exception.
        _mark_rendering_if_current(db, user_id, html_file_id, revision)
        yield _sse(
            "status",
            {
                "phase": "rendering",
                "message": "Updating slide previews…",
                "revision": revision,
            },
        )
        rendered = render_slide_presentation(
            html=html,
            user_id=user_id,
            filename=f"{sanitize_slide_presentation_title(title, fallback='Presentation')}.pptx",
            presentation_dir=staging_dir,
            input_file_ids=None,
            existing_file_id=None,
            artifact_presentation_id=str(html_file_id),
            db=db,
        )
        new_pptx_file_id = str(rendered["file_id"])
        slide_count = int(rendered.get("slide_count") or expected_slide_count)
        if slide_count != expected_slide_count:
            raise RuntimeError(
                "Renderer slide count did not match the canonical HTML deck."
            )

        metadata = {
            "title": title,
            "slide_count": slide_count,
            "html_file_id": str(html_file_id),
            "brief_file_id": source_meta.get("slide_presentation_brief_file_id"),
            "asset_file_ids": asset_file_ids,
            "render_revision": revision,
        }
        (staging_dir / "presentation.html").write_text(html, encoding="utf-8")
        (staging_dir / "title.txt").write_text(title, encoding="utf-8")
        (staging_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Hold the canonical source row while the complete immutable artifact
        # bundle is uploaded and the index pointer is changed.
        locked_source = (
            db.query(Files)
            .filter(Files.id == str(html_file_id), Files.user_id == str(user_id))
            .with_for_update()
            .first()
        )
        locked_meta = (
            dict(locked_source.meta)
            if locked_source and isinstance(locked_source.meta, dict)
            else {}
        )
        if locked_source is None or int(locked_meta.get("canvas_revision") or 0) != revision:
            raise PresentationRevisionConflict(
                "A newer presentation revision was saved while rendering."
            )

        manifest = upload_presentation_artifacts(
            presentation_dir=staging_dir,
            user_id=user_id,
            presentation_id=html_file_id,
            slide_count=slide_count,
            revision=revision,
        )
        storage_meta = dict(manifest)
        storage_meta.update(metadata)
        upsert_slide_presentation(
            db,
            presentation_id=html_file_id,
            user_id=user_id,
            title=title,
            slide_count=slide_count,
            storage_provider=str(manifest.get("provider") or "local"),
            storage_prefix=str(manifest["storage_prefix"]),
            file_id=new_pptx_file_id,
            storage_meta=storage_meta,
            commit=False,
        )
        locked_meta.update(
            {
                "presentation_pptx_file_id": new_pptx_file_id,
                "presentation_slide_count": slide_count,
                "presentation_render_revision": revision,
                "presentation_render_status": "ready",
            }
        )
        locked_source.meta = locked_meta
        db.add(locked_source)
        db.commit()
    except GeneratorExit:
        if manifest:
            delete_slide_presentation_artifacts(
                storage_provider=str(manifest.get("provider") or "local"),
                storage_prefix=str(manifest.get("storage_prefix") or ""),
                slide_count=expected_slide_count,
            )
        _delete_unpublished_render_file(db, user_id, new_pptx_file_id)
        _mark_render_failed_if_current(db, user_id, html_file_id, revision)
        raise
    except PresentationRevisionConflict:
        db.rollback()
        if manifest:
            delete_slide_presentation_artifacts(
                storage_provider=str(manifest.get("provider") or "local"),
                storage_prefix=str(manifest.get("storage_prefix") or ""),
                slide_count=expected_slide_count,
            )
        _delete_unpublished_render_file(db, user_id, new_pptx_file_id)
        raise
    except Exception:
        if manifest:
            delete_slide_presentation_artifacts(
                storage_provider=str(manifest.get("provider") or "local"),
                storage_prefix=str(manifest.get("storage_prefix") or ""),
                slide_count=expected_slide_count,
            )
        _delete_unpublished_render_file(db, user_id, new_pptx_file_id)
        _mark_render_failed_if_current(db, user_id, html_file_id, revision)
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    if old_pptx_file_id and old_pptx_file_id != new_pptx_file_id:
        _delete_unpublished_render_file(db, user_id, old_pptx_file_id)
    if old_storage_prefix and old_storage_prefix != str(manifest["storage_prefix"]):
        delete_slide_presentation_artifacts(
            storage_provider=old_storage_provider,
            storage_prefix=old_storage_prefix,
            slide_count=old_slide_count,
        )

    result = {
        "presentation_id": html_file_id,
        "html_file_id": html_file_id,
        "file_id": new_pptx_file_id,
        "pptx_file_id": new_pptx_file_id,
        "title": title,
        "slide_count": slide_count,
        "asset_file_ids": asset_file_ids,
        "revision": revision,
        "operation": "updated",
    }
    yield _sse(
        "slide_images",
        {
            "presentation_id": html_file_id,
            "count": slide_count,
            "revision": revision,
        },
    )
    yield _sse("complete", result)
    return result
