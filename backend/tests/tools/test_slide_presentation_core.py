"""Focused tests for the direct slide-presentation contract."""

from pathlib import Path
from io import BytesIO
import inspect
import json
import shutil
from types import SimpleNamespace
import zipfile

import pytest
from fastapi import HTTPException

from app.tools.schemas import tool_schemas
from app.tools import helper as tool_helper
from app.tools.slide_presentation.sanitizer import (
    inspect_slide_presentation_html,
    prepare_slide_presentation_html,
    sanitize_slide_presentation_html,
    sanitize_slide_presentation_title,
    validate_slide_presentation_html,
)
from app.tools.slide_presentation import storage as presentation_storage
from app.tools.slide_presentation import pipeline as presentation_pipeline
from app.tools.slide_presentation import models as presentation_models
from app.tools.slide_presentation import router as presentation_router


VALID_DECK = """<!doctype html><html><head><style>
.slide { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; }
</style></head><body>
<section class="slide" data-slide-index="1" data-slide-title="One"><h1>One</h1></section>
<section class="slide" data-slide-index="2" data-slide-title="Two"><h2>Two</h2></section>
</body></html>"""


@pytest.fixture(autouse=True)
def _isolate_model_presentation_audits(monkeypatch):
    monkeypatch.setattr(
        presentation_pipeline,
        "stage_tool_audit_action",
        lambda *_args, **_kwargs: None,
    )


def test_slide_tool_requires_markdown_file_id_and_accepts_image_assets():
    schema = tool_schemas["slide_presentation"]["parameters"]
    assert schema["required"] == ["file_id"]
    assert set(schema["properties"]) == {"file_id", "file_ids"}
    assert schema["properties"]["file_ids"]["maxItems"] == 20


def test_provisional_slide_endpoint_requires_owned_canonical_source(
    tmp_path, monkeypatch
):
    """An in-progress render is visible only through its owner's source row."""

    draft_doc = inspect.getdoc(presentation_router.get_draft_slide_image) or ""
    assert "first render writes directly" in draft_doc
    assert "temporarily receive 404" in draft_doc
    assert "treat that response as not ready and retry" in draft_doc

    image_path = (
        tmp_path / "user-1" / "presentations" / "deck-1" / "images" / "slide_1.png"
    )
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"provisional-png")
    monkeypatch.setattr(presentation_router, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        presentation_router,
        "get_file",
        lambda db, file_id, user_id: SimpleNamespace(
            meta={"slide_presentation_source": True}
        ),
    )

    response = presentation_router.get_draft_slide_image(
        "deck-1",
        1,
        user=SimpleNamespace(id="user-1"),
        db=object(),
    )

    assert Path(response.path) == image_path
    assert response.headers["cache-control"] == "no-store, max-age=0"

    monkeypatch.setattr(presentation_router, "get_file", lambda *args: None)
    with pytest.raises(HTTPException) as raised:
        presentation_router.get_draft_slide_image(
            "deck-1",
            1,
            user=SimpleNamespace(id="user-1"),
            db=object(),
        )
    assert getattr(raised.value, "status_code", None) == 404


def test_editor_render_route_rejects_a_success_below_the_requested_revision(
    monkeypatch,
):
    """A successful editor response must cover its requested canvas revision."""

    presentation = SimpleNamespace(
        file_id="pptx-old",
        title="Quarterly plan",
        slide_count=2,
        storage_provider="local",
        storage_prefix="presentations/deck-1",
    )
    source = SimpleNamespace(meta={"canvas_revision": 4})
    refreshed_source = SimpleNamespace(
        meta={
            "canvas_revision": 4,
            "presentation_render_revision": 3,
            "presentation_render_status": "ready",
        }
    )

    def stale_success():
        if False:
            yield ""
        return {
            "file_id": "pptx-new",
            "title": "Quarterly plan",
            "slide_count": 2,
        }

    monkeypatch.setattr(
        presentation_router,
        "_owned_editor_records",
        lambda *args: (presentation, source, source.meta),
    )
    monkeypatch.setattr(
        presentation_router, "_read_editor_source", lambda *args: VALID_DECK
    )
    monkeypatch.setattr(
        presentation_router,
        "rerender_presentation_source",
        lambda **kwargs: stale_success(),
    )
    monkeypatch.setattr(presentation_router, "get_file", lambda *args: refreshed_source)
    monkeypatch.setattr(presentation_router, "_audit_editor_event", lambda *args: None)
    monkeypatch.setattr(
        tool_helper, "enforce_tool_rate_limit_or_raise", lambda *args, **kwargs: None
    )

    with pytest.raises(HTTPException) as raised:
        presentation_router.render_presentation_editor_source(
            "deck-1",
            presentation_router.SlidePresentationEditorRenderRequest(
                expected_revision=4
            ),
            request=SimpleNamespace(),
            user=SimpleNamespace(id="user-1", group_id=None),
            db=SimpleNamespace(),
            db_log=SimpleNamespace(),
        )

    assert raised.value.status_code == 502
    assert "requested revision" in raised.value.detail


def test_slide_html_classifier_requires_fixed_canvas_and_sequential_indexes():
    assert validate_slide_presentation_html(VALID_DECK) == 2
    invalid = VALID_DECK.replace('data-slide-index="2"', 'data-slide-index="4"')
    assert inspect_slide_presentation_html(invalid)["is_presentation"] is False
    with pytest.raises(ValueError):
        validate_slide_presentation_html(invalid)


def test_slide_html_classifier_accepts_qualified_and_listed_slide_selectors():
    qualified = VALID_DECK.replace(
        ".slide { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; }",
        ".notes, section.slide[data-layout] { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; }",
    ).replace('class="slide"', 'class="slide" data-layout="standard"')

    assert validate_slide_presentation_html(qualified) == 2


def test_slide_html_classifier_requires_contract_to_match_every_slide():
    unmatched = VALID_DECK.replace(
        ".slide { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; }",
        ".missing .slide { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; }",
    )
    with pytest.raises(ValueError, match="missing_required_slide_markup"):
        validate_slide_presentation_html(unmatched)

    partial = VALID_DECK.replace(
        ".slide { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; }",
        "section.slide[data-layout] { width: 1920px; height: 1080px; position: relative; overflow: hidden; box-sizing: border-box; }",
    ).replace(
        'class="slide" data-slide-index="1"',
        'class="slide" data-layout="standard" data-slide-index="1"',
    )
    with pytest.raises(ValueError, match="missing_required_slide_markup"):
        validate_slide_presentation_html(partial)


