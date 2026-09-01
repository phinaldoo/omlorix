"""xAI Imagine image generation and editing integration."""

from __future__ import annotations

import base64
import binascii
from typing import Any

import requests

from app.llm.models import LLMProvider
from app.llm.xai.common import (
    require_xai_success,
    download_xai_result_url,
    xai_base_url,
    xai_cost_from_usage,
    xai_headers,
    xai_timeout,
)
from app.utils.schemas import FieldSchema, Option, Section, Sections


XAI_IMAGE_MODELS = [
    "grok-imagine-image-2.0",
    "grok-imagine-image",
    "grok-imagine-image-quality",
]
XAI_IMAGE_RESOLUTIONS = ["1k", "2k"]
XAI_IMAGE_QUALITIES = ["auto", "low", "medium"]
XAI_IMAGE_PRICING = {
    "grok-imagine-image-2.0": {
        "input_image": 0.01,
        "output": {
            ("1k", "low"): 0.04,
            ("2k", "low"): 0.06,
            ("1k", "medium"): 0.06,
            ("2k", "medium"): 0.08,
        },
    },
    "grok-imagine-image": {
        "input_image": 0.002,
        "output": {("1k", None): 0.02, ("2k", None): 0.02},
    },
    "grok-imagine-image-quality": {
        "input_image": 0.01,
        "output": {("1k", None): 0.05, ("2k", None): 0.07},
    },
}
XAI_IMAGE_RESULT_MAX_BYTES = 50 * 1024 * 1024
XAI_IMAGE_ASPECT_RATIOS = [
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "19.5:9",
    "9:19.5",
    "20:9",
    "9:20",
    "21:9",
    "5:2",
]
XAI_IMAGE_FORMATS = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/webp": (".webp", b"RIFF"),
    "image/gif": (".gif", b"GIF8"),
}


def _image_data_url(reference: dict | bytes) -> str | None:
    """Encode a stored Omlorix image as an xAI-supported data URL."""
    image_bytes: Any = reference
    mime_type = "image/png"
    if isinstance(reference, dict):
        image_bytes = reference.get("bytes")
        configured_mime = str(reference.get("mime_type") or "").strip().lower()
        if configured_mime.startswith("image/"):
            mime_type = configured_mime
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        return None
    encoded = base64.b64encode(bytes(image_bytes)).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _normalize_image_format(
    image_bytes: bytes,
    content_type: str | None = None,
) -> tuple[str, str]:
    """Resolve a safe MIME type and extension from headers and file signatures."""
    normalized_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type == "image/jpg":
        normalized_type = "image/jpeg"
    if normalized_type in XAI_IMAGE_FORMATS:
        expected_prefix = XAI_IMAGE_FORMATS[normalized_type][1]
        if image_bytes.startswith(expected_prefix) and (
            normalized_type != "image/webp"
            or (
                len(image_bytes) >= 12
                and image_bytes[8:12] == b"WEBP"
            )
        ):
            return normalized_type, XAI_IMAGE_FORMATS[normalized_type][0]

    for detected_type, (extension, prefix) in XAI_IMAGE_FORMATS.items():
        if not image_bytes.startswith(prefix):
            continue
        if detected_type == "image/webp" and (
            len(image_bytes) < 12 or image_bytes[8:12] != b"WEBP"
        ):
            continue
        return detected_type, extension
    raise RuntimeError("xAI returned an unsupported image format")


def _download_xai_image(url: str, *, timeout: int) -> tuple[bytes, str, str]:
    """Download an xAI image through the public-peer-pinning transport."""
    image_bytes, content_type, _final_url = download_xai_result_url(
        url,
        operation="image",
        expected_content_prefix="image/",
        max_bytes=XAI_IMAGE_RESULT_MAX_BYTES,
        timeout=timeout,
    )
    file_type, extension = _normalize_image_format(image_bytes, content_type)
    return image_bytes, file_type, extension


