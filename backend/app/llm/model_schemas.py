from app.utils.schemas import (
    FieldSchema,
    FieldAttributes,
    Option,
    Section,
    Sections,
    _get_field_from_section,
    _remove_field_from_section,
    _set_schema_field_value,
    _remove_section_from_sections,
)
from app.groups.models import list_all_groups
from app.users.models import list_all_users

# -------------------
# Model Schemas
# -------------------


def _tool_option_i18n_label(tool_name: str | None) -> str | None:
    """Return the stable admin translation key for a built-in model tool option."""
    normalized = str(tool_name or "").strip()
    if normalized == "mcp":
        return "schema_backend_mcp_servers"
    if not normalized:
        return None
    from app.tools.registry import get_rate_limit_tool_label_i18n_key

    return get_rate_limit_tool_label_i18n_key(normalized)

MODEL_SCHEMA_INFORMATION_SECTION = Sections(
    sections=[
        Section(
            title="Model Information",
            description="Provide the core metadata shown to admins and users when selecting this model.",
            fields=[
                FieldSchema(
                    key="model_name",
                    label="Model ID",
                    description="Identifier used when calling the provider API.",
                    type="string",
                    input_type="str",
                    required=True,
                ),
                FieldSchema(
                    key="name",
                    label="Display name",
                    description="Shown to admins and users when selecting this model.",
                    type="string",
                    input_type="text",
                    required=True,
                ),
                FieldSchema(
                    key="description",
                    label="Description",
                    description="Summarize when this model should be used.",
                    type="string",
                    input_type="text",
                    required=False,
                    attributes=FieldAttributes(max=100),
                ),
                FieldSchema(
                    key="model_icon",
                    label="Model icon",
                    description="Paste an SVG snippet to represent the model.",
                    type="string",
                    input_type="text",
                    required=False,
                ),
                FieldSchema(
                    key="status",
                    label="Status",
                    description="Communicate the rollout stage of this model.",
                    type="select",
                    options=[
                        Option(value="normal", label="Normal", i18n_label="llm.shared.system_instruction.option.normal"),
                        Option(value="alpha", label="Alpha", i18n_label="llm.shared.system_instruction.option.alpha"),
                        Option(value="experimental", label="Experimental", i18n_label="llm.shared.system_instruction.option.experimental"),
                    ],
                    required=False,
                ),
            ]
        ),  
    ]
)


def _coerce_schema_sections(part: Sections | list[Section] | None) -> list[Section]:
    """Normalize schema parts to a flat section list."""
    if part is None:
        return []
    if isinstance(part, Sections):
        return list(part.sections or [])
    return list(part or [])


def combine_model_schema_sections(*parts: Sections | list[Section] | None) -> Sections:
    """Combine model schema sections in the provided order."""
    sections: list[Section] = []
    for part in parts:
        sections.extend(_coerce_schema_sections(part))
    return Sections(sections=sections)


def apply_model_mcp_schema_values(schema: Sections, model_settings: dict | None) -> None:
    if not isinstance(model_settings, dict):
        return
    from app.mcp.utils import (
        get_model_allowed_mcp_selector_values,
        model_allows_custom_user_mcp_servers,
        parse_connection_provider_mcp_value,
    )

    if "allowed_mcp_servers" in model_settings:
        selector_values = get_model_allowed_mcp_selector_values(model_settings)
        selector_field = next(
            (
                field
                for section in schema.sections
                for field in section.fields
                if field.key == "settings.allowed_mcp_servers"
            ),
            None,
        )
        available_values = {
            str(option.value)
            for option in (selector_field.options if selector_field else []) or []
        }

        # Preserve explicit admin MCP identifiers so an administrator can see
        # and repair stale server selections. Connection provider sentinels are
        # different: an unavailable OAuth provider must not be synthesized as
        # a selected UI value after its option was intentionally filtered out.
        selector_values = [
            value
            for value in selector_values
            if parse_connection_provider_mcp_value(value) is None
            or value in available_values
        ]
        _set_schema_field_value(
            schema,
            "settings.allowed_mcp_servers",
            selector_values,
        )
    if (
        "allow_custom_user_mcp_servers" in model_settings
        or "allowed_mcp_servers" in model_settings
    ):
        _set_schema_field_value(
            schema,
            "settings.allow_custom_user_mcp_servers",
            model_allows_custom_user_mcp_servers(model_settings),
        )




