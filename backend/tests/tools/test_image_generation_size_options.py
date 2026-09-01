import sys
from pathlib import Path
from types import ModuleType

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "openai" not in sys.modules:
    fake_openai = ModuleType("openai")
    fake_openai.Client = lambda *args, **kwargs: None
    sys.modules["openai"] = fake_openai


from app.tools.image_generation.size_options import (
    ASSISTANT_SIZE_SELECTION_KEY,
    OLLAMA_IMAGE_DIMENSION_MAX,
    OLLAMA_IMAGE_DIMENSION_MIN,
    build_assistant_size_selection_fields,
    build_allowed_tool_sizes_field,
    build_fixed_image_size_fields,
    get_assistant_size_selection_kind,
    get_effective_tool_size_values,
    get_supported_tool_size_values,
    validate_image_generation_settings_size,
    validate_requested_ollama_dimensions,
    validate_requested_tool_size,
)


def test_openai_supported_tool_sizes_include_high_resolution_options():
    sizes = get_supported_tool_size_values("openai", "gpt-image-2")

    assert sizes == [
        "1024x1024",
        "1536x1024",
        "1024x1536",
        "2560x1440",
        "3840x2160",
        "auto",
    ]


def test_google_supported_tool_sizes_return_aspect_ratios():
    sizes = get_supported_tool_size_values("google_aistudio", "gemini-2.5-flash-image")

    assert "1:1" in sizes
    assert "16:9" in sizes
    assert "21:9" in sizes


def test_effective_tool_sizes_are_filtered_by_allowed_subset():
    sizes = get_effective_tool_size_values(
        "openai",
        "gpt-image-2",
        {"allowed_sizes": ["1024x1024", "3840x2160", "not-valid"]},
    )

    assert sizes == ["1024x1024", "3840x2160"]


def test_effective_tool_sizes_can_be_disabled_entirely():
    sizes = get_effective_tool_size_values(
        "openai",
        "gpt-image-2",
        {"allowed_sizes": []},
    )

    assert sizes == []


def test_effective_tool_sizes_are_hidden_when_assistant_selection_is_disabled():
    sizes = get_effective_tool_size_values(
        "openai",
        "gpt-image-2",
        {
            ASSISTANT_SIZE_SELECTION_KEY: False,
            "allowed_sizes": ["1024x1024", "3840x2160"],
        },
    )

    assert sizes == []


def test_validate_requested_tool_size_rejects_disabled_size():
    with pytest.raises(ValueError, match="disabled in admin settings"):
        validate_requested_tool_size(
            "openai",
            "gpt-image-2",
            {"allowed_sizes": ["1024x1024"]},
            "3840x2160",
        )


def test_validate_requested_tool_size_rejects_when_assistant_selection_is_off():
    with pytest.raises(ValueError, match="selection is disabled"):
        validate_requested_tool_size(
            "openai",
            "gpt-image-2",
            {ASSISTANT_SIZE_SELECTION_KEY: False},
            "1024x1024",
        )


def test_configured_default_does_not_have_to_be_in_assistant_allowlist():
    validate_image_generation_settings_size(
        "google_aistudio",
        "gemini-2.5-flash-image",
        {"allowed_sizes": ["1:1"], "aspect_ratio": "16:9"},
    )


def test_build_allowed_tool_sizes_field_defaults_to_all_supported_values():
    field = build_allowed_tool_sizes_field("openai", "gpt-image-2")

    assert field is not None
    assert field.key == "settings.allowed_sizes"
    assert field.multiple is True
    assert field.default == get_supported_tool_size_values("openai", "gpt-image-2")
    assert field.i18n_label == "image_generation_allowed_tool_sizes_label"
    assert field.i18n_description == "image_generation_allowed_tool_sizes_description"
    assert field.dependency == f"settings.{ASSISTANT_SIZE_SELECTION_KEY}"
    assert field.dependency_value is True


