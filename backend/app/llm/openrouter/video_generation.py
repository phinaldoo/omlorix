from __future__ import annotations

import base64
import logging
from typing import Any

import requests

from app.llm.models import LLMProvider
from app.llm.openrouter.common import build_openrouter_api_url, build_openrouter_headers
from app.llm.video_generation.shared import (
    collect_video_candidates,
    extract_job_id,
    request_with_retries,
    to_plain_data,
    wait_for_job_result,
)
from app.utils.schemas import FieldSchema, Option, Section, Sections


logger = logging.getLogger(__name__)


OPENROUTER_VIDEO_DEFAULT_DURATION_SECONDS = 6
OPENROUTER_VIDEO_DEFAULT_RESOLUTION = "720p"
OPENROUTER_VIDEO_DEFAULT_ASPECT_RATIO = "16:9"
OPENROUTER_VIDEO_SUPPORTED_RESOLUTIONS = [
    "480p",
    "720p",
    "768p",
    "1080p",
    "1K",
    "2K",
    "4K",
]
OPENROUTER_VIDEO_SUPPORTED_ASPECT_RATIOS = [
    "16:9",
    "9:16",
    "1:1",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "21:9",
    "9:21",
]


def _make_openrouter_headers(provider: LLMProvider, *, include_content_type: bool = True) -> dict[str, str]:
    """Build headers shared by OpenRouter video discovery and generation."""
    provider_settings = provider.settings if isinstance(provider.settings, dict) else None
    return build_openrouter_headers(
        provider.api_key,
        provider_settings,
        include_content_type=include_content_type,
    )


def _normalize_output_modalities(item: dict[str, Any]) -> set[str]:
    architecture = item.get("architecture")
    if not isinstance(architecture, dict):
        return set()

    raw_values = architecture.get("output_modalities")
    if not isinstance(raw_values, list):
        return set()

    normalized: set[str] = set()
    for raw_value in raw_values:
        value = str(raw_value or "").strip().lower()
        if value:
            normalized.add(value)
    return normalized


def _normalize_reference_images(
    reference_files: list[dict[str, Any]] | None,
    *,
    max_items: int = 4,
) -> list[dict[str, Any]]:
    if not reference_files:
        return []

    references: list[dict[str, Any]] = []
    for reference in reference_files:
        mime_type = str((reference or {}).get("mime_type") or "").strip().lower()
        if not mime_type.startswith("image/"):
            continue
        image_bytes = (reference or {}).get("bytes")
        if not isinstance(image_bytes, (bytes, bytearray)):
            continue

        data_url = f"data:{mime_type};base64,{base64.b64encode(bytes(image_bytes)).decode('ascii')}"
        references.append(
            {
                "type": "image_url",
                "image_url": {"url": data_url},
            }
        )
        if len(references) >= max_items:
            break
    return references


def _coerce_optional_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return value != 0
    return None


def _build_video_generation_payload(
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": str(model_name or "").strip(),
        "prompt": str(prompt or "").strip(),
    }
    if not payload["model"]:
        raise ValueError("model is required for OpenRouter video generation")
    if not payload["prompt"]:
        raise ValueError("prompt is required for OpenRouter video generation")

    duration_seconds = _coerce_optional_int(config.get("duration_seconds"))
    if duration_seconds is not None and duration_seconds > 0:
        payload["duration"] = duration_seconds

    size = str(config.get("size") or "").strip()
    if size:
        payload["size"] = size

    resolution = str(config.get("resolution") or "").strip()
    if resolution:
        payload["resolution"] = resolution

    aspect_ratio = str(config.get("aspect_ratio") or "").strip()
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio

    seed = _coerce_optional_int(config.get("seed"))
    if seed is not None:
        payload["seed"] = seed

    generate_audio = _coerce_optional_bool(config.get("generate_audio"))
    if generate_audio is not None:
        payload["generate_audio"] = generate_audio

    input_references = _normalize_reference_images(reference_files)
    if input_references:
        payload["input_references"] = input_references

    return payload


