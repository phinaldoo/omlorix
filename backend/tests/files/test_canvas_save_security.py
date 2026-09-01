from __future__ import annotations

import asyncio
import io
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import types
import zipfile

from fastapi import HTTPException, Request, UploadFile
import pypdfium2 as pdfium
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", types.ModuleType("zstandard"))

from app.database import Base  # noqa: E402
from app.file_folders.models import (  # noqa: E402
    FileFolders,
    ShareType,
    SharedFileFolderSubscription,
    create_folder_share,
)
from app.files.access import ResolvedFileAccess, accessible_files_query  # noqa: E402
from app.files.canvas_assets import CanvasAssetAccessError  # noqa: E402
from app.files.models import Files  # noqa: E402
from app.files import router as files_router  # noqa: E402
from app.files import utils as file_utils  # noqa: E402
from app.files.schemas import (  # noqa: E402
    CanvasAssetDecisionRequest,
    CanvasFileSaveResponse,
    CanvasFileSaveRequest,
    CanvasMarkdownPdfRequest,
)
from app.chats import utils as chat_utils  # noqa: E402
from app.tools.common import should_hide_tool_call_from_user, tools_not_yield_arguments  # noqa: E402
from app.tools import helper as tool_helper  # noqa: E402
from app.tools.schemas import tool_schemas  # noqa: E402
from app.tools.canvas_markdown import utils as canvas_utils  # noqa: E402
from app.tools.canvas_markdown import pdf as canvas_pdf  # noqa: E402


def _inspect_pdf(content: bytes) -> tuple[int, str, list[tuple], list[str]]:
    """Inspect trusted test output through the replacement PDFium API."""
    document = pdfium.PdfDocument(content)
    text_parts: list[str] = []
    image_bounds: list[tuple] = []
    embedded_font_names: list[str] = []
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                text_parts.append(text_page.get_text_range())
                for page_object in page.get_objects():
                    if isinstance(page_object, pdfium.PdfImage):
                        image_bounds.append((page_object.get_bounds(), page.get_size()))
                    elif isinstance(page_object, pdfium.PdfTextObj):
                        font = page_object.get_font()
                        try:
                            if font.is_embedded:
                                embedded_font_names.append(font.get_base_name())
                        finally:
                            font.close()
            finally:
                text_page.close()
                page.close()
        return len(document), "\n".join(text_parts), image_bounds, embedded_font_names
    finally:
        document.close()


def test_canvas_save_preserves_active_html_as_attachment_source():
    """Interactive source is stored inertly and executed only by the preview proxy."""
    canvas_utils._validate_canvas_content_bytes(
        b"<!doctype html><html><body><script>alert(1)</script></body></html>",
        file_type="text/html",
    )


def test_canvas_save_allows_static_html_content():
    canvas_utils._validate_canvas_content_bytes(
        b"<!doctype html><html><head><style>body { color: #111; }</style></head><body><main>Hello</main></body></html>",
        file_type="text/html",
    )


def test_canvas_user_attachment_save_allows_active_html_source():
    """Authenticated edits retain active markup as inert attachment source."""
    canvas_utils._validate_canvas_content_bytes(
        b"<!doctype html><html><body><script>alert(1)</script></body></html>",
        file_type="text/html",
        allow_html_attachment=True,
    )


@pytest.mark.parametrize(
    ("mime_type", "file_name"),
    (
        ("application/html", "page.html"),
        ("application/xhtml+xml", "page.xhtml"),
        ("application/x-html", "page.shtm"),
        ("text/xhtml", "page.xht"),
        ("application/octet-stream", "page.xhtm"),
    ),
)
def test_canvas_recognizes_html_attachment_aliases(mime_type, file_name):
    """Frontend-advertised HTML aliases remain editable on the save path."""
    file_record = SimpleNamespace(
        file_type=mime_type,
        file_name="stored.bin",
        meta={"original_filename": file_name},
    )

    assert canvas_utils._content_type_from_file_record(file_record) == "html"


def test_canvas_save_allows_same_document_svg_url_references(monkeypatch, tmp_path):
    """Inline SVG markers may reference an ID without loading a resource."""

    monkeypatch.setattr(canvas_utils, "TEMP_DIR", tmp_path)

    canvas_utils._validate_canvas_content_bytes(
        b"""<!doctype html>
        <html><head><style>
        .arrow { marker-end: url(#arrow); }
        .clipped { clip-path: url(\"#clip-region\"); }
        </style></head><body>
        <svg viewBox="0 0 100 100">
          <defs>
            <marker id="arrow"><path d="M0 0 L10 5 L0 10 Z"></path></marker>
            <clipPath id="clip-region"><rect width="100" height="100"></rect></clipPath>
          </defs>
          <path class="arrow clipped" d="M0 0 L100 100"></path>
        </svg>
        </body></html>""",
        file_type="text/html",
    )


@pytest.mark.parametrize(
    "css",
    (
        ".hero { background-image: url(https://example.invalid/hero.png); }",
        ".hero { background-image: url('data:image/png;base64,AAAA'); }",
        ".hero { background-image: url(//example.invalid/hero.png); }",
        ".hero { background-image: u\\72l(https://example.invalid/escaped.png); }",
    ),
)
def test_canvas_save_preserves_css_urls_for_permission_gated_preview(css):
    """CSS stays intact at rest; preview CSP enforces the viewer's network grant."""
    canvas_utils._validate_canvas_content_bytes(
        f"<!doctype html><html><head><style>{css}</style></head><body></body></html>".encode(),
        file_type="text/html",
    )


@pytest.mark.parametrize(
    "body",
    (
        "<main style=\"background:url('https://example.invalid/inline.png')\">Text</main>",
        '<svg><path marker-end="url(https://example.invalid/marker.svg#arrow)"></path></svg>',
        "<style>.hero { --image: url(https://example.invalid/custom.png); background: var(--image); }</style>",
    ),
)
def test_canvas_save_preserves_urls_in_every_css_context(body):
    """Inline, SVG, and nested CSS are preserved for the isolated preview."""
    canvas_utils._validate_canvas_content_bytes(
        f"<!doctype html><html><body>{body}</body></html>".encode(),
        file_type="text/html",
    )


def test_canvas_save_preserves_unresolved_svg_fragment():
    """Editing can temporarily leave unresolved SVG references in valid source."""
    canvas_utils._validate_canvas_content_bytes(
        b"<!doctype html><html><body><svg><path style='marker-end:url(#missing)'></path></svg></body></html>",
        file_type="text/html",
    )


def test_canvas_save_applies_size_limit(monkeypatch):
    monkeypatch.setattr(canvas_utils, "MAX_FILE_SIZE", 4)

    with pytest.raises(ValueError, match="exceeds maximum"):
        canvas_utils._validate_canvas_content_bytes(b"12345", file_type="text/markdown")


def test_canvas_save_allows_canvas_safe_text_types():
    canvas_utils._validate_canvas_content_bytes(
        b"graph TD; A-->B", file_type="text/x-mermaid"
    )


def test_canvas_markdown_pdf_renders_markdown_and_file_image(monkeypatch, tmp_path):
    image_path = tmp_path / "chart.png"
    from PIL import Image

    Image.new("RGB", (24, 16), (12, 90, 160)).save(image_path)

    def fake_resolve_accessible_file_record(db, user_id, file_id):
        if file_id == "source-1":
            return SimpleNamespace(
                id="source-1",
                file_type="text/markdown",
                meta={"canvas_type": "markdown"},
            ), "owner-1"
        if file_id == "image-1":
            return SimpleNamespace(
                id="image-1", file_type="image/png", meta={}
            ), "owner-1"
        return None, None

    monkeypatch.setattr(
        canvas_pdf,
        "resolve_accessible_file_record",
        fake_resolve_accessible_file_record,
    )
    monkeypatch.setattr(
        canvas_pdf,
        "resolve_canvas_asset_for_read",
        lambda db, **kwargs: SimpleNamespace(
            record=SimpleNamespace(id="image-1", file_type="image/png", meta={}),
            storage_owner_user_id="owner-1",
        ),
    )
    monkeypatch.setattr(
        canvas_pdf,
        "materialize_file_record",
        lambda file_record, owner_user_id: image_path,
    )

    result = canvas_pdf.render_canvas_markdown_pdf(
        object(),
        user_id="user-1",
        source_file_id="source-1",
        filename="report.md",
        markdown_text="# Report\n\n![Chart](omlorix-file://image-1)\n\n| A | B |\n| - | - |\n| 1 | 2 |\n",
    )

    assert result.filename == "report.pdf"
    assert result.content.startswith(b"%PDF")

    page_count, pdf_text, image_bounds, _font_names = _inspect_pdf(result.content)
    assert page_count == 1
    assert "Report" in pdf_text
    assert image_bounds


def test_canvas_markdown_pdf_splits_long_blockquotes_across_pages():
    quote = "\n".join(f"> Quoted line {index}  " for index in range(180))

    result = canvas_pdf.render_canvas_markdown_pdf(
        object(),
        user_id="user-1",
        markdown_text=quote,
    )

    page_count, pdf_text, _image_bounds, _font_names = _inspect_pdf(result.content)
    assert page_count > 1
    assert "Quoted line 0" in pdf_text
    assert "Quoted line 179" in pdf_text


