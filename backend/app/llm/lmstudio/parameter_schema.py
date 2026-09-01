"""LM Studio per-request parameter schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.lmstudio import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_lmstudio_model_schema_parameter": (
        "Sections",
        "_build_generation_section",
        "_build_reasoning_schema",
        "_coerce_model_settings",
        "_lmstudio_reasoning_options",
        "apply_model_mcp_schema_values",
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
    "Sections",
    "_build_generation_section",
    "_build_reasoning_schema",
    "_coerce_model_settings",
    "_lmstudio_reasoning_options",
    "apply_model_mcp_schema_values",
    "populate_sections_with_values",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_lmstudio_model_schema_parameter(db, user_id, model_id, project_id):
    from app.llm.models import get_model
    from app.llm.model_schemas import get_parameter_basic_schema
    from app.llm.lmstudio.utils import get_model_info

    model = get_model(db, model_id)
    model_settings = _coerce_model_settings(getattr(model, "settings", None))
    raw_tools = getattr(model, "tools", []) or []
    tool_names = [tool for tool in raw_tools if isinstance(tool, str)]
    basic_schema = get_parameter_basic_schema(
        db,
        user_id,
        project_id,
        tool_names=tool_names,
        enabled_tools_value=tool_names,
        model_settings=model_settings,
    )

    model_info = None
    try:
        model_info = get_model_info(
            db, getattr(model, "model_name", None), getattr(model, "provider_id", None)
        )
    except Exception:
        model_info = None

    combined_schema = Sections(
        sections=(basic_schema.sections or [])
        + _build_reasoning_schema(
            _lmstudio_reasoning_options(model_info), model_settings=model_settings
        )
        + _build_generation_section(model_settings)
    )

    populate_sections_with_values(combined_schema, {"settings": model_settings})
    apply_model_mcp_schema_values(combined_schema, model_settings)
    return combined_schema
