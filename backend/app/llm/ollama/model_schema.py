"""Ollama model configuration schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.ollama import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_ollama_model_schema": (
        "InputFormatEnum",
        "MODEL_SCHEMA_FILE_SECTION",
        "MODEL_SCHEMA_INFORMATION_SECTION",
        "OLLAMA_MODEL_SCHEMA_THINKING_SECTION",
        "OutputFormatEnum",
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
        "get_ollama_model_info",
        "get_parameters_schema_filled",
        "ollama_model_supports_reasoning_effort",
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
    "InputFormatEnum",
    "MODEL_SCHEMA_FILE_SECTION",
    "MODEL_SCHEMA_INFORMATION_SECTION",
    "OLLAMA_MODEL_SCHEMA_THINKING_SECTION",
    "OutputFormatEnum",
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
    "get_ollama_model_info",
    "get_parameters_schema_filled",
    "ollama_model_supports_reasoning_effort",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_ollama_model_schema(
    db, provider_id, model_name: str | None = None, model_id: str | None = None
):
    """Get Ollama model schema."""
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

    model_info = get_ollama_model_info(db, provider_id, model_name)
    model_capabilities = model_info.get("capabilities") or []
    model_details = model_info.get("details") or {}

    value_model_name = ""
    value_name = ""
    value_description = ""
    value_icon = ""
    value_tools: list[str] = []
    value_access_everyone = False
    value_access_users: list[str] = []
    value_access_groups: list[str] = []
    value_status = ""
    model_settings: dict[str, Any] = {}
    value_title_generation = False
    value_title_generation_model = "current"
    value_title_generation_model_id = ""
    value_custom_title_generation_instruction = ""
    value_system_instruction = ""
    value_knowledge_cutoff = None
    value_training_data = None
    value_allow_custom_generation_parameter = False
    value_input_formats: list[str] = []
    value_output_formats: list[str] = []
    value_input_token_limit: int | None = 0
    value_output_token_limit: int | None = 0
    value_max_image_count = None
    value_max_document_count = None
    value_reasoning = None
    value_reasoning_effort = None
    value_websearch_scrape_provider = None
    value_websearch_search_provider = None
    value_skill_id = None

    if model_id and model:
        value_model_name = model.model_name
        value_name = model.name
        value_description = model.description
        value_icon = model.model_icon
        value_tools = model.tools or []
        model_access = model.access or {}
        if isinstance(model_access, dict):
            value_access_everyone = bool(model_access.get("everyone"))
            value_access_users = _ensure_list(model_access.get("users"))
            value_access_groups = _ensure_list(model_access.get("groups"))
        else:
            value_access_everyone = bool(getattr(model_access, "everyone", False))
            value_access_users = _ensure_list(getattr(model_access, "users", []) or [])
            value_access_groups = _ensure_list(
                getattr(model_access, "groups", []) or []
            )
        value_status = model.status
        model_settings = model.settings if isinstance(model.settings, dict) else {}
        value_title_generation = bool(model_settings.get("title_generation", False))
        value_title_generation_model = (
            model_settings.get("title_generation_model") or "current"
        )
        value_title_generation_model_id = (
            model_settings.get("title_generation_model_id") or ""
        )
        value_custom_title_generation_instruction = (
            model_settings.get("custom_title_generation_instruction") or ""
        )
        value_system_instruction = model_settings.get("system_instruction") or ""
        value_knowledge_cutoff = model_settings.get("knowledge_cutoff")
        value_training_data = model_settings.get("training_data")
        value_allow_custom_generation_parameter = bool(
            model_settings.get("allow_custom_generation_parameter", False)
        )
        value_input_formats = _ensure_list(model_settings.get("input_formats"))
        value_output_formats = _ensure_list(model_settings.get("output_formats"))
        value_input_token_limit = model_settings.get("input_token_limit", 0)
        value_output_token_limit = model_settings.get("output_token_limit", 0)
        value_max_image_count = model_settings.get("max_image_count")
        value_max_document_count = model_settings.get("max_document_count")
        value_reasoning = model_settings.get("reasoning")
        if value_reasoning is None:
            value_reasoning = model_settings.get("thinking_enabled")
        value_reasoning_effort = model_settings.get("reasoning_effort")
        value_websearch_scrape_provider = model_settings.get(
            "websearch_scrape_provider"
        )
        value_websearch_search_provider = model_settings.get(
            "websearch_search_provider"
        )
        value_skill_id = model_settings.get("skill_id")
    else:
        value_model_name = model_name or ""
        value_name = model_info.get("display_name") or model_name or ""
        value_description = model_info.get("description") or ""
        families = _ensure_list(model_details.get("families"))
        family = (families or [model_details.get("family") or ""])[0]
        family_lower = (family or "").lower()
        model_name_lower = (value_model_name or "").lower()

        if "gemma" in family_lower or "gemma" in model_name_lower:
            value_icon = "gemma"
        elif "gemini" in family_lower or "gemini" in model_name_lower:
            value_icon = "gemini"
        elif "qwen" in family_lower or "qwen" in model_name_lower:
            value_icon = "qwen"
        elif "claude" in family_lower or "claude" in model_name_lower:
            value_icon = "claude"
        elif "gpt-oss" in family_lower or "gpt-oss" in model_name_lower:
            value_icon = "openai"
        elif "deepseek" in family_lower or "deepseek" in model_name_lower:
            value_icon = "deepseek"
        elif "mistral" in family_lower or "mistral" in model_name_lower:
            value_icon = "mistral"
        elif "llama" in family_lower or "llama" in model_name_lower:
            value_icon = "meta"
        elif "minimax" in family_lower or "minimax" in model_name_lower:
            value_icon = "minimax"
        elif "kimi" in family_lower or "kimi" in model_name_lower:
            value_icon = "kimi"
        if not value_output_formats:
            value_output_formats = [OutputFormatEnum.text.value]

    supported_input_formats = {item.value for item in InputFormatEnum}
    value_input_formats = [
        value
        for value in value_input_formats
        if value in supported_input_formats
    ]
    if not value_input_formats:
        value_input_formats = [InputFormatEnum.text.value]
        if "vision" in model_capabilities:
            value_input_formats.append(InputFormatEnum.image.value)
        value_input_formats.extend(
            [InputFormatEnum.pdf.value, InputFormatEnum.text_document.value]
        )

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
    thinking_schema = OLLAMA_MODEL_SCHEMA_THINKING_SECTION.model_copy(deep=True)
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
        if isinstance(value_knowledge_cutoff, date):
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
    if value_max_document_count not in (None, -1):
        _set_schema_field_value(
            combined_schema,
            "settings.max_document_count",
            value_max_document_count,
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
    if value_tools:
        _set_schema_field_value(combined_schema, "tools", value_tools)
    apply_model_mcp_schema_values(combined_schema, model_settings)

    if value_reasoning is not None:
        _set_schema_field_value(
            combined_schema, "settings.reasoning", bool(value_reasoning)
        )
    if value_reasoning_effort:
        _set_schema_field_value(
            combined_schema, "settings.reasoning_effort", value_reasoning_effort
        )

    # Fixed Skill ID
    if value_skill_id:
        _set_schema_field_value(combined_schema, "settings.skill_id", value_skill_id)

    supports_reasoning_effort = ollama_model_supports_reasoning_effort(value_model_name)
    has_thinking = ("thinking" in model_capabilities) or supports_reasoning_effort
    has_vision = "vision" in model_capabilities
    sections_list = getattr(combined_schema, "sections", [])
    if not has_thinking:
        _remove_section_from_sections(sections_list, "Reasoning")
    elif not supports_reasoning_effort:
        _remove_field_from_section(
            sections_list, "Reasoning", "settings.reasoning_effort"
        )
    if not has_vision:
        _remove_field_from_section(
            sections_list, "File attachments", "settings.max_image_count"
        )
    # Remove attachment controls that Omlorix cannot enforce for Ollama.
    _remove_field_from_section(
        sections_list, "File attachments", "settings.max_video_count"
    )
    _remove_field_from_section(
        sections_list, "File attachments", "settings.max_audio_count"
    )
    _remove_field_from_section(
        sections_list, "File attachments", "settings.max_youtube_video_count"
    )
    _remove_field_from_section(
        sections_list, "File attachments", "settings.native_youtube_video"
    )
    _remove_field_from_section(
        sections_list, "File attachments", "settings.pdf_processing_engine"
    )
    _remove_field_from_section(
        sections_list, "Tools & enrichment", "settings.native_websearch"
    )

    return combined_schema
