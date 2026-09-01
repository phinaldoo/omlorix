"""Assembly of the full Anthropic model configuration schema."""

from datetime import datetime

from app.llm.anthropic.generation_schema import get_parameters_schema_filled
from app.llm.anthropic.model_list import (
    get_anthropic_knowledge_cutoff,
    supports_anthropic_native_websearch,
)
from app.llm.anthropic.schema_definitions import (
    ANTHROPIC_MODEL_SCHEMA_PROMPT_CACHE_SECTION,
    ANTHROPIC_MODEL_SCHEMA_THINKING_SECTION,
    InputFormatEnum,
    OutputFormatEnum,
)
from app.llm.anthropic.thinking import (
    get_anthropic_thinking_capabilities,
    is_anthropic_base_provider_type,
)
from app.llm.model_schemas import (
    MODEL_SCHEMA_FILE_SECTION,
    MODEL_SCHEMA_INFORMATION_SECTION,
    apply_model_mcp_schema_values,
    combine_model_schema_sections,
    get_model_schema_access_section,
    get_model_schema_modalities_section,
    get_model_schema_skill_section,
    get_model_schema_title_section,
    get_model_schema_tools_section,
)
from app.llm.reasoning_effort_options import build_reasoning_effort_options
from app.utils.schemas import (
    _get_field_from_section,
    _remove_field_from_section,
    _set_schema_field_value,
)


def get_anthropic_model_info(
    db,
    provider_id,
    model_name: str | None = None,
    model_id: str | None = None,
    model_info: dict | None = None,
):
    """Get current model metadata from Anthropic instead of a static catalog."""
    from app.llm.models import get_model

    resolved_model_name = (model_name or "").strip()
    if model_id:
        model = get_model(db, model_id)
        resolved_model_name = model.model_name
    candidates = [model_info] if isinstance(model_info, dict) else []
    if provider_id and resolved_model_name and not candidates:
        try:
            # Local import avoids a module cycle: the provider utility imports
            # these schemas for request validation.
            from app.llm.anthropic.utils import list_anthropic_models

            candidates = list_anthropic_models(db, anthropic_provider_id=provider_id)
        except Exception:
            # Schema rendering must remain available when discovery is down or
            # an Anthropic-compatible endpoint omits model-list support.
            candidates = []
    normalized_name = resolved_model_name.casefold()
    match = next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("id") or "").casefold() == normalized_name
        ),
        {},
    )
    return {"model_group_dict": match}


