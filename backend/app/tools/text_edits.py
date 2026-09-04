"""Shared bounded reads and atomic exact-range edits for text artifacts."""

from __future__ import annotations

import re
from typing import Any


MAX_BATCH_TEXT_EDITS = 50
DEFAULT_TOOL_TEXT_READ_CHARS = 20_000
MAX_TOOL_TEXT_READ_CHARS = 100_000
MAX_TOOL_TEXT_QUERY_CHARS = 200
MAX_TOOL_TEXT_HEADING_CHARS = 500


def normalize_tool_text_query(query: Any) -> str | None:
    """Normalize and bound a model-authored text search before DB or memory use."""

    normalized = str(query or "").strip()
    if not normalized:
        return None
    if len(normalized) > MAX_TOOL_TEXT_QUERY_CHARS:
        raise ValueError(
            f"query must be {MAX_TOOL_TEXT_QUERY_CHARS} characters or fewer."
        )
    return normalized


def _find_unique(content: str, snippet: str, label: str, artifact_label: str) -> int:
    if not snippet:
        raise ValueError(f"{label} is required for a snippet update.")
    first_index = content.find(snippet)
    if first_index < 0:
        raise ValueError(f"{label} was not found in the existing {artifact_label}.")
    if content.find(snippet, first_index + 1) >= 0:
        raise ValueError(f"{label} matched more than once. Provide a longer unique snippet.")
    return first_index


def _resolve_edit_range(
    content: str,
    *,
    start_snippet: str,
    end_snippet: str,
    artifact_label: str,
) -> tuple[int, int]:
    start_index = _find_unique(content, start_snippet, "start_snippet", artifact_label)
    if start_snippet == end_snippet:
        return start_index, start_index + len(end_snippet)

    search_from = start_index + len(start_snippet)
    end_start_index = content.find(end_snippet, search_from)
    if end_start_index < 0:
        raise ValueError(
            f"end_snippet was not found after start_snippet in the existing {artifact_label}."
        )
    if content.find(end_snippet, end_start_index + 1) >= 0:
        raise ValueError(
            "end_snippet matched more than once after start_snippet. "
            "Provide a longer unique snippet."
        )
    return start_index, end_start_index + len(end_snippet)


def apply_atomic_text_edits(
    existing_content: str,
    edits: list[dict[str, Any]],
    *,
    artifact_label: str,
) -> str:
    """Resolve all edits against one snapshot and apply them in one operation."""

    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must contain at least one text edit.")
    if len(edits) > MAX_BATCH_TEXT_EDITS:
        raise ValueError(f"edits may contain at most {MAX_BATCH_TEXT_EDITS} entries.")

    resolved: list[tuple[int, int, str, int]] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError(f"edits[{index}] must be an object.")
        start_snippet = str(edit.get("start_snippet") or "")
        end_snippet = str(edit.get("end_snippet") or "")
        if "content" not in edit:
            raise ValueError(f"edits[{index}].content is required.")
        replacement = str(edit.get("content") or "")
        start, end = _resolve_edit_range(
            existing_content,
            start_snippet=start_snippet,
            end_snippet=end_snippet,
            artifact_label=artifact_label,
        )
        resolved.append((start, end, replacement, index))

    ordered = sorted(resolved, key=lambda item: (item[0], item[1]))
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise ValueError(
                f"edits[{previous[3]}] and edits[{current[3]}] overlap. "
                "Batch edits must target separate ranges in the same snapshot."
            )

    updated = existing_content
    for start, end, replacement, _index in reversed(ordered):
        updated = f"{updated[:start]}{replacement}{updated[end:]}"
    return updated


def apply_single_text_edit(
    existing_content: str,
    *,
    start_snippet: str | None,
    end_snippet: str | None,
    replacement_content: str,
    artifact_label: str,
) -> str:
    if start_snippet is None and end_snippet is None:
        return replacement_content
    if start_snippet is None or end_snippet is None:
        raise ValueError(
            "Both start_snippet and end_snippet are required for a snippet update."
        )
    return apply_atomic_text_edits(
        existing_content,
        [
            {
                "start_snippet": start_snippet,
                "end_snippet": end_snippet,
                "content": replacement_content,
            }
        ],
        artifact_label=artifact_label,
    )


