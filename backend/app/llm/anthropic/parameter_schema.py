"""Assembly of per-request Anthropic parameter controls."""

from app.llm.anthropic.generation_schema import get_parameters_schema_filled
from app.llm.anthropic.model_schema import get_anthropic_model_info
from app.llm.anthropic.schema_definitions import ANTHROPIC_MODEL_SCHEMA_THINKING_SECTION
from app.llm.anthropic.thinking import (
    get_anthropic_thinking_capabilities,
    is_anthropic_base_provider_type,
)
from app.llm.model_schemas import get_parameter_basic_schema
from app.llm.reasoning_effort_options import build_reasoning_effort_options
from app.utils.schemas import (
    Section,
    Sections,
    _get_field_from_section,
    _remove_field_from_section,
    _set_schema_field_value,
)


def get_anthropic_model_schema_parameter(
    db,
    user_id,
    model_id,
    project_id,
    anthropic_provider_type: str = "anthropic",
):
    """Get Anthropic model schema parameter."""
    from app.llm.models import get_model

    model = get_model(db, model_id)
    model_settings = model.settings if isinstance(model.settings, dict) else {}
    raw_tools = getattr(model, "tools", []) or []
    tool_names = [tool for tool in raw_tools if isinstance(tool, str)]
    model_info = get_anthropic_model_info(db, model.provider_id, model.model_name)
    model_group_dict = model_info.get("model_group_dict") or {}
    thinking_capabilities = get_anthropic_thinking_capabilities(
        model.model_name,
        model_info=model_group_dict,
        allow_compatible_fallback=is_anthropic_base_provider_type(
            anthropic_provider_type
        ),
    )
    has_existing_thinking_values = any(
        model_settings.get(key) is not None
        for key in (
            "thinking",
            "thinking_budget",
            "reasoning_effort",
            "thinking_adaptive",
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
    if thinking_capabilities.get("thinking") or has_existing_thinking_values:
        thinking_schema = ANTHROPIC_MODEL_SCHEMA_THINKING_SECTION.model_copy(deep=True)

        def _maybe_set(field_key: str):
            value = model_settings.get(field_key.split(".")[-1])
            if value is not None:
                _set_schema_field_value(thinking_schema, field_key, value)

        _maybe_set("settings.thinking")
        _maybe_set("settings.thinking_budget")
        _maybe_set("settings.reasoning_effort")
        _maybe_set("settings.thinking_adaptive")

        if (
            model_settings.get("thinking_adaptive") is None
            and thinking_capabilities.get("thinking")
            and thinking_capabilities.get("thinking_support_adaptive", False)
        ):
            _set_schema_field_value(thinking_schema, "settings.thinking_adaptive", True)

        if model_group_dict:
            thinking_enabled = thinking_capabilities.get("thinking")
            thinking_budget_supported = thinking_capabilities.get(
                "thinking_budget_support", False
            )
            reasoning_effort_supported = thinking_capabilities.get(
                "reasoning_effort_support", False
            )
            thinking_adaptive_supported = thinking_capabilities.get(
                "thinking_support_adaptive", False
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
                        "Thinking & reasoning",
                        "settings.thinking_budget",
                    )
                reasoning_effort_field = _get_field_from_section(
                    thinking_schema.sections,
                    "Thinking & reasoning",
                    "settings.reasoning_effort",
                )
                if not reasoning_effort_supported:
                    _remove_field_from_section(
                        thinking_schema.sections,
                        "Thinking & reasoning",
                        "settings.reasoning_effort",
                    )
                elif reasoning_effort_field and reasoning_effort_values:
                    reasoning_effort_field.options = build_reasoning_effort_options(
                        reasoning_effort_values
                    )
                if not thinking_adaptive_supported:
                    _remove_field_from_section(
                        thinking_schema.sections,
                        "Thinking & reasoning",
                        "settings.thinking_adaptive",
                    )
                if not thinking_disabled_allowed:
                    _remove_field_from_section(
                        thinking_schema.sections,
                        "Thinking & reasoning",
                        "settings.thinking",
                    )
            elif not has_existing_thinking_values:
                thinking_schema.sections = []
        elif not thinking_capabilities.get("thinking_disabled_allowed", True):
            # The API may be temporarily unavailable. Preserve all configurable
            # fields, but keep the known restriction for required-thinking models.
            _remove_field_from_section(
                thinking_schema.sections,
                "Thinking & reasoning",
                "settings.thinking",
            )

        thinking_sections = thinking_schema.sections or []

    combined_schema = Sections(
        sections=(basic_schema.sections or [])
        + thinking_sections
        + (parameter_schema.sections or [])
    )
    return combined_schema