def test_build_allowed_tool_sizes_field_uses_aspect_ratio_copy_for_xai():
    field = build_allowed_tool_sizes_field("xai", "grok-imagine-image")

    assert field is not None
    assert field.i18n_label == "image_generation_allowed_tool_aspect_ratios_label"
    assert field.i18n_description == "image_generation_allowed_tool_aspect_ratios_description"


@pytest.mark.parametrize(
    ("provider_type", "model_name", "expected_kind"),
    [
        ("openai", "gpt-image-2", "size"),
        ("openai_responses", "gpt-image-2", "size"),
        ("openai_chat_completions", "gpt-image-2", "size"),
        ("google_aistudio", "gemini-3.1-flash-image", "aspect_ratio"),
        ("xai", "grok-imagine-image", "aspect_ratio"),
        ("openrouter", "google/gemini-3.1-flash-image", "aspect_ratio"),
        ("ollama", "x/flux2-klein", "dimensions"),
        ("openai_responses", "unknown-compatible-model", None),
    ],
)
def test_assistant_size_selection_kind_matches_provider_capability(
    provider_type,
    model_name,
    expected_kind,
):
    assert get_assistant_size_selection_kind(provider_type, model_name) == expected_kind


def test_assistant_size_fields_put_allowlist_behind_toggle():
    fields = build_assistant_size_selection_fields("xai", "grok-imagine-image")

    assert [field.key for field in fields] == [
        f"settings.{ASSISTANT_SIZE_SELECTION_KEY}",
        "settings.aspect_ratio",
        "settings.allowed_sizes",
    ]
    assert fields[0].type == "boolean"
    assert fields[0].default is True
    assert fields[1].multiple is not True
    assert fields[1].dependency == fields[0].key
    assert fields[1].dependency_value is False
    assert fields[2].multiple is True
    assert fields[2].dependency == fields[0].key
    assert fields[2].dependency_value is True


def test_fixed_openai_size_field_uses_supported_options():
    fields = build_fixed_image_size_fields("openai", "gpt-image-2")

    assert len(fields) == 1
    assert fields[0].key == "settings.size"
    assert fields[0].default == "auto"
    assert [option.value for option in fields[0].options] == get_supported_tool_size_values(
        "openai", "gpt-image-2"
    )
    assert fields[0].dependency_value is False


def test_google_fixed_aspect_ratio_defaults_to_square():
    fields = build_fixed_image_size_fields(
        "google_aistudio", "gemini-2.5-flash-image"
    )

    assert len(fields) == 1
    assert fields[0].key == "settings.aspect_ratio"
    assert fields[0].default == "1:1"


def test_ollama_assistant_size_fields_include_fixed_dimensions():
    fields = build_assistant_size_selection_fields("ollama", "x/flux2-klein")

    assert [field.key for field in fields] == [
        f"settings.{ASSISTANT_SIZE_SELECTION_KEY}",
        "settings.width",
        "settings.height",
    ]
    assert fields[1].default == 1024
    assert fields[2].default == 1024
    assert fields[1].dependency_value is False
    assert fields[2].dependency_value is False
    assert fields[1].attributes.min == OLLAMA_IMAGE_DIMENSION_MIN
    assert fields[1].attributes.max == OLLAMA_IMAGE_DIMENSION_MAX


def test_validate_requested_ollama_dimensions_accepts_safe_values():
    width, height = validate_requested_ollama_dimensions("1024", 768.0)

    assert width == 1024
    assert height == 768


def test_validate_requested_ollama_dimensions_rejects_oversized_values():
    message = f"between {OLLAMA_IMAGE_DIMENSION_MIN} and {OLLAMA_IMAGE_DIMENSION_MAX}"

    with pytest.raises(ValueError, match=message):
        validate_requested_ollama_dimensions(100000, 1024)


def test_validate_requested_ollama_dimensions_rejects_non_positive_values():
    message = f"between {OLLAMA_IMAGE_DIMENSION_MIN} and {OLLAMA_IMAGE_DIMENSION_MAX}"

    with pytest.raises(ValueError, match=message):
        validate_requested_ollama_dimensions(1024, 0)
