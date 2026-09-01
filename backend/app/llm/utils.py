from fastapi import HTTPException
import hashlib
import logging
import json
import requests
from datetime import datetime, timezone
from typing import Any

from app.groups.init import get_user_group_setting_value
from app.llm.anthropic.utils import list_anthropic_models
from app.llm.google_aistudio.utils import list_models_google_aistudio
from app.llm.ollama.utils import list_models_ollama, list_models_all as list_models_all_ollama
from app.llm.openai.utils import list_models_openai
from app.llm.openai.custom_headers import (
    preserve_redacted_custom_headers_in_settings,
    redact_custom_headers_for_display_settings,
)
from app.llm.openrouter.utils import list_models_openrouter
from app.llm.models import (
    apply_disabled_sync_status,
    get_llm_provider,
    get_provider_group,
    get_model,
    list_models,
    normalize_llm_provider_status,
    provider_regular_requests_disabled,
    update_provider_availability,
)
from app.llm.schemas import (
    ProviderEnum,
    PROVIDER_SETTINGS_SCHEMAS,
    TestProviderPayload,
    normalize_provider_value,
    provider_api_key_is_optional,
    resolve_provider_icon,
)
from app.llm.provider_url_suggestions import attach_provider_url_suggestions
from app.network.policy import (
    OutboundRequestBlockedError,
    assert_llm_config_allowed,
    assert_llm_provider_allowed,
)
from app.tools.websearch.models import get_websearch_provider, _get_provider_types
from app.users.models import get_user
from app.users.roles import is_admin_role
from app.utils.utils import coerce_to_dict
from app.utils.schemas import populate_sections_with_values


def _coerce_bool(value) -> bool:
    """Coerce a value to boolean, handling string representations."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value) if value is not None else False



logger = logging.getLogger(__name__)


def anonymize_user_id(user_id: str | None) -> str:
    normalized = str(user_id or "").strip()
    if not normalized:
        return "anonymous"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]

CUSTOM_PROVIDER_TEST_WARNING_PROVIDERS: set[ProviderEnum] = {
    ProviderEnum.anthropic_base,
    ProviderEnum.openai_responses,
    ProviderEnum.openai_chat_completions,
    ProviderEnum.microsoft_azure,
}

CUSTOM_PROVIDER_WARNING_MESSAGES = {
    "list_failed": (
        "We couldn't reach the model list endpoint for this custom provider. "
        "Some self-hosted deployments don't expose it, so the connection might still work."
    ),
    "no_models": (
        "The custom provider returned zero models. It may not support the model list API, "
        "so please verify the credentials manually."
    ),
}


def _custom_provider_warning_response(
    provider: ProviderEnum,
    *,
    reason: str,
    detail: str | None = None,
    exception: str | None = None,
    status_code: int | None = None,
    models: list | None = None,
) -> dict:
    """Generate a warning response for custom provider model list failures."""
    message = CUSTOM_PROVIDER_WARNING_MESSAGES.get(
        reason,
        "We couldn't confirm this custom provider. It may still work even though the model list isn't available.",
    )
    payload = {
        "status": "warning",
        "provider": provider.value,
        "warning_reason": reason,
        "message": message,
    }
    if models is not None:
        payload["models"] = models
        payload["model_count"] = len(models or [])
    if detail:
        payload["detail"] = detail
        payload["error"] = detail
    if status_code is not None:
        payload["status_code"] = status_code
    if exception:
        payload["exception"] = exception
    return payload


# -------------------
# Helpers
# -------------------
def _sort_models_by_name(models: list[dict]) -> list[dict]:
    """
    Ensure a deterministic alphabetical order (case-insensitive) for model payloads.
    Empty names fall back to the model identifier so unnamed entries still remain stable.
    """
    def _sort_key(item: dict):
        name = str(item.get("name") or "").strip()
        identifier = str(
            item.get("model_id")
            or item.get("id")
            or item.get("model_name")
            or ""
        ).strip()
        normalized_name = name.casefold()
        normalized_identifier = identifier.casefold()
        return (
            1 if not normalized_name else 0,
            normalized_name,
            normalized_identifier,
        )

    return sorted(models, key=_sort_key)


def _provider_availability_value(provider) -> str:
    """Normalize the provider availability flag stored in provider.status."""
    return str(normalize_llm_provider_status(provider).get("available") or "unknown")


def _is_provider_available_to_user(db, provider_id: str | None) -> bool:
    """
    Return whether a provider-backed model should be usable for non-admin users.

    Regular providers are hidden when their status is explicitly ``down``.
    Provider-group models stay visible as long as at least one member provider
    is not down, which matches the group's failover behavior.
    """
    if not provider_id:
        return True

    from app.llm.provider_groups import get_group_member_providers, is_provider_group

    try:
        if is_provider_group(db, provider_id):
            providers = get_group_member_providers(db, provider_id)
            if not providers:
                return False
            return any(_provider_availability_value(provider) != "down" for provider in providers)

        provider = get_llm_provider(db, provider_id)
    except HTTPException:
        return False

    return _provider_availability_value(provider) != "down"


# -------------------
# List user models
# -------------------
MODEL_SELECT_OUTPUT_TOOL_FORMATS = {
    "image_generation": "image",
    "video_generation": "video",
    "audio_generation": "audio",
}


def _is_model_select_mcp_tool(tool_name: str) -> bool:
    """Return whether a tool name represents an MCP integration.

    The standalone ``mcp`` marker enables configured servers, while individual
    MCP tools receive stable public names beginning with ``mcp_``. Neither is a
    model capability that should consume space in the model-select preview.
    """
    normalized_name = str(tool_name or "").strip().lower()
    return normalized_name == "mcp" or normalized_name.startswith("mcp_")


def _normalize_model_select_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        iterable = value.keys()
    elif isinstance(value, (list, tuple, set)):
        iterable = value
    else:
        iterable = [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for item in iterable:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _build_model_select_modalities(settings: dict, tools: Any) -> tuple[list[str], list[str], list[str]]:
    input_formats = _normalize_model_select_string_list(settings.get("input_formats")) or ["text"]
    output_formats = _normalize_model_select_string_list(settings.get("output_formats")) or ["text"]
    tool_names = _normalize_model_select_string_list(tools)

    output_seen = set(output_formats)
    for tool_name in tool_names:
        output_format = MODEL_SELECT_OUTPUT_TOOL_FORMATS.get(tool_name)
        if output_format and output_format not in output_seen:
            output_seen.add(output_format)
            output_formats.append(output_format)

    # Only advertise hardcoded built-in tool categories. Raw model tool lists
    # may contain custom Python tool names or generated MCP names, both of which
    # reveal internal integration details without helping the model picker.
    from app.tools.registry import (
        RATE_LIMIT_TOOL_LABEL_I18N_KEYS,
        normalize_rate_limit_tool_key,
    )

    model_select_tools: list[str] = []
    seen_public_tools: set[str] = set()
    for tool_name in tool_names:
        if tool_name in MODEL_SELECT_OUTPUT_TOOL_FORMATS or _is_model_select_mcp_tool(tool_name):
            continue
        public_tool_name = normalize_rate_limit_tool_key(tool_name)
        if (
            public_tool_name not in RATE_LIMIT_TOOL_LABEL_I18N_KEYS
            or public_tool_name in seen_public_tools
        ):
            continue
        seen_public_tools.add(public_tool_name)
        model_select_tools.append(public_tool_name)
    return input_formats, output_formats, model_select_tools


def _build_model_select_connections(
    settings: dict,
    tools: Any,
    group_connection_catalog: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build the safe MCP connection summary shown in the model picker.

    The summary is available only when the model has the MCP bridge tool and is
    the intersection of the model-level provider allow-list and the requesting
    user's group-level connection allow-list.  It deliberately contains only
    public provider keys and product titles; model settings, MCP server IDs,
    credentials, and per-user connection state remain private.
    """
    tool_names = {
        tool_name.lower()
        for tool_name in _normalize_model_select_string_list(tools)
    }
    if "mcp" not in tool_names or not group_connection_catalog:
        return []

    # Import locally because MCP utilities also support LLM runtime paths.  A
    # local import keeps module initialization acyclic while sharing the exact
    # model-selector semantics used during MCP execution.
    from app.mcp.utils import get_model_allowed_connection_providers

    allowed_providers = get_model_allowed_connection_providers(settings)
    return [
        {
            "provider": str(item.get("provider") or "").strip().lower(),
            "title": str(item.get("title") or item.get("provider") or "").strip(),
        }
        for item in group_connection_catalog
        if str(item.get("provider") or "").strip().lower() in allowed_providers
    ]


