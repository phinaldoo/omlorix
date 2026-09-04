import base64
import json
from unittest.mock import patch

from app.llm.helper import (
    build_tool_call_block,
    extract_tool_call_block,
    format_tool_call_block_label,
    sanitize_tool_call_arguments_for_persistence,
    stringify_tool_result_content_for_persistence,
)


def test_build_tool_call_block_persists_only_canonical_structured_data():
    block = build_tool_call_block(
        "code_execution",
        {"language": "python", "code": "print('ok')"},
        tool_call_id="call-1",
        tool_namespace="builtin",
    )

    assert block == {
        "type": "tool_call",
        "meta": {
            "tool_name": "code_execution",
            "arguments": '{"language":"python","code":"print(\'ok\')"}',
            "tool_call_id": "call-1",
            "tool_namespace": "builtin",
        },
    }
    assert "content" not in block
    assert format_tool_call_block_label(block) == (
        'code_execution({"language":"python","code":"print(\'ok\')"})'
    )


def test_artifact_history_uses_stable_receipts_instead_of_repeated_bodies():
    body = "# Large Canvas\n" + ("content " * 25_000)
    arguments = {
        "type": "markdown",
        "file_id": "canvas-1",
        "expected_revision": 7,
        "content": body,
    }

    compact_arguments = sanitize_tool_call_arguments_for_persistence(
        "canvas",
        arguments,
    )
    compact_arguments_again = sanitize_tool_call_arguments_for_persistence(
        "canvas",
        compact_arguments,
    )
    decoded_arguments = json.loads(compact_arguments)

    assert compact_arguments_again == compact_arguments
    assert len(compact_arguments) < 300
    assert decoded_arguments["file_id"] == "canvas-1"
    assert decoded_arguments["expected_revision"] == 7
    assert decoded_arguments["content"].startswith("[omitted from chat history:")
    assert body not in compact_arguments

    raw_result = json.dumps(
        {
            "file_id": "canvas-1",
            "canvas_revision": 8,
            "content": body,
        }
    )
    compact_result = stringify_tool_result_content_for_persistence(
        "canvas",
        raw_result,
    )
    compact_result_again = stringify_tool_result_content_for_persistence(
        "canvas",
        compact_result,
    )
    decoded_result = json.loads(compact_result)

    assert compact_result_again == compact_result
    assert len(compact_result) < 300
    assert decoded_result["file_id"] == "canvas-1"
    assert decoded_result["canvas_revision"] == 8
    assert decoded_result["content_length"] == len(body)
    assert "content" not in decoded_result


def test_extract_tool_call_block_keeps_legacy_content_compatible():
    block = {
        "type": "tool_call",
        "content": 'weather({"location":"Berlin (DE)"})',
        "meta": {"tool_use_id": "legacy-call"},
    }

    assert extract_tool_call_block(block) == {
        "tool_name": "weather",
        "arguments": '{"location":"Berlin (DE)"}',
        "tool_call_id": "legacy-call",
        "tool_namespace": None,
    }
    assert format_tool_call_block_label(block) == 'weather({"location":"Berlin (DE)"})'


def test_legacy_tool_call_arguments_are_not_compacted_on_replay():
    body = "legacy canvas body " * 5_000
    arguments = json.dumps(
        {"type": "markdown", "content": body},
        separators=(",", ":"),
    )

    extracted = extract_tool_call_block(
        {"type": "tool_call", "content": f"canvas({arguments})"}
    )

    assert body in str(extracted["arguments"])


def test_extract_tool_call_block_accepts_imported_argument_aliases():
    block = {
        "type": "tool_call",
        "meta": {
            "tool_name": "search",
            "tool_args": {"query": "canonical metadata"},
        },
    }

    assert extract_tool_call_block(block)["arguments"] == '{"query":"canonical metadata"}'
    assert format_tool_call_block_label(block) == 'search({"query":"canonical metadata"})'


