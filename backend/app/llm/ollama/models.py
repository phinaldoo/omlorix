"""Ollama provider, client, model discovery, and lifecycle operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.ollama import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "_resolve_ollama_provider": ("HTTPException", "LLMProvider"),
    "_extract_provider_base_url": ("HTTPException",),
    "create_ollama_provider": (
        "HTTPException",
        "create_llm_provider",
        "datetime",
        "list_models_all",
        "logger",
        "timezone",
    ),
    "get_ollama_provider_url": (
        "_extract_provider_base_url",
        "_resolve_ollama_provider",
    ),
    "get_ollama_client": (
        "Client",
        "HTTPException",
        "_extract_provider_base_url",
        "_resolve_ollama_provider",
    ),
    "get_model_capabilities": (
        "HTTPException",
        "RemoteProtocolError",
        "ResponseError",
        "get_ollama_client",
    ),
    "list_models_ollama": ("HTTPException", "get_model_info", "list_models_all"),
    "list_models_all": ("HTTPException", "get_ollama_client", "logger", "requests"),
    "list_models_loaded": ("HTTPException", "get_ollama_client", "logger", "requests"),
    "ollama_create_model": (
        "HTTPException",
        "Models",
        "datetime",
        "get_model_capabilities",
        "jsonable_encoder",
        "list_models_ollama",
        "timezone",
    ),
    "get_model_info": (
        "HTTPException",
        "RemoteProtocolError",
        "ResponseError",
        "get_ollama_client",
    ),
    "_progress_to_jsonable": (),
    "download_model": (
        "HTTPException",
        "RemoteProtocolError",
        "ResponseError",
        "_progress_to_jsonable",
        "get_ollama_client",
        "json",
    ),
    "delete_model": (
        "HTTPException",
        "RemoteProtocolError",
        "ResponseError",
        "get_ollama_client",
    ),
    "load_model": (
        "HTTPException",
        "RemoteProtocolError",
        "ResponseError",
        "get_ollama_client",
    ),
    "unload_model": (
        "HTTPException",
        "RemoteProtocolError",
        "ResponseError",
        "get_ollama_client",
    ),
    "check_ollama_version": (
        "HTTPException",
        "LLMProvider",
        "get_ollama_provider_url",
        "requests",
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
    "Client",
    "HTTPException",
    "LLMProvider",
    "Models",
    "RemoteProtocolError",
    "ResponseError",
    "_extract_provider_base_url",
    "_progress_to_jsonable",
    "_resolve_ollama_provider",
    "create_llm_provider",
    "datetime",
    "get_model_capabilities",
    "get_model_info",
    "get_ollama_client",
    "get_ollama_provider_url",
    "json",
    "jsonable_encoder",
    "list_models_all",
    "list_models_ollama",
    "logger",
    "requests",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl__resolve_ollama_provider(
    db,
    provider_id: str,
    *,
    allow_direct_lookup: bool = True,
    resolved_provider: LLMProvider | None = None,
) -> LLMProvider:
    """Resolve a provider id or group id to a concrete Ollama provider."""

    if resolved_provider:
        provider = resolved_provider
    else:
        if not provider_id:
            raise HTTPException(
                status_code=422, detail="Ollama provider_id is required"
            )
        if db is None:
            raise HTTPException(
                status_code=500, detail="Database session required to resolve provider"
            )

        if allow_direct_lookup:
            provider = db.query(LLMProvider).filter_by(id=provider_id).first()
            if not provider:
                from app.llm.provider_groups import resolve_provider_for_request

                provider = resolve_provider_for_request(db, provider_id)
        else:
            from app.llm.provider_groups import resolve_provider_for_request

            provider = resolve_provider_for_request(db, provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail="LLM provider not found")
    if provider.provider != "ollama":
        raise HTTPException(
            status_code=422, detail="Resolved provider is not an Ollama provider"
        )
    return provider


def _impl__extract_provider_base_url(provider: LLMProvider) -> str:
    """Extract provider base URL."""
    settings = getattr(provider, "settings", None) or {}
    if not isinstance(settings, dict):
        raise HTTPException(status_code=422, detail="Invalid provider.settings format")

    base_url = settings.get("base_url")
    if not base_url or not isinstance(base_url, str):
        raise HTTPException(status_code=422, detail="Provider base_url not configured")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422,
            detail="Provider base_url must start with http:// or https://",
        )
    return base_url


def _impl_create_ollama_provider(
    db, name: str, api_key: str, settings, icon: str | None = None
):
    """Create Ollama provider."""
    status = {
        "available": "unknown",
        "model_list": [],
        "supports_model_list": True,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }

    base_url = settings.get("base_url") if isinstance(settings, dict) else None
    if isinstance(base_url, str) and base_url.strip():
        identifiers: set[str] = set()
        try:
            for entry in list_models_all(
                db, byok_base_url=base_url.strip(), byok_api_key=api_key
            ):
                candidate = None
                if isinstance(entry, dict):
                    candidate = (
                        entry.get("id") or entry.get("model") or entry.get("name")
                    )
                elif isinstance(entry, str):
                    candidate = entry
                else:
                    candidate = getattr(entry, "id", None) or getattr(
                        entry, "model", None
                    )
                if isinstance(candidate, str) and candidate.strip():
                    identifiers.add(candidate.strip())

            status["available"] = "up"
            status["model_list"] = sorted(identifiers)
        except HTTPException:
            status["available"] = "down"
        except Exception:
            logger.exception(
                "[Ollama Provider] Failed to seed status from %s", base_url
            )

    return create_llm_provider(
        db, "ollama", name, api_key, settings, status=status, icon=icon
    )


def _impl_get_ollama_provider_url(db, ollama_provider_id: str):
    """Get Ollama provider URL."""
    provider = _resolve_ollama_provider(db, ollama_provider_id)
    return _extract_provider_base_url(provider)


def _impl_get_ollama_client(
    db,
    ollama_provider_id: str | None = None,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
    cloud_blocked: bool = False,
    timeout: httpx.Timeout | float | None = None,
    return_provider: bool = False,
) -> Client | tuple[Client, LLMProvider | None]:
    """Construct an Ollama Client using provider settings, including optional API key."""
    # Uses Authorization header if settings.api_key is present.
    if ollama_provider_id:
        provider = _resolve_ollama_provider(db, ollama_provider_id)
        base_url = _extract_provider_base_url(provider)
        if "ollama.com" in base_url and cloud_blocked:
            raise HTTPException(
                status_code=422, detail="Ollama cloud does not support this feature"
            )
        api_key = provider.api_key
        headers = {"Authorization": api_key} if api_key else None
        client_kwargs = {"host": base_url, "timeout": (3, 5)}
        if headers:
            client_kwargs["headers"] = headers
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        client = Client(**client_kwargs)
        if return_provider:
            return client, provider
        return client
    elif byok_base_url:
        if "ollama.com" in byok_base_url and cloud_blocked:
            raise HTTPException(
                status_code=422, detail="Ollama cloud does not support this feature"
            )
        headers = {"Authorization": byok_api_key} if byok_api_key else None
        client_kwargs = {"host": byok_base_url, "timeout": (3, 5)}
        if headers:
            client_kwargs["headers"] = headers
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        client = Client(**client_kwargs)
        if return_provider:
            return client, None
        return client
    else:
        raise HTTPException(status_code=422, detail="Ollama base_url is required")


def _impl_get_model_capabilities(
    db,
    model: str,
    ollama_provider_id: str | None = None,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
):
    """Get model capabilities."""
    try:
        if ollama_provider_id:
            client = get_ollama_client(db, ollama_provider_id)
        elif byok_base_url:
            client = get_ollama_client(
                db, byok_base_url=byok_base_url, byok_api_key=byok_api_key
            )
        m = client.show(model)
        caps = getattr(m, "capabilities", []) or []
        return caps
    except ConnectionError as e:
        meta_error = True
        meta_error_message = str(e)
        raise HTTPException(
            status_code=400, detail=f"Ollama is not reachable: + {str(e)}"
        )
    except ResponseError as e:
        meta_error = True
        meta_error_status_code = getattr(e, "status_code", None)
        meta_error_message = getattr(e, "message", str(e))
        meta_error_type = "ResponseError"
        raise HTTPException(
            status_code=400, detail=f"Error why getting model information: + {str(e)}"
        )
    except RemoteProtocolError as e:
        meta_error = True
        meta_error_status_code = None
        meta_error_message = str(e)
        meta_error_type = "RemoteProtocolError"
        raise HTTPException(
            status_code=400, detail=f"Error why getting model information: + {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Error why getting model information: + {str(e)}"
        )


def _impl_list_models_ollama(
    db,
    ollama_provider_id: str | None = None,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
):
    """List Ollama models."""
    result = []
    if ollama_provider_id:
        for m in list_models_all(db, ollama_provider_id):
            try:
                model_name = (
                    m.get("id") if isinstance(m, dict) else getattr(m, "id", None)
                )
                if not model_name:
                    continue
                model_info = get_model_info(db, model_name, ollama_provider_id)
                caps = getattr(model_info, "capabilities", []) or []
                if "completion" in caps:
                    result.append(m)
            except HTTPException as he:
                # Skip models that can't be fetched or are missing
                if he.status_code != 200:
                    continue
                raise
            except Exception:
                # Skip any malformed entries silently
                continue
    elif byok_base_url:
        for m in list_models_all(
            db, byok_base_url=byok_base_url, byok_api_key=byok_api_key
        ):
            try:
                model_name = (
                    m.get("id") if isinstance(m, dict) else getattr(m, "id", None)
                )
                if not model_name:
                    continue
                model_info = get_model_info(
                    db,
                    model_name,
                    byok_base_url=byok_base_url,
                    byok_api_key=byok_api_key,
                )
                caps = getattr(model_info, "capabilities", []) or []
                if "completion" in caps:
                    result.append(m)
            except HTTPException as he:
                # Skip models that can't be fetched or are missing
                if he.status_code in (400, 404):
                    continue
                raise
            except Exception:
                # Skip any malformed entries silently
                continue
    return result


def _impl_list_models_all(
    db,
    ollama_provider_id: str | None = None,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
):
    """List all models."""
    client = None
    if ollama_provider_id:
        client = get_ollama_client(db, ollama_provider_id)
    elif byok_base_url:
        client = get_ollama_client(
            db, byok_base_url=byok_base_url, byok_api_key=byok_api_key
        )

    if client is None:
        raise HTTPException(status_code=400, detail="Ollama base_url is required")
    try:
        models = client.list().models
    except (requests.RequestException, ConnectionError) as e:
        raise HTTPException(status_code=400, detail="Ollama is not reachable")
    except Exception as e:
        logger.error(f"Failed to list models: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to list models. This could be due to a connection issue.",
        )
    result = []
    for model in models:
        result.append(
            {
                "id": model.model,
                "modified_at": model.modified_at,
                "digest": model.digest,
                "size": model.size,
                "parent_model": model.details.parent_model,
                "format": model.details.format,
                "family": model.details.family,
                "families": model.details.families,
                "parameter_size": model.details.parameter_size,
                "quantization_level": model.details.quantization_level,
            }
        )
    return result


def _impl_list_models_loaded(ollama_provider_id: str, db):
    """List loaded models."""
    client = get_ollama_client(db, ollama_provider_id, cloud_blocked=True)
    models = []
    try:
        response: ProcessResponse = client.ps()

        for model in response.models:
            models.append(model)

    except (requests.RequestException, ConnectionError):
        logger.error("Ollama is not reachable")
        raise HTTPException(status_code=404, detail="Ollama is not reachable")
    except Exception as e:
        logger.error(f"Failed to list loaded models: {e}")
        raise HTTPException(
            status_code=404, detail=f"Failed to list loaded models: {e}"
        )
    return models


def _impl_ollama_create_model(
    ollama_provider_id: str,
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
    """Ollama create model."""
    try:
        # Check if the provider supports the model
        models = list_models_ollama(db, ollama_provider_id)
        for m in models:
            if m.get("id") == model:
                # Model is supported, create a db entry for it
                caps_list = get_model_capabilities(db, model, ollama_provider_id)

                # Convert pydantic models to plain dicts/lists for JSON columns
                def _to_plain(obj):
                    if obj is None:
                        return None
                    try:
                        # If it's a Pydantic v2 model, dump using JSON mode so dates/decimals/etc. are JSON-safe
                        if hasattr(obj, "model_dump") and callable(
                            getattr(obj, "model_dump")
                        ):
                            return obj.model_dump(mode="json")
                        if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
                            return obj.dict()
                    except Exception:
                        pass
                    return obj

                plain_settings = _to_plain(settings)
                if not isinstance(plain_settings, dict):
                    plain_settings = {}
                configured_input_formats = {
                    str(getattr(item, "value", item)).strip().lower()
                    for item in (plain_settings.get("input_formats") or [])
                    if item is not None
                }
                caps_list = [
                    capability
                    for capability in (caps_list or [])
                    if capability not in {"audio", "video", "documents"}
                ]
                if configured_input_formats & {"pdf", "text_document"}:
                    caps_list.append("documents")
                if "-cloud" or ":cloud" in model:
                    plain_settings["is_ollama_cloud"] = True
                else:
                    plain_settings["is_ollama_cloud"] = False
                tools_to_store = (
                    tools
                    if (isinstance(caps_list, list) and "tools" in caps_list)
                    else None
                )
                plain_tools = _to_plain(tools_to_store)
                plain_access = _to_plain(access)
                if save_model:
                    model_db = Models(
                        name=name,
                        description=description,
                        model_icon=model_icon,
                        provider="ollama",
                        provider_id=ollama_provider_id
                        if not group_provider_id
                        else group_provider_id,
                        model_name=model,
                        settings=plain_settings,
                        capabilities=caps_list,
                        tools=plain_tools,
                        access=plain_access,
                        status=status,
                        is_active=True,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    db.add(model_db)
                    db.commit()
                    db.refresh(model_db)
                    return jsonable_encoder(model_db)
                else:
                    return True
        raise HTTPException(
            status_code=400, detail=f"Model {model} does not support completion"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create model: {e}")


def _impl_get_model_info(
    db,
    model: str,
    ollama_provider_id: str | None = None,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
):
    """Get model info."""
    if ollama_provider_id:
        client = get_ollama_client(db, ollama_provider_id)
    elif byok_base_url:
        client = get_ollama_client(
            db, byok_base_url=byok_base_url, byok_api_key=byok_api_key
        )
    else:
        raise HTTPException(status_code=422, detail="Ollama base_url is required")
    try:
        info = client.show(model)
        return info
    except ConnectionError as e:
        meta_error = True
        meta_error_message = str(e)
        raise HTTPException(
            status_code=400, detail=f"Ollama is not reachable: {str(e)}"
        )
    except ResponseError as e:
        meta_error = True
        meta_error_status_code = getattr(e, "status_code", None)
        meta_error_message = getattr(e, "message", str(e))
        meta_error_type = "ResponseError"
        raise HTTPException(
            status_code=400, detail=f"Error why getting model information: {str(e)}"
        )
    except RemoteProtocolError as e:
        meta_error = True
        meta_error_status_code = None
        meta_error_message = str(e)
        meta_error_type = "RemoteProtocolError"
        raise HTTPException(
            status_code=400, detail=f"Error why getting model information: {str(e)}"
        )
    except Exception as e:
        msg = str(e).lower()
        status = 404 if "not found" in msg or "unknown model" in msg else 400
        detail = (
            "Model not found"
            if status == 404
            else f"Failed to retrieve model info: {e}"
        )
        raise HTTPException(status_code=status, detail=detail)


def _impl__progress_to_jsonable(obj):
    """Coerce Ollama progress objects into a plain dict for JSON."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        return obj.model_dump()
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        return obj.dict()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {"status": str(obj)}


