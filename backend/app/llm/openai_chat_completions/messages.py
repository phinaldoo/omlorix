"""Chat Completions chat-history normalization and message formatting.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai_chat_completions import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "reformat_chat_history": (
        "Any",
        "copy",
        "extract_tool_call_block",
        "format_tool_call_block_label",
        "get_group_context_end",
        "get_group_context_start",
        "get_project_context_end",
        "get_project_context_start",
        "get_user_group_setting_value",
        "json",
        "logger",
        "merge_unsupported_file_ids",
        "safe_list_project_files",
        "upload_files",
    ),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate dependencies before definitions so annotations and defaults retain
# exactly the same evaluation behavior as in the original module.
for _dependency_name in (
    "Any",
    "copy",
    "extract_tool_call_block",
    "format_tool_call_block_label",
    "get_group_context_end",
    "get_group_context_start",
    "get_project_context_end",
    "get_project_context_start",
    "get_user_group_setting_value",
    "json",
    "logger",
    "merge_unsupported_file_ids",
    "safe_list_project_files",
    "upload_files",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_reformat_chat_history(
    chat_history,
    user_id: str | None = None,
    db=None,
    include_tool_content: bool = True,
    project_id: str | None = None,
    max_image_count: int | None = None,
    max_audio_count: int | None = None,
    max_document_count: int | None = None,
    upload_files_bool: bool = True,
    input_formats_allowed: list[str] | None = None,
    use_group_context: bool = True,
    use_project_context: bool = True,
    is_chat_completions_api: bool = False,
    note_ids: list[str] | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
    image_detail=None,
):
    text_type_user = "text"
    text_type_assistant = "text"

    if not chat_history:
        return {
            "formatted": [],
            "unsupported": False,
            "unsupported_file_ids": [],
        }

    def _msg_to_dict(msg):
        if isinstance(msg, dict):
            return msg
        if hasattr(msg, "model_dump") and callable(getattr(msg, "model_dump")):
            try:
                return msg.model_dump()
            except Exception:
                return {}
        if hasattr(msg, "dict") and callable(getattr(msg, "dict")):
            try:
                return msg.dict()
            except Exception:
                return {}
        keys = (
            "role",
            "content",
            "images",
            "videos",
            "audios",
            "documents",
            "youtube",
            "tool_name",
            "name",
            "system_instruction",
            "meta",
            "reasoning",
        )
        return {k: getattr(msg, k, None) for k in keys}

    def _decode_jsonish(raw):
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
            except Exception:
                return stripped
        return raw

    def _normalize_content_blocks(raw):
        decoded = _decode_jsonish(raw)
        if decoded is None:
            return []
        if isinstance(decoded, list):
            return decoded
        return [{"type": "content", "content": decoded}]

    def _filter_widget_blocks(blocks):
        if not isinstance(blocks, list):
            return blocks
        return [
            block
            for block in blocks
            if not (isinstance(block, dict) and block.get("type") == "widget")
        ]

    def _extract_attachment_ids(value):
        decoded = _decode_jsonish(value)
        if decoded is None:
            return []
        if isinstance(decoded, list):
            ids: list[str] = []
            for item in decoded:
                if isinstance(item, dict):
                    fid = item.get("id") or item.get("file_id")
                    if fid:
                        ids.append(str(fid))
                elif item is not None:
                    ids.append(str(item))
            return ids
        if isinstance(decoded, dict):
            fid = decoded.get("id") or decoded.get("file_id")
            return [str(fid)] if fid else []
        if isinstance(decoded, str):
            stripped = decoded.strip()
            if stripped:
                return [stripped]
        return []

    attachment_fields = ("images", "videos", "audios", "documents")

    def _collect_block_file_ids(block):
        collected: dict[str, list[str]] = {field: [] for field in attachment_fields}
        if not isinstance(block, dict):
            return collected
        for field in attachment_fields:
            collected[field] = _extract_attachment_ids(block.get(field))
        return collected

    def _coerce_text_from_block(block):
        if not isinstance(block, dict):
            return None
        text = block.get("content") or block.get("text")
        if isinstance(text, str):
            return text
        return None

    def _format_block_text(block_type: str | None, text: str | None):
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

    def _append_structured_assistant_history(message_blocks: list[Any]) -> bool:
        """Replay canonical tool blocks as Chat Completions messages."""
        structured_types = {
            str(block.get("type") or "").strip().lower()
            for block in message_blocks
            if isinstance(block, dict)
        }
        if not structured_types.intersection(
            {"reasoning", "tool_call", "tool_call_result"}
        ):
            return False

        formatted_start = len(formatted)
        text_segments: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        last_call_id: str | None = None

        def flush_assistant() -> None:
            if not text_segments and not tool_calls:
                return
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": "\n\n".join(text_segments),
            }
            if tool_calls:
                assistant_message["tool_calls"] = copy.deepcopy(tool_calls)
            formatted.append(assistant_message)
            text_segments.clear()
            tool_calls.clear()

        for block in message_blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type in {"widget", "file", "file_gen"}:
                continue
            if block_type == "reasoning":
                reasoning_text = block.get("content")
                if isinstance(reasoning_text, str) and reasoning_text:
                    # Chat Completions has no resumable reasoning item. Keep
                    # the visible transcript behavior as ordinary text.
                    text_segments.append(f"Reasoning: {reasoning_text}")
                continue
            if block_type in {"content", "assistant", "text"}:
                text_value = block.get("content") or block.get("text")
                if isinstance(text_value, str) and text_value:
                    text_segments.append(text_value)
                continue
            if block_type == "tool_call":
                extracted = extract_tool_call_block(block)
                call_id = str(extracted.get("tool_call_id") or "").strip()
                if not call_id:
                    continue
                arguments = extracted.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(
                        arguments or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": str(extracted.get("tool_name") or "tool"),
                            "arguments": arguments,
                        },
                    }
                )
                last_call_id = call_id
                continue
            if block_type == "tool_call_result":
                meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
                call_id = str(
                    meta.get("tool_call_id")
                    or meta.get("tool_use_id")
                    or last_call_id
                    or ""
                ).strip()
                if not call_id:
                    continue
                flush_assistant()
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": (
                            str(block.get("content") or "")
                            if include_tool_content
                            else ""
                        ),
                    }
                )

        flush_assistant()
        return len(formatted) > formatted_start

    def _build_reference_context_text() -> str:
        """Build selected-reference context so it can travel with the latest prompt."""
        segments: list[str] = []
        if reference_parts and isinstance(reference_parts, list):
            valid_parts = [
                p for p in reference_parts if isinstance(p, str) and p.strip()
            ]
            if valid_parts:
                ref_intro = (
                    "The user refers to the following parts from previous messages:\n\n"
                )
                ref_content = "\n\n---\n\n".join(
                    f'"{part.strip()}"' for part in valid_parts
                )
                segments.append(ref_intro + ref_content)
        if isinstance(chat_reference_context, str) and chat_reference_context.strip():
            segments.append(chat_reference_context.strip())
        return "\n\n".join(segments).strip()

    def _append_reference_context_to_latest_user(
        reference_text: str, history_start_index: int
    ) -> None:
        """Attach reference context before the newest user prompt instead of creating early history."""
        if not reference_text:
            return
        reference_part = {"type": text_type_user, "text": reference_text}
        for entry in reversed(formatted[history_start_index:]):
            if not isinstance(entry, dict) or entry.get("role") != "user":
                continue
            content = entry.get("content")
            if isinstance(content, list):
                content.insert(0, reference_part)
            elif isinstance(content, str) and content:
                entry["content"] = [
                    reference_part,
                    {"type": text_type_user, "text": content},
                ]
            else:
                entry["content"] = [reference_part]
            return
        formatted.append({"role": "user", "content": [reference_part]})

    formatted = []
    # Initialize counters
    counters = {
        "image": {"count": 0, "max": max_image_count or -1},
        "document": {"count": 0, "max": max_document_count or -1},
        "audio": {"count": 0, "max": max_audio_count or -1},
    }

    unsupported_flag = False
    unsupported_file_ids: set[str] = set()

    group_context_enabled = False
    if user_id and db:
        try:
            group_context_enabled = get_user_group_setting_value(
                user_id, "context", "enable_group_context", db
            )
        except Exception as exc:
            logger.warning(
                "[OpenAI] Failed to read group context setting for user %s: %s",
                user_id,
                exc,
            )

    if use_group_context and group_context_enabled and upload_files_bool:
        try:
            group_context_raw = get_group_context_start(db, user_id) or {}
        except Exception as exc:
            logger.warning(
                "[OpenAI] Failed to load group context for user %s: %s", user_id, exc
            )
            group_context_raw = {}

        group_context_text = group_context_raw.get("context", "")
        group_context_file_ids = (
            group_context_raw.get("group_context_file_ids", []) or []
        )

        if group_context_text:
            formatted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": text_type_user,
                            "text": group_context_text,
                        }
                    ],
                }
            )

        if group_context_file_ids and upload_files_bool:
            context_counters = copy.deepcopy(counters) if counters else None
            try:
                upload_result = upload_files(
                    db,
                    [str(file_id) for file_id in group_context_file_ids],
                    user_id,
                    context_counters,
                    input_formats_allowed,
                    image_detail,
                )
            except Exception as exc:
                logger.warning(
                    "[OpenAI] Failed to load group context files for user %s: %s",
                    user_id,
                    exc,
                )
                upload_result = {"parts": [], "counters": counters, "unsupported": True}

            parts = upload_result.get("parts", [])
            if parts:
                formatted.append({"role": "user", "content": parts})

            if upload_result.get("unsupported"):
                unsupported_flag = True
                merge_unsupported_file_ids(
                    unsupported_file_ids, upload_result.get("unsupported_file_ids")
                )

        if group_context_text or group_context_file_ids:
            try:
                group_context_end = get_group_context_end()
            except Exception as exc:
                logger.warning(
                    "[OpenAI] Failed to load group context end for user %s: %s",
                    user_id,
                    exc,
                )
            else:
                if group_context_end:
                    formatted.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": text_type_user,
                                    "text": group_context_end,
                                }
                            ],
                        }
                    )

    # --- Optional: attach project-level files once at the start ---
    if use_project_context and project_id and db and user_id:
        try:
            project_start = get_project_context_start(db, user_id, project_id)
            if project_start:
                formatted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": text_type_user,
                                "text": project_start,
                            }
                        ],
                    }
                )
        except Exception as exc:
            logger.warning(
                "[OpenAI] Project instruction attach failed: %s",
                exc,
            )
        response = safe_list_project_files(
            db,
            user_id,
            project_id,
            logger=logger,
            log_prefix="[OpenAI]",
            failure_message="Project file attach failed",
        )
        file_ids = []
        for item in response:
            file_id = (
                item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            )
            if file_id:
                file_ids.append(str(file_id))
        if file_ids and upload_files_bool:
            project_counters = copy.deepcopy(counters) if counters else None
            upload_result = upload_files(
                db,
                file_ids,
                user_id,
                project_counters,
                input_formats_allowed,
                image_detail,
            )
            project_parts = upload_result.get("parts", [])
            if project_parts:
                formatted.append({"role": "user", "content": project_parts})
            if upload_result.get("unsupported"):
                unsupported_flag = True
                merge_unsupported_file_ids(
                    unsupported_file_ids, upload_result.get("unsupported_file_ids")
                )
        elif file_ids:
            unsupported_flag = True
            merge_unsupported_file_ids(unsupported_file_ids, file_ids)
        try:
            project_end = get_project_context_end()
            if project_end:
                formatted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": text_type_user,
                                "text": project_end,
                            }
                        ],
                    }
                )
        except Exception as exc:
            logger.warning(
                "[OpenAI] Project instruction end attach failed: %s",
                exc,
            )

    # --- Optional: attach notes context if note_ids provided ---
    if note_ids and db and user_id:
        try:
            from app.llm.system_instruction.notes import (
                fetch_notes_for_chat,
                get_notes_context_start,
                get_notes_context_end,
            )

            notes_content = fetch_notes_for_chat(db, user_id, note_ids)
            if notes_content:
                notes_start = get_notes_context_start(notes_content)
                if notes_start:
                    formatted.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": text_type_user,
                                    "text": notes_start,
                                }
                            ],
                        }
                    )
                notes_end = get_notes_context_end()
                if notes_end:
                    formatted.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": text_type_user,
                                    "text": notes_end,
                                }
                            ],
                        }
                    )
        except Exception as exc:
            logger.warning(
                "[OpenAI Chat Completions] Notes context attach failed: %s", exc
            )

    if db and user_id:
        try:
            from app.llm.system_instruction.memories import get_memories_context

            memories_context = get_memories_context(db, user_id, project_id=project_id)
            if memories_context:
                formatted.append(
                    {
                        "role": "user",
                        "content": [{"type": text_type_user, "text": memories_context}],
                    }
                )
        except Exception as exc:
            logger.warning(
                "[OpenAI Chat Completions] Memories context attach failed: %s", exc
            )

    reference_context_text = _build_reference_context_text()
    history_start_index = len(formatted)

    # --- Process each chat message ---
    for msg in chat_history:
        try:
            msg_dict = _msg_to_dict(msg)
            role_value = msg_dict.get("role")
            if not role_value:
                continue
            role = str(role_value).lower()
            if role == "developer":
                role = "system"

            # normalize tool messages
            if role not in {"user", "assistant", "system", "tool"}:
                continue

            message_blocks = _normalize_content_blocks(msg_dict.get("content"))
            message_blocks = _filter_widget_blocks(message_blocks)
            if not message_blocks and msg_dict.get("system_instruction"):
                message_blocks = [
                    {
                        "type": "system_instruction",
                        "content": msg_dict.get("system_instruction"),
                    }
                ]
            has_assistant_attachments = any(
                _extract_attachment_ids(msg_dict.get(field))
                for field in attachment_fields
            ) or any(
                any(
                    _collect_block_file_ids(block)[field] for field in attachment_fields
                )
                for block in message_blocks
            )
            if (
                role == "assistant"
                and not has_assistant_attachments
                and _append_structured_assistant_history(message_blocks)
            ):
                continue

            attachment_tracker = {field: [] for field in attachment_fields}
            attachment_seen = {field: set() for field in attachment_fields}

            def _append_ids(field, values):
                for value in values:
                    if not value:
                        continue
                    if value in attachment_seen[field]:
                        continue
                    attachment_seen[field].add(value)
                    attachment_tracker[field].append(value)

            text_segments: list[str] = []
            for block in message_blocks:
                block_type = block.get("type") if isinstance(block, dict) else None
                files = _collect_block_file_ids(block)
                for field in attachment_fields:
                    _append_ids(field, files[field])
                block_text = (
                    format_tool_call_block_label(block)
                    if block_type == "tool_call"
                    else _coerce_text_from_block(block)
                )
                formatted_text = _format_block_text(block_type, block_text)
                if formatted_text:
                    text_segments.append(formatted_text)

            for field in attachment_fields:
                _append_ids(field, _extract_attachment_ids(msg_dict.get(field)))

            img_ids = attachment_tracker["images"]
            vid_ids = attachment_tracker["videos"]
            aud_ids = attachment_tracker["audios"]
            doc_ids = attachment_tracker["documents"]

            if vid_ids:
                unsupported_flag = True
                merge_unsupported_file_ids(unsupported_file_ids, vid_ids)

            file_ids = img_ids + aud_ids + doc_ids

            upload_parts: list[dict] = []
            if upload_files_bool and file_ids:
                upload_result = upload_files(
                    db,
                    file_ids,
                    user_id,
                    counters,
                    input_formats_allowed,
                    image_detail,
                )
                upload_parts = upload_result.get("parts", []) or []
                counters = upload_result.get("counters", counters) or counters
                if upload_result.get("unsupported"):
                    unsupported_flag = True
                    merge_unsupported_file_ids(
                        unsupported_file_ids, upload_result.get("unsupported_file_ids")
                    )
            elif file_ids and not upload_files_bool:
                unsupported_flag = True
                merge_unsupported_file_ids(unsupported_file_ids, file_ids)

            # --- Tool Messages ---
            if role == "tool":
                tool_name = msg_dict.get("tool_name") or msg_dict.get("name") or "tool"
                tool_result = "\n\n".join(text_segments).strip()
                if include_tool_content and tool_result:
                    formatted.append(
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": text_type_assistant,
                                    "text": f"[Tool: {tool_name}] {tool_result}",
                                }
                            ],
                        }
                    )
                continue

            text_type = text_type_assistant if role == "assistant" else text_type_user
            text_parts = [
                {"type": text_type, "text": segment}
                for segment in text_segments
                if segment
            ]

            message_parts = []
            if upload_parts:
                message_parts.extend(upload_parts)
            if text_parts:
                message_parts.extend(text_parts)

            if message_parts:
                formatted.append(
                    {
                        "role": role,
                        "content": message_parts,
                    }
                )

        except Exception as exc:
            logger.warning(f"[OpenAI] Skipping malformed message: {exc}")
            continue

    _append_reference_context_to_latest_user(
        reference_context_text, history_start_index
    )

    return {
        "formatted": formatted,
        "unsupported": unsupported_flag,
        "unsupported_file_ids": sorted(unsupported_file_ids),
    }
