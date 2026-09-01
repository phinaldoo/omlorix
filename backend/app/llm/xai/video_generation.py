"""xAI Imagine asynchronous video generation integration."""

from __future__ import annotations

import base64
from typing import Any

import requests

from app.llm.models import LLMProvider
from app.llm.video_generation.shared import (
    collect_video_candidates,
    extract_job_id,
    wait_for_job_result,
)
from app.llm.xai.common import (
    require_xai_success,
    xai_base_url,
    xai_cost_from_usage,
    xai_headers,
    xai_timeout,
)
from app.utils.schemas import FieldSchema, Option, Section, Sections


XAI_VIDEO_MODELS = ["grok-imagine-video", "grok-imagine-video-1.5"]
XAI_VIDEO_ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]
XAI_VIDEO_RESOLUTIONS = ["480p", "720p"]
XAI_VIDEO_15_RESOLUTIONS = [*XAI_VIDEO_RESOLUTIONS, "1080p"]


def _is_xai_video_15(model_name: str | None) -> bool:
    """Return whether a canonical or aliased Video 1.5 identifier is selected."""
    return str(model_name or "").strip().lower().startswith("grok-imagine-video-1.5")


def _reference_data_url(reference: dict[str, Any]) -> str | None:
    """Encode an image reference for xAI's JSON request body."""
    mime_type = str(reference.get("mime_type") or "").strip().lower()
    image_bytes = reference.get("bytes")
    if not mime_type.startswith("image/") or not isinstance(image_bytes, (bytes, bytearray)):
        return None
    encoded = base64.b64encode(bytes(image_bytes)).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_payload(
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build one xAI text-, image-, or reference-to-video payload."""
    try:
        duration = int(config.get("duration_seconds") or 8)
    except (TypeError, ValueError):
        duration = 8
    encoded_references = [
        url
        for reference in (reference_files or [])[:7]
        if (url := _reference_data_url(reference))
    ]
    is_video_15 = _is_xai_video_15(model_name)
    if is_video_15 and not encoded_references:
        # xAI currently exposes Video 1.5 only for image-to-video. Rejecting a
        # text-only call here avoids creating a job the provider cannot serve.
        raise ValueError("xAI Video 1.5 requires a reference image")
    if is_video_15:
        # The first image is the starting frame. Additional images belong to
        # the original model's separate reference-to-video mode.
        encoded_references = encoded_references[:1]
    duration = min(max(duration, 1), 15)

    aspect_ratio = str(config.get("aspect_ratio") or "16:9").strip()
    resolution = str(config.get("resolution") or "720p").strip().lower()
    supported_resolutions = (
        XAI_VIDEO_15_RESOLUTIONS if is_video_15 else XAI_VIDEO_RESOLUTIONS
    )
    payload: dict[str, Any] = {
        "model": model_name,
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": (
            aspect_ratio if aspect_ratio in XAI_VIDEO_ASPECT_RATIOS else "16:9"
        ),
        "resolution": (
            resolution if resolution in supported_resolutions else "720p"
        ),
    }

    if len(encoded_references) == 1:
        payload["image"] = {"url": encoded_references[0]}
    elif encoded_references:
        payload["reference_images"] = [
            {"url": url}
            for url in encoded_references
        ]
    return payload


def _submit_job(
    provider: LLMProvider,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Submit an xAI Imagine video job."""
    response = requests.post(
        f"{xai_base_url(provider)}/videos/generations",
        headers=xai_headers(provider),
        json=payload,
        timeout=xai_timeout(),
    )
    require_xai_success(response, "video generation")
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("xAI video submission response was not a JSON object")
    return result


def _fetch_job(
    provider: LLMProvider,
    request_id: str,
) -> dict[str, Any]:
    """Poll one xAI Imagine video job."""
    response = requests.get(
        f"{xai_base_url(provider)}/videos/{request_id}",
        headers=xai_headers(provider, include_content_type=False),
        timeout=xai_timeout(),
    )
    require_xai_success(response, "video polling")
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("xAI video polling response was not a JSON object")
    return result


def generate_video(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    config: dict[str, Any],
    reference_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate a video and wait until xAI returns a temporary download URL."""
    payload = _build_payload(model_name, prompt, config, reference_files)
    timeout_seconds = int(config.get("timeout_seconds") or 600)
    poll_interval_seconds = int(config.get("poll_interval_seconds") or 5)
    initial = _submit_job(provider, payload)
    request_id = extract_job_id(initial)
    if not request_id:
        raise RuntimeError("xAI did not return a video request id")

    final = wait_for_job_result(
        lambda: _fetch_job(provider, request_id),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        provider_name="xAI",
    )
    urls, inline_videos = collect_video_candidates(final)
    if not urls and not inline_videos:
        raise RuntimeError("xAI completed the video job without a downloadable result")
    cost, cost_details = xai_cost_from_usage(final)
    return {
        "provider_job_id": request_id,
        "payload": final,
        "urls": urls,
        "inline_videos": inline_videos,
        "request_payload": payload,
        "cost": cost,
        "cost_details": {
            "model": model_name,
            "duration_seconds": payload["duration"],
            "aspect_ratio": payload["aspect_ratio"],
            "resolution": payload["resolution"],
            **cost_details,
        },
    }


def list_video_models(provider: LLMProvider) -> list[dict[str, str]]:
    """List xAI video models, with a documented fallback."""
    try:
        response = requests.get(
            f"{xai_base_url(provider)}/video-generation-models",
            headers=xai_headers(provider, include_content_type=False),
            timeout=xai_timeout(),
        )
        require_xai_success(response, "model listing")
        payload = response.json()
        entries = payload.get("models") if isinstance(payload, dict) else []
        model_ids: list[str] = []
        for item in entries if isinstance(entries, list) else []:
            if not isinstance(item, dict):
                continue
            candidates = [item.get("id"), *(item.get("aliases") or [])]
            for candidate in candidates:
                model_id = str(candidate or "").strip()
                if (
                    model_id.startswith("grok-imagine-video")
                    and model_id not in model_ids
                ):
                    model_ids.append(model_id)
        if model_ids:
            return [{"id": item} for item in model_ids]
    except Exception:
        pass
    return [{"id": item} for item in XAI_VIDEO_MODELS]


def get_video_generation_schema_part_1(db, provider_id: str) -> Sections:
    """Build the xAI video model picker."""
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    models = list_video_models(provider) if provider else [{"id": item} for item in XAI_VIDEO_MODELS]
    return Sections(
        sections=[
            Section(
                title="Video Generation Models",
                i18n_title="admin.shared.section_video_generation.title",
                description="",
                i18n_description="admin.shared.section_value.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="admin.shared.model_name.label",
                        description="Choose the video generation model.",
                        i18n_description="admin.shared.model_name.description",
                        type="select",
                        options=[
                            Option(value=item["id"], label=item["id"])
                            for item in models
                        ],
                        placeholder="Select a model",
                        i18n_placeholder="admin.shared.model_name.placeholder",
                    )
                ],
            )
        ]
    )


def get_video_generation_schema_part_2(model_name: str) -> Sections:
    """Build xAI Imagine video defaults."""
    resolutions = (
        XAI_VIDEO_15_RESOLUTIONS
        if _is_xai_video_15(model_name)
        else XAI_VIDEO_RESOLUTIONS
    )
    return Sections(
        sections=[
            Section(
                title="Generation Settings",
                i18n_title="schema_video_generation_sec1_title",
                description="Configure defaults for xAI Imagine video.",
                i18n_description="schema_video_generation_sec1_desc",
                fields=[
                    FieldSchema(
                        key="duration_seconds",
                        label="Duration (seconds)",
                        i18n_label="schema_video_generation_duration_seconds",
                        description="Target video length in seconds.",
                        i18n_description="schema_video_generation_duration_seconds_desc",
                        type="number",
                        attributes={"min": 1, "max": 15},
                        default=8,
                    ),
                    FieldSchema(
                        key="aspect_ratio",
                        label="Aspect Ratio",
                        i18n_label="schema_video_generation_aspect_ratio",
                        description="Target aspect ratio for generated videos.",
                        i18n_description="schema_video_generation_aspect_ratio_desc",
                        type="select",
                        options=[
                            Option(value=value, label=value)
                            for value in XAI_VIDEO_ASPECT_RATIOS
                        ],
                        default="16:9",
                    ),
                    FieldSchema(
                        key="resolution",
                        label="Resolution",
                        i18n_label="schema_video_generation_resolution",
                        description="Output resolution preset.",
                        i18n_description="schema_video_generation_resolution_desc",
                        type="select",
                        options=[
                            Option(value=value, label=value)
                            for value in resolutions
                        ],
                        default="720p",
                    ),
                    FieldSchema(
                        key="enable_reference_files",
                        label="Enable Reference Files",
                        i18n_label="schema_music_generation_enable_reference_images",
                        description="Allow xAI video generation to use chat reference images.",
                        i18n_description="schema_xai_video_reference_desc",
                        type="boolean",
                        # Video 1.5 is image-to-video only; automatically load
                        # the chat's first eligible image for that model.
                        default=_is_xai_video_15(model_name),
                    ),
                ],
            ),
            Section(
                title="Execution Controls",
                i18n_title="schema_video_generation_sec2_title",
                description="Configure polling, timeouts, and retry behavior.",
                i18n_description="schema_video_generation_sec2_desc",
                fields=[
                    FieldSchema(
                        key="timeout_seconds",
                        label="Job Timeout (seconds)",
                        i18n_label="schema_video_generation_timeout_seconds",
                        description="Maximum wait time for provider jobs before timeout.",
                        i18n_description="schema_video_generation_timeout_seconds_desc",
                        type="number",
                        attributes={"min": 60, "max": 3600},
                        default=600,
                    ),
                    FieldSchema(
                        key="poll_interval_seconds",
                        label="Poll Interval (seconds)",
                        i18n_label="schema_video_generation_poll_interval_seconds",
                        description="How often to poll provider job status.",
                        i18n_description="schema_video_generation_poll_interval_seconds_desc",
                        type="number",
                        attributes={"min": 1, "max": 30},
                        default=5,
                    ),
                    FieldSchema(
                        key="max_retries",
                        label="Max Retries",
                        i18n_label="schema_video_generation_max_retries",
                        description="Maximum retry attempts for transient failures.",
                        i18n_description="schema_video_generation_max_retries_desc",
                        type="number",
                        attributes={"min": 0, "max": 10},
                        default=2,
                    ),
                ],
            ),
        ]
    )
