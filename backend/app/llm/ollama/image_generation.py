"""Helpers for Ollama image generation.

Image generation is exposed through Ollama's `/api/generate` endpoint when the
selected model supports image modalities. The API streams NDJSON updates and the
final object includes a base64 encoded `image` field. We request a non-streaming
response, but still tolerate NDJSON payloads by scanning the response body for
the final event.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Iterable

import requests
from fastapi import HTTPException

from app.llm.models import LLMProvider
from app.llm.ollama.utils import get_model_capabilities, list_models_all
from app.tools.image_generation.size_options import (
    OLLAMA_IMAGE_DIMENSION_MAX,
    OLLAMA_IMAGE_DIMENSION_MIN,
)
from app.utils.schemas import FieldSchema, Option, Section, Sections


logger = logging.getLogger(__name__)


def _coerce_positive_int(value) -> int | None:
    """Convert user settings into positive ints, ignoring invalid values."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        coerced = int(value)
    elif isinstance(value, str) and value.strip():
        try:
            coerced = int(float(value.strip()))
        except ValueError:
            return None
    else:
        return None
    if coerced <= 0:
        return None
    return coerced


def _coerce_ollama_dimension(value) -> int | None:
    """Convert settings into safe Ollama image dimensions."""

    coerced = _coerce_positive_int(value)
    if coerced is None:
        return None
    if coerced < OLLAMA_IMAGE_DIMENSION_MIN or coerced > OLLAMA_IMAGE_DIMENSION_MAX:
        return None
    return coerced


def _build_payload(prompt: str, model: str, settings: dict | None) -> dict:
    """Build payload for image generation."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    settings = settings or {}
    width = _coerce_ollama_dimension(settings.get("width"))
    height = _coerce_ollama_dimension(settings.get("height"))
    steps = _coerce_positive_int(settings.get("steps"))
    if width:
        payload["width"] = width
    if height:
        payload["height"] = height
    if steps:
        payload["steps"] = steps
    return payload


def _decode_image_payload(image_payload: str | None) -> bytes | None:
    """Decode image payload."""
    if not image_payload or not isinstance(image_payload, str):
        return None
    content = image_payload
    if image_payload.startswith("data:"):
        _, _, content = image_payload.partition(",")
    try:
        return base64.b64decode(content)
    except (ValueError, TypeError):
        return None


def _parse_generate_response(response: requests.Response) -> dict:
    """Parse JSON or NDJSON payloads, returning the last object."""

    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass

    text = response.text or ""
    final_obj: dict | None = None
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        final_obj = parsed
        if isinstance(parsed, dict) and parsed.get("done"):
            break
    if not isinstance(final_obj, dict):
        raise RuntimeError("Ollama did not return a valid JSON payload for image generation")
    return final_obj


def generate_image_ollama(
    provider: LLMProvider,
    model: str,
    prompt: str,
    settings: dict | None = None,
) -> dict:
    """Generate an image via Ollama's `/api/generate` endpoint."""

    if not provider:
        raise ValueError("provider is required for Ollama image generation")

    provider_settings = provider.settings or {}
    if not isinstance(provider_settings, dict):
        raise ValueError("Ollama provider settings must be a dictionary")

    base_url = provider_settings.get("base_url")
    if not base_url:
        raise ValueError("Ollama provider base_url is not configured")

    url = f"{base_url.rstrip('/')}/api/generate"
    payload = _build_payload(prompt, model, settings)
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = provider.api_key

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=120)
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to reach Ollama server: {exc}") from exc

    if response.status_code >= 400:
        snippet = response.text[:500]
        raise RuntimeError(
            "Ollama image generation failed: "
            f"{response.status_code} {snippet}"
        )

    result_obj = _parse_generate_response(response)
    image_bytes = None

    primary_image = result_obj.get("image")
    if isinstance(primary_image, str):
        image_bytes = _decode_image_payload(primary_image)

    if not image_bytes:
        images = result_obj.get("images")
        if isinstance(images, Iterable):
            for entry in images:
                decoded = _decode_image_payload(entry)
                if decoded:
                    image_bytes = decoded
                    break

    if not image_bytes:
        raise RuntimeError("Ollama response did not include image data")

    return {
        "image_bytes": image_bytes,
        "cost": 0.0,
        "cost_details": {
            "provider": "ollama",
            "model": result_obj.get("model", model),
        },
    }


def _model_supports_image_generation(capabilities: list | None) -> bool:
    """Check if model supports image generation."""
    if not capabilities:
        return True  # fall back to showing the model if capabilities are unknown
    normalized = {str(cap).lower() for cap in capabilities}
    return bool({"image", "images", "vision"} & normalized)


def get_image_generation_schema_part_1(db, provider_id: str | None = None):
    """Get image generation schema part 1."""
    model_options: list[Option] = []
    if not provider_id:
        return Sections(sections=[])

    try:
        models = list_models_all(db, ollama_provider_id=provider_id)
    except HTTPException:
        models = []
    except Exception:
        logger.exception("Failed to fetch Ollama model list for image generation")
        models = []

    for entry in models:
        model_id = None
        if isinstance(entry, dict):
            model_id = entry.get("id") or entry.get("model")
        if not model_id:
            continue
        try:
            capabilities = get_model_capabilities(db, model_id, ollama_provider_id=provider_id)
        except Exception:
            capabilities = []
        if not _model_supports_image_generation(capabilities):
            continue
        model_options.append(Option(value=model_id, label=model_id))

    return Sections(
        sections=[
            Section(
                title="Ollama Image Models",
                i18n_title="llm.shared.section_ollama_image_models.title",
                description="Select which Ollama model to use for image generation.",
                i18n_description="llm.shared.section_select_which_ollama.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="llm.shared.model_name.label",
                        description="Choose an Ollama model that supports image outputs.",
                        i18n_description="llm.shared.model_name.description",
                        type="select",
                        options=model_options,
                        placeholder="Select a model",
                        i18n_placeholder="llm.shared.model_name.placeholder",
                    )
                ],
            )
        ]
    )


def get_image_generation_schema_part_2():
    """Get image generation schema part 2."""
    return Sections(
        sections=[
            Section(
                title="Ollama Image Settings",
                i18n_title="llm.shared.section_ollama_image_settings.title",
                description="Optional experimental parameters exposed by the Ollama API.",
                i18n_description="llm.shared.section_optional_experimental.description",
                fields=[
                    FieldSchema(
                        key="settings.steps",
                        label="Steps",
                        i18n_label="llm.shared.settings.steps.label",
                        description="Number of diffusion steps (experimental).",
                        i18n_description="llm.shared.settings.steps.description",
                        type="string",
                        input_type="int",
                        placeholder="e.g. 20",
                        i18n_placeholder="llm.shared.settings.steps.placeholder",
                    ),
                ],
            )
        ]
    )
