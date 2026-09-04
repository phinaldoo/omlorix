from datetime import datetime, timezone, timedelta
from contextlib import suppress
from pydantic import ValidationError
from fastapi import HTTPException
from typing import Optional, Callable
from copy import deepcopy
from sqlalchemy import func, or_
import threading
import logging
import hashlib
import queue
import uuid
import json
import time

from app.database import SessionLocal
from app.agents.utils import resolve_selected_model_for_user
from app.auth.utils import hash_password, verify_password
from app.auth.token import check_user_by_token
from app.chats.models import (
    Chats, 
    apply_chat_unread_state,
    record_successful_generation_completion,
    _cleanup_chat_after_empty_transcript,
    create_chat, 
    get_chat,
    get_chats,
    get_visible_chats_query,
    is_chat_hidden_from_default_list,
    ensure_chat_sendable,
    ChatMessages, 
    clone_chat_message_for_new_chat,
    create_chat_message,
    get_chat_messages as db_get_chat_messages
)
from app.chats.schemas import (
    ChatMessage,
    RetryGuidance,
    RetryGuidanceMode,
    RetryGuidancePreset,
)
from app.tools.subagents.schemas import SUBAGENT_RUNTIME_TARGETS_SETTING
from app.utils.utils import sanitize_chat_text
from app.chats.streaming import stream_hub, cancel_registry
from app.utils.background import title_generation_executor
from app.files.access import accessible_files_query
from app.files.models import Files
from app.files.utils import get_file_info
from app.groups.init import get_group_setting_value, get_user_group_setting_value
from app.users.roles import is_admin_role
from app.llm.models import (
    RATE_LIMIT_ADMISSION_COMPLETED,
    RATE_LIMIT_ADMISSION_FAILED,
    RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
    RATE_LIMIT_ADMISSION_ACTION_REGENERATE,
    RATE_LIMIT_BLOCK_REASON_IN_FLIGHT,
    RATE_LIMIT_QUOTA_UNIT_REQUESTS,
    Models,
    admit_user_rate_limit,
    check_user_rate_limit as _legacy_check_user_rate_limit,
    finalize_rate_limit_admission,
    get_llm_provider,
    reset_current_rate_limit_admission_context,
    set_current_rate_limit_admission_context,
)
from app.llm.system_instruction.title import get_title_generation_prompt
from app.llm.utils import ensure_user_access_to_model
from app.llm.google_aistudio.utils import aistudio_chat
from app.llm.ollama.utils import ollama_chat
from app.llm.openai.utils import openai_chat
from app.llm.openai_chat_completions.utils import openai_chat_completions_chat
from app.llm.openrouter.utils import openrouter_chat
from app.llm.anthropic.utils import anthropic_chat
from app.llm.helper import coerce_allow_custom_flag
from app.llm.metadata import resolve_model_metadata_id
from app.llm.provider_request import (
    ProviderRequest,
    REQUEST_TYPE_CHAT,
    REQUEST_TYPE_TITLE_GENERATION,
    call_provider_chat,
    call_provider_title_generation,
)
from app.llm.schemas import normalize_provider_value
from app.logging.privacy import (
    exception_metadata,
    redacted_debug_logging_enabled,
    safe_count,
    stream_line_metadata,
)
from app.llm.system_instruction.personality import get_user_personality_system_instruction_section
from app.network.policy import (
    OutboundRequestBlockedError,
    assert_llm_config_allowed,
    assert_llm_provider_allowed,
)
from app.skills.models import get_skill_content_for_user, get_skill_file_descriptors_by_category_for_user
from app.telemetry.metrics import record_llm_request_metric
from app.prompts.models import get_prompt_content_for_user
from app.settings.utils import get_public_url
from app.redis_client import get_redis_client
from app.chats.share_tokens import (
    create_chat_share_access_token,
    _share_password_fingerprint,
    verify_chat_share_access_token,
)


DEFAULT_CHAT_PAGE_LIMIT = 20
MAX_CHAT_PAGE_LIMIT = 100
MAX_PINNED_CHAT_LIST_LIMIT = 100


logger = logging.getLogger(__name__)

_CHAT_STREAM_DEBUG_FLAG = "OMLORIX_LOG_REDACTED_CHAT_STREAMS"


def resolve_chat_model_for_user(db, *, user_id: str, model_id: str):
    """Resolve a chat selection and enforce every runtime access gate."""
    resolved_selection = resolve_selected_model_for_user(
        db,
        user_id=user_id,
        model_id=model_id,
    )
    selected_model_id = resolved_selection.selected_model_id
    base_model_id = resolved_selection.base_model.id
    ensure_user_access_to_model(user_id, selected_model_id, db)
    if base_model_id != selected_model_id:
        # Agent access and group policy are checked against the selected agent,
        # while provider availability belongs to its backing model.
        ensure_user_access_to_model(user_id, base_model_id, db)
    return resolved_selection


class _IncompleteProviderStreamError(RuntimeError):
    """Raised when a provider closes without any application terminal event."""


