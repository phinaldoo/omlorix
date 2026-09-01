import json
import sys
import types

sys.modules.setdefault("zstandard", types.SimpleNamespace())

from app.chats.utils import _convert_temp_message_to_blocks, _hydrate_content_blocks


def test_hydrate_content_blocks_redacts_tool_result_content():
    raw = json.dumps(
        [
            {
                "type": "tool_call_result",
                "content": '{"status":"ok"}',
                "meta": {
                    "tool_name": "weather",
                    "deep_research_activity": {
                        "schema_version": 1,
                        "events": [
                            {
                                "event": "tool_call",
                                "tool": "web_search",
                                "arguments": {"query": "primary source"},
                            }
                        ],
                    },
                },
            },
            {
                "type": "widget",
                "content": "<div>widget</div>",
                "meta": {"widget_type": "weather"},
            },
        ]
    )

    blocks = _hydrate_content_blocks(raw, lambda _file_id: None)

    assert "content" not in blocks[0]
    assert blocks[0]["meta"]["deep_research_activity"]["events"][0][
        "arguments"
    ] == {"query": "primary source"}
    assert blocks[1]["content"] == "<div>widget</div>"


def test_convert_temp_message_to_blocks_prefers_non_reasoning_primary_block_for_attachments():
    blocks = _convert_temp_message_to_blocks(
        {
            "role": "assistant",
            "content": [
                {"type": "reasoning", "content": "Thinking"},
                {"type": "tool_call_result", "content": '{"status":"ok"}'},
            ],
            "documents": ["doc-1"],
        }
    )

    assert "documents" not in blocks[0]
    assert blocks[1]["documents"] == ["doc-1"]
