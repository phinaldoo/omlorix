from fastapi import APIRouter, Depends, HTTPException, Request, File, Form, UploadFile, Query
from fastapi.security import HTTPAuthorizationCredentials
from app.files.schemas import (
    supported_file_format_catalog,
    supported_file_format_groups_for_model_input_formats,
)
from fastapi.responses import RedirectResponse, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from pydantic import BaseModel, ValidationError
from functools import partial
from typing import Any, List
import anyio
import hashlib
import httpx2
import logging
import math
from urllib.parse import parse_qs, urlencode, urlparse

from app.auth.account_slots import should_secure_auth_cookie
from app.dependencies import (
    bearer_scheme,
    get_db,
    get_db_log,
    verified_access_token,
    verified_admin,
    verified_user,
)
from app.users.roles import is_admin_role

from app.groups.init import get_user_group_setting_value
from app.groups.models import Group
from app.llm.models import (
    RATE_LIMIT_QUOTA_UNIT_REQUESTS,
    RATE_LIMIT_ADMISSION_COMPLETED,
    RATE_LIMIT_ADMISSION_FAILED,
    RATE_LIMIT_TARGET_TYPE_DICTATION,
    RATE_LIMIT_TARGET_TYPE_MODEL,
    LLMProvider,
    Models,
    RateLimit,
    create_llm_provider,
    list_llm_provider,
    get_llm_provider,
    update_llm_provider,
    delete_llm_provider,
    export_llm_providers,
    import_llm_providers,
    export_llm_models,
    import_llm_models,
    duplicate_model,
    delete_model,
    get_model,
    update_model_entry,
    list_user_model_setting_presets,
    get_user_model_setting_preset,
    create_user_model_setting_preset,
    delete_user_model_setting_preset,
    create_provider_group,
    get_provider_group,
    list_provider_groups,
    update_provider_group,
    delete_provider_group,
    get_provider_groups_for_provider,
    remove_provider_from_groups,
    create_rate_limit,
    update_rate_limit,
    get_rate_limit,
    list_rate_limits,
    delete_rate_limit,
    check_rate_limit_conflicts,
    get_rate_limit_usage_snapshot,
    rate_limit_targets_user,
    admit_user_duration_rate_limit,
    finalize_duration_rate_limit_admission,
    renew_dictation_duration_rate_limit_lease,
    normalize_llm_provider_status,
)
from app.tools.registry import get_rate_limit_tool, list_rate_limit_tools
from app.tools.widget_frames import create_widget_frame_payload, get_widget_frame_payload
from app.llm.schemas import (
    ProviderEnum,
    PROVIDER_MODEL_SETTINGS_MODELS,
    PROVIDER_SETTINGS_MODELS,
    LLMProviderDetail,
    LLMProviderListItem,
    ProviderModelListItem,
    CreateProviderRequest,
    ListProviderModelsByokRequest,
    ByokCredentialTokenRequest,
    ByokCredentialTokenResponse,
    ByokModelSchemaRequest,
    UpdateProviderPayload,
    CreateProviderModelRequest,
    UpdateModelPayload,
    BulkUpdateModelsPayload,
    ModelSettingPresetDetail,
    ModelSettingPresetListItem,
    CreateModelSettingPresetRequest,
    TestProviderPayload,
    MODEL_CAPABLE_PROVIDERS,
    resolve_provider_icon,
    normalize_provider_value,
    provider_api_key_is_optional,
    PROVIDER_BYOK_PAYLOAD_MODELS,
    CreateProviderGroupRequest,
    UpdateProviderGroupRequest,
    ProviderGroupListItem,
    CreateRateLimitRequest,
    UpdateRateLimitRequest,
    RateLimitListItem,
    RateLimitDetail,
    RateLimitMutationResponse,
    LLMLeaderboardResponse,
    FileFormatCatalogResponse,
    ModelSettingsResponse,
    UserModelSummary,
    serialize_llm_provider_detail,
)
from app.llm.byok_schema import sanitize_byok_provider_schema
from app.llm.byok_credentials import (
    ByokCredentialTokenError,
    issue_byok_credential_token,
    resolve_byok_credential_token,
)
from app.settings.models import get_settings_page
from app.llm.utils import (
    create_provider_model,
    list_admin_models,
    list_provider_models,
    list_user_models,
    test_llm_provider,
    refresh_provider_status_snapshot,
    _validate_websearch_providers,
)
from app.llm.openai.custom_headers import preserve_redacted_custom_headers_in_settings
from app.llm.leaderboard import get_llm_model_leaderboard, clear_llm_model_leaderboard_cache
from app.llm.transcription_errors import (
    TRANSCRIPTION_NOT_ENABLED_ERROR_CODE,
    build_transcription_error_detail,
)
from app.llm.audio_duration import measure_audio_duration_seconds
from app.llm.google_aistudio.schemas import get_aistudio_model_schema, get_aistudio_model_schema_parameter
from app.llm.anthropic.schemas import get_anthropic_model_schema, get_anthropic_model_schema_parameter
from app.llm.openai.schemas import get_openai_model_schema, get_openai_model_schema_parameter
from app.llm.openrouter.schemas import get_openrouter_model_schema, get_openrouter_model_schema_parameter
from app.llm.openrouter.utils import get_model_providers
from app.llm.ollama.schemas import get_ollama_model_schema
from app.llm.lmstudio.schemas import get_lmstudio_model_schema
from app.llm.google_aistudio.utils import list_models_google_aistudio, create_aistudio_provider
from app.llm.openai.utils import create_openai_provider, list_models_openai
from app.llm.openrouter.utils import create_open_router_provider, list_models_openrouter
from app.llm.ollama.utils import create_ollama_provider, list_models_ollama
from app.llm.lmstudio.utils import create_lmstudio_provider, list_models_lmstudio
from app.llm.anthropic.utils import list_anthropic_models, create_anthropic_provider
from app.llm.utils import get_provider_schema
from app.llm.provider_groups import get_group_common_models, get_group_with_provider_details
from app.logging.models import create_audit_log, get_audit_request_ip, _hash_text
from app.llm.capabilities import determine_model_capabilities
from app.llm.settings_merge import merge_settings_update
from app.llm.speech import (
    get_transcription_runtime_for_provider,
    snapshot_transcription_provider,
    transcribe_audio_bytes_for_provider,
)
from app.llm.provider_request import release_db_session_before_provider_io
from app.utils.blocking_io import run_blocking_io
from app.mcp.models import MCPOAuthState, MCPServer, OWNER_ADMIN, OWNER_USER, get_mcp_server
from app.mcp.schemas import (
    MCPAppFrameCreateRequest,
    MCPAppFrameCreateResponse,
    MCPAppResourceReadRequest,
    MCPAppServerRequest,
    MCPAppTokenRefreshResponse,
    MCPAppToolCallRequest,
    CreateMCPServerRequest,
    MCPServerDetail,
    MCPServerListItem,
    MCPMentionConnector,
    MCPServerTestRequest,
    MCPToolPreviewResponse,
    MCPOAuthStartResponse,
    UpdateMCPServerRequest,
)
from app.mcp.oauth import (
    MCP_OAUTH_STATE_TTL_SECONDS,
    abort_mcp_oauth,
    build_client_metadata,
    complete_mcp_oauth,
    start_mcp_oauth,
)
from app.mcp.utils import (
    call_mcp_app_tool_payload,
    create_mcp_app_frame_payload,
    export_admin_servers_bundle,
    get_mcp_app_frame_payload,
    get_mcp_app_sandbox_proxy_payload,
    import_admin_servers_bundle,
    list_mcp_app_prompts_payload,
    list_mcp_app_resources_payload,
    list_mcp_app_resource_templates_payload,
    list_mcp_app_tools_payload,
    read_mcp_app_resource_payload,
    refresh_mcp_app_access_token_payload,
    create_admin_mcp_server,
    create_user_mcp_server,
    delete_admin_server_payload,
    delete_user_server_payload,
    get_admin_server_payload,
    get_user_server_payload,
    list_admin_servers_payload,
    list_user_servers_payload,
    list_mcp_mention_connectors,
    preview_server_tools,
    require_group_mcp_enabled,
    resolve_mcp_headers_from_payload,
    resolve_mcp_oauth_from_payload,
    update_admin_server_payload,
    update_user_server_payload,
)
from app.network.policy import OutboundRequestBlockedError, assert_llm_config_allowed
from app.users.models import User
from app.settings.utils import get_public_url


logger = logging.getLogger(__name__)


_MCP_OAUTH_RETURN_PATHS = frozenset({
    "/workspace/connections",
    "/admin/mcp-settings",
})
_MCP_OAUTH_CALLBACK_PATH = "/api/v1/llm/mcp/oauth/callback"
_MCP_OAUTH_CALLBACK_COOKIE_PREFIX = "omlorix_mcp_oauth_callback_"


def _safe_mcp_oauth_return_path(value: str | None) -> str:
    """Return an allowlisted local MCP settings path for OAuth redirects.

    OAuth state is encrypted at rest, but treating every persisted value as
    untrusted keeps a corrupted or legacy row from becoming an open redirect.
    """
    candidate = str(value or "").strip()
    return candidate if candidate in _MCP_OAUTH_RETURN_PATHS else "/workspace/connections"


def _mcp_oauth_state_from_authorization_url(authorization_url: str) -> str:
    """Extract the freshly generated state used to scope the callback cookie."""
    values = parse_qs(urlparse(authorization_url).query).get("state") or []
    state = str(values[0] if values else "").strip()
    if not state:
        raise ValueError("MCP OAuth authorization URL is missing state.")
    return state


def _mcp_oauth_callback_cookie_name(state: str) -> str:
    """Return a fixed-size, cookie-safe name unique to one OAuth state."""
    state_digest = hashlib.sha256(str(state or "").encode("utf-8")).hexdigest()
    return f"{_MCP_OAUTH_CALLBACK_COOKIE_PREFIX}{state_digest}"


def _set_mcp_oauth_callback_cookie(
    response: Response,
    *,
    state: str,
    access_token: str,
    db,
    request: Request,
) -> None:
    """Store a short-lived callback-only session token with SameSite=Lax.

    The normal access cookie may intentionally use SameSite=Strict. OAuth
    returns from a different site, so this narrowly path-scoped duplicate is
    required for the top-level callback GET and is never sent to other APIs.
    """
    response.set_cookie(
        key=_mcp_oauth_callback_cookie_name(state),
        value=access_token,
        httponly=True,
        secure=should_secure_auth_cookie(db, request),
        samesite="lax",
        path=_MCP_OAUTH_CALLBACK_PATH,
        max_age=MCP_OAUTH_STATE_TTL_SECONDS,
    )


def _clear_mcp_oauth_callback_cookie(
    response: Response,
    *,
    state: str,
    db,
    request: Request,
) -> None:
    """Delete the callback-only token after an accepted OAuth response."""
    response.delete_cookie(
        key=_mcp_oauth_callback_cookie_name(state),
        path=_MCP_OAUTH_CALLBACK_PATH,
        secure=should_secure_auth_cookie(db, request),
        httponly=True,
        samesite="lax",
    )


def _verified_mcp_oauth_callback_user(
    request: Request,
    *,
    state: str,
    credentials: HTTPAuthorizationCredentials | None,
    db,
):
    """Authenticate the callback with its Lax cookie or normal credentials.

    Falling back to the ordinary access credential preserves in-flight flows
    created before this protection was deployed. A state-specific callback
    cookie, when present, is authoritative and is never bypassed by fallback.
    """
    callback_token = request.cookies.get(_mcp_oauth_callback_cookie_name(state))
    if callback_token:
        callback_credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=callback_token,
        )
        return verified_user(request, callback_credentials, db)
    return verified_user(request, credentials, db)



llm_router = APIRouter(prefix="/api/v1/llm", tags=["llm"])


class WidgetFrameCreateRequest(BaseModel):
    html: str
    widget_type: str | None = None
    theme_mode: str | None = None


class WidgetFrameCreateResponse(BaseModel):
    frame_id: str
    frame_url: str


def _audit_llm_event(
    db_log: Session,
    request: Request,
    user_id: str,
    action: str,
    details: dict | None = None,
    category: str = "llm",
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category=category,
    )


def _mcp_server_audit_details(server) -> dict[str, Any]:
    if isinstance(server, dict):
        data = server
    elif hasattr(server, "model_dump"):
        data = server.model_dump()
    else:
        data = {
            "id": getattr(server, "id", None),
            "name": getattr(server, "name", None),
            "namespace": getattr(server, "namespace", None),
            "transport": getattr(server, "transport", None),
            "enabled": getattr(server, "enabled", None),
        }
    return {
        "server_id": data.get("id"),
        "name": data.get("name"),
        "namespace": data.get("namespace"),
        "transport": data.get("transport"),
        "enabled": data.get("enabled"),
    }


def _resolve_existing_mcp_test_server(
    db: Session,
    server_id: str | None,
    *,
    owner_type: str,
    owner_user_id: str | None = None,
) -> MCPServer | None:
    normalized_server_id = str(server_id or "").strip()
    if not normalized_server_id:
        return None
    server = get_mcp_server(db, normalized_server_id)
    if server.owner_type != owner_type:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    if owner_type == OWNER_USER and (server.owner_user_id != owner_user_id or server.managed_connection_id):
        raise HTTPException(status_code=404, detail="MCP server not found.")
    return server


