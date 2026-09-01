"""OpenAI-compatible asynchronous video generation integration."""

import base64
import json
from typing import Any

from openai import Client

from app.llm.models import LLMProvider
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.llm.video_generation.shared import (
    collect_video_candidates,
    extract_job_id,
    request_with_retries,
    to_plain_data,
    wait_for_job_result,
)
from app.utils.schemas import FieldSchema, Section, Sections


OPENAI_COMPATIBLE_VIDEO_DEFAULT_DURATION_SECONDS = 8
OPENAI_COMPATIBLE_VIDEO_DEFAULT_SIZE = "720x1280"


def get_video_generation_schema_part_2(model_name: str):
    """Return settings understood by OpenAI-compatible Videos endpoints.

    Custom providers do not expose a common capability schema, so the fields
    intentionally remain generic instead of applying OpenAI's removed Sora
    model catalog or pricing assumptions.
    """
    selected_model = str(model_name or "").strip() or "OpenAI-compatible video model"
    return Sections(
        sections=[
            Section(
                title="Generation Settings",
                description=f"Configure defaults for {selected_model}.",
                i18n_title="schema_video_generation_sec1_title",
                i18n_description="schema_video_generation_sec1_desc",
                fields=[
                    FieldSchema(
                        key="duration_seconds",
                        label="Duration (seconds)",
                        description="Target video length in seconds.",
                        type="number",
                        attributes={"min": 1, "max": 120},
                        default=OPENAI_COMPATIBLE_VIDEO_DEFAULT_DURATION_SECONDS,
                        i18n_label="schema_video_generation_duration_seconds",
                        i18n_description="schema_video_generation_duration_seconds_desc",
                    ),
                    FieldSchema(
                        key="size",
                        label="Size",
                        description="Output width × height preset.",
                        type="string",
                        default=OPENAI_COMPATIBLE_VIDEO_DEFAULT_SIZE,
                        i18n_label="schema_video_generation_size",
                        i18n_description="schema_video_generation_size_desc",
                    ),
                    FieldSchema(
                        key="enable_reference_files",
                        label="Enable Reference Files",
                        description=(
                            "Allow the video_generation tool to pass chat reference images "
                            "to OpenAI-compatible video generation."
                        ),
                        type="boolean",
                        default=False,
                    ),
                ],
            ),
            Section(
                title="Execution Controls",
                description=f"Configure polling, timeouts, and retries for {selected_model}.",
                i18n_title="schema_video_generation_sec2_title",
                i18n_description="schema_video_generation_sec2_desc",
                fields=[
                    FieldSchema(
                        key="timeout_seconds",
                        label="Job Timeout (seconds)",
                        description="Maximum wait time for provider jobs before timeout.",
                        type="number",
                        attributes={"min": 60, "max": 3600},
                        default=600,
                        i18n_label="schema_video_generation_timeout_seconds",
                        i18n_description="schema_video_generation_timeout_seconds_desc",
                    ),
                    FieldSchema(
                        key="poll_interval_seconds",
                        label="Poll Interval (seconds)",
                        description="How often to poll provider job status.",
                        type="number",
                        attributes={"min": 1, "max": 30},
                        default=5,
                        i18n_label="schema_video_generation_poll_interval_seconds",
                        i18n_description="schema_video_generation_poll_interval_seconds_desc",
                    ),
                    FieldSchema(
                        key="max_retries",
                        label="Max Retries",
                        description="Maximum retry attempts for transient provider/API failures.",
                        type="number",
                        attributes={"min": 0, "max": 10},
                        default=2,
                        i18n_label="schema_video_generation_max_retries",
                        i18n_description="schema_video_generation_max_retries_desc",
                    ),
                ],
            ),
        ]
    )


