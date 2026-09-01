"""Conversion of stored Omlorix chat history into Anthropic message blocks."""

import copy
import json
import logging
import uuid
from typing import Any

from app.groups.init import get_user_group_setting_value
from app.llm.anthropic.attachments import upload_files
from app.llm.helper import (
    extract_tool_call_block,
    format_tool_call_block_label,
    normalize_unsupported_file_ids,
    safe_list_project_files,
)
from app.llm.system_instruction.group import (
    get_group_context_end,
    get_group_context_start,
)
from app.llm.system_instruction.projects import (
    get_project_context_end,
    get_project_context_start,
)

logger = logging.getLogger(__name__)


def reformat_chat_history(
    chat_history,
    user_id: str | None = None,
    db=None,
    uploaded_cleanup: list[str] | None = None,
    include_tool_content: bool = True,
    project_id: str | None = None,
    max_image_count: int | None = None,
    max_document_count: int | None = None,
    upload_files_bool: bool = True,
    input_formats_allowed: list[str] | None = None,
    use_group_context: bool = True,
    use_project_context: bool = True,
    note_ids: list[str] | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
):
    """Reformat chat history."""

    def _decode_jsonish(value):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return json.loads(stripped)
            except Exception:
                return value
        return value

    def _normalize_blocks(value):
        decoded = _decode_jsonish(value)
        if decoded is None:
            return []
        if isinstance(decoded, list):
            return decoded
        if isinstance(decoded, dict):
            return [decoded]
        if isinstance(decoded, str):
            return [{"type": "text", "text": decoded}]
        return []

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
            "system_instruction",
            "meta",
            "images",
            "videos",
            "audios",
            "documents",
            "youtube",
        )
        return {k: getattr(msg, k, None) for k in keys}

    def _extract_text(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            text_parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") in {"widget", "compaction"}:
                        continue
                    txt = (
                        format_tool_call_block_label(item)
                        if item.get("type") == "tool_call"
                        else item.get("text") or item.get("content")
                    )
                    if isinstance(txt, str):
                        text_parts.append(txt)
            return "\n\n".join(text_parts) if text_parts else None
        if isinstance(value, dict):
            if value.get("type") in {"widget", "compaction"}:
                return None
            txt = (
                format_tool_call_block_label(value)
                if value.get("type") == "tool_call"
                else value.get("text") or value.get("content")
            )
            return txt if isinstance(txt, str) else None
        try:
            return str(value)
        except Exception:
            return None

    def _block_meta(block: Any) -> dict[str, Any]:
        """Return one persisted block's metadata without mutating history."""
        if not isinstance(block, dict) or not isinstance(block.get("meta"), dict):
            return {}
        return block["meta"]

    def _anthropic_thinking_signature(block: Any) -> str | None:
        """Read every metadata layout emitted by older and current streams."""
        meta = _block_meta(block)
        direct = meta.get("thinking_signature")
        if isinstance(direct, dict):
            direct = direct.get("anthropic")
        nested = meta.get("anthropic")
        if not direct and isinstance(nested, dict):
            direct = nested.get("thinking_signature")
        normalized = str(direct or "").strip()
        return normalized or None

    def _tool_result_call_id(block: Any, fallback: str | None) -> str | None:
        """Resolve the exact provider tool-use ID for a persisted result."""
        meta = _block_meta(block)
        value = meta.get("tool_call_id") or meta.get("tool_use_id") or fallback
        normalized = str(value or "").strip()
        return normalized or None

    def _decode_tool_arguments(value: Any) -> dict[str, Any]:
        """Anthropic tool_use input must be an object, even for legacy rows."""
        decoded = _decode_jsonish(value)
        return decoded if isinstance(decoded, dict) else {}

    def _append_structured_assistant_history(
        message: dict[str, Any],
        message_blocks: list[Any],
    ) -> bool:
        """Replay Anthropic thinking and tool blocks in their native API shape.

        Claude requires signed thinking blocks to be returned unchanged during
        tool continuations.  Persisted Omlorix messages contain the whole tool
        loop in one row, so this routine expands it back into alternating
        assistant ``tool_use`` and user ``tool_result`` messages.
        """
        structured_types = {
            str(block.get("type") or "").strip().lower()
            for block in message_blocks
            if isinstance(block, dict)
        }
        if not structured_types.intersection(
            {"reasoning", "tool_call", "tool_call_result"}
        ):
            return False

        nonlocal counters, unsupported_flag

        assistant_parts: list[dict[str, Any]] = []
        last_tool_call_id: str | None = None
        formatted_start = len(formatted)

        def flush_assistant() -> None:
            if assistant_parts:
                formatted.append(
                    {"role": "assistant", "content": list(assistant_parts)}
                )
                assistant_parts.clear()

        for block_index, block in enumerate(message_blocks):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type in {"widget", "file"}:
                continue

            if block_type == "reasoning":
                redacted_data = _block_meta(block).get("anthropic_redacted_thinking")
                if isinstance(redacted_data, str) and redacted_data:
                    # Anthropic requires redacted thinking blocks to be replayed
                    # byte-for-byte just like ordinary signed thinking blocks.
                    assistant_parts.append(
                        {"type": "redacted_thinking", "data": redacted_data}
                    )
                    continue
                signature = _anthropic_thinking_signature(block)
                # Older rows sometimes attached the signature to the following
                # visible-content block. Associate it with this reasoning block
                # without changing the opaque value.
                if not signature:
                    for candidate in message_blocks[block_index + 1 :]:
                        if not isinstance(candidate, dict):
                            continue
                        if str(candidate.get("type") or "").lower() == "reasoning":
                            break
                        signature = _anthropic_thinking_signature(candidate)
                        if signature:
                            break
                reasoning_text = str(block.get("content") or "")
                if signature:
                    assistant_parts.append(
                        {
                            "type": "thinking",
                            "thinking": reasoning_text,
                            "signature": signature,
                        }
                    )
                elif reasoning_text:
                    # Unsigned legacy reasoning is not a valid Anthropic
                    # thinking block. Preserve its prior transcript behavior as
                    # ordinary text instead of manufacturing a signature.
                    assistant_parts.append(
                        {"type": "text", "text": f"Reasoning: {reasoning_text}"}
                    )
                continue

            if block_type in {"content", "assistant", "text"}:
                text = block.get("content") or block.get("text")
                if isinstance(text, str) and text:
                    assistant_parts.append({"type": "text", "text": text})
                continue

            if block_type == "compaction":
                compacted = block.get("content")
                if isinstance(compacted, str) and compacted:
                    assistant_parts.append({"type": "compaction", "content": compacted})
                continue

            if block_type == "tool_call":
                extracted = extract_tool_call_block(block)
                tool_name = extracted.get("tool_name") or "tool"
                call_id = str(extracted.get("tool_call_id") or "").strip()
                if not call_id:
                    last_tool_call_id = None
                    # Legacy messages cannot be paired safely without an ID;
                    # retain them as display text rather than sending a broken
                    # tool_use block to Anthropic.
                    label = format_tool_call_block_label(block)
                    if label:
                        assistant_parts.append(
                            {"type": "text", "text": f"Tool call: {label}"}
                        )
                    continue
                last_tool_call_id = call_id
                meta = _block_meta(block)
                native_web_search = bool(meta.get("native_web_search"))
                if not native_web_search:
                    # Rows written before native server-tool metadata was added
                    # still identify the paired result correctly. Infer the
                    # call type from that exact ID so existing histories replay
                    # a valid server_tool_use/web_search_tool_result pair.
                    for candidate in message_blocks[block_index + 1 :]:
                        if not isinstance(candidate, dict):
                            continue
                        candidate_type = str(candidate.get("type") or "").lower()
                        if candidate_type == "tool_call":
                            break
                        if candidate_type != "tool_call_result":
                            continue
                        candidate_meta = _block_meta(candidate)
                        candidate_id = _tool_result_call_id(candidate, None)
                        if candidate_id == call_id:
                            native_web_search = bool(
                                candidate_meta.get("native_web_search")
                            )
                            break
                assistant_parts.append(
                    {
                        "type": "server_tool_use" if native_web_search else "tool_use",
                        "id": call_id,
                        "name": tool_name,
                        "input": _decode_tool_arguments(extracted.get("arguments")),
                    }
                )
                continue

            if block_type == "tool_call_result":
                meta = _block_meta(block)
                call_id = _tool_result_call_id(block, last_tool_call_id)
                if not call_id:
                    result_text = _extract_text(block)
                    if result_text:
                        assistant_parts.append(
                            {"type": "text", "text": f"Tool result: {result_text}"}
                        )
                    continue

                if meta.get("native_web_search"):
                    payload = _decode_jsonish(block.get("content"))
                    results = (
                        payload.get("results") if isinstance(payload, dict) else None
                    )
                    normalized_results = (
                        [
                            {**page, "type": "web_search_result"}
                            for page in results
                            if isinstance(page, dict)
                        ]
                        if isinstance(results, list)
                        else []
                    )
                    assistant_parts.append(
                        {
                            "type": "web_search_tool_result",
                            "tool_use_id": call_id,
                            "content": normalized_results,
                        }
                    )
                    continue

                flush_assistant()
                result_text = block.get("content")
                tool_result: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": str(result_text or ""),
                }
                formatted.append({"role": "user", "content": [tool_result]})
                continue

            fallback_text = _extract_text(block)
            if fallback_text:
                assistant_parts.append({"type": "text", "text": fallback_text})

        flush_assistant()

        # Assistant rows may carry ordinary attachments at the message level,
        # while tool-produced files are persisted on tool result blocks. Keep
        # both when native tool history reconstruction bypasses the legacy path.
        file_ids: list[str] = []
        seen_file_ids: set[str] = set()
        unsupported_attachment_ids: list[str] = []
        has_unsupported_youtube = False

        def collect_file_ids(raw_ids: Any) -> None:
            for file_id in _normalize_file_ids(raw_ids):
                if file_id in seen_file_ids:
                    continue
                seen_file_ids.add(file_id)
                file_ids.append(file_id)

        attachment_sources = [message]
        attachment_sources.extend(
            block
            for block in message_blocks
            if isinstance(block, dict) and block.get("type") == "tool_call_result"
        )
        for source in attachment_sources:
            for attachment_key in ("images", "documents"):
                collect_file_ids(source.get(attachment_key))
            for attachment_key in ("videos", "audios"):
                unsupported_attachment_ids.extend(
                    _normalize_file_ids(source.get(attachment_key))
                )
            if _has_attachment(source.get("youtube")):
                has_unsupported_youtube = True

        attachment_parts: list[dict[str, Any]] = []
        if file_ids and upload_files_bool:
            upload_result = upload_files(
                db,
                file_ids,
                user_id,
                input_formats_allowed,
                counters=counters,
            )
            counters = upload_result.get("counters", counters)
            attachment_parts.extend(upload_result.get("parts") or [])
            if upload_result.get("unsupported"):
                unsupported_flag = True
                _mark_unsupported_file_ids(upload_result.get("unsupported_file_ids"))
        elif file_ids:
            unsupported_flag = True
            _mark_unsupported_file_ids(file_ids)

        if unsupported_attachment_ids or has_unsupported_youtube:
            unsupported_flag = True
            _mark_unsupported_file_ids(unsupported_attachment_ids)

        if attachment_parts:
            for entry in reversed(formatted[formatted_start:]):
                if entry.get("role") == "assistant" and isinstance(
                    entry.get("content"), list
                ):
                    entry["content"].extend(attachment_parts)
                    break
            else:
                formatted.append({"role": "assistant", "content": attachment_parts})
        return True

    uploaded_cleanup = uploaded_cleanup or []
    formatted: list[dict] = []
    unsupported_flag = False
    unsupported_file_ids: set[str] = set()
    allowed_roles = {"user", "assistant"}
    counters = {
        "image": {
            "count": 0,
            "max": max_image_count if max_image_count is not None else -1,
        },
        "document": {
            "count": 0,
            "max": max_document_count if max_document_count is not None else -1,
        },
    }
    pending_web_search_id: str | None = None
    pending_web_search_results: list[dict] | None = None
    pending_web_search_input: dict | None = None
    pending_web_search_name: str = "web_search"

    def _normalize_file_ids(raw_ids):
        if not raw_ids:
            return []
        if isinstance(raw_ids, str):
            try:
                parsed = json.loads(raw_ids)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
                return [str(parsed)]
            except Exception:
                return [raw_ids]
        if isinstance(raw_ids, (list, tuple, set)):
            return [str(item) for item in raw_ids]
        return [str(raw_ids)]

    def _mark_unsupported_file_ids(raw_ids) -> None:
        for sid in normalize_unsupported_file_ids(raw_ids):
            unsupported_file_ids.add(sid)

    def _has_attachment(raw_value):
        if raw_value in (None, "", [], {}, ()):
            return False
        if isinstance(raw_value, str):
            stripped = raw_value.strip()
            if not stripped:
                return False
            try:
                parsed = json.loads(stripped)
            except Exception:
                return True
            return _has_attachment(parsed)
        if isinstance(raw_value, (list, tuple, set)):
            return any(_has_attachment(item) for item in raw_value)
        return True

    # ---------------------------------------------------------
    # Build Context Parts (Group + Project)
    # ---------------------------------------------------------
    context_parts = []

    def _build_reference_context_parts() -> list[dict]:
        """Build selected-reference parts that should stay with the latest prompt."""
        parts: list[dict] = []
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
                parts.append({"type": "text", "text": ref_intro + ref_content})
        if isinstance(chat_reference_context, str) and chat_reference_context.strip():
            parts.append({"type": "text", "text": chat_reference_context.strip()})
        return parts

    def _append_reference_context_to_latest_user(
        reference_context_parts: list[dict], latest_user_message: dict | None
    ) -> None:
        """Attach reference context before the newest user prompt instead of the first one."""
        if not reference_context_parts:
            return
        if latest_user_message is not None:
            content = latest_user_message.get("content")
            if isinstance(content, list):
                content[0:0] = reference_context_parts
            else:
                latest_user_message["content"] = list(reference_context_parts)
            return
        formatted.append({"role": "user", "content": list(reference_context_parts)})

    # Group Context
    group_context_enabled = get_user_group_setting_value(
        user_id,
        "context",
        "enable_group_context",
        db,
    )
    if use_group_context and group_context_enabled:
        group_context_raw = get_group_context_start(db, user_id)
        group_context = group_context_raw.get("context", "")
        group_context_file_ids = group_context_raw.get("group_context_file_ids", [])
        if group_context:
            context_parts.append({"type": "text", "text": group_context})
        if group_context_file_ids and upload_files_bool:
            context_counters = copy.deepcopy(counters) if counters else None
            upload_result = upload_files(
                db,
                group_context_file_ids,
                user_id,
                input_formats_allowed,
                counters=context_counters,
            )
            parts = upload_result.get("parts", [])
            if parts:
                context_parts.extend(parts)
            if upload_result.get("unsupported"):
                unsupported_flag = True
                _mark_unsupported_file_ids(upload_result.get("unsupported_file_ids"))
        end_text = get_group_context_end()
        if end_text:
            context_parts.append({"type": "text", "text": end_text})

    # Project Context
    if use_project_context and project_id and db and user_id:
        project_start = get_project_context_start(db, user_id, project_id)
        if project_start:
            context_parts.append({"type": "text", "text": project_start})
        if upload_files_bool:
            data = safe_list_project_files(
                db,
                user_id,
                project_id,
                logger=logger,
                log_prefix="[Anthropic]",
                failure_message="Project file attach failed",
            )
            file_ids = []
            for item in data:
                if isinstance(item, dict):
                    file_id = item.get("id")
                else:
                    file_id = getattr(item, "id", None)
                if file_id is not None:
                    file_ids.append(str(file_id))
            if file_ids:
                project_counters = copy.deepcopy(counters) if counters else None
                upload_result = upload_files(
                    db,
                    file_ids,
                    user_id,
                    input_formats_allowed,
                    counters=project_counters,
                )
                parts = upload_result.get("parts", [])
                if parts:
                    context_parts.extend(parts)
                if upload_result.get("unsupported"):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(
                        upload_result.get("unsupported_file_ids")
                    )
        project_end = get_project_context_end()
        if project_end:
            context_parts.append({"type": "text", "text": project_end})

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
                    context_parts.append({"type": "text", "text": notes_start})
                notes_end = get_notes_context_end()
                if notes_end:
                    context_parts.append({"type": "text", "text": notes_end})
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "[Anthropic] Notes context attach failed: %s", exc
            )

    if db and user_id:
        try:
            from app.llm.system_instruction.memories import get_memories_context

            memories_context = get_memories_context(db, user_id, project_id=project_id)
            if memories_context:
                context_parts.append({"type": "text", "text": memories_context})
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "[Anthropic] Memories context attach failed: %s", exc
            )

    reference_context_parts = _build_reference_context_parts()

    context_added = False
    latest_history_user_message: dict | None = None

    if not chat_history:
        if context_parts:
            return {
                "formatted": [{"role": "user", "content": context_parts}],
                "uploaded_cleanup": uploaded_cleanup,
                "unsupported": unsupported_flag,
                "unsupported_file_ids": sorted(unsupported_file_ids),
            }
        else:
            return {
                "formatted": [],
                "uploaded_cleanup": uploaded_cleanup,
                "unsupported": False,
                "unsupported_file_ids": [],
            }

    for raw_msg in chat_history:
        msg = _msg_to_dict(raw_msg) or {}
        role = msg.get("role")
        if role not in allowed_roles:
            continue

        meta = msg.get("meta") or {}
        message_blocks = _normalize_blocks(msg.get("content"))
        if role == "assistant" and _append_structured_assistant_history(
            msg, message_blocks
        ):
            # Structured reconstruction already emitted every provider-native
            # block in order, including any user-role tool results.
            continue
        text_content = _extract_text(message_blocks)
        if not text_content:
            text_content = _extract_text(msg.get("system_instruction"))

        compaction_parts = []
        if role == "assistant" and message_blocks:
            for block in message_blocks:
                if not isinstance(block, dict) or block.get("type") != "compaction":
                    continue
                compaction_content = block.get("content")
                if isinstance(compaction_content, str) and compaction_content.strip():
                    compaction_parts.append(
                        {
                            "type": "compaction",
                            "content": compaction_content,
                        }
                    )

        has_files = bool(msg.get("images") or msg.get("documents"))
        if not text_content and not has_files and not compaction_parts:
            continue

        parts = list(compaction_parts)
        if text_content:
            parts.append({"type": "text", "text": text_content})

        file_ids = []
        if msg.get("images"):
            file_ids.extend(_normalize_file_ids(msg.get("images")))
        if msg.get("documents"):
            file_ids.extend(_normalize_file_ids(msg.get("documents")))

        if file_ids and upload_files_bool:
            res = upload_files(
                db,
                file_ids,
                user_id,
                input_formats_allowed,
                counters=counters,
            )
            counters = res.get("counters", counters)
            if res.get("parts"):
                parts.extend(res["parts"])
            if res.get("unsupported"):
                unsupported_flag = True
                _mark_unsupported_file_ids(res.get("unsupported_file_ids"))
        elif file_ids and not upload_files_bool:
            unsupported_flag = True
            _mark_unsupported_file_ids(file_ids)

        # Insert context if this is the first user message
        if role == "user" and not context_added and context_parts:
            parts = context_parts + parts
            context_added = True

        if parts:
            formatted.append({"role": role, "content": parts})
            if role == "user":
                latest_history_user_message = formatted[-1]

        # Capture native Anthropic web search server tool usage data.
        if meta:
            server_tool = (meta.get("anthropic") or {}).get("server_tool_use") or {}
            web_search_requests = server_tool.get("web_search_requests") or 0
            history = meta.get("web_search_history") or []
            history_lookup_id = meta.get("web_search_tool_use_id")
            matching_event = None
            if history_lookup_id:
                matching_event = next(
                    (
                        event
                        for event in history
                        if event.get("tool_use_id") == history_lookup_id
                    ),
                    None,
                )
            if not matching_event and history:
                matching_event = history[-1]
            if web_search_requests and (
                meta.get("web_search_sources")
                or (matching_event and matching_event.get("results"))
            ):
                event_id = history_lookup_id or (
                    matching_event.get("tool_use_id") if matching_event else None
                )
                pending_web_search_id = event_id or f"srvtoolu_{uuid.uuid4().hex}"
                pending_web_search_results = (
                    (matching_event.get("results") if matching_event else None)
                    or meta.get("web_search_sources")
                    or []
                )
                pending_web_search_input = (
                    matching_event.get("input") if matching_event else None
                ) or {}
                pending_web_search_name = (
                    matching_event.get("name") if matching_event else None
                ) or "web_search"

        if pending_web_search_id and pending_web_search_results is not None:
            assistant_content = [
                {
                    "type": "server_tool_use",
                    "id": pending_web_search_id,
                    "name": pending_web_search_name,
                    "input": pending_web_search_input or {},
                },
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": pending_web_search_id,
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": page.get("url"),
                            "title": page.get("title"),
                            "encrypted_content": page.get("encrypted_content"),
                            "page_age": page.get("page_age"),
                        }
                        for page in pending_web_search_results
                        if page
                    ],
                },
            ]
            formatted.append({"role": "assistant", "content": assistant_content})
            pending_web_search_id = None
            pending_web_search_results = None
            pending_web_search_input = None
            pending_web_search_name = "web_search"

        for attachment_key in ("videos", "audios", "youtube"):
            if _has_attachment(msg.get(attachment_key)):
                unsupported_flag = True
                if attachment_key in {"videos", "audios"}:
                    _mark_unsupported_file_ids(
                        _normalize_file_ids(msg.get(attachment_key))
                    )

    # If context still not added (e.g. all messages were skipped or no user message), prepend it
    if not context_added and context_parts:
        formatted.insert(0, {"role": "user", "content": context_parts})

    _append_reference_context_to_latest_user(
        reference_context_parts, latest_history_user_message
    )

    return {
        "formatted": formatted,
        "uploaded_cleanup": uploaded_cleanup,
        "unsupported": unsupported_flag,
        "unsupported_file_ids": sorted(unsupported_file_ids),
    }
