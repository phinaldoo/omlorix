from io import BytesIO
import inspect
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from PIL import Image
import pypdfium2 as pdfium
import pytest
from fastapi import HTTPException, Response
from reportlab.pdfgen.canvas import Canvas
from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard


from app.files import pdf_preview  # noqa: E402
from app.files import router as files_router  # noqa: E402


def _create_pdf(path: Path) -> None:
    """Create a deterministic two-page PDF with selectable source text."""
    document = Canvas(str(path), pagesize=(300, 400), pageCompression=0)
    document.drawString(40, 330, "Selectable PDF text")
    document.showPage()
    document.setPageSize((400, 300))
    document.drawString(50, 220, "Second page reference")
    document.showPage()
    document.save()


def _create_rotated_pdf(path: Path) -> None:
    """Create a visible page with a standard PDF /Rotate entry."""
    source_path = path.with_name(f"{path.stem}-source.pdf")
    source = Canvas(str(source_path), pagesize=(300, 400), pageCompression=0)
    source.drawString(40, 330, "Rotated selectable text")
    source.showPage()
    source.save()

    document = pdfium.PdfDocument(source_path)
    try:
        page = document[0]
        try:
            page.set_rotation(90)
        finally:
            page.close()
        document.save(path)
    finally:
        document.close()


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/files/pdf/preview",
        "headers": [(b"user-agent", b"pytest")],
        "client": ("203.0.113.10", 12345),
    })


def test_pdf_preview_extracts_page_geometry_text_and_png(tmp_path):
    pdf_path = tmp_path / "selectable.pdf"
    _create_pdf(pdf_path)

    document = pdf_preview.inspect_pdf_preview_document(pdf_path)
    page = pdf_preview.extract_pdf_preview_page(pdf_path, 1)
    image = pdf_preview.render_pdf_preview_page_png(pdf_path, 2)

    assert document == {
        "page_count": 2,
        "pages": [
            {"page": 1, "width": 300.0, "height": 400.0},
            {"page": 2, "width": 400.0, "height": 300.0},
        ],
    }
    assert " ".join(word["text"] for word in page["words"]) == "Selectable PDF text"
    assert all(word["width"] > 0 and word["height"] > 0 for word in page["words"])
    assert image.startswith(b"\x89PNG\r\n\x1a\n")


def test_pdf_preview_keeps_rotated_metadata_text_and_png_geometry_aligned(tmp_path):
    """Metadata and selectable boxes must occupy the rendered page bounds."""
    pdf_path = tmp_path / "rotated.pdf"
    _create_rotated_pdf(pdf_path)

    document = pdf_preview.inspect_pdf_preview_document(pdf_path)
    page = pdf_preview.extract_pdf_preview_page(pdf_path, 1)
    image = pdf_preview.render_pdf_preview_page_png(pdf_path, 1)

    assert document["pages"] == [{"page": 1, "width": 400.0, "height": 300.0}]
    assert page["width"] == 400.0
    assert page["height"] == 300.0
    assert " ".join(word["text"] for word in page["words"]) == "Rotated selectable text"
    with Image.open(BytesIO(image)) as rendered:
        assert rendered.size == (800, 600)

    # PDFium exposes raw character boxes before /Rotate, so the selectable
    # layer still needs the worker's box transform even though get_size() and
    # render() already expose the rotated page dimensions.
    assert all(word["x"] > 300 for word in page["words"])
    assert all(word["height"] > word["width"] for word in page["words"])
    assert all(
        0 <= word["x"] < word["x"] + word["width"] <= page["width"]
        and 0 <= word["y"] < word["y"] + word["height"] <= page["height"]
        for word in page["words"]
    )


def test_pdf_preview_resolver_enforces_file_access_and_supports_generic_pdf_metadata(tmp_path):
    pdf_path = tmp_path / "stored.bin"
    _create_pdf(pdf_path)
    file_record = SimpleNamespace(
        id="pdf-1",
        user_id="owner-1",
        file_type="application/octet-stream",
        file_name="stored.bin",
        file_size=pdf_path.stat().st_size,
        meta={"original_filename": "Report.PDF"},
    )

    with patch.object(
        pdf_preview,
        "resolve_accessible_file_record",
        return_value=(file_record, "owner-1"),
    ) as mock_resolve, patch.object(
        pdf_preview,
        "materialize_file_record",
        return_value=pdf_path,
    ):
        resolved_record, resolved_path = pdf_preview.resolve_pdf_preview_path(
            MagicMock(),
            user_id="viewer-1",
            file_id="pdf-1",
        )

    assert resolved_record is file_record
    assert resolved_path == pdf_path
    mock_resolve.assert_called_once_with(ANY, "viewer-1", "pdf-1")

    with patch.object(pdf_preview, "resolve_accessible_file_record", return_value=(None, None)):
        with pytest.raises(HTTPException) as exc_info:
            pdf_preview.resolve_pdf_preview_path(
                MagicMock(),
                user_id="viewer-2",
                file_id="private-pdf",
            )
    assert exc_info.value.status_code == 404


def test_pdf_preview_metadata_route_is_no_store_and_audited(tmp_path):
    pdf_path = tmp_path / "route.pdf"
    _create_pdf(pdf_path)
    file_record = SimpleNamespace(id="pdf-route")
    response = Response()
    db_log = MagicMock()

    with patch.object(
        files_router,
        "resolve_pdf_preview_path",
        return_value=(file_record, pdf_path),
    ), patch.object(
        files_router,
        "_audit_file_event",
    ) as mock_audit:
        payload = files_router.get_pdf_preview_document_route(
            request=_request(),
            response=response,
            file_id="pdf-route",
            user=SimpleNamespace(id="viewer-1"),
            db=MagicMock(),
            db_log=db_log,
        )

    assert payload["page_count"] == 2
    assert response.headers["Cache-Control"] == "no-store, private"
    mock_audit.assert_called_once()
    assert mock_audit.call_args.args[3] == "FILE_PREVIEWED"


def test_pdf_preview_page_image_route_returns_inert_png(tmp_path):
    pdf_path = tmp_path / "image.pdf"
    _create_pdf(pdf_path)

    with patch.object(
        files_router,
        "resolve_pdf_preview_path",
        return_value=(SimpleNamespace(id="pdf-image"), pdf_path),
    ):
        response = files_router.get_pdf_preview_page_image_route(
            file_id="pdf-image",
            page=1,
            user=SimpleNamespace(id="viewer-1"),
            db=MagicMock(),
        )

    assert response.media_type == "image/png"
    assert response.body.startswith(b"\x89PNG\r\n\x1a\n")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store, private"


def test_pdf_preview_routes_bound_page_before_enqueuing_work():
    for route in (
        files_router.get_pdf_preview_page_route,
        files_router.get_pdf_preview_page_image_route,
    ):
        query = inspect.signature(route).parameters["page"].default
        assert any(
            getattr(constraint, "le", None) == pdf_preview.PDF_PREVIEW_MAX_PAGES
            for constraint in query.metadata
        )