def openai_compatible_video_generation_models_list(
    provider: LLMProvider,
) -> list[dict[str, Any]]:
    """List models advertised by a custom OpenAI-compatible provider."""
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    base_url = str(provider_settings.get("base_url") or "").strip()
    if not base_url:
        raise ValueError(
            "A base URL is required for OpenAI-compatible video generation."
        )

    client_kwargs = {
        "api_key": provider.api_key,
        "base_url": base_url,
    }
    default_headers = custom_headers_to_dict(provider_settings.get("custom_headers"))
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    client = Client(**client_kwargs)
    models = client.models.list()
    items: list[dict[str, Any]] = []
    for model in models:
        model_id = getattr(model, "id", None) or getattr(model, "model", None)
        if not model_id:
            continue
        items.append(
            {
                "id": model_id,
                "created": getattr(model, "created", None),
                "object": getattr(model, "object", None),
                "owned_by": getattr(model, "owned_by", None),
            }
        )
    return items


def _build_video_generation_payload(
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a conservative payload for compatible Videos endpoints."""
    normalized_model = str(model_name or "").strip()
    normalized_prompt = str(prompt or "").strip()
    if not normalized_model:
        raise ValueError("model is required for OpenAI-compatible video generation")
    if not normalized_prompt:
        raise ValueError("prompt is required for OpenAI-compatible video generation")
    try:
        seconds = int(
            config.get("duration_seconds")
            or OPENAI_COMPATIBLE_VIDEO_DEFAULT_DURATION_SECONDS
        )
    except (TypeError, ValueError):
        seconds = OPENAI_COMPATIBLE_VIDEO_DEFAULT_DURATION_SECONDS
    seconds = min(max(seconds, 1), 120)

    payload: dict[str, Any] = {
        "model": normalized_model,
        "prompt": normalized_prompt,
        "seconds": str(seconds),
    }
    size = str(config.get("size") or "").strip()
    if size:
        payload["size"] = size
    input_reference = _build_openai_input_references(reference_files)
    if input_reference:
        payload["input_reference"] = input_reference
    return payload


def _build_openai_input_references(
    reference_files: list[dict[str, Any]] | None,
    *,
    max_items: int = 3,
) -> list[dict[str, str]]:
    """Build OpenAI input references."""
    if not reference_files:
        return []

    refs: list[dict[str, str]] = []
    for reference in reference_files:
        mime_type = str((reference or {}).get("mime_type") or "").strip().lower()
        if not mime_type.startswith("image/"):
            continue
        image_bytes = (reference or {}).get("bytes")
        if not isinstance(image_bytes, (bytes, bytearray)):
            continue
        data_url = f"data:{mime_type};base64,{base64.b64encode(bytes(image_bytes)).decode('ascii')}"
        refs.append({"type": "image_url", "url": data_url})
        if len(refs) >= max_items:
            break
    return refs


def _make_openai_compatible_headers(
    provider: LLMProvider,
    *,
    include_content_type: bool = True,
) -> dict[str, str]:
    """Build headers for the configured compatible endpoint."""
    headers: dict[str, str] = {}
    api_key = str(provider.api_key or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if include_content_type:
        headers["Content-Type"] = "application/json"
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    headers.update(custom_headers_to_dict(provider_settings.get("custom_headers")))
    return headers


def openai_compatible_video_base_url(provider: LLMProvider) -> str:
    """Return the required custom endpoint URL without an OpenAI fallback."""
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}
    base_url = str(provider_settings.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError(
            "A base URL is required for OpenAI-compatible video generation."
        )
    return base_url


def _submit_openai_compatible_job(
    provider: LLMProvider,
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    max_retries: int,
) -> dict[str, Any]:
    """Submit one job to the configured compatible endpoint."""
    base_url = openai_compatible_video_base_url(provider)
    has_input_reference = bool(payload.get("input_reference"))
    headers = _make_openai_compatible_headers(
        provider,
        include_content_type=not has_input_reference,
    )
    endpoint_candidates = [
        f"{base_url}/videos/generations",
        f"{base_url}/video/generations",
        f"{base_url}/videos",
    ]
    last_error: Exception | None = None
    for endpoint in endpoint_candidates:
        try:
            if has_input_reference:
                form_payload: dict[str, Any] = {}
                for key, value in payload.items():
                    if value in (None, ""):
                        continue
                    if key == "input_reference":
                        form_payload[key] = json.dumps(value)
                    else:
                        form_payload[key] = str(value)
                response = request_with_retries(
                    "POST",
                    endpoint,
                    headers=headers,
                    data_payload=form_payload,
                    timeout_seconds=min(90, max(30, timeout_seconds)),
                    max_retries=max_retries,
                )
                return to_plain_data(response.json())

            response = request_with_retries(
                "POST",
                endpoint,
                headers=headers,
                json_payload=payload,
                timeout_seconds=min(90, max(30, timeout_seconds)),
                max_retries=max_retries,
            )
            return to_plain_data(response.json())
        except Exception as exc:  # pragma: no cover - provider behavior
            last_error = exc

        # If form payload was rejected, retry endpoint once with JSON.
        if has_input_reference:
            try:
                json_headers = _make_openai_compatible_headers(
                    provider,
                    include_content_type=True,
                )
                response = request_with_retries(
                    "POST",
                    endpoint,
                    headers=json_headers,
                    json_payload=payload,
                    timeout_seconds=min(90, max(30, timeout_seconds)),
                    max_retries=max_retries,
                )
                return to_plain_data(response.json())
            except Exception as exc:  # pragma: no cover - provider behavior
                last_error = exc
    raise RuntimeError(
        f"Failed to submit OpenAI-compatible video generation request: {last_error}"
    )


def _fetch_openai_compatible_job(
    provider: LLMProvider,
    job_id: str,
    *,
    timeout_seconds: int,
    max_retries: int,
) -> dict[str, Any]:
    """Fetch one compatible video-generation job."""
    base_url = openai_compatible_video_base_url(provider)
    headers = _make_openai_compatible_headers(provider)
    endpoint_candidates = [
        f"{base_url}/videos/generations/{job_id}",
        f"{base_url}/video/generations/{job_id}",
        f"{base_url}/videos/{job_id}",
    ]
    last_error: Exception | None = None
    for endpoint in endpoint_candidates:
        try:
            response = request_with_retries(
                "GET",
                endpoint,
                headers=headers,
                timeout_seconds=min(60, max(20, timeout_seconds)),
                max_retries=max_retries,
            )
            return to_plain_data(response.json())
        except Exception as exc:  # pragma: no cover - provider behavior
            last_error = exc
    raise RuntimeError(
        f"Failed to poll OpenAI-compatible video job '{job_id}': {last_error}"
    )


def generate_video(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate video through a custom OpenAI-compatible endpoint."""
    payload = _build_video_generation_payload(
        model_name,
        prompt,
        config,
        reference_files=reference_files,
    )
    timeout_seconds = int(config.get("timeout_seconds") or 600)
    poll_interval_seconds = int(config.get("poll_interval_seconds") or 5)
    max_retries = int(config.get("max_retries") or 0)

    initial_payload = _submit_openai_compatible_job(
        provider,
        payload,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )

    urls, inline_videos = collect_video_candidates(initial_payload)
    if urls or inline_videos:
        return {
            "provider_job_id": extract_job_id(initial_payload),
            "payload": initial_payload,
            "urls": urls,
            "inline_videos": inline_videos,
            "request_payload": dict(payload),
        }

    job_id = extract_job_id(initial_payload)
    if not job_id:
        raise RuntimeError(
            "OpenAI-compatible provider did not return a job id or video output."
        )

    final_payload = wait_for_job_result(
        lambda: _fetch_openai_compatible_job(
            provider,
            job_id,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        ),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        provider_name="OpenAI-compatible provider",
    )
    final_urls, final_inline_videos = collect_video_candidates(final_payload)
    if not final_urls and not final_inline_videos:
        raise RuntimeError(
            "OpenAI-compatible provider completed the job but did not return "
            "a downloadable video."
        )

    return {
        "provider_job_id": job_id,
        "payload": final_payload,
        "urls": final_urls,
        "inline_videos": final_inline_videos,
        "request_payload": dict(payload),
    }
