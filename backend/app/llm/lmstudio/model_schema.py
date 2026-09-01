"""LM Studio model configuration schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.lmstudio import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_lmstudio_model_schema": (
        "MODEL_SCHEMA_FILE_SECTION",
        "MODEL_SCHEMA_INFORMATION_SECTION",
        "_apply_model_caps_to_schema",
        "_build_generation_section",
        "_build_reasoning_schema",
        "_coerce_model_settings",
        "_lmstudio_modalities_for_model",
        "_lmstudio_reasoning_options",
        "_remove_field_from_section",
        "_set_schema_field_value",
        "apply_model_mcp_schema_values",
        "combine_model_schema_sections",
        "get_model_schema_access_section",
        "get_model_schema_modalities_section",
        "get_model_schema_skill_section",
        "get_model_schema_title_section",
        "get_model_schema_tools_section",
        "populate_sections_with_values",
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
    "MODEL_SCHEMA_FILE_SECTION",
    "MODEL_SCHEMA_INFORMATION_SECTION",
    "_apply_model_caps_to_schema",
    "_build_generation_section",
    "_build_reasoning_schema",
    "_coerce_model_settings",
    "_lmstudio_modalities_for_model",
    "_lmstudio_reasoning_options",
    "_remove_field_from_section",
    "_set_schema_field_value",
    "apply_model_mcp_schema_values",
    "combine_model_schema_sections",
    "get_model_schema_access_section",
    "get_model_schema_modalities_section",
    "get_model_schema_skill_section",
    "get_model_schema_title_section",
    "get_model_schema_tools_section",
    "populate_sections_with_values",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_lmstudio_model_schema(
    db, provider_id, model_name: str | None = None, model_id: str | None = None
):
    from app.llm.models import get_model
    from app.llm.lmstudio.utils import get_model_info

    model_settings: dict[str, Any] = {}
    access_payload = {"everyone": False, "users": [], "groups": []}
    value_name = None
    value_description = None
    value_icon = None
    value_model_name = model_name
    value_status = "normal"

    if model_id:
        model = get_model(db, model_id)
        model_settings = _coerce_model_settings(getattr(model, "settings", None))
        access_payload = (
            getattr(model, "access", None)
            if isinstance(getattr(model, "access", None), dict)
            else access_payload
        )
        value_name = getattr(model, "name", None)
        value_description = getattr(model, "description", None)
        value_icon = getattr(model, "model_icon", None)
        value_model_name = getattr(model, "model_name", None) or value_model_name
        value_status = getattr(model, "status", None) or value_status

    model_info = None
    if value_model_name:
        try:
            model_info = get_model_info(db, value_model_name, provider_id)
        except Exception:
            model_info = None

    info_schema = MODEL_SCHEMA_INFORMATION_SECTION.model_copy(deep=True)
    access_schema = get_model_schema_access_section(db)
    title_schema = get_model_schema_title_section(db)
    skill_schema = get_model_schema_skill_section(db)
    input_formats, output_formats = _lmstudio_modalities_for_model(model_info)
    modalities_schema = get_model_schema_modalities_section(
        input_formats, output_formats
    ).model_copy(deep=True)
    file_schema = MODEL_SCHEMA_FILE_SECTION.model_copy(deep=True)
    tools_schema = get_model_schema_tools_section(db)

    reasoning_options = _lmstudio_reasoning_options(model_info)
    combined_schema = combine_model_schema_sections(
        info_schema,
        access_schema,
        title_schema,
        skill_schema,
        modalities_schema,
        file_schema,
        _build_reasoning_schema(reasoning_options, model_settings=model_settings),
        tools_schema,
        _build_generation_section(model_settings),
    )

    _apply_model_caps_to_schema(combined_schema, model_info)

    if value_model_name:
        _set_schema_field_value(combined_schema, "model_name", value_model_name)
    if value_name:
        _set_schema_field_value(combined_schema, "name", value_name)
    if value_description:
        _set_schema_field_value(combined_schema, "description", value_description)
    if value_icon:
        _set_schema_field_value(combined_schema, "model_icon", value_icon)
    if value_status:
        _set_schema_field_value(combined_schema, "status", value_status)

    _set_schema_field_value(
        combined_schema, "access.everyone", bool(access_payload.get("everyone"))
    )
    if access_payload.get("users"):
        _set_schema_field_value(
            combined_schema, "access.users", access_payload.get("users")
        )
    if access_payload.get("groups"):
        _set_schema_field_value(
            combined_schema, "access.groups", access_payload.get("groups")
        )

    populate_sections_with_values(combined_schema, {"settings": model_settings})
    apply_model_mcp_schema_values(combined_schema, model_settings)

    # LM Studio does not support these OpenAI-hosted concepts.
    for field_key in (
        "settings.native_youtube_video",
        "settings.max_video_count",
        "settings.max_audio_count",
        "settings.max_youtube_video_count",
        "settings.pdf_processing_engine",
    ):
        _remove_field_from_section(
            combined_schema.sections, "File attachments", field_key
        )

    return combined_schema
