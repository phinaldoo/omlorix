import hashlib
import logging

import anyio
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse, Response
from fastapi import APIRouter, Depends, Request, HTTPException, Body, Query, UploadFile, File, Form
from sqlalchemy.orm import Session
from copy import deepcopy
from urllib.parse import urlsplit
from app.database import SessionLocal
from app.dependencies import get_db, get_db_log, verified_user
from app.utils.db import release_db_session_before_long_wait
from app.llm.models import admit_user_tool_rate_limit
from app.llm.schemas import ProviderEnum, normalize_provider_value, provider_api_key_is_optional
from app.llm.byok_credentials import ByokCredentialTokenError, resolve_byok_credential_token
from app.chats.download import prepare_chat_download
from app.chats.models import (
    Chats,
    apply_chat_unread_state,
    get_accessible_chat_attention,
    mark_chat_read_for_user,
    rename_chat, 
    delete_chat, 
    delete_all_chats, 
    duplicate_chat, 
    delete_chat_message as delete_chat_message_record,
    edit_chat_message,
    update_chat_project,
    archive_chat,
    unarchive_chat,
    get_archived_chats,
    toggle_message_bookmark,
    get_bookmarked_messages,
)
from app.chats.schemas import (
    SendChatRequest,
    SendChatRequestModelSettings,
    SaveTemporaryChatRequest,
    MarkdownCodeExecutionRequest,
    MarkdownCodeExecutionResponse,
    VegaPreviewResourceRequest,
    ShareChatRequest,
    InviteChatUsersRequest,
    InviteChatUsersResponse,
    ChatSharePublicationOptionsResponse,
    UpdateChatSharePublicationRequest,
    AssistantReadAloudRequest,
    AccessSharedChatRequest,
    DeleteShareRequest,
    ChangeShareAccessModeRequest,
    RemoveSharePasswordRequest,
    AddSharePasswordRequest,
    ChangeSharePasswordRequest,
    ToggleBookmarkRequest,
    CreateShareExpiryRequest,
    ChangeShareExpiryRequest,
    DeleteShareExpiryRequest,
    PinChatRequest,
    UnpinChatRequest,
    MovePinnedChatRequest,
    UpdateChatProjectRequest,
    ArchiveChatRequest,
    UnarchiveChatRequest,
    EditMessageRequest,
    RegenerateMessageRequest,
    Chat,
    ChatListPage,
    ChatReadResponse,
    ChatAttentionQuery,
    ChatAttentionQueryResponse,
    ChatReferenceCandidatePage,
    ChatGPTArchiveImportResult,
)
from app.chats.read_aloud import (
    get_or_create_read_aloud_file,
    get_owned_assistant_message_read_aloud_text,
    sanitize_read_aloud_text,
)
from app.chats.meeting_transcripts import (
    create_meeting_transcript_off_event_loop as create_meeting_transcript,
)
from app.chats.vega_preview import fetch_vega_preview_resource
from app.chats.streaming import stream_hub, cancel_registry
from app.chats.utils import (
    send_message,
    resolve_chat_model_for_user,
    save_temporary_chat,
    regenerate_message,
    list_chats,
    list_chats_paginated,
    MAX_CHAT_PAGE_LIMIT,
    list_chat_reference_candidates,
    search_chats,
    branch_chat,
    get_chat_for_read,
    get_chat_messages,

    share_chat,
    get_share_status,
    get_share_publication_options,
    update_share_publication,
    get_shared_chat_messages,
    resolve_shared_chat_file_access,
    delete_chat_share,
    update_share_access_mode,
    update_share_password,
    update_share_expiry,
    pin_chat,
    unpin_chat,
    move_pinned_chat,
)
from app.logging.models import create_audit_log, get_audit_request_ip, _hash_text
from app.logging.privacy import (
    exception_metadata,
    redacted_debug_logging_enabled,
    safe_count,
    stream_line_metadata,
)
from app.files.utils import download_file
from app.users.models import User
from app.users.sharing import resolve_invitable_users_for_sharing
from app.userNotifications.models import create_user_notification
from app.groups.init import ensure_data_control_permission, get_user_group_setting_value
from app.projects.models import (
    has_project_access,
    can_send_message_in_chat,
    ensure_project_access_for_chat_send,
)
from app.utils.background import background_task_executor
from app.utils.client_ip import extract_client_ip_from_request, resolve_trusted_proxy_networks
from app.utils.cache_headers import apply_no_store_headers
from app.workers.generation import (
    cancel_queued_generation,
    enqueue_generation_job,
    external_generation_enabled,
)
from app.workers.operations import (
    enqueue_import_job,
    stage_import_stream,
    wait_for_operations_result,
)

MAX_REFERENCE_PARTS = 30

chats_router = APIRouter(prefix="/api/v1/chats", tags=["chats"])

logger = logging.getLogger(__name__)

_CHAT_STREAM_DEBUG_FLAG = "OMLORIX_LOG_REDACTED_CHAT_STREAMS"


def _custom_settings_override(custom_settings: SendChatRequestModelSettings | dict | None) -> dict | None:
    if custom_settings is None:
        return None
    if isinstance(custom_settings, SendChatRequestModelSettings):
        return custom_settings.as_override_dict()
    if isinstance(custom_settings, dict):
        return SendChatRequestModelSettings.model_validate(custom_settings).as_override_dict()
    return None


def _normalize_audit_url_host(value: object) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None

    try:
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    except ValueError:
        return None

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return None

    try:
        port = parsed.port
    except ValueError:
        port = None

    if port is None:
        return host
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _build_byok_audit_details(byok: dict | None) -> dict:
    if not isinstance(byok, dict) or not byok:
        return {}

    details: dict[str, str] = {}
    provider_type = normalize_provider_value(byok.get("provider"))
    if provider_type:
        details["byok_provider_type"] = provider_type

    model_name = str(byok.get("model_name") or "").strip()
    if model_name:
        details["byok_model_name"] = model_name[:255]

    provider_instance_id = str(byok.get("provider_id") or "").strip()
    if provider_instance_id:
        details["byok_provider_instance_hash"] = _hash_text(
            provider_instance_id,
            prefix="byok_provider_hash",
            keep=16,
        )

    provider_settings = byok.get("provider_settings") if isinstance(byok.get("provider_settings"), dict) else {}
    base_url_host = _normalize_audit_url_host(
        byok.get("base_url")
        or byok.get("azure_endpoint")
        or provider_settings.get("base_url")
    )
    if base_url_host:
        details["byok_base_url_host"] = base_url_host

    return details


def _retry_guidance_log_metadata(retry_guidance) -> dict:
    if retry_guidance is None:
        return {"mode": None, "preset": None, "custom_instruction_length": 0}
    instruction = getattr(retry_guidance, "instruction", None)
    mode = getattr(getattr(retry_guidance, "mode", None), "value", getattr(retry_guidance, "mode", None))
    preset = getattr(getattr(retry_guidance, "preset", None), "value", getattr(retry_guidance, "preset", None))
    return {
        "mode": mode,
        "preset": preset,
        "custom_instruction_length": len(instruction or "") if isinstance(instruction, str) else 0,
    }


def _log_redacted_stream_line(message: str, *, user_id: str, chat_id: str | None, line: str) -> None:
    if not redacted_debug_logging_enabled(_CHAT_STREAM_DEBUG_FLAG):
        return
    logger.debug(message, user_id, chat_id, stream_line_metadata(line))


def _ensure_byok_allowed_for_user(user_id: str, db: Session, byok: dict | None) -> None:
    """Authorize BYOK and replace its sealed token with an in-memory API key.

    The request dictionary is deliberately mutated in place because the chat
    routes forward that same object into background generation threads.  The
    raw key exists only in backend process memory after this boundary.
    """

    if not byok:
        return
    allow_byok = bool(get_user_group_setting_value(user_id, "chat", "allow_byok", db))
    if not allow_byok:
        raise HTTPException(status_code=403, detail="Bring Your Own Key is disabled for your group.")

    # Raw credentials are accepted only by the dedicated sealing endpoint.
    # Rejecting the field here prevents older or malicious clients from
    # bypassing the new browser-to-backend credential boundary.
    if "api_key" in byok:
        raise HTTPException(status_code=400, detail={"code": "byok_credential_unavailable"})

    try:
        provider = ProviderEnum(normalize_provider_value(byok.get("provider")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "byok_credential_unavailable"}) from exc

    provider_id = str(byok.get("provider_id") or "").strip()
    credential_token = str(byok.get("credential_token") or "").strip()
    if not provider_id:
        raise HTTPException(status_code=400, detail={"code": "byok_credential_unavailable"})

    if credential_token:
        try:
            api_key = resolve_byok_credential_token(
                credential_token,
                user_id=user_id,
                provider=provider.value,
                provider_id=provider_id,
            )
        except ByokCredentialTokenError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "byok_credential_unavailable"},
            ) from exc
    elif provider_api_key_is_optional(provider):
        api_key = ""
    else:
        raise HTTPException(status_code=400, detail={"code": "byok_credential_unavailable"})

    byok.pop("credential_token", None)
    byok["api_key"] = api_key


def _ensure_bookmarks_enabled_for_user(user_id: str, db: Session) -> None:
    if not bool(get_user_group_setting_value(user_id, "bookmarks", "enabled_bookmarks", db)):
        raise HTTPException(status_code=403, detail="Bookmarks are disabled for your group.")


def _log_chat_share_event(
    db_log: Session,
    user_id: str | None,
    action: str,
    request: Request | None = None,
    client_ip: str | None = None,
    details: dict | None = None,
):
    """Create an audit log entry for a chat share event."""
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=client_ip or get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
        category="share",
    )


def _log_chat_event(
    db_log: Session,
    request: Request | None,
    user_id: str,
    action: str,
    details: dict | None = None,
):
    """Create an audit log entry for a chat action without storing message content."""
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
        category="chat",
    )