def test_extra_metadata_cannot_override_canonical_tool_fields():
    block = build_tool_call_block(
        "weather",
        {"location": "Berlin"},
        tool_call_id="call-current",
        extra_meta={
            "tool_name": "stale",
            "arguments": "stale",
            "tool_call_id": "call-stale",
            "native_web_search": True,
        },
    )

    assert block["meta"]["tool_name"] == "weather"
    assert block["meta"]["arguments"] == '{"location":"Berlin"}'
    assert block["meta"]["tool_call_id"] == "call-current"
    assert block["meta"]["native_web_search"] is True


def test_openai_responses_history_rehydrates_canonical_tool_call():
    from app.llm.openai.utils import reformat_chat_history

    block = build_tool_call_block(
        "weather",
        {"location": "Berlin"},
        tool_call_id="call-weather",
        tool_namespace="builtin",
    )

    result = reformat_chat_history(
        [{"id": "message-1", "role": "assistant", "content": [block]}],
        user_id=None,
        db=None,
        use_group_context=False,
        use_project_context=False,
    )

    assert result["formatted"] == [
        {
            "type": "function_call",
            "call_id": "call-weather",
            "name": "weather",
            "namespace": "builtin",
            "arguments": '{"location":"Berlin"}',
            "status": "completed",
        }
    ]


def test_anthropic_history_replays_signed_thinking_and_exact_tool_id():
    from app.llm.anthropic.utils import reformat_chat_history

    blocks = [
        {
            "type": "reasoning",
            "content": "check the forecast",
            "meta": {"anthropic": {"thinking_signature": "signed-state"}},
        },
        build_tool_call_block(
            "weather",
            {"location": "Berlin"},
            tool_call_id="toolu_weather",
        ),
        {
            "type": "tool_call_result",
            "content": "sunny",
            "meta": {"tool_call_id": "toolu_weather"},
        },
    ]

    with patch(
        "app.llm.anthropic.messages.get_user_group_setting_value",
        return_value=False,
    ):
        result = reformat_chat_history(
            [{"role": "assistant", "content": blocks}],
            user_id=None,
            db=None,
            use_group_context=False,
            use_project_context=False,
        )

    assert result["formatted"] == [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "check the forecast",
                    "signature": "signed-state",
                },
                {
                    "type": "tool_use",
                    "id": "toolu_weather",
                    "name": "weather",
                    "input": {"location": "Berlin"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_weather",
                    "content": "sunny",
                }
            ],
        },
    ]


def test_anthropic_structured_history_keeps_files_and_breaks_stale_id_fallback():
    from app.llm.anthropic.utils import reformat_chat_history

    blocks = [
        build_tool_call_block("weather", {}, tool_call_id="old-call"),
        {
            "type": "tool_call_result",
            "content": "sunny",
            "meta": {"tool_call_id": "old-call"},
        },
        {"type": "tool_call", "content": "broken({})", "meta": {}},
        {"type": "tool_call_result", "content": "orphaned result"},
        build_tool_call_block(
            "web_search",
            {"query": "Berlin"},
            tool_call_id="web-call",
        ),
        {
            "type": "tool_call_result",
            "content": '{"results":[{"url":"https://example.com","extra":"kept"}]}',
            "documents": ["document-1"],
            "audios": ["audio-1"],
            "meta": {
                "tool_call_id": "web-call",
                "native_web_search": True,
            },
        },
    ]
    uploaded_part = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AA=="},
    }

    with (
        patch(
            "app.llm.anthropic.messages.get_user_group_setting_value",
            return_value=False,
        ),
        patch(
            "app.llm.anthropic.messages.upload_files",
            return_value={
                "parts": [uploaded_part],
                "counters": {},
                "unsupported": True,
                "unsupported_file_ids": ["document-1"],
            },
        ) as upload_mock,
    ):
        result = reformat_chat_history(
            [{"role": "assistant", "content": blocks, "images": ["image-1"]}],
            user_id="user-1",
            db=None,
            use_group_context=False,
            use_project_context=False,
        )

    assert upload_mock.call_args.args[1] == ["image-1", "document-1"]
    assert result["unsupported_file_ids"] == ["audio-1", "document-1"]
    assert [message["role"] for message in result["formatted"]].count("user") == 1
    assistant_parts = [
        part
        for message in result["formatted"]
        if message["role"] == "assistant"
        for part in message["content"]
    ]
    assert uploaded_part in assistant_parts
    assert any(
        part.get("type") == "text" and "orphaned result" in part.get("text", "")
        for part in assistant_parts
    )
    assert any(
        part.get("type") == "server_tool_use" and part.get("id") == "web-call"
        for part in assistant_parts
    )
    web_result = next(
        part for part in assistant_parts if part.get("type") == "web_search_tool_result"
    )
    assert web_result["content"] == [
        {
            "url": "https://example.com",
            "extra": "kept",
            "type": "web_search_result",
        }
    ]


