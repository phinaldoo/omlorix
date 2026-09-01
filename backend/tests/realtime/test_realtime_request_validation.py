from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.realtime.schemas import (  # noqa: E402
    MAX_REALTIME_ID_LENGTH,
    MAX_REALTIME_SDP_LENGTH,
    MAX_REALTIME_TEXT_LENGTH,
    PersistRealtimeTurnRequest,
    PrepareRealtimeInputRequest,
    RealtimeWebRTCOfferRequest,
)


def test_realtime_transcripts_are_bounded_before_persistence():
    """A client cannot use realtime persistence as an unbounded data sink."""
    with pytest.raises(ValidationError):
        PersistRealtimeTurnRequest(
            turn_id="turn-1",
            user_transcript="x" * (MAX_REALTIME_TEXT_LENGTH + 1),
        )


def test_realtime_file_lists_are_bounded_before_file_resolution():
    """Attachment fan-out is capped before any file storage work begins."""
    with pytest.raises(ValidationError):
        PrepareRealtimeInputRequest(file_ids=[f"file-{index}" for index in range(21)])


@pytest.mark.parametrize(
    ("request_type", "request_fields"),
    [
        (PrepareRealtimeInputRequest, {}),
        (PersistRealtimeTurnRequest, {"turn_id": "turn-1"}),
    ],
)
def test_realtime_file_ids_are_individually_bounded(request_type, request_fields):
    """Each attachment ID is bounded in both realtime request paths."""
    with pytest.raises(ValidationError):
        request_type(
            **request_fields,
            file_ids=["x" * (MAX_REALTIME_ID_LENGTH + 1)],
        )


def test_realtime_usage_rejects_negative_or_implausible_counts():
    """Client-reported analytics remain numeric and within operational bounds."""
    with pytest.raises(ValidationError):
        PersistRealtimeTurnRequest(
            turn_id="turn-1",
            usage={"input_tokens": -1},
        )

    with pytest.raises(ValidationError):
        PersistRealtimeTurnRequest(
            turn_id="turn-2",
            usage={"output_tokens": 1_000_000_001},
        )


def test_realtime_sdp_offer_is_nonempty_and_bounded():
    """Server-side signaling rejects empty or unbounded browser offers."""
    with pytest.raises(ValidationError):
        RealtimeWebRTCOfferRequest(sdp="")

    with pytest.raises(ValidationError):
        RealtimeWebRTCOfferRequest(sdp="x" * (MAX_REALTIME_SDP_LENGTH + 1))
