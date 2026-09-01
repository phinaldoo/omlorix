from __future__ import annotations

import json

from app.tools.errors import (
    SafeToolExecutionError,
    ToolErrorTracker,
    ToolExecutionDiagnosticError,
    build_tool_error_stream_event,
)


def test_safe_tool_error_is_actionable_once_then_stops_tool_retries():
    """One correction is allowed; the same validation failure twice ends tool use."""

    tracker = ToolErrorTracker(max_identical_safe_errors=2)
    error = SafeToolExecutionError(
        code="canvas_html_external_css_url",
        safe_message="HTML Canvas cannot load external CSS URLs.",
    )

    first = tracker.record("canvas", error)
    second = tracker.record("canvas", error)

    assert first.retry_allowed is True
    assert first.stop_tool_calls is False
    assert second.retry_allowed is False
    assert second.stop_tool_calls is True
    assert second.error_code == "canvas_html_external_css_url"

    payload = json.loads(second.model_output)
    assert payload == {
        "error": (
            "HTML Canvas cannot load external CSS URLs. The same validation error "
            "occurred again, so do not call tools again in this response. Explain "
            "the limitation to the user."
        ),
        "error_code": "canvas_html_external_css_url",
        "retry_allowed": False,
    }


def test_unexpected_tool_error_remains_masked_and_does_not_trigger_retry_cap():
    """Only explicitly safe errors may expose details to the model."""

    tracker = ToolErrorTracker()
    response = tracker.record("canvas", RuntimeError("database password leaked here"))

    assert response.internal_message == "database password leaked here"
    assert response.public_message == "An error occurred during tool execution."
    assert response.error_code is None
    assert response.retry_allowed is False
    assert response.stop_tool_calls is False
    assert "password" not in response.model_output


def test_safe_transient_error_can_forbid_same_response_retry_immediately():
    """Capacity-style safe errors must explain the delay without a futile retry."""

    tracker = ToolErrorTracker()
    error = SafeToolExecutionError(
        code="temporary_capacity",
        safe_message="No temporary execution slot is available. Try again later.",
        allow_same_response_retry=False,
    )

    response = tracker.record("code_execution", error)

    assert response.retry_allowed is False
    assert response.stop_tool_calls is True
    assert response.public_message == error.safe_message
    assert json.loads(response.model_output) == {
        "error": error.safe_message,
        "error_code": "temporary_capacity",
        "retry_allowed": False,
    }


def test_internal_tool_diagnostics_reach_statistics_but_remain_masked_from_model():
    """Nested component identity is retained without exposing provider errors."""

    error = ToolExecutionDiagnosticError(
        "Nested provider connection failed.",
        statistic_meta={
            "nested_generation": {
                "phase": "slide presentation HTML generation",
                "model_name": "Presentation model",
                "provider": "anthropic",
            }
        },
    )

    response = ToolErrorTracker().record("slide_presentation", error)

    assert response.model_output == "An error occurred during tool execution."
    assert response.statistic_meta == error.tool_statistic_meta


def test_tool_error_stream_event_exposes_only_explicitly_safe_details():
    safe_event = json.loads(build_tool_error_stream_event(
        "automations",
        "call-1",
        SafeToolExecutionError(
            code="automations_feature_disabled",
            safe_message="Automations are disabled for your group.",
        ),
    ))
    unsafe_event = json.loads(build_tool_error_stream_event(
        "automations",
        "call-2",
        RuntimeError("secret database diagnostic"),
    ))

    assert safe_event["d"]["error_code"] == "automations_feature_disabled"
    assert safe_event["d"]["error"] == "Automations are disabled for your group."
    assert unsafe_event["d"]["error"] == "An error occurred during tool execution."
    assert "error_code" not in unsafe_event["d"]
    assert "secret" not in json.dumps(unsafe_event)