def get_model_schema_access_section(db) -> Sections:
    """Get the access section of the model schema."""
    groups = list_all_groups(db)
    users = list_all_users(db)

    group_options = []
    for group in groups or []:
        group_id = getattr(group, "id", None)
        group_name = getattr(group, "name", None) or "Unnamed group"
        if not group_id:
            continue
        group_options.append(Option(value=str(group_id), label=str(group_name)))

    user_options = []
    for user in users or []:
        user_id = getattr(user, "id", None)
        if not user_id:
            continue
        first_name = (getattr(user, "first_name", "") or "").strip()
        last_name = (getattr(user, "last_name", "") or "").strip()
        email = (getattr(user, "email", "") or "").strip()
        display_name = " ".join(part for part in [first_name, last_name] if part).strip()
        label = display_name or email or "Unnamed user"
        if email and display_name:
            label = f"{display_name} ({email})"
        user_options.append(Option(value=str(user_id), label=label))

    schema = Sections(
        sections=[
            Section(
                title="Access",
                description="Control who may launch this model from the chat interface.",
                fields=[
                    FieldSchema(
                        key="access.everyone",
                        label="Visibility",
                        description="Enable to make this model available to everyone.",
                        type="boolean",
                    ),
                    FieldSchema(
                        key="access.users",
                        label="Allowed users",
                        description="Select specific users that may access this model.",
                        type="select",
                        multiple=True,
                        options=user_options,
                        required=False,
                        dependency="access.everyone",
                        dependency_value=False,
                    ),
                    FieldSchema(
                        key="access.groups",
                        label="Allowed groups",
                        description="Select specific groups that may access this model.",
                        type="select",
                        multiple=True,
                        options=group_options,
                        required=False,
                        dependency="access.everyone",
                        dependency_value=False,
                    ),
                ],
            ),
        ]
    )
    return schema




def get_model_schema_title_section(db) -> Sections:
    """Get the title section of the model schema."""
    from app.llm.models import list_models as _list_models

    schema = Sections(
        sections=[
            Section(
                title="Conversation titles & prompts",
                description="Configure how this model generates conversation titles and which base instructions apply.",
                fields=[
                    FieldSchema(
                        key="settings.title_generation",
                        label="Title generation",
                        description="Enable title generation for this model.",
                        type="boolean",
                    ),
                    FieldSchema(
                        key="settings.title_generation_model",
                        label="Title generation model",
                        description="Choose whether to use the current model or a specific ID for title generation.",
                        type="select",
                        options=[
                            Option(value="current", label="Current model", i18n_label="llm.shared.system_instruction.option.current"),
                            Option(value="specific", label="Specific model", i18n_label="llm.shared.system_instruction.option.specific"),
                        ],
                        dependency="settings.title_generation",
                        dependency_value=True,
                    ),
                    FieldSchema(
                        key="settings.title_generation_model_id",
                        label="Title generation model ID",
                        description="ID of the model to use for title generation.",
                        type="select",
                        options=[],
                        multiple=False,
                        required=False,
                        dependency="settings.title_generation_model",
                        dependency_value="specific",
                        dependency2="settings.title_generation",
                        dependency2_value=True,
                    ),
                    FieldSchema(
                        key="settings.custom_title_generation_instruction",
                        label="Custom title generation instruction",
                        description="Custom title generation instruction for the model, if it is used for title generation.",
                        placeholder="Leave empty for default",
                        type="string",
                        input_type="text",
                        required=False,
                        dependency="settings.title_generation",
                        dependency_value=True,
                    ),
                    FieldSchema(
                        key="settings.system_instruction",
                        label="System instruction",
                        description="System instruction for the model.",
                        placeholder="Leave empty for default",
                        type="string",
                        input_type="text",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.knowledge_cutoff",
                        label="Knowledge cutoff",
                        description="Knowledge cutoff date for the model.",
                        type="string",
                        input_type="date",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.training_data",
                        label="Training data",
                        description="Indicate whether the model was trained on proprietary data.",
                        type="select",
                        options=[
                            Option(value="true", label="true", i18n_label="llm.shared.system_instruction.option.true"),
                            Option(value="false", label="false", i18n_label="llm.shared.system_instruction.option.false"),
                            Option(value="unknown", label="unknown", i18n_label="llm.shared.system_instruction.option.unknown"),
                        ],
                        required=True,
                    ),
                    FieldSchema(
                        key="settings.allow_custom_generation_parameter",
                        label="Allow custom generation parameter",
                        description="Allow the user to set custom generation settings for this model. This could also include thinking settings, tools and max token settings.",
                        type="boolean",
                        required=False,
                    ),
                ],
            ),
        ]
    )

    models = _list_models(db)
    options = []
    for model in models or []:
        model_id = getattr(model, "id", None)
        if not model_id:
            continue
        label = getattr(model, "name", None) or getattr(model, "model_name", None) or model_id
        options.append(Option(value=model_id, label=str(label)))

    options.sort(key=lambda option: option.label.lower())

    for section in schema.sections:
        for field in section.fields:
            if getattr(field, "key", None) == "settings.title_generation_model_id":
                field.options = options
                break

    return schema



