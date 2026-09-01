import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.tools import helper as tool_helper
from app.tools.latex_pdf import utils as latex_utils


def _latex_render_zip(
    tex_source="\\documentclass{article}\\begin{document}Hi\\end{document}",
    *,
    pdf_bytes=b"%PDF-1.4\nfake\n",
    log_text="compile ok",
    metadata_text='{"compiler":"pdflatex","execution_time":1.25}',
):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("output/main.pdf", pdf_bytes)
        archive.writestr("source/main.tex", tex_source)
        archive.writestr("logs/pdflatex.log", log_text)
        archive.writestr("metadata.json", metadata_text)
    return buffer.getvalue()


class _FakeDb:
    def add(self, _record):
        pass

    def commit(self):
        pass

    def refresh(self, _record):
        pass


def _drain_tool_generator(generator):
    """Consume a streaming tool helper and return its final payload."""
    streamed_items = []
    try:
        while True:
            streamed_items.append(next(generator))
    except StopIteration as done:
        return done.value, streamed_items


class _FakeStreamResponse:
    """Small context-managed response used to exercise the real stream reader."""

    def __init__(self, chunks, *, status_code=200, headers=None):
        self._chunks = list(chunks)
        self.status_code = status_code
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def iter_bytes(self):
        yield from self._chunks


class _FakeStreamClient:
    """Record stream requests while returning deterministic response objects."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._responses.pop(0)


def test_post_latex_render_streams_a_normal_response():
    payload = _latex_render_zip()
    client = _FakeStreamClient(
        [
            _FakeStreamResponse(
                [payload[:11], payload[11:]],
                headers={"Content-Length": str(len(payload)), "X-LaTeX-Compiler": "pdflatex"},
            )
        ]
    )

    response = latex_utils._post_latex_render(
        client,
        "https://renderer.example",
        {"Authorization": "Bearer secret"},
        {"tex": "valid"},
    )

    assert response.content == payload
    assert response.headers["X-LaTeX-Compiler"] == "pdflatex"
    assert client.calls[0][0:2] == ("POST", "https://renderer.example/api/latex/render")


def test_post_latex_render_rejects_oversized_declared_response(monkeypatch):
    monkeypatch.setattr(latex_utils, "LATEX_RENDER_MAX_RESPONSE_BYTES", 16)
    response = _FakeStreamResponse(
        [b"must not be consumed"],
        headers={"Content-Length": "17"},
    )
    client = _FakeStreamClient([response])

    with pytest.raises(latex_utils.LatexRenderOutputLimitError, match="response exceeds"):
        latex_utils._post_latex_render(client, "https://renderer.example", {}, {"tex": "valid"})


def test_post_latex_render_rejects_chunked_response_over_limit(monkeypatch):
    """A missing or dishonest Content-Length must not bypass the streamed limit."""
    monkeypatch.setattr(latex_utils, "LATEX_RENDER_MAX_RESPONSE_BYTES", 8)
    client = _FakeStreamClient([_FakeStreamResponse([b"1234", b"5678", b"9"])])

    with pytest.raises(latex_utils.LatexRenderOutputLimitError, match="response exceeds"):
        latex_utils._post_latex_render(client, "https://renderer.example", {}, {"tex": "valid"})


def test_extract_latex_bundle_rejects_oversized_pdf_before_read(monkeypatch):
    """A highly compressed PDF must be rejected from ZipInfo before allocation."""
    monkeypatch.setattr(latex_utils, "LATEX_RENDER_MAX_PDF_BYTES", 1024)
    payload = _latex_render_zip(pdf_bytes=b"A" * 4096)

    with pytest.raises(latex_utils.LatexRenderOutputLimitError, match="PDF exceeds"):
        latex_utils._extract_latex_bundle(payload)


def test_extract_latex_bundle_rejects_realistic_compressed_pdf_bomb():
    """A tiny ZIP expanding beyond the production PDF cap is rejected safely."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open("output/main.pdf", "w") as pdf:
            one_megabyte = b"A" * (1024 * 1024)
            for _ in range(51):
                pdf.write(one_megabyte)
    payload = buffer.getvalue()

    # The container stays tiny, while its declared PDF expands past the real
    # 50 MiB production limit that protects the backend allocation.
    assert len(payload) < 1024 * 1024
    with pytest.raises(latex_utils.LatexRenderOutputLimitError, match="PDF exceeds"):
        latex_utils._extract_latex_bundle(payload)


def test_extract_latex_bundle_rejects_oversized_log(monkeypatch):
    """Ancillary renderer files are bounded even though only an excerpt is returned."""
    monkeypatch.setattr(latex_utils, "LATEX_RENDER_MAX_LOG_BYTES", 128)
    payload = _latex_render_zip(log_text="log line\n" * 100)

    with pytest.raises(latex_utils.LatexRenderOutputLimitError, match="compile log exceeds"):
        latex_utils._extract_latex_bundle(payload)


