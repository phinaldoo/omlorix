"""OpenAI title-generation operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import utils as _compat_source
from app.llm.provider_request import release_db_session_before_provider_io

_COMPAT_DEPENDENCIES = {
    "openai_title_generation": (
        "APIConnectionError",
        "AuthenticationError",
        "BadRequestError",
        "HTTPException",
        "OpenAI",
        "OpenAIModelSettings",
        "_apply_openai_simple_generation_settings",
        "_apply_provider_reported_cost_meta",
        "_merge_openai_request_options",
        "_parse_openai_exception",
        "_record_openai_stat_with_costs",
        "_resolve_openai_client_context",
        "datetime",
        "merge_settings",
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
    "APIConnectionError",
    "AuthenticationError",
    "BadRequestError",
    "HTTPException",
    "OpenAI",
    "OpenAIModelSettings",
    "_apply_openai_simple_generation_settings",
    "_apply_provider_reported_cost_meta",
    "_merge_openai_request_options",
    "_parse_openai_exception",
    "_record_openai_stat_with_costs",
    "_resolve_openai_client_context",
    "datetime",
    "merge_settings",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_openai_title_generation(
    db,
    model: str,
    prompt: str,
    system_instruction: str,
    openai_provider_id: str | None = None,
    byok: dict | None = None,
    openai_provider_type: str | None = "openai",
    user_id: str | None = None,
    model_settings: dict | None = None,
    settings_override: dict | None = None,
):
    """OpenAI title generation."""
    start_time = datetime.now(timezone.utc)
    meta: dict = {}
    meta_success = False
    meta_error = False
    meta_error_status_code = 0
    meta_error_message = ""
    meta_error_type = ""

    def _provider_id():
        if openai_provider_id:
            return openai_provider_id
        if isinstance(byok, dict):
            candidate = byok.get("provider_id") or byok.get("provider")
            if candidate:
                return candidate
        return "unknown"

    def _record_stat():
        meta.setdefault(
            "generation_time",
            round((datetime.now(timezone.utc) - start_time).total_seconds(), 2),
        )
        _record_openai_stat_with_costs(
            db,
            category="title_generation",
            meta=meta,
            success=meta_success,
            error=meta_error,
            error_status_code=meta_error_status_code,
            error_message=meta_error_message,
            error_type=meta_error_type,
            model_name=model,
            provider=openai_provider_type,
            provider_id=_provider_id(),
            service_tier=meta.get("service_tier") or "standard",
            native_websearch_tool_calls_count=0,
            user_id=user_id,
            is_byok=bool(byok),
        )

    try:
        client_context = _resolve_openai_client_context(
            db,
            openai_provider_id,
            byok,
            openai_provider_type=openai_provider_type,
        )
        client_kwargs = client_context["client_kwargs"]
        request_options = client_context["request_options"]
        client = OpenAI(**client_kwargs)

        settings, _ = merge_settings(
            model_settings,
            settings_override,
            getattr(OpenAIModelSettings, "model_fields", None),
        )

        prompt_input = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    }
                ],
            }
        ]

        request_kwargs = {
            "model": model,
            "input": prompt_input,
            "instructions": system_instruction,
        }
        _apply_openai_simple_generation_settings(
            request_kwargs,
            settings,
            user_id=user_id,
            openai_provider_type=openai_provider_type,
        )
        release_db_session_before_provider_io(db)
        response = client.responses.create(
            **_merge_openai_request_options(request_kwargs, request_options)
        )
        meta["service_tier"] = getattr(response, "service_tier", None) or "standard"
        usage = response.usage
        if usage:
            _apply_provider_reported_cost_meta(
                meta,
                usage,
                provider_type=openai_provider_type,
            )
            meta["input_tokens"] = int(getattr(usage, "input_tokens", 0) or 0)
            input_tokens_details = getattr(usage, "input_tokens_details", None)
            if input_tokens_details:
                meta["input_token_cached"] = getattr(
                    input_tokens_details, "cached_tokens", 0
                )
                meta["cache_write_tokens"] = (
                    getattr(input_tokens_details, "cache_write_tokens", 0) or 0
                )
            meta["output_tokens"] = int(getattr(usage, "output_tokens", 0) or 0)
            output_details = getattr(usage, "output_tokens_details", None)
            if output_details:
                meta["reasoning_tokens"] = int(
                    getattr(output_details, "reasoning_tokens", 0) or 0
                )
            meta["total_tokens"] = int(getattr(usage, "total_tokens", 0) or 0)

        title = response.output_text
        if not title:
            raise HTTPException(
                status_code=400, detail="Failed to generate title. Pls try again later."
            )

        meta_success = True
        return title[:60]
    except (AuthenticationError, BadRequestError, APIConnectionError) as exc:
        status, message, error_type, _ = _parse_openai_exception(exc)
        meta_error = True
        meta_error_status_code = status or 400
        meta_error_type = error_type or exc.__class__.__name__
        meta_error_message = message
        raise HTTPException(
            status_code=status or 400, detail=f"Failed to generate title: {message}"
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        meta_error = True
        meta_error_type = exc.__class__.__name__
        meta_error_message = detail
        meta_error_status_code = exc.status_code
        raise
    except Exception as exc:
        meta_error = True
        meta_error_type = exc.__class__.__name__
        meta_error_message = str(exc)
        meta_error_status_code = getattr(exc, "status_code", 0) or 400
        raise HTTPException(status_code=400, detail=f"Failed to generate title: {exc}")
    finally:
        _record_stat()