def get_model_schema_skill_section(db) -> Sections:
    """Return a schema section for selecting a fixed admin skill for this model."""
    from app.skills.models import list_admin_skills as _list_admin_skills

    schema = Sections(
        sections=[
            Section(
                title="Fixed skill",
                description="Optionally assign a fixed managed skill to this model. When set, this skill will be applied to every generation and users cannot override it.",
                fields=[
                    FieldSchema(
                        key="settings.skill_id",
                        label="Fixed skill",
                        description="Select a managed skill to apply to every generation with this model. Leave empty for no fixed skill.",
                        type="select",
                        options=[],
                        multiple=False,
                        required=False,
                    ),
                ],
            ),
        ]
    )

    admin_skills = _list_admin_skills(db)
    options = [Option(value="", label="No fixed skill", i18n_label="llm.shared.system_instruction.option.no_fixed_skill")]
    for skill in admin_skills or []:
        skill_id = getattr(skill, "id", None)
        if not skill_id:
            continue
        skill_name = getattr(skill, "name", None) or "Unnamed skill"
        options.append(Option(value=str(skill_id), label=str(skill_name)))

    for section in schema.sections:
        for field in section.fields:
            if getattr(field, "key", None) == "settings.skill_id":
                field.options = options
                break

    return schema



MODEL_SCHEMA_FILE_SECTION = Sections(
    sections=[
        Section(
            title="File attachments",
            description="Configure file attachments for this model.",
            fields=[
                FieldSchema(
                    key="settings.native_youtube_video",
                    label="Native YouTube video",
                    description="Enable native YouTube video support.",
                    type="boolean",
                    default=True,
                    required=False,
                ),
                FieldSchema(
                    key="settings.max_image_count",
                    label="Max image count",
                    description="Maximum number of images allowed in the input. Leave empty for unlimited.",
                    placeholder="Leave empty for unlimited.",
                    type="string",
                    input_type="int",
                    required=False,
                    dependency="settings.input_formats",
                    dependency_value=["image"],
                ),
                FieldSchema(
                    key="settings.max_video_count",
                    label="Max video count",
                    description="Maximum number of videos allowed in the input. Leave empty for unlimited.",
                    placeholder="Leave empty for unlimited.",
                    type="string",
                    input_type="int",
                    required=False,
                    dependency="settings.input_formats",
                    dependency_value=["video"],
                ),
                FieldSchema(
                    key="settings.max_audio_count",
                    label="Max audio count",
                    description="Maximum number of audio files allowed in the input. Leave empty for unlimited.",
                    placeholder="Leave empty for unlimited.",
                    type="string",
                    input_type="int",
                    required=False,
                    dependency="settings.input_formats",
                    dependency_value=["audio"],
                ),
                FieldSchema(
                    key="settings.max_document_count",
                    label="Max document count",
                    description="Maximum number of documents allowed in the input. Leave empty for unlimited.",
                    placeholder="Leave empty for unlimited.",
                    type="string",
                    input_type="int",
                    required=False,
                    dependency="settings.input_formats",
                    dependency_value=["pdf", "text_document"],
                ),
                FieldSchema(
                    key="settings.max_youtube_video_count",
                    label="Max YouTube video count",
                    description="Maximum number of native YouTube videos allowed in the input. Leave empty for unlimited.",
                    placeholder="Leave empty for unlimited.",
                    type="string",
                    input_type="int",
                    required=False,
                    dependency="settings.native_youtube_video",
                    dependency_value=True,
                ),
                FieldSchema(
                    key="settings.pdf_processing_engine",
                    label="PDF processing engine",
                    description="Select the engine used to process PDF documents.",
                    type="select",
                    options=[
                        Option(value="pdf-text", label="pdf-text", i18n_label="llm.shared.system_instruction.option.pdf-text"),
                        Option(value="mistral-ocr", label="mistral-ocr", i18n_label="llm.shared.system_instruction.option.mistral-ocr"),
                        Option(value="native", label="native", i18n_label="llm.shared.system_instruction.option.native"),
                    ],
                    required=False,
                ),
            ],
        ),
    ]
)        