def test_gemini_history_replays_thought_signature_and_function_response_id():
    from app.llm.google_aistudio.utils import reformat_chat_history

    signature = base64.b64encode(b"opaque-gemini-signature").decode("ascii")
    blocks = [
        build_tool_call_block(
            "weather",
            {"location": "Berlin"},
            tool_call_id="gemini-call-1",
            extra_meta={
                "thinking_signature": {"google_aistudio": signature},
            },
        ),
        {
            "type": "tool_call_result",
            "content": "sunny",
            "meta": {"tool_call_id": "gemini-call-1"},
        },
    ]

    with patch(
        "app.llm.google_aistudio.utils.get_user_group_setting_value",
        return_value=False,
    ):
        result = reformat_chat_history(
            [{"role": "assistant", "content": blocks}],
            user_id=None,
            db=None,
            client=None,
            upload_files_bool=False,
            use_group_context=False,
            use_project_context=False,
        )

    dumped = [entry.model_dump(exclude_none=True) for entry in result["formatted"]]
    assert dumped == [
        {
            "role": "model",
            "parts": [
                {
                    "function_call": {
                        "id": "gemini-call-1",
                        "args": {"location": "Berlin"},
                        "name": "weather",
                    },
                    "thought_signature": b"opaque-gemini-signature",
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "id": "gemini-call-1",
                        "name": "weather",
                        "response": {"result": "sunny"},
                    }
                }
            ],
        },
    ]


def test_gemini_history_groups_parallel_calls_and_responses_by_model_turn():
    from app.llm.google_aistudio.utils import reformat_chat_history

    signature = base64.b64encode(b"parallel-turn-signature").decode("ascii")
    turn_id = "model-turn:7"
    blocks = [
        build_tool_call_block(
            "weather",
            {"location": "Berlin"},
            tool_call_id="gemini-call-1",
            extra_meta={
                "thinking_signature": {"google_aistudio": signature},
                "google_aistudio_model_turn_id": turn_id,
            },
        ),
        {
            "type": "tool_call_result",
            "content": "sunny",
            "meta": {
                "tool_call_id": "gemini-call-1",
                "google_aistudio_model_turn_id": turn_id,
            },
        },
        build_tool_call_block(
            "calendar",
            {"day": "today"},
            tool_call_id="gemini-call-2",
            extra_meta={"google_aistudio_model_turn_id": turn_id},
        ),
        {
            "type": "tool_call_result",
            "content": {"events": []},
            "meta": {
                "tool_call_id": "gemini-call-2",
                "google_aistudio_model_turn_id": turn_id,
            },
        },
    ]

    with patch(
        "app.llm.google_aistudio.utils.get_user_group_setting_value",
        return_value=False,
    ):
        result = reformat_chat_history(
            [{"role": "assistant", "content": blocks}],
            user_id=None,
            db=None,
            client=None,
            upload_files_bool=False,
            use_group_context=False,
            use_project_context=False,
        )

    dumped = [entry.model_dump(exclude_none=True) for entry in result["formatted"]]
    assert [entry["role"] for entry in dumped] == ["model", "user"]
    assert [
        part["function_call"]["id"] for part in dumped[0]["parts"]
    ] == ["gemini-call-1", "gemini-call-2"]
    assert dumped[0]["parts"][0]["thought_signature"] == b"parallel-turn-signature"
    assert "thought_signature" not in dumped[0]["parts"][1]
    assert [
        part["function_response"]["id"] for part in dumped[1]["parts"]
    ] == ["gemini-call-1", "gemini-call-2"]


