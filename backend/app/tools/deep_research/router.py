from __future__ import annotations

import mimetypes
from pathlib import Path
import re
from typing import Annotated
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.logging.models import create_audit_log, get_audit_request_ip
from app.tools.canvas_markdown.pdf import render_canvas_markdown_pdf
from app.tools.deep_research.models import (
    DeepResearchArtifact,
    RUN_STATUS_COMPLETED,
    get_user_deep_research_run,
)
from app.tools.deep_research.storage import (
    get_deep_research_run_storage_provider,
    materialize_deep_research_artifact,
)
from app.utils.attachments import attachment_headers
from app.users.models import User


deep_research_router = APIRouter(
    prefix="/api/v1/deep-research",
    tags=["deep-research"],
)

_INLINE_IMAGE_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_REPORT_IMAGE_PATH_RE = re.compile(r"^artifacts/[A-Za-z0-9._/-]+$")


def _owned_run_or_404(db: Session, run_id: str, user_id: str):
    """Resolve one run with an ownership check shared by every endpoint."""

    run = get_user_deep_research_run(db, run_id, user_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deep Research run not found.",
        )
    return run


def _safe_report_export_filename(title: str | None, extension: str) -> str:
    """Create a readable attachment filename from a report title."""

    normalized_extension = str(extension or "pdf").strip().lstrip(".").lower()
    normalized_extension = (
        normalized_extension if normalized_extension in {"md", "pdf"} else "pdf"
    )
    raw_title = str(title or "").replace("\x00", "").strip() or "deep-research-report"
    raw_title = re.sub(r"[\r\n\t]+", " ", raw_title)
    raw_title = "".join("-" if char in '/\\:*?"<>|' else char for char in raw_title)
    raw_title = re.sub(r"\s+", " ", raw_title).strip(" .") or "deep-research-report"
    raw_title = (
        re.sub(
            r"\.(md|markdown|pdf|txt)$",
            "",
            raw_title,
            flags=re.IGNORECASE,
        ).strip(" .")
        or "deep-research-report"
    )
    suffix = f".{normalized_extension}"
    return f"{raw_title[: max(1, 255 - len(suffix))]}{suffix}"


def _report_export_title(run, markdown: str) -> str:
    """Prefer the approved research title, then the report H1 and query."""

    result_meta = run.result_meta if isinstance(run.result_meta, dict) else {}
    stored_title = str(result_meta.get("title") or "").strip()
    if stored_title:
        return stored_title
    for line in str(markdown or "").splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*#*\s*$", line)
        if match:
            # Filenames should describe the report without carrying Markdown
            # emphasis or inline-link punctuation into the operating system.
            return re.sub(r"[*_`\[\]]+", "", match.group(1)).strip()
    return str(getattr(run, "query", "") or "Deep Research report").strip()


def _report_image_resolver(run, user_id: str):
    """Authorize only validated, run-owned raster images referenced by the report."""

    allowed_images = {
        str(item.get("relative_path") or ""): str(item.get("media_type") or "")
        for item in run.artifacts or []
        if isinstance(item, dict)
        and item.get("validation_status") == "validated"
        and str(item.get("media_type") or "") in _INLINE_IMAGE_TYPES
    }

    def resolve(src: str) -> Path | None:
        raw_src = unquote(str(src or "").strip())
        parsed = urlparse(raw_src)
        if parsed.scheme or parsed.netloc:
            return None
        relative_path = parsed.path.removeprefix("./")
        if (
            not _REPORT_IMAGE_PATH_RE.fullmatch(relative_path)
            or relative_path not in allowed_images
        ):
            return None
        try:
            return materialize_deep_research_artifact(
                user_id,
                run.id,
                relative_path,
                storage_provider=get_deep_research_run_storage_provider(run),
            )
        except (FileNotFoundError, ValueError):
            # A missing optional figure should not prevent exporting the text
            # of an otherwise complete report.
            return None

    return resolve


