"""Google AI Studio model configuration schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_aistudio_model_schema": (
        "FieldAttributes",
        "GOOGLE_AISTUDIO_MODEL_SCHEMA_THINKING_SECTION",
        "InputFormatEnum",
        "MODEL_SCHEMA_FILE_SECTION",
        "MODEL_SCHEMA_INFORMATION_SECTION",
        "OutputFormatEnum",
        "_get_field_from_section",
        "_remove_field_from_section",
        "_set_schema_field_value",
        "apply_model_mcp_schema_values",
        "build_reasoning_effort_options",
        "combine_model_schema_sections",
        "datetime",
        "get_aistudio_model_info",
        "get_model_schema_access_section",
        "get_model_schema_modalities_section",
        "get_model_schema_skill_section",
        "get_model_schema_title_section",
        "get_model_schema_tools_section",
        "get_parameters_schema_filled",
        "normalize_aistudio_model_description",
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
    "InputFormatEnum",
    "MODEL_SCHEMA_FILE_SECTION",
    "MODEL_SCHEMA_INFORMATION_SECTION",
    "OutputFormatEnum",
    "_get_field_from_section",
    "_remove_field_from_section",
    "_set_schema_field_value",
    "apply_model_mcp_schema_values",
    "build_reasoning_effort_options",
    "combine_model_schema_sections",
    "datetime",
    "get_aistudio_model_info",
    "get_model_schema_access_section",
    "get_model_schema_modalities_section",
    "get_model_schema_skill_section",
    "get_model_schema_title_section",
    "get_model_schema_tools_section",
    "get_parameters_schema_filled",
    "normalize_aistudio_model_description",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_aistudio_model_schema(
    db, provider_id, model_name: str | None = None, model_id: str | None = None
):
    from app.llm.models import get_model

    model = None
    if model_id:
        model = get_model(db, model_id)
        model_name = model.model_name
    model_info = get_aistudio_model_info(db, provider_id, model_name)
    model_group_dict = model_info.get("model_group_dict") or {}
    supports_native_websearch = bool(model_group_dict.get("supports_native_websearch"))
    value_model_name = ""
    value_name = ""
    value_description = ""
    value_icon = ""
    value_tools = []
    value_websearch_scrape_provider = None
    value_websearch_search_provider = None
    value_native_websearch = False

    value_access_everyone = False
    value_access_users = []
    value_access_groups = []

    value_status = ""

    model_settings = {}

    value_title_generation = False
    value_title_generation_model = ""
    value_title_generation_model_id = ""

    value_system_instruction = ""
    value_knowledge_cutoff = None
    value_training_data = None
    value_allow_custom_generation_parameter = False

    value_input_formats = []
    value_output_formats = []
    value_input_token_limit = 0
    value_output_token_limit = 0

    value_thinking = False
    value_thinking_budget = None
    value_thinking_dynamic = False
    value_reasoning_effort = None
    value_include_thinking = None

    value_max_image_count = -1
    value_max_video_count = -1
    value_max_audio_count = -1
    value_max_document_count = -1
    value_native_youtube_video = False
    value_max_youtube_video_count = -1

    value_skill_id = None

    # Get values. if already in db saved model use the model values, if not, use default values
    if model_id:
        value_model_name = model.model_name
        value_name = model.name
        value_description = normalize_aistudio_model_description(model.description)
        value_icon = model.model_icon
        value_tools = model.tools
        model_access = model.access or {}
        if isinstance(model_access, dict):
            value_access_everyone = bool(model_access.get("everyone"))
            value_access_users = model_access.get("users") or []
            value_access_groups = model_access.get("groups") or []
        else:
            value_access_everyone = bool(getattr(model_access, "everyone", False))
            value_access_users = getattr(model_access, "users", []) or []
            value_access_groups = getattr(model_access, "groups", []) or []
        value_status = model.status
        model_settings = model.settings if isinstance(model.settings, dict) else {}
        value_title_generation = bool(model_settings.get("title_generation", False))
        value_title_generation_model = (
            model_settings.get("title_generation_model") or ""
        )
        value_title_generation_model_id = (
            model_settings.get("title_generation_model_id") or ""
        )
        value_system_instruction = model_settings.get("system_instruction") or ""
        value_knowledge_cutoff = model_settings.get("knowledge_cutoff")
        value_training_data = model_settings.get("training_data")
        value_allow_custom_generation_parameter = bool(
            model_settings.get("allow_custom_generation_parameter", False)
        )
        value_input_formats = model_settings.get("input_formats") or []
        value_output_formats = model_settings.get("output_formats") or []
        value_input_token_limit = model_settings.get("input_token_limit", 0)
        value_output_token_limit = model_settings.get("output_token_limit", 0)
        value_thinking = bool(model_settings.get("thinking", False))
        value_thinking_budget = model_settings.get("thinking_budget")
        value_thinking_dynamic = bool(model_settings.get("thinking_dynamic", False))
        value_reasoning_effort = model_settings.get("reasoning_effort")
        value_include_thinking = model_settings.get("include_thinking")
        value_max_image_count = model_settings.get("max_image_count", -1)
        value_max_video_count = model_settings.get("max_video_count", -1)
        value_max_audio_count = model_settings.get("max_audio_count", -1)
        value_max_document_count = model_settings.get("max_document_count", -1)
        value_native_youtube_video = bool(
            model_settings.get("native_youtube_video", False)
        )
        value_max_youtube_video_count = model_settings.get(
            "max_youtube_video_count", -1
        )
        value_websearch_scrape_provider = model_settings.get(
            "websearch_scrape_provider"
        )
        value_websearch_search_provider = model_settings.get(
            "websearch_search_provider"
        )
        value_native_websearch = bool(model_settings.get("native_websearch", False))
        value_use_group_context = bool(model_settings.get("use_group_context", True))
        value_use_project_context = bool(
            model_settings.get("use_project_context", True)
        )
        value_skill_id = model_settings.get("skill_id")
    else:
        value_model_name = model_name
        if model_info:
            value_name = model_info.get("display_name")
            value_description = normalize_aistudio_model_description(
                model_info.get("description")
            )
            if model_name and "gemini" in model_name:
                value_icon = "gemini"
            if model_name and "gemma" in model_name:
                value_icon = "gemma"
            value_input_token_limit = model_info.get("input_token_limit")
            value_output_token_limit = model_info.get("output_token_limit")
        if model_group_dict:
            value_knowledge_cutoff = model_group_dict.get("knowledge_cutoff")

    # Combine the schema into one variable
    info_schema = MODEL_SCHEMA_INFORMATION_SECTION.model_copy(deep=True)
    access_schema = get_model_schema_access_section(db)
    title_schema = get_model_schema_title_section(db)
    skill_schema = get_model_schema_skill_section(db)
    input_format_options = [item.value for item in InputFormatEnum]
    output_format_options = [item.value for item in OutputFormatEnum]
    if not value_input_formats and input_format_options:
        value_input_formats = list(input_format_options)
    if not value_output_formats and output_format_options:
        value_output_formats = list(output_format_options)
    modalities_schema = get_model_schema_modalities_section(
        input_format_options,
        output_format_options,
    ).model_copy(deep=True)
    file_schema = MODEL_SCHEMA_FILE_SECTION.model_copy(deep=True)
    thinking_schema = GOOGLE_AISTUDIO_MODEL_SCHEMA_THINKING_SECTION.model_copy(
        deep=True
    )
    tools_schema = get_model_schema_tools_section(db)
    provider_schema = get_parameters_schema_filled(model_settings)
    combined_schema = combine_model_schema_sections(
        info_schema,
        access_schema,
        title_schema,
        skill_schema,
        modalities_schema,
        file_schema,
        thinking_schema,
        tools_schema,
        provider_schema,
    )

    # Model Name
    if value_model_name:
        _set_schema_field_value(combined_schema, "model_name", value_model_name)

    # Display name like "Gemini 4 Pro"
    if value_name:
        _set_schema_field_value(combined_schema, "name", value_name)

    # Description
    if value_description:
        _set_schema_field_value(combined_schema, "description", value_description)

    # Model icon
    if value_icon:
        _set_schema_field_value(combined_schema, "model_icon", value_icon)

    # Status
    if value_status:
        _set_schema_field_value(combined_schema, "status", value_status)

    # Access Everyone (write even when False so saved value shows up in UI)
    _set_schema_field_value(
        combined_schema, "access.everyone", bool(value_access_everyone)
    )

    # Access Users
    if value_access_users:
        _set_schema_field_value(combined_schema, "access.users", value_access_users)

    # Access Groups
    if value_access_groups:
        _set_schema_field_value(combined_schema, "access.groups", value_access_groups)

    if model_id:
        _set_schema_field_value(
            combined_schema, "settings.use_group_context", bool(value_use_group_context)
        )
        _set_schema_field_value(
            combined_schema,
            "settings.use_project_context",
            bool(value_use_project_context),
        )

    # Title Generation
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

    # System Instruction
    if value_system_instruction:
        _set_schema_field_value(
            combined_schema, "settings.system_instruction", value_system_instruction
        )

    # Knowledge Cutoff
    if value_knowledge_cutoff:
        if isinstance(value_knowledge_cutoff, datetime):
            value_knowledge_cutoff = value_knowledge_cutoff.date().isoformat()
        if isinstance(value_knowledge_cutoff, str):
            value_knowledge_cutoff = (
                datetime.strptime(value_knowledge_cutoff, "%Y-%m-%d").date().isoformat()
            )
        _set_schema_field_value(
            combined_schema, "settings.knowledge_cutoff", value_knowledge_cutoff
        )

    # Training Data
    if value_training_data:
        _set_schema_field_value(
            combined_schema, "settings.training_data", value_training_data
        )

    # Allow Custom Generation Parameter
    if value_allow_custom_generation_parameter:
        _set_schema_field_value(
            combined_schema,
            "settings.allow_custom_generation_parameter",
            value_allow_custom_generation_parameter,
        )

    # Input Formats
    if value_input_formats:
        _set_schema_field_value(
            combined_schema, "settings.input_formats", value_input_formats
        )

    # Output Formats
    if value_output_formats:
        _set_schema_field_value(
            combined_schema, "settings.output_formats", value_output_formats
        )

    # Input Token Limit
    if value_input_token_limit is not None:
        _set_schema_field_value(
            combined_schema, "settings.input_token_limit", value_input_token_limit
        )

    # Output Token Limit
    if value_output_token_limit is not None:
        _set_schema_field_value(
            combined_schema, "settings.output_token_limit", value_output_token_limit
        )

    # Max Image Count
    if value_max_image_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_image_count", value_max_image_count
        )

    # Max Video Count
    if value_max_video_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_video_count", value_max_video_count
        )

    # Max Audio Count
    if value_max_audio_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_audio_count", value_max_audio_count
        )

    # Max Document Count
    if value_max_document_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_document_count", value_max_document_count
        )

    # Native Youtube Video
    if value_native_youtube_video:
        _set_schema_field_value(
            combined_schema, "settings.native_youtube_video", value_native_youtube_video
        )

    # Max Youtube Video Count
    if value_max_youtube_video_count not in (None, -1):
        _set_schema_field_value(
            combined_schema,
            "settings.max_youtube_video_count",
            value_max_youtube_video_count,
        )

    # Tools
    if value_tools:
        _set_schema_field_value(combined_schema, "tools", value_tools)
    apply_model_mcp_schema_values(combined_schema, model_settings)

    # Websearch Scrape Provider
    if value_websearch_scrape_provider:
        _set_schema_field_value(
            combined_schema,
            "settings.websearch_scrape_provider",
            value_websearch_scrape_provider,
        )

    # Websearch Search Provider
    if value_websearch_search_provider:
        _set_schema_field_value(
            combined_schema,
            "settings.websearch_search_provider",
            value_websearch_search_provider,
        )

    if supports_native_websearch and value_native_websearch:
        _set_schema_field_value(
            combined_schema, "settings.native_websearch", value_native_websearch
        )

    # Fixed Skill ID
    if value_skill_id:
        _set_schema_field_value(combined_schema, "settings.skill_id", value_skill_id)

    # Thinking
    if value_thinking:
        _set_schema_field_value(combined_schema, "settings.thinking", value_thinking)

    # Thinking Budget
    if value_thinking_budget:
        _set_schema_field_value(
            combined_schema, "settings.thinking_budget", value_thinking_budget
        )

    # Thinking Dynamic
    if value_thinking_dynamic:
        _set_schema_field_value(
            combined_schema, "settings.thinking_dynamic", value_thinking_dynamic
        )

    # Thinking Level
    if value_reasoning_effort:
        _set_schema_field_value(
            combined_schema, "settings.reasoning_effort", value_reasoning_effort
        )

    # Include Thinking
    if value_include_thinking is not None:
        _set_schema_field_value(
            combined_schema, "settings.include_thinking", value_include_thinking
        )

    model_caps = model_group_dict or {}
    thinking = model_caps.get("thinking") or {}
    thinking_enabled = thinking.get("thinking")
    thinking_budget_supported = thinking.get("thinking_budget_support", False)
    reasoning_effort_supported = thinking.get("reasoning_effort_support", False)
    thinking_dynamic_supported = thinking.get("thinking_support_dynamic", False)
    thinking_disabled_allowed = thinking.get("thinking_disabled_allowed", True)
    reasoning_effort_values = thinking.get("reasoning_effort") or []

    if thinking:
        if thinking_enabled:
            # Thinking is enabled, only keep the fields the model actually supports
            if not thinking_budget_supported:
                _remove_field_from_section(
                    combined_schema.sections, "Thinking", "settings.thinking_budget"
                )
            else:
                budget_field = _get_field_from_section(
                    combined_schema.sections, "Thinking", "settings.thinking_budget"
                )
                if budget_field:
                    min_value = thinking.get("thinking_budget_min")
                    max_value = thinking.get("thinking_budget_max")
                    attributes = budget_field.attributes or FieldAttributes()
                    attributes.min = min_value
                    attributes.max = max_value
                    budget_field.attributes = attributes

            reasoning_effort_field = _get_field_from_section(
                combined_schema.sections,
                "Thinking",
                "settings.reasoning_effort",
            )
            if not reasoning_effort_supported:
                _remove_field_from_section(
                    combined_schema.sections, "Thinking", "settings.reasoning_effort"
                )
            elif reasoning_effort_field and reasoning_effort_values:
                reasoning_effort_field.options = build_reasoning_effort_options(
                    reasoning_effort_values
                )

            if not (thinking_budget_supported and thinking_dynamic_supported):
                _remove_field_from_section(
                    combined_schema.sections, "Thinking", "settings.thinking_dynamic"
                )
            else:
                dynamic_field = _get_field_from_section(
                    combined_schema.sections, "Thinking", "settings.thinking_dynamic"
                )
                if dynamic_field:
                    dynamic_field.default = thinking.get("thinking_dynamic_default")

            if not thinking_disabled_allowed:
                _remove_field_from_section(
                    combined_schema.sections, "Thinking", "settings.thinking"
                )
        else:
            # Thinking metadata exists but feature disabled for this model
            combined_schema.sections = [
                section
                for section in combined_schema.sections
                if section.title != "Thinking"
            ]

    elif model_group_dict:
        # Known model without detailed thinking metadata -> remove section
        combined_schema.sections = [
            section
            for section in combined_schema.sections
            if section.title != "Thinking"
        ]

    # Media resolution support
    support_media_resolution = model_caps.get("support_media_resolution", False)
    if not support_media_resolution:
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

    # Remove Google Aistudio unsupported fields
    _remove_field_from_section(
        combined_schema.sections, "File attachments", "settings.pdf_processing_engine"
    )
    if not supports_native_websearch:
        _remove_field_from_section(
            combined_schema.sections, "Tools & enrichment", "settings.native_websearch"
        )
    if provider_id:
        _remove_field_from_section(
            combined_schema.sections, "Model Information", "model_name"
        )
    return combined_schema
