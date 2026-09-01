from __future__ import annotations

from io import BytesIO
import logging
from typing import Any

from PIL import Image
from google.genai import errors as genai_errors
from google.genai import types

from app.llm.google_aistudio.utils import (
    build_aistudio_generate_content_config,
    get_aistudio_client,
)
from app.llm.models import LLMProvider
from app.llm.google_aistudio.model_list import (
    GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_LAST_UPDATED,
    GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_URL,
    GOOGLE_AISTUDIO_MUSIC_GENERATION_MODELS,
)
from app.utils.schemas import FieldSchema, Option, Section, Sections


logger = logging.getLogger(__name__)


def _normalize_model_id(model_name: str | None) -> str:
    return str(model_name or "").strip().replace("models/", "", 1)


def _resolve_model_definition(model_name: str | None) -> dict[str, Any] | None:
    normalized = _normalize_model_id(model_name)
    if not normalized:
        return None
    for item in GOOGLE_AISTUDIO_MUSIC_GENERATION_MODELS:
        ids = [str(value).strip() for value in item.get("ids", []) if str(value).strip()]
        if normalized in ids:
            return item
    return None


def get_google_aistudio_music_model_pricing(model_name: str | None) -> dict[str, Any] | None:
    item = _resolve_model_definition(model_name) or {}
    pricing = item.get("pricing")
    if not isinstance(pricing, dict):
        return None
    return dict(pricing)


def _format_music_generation_price(pricing: dict[str, Any] | None) -> str:
    if not isinstance(pricing, dict):
        return ""
    request_price = pricing.get("request")
    if request_price is None:
        return ""
    try:
        normalized_price = float(request_price)
    except (TypeError, ValueError):
        return ""
    unit = str(pricing.get("unit") or "song").strip() or "song"
    return f"${normalized_price:.2f}/{unit}"


def calculate_google_aistudio_music_generation_cost(
    model_name: str | None,
    *,
    request_count: int = 1,
) -> dict[str, Any] | None:
    pricing = get_google_aistudio_music_model_pricing(model_name)
    if not pricing:
        return None

    try:
        price_per_request = float(pricing.get("request") or 0.0)
    except (TypeError, ValueError):
        return None

    normalized_request_count = max(int(request_count or 0), 0)
    total_cost = round(price_per_request * normalized_request_count, 10)
    return {
        "cost": total_cost,
        "request_count": normalized_request_count,
        "price_per_request": price_per_request,
        "pricing_model": "per_request",
        "billing_unit": str(pricing.get("unit") or "song").strip() or "song",
        "currency": str(pricing.get("currency") or "USD").strip() or "USD",
        "pricing_display": _format_music_generation_price(pricing),
        "pricing_source_url": str(pricing.get("source_url") or GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_URL).strip(),
        "pricing_last_updated": str(
            pricing.get("source_last_updated") or GOOGLE_AISTUDIO_LYRIA_PRICING_DOCS_LAST_UPDATED
        ).strip(),
    }


def get_google_aistudio_music_model_capabilities(model_name: str | None) -> dict[str, Any]:
    item = _resolve_model_definition(model_name) or {}
    pricing = get_google_aistudio_music_model_pricing(model_name)
    return {
        "response_formats": list(item.get("response_formats") or ["mp3"]),
        "supports_reference_images": bool(item.get("supports_reference_images", False)),
        "max_reference_images": int(item.get("max_reference_images") or 0),
        "duration_label": str(item.get("duration_label") or "").strip(),
        "label": str(item.get("name") or item.get("label") or _normalize_model_id(model_name) or "Music Model").strip(),
        "pricing": pricing,
        "pricing_label": _format_music_generation_price(pricing),
    }


def _build_model_option(item: dict[str, Any], discovered_item: dict[str, Any] | None = None) -> dict[str, Any]:
    ids = [str(value).strip() for value in item.get("ids", []) if str(value).strip()]
    model_id = ids[0] if ids else ""
    fallback_label = str(item.get("name") or item.get("label") or model_id).strip() or model_id
    discovered_label = ""
    if isinstance(discovered_item, dict):
        discovered_label = str(discovered_item.get("name") or discovered_item.get("label") or "").strip()
    duration_label = str(item.get("duration_label") or "").strip()
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else None
    pricing_label = _format_music_generation_price(pricing)
    label = discovered_label or fallback_label
    suffix_parts = [part for part in (duration_label, pricing_label) if part]
    if suffix_parts:
        label = f"{label} ({', '.join(suffix_parts)})"
    return {
        "id": model_id,
        "name": label,
        "metadata": {
            "duration_label": duration_label,
            "pricing": pricing,
            "pricing_label": pricing_label,
        },
    }


