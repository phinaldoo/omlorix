from types import SimpleNamespace

import pytest


# Importing the files router loads the backup subsystem, whose zstandard
# dependency is present in the backend image but not every lightweight host test
# environment. The security test runs normally in the supported backend image.
pytest.importorskip("zstandard")

from app.files import router as files_router  # noqa: E402
from app.files.access import ResolvedFileAccess  # noqa: E402
from app.files.schemas import CanvasLatexRenderRequest  # noqa: E402
from app.tools import helper as tool_helper  # noqa: E402


def test_canvas_latex_render_uses_stored_revision_and_shared_render_limit(monkeypatch):
    """The unified route renders by source identity and never accepts source text."""
    events = []
    source = SimpleNamespace(
        id="source-1",
        user_id="user-1",
        folder_id=None,
        meta={"canvas_type": "latex", "canvas_revision": 4},
    )
    monkeypatch.setattr(
        files_router,
        "resolve_file_for_edit",
        lambda db, user_id, file_id: ResolvedFileAccess(source, "user-1"),
    )
    monkeypatch.setattr(
        tool_helper,
        "enforce_tool_rate_limit_or_raise",
        lambda db, **kwargs: events.append(("admit", kwargs)),
    )

    def render(db, **kwargs):
        events.append(("render", kwargs))
        return {
            "file_id": "pdf-1",
            "source_file_id": "source-1",
            "file_name": "report.pdf",
            "source_file_name": "report.tex",
            "title": "Report",
            "mime_type": "application/pdf",
            "size": 12,
            "compiler": "pdflatex",
            "source_revision": 4,
            "render_revision": 4,
            "render_status": "ready",
            "service_connection": None,
        }

    monkeypatch.setattr(files_router, "render_latex_canvas", render)
    monkeypatch.setattr(files_router, "get_audit_request_ip", lambda *_args: "203.0.113.10")
    response = files_router.render_canvas_latex_route(
        CanvasLatexRenderRequest(file_id="source-1", expected_revision=4),
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        user=SimpleNamespace(id="user-1", group_id="group-1", role="user"),
        db=object(),
    )

    assert response.file_id == "pdf-1"
    assert events[0][1]["tool_name"] == "latex_pdf"
    assert events[1][1] == {
        "user_id": "user-1",
        # The renderer must retain the authenticated actor separately from the
        # storage owner.  Shared Canvas routes can persist the source and PDF as
        # the owner, but referenced assets are authorized as this actor.
        "asset_actor_user_id": "user-1",
        "source_file_id": "source-1",
        "expected_revision": 4,
        "audit_ip_address": "203.0.113.10",
        "audit_user_agent": "pytest",
    }


def test_shared_canvas_latex_render_keeps_actor_separate_from_owner(monkeypatch):
    """A collaborator render must not authorize asset IDs as the Canvas owner."""
    events = []
    source = SimpleNamespace(
        id="source-1",
        user_id="owner-1",
        folder_id="folder-1",
        meta={"canvas_type": "latex", "canvas_revision": 2},
    )

    # The collaborator does not own the source, so the route takes its shared-
    # folder branch and uses the source owner only for artifact persistence.
    monkeypatch.setattr(
        files_router,
        "resolve_file_for_edit",
        lambda db, user_id, file_id: ResolvedFileAccess(source, "owner-1"),
    )

    class _Query:
        def filter(self, *_args):
            return self

        def first(self):
            return source

    monkeypatch.setattr(
        tool_helper,
        "enforce_tool_rate_limit_or_raise",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        files_router,
        "render_latex_canvas",
        lambda db, **kwargs: events.append(kwargs)
        or {
            "file_id": "pdf-1",
            "source_file_id": "source-1",
            "file_name": "report.pdf",
            "source_file_name": "report.tex",
            "title": "Report",
            "mime_type": "application/pdf",
            "size": 12,
            "compiler": "pdflatex",
            "source_revision": 2,
            "render_revision": 2,
            "render_status": "ready",
            "service_connection": None,
        },
    )
    monkeypatch.setattr(files_router, "get_audit_request_ip", lambda *_args: "203.0.113.11")

    files_router.render_canvas_latex_route(
        CanvasLatexRenderRequest(file_id="source-1", expected_revision=2),
        request=SimpleNamespace(headers={"user-agent": "pytest-collaborator"}),
        user=SimpleNamespace(
            id="collaborator-1", group_id="group-1", role="user"
        ),
        db=SimpleNamespace(query=lambda _model: _Query()),
    )

    assert events == [
        {
            "user_id": "owner-1",
            "asset_actor_user_id": "collaborator-1",
            "source_file_id": "source-1",
            "expected_revision": 2,
            "audit_ip_address": "203.0.113.11",
            "audit_user_agent": "pytest-collaborator",
        }
    ]
