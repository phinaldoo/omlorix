from io import BytesIO
from pathlib import Path
import os
import tempfile
import time

from google.genai import types

from app.llm.google_aistudio.utils import (
    AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS,
    build_aistudio_generate_content_config,
    get_aistudio_client,
    wait_for_aistudio_file_active,
)
from app.utils.schemas import FieldSchema, Option, Section, Sections
from app.llm.google_aistudio.model_list import IMAGE_GEN_MODELS


DEFAULT_API_VERSION = "v1beta"





def _normalize_model_name(model: str) -> str:
    model = model or ""
    return model.replace("models/", "", 1)


def _find_model_definition(model: str) -> dict | None:
    normalized = _normalize_model_name(model)
    for entry in IMAGE_GEN_MODELS:
        ids = entry.get("ids") or []
        if normalized in ids:
            return entry
    return None


def _to_png_bytes_from_pil(pil_image) -> bytes:
    buffer = BytesIO()
    pil_image.save(buffer, format="PNG")
    return buffer.getvalue()


def _extract_bytes_from_inline_part(part) -> bytes | None:
    inline_data = getattr(part, "inline_data", None)
    if inline_data and getattr(inline_data, "data", None):
        data = inline_data.data
        if isinstance(data, bytes):
            return data
        return bytes(data)
    if hasattr(part, "as_image"):
        try:
            pil_image = part.as_image()
            if pil_image:
                return _to_png_bytes_from_pil(pil_image)
        except Exception:
            return None
    return None


def _normalize_reference_image_mime(mime_type: str | None) -> str:
    value = str(mime_type or "").strip().lower()
    if value.startswith("image/"):
        return value
    return "image/png"


def _mime_to_suffix(mime_type: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(mime_type, ".png")


def google_model_supports_image_edit(model_name: str) -> bool:
    entry = _find_model_definition(model_name)
    if not entry:
        return False
    return str(entry.get("category", "model")).strip().lower() == "model"


def generate_image_google_aistudio(
    api_key: str,
    model: str,
    prompt: str,
    *,
    settings: dict | None = None,
    api_version: str | None = None,
) -> bytes:
    settings = settings or {}
    client = get_aistudio_client(
        None,
        api_key=api_key,
        api_version=api_version or DEFAULT_API_VERSION,
    )
    model_info = _find_model_definition(model)
    category = (model_info or {}).get("category", "model")
    model_name = model if model.startswith("models/") else f"models/{model}"

    if category == "imagen":
        config_kwargs = {"number_of_images": 1}
        image_size = settings.get("imageSize") or settings.get("resolution")
        if image_size:
            config_kwargs["imageSize"] = image_size
        aspect_ratio = settings.get("aspectRatio") or settings.get("aspect_ratio")
        if aspect_ratio:
            config_kwargs["aspectRatio"] = aspect_ratio

        response = client.models.generate_images(
            model=model_name,
            prompt=prompt,
            config=types.GenerateImagesConfig(**config_kwargs),
        )
        for generated_image in getattr(response, "generated_images", []) or []:
            image_obj = getattr(generated_image, "image", None)
            if image_obj:
                data = getattr(image_obj, "image_bytes", None) or getattr(image_obj, "data", None)
                if data:
                    return data if isinstance(data, bytes) else bytes(data)
                if hasattr(image_obj, "as_pil_image"):
                    try:
                        return _to_png_bytes_from_pil(image_obj.as_pil_image())
                    except Exception:
                        pass
            inline_data = getattr(generated_image, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                data = inline_data.data
                return data if isinstance(data, bytes) else bytes(data)
        raise RuntimeError("Imagen response did not include image data")

    image_config_kwargs = {}
    aspect_ratio = settings.get("aspect_ratio") or settings.get("aspectRatio")
    if aspect_ratio:
        image_config_kwargs["aspect_ratio"] = aspect_ratio
    resolution = settings.get("resolution") or settings.get("imageSize")
    if resolution:
        image_config_kwargs["image_size"] = resolution

    config = None
    if image_config_kwargs:
        config = build_aistudio_generate_content_config(
            settings,
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(**image_config_kwargs),
        )
    else:
        config = build_aistudio_generate_content_config(settings, response_modalities=["IMAGE"])

    response = client.models.generate_content(
        model=model_name,
        contents=[prompt],
        config=config,
    )

    candidates = getattr(response, "candidates", []) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None)
        if not parts:
            continue
        for part in parts:
            data = _extract_bytes_from_inline_part(part)
            if data:
                return data

    parts = getattr(response, "parts", None) or []
    for part in parts:
        data = _extract_bytes_from_inline_part(part)
        if data:
            return data

    raise RuntimeError("Google AI Studio did not return image data")


def edit_image_google_aistudio(
    api_key: str,
    model: str,
    prompt: str,
    *,
    reference_images: list[dict | bytes],
    settings: dict | None = None,
    api_version: str | None = None,
) -> bytes:
    if not reference_images:
        raise ValueError("reference_images is required for image edit")

    if not google_model_supports_image_edit(model):
        raise ValueError("The selected Google image model does not support image edit")

    settings = settings or {}
    client = get_aistudio_client(
        None,
        api_key=api_key,
        api_version=api_version or DEFAULT_API_VERSION,
    )
    model_name = model if model.startswith("models/") else f"models/{model}"

    temp_paths: list[str] = []
    uploaded_cleanup: list[str] = []
    reference_parts: list[types.Part] = []

    file_active_deadline_monotonic = time.monotonic() + AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS

    try:
        for index, reference_image in enumerate(reference_images):
            image_bytes = reference_image
            mime_type = "image/png"
            if isinstance(reference_image, dict):
                image_bytes = reference_image.get("bytes")
                mime_type = _normalize_reference_image_mime(reference_image.get("mime_type"))
            if not isinstance(image_bytes, (bytes, bytearray)):
                continue

            suffix = _mime_to_suffix(mime_type)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(bytes(image_bytes))
                temp_path = temp_file.name
            temp_paths.append(temp_path)

            uploaded = client.files.upload(file=temp_path)
            uploaded_name = getattr(uploaded, "name", None)
            if uploaded_name:
                uploaded_cleanup.append(uploaded_name)
            uploaded = wait_for_aistudio_file_active(
                client,
                uploaded,
                deadline_monotonic=file_active_deadline_monotonic,
            )
            file_uri = getattr(uploaded, "uri", None)
            if not file_uri:
                continue
            reference_parts.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))

        if not reference_parts:
            raise ValueError("No valid reference images were provided for image edit")

        image_config_kwargs = {}
        aspect_ratio = settings.get("aspect_ratio") or settings.get("aspectRatio")
        if aspect_ratio:
            image_config_kwargs["aspect_ratio"] = aspect_ratio
        resolution = settings.get("resolution") or settings.get("imageSize")
        if resolution:
            image_config_kwargs["image_size"] = resolution

        if image_config_kwargs:
            config = build_aistudio_generate_content_config(
                settings,
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(**image_config_kwargs),
            )
        else:
            config = build_aistudio_generate_content_config(settings, response_modalities=["IMAGE"])

        response = client.models.generate_content(
            model=model_name,
            contents=[prompt, *reference_parts],
            config=config,
        )

        candidates = getattr(response, "candidates", []) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None)
            if not parts:
                continue
            for part in parts:
                data = _extract_bytes_from_inline_part(part)
                if data:
                    return data

        parts = getattr(response, "parts", None) or []
        for part in parts:
            data = _extract_bytes_from_inline_part(part)
            if data:
                return data

        raise RuntimeError("Google AI Studio did not return edited image data")
    finally:
        for uploaded_name in uploaded_cleanup:
            try:
                client.files.delete(name=uploaded_name)
            except Exception:
                pass
        for temp_path in temp_paths:
            try:
                if temp_path and Path(temp_path).exists():
                    os.remove(temp_path)
            except Exception:
                pass


