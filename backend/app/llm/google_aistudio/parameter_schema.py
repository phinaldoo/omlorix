"""Google AI Studio per-request parameter schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_aistudio_model_schema_parameter": (
        "FieldAttributes",
        "GOOGLE_AISTUDIO_MODEL_SCHEMA_THINKING_SECTION",
        "Sections",
        "_get_field_from_section",
        "_remove_field_from_section",
        "_set_schema_field_value",
        "build_reasoning_effort_options",
        "get_aistudio_model_info",
        "get_parameter_basic_schema",
        "get_parameters_schema_filled",
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
    "FieldAttributes",
    "GOOGLE_AISTUDIO_MODEL_SCHEMA_THINKING_SECTION",
    "Sections",
    "_get_field_from_section",
    "_remove_field_from_section",
    "_set_schema_field_value",
    "build_reasoning_effort_options",
    "get_aistudio_model_info",
    "get_parameter_basic_schema",
    "get_parameters_schema_filled",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_aistudio_model_schema_parameter(db, user_id, model_id, project_id):
    from app.llm.models import get_model

    model = get_model(db, model_id)
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    raw_tools = getattr(model, "tools", []) or []
    tool_names = [tool for tool in raw_tools if isinstance(tool, str)]
    model_info = get_aistudio_model_info(db, model.provider_id, model.model_name)
    model_group_dict = model_info.get("model_group_dict") or {}
    thinking_capabilities = model_group_dict.get("thinking") or {}
    has_existing_thinking_values = any(
        model_settings.get(key) is not None
        for key in (
            "thinking",
            "thinking_budget",
            "thinking_dynamic",
            "reasoning_effort",
            "include_thinking",
        )
    )
    basic_schema = get_parameter_basic_schema(
        db,
        user_id,
        project_id,
        tool_names=tool_names,
        enabled_tools_value=tool_names,
        model_settings=model_settings,
    )
    parameter_schema = get_parameters_schema_filled(model_settings)
    thinking_sections: list[Section] = []
    if thinking_capabilities or has_existing_thinking_values:
        thinking_schema = GOOGLE_AISTUDIO_MODEL_SCHEMA_THINKING_SECTION.model_copy(
            deep=True
        )

        def _maybe_set(field_key: str):
            value = model_settings.get(field_key.split(".")[-1])
            if value is not None:
                _set_schema_field_value(thinking_schema, field_key, value)

        _maybe_set("settings.thinking")
        _maybe_set("settings.thinking_dynamic")
        _maybe_set("settings.thinking_budget")
        _maybe_set("settings.reasoning_effort")
        _maybe_set("settings.include_thinking")

        if thinking_capabilities:
            thinking_enabled = thinking_capabilities.get("thinking")
            thinking_budget_supported = thinking_capabilities.get(
                "thinking_budget_support", False
            )
            reasoning_effort_supported = thinking_capabilities.get(
                "reasoning_effort_support", False
            )
            thinking_dynamic_supported = thinking_capabilities.get(
                "thinking_support_dynamic", False
            )
            thinking_disabled_allowed = thinking_capabilities.get(
                "thinking_disabled_allowed", True
            )
            reasoning_effort_values = (
                thinking_capabilities.get("reasoning_effort") or []
            )

            if thinking_enabled:
                if not thinking_budget_supported:
                    _remove_field_from_section(
                        thinking_schema.sections,
                        "Thinking",
                        "settings.thinking_budget",
                    )
                else:
                    budget_field = _get_field_from_section(
                        thinking_schema.sections,
                        "Thinking",
                        "settings.thinking_budget",
                    )
                    if budget_field:
                        attributes = budget_field.attributes or FieldAttributes()
                        attributes.min = thinking_capabilities.get(
                            "thinking_budget_min"
                        )
                        attributes.max = thinking_capabilities.get(
                            "thinking_budget_max"
                        )
                        budget_field.attributes = attributes

                reasoning_effort_field = _get_field_from_section(
                    thinking_schema.sections,
                    "Thinking",
                    "settings.reasoning_effort",
                )
                if not reasoning_effort_supported:
                    _remove_field_from_section(
                        thinking_schema.sections,
                        "Thinking",
                        "settings.reasoning_effort",
                    )
                elif reasoning_effort_field and reasoning_effort_values:
                    reasoning_effort_field.options = build_reasoning_effort_options(
                        reasoning_effort_values
                    )

                if not (thinking_budget_supported and thinking_dynamic_supported):
                    _remove_field_from_section(
                        thinking_schema.sections,
                        "Thinking",
                        "settings.thinking_dynamic",
                    )
                if not thinking_disabled_allowed:
                    _remove_field_from_section(
                        thinking_schema.sections,
                        "Thinking",
                        "settings.thinking",
                    )
            else:
                if not has_existing_thinking_values:
                    thinking_schema.sections = []

        thinking_sections = thinking_schema.sections or []

    combined_schema = Sections(
        sections=(basic_schema.sections or [])
        + thinking_sections
        + (parameter_schema.sections or [])
    )
    if not model_group_dict.get("support_media_resolution", False):
        _remove_field_from_section(
            combined_schema.sections, "Video settings", "settings.media_resolution"
        )
        _remove_field_from_section(
            combined_schema.sections, "Video settings", "settings.video_fps"
        )
        combined_schema.sections = [
            section
            for section in combined_schema.sections
            if section.title != "Video settings" or (section.fields or [])
        ]
    return combined_schema
