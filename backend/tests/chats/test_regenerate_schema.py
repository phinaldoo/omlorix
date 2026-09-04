import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats.schemas import RegenerateMessageRequest, SendChatRequest


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


@pytest.mark.parametrize("field_name", ["skill_ids", "note_ids", "prompt_ids"])
def test_chat_requests_reject_unbounded_context_id_lists(field_name):
    values = [f"id-{index}" for index in range(21)]

    with pytest.raises(ValidationError):
        SendChatRequest(model_id="model-1", message="hello", **{field_name: values})
    with pytest.raises(ValidationError):
        RegenerateMessageRequest(
            chat_id="chat-1",
            user_message_id="message-1",
            **{field_name: values},
        )


def test_chat_requests_reject_more_than_five_chat_references():
    chat_ids = [f"chat-{index}" for index in range(6)]

    with pytest.raises(ValidationError):
        SendChatRequest(
            model_id="model-1",
            message="hello",
            chat_reference_ids=chat_ids,
        )
    with pytest.raises(ValidationError):
        RegenerateMessageRequest(
            chat_id="chat-1",
            user_message_id="message-1",
            chat_reference_ids=chat_ids,
        )
