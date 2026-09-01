"""Focused validation tests for durable visualization artifacts."""

import json

import pytest

from app.llm.helper import build_widget_block_meta
from app.tools import helper as tool_helper
from app.tools.schemas import tool_schemas
from app.tools.visualization.utils import (
    VisualizationValidationError,
    create_visualization_payload,
    validate_visualization_fragment,
)


def test_create_visualization_builds_canonical_widget_metadata():
    """A valid fragment becomes a persistable, static-first widget payload."""

    content = """
    <section id="revenue-comparison">
      <h2>Revenue comparison</h2>
      <svg role="img" aria-label="Revenue by quarter"></svg>
      <script>document.querySelector('#revenue-comparison').dataset.ready = 'true';</script>
    </section>
    """
    payload = create_visualization_payload(
        title="Revenue comparison",
        content=content,
        mode="wide",
        capabilities={"scripts": True, "chat_followup": True},
    )

    assert payload["type"] == "visualization"
    assert payload["render_mode"] == "visualization"
    assert payload["allow_scripts"] is True
    assert payload["visualization"]["root_id"] == "revenue-comparison"
    assert payload["visualization"]["mode"] == "wide"
    assert payload["visualization"]["capabilities"] == {
        "scripts": True,
        "external_data": False,
        "chat_followup": True,
        "download": False,
    }
    assert len(payload["visualization"]["source_hash"]) == 64
    assert payload["model_context"]["status"] == "created"


@pytest.mark.parametrize(
    "content, message",
    [
        ("<html><div id='root'></div></html>", "HTML fragment"),
        ("<div id='root'><iframe></iframe></div>", "<iframe>"),
        ("<div id='root' onclick='run()'></div>", "event-handler"),
        ("<div id='root'><script>fetch('/secret')</script></div>", "direct fetch"),
        ("<div id='root'><img src='/private/file.png'></div>", "direct external resources"),
        ("<div id='root'><meta http-equiv='refresh' content='0; /login'></div>", "<meta>"),
        ("<div id='same'></div><div id='same'></div>", "duplicate element id"),
        ("<section><h2>Missing stable root</h2></section>", "stable id"),
    ],
)
def test_visualization_rejects_unsafe_or_ambiguous_fragments(content, message):
    """Invalid artifacts fail early with a repairable model-facing reason."""

    with pytest.raises(VisualizationValidationError, match=message):
        validate_visualization_fragment(content)


def test_visualization_allows_network_words_in_visible_copy():
    """Prose containing a network API name is not mistaken for executable code."""

    fragment, info = validate_visualization_fragment(
        '<section id="lesson"><h2>How fetch works</h2><p>Fetch data responsibly.</p></section>'
    )

    assert "Fetch data responsibly" in fragment
    assert info.root_id == "lesson"


def test_visualization_tool_schema_describes_the_runtime_contract():
    """Every provider receives the same authoring and security contract."""

    schema = tool_schemas["create_visualization"]
    assert schema["type"] == "function"
    assert schema["parameters"]["required"] == ["title", "content"]
    assert schema["parameters"]["properties"]["mode"]["enum"] == ["normal", "wide"]
    assert "D3 v7" in schema["description"]
    assert "window.omlorix.visualization" in schema["description"]


def test_visualization_tool_dispatches_a_stream_widget(monkeypatch):
    """The normal tool dispatcher emits and returns the same durable artifact."""

    monkeypatch.setattr(tool_helper, "_admit_tool_invocation_or_payload", lambda *args, **kwargs: None)
    resolver = tool_helper.resolve_tool_call(
        db=None,
        tool_name="create_visualization",
        tool_arguments={
            "title": "Deployment path",
            "content": '<section id="deployment"><h2>Deployment path</h2></section>',
        },
        user_id="user-1",
        group_id=None,
        project_id=None,
    )

    streamed = json.loads(next(resolver))
    with pytest.raises(StopIteration) as completed:
        next(resolver)

    result = completed.value.value
    assert streamed["widget_type"] == "visualization"
    assert streamed["c"] == result["widget"]["html"]
    assert streamed["meta"]["visualization"] == result["widget"]["visualization"]
    assert result["result"]["status"] == "created"


def test_visualization_metadata_survives_widget_block_persistence():
    """Reloaded, shared, imported, and exported chats retain runtime metadata."""

    payload = create_visualization_payload(
        title="Persistent chart",
        content='<section id="persistent-chart"><h2>Persistent chart</h2></section>',
        capabilities={"scripts": False, "download": True},
    )
    meta = build_widget_block_meta(payload, tool_name="create_visualization", tool_call_id="call-1")

    assert meta["widget_type"] == "visualization"
    assert meta["render_mode"] == "visualization"
    assert meta["visualization"] == payload["visualization"]
    assert meta["tool_result"]["root_id"] == "persistent-chart"
    assert meta["tool_name"] == "create_visualization"