def _resolve_image_result(
    payload: dict[str, Any],
    *,
    timeout: int,
) -> tuple[bytes, str, str]:
    """Resolve either URL or base64 image data from xAI's response envelope."""
    data = payload.get("data")
    first = data[0] if isinstance(data, list) and data else None
    if not isinstance(first, dict):
        raise RuntimeError("xAI did not return image data")

    encoded = str(first.get("b64_json") or first.get("base64") or "").strip()
    if encoded:
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("xAI returned invalid base64 image data") from exc
        file_type, extension = _normalize_image_format(image_bytes)
        return image_bytes, file_type, extension
    url = str(first.get("url") or "").strip()
    if url:
        return _download_xai_image(url, timeout=timeout)
    raise RuntimeError("xAI did not return a usable image result")


def _request_image(
    provider: LLMProvider,
    *,
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Call one native Imagine endpoint and normalize its result for Omlorix."""
    response = requests.post(
        f"{xai_base_url(provider)}/{endpoint.lstrip('/')}",
        headers=xai_headers(provider),
        json=payload,
        timeout=xai_timeout(),
    )
    require_xai_success(response, "image request")
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("xAI image response was not a JSON object")
    image_bytes, file_type, extension = _resolve_image_result(
        result,
        timeout=xai_timeout(),
    )
    cost, cost_details = xai_cost_from_usage(result)
    if not cost:
        pricing = XAI_IMAGE_PRICING.get(str(payload.get("model") or ""), {})
        resolution = str(payload.get("resolution") or "1k").lower()
        is_edit = endpoint.rstrip("/").endswith("edits")
        quality = payload.get("quality")
        if quality == "auto":
            quality = "medium" if is_edit else "low"
        output_price = pricing.get("output", {}).get((resolution, quality))
        if output_price is not None:
            image_input_count = len(payload.get("images") or [])
            if payload.get("image"):
                image_input_count = 1
            cost = float(output_price) + image_input_count * float(
                pricing.get("input_image", 0) or 0
            )
            cost_details = {
                "currency": "USD",
                "pricing_source": "static_catalog",
                "image_input_count": image_input_count,
                "output_images": 1,
            }
    return {
        "image_bytes": image_bytes,
        "file_type": file_type,
        "extension": extension,
        "cost": cost,
        "cost_details": {
            "model": str(payload.get("model") or ""),
            "aspect_ratio": str(payload.get("aspect_ratio") or "auto"),
            "resolution": str(payload.get("resolution") or "1k"),
            "quality": str(payload.get("quality") or "auto"),
            **cost_details,
        },
    }


def generate_image(
    provider: LLMProvider,
    model: str,
    prompt: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Generate an image with xAI Imagine."""
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "url",
    }
    aspect_ratio = str(settings.get("aspect_ratio") or settings.get("size") or "auto").strip()
    if aspect_ratio in XAI_IMAGE_ASPECT_RATIOS:
        payload["aspect_ratio"] = aspect_ratio
    resolution = str(settings.get("resolution") or "1k").strip().lower()
    if resolution in XAI_IMAGE_RESOLUTIONS:
        payload["resolution"] = resolution
    quality = str(settings.get("quality") or "auto").strip().lower()
    if model == "grok-imagine-image-2.0" and quality in XAI_IMAGE_QUALITIES:
        payload["quality"] = quality
    return _request_image(provider, endpoint="images/generations", payload=payload)


def edit_image(
    provider: LLMProvider,
    model: str,
    prompt: str,
    settings: dict[str, Any],
    reference_images: list[dict | bytes],
) -> dict[str, Any]:
    """Edit one or more images with xAI Imagine."""
    max_reference_images = 5 if model == "grok-imagine-image-2.0" else 3
    encoded_images = [
        {"type": "image_url", "url": data_url}
        for reference in reference_images[:max_reference_images]
        if (data_url := _image_data_url(reference))
    ]
    if not encoded_images:
        raise ValueError("At least one valid reference image is required for xAI image editing")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "response_format": "url",
    }
    if len(encoded_images) == 1:
        payload["image"] = {"url": encoded_images[0]["url"]}
    else:
        payload["images"] = encoded_images
    aspect_ratio = str(settings.get("aspect_ratio") or settings.get("size") or "auto").strip()
    if aspect_ratio in XAI_IMAGE_ASPECT_RATIOS:
        payload["aspect_ratio"] = aspect_ratio
    resolution = str(settings.get("resolution") or "1k").strip().lower()
    if resolution in XAI_IMAGE_RESOLUTIONS:
        payload["resolution"] = resolution
    quality = str(settings.get("quality") or "auto").strip().lower()
    if model == "grok-imagine-image-2.0" and quality in XAI_IMAGE_QUALITIES:
        payload["quality"] = quality
    return _request_image(provider, endpoint="images/edits", payload=payload)