def _extract_bearer_token(value: str | None) -> str:
    header_value = str(value or "").strip()
    if header_value.lower().startswith("bearer "):
        return header_value[7:].strip()
    return header_value


def _shared_chat_audit_subject(db: Session, share_id: str | None) -> dict:
    cleaned_share_id = str(share_id or "").strip()
    if not cleaned_share_id:
        return {}
    chat = db.query(Chats).filter(Chats.share_id == cleaned_share_id).first()
    if not chat:
        return {"share_id": cleaned_share_id}
    return {
        "share_id": cleaned_share_id,
        "chat_id": chat.id,
        "owner_user_id": chat.user_id,
    }


def _get_user_display_name(user_obj) -> str:
    if not user_obj:
        return "Someone"
    if getattr(user_obj, "first_name", None) and getattr(user_obj, "last_name", None):
        return f"{user_obj.first_name} {user_obj.last_name}"
    if getattr(user_obj, "first_name", None):
        return user_obj.first_name
    email = getattr(user_obj, "email", None)
    return email.split("@")[0] if email else "Someone"


def _chatgpt_archive_display_name(filename: str | None) -> str:
    """Return a bounded basename safe to persist in imported chat metadata."""
    cleaned = str(filename or "").replace("\x00", "").replace("\\", "/")
    return (cleaned.rsplit("/", 1)[-1].strip() or "chatgpt-export.zip")[:255]


def _chat_invitation_title(chat: Chats | None) -> str:
    title = str(getattr(chat, "title", "") or "").strip() or "Untitled chat"
    return title if len(title) <= 80 else f"{title[:77]}..."


