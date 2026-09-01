"""OpenAI model configuration schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_openai_model_schema": (
        "MODEL_SCHEMA_FILE_SECTION",
        "MODEL_SCHEMA_INFORMATION_SECTION",
        "OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY",
        "OPENAI_REASONING_CONTEXT_SETTING_KEY",
        "OPENAI_REASONING_MODE_SETTING_KEY",
        "OPENAI_THINKING_MODEL_SCHEMA",
        "OPENAI_TOOL_SEARCH_SETTING_KEY",
        "OpenAIInputFormatEnum",
        "OpenAIOutputFormatEnum",
        "XAI_PROVIDER_TYPE",
        "_apply_azure_model_name_copy",
        "_apply_openai_model_caps_to_schema",
        "_get_openai_model_caps",
        "_hide_openai_model_id_field",
        "_openai_reasoning_toggle_supported",
        "_openai_tool_search_supported",
        "_remove_field_from_section",
        "_schema_option_values",
        "_set_schema_field_value",
        "_upsert_openai_tool_search_field",
        "allows_manual_openai_model_entry",
        "apply_model_mcp_schema_values",
        "combine_model_schema_sections",
        "date",
        "datetime",
        "get_model_schema_access_section",
        "get_model_schema_modalities_section",
        "get_model_schema_skill_section",
        "get_model_schema_title_section",
        "get_model_schema_tools_section",
        "get_parameters_schema_filled",
        "is_openai_chat_completions_provider_type",
        "is_openai_custom_base_url_provider_type",
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
    "OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY",
    "OPENAI_REASONING_CONTEXT_SETTING_KEY",
    "OPENAI_REASONING_MODE_SETTING_KEY",
    "OPENAI_THINKING_MODEL_SCHEMA",
    "OPENAI_TOOL_SEARCH_SETTING_KEY",
    "OpenAIInputFormatEnum",
    "OpenAIOutputFormatEnum",
    "XAI_PROVIDER_TYPE",
    "_apply_azure_model_name_copy",
    "_apply_openai_model_caps_to_schema",
    "_get_openai_model_caps",
    "_hide_openai_model_id_field",
    "_openai_reasoning_toggle_supported",
    "_openai_tool_search_supported",
    "_remove_field_from_section",
    "_schema_option_values",
    "_set_schema_field_value",
    "_upsert_openai_tool_search_field",
    "allows_manual_openai_model_entry",
    "apply_model_mcp_schema_values",
    "combine_model_schema_sections",
    "date",
    "datetime",
    "get_model_schema_access_section",
    "get_model_schema_modalities_section",
    "get_model_schema_skill_section",
    "get_model_schema_title_section",
    "get_model_schema_tools_section",
    "get_parameters_schema_filled",
    "is_openai_chat_completions_provider_type",
    "is_openai_custom_base_url_provider_type",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_openai_model_schema(
    db,
    provider_id,
    model_name: str | None = None,
    model_id: str | None = None,
    openai_provider_type: str | None = None,
):
    """Get OpenAI model schema."""
    from app.llm.models import get_model

    def _ensure_list(value):
        if not value:
            return []
        if isinstance(value, list):
            return value
        return [value]

    model = None
    if model_id:
        model = get_model(db, model_id)
        model_name = model.model_name

    value_model_name = model_name or ""
    value_name = ""
    value_description = ""
    value_icon = "openai"
    value_tools: list[str] = []
    value_access_everyone = False
    value_access_users: list[str] = []
    value_access_groups: list[str] = []
    value_status = ""
    model_settings: dict[str, Any] = {}

    value_websearch_scrape_provider = ""
    value_websearch_search_provider = ""
    value_native_websearch = False

    value_title_generation = False
    value_title_generation_model = "current"
    value_title_generation_model_id = ""
    value_custom_title_generation_instruction = ""
    value_system_instruction = ""
    value_knowledge_cutoff: str | None = None
    value_training_data = "unknown"
    value_allow_custom_generation_parameter = False
    value_input_formats: list[str] = [OpenAIInputFormatEnum.text.value]
    value_output_formats: list[str] = [OpenAIOutputFormatEnum.text.value]
    value_input_token_limit: int | None = 0
    value_output_token_limit: int | None = 0
    value_max_image_count: int | None = None
    value_max_video_count: int | None = None
    value_max_audio_count: int | None = None
    value_max_document_count: int | None = None
    value_max_youtube_video_count: int | None = None
    value_native_youtube_video = False
    value_reasoning: bool | None = None
    value_reasoning_effort: str | None = None
    value_reasoning_summary: str | None = None
    value_reasoning_mode = "standard"
    value_reasoning_context = "auto"
    value_prompt_cache_override = not is_openai_custom_base_url_provider_type(
        openai_provider_type
    )
    value_prompt_cache_ttl = "30m"
    value_prompt_cache_key = ""
    value_priority_processing = "standard"
    value_tool_search = False

    resolved_model_identifier = ""
    if model:
        value_model_name = model.model_name or value_model_name
        value_name = model.name
        value_description = model.description
        value_icon = model.model_icon or value_icon
        value_tools = model.tools or []
        model_access = model.access or {}
        if isinstance(model_access, dict):
            value_access_everyone = bool(model_access.get("everyone"))
            value_access_users = _ensure_list(model_access.get("users"))
            value_access_groups = _ensure_list(model_access.get("groups"))
        else:
            value_access_everyone = bool(getattr(model_access, "everyone", False))
            value_access_users = _ensure_list(getattr(model_access, "users", []))
            value_access_groups = _ensure_list(getattr(model_access, "groups", []))
        value_status = getattr(model, "status", "")
        model_settings = model.settings if isinstance(model.settings, dict) else {}
        resolved_model_identifier = model.model_name or ""
    else:
        value_model_name = model_name
        resolved_model_identifier = model_name or ""

    model_caps = _get_openai_model_caps(
        resolved_model_identifier,
        openai_provider_type=openai_provider_type,
    )

    if not model and model_caps:
        cap_name = model_caps.get("name")
        cap_description = model_caps.get("description")
        if cap_name and not value_name:
            value_name = cap_name
        if cap_description and not value_description:
            value_description = cap_description
        cap_input_formats = model_caps.get("input_formats")
        if cap_input_formats:
            value_input_formats = list(cap_input_formats)
        cap_output_formats = model_caps.get("output_formats")
        if cap_output_formats:
            value_output_formats = list(cap_output_formats)
        cap_input_limit = model_caps.get("input_token_limit")
        if cap_input_limit is not None:
            value_input_token_limit = cap_input_limit
        cap_output_limit = model_caps.get("output_token_limit")
        if cap_output_limit is not None:
            value_output_token_limit = cap_output_limit
        cap_knowledge_cutoff = model_caps.get("knowledge_cutoff")
        if cap_knowledge_cutoff and not value_knowledge_cutoff:
            if isinstance(cap_knowledge_cutoff, datetime):
                value_knowledge_cutoff = cap_knowledge_cutoff.date().isoformat()
            elif isinstance(cap_knowledge_cutoff, date):
                value_knowledge_cutoff = cap_knowledge_cutoff.isoformat()
            else:
                value_knowledge_cutoff = str(cap_knowledge_cutoff)

    value_title_generation = bool(
        model_settings.get("title_generation", value_title_generation)
    )
    value_title_generation_model = (
        model_settings.get("title_generation_model") or value_title_generation_model
    )
    value_title_generation_model_id = (
        model_settings.get("title_generation_model_id")
        or value_title_generation_model_id
    )
    value_custom_title_generation_instruction = (
        model_settings.get("custom_title_generation_instruction")
        or value_custom_title_generation_instruction
    )
    value_system_instruction = (
        model_settings.get("system_instruction") or value_system_instruction
    )
    value_training_data = model_settings.get("training_data") or value_training_data
    value_allow_custom_generation_parameter = bool(
        model_settings.get(
            "allow_custom_generation_parameter", value_allow_custom_generation_parameter
        )
    )
    value_input_formats = (
        _ensure_list(model_settings.get("input_formats")) or value_input_formats
    )
    value_output_formats = (
        _ensure_list(model_settings.get("output_formats")) or value_output_formats
    )
    value_input_token_limit = model_settings.get(
        "input_token_limit", value_input_token_limit
    )
    value_output_token_limit = model_settings.get(
        "output_token_limit", value_output_token_limit
    )
    value_max_image_count = model_settings.get("max_image_count", value_max_image_count)
    value_max_video_count = model_settings.get("max_video_count", value_max_video_count)
    value_max_audio_count = model_settings.get("max_audio_count", value_max_audio_count)
    value_websearch_scrape_provider = model_settings.get(
        "websearch_scrape_provider", value_websearch_scrape_provider
    )
    value_websearch_search_provider = model_settings.get(
        "websearch_search_provider", value_websearch_search_provider
    )
    value_native_websearch = model_settings.get(
        "native_websearch", value_native_websearch
    )
    value_max_document_count = model_settings.get(
        "max_document_count", value_max_document_count
    )
    value_max_youtube_video_count = model_settings.get(
        "max_youtube_video_count", value_max_youtube_video_count
    )
    value_native_youtube_video = bool(
        model_settings.get("native_youtube_video", value_native_youtube_video)
    )
    if is_openai_chat_completions_provider_type(openai_provider_type):
        value_reasoning = model_settings.get("reasoning", value_reasoning)
    value_reasoning_effort = model_settings.get(
        "reasoning_effort", value_reasoning_effort
    )
    value_reasoning_summary = model_settings.get(
        "reasoning_summary", value_reasoning_summary
    )
    value_reasoning_mode = model_settings.get("reasoning_mode") or value_reasoning_mode
    value_reasoning_context = (
        model_settings.get("reasoning_context") or value_reasoning_context
    )
    value_prompt_cache_override = bool(
        model_settings.get("prompt_cache_override", value_prompt_cache_override)
    )
    value_prompt_cache_ttl = (
        model_settings.get("prompt_cache_ttl") or value_prompt_cache_ttl
    )
    value_prompt_cache_key = (
        model_settings.get("prompt_cache_key") or value_prompt_cache_key
    )
    value_priority_processing = (
        model_settings.get("priority_processing") or value_priority_processing
    )
    value_tool_search = bool(model_settings.get("tool_search", value_tool_search))
    if "knowledge_cutoff" in model_settings:
        value_knowledge_cutoff = (
            model_settings.get("knowledge_cutoff") or value_knowledge_cutoff
        )

    info_schema = MODEL_SCHEMA_INFORMATION_SECTION.model_copy(deep=True)
    access_schema = get_model_schema_access_section(db)
    title_schema = get_model_schema_title_section(db)
    skill_schema = get_model_schema_skill_section(db)
    input_format_options = [item.value for item in OpenAIInputFormatEnum]
    output_format_options = [item.value for item in OpenAIOutputFormatEnum]
    modalities_schema = get_model_schema_modalities_section(
        input_format_options,
        output_format_options,
    ).model_copy(deep=True)
    file_schema = MODEL_SCHEMA_FILE_SECTION.model_copy(deep=True)
    tools_schema = get_model_schema_tools_section(db)
    parameter_settings = dict(model_settings or {})
    parameter_settings.setdefault("store", True)
    parameter_settings.setdefault("send_user_identifier", False)
    parameter_schema = get_parameters_schema_filled(
        parameter_settings,
        resolved_model_identifier,
        openai_provider_type=openai_provider_type,
    )
    thinking_schema = OPENAI_THINKING_MODEL_SCHEMA.model_copy(deep=True)

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

    if _openai_tool_search_supported(
        model_caps, openai_provider_type=openai_provider_type
    ):
        _upsert_openai_tool_search_field(
            combined_schema,
            section_title="Tools & enrichment",
            dependency_key="tools",
            dependency_values=_schema_option_values(
                combined_schema, "Tools & enrichment", "tools"
            ),
        )

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
        combined_schema, "access.everyone", bool(value_access_everyone)
    )
    if value_access_users:
        _set_schema_field_value(combined_schema, "access.users", value_access_users)
    if value_access_groups:
        _set_schema_field_value(combined_schema, "access.groups", value_access_groups)

    if value_tools:
        _set_schema_field_value(combined_schema, "tools", value_tools)
    apply_model_mcp_schema_values(combined_schema, model_settings)

    if value_title_generation:
        _set_schema_field_value(
            combined_schema, "settings.title_generation", value_title_generation
        )
    if value_title_generation_model:
        _set_schema_field_value(
            combined_schema,
            "settings.title_generation_model",
            value_title_generation_model,
        )
    if value_title_generation_model_id:
        _set_schema_field_value(
            combined_schema,
            "settings.title_generation_model_id",
            value_title_generation_model_id,
        )
    if value_custom_title_generation_instruction:
        _set_schema_field_value(
            combined_schema,
            "settings.custom_title_generation_instruction",
            value_custom_title_generation_instruction,
        )
    if value_system_instruction:
        _set_schema_field_value(
            combined_schema, "settings.system_instruction", value_system_instruction
        )
    if value_knowledge_cutoff:
        if isinstance(value_knowledge_cutoff, datetime):
            value_knowledge_cutoff = value_knowledge_cutoff.date().isoformat()
        elif isinstance(value_knowledge_cutoff, date):
            value_knowledge_cutoff = value_knowledge_cutoff.isoformat()
        _set_schema_field_value(
            combined_schema, "settings.knowledge_cutoff", value_knowledge_cutoff
        )
    if value_training_data:
        _set_schema_field_value(
            combined_schema, "settings.training_data", value_training_data
        )
    if value_allow_custom_generation_parameter:
        _set_schema_field_value(
            combined_schema,
            "settings.allow_custom_generation_parameter",
            value_allow_custom_generation_parameter,
        )
    if value_input_formats:
        _set_schema_field_value(
            combined_schema, "settings.input_formats", value_input_formats
        )
    if value_output_formats:
        _set_schema_field_value(
            combined_schema, "settings.output_formats", value_output_formats
        )
    if value_input_token_limit is not None:
        _set_schema_field_value(
            combined_schema, "settings.input_token_limit", value_input_token_limit
        )
    if value_output_token_limit is not None:
        _set_schema_field_value(
            combined_schema, "settings.output_token_limit", value_output_token_limit
        )
    if value_max_image_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_image_count", value_max_image_count
        )
    if value_max_video_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_video_count", value_max_video_count
        )
    if value_max_audio_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_audio_count", value_max_audio_count
        )
    if value_max_document_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_document_count", value_max_document_count
        )
    if value_websearch_scrape_provider:
        _set_schema_field_value(
            combined_schema,
            "settings.websearch_scrape_provider",
            value_websearch_scrape_provider,
        )
    if value_websearch_search_provider:
        _set_schema_field_value(
            combined_schema,
            "settings.websearch_search_provider",
            value_websearch_search_provider,
        )
    if value_native_websearch:
        _set_schema_field_value(
            combined_schema, "settings.native_websearch", value_native_websearch
        )
    if value_tool_search:
        _set_schema_field_value(
            combined_schema, OPENAI_TOOL_SEARCH_SETTING_KEY, value_tool_search
        )
    reasoning_toggle_supported = _openai_reasoning_toggle_supported(
        model_caps,
        openai_provider_type=openai_provider_type,
    )

    if reasoning_toggle_supported and value_reasoning is not None:
        _set_schema_field_value(combined_schema, "settings.reasoning", value_reasoning)
    if value_reasoning_effort:
        _set_schema_field_value(
            combined_schema, "settings.reasoning_effort", value_reasoning_effort
        )
    if value_reasoning_summary is not None:
        _set_schema_field_value(
            combined_schema, "settings.reasoning_summary", value_reasoning_summary
        )
    _set_schema_field_value(
        combined_schema, OPENAI_REASONING_MODE_SETTING_KEY, value_reasoning_mode
    )
    _set_schema_field_value(
        combined_schema, OPENAI_REASONING_CONTEXT_SETTING_KEY, value_reasoning_context
    )
    _set_schema_field_value(
        combined_schema,
        OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY,
        value_prompt_cache_override,
    )
    _set_schema_field_value(
        combined_schema, "settings.prompt_cache_ttl", value_prompt_cache_ttl
    )
    if value_prompt_cache_key:
        _set_schema_field_value(
            combined_schema, "settings.prompt_cache_key", value_prompt_cache_key
        )
    if value_priority_processing:
        _set_schema_field_value(
            combined_schema, "settings.priority_processing", value_priority_processing
        )

    # Fixed Skill ID
    value_skill_id = model_settings.get("skill_id")
    if value_skill_id:
        _set_schema_field_value(combined_schema, "settings.skill_id", value_skill_id)

    _apply_openai_model_caps_to_schema(
        combined_schema,
        model_caps,
        openai_provider_type=openai_provider_type,
    )

    if allows_manual_openai_model_entry(openai_provider_type):
        # xAI supports Responses storage and priority processing even though
        # its provider also permits manual model IDs.
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

    # Remove file attachment fields that OpenAI does not support.
    unsupported_attachment_fields = [
        "settings.native_youtube_video",
        "settings.max_video_count",
        "settings.max_audio_count",
        "settings.max_youtube_video_count",
        "settings.pdf_processing_engine",
    ]
    for field_key in unsupported_attachment_fields:
        _remove_field_from_section(
            combined_schema.sections, "File attachments", field_key
        )

    if _hide_openai_model_id_field(provider_id, openai_provider_type):
        _remove_field_from_section(
            combined_schema.sections, "Model Information", "model_name"
        )

    _apply_azure_model_name_copy(combined_schema, openai_provider_type)
    return combined_schema
