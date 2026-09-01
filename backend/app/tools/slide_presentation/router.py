"""Router for serving presentation slide images and resolving file previews."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import tempfile
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from reportlab.pdfgen import canvas as pdf_canvas
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.dependencies import get_db, get_db_log, verified_user
from app.files.models import get_file
from app.files.storage import get_user_file_storage_config
from app.files.utils import BASE_STORAGE_DIR, materialize_file_record
from app.logging.models import create_audit_log, get_audit_request_ip
from app.tools.canvas_markdown.utils import (
    CanvasRevisionConflict,
    save_canvas_markdown,
)
from app.tools.slide_presentation.models import (
    get_slide_presentation,
    resolve_slide_presentation_by_file_id,
)
from app.tools.slide_presentation.storage import (
    download_slide_to_temp,
    get_presentation_slide_count,
    load_presentation_title,
    materialize_presentation_artifact,
)
from app.tools.slide_presentation.pipeline import (
    PresentationRevisionConflict,
    rerender_presentation_source,
)
from app.tools.slide_presentation.sanitizer import (
    sanitize_slide_presentation_title,
    sanitize_slide_presentation_html,
    validate_slide_presentation_html,
)
from app.tools.slide_presentation.schemas import (
    SlidePresentationEditorRenderRequest,
    SlidePresentationEditorRenderResponse,
    SlidePresentationEditorResponse,
    SlidePresentationEditorSaveRequest,
    SlidePresentationEditorSaveResponse,
)

presentations_router = APIRouter(
    prefix="/api/v1/presentations",
    tags=["presentations"],
)

logger = logging.getLogger(__name__)


def _audit_editor_event(
    db_log: Session,
    request: Request,
    user_id: str,
    action: str,
    details: dict,
) -> None:
    """Record presentation edits without ever logging the deck HTML."""

    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details,
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="presentations",
    )


def _audit_presentation_export(
    db_log: Session,
    request: Request,
    user_id: str,
    details: dict,
    *,
    temp_path: Path,
) -> None:
    """Fail closed and remove the export artifact if auditing fails."""

    try:
        _audit_editor_event(
            db_log,
            request,
            user_id,
            "EXPORT_SLIDE_PRESENTATION",
            details,
        )
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _owned_editor_records(db: Session, user_id: str, presentation_id: str):
    """Resolve the presentation index and its canonical owned HTML file."""

    presentation = get_slide_presentation(db, presentation_id, user_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")

    source = get_file(db, presentation.id, user_id)
    source_meta = source.meta if source and isinstance(source.meta, dict) else {}
    if source is None or source_meta.get("slide_presentation_source") is not True:
        raise HTTPException(
            status_code=422,
            detail="This presentation does not have an editable source file",
        )
    return presentation, source, dict(source_meta)


def _editor_revision_payload(source_meta: dict) -> tuple[int, int, str]:
    canvas_revision = max(0, int(source_meta.get("canvas_revision") or 0))
    render_revision = max(
        0,
        int(source_meta.get("presentation_render_revision") or 0),
    )
    render_status = str(
        source_meta.get("presentation_render_status")
        or ("ready" if render_revision >= canvas_revision else "stale")
    )
    if render_revision < canvas_revision and render_status == "ready":
        render_status = "stale"
    return canvas_revision, render_revision, render_status


def _read_editor_source(
    source,
    user_id: str,
    presentation_id: str,
    storage_provider: str,
    storage_prefix: str | None = None,
) -> str:
    """Read the canonical Canvas source, falling back to its artifact copy."""

    try:
        return materialize_file_record(source, user_id).read_text(encoding="utf-8")
    except Exception:
        try:
            path = materialize_presentation_artifact(
                user_id,
                presentation_id,
                "presentation.html",
                storage_provider=storage_provider,
                storage_prefix=storage_prefix,
            )
            return path.read_text(encoding="utf-8")
        except Exception as exc:
            raise HTTPException(
                status_code=404,
                detail="Presentation source is not available",
            ) from exc


def _validate_presentation_id(presentation_id: str) -> str:
    normalized = str(presentation_id or "").strip()
    if not normalized or "/" in normalized or "\\" in normalized or ".." in normalized:
        raise HTTPException(status_code=400, detail="Invalid presentation id")
    return normalized


def _sanitize_download_filename_fragment(value: str) -> str:
    return re.sub(r'[\r\n"\'\\\\/]+', "-", str(value or "").strip()) or "presentation"


def _is_local_provider(storage_provider: str | None = None) -> bool:
    provider = (
        str(storage_provider or get_user_file_storage_config().provider or "local")
        .strip()
        .lower()
        or "local"
    )
    return provider == "local"


def _get_presentations_dir(user_id: str) -> Path:
    return BASE_STORAGE_DIR / str(user_id) / "presentations"


def _get_images_dir(user_id: str, presentation_id: str) -> Path:
    return _get_presentations_dir(user_id) / presentation_id / "images"


def _count_slide_images(images_dir: Path) -> int:
    if not images_dir.exists():
        return 0
    return len(sorted(images_dir.glob("slide_*.png")))


def _resolve_cloud_slide_count(
    user_id: str,
    presentation_id: str,
    storage_provider: str,
    storage_prefix: str | None = None,
) -> int:
    slide_count = int(
        get_presentation_slide_count(
            user_id,
            presentation_id,
            storage_provider=storage_provider,
            storage_prefix=storage_prefix,
        )
        or 0
    )
    if slide_count > 0:
        return slide_count

    try:
        materialize_presentation_artifact(
            user_id,
            presentation_id,
            "metadata.json",
            storage_provider=storage_provider,
            storage_prefix=storage_prefix,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Presentation not found")

    raise HTTPException(
        status_code=422,
        detail="Slide images not available. The presentation may not have been fully rendered or the rendering service is not configured.",
    )


def _resolve_slide_paths(
    user_id: str,
    presentation_id: str,
    storage_provider: str,
    storage_prefix: str | None = None,
) -> list[Path]:
    slide_count = _resolve_cloud_slide_count(
        user_id, presentation_id, storage_provider, storage_prefix
    )
    slide_paths: list[Path] = []
    for slide_number in range(1, slide_count + 1):
        try:
            slide_paths.append(
                download_slide_to_temp(
                    user_id,
                    presentation_id,
                    slide_number,
                    storage_provider=storage_provider,
                    storage_prefix=storage_prefix,
                )
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Slide {slide_number} not found"
            )
    return slide_paths


def _build_slide_pdf_file(slide_paths: list[Path], output_path: Path) -> None:
    """Write one PDF page at a time so exports do not retain every bitmap."""
    if not slide_paths:
        raise HTTPException(status_code=404, detail="Slide images not found")
    document = pdf_canvas.Canvas(str(output_path), pagesize=(1920, 1080))
    try:
        for slide_path in slide_paths:
            document.drawImage(
                str(slide_path),
                0,
                0,
                width=1920,
                height=1080,
                preserveAspectRatio=True,
                mask="auto",
            )
            document.showPage()
        document.save()
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def _read_presentation_title(presentation_dir: Path) -> str:
    title_path = presentation_dir / "title.txt"
    if title_path.exists():
        try:
            title = title_path.read_text(encoding="utf-8").strip()
            if title:
                return title
        except Exception:
            pass

    metadata_path = presentation_dir / "metadata.json"
    if metadata_path.exists():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        title = str(payload.get("title") or "").strip()
        if title:
            return title

    return ""


def _resolve_direct_presentation_meta(user_id: str, meta: dict) -> dict | None:
    presentation_id = str(meta.get("presentation_id") or "").strip()
    if not presentation_id:
        return None

    if _is_local_provider():
        images_dir = _get_images_dir(user_id, presentation_id)
        slide_count = _count_slide_images(images_dir)
        if slide_count <= 0:
            return None
        title = _read_presentation_title(images_dir.parent)
    else:
        slide_count = int(get_presentation_slide_count(user_id, presentation_id) or 0)
        if slide_count <= 0:
            return None
        title = load_presentation_title(user_id, presentation_id)

    return {
        "presentation_id": presentation_id,
        "slide_count": slide_count,
        "title": title,
    }


def _resolve_indexed_presentation_meta(
    db: Session, user_id: str, file_id: str
) -> dict | None:
    indexed = resolve_slide_presentation_by_file_id(db, file_id, user_id)
    if indexed is None:
        return None
    slide_count = int(indexed.slide_count or 0)
    if slide_count <= 0:
        return None
    return {
        "presentation_id": str(indexed.id),
        "slide_count": slide_count,
        "title": str(indexed.title or "").strip(),
    }


def resolve_file_presentation_preview(
    db: Session, user_id: str, file_id: str
) -> dict | None:
    file_record = get_file(db, file_id, user_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    if not meta.get("render_slide_presentation") and not meta.get("slide_presentation_source"):
        return None

    resolved = _resolve_direct_presentation_meta(user_id, meta)
    if resolved is None:
        resolved = _resolve_indexed_presentation_meta(db, user_id, file_id)
    if resolved is None:
        return None

    updated_meta = dict(meta)
    updated_meta["presentation_id"] = resolved["presentation_id"]
    updated_meta["slide_count"] = int(resolved["slide_count"] or 0)
    if resolved.get("title"):
        updated_meta["presentation_title"] = resolved["title"]

    if updated_meta != meta:
        file_record.meta = updated_meta
        db.commit()
        db.refresh(file_record)

    title = str(
        resolved.get("title")
        or updated_meta.get("presentation_title")
        or meta.get("original_filename")
        or file_record.file_name
        or "Presentation"
    ).strip()

    return {
        "file_id": str(updated_meta.get("presentation_pptx_file_id") or file_record.id),
        "html_file_id": str(resolved["presentation_id"]),
        "presentation_id": resolved["presentation_id"],
        "slide_count": int(resolved["slide_count"] or 0),
        "title": title,
    }


@presentations_router.get("/by-file/{file_id}")
def get_presentation_preview_by_file(
    file_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    result = resolve_file_presentation_preview(db, str(user.id), file_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail="Slide preview not available for this file"
        )
    return result


@presentations_router.get(
    "/{presentation_id}/editor",
    response_model=SlidePresentationEditorResponse,
)
def get_presentation_editor_source(
    presentation_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Return the editable source only to the presentation owner."""

    presentation_id = _validate_presentation_id(presentation_id)
    user_id = str(user.id)
    presentation, source, source_meta = _owned_editor_records(
        db, user_id, presentation_id
    )
    html = _read_editor_source(
        source,
        user_id,
        presentation_id,
        str(presentation.storage_provider or "local"),
        str(presentation.storage_prefix or ""),
    )
    try:
        slide_count = validate_slide_presentation_html(html)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Presentation source is not editable",
        ) from exc

    canvas_revision, render_revision, render_status = _editor_revision_payload(
        source_meta
    )
    return SlidePresentationEditorResponse(
        presentation_id=presentation_id,
        file_id=str(presentation.file_id or ""),
        title=str(presentation.title or "Presentation"),
        html=html,
        slide_count=slide_count,
        canvas_revision=canvas_revision,
        render_revision=render_revision,
        render_status=render_status,
    )


