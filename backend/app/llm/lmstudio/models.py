"""LM Studio provider and model lifecycle operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.lmstudio import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "create_lmstudio_provider": (
        "HTTPException",
        "create_llm_provider",
        "datetime",
        "list_models_lmstudio",
        "logger",
        "normalize_lmstudio_base_url",
        "timezone",
    ),
    "list_models_all": (
        "HTTPException",
        "_assert_lmstudio_url_allowed",
        "_coerce_lmstudio_model_entry",
        "_get_lmstudio_credentials",
        "_lmstudio_json_object",
        "_lmstudio_request",
    ),
    "list_models_lmstudio": ("list_models_all",),
    "get_model_info": ("HTTPException", "list_models_lmstudio"),
    "list_models_loaded": ("list_models_all",),
    "_plain_json_value": (),
    "lmstudio_create_model": (
        "HTTPException",
        "Models",
        "_plain_json_value",
        "datetime",
        "get_model_info",
        "jsonable_encoder",
        "lmstudio_capabilities_to_list",
        "timezone",
    ),
    "_build_load_payload": (),
    "load_model": (
        "HTTPException",
        "LMSTUDIO_MODEL_LOAD_TIMEOUT",
        "_assert_lmstudio_url_allowed",
        "_build_load_payload",
        "_get_lmstudio_credentials",
        "_lmstudio_json_object",
        "_lmstudio_request",
    ),
    "unload_model": (
        "HTTPException",
        "LMSTUDIO_MODEL_ACTION_TIMEOUT",
        "_assert_lmstudio_url_allowed",
        "_get_lmstudio_credentials",
        "_lmstudio_request",
        "list_models_loaded",
    ),
    "_normalize_download_progress": (),
    "download_model": (
        "HTTPException",
        "LMSTUDIO_DOWNLOAD_POLL_INTERVAL_SECONDS",
        "LMSTUDIO_DOWNLOAD_POLL_TIMEOUT_SECONDS",
        "LMSTUDIO_MODEL_ACTION_TIMEOUT",
        "_assert_lmstudio_url_allowed",
        "_get_lmstudio_credentials",
        "_lmstudio_error_message",
        "_lmstudio_json_object",
        "_lmstudio_request",
        "_normalize_download_progress",
        "json",
        "time",
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
    "HTTPException",
    "LMSTUDIO_DOWNLOAD_POLL_INTERVAL_SECONDS",
    "LMSTUDIO_DOWNLOAD_POLL_TIMEOUT_SECONDS",
    "LMSTUDIO_MODEL_ACTION_TIMEOUT",
    "LMSTUDIO_MODEL_LOAD_TIMEOUT",
    "Models",
    "_assert_lmstudio_url_allowed",
    "_build_load_payload",
    "_coerce_lmstudio_model_entry",
    "_get_lmstudio_credentials",
    "_lmstudio_error_message",
    "_lmstudio_json_object",
    "_lmstudio_request",
    "_normalize_download_progress",
    "_plain_json_value",
    "create_llm_provider",
    "datetime",
    "get_model_info",
    "json",
    "jsonable_encoder",
    "list_models_all",
    "list_models_lmstudio",
    "list_models_loaded",
    "lmstudio_capabilities_to_list",
    "logger",
    "normalize_lmstudio_base_url",
    "time",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_create_lmstudio_provider(
    db, name: str, api_key: str, settings: dict, icon: str | None = None
):
    """Create an LM Studio provider."""
    normalized_settings = dict(settings or {})
    normalized_settings["base_url"] = normalize_lmstudio_base_url(
        normalized_settings.get("base_url")
    )
    status = {
        "available": "unknown",
        "model_list": [],
        "supports_model_list": True,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        models = list_models_lmstudio(
            db,
            byok_base_url=normalized_settings.get("base_url"),
            byok_api_key=api_key,
        )
        identifiers = sorted(
            {
                str(item.get("id") or item.get("model") or "").strip()
                for item in models
                if isinstance(item, dict)
            }
        )
        status["available"] = "up"
        status["model_list"] = [identifier for identifier in identifiers if identifier]
    except HTTPException:
        status["available"] = "down"
    except Exception:
        logger.exception("[LM Studio Provider] Failed to seed status during creation")
    return create_llm_provider(
        db, "lmstudio", name, api_key, normalized_settings, status=status, icon=icon
    )


def _impl_list_models_all(
    db,
    lmstudio_provider_id: str | None = None,
    *,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
) -> list[dict[str, Any]]:
    """List all native LM Studio models."""
    base_url, api_key = _get_lmstudio_credentials(
        db,
        lmstudio_provider_id,
        byok_base_url=byok_base_url,
        byok_api_key=byok_api_key,
    )
    _assert_lmstudio_url_allowed(
        db, base_url=base_url, feature="LM Studio model listing"
    )
    response = _lmstudio_request("GET", f"{base_url}/api/v1/models", api_key=api_key)
    payload = _lmstudio_json_object(response, operation="listing models")
    # Native v1 uses "models". Accept the OpenAI-style "data" key as a
    # compatibility fallback for early preview builds.
    models = payload.get("models")
    if models is None:
        models = payload.get("data")
    if not isinstance(models, list):
        raise HTTPException(
            status_code=424, detail="LM Studio did not return a valid model list"
        )
    return [
        _coerce_lmstudio_model_entry(entry)
        for entry in models
        if isinstance(entry, dict)
    ]


def _impl_list_models_lmstudio(
    db,
    lmstudio_provider_id: str | None = None,
    *,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
) -> list[dict[str, Any]]:
    """List LM Studio LLM models available for chat/completions."""
    return [
        item
        for item in list_models_all(
            db,
            lmstudio_provider_id,
            byok_base_url=byok_base_url,
            byok_api_key=byok_api_key,
        )
        if str(item.get("type") or "").strip().lower() == "llm"
    ]


def _impl_get_model_info(
    db,
    model: str,
    lmstudio_provider_id: str | None = None,
    *,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
) -> dict[str, Any]:
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise HTTPException(status_code=422, detail="model is required")
    for entry in list_models_lmstudio(
        db,
        lmstudio_provider_id,
        byok_base_url=byok_base_url,
        byok_api_key=byok_api_key,
    ):
        if str(entry.get("key") or entry.get("id") or "").strip() == normalized_model:
            return entry
    raise HTTPException(status_code=404, detail="Model not found")


def _impl_list_models_loaded(lmstudio_provider_id: str, db) -> list[dict[str, Any]]:
    """List all loaded LM Studio instances, including embedding models."""
    loaded_rows: list[dict[str, Any]] = []
    for item in list_models_all(db, lmstudio_provider_id):
        for instance in item.get("loaded_instances") or []:
            if not isinstance(instance, dict):
                continue
            # Native v1 calls this object "config". Keep the old key as a
            # compatibility fallback for pre-release server builds.
            config = instance.get("config")
            if not isinstance(config, dict):
                config = instance.get("load_config")
            if not isinstance(config, dict):
                config = {}
            loaded_rows.append(
                {
                    "instance_id": instance.get("id"),
                    "model": item.get("key"),
                    "name": item.get("name"),
                    "publisher": item.get("publisher"),
                    "type": item.get("type"),
                    "architecture": item.get("architecture"),
                    "quantization": item.get("quantization"),
                    "size_bytes": item.get("size_bytes"),
                    "params_string": item.get("params_string"),
                    "max_context_length": item.get("max_context_length"),
                    "capabilities": item.get("capabilities") or {},
                    "context_length": config.get("context_length"),
                    "eval_batch_size": config.get("eval_batch_size"),
                    "parallel": config.get("parallel"),
                    "flash_attention": config.get("flash_attention"),
                    "num_experts": config.get("num_experts"),
                    "offload_kv_cache_to_gpu": config.get("offload_kv_cache_to_gpu"),
                    "raw": instance,
                }
            )
    return loaded_rows


def _impl__plain_json_value(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        return obj.dict()
    return obj


def _impl_lmstudio_create_model(
    lmstudio_provider_id: str,
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
) -> dict[str, Any] | None:
    """Create a database model entry backed by LM Studio."""
    model_info = get_model_info(db, model, lmstudio_provider_id)
    if str(model_info.get("type") or "").strip().lower() != "llm":
        raise HTTPException(
            status_code=400, detail=f"Model {model} does not support chat completion"
        )

    capabilities = lmstudio_capabilities_to_list(model_info)
    plain_settings = _plain_json_value(settings) or {}
    tools_to_store = tools if "tools" in capabilities else None
    plain_tools = _plain_json_value(tools_to_store)
    plain_access = _plain_json_value(access)

    if not save_model:
        return None

    model_db = Models(
        name=name,
        description=description,
        model_icon=model_icon,
        provider="lmstudio",
        provider_id=group_provider_id or lmstudio_provider_id,
        model_name=model,
        settings=plain_settings,
        capabilities=capabilities,
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


def _impl__build_load_payload(
    model: str, load_config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build only fields documented by POST /api/v1/models/load."""
    payload = {
        "model": model,
        # Echoing the resolved configuration lets callers verify the settings
        # that LM Studio actually applied.
        "echo_load_config": True,
    }
    if isinstance(load_config, dict):
        for key in (
            "context_length",
            "eval_batch_size",
            "flash_attention",
            "num_experts",
            "offload_kv_cache_to_gpu",
        ):
            value = load_config.get(key)
            if value not in (None, "", []):
                payload[key] = value
    return payload


