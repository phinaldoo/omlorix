"""Google AI Studio chat-history normalization and message formatting.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "reformat_chat_history": (
        "AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS",
        "_build_aistudio_file_part",
        "base64",
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
        "normalize_unsupported_file_ids",
        "safe_list_project_files",
        "time",
        "types",
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
    "AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS",
    "_build_aistudio_file_part",
    "base64",
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
    "normalize_unsupported_file_ids",
    "safe_list_project_files",
    "time",
    "types",
    "upload_files",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_reformat_chat_history(
    chathistory,
    user_id: str | None = None,
    db=None,
    client=None,
    uploaded_cleanup: list[str] | None = None,
    include_tool_content: bool = True,
    project_id: str | None = None,
    native_youtube_video: bool = False,
    max_image_count: int | None = None,
    max_audio_count: int | None = None,
    max_video_count: int | None = None,
    max_document_count: int | None = None,
    max_youtube_video_count: int | None = None,
    upload_files_bool: bool = True,
    input_formats_allowed: list[str] | None = None,
    use_group_context: bool = True,
    use_project_context: bool = True,
    note_ids: list[str] | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
    video_metadata=None,
    file_active_deadline_monotonic: float | None = None,
):
    if file_active_deadline_monotonic is None:
        file_active_deadline_monotonic = (
            time.monotonic() + AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS
        )

    if not chathistory:
        return {
            "formatted": [],
            "uploaded_cleanup": uploaded_cleanup,
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
                pass
        if hasattr(msg, "dict") and callable(getattr(msg, "dict")):
            try:
                return msg.dict()
            except Exception:
                pass
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
            "thinking",
        )
        return {k: getattr(msg, k, None) for k in keys}

    def _ensure_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, (set, tuple)):
            return list(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                return [value]
            return [value]
        return [value]

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

    attachment_fields = ("images", "videos", "audios", "documents")

    def _collect_block_file_ids(blocks):
        collected: dict[str, list[str]] = {field: [] for field in attachment_fields}
        if not isinstance(blocks, list):
            return collected
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for field in attachment_fields:
                field_value = block.get(field)
                if not field_value:
                    continue
                field_items = _ensure_list(field_value)
                for item in field_items:
                    if item is None:
                        continue
                    if isinstance(item, dict):
                        fid = item.get("id") or item.get("file_id")
                        if fid:
                            collected[field].append(str(fid))
                    else:
                        collected[field].append(str(item))
        return collected

    def _coerce_text_from_block(block):
        if isinstance(block, str):
            return block
        if isinstance(block, dict):
            text_val = block.get("content") or block.get("text")
            if isinstance(text_val, list):
                return "\n".join(
                    str(item) for item in text_val if isinstance(item, str)
                ).strip()
            if isinstance(text_val, (str, int, float)):
                return str(text_val)
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

    def _block_meta(block: Any) -> dict[str, Any]:
        """Return provider metadata saved on one canonical Omlorix block."""
        if isinstance(block, dict) and isinstance(block.get("meta"), dict):
            return block["meta"]
        return {}

    def _google_thinking_signature(block: Any) -> str | None:
        """Read the base64 Gemini thought signature from supported layouts."""
        meta = _block_meta(block)
        signature = meta.get("thinking_signature")
        if isinstance(signature, dict):
            signature = signature.get("google_aistudio") or signature.get("google")
        google_meta = meta.get("google_aistudio")
        if not signature and isinstance(google_meta, dict):
            signature = google_meta.get("thinking_signature")
        normalized = str(signature or "").strip()
        return normalized or None

    def _google_model_turn_id(block: Any) -> str | None:
        """Return the persisted model-turn identifier for a Gemini tool block."""
        normalized = str(
            _block_meta(block).get("google_aistudio_model_turn_id") or ""
        ).strip()
        return normalized or None

    def _decode_google_signature(signature: str | None) -> bytes | None:
        """Decode an opaque signature without accepting a mutated payload."""
        if not signature:
            return None
        try:
            return base64.b64decode(signature, validate=True)
        except (ValueError, TypeError):
            return None

    def _append_structured_assistant_history(content_blocks: list[Any]) -> bool:
        """Expand a saved Gemini tool loop into native model/user contents.

        Gemini is stateless and validates thought signatures on the exact Part
        that produced them. Function responses likewise have to retain the
        original function-call ID. Older Omlorix rows put a call signature on
        the preceding visible block, so the backward lookup below repairs that
        known storage layout while leaving the opaque signature unchanged.
        """
        structured_types = {
            str(block.get("type") or "").strip().lower()
            for block in content_blocks
            if isinstance(block, dict)
        }
        if not structured_types.intersection(
            {"reasoning", "tool_call", "tool_call_result"}
        ):
            return False

        model_parts: list[types.Part] = []
        function_response_parts: list[types.Part] = []
        call_names: dict[str, str] = {}
        last_call_id: str | None = None
        active_function_turn_id: str | None = None
        active_function_turn_has_signature = False

        def flush_model() -> None:
            if model_parts:
                formatted.append(types.Content(role="model", parts=list(model_parts)))
                model_parts.clear()

        def flush_function_exchange() -> None:
            """Emit one complete model-call/user-response exchange.

            Omlorix persists each call next to its result for presentation, while
            Gemini requires every parallel call Part in one model Content and
            every corresponding response Part in the following user Content.
            """
            nonlocal active_function_turn_id
            nonlocal active_function_turn_has_signature
            if not function_response_parts:
                return
            flush_model()
            formatted.append(
                types.Content(role="user", parts=list(function_response_parts))
            )
            function_response_parts.clear()
            active_function_turn_id = None
            active_function_turn_has_signature = False

        def signature_for_call(block_index: int, block: dict) -> bytes | None:
            direct = _decode_google_signature(_google_thinking_signature(block))
            if direct:
                return direct
            # Releases before this fix stored the function-call Part's
            # signature on the preceding content/reasoning block.
            for prior in reversed(content_blocks[:block_index]):
                if not isinstance(prior, dict):
                    continue
                if str(prior.get("type") or "").lower() in {
                    "tool_call",
                    "tool_call_result",
                }:
                    break
                inherited = _decode_google_signature(_google_thinking_signature(prior))
                if inherited:
                    return inherited
            return None

        def signature_is_legacy_call_state(block_index: int) -> bool:
            """Detect signatures misplaced before an older unsigned call."""
            for candidate in content_blocks[block_index + 1 :]:
                if not isinstance(candidate, dict):
                    continue
                candidate_type = str(candidate.get("type") or "").lower()
                if candidate_type == "tool_call_result":
                    return False
                if candidate_type == "tool_call":
                    return not bool(_google_thinking_signature(candidate))
            return False

        for block_index, block in enumerate(content_blocks):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type in {"widget", "file", "file_gen"}:
                continue

            if block_type == "reasoning":
                flush_function_exchange()
                text_value = str(block.get("content") or "")
                if text_value:
                    part_signature = None
                    if not signature_is_legacy_call_state(block_index):
                        part_signature = _decode_google_signature(
                            _google_thinking_signature(block)
                        )
                    model_parts.append(
                        types.Part(
                            text=text_value,
                            thought=True,
                            thought_signature=part_signature,
                        )
                    )
                continue

            if block_type in {"content", "assistant", "text"}:
                flush_function_exchange()
                text_value = block.get("content") or block.get("text")
                if isinstance(text_value, str) and text_value:
                    # Do not leave a legacy function-call signature on the
                    # preceding text Part; signature_for_call moves it back.
                    part_signature = None
                    if not signature_is_legacy_call_state(block_index):
                        part_signature = _decode_google_signature(
                            _google_thinking_signature(block)
                        )
                    model_parts.append(
                        types.Part(
                            text=text_value,
                            thought_signature=part_signature,
                        )
                    )
                continue

            if block_type == "tool_call":
                extracted = extract_tool_call_block(block)
                call_id = str(extracted.get("tool_call_id") or "").strip()
                if not call_id:
                    continue
                tool_name = str(extracted.get("tool_name") or "tool")
                arguments = _decode_jsonish(extracted.get("arguments"))
                if not isinstance(arguments, dict):
                    arguments = {}
                call_names[call_id] = tool_name
                last_call_id = call_id
                signature = signature_for_call(block_index, block)
                if _block_meta(block).get("native_web_search"):
                    flush_function_exchange()
                    model_parts.append(
                        types.Part(
                            tool_call=types.ToolCall(
                                id=call_id,
                                tool_type="google_search",
                                args=arguments,
                            ),
                            thought_signature=signature,
                        )
                    )
                else:
                    call_turn_id = _google_model_turn_id(block)
                    if function_response_parts:
                        if active_function_turn_id and call_turn_id:
                            same_model_turn = active_function_turn_id == call_turn_id
                        elif active_function_turn_id or call_turn_id:
                            same_model_turn = False
                        else:
                            # Before model-turn IDs were persisted, Gemini 3
                            # parallel calls carried a signature only on the
                            # first call Part. A new signed call starts the next
                            # model turn; an unsigned call belongs to the active
                            # parallel group.
                            same_model_turn = (
                                active_function_turn_has_signature and signature is None
                            )
                        if not same_model_turn:
                            flush_function_exchange()
                    if call_turn_id:
                        active_function_turn_id = call_turn_id
                    if signature is not None:
                        active_function_turn_has_signature = True
                    model_parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                id=call_id,
                                name=tool_name,
                                args=arguments,
                            ),
                            thought_signature=signature,
                        )
                    )
                continue

            if block_type == "tool_call_result":
                meta = _block_meta(block)
                call_id = str(
                    meta.get("tool_call_id")
                    or meta.get("tool_use_id")
                    or last_call_id
                    or ""
                ).strip()
                if not call_id:
                    continue
                response_value = block.get("content") if include_tool_content else ""
                decoded_response = _decode_jsonish(response_value)
                if meta.get("native_web_search"):
                    flush_function_exchange()
                    payload = (
                        decoded_response if isinstance(decoded_response, dict) else {}
                    )
                    model_parts.append(
                        types.Part(
                            tool_response=types.ToolResponse(
                                id=call_id,
                                tool_type=str(
                                    payload.get("tool_type") or "google_search"
                                ),
                                response=payload.get("response") or payload,
                            )
                        )
                    )
                else:
                    result_turn_id = _google_model_turn_id(block)
                    if (
                        function_response_parts
                        and active_function_turn_id
                        and result_turn_id
                        and active_function_turn_id != result_turn_id
                    ):
                        flush_function_exchange()
                    if result_turn_id and not active_function_turn_id:
                        active_function_turn_id = result_turn_id
                    tool_name = (
                        call_names.get(call_id)
                        or str(block.get("tool_name") or "tool").split("(", 1)[0]
                    )
                    response_part = types.Part(
                        function_response=types.FunctionResponse(
                            id=call_id,
                            name=tool_name,
                            response={"result": decoded_response},
                        )
                    )
                    function_response_parts.append(response_part)
                continue

        flush_function_exchange()
        flush_model()
        return True

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
        reference_part = types.Part(text=reference_text)
        for entry in reversed(formatted[history_start_index:]):
            if getattr(entry, "role", None) != "user":
                continue
            parts = getattr(entry, "parts", None)
            if isinstance(parts, list):
                parts.insert(0, reference_part)
            else:
                entry.parts = [reference_part]
            return
        formatted.append(types.Content(role="user", parts=[reference_part]))

    # Initialize counters
    counters = {
        "image": {"count": 0, "max": max_image_count or -1},
        "document": {"count": 0, "max": max_document_count or -1},
        "audio": {"count": 0, "max": max_audio_count or -1},
        "video": {"count": 0, "max": max_video_count or -1},
        "youtube_video": {"count": 0, "max": max_youtube_video_count or -1},
    }

    # Process messages
    formatted: list[types.Content] = []

    unsupported_flag = False
    unsupported_file_ids: set[str] = set()

    def _mark_unsupported_file_ids(raw_ids) -> None:
        for sid in normalize_unsupported_file_ids(raw_ids):
            unsupported_file_ids.add(sid)

    # Attach group context
    group_context_enabled = get_user_group_setting_value(
        user_id, "context", "enable_group_context", db
    )
    if use_group_context and group_context_enabled and upload_files_bool:
        group_context_raw = get_group_context_start(db, user_id)
        group_context = group_context_raw.get("context", "")
        group_context_file_ids = group_context_raw.get("group_context_file_ids", [])
        formatted.append(
            types.Content(role="user", parts=[types.Part(text=group_context)])
        )
        if group_context_file_ids:
            context_counters = copy.deepcopy(counters) if counters else None
            upload_result = upload_files(
                db,
                client,
                group_context_file_ids,
                user_id,
                uploaded_cleanup,
                context_counters,
                input_formats_allowed,
                video_metadata=video_metadata,
                file_active_deadline_monotonic=file_active_deadline_monotonic,
            )
            formatted.extend(upload_result.get("parts", []))
            cleanup_items = upload_result.get("uploaded_cleanup") or []
            if uploaded_cleanup is not None:
                uploaded_cleanup.extend(cleanup_items)
            if upload_result.get("unsupported"):
                unsupported_flag = True
                _mark_unsupported_file_ids(upload_result.get("unsupported_file_ids"))
        formatted.append(
            types.Content(role="user", parts=[types.Part(text=get_group_context_end())])
        )

    # Attach project-level files once at the beginning
    if use_project_context and project_id and db and user_id and upload_files_bool:
        # Get the project system instruction
        project_start = get_project_context_start(db, user_id, project_id)
        # Add as user part
        formatted.append(
            types.Content(role="user", parts=[types.Part(text=project_start)])
        )
        project_file_parts: list[types.Part] = []
        response = safe_list_project_files(
            db,
            user_id,
            project_id,
            logger=logger,
            log_prefix="[Google AI Studio]",
            failure_message="Project file attach failed",
        )
        data = response
        # Extract all file IDs
        file_ids: list[str] = []
        for item in data:
            if isinstance(item, dict):
                file_id = item.get("id")
            else:
                file_id = getattr(item, "id", None)
            if file_id is None:
                continue
            file_ids.append(str(file_id))
        if file_ids and upload_files_bool:
            project_counters = copy.deepcopy(counters) if counters else None
            upload_result = upload_files(
                db,
                client,
                file_ids,
                user_id,
                uploaded_cleanup,
                project_counters,
                input_formats_allowed,
                video_metadata=video_metadata,
                file_active_deadline_monotonic=file_active_deadline_monotonic,
            )
            project_file_parts.extend(upload_result.get("parts", []))
            cleanup_items = upload_result.get("uploaded_cleanup") or []
            if uploaded_cleanup is not None:
                uploaded_cleanup.extend(cleanup_items)
            if upload_result.get("unsupported"):
                unsupported_flag = True
                _mark_unsupported_file_ids(upload_result.get("unsupported_file_ids"))
        if project_file_parts:
            formatted.append(types.Content(role="user", parts=project_file_parts))
        # Get the project system instruction end
        project_end = get_project_context_end()
        # Add as user part
        formatted.append(
            types.Content(role="user", parts=[types.Part(text=project_end)])
        )

    # Notes Context
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
                        types.Content(role="user", parts=[types.Part(text=notes_start)])
                    )
                notes_end = get_notes_context_end()
                if notes_end:
                    formatted.append(
                        types.Content(role="user", parts=[types.Part(text=notes_end)])
                    )
        except Exception as exc:
            logger.warning("[Google AI Studio] Notes context attach failed: %s", exc)

    if db and user_id:
        try:
            from app.llm.system_instruction.memories import get_memories_context

            memories_context = get_memories_context(db, user_id, project_id=project_id)
            if memories_context:
                formatted.append(
                    types.Content(role="user", parts=[types.Part(text=memories_context)])
                )
        except Exception as exc:
            logger.warning("[Google AI Studio] Memories context attach failed: %s", exc)

    reference_context_text = _build_reference_context_text()
    history_start_index = len(formatted)

    for msg in chathistory:
        try:
            msg_dict = _msg_to_dict(msg)
            role_value = msg_dict.get("role")
            if not role_value:
                continue
            role = str(role_value).lower()
            allowed_roles = ["user", "assistant", "tool"]
            if not role or role not in allowed_roles:
                continue
            if role == "assistant":
                role = "model"

            # Extract content and file IDs
            content_blocks = _normalize_content_blocks(msg_dict.get("content"))
            content_blocks = _filter_widget_blocks(content_blocks)
            block_file_ids = _collect_block_file_ids(content_blocks)
            block_youtube_entries: list = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("youtube"):
                    block_youtube_entries.extend(_ensure_list(block.get("youtube")))
            has_model_attachments = any(
                _ensure_list(msg_dict.get(field)) or block_file_ids.get(field)
                for field in attachment_fields
            ) or bool(_ensure_list(msg_dict.get("youtube")) or block_youtube_entries)
            if (
                role == "model"
                and not has_model_attachments
                and _append_structured_assistant_history(content_blocks)
            ):
                continue
            content_fragments: list[str] = []
            for block in content_blocks:
                block_type = block.get("type") if isinstance(block, dict) else None
                block_text = (
                    format_tool_call_block_label(block)
                    if block_type == "tool_call"
                    else _coerce_text_from_block(block)
                )
                formatted_text = _format_block_text(block_type, block_text)
                if formatted_text:
                    content_fragments.append(formatted_text)
            content_str = "\n\n".join(
                fragment for fragment in content_fragments if fragment
            ).strip()
            if not content_str:
                content_str = str(msg_dict.get("content", "") or "")

            img_ids = _ensure_list(msg_dict.get("images")) + block_file_ids.get(
                "images", []
            )
            vid_ids = _ensure_list(msg_dict.get("videos")) + block_file_ids.get(
                "videos", []
            )
            aud_ids = _ensure_list(msg_dict.get("audios")) + block_file_ids.get(
                "audios", []
            )
            doc_ids = _ensure_list(msg_dict.get("documents")) + block_file_ids.get(
                "documents", []
            )
            file_ids = img_ids + vid_ids + aud_ids + doc_ids
            youtube_entries = (
                _ensure_list(msg_dict.get("youtube")) or block_youtube_entries
            )

            parts: list[types.Part] = []

            if role == "tool":
                # Get tool name
                name = msg_dict.get("tool_name") or msg_dict.get("name") or "tool"
                result_val = None

                # Handle YouTube videos
                yt_entries = youtube_entries
                if yt_entries:
                    if not isinstance(yt_entries, list):
                        yt_entries = [yt_entries]

                    # Add metadata as JSON
                    meta_text = json.dumps({"youtube": yt_entries}, ensure_ascii=False)
                    result_val = meta_text

                    # Add native video parts if enabled
                    if native_youtube_video:
                        for y in yt_entries:
                            try:
                                url = (
                                    y.get("url")
                                    if isinstance(y, dict)
                                    else y
                                    if isinstance(y, str)
                                    else None
                                )
                                if url:
                                    parts.append(
                                        _build_aistudio_file_part(
                                            file_uri=url,
                                            video_metadata=video_metadata,
                                        )
                                    )
                            except Exception:
                                continue
                # Add text content
                if content_str:
                    parts.append(types.Part(text=content_str))

                # Add file parts
                if file_ids and upload_files_bool:
                    result = upload_files(
                        db,
                        client,
                        file_ids,
                        user_id,
                        uploaded_cleanup
                        if isinstance(uploaded_cleanup, list)
                        else None,
                        counters,
                        input_formats_allowed=input_formats_allowed,
                        video_metadata=video_metadata,
                        file_active_deadline_monotonic=file_active_deadline_monotonic,
                    )
                    parts.extend(result.get("parts", []))
                    if isinstance(uploaded_cleanup, list):
                        uploaded_cleanup.extend(result.get("uploaded_cleanup") or [])
                    counters.update(result.get("counters", counters))
                    if result.get("unsupported"):
                        unsupported_flag = True
                        _mark_unsupported_file_ids(result.get("unsupported_file_ids"))

                # Add function response part
                response_content = ""
                if include_tool_content:
                    response_content = (
                        result_val if result_val is not None else content_str
                    )
                parts.append(
                    types.Part.from_function_response(
                        name=str(name), response={"result": response_content}
                    )
                )

            else:
                # Add text content
                if content_str:
                    parts.append(types.Part(text=content_str))

                # Structured assistant rows can persist YouTube context on a
                # tool result block. Preserve it when this message takes the
                # attachment-aware legacy path.
                if youtube_entries:
                    if native_youtube_video:
                        for entry in youtube_entries:
                            url = (
                                entry.get("url")
                                if isinstance(entry, dict)
                                else entry
                                if isinstance(entry, str)
                                else None
                            )
                            if url:
                                parts.append(
                                    _build_aistudio_file_part(
                                        file_uri=url,
                                        video_metadata=video_metadata,
                                    )
                                )
                    else:
                        parts.append(
                            types.Part(
                                text=json.dumps(
                                    {"youtube": youtube_entries},
                                    ensure_ascii=False,
                                )
                            )
                        )

                # Add file parts
                if file_ids and upload_files_bool:
                    result = upload_files(
                        db,
                        client,
                        file_ids,
                        user_id,
                        uploaded_cleanup
                        if isinstance(uploaded_cleanup, list)
                        else None,
                        counters,
                        input_formats_allowed=input_formats_allowed,
                        video_metadata=video_metadata,
                        file_active_deadline_monotonic=file_active_deadline_monotonic,
                    )
                    parts.extend(result.get("parts", []))
                    if isinstance(uploaded_cleanup, list):
                        uploaded_cleanup.extend(result.get("uploaded_cleanup") or [])
                    counters.update(result.get("counters", counters))
                    if result.get("unsupported"):
                        unsupported_flag = True
                        _mark_unsupported_file_ids(result.get("unsupported_file_ids"))
                elif file_ids and not upload_files_bool:
                    unsupported_flag = True
                    _mark_unsupported_file_ids(file_ids)

            if parts:
                formatted.append(types.Content(role=role, parts=parts))

        except Exception as e:
            logger.warning(f"[AIStudio] Failed to process message: {e}")
            continue

    _append_reference_context_to_latest_user(
        reference_context_text, history_start_index
    )

    return {
        "formatted": formatted,
        "uploaded_cleanup": uploaded_cleanup,
        "unsupported": unsupported_flag,
        "unsupported_file_ids": sorted(unsupported_file_ids),
    }