@presentations_router.put(
    "/{presentation_id}/editor",
    response_model=SlidePresentationEditorSaveResponse,
)
def save_presentation_editor_source(
    presentation_id: str,
    payload: SlidePresentationEditorSaveRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Persist one complete editor snapshot and mark derivatives as stale."""

    presentation_id = _validate_presentation_id(presentation_id)
    user_id = str(user.id)
    presentation, source, source_meta = _owned_editor_records(
        db, user_id, presentation_id
    )
    current_revision, render_revision, _ = _editor_revision_payload(source_meta)
    if payload.expected_revision != current_revision:
        raise HTTPException(
            status_code=409,
            detail="The presentation changed in another editor. Reload before saving.",
        )

    # Generated decks are static by contract. Re-sanitize browser output so a
    # source-mode edit cannot add scripts, event handlers, or remote requests.
    sanitized_html = sanitize_slide_presentation_html(payload.html)
    try:
        slide_count = validate_slide_presentation_html(sanitized_html)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="The edited presentation is not a valid 1920 by 1080 slide deck.",
        ) from exc

    title = sanitize_slide_presentation_title(payload.title)
    if not title:
        raise HTTPException(status_code=400, detail="Presentation title is required")

    try:
        save_result = save_canvas_markdown(
            db=db,
            user_id=user_id,
            file_id=presentation_id,
            content=sanitized_html,
            content_type="html",
            filename=f"{title}.html",
            edit_source="presentation_editor",
            edited_by=user_id,
            allow_html_attachment=True,
            content_validator=validate_slide_presentation_html,
            expected_revision=payload.expected_revision,
        )
    except CanvasRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="The presentation changed in another editor. Reload before saving.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    canvas_revision = int(save_result.get("canvas_revision") or current_revision + 1)
    # An external renderer commits through its own SQLAlchemy session.
    db.expire_all()
    refreshed_source = get_file(db, presentation_id, user_id)
    refreshed_meta = (
        dict(refreshed_source.meta)
        if refreshed_source and isinstance(refreshed_source.meta, dict)
        else {}
    )
    refreshed_meta.update(
        {
            "presentation_render_status": "stale",
            "presentation_slide_count": slide_count,
        }
    )
    if refreshed_source is not None:
        refreshed_source.meta = refreshed_meta
    presentation.title = title
    presentation.last_updated_at = datetime.now(timezone.utc)
    if refreshed_source is not None:
        db.add(refreshed_source)
    db.add(presentation)
    db.commit()

    # The Canvas file is canonical. Keep the last complete artifact bundle
    # untouched until the matching revision has rendered successfully.

    _audit_editor_event(
        db_log,
        request,
        user_id,
        "SLIDE_PRESENTATION_SOURCE_SAVED",
        {
            "presentation_id": presentation_id,
            "canvas_revision": canvas_revision,
            "slide_count": slide_count,
        },
    )
    return SlidePresentationEditorSaveResponse(
        presentation_id=presentation_id,
        file_id=str(presentation.file_id or ""),
        title=title,
        slide_count=slide_count,
        canvas_revision=canvas_revision,
        render_revision=render_revision,
        render_status="stale",
    )


@presentations_router.post(
    "/{presentation_id}/editor/render",
    response_model=SlidePresentationEditorRenderResponse,
)
def render_presentation_editor_source(
    presentation_id: str,
    payload: SlidePresentationEditorRenderRequest,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Refresh PPTX and slide-image derivatives from the saved revision."""

    presentation_id = _validate_presentation_id(presentation_id)
    user_id = str(user.id)
    presentation, source, source_meta = _owned_editor_records(
        db, user_id, presentation_id
    )
    current_revision, _, _ = _editor_revision_payload(source_meta)
    if payload.expected_revision != current_revision:
        raise HTTPException(
            status_code=409,
            detail="A newer presentation revision must be saved before rendering.",
        )

    # Rendering is the expensive half of the slide-presentation tool. Apply
    # the same fail-closed admission policy as model-initiated tool calls,
    # using the authenticated owner's current group context.
    from app.tools.helper import enforce_tool_rate_limit_or_raise

    enforce_tool_rate_limit_or_raise(
        db,
        user_id=user_id,
        group_id=getattr(user, "group_id", None),
        tool_name="slide_presentation",
    )
    from app.workers.tool_jobs import external_rendering_enabled

    rendering_is_external = external_rendering_enabled()

    try:
        if rendering_is_external:
            from app.workers.rendering import (
                enqueue_presentation_rerender,
                wait_for_rendering_job,
            )

            job = enqueue_presentation_rerender(
                user_id=user_id,
                presentation_id=presentation_id,
                expected_revision=payload.expected_revision,
                audit_ip_address=get_audit_request_ip(request),
                audit_user_agent=request.headers.get("user-agent"),
                audit_success_action="SLIDE_PRESENTATION_RENDERED",
                audit_failure_action="SLIDE_PRESENTATION_RENDER_FAILED",
            )
            queued_result = wait_for_rendering_job(job)
            result = (
                queued_result.get("result")
                if isinstance(queued_result.get("result"), dict)
                else {}
            )
        else:
            html = _read_editor_source(
                source,
                user_id,
                presentation_id,
                str(presentation.storage_provider or "local"),
                str(presentation.storage_prefix or ""),
            )
            rerender = rerender_presentation_source(
                db=db,
                user_id=user_id,
                html_file_id=presentation_id,
                html=html,
                expected_revision=payload.expected_revision,
            )
            while True:
                next(rerender)
    except StopIteration as completed:
        result = completed.value or {}
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "render_still_processing",
                "job_id": getattr(locals().get("job"), "id", None),
            },
            headers={"Retry-After": "3"},
        ) from exc
    except PresentationRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        from app.workers.models import WorkerJobFailed

        if isinstance(exc, WorkerJobFailed) and exc.code == "presentation_revision_conflict":
            raise HTTPException(
                status_code=409,
                detail="A newer presentation revision must be saved before rendering.",
            ) from exc
        if isinstance(exc, WorkerJobFailed) and exc.code == "presentation_invalid":
            raise HTTPException(status_code=400, detail="Invalid presentation source") from exc
        # Clear any partial render/upload transaction before attempting to
        # record a clean terminal state in the same session.
        db.rollback()
        failed_source = get_file(db, presentation_id, user_id)
        if failed_source:
            failed_meta = (
                dict(failed_source.meta)
                if isinstance(failed_source.meta, dict)
                else {}
            )
            if int(failed_meta.get("canvas_revision") or 0) == current_revision:
                failed_meta["presentation_render_status"] = "failed"
                failed_source.meta = failed_meta
                db.add(failed_source)
                db.commit()
        if not rendering_is_external:
            _audit_editor_event(
                db_log,
                request,
                user_id,
                "SLIDE_PRESENTATION_RENDER_FAILED",
                {"presentation_id": presentation_id, "canvas_revision": current_revision},
            )
        raise HTTPException(
            status_code=502,
            detail="The presentation was saved, but refreshed previews could not be rendered.",
        ) from exc

    refreshed_source = get_file(db, presentation_id, user_id)
    refreshed_meta = (
        refreshed_source.meta
        if refreshed_source and isinstance(refreshed_source.meta, dict)
        else {}
    )
    canvas_revision, render_revision, render_status = _editor_revision_payload(
        refreshed_meta
    )
    if render_revision < payload.expected_revision:
        # Never acknowledge an editor render unless the published derivatives
        # cover at least the exact revision requested by the client. This also
        # prevents a malformed success response from keeping the browser's
        # bounded render drain alive without making revision progress.
        raise HTTPException(
            status_code=502,
            detail="The presentation render did not publish the requested revision.",
        )
    if not rendering_is_external:
        _audit_editor_event(
            db_log,
            request,
            user_id,
            "SLIDE_PRESENTATION_RENDERED",
            {
                "presentation_id": presentation_id,
                "canvas_revision": canvas_revision,
                "slide_count": int(result.get("slide_count") or presentation.slide_count or 1),
            },
        )
    return SlidePresentationEditorRenderResponse(
        presentation_id=presentation_id,
        file_id=str(result.get("file_id") or presentation.file_id or ""),
        title=str(result.get("title") or presentation.title or "Presentation"),
        slide_count=int(result.get("slide_count") or presentation.slide_count or 1),
        canvas_revision=canvas_revision,
        render_revision=render_revision,
        render_status=render_status,
    )


