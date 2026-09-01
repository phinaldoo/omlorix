"""OpenRouter model configuration schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openrouter import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_openrouter_model_schema": (
        "HTTPException",
        "InputFormatEnum",
        "MODEL_SCHEMA_FILE_SECTION",
        "MODEL_SCHEMA_INFORMATION_SECTION",
        "OPENROUTER_MODEL_PROVIDER_SCHEMA",
        "OPENROUTER_THINKING_SECTION_SCHEMA",
        "OutputFormatEnum",
        "_coerce_list",
        "_filter_modalities_schema_options",
        "_get_field_from_section",
        "_normalize_knowledge_cutoff_value",
        "_normalize_modalities_list",
        "_normalize_model_identifier",
        "_normalize_supported_parameters",
        "_prune_generation_parameters_section",
        "_remove_field_from_section",
        "_remove_section_from_sections",
        "_set_schema_field_value",
        "apply_model_mcp_schema_values",
        "combine_model_schema_sections",
        "date",
        "datetime",
        "get_model_schema_access_section",
        "get_model_schema_modalities_section",
        "get_model_schema_skill_section",
        "get_model_schema_title_section",
        "get_model_schema_tools_section",
        "get_openrouter_parameters_schema",
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
    "HTTPException",
    "InputFormatEnum",
    "MODEL_SCHEMA_FILE_SECTION",
    "MODEL_SCHEMA_INFORMATION_SECTION",
    "OPENROUTER_MODEL_PROVIDER_SCHEMA",
    "OPENROUTER_THINKING_SECTION_SCHEMA",
    "OutputFormatEnum",
    "_coerce_list",
    "_filter_modalities_schema_options",
    "_get_field_from_section",
    "_normalize_knowledge_cutoff_value",
    "_normalize_modalities_list",
    "_normalize_model_identifier",
    "_normalize_supported_parameters",
    "_prune_generation_parameters_section",
    "_remove_field_from_section",
    "_remove_section_from_sections",
    "_set_schema_field_value",
    "apply_model_mcp_schema_values",
    "combine_model_schema_sections",
    "date",
    "datetime",
    "get_model_schema_access_section",
    "get_model_schema_modalities_section",
    "get_model_schema_skill_section",
    "get_model_schema_title_section",
    "get_model_schema_tools_section",
    "get_openrouter_parameters_schema",
    "openrouter_model_parameters",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_openrouter_model_schema(
    db,
    provider_id,
    model_name: str | None = None,
    model_id: str | None = None,
    model_provider: str | None = None,
):
    from app.llm.models import get_model
    from app.llm.openrouter.utils import get_model_information_endpoint

    model = None
    if model_id:
        model = get_model(db, model_id)
        model_name = getattr(model, "model_name", None)
        settings = getattr(model, "settings", None)
        provider_from_settings = None
        if isinstance(settings, dict):
            provider_from_settings = settings.get("only_provider") or settings.get(
                "provider"
            )
        elif settings is not None:
            provider_from_settings = getattr(
                settings, "only_provider", None
            ) or getattr(settings, "provider", None)
        if not model_provider:
            model_provider = provider_from_settings

    resolved_model_name = _normalize_model_identifier(model_name)
    if not resolved_model_name:
        raise HTTPException(status_code=422, detail="OpenRouter model_name is required")

    model_info = (
        get_model_information_endpoint(
            db, resolved_model_name, provider_id, model_provider
        )
        or {}
    )
    endpoint_info = model_info.get("endpoint") or {}
    supported_parameters_raw = endpoint_info.get("supported_parameters")
    supported_parameters = _normalize_supported_parameters(supported_parameters_raw)
    if not supported_parameters:
        supported_parameters = {param.lower() for param in openrouter_model_parameters}

    value_model_name = model_name
    value_name = model_info.get("name")
    raw_description = model_info.get("description") or ""
    available_provider_icons = [
        "openai",
        "mistral",
        "openrouter",
        "amazon",
        "nvidia",
        "minimax",
        "qwen",
        "microsoft",
    ]
    if len(raw_description) > 97:
        value_description = raw_description[:97].rstrip() + "..."
    else:
        value_description = raw_description
    raw_provider = model_info.get("author") or "openrouter"
    if raw_provider == "mistralai":
        raw_provider = "mistral"
    elif raw_provider == "google":
        if "gemini" in model_name.lower():
            raw_provider = "gemini"
        elif "gemma" in model_name.lower():
            raw_provider = "gemma"
    elif raw_provider == "anthropic":
        raw_provider = "claude"
    elif raw_provider == "x-ai":
        raw_provider = "grok"
    elif raw_provider == "moonshotai":
        raw_provider = "kimi"
    elif raw_provider == "meta-llama":
        raw_provider = "meta"
    elif raw_provider not in available_provider_icons:
        raw_provider = "omlorix"

    value_icon = raw_provider
    value_status = ""

    value_tools: list = []
    value_access_everyone = False
    value_access_users: list[str] = []
    value_access_groups: list[str] = []

    architecture_block = model_info.get("architecture", {}) or {}
    architecture_input_modalities = architecture_block.get("input_modalities", []) or []
    supported_input_modalities = _normalize_modalities_list(
        architecture_input_modalities
    )

    endpoint_info = model_info.get("endpoint") or {}
    raw_default_knowledge_cutoff = model_info.get("knowledge_cutoff")
    if raw_default_knowledge_cutoff in (None, ""):
        raw_default_knowledge_cutoff = endpoint_info.get("knowledge_cutoff")
    default_knowledge_cutoff = _normalize_knowledge_cutoff_value(
        raw_default_knowledge_cutoff
    )
    input_token_limit = endpoint_info.get("max_prompt_tokens")
    if not input_token_limit:
        input_token_limit = endpoint_info.get("context_length")

    settings_defaults = {
        "input_formats": architecture_input_modalities,
        "output_formats": architecture_block.get("output_modalities", []),
        "input_token_limit": input_token_limit,
        "output_token_limit": endpoint_info.get("max_completion_tokens"),
        "provider_mode": "specific" if model_provider else "auto",
        "only_provider": model_provider,
        "provider_sort": None,
        "allow_fallbacks": False,
    }

    model_settings: dict = {}
    if model:
        value_name = model.name or value_name
        value_description = model.description or value_description
        value_icon = model.model_icon or value_icon
        value_status = model.status or value_status
        value_tools = model.tools or []
        model_access = model.access or {}
        if isinstance(model_access, dict):
            value_access_everyone = bool(model_access.get("everyone"))
            value_access_users = _coerce_list(model_access.get("users"))
            value_access_groups = _coerce_list(model_access.get("groups"))
        else:
            value_access_everyone = bool(getattr(model_access, "everyone", False))
            value_access_users = _coerce_list(getattr(model_access, "users", []))
            value_access_groups = _coerce_list(getattr(model_access, "groups", []))
        model_settings = model.settings if isinstance(model.settings, dict) else {}

    # Combine schema sections
    info_schema = MODEL_SCHEMA_INFORMATION_SECTION.model_copy(deep=True)
    access_schema = get_model_schema_access_section(db)
    title_schema = get_model_schema_title_section(db)
    skill_schema = get_model_schema_skill_section(db)
    modalities_schema = get_model_schema_modalities_section(
        [item.value for item in InputFormatEnum],
        [item.value for item in OutputFormatEnum],
    ).model_copy(deep=True)
    _filter_modalities_schema_options(modalities_schema, supported_input_modalities)
    file_schema = MODEL_SCHEMA_FILE_SECTION.model_copy(deep=True)
    thinking_schema = OPENROUTER_THINKING_SECTION_SCHEMA.model_copy(deep=True)
    tools_schema = get_model_schema_tools_section(db)
    parameter_schema = get_openrouter_parameters_schema(model_settings)

    combined_schema = combine_model_schema_sections(
        info_schema,
        access_schema,
        title_schema,
        skill_schema,
        modalities_schema,
        file_schema,
        thinking_schema,
        tools_schema,
        parameter_schema,
    )

    # Only include provider settings section when a specific provider is set
    if model_provider:
        provider_schema = OPENROUTER_MODEL_PROVIDER_SCHEMA.model_copy(deep=True)

        # Set fallback value
        fallback_field = _get_field_from_section(
            provider_schema.sections,
            "Model Provider Settings",
            "settings.allow_fallbacks",
        )
        if fallback_field:
            fallback_value = model_settings.get("allow_fallbacks")
            if fallback_value is not None:
                fallback_field.value = bool(fallback_value)

        combined_schema = combine_model_schema_sections(
            combined_schema, provider_schema
        )

    _remove_field_from_section(
        combined_schema.sections,
        "Tools & enrichment",
        "settings.native_websearch",
    )

    # Populate base fields
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
        combined_schema, "access.everyone", bool(value_access_everyone)
    )
    if value_access_users:
        _set_schema_field_value(combined_schema, "access.users", value_access_users)
    if value_access_groups:
        _set_schema_field_value(combined_schema, "access.groups", value_access_groups)
    if value_tools:
        _set_schema_field_value(combined_schema, "tools", value_tools)
    apply_model_mcp_schema_values(combined_schema, model_settings)

    _unlimited_fields = {
        "max_image_count",
        "max_video_count",
        "max_audio_count",
        "max_document_count",
        "max_youtube_video_count",
    }

    def _set_setting(key: str, fallback=None):
        value = model_settings.get(key, fallback)
        if key in _unlimited_fields and value == -1:
            return
        if value in (None, ""):
            return
        if isinstance(value, datetime):
            value = value.date().isoformat()
        if isinstance(value, date):
            value = value.isoformat()
        _set_schema_field_value(combined_schema, f"settings.{key}", value)

    # Default settings when not stored yet
    for key, default in settings_defaults.items():
        if key not in model_settings and default not in (None, [], ""):
            _set_schema_field_value(combined_schema, f"settings.{key}", default)

    for field_key in (
        "title_generation",
        "title_generation_model",
        "title_generation_model_id",
        "system_instruction",
        "training_data",
        "allow_custom_generation_parameter",
        "custom_title_generation_instruction",
        "input_formats",
        "output_formats",
        "input_token_limit",
        "output_token_limit",
        "reasoning_enabled",
        "reasoning_mode",
        "reasoning_effort",
        "reasoning_max_tokens",
        "reasoning_exclude",
        "pdf_processing_engine",
        "max_image_count",
        "max_video_count",
        "max_audio_count",
        "max_document_count",
        "native_youtube_video",
        "websearch_scrape_provider",
        "websearch_search_provider",
        "native_websearch",
        "temperature",
        "top_p",
        "top_k",
        "frequency_penalty",
        "presence_penalty",
        "repetition_penalty",
        "min_p",
        "top_a",
        "seed",
        "max_tokens",
        "logit_bias",
        "stop",
        "verbosity",
        "provider_mode",
        "only_provider",
        "provider_sort",
        "allow_fallbacks",
        "skill_id",
    ):
        _set_setting(field_key)

    knowledge_cutoff_value = _normalize_knowledge_cutoff_value(
        model_settings.get("knowledge_cutoff")
    )
    if knowledge_cutoff_value is None:
        knowledge_cutoff_value = default_knowledge_cutoff
    if knowledge_cutoff_value:
        _set_schema_field_value(
            combined_schema, "settings.knowledge_cutoff", knowledge_cutoff_value
        )

    #  ['frequency_penalty', 'response_format', 'structured_outputs', 'temperature', 'tool_choice', 'tools', 'top_k', 'top_p']
    # ['max_tokens', 'response_format', 'seed', 'structured_outputs', 'tool_choice', 'tools'
    # ['include_reasoning', 'max_tokens', 'reasoning', 'response_format', 'seed', 'structured_outputs', 'tool_choice', 'tools']
    # ['frequency_penalty', 'include_reasoning', 'logit_bias', 'max_tokens', 'min_p', 'presence_penalty', 'reasoning', 'repetition_penalty', 'response_format', 'seed', 'stop', 'structured_outputs', 'temperature', 'tool_choice', 'tools', 'top_k', 'top_p']
    schema_sections = combined_schema.sections
    generation_section_title = "Generation parameters"

    if "tools" not in supported_parameters:
        _remove_section_from_sections(schema_sections, "Tools & enrichment")

    _prune_generation_parameters_section(
        schema_sections,
        supported_parameters,
        section_title=generation_section_title,
    )

    thinking_section_title = "Reasoning & advanced capabilities"
    if "reasoning" not in supported_parameters:
        _remove_section_from_sections(schema_sections, thinking_section_title)
    elif "include_reasoning" not in supported_parameters:
        _remove_field_from_section(
            schema_sections, thinking_section_title, "settings.reasoning_exclude"
        )

    if provider_id:
        _remove_field_from_section(
            combined_schema.sections, "Model Information", "model_name"
        )

    return combined_schema