def test_canvas_markdown_pdf_preserves_fenced_code_whitespace():
    result = canvas_pdf.render_canvas_markdown_pdf(
        object(),
        user_id="user-1",
        markdown_text="```python\n    if x:\n\treturn  1\ndone\n```",
    )

    document = pdfium.PdfDocument(result.content)
    page = document[0]
    text_page = page.get_textpage()
    try:
        extracted = text_page.get_text_range()
        if_index = extracted.index("if x:")
        return_index = extracted.index("return 1")
        done_index = extracted.index("done")

        def left(character_index: int) -> float:
            return float(text_page.get_charbox(character_index, loose=True)[0])

        character_advance = left(if_index + 1) - left(if_index)
        assert left(if_index) - left(done_index) == pytest.approx(
            4 * character_advance,
            abs=0.25,
        )
        assert left(return_index) - left(done_index) == pytest.approx(
            4 * character_advance,
            abs=0.25,
        )
        one_index = return_index + len("return ")
        assert left(one_index) - left(return_index) == pytest.approx(
            8 * character_advance,
            abs=0.25,
        )
    finally:
        text_page.close()
        page.close()
        document.close()


def test_canvas_markdown_pdf_embeds_images_from_authorized_resolver(tmp_path):
    """Deep Research can reuse the renderer without weakening file access checks."""

    image_path = tmp_path / "research-chart.png"
    from PIL import Image

    Image.new("RGB", (24, 16), (12, 90, 160)).save(image_path)
    resolved_sources = []

    def resolve_image(src):
        resolved_sources.append(src)
        return image_path if src == "artifacts/research-chart.png" else None

    result = canvas_pdf.render_canvas_markdown_pdf(
        object(),
        user_id="user-1",
        filename="research-report.pdf",
        markdown_text="# Research\n\n![Chart](artifacts/research-chart.png)",
        image_path_resolver=resolve_image,
    )

    assert resolved_sources == ["artifacts/research-chart.png"]
    assert result.content.startswith(b"%PDF")

    _page_count, _pdf_text, image_bounds, _font_names = _inspect_pdf(result.content)
    assert image_bounds


def test_canvas_markdown_pdf_preserves_multilingual_and_rtl_text():
    """PDF fallback fonts must cover every writing system supported by the UI."""

    result = canvas_pdf.render_canvas_markdown_pdf(
        object(),
        user_id="user-1",
        filename="multilingual.pdf",
        markdown_text=(
            "# Résumé · Отчёт · تقرير · रिपोर्ट · レポート · 中文测试\n\n"
            "Café naïve — Проверенные доказательства. "
            "أدلة تم التحقق منها. सत्यापित साक्ष्य। 検証済みの証拠。"
        ),
    )

    _page_count, extracted, _image_bounds, font_names = _inspect_pdf(result.content)

    # Complex scripts are shaped for visual output. PDFium may expose their
    # glyph-oriented text representation, so exact extraction is asserted for
    # scripts whose ToUnicode mapping remains one-to-one.
    assert "Résumé" in extracted
    assert "Отчёт" in extracted
    assert "レポート" in extracted
    assert "中文测试" in extracted
    assert "■" not in extracted
    assert "�" not in extracted
    assert font_names
    assert any("NotoSansSC" in name for name in font_names)
    assert len(result.content) < 500_000


def test_canvas_markdown_pdf_drops_active_and_remote_html():
    """Untrusted Markdown cannot make the PDF renderer load external content."""

    result = canvas_pdf.render_canvas_markdown_pdf(
        object(),
        user_id="user-1",
        markdown_text=(
            "<script>active payload</script>\n\n"
            "![Remote evidence](https://example.invalid/evidence.png)"
        ),
    )

    _page_count, pdf_text, image_bounds, _font_names = _inspect_pdf(result.content)
    assert "active payload" not in pdf_text
    assert "Remote evidence" in pdf_text
    assert not image_bounds


def test_canvas_markdown_pdf_rejects_non_markdown_source(monkeypatch):
    monkeypatch.setattr(
        canvas_pdf,
        "resolve_accessible_file_record",
        lambda db, user_id, file_id: (
            SimpleNamespace(
                id=file_id, file_type="text/html", meta={"canvas_type": "html"}
            ),
            "owner-1",
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        canvas_pdf.render_canvas_markdown_pdf(
            object(),
            user_id="user-1",
            source_file_id="html-1",
            filename="site.html",
            markdown_text="# Not allowed",
        )

    assert exc_info.value.status_code == 400


def test_canvas_markdown_pdf_request_rejects_oversized_markdown():
    with pytest.raises(ValidationError):
        CanvasMarkdownPdfRequest(markdown="x" * ((2 * 1024 * 1024) + 1))


def test_canvas_markdown_pdf_filename_preserves_extension_and_strips_backslash():
    long_name = f"folder\\{'a' * 300}.pdf"
    safe_name = canvas_pdf._safe_pdf_filename(long_name)

    assert safe_name.endswith(".pdf")
    assert len(safe_name) == 255
    assert "\\" not in safe_name


def test_canvas_markdown_pdf_image_scaling_bounds_height(tmp_path):
    image_path = tmp_path / "tall.png"
    from PIL import Image

    Image.new("RGB", (20, 2000), (12, 90, 160)).save(image_path)

    result = canvas_pdf.render_canvas_markdown_pdf(
        object(),
        user_id="user-1",
        markdown_text="![Tall chart](artifacts/tall.png)",
        image_path_resolver=lambda _src: image_path,
    )

    _page_count, _pdf_text, image_bounds, _font_names = _inspect_pdf(result.content)
    image_rect, page_size = image_bounds[0]
    image_height = image_rect[3] - image_rect[1]
    assert image_height <= page_size[1] - 102


def test_canvas_markdown_pdf_route_uses_unicode_safe_attachment_header(monkeypatch):
    monkeypatch.setattr(
        files_router,
        "render_canvas_markdown_pdf",
        lambda *args, **kwargs: SimpleNamespace(
            filename="Résumé 📄.pdf", content=b"%PDF-test"
        ),
    )
    monkeypatch.setattr(files_router, "_audit_file_event", lambda *args, **kwargs: None)

    response = files_router.render_canvas_markdown_pdf_route(
        payload=CanvasMarkdownPdfRequest(markdown="# Hi", filename="Résumé 📄.pdf"),
        request=SimpleNamespace(headers={}),
        user=SimpleNamespace(id="user-1"),
        db=object(),
        db_log=object(),
    )

    disposition = response.headers["content-disposition"]
    assert 'filename="Resume.pdf"' in disposition
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9%20%F0%9F%93%84.pdf" in disposition


def test_canvas_save_enforces_configured_upload_size(monkeypatch):
    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )

    def reject_large_canvas(db, user_id, file_size):
        raise HTTPException(status_code=413, detail="File size exceeds limit of 1 MB")

    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", reject_large_canvas
    )

    with pytest.raises(HTTPException) as exc_info:
        canvas_utils.save_canvas_markdown(
            object(),
            user_id="user-1",
            content="hello",
            content_type="markdown",
        )

    assert exc_info.value.status_code == 413


def test_canvas_save_new_file_enforces_user_capacity(monkeypatch):
    capacity_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 100)
    )

    def fake_capacity(
        db,
        user_id,
        file_size,
        *,
        max_files_limit,
        max_user_storage_limit_bytes,
        existing_file_id=None,
    ):
        capacity_calls.append(
            {
                "user_id": user_id,
                "file_size": file_size,
                "max_files_limit": max_files_limit,
                "max_user_storage_limit_bytes": max_user_storage_limit_bytes,
                "existing_file_id": existing_file_id,
            }
        )

    def fake_persist_generated_file_bytes(**kwargs):
        return SimpleNamespace(id=kwargs["file_id"], file_name=kwargs["file_name"])

    monkeypatch.setattr(canvas_utils, "ensure_user_file_upload_capacity", fake_capacity)
    monkeypatch.setattr(
        canvas_utils, "persist_generated_file_bytes", fake_persist_generated_file_bytes
    )

    result = canvas_utils.save_canvas_markdown(
        object(),
        user_id="user-1",
        content="hello",
        content_type="markdown",
    )

    assert result["created"] is True
    assert capacity_calls == [
        {
            "user_id": "user-1",
            "file_size": 5,
            "max_files_limit": 3,
            "max_user_storage_limit_bytes": 100,
            "existing_file_id": None,
        }
    ]


def test_canvas_save_serializes_system_folder_creation_with_file_admission(monkeypatch):
    """The first-folder lookup and insert must run under the per-user lock."""

    admission_active = False
    observed_steps: list[str] = []

    @contextmanager
    def fake_serialized_admission(db, user_id):
        nonlocal admission_active
        assert user_id == "user-1"
        admission_active = True
        observed_steps.append("lock_entered")
        try:
            yield
        finally:
            admission_active = False
            observed_steps.append("lock_exited")

    def fake_capacity(*args, **kwargs):
        assert admission_active is True
        observed_steps.append("capacity_checked")

    def fake_ensure_folder(db, user_id):
        assert admission_active is True
        observed_steps.append("folder_ensured")
        return "canvas-folder-1"

    def fake_persist(**kwargs):
        assert admission_active is True
        assert "max_files_limit" not in kwargs
        assert "max_user_storage_limit_bytes" not in kwargs
        observed_steps.append("file_persisted")
        return SimpleNamespace(id=kwargs["file_id"], file_name=kwargs["file_name"])

    db = SimpleNamespace(rollback=lambda: observed_steps.append("rolled_back"))
    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 100)
    )
    monkeypatch.setattr(
        canvas_utils, "serialized_user_file_quota_admission", fake_serialized_admission
    )
    monkeypatch.setattr(canvas_utils, "ensure_user_file_upload_capacity", fake_capacity)
    monkeypatch.setattr(canvas_utils, "_ensure_canvas_folder_id", fake_ensure_folder)
    monkeypatch.setattr(canvas_utils, "persist_generated_file_bytes", fake_persist)

    result = canvas_utils.save_canvas_markdown(
        db,
        user_id="user-1",
        content="hello",
        content_type="markdown",
    )

    assert result["created"] is True
    assert observed_steps == [
        "lock_entered",
        "capacity_checked",
        "folder_ensured",
        "file_persisted",
        "lock_exited",
    ]