def _coerce_allow_custom_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _coerce_bool(value) -> bool:
    """Coerce a value to boolean, handling string representations."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value) if value is not None else False


def _build_rate_limit_payload(db: Session, rate_limit_obj) -> dict[str, Any]:
    target_type = getattr(rate_limit_obj, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL) or RATE_LIMIT_TARGET_TYPE_MODEL
    model_ids = list(rate_limit_obj.model_ids or [])
    tool_keys = list(getattr(rate_limit_obj, "tool_keys", None) or [])
    user_ids = list(rate_limit_obj.user_ids or [])
    group_ids = list(rate_limit_obj.group_ids or [])

    models = db.query(Models).filter(Models.id.in_(model_ids)).all() if model_ids else []
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []

    model_lookup = {model.id: model for model in models}
    user_lookup = {user.id: user for user in users}
    group_lookup = {group.id: group for group in groups}

    current_usage = None
    remaining_usage = None
    current_usage_seconds = None
    remaining_usage_seconds = None
    if user_ids and len(user_ids) == 1:
        usage_snapshot = get_rate_limit_usage_snapshot(db, rate_limit_obj, user_ids[0])
        current_usage = usage_snapshot["current_usage"]
        remaining_usage = usage_snapshot["remaining_usage"]
        current_usage_seconds = usage_snapshot.get("current_usage_seconds")
        remaining_usage_seconds = usage_snapshot.get("remaining_usage_seconds")

    return {
        "id": rate_limit_obj.id,
        "name": rate_limit_obj.name,
        "target_type": target_type,
        "model_ids": model_ids,
        "tool_keys": tool_keys,
        "user_ids": user_ids,
        "group_ids": group_ids,
        "scope": getattr(rate_limit_obj, "scope", "chat"),
        "period": rate_limit_obj.period,
        "timezone": getattr(rate_limit_obj, "timezone", "UTC"),
        "quota_unit": getattr(rate_limit_obj, "quota_unit", RATE_LIMIT_QUOTA_UNIT_REQUESTS),
        "quota_value": int(getattr(rate_limit_obj, "quota_value", rate_limit_obj.max_requests) or 0),
        "current_usage": current_usage,
        "remaining_usage": remaining_usage,
        "current_usage_seconds": current_usage_seconds,
        "remaining_usage_seconds": remaining_usage_seconds,
        "max_requests": (
            int(getattr(rate_limit_obj, "quota_value", rate_limit_obj.max_requests) or 0)
            if getattr(rate_limit_obj, "quota_unit", RATE_LIMIT_QUOTA_UNIT_REQUESTS) == RATE_LIMIT_QUOTA_UNIT_REQUESTS
            else None
        ),
        "is_active": bool(rate_limit_obj.is_active),
        "created_at": rate_limit_obj.created_at,
        "updated_at": getattr(rate_limit_obj, "updated_at", None),
        "models": [
            {
                "id": model_id,
                "name": getattr(model_lookup.get(model_id), "name", None) or model_id,
            }
            for model_id in model_ids
        ],
        "tools": [
            get_rate_limit_tool(db, tool_key) or {
                "key": tool_key,
                "id": tool_key,
                "name": tool_key,
                "label": tool_key,
                "description": "",
                "source": "unknown",
                "available": False,
            }
            for tool_key in tool_keys
        ],
        "users": [
            {
                "id": user_id,
                "email": getattr(user_lookup.get(user_id), "email", None) or user_id,
                "first_name": getattr(user_lookup.get(user_id), "first_name", None),
                "last_name": getattr(user_lookup.get(user_id), "last_name", None),
            }
            for user_id in user_ids
        ],
        "groups": [
            {
                "id": group_id,
                "name": getattr(group_lookup.get(group_id), "name", None) or group_id,
            }
            for group_id in group_ids
        ],
    }


def _extract_enabled_tool_names(raw_tools: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(raw_tools, list):
        return names
    for entry in raw_tools:
        if isinstance(entry, str) and entry.strip():
            names.add(entry.strip())
            continue
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    return names


def _ensure_byok_allowed(user_id: str, db: Session) -> None:
    allow_byok = bool(get_user_group_setting_value(user_id, "chat", "allow_byok", db))
    if not allow_byok:
        raise HTTPException(status_code=403, detail="Bring Your Own Key is disabled for your group.")


def _resolve_byok_credential_or_error(
    *,
    credential_token: str | None,
    user_id: str,
    provider: ProviderEnum,
    provider_id: str,
) -> str:
    """Resolve one sealed credential without exposing cryptographic details."""

    if not str(credential_token or "").strip():
        if provider_api_key_is_optional(provider):
            return ""
        raise HTTPException(status_code=400, detail={"code": "byok_credential_unavailable"})
    try:
        return resolve_byok_credential_token(
            str(credential_token),
            user_id=user_id,
            provider=provider.value,
            provider_id=provider_id,
        )
    except ByokCredentialTokenError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "byok_credential_unavailable"},
        ) from exc


def _ensure_byok_target_allowed(db: Session, provider: ProviderEnum, config) -> dict[str, Any]:
    config_dict = config.model_dump(exclude_none=True) if hasattr(config, "model_dump") else {}
    try:
        assert_llm_config_allowed(
            db,
            provider_type=provider.value,
            settings=config_dict,
            feature="BYOK model discovery",
            require_private_allowlist=True,
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc
    return config_dict


def _byok_discovery_http_error(exc: HTTPException) -> HTTPException:
    """Replace provider-authored discovery copy with a stable client code."""

    detail = exc.detail
    stable_codes = {
        "byok_credential_unavailable",
        "byok_provider_authentication_failed",
        "byok_provider_configuration_invalid",
        "byok_model_discovery_failed",
    }
    if isinstance(detail, dict) and detail.get("code") in stable_codes:
        return exc
    normalized_detail = str(detail or "").strip().lower()
    authentication_markers = (
        "authentication",
        "invalid api key",
        "invalid_api_key",
        "incorrect api key",
        "unauthorized",
    )
    if exc.status_code in {401, 403} or any(
        marker in normalized_detail for marker in authentication_markers
    ):
        return HTTPException(
            status_code=401,
            detail={"code": "byok_provider_authentication_failed"},
        )
    return HTTPException(
        status_code=exc.status_code if 400 <= exc.status_code <= 599 else 424,
        detail={"code": "byok_model_discovery_failed"},
    )


def _list_models_for_byok_provider(
    db: Session,
    provider: ProviderEnum,
    config,
    config_dict: dict[str, Any],
):
    """Dispatch BYOK discovery while keeping the route wiring compact."""

    match provider:
        case ProviderEnum.openai:
            return list_models_openai(db, byok=config_dict)
        case ProviderEnum.openai_responses:
            return list_models_openai(
                db,
                byok=config_dict,
                openai_provider_type="openai_responses",
            )
        case ProviderEnum.xai:
            config_dict["base_url"] = (
                str(config_dict.get("base_url") or "").strip().rstrip("/")
                or "https://api.x.ai/v1"
            )
            return list_models_openai(
                db,
                byok=config_dict,
                openai_provider_type=ProviderEnum.xai.value,
            )
        case ProviderEnum.openai_chat_completions:
            return list_models_openai(
                db,
                byok=config_dict,
                openai_provider_type="openai_chat_completions",
            )
        case ProviderEnum.microsoft_azure:
            return list_models_openai(
                db,
                byok=config_dict,
                openai_provider_type="microsoft_azure",
            )
        case ProviderEnum.anthropic:
            return list_anthropic_models(
                db,
                anthropic_provider_id=getattr(config, "anthropic_provider_id", None),
                api_key=getattr(config, "api_key", None),
            )
        case ProviderEnum.anthropic_base:
            return list_anthropic_models(
                db,
                anthropic_provider_id=getattr(config, "anthropic_provider_id", None),
                api_key=getattr(config, "api_key", None),
                base_url=getattr(config, "base_url", None),
            )
        case ProviderEnum.google_aistudio:
            return list_models_google_aistudio(
                db,
                byok=config_dict,
                type="generateContent",
            )
        case ProviderEnum.openrouter:
            return list_models_openrouter(
                db,
                openrouter_provider_id=getattr(config, "openrouter_provider_id", None),
                api_key=getattr(config, "api_key", None),
                parameters=getattr(config, "parameters", None),
            )
        case ProviderEnum.ollama:
            return list_models_ollama(
                db,
                byok_base_url=getattr(config, "base_url", None),
                byok_api_key=getattr(config, "api_key", None),
            )
        case ProviderEnum.lmstudio:
            return list_models_lmstudio(
                db,
                byok_base_url=getattr(config, "base_url", None),
                byok_api_key=getattr(config, "api_key", None),
            )
    raise HTTPException(status_code=400, detail={"code": "byok_model_discovery_failed"})


def _schema_to_payload(schema_obj: Any) -> dict[str, Any]:
    if schema_obj is not None:
        schema_obj = schema_obj
    payload = jsonable_encoder(schema_obj) if schema_obj is not None else {}
    if isinstance(payload, dict) and isinstance(payload.get("sections"), list):
        return payload
    return {"sections": []}


def _schema_to_compact_payload(schema_obj: Any) -> dict[str, Any]:
    """Serialize a chat settings schema without wire-only boilerplate.

    Provider schema models intentionally have many optional properties because
    the same classes power several admin editors.  Sending every absent value
    as JSON ``null`` made the chat endpoint several times larger than its
    meaningful content.  Missing values and model defaults have the same
    semantics in the chat renderer, so omit them at this boundary.

    MCP server choices are also excluded here.  They are user-scoped runtime
    data rather than model schema, and the frontend hydrates that one field via
    the already-authorized MCP connector endpoint.  This avoids disclosing a
    user's integration inventory to callers that only need model controls.
    """
    if schema_obj is None:
        return {"sections": []}
    payload = jsonable_encoder(
        schema_obj,
        exclude_none=True,
        exclude_defaults=True,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), list):
        return {"sections": []}

    for section in payload["sections"]:
        if not isinstance(section, dict):
            continue
        # Stable translation keys are the wire representation. English copies
        # already live in the bundled ``en`` dictionary and need not be sent
        # again for every model and every request.
        if section.get("i18n_title"):
            section.pop("title", None)
        if section.get("i18n_description"):
            section.pop("description", None)
        for field in section.get("fields") or []:
            if not isinstance(field, dict):
                continue
            if field.get("i18n_label"):
                field.pop("label", None)
            if field.get("i18n_description"):
                field.pop("description", None)
            if field.get("i18n_placeholder"):
                field.pop("placeholder", None)
            for option in field.get("options") or []:
                if isinstance(option, dict) and option.get("i18n_label"):
                    option.pop("label", None)
            # The renderer treats an omitted false flag exactly like false.
            # Pydantic cannot remove these with ``exclude_defaults`` because
            # several legacy schema flags use ``None`` as their class default.
            for flag in (
                "multiple",
                "searchable",
                "required",
                "redact_value",
                "masked_placeholder",
                "masked_value_set",
                "hidden",
                "hide_on_byok",
            ):
                if field.get(flag) is False:
                    field.pop(flag, None)
            if field.get("key") == "settings.enabled_mcp_servers":
                field.pop("options", None)
        # Empty section keys are presentation no-ops and can be omitted even
        # when a dict-based provider schema supplied them explicitly.
        if section.get("key") is None:
            section.pop("key", None)
    return payload


BYOK_MODEL_SCHEMA_EXCLUDED_FIELDS = {
    "access.everyone",
    "access.users",
    "access.groups",
    "settings.title_generation",
    "settings.title_generation_model",
    "settings.title_generation_model_id",
    "settings.custom_title_generation_instruction",
    "settings.allow_custom_generation_parameter",
    "status",
}


def _remove_schema_fields(schema_payload: dict[str, Any], field_keys: set[str]) -> dict[str, Any]:
    sections = schema_payload.get("sections")
    if not isinstance(sections, list) or not field_keys:
        return schema_payload

    filtered_sections: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        fields = section.get("fields")
        if not isinstance(fields, list):
            continue
        next_fields = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or "").strip()
            if key and key in field_keys:
                continue
            next_fields.append(field)
        if next_fields:
            section_copy = dict(section)
            section_copy["fields"] = next_fields
            filtered_sections.append(section_copy)

    schema_payload["sections"] = filtered_sections
    return schema_payload


def _resolve_dotted_value(payload: Any, dotted_key: str):
    if not isinstance(payload, dict) or not isinstance(dotted_key, str) or not dotted_key:
        return None
    cursor: Any = payload
    for segment in dotted_key.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(segment)
        if cursor is None:
            return None
    return cursor


def _prefix_tool_schema_sections(
    schema_payload: dict[str, Any],
    *,
    key_prefix: str,
    section_title_prefix: str,
) -> list[dict[str, Any]]:
    sections = schema_payload.get("sections")
    if not isinstance(sections, list):
        return []

    prefixed_sections: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_copy = dict(section)
        section_title = str(section_copy.get("title") or "").strip()
        section_copy["title"] = (
            f"{section_title_prefix} - {section_title}"
            if section_title
            else section_title_prefix
        )

        fields = section_copy.get("fields")
        if not isinstance(fields, list):
            section_copy["fields"] = []
            prefixed_sections.append(section_copy)
            continue

        prefixed_fields: list[dict[str, Any]] = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_copy = dict(field)
            field_key = field_copy.get("key")
            if isinstance(field_key, str) and field_key.strip():
                field_copy["key"] = f"{key_prefix}.{field_key}"

            for dep_key in ("dependency", "dependency2"):
                dep_value = field_copy.get(dep_key)
                if isinstance(dep_value, str) and dep_value.strip():
                    field_copy[dep_key] = f"{key_prefix}.{dep_value}"

            field_type = str(field_copy.get("type") or "").strip().lower()
            input_type = str(field_copy.get("input_type") or "").strip().lower()
            if field_type == "number" and not input_type:
                step_value = None
                attributes = field_copy.get("attributes")
                if isinstance(attributes, dict):
                    step_value = attributes.get("step")
                if isinstance(step_value, (int, float)) and float(step_value) not in {0.0, 1.0}:
                    field_copy["input_type"] = "float"
                else:
                    field_copy["input_type"] = "int"

            prefixed_fields.append(field_copy)

        section_copy["fields"] = prefixed_fields
        prefixed_sections.append(section_copy)

    return prefixed_sections


def _apply_values_to_schema_sections(
    sections: list[dict[str, Any]],
    values_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    for section in sections:
        fields = section.get("fields")
        if not isinstance(fields, list):
            continue
        for field in fields:
            if not isinstance(field, dict):
                continue
            key = field.get("key")
            if not isinstance(key, str) or not key:
                continue
            value = _resolve_dotted_value(values_payload, key)
            if value is not None:
                field["value"] = value
    return sections


def _build_image_tool_settings_sections(db: Session) -> list[dict[str, Any]]:
    record = get_settings_page(db, "image_generation")
    values = {"provider_id": "", "model_name": "", "settings": {}}
    if record and isinstance(record.data, dict):
        values["provider_id"] = str(record.data.get("provider_id") or "").strip()
        values["model_name"] = str(record.data.get("model_name") or "").strip()
        settings_payload = record.data.get("settings")
        if isinstance(settings_payload, dict):
            values["settings"] = dict(settings_payload)

    provider_id = values["provider_id"]
    model_name = values["model_name"]
    if not provider_id or not model_name:
        return []

    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        return []

    provider_type = str(provider.provider or "").strip().lower()
    schema_obj = None
    try:
        if provider_type == "openai":
            from app.llm.openai.image_generation import get_image_generation_schema_part_2

            schema_obj = get_image_generation_schema_part_2(model_name)
        elif provider_type in {"openai_responses", "openai_chat_completions"}:
            from app.llm.openai_responses.image_generation import get_image_generation_schema_part_2

            schema_obj = get_image_generation_schema_part_2()
        elif provider_type == "openrouter":
            from app.llm.openrouter.image_generation import get_image_generation_schema_part_2

            schema_obj = get_image_generation_schema_part_2()
        elif provider_type == "google_aistudio":
            from app.llm.google_aistudio.image_generation import get_image_generation_schema_part_2

            schema_obj = get_image_generation_schema_part_2(model_name)
        elif provider_type == "xai":
            from app.llm.xai.image_generation import get_image_generation_schema_part_2

            schema_obj = get_image_generation_schema_part_2()
        elif provider_type == "ollama":
            from app.llm.ollama.image_generation import get_image_generation_schema_part_2

            schema_obj = get_image_generation_schema_part_2()
    except Exception:
        logger.warning("Failed to build image generation tool settings schema", exc_info=True)
        return []

    schema_payload = _schema_to_payload(schema_obj)
    prefixed = _prefix_tool_schema_sections(
        schema_payload,
        key_prefix="tool_settings.image_generation",
        section_title_prefix="Image Generation Tool Settings",
    )
    return _apply_values_to_schema_sections(
        prefixed,
        {"tool_settings": {"image_generation": values}},
    )


def _build_audio_tool_settings_sections(db: Session) -> list[dict[str, Any]]:
    record = get_settings_page(db, "audio_generation")
    values = {
        "provider_id": "",
        "model_name": "",
        "voice": None,
        "response_format": None,
        "language": None,
        "sample_rate": None,
        "bit_rate": None,
        "speed": None,
        "optimize_streaming_latency": None,
        "text_normalization": None,
    }
    if record and isinstance(record.data, dict):
        values["provider_id"] = str(record.data.get("provider_id") or "").strip()
        values["model_name"] = str(record.data.get("model_name") or "").strip()
        voice_value = record.data.get("voice")
        if isinstance(voice_value, str) and voice_value.strip():
            values["voice"] = voice_value.strip()
        response_format_value = record.data.get("response_format")
        if isinstance(response_format_value, str) and response_format_value.strip():
            values["response_format"] = response_format_value.strip()
        for key in (
            "language",
            "sample_rate",
            "bit_rate",
            "speed",
            "optimize_streaming_latency",
            "text_normalization",
        ):
            if key in record.data:
                values[key] = record.data.get(key)

    provider_id = values["provider_id"]
    model_name = values["model_name"]
    if not provider_id or not model_name:
        return []

    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        return []

    provider_type = str(provider.provider or "").strip().lower()
    schema_obj = None
    try:
        if provider_type in {"openai", "openai_responses", "openai_chat_completions"}:
            from app.llm.openai.text_to_speech import get_audio_generation_schema_part_2

            schema_obj = get_audio_generation_schema_part_2(model_name, provider=provider)
        elif provider_type == "openrouter":
            from app.llm.openrouter.audio_generation import get_audio_generation_schema_part_2

            schema_obj = get_audio_generation_schema_part_2(model_name, provider=provider)
        elif provider_type == "google_aistudio":
            from app.llm.google_aistudio.text_to_speech import get_audio_generation_schema_part_2

            schema_obj = get_audio_generation_schema_part_2(model_name)
        elif provider_type == "elevenlabs":
            from app.llm.elevenlabs.text_to_speech import get_audio_generation_schema_part_2

            schema_obj = get_audio_generation_schema_part_2(
                api_key=provider.api_key,
                model_name=model_name,
            )
        elif provider_type == "xai":
            from app.llm.xai.text_to_speech import get_audio_generation_schema_part_2

            schema_obj = get_audio_generation_schema_part_2(
                model_name,
                provider=provider,
            )
    except Exception:
        logger.warning("Failed to build audio generation tool settings schema", exc_info=True)
        return []

    schema_payload = _schema_to_payload(schema_obj)
    prefixed = _prefix_tool_schema_sections(
        schema_payload,
        key_prefix="tool_settings.audio_generation",
        section_title_prefix="Audio Generation Tool Settings",
    )
    return _apply_values_to_schema_sections(
        prefixed,
        {"tool_settings": {"audio_generation": values}},
    )


def _build_music_tool_settings_sections(db: Session) -> list[dict[str, Any]]:
    record = get_settings_page(db, "music_generation")
    values = {
        "provider_id": "",
        "model_name": "",
        "response_format": "mp3",
        "enable_reference_images": False,
        "max_reference_images": 3,
    }
    if record and isinstance(record.data, dict):
        for key in (
            "provider_id",
            "model_name",
            "response_format",
            "enable_reference_images",
            "max_reference_images",
        ):
            if key in record.data:
                values[key] = record.data.get(key)
        values["provider_id"] = str(values.get("provider_id") or "").strip()
        values["model_name"] = str(values.get("model_name") or "").strip()
        values["response_format"] = str(values.get("response_format") or "mp3").strip().lower() or "mp3"

    provider_id = values["provider_id"]
    model_name = values["model_name"]
    if not provider_id or not model_name:
        return []

    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        return []

    if str(provider.provider or "").strip().lower() != "google_aistudio":
        return []

    try:
        from app.llm.google_aistudio.music_generation import get_music_generation_schema_part_2

        schema_obj = get_music_generation_schema_part_2(model_name)
    except Exception:
        logger.warning("Failed to build music generation tool settings schema", exc_info=True)
        return []

    schema_payload = _schema_to_payload(schema_obj)
    prefixed = _prefix_tool_schema_sections(
        schema_payload,
        key_prefix="tool_settings.music_generation",
        section_title_prefix="Music Generation Tool Settings",
    )
    return _apply_values_to_schema_sections(
        prefixed,
        {"tool_settings": {"music_generation": values}},
    )


def _build_video_tool_settings_sections(db: Session) -> list[dict[str, Any]]:
    record = get_settings_page(db, "video_generation")
    values = {
        "provider_id": "",
        "model_name": "",
        "duration_seconds": 6,
        "size": "720x1280",
        "aspect_ratio": None,
        "resolution": None,
        "seed": None,
        "generate_audio": None,
        "enable_reference_files": False,
        "timeout_seconds": 600,
        "poll_interval_seconds": 5,
        "max_retries": 2,
    }
    if record and isinstance(record.data, dict):
        for key in (
            "provider_id",
            "model_name",
            "duration_seconds",
            "size",
            "aspect_ratio",
            "resolution",
            "seed",
            "generate_audio",
            "enable_reference_files",
            "timeout_seconds",
            "poll_interval_seconds",
            "max_retries",
        ):
            if key in record.data:
                values[key] = record.data.get(key)
        values["provider_id"] = str(values.get("provider_id") or "").strip()
        values["model_name"] = str(values.get("model_name") or "").strip()

    provider_id = values["provider_id"]
    model_name = values["model_name"]
    if not provider_id or not model_name:
        return []

    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        return []

    provider_type = str(provider.provider or "").strip().lower()
    schema_obj = None
    try:
        if provider_type in {"openai_responses", "openai_chat_completions"}:
            from app.llm.openai_responses.video_generation import get_video_generation_schema_part_2

            schema_obj = get_video_generation_schema_part_2(model_name)
        elif provider_type == "openrouter":
            from app.llm.openrouter.video_generation import get_video_generation_schema_part_2

            schema_obj = get_video_generation_schema_part_2(model_name, provider=provider)
        elif provider_type == "google_aistudio":
            from app.llm.google_aistudio.video_generation import get_video_generation_schema_part_2

            schema_obj = get_video_generation_schema_part_2(model_name)
        elif provider_type == "xai":
            from app.llm.xai.video_generation import get_video_generation_schema_part_2

            schema_obj = get_video_generation_schema_part_2(model_name)
    except Exception:
        logger.warning("Failed to build video generation tool settings schema", exc_info=True)
        return []

    schema_payload = _schema_to_payload(schema_obj)
    prefixed = _prefix_tool_schema_sections(
        schema_payload,
        key_prefix="tool_settings.video_generation",
        section_title_prefix="Video Generation Tool Settings",
    )
    return _apply_values_to_schema_sections(
        prefixed,
        {"tool_settings": {"video_generation": values}},
    )


def _build_tool_settings_sections_for_model(
    db: Session,
    enabled_tool_names: set[str],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    if "audio_generation" in enabled_tool_names:
        sections.extend(_build_audio_tool_settings_sections(db))
    if "music_generation" in enabled_tool_names:
        sections.extend(_build_music_tool_settings_sections(db))
    if "image_generation" in enabled_tool_names:
        sections.extend(_build_image_tool_settings_sections(db))
    if "video_generation" in enabled_tool_names:
        sections.extend(_build_video_tool_settings_sections(db))
    return sections


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
# -------------------
# Get provider schema (with values if provider_id given)
# -------------------
@llm_router.get("/provider", dependencies=[Depends(verified_admin)])
def get_provider_schema_route(provider: ProviderEnum, provider_id: str | None = None, db: Session = Depends(get_db)):
    if provider_id:
        get_llm_provider(db, provider_id)
    return get_provider_schema(db, provider, provider_id)


@llm_router.post(
    "/byok/credential-token",
    response_model=ByokCredentialTokenResponse,
)
def issue_byok_credential_token_route(
    payload: ByokCredentialTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    """Exchange a raw provider key for a user-bound, reload-safe token."""

    _ensure_byok_allowed(user.id, db)
    try:
        credential_token, expires_at = issue_byok_credential_token(
            user_id=user.id,
            provider=payload.provider.value,
            provider_id=payload.provider_id,
            api_key=payload.api_key,
        )
    except ByokCredentialTokenError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "byok_credential_unavailable"},
        ) from exc

    # Record that a credential was sealed without logging the credential, the
    # sealed bearer value, or the client-supplied local provider ID. The same
    # pseudonymous provider-instance field is used by later BYOK request audit
    # events so operators can correlate them without retaining the raw ID.
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "BYOK_CREDENTIAL_TOKEN_ISSUED",
        {
            "provider": normalize_provider_value(payload.provider),
            "byok_provider_instance_hash": _hash_text(
                payload.provider_id,
                prefix="byok_provider_hash",
                keep=16,
            ),
            "expires_at": expires_at.isoformat(),
        },
        "llm_byok",
    )
    return ByokCredentialTokenResponse(
        credential_token=credential_token,
        expires_at=expires_at,
    )


@llm_router.get("/byok/provider-schema")
def get_byok_provider_schema_route(
    provider: ProviderEnum,
    db: Session = Depends(get_db),
    user = Depends(verified_user),
):
    _ensure_byok_allowed(user.id, db)
    schema = get_provider_schema(db, provider)
    schema_payload = _schema_to_payload(schema)
    schema_payload = sanitize_byok_provider_schema(schema_payload, provider.value)
    return schema_payload


@llm_router.post("/byok/model-schema")
def get_byok_model_schema_route(
    payload: ByokModelSchemaRequest,
    db: Session = Depends(get_db),
    user = Depends(verified_user),
):
    _ensure_byok_allowed(user.id, db)
    provider = ProviderEnum(normalize_provider_value(payload.provider))

    match provider:
        case ProviderEnum.openai:
            schema_obj = get_openai_model_schema(db, None, payload.model_name)
        case ProviderEnum.openai_responses:
            schema_obj = get_openai_model_schema(
                db,
                None,
                payload.model_name,
                openai_provider_type="openai_responses",
            )
        case ProviderEnum.xai:
            schema_obj = get_openai_model_schema(
                db,
                None,
                payload.model_name,
                openai_provider_type=ProviderEnum.xai.value,
            )
        case ProviderEnum.openai_chat_completions:
            schema_obj = get_openai_model_schema(
                db,
                None,
                payload.model_name,
                openai_provider_type="openai_chat_completions",
            )
        case ProviderEnum.microsoft_azure:
            schema_obj = get_openai_model_schema(
                db,
                None,
                payload.model_name,
                openai_provider_type="microsoft_azure",
            )
        case ProviderEnum.anthropic:
            schema_obj = get_anthropic_model_schema(
                db,
                None,
                payload.model_name,
                model_info=payload.model_info,
            )
        case ProviderEnum.anthropic_base:
            schema_obj = get_anthropic_model_schema(
                db,
                None,
                payload.model_name,
                anthropic_provider_type="anthropic_base",
                model_info=payload.model_info,
            )
        case ProviderEnum.google_aistudio:
            schema_obj = get_aistudio_model_schema(db, None, payload.model_name)
        case ProviderEnum.openrouter:
            schema_obj = get_openrouter_model_schema(
                db,
                None,
                payload.model_name,
                model_provider=payload.model_provider,
            )
        case ProviderEnum.ollama:
            schema_obj = get_ollama_model_schema(db, None, payload.model_name)
        case ProviderEnum.lmstudio:
            schema_obj = get_lmstudio_model_schema(db, None, payload.model_name)
        case _:
            raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider.value}'")

    schema_payload = _schema_to_payload(schema_obj)
    schema_payload = _remove_schema_fields(
        schema_payload,
        BYOK_MODEL_SCHEMA_EXCLUDED_FIELDS,
    )

    default_scrape_provider = get_user_group_setting_value(user.id, "chat", "byok_default_scrape_provider", db)
    default_search_provider = get_user_group_setting_value(user.id, "chat", "byok_default_search_provider", db)
    defaults_payload = {
        "settings": {
            "websearch_scrape_provider": default_scrape_provider or "",
            "websearch_search_provider": default_search_provider or "",
        }
    }
    schema_payload["sections"] = _apply_values_to_schema_sections(
        schema_payload.get("sections") or [],
        defaults_payload,
    )

    return {
        "supported": True,
        "schema": schema_payload,
    }


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------
@llm_router.get("/mcp/oauth/client-metadata.json")
def mcp_oauth_client_metadata_route(db: Session = Depends(get_db)):
    """Publish Omlorix's OAuth Client ID Metadata Document."""
    public_url = get_public_url(db).rstrip("/")
    redirect_uri = f"{public_url}/api/v1/llm/mcp/oauth/callback"
    return build_client_metadata(
        public_url=public_url,
        redirect_uri=redirect_uri,
    ).model_dump(by_alias=True, mode="json", exclude_none=True)


