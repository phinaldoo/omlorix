"""Google AI Studio provider, client, and model operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "create_aistudio_provider": (
        "HTTPException",
        "create_llm_provider",
        "datetime",
        "list_models_google_aistudio",
        "logger",
        "timezone",
    ),
    "get_aistudio_client": ("HTTPException", "genai", "get_llm_provider", "types"),
    "list_models_google_aistudio": (
        "AISTUDIO_MODELS_NOT_SUPPORTED",
        "HTTPException",
        "genai_errors",
        "get_aistudio_client",
        "normalize_aistudio_model_description",
        "update_provider_availability",
    ),
    "get_aistudio_model": (
        "HTTPException",
        "genai_errors",
        "get_aistudio_client",
        "update_provider_availability",
    ),
    "aistudio_create_model": (
        "HTTPException",
        "create_model",
        "genai_errors",
        "is_aistudio_thinking_enforced",
        "jsonable_encoder",
        "list_models_google_aistudio",
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
    "AISTUDIO_MODELS_NOT_SUPPORTED",
    "HTTPException",
    "create_llm_provider",
    "create_model",
    "datetime",
    "genai",
    "genai_errors",
    "get_aistudio_client",
    "get_llm_provider",
    "is_aistudio_thinking_enforced",
    "jsonable_encoder",
    "list_models_google_aistudio",
    "logger",
    "normalize_aistudio_model_description",
    "timezone",
    "types",
    "update_provider_availability",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_create_aistudio_provider(
    db, name: str, api_key: str, settings, icon: str | None = None
):
    status = {
        "available": "unknown",
        "model_list": [],
        "supports_model_list": True,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        models = list_models_google_aistudio(
            db,
            byok={
                "api_key": api_key,
                "api_version": (settings or {}).get("api_version", "v1beta"),
            },
            type="generateContent",
        )
        identifiers = {
            str(model.get("id") or model.get("name")).strip()
            for model in models
            if isinstance(model, dict)
        }
        status["available"] = "up"
        status["model_list"] = sorted(
            identifier for identifier in identifiers if identifier
        )
    except HTTPException:
        status["available"] = "down"
    except Exception:
        logger.exception(
            "[Google AI Studio Provider] Failed to seed status during creation"
        )

    return create_llm_provider(
        db, "google_aistudio", name, api_key, settings, status=status, icon=icon
    )


def _impl_get_aistudio_client(
    db,
    aistudio_provider_id: str | None = None,
    api_key: str | None = None,
    api_version: str | None = "v1beta",
):
    if aistudio_provider_id:
        provider = get_llm_provider(db, aistudio_provider_id)
        if not provider.api_key:
            raise HTTPException(
                status_code=422, detail="Provider api_key not configured"
            )
        provider_settings = (
            provider.settings if isinstance(provider.settings, dict) else {}
        )
        return genai.Client(
            api_key=provider.api_key,
            http_options=types.HttpOptions(
                api_version=provider_settings.get("api_version", "v1beta")
            ),
        )
    elif api_key:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version=api_version),
        )
    else:
        raise HTTPException(status_code=422, detail="Provider api_key not configured")


def _impl_list_models_google_aistudio(
    db,
    aistudio_provider_id: str | None = None,
    byok: dict | None = None,
    type: str | None = None,
):
    if not aistudio_provider_id and not byok:
        raise HTTPException(status_code=422, detail="Provider api_key not configured")
    if aistudio_provider_id:
        client = get_aistudio_client(db, aistudio_provider_id)
    else:
        client = get_aistudio_client(
            db,
            api_key=byok.get("api_key"),
            api_version=byok.get("api_version"),
        )
    try:
        raw_models = list(client.models.list())
    except genai_errors.ClientError as exc:
        meta_error_message = getattr(exc, "message", str(exc))
        if aistudio_provider_id:
            update_provider_availability(db, aistudio_provider_id, "down")
        raise HTTPException(
            status_code=400, detail=f"Failed to get model: {meta_error_message}"
        )
    except Exception as exc:
        if aistudio_provider_id:
            update_provider_availability(db, aistudio_provider_id, "down")
        raise HTTPException(status_code=400, detail=f"Failed to get model: {exc}")
    unsupported_ids = set(AISTUDIO_MODELS_NOT_SUPPORTED)
    models = []
    for item in raw_models:
        if type and type not in item.supported_actions:
            continue
        model_id = item.name.split("/")[-1]
        if model_id in unsupported_ids:
            continue
        models.append(
            {
                "id": model_id,
                "name": item.display_name,
                "description": normalize_aistudio_model_description(item.description),
                "version": item.version,
                "input_token_limit": item.input_token_limit,
                "output_token_limit": item.output_token_limit,
                "supported_actions": item.supported_actions or [],
            }
        )
    return models


def _impl_get_aistudio_model(
    db,
    model_name: str,
    aistudio_provider_id: str | None = None,
    byok: dict | None = None,
):
    if not aistudio_provider_id and not byok:
        raise HTTPException(status_code=422, detail="Provider api_key not configured")
    if aistudio_provider_id:
        client = get_aistudio_client(db, aistudio_provider_id)
    else:
        client = get_aistudio_client(
            db,
            api_key=byok.get("api_key"),
            api_version=byok.get("api_version"),
        )
    try:
        model = client.models.get(model=model_name)
        return model
    except genai_errors.ClientError as exc:
        meta_error_message = getattr(exc, "message", str(exc))
        if aistudio_provider_id:
            update_provider_availability(db, aistudio_provider_id, "down")
        raise HTTPException(
            status_code=400, detail=f"Failed to get model: {meta_error_message}"
        )
    except Exception as exc:
        if aistudio_provider_id:
            update_provider_availability(db, aistudio_provider_id, "down")
        raise HTTPException(status_code=400, detail=f"Failed to get model: {exc}")


def _impl_aistudio_create_model(
    aistudio_provider_id: str,
    model: str,
    name: str,
    description: str,
    model_icon: str,
    settings,
    tools,
    access,
    status: str,
    db,
    save_model: bool | None = True,
    group_provider_id: str | None = None,
):
    try:
        # Check if the provider supports the model
        models = list_models_google_aistudio(
            db, aistudio_provider_id=aistudio_provider_id, type="generateContent"
        )

        # Normalize input_formats to plain strings (handles enums or strings)
        raw_input_formats = getattr(settings, "input_formats", []) or []
        input_formats_set = {(getattr(fmt, "value", fmt)) for fmt in raw_input_formats}

        capabilities = ["completion"]
        if "image" in input_formats_set:
            capabilities.append("vision")
        if "audio" in input_formats_set:
            capabilities.append("audio")
        if "video" in input_formats_set:
            capabilities.append("video")
        if "pdf" in input_formats_set:
            capabilities.append("documents")
        if getattr(settings, "thinking", False):
            if getattr(settings, "thinking_budget", 0) != 0:
                capabilities.append("thinking")
        elif is_aistudio_thinking_enforced(model):
            capabilities.append("thinking")

        tools_enabled = False
        if isinstance(tools, (list, tuple, set)):
            tools_enabled = any(str(item).strip() for item in tools if item is not None)
        elif isinstance(tools, dict):
            tools_enabled = any(bool(value) for value in tools.values())
        else:
            tools_enabled = bool(tools)
        if tools_enabled:
            capabilities.append("tools")

        capabilities = list(dict.fromkeys(capabilities)) or ["completion"]

        for m in models:
            model_id = m.get("id") if isinstance(m, dict) else getattr(m, "id", None)
            if model_id == model:
                # Ensure JSON-serializable payloads for JSON columns
                settings_payload = jsonable_encoder(settings)
                tools_payload = jsonable_encoder(tools)
                access_payload = jsonable_encoder(access)
                if save_model:
                    provider_id_to_store = (
                        aistudio_provider_id
                        if not group_provider_id
                        else group_provider_id
                    )
                    return create_model(
                        db,
                        name,
                        description,
                        model_icon,
                        "google_aistudio",
                        provider_id_to_store,
                        model,
                        settings_payload,
                        capabilities,
                        tools_payload,
                        access_payload,
                        status,
                    )
                else:
                    return True
        raise HTTPException(status_code=400, detail="Model is not supported")
    except genai_errors.ClientError as exc:
        meta_error_message = getattr(exc, "message", str(exc))
        raise HTTPException(
            status_code=400, detail=f"Failed to create model: {meta_error_message}"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create model: {e}")