def test_canvas_save_new_file_ignores_blank_snippet_fields(monkeypatch):
    persisted: dict[str, object] = {}

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 100)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )

    def fake_persist_generated_file_bytes(**kwargs):
        persisted.update(kwargs)
        return SimpleNamespace(id=kwargs["file_id"], file_name=kwargs["file_name"])

    monkeypatch.setattr(
        canvas_utils, "persist_generated_file_bytes", fake_persist_generated_file_bytes
    )

    result = canvas_utils.save_canvas_markdown(
        object(),
        user_id="user-1",
        content="<main>Hello</main>",
        content_type="html",
        start_snippet="",
        end_snippet="   ",
    )

    assert result["created"] is True
    assert result["content"] == "<main>Hello</main>"
    assert persisted["file_bytes"] == b"<main>Hello</main>"


def test_canvas_folder_helper_creates_and_reuses_canvas_folder():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[FileFolders.__table__, SharedFileFolderSubscription.__table__],
    )
    db = sessionmaker(bind=engine)()
    try:
        folder_id = canvas_utils._ensure_canvas_folder_id(db, "user-1")
        db.commit()

        folder = db.query(FileFolders).filter(FileFolders.id == folder_id).one()
        assert folder.name == "Canvas"
        assert folder.user_id == "user-1"
        assert folder.system_kind == "canvas"

        assert canvas_utils._ensure_canvas_folder_id(db, "user-1") == folder_id
        assert (
            db.query(FileFolders).filter(FileFolders.user_id == "user-1").count() == 1
        )
    finally:
        db.close()


def test_canvas_create_rolls_back_source_and_storage_when_grants_fail_after_preflight(
    monkeypatch,
    tmp_path,
):
    """A concurrent asset revocation cannot leave a half-saved new Canvas."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            FileFolders.__table__,
            SharedFileFolderSubscription.__table__,
            Files.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    asset = Files(
        id="asset-1",
        user_id="user-1",
        file_name="asset-1.png",
        storage_provider="local",
        storage_key="user-1/asset-1.png",
        file_category="image",
        file_type="image/png",
        file_size=10,
        meta={"original_filename": "asset.png"},
        created_at=now,
        last_updated_at=now,
    )
    db.add(asset)
    db.commit()

    events: list[str] = []

    @contextmanager
    def unlocked_admission(*_args, **_kwargs):
        yield

    monkeypatch.setattr(canvas_utils, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils,
        "ensure_user_file_upload_size_limit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        canvas_utils,
        "resolve_user_file_upload_limits",
        lambda *args, **kwargs: (20, 10_000_000),
    )
    monkeypatch.setattr(
        canvas_utils,
        "ensure_user_file_upload_capacity",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        canvas_utils, "serialized_user_file_quota_admission", unlocked_admission
    )
    monkeypatch.setattr(
        canvas_utils,
        "_validate_implicit_canvas_asset_ids",
        lambda *args, **kwargs: events.append("preflight"),
    )

    monkeypatch.setattr(
        file_utils, "resolve_user_owned_file_limits", lambda *args: (-1, None)
    )
    monkeypatch.setattr(
        file_utils, "serialized_user_file_quota_admission", unlocked_admission
    )
    monkeypatch.setattr(
        file_utils,
        "_delete_expired_file_quota_reservations_locked",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        file_utils,
        "ensure_user_file_upload_capacity",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        file_utils,
        "upload_file_to_storage",
        lambda source, user_id, file_name: (
            events.append("storage_written")
            or ("local", f"{user_id}/{file_name}", {})
        ),
    )
    monkeypatch.setattr(
        file_utils,
        "delete_storage_reference",
        lambda **kwargs: events.append(f"storage_deleted:{kwargs['storage_key']}"),
    )

    def fail_reconciliation(db, *, actor_user_id, file_record, asset_file_ids):
        assert file_record.id
        assert db.query(Files).filter(Files.id == file_record.id).one() is file_record
        assert asset_file_ids == ["asset-1"]
        events.append("reconcile_failed")
        raise CanvasAssetAccessError(CanvasAssetAccessError.code)

    monkeypatch.setattr(
        canvas_utils, "_persist_implicit_canvas_asset_grants", fail_reconciliation
    )

    try:
        with pytest.raises(CanvasAssetAccessError):
            canvas_utils.save_canvas_markdown(
                db,
                user_id="user-1",
                content="![asset](omlorix-file://asset-1)",
                content_type="markdown",
            )

        db.expire_all()
        assert db.query(Files).all() == [asset]
        assert events[:3] == ["preflight", "storage_written", "reconcile_failed"]
        assert events[3].startswith("storage_deleted:user-1/")
    finally:
        db.close()
        engine.dispose()


def test_canvas_folder_helper_does_not_reuse_shared_name_collision(monkeypatch):
    """A saved Canvas must not cross a same-named folder's sharing boundary."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            FileFolders.__table__,
            SharedFileFolderSubscription.__table__,
            Files.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    try:
        shared_folder = FileFolders(
            id="shared-canvas-folder",
            user_id="owner-1",
            name="canvas",
            icon="folder",
            icon_color="#6366f1",
            order=0,
            live_share_id="live-share-token",
            created_at=now,
            updated_at=now,
        )
        db.add(shared_folder)
        db.add(
            SharedFileFolderSubscription(
                id="subscription-1",
                folder_id=shared_folder.id,
                subscriber_id="subscriber-1",
                share_type="live",
                subscribed_at=now,
            )
        )
        db.commit()

        monkeypatch.setattr(
            canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
        )
        monkeypatch.setattr(
            canvas_utils,
            "ensure_user_file_upload_size_limit",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            canvas_utils,
            "resolve_user_file_upload_limits",
            lambda db, user_id: (3, 100),
        )
        monkeypatch.setattr(
            canvas_utils,
            "ensure_user_file_upload_capacity",
            lambda *args, **kwargs: None,
        )

        def persist_to_database(**kwargs):
            """Persist through the real model while avoiding external storage."""

            persisted_at = datetime.now(timezone.utc)
            record = Files(
                id=kwargs["file_id"],
                user_id=kwargs["user_id"],
                file_name=kwargs["file_name"],
                storage_provider="local",
                storage_key=f"{kwargs['user_id']}/{kwargs['file_name']}",
                file_category=kwargs["file_category"],
                file_type=kwargs["file_type"],
                file_size=len(kwargs["file_bytes"]),
                folder_id=kwargs["folder_id"],
                meta=kwargs["meta"],
                created_at=persisted_at,
                last_updated_at=persisted_at,
            )
            kwargs["db"].add(record)
            kwargs["db"].commit()
            return record

        monkeypatch.setattr(
            canvas_utils, "persist_generated_file_bytes", persist_to_database
        )
        save_result = canvas_utils.save_canvas_markdown(
            db,
            user_id="owner-1",
            content="private canvas content",
            content_type="markdown",
        )

        generated_file = (
            db.query(Files).filter(Files.id == save_result["file_id"]).one()
        )
        resolved_folder_id = generated_file.folder_id

        assert resolved_folder_id != shared_folder.id
        resolved_folder = (
            db.query(FileFolders).filter(FileFolders.id == resolved_folder_id).one()
        )
        assert resolved_folder.system_kind == "canvas"
        assert resolved_folder.live_share_id is None
        assert (
            accessible_files_query(db, "subscriber-1")
            .filter(Files.id == generated_file.id)
            .first()
            is None
        )
    finally:
        db.close()