def _impl_download_model(ollama_provider_id: str, model: str, db):
    """Download model."""
    client = get_ollama_client(db, ollama_provider_id, cloud_blocked=True)
    try:
        for progress in client.pull(model, stream=True):
            # progress example: {"status": str, "digest": str, "total": int, "completed": int}
            # Emit as NDJSON so clients (e.g. curl) can consume line-by-line
            data = _progress_to_jsonable(progress)
            yield json.dumps(data) + "\n"
    except ConnectionError as e:
        meta_error = True
        meta_error_message = str(e)
        raise HTTPException(
            status_code=400, detail=f"Ollama is not reachable: {str(e)}"
        )
    except ResponseError as e:
        meta_error = True
        meta_error_status_code = getattr(e, "status_code", None)
        meta_error_message = getattr(e, "message", str(e))
        meta_error_type = "ResponseError"
        raise HTTPException(
            status_code=400, detail=f"Failed to download model: {str(e)}"
        )
    except RemoteProtocolError as e:
        meta_error = True
        meta_error_status_code = None
        meta_error_message = str(e)
        meta_error_type = "RemoteProtocolError"
        raise HTTPException(
            status_code=400, detail=f"Failed to download model: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to download model: {e}")


def _impl_delete_model(ollama_provider_id: str, model: str, db):
    """Delete model."""
    client = get_ollama_client(db, ollama_provider_id, cloud_blocked=True)
    try:
        response = client.delete(model)
        return response
    except ConnectionError as e:
        meta_error = True
        meta_error_message = str(e)
        raise HTTPException(status_code=400, detail=f"Failed to delete model: {e}")
    except ResponseError as e:
        meta_error = True
        meta_error_status_code = getattr(e, "status_code", None)
        meta_error_message = getattr(e, "message", str(e))
        meta_error_type = "ResponseError"
        raise HTTPException(status_code=400, detail=f"Failed to delete model: {e}")
    except RemoteProtocolError as e:
        meta_error = True
        meta_error_status_code = None
        meta_error_message = str(e)
        meta_error_type = "RemoteProtocolError"
        raise HTTPException(status_code=400, detail=f"Failed to delete model: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to delete model: {e}")


