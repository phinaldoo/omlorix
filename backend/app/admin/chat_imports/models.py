"""
Import chats exported from Open WebUI into Omlorix.

The Open WebUI export is a JSON array where each element represents a
conversation.  Each conversation contains a nested tree of messages
stored under ``chat.history.messages`` (keyed by UUID) with parent/child
relationships.  This module walks the tree from the root to the leaf
for every branch, converts the messages into the Omlorix ``ChatMessages``
schema and persists each branch as an Omlorix chat.
"""

import csv
import io
import json
import logging
import math
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.chats.models import ChatMessages, Chats
from app.users.models import User
from sqlalchemy import func

logger = logging.getLogger(__name__)

# Regex to extract <details type="reasoning" ...> blocks that Open WebUI
# uses to wrap chain-of-thought / thinking content.
_REASONING_RE = re.compile(
    r"<details\b[^>]*\btype\s*=\s*['\"]reasoning['\"][^>]*>"
    r"\s*(?:<summary>.*?</summary>\s*)?(.*?)\s*</details>",
    re.DOTALL | re.IGNORECASE,
)

_SUPPORTED_ROLES = {"user", "assistant"}
_OPENWEBUI_TOOL_TYPES = {
    "web_search_call": "Web Search",
    "file_search_call": "File Search",
    "computer_call": "Computer Use",
}