def test_canvas_system_folder_cannot_be_shared():
    """Backend authorization must enforce privacy even if the UI is bypassed."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[FileFolders.__table__])
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    try:
        db.add(
            FileFolders(
                id="system-canvas-folder",
                user_id="owner-1",
                name="Canvas",
                system_kind="canvas",
                icon="folder",
                icon_color="#6366f1",
                order=0,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            create_folder_share(db, "owner-1", "system-canvas-folder", ShareType.LIVE)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == {"code": "system_folder_not_shareable"}
    finally:
        db.close()


def test_canvas_save_new_file_uses_canvas_folder(monkeypatch):
    persisted: dict[str, object] = {}

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 100)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "_ensure_canvas_folder_id", lambda db, user_id: "canvas-folder-1"
    )

    def fake_persist_generated_file_bytes(**kwargs):
        persisted.update(kwargs)
        return SimpleNamespace(
            id=kwargs["file_id"],
            file_name=kwargs["file_name"],
            folder_id=kwargs["folder_id"],
        )

    monkeypatch.setattr(
        canvas_utils, "persist_generated_file_bytes", fake_persist_generated_file_bytes
    )

    result = canvas_utils.save_canvas_markdown(
        object(),
        user_id="user-1",
        content="<main>Hello</main>",
        content_type="html",
    )

    assert result["created"] is True
    assert persisted["folder_id"] == "canvas-folder-1"


def test_canvas_save_new_file_does_not_auto_attach_to_project(monkeypatch):
    persisted: dict[str, object] = {}

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 100)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )

    def fake_persist_generated_file_bytes(**kwargs):
        persisted.update(kwargs)
        return SimpleNamespace(id=kwargs["file_id"], file_name=kwargs["file_name"])

    monkeypatch.setattr(
        canvas_utils, "persist_generated_file_bytes", fake_persist_generated_file_bytes
    )

    result = canvas_utils.save_canvas_markdown(
        object(),
        user_id="user-1",
        content="# Generated",
        content_type="markdown",
        project_id="project-1",
    )

    assert result["created"] is True
    assert "project_id" not in persisted


def test_canvas_save_overwrite_enforces_storage_capacity_without_file_count(
    monkeypatch,
):
    capacity_calls: list[dict[str, object]] = []
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.md",
        file_type="text/markdown",
        file_category="document",
        file_size=2,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={},
        project_id=None,
    )
    db = SimpleNamespace(
        add=lambda record: None, commit=lambda: None, refresh=lambda record: None
    )

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (0, 100)
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )
    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        lambda **kwargs: ("local", "new-key", {}),
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **kwargs: None)

    def fake_capacity(
        db,
        user_id,
        file_size,
        *,
        max_files_limit,
        max_user_storage_limit_bytes,
        existing_file_id=None,
    ):
        capacity_calls.append(
            {
                "user_id": user_id,
                "file_size": file_size,
                "max_files_limit": max_files_limit,
                "max_user_storage_limit_bytes": max_user_storage_limit_bytes,
                "existing_file_id": existing_file_id,
            }
        )

    monkeypatch.setattr(canvas_utils, "ensure_user_file_upload_capacity", fake_capacity)

    result = canvas_utils.save_canvas_markdown(
        db,
        user_id="user-1",
        file_id="file-1",
        content="updated",
        content_type="markdown",
    )

    assert result["created"] is False
    assert capacity_calls == [
        {
            "user_id": "user-1",
            "file_size": 7,
            "max_files_limit": 0,
            "max_user_storage_limit_bytes": 100,
            "existing_file_id": "file-1",
        }
    ]


def test_canvas_user_edit_persists_active_xhtml_attachment(monkeypatch, tmp_path):
    """Active HTML aliases save as canonical inert HTML without losing their name."""
    saved_bytes: dict[str, object] = {}
    file_record = SimpleNamespace(
        id="html-file-1",
        file_name="html-file-1.xhtml",
        file_type="application/xhtml+xml; charset=utf-8",
        file_category="document",
        file_size=10,
        storage_provider="local",
        storage_key="old-html-key",
        storage_meta={},
        meta={"original_filename": "interactive.xhtml", "origin": "user"},
        project_id=None,
    )
    db = SimpleNamespace(
        add=lambda record: None,
        commit=lambda: None,
        rollback=lambda: None,
        refresh=lambda record: None,
    )

    monkeypatch.setattr(canvas_utils, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 10000)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )

    def fake_overwrite_existing_file_bytes(**kwargs):
        saved_bytes.update(kwargs)
        return "local", "new-html-key", {}

    monkeypatch.setattr(
        canvas_utils, "overwrite_existing_file_bytes", fake_overwrite_existing_file_bytes
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **kwargs: None)

    html = "<!doctype html><html><body><script>window.example = true;</script></body></html>"
    result = canvas_utils.save_canvas_markdown(
        db,
        user_id="user-1",
        file_id="html-file-1",
        content=html,
        content_type="html",
        filename="interactive.xhtml",
        edit_source="user",
        edited_by="user-1",
        allow_html_attachment=True,
    )

    assert result["content"] == html
    assert result["file_name"] == "interactive.xhtml"
    assert saved_bytes["file_bytes"] == html.encode("utf-8")
    assert file_record.file_type == "text/html"
    assert file_record.meta["origin"] == "user"


def test_canvas_save_update_does_not_auto_attach_to_project(monkeypatch):
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.md",
        file_type="text/markdown",
        file_category="document",
        file_size=2,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={},
        project_id=None,
    )
    db = SimpleNamespace(
        add=lambda record: None, commit=lambda: None, refresh=lambda record: None
    )

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 100)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )
    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        lambda **kwargs: ("local", "new-key", {}),
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **kwargs: None)

    result = canvas_utils.save_canvas_markdown(
        db,
        user_id="user-1",
        file_id="file-1",
        content="updated",
        content_type="markdown",
        project_id="project-1",
    )

    assert result["created"] is False
    assert file_record.project_id is None


def test_canvas_save_update_preserves_existing_private_folder(monkeypatch):
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.html",
        file_type="text/html",
        file_category="document",
        file_size=2,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={"original_filename": "site.html", "canvas_type": "html"},
        project_id=None,
        folder_id="private-folder-1",
    )
    db = SimpleNamespace(
        add=lambda record: None, commit=lambda: None, refresh=lambda record: None
    )

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 100)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "_ensure_canvas_folder_id", lambda db, user_id: "canvas-folder-1"
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )
    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        lambda **kwargs: ("local", "new-key", {}),
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **kwargs: None)

    result = canvas_utils.save_canvas_markdown(
        db,
        user_id="user-1",
        file_id="file-1",
        content="<main>Updated</main>",
        content_type="html",
    )

    assert result["created"] is False
    assert file_record.folder_id == "private-folder-1"


def test_canvas_update_compensates_staged_storage_when_grant_reconciliation_fails(
    monkeypatch,
):
    """An edit keeps the previous object authoritative when grants race."""

    events: list[str] = []
    file_record = SimpleNamespace(
        id="canvas-1",
        file_name="canvas-1.md",
        file_type="text/markdown",
        file_category="document",
        file_size=3,
        storage_provider="local",
        storage_key="owner-1/canvas-1.md",
        storage_meta={},
        meta={"canvas": True, "canvas_type": "markdown"},
        project_id=None,
        folder_id=None,
    )
    db = SimpleNamespace(
        add=lambda _record: events.append("source_staged"),
        commit=lambda: events.append("committed"),
        refresh=lambda _record: None,
        rollback=lambda: events.append("rolled_back"),
    )

    monkeypatch.setattr(canvas_utils, "get_file", lambda *args: file_record)
    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils,
        "ensure_user_file_upload_size_limit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        canvas_utils,
        "resolve_user_file_upload_limits",
        lambda *args, **kwargs: (20, 10_000),
    )
    monkeypatch.setattr(
        canvas_utils,
        "ensure_user_file_upload_capacity",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        canvas_utils,
        "_validate_implicit_canvas_asset_ids",
        lambda *args, **kwargs: events.append("preflight"),
    )
    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        lambda **kwargs: (
            events.append("storage_staged")
            or ("local", "owner-1/canvas-1.staged.md", {})
        ),
    )
    monkeypatch.setattr(
        canvas_utils,
        "invalidate_materialized_file_cache",
        lambda **kwargs: events.append("cache_invalidated"),
    )
    monkeypatch.setattr(
        canvas_utils,
        "delete_storage_reference",
        lambda **kwargs: events.append(f"deleted:{kwargs['storage_key']}"),
    )

    def fail_reconciliation(*_args, **_kwargs):
        events.append("reconcile_failed")
        raise CanvasAssetAccessError(CanvasAssetAccessError.code)

    monkeypatch.setattr(
        canvas_utils, "_persist_implicit_canvas_asset_grants", fail_reconciliation
    )

    with pytest.raises(CanvasAssetAccessError):
        canvas_utils.save_canvas_markdown(
            db,
            user_id="owner-1",
            file_id="canvas-1",
            content="![asset](omlorix-file://asset-1)",
            content_type="markdown",
        )

    assert "committed" not in events
    assert events == [
        "preflight",
        "storage_staged",
        "source_staged",
        "reconcile_failed",
        "rolled_back",
        "cache_invalidated",
        "deleted:owner-1/canvas-1.staged.md",
    ]


def test_canvas_save_partial_update_replaces_snippet_range_and_preserves_existing_type(
    monkeypatch, tmp_path
):
    existing_file = tmp_path / "website.html"
    existing_file.write_text(
        '<!doctype html>\n<html>\n<body>\n<main id="app">Old content</main>\n</body>\n</html>',
        encoding="utf-8",
    )
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.html",
        file_type="text/html",
        file_category="document",
        file_size=existing_file.stat().st_size,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={"original_filename": "website.html", "canvas_type": "html"},
        project_id=None,
    )
    db = SimpleNamespace(
        add=lambda record: None,
        commit=lambda: None,
        refresh=lambda record: None,
        rollback=lambda: None,
    )
    saved: dict[str, object] = {}

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 1000)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )
    monkeypatch.setattr(
        canvas_utils, "materialize_file_record", lambda record, user_id: existing_file
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **kwargs: None)

    def fake_overwrite_existing_file_bytes(**kwargs):
        saved.update(kwargs)
        return "local", "new-key", {}

    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        fake_overwrite_existing_file_bytes,
    )

    result = canvas_utils.save_canvas_markdown(
        db,
        user_id="user-1",
        file_id="file-1",
        content='<main id="app">New content</main>',
        content_type=None,
        start_snippet='<main id="app">',
        end_snippet="</main>",
    )

    saved_text = saved["file_bytes"].decode("utf-8")
    assert (
        saved_text
        == '<!doctype html>\n<html>\n<body>\n<main id="app">New content</main>\n</body>\n</html>'
    )
    assert result["content"] == saved_text
    assert result["content_type"] == "html"
    assert file_record.file_type == "text/html"


def test_existing_html_canvas_overwrite_reloads_new_storage_bytes(monkeypatch, tmp_path):
    """A saved model edit must survive closing and reopening the Canvas file."""

    storage_root = tmp_path / "files"
    temp_root = storage_root / "temp"
    materialized_root = temp_root / "materialized"
    storage_root.mkdir(parents=True)
    temp_root.mkdir(parents=True)
    materialized_root.mkdir(parents=True)
    monkeypatch.setattr(file_utils, "BASE_STORAGE_DIR", storage_root)
    monkeypatch.setattr(file_utils, "TEMP_DIR", temp_root)
    monkeypatch.setattr(file_utils, "MATERIALIZED_TEMP_DIR", materialized_root)

    file_record = SimpleNamespace(
        id="file-1",
        user_id="user-1",
        file_name="file-1.html",
        file_type="text/html",
        file_category="document",
        file_size=0,
        storage_provider="local",
        storage_key=file_utils.build_storage_key("user-1", "file-1.html"),
        storage_meta={},
        meta={"original_filename": "website.html", "canvas_type": "html"},
        project_id=None,
        folder_id="canvas-folder-1",
    )
    stored_path = file_utils.get_file_path("user-1", file_record.file_name)
    stored_path.write_text("<main>Before edit</main>", encoding="utf-8")
    file_record.file_size = stored_path.stat().st_size

    db = SimpleNamespace(
        add=lambda record: None,
        commit=lambda: None,
        refresh=lambda record: None,
        rollback=lambda: None,
    )
    monkeypatch.setattr(canvas_utils, "get_file", lambda *args: file_record)
    monkeypatch.setattr(
        canvas_utils,
        "resolve_accessible_file_record",
        lambda *args: (file_record, "user-1"),
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda *args: (10, 1024 * 1024)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    def upload_to_test_storage(source, user_id, file_name):
        storage_key = file_utils.build_storage_key(user_id, file_name)
        target = file_utils._resolve_local_storage_path(storage_key, create_parent=True)
        target.write_bytes(Path(source).read_bytes())
        return "local", storage_key, {"size_bytes": target.stat().st_size}

    monkeypatch.setattr(file_utils, "upload_file_to_storage", upload_to_test_storage)

    updated_html = "<!doctype html><html><body><main>After edit</main></body></html>"
    save_result = canvas_utils.save_canvas_markdown(
        db,
        user_id="user-1",
        file_id="file-1",
        content=updated_html,
        content_type="html",
    )
    reopened = canvas_utils.view_canvas_file(
        db,
        user_id="user-1",
        file_id="file-1",
    )

    assert save_result["created"] is False
    assert save_result["content"] == updated_html
    assert reopened["content"] == updated_html
    assert not stored_path.exists()
    assert file_utils._resolve_local_storage_path(
        file_record.storage_key
    ).read_text(encoding="utf-8") == updated_html


def test_canvas_save_partial_update_allows_empty_replacement(monkeypatch, tmp_path):
    existing_file = tmp_path / "data.csv"
    existing_file.write_text("name,value\nkeep,1\nremove,2\n", encoding="utf-8")
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.csv",
        file_type="text/csv",
        file_category="document",
        file_size=existing_file.stat().st_size,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={"original_filename": "data.csv", "canvas_type": "csv"},
        project_id=None,
    )
    db = SimpleNamespace(
        add=lambda record: None,
        commit=lambda: None,
        refresh=lambda record: None,
        rollback=lambda: None,
    )
    saved: dict[str, object] = {}

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 1000)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )
    monkeypatch.setattr(
        canvas_utils, "materialize_file_record", lambda record, user_id: existing_file
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **kwargs: None)
    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        lambda **kwargs: saved.update(kwargs) or ("local", "new-key", {}),
    )

    result = canvas_utils.save_canvas_markdown(
        db,
        user_id="user-1",
        file_id="file-1",
        content="",
        content_type=None,
        start_snippet="remove,2\n",
        end_snippet="remove,2\n",
    )

    assert saved["file_bytes"].decode("utf-8") == "name,value\nkeep,1\n"
    assert result["content_type"] == "csv"


def test_canvas_view_reads_accessible_current_file_content(monkeypatch, tmp_path):
    existing_file = tmp_path / "notes.md"
    existing_file.write_text("# Current\n\nEdited by the user.\n", encoding="utf-8")
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.md",
        file_type="text/markdown",
        meta={"original_filename": "notes.md", "canvas_type": "markdown"},
    )

    monkeypatch.setattr(
        canvas_utils,
        "resolve_accessible_file_record",
        lambda db, user_id, file_id: (file_record, "owner-1"),
    )
    monkeypatch.setattr(
        canvas_utils, "materialize_file_record", lambda record, user_id: existing_file
    )

    result = canvas_utils.view_canvas_file(
        object(), user_id="viewer-1", file_id="file-1"
    )

    assert result["file_id"] == "file-1"
    assert result["file_name"] == "notes.md"
    assert result["content"] == "# Current\n\nEdited by the user.\n"
    assert result["content_type"] == "markdown"
    assert result["viewed"] is True


def test_canvas_view_tool_result_does_not_emit_visible_file_attachment(monkeypatch):
    monkeypatch.setattr(
        tool_helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        tool_helper,
        "view_canvas_file",
        lambda **kwargs: {
            "file_id": "file-1",
            "file_name": "notes.md",
            "content": "# Current\n\nEdited by the user.",
            "content_type": "markdown",
            "created": False,
            "viewed": True,
        },
    )

    tool_call = tool_helper.resolve_tool_call(
        object(),
        "canvas",
        {"type": "view", "file_id": "file-1"},
        "user-1",
        None,
        None,
    )

    with pytest.raises(StopIteration) as completed:
        next(tool_call)

    payload = completed.value.value
    assert payload["documents"] == []
    assert payload["images"] == []
    assert payload["videos"] == []
    assert payload["audios"] == []
    assert "file_id" not in payload
    assert payload["result"]["viewed"] is True
    assert "# Current" in payload["content"]


def test_canvas_tool_call_keeps_tool_row_and_allows_live_preview_arguments():
    # Canvas arguments must reach the browser as t_cd events so the sidebar can
    # render content while the model is still constructing the tool call. The
    # frontend independently suppresses those large arguments from the compact
    # chat tool row.
    assert "canvas" not in tools_not_yield_arguments
    assert not should_hide_tool_call_from_user(
        "canvas", {"type": "view", "file_id": "file-1"}
    )
    assert not should_hide_tool_call_from_user(
        "canvas", {"type": "markdown", "content": "# New"}
    )


def test_canvas_update_records_user_revision_metadata(monkeypatch, tmp_path):
    existing_file = tmp_path / "notes.md"
    existing_file.write_text("old", encoding="utf-8")
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.md",
        file_type="text/markdown",
        file_category="document",
        file_size=existing_file.stat().st_size,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={
            "original_filename": "notes.md",
            "canvas_type": "markdown",
            "canvas_revision": 3,
        },
    )
    db = SimpleNamespace(
        add=lambda record: None,
        commit=lambda: None,
        refresh=lambda record: None,
        rollback=lambda: None,
    )

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 1000)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **kwargs: None)
    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        lambda **kwargs: ("local", "new-key", {}),
    )

    canvas_utils.save_canvas_markdown(
        db,
        user_id="owner-1",
        file_id="file-1",
        content="new",
        content_type="markdown",
        edit_source="user",
        edited_by="editor-1",
    )

    assert file_record.meta["canvas_revision"] == 4
    assert file_record.meta["canvas_last_edit_source"] == "user"
    assert file_record.meta["canvas_last_edited_by"] == "editor-1"
    assert file_record.meta["canvas_last_edited_at"]


def test_spreadsheet_update_preserves_file_identity_and_records_revision(monkeypatch):
    """Binary spreadsheet saves reuse Canvas revision metadata and storage identity."""
    file_record = SimpleNamespace(
        id="workbook-1",
        user_id="owner-1",
        file_name="workbook-1.xlsx",
        file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_category="document",
        file_size=12,
        folder_id="folder-1",
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={
            "origin": "assistant",
            "original_filename": "forecast.xlsx",
            "canvas_revision": 4,
        },
    )
    db = SimpleNamespace(
        add=lambda record: None,
        commit=lambda: None,
        refresh=lambda record: None,
        rollback=lambda: None,
    )

    @contextmanager
    def quota_lock(*_args, **_kwargs):
        yield

    monkeypatch.setattr(canvas_utils, "get_file", lambda *_args, **_kwargs: file_record)
    monkeypatch.setattr(canvas_utils, "_validate_canvas_content_bytes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(canvas_utils, "ensure_user_file_upload_size_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(canvas_utils, "resolve_user_file_upload_limits", lambda *_args, **_kwargs: (20, 10_000))
    monkeypatch.setattr(canvas_utils, "serialized_user_file_quota_admission", quota_lock)
    monkeypatch.setattr(canvas_utils, "ensure_user_file_upload_capacity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        lambda **_kwargs: ("local", "new-key", {"etag": "new"}),
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **_kwargs: None)

    result = canvas_utils.save_canvas_spreadsheet(
        db,
        user_id="owner-1",
        file_id="workbook-1",
        file_bytes=b"PK spreadsheet bytes",
        file_format="xlsx",
        expected_revision=4,
        filename="forecast.xlsx",
        edit_source="user",
        edited_by="editor-2",
        requires_recalculation=True,
    )

    assert result["file_id"] == "workbook-1"
    assert result["spreadsheet_format"] == "xlsx"
    assert result["canvas_revision"] == 5
    assert result["spreadsheet_requires_recalculation"] is True
    assert file_record.folder_id == "folder-1"
    assert file_record.meta["canvas"] is True
    assert file_record.meta["canvas_type"] == "spreadsheet"
    assert file_record.meta["spreadsheet_requires_recalculation"] is True
    assert file_record.meta["canvas_last_edit_source"] == "user"
    assert file_record.meta["canvas_last_edited_by"] == "editor-2"


def test_canvas_tsv_validation_uses_the_shared_document_allowlist(monkeypatch, tmp_path):
    """TSV Canvas autosaves pass the same MIME validation as ordinary uploads."""
    monkeypatch.setattr(canvas_utils, "TEMP_DIR", tmp_path)

    assert file_utils.validate_file_type("text/tab-separated-values") is True
    canvas_utils._validate_canvas_content_bytes(
        b"name\tvalue\nalpha\t1\n",
        file_type="text/tab-separated-values",
    )


def test_spreadsheet_update_rejects_an_outdated_expected_revision():
    """A stale collaborator cannot overwrite bytes from a newer revision."""
    file_record = SimpleNamespace(
        id="workbook-1",
        user_id="owner-1",
        file_name="workbook-1.csv",
        file_type="text/csv",
        meta={
            "original_filename": "forecast.csv",
            "canvas_revision": 7,
        },
    )
    class LockingQuery:
        """Minimal query stand-in that records use of SELECT FOR UPDATE."""

        locked = False

        def filter(self, *_args):
            return self

        def with_for_update(self):
            self.locked = True
            return self

        def first(self):
            return file_record

    query = LockingQuery()
    db = SimpleNamespace(query=lambda _model: query)

    with pytest.raises(canvas_utils.CanvasSpreadsheetRevisionConflict) as caught:
        canvas_utils.save_canvas_spreadsheet(
            db,
            user_id="owner-1",
            file_id="workbook-1",
            file_bytes=b"name,value\nalpha,2\n",
            file_format="csv",
            expected_revision=6,
        )

    assert caught.value.expected_revision == 6
    assert caught.value.current_revision == 7
    assert query.locked is True


def _xlsx_package_bytes(*, extra_entries: dict[str, bytes] | None = None) -> bytes:
    """Build the smallest XLSX-shaped ZIP needed by archive validation tests."""
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("xl/workbook.xml", b"<workbook/>")
        for name, content in (extra_entries or {}).items():
            archive.writestr(name, content)
    return payload.getvalue()


def test_spreadsheet_archive_validation_accepts_bounded_xlsx(tmp_path):
    """A normal workbook remains available after expanded-package checks."""
    workbook_path = tmp_path / "bounded.xlsx"
    workbook_path.write_bytes(
        _xlsx_package_bytes(
            extra_entries={"xl/worksheets/sheet1.xml": b"<worksheet/>"}
        )
    )

    file_utils.validate_spreadsheet_archive(
        workbook_path,
        file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def test_spreadsheet_archive_validation_rejects_excessive_expansion(
    monkeypatch,
    tmp_path,
):
    """Highly compressible workbook members cannot bypass the browser budget."""
    monkeypatch.setattr(
        file_utils,
        "SPREADSHEET_ARCHIVE_MAX_TOTAL_UNCOMPRESSED_BYTES",
        64,
    )
    workbook_path = tmp_path / "expanding.xlsx"
    workbook_bytes = bytearray(
        _xlsx_package_bytes(
            extra_entries={"xl/worksheets/sheet1.xml": b"A" * 1_024}
        )
    )
    # Forge the last central-directory entry's uncompressed size so a check
    # that trusts ZIP metadata would accept this highly compressible member.
    sheet_directory_offset = workbook_bytes.rfind(b"PK\x01\x02")
    assert sheet_directory_offset >= 0
    workbook_bytes[
        sheet_directory_offset + 24 : sheet_directory_offset + 28
    ] = (1).to_bytes(4, "little")
    workbook_path.write_bytes(workbook_bytes)

    with pytest.raises(file_utils.SpreadsheetArchiveValidationError):
        file_utils.validate_spreadsheet_archive(
            workbook_path,
            file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def test_spreadsheet_content_route_returns_the_validated_snapshot(
    monkeypatch,
    tmp_path,
):
    """The browser receives exactly the bounded bytes and revision just validated."""
    workbook_bytes = _xlsx_package_bytes(
        extra_entries={"xl/worksheets/sheet1.xml": b"<worksheet/>"}
    )
    workbook_path = tmp_path / "forecast.xlsx"
    workbook_path.write_bytes(workbook_bytes)
    file_record = SimpleNamespace(
        id="workbook-1",
        user_id="owner-1",
        file_name="workbook-1.xlsx",
        file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_size=len(workbook_bytes),
        meta={
            "original_filename": "forecast.xlsx",
            "spreadsheet_format": "xlsx",
            "canvas_revision": 7,
            "spreadsheet_requires_recalculation": True,
        },
    )
    monkeypatch.setattr(
        files_router,
        "get_accessible_file",
        lambda _db, _user_id, _file_id: file_record,
    )
    monkeypatch.setattr(
        files_router,
        "materialize_file_record",
        lambda _record, _user_id: workbook_path,
    )

    response = files_router.get_canvas_spreadsheet_content_route(
        file_id="workbook-1",
        user=SimpleNamespace(id="viewer-1"),
        db=object(),
    )

    assert response.body == workbook_bytes
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="forecast.xlsx";'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-canvas-revision"] == "7"
    assert response.headers["x-spreadsheet-requires-recalculation"] == "true"


@pytest.mark.parametrize(
    "unsafe_mime_type",
    (*file_utils.HTML_ATTACHMENT_MIME_TYPES, "image/svg+xml"),
)
def test_spreadsheet_content_route_rejects_misnamed_active_content(
    monkeypatch,
    tmp_path,
    unsafe_mime_type,
):
    """A spreadsheet-looking name must not reactivate stored markup."""
    active_path = tmp_path / "data.csv"
    active_path.write_text(
        "<!doctype html><html><body><script>alert(1)</script></body></html>",
        encoding="utf-8",
    )
    file_record = SimpleNamespace(
        id="active-1",
        user_id="owner-1",
        file_name="active-1.csv",
        file_type=unsafe_mime_type,
        file_size=active_path.stat().st_size,
        meta={"original_filename": "data.csv"},
    )
    monkeypatch.setattr(
        files_router,
        "get_accessible_file",
        lambda _db, _user_id, _file_id: file_record,
    )
    monkeypatch.setattr(
        files_router,
        "materialize_file_record",
        lambda _record, _user_id: active_path,
    )

    with pytest.raises(HTTPException) as exc_info:
        files_router.get_canvas_spreadsheet_content_route(
            file_id="active-1",
            user=SimpleNamespace(id="viewer-1"),
            db=object(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported spreadsheet format"


def test_spreadsheet_content_route_keeps_inert_extension_fallback(
    monkeypatch,
    tmp_path,
):
    """Content-inert CSV detections remain editable when MIME sniffing is generic."""
    csv_bytes = b"name,value\nalpha,1\n"
    csv_path = tmp_path / "report.csv"
    csv_path.write_bytes(csv_bytes)
    file_record = SimpleNamespace(
        id="csv-1",
        user_id="owner-1",
        file_name="csv-1.csv",
        file_type="text/plain",
        file_size=len(csv_bytes),
        meta={"original_filename": "Quarterly Report.csv", "canvas_revision": 2},
    )
    monkeypatch.setattr(
        files_router,
        "get_accessible_file",
        lambda _db, _user_id, _file_id: file_record,
    )
    monkeypatch.setattr(
        files_router,
        "materialize_file_record",
        lambda _record, _user_id: csv_path,
    )

    response = files_router.get_canvas_spreadsheet_content_route(
        file_id="csv-1",
        user=SimpleNamespace(id="viewer-1"),
        db=object(),
    )

    assert response.body == csv_bytes
    assert response.headers["content-type"] == "application/octet-stream"
    assert "Quarterly%20Report.csv" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-canvas-revision"] == "2"


def test_spreadsheet_persistence_error_is_not_reclassified_as_client_input(
    monkeypatch,
):
    """Storage failures reach the route's generic 500 boundary, not its 400 detail."""
    file_record = SimpleNamespace(
        id="workbook-1",
        user_id="owner-1",
        file_name="workbook-1.csv",
        file_type="text/csv",
        file_category="document",
        file_size=4,
        folder_id=None,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={"original_filename": "forecast.csv"},
    )
    db = SimpleNamespace(
        add=lambda _record: None,
        commit=lambda: None,
        refresh=lambda _record: None,
        rollback=lambda: None,
    )

    @contextmanager
    def quota_lock(*_args, **_kwargs):
        yield

    monkeypatch.setattr(canvas_utils, "get_file", lambda *_args, **_kwargs: file_record)
    monkeypatch.setattr(
        canvas_utils,
        "_validate_canvas_content_bytes",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        canvas_utils,
        "ensure_user_file_upload_size_limit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        canvas_utils,
        "resolve_user_file_upload_limits",
        lambda *_args, **_kwargs: (20, 10_000),
    )
    monkeypatch.setattr(canvas_utils, "serialized_user_file_quota_admission", quota_lock)
    monkeypatch.setattr(
        canvas_utils,
        "ensure_user_file_upload_capacity",
        lambda *_args, **_kwargs: None,
    )

    def fail_storage(**_kwargs):
        raise RuntimeError("private storage backend detail")

    monkeypatch.setattr(canvas_utils, "overwrite_existing_file_bytes", fail_storage)

    with pytest.raises(RuntimeError, match="private storage backend detail"):
        canvas_utils.save_canvas_spreadsheet(
            db,
            user_id="owner-1",
            file_id="workbook-1",
            file_bytes=b"a,b\n1,2\n",
            file_format="csv",
            expected_revision=0,
        )


