"""Direct Markdown → HTML → render → visual-refinement presentation loop."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Generator
import uuid

from PIL import Image, ImageDraw, ImageFont

from app.files.models import Files, get_file
from app.files.utils import (
    BASE_STORAGE_DIR,
    delete_storage_reference,
    materialize_file_record,
)
from app.llm.nested_generation import run_nested_generation, stream_nested_generation
from app.settings.models import get_settings_page_data
from app.tools.audit import stage_tool_audit_action
from app.tools.canvas_markdown.utils import save_canvas_markdown
from app.tools.slide_presentation.models import upsert_slide_presentation
from app.tools.slide_presentation.models import get_slide_presentation
from app.tools.slide_presentation.rendering.utils import render_slide_presentation
from app.tools.slide_presentation.sanitizer import (
    prepare_slide_presentation_html,
    sanitize_slide_presentation_title,
    validate_slide_presentation_asset_file_ids,
    validate_slide_presentation_html,
)
from app.tools.slide_presentation.storage import (
    build_presentation_storage_prefix,
    delete_slide_presentation_artifacts,
    upload_presentation_artifacts,
)
from app.tools.slide_presentation.system_instructions import get_sys_instruct_generate_html

logger = logging.getLogger(__name__)
MAX_VISUAL_REFINEMENTS = 3
MAX_REVIEW_SLIDE_IMAGES = 50
REVIEW_CONTACT_SHEET_SIZE = 12


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


def _extract_html(text: str) -> str:
    """Extract a complete HTML document from plain or fenced model output."""
    value = str(text or "").strip()
    fenced = re.search(r"```(?:html)?\s*(.*?)```", value, re.I | re.S)
    if fenced:
        value = fenced.group(1).strip()
    start = value.lower().find("<!doctype")
    if start < 0:
        start = value.lower().find("<html")
    end = value.lower().rfind("</html>")
    if start >= 0 and end >= start:
        value = value[start : end + len("</html>")]
    return value


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


def _review_message(html: str, images_dir: Path) -> dict[str, Any]:
    """Build bounded contact sheets that still review every rendered slide."""
    content: list[dict[str, Any]] = [{
        "type": "content",
        "content": (
            "Review every rendered slide for clipping, overlap, tiny text, weak hierarchy, "
            "inconsistent spacing, and poor visual balance. If the deck is excellent, reply only DONE. "
            "Otherwise return the complete corrected HTML document and nothing else. Preserve every "
            "__OMLORIX_EMBEDDED_IMAGE_N__ token exactly; Omlorix restores those image bytes after review."
            "\n\nCURRENT HTML:\n" + html
        ),
    }]
    def slide_number(path: Path) -> tuple[int, str]:
        """Sort renderer output by its numeric slide suffix, not lexically."""

        match = re.search(r"(\d+)(?=\.png$)", path.name, re.IGNORECASE)
        return (int(match.group(1)) if match else 2**31, path.name)

    image_paths = sorted(images_dir.glob("slide_*.png"), key=slide_number)
    if len(image_paths) > MAX_REVIEW_SLIDE_IMAGES:
        raise ValueError("The rendered deck exceeds the visual-review slide limit.")

    try:
        label_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
    except (OSError, ValueError):
        # Pillow's bundled font availability varies by deployment image.
        label_font = ImageFont.load_default()

    for start in range(0, len(image_paths), REVIEW_CONTACT_SHEET_SIZE):
        batch = image_paths[start : start + REVIEW_CONTACT_SHEET_SIZE]
        sheet = Image.new("RGB", (1920, 840), "white")
        draw = ImageDraw.Draw(sheet)
        for offset, image_path in enumerate(batch):
            row, column = divmod(offset, 4)
            with Image.open(image_path) as rendered:
                preview = rendered.convert("RGB")
                preview.thumbnail((460, 259))
                x = column * 480 + 10
                y = row * 280 + 20
                sheet.paste(preview, (x, y))
                preview.close()
            draw.text(
                (x, y - 16),
                f"Slide {start + offset + 1}",
                fill="black",
                font=label_font,
            )
        buffer = BytesIO()
        sheet.save(buffer, format="JPEG", quality=82, optimize=True)
        sheet.close()
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{encoded}",
                    "detail": "high",
                },
            }
        )
    return {"id": "slide-visual-review", "role": "user", "content": content}


def _update_source_meta(db, file_id: str, user_id: str, **updates: Any) -> None:
    """Attach presentation identity and render state to the canonical Canvas file."""
    record = get_file(db, file_id, user_id)
    if not record:
        raise ValueError("Presentation HTML file disappeared during generation.")
    meta = dict(record.meta) if isinstance(record.meta, dict) else {}
    meta.update(updates)
    record.meta = meta
    db.add(record)
    db.commit()


def _promote_refinement_images(
    presentation_dir: Path, refinement_dir: Path
) -> None:
    """Atomically replace a last-good preview set after refinement succeeds.

    Later visual-review renders use an isolated directory so a renderer error
    can never destroy the first usable deck.  Promotion happens only after the
    candidate HTML has validated, rendered, and been saved as the canonical
    Canvas revision.
    """

    candidate_images = refinement_dir / "images"
    if not candidate_images.is_dir():
        raise RuntimeError("Refinement render did not produce slide previews.")
    current_images = presentation_dir / "images"
    backup_images = presentation_dir / f".images-{uuid.uuid4().hex}.last-good"
    try:
        if current_images.exists():
            os.replace(current_images, backup_images)
        os.replace(candidate_images, current_images)
    except Exception:
        if not current_images.exists() and backup_images.exists():
            os.replace(backup_images, current_images)
        raise
    finally:
        shutil.rmtree(backup_images, ignore_errors=True)


def run_presentation_pipeline(
    *,
    user_id: str,
    markdown_file_id: str,
    db,
    chat_id: str | None = None,
    project_id: str | None = None,
    user_role: str | None = None,
    input_file_ids: list[str] | None = None,
) -> Generator[str, None, dict[str, Any]]:
    """Create and refine a presentation directly from one owned Markdown file."""
    brief_record = get_file(db, str(markdown_file_id), str(user_id))
    if not brief_record:
        raise ValueError("The presentation brief file was not found for this user.")
    file_type = str(brief_record.file_type or "").lower()
    if "markdown" not in file_type and not str(brief_record.file_name or "").lower().endswith((".md", ".markdown")):
        raise ValueError("The slide_presentation tool requires a Markdown file.")
    try:
        brief = materialize_file_record(brief_record, str(user_id)).read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError as exc:
        raise ValueError(
            "The presentation brief must be a UTF-8 encoded Markdown file."
        ) from exc
    if not brief.strip():
        raise ValueError("The presentation brief is empty.")
    requested_asset_ids = [
        *(input_file_ids or []),
        *_asset_ids_from_markdown(brief),
    ]
    asset_file_ids = validate_slide_presentation_asset_file_ids(
        db,
        str(user_id),
        requested_asset_ids,
    )

    config = get_settings_page_data(db, "slide_presentation") or {}
    model_id = str(config.get("presentation_model_id") or "").strip()
    if not model_id:
        raise ValueError("No presentation model is configured in admin settings.")
    title = _title_from_brief(brief)
    yield _sse(
        "status",
        {
            "phase": "generating",
            "message": "Creating presentation HTML…",
            "title": title,
            "max_refinements": MAX_VISUAL_REFINEMENTS,
        },
    )
    generation_stream = stream_nested_generation(
        db,
        model_id=model_id,
        user_id=user_id,
        chat_id=chat_id,
        project_id=project_id,
        user_role=user_role,
        purpose="slide-presentation-generate",
        phase="slide presentation HTML generation",
        instructions=(
            get_sys_instruct_generate_html()
            + "\nReturn only one complete HTML document. Treat the Markdown brief as authoritative."
            + "\nUploaded images include exact file IDs in their metadata. To place one in the deck, "
              "use an HTML image whose src is omlorix-file://FILE_ID. Do not copy or invent a file ID."
        ),
        messages=[{
            "id": "slide-brief",
            "role": "user",
            "content": [{
                "type": "content",
                "content": brief,
                "images": asset_file_ids,
            }],
        }],
    )
    # Forward provider-normalized text as feature events.  The browser renders
    # only sandboxed, sanitized slide sections, while this complete stream is
    # still accumulated and subjected to the normal server-side sanitizer and
    # validator before anything becomes canonical.
    while True:
        try:
            html_delta = next(generation_stream)
        except StopIteration as completed:
            generation = completed.value
            break
        if html_delta:
            yield _sse("html_delta", {"delta": html_delta})
    html = prepare_slide_presentation_html(
        _extract_html(generation.text),
        db=db,
        user_id=str(user_id),
        allowed_file_ids=asset_file_ids,
    )
    expected_slide_count = validate_slide_presentation_html(html)

    def stage_source_saved(saved: dict[str, Any]) -> None:
        stage_tool_audit_action(
            db,
            str(user_id),
            "SLIDE_PRESENTATION_SOURCE_SAVED",
            category="files",
            details={
                "presentation_id": str(saved["file_id"]),
                "canvas_revision": int(saved.get("canvas_revision") or 1),
                "slide_count": expected_slide_count,
                "asset_count": len(asset_file_ids),
                "project_id": project_id,
            },
        )

    source = save_canvas_markdown(
        db=db, user_id=user_id, content=html, content_type="html",
        filename=f"{title}.html", project_id=project_id,
        before_commit=stage_source_saved,
    )
    presentation_id = str(source["file_id"])
    revision = int(source.get("canvas_revision") or 1)

    def stage_failed_render() -> None:
        stage_tool_audit_action(
            db,
            str(user_id),
            "SLIDE_PRESENTATION_RENDER_FAILED",
            category="files",
            details={
                "presentation_id": presentation_id,
                "canvas_revision": revision,
                "asset_count": len(asset_file_ids),
                "project_id": project_id,
            },
        )

    pres_dir = BASE_STORAGE_DIR / build_presentation_storage_prefix(
        user_id, presentation_id
    )
    pres_dir.mkdir(parents=True, exist_ok=True)
    (pres_dir / "presentation.html").write_text(html, encoding="utf-8")
    (pres_dir / "title.txt").write_text(title, encoding="utf-8")
    _update_source_meta(
        db, presentation_id, user_id,
        slide_presentation_source=True, presentation_id=presentation_id,
        slide_presentation_brief_file_id=str(markdown_file_id),
        slide_presentation_asset_file_ids=asset_file_ids,
        presentation_render_status="rendering",
    )

    pptx_file_id: str | None = None
    unpublished_pptx_file_ids: set[str] = set()
    slide_count = 0
    yield _sse(
        "draft_complete",
        {
            "presentation_id": presentation_id,
            "title": title,
            "count": expected_slide_count,
            "revision": revision,
        },
    )
    manifest: dict[str, Any] | None = None
    # Refinements remain provisional until both their HTML and complete image
    # set succeed.  These last-good values let visual QA degrade gracefully to
    # the first valid render instead of turning a polished-draft failure into a
    # total presentation failure.
    persisted_html = html
    pending_html = html
    pending_slide_count = expected_slide_count
    try:
        for review_index in range(MAX_VISUAL_REFINEMENTS + 1):
            target_revision = revision + (0 if pending_html == persisted_html else 1)
            yield _sse(
                "status",
                {
                    "phase": "rendering",
                    "message": "Rendering slide previews…",
                    "revision": target_revision,
                    "pass": review_index + 1,
                    "max_passes": MAX_VISUAL_REFINEMENTS + 1,
                    "slide_count": pending_slide_count,
                },
            )
            previous_pptx_file_id = pptx_file_id
            refinement_dir: Path | None = None
            render_dir = pres_dir
            if pending_html != persisted_html:
                refinement_dir = (
                    pres_dir.parent
                    / f".{presentation_id}-refinement-{uuid.uuid4().hex}"
                )
                refinement_dir.mkdir(parents=True, exist_ok=False)
                render_dir = refinement_dir
            try:
                render = render_slide_presentation(
                    html=pending_html,
                    user_id=user_id,
                    filename=f"{sanitize_slide_presentation_title(title, fallback='Presentation')}.pptx",
                    presentation_dir=render_dir,
                    # Uploaded assets are already authorized and embedded as
                    # data URIs in the canonical HTML. Sending them again
                    # doubles the renderer request without adding information.
                    input_file_ids=None,
                    existing_file_id=None,
                    artifact_presentation_id=presentation_id,
                    db=db,
                )
                candidate_pptx_file_id = str(render["file_id"])
                unpublished_pptx_file_ids.add(candidate_pptx_file_id)
                candidate_slide_count = int(render.get("slide_count") or 0)
                if candidate_slide_count != pending_slide_count:
                    _delete_unpublished_render_file(
                        db, user_id, candidate_pptx_file_id
                    )
                    unpublished_pptx_file_ids.discard(candidate_pptx_file_id)
                    raise RuntimeError(
                        "Renderer slide count did not match the canonical HTML deck."
                    )
            except Exception:
                if refinement_dir is not None:
                    shutil.rmtree(refinement_dir, ignore_errors=True)
                if previous_pptx_file_id:
                    logger.warning(
                        "Presentation refinement render failed; publishing the last-good revision",
                        exc_info=True,
                    )
                    yield _sse(
                        "warning",
                        {
                            "code": "refinement_render_failed",
                            "recoverable": True,
                            "revision": revision,
                        },
                    )
                    break
                raise

            # From here onward, any escaped persistence/promotion error should
            # clean up this newly-created derivative, not the last-good PPTX.
            pptx_file_id = candidate_pptx_file_id

            if pending_html != persisted_html:
                try:
                    edited = save_canvas_markdown(
                        db=db,
                        user_id=user_id,
                        file_id=presentation_id,
                        content=pending_html,
                        content_type="html",
                        edit_source="slide_refinement",
                        edited_by=user_id,
                        expected_revision=revision,
                    )
                    revision = int(edited.get("canvas_revision") or revision + 1)
                    _promote_refinement_images(pres_dir, refinement_dir)
                finally:
                    shutil.rmtree(refinement_dir, ignore_errors=True)
                persisted_html = pending_html
                html = pending_html
                expected_slide_count = pending_slide_count
                (pres_dir / "presentation.html").write_text(
                    html, encoding="utf-8"
                )

            slide_count = candidate_slide_count
            if previous_pptx_file_id and previous_pptx_file_id != pptx_file_id:
                _delete_unpublished_render_file(
                    db, user_id, previous_pptx_file_id
                )
                unpublished_pptx_file_ids.discard(previous_pptx_file_id)

            # The authenticated draft endpoint exposes this complete immutable
            # image set while visual QA continues.  No database artifact index
            # is published until the final revision is selected below.
            yield _sse(
                "revision_ready",
                {
                    "presentation_id": presentation_id,
                    "count": slide_count,
                    "revision": revision,
                    "pass": review_index + 1,
                    "max_passes": MAX_VISUAL_REFINEMENTS + 1,
                },
            )
            if review_index >= MAX_VISUAL_REFINEMENTS:
                break
            yield _sse(
                "status",
                {
                    "phase": "refining",
                    "message": "Reviewing visual quality…",
                    "revision": revision,
                    "pass": review_index + 1,
                    "max_refinements": MAX_VISUAL_REFINEMENTS,
                },
            )
            review_html, review_assets = _mask_review_assets(html)
            try:
                review = run_nested_generation(
                    db,
                    model_id=model_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    project_id=project_id,
                    user_role=user_role,
                    purpose="slide-presentation-review",
                    phase="slide presentation visual review",
                    instructions=get_sys_instruct_generate_html() + "\nYou are performing visual QA. Reply DONE or return one complete corrected HTML document.",
                    messages=[_review_message(review_html, pres_dir / "images")],
                )
            except Exception:
                logger.warning(
                    "Presentation visual review failed; publishing the last-good revision",
                    exc_info=True,
                )
                yield _sse(
                    "warning",
                    {
                        "code": "visual_review_failed",
                        "recoverable": True,
                        "revision": revision,
                    },
                )
                break
            if review.text.strip().upper() == "DONE":
                break
            reviewed_html = _extract_html(review.text)
            if any(token not in reviewed_html for token in review_assets):
                logger.warning(
                    "Slide visual reviewer removed an embedded-image token"
                )
                break
            candidate = prepare_slide_presentation_html(
                _restore_review_assets(reviewed_html, review_assets),
                db=db,
                user_id=str(user_id),
                allowed_file_ids=asset_file_ids,
            )
            try:
                candidate_slide_count = validate_slide_presentation_html(candidate)
            except ValueError:
                # A reviewer that explains instead of returning a full replacement
                # must not discard the last successfully rendered deck.
                logger.warning("Slide visual reviewer returned no valid replacement HTML")
                yield _sse(
                    "warning",
                    {
                        "code": "visual_review_invalid",
                        "recoverable": True,
                        "revision": revision,
                    },
                )
                break
            pending_html = candidate
            pending_slide_count = candidate_slide_count
    except Exception:
        # Persist terminal failure state before propagating the original render
        # or nested-generation exception to the tool boundary.
        _mark_render_failed_if_current(
            db,
            str(user_id),
            presentation_id,
            revision,
            before_commit=stage_failed_render,
        )
        for unpublished_file_id in tuple(unpublished_pptx_file_ids):
            _delete_unpublished_render_file(db, user_id, unpublished_file_id)
        raise

    try:
        # Artifact upload and index persistence are part of rendering from the
        # user's perspective. Keep them in the same terminal-state boundary so
        # a storage outage cannot leave the canonical source stuck at
        # ``rendering`` forever.
        (pres_dir / "metadata.json").write_text(
            json.dumps({"title": title, "slide_count": slide_count, "html_file_id": presentation_id,
                        "brief_file_id": str(markdown_file_id), "asset_file_ids": asset_file_ids,
                        "render_revision": revision}, indent=2),
            encoding="utf-8",
        )
        locked_source = (
            db.query(Files)
            .filter(Files.id == presentation_id, Files.user_id == str(user_id))
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
                "The generated presentation changed before artifacts were published."
            )
        manifest = upload_presentation_artifacts(
            presentation_dir=pres_dir, user_id=user_id,
            presentation_id=presentation_id, slide_count=slide_count,
            revision=revision,
        )
        storage_provider = str(manifest.get("provider") or "local")
        storage_prefix = str(manifest.get("storage_prefix") or f"{user_id}/presentations/{presentation_id}")
        storage_meta = dict(manifest)
        storage_meta.update({"html_file_id": presentation_id, "brief_file_id": str(markdown_file_id),
                             "asset_file_ids": asset_file_ids, "render_revision": revision})
        upsert_slide_presentation(
            db, presentation_id=presentation_id, user_id=user_id, title=title,
            slide_count=slide_count, storage_provider=storage_provider,
            storage_prefix=storage_prefix, file_id=pptx_file_id, storage_meta=storage_meta,
            commit=False,
        )
        locked_meta.update(
            {
                "presentation_pptx_file_id": pptx_file_id,
                "presentation_slide_count": slide_count,
                "presentation_render_revision": revision,
                "presentation_render_status": "ready",
            }
        )
        locked_source.meta = locked_meta
        db.add(locked_source)
        stage_tool_audit_action(
            db,
            str(user_id),
            "SLIDE_PRESENTATION_RENDERED",
            category="files",
            details={
                "presentation_id": presentation_id,
                "pptx_file_id": pptx_file_id,
                "canvas_revision": revision,
                "slide_count": slide_count,
                "asset_count": len(asset_file_ids),
                "project_id": project_id,
            },
        )
        db.commit()
        if str(manifest.get("provider") or "local") == "local":
            for working_path in (
                pres_dir / "metadata.json",
                pres_dir / "title.txt",
                pres_dir / "presentation.html",
            ):
                working_path.unlink(missing_ok=True)
            shutil.rmtree(pres_dir / "images", ignore_errors=True)
    except Exception:
        _mark_render_failed_if_current(
            db,
            str(user_id),
            presentation_id,
            revision,
            before_commit=stage_failed_render,
        )
        if manifest:
            delete_slide_presentation_artifacts(
                storage_provider=str(manifest.get("provider") or "local"),
                storage_prefix=str(manifest.get("storage_prefix") or ""),
                slide_count=slide_count,
            )
        for unpublished_file_id in tuple(unpublished_pptx_file_ids):
            _delete_unpublished_render_file(db, user_id, unpublished_file_id)
        raise
    result = {
        "presentation_id": presentation_id, "html_file_id": presentation_id,
        "file_id": pptx_file_id, "pptx_file_id": pptx_file_id, "title": title,
        "slide_count": slide_count, "asset_file_ids": asset_file_ids,
        "revision": revision, "operation": "created",
    }
    yield _sse(
        "slide_images",
        {
            "presentation_id": presentation_id,
            "count": slide_count,
            "revision": revision,
        },
    )
    yield _sse("complete", result)
    return result


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
