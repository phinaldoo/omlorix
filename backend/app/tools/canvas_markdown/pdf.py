from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Callable
from urllib.parse import unquote, urlparse, parse_qs

from bs4 import BeautifulSoup, Comment
from fastapi import HTTPException

from app.files.utils import materialize_file_record, resolve_accessible_file_record
from app.files.canvas_assets import (
    CanvasAssetAccessError,
    resolve_canvas_asset_for_read,
)
from app.tools.canvas_markdown.reportlab_pdf import (
    ReportLabPdfError,
    render_reportlab_pdf,
)


_OMLORIX_FILE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_OMLORIX_FILE_URL_RE = re.compile(
    r"omlorix-file://([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})", re.IGNORECASE
)
_STORY_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "kbd",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_STORY_DROP_TAGS = {
    "audio",
    "embed",
    "iframe",
    "object",
    "script",
    "source",
    "style",
    "video",
}
_STORY_LINK_SCHEMES = {"http", "https", "mailto", "tel"}

@dataclass(frozen=True)
class CanvasMarkdownPdfResult:
    """Rendered PDF bytes and a safe attachment filename for a Canvas document."""

    filename: str
    content: bytes


def _safe_pdf_filename(filename: str | None) -> str:
    """Return a download-safe PDF filename while preserving the user's title."""
    raw_name = (
        Path(str(filename or "canvas.pdf").replace("\x00", "")).name.strip()
        or "canvas.pdf"
    )
    raw_name = re.sub(r"[\r\n\t]+", " ", raw_name)
    raw_name = "".join("-" if char in '/\\:*?"<>|' else char for char in raw_name)
    raw_name = re.sub(r"\s+", " ", raw_name).strip(" .") or "canvas.pdf"
    if not raw_name.lower().endswith(".pdf"):
        raw_name = re.sub(
            r"\.(md|markdown|txt|html|htm)$", "", raw_name, flags=re.IGNORECASE
        )
    else:
        raw_name = raw_name[:-4].strip(" .") or "canvas"
    suffix = ".pdf"
    return f"{raw_name[: max(1, 255 - len(suffix))]}{suffix}"