def test_spreadsheet_route_masks_unexpected_value_error(monkeypatch):
    """Only the dedicated input exception may cross the route's 400 boundary."""
    file_record = SimpleNamespace(id="workbook-1", user_id="owner-1")
    monkeypatch.setattr(
        files_router,
        "get_file",
        lambda _db, _file_id, _user_id: file_record,
    )
    monkeypatch.setattr(
        files_router,
        "ensure_user_file_upload_size_limit",
        lambda *_args, **_kwargs: None,
    )

    def fail_save(*_args, **_kwargs):
        raise ValueError("private database adapter detail")

    monkeypatch.setattr(files_router, "save_canvas_spreadsheet", fail_save)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/files/canvas/spreadsheet/save",
            "headers": [],
        }
    )
    upload = UploadFile(filename="forecast.csv", file=io.BytesIO(b"a,b\n1,2\n"))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            files_router.save_canvas_spreadsheet_route(
                request=request,
                file=upload,
                file_id="workbook-1",
                file_format="csv",
                expected_revision=0,
                filename="forecast.csv",
                requires_recalculation=False,
                user=SimpleNamespace(id="owner-1"),
                db=object(),
                db_log=object(),
            )
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == "Failed to save spreadsheet"
    assert "private database adapter detail" not in str(caught.value.detail)