def _is_successful_generation_done_line(line: str) -> bool:
    """Return whether a provider stream line represents successful completion."""
    try:
        payload = json.loads(str(line or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("t") != "d":
        return False

    terminal_code = payload.get("d")
    metadata = payload.get("c") if isinstance(payload.get("c"), dict) else {}
    terminal_status = str(metadata.get("status") or "").strip().lower()
    if terminal_code == "c" or terminal_status in {"cancelled", "canceled", "error", "failed"}:
        return False
    return terminal_code in {None, "f"}


def _is_generation_terminal_line(line: str) -> bool:
    """Return whether a provider line explicitly completes, cancels, or fails."""
    try:
        payload = json.loads(str(line or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("t") == "e":
        return True
    if payload.get("t") != "d":
        return False

    terminal_code = payload.get("d")
    metadata = payload.get("c") if isinstance(payload.get("c"), dict) else {}
    terminal_status = str(metadata.get("status") or "").strip().lower()
    return (
        _is_successful_generation_done_line(line)
        or terminal_code == "c"
        or terminal_status in {"cancelled", "canceled", "error", "failed", "failure"}
    )


def _require_provider_stream_terminal(upstream, generation_id: str | None):
    """Forward provider lines and reject a non-cancelled stream without a terminal event."""
    terminal_seen = False
    for line in upstream:
        terminal_seen = terminal_seen or _is_generation_terminal_line(line)
        yield line
    if not terminal_seen and not _is_generation_cancelled(generation_id):
        raise _IncompleteProviderStreamError(
            "Provider stream ended without a terminal event."
        )


def _build_generation_error_line(
    exc: Exception,
    *,
    user_role: str | None,
    byok: dict | None,
) -> str:
    """Build a safe stream error, translating incomplete transports in the UI."""
    is_admin = is_admin_role(user_role)
    incomplete_stream = isinstance(exc, _IncompleteProviderStreamError)
    error_message = (
        "Connection interrupted. Please try again."
        if incomplete_stream
        else (
            str(exc)
            if is_admin or bool(byok)
            else "An error occurred during generation. Please try again."
        )
    )
    payload = {
        "t": "e",
        "d": error_message,
        "admin_detail": str(exc) if is_admin else None,
    }
    if incomplete_stream:
        payload["i18n_key"] = "chat_connection_interrupted_retry"
    return json.dumps(payload)


def _record_completion_before_stream_publish(db, chat_id: str, generation_id: str, line: str) -> None:
    """Persist unread-version state before clients can observe a done event."""
    if not _is_successful_generation_done_line(line):
        return
    record_successful_generation_completion(db, chat_id, generation_id)


def _is_generation_cancelled(generation_id: str | None) -> bool:
    """Best-effort cancellation check used by non-critical cleanup work."""
    if not generation_id:
        return False
    try:
        checker = getattr(cancel_registry, "is_cancelled", None)
        return bool(callable(checker) and checker(generation_id))
    except Exception:
        return False


def _retry_guidance_log_metadata(retry_guidance: RetryGuidance | None) -> dict:
    if not isinstance(retry_guidance, RetryGuidance):
        return {"mode": None, "preset": None, "custom_instruction_length": 0}
    return {
        "mode": retry_guidance.mode.value if retry_guidance.mode else None,
        "preset": retry_guidance.preset.value if retry_guidance.preset else None,
        "custom_instruction_length": len(retry_guidance.instruction or ""),
    }


TEMP_CHAT_ATTACHMENT_FIELDS = ("images", "videos", "audios", "documents", "youtube", "sources")
MAX_CHAT_REFERENCE_CHATS = 5
MAX_CHAT_REFERENCE_CONTEXT_CHARS = 120000
MAX_TRACKED_ARTIFACT_IDS = 100
MAX_CONTEXT_SELECTION_ITEMS = 20
MAX_SKILL_INSTRUCTION_CHARS = 96_000
MAX_PROMPT_LIBRARY_CHARS = 32_000
MAX_SKILL_ATTACHMENT_FILES = 20
CHAT_REFERENCE_BLOCK_FIELD = "chat_references"
CHAT_REFERENCE_DETAIL_INVALID = "chat_reference_invalid"
CHAT_REFERENCE_DETAIL_OVERSIZE = "chat_reference_context_too_large"
RETRY_GUIDANCE_PRESET_INSTRUCTIONS = {
    RetryGuidancePreset.try_again.value: (
        "Answer the same last user request again with a fresh attempt. "
        "Do not mention that this is a retry."
    ),
    RetryGuidancePreset.add_details.value: (
        "Answer the same last user request again, but provide a more complete response. "
        "Add more detail, explanation, examples, and useful edge cases when appropriate."
    ),
    RetryGuidancePreset.more_concise.value: (
        "Answer the same last user request again, but make the response significantly more concise. "
        "Keep the core answer, remove repetition, and prefer shorter wording."
    ),
}


def _assert_generation_provider_allowed(
    db,
    *,
    provider: str | None,
    db_model,
    byok: dict | None,
    feature: str,
) -> None:
    """Validate that the LLM provider is allowed for the given feature, raising an HTTP exception if blocked."""
    try:
        if byok:
            assert_llm_config_allowed(
                db,
                provider_type=str(provider or byok.get("provider") or "").strip(),
                settings=byok,
                feature=feature,
                require_private_allowlist=True,
            )
            return

        provider_id = getattr(db_model, "provider_id", None)
        if provider_id:
            provider_row = get_llm_provider(db, provider_id)
            assert_llm_provider_allowed(db, provider_row, feature=feature)
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc


def _build_user_rate_limit_detail(rate_limit_result: dict, model) -> dict:
    """Build a detailed error dict for a user rate-limit hit, including a human-readable message."""
    period_labels = {"day": "daily", "week": "weekly", "month": "monthly"}
    period_key = str(rate_limit_result.get("period") or "").strip()
    period_label = period_labels.get(period_key, period_key or "active")
    rate_limit_timezone = str(rate_limit_result.get("timezone") or "UTC").strip() or "UTC"
    quota_unit = str(rate_limit_result.get("quota_unit") or RATE_LIMIT_QUOTA_UNIT_REQUESTS).strip().lower()
    quota_value = int(rate_limit_result.get("quota_value") or rate_limit_result.get("max_requests") or 0)
    resets_at = str(rate_limit_result.get("resets_at") or "").strip()
    model_id = str(getattr(model, "id", "") or "").strip()
    model_name = str(getattr(model, "name", "") or model_id or "this model").strip()
    raw_current_usage = rate_limit_result.get("current_usage")
    if raw_current_usage is None:
        raw_current_usage = rate_limit_result.get("current_count")
    current_usage = int(raw_current_usage if raw_current_usage is not None else (quota_value or 0))
    quota_unit_label = "request" if quota_unit == RATE_LIMIT_QUOTA_UNIT_REQUESTS else "token"
    quota_unit_label_plural = quota_unit_label if quota_value == 1 else f"{quota_unit_label}s"
    block_reason = str(rate_limit_result.get("block_reason") or "").strip().lower()
    logger.warning(
        "[chat-send] user model rate limit hit user_model=%s model_name=%s current_usage=%s quota_value=%s quota_unit=%s period=%s resets_at=%s rate_limit_id=%s",
        model_id,
        model_name,
        current_usage,
        quota_value,
        quota_unit,
        period_key,
        resets_at,
        str(rate_limit_result.get("rate_limit_id") or "").strip(),
    )

    if block_reason == RATE_LIMIT_BLOCK_REASON_IN_FLIGHT:
        message = (
            f"A generation for {model_name} is already using this {period_label} token budget. "
            f"Wait for it to finish, then try again."
        )
    else:
        message = (
            f"You have reached your {period_label} {quota_unit_label} limit of "
            f"{quota_value} {quota_unit_label_plural} for {model_name}. "
            f"Your limit resets at {resets_at} ({rate_limit_timezone}). "
            f"Try switching to a different model."
        )

    return {
        "code": "user_model_rate_limited",
        "message": message,
        "rate_limit_id": str(rate_limit_result.get("rate_limit_id") or "").strip(),
        "rate_limit_name": str(rate_limit_result.get("name") or "").strip(),
        "block_reason": block_reason,
        "period": period_key,
        "period_label": period_label,
        "timezone": rate_limit_timezone,
        "quota_unit": quota_unit,
        "quota_value": quota_value,
        "current_usage": current_usage,
        "remaining_usage": int(rate_limit_result.get("remaining_usage") or 0),
        "max_requests": quota_value if quota_unit == RATE_LIMIT_QUOTA_UNIT_REQUESTS else None,
        "current_count": current_usage if quota_unit == RATE_LIMIT_QUOTA_UNIT_REQUESTS else None,
        "resets_at": resets_at,
        "model_id": model_id,
        "model_name": model_name,
    }


def check_user_rate_limit(db, user_id: str, group_id: str | None, model_id: str) -> dict | None:
    """Backward-compatible shim for callers patching chats.utils.check_user_rate_limit."""
    return _legacy_check_user_rate_limit(db, user_id=user_id, group_id=group_id, model_id=model_id)


def _admit_rate_limited_chat_action(
    db,
    *,
    user_id: str,
    group_id: str,
    model,
    action_type: str,
    chat_id: str | None = None,
    user_message_id: str | None = None,
):
    legacy_rate_limit_result = check_user_rate_limit(
        db,
        user_id=user_id,
        group_id=group_id,
        model_id=str(getattr(model, "id", "") or "").strip(),
    )
    if isinstance(legacy_rate_limit_result, dict):
        raise HTTPException(
            status_code=429,
            detail=_build_user_rate_limit_detail(legacy_rate_limit_result, model),
        )

    db_type_module = str(getattr(type(db), "__module__", "") or "")
    if not db_type_module.startswith("sqlalchemy"):
        return None

    try:
        admission_result = admit_user_rate_limit(
            db,
            user_id=user_id,
            group_id=group_id,
            model_id=str(getattr(model, "id", "") or "").strip(),
            action_type=action_type,
            chat_id=chat_id,
            user_message_id=user_message_id,
        )
        if isinstance(admission_result, dict) and admission_result.get("blocked"):
            raise HTTPException(
                status_code=429,
                detail=_build_user_rate_limit_detail(admission_result, model),
            )
        return admission_result
    except TypeError as exc:
        if "not iterable" in str(exc):
            logger.debug("Skipping rate-limit admission due to non-queryable db session: %s", exc)
            return None
        raise


def _ensure_group_chat_interaction_allowed(
    group_id: str,
    setting_key: str,
    db,
    detail: str,
) -> None:
    """Raise 403 if the given group chat interaction setting is disabled."""
    if not bool(get_group_setting_value(group_id, "chat", setting_key, db)):
        raise HTTPException(status_code=403, detail=detail)


def _filter_latest_assistant_versions(messages):
    """Return history where each assistant reference_id keeps only its latest version."""

    if not messages:
        return messages

    def _get_attr(msg, attr):
        if isinstance(msg, dict):
            return msg.get(attr)
        return getattr(msg, attr, None)

    latest_ids: dict[str, str] = {}
    for msg in messages:
        if (_get_attr(msg, "role") == "assistant"):
            ref_id = _get_attr(msg, "reference_id")
            msg_id = _get_attr(msg, "id")
            if ref_id and msg_id:
                latest_ids[ref_id] = msg_id

    if not latest_ids:
        return messages

    filtered = []
    for msg in messages:
        role = _get_attr(msg, "role")
        if role != "assistant":
            filtered.append(msg)
            continue

        ref_id = _get_attr(msg, "reference_id")
        if not ref_id:
            filtered.append(msg)
            continue

        msg_id = _get_attr(msg, "id")
        if msg_id and latest_ids.get(ref_id) != msg_id:
            continue
        filtered.append(msg)

    return filtered


def _metric_positive_int(value) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _metric_model_name(db_model, model_id: str | None, byok: dict | None = None) -> str:
    if byok:
        return "byok"
    return str(getattr(db_model, "model_name", None) or model_id or getattr(db_model, "id", None) or "unknown")


def _metric_provider_name(provider, byok: dict | None = None) -> str:
    if byok:
        return "byok"
    return str(provider or "unknown")


def _extract_llm_metric_stream_state(line: str) -> tuple[bool, str | None, int, int]:
    """Return success hint, error type, input tokens, and output tokens from a stream line."""
    try:
        event = json.loads(str(line).strip())
    except Exception:
        return True, None, 0, 0
    if not isinstance(event, dict):
        return True, None, 0, 0

    event_type = event.get("t") or event.get("type")
    if event_type == "e":
        return False, "ProviderErrorEvent", 0, 0

    completion = event.get("c")
    if event_type == "d" and isinstance(completion, dict):
        status = str(completion.get("status") or "").strip().lower()
        if status in {"error", "failed", "failure"}:
            return False, "ProviderErrorStatus", 0, 0

    if event_type == "d" and event.get("d") == "f" and isinstance(completion, dict):
        return (
            True,
            None,
            _metric_positive_int(completion.get("input_tokens")),
            _metric_positive_int(completion.get("output_tokens")),
        )
    return True, None, 0, 0


def _normalize_skill_ids(skill_id: str | None = None, skill_ids: list[str] | None = None) -> list[str]:
    """Merge and deduplicate a single skill_id and a list of skill_ids."""
    normalized: list[str] = []
    seen: set[str] = set()

    def _push(raw_value) -> None:
        if len(normalized) >= MAX_CONTEXT_SELECTION_ITEMS:
            return
        value = str(raw_value or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        normalized.append(value)

    if skill_id is not None:
        _push(skill_id)
    if isinstance(skill_ids, list):
        for item in skill_ids:
            _push(item)

    return normalized


def _extract_model_skill_ids(model_settings: dict | None) -> list[str]:
    """Extract skill IDs from a model settings dict."""
    settings = model_settings if isinstance(model_settings, dict) else {}
    model_skill_ids = settings.get("skill_ids")
    if isinstance(model_skill_ids, list):
        return _normalize_skill_ids(skill_ids=model_skill_ids)
    return _normalize_skill_ids(skill_id=settings.get("skill_id"))


def _resolve_trusted_admin_skill_ids(
    *,
    model_skill_ids: list[str] | None,
    agent_skill_ids: list[str] | None = None,
) -> list[str]:
    """Return admin skill IDs that may bypass per-user admin-skill ACLs.

    Only server/operator-controlled model settings are trusted here. Agent skill
    IDs can come from user-created shared agents, so they must stay in the
    effective skill list and go through the current user's normal skill access
    checks.
    """
    return _normalize_skill_ids(skill_ids=model_skill_ids)


FIXED_MODEL_SKILL_OVERRIDE_ERROR = "This model has a fixed skill. Remove selected skills and try again."


def _resolve_generation_skill_ids(
    *,
    requested_skill_ids: list[str] | None,
    model_skill_ids: list[str] | None,
    agent_skill_ids: list[str] | None = None,
) -> list[str]:
    """Resolve skill IDs while enforcing fixed model skills as a non-overridable policy boundary."""
    normalized_requested_skill_ids = _normalize_skill_ids(skill_ids=requested_skill_ids)
    normalized_model_skill_ids = _normalize_skill_ids(skill_ids=model_skill_ids)
    normalized_agent_skill_ids = _normalize_skill_ids(skill_ids=agent_skill_ids)

    if normalized_model_skill_ids and normalized_requested_skill_ids:
        raise HTTPException(status_code=400, detail=FIXED_MODEL_SKILL_OVERRIDE_ERROR)

    return _normalize_skill_ids(
        skill_ids=[
            *normalized_agent_skill_ids,
            *normalized_model_skill_ids,
            *normalized_requested_skill_ids,
        ],
    )


def _fit_instruction_section(
    header: str,
    content: str,
    *,
    available_chars: int,
) -> tuple[str, bool]:
    """Fit one instruction source into a deterministic prompt budget."""
    if available_chars <= 0:
        return "", True
    section = f"{header}\n{content}"
    if len(section) <= available_chars:
        return section, False
    marker = "\n[Content truncated to the configured context budget.]"
    if available_chars <= len(marker):
        return section[:available_chars], True
    return f"{section[: available_chars - len(marker)]}{marker}", True


def _compose_skill_content(
    db,
    user_id: str,
    resolved_skill_ids: list[str],
    *,
    trusted_admin_skill_ids: list[str] | None = None,
) -> str | None:
    """Compose skill instruction text from resolved skill IDs for system prompt injection."""
    if not resolved_skill_ids:
        return None

    chunks: list[str] = []
    used_chars = 0
    for resolved_skill_id in resolved_skill_ids:
        # Only the skill's authored instructions belong in the system prompt.
        # Its files are added separately through the provider attachment path,
        # which avoids sending text-readable files twice.
        content = get_skill_content_for_user(
            db,
            user_id,
            resolved_skill_id,
            trusted_admin_skill_ids=trusted_admin_skill_ids,
        )
        if not content:
            continue
        separator_chars = 2 if chunks else 0
        available_chars = MAX_SKILL_INSTRUCTION_CHARS - used_chars - separator_chars
        chunk, was_truncated = _fit_instruction_section(
            f"[Skill {len(chunks) + 1}]",
            str(content),
            available_chars=available_chars,
        )
        if not chunk:
            break
        chunks.append(chunk)
        used_chars += separator_chars + len(chunk)
        if was_truncated:
            break

    return "\n\n".join(chunks) if chunks else None


def _collect_skill_file_attachment_ids(
    db,
    user_id: str,
    resolved_skill_ids: list[str],
    *,
    trusted_admin_skill_ids: list[str] | None = None,
) -> dict[str, list[str]]:
    """Collect file attachment IDs from skills, grouped by media type."""
    grouped: dict[str, list[str]] = {
        "images": [],
        "videos": [],
        "audios": [],
        "documents": [],
    }
    if not resolved_skill_ids:
        return grouped

    field_map = {
        "image": "images",
        "video": "videos",
        "audio": "audios",
        "document": "documents",
    }

    seen_file_ids: set[str] = set()
    for resolved_skill_id in resolved_skill_ids:
        if len(seen_file_ids) >= MAX_SKILL_ATTACHMENT_FILES:
            break
        by_category = get_skill_file_descriptors_by_category_for_user(
            db,
            user_id,
            resolved_skill_id,
            trusted_admin_skill_ids=trusted_admin_skill_ids,
        )
        if not isinstance(by_category, dict):
            continue
        for source_key, target_key in field_map.items():
            if len(seen_file_ids) >= MAX_SKILL_ATTACHMENT_FILES:
                break
            for file_id in _normalize_attachment_ids(by_category.get(source_key)):
                if len(seen_file_ids) >= MAX_SKILL_ATTACHMENT_FILES:
                    break
                if file_id in seen_file_ids:
                    continue
                seen_file_ids.add(file_id)
                grouped[target_key].append(file_id)
                if len(seen_file_ids) >= MAX_SKILL_ATTACHMENT_FILES:
                    break

    return grouped


def _normalize_prompt_ids(prompt_ids: list[str] | None = None) -> list[str]:
    """Deduplicate and normalize a list of prompt IDs."""
    normalized: list[str] = []
    seen: set[str] = set()

    if not isinstance(prompt_ids, list):
        return normalized

    for item in prompt_ids:
        if len(normalized) >= MAX_CONTEXT_SELECTION_ITEMS:
            break
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _normalize_attachment_ids(file_ids: list[str] | None = None) -> list[str]:
    """Deduplicate and normalize a list of file attachment IDs."""
    normalized: list[str] = []
    seen: set[str] = set()
    if not isinstance(file_ids, list):
        return normalized
    for item in file_ids:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _merge_attachment_ids(file_ids: list[str] | None, extra_ids: list[str] | None) -> list[str]:
    """Merge two lists of attachment IDs into a single deduplicated list."""
    return _normalize_attachment_ids([*(_normalize_attachment_ids(file_ids)), *(_normalize_attachment_ids(extra_ids))])


def _build_agent_selection_metadata(
    *,
    selected_agent_id: str | None = None,
    resolved_base_model_id: str | None = None,
) -> dict[str, str]:
    """Build metadata dict for agent/model selection tracking."""
    metadata: dict[str, str] = {}
    if selected_agent_id:
        metadata["agent_id"] = selected_agent_id
    if resolved_base_model_id:
        metadata["base_model_id"] = resolved_base_model_id
    return metadata


def _build_provider_settings_override(
    custom_settings: dict | None,
    *,
    allow_custom_generation_parameter: bool,
    subagent_targets: list[dict[str, str]] | None,
) -> dict | None:
    """Combine permitted user overrides with server-authorized runtime policy.

    The Subagent selection is intentionally added after request schema and
    authorization checks. It must survive even when the selected model does
    not permit ordinary custom generation parameters, because it only narrows
    tool authority and never changes provider sampling behavior.
    """
    override = (
        deepcopy(custom_settings)
        if allow_custom_generation_parameter and isinstance(custom_settings, dict)
        else {}
    )
    override.pop(SUBAGENT_RUNTIME_TARGETS_SETTING, None)
    if subagent_targets is not None:
        override[SUBAGENT_RUNTIME_TARGETS_SETTING] = deepcopy(subagent_targets)
    return override or None


def _merge_attachment_ids_into_content(
    raw_content,
    *,
    image_ids: list[str] | None = None,
    video_ids: list[str] | None = None,
    audio_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
):
    """Merge attachment IDs into existing message content blocks."""
    decoded = _decode_jsonish(raw_content)
    if not isinstance(decoded, list):
        return raw_content

    merged_blocks = deepcopy(decoded)
    primary_block = next((block for block in merged_blocks if isinstance(block, dict)), None)
    if not primary_block:
        return raw_content

    attachment_map = {
        "images": image_ids,
        "videos": video_ids,
        "audios": audio_ids,
        "documents": document_ids,
    }
    for field, values in attachment_map.items():
        merged = _merge_attachment_ids(primary_block.get(field), values)
        if merged:
            primary_block[field] = merged

    return merged_blocks


def _compose_prompt_content(db, user_id: str, resolved_prompt_ids: list[str]) -> str | None:
    """Compose prompt library content text from resolved prompt IDs."""
    if not resolved_prompt_ids:
        return None

    chunks: list[str] = []
    used_chars = 0
    for resolved_prompt_id in resolved_prompt_ids:
        prompt_payload = get_prompt_content_for_user(db, user_id, resolved_prompt_id)
        if not prompt_payload:
            continue
        title = str(prompt_payload.get("title") or resolved_prompt_id).strip() or resolved_prompt_id
        content = str(prompt_payload.get("content") or "").strip()
        if not content:
            continue
        separator_chars = 2 if chunks else 0
        available_chars = MAX_PROMPT_LIBRARY_CHARS - used_chars - separator_chars
        chunk, was_truncated = _fit_instruction_section(
            f"[Prompt: {title} | ID: {resolved_prompt_id}]",
            content,
            available_chars=available_chars,
        )
        if not chunk:
            break
        chunks.append(chunk)
        used_chars += separator_chars + len(chunk)
        if was_truncated:
            break

    return "\n\n".join(chunks) if chunks else None


def _build_system_instruction_sections(
    *,
    personality_section: dict[str, str] | None = None,
    skill_content: str | None = None,
    agent_instruction: str | None = None,
    retry_guidance: RetryGuidance | None = None,
) -> list[dict[str, str]]:
    """Build a list of system instruction sections for LLM prompting."""
    sections: list[dict[str, str]] = []

    if isinstance(agent_instruction, str) and agent_instruction.strip():
        sections.append(
            {
                "title": "Agent Instructions",
                "content": agent_instruction.strip(),
            }
        )

    if isinstance(personality_section, dict):
        title = str(personality_section.get("title") or "").strip()
        content = str(personality_section.get("content") or "").strip()
        if content:
            sections.append(
                {
                    "title": title or "User Personality Preferences",
                    "content": content,
                }
            )

    if isinstance(skill_content, str) and skill_content.strip():
        sections.append(
            {
                "title": "Skill Instructions",
                "content": skill_content.strip(),
            }
        )

    retry_section = _build_retry_guidance_section(retry_guidance)
    if retry_section:
        sections.append(retry_section)

    return sections


def _join_latest_user_context(*parts: str | None) -> str | None:
    """Join contextual text that should be attached to the latest user prompt."""
    normalized_parts = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    if not normalized_parts:
        return None
    return "\n\n---\n\n".join(normalized_parts)


def _build_retry_guidance_section(retry_guidance: RetryGuidance | None) -> dict[str, str] | None:
    """Build a retry guidance section dict from a RetryGuidance object."""
    if not isinstance(retry_guidance, RetryGuidance):
        return None

    if retry_guidance.mode == RetryGuidanceMode.default:
        return None

    if retry_guidance.mode == RetryGuidanceMode.preset and retry_guidance.preset:
        content = RETRY_GUIDANCE_PRESET_INSTRUCTIONS.get(retry_guidance.preset.value, "").strip()
        if content:
            return {
                "title": "Retry Guidance",
                "content": content,
            }
        return None

    if retry_guidance.mode == RetryGuidanceMode.custom and retry_guidance.instruction:
        return {
            "title": "Retry Guidance",
            "content": (
                "Regenerate the answer for the same last user request, following this user guidance: "
                f"{retry_guidance.instruction.strip()}"
            ),
        }

    return None


def _build_retry_guidance_metadata(retry_guidance: RetryGuidance | None) -> dict[str, str]:
    """Build metadata dict for retry guidance tracking."""
    if not isinstance(retry_guidance, RetryGuidance) or retry_guidance.mode == RetryGuidanceMode.default:
        return {}

    metadata = {
        "regeneration_guidance_mode": retry_guidance.mode.value,
    }
    if retry_guidance.preset:
        metadata["regeneration_guidance_preset"] = retry_guidance.preset.value
    if retry_guidance.instruction:
        metadata["regeneration_guidance_text"] = retry_guidance.instruction
    return metadata


def _build_chat_reference_error_detail(
    code: str,
    message: str,
    chats: list[dict] | None = None,
    **extra,
) -> dict:
    """Build a structured error detail dict for chat reference errors."""
    detail = {
        "code": code,
        "message": message,
    }
    if chats:
        detail["chat_references"] = chats
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            detail[key] = value
    return detail


def _normalize_chat_reference_ids(chat_reference_ids: list[str] | None = None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    if not isinstance(chat_reference_ids, list):
        return normalized

    for item in chat_reference_ids:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _extract_primary_text_from_block(block: dict) -> str:
    if not isinstance(block, dict):
        return ""
    content = block.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [str(item).strip() for item in content if isinstance(item, str) and str(item).strip()]
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        serialized = json.dumps(content, ensure_ascii=False)
        return serialized.strip()
    return ""


def _extract_chat_search_text(raw_content) -> str:
    """Return only user-readable text from stored message content blocks."""
    decoded = _decode_jsonish(raw_content)
    blocks = decoded if isinstance(decoded, list) else [decoded]
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            block_type = str(block.get("type") or "").strip().lower()
            if block_type in {"reasoning", "widget", "tool_call", "tool_call_result"}:
                continue
            content = block.get("content")
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                text = "\n".join(
                    item.strip()
                    for item in content
                    if isinstance(item, str) and item.strip()
                )
            else:
                text = ""
        elif isinstance(block, str):
            text = block.strip()
        else:
            text = ""
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _first_chat_message_preview(raw_content) -> str:
    return _extract_chat_search_text(raw_content)[:140]


def _extract_chat_reference_metadata_from_content(raw_content) -> list[dict]:
    decoded = _decode_jsonish(raw_content)
    if not isinstance(decoded, list):
        return []
    for block in decoded:
        if not isinstance(block, dict):
            continue
        raw_refs = block.get(CHAT_REFERENCE_BLOCK_FIELD)
        if not isinstance(raw_refs, list):
            continue
        references: list[dict] = []
        seen: set[str] = set()
        for item in raw_refs:
            if not isinstance(item, dict):
                continue
            chat_id = str(item.get("chat_id") or item.get("id") or "").strip()
            if not chat_id or chat_id in seen:
                continue
            seen.add(chat_id)
            references.append(
                {
                    "chat_id": chat_id,
                    "title": str(item.get("title") or "Untitled chat").strip() or "Untitled chat",
                    "last_updated_at": item.get("last_updated_at"),
                    "snippet": str(item.get("snippet") or "").strip(),
                    "message_count": int(item.get("message_count") or 0),
                    "estimated_chars": int(item.get("estimated_chars") or 0),
                }
            )
        return references
    return []


def _estimate_chat_reference_stats(messages: list[ChatMessages]) -> tuple[int, int, str]:
    estimated_chars = 0
    snippet = ""
    for message in messages:
        decoded = _decode_jsonish(getattr(message, "content", None))
        if isinstance(decoded, list):
            for block in decoded:
                if isinstance(block, dict):
                    text = _extract_primary_text_from_block(block)
                else:
                    text = str(block or "").strip()
                if text and not snippet:
                    snippet = text[:140]
                estimated_chars += len(text)
        elif isinstance(decoded, dict):
            text = _extract_primary_text_from_block(decoded)
            if text and not snippet:
                snippet = text[:140]
            estimated_chars += len(text)
        elif isinstance(decoded, str):
            text = decoded.strip()
            if text and not snippet:
                snippet = text[:140]
            estimated_chars += len(text)
    return len(messages), estimated_chars, snippet


def _list_attachable_reference_chats(
    user_id: str,
    db,
    project_id: str | None = None,
) -> list[Chats]:
    chats_by_id: dict[str, Chats] = {}
    for chat in get_chats(db, user_id):
        chats_by_id[str(chat.id)] = chat

    normalized_project_id = str(project_id or "").strip()
    if normalized_project_id:
        from app.projects.models import has_project_access

        if has_project_access(db, user_id, normalized_project_id):
            for chat in get_chats(
                db,
                user_id,
                project_id=normalized_project_id,
                include_shared_project=True,
            ):
                chats_by_id[str(chat.id)] = chat

    attachable: list[Chats] = []
    for chat in chats_by_id.values():
        meta = chat.meta if isinstance(chat.meta, dict) else {}
        if meta.get("status") == "temp" or meta.get("shadow_deleted"):
            continue
        if getattr(chat, "archived", False):
            continue
        attachable.append(chat)

    attachable.sort(
        key=lambda chat: getattr(chat, "last_updated_at", None).timestamp() if getattr(chat, "last_updated_at", None) else 0,
        reverse=True,
    )
    return attachable


def list_chat_reference_candidates(
    user_id: str,
    db,
    query: str = "",
    offset: int = 0,
    limit: int = 20,
    project_id: str | None = None,
) -> dict:
    normalized_query = str(query or "").strip().lower()
    bounded_offset = max(0, int(offset or 0))
    bounded_limit = max(1, min(limit, 50))
    all_chats = _list_attachable_reference_chats(user_id, db, project_id=project_id)

    matches: list[dict] = []
    for chat in all_chats:
        messages = db_get_chat_messages(db, chat.id)
        message_count, estimated_chars, snippet = _estimate_chat_reference_stats(messages)
        title = str(getattr(chat, "title", None) or "Untitled chat").strip() or "Untitled chat"
        if normalized_query:
            haystack = f"{title}\n{snippet}".lower()
            if normalized_query not in haystack:
                continue
        matches.append(
            {
                "chat_id": str(chat.id),
                "title": title,
                "last_updated_at": chat.last_updated_at.isoformat() if getattr(chat, "last_updated_at", None) else None,
                "snippet": snippet,
                "message_count": message_count,
                "estimated_chars": estimated_chars,
            }
        )
    items = matches[bounded_offset : bounded_offset + bounded_limit]

    return {
        "items": items,
        "total_count": len(matches),
        "has_more": bounded_offset + len(items) < len(matches),
        "offset": bounded_offset,
        "limit": bounded_limit,
    }


def _extract_chat_reference_text_from_content(raw_content) -> str:
    decoded = _decode_jsonish(raw_content)
    if isinstance(decoded, list):
        parts: list[str] = []
        attachment_counts = {field: 0 for field in ATTACHMENT_FIELDS}
        for block in decoded:
            if isinstance(block, dict):
                block_type = str(block.get("type") or "").lower()
                if block_type in {"widget", "tool_call", "tool_call_result"}:
                    continue
                text = _extract_primary_text_from_block(block)
                if text:
                    parts.append(text)
                for field in ATTACHMENT_FIELDS:
                    values = block.get(field)
                    if isinstance(values, list):
                        attachment_counts[field] += len(values)
            else:
                text = str(block or "").strip()
                if text:
                    parts.append(text)
        attachment_summary = ", ".join(
            f"{count} {field}"
            for field, count in attachment_counts.items()
            if count
        )
        if attachment_summary:
            parts.append(f"[Attachments: {attachment_summary}]")
        return "\n".join(part for part in parts if part).strip()
    if isinstance(decoded, dict):
        return _extract_primary_text_from_block(decoded)
    if isinstance(decoded, str):
        return decoded.strip()
    return ""


def _build_chat_reference_context(
    chat_reference_payload: list[dict],
    db,
) -> str | None:
    if not chat_reference_payload:
        return None

    sections = [
        "The user attached the following chats as additional context. Use them as reference material for this reply.",
    ]
    total_chars = len(sections[0])
    oversize_candidates: list[dict] = []

    for index, item in enumerate(chat_reference_payload, start=1):
        chat_id = item["chat_id"]
        chat = db.query(Chats).filter(Chats.id == chat_id).first()
        if not chat:
            continue
        messages = db_get_chat_messages(db, chat_id)
        lines = [
            f"[Referenced Chat {index}]",
            f"Title: {item['title']}",
        ]
        if item.get("last_updated_at"):
            lines.append(f"Last updated: {item['last_updated_at']}")
        lines.append(f"Message count: {item.get('message_count') or len(messages)}")
        lines.append("Transcript:")

        for message in messages:
            role = str(getattr(message, "role", "message") or "message").strip().title()
            text = _extract_chat_reference_text_from_content(getattr(message, "content", None))
            if not text:
                continue
            lines.append(f"{role}: {text}")

        section = "\n".join(lines).strip()
        section_length = len(section) + 2
        if total_chars + section_length > MAX_CHAT_REFERENCE_CONTEXT_CHARS:
            oversize_candidates.append(
                {
                    "chat_id": item["chat_id"],
                    "title": item["title"],
                    "estimated_chars": item.get("estimated_chars") or section_length,
                    "message_count": item.get("message_count") or len(messages),
                }
            )
            continue
        total_chars += section_length
        sections.append(section)

    if oversize_candidates:
        raise HTTPException(
            status_code=422,
            detail=_build_chat_reference_error_detail(
                CHAT_REFERENCE_DETAIL_OVERSIZE,
                "Attached chat references are too large for this request. Remove one or more chats and try again.",
                chats=oversize_candidates,
                max_chars=MAX_CHAT_REFERENCE_CONTEXT_CHARS,
            ),
        )

    return "\n\n".join(sections)


def resolve_chat_reference_payload(
    user_id: str,
    db,
    chat_reference_ids: list[str] | None = None,
    *,
    current_chat_id: str | None = None,
    project_id: str | None = None,
) -> tuple[list[dict], str | None]:
    normalized_ids = _normalize_chat_reference_ids(chat_reference_ids)
    if not normalized_ids:
        return [], None

    if len(normalized_ids) > MAX_CHAT_REFERENCE_CHATS:
        raise HTTPException(
            status_code=422,
            detail=_build_chat_reference_error_detail(
                CHAT_REFERENCE_DETAIL_INVALID,
                f"You can attach up to {MAX_CHAT_REFERENCE_CHATS} chats as context.",
                max_chat_references=MAX_CHAT_REFERENCE_CHATS,
            ),
        )

    attachable = {
        str(chat.id): chat
        for chat in _list_attachable_reference_chats(user_id, db, project_id=project_id)
    }
    rejected: list[dict] = []
    resolved: list[dict] = []

    for chat_id in normalized_ids:
        if current_chat_id and str(current_chat_id) == chat_id:
            rejected.append({"chat_id": chat_id, "reason": "current_chat"})
            continue
        chat = attachable.get(chat_id)
        if not chat:
            rejected.append({"chat_id": chat_id, "reason": "inaccessible"})
            continue
        messages = db_get_chat_messages(db, chat.id)
        message_count, estimated_chars, snippet = _estimate_chat_reference_stats(messages)
        resolved.append(
            {
                "chat_id": chat_id,
                "title": str(getattr(chat, "title", None) or "Untitled chat").strip() or "Untitled chat",
                "last_updated_at": chat.last_updated_at.isoformat() if getattr(chat, "last_updated_at", None) else None,
                "snippet": snippet,
                "message_count": message_count,
                "estimated_chars": estimated_chars,
            }
        )

    if rejected:
        raise HTTPException(
            status_code=422,
            detail=_build_chat_reference_error_detail(
                CHAT_REFERENCE_DETAIL_INVALID,
                "One or more attached chats are unavailable or cannot be attached as context.",
                chats=rejected,
                max_chat_references=MAX_CHAT_REFERENCE_CHATS,
            ),
        )

    return resolved, _build_chat_reference_context(resolved, db)


def _build_temp_message_meta_payload(message_dict):
    if not isinstance(message_dict, dict):
        return None
    meta_value = message_dict.get("meta")
    meta_payload = meta_value if isinstance(meta_value, dict) else None
    system_instruction = message_dict.get("system_instruction")
    if system_instruction:
        meta_payload = {**meta_payload} if meta_payload else {}
        meta_payload["system_instruction"] = system_instruction
    return meta_payload


def _convert_temp_message_to_blocks(message_dict):
    if not isinstance(message_dict, dict):
        return []
    role = str(message_dict.get("role") or "user")
    raw_content = message_dict.get("content")
    blocks: list[dict] = []
    if isinstance(raw_content, list):
        for block in raw_content:
            if isinstance(block, dict):
                blocks.append(deepcopy(block))
            elif block is not None:
                blocks.append({"type": "content", "content": str(block)})
    elif isinstance(raw_content, dict):
        blocks.append(deepcopy(raw_content))
    else:
        block_type = "user" if role == "user" else ("tool_call_result" if role == "tool" else "content")
        block = {"type": block_type}
        if raw_content not in (None, ""):
            block["content"] = raw_content
        blocks.append(block)
    if not blocks:
        default_type = "user" if role == "user" else ("tool_call_result" if role == "tool" else "content")
        blocks.append({"type": default_type})

    attachments = {
        field: message_dict.get(field)
        for field in TEMP_CHAT_ATTACHMENT_FIELDS
        if message_dict.get(field)
    }
    if isinstance(message_dict.get(CHAT_REFERENCE_BLOCK_FIELD), list) and message_dict.get(CHAT_REFERENCE_BLOCK_FIELD):
        attachments[CHAT_REFERENCE_BLOCK_FIELD] = deepcopy(message_dict[CHAT_REFERENCE_BLOCK_FIELD])
    primary_block = next(
        (
            b for b in blocks
            if isinstance(b, dict) and str(b.get("type") or "").strip().lower() != "reasoning"
        ),
        None,
    )
    if primary_block is None:
        primary_block = next((b for b in blocks if isinstance(b, dict)), None)
    if primary_block:
        for field, value in attachments.items():
            if field not in primary_block:
                primary_block[field] = value
        if role == "tool" and message_dict.get("tool_name"):
            primary_block.setdefault("tool_name", message_dict["tool_name"])
        meta_payload = _build_temp_message_meta_payload(message_dict)
        if meta_payload and "meta" not in primary_block:
            primary_block["meta"] = meta_payload

    thinking = message_dict.get("thinking")
    if thinking:
        has_reasoning = any(isinstance(block, dict) and block.get("type") == "reasoning" for block in blocks)
        if not has_reasoning:
            reasoning_block = {"type": "reasoning", "content": thinking}
            meta_payload = _build_temp_message_meta_payload(message_dict)
            if meta_payload:
                reasoning_block["meta"] = meta_payload
            blocks.insert(0, reasoning_block)
    return blocks


def _parse_temp_chat_history(temp_chat: str | None) -> list[dict]:
    try:
        parsed = json.loads(temp_chat or "[]")
        if not isinstance(parsed, list):
            raise ValueError("temp_chat must be a JSON array of messages")
        for i, message in enumerate(parsed):
            if not isinstance(message, dict):
                raise ValueError(f"temp_chat item at index {i} is not a message-like object")
            try:
                ChatMessage(**message)
            except ValidationError as exc:
                raise ValueError(
                    f"temp_chat item at index {i} does not match ChatMessage schema: {exc.errors()}"
                ) from exc
        return parsed
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid temp_chat format") from exc


def _extract_temp_chat_title(chat_history: list[dict]) -> str:
    def _iter_text_fragments(value):
        if isinstance(value, str):
            text = value.strip()
            if text:
                yield text
            return
        if isinstance(value, dict):
            content = value.get("content")
            if isinstance(content, str):
                text = content.strip()
                if text:
                    yield text
            return
        if isinstance(value, list):
            for item in value:
                yield from _iter_text_fragments(item)

    for message in chat_history:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip() != "user":
            continue
        first_text = next(_iter_text_fragments(message.get("content")), "")
        if first_text:
            return sanitize_chat_text(first_text[:60]) or "Saved chat"

    for message in chat_history:
        if not isinstance(message, dict):
            continue
        first_text = next(_iter_text_fragments(message.get("content")), "")
        if first_text:
            return sanitize_chat_text(first_text[:60]) or "Saved chat"

    return "Saved chat"


def save_temporary_chat(user_id: str, temp_chat: str | None, model_id: str | None, db, project_id: str | None = None):
    parsed_history = _parse_temp_chat_history(temp_chat)
    if not parsed_history:
        raise HTTPException(status_code=400, detail="Temporary chat is empty")

    prepared_messages: list[dict] = []
    original_to_new_ids: dict[str, str] = {}
    for entry in parsed_history:
        blocks = _convert_temp_message_to_blocks(entry)
        has_content = any(
            isinstance(block, dict)
            and (
                block.get("content") not in (None, "")
                or any(block.get(field) for field in TEMP_CHAT_ATTACHMENT_FIELDS)
            )
            for block in blocks
        )
        if not has_content:
            continue

        original_id = str(entry.get("id") or "").strip()
        new_id = str(uuid.uuid4())
        if original_id:
            original_to_new_ids[original_id] = new_id

        retry_count_raw = entry.get("retry_count")
        retry_count = retry_count_raw if isinstance(retry_count_raw, int) and retry_count_raw >= 0 else 0
        prepared_messages.append(
            {
                "original_id": original_id,
                "new_id": new_id,
                "role": str(entry.get("role") or "user"),
                "model_id": str(entry.get("model_id") or model_id or "user"),
                "reference_id": str(entry.get("reference_id") or "").strip(),
                "content": json.dumps(blocks),
                "retry_count": retry_count,
            }
        )

    if not prepared_messages:
        raise HTTPException(status_code=400, detail="Temporary chat is empty")

    referenced_model_ids = {
        prepared["model_id"]
        for prepared in prepared_messages
        if prepared.get("model_id") and prepared["model_id"] != "user"
    }
    for referenced_model_id in referenced_model_ids:
        ensure_user_access_to_model(user_id, referenced_model_id, db)

    now = datetime.now(timezone.utc)
    chat = Chats(
        user_id=user_id,
        title=_extract_temp_chat_title(parsed_history),
        project_id=project_id,
        share=None,
        share_id=None,
        archived=False,
        pinned_position=None,
        meta={"status": "normal"},
        created_at=now,
        last_updated_at=now,
    )
    db.add(chat)
    db.flush()

    last_user_message_id = None
    for index, prepared in enumerate(prepared_messages):
        mapped_reference_id = original_to_new_ids.get(prepared["reference_id"]) if prepared["reference_id"] else None
        if prepared["role"] == "assistant" and not mapped_reference_id:
            mapped_reference_id = last_user_message_id

        message_created_at = now + timedelta(microseconds=index)

        db.add(
            ChatMessages(
                id=prepared["new_id"],
                chat_id=chat.id,
                model_id=prepared["model_id"],
                content=prepared["content"],
                role=prepared["role"],
                reference_id=mapped_reference_id,
                generation={"generation_number": 1},
                retry_count=prepared["retry_count"],
                bookmarked=False,
                created_at=message_created_at,
            )
        )

        if prepared["role"] == "user":
            last_user_message_id = prepared["new_id"]

    chat.last_updated_at = now + timedelta(microseconds=max(len(prepared_messages) - 1, 0))

    db.commit()
    db.refresh(chat)
    return {
        "chat_id": chat.id,
        "title": chat.title,
        "project_id": chat.project_id,
    }


# -------------------
# Chats
# -------------------
def send_message(
    user_id: str,
    group_id: str,
    chat_id: str | None,
    message: str,
    image_ids: list[str] | None,
    video_ids: list[str] | None,
    audio_ids: list[str] | None,
    document_ids: list[str] | None,
    project_id: str | None,
    temp_chat: str | None,
    model_id: str | None,
    byok: dict | None,
    custom_settings,
    db,
    skill_id: str | None = None,
    skill_ids: list[str] | None = None,
    note_ids: list[str] | None = None,
    prompt_ids: list[str] | None = None,
    reference_parts: list[str] | None = None,
    chat_reference_ids: list[str] | None = None,
    user_role: str | None = None,
    generation_id: str | None = None,
    subagent_targets: list[dict[str, str]] | None = None,
):
    # The browser creates this ID before sending so Stop can address the job
    # even while request setup or the first provider event is still pending.
    requested_generation_id = str(generation_id or "").strip() or None
    normalized_chat_id = str(chat_id or "").strip()
    existing_chat_for_send = None
    if normalized_chat_id:
        existing_chat_for_send = _ensure_chat_available_for_send(db, normalized_chat_id, user_id)
        chat_id = normalized_chat_id

    from app.projects.models import ensure_project_access_for_chat_send

    project_id, _ = ensure_project_access_for_chat_send(
        db,
        user_id,
        project_id=project_id,
        chat=existing_chat_for_send,
    )

    db_model = None
    rate_limit_admission = None
    rate_limit_context_token = None
    rate_limit_final_status = RATE_LIMIT_ADMISSION_COMPLETED
    allow_custom_generation_parameter = False
    model_settings: dict = {}
    effective_skill_ids = _normalize_skill_ids(skill_id=skill_id, skill_ids=skill_ids)
    trusted_admin_skill_ids: list[str] = []
    selected_agent_id: str | None = None
    resolved_base_model_id: str | None = None
    agent_instruction: str | None = None
    agent_skill_ids: list[str] = []
    agent_asset_descriptors_by_category: dict[str, list[str]] = {
        "image": [],
        "audio": [],
        "video": [],
        "document": [],
    }
    effective_prompt_ids = _normalize_prompt_ids(prompt_ids=prompt_ids)
    prompts_enabled = bool(get_user_group_setting_value(user_id, "prompts", "enabled_prompts", db))
    if not prompts_enabled:
        effective_prompt_ids = []

    if byok:
        model_id = "byok"
    else:
        resolved_selection = resolve_chat_model_for_user(db, user_id=user_id, model_id=model_id)
        db_model = resolved_selection.base_model
        resolved_base_model_id = db_model.id
        if resolved_selection.model_kind == "agent" and resolved_selection.agent is not None:
            selected_agent_id = resolved_selection.agent.id
            agent_instruction = resolved_selection.agent_instruction
            agent_skill_ids = list(resolved_selection.agent_skill_ids or [])
            agent_asset_descriptors_by_category = resolved_selection.asset_descriptors_by_category or agent_asset_descriptors_by_category
        rate_limit_admission = _admit_rate_limited_chat_action(
            db,
            user_id=user_id,
            group_id=group_id,
            model=db_model,
            action_type=RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
            chat_id=chat_id,
        )
        model_settings = db_model.settings if isinstance(db_model.settings, dict) else {}
        allow_custom_generation_parameter = coerce_allow_custom_flag(
            model_settings.get("allow_custom_generation_parameter")
        )
        image_ids = _merge_attachment_ids(image_ids, agent_asset_descriptors_by_category.get("image"))
        video_ids = _merge_attachment_ids(video_ids, agent_asset_descriptors_by_category.get("video"))
        audio_ids = _merge_attachment_ids(audio_ids, agent_asset_descriptors_by_category.get("audio"))
        document_ids = _merge_attachment_ids(document_ids, agent_asset_descriptors_by_category.get("document"))

    sanitized_message = sanitize_chat_text(message) if isinstance(message, str) else message

    if db_model and not byok:
        model_skill_ids = _extract_model_skill_ids(model_settings)
        trusted_admin_skill_ids = _resolve_trusted_admin_skill_ids(
            model_skill_ids=model_skill_ids,
            agent_skill_ids=agent_skill_ids,
        )
        effective_skill_ids = _resolve_generation_skill_ids(
            requested_skill_ids=effective_skill_ids,
            model_skill_ids=model_skill_ids,
            agent_skill_ids=agent_skill_ids,
        )

    skill_file_attachments = _collect_skill_file_attachment_ids(
        db,
        user_id,
        effective_skill_ids,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    image_ids = _merge_attachment_ids(image_ids, skill_file_attachments.get("images"))
    video_ids = _merge_attachment_ids(video_ids, skill_file_attachments.get("videos"))
    audio_ids = _merge_attachment_ids(audio_ids, skill_file_attachments.get("audios"))
    document_ids = _merge_attachment_ids(document_ids, skill_file_attachments.get("documents"))

    def _stamp_chat_selection_metadata(chat_obj) -> None:
        if not chat_obj:
            return
        existing_meta = getattr(chat_obj, "meta", None)
        meta = existing_meta if isinstance(existing_meta, dict) else {}
        if selected_agent_id:
            meta["agent_id"] = selected_agent_id
        else:
            meta.pop("agent_id", None)
        if resolved_base_model_id:
            meta["base_model_id"] = resolved_base_model_id
        else:
            meta.pop("base_model_id", None)
        chat_obj.meta = meta

    def _finalize_failed_message_persistence() -> None:
        nonlocal rate_limit_final_status
        rate_limit_final_status = RATE_LIMIT_ADMISSION_FAILED
        finalize_rate_limit_admission(
            db,
            getattr(rate_limit_admission, "admission_id", None),
            final_status=rate_limit_final_status,
        )

    def _cleanup_empty_chat_after_persistence_failure(chat_obj) -> None:
        if not chat_obj:
            return
        try:
            _cleanup_chat_after_empty_transcript(chat_obj, group_id, db)
        except Exception:
            logger.warning(
                "Failed to clean up chat %s after message persistence error.",
                getattr(chat_obj, "id", None),
                exc_info=True,
            )

    def _raise_message_persistence_failed(exc: Exception, *, chat_obj=None, cleanup_empty_chat: bool = False) -> None:
        logger.exception(
            "Failed to persist current chat message for chat %s.",
            getattr(chat_obj, "id", chat_id),
        )
        with suppress(Exception):
            db.rollback()
        if cleanup_empty_chat:
            _cleanup_empty_chat_after_persistence_failure(chat_obj)
        _finalize_failed_message_persistence()
        raise HTTPException(status_code=500, detail="Failed to persist chat message") from exc

    assistant_metadata = _build_agent_selection_metadata(
        selected_agent_id=selected_agent_id,
        resolved_base_model_id=resolved_base_model_id,
    )
    new_chat = False
    temp_request_flag = False
    # Initialize variables used later for streaming
    chat_history = []
    generation_id = requested_generation_id
    reference_id = None
    memory_source_at = datetime.now(timezone.utc)
    normalized_current_chat_id = normalized_chat_id or None
    resolved_project_scope_id = str(project_id or "").strip() or None
    if normalized_current_chat_id and not resolved_project_scope_id:
        existing_chat_for_scope = existing_chat_for_send or db.query(Chats).filter(Chats.id == normalized_current_chat_id).first()
        if existing_chat_for_scope and getattr(existing_chat_for_scope, "project_id", None):
            resolved_project_scope_id = str(existing_chat_for_scope.project_id)
    chat_reference_payload, chat_reference_context = resolve_chat_reference_payload(
        user_id,
        db,
        chat_reference_ids,
        current_chat_id=normalized_current_chat_id,
        project_id=resolved_project_scope_id,
    )

    temp_chat_requested = isinstance(temp_chat, str) and temp_chat.strip() != ""
    temporary_chat_allowed = bool(get_group_setting_value(group_id, "chat", "allow_temporary_chat", db))
    if temp_chat_requested and not temporary_chat_allowed:
        raise HTTPException(status_code=403, detail="Temporary chats are disabled for your group.")

    if temp_chat_requested:
        chat_history = _parse_temp_chat_history(temp_chat)

        # Check setting: save temp chats?
        save_temp = False
        save_temp = bool(get_group_setting_value(group_id, "chat", "save_temp_chats", db))

        if save_temp:
            # Create a chat with meta.status = "temp" and persist the provided history.
            chat = create_chat(user_id, db, project_id=project_id, meta={"status": "temp"})
            _stamp_chat_selection_metadata(chat)
            db.commit()
            db.refresh(chat)
            chat_id = chat.id
            new_chat = True

            try:
                # Persist prior temp history messages into DB in order.
                for m in chat_history:
                    try:
                        role = str(m.get("role"))
                    except Exception:
                        role = "user"
                    blocks = _convert_temp_message_to_blocks(m)
                    if not blocks:
                        continue
                    message_model_id = m.get("model_id") or model_id or "user"
                    reference_id = m.get("reference_id")
                    create_chat_message(
                        db,
                        chat_id,
                        message_model_id,
                        role,
                        reference_id=reference_id,
                        content=blocks,
                        commit=False,
                    )

                # Now treat like a normal message: persist the new user message.
                user_msg_blocks = _convert_temp_message_to_blocks(
                    {
                        "role": "user",
                        "content": sanitized_message,
                        "images": image_ids,
                        "videos": video_ids,
                        "audios": audio_ids,
                        "documents": document_ids,
                        CHAT_REFERENCE_BLOCK_FIELD: chat_reference_payload,
                    }
                )
                user_msg = create_chat_message(
                    db,
                    chat_id,
                    model_id,
                    "user",
                    content=user_msg_blocks,
                    commit=False,
                )
                from app.memories.consolidation import stage_memory_consolidation

                stage_memory_consolidation(
                    db, user_id=user_id, source_message_id=str(user_msg.id),
                    source_at=user_msg.created_at or memory_source_at,
                    source_text=sanitized_message,
                    current_model_id=resolved_base_model_id if not byok else None,
                    byok=byok,
                )
                db.commit()
                db.refresh(user_msg)
                memory_source_at = (
                    getattr(user_msg, "created_at", None) or memory_source_at
                )
            except Exception as exc:
                _raise_message_persistence_failed(exc, chat_obj=chat, cleanup_empty_chat=True)

            # Start a normal generation bound to chat_id so attach/cancel works.
            generation_id = requested_generation_id or str(uuid.uuid4())
            stream_hub.start(generation_id, chat_id)
            cancel_registry.set_active(chat_id, generation_id)
            start_line = json.dumps({"t": "s", "d": generation_id})
            stream_hub.publish_line(generation_id, start_line)
            yield start_line + "\n"

            reference_id = user_msg.id if user_msg else None
            if reference_id:
                user_msg_line = json.dumps({"t": "m_id", "d": str(reference_id)})
                stream_hub.publish_line(generation_id, user_msg_line)
                yield user_msg_line + "\n"


            # Prepare chat_history for provider (re-read from DB to ensure consistent formatting/order)
            chat_history = (
                db.query(ChatMessages)
                .filter(ChatMessages.chat_id == chat_id)
                .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
                .all()
            )
            temp_request_flag = False  # allow providers to persist assistant messages for saved temp chats
        else:
            # Do not persist anything to DB; stream as a temp-only session
            temp_request_flag = True
            generation_id = requested_generation_id or str(uuid.uuid4())
            # Unsaved temporary chats have no durable chat ID. Cancellation is
            # authorized by the generation-owner reservation made in the
            # router; do not create a shared synthetic "temp" chat mapping that
            # collides across users or fails the chat ownership lookup.
            stream_hub.start(generation_id, "")
            start_line = json.dumps({"t": "s", "d": generation_id})
            stream_hub.publish_line(generation_id, start_line)
            yield start_line + "\n"

            # Emit a synthetic user message id so frontend rendering and streaming state work
            reference_id = str(uuid.uuid4())
            user_msg_line = json.dumps({"t": "m_id", "d": reference_id})
            stream_hub.publish_line(generation_id, user_msg_line)
            yield user_msg_line + "\n"

            # Append the current user turn to in-memory history so providers receive full context
            user_msg_blocks = _convert_temp_message_to_blocks(
                {
                    "role": "user",
                    "content": sanitized_message,
                    "images": image_ids,
                    "videos": video_ids,
                    "audios": audio_ids,
                    "documents": document_ids,
                }
            )

            has_text_content = any(
                isinstance(block, dict)
                and isinstance(block.get("content"), str)
                and block.get("content", "").strip()
                for block in user_msg_blocks
            )
            has_file_attachments = any(
                bool(items)
                for items in (image_ids, video_ids, audio_ids, document_ids)
            )
            has_chat_references = bool(chat_reference_payload)

            if has_text_content or has_file_attachments or has_chat_references:
                chat_history.append(
                    {
                        "id": reference_id,
                        "role": "user",
                        "content": user_msg_blocks,
                    }
                )
            else:
                reference_id = None
    else:
        reference_id = None
        
        # Handle new chat creation
        chat = None
        if not chat_id:
            # Create a new chat for normal (non-temp) messages
            chat = create_chat(user_id, db, project_id=project_id)
            _stamp_chat_selection_metadata(chat)
            db.commit()
            db.refresh(chat)
            chat_id = chat.id
            new_chat = True
            chat_history = []
        else:
            # Existing chat - get the history
            chat = existing_chat_for_send or _ensure_chat_available_for_send(db, chat_id, user_id)
            _stamp_chat_selection_metadata(chat)
            db.commit()
            if chat:
                db.refresh(chat)
            chat_history = db_get_chat_messages(db, chat_id)

        user_msg = None
        try:
            # Persist user message unless it's empty and has no files
            user_msg_blocks = _convert_temp_message_to_blocks(
                {
                    "role": "user",
                    "content": sanitized_message,
                    "images": image_ids,
                    "videos": video_ids,
                    "audios": audio_ids,
                    "documents": document_ids,
                    CHAT_REFERENCE_BLOCK_FIELD: chat_reference_payload,
                }
            )

            has_text_content = any(
                isinstance(block, dict)
                and isinstance(block.get("content"), str)
                and block.get("content", "").strip()
                for block in user_msg_blocks
            )
            has_file_attachments = any(
                bool(items)
                for items in (image_ids, video_ids, audio_ids, document_ids)
            )
            has_chat_references = bool(chat_reference_payload)

            if has_text_content or has_file_attachments or has_chat_references:
                user_msg = create_chat_message(
                    db,
                    chat_id,
                    model_id,
                    "user",
                    content=user_msg_blocks,
                    commit=False,
                )
                from app.memories.consolidation import stage_memory_consolidation

                stage_memory_consolidation(
                    db, user_id=user_id, source_message_id=str(user_msg.id),
                    source_at=user_msg.created_at or memory_source_at,
                    source_text=sanitized_message,
                    current_model_id=resolved_base_model_id if not byok else None,
                    byok=byok,
                )
                db.commit()
                db.refresh(user_msg)
                memory_source_at = (
                    getattr(user_msg, "created_at", None) or memory_source_at
                )
        except Exception as exc:
            _raise_message_persistence_failed(exc, chat_obj=chat, cleanup_empty_chat=new_chat)

        if new_chat:
            # Emit new chat event only after the first user turn is safely stored.
            new_chat_event = json.dumps({"t": "n_c", "d": chat_id})
            yield new_chat_event + "\n"

        # Create a new generation id and start hub only after the user message is stored
        generation_id = requested_generation_id or str(uuid.uuid4())
        stream_hub.start(generation_id, chat_id)
        cancel_registry.set_active(chat_id, generation_id)
        # Emit start event (first line) so clients can attach/cancel later
        start_line = json.dumps({"t": "s", "d": generation_id})
        stream_hub.publish_line(generation_id, start_line)
        yield start_line + "\n"

        if user_msg:
            reference_id = user_msg.id
            user_msg_line = json.dumps({"t": "m_id", "d": str(user_msg.id)})
            stream_hub.publish_line(generation_id, user_msg_line)
            yield user_msg_line + "\n"
        else:
            reference_id = None
        chat_history = db_get_chat_messages(db, chat_id)

    def _dispatch_title_generation(session, provider, model, message, sys_instr, *, provider_id=None, byok=None, user_id=None):
        """Dispatch title generation to the appropriate provider."""
        return call_provider_title_generation(
            ProviderRequest(
                request_type=REQUEST_TYPE_TITLE_GENERATION,
                db=session,
                provider=provider,
                model=model,
                prompt=message,
                system_instruction=sys_instr,
                byok=byok,
                user_id=user_id,
                extra={"provider_id": provider_id},
            )
        )

    # Prepare optional background title generation for new chats
    title_queue: "queue.Queue[str]" | None = None
    title_sent = False
    title_future = None
    def _start_title_thread_if_needed():
        nonlocal title_queue, title_future
        if not new_chat:
            return
        # Start background title generation that publishes to hub and enqueues a line for this stream
        title_queue = queue.Queue(maxsize=1)

        def _bg_generate_title():
            # Use independent DB session; do not reuse request-scoped session across threads
            session = SessionLocal()
            title_rate_limit_context_token = None
            fallback_title = (message or "")[:60]
            try:
                if rate_limit_admission:
                    title_rate_limit_context_token = set_current_rate_limit_admission_context(rate_limit_admission)
                def _resolve_title_instruction(settings):
                    custom_instr = None
                    if isinstance(settings, dict):
                        custom_instr = settings.get("custom_title_generation_instruction")
                    elif settings is not None:
                        custom_instr = getattr(settings, "custom_title_generation_instruction", None)

                    if isinstance(custom_instr, str):
                        stripped = custom_instr.strip()
                        if stripped:
                            return stripped

                    return get_title_generation_prompt(user_id, session)

                # Resolve provider/model for title generation
                title: str | None = None
                if byok:
                    settings = byok.get("settings") if isinstance(byok.get("settings"), dict) else {}
                    title_model_id = str(
                        get_group_setting_value(
                            group_id,
                            "chat",
                            "byok_title_generation_model_id",
                            session,
                        )
                        or ""
                    ).strip()
                    title_model = None
                    if title_model_id:
                        try:
                            title_model = session.query(Models).filter(Models.id == title_model_id).first()
                        except Exception:
                            title_model = None

                    if title_model:
                        sys_instr = _resolve_title_instruction(getattr(title_model, "settings", None))
                        title = _dispatch_title_generation(session, title_model.provider, title_model, message, sys_instr, provider_id=title_model.provider_id, user_id=user_id)
                    elif not settings.get("title_generation", False):
                        title = (message or "")[:60]
                    else:
                        provider = byok.get("provider")
                        model_name = byok.get("model_name")
                        sys_instr = _resolve_title_instruction(settings)
                        title = _dispatch_title_generation(session, provider, model_name, message, sys_instr, byok=byok, user_id=user_id)
                        if title is None:
                            title = (message or "")[:60]
                else:
                    # Use db_model/settings if available
                    db_model_local = None
                    try:
                        title_model_lookup_id = resolved_base_model_id or model_id
                        db_model_local = session.query(Models).filter(Models.id == title_model_lookup_id).first()
                    except Exception:
                        db_model_local = None

                    settings = getattr(db_model_local, "settings", None) if db_model_local else None
                    title_generation_enabled = settings.get("title_generation", False) if isinstance(settings, dict) else False
                    if not title_generation_enabled or not db_model_local:
                        title = (message or "")[:60]
                    else:
                        # Determine which model to use
                        use_model = db_model_local
                        if settings.get("title_generation_model") == "specific":
                            tg_model_id = settings.get("title_generation_model_id")
                            if tg_model_id:
                                specific = session.query(Models).filter(Models.id == tg_model_id).first()
                                if specific:
                                    use_model = specific
                        elif settings.get("title_generation_model") == "current":
                            pass
                        else:
                            # Invalid setting; fallback
                            title = (message or "")[:60]

                        if title is None and use_model:
                            sys_instr = _resolve_title_instruction(getattr(use_model, "settings", None))
                            title = _dispatch_title_generation(session, use_model.provider, use_model, message, sys_instr, provider_id=use_model.provider_id, user_id=user_id)
                            if title is None:
                                title = (message or "")[:60]

                if title is None:
                    title = (message or "")[:60]
                title = title[:60]
                # Persist title to chat
                chat_obj = session.query(Chats).filter(Chats.id == chat_id).first()
                if chat_obj:
                    chat_obj.title = sanitize_chat_text(title)
                    session.commit()
                if title_queue:
                    payload = json.dumps({"t": "n_t", "d": title})
                    title_queue.put_nowait(payload)
                    stream_hub.publish_line(generation_id, payload)
            except Exception as exc:
                logger = logging.getLogger(__name__)
                logger.exception("Background title generation failed: %s", exc)
                with suppress(Exception):
                    session.rollback()
                try:
                    chat_obj = session.query(Chats).filter(Chats.id == chat_id).first()
                    if chat_obj:
                        chat_obj.title = sanitize_chat_text(fallback_title)
                        session.commit()
                except Exception:
                    logger.warning("Failed to persist fallback title for chat %s.", chat_id, exc_info=True)
                    try:
                        session.rollback()
                    except Exception:
                        pass
                line = json.dumps({"t": "t_g", "d": fallback_title})
                stream_hub.publish_line(generation_id, line)
                try:
                    title_queue.put_nowait(line + "\n")
                except Exception:
                    pass
            finally:
                if title_rate_limit_context_token is not None:
                    reset_current_rate_limit_admission_context(title_rate_limit_context_token)
                try:
                    session.close()
                except Exception:
                    pass

        title_future = title_generation_executor.submit(_bg_generate_title)


    if byok:
        provider = byok.get("provider")
    else:
        provider = normalize_provider_value(db_model.provider)

    try:
        _assert_generation_provider_allowed(
            db,
            provider=provider,
            db_model=db_model,
            byok=byok,
            feature="chat generation",
        )
    except HTTPException as exc:
        rate_limit_final_status = RATE_LIMIT_ADMISSION_FAILED
        is_admin = is_admin_role(user_role)
        show_raw_error = is_admin or bool(byok)
        error_message = str(exc.detail) if show_raw_error else "An error occurred during generation. Please try again."
        err_line = json.dumps({"t": "e", "d": error_message, "admin_detail": str(exc.detail) if is_admin else None})
        stream_hub.publish_line(generation_id, err_line)
        yield err_line + "\n"
        finalize_rate_limit_admission(
            db,
            getattr(rate_limit_admission, "admission_id", None),
            final_status=rate_limit_final_status,
        )
        return

    if rate_limit_admission:
        rate_limit_context_token = set_current_rate_limit_admission_context(rate_limit_admission)

    rate_limit_admission_finalized = False

    def _finalize_primary_rate_limit_admission() -> None:
        """Release this generation's in-flight slot exactly once.

        The client treats a terminal stream event as permission to dispatch the
        next queued turn. Finalizing after yielding that event leaves a small
        race where the next request sees the prior admission as still open and
        is rejected. Title generation may continue independently, but it must
        not keep the completed chat turn's in-flight slot occupied.
        """

        nonlocal rate_limit_admission_finalized, rate_limit_context_token
        if rate_limit_admission_finalized:
            return

        if rate_limit_context_token is not None:
            reset_current_rate_limit_admission_context(rate_limit_context_token)
            rate_limit_context_token = None

        finalize_rate_limit_admission(
            db,
            getattr(rate_limit_admission, "admission_id", None),
            final_status=rate_limit_final_status,
        )
        rate_limit_admission_finalized = True

    chat_history = _filter_latest_assistant_versions(chat_history)
    logger.debug(
        "[ChatGeneration] utils.provider_resolved user=%s chat=%s gen_id=%s provider=%s",
        user_id,
        chat_id,
        generation_id,
        provider,
    )

    # Fetch and compose skill/prompt instructions for provider system sections.
    skill_content = _compose_skill_content(
        db,
        user_id,
        effective_skill_ids,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    prompt_content = _compose_prompt_content(db, user_id, effective_prompt_ids)
    personality_section = get_user_personality_system_instruction_section(user_id, db)
    if prompt_content:
        skill_content = f"{skill_content}\n\n{prompt_content}" if skill_content else prompt_content
    system_instruction_sections = _build_system_instruction_sections(
        personality_section=personality_section,
        skill_content=skill_content,
        agent_instruction=agent_instruction,
    )
    canvas_update_context = _build_canvas_user_edit_user_context(
        db,
        user_id=user_id,
        chat_history=chat_history,
    )
    notes_update_context = _build_notes_user_edit_context(
        db,
        user_id=user_id,
        chat_history=chat_history,
    )
    chat_reference_context = _join_latest_user_context(chat_reference_context, canvas_update_context, notes_update_context)
    if effective_skill_ids:
        logger.debug(
            "[ChatGeneration] utils.skills_loaded user=%s chat=%s skill_ids=%s",
            user_id,
            chat_id,
            effective_skill_ids,
        )
    if effective_prompt_ids:
        logger.debug(
            "[ChatGeneration] utils.prompts_loaded user=%s chat=%s prompt_ids=%s",
            user_id,
            chat_id,
            effective_prompt_ids,
        )

    def _extract_last_user_prompt():
        for msg in reversed(chat_history):
            role = getattr(msg, "role", None) if not isinstance(msg, dict) else msg.get("role")
            if role != "user":
                continue
            content = msg.content if hasattr(msg, "content") else msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("content"), str):
                        text = block.get("content", "").strip()
                        if text:
                            return text
                    elif isinstance(block, str) and block.strip():
                        return block.strip()
            elif isinstance(content, str) and content.strip():
                return content.strip()
        return ""

    llm_metric_provider = _metric_provider_name(provider, byok)
    llm_metric_model = _metric_model_name(db_model, model_id, byok)
    llm_metric_started_at = time.monotonic()
    llm_metric_success = True
    llm_metric_error_type: str | None = None
    llm_metric_input_tokens = 0
    llm_metric_output_tokens = 0

    try:
        # If a new chat with a valid generation_id has been started, kick off title generation
        _start_title_thread_if_needed()
        provider_settings_override = _build_provider_settings_override(
            custom_settings,
            allow_custom_generation_parameter=allow_custom_generation_parameter,
            subagent_targets=subagent_targets,
        )

        upstream = call_provider_chat(
            ProviderRequest(
                request_type=REQUEST_TYPE_CHAT,
                db=db,
                provider=provider,
                model=db_model,
                chat_history=chat_history,
                user_id=user_id,
                project_id=project_id,
                generation_id=generation_id,
                temp_request_flag=temp_request_flag,
                byok=byok,
                settings_override=provider_settings_override,
                reference_id=reference_id,
                system_instruction_sections=system_instruction_sections,
                assistant_metadata=assistant_metadata,
                note_ids=note_ids,
                reference_parts=reference_parts,
                chat_reference_context=chat_reference_context,
                user_role=user_role,
                extra={
                    "chat_id": chat_id,
                    "provider_callables": {
                        "google_aistudio": aistudio_chat,
                        "ollama": ollama_chat,
                        "openai": openai_chat,
                        "openai_responses": openai_chat,
                        "xai": openai_chat,
                        "microsoft_azure": openai_chat,
                        "lmstudio": openai_chat,
                        "openai_chat_completions": openai_chat_completions_chat,
                        "openrouter": openrouter_chat,
                        "anthropic": anthropic_chat,
                        "anthropic_base": anthropic_chat,
                    },
                },
            )
        )

        pending_done_line: str | None = None
        try:
            for line in _require_provider_stream_terminal(upstream, generation_id):
                line_success, line_error_type, line_input_tokens, line_output_tokens = _extract_llm_metric_stream_state(line)
                if not line_success:
                    llm_metric_success = False
                    llm_metric_error_type = llm_metric_error_type or line_error_type
                llm_metric_input_tokens = max(llm_metric_input_tokens, line_input_tokens)
                llm_metric_output_tokens = max(llm_metric_output_tokens, line_output_tokens)
                # Providers currently emit their logical done event before
                # finalizing the pending assistant row. Hold that event until
                # the generator advances through persistence, then record the
                # durable unread version before releasing completion.
                if _is_successful_generation_done_line(line):
                    pending_done_line = line
                    continue
                # Publish to hub with sequencing and forward to client.
                stream_hub.publish_line(generation_id, line)
                # Upstream already includes a trailing newline; forward as-is
                yield line
                # Opportunistically inject title if it became available
                if not title_sent and title_queue is not None:
                    try:
                        title_line = title_queue.get_nowait()
                        title_sent = True
                        yield title_line
                    except Exception:
                        pass
            if pending_done_line is not None:
                try:
                    _record_completion_before_stream_publish(db, chat_id, generation_id, pending_done_line)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[Chats] Failed to record completion before stream publish chat_id=%s gen_id=%s",
                        chat_id,
                        generation_id,
                    )
                # Close the in-flight reservation before the browser observes
                # completion and immediately submits the next queued turn.
                _finalize_primary_rate_limit_admission()
                stream_hub.publish_line(generation_id, pending_done_line)
                yield pending_done_line
        except _IncompleteProviderStreamError:
            llm_metric_success = False
            llm_metric_error_type = "IncompleteProviderStream"
            raise
        except HTTPException:
            llm_metric_success = False
            llm_metric_error_type = "HTTPException"
            raise
        except Exception as exc:  # noqa: BLE001
            llm_metric_success = False
            llm_metric_error_type = type(exc).__name__
            error_ref = str(uuid.uuid4())
            logger.exception(
                "[Chats] Generation failed user=%s chat_id=%s gen_id=%s ref=%s",
                user_id,
                chat_id,
                generation_id,
                error_ref,
            )
            detail = str(exc)
            if not is_admin_role(user_role) and not byok:
                detail = (
                    "An error occurred while generating a response. "
                    f"Reference ID: {error_ref}. Please contact support if this persists."
                )
            raise HTTPException(status_code=500, detail=detail) from exc
        # After upstream completes, attempt one last non-blocking injection for title
        if not title_sent and title_queue is not None:
            try:
                title_line = title_queue.get_nowait()
                title_sent = True
                yield title_line
            except Exception:
                pass
    except Exception as e:
        llm_metric_success = False
        llm_metric_error_type = llm_metric_error_type or type(e).__name__
        rate_limit_final_status = RATE_LIMIT_ADMISSION_FAILED
        err_line = _build_generation_error_line(
            e,
            user_role=user_role,
            byok=byok,
        )
        _finalize_primary_rate_limit_admission()
        stream_hub.publish_line(generation_id, err_line)
        yield err_line + "\n"
    finally:
        # Wait for title generation to complete before marking stream done
        if (
            not _is_generation_cancelled(generation_id)
            and not title_sent
            and title_queue is not None
            and title_future is not None
        ):
            try:
                # Wait for title with a timeout (max 90 seconds)
                title_line = title_queue.get(timeout=90.0)
                title_sent = True
                # Just yield it; the background thread already published to hub
                yield title_line
            except Exception:
                # Title generation timed out or failed; continue anyway
                pass
        record_llm_request_metric(
            provider=llm_metric_provider,
            model=llm_metric_model,
            success=llm_metric_success,
            duration_ms=(time.monotonic() - llm_metric_started_at) * 1000,
            input_tokens=llm_metric_input_tokens,
            output_tokens=llm_metric_output_tokens,
            error_type=llm_metric_error_type,
        )
        stream_hub.mark_done(generation_id)
        cancel_registry.clear(generation_id)
        _finalize_primary_rate_limit_admission()
    return True



# -------------------
# List Chats
# -------------------
def list_chats(
    user_id: str,
    db,
    project_id: str | None = None,
    include_shared_project: bool = False,
    include_archived: bool = False,
    limit: int = MAX_CHAT_PAGE_LIMIT,
):
    page = list_chats_paginated(
        user_id,
        db,
        offset=0,
        limit=limit,
        project_id=project_id,
        include_shared_project=include_shared_project,
        include_archived=include_archived,
    )
    return [*page["pinned"], *page["items"]]



# -------------------
# List Chats Paginated
# -------------------
def _coerce_chat_page_limit(value: int, default: int = DEFAULT_CHAT_PAGE_LIMIT) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, MAX_CHAT_PAGE_LIMIT))


def _coerce_chat_offset(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return max(0, parsed)


def _visible_loaded_chats(rows, include_temp: bool = False):
    return [
        chat
        for chat in rows
        if not is_chat_hidden_from_default_list(chat, include_temp=include_temp)
    ]


def list_chats_paginated(
    user_id: str,
    db,
    offset: int = 0,
    limit: int = DEFAULT_CHAT_PAGE_LIMIT,
    project_id: str | None = None,
    include_shared_project: bool = False,
    include_archived: bool = False,
    pinned_limit: int = MAX_PINNED_CHAT_LIST_LIMIT,
):
    """
    Return chats with pagination for lazy loading in the sidebar.
    
    - Pinned chats are returned first and capped to avoid unbounded responses
    - Unpinned chats are paginated with offset/limit
    
    Returns: {
        "pinned": [...],           # Capped pinned chats
        "items": [...],            # Paginated unpinned chats
        "total_pinned": int,       # Total count of pinned chats
        "pinned_has_more": bool,   # Whether pinned results were capped
        "total_unpinned": int,     # Total count of unpinned chats
        "has_more": bool           # Whether there are more unpinned chats to load
    }
    """
    offset = _coerce_chat_offset(offset)
    limit = _coerce_chat_page_limit(limit)
    pinned_limit = _coerce_chat_page_limit(pinned_limit, default=MAX_PINNED_CHAT_LIST_LIMIT)

    base_query = get_visible_chats_query(
        db,
        user_id,
        project_id=project_id,
        include_shared_project=include_shared_project,
        include_archived=include_archived,
    )
    pinned_query = base_query.filter(Chats.pinned_position.isnot(None))
    unpinned_query = base_query.filter(Chats.pinned_position.is_(None))

    total_pinned = pinned_query.order_by(None).count()
    total_unpinned = unpinned_query.order_by(None).count()

    pinned = _visible_loaded_chats(
        pinned_query
        .order_by(Chats.pinned_position.asc(), Chats.last_updated_at.desc(), Chats.id.asc())
        .limit(pinned_limit)
        .all()
    )
    paginated_unpinned = _visible_loaded_chats(
        unpinned_query
        .order_by(Chats.last_updated_at.desc(), Chats.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    apply_chat_unread_state(db, user_id, [*pinned, *paginated_unpinned])
    has_more = (offset + limit) < total_unpinned
    
    return {
        "pinned": pinned,
        "items": paginated_unpinned,
        "total_pinned": total_pinned,
        "pinned_has_more": total_pinned > len(pinned),
        "total_unpinned": total_unpinned,
        "has_more": has_more,
        "offset": offset,
        "limit": limit,
    }


def _build_chat_search_snippet_from_match(text: str, query: str, index: int) -> str:
    start = max(0, index - 10)
    end = min(len(text), start + 100)
    query_end = index + len(query)
    if query_end > end:
        end = min(len(text), query_end + (100 - (query_end - start)))
    if start > 0:
        whitespace_index = text.rfind(" ", 0, start)
        if whitespace_index != -1:
            start = whitespace_index + 1
    if end < len(text):
        whitespace_index = text.rfind(" ", start, end)
        if whitespace_index != -1 and whitespace_index > query_end - 1:
            end = whitespace_index
    return text[start:end]


def _build_chat_search_like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _get_first_chat_match_content_map(db, chat_ids: list[str], pattern: str) -> dict[str, str]:
    if not chat_ids:
        return {}

    ranked_matches = (
        db.query(
            ChatMessages.chat_id.label("chat_id"),
            ChatMessages.content.label("content"),
            func.row_number()
            .over(
                partition_by=ChatMessages.chat_id,
                order_by=(ChatMessages.created_at.asc(), ChatMessages.id.asc()),
            )
            .label("row_number"),
        )
        .filter(
            ChatMessages.chat_id.in_(chat_ids),
            ChatMessages.content.ilike(pattern, escape="\\"),
        )
        .subquery()
    )

    rows = (
        db.query(ranked_matches.c.chat_id, ranked_matches.c.content)
        .filter(ranked_matches.c.row_number == 1)
        .all()
    )
    return {str(row.chat_id): row.content or "" for row in rows}


def _get_first_user_message_preview_map(db, chat_ids: list[str]) -> dict[str, str]:
    if not chat_ids:
        return {}

    ranked_previews = (
        db.query(
            ChatMessages.chat_id.label("chat_id"),
            ChatMessages.content.label("content"),
            func.row_number()
            .over(
                partition_by=ChatMessages.chat_id,
                order_by=(ChatMessages.created_at.asc(), ChatMessages.id.asc()),
            )
            .label("row_number"),
        )
        .filter(
            ChatMessages.chat_id.in_(chat_ids),
            ChatMessages.role == "user",
            ChatMessages.content.isnot(None),
            ChatMessages.content != "",
        )
        .subquery()
    )

    rows = (
        db.query(ranked_previews.c.chat_id, ranked_previews.c.content)
        .filter(ranked_previews.c.row_number == 1)
        .all()
    )
    return {
        str(row.chat_id): _first_chat_message_preview(row.content)[:100]
        for row in rows
    }


def search_chats(user_id: str, query: str, db, offset: int = 0, limit: int = 20):
    q = (query or "").strip()
    if not q:
        return {"items": [], "total_count": 0, "has_more": False}

    offset = _coerce_chat_offset(offset)
    limit = _coerce_chat_page_limit(limit)
    pattern = _build_chat_search_like_pattern(q)
    ql = q.lower()

    message_match_exists = (
        db.query(ChatMessages.id)
        .filter(
            ChatMessages.chat_id == Chats.id,
            ChatMessages.content.ilike(pattern, escape="\\"),
        )
        .exists()
    )
    title_match_clause = Chats.title.ilike(pattern, escape="\\")

    matched_chats_query = (
        get_visible_chats_query(db, user_id)
        .filter(or_(title_match_clause, message_match_exists))
    )

    total_count = matched_chats_query.order_by(None).count()
    paginated_chats = (
        matched_chats_query
        .order_by(Chats.last_updated_at.desc(), Chats.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    apply_chat_unread_state(db, user_id, paginated_chats)
    has_more = (offset + limit) < total_count

    chat_ids = [str(chat.id) for chat in paginated_chats]
    first_match_by_chat_id = _get_first_chat_match_content_map(db, chat_ids, pattern)
    first_preview_by_chat_id = _get_first_user_message_preview_map(db, chat_ids)

    paginated_results: list[dict] = []
    for chat in paginated_chats:
        title = getattr(chat, "title", None) or ""
        snippet = ""
        matched_message_content = first_match_by_chat_id.get(str(chat.id), "")
        if matched_message_content:
            matched_message_text = _extract_chat_search_text(matched_message_content)
            match_index = matched_message_text.lower().find(ql)
            if match_index >= 0:
                snippet = _build_chat_search_snippet_from_match(matched_message_text, q, match_index)
        if not snippet and ql in title.lower():
            snippet = first_preview_by_chat_id.get(str(chat.id), "")

        paginated_results.append(
            {
                "chat_id": str(chat.id),
                "title": title,
                "last_updated_at": _chat_updated_at_iso(chat),
                "snippet": snippet,
                "has_unread_response": bool(getattr(chat, "has_unread_response", False)),
            }
        )
            
    return {
        "items": paginated_results,
        "total_count": total_count,
        "has_more": has_more
    }



# -------------------
# Pinning helpers
# -------------------
def _lock_user_pinned_chats(user_id: str, db):
    return (
        db.query(Chats)
        .filter(Chats.user_id == user_id, Chats.pinned_position.isnot(None))
        .with_for_update()
        .all()
    )


def _get_max_pinned_position(rows) -> int:
    if not rows:
        return 0
    return max(int(getattr(r, "pinned_position") or 0) for r in rows)



# -------------------
# Pin chat
# -------------------
def pin_chat(user_id: str, chat_id: str, db, position: int | None = None):
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    logger.debug("[PinChat] utils.chat_verified user=%s chat=%s", user_id, chat_id)

    # If already pinned and no position change requested, no-op
    if (chat.pinned_position is not None) and position is None:
        return True

    # Determine target position
    pinned_rows = _lock_user_pinned_chats(user_id, db)
    if position is None or position <= 0:
        position = _get_max_pinned_position(pinned_rows) + 1
    else:
        # Clamp into range [1..max+1]
        max_pos = _get_max_pinned_position(pinned_rows)
        if position > max_pos + 1:
            position = max_pos + 1

    # Shift down chats with position >= target
    affected = [
        c
        for c in pinned_rows
        if c.pinned_position is not None and c.pinned_position >= position and c.id != chat_id
    ]
    for c in affected:
        c.pinned_position = int(c.pinned_position) + 1

    chat.pinned_position = int(position)
    db.commit()
    return True


# -------------------
# Unpin chat
# -------------------
def unpin_chat(user_id: str, chat_id: str, db):
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    if chat.pinned_position is None:
        return True
    old_pos = chat.pinned_position

    chat.pinned_position = None

    if old_pos is not None:
        pinned_rows = _lock_user_pinned_chats(user_id, db)
        # Pull up chats with position > old_pos
        affected = [
            c
            for c in pinned_rows
            if c.pinned_position is not None and c.pinned_position > int(old_pos)
        ]
        for c in affected:
            c.pinned_position = int(c.pinned_position) - 1

    db.commit()
    return True



# -------------------
# Move pinned chat
# -------------------
def move_pinned_chat(user_id: str, chat_id: str, new_position: int, db):
    if new_position is None or new_position <= 0:
        raise HTTPException(status_code=400, detail="position must be >= 1")

    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")

    # If not pinned yet, pin it at requested position
    if chat.pinned_position is None:
        return pin_chat(user_id, chat_id, db, position=new_position)

    cur_pos = chat.pinned_position or 0
    if cur_pos == new_position:
        return True

    pinned_rows = _lock_user_pinned_chats(user_id, db)
    max_pos = _get_max_pinned_position(pinned_rows)
    # Clamp
    if new_position > max_pos:
        new_position = max_pos
        if new_position <= 0:
            new_position = 1

    if new_position > cur_pos:
        # Moving down: decrement others in (cur_pos, new_position]
        affected = [
            c
            for c in pinned_rows
            if c.pinned_position is not None
            and c.pinned_position > int(cur_pos)
            and c.pinned_position <= int(new_position)
            and c.id != chat_id
        ]
        for c in affected:
            c.pinned_position = int(c.pinned_position) - 1
    else:
        # Moving up: increment others in [new_position, cur_pos)
        affected = [
            c
            for c in pinned_rows
            if c.pinned_position is not None
            and c.pinned_position >= int(new_position)
            and c.pinned_position < int(cur_pos)
            and c.id != chat_id
        ]
        for c in affected:
            c.pinned_position = int(c.pinned_position) + 1

    chat.pinned_position = int(new_position)
    db.commit()
    return True



# -------------------
# Branch Chat (up to message)
# -------------------
def _select_branch_message_rows(
    messages: list[ChatMessages],
    message_id: str,
) -> list[ChatMessages]:
    """Return the canonical inclusive message slice for a branch.

    A branch ends at the next user-authored row, not necessarily immediately
    after the selected row. Keeping later assistant, tool, system, or other
    non-user rows preserves the remainder of the selected logical response turn.
    The next user turn and everything after it are excluded.
    """
    selected_rows: list[ChatMessages] = []
    reached_branch_point = False

    for message in messages:
        if reached_branch_point and message.role == "user":
            break

        selected_rows.append(message)
        if message.id == message_id:
            reached_branch_point = True

    if not reached_branch_point:
        raise HTTPException(status_code=400, detail="Message not found in chat history")

    return selected_rows


def branch_chat(user_id: str, message_id: str, db):
    """
    Create a new chat through the selected message's complete logical turn.

    The inclusive branch boundary is defined by
    :func:`_select_branch_message_rows`: subsequent non-user rows remain in the
    branch until the next user row. Ownership is enforced. The new chat retains
    the source title (with a Branch suffix) and project association, but starts
    unshared, unpinned, active, and without copied chat metadata.

    Returns: {"status": "success", "new_chat_id": str}
    """
    src_msg = db.query(ChatMessages).filter(ChatMessages.id == message_id).first()
    if not src_msg:
        raise HTTPException(status_code=404, detail="Message not found")

    src_chat = (
        db.query(Chats)
        .filter(Chats.id == src_msg.chat_id, Chats.user_id == user_id)
        .first()
    )
    if not src_chat:
        raise HTTPException(status_code=404, detail="Chat not found!")

    now = datetime.now(timezone.utc)
    new_chat = Chats(
        user_id=user_id,
        title=(src_chat.title + " (Branch)") if src_chat.title else None,
        project_id=src_chat.project_id,
        share=None,
        share_id=None,
        archived=False,
        pinned_position=None,
        meta=None,
        created_at=now,
        last_updated_at=now,
        response_version=0,
        last_completed_generation_id=None,
    )
    db.add(new_chat)
    db.flush()

    messages = (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == src_chat.id)
        .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        .all()
    )

    branch_rows = _select_branch_message_rows(messages, message_id)
    id_map: dict[str, str] = {}
    cloned_messages = [
        clone_chat_message_for_new_chat(message, new_chat.id, id_map)
        for message in branch_rows
    ]

    if cloned_messages:
        db.add_all(cloned_messages)

    # Branch operation should appear as freshly updated
    new_chat.last_updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"status": "success", "new_chat_id": new_chat.id}



# -------------------
# Get Chat Messages
# -------------------
ATTACHMENT_FIELDS = ("images", "videos", "audios", "documents")
CHAT_SHARE_MIN_PASSWORD_LENGTH = 8
CHAT_SHARE_MAX_PASSWORD_LENGTH = 256
CHAT_SHARE_PASSWORD_ATTEMPT_LIMIT = 5
CHAT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS = 10 * 60
CHAT_SHARE_ACCESS_PUBLIC = "public"
CHAT_SHARE_ACCESS_AUTHENTICATED = "authenticated"
CHAT_SHARE_ACCESS_INVITED = "invited"
CHAT_SHARE_ACCESS_MODES = {CHAT_SHARE_ACCESS_PUBLIC, CHAT_SHARE_ACCESS_AUTHENTICATED, CHAT_SHARE_ACCESS_INVITED}
_SHARE_PASSWORD_ATTEMPTS_MAX_SIZE = 10000
_SHARE_PASSWORD_ATTEMPT_LOCK = threading.Lock()
_SHARE_PASSWORD_ATTEMPTS: dict[str, tuple[int, float]] = {}


def _cleanup_stale_password_attempts() -> None:
    now = time.time()
    with _SHARE_PASSWORD_ATTEMPT_LOCK:
        if len(_SHARE_PASSWORD_ATTEMPTS) <= _SHARE_PASSWORD_ATTEMPTS_MAX_SIZE:
            return
        stale_keys = [key for key, (_count, reset_at) in _SHARE_PASSWORD_ATTEMPTS.items() if reset_at <= now]
        for key in stale_keys:
            _SHARE_PASSWORD_ATTEMPTS.pop(key, None)


def ensure_chat_sharing_enabled_for_user(user_id: str, db) -> None:
    enabled = bool(get_user_group_setting_value(user_id, "sharing", "enable_chat_sharing", db))
    if not enabled:
        raise HTTPException(status_code=403, detail="Chat sharing is disabled for your group")


def ensure_chat_share_file_access_enabled_for_user(user_id: str, db) -> None:
    sharing_enabled = bool(
        get_user_group_setting_value(user_id, "sharing", "enable_chat_sharing", db)
    )
    if not sharing_enabled:
        raise HTTPException(status_code=403, detail="Shared file access is disabled for your group")


def ensure_chat_sharing_enabled_or_existing_share(user_id: str, chat: Chats, db) -> bool:
    """Return whether sharing can mutate the chat, preserving existing shares."""
    if getattr(chat, "share_id", None):
        try:
            ensure_chat_sharing_enabled_for_user(user_id, db)
            return True
        except HTTPException:
            return False
    ensure_chat_sharing_enabled_for_user(user_id, db)
    return True


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


def _extract_file_ids(raw):
    if not raw:
        return []
    if isinstance(raw, list) and raw and all(isinstance(r, dict) and r.get("id") for r in raw):
        return [str(r.get("id")) for r in raw if r.get("id")]
    if isinstance(raw, list):
        ids: list[str] = []
        for item in raw:
            if item is None:
                continue
            if isinstance(item, dict):
                file_id = item.get("id") or item.get("file_id")
                if file_id:
                    ids.append(str(file_id))
            else:
                ids.append(str(item))
        return ids
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            return [stripped]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item is not None]
        return [stripped]
    return []


def _build_file_lookup_for_user(user_id: str) -> Callable[[str], dict | None]:
    cache: dict[str, dict | None] = {}

    def _lookup(file_id: str) -> dict | None:
        normalized_file_id = str(file_id or "").strip()
        if not normalized_file_id:
            return None
        if normalized_file_id not in cache:
            cache[normalized_file_id] = get_file_info(user_id, normalized_file_id)
        return cache[normalized_file_id]

    return _lookup


def _hydrate_files(raw, file_lookup: Callable[[str], dict | None]):
    if isinstance(raw, list) and raw and all(isinstance(item, dict) and item.get("id") for item in raw):
        normalized = []
        for item in raw:
            file_id = str(item.get("id"))
            info = file_lookup(file_id)
            meta = info.get("meta") if info else {}
            meta = meta if isinstance(meta, dict) else {}
            original_name = (
                item.get("original_name")
                or item.get("original_filename")
                or meta.get("original_filename")
                or (info.get("file_name") if info else None)
            )
            mime_type = item.get("file_type") or item.get("mime_type") or (info.get("file_type") if info else None)
            file_size = item.get("file_size")
            if file_size is None and info:
                file_size = info.get("file_size")
            normalized.append(
                {
                    **item,
                    "id": file_id,
                    "file_id": item.get("file_id") or file_id,
                    "file_name": item.get("file_name") or (info.get("file_name") if info else None),
                    "original_name": original_name,
                    "original_filename": item.get("original_filename") or original_name,
                    "file_type": mime_type,
                    "mime_type": mime_type,
                    "file_size": file_size,
                    "meta": {
                        **meta,
                        **(item.get("meta") if isinstance(item.get("meta"), dict) else {}),
                        "original_filename": item.get("original_filename") or original_name,
                        "mime_type": mime_type,
                        "file_size": file_size,
                    },
                }
            )
        return normalized
    file_ids = _extract_file_ids(raw)
    if not file_ids:
        return None

    hydrated = []
    for file_id in file_ids:
        info = file_lookup(file_id)
        if info:
            meta = info.get("meta") or {}
            original_name = meta.get("original_filename") or info.get("file_name")
            mime_type = info.get("file_type")
            file_size = info.get("file_size")
            hydrated.append(
                {
                    "id": file_id,
                    "file_id": file_id,
                    "file_name": info.get("file_name"),
                    "original_name": original_name,
                    "original_filename": original_name,
                    "file_type": mime_type,
                    "mime_type": mime_type,
                    "file_size": file_size,
                    "meta": {
                        **meta,
                        "original_filename": original_name,
                        "mime_type": mime_type,
                        "file_size": file_size,
                    },
                }
            )
        else:
            hydrated.append(
                {
                    "id": file_id,
                    "file_id": file_id,
                    "file_name": None,
                    "original_name": None,
                    "original_filename": None,
                    "file_type": None,
                    "mime_type": None,
                    "file_size": None,
                    "meta": {},
                }
            )
    return hydrated


def _hydrate_content_blocks(raw, file_lookup: Callable[[str], dict | None]):
    decoded = _decode_jsonish(raw)
    if not isinstance(decoded, list):
        return decoded

    hydrated_blocks = []
    for block in decoded:
        if not isinstance(block, dict):
            hydrated_blocks.append(block)
            continue
        hydrated_block = dict(block)
        if str(hydrated_block.get("type") or "").strip().lower() == "tool_call_result":
            hydrated_block.pop("content", None)
        for field in ATTACHMENT_FIELDS:
            hydrated_block[field] = _hydrate_files(block.get(field), file_lookup)
        hydrated_blocks.append(hydrated_block)
    return hydrated_blocks


def _collect_attachment_summary(blocks):
    summary = {field: [] for field in ATTACHMENT_FIELDS}
    seen = {field: set() for field in ATTACHMENT_FIELDS}
    if not isinstance(blocks, list):
        return {field: None for field in ATTACHMENT_FIELDS}

    for block in blocks:
        if not isinstance(block, dict):
            continue
        for field in ATTACHMENT_FIELDS:
            attachments = block.get(field)
            if not attachments:
                continue
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                file_id = attachment.get("id")
                if file_id and file_id in seen[field]:
                    continue
                if file_id:
                    seen[field].add(file_id)
                summary[field].append(attachment)
    return {field: summary[field] or None for field in ATTACHMENT_FIELDS}


def _collect_attachment_file_ids_from_blocks(raw_content) -> set[str]:
    file_ids: set[str] = set()
    decoded = _decode_jsonish(raw_content)
    if not isinstance(decoded, list):
        return file_ids

    for block in decoded:
        if not isinstance(block, dict):
            continue
        for field in ATTACHMENT_FIELDS:
            for file_id in _extract_file_ids(block.get(field)):
                normalized_file_id = str(file_id or "").strip()
                if normalized_file_id:
                    file_ids.add(normalized_file_id)
    return file_ids


def _collect_attachment_file_ids_for_chat_rows(rows: list[ChatMessages]) -> set[str]:
    file_ids: set[str] = set()
    for row in rows:
        file_ids.update(_collect_attachment_file_ids_from_blocks(getattr(row, "content", None)))
    return file_ids


def _message_role(message) -> str:
    """Return the message role for either ORM rows or temporary-history dictionaries."""
    if isinstance(message, dict):
        return str(message.get("role") or "")
    return str(getattr(message, "role", "") or "")


def _message_created_at(message) -> datetime | None:
    """Return a message timestamp for either ORM rows or temporary-history dictionaries."""
    value = message.get("created_at") if isinstance(message, dict) else getattr(message, "created_at", None)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize datetimes to timezone-aware UTC for safe comparisons."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso_utc(value) -> datetime | None:
    """Parse an ISO timestamp and normalize it to timezone-aware UTC."""
    if isinstance(value, datetime):
        return _as_utc_datetime(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc_datetime(parsed)


def _last_assistant_message_time(chat_history: list) -> datetime | None:
    """Return the latest assistant timestamp visible in the current provider history."""
    for message in reversed(chat_history or []):
        if _message_role(message) != "assistant":
            continue
        timestamp = _as_utc_datetime(_message_created_at(message))
        if timestamp is not None:
            return timestamp
    return None


def _collect_canvas_file_ids_from_chat_history(chat_history: list) -> set[str]:
    """Collect file IDs referenced in chat history so canvas-change notices stay scoped."""
    file_ids: set[str] = set()
    for message in chat_history or []:
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        file_ids.update(_collect_attachment_file_ids_from_blocks(content))
    return file_ids


def _collect_note_ids_from_value(value, *, in_note_context: bool = False) -> set[str]:
    """Collect note IDs from structured notes tool payloads in chat history."""
    note_ids: set[str] = set()
    if isinstance(value, dict):
        meta = value.get("meta") if isinstance(value.get("meta"), dict) else {}
        tool_label = str(
            value.get("tool_name")
            or meta.get("tool_name")
            or ""
        ).strip()
        block_type = str(value.get("type") or "").strip().lower()
        legacy_call = str(value.get("content") or "").lstrip()
        is_notes_block = (
            tool_label == "notes"
            or tool_label.startswith("notes(")
            or (
                block_type == "tool_call"
                and legacy_call.startswith("notes(")
            )
        )
        current_note_context = in_note_context or is_notes_block
        direct_note_id = value.get("note_id")
        if isinstance(direct_note_id, str) and direct_note_id.strip():
            note_ids.add(direct_note_id.strip())

        if current_note_context:
            direct_id = value.get("id")
            if isinstance(direct_id, str) and direct_id.strip():
                note_ids.add(direct_id.strip())

        for key, child in value.items():
            child_note_context = current_note_context or key in {"note", "notes"}
            note_ids.update(_collect_note_ids_from_value(child, in_note_context=child_note_context))
    elif isinstance(value, list):
        for child in value:
            note_ids.update(_collect_note_ids_from_value(child, in_note_context=in_note_context))
    elif isinstance(value, str) and in_note_context:
        decoded = _decode_jsonish(value)
        if isinstance(decoded, (dict, list)):
            note_ids.update(_collect_note_ids_from_value(decoded, in_note_context=True))
    return note_ids


def _collect_note_ids_from_chat_history(chat_history: list) -> set[str]:
    """Collect notes referenced by notes tool calls/results in the current chat history."""
    note_ids: set[str] = set()
    for message in chat_history or []:
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        decoded = _decode_jsonish(content)
        note_ids.update(_collect_note_ids_from_value(decoded))
    return note_ids


def _note_title_from_content(content: str | None) -> str:
    """Return a short human-readable note title for stale-note context."""
    text = str(content or "").strip()
    if not text:
        return "Untitled note"
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned[:80]
    return "Untitled note"


def _build_notes_user_edit_context(db, *, user_id: str, chat_history: list) -> str | None:
    """Build user prompt context for notes edited after the last assistant turn."""
    last_assistant_at = _last_assistant_message_time(chat_history)
    if last_assistant_at is None:
        return None

    candidate_note_ids = sorted(_collect_note_ids_from_chat_history(chat_history))[
        :MAX_TRACKED_ARTIFACT_IDS
    ]
    if not candidate_note_ids:
        return None

    try:
        from app.notes.models import NoteHistory, Notes, SharedNoteSubscription
    except Exception:
        logger.warning("Failed to import note models for edit context.", exc_info=True)
        return None

    try:
        notes = db.query(Notes).filter(Notes.id.in_(candidate_note_ids)).all()
        notes_by_id = {str(note.id): note for note in notes}
        subscriptions = (
            db.query(SharedNoteSubscription)
            .filter(
                SharedNoteSubscription.note_id.in_(candidate_note_ids),
                SharedNoteSubscription.subscriber_id == str(user_id),
            )
            .all()
        )
        subscriptions_by_note_id = {
            str(subscription.note_id): subscription for subscription in subscriptions
        }
        ranked_history = (
            db.query(
                NoteHistory.note_id.label("note_id"),
                NoteHistory.actor_type.label("actor_type"),
                NoteHistory.version_number.label("version_number"),
                NoteHistory.created_at.label("created_at"),
                NoteHistory.id.label("id"),
                func.row_number()
                .over(
                    partition_by=NoteHistory.note_id,
                    order_by=(
                        NoteHistory.created_at.desc(),
                        NoteHistory.id.desc(),
                    ),
                )
                .label("history_rank"),
            )
            .filter(
                NoteHistory.note_id.in_(candidate_note_ids),
                NoteHistory.created_at > last_assistant_at,
            )
            .subquery()
        )
        history_rows = (
            db.query(
                ranked_history.c.note_id,
                ranked_history.c.actor_type,
                ranked_history.c.version_number,
                ranked_history.c.created_at,
                ranked_history.c.id,
            )
            .filter(ranked_history.c.history_rank == 1)
            .all()
        )
    except Exception:
        logger.warning("Failed to batch note edit history for chat context.", exc_info=True)
        return None

    latest_history_by_note_id = {
        str(history_row.note_id): history_row for history_row in history_rows
    }

    changed_notes: list[dict[str, str]] = []
    for note_id in candidate_note_ids:
        try:
            note = notes_by_id.get(str(note_id))
            if not note:
                continue
            is_owner = str(note.user_id) == str(user_id)
            subscription = subscriptions_by_note_id.get(str(note_id))
            share_type = str(getattr(subscription, "share_type", "") or "")
            share_is_active = (
                (share_type == "live" and bool(note.live_share_id))
                or (share_type == "collaborate" and bool(note.collaborate_share_id))
            )
            if not is_owner and not share_is_active:
                continue
            latest_history = latest_history_by_note_id.get(str(note_id))
            if not latest_history or str(latest_history.actor_type or "").strip().lower() != "user":
                continue
            edited_at = _as_utc_datetime(latest_history.created_at)
            if edited_at is None or edited_at <= last_assistant_at:
                continue
            changed_notes.append(
                {
                    "note_id": str(note.id),
                    "title": _note_title_from_content(note.content),
                    "version": str(latest_history.version_number or ""),
                    "edited_at": edited_at.isoformat(),
                }
            )
        except Exception:
            logger.warning("Failed to inspect note edit history for chat context.", exc_info=True)
            continue

    if not changed_notes:
        return None

    changed_notes = sorted(changed_notes, key=lambda item: (item["edited_at"], item["title"]))[-5:]
    lines = [
        "The user edited one or more notes after your last response. The note contents in your prior conversation context may be stale.",
        "Before commenting on, summarizing, or editing any listed note, call the notes tool with type='view' and that note_id to load the current saved contents.",
        "Changed notes:",
    ]
    for item in changed_notes:
        version = f", version={item['version']}" if item["version"] else ""
        lines.append(
            f"- note_id={item['note_id']}, title={item['title']}{version}, edited_at={item['edited_at']}"
        )

    return "## Note Updates\n\n" + "\n".join(lines)


def _canvas_content_type_from_file_record(file_record: Files) -> str:
    """Infer a canvas content type from file metadata, MIME type, or filename."""
    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    canvas_type = str(meta.get("canvas_type") or "").strip().lower()
    if canvas_type in {"markdown", "mermaid", "csv", "html", "spreadsheet"}:
        if canvas_type == "spreadsheet":
            spreadsheet_format = str(meta.get("spreadsheet_format") or "").strip().lower()
            return spreadsheet_format if spreadsheet_format in {"csv", "tsv", "xlsx", "xls"} else "spreadsheet"
        return canvas_type
    mime_type = str(file_record.file_type or "").strip().lower()
    if mime_type in {"text/markdown", "text/x-markdown", "text/plain"}:
        return "markdown"
    if mime_type == "text/x-mermaid":
        return "mermaid"
    if mime_type == "text/csv":
        return "csv"
    if mime_type == "text/tab-separated-values":
        return "tsv"
    if mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return "xlsx"
    if mime_type == "application/vnd.ms-excel":
        return "xls"
    if mime_type == "text/html":
        return "html"
    original_name = str(meta.get("original_filename") or file_record.file_name or "").strip().lower()
    if original_name.endswith((".html", ".htm")):
        return "html"
    if original_name.endswith((".mmd", ".mermaid")):
        return "mermaid"
    if original_name.endswith(".csv"):
        return "csv"
    if original_name.endswith(".tsv"):
        return "tsv"
    if original_name.endswith(".xlsx"):
        return "xlsx"
    if original_name.endswith(".xls"):
        return "xls"
    return "markdown"


def _build_canvas_user_edit_user_context(db, *, user_id: str, chat_history: list) -> str | None:
    """Build user prompt context for canvas revisions saved after the last assistant turn."""
    last_assistant_at = _last_assistant_message_time(chat_history)
    if last_assistant_at is None:
        return None

    candidate_file_ids = sorted(_collect_canvas_file_ids_from_chat_history(chat_history))[
        :MAX_TRACKED_ARTIFACT_IDS
    ]
    if not candidate_file_ids:
        return None

    try:
        file_records = (
            accessible_files_query(db, str(user_id))
            .filter(Files.id.in_(candidate_file_ids))
            .all()
        )
    except Exception:
        logger.warning("Failed to inspect canvas edit revisions for chat context.", exc_info=True)
        return None

    changed_files: list[dict[str, str]] = []
    for file_record in file_records:
        meta = file_record.meta if isinstance(file_record.meta, dict) else {}
        canvas_type = str(meta.get("canvas_type") or "").strip().lower()
        is_canvas = meta.get("canvas") is True or canvas_type in {"markdown", "mermaid", "csv", "html", "spreadsheet"}
        if not is_canvas:
            continue
        if str(meta.get("canvas_last_edit_source") or "").strip().lower() != "user":
            continue

        edited_at = _parse_iso_utc(meta.get("canvas_last_edited_at"))
        if edited_at is None or edited_at <= last_assistant_at:
            continue

        original_name = str(meta.get("original_filename") or file_record.file_name or "canvas file")
        changed_files.append(
            {
                "file_id": str(file_record.id),
                "file_name": original_name,
                "content_type": _canvas_content_type_from_file_record(file_record),
                "revision": str(meta.get("canvas_revision") or ""),
                "edited_at": edited_at.isoformat(),
            }
        )

    if not changed_files:
        return None

    changed_files = sorted(changed_files, key=lambda item: (item["edited_at"], item["file_name"]))[-5:]
    lines = [
        "The user edited one or more canvas files after your last response. The file contents in your prior conversation context may be stale.",
        "Before commenting on, summarizing, or editing any listed text canvas file, call the canvas tool with type='view' and that file_id to load the current saved contents.",
        "For XLS, XLSX, or TSV spreadsheets, reload the current saved attachment with the available file or code-execution tools instead; the text Canvas tool does not decode binary Excel workbooks.",
        "Changed canvas files:",
    ]
    for item in changed_files:
        revision = f", revision={item['revision']}" if item["revision"] else ""
        lines.append(
            f"- file_id={item['file_id']}, name={item['file_name']}, content_type={item['content_type']}{revision}, edited_at={item['edited_at']}"
        )

    return "## Canvas File Updates\n\n" + "\n".join(lines)


def _collect_public_attachment_file_ids_from_content(raw_content) -> set[str]:
    file_ids: set[str] = set()
    decoded = _decode_jsonish(raw_content)
    if not isinstance(decoded, list):
        return file_ids

    for block in decoded:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type not in {"user", "content", "file"}:
            continue
        for field in ATTACHMENT_FIELDS:
            for file_id in _extract_file_ids(block.get(field)):
                normalized_file_id = str(file_id or "").strip()
                if normalized_file_id:
                    file_ids.add(normalized_file_id)
    return file_ids


def _collect_public_attachment_file_ids_for_chat_rows(rows: list[ChatMessages]) -> set[str]:
    file_ids: set[str] = set()
    for row in rows:
        if getattr(row, "role", None) not in {"user", "assistant"}:
            continue
        file_ids.update(_collect_public_attachment_file_ids_from_content(getattr(row, "content", None)))
    return file_ids


def _public_file_payload(file_value):
    if isinstance(file_value, str):
        normalized_file_id = file_value.strip()
        return {"id": normalized_file_id, "file_id": normalized_file_id} if normalized_file_id else None
    if not isinstance(file_value, dict):
        return None

    file_id = str(file_value.get("id") or file_value.get("file_id") or "").strip()
    if not file_id:
        return None

    meta = file_value.get("meta") if isinstance(file_value.get("meta"), dict) else {}
    original_name = (
        file_value.get("original_name")
        or file_value.get("original_filename")
        or meta.get("original_filename")
        or file_value.get("file_name")
    )
    mime_type = file_value.get("mime_type") or file_value.get("file_type") or meta.get("mime_type") or meta.get("file_type")
    file_size = file_value.get("file_size")
    if file_size is None:
        file_size = meta.get("file_size")

    return {
        "id": file_id,
        "file_id": file_id,
        "file_name": file_value.get("file_name"),
        "original_name": original_name,
        "original_filename": original_name,
        "file_type": mime_type,
        "mime_type": mime_type,
        "file_size": file_size,
        "meta": {
            "original_filename": original_name,
            "mime_type": mime_type,
            "file_size": file_size,
        },
    }


def _public_file_list(raw):
    if not isinstance(raw, list):
        return None
    public_files = []
    seen: set[str] = set()
    for item in raw:
        public_file = _public_file_payload(item)
        if not public_file:
            continue
        file_id = public_file["id"]
        if file_id in seen:
            continue
        seen.add(file_id)
        public_files.append(public_file)
    return public_files or None


CHAT_SHARE_PUBLICATION_SCHEMA_VERSION = 1
CHAT_SHARE_PUBLIC_TEXT_LIMIT = 4000
CHAT_SHARE_PUBLIC_PREVIEW_LIMIT = 240


def _bounded_public_text(value, limit: int = CHAT_SHARE_PUBLIC_TEXT_LIMIT) -> str:
    """Return bounded plain text for a public static projection.

    Static share cards are deliberately data-only. They never reuse persisted
    widget HTML, scripts, tool arguments, access tokens, or arbitrary metadata.
    The shared frontend renders these strings with ``textContent``.
    """

    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _public_quiz_projection(tool_result: dict) -> dict | None:
    """Build the reviewed, non-interactive representation of a quiz widget."""

    raw_questions = tool_result.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        return None
    items: list[dict] = []
    for raw_question in raw_questions[:20]:
        if not isinstance(raw_question, dict):
            continue
        prompt = _bounded_public_text(raw_question.get("question") or raw_question.get("prompt"))
        raw_options = raw_question.get("options") or raw_question.get("choices")
        if not prompt or not isinstance(raw_options, list):
            continue
        options = [_bounded_public_text(option) for option in raw_options[:4]]
        options = [option for option in options if option]
        raw_index = raw_question.get("correct_option_index")
        if not isinstance(raw_index, int) or isinstance(raw_index, bool) or raw_index < 0 or raw_index >= len(options):
            continue
        items.append(
            {
                "prompt": prompt,
                "options": options,
                "answer": options[raw_index],
                "explanation": _bounded_public_text(raw_question.get("explanation")),
            }
        )
    if not items:
        return None
    return {
        "type": "shared_tool_output",
        "output_type": "quiz",
        "title": _bounded_public_text(tool_result.get("title"), 300) or "Quiz",
        "description": _bounded_public_text(tool_result.get("description")),
        "items": items,
    }


def _public_flashcards_projection(tool_result: dict) -> dict | None:
    """Build the reviewed, non-interactive representation of flashcards."""

    raw_cards = tool_result.get("cards") or tool_result.get("flashcards")
    if not isinstance(raw_cards, list) or not raw_cards:
        return None
    items: list[dict] = []
    for raw_card in raw_cards[:40]:
        if not isinstance(raw_card, dict):
            continue
        front = _bounded_public_text(
            raw_card.get("front") or raw_card.get("term") or raw_card.get("prompt") or raw_card.get("question")
        )
        back = _bounded_public_text(
            raw_card.get("back") or raw_card.get("definition") or raw_card.get("translation") or raw_card.get("answer")
        )
        if not front or not back:
            continue
        items.append(
            {
                "front": front,
                "back": back,
                "hint": _bounded_public_text(raw_card.get("hint") or raw_card.get("clue")),
                "example": _bounded_public_text(raw_card.get("example") or raw_card.get("example_sentence")),
                "note": _bounded_public_text(raw_card.get("note") or raw_card.get("notes")),
            }
        )
    if not items:
        return None
    return {
        "type": "shared_tool_output",
        "output_type": "flashcards",
        "title": _bounded_public_text(tool_result.get("title"), 300) or "Flashcards",
        "description": _bounded_public_text(tool_result.get("description")),
        "items": items,
    }


def _mcp_result_text(tool_result: dict) -> str:
    """Extract display text from an MCP result without copying private app state."""

    content = tool_result.get("content")
    if isinstance(content, list):
        text_parts = []
        for item in content[:50]:
            if isinstance(item, dict) and str(item.get("type") or "").lower() == "text":
                text = _bounded_public_text(item.get("text"))
                if text:
                    text_parts.append(text)
        if text_parts:
            return _bounded_public_text("\n\n".join(text_parts))
    if isinstance(content, str) and content.strip():
        return _bounded_public_text(content)
    structured = tool_result.get("structuredContent")
    if structured is not None:
        try:
            return _bounded_public_text(json.dumps(structured, ensure_ascii=False, indent=2))
        except (TypeError, ValueError):
            return _bounded_public_text(structured)
    return ""


def _public_mcp_projection(meta: dict) -> dict | None:
    """Build a reviewed text snapshot for an MCP App without embedding the app."""

    app = meta.get("mcp_app") if isinstance(meta.get("mcp_app"), dict) else {}
    tool_result = app.get("tool_result") if isinstance(app.get("tool_result"), dict) else {}
    text = _mcp_result_text(tool_result)
    if not text:
        fallback = meta.get("tool_result")
        if isinstance(fallback, dict):
            try:
                text = _bounded_public_text(json.dumps(fallback, ensure_ascii=False, indent=2))
            except (TypeError, ValueError):
                text = ""
    if not text:
        return None
    title = (
        app.get("resource_title")
        or (app.get("tool_info") or {}).get("title")
        or app.get("tool_name")
        or "MCP App result"
    )
    return {
        "type": "shared_tool_output",
        "output_type": "mcp_app",
        "title": _bounded_public_text(title, 300),
        "description": "",
        "text": text,
    }


def _public_static_output_projection(block) -> dict | None:
    """Project one allowlisted widget type into inert public data."""

    if not isinstance(block, dict) or str(block.get("type") or "").strip().lower() != "widget":
        return None
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    widget_type = str(meta.get("widget_type") or "").strip().lower()
    tool_result = meta.get("tool_result") if isinstance(meta.get("tool_result"), dict) else {}
    if widget_type == "quiz":
        return _public_quiz_projection(tool_result)
    if widget_type == "flashcards":
        return _public_flashcards_projection(tool_result)
    if widget_type == "mcp_app":
        return _public_mcp_projection(meta)
    return None


def _public_static_output_id(row_id: str, block_index: int, projection: dict) -> str:
    """Bind an approval to the exact persisted row, block position, and projection."""

    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    material = f"{row_id}:{block_index}:{canonical}".encode("utf-8")
    return hashlib.sha256(material, usedforsecurity=False).hexdigest()


def _public_content_block(
    block,
    *,
    row_id: str = "",
    block_index: int = 0,
    approved_output_ids: set[str] | None = None,
):
    if not isinstance(block, dict):
        return None

    block_type = str(block.get("type") or "").strip().lower()
    if block_type in {"user", "content"}:
        public_block = {
            "type": block_type,
            "content": str(block.get("content") or ""),
        }
        for field in ATTACHMENT_FIELDS:
            files = _public_file_list(block.get(field))
            if files:
                public_block[field] = files
        if block_type == "content":
            meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
            citations = meta.get("citations")
            if isinstance(citations, list) and citations:
                public_block["meta"] = {"citations": citations}
        return public_block

    if block_type == "file":
        public_block = {"type": "file"}
        has_files = False
        for field in ATTACHMENT_FIELDS:
            files = _public_file_list(block.get(field))
            if files:
                public_block[field] = files
                has_files = True
        return public_block if has_files else None

    # Internal reasoning remains silently excluded. An omission notice here
    # would reveal model-internal behavior without helping readers understand
    # a missing result in the conversation timeline.
    if block_type == "reasoning":
        return None

    if block_type == "widget":
        projection = _public_static_output_projection(block)
        if projection:
            output_id = _public_static_output_id(row_id, block_index, projection)
            if output_id in (approved_output_ids or set()):
                return projection
        return {"type": "share_omission", "reason": "interactive_output_not_published"}

    if block_type in {"tool_call", "tool_call_result", "tool_result"}:
        return {"type": "share_omission", "reason": "tool_activity_not_published"}

    if block_type:
        return {"type": "share_omission", "reason": "unsupported_block_not_published"}

    return None


def _assistant_version_groups(rows: list[ChatMessages]) -> dict[str, list[ChatMessages]]:
    """Group persisted assistant answers by the user message they answer."""

    groups: dict[str, list[ChatMessages]] = {}
    for row in rows or []:
        if getattr(row, "role", None) != "assistant":
            continue
        reference_id = str(getattr(row, "reference_id", None) or "").strip()
        if reference_id:
            groups.setdefault(reference_id, []).append(row)
    for versions in groups.values():
        versions.sort(
            key=lambda row: (
                int(getattr(row, "retry_count", 0) or 0),
                _as_utc_datetime(getattr(row, "created_at", None)) or datetime.min.replace(tzinfo=timezone.utc),
                str(getattr(row, "id", "") or ""),
            )
        )
    return groups


def _publication_data(share_info: dict | None) -> dict:
    """Return normalized publication settings without trusting stored JSON types."""

    share_data = share_info if isinstance(share_info, dict) else {}
    publication = share_data.get("publication") if isinstance(share_data.get("publication"), dict) else {}
    response_versions = publication.get("response_versions")
    approved_output_ids = publication.get("approved_output_ids")
    return {
        "schema_version": publication.get("schema_version"),
        "response_versions": dict(response_versions) if isinstance(response_versions, dict) else {},
        "approved_output_ids": [str(value) for value in approved_output_ids] if isinstance(approved_output_ids, list) else [],
    }


def _select_public_chat_rows(rows: list[ChatMessages], share_info: dict | None) -> list[ChatMessages]:
    """Select the only assistant version that the public endpoint may transmit.

    Legacy shares keep their documented latest-answer behavior. New reviewed
    shares pin every known turn and use the original response for a future
    turn until the owner explicitly reviews a different saved answer.
    """

    groups = _assistant_version_groups(rows)
    publication = _publication_data(share_info)
    configured = publication["response_versions"]
    reviewed_share = publication.get("schema_version") == CHAT_SHARE_PUBLICATION_SCHEMA_VERSION
    selected_ids: set[str] = set()
    for reference_id, versions in groups.items():
        configured_id = str(configured.get(reference_id) or "").strip()
        valid_ids = {str(getattr(row, "id", "") or "") for row in versions}
        if configured_id:
            # A stale or corrupted selection fails closed instead of silently
            # publishing a different response than the owner approved.
            if configured_id in valid_ids:
                selected_ids.add(configured_id)
            continue
        fallback = (
            next(
                (
                    version
                    for version in versions
                    if int(getattr(version, "retry_count", 0) or 0) == 0
                ),
                None,
            )
            if reviewed_share
            else versions[-1]
        )
        fallback_id = str(getattr(fallback, "id", "") or "") if fallback else ""
        if fallback_id:
            selected_ids.add(fallback_id)

    selected: list[ChatMessages] = []
    for row in rows or []:
        if getattr(row, "role", None) == "assistant" and getattr(row, "reference_id", None):
            if str(getattr(row, "id", "") or "") not in selected_ids:
                continue
        selected.append(row)
    return selected


def _message_public_preview(row) -> str:
    """Return a compact owner-only preview without rendering widget HTML."""

    decoded = _decode_jsonish(getattr(row, "content", None))
    if isinstance(decoded, list):
        pieces = []
        for block in decoded:
            if not isinstance(block, dict) or str(block.get("type") or "").lower() not in {"user", "content"}:
                continue
            text = _bounded_public_text(block.get("content"), CHAT_SHARE_PUBLIC_PREVIEW_LIMIT)
            if text:
                pieces.append(text)
        preview = " ".join(pieces)
    else:
        preview = _bounded_public_text(decoded, CHAT_SHARE_PUBLIC_PREVIEW_LIMIT)
    return preview or "No ordinary text in this saved response."


def _static_output_options_for_row(row, approved_output_ids: set[str]) -> list[dict]:
    """List safe review candidates for one persisted assistant row."""

    decoded = _decode_jsonish(getattr(row, "content", None))
    if not isinstance(decoded, list):
        return []
    options: list[dict] = []
    row_id = str(getattr(row, "id", "") or "")
    for block_index, block in enumerate(decoded):
        projection = _public_static_output_projection(block)
        if not projection:
            continue
        output_id = _public_static_output_id(row_id, block_index, projection)
        options.append(
            {
                "id": output_id,
                "output_type": projection["output_type"],
                "title": projection["title"],
                "preview": projection,
                "approved": output_id in approved_output_ids,
            }
        )
    return options


def _build_share_publication_options(rows: list[ChatMessages], share_info: dict | None) -> dict:
    """Build the owner-only response-version and static-output review model."""

    publication = _publication_data(share_info)
    groups = _assistant_version_groups(rows)
    approved = set(publication["approved_output_ids"])
    user_rows = {
        str(getattr(row, "id", "") or ""): row
        for row in rows or []
        if getattr(row, "role", None) == "user"
    }
    turns: list[dict] = []
    normalized_selected: dict[str, str] = {}
    reviewed_share = publication.get("schema_version") == CHAT_SHARE_PUBLICATION_SCHEMA_VERSION
    for reference_id, versions in groups.items():
        configured_id = str(publication["response_versions"].get(reference_id) or "")
        valid_ids = {str(getattr(version, "id", "") or "") for version in versions}
        selected_id = configured_id if configured_id in valid_ids else ""
        if not selected_id:
            fallback = (
                next(
                    (
                        version
                        for version in versions
                        if int(getattr(version, "retry_count", 0) or 0) == 0
                    ),
                    None,
                )
                if reviewed_share
                else versions[-1]
            )
            selected_id = str(getattr(fallback, "id", "") or "") if fallback else ""
        if selected_id:
            normalized_selected[reference_id] = selected_id
        turns.append(
            {
                "reference_id": reference_id,
                "prompt_preview": _message_public_preview(user_rows[reference_id]) if reference_id in user_rows else "",
                "versions": [
                    {
                        "message_id": str(getattr(version, "id", "") or ""),
                        "retry_count": int(getattr(version, "retry_count", 0) or 0),
                        "preview": _message_public_preview(version),
                        "selected": str(getattr(version, "id", "") or "") == selected_id,
                        "static_outputs": _static_output_options_for_row(version, approved),
                    }
                    for version in versions
                ],
            }
        )
    return {
        "publication": {
            "response_versions": normalized_selected,
            "approved_output_ids": publication["approved_output_ids"],
        },
        "turns": turns,
    }


def _serialize_public_chat_rows(
    rows: list[ChatMessages],
    file_lookup: Callable[[str], dict | None],
    *,
    approved_output_ids: set[str] | None = None,
) -> list[dict]:
    public_ids = {
        str(getattr(row, "id", "") or ""): f"shared-msg-{index + 1}"
        for index, row in enumerate(rows)
        if getattr(row, "id", None)
    }
    serialized = []
    for row in rows:
        if row.role in {"tool", "tools"}:
            serialized.append(
                {
                    "id": public_ids.get(str(getattr(row, "id", "") or ""), f"shared-msg-{len(serialized) + 1}"),
                    "role": "share_notice",
                    "reference_id": public_ids.get(str(getattr(row, "reference_id", "") or "")),
                    "content": [{"type": "share_omission", "reason": "tool_message_not_published"}],
                    "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
                }
            )
            continue
        if row.role not in {"user", "assistant"}:
            continue

        hydrated_content = _hydrate_content_blocks(row.content, file_lookup)
        retry_count = getattr(row, "retry_count", None) or 0

        if isinstance(hydrated_content, list):
            public_content = [
                public_block
                for public_block in (
                    _public_content_block(
                        block,
                        row_id=str(getattr(row, "id", "") or ""),
                        block_index=block_index,
                        approved_output_ids=approved_output_ids,
                    )
                    for block_index, block in enumerate(hydrated_content)
                )
                if public_block is not None
            ]
        else:
            public_content = str(hydrated_content or "")

        attachment_summary = _collect_attachment_summary(public_content)
        serialized.append(
            {
                "id": public_ids.get(str(getattr(row, "id", "") or ""), f"shared-msg-{len(serialized) + 1}"),
                "role": row.role,
                "reference_id": public_ids.get(str(getattr(row, "reference_id", "") or "")),
                "retry_count": 0 if row.role == "assistant" else retry_count,
                "total_versions": 1 if row.role == "assistant" else None,
                "content": public_content,
                "images": attachment_summary["images"],
                "videos": attachment_summary["videos"],
                "audios": attachment_summary["audios"],
                "documents": attachment_summary["documents"],
                "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
                "bookmarked": False,
            }
        )
    return serialized


def _serialize_chat_rows(
    rows: list[ChatMessages],
    file_lookup: Callable[[str], dict | None],
    include_bookmarked: bool = True,
    model_name_by_id: dict[str, str] | None = None,
) -> list[dict]:
    """Serialize private chat rows and repair missing display model metadata.

    Older provider adapters trusted the upstream stream to echo ``model``. A
    few compatibility gateways returned null instead, leaving otherwise valid
    messages without a model row in the diagnostics tooltip. The database row
    still records Omlorix's internal model ID, so reads can safely recover the
    configured provider model name without rewriting historical message data.
    """
    regeneration_map: dict[str, list[int]] = {}
    for row in rows:
        if row.role == "assistant" and row.reference_id:
            regeneration_map.setdefault(row.reference_id, []).append(row.retry_count or 0)
    for reference_id in regeneration_map:
        regeneration_map[reference_id].sort()

    serialized = []
    normalized_model_names = model_name_by_id if isinstance(model_name_by_id, dict) else {}
    for row in rows:
        hydrated_content = _hydrate_content_blocks(row.content, file_lookup)
        if row.role == "assistant":
            configured_model_name = resolve_model_metadata_id(
                normalized_model_names.get(str(getattr(row, "model_id", "") or ""))
            )
            hydrated_content = _add_missing_model_metadata(
                hydrated_content,
                configured_model_name,
            )
        attachment_summary = _collect_attachment_summary(hydrated_content)
        retry_count = getattr(row, "retry_count", None) or 0
        total_versions = 1
        if row.role == "assistant" and row.reference_id and row.reference_id in regeneration_map:
            total_versions = len(regeneration_map[row.reference_id])

        item = {
            "id": row.id,
            "role": row.role,
            "model_id": getattr(row, "model_id", None),
            "reference_id": getattr(row, "reference_id", None),
            "retry_count": retry_count,
            "total_versions": total_versions if row.role == "assistant" else None,
            "content": hydrated_content,
            "thinking": row.thinking,
            "images": attachment_summary["images"],
            "videos": attachment_summary["videos"],
            "audios": attachment_summary["audios"],
            "documents": attachment_summary["documents"],
            "name": getattr(row, "tool_name", None),
            "system_instruction": getattr(row, "system_instruction", None),
            "meta": getattr(row, "meta", None),
            "generation": getattr(row, "generation", None),
            "sources": _decode_jsonish(getattr(row, "sources", None)),
            "youtube": _decode_jsonish(getattr(row, "youtube", None)),
            "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
            "bookmarked": bool(getattr(row, "bookmarked", False)) if include_bookmarked else False,
        }
        if isinstance(row.role, str) and row.role.lower() in {"tool", "tools"}:
            item.pop("content", None)
        serialized.append(item)
    return serialized


_DISPLAY_MODEL_METADATA_KEYS = (
    "model_id",
    "modelId",
    "model",
    "model_name",
    "modelName",
)
_GENERATION_METADATA_HINT_KEYS = (
    "timestamp",
    "response_id",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "request_count",
)


def _add_missing_model_metadata(content, configured_model_name: str | None):
    """Add a configured model fallback to one assistant generation block.

    The helper only enriches serialized copies. Provider-reported identifiers
    remain authoritative, and tool/file metadata is not modified unless no
    normal assistant content or reasoning block exists.
    """
    fallback = resolve_model_metadata_id(configured_model_name)
    if not fallback or not isinstance(content, list):
        return content

    target = None
    # Prefer the block already carrying generation statistics because that is
    # the metadata object consumed by appendAssistantDone on transcript reload.
    for block in reversed(content):
        if not isinstance(block, dict):
            continue
        meta = block.get("meta")
        if isinstance(meta, dict) and any(key in meta for key in _GENERATION_METADATA_HINT_KEYS):
            target = block
            break

    if target is None:
        target = next(
            (
                block
                for block in reversed(content)
                if isinstance(block, dict)
                and str(block.get("type") or "").lower() in {"content", "reasoning"}
            ),
            None,
        )
    if target is None:
        return content

    meta = target.get("meta") if isinstance(target.get("meta"), dict) else {}
    if any(resolve_model_metadata_id(meta.get(key)) for key in _DISPLAY_MODEL_METADATA_KEYS):
        return content

    target["meta"] = {**meta, "model": fallback}
    return content


def _message_model_name_lookup(db, rows: list[ChatMessages]) -> dict[str, str]:
    """Load configured model names needed to enrich historical assistant rows."""
    model_ids = {
        str(getattr(row, "model_id", "") or "").strip()
        for row in rows
        if getattr(row, "role", None) == "assistant"
    }
    model_ids.discard("")
    model_ids.discard("byok")
    if not model_ids:
        return {}

    models = db.query(Models).filter(Models.id.in_(model_ids)).all()
    return {
        str(model.id): model_name
        for model in models
        if (model_name := resolve_model_metadata_id(getattr(model, "model_name", None)))
    }


def _get_ordered_chat_rows(db, chat_id: str) -> list[ChatMessages]:
    return (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == chat_id)
        .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        .all()
    )


def _share_url_for_id(db, share_id: str) -> str:
    base_url = get_public_url(db).rstrip("/")
    return f"{base_url}/chats/shared/{share_id}"


def _chat_updated_at_iso(chat: Chats) -> str | None:
    updated_at = getattr(chat, "last_updated_at", None)
    if not updated_at:
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    else:
        updated_at = updated_at.astimezone(timezone.utc)
    return updated_at.isoformat()


def _empty_share_status() -> dict:
    return {
        "share_id": None,
        "share_url": None,
        "created_at": None,
        "access_mode": CHAT_SHARE_ACCESS_PUBLIC,
        "has_password": False,
        "expires_at": None,
        "invited_user_ids": [],
        "publication": {"response_versions": {}, "approved_output_ids": []},
    }


def _serialize_chat_share_status(chat: Chats, db) -> dict:
    if not chat.share_id:
        return _empty_share_status()
    share_data = chat.share if isinstance(chat.share, dict) else {}
    return {
        "share_id": chat.share_id,
        "share_url": _share_url_for_id(db, chat.share_id),
        "created_at": share_data.get("created_at"),
        "access_mode": _share_access_mode(share_data),
        "has_password": bool(share_data.get("password")),
        "expires_at": share_data.get("expires_at"),
        "invited_user_ids": _normalize_invited_user_ids(share_data.get("invited_user_ids")),
        "publication": {
            "response_versions": _publication_data(share_data)["response_versions"],
            "approved_output_ids": _publication_data(share_data)["approved_output_ids"],
        },
    }


def _requested_publication_dict(publication) -> dict:
    """Accept a validated schema or a plain dictionary from internal callers."""

    if publication is None:
        return {}
    if hasattr(publication, "model_dump"):
        publication = publication.model_dump()
    return dict(publication) if isinstance(publication, dict) else {}


def _normalize_share_publication(
    rows: list[ChatMessages],
    requested_publication=None,
    *,
    existing_share_info: dict | None = None,
) -> dict:
    """Validate owner choices and pin every currently persisted answer turn.

    IDs are accepted only when they identify an assistant row in the same chat
    and answer the referenced user row. Static-output approvals are accepted
    only when the exact deterministic projection exists in a selected answer.
    """

    requested_was_supplied = requested_publication is not None
    requested = _requested_publication_dict(requested_publication)
    requested_versions = requested.get("response_versions")
    requested_versions = dict(requested_versions) if isinstance(requested_versions, dict) else {}
    requested_outputs = requested.get("approved_output_ids")
    requested_outputs = [str(value).strip().lower() for value in requested_outputs] if isinstance(requested_outputs, list) else []
    groups = _assistant_version_groups(rows)
    existing = _publication_data(existing_share_info)

    unknown_references = set(requested_versions) - set(groups)
    if unknown_references:
        raise HTTPException(status_code=400, detail="A selected response turn does not belong to this chat")
    if requested_was_supplied and set(requested_versions) != set(groups):
        raise HTTPException(status_code=400, detail="Select a saved response version for every chat turn")

    response_versions: dict[str, str] = {}
    for reference_id, versions in groups.items():
        valid_ids = {str(getattr(version, "id", "") or "") for version in versions}
        requested_id = str(requested_versions.get(reference_id) or "").strip()
        existing_id = str(existing["response_versions"].get(reference_id) or "").strip()
        selected_id = requested_id or (existing_id if existing_id in valid_ids else "")
        if requested_id and requested_id not in valid_ids:
            raise HTTPException(status_code=400, detail="A selected response version does not belong to this chat turn")
        if not selected_id:
            selected_id = str(getattr(versions[-1], "id", "") or "")
        if selected_id:
            response_versions[reference_id] = selected_id

    prospective_share = {
        "publication": {
            "schema_version": CHAT_SHARE_PUBLICATION_SCHEMA_VERSION,
            "response_versions": response_versions,
            "approved_output_ids": [],
        }
    }
    selected_rows = _select_public_chat_rows(rows, prospective_share)
    valid_output_ids: set[str] = set()
    for row in selected_rows:
        if getattr(row, "role", None) != "assistant":
            continue
        for option in _static_output_options_for_row(row, set()):
            valid_output_ids.add(option["id"])
    invalid_outputs = set(requested_outputs) - valid_output_ids
    if invalid_outputs:
        raise HTTPException(status_code=400, detail="A reviewed tool output is not part of the selected response versions")

    return {
        "schema_version": CHAT_SHARE_PUBLICATION_SCHEMA_VERSION,
        "response_versions": response_versions,
        "approved_output_ids": list(dict.fromkeys(requested_outputs)),
    }


def get_share_publication_options(user_id: str, chat_id: str, db) -> dict:
    """Return owner-only saved response and static-output review choices."""

    chat = get_chat(db, chat_id, user_id)
    _ensure_chat_available_for_read(chat, detail="Chat not found")
    rows = _get_ordered_chat_rows(db, chat.id)
    return _build_share_publication_options(rows, chat.share if isinstance(chat.share, dict) else {})


def update_share_publication(user_id: str, chat_id: str, publication, db) -> dict:
    """Replace the reviewed projection for an existing owner-controlled share."""

    chat = get_chat(db, chat_id, user_id)
    if not chat.share_id or _check_and_cleanup_expired(chat, db):
        raise HTTPException(status_code=404, detail="Share not found")
    ensure_chat_sharing_enabled_or_existing_share(user_id, chat, db)
    rows = _get_ordered_chat_rows(db, chat.id)
    normalized = _normalize_share_publication(
        rows,
        publication,
        existing_share_info=chat.share if isinstance(chat.share, dict) else {},
    )
    _update_share_data(chat, db, publication=normalized)
    return _serialize_chat_share_status(chat, db)


def _resolve_shared_chat_or_404(db, share_id: str) -> tuple[Chats, dict]:
    cleaned_share_id = str(share_id or "").strip()
    if not cleaned_share_id:
        raise HTTPException(status_code=404, detail="Shared chat not found")

    chat = db.query(Chats).filter(Chats.share_id == cleaned_share_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Shared chat not found")

    _ensure_chat_available_for_read(chat, detail="Shared chat not found")

    if _check_and_cleanup_expired(chat, db):
        raise HTTPException(status_code=404, detail="Shared chat not found")

    if not chat.share_id:
        raise HTTPException(status_code=404, detail="Shared chat not found")

    share_data = chat.share if isinstance(chat.share, dict) else {}
    return chat, share_data


def _chat_meta_dict(chat: Chats) -> dict:
    meta = _decode_jsonish(getattr(chat, "meta", None))
    return meta if isinstance(meta, dict) else {}


def _ensure_chat_available_for_read(chat: Chats, *, detail: str = "Chat not found!") -> None:
    meta = _chat_meta_dict(chat)
    if meta.get("status") == "temp" or meta.get("shadow_deleted"):
        raise HTTPException(status_code=404, detail=detail)


def get_chat_for_read(user_id: str, chat_id: str, db) -> Chats:
    """Return a readable chat for the user, including shared-project chats."""
    normalized_chat_id = str(chat_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_chat_id or not normalized_user_id:
        raise HTTPException(status_code=404, detail="Chat not found!")

    # Prefer the direct owner lookup because that is the common read path.
    chat = db.query(Chats).filter(Chats.id == normalized_chat_id, Chats.user_id == normalized_user_id).first()

    if not chat:
        # Shared project members can read chats that belong to the project owner or
        # other members, so we fall back to the broader lookup and re-check access.
        chat = db.query(Chats).filter(Chats.id == normalized_chat_id).first()
        normalized_project_id = str(getattr(chat, "project_id", None) or "").strip() if chat else ""
        if not normalized_project_id:
            raise HTTPException(status_code=404, detail="Chat not found!")

        from app.projects.models import has_project_access

        if not has_project_access(db, normalized_user_id, normalized_project_id):
            raise HTTPException(status_code=404, detail="Chat not found!")

    _ensure_chat_available_for_read(chat)
    return chat


def _ensure_chat_available_for_send(db, chat_id: str, user_id: str) -> Chats:
    chat = get_chat(db, chat_id, user_id)
    return ensure_chat_sendable(chat)


def get_chat_messages(user_id: str, chat_id: str, db):
    # Keep message reads aligned with metadata reads so reload flows can use the
    # same access rules as the transcript endpoint.
    chat = get_chat_for_read(user_id, chat_id, db)
    include_bookmarked = chat.user_id == user_id
    rows = _get_ordered_chat_rows(db, chat_id)
    file_lookup = _build_file_lookup_for_user(user_id)
    return _serialize_chat_rows(
        rows,
        file_lookup,
        include_bookmarked=include_bookmarked,
        model_name_by_id=_message_model_name_lookup(db, rows),
    )


def _parse_expiry(exp_str: str) -> datetime:
    """Parse expiry string to timezone-aware datetime."""
    if exp_str.endswith('Z'):
        exp_str = exp_str[:-1] + "+00:00"
    
    exp_dt = datetime.fromisoformat(exp_str)
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    else:
        exp_dt = exp_dt.astimezone(timezone.utc)
    
    return exp_dt


def _validate_expiry(expires_at: Optional[datetime]) -> Optional[str]:
    """Validate and normalize expiry datetime."""
    if expires_at is None:
        return None
    
    exp_dt = expires_at
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    else:
        exp_dt = exp_dt.astimezone(timezone.utc)
    
    if exp_dt <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="expires_at must be in the future")
    
    return exp_dt.isoformat()


def _normalize_share_password_for_storage(password: str | None) -> str | None:
    if password is None:
        return None
    normalized = str(password).strip()
    if not normalized:
        return None
    if len(normalized) < CHAT_SHARE_MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Share password must be at least {CHAT_SHARE_MIN_PASSWORD_LENGTH} characters long",
        )
    if len(normalized) > CHAT_SHARE_MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Share password must be at most {CHAT_SHARE_MAX_PASSWORD_LENGTH} characters long",
        )
    return normalized


def _normalize_share_access_mode(access_mode: str | None, default: str | None = None) -> str:
    raw = str(access_mode or "").strip().lower()
    if not raw:
        return default or CHAT_SHARE_ACCESS_PUBLIC
    aliases = {
        "link": CHAT_SHARE_ACCESS_PUBLIC,
        "public_link": CHAT_SHARE_ACCESS_PUBLIC,
        "anyone": CHAT_SHARE_ACCESS_PUBLIC,
        "authenticated_users": CHAT_SHARE_ACCESS_AUTHENTICATED,
        "signed_in": CHAT_SHARE_ACCESS_AUTHENTICATED,
        "signed-in": CHAT_SHARE_ACCESS_AUTHENTICATED,
        "users": CHAT_SHARE_ACCESS_AUTHENTICATED,
        "invite": CHAT_SHARE_ACCESS_INVITED,
        "invites": CHAT_SHARE_ACCESS_INVITED,
        "invited_users": CHAT_SHARE_ACCESS_INVITED,
        "specific_users": CHAT_SHARE_ACCESS_INVITED,
    }
    normalized = aliases.get(raw, raw)
    if normalized not in CHAT_SHARE_ACCESS_MODES:
        raise HTTPException(status_code=400, detail="Invalid share access mode")
    return normalized


def _share_access_mode(share_info: dict | None) -> str:
    if not isinstance(share_info, dict):
        return CHAT_SHARE_ACCESS_PUBLIC
    raw = share_info.get("access_mode")
    if raw is None or str(raw).strip() == "":
        return CHAT_SHARE_ACCESS_PUBLIC
    try:
        return _normalize_share_access_mode(str(raw))
    except HTTPException:
        return CHAT_SHARE_ACCESS_AUTHENTICATED


def _normalize_invited_user_ids(user_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_user_id in user_ids or []:
        user_id = str(raw_user_id or "").strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        normalized.append(user_id)
    return normalized


def _verify_authenticated_share_user(user_access_token: str | None, client_ip: str | None, db):
    token = str(user_access_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return check_user_by_token(token, client_ip, "access", db)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise HTTPException(status_code=401, detail="Authentication required") from exc
        raise


def _ensure_share_access_mode_allowed(share_info: dict, db, user_access_token: str | None, client_ip: str | None):
    access_mode = _share_access_mode(share_info)
    if access_mode == CHAT_SHARE_ACCESS_PUBLIC:
        return None
    viewer = _verify_authenticated_share_user(user_access_token, client_ip, db)
    if access_mode != CHAT_SHARE_ACCESS_INVITED:
        return viewer

    owner_user_id = str(share_info.get("owner_user_id") or "").strip()
    viewer_user_id = str(getattr(viewer, "id", "") or "").strip()
    invited_user_ids = set(_normalize_invited_user_ids(share_info.get("invited_user_ids")))
    if viewer_user_id and (viewer_user_id == owner_user_id or viewer_user_id in invited_user_ids):
        return viewer
    raise HTTPException(status_code=403, detail="You are not invited to this shared chat")


def _share_password_attempt_key(share_id: str, client_ip: str | None) -> str:
    material = f"{str(share_id or '').strip()}:{str(client_ip or 'unknown').strip() or 'unknown'}"
    digest = hashlib.sha256(material.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"omlorix:shared-chat:password-attempts:{digest}"


def _get_local_share_password_attempt_count(key: str) -> int:
    now = time.time()
    with _SHARE_PASSWORD_ATTEMPT_LOCK:
        count, reset_at = _SHARE_PASSWORD_ATTEMPTS.get(key, (0, 0.0))
        if reset_at <= now:
            _SHARE_PASSWORD_ATTEMPTS.pop(key, None)
            return 0
        return count


def _increment_local_share_password_attempt_count(key: str) -> int:
    _cleanup_stale_password_attempts()
    now = time.time()
    with _SHARE_PASSWORD_ATTEMPT_LOCK:
        count, reset_at = _SHARE_PASSWORD_ATTEMPTS.get(key, (0, 0.0))
        if reset_at <= now:
            count = 0
            reset_at = now + CHAT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS
        count += 1
        _SHARE_PASSWORD_ATTEMPTS[key] = (count, reset_at)
        return count


def _clear_local_share_password_attempt_count(key: str) -> None:
    with _SHARE_PASSWORD_ATTEMPT_LOCK:
        _SHARE_PASSWORD_ATTEMPTS.pop(key, None)


def _get_share_password_attempt_count(key: str) -> int:
    client = get_redis_client()
    if client is not None:
        try:
            return int(client.get(key) or 0)
        except Exception:
            pass
    return _get_local_share_password_attempt_count(key)


def _record_share_password_failure(key: str) -> int:
    client = get_redis_client()
    if client is not None:
        try:
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, CHAT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS)
            return count
        except Exception:
            pass
    return _increment_local_share_password_attempt_count(key)


def _clear_share_password_failures(key: str) -> None:
    client = get_redis_client()
    if client is not None:
        try:
            client.delete(key)
            return
        except Exception:
            pass
    _clear_local_share_password_attempt_count(key)


def _enforce_share_password_attempt_limit(share_id: str, client_ip: str | None) -> str:
    key = _share_password_attempt_key(share_id, client_ip)
    if _get_share_password_attempt_count(key) >= CHAT_SHARE_PASSWORD_ATTEMPT_LIMIT:
        raise HTTPException(status_code=429, detail="Too many invalid password attempts. Please retry later.")
    return key


def _check_and_cleanup_expired(chat, db) -> bool:
    """Check if share is expired and clean up if needed. Returns True if expired."""
    if not chat.share_id or not isinstance(chat.share, dict):
        return False
    
    exp_str = chat.share.get("expires_at")
    if not exp_str:
        return False
    
    try:
        exp_dt = _parse_expiry(exp_str)
        if exp_dt <= datetime.now(timezone.utc):
            chat.share = None
            chat.share_id = None
            chat.last_updated_at = datetime.now(timezone.utc)
            db.commit()
            return True
    except Exception:
        pass
    
    return False


_SHARE_FIELD_UNSET = object()


def _update_share_data(
    chat,
    db,
    password=_SHARE_FIELD_UNSET,
    expires_at=_SHARE_FIELD_UNSET,
    access_mode=_SHARE_FIELD_UNSET,
    invited_user_ids=_SHARE_FIELD_UNSET,
    publication=_SHARE_FIELD_UNSET,
):
    """Update share data while preserving unspecified fields."""
    share_data = chat.share if isinstance(chat.share, dict) else {}

    if password is _SHARE_FIELD_UNSET:
        next_password = share_data.get("password")
    elif password is None:
        next_password = None
    else:
        next_password = hash_password(password)

    if expires_at is _SHARE_FIELD_UNSET:
        next_expires_at = share_data.get("expires_at")
    else:
        next_expires_at = expires_at

    if access_mode is _SHARE_FIELD_UNSET:
        next_access_mode = _share_access_mode(share_data)
    else:
        next_access_mode = _normalize_share_access_mode(access_mode)

    if invited_user_ids is _SHARE_FIELD_UNSET:
        next_invited_user_ids = _normalize_invited_user_ids(share_data.get("invited_user_ids"))
    else:
        next_invited_user_ids = _normalize_invited_user_ids(invited_user_ids)
    if next_access_mode != CHAT_SHARE_ACCESS_INVITED:
        next_invited_user_ids = []

    if publication is _SHARE_FIELD_UNSET:
        next_publication = _publication_data(share_data)
    else:
        next_publication = dict(publication) if isinstance(publication, dict) else {}

    new_share = {
        "password": next_password,
        "created_at": share_data.get("created_at", datetime.now(timezone.utc).isoformat()),
        "expires_at": next_expires_at,
        "access_mode": next_access_mode,
        "owner_user_id": str(getattr(chat, "user_id", "") or ""),
        "invited_user_ids": next_invited_user_ids,
        "publication": next_publication,
    }

    chat.share = new_share
    chat.last_updated_at = datetime.now(timezone.utc)
    db.commit()


# -------------------
# Share Chat
# -------------------
def share_chat(
    user_id: str,
    chat_id: str,
    password: Optional[str],
    db,
    expires_at: Optional[datetime] = None,
    access_mode: Optional[str] = None,
    invited_user_ids: Optional[list[str]] = None,
    publication=None,
):
    """Create a fresh active share link for a chat."""
    chat = get_chat(db, chat_id, user_id)
    _ensure_chat_available_for_read(chat, detail="Chat not found")

    _check_and_cleanup_expired(chat, db)
    sharing_can_mutate = ensure_chat_sharing_enabled_or_existing_share(user_id, chat, db)
    if chat.share_id and not sharing_can_mutate:
        raise HTTPException(status_code=403, detail="Share updates blocked by policy")

    password_hash = None
    if password is not None:
        normalized_password = _normalize_share_password_for_storage(password)
        password_hash = hash_password(normalized_password) if normalized_password else None

    expires_iso = None
    if expires_at is not None:
        expires_iso = _validate_expiry(expires_at)

    normalized_access_mode = (
        _normalize_share_access_mode(access_mode)
        if access_mode is not None
        else CHAT_SHARE_ACCESS_PUBLIC
    )
    normalized_invited_user_ids = _normalize_invited_user_ids(invited_user_ids)
    if normalized_access_mode == CHAT_SHARE_ACCESS_INVITED and not normalized_invited_user_ids:
        raise HTTPException(status_code=400, detail="Select at least one user to invite")
    if normalized_access_mode != CHAT_SHARE_ACCESS_INVITED:
        normalized_invited_user_ids = []

    now_iso = datetime.now(timezone.utc).isoformat()
    share_id = str(uuid.uuid4())
    rows = _get_ordered_chat_rows(db, chat.id)
    normalized_publication = _normalize_share_publication(rows, publication)

    chat.share_id = share_id
    chat.share = {
        "password": password_hash,
        "created_at": now_iso,
        "expires_at": expires_iso,
        "access_mode": normalized_access_mode,
        "owner_user_id": user_id,
        "invited_user_ids": normalized_invited_user_ids,
        "publication": normalized_publication,
    }
    chat.last_updated_at = datetime.now(timezone.utc)
    db.commit()

    return _serialize_chat_share_status(chat, db)



# -------------------
# Get Share Status
# -------------------
def get_share_status(user_id: str, chat_id: str, db):
    """Return current share status for the chat."""
    chat = get_chat(db, chat_id, user_id)

    if _check_and_cleanup_expired(chat, db):
        return _empty_share_status()
    return _serialize_chat_share_status(chat, db)



# -------------------
# Get Shared Chat Messages
# -------------------
def get_shared_chat_messages(
    share_id: str,
    password: Optional[str],
    db,
    known_updated_at: str | None = None,
    client_ip: str | None = None,
    user_access_token: str | None = None,
    share_access_token: str | None = None,
):
    """Resolve a shared chat by share_id and verify password if needed."""
    chat, share_info = _resolve_shared_chat_or_404(db, share_id)
    access_mode = _share_access_mode(share_info)
    _ensure_share_access_mode_allowed(share_info, db, user_access_token, client_ip)

    hashed = share_info.get("password")
    if hashed:
        token_authorized = False
        cleaned_share_access_token = str(share_access_token or "").strip()
        if cleaned_share_access_token:
            try:
                token_share_id, token_pwd_fp = verify_chat_share_access_token(db, cleaned_share_access_token)
                token_authorized = (
                    token_share_id == chat.share_id
                    and token_pwd_fp == _share_password_fingerprint(hashed)
                )
            except HTTPException:
                token_authorized = False

        if not token_authorized:
            normalized_password = str(password or "").strip()
            if not normalized_password:
                raise HTTPException(status_code=401, detail="Password required")
            attempt_key = _enforce_share_password_attempt_limit(chat.share_id, client_ip)
            if not verify_password(normalized_password, hashed):
                _record_share_password_failure(attempt_key)
                raise HTTPException(status_code=401, detail="Invalid password")
            _clear_share_password_failures(attempt_key)

    updated_at = _chat_updated_at_iso(chat)

    share_access_token, share_access_token_exp = create_chat_share_access_token(
        db,
        chat.share_id,
        share_password_hash=share_info.get("password"),
    )
    if updated_at and str(known_updated_at or "").strip() == updated_at:
        return {
            "share_id": chat.share_id,
            "updated_at": updated_at,
            "unchanged": True,
            "access_mode": access_mode,
            "has_password": bool(hashed),
            "expires_at": share_info.get("expires_at"),
            "share_access_token": share_access_token,
            "share_access_token_expires_at": share_access_token_exp.isoformat(),
        }

    rows = _get_ordered_chat_rows(db, chat.id)
    rows = _select_public_chat_rows(rows, share_info)
    file_lookup = _build_file_lookup_for_user(chat.user_id)
    publication = _publication_data(share_info)
    messages = _serialize_public_chat_rows(
        rows,
        file_lookup,
        approved_output_ids=set(publication["approved_output_ids"]),
    )
    return {
        "share_id": chat.share_id,
        "title": chat.title,
        "messages": messages,
        "updated_at": updated_at,
        "unchanged": False,
        "access_mode": access_mode,
        "has_password": bool(hashed),
        "expires_at": share_info.get("expires_at"),
        "share_access_token": share_access_token,
        "share_access_token_expires_at": share_access_token_exp.isoformat(),
    }



# -------------------
# Delete Chat Share
# -------------------
def delete_chat_share(user_id: str, chat_id: str, db):
    """Remove share info from a chat."""
    chat = get_chat(db, chat_id, user_id)

    chat.share = None
    chat.share_id = None
    chat.last_updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"ok": True}


def update_share_access_mode(user_id: str, chat_id: str, access_mode: str, db):
    """Change who may access an existing share while keeping share_id stable."""
    chat = get_chat(db, chat_id, user_id)

    if not chat.share_id:
        raise HTTPException(status_code=404, detail="Share not found")

    if _check_and_cleanup_expired(chat, db):
        raise HTTPException(status_code=404, detail="Share not found")

    normalized_access_mode = _normalize_share_access_mode(access_mode)
    if normalized_access_mode == CHAT_SHARE_ACCESS_INVITED:
        raise HTTPException(status_code=400, detail="Use the invite endpoint to create invited-user shares")
    _update_share_data(chat, db, access_mode=normalized_access_mode)
    return _serialize_chat_share_status(chat, db)



# -------------------
# Update Share Password
# -------------------
def update_share_password(user_id: str, chat_id: str, password: Optional[str], db, action: str = "add"):
    """
    Unified function to add, change, or remove share password.
    
    Args:
        action: "add", "change", or "remove"
    """
    chat = get_chat(db, chat_id, user_id)

    if not chat.share_id:
        raise HTTPException(status_code=404, detail="Share not found")

    if _check_and_cleanup_expired(chat, db):
        raise HTTPException(status_code=404, detail="Share not found")

    if action in {"add", "change"}:
        normalized_password = _normalize_share_password_for_storage(password)
        if not normalized_password:
            raise HTTPException(status_code=400, detail="Password cannot be empty")
        _update_share_data(chat, db, password=normalized_password)
    elif action == "remove":
        _update_share_data(chat, db, password=None)

    return _serialize_chat_share_status(chat, db)



# -------------------
# Update Share Expiry
# -------------------
def update_share_expiry(user_id: str, chat_id: str, expires_at: Optional[datetime], db, action: str = "set"):
    """
    Unified function to set, change, or remove share expiry.
    
    Args:
        action: "set", "change", or "remove"
    """
    chat = get_chat(db, chat_id, user_id)

    if not chat.share_id:
        raise HTTPException(status_code=404, detail="Share not found")

    # Check if already expired
    if _check_and_cleanup_expired(chat, db):
        raise HTTPException(status_code=404, detail="Share expired and has been removed")

    if action in {"set", "change"}:
        expires_iso = _validate_expiry(expires_at)
        _update_share_data(chat, db, expires_at=expires_iso)
        return _serialize_chat_share_status(chat, db)
    elif action == "remove":
        _update_share_data(chat, db, expires_at=None)
        return _serialize_chat_share_status(chat, db)

    raise HTTPException(status_code=400, detail="Invalid action")


def resolve_shared_chat_file_access(
    share_access_token: str,
    file_id: str,
    db,
    user_access_token: str | None = None,
    client_ip: str | None = None,
) -> dict:
    cleaned_file_id = str(file_id or "").strip()
    if not cleaned_file_id:
        raise HTTPException(status_code=400, detail="file_id is required")

    share_id, token_pwd_fp = verify_chat_share_access_token(db, share_access_token)
    chat, share_info = _resolve_shared_chat_or_404(db, share_id)
    expected_fp = _share_password_fingerprint(share_info.get("password"))
    if token_pwd_fp != expected_fp:
        raise HTTPException(status_code=401, detail="Invalid or expired share access token")
    _ensure_share_access_mode_allowed(share_info, db, user_access_token, client_ip)
    try:
        ensure_chat_share_file_access_enabled_for_user(chat.user_id, db)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Shared file not found")

    rows = _select_public_chat_rows(_get_ordered_chat_rows(db, chat.id), share_info)
    allowed_file_ids = _collect_public_attachment_file_ids_for_chat_rows(rows)
    if cleaned_file_id not in allowed_file_ids:
        raise HTTPException(status_code=404, detail="Shared file not found")

    file_info = get_file_info(chat.user_id, cleaned_file_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="Shared file not found")

    return {
        "share_id": share_id,
        "user_id": chat.user_id,
        "file_id": cleaned_file_id,
    }



# -------------------
# Regenerate Message
# -------------------
MAX_REGENERATIONS = 10

def regenerate_message(
    user_id: str,
    group_id: str,
    chat_id: str,
    user_message_id: str,
    model_id: str | None,
    byok: dict | None,
    custom_settings,
    db,
    skill_id: str | None = None,
    skill_ids: list[str] | None = None,
    note_ids: list[str] | None = None,
    prompt_ids: list[str] | None = None,
    chat_reference_ids: list[str] | None = None,
    retry_guidance: RetryGuidance | None = None,
    user_role: str | None = None,
    generation_id: str | None = None,
    subagent_targets: list[dict[str, str]] | None = None,
):
    """
    Regenerate an assistant response for a given user message.
    Creates a new assistant message with incremented retry_count.
    Only allows regeneration of the latest user message in the chat.
    """
    retry_guidance_meta = _retry_guidance_log_metadata(retry_guidance)
    logger.info(
        "[Regenerate] utils.start user=%s group=%s chat=%s user_msg=%s model=%s skill=%s skill_count=%s note_count=%s prompt_count=%s chat_reference_count=%s retry_guidance_mode=%s retry_guidance_preset=%s retry_guidance_custom_instruction_length=%s byok=%s custom_keys=%s",
        user_id,
        group_id,
        chat_id,
        user_message_id,
        model_id,
        skill_id,
        safe_count(skill_ids),
        safe_count(note_ids),
        safe_count(prompt_ids),
        safe_count(chat_reference_ids),
        retry_guidance_meta["mode"],
        retry_guidance_meta["preset"],
        retry_guidance_meta["custom_instruction_length"],
        bool(byok),
        list(custom_settings.keys()) if isinstance(custom_settings, dict) else None,
    )
    _ensure_group_chat_interaction_allowed(
        group_id,
        "allow_regenerate_response",
        db,
        "Response regeneration is disabled for your group.",
    )
    db_model = None
    rate_limit_admission = None
    rate_limit_context_token = None
    rate_limit_final_status = RATE_LIMIT_ADMISSION_COMPLETED
    rate_limit_admission_finalized = False

    def _finalize_regeneration_rate_limit_admission() -> None:
        """Release this regeneration's in-flight rate-limit slot once.

        The browser treats the successful terminal stream event as permission
        to dispatch the next queued turn. Releasing before that event prevents
        the next request from racing the prior regeneration's admission.
        """

        nonlocal rate_limit_admission_finalized, rate_limit_context_token
        if rate_limit_admission_finalized:
            return

        if rate_limit_context_token is not None:
            reset_current_rate_limit_admission_context(rate_limit_context_token)
            rate_limit_context_token = None

        finalize_rate_limit_admission(
            db,
            getattr(rate_limit_admission, "admission_id", None),
            final_status=rate_limit_final_status,
        )
        rate_limit_admission_finalized = True

    allow_custom_generation_parameter = False
    model_settings: dict = {}
    effective_skill_ids = _normalize_skill_ids(skill_id=skill_id, skill_ids=skill_ids)
    trusted_admin_skill_ids: list[str] = []
    selected_agent_id: str | None = None
    resolved_base_model_id: str | None = None
    agent_instruction: str | None = None
    agent_skill_ids: list[str] = []
    agent_asset_descriptors_by_category: dict[str, list[str]] = {
        "image": [],
        "audio": [],
        "video": [],
        "document": [],
    }
    effective_prompt_ids = _normalize_prompt_ids(prompt_ids=prompt_ids)
    prompts_enabled = bool(get_user_group_setting_value(user_id, "prompts", "enabled_prompts", db))
    if not prompts_enabled:
        effective_prompt_ids = []
    
    if byok:
        model_id = "byok"
    else:
        resolved_selection = resolve_chat_model_for_user(db, user_id=user_id, model_id=model_id)
        db_model = resolved_selection.base_model
        resolved_base_model_id = db_model.id
        if resolved_selection.model_kind == "agent" and resolved_selection.agent is not None:
            selected_agent_id = resolved_selection.agent.id
            agent_instruction = resolved_selection.agent_instruction
            agent_skill_ids = list(resolved_selection.agent_skill_ids or [])
            agent_asset_descriptors_by_category = (
                resolved_selection.asset_descriptors_by_category
                or agent_asset_descriptors_by_category
            )
        rate_limit_admission = _admit_rate_limited_chat_action(
            db,
            user_id=user_id,
            group_id=group_id,
            model=db_model,
            action_type=RATE_LIMIT_ADMISSION_ACTION_REGENERATE,
            chat_id=chat_id,
            user_message_id=user_message_id,
        )
        model_settings = db_model.settings if isinstance(db_model.settings, dict) else {}
        allow_custom_generation_parameter = coerce_allow_custom_flag(
            model_settings.get("allow_custom_generation_parameter")
        )

    if db_model and not byok:
        model_skill_ids = _extract_model_skill_ids(model_settings)
        trusted_admin_skill_ids = _resolve_trusted_admin_skill_ids(
            model_skill_ids=model_skill_ids,
            agent_skill_ids=agent_skill_ids,
        )
        effective_skill_ids = _resolve_generation_skill_ids(
            requested_skill_ids=effective_skill_ids,
            model_skill_ids=model_skill_ids,
            agent_skill_ids=agent_skill_ids,
        )

    # Verify chat exists and belongs to user
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    existing_chat_meta = getattr(chat, "meta", None)
    chat_meta = existing_chat_meta if isinstance(existing_chat_meta, dict) else {}
    selection_metadata = _build_agent_selection_metadata(
        selected_agent_id=selected_agent_id,
        resolved_base_model_id=resolved_base_model_id,
    )
    if selected_agent_id:
        chat_meta["agent_id"] = selected_agent_id
    else:
        chat_meta.pop("agent_id", None)
    if resolved_base_model_id:
        chat_meta["base_model_id"] = resolved_base_model_id
    else:
        chat_meta.pop("base_model_id", None)
    chat.meta = chat_meta
    db.commit()
    db.refresh(chat)

    # Verify the user message exists and belongs to this chat
    user_msg = db.query(ChatMessages).filter(
        ChatMessages.id == user_message_id,
        ChatMessages.chat_id == chat_id,
        ChatMessages.role == "user"
    ).first()
    if not user_msg:
        raise HTTPException(status_code=404, detail="User message not found!")
    logger.debug("[Regenerate] utils.user_msg_verified user=%s chat=%s user_msg=%s", user_id, chat_id, user_message_id)
    stored_chat_references = _extract_chat_reference_metadata_from_content(getattr(user_msg, "content", None))
    effective_chat_reference_ids = (
        _normalize_chat_reference_ids(chat_reference_ids)
        if isinstance(chat_reference_ids, list) and chat_reference_ids
        else [item["chat_id"] for item in stored_chat_references]
    )
    _, chat_reference_context = resolve_chat_reference_payload(
        user_id,
        db,
        effective_chat_reference_ids,
        current_chat_id=chat_id,
        project_id=getattr(chat, "project_id", None),
    )

    # Check that this is the latest user message in the chat
    latest_user_msg = (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == chat_id, ChatMessages.role == "user")
        .order_by(ChatMessages.created_at.desc(), ChatMessages.id.desc())
        .first()
    )
    latest_user_msg_id = None
    if isinstance(latest_user_msg, dict):
        latest_user_msg_id = latest_user_msg.get("id")
    else:
        latest_user_msg_id = getattr(latest_user_msg, "id", None)
    if latest_user_msg_id is None:
        db_type_module = str(getattr(type(db), "__module__", "") or "")
        if not db_type_module.startswith("sqlalchemy"):
            latest_user_msg_id = user_message_id
    if latest_user_msg_id != user_message_id:
        raise HTTPException(status_code=400, detail="Can only regenerate the latest message")
    logger.debug("[Regenerate] utils.latest_user_msg_confirmed user=%s chat=%s", user_id, chat_id)

    # Count existing assistant responses for this user message
    existing_responses = (
        db.query(ChatMessages)
        .filter(
            ChatMessages.chat_id == chat_id,
            ChatMessages.reference_id == user_message_id,
            ChatMessages.role == "assistant"
        )
        .all()
    )
    
    current_max_retry = max((r.retry_count or 0) for r in existing_responses) if existing_responses else -1
    new_retry_count = current_max_retry + 1
    
    if new_retry_count >= MAX_REGENERATIONS:
        raise HTTPException(status_code=400, detail=f"Maximum regenerations ({MAX_REGENERATIONS}) reached")
    logger.debug(
        "[Regenerate] utils.retry_count user=%s chat=%s new_retry=%s",
        user_id,
        chat_id,
        new_retry_count,
    )

    # Build chat history up to (but not including) the user message's assistant responses
    # We need all messages before this user message, plus the user message itself
    chat_history = (
        db.query(ChatMessages)
        .filter(ChatMessages.chat_id == chat_id)
        .order_by(ChatMessages.created_at.asc(), ChatMessages.id.asc())
        .all()
    )
    
    # Filter to include messages up to and including the user message,
    # but exclude any assistant responses to this user message
    filtered_history = []
    for msg in chat_history:
        if msg.role == "assistant" and msg.reference_id == user_message_id:
            continue  # Skip existing regenerations for this user message
        filtered_history.append(msg)
        if msg.id == user_message_id:
            break  # Stop after the user message

    skill_file_attachments = _collect_skill_file_attachment_ids(
        db,
        user_id,
        effective_skill_ids,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    merged_image_ids = _merge_attachment_ids(
        skill_file_attachments.get("images"),
        (agent_asset_descriptors_by_category.get("image") if not byok and selected_agent_id else None),
    )
    merged_video_ids = _merge_attachment_ids(
        skill_file_attachments.get("videos"),
        (agent_asset_descriptors_by_category.get("video") if not byok and selected_agent_id else None),
    )
    merged_audio_ids = _merge_attachment_ids(
        skill_file_attachments.get("audios"),
        (agent_asset_descriptors_by_category.get("audio") if not byok and selected_agent_id else None),
    )
    merged_document_ids = _merge_attachment_ids(
        skill_file_attachments.get("documents"),
        (agent_asset_descriptors_by_category.get("document") if not byok and selected_agent_id else None),
    )

    if any((merged_image_ids, merged_video_ids, merged_audio_ids, merged_document_ids)):
        for idx in range(len(filtered_history) - 1, -1, -1):
            msg = filtered_history[idx]
            if getattr(msg, "id", None) != user_message_id:
                continue

            merged_content = _merge_attachment_ids_into_content(
                getattr(msg, "content", None),
                image_ids=merged_image_ids,
                video_ids=merged_video_ids,
                audio_ids=merged_audio_ids,
                document_ids=merged_document_ids,
            )

            filtered_history[idx] = {
                "id": getattr(msg, "id", None),
                "chat_id": getattr(msg, "chat_id", None),
                "model_id": getattr(msg, "model_id", None),
                "role": getattr(msg, "role", None),
                "content": merged_content,
                "reference_id": getattr(msg, "reference_id", None),
                "generation": getattr(msg, "generation", None),
                "thinking": getattr(msg, "thinking", None),
                "retry_count": getattr(msg, "retry_count", None),
                "created_at": getattr(msg, "created_at", None),
            }
            break

    # Auto-cancel any active generation for this chat
    status = stream_hub.get_status(chat_id)
    if status.get("active"):
        prev_gen = status.get("generation_id")
        if prev_gen:
            cancel_registry.cancel(prev_gen)
            logger.debug(
                "[Regenerate] utils.cancelled_active_generation user=%s chat=%s prev_gen=%s",
                user_id,
                chat_id,
                prev_gen,
            )

    # Create a new generation id and start hub
    generation_id = str(generation_id or "").strip() or str(uuid.uuid4())
    stream_hub.start(generation_id, chat_id)
    cancel_registry.set_active(chat_id, generation_id)
    logger.debug(
        "[Regenerate] utils.new_generation user=%s chat=%s gen_id=%s",
        user_id,
        chat_id,
        generation_id,
    )
    
    # Emit start event
    start_line = json.dumps({"t": "s", "d": generation_id})
    stream_hub.publish_line(generation_id, start_line)
    yield start_line + "\n"

    # Emit regeneration info
    regen_line = json.dumps({"t": "regen", "d": {"retry_count": new_retry_count, "user_message_id": user_message_id}})
    stream_hub.publish_line(generation_id, regen_line)
    yield regen_line + "\n"
    logger.debug(
        "[Regenerate] utils.emitted_regen_event user=%s chat=%s gen_id=%s retry=%s",
        user_id,
        chat_id,
        generation_id,
        new_retry_count,
    )

    if byok:
        provider = normalize_provider_value(byok.get("provider"))
    else:
        provider = normalize_provider_value(db_model.provider)

    try:
        _assert_generation_provider_allowed(
            db,
            provider=provider,
            db_model=db_model,
            byok=byok,
            feature="chat retry generation",
        )
    except HTTPException as exc:
        rate_limit_final_status = RATE_LIMIT_ADMISSION_FAILED
        is_admin = is_admin_role(user_role)
        show_raw_error = is_admin or bool(byok)
        error_message = str(exc.detail) if show_raw_error else "An error occurred during generation. Please try again."
        err_line = json.dumps({"t": "e", "d": error_message, "admin_detail": str(exc.detail) if is_admin else None})
        stream_hub.publish_line(generation_id, err_line)
        yield err_line + "\n"
        _finalize_regeneration_rate_limit_admission()
        return

    if rate_limit_admission:
        rate_limit_context_token = set_current_rate_limit_admission_context(rate_limit_admission)

    skill_content = _compose_skill_content(
        db,
        user_id,
        effective_skill_ids,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    prompt_content = _compose_prompt_content(db, user_id, effective_prompt_ids)
    personality_section = get_user_personality_system_instruction_section(user_id, db)
    if prompt_content:
        skill_content = f"{skill_content}\n\n{prompt_content}" if skill_content else prompt_content
    system_instruction_sections = _build_system_instruction_sections(
        personality_section=personality_section,
        skill_content=skill_content,
        agent_instruction=agent_instruction,
        retry_guidance=retry_guidance,
    )
    canvas_update_context = _build_canvas_user_edit_user_context(
        db,
        user_id=user_id,
        chat_history=filtered_history,
    )
    notes_update_context = _build_notes_user_edit_context(
        db,
        user_id=user_id,
        chat_history=filtered_history,
    )
    chat_reference_context = _join_latest_user_context(chat_reference_context, canvas_update_context, notes_update_context)
    assistant_metadata = _build_retry_guidance_metadata(retry_guidance)
    assistant_metadata.update(selection_metadata)

    llm_metric_provider = _metric_provider_name(provider, byok)
    llm_metric_model = _metric_model_name(db_model, model_id, byok)
    llm_metric_started_at = time.monotonic()
    llm_metric_success = True
    llm_metric_error_type: str | None = None
    llm_metric_input_tokens = 0
    llm_metric_output_tokens = 0

    try:
        provider_settings_override = _build_provider_settings_override(
            custom_settings,
            allow_custom_generation_parameter=allow_custom_generation_parameter,
            subagent_targets=subagent_targets,
        )

        upstream = call_provider_chat(
            ProviderRequest(
                request_type=REQUEST_TYPE_CHAT,
                db=db,
                provider=provider,
                model=db_model,
                chat_history=filtered_history,
                user_id=user_id,
                project_id=chat.project_id,
                generation_id=generation_id,
                temp_request_flag=False,
                byok=byok,
                settings_override=provider_settings_override,
                reference_id=user_message_id,
                system_instruction_sections=system_instruction_sections,
                assistant_metadata=assistant_metadata,
                note_ids=note_ids,
                chat_reference_context=chat_reference_context,
                retry_count=new_retry_count,
                user_role=user_role,
                extra={
                    "chat_id": chat_id,
                    "provider_callables": {
                        "google_aistudio": aistudio_chat,
                        "ollama": ollama_chat,
                        "openai": openai_chat,
                        "openai_responses": openai_chat,
                        "xai": openai_chat,
                        "microsoft_azure": openai_chat,
                        "lmstudio": openai_chat,
                        "openai_chat_completions": openai_chat_completions_chat,
                        "openrouter": openrouter_chat,
                        "anthropic": anthropic_chat,
                        "anthropic_base": anthropic_chat,
                    },
                },
            )
        )

        pending_done_line: str | None = None
        for line in _require_provider_stream_terminal(upstream, generation_id):
            line_success, line_error_type, line_input_tokens, line_output_tokens = _extract_llm_metric_stream_state(line)
            if not line_success:
                llm_metric_success = False
                llm_metric_error_type = llm_metric_error_type or line_error_type
            llm_metric_input_tokens = max(llm_metric_input_tokens, line_input_tokens)
            llm_metric_output_tokens = max(llm_metric_output_tokens, line_output_tokens)
            if _is_successful_generation_done_line(line):
                pending_done_line = line
                continue
            stream_hub.publish_line(generation_id, line)
            yield line
            if redacted_debug_logging_enabled(_CHAT_STREAM_DEBUG_FLAG):
                logger.debug(
                    "[Regenerate] utils.forwarded_event user=%s chat=%s gen_id=%s meta=%s",
                    user_id,
                    chat_id,
                    generation_id,
                    stream_line_metadata(line),
                )
        if pending_done_line is not None:
            _record_completion_before_stream_publish(db, chat_id, generation_id, pending_done_line)
            _finalize_regeneration_rate_limit_admission()
            stream_hub.publish_line(generation_id, pending_done_line)
            yield pending_done_line

    except Exception as e:
        llm_metric_success = False
        llm_metric_error_type = llm_metric_error_type or type(e).__name__
        rate_limit_final_status = RATE_LIMIT_ADMISSION_FAILED
        err_line = _build_generation_error_line(
            e,
            user_role=user_role,
            byok=byok,
        )
        stream_hub.publish_line(generation_id, err_line)
        yield err_line + "\n"
        logger.error(
            "[Regenerate] utils.exception user=%s chat=%s gen_id=%s meta=%s",
            user_id,
            chat_id,
            generation_id,
            exception_metadata(e),
        )
    finally:
        record_llm_request_metric(
            provider=llm_metric_provider,
            model=llm_metric_model,
            success=llm_metric_success,
            duration_ms=(time.monotonic() - llm_metric_started_at) * 1000,
            input_tokens=llm_metric_input_tokens,
            output_tokens=llm_metric_output_tokens,
            error_type=llm_metric_error_type,
        )
        stream_hub.mark_done(generation_id)
        cancel_registry.clear(generation_id)
        _finalize_regeneration_rate_limit_admission()
        logger.debug(
            "[Regenerate] utils.finished user=%s chat=%s gen_id=%s",
            user_id,
            chat_id,
            generation_id,
        )