def test_gemini_structured_history_uses_attachment_aware_replay_path():
    from google.genai import types

    from app.llm.google_aistudio.utils import reformat_chat_history

    blocks = [
        {"type": "reasoning", "content": "checking"},
        build_tool_call_block("inspect", {}, tool_call_id="gemini-call"),
        {
            "type": "tool_call_result",
            "content": "done",
            "images": ["image-1"],
            "youtube": [{"url": "https://youtu.be/example"}],
            "meta": {"tool_call_id": "gemini-call"},
        },
    ]

    with (
        patch(
            "app.llm.google_aistudio.utils.get_user_group_setting_value",
            return_value=False,
        ),
        patch(
            "app.llm.google_aistudio.utils.upload_files",
            return_value={
                "parts": [types.Part(text="uploaded image")],
                "uploaded_cleanup": [],
                "counters": {},
                "unsupported": False,
            },
        ) as upload_mock,
    ):
        result = reformat_chat_history(
            [{"role": "assistant", "content": blocks}],
            user_id="user-1",
            db=None,
            client=None,
            upload_files_bool=True,
            use_group_context=False,
            use_project_context=False,
        )

    assert upload_mock.call_args.args[2] == ["image-1"]
    dumped = [entry.model_dump(exclude_none=True) for entry in result["formatted"]]
    part_texts = [
        part.get("text", "")
        for entry in dumped
        for part in entry.get("parts", [])
    ]
    assert "uploaded image" in part_texts
    assert any('"youtube"' in text for text in part_texts)