def get_image_generation_schema_part_1(db, provider_id: str | None = None):
    model_options: list[Option] = []

    if not provider_id:
        return Sections(
            sections=[
                Section(
                    title="Google AI Studio Models",
                    i18n_title="llm.shared.section_google_ai_studio.title",
                    description="Select which Google AI Studio image model to use.",
                    i18n_description="llm.shared.section_select_which_google_model.description",
                    fields=[
                        FieldSchema(
                            key="model_name",
                            label="Model",
                            i18n_label="llm.shared.model_name.label",
                            description="Choose the image generation model.",
                            i18n_description="llm.shared.model_name.description",
                            type="select",
                            options=[],
                            placeholder="Select a model",
                            i18n_placeholder="llm.shared.model_name.placeholder",
                        )
                    ],
                )
            ]
        )

    client = get_aistudio_client(db, provider_id)
    models = client.models.list()

    for model in models:
        if "image" in model.name:
            model_id = str(model.name or "").strip()
            if not model_id:
                continue
            display_name = str(model.display_name or "").strip()
            label = (
                f"{display_name} ({model_id})"
                if display_name and display_name.lower() != model_id.lower()
                else model_id
            )
            model_options.append(Option(value=model_id, label=label))
    model_options.sort(key=lambda option: option.label.lower())

    return Sections(
        sections=[
            Section(
                title="Google AI Studio Models",
                i18n_title="llm.shared.section_google_ai_studio.title",
                description="Select which Google AI Studio image model to use.",
                i18n_description="llm.shared.section_select_which_google_model.description",
                fields=[
                    FieldSchema(
                        key="model_name",
                        label="Model",
                        i18n_label="llm.shared.model_name.label",
                        description="Choose the image generation model.",
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


def get_image_generation_schema_part_2(model_name: str):
    entry = _find_model_definition(model_name)
    if not entry:
        return Sections(sections=[])

    fields: list[FieldSchema] = []
    category = entry.get("category", "model")
    if category == "model":
        resolutions = entry.get("resolution") or []
        if resolutions:
            fields.append(
                FieldSchema(
                    key="settings.resolution",
                    label="Resolution",
                    i18n_label="llm.shared.settings.resolution.label",
                    description="Choose the output resolution.",
                    i18n_description="llm.shared.settings.resolution.description",
                    type="select",
                    options=[Option(value=value, label=value) for value in resolutions],
                )
            )
    else:
        image_sizes = entry.get("supported_imageSize") or []
        if entry.get("support_imageSize") and image_sizes:
            fields.append(
                FieldSchema(
                    key="settings.imageSize",
                    label="Image Size",
                    i18n_label="llm.shared.settings.imageSize.label",
                    description="Select the desired image size.",
                    i18n_description="llm.shared.settings.imageSize.description",
                    type="select",
                    options=[Option(value=value, label=value) for value in image_sizes],
                )
            )

    if google_model_supports_image_edit(model_name):
        fields.append(
            FieldSchema(
                key="settings.enable_image_edit",
                label="Enable Image Edit",
                i18n_label="llm.shared.settings.enable_image_edit.label",
                description=(
                    "Allow this model to use Gemini image-edit mode with reference "
                    "images from the chat."
                ),
                type="boolean",
                default=True,
            )
        )

    if not fields:
        return Sections(sections=[])

    return Sections(
        sections=[
            Section(
                title="Model Settings",
                i18n_title="llm.shared.section_model_settings.title",
                description="Configure options for the selected model.",
                i18n_description="llm.shared.section_configure_options.description",
                fields=fields,
            )
        ]
    )