def test_spreadsheet_route_returns_structured_revision_conflict(monkeypatch):
    """The browser can distinguish a stale save from other input failures."""
    file_record = SimpleNamespace(id="workbook-1", user_id="owner-1")
    monkeypatch.setattr(
        files_router,
        "get_file",
        lambda _db, _file_id, _user_id: file_record,
    )
    monkeypatch.setattr(
        files_router,
        "ensure_user_file_upload_size_limit",
        lambda *_args, **_kwargs: None,
    )

    def reject_stale_save(*_args, **_kwargs):
        raise canvas_utils.CanvasSpreadsheetRevisionConflict(
            expected_revision=4,
            current_revision=5,
        )

    monkeypatch.setattr(files_router, "save_canvas_spreadsheet", reject_stale_save)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/files/canvas/spreadsheet/save",
            "headers": [],
        }
    )
    upload = UploadFile(filename="forecast.csv", file=io.BytesIO(b"a,b\n1,2\n"))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            files_router.save_canvas_spreadsheet_route(
                request=request,
                file=upload,
                file_id="workbook-1",
                file_format="csv",
                expected_revision=4,
                filename="forecast.csv",
                requires_recalculation=False,
                user=SimpleNamespace(id="owner-1"),
                db=object(),
                db_log=object(),
            )
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "code": "spreadsheet_revision_conflict",
        "expected_revision": 4,
        "current_revision": 5,
    }