def get_google_aistudio_music_generation_models(provider: LLMProvider) -> list[dict[str, Any]]:
    fallback_items = [
        _build_model_option(item)
        for item in GOOGLE_AISTUDIO_MUSIC_GENERATION_MODELS
    ]
    fallback_by_id = {item["id"]: item for item in fallback_items if item.get("id")}

    try:
        client = get_aistudio_client(
            None,
            api_key=provider.api_key,
            api_version=(provider.settings or {}).get("api_version", "v1beta"),
        )
        raw_models = list(client.models.list())
    except Exception:
        return fallback_items

    discovered: dict[str, dict[str, Any]] = {}
    for model in raw_models:
        model_name = str(getattr(model, "name", "") or "").strip()
        model_id = _normalize_model_id(model_name)
        if not model_id.startswith("lyria-3-"):
            continue
        static_definition = _resolve_model_definition(model_id)
        if static_definition:
            discovered[model_id] = _build_model_option(
                static_definition,
                {
                    "name": str(getattr(model, "display_name", "") or "").strip(),
                },
            )
        else:
            discovered[model_id] = {
                "id": model_id,
                "name": str(getattr(model, "display_name", "") or model_id).strip() or model_id,
            }

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in GOOGLE_AISTUDIO_MUSIC_GENERATION_MODELS:
        model_id = str((item.get("ids") or [""])[0]).strip()
        if not model_id:
            continue
        merged_item = discovered.get(model_id) or fallback_by_id.get(model_id)
        if merged_item and model_id not in seen:
            merged.append(merged_item)
            seen.add(model_id)
    for model_id, item in discovered.items():
        if model_id in seen:
            continue
        merged.append(item)
        seen.add(model_id)
    return merged or fallback_items


def get_music_generation_schema_part_1(db, provider_id: str):
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        raise ValueError("Provider not found")

    options = [
        Option(value=item["id"], label=item["name"], metadata=item.get("metadata"))
        for item in get_google_aistudio_music_generation_models(provider)
        if item.get("id")
    ]

    return Sections(
        sections=[
            Section(
                title="Music Generation Models",
                i18n_title="llm.shared.section_music_generation.title",
                description="Select one of the available Lyria 3 music generation models.",
                i18n_description="llm.shared.section_select_one_of_the_available_lyria_3_music_generation_models.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="llm.shared.model_name.label",
                        description="Choose the Lyria 3 model to use for music generation.",
                        i18n_description="llm.shared.model_name.description",
                        type="select",
                        options=options,
                        placeholder="Select a model",
                        i18n_placeholder="llm.shared.model_name.placeholder",
                    )
                ],
            )
        ]
    )


def get_music_generation_schema_part_2(model_name: str):
    capabilities = get_google_aistudio_music_model_capabilities(model_name)
    response_formats = capabilities.get("response_formats") or ["mp3"]
    supports_reference_images = bool(capabilities.get("supports_reference_images"))
    max_reference_images = int(capabilities.get("max_reference_images") or 10)
    model_label = str(capabilities.get("label") or _normalize_model_id(model_name) or "Music Model").strip()
    duration_label = str(capabilities.get("duration_label") or "").strip()
    pricing_label = str(capabilities.get("pricing_label") or "").strip()

    generation_fields = [
        FieldSchema(
            key="response_format",
            label="Output Format",
            description="Choose the audio format returned by the music model.",
            type="select",
            options=[
                Option(value=value, label=value.upper())
                for value in response_formats
            ],
            default=response_formats[0],
            i18n_label="schema_music_generation_response_format",
            i18n_description="schema_music_generation_response_format_desc",
        ),
    ]

    if supports_reference_images:
        generation_fields.extend(
            [
                FieldSchema(
                    key="enable_reference_images",
                    label="Enable Reference Images",
                    description="Allow the music_generation tool to pass recent chat images as visual inspiration for the song.",
                    type="boolean",
                    default=False,
                    i18n_label="schema_music_generation_enable_reference_images",
                    i18n_description="schema_music_generation_enable_reference_images_desc",
                ),
                FieldSchema(
                    key="max_reference_images",
                    label="Max Reference Images",
                    description="Maximum number of recent chat images to pass when reference-image mode is enabled.",
                    type="number",
                    default=min(max_reference_images, 3),
                    attributes={"min": 1, "max": min(max_reference_images, 10)},
                    dependency="enable_reference_images",
                    dependency_value=True,
                    i18n_label="schema_music_generation_max_reference_images",
                    i18n_description="schema_music_generation_max_reference_images_desc",
                ),
            ]
        )

    description = f"Configure defaults for {model_label}."
    if duration_label:
        description = f"{description} Typical output length: {duration_label}."
    if pricing_label:
        description = f"{description} Google list price: {pricing_label}."

    return Sections(
        sections=[
            Section(
                title="Generation Settings",
                description=description,
                i18n_title="schema_music_generation_sec1_title",
                i18n_description="schema_music_generation_sec1_desc",
                fields=generation_fields,
            )
        ]
    )


