"""OpenRouter chat-history normalization and message formatting.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openrouter import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "reformat_chat_history": (
        "Any",
        "SessionLocal",
        "TEXT_EXTRACTED_DOCUMENT_MIME_TYPES",
        "base64",
        "build_file_metadata_text",
        "copy",
        "extract_text_file",
        "extract_text_from_file_info",
        "extract_tool_call_block",
        "format_tool_call_block_label",
        "get_file_info",
        "get_group_context_end",
        "get_group_context_start",
        "get_project_context_end",
        "get_project_context_start",
        "get_user_group_setting_value",
        "json",
        "logger",
        "normalize_file_mime_type",
        "normalize_unsupported_file_ids",
        "openrouter_audio_mime_types",
        "openrouter_document_mime_types",
        "openrouter_image_mime_types",
        "openrouter_video_mime_types",
        "render_pdf_pages_to_png_bytes",
        "safe_list_project_files",
        "should_convert_pdf_to_images",
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
    "SessionLocal",
    "TEXT_EXTRACTED_DOCUMENT_MIME_TYPES",
    "base64",
    "build_file_metadata_text",
    "copy",
    "extract_text_file",
    "extract_text_from_file_info",
    "extract_tool_call_block",
    "format_tool_call_block_label",
    "get_file_info",
    "get_group_context_end",
    "get_group_context_start",
    "get_project_context_end",
    "get_project_context_start",
    "get_user_group_setting_value",
    "json",
    "logger",
    "normalize_file_mime_type",
    "normalize_unsupported_file_ids",
    "openrouter_audio_mime_types",
    "openrouter_document_mime_types",
    "openrouter_image_mime_types",
    "openrouter_video_mime_types",
    "render_pdf_pages_to_png_bytes",
    "safe_list_project_files",
    "should_convert_pdf_to_images",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_reformat_chat_history(
    chat_history,
    user_id,
    db,
    project_id: str | None = None,
    max_image_count: int | None = None,
    max_document_count: int | None = None,
    max_audio_count: int | None = None,
    max_video_count: int | None = None,
    upload_files_bool: bool = True,
    video_enabled: bool = False,
    native_youtube_video: bool = False,
    input_formats_allowed: list[str] | None = None,
    use_group_context: bool = True,
    use_project_context: bool = True,
    note_ids: list[str] | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
):
    """
    Convert chat history to OpenRouter format with file support.

    Supports:
    - Images: Using image_url format with base64 data URLs
    - Documents (PDFs): Using file format with base64 data URLs
    - Audio: Using input_audio format with base64 data
    - Video: Using input_video format with base64 data URLs or direct URLs (YouTube when enabled)
    """

    def _coerce_text_content(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            pieces: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    if item.get("type") == "widget":
                        continue
                    text = (
                        format_tool_call_block_label(item)
                        if item.get("type") == "tool_call"
                        else item.get("content") or item.get("text")
                    )
                    if isinstance(text, str):
                        pieces.append(text)
                elif isinstance(item, str):
                    pieces.append(item)
            return "\n\n".join(pieces)
        if isinstance(value, dict):
            if value.get("type") == "widget":
                return ""
            text = (
                format_tool_call_block_label(value)
                if value.get("type") == "tool_call"
                else value.get("content") or value.get("text")
            )
            if isinstance(text, str):
                return text
        try:
            return str(value)
        except Exception:
            return ""

    def _decode_jsonish(value: Any) -> Any:
        """Decode persisted JSON strings while retaining plain text results."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value

    def _normalize_content_blocks(value: Any) -> list[Any]:
        decoded = _decode_jsonish(value)
        if isinstance(decoded, list):
            return decoded
        if isinstance(decoded, dict):
            return [decoded]
        return []

    def _structured_assistant_messages(blocks: list[Any]) -> list[dict] | None:
        """Reconstruct OpenRouter reasoning and tool messages with exact IDs."""
        block_types = {
            str(block.get("type") or "").strip().lower()
            for block in blocks
            if isinstance(block, dict)
        }
        if not block_types.intersection({"reasoning", "tool_call", "tool_call_result"}):
            return None

        result_messages: list[dict] = []
        assistant_text: list[str] = []
        tool_calls: list[dict] = []
        reasoning_text: list[str] = []
        reasoning_details: list[dict] = []
        response_reasoning_items: list[dict] = []
        last_call_id: str | None = None

        def flush_assistant_message() -> None:
            if not (
                assistant_text
                or tool_calls
                or reasoning_text
                or reasoning_details
                or response_reasoning_items
            ):
                return
            message: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(assistant_text),
            }
            if tool_calls:
                message["tool_calls"] = copy.deepcopy(tool_calls)
            if reasoning_text:
                message["reasoning"] = "".join(reasoning_text)
            if reasoning_details:
                # OpenRouter requires this sequence to remain ordered and
                # otherwise byte-for-byte unchanged across tool continuations.
                message["reasoning_details"] = copy.deepcopy(reasoning_details)
            if response_reasoning_items:
                message["_openrouter_responses_reasoning_items"] = copy.deepcopy(
                    response_reasoning_items
                )
            result_messages.append(message)
            assistant_text.clear()
            tool_calls.clear()
            reasoning_text.clear()
            reasoning_details.clear()
            response_reasoning_items.clear()

        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type in {"widget", "file", "file_gen"}:
                continue
            if block_type == "reasoning":
                reasoning_value = block.get("content")
                if isinstance(reasoning_value, str):
                    reasoning_text.append(reasoning_value)
                meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
                details = meta.get("openrouter_reasoning_details")
                if isinstance(details, list):
                    reasoning_details.extend(
                        copy.deepcopy(item)
                        for item in details
                        if isinstance(item, dict)
                    )
                response_items = meta.get("openrouter_responses_reasoning_items")
                if isinstance(response_items, list):
                    response_reasoning_items.extend(
                        copy.deepcopy(item)
                        for item in response_items
                        if isinstance(item, dict)
                    )
                continue
            if block_type in {"content", "assistant", "text"}:
                text_value = block.get("content") or block.get("text")
                if isinstance(text_value, str):
                    assistant_text.append(text_value)
                continue
            if block_type == "tool_call":
                extracted = extract_tool_call_block(block)
                block_meta = (
                    block.get("meta") if isinstance(block.get("meta"), dict) else {}
                )
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
                        "_openrouter_item_id": block_meta.get("openrouter_item_id")
                        or call_id,
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
                flush_assistant_message()
                result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": str(block.get("content") or ""),
                    }
                )

        flush_assistant_message()
        return result_messages or None

    if not chat_history:
        return {"formatted": [], "unsupported": False, "unsupported_file_ids": []}

    # Initialize counters with limits
    counters = {
        "image": {"count": 0, "max": max_image_count or -1},
        "document": {"count": 0, "max": max_document_count or -1},
        "audio": {"count": 0, "max": max_audio_count or -1},
        "video": {"count": 0, "max": max_video_count or -1},
    }

    # Mapping for file types and their OpenRouter formats
    FILE_TYPE_CONFIG = {
        "image": {
            "mime_types": openrouter_image_mime_types,
            "format": lambda file_type, data, **_: {
                "type": "image_url",
                "image_url": {"url": f"data:{file_type};base64,{data}"},
            },
        },
        "document": {
            "mime_types": openrouter_document_mime_types,
            "format": lambda file_type, data, file_name, file_id: {
                "type": "file",
                "file": {
                    "filename": file_name or f"document_{file_id}.pdf",
                    "file_data": f"data:{file_type};base64,{data}",
                },
            },
        },
        "audio": {
            "mime_types": openrouter_audio_mime_types,
            "format": lambda file_type, data, **_: {
                "type": "input_audio",
                "input_audio": {"data": data, "format": file_type.split("/")[1]},
            },
        },
        "video": {
            "mime_types": openrouter_video_mime_types,
            "format": lambda file_type, data, **_: {
                "type": "input_video",
                "video_url": {"url": f"data:{file_type};base64,{data}"},
            },
        },
    }

    formatted: list[dict] = []

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
        reference_part = {"type": "text", "text": reference_text}
        for entry in reversed(formatted[history_start_index:]):
            if not isinstance(entry, dict) or entry.get("role") != "user":
                continue
            content = entry.get("content")
            if isinstance(content, list):
                content.insert(0, reference_part)
            elif isinstance(content, str) and content:
                entry["content"] = [
                    reference_part,
                    {"type": "text", "text": content},
                ]
            else:
                entry["content"] = [reference_part]
            return
        formatted.append({"role": "user", "content": [reference_part]})

    def _extract_text_snippet(file_id: str, file_info: dict | None) -> str | None:
        text_content = extract_text_from_file_info(file_info)
        if isinstance(text_content, str) and text_content.strip():
            content_to_use = text_content
            truncated = False
            if len(content_to_use) > 200000:
                content_to_use = content_to_use[:200000]
                truncated = True
            file_name = (
                file_info.get("file_name") if isinstance(file_info, dict) else None
            )
            prefix = f"{file_name}\n\n" if file_name else ""
            suffix = "\n\n...[truncated]" if truncated else ""
            return f"{prefix}{content_to_use}{suffix}"

        if not user_id:
            return None
        try:
            with SessionLocal() as session:
                extracted = extract_text_file(session, str(file_id))
        except Exception:
            return None
        if not extracted:
            return None
        text_content = extracted.get("content")
        if not isinstance(text_content, str) or not text_content.strip():
            return None
        content_to_use = text_content
        truncated = False
        if len(content_to_use) > 200000:
            content_to_use = content_to_use[:200000]
            truncated = True
        file_name = None
        if file_info and isinstance(file_info, dict):
            file_name = file_info.get("file_name")
        prefix = f"{file_name}\n\n" if file_name else ""
        suffix = "\n\n...[truncated]" if truncated else ""
        return f"{prefix}{content_to_use}{suffix}"

    def _counter_has_capacity(counter_key: str, state: dict | None = None) -> bool:
        active_state = state if state is not None else counters
        if not active_state:
            return True
        counter = active_state.get(counter_key)
        if not counter:
            return True
        max_allowed = counter.get("max", -1)
        if max_allowed < 0:
            return True
        return counter["count"] < max_allowed

    def _remaining_capacity(counter_key: str, state: dict | None = None) -> int | None:
        active_state = state if state is not None else counters
        if not active_state:
            return None
        counter = active_state.get(counter_key)
        if not counter:
            return None
        max_allowed = counter.get("max", -1)
        if max_allowed < 0:
            return None
        return max(max_allowed - counter["count"], 0)

    def _increment_counter(
        counter_key: str, amount: int = 1, state: dict | None = None
    ):
        active_state = state if state is not None else counters
        if not active_state:
            return
        counter = active_state.get(counter_key)
        if counter:
            counter["count"] += amount

    def _documents_allowed_natively() -> bool:
        return (
            not input_formats_allowed
            or "pdf" in input_formats_allowed
            or "documents" in input_formats_allowed
        )

    def _can_use_pdf_image_fallback() -> bool:
        return should_convert_pdf_to_images(input_formats_allowed)

    def _build_metadata_only_part(file_id: str, file_info: dict | None) -> dict | None:
        if not isinstance(file_info, dict):
            return None
        return {
            "type": "text",
            "text": build_file_metadata_text(
                file_id,
                file_info,
                native_context_included=False,
                model_context_representation="metadata_only",
                text_content_included=False,
                provider_supported_image_mime_types=openrouter_image_mime_types,
            ),
        }

    def _resolve_file_parts(
        file_ids: list[str], category: str, counter_state: dict | None = None
    ) -> dict[str, Any]:
        """Generic file resolver for all file types."""
        if not file_ids or not user_id:
            return {"parts": [], "unsupported_file_ids": []}

        state = counter_state if counter_state is not None else counters
        if not state or category not in state:
            return {"parts": [], "unsupported_file_ids": []}
        config = FILE_TYPE_CONFIG[category]
        parts = []
        unsupported_ids: set[str] = set()

        for file_id in file_ids:
            try:
                file_info = get_file_info(user_id, str(file_id))

                # Guard against missing/inaccessible files
                if not file_info:
                    logger.warning(f"[OpenRouter] File not found: {category} {file_id}")
                    unsupported_ids.add(str(file_id))
                    continue

                file_type = normalize_file_mime_type(file_info.get("file_type"))

                # Source-text documents are advertised to every text-capable
                # model. Resolve them before provider-native document checks so
                # HTML is inert model context and SVG avoids unsupported vision
                # paths.
                if (
                    category == "document"
                    and file_type in TEXT_EXTRACTED_DOCUMENT_MIME_TYPES
                ):
                    if not _counter_has_capacity("document", state):
                        if metadata_part := _build_metadata_only_part(
                            str(file_id), file_info
                        ):
                            parts.append(metadata_part)
                        unsupported_ids.add(str(file_id))
                        continue
                    snippet = _extract_text_snippet(str(file_id), file_info)
                    if snippet:
                        parts.append(
                            {
                                "type": "text",
                                "text": build_file_metadata_text(
                                    file_id,
                                    file_info,
                                    native_context_included=False,
                                    model_context_representation="text_extract",
                                    text_content_included=True,
                                    provider_supported_image_mime_types=openrouter_image_mime_types,
                                ),
                            }
                        )
                        parts.append({"type": "text", "text": snippet})
                        _increment_counter("document", 1, state)
                    else:
                        if metadata_part := _build_metadata_only_part(
                            str(file_id), file_info
                        ):
                            parts.append(metadata_part)
                        unsupported_ids.add(str(file_id))
                    continue

                # Keep the existing text-only policy for all other document
                # types. PDF may still proceed through the configured image
                # fallback, while arbitrary documents remain unsupported.
                if (
                    category == "document"
                    and not _documents_allowed_natively()
                    and not _can_use_pdf_image_fallback()
                ):
                    unsupported_ids.add(str(file_id))
                    continue

                # Validate file category and type
                if file_info.get("file_category") != category:
                    if category == "document":
                        if not _counter_has_capacity("document", state):
                            if metadata_part := _build_metadata_only_part(
                                str(file_id), file_info
                            ):
                                parts.append(metadata_part)
                            unsupported_ids.add(str(file_id))
                            continue
                        snippet = _extract_text_snippet(file_id, file_info)
                        if snippet:
                            parts.append(
                                {
                                    "type": "text",
                                    "text": build_file_metadata_text(
                                        file_id,
                                        file_info,
                                        native_context_included=False,
                                        model_context_representation="text_extract",
                                        text_content_included=True,
                                        provider_supported_image_mime_types=openrouter_image_mime_types,
                                    ),
                                }
                            )
                            parts.append({"type": "text", "text": snippet})
                            _increment_counter("document", 1, state)
                        else:
                            if metadata_part := _build_metadata_only_part(
                                str(file_id), file_info
                            ):
                                parts.append(metadata_part)
                            unsupported_ids.add(str(file_id))
                    else:
                        if metadata_part := _build_metadata_only_part(
                            str(file_id), file_info
                        ):
                            parts.append(metadata_part)
                        unsupported_ids.add(str(file_id))
                    continue
                if (
                    category == "document"
                    and file_type in config["mime_types"]
                    and not _documents_allowed_natively()
                    and _can_use_pdf_image_fallback()
                ):
                    if not _counter_has_capacity("image", state):
                        if metadata_part := _build_metadata_only_part(
                            str(file_id), file_info
                        ):
                            parts.append(metadata_part)
                        unsupported_ids.add(str(file_id))
                        continue
                    page_images = render_pdf_pages_to_png_bytes(
                        file_info["path"],
                        max_pages=_remaining_capacity("image", state),
                    )
                    if not page_images:
                        if metadata_part := _build_metadata_only_part(
                            str(file_id), file_info
                        ):
                            parts.append(metadata_part)
                        unsupported_ids.add(str(file_id))
                        continue
                    parts.append(
                        {
                            "type": "text",
                            "text": build_file_metadata_text(
                                file_id,
                                file_info,
                                native_context_included=False,
                                model_context_representation="rendered_images",
                                text_content_included=False,
                                provider_supported_image_mime_types=openrouter_image_mime_types,
                            ),
                        }
                    )
                    for image_bytes in page_images:
                        encoded_image = base64.b64encode(image_bytes).decode()
                        parts.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{encoded_image}"
                                },
                            }
                        )
                    _increment_counter("image", len(page_images), state)
                    continue
                if file_type not in config["mime_types"]:
                    if category == "document":
                        if not _counter_has_capacity("document", state):
                            if metadata_part := _build_metadata_only_part(
                                str(file_id), file_info
                            ):
                                parts.append(metadata_part)
                            unsupported_ids.add(str(file_id))
                            continue
                        snippet = _extract_text_snippet(file_id, file_info)
                        if snippet:
                            parts.append(
                                {
                                    "type": "text",
                                    "text": build_file_metadata_text(
                                        file_id,
                                        file_info,
                                        native_context_included=False,
                                        model_context_representation="text_extract",
                                        text_content_included=True,
                                        provider_supported_image_mime_types=openrouter_image_mime_types,
                                    ),
                                }
                            )
                            parts.append({"type": "text", "text": snippet})
                            _increment_counter("document", 1, state)
                        else:
                            if metadata_part := _build_metadata_only_part(
                                str(file_id), file_info
                            ):
                                parts.append(metadata_part)
                            unsupported_ids.add(str(file_id))
                    else:
                        if metadata_part := _build_metadata_only_part(
                            str(file_id), file_info
                        ):
                            parts.append(metadata_part)
                        unsupported_ids.add(str(file_id))
                    continue

                if not _counter_has_capacity(category, state):
                    if metadata_part := _build_metadata_only_part(
                        str(file_id), file_info
                    ):
                        parts.append(metadata_part)
                    unsupported_ids.add(str(file_id))
                    continue

                # Read and encode file
                with open(file_info["path"], "rb") as f:
                    encoded_data = base64.b64encode(f.read()).decode()

                # Format according to file type
                parts.append(
                    {
                        "type": "text",
                        "text": build_file_metadata_text(
                            file_id,
                            file_info,
                            native_context_included=True,
                            model_context_representation="native_file",
                            text_content_included=False,
                            provider_supported_image_mime_types=openrouter_image_mime_types,
                        ),
                    }
                )
                parts.append(
                    config["format"](
                        file_type=file_type,
                        data=encoded_data,
                        file_name=file_info.get("file_name"),
                        file_id=file_id,
                    )
                )
                _increment_counter(category, 1, state)

            except Exception as e:
                logger.warning(
                    f"[OpenRouter] Failed to process {category} {file_id}: {e}"
                )
                unsupported_ids.add(str(file_id))
                continue

        return {
            "parts": parts,
            "unsupported_file_ids": sorted(unsupported_ids),
        }

    unsupported_flag = False
    unsupported_file_ids: set[str] = set()

    def _mark_unsupported_file_ids(raw_ids) -> None:
        for sid in normalize_unsupported_file_ids(raw_ids):
            unsupported_file_ids.add(sid)

    group_context_enabled = False
    if user_id and db:
        try:
            group_context_enabled = get_user_group_setting_value(
                user_id,
                "context",
                "enable_group_context",
                db,
            )
        except Exception as exc:
            logger.warning(
                "[OpenRouter] Failed to read group context setting for user %s: %s",
                user_id,
                exc,
            )

    if use_group_context and group_context_enabled:
        try:
            group_context_raw = get_group_context_start(db, user_id) or {}
        except Exception as exc:
            logger.warning(
                "[OpenRouter] Failed to load group context for user %s: %s",
                user_id,
                exc,
            )
            group_context_raw = {}

        group_context_text = group_context_raw.get("context") or ""
        raw_group_file_ids = group_context_raw.get("group_context_file_ids") or []

        if group_context_text:
            formatted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": group_context_text,
                        }
                    ],
                }
            )

        if raw_group_file_ids:
            if not upload_files_bool:
                unsupported_flag = True
                _mark_unsupported_file_ids(raw_group_file_ids)
            else:
                context_counters = copy.deepcopy(counters) if counters else None
                files_by_category: dict[str, list[str]] = {
                    "image": [],
                    "document": [],
                    "audio": [],
                    "video": [],
                }
                for file_id in raw_group_file_ids:
                    sid = str(file_id).strip()
                    if not sid:
                        continue
                    try:
                        file_info = get_file_info(user_id, sid)
                    except Exception as exc:
                        logger.warning(
                            "[OpenRouter] Failed to load group context file %s for user %s: %s",
                            file_id,
                            user_id,
                            exc,
                        )
                        continue
                    if not file_info:
                        unsupported_flag = True
                        _mark_unsupported_file_ids([sid])
                        continue
                    category = file_info.get("file_category")
                    if category not in files_by_category:
                        if category == "document":
                            files_by_category["document"].append(sid)
                        else:
                            unsupported_flag = True
                            _mark_unsupported_file_ids([sid])
                        continue
                    files_by_category[category].append(sid)

                group_content_parts: list[dict] = []
                for key, category in [
                    ("image", "image"),
                    ("document", "document"),
                    ("audio", "audio"),
                    ("video", "video"),
                ]:
                    if not files_by_category[key]:
                        continue
                    if category == "image" and (
                        input_formats_allowed and "image" not in input_formats_allowed
                    ):
                        unsupported_flag = True
                        _mark_unsupported_file_ids(files_by_category[key])
                        continue
                    if category == "video" and (
                        not video_enabled
                        or (
                            input_formats_allowed
                            and "video" not in input_formats_allowed
                        )
                    ):
                        unsupported_flag = True
                        _mark_unsupported_file_ids(files_by_category[key])
                        continue
                    if (
                        category == "audio"
                        and input_formats_allowed
                        and "audio" not in input_formats_allowed
                    ):
                        unsupported_flag = True
                        _mark_unsupported_file_ids(files_by_category[key])
                        continue
                    resolve_result = _resolve_file_parts(
                        files_by_category[key], category, context_counters
                    )
                    parts = resolve_result.get("parts", [])
                    unresolved_ids = resolve_result.get("unsupported_file_ids", [])
                    if unresolved_ids:
                        unsupported_flag = True
                        _mark_unsupported_file_ids(unresolved_ids)
                    group_content_parts.extend(parts)

                if group_content_parts:
                    formatted.append({"role": "user", "content": group_content_parts})

        try:
            group_context_end = get_group_context_end()
        except Exception as exc:
            logger.warning(
                "[OpenRouter] Failed to load group context end for user %s: %s",
                user_id,
                exc,
            )
            group_context_end = ""
        if group_context_text or raw_group_file_ids:
            if group_context_end:
                formatted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": group_context_end,
                            }
                        ],
                    }
                )

    project_file_parts: list[dict] = []
    project_start_text: str | None = None
    project_end_text: str | None = None
    if use_project_context and project_id and db and user_id:
        try:
            project_start_text = get_project_context_start(db, user_id, project_id)
        except Exception as exc:
            logger.warning(
                "[OpenRouter] Failed to load project instruction for project %s: %s",
                project_id,
                exc,
            )
        project_files = safe_list_project_files(
            db,
            user_id,
            project_id,
            logger=logger,
            log_prefix="[OpenRouter]",
            failure_message="Failed to attach project files",
            include_project_id=True,
        )
        seen: set[str] = set()
        files_by_category: dict[str, list[str]] = {
            "image": [],
            "document": [],
            "audio": [],
            "video": [],
        }

        for pfile in project_files:
            file_id = getattr(pfile, "id", None)
            if not file_id:
                continue
            sid = str(file_id)
            if sid in seen:
                continue

            category = getattr(pfile, "file_category", None)
            file_type = normalize_file_mime_type(getattr(pfile, "file_type", None))
            # Older rows can still classify source-text formats incorrectly.
            # Normalize them before applying provider-native MIME checks.
            if file_type in TEXT_EXTRACTED_DOCUMENT_MIME_TYPES:
                category = "document"
            if category not in FILE_TYPE_CONFIG:
                continue
            if (
                file_type not in TEXT_EXTRACTED_DOCUMENT_MIME_TYPES
                and file_type not in FILE_TYPE_CONFIG[category]["mime_types"]
            ):
                continue

            files_by_category[category].append(sid)
            seen.add(sid)

        project_counters = copy.deepcopy(counters) if counters else None
        for ids_key, category in [
            ("image", "image"),
            ("document", "document"),
            ("audio", "audio"),
            ("video", "video"),
        ]:
            if not files_by_category[category]:
                continue
            if category == "image" and (
                input_formats_allowed and "image" not in input_formats_allowed
            ):
                unsupported_flag = True
                _mark_unsupported_file_ids(files_by_category[category])
                continue
            if category == "video" and (
                not video_enabled
                or (input_formats_allowed and "video" not in input_formats_allowed)
            ):
                unsupported_flag = True
                _mark_unsupported_file_ids(files_by_category[category])
                continue
            if (
                category == "audio"
                and input_formats_allowed
                and "audio" not in input_formats_allowed
            ):
                unsupported_flag = True
                _mark_unsupported_file_ids(files_by_category[category])
                continue
            resolve_result = _resolve_file_parts(
                files_by_category[category], category, project_counters
            )
            parts = resolve_result.get("parts", [])
            unresolved_ids = resolve_result.get("unsupported_file_ids", [])
            if unresolved_ids:
                unsupported_flag = True
                _mark_unsupported_file_ids(unresolved_ids)
            project_file_parts.extend(parts)
        try:
            project_end_text = get_project_context_end()
        except Exception as exc:
            logger.warning(
                "[OpenRouter] Failed to load project instruction end for project %s: %s",
                project_id,
                exc,
            )

    # Process messages
    if project_start_text:
        formatted.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": project_start_text,
                    }
                ],
            }
        )
    if project_file_parts:
        formatted.append({"role": "user", "content": project_file_parts})
    if project_end_text:
        formatted.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": project_end_text,
                    }
                ],
            }
        )

    # Notes Context
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
                                    "type": "text",
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
                                    "type": "text",
                                    "text": notes_end,
                                }
                            ],
                        }
                    )
        except Exception as exc:
            logger.warning("[OpenRouter] Notes context attach failed: %s", exc)

    memories_start_index = len(formatted)
    if db and user_id:
        try:
            from app.llm.system_instruction.memories import get_memories_context

            memories_context = get_memories_context(db, user_id, project_id=project_id)
            if memories_context:
                formatted.append(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": memories_context}],
                    }
                )
        except Exception as exc:
            logger.warning("[OpenRouter] Memories context attach failed: %s", exc)

    reference_context_text = _build_reference_context_text()
    history_start_index = len(formatted)

    assistant_parts = []
    assistant_files = {"images": [], "documents": [], "audios": [], "videos": []}

    def flush_assistant():
        nonlocal unsupported_flag
        if assistant_parts or any(assistant_files.values()):
            content_parts = []

            # Add file parts
            for ids_key, category in [
                ("images", "image"),
                ("documents", "document"),
                ("audios", "audio"),
                ("videos", "video"),
            ]:
                if not assistant_files[ids_key]:
                    continue
                if category == "image" and (
                    input_formats_allowed and "image" not in input_formats_allowed
                ):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(assistant_files[ids_key])
                    continue
                if category == "video" and (
                    not video_enabled
                    or (input_formats_allowed and "video" not in input_formats_allowed)
                ):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(assistant_files[ids_key])
                    continue
                if (
                    category == "audio"
                    and input_formats_allowed
                    and "audio" not in input_formats_allowed
                ):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(assistant_files[ids_key])
                    continue
                resolve_result = _resolve_file_parts(assistant_files[ids_key], category)
                parts = resolve_result.get("parts", [])
                unresolved_ids = resolve_result.get("unsupported_file_ids", [])
                if unresolved_ids:
                    unsupported_flag = True
                    _mark_unsupported_file_ids(unresolved_ids)
                content_parts.extend(parts)

            # Add text content
            text = "".join(p for p in assistant_parts if isinstance(p, str) and p)
            if text:
                content_parts.append({"type": "text", "text": text})

            if content_parts:
                # Use structured format if multiple parts or if single part is not text
                if len(content_parts) > 1 or content_parts[0].get("type") != "text":
                    formatted.append({"role": "assistant", "content": content_parts})
                else:
                    formatted.append(
                        {"role": "assistant", "content": content_parts[0]["text"]}
                    )

            assistant_parts.clear()
            assistant_files.update(
                {"images": [], "documents": [], "audios": [], "videos": []}
            )

    for msg in chat_history:
        is_dict = isinstance(msg, dict)
        role_raw = msg.get("role") if is_dict else getattr(msg, "role", None)
        role = str(role_raw or "").lower().strip()
        if not role:
            continue

        raw_content = msg.get("content") if is_dict else getattr(msg, "content", None)
        content = _coerce_text_content(raw_content)
        media_lists: dict[str, list[str]] = {
            "images": [],
            "documents": [],
            "audios": [],
            "videos": [],
        }
        for media_key in media_lists:
            raw_value = msg.get(media_key) if is_dict else getattr(msg, media_key, None)
            if raw_value is None:
                continue
            if isinstance(raw_value, (list, tuple, set)):
                media_lists[media_key] = [
                    str(item) for item in raw_value if item is not None
                ]
                continue
            if isinstance(raw_value, str):
                candidate = raw_value.strip()
                if not candidate:
                    continue
                try:
                    decoded = json.loads(candidate)
                    if isinstance(decoded, list):
                        media_lists[media_key] = [
                            str(item) for item in decoded if item is not None
                        ]
                        continue
                except json.JSONDecodeError:
                    pass
                media_lists[media_key] = [candidate]

        structured_messages = None
        if role == "assistant" and not any(media_lists.values()):
            structured_messages = _structured_assistant_messages(
                _normalize_content_blocks(raw_content)
            )

        if role == "assistant":
            if structured_messages is not None:
                flush_assistant()
                formatted.extend(structured_messages)
                continue
            if content:
                assistant_parts.append(content)
            # Collect assistant files
            for key in ["images", "documents", "audios", "videos"]:
                if media_lists[key]:
                    assistant_files[key].extend(media_lists[key])
            continue

        if role == "tool":
            flush_assistant()
            tool_entry = {"role": "tool", "content": content or ""}
            tool_name = (
                msg.get("tool_name") if is_dict else getattr(msg, "tool_name", None)
            )
            if tool_name:
                if isinstance(tool_name, str):
                    tool_entry["tool_name"] = tool_name

            youtube_entries = (
                msg.get("youtube") if is_dict else getattr(msg, "youtube", None)
            )
            if youtube_entries:
                if not isinstance(youtube_entries, list):
                    youtube_entries = [youtube_entries]
                for entry in youtube_entries:
                    url = entry.get("url") if isinstance(entry, dict) else entry
                    if not url:
                        continue
                    if not native_youtube_video or not video_enabled:
                        unsupported_flag = True
                        continue
                    video_counter = counters.get("video")
                    if video_counter:
                        video_counter["count"] += 1
                        if (
                            video_counter["max"] != -1
                            and video_counter["count"] > video_counter["max"]
                        ):
                            video_counter["count"] -= 1
                            unsupported_flag = True
                            continue
                    tool_entry.setdefault("attachments", []).append(
                        {
                            "type": "input_video",
                            "video_url": {"url": url},
                        }
                    )
            formatted.append(tool_entry)
            continue

        if role == "user":
            flush_assistant()
            content_parts = []

            # Process all file types
            for ids_key, category in [
                ("images", "image"),
                ("documents", "document"),
                ("audios", "audio"),
                ("videos", "video"),
            ]:
                if not media_lists[ids_key]:
                    continue
                if category == "image" and (
                    input_formats_allowed and "image" not in input_formats_allowed
                ):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(media_lists[ids_key])
                    continue
                if category == "video" and (
                    not video_enabled
                    or (input_formats_allowed and "video" not in input_formats_allowed)
                ):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(media_lists[ids_key])
                    continue
                if (
                    category == "audio"
                    and input_formats_allowed
                    and "audio" not in input_formats_allowed
                ):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(media_lists[ids_key])
                    continue
                resolve_result = _resolve_file_parts(media_lists[ids_key], category)
                parts = resolve_result.get("parts", [])
                unresolved_ids = resolve_result.get("unsupported_file_ids", [])
                if unresolved_ids:
                    unsupported_flag = True
                    _mark_unsupported_file_ids(unresolved_ids)
                content_parts.extend(parts)

            youtube_entries = (
                msg.get("youtube") if is_dict else getattr(msg, "youtube", None)
            )
            if youtube_entries:
                if not isinstance(youtube_entries, list):
                    youtube_entries = [youtube_entries]
                for entry in youtube_entries:
                    url = entry.get("url") if isinstance(entry, dict) else entry
                    if not url:
                        continue
                    if not native_youtube_video or not video_enabled:
                        unsupported_flag = True
                        continue
                    video_counter = counters.get("video")
                    if video_counter:
                        video_counter["count"] += 1
                        if (
                            video_counter["max"] != -1
                            and video_counter["count"] > video_counter["max"]
                        ):
                            video_counter["count"] -= 1
                            unsupported_flag = True
                            continue
                    content_parts.append(
                        {
                            "type": "input_video",
                            "video_url": {"url": url},
                        }
                    )

            # Add text content last
            if content:
                content_parts.append({"type": "text", "text": content})

            if content_parts:
                formatted.append({"role": "user", "content": content_parts})

    flush_assistant()
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