@pytest.mark.parametrize(
    ("pdf_file_id", "expected_status"),
    [("", "not_rendered"), ("pdf-1", "stale")],
)
def test_latex_canvas_edit_status_depends_on_existing_pdf(
    monkeypatch,
    tmp_path,
    pdf_file_id,
    expected_status,
):
    """Editing LaTeX is stale only when a rendered derivative already exists."""
    existing_file = tmp_path / "document.tex"
    existing_file.write_text("old", encoding="utf-8")
    file_record = SimpleNamespace(
        id="source-1",
        file_name="source-1.tex",
        file_type="text/x-tex",
        file_category="document",
        file_size=existing_file.stat().st_size,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={
            "original_filename": "document.tex",
            "canvas_type": "latex",
            "canvas_revision": 3,
            "latex_pdf_file_id": pdf_file_id,
            "latex_render_status": "ready" if pdf_file_id else "not_rendered",
        },
    )
    db = SimpleNamespace(
        add=lambda record: None,
        commit=lambda: None,
        refresh=lambda record: None,
        rollback=lambda: None,
    )

    monkeypatch.setattr(
        canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 1000)
    )
    monkeypatch.setattr(
        canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )
    monkeypatch.setattr(canvas_utils, "delete_storage_reference", lambda **kwargs: None)
    monkeypatch.setattr(
        canvas_utils,
        "overwrite_existing_file_bytes",
        lambda **kwargs: ("local", "new-key", {}),
    )

    result = canvas_utils.save_canvas_markdown(
        db,
        user_id="owner-1",
        file_id="source-1",
        content="new",
        content_type="latex",
        edit_source="user",
    )

    assert file_record.meta["latex_render_status"] == expected_status
    assert result["render_status"] == expected_status


def test_html_canvas_user_edit_context_prompts_view_for_newer_user_revision(
    monkeypatch,
):
    assistant_time = "2026-07-05T10:00:00+00:00"
    edited_time = "2026-07-05T10:05:00+00:00"
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.html",
        file_type="text/html",
        meta={
            "canvas": True,
            "canvas_type": "html",
            "original_filename": "website.html",
            "canvas_revision": 2,
            "canvas_last_edit_source": "user",
            "canvas_last_edited_at": edited_time,
        },
    )
    chat_history = [
        SimpleNamespace(
            role="assistant",
            created_at=assistant_time,
            content='[{"type":"content","documents":["file-1"]}]',
        ),
        SimpleNamespace(
            role="user",
            created_at="2026-07-05T10:06:00+00:00",
            content='[{"type":"user","content":"what changed?"}]',
        ),
    ]

    monkeypatch.setattr(
        chat_utils, "get_accessible_file", lambda db, user_id, file_id: file_record
    )

    context = chat_utils._build_canvas_user_edit_user_context(
        object(), user_id="user-1", chat_history=chat_history
    )

    assert context is not None
    assert context.startswith("## Canvas File Updates")
    assert "type='view'" in context
    assert "file_id=file-1" in context
    assert "website.html" in context
    assert "content_type=html" in context


def test_excel_user_edit_context_directs_model_to_binary_file_tools(monkeypatch):
    """Excel revisions notify the model without suggesting the text Canvas reader."""
    file_record = SimpleNamespace(
        id="workbook-1",
        file_name="workbook-1.xlsx",
        file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        meta={
            "canvas": True,
            "canvas_type": "spreadsheet",
            "spreadsheet_format": "xlsx",
            "original_filename": "forecast.xlsx",
            "canvas_revision": 3,
            "canvas_last_edit_source": "user",
            "canvas_last_edited_at": "2026-07-05T10:05:00+00:00",
        },
    )
    chat_history = [
        SimpleNamespace(
            role="assistant",
            created_at="2026-07-05T10:00:00+00:00",
            content='[{"type":"content","documents":["workbook-1"]}]',
        ),
        SimpleNamespace(
            role="user",
            created_at="2026-07-05T10:06:00+00:00",
            content='[{"type":"user","content":"check my changes"}]',
        ),
    ]
    monkeypatch.setattr(
        chat_utils,
        "get_accessible_file",
        lambda _db, _user_id, _file_id: file_record,
    )

    context = chat_utils._build_canvas_user_edit_user_context(
        object(), user_id="user-1", chat_history=chat_history
    )

    assert context is not None
    assert "file or code-execution tools" in context
    assert "content_type=xlsx" in context
    assert "forecast.xlsx" in context


def test_canvas_user_edit_user_context_ignores_assistant_revision(monkeypatch):
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.md",
        file_type="text/markdown",
        meta={
            "canvas": True,
            "canvas_type": "markdown",
            "canvas_last_edit_source": "assistant",
            "canvas_last_edited_at": "2026-07-05T10:05:00+00:00",
        },
    )
    chat_history = [
        SimpleNamespace(
            role="assistant",
            created_at="2026-07-05T10:00:00+00:00",
            content='[{"type":"content","documents":["file-1"]}]',
        ),
        SimpleNamespace(
            role="user",
            created_at="2026-07-05T10:06:00+00:00",
            content='[{"type":"user","content":"continue"}]',
        ),
    ]

    monkeypatch.setattr(
        chat_utils, "get_accessible_file", lambda db, user_id, file_id: file_record
    )

    assert (
        chat_utils._build_canvas_user_edit_user_context(
            object(), user_id="user-1", chat_history=chat_history
        )
        is None
    )


