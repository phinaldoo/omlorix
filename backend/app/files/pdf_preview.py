"""Safe, authenticated PDF page data for the selectable file preview.

The browser's built-in PDF viewer lives in an isolated extension context, so
the application cannot provide a selectable in-app text layer. This module
uses a process-isolated PDFium worker for inert page images and positioned
words; the frontend combines both into an app-owned selectable page surface.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.files.utils import (
    materialize_file_record,
    normalize_file_mime_type,
    resolve_accessible_file_record,
)
from app.files.pdfium import (
    PdfiumPageNotFoundError,
    PdfiumPasswordError,
    PdfiumProcessingError,
    PdfiumUnavailableError,
    extract_pdf_page_words,
    inspect_pdf_document,
    render_pdf_page,
)


PDF_PREVIEW_MAX_FILE_BYTES = 100 * 1024 * 1024
PDF_PREVIEW_MAX_PAGES = 1000
PDF_PREVIEW_MAX_WORDS_PER_PAGE = 50_000
PDF_PREVIEW_MAX_TEXT_CHARS_PER_PAGE = 2 * 1024 * 1024
PDF_PREVIEW_RENDER_SCALE = 2.0
PDF_PREVIEW_MAX_SIDE_PIXELS = 4096
PDF_PREVIEW_MAX_PIXELS = 16_000_000
PDF_PREVIEW_MAX_PNG_BYTES = 10 * 1024 * 1024


class PdfPreviewError(ValueError):
    """Raised when a file cannot be represented by the selectable preview."""


def _is_pdf_record(file_record) -> bool:
    """Accept canonical PDF metadata and generic records with a PDF filename."""
    mime_type = normalize_file_mime_type(getattr(file_record, "file_type", ""))
    meta = file_record.meta if isinstance(getattr(file_record, "meta", None), dict) else {}
    file_name = str(meta.get("original_filename") or file_record.file_name or "").lower()
    return mime_type == "application/pdf" or file_name.endswith(".pdf")


def resolve_pdf_preview_path(
    db: Session,
    *,
    user_id: str,
    file_id: str,
) -> tuple[object, Path]:
    """Resolve an accessible PDF and materialize cloud-backed storage safely."""
    file_record, owner_user_id = resolve_pdf_preview_record(
        db,
        user_id=user_id,
        file_id=file_id,
    )

    file_path = materialize_file_record(file_record, owner_user_id)
    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=404, detail="PDF file not found") from exc
    if file_size <= 0:
        raise PdfPreviewError("PDF file is empty.")
    if file_size > PDF_PREVIEW_MAX_FILE_BYTES:
        raise PdfPreviewError("PDF file is too large to preview.")
    return file_record, file_path


def resolve_pdf_preview_record(
    db: Session,
    *,
    user_id: str,
    file_id: str,
) -> tuple[object, str]:
    """Authorize a PDF without materializing it on the API process."""
    normalized_file_id = str(file_id or "").strip()
    file_record, owner_user_id = resolve_accessible_file_record(
        db,
        str(user_id),
        normalized_file_id,
    )
    if not file_record or not owner_user_id or not _is_pdf_record(file_record):
        raise HTTPException(status_code=404, detail="PDF file not found")
    file_size = int(getattr(file_record, "file_size", 0) or 0)
    if file_size <= 0:
        raise PdfPreviewError("PDF file is empty.")
    if file_size > PDF_PREVIEW_MAX_FILE_BYTES:
        raise PdfPreviewError("PDF file is too large to preview.")
    return file_record, str(owner_user_id)


def _preview_error(exc: Exception) -> PdfPreviewError:
    """Normalize worker failures without exposing parser or process details."""

    if isinstance(exc, PdfiumUnavailableError):
        return PdfPreviewError("PDF preview support is unavailable.")
    if isinstance(exc, PdfiumPasswordError):
        return PdfPreviewError("Password-protected PDF files cannot be previewed.")
    message = str(exc)
    message = message.replace(" to process", " to preview")
    message = message.replace(" processing", " preview")
    return PdfPreviewError(message or "PDF file could not be previewed.")


def inspect_pdf_preview_document(file_path: Path) -> dict:
    """Return bounded page dimensions for constructing lazy page surfaces."""
    try:
        return inspect_pdf_document(
            file_path,
            max_file_bytes=PDF_PREVIEW_MAX_FILE_BYTES,
            max_pages=PDF_PREVIEW_MAX_PAGES,
        )
    except (PdfiumProcessingError, PdfiumUnavailableError) as exc:
        raise _preview_error(exc) from exc


def extract_pdf_preview_page(file_path: Path, page_number: int) -> dict:
    """Return positioned words for one 1-based PDF page."""
    try:
        return extract_pdf_page_words(
            file_path,
            page_number=page_number,
            max_file_bytes=PDF_PREVIEW_MAX_FILE_BYTES,
            max_pages=PDF_PREVIEW_MAX_PAGES,
            max_words=PDF_PREVIEW_MAX_WORDS_PER_PAGE,
            max_text_chars=PDF_PREVIEW_MAX_TEXT_CHARS_PER_PAGE,
        )
    except PdfiumPageNotFoundError as exc:
        raise HTTPException(status_code=404, detail="PDF page not found") from exc
    except (PdfiumProcessingError, PdfiumUnavailableError) as exc:
        raise _preview_error(exc) from exc


def render_pdf_preview_page_png(file_path: Path, page_number: int) -> bytes:
    """Render one bounded PDF page to an inert PNG background."""
    try:
        return render_pdf_page(
            file_path,
            page_number=page_number,
            scale=PDF_PREVIEW_RENDER_SCALE,
            max_file_bytes=PDF_PREVIEW_MAX_FILE_BYTES,
            max_document_pages=PDF_PREVIEW_MAX_PAGES,
            max_side_pixels=PDF_PREVIEW_MAX_SIDE_PIXELS,
            max_pixels=PDF_PREVIEW_MAX_PIXELS,
            max_page_png_bytes=PDF_PREVIEW_MAX_PNG_BYTES,
        )
    except PdfiumPageNotFoundError as exc:
        raise HTTPException(status_code=404, detail="PDF page not found") from exc
    except (PdfiumProcessingError, PdfiumUnavailableError) as exc:
        raise _preview_error(exc) from exc