def _unix_ts_to_utc(ts) -> datetime:
    """Convert a Unix timestamp (seconds) to a UTC datetime."""
    if ts is None:
        return datetime.now(timezone.utc)
    try:
        ts_float = float(ts)
        # Heuristic: if the value is unreasonably large it might be in
        # milliseconds.
        if ts_float > 1e12:
            ts_float /= 1000.0
        return datetime.fromtimestamp(ts_float, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return datetime.now(timezone.utc)


def _extract_thinking(content: str) -> tuple[str, str | None]:
    """
    Split Open WebUI assistant content into (visible_content, thinking).

    Open WebUI wraps chain-of-thought in ``<details type="reasoning">``
    HTML blocks.  We extract that text and store it in the dedicated
    ``thinking`` column, removing it from the main content.
    """
    if not content:
        return content or "", None

    thinking_parts: list[str] = []

    def _collect(match):
        thinking_parts.append(match.group(1).strip())
        return ""

    cleaned = _REASONING_RE.sub(_collect, content).strip()
    thinking = "\n\n".join(thinking_parts) if thinking_parts else None
    return cleaned, thinking


def _text_from_parts(value: Any) -> str:
    """Extract readable text from current and legacy Open WebUI content values.

    Open WebUI historically stored message content as a string, but current
    exports can contain OpenAI-style content-part lists.  Omlorix's transcript
    renderer expects the ``content`` member of its own text blocks to be a
    string, so retaining the foreign list verbatim would render as
    ``[object Object]`` in the browser.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "".join(_text_from_parts(part) for part in value)
    if isinstance(value, dict):
        # OpenAI/Open WebUI content parts use ``text``.  Nested Open WebUI
        # output items use ``content`` or ``summary`` arrays of those parts.
        if value.get("text") is not None:
            return _text_from_parts(value.get("text"))
        if value.get("content") is not None:
            return _text_from_parts(value.get("content"))
        if value.get("summary") is not None:
            return _text_from_parts(value.get("summary"))
        return ""
    return str(value)


def _json_string(value: Any) -> str:
    """Serialize structured tool arguments without losing non-ASCII text."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value or "")


def _structured_output_blocks(output: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Convert Open WebUI v0.11 structured output into Omlorix blocks.

    Open WebUI duplicates normal assistant text into ``message.content`` but
    keeps reasoning and tool activity only in ``message.output``.  Converting
    those records keeps the visible answer, reasoning, and ordinary tool
    calls available after migration instead of silently discarding them.
    """
    if not isinstance(output, list):
        return [], []

    blocks: list[dict[str, Any]] = []
    thinking_parts: list[str] = []
    tool_names_by_call_id: dict[str, str] = {}

    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()

        if item_type == "reasoning":
            reasoning_source = item.get("summary") or item.get("content")
            reasoning_text = _text_from_parts(reasoning_source).strip()
            if reasoning_text:
                thinking_parts.append(reasoning_text)
                # Omlorix renders reasoning from an inline block whenever a
                # message uses structured content.  Keep the database column
                # too, since it remains useful to non-transcript consumers.
                reasoning_block: dict[str, Any] = {
                    "type": "reasoning",
                    "content": reasoning_text,
                }
                try:
                    duration = float(item.get("duration"))
                except (TypeError, ValueError):
                    duration = 0
                if math.isfinite(duration) and duration > 0:
                    reasoning_block["meta"] = {"reasoning_time": duration}
                blocks.append(reasoning_block)
            continue

        if item_type == "message":
            text = _text_from_parts(item.get("content"))
            if text:
                blocks.append({"type": "content", "content": text})
            continue

        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            tool_name = str(item.get("name") or "tool").strip() or "tool"
            if call_id:
                tool_names_by_call_id[call_id] = tool_name
            blocks.append(
                {
                    "type": "tool_call",
                    "content": "",
                    "meta": {
                        "tool_name": tool_name,
                        "arguments": _json_string(item.get("arguments") or {}),
                        "tool_call_id": call_id,
                    },
                }
            )
            continue

        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            result_text = _text_from_parts(item.get("output") or item.get("content"))
            blocks.append(
                {
                    "type": "tool_call_result",
                    "content": result_text,
                    "tool_name": tool_names_by_call_id.get(call_id, "tool"),
                    "meta": {"tool_call_id": call_id},
                }
            )
            continue

        if item_type in _OPENWEBUI_TOOL_TYPES:
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            blocks.append(
                {
                    "type": "tool_call",
                    "content": "",
                    "meta": {
                        "tool_name": _OPENWEBUI_TOOL_TYPES[item_type],
                        "arguments": _json_string(
                            item.get("action")
                            or item.get("actions")
                            or item.get("queries")
                            or {}
                        ),
                        "tool_call_id": call_id,
                    },
                }
            )
            continue

        if item_type == "open_webui:code_interpreter":
            call_id = str(item.get("id") or "").strip()
            code = _text_from_parts(item.get("code"))
            language = str(item.get("lang") or "python")
            blocks.append(
                {
                    "type": "tool_call",
                    "content": "",
                    "meta": {
                        "tool_name": "Code Interpreter",
                        "arguments": _json_string({"language": language, "code": code}),
                        "tool_call_id": call_id,
                    },
                }
            )
            result_text = _text_from_parts(item.get("output"))
            if result_text:
                blocks.append(
                    {
                        "type": "tool_call_result",
                        "content": result_text,
                        "tool_name": "Code Interpreter",
                        "meta": {"tool_call_id": call_id},
                    }
                )
            continue

        # Preserve readable text from future Open WebUI output item types.
        fallback_text = _text_from_parts(item.get("content"))
        if fallback_text:
            blocks.append({"type": "content", "content": fallback_text})

    return blocks, thinking_parts


def _build_content_blocks(
    role: str, text: str, *, message: dict | None = None
) -> tuple[str, str | None]:
    """
    Convert one Open WebUI message into Omlorix content blocks and thinking.
    """
    message = message or {}
    normalized_text = _text_from_parts(text)

    if role == "user":
        return json.dumps(
            [{"type": "user", "content": normalized_text}], ensure_ascii=False
        ), None

    normalized_text, legacy_thinking = _extract_thinking(normalized_text)
    blocks, structured_thinking = _structured_output_blocks(message.get("output"))

    # Open WebUI normally mirrors its structured ``message`` output items in
    # ``content``.  Use the structured version when present and fall back to
    # the legacy content string when it is absent.
    has_visible_output = any(block.get("type") == "content" for block in blocks)
    if normalized_text and not has_visible_output:
        blocks.append({"type": "content", "content": normalized_text})
    if not blocks:
        blocks.append({"type": "content", "content": ""})

    direct_thinking = _text_from_parts(
        message.get("thinking") or message.get("reasoning_content")
    ).strip()
    thinking_parts = [
        part
        for part in [legacy_thinking, direct_thinking, *structured_thinking]
        if part
    ]
    # Deduplicate reasoning mirrored in more than one Open WebUI field while
    # retaining its original order.
    thinking = "\n\n".join(dict.fromkeys(thinking_parts)) or None

    # Legacy details blocks and provider-specific ``thinking`` fields are not
    # represented in ``output``.  Add any such reasoning to the structured
    # content too; otherwise the transcript renderer ignores the standalone
    # thinking column when normal/tool content blocks are present.
    rendered_reasoning = {
        str(block.get("content") or "").strip()
        for block in blocks
        if block.get("type") == "reasoning"
    }
    missing_reasoning = [
        part for part in dict.fromkeys(thinking_parts) if part not in rendered_reasoning
    ]
    blocks[0:0] = [{"type": "reasoning", "content": part} for part in missing_reasoning]
    return json.dumps(blocks, ensure_ascii=False), thinking


def _normalise_message_id(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _message_parent_id(message: dict) -> str | None:
    return _normalise_message_id(message.get("parentId"))


def _normalise_history_messages(history_messages: dict) -> tuple[dict[str, dict], int]:
    valid_messages: dict[str, dict] = {}
    skipped_messages = 0
    if not isinstance(history_messages, dict):
        return valid_messages, skipped_messages
    for raw_id, message in history_messages.items():
        message_id = _normalise_message_id(raw_id)
        if not message_id or not isinstance(message, dict):
            skipped_messages += 1
            continue
        valid_messages[message_id] = message
    return valid_messages, skipped_messages


def _children_lookup(history_messages: dict[str, dict]) -> dict[str, list[str]]:
    message_ids = set(history_messages)
    children_by_parent: dict[str, list[str]] = {
        message_id: [] for message_id in history_messages
    }

    for message_id, message in history_messages.items():
        children = message.get("childrenIds") or []
        if not isinstance(children, list):
            continue
        for raw_child_id in children:
            child_id = _normalise_message_id(raw_child_id)
            if (
                child_id in message_ids
                and child_id not in children_by_parent[message_id]
            ):
                children_by_parent[message_id].append(child_id)

    # Some exports have incomplete childrenIds but reliable parentId links.
    # Preserve the exported childrenIds order, then append any missing children.
    for child_id, message in history_messages.items():
        parent_id = _message_parent_id(message)
        if parent_id in message_ids and child_id not in children_by_parent[parent_id]:
            children_by_parent[parent_id].append(child_id)

    return children_by_parent


def _path_to_message(
    history_messages: dict[str, dict], message_id: str | None
) -> list[str]:
    current_id = _normalise_message_id(message_id)
    if not current_id or current_id not in history_messages:
        return []

    path: list[str] = []
    visited: set[str] = set()
    while current_id and current_id in history_messages:
        if current_id in visited:
            return []
        visited.add(current_id)
        path.append(current_id)
        parent_id = _message_parent_id(history_messages[current_id])
        if not parent_id or parent_id not in history_messages:
            break
        current_id = parent_id

    return list(reversed(path))


def _dedupe_paths(paths: list[list[str]]) -> list[list[str]]:
    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for path in paths:
        path_key = tuple(path)
        if not path or path_key in seen:
            continue
        seen.add(path_key)
        deduped.append(path)
    return deduped


def _build_message_branches(
    history_messages: dict, current_id: str | None
) -> tuple[list[dict], int]:
    """
    Build every root-to-leaf Open WebUI branch.

    Returns ``(branches, skipped_messages)``.  The selected ``currentId``
    branch, when present, is first so the primary imported chat keeps the
    original title.
    """
    valid_messages, skipped_messages = _normalise_history_messages(
        history_messages or {}
    )
    if not valid_messages:
        return [], skipped_messages

    children_by_parent = _children_lookup(valid_messages)
    roots = [
        message_id
        for message_id, message in valid_messages.items()
        if not _message_parent_id(message)
        or _message_parent_id(message) not in valid_messages
    ]

    if not roots:
        fallback_path = sorted(
            valid_messages,
            key=lambda message_id: (
                _unix_ts_to_utc(
                    valid_messages[message_id].get("timestamp")
                ).timestamp(),
                message_id,
            ),
        )
        return [
            {
                "message_ids": fallback_path,
                "messages": [
                    valid_messages[message_id] for message_id in fallback_path
                ],
                "leaf_id": fallback_path[-1] if fallback_path else None,
                "is_current": False,
            }
        ], skipped_messages

    paths: list[list[str]] = []

    # Use an explicit stack rather than recursion. Long-running Open WebUI
    # conversations can legitimately exceed Python's recursion depth, and a
    # migration must not discard them merely because they have 1,000+ turns.
    for root_id in roots:
        stack: list[tuple[str, list[str]]] = [(root_id, [])]
        while stack:
            message_id, path = stack.pop()
            next_path = [*path, message_id]
            children = [
                child_id
                for child_id in children_by_parent.get(message_id, [])
                if child_id not in next_path
            ]
            if not children:
                paths.append(next_path)
                continue
            # Reverse push order so traversal still honors childrenIds order.
            for child_id in reversed(children):
                stack.append((child_id, next_path))

    current_path = _path_to_message(valid_messages, current_id)
    if current_path:
        paths.insert(0, current_path)

    paths = _dedupe_paths(paths)
    current_path_key = tuple(current_path)
    current_message_id = _normalise_message_id(current_id)
    paths.sort(
        key=lambda path: (
            0 if current_path_key and tuple(path) == current_path_key else 1,
            0 if current_message_id and path[-1] == current_message_id else 1,
        )
    )

    imported_message_ids = {message_id for path in paths for message_id in path}
    skipped_messages += max(0, len(valid_messages) - len(imported_message_ids))

    branches: list[dict] = []
    for path in paths:
        leaf_id = path[-1] if path else None
        branches.append(
            {
                "message_ids": path,
                "messages": [valid_messages[message_id] for message_id in path],
                "leaf_id": leaf_id,
                "is_current": bool(
                    current_message_id
                    and (
                        tuple(path) == current_path_key or leaf_id == current_message_id
                    )
                ),
            }
        )

    return branches, skipped_messages


def _linearise_messages(history_messages: dict, current_id: str | None) -> list[dict]:
    """
    Return the primary Open WebUI branch as a flat ordered list.

    ``currentId`` is preferred when present.  Kept for compatibility with
    tests and callers that need one selected branch rather than all branches.
    """
    branches, _skipped_messages = _build_message_branches(history_messages, current_id)
    return branches[0]["messages"] if branches else []


def _strict_bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _prepare_branch_messages(ordered: list[Any]) -> tuple[list[dict[str, Any]], int]:
    """Validate and convert one branch before any database rows are staged.

    Preparing first is important: a malformed message must never leave behind
    a partially imported chat.  Unsupported Open WebUI-only roles are counted
    and skipped because Omlorix transcripts support user and assistant turns.
    """
    prepared: list[dict[str, Any]] = []
    skipped = 0
    previous_timestamp: datetime | None = None

    for raw_message in ordered:
        if not isinstance(raw_message, dict):
            skipped += 1
            continue

        role = str(raw_message.get("role") or "user").strip().lower()
        if role not in _SUPPORTED_ROLES:
            skipped += 1
            continue

        raw_content = raw_message.get("content")
        content_json, thinking = _build_content_blocks(
            role,
            raw_content,
            message=raw_message,
        )
        model_id = (
            str(
                raw_message.get("model") or raw_message.get("modelName") or "unknown"
            ).strip()
            or "unknown"
        )
        timestamp = _unix_ts_to_utc(raw_message.get("timestamp"))

        # Open WebUI timestamps have historically used whole seconds.  Omlorix
        # orders equal timestamps by random UUID, which can invert a fast
        # user/assistant pair.  A one-microsecond adjustment preserves the
        # exported path order without materially changing its time.
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            timestamp = previous_timestamp + timedelta(microseconds=1)
        previous_timestamp = timestamp

        prepared.append(
            {
                "role": role,
                "model_id": model_id,
                "content": content_json,
                "thinking": thinking,
                "created_at": timestamp,
            }
        )

    return prepared, skipped


def import_openwebui_chats(
    db, user_id: str, chats: list[dict], *, force_archived: bool = False
) -> dict:
    """
    Import a list of Open WebUI chat export objects for *user_id*.

    Returns a summary dict with counts.
    """
    imported_chats = 0
    imported_messages = 0
    imported_branches = 0
    skipped_chats = 0
    skipped_branches = 0
    skipped_messages = 0
    next_pinned_position = (
        db.query(func.max(Chats.pinned_position))
        .filter(Chats.user_id == user_id)
        .scalar()
        or 0
    ) + 1

    for entry in chats:
        imported_entry_branches = 0
        try:
            if not isinstance(entry, dict):
                skipped_chats += 1
                continue
            chat_data = entry.get("chat") or {}
            if not isinstance(chat_data, dict):
                skipped_chats += 1
                continue
            title = str(entry.get("title") or chat_data.get("title") or "Untitled")
            archived = (
                True if force_archived else _strict_bool(entry.get("archived"), False)
            )
            pinned = _strict_bool(entry.get("pinned"), False) and not archived
            created_at = _unix_ts_to_utc(
                entry.get("created_at") or chat_data.get("timestamp")
            )
            updated_at = (
                _unix_ts_to_utc(entry.get("updated_at"))
                if entry.get("updated_at")
                else created_at
            )

            # Resolve messages ------------------------------------------------
            history = chat_data.get("history") or {}
            if not isinstance(history, dict):
                history = {}
            history_messages = history.get("messages") or {}
            current_id = history.get("currentId")

            # Some exports also have a flat ``chat.messages`` array
            flat_messages = chat_data.get("messages") or []

            if history_messages:
                branches, branch_skipped_messages = _build_message_branches(
                    history_messages, current_id
                )
                skipped_messages += branch_skipped_messages
            elif isinstance(flat_messages, list) and flat_messages:
                branches = [
                    {
                        "message_ids": [],
                        "messages": list(flat_messages),
                        "leaf_id": None,
                        "is_current": False,
                    }
                ]
            else:
                logger.info("Skipping chat '%s' – no messages found.", title)
                skipped_chats += 1
                continue

            if not branches:
                skipped_chats += 1
                continue

            branch_count = len(branches)
            source_chat_id = entry.get("id") or chat_data.get("id")

            for branch_index, branch in enumerate(branches, start=1):
                ordered = branch.get("messages") or []
                if not ordered:
                    skipped_branches += 1
                    continue

                # Convert the whole branch before creating ORM rows.  Any
                # unexpected conversion failure therefore cannot persist a
                # chat with only a prefix of its transcript.
                prepared_messages, branch_skipped_messages = _prepare_branch_messages(
                    ordered
                )
                skipped_messages += branch_skipped_messages
                if not prepared_messages:
                    skipped_branches += 1
                    continue

                branch_title = title
                if branch_count > 1 and branch_index > 1:
                    branch_title = f"{title} (Branch {branch_index})"
                # A pinned Open WebUI conversation maps to the selected/current
                # branch only. Pinning every alternate would unexpectedly fill
                # the user's pinned list with variants of the same source chat.
                pinned_position = (
                    next_pinned_position if pinned and branch_index == 1 else None
                )

                chat_meta = {"status": "normal", "imported_from": "openwebui"}
                if source_chat_id:
                    chat_meta["openwebui_chat_id"] = str(source_chat_id)
                if history_messages:
                    chat_meta["openwebui_branch"] = {
                        "index": branch_index,
                        "count": branch_count,
                        "leaf_id": branch.get("leaf_id"),
                        "current": bool(branch.get("is_current")),
                    }

                try:
                    # A savepoint isolates database failures to this branch;
                    # successful earlier branches remain usable and failed
                    # ones cannot leak partial rows into the final commit.
                    with db.begin_nested():
                        new_chat = Chats(
                            id=str(uuid.uuid4()),
                            user_id=user_id,
                            title=branch_title,
                            archived=archived,
                            pinned_position=pinned_position,
                            meta=chat_meta,
                            created_at=created_at,
                            last_updated_at=updated_at,
                        )
                        db.add(new_chat)
                        # ChatMessages has a database foreign key but no ORM
                        # relationship to teach SQLAlchemy the dependency
                        # order.  Flush the parent explicitly so PostgreSQL
                        # never attempts the child INSERT first.
                        db.flush()

                        last_user_msg_id: str | None = None
                        new_messages: list[ChatMessages] = []
                        for prepared in prepared_messages:
                            role = prepared["role"]
                            new_msg = ChatMessages(
                                id=str(uuid.uuid4()),
                                chat_id=new_chat.id,
                                model_id=prepared["model_id"],
                                role=role,
                                content=prepared["content"],
                                reference_id=last_user_msg_id
                                if role == "assistant"
                                else None,
                                thinking=prepared["thinking"],
                                generation={"generation_number": 1},
                                retry_count=0,
                                created_at=prepared["created_at"],
                            )
                            db.add(new_msg)
                            new_messages.append(new_msg)

                            if role == "user":
                                last_user_msg_id = new_msg.id

                        db.flush()
                except Exception:
                    logger.exception(
                        "Failed to import branch %d of Open WebUI chat '%s', skipping.",
                        branch_index,
                        title,
                    )
                    skipped_branches += 1
                    continue

                imported_messages += len(new_messages)
                imported_chats += 1
                imported_entry_branches += 1
                if pinned_position is not None:
                    next_pinned_position += 1

        except Exception:
            logger.exception("Failed to import chat entry, skipping.")
            if imported_entry_branches == 0:
                skipped_chats += 1
            continue

        if imported_entry_branches == 0:
            skipped_chats += 1
        else:
            # Count every successfully imported conversation after the first
            # as an alternate branch, even if an earlier branch failed.
            imported_branches += max(0, imported_entry_branches - 1)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "imported_chats": imported_chats,
        "imported_messages": imported_messages,
        "imported_branches": imported_branches,
        "skipped_chats": skipped_chats,
        "skipped_branches": skipped_branches,
        "skipped_messages": skipped_messages,
    }


def _parse_users_csv(csv_text: str) -> dict[str, str]:
    """
    Parse the Open WebUI users CSV and return a mapping of
    ``{owui_user_id: email}`` (all emails lowercased).

    Expected CSV columns: id, name, email, role
    """
    # Excel and several browser CSV exporters prefix UTF-8 files with a BOM.
    # Removing it keeps the first header named ``id`` instead of ``\ufeffid``.
    reader = csv.DictReader(io.StringIO(str(csv_text or "").lstrip("\ufeff")))
    mapping: dict[str, str] = {}
    for row in reader:
        owui_id = (row.get("id") or "").strip()
        email = (row.get("email") or "").strip().lower()
        if owui_id and email:
            mapping[owui_id] = email
    return mapping


def import_openwebui_chats_bulk(db, users_csv: str, chats: list[dict]) -> dict:
    """
    Bulk-import Open WebUI chats for all users.

    *users_csv* is the raw CSV text with ``id,name,email,role`` columns.
    *chats* is the JSON array of all-chats export entries (each has a
    ``user_id`` field referencing the Open WebUI user id).

    The function:
    1. Parses the CSV to build ``owui_id → email``.
    2. Looks up each email in the local DB.
    3. Groups chats by their ``user_id``.
    4. Imports each group using :func:`import_openwebui_chats`.

    Users whose email is not found locally are skipped.
    """
    owui_id_to_email = _parse_users_csv(users_csv)
    if not owui_id_to_email:
        raise ValueError("No valid user rows found in the CSV file.")

    # Build local email → user_id lookup (only for emails present in CSV)
    unique_emails = set(owui_id_to_email.values())
    local_users = (
        db.query(User.id, User.email).filter(User.email.in_(unique_emails)).all()
    )
    email_to_local_id: dict[str, str] = {u.email.lower(): u.id for u in local_users}

    # Build owui_id → local_user_id
    owui_to_local: dict[str, str] = {}
    skipped_emails: set[str] = set()
    for owui_id, email in owui_id_to_email.items():
        local_id = email_to_local_id.get(email)
        if local_id:
            owui_to_local[owui_id] = local_id
        else:
            skipped_emails.add(email)

    if skipped_emails:
        logger.info(
            "Bulk import: %d email(s) not found locally, skipping: %s",
            len(skipped_emails),
            ", ".join(sorted(skipped_emails)),
        )

    # Group chats by their owui user_id
    chats_by_owui_user: dict[str, list[dict]] = defaultdict(list)
    skipped_chats_no_user = 0
    for entry in chats:
        if not isinstance(entry, dict):
            skipped_chats_no_user += 1
            continue
        owui_uid = str(entry.get("user_id") or "").strip()
        if not owui_uid or owui_uid not in owui_to_local:
            skipped_chats_no_user += 1
            continue
        chats_by_owui_user[owui_uid].append(entry)

    # Import per local user
    total_imported_chats = 0
    total_imported_messages = 0
    total_imported_branches = 0
    total_skipped_chats = skipped_chats_no_user
    total_skipped_branches = 0
    total_skipped_messages = 0

    for owui_uid, user_chats in chats_by_owui_user.items():
        local_uid = owui_to_local[owui_uid]
        result = import_openwebui_chats(db, local_uid, user_chats)
        total_imported_chats += result["imported_chats"]
        total_imported_messages += result["imported_messages"]
        total_imported_branches += result.get("imported_branches", 0)
        total_skipped_chats += result["skipped_chats"]
        total_skipped_branches += result.get("skipped_branches", 0)
        total_skipped_messages += result.get("skipped_messages", 0)

    return {
        "imported_chats": total_imported_chats,
        "imported_messages": total_imported_messages,
        "imported_branches": total_imported_branches,
        "skipped_chats": total_skipped_chats,
        "skipped_branches": total_skipped_branches,
        "skipped_messages": total_skipped_messages,
        "matched_users": len(owui_to_local),
        "skipped_users": len(skipped_emails),
    }
