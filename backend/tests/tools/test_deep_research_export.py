from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.tools.deep_research import router


def _completed_run(**overrides):
    """Return the smallest completed run accepted by the export endpoint."""

    values = {
        "id": "run-1",
        "user_id": "user-1",
        "query": "What changed?",
        "status": "completed",
        "final_report_path": "final-report.md",
        "final_html_path": "final-report.html",
        "manifest_path": "manifest.json",
        "result_meta": {"title": "Evidence résumé"},
        "artifacts": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_markdown_report_export_uses_readable_filename_and_audits(
    monkeypatch,
    tmp_path,
):
    """The canonical Markdown download remains byte-for-byte report content."""

    report_path = tmp_path / "final-report.md"
    report_path.write_text("# Fallback title\n\nVerified evidence.", encoding="utf-8")
    run = _completed_run()
    audit_calls = []

    monkeypatch.setattr(router, "_owned_run_or_404", lambda *args: run)
    monkeypatch.setattr(
        router,
        "materialize_deep_research_artifact",
        lambda *args, **_kwargs: report_path,
    )
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *args: "audit-ip")
    monkeypatch.setattr(
        router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    response = router.export_deep_research_report(
        run_id=run.id,
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
        format="md",
    )

    assert response.body.decode("utf-8") == "# Fallback title\n\nVerified evidence."
    assert response.media_type == "text/markdown; charset=utf-8"
    assert "Evidence%20r%C3%A9sum%C3%A9.md" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"
    assert audit_calls[0]["action"] == "DEEP_RESEARCH_REPORT_EXPORTED"
    assert audit_calls[0]["details"] == {"run_id": "run-1", "format": "md"}


def test_pdf_report_export_reuses_markdown_renderer_with_validated_images(
    monkeypatch,
    tmp_path,
):
    """Only validated run-owned raster artifacts are exposed to PDF rendering."""

    report_path = tmp_path / "final-report.md"
    report_path.write_text("# Report\n\n![Chart](artifacts/chart.png)", encoding="utf-8")
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"image")
    run = _completed_run(
        artifacts=[
            {
                "relative_path": "artifacts/chart.png",
                "media_type": "image/png",
                "validation_status": "validated",
            },
            {
                "relative_path": "artifacts/unreviewed.png",
                "media_type": "image/png",
                "validation_status": "pending",
            },
        ],
    )
    render_calls = []

    monkeypatch.setattr(router, "_owned_run_or_404", lambda *args: run)

    def materialize(_user_id, _run_id, relative_path, **_kwargs):
        return image_path if relative_path == "artifacts/chart.png" else report_path

    def render_pdf(*args, **kwargs):
        resolver = kwargs["image_path_resolver"]
        assert resolver("artifacts/chart.png") == image_path
        assert resolver("artifacts/unreviewed.png") is None
        assert resolver("https://example.com/chart.png") is None
        render_calls.append(kwargs)
        return SimpleNamespace(filename="Evidence résumé.pdf", content=b"%PDF-test")

    monkeypatch.setattr(router, "materialize_deep_research_artifact", materialize)
    monkeypatch.setattr(router, "render_canvas_markdown_pdf", render_pdf)
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *args: None)
    monkeypatch.setattr(router, "create_audit_log", lambda **kwargs: None)

    response = router.export_deep_research_report(
        run_id=run.id,
        request=SimpleNamespace(headers={}),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
        format="pdf",
    )

    assert response.body == b"%PDF-test"
    assert response.media_type == "application/pdf"
    assert render_calls[0]["markdown_text"].startswith("# Report")


def test_report_export_rejects_unfinished_runs(monkeypatch):
    """Running, failed, and cancelled reports never masquerade as final exports."""

    monkeypatch.setattr(
        router,
        "_owned_run_or_404",
        lambda *args: _completed_run(status="running"),
    )

    with pytest.raises(HTTPException) as exc_info:
        router.export_deep_research_report(
            run_id="run-1",
            request=SimpleNamespace(headers={}),
            db=object(),
            db_log=object(),
            user=SimpleNamespace(id="user-1"),
            format="pdf",
        )

    assert exc_info.value.status_code == 409


@pytest.mark.parametrize(
    ("relative_path", "download", "expected_kind"),
    [
        ("final-report.md", False, None),
        ("final-report.md", True, "report_markdown"),
        ("workspace.zip", False, "workspace_archive"),
    ],
)
def test_run_file_audits_only_effective_attachments(
    monkeypatch,
    tmp_path,
    relative_path,
    download,
    expected_kind,
):
    materialized = tmp_path / relative_path
    materialized.write_bytes(b"artifact")
    run = _completed_run()
    audit_calls = []
    monkeypatch.setattr(router, "_owned_run_or_404", lambda *_args: run)
    monkeypatch.setattr(
        router,
        "materialize_deep_research_artifact",
        lambda *_args, **_kwargs: materialized,
    )
    monkeypatch.setattr(
        router,
        "get_deep_research_run_storage_provider",
        lambda *_args: "local",
    )
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: None)
    monkeypatch.setattr(
        router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    response = router.get_deep_research_run_file(
        run_id=run.id,
        relative_path=relative_path,
        request=SimpleNamespace(headers={}),
        db=object(),
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
        download=download,
    )

    expected_disposition = "inline" if expected_kind is None else "attachment"
    assert response.headers["content-disposition"].startswith(expected_disposition)
    if expected_kind is None:
        assert audit_calls == []
    else:
        assert audit_calls[0]["action"] == "DEEP_RESEARCH_FILE_DOWNLOADED"
        assert audit_calls[0]["details"] == {
            "run_id": "run-1",
            "file_kind": expected_kind,
            "explicit_download": download,
            "disposition": "attachment",
        }
        assert "relative_path" not in audit_calls[0]["details"]