def _get_group_model_select_connection_catalog(db, user_id: str) -> list[dict[str, str]]:
    """Return managed MCP connection types enabled for the user's group.

    This is resolved once per model-list request so a large model catalog does
    not repeatedly read the same group policy.  ``list_managed_connection_mcp_catalog``
    excludes file-only and incompletely configured connections, matching the
    options offered by the model editor's MCP selector.
    """
    from app.connections.policy import normalize_enabled_connections
    from app.connections.service import list_managed_connection_mcp_catalog

    enabled_providers = set(
        normalize_enabled_connections(
            get_user_group_setting_value(
                user_id,
                "tools_mcp",
                "enabled_connections",
                db,
            )
        )
    )
    if not enabled_providers:
        return []

    return [
        {
            "provider": str(item.get("provider") or "").strip().lower(),
            "title": str(item.get("title") or item.get("provider") or "").strip(),
        }
        for item in list_managed_connection_mcp_catalog(db)
        if str(item.get("provider") or "").strip().lower() in enabled_providers
    ]


def _list_user_models(
    db,
    user_id: str,
    *,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
    include_agents: bool = True,
    include_admin_fields: bool = False,
):
    """Build either user-safe model summaries or administrator model records.

    The default branch is a strict allowlist suitable for the shared chat cache.
    Administrative fields are available only to the dedicated admin wrapper and
    are never mixed into the user response based solely on the caller's role.
    """
    user = get_user(db, user_id)
    group_connection_catalog = _get_group_model_select_connection_catalog(db, user_id)

    provider_info_cache: dict[str, dict[str, Any]] = {}

    def resolve_provider_info(provider_id: str | None) -> dict[str, Any]:
        """
        Look up provider routing details once and cache them for the current request.
        """
        if not provider_id:
            return {}
        if provider_id in provider_info_cache:
            return provider_info_cache[provider_id]
        try:
            provider = get_llm_provider(db, provider_id)
            provider_info_cache[provider_id] = {
                "is_provider_group": False,
                "provider_name": getattr(provider, "name", None),
                "provider_recipients": [],
            }
        except HTTPException:
            try:
                from app.llm.provider_groups import get_group_member_providers

                group = get_provider_group(db, provider_id)
                recipients = []
                for member_provider in get_group_member_providers(db, provider_id):
                    recipients.append(
                        {
                            "provider": normalize_provider_value(getattr(member_provider, "provider", None)),
                        }
                    )
                provider_info_cache[provider_id] = {
                    "is_provider_group": True,
                    "provider_group_id": getattr(group, "id", provider_id),
                    "provider_name": getattr(group, "name", None),
                    "provider_recipients": recipients,
                }
            except HTTPException:
                provider_info_cache[provider_id] = {}
        return provider_info_cache[provider_id]

    def _allowed(m) -> bool:
        meta = m.meta if isinstance(getattr(m, "meta", None), dict) else {}
        if meta.get("user_managed") is True:
            return str(meta.get("owner_user_id") or "") == str(user_id)
        if user and is_admin_role(getattr(user, "role", None)):
            return True
        access = m.access or {}
        if not isinstance(access, dict):
            return False
        if access.get("everyone") is True:
            return True
        users_list = access.get("users", [])
        groups_list = access.get("groups", [])
        if isinstance(users_list, list) and user_id in users_list:
            return True
        if isinstance(groups_list, list) and isinstance(getattr(user, "group_id", None), str) and user.group_id in groups_list:
            return True
        return False

    rows = (
        list_models_ollama(db, byok_base_url=byok_base_url, byok_api_key=byok_api_key)
        if byok_base_url
        else list_models(db)
    )
    filtered_base_models = [
        m
        for m in rows
        if _allowed(m)
        and (
            include_agents
            or not (isinstance(getattr(m, "meta", None), dict) and m.meta.get("user_managed") is True)
        )
        and _is_provider_available_to_user(db, getattr(m, "provider_id", None))
    ]
    agents_enabled = include_agents and bool(get_user_group_setting_value(user_id, "agents", "allow_agents", db))

    models = []
    accessible_base_models: dict[str, Any] = {}
    from app.llmstats.models import (
        get_model_cached_tokens_per_second,
        get_model_cached_tokens_per_second_sample_count,
        get_model_performance_meta,
    )

    for model in filtered_base_models:
        accessible_base_models[str(model.id)] = model
        settings = coerce_to_dict(getattr(model, "settings", None))
        meta = coerce_to_dict(getattr(model, "meta", None))
        performance_meta = get_model_performance_meta(meta)
        tools = model.tools or []
        input_formats, output_formats, model_select_tools = _build_model_select_modalities(settings, tools)
        has_fixed_skill = bool(
            _normalize_model_select_string_list(settings.get("skill_ids"))
            or _normalize_model_select_string_list(settings.get("skill_id"))
        )
        user_settings = dict(settings)
        user_settings.pop("skill_id", None)
        user_settings.pop("skill_ids", None)
        provider_info = resolve_provider_info(getattr(model, "provider_id", None))
        normalized_provider = normalize_provider_value(getattr(model, "provider", None))
        model_payload = {
            "model_id": model.id,
            "name": model.name,
            "description": model.description,
            "model_icon": model.model_icon,
            "provider": normalized_provider,
            "is_provider_group": bool(provider_info.get("is_provider_group", False)),
            "provider_recipients": provider_info.get("provider_recipients", []),
            "capabilities": _normalize_model_select_string_list(model.capabilities),
            "status": model.status,
            "is_last": False,
            "model_select_tools": model_select_tools,
            "model_select_connections": _build_model_select_connections(
                settings,
                tools,
                group_connection_catalog,
            ),
            "input_formats": input_formats,
            "output_formats": output_formats,
            "tokens_per_second": get_model_cached_tokens_per_second(meta),
            "increased_errors": bool(meta.get("increased_errors", False)),
            "has_fixed_skill": has_fixed_skill,
            "model_kind": "base",
        }
        if include_admin_fields:
            model_payload.update(
                {
                    "id": model.id,
                    "provider_type": normalized_provider,
                    "provider_id": model.provider_id,
                    "provider_group_id": provider_info.get("provider_group_id"),
                    "provider_name": provider_info.get("provider_name"),
                    "model_name": model.model_name,
                    "is_active": bool(getattr(model, "is_active", True)),
                    "training_data": settings.get("training_data"),
                    "settings": user_settings,
                    "tools": tools,
                    "access": model.access or {},
                    "tokens_per_second_sample_count": get_model_cached_tokens_per_second_sample_count(meta),
                    "tokens_per_second_sample_limit": performance_meta.get("sample_limit"),
                    "tokens_per_second_max_age_days": performance_meta.get("max_age_days"),
                    "is_custom_agent": False,
                }
            )
        models.append(model_payload)

    if agents_enabled:
        try:
            from app.agents.utils import list_accessible_agents

            agent_payloads = list_accessible_agents(
                db,
                user_id,
                accessible_base_models=accessible_base_models,
            )
            for agent_payload in agent_payloads:
                if not isinstance(agent_payload, dict):
                    continue
                base_model = accessible_base_models.get(str(agent_payload.get("base_model_id") or ""))
                if base_model is None:
                    continue

                base_settings = coerce_to_dict(getattr(base_model, "settings", None))
                base_meta = coerce_to_dict(getattr(base_model, "meta", None))
                base_tools = getattr(base_model, "tools", None) or []
                input_formats, output_formats, model_select_tools = _build_model_select_modalities(
                    base_settings,
                    base_tools,
                )
                normalized_provider = normalize_provider_value(getattr(base_model, "provider", None))
                provider_info = resolve_provider_info(getattr(base_model, "provider_id", None))
                base_model_name = str(
                    getattr(base_model, "name", None)
                    or getattr(base_model, "model_name", None)
                    or ""
                ).strip()
                is_shared = bool(agent_payload.get("is_shared"))
                agent_summary = {
                    "model_id": str(agent_payload.get("model_id") or agent_payload.get("id") or ""),
                    "name": str(agent_payload.get("name") or ""),
                    # Agent instructions are private. The selector receives a
                    # neutral description derived only from public model data.
                    "description": base_model_name[:100] or None,
                    "model_icon": agent_payload.get("model_icon") or getattr(base_model, "model_icon", None),
                    "provider": normalized_provider,
                    "model_kind": "agent",
                    "status": getattr(base_model, "status", "normal"),
                    "is_last": False,
                    "capabilities": _normalize_model_select_string_list(getattr(base_model, "capabilities", None)),
                    "input_formats": input_formats,
                    "output_formats": output_formats,
                    "model_select_tools": model_select_tools,
                    "model_select_connections": _build_model_select_connections(
                        base_settings,
                        base_tools,
                        group_connection_catalog,
                    ),
                    "is_provider_group": bool(provider_info.get("is_provider_group", False)),
                    "provider_recipients": provider_info.get("provider_recipients", []),
                    "tokens_per_second": get_model_cached_tokens_per_second(base_meta),
                    "increased_errors": bool(base_meta.get("increased_errors", False)),
                    "has_fixed_skill": bool(
                        agent_payload.get("skill_id")
                        or _normalize_model_select_string_list(base_settings.get("skill_ids"))
                        or _normalize_model_select_string_list(base_settings.get("skill_id"))
                    ),
                }
                if is_shared:
                    agent_summary["is_shared"] = True
                    owner_name = str(agent_payload.get("owner_name") or "").strip()
                    if owner_name:
                        agent_summary["owner_name"] = owner_name
                models.append(agent_summary)
        except Exception:
            logger.exception("Failed to append accessible agents for user %s", user_id)

    # Normalize last_model: treat None/empty/'-' as invalid and drop if not in accessible models
    raw_last = (user.last_model if user else None)
    raw_last = str(raw_last).strip() if raw_last is not None else None
    last_model = raw_last if raw_last and raw_last != '-' else None
    visible_ids = {
        str(item.get("model_id") or item.get("id"))
        for item in models
        if isinstance(item, dict)
    }
    if last_model and str(last_model) not in visible_ids:
        last_model = None

    for model_payload in models:
        if not isinstance(model_payload, dict):
            continue
        model_payload_id = model_payload.get("model_id") or model_payload.get("id")
        model_payload["is_last"] = bool(last_model and str(model_payload_id) == str(last_model))

    return _sort_models_by_name(models)