def _extract_usage_cost_details(
    payload: dict[str, Any],
    *,
    model_name: str,
    request_payload: dict[str, Any],
) -> tuple[float | None, dict[str, Any]]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    cost: float | None = None
    raw_cost = usage.get("cost")
    try:
        if raw_cost not in (None, ""):
            cost = float(raw_cost)
    except (TypeError, ValueError):
        cost = None

    details = {
        "model": model_name,
        "duration_seconds": request_payload.get("duration"),
        "resolution": request_payload.get("resolution"),
        "aspect_ratio": request_payload.get("aspect_ratio"),
        "size": request_payload.get("size"),
        "seed": request_payload.get("seed"),
        "generate_audio": request_payload.get("generate_audio"),
        "is_byok": usage.get("is_byok"),
        "generation_id": payload.get("generation_id"),
    }
    return cost, {key: value for key, value in details.items() if value not in (None, "", [])}


def _get_video_models_from_models_api(provider: LLMProvider) -> list[dict[str, Any]]:
    headers = _make_openrouter_headers(provider, include_content_type=False)
    url = build_openrouter_api_url("/models/user", provider.settings if isinstance(provider.settings, dict) else None)
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()

    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []

    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id == "openrouter/auto":
            continue
        if "video" not in _normalize_output_modalities(item):
            continue
        models.append(
            {
                "id": model_id,
                "name": str(item.get("name") or model_id).strip() or model_id,
                "supported_resolutions": [],
                "supported_aspect_ratios": [],
                "supported_sizes": [],
            }
        )
    return models


def openrouter_video_generation_models_list(provider: LLMProvider) -> list[dict[str, Any]]:
    headers = _make_openrouter_headers(provider, include_content_type=False)
    url = build_openrouter_api_url("/videos/models", provider.settings if isinstance(provider.settings, dict) else None)

    try:
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict) and str(item.get("id") or "").strip()]
    except Exception:
        logger.exception("Failed to fetch OpenRouter video models from /videos/models; falling back to /models/user")

    return _get_video_models_from_models_api(provider)


def _get_model_capabilities(
    model_name: str,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    model_id = str(model_name or "").strip()
    if provider is not None:
        try:
            for item in openrouter_video_generation_models_list(provider):
                current_id = str(item.get("id") or "").strip()
                if current_id == model_id:
                    return item
        except Exception:
            logger.exception("Failed to resolve OpenRouter video capabilities for model '%s'", model_id)

    return {
        "id": model_id,
        "name": model_id or "OpenRouter Video Model",
        "supported_resolutions": list(OPENROUTER_VIDEO_SUPPORTED_RESOLUTIONS),
        "supported_aspect_ratios": list(OPENROUTER_VIDEO_SUPPORTED_ASPECT_RATIOS),
        "supported_sizes": [],
    }


def get_video_generation_schema_part_1(db, provider_id: str):
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []

    if provider and provider.api_key:
        try:
            for item in openrouter_video_generation_models_list(provider):
                model_id = str(item.get("id") or "").strip()
                model_label = str(item.get("name") or model_id).strip() or model_id
                if model_id:
                    model_options.append(Option(value=model_id, label=model_label))
        except Exception:
            logger.exception(
                "Failed to fetch OpenRouter video generation models for provider '%s'",
                provider_id,
            )

    return Sections(
        sections=[
            Section(
                title="OpenRouter Video Generation",
                i18n_title="llm.shared.section_openrouter_video.title",
                description="Select the OpenRouter video generation model.",
                i18n_description="llm.shared.section_select_the_openrouter_video.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        description="Choose the video generation model.",
                        i18n_label="schema_video_generation_model_name",
                        i18n_description="schema_video_generation_model_name_desc",
                        type="select",
                        options=model_options,
                        placeholder="Select a model",
                        i18n_placeholder="llm.shared.model_name.placeholder",
                    )
                ],
            )
        ]
    )


