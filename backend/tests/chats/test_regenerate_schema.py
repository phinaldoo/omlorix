import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats.schemas import RegenerateMessageRequest


def test_regenerate_request_accepts_longer_custom_retry_guidance():
    payload = RegenerateMessageRequest(
        chat_id="chat-1",
        user_message_id="message-1",
        retry_guidance={
            "mode": "custom",
            "instruction": "a" * 2000,
        },
    )

    assert payload.retry_guidance is not None
    assert payload.retry_guidance.instruction == "a" * 2000


def test_regenerate_request_rejects_retry_guidance_above_limit():
    with pytest.raises(ValidationError):
        RegenerateMessageRequest(
            chat_id="chat-1",
            user_message_id="message-1",
            retry_guidance={
                "mode": "custom",
                "instruction": "a" * 2001,
            },
        )