def _impl_load_model(ollama_provider_id: str, model: str, db):
    """Load model."""
    client = get_ollama_client(db, ollama_provider_id, cloud_blocked=True)
    try:
        response = client.generate(model)
        return response
    except ConnectionError as e:
        meta_error = True
        meta_error_message = str(e)
        raise HTTPException(status_code=400, detail=f"Failed to load model: {e}")
    except ResponseError as e:
        meta_error = True
        meta_error_status_code = getattr(e, "status_code", None)
        meta_error_message = getattr(e, "message", str(e))
        meta_error_type = "ResponseError"
        raise HTTPException(status_code=400, detail=f"Failed to load model: {e}")
    except RemoteProtocolError as e:
        meta_error = True
        meta_error_status_code = None
        meta_error_message = str(e)
        meta_error_type = "RemoteProtocolError"
        raise HTTPException(status_code=400, detail=f"Failed to load model: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load model: {e}")


def _impl_unload_model(ollama_provider_id: str, model: str, db):
    """Unload model."""
    client = get_ollama_client(db, ollama_provider_id, cloud_blocked=True)
    try:
        response = client.generate(model, keep_alive=0)
        return response
    except ConnectionError as e:
        meta_error = True
        meta_error_message = str(e)
        raise HTTPException(status_code=400, detail=f"Failed to unload model: {e}")
    except ResponseError as e:
        meta_error = True
        meta_error_status_code = getattr(e, "status_code", None)
        meta_error_message = getattr(e, "message", str(e))
        meta_error_type = "ResponseError"
        raise HTTPException(status_code=400, detail=f"Failed to unload model: {e}")
    except RemoteProtocolError as e:
        meta_error = True
        meta_error_status_code = None
        meta_error_message = str(e)
        meta_error_type = "RemoteProtocolError"
        raise HTTPException(status_code=400, detail=f"Failed to unload model: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to unload model: {e}")


def _impl_check_ollama_version(
    db,
    ollama_provider_id: str | None = None,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
):
    """Check Ollama version."""
    if ollama_provider_id:
        base_url = get_ollama_provider_url(db, ollama_provider_id)
    elif byok_base_url:
        base_url = byok_base_url
    # Attach Authorization header if api_key present
    headers = None
    if ollama_provider_id:
        try:
            provider = db.query(LLMProvider).filter_by(id=ollama_provider_id).first()
            settings = getattr(provider, "settings", None) or {}
            if isinstance(settings, dict):
                api_key = settings.get("api_key")
                if api_key:
                    headers = {"Authorization": api_key}
        except Exception:
            headers = None
    elif byok_api_key:
        headers = {"Authorization": byok_api_key}
    try:
        response = requests.get(f"{base_url}/api/version", timeout=10, headers=headers)
        response.raise_for_status()
        return response.json().get("version", "Unknown version")
    except requests.exceptions.HTTPError as e:
        status = (
            e.response.status_code if getattr(e, "response", None) is not None else 424
        )
        raise HTTPException(
            status_code=status, detail=f"Error from Ollama version endpoint: {e}"
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Ollama is not reachable: {e}")
