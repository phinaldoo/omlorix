"""Shared normalization for provider chat-history adapters."""

import json


def decode_jsonish(raw):
    """Decode legacy JSON content while retaining ordinary text and structured values."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except (ValueError, RecursionError):
            return stripped
    return raw


def filter_widget_blocks(blocks):
    if not isinstance(blocks, list):
        return blocks
    return [
        block
        for block in blocks
        if not (isinstance(block, dict) and block.get("type") == "widget")
    ]


def format_block_text(block_type: str | None, text: str | None):
    if not text:
        return None
    normalized = (block_type or "").strip().lower()
    prefix_map = {
        "reasoning": "Reasoning:",
        "tool_call": "Tool call:",
        "tool_call_result": "Tool result:",
        "file_gen": "Generated file:",
    }
    prefix = prefix_map.get(normalized)
    if prefix:
        return f"{prefix} {text}".strip()
    return text


def build_reference_context_text(
    reference_parts: list[str] | None,
    chat_reference_context: str | None,
) -> str:
    """Build selected-reference context to attach to the latest user prompt."""
    segments: list[str] = []
    if isinstance(reference_parts, list):
        valid_parts = [p for p in reference_parts if isinstance(p, str) and p.strip()]
        if valid_parts:
            ref_intro = "The user refers to the following parts from previous messages:\n\n"
            ref_content = "\n\n---\n\n".join(f'"{part.strip()}"' for part in valid_parts)
            segments.append(ref_intro + ref_content)
    if isinstance(chat_reference_context, str) and chat_reference_context.strip():
        segments.append(chat_reference_context.strip())
    return "\n\n".join(segments).strip()