@llm_router.post(
    "/mcp/servers/admin/{server_id}/oauth/start",
    response_model=MCPOAuthStartResponse,
)
async def start_admin_mcp_oauth_route(
    server_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    access_token: str = Depends(verified_access_token),
    admin=Depends(verified_admin),
):
    """Start OAuth for an admin-owned remote MCP server."""
    server = get_mcp_server(db, server_id)
    if server.owner_type != OWNER_ADMIN:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    try:
        authorization_url = await start_mcp_oauth(
            db,
            server=server,
            user_id=admin.id,
            public_url=get_public_url(db).rstrip("/"),
            return_path="/admin/mcp-settings",
        )
        oauth_state = _mcp_oauth_state_from_authorization_url(authorization_url)
    except (ValueError, httpx2.HTTPError) as exc:
        logger.warning("Unable to start admin MCP OAuth for %s: %s", server.id, exc)
        raise HTTPException(status_code=400, detail="Unable to start MCP OAuth authorization.") from exc
    _set_mcp_oauth_callback_cookie(
        response,
        state=oauth_state,
        access_token=access_token,
        db=db,
        request=request,
    )
    _audit_llm_event(
        db_log,
        request,
        admin.id,
        "MCP_ADMIN_OAUTH_STARTED",
        {"server_id": server.id},
        "mcp",
    )
    return {"authorization_url": authorization_url}


@llm_router.post(
    "/mcp/servers/user/{server_id}/oauth/start",
    response_model=MCPOAuthStartResponse,
)
async def start_user_mcp_oauth_route(
    server_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    access_token: str = Depends(verified_access_token),
    user=Depends(verified_user),
):
    """Start OAuth for a personal remote MCP server."""
    require_group_mcp_enabled(user.id, db)
    server = get_mcp_server(db, server_id)
    if (
        server.owner_type != OWNER_USER
        or server.owner_user_id != user.id
        or server.managed_connection_id
    ):
        raise HTTPException(status_code=404, detail="MCP server not found.")
    try:
        authorization_url = await start_mcp_oauth(
            db,
            server=server,
            user_id=user.id,
            public_url=get_public_url(db).rstrip("/"),
            return_path="/workspace/connections",
        )
        oauth_state = _mcp_oauth_state_from_authorization_url(authorization_url)
    except (ValueError, httpx2.HTTPError) as exc:
        logger.warning("Unable to start user MCP OAuth for %s: %s", server.id, exc)
        raise HTTPException(status_code=400, detail="Unable to start MCP OAuth authorization.") from exc
    _set_mcp_oauth_callback_cookie(
        response,
        state=oauth_state,
        access_token=access_token,
        db=db,
        request=request,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_USER_OAUTH_STARTED",
        {"server_id": server.id},
        "mcp",
    )
    return {"authorization_url": authorization_url}