def list_user_models(
    db,
    user_id: str,
    *,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
    include_agents: bool = True,
):
    """Return the strict, user-safe model summaries used by chat surfaces."""
    return _list_user_models(
        db,
        user_id,
        byok_base_url=byok_base_url,
        byok_api_key=byok_api_key,
        include_agents=include_agents,
        include_admin_fields=False,
    )


def list_admin_models(db, user_id: str):
    """Return administrator-managed model records to an administrator only."""
    user = get_user(db, user_id)
    if not user or not is_admin_role(getattr(user, "role", None)):
        raise HTTPException(status_code=403, detail="Administrator access required")
    models = _list_user_models(
        db,
        user_id,
        include_agents=False,
        include_admin_fields=True,
    )
    return models

# -------------------
# Ensure user access to model
# -------------------
def ensure_user_access_to_model(user_id: str, model_id: str, db) -> bool:
    """Ensure user has access to a model, raising an exception if not."""
    user = get_user(db, user_id)
    normalized_model_id = str(model_id or "").strip()
    if not normalized_model_id:
        raise HTTPException(status_code=404, detail="You do not have access to this model")

    # User-managed models remain private even from another administrator's
    # normal chat session. Administrative database access is a separate,
    # explicitly audited operational capability.
    try:
        candidate_model = get_model(db, normalized_model_id)
    except HTTPException:
        candidate_model = None
    candidate_meta = candidate_model.meta if candidate_model and isinstance(candidate_model.meta, dict) else {}
    if candidate_meta.get("user_managed") is True and str(candidate_meta.get("owner_user_id") or "") != str(user_id):
        raise HTTPException(status_code=404, detail="You do not have access to this model")

    # Admins have access to shared administrator-managed models.
    if is_admin_role(getattr(user, "role", None)):
        return True

    try:
        db_model = get_model(db, normalized_model_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        try:
            if not bool(get_user_group_setting_value(user_id, "agents", "allow_agents", db)):
                raise HTTPException(status_code=404, detail="You do not have access to this model")
            from app.agents.utils import can_user_access_agent

            if can_user_access_agent(db, user_id, normalized_model_id):
                return True
        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to validate agent access for user %s", user_id)
        raise

    access = db_model.access or {}
    # Normalize for safety
    users_list = access.get("users", []) if isinstance(access, dict) else []
    groups_list = access.get("groups", []) if isinstance(access, dict) else []

    allowed = bool(
        (isinstance(access, dict) and access.get("everyone") is True)
        or (
            isinstance(users_list, list) and user_id in users_list
        )
        or (
            isinstance(groups_list, list) and isinstance(getattr(user, "group_id", None), str)
            and user.group_id in groups_list
        )
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="You do not have access to this model")

    if not _is_provider_available_to_user(db, getattr(db_model, "provider_id", None)):
        raise HTTPException(
            status_code=503,
            detail="This model is currently unavailable because its provider is down",
        )
    return True

# -------------------
# List provider models
# -------------------
def _mark_provider_supports_model_list(db, provider_id: str):
    """Mark that the provider supports model listing."""
    provider = get_llm_provider(db, provider_id)
    status_payload = provider.status if isinstance(provider.status, dict) else {}
    if status_payload.get("supports_model_list", True):
        return provider

    new_status = dict(status_payload)
    new_status["supports_model_list"] = True
    provider.status = new_status

    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _mark_model_listing_success(db, provider_id: str):
    """Mark that model listing was successful."""
    provider = update_provider_availability(db, provider_id, "up")
    if isinstance(provider.status, dict) and provider.status.get("supports_model_list", True):
        return provider
    return _mark_provider_supports_model_list(db, provider_id)


def list_provider_models(db, provider_id: str):
    """List models for a provider."""
    provider = get_llm_provider(db, provider_id)
    try:
        assert_llm_provider_allowed(db, provider, feature="LLM provider model listing")
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc
    provider_name = normalize_provider_value(provider.provider)
    models = []
    match provider_name:
        case "google_aistudio":
            try: 
                models = list_models_google_aistudio(db, aistudio_provider_id=provider_id, type="generateContent")
            except Exception as e:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list models: {str(e)}")
            else:
                _mark_model_listing_success(db, provider.id)
        case "anthropic":
            try:
                models = list_anthropic_models(db, anthropic_provider_id=provider_id)
            except HTTPException:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise
            except Exception as exc:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list Anthropic models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "anthropic_base":
            try:
                models = list_anthropic_models(db, anthropic_provider_id=provider_id)
            except HTTPException:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise
            except Exception as exc:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list Anthropic models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "ollama":
            try:
                models = list_models_ollama(db, provider_id)
            except HTTPException:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise
            except Exception as exc:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list ollama models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "lmstudio":
            try:
                models = list_models_lmstudio(db, provider_id)
            except HTTPException:
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise
            except Exception as exc:
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list LM Studio models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "openai":
            try:
                models = list_models_openai(db, provider_id)
            except HTTPException:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise
            except Exception as exc:               
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list OpenAI models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "openai_responses":
            try:
                models = list_models_openai(db, provider_id, openai_provider_type="openai_responses")
            except HTTPException as exc:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list OpenAI Responses API models: {exc}") from exc
            except Exception as exc:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list OpenAI Responses API models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "xai":
            # xAI exposes an OpenAI-compatible model catalog. Keep it on the
            # Responses adapter so models discovered here use the same request
            # path when administrators add them to Omlorix.
            try:
                models = list_models_openai(
                    db,
                    provider_id,
                    openai_provider_type=ProviderEnum.xai.value,
                )
            except HTTPException as exc:
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(
                    status_code=424,
                    detail=f"Failed to list xAI models: {exc}",
                ) from exc
            except Exception as exc:
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(
                    status_code=424,
                    detail=f"Failed to list xAI models: {exc}",
                ) from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "microsoft_azure":
            try:
                models = list_models_openai(db, provider_id, openai_provider_type="microsoft_azure")
            except HTTPException as exc:
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list Microsoft Azure models: {exc}") from exc
            except Exception as exc:
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list Microsoft Azure models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "openai_chat_completions":
            try:
                models = list_models_openai(db, provider_id, openai_provider_type="openai_chat_completions")
            except HTTPException as exc:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list OpenAI Chat Completions models: {exc}") from exc
            except Exception as exc:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list OpenAI Chat Completions models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "openrouter":
            try:
                models = list_models_openrouter(db, provider_id)
            except HTTPException:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise
            except Exception as exc:
                # Check if the provider supports model list
                provider_status = provider.status
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list OpenRouter models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case "elevenlabs":
            provider = _mark_provider_supports_model_list(db, provider.id)
            provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
            try:
                models = _list_elevenlabs_models(
                    api_key=provider.api_key,
                    base_url=provider_settings.get("base_url"),
                )
            except HTTPException:
                provider_status = provider.status if isinstance(provider.status, dict) else {}
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise
            except Exception as exc:
                provider_status = provider.status if isinstance(provider.status, dict) else {}
                supports_model_list = provider_status.get("supports_model_list", True)
                if supports_model_list:
                    update_provider_availability(db, provider.id, "down")
                raise HTTPException(status_code=424, detail=f"Failed to list ElevenLabs models: {exc}") from exc
            else:
                _mark_model_listing_success(db, provider.id)
        case _:
            raise HTTPException(status_code=422, detail="Unsupported provider")
    return models


def list_provider_status_models(db, provider_id: str):
    """List models for provider status snapshots and background sync."""
    provider = get_llm_provider(db, provider_id)
    provider_name = normalize_provider_value(provider.provider)

    if provider_name != "ollama":
        return list_provider_models(db, provider_id)

    try:
        assert_llm_provider_allowed(db, provider, feature="LLM provider model listing")
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc

    try:
        models = list_models_all_ollama(db, provider_id)
    except HTTPException:
        provider_status = provider.status or {}
        supports_model_list = provider_status.get("supports_model_list", True)
        if supports_model_list:
            update_provider_availability(db, provider.id, "down")
        raise
    except Exception as exc:
        provider_status = provider.status or {}
        supports_model_list = provider_status.get("supports_model_list", True)
        if supports_model_list:
            update_provider_availability(db, provider.id, "down")
        raise HTTPException(status_code=424, detail=f"Failed to list ollama models: {exc}") from exc
    else:
        _mark_model_listing_success(db, provider.id)
        return models


def _list_elevenlabs_models(api_key: str, base_url: str | None = None) -> list[dict]:
    """List available ElevenLabs models."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise HTTPException(status_code=400, detail="Provider api_key is required for 'elevenlabs'.")

    _verify_elevenlabs_account(api_key, base_url=base_url)

    resolved_base_url = (base_url or "https://api.elevenlabs.io").rstrip("/")
    endpoint = f"{resolved_base_url}/v1/models"
    try:
        response = requests.get(
            endpoint,
            headers={"xi-api-key": api_key.strip()},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=424, detail=f"Failed to reach ElevenLabs API: {exc}") from exc

    if not response.ok:
        detail = response.text.strip() or f"HTTP {response.status_code}"
        raise HTTPException(
            status_code=response.status_code if response.status_code >= 400 else 424,
            detail=f"Failed to list ElevenLabs models: {detail}",
        )

    payload = response.json()
    raw_models = payload.get("models", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        return []

    normalized: list[dict] = []
    for entry in raw_models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("model_id") or entry.get("id") or "").strip()
        name = str(entry.get("name") or model_id or "").strip()
        if not model_id and not name:
            continue
        normalized.append(
            {
                "id": model_id or name,
                "model": model_id or name,
                "name": name or model_id,
                "description": str(entry.get("description") or "").strip() or None,
            }
        )
    return normalized


def _verify_elevenlabs_account(api_key: str, base_url: str | None = None) -> None:
    """Verify ElevenLabs account credentials."""
    token = str(api_key or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Provider api_key is required for 'elevenlabs'.")

    try:
        from elevenlabs import ElevenLabs
    except Exception:
        try:
            from elevenlabs.client import ElevenLabs
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"ElevenLabs SDK is unavailable: {exc}") from exc

    try:
        from elevenlabs.core import ApiError
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ElevenLabs SDK is unavailable: {exc}") from exc

    try:
        client_kwargs: dict = {"api_key": token}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = ElevenLabs(**client_kwargs)
        client.user.get()
    except ApiError as exc:
        status_code = getattr(exc, "status_code", None)
        normalized_status = status_code if isinstance(status_code, int) and status_code >= 400 else 424

        body = getattr(exc, "body", None)
        if isinstance(body, (dict, list)):
            detail = json.dumps(body)
        elif body is None:
            detail = str(exc)
        else:
            detail = str(body)
        detail = detail.strip() or str(exc)

        raise HTTPException(
            status_code=normalized_status,
            detail=f"Failed to authenticate ElevenLabs credentials: {detail}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=424, detail=f"Failed to reach ElevenLabs API: {exc}") from exc


def _collect_provider_model_ids(models) -> list[str]:
    """Collect unique model IDs from a list of model entries."""
    identifiers: list[str] = []
    seen: set[str] = set()

    for entry in models or []:
        candidate = None
        if isinstance(entry, dict):
            for key in ("id", "model", "name"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    candidate = value.strip()
                    break
        elif isinstance(entry, str):
            candidate = entry.strip()
        else:
            for attr in ("id", "model", "name"):
                value = getattr(entry, attr, None)
                if isinstance(value, str) and value.strip():
                    candidate = value.strip()
                    break

        if candidate and candidate not in seen:
            seen.add(candidate)
            identifiers.append(candidate)

    return sorted(identifiers)

def refresh_provider_status_snapshot(db, provider_id: str):
    """Re-run provider model listing to update availability + cached model list."""

    provider = get_llm_provider(db, provider_id)
    current_status = normalize_llm_provider_status(provider)
    status_payload = dict(current_status)
    status_payload["last_synced_at"] = datetime.now(timezone.utc).isoformat()

    if provider_regular_requests_disabled(provider):
        apply_disabled_sync_status(db, provider)
        return provider

    try:
        models = list_provider_status_models(db, provider_id)
    except HTTPException as exc:
        detail = exc.detail if hasattr(exc, "detail") else str(exc)
        logger.warning(
            "[LLM Provider] Failed to refresh model list for %s: %s",
            provider_id,
            detail,
        )
        status_payload["available"] = "unknown" if exc.status_code == 403 else "down"
        status_payload["policy_blocked"] = exc.status_code == 403
        # Coerce detail to string for JSON serialization
        if isinstance(detail, (dict, list)):
            import json
            status_payload["last_error"] = json.dumps(detail, ensure_ascii=False)
        else:
            status_payload["last_error"] = str(detail)
        if "model_list" not in status_payload:
            status_payload["model_list"] = current_status.get("model_list", [])
    except Exception:
        logger.exception(
            "[LLM Provider] Unexpected error while refreshing provider %s status",
            provider_id,
        )
        status_payload["available"] = "unknown"
        status_payload["policy_blocked"] = False
        if "model_list" not in status_payload:
            status_payload["model_list"] = current_status.get("model_list", [])
    else:
        status_payload["available"] = "up"
        status_payload["policy_blocked"] = False
        status_payload["last_error"] = ""
        status_payload["model_list"] = _collect_provider_model_ids(models)

    provider.status = status_payload
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


# -------------------
# Test provider connection
# -------------------
def test_llm_provider(db, payload: TestProviderPayload | dict):
    """Test a provider draft, reusing saved secrets only for edit requests."""
    if not isinstance(payload, TestProviderPayload):
        payload = TestProviderPayload.model_validate(payload)

    provider = ProviderEnum(normalize_provider_value(payload.provider))
    base_url = payload.base_url
    api_key = payload.api_key
    provider_settings = payload.settings if isinstance(payload.settings, dict) else {}

    if payload.provider_id:
        # Provider schemas expose masked credential previews to administrators,
        # never plaintext secrets. Resolve the saved row server-side so an edit
        # can be tested without forcing the administrator to re-enter its key.
        saved_provider = get_llm_provider(db, payload.provider_id)
        saved_provider_type = normalize_provider_value(saved_provider.provider)
        if saved_provider_type != provider.value:
            raise HTTPException(
                status_code=400,
                # Return a stable code instead of English prose. The admin
                # frontend maps this code to the active locale.
                detail={"code": "provider_test_saved_provider_type_mismatch"},
            )

        if not api_key:
            saved_api_key = getattr(saved_provider, "api_key", None)
            api_key = saved_api_key.strip() if isinstance(saved_api_key, str) and saved_api_key.strip() else None

        # Custom-header values are redacted in the edit schema as well. Restore
        # only unchanged placeholders; newly entered header values win.
        provider_settings = preserve_redacted_custom_headers_in_settings(
            getattr(saved_provider, "settings", None) or {},
            provider_settings,
        )

    if not provider_api_key_is_optional(provider) and not api_key:
        raise HTTPException(
            status_code=400,
            # Avoid exposing an untranslated backend validation sentence in
            # the notification shown to administrators.
            detail={"code": "provider_test_api_key_required"},
        )

    is_custom_provider = provider in CUSTOM_PROVIDER_TEST_WARNING_PROVIDERS
    models = None

    candidate_settings = dict(provider_settings)
    if base_url:
        candidate_settings["base_url"] = base_url
    try:
        assert_llm_config_allowed(
            db,
            provider_type=provider.value if isinstance(provider, ProviderEnum) else str(provider),
            settings=candidate_settings,
            feature="LLM provider test connection",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc

    try:
        match provider:
            case ProviderEnum.openai:
                creds = {"api_key": api_key}
                if base_url:
                    creds["base_url"] = base_url
                if provider_settings.get("custom_headers") is not None:
                    creds["custom_headers"] = provider_settings.get("custom_headers")
                models = list_models_openai(db, byok=creds)
            case ProviderEnum.openai_responses:
                creds = {"api_key": api_key}
                if base_url:
                    creds["base_url"] = base_url
                if provider_settings.get("custom_headers") is not None:
                    creds["custom_headers"] = provider_settings.get("custom_headers")
                models = list_models_openai(db, byok=creds, openai_provider_type="openai_responses")
            case ProviderEnum.xai:
                creds = {
                    "api_key": api_key,
                    "base_url": (
                        base_url
                        or provider_settings.get("base_url")
                        or "https://api.x.ai/v1"
                    ),
                }
                if provider_settings.get("custom_headers") is not None:
                    creds["custom_headers"] = provider_settings.get("custom_headers")
                models = list_models_openai(
                    db,
                    byok=creds,
                    openai_provider_type=ProviderEnum.xai.value,
                )
            case ProviderEnum.openai_chat_completions:
                creds = {"api_key": api_key}
                if base_url:
                    creds["base_url"] = base_url
                if provider_settings.get("custom_headers") is not None:
                    creds["custom_headers"] = provider_settings.get("custom_headers")
                models = list_models_openai(db, byok=creds, openai_provider_type="openai_chat_completions")
            case ProviderEnum.microsoft_azure:
                creds = {
                    "api_key": api_key,
                    "azure_endpoint": provider_settings.get("azure_endpoint"),
                    "api_version": provider_settings.get("api_version"),
                    "custom_headers": provider_settings.get("custom_headers"),
                }
                models = list_models_openai(db, byok=creds, openai_provider_type="microsoft_azure")
            case ProviderEnum.anthropic:
                models = list_anthropic_models(db, api_key=api_key, base_url=base_url)
            case ProviderEnum.anthropic_base:
                models = list_anthropic_models(db, api_key=api_key, base_url=base_url)
            case ProviderEnum.google_aistudio:
                config = {
                    "api_key": api_key,
                    "api_version": provider_settings.get("api_version", "v1beta"),
                }
                models = list_models_google_aistudio(db, byok=config, type="generateContent")
            case ProviderEnum.openrouter:
                models = list_models_openrouter(
                    db,
                    api_key=api_key,
                    provider_settings=provider_settings,
                )
            case ProviderEnum.ollama:
                models = list_models_ollama(db, byok_base_url=base_url, byok_api_key=api_key)
            case ProviderEnum.lmstudio:
                models = list_models_lmstudio(db, byok_base_url=base_url, byok_api_key=api_key)
            case ProviderEnum.elevenlabs:
                models = _list_elevenlabs_models(api_key=api_key or "", base_url=base_url)
            case _:
                raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider.value}'")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
        if is_custom_provider:
            return _custom_provider_warning_response(
                provider,
                reason="list_failed",
                detail=detail,
                exception=exc.__class__.__name__,
                status_code=exc.status_code,
            )
        return {
            "status": "error",
            "provider": provider.value,
            "error": detail,
            "exception": exc.__class__.__name__,
            "status_code": exc.status_code,
        }
    except Exception as exc:
        logger.exception("[LLM Provider] Failed to test provider %s", provider.value)
        if is_custom_provider:
            return _custom_provider_warning_response(
                provider,
                reason="list_failed",
                detail=str(exc),
                exception=exc.__class__.__name__,
            )
        return {
            "status": "error",
            "provider": provider.value,
            "error": str(exc),
            "exception": exc.__class__.__name__,
        }

    if is_custom_provider and not models:
        return _custom_provider_warning_response(
            provider,
            reason="no_models",
            models=models or [],
        )

    return {
        "status": "success",
        "provider": provider.value,
        "model_count": len(models or []),
        "models": models,
    }

# -------------------
# Get Provider Schema Helper
# -------------------
def get_provider_schema(db, provider: ProviderEnum, provider_id: str | None = None):
    """Return provider schema definition optionally populated with saved values."""
    provider = ProviderEnum(normalize_provider_value(provider))
    schema = PROVIDER_SETTINGS_SCHEMAS.get(provider)
    if schema is None:
        raise ValueError(f"Missing provider schema for '{provider.value}'")
    schema_copy = schema.model_copy(deep=True)
    schema_copy = attach_provider_url_suggestions(schema_copy, provider.value)
    if not provider_id:
        return schema_copy

    provider_row = get_llm_provider(db, provider_id, mask_api_key=True)
    provider_payload = {
        "id": provider_row.id,
        "provider": normalize_provider_value(provider_row.provider),
        "name": provider_row.name,
        "icon": resolve_provider_icon(provider_row.provider, provider_row.icon),
        "api_key": provider_row.api_key,
        "settings": redact_custom_headers_for_display_settings(provider_row.settings or {}),
        "status": normalize_llm_provider_status(provider_row),
    }

    return populate_sections_with_values(schema_copy, provider_payload)

from app.llm.google_aistudio.utils import aistudio_create_model, list_models_google_aistudio
from app.llm.openai.utils import openai_create_model, list_models_openai
from app.llm.openrouter.utils import create_open_router_model, list_models_openrouter
from app.llm.ollama.utils import ollama_create_model, list_models_ollama
from app.llm.lmstudio.utils import lmstudio_create_model, list_models_lmstudio
from app.llm.anthropic.utils import list_anthropic_models, create_anthropic_model



def _validate_websearch_providers(tools: list | None, settings: dict | None, *, db=None) -> None:
    """Validate that websearch providers are set when required.
    
    If web_search tool is enabled and native_websearch is not enabled,
    both websearch_scrape_provider and websearch_search_provider must be set.
    
    Raises HTTPException with 400 status if validation fails.
    """
    if not tools or not isinstance(tools, list):
        return
    
    # Check if web_search tool is enabled
    websearch_tools = {"web_search"}
    has_websearch_tools = any(tool in websearch_tools for tool in tools if isinstance(tool, str))
    
    if not has_websearch_tools:
        return
    
    if not settings:
        settings_dict = {}
    elif hasattr(settings, "model_dump"):
        settings_dict = settings.model_dump()
    elif isinstance(settings, dict):
        settings_dict = settings
    else:
        settings_dict = {key: getattr(settings, key) for key in dir(settings) if not key.startswith("_")}

    settings = settings_dict
    
    # Check if native websearch is enabled
    native_websearch = _coerce_bool(settings.get("native_websearch"))
    
    if native_websearch:
        # Native websearch handles everything, no external providers needed
        return
    
    # Validate that both providers are set
    scrape_provider = settings.get("websearch_scrape_provider")
    search_provider = settings.get("websearch_search_provider")
    
    missing = []
    if not scrape_provider or (isinstance(scrape_provider, str) and not scrape_provider.strip()):
        missing.append("websearch scrape provider")
    if not search_provider or (isinstance(search_provider, str) and not search_provider.strip()):
        missing.append("websearch search provider")
    
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Web search tool requires both websearch providers to be configured. Missing: {', '.join(missing)}. Either select providers or enable native web search."
        )

    if db is None:
        return

    search_provider = get_websearch_provider(db, str(search_provider).strip())
    search_types = set(_get_provider_types(search_provider))
    if "search" not in search_types and "combined" not in search_types:
        raise HTTPException(
            status_code=400,
            detail="Selected websearch search provider does not support searching.",
        )

    scrape_provider = get_websearch_provider(db, str(scrape_provider).strip())
    scrape_types = set(_get_provider_types(scrape_provider))
    if "scrape" not in scrape_types:
        raise HTTPException(
            status_code=400,
            detail="Selected websearch scrape provider does not support direct URL scraping.",
        )


from app.llm.provider_groups import is_provider_group, get_group_member_providers

from app.llm.schemas import CreateProviderModelRequest
def create_provider_model(db, payload: CreateProviderModelRequest):
    """Create a model for a provider."""
    model_payload = payload.model
    payload_provider = ProviderEnum(normalize_provider_value(payload.provider))

    # Validate websearch providers if web_search tool is enabled
    _validate_websearch_providers(model_payload.tools, payload.settings, db=db)

    if is_provider_group(db, payload.provider_id):
        provider_group = get_group_member_providers(db, payload.provider_id)
        # get the count of of providergroups
        provider_count = len(provider_group)
        if provider_count == 0 or provider_count == 1:
            raise HTTPException(status_code=400, detail="Provider group needs at least 2 providers")
        for provider in provider_group:
            if normalize_provider_value(provider.provider) != payload_provider.value:
                raise HTTPException(status_code=400, detail="Provider type mismatch for given provider_id")
            model_save = False
            provider_id = provider.id
            match payload_provider:
                case ProviderEnum.openai:
                    result = openai_create_model(
                        openai_provider_id=provider_id,
                        model=model_payload.model,
                        name=model_payload.name,
                        description=model_payload.description,
                        model_icon=model_payload.model_icon,
                        settings=payload.settings,
                        tools=model_payload.tools,
                        access=payload.access,
                        status=model_payload.status,
                        db=db,
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case ProviderEnum.openai_responses:
                    result = openai_create_model(
                        openai_provider_id=provider_id,
                        model=model_payload.model,
                        name=model_payload.name,
                        description=model_payload.description,
                        model_icon=model_payload.model_icon,
                        settings=payload.settings,
                        tools=model_payload.tools,
                        access=payload.access,
                        status=model_payload.status,
                        db=db,
                        openai_provider_type="openai_responses",
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case ProviderEnum.xai:
                    result = openai_create_model(
                        openai_provider_id=provider_id,
                        model=model_payload.model,
                        name=model_payload.name,
                        description=model_payload.description,
                        model_icon=model_payload.model_icon,
                        settings=payload.settings,
                        tools=model_payload.tools,
                        access=payload.access,
                        status=model_payload.status,
                        db=db,
                        openai_provider_type=ProviderEnum.xai.value,
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case ProviderEnum.openai_chat_completions:
                    result = openai_create_model(
                        openai_provider_id=provider_id,
                        model=model_payload.model,
                        name=model_payload.name,
                        description=model_payload.description,
                        model_icon=model_payload.model_icon,
                        settings=payload.settings,
                        tools=model_payload.tools,
                        access=payload.access,
                        status=model_payload.status,
                        db=db,
                        openai_provider_type="openai_chat_completions",
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case ProviderEnum.microsoft_azure:
                    result = openai_create_model(
                        openai_provider_id=provider_id,
                        model=model_payload.model,
                        name=model_payload.name,
                        description=model_payload.description,
                        model_icon=model_payload.model_icon,
                        settings=payload.settings,
                        tools=model_payload.tools,
                        access=payload.access,
                        status=model_payload.status,
                        db=db,
                        openai_provider_type="microsoft_azure",
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case ProviderEnum.anthropic:
                    result = create_anthropic_model(
                        db=db,
                        anthropic_provider_id=provider_id,
                        model=model_payload.model,
                        name=model_payload.name,
                        description=model_payload.description,
                        model_icon=model_payload.model_icon,
                        settings=payload.settings,
                        tools=model_payload.tools,
                        access=payload.access,
                        status=model_payload.status,
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case ProviderEnum.anthropic_base:
                    result = create_anthropic_model(
                        db=db,
                        anthropic_provider_id=provider_id,
                        model=model_payload.model,
                        name=model_payload.name,
                        description=model_payload.description,
                        model_icon=model_payload.model_icon,
                        settings=payload.settings,
                        tools=model_payload.tools,
                        access=payload.access,
                        status=model_payload.status,
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                        anthropic_provider_type="anthropic_base",
                    )
                case ProviderEnum.google_aistudio:
                    result = aistudio_create_model(
                        provider_id,
                        model_payload.model,
                        model_payload.name,
                        model_payload.description,
                        model_payload.model_icon,
                        payload.settings,
                        model_payload.tools,
                        payload.access,
                        model_payload.status,
                        db,
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case ProviderEnum.openrouter:
                    result = create_open_router_model(
                        db,
                        provider_id,
                        model_payload.name,
                        model_payload.description,
                        model_payload.model_icon,
                        model_payload.model,
                        payload.settings,
                        model_payload.tools,
                        payload.access,
                        model_payload.status,
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case ProviderEnum.ollama:
                    result = ollama_create_model(
                        provider_id,
                        model_payload.model,
                        model_payload.name,
                        model_payload.description,
                        model_payload.model_icon,
                        payload.settings,
                        model_payload.tools,
                        payload.access,
                        model_payload.status,
                        db,
                        save_model=model_save,
                        group_provider_id=payload.provider_id, # TODO: add this to every other provider as well
                    )
                case ProviderEnum.lmstudio:
                    result = lmstudio_create_model(
                        provider_id,
                        model_payload.model,
                        model_payload.name,
                        model_payload.description,
                        model_payload.model_icon,
                        payload.settings,
                        model_payload.tools,
                        payload.access,
                        model_payload.status,
                        db,
                        save_model=model_save,
                        group_provider_id=payload.provider_id,
                    )
                case _:
                    raise HTTPException(status_code=400, detail=f"Unsupported provider '{payload_provider}'")
            provider_count = provider_count - 1
        return {
            "provider": payload_provider,
            "model": result,
        }
            
            
        
    else:
        # Single provider, only one provider has to be checked for the model
        provider = get_llm_provider(db, payload.provider_id)
        if normalize_provider_value(provider.provider) != payload_provider.value:
            raise HTTPException(status_code=400, detail="Provider type mismatch for given provider_id")

        model_payload = payload.model

        match payload_provider:
            case ProviderEnum.openai:
                result = openai_create_model(
                    openai_provider_id=payload.provider_id,
                    model=model_payload.model,
                    name=model_payload.name,
                    description=model_payload.description,
                    model_icon=model_payload.model_icon,
                    settings=payload.settings,
                    tools=model_payload.tools,
                    access=payload.access,
                    status=model_payload.status,
                    db=db,
                )
            case ProviderEnum.openai_responses:
                result = openai_create_model(
                    openai_provider_id=payload.provider_id,
                    model=model_payload.model,
                    name=model_payload.name,
                    description=model_payload.description,
                    model_icon=model_payload.model_icon,
                    settings=payload.settings,
                    tools=model_payload.tools,
                    access=payload.access,
                    status=model_payload.status,
                    db=db,
                    openai_provider_type="openai_responses",
                )
            case ProviderEnum.xai:
                result = openai_create_model(
                    openai_provider_id=payload.provider_id,
                    model=model_payload.model,
                    name=model_payload.name,
                    description=model_payload.description,
                    model_icon=model_payload.model_icon,
                    settings=payload.settings,
                    tools=model_payload.tools,
                    access=payload.access,
                    status=model_payload.status,
                    db=db,
                    openai_provider_type=ProviderEnum.xai.value,
                )
            case ProviderEnum.openai_chat_completions:
                result = openai_create_model(
                    openai_provider_id=payload.provider_id,
                    model=model_payload.model,
                    name=model_payload.name,
                    description=model_payload.description,
                    model_icon=model_payload.model_icon,
                    settings=payload.settings,
                    tools=model_payload.tools,
                    access=payload.access,
                    status=model_payload.status,
                    db=db,
                    openai_provider_type="openai_chat_completions",
                )
            case ProviderEnum.microsoft_azure:
                result = openai_create_model(
                    openai_provider_id=payload.provider_id,
                    model=model_payload.model,
                    name=model_payload.name,
                    description=model_payload.description,
                    model_icon=model_payload.model_icon,
                    settings=payload.settings,
                    tools=model_payload.tools,
                    access=payload.access,
                    status=model_payload.status,
                    db=db,
                    openai_provider_type="microsoft_azure",
                )
            case ProviderEnum.anthropic:
                result = create_anthropic_model(
                    db=db,
                    anthropic_provider_id=payload.provider_id,
                    model=model_payload.model,
                    name=model_payload.name,
                    description=model_payload.description,
                    model_icon=model_payload.model_icon,
                    settings=payload.settings,
                    tools=model_payload.tools,
                    access=payload.access,
                    status=model_payload.status,
                )
            case ProviderEnum.anthropic_base:
                result = create_anthropic_model(
                    db=db,
                    anthropic_provider_id=payload.provider_id,
                    model=model_payload.model,
                    name=model_payload.name,
                    description=model_payload.description,
                    model_icon=model_payload.model_icon,
                    settings=payload.settings,
                    tools=model_payload.tools,
                    access=payload.access,
                    status=model_payload.status,
                    anthropic_provider_type="anthropic_base",
                )
            case ProviderEnum.google_aistudio:
                result = aistudio_create_model(
                    payload.provider_id,
                    model_payload.model,
                    model_payload.name,
                    model_payload.description,
                    model_payload.model_icon,
                    payload.settings,
                    model_payload.tools,
                    payload.access,
                    model_payload.status,
                    db,
                )
            case ProviderEnum.openrouter:
                result = create_open_router_model(
                    db,
                    payload.provider_id,
                    model_payload.name,
                    model_payload.description,
                    model_payload.model_icon,
                    model_payload.model,
                    payload.settings,
                    model_payload.tools,
                    payload.access,
                    model_payload.status,
                )
            case ProviderEnum.ollama:
                result = ollama_create_model(
                    payload.provider_id,
                    model_payload.model,
                    model_payload.name,
                    model_payload.description,
                    model_payload.model_icon,
                    payload.settings,
                    model_payload.tools,
                    payload.access,
                    model_payload.status,
                    db,
                )
            case ProviderEnum.lmstudio:
                result = lmstudio_create_model(
                    payload.provider_id,
                    model_payload.model,
                    model_payload.name,
                    model_payload.description,
                    model_payload.model_icon,
                    payload.settings,
                    model_payload.tools,
                    payload.access,
                    model_payload.status,
                    db,
                )
            case _:
                raise HTTPException(status_code=400, detail=f"Unsupported provider '{payload_provider}'")

        return {
            "provider": payload_provider,
            "model": result,
        }