def test_extract_latex_bundle_rejects_entry_flood_before_zipfile_open(monkeypatch):
    """The central-directory entry count is checked before ZipInfo allocation."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("output/main.pdf", b"%PDF-1.4\n")
        for index in range(4):
            archive.writestr(f"extra/{index}.txt", b"")
    payload = buffer.getvalue()

    monkeypatch.setattr(latex_utils, "LATEX_RENDER_MAX_ARCHIVE_ENTRIES", 4)

    def fail_if_opened(*args, **kwargs):
        raise AssertionError("The oversized entry table must be rejected before ZipFile opens it")

    monkeypatch.setattr(latex_utils.zipfile, "ZipFile", fail_if_opened)

    with pytest.raises(latex_utils.LatexRenderOutputLimitError, match="too many entries"):
        latex_utils._extract_latex_bundle(payload)


def test_archive_member_read_enforces_actual_bytes_when_metadata_is_wrong():
    """The streaming member limit remains authoritative over declared file_size."""
    info = SimpleNamespace(file_size=1)

    class Archive:
        @staticmethod
        def open(_info, _mode):
            return io.BytesIO(b"123456789")

    with pytest.raises(latex_utils.LatexRenderOutputLimitError, match="PDF exceeds"):
        latex_utils._read_latex_archive_member(
            Archive(),
            info,
            max_bytes=8,
            label="PDF",
        )


def test_latex_snippet_update_replaces_exact_inclusive_range():
    source = "before\n\\section{Old}\nold body\nafter"

    updated = latex_utils._apply_snippet_update(
        source,
        start_snippet="\\section{Old}",
        end_snippet="old body",
        replacement_content="\\section{New}\nnew body",
    )

    assert updated == "before\n\\section{New}\nnew body\nafter"


def test_latex_snippet_update_rejects_ambiguous_start():
    with pytest.raises(ValueError, match="matched more than once"):
        latex_utils._apply_snippet_update(
            "repeat\nmiddle\nrepeat",
            start_snippet="repeat",
            end_snippet="middle",
            replacement_content="new",
        )


def test_latex_input_files_are_sanitized_and_deduplicated():
    files = latex_utils._build_latex_input_files(
        [
            {"name": "../logo.png", "content": "aaa"},
            {"name": "logo.png", "content": "bbb"},
            {"name": "figures/chart.pdf", "content": "ccc"},
        ]
    )

    assert [item["file_name"] for item in files] == ["logo.png", "logo-1.png", "chart.pdf"]
    assert [item["base64_content"] for item in files] == ["aaa", "bbb", "ccc"]


def test_render_latex_pdf_saves_source_pdf_and_asset_metadata(monkeypatch):
    saved_records = []
    checked_file_sizes = []
    audit_calls = []

    def fake_persist_generated_file_bytes(db, **kwargs):
        # Both newly generated records must use the resolved user quotas. Omitting
        # either value would restore the vulnerable quota-free persistence path.
        assert kwargs["max_files_limit"] == 100
        assert kwargs["max_user_storage_limit_bytes"] == 5 * 1024**3
        record = SimpleNamespace(
            id=kwargs["file_id"],
            file_name=kwargs.get("file_name") or kwargs["original_filename"],
            file_type=kwargs["file_type"],
            file_category=kwargs["file_category"],
            file_size=len(kwargs["file_bytes"]),
            meta=kwargs["meta"],
        )
        saved_records.append(record)
        if kwargs.get("before_commit") is not None:
            kwargs["before_commit"](record)
        return record

    captured_payloads = []

    def fake_post_latex_render(client, base_url, headers, request_payload):
        captured_payloads.append(request_payload)
        return SimpleNamespace(
            content=_latex_render_zip(),
            headers={"X-LaTeX-Compiler": "pdflatex"},
        )

    monkeypatch.setattr(
        latex_utils,
        "get_service_connection_candidates",
        lambda db, purpose: [{"id": "svc-1", "name": "Code", "base_url": "http://code.local"}],
    )
    monkeypatch.setattr(latex_utils, "_connection_headers", lambda connection: {})
    monkeypatch.setattr(latex_utils, "assert_url_allowed", lambda db, url, feature: None)
    monkeypatch.setattr(latex_utils, "_check_service_health", lambda client, base_url, headers: None)
    monkeypatch.setattr(latex_utils, "record_service_connection_runtime_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(latex_utils, "_post_latex_render", fake_post_latex_render)
    monkeypatch.setattr(
        latex_utils,
        "resolve_user_file_upload_limits",
        lambda db, user_id: (100, 5 * 1024**3),
    )
    monkeypatch.setattr(
        latex_utils,
        "ensure_user_file_upload_size_limit",
        lambda db, user_id, file_size: checked_file_sizes.append(file_size),
    )
    monkeypatch.setattr(latex_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        latex_utils,
        "_prepare_input_files_payload",
        lambda db, user_id, file_ids: [{"name": "logo.png", "content": "base64-logo"}],
    )
    monkeypatch.setattr(latex_utils, "persist_generated_file_bytes", fake_persist_generated_file_bytes)
    monkeypatch.setattr(
        latex_utils,
        "stage_tool_audit_action",
        lambda db, user_id, action, **kwargs: audit_calls.append(
            {"user_id": user_id, "action": action, **kwargs}
        ),
    )

    result = latex_utils.render_latex_pdf(
        _FakeDb(),
        user_id="user-1",
        tex="\\documentclass{article}\\begin{document}Hi\\end{document}",
        title="Report",
        file_ids=["asset-1"],
        audit_tool_mutations=True,
    )

    assert result["file_id"] == saved_records[1].id
    assert result["source_file_id"] == saved_records[0].id
    assert result["title"] == "Report"
    assert result["source_file_name"] == "Report.tex"
    assert result["input_file_names"] == ["logo.png"]
    assert result["asset_file_ids"] == ["asset-1"]
    assert captured_payloads[0]["input_files"][0]["file_name"] == "logo.png"
    assert saved_records[0].file_type == "text/x-tex"
    assert saved_records[0].meta["latex_source"] is True
    assert saved_records[0].meta["latex_display_title"] == "Report"
    assert saved_records[0].meta["title"] == "Report"
    assert saved_records[0].meta["latex_pdf_file_name"] == "Report.pdf"
    assert saved_records[1].file_type == "application/pdf"
    assert saved_records[1].meta["latex_source_file_id"] == saved_records[0].id
    assert saved_records[1].meta["latex_display_title"] == "Report"
    assert saved_records[1].meta["title"] == "Report"
    assert saved_records[1].meta["latex_input_file_names"] == ["logo.png"]
    assert checked_file_sizes == [
        len("\\documentclass{article}\\begin{document}Hi\\end{document}".encode("utf-8")),
        len(b"%PDF-1.4\nfake\n"),
        len("\\documentclass{article}\\begin{document}Hi\\end{document}".encode("utf-8")),
    ]
    assert audit_calls == [
        {
            "user_id": "user-1",
            "action": "LATEX_SOURCE_CREATED",
            "category": "files",
            "details": {
                "source_file_id": str(saved_records[0].id),
                "compile_failed": False,
            },
        },
        {
            "user_id": "user-1",
            "action": "LATEX_PDF_CREATED",
            "category": "files",
            "details": {
                "source_file_id": str(saved_records[0].id),
                "pdf_file_id": str(saved_records[1].id),
                "source_revision": result["source_revision"],
            },
        },
    ]
    assert "\\documentclass" not in repr(audit_calls)
    assert "Report" not in repr(audit_calls)


def test_render_latex_pdf_saves_failed_source_attempt(monkeypatch):
    saved_records = []
    audit_calls = []

    def fake_persist_generated_file_bytes(db, **kwargs):
        assert kwargs["max_files_limit"] == 100
        assert kwargs["max_user_storage_limit_bytes"] == 5 * 1024**3
        record = SimpleNamespace(
            id=kwargs["file_id"],
            file_name=kwargs.get("file_name") or kwargs["original_filename"],
            file_type=kwargs["file_type"],
            file_category=kwargs["file_category"],
            file_size=len(kwargs["file_bytes"]),
            meta=kwargs["meta"],
        )
        saved_records.append(record)
        if kwargs.get("before_commit") is not None:
            kwargs["before_commit"](record)
        return record

    def fake_post_latex_render(client, base_url, headers, request_payload):
        raise latex_utils.LatexServiceRenderError(
            status_code=422,
            detail="pdflatex failed",
            log_excerpt="./main.tex:32: Missing \\begin{document}",
        )

    monkeypatch.setattr(
        latex_utils,
        "get_service_connection_candidates",
        lambda db, purpose: [{"id": "svc-1", "name": "Code", "base_url": "http://code.local"}],
    )
    monkeypatch.setattr(latex_utils, "_connection_headers", lambda connection: {})
    monkeypatch.setattr(latex_utils, "assert_url_allowed", lambda db, url, feature: None)
    monkeypatch.setattr(latex_utils, "_check_service_health", lambda client, base_url, headers: None)
    monkeypatch.setattr(latex_utils, "record_service_connection_runtime_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(latex_utils, "_post_latex_render", fake_post_latex_render)
    monkeypatch.setattr(
        latex_utils,
        "resolve_user_file_upload_limits",
        lambda db, user_id: (100, 5 * 1024**3),
    )
    monkeypatch.setattr(latex_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(latex_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None)
    monkeypatch.setattr(latex_utils, "_prepare_input_files_payload", lambda db, user_id, file_ids: [])
    monkeypatch.setattr(latex_utils, "persist_generated_file_bytes", fake_persist_generated_file_bytes)
    monkeypatch.setattr(
        latex_utils,
        "stage_tool_audit_action",
        lambda db, user_id, action, **kwargs: audit_calls.append(
            {"user_id": user_id, "action": action, **kwargs}
        ),
    )

    with pytest.raises(latex_utils.LatexCompileError) as exc_info:
        latex_utils.render_latex_pdf(
            _FakeDb(),
            user_id="user-1",
            tex="\\documentclass{article}\nby Confidential Author",
            title="Broken",
            audit_tool_mutations=True,
        )

    assert saved_records
    assert exc_info.value.source_file_id == saved_records[0].id
    assert saved_records[0].file_type == "text/x-tex"
    assert saved_records[0].meta["latex_display_title"] == "Broken"
    assert saved_records[0].meta["title"] == "Broken"
    assert saved_records[0].meta["latex_compile_failed"] is True
    assert "Missing \\begin{document}" in saved_records[0].meta["latex_log_excerpt"]
    assert audit_calls == [
        {
            "user_id": "user-1",
            "action": "LATEX_SOURCE_CREATED",
            "category": "files",
            "details": {
                "source_file_id": str(saved_records[0].id),
                "compile_failed": True,
            },
        }
    ]
    assert "Confidential Author" not in repr(audit_calls)
    assert "Missing" not in repr(audit_calls)


def test_render_latex_pdf_removes_new_source_when_pdf_persistence_fails(monkeypatch):
    """A rejected PDF must not leave its newly created source consuming quota."""
    source_record = SimpleNamespace(
        id="source-id",
        file_name="Report.tex",
        file_type="text/x-tex",
        file_category="document",
        file_size=20,
        meta={"latex_source": True},
    )
    deleted_sources = []
    audit_calls = []

    def fake_persist_generated_file_bytes(_db, **kwargs):
        if kwargs["file_type"] == "text/x-tex":
            kwargs["before_commit"](source_record)
            return source_record
        raise HTTPException(status_code=400, detail="Maximum storage quota reached")

    def fake_delete_file(user_id, file_id, _db, time_option):
        deleted_sources.append((user_id, file_id, time_option))

    monkeypatch.setattr(
        latex_utils,
        "get_service_connection_candidates",
        lambda db, purpose: [{"id": "svc-1", "name": "Code", "base_url": "http://code.local"}],
    )
    monkeypatch.setattr(latex_utils, "_connection_headers", lambda connection: {})
    monkeypatch.setattr(latex_utils, "assert_url_allowed", lambda db, url, feature: None)
    monkeypatch.setattr(latex_utils, "_check_service_health", lambda client, base_url, headers: None)
    monkeypatch.setattr(latex_utils, "record_service_connection_runtime_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        latex_utils,
        "_post_latex_render",
        lambda **_kwargs: SimpleNamespace(
            content=_latex_render_zip(),
            headers={"X-LaTeX-Compiler": "pdflatex"},
        ),
    )
    monkeypatch.setattr(latex_utils, "resolve_user_file_upload_limits", lambda db, user_id: (100, 1024))
    monkeypatch.setattr(latex_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(latex_utils, "ensure_user_file_upload_capacity", lambda *args, **kwargs: None)
    monkeypatch.setattr(latex_utils, "_prepare_input_files_payload", lambda db, user_id, file_ids: [])
    monkeypatch.setattr(latex_utils, "persist_generated_file_bytes", fake_persist_generated_file_bytes)
    monkeypatch.setattr(latex_utils, "delete_file", fake_delete_file)
    monkeypatch.setattr(
        latex_utils,
        "stage_tool_audit_action",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)),
    )

    with pytest.raises(HTTPException, match="Maximum storage quota reached"):
        latex_utils.render_latex_pdf(
            _FakeDb(),
            user_id="user-1",
            tex="\\documentclass{article}\\begin{document}Hi\\end{document}",
            title="Report",
            audit_tool_mutations=True,
        )

    assert deleted_sources == [
        ("user-1", "source-id", latex_utils.FileDeleteTimeOption.ALL)
    ]
    assert len(audit_calls) == 1
    audit_args, audit_kwargs = audit_calls[0]
    assert audit_args[1:] == ("user-1", "LATEX_SOURCE_CREATED")
    assert audit_kwargs == {
        "category": "files",
        "details": {
            "source_file_id": "source-id",
            "compile_failed": False,
        },
    }


def test_render_latex_pdf_audits_retained_source_update_when_pdf_save_fails(
    monkeypatch,
):
    source_record = SimpleNamespace(
        id="source-id",
        file_name="Report.tex",
        file_type="text/x-tex",
        folder_id=None,
        project_id=None,
        meta={"latex_source": True, "canvas_revision": 4},
    )
    audit_calls = []
    monkeypatch.setattr(latex_utils, "get_file", lambda *_args: source_record)
    monkeypatch.setattr(
        latex_utils,
        "get_service_connection_candidates",
        lambda *_args: [
            {"id": "svc-1", "name": "Code", "base_url": "http://code.local"}
        ],
    )
    monkeypatch.setattr(latex_utils, "_connection_headers", lambda _connection: {})
    monkeypatch.setattr(latex_utils, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(latex_utils, "_check_service_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        latex_utils,
        "record_service_connection_runtime_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        latex_utils,
        "_post_latex_render",
        lambda *_args, **_kwargs: SimpleNamespace(
            content=_latex_render_zip(),
            headers={"X-LaTeX-Compiler": "pdflatex"},
        ),
    )
    monkeypatch.setattr(
        latex_utils,
        "resolve_user_file_upload_limits",
        lambda *_args: (100, 5 * 1024**3),
    )
    monkeypatch.setattr(
        latex_utils,
        "ensure_user_file_upload_size_limit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        latex_utils,
        "_prepare_input_files_payload",
        lambda *_args, **_kwargs: [],
    )
    def persist_updated_source(*_args, **kwargs):
        if kwargs.get("before_commit") is not None:
            kwargs["before_commit"](source_record)
        return source_record

    monkeypatch.setattr(
        latex_utils,
        "_persist_latex_source_attempt",
        persist_updated_source,
    )
    monkeypatch.setattr(
        latex_utils,
        "persist_generated_file_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=400, detail="Maximum storage quota reached")
        ),
    )
    monkeypatch.setattr(
        latex_utils,
        "stage_tool_audit_action",
        lambda db, user_id, action, **kwargs: audit_calls.append(
            {"user_id": user_id, "action": action, **kwargs}
        ),
    )

    with pytest.raises(HTTPException, match="Maximum storage quota reached"):
        latex_utils.render_latex_pdf(
            _FakeDb(),
            user_id="user-1",
            source_file_id="source-id",
            tex="\\documentclass{article}\\begin{document}Updated\\end{document}",
            title="Report",
            audit_tool_mutations=True,
        )

    assert audit_calls == [
        {
            "user_id": "user-1",
            "action": "LATEX_SOURCE_UPDATED",
            "category": "files",
            "details": {
                "source_file_id": "source-id",
                "compile_failed": False,
            },
        }
    ]
    assert "Updated" not in repr(audit_calls)


def test_render_latex_pdf_rejects_exhausted_storage_before_rendering(monkeypatch):
    """An over-quota fresh source must never reach the renderer or persistence."""
    renderer_called = False
    persistence_called = False

    def fake_post_latex_render(*args, **kwargs):
        nonlocal renderer_called
        renderer_called = True
        raise AssertionError("The renderer must not run after quota admission fails")

    def fake_persist_generated_file_bytes(*args, **kwargs):
        nonlocal persistence_called
        persistence_called = True
        raise AssertionError("An over-quota source must not be persisted")

    def reject_capacity(*args, **kwargs):
        raise HTTPException(status_code=400, detail="Maximum storage quota reached")

    monkeypatch.setattr(
        latex_utils,
        "get_service_connection_candidates",
        lambda db, purpose: [{"id": "svc-1", "name": "Code", "base_url": "http://code.local"}],
    )
    monkeypatch.setattr(latex_utils, "_prepare_input_files_payload", lambda db, user_id, file_ids: [])
    monkeypatch.setattr(latex_utils, "resolve_user_file_upload_limits", lambda db, user_id: (100, 0))
    monkeypatch.setattr(latex_utils, "ensure_user_file_upload_size_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(latex_utils, "ensure_user_file_upload_capacity", reject_capacity)
    monkeypatch.setattr(latex_utils, "_post_latex_render", fake_post_latex_render)
    monkeypatch.setattr(latex_utils, "persist_generated_file_bytes", fake_persist_generated_file_bytes)

    with pytest.raises(HTTPException) as exc_info:
        latex_utils.render_latex_pdf(
            _FakeDb(),
            user_id="user-1",
            tex="\\documentclass{article}\\begin{document}Hi\\end{document}",
            title="No storage",
        )

    assert exc_info.value.detail == "Maximum storage quota reached"
    assert renderer_called is False
    assert persistence_called is False


def test_latex_storage_quota_failure_is_returned_to_the_model(monkeypatch):
    """Expected storage exhaustion must be a specific model-visible tool result."""
    render_kwargs = {}

    def fail_render(*_args, **kwargs):
        render_kwargs.update(kwargs)
        raise HTTPException(status_code=400, detail="Maximum storage quota reached")

    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        latex_utils,
        "render_latex_pdf",
        fail_render,
    )
    monkeypatch.setattr(
        "app.llmstats.models.create_tool_call_statistic",
        lambda *args, **kwargs: None,
    )

    payload, streamed_items = _drain_tool_generator(
        tool_helper.resolve_tool_call(
            db=object(),
            tool_name="latex_pdf",
            tool_arguments={"tex": "\\documentclass{article}"},
            user_id="user-1",
            group_id=None,
            project_id=None,
        )
    )

    model_result = json.loads(payload["content"])
    assert model_result == payload["result"]
    assert model_result["code"] == "user_file_storage_quota_reached"
    assert model_result["saved"] is False
    assert "not saved" in model_result["message"]
    assert "no file storage remaining" in model_result["message"]
    assert payload["tool_meta"]["execution_error"] is True
    assert payload["tool_meta"]["save_failed"] is True

    error_events = [
        json.loads(item)
        for item in streamed_items
        if json.loads(item).get("event") == "error"
    ]
    assert error_events[0]["data"]["code"] == "user_file_storage_quota_reached"
    assert render_kwargs["audit_tool_mutations"] is True


def test_latex_internal_persistence_failure_is_not_exposed_in_stream(monkeypatch):
    monkeypatch.setattr(
        tool_helper,
        "_admit_tool_invocation_or_payload",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        latex_utils,
        "render_latex_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit outbox unavailable at internal-db-host")
        ),
    )
    monkeypatch.setattr(
        "app.llmstats.models.create_tool_call_statistic",
        lambda *args, **kwargs: None,
    )

    streamed_items = []
    generator = tool_helper.resolve_tool_call(
        db=object(),
        tool_name="latex_pdf",
        tool_arguments={"tex": "\\documentclass{article}"},
        user_id="user-1",
        group_id=None,
        project_id=None,
    )
    with pytest.raises(RuntimeError, match="audit outbox unavailable"):
        while True:
            streamed_items.append(next(generator))

    serialized = "".join(streamed_items)
    assert "audit outbox unavailable" not in serialized
    assert "internal-db-host" not in serialized
    error_events = [
        json.loads(item)
        for item in streamed_items
        if json.loads(item).get("event") == "error"
    ]
    assert error_events[0]["data"]["message"] == "An error occurred during tool execution."


def test_canvas_latex_render_rejects_stale_revision_before_compilation(monkeypatch):
    """An old preview request must not spend renderer capacity or replace the PDF."""
    source = SimpleNamespace(
        id="source-1",
        file_name="report.tex",
        file_type="text/x-tex",
        meta={"canvas": True, "canvas_type": "latex", "canvas_revision": 9},
    )
    monkeypatch.setattr(latex_utils, "get_file", lambda *args, **kwargs: source)
    monkeypatch.setattr(
        latex_utils,
        "get_service_connection_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("renderer lookup must not run")),
    )

    with pytest.raises(latex_utils.LatexSourceRevisionConflict) as exc_info:
        latex_utils.render_latex_canvas(
            object(),
            user_id="user-1",
            source_file_id="source-1",
            expected_revision=8,
        )

    assert exc_info.value.expected_revision == 8
    assert exc_info.value.current_revision == 9


@pytest.mark.parametrize("audit_fails", [False, True])
def test_uploaded_latex_canvas_adoption_stages_actor_audit_in_commit(
    monkeypatch,
    audit_fails,
):
    source = SimpleNamespace(
        id="source-1",
        file_name="uploaded.tex",
        file_type="text/x-tex",
        folder_id="shared-folder-1",
        project_id=None,
        meta={"original_filename": "uploaded.tex"},
    )
    original_meta = dict(source.meta)
    events = []

    class TrackingDb:
        commit_count = 0
        rollback_count = 0

        def add(self, _record):
            events.append("add")

        def flush(self):
            events.append("flush")

        def commit(self):
            self.commit_count += 1
            events.append("commit")

        def rollback(self):
            self.rollback_count += 1
            source.meta = dict(original_meta)
            events.append("rollback")

        def refresh(self, _record):
            pass

    db = TrackingDb()
    render_calls = []
    audit_calls = []
    monkeypatch.setattr(latex_utils, "get_file", lambda *_args, **_kwargs: source)

    def stage_audit(_db, **kwargs):
        audit_calls.append(kwargs)
        events.append("audit")
        if audit_fails:
            raise RuntimeError("audit outbox unavailable")

    monkeypatch.setattr(latex_utils, "stage_audit_log_event", stage_audit)
    monkeypatch.setattr(
        latex_utils,
        "render_latex_pdf",
        lambda _db, **kwargs: render_calls.append(kwargs) or {"file_id": "pdf-1"},
    )

    def render():
        return latex_utils.render_latex_canvas(
            db,
            user_id="owner-1",
            asset_actor_user_id="collaborator-1",
            source_file_id="source-1",
            audit_ip_address="203.0.113.14",
            audit_user_agent="pytest-collaborator",
        )

    if audit_fails:
        with pytest.raises(RuntimeError, match="audit outbox unavailable"):
            render()
        assert db.commit_count == 0
        assert db.rollback_count == 1
        assert source.meta == original_meta
        assert render_calls == []
        assert events == ["add", "flush", "audit", "rollback"]
    else:
        assert render() == {"file_id": "pdf-1"}
        assert db.commit_count == 1
        assert db.rollback_count == 0
        assert events == ["add", "flush", "audit", "commit"]
        context = render_calls[0]["canvas_audit_context"]
        assert context.actor_user_id == "collaborator-1"
        assert context.ip_address == "203.0.113.14"
        assert context.user_agent == "pytest-collaborator"

    assert audit_calls == [
        {
            "user_id": "collaborator-1",
            "action": "CANVAS_LATEX_ADOPTED",
            "reason": None,
            "details": {
                "source_file_id": "source-1",
                "source_revision": 1,
            },
            "ip_address": "203.0.113.14",
            "user_agent": "pytest-collaborator",
            "category": "files",
        }
    ]
    assert "uploaded.tex" not in repr(audit_calls)


@pytest.mark.parametrize("audit_fails", [False, True])
def test_canvas_compile_failure_state_and_audit_are_atomic(monkeypatch, audit_fails):
    source = SimpleNamespace(
        id="source-1",
        file_name="report.tex",
        file_type="text/x-tex",
        folder_id="shared-folder-1",
        project_id=None,
        meta={
            "canvas": True,
            "canvas_type": "latex",
            "canvas_revision": 5,
            "latex_render_status": "stale",
        },
    )
    original_meta = dict(source.meta)
    events = []

    class TrackingDb:
        commit_count = 0
        rollback_count = 0

        def add(self, _record):
            events.append("add")

        def flush(self):
            events.append("flush")

        def commit(self):
            self.commit_count += 1
            events.append("commit")

        def rollback(self):
            self.rollback_count += 1
            source.meta = dict(original_meta)
            events.append("rollback")

        def refresh(self, _record):
            pass

    db = TrackingDb()
    audit_calls = []
    monkeypatch.setattr(latex_utils, "get_file", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        latex_utils,
        "_read_latex_source_file",
        lambda *_args, **_kwargs: "\\documentclass{article}\\begin{document}Hi\\end{document}",
    )
    monkeypatch.setattr(
        latex_utils,
        "prepare_canvas_asset_files_payload",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        latex_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [
            {"id": "svc-1", "name": "Renderer", "base_url": "http://latex.local"}
        ],
    )
    monkeypatch.setattr(latex_utils, "_connection_headers", lambda _connection: {})
    monkeypatch.setattr(latex_utils, "assert_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(latex_utils, "_check_service_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        latex_utils,
        "record_service_connection_runtime_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        latex_utils,
        "_post_latex_render",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            latex_utils.LatexServiceRenderError(
                status_code=422,
                detail="secret compiler error",
                log_excerpt="source and error text must not enter audit",
            )
        ),
    )
    monkeypatch.setattr(
        latex_utils,
        "resolve_user_file_upload_limits",
        lambda *_args, **_kwargs: (100, 5 * 1024**3),
    )
    monkeypatch.setattr(
        latex_utils,
        "ensure_user_file_upload_size_limit",
        lambda *_args, **_kwargs: None,
    )

    def stage_audit(_db, **kwargs):
        audit_calls.append(kwargs)
        events.append("audit")
        if audit_fails:
            raise RuntimeError("audit outbox unavailable")

    monkeypatch.setattr(latex_utils, "stage_audit_log_event", stage_audit)
    audit_context = latex_utils.LatexCanvasAuditContext(
        actor_user_id="collaborator-1",
        ip_address="203.0.113.15",
        user_agent="pytest-collaborator",
    )

    if audit_fails:
        with pytest.raises(RuntimeError, match="audit outbox unavailable"):
            latex_utils.render_latex_pdf(
                db,
                user_id="owner-1",
                source_file_id="source-1",
                file_ids=["asset-1"],
                persist_source=False,
                expected_source_revision=5,
                canvas_audit_context=audit_context,
            )
        assert source.meta == original_meta
        assert db.commit_count == 0
        assert db.rollback_count == 1
        assert events == ["add", "flush", "audit", "rollback"]
    else:
        with pytest.raises(latex_utils.LatexCompileError):
            latex_utils.render_latex_pdf(
                db,
                user_id="owner-1",
                source_file_id="source-1",
                file_ids=["asset-1"],
                persist_source=False,
                expected_source_revision=5,
                canvas_audit_context=audit_context,
            )
        assert source.meta["latex_render_status"] == "failed"
        assert db.commit_count == 1
        assert db.rollback_count == 0
        assert events == ["add", "flush", "audit", "commit"]

    assert audit_calls == [
        {
            "user_id": "collaborator-1",
            "action": "CANVAS_LATEX_RENDER_FAILED",
            "reason": "compile_failed",
            "details": {
                "source_file_id": "source-1",
                "source_revision": 5,
                "asset_count": 1,
            },
            "ip_address": "203.0.113.15",
            "user_agent": "pytest-collaborator",
            "category": "files",
        }
    ]
    assert "secret compiler error" not in repr(audit_calls)
    assert "source and error text" not in repr(audit_calls)


@pytest.mark.parametrize(
    "outcome",
    ["success", "pdf_persistence_failure", "revision_conflict", "audit_failure"],
)
def test_canvas_latex_render_records_only_terminal_source_states(
    monkeypatch,
    outcome,
):
    """Canvas previews become ready or preserve their prior terminal state."""
    audit_calls = []
    source = SimpleNamespace(
        id="source-1",
        file_name="report.tex",
        file_type="text/x-tex",
        folder_id="shared-folder-1",
        project_id="project-1",
        meta={
            "canvas": True,
            "canvas_type": "latex",
            "canvas_revision": 7,
            "latex_render_status": "stale",
            "latex_pdf_file_id": "",
        },
    )

    class TrackingDb:
        def __init__(self):
            self.commit_count = 0
            self.rollback_count = 0

        def add(self, _record):
            pass

        def commit(self):
            self.commit_count += 1

        def rollback(self):
            self.rollback_count += 1

        def refresh(self, _record):
            pass

    db = TrackingDb()

    monkeypatch.setattr(latex_utils, "get_file", lambda *args, **kwargs: source)
    monkeypatch.setattr(
        latex_utils,
        "_read_latex_source_file",
        lambda *args, **kwargs: "\\documentclass{article}\\begin{document}Hi\\end{document}",
    )
    monkeypatch.setattr(
        latex_utils,
        "get_service_connection_candidates",
        lambda *args, **kwargs: [
            {"id": "svc-1", "name": "Renderer", "base_url": "http://latex.local"}
        ],
    )
    monkeypatch.setattr(latex_utils, "_connection_headers", lambda connection: {})
    monkeypatch.setattr(latex_utils, "assert_url_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(latex_utils, "_check_service_health", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        latex_utils,
        "record_service_connection_runtime_status",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        latex_utils,
        "_post_latex_render",
        lambda *args, **kwargs: SimpleNamespace(
            content=_latex_render_zip(),
            headers={"X-LaTeX-Compiler": "pdflatex"},
        ),
    )
    monkeypatch.setattr(
        latex_utils,
        "resolve_user_file_upload_limits",
        lambda *args, **kwargs: (100, 5 * 1024**3),
    )
    monkeypatch.setattr(
        latex_utils,
        "ensure_user_file_upload_size_limit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        latex_utils,
        "_prepare_input_files_payload",
        lambda *args, **kwargs: [],
    )

    def stage_audit(_db, **kwargs):
        audit_calls.append(kwargs)
        if outcome == "audit_failure":
            raise RuntimeError("audit outbox unavailable")

    monkeypatch.setattr(latex_utils, "stage_audit_log_event", stage_audit)

    def persist_pdf(_db, **kwargs):
        # The render must still be in its prior terminal state when PDF
        # persistence starts; no source metadata commit is allowed beforehand.
        assert source.meta["latex_render_status"] == "stale"
        assert db.commit_count == 0
        # The derivative must share the source's access boundary. Otherwise a
        # collaborator can render successfully but cannot open the returned PDF.
        assert kwargs["folder_id"] == "shared-folder-1"
        assert kwargs["project_id"] == "project-1"
        if outcome == "pdf_persistence_failure":
            raise RuntimeError("PDF persistence failed")
        if outcome == "revision_conflict":
            source.meta = {
                **source.meta,
                "canvas_revision": 8,
                "latex_render_status": "stale",
            }
        pdf_record = SimpleNamespace(id="pdf-1")
        if outcome == "audit_failure":
            prior_meta = dict(source.meta)
            try:
                kwargs["before_commit"](pdf_record)
            except Exception:
                source.meta = prior_meta
                db.rollback()
                raise
        else:
            kwargs["before_commit"](pdf_record)
        db.commit()
        return pdf_record

    monkeypatch.setattr(latex_utils, "persist_generated_file_bytes", persist_pdf)

    render_kwargs = {
        "user_id": "user-1",
        "tex": None,
        "title": "Report",
        "source_file_id": "source-1",
        "persist_source": False,
        "expected_source_revision": 7,
        "canvas_audit_context": latex_utils.LatexCanvasAuditContext(
            actor_user_id="collaborator-1",
            ip_address="203.0.113.12",
            user_agent="pytest",
        ),
    }
    if outcome == "success":
        result = latex_utils.render_latex_pdf(db, **render_kwargs)
        assert result["render_status"] == "ready"
        assert source.meta["latex_render_status"] == "ready"
        assert db.commit_count == 1
        assert db.rollback_count == 0
        assert audit_calls == [
            {
                "user_id": "collaborator-1",
                "action": "CANVAS_LATEX_RENDERED",
                "reason": None,
                "details": {
                    "source_file_id": "source-1",
                    "pdf_file_id": "pdf-1",
                    "source_revision": 7,
                    "asset_count": 0,
                },
                "ip_address": "203.0.113.12",
                "user_agent": "pytest",
                "category": "files",
            }
        ]
        assert "documentclass" not in repr(audit_calls).lower()
    else:
        expected_error = (
            latex_utils.LatexSourceRevisionConflict
            if outcome == "revision_conflict"
            else RuntimeError
        )
        with pytest.raises(expected_error):
            latex_utils.render_latex_pdf(db, **render_kwargs)

        assert source.meta["latex_render_status"] == "stale"
        assert db.commit_count == 0
        if outcome == "audit_failure":
            assert db.rollback_count == 1
            assert len(audit_calls) == 1
            assert audit_calls[0]["action"] == "CANVAS_LATEX_RENDERED"
        else:
            assert db.rollback_count == 0
            assert audit_calls == []


def test_existing_latex_pdf_moves_to_the_source_access_boundary(monkeypatch):
    """Re-rendering repairs a legacy derivative left outside the shared folder."""
    pdf_record = SimpleNamespace(
        id="pdf-1",
        file_name="pdf-1.pdf",
        file_type="application/pdf",
        file_category="document",
        file_size=10,
        folder_id=None,
        project_id=None,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={"latex_pdf": True},
        last_updated_at=None,
    )

    monkeypatch.setattr(
        latex_utils,
        "ensure_user_file_upload_size_limit",
        lambda *args, **kwargs: None,
    )

    def fake_replace(_db, **kwargs):
        record = kwargs["file_record"]
        if kwargs["update_location"]:
            record.folder_id = kwargs["folder_id"]
            record.project_id = kwargs["project_id"]
        return record

    monkeypatch.setattr(
        latex_utils,
        "persist_generated_file_replacement_bytes",
        fake_replace,
    )

    result = latex_utils._overwrite_generated_file_bytes(
        object(),
        user_id="owner-1",
        file_record=pdf_record,
        original_filename="report.pdf",
        file_bytes=b"%PDF-1.4\nupdated\n",
        file_type="application/pdf",
        file_category="document",
        meta={"latex_pdf": True},
        folder_id="shared-folder-1",
        project_id="project-1",
        update_location=True,
    )

    assert result.folder_id == "shared-folder-1"
    assert result.project_id == "project-1"


def test_latex_overwrite_forwards_transactional_audit_callback(monkeypatch):
    """LaTeX replacement delegates its audit callback to atomic persistence."""
    pdf_record = SimpleNamespace(
        id="pdf-1",
        file_name="pdf-1.pdf",
        file_type="application/pdf",
        file_category="document",
        file_size=10,
        folder_id=None,
        project_id=None,
        storage_provider="local",
        storage_key="old-key",
        storage_meta={},
        meta={"latex_pdf": True},
        last_updated_at=None,
    )

    monkeypatch.setattr(
        latex_utils,
        "ensure_user_file_upload_size_limit",
        lambda *args, **kwargs: None,
    )

    captured: dict = {}

    def fake_replace(_db, **kwargs):
        captured.update(kwargs)
        return kwargs["file_record"]

    monkeypatch.setattr(
        latex_utils,
        "persist_generated_file_replacement_bytes",
        fake_replace,
    )

    def stage_audit(_record):
        pass

    latex_utils._overwrite_generated_file_bytes(
        object(),
        user_id="owner-1",
        file_record=pdf_record,
        original_filename="report.pdf",
        file_bytes=b"%PDF-1.4\nupdated\n",
        file_type="application/pdf",
        file_category="document",
        meta={"latex_pdf": True},
        before_commit=stage_audit,
    )

    assert captured["before_commit"] is stage_audit
    assert captured["file_record"] is pdf_record