def test_canvas_save_partial_update_rejects_ambiguous_start_snippet(
    monkeypatch, tmp_path
):
    existing_file = tmp_path / "diagram.mmd"
    existing_file.write_text("graph TD\nA-->B\nA-->C\n", encoding="utf-8")
    file_record = SimpleNamespace(
        id="file-1",
        file_name="file-1.mmd",
        file_type="text/x-mermaid",
        meta={"original_filename": "diagram.mmd", "canvas_type": "mermaid"},
    )

    monkeypatch.setattr(
        canvas_utils, "resolve_user_file_upload_limits", lambda db, user_id: (3, 1000)
    )
    monkeypatch.setattr(
        canvas_utils, "get_file", lambda db, file_id, user_id: file_record
    )
    monkeypatch.setattr(
        canvas_utils, "materialize_file_record", lambda record, user_id: existing_file
    )

    with pytest.raises(ValueError, match="start_snippet matched more than once"):
        canvas_utils.save_canvas_markdown(
            object(),
            user_id="user-1",
            file_id="file-1",
            content="A-->D",
            content_type=None,
            start_snippet="A-->",
            end_snippet="B",
        )


def test_canvas_save_route_preserves_http_exceptions(monkeypatch):
    payload = CanvasFileSaveRequest(
        file_id="file-1", content="hello", content_type="markdown"
    )

    source = SimpleNamespace(id="file-1", user_id="user-1", file_name="file.md", meta={})
    monkeypatch.setattr(
        files_router,
        "resolve_file_for_edit",
        lambda db, user_id, file_id: ResolvedFileAccess(source, "user-1"),
    )
    def fake_save_canvas_markdown(**kwargs):
        raise HTTPException(status_code=400, detail="Canvas save rejected")

    monkeypatch.setattr(files_router, "save_canvas_markdown", fake_save_canvas_markdown)

    with pytest.raises(HTTPException, match="Canvas save rejected") as exc_info:
        files_router.save_canvas_file_route(
            payload,
            request=SimpleNamespace(headers={}),
            user=SimpleNamespace(id="user-1"),
            db=object(),
        )

    assert exc_info.value.status_code == 400


def test_canvas_save_request_accepts_html_content_type():
    payload = CanvasFileSaveRequest(
        file_id="file-1",
        content="<main>Hello</main>",
        content_type="html",
        filename="website",
    )

    assert payload.content_type == "html"
    assert payload.filename == "website"


def test_canvas_save_response_does_not_reject_a_committed_pending_count():
    """Response validation must not turn a completed save into a server error."""

    response = CanvasFileSaveResponse(
        file_id="file-1",
        file_name="canvas.md",
        content="# Canvas",
        content_type="markdown",
        pending_asset_approval_count=21,
    )

    assert response.pending_asset_approval_count == 21


def test_canvas_asset_decision_requires_the_actionable_notification_id():
    """Notification cleanup must always address one deterministic row."""

    with pytest.raises(ValidationError):
        CanvasAssetDecisionRequest(
            canvas_file_id="canvas-1",
            request_id="request-1",
            decision="approve",
        )


def test_canvas_save_route_accepts_html_content_type(monkeypatch):
    payload = CanvasFileSaveRequest(
        file_id="file-1",
        content="<main>Hello</main>",
        content_type="html",
        filename="website.html",
    )
    captured: dict[str, object] = {}
    audit_calls: list[dict[str, object]] = []

    source = SimpleNamespace(id="file-1", user_id="user-1", file_name="website.html", meta={})
    monkeypatch.setattr(
        files_router,
        "resolve_file_for_edit",
        lambda db, user_id, file_id: ResolvedFileAccess(source, "user-1"),
    )
    def fake_save_canvas_markdown(**kwargs):
        captured.update(kwargs)
        kwargs["before_commit"](
            {
                "file_id": kwargs["file_id"],
                "content_type": kwargs["content_type"],
                "canvas_revision": None,
                "asset_count": 0,
                "pending_asset_approval_count": 0,
            }
        )
        return {
            "file_id": kwargs["file_id"],
            "file_name": "website.html",
            "content": kwargs["content"],
            "content_type": kwargs["content_type"],
            "page_count": 1,
            "created": False,
        }

    monkeypatch.setattr(files_router, "save_canvas_markdown", fake_save_canvas_markdown)
    monkeypatch.setattr(
        files_router,
        "stage_audit_log_event",
        lambda _db, *, user_id, action, details, **_kwargs: audit_calls.append(
            {
                "user_id": user_id,
                "action": action,
                "details": details,
            }
        ),
    )

    result = files_router.save_canvas_file_route(
        payload,
        request=SimpleNamespace(headers={}),
        user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert captured["content_type"] == "html"
    assert captured["edit_source"] == "user"
    assert captured["edited_by"] == "user-1"
    assert captured["allow_html_attachment"] is True
    assert captured["force_canvas_asset_reconciliation"] is True
    assert result.content_type == "html"
    assert result.file_name == "website.html"
    assert audit_calls == [
        {
            "user_id": "user-1",
            "action": "CANVAS_EDITED",
            "details": {
                "file_id": "file-1",
                "content_type": "html",
                "canvas_revision": None,
                "asset_count": 0,
                "pending_asset_approval_count": 0,
                "is_collaborator": False,
            },
        }
    ]
    assert "content" not in audit_calls[0]["details"]
    assert "filename" not in audit_calls[0]["details"]


def test_canvas_tool_schema_advertises_html_content_type():
    canvas_type_schema = tool_schemas["canvas"]["parameters"]["properties"]["type"]

    assert "html" in canvas_type_schema["enum"]
    assert "latex" in canvas_type_schema["enum"]
    assert "view" in canvas_type_schema["enum"]
    assert "HTML" in tool_schemas["canvas"]["description"]
    assert "view" in tool_schemas["canvas"]["description"]
    assert "proofreading" in tool_schemas["canvas"]["description"]
    assert (
        "do not send the complete article again"
        in tool_schemas["canvas"]["description"]
    )
    assert "file_ids" in tool_schemas["canvas"]["parameters"]["properties"]


def test_canvas_save_creates_latex_source_with_render_metadata(monkeypatch):
    """Model-created LaTeX also routes assets through authoritative grants."""
    persisted: dict[str, object] = {}
    grant_events: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(canvas_utils, "_validate_canvas_content_bytes", lambda *args, **kwargs: None)
    monkeypatch.setattr(canvas_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(canvas_utils, "resolve_user_file_upload_limits", lambda *args, **kwargs: (20, 10_000_000))
    monkeypatch.setattr(canvas_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        canvas_utils,
        "_validate_implicit_canvas_asset_ids",
        lambda db, *, actor_user_id, asset_file_ids: grant_events.append(
            ("validate", list(asset_file_ids or []))
        ),
    )
    monkeypatch.setattr(
        canvas_utils,
        "_persist_implicit_canvas_asset_grants",
        lambda db, *, actor_user_id, file_record, asset_file_ids: (
            grant_events.append(("persist", list(asset_file_ids or []))) or ([], [])
        ),
    )

    def persist(**kwargs):
        persisted.update(kwargs)
        record = SimpleNamespace(
            id=kwargs["file_id"],
            file_name=kwargs["file_name"],
            meta=kwargs["meta"],
        )
        kwargs["before_commit"](record)
        return record

    monkeypatch.setattr(canvas_utils, "persist_generated_file_bytes", persist)
    result = canvas_utils.save_canvas_markdown(
        object(),
        user_id="user-1",
        content="\\documentclass{article}\\begin{document}Hi\\end{document}",
        content_type="latex",
        filename="report.tex",
        file_ids=["asset-1", "asset-1", "asset-2"],
    )

    assert persisted["file_type"] == "text/x-tex"
    assert persisted["file_name"].endswith(".tex")
    assert persisted["meta"]["canvas_type"] == "latex"
    assert persisted["meta"]["latex_source"] is True
    assert persisted["meta"]["latex_asset_file_ids"] == ["asset-1", "asset-2"]
    assert persisted["meta"]["latex_render_status"] == "not_rendered"
    assert grant_events == [
        ("validate", ["asset-1", "asset-2"]),
        ("persist", ["asset-1", "asset-2"]),
    ]
    assert result["canvas_revision"] == 1
    assert result["pdf_file_id"] == ""
    content_description = tool_schemas["canvas"]["parameters"]["properties"]["content"][
        "description"
    ]
    assert "isolated Canvas preview" in content_description
    assert "explicit viewer grant" in content_description
    assert "retry_allowed" in content_description


def test_canvas_tool_schema_lists_file_metadata_before_content():
    property_names = list(tool_schemas["canvas"]["parameters"]["properties"].keys())

    assert property_names[:7] == [
        "type",
        "filename",
        "file_id",
        "id",
        "start_snippet",
        "end_snippet",
        "content",
    ]
    assert tool_schemas["canvas"]["parameters"]["required"] == []


def test_notes_tool_schema_advertises_view_snippet_edits_and_file_refs():
    notes_schema = tool_schemas["notes"]
    properties = notes_schema["parameters"]["properties"]

    assert "view" in properties["type"]["enum"]
    assert "delete" not in properties["type"]["enum"]
    assert "start_snippet" in properties
    assert "end_snippet" in properties
    assert "omlorix-file://FILE_ID" in notes_schema["description"]
    assert "proofreading" in notes_schema["description"]
    assert "do not send the complete note again" in notes_schema["description"]
