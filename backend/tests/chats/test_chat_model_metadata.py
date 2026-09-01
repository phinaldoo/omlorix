"""Coverage for model metadata shown when historical chats are reloaded."""

import json
from datetime import datetime, timezone

import pytest

from app.chats.models import ChatMessages
from app.chats.utils import _serialize_chat_rows


def _assistant_message(metadata: dict) -> ChatMessages:
    """Build an assistant row with one generation-metadata content block."""
    return ChatMessages(
        id="assistant-1",
        chat_id="chat-1",
        model_id="internal-opus-id",
        role="assistant",
        content=json.dumps(
            [
                {
                    "type": "content",
                    "content": "Response",
                    "meta": metadata,
                }
            ]
        ),
        created_at=datetime.now(timezone.utc),
    )


def test_serialization_repairs_missing_historical_model_metadata():
    """The tooltip receives the configured model for older incomplete rows."""
    serialized = _serialize_chat_rows(
        [_assistant_message({"input_tokens": 622, "output_tokens": 6574})],
        lambda _file_id: None,
        model_name_by_id={"internal-opus-id": "claude-opus-4-6-thinking"},
    )

    assert serialized[0]["content"][0]["meta"]["model"] == (
        "claude-opus-4-6-thinking"
    )


@pytest.mark.parametrize("metadata_key", ["model", "model_name", "modelId"])
def test_serialization_preserves_provider_reported_model_metadata(metadata_key):
    """Read-time repair must never replace a provider's canonical identifier."""
    serialized = _serialize_chat_rows(
        [
            _assistant_message(
                {metadata_key: "provider-canonical-opus", "input_tokens": 1}
            )
        ],
        lambda _file_id: None,
        model_name_by_id={"internal-opus-id": "configured-opus-alias"},
    )

    assert serialized[0]["content"][0]["meta"][metadata_key] == (
        "provider-canonical-opus"
    )
    if metadata_key != "model":
        assert "model" not in serialized[0]["content"][0]["meta"]