def _coerce_response_mime_type(response_format: str | None) -> str | None:
    normalized = str(response_format or "").strip().lower()
    if normalized == "wav":
        return "audio/wav"
    return None


def _extract_text_part(part: Any) -> str | None:
    text_value = getattr(part, "text", None)
    if isinstance(text_value, str):
        value = text_value.strip()
        return value or None
    return None


def _extract_inline_bytes(part: Any) -> tuple[bytes | None, str | None]:
    inline_data = getattr(part, "inline_data", None)
    if not inline_data:
        return None, None
    payload = getattr(inline_data, "data", None)
    if payload is None:
        return None, getattr(inline_data, "mime_type", None)
    if isinstance(payload, bytes):
        data = payload
    else:
        try:
            data = bytes(payload)
        except Exception:
            return None, getattr(inline_data, "mime_type", None)
    return data or None, getattr(inline_data, "mime_type", None)


def _iter_response_parts(response: Any) -> list[Any]:
    parts: list[Any] = []
    response_parts = getattr(response, "parts", None)
    if isinstance(response_parts, list) and response_parts:
        parts.extend(response_parts)
    candidates = getattr(response, "candidates", None)
    if isinstance(candidates, list):
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            candidate_parts = getattr(content, "parts", None)
            if isinstance(candidate_parts, list):
                parts.extend(candidate_parts)
    return parts


def _load_reference_images(reference_images: list[dict[str, Any]] | None) -> list[Image.Image]:
    loaded: list[Image.Image] = []
    for item in reference_images or []:
        if not isinstance(item, dict):
            continue
        image_bytes = item.get("bytes")
        if not isinstance(image_bytes, (bytes, bytearray)):
            continue
        try:
            with Image.open(BytesIO(bytes(image_bytes))) as image:
                loaded_image = image.copy()
        except Exception:
            logger.warning("Failed to decode music reference image", exc_info=True)
            continue
        loaded.append(loaded_image)
    return loaded


def generate_music_google_aistudio(
    provider: LLMProvider,
    model_name: str,
    prompt: str,
    *,
    config: dict[str, Any] | None = None,
    reference_images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = config if isinstance(config, dict) else {}
    provider_settings = provider.settings if isinstance(provider.settings, dict) else {}

    client = get_aistudio_client(
        None,
        api_key=provider.api_key,
        api_version=provider_settings.get("api_version", "v1beta"),
    )

    normalized_model = _normalize_model_id(model_name)
    contents: list[Any] = [prompt]
    contents.extend(_load_reference_images(reference_images))

    requested_format = str(settings.get("response_format") or "mp3").strip().lower() or "mp3"
    response_mime_type = _coerce_response_mime_type(requested_format)
    generation_config = build_aistudio_generate_content_config(
        settings,
        response_modalities=["AUDIO", "TEXT"],
        response_mime_type=response_mime_type,
    )

    try:
        response = client.models.generate_content(
            model=normalized_model,
            contents=contents,
            config=generation_config,
        )
    except genai_errors.ClientError as exc:
        message = getattr(exc, "message", str(exc))
        raise RuntimeError(f"Google AI Studio music generation failed: {message}") from exc
    except Exception as exc:
        raise RuntimeError(f"Google AI Studio music generation failed: {exc}") from exc

    text_blocks: list[str] = []
    audio_bytes: bytes | None = None
    audio_mime_type: str | None = None

    for part in _iter_response_parts(response):
        text_value = _extract_text_part(part)
        if text_value:
            text_blocks.append(text_value)
            continue
        inline_bytes, inline_mime_type = _extract_inline_bytes(part)
        if inline_bytes and audio_bytes is None:
            audio_bytes = inline_bytes
            audio_mime_type = str(inline_mime_type or "").strip().lower() or "audio/mpeg"

    if not audio_bytes:
        raise RuntimeError("Google AI Studio music generation did not return audio data")

    if audio_mime_type == "audio/wav":
        extension = "wav"
        response_format = "wav"
    else:
        audio_mime_type = "audio/mpeg"
        extension = "mp3"
        response_format = "mp3"

    cost_info = calculate_google_aistudio_music_generation_cost(
        normalized_model,
        request_count=1,
    )
    cost = 0.0
    cost_details: dict[str, Any] = {}
    if cost_info:
        cost = float(cost_info.get("cost") or 0.0)
        cost_details = {"model": normalized_model, **cost_info}

    return {
        "audio_bytes": audio_bytes,
        "file_type": audio_mime_type,
        "extension": extension,
        "response_format": response_format,
        "text_blocks": text_blocks,
        "text_content": "\n\n".join(text_blocks).strip(),
        "reference_image_count": len(reference_images or []),
        "cost": cost,
        "cost_details": cost_details,
    }