@presentations_router.get("/{presentation_id}/slides/count")
def get_slide_count(
    presentation_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    presentation_id = _validate_presentation_id(presentation_id)
    user_id = str(user.id)
    presentation = get_slide_presentation(db, presentation_id, user_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")
    storage_provider = str(presentation.storage_provider or "local")

    return {
        "count": _resolve_cloud_slide_count(
            user_id,
            presentation_id,
            storage_provider,
            str(presentation.storage_prefix or ""),
        )
    }


@presentations_router.get("/{presentation_id}/draft-slides/{slide_number}")
def get_draft_slide_image(
    presentation_id: str,
    slide_number: int,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Serve a complete provisional render while visual refinement continues.

    Draft images deliberately bypass the published presentation index because
    that index must continue to identify only the final, portable artifact
    bundle.  Ownership comes from the canonical Canvas source record, and the
    mutable response is never cacheable. Refinement passes render into a
    staging directory and atomically swap the complete image directory into
    place. The first render writes directly into the live image directory, so
    an early request can temporarily receive 404 for a slide that has not been
    written yet; draft clients must treat that response as not ready and retry.
    """

    presentation_id = _validate_presentation_id(presentation_id)
    user_id = str(user.id)
    source = get_file(db, presentation_id, user_id)
    source_meta = source.meta if source and isinstance(source.meta, dict) else {}
    if source is None or source_meta.get("slide_presentation_source") is not True:
        raise HTTPException(status_code=404, detail="Presentation draft not found")
    if slide_number <= 0:
        raise HTTPException(status_code=404, detail=f"Slide {slide_number} not found")

    image_path = _get_images_dir(user_id, presentation_id) / f"slide_{slide_number}.png"
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail=f"Slide {slide_number} not found")
    return FileResponse(
        image_path,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@presentations_router.get("/{presentation_id}/slides/archive")
def download_slide_images_archive(
    presentation_id: str,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    presentation_id = _validate_presentation_id(presentation_id)
    user_id = str(user.id)
    presentation = get_slide_presentation(db, presentation_id, user_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")
    storage_provider = str(presentation.storage_provider or "local")
    slide_paths = _resolve_slide_paths(
        user_id,
        presentation_id,
        storage_provider,
        str(presentation.storage_prefix or ""),
    )

    archive_handle = tempfile.NamedTemporaryFile(
        prefix="omlorix-presentation-", suffix=".zip", delete=False
    )
    archive_path = Path(archive_handle.name)
    archive_handle.close()
    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for slide_path in slide_paths:
                archive.write(slide_path, arcname=slide_path.name)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_presentation_id = _sanitize_download_filename_fragment(presentation_id)
    filename = f"presentation-{safe_presentation_id}-images-{timestamp}.zip"

    _audit_presentation_export(
        db_log,
        request,
        user_id,
        {
            "presentation_id": presentation_id,
            "format": "images_zip",
            "slide_count": len(slide_paths),
        },
        temp_path=archive_path,
    )
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@presentations_router.get("/{presentation_id}/slides/pdf")
def download_slide_images_pdf(
    presentation_id: str,
    request: Request,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    presentation_id = _validate_presentation_id(presentation_id)
    user_id = str(user.id)
    presentation = get_slide_presentation(db, presentation_id, user_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")
    storage_provider = str(presentation.storage_provider or "local")
    slide_paths = _resolve_slide_paths(
        user_id,
        presentation_id,
        storage_provider,
        str(presentation.storage_prefix or ""),
    )

    pdf_handle = tempfile.NamedTemporaryFile(
        prefix="omlorix-presentation-", suffix=".pdf", delete=False
    )
    pdf_path = Path(pdf_handle.name)
    pdf_handle.close()
    try:
        _build_slide_pdf_file(slide_paths, pdf_path)
    except Exception:
        pdf_path.unlink(missing_ok=True)
        raise
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_presentation_id = _sanitize_download_filename_fragment(presentation_id)
    filename = f"presentation-{safe_presentation_id}-{timestamp}.pdf"

    _audit_presentation_export(
        db_log,
        request,
        user_id,
        {
            "presentation_id": presentation_id,
            "format": "pdf",
            "slide_count": len(slide_paths),
        },
        temp_path=pdf_path,
    )
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=filename,
        background=BackgroundTask(pdf_path.unlink, missing_ok=True),
    )


@presentations_router.get("/{presentation_id}/slides/{slide_number}")
def get_slide_image(
    presentation_id: str,
    slide_number: int,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    presentation_id = _validate_presentation_id(presentation_id)
    user_id = str(user.id)
    presentation = get_slide_presentation(db, presentation_id, user_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")
    storage_provider = str(presentation.storage_provider or "local")

    if slide_number <= 0:
        raise HTTPException(status_code=404, detail=f"Slide {slide_number} not found")

    slide_count = _resolve_cloud_slide_count(
        user_id,
        presentation_id,
        storage_provider,
        str(presentation.storage_prefix or ""),
    )
    if slide_number > slide_count:
        raise HTTPException(status_code=404, detail=f"Slide {slide_number} not found")

    try:
        image_path = download_slide_to_temp(
            user_id,
            presentation_id,
            slide_number,
            storage_provider=storage_provider,
            storage_prefix=str(presentation.storage_prefix or ""),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Slide {slide_number} not found")

    return FileResponse(image_path, media_type="image/png")
