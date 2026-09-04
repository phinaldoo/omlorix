"""Ollama chat-history normalization and message formatting.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.ollama import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "reformat_chat_history": (
        "Any",
        "SessionLocal",
        "TEXT_EXTRACTED_DOCUMENT_MIME_TYPES",
        "_ollama_images_allowed",
        "base64",
        "build_file_metadata_text",
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
    "_ollama_images_allowed",
    "base64",
    "build_file_metadata_text",
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
    "render_pdf_pages_to_png_bytes",
    "safe_list_project_files",
    "should_convert_pdf_to_images",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_reformat_chat_history(
    chathistory,
    user_id: str | None = None,
    db=None,
    include_tool_content: bool = True,
    project_id: str | None = None,
    input_formats_allowed: list[str] | None = None,
    use_group_context: bool = True,
    use_project_context: bool = True,
    max_image_count: int | None = None,
    max_document_count: int | None = None,
    note_ids: list[str] | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_context: str | None = None,
):
    """
    Transform persisted chat history into Ollama message format, including base64 images.

    The message object may contain:
      - role: "system" | "user" | "assistant" | "tool"
      - content: textual content
      - thinking: optional string/dict/list (kept as string)
      - images: JSON string or list of file identifiers (either "<uuid>___<original>" or just "<uuid>")
      - tool_name/name: tool name for tool role
    """

    def _get(obj, key, default=None):
        """Safely read attribute or dict key."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _remove_widget_blocks(obj):
        if obj is None:
            return None
        if isinstance(obj, dict):
            if obj.get("type") == "widget":
                return None
            cleaned = {}
            for key, val in obj.items():
                sanitized = _remove_widget_blocks(val)
                if sanitized is not None:
                    cleaned[key] = sanitized
            return cleaned
        if isinstance(obj, list):
            cleaned_list = []
            for item in obj:
                sanitized = _remove_widget_blocks(item)
                if sanitized is not None:
                    cleaned_list.append(sanitized)
            return cleaned_list
        return obj

    def _to_str(value):
        """Coerce value to string for content/thinking fields."""
        if value is None:
            return None
        try:
            if isinstance(value, dict):
                if value.get("type") == "tool_call":
                    return format_tool_call_block_label(value) or None
                sanitized = _remove_widget_blocks(value)
                if sanitized in (None, {}):
                    return None
                block_text = sanitized.get("content") or sanitized.get("text")
                if isinstance(block_text, str):
                    return block_text
                return json.dumps(sanitized)
            if isinstance(value, list):
                text_parts: list[str] = []
                for item in value:
                    item_text = _to_str(item)
                    if item_text:
                        text_parts.append(item_text)
                return "\n\n".join(text_parts) or None
            if isinstance(value, str):
                return value
        except Exception:
            pass
        return str(value)

    def _parse_images_field(img_field):
        """Return list[str] of ids/filenames from various stored formats."""
        if img_field is None:
            return []
        if isinstance(img_field, list):
            return [str(x) for x in img_field]
        if isinstance(img_field, str):
            # Try JSON array
            try:
                parsed = json.loads(img_field)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
                # If a JSON scalar, treat as single id
                return [str(parsed)]
            except Exception:
                # Treat as single identifier string
                return [img_field]
        # Fallback: try to coerce
        try:
            return [str(img_field)]
        except Exception:
            return []

    def _extract_text_snippet(file_id: str, file_info: dict | None) -> str | None:
        text_content = extract_text_from_file_info(file_info)
        if isinstance(text_content, str) and text_content.strip():
            content_to_use = text_content
            truncated = False
            if len(content_to_use) > 200000:
                content_to_use = content_to_use[:200000]
                truncated = True
            original_name = None
            if isinstance(file_info, dict):
                meta = (
                    file_info.get("meta")
                    if isinstance(file_info.get("meta"), dict)
                    else {}
                )
                original_name = (
                    meta.get("original_filename") if isinstance(meta, dict) else None
                )
            display_name = (
                original_name
                or (file_info.get("file_name") if isinstance(file_info, dict) else None)
                or str(file_id)
            )
            prefix = f"{display_name}\n\n" if display_name else ""
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
        metadata = extracted.get("metadata") or {}
        meta_info = (
            metadata.get("meta") if isinstance(metadata.get("meta"), dict) else {}
        )
        original_name = (
            meta_info.get("original_filename") if isinstance(meta_info, dict) else None
        )
        metadata_name = (
            metadata.get("name") if isinstance(metadata.get("name"), str) else None
        )
        stored_name = (
            metadata.get("stored_name")
            if isinstance(metadata.get("stored_name"), str)
            else None
        )
        fallback_name = None
        if file_info and isinstance(file_info, dict):
            fallback_name = file_info.get("file_name")
        display_name = (
            original_name
            or metadata_name
            or fallback_name
            or stored_name
            or str(file_id)
        )
        prefix = f"{display_name}\n\n" if display_name else ""
        suffix = "\n\n...[truncated]" if truncated else ""
        return f"{prefix}{content_to_use}{suffix}"

    def _merge_content_text(existing_content: Any, addition: str | None) -> str | None:
        if not addition:
            if isinstance(existing_content, str):
                return existing_content
            return _to_str(existing_content)
        existing_text = (
            existing_content
            if isinstance(existing_content, str)
            else _to_str(existing_content)
        )
        if existing_text:
            return f"{existing_text}\n\n{addition}"
        return addition

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
        for entry in reversed(formatted[history_start_index:]):
            if not isinstance(entry, dict) or entry.get("role") != "user":
                continue
            existing_content = entry.get("content")
            existing_text = (
                existing_content
                if isinstance(existing_content, str)
                else _to_str(existing_content)
            )
            merged_content = (
                f"{reference_text}\n\n{existing_text}"
                if existing_text
                else reference_text
            )
            if merged_content:
                entry["content"] = merged_content
            return
        formatted.append({"role": "user", "content": reference_text})

    def _metadata_texts_for_ids(
        identifiers,
        *,
        native_context_included: bool | None = None,
        model_context_representation: str | None = None,
        text_content_included: bool | None = None,
    ) -> list[str]:
        if not identifiers or not user_id:
            return []
        metadata_texts: list[str] = []
        seen: set[str] = set()
        for ident in identifiers:
            sid = str(ident or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            try:
                file_info = get_file_info(user_id, sid)
            except Exception:
                continue
            if not file_info:
                continue
            metadata_texts.append(
                build_file_metadata_text(
                    sid,
                    file_info,
                    native_context_included=native_context_included,
                    model_context_representation=model_context_representation,
                    text_content_included=text_content_included,
                )
            )
        return metadata_texts

    def _resolve_to_b64(identifiers):
        if not identifiers or not user_id:
            return []
        out = []
        seen_in_batch: set[str] = set()
        for ident in identifiers:
            try:
                sid = str(ident).strip()
                if not sid or sid in seen_in_batch:
                    continue
                seen_in_batch.add(sid)
                file_info = get_file_info(user_id, sid)
                if not file_info:
                    continue
                if file_info.get("file_category") != "image":
                    continue
                file_type = file_info.get("file_type")
                if not isinstance(file_type, str) or not file_type.startswith("image/"):
                    continue
                path = file_info.get("path")
                if not path:
                    continue
                with open(path, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("utf-8")
                out.append(b64)
            except Exception as exc:
                logger.warning("[Ollama] Failed to resolve image %s: %s", ident, exc)
                continue
        return out

    unsupported_flag = False
    unsupported_file_ids: set[str] = set()

    def _mark_unsupported_file_ids(raw_ids) -> None:
        for sid in normalize_unsupported_file_ids(raw_ids):
            unsupported_file_ids.add(sid)

    formatted: list[dict] = []
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

    def _has_capacity(category: str) -> bool:
        counter = counters.get(category)
        if not counter:
            return True
        max_allowed = counter.get("max", -1)
        if max_allowed < 0:
            return True
        return counter["count"] < max_allowed

    def _increment(category: str, amount: int = 1):
        counter = counters.get(category)
        if counter:
            counter["count"] += amount

    def _limit_ids(ids: list[str], category: str) -> list[str]:
        counter = counters.get(category)
        if not counter or not ids:
            return ids
        max_allowed = counter.get("max", -1)
        if max_allowed < 0:
            return ids
        remaining = max_allowed - counter["count"]
        if remaining <= 0:
            return []
        return ids[:remaining]

    def _remaining_capacity(category: str) -> int | None:
        counter = counters.get(category)
        if not counter:
            return None
        max_allowed = counter.get("max", -1)
        if max_allowed < 0:
            return None
        return max(max_allowed - counter["count"], 0)

    def _resolve_document_payload(
        doc_ids: list[str],
    ) -> tuple[list[str], list[str], list[str], bool]:
        if not doc_ids:
            return [], [], [], False

        image_b64: list[str] = []
        text_snippets: list[str] = []
        image_metadata_texts: list[str] = []
        unsupported = False
        use_pdf_image_fallback = should_convert_pdf_to_images(input_formats_allowed)
        documents_allowed_natively = (
            not input_formats_allowed
            or "pdf" in input_formats_allowed
            or "documents" in input_formats_allowed
        )

        if not user_id:
            _mark_unsupported_file_ids(doc_ids)
            return [], [], [], True

        for doc_id in doc_ids:
            try:
                file_info = get_file_info(user_id, str(doc_id))
            except Exception:
                unsupported = True
                _mark_unsupported_file_ids([doc_id])
                continue
            if not file_info:
                unsupported = True
                _mark_unsupported_file_ids([doc_id])
                continue

            file_type = normalize_file_mime_type(file_info.get("file_type"))
            file_path = file_info.get("path")

            if use_pdf_image_fallback and file_type == "application/pdf" and file_path:
                remaining_images = _remaining_capacity("image")
                if remaining_images == 0:
                    unsupported = True
                    _mark_unsupported_file_ids([doc_id])
                    continue
                try:
                    page_images = render_pdf_pages_to_png_bytes(
                        file_path,
                        max_pages=remaining_images,
                    )
                except Exception as exc:
                    logger.warning(
                        "[Ollama] Failed PDF-to-image conversion for %s: %s",
                        doc_id,
                        exc,
                    )
                    unsupported = True
                    _mark_unsupported_file_ids([doc_id])
                    continue
                if not page_images:
                    unsupported = True
                    _mark_unsupported_file_ids([doc_id])
                    continue
                image_metadata_texts.append(
                    build_file_metadata_text(
                        doc_id,
                        file_info,
                        native_context_included=False,
                        model_context_representation="rendered_images",
                        text_content_included=False,
                    )
                )
                for image_bytes in page_images:
                    image_b64.append(base64.b64encode(image_bytes).decode("utf-8"))
                _increment("image", len(page_images))
                continue

            # Source-text documents remain valid even when the provider model
            # does not advertise native document/PDF input.
            if (
                not documents_allowed_natively
                and file_type not in TEXT_EXTRACTED_DOCUMENT_MIME_TYPES
            ):
                unsupported = True
                _mark_unsupported_file_ids([doc_id])
                continue
            if not _has_capacity("document"):
                unsupported = True
                _mark_unsupported_file_ids([doc_id])
                continue

            snippet = _extract_text_snippet(str(doc_id), file_info)
            if snippet:
                text_snippets.append(
                    build_file_metadata_text(
                        doc_id,
                        file_info,
                        native_context_included=False,
                        model_context_representation="text_extract",
                        text_content_included=True,
                    )
                )
                text_snippets.append(snippet)
                _increment("document")
            else:
                unsupported = True
                _mark_unsupported_file_ids([doc_id])

        return image_b64, text_snippets, image_metadata_texts, unsupported

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
                "[Ollama] Failed to read group context setting for user %s: %s",
                user_id,
                exc,
            )

    if use_group_context and group_context_enabled:
        try:
            group_context_raw = get_group_context_start(db, user_id) or {}
        except Exception as exc:
            logger.warning(
                "[Ollama] Failed to load group context for user %s: %s",
                user_id,
                exc,
            )
            group_context_raw = {}

        group_context_text = group_context_raw.get("context") or ""
        group_context_file_ids = group_context_raw.get("group_context_file_ids") or []

        if group_context_text:
            formatted.append({"role": "user", "content": group_context_text})

        image_ids: list[str] = []
        document_ids: list[str] = []

        for file_id in group_context_file_ids:
            sid = str(file_id).strip()
            if not sid:
                continue
            try:
                file_info = get_file_info(user_id, sid)
            except Exception as exc:
                logger.warning(
                    "[Ollama] Failed to load group context file %s for user %s: %s",
                    file_id,
                    user_id,
                    exc,
                )
                continue
            if not file_info:
                continue

            category = file_info.get("file_category")
            if category == "image":
                if _has_capacity("image"):
                    image_ids.append(sid)
                continue
            if category == "document":
                document_ids.append(sid)
                continue

            unsupported_flag = True
            _mark_unsupported_file_ids([sid])

        if image_ids:
            if _ollama_images_allowed(input_formats_allowed):
                allowed_image_ids = _limit_ids(image_ids, "image")
                group_images = _resolve_to_b64(allowed_image_ids)
                if group_images:
                    group_message = {"role": "user", "images": group_images}
                    metadata_text = "\n\n".join(
                        _metadata_texts_for_ids(allowed_image_ids)
                    )
                    if metadata_text:
                        group_message["content"] = metadata_text
                    formatted.append(group_message)
                    _increment("image", len(group_images))
            else:
                unsupported_flag = True
                _mark_unsupported_file_ids(image_ids)
                metadata_text = "\n\n".join(
                    _metadata_texts_for_ids(
                        image_ids,
                        native_context_included=False,
                        model_context_representation="metadata_only",
                        text_content_included=False,
                    )
                )
                if metadata_text:
                    formatted.append({"role": "user", "content": metadata_text})

        (
            document_images,
            document_snippets,
            document_metadata_texts,
            docs_unsupported,
        ) = _resolve_document_payload(document_ids)
        if document_images:
            document_message = {"role": "user", "images": document_images}
            metadata_text = "\n\n".join(document_metadata_texts)
            if metadata_text:
                document_message["content"] = metadata_text
            formatted.append(document_message)
        if document_snippets:
            formatted.append(
                {"role": "user", "content": "\n\n".join(document_snippets)}
            )
        if docs_unsupported:
            unsupported_flag = True

        try:
            group_context_end = get_group_context_end()
        except Exception as exc:
            logger.warning(
                "[Ollama] Failed to load group context end for user %s: %s",
                user_id,
                exc,
            )
            group_context_end = ""

        if (group_context_text or group_context_file_ids) and group_context_end:
            formatted.append({"role": "user", "content": group_context_end})

    project_image_ids: list[str] = []
    project_document_ids: list[str] = []
    project_start_text: str | None = None
    project_end_text: str | None = None
    if use_project_context and project_id and db is not None and user_id:
        try:
            project_start_text = get_project_context_start(db, user_id, project_id)
        except Exception as exc:
            logger.warning(
                "[Ollama] Failed to load project instruction for project %s: %s",
                project_id,
                exc,
            )
        project_files = safe_list_project_files(
            db,
            user_id,
            project_id,
            logger=logger,
            log_prefix="[Ollama]",
            failure_message="Failed to load project images",
            include_project_id=True,
        )
        seen_project_ids: set[str] = set()
        for pfile in project_files:
            file_id = getattr(pfile, "id", None)
            file_category = getattr(pfile, "file_category", None)
            file_type = normalize_file_mime_type(getattr(pfile, "file_type", None))
            if not file_id or not isinstance(file_id, (str, int)):
                continue
            sid = str(file_id).strip()
            if not sid or sid in seen_project_ids:
                continue
            # Preserve access to source-text records created with an incorrect
            # image category by correcting the legacy ORM value here.
            if file_type in TEXT_EXTRACTED_DOCUMENT_MIME_TYPES:
                project_document_ids.append(sid)
                seen_project_ids.add(sid)
                continue
            if file_category == "document":
                project_document_ids.append(sid)
                seen_project_ids.add(sid)
                continue
            if file_category != "image":
                unsupported_flag = True
                _mark_unsupported_file_ids([sid])
                continue
            if not isinstance(file_type, str) or not file_type.startswith("image/"):
                unsupported_flag = True
                _mark_unsupported_file_ids([sid])
                continue
            project_image_ids.append(sid)
            seen_project_ids.add(sid)
        try:
            project_end_text = get_project_context_end()
        except Exception as exc:
            logger.warning(
                "[Ollama] Failed to load project instruction end for project %s: %s",
                project_id,
                exc,
            )

    if project_start_text:
        formatted.append({"role": "user", "content": project_start_text})
    if project_image_ids:
        if _ollama_images_allowed(input_formats_allowed):
            allowed_project_ids = _limit_ids(project_image_ids, "image")
            project_images = _resolve_to_b64(allowed_project_ids)
            if project_images:
                project_message = {"role": "user", "images": project_images}
                metadata_text = "\n\n".join(
                    _metadata_texts_for_ids(allowed_project_ids)
                )
                if metadata_text:
                    project_message["content"] = metadata_text
                formatted.append(project_message)
                _increment("image", len(project_images))
        else:
            unsupported_flag = True
            _mark_unsupported_file_ids(project_image_ids)
            metadata_text = "\n\n".join(
                _metadata_texts_for_ids(
                    project_image_ids,
                    native_context_included=False,
                    model_context_representation="metadata_only",
                    text_content_included=False,
                )
            )
            if metadata_text:
                formatted.append({"role": "user", "content": metadata_text})
    (
        project_document_images,
        project_document_snippets,
        project_document_metadata_texts,
        project_docs_unsupported,
    ) = _resolve_document_payload(project_document_ids)
    if project_document_images:
        project_document_message = {"role": "user", "images": project_document_images}
        metadata_text = "\n\n".join(project_document_metadata_texts)
        if metadata_text:
            project_document_message["content"] = metadata_text
        formatted.append(project_document_message)
    if project_document_snippets:
        formatted.append(
            {"role": "user", "content": "\n\n".join(project_document_snippets)}
        )
    if project_docs_unsupported:
        unsupported_flag = True
    if project_end_text:
        formatted.append({"role": "user", "content": project_end_text})

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
                    formatted.append({"role": "user", "content": notes_start})
                notes_end = get_notes_context_end()
                if notes_end:
                    formatted.append({"role": "user", "content": notes_end})
        except Exception as exc:
            logger.warning("[Ollama] Notes context attach failed: %s", exc)

    memories_start_index = len(formatted)
    if db and user_id:
        try:
            from app.llm.system_instruction.memories import get_memories_context

            memories_context = get_memories_context(db, user_id, project_id=project_id)
            if memories_context:
                formatted.append({"role": "user", "content": memories_context})
        except Exception as exc:
            logger.warning("[Ollama] Memories context attach failed: %s", exc)

    reference_context_text = _build_reference_context_text()
    history_start_index = len(formatted)

    if not chathistory:
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

    allowed_roles = {"user", "assistant", "tool"}

    def _parse_generic_field(field):
        if field is None:
            return []
        if isinstance(field, (list, tuple, set)):
            return [str(item) for item in field if item is not None]
        if isinstance(field, str):
            candidate = field.strip()
            if not candidate:
                return []
            try:
                decoded = json.loads(candidate)
                if isinstance(decoded, list):
                    return [str(item) for item in decoded if item is not None]
            except Exception:
                pass
            return [candidate]
        return [str(field)]

    def _normalize_content_blocks(value: Any) -> list[Any]:
        """Decode canonical blocks from ORM JSON strings or in-memory lists."""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
        return []

    def _structured_assistant_messages(blocks: list[Any]) -> list[dict] | None:
        """Rebuild Ollama assistant/tool turns with their original call IDs."""
        block_types = {
            str(block.get("type") or "").strip().lower()
            for block in blocks
            if isinstance(block, dict)
        }
        if not block_types.intersection({"reasoning", "tool_call", "tool_call_result"}):
            return None

        result_messages: list[dict] = []
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[dict] = []
        call_names: dict[str, str] = {}
        last_call_id: str | None = None

        def flush_assistant() -> None:
            if not content_parts and not thinking_parts and not tool_calls:
                return
            message: dict[str, Any] = {
                "role": "assistant",
                "content": "\n\n".join(content_parts),
            }
            if thinking_parts:
                message["thinking"] = "".join(thinking_parts)
            if tool_calls:
                message["tool_calls"] = list(tool_calls)
            result_messages.append(message)
            content_parts.clear()
            thinking_parts.clear()
            tool_calls.clear()

        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type in {"widget", "file", "file_gen"}:
                continue
            if block_type == "reasoning":
                value = block.get("content")
                if isinstance(value, str):
                    thinking_parts.append(value)
                continue
            if block_type in {"content", "assistant", "text"}:
                value = block.get("content") or block.get("text")
                if isinstance(value, str):
                    content_parts.append(value)
                continue
            if block_type == "tool_call":
                extracted = extract_tool_call_block(block)
                call_id = str(extracted.get("tool_call_id") or "").strip()
                if not call_id:
                    continue
                tool_name = str(extracted.get("tool_name") or "tool")
                arguments = extracted.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                tool_calls.append(
                    {
                        "id": call_id,
                        "function": {
                            "name": tool_name,
                            "arguments": arguments,
                        },
                    }
                )
                call_names[call_id] = tool_name
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
                tool_name = (
                    call_names.get(call_id)
                    or str(block.get("tool_name") or "tool").split("(", 1)[0]
                )
                result_messages.append(
                    {
                        "role": "tool",
                        "content": (
                            str(block.get("content") or "")
                            if include_tool_content
                            else ""
                        ),
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                    }
                )

        flush_assistant()
        return result_messages or None

    for m in chathistory:
        try:
            role = _get(m, "role")
            if not role or role not in allowed_roles:
                continue

            if role == "assistant":
                has_attachments = bool(
                    _parse_images_field(_get(m, "images"))
                    or _parse_generic_field(_get(m, "documents"))
                )
                structured_messages = None
                if not has_attachments:
                    structured_messages = _structured_assistant_messages(
                        _normalize_content_blocks(_get(m, "content"))
                    )
                if structured_messages is not None:
                    formatted.extend(structured_messages)
                    continue

            msg: dict = {"role": role}

            if role == "tool":
                if include_tool_content:
                    content = _get(m, "content")
                    content_str = _to_str(content)
                    if content_str is not None:
                        msg["content"] = content_str
            else:
                content = _get(m, "content")
                content_str = _to_str(content)
                if content_str is not None:
                    msg["content"] = content_str

            # Thinking is optional
            thinking = _get(m, "thinking")
            thinking_str = _to_str(thinking)
            if thinking_str:
                msg["thinking"] = thinking_str

            # For tool results, include the tool/function name if present
            if role == "tool":
                tool_name = _get(m, "tool_name")
                if tool_name is None:
                    tool_name = _get(m, "name")
                if tool_name:
                    msg["tool_name"] = str(tool_name)

            # Images: resolve to base64 list if any
            imgs_field = _get(m, "images")
            identifiers = _parse_images_field(imgs_field)
            allowed_identifiers = _limit_ids(identifiers, "image")
            if not _ollama_images_allowed(input_formats_allowed):
                if identifiers:
                    unsupported_flag = True
                    _mark_unsupported_file_ids(identifiers)
                    metadata_text = "\n\n".join(
                        _metadata_texts_for_ids(
                            identifiers,
                            native_context_included=False,
                            model_context_representation="metadata_only",
                            text_content_included=False,
                        )
                    )
                    merged_content = _merge_content_text(
                        msg.get("content"), metadata_text
                    )
                    if merged_content:
                        msg["content"] = merged_content
            else:
                b64_list = _resolve_to_b64(allowed_identifiers)
                if b64_list:
                    msg["images"] = b64_list
                    metadata_text = "\n\n".join(
                        _metadata_texts_for_ids(
                            allowed_identifiers,
                            native_context_included=True,
                            model_context_representation="native_file",
                            text_content_included=False,
                        )
                    )
                    merged_content = _merge_content_text(
                        msg.get("content"), metadata_text
                    )
                    if merged_content:
                        msg["content"] = merged_content
                    _increment("image", len(b64_list))
                else:
                    if allowed_identifiers and identifiers:
                        # identifiers were present but failed to resolve
                        unsupported_flag = True
                        _mark_unsupported_file_ids(identifiers)
                        metadata_text = "\n\n".join(
                            _metadata_texts_for_ids(
                                identifiers,
                                native_context_included=False,
                                model_context_representation="metadata_only",
                                text_content_included=False,
                            )
                        )
                        merged_content = _merge_content_text(
                            msg.get("content"), metadata_text
                        )
                        if merged_content:
                            msg["content"] = merged_content

            # Detect unsupported attachment categories
            doc_ids = _parse_generic_field(_get(m, "documents"))
            vid_ids = _parse_generic_field(_get(m, "videos"))
            aud_ids = _parse_generic_field(_get(m, "audios"))
            if vid_ids:
                if (
                    input_formats_allowed is None
                    or "video" not in input_formats_allowed
                ):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(vid_ids)
                    metadata_text = "\n\n".join(
                        _metadata_texts_for_ids(
                            vid_ids,
                            native_context_included=False,
                            model_context_representation="metadata_only",
                            text_content_included=False,
                        )
                    )
                    merged_content = _merge_content_text(
                        msg.get("content"), metadata_text
                    )
                    if merged_content:
                        msg["content"] = merged_content
            if aud_ids:
                if (
                    input_formats_allowed is None
                    or "audio" not in input_formats_allowed
                ):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(aud_ids)
                    metadata_text = "\n\n".join(
                        _metadata_texts_for_ids(
                            aud_ids,
                            native_context_included=False,
                            model_context_representation="metadata_only",
                            text_content_included=False,
                        )
                    )
                    merged_content = _merge_content_text(
                        msg.get("content"), metadata_text
                    )
                    if merged_content:
                        msg["content"] = merged_content
            if doc_ids:
                (
                    document_images,
                    text_snippets,
                    document_metadata_texts,
                    docs_unsupported,
                ) = _resolve_document_payload(doc_ids)
                if document_images:
                    existing_images = msg.get("images")
                    if isinstance(existing_images, list):
                        existing_images.extend(document_images)
                    else:
                        msg["images"] = document_images
                    metadata_text = "\n\n".join(document_metadata_texts)
                    merged_content = _merge_content_text(
                        msg.get("content"), metadata_text
                    )
                    if merged_content:
                        msg["content"] = merged_content
                if text_snippets:
                    msg_content = msg.get("content")
                    addition = "\n\n".join(text_snippets)
                    if isinstance(msg_content, str) and msg_content:
                        msg["content"] = f"{msg_content}\n\n{addition}"
                    elif isinstance(msg_content, str):
                        msg["content"] = addition
                    elif msg_content:
                        msg["content"] = addition
                    else:
                        msg["content"] = addition
                if docs_unsupported:
                    unsupported_flag = True
                elif doc_ids and not (document_images or text_snippets):
                    unsupported_flag = True
                    _mark_unsupported_file_ids(doc_ids)

            formatted.append(msg)
        except Exception:
            # Skip malformed entries rather than failing the whole reformat
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
