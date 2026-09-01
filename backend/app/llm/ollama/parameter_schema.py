"""Ollama per-request parameter schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.ollama import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_ollama_model_schema_parameter": (
        "OLLAMA_MODEL_SCHEMA_THINKING_SECTION",
        "Sections",
        "_remove_field_from_section",
        "_set_schema_field_value",
        "apply_model_mcp_schema_values",
        "get_ollama_model_info",
        "get_parameter_basic_schema",
        "get_parameters_schema_filled",
        "ollama_model_supports_reasoning_effort",
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
    "OLLAMA_MODEL_SCHEMA_THINKING_SECTION",
    "Sections",
    "_remove_field_from_section",
    "_set_schema_field_value",
    "apply_model_mcp_schema_values",
    "get_ollama_model_info",
    "get_parameter_basic_schema",
    "get_parameters_schema_filled",
    "ollama_model_supports_reasoning_effort",
    "populate_sections_with_values",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_ollama_model_schema_parameter(db, user_id, model_id, project_id):
    """Get Ollama model schema parameter."""
    from app.llm.models import get_model

    model = get_model(db, model_id)
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    raw_tools = getattr(model, "tools", []) or []
    tool_names = [tool for tool in raw_tools if isinstance(tool, str)]
    model_info = get_ollama_model_info(db, model.provider_id, model.model_name)
    model_capabilities = model_info.get("capabilities") or []
    supports_reasoning_effort = ollama_model_supports_reasoning_effort(model.model_name)
    has_thinking = ("thinking" in model_capabilities) or supports_reasoning_effort

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
    has_existing_reasoning_values = any(
        model_settings.get(key) not in (None, "")
        for key in ("reasoning", "reasoning_effort", "thinking_enabled")
    )
    if has_thinking or has_existing_reasoning_values:
        thinking_schema = OLLAMA_MODEL_SCHEMA_THINKING_SECTION.model_copy(deep=True)
        reasoning_value = model_settings.get("reasoning")
        if reasoning_value is None:
            reasoning_value = model_settings.get("thinking_enabled")
        if reasoning_value is not None:
            _set_schema_field_value(
                thinking_schema, "settings.reasoning", reasoning_value
            )
        reasoning_effort_value = model_settings.get("reasoning_effort")
        if reasoning_effort_value is not None:
            _set_schema_field_value(
                thinking_schema,
                "settings.reasoning_effort",
                reasoning_effort_value,
            )

        if not has_thinking:
            thinking_schema.sections = []
        elif not supports_reasoning_effort:
            _remove_field_from_section(
                thinking_schema.sections,
                "Reasoning",
                "settings.reasoning_effort",
            )

        thinking_sections = thinking_schema.sections or []

    combined_schema = Sections(
        sections=(basic_schema.sections or [])
        + thinking_sections
        + (parameter_schema.sections or [])
    )
    populate_sections_with_values(combined_schema, {"settings": model_settings})
    apply_model_mcp_schema_values(combined_schema, model_settings)
    return combined_schema
