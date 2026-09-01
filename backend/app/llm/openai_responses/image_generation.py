import base64
from io import BytesIO

from app.llm.models import LLMProvider
from app.llm.openai.custom_headers import custom_headers_to_dict
from app.utils.schemas import (
    FieldSchema,
    Option,
    Section,
    Sections,
)
from openai import Client

OPENAI_COMPATIBLE_QUALITY_OFF = "off"


def _normalize_openai_compatible_quality(quality: str | None) -> str | None:
    """Return a request-safe quality value or ``None`` for the Off state."""
    normalized = str(quality or "").strip()
    if not normalized or normalized.lower() == OPENAI_COMPATIBLE_QUALITY_OFF:
        return None
    return normalized


def _resolve_openai_compatible_image_bytes(response) -> bytes:
    data = getattr(response, "data", None) or []
    first = data[0] if data else None
    image_payload = getattr(first, "b64_json", None) if first else None
    if not image_payload:
        raise RuntimeError("Provider did not return image data")
    return base64.b64decode(image_payload)


def generate_image_openai_responses(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    size: str | None = None,
    quality: str | None = None,
    extra_body: dict | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
):
    """Generate an image through an OpenAI-compatible Images endpoint.

    Compatibility providers do not publish model capability metadata for the
    ``quality`` field. The admin therefore offers every known OpenAI quality
    value, while the explicit Off state leaves the field out so providers with
    a narrower request schema continue to work.
    """
    client_kwargs = {
        "base_url": base_url,
        "api_key": api_key,
    }
    default_headers = custom_headers_to_dict(custom_headers)
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    client = Client(**client_kwargs)
    payload = {
        "model": model,
        "prompt": prompt,
        "response_format": "b64_json",
    }
    if size:
        payload["size"] = size
    normalized_quality = _normalize_openai_compatible_quality(quality)
    if normalized_quality:
        payload["quality"] = normalized_quality
    if isinstance(extra_body, dict) and extra_body:
        payload["extra_body"] = extra_body

    response = client.images.generate(
        **payload,
    )
    return _resolve_openai_compatible_image_bytes(response)


def edit_image_openai_responses(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    reference_images: list[dict | bytes],
    size: str = "1024x1024",
    quality: str | None = None,
    extra_body: dict | None = None,
    custom_headers: dict[str, str] | list[str] | None = None,
) -> bytes:
    if not reference_images:
        raise ValueError("reference_images is required for image edit")

    image_streams: list[BytesIO] = []
    for index, reference_image in enumerate(reference_images):
        image_bytes = reference_image
        image_name = f"reference_{index + 1}.png"
        if isinstance(reference_image, dict):
            image_bytes = reference_image.get("bytes")
            candidate_name = str(reference_image.get("filename") or "").strip()
            if candidate_name:
                image_name = candidate_name
        if not isinstance(image_bytes, (bytes, bytearray)):
            continue
        stream = BytesIO(bytes(image_bytes))
        stream.name = image_name
        image_streams.append(stream)

    if not image_streams:
        raise ValueError("No valid reference images were provided for image edit")

    client_kwargs = {"base_url": base_url, "api_key": api_key}
    default_headers = custom_headers_to_dict(custom_headers)
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    client = Client(**client_kwargs)
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "image": image_streams[0] if len(image_streams) == 1 else image_streams,
        "response_format": "b64_json",
    }
    normalized_quality = _normalize_openai_compatible_quality(quality)
    if normalized_quality:
        payload["quality"] = normalized_quality
    if isinstance(extra_body, dict) and extra_body:
        payload["extra_body"] = extra_body

    try:
        response = client.images.edit(**payload)
    finally:
        for stream in image_streams:
            try:
                stream.close()
            except Exception:
                pass
    return _resolve_openai_compatible_image_bytes(response)



def list_image_models(
    base_url: str,
    api_key: str,
    custom_headers: dict[str, str] | list[str] | None = None,
):
    client_kwargs = {"base_url": base_url, "api_key": api_key}
    default_headers = custom_headers_to_dict(custom_headers)
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    client = Client(**client_kwargs)
    models = client.models.list()
    result_models = []
    for model in models:
        result_models.append({"id": model.id})
    return result_models



def get_image_generation_schema_part_1(db, provider_id: str):
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    model_options: list[Option] = []
    provider_settings = provider.settings if provider and isinstance(provider.settings, dict) else {}
    if provider and provider_settings.get("base_url"):
        try:
            models = list_image_models(
                provider_settings["base_url"],
                provider.api_key or "",
                custom_headers=provider_settings.get("custom_headers"),
            )
            for m in models:
                model_id = m.get("id") if isinstance(m, dict) else m
                if not model_id:
                    continue
                model_options.append(
                    Option(
                        value=model_id,
                        label=model_id,
                    )
                )
        except Exception:
            model_options = []

    schema = Sections(
        sections=[
            Section(
                title="OpenAI Image Generation",
                i18n_title="llm.shared.section_openai_image_generation.title",
                description="",
                i18n_description="llm.shared.section_openai_image_generation.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="llm.shared.model_name.label",
                        description="Choose which model to use for this provider.",
                        i18n_description="llm.shared.model_name.description",
                        type="select",
                        options=model_options,
                        placeholder="Select a model",
                        i18n_placeholder="llm.shared.model_name.placeholder",
                    ),
                ]
            )
        ]
    )
    return schema


def get_image_generation_schema_part_2():
    """Return model settings shared by OpenAI-compatible image providers."""
    fields = [
        FieldSchema(
            key="settings.quality",
            label="Quality",
            i18n_label="llm.shared.settings.quality.label",
            description=(
                "Choose a quality value to send to the provider, or Off to omit "
                "the quality field from the request."
            ),
            i18n_description="llm.shared.settings.quality.compatible_description",
            type="select",
            options=[
                # ``off`` is a visible, persistable select value. Both request
                # adapters normalize it to omission before serializing payloads.
                Option(
                    value=OPENAI_COMPATIBLE_QUALITY_OFF,
                    label="Off",
                    i18n_label="llm.shared.option.off",
                ),
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
                Option(
                    value="high",
                    label="High",
                    i18n_label="llm.shared.settings.quality.option.high",
                ),
                Option(
                    value="standard",
                    label="Standard",
                    i18n_label="llm.shared.settings.quality.option.standard",
                ),
                Option(
                    value="hd",
                    label="HD",
                    i18n_label="llm.shared.settings.quality.option.hd",
                ),
            ],
            default=OPENAI_COMPATIBLE_QUALITY_OFF,
        ),
        FieldSchema(
            key="settings.extra_body",
            label="Extra Body",
            i18n_label="llm.shared.settings.extra_body.label",
            description="Additional provider-specific JSON payload for image generation/edit requests.",
            i18n_description="llm.shared.settings.extra_body.description",
            type="string",
            placeholder='{"key":"value"}',
            i18n_placeholder="llm.shared.settings.extra_body.placeholder",
            default="",
        ),
        FieldSchema(
            key="settings.enable_image_edit",
            label="Enable Image Edit",
            i18n_label="llm.shared.settings.enable_image_edit.label",
            description=(
                "Allow image-edit mode with reference images for this OpenAI-compatible "
                "provider model."
            ),
            type="boolean",
            default=True,
        ),
    ]
    schema = Sections(
        sections=[
            Section(
                title="OpenAI Image Generation",
                i18n_title="llm.shared.section_openai_image_generation.title",
                description="",
                i18n_description="llm.shared.section_openai_image_generation.description",
                fields=fields,
            )
        ]
    )
    return schema