def _file_id_from_reference(value: str | None) -> str:
    """Extract an Omlorix file id from markdown-rendered file URLs."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("omlorix-file://"):
        candidate = unquote(
            raw[len("omlorix-file://") :].split("?", 1)[0].split("#", 1)[0]
        )
        return candidate if _OMLORIX_FILE_ID_RE.fullmatch(candidate) else ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.path == "/api/v1/files/download":
        candidate = str((parse_qs(parsed.query).get("file_id") or [""])[0]).strip()
        return candidate if _OMLORIX_FILE_ID_RE.fullmatch(candidate) else ""
    return ""


def _markdown_to_html(markdown_text: str) -> str:
    """Render Markdown to HTML using the backend Markdown package when available."""
    try:
        import markdown

        return markdown.markdown(
            str(markdown_text or ""),
            extensions=["extra", "sane_lists", "nl2br"],
            output_format="html5",
        )
    except Exception:
        # Keep the endpoint available even if the optional renderer is absent.
        import html

        return "<pre>{}</pre>".format(html.escape(str(markdown_text or "")))


def _resolve_image_path(
    db,
    user_id: str,
    src: str | None,
    image_path_resolver: Callable[[str], Path | None] | None = None,
    canvas_record=None,
) -> Path | None:
    """Materialize an image only through an explicitly authorized resolver."""
    raw_src = str(src or "").strip()
    if image_path_resolver and raw_src:
        resolved_path = image_path_resolver(raw_src)
        if resolved_path is not None:
            candidate = Path(resolved_path)
            if candidate.exists() and candidate.is_file():
                return candidate

    file_id = _file_id_from_reference(raw_src)
    if not file_id:
        return None
    if canvas_record is not None:
        try:
            resolved = resolve_canvas_asset_for_read(
                db,
                canvas_record=canvas_record,
                actor_user_id=str(user_id),
                asset_file_id=file_id,
            )
        except CanvasAssetAccessError:
            return None
        file_record = resolved.record
        owner_user_id = resolved.storage_owner_user_id
    else:
        file_record, owner_user_id = resolve_accessible_file_record(
            db, str(user_id), file_id
        )
    if not file_record or not owner_user_id:
        return None
    file_type = str(getattr(file_record, "file_type", "") or "").lower()
    if not file_type.startswith("image/"):
        return None
    return materialize_file_record(file_record, owner_user_id)


def _reencoded_image_data_uri(image_source) -> str | None:
    """Return safe PNG data for a local path or bounded in-memory raster image."""

    from PIL import Image, ImageOps

    try:
        # Re-encoding strips active or external image payloads and prevents the
        # PDF renderer from receiving a filesystem path. It also normalizes
        # formats such as AVIF and WebP to one inert supported format.
        with Image.open(image_source) as source:
            source.seek(0)
            if source.width <= 0 or source.height <= 0:
                return None
            if source.width * source.height > 40_000_000:
                return None
            image = ImageOps.exif_transpose(source).copy()
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return None


def _image_data_uri(image_path: Path) -> str | None:
    """Re-encode one authorized local raster image for an isolated PDF story."""

    return _reencoded_image_data_uri(image_path)


def _bounded_table_span(value: object) -> str | None:
    """Keep harmless HTML table spans while rejecting malformed attributes."""

    if value is None:
        return None
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return str(normalized) if 1 <= normalized <= 100 else None


def _sanitize_story_html(
    html_text: str,
    *,
    db,
    user_id: str,
    image_path_resolver: Callable[[str], Path | None] | None,
    canvas_record=None,
) -> str:
    """Build isolated HTML with safe links and caller-authorized inline images."""

    soup = BeautifulSoup(
        _OMLORIX_FILE_URL_RE.sub(r"omlorix-file://\1", str(html_text or "")),
        "html.parser",
    )
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()

    # Drop active and resource-loading elements with their contents. Unknown
    # formatting containers are unwrapped later so their readable text remains.
    for tag in list(soup.find_all(True)):
        name = str(tag.name or "").lower()
        if name in _STORY_DROP_TAGS:
            tag.decompose()

    for tag in list(soup.find_all(True)):
        name = str(tag.name or "").lower()
        if name not in _STORY_ALLOWED_TAGS:
            tag.unwrap()
            continue

        original_attributes = dict(tag.attrs)
        tag.attrs = {}
        if name == "a":
            href = str(original_attributes.get("href") or "").strip()
            parsed = urlparse(href)
            if parsed.scheme.lower() in _STORY_LINK_SCHEMES:
                tag["href"] = href
        elif name == "img":
            src = str(original_attributes.get("src") or "").strip()
            image_path = _resolve_image_path(
                db,
                user_id,
                src,
                image_path_resolver,
                canvas_record,
            )
            data_uri = _image_data_uri(image_path) if image_path else None
            if not data_uri:
                # Preserve useful alt text without leaving any URL for the PDF
                # renderer to resolve. This makes failed images visible but inert.
                tag.replace_with(
                    str(original_attributes.get("alt") or "[Image unavailable]")
                )
                continue
            tag["src"] = data_uri
            tag["alt"] = str(original_attributes.get("alt") or "")[:1_000]
        elif name == "ol":
            start = _bounded_table_span(original_attributes.get("start"))
            if start:
                tag["start"] = start
        elif name in {"td", "th"}:
            for attribute in ("colspan", "rowspan"):
                span = _bounded_table_span(original_attributes.get(attribute))
                if span:
                    tag[attribute] = span

    return str(soup)


def _render_story_pdf(
    *,
    story_html: str,
    filename: str | None,
) -> CanvasMarkdownPdfResult:
    """Lay out inert HTML inside fixed A4 pages with 18 mm print margins."""

    try:
        content = render_reportlab_pdf(
            story_html=story_html,
            title=Path(str(filename or "Canvas")).stem,
        )
    except ReportLabPdfError as exc:
        raise HTTPException(
            status_code=500, detail="The PDF could not be rendered."
        ) from exc

    return CanvasMarkdownPdfResult(
        filename=_safe_pdf_filename(filename),
        content=content,
    )


def render_canvas_markdown_pdf(
    db,
    *,
    user_id: str,
    markdown_text: str,
    filename: str | None = None,
    source_file_id: str | None = None,
    image_path_resolver: Callable[[str], Path | None] | None = None,
) -> CanvasMarkdownPdfResult:
    """Render Markdown to PDF with caller-authorized image materialization."""
    source_record = None
    if source_file_id:
        file_record, _owner_user_id = resolve_accessible_file_record(
            db, str(user_id), source_file_id
        )
        if not file_record:
            raise HTTPException(status_code=404, detail="Canvas file not found")
        meta = file_record.meta if isinstance(file_record.meta, dict) else {}
        canvas_type = str(meta.get("canvas_type") or "").lower().strip()
        file_type = str(getattr(file_record, "file_type", "") or "").lower().strip()
        is_markdown_canvas = canvas_type == "markdown"
        is_markdown_file = not canvas_type and file_type in {
            "text/markdown",
            "text/x-markdown",
            "text/plain",
        }
        if not (is_markdown_canvas or is_markdown_file):
            raise HTTPException(
                status_code=400,
                detail="Only Markdown canvas files can be exported as PDF",
            )
        source_record = file_record

    story_html = _sanitize_story_html(
        _markdown_to_html(markdown_text),
        db=db,
        user_id=str(user_id),
        image_path_resolver=image_path_resolver,
        canvas_record=source_record,
    )
    return _render_story_pdf(
        story_html=story_html,
        filename=filename,
    )