def get_model_schema_tools_section(db) -> Sections:
    """Get the tools section of the model schema."""
    from app.connections.service import list_managed_connection_mcp_catalog
    from app.mcp.models import OWNER_ADMIN, list_mcp_servers
    from app.mcp.utils import build_connection_provider_mcp_value
    from app.tools.utils import list_available_tool_options

    def _provider_to_option_with_types(provider_info: dict) -> Option:
        """Create an Option with type metadata embedded."""
        provider_id = provider_info.get("id", "")
        label = provider_info.get("name") or provider_info.get("provider") or provider_id
        types = provider_info.get("types", [])
        has_combined = provider_info.get("has_combined", False)
        has_scrape = provider_info.get("has_scrape", False)
        has_search = provider_info.get("has_search", False)
        
        return Option(
            value=str(provider_id),
            label=str(label),
            metadata={
                "types": types,
                "has_combined": has_combined,
                "has_scrape": has_scrape,
                "has_search": has_search,
            }
        )
    
    from app.tools.websearch.models import list_websearch_providers_with_types as _list_providers_with_types
    raw_tool_options = list_available_tool_options(db=db) or []
    raw_tool_options = sorted(raw_tool_options, key=lambda item: str(item.get("label") or item.get("name") or "").lower())
    tool_options = [
        Option(
            value=item["name"],
            label=item.get("label") or item["name"],
            i18n_label=item.get("i18n_label") or _tool_option_i18n_label(item.get("name")),
        )
        for item in raw_tool_options
        if item.get("name")
    ]
    tool_options.append(
        Option(
            value="mcp",
            label="MCP Servers",
            i18n_label="schema_backend_mcp_servers",
            metadata={"dynamic": True},
        )
    )
    
    all_providers = _list_providers_with_types(db)
    
    scrape_options = [
        _provider_to_option_with_types(p) 
        for p in all_providers 
        if p.get("has_scrape", False)
    ]
    
    search_options = [
        _provider_to_option_with_types(p) 
        for p in all_providers 
        if p.get("has_search", False) or p.get("has_combined", False)
    ]
    admin_mcp_options = []
    for server in list_mcp_servers(db, owner_type=OWNER_ADMIN):
        status_suffix = "" if bool(getattr(server, "enabled", True)) else " (disabled)"
        admin_mcp_options.append(
            Option(
                value=str(server.id),
                label=f"{server.name}{status_suffix}",
            )
        )
    # Only offer connection types that have a usable setup path on this
    # instance. In particular, OAuth-only providers stay out of the model
    # configuration until their OAuth client is fully configured.
    for connection_meta in list_managed_connection_mcp_catalog(db):
        provider = str(connection_meta.get("provider") or "").strip().lower()
        title = str(connection_meta.get("title") or provider).strip() or provider
        admin_mcp_options.append(
            Option(
                value=build_connection_provider_mcp_value(provider),
                # Keep the model-level allow-list terminology aligned with
                # the conversation selector: it allows an MCP server, even
                # when that server is backed by a managed connection.
                label=f"{title} (Server)",
            )
        )
    admin_mcp_options.sort(key=lambda option: str(option.label or option.value or "").lower())

    return Sections(
        sections=[
            Section(
                title="Tools & enrichment",
                description="Toggle built-in tools and web search enrichment for this model.",
                fields=[
                    FieldSchema(
                        key="tools",
                        label="Enabled tools",
                        description="Select which platform tools this model may call.",
                        type="select",
                        multiple=True,
                        searchable=True,
                        options=tool_options,
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.native_websearch",
                        label="Native web search",
                        description="Enable native web search support of the model provider.",
                        type="boolean",
                        required=False,
                        dependency="tools",
                        dependency_value=["web_search"],
                        value=True,
                    ),
                    FieldSchema(
                        key="settings.websearch_search_provider",
                        label="Web search provider",
                        description="Provider used to perform web searches. Combined providers handle both search and scraping in one request.",
                        type="select",
                        options=search_options,
                        required=False,
                        dependency="tools",
                        dependency_value=["web_search"],
                        dependency2="settings.native_websearch",
                        dependency2_value=False,
                    ),
                    FieldSchema(
                        key="settings.websearch_scrape_provider",
                        label="Web scrape provider",
                        description="Provider used to scrape webpage content. Hidden only when the selected search provider also supports direct URL scraping itself.",
                        type="select",
                        options=scrape_options,
                        required=False,
                        dependency="tools",
                        dependency_value=["web_search"],
                        dependency2="settings.native_websearch",
                        dependency2_value=False,
                    ),
                    FieldSchema(
                        key="settings.allowed_mcp_servers",
                        label="Allowed admin MCPs & connections",
                        description="Restrict this model to selected admin MCP servers and connection-backed MCP providers. Leave empty to allow all admin MCP servers and all connection-backed MCP providers.",
                        type="select",
                        multiple=True,
                        options=admin_mcp_options,
                        required=False,
                        dependency="tools",
                        dependency_value=["mcp"],
                    ),
                    FieldSchema(
                        key="settings.allow_custom_user_mcp_servers",
                        label="Allow custom user MCPs",
                        description="Enable each user's personal custom MCP servers in addition to the admin MCP and connection options selected above.",
                        type="boolean",
                        required=False,
                        dependency="tools",
                        dependency_value=["mcp"],
                        value=True,
                    ),
                ],
            ),
        ]
    )


