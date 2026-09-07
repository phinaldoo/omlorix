from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.chats.schemas import Chat, ChatAttentionQuery


def test_attention_query_normalizes_ids_and_keeps_input_limits():
    assert ChatAttentionQuery(chat_ids=[" chat-1 ", "", "chat-1", "chat-2"]).chat_ids == [
        "chat-1",
        "chat-2",
    ]
    assert len(ChatAttentionQuery(chat_ids=[str(i) for i in range(200)]).chat_ids) == 200
    with pytest.raises(ValidationError):
        ChatAttentionQuery(chat_ids=["chat-1"] * 201)
    with pytest.raises(ValidationError):
        ChatAttentionQuery(chat_ids=[None])


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (None, None),
        ({"status": "normal"}, {"status": "normal"}),
        ('{"status":"normal"}', {"status": "normal"}),
        ("invalid JSON", {}),
        ("[]", {}),
    ],
)
def test_chat_metadata_preserves_legacy_json_normalization(meta, expected):
    now = datetime.now(timezone.utc)
    chat = Chat(
        id="chat-1",
        user_id="user-1",
        archived=False,
        meta=meta,
        created_at=now,
        last_updated_at=now,
    )
    assert chat.meta == expected
