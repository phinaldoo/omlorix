"""OpenAI per-request parameter schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_openai_model_schema_parameter": (
        "FieldSchema",
        "OPENAI_THINKING_MODEL_SCHEMA",
        "Sections",
        "XAI_PROVIDER_TYPE",
        "_apply_azure_model_name_copy",
        "_apply_openai_model_caps_to_schema",
        "_build_openai_reasoning_effort_options",
        "_build_settings_payload",
        "_get_field_from_section",
        "_get_openai_model_caps",
        "_openai_reasoning_toggle_supported",
        "_openai_tool_search_supported",
        "_remove_field_from_section",
        "_schema_option_values",
        "_set_schema_field_value",
        "_upsert_openai_tool_search_field",
        "allows_manual_openai_model_entry",
        "get_parameter_basic_schema",
        "get_parameters_schema_filled",
        "is_openai_chat_completions_provider_type",
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
    "FieldSchema",
    "OPENAI_THINKING_MODEL_SCHEMA",
    "Sections",
    "XAI_PROVIDER_TYPE",
    "_apply_azure_model_name_copy",
    "_apply_openai_model_caps_to_schema",
    "_build_openai_reasoning_effort_options",
    "_build_settings_payload",
    "_get_field_from_section",
    "_get_openai_model_caps",
    "_openai_reasoning_toggle_supported",
    "_openai_tool_search_supported",
    "_remove_field_from_section",
    "_schema_option_values",
    "_set_schema_field_value",
    "_upsert_openai_tool_search_field",
    "allows_manual_openai_model_entry",
    "get_parameter_basic_schema",
    "get_parameters_schema_filled",
    "is_openai_chat_completions_provider_type",
    "populate_sections_with_values",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_openai_model_schema_parameter(
    db,
    user_id,
    model_id,
    project_id,
    openai_provider_type: str = "openai",
):
    """Get OpenAI model schema parameter."""
    from app.llm.models import get_model

    model = get_model(db, model_id)
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    raw_tools = getattr(model, "tools", []) or []
    tool_names = [tool for tool in raw_tools if isinstance(tool, str)]
    native_websearch_value = model_settings.get("native_websearch")
    if isinstance(native_websearch_value, str):
        native_websearch_enabled = native_websearch_value.strip().lower() in {
            "true",
            "1",
            "yes",
            "on",
        }
    else:
        native_websearch_enabled = native_websearch_value is True
    model_caps = _get_openai_model_caps(
        getattr(model, "model_name", None),
        openai_provider_type=openai_provider_type,
    )

    basic_schema = get_parameter_basic_schema(
        db,
        user_id,
        project_id,
        tool_names=tool_names,
        enabled_tools_value=tool_names,
        model_settings=model_settings,
    )
    model_name = getattr(model, "model_name", None)
    parameter_schema = get_parameters_schema_filled(
        model_settings,
        model_name,
        openai_provider_type=openai_provider_type,
    )
    if is_openai_chat_completions_provider_type(openai_provider_type):
        generation_section = next(
            (
                section
                for section in parameter_schema.sections or []
                if section.title == "Generation parameters"
            ),
            None,
        )
        if generation_section is not None:
            generation_section.fields.append(
                FieldSchema(
                    key="settings.logit_bias",
                    label="Logit bias",
                    description=(
                        "JSON object mapping token IDs to numeric bias values "
                        "for Chat Completions."
                    ),
                    type="string",
                    input_type="dict[str,float]",
                    required=False,
                    placeholder='{"123": -1.5, "456": 2}',
                    value=model_settings.get("logit_bias"),
                )
            )

    thinking_sections: list[Section] = []
    thinking_caps = model_caps.get("thinking") if model_caps else {}
    reasoning_supported = bool(thinking_caps.get("thinking"))
    has_existing_reasoning_values = any(
        model_settings.get(field) not in (None, "")
        for field in ("reasoning", "reasoning_effort", "reasoning_summary")
    )
    if reasoning_supported or has_existing_reasoning_values:
        thinking_schema = OPENAI_THINKING_MODEL_SCHEMA.model_copy(deep=True)
        effort_options = (
            _build_openai_reasoning_effort_options(thinking_caps)
            if thinking_caps
            else None
        )
        if effort_options is not None:
            effort_field = _get_field_from_section(
                thinking_schema.sections,
                "Reasoning & advanced capabilities",
                "settings.reasoning_effort",
            )
            if effort_field:
                if effort_options:
                    effort_field.options = effort_options
                else:
                    _remove_field_from_section(
                        thinking_schema.sections,
                        "Reasoning & advanced capabilities",
                        "settings.reasoning_effort",
                    )
        reasoning_effort_value = model_settings.get("reasoning_effort")
        if reasoning_effort_value is not None:
            _set_schema_field_value(
                thinking_schema,
                "settings.reasoning_effort",
                reasoning_effort_value,
            )
        reasoning_summary_value = model_settings.get("reasoning_summary")
        if reasoning_summary_value is not None:
            _set_schema_field_value(
                thinking_schema,
                "settings.reasoning_summary",
                reasoning_summary_value,
            )
        reasoning_value = model_settings.get("reasoning")
        if reasoning_value is not None:
            _set_schema_field_value(
                thinking_schema,
                "settings.reasoning",
                reasoning_value,
            )
        thinking_sections = thinking_schema.sections or []

    combined_schema = Sections(
        sections=(basic_schema.sections or [])
        + thinking_sections
        + (parameter_schema.sections or [])
    )
    if _openai_tool_search_supported(
        model_caps, openai_provider_type=openai_provider_type
    ):
        _upsert_openai_tool_search_field(
            combined_schema,
            section_title="Model Context",
            dependency_key="settings.enabled_tools",
            dependency_values=_schema_option_values(
                combined_schema, "Model Context", "settings.enabled_tools"
            ),
        )
    _apply_openai_model_caps_to_schema(
        combined_schema, model_caps, openai_provider_type=openai_provider_type
    )
    populate_sections_with_values(
        combined_schema, _build_settings_payload(model_settings)
    )

    if allows_manual_openai_model_entry(openai_provider_type):
        unsupported_fields = (
            ("settings.send_user_identifier",)
            if openai_provider_type == XAI_PROVIDER_TYPE
            else (
                "settings.priority_processing",
                "settings.store",
                "settings.send_user_identifier",
            )
        )
        for field_key in unsupported_fields:
            _remove_field_from_section(
                combined_schema.sections,
                "Generation parameters",
                field_key,
            )

    reasoning_toggle_supported = _openai_reasoning_toggle_supported(
        model_caps,
        openai_provider_type=openai_provider_type,
    )

    if is_openai_chat_completions_provider_type(openai_provider_type):
        _remove_field_from_section(
            combined_schema.sections,
            "Reasoning & advanced capabilities",
            "settings.reasoning_summary",
        )
    elif not reasoning_toggle_supported:
        _remove_field_from_section(
            combined_schema.sections,
            "Reasoning & advanced capabilities",
            "settings.reasoning",
        )

    _apply_azure_model_name_copy(combined_schema, openai_provider_type)
    return combined_schema
