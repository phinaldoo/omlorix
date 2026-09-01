"""Regression coverage for OpenAI Responses streaming state."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.llm.openai.chat import (
    _OpenAIResponsesStreamProtocolError,
    _validated_openai_responses_stream,
)
from app.llm.openai.utils import (
    _OpenAIFunctionCallAccumulator,
    _OpenAIReasoningTimer,
    _OpenAIToolCallBudget,
)
from app.llm.tool_call_budget import MAX_TOOL_CALLS_PER_GENERATION


@pytest.mark.parametrize(
    "events",
    [
        [],
        [
            SimpleNamespace(type="response.created"),
            SimpleNamespace(type="response.output_text.delta"),
        ],
    ],
)
def test_responses_stream_requires_canonical_completion(events):
    with pytest.raises(
        _OpenAIResponsesStreamProtocolError,
        match=r"without response\.completed",
    ):
        list(_validated_openai_responses_stream(events))


@pytest.mark.parametrize("terminal_type", ["response.failed", "response.incomplete"])
def test_responses_stream_rejects_explicit_failure_terminals(terminal_type):
    with pytest.raises(_OpenAIResponsesStreamProtocolError, match=terminal_type):
        list(
            _validated_openai_responses_stream(
                [SimpleNamespace(type=terminal_type)]
            )
        )


def test_responses_stream_accepts_canonical_completion():
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.completed"),
    ]

    assert list(_validated_openai_responses_stream(events)) == events


def _function_item(
    *,
    item_id: str,
    call_id: str,
    name: str,
    arguments: str = "",
):
    """Build a small function-call item matching the SDK attributes we use."""
    return SimpleNamespace(
        type="function_call",
        id=item_id,
        call_id=call_id,
        name=name,
        namespace=None,
        arguments=arguments,
    )


def test_arguments_done_payload_is_authoritative_without_deltas():
    """Compatible providers may provide arguments only on the done event."""
    accumulator = _OpenAIFunctionCallAccumulator()
    accumulator.register_output_event(
        SimpleNamespace(
            item=_function_item(
                item_id="fc-image",
                call_id="call-image",
                name="image_generation",
            ),
            output_index=0,
        )
    )

    completed = accumulator.finalize_arguments(
        SimpleNamespace(
            item_id="fc-image",
            output_index=0,
            name="image_generation",
            arguments='{"description":"A lighthouse at dusk"}',
        )
    )

    assert completed == {
        "id": "fc-image",
        "call_id": "call-image",
        "name": "image_generation",
        "namespace": None,
        "arguments": '{"description":"A lighthouse at dusk"}',
    }


def test_parallel_argument_deltas_remain_bound_to_their_item_ids():
    """Interleaved calls must never share a single argument string."""
    accumulator = _OpenAIFunctionCallAccumulator()
    for output_index, item in enumerate(
        (
            _function_item(item_id="fc-weather", call_id="call-weather", name="weather"),
            _function_item(item_id="fc-search", call_id="call-search", name="search"),
        )
    ):
        accumulator.register_item(item, output_index=output_index)

    deltas = (
        ("fc-weather", 0, '{"city":"'),
        ("fc-search", 1, '{"query":"'),
        ("fc-weather", 0, 'Berlin"}'),
        ("fc-search", 1, 'Brandenburg Gate"}'),
    )
    for item_id, output_index, delta in deltas:
        accumulator.append_delta(
            SimpleNamespace(
                item_id=item_id,
                output_index=output_index,
                delta=delta,
            )
        )

    weather_call = accumulator.finalize_arguments(
        SimpleNamespace(
            item_id="fc-weather",
            output_index=0,
            name="weather",
            arguments=None,
        )
    )
    search_call = accumulator.finalize_arguments(
        SimpleNamespace(
            item_id="fc-search",
            output_index=1,
            name="search",
            arguments=None,
        )
    )

    assert weather_call["arguments"] == '{"city":"Berlin"}'
    assert search_call["arguments"] == '{"query":"Brandenburg Gate"}'


def test_completed_output_item_is_used_when_argument_events_are_missing():
    """The completed response output provides a final compatibility fallback."""
    accumulator = _OpenAIFunctionCallAccumulator()
    accumulator.register_item(
        _function_item(
            item_id="fc-image",
            call_id="call-image",
            name="image_generation",
            arguments='{"description":"A glass forest"}',
        ),
        output_index=0,
        finalized=True,
    )

    assert accumulator.drain_finalized()[0]["arguments"] == (
        '{"description":"A glass forest"}'
    )
    assert accumulator.drain_finalized() == []


def test_completed_output_can_fill_an_empty_arguments_done_event():
    """A later completed item may repair an incomplete compatible-provider event."""
    accumulator = _OpenAIFunctionCallAccumulator()
    accumulator.register_item(
        _function_item(item_id="fc-image", call_id="call-image", name="image_generation"),
        output_index=0,
    )
    accumulator.finalize_arguments(
        SimpleNamespace(
            item_id="fc-image",
            output_index=0,
            name="image_generation",
            arguments="",
        )
    )
    accumulator.register_item(
        _function_item(
            item_id="fc-image",
            call_id="call-image",
            name="image_generation",
            arguments='{"description":"A copper airship"}',
        ),
        output_index=0,
        finalized=True,
    )

    assert accumulator.drain_finalized()[0]["arguments"] == (
        '{"description":"A copper airship"}'
    )
    assert accumulator.drain_finalized() == []


def test_tool_call_budget_caps_one_completed_response_batch_at_200_calls():
    """One completed-response batch must never overrun the generation budget."""
    accumulator = _OpenAIFunctionCallAccumulator()
    for index in range(205):
        accumulator.register_item(
            _function_item(
                item_id=f"fc-{index}",
                call_id=f"call-{index}",
                name="weather",
                arguments="{}",
            ),
            output_index=index,
            finalized=True,
        )

    budget = _OpenAIToolCallBudget()
    admitted, rejected = budget.admit(accumulator.drain_finalized())

    assert MAX_TOOL_CALLS_PER_GENERATION == 200
    assert len(admitted) == 200
    assert len(rejected) == 5
    assert budget.remaining == 0


def test_tool_call_budget_is_shared_across_multiple_response_batches():
    """Several model turns must consume the same per-generation budget."""
    budget = _OpenAIToolCallBudget()

    first_admitted, first_rejected = budget.admit(
        [{"id": f"first-{index}"} for index in range(150)]
    )
    second_admitted, second_rejected = budget.admit(
        [{"id": f"second-{index}"} for index in range(60)]
    )

    assert len(first_admitted) == 150
    assert first_rejected == []
    assert len(second_admitted) == 50
    assert len(second_rejected) == 10
    assert budget.remaining == 0


def test_reasoning_timer_attaches_each_segment_time_and_tracks_total():
    """Tool-separated reasoning blocks receive independent timing metadata."""
    timer = _OpenAIReasoningTimer()
    first_start = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
    timer.start(first_start)

    first_meta, first_elapsed = timer.finish_metadata(
        {"segment": 1},
        finished_at=first_start + timedelta(seconds=0.4),
    )

    second_start = first_start + timedelta(seconds=2)
    timer.start(second_start)
    second_meta, second_elapsed = timer.finish_metadata(
        {"segment": 2},
        finished_at=second_start + timedelta(seconds=0.6),
    )

    assert first_meta == {"segment": 1, "reasoning_time": 0.4}
    assert second_meta == {"segment": 2, "reasoning_time": 0.6}
    assert first_elapsed == 0.4
    assert second_elapsed == 0.6
    assert timer.last_duration == 0.6
    assert timer.total_duration == 1.0
