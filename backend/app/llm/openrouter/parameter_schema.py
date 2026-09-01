"""OpenRouter per-request parameter schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openrouter import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_openrouter_model_schema_parameter": (
        "OPENROUTER_THINKING_SECTION_SCHEMA",
        "Sections",
        "_normalize_supported_parameters",
        "_prune_generation_parameters_section",
        "_remove_field_from_section",
        "_remove_section_from_sections",
        "_set_schema_field_value",
        "get_openrouter_parameters_schema",
        "get_parameter_basic_schema",
        "infer_reasoning_mode_from_settings",
        "openrouter_model_parameters",
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
    "OPENROUTER_THINKING_SECTION_SCHEMA",
    "Sections",
    "_normalize_supported_parameters",
    "_prune_generation_parameters_section",
    "_remove_field_from_section",
    "_remove_section_from_sections",
    "_set_schema_field_value",
    "get_openrouter_parameters_schema",
    "get_parameter_basic_schema",
    "infer_reasoning_mode_from_settings",
    "openrouter_model_parameters",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_openrouter_model_schema_parameter(db, user_id, model_id, project_id):
    from app.llm.models import get_model

    model = get_model(db, model_id)
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    raw_tools = getattr(model, "tools", []) or []

    tool_names: list[str] = []
    for tool in raw_tools:
        if isinstance(tool, str):
            tool_names.append(tool)
        elif isinstance(tool, dict):
            name_value = tool.get("name")
            if isinstance(name_value, str):
                tool_names.append(name_value)

    basic_schema = get_parameter_basic_schema(
        db,
        user_id,
        project_id,
        tool_names=tool_names,
        enabled_tools_value=tool_names,
        model_settings=model_settings,
    )
    parameter_schema = get_openrouter_parameters_schema(model_settings)

    supported_parameters = _normalize_supported_parameters(
        model_settings.get("supported_parameters")
    )
    if not supported_parameters:
        try:
            from app.llm.openrouter.utils import get_model_information

            model_info = get_model_information(
                db,
                getattr(model, "model_name", None),
                getattr(model, "provider_id", None),
            )
            if isinstance(model_info, dict):
                supported_parameters = _normalize_supported_parameters(
                    model_info.get("supported_parameters")
                )
        except Exception:
            supported_parameters = set()
    if not supported_parameters:
        supported_parameters = {param.lower() for param in openrouter_model_parameters}

    include_reasoning_section = "reasoning" in supported_parameters
    thinking_sections: list[Section] = []
    if include_reasoning_section:
        thinking_schema = OPENROUTER_THINKING_SECTION_SCHEMA.model_copy(deep=True)
        for key in (
            "reasoning_enabled",
            "reasoning_mode",
            "reasoning_effort",
            "reasoning_max_tokens",
            "reasoning_exclude",
        ):
            if key not in model_settings:
                continue
            value = model_settings.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value:
                continue
            _set_schema_field_value(thinking_schema, f"settings.{key}", value)
        inferred_mode = infer_reasoning_mode_from_settings(model_settings)
        if inferred_mode:
            _set_schema_field_value(
                thinking_schema, "settings.reasoning_mode", inferred_mode
            )
        thinking_sections = thinking_schema.sections or []

    combined_schema = Sections(
        sections=(basic_schema.sections or [])
        + thinking_sections
        + (parameter_schema.sections or [])
    )

    schema_sections = combined_schema.sections

    if "tools" not in supported_parameters:
        _remove_field_from_section(
            schema_sections, "Model Context", "settings.enabled_tools"
        )

    _prune_generation_parameters_section(schema_sections, supported_parameters)

    thinking_section_title = "Reasoning & advanced capabilities"
    if not include_reasoning_section:
        _remove_section_from_sections(schema_sections, thinking_section_title)
    elif "include_reasoning" not in supported_parameters:
        _remove_field_from_section(
            schema_sections, thinking_section_title, "settings.reasoning_exclude"
        )
    return combined_schema