def test_slide_html_classifier_rejects_missing_contract_fields_and_overrides():
    missing_title = VALID_DECK.replace(' data-slide-title="Two"', "")
    with pytest.raises(ValueError, match="missing_slide_titles"):
        validate_slide_presentation_html(missing_title)

    overridden = VALID_DECK.replace(
        "</style>",
        ".slide { width: 100px; }</style>",
    )
    with pytest.raises(ValueError, match="missing_required_slide_markup"):
        validate_slide_presentation_html(overridden)

    inline_override = VALID_DECK.replace(
        'class="slide" data-slide-index="2"',
        'class="slide" style="width: 100px" data-slide-index="2"',
    )
    with pytest.raises(ValueError, match="missing_required_slide_markup"):
        validate_slide_presentation_html(inline_override)


def test_slide_html_classifier_uses_top_level_css_cascade_rules():
    cascaded = VALID_DECK.replace(
        "</style>",
        """
        .slide.featured { width: 100px; }
        .slide { width: 120px !important; }
        .slide { width: 1920px !important; }
        @media print { .slide { height: 100px !important; } }
        </style>
        """,
    )

    assert validate_slide_presentation_html(cascaded) == 2

    higher_specificity_override = VALID_DECK.replace(
        "</style>",
        ".slide.featured { width: 100px; }</style>",
    ).replace(
        'class="slide" data-slide-index="1"',
        'class="slide featured" data-slide-index="1"',
    )
    with pytest.raises(ValueError, match="missing_required_slide_markup"):
        validate_slide_presentation_html(higher_specificity_override)


def test_slide_html_classifier_enforces_maximum_deck_size():
    slides = "".join(
        f'<section class="slide" data-slide-index="{index}" data-slide-title="{index}"></section>'
        for index in range(1, 52)
    )
    oversized = VALID_DECK.replace(
        VALID_DECK.split("<body>", 1)[1].split("</body>", 1)[0],
        slides,
    )
    with pytest.raises(ValueError, match="too_many_slides"):
        validate_slide_presentation_html(oversized)


def test_presentation_title_is_safe_as_a_file_basename():
    assert (
        sanitize_slide_presentation_title("../Quarterly: plan\\draft")
        == "..Quarterly plandraft"
    )
    assert (
        sanitize_slide_presentation_title("///", fallback="Presentation")
        == "Presentation"
    )


def test_visual_review_images_use_numeric_order_and_a_bounded_payload(tmp_path):
    from PIL import Image

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    for number in [10, 2, 1, *range(3, 10), *range(11, 24)]:
        Image.new("RGB", (32, 18), color=(number, 0, 0)).save(
            images_dir / f"slide_{number}.png", "PNG"
        )

    message = presentation_pipeline._review_message("<html></html>", images_dir)
    image_blocks = message["content"][1:]

    assert len(image_blocks) == 2
    assert all(
        block["image_url"]["url"].startswith("data:image/jpeg;base64,")
        for block in image_blocks
    )
    assert all(block["image_url"]["detail"] == "high" for block in image_blocks)


def test_renderer_zip_uses_numeric_slide_order():
    from app.tools.slide_presentation.rendering import utils as rendering_utils

    bundle = BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("deck.pptx", b"pptx")
        for number in [1, 10, 11, 2, 3, 4, 5, 6, 7, 8, 9]:
            archive.writestr(f"slides/slide_{number}.png", str(number).encode())

    _, images = rendering_utils._extract_zip_bundle(bundle.getvalue())
    assert [image.decode() for image in images] == [
        str(number) for number in range(1, 12)
    ]


@pytest.mark.parametrize(
    "configured_url",
    [
        "http://renderer.example.test",
        "http://renderer.example.test/api",
        "http://renderer.example.test/api/render",
        "http://renderer.example.test/api/v1",
        "http://renderer.example.test/api/v1/render",
    ],
)
def test_renderer_endpoints_normalize_legacy_service_urls(configured_url):
    """All supported saved URL forms must use the current gateway routes."""
    from app.tools.slide_presentation.rendering import utils as rendering_utils

    assert rendering_utils._resolve_render_endpoint(configured_url) == (
        "http://renderer.example.test/api/render"
    )
    assert rendering_utils._resolve_health_endpoint(configured_url) == (
        "http://renderer.example.test/health"
    )


def test_visual_review_masks_and_restores_embedded_image_bytes():
    html = '<img src="data:image/png;base64,' + ("A" * 100_000) + '">'
    masked, assets = presentation_pipeline._mask_review_assets(html)

    assert len(masked) < 200
    assert "__OMLORIX_EMBEDDED_IMAGE_1__" in masked
    assert presentation_pipeline._restore_review_assets(masked, assets) == html


def test_slide_sanitizer_removes_active_and_remote_content():
    unsafe = VALID_DECK.replace(
        "<h1>One</h1>",
        '<script>alert(1)</script><img src="https://example.com/x.png" onerror="alert(2)"><h1>One</h1>',
    )
    sanitized = sanitize_slide_presentation_html(unsafe)
    assert "<script" not in sanitized
    assert "onerror" not in sanitized
    assert "https://example.com" not in sanitized
    assert validate_slide_presentation_html(sanitized) == 2


def test_slide_asset_reference_is_authorized_and_inlined(tmp_path, monkeypatch):
    """An attached image ID becomes a self-contained data URI before rendering."""
    from app.tools.slide_presentation import sanitizer

    image_path = tmp_path / "logo.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nlogo-bytes")

    class ImageRecord:
        id = "logo-file-id"
        file_type = "image/png"
        file_category = "image"
        file_size = image_path.stat().st_size

    monkeypatch.setattr(
        sanitizer,
        "get_file",
        lambda db, file_id, user_id: (
            ImageRecord() if file_id == "logo-file-id" and user_id == "user-1" else None
        ),
    )
    monkeypatch.setattr(
        sanitizer,
        "materialize_file_record",
        lambda record, user_id: image_path,
    )

    html = VALID_DECK.replace(
        "<h1>One</h1>",
        '<img src="omlorix-file://logo-file-id" alt="Logo"><h1>One</h1>',
    )
    prepared = prepare_slide_presentation_html(
        html,
        db=object(),
        user_id="user-1",
        allowed_file_ids=["logo-file-id"],
    )

    assert "omlorix-file://" not in prepared
    assert 'src="data:image/png;base64,' in prepared
    assert validate_slide_presentation_html(prepared) == 2


