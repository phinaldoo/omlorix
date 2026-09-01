"""OpenRouter provider discovery, model metadata, and creation operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openrouter import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "create_open_router_provider": (
        "BaseModel",
        "HTTPException",
        "_openrouter_extract_model_slug",
        "create_llm_provider",
        "datetime",
        "get_api_key_info",
        "jsonable_encoder",
        "list_models_openrouter",
        "logger",
        "timezone",
    ),
    "list_models_openrouter": (
        "HTTPError",
        "HTTPException",
        "_format_model_payload",
        "_is_zero_priced_model",
        "build_openrouter_api_url",
        "get_api_key_info",
        "get_openrouter_provider_information",
        "requests",
    ),
    "get_model_providers": (
        "HTTPError",
        "HTTPException",
        "_find_catalog_model_entry",
        "_get_catalog_model_parts",
        "_price_per_million_tokens",
        "build_openrouter_api_url",
        "get_openrouter_provider_information",
        "list_models_openrouter",
        "requests",
    ),
    "get_model_information": (
        "HTTPError",
        "HTTPException",
        "OPENROUTER_NAME_EXTENSIONS",
        "_format_model_payload",
        "build_openrouter_api_url",
        "get_openrouter_provider_information",
        "list_models_openrouter",
        "requests",
    ),
    "get_model_information_endpoint": (
        "HTTPError",
        "HTTPException",
        "OPENROUTER_NAME_EXTENSIONS",
        "_get_catalog_model_parts",
        "build_openrouter_api_url",
        "get_openrouter_provider_information",
        "list_models_openrouter",
        "requests",
    ),
    "get_api_key_info": (
        "HTTPException",
        "build_openrouter_api_url",
        "logger",
        "requests",
    ),
    "create_open_router_model": (
        "BaseModel",
        "HTTPException",
        "Models",
        "ProviderEnum",
        "_has_configured_tools",
        "datetime",
        "determine_model_capabilities",
        "get_model_information",
        "jsonable_encoder",
        "timezone",
    ),
    "get_openrouter_provider_information": (
        "HTTPException",
        "get_llm_provider",
        "get_openrouter_api_base_url",
        "get_openrouter_base_url",
        "resolve_openrouter_attribution",
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
    "BaseModel",
    "HTTPError",
    "HTTPException",
    "Models",
    "OPENROUTER_NAME_EXTENSIONS",
    "ProviderEnum",
    "_find_catalog_model_entry",
    "_format_model_payload",
    "_get_catalog_model_parts",
    "_has_configured_tools",
    "_is_zero_priced_model",
    "_openrouter_extract_model_slug",
    "_price_per_million_tokens",
    "build_openrouter_api_url",
    "create_llm_provider",
    "datetime",
    "determine_model_capabilities",
    "get_api_key_info",
    "get_llm_provider",
    "get_model_information",
    "get_openrouter_api_base_url",
    "get_openrouter_base_url",
    "get_openrouter_provider_information",
    "jsonable_encoder",
    "list_models_openrouter",
    "logger",
    "requests",
    "resolve_openrouter_attribution",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_create_open_router_provider(
    db, name: str, api_key: str, settings, icon: str | None = None
):
    if not isinstance(settings, (BaseModel, dict)):
        raise HTTPException(status_code=400, detail="Invalid settings payload")
    settings_data = jsonable_encoder(settings)
    if not isinstance(settings_data, dict):
        raise HTTPException(status_code=400, detail="Invalid settings payload")

    api_key_info = get_api_key_info(api_key, provider_settings=settings_data)
    if not api_key_info:
        raise HTTPException(status_code=400, detail="Invalid API key")

    if isinstance(api_key_info, dict):
        api_key_data = (
            api_key_info.get("data")
            if isinstance(api_key_info.get("data"), dict)
            else {}
        )
    else:
        api_key_data = {}

    is_free_tier_value = (
        api_key_data.get("is_free_tier") if isinstance(api_key_data, dict) else None
    )
    if is_free_tier_value is None and isinstance(api_key_info, dict):
        is_free_tier_value = api_key_info.get("is_free_tier")

    is_free_tier = bool(is_free_tier_value) if is_free_tier_value is not None else False

    settings_data.update({"is_free_tier": is_free_tier})

    status = {
        "available": "unknown",
        "model_list": [],
        "supports_model_list": True,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        models = list_models_openrouter(
            db, api_key=api_key, provider_settings=settings_data
        )
        identifiers = {
            str(
                _openrouter_extract_model_slug(model)
                or model.get("id")
                or model.get("name")
            ).strip()
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
        logger.exception("[OpenRouter Provider] Failed to seed status during creation")

    return create_llm_provider(
        db, "openrouter", name, api_key, settings_data, status=status, icon=icon
    )


def _impl_list_models_openrouter(
    db,
    openrouter_provider_id: str | None = None,
    api_key: str | None = None,
    parameters: list[str] | None = None,
    provider_settings: dict | None = None,
):
    try:
        api_key_value: str | None = None
        api_key_metadata: dict | None = None
        provider = None
        request_settings = (
            provider_settings if isinstance(provider_settings, dict) else {}
        )
        is_free_tier = False
        if openrouter_provider_id:
            provider_info = get_openrouter_provider_information(
                db, openrouter_provider_id
            )
            provider = provider_info["provider"]
            api_key_value = provider_info["api_key"]
            request_settings = provider_info["settings"]
            is_free_tier = bool(request_settings.get("is_free_tier"))
        else:
            if not isinstance(api_key, str) or not api_key.strip():
                raise HTTPException(
                    status_code=422, detail="OpenRouter api_key not provided"
                )
            api_key_value = api_key.strip()
            api_key_info = get_api_key_info(
                api_key_value, provider_settings=request_settings
            )
            if not api_key_info:
                raise HTTPException(status_code=400, detail="Invalid API key")
            if isinstance(api_key_info, dict):
                data_block = api_key_info.get("data")
                if isinstance(data_block, dict):
                    api_key_metadata = data_block
                else:
                    api_key_metadata = api_key_info

                is_free_tier_value = None
                if api_key_metadata:
                    is_free_tier_value = api_key_metadata.get("is_free_tier")
                if is_free_tier_value is None:
                    is_free_tier_value = api_key_info.get("is_free_tier")
                is_free_tier = (
                    bool(is_free_tier_value)
                    if is_free_tier_value is not None
                    else False
                )
            else:
                is_free_tier = False

        if not isinstance(api_key_value, str) or not api_key_value:
            raise HTTPException(
                status_code=422, detail="OpenRouter api_key not configured"
            )

        response = requests.get(
            build_openrouter_api_url("/models/user", request_settings),
            headers={"Authorization": f"Bearer {api_key_value}"},
        )
        response.raise_for_status()

        payload = response.json() or {}
        items = []

        for item in payload.get("data", []):
            formatted = _format_model_payload(item)
            if not is_free_tier or _is_zero_priced_model(formatted):
                items.append(formatted)

        if parameters:
            requested_parameters = {
                param.strip().lower()
                for param in parameters
                if isinstance(param, str) and param.strip()
            }

            if requested_parameters:
                filtered_items: list[dict] = []

                for item in items:
                    supported = item.get("supported_parameters", [])
                    supported_set = {
                        str(supported_param).strip().lower()
                        for supported_param in supported
                        if isinstance(supported_param, str)
                    }

                    if requested_parameters.issubset(supported_set):
                        filtered_items.append(item)

                items = filtered_items

        return items
    except HTTPError as e:
        meta_error = True
        resp = e.response
        if resp is not None:
            meta_error_status_code = resp.status_code
            meta_error_type = "HTTPError"
            try:
                data = resp.json()
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict) and "message" in err:
                        meta_error_message = err["message"]
                    else:
                        meta_error_message = data.get("message", str(data))
                else:
                    meta_error_message = str(data)
            except ValueError:
                meta_error_message = resp.text
        else:
            meta_error_message = str(e)
            meta_error_type = "HTTPError"


def _impl_get_model_providers(
    db, openrouter_provider_id: str, model_name: str, api_key: str | None = None
):
    """Return OpenRouter endpoints for a canonical ID or a legacy short model name."""
    try:
        author, slug = model_name.split("/", 1)
    except ValueError:
        # Model creation previously stored the catalog's short ``model`` field.
        # Resolve it only when the catalog identifies one unambiguous model.
        catalog = list_models_openrouter(
            db,
            openrouter_provider_id=openrouter_provider_id,
            api_key=api_key,
        )
        model_entry = _find_catalog_model_entry(catalog, model_name)
        author, slug = _get_catalog_model_parts(model_entry, model_name)
    try:
        api_key_value: str | None = None
        provider = None
        request_settings: dict | None = None
        if openrouter_provider_id:
            provider_info = get_openrouter_provider_information(
                db, openrouter_provider_id
            )
            provider = provider_info["provider"]
            api_key_value = provider_info["api_key"]
            request_settings = provider_info["settings"]
        else:
            if not isinstance(api_key, str) or not api_key.strip():
                raise HTTPException(
                    status_code=422, detail="OpenRouter api_key not provided"
                )
            api_key_value = api_key.strip()
        url = build_openrouter_api_url(
            f"/models/{author}/{slug}/endpoints", request_settings
        )
        headers = {"Authorization": "Bearer " + api_key_value}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        model_data = data.get("data")
        endpoints = model_data.get("endpoints")
        endpoint_list = []
        for endpoint in endpoints:
            pricing_block = endpoint.get("pricing") or {}
            endpoint_list.append(
                {
                    "name": endpoint.get("name"),
                    "model_name": endpoint.get("model_name"),
                    "context_length": endpoint.get("context_length"),
                    "pricing_prompt": _price_per_million_tokens(
                        pricing_block.get("prompt")
                    ),
                    "pricing_completion": _price_per_million_tokens(
                        pricing_block.get("completion")
                    ),
                    "pricing_request": pricing_block.get("request"),
                    "pricing_image": pricing_block.get("image"),
                    "pricing_image_token": pricing_block.get("image_token"),
                    "pricing_image_output": pricing_block.get("image_output"),
                    "pricing_audio": pricing_block.get("audio"),
                    "input_audio_cache": pricing_block.get("input_audio_cache"),
                    "pricing_websearch": pricing_block.get("websearch"),
                    "pricing_internal_reasoning": pricing_block.get(
                        "internal_reasoning"
                    ),
                    "pricing_input_cache_read": pricing_block.get("input_cache_read"),
                    "pricing_input_cache_write": pricing_block.get("input_cache_write"),
                    "pricing_discount": pricing_block.get("discount"),
                    "provider_name": endpoint.get("provider_name"),
                    "tags": endpoint.get("tags"),
                    "quantization": endpoint.get("quantization"),
                    "max_completion_tokens": endpoint.get("max_completion_tokens"),
                    "max_prompt_tokens": endpoint.get("max_prompt_tokens"),
                    "supported_parameters": endpoint.get("supported_parameters"),
                    "status": endpoint.get("status"),
                    "uptime_last_30m": endpoint.get("uptime_last_30m"),
                    "supports_implicit_caching": endpoint.get(
                        "supports_implicit_caching"
                    ),
                }
            )
        return endpoint_list
    except HTTPError as e:
        raise HTTPException


def _impl_get_model_information(
    db, model: str, openrouter_provider_id: str
) -> dict | None:
    try:
        if not model or not isinstance(model, str):
            raise ValueError("`model` must be a non-empty string")

        for extension in OPENROUTER_NAME_EXTENSIONS:
            if extension in model:
                # Remove this extension from the model name
                model = model.replace(extension, "")
        provider_info = get_openrouter_provider_information(db, openrouter_provider_id)
        api_key = provider_info["api_key"]
        provider_settings = provider_info["settings"]

        model_entry: dict | None = None
        for item in list_models_openrouter(
            db, openrouter_provider_id=openrouter_provider_id, api_key=api_key
        ):
            if item.get("id") == model or item.get("model") == model:
                model_entry = item
                break

        if not model_entry:
            raise HTTPException(status_code=404, detail="Model not found")

        provider = model_entry.get("provider")
        slug = model_entry.get("model")
        endpoints: list = model_entry.get("endpoints", [])
        merged = dict(model_entry)

        if provider and slug:
            try:
                response = requests.get(
                    build_openrouter_api_url(
                        f"/models/{provider}/{slug}/endpoints", provider_settings
                    ),
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                payload = response.json() or {}
                data = payload.get("data") if isinstance(payload, dict) else {}

                if isinstance(data, dict):
                    endpoints = data.get("endpoints", endpoints)
                    merged.update({k: v for k, v in data.items() if k != "endpoints"})
            except requests.RequestException:
                pass

        formatted = _format_model_payload(merged)
        formatted["endpoints"] = endpoints
        return formatted
    except HTTPError as e:
        meta_error = True
        resp = e.response
        if resp is not None:
            meta_error_status_code = resp.status_code
            meta_error_type = "HTTPError"
            try:
                data = resp.json()
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict) and "message" in err:
                        meta_error_message = err["message"]
                    else:
                        meta_error_message = data.get("message", str(data))
                else:
                    meta_error_message = str(data)
            except ValueError:
                meta_error_message = resp.text
        else:
            meta_error_message = str(e)
            meta_error_type = "HTTPError"


def _impl_get_model_information_endpoint(
    db, model: str, openrouter_provider_id: str, provider_name: str | None
):
    try:
        if not model or not isinstance(model, str):
            raise ValueError("`model` must be a non-empty string")

        for extension in OPENROUTER_NAME_EXTENSIONS:
            if extension in model:
                # Remove this extension from the model name
                model = model.replace(extension, "")
        provider_info = get_openrouter_provider_information(db, openrouter_provider_id)
        api_key = provider_info["api_key"]
        provider_settings = provider_info["settings"]

        # First, get model info from main models list (includes knowledge_cutoff)
        model_entry: dict | None = None
        for item in list_models_openrouter(
            db, openrouter_provider_id=openrouter_provider_id, api_key=api_key
        ):
            if item.get("id") == model or item.get("model") == model:
                model_entry = item
                break

        if not model_entry:
            raise HTTPException(
                status_code=404, detail="Model not found in provider model list"
            )

        # Use the matched catalog row as the source of truth.  The incoming value
        # may be a legacy short slug even though the catalog has a canonical ID.
        author, slug = _get_catalog_model_parts(model_entry, model)
        url = build_openrouter_api_url(
            f"/models/{author}/{slug}/endpoints", provider_settings
        )
        headers = {"Authorization": "Bearer " + api_key}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        model_data = data.get("data") or {}
        endpoints = model_data.get("endpoints") or []
        architecture_data = model_data.get("architecture") or {}

        # Get knowledge_cutoff from model_entry (main models list), not from /endpoints
        knowledge_cutoff = model_entry.get("knowledge_cutoff")

        result = {
            "name": model_data.get("name") or model_entry.get("name"),
            "description": model_data.get("description")
            or model_entry.get("description"),
            "slug": slug,
            "author": author,
            "architecture": architecture_data or model_entry.get("architecture", {}),
            "knowledge_cutoff": knowledge_cutoff,
        }

        normalized_provider = (
            provider_name.strip().lower() if isinstance(provider_name, str) else None
        )
        selected_endpoint = None
        for endpoint in endpoints:
            provider_label = endpoint.get("provider_name")
            if (
                normalized_provider
                and isinstance(provider_label, str)
                and provider_label.strip().lower() == normalized_provider
            ):
                selected_endpoint = endpoint
                break
        if not selected_endpoint and endpoints:
            selected_endpoint = endpoints[0]
        if not selected_endpoint:
            raise HTTPException(
                status_code=404, detail="OpenRouter Model Provider not found"
            )
        result["endpoint"] = selected_endpoint
        return result
    except HTTPError as e:
        meta_error = True
        resp = e.response
        if resp is not None:
            meta_error_status_code = resp.status_code
            meta_error_type = "HTTPError"
            try:
                data = resp.json()
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict) and "message" in err:
                        meta_error_message = err["message"]
                    else:
                        meta_error_message = data.get("message", str(data))
                else:
                    meta_error_message = str(data)
            except ValueError:
                meta_error_message = resp.text
        else:
            meta_error_message = str(e)
            meta_error_type = "HTTPError"


def _impl_get_api_key_info(
    api_key: str, provider_settings: dict | None = None
) -> dict | None:
    try:
        response = requests.get(
            build_openrouter_api_url("/key", provider_settings),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"data": payload}
    except requests.HTTPError as exc:
        resp = exc.response
        message = None
        if resp is not None:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    error_block = data.get("error") or data
                    if isinstance(error_block, dict):
                        message = error_block.get("message") or error_block.get("code")
                    elif isinstance(error_block, str):
                        message = error_block
                else:
                    message = str(data)
            except ValueError:
                message = resp.text or str(exc)
        logger.error("[OpenRouter] Invalid API key response: %s", message or str(exc))
        raise HTTPException(
            status_code=400, detail=message or "Invalid OpenRouter API key"
        ) from exc
    except requests.RequestException as exc:
        logger.error("[OpenRouter] Failed to reach key endpoint: %s", exc)
        raise HTTPException(
            status_code=424,
            detail="Failed to validate OpenRouter API key: upstream unreachable",
        ) from exc


def _impl_create_open_router_model(
    db,
    openrouter_provider_id,
    name: str,
    description: str,
    model_icon: str,
    model: str,
    settings,
    tools,
    access,
    status,
    save_model: bool | None = True,
    group_provider_id: str | None = None,
):
    if ":online" in model:
        raise HTTPException(
            status_code=400,
            detail="Using the extension ':online' is not supported. Please use OpenRouter's websearch functionality or add the websearch tool to the model. But cant use both at the same time.",
        )
    # Get the model info, at the same time it checks if the model exists
    model_info = get_model_information(db, model, openrouter_provider_id)
    architecture = model_info.get("architecture", {})
    top_provider = model_info.get("top_provider", {})
    input_formats = architecture.get("input_modalities", [])
    output_formats = architecture.get("output_modalities", [])
    input_token_limit = top_provider.get("context_length", 0)
    output_token_limit = top_provider.get("max_completion_tokens", 0)
    supported_parameters = model_info.get("supported_parameters", [])
    provider = model_info.get("provider", "")

    if isinstance(settings, BaseModel):
        settings_data = settings.model_dump()
    elif isinstance(settings, dict):
        settings_data = dict(settings)
    else:
        raise HTTPException(status_code=400, detail="Invalid settings payload")

    # Append all this to the settings
    settings_data.update(
        {
            "input_formats": input_formats,
            "output_formats": output_formats,
            "input_token_limit": input_token_limit,
            "output_token_limit": output_token_limit,
            "supported_parameters": supported_parameters,
            "provider": provider,
        }
    )
    settings_data = jsonable_encoder(settings_data)

    tools_configured = _has_configured_tools(tools)
    if not tools_configured:
        supported_parameters_value = settings_data.get("supported_parameters")
        if isinstance(supported_parameters_value, list):
            filtered_supported_parameters = [
                item
                for item in supported_parameters_value
                if not (isinstance(item, str) and item.strip().lower() == "tools")
            ]
            settings_data["supported_parameters"] = filtered_supported_parameters
        elif isinstance(supported_parameters_value, (set, tuple)):
            filtered_supported_parameters = [
                item
                for item in supported_parameters_value
                if not (isinstance(item, str) and item.strip().lower() == "tools")
            ]
            settings_data["supported_parameters"] = filtered_supported_parameters

    capabilities = determine_model_capabilities(
        ProviderEnum.openrouter,
        settings_data,
        tools or [],
        model_name=model,
    )

    if isinstance(access, (BaseModel, dict)):
        access_data = jsonable_encoder(access)
    else:
        access_data = access
    access_data = jsonable_encoder(access_data)

    tools_data = jsonable_encoder(tools) if tools is not None else tools
    tools_data = jsonable_encoder(tools_data)
    if save_model:
        model = Models(
            name=name,
            description=description,
            model_icon=model_icon,
            provider="openrouter",
            provider_id=openrouter_provider_id
            if not group_provider_id
            else group_provider_id,
            model_name=model,
            settings=settings_data,
            capabilities=capabilities,
            tools=tools_data,
            access=access_data,
            status=status,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(model)
        db.commit()
        db.refresh(model)
        return model
    else:
        return True


def _impl_get_openrouter_provider_information(
    db, provider_id: str, require_api_key: bool = True
):
    provider = get_llm_provider(db, provider_id)
    if not provider or getattr(provider, "provider", None) != "openrouter":
        raise HTTPException(status_code=404, detail="OpenRouter provider not found")

    api_key = getattr(provider, "api_key", None)
    if require_api_key:
        if not isinstance(api_key, str) or not api_key.strip():
            raise HTTPException(
                status_code=422, detail="OpenRouter provider api_key not configured"
            )
        api_key_value = api_key.strip()
    else:
        api_key_value = (
            api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        )

    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    ranking_url, ranking_title = resolve_openrouter_attribution(provider_settings)
    base_url = get_openrouter_base_url(provider_settings)
    api_base_url = get_openrouter_api_base_url(provider_settings)
    return {
        "provider": provider,
        "api_key": api_key_value,
        "settings": provider_settings,
        "ranking_url": ranking_url,
        "ranking_title": ranking_title,
        "base_url": base_url,
        "api_base_url": api_base_url,
    }
