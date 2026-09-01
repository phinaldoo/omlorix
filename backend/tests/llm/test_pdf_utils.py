from pathlib import Path

import pytest
from reportlab.pdfgen.canvas import Canvas

from app.llm import pdf_utils


def _create_pdf(
    path: Path,
    *,
    page_count: int = 1,
    width: float = 100,
    height: float = 100,
) -> None:
    document = Canvas(str(path), pagesize=(width, height), pageCompression=0)
    for page_number in range(1, page_count + 1):
        document.drawString(10, max(10, height - 20), f"Page {page_number}")
        document.showPage()
    document.save()


def test_render_pdf_pages_caps_unlimited_requests(tmp_path):
    pdf_path = tmp_path / "uploaded.pdf"
    _create_pdf(
        pdf_path,
        page_count=pdf_utils.DEFAULT_MAX_RENDERED_PDF_PAGES + 5,
    )

    rendered = pdf_utils.render_pdf_pages_to_png_bytes(pdf_path, max_pages=None)

    assert len(rendered) == pdf_utils.DEFAULT_MAX_RENDERED_PDF_PAGES
    assert all(page.startswith(b"\x89PNG\r\n\x1a\n") for page in rendered)


def test_render_pdf_pages_rejects_oversized_pages_before_rendering(tmp_path):
    pdf_path = tmp_path / "oversized-page.pdf"
    _create_pdf(
        pdf_path,
        width=pdf_utils.MAX_RENDERED_PDF_SIDE_PIXELS,
        height=100,
    )

    with pytest.raises(ValueError, match="too large"):
        pdf_utils.render_pdf_pages_to_png_bytes(pdf_path)


def test_render_pdf_pages_rejects_malformed_pdf(tmp_path):
    pdf_path = tmp_path / "malformed.pdf"
    pdf_path.write_bytes(b"not a PDF")

    with pytest.raises(ValueError, match="could not be opened"):
        pdf_utils.render_pdf_pages_to_png_bytes(pdf_path)
