"""Google AI Studio title-generation operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

from fastapi import HTTPException

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "google_aistudio_title_generation": (
        "build_aistudio_generate_content_config",
        "calculate_aistudio_token_costs",
        "create_llm_generation_statistic",
        "datetime",
        "genai_errors",
        "get_aistudio_client",
        "normalize_aistudio_usage_metadata",
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
    "build_aistudio_generate_content_config",
    "calculate_aistudio_token_costs",
    "create_llm_generation_statistic",
    "datetime",
    "genai_errors",
    "get_aistudio_client",
    "normalize_aistudio_usage_metadata",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_google_aistudio_title_generation(
    db,
    model: str,
    prompt: str,
    system_instruction: str,
    aistudio_provider_id: str | None = None,
    byok: dict | None = None,
    user_id: str | None = None,
    model_settings: dict | None = None,
    generation_category: str = "title_generation",
    output_char_limit: int | None = None,
    max_output_tokens: int | None = None,
    response_schema: dict | None = None,
    raise_on_error: bool = False,
):
    start_time = datetime.now(timezone.utc)
    meta: dict = {}
    meta_success = False
    meta_error = False
    meta_error_status_code = 0
    meta_error_message = ""
    meta_error_type = ""

    def _record_stat():
        meta.setdefault(
            "generation_time",
            round((datetime.now(timezone.utc) - start_time).total_seconds(), 2),
        )
        cost_data = calculate_aistudio_token_costs(
            model,
            input_tokens_total=meta.get("input_tokens", 0),
            input_text_tokens=meta.get("input_token_text", 0),
            input_image_tokens=meta.get("input_token_image", 0),
            input_audio_tokens=meta.get("input_token_audio", 0),
            input_video_tokens=meta.get("input_token_video", 0),
            cached_input_tokens=meta.get("input_token_cached", 0),
            cached_input_text_tokens=meta.get("input_token_cached_text", 0),
            cached_input_image_tokens=meta.get("input_token_cached_image", 0),
            cached_input_audio_tokens=meta.get("input_token_cached_audio", 0),
            cached_input_video_tokens=meta.get("input_token_cached_video", 0),
            output_tokens=meta.get("output_tokens", 0),
            reasoning_tokens=meta.get("reasoning_tokens", 0),
        )
        if cost_data:
            meta.setdefault("input_tokens_cost", cost_data.get("input_tokens_cost", 0))
            meta.setdefault(
                "cached_input_tokens_cost",
                cost_data.get("cached_input_tokens_cost", 0),
            )
            meta.setdefault(
                "output_tokens_cost", cost_data.get("output_tokens_cost", 0)
            )
            meta.setdefault("total_costs", cost_data.get("total_costs", 0))
        create_llm_generation_statistic(
            db,
            model_name=model,
            model_id=model,
            provider="google_aistudio",
            provider_id=aistudio_provider_id or "byok",
            success=meta_success,
            error=meta_error,
            error_status_code=meta_error_status_code,
            error_message=meta_error_message,
            error_type=meta_error_type,
            category=generation_category,
            meta=meta,
            user_id=user_id,
            is_byok=bool(byok),
        )

    try:
        if byok:
            client = get_aistudio_client(
                db,
                api_key=byok.get("api_key"),
                api_version=byok.get("api_version"),
            )
        else:
            client = get_aistudio_client(db, aistudio_provider_id)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=build_aistudio_generate_content_config(
                model_settings,
                system_instruction=system_instruction,
                max_output_tokens=max_output_tokens,
                response_mime_type=(
                    "application/json" if isinstance(response_schema, dict) else None
                ),
                response_json_schema=(
                    response_schema if isinstance(response_schema, dict) else None
                ),
            ),
        )
        if hasattr(response, "usage_metadata"):
            meta.update(normalize_aistudio_usage_metadata(response.usage_metadata))
        title = response.text
        if not title:
            if raise_on_error:
                raise RuntimeError("empty_model_output")
            title = prompt[:60]
        meta_success = True
        if output_char_limit is None:
            return title
        return title[: max(1, int(output_char_limit))]
    except genai_errors.ClientError as exc:
        meta_error = True
        meta_error_status_code = getattr(exc, "code", 0)
        meta_error_message = getattr(exc, "message", str(exc))
        meta_error_type = getattr(exc, "status", str(exc))
        if raise_on_error:
            raise HTTPException(
                status_code=int(meta_error_status_code or 400),
                detail="Memory model request failed",
            ) from exc
    except Exception as e:
        meta_error = True
        meta_error_message = str(e)
        meta_error_type = e.__class__.__name__
        meta_error_status_code = getattr(e, "status_code", 0)
        if raise_on_error:
            raise
        return prompt[:60]
    finally:
        _record_stat()