@deep_research_router.get("/runs/{run_id}/export")
def export_deep_research_report(
    run_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    db_log: Annotated[Session, Depends(get_db_log)],
    user: Annotated[User, Depends(verified_user)],
    format: str = Query(default="pdf", pattern="^(md|pdf)$"),
):
    """Download a completed user-owned report as Markdown or a rendered PDF."""

    run = _owned_run_or_404(db, run_id, user.id)
    if run.status != RUN_STATUS_COMPLETED or not run.final_report_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The finished report is not ready to export.",
        )
    try:
        report_path = materialize_deep_research_artifact(
            user.id,
            run.id,
            run.final_report_path,
            storage_provider=get_deep_research_run_storage_provider(run),
        )
        markdown = report_path.read_text(encoding="utf-8")
    except (FileNotFoundError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The finished report could not be found.",
        )
    except (OSError, UnicodeError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The finished report could not be read.",
        )

    normalized_format = str(format or "pdf").lower()
    title = _report_export_title(run, markdown)
    filename = _safe_report_export_filename(title, normalized_format)
    content: str | bytes = markdown
    media_type = "text/markdown; charset=utf-8"
    if normalized_format == "pdf":
        rendered = render_canvas_markdown_pdf(
            db,
            user_id=str(user.id),
            markdown_text=markdown,
            filename=filename,
            image_path_resolver=_report_image_resolver(run, str(user.id)),
        )
        filename = rendered.filename
        content = rendered.content
        media_type = "application/pdf"

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="DEEP_RESEARCH_REPORT_EXPORTED",
        details={"run_id": run.id, "format": normalized_format},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="deep_research",
    )
    headers = {
        **attachment_headers(filename, fallback="deep-research-report"),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type=media_type, headers=headers)


@deep_research_router.get("/runs/{run_id}/files/{relative_path:path}")
def get_deep_research_run_file(
    run_id: str,
    relative_path: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    db_log: Annotated[Session, Depends(get_db_log)],
    user: Annotated[User, Depends(verified_user)],
    download: bool = Query(default=False),
):
    """Serve a user-owned report or validated artifact with safe disposition."""

    run = _owned_run_or_404(db, run_id, user.id)
    normalized_path = str(relative_path or "").strip().lstrip("/")
    if not normalized_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found."
        )
    public_metadata_files = {
        run.final_report_path,
        run.final_html_path,
        run.manifest_path,
        "citations.json",
        "workspace.zip",
    }
    if normalized_path not in public_metadata_files and not normalized_path.startswith(
        "artifacts/"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )
    artifact = None
    if normalized_path.startswith("artifacts/"):
        artifact = next(
            (
                DeepResearchArtifact.from_dict(item)
                for item in run.artifacts or []
                if isinstance(item, dict)
                and item.get("relative_path") == normalized_path
                and item.get("validation_status") == "validated"
            ),
            None,
        )
        # Merely knowing a workspace path must never authorize its download.
        # Only the validated allowlist persisted on the owned run does so.
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found.",
            )
    try:
        path = materialize_deep_research_artifact(
            user.id,
            run.id,
            normalized_path,
            storage_provider=get_deep_research_run_storage_provider(run),
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found."
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path."
        )

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    inline_allowed = False
    if normalized_path == run.final_html_path:
        media_type = "text/html; charset=utf-8"
        inline_allowed = True
    elif normalized_path == run.final_report_path:
        media_type = "text/markdown; charset=utf-8"
        inline_allowed = True
    elif normalized_path.startswith("artifacts/"):
        inline_allowed = bool(artifact and artifact.media_type in _INLINE_IMAGE_TYPES)
        if artifact is not None:
            media_type = artifact.media_type

    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if normalized_path == run.final_html_path:
        headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )

    disposition = "attachment" if download or not inline_allowed else "inline"
    response = FileResponse(
        path=path,
        media_type=media_type,
        filename=Path(normalized_path).name or path.name,
        content_disposition_type=disposition,
        headers=headers,
    )
    if disposition == "attachment":
        if normalized_path == run.final_report_path:
            file_kind = "report_markdown"
        elif normalized_path == run.final_html_path:
            file_kind = "report_html"
        elif normalized_path == run.manifest_path:
            file_kind = "manifest"
        elif normalized_path == "citations.json":
            file_kind = "citations"
        elif normalized_path == "workspace.zip":
            file_kind = "workspace_archive"
        else:
            file_kind = "artifact"
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="DEEP_RESEARCH_FILE_DOWNLOADED",
            details={
                "run_id": run.id,
                "file_kind": file_kind,
                "explicit_download": bool(download),
                "disposition": "attachment",
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="deep_research",
        )
    return response