def test_slide_asset_reference_must_be_attached(tmp_path, monkeypatch):
    """Knowing an ID is insufficient unless the tool call explicitly attached it."""
    html = VALID_DECK.replace(
        "<h1>One</h1>",
        '<img src="omlorix-file://logo-file-id"><h1>One</h1>',
    )
    with pytest.raises(ValueError, match="file_ids"):
        prepare_slide_presentation_html(
            html,
            db=object(),
            user_id="user-1",
            allowed_file_ids=[],
        )


def test_editor_text_autosave_updates_coherent_local_artifacts(tmp_path, monkeypatch):
    """Autosave must update source metadata without replacing rendered images."""

    monkeypatch.setattr(presentation_storage, "BASE_STORAGE_DIR", tmp_path)
    presentation_storage.save_presentation_text_artifacts(
        "user-1",
        "presentation-1",
        html=VALID_DECK,
        title="Quarterly plan",
        metadata={"title": "Quarterly plan", "slide_count": 2, "render_revision": 4},
        storage_provider="local",
    )

    presentation_dir = tmp_path / "user-1" / "presentations" / "presentation-1"
    assert (presentation_dir / "presentation.html").read_text(
        encoding="utf-8"
    ) == VALID_DECK
    assert (presentation_dir / "title.txt").read_text(
        encoding="utf-8"
    ) == "Quarterly plan"
    assert json.loads(
        (presentation_dir / "metadata.json").read_text(encoding="utf-8")
    ) == {
        "title": "Quarterly plan",
        "slide_count": 2,
        "render_revision": 4,
    }


def test_artifact_publication_uses_immutable_revision_prefixes(tmp_path, monkeypatch):
    source_dir = tmp_path / "render"
    (source_dir / "images").mkdir(parents=True)
    (source_dir / "metadata.json").write_text('{"slide_count": 1}', encoding="utf-8")
    (source_dir / "title.txt").write_text("Deck", encoding="utf-8")
    (source_dir / "presentation.html").write_text(VALID_DECK, encoding="utf-8")
    (source_dir / "images" / "slide_1.png").write_bytes(b"first")
    storage_root = tmp_path / "storage"

    class LocalAdapter:
        def upload_file(self, local_path, storage_key):
            target = storage_root / storage_key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, target)
            return {"size_bytes": target.stat().st_size}

    monkeypatch.setattr(presentation_storage, "BASE_STORAGE_DIR", storage_root)
    monkeypatch.setattr(
        presentation_storage, "get_presentation_storage_provider", lambda: "local"
    )
    monkeypatch.setattr(
        presentation_storage, "get_user_file_storage_adapter", LocalAdapter
    )

    first = presentation_storage.upload_presentation_artifacts(
        presentation_dir=source_dir,
        user_id="user-1",
        presentation_id="deck-1",
        slide_count=1,
        revision=1,
    )
    (source_dir / "images" / "slide_1.png").write_bytes(b"second")
    second = presentation_storage.upload_presentation_artifacts(
        presentation_dir=source_dir,
        user_id="user-1",
        presentation_id="deck-1",
        slide_count=1,
        revision=2,
    )

    assert first["storage_prefix"] != second["storage_prefix"]
    assert (
        storage_root / first["storage_prefix"] / "images" / "slide_1.png"
    ).read_bytes() == b"first"
    assert (
        storage_root / second["storage_prefix"] / "images" / "slide_1.png"
    ).read_bytes() == b"second"


def test_presentation_upsert_race_uses_savepoint_without_outer_rollback(monkeypatch):
    existing = SimpleNamespace(
        title="Old",
        slide_count=1,
        storage_provider="local",
        storage_prefix="old",
        storage_meta={},
        file_id=None,
        last_updated_at=None,
    )
    lookups = iter([None, existing])
    monkeypatch.setattr(
        presentation_models,
        "get_slide_presentation",
        lambda *args: next(lookups),
    )

    class Savepoint:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeDB:
        flushes = 0

        def begin_nested(self):
            return Savepoint()

        def add(self, value):
            return None

        def flush(self):
            self.flushes += 1
            if self.flushes == 1:
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError("insert", {}, RuntimeError("duplicate"))

    db = FakeDB()
    result = presentation_models.upsert_slide_presentation(
        db,
        presentation_id="deck-1",
        user_id="user-1",
        title="Updated",
        slide_count=3,
        storage_provider="local",
        storage_prefix="new",
        file_id="pptx-1",
        commit=False,
    )

    assert result is existing
    assert existing.title == "Updated"
    assert existing.storage_prefix == "new"
    assert db.flushes == 2


def test_provider_adapters_do_not_treat_slides_as_terminal_tools():
    """A completed slide tool must return to the model like every other tool."""
    app_root = Path(__file__).resolve().parents[2] / "app"
    provider_files = [
        app_root / "llm/openai/chat.py",
        app_root / "llm/openrouter/chat.py",
        app_root / "llm/openai_chat_completions/chat.py",
        app_root / "llm/google_aistudio/chat.py",
        app_root / "llm/anthropic/chat.py",
        app_root / "llm/ollama/chat.py",
    ]
    for provider_file in provider_files:
        source = provider_file.read_text(encoding="utf-8")
        assert '== "slide_presentation"' not in source
        assert "SLIDE_PRESENTATION_TOOL_NAMES" not in source


def test_slide_tool_failure_emits_terminal_sidebar_event_before_reraising():
    """A pipeline exception must close the live preview even if the provider recovers."""
    helper_source = Path(tool_helper.__file__).read_text(encoding="utf-8")
    branch_start = helper_source.index('elif tool_name == "slide_presentation":')
    branch_end = helper_source.index('elif tool_name == "deep_research":', branch_start)
    slide_branch = helper_source[branch_start:branch_end]

    error_event = slide_branch.index('"event": "error"')
    diagnostic_raise = slide_branch.index(
        "raise ToolExecutionDiagnosticError", error_event
    )
    assert error_event < diagnostic_raise
    assert '"message": detail' not in slide_branch[error_event:diagnostic_raise]
    assert "could not be generated" in slide_branch[error_event:diagnostic_raise]