def test_openrouter_history_keeps_reasoning_sequence_and_tool_ids():
    from app.llm.openrouter.utils import (
        _openrouter_convert_history_to_responses_input,
        reformat_chat_history,
    )

    reasoning_details = [
        {
            "type": "reasoning.encrypted",
            "data": "opaque-data",
            "id": "reasoning-1",
            "format": "anthropic-claude-v1",
            "index": 0,
        }
    ]
    response_reasoning_item = {
        "type": "reasoning",
        "id": "rs_openrouter_1",
        "encrypted_content": "opaque-response-state",
        "summary": [],
    }
    blocks = [
        {
            "type": "reasoning",
            "content": "checking",
            "meta": {
                "openrouter_reasoning_details": reasoning_details,
                "openrouter_responses_reasoning_items": [
                    response_reasoning_item,
                ],
            },
        },
        build_tool_call_block(
            "weather",
            {"location": "Berlin"},
            tool_call_id="openrouter-call-1",
        ),
        {
            "type": "tool_call_result",
            "content": "sunny",
            "meta": {"tool_call_id": "openrouter-call-1"},
        },
    ]

    with patch(
        "app.llm.openrouter.utils.get_user_group_setting_value",
        return_value=False,
    ):
        result = reformat_chat_history(
            [{"role": "assistant", "content": blocks}],
            user_id=None,
            db=None,
            upload_files_bool=False,
            use_group_context=False,
            use_project_context=False,
        )

    assert result["formatted"] == [
        {
            "role": "assistant",
            "content": "",
            "reasoning": "checking",
            "reasoning_details": reasoning_details,
            "_openrouter_responses_reasoning_items": [response_reasoning_item],
            "tool_calls": [
                {
                    "id": "openrouter-call-1",
                    "_openrouter_item_id": "openrouter-call-1",
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "arguments": '{"location":"Berlin"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "openrouter-call-1",
            "content": "sunny",
        },
    ]
    assert _openrouter_convert_history_to_responses_input(result["formatted"]) == [
        response_reasoning_item,
        {
            "type": "function_call",
            "id": "openrouter-call-1",
            "call_id": "openrouter-call-1",
            "name": "weather",
            "arguments": '{"location":"Berlin"}',
        },
        {
            "type": "function_call_output",
            "call_id": "openrouter-call-1",
            "output": "sunny",
        },
    ]


def test_openrouter_responses_replay_rejects_chat_reasoning_detail_shapes():
    from app.llm.openrouter.utils import (
        _append_openrouter_response_reasoning_items,
    )

    exact_item = {
        "type": "reasoning",
        "id": "rs_exact",
        "encrypted_content": "opaque-state",
        "summary": [],
    }
    target = []

    assert _append_openrouter_response_reasoning_items(
        target,
        [
            {
                "type": "reasoning.encrypted",
                "id": "reasoning-detail",
                "data": "chat-only-state",
            },
            exact_item,
        ],
    )
    assert target == [exact_item]
    assert target[0] is not exact_item


def test_openrouter_responses_normalizes_image_and_video_input_shapes():
    from app.llm.openrouter.utils import (
        _openrouter_transform_content_part_for_responses,
    )

    assert _openrouter_transform_content_part_for_responses(
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAAA"},
        }
    ) == {
        "type": "input_image",
        "image_url": "data:image/png;base64,AAAA",
        "detail": "auto",
    }
    assert _openrouter_transform_content_part_for_responses(
        {
            "type": "input_video",
            "video_url": {"url": "https://example.test/video.mp4"},
        }
    ) == {
        "type": "input_video",
        "video_url": "https://example.test/video.mp4",
    }


def test_openrouter_responses_history_replays_completed_assistant_messages():
    """Assistant history must use the required Responses replay envelope."""
    from app.llm.openrouter.utils import _openrouter_convert_history_to_responses_input

    converted = _openrouter_convert_history_to_responses_input(
        [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {
                "type": "message",
                "role": "assistant",
                "id": "msg_provider_1",
                "content": [{"type": "text", "text": "Preserve this ID"}],
            },
        ]
    )

    assert converted == [
        {"type": "message", "role": "user", "content": "Hello"},
        {
            "type": "message",
            "role": "assistant",
            "id": "omlorix_assistant_0",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Hi there"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "id": "msg_provider_1",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Preserve this ID"}],
        },
    ]


def test_openrouter_attachment_and_empty_structured_replay_use_legacy_path(tmp_path):
    from app.llm.openrouter.utils import reformat_chat_history

    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    blocks_with_image = [
        {"type": "reasoning", "content": "inspect image"},
        build_tool_call_block("vision", {}, tool_call_id="openrouter-call"),
    ]
    call_without_id = {"type": "tool_call", "content": "legacy_tool({})", "meta": {}}

    with (
        patch(
            "app.llm.openrouter.utils.get_user_group_setting_value",
            return_value=False,
        ),
        patch(
            "app.llm.openrouter.utils.get_file_info",
            return_value={
                "file_category": "image",
                "file_type": "image/png",
                "file_name": "image.png",
                "path": str(image_path),
                "meta": {"original_filename": "image.png"},
            },
        ),
    ):
        result = reformat_chat_history(
            [
                {"role": "assistant", "content": blocks_with_image, "images": ["image-1"]},
                {"role": "assistant", "content": [call_without_id]},
            ],
            user_id="user-1",
            db=None,
            input_formats_allowed=["image"],
            use_group_context=False,
            use_project_context=False,
        )

    content = result["formatted"][0]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    text = "\n".join(part.get("text", "") for part in content)
    assert "vision({})" in text
    assert "legacy_tool({})" in text