# -------------------
# Import ChatGPT archive
# -------------------
@chats_router.post("/import/chatgpt", response_model=ChatGPTArchiveImportResult)
def import_chatgpt_archive_route(
    request: Request,
    archive: UploadFile = File(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Import the authenticated user's bounded ChatGPT export ZIP archive."""
    ensure_data_control_permission(
        user.id,
        "allow_user_data",
        db,
        detail="ChatGPT archive import is disabled for your group's data controls.",
    )
    archive_name = _chatgpt_archive_display_name(archive.filename)
    try:
        archive.file.seek(0)
        staged_name = stage_import_stream(
            archive.file,
            extension="zip",
            principal_id=user.id,
            import_kind="import_chatgpt",
        )
    finally:
        archive.file.close()
    job = enqueue_import_job(
        db,
        kind="import_chatgpt",
        staged_name=staged_name,
        user_id=user.id,
        options={"archive_name": archive_name},
    )
    result = wait_for_operations_result(job)

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="IMPORT_CHATGPT_ARCHIVE",
        details={
            key: int(result.get(key, 0))
            for key in (
                "imported_chats",
                "imported_messages",
                "imported_files",
                "skipped_chats",
                "skipped_duplicates",
                "shared_index_entries",
            )
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )
    return result


# -------------------
# Send chat message
# -------------------
@chats_router.post("/send")
def send(
    payload: SendChatRequest,
    request: Request,
    custom_settings: SendChatRequestModelSettings | None = Body(default=None),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
    byok: dict | None = None,
):
    _ensure_byok_allowed_for_user(user.id, db, byok)
    if not byok and not str(payload.model_id or "").strip():
        _log_chat_event(
            db_log,
            request,
            user.id,
            "CHAT_MESSAGE_SEND_DENIED",
            {"chat_id": str(payload.chat_id or "").strip() or None, "reason": "model_required"},
        )
        raise HTTPException(status_code=400, detail={"code": "chat_model_required"})
    logger.info(
        "[chat-send] request received user=%s group=%s chat_id=%s model_id=%s temp_chat=%s message_length=%s image_count=%s video_count=%s audio_count=%s document_count=%s skill_id=%s skill_count=%s note_count=%s prompt_count=%s reference_part_count=%s chat_reference_count=%s custom_settings_keys=%s byok=%s",
        user.id,
        user.group_id,
        payload.chat_id,
        payload.model_id,
        bool(payload.temp_chat),
        len(str(payload.message or "")),
        len(payload.image_ids or []),
        len(payload.video_ids or []),
        len(payload.audio_ids or []),
        len(payload.document_ids or []),
        payload.skill_id,
        safe_count(payload.skill_ids),
        safe_count(payload.note_ids),
        safe_count(payload.prompt_ids),
        safe_count(payload.reference_parts),
        safe_count(payload.chat_reference_ids),
        sorted((_custom_settings_override(custom_settings) or {}).keys()),
        bool(byok),
    )
    normalized_chat_id = str(payload.chat_id or "").strip()
    if normalized_chat_id and not can_send_message_in_chat(db, user.id, normalized_chat_id):
        _log_chat_event(
            db_log,
            request,
            user.id,
            "CHAT_MESSAGE_SEND_DENIED",
            {"chat_id": normalized_chat_id, "reason": "chat_unavailable"},
        )
        raise HTTPException(status_code=404, detail="Chat not found!")

    current_chat = None
    if normalized_chat_id:
        current_chat = (
            db.query(Chats)
            .filter(Chats.id == normalized_chat_id, Chats.user_id == user.id)
            .first()
        )
    current_chat_project_id = str(getattr(current_chat, "project_id", None) or "").strip() or None
    try:
        normalized_project_id, current_chat_project_id = ensure_project_access_for_chat_send(
            db,
            user.id,
            project_id=payload.project_id,
            chat=current_chat,
        )
    except HTTPException as exc:
        if exc.status_code == 404 and exc.detail == "Project not found":
            _log_chat_event(
                db_log,
                request,
                user.id,
                "CHAT_MESSAGE_SEND_DENIED",
                {
                    "chat_id": normalized_chat_id or None,
                    # Prefer a persisted server-owned scope when one exists.
                    # Scope-less chats retain the normalized request fallback.
                    "project_id": (
                        current_chat_project_id
                        if current_chat_project_id is not None
                        else str(payload.project_id or "").strip() or None
                    ),
                    "reason": "project_unavailable",
                },
            )
        raise

    from app.tools.subagents.runtime import validate_subagent_target_selection

    authorized_subagent_targets = validate_subagent_target_selection(
        db,
        user_id=user.id,
        targets=payload.subagent_targets,
    )

    if not byok:
        try:
            # Resolve and authorize provider-backed models and agents before
            # opening the stream. Identifier, policy, ownership, and provider-
            # availability failures must retain their real HTTP status.
            resolve_chat_model_for_user(
                db,
                user_id=user.id,
                model_id=payload.model_id,
            )
        except HTTPException as exc:
            _log_chat_event(
                db_log,
                request,
                user.id,
                "CHAT_MESSAGE_SEND_DENIED",
                {
                    "chat_id": normalized_chat_id or None,
                    "reason": "model_unavailable",
                    "status_code": exc.status_code,
                },
            )
            raise

    _log_chat_event(
        db_log,
        request,
        user.id,
        "CHAT_MESSAGE_SENT",
        {
            "chat_id": payload.chat_id,
            "model_id": payload.model_id,
            "temp_chat": bool(payload.temp_chat),
            "message_length": len(str(payload.message or "")),
            "image_count": len(payload.image_ids or []),
            "video_count": len(payload.video_ids or []),
            "audio_count": len(payload.audio_ids or []),
            "document_count": len(payload.document_ids or []),
            "skill_id": payload.skill_id,
            "skill_count": len(payload.skill_ids or []),
            "note_count": len(payload.note_ids or []),
            "prompt_count": len(payload.prompt_ids or []),
            "subagent_target_mode": "automatic" if authorized_subagent_targets is None else "selected",
            "subagent_target_count": len(authorized_subagent_targets or []),
            "byok": bool(byok),
            **_build_byok_audit_details(byok),
        },
    )
    """
    Send a chat message and stream the assistant's response.

    Body: `SendChatRequest` with fields like `chat_id`, `model_id`, `message`, optional media/document IDs,
    optional `project_id`, and `temp_chat`.

    Returns: a text/plain streaming response (Server-Sent style lines) containing tokens/chunks and events.

    Implementation detail:
    - We start the actual LLM generation in a background daemon thread which publishes to `stream_hub` and writes
      to the DB. This continues even if the client disconnects.
    - The HTTP stream forwards the initial start lines (so the client learns `generation_id`) and then switches to
      `stream_hub.subscribe_async(generation_id, from_seq=last_seq_sent)` to avoid duplicates.
    """
    import json
    import queue
    from contextlib import suppress

    client_generation_id = str(payload.generation_id)
    if not cancel_registry.reserve(client_generation_id, user.id):
        raise HTTPException(status_code=409, detail={"code": "generation_id_conflict"})

    if external_generation_enabled():
        # Reserve the shared stream before enqueueing. The subscriber can then
        # attach immediately while any generation replica claims the job.
        stream_hub.start(
            client_generation_id,
            normalized_chat_id,
            metadata={"state": "queued", "user_id": user.id},
        )
        try:
            enqueue_generation_job(
                db,
                kind="send",
                user_id=user.id,
                generation_id=client_generation_id,
                request_payload=payload.model_dump(mode="json"),
                custom_settings=_custom_settings_override(custom_settings),
                byok=byok,
                normalized_project_id=normalized_project_id,
                subagent_targets=authorized_subagent_targets,
            )
        except Exception as exc:
            logger.error(
                "[chat-send] failed to enqueue external generation user=%s chat_id=%s meta=%s",
                user.id,
                normalized_chat_id,
                exception_metadata(exc),
            )
            stream_hub.publish_line(
                client_generation_id,
                json.dumps(
                    {
                        "t": "e",
                        "d": "Assistant response failed",
                        "code": "generation_queue_unavailable",
                        "i18n_key": "chat_sr_response_failed",
                    },
                    separators=(",", ":"),
                ),
            )
            stream_hub.mark_done(client_generation_id, status="failed")
            cancel_registry.clear(client_generation_id)
            raise HTTPException(
                status_code=503,
                detail={"code": "generation_queue_unavailable"},
            ) from exc
        return StreamingResponse(
            stream_hub.subscribe_async(client_generation_id, from_seq=0),
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )

    # We will forward initial lines (including the start event) to the client, then switch to hub subscribe.
    initial_lines_q: "queue.Queue[str]" = queue.Queue(maxsize=10)
    switch_info_q: "queue.Queue[object]" = queue.Queue(maxsize=1)  # (generation_id, last_seq) or sentinel

    def _normalize(line: str) -> str:
        return line if line.endswith("\n") else f"{line}\n"

    def _enqueue_initial(line: str) -> None:
        with suppress(Exception):
            initial_lines_q.put_nowait(_normalize(line))

    def _signal_switch(gen_id: str | None, last_seq: int) -> None:
        if gen_id and switch_info_q.empty():
            with suppress(Exception):
                switch_info_q.put_nowait((gen_id, last_seq))

    def _publish_error(detail: str | dict) -> None:
        logger.error(
            "[chat-send] publishing pre-stream error user=%s chat_id=%s detail_type=%s",
            user.id,
            payload.chat_id,
            type(detail).__name__,
        )
        with suppress(Exception):
            initial_lines_q.put_nowait(_normalize(json.dumps({"t": "e", "d": detail})))

    def _drain_initial_lines():
        while True:
            try:
                yield initial_lines_q.get_nowait()
            except queue.Empty:
                break

    def _bg_consume():
        """Consume the upstream generator fully in background so it keeps running after HTTP disconnects.
        Forward only the very first start line (and any prior lines like new_chat id) via initial_lines_q
        so the HTTP stream can reveal generation_id before switching to subscribe.
        """
        saw_start = False
        last_seq = 0
        gen_id: str | None = None
        session = SessionLocal()
        try:
            logger.debug("[chat-send] background consumer starting user=%s chat_id=%s model_id=%s", user.id, payload.chat_id, payload.model_id)
            upstream = send_message(
                user.id,
                user.group_id,
                normalized_chat_id,
                payload.message,
                payload.image_ids,
                payload.video_ids,
                payload.audio_ids,
                payload.document_ids,
                normalized_project_id,
                payload.temp_chat,
                payload.model_id,
                byok,
                _custom_settings_override(custom_settings),
                session,
                skill_id=payload.skill_id,
                skill_ids=payload.skill_ids,
                note_ids=payload.note_ids,
                prompt_ids=payload.prompt_ids,
                reference_parts=payload.reference_parts,
                chat_reference_ids=payload.chat_reference_ids,
                user_role=user.role,
                generation_id=client_generation_id,
                subagent_targets=authorized_subagent_targets,
            )

            for line in upstream:
                _log_redacted_stream_line(
                    "[chat-send] upstream event user=%s chat_id=%s meta=%s",
                    user_id=user.id,
                    chat_id=payload.chat_id,
                    line=line,
                )
                obj = None
                with suppress(Exception):
                    obj = json.loads(line)
                if isinstance(obj, dict):
                    with suppress(Exception):
                        if "seq" in obj:
                            last_seq = int(obj["seq"])
                    event_type = obj.get("t") or obj.get("type")
                    if not saw_start and event_type in ("s", "start"):
                        data = obj.get("d") or {}
                        gen_id = data if isinstance(data, str) else obj.get("generation_id") or gen_id
                        saw_start = True

                if not saw_start:
                    _enqueue_initial(line)
                else:
                    _signal_switch(gen_id, last_seq)
        except Exception as exc:
            logger.error(
                "[chat-send] background consumer failed user=%s chat_id=%s meta=%s",
                user.id,
                payload.chat_id,
                exception_metadata(exc),
            )
            if gen_id:
                _signal_switch(gen_id, last_seq)
            else:
                _publish_error(getattr(exc, "detail", str(exc)))
                if switch_info_q.empty():
                    with suppress(Exception):
                        switch_info_q.put_nowait(None)
        finally:
            if not saw_start:
                # Validation/setup failures that occur before the start event
                # must not leave a client-owned ID reserved until its TTL.
                cancel_registry.clear(client_generation_id)
            with suppress(Exception):
                session.close()
            logger.debug("[chat-send] background consumer finished user=%s chat_id=%s", user.id, payload.chat_id)

    # Use the global executor to prevent unbounded thread creation
    future = background_task_executor.submit(_bg_consume)

    async def http_stream():
        # Drain any initial lines (before start) while waiting for the start signal
        gen_id = None
        last_seq = 0
        while gen_id is None:
            try:
                info = switch_info_q.get_nowait()
            except queue.Empty:
                for il in _drain_initial_lines():
                    _log_redacted_stream_line(
                        "[chat-send] draining initial event while waiting user=%s chat_id=%s meta=%s",
                        user_id=user.id,
                        chat_id=payload.chat_id,
                        line=il,
                    )
                    yield il
                if not future.done():
                    await anyio.sleep(0.05)
                    continue

                # The producer can publish switch information between the
                # first non-blocking read and this completion check. Re-read
                # before treating a completed future as a pre-stream exit.
                try:
                    info = switch_info_q.get_nowait()
                except queue.Empty:
                    logger.debug("[chat-send] background future completed before stream switch user=%s chat_id=%s", user.id, payload.chat_id)
                    for il in _drain_initial_lines():
                        _log_redacted_stream_line(
                            "[chat-send] draining final initial event user=%s chat_id=%s meta=%s",
                            user_id=user.id,
                            chat_id=payload.chat_id,
                            line=il,
                        )
                        yield il
                    exc = future.exception()
                    if exc:
                        logger.error(
                            "[chat-send] future exception before stream switch user=%s chat_id=%s meta=%s",
                            user.id,
                            payload.chat_id,
                            exception_metadata(exc),
                        )
                        raise exc
                    return
            except Exception as exc:
                logger.error(
                    "[chat-send] http_stream aborted unexpectedly user=%s chat_id=%s meta=%s",
                    user.id,
                    payload.chat_id,
                    exception_metadata(exc),
                )
                for il in _drain_initial_lines():
                    yield il
                return

            if info is None:
                logger.error("[chat-send] switch info sentinel received user=%s chat_id=%s; draining queued initial lines before closing", user.id, payload.chat_id)
                for il in _drain_initial_lines():
                    _log_redacted_stream_line(
                        "[chat-send] draining initial event on sentinel user=%s chat_id=%s meta=%s",
                        user_id=user.id,
                        chat_id=payload.chat_id,
                        line=il,
                    )
                    yield il
                return
            if not isinstance(info, tuple) or len(info) != 2:
                logger.error("[chat-send] switch info malformed user=%s chat_id=%s info=%s; draining queued initial lines before closing", user.id, payload.chat_id, info)
                for il in _drain_initial_lines():
                    _log_redacted_stream_line(
                        "[chat-send] draining initial event on malformed info user=%s chat_id=%s meta=%s",
                        user_id=user.id,
                        chat_id=payload.chat_id,
                        line=il,
                    )
                    yield il
                return
            gen_id, last_seq = info
            logger.debug("[chat-send] switching to live stream user=%s chat_id=%s gen_id=%s last_seq=%s", user.id, payload.chat_id, gen_id, last_seq)

        for il in _drain_initial_lines():
            _log_redacted_stream_line(
                "[chat-send] draining post-switch initial event user=%s chat_id=%s meta=%s",
                user_id=user.id,
                chat_id=payload.chat_id,
                line=il,
            )
            yield il

        if gen_id is None:
            logger.error("[chat-send] no generation id resolved user=%s chat_id=%s", user.id, payload.chat_id)
            return

        async for chunk in stream_hub.subscribe_async(gen_id, from_seq=last_seq):
            if redacted_debug_logging_enabled(_CHAT_STREAM_DEBUG_FLAG):
                logger.debug(
                    "[chat-send] streaming event gen_id=%s user=%s chat_id=%s meta=%s",
                    gen_id,
                    user.id,
                    payload.chat_id,
                    stream_line_metadata(chunk),
                )
            yield chunk

    return StreamingResponse(http_stream(), media_type="text/plain")


@chats_router.post("/save-temp")
def save_temp_chat_route(
    payload: SaveTemporaryChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Save a temporary chat transcript as a permanent chat."""
    temporary_chat_allowed = bool(get_user_group_setting_value(user.id, "chat", "allow_temporary_chat", db))
    if not temporary_chat_allowed:
        raise HTTPException(status_code=403, detail="Temporary chats are disabled for your group.")

    normalized_project_id = str(payload.project_id or "").strip() or None
    if normalized_project_id and not has_project_access(db, user.id, normalized_project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    result = save_temporary_chat(
        user.id,
        payload.temp_chat,
        payload.model_id,
        db,
        project_id=normalized_project_id,
    )
    _log_chat_event(
        db_log,
        request,
        user.id,
        "TEMP_CHAT_SAVED",
        {"chat_id": result.get("chat_id") if isinstance(result, dict) else None, "model_id": payload.model_id, "project_id": normalized_project_id},
    )
    return result


@chats_router.post("/code-execution/markdown/python", response_model=MarkdownCodeExecutionResponse)
def execute_markdown_python_code(
    payload: MarkdownCodeExecutionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Execute Python code from a markdown code block in a sandboxed environment."""
    normalized_chat_id = str(payload.chat_id or "").strip() or None
    if normalized_chat_id and not can_send_message_in_chat(db, user.id, normalized_chat_id):
        raise HTTPException(status_code=403, detail="You do not have access to this chat.")

    rate_limit_result = admit_user_tool_rate_limit(
        db,
        user_id=user.id,
        group_id=getattr(user, "group_id", None),
        tool_key="code_execution",
    )
    if isinstance(rate_limit_result, dict) and rate_limit_result.get("blocked"):
        raise HTTPException(status_code=429, detail=rate_limit_result)

    try:
        from app.tools.code_execution.utils import execute_code_tool_call

        execution_payload = execute_code_tool_call(
            {
                "type": "public",
                "language": "python",
                "code": payload.code,
            },
            user_id=user.id,
            chat_id=normalized_chat_id,
            tool_name="code_execution",
            include_file_ids=True,
            default_type="public",
        )
    except ValueError as exc:
        detail = str(exc) or "Python code execution failed."
        status_code = 400 if "non-empty 'code' argument" in detail or "code is required" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc

    exec_result = execution_payload.get("exec_result", {}) if isinstance(execution_payload, dict) else {}
    result = exec_result.get("result", {}) if isinstance(exec_result.get("result"), dict) else {}
    saved_files = exec_result.get("saved_files", []) if isinstance(exec_result.get("saved_files"), list) else []

    response_payload = MarkdownCodeExecutionResponse(
        ok=bool(execution_payload.get("service_available", True)),
        available=bool(execution_payload.get("service_available", True)),
        language=str(result.get("language") or "python"),
        execution_id=result.get("execution_id"),
        stdout=str(result.get("stdout") or ""),
        stderr=str(result.get("stderr") or ""),
        error=result.get("error"),
        error_type=result.get("error_type"),
        execution_time=result.get("execution_time"),
        timed_out=bool(result.get("timed_out", False)),
        files_generated=int(result.get("files_generated", len(saved_files)) or 0),
        files=[
            {
                "file_id": str(file_info.get("file_id") or ""),
                "name": str(file_info.get("name") or "output.bin"),
                "mime_type": str(file_info.get("mime_type") or "application/octet-stream"),
                "file_category": file_info.get("file_category"),
                "size": file_info.get("size"),
            }
            for file_info in saved_files
            if isinstance(file_info, dict) and file_info.get("file_id")
        ],
    )
    _log_chat_event(
        db_log,
        request,
        user.id,
        "MARKDOWN_PYTHON_CODE_EXECUTED",
        {
            "chat_id": normalized_chat_id,
            "ok": response_payload.ok,
            "code_length": len(str(payload.code or "")),
            "files_generated": response_payload.files_generated,
            "timed_out": bool(response_payload.timed_out),
        },
    )
    return response_payload


@chats_router.post("/code-preview/vega/resource", response_class=Response)
async def proxy_vega_preview_resource(
    payload: VegaPreviewResourceRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(verified_user),
):
    """Fetch one user-approved public resource for an inline Vega preview."""

    content, content_type = await fetch_vega_preview_resource(payload.url, db)
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@chats_router.post("/meetings/transcribe")
async def transcribe_meeting_route(
    media: UploadFile = File(...),
    request: Request = None,
    chat_id: str | None = Form(None),
    project_id: str | None = Form(None),
    browser_date_iso: str | None = Form(None),
    browser_date_label: str | None = Form(None),
    consent_confirmed: bool = Form(False),
    legal_basis: str | None = Form(None),
    legal_basis_details: str | None = Form(None),
    retention_days: int | None = Form(None),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Transcribe an uploaded audio/video file and create a meeting transcript chat message."""
    from app.workers.tool_jobs import external_media_enabled

    user_id = str(user.id)
    user_role = str(user.role or "")
    if external_media_enabled():
        from app.chats.meeting_transcripts import validate_meeting_transcript_admission
        from app.workers.media import (
            MeetingUploadEmpty,
            MeetingUploadTooLarge,
            discard_media_staging,
            enqueue_meeting_transcript_job_async,
            stage_meeting_media_upload,
            wait_for_media_job_async,
        )
        from app.workers.models import WorkerJobFailed

        filename = str(media.filename or "meeting")
        max_upload_bytes = validate_meeting_transcript_admission(
            db,
            user_id=user_id,
            filename=filename,
            content_type=media.content_type,
            chat_id=chat_id,
            project_id=project_id,
            consent_confirmed=consent_confirmed,
            legal_basis=legal_basis,
            legal_basis_details=legal_basis_details,
            retention_days=retention_days,
        )
        audit_ip_address = get_audit_request_ip(request, db)
        release_db_session_before_long_wait(db)
        try:
            staged_name, _uploaded_bytes = await stage_meeting_media_upload(
                media,
                max_bytes=max_upload_bytes,
            )
        except MeetingUploadTooLarge as exc:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Meeting upload exceeds the chat limit of "
                    f"{max_upload_bytes // (1024 * 1024)} MB"
                ),
            ) from exc
        except MeetingUploadEmpty as exc:
            raise HTTPException(status_code=400, detail="Uploaded media was empty") from exc
        try:
            # Once staging succeeds, either the durable job must own the bytes
            # or this request must remove them. Shield that short ownership
            # handoff so disconnect cancellation cannot strand an encrypted
            # upload between the two states.
            with anyio.CancelScope(shield=True):
                job = await enqueue_meeting_transcript_job_async(
                    user_id=user_id,
                    staged_name=staged_name,
                    filename=filename,
                    content_type=media.content_type,
                    chat_id=chat_id,
                    project_id=project_id,
                    browser_date_iso=browser_date_iso,
                    browser_date_label=browser_date_label,
                    consent_confirmed=consent_confirmed,
                    legal_basis=legal_basis,
                    legal_basis_details=legal_basis_details,
                    retention_days=retention_days,
                    audit_ip_address=audit_ip_address,
                    audit_user_agent=request.headers.get("user-agent"),
                )
        except BaseException:
            discard_media_staging(staged_name)
            raise
        try:
            result = await wait_for_media_job_async(job)
        except TimeoutError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "meeting_still_processing", "job_id": job.id},
                headers={"Retry-After": "3"},
            ) from exc
        except WorkerJobFailed as exc:
            status_code = 400 if exc.code in {
                "meeting_request_invalid",
                "staged_meeting_invalid",
                "user_unavailable",
            } else 500
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "job_id": job.id},
            ) from exc
    else:
        release_db_session_before_long_wait(db)
        result = await create_meeting_transcript(
            db=db,
            user_id=user_id,
            user_role=user_role,
            media=media,
            chat_id=chat_id,
            project_id=project_id,
            browser_date_iso=browser_date_iso,
            browser_date_label=browser_date_label,
            consent_confirmed=consent_confirmed,
            legal_basis=legal_basis,
            legal_basis_details=legal_basis_details,
            retention_days=retention_days,
        )
        _log_chat_event(
            db_log,
            request,
            user_id,
            "MEETING_TRANSCRIBED",
            {"chat_id": chat_id, "project_id": project_id, "filename": media.filename},
        )
    return result



# -------------------
# Regenerate assistant message
# -------------------
@chats_router.post("/regenerate")
def regenerate(
    payload: RegenerateMessageRequest,
    request: Request,
    custom_settings: SendChatRequestModelSettings | None = Body(default=None),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
    byok: dict | None = None,
):
    _ensure_byok_allowed_for_user(user.id, db, byok)
    from app.tools.subagents.runtime import validate_subagent_target_selection

    authorized_subagent_targets = validate_subagent_target_selection(
        db,
        user_id=user.id,
        targets=payload.subagent_targets,
    )
    _log_chat_event(
        db_log,
        request,
        user.id,
        "CHAT_MESSAGE_REGENERATED",
        {
            "chat_id": payload.chat_id,
            "user_message_id": payload.user_message_id,
            "model_id": payload.model_id,
            "subagent_target_mode": "automatic" if authorized_subagent_targets is None else "selected",
            "subagent_target_count": len(authorized_subagent_targets or []),
            "byok": bool(byok),
            **_build_byok_audit_details(byok),
        },
    )
    """
    Regenerate the assistant response for the latest user message in a chat.
    Creates a new assistant message with incremented retry_count.
    Uses the currently selected model (can be different from original).
    
    Body: `RegenerateMessageRequest` with `chat_id`, `user_message_id`, optional `model_id`, `skill_id`/`skill_ids`, `note_ids`, `prompt_ids`.
    
    Returns: a text/plain streaming response similar to /send.
    """
    import json
    import queue
    from contextlib import suppress

    client_generation_id = str(payload.generation_id)
    if not cancel_registry.reserve(client_generation_id, user.id):
        raise HTTPException(status_code=409, detail={"code": "generation_id_conflict"})

    if external_generation_enabled():
        stream_hub.start(
            client_generation_id,
            str(payload.chat_id),
            metadata={"state": "queued", "user_id": user.id},
        )
        try:
            enqueue_generation_job(
                db,
                kind="regenerate",
                user_id=user.id,
                generation_id=client_generation_id,
                request_payload=payload.model_dump(mode="json"),
                custom_settings=_custom_settings_override(custom_settings),
                byok=byok,
                subagent_targets=authorized_subagent_targets,
            )
        except Exception as exc:
            logger.error(
                "[Regenerate] failed to enqueue external generation user=%s chat_id=%s meta=%s",
                user.id,
                payload.chat_id,
                exception_metadata(exc),
            )
            stream_hub.publish_line(
                client_generation_id,
                json.dumps(
                    {
                        "t": "e",
                        "d": "Assistant response failed",
                        "code": "generation_queue_unavailable",
                        "i18n_key": "chat_sr_response_failed",
                    },
                    separators=(",", ":"),
                ),
            )
            stream_hub.mark_done(client_generation_id, status="failed")
            cancel_registry.clear(client_generation_id)
            raise HTTPException(
                status_code=503,
                detail={"code": "generation_queue_unavailable"},
            ) from exc
        return StreamingResponse(
            stream_hub.subscribe_async(client_generation_id, from_seq=0),
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )

    retry_guidance_meta = _retry_guidance_log_metadata(payload.retry_guidance)
    logger.info(
        "[Regenerate] request_user=%s chat_id=%s user_message_id=%s model_id=%s skill_id=%s skill_count=%s note_count=%s prompt_count=%s chat_reference_count=%s retry_guidance_mode=%s retry_guidance_preset=%s retry_guidance_custom_instruction_length=%s custom_settings_keys=%s",
        user.id,
        payload.chat_id,
        payload.user_message_id,
        payload.model_id,
        payload.skill_id,
        safe_count(payload.skill_ids),
        safe_count(payload.note_ids),
        safe_count(payload.prompt_ids),
        safe_count(payload.chat_reference_ids),
        retry_guidance_meta["mode"],
        retry_guidance_meta["preset"],
        retry_guidance_meta["custom_instruction_length"],
        list((_custom_settings_override(custom_settings) or {}).keys()) if custom_settings else None,
    )

    initial_lines_q: "queue.Queue[str]" = queue.Queue(maxsize=10)
    switch_info_q: "queue.Queue[object]" = queue.Queue(maxsize=1)

    def _normalize(line: str) -> str:
        return line if line.endswith("\n") else f"{line}\n"

    def _enqueue_initial(line: str) -> None:
        with suppress(Exception):
            initial_lines_q.put_nowait(_normalize(line))

    def _signal_switch(gen_id: str | None, last_seq: int) -> None:
        if gen_id and switch_info_q.empty():
            with suppress(Exception):
                switch_info_q.put_nowait((gen_id, last_seq))

    def _publish_error(detail: str) -> None:
        with suppress(Exception):
            initial_lines_q.put_nowait(_normalize(json.dumps({"t": "e", "d": detail})))

    def _drain_initial_lines():
        while True:
            try:
                yield initial_lines_q.get_nowait()
            except queue.Empty:
                break

    def _bg_consume():
        saw_start = False
        last_seq = 0
        gen_id: str | None = None
        session = SessionLocal()
        try:
            logger.debug(
                "[Regenerate] background consumer starting user=%s chat_id=%s user_msg=%s",
                user.id,
                payload.chat_id,
                payload.user_message_id,
            )
            upstream = regenerate_message(
                user.id,
                user.group_id,
                payload.chat_id,
                payload.user_message_id,
                payload.model_id,
                byok,
                _custom_settings_override(custom_settings),
                session,
                skill_id=payload.skill_id,
                skill_ids=payload.skill_ids,
                note_ids=payload.note_ids,
                prompt_ids=payload.prompt_ids,
                chat_reference_ids=payload.chat_reference_ids,
                retry_guidance=payload.retry_guidance,
                user_role=user.role,
                generation_id=client_generation_id,
                subagent_targets=authorized_subagent_targets,
            )

            for line in upstream:
                _log_redacted_stream_line(
                    "[Regenerate] upstream event user=%s chat_id=%s meta=%s",
                    user_id=user.id,
                    chat_id=payload.chat_id,
                    line=line,
                )
                obj = None
                with suppress(Exception):
                    obj = json.loads(line)
                if isinstance(obj, dict):
                    with suppress(Exception):
                        if "seq" in obj:
                            last_seq = int(obj["seq"])
                    event_type = obj.get("t") or obj.get("type")
                    if not saw_start and event_type in ("s", "start"):
                        data = obj.get("d") or {}
                        gen_id = data if isinstance(data, str) else obj.get("generation_id") or gen_id
                        saw_start = True

                if not saw_start:
                    _enqueue_initial(line)
                else:
                    _signal_switch(gen_id, last_seq)
        except Exception as exc:
            if gen_id:
                _signal_switch(gen_id, last_seq)
            else:
                _publish_error(getattr(exc, "detail", str(exc)))
                if switch_info_q.empty():
                    with suppress(Exception):
                        switch_info_q.put_nowait(None)
        finally:
            if not saw_start:
                cancel_registry.clear(client_generation_id)
            with suppress(Exception):
                session.close()

    future = background_task_executor.submit(_bg_consume)

    async def http_stream():
        gen_id = None
        last_seq = 0
        while gen_id is None:
            try:
                info = switch_info_q.get_nowait()
            except queue.Empty:
                for il in _drain_initial_lines():
                    yield il
                if not future.done():
                    await anyio.sleep(0.05)
                    continue

                try:
                    info = switch_info_q.get_nowait()
                except queue.Empty:
                    logger.debug("[Regenerate] background future already done before start user=%s chat_id=%s", user.id, payload.chat_id)
                    return
            except Exception:
                logger.error("[Regenerate] http_stream aborting due to exception user=%s chat_id=%s", user.id, payload.chat_id)
                for il in _drain_initial_lines():
                    _log_redacted_stream_line(
                        "[Regenerate] draining initial event on exception user=%s chat_id=%s meta=%s",
                        user_id=user.id,
                        chat_id=payload.chat_id,
                        line=il,
                    )
                    yield il
                return

            if info is None:
                logger.error("[Regenerate] switch info sentinel received user=%s chat_id=%s; draining queued initial lines before closing", user.id, payload.chat_id)
                for il in _drain_initial_lines():
                    _log_redacted_stream_line(
                        "[Regenerate] draining initial event on sentinel user=%s chat_id=%s meta=%s",
                        user_id=user.id,
                        chat_id=payload.chat_id,
                        line=il,
                    )
                    yield il
                return
            if not isinstance(info, tuple) or len(info) != 2:
                logger.error("[Regenerate] switch info malformed=%s user=%s chat_id=%s; draining queued initial lines before closing", info, user.id, payload.chat_id)
                for il in _drain_initial_lines():
                    _log_redacted_stream_line(
                        "[Regenerate] draining initial event on malformed info user=%s chat_id=%s meta=%s",
                        user_id=user.id,
                        chat_id=payload.chat_id,
                        line=il,
                    )
                    yield il
                return
            gen_id, last_seq = info
            logger.debug(
                "[Regenerate] switching to stream gen_id=%s last_seq=%s user=%s chat_id=%s",
                gen_id,
                last_seq,
                user.id,
                payload.chat_id,
            )

        for il in _drain_initial_lines():
            yield il

        if gen_id is None:
            return

        async for chunk in stream_hub.subscribe_async(gen_id, from_seq=last_seq):
            yield chunk

    return StreamingResponse(http_stream(), media_type="text/plain")



# -------------------
# Chat attention state
# -------------------
@chats_router.post("/{chat_id}/read", response_model=ChatReadResponse)
def mark_chat_read_route(
    chat_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Mark every currently persisted assistant response in a chat as read."""
    chat = get_chat_for_read(user.id, chat_id, db)
    mark_chat_read_for_user(db, user.id, chat)
    return ChatReadResponse(chat_id=str(chat.id), has_unread_response=False)


@chats_router.post("/attention/query", response_model=ChatAttentionQueryResponse)
def query_chat_attention_route(
    payload: ChatAttentionQuery,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Refresh unread flags for the bounded set of sidebar chats on screen."""
    unread_by_chat_id = get_accessible_chat_attention(db, user.id, payload.chat_ids)
    return ChatAttentionQueryResponse(unread_by_chat_id=unread_by_chat_id)


# -------------------
# Get Generation Status
# -------------------
@chats_router.get("/status")
def get_generation_status(chat_id: str, db: Session = Depends(get_db), user: User = Depends(verified_user)):
    """
    Get the current generation/streaming status for a chat.

    Query params:
      - chat_id: The chat ID owned by the authenticated user.
    Returns: A status object from the stream hub (e.g., active, generation_id, sequence info).
    """
    # Ensure ownership
    from app.chats.models import Chats
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user.id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    return stream_hub.get_status(chat_id)



# -------------------
# Attach stream
# -------------------
@chats_router.get("/attach")
def attach_stream(generation_id: str, from_seq: int = 0, db: Session = Depends(get_db), user: User = Depends(verified_user)):
    """
    Attach to an in-flight generation stream by `generation_id`.

    Query params:
      - generation_id: The ongoing generation identifier to attach to.
      - from_seq: Optional sequence number to resume from (default 0).
    Returns: a text/plain streaming response with subsequent events/lines.
    """
    # Resolve generation -> chat and verify ownership
    chat_id = stream_hub.get_chat_for_generation(generation_id)
    if not chat_id:
        # No such generation; return empty stream immediately
        return StreamingResponse(iter(()), media_type="text/plain")
    from app.chats.models import Chats
    chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user.id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found!")
    import json

    async def _filtered_stream():
        async for chunk in stream_hub.subscribe_async(
            generation_id,
            from_seq=from_seq,
        ):
            try:
                obj = json.loads(chunk)
            except Exception:
                yield chunk
                continue
            if isinstance(obj, dict) and obj.get("t") == "n_c":
                continue
            yield chunk

    return StreamingResponse(_filtered_stream(), media_type="text/plain")



# -------------------
# Cancel generation
# -------------------
@chats_router.post("/cancel")
def cancel_generation(
    generation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Cancel an active generation by `generation_id` for a chat owned by the user.

    Body/Query:
      - generation_id: The generation to cancel.
    Returns: {"ok": bool, "cancelled": bool, "reason"?: str}
    """
    # Deep Research uses the same generation ID and cancellation endpoint as
    # normal chat. Its durable row also lets cancellation remain authorized if
    # the in-memory stream mapping was already pruned or the process restarted.
    from app.tools.deep_research.models import (
        get_user_deep_research_run_by_generation,
        request_deep_research_cancellation,
    )

    deep_research_run = get_user_deep_research_run_by_generation(
        db,
        generation_id,
        user.id,
    )
    chat_id = stream_hub.get_chat_for_generation(generation_id)
    owns_reserved_generation = cancel_registry.is_owned_by(generation_id, user.id)
    if not chat_id and deep_research_run is None and not owns_reserved_generation:
        return {"status": "error"}

    if chat_id:
        from app.chats.models import Chats

        chat = db.query(Chats).filter(Chats.id == chat_id, Chats.user_id == user.id).first()
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found!")

    cancel_registry.cancel(generation_id)
    # Pending durable jobs are terminally cancelled; processing jobs receive a
    # cooperative DB flag in addition to the Redis provider-interrupt signal.
    if external_generation_enabled():
        cancel_queued_generation(
            db,
            generation_id=generation_id,
            user_id=user.id,
        )
    if deep_research_run is not None:
        request_deep_research_cancellation(db, deep_research_run)

    _log_chat_event(
        db_log,
        request,
        user.id,
        "CHAT_GENERATION_CANCELLED",
        {
            "generation_id": generation_id,
            "chat_id": chat_id or (
                deep_research_run.chat_id if deep_research_run is not None else None
            ),
            "deep_research_run_id": (
                deep_research_run.id if deep_research_run is not None else None
            ),
        },
    )
    return {"status": "success"}



# -------------------
# List Chats
# -------------------
@chats_router.get("", response_model=list[Chat])
def list_chats_route(
    project_id: str | None = None,
    limit: int = Query(default=MAX_CHAT_PAGE_LIMIT, ge=1, le=MAX_CHAT_PAGE_LIMIT),
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Legacy chat list route. Returns a capped first page of visible chats."""
    filter_project_id = None
    if project_id:
        # Check if user has access to project (owner or member)
        if has_project_access(db, user.id, project_id):
            filter_project_id = project_id
        else:
            raise HTTPException(status_code=404, detail="Project not found")
    return list_chats(
        user.id,
        db,
        project_id=filter_project_id,
        include_shared_project=project_id is not None,
        limit=limit,
    )



# -------------------
# List Chats Paginated
# -------------------
@chats_router.get("/paginated", response_model=ChatListPage)
def list_chats_paginated_route(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=MAX_CHAT_PAGE_LIMIT),
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """
    Get chats with pagination for lazy loading in the sidebar.
    
    - Pinned chats are returned first and capped
    - Unpinned chats are paginated with offset/limit
    
    Query params:
      - offset (default 0): Number of unpinned chats to skip
      - limit (default 20): Max unpinned chats to return
      - project_id (optional): Filter by project
      
    Returns: {
        "pinned": [...],           # Capped pinned chats
        "items": [...],            # Paginated unpinned chats  
        "total_pinned": int,       # Total count of pinned chats
        "pinned_has_more": bool,   # Whether pinned results were capped
        "total_unpinned": int,     # Total count of unpinned chats
        "has_more": bool           # Whether there are more unpinned chats
    }
    """
    offset = max(0, offset)
    limit = max(1, min(limit, MAX_CHAT_PAGE_LIMIT))
    filter_project_id = None
    if project_id:
        # Check if user has access to project (owner or member)
        if has_project_access(db, user.id, project_id):
            filter_project_id = project_id
        else:
            raise HTTPException(status_code=404, detail="Project not found")
    return list_chats_paginated(user.id, db, offset=offset, limit=limit, project_id=filter_project_id, include_shared_project=project_id is not None)



# -------------------
# Search Chats
# -------------------
@chats_router.get("/search")
def search_chats_route(
    query: str, 
    offset: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db), 
    user: User = Depends(verified_user)
):
    """Search chats by query string with pagination."""
    offset = max(0, offset)
    limit = max(1, min(limit, MAX_CHAT_PAGE_LIMIT))
    return search_chats(user.id, query, db, offset=offset, limit=limit)


# -------------------
# List Chat Reference Candidates
# -------------------
@chats_router.get("/references", response_model=ChatReferenceCandidatePage)
def list_chat_reference_candidates_route(
    q: str = "",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """List chats that can be attached as context references."""
    return list_chat_reference_candidates(
        user.id,
        db,
        query=q,
        offset=offset,
        limit=limit,
        project_id=project_id,
    )



# -------------------
# Rename Chat
# -------------------
@chats_router.put("/rename")
def rename_chat_route(
    chat_id: str,
    title: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Rename a chat and return the updated chat list."""
    rename_chat(user.id, chat_id, title, db)
    _log_chat_event(db_log, request, user.id, "CHAT_RENAMED", {"chat_id": chat_id, "title_length": len(str(title or ""))})
    return list_chats(user.id, db)



# -------------------
# Delete Chat
# -------------------
@chats_router.delete("/delete")
def delete_chat_route(
    chat_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Delete a single chat and log the action."""
    delete_chat(user.id, user.group_id, chat_id, db)

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="DELETE_CHAT",
        details={"chat_id": chat_id, "status": "success"},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return list_chats(user.id, db)



# -------------------
# Delete All Chats
# -------------------
@chats_router.delete("/delete/all")
def delete_all_chats_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Delete all chats for the authenticated user."""
    delete_all_chats(user.id, user.group_id, db)
    _log_chat_event(db_log, request, user.id, "ALL_CHATS_DELETED")
    return list_chats(user.id, db)



# -------------------
# Download Chat
# -------------------
@chats_router.get("/download")
def download_chat_route(
    chat_id: str,
    format: str = "json",
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Download a chat including its messages as a file.

    Query params:
      - chat_id: The chat to export (must belong to the authenticated user)
      - format: One of "json", "txt", "md", "pdf", or "docx" (default: json)
    """
    result = prepare_chat_download(user.id, chat_id, format, db)

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="DOWNLOAD_CHAT",
        details={
            "chat_id": chat_id,
            "format": format,
            "content_type": result.get("type"),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent") if request else None,
        category="user",
    )
    headers = {"Content-Disposition": f'attachment; filename="{result["filename"]}"'}
    if result["type"] == "json":
        return JSONResponse(content=result["content"], headers=headers)
    if result["type"] == "txt":
        return PlainTextResponse(content=result["content"], headers=headers)
    if result["type"] == "md":
        return PlainTextResponse(content=result["content"], media_type="text/markdown", headers=headers)
    if result["type"] == "pdf":
        return Response(content=result["content"], media_type="application/pdf", headers=headers)
    if result["type"] == "docx":
        return Response(content=result["content"], media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers=headers)
    # fallback
    return Response(content=result["content"], media_type="application/octet-stream", headers=headers)



# -------------------
# Duplicate Chat
# -------------------
@chats_router.post("/duplicate")
def duplicate_chat_route(
    chat_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Duplicate a chat and all its messages."""
    result = duplicate_chat(user.id, chat_id, db)
    _log_chat_event(
        db_log,
        request,
        user.id,
        "CHAT_DUPLICATED",
        {"source_chat_id": chat_id, "new_chat_id": result.get("id") if isinstance(result, dict) else None},
    )
    return result



# -------------------
# Branch Chat
# -------------------
@chats_router.post("/branch")
def branch_chat_route(
    message_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Create a new chat through the provided message's complete logical turn.

    The selected message is included. Later non-user messages (for example,
    assistant or tool rows) are also included until the next user message; that
    next user turn and all following messages are omitted.

    Parameters:
        message_id (str): The message ID to branch at (inclusive)
    Returns:
        {"new_chat_id": str}
    """
    result = branch_chat(user.id, message_id, db)
    _log_chat_event(
        db_log,
        request,
        user.id,
        "CHAT_BRANCHED",
        {"message_id": message_id, "new_chat_id": result.get("new_chat_id") if isinstance(result, dict) else None},
    )
    return result



# -------------------
# Get Chat Messages
# -------------------
@chats_router.get("/messages")
def get_chat_messages_route(chat_id: str, db: Session = Depends(get_db), user: User = Depends(verified_user)):
    """Return all messages for a specific chat."""
    return get_chat_messages(user.id, chat_id, db)


@chats_router.get("/detail", response_model=Chat)
def get_chat_detail_route(chat_id: str, db: Session = Depends(get_db), user: User = Depends(verified_user)):
    """Return chat metadata for the active chat, including project scope."""
    return get_chat_for_read(user.id, chat_id, db)



# -------------------
# Delete Chat Message
# -------------------
@chats_router.delete("/messages/delete")
def delete_chat_message_route(
    message_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Delete a single chat message."""
    result = delete_chat_message_record(user.id, getattr(user, "group_id", None), message_id, db)
    _log_chat_event(
        db_log,
        request,
        user.id,
        "CHAT_MESSAGE_DELETED",
        {"message_id": message_id, "chat_id": result.get("chat_id"), "chat_deleted": bool(result.get("chat_deleted"))},
    )
    if result.get("chat_deleted"):
        return {
            "chat_deleted": True,
            "chat_id": result.get("chat_id"),
            "messages": [],
        }
    return {
        "chat_deleted": False,
        "chat_id": result.get("chat_id"),
        "messages": get_chat_messages(user.id, result.get("chat_id"), db),
    }



# -------------------
# Share chat
# -------------------
@chats_router.post("/share")
def share_chat_route(
    payload: ShareChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Create a fresh active share link for a chat owned by the authenticated user.

    Body:
      - chat_id: str (required)
      - password: Optional[str] (if provided, will be required for access)
      - expires_at: Optional[datetime] (UTC timestamp when the share expires)
    Returns:
      {"share_id": str, "created_at": str, "expires_at": str | null}
    """
    result = share_chat(
        user.id,
        payload.chat_id,
        payload.password,
        db,
        payload.expires_at,
        access_mode=payload.access_mode,
        publication=payload.publication,
    )
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_CREATED",
        request=request,
        details={
            "chat_id": payload.chat_id,
            "share_id": result.get("share_id"),
            "has_password": bool(result.get("has_password")),
            "expires_at": result.get("expires_at"),
            "access_mode": result.get("access_mode"),
        },
    )
    return result



@chats_router.post("/share/invite", response_model=InviteChatUsersResponse)
def invite_users_to_chat_route(
    payload: InviteChatUsersRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Create a signed-in-only chat share link and invite selected users through workspace notifications.
    """
    active_invited_users = resolve_invitable_users_for_sharing(db, user, payload.user_ids)

    chat_before_share = db.query(Chats).filter(Chats.id == payload.chat_id, Chats.user_id == user.id).first()
    previous_share_id = getattr(chat_before_share, "share_id", None) if chat_before_share else None
    previous_share = deepcopy(getattr(chat_before_share, "share", None)) if chat_before_share else None

    share_kwargs = {
        "access_mode": "invited",
        "invited_user_ids": [invited_user.id for invited_user in active_invited_users],
    }
    if payload.publication is not None:
        share_kwargs["publication"] = payload.publication
    result = share_chat(
        user.id,
        payload.chat_id,
        None,
        db,
        payload.expires_at,
        **share_kwargs,
    )

    chat = db.query(Chats).filter(Chats.id == payload.chat_id, Chats.user_id == user.id).first()
    chat_title = _chat_invitation_title(chat)
    inviter_name = _get_user_display_name(user)

    successful_invited_user_ids = []
    for invited_user in active_invited_users:
        try:
            create_user_notification(
                db,
                message=f"{inviter_name} invited you to a chat: {chat_title}",
                category="share_invitation",
                notification_type="info",
                user_ids=[invited_user.id],
                details={
                    "type": "share_invitation",
                    "item_type": "chat",
                    "item_id": payload.chat_id,
                    "item_title": chat_title,
                    "share_id": result.get("share_id"),
                    "share_url": result.get("share_url"),
                    "share_type": "invited",
                    "inviter_id": user.id,
                    "inviter_name": inviter_name,
                },
            )
            successful_invited_user_ids.append(invited_user.id)
        except Exception:
            logger.exception(
                "Failed to create chat invitation notification for invited_user_id=%s chat_id=%s",
                invited_user.id,
                payload.chat_id,
            )

    invited_count = len(successful_invited_user_ids)
    if invited_count == 0:
        if chat:
            chat.share_id = previous_share_id
            chat.share = previous_share
            db.commit()
        raise HTTPException(status_code=500, detail="Failed to send invitations")

    if chat and invited_count != len(active_invited_users):
        share_data = chat.share if isinstance(chat.share, dict) else {}
        share_data = {
            **share_data,
            "invited_user_ids": successful_invited_user_ids,
        }
        chat.share = share_data
        db.commit()

    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_USERS_INVITED",
        request=request,
        details={
            "chat_id": payload.chat_id,
            "share_id": result.get("share_id"),
            "invited_user_ids": successful_invited_user_ids,
            "invited_count": invited_count,
            "expires_at": result.get("expires_at"),
        },
    )

    return InviteChatUsersResponse(
        share_id=result.get("share_id"),
        share_url=result.get("share_url"),
        access_mode=result.get("access_mode"),
        created_at=result.get("created_at"),
        expires_at=result.get("expires_at"),
        invited_count=invited_count,
        invited_user_ids=successful_invited_user_ids,
        publication=result.get("publication") or {},
        message=f"Successfully invited {invited_count} user(s) to the chat.",
    )



# -------------------
# Get Share Status
# -------------------
@chats_router.get("/share/status")
def get_share_status_route(chat_id: str, db: Session = Depends(get_db), user: User = Depends(verified_user)):
    """
    Get share status for a chat owned by the authenticated user, returning the share_id if present.

    Query:
      - chat_id: str (required)
    Returns: {"share_id": str | null, "created_at": str | null}
    """
    return get_share_status(user.id, chat_id, db)


@chats_router.get("/share/publication/options", response_model=ChatSharePublicationOptionsResponse)
def get_share_publication_options_route(
    chat_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Return owner-only saved answer versions and safe static output previews."""

    return get_share_publication_options(user.id, chat_id, db)


@chats_router.post("/share/publication")
def update_share_publication_route(
    payload: UpdateChatSharePublicationRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Publish the owner's reviewed response versions and static outputs."""

    result = update_share_publication(user.id, payload.chat_id, payload.publication, db)
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_PUBLICATION_UPDATED",
        request=request,
        details={
            "chat_id": payload.chat_id,
            "share_id": result.get("share_id"),
            "selected_response_count": len(payload.publication.response_versions),
            "approved_output_count": len(payload.publication.approved_output_ids),
        },
    )
    return result



# -------------------
# Access Shared Chat
# -------------------
@chats_router.post("/shared/access")
def access_shared_chat_route(
    payload: AccessSharedChatRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """
    Public endpoint to access shared chat messages by share_id and optional password.

    Body:
      - share_id: str (required)
      - password: Optional[str]
    Returns:
      {"title": str | null, "messages": [ ... ]}
    """
    apply_no_store_headers(response)

    client_ip = extract_client_ip_from_request(
        request,
        trusted_proxy_networks=resolve_trusted_proxy_networks("RATE_LIMIT_TRUSTED_PROXIES", "TRUSTED_PROXIES"),
        default=None,
    )
    user_access_token = _extract_bearer_token(request.headers.get("Authorization"))
    audit_subject = _shared_chat_audit_subject(db, payload.share_id)
    try:
        result = get_shared_chat_messages(
            payload.share_id,
            payload.password,
            db,
            known_updated_at=payload.known_updated_at,
            client_ip=client_ip,
            user_access_token=user_access_token,
            share_access_token=payload.share_access_token,
        )
    except HTTPException as exc:
        _log_chat_share_event(
            db_log=db_log,
            user_id=audit_subject.get("owner_user_id"),
            action="CHAT_SHARE_ACCESS_DENIED",
            request=request,
            client_ip=client_ip,
            details={
                **audit_subject,
                "status_code": exc.status_code,
                "reason": str(exc.detail or ""),
                "authenticated_request": bool(user_access_token),
                "used_share_access_token": bool(payload.share_access_token),
            },
        )
        raise

    should_audit_success = not bool(result.get("unchanged")) or not bool(payload.share_access_token)
    if should_audit_success:
        _log_chat_share_event(
            db_log=db_log,
            user_id=audit_subject.get("owner_user_id"),
            action="CHAT_SHARE_ACCESSED",
            request=request,
            client_ip=client_ip,
            details={
                **audit_subject,
                "access_mode": result.get("access_mode"),
                "has_password": bool(result.get("has_password")),
                "unchanged": bool(result.get("unchanged")),
                "authenticated_request": bool(user_access_token),
                "used_share_access_token": bool(payload.share_access_token),
            },
        )
    return result


@chats_router.get("/shared/files/{file_id}")
def access_shared_chat_file_route(
    file_id: str,
    request: Request,
    inline: bool = Query(False),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    share_access_token = _extract_bearer_token(request.headers.get("Authorization"))
    user_access_token = _extract_bearer_token(
        request.headers.get("X-Omlorix-User-Authorization")
        or request.headers.get("X-Omlorix-User-Access-Token")
    )
    client_ip = extract_client_ip_from_request(
        request,
        trusted_proxy_networks=resolve_trusted_proxy_networks("RATE_LIMIT_TRUSTED_PROXIES", "TRUSTED_PROXIES"),
        default=None,
    )
    try:
        access = resolve_shared_chat_file_access(
            share_access_token,
            file_id,
            db,
            user_access_token=user_access_token,
            client_ip=client_ip,
        )
    except HTTPException as exc:
        _log_chat_share_event(
            db_log=db_log,
            user_id=None,
            action="CHAT_SHARE_FILE_ACCESS_DENIED",
            request=request,
            client_ip=client_ip,
            details={
                "file_id": file_id,
                "status_code": exc.status_code,
                "reason": str(exc.detail or ""),
                "authenticated_request": bool(user_access_token),
                "has_share_access_token": bool(share_access_token),
            },
        )
        raise
    _log_chat_share_event(
        db_log=db_log,
        user_id=access["user_id"],
        action="CHAT_SHARE_FILE_ACCESSED",
        request=request,
        client_ip=client_ip,
        details={
            "share_id": access["share_id"],
            "file_id": access["file_id"],
            "inline": bool(inline),
            "authenticated_request": bool(user_access_token),
        },
    )
    response = download_file(access["user_id"], access["file_id"], db, inline=inline)
    return apply_no_store_headers(response)



# -------------------
# Delete Share
# -------------------
@chats_router.post("/share/delete")
def delete_share_route(
    payload: DeleteShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Unshare a chat (remove share info).

    Body:
      - chat_id: str (required)
    Returns: {"ok": true}
    """
    result = delete_chat_share(user.id, payload.chat_id, db)
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_DELETED",
        request=request,
        details={"chat_id": payload.chat_id},
    )
    return result



# -------------------
# Change Share Access Mode
# -------------------
@chats_router.post("/share/access/change")
def change_share_access_mode_route(
    payload: ChangeShareAccessModeRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Change whether a shared chat is open to anyone with the link or only signed-in users.

    Body: {"chat_id": str, "access_mode": "public" | "authenticated"}
    Use the invite endpoint to create invited-user shares.
    Returns: {"share_id": str, "access_mode": str}
    """
    result = update_share_access_mode(user.id, payload.chat_id, payload.access_mode, db)
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_ACCESS_MODE_CHANGED",
        request=request,
        details={
            "chat_id": payload.chat_id,
            "share_id": result.get("share_id"),
            "access_mode": result.get("access_mode"),
        },
    )
    return result



# -------------------
# Add Share Password
# -------------------
@chats_router.post("/share/password/create")
def add_share_password_route(
    payload: AddSharePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Add password protection to an existing shared chat (keeps share_id).

    Body: {"chat_id": str, "password": str}
    Returns: {"share_id": str}
    """
    result = update_share_password(user.id, payload.chat_id, payload.password, db, action="add")
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_PASSWORD_CREATED",
        request=request,
        details={"chat_id": payload.chat_id, "share_id": result.get("share_id")},
    )
    return result



# -------------------
# Change Share Password
# -------------------
@chats_router.post("/share/password/change")
def change_share_password_route(
    payload: ChangeSharePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Change the password for an existing shared chat (keeps share_id).

    Body: {"chat_id": str, "password": str}
    Returns: {"share_id": str}
    """
    result = update_share_password(user.id, payload.chat_id, payload.password, db, action="change")
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_PASSWORD_CHANGED",
        request=request,
        details={"chat_id": payload.chat_id, "share_id": result.get("share_id")},
    )
    return result



# -------------------
# Remove Share Password
# -------------------
@chats_router.post("/share/password/remove")
def remove_share_password_route(
    payload: RemoveSharePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Remove password protection from an existing shared chat (keeps share_id).

    Returns: {"share_id": str}
    """
    result = update_share_password(user.id, payload.chat_id, None, db, action="remove")
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_PASSWORD_REMOVED",
        request=request,
        details={"chat_id": payload.chat_id, "share_id": result.get("share_id")},
    )
    return result



# -------------------
# Create Share Expiry
# -------------------
@chats_router.post("/share/expiry/create")
def create_share_expiry_route(
    payload: CreateShareExpiryRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Create/set an expiry for an existing shared chat.

    Body: {"chat_id": str, "expires_at": datetime}
    Returns: {"share_id": str, "expires_at": str}
    """
    result = update_share_expiry(user.id, payload.chat_id, payload.expires_at, db, action="set")
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_EXPIRY_CREATED",
        request=request,
        details={"chat_id": payload.chat_id, "share_id": result.get("share_id"), "expires_at": result.get("expires_at")},
    )
    return result



# -------------------
# Change Share Expiry
# -------------------
@chats_router.post("/share/expiry/change")
def change_share_expiry_route(
    payload: ChangeShareExpiryRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Change the expiry for an existing shared chat.

    Body: {"chat_id": str, "expires_at": datetime}
    Returns: {"share_id": str, "expires_at": str}
    """
    result = update_share_expiry(user.id, payload.chat_id, payload.expires_at, db, action="change")
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_EXPIRY_CHANGED",
        request=request,
        details={"chat_id": payload.chat_id, "share_id": result.get("share_id"), "expires_at": result.get("expires_at")},
    )
    return result



# -------------------
# Delete Share Expiry
# -------------------
@chats_router.post("/share/expiry/delete")
def delete_share_expiry_route(
    payload: DeleteShareExpiryRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Delete/remove the expiry from an existing shared chat.

    Body: {"chat_id": str}
    Returns: {"share_id": str}
    """
    result = update_share_expiry(user.id, payload.chat_id, None, db, action="remove")
    _log_chat_share_event(
        db_log=db_log,
        user_id=user.id,
        action="CHAT_SHARE_EXPIRY_REMOVED",
        request=request,
        details={"chat_id": payload.chat_id, "share_id": result.get("share_id")},
    )
    return result



# -------------------
# Pin Chat
# -------------------
@chats_router.post("/pin")
def pin_chat_route(
    payload: PinChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Pin a chat at a given position (1-based). If position is omitted, append to the end
    of the pinned list. Returns the updated list of chats.
    """
    pin_chat(user.id, payload.chat_id, db, position=payload.position)
    _log_chat_event(db_log, request, user.id, "CHAT_PINNED", {"chat_id": payload.chat_id, "position": payload.position})
    return {"success": True}



# -------------------
# Unpin Chat
# -------------------
@chats_router.post("/unpin")
def unpin_chat_route(
    payload: UnpinChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Unpin a chat and compact positions of remaining pinned chats. Returns updated list.
    """
    unpin_chat(user.id, payload.chat_id, db)
    _log_chat_event(db_log, request, user.id, "CHAT_UNPINNED", {"chat_id": payload.chat_id})
    return {"success": True}



# -------------------
# Move Pinned Chat
# -------------------
@chats_router.post("/pin/move")
def move_pinned_chat_route(
    payload: MovePinnedChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Move a pinned chat to a new position. If the chat is not pinned yet, it will be pinned
    at the requested position. Returns the updated list of chats.
    """
    move_pinned_chat(user.id, payload.chat_id, payload.position, db)
    _log_chat_event(db_log, request, user.id, "CHAT_PIN_MOVED", {"chat_id": payload.chat_id, "position": payload.position})
    return {"success": True}



# -------------------
# Update Chat Project
# -------------------
@chats_router.post("/project/update")
def update_chat_project_route(
    payload: UpdateChatProjectRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Attach a chat to a project (or remove the association) for the authenticated user.

    Body: {"chat_id": str, "project_id": str | null}
    Returns: {"status": "success", "chat": Chat}
    """
    chat = update_chat_project(user.id, payload.chat_id, payload.project_id, db)
    _log_chat_event(db_log, request, user.id, "CHAT_PROJECT_UPDATED", {"chat_id": payload.chat_id, "project_id": payload.project_id})
    return {"status": "success", "chat": chat}



# -------------------
# Archive Chat
# -------------------
@chats_router.post("/archive")
def archive_chat_route(
    payload: ArchiveChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Archive a chat for the authenticated user. Archived chats are hidden from the main chat list.

    Body: {"chat_id": str}
    Returns: {"status": "success"}
    """
    archive_chat(user.id, payload.chat_id, db)
    _log_chat_event(db_log, request, user.id, "CHAT_ARCHIVED", {"chat_id": payload.chat_id})
    return {"status": "success"}



# -------------------
# Unarchive Chat
# -------------------
@chats_router.post("/unarchive")
def unarchive_chat_route(
    payload: UnarchiveChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Unarchive a chat for the authenticated user. The chat will reappear in the main chat list.

    Body: {"chat_id": str}
    Returns: {"status": "success"}
    """
    unarchive_chat(user.id, payload.chat_id, db)
    _log_chat_event(db_log, request, user.id, "CHAT_UNARCHIVED", {"chat_id": payload.chat_id})
    return {"status": "success"}



# -------------------
# Get Archived Chats
# -------------------
@chats_router.get("/archived")
def get_archived_chats_route(db: Session = Depends(get_db), user: User = Depends(verified_user)):
    """
    Get all archived chats for the authenticated user.

    Returns: List of archived chats
    """
    from app.chats.schemas import Chat
    chats = get_archived_chats(db, user.id)
    apply_chat_unread_state(db, user.id, chats)
    return [Chat.model_validate(chat) for chat in chats]



# -------------------
# Edit Chat Message
# -------------------
@chats_router.post("/messages/edit")
def edit_chat_message_route(
    payload: EditMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Edit a user message's content.

    Body: {"message_id": str, "content": str, "image_ids": [], "video_ids": [], "audio_ids": [], "document_ids": []}
    Returns: {"status": "success", "message": {...}}
    """
    message = edit_chat_message(
        user.id,
        payload.message_id,
        payload.content,
        db,
        image_ids=payload.image_ids,
        video_ids=payload.video_ids,
        audio_ids=payload.audio_ids,
        document_ids=payload.document_ids,
        chat_reference_ids=payload.chat_reference_ids,
    )
    _log_chat_event(
        db_log,
        request,
        user.id,
        "CHAT_MESSAGE_EDITED",
        {
            "message_id": payload.message_id,
            "content_length": len(str(payload.content or "")),
            "image_count": len(payload.image_ids or []),
            "video_count": len(payload.video_ids or []),
            "audio_count": len(payload.audio_ids or []),
            "document_count": len(payload.document_ids or []),
        },
    )
    return {"status": "success", "message": {"id": message.id, "content": message.content}}



# -------------------
# Toggle Message Bookmark
# -------------------
@chats_router.post("/messages/bookmark")
def toggle_bookmark_route(
    payload: ToggleBookmarkRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """
    Toggle bookmark status for a user or assistant message.

    Body: {"message_id": str}
    Returns: {"message_id": str, "bookmarked": bool, "role": str}
    """
    _ensure_bookmarks_enabled_for_user(user.id, db)
    result = toggle_message_bookmark(user.id, payload.message_id, db)
    _log_chat_event(
        db_log,
        request,
        user.id,
        "CHAT_MESSAGE_BOOKMARK_TOGGLED",
        {"message_id": payload.message_id, "bookmarked": result.get("bookmarked") if isinstance(result, dict) else None},
    )
    return result


@chats_router.post("/messages/read-aloud")
def read_aloud_message_route(
    payload: AssistantReadAloudRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Generate or retrieve cached TTS audio for an assistant message."""
    from app.workers.tool_jobs import external_media_enabled

    media_is_external = external_media_enabled()
    try:
        canonical_text = get_owned_assistant_message_read_aloud_text(
            db,
            user_id=user.id,
            message_id=payload.message_id,
        )
        if sanitize_read_aloud_text(payload.text) != canonical_text:
            raise HTTPException(status_code=400, detail="Read aloud text does not match the assistant message.")
        if media_is_external:
            from app.workers.media import enqueue_read_aloud_job, wait_for_media_job
            from app.workers.models import WorkerJobFailed

            job = enqueue_read_aloud_job(
                user_id=user.id,
                message_id=payload.message_id,
                expected_text_sha256=hashlib.sha256(
                    canonical_text.encode("utf-8")
                ).hexdigest(),
                audit_ip_address=get_audit_request_ip(request, db),
                audit_user_agent=request.headers.get("user-agent"),
            )
            try:
                worker_result = wait_for_media_job(job)
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "read_aloud_still_processing", "job_id": job.id},
                    headers={"Retry-After": "3"},
                ) from exc
            except WorkerJobFailed as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": exc.code, "job_id": job.id},
                ) from exc
            file_id = str(worker_result.get("file_id") or "")
            if not file_id:
                raise HTTPException(
                    status_code=500,
                    detail={"code": "read_aloud_result_invalid"},
                )
        else:
            file_record, _ = get_or_create_read_aloud_file(
                db,
                user_id=user.id,
                message_id=payload.message_id,
                text=canonical_text,
            )
            file_id = str(file_record.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not media_is_external:
        _log_chat_event(
            db_log,
            request,
            user.id,
            "CHAT_MESSAGE_READ_ALOUD",
            {"message_id": payload.message_id, "file_id": file_id},
        )
    db.expire_all()
    return download_file(user.id, file_id, db, inline=True)



# -------------------
# Get Bookmarked Messages
# -------------------
@chats_router.get("/bookmarks")
def get_bookmarks_route(db: Session = Depends(get_db), user: User = Depends(verified_user)):
    """
    Get all bookmarked messages (user and assistant) for the authenticated user.

    Returns: List of bookmarked messages with chat info including role
    """
    _ensure_bookmarks_enabled_for_user(user.id, db)
    return get_bookmarked_messages(user.id, db)