@pytest.mark.parametrize(
    ("route_name", "expected_format", "expected_media_type"),
    [
        ("download_slide_images_archive", "images_zip", "application/zip"),
        ("download_slide_images_pdf", "pdf", "application/pdf"),
    ],
)
def test_presentation_exports_emit_content_free_audit_events(
    monkeypatch,
    tmp_path,
    route_name,
    expected_format,
    expected_media_type,
):
    slide_paths = [tmp_path / "slide_1.png", tmp_path / "slide_2.png"]
    for slide_path in slide_paths:
        slide_path.write_bytes(b"png")
    presentation = SimpleNamespace(
        id="deck-1",
        storage_provider="local",
        storage_prefix="presentations/deck-1",
    )
    audit_calls = []
    monkeypatch.setattr(
        presentation_router,
        "get_slide_presentation",
        lambda *_args: presentation,
    )
    monkeypatch.setattr(
        presentation_router,
        "_resolve_slide_paths",
        lambda *_args: slide_paths,
    )
    monkeypatch.setattr(
        presentation_router,
        "_build_slide_pdf_file",
        lambda _slides, output_path: output_path.write_bytes(b"%PDF-test"),
    )
    monkeypatch.setattr(
        presentation_router,
        "_audit_editor_event",
        lambda db_log, request, user_id, action, details: audit_calls.append(
            {
                "user_id": user_id,
                "action": action,
                "details": details,
            }
        ),
    )

    route = getattr(presentation_router, route_name)
    response = route(
        presentation_id="deck-1",
        request=SimpleNamespace(headers={}),
        user=SimpleNamespace(id="user-1"),
        db=object(),
        db_log=object(),
    )

    try:
        assert response.media_type == expected_media_type
        assert audit_calls == [
            {
                "user_id": "user-1",
                "action": "EXPORT_SLIDE_PRESENTATION",
                "details": {
                    "presentation_id": "deck-1",
                    "format": expected_format,
                    "slide_count": 2,
                },
            }
        ]
        assert "filename" not in audit_calls[0]["details"]
    finally:
        Path(response.path).unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("route_name", "suffix"),
    [
        ("download_slide_images_archive", ".zip"),
        ("download_slide_images_pdf", ".pdf"),
    ],
)
def test_presentation_export_audit_failure_removes_temporary_artifact(
    monkeypatch,
    tmp_path,
    route_name,
    suffix,
):
    slide_path = tmp_path / "slide_1.png"
    slide_path.write_bytes(b"png")
    output_path = tmp_path / f"failed-export{suffix}"

    class TempHandle:
        name = str(output_path)

        def close(self):
            return None

    monkeypatch.setattr(
        presentation_router,
        "get_slide_presentation",
        lambda *_args: SimpleNamespace(
            id="deck-1",
            storage_provider="local",
            storage_prefix="presentations/deck-1",
        ),
    )
    monkeypatch.setattr(
        presentation_router,
        "_resolve_slide_paths",
        lambda *_args: [slide_path],
    )
    monkeypatch.setattr(
        presentation_router.tempfile,
        "NamedTemporaryFile",
        lambda **_kwargs: TempHandle(),
    )
    monkeypatch.setattr(
        presentation_router,
        "_build_slide_pdf_file",
        lambda _slides, path: path.write_bytes(b"%PDF-test"),
    )
    monkeypatch.setattr(
        presentation_router,
        "_audit_editor_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    route = getattr(presentation_router, route_name)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        route(
            presentation_id="deck-1",
            request=SimpleNamespace(headers={}),
            user=SimpleNamespace(id="user-1"),
            db=object(),
            db_log=object(),
        )

    assert not output_path.exists()


def test_presentation_pdf_render_failure_removes_temporary_artifact(
    monkeypatch,
    tmp_path,
):
    slide_path = tmp_path / "slide_1.png"
    slide_path.write_bytes(b"png")
    output_path = tmp_path / "failed-render.pdf"

    class TempHandle:
        name = str(output_path)

        def close(self):
            return None

    monkeypatch.setattr(
        presentation_router,
        "get_slide_presentation",
        lambda *_args: SimpleNamespace(
            id="deck-1",
            storage_provider="local",
            storage_prefix="presentations/deck-1",
        ),
    )
    monkeypatch.setattr(
        presentation_router,
        "_resolve_slide_paths",
        lambda *_args: [slide_path],
    )
    monkeypatch.setattr(
        presentation_router.tempfile,
        "NamedTemporaryFile",
        lambda **_kwargs: TempHandle(),
    )
    monkeypatch.setattr(
        presentation_router,
        "_build_slide_pdf_file",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("render failed")),
    )

    with pytest.raises(RuntimeError, match="render failed"):
        presentation_router.download_slide_images_pdf(
            presentation_id="deck-1",
            request=SimpleNamespace(headers={}),
            user=SimpleNamespace(id="user-1"),
            db=object(),
            db_log=object(),
        )

    assert not output_path.exists()


def test_slide_pipeline_ignores_non_object_json_events(monkeypatch):
    """Valid JSON scalars must not enter presentation event/error handling."""

    monkeypatch.setattr(
        tool_helper,
        "_admit_tool_invocation_or_payload",
        lambda *args, **kwargs: None,
    )

    def fake_pipeline(**kwargs):
        yield "42\n"
        yield (
            json.dumps(
                {
                    "t": "slide_presentation_evt",
                    "event": "complete",
                    "data": {"html_file_id": "deck-1", "pptx_file_id": "pptx-1"},
                }
            )
            + "\n"
        )
        return {"html_file_id": "deck-1", "pptx_file_id": "pptx-1"}

    monkeypatch.setattr(
        presentation_pipeline, "run_presentation_pipeline", fake_pipeline
    )
    runner = tool_helper.resolve_tool_call(
        object(),
        "slide_presentation",
        {"file_id": "brief-1"},
        "user-1",
        None,
        None,
    )
    emitted = []
    while True:
        try:
            emitted.append(next(runner))
        except StopIteration as completed:
            result = completed.value
            break

    assert emitted[0] == "42\n"
    assert result["documents"] == ["deck-1", "pptx-1"]