def list_image_models(provider: LLMProvider) -> list[dict[str, str]]:
    """List Imagine models, falling back to xAI's documented model IDs."""
    try:
        response = requests.get(
            f"{xai_base_url(provider)}/image-generation-models",
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
                    model_id.startswith("grok-imagine-image")
                    and model_id not in model_ids
                ):
                    model_ids.append(model_id)
        if model_ids:
            return [{"id": model_id} for model_id in model_ids]
    except Exception:
        pass
    return [{"id": model_id} for model_id in XAI_IMAGE_MODELS]


def get_image_generation_schema_part_1(db, provider_id: str) -> Sections:
    """Build the xAI image-model picker."""
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    models = list_image_models(provider) if provider else [{"id": item} for item in XAI_IMAGE_MODELS]
    return Sections(
        sections=[
            Section(
                title="xAI Image Generation",
                i18n_title="schema_xai_image_generation_title",
                description="Choose which xAI Imagine model to use.",
                i18n_description="schema_xai_image_model_desc",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="llm.shared.model_name.label",
                        description="Choose which model to use for this provider.",
                        i18n_description="llm.shared.model_name.description",
                        type="select",
                        options=[
                            Option(value=item["id"], label=item["id"])
                            for item in models
                        ],
                        placeholder="Select a model",
                        i18n_placeholder="llm.shared.model_name.placeholder",
                    )
                ],
            )
        ]
    )


def get_image_generation_schema_part_2() -> Sections:
    """Build xAI Imagine generation defaults."""
    return Sections(
        sections=[
            Section(
                title="xAI Image Generation",
                i18n_title="schema_xai_image_generation_title",
                description="Configure xAI Imagine generation and editing.",
                i18n_description="schema_xai_image_settings_desc",
                fields=[
                    FieldSchema(
                        key="settings.aspect_ratio",
                        label="Aspect Ratio",
                        i18n_label="schema_video_generation_aspect_ratio",
                        description="Target aspect ratio for generated images.",
                        i18n_description="schema_xai_image_aspect_ratio_desc",
                        type="select",
                        options=[
                            Option(value=value, label=value)
                            for value in XAI_IMAGE_ASPECT_RATIOS
                        ],
                        default="auto",
                    ),
                    FieldSchema(
                        key="settings.resolution",
                        label="Resolution",
                        i18n_label="schema_xai_image_resolution",
                        description="Choose 1K or 2K output resolution for generated images.",
                        i18n_description="schema_xai_image_resolution_desc",
                        type="select",
                        options=[
                            Option(value=value, label=value.upper(), translatable=False)
                            for value in XAI_IMAGE_RESOLUTIONS
                        ],
                        default="1k",
                    ),
                    FieldSchema(
                        key="settings.quality",
                        label="Quality",
                        i18n_label="llm.shared.settings.quality.label",
                        description="Choose the output quality.",
                        i18n_description="llm.shared.settings.quality.description",
                        type="select",
                        options=[
                            Option(
                                value="auto",
                                label="Auto",
                                i18n_label="llm.shared.settings.quality.option.auto",
                            ),
                            Option(
                                value="low",
                                label="Low",
                                i18n_label="llm.shared.settings.quality.option.low",
                            ),
                            Option(
                                value="medium",
                                label="Medium",
                                i18n_label="llm.shared.settings.quality.option.medium",
                            ),
                        ],
                        default="auto",
                    ),
                    FieldSchema(
                        key="settings.enable_image_edit",
                        label="Enable Image Edit",
                        i18n_label="llm.shared.settings.enable_image_edit.label",
                        description="Allow xAI Imagine to edit up to five reference images.",
                        i18n_description="schema_xai_image_edit_desc",
                        type="boolean",
                        default=True,
                    ),
                ],
            )
        ]
    )
