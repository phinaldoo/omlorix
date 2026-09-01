import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.openai.utils import (
    _OPENAI_REASONING_SUMMARY_DONE_EVENT_TYPES,
    _extract_openai_reasoning_summary_text,
)


def _collect_reasoning_summary_chunks(events):
    emitted = []
    accumulated = ""
    for event in events:
        delta = _extract_openai_reasoning_summary_text(event)
        if not delta:
            continue
        if event.type in _OPENAI_REASONING_SUMMARY_DONE_EVENT_TYPES and accumulated.endswith(delta):
            continue
        accumulated += delta
        emitted.append(delta)
    return emitted


def test_openai_reasoning_summary_text_delta_streams_incrementally():
    event = SimpleNamespace(
        type="response.reasoning_summary_text.delta",
        delta=" thinking",
    )

    assert _extract_openai_reasoning_summary_text(event) == " thinking"


def test_openai_reasoning_summary_done_does_not_duplicate_streamed_text():
    events = [
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="A"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta=" plan"),
        SimpleNamespace(type="response.reasoning_summary_text.done", text="A plan"),
        SimpleNamespace(
            type="response.reasoning_summary_part.done",
            part=SimpleNamespace(text="A plan"),
        ),
    ]

    assert _collect_reasoning_summary_chunks(events) == ["A", " plan"]


def test_openai_reasoning_summary_done_still_works_as_fallback():
    events = [
        SimpleNamespace(
            type="response.reasoning_summary_part.done",
            part=SimpleNamespace(text="Completed reasoning"),
        ),
    ]

    assert _collect_reasoning_summary_chunks(events) == ["Completed reasoning"]
