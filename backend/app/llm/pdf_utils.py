from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

from app.files.pdfium import PdfiumUnavailableError, render_pdf_pages

DEFAULT_MAX_RENDERED_PDF_PAGES = 20
MAX_RENDERED_PDF_FILE_BYTES = 100 * 1024 * 1024
MAX_RENDERED_PDF_DOCUMENT_PAGES = 1000
MAX_RENDERED_PDF_SIDE_PIXELS = 4096
MAX_RENDERED_PDF_PIXELS = 16_000_000
MAX_RENDERED_PDF_PAGE_PNG_BYTES = 10 * 1024 * 1024
MAX_RENDERED_PDF_TOTAL_PNG_BYTES = 50 * 1024 * 1024
MAX_RENDERED_PDF_SECONDS = 10.0


def normalize_input_formats(input_formats_allowed: Sequence[str] | None) -> set[str]:
    if not input_formats_allowed:
        return set()
    normalized: set[str] = set()
    for item in input_formats_allowed:
        if item is None:
            continue
        text = str(item).strip().lower()
        if text:
            normalized.add(text)
    return normalized


def should_convert_pdf_to_images(input_formats_allowed: Sequence[str] | None) -> bool:
    formats = normalize_input_formats(input_formats_allowed)
    if not formats:
        return False
    return "image" in formats and "pdf" not in formats and "documents" not in formats


def _bounded_page_count(max_pages: int | None) -> int:
    if max_pages is not None and max_pages <= 0:
        return 0
    if max_pages is None:
        return DEFAULT_MAX_RENDERED_PDF_PAGES
    return min(max_pages, DEFAULT_MAX_RENDERED_PDF_PAGES)


def render_pdf_pages_to_png_bytes(
    file_path: str | Path,
    *,
    max_pages: int | None = None,
    zoom: float = 2.0,
) -> list[bytes]:
    page_limit = _bounded_page_count(max_pages)
    if page_limit <= 0:
        return []
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("PDF render zoom must be greater than zero.")

    try:
        return render_pdf_pages(
            Path(file_path),
            page_limit=page_limit,
            scale=zoom,
            max_file_bytes=MAX_RENDERED_PDF_FILE_BYTES,
            max_document_pages=MAX_RENDERED_PDF_DOCUMENT_PAGES,
            max_side_pixels=MAX_RENDERED_PDF_SIDE_PIXELS,
            max_pixels=MAX_RENDERED_PDF_PIXELS,
            max_page_png_bytes=MAX_RENDERED_PDF_PAGE_PNG_BYTES,
            max_total_png_bytes=MAX_RENDERED_PDF_TOTAL_PNG_BYTES,
            timeout_seconds=MAX_RENDERED_PDF_SECONDS,
        )
    except PdfiumUnavailableError as exc:
        raise RuntimeError("PDFium is required for PDF-to-image conversion.") from exc
