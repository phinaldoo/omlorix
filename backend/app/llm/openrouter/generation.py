"""OpenRouter title-generation operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openrouter import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "openrouter_title_generation": (
        "HTTPError",
        "HTTPException",
        "Models",
        "_apply_openrouter_simple_settings",
        "_merge_openrouter_simple_settings",
        "_openrouter_extract_response_text",
        "build_openrouter_api_url",
        "create_llm_generation_statistic",
        "datetime",
        "extract_openrouter_incomplete_reason",
        "extract_openrouter_response_error",
        "extract_openrouter_response_usage",
        "get_openrouter_attribution_headers",
        "get_openrouter_provider_information",
        "logger",
        "normalize_openrouter_usage",
        "openrouter_response_error_http_status",
        "requests",
        "timezone",
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
    "HTTPError",
    "HTTPException",
    "Models",
    "_apply_openrouter_simple_settings",
    "_merge_openrouter_simple_settings",
    "_openrouter_extract_response_text",
    "build_openrouter_api_url",
    "create_llm_generation_statistic",
    "datetime",
    "extract_openrouter_incomplete_reason",
    "extract_openrouter_response_error",
    "extract_openrouter_response_usage",
    "get_openrouter_attribution_headers",
    "get_openrouter_provider_information",
    "logger",
    "normalize_openrouter_usage",
    "openrouter_response_error_http_status",
    "requests",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_openrouter_title_generation(
    db,
    model_or_model_name: Models | str,
    prompt: str,
    system_instruction: str,
    openrouter_provider_id: str | None = None,
    byok: dict | None = None,
    user_id: str | None = None,
    model_settings: dict | None = None,
    settings_override: dict | None = None,
):
    model_name: str | None = None
    start_time = datetime.now(timezone.utc)
    meta: dict = {}
    meta_success = False
    meta_error = False
    meta_error_status_code = 0
    meta_error_message = ""
    meta_error_type = ""

    def _provider_id():
        if openrouter_provider_id:
            return openrouter_provider_id
        if isinstance(byok, dict):
            candidate = byok.get("provider_id") or byok.get("provider")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return "byok"

    def _record_stat():
        if not model_name:
            return
        meta.setdefault(
            "generation_time",
            round((datetime.now(timezone.utc) - start_time).total_seconds(), 2),
        )
        create_llm_generation_statistic(
            db,
            model_name=model_name,
            model_id=model_name,
            provider="openrouter",
            provider_id=_provider_id(),
            success=meta_success,
            error=meta_error,
            error_status_code=meta_error_status_code,
            error_message=meta_error_message,
            error_type=meta_error_type,
            category="title_generation",
            meta=meta,
            user_id=user_id,
            is_byok=bool(byok),
        )

    try:
        if isinstance(model_or_model_name, Models):
            model_name = model_or_model_name.model_name
            if model_settings is None and isinstance(
                getattr(model_or_model_name, "settings", None), dict
            ):
                model_settings = getattr(model_or_model_name, "settings", None)
        elif isinstance(model_or_model_name, str):
            model_name = model_or_model_name
        else:
            raise HTTPException(
                status_code=422, detail="Invalid model reference supplied"
            )

        if not isinstance(model_name, str) or not model_name.strip():
            raise HTTPException(
                status_code=422, detail="Model name is required for title generation"
            )

        model_name = model_name.strip()

        headers = {"Content-Type": "application/json"}
        settings_block: dict | None = None

        if byok:
            if not isinstance(byok, dict):
                raise HTTPException(status_code=422, detail="Invalid BYOK payload")
            api_key = byok.get("api_key")
            if not isinstance(api_key, str) or not api_key.strip():
                raise HTTPException(status_code=422, detail="BYOK api_key not provided")
            headers["Authorization"] = f"Bearer {api_key.strip()}"
            settings_candidate = byok.get("settings")
            settings_block = (
                settings_candidate if isinstance(settings_candidate, dict) else None
            )
        else:
            provider_info = get_openrouter_provider_information(
                db, openrouter_provider_id
            )
            headers["Authorization"] = f"Bearer {provider_info['api_key']}"
            settings_block = provider_info["settings"]

        headers.update(get_openrouter_attribution_headers(settings_block))

        url = build_openrouter_api_url("/responses", settings_block)
        payload = {
            "model": model_name,
            "instructions": system_instruction,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        }
                    ],
                }
            ],
        }
        settings = _merge_openrouter_simple_settings(model_settings, settings_override)
        _apply_openrouter_simple_settings(payload, settings)

        try:
            response = requests.post(url, json=payload, headers=headers)
        except requests.RequestException as exc:
            message = f"request_exception: {exc}"
            meta_error = True
            meta_error_type = exc.__class__.__name__
            meta_error_message = message
            meta_error_status_code = getattr(exc, "status_code", 0)
            _record_stat()
            return None

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            status_code = response.status_code
            if status_code == 429:
                meta_error = True
                meta_error_type = exc.__class__.__name__
                meta_error_message = "rate_limited"
                meta_error_status_code = status_code
                _record_stat()
                return None

            err_message = None
            try:
                error_payload = response.json()
                if isinstance(error_payload, dict):
                    err_message = error_payload.get("error") or error_payload.get(
                        "message"
                    )
                    if isinstance(err_message, dict):
                        err_message = err_message.get("message") or err_message.get(
                            "code"
                        )
            except ValueError:
                err_message = response.text
            detail = "Failed to generate title."
            if err_message:
                detail += f" OpenRouter error: {err_message}"
            meta_error = True
            meta_error_type = exc.__class__.__name__
            meta_error_message = detail
            meta_error_status_code = status_code
            _record_stat()
            raise HTTPException(status_code=status_code, detail=detail) from exc

        data = response.json() if response.content else {}
        usage_block = extract_openrouter_response_usage(data)
        if isinstance(usage_block, dict):
            meta.update(normalize_openrouter_usage(usage_block))
        response_error = extract_openrouter_response_error(data)
        if response_error:
            meta_error = True
            meta_error_type = response_error["error_type"]
            meta_error_message = response_error["message"]
            meta_error_status_code = openrouter_response_error_http_status(
                response_error
            )
            _record_stat()
            return None
        incomplete_reason = extract_openrouter_incomplete_reason(data)
        if incomplete_reason:
            meta_error = True
            meta_error_type = "IncompleteResponse"
            meta_error_message = incomplete_reason
            _record_stat()
            return None
        title = _openrouter_extract_response_text(
            data if isinstance(data, dict) else {}
        )

        if not title:
            meta_error = True
            meta_error_message = "empty_response"
            _record_stat()
            return None

        meta_success = True
        _record_stat()
        return title
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
        logger.error("[OpenRouter] Failed to reach key endpoint: %s", e)
        _record_stat()
        raise HTTPException(
            status_code=424, detail="Failed to request OpenRouter"
        ) from e

    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        if not meta_error:
            meta_error = True
            meta_error_type = exc.__class__.__name__
            meta_error_message = detail
            meta_error_status_code = exc.status_code
        _record_stat()
        raise
    except Exception as e:
        if not meta_error:
            meta_error = True
            meta_error_type = e.__class__.__name__
            meta_error_message = str(e)
            meta_error_status_code = getattr(e, "status_code", 0)
        _record_stat()
        raise HTTPException(status_code=400, detail=f"Failed to generate title: {e}")
    finally:
        if not meta_success and not meta_error:
            _record_stat()