def get_video_generation_schema_part_2(model_name: str, provider: LLMProvider | None = None):
    capabilities = _get_model_capabilities(model_name, provider=provider)
    model_label = str(capabilities.get("name") or model_name or "OpenRouter Video Model").strip()

    resolution_options = [
        str(value).strip()
        for value in (capabilities.get("supported_resolutions") or OPENROUTER_VIDEO_SUPPORTED_RESOLUTIONS)
        if str(value).strip()
    ]
    aspect_ratio_options = [
        str(value).strip()
        for value in (capabilities.get("supported_aspect_ratios") or OPENROUTER_VIDEO_SUPPORTED_ASPECT_RATIOS)
        if str(value).strip()
    ]
    size_options = [
        str(value).strip()
        for value in (capabilities.get("supported_sizes") or [])
        if str(value).strip()
    ]

    size_field = FieldSchema(
        key="size",
        label="Size",
        description="Optional exact output size in WIDTHxHEIGHT format.",
        i18n_label="schema_video_generation_size",
        i18n_description="schema_video_generation_size_desc",
        type="string",
        placeholder="Example: 1280x720",
        i18n_placeholder="llm.shared.size.placeholder",
    )
    if size_options:
        size_field.type = "select"
        size_field.options = [Option(value=opt, label=opt.replace("x", " × ")) for opt in size_options]
        size_field.default = size_options[0]
        size_field.placeholder = "Select a size"

    return Sections(
        sections=[
            Section(
                title="Generation Settings",
                description=f"Configure defaults for {model_label}.",
                i18n_title="schema_video_generation_sec1_title",
                i18n_description="schema_video_generation_sec1_desc",
                fields=[
                    FieldSchema(
                        key="duration_seconds",
                        label="Duration (seconds)",
                        description="Target video length in seconds.",
                        type="number",
                        attributes={"min": 1, "max": 120},
                        default=OPENROUTER_VIDEO_DEFAULT_DURATION_SECONDS,
                        i18n_label="schema_video_generation_duration_seconds",
                        i18n_description="schema_video_generation_duration_seconds_desc",
                    ),
                    size_field,
                    FieldSchema(
                        key="resolution",
                        label="Resolution",
                        description="Output resolution preset.",
                        type="select",
                        options=[Option(value=opt, label=opt) for opt in resolution_options],
                        default=(
                            OPENROUTER_VIDEO_DEFAULT_RESOLUTION
                            if OPENROUTER_VIDEO_DEFAULT_RESOLUTION in resolution_options
                            else (
                                resolution_options[0]
                                if resolution_options
                                else OPENROUTER_VIDEO_DEFAULT_RESOLUTION
                            )
                        ),
                        i18n_label="schema_video_generation_resolution",
                        i18n_description="schema_video_generation_resolution_desc",
                    ),
                    FieldSchema(
                        key="aspect_ratio",
                        label="Aspect Ratio",
                        description="Target aspect ratio for generated videos.",
                        type="select",
                        options=[Option(value=opt, label=opt) for opt in aspect_ratio_options],
                        default=(
                            OPENROUTER_VIDEO_DEFAULT_ASPECT_RATIO
                            if OPENROUTER_VIDEO_DEFAULT_ASPECT_RATIO in aspect_ratio_options
                            else (
                                aspect_ratio_options[0]
                                if aspect_ratio_options
                                else OPENROUTER_VIDEO_DEFAULT_ASPECT_RATIO
                            )
                        ),
                        i18n_label="schema_video_generation_aspect_ratio",
                        i18n_description="schema_video_generation_aspect_ratio_desc",
                    ),
                    FieldSchema(
                        key="seed",
                        label="Seed",
                        description="Optional deterministic seed for reproducible generations.",
                        type="number",
                        attributes={"min": 0},
                        i18n_label="schema_video_generation_seed",
                        i18n_description="schema_video_generation_seed_desc",
                    ),
                    FieldSchema(
                        key="generate_audio",
                        label="Generate Audio",
                        description="Generate soundtrack/audio alongside the video when the model supports it.",
                        type="boolean",
                        default=True,
                    ),
                    FieldSchema(
                        key="enable_reference_files",
                        label="Enable Reference Files",
                        description=(
                            "Allow the video_generation tool to pass chat reference images "
                            "to OpenRouter as visual guidance."
                        ),
                        type="boolean",
                        default=False,
                    ),
                ],
            ),
            Section(
                title="Execution Controls",
                description=f"Configure polling, timeouts, and retries for {model_label}.",
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


def _submit_job(
    provider: LLMProvider,
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    max_retries: int,
) -> dict[str, Any]:
    url = build_openrouter_api_url("/videos", provider.settings if isinstance(provider.settings, dict) else None)
    headers = _make_openrouter_headers(provider)
    response = request_with_retries(
        "POST",
        url,
        headers=headers,
        json_payload=payload,
        timeout_seconds=min(90, max(30, timeout_seconds)),
        max_retries=max_retries,
    )
    return to_plain_data(response.json())


def _fetch_job(
    provider: LLMProvider,
    *,
    job_id: str | None,
    polling_url: str | None,
    timeout_seconds: int,
    max_retries: int,
) -> dict[str, Any]:
    url = str(polling_url or "").strip()
    if not url:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise RuntimeError("OpenRouter video generation did not return a job id.")
        url = build_openrouter_api_url(
            f"/videos/{normalized_job_id}",
            provider.settings if isinstance(provider.settings, dict) else None,
        )

    headers = _make_openrouter_headers(provider)
    response = request_with_retries(
        "GET",
        url,
        headers=headers,
        timeout_seconds=min(60, max(20, timeout_seconds)),
        max_retries=max_retries,
    )
    return to_plain_data(response.json())


def generate_video(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request_payload = _build_video_generation_payload(
        model_name,
        prompt,
        config,
        reference_files=reference_files,
    )
    timeout_seconds = _coerce_optional_int(config.get("timeout_seconds")) or 600
    poll_interval_seconds = _coerce_optional_int(config.get("poll_interval_seconds")) or 5
    max_retries = _coerce_optional_int(config.get("max_retries")) or 0

    initial_payload = _submit_job(
        provider,
        request_payload,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )

    initial_urls, initial_inline_videos = collect_video_candidates(initial_payload)
    initial_cost, initial_cost_details = _extract_usage_cost_details(
        initial_payload,
        model_name=model_name,
        request_payload=request_payload,
    )
    if initial_urls or initial_inline_videos:
        return {
            "provider_job_id": extract_job_id(initial_payload),
            "payload": initial_payload,
            "urls": initial_urls,
            "inline_videos": initial_inline_videos,
            "request_payload": dict(request_payload),
            "cost": initial_cost,
            "cost_details": initial_cost_details,
        }

    job_id = extract_job_id(initial_payload)
    polling_url = str(initial_payload.get("polling_url") or "").strip() or None
    final_payload = wait_for_job_result(
        lambda: _fetch_job(
            provider,
            job_id=job_id,
            polling_url=polling_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        ),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        provider_name="OpenRouter",
    )
    final_urls, final_inline_videos = collect_video_candidates(final_payload)
    if not final_urls and not final_inline_videos:
        raise RuntimeError("OpenRouter completed the video job but returned no downloadable output.")

    final_cost, final_cost_details = _extract_usage_cost_details(
        final_payload,
        model_name=model_name,
        request_payload=request_payload,
    )
    return {
        "provider_job_id": job_id or extract_job_id(final_payload),
        "payload": final_payload,
        "urls": final_urls,
        "inline_videos": final_inline_videos,
        "request_payload": dict(request_payload),
        "cost": final_cost,
        "cost_details": final_cost_details,
    }