def test_canvas_save_survives_presentation_rerender_failure(monkeypatch):
    """A committed Canvas edit still emits its saved event if rendering fails."""

    from app.files import models as file_models

    class FakeDB:
        def rollback(self):
            return None

        def add(self, value):
            return None

        def commit(self):
            return None

        def refresh(self, value):
            return None

    deck_record = SimpleNamespace(
        id="deck-1",
        meta={"slide_presentation_source": True},
    )
    monkeypatch.setattr(
        tool_helper,
        "_admit_tool_invocation_or_payload",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        tool_helper, "stage_tool_audit_action", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(file_models, "get_file", lambda *args, **kwargs: deck_record)

    def fake_save_canvas_markdown(**kwargs):
        result = {
            "file_id": "deck-1",
            "file_name": "deck.html",
            "content": VALID_DECK,
            "content_type": "html",
            "created": False,
            "canvas_revision": 2,
        }
        kwargs["before_commit"](
            {
                "file_id": "deck-1",
                "created": False,
                "content_type": "html",
                "canvas_revision": 2,
                "asset_count": 0,
                "pending_asset_approval_count": 0,
            }
        )
        return result

    monkeypatch.setattr(
        tool_helper,
        "save_canvas_markdown",
        fake_save_canvas_markdown,
    )

    def failed_rerender(**kwargs):
        raise RuntimeError("renderer offline")
        yield  # pragma: no cover - preserve generator semantics

    monkeypatch.setattr(
        presentation_pipeline,
        "rerender_presentation_source",
        failed_rerender,
    )
    runner = tool_helper.resolve_tool_call(
        FakeDB(),
        "canvas",
        {"type": "html", "file_id": "deck-1", "content": VALID_DECK},
        "user-1",
        None,
        None,
    )
    emitted = []
    while True:
        try:
            emitted.append(next(runner))
        except StopIteration as completed:
            result = completed.value
            break

    saved_events = [json.loads(line) for line in emitted if '"event": "saved"' in line]
    assert saved_events[0]["data"]["file_id"] == "deck-1"
    assert result["file_id"] == "deck-1"


def test_canvas_immediate_result_exposes_exact_file_id(monkeypatch):
    """The model must receive the ID needed by a following slide tool call."""
    audit_calls = []
    monkeypatch.setattr(
        tool_helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        tool_helper,
        "stage_tool_audit_action",
        lambda db, user_id, action, **kwargs: audit_calls.append(
            {"user_id": user_id, "action": action, **kwargs}
        ),
    )

    def fake_save_canvas_markdown(**kwargs):
        result = {
            "file_id": "brief-file-123",
            "file_name": "presentation-brief.md",
            "content": "# Brief",
            "content_type": "markdown",
            "created": True,
            "page_count": 1,
            "canvas_revision": 1,
            "pending_asset_approval_count": 2,
        }
        kwargs["before_commit"](
            {
                "file_id": "brief-file-123",
                "created": True,
                "content_type": "markdown",
                "canvas_revision": 1,
                "asset_count": 0,
                "pending_asset_approval_count": 2,
            }
        )
        return result

    monkeypatch.setattr(
        tool_helper,
        "save_canvas_markdown",
        fake_save_canvas_markdown,
    )
    runner = tool_helper.resolve_tool_call(
        None,
        "canvas",
        {"type": "markdown", "content": "# Brief"},
        "user-1",
        None,
        None,
    )
    while True:
        try:
            next(runner)
        except StopIteration as completed:
            payload = completed.value
            break

    model_result = json.loads(payload["content"])
    assert model_result["file_id"] == "brief-file-123"
    assert model_result["content_type"] == "markdown"
    assert audit_calls == [
        {
            "user_id": "user-1",
            "action": "CANVAS_CREATED",
            "category": "files",
            "details": {
                "file_id": "brief-file-123",
                "created": True,
                "content_type": "markdown",
                "canvas_revision": 1,
                "project_id": None,
                "asset_count": 0,
                "pending_asset_approval_count": 2,
            },
        }
    ]
    assert "# Brief" not in repr(audit_calls)
    assert "presentation-brief.md" not in repr(audit_calls)


def test_initial_pipeline_sends_and_renders_uploaded_assets(tmp_path, monkeypatch):
    """The same owned image IDs must reach generation, source metadata, and rendering."""
    brief_path = tmp_path / "brief.md"
    brief_path.write_text("# Deck\n\n![Logo](omlorix-file://logo-1)", encoding="utf-8")
    brief_record = SimpleNamespace(
        id="brief-1",
        file_type="text/markdown",
        file_name="brief.md",
    )
    captured: dict[str, object] = {}
    audit_calls: list[dict[str, object]] = []
    lifecycle_order: list[str] = []
    source_record = SimpleNamespace(meta={"canvas_revision": 1})

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return source_record

    class FakeDB:
        def query(self, model):
            return FakeQuery()

        def add(self, value):
            return None

        def commit(self):
            lifecycle_order.append("db_commit")
            return None

    db = FakeDB()

    monkeypatch.setattr(presentation_pipeline, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(presentation_pipeline, "MAX_VISUAL_REFINEMENTS", 0)
    monkeypatch.setattr(
        presentation_pipeline, "get_file", lambda db, file_id, user_id: brief_record
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "materialize_file_record",
        lambda record, user_id: brief_path,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "validate_slide_presentation_asset_file_ids",
        lambda db, user_id, file_ids: list(dict.fromkeys(file_ids)),
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "get_settings_page_data",
        lambda db, page: {"presentation_model_id": "model-1"},
    )

    def fake_nested_generation(db, **kwargs):
        captured["generation_messages"] = kwargs["messages"]
        generated_html = VALID_DECK.replace(
            "<h1>One</h1>",
            '<img src="omlorix-file://logo-1"><h1>One</h1>',
        )
        yield generated_html
        return SimpleNamespace(text=generated_html)

    monkeypatch.setattr(
        presentation_pipeline, "stream_nested_generation", fake_nested_generation
    )

    def fake_prepare(html, *, db, user_id, allowed_file_ids):
        captured["prepared_assets"] = list(allowed_file_ids)
        return html.replace("omlorix-file://logo-1", "data:image/png;base64,bG9nbw==")

    monkeypatch.setattr(
        presentation_pipeline, "prepare_slide_presentation_html", fake_prepare
    )
    monkeypatch.setattr(
        presentation_pipeline, "validate_slide_presentation_html", lambda html: 2
    )

    def fake_save_canvas_markdown(**kwargs):
        result = {"file_id": "deck-1", "canvas_revision": 1}
        kwargs["before_commit"](result)
        lifecycle_order.append("source_commit")
        return result

    monkeypatch.setattr(
        presentation_pipeline,
        "save_canvas_markdown",
        fake_save_canvas_markdown,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "_update_source_meta",
        lambda db, file_id, user_id, **updates: captured.setdefault(
            "source_meta", {}
        ).update(updates),
    )

    def fake_render(**kwargs):
        captured["render_assets"] = kwargs["input_file_ids"]
        return {"file_id": "pptx-1", "slide_count": 2}

    monkeypatch.setattr(presentation_pipeline, "render_slide_presentation", fake_render)
    monkeypatch.setattr(
        presentation_pipeline,
        "upload_presentation_artifacts",
        lambda **kwargs: {
            "provider": "local",
            "storage_prefix": "presentations/deck-1",
        },
    )
    monkeypatch.setattr(
        presentation_pipeline, "upsert_slide_presentation", lambda *args, **kwargs: None
    )

    def capture_audit(audit_db, user_id, action, **kwargs):
        assert audit_db is db
        lifecycle_order.append(action)
        audit_calls.append({"user_id": user_id, "action": action, **kwargs})

    monkeypatch.setattr(
        presentation_pipeline,
        "stage_tool_audit_action",
        capture_audit,
    )

    runner = presentation_pipeline.run_presentation_pipeline(
        user_id="user-1",
        markdown_file_id="brief-1",
        input_file_ids=["logo-1"],
        db=db,
    )
    events = []
    while True:
        try:
            events.append(next(runner))
        except StopIteration as completed:
            result = completed.value
            break

    generation_block = captured["generation_messages"][0]["content"][0]
    assert generation_block["images"] == ["logo-1"]
    assert captured["prepared_assets"] == ["logo-1"]
    assert captured["render_assets"] is None
    assert captured["source_meta"]["slide_presentation_asset_file_ids"] == ["logo-1"]
    assert result["pptx_file_id"] == "pptx-1"
    event_names = [json.loads(event)["event"] for event in events]
    assert event_names == [
        "status",
        "html_delta",
        "draft_complete",
        "status",
        "revision_ready",
        "slide_images",
        "complete",
    ]
    assert audit_calls == [
        {
            "user_id": "user-1",
            "action": "SLIDE_PRESENTATION_SOURCE_SAVED",
            "category": "files",
            "details": {
                "presentation_id": "deck-1",
                "canvas_revision": 1,
                "slide_count": 2,
                "asset_count": 1,
                "project_id": None,
            },
        },
        {
            "user_id": "user-1",
            "action": "SLIDE_PRESENTATION_RENDERED",
            "category": "files",
            "details": {
                "presentation_id": "deck-1",
                "pptx_file_id": "pptx-1",
                "canvas_revision": 1,
                "slide_count": 2,
                "asset_count": 1,
                "project_id": None,
            },
        },
    ]
    assert lifecycle_order == [
        "SLIDE_PRESENTATION_SOURCE_SAVED",
        "source_commit",
        "SLIDE_PRESENTATION_RENDERED",
        "db_commit",
    ]
    assert "# Deck" not in repr(audit_calls)
    assert "logo-1" not in repr(audit_calls)


def test_visual_review_failure_publishes_last_good_render(tmp_path, monkeypatch):
    """Optional polishing must not discard an already usable presentation."""

    brief_path = tmp_path / "brief.md"
    brief_path.write_text("# Deck", encoding="utf-8")
    brief_record = SimpleNamespace(
        id="brief-1",
        file_type="text/markdown",
        file_name="brief.md",
    )
    source_record = SimpleNamespace(meta={"canvas_revision": 1})

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return source_record

    class FakeDB:
        def query(self, model):
            return FakeQuery()

        def add(self, value):
            return None

        def commit(self):
            return None

    def fake_initial_generation(*args, **kwargs):
        yield VALID_DECK
        return SimpleNamespace(text=VALID_DECK)

    def fail_visual_review(*args, **kwargs):
        raise RuntimeError("review offline")

    monkeypatch.setattr(presentation_pipeline, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        presentation_pipeline,
        "get_file",
        lambda db, file_id, user_id: brief_record,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "materialize_file_record",
        lambda record, user_id: brief_path,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "validate_slide_presentation_asset_file_ids",
        lambda *args: [],
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "get_settings_page_data",
        lambda *args: {"presentation_model_id": "model-1"},
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "stream_nested_generation",
        fake_initial_generation,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "run_nested_generation",
        fail_visual_review,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "prepare_slide_presentation_html",
        lambda html, **kwargs: html,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "validate_slide_presentation_html",
        lambda html: 2,
    )

    def fake_save_canvas_markdown(**kwargs):
        result = {"file_id": "deck-1", "canvas_revision": 1}
        kwargs["before_commit"](result)
        return result

    monkeypatch.setattr(
        presentation_pipeline,
        "save_canvas_markdown",
        fake_save_canvas_markdown,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "_update_source_meta",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "render_slide_presentation",
        lambda **kwargs: {"file_id": "pptx-1", "slide_count": 2},
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "upload_presentation_artifacts",
        lambda **kwargs: {
            "provider": "local",
            "storage_prefix": "presentations/deck-1",
        },
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "upsert_slide_presentation",
        lambda *args, **kwargs: None,
    )

    events = [
        json.loads(line)
        for line in presentation_pipeline.run_presentation_pipeline(
            user_id="user-1",
            markdown_file_id="brief-1",
            db=FakeDB(),
        )
    ]

    warning = next(event for event in events if event["event"] == "warning")
    assert warning["data"]["code"] == "visual_review_failed"
    assert warning["data"]["recoverable"] is True
    assert events[-1]["event"] == "complete"
    assert events[-1]["data"]["pptx_file_id"] == "pptx-1"


def test_initial_pipeline_marks_source_failed_before_reraising(tmp_path, monkeypatch):
    """An escaped renderer failure leaves a terminal source status."""

    brief_path = tmp_path / "brief.md"
    brief_path.write_text("# Deck", encoding="utf-8")
    brief_record = SimpleNamespace(
        id="brief-1",
        file_type="text/markdown",
        file_name="brief.md",
    )
    source_record = SimpleNamespace(meta={"canvas_revision": 1})

    class FakeDB:
        rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

        def add(self, value):
            return None

        def commit(self):
            return None

    db = FakeDB()
    statuses = []
    audit_calls = []
    monkeypatch.setattr(presentation_pipeline, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        presentation_pipeline,
        "get_file",
        lambda db, file_id, user_id: (
            brief_record if file_id == "brief-1" else source_record
        ),
    )
    monkeypatch.setattr(
        presentation_pipeline, "materialize_file_record", lambda *args: brief_path
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "validate_slide_presentation_asset_file_ids",
        lambda *args: [],
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "get_settings_page_data",
        lambda *args: {"presentation_model_id": "model-1"},
    )

    def fake_nested_generation(*args, **kwargs):
        yield VALID_DECK
        return SimpleNamespace(text=VALID_DECK)

    monkeypatch.setattr(
        presentation_pipeline,
        "stream_nested_generation",
        fake_nested_generation,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "prepare_slide_presentation_html",
        lambda html, **kwargs: html,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "validate_slide_presentation_html",
        lambda html: 2,
    )

    def fake_save_canvas_markdown(**kwargs):
        result = {"file_id": "deck-1", "canvas_revision": 1}
        kwargs["before_commit"](result)
        return result

    monkeypatch.setattr(
        presentation_pipeline,
        "save_canvas_markdown",
        fake_save_canvas_markdown,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "_update_source_meta",
        lambda db, file_id, user_id, **updates: statuses.append(
            updates["presentation_render_status"]
        ),
    )

    def failed_render(**kwargs):
        raise RuntimeError("renderer offline")

    monkeypatch.setattr(
        presentation_pipeline,
        "render_slide_presentation",
        failed_render,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "stage_tool_audit_action",
        lambda audit_db, user_id, action, **kwargs: audit_calls.append(
            {"user_id": user_id, "action": action, **kwargs}
        ),
    )

    runner = presentation_pipeline.run_presentation_pipeline(
        user_id="user-1",
        markdown_file_id="brief-1",
        db=db,
    )
    with pytest.raises(RuntimeError, match="renderer offline"):
        list(runner)

    assert statuses == ["rendering"]
    assert source_record.meta["presentation_render_status"] == "failed"
    assert db.rollbacks == 1
    assert [call["action"] for call in audit_calls] == [
        "SLIDE_PRESENTATION_SOURCE_SAVED",
        "SLIDE_PRESENTATION_RENDER_FAILED",
    ]
    assert audit_calls[-1]["details"] == {
        "presentation_id": "deck-1",
        "canvas_revision": 1,
        "asset_count": 0,
        "project_id": None,
    }
    assert "renderer offline" not in repr(audit_calls)


def test_failed_render_state_is_not_committed_without_its_audit_intent(monkeypatch):
    """A failed audit stage rolls back instead of publishing unaudited state."""

    source_record = SimpleNamespace(
        meta={"canvas_revision": 4, "presentation_render_status": "rendering"}
    )

    class FakeDB:
        commits = 0
        rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

        def add(self, value):
            return None

        def commit(self):
            self.commits += 1

    db = FakeDB()
    monkeypatch.setattr(
        presentation_pipeline,
        "get_file",
        lambda db, file_id, user_id: source_record,
    )

    def fail_audit_stage():
        raise RuntimeError("audit outbox unavailable")

    presentation_pipeline._mark_render_failed_if_current(
        db,
        "user-1",
        "deck-1",
        4,
        before_commit=fail_audit_stage,
    )

    assert db.commits == 0
    assert db.rollbacks == 2


def test_rerender_rebuilds_required_text_artifacts_after_restart(tmp_path, monkeypatch):
    """A cloud-backed edit must work when the disposable local cache is empty."""
    source_record = SimpleNamespace(
        id="deck-1",
        user_id="user-1",
        meta={
            "canvas_revision": 6,
            "slide_presentation_brief_file_id": "brief-1",
            "slide_presentation_asset_file_ids": ["logo-1"],
        },
    )
    presentation_record = SimpleNamespace(
        title="Quarterly plan",
        file_id=None,
        storage_provider="webdav",
        storage_prefix="user-1/presentations/deck-1",
        slide_count=2,
    )
    captured: dict[str, object] = {}

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return source_record

    class FakeDB:
        def query(self, model):
            return FakeQuery()

        def add(self, value):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

        def delete(self, value):
            return None

    db = FakeDB()

    monkeypatch.setattr(presentation_pipeline, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(presentation_storage, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        presentation_pipeline,
        "get_slide_presentation",
        lambda db, presentation_id, user_id: presentation_record,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "get_file",
        lambda db, file_id, user_id: source_record if file_id == "deck-1" else None,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "validate_slide_presentation_asset_file_ids",
        lambda db, user_id, file_ids: list(file_ids),
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "render_slide_presentation",
        lambda **kwargs: {"file_id": "pptx-1", "slide_count": 2},
    )

    def fake_upload(**kwargs):
        presentation_dir = Path(kwargs["presentation_dir"])
        captured["title"] = (presentation_dir / "title.txt").read_text(encoding="utf-8")
        captured["metadata"] = json.loads(
            (presentation_dir / "metadata.json").read_text(encoding="utf-8")
        )
        return {
            "provider": "webdav",
            "storage_prefix": "user-1/presentations/deck-1",
        }

    monkeypatch.setattr(
        presentation_pipeline, "upload_presentation_artifacts", fake_upload
    )
    monkeypatch.setattr(
        presentation_pipeline, "upsert_slide_presentation", lambda *args, **kwargs: None
    )

    def fake_update_source_meta(db, file_id, user_id, **updates):
        captured.setdefault("statuses", []).append(
            updates.get("presentation_render_status")
        )

    monkeypatch.setattr(
        presentation_pipeline, "_update_source_meta", fake_update_source_meta
    )

    runner = presentation_pipeline.rerender_presentation_source(
        db=db,
        user_id="user-1",
        html_file_id="deck-1",
        html=VALID_DECK,
    )
    while True:
        try:
            next(runner)
        except StopIteration as completed:
            result = completed.value
            break

    assert captured["title"] == "Quarterly plan"
    assert captured["metadata"] == {
        "title": "Quarterly plan",
        "slide_count": 2,
        "html_file_id": "deck-1",
        "brief_file_id": "brief-1",
        "asset_file_ids": ["logo-1"],
        "render_revision": 6,
    }
    assert source_record.meta["presentation_render_status"] == "ready"
    assert result["operation"] == "updated"


def test_rendering_status_transition_does_not_overwrite_a_newer_revision():
    """A stale render cannot replace a newer source's stale status."""
    source_record = SimpleNamespace(
        meta={"canvas_revision": 5, "presentation_render_status": "stale"}
    )

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return source_record

    class FakeDB:
        def query(self, model):
            return FakeQuery()

        def rollback(self):
            return None

    with pytest.raises(
        presentation_pipeline.PresentationRevisionConflict,
        match="changed before rendering began",
    ):
        presentation_pipeline._mark_rendering_if_current(
            FakeDB(), "user-1", "deck-1", 4
        )

    assert source_record.meta == {
        "canvas_revision": 5,
        "presentation_render_status": "stale",
    }


def test_rerender_disconnect_marks_current_revision_failed(tmp_path, monkeypatch):
    """Closing at the first progress event must not leave rendering stuck."""
    source_record = SimpleNamespace(meta={"canvas_revision": 4})
    presentation_record = SimpleNamespace(
        title="Quarterly plan",
        file_id=None,
        storage_provider="local",
        storage_prefix="user-1/presentations/deck-1/revisions/old",
        slide_count=2,
    )

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return source_record

    class FakeDB:
        def query(self, model):
            return FakeQuery()

        def rollback(self):
            return None

        def add(self, value):
            return None

        def commit(self):
            return None

    monkeypatch.setattr(presentation_pipeline, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(
        presentation_pipeline,
        "get_slide_presentation",
        lambda *args: presentation_record,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "get_file",
        lambda *args: source_record,
    )
    monkeypatch.setattr(
        presentation_pipeline,
        "validate_slide_presentation_asset_file_ids",
        lambda *args: [],
    )

    runner = presentation_pipeline.rerender_presentation_source(
        db=FakeDB(),
        user_id="user-1",
        html_file_id="deck-1",
        html=VALID_DECK,
    )
    assert '"phase": "rendering"' in next(runner)
    runner.close()

    assert source_record.meta["presentation_render_status"] == "failed"
    assert not list(tmp_path.glob("user-1/presentations/.deck-1-render-*"))


def test_canvas_presentation_edit_persists_assets_before_rerender(monkeypatch):
    """A Canvas logo edit must authorize, inline, persist, and rerender one asset bundle."""
    from app.files import models as file_models
    from app.tools.slide_presentation import sanitizer

    class FakeDB:
        def add(self, value):
            return None

        def commit(self):
            return None

        def refresh(self, value):
            return None

    deck_record = SimpleNamespace(
        id="deck-1",
        meta={"slide_presentation_source": True},
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        tool_helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        tool_helper, "stage_tool_audit_action", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        file_models, "get_file", lambda db, file_id, user_id: deck_record
    )
    monkeypatch.setattr(
        sanitizer,
        "validate_slide_presentation_asset_file_ids",
        lambda db, user_id, file_ids: list(file_ids),
    )
    monkeypatch.setattr(
        sanitizer,
        "prepare_slide_presentation_html",
        lambda html, **kwargs: html.replace(
            "omlorix-file://logo-1", "data:image/png;base64,bG9nbw=="
        ),
    )

    def fake_save_canvas_markdown(**kwargs):
        transformed = kwargs["content_transformer"](kwargs["content"])
        kwargs["content_validator"](transformed)
        captured["saved_html"] = transformed
        result = {
            "file_id": "deck-1",
            "file_name": "deck.html",
            "content": transformed,
            "content_type": "html",
            "created": False,
            "page_count": 1,
            "canvas_revision": 2,
        }
        kwargs["before_commit"](
            {
                "file_id": "deck-1",
                "created": False,
                "content_type": "html",
                "canvas_revision": 2,
                "asset_count": 1,
                "pending_asset_approval_count": 0,
            }
        )
        return result

    monkeypatch.setattr(tool_helper, "save_canvas_markdown", fake_save_canvas_markdown)
    monkeypatch.setattr(sanitizer, "validate_slide_presentation_html", lambda html: 2)

    def fake_rerender(**kwargs):
        captured["rerender_html"] = kwargs["html"]
        yield (
            json.dumps(
                {
                    "t": "slide_presentation_evt",
                    "event": "progress",
                    "data": {"percent": 80},
                }
            )
            + "\n"
        )
        yield (
            json.dumps(
                {
                    "t": "slide_presentation_evt",
                    "event": "complete",
                    "data": {"presentation_id": "deck-1"},
                }
            )
            + "\n"
        )
        return {
            "presentation_id": "deck-1",
            "file_id": "pptx-1",
            "title": "Quarterly plan",
            "slide_count": 2,
        }

    monkeypatch.setattr(
        presentation_pipeline, "rerender_presentation_source", fake_rerender
    )

    runner = tool_helper.resolve_tool_call(
        FakeDB(),
        "canvas",
        {
            "type": "html",
            "file_id": "deck-1",
            "content": VALID_DECK.replace(
                "<h1>One</h1>",
                '<img src="omlorix-file://logo-1"><h1>One</h1>',
            ),
            "file_ids": ["logo-1"],
        },
        "user-1",
        None,
        None,
    )
    emitted_events = []
    while True:
        try:
            emitted_events.append(json.loads(next(runner)))
        except StopIteration:
            break

    assert "omlorix-file://" not in captured["saved_html"]
    assert captured["rerender_html"] == captured["saved_html"]
    assert deck_record.meta["slide_presentation_asset_file_ids"] == ["logo-1"]
    assert [
        event["event"]
        for event in emitted_events
        if event.get("t") == "slide_presentation_evt"
    ] == ["progress"]
    canvas_events = [
        event for event in emitted_events if event.get("t") == "canvas_evt"
    ]
    assert len(canvas_events) == 1
    assert canvas_events[0]["data"]["artifact_kind"] == "slide_presentation"