def _heading_slice(content: str, heading: str) -> tuple[str, int]:
    target = str(heading or "").strip().lstrip("#").strip().casefold()
    if not target:
        raise ValueError("heading must not be empty.")
    lines = content.splitlines(keepends=True)
    start_index = None
    start_level = None
    offset = 0
    end_index = len(content)
    for line in lines:
        stripped = line.lstrip()
        hashes = len(stripped) - len(stripped.lstrip("#"))
        heading_text = stripped[hashes:].strip() if hashes else ""
        if start_index is None:
            if hashes and heading_text.casefold() == target:
                start_index = offset
                start_level = hashes
        elif hashes and hashes <= int(start_level or 0):
            end_index = offset
            break
        offset += len(line)
    if start_index is None:
        raise ValueError("heading was not found in the document.")
    return content[start_index:end_index], start_index


def select_text_content(
    content: str,
    *,
    heading: str | None = None,
    query: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int | None = DEFAULT_TOOL_TEXT_READ_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Return one bounded selection and metadata describing the selected snapshot."""

    source = str(content or "")
    heading_text = str(heading or "").strip()
    if len(heading_text) > MAX_TOOL_TEXT_HEADING_CHARS:
        raise ValueError(
            f"heading must be {MAX_TOOL_TEXT_HEADING_CHARS} characters or fewer."
        )
    query_text = normalize_tool_text_query(query)
    requested_modes = sum(
        bool(value)
        for value in (
            heading_text,
            query_text,
            start_line is not None or end_line is not None,
        )
    )
    if requested_modes > 1:
        raise ValueError("Use only one of heading, query, or line range for a read.")

    try:
        limit = (
            int(max_chars)
            if max_chars is not None
            else DEFAULT_TOOL_TEXT_READ_CHARS
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("max_chars must be an integer.") from exc
    if limit < 1 or limit > MAX_TOOL_TEXT_READ_CHARS:
        raise ValueError(
            f"max_chars must be between 1 and {MAX_TOOL_TEXT_READ_CHARS}."
        )

    mode = "prefix"
    selected = source
    source_start = 0
    selected_start_line = 1
    selected_end_line = max(1, len(source.splitlines()))

    if heading_text:
        mode = "heading"
        selected, source_start = _heading_slice(source, heading_text)
        selected_start_line = source.count("\n", 0, source_start) + 1
        selected_end_line = selected_start_line + max(0, len(selected.splitlines()) - 1)
    elif query_text:
        mode = "query"
        match = re.search(re.escape(query_text), source, flags=re.IGNORECASE)
        if match is None:
            raise ValueError("query was not found in the document.")
        match_start, match_end = match.span()
        match_chars = match_end - match_start
        if match_chars >= limit:
            source_start = match_start
        else:
            source_start = max(0, match_start - (limit - match_chars) // 2)
        source_end = min(len(source), source_start + limit)
        source_start = max(0, source_end - limit)
        selected = source[source_start:source_end]
        selected_start_line = source.count("\n", 0, source_start) + 1
        selected_end_line = selected_start_line + max(0, len(selected.splitlines()) - 1)
    elif start_line is not None or end_line is not None:
        mode = "lines"
        try:
            first = int(start_line or 1)
            last = int(end_line or first)
        except (TypeError, ValueError) as exc:
            raise ValueError("start_line and end_line must be integers.") from exc
        if first < 1 or last < first:
            raise ValueError("Line ranges require 1 <= start_line <= end_line.")
        lines = source.splitlines(keepends=True)
        if first > max(1, len(lines)):
            raise ValueError("start_line is beyond the end of the document.")
        selected = "".join(lines[first - 1 : last])
        source_start = len("".join(lines[: first - 1]))
        selected_start_line = first
        selected_end_line = min(last, max(1, len(lines)))

    selection_chars = len(selected)
    truncated = selection_chars > limit
    returned = selected[:limit]
    metadata = {
        "mode": mode,
        "start_line": selected_start_line,
        "end_line": selected_end_line,
        "source_start": source_start,
        "total_chars": len(source),
        "selection_chars": selection_chars,
        "returned_chars": len(returned),
        "truncated": truncated or (mode == "prefix" and len(source) > len(returned)),
    }
    return returned, metadata