def get_anthropic_model_schema(
    db,
    provider_id,
    model_name: str | None = None,
    model_id: str | None = None,
    anthropic_provider_type: str = "anthropic",
    model_info: dict | None = None,
):
    """Get Anthropic model schema."""
    from app.llm.models import get_model

    model = None
    if model_id:
        model = get_model(db, model_id)
        model_name = model.model_name
    resolved_info = get_anthropic_model_info(
        db,
        provider_id,
        model_name,
        model_info=model_info,
    )
    model_group_dict = resolved_info.get("model_group_dict") or {}
    supports_native_websearch = not is_anthropic_base_provider_type(
        anthropic_provider_type
    ) and supports_anthropic_native_websearch(model_name or "")
    thinking = get_anthropic_thinking_capabilities(
        model_name,
        model_info=model_group_dict,
        allow_compatible_fallback=is_anthropic_base_provider_type(
            anthropic_provider_type
        ),
    )
    thinking_enabled = thinking.get("thinking")
    thinking_budget_supported = thinking.get("thinking_budget_support", False)
    reasoning_effort_supported = thinking.get("reasoning_effort_support", False)
    thinking_adaptive_supported = thinking.get("thinking_support_adaptive", False)
    thinking_disabled_allowed = thinking.get("thinking_disabled_allowed", True)
    reasoning_effort_values = thinking.get("reasoning_effort") or []

    value_model_name = ""
    value_name = ""
    value_description = ""
    value_icon = ""
    value_tools = []
    value_websearch_scrape_provider = None
    value_websearch_search_provider = None

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
    value_reasoning_effort = None
    value_thinking_adaptive = None

    value_max_image_count = -1
    value_max_document_count = -1
    value_max_youtube_video_count = -1
    value_use_group_context = True
    value_use_project_context = True
    value_native_websearch = False
    value_prompt_cache_enabled = False
    value_skill_id = None
    if model_id:
        value_model_name = model.model_name
        value_name = model.name
        value_description = model.description
        value_icon = model.model_icon
        value_tools = model.tools
        model_settings = model.settings if isinstance(model.settings, dict) else {}
        value_websearch_scrape_provider = model_settings.get(
            "websearch_scrape_provider"
        )
        value_websearch_search_provider = model_settings.get(
            "websearch_search_provider"
        )
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
        value_reasoning_effort = model_settings.get("reasoning_effort")
        value_thinking_adaptive = model_settings.get("thinking_adaptive")
        value_max_image_count = model_settings.get("max_image_count", -1)
        value_max_document_count = model_settings.get("max_document_count", -1)
        value_max_youtube_video_count = model_settings.get(
            "max_youtube_video_count", -1
        )
        value_use_group_context = bool(model_settings.get("use_group_context", True))
        value_use_project_context = bool(
            model_settings.get("use_project_context", True)
        )
        value_native_websearch = bool(model_settings.get("native_websearch", False))
        value_prompt_cache_enabled = bool(
            model_settings.get("prompt_cache_enabled", False)
        )
        value_skill_id = model_settings.get("skill_id")
    else:
        value_model_name = model_name
        value_knowledge_cutoff = get_anthropic_knowledge_cutoff(model_name or "")
        if model_group_dict:
            value_name = model_group_dict.get("display_name") or model_group_dict.get(
                "name"
            )
            value_input_token_limit = model_group_dict.get("max_input_tokens")
            value_output_token_limit = model_group_dict.get("max_tokens")
            capabilities = model_group_dict.get("capabilities") or {}
            value_input_formats = ["text", "text_document"]
            if capabilities.get("image_input"):
                value_input_formats.append("image")
            if capabilities.get("pdf_input"):
                value_input_formats.append("pdf")
            value_output_formats = ["text"]
            value_thinking = bool(thinking_enabled and not thinking_disabled_allowed)
            if value_output_token_limit is not None:
                model_settings["max_tokens"] = value_output_token_limit
    if (
        value_thinking_adaptive is None
        and thinking_enabled
        and thinking_adaptive_supported
    ):
        value_thinking_adaptive = True

    # Combine the schema into one variable
    info_schema = MODEL_SCHEMA_INFORMATION_SECTION.model_copy(deep=True)
    access_schema = get_model_schema_access_section(db)
    title_schema = get_model_schema_title_section(db)
    skill_schema = get_model_schema_skill_section(db)
    input_format_options = [item.value for item in InputFormatEnum]
    output_format_options = [item.value for item in OutputFormatEnum]
    modalities_schema = get_model_schema_modalities_section(
        input_format_options,
        output_format_options,
    ).model_copy(deep=True)
    file_schema = MODEL_SCHEMA_FILE_SECTION.model_copy(deep=True)
    thinking_schema = ANTHROPIC_MODEL_SCHEMA_THINKING_SECTION.model_copy(deep=True)
    prompt_cache_schema = ANTHROPIC_MODEL_SCHEMA_PROMPT_CACHE_SECTION.model_copy(
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
        prompt_cache_schema,
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

    # Max Document Count
    if value_max_document_count not in (None, -1):
        _set_schema_field_value(
            combined_schema, "settings.max_document_count", value_max_document_count
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

    # This setting is available for first-party and Anthropic Base models. A
    # compatible endpoint receives cache_control only after an administrator
    # explicitly enables the model-level switch.
    _set_schema_field_value(
        combined_schema,
        "settings.prompt_cache_enabled",
        value_prompt_cache_enabled,
    )

    # Fixed Skill ID
    if value_skill_id:
        _set_schema_field_value(combined_schema, "settings.skill_id", value_skill_id)

    # Thinking
    if value_thinking:
        _set_schema_field_value(combined_schema, "settings.thinking", value_thinking)

    # Thinking Budget
    if value_thinking_budget is not None:
        _set_schema_field_value(
            combined_schema, "settings.thinking_budget", value_thinking_budget
        )

    # Reasoning Effort
    if value_reasoning_effort is not None:
        _set_schema_field_value(
            combined_schema, "settings.reasoning_effort", value_reasoning_effort
        )

    # Adaptive Thinking
    if value_thinking_adaptive is not None:
        _set_schema_field_value(
            combined_schema, "settings.thinking_adaptive", value_thinking_adaptive
        )

    # Remove fields not supported by anthropic
    file_sections = combined_schema.sections
    _remove_field_from_section(
        file_sections, "File attachments", "settings.native_youtube_video"
    )
    _remove_field_from_section(
        file_sections, "File attachments", "settings.max_video_count"
    )
    _remove_field_from_section(
        file_sections, "File attachments", "settings.max_audio_count"
    )
    _remove_field_from_section(
        file_sections, "File attachments", "settings.max_youtube_video_count"
    )
    _remove_field_from_section(
        file_sections, "File attachments", "settings.pdf_processing_engine"
    )

    tools_section = combined_schema.sections
    if not supports_native_websearch:
        _remove_field_from_section(
            tools_section, "Tools & enrichment", "settings.native_websearch"
        )

    thinking_section_title = "Thinking & reasoning"
    if thinking:
        if thinking_enabled:
            if not thinking_budget_supported:
                _remove_field_from_section(
                    combined_schema.sections,
                    thinking_section_title,
                    "settings.thinking_budget",
                )
            reasoning_effort_field = _get_field_from_section(
                combined_schema.sections,
                thinking_section_title,
                "settings.reasoning_effort",
            )
            if not reasoning_effort_supported:
                _remove_field_from_section(
                    combined_schema.sections,
                    thinking_section_title,
                    "settings.reasoning_effort",
                )
            elif reasoning_effort_field and reasoning_effort_values:
                reasoning_effort_field.options = build_reasoning_effort_options(
                    reasoning_effort_values
                )
            if not thinking_adaptive_supported:
                _remove_field_from_section(
                    combined_schema.sections,
                    thinking_section_title,
                    "settings.thinking_adaptive",
                )
            if not thinking_disabled_allowed:
                _remove_field_from_section(
                    combined_schema.sections,
                    thinking_section_title,
                    "settings.thinking",
                )
        else:
            combined_schema.sections = [
                section
                for section in combined_schema.sections
                if section.title != thinking_section_title
            ]
    elif model_group_dict:
        combined_schema.sections = [
            section
            for section in combined_schema.sections
            if section.title != thinking_section_title
        ]
    if provider_id:
        _remove_field_from_section(
            combined_schema.sections, "Model Information", "model_name"
        )
    return combined_schema
