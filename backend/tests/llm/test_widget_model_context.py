import json

from app.llm.helper import (
    build_widget_block_meta,
    stringify_tool_result_content_for_model,
    stringify_tool_result_content_for_persistence,
)


def test_widget_meta_includes_structured_tool_result():
    widget_payload = {
        "type": "weather",
        "html": "<div>widget</div>",
        "model_context": {
            "location": "Berlin",
            "temperature": 21,
        },
    }

    meta = build_widget_block_meta(widget_payload, tool_name="weather", tool_call_id="call_weather_1")

    assert meta["widget_type"] == "weather"
    assert meta["tool_name"] == "weather"
    assert meta["tool_call_id"] == "call_weather_1"
    assert meta["tool_result"] == {"location": "Berlin", "temperature": 21}
    assert json.loads(stringify_tool_result_content_for_persistence("weather", "", widget_payload)) == {
        "location": "Berlin",
        "temperature": 21,
    }


def test_widget_meta_carries_backend_rendering_contract():
    widget_payload = {
        "type": "flashcards",
        "html": "<div>widget</div><script>window.ready = true;</script>",
        "render_mode": "iframe",
        "allow_scripts": True,
        "model_context": {
            "title": "Deck",
            "cards": [{"front": "A", "back": "B"}],
        },
    }

    meta = build_widget_block_meta(widget_payload, tool_name="flashcards")

    assert meta["widget_type"] == "flashcards"
    assert meta["render_mode"] == "iframe"
    assert meta["allow_scripts"] is True
    assert meta["tool_result"]["title"] == "Deck"


def test_active_model_receives_rich_tool_output_while_history_stays_bounded():
    """Widget persistence must not replace the active model's report output."""

    widget_payload = {
        "type": "deep_research",
        "html": "<section>Research card</section>",
        "model_context": {
            "status": "completed",
            "run_id": "run-1",
            "final_report_path": "final-report.md",
        },
    }
    report_output = "Deep Research has finished.\n\nFinal report:\nEvidence-backed answer."

    persisted = stringify_tool_result_content_for_persistence(
        "deep_research",
        report_output,
        widget_payload,
    )
    model_output = stringify_tool_result_content_for_model(report_output, persisted)

    assert json.loads(persisted) == widget_payload["model_context"]
    assert model_output == report_output
    assert "Evidence-backed answer." in model_output
