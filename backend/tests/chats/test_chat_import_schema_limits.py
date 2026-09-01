"""Resource bounds for the canonical chat-import schema."""

import pytest
from pydantic import ValidationError

from app.chats import schemas as chat_schemas


def test_import_rejects_aggregate_message_content_over_per_chat_limit(monkeypatch):
    """Individually valid messages must still share one bounded chat budget."""

    monkeypatch.setattr(chat_schemas, "CHAT_IMPORT_MAX_MESSAGE_CONTENT_LENGTH", 10)
    monkeypatch.setattr(chat_schemas, "CHAT_IMPORT_MAX_MESSAGE_BYTES_PER_CHAT", 12)

    with pytest.raises(ValidationError, match="per-chat import content limit"):
        chat_schemas.ImportedChatEntry.model_validate(
            {
                "chat": {},
                "messages": [
                    {"content": "12345678"},
                    {"content": "abcdefgh"},
                ],
            }
        )


def test_structured_message_content_is_serialized_once(monkeypatch):
    """The aggregate validator must reuse each message's cached byte count."""

    structured_content = {"blocks": [{"type": "text", "content": "hello"}]}
    original_dumps = chat_schemas.json.dumps
    matching_calls = 0

    def counting_dumps(value, *args, **kwargs):
        nonlocal matching_calls
        if value == structured_content:
            matching_calls += 1
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setattr(chat_schemas.json, "dumps", counting_dumps)

    entry = chat_schemas.ImportedChatEntry.model_validate(
        {
            "chat": {},
            "messages": [{"content": structured_content}],
        }
    )

    assert entry.messages[0].content == structured_content
    assert matching_calls == 1
