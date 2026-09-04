"""Ollama title-generation operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.ollama import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "ollama_title_generation": (
        "HTTPException",
        "RemoteProtocolError",
        "ResponseError",
        "_merge_ollama_simple_settings",
        "_ollama_options_from_settings",
        "_resolve_ollama_think_value",
        "create_llm_generation_statistic",
        "datetime",
        "get_model_capabilities",
        "get_ollama_client",
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
    "HTTPException",
    "RemoteProtocolError",
    "ResponseError",
    "_merge_ollama_simple_settings",
    "_ollama_options_from_settings",
    "_resolve_ollama_think_value",
    "create_llm_generation_statistic",
    "datetime",
    "get_model_capabilities",
    "get_ollama_client",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_ollama_title_generation(
    db,
    model_name: str,
    prompt: str,
    system_instruction: str,
    ollama_provider_id: str | None = None,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
    user_id: str | None = None,
    model_settings: dict | None = None,
    settings_override: dict | None = None,
    generation_category: str = "title_generation",
    output_char_limit: int | None = None,
    max_output_tokens: int | None = None,
    response_schema: dict | None = None,
    raise_on_error: bool = True,
):
    """Ollama title generation."""
    start_time = datetime.now(timezone.utc)
    meta: dict = {}
    meta_success = False
    meta_error = False
    meta_error_status_code = 0
    meta_error_message = ""
    meta_error_type = ""

    provider_identifier = ollama_provider_id or byok_base_url or "byok"

    def _record_stat():
        meta.setdefault(
            "generation_time",
            round((datetime.now(timezone.utc) - start_time).total_seconds(), 2),
        )
        create_llm_generation_statistic(
            db,
            model_name=model_name,
            model_id=model_name,
            provider="ollama",
            provider_id=provider_identifier,
            success=meta_success,
            error=meta_error,
            error_status_code=meta_error_status_code,
            error_message=meta_error_message,
            error_type=meta_error_type,
            category=generation_category,
            meta=meta,
            user_id=user_id,
            is_byok=bool(byok_base_url or byok_api_key),
        )

    try:
        thinking = None
        if ollama_provider_id:
            client = get_ollama_client(db, ollama_provider_id)
            caps = get_model_capabilities(db, model_name, ollama_provider_id)
        elif byok_base_url:
            client = get_ollama_client(
                db, byok_base_url=byok_base_url, byok_api_key=byok_api_key
            )
            caps = get_model_capabilities(
                db, model_name, byok_base_url=byok_base_url, byok_api_key=byok_api_key
            )
        else:
            raise HTTPException(status_code=422, detail="Ollama base_url is required")
        thinking = _resolve_ollama_think_value(model_name, caps, None)
        messages = [
            {
                "role": "system",
                "content": system_instruction,
            },
            {
                "role": "user",
                "content": prompt or "",
            },
        ]
        settings = _merge_ollama_simple_settings(model_settings, settings_override)
        chat_kwargs = {
            "model": model_name,
            "messages": messages,
        }
        options = _ollama_options_from_settings(settings)
        if max_output_tokens is not None:
            options["num_predict"] = max(1, int(max_output_tokens))
        if options:
            chat_kwargs["options"] = options
        if isinstance(response_schema, dict):
            chat_kwargs["format"] = response_schema
        if thinking is not None:
            chat_kwargs["think"] = thinking
        response = client.chat(**chat_kwargs)

        message = getattr(response, "message", None)
        if message is None and isinstance(response, dict):
            message = response.get("message")

        title = None
        if message is not None:
            title = getattr(message, "content", None)
            if title is None and isinstance(message, dict):
                title = message.get("content")

        if not isinstance(title, str) or not title.strip():
            raise HTTPException(
                status_code=400, detail="Failed to generate title. Pls try again later."
            )
        meta_success = True
        title = title.strip()
        if output_char_limit is None:
            return title
        return title[: max(1, int(output_char_limit))]
    except ConnectionError as e:
        meta_error = True
        meta_error_message = str(e)
        raise HTTPException(status_code=400, detail="Ollama is not reachable") from e
    except ResponseError as e:
        meta_error = True
        meta_error_status_code = getattr(e, "status_code", None)
        meta_error_message = getattr(e, "message", str(e))
        meta_error_type = "ResponseError"
        raise HTTPException(status_code=400, detail=f"Failed to generate title: {e}")
    except RemoteProtocolError as e:
        meta_error = True
        meta_error_status_code = None
        meta_error_message = str(e)
        meta_error_type = "RemoteProtocolError"
        raise HTTPException(status_code=400, detail=f"Failed to generate title: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate title: {e}")
    finally:
        _record_stat()