@llm_router.get("/mcp/oauth/callback")
async def complete_mcp_oauth_route(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    iss: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Complete MCP OAuth for the authenticated user who started the flow."""
    if not state:
        return RedirectResponse(
            url="/workspace/connections?mcp_oauth_status=error",
            status_code=302,
        )
    user = _verified_mcp_oauth_callback_user(
        request,
        state=state,
        credentials=credentials,
        db=db,
    )
    user_is_admin = is_admin_role(getattr(user, "role", None))
    if error or not code:
        state_row = db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first()
        return_path = _safe_mcp_oauth_return_path(
            state_row.return_path if state_row else None
        )
        try:
            aborted = abort_mcp_oauth(
                db,
                state,
                authorization_issuer=iss,
                expected_user_id=user.id,
                expected_user_is_admin=user_is_admin,
            )
        except Exception:
            # Do not log or display attacker-controlled OAuth error details.
            # Invalid issuer, user, ownership, and role checks all fail closed.
            logger.warning("Rejected invalid MCP OAuth error callback")
            aborted = None
        if aborted:
            return_path = _safe_mcp_oauth_return_path(aborted[1])
        if aborted:
            _audit_llm_event(
                db_log,
                request,
                aborted[0],
                "MCP_OAUTH_DENIED",
                {"oauth_error": str(error or "missing_code")[:120]},
                "mcp",
            )
        redirect = RedirectResponse(
            url=f"{return_path}?{urlencode({'mcp_oauth_status': 'error'})}",
            status_code=302,
        )
        _clear_mcp_oauth_callback_cookie(
            redirect,
            state=state,
            db=db,
            request=request,
        )
        return redirect
    state_row = db.query(MCPOAuthState).filter(MCPOAuthState.state == state).first()
    return_path = _safe_mcp_oauth_return_path(
        state_row.return_path if state_row else None
    )
    state_user_id = state_row.user_id if state_row else None
    state_server_id = state_row.server_id if state_row else None
    try:
        server, user_id, completed_return_path = await complete_mcp_oauth(
            db,
            state=state,
            code=code,
            authorization_issuer=iss,
            expected_user_id=user.id,
            expected_user_is_admin=user_is_admin,
        )
        return_path = _safe_mcp_oauth_return_path(completed_return_path)
        _audit_llm_event(
            db_log,
            request,
            user_id,
            "MCP_OAUTH_COMPLETED",
            {"server_id": server.id, "owner_type": server.owner_type},
            "mcp",
        )
        query = urlencode({"mcp_oauth_status": "connected", "mcp_server_id": server.id})
    except ValueError:
        # Invalid state, user, ownership, role, issuer, or provider responses
        # are expected rejection paths and must not emit attacker-triggered
        # stack traces.
        logger.warning("Rejected invalid MCP OAuth callback")
        if state_user_id is not None:
            _audit_llm_event(
                db_log,
                request,
                state_user_id,
                "MCP_OAUTH_FAILED",
                {"server_id": state_server_id},
                "mcp",
            )
        query = urlencode({"mcp_oauth_status": "error"})
    except Exception:
        logger.exception("MCP OAuth callback failed")
        if state_user_id is not None:
            _audit_llm_event(
                db_log,
                request,
                state_user_id,
                "MCP_OAUTH_FAILED",
                {"server_id": state_server_id},
                "mcp",
            )
        query = urlencode({"mcp_oauth_status": "error"})
    redirect = RedirectResponse(url=f"{return_path}?{query}", status_code=302)
    _clear_mcp_oauth_callback_cookie(
        redirect,
        state=state,
        db=db,
        request=request,
    )
    return redirect


@llm_router.get("/mcp/servers/admin", response_model=List[MCPServerListItem], dependencies=[Depends(verified_admin)])
def list_admin_mcp_servers_route(db: Session = Depends(get_db)):
    return list_admin_servers_payload(db)


@llm_router.get("/mcp/servers/admin/export", dependencies=[Depends(verified_admin)])
def export_admin_mcp_servers_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    payload = export_admin_servers_bundle(db)
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="export_admin_mcp_servers",
        details={"server_count": len(payload.get("data", {}).get("servers", []))},
        ip_address=get_audit_request_ip(request, db),
    )
    return payload


@llm_router.post("/mcp/servers/admin/import", dependencies=[Depends(verified_admin)])
def import_admin_mcp_servers_route(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    result = import_admin_servers_bundle(db, payload)
    create_audit_log(
        db_log=db_log,
        user_id=admin.id,
        action="import_admin_mcp_servers",
        details={
            "created_count": len(result.get("created", [])),
            "error_count": len(result.get("errors", [])),
        },
        ip_address=get_audit_request_ip(request, db),
    )
    return result


@llm_router.post("/mcp/servers/admin", response_model=MCPServerDetail, dependencies=[Depends(verified_admin)])
def create_admin_mcp_server_route(
    payload: CreateMCPServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    result = create_admin_mcp_server(db, payload)
    _audit_llm_event(db_log, request, admin.id, "MCP_ADMIN_SERVER_CREATED", _mcp_server_audit_details(result), "mcp")
    return result


@llm_router.get("/mcp/servers/admin/{server_id}", response_model=MCPServerDetail, dependencies=[Depends(verified_admin)])
def get_admin_mcp_server_route(server_id: str, db: Session = Depends(get_db)):
    return get_admin_server_payload(db, server_id)


@llm_router.patch("/mcp/servers/admin/{server_id}", response_model=MCPServerDetail, dependencies=[Depends(verified_admin)])
def update_admin_mcp_server_route(
    server_id: str,
    payload: UpdateMCPServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    result = update_admin_server_payload(db, server_id, payload)
    details = _mcp_server_audit_details(result)
    details["updated_fields"] = sorted(getattr(payload, "model_fields_set", set()))
    _audit_llm_event(db_log, request, admin.id, "MCP_ADMIN_SERVER_UPDATED", details, "mcp")
    return result


@llm_router.delete("/mcp/servers/admin/{server_id}", dependencies=[Depends(verified_admin)])
def delete_admin_mcp_server_route(
    server_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    delete_admin_server_payload(db, server_id)
    _audit_llm_event(db_log, request, admin.id, "MCP_ADMIN_SERVER_DELETED", {"server_id": server_id}, "mcp")
    return {"status": "success"}


@llm_router.post("/mcp/servers/admin/test", response_model=MCPToolPreviewResponse, dependencies=[Depends(verified_admin)])
def test_admin_mcp_server_route(
    payload: MCPServerTestRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin=Depends(verified_admin),
):
    existing_server = _resolve_existing_mcp_test_server(
        db,
        getattr(payload, "server_id", None),
        owner_type=OWNER_ADMIN,
    )
    server = MCPServer(
        owner_type="admin",
        owner_user_id=None,
        name=payload.name,
        description=payload.description,
        namespace=payload.namespace,
        transport=payload.transport,
        # A connection test must work for a saved-disabled draft so admins can
        # validate it safely before making tools available to users.
        enabled=True,
        url=payload.url,
        command=None,
        args=[],
        headers=resolve_mcp_headers_from_payload(payload, existing_server),
        auth_mode=payload.auth_mode,
        oauth=resolve_mcp_oauth_from_payload(payload, existing_server),
        env={},
        timeout_seconds=payload.timeout_seconds,
        status={"available": "unknown", "tool_count": 0},
    )
    result = {"tools": preview_server_tools(db, server), "source": "bridge"}
    _audit_llm_event(
        db_log,
        request,
        admin.id,
        "MCP_ADMIN_SERVER_TESTED",
        {"transport": payload.transport, "tool_count": len(result["tools"])},
        "mcp",
    )
    return result


@llm_router.get("/mcp/servers/user", response_model=List[MCPServerListItem])
def list_user_mcp_servers_route(db: Session = Depends(get_db), user=Depends(verified_user)):
    return list_user_servers_payload(db, user.id)


@llm_router.get("/mcp/connectors/mentions", response_model=List[MCPMentionConnector])
def list_mcp_mention_connectors_route(
    model_id: str = Query(min_length=1, max_length=255),
    project_id: str | None = Query(default=None, max_length=255),
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """List MCP connectors the selected model may use in the next request.

    This endpoint intentionally works independently of the custom-settings
    sidebar. Models may disable that UI while still supporting administrator-
    configured MCP tools, and the chat composer must retain the same opt-in
    request allowlist in that case.
    """
    from app.agents.utils import resolve_selected_model_for_user
    from app.tools.utils import resolve_enabled_tools

    resolved_selection = resolve_selected_model_for_user(
        db,
        user_id=user.id,
        model_id=model_id,
    )
    model = resolved_selection.base_model
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    tool_resolution = resolve_enabled_tools(
        model.tools or [],
        db=db,
        model_settings=model_settings,
        user_id=user.id,
        project_id=project_id,
    )
    if not tool_resolution.get("mcp_requested"):
        return []
    return list_mcp_mention_connectors(
        db,
        user.id,
        model_settings=model_settings,
    )


@llm_router.post("/mcp/servers/user", response_model=MCPServerDetail)
def create_user_mcp_server_route(
    payload: CreateMCPServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = create_user_mcp_server(db, user.id, payload)
    _audit_llm_event(db_log, request, user.id, "MCP_USER_SERVER_CREATED", _mcp_server_audit_details(result), "mcp")
    return result


@llm_router.get("/mcp/servers/user/{server_id}", response_model=MCPServerDetail)
def get_user_mcp_server_route(server_id: str, db: Session = Depends(get_db), user=Depends(verified_user)):
    return get_user_server_payload(db, user.id, server_id)


@llm_router.patch("/mcp/servers/user/{server_id}", response_model=MCPServerDetail)
def update_user_mcp_server_route(
    server_id: str,
    payload: UpdateMCPServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = update_user_server_payload(db, user.id, server_id, payload)
    details = _mcp_server_audit_details(result)
    details["updated_fields"] = sorted(getattr(payload, "model_fields_set", set()))
    _audit_llm_event(db_log, request, user.id, "MCP_USER_SERVER_UPDATED", details, "mcp")
    return result


@llm_router.delete("/mcp/servers/user/{server_id}")
def delete_user_mcp_server_route(
    server_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    delete_user_server_payload(db, user.id, server_id)
    _audit_llm_event(db_log, request, user.id, "MCP_USER_SERVER_DELETED", {"server_id": server_id}, "mcp")
    return {"status": "success"}


@llm_router.post("/mcp/servers/user/test", response_model=MCPToolPreviewResponse)
def test_user_mcp_server_route(
    payload: MCPServerTestRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    require_group_mcp_enabled(user.id, db)
    existing_server = _resolve_existing_mcp_test_server(
        db,
        getattr(payload, "server_id", None),
        owner_type=OWNER_USER,
        owner_user_id=user.id,
    )
    server = MCPServer(
        owner_type="user",
        owner_user_id=user.id,
        name=payload.name,
        description=payload.description,
        namespace=payload.namespace,
        transport=payload.transport,
        # Testing is an explicit one-off action and must not require enabling
        # the persisted integration first.
        enabled=True,
        url=payload.url,
        command=None,
        args=[],
        headers=resolve_mcp_headers_from_payload(payload, existing_server),
        auth_mode=payload.auth_mode,
        oauth=resolve_mcp_oauth_from_payload(payload, existing_server),
        env={},
        timeout_seconds=payload.timeout_seconds,
        status={"available": "unknown", "tool_count": 0},
    )
    result = {"tools": preview_server_tools(db, server), "source": "bridge"}
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_USER_SERVER_TESTED",
        {"transport": payload.transport, "tool_count": len(result["tools"])},
        "mcp",
    )
    return result


@llm_router.post("/mcp/apps/tools/list")
def list_mcp_app_tools_route(
    payload: MCPAppServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = list_mcp_app_tools_payload(
        db,
        user_id=user.id,
        server_id=payload.server_id,
        access_server_ids=payload.access_server_ids,
        app_access_token=payload.app_access_token,
        tool_call_id=payload.tool_call_id,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_APP_TOOLS_LISTED",
        {"server_id": payload.server_id, "access_server_count": len(payload.access_server_ids or []), "tool_count": len(result.get("tools", [])) if isinstance(result, dict) else None},
        "mcp_app",
    )
    return result


@llm_router.post("/mcp/apps/resources/list")
def list_mcp_app_resources_route(
    payload: MCPAppServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = list_mcp_app_resources_payload(
        db,
        user_id=user.id,
        server_id=payload.server_id,
        access_server_ids=payload.access_server_ids,
        app_access_token=payload.app_access_token,
        tool_call_id=payload.tool_call_id,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_APP_RESOURCES_LISTED",
        {"server_id": payload.server_id, "access_server_count": len(payload.access_server_ids or []), "resource_count": len(result.get("resources", [])) if isinstance(result, dict) else None},
        "mcp_app",
    )
    return result


@llm_router.post("/mcp/apps/resources/read")
def read_mcp_app_resource_route(
    payload: MCPAppResourceReadRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = read_mcp_app_resource_payload(
        db,
        user_id=user.id,
        server_id=payload.server_id,
        uri=payload.uri,
        access_server_ids=payload.access_server_ids,
        app_access_token=payload.app_access_token,
        tool_call_id=payload.tool_call_id,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_APP_RESOURCE_READ",
        {"server_id": payload.server_id, "uri": payload.uri, "access_server_count": len(payload.access_server_ids or [])},
        "mcp_app",
    )
    return result


@llm_router.post("/mcp/apps/frame", response_model=MCPAppFrameCreateResponse)
def create_mcp_app_frame_route(
    payload: MCPAppFrameCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = create_mcp_app_frame_payload(
        db,
        user_id=user.id,
        server_id=payload.server_id,
        html=payload.html,
        resource_meta=payload.resource_meta,
        app_access_token=payload.app_access_token,
        tool_call_id=payload.tool_call_id,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_APP_FRAME_CREATED",
        {"server_id": payload.server_id, "html_length": len(payload.html or "")},
        "mcp_app",
    )
    return result


@llm_router.get("/mcp/apps/frame/{frame_id}")
def get_mcp_app_frame_route(frame_id: str):
    frame = get_mcp_app_frame_payload(frame_id)
    return Response(
        content=frame["html"],
        media_type="text/html; charset=utf-8",
        headers=frame["headers"],
    )


@llm_router.get("/mcp/apps/sandbox-proxy")
def get_mcp_app_sandbox_proxy_route():
    """Serve the trusted MCP Apps bridge with a Safari-compatible response CSP."""
    proxy = get_mcp_app_sandbox_proxy_payload()
    return Response(
        content=proxy["html"],
        media_type="text/html; charset=utf-8",
        headers=proxy["headers"],
    )


@llm_router.post("/widgets/frame", response_model=WidgetFrameCreateResponse)
def create_widget_frame_route(
    payload: WidgetFrameCreateRequest,
    request: Request,
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = create_widget_frame_payload(
        user_id=user.id,
        html=payload.html,
        widget_type=payload.widget_type,
        theme_mode=payload.theme_mode,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "WIDGET_FRAME_CREATED",
        {
            "widget_type": payload.widget_type or "unknown",
            "html_length": len(payload.html or ""),
        },
        "widget",
    )
    return result


@llm_router.get("/widgets/frame/{frame_id}")
def get_widget_frame_route(frame_id: str):
    frame = get_widget_frame_payload(frame_id)
    return Response(
        content=frame["html"],
        media_type="text/html; charset=utf-8",
        headers=frame["headers"],
    )


@llm_router.post("/mcp/apps/token/refresh", response_model=MCPAppTokenRefreshResponse)
def refresh_mcp_app_access_token_route(
    payload: MCPAppServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = refresh_mcp_app_access_token_payload(
        db,
        user_id=user.id,
        server_id=payload.server_id,
        app_access_token=payload.app_access_token,
        tool_call_id=payload.tool_call_id,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_APP_TOKEN_REFRESHED",
        {"server_id": payload.server_id, "access_server_count": len(payload.access_server_ids or [])},
        "mcp_app",
    )
    return result


@llm_router.post("/mcp/apps/resources/templates/list")
def list_mcp_app_resource_templates_route(
    payload: MCPAppServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = list_mcp_app_resource_templates_payload(
        db,
        user_id=user.id,
        server_id=payload.server_id,
        access_server_ids=payload.access_server_ids,
        app_access_token=payload.app_access_token,
        tool_call_id=payload.tool_call_id,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_APP_RESOURCE_TEMPLATES_LISTED",
        {"server_id": payload.server_id, "access_server_count": len(payload.access_server_ids or []), "template_count": len(result.get("resourceTemplates", [])) if isinstance(result, dict) else None},
        "mcp_app",
    )
    return result


@llm_router.post("/mcp/apps/prompts/list")
def list_mcp_app_prompts_route(
    payload: MCPAppServerRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = list_mcp_app_prompts_payload(
        db,
        user_id=user.id,
        server_id=payload.server_id,
        access_server_ids=payload.access_server_ids,
        app_access_token=payload.app_access_token,
        tool_call_id=payload.tool_call_id,
    )
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_APP_PROMPTS_LISTED",
        {"server_id": payload.server_id, "access_server_count": len(payload.access_server_ids or []), "prompt_count": len(result.get("prompts", [])) if isinstance(result, dict) else None},
        "mcp_app",
    )
    return result


@llm_router.post("/mcp/apps/tools/call")
def call_mcp_app_tool_route(
    payload: MCPAppToolCallRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    try:
        result = call_mcp_app_tool_payload(
            db,
            user_id=user.id,
            group_id=getattr(user, "group_id", None),
            server_id=payload.server_id,
            tool_name=payload.tool_name,
            arguments=payload.arguments,
            access_server_ids=payload.access_server_ids,
            app_access_token=payload.app_access_token,
            tool_call_id=payload.tool_call_id,
        )
    except HTTPException as exc:
        _audit_llm_event(
            db_log,
            request,
            user.id,
            "MCP_APP_TOOL_DENIED",
            {
                "server_id": payload.server_id,
                "tool_name": payload.tool_name,
                "status_code": exc.status_code,
            },
            "mcp_app",
        )
        raise
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MCP_APP_TOOL_CALLED",
        {"server_id": payload.server_id, "tool_name": payload.tool_name, "access_server_count": len(payload.access_server_ids or [])},
        "mcp_app",
    )
    return result



# -------------------
# Create provider
# -------------------
@llm_router.post("/provider", response_model=LLMProviderDetail)
def create_provider_route(
    payload: CreateProviderRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    settings_dict = jsonable_encoder(payload.settings)
    if not isinstance(settings_dict, dict):
        raise HTTPException(status_code=400, detail="Invalid settings payload")
    provider = ProviderEnum(normalize_provider_value(payload.provider))

    result = None

    # Keep icon handling in one place so every provider-specific create helper
    # follows the same native-vs-custom policy.
    icon = resolve_provider_icon(provider, payload.icon)

    match provider:
        case ProviderEnum.openai:
            result = create_openai_provider(db, payload.name, payload.api_key, settings_dict, icon=icon)
            metadata = {"action": "CREATE_OPENAI_PROVIDER", "category": "llm_openai"}
        case ProviderEnum.openai_responses:
            result = create_openai_provider(db, payload.name, payload.api_key, settings_dict, icon=icon, openai_provider_type="openai_responses")
            metadata = {"action": "CREATE_OPENAI_RESPONSES_PROVIDER", "category": "llm_openai_responses"}
        case ProviderEnum.xai:
            # The xAI provider uses Omlorix's OpenAI Responses transport for
            # chat, but keeps its own provider identity for every other API.
            settings_dict["base_url"] = (
                str(settings_dict.get("base_url") or "").strip().rstrip("/")
                or "https://api.x.ai/v1"
            )
            result = create_openai_provider(
                db,
                payload.name,
                payload.api_key,
                settings_dict,
                icon=icon,
                openai_provider_type=ProviderEnum.xai.value,
            )
            metadata = {"action": "CREATE_XAI_PROVIDER", "category": "llm_xai"}
        case ProviderEnum.openai_chat_completions:
            result = create_openai_provider(db, payload.name, payload.api_key, settings_dict, icon=icon, openai_provider_type="openai_chat_completions")
            metadata = {"action": "CREATE_OPENAI_CHAT_COMPLETIONS_PROVIDER", "category": "llm_openai_chat_completions"}
        case ProviderEnum.microsoft_azure:
            result = create_openai_provider(db, payload.name, payload.api_key, settings_dict, icon=icon, openai_provider_type="microsoft_azure")
            metadata = {"action": "CREATE_MICROSOFT_AZURE_PROVIDER", "category": "llm_microsoft_azure"}
        case ProviderEnum.anthropic:
            result = create_anthropic_provider(db, payload.name, payload.api_key, settings_dict, icon=icon)
            metadata = {"action": "CREATE_ANTHROPIC_PROVIDER", "category": "llm_anthropic"}
        case ProviderEnum.anthropic_base:
            result = create_anthropic_provider(db, payload.name, payload.api_key, settings_dict, icon=icon, anthropic_provider_type="anthropic_base")
            metadata = {"action": "CREATE_ANTHROPIC_BASE_PROVIDER", "category": "llm_anthropic_base"}
        case ProviderEnum.openrouter:
            result = create_open_router_provider(db, payload.name, payload.api_key, settings_dict, icon=icon)
            metadata = {"action": "CREATE_OPENROUTER_PROVIDER", "category": "llm_openrouter"}
        case ProviderEnum.ollama:
            result = create_ollama_provider(db, payload.name, payload.api_key, settings_dict, icon=icon)
            metadata = {"action": "CREATE_OLLAMA_PROVIDER", "category": "llm_ollama"}
        case ProviderEnum.lmstudio:
            result = create_lmstudio_provider(db, payload.name, payload.api_key, settings_dict, icon=icon)
            metadata = {"action": "CREATE_LMSTUDIO_PROVIDER", "category": "llm_lmstudio"}
        case ProviderEnum.google_aistudio:
            result = create_aistudio_provider(db, payload.name, payload.api_key, settings_dict, icon=icon)
            metadata = {"action": "CREATE_GOOGLE_AISTUDIO_PROVIDER", "category": "llm_google_aistudio"}
        case ProviderEnum.elevenlabs:
            status = {
                "available": "unknown",
                "model_list": [],
                "supports_model_list": True,
            }
            result = create_llm_provider(
                db,
                payload.provider.value,
                payload.name,
                payload.api_key,
                settings_dict,
                status=status,
                icon=icon,
            )
            result = refresh_provider_status_snapshot(db, result.id)
            metadata = {"action": "CREATE_ELEVENLABS_PROVIDER", "category": "llm_elevenlabs"}

    if result is None:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider}'")
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action=metadata["action"],
        details={
            "provider": provider.value,
            "name": payload.name,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category=metadata["category"],
    )
    result.status = normalize_llm_provider_status(result)
    return serialize_llm_provider_detail(result)



# -------------------
# Update provider
# -------------------
@llm_router.put("/provider", response_model=LLMProviderDetail)
def update_provider_route(
    provider_id: str,
    payload: UpdateProviderPayload,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    current_provider = get_llm_provider(db, provider_id)
    try:
        provider_enum = ProviderEnum(normalize_provider_value(current_provider.provider))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{current_provider.provider}'")

    if not provider_api_key_is_optional(provider_enum):
        stored_api_key = getattr(current_provider, "api_key", None)
        supplied_api_key = payload.api_key
        has_stored_api_key = isinstance(stored_api_key, str) and bool(stored_api_key.strip())
        has_supplied_api_key = isinstance(supplied_api_key, str) and bool(supplied_api_key.strip())
        if not has_stored_api_key and not has_supplied_api_key:
            raise HTTPException(status_code=400, detail=f"Provider api_key is required for '{provider_enum.value}'.")

    settings_model = PROVIDER_SETTINGS_MODELS.get(provider_enum)
    if settings_model is None:
        raise HTTPException(status_code=400, detail=f"Settings schema not configured for provider '{provider_enum.value}'")

    if isinstance(payload.settings, settings_model):
        settings_payload = payload.settings.model_dump(exclude_unset=True)
    elif isinstance(payload.settings, BaseModel):
        settings_payload = payload.settings.model_dump(exclude_unset=True)
    else:
        settings_payload = payload.settings if isinstance(payload.settings, dict) else {}
    settings_payload = preserve_redacted_custom_headers_in_settings(current_provider.settings or {}, settings_payload)
    settings_obj = settings_model.model_validate(settings_payload)

    settings_dict = jsonable_encoder(settings_obj)
    if not isinstance(settings_dict, dict):
        raise HTTPException(status_code=400, detail="Invalid settings payload")

    update_llm_provider(
        db,
        provider_id,
        provider=provider_enum.value,
        name=payload.name,
        icon=payload.icon,
        api_key=payload.api_key,
        settings=settings_dict,
    )
    refreshed_provider = refresh_provider_status_snapshot(db, provider_id)


    PROVIDER_UPDATE_METADATA = {
        ProviderEnum.openai: {"action": "UPDATE_OPENAI_PROVIDER", "category": "llm_openai"},
        ProviderEnum.openai_responses: {"action": "UPDATE_OPENAI_RESPONSES_PROVIDER", "category": "llm_openai_responses"},
        ProviderEnum.xai: {"action": "UPDATE_XAI_PROVIDER", "category": "llm_xai"},
        ProviderEnum.openai_chat_completions: {"action": "UPDATE_OPENAI_CHAT_COMPLETIONS_PROVIDER", "category": "llm_openai_chat_completions"},
        ProviderEnum.microsoft_azure: {"action": "UPDATE_MICROSOFT_AZURE_PROVIDER", "category": "llm_microsoft_azure"},
        ProviderEnum.anthropic: {"action": "UPDATE_ANTHROPIC_PROVIDER", "category": "llm_anthropic"},
        ProviderEnum.anthropic_base: {"action": "UPDATE_ANTHROPIC_BASE_PROVIDER", "category": "llm_anthropic_base"},
        ProviderEnum.google_aistudio: {"action": "UPDATE_GOOGLE_AISTUDIO_PROVIDER", "category": "llm_google_aistudio"},
        ProviderEnum.openrouter: {"action": "UPDATE_OPENROUTER_PROVIDER", "category": "llm_openrouter"},
        ProviderEnum.ollama: {"action": "UPDATE_OLLAMA_PROVIDER", "category": "llm_ollama"},
        ProviderEnum.lmstudio: {"action": "UPDATE_LMSTUDIO_PROVIDER", "category": "llm_lmstudio"},
        ProviderEnum.elevenlabs: {"action": "UPDATE_ELEVENLABS_PROVIDER", "category": "llm_elevenlabs"},
    }

    metadata = PROVIDER_UPDATE_METADATA.get(provider_enum, {"action": "UPDATE_LLM_PROVIDER", "category": "llm_provider"})
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action=metadata["action"],
        details={
            "provider_id": provider_id,
            "provider": provider_enum.value,
            "name": payload.name,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category=metadata["category"],
    )

    refreshed_provider.status = normalize_llm_provider_status(refreshed_provider)
    return serialize_llm_provider_detail(refreshed_provider)



# -------------------
# Check provider group membership before deletion
# -------------------
@llm_router.get("/provider/groups", dependencies=[Depends(verified_admin)])
def get_provider_group_membership_route(provider_id: str, db: Session = Depends(get_db)):
    """Check if a provider belongs to any provider groups before deletion."""
    get_llm_provider(db, provider_id)
    groups = get_provider_groups_for_provider(db, provider_id)
    return {"provider_id": provider_id, "groups": groups}


# -------------------
# Delete provider
# -------------------
@llm_router.delete("/provider", dependencies=[Depends(verified_admin)])
def delete_llm_provider_route(
    provider_id: str,
    request: Request,
    handle_groups: bool = False,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """
    Delete a provider. If handle_groups=True, also remove the provider from any
    groups it belongs to (or delete groups that would have fewer than 2 members).
    """
    provider = get_llm_provider(db, provider_id)
    group_result = None
    if handle_groups:
        group_result = remove_provider_from_groups(db, provider_id)
    
    result = delete_llm_provider(db, provider_id)
    
    if group_result:
        result["group_actions"] = group_result
    _audit_llm_event(
        db_log,
        request,
        admin_user.id,
        "DELETE_LLM_PROVIDER",
        {
            "provider_id": provider_id,
            "provider": getattr(provider, "provider", None),
            "name": getattr(provider, "name", None),
            "handle_groups": handle_groups,
            "group_actions": group_result,
        },
        "llm_provider",
    )
    
    return result



# -------------------
# Test provider connection
# -------------------
@llm_router.post("/provider/test")
def test_llm_provider_route(
    payload: TestProviderPayload,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    if payload.provider_id:
        get_llm_provider(db, payload.provider_id)
    result = test_llm_provider(db, payload)
    _audit_llm_event(
        db_log,
        request,
        admin_user.id,
        "LLM_PROVIDER_TESTED",
        {
            "provider": getattr(payload, "provider", None),
            "provider_id": getattr(payload, "provider_id", None),
            "success": result.get("success") if isinstance(result, dict) else None,
        },
        "llm_provider",
    )
    return result


# -------------------
# List all provider
# -------------------
@llm_router.get("/providers", response_model=List[LLMProviderListItem], dependencies=[Depends(verified_admin)])
def list_llm_providers_route(
    provider: ProviderEnum | None = None,
    model_capable_only: bool = False,
    db: Session = Depends(get_db),
):
    providers = list_llm_provider(db, provider)
    if model_capable_only:
        allowed_provider_values = {entry.value for entry in MODEL_CAPABLE_PROVIDERS}
        providers = [
            entry
            for entry in providers
            if normalize_provider_value(entry.provider) in allowed_provider_values
        ]
    for p in providers:
        p.status = normalize_llm_provider_status(p)
    return providers



# -------------------
# List all possibly available provider
# -------------------
@llm_router.get("/providers/available", dependencies=[Depends(verified_admin)])
def list_llm_providers_available_route():
    return [
        {
            "id": provider.value,
        }
        for provider in ProviderEnum
    ]



# -------------------
# Export all llm providers
# -------------------
@llm_router.get("/providers/export")
def export_llm_providers_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    result = export_llm_providers(db)
    providers = result.get("data", {}).get("providers", [])
    configured_api_key_count = sum(
        1
        for provider in providers
        if provider.get("credentials", {}).get("api_key_configured") is True
    )
    required_api_key_count = sum(
        1
        for provider in providers
        if provider.get("credentials", {}).get("api_key_required") is True
    )
    provider_types = sorted(
        {
            provider.get("provider")
            for provider in providers
            if isinstance(provider.get("provider"), str) and provider.get("provider")
        }
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_LLM_PROVIDERS",
        details={
            "export_version": result.get("export_version"),
            "provider_count": len(providers),
            "provider_types": provider_types,
            "required_api_key_count": required_api_key_count,
            "configured_api_key_count": configured_api_key_count,
            "api_keys_exported": False,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_provider",
    )

    return result



# -------------------
# Import llm providers
# -------------------
@llm_router.post("/providers/import")
def import_llm_providers_route(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    result = import_llm_providers(db, payload)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="IMPORT_LLM_PROVIDERS",
        details={
            "created_count": len(result.get("created", [])),
            "error_count": len(result.get("errors", [])),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_provider",
    )
    return result






# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
# -------------------
# Get model schema (with values if model_id given)
# -------------------
@llm_router.get("/model", dependencies=[Depends(verified_admin)])
def get_model_schema_route(
    provider: ProviderEnum,
    provider_id: str,
    model_name: str | None = None,
    model_id: str | None = None,
    model_provider: str| None = None,
    db: Session = Depends(get_db),
):
    schema_obj = None
    provider = ProviderEnum(normalize_provider_value(provider))
    get_llm_provider(db, provider_id)
    if model_id:
        get_model(db, model_id)
    match provider:
        case ProviderEnum.openai:
            schema_obj = get_openai_model_schema(db, provider_id, model_name, model_id)
        case ProviderEnum.openai_responses:
            schema_obj = get_openai_model_schema(db, provider_id, model_name, model_id, openai_provider_type="openai_responses",)
        case ProviderEnum.xai:
            schema_obj = get_openai_model_schema(
                db,
                provider_id,
                model_name,
                model_id,
                openai_provider_type=ProviderEnum.xai.value,
            )
        case ProviderEnum.openai_chat_completions:
            schema_obj = get_openai_model_schema(db, provider_id, model_name, model_id, openai_provider_type="openai_chat_completions")
        case ProviderEnum.microsoft_azure:
            schema_obj = get_openai_model_schema(db, provider_id, model_name, model_id, openai_provider_type="microsoft_azure")
        case ProviderEnum.google_aistudio:
            schema_obj = get_aistudio_model_schema(db, provider_id, model_name, model_id)
        case ProviderEnum.anthropic:
            schema_obj = get_anthropic_model_schema(db, provider_id, model_name, model_id)
        case ProviderEnum.anthropic_base:
            schema_obj = get_anthropic_model_schema(
                db,
                provider_id,
                model_name,
                model_id,
                anthropic_provider_type="anthropic_base",
            )
        case ProviderEnum.openrouter:
            schema_obj = get_openrouter_model_schema(db, provider_id, model_name, model_id, model_provider)
        case ProviderEnum.ollama:
            schema_obj = get_ollama_model_schema(db, provider_id, model_name, model_id)
        case ProviderEnum.lmstudio:
            schema_obj = get_lmstudio_model_schema(db, provider_id, model_name, model_id)
        case _:
            raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider.value}'")

    return _schema_to_payload(schema_obj)



# -------------------
# Get openrouter model providers by model_name (for model creation flow)
# -------------------
@llm_router.get("/model/openrouter/providers/byname", dependencies=[Depends(verified_admin)])
def get_openrouter_model_providers_by_name_route(
    openrouter_provider_id: str,
    model_name: str,
    db: Session = Depends(get_db),
):
    if not model_name or not model_name.strip():
        raise HTTPException(status_code=422, detail="model_name is required")

    try:
        providers = get_model_providers(db, openrouter_provider_id, model_name.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"providers": providers}
# -------------------
# Create model
# -------------------
@llm_router.post("/model")
def create_provider_model_route(
    payload: CreateProviderModelRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    get_llm_provider(db, payload.provider_id)
    result = create_provider_model(db, payload)
    provider = ProviderEnum(normalize_provider_value(payload.provider))
    MODEL_CREATE_METADATA = {
        ProviderEnum.openai: {"action": "CREATE_OPENAI_MODEL", "category": "llm_openai", "provider_id_key": "openai_provider_id"},
        ProviderEnum.openai_chat_completions: {"action": "CREATE_OPENAI_CHAT_COMPLETIONS_MODEL", "category": "llm_openai_chat_completions", "provider_id_key": "openai_provider_id"},
        ProviderEnum.openai_responses: {"action": "CREATE_OPENAI_RESPONSES_MODEL", "category": "llm_openai_responses", "provider_id_key": "openai_provider_id"},
        ProviderEnum.xai: {"action": "CREATE_XAI_MODEL", "category": "llm_xai", "provider_id_key": "openai_provider_id"},
        ProviderEnum.microsoft_azure: {"action": "CREATE_MICROSOFT_AZURE_MODEL", "category": "llm_microsoft_azure", "provider_id_key": "openai_provider_id"},
        ProviderEnum.anthropic: {"action": "CREATE_ANTHROPIC_MODEL", "category": "llm_anthropic", "provider_id_key": "anthropic_provider_id"},
        ProviderEnum.anthropic_base: {"action": "CREATE_ANTHROPIC_BASE_MODEL", "category": "llm_anthropic_base", "provider_id_key": "anthropic_provider_id"},
        ProviderEnum.google_aistudio: {"action": "CREATE_GOOGLE_AISTUDIO_MODEL", "category": "llm_google_aistudio", "provider_id_key": "aistudio_provider_id"},
        ProviderEnum.openrouter: {"action": "CREATE_OPENROUTER_MODEL", "category": "llm_openrouter", "provider_id_key": "openrouter_provider_id"},
        ProviderEnum.ollama: {"action": "CREATE_OLLAMA_MODEL", "category": "llm_ollama", "provider_id_key": "ollama_provider_id"},
        ProviderEnum.lmstudio: {"action": "CREATE_LMSTUDIO_MODEL", "category": "llm_lmstudio", "provider_id_key": "lmstudio_provider_id"},
    }
    metadata = MODEL_CREATE_METADATA.get(provider, {"action": "CREATE_MODEL", "category": "llm_provider", "provider_id_key": "provider_id"})
    details = {
        metadata.get("provider_id_key", "provider_id"): payload.provider_id,
        "model": payload.model.model,
        "name": payload.model.name,
        "status": str(payload.model.status),
    }

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action=metadata["action"],
        details=details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category=metadata["category"],
    )

    return result



# -------------------
# Update model setting values
# -------------------
@llm_router.put("/model", dependencies=[Depends(verified_admin)])
def update_model_values_route(
    model_id: str,
    payload: UpdateModelPayload,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    # TODO
    model = get_model(db, model_id)
    try:
        provider_enum = ProviderEnum(normalize_provider_value(model.provider))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{model.provider}'")

    settings_model = PROVIDER_MODEL_SETTINGS_MODELS.get(provider_enum)
    validated_settings = None
    if settings_model is not None:
        if payload.settings is None:
            raise HTTPException(status_code=400, detail="Settings payload required.")
        existing_settings = model.settings if isinstance(model.settings, dict) else {}
        merged_settings = merge_settings_update(existing_settings, payload.settings)
        validated_settings = settings_model.model_validate(merged_settings)
    else:
        validated_settings = payload.settings or {}

    settings_payload = (
        validated_settings.model_dump(exclude_unset=True)
        if hasattr(validated_settings, "model_dump")
        else validated_settings
    )
    
    # Validate websearch providers if web_search tool is enabled
    _validate_websearch_providers(payload.tools, settings_payload)

    capabilities = determine_model_capabilities(
        provider_enum,
        settings_payload,
        payload.tools or [],
        model_name=payload.model_name or getattr(model, "model_name", None),
        existing_capabilities=getattr(model, "capabilities", None),
    )

    updated = update_model_entry(
        db,
        model_id,
        model_name=payload.model_name,
        name=payload.name,
        description=payload.description,
        model_icon=payload.model_icon,
        status=str(payload.status),
        tools=payload.tools or [],
        access=payload.access or {},
        settings=settings_payload,
        capabilities=capabilities,
        is_active=payload.is_active,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_MODEL_ADMIN",
        details={
            "model_id": model_id,
            "name": payload.name,
            "status": str(payload.status),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_model",
    )

    clear_llm_model_leaderboard_cache()

    return updated


def _prepare_model_update(
    db: Session,
    model: Models,
    payload,
    update_fields: set[str],
):
    try:
        provider_enum = ProviderEnum(normalize_provider_value(model.provider))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{model.provider}'")

    existing_settings = model.settings if isinstance(model.settings, dict) else {}
    settings_included = "settings" in update_fields
    merged_settings = existing_settings
    if settings_included:
        incoming_settings = payload.settings or {}
        if not isinstance(incoming_settings, dict):
            raise HTTPException(status_code=400, detail="Settings must be an object.")
        merged_settings = merge_settings_update(existing_settings, incoming_settings)

    settings_model = PROVIDER_MODEL_SETTINGS_MODELS.get(provider_enum)
    if settings_model is not None:
        validated_settings = settings_model.model_validate(merged_settings)
        settings_payload = validated_settings.model_dump(exclude_unset=True)
    else:
        settings_payload = merged_settings if isinstance(merged_settings, dict) else {}

    if "tools" in update_fields:
        tools_value = payload.tools or []
    else:
        tools_value = model.tools or []

    _validate_websearch_providers(tools_value, settings_payload)

    target_model_name = payload.model_name if "model_name" in update_fields else getattr(model, "model_name", None)
    capabilities = determine_model_capabilities(
        provider_enum,
        settings_payload,
        tools_value,
        model_name=target_model_name,
        existing_capabilities=getattr(model, "capabilities", None),
    )

    return {
        "model_name": payload.model_name if "model_name" in update_fields else None,
        "name": payload.name if "name" in update_fields else None,
        "description": payload.description if "description" in update_fields else None,
        "model_icon": payload.model_icon if "model_icon" in update_fields else None,
        "status": str(payload.status) if "status" in update_fields and payload.status is not None else None,
        "tools": tools_value if "tools" in update_fields else None,
        "access": payload.access if "access" in update_fields else None,
        "settings": settings_payload if settings_included else None,
        "capabilities": capabilities,
        "is_active": payload.is_active if "is_active" in update_fields else None,
    }


@llm_router.post("/models/bulk-update", dependencies=[Depends(verified_admin)])
def bulk_update_models_route(
    payload: BulkUpdateModelsPayload,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    model_ids = list(
        dict.fromkeys(
            model_id.strip()
            for model_id in payload.model_ids
            if isinstance(model_id, str) and model_id.strip()
        )
    )
    if not model_ids:
        raise HTTPException(status_code=400, detail="At least one model_id is required.")
    if len(model_ids) > 100:
        raise HTTPException(status_code=400, detail="Bulk update is limited to 100 models per request.")

    update_fields = set(payload.model_fields_set) - {"model_ids"}
    if not update_fields:
        raise HTTPException(status_code=400, detail="No update fields provided.")

    models = (
        db.query(Models)
        .filter(Models.id.in_(model_ids), Models.is_active.is_(True))
        .all()
    )
    models_by_id = {model.id: model for model in models}
    missing_model_ids = [model_id for model_id in model_ids if model_id not in models_by_id]
    if missing_model_ids:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "One or more models were not found.",
                "missing_model_ids": missing_model_ids,
            },
        )

    prepared_updates = []
    for model_id in model_ids:
        model = models_by_id[model_id]
        prepared_updates.append((model, _prepare_model_update(db, model, payload, update_fields)))

    updated_models = []
    try:
        for model, update_kwargs in prepared_updates:
            updated_models.append(
                update_model_entry(
                    db,
                    model.id,
                    commit=False,
                    **update_kwargs,
                )
            )
        db.commit()
        for updated_model in updated_models:
            db.refresh(updated_model)
    except SQLAlchemyError:
        db.rollback()
        raise

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="BULK_UPDATE_MODELS_ADMIN",
        details={
            "model_ids": model_ids,
            "updated_fields": sorted(update_fields),
            "updated_count": len(updated_models),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_model",
    )

    clear_llm_model_leaderboard_cache()

    return {
        "updated_count": len(updated_models),
        "models": updated_models,
    }



# -------------------
# Delete model
# -------------------
@llm_router.delete("/model")
def delete_model_route(
    model_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Delete a model by its database ID (admin only)."""
    get_model(db, model_id)
    result = delete_model(db, model_id)
    _audit_llm_event(
        db_log,
        request,
        admin_user.id,
        "MODEL_DELETED",
        {
            "model_id": model_id,
            "rate_limit_ids_updated": result.get("rate_limit_ids_updated", []),
            "rate_limit_ids_deleted": result.get("rate_limit_ids_deleted", []),
        },
    )
    return result



# -------------------
# Duplicate model
# -------------------
@llm_router.post("/model/duplicate")
def duplicate_model_route(
    model_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Duplicate a model (admin only). New model has identical fields; name gets a ' Copy' suffix."""
    get_model(db, model_id)
    result = duplicate_model(db, model_id)
    _audit_llm_event(
        db_log,
        request,
        admin_user.id,
        "MODEL_DUPLICATED",
        {"source_model_id": model_id, "new_model_id": result.get("id") if isinstance(result, dict) else None},
    )
    return result


# -------------------
# Get Model Settings schema for main chat area website, custom model settings
# -------------------
@llm_router.get(
    "/model/file-format-catalog",
    response_model=FileFormatCatalogResponse,
)
def model_file_format_catalog_route(
    response: Response,
    _user=Depends(verified_user),
):
    """Return the shared MIME catalog used to expand compact model groups.

    The data is static and contains no user or provider metadata.  A private
    browser cache prevents the catalog from being downloaded again for every
    model selection while avoiding shared-proxy surprises on authenticated
    installations.
    """
    response.headers["Cache-Control"] = "private, max-age=86400"
    return {"groups": supported_file_format_catalog()}


@llm_router.get(
    "/model/settings",
    response_model=ModelSettingsResponse,
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
)
def model_init_route(
    response: Response,
    provider: ProviderEnum,
    model_id: str,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user = Depends(verified_user)
):
    """Return the compact, authorized settings schema for a selected model."""
    from app.agents.utils import resolve_selected_model_for_user

    response.headers["Cache-Control"] = "private, no-store"
    resolved_selection = resolve_selected_model_for_user(db, user_id=user.id, model_id=model_id)
    model = resolved_selection.base_model
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    provider = ProviderEnum(normalize_provider_value(model.provider))

    def _supported_file_format_groups_payload() -> list[str]:
        """Describe attachment support without repeating the MIME catalog.

        SVG is always advertised because Omlorix converts it to ordinary text
        context before provider dispatch. It therefore does not require native
        image or document support from the selected model.
        """
        return supported_file_format_groups_for_model_input_formats(
            model_settings.get("input_formats")
        )

    supported_file_format_groups = _supported_file_format_groups_payload()
    allow_custom = _coerce_allow_custom_flag(model_settings.get("allow_custom_generation_parameter"))
    if not allow_custom:
        return {
            "supported": False,
            "message": "This model does not support custom model settings.",
            "supported_file_format_groups": supported_file_format_groups,
        }

    def _supported(schema_obj):
        schema_payload = _schema_to_compact_payload(schema_obj)
        return {
            "supported": True,
            "schema": schema_payload,
            "supported_file_format_groups": supported_file_format_groups,
        }

    match provider:
        case ProviderEnum.openai:
            schema = get_openai_model_schema_parameter(db, user.id, model_id, project_id)
            return _supported(schema)
        case ProviderEnum.openai_responses:
            schema = get_openai_model_schema_parameter(
                db,
                user.id,
                model_id,
                project_id,
                openai_provider_type="openai_responses",
            )
            return _supported(schema)
        case ProviderEnum.xai:
            schema = get_openai_model_schema_parameter(
                db,
                user.id,
                model_id,
                project_id,
                openai_provider_type=ProviderEnum.xai.value,
            )
            return _supported(schema)
        case ProviderEnum.openai_chat_completions:
            schema = get_openai_model_schema_parameter(
                db,
                user.id,
                model_id,
                project_id,
                openai_provider_type="openai_chat_completions",
            )
            return _supported(schema)
        case ProviderEnum.microsoft_azure:
            schema = get_openai_model_schema_parameter(
                db,
                user.id,
                model_id,
                project_id,
                openai_provider_type="microsoft_azure",
            )
            return _supported(schema)
        case ProviderEnum.google_aistudio:
            schema = get_aistudio_model_schema_parameter(db, user.id, model_id, project_id)
            return _supported(schema)
        case ProviderEnum.anthropic:
            schema = get_anthropic_model_schema_parameter(db, user.id, model_id, project_id)
            return _supported(schema)
        case ProviderEnum.anthropic_base:
            schema = get_anthropic_model_schema_parameter(
                db,
                user.id,
                model_id,
                project_id,
                anthropic_provider_type="anthropic_base",
            )
            return _supported(schema)
        case ProviderEnum.openrouter:
            schema = get_openrouter_model_schema_parameter(db, user.id, model_id, project_id)
            return _supported(schema)
        case ProviderEnum.ollama:
            from app.llm.ollama.schemas import get_ollama_model_schema_parameter

            schema = get_ollama_model_schema_parameter(db, user.id, model_id, project_id)
            return _supported(schema)
        case ProviderEnum.lmstudio:
            from app.llm.lmstudio.schemas import get_lmstudio_model_schema_parameter

            schema = get_lmstudio_model_schema_parameter(db, user.id, model_id, project_id)
            return _supported(schema)
        case _:
            raise HTTPException(status_code=400, detail="Unsupported provider")



# -------------------
# List models
# -------------------
@llm_router.get(
    "/models",
    response_model=List[ProviderModelListItem],
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
    response_model_exclude_unset=True,
)
def list_provider_models_route(request: Request, provider_id: str, db: Session = Depends(get_db), db_log: Session = Depends(get_db_log), admin_user = Depends(verified_admin)):
    get_llm_provider(db, provider_id)
    models = list_provider_models(db, provider_id)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_PROVIDER_MODELS",
        details={
            "provider_id": provider_id,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_provider",
    )
    return models



# -------------------
# List provider models BYOK (central)
# -------------------
@llm_router.post("/models/byok")
def list_provider_models_byok_route(
    payload: ListProviderModelsByokRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    _ensure_byok_allowed(user.id, db)
    provider = ProviderEnum(normalize_provider_value(payload.provider))
    api_key = _resolve_byok_credential_or_error(
        credential_token=payload.credential_token,
        user_id=user.id,
        provider=provider,
        provider_id=payload.provider_id,
    )

    # Provider-specific models continue to own validation of endpoint and
    # settings fields.  The raw key is inserted only after authentication and
    # sealed-token verification, so it never crosses the browser-storage or
    # external request-schema boundary.
    config_model = PROVIDER_BYOK_PAYLOAD_MODELS[provider]
    try:
        config = config_model.model_validate({**payload.config, "api_key": api_key})
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "byok_provider_configuration_invalid"},
        ) from exc
    config_dict = _ensure_byok_target_allowed(db, provider, config)
    try:
        result = _list_models_for_byok_provider(db, provider, config, config_dict)
        if result is None:
            raise HTTPException(
                status_code=424,
                detail={"code": "byok_model_discovery_failed"},
            )
    except HTTPException as exc:
        raise _byok_discovery_http_error(exc) from exc
    except Exception as exc:
        logger.warning(
            "BYOK model discovery failed for provider %s (error_type=%s)",
            provider.value,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=424,
            detail={"code": "byok_model_discovery_failed"},
        ) from exc



    MODEL_BYOK_METADATA = {
        ProviderEnum.openai: {"action": "LIST_OPENAI_MODELS_BYOK", "category": "llm_openai"},
        ProviderEnum.openai_responses: {"action": "LIST_OPENAI_RESPONSES_MODELS_BYOK", "category": "llm_openai_responses"},
        ProviderEnum.xai: {"action": "LIST_XAI_MODELS_BYOK", "category": "llm_xai"},
        ProviderEnum.openai_chat_completions: {"action": "LIST_OPENAI_CHAT_COMPLETIONS_MODELS_BYOK", "category": "llm_openai_chat_completions"},
        ProviderEnum.microsoft_azure: {"action": "LIST_MICROSOFT_AZURE_MODELS_BYOK", "category": "llm_microsoft_azure"},
        ProviderEnum.anthropic: {"action": "LIST_ANTHROPIC_MODELS_BYOK", "category": "llm_anthropic"},
        ProviderEnum.anthropic_base: {"action": "LIST_ANTHROPIC_BASE_MODELS_BYOK", "category": "llm_anthropic"},
        ProviderEnum.google_aistudio: {"action": "LIST_GOOGLE_AISTUDIO_MODELS_BYOK", "category": "llm_google_aistudio"},
        ProviderEnum.openrouter: {"action": "LIST_OPENROUTER_MODELS", "category": "llm_openrouter"},
        ProviderEnum.ollama: {"action": "LIST_OLLAMA_MODELS_BYOK", "category": "llm_ollama"},
        ProviderEnum.lmstudio: {"action": "LIST_LMSTUDIO_MODELS_BYOK", "category": "llm_lmstudio"},
    }

    metadata = MODEL_BYOK_METADATA.get(provider, {"action": "LIST_PROVIDER_MODELS_BYOK", "category": "llm_provider"})
    details = {"provider": provider.value}

    if provider in {
        ProviderEnum.openai,
        ProviderEnum.microsoft_azure,
    }:
        details.update(
            {
                "has_api_key": bool(getattr(config, "api_key", None)),
                "api_version": getattr(config, "api_version", None),
            }
        )
    elif provider == ProviderEnum.anthropic:
        details.update(
            {
                "has_api_key": bool(getattr(config, "api_key", None)),
            }
        )
    elif provider == ProviderEnum.google_aistudio:
        details.update(
            {
                "has_api_key": bool(getattr(config, "api_key", None)),
                "api_version": getattr(config, "api_version", None),
            }
        )
    elif provider == ProviderEnum.openrouter:
        details.update(
            {
                "has_api_key": bool(getattr(config, "api_key", None)),
                "parameters": getattr(config, "parameters", None),
            }
        )
    elif provider == ProviderEnum.ollama:
        details.update(
            {
                "base_url": getattr(config, "base_url", None),
                "has_api_key": bool(getattr(config, "api_key", None)),
            }
        )
    elif provider == ProviderEnum.lmstudio:
        details.update(
            {
                "base_url": getattr(config, "base_url", None),
                "has_api_key": bool(getattr(config, "api_key", None)),
            }
        )

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action=metadata["action"],
        details=details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category=metadata["category"],
    )

    return result



# -------------------
# List user models
# -------------------
@llm_router.get("/models/admin")
def list_admin_models_route(
    response: Response,
    user=Depends(verified_admin),
    db: Session = Depends(get_db),
):
    """Return full model records for the administrator model editor."""
    response.headers["Cache-Control"] = "private, no-store"
    return list_admin_models(db, user.id)


@llm_router.get(
    "/models/user",
    response_model=List[UserModelSummary],
    response_model_exclude_none=True,
)
def list_user_models_route(
    response: Response,
    include_agents: bool = Query(
        True,
        description="Include custom agent model entries in addition to base LLM models.",
    ),
    user = Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Return only the public model-selection metadata visible to the user."""
    response.headers["Cache-Control"] = "private, no-store"
    return list_user_models(db, user.id, include_agents=include_agents)


# -------------------
# Model setting presets
# -------------------
@llm_router.get("/models/{model_id}/presets", response_model=List[ModelSettingPresetListItem])
def list_model_setting_presets_route(
    model_id: str,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
):
    return list_user_model_setting_presets(db, user.id, model_id)


@llm_router.get(
    "/models/{model_id}/presets/{preset_id}",
    response_model=ModelSettingPresetDetail,
)
def get_model_setting_preset_route(
    model_id: str,
    preset_id: str,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
):
    return get_user_model_setting_preset(db, user.id, model_id, preset_id)


@llm_router.post(
    "/models/{model_id}/presets",
    response_model=ModelSettingPresetDetail,
)
def create_model_setting_preset_route(
    model_id: str,
    payload: CreateModelSettingPresetRequest,
    request: Request,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    result = create_user_model_setting_preset(db, user.id, model_id, payload.name, payload.settings)
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MODEL_SETTING_PRESET_CREATED",
        {"model_id": model_id, "preset_id": getattr(result, "id", None), "name": payload.name},
    )
    return result


@llm_router.delete("/models/{model_id}/presets/{preset_id}")
def delete_model_setting_preset_route(
    model_id: str,
    preset_id: str,
    request: Request,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    result = delete_user_model_setting_preset(db, user.id, model_id, preset_id)
    _audit_llm_event(
        db_log,
        request,
        user.id,
        "MODEL_SETTING_PRESET_DELETED",
        {"model_id": model_id, "preset_id": preset_id},
    )
    return result


# -------------------
# List models leaderboard
# -------------------
@llm_router.get("/models/leaderboard", response_model=LLMLeaderboardResponse)
def get_llm_model_leaderboard_route(request: Request, user = Depends(verified_user), db: Session = Depends(get_db), db_log: Session = Depends(get_db_log)):
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="VIEW_LLM_MODEL_LEADERBOARD",
        details={},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_model",
    )
    return get_llm_model_leaderboard(db, user.id)



# -------------------
# Export all llm models
# -------------------
@llm_router.get("/models/export")
def export_llm_models_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    result = export_llm_models(db)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_LLM_MODELS",
        details={
            "export_version": result.get("export_version"),
            "model_count": len((result.get("data") or {}).get("models", [])),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_model",
    )

    return result



# -------------------
# Import llm models
# -------------------
@llm_router.post("/models/import")
def import_llm_models_route(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    result = import_llm_models(db, payload)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="IMPORT_LLM_MODELS",
        details={
            "created_count": len(result.get("created", [])),
            "error_count": len(result.get("errors", [])),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_model",
    )

    return result







# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

_AUDIO_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


def _raise_audio_upload_too_large(size_bytes: int, upload_limit_bytes: int) -> None:
    raise HTTPException(
        status_code=400,
        detail=(
            f"File size ({size_bytes / (1024 * 1024):.2f}MB) exceeds the "
            f"{upload_limit_bytes / (1024 * 1024):.0f}MB limit"
        ),
    )


async def _read_audio_upload_with_limit(audio: UploadFile, upload_limit_bytes: int) -> bytes:
    declared_size = getattr(audio, "size", None)
    if isinstance(declared_size, int) and declared_size > upload_limit_bytes:
        _raise_audio_upload_too_large(declared_size, upload_limit_bytes)

    chunks: list[bytes] = []
    total_bytes = 0

    while True:
        read_size = min(
            _AUDIO_UPLOAD_READ_CHUNK_BYTES,
            max(upload_limit_bytes - total_bytes + 1, 1),
        )
        chunk = await audio.read(read_size)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > upload_limit_bytes:
            _raise_audio_upload_too_large(total_bytes, upload_limit_bytes)
        chunks.append(chunk)

    return b"".join(chunks)


@llm_router.post("/transcribe")
async def transcribe_audio_route(
    audio: UploadFile = File(...),
    duration_seconds: float | None = Form(default=None, gt=0, le=86_400),
    request: Request = None,
    user = Depends(verified_user),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """
    Transcribe audio file to text using the configured transcription provider.
    """
    if not audio.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    user_id = str(user.id)
    user_group_id = getattr(user, "group_id", None)
    user_role = str(getattr(user, "role", "") or "")
    audio_filename = str(audio.filename)

    dictation_settings = get_settings_page(db, "dictation")
    dictation_settings_data = (
        dictation_settings.data
        if dictation_settings and isinstance(dictation_settings.data, dict)
        else {}
    )
    transcription_enabled = bool(dictation_settings_data.get("transcription_enabled"))
    transcription_provider_id = dictation_settings_data.get("transcription_provider_id")
    transcription_model = dictation_settings_data.get("transcription_model")

    if not transcription_enabled:
        raise HTTPException(
            status_code=400,
            detail={"code": TRANSCRIPTION_NOT_ENABLED_ERROR_CODE},
        )
    if not isinstance(transcription_provider_id, str) or not transcription_provider_id.strip():
        raise HTTPException(status_code=400, detail="Transcription provider is not configured")
    if not isinstance(transcription_model, str) or not transcription_model.strip():
        raise HTTPException(status_code=400, detail="Transcription model is not configured")

    model_name = transcription_model.strip()

    provider_id = transcription_provider_id.strip()
    runtime = get_transcription_runtime_for_provider(db, provider_id)
    provider = snapshot_transcription_provider(runtime["provider"])
    provider_models = runtime["models"]
    allowed_formats = runtime["allowed_formats"]
    upload_limit_bytes = runtime["upload_limit_bytes"]

    if model_name not in provider_models:
        raise HTTPException(status_code=400, detail="Unsupported transcription model")
    
    # Validate file extension
    extension = audio_filename.rsplit(".", 1)[-1].lower() if "." in audio_filename else ""
    if extension not in allowed_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{extension}'. Supported formats: {', '.join(allowed_formats)}"
        )
    
    content = await _read_audio_upload_with_limit(audio, upload_limit_bytes)

    # Quotas are based on audio time, not file size or provider tokens. Duration
    # inspection performs file I/O and may invoke ffprobe, so keep it off the
    # request event loop. Client-reported duration is not trusted for quotas.
    measured_duration = await run_blocking_io(
        partial(
            measure_audio_duration_seconds,
            content,
            filename=audio_filename,
            reported_duration_seconds=duration_seconds,
        )
    )
    duration_admission = None
    if measured_duration is not None:
        duration_admission = admit_user_duration_rate_limit(
            db,
            user_id=user_id,
            group_id=user_group_id,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
            requested_seconds=max(1, int(math.ceil(measured_duration))),
        )
        if isinstance(duration_admission, dict):
            _audit_llm_event(
                db_log,
                request,
                user_id,
                "DICTATION_RATE_LIMITED",
                {
                    "rate_limit_id": duration_admission.get("rate_limit_id"),
                    "period": duration_admission.get("period"),
                    "requested_duration_seconds": max(1, int(math.ceil(measured_duration))),
                },
            )
            raise HTTPException(status_code=429, detail=duration_admission)
    else:
        # Existing installations without a dictation policy continue to work,
        # while a configured policy fails closed if duration cannot be measured.
        policy_probe = admit_user_duration_rate_limit(
            db,
            user_id=user_id,
            group_id=user_group_id,
            target_type=RATE_LIMIT_TARGET_TYPE_DICTATION,
            requested_seconds=1,
        )
        if policy_probe is not None:
            if isinstance(policy_probe, dict):
                raise HTTPException(status_code=429, detail=policy_probe)
            finalize_duration_rate_limit_admission(
                db,
                policy_probe.admission_id,
                consumed_seconds=0,
                final_status=RATE_LIMIT_ADMISSION_FAILED,
            )
            raise HTTPException(
                status_code=400,
                detail="Could not determine the dictation audio duration required for minute-based rate limiting.",
            )

        # With no dictation policy, retain the browser duration as a bounded
        # compatibility value for audit metadata only. It is never admitted or
        # charged against quota.
        measured_duration = await run_blocking_io(
            partial(
                measure_audio_duration_seconds,
                content,
                filename=audio_filename,
                reported_duration_seconds=duration_seconds,
                allow_reported_duration=True,
            )
        )

    admission_id = duration_admission.admission_id if duration_admission else None
    admission_finalized = False
    try:
        from app.workers.tool_jobs import external_media_enabled

        if external_media_enabled():
            from app.workers.media import (
                enqueue_transcription_job_async,
                wait_for_transcription_async,
            )

            audit_ip_address = get_audit_request_ip(request, db)
            release_db_session_before_provider_io(db)
            job = await enqueue_transcription_job_async(
                user_id=user_id,
                audio_bytes=content,
                filename=audio_filename,
                provider_id=provider_id,
                model_name=model_name,
                measured_duration=measured_duration,
                admission_id=admission_id,
                audit_ip_address=audit_ip_address,
                audit_user_agent=request.headers.get("user-agent"),
            )
            # From this point the durable job and its reconciler own the quota
            # reservation even if the browser disconnects or this wait times out.
            admission_finalized = True
            try:
                return await wait_for_transcription_async(job)
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "transcription_still_processing", "job_id": job.id},
                    headers={"Retry-After": "3"},
                ) from exc
            except Exception as exc:
                from app.workers.models import WorkerJobFailed

                if isinstance(exc, WorkerJobFailed):
                    raise HTTPException(
                        status_code=500,
                        detail={"code": exc.code, "message": "Transcription failed. Please try again."},
                    ) from exc
                raise

        # Provider upload and transcription can exceed the short dictation
        # lease. Keep the existing reservation active for the complete request,
        # including the provider's final transcript generation.
        # FastAPI may run on any AnyIO backend. A task group keeps the lease
        # heartbeat portable while guaranteeing cancellation before the
        # admission is finalized.
        release_db_session_before_provider_io(db)
        async with anyio.create_task_group() as task_group:
            if admission_id is not None:
                task_group.start_soon(
                    renew_dictation_duration_rate_limit_lease,
                    admission_id,
                )
            try:
                text = await transcribe_audio_bytes_for_provider(
                    provider,
                    model_name=model_name,
                    audio_bytes=content,
                    filename=audio_filename,
                )
            finally:
                task_group.cancel_scope.cancel()
        if admission_id is not None:
            finalize_duration_rate_limit_admission(
                db,
                admission_id,
                consumed_seconds=max(1, int(math.ceil(measured_duration or 0))),
                final_status=RATE_LIMIT_ADMISSION_COMPLETED,
            )
            admission_finalized = True
        _audit_llm_event(
            db_log,
            request,
            user_id,
            "AUDIO_TRANSCRIBED",
            {
                "provider_id": provider_id,
                "model_name": model_name,
                "filename": audio_filename,
                "audio_bytes": len(content),
                "audio_duration_seconds": round(measured_duration or 0, 3),
            },
        )
        return {"text": text}
    except HTTPException:
        raise
    except Exception as e:
        if admission_id is not None:
            finalize_duration_rate_limit_admission(
                db,
                admission_id,
                consumed_seconds=0,
                final_status=RATE_LIMIT_ADMISSION_FAILED,
            )
            admission_finalized = True
        logging.exception("Transcription error")
        raise HTTPException(
            status_code=500,
            detail=build_transcription_error_detail(
                e,
                is_admin=is_admin_role(user_role),
                fallback_message="Transcription failed. Please try again.",
            ),
        ) from e
    finally:
        # Request cancellation is a BaseException on supported Python versions
        # and therefore bypasses the provider-error handler above. Always
        # release an unfinished reservation so a disconnected browser cannot
        # strand the user's remaining dictation minutes.
        if admission_id is not None and not admission_finalized:
            finalize_duration_rate_limit_admission(
                db,
                admission_id,
                consumed_seconds=0,
                final_status=RATE_LIMIT_ADMISSION_FAILED,
            )



# ---------------------------------------------------------------------------
# Provider Groups - Load Balancing
# ---------------------------------------------------------------------------
# -------------------
# Create provider group
# -------------------
@llm_router.post("/provider-group")
def create_provider_group_route(
    payload: CreateProviderGroupRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    members_list = [{"provider_id": m.provider_id, "weight": m.weight} for m in payload.members]
    group = create_provider_group(db, payload.name, members_list, payload.icon)
    
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="CREATE_PROVIDER_GROUP",
        details={
            "group_id": group.id,
            "name": payload.name,
            "member_count": len(members_list),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_provider_group",
    )
    
    return get_group_with_provider_details(db, group.id)


# -------------------
# List provider groups
# -------------------
@llm_router.get("/provider-groups", response_model=List[ProviderGroupListItem])
def list_provider_groups_route(db: Session = Depends(get_db), admin_user = Depends(verified_admin)):
    groups = list_provider_groups(db)
    return [
        {
            "id": g.id,
            "name": g.name,
            "icon": g.icon,
            "member_count": len(g.members or []),
            "created_at": g.created_at,
        }
        for g in groups
    ]


# -------------------
# Get provider group detail
# -------------------
@llm_router.get("/provider-group")
def get_provider_group_route(group_id: str, db: Session = Depends(get_db), admin_user = Depends(verified_admin)):
    get_provider_group(db, group_id)
    return get_group_with_provider_details(db, group_id)


# -------------------
# Update provider group
# -------------------
@llm_router.put("/provider-group")
def update_provider_group_route(
    group_id: str,
    payload: UpdateProviderGroupRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    get_provider_group(db, group_id)
    members_list = None
    if payload.members is not None:
        members_list = [{"provider_id": m.provider_id, "weight": m.weight} for m in payload.members]
    
    group = update_provider_group(db, group_id, payload.name, members_list, payload.icon)
    
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_PROVIDER_GROUP",
        details={
            "group_id": group_id,
            "name": payload.name,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_provider_group",
    )
    
    return get_group_with_provider_details(db, group.id)


# -------------------
# Delete provider group
# -------------------
@llm_router.delete("/provider-group")
def delete_provider_group_route(
    group_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    get_provider_group(db, group_id)
    result = delete_provider_group(db, group_id)
    
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DELETE_PROVIDER_GROUP",
        details={"group_id": group_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_provider_group",
    )
    
    return result


# -------------------
# Get common models for a provider group
# -------------------
@llm_router.get("/provider-group/models")
def get_provider_group_models_route(group_id: str, db: Session = Depends(get_db), admin_user = Depends(verified_admin)):
    """Get the list of models common to ALL providers in the group."""
    get_provider_group(db, group_id)
    return get_group_common_models(db, group_id)


# ---------------------------------------------------------------------------
# User Rate Limits (non-admin)
# ---------------------------------------------------------------------------
@llm_router.get("/rate-limits/user")
def list_user_rate_limits_route(db: Session = Depends(get_db), user = Depends(verified_user)):
    """Return all active rate limits applicable to the current user with usage progress."""
    all_active = (
        db.query(RateLimit)
        .filter(
            RateLimit.is_active.is_(True),
            RateLimit.scope == "chat",
        )
        .all()
    )

    results = []
    for rl in all_active:
        if not rate_limit_targets_user(rl, user.id, getattr(user, "group_id", None)):
            continue

        target_type = getattr(rl, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL) or RATE_LIMIT_TARGET_TYPE_MODEL
        model_ids = list(rl.model_ids or [])
        tool_keys = list(getattr(rl, "tool_keys", None) or [])
        usage_snapshot = get_rate_limit_usage_snapshot(db, rl, user.id)

        # Resolve model names
        model_rows = db.query(Models).filter(Models.id.in_(model_ids)).all() if model_ids else []
        model_lookup = {m.id: m.name for m in model_rows}

        results.append({
            "id": rl.id,
            "name": rl.name,
            "target_type": target_type,
            "scope": getattr(rl, "scope", "chat"),
            "period": rl.period,
            "timezone": getattr(rl, "timezone", "UTC"),
            "quota_unit": getattr(rl, "quota_unit", RATE_LIMIT_QUOTA_UNIT_REQUESTS),
            "quota_value": int(getattr(rl, "quota_value", rl.max_requests) or 0),
            "is_active": bool(rl.is_active),
            "current_usage": usage_snapshot["current_usage"],
            "remaining_usage": usage_snapshot["remaining_usage"],
            "current_usage_seconds": usage_snapshot.get("current_usage_seconds"),
            "remaining_usage_seconds": usage_snapshot.get("remaining_usage_seconds"),
            "max_requests": (
                int(getattr(rl, "quota_value", rl.max_requests) or 0)
                if getattr(rl, "quota_unit", RATE_LIMIT_QUOTA_UNIT_REQUESTS) == RATE_LIMIT_QUOTA_UNIT_REQUESTS
                else None
            ),
            "current_count": (
                int(usage_snapshot["current_usage"])
                if getattr(rl, "quota_unit", RATE_LIMIT_QUOTA_UNIT_REQUESTS) == RATE_LIMIT_QUOTA_UNIT_REQUESTS
                else None
            ),
            "resets_at": usage_snapshot["window_end"].isoformat(),
            "models": [
                {"id": mid, "name": model_lookup.get(mid, mid)}
                for mid in model_ids
            ],
            "tools": [
                get_rate_limit_tool(db, tool_key) or {
                    "key": tool_key,
                    "id": tool_key,
                    "name": tool_key,
                    "label": tool_key,
                    "description": "",
                    "source": "unknown",
                    "available": False,
                }
                for tool_key in tool_keys
            ],
        })

    return results


@llm_router.get("/rate-limit-tools")
def list_rate_limit_tools_route(db: Session = Depends(get_db), admin_user = Depends(verified_admin)):
    return list_rate_limit_tools(db)


# ---------------------------------------------------------------------------
# Rate Limits (admin)
# ---------------------------------------------------------------------------
@llm_router.post("/rate-limit", response_model=RateLimitMutationResponse)
def create_rate_limit_route(
    payload: CreateRateLimitRequest,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    conflicts = (
        check_rate_limit_conflicts(
            db,
            target_type=payload.target_type.value,
            model_ids=payload.model_ids,
            tool_keys=payload.tool_keys,
            user_ids=payload.user_ids,
            group_ids=payload.group_ids,
        )
        if payload.is_active
        else []
    )
    if conflicts and not force:
        return {"created": None, "conflicts": conflicts}

    rate_limit_obj = create_rate_limit(
        db,
        name=payload.name,
        target_type=payload.target_type.value,
        model_ids=payload.model_ids,
        tool_keys=payload.tool_keys,
        user_ids=payload.user_ids,
        group_ids=payload.group_ids,
        scope="chat",
        period=payload.period.value,
        timezone_name=payload.timezone,
        quota_unit=payload.quota_unit.value,
        quota_value=payload.quota_value,
        max_requests=payload.max_requests,
        is_active=payload.is_active,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="CREATE_RATE_LIMIT",
        details={
            "rate_limit_id": rate_limit_obj.id,
            "name": rate_limit_obj.name,
            "target_type": getattr(rate_limit_obj, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL),
            "model_count": len(rate_limit_obj.model_ids or []),
            "tool_count": len(getattr(rate_limit_obj, "tool_keys", None) or []),
            "user_count": len(rate_limit_obj.user_ids or []),
            "group_count": len(rate_limit_obj.group_ids or []),
            "scope": rate_limit_obj.scope,
            "period": rate_limit_obj.period,
            "timezone": getattr(rate_limit_obj, "timezone", "UTC"),
            "quota_unit": rate_limit_obj.quota_unit,
            "quota_value": int(rate_limit_obj.quota_value or 0),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_rate_limit",
    )

    return {"created": _build_rate_limit_payload(db, rate_limit_obj), "conflicts": []}


@llm_router.get("/rate-limits", response_model=List[RateLimitListItem])
def list_rate_limits_route(db: Session = Depends(get_db), admin_user = Depends(verified_admin)):
    rate_limit_rows = list_rate_limits(db)
    return [_build_rate_limit_payload(db, row) for row in rate_limit_rows]


@llm_router.get("/rate-limit", response_model=RateLimitDetail)
def get_rate_limit_route(rate_limit_id: str, db: Session = Depends(get_db), admin_user = Depends(verified_admin)):
    rate_limit_obj = get_rate_limit(db, rate_limit_id)
    return _build_rate_limit_payload(db, rate_limit_obj)


@llm_router.put("/rate-limit", response_model=RateLimitMutationResponse)
def update_rate_limit_route(
    rate_limit_id: str,
    payload: UpdateRateLimitRequest,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    existing = get_rate_limit(db, rate_limit_id)
    next_target_type = payload.target_type.value if payload.target_type is not None else getattr(existing, "target_type", RATE_LIMIT_TARGET_TYPE_MODEL)
    next_model_ids = payload.model_ids if payload.model_ids is not None else list(existing.model_ids or [])
    next_tool_keys = payload.tool_keys if payload.tool_keys is not None else list(getattr(existing, "tool_keys", None) or [])
    next_user_ids = payload.user_ids if payload.user_ids is not None else list(existing.user_ids or [])
    next_group_ids = payload.group_ids if payload.group_ids is not None else list(existing.group_ids or [])
    target_is_active = payload.is_active if payload.is_active is not None else bool(existing.is_active)

    conflicts = []
    if target_is_active:
        conflicts = check_rate_limit_conflicts(
            db,
            target_type=next_target_type,
            model_ids=next_model_ids,
            tool_keys=next_tool_keys,
            user_ids=next_user_ids,
            group_ids=next_group_ids,
            exclude_rate_limit_id=rate_limit_id,
        )
        if conflicts and not force:
            return {"updated": None, "conflicts": conflicts}

    updates = payload.model_dump(exclude_unset=True)
    if "target_type" in updates and payload.target_type is not None:
        updates["target_type"] = payload.target_type.value
    if "period" in updates and payload.period is not None:
        updates["period"] = payload.period.value
    if "quota_unit" in updates and payload.quota_unit is not None:
        updates["quota_unit"] = payload.quota_unit.value
    rate_limit_obj = update_rate_limit(db, rate_limit_id, **updates)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_RATE_LIMIT",
        details={
            "rate_limit_id": rate_limit_id,
            "updated_fields": sorted(list(updates.keys())),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_rate_limit",
    )

    return {"updated": _build_rate_limit_payload(db, rate_limit_obj), "conflicts": []}


@llm_router.delete("/rate-limit")
def delete_rate_limit_route(
    rate_limit_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    result = delete_rate_limit(db, rate_limit_id)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DELETE_RATE_LIMIT",
        details={"rate_limit_id": rate_limit_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_rate_limit",
    )

    return result