def test_chat_completions_history_replays_exact_tool_ids():
    from app.llm.openai_chat_completions.utils import reformat_chat_history

    blocks = [
        build_tool_call_block(
            "weather",
            {"location": "Berlin"},
            tool_call_id="chat-call-1",
        ),
        {
            "type": "tool_call_result",
            "content": "sunny",
            "meta": {"tool_call_id": "chat-call-1"},
        },
    ]

    with patch(
        "app.llm.openai_chat_completions.utils.get_user_group_setting_value",
        return_value=False,
    ):
        result = reformat_chat_history(
            [{"role": "assistant", "content": blocks}],
            user_id=None,
            db=None,
            upload_files_bool=False,
            use_group_context=False,
            use_project_context=False,
            is_chat_completions_api=True,
        )

    assert result["formatted"] == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "chat-call-1",
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "arguments": '{"location":"Berlin"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "chat-call-1",
            "content": "sunny",
        },
    ]


def test_chat_completions_attachment_and_empty_structured_replay_use_legacy_path():
    from app.llm.openai_chat_completions.utils import reformat_chat_history

    attachment_block = build_tool_call_block("inspect", {}, tool_call_id="chat-call")
    attachment_block["documents"] = ["document-1"]
    call_without_id = {"type": "tool_call", "content": "legacy_tool({})", "meta": {}}
    uploaded_part = {"type": "file", "file": {"file_id": "document-1"}}

    with (
        patch(
            "app.llm.openai_chat_completions.utils.get_user_group_setting_value",
            return_value=False,
        ),
        patch(
            "app.llm.openai_chat_completions.utils.upload_files",
            return_value={
                "parts": [uploaded_part],
                "counters": {},
                "unsupported": False,
            },
        ) as upload_mock,
    ):
        result = reformat_chat_history(
            [
                {"role": "assistant", "content": [attachment_block]},
                {"role": "assistant", "content": [call_without_id]},
            ],
            user_id="user-1",
            db=None,
            use_group_context=False,
            use_project_context=False,
            is_chat_completions_api=True,
        )

    assert upload_mock.call_args.args[1] == ["document-1"]
    assert uploaded_part in result["formatted"][0]["content"]
    assert result["formatted"][1]["content"] == [
        {"type": "text", "text": "Tool call: legacy_tool({})"}
    ]


def test_ollama_history_replays_thinking_and_exact_tool_ids():
    from app.llm.ollama.utils import reformat_chat_history

    blocks = [
        {"type": "reasoning", "content": "checking"},
        build_tool_call_block(
            "weather",
            {"location": "Berlin"},
            tool_call_id="ollama-call-1",
        ),
        {
            "type": "tool_call_result",
            "content": "sunny",
            "meta": {"tool_call_id": "ollama-call-1"},
        },
    ]

    with patch(
        "app.llm.ollama.utils.get_user_group_setting_value",
        return_value=False,
    ):
        result = reformat_chat_history(
            [{"role": "assistant", "content": blocks}],
            user_id=None,
            db=None,
            use_group_context=False,
            use_project_context=False,
        )

    assert result["formatted"] == [
        {
            "role": "assistant",
            "content": "",
            "thinking": "checking",
            "tool_calls": [
                {
                    "id": "ollama-call-1",
                    "function": {
                        "name": "weather",
                        "arguments": {"location": "Berlin"},
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "sunny",
            "tool_name": "weather",
            "tool_call_id": "ollama-call-1",
        },
    ]


def test_ollama_attachment_and_empty_structured_replay_use_legacy_path():
    from app.llm.ollama.utils import reformat_chat_history

    call_without_id = {"type": "tool_call", "content": "legacy_tool({})", "meta": {}}

    with patch(
        "app.llm.ollama.utils.get_user_group_setting_value",
        return_value=False,
    ):
        result = reformat_chat_history(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "reasoning", "content": "checking attachments"},
                        call_without_id,
                    ],
                    "images": ["image-1"],
                    "documents": ["document-1"],
                },
                {"role": "assistant", "content": [call_without_id]},
            ],
            user_id=None,
            db=None,
            input_formats_allowed=["text"],
            use_group_context=False,
            use_project_context=False,
        )

    assert result["unsupported_file_ids"] == ["document-1", "image-1"]
    assert "legacy_tool({})" in result["formatted"][0]["content"]
    assert result["formatted"][1]["content"] == "legacy_tool({})"