def get_model_schema_modalities_section(input_formats: list[str], output_formats: list[str]) -> Sections:
    """Get the modalities section of the model schema."""
    return Sections(
        sections=[
            Section(
                title="Modalities & platform limits",
                description="Configure the supported modalities and attachment or token limits for this model.",
                fields=[
                    FieldSchema(
                        key="settings.input_formats",
                        label="Input formats",
                        description="Select the input formats supported by this model.",
                        type="select",
                        multiple=True,
                        options=[
                            Option(value=item, label=item)
                            for item in input_formats
                        ],
                    ),
                    FieldSchema(
                        key="settings.output_formats",
                        label="Output formats",
                        description="Select the output formats supported by this model.",
                        type="select",
                        multiple=True,
                        options=[
                            Option(value=item, label=item)
                            for item in output_formats
                        ],
                    ),
                    FieldSchema(
                        key="settings.input_token_limit",
                        label="Input token limit",
                        description="Maximum number of tokens allowed in the input.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.output_token_limit",
                        label="Output token limit",
                        description="Maximum number of tokens allowed in the output.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                ],
            ),
        ]
    )


def get_parameter_basic_schema(
    db,
    user_id,
    project_id: str | None = None,
    tool_names: list[str] | None = None,
    enabled_tools_value: list[str] | None = None,
    model_settings: dict | None = None,
):
    """Get basic parameter schema."""
    from app.users.models import get_user
    from app.groups.models import get_group
    from app.projects.models import get_project_with_access

    # Group
    user = get_user(db, user_id)
    group = get_group(db, user.group_id)
    group_settings = group.settings if isinstance(group.settings, dict) else {}
    group_context = group_settings.get("context", {})
    enable_group_context = bool(group_context.get("enable_group_context", False))

    # Project
    project_settings_system_instruction = ""
    if project_id:
        project = get_project_with_access(db, user_id, project_id)
        project_settings = project.settings if isinstance(project.settings, dict) else {}
        project_settings_system_instruction = project_settings.get("system_instruction", False)

    tool_options: list[Option] = []
    if tool_names:
        tool_options = [
            Option(
                value=name,
                label="MCP Servers" if name == "mcp" else str(name).replace("_", " ").title(),
                i18n_label=_tool_option_i18n_label(name),
            )
            for name in tool_names
        ]

    default_enabled_tools = enabled_tools_value if enabled_tools_value is not None else tool_names

    context_fields = [
        FieldSchema(
            key="settings.use_group_context",
            label="Use group context",
            description="Whether to use group context for this model.",
            type="boolean",
            required=False,
            value=True
        ),
        FieldSchema(
            key="settings.use_project_context",
            label="Use project context",
            description="Whether to use project context for this model.",
            type="boolean",
            required=True,
            value=True
        ),
    ]

    if tool_options:
        context_fields.append(
            FieldSchema(
                key="settings.enabled_tools",
                label="Enabled tools",
                description="Unselect tools to disable them for this conversation.",
                type="select",
                multiple=True,
                searchable=True,
                options=tool_options,
                required=False,
                value=default_enabled_tools,
            )
        )
        if any(option.value == "mcp" for option in tool_options):
            try:
                from app.mcp.utils import get_mcp_server_options_for_user

                mcp_server_options = [
                    Option(value=item["value"], label=item["label"])
                    for item in get_mcp_server_options_for_user(
                        db,
                        user_id,
                        model_settings=model_settings,
                    )
                ]
            except Exception:
                mcp_server_options = []
            if mcp_server_options:
                context_fields.append(
                    FieldSchema(
                        key="settings.enabled_mcp_servers",
                        label="Enabled MCP servers",
                        # MCP access is opt-in for every request. The chat
                        # mention menu and this selector both populate this
                        # explicit allowlist; an empty list exposes no server.
                        description="Choose the MCP servers available for the next request. Servers are disabled by default.",
                        type="select",
                        multiple=True,
                        options=mcp_server_options,
                        required=False,
                        value=[],
                        dependency="settings.enabled_tools",
                        dependency_value=["mcp"],
                    )
                )

    schema = Sections(
        sections=[
            Section(
                title="Model Context",
                description="Decide which context the model should get.",
                fields=[
                    FieldSchema(
                        key="system_instruction",
                        label="System Instruction",
                        i18n_label="model_settings_system_instruction_label",
                        description="Replace the administrator-configured system instruction for this conversation.",
                        i18n_description="model_settings_system_instruction_description",
                        type="string",
                        input_type="textarea",
                        required=False,
                        placeholder="Leave empty to use the administrator-configured system instruction.",
                        i18n_placeholder="model_settings_system_instruction_placeholder",
                        value="",
                    ),
                    *context_fields,
                ],
            )
        ]
    )
    
    if not enable_group_context:
        # Remove the field from the schema
        _remove_field_from_section(schema.sections, "Model Context", "settings.use_group_context")
    
    if not project_id:
        _remove_field_from_section(schema.sections, "Model Context", "settings.use_project_context")

    return schema