def _impl_load_model(
    lmstudio_provider_id: str,
    model: str,
    db,
    *,
    load_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise HTTPException(status_code=422, detail="model is required")
    base_url, api_key = _get_lmstudio_credentials(db, lmstudio_provider_id)
    _assert_lmstudio_url_allowed(
        db, base_url=base_url, feature="LM Studio model loading"
    )
    response = _lmstudio_request(
        "POST",
        f"{base_url}/api/v1/models/load",
        api_key=api_key,
        payload=_build_load_payload(normalized_model, load_config),
        timeout=LMSTUDIO_MODEL_LOAD_TIMEOUT,
    )
    return _lmstudio_json_object(response, operation="loading a model")


def _impl_unload_model(
    lmstudio_provider_id: str, identifier: str, db
) -> dict[str, Any]:
    normalized = str(identifier or "").strip()
    if not normalized:
        raise HTTPException(
            status_code=422, detail="instance_id or model key is required"
        )

    base_url, api_key = _get_lmstudio_credentials(db, lmstudio_provider_id)
    loaded = list_models_loaded(lmstudio_provider_id, db)
    matches = [
        row for row in loaded if str(row.get("instance_id") or "").strip() == normalized
    ]
    if not matches:
        matches = [
            row for row in loaded if str(row.get("model") or "").strip() == normalized
        ]
    if not matches:
        raise HTTPException(status_code=404, detail="Loaded model instance not found")

    _assert_lmstudio_url_allowed(
        db, base_url=base_url, feature="LM Studio model unloading"
    )
    unloaded: list[str] = []
    failures: list[dict[str, str]] = []
    for match in matches:
        instance_id = str(match.get("instance_id") or "").strip()
        if not instance_id:
            continue
        try:
            _lmstudio_request(
                "POST",
                f"{base_url}/api/v1/models/unload",
                api_key=api_key,
                payload={"instance_id": instance_id},
                timeout=LMSTUDIO_MODEL_ACTION_TIMEOUT,
            )
            unloaded.append(instance_id)
        except HTTPException as exc:
            failures.append(
                {
                    "instance_id": instance_id,
                    "message": str(exc.detail or "LM Studio unload failed").strip(),
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "instance_id": instance_id,
                    "message": str(exc or "LM Studio unload failed").strip(),
                }
            )

    if failures:
        failed_ids = (
            ", ".join(
                item["instance_id"] for item in failures if item.get("instance_id")
            )
            or "unknown"
        )
        raise HTTPException(
            status_code=424,
            detail={
                "message": f"Failed to unload one or more LM Studio instances: {failed_ids}",
                "model": matches[0].get("model"),
                "unloaded_instances": unloaded,
                "failures": failures,
            },
        )

    if not unloaded:
        raise HTTPException(
            status_code=424, detail="LM Studio did not return any unloadable instances"
        )

    return {
        "status": "success",
        "model": matches[0].get("model"),
        "unloaded_instances": unloaded,
        "count": len(unloaded),
    }


def _impl__normalize_download_progress(
    payload: dict[str, Any], *, job_id: str, model: str
) -> dict[str, Any]:
    """Normalize current native v1 download status and older preview shapes."""
    overall = payload.get("overall_progress")
    step_counter = payload.get("step_counter")
    status = str(
        payload.get("status") or payload.get("message") or "Downloading..."
    ).strip()
    downloaded_bytes = payload.get("downloaded_bytes")
    total_size_bytes = payload.get("total_size_bytes")
    completed = (
        float(downloaded_bytes) if isinstance(downloaded_bytes, (int, float)) else None
    )
    total = (
        float(total_size_bytes) if isinstance(total_size_bytes, (int, float)) else None
    )
    percent = None
    if completed is None and isinstance(overall, dict):
        legacy_downloaded_bytes = overall.get("downloaded_bytes")
        if isinstance(legacy_downloaded_bytes, (int, float)):
            completed = float(legacy_downloaded_bytes)
    if total is None and isinstance(overall, dict):
        legacy_total_bytes = overall.get("total_size_bytes", overall.get("total_bytes"))
        if isinstance(legacy_total_bytes, (int, float)):
            total = float(legacy_total_bytes)
    if (
        isinstance(completed, (int, float))
        and isinstance(total, (int, float))
        and total > 0
    ):
        percent = (completed / total) * 100.0
    if percent is None and isinstance(step_counter, dict):
        current = step_counter.get("current")
        total_steps = step_counter.get("total")
        if (
            isinstance(current, (int, float))
            and isinstance(total_steps, (int, float))
            and total_steps > 0
        ):
            percent = (float(current) / float(total_steps)) * 100.0
    if status.lower() in {"completed", "already_downloaded"} and percent is None:
        percent = 100.0
    return {
        "job_id": job_id,
        "model": model,
        "status": status,
        "completed": completed,
        "total": total,
        "percent": percent,
        "step_counter": step_counter if isinstance(step_counter, dict) else None,
        "bytes_per_second": payload.get("bytes_per_second"),
        "estimated_completion": payload.get("estimated_completion"),
        "started_at": payload.get("started_at"),
        "completed_at": payload.get("completed_at"),
        "raw": payload,
    }


def _impl_download_model(
    lmstudio_provider_id: str,
    model: str,
    db,
    *,
    quantization: str | None = None,
):
    """Start an LM Studio download and stream status as NDJSON."""
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise HTTPException(status_code=422, detail="model is required")

    base_url, api_key = _get_lmstudio_credentials(db, lmstudio_provider_id)
    _assert_lmstudio_url_allowed(
        db, base_url=base_url, feature="LM Studio model download"
    )
    payload: dict[str, Any] = {"model": normalized_model}
    normalized_quantization = str(quantization or "").strip()
    if normalized_quantization:
        payload["quantization"] = normalized_quantization

    response = _lmstudio_request(
        "POST",
        f"{base_url}/api/v1/models/download",
        api_key=api_key,
        payload=payload,
        timeout=LMSTUDIO_MODEL_ACTION_TIMEOUT,
    )
    body = _lmstudio_json_object(response, operation="starting a model download")
    initial_status = str(body.get("status") or "").strip().lower()

    # The documented already_downloaded response intentionally has no job_id.
    # Report it as a completed operation so the UI refreshes its model list and
    # the audit wrapper records the successful outcome.
    if initial_status == "already_downloaded":
        yield (
            json.dumps(
                {
                    **_normalize_download_progress(
                        body, job_id="", model=normalized_model
                    ),
                    "status": "completed",
                    "already_downloaded": True,
                }
            )
            + "\n"
        )
        return

    if initial_status in {"failed", "error", "cancelled"}:
        yield (
            json.dumps(
                {
                    **_normalize_download_progress(
                        body, job_id="", model=normalized_model
                    ),
                    "status": "error",
                    "message": _lmstudio_error_message(
                        body, "LM Studio download failed"
                    ),
                }
            )
            + "\n"
        )
        return

    job_id = str(body.get("job_id") or body.get("id") or "").strip()
    if not job_id:
        raise HTTPException(
            status_code=424, detail="LM Studio did not return a download job id"
        )

    initial_payload = _normalize_download_progress(
        body, job_id=job_id, model=normalized_model
    )
    if initial_payload.get("percent") is None:
        initial_payload["percent"] = 0
    yield json.dumps(initial_payload) + "\n"
    if initial_status == "completed":
        return

    terminal_statuses = {"completed", "error", "failed", "cancelled"}
    seen_payloads: set[str] = set()
    deadline = time.monotonic() + LMSTUDIO_DOWNLOAD_POLL_TIMEOUT_SECONDS

    while True:
        if time.monotonic() >= deadline:
            error_payload = {
                **_normalize_download_progress(
                    {
                        "status": "error",
                        "message": "LM Studio download polling timed out",
                    },
                    job_id=job_id,
                    model=normalized_model,
                ),
                "status": "error",
                "message": "LM Studio download polling timed out",
            }
            error_encoded = json.dumps(error_payload)
            if error_encoded not in seen_payloads:
                seen_payloads.add(error_encoded)
                yield error_encoded + "\n"
            return

        try:
            status_response = _lmstudio_request(
                "GET",
                f"{base_url}/api/v1/models/download/status/{job_id}",
                api_key=api_key,
            )
            status_body = _lmstudio_json_object(
                status_response, operation="checking download status"
            )
        except HTTPException as exc:
            yield (
                json.dumps(
                    {
                        "job_id": job_id,
                        "model": normalized_model,
                        "status": "error",
                        "message": str(
                            exc.detail or "LM Studio download status request failed"
                        ),
                    }
                )
                + "\n"
            )
            return
        progress_payload = _normalize_download_progress(
            status_body, job_id=job_id, model=normalized_model
        )
        status_value = str(status_body.get("status") or "").strip().lower()
        if status_value in terminal_statuses:
            if status_value != "completed":
                message = _lmstudio_error_message(
                    status_body, "LM Studio download failed"
                )
                error_payload = {
                    **progress_payload,
                    "status": "error",
                    "message": message,
                }
                error_encoded = json.dumps(error_payload)
                if error_encoded not in seen_payloads:
                    seen_payloads.add(error_encoded)
                    yield error_encoded + "\n"
                return
        encoded = json.dumps(progress_payload)
        if encoded not in seen_payloads:
            seen_payloads.add(encoded)
            yield encoded + "\n"
        if status_value == "completed":
            break

        time.sleep(LMSTUDIO_DOWNLOAD_POLL_INTERVAL_SECONDS)
