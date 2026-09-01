"""Anthropic clients, provider persistence, and model discovery/creation."""

from datetime import datetime, timezone
from typing import Any

import anthropic
from anthropic import APIStatusError
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.llm.anthropic.request_settings import (
    _validate_anthropic_thinking_disabled_effort,
)
from app.llm.anthropic.thinking import (
    ANTHROPIC_PROVIDER_TYPE,
    get_anthropic_thinking_capabilities,
    is_anthropic_base_provider_type,
)
from app.llm.models import create_llm_provider, create_model, get_llm_provider
from app.llm.schemas import ProviderEnum, provider_api_key_is_optional
from app.network.policy import (
    OutboundRequestBlockedError,
    assert_llm_config_allowed,
    assert_llm_provider_allowed,
)


def create_anthropic_provider(
    db,
    name: str,
    api_key: str,
    settings,
    icon: str | None = None,
    anthropic_provider_type: str = "anthropic",
):
    """Create an Anthropic provider."""
    status = {
        "available": "unknown",
        "model_list": [],
        "supports_model_list": True,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(settings, BaseModel):
        settings_payload = settings.model_dump()
    else:
        settings_payload = dict(settings or {})

    base_url = settings_payload.get("base_url")
    try:
        models = list_anthropic_models(db, api_key=api_key, base_url=base_url)
        identifiers = {
            str(model.get("id") or model.get("name")).strip()
            for model in models
            if isinstance(model, dict)
        }
        status["available"] = "up"
        status["model_list"] = sorted(
            identifier for identifier in identifiers if identifier
        )
    except Exception:
        if anthropic_provider_type not in ("anthropic_base"):
            status["available"] = "down"
        else:
            status["supports_model_list"] = False
    return create_llm_provider(
        db,
        anthropic_provider_type,
        name,
        api_key,
        settings,
        status=status,
        icon=icon,
    )


def get_anthropic_client(
    db,
    anthropic_provider_id: str | None = None,
    api_key: str | None = None,
    *,
    base_url: str | None = None,
):
    """Get Anthropic client."""
    api_key_value: str | None = (
        str(api_key).strip()
        if isinstance(api_key, str) and str(api_key).strip()
        else None
    )
    normalized_base_url = (
        str(base_url).strip()
        if isinstance(base_url, str) and str(base_url).strip()
        else None
    )

    if anthropic_provider_id:
        provider = get_llm_provider(db, anthropic_provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        if not provider.api_key and not provider_api_key_is_optional(provider.provider):
            raise HTTPException(
                status_code=422, detail="Provider api_key not configured"
            )
        api_key_value = (
            str(provider.api_key).strip()
            if isinstance(provider.api_key, str) and str(provider.api_key).strip()
            else None
        )
        settings = provider.settings if isinstance(provider.settings, dict) else {}
        provider_base_url = str(settings.get("base_url") or "").strip()
        if provider_base_url:
            normalized_base_url = provider_base_url

    if not api_key_value and not normalized_base_url:
        raise HTTPException(status_code=422, detail="Provider api_key not configured")

    client_kwargs: dict[str, Any] = {}
    if api_key_value is not None:
        client_kwargs["api_key"] = api_key_value
    if normalized_base_url:
        client_kwargs["base_url"] = normalized_base_url
    return anthropic.Anthropic(**client_kwargs)


def _assert_anthropic_model_listing_allowed(
    db,
    anthropic_provider_id: str | None = None,
    *,
    base_url: str | None = None,
):
    """Apply outbound network policy before Anthropic model listing."""
    try:
        if anthropic_provider_id:
            provider = get_llm_provider(db, anthropic_provider_id)
            if provider is None:
                raise HTTPException(status_code=404, detail="Provider not found")
            assert_llm_provider_allowed(
                db,
                provider,
                feature="LLM provider model listing",
            )
            return

        normalized_base_url = (
            str(base_url).strip()
            if isinstance(base_url, str) and str(base_url).strip()
            else None
        )
        settings = {"base_url": normalized_base_url} if normalized_base_url else None
        provider_type = (
            ProviderEnum.anthropic_base.value
            if normalized_base_url
            else ProviderEnum.anthropic.value
        )
        assert_llm_config_allowed(
            db,
            provider_type=provider_type,
            settings=settings,
            feature="LLM provider model listing",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc


def _anthropic_model_value(value, key: str, default=None):
    """Read a field from either an SDK model or a compatible-provider dict."""
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _anthropic_capability_supported(value, key: str) -> bool:
    """Return a nested SDK capability's supported flag safely."""
    capability = _anthropic_model_value(value, key)
    return bool(_anthropic_model_value(capability, "supported", False))


def _serialize_anthropic_model(model) -> dict:
    """Normalize rich Anthropic metadata while tolerating the basic API shape."""
    model_id = str(_anthropic_model_value(model, "id", "")).strip()
    display_name = (
        str(_anthropic_model_value(model, "display_name", model_id)).strip() or model_id
    )
    created_at = _anthropic_model_value(model, "created_at")
    result = {"id": model_id, "name": display_name, "display_name": display_name}
    if hasattr(created_at, "timestamp"):
        result["created"] = int(created_at.timestamp())

    for source_key, output_key in (
        ("max_input_tokens", "max_input_tokens"),
        ("max_tokens", "max_tokens"),
    ):
        value = _anthropic_model_value(model, source_key)
        if value is not None:
            result[output_key] = value

    capabilities = _anthropic_model_value(model, "capabilities")
    if capabilities is not None:
        effort = _anthropic_model_value(capabilities, "effort")
        thinking = _anthropic_model_value(capabilities, "thinking")
        thinking_types = _anthropic_model_value(thinking, "types")
        efforts = [
            name
            for name in ("low", "medium", "high", "xhigh", "max")
            if _anthropic_capability_supported(effort, name)
        ]
        result["reasoning"] = {
            "supported": bool(_anthropic_model_value(thinking, "supported", False)),
            "reasoning_efforts_supported": bool(
                _anthropic_model_value(effort, "supported", False)
            ),
            "adaptive": _anthropic_capability_supported(thinking_types, "adaptive"),
            "enabled": _anthropic_capability_supported(thinking_types, "enabled"),
            "efforts": efforts,
        }
        result["capabilities"] = {
            "image_input": _anthropic_capability_supported(capabilities, "image_input"),
            "pdf_input": _anthropic_capability_supported(capabilities, "pdf_input"),
        }
    return result


def _uses_anthropic_base_models_api(
    db,
    provider_id: str | None,
    base_url: str | None,
) -> bool:
    """Avoid the beta query parameter for Anthropic-compatible base URLs."""
    if base_url:
        return True
    provider = get_llm_provider(db, provider_id) if provider_id else None
    settings = (
        provider.settings if provider and isinstance(provider.settings, dict) else {}
    )
    return bool(
        provider
        and (
            is_anthropic_base_provider_type(provider.provider)
            or str(settings.get("base_url") or "").strip()
        )
    )


def list_anthropic_models(
    db,
    anthropic_provider_id: str | None = None,
    api_key: str | None = None,
    *,
    base_url: str | None = None,
):
    """List models, including rich capabilities when the provider returns them."""
    _assert_anthropic_model_listing_allowed(
        db,
        anthropic_provider_id,
        base_url=base_url,
    )
    client = get_anthropic_client(
        db,
        anthropic_provider_id,
        api_key,
        base_url=base_url,
    )
    try:
        if _uses_anthropic_base_models_api(db, anthropic_provider_id, base_url):
            resource = client.models
        else:
            resource = client.beta.models
        # anthropic==0.120.2 returns a SyncPage whose iterator follows every
        # cursor. Accessing ``response.data`` would silently keep only page one.
        response = resource.list(limit=1000)
        return [_serialize_anthropic_model(model) for model in response]
    except APIStatusError as exc:
        error = exc.body.get("error", {}) if isinstance(exc.body, dict) else {}
        message = error.get("message", str(exc))
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"Failed to list models: {message}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to list models") from exc


def create_anthropic_model(
    db,
    model: str,
    name: str,
    description: str,
    model_icon: str,
    settings: dict | BaseModel,
    tools: list,
    access: dict,
    status: str,
    anthropic_provider_id: str | None = None,
    save_model: bool | None = True,
    group_provider_id: str | None = None,
    anthropic_provider_type: str = ANTHROPIC_PROVIDER_TYPE,
) -> None:
    """Create an Anthropic model."""
    settings_dict = (
        settings.model_dump() if isinstance(settings, BaseModel) else (settings or {})
    )
    try:
        models = list_anthropic_models(db, anthropic_provider_id)
    except HTTPException:
        # Discovery is optional for compatible providers and may be briefly
        # unavailable for first-party providers. Creation can still proceed
        # using the administrator-supplied settings.
        models = None
    model_info = (
        next((item for item in models if item.get("id") == model), None)
        if models is not None
        else None
    )
    if models is not None and model_info is None:
        raise HTTPException(
            status_code=422,
            detail=f"Model '{model}' is not available for this provider.",
        )
    thinking_enabled = bool(settings_dict.get("thinking", False))
    thinking_budget = settings_dict.get("thinking_budget")
    reasoning_effort = settings_dict.get("reasoning_effort")
    thinking_adaptive = settings_dict.get("thinking_adaptive")
    thinking_caps = get_anthropic_thinking_capabilities(
        model,
        model_info=model_info,
        allow_compatible_fallback=is_anthropic_base_provider_type(
            anthropic_provider_type
        ),
    )
    _validate_anthropic_thinking_disabled_effort(
        settings_dict.get("thinking"),
        reasoning_effort,
        thinking_caps,
    )
    budget_supported = thinking_caps.get("thinking_budget_support", False)
    effort_supported = thinking_caps.get("reasoning_effort_support", False)
    adaptive_supported = thinking_caps.get("thinking_support_adaptive", False)
    has_effort = reasoning_effort is not None
    has_adaptive = bool(thinking_adaptive)
    if thinking_enabled:
        max_tokens = settings_dict.get("max_tokens")
        if (
            budget_supported
            and thinking_budget is None
            and not (
                (effort_supported and has_effort)
                or (adaptive_supported and has_adaptive)
            )
        ):
            raise HTTPException(
                status_code=422,
                detail="Thinking budget or reasoning effort must be provided when thinking mode is enabled.",
            )
        if thinking_budget is not None:
            if thinking_budget < 1024:
                raise HTTPException(
                    status_code=422,
                    detail="Thinking budget must be at least 1024 tokens when thinking mode is enabled",
                )
            if max_tokens is None:
                raise HTTPException(
                    status_code=422,
                    detail="Max tokens must be provided when thinking mode is enabled",
                )
            if max_tokens < thinking_budget:
                raise HTTPException(
                    status_code=422,
                    detail="Max tokens must be greater than or equal to the thinking budget when thinking mode is enabled",
                )
    # Normalize input_formats to plain strings (handles enums or strings)
    raw_input_formats = settings_dict.get("input_formats", []) or []
    input_formats_set = {(getattr(fmt, "value", fmt)) for fmt in raw_input_formats}
    capabilities = ["completion"]
    if "image" in input_formats_set:
        capabilities.append("vision")
    if "pdf" in input_formats_set:
        capabilities.append("documents")
    if settings_dict.get("thinking", False):
        capabilities.append("thinking")
    if "audio" in input_formats_set:
        capabilities.append("audio")

    tools_enabled = False
    if isinstance(tools, (list, tuple, set)):
        tools_enabled = any(str(item).strip() for item in tools if item is not None)
    if tools_enabled:
        capabilities.append("tools")
    capabilities = list(dict.fromkeys(capabilities)) or ["completion"]
    try:
        # Ensure JSON-serializable payloads for JSON columns.
        if not save_model:
            return True
        return create_model(
            db,
            name,
            description,
            model_icon,
            anthropic_provider_type,
            anthropic_provider_id if not group_provider_id else group_provider_id,
            model,
            jsonable_encoder(settings_dict),
            capabilities,
            jsonable_encoder(tools),
            jsonable_encoder(access),
            status,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to create model") from exc
