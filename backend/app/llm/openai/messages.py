"""OpenAI chat-history normalization and message formatting.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "reformat_chat_history": (
        "_sanitize_openai_reasoning_item",
        "copy",
        "extract_tool_call_block",
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
    "_sanitize_openai_reasoning_item",
    "copy",
    "extract_tool_call_block",
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
    note_ids: list[str] | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
    image_detail=None,
):
    """Reformat chat history."""
    text_type_user = "input_text"
    text_type_assistant = "output_text"
    legacy_tool_call_counter = 0

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
            "id",
            "role",
            "content",
            "images",
            "videos",
            "audios",
            "documents",
            "youtube",
            "tool_name",
            "tool_call_id",
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

    def _next_legacy_tool_call_id(message_identifier: str | None = None):
        nonlocal legacy_tool_call_counter
        legacy_tool_call_counter += 1
        base = str(message_identifier or "message").strip() or "message"
        sanitized = "".join(
            ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in base
        )
        return f"history_tool_call_{sanitized}_{legacy_tool_call_counter}"

    def _extract_tool_block_meta(block):
        extracted = extract_tool_call_block(block)
        raw_content = _coerce_text_from_block(block)
        return {
            "tool_name": extracted["tool_name"],
            "call_id": extracted["tool_call_id"],
            "namespace": extracted["tool_namespace"],
            "arguments": extracted["arguments"],
            "raw_content": raw_content or "",
        }

    def _extract_tool_search_call_payload(block):
        meta = (
            block.get("meta")
            if isinstance(block, dict) and isinstance(block.get("meta"), dict)
            else {}
        )
        if not meta.get("tool_search_call"):
            return None
        arguments = meta.get("tool_search_arguments")
        if not isinstance(arguments, dict):
            arguments = _decode_jsonish(meta.get("arguments"))
        if not isinstance(arguments, dict):
            raw_content = _coerce_text_from_block(block)
            arguments = _decode_jsonish(
                extract_tool_call_block(block).get("arguments") or raw_content
            )
        if not isinstance(arguments, dict):
            arguments = {}
        execution = (
            str(meta.get("tool_search_execution") or "server").strip() or "server"
        )
        status = (
            str(meta.get("tool_search_status") or "completed").strip() or "completed"
        )
        return {
            "type": "tool_search_call",
            "execution": execution,
            "call_id": meta.get("tool_call_id") or meta.get("tool_search_call_id"),
            "status": status,
            "arguments": arguments,
        }

    def _extract_tool_search_output_payload(block):
        meta = (
            block.get("meta")
            if isinstance(block, dict) and isinstance(block.get("meta"), dict)
            else {}
        )
        if not meta.get("tool_search_output"):
            return None
        tools = meta.get("tool_search_tools")
        if not isinstance(tools, list):
            tools = _decode_jsonish(_coerce_text_from_block(block))
        if not isinstance(tools, list):
            tools = []
        execution = (
            str(meta.get("tool_search_execution") or "server").strip() or "server"
        )
        status = (
            str(meta.get("tool_search_status") or "completed").strip() or "completed"
        )
        return {
            "type": "tool_search_output",
            "execution": execution,
            "call_id": meta.get("tool_search_call_id"),
            "status": status,
            "tools": tools,
        }

    def _normalize_function_output_parts(output):
        if output in (None, ""):
            return []
        if isinstance(output, list):
            return [part for part in output if isinstance(part, dict)]
        if isinstance(output, str):
            return [{"type": "input_text", "text": output}]
        return [{"type": "input_text", "text": str(output)}]

    def _collapse_function_output_parts(parts):
        normalized_parts = [part for part in parts if isinstance(part, dict)]
        if not normalized_parts:
            return ""
        if (
            len(normalized_parts) == 1
            and normalized_parts[0].get("type") == "input_text"
            and isinstance(normalized_parts[0].get("text"), str)
        ):
            return normalized_parts[0]["text"]
        return normalized_parts

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

    def _upload_file_ids(file_ids: list[str]):
        nonlocal counters, unsupported_flag
        if not file_ids:
            return []
        if not upload_files_bool:
            unsupported_flag = True
            merge_unsupported_file_ids(unsupported_file_ids, file_ids)
            return []
        upload_result = upload_files(
            db,
            file_ids,
            user_id,
            counters,
            input_formats_allowed,
            image_detail,
        )
        counters = upload_result.get("counters", counters) or counters
        if upload_result.get("unsupported"):
            unsupported_flag = True
            merge_unsupported_file_ids(
                unsupported_file_ids, upload_result.get("unsupported_file_ids")
            )
        return upload_result.get("parts", []) or []

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
    notes_start_index = len(formatted)
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
            logger.warning("[OpenAI] Notes context attach failed: %s", exc)

    memories_start_index = len(formatted)
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
            logger.warning("[OpenAI] Memories context attach failed: %s", exc)

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
                block_text = _coerce_text_from_block(block)
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

            if role == "assistant":
                assistant_parts: list[dict] = []
                last_tool_call_id: str | None = None
                consumed_file_ids: set[str] = set()
                message_identifier = msg_dict.get("id")

                # Stateless/ZDR GPT-5.6 chats keep opaque encrypted reasoning
                # state inside the hidden reasoning block. Replay it as a native
                # Responses item before the visible assistant message; never
                # flatten the encrypted value into output text.
                replayed_reasoning_items: list[dict[str, Any]] = []
                for candidate_block in message_blocks:
                    if not isinstance(candidate_block, dict):
                        continue
                    candidate_meta = candidate_block.get("meta")
                    if not isinstance(candidate_meta, dict):
                        continue
                    for raw_item in candidate_meta.get("openai_reasoning_items") or []:
                        sanitized_item = _sanitize_openai_reasoning_item(raw_item)
                        if sanitized_item:
                            replayed_reasoning_items.append(sanitized_item)
                formatted.extend(replayed_reasoning_items)

                def _flush_assistant_parts():
                    if not assistant_parts:
                        return
                    formatted.append(
                        {
                            "role": "assistant",
                            "content": list(assistant_parts),
                        }
                    )
                    assistant_parts.clear()

                def _append_function_output(
                    call_id: str,
                    text: str | None = None,
                    extra_parts: list[dict] | None = None,
                ):
                    output_parts = []
                    if isinstance(text, str) and text:
                        output_parts.append({"type": "input_text", "text": text})
                    if extra_parts:
                        output_parts.extend(extra_parts)
                    if not output_parts:
                        return

                    if (
                        formatted
                        and isinstance(formatted[-1], dict)
                        and formatted[-1].get("type") == "function_call_output"
                        and formatted[-1].get("call_id") == call_id
                    ):
                        merged_parts = _normalize_function_output_parts(
                            formatted[-1].get("output")
                        )
                        merged_parts.extend(output_parts)
                        formatted[-1]["output"] = _collapse_function_output_parts(
                            merged_parts
                        )
                        return

                    formatted.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": _collapse_function_output_parts(output_parts),
                        }
                    )

                for block_index, block in enumerate(message_blocks):
                    if not isinstance(block, dict):
                        continue

                    block_type = str(block.get("type") or "").strip().lower()
                    block_files = _collect_block_file_ids(block)
                    block_file_ids = (
                        block_files["images"]
                        + block_files["audios"]
                        + block_files["documents"]
                    )
                    block_video_ids = block_files["videos"]
                    if block_video_ids:
                        unsupported_flag = True
                        merge_unsupported_file_ids(
                            unsupported_file_ids, block_video_ids
                        )

                    if block_type == "tool_call":
                        if not include_tool_content:
                            continue
                        _flush_assistant_parts()
                        tool_search_call_payload = _extract_tool_search_call_payload(
                            block
                        )
                        if tool_search_call_payload is not None:
                            formatted.append(tool_search_call_payload)
                            continue
                        tool_meta = _extract_tool_block_meta(block)
                        tool_name = tool_meta["tool_name"] or "tool"
                        call_id = tool_meta["call_id"] or _next_legacy_tool_call_id(
                            f"{message_identifier or 'assistant'}_{block_index}"
                        )
                        arguments = tool_meta["arguments"] or "{}"
                        formatted.append(
                            {
                                "type": "function_call",
                                "call_id": call_id,
                                "name": tool_name,
                                "namespace": tool_meta["namespace"],
                                "arguments": arguments,
                                "status": "completed",
                            }
                        )
                        last_tool_call_id = call_id
                        continue

                    if block_type == "reasoning" and replayed_reasoning_items:
                        # The native reasoning item already carries its summary;
                        # adding "Reasoning: ..." as assistant output would alter
                        # the prior response and waste context tokens.
                        continue

                    if block_type in {"tool_call_result", "file_gen"}:
                        if not include_tool_content:
                            continue
                        _flush_assistant_parts()
                        tool_search_output_payload = (
                            _extract_tool_search_output_payload(block)
                        )
                        if tool_search_output_payload is not None:
                            formatted.append(tool_search_output_payload)
                            continue
                        tool_meta = _extract_tool_block_meta(block)
                        call_id = (
                            tool_meta["call_id"]
                            or last_tool_call_id
                            or _next_legacy_tool_call_id(
                                f"{message_identifier or 'assistant'}_{block_index}"
                            )
                        )
                        last_tool_call_id = call_id
                        block_upload_parts = _upload_file_ids(block_file_ids)
                        consumed_file_ids.update(block_file_ids)
                        block_text = _coerce_text_from_block(block)
                        if block_type == "file_gen" and block_text:
                            block_text = _format_block_text(block_type, block_text)
                        _append_function_output(call_id, block_text, block_upload_parts)
                        continue

                    formatted_text = _format_block_text(
                        block_type, _coerce_text_from_block(block)
                    )
                    if formatted_text:
                        assistant_parts.append(
                            {"type": text_type_assistant, "text": formatted_text}
                        )

                remaining_file_ids = [
                    file_id for file_id in file_ids if file_id not in consumed_file_ids
                ]
                if remaining_file_ids and last_tool_call_id:
                    _append_function_output(
                        last_tool_call_id, None, _upload_file_ids(remaining_file_ids)
                    )

                _flush_assistant_parts()
                continue

            upload_parts: list[dict] = []
            if file_ids:
                upload_parts = _upload_file_ids(file_ids)

            # --- Tool Messages ---
            if role == "tool":
                tool_name = msg_dict.get("tool_name") or msg_dict.get("name") or "tool"
                tool_result = "\n\n".join(text_segments).strip()
                tool_call_id = msg_dict.get("tool_call_id")
                if include_tool_content and tool_call_id:
                    output_parts = []
                    if tool_result:
                        output_parts.append({"type": "input_text", "text": tool_result})
                    if upload_parts:
                        output_parts.extend(upload_parts)
                    if output_parts:
                        formatted.append(
                            {
                                "type": "function_call_output",
                                "call_id": str(tool_call_id),
                                "output": _collapse_function_output_parts(output_parts),
                            }
                        )
                elif include_tool_content and tool_result:
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
        "context_prefix_count": history_start_index,
        "context_sections": [("workspace", 0, notes_start_index, True, 90),
                             ("notes", notes_start_index, memories_start_index, False, 60),
                             ("memories", memories_start_index, history_start_index, False, 40)],
        "unsupported": unsupported_flag,
        "unsupported_file_ids": sorted(unsupported_file_ids),
    }
