"""Schemas and field definitions for the administrator Groups page."""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, model_validator
from datetime import datetime

from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.timezones import COMMON_TIMEZONES
from app.utils.schemas import FieldSchema, Section, Sections



# -------------------
# Group form schema response
# -------------------
class GroupFormSchema(Sections):
    """Schema definition (sections + fields) for group create/edit forms."""


class GroupManagerCandidateOption(BaseModel):
    """One eligible user option returned to the remote manager picker."""

    value: str
    label: str


class GroupManagerCandidatePage(BaseModel):
    """Bounded, server-searchable options for administrator role assignment."""

    options: list[GroupManagerCandidateOption]
    offset: int
    limit: int
    total: int
    has_more: bool


def _default_setting(path: str) -> Any:
    """Return default value for a dotted path within DEFAULT_GROUP_SETTINGS."""
    current: Any = DEFAULT_GROUP_SETTINGS
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _timezone_select_options() -> list[dict[str, str]]:
    """Build stable select options for every supported access-window timezone."""
    return [
        {"value": timezone_name, "label": timezone_name, "i18n_label": timezone_name}
        for timezone_name in COMMON_TIMEZONES
    ]


GROUP_FORM_SCHEMA = GroupFormSchema(
    sections=[
        Section(
            key="general",
            title="General",
            description="Core information used to identify and organize this group.",
            i18n_title="schema_group_sec_general_title",
            i18n_description="schema_group_sec_general_desc",
            fields=[
                FieldSchema(
                    key="name",
                    label="Group name",
                    i18n_label="schema_group_field_name_label",
                    description="Name shown for this group throughout the admin and user interfaces.",
                    i18n_description="schema_group_field_name_desc",
                    type="string",
                    placeholder="E.g. Marketing Europe",
                    i18n_placeholder="schema_group_field_name_placeholder",
                ),
                FieldSchema(
                    key="parent_id",
                    label="Parent group",
                    i18n_label="schema_group_field_parent_id_label",
                    description="Optional parent group used for hierarchy and delegated management scope. Settings remain independent.",
                    i18n_description="schema_group_field_parent_id_desc",
                    type="select",
                    options=[],
                    default="",
                ),
            ],
        ),
        Section(
            key="management",
            title="Management",
            description="Assign the users who may administer this group and its descendants.",
            i18n_title="schema_group_sec_management_title",
            i18n_description="schema_group_sec_management_desc",
            fields=[
                FieldSchema(
                    key="owner_user_ids",
                    label="Owners",
                    i18n_label="schema_group_field_owner_user_ids_label",
                    description="Owners can view members, promote them to higher group roles, and manage settings and temporary accounts.",
                    i18n_description="schema_group_field_owner_user_ids_desc",
                    type="select",
                    multiple=True,
                    searchable=True,
                    metadata={
                        "remote_options": {
                            "url": "/api/v1/groups/manager-candidates",
                            "limit": 100,
                        }
                    },
                    options=[],
                    default=[],
                ),
                FieldSchema(
                    key="manager_user_ids",
                    label="Managers",
                    i18n_label="schema_group_field_manager_user_ids_label",
                    description="Managers can view members and manage settings and temporary accounts, but cannot promote members.",
                    i18n_description="schema_group_field_manager_user_ids_desc",
                    type="select",
                    multiple=True,
                    searchable=True,
                    metadata={
                        "remote_options": {
                            "url": "/api/v1/groups/manager-candidates",
                            "limit": 100,
                        }
                    },
                    options=[],
                    default=[],
                ),
                FieldSchema(
                    key="coordinator_user_ids",
                    label="Coordinators",
                    i18n_label="schema_group_field_coordinator_user_ids_label",
                    description="Coordinators can view members and manage temporary accounts without changing group settings.",
                    i18n_description="schema_group_field_coordinator_user_ids_desc",
                    type="select",
                    multiple=True,
                    searchable=True,
                    metadata={
                        "remote_options": {
                            "url": "/api/v1/groups/manager-candidates",
                            "limit": 100,
                        }
                    },
                    options=[],
                    default=[],
                ),
                FieldSchema(
                    key="settings.temporary_accounts.enabled",
                    label="Allow temporary accounts",
                    i18n_label="schema_group_field_settings_temporary_accounts_enabled_label",
                    description="Permit delegated managers to create temporary accounts for this group.",
                    i18n_description="schema_group_field_settings_temporary_accounts_enabled_description",
                    type="boolean",
                    default=_default_setting("temporary_accounts.enabled"),
                ),
                FieldSchema(
                    key="settings.temporary_accounts.max_active_accounts",
                    label="Max active temporary accounts",
                    i18n_label="schema_group_field_settings_temporary_accounts_max_active_accounts_label",
                    description="Upper bound for simultaneously active temporary accounts in this group.",
                    i18n_description="schema_group_field_settings_temporary_accounts_max_active_accounts_description",
                    type="number",
                    attributes={"min": 1, "max": 1000, "step": 1},
                    dependency="settings.temporary_accounts.enabled",
                    dependency_value=True,
                    default=_default_setting("temporary_accounts.max_active_accounts"),
                ),
                FieldSchema(
                    key="settings.temporary_accounts.credential_length",
                    label="Credential length",
                    i18n_label="schema_group_field_settings_temporary_accounts_credential_length_label",
                    description="Length of generated PIN-style credentials for temporary accounts.",
                    i18n_description="schema_group_field_settings_temporary_accounts_credential_length_description",
                    type="number",
                    attributes={"min": 16, "max": 64, "step": 1},
                    dependency="settings.temporary_accounts.enabled",
                    dependency_value=True,
                    default=_default_setting("temporary_accounts.credential_length"),
                ),
            ],
        ),
        Section(
            key="skills",
            title="Skills",
            description="Configure skill access and sharing for this group.",
            i18n_title="schema_group_sec_skills_title",
            i18n_description="schema_group_sec_skills_desc",
            fields=[
                FieldSchema(
                    key="settings.skills.enabled_skills",
                    label="Enable skills",
                    i18n_label="schema_group_field_settings_skills_enabled_skills_label",
                    description="Allow members of this group to browse and use skills.",
                    i18n_description="schema_group_field_settings_skills_enabled_skills_description",
                    type="boolean",
                    default=_default_setting("skills.enabled_skills"),
                ),
                FieldSchema(
                    key="settings.skills.allow_skill_share",
                    label="Enable skill sharing",
                    i18n_label="schema_group_field_settings_skills_allow_skill_share_label",
                    description="Allow members to share skills with others.",
                    i18n_description="schema_group_field_settings_skills_allow_skill_share_description",
                    type="boolean",
                    dependency="settings.skills.enabled_skills",
                    dependency_value=True,
                    default=_default_setting("skills.allow_skill_share"),
                ),
                FieldSchema(
                    key="settings.skills.admin_skill_ids",
                    label="Managed skills",
                    i18n_label="schema_group_field_settings_skills_admin_skill_ids_label",
                    description="Choose the managed skills that should be available to this group.",
                    i18n_description="schema_group_field_settings_skills_admin_skill_ids_description",
                    type="select",
                    multiple=True,
                    options=[],
                    dependency="settings.skills.enabled_skills",
                    dependency_value=True,
                    default=_default_setting("skills.admin_skill_ids"),
                ),
            ],
        ),
        Section(
            key="projects",
            title="Projects",
            description="Configure collaborative project workspaces for this group.",
            i18n_title="schema_group_sec_projects_title",
            i18n_description="schema_group_sec_projects_desc",
            fields=[
                FieldSchema(
                    key="settings.projects.enable_projects",
                    label="Enable projects",
                    i18n_label="schema_group_field_settings_projects_enable_projects_label",
                    description="Allow members of this group to work in shared projects.",
                    i18n_description="schema_group_field_settings_projects_enable_projects_description",
                    type="boolean",
                    default=_default_setting("projects.enable_projects"),
                ),
                FieldSchema(
                    key="settings.projects.allow_project_share",
                    label="Allow project sharing",
                    i18n_label="schema_group_field_settings_projects_allow_project_share_label",
                    description="Allow members to share projects with others.",
                    i18n_description="schema_group_field_settings_projects_allow_project_share_description",
                    type="boolean",
                    dependency="settings.projects.enable_projects",
                    dependency_value=True,
                    default=_default_setting("projects.allow_project_share"),
                ),
            ],
        ),
        Section(
            key="automations",
            title="Automations",
            description="Control scheduled automation features for this group.",
            i18n_title="schema_group_sec_automations_title",
            i18n_description="schema_group_sec_automations_desc",
            fields=[
                FieldSchema(
                    key="settings.automations.enabled_automations",
                    label="Enable automations",
                    i18n_label="schema_group_field_settings_automations_enabled_automations_label",
                    description="Allow members of this group to create and manage automations.",
                    i18n_description="schema_group_field_settings_automations_enabled_automations_description",
                    type="boolean",
                    default=_default_setting("automations.enabled_automations"),
                ),
            ],
        ),
        Section(
            key="todo",
            title="Todo lists",
            description="Configure shared todo lists for this group.",
            i18n_title="schema_group_sec_todo_title",
            i18n_description="schema_group_sec_todo_desc",
            fields=[
                FieldSchema(
                    key="settings.todo.enabled_todo",
                    label="Enable todo lists",
                    i18n_label="schema_group_field_settings_todo_enabled_todo_label",
                    description="Allow members of this group to use todo lists.",
                    i18n_description="schema_group_field_settings_todo_enabled_todo_description",
                    type="boolean",
                    default=_default_setting("todo.enabled_todo"),
                ),
                FieldSchema(
                    key="settings.todo.allow_todo_list_share",
                    label="Allow todo list sharing",
                    i18n_label="schema_group_field_settings_todo_allow_todo_list_share_label",
                    description="Allow members to share todo lists with others.",
                    i18n_description="schema_group_field_settings_todo_allow_todo_list_share_description",
                    type="boolean",
                    dependency="settings.todo.enabled_todo",
                    dependency_value=True,
                    default=_default_setting("todo.allow_todo_list_share"),
                ),
            ],
        ),
        Section(
            key="notes",
            title="Notes",
            description="Control notes and note sharing for this group.",
            i18n_title="schema_group_sec_notes_title",
            i18n_description="schema_group_sec_notes_desc",
            fields=[
                FieldSchema(
                    key="settings.notes.enabled_notes",
                    label="Enable notes",
                    i18n_label="schema_group_field_settings_notes_enabled_notes_label",
                    description="Allow members of this group to create and manage notes.",
                    i18n_description="schema_group_field_settings_notes_enabled_notes_description",
                    type="boolean",
                    default=_default_setting("notes.enabled_notes"),
                ),
                FieldSchema(
                    key="settings.notes.allow_notes_share",
                    label="Allow note sharing",
                    i18n_label="schema_group_field_settings_notes_allow_notes_share_label",
                    description="Allow members to share notes with others.",
                    i18n_description="schema_group_field_settings_notes_allow_notes_share_description",
                    type="boolean",
                    dependency="settings.notes.enabled_notes",
                    dependency_value=True,
                    default=_default_setting("notes.allow_notes_share"),
                ),
            ],
        ),
        Section(
            key="memories",
            title="Memories",
            description="Manage memory features for this group.",
            i18n_title="schema_group_sec_memories_title",
            i18n_description="schema_group_sec_memories_desc",
            fields=[
                FieldSchema(
                    key="settings.memories.enabled_memories",
                    label="Enable memories",
                    i18n_label="schema_group_field_settings_memories_enabled_memories_label",
                    description="Allow members to store memories for future personalization.",
                    i18n_description="schema_group_field_settings_memories_enabled_memories_description",
                    type="boolean",
                    default=_default_setting("memories.enabled_memories"),
                ),
                FieldSchema(
                    key="settings.memories.memory_model_id",
                    label="Memory model",
                    i18n_label="schema_group_field_settings_memories_memory_model_id_label",
                    description="Dedicated model used to update member memories after each user message. Leave empty to use the current chat model.",
                    i18n_description="schema_group_field_settings_memories_memory_model_id_description",
                    type="select",
                    options=[],
                    dependency="settings.memories.enabled_memories",
                    dependency_value=True,
                    default=_default_setting("memories.memory_model_id"),
                ),
            ],
        ),
        Section(
            key="prompts",
            title="Prompt library",
            description="Configure reusable prompts and prompt sharing.",
            i18n_title="schema_group_sec_prompts_title",
            i18n_description="schema_group_sec_prompts_desc",
            fields=[
                FieldSchema(
                    key="settings.prompts.enabled_prompts",
                    label="Enable prompt library",
                    i18n_label="schema_group_field_settings_prompts_enabled_prompts_label",
                    description="Allow members to create and use reusable prompts.",
                    i18n_description="schema_group_field_settings_prompts_enabled_prompts_description",
                    type="boolean",
                    default=_default_setting("prompts.enabled_prompts"),
                ),
                FieldSchema(
                    key="settings.prompts.allow_prompt_share",
                    label="Allow prompt sharing",
                    i18n_label="schema_group_field_settings_prompts_allow_prompt_share_label",
                    description="Allow members to share prompts with others.",
                    i18n_description="schema_group_field_settings_prompts_allow_prompt_share_description",
                    type="boolean",
                    dependency="settings.prompts.enabled_prompts",
                    dependency_value=True,
                    default=_default_setting("prompts.allow_prompt_share"),
                ),
            ],
        ),
        Section(
            key="bookmarks",
            title="Bookmarks",
            i18n_title="schema_group_sec_bookmarks_title",
            description="Control saved message bookmarks and bookmark sharing from the workspace.",
            i18n_description="schema_group_sec_bookmarks_desc",
            fields=[
                FieldSchema(
                    key="settings.bookmarks.enabled_bookmarks",
                    label="Enable bookmarks",
                    i18n_label="schema_group_field_settings_bookmarks_enabled_bookmarks_label",
                    description="Allow members to bookmark chat messages and browse them in the workspace.",
                    i18n_description="schema_group_field_settings_bookmarks_enabled_bookmarks_description",
                    type="boolean",
                    default=_default_setting("bookmarks.enabled_bookmarks"),
                ),
                FieldSchema(
                    key="settings.bookmarks.allow_bookmark_share",
                    label="Allow bookmark sharing",
                    i18n_label="schema_group_field_settings_bookmarks_allow_bookmark_share_label",
                    description="Allow members to open sharing flows for chats from bookmarked messages.",
                    i18n_description="schema_group_field_settings_bookmarks_allow_bookmark_share_description",
                    type="boolean",
                    dependency="settings.bookmarks.enabled_bookmarks",
                    dependency_value=True,
                    default=_default_setting("bookmarks.allow_bookmark_share"),
                ),
            ],
        ),
        Section(
            key="agents",
            title="Agents",
            i18n_title="schema_group_sec_agents_title",
            description="Control access to custom agents and whether they can be shared.",
            i18n_description="schema_group_sec_agents_desc",
            fields=[
                FieldSchema(
                    key="settings.agents.allow_agents",
                    label="Allow agents",
                    i18n_label="schema_group_field_settings_agents_allow_agents_label",
                    description="Allow members to create, use, and access custom agents.",
                    i18n_description="schema_group_field_settings_agents_allow_agents_description",
                    type="boolean",
                    default=_default_setting("agents.allow_agents"),
                ),
                FieldSchema(
                    key="settings.agents.allow_agent_share",
                    label="Allow agent sharing",
                    i18n_label="schema_group_field_settings_agents_allow_agent_share_label",
                    description="Allow members to create agent share links and invitations.",
                    i18n_description="schema_group_field_settings_agents_allow_agent_share_description",
                    type="boolean",
                    dependency="settings.agents.allow_agents",
                    dependency_value=True,
                    default=_default_setting("agents.allow_agent_share"),
                ),
            ],
        ),
        Section(
            key="byok",
            title="BYOK",
            description="Configure bring-your-own-key access and defaults for this group.",
            i18n_title="schema_group_sec_byok_title",
            i18n_description="schema_group_sec_byok_desc",
            fields=[
                FieldSchema(
                    key="settings.chat.allow_byok",
                    label="Enable BYOK",
                    i18n_label="schema_group_field_settings_chat_allow_byok_label",
                    description="Allow members to connect their own provider credentials.",
                    i18n_description="schema_group_field_settings_chat_allow_byok_description",
                    type="boolean",
                    default=_default_setting("chat.allow_byok"),
                ),
                FieldSchema(
                    key="settings.chat.byok_title_generation_model_id",
                    label="BYOK title generation model",
                    i18n_label="schema_group_field_settings_chat_byok_title_generation_model_id_label",
                    description="Admin-managed model used to generate titles for BYOK chats.",
                    i18n_description="schema_group_field_settings_chat_byok_title_generation_model_id_description",
                    type="select",
                    options=[],
                    dependency="settings.chat.allow_byok",
                    dependency_value=True,
                    default=_default_setting("chat.byok_title_generation_model_id"),
                ),
                FieldSchema(
                    key="settings.chat.byok_allowed_tools",
                    label="Allowed BYOK tools",
                    i18n_label="schema_group_field_settings_chat_byok_allowed_tools_label",
                    description="Choose which tools members can expose when they run BYOK models.",
                    i18n_description="schema_group_field_settings_chat_byok_allowed_tools_description",
                    type="select",
                    multiple=True,
                    options=[],
                    dependency="settings.chat.allow_byok",
                    dependency_value=True,
                    default=_default_setting("chat.byok_allowed_tools"),
                ),
                FieldSchema(
                    key="settings.chat.byok_default_scrape_provider",
                    label="Default BYOK scrape provider",
                    i18n_label="schema_group_field_settings_chat_byok_default_scrape_provider_label",
                    description="Provider preselected by default for BYOK scraping jobs.",
                    i18n_description="schema_group_field_settings_chat_byok_default_scrape_provider_description",
                    type="select",
                    options=[],
                    dependency="settings.chat.allow_byok",
                    dependency_value=True,
                    # Provider defaults are useful only when the BYOK model
                    # can call the web-search tool. Keep this as a second
                    # dependency so the generic group-form renderer hides
                    # the complete row when that tool is not allowed.
                    dependency2="settings.chat.byok_allowed_tools",
                    dependency2_value="web_search",
                    default=_default_setting("chat.byok_default_scrape_provider"),
                ),
                FieldSchema(
                    key="settings.chat.byok_default_search_provider",
                    label="Default BYOK search provider",
                    i18n_label="schema_group_field_settings_chat_byok_default_search_provider_label",
                    description="Provider preselected by default for BYOK search jobs.",
                    i18n_description="schema_group_field_settings_chat_byok_default_search_provider_description",
                    type="select",
                    options=[],
                    dependency="settings.chat.allow_byok",
                    dependency_value=True,
                    # Keep the search provider in lockstep with the scrape
                    # provider: both defaults belong to the web-search tool
                    # and should not be shown for other BYOK tools.
                    dependency2="settings.chat.byok_allowed_tools",
                    dependency2_value="web_search",
                    default=_default_setting("chat.byok_default_search_provider"),
                ),
            ],
        ),
        Section(
            key="sharing",
            title="Sharing permissions",
            description="Control which collaboration surfaces can be shared externally.",
            i18n_title="schema_group_sec_sharing_title",
            i18n_description="schema_group_sec_sharing_desc",
            fields=[
                FieldSchema(
                    key="settings.sharing.enable_chat_sharing",
                    label="Allow chat sharing",
                    i18n_label="schema_group_field_settings_sharing_enable_chat_sharing_label",
                    description="Permit group members to share or copy chat transcripts.",
                    i18n_description="schema_group_field_settings_sharing_enable_chat_sharing_description",
                    type="boolean",
                    default=_default_setting("sharing.enable_chat_sharing"),
                ),
                FieldSchema(
                    key="settings.sharing.enable_artifact_sharing",
                    label="Allow artifact sharing",
                    i18n_label="schema_group_field_settings_sharing_enable_artifact_sharing_label",
                    description="Enable sharing of generated artifacts (code, charts, etc.).",
                    i18n_description="schema_group_field_settings_sharing_enable_artifact_sharing_description",
                    type="boolean",
                    default=_default_setting("sharing.enable_artifact_sharing"),
                ),
            ],
        ),
        Section(
            key="chat",
            title="Chat experience",
            description="Controls for chat safety, lifecycle, and storage.",
            i18n_title="schema_group_sec_chat_title",
            i18n_description="schema_group_sec_chat_desc",
            fields=[
                FieldSchema(
                    key="settings.chat.allow_temporary_chat",
                    label="Allow temporary chats",
                    description="Allow members to start chats in temporary mode.",
                    type="boolean",
                    i18n_label="schema_group_chat_allow_temporary_chat_label",
                    i18n_description="schema_group_chat_allow_temporary_chat_desc",
                    default=_default_setting("chat.allow_temporary_chat"),
                ),
                FieldSchema(
                    key="settings.chat.save_temp_chats",
                    label="Persist temporary chats",
                    i18n_label="schema_group_field_settings_chat_save_temp_chats_label",
                    description="Store temporary chats instead of dropping them after refresh.",
                    i18n_description="schema_group_field_settings_chat_save_temp_chats_description",
                    type="boolean",
                    dependency="settings.chat.allow_temporary_chat",
                    dependency_value=True,
                    default=_default_setting("chat.save_temp_chats"),
                ),
                FieldSchema(
                    key="settings.chat.save_temp_chats_retention_enabled",
                    label="Enable temporary chat retention",
                    description="Automatically delete saved temporary chats after a retention window.",
                    type="boolean",
                    dependency="settings.chat.save_temp_chats",
                    dependency_value=True,
                    dependency2="settings.chat.allow_temporary_chat",
                    dependency2_value=True,
                    i18n_label="schema_group_chat_temp_retention_enabled_label",
                    i18n_description="schema_group_chat_temp_retention_enabled_desc",
                    default=_default_setting("chat.save_temp_chats_retention_enabled"),
                ),
                FieldSchema(
                    key="settings.chat.save_temp_chats_retention_days",
                    label="Temporary chat retention (days)",
                    description="Number of days to keep saved temporary chats before deletion.",
                    type="number",
                    attributes={"min": 1},
                    dependency="settings.chat.save_temp_chats_retention_enabled",
                    dependency_value=True,
                    dependency2="settings.chat.save_temp_chats",
                    dependency2_value=True,
                    i18n_label="schema_group_chat_temp_retention_days_label",
                    i18n_description="schema_group_chat_temp_retention_days_desc",
                    default=_default_setting("chat.save_temp_chats_retention_days"),
                ),
                FieldSchema(
                    key="settings.chat.allow_regenerate_response",
                    label="Allow regenerate response",
                    description="Allow users to regenerate assistant responses for the latest prompt.",
                    type="boolean",
                    i18n_label="schema_group_field_settings_chat_allow_regenerate_response_label",
                    i18n_description="schema_group_field_settings_chat_allow_regenerate_response_desc",
                    default=_default_setting("chat.allow_regenerate_response"),
                ),
                FieldSchema(
                    key="settings.chat.allow_rate_response",
                    label="Allow rate response",
                    description="Allow users to submit thumbs up or thumbs down feedback on assistant responses.",
                    type="boolean",
                    i18n_label="schema_group_field_settings_chat_allow_rate_response_label",
                    i18n_description="schema_group_field_settings_chat_allow_rate_response_desc",
                    default=_default_setting("chat.allow_rate_response"),
                ),
                FieldSchema(
                    key="settings.chat.allow_delete_messages",
                    label="Allow delete messages",
                    description="Allow users to delete individual chat messages from a conversation.",
                    type="boolean",
                    i18n_label="schema_group_field_settings_chat_allow_delete_messages_label",
                    i18n_description="schema_group_field_settings_chat_allow_delete_messages_desc",
                    default=_default_setting("chat.allow_delete_messages"),
                ),
                FieldSchema(
                    key="settings.chat.auto_delete_chats",
                    label="Auto delete chats",
                    i18n_label="schema_group_field_settings_chat_auto_delete_chats_label",
                    description="Automatically purge chats after the configured number of days.",
                    i18n_description="schema_group_field_settings_chat_auto_delete_chats_description",
                    type="boolean",
                    default=_default_setting("chat.auto_delete_chats"),
                ),
                FieldSchema(
                    key="settings.chat.auto_delete_chats_days",
                    label="Auto delete after (days)",
                    i18n_label="schema_group_field_settings_chat_auto_delete_chats_days_label",
                    description="Number of days to retain chats when auto delete is enabled.",
                    i18n_description="schema_group_field_settings_chat_auto_delete_chats_days_description",
                    type="number",
                    attributes={"min": 1},
                    dependency="settings.chat.auto_delete_chats",
                    dependency_value=True,
                    default=_default_setting("chat.auto_delete_chats_days"),
                ),
                FieldSchema(
                    key="settings.chat.allow_chat_deletion",
                    label="Allow manual chat deletion",
                    i18n_label="schema_group_field_settings_chat_allow_chat_deletion_label",
                    description="Allow members to delete their own chats manually.",
                    i18n_description="schema_group_field_settings_chat_allow_chat_deletion_description",
                    type="boolean",
                    default=_default_setting("chat.allow_chat_deletion"),
                ),
                FieldSchema(
                    key="settings.chat.shadow_chat_deletion",
                    label="Shadow delete chats",
                    i18n_label="schema_group_field_settings_chat_shadow_chat_deletion_label",
                    description="Perform soft deletes so admins can recover mistakes.",
                    i18n_description="schema_group_field_settings_chat_shadow_chat_deletion_description",
                    type="boolean",
                    default=_default_setting("chat.shadow_chat_deletion"),
                ),
                FieldSchema(
                    key="settings.chat.shadow_chat_deletion_retention_enabled",
                    label="Enable shadow deletion retention",
                    description="Automatically hard delete shadow-deleted chats after a retention window.",
                    type="boolean",
                    dependency="settings.chat.shadow_chat_deletion",
                    dependency_value=True,
                    i18n_label="schema_group_chat_shadow_deletion_retention_enabled_label",
                    i18n_description="schema_group_chat_shadow_deletion_retention_enabled_desc",
                    default=_default_setting("chat.shadow_chat_deletion_retention_enabled"),
                ),
                FieldSchema(
                    key="settings.chat.shadow_chat_deletion_retention_days",
                    label="Shadow deletion retention (days)",
                    description="Number of days to keep shadow-deleted chats before permanent deletion.",
                    type="number",
                    attributes={"min": 1},
                    dependency="settings.chat.shadow_chat_deletion_retention_enabled",
                    dependency_value=True,
                    dependency2="settings.chat.shadow_chat_deletion",
                    dependency2_value=True,
                    i18n_label="schema_group_chat_shadow_deletion_retention_days_label",
                    i18n_description="schema_group_chat_shadow_deletion_retention_days_desc",
                    default=_default_setting("chat.shadow_chat_deletion_retention_days"),
                ),
            ],
        ),
        Section(
            key="context",
            title="Context enrichment",
            description="Give the assistant more context for this group.",
            i18n_title="schema_group_sec_context_title",
            i18n_description="schema_group_sec_context_desc",
            fields=[
                FieldSchema(
                    key="settings.context.enable_group_context",
                    label="Enable group context",
                    i18n_label="schema_group_field_settings_context_enable_group_context_label",
                    description="Attach additional context to every conversation.",
                    i18n_description="schema_group_field_settings_context_enable_group_context_description",
                    type="boolean",
                    default=_default_setting("context.enable_group_context"),
                ),
                FieldSchema(
                    key="settings.context.group_context",
                    label="Context instructions",
                    i18n_label="schema_group_field_settings_context_group_context_label",
                    description="Rich text or markdown appended when context is enabled.",
                    i18n_description="schema_group_field_settings_context_group_context_description",
                    placeholder="Add context for this group",
                    i18n_placeholder="schema_group_field_settings_context_group_context_placeholder",
                    type="string",
                    input_type="text",
                    dependency="settings.context.enable_group_context",
                    dependency_value=True,
                    default=_default_setting("context.group_context"),
                ),
                FieldSchema(
                    key="settings.context.group_context_file_ids",
                    label="Context files",
                    i18n_label="schema_group_field_settings_context_group_context_file_ids_label",
                    description="Upload files to provide additional context for this group's conversations.",
                    i18n_description="schema_group_field_settings_context_group_context_file_ids_description",
                    type="context_files",
                    dependency="settings.context.enable_group_context",
                    dependency_value=True,
                    default=_default_setting("context.group_context_file_ids"),
                ),
            ],
        ),
        Section(
            key="data_controls",
            title="Data controls",
            description="Control complete account data export and restoration.",
            i18n_title="schema_group_sec_data_controls_title",
            i18n_description="schema_group_sec_data_controls_desc",
            fields=[
                FieldSchema(
                    key="settings.data_controls.allow_user_data",
                    label="Allow user data",
                    i18n_label="schema_group_field_settings_data_controls_allow_user_data_label",
                    description="Allow members to export and restore their user data.",
                    i18n_description="schema_group_field_settings_data_controls_allow_user_data_description",
                    type="boolean",
                    default=_default_setting("data_controls.allow_user_data"),
                ),
            ],
        ),
        Section(
            key="files",
            title="File storage",
            description="Limits and toggles for personal file storage.",
            i18n_title="schema_group_sec_files_title",
            i18n_description="schema_group_sec_files_desc",
            fields=[
                FieldSchema(
                    key="settings.files.allow_file_uploads",
                    label="Allow file uploads",
                    i18n_label="schema_group_field_settings_files_allow_file_uploads_label",
                    description="Permit upload + attachment of files inside chats.",
                    i18n_description="schema_group_field_settings_files_allow_file_uploads_description",
                    type="boolean",
                    default=_default_setting("files.allow_file_uploads"),
                ),
                FieldSchema(
                    key="settings.files.max_files_upload_count",
                    label="Max files per upload",
                    i18n_label="schema_group_field_settings_files_max_files_upload_count_label",
                    description="Maximum number of files per chat message.",
                    i18n_description="schema_group_field_settings_files_max_files_upload_count_description",
                    type="number",
                    attributes={"min": 1},
                    dependency="settings.files.allow_file_uploads",
                    dependency_value=True,
                    default=_default_setting("files.max_files_upload_count"),
                ),
                FieldSchema(
                    key="settings.files.max_user_files_size_gb",
                    label="Per-user storage (GB)",
                    i18n_label="schema_group_field_settings_files_max_user_files_size_gb_label",
                    description="Cap total storage per user within this group.",
                    i18n_description="schema_group_field_settings_files_max_user_files_size_gb_description",
                    type="number",
                    attributes={"min": 1, "max": 100},
                    dependency="settings.files.allow_file_uploads",
                    dependency_value=True,
                    default=_default_setting("files.max_user_files_size_gb"),
                ),
            ],
        ),
        Section(
            key="users",
            title="User permissions",
            description="Profile customization capabilities offered to members.",
            i18n_title="schema_group_sec_users_title",
            i18n_description="schema_group_sec_users_desc",
            fields=[
                FieldSchema(
                    key="settings.users.enable_custom_profile_picture",
                    label="Allow custom profile pictures",
                    i18n_label="schema_group_field_settings_users_enable_custom_profile_picture_label",
                    description="Enable avatar uploads for members of this group.",
                    i18n_description="schema_group_field_settings_users_enable_custom_profile_picture_description",
                    type="boolean",
                    default=_default_setting("users.enable_custom_profile_picture"),
                ),
                FieldSchema(
                    key="settings.users.allow_change_name",
                    label="Allow changing display name",
                    i18n_label="schema_group_field_settings_users_allow_change_name_label",
                    description="Members can update their profile name.",
                    i18n_description="schema_group_field_settings_users_allow_change_name_description",
                    type="boolean",
                    default=_default_setting("users.allow_change_name"),
                ),
                FieldSchema(
                    key="settings.users.allow_change_email",
                    label="Allow changing email",
                    i18n_label="schema_group_field_settings_users_allow_change_email_label",
                    description="Members can update their login email address.",
                    i18n_description="schema_group_field_settings_users_allow_change_email_description",
                    type="boolean",
                    default=_default_setting("users.allow_change_email"),
                ),
                FieldSchema(
                    key="settings.users.allow_change_password",
                    label="Allow password changes",
                    i18n_label="schema_group_field_settings_users_allow_change_password_label",
                    description="Expose the password reset UI within settings.",
                    i18n_description="schema_group_field_settings_users_allow_change_password_description",
                    type="boolean",
                    default=_default_setting("users.allow_change_password"),
                ),
                FieldSchema(
                    key="settings.users.allow_self_deletion",
                    label="Allow self-account deletion",
                    i18n_label="schema_group_field_settings_users_allow_self_deletion_label",
                    description="Permit members to delete their own account records.",
                    i18n_description="schema_group_field_settings_users_allow_self_deletion_description",
                    type="boolean",
                    default=_default_setting("users.allow_self_deletion"),
                ),
            ],
        ),
        Section(
            key="connections",
            title="Connections",
            description="Control connection-backed tooling for this group.",
            i18n_title="schema_group_sec_connections_title",
            i18n_description="schema_group_sec_connections_desc",
            fields=[
                FieldSchema(
                    key="settings.tools_mcp.enable_mcp",
                    label="Enable personal MCP servers",
                    i18n_label="schema_group_field_settings_tools_mcp_enable_mcp_label",
                    description="Allow members to create and use their own MCP servers.",
                    i18n_description="schema_group_field_settings_tools_mcp_enable_mcp_description",
                    type="boolean",
                    default=_default_setting("tools_mcp.enable_mcp"),
                ),
                FieldSchema(
                    key="settings.tools_mcp.allow_file_storage_connections",
                    label="Allow file storage connections",
                    i18n_label="schema_group_field_settings_tools_mcp_allow_file_storage_connections_label",
                    description="Explicitly opt in to Google Drive connections for this group.",
                    i18n_description="schema_group_field_settings_tools_mcp_allow_file_storage_connections_description",
                    type="boolean",
                    default=_default_setting("tools_mcp.allow_file_storage_connections"),
                ),
                FieldSchema(
                    key="settings.tools_mcp.enabled_connections",
                    label="Enabled workspace connections",
                    i18n_label="schema_group_field_settings_tools_mcp_enabled_connections_label",
                    description="Choose which workspace connections this group can access. Leave empty to disable connections for this group.",
                    i18n_description="schema_group_field_settings_tools_mcp_enabled_connections_description",
                    type="select",
                    multiple=True,
                    options=[
                        {"value": "notion", "label": "Notion", "i18n_label": "schema_group_option_settings_tools_mcp_enabled_connections_notion"},
                        {"value": "github", "label": "GitHub", "i18n_label": "schema_group_option_settings_tools_mcp_enabled_connections_github"},
                        {"value": "gmail", "label": "Gmail", "i18n_label": "schema_group_option_settings_tools_mcp_enabled_connections_gmail"},
                        {"value": "google_calendar", "label": "Google Calendar", "i18n_label": "schema_group_option_settings_tools_mcp_enabled_connections_google_calendar"},
                        {"value": "google_drive", "label": "Google Drive", "i18n_label": "schema_group_option_settings_tools_mcp_enabled_connections_google_drive"},
                        {"value": "slack", "label": "Slack", "i18n_label": "schema_group_option_settings_tools_mcp_enabled_connections_slack"},
                    ],
                    default=_default_setting("tools_mcp.enabled_connections"),
                ),
            ],
        ),
        Section(
            key="leaderboard",
            title="Leaderboard",
            i18n_title="schema_group_sec_leaderboard_title",
            description="Control access to the LLM leaderboard experience.",
            i18n_description="schema_group_sec_leaderboard_desc",
            fields=[
                FieldSchema(
                    key="settings.leaderboard.enabled",
                    label="Enable leaderboard",
                    i18n_label="schema_group_field_settings_leaderboard_enabled_label",
                    description="Toggle access to the Artificial Analysis leaderboard for this group.",
                    i18n_description="schema_group_field_settings_leaderboard_enabled_description",
                    type="boolean",
                    default=_default_setting("leaderboard.enabled"),
                ),
                FieldSchema(
                    key="settings.leaderboard.artificial_analysis_data_level",
                    label="Artificial Analysis data level",
                    i18n_label="schema_group_field_settings_leaderboard_artificial_analysis_data_level_label",
                    description="Choose the Free dataset or the expanded model data available with a Pro or Commercial API key.",
                    i18n_description="schema_group_field_settings_leaderboard_artificial_analysis_data_level_description",
                    type="select",
                    options=[
                        {
                            "value": "free",
                            "label": "Free",
                            "i18n_label": "schema_group_option_settings_leaderboard_artificial_analysis_data_level_free",
                        },
                        {
                            "value": "full",
                            "label": "Full model data (Pro or Commercial)",
                            "i18n_label": "schema_group_option_settings_leaderboard_artificial_analysis_data_level_full",
                        },
                    ],
                    dependency="settings.leaderboard.enabled",
                    dependency_value=True,
                    default=_default_setting(
                        "leaderboard.artificial_analysis_data_level"
                    ),
                ),
                FieldSchema(
                    key="settings.leaderboard.artificial_analysis_api_key",
                    label="Artificial Analysis API key",
                    i18n_label="schema_group_field_settings_leaderboard_artificial_analysis_api_key_label",
                    description="Secret used for Artificial Analysis leaderboard calls.",
                    i18n_description="schema_group_field_settings_leaderboard_artificial_analysis_api_key_description",
                    type="string",
                    input_type="password",
                    placeholder="Enter your Artificial Analysis API key",
                    i18n_placeholder="schema_group_field_settings_leaderboard_artificial_analysis_api_key_placeholder",
                    dependency="settings.leaderboard.enabled",
                    dependency_value=True,
                    default=_default_setting("leaderboard.artificial_analysis_api_key"),
                    redact_value=True,
                    masked_placeholder=True,
                ),
            ],
        ),
        Section(
            key="compliance",
            title="Compliance",
            description="Controls for compliance notices and provenance of generated outputs.",
            i18n_title="schema_group_sec_compliance_title",
            i18n_description="schema_group_sec_compliance_desc",
            fields=[
                FieldSchema(
                    key="settings.chat.show_chat_box_warning",
                    label="Chat warning level",
                    i18n_label="schema_group_field_settings_chat_show_chat_box_warning_label",
                    description="Banner shown in the chat composer.",
                    i18n_description="schema_group_field_settings_chat_show_chat_box_warning_description",
                    type="boolean",
                    default=_default_setting("chat.show_chat_box_warning"),
                ),
                FieldSchema(
                    key="settings.chat.chat_box_warning_message",
                    label="Chat warning copy",
                    i18n_label="schema_group_field_settings_chat_chat_box_warning_message_label",
                    description="Custom text displayed when the warning banner is enabled.",
                    i18n_description="schema_group_field_settings_chat_chat_box_warning_message_description",
                    type="string",
                    placeholder="E.g. AI can make mistakes. Double check information.",
                    i18n_placeholder="schema_group_field_settings_chat_chat_box_warning_message_placeholder",
                    dependency="settings.chat.show_chat_box_warning",
                    dependency_value=True,
                    default=_default_setting("chat.chat_box_warning_message"),
                ),
                FieldSchema(
                    key="settings.compliance.enable_content_credentials",
                    label="Embed C2PA Content Credentials",
                    i18n_label="schema_group_field_settings_compliance_enable_content_credentials_label",
                    description=(
                        "Locally sign supported AI-generated image, audio, and video files "
                        "with machine-readable C2PA provenance when they are created. "
                        "Existing files are unchanged."
                    ),
                    i18n_description="schema_group_field_settings_compliance_enable_content_credentials_description",
                    type="boolean",
                    default=_default_setting("compliance.enable_content_credentials"),
                ),
                FieldSchema(
                    key="settings.compliance.enable_watermark",
                    label="Enable watermarking",
                    i18n_label="schema_group_field_settings_compliance_enable_watermark_label",
                    description="Add compliance watermarks to exported content.",
                    i18n_description="schema_group_field_settings_compliance_enable_watermark_description",
                    type="boolean",
                    default=_default_setting("compliance.enable_watermark"),
                ),
                FieldSchema(
                    key="settings.compliance.watermark",
                    label="Watermark text",
                    i18n_label="schema_group_field_settings_compliance_watermark_label",
                    description="Custom text appended when watermarking is enabled.",
                    i18n_description="schema_group_field_settings_compliance_watermark_description",
                    type="string",
                    dependency="settings.compliance.enable_watermark",
                    dependency_value=True,
                    default=_default_setting("compliance.watermark"),
                ),
            ],
        ),
        Section(
            key="access_windows",
            title="Access Windows",
            description="Configure time-based access restrictions for this group.",
            i18n_title="schema_group_sec_access_windows_title",
            i18n_description="schema_group_sec_access_windows_desc",
            fields=[
                FieldSchema(
                    key="settings.access_windows.enabled",
                    label="Enable access windows",
                    i18n_label="schema_group_field_settings_access_windows_enabled_label",
                    description="Restrict group member sign-in to specific time windows.",
                    i18n_description="schema_group_field_settings_access_windows_enabled_description",
                    type="boolean",
                    default=_default_setting("access_windows.enabled"),
                ),
                FieldSchema(
                    key="settings.access_windows.timezone",
                    label="Timezone",
                    i18n_label="schema_group_field_settings_access_windows_timezone_label",
                    description="Timezone used for evaluating access rules (e.g., Europe/Berlin, America/New_York).",
                    i18n_description="schema_group_field_settings_access_windows_timezone_description",
                    type="select",
                    options=_timezone_select_options(),
                    placeholder="UTC",
                    i18n_placeholder="schema_group_field_settings_access_windows_timezone_placeholder",
                    dependency="settings.access_windows.enabled",
                    dependency_value=True,
                    default=_default_setting("access_windows.timezone"),
                ),
                FieldSchema(
                    key="settings.access_windows.mode",
                    label="Access mode",
                    i18n_label="schema_group_field_settings_access_windows_mode_label",
                    description="Allowlist: only allow during rules. Blocklist: block during rules.",
                    i18n_description="schema_group_field_settings_access_windows_mode_description",
                    type="select",
                    options=[
                        {"value": "allowlist", "label": "Allowlist (allow only during specified times)", "i18n_label": "schema_group_option_settings_access_windows_mode_allowlist"},
                        {"value": "blocklist", "label": "Blocklist (block during specified times)", "i18n_label": "schema_group_option_settings_access_windows_mode_blocklist"},
                    ],
                    dependency="settings.access_windows.enabled",
                    dependency_value=True,
                    default=_default_setting("access_windows.mode"),
                ),
                FieldSchema(
                    key="settings.access_windows.rules",
                    label="Access rules",
                    i18n_label="schema_group_field_settings_access_windows_rules_label",
                    description="Define time windows. Format: JSON array of rules with start, end (HH:MM), days (0=Mon to 6=Sun), and label.",
                    i18n_description="schema_group_field_settings_access_windows_rules_description",
                    type="access_rules",
                    dependency="settings.access_windows.enabled",
                    dependency_value=True,
                    default=_default_setting("access_windows.rules"),
                ),
                FieldSchema(
                    key="settings.access_windows.show_next_available",
                    label="Show next available time",
                    i18n_label="schema_group_field_settings_access_windows_show_next_available_label",
                    description="Display countdown to next allowed access window on login screen.",
                    i18n_description="schema_group_field_settings_access_windows_show_next_available_description",
                    type="boolean",
                    dependency="settings.access_windows.enabled",
                    dependency_value=True,
                    default=_default_setting("access_windows.show_next_available"),
                ),
                FieldSchema(
                    key="settings.access_windows.blocked_message",
                    label="Blocked message",
                    i18n_label="schema_group_field_settings_access_windows_blocked_message_label",
                    description="Custom message shown when access is blocked. Leave empty for default.",
                    i18n_description="schema_group_field_settings_access_windows_blocked_message_description",
                    type="string",
                    input_type="text",
                    dependency="settings.access_windows.enabled",
                    dependency_value=True,
                    default=_default_setting("access_windows.blocked_message"),
                ),
            ],
        ),
    ]
)


def _group_field_iterator():
    """Yield all FieldSchema instances from the GROUP_FORM_SCHEMA."""
    for section in GROUP_FORM_SCHEMA.sections:
        for field in getattr(section, "fields", []) or []:
            yield field


FIELD_SCHEMA_BY_KEY: Dict[str, FieldSchema] = {
    field.key: field for field in _group_field_iterator()
}

# These limits represent counts and lengths, so accepting fractional values
# would create settings that cannot be applied faithfully by account creation.
_INTEGER_GROUP_FIELD_KEYS = frozenset(
    {
        "settings.temporary_accounts.max_active_accounts",
        "settings.temporary_accounts.credential_length",
    }
)


def _validate_group_field_value(field: FieldSchema, value: Any) -> Any:
    """Validate and coerce values according to the field definition."""
    if field.type == "boolean":
        if isinstance(value, bool):
            return value
        raise ValueError(f"{field.label} must be true or false")

    if field.type == "number":
        if value in (None, ""):
            return value
        if isinstance(value, bool):
            raise ValueError(f"{field.label} must be a valid number")

        try:
            if isinstance(value, (int, float)):
                numeric_value = float(value)
            elif isinstance(value, str):
                stripped = value.strip()
                numeric_value = float(stripped)
            else:
                numeric_value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field.label} must be a valid number")

        requires_integer = field.key in _INTEGER_GROUP_FIELD_KEYS
        if requires_integer and not numeric_value.is_integer():
            raise ValueError(f"{field.label} must be an integer")

        attributes = field.attributes
        if attributes:
            if attributes.min is not None and numeric_value < attributes.min:
                raise ValueError(f"{field.label} must be at least {attributes.min}")
            if attributes.max is not None and numeric_value > attributes.max:
                raise ValueError(f"{field.label} must be at most {attributes.max}")

        # Normalize every accepted representation of a discrete setting so the
        # persisted value has an integer type as well as an integral value.
        if requires_integer:
            return int(numeric_value)
        if isinstance(value, (int, float)):
            return value
        if numeric_value.is_integer():
            return int(numeric_value)
        return numeric_value

    if field.type in {"string", "textarea"}:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{field.label} must be a string")
        return value

    if field.type == "select":
        if field.multiple:
            if value is None:
                return []
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"{field.label} must be a list of strings")
            allowed_values = {option.value for option in field.options or []}
            if allowed_values:
                invalid = [item for item in value if item not in allowed_values]
                if invalid:
                    raise ValueError(f"{field.label} contains an unsupported option")
            return value

        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{field.label} must be a string")
        allowed_values = {option.value for option in field.options or []}
        if value and allowed_values and value not in allowed_values:
            raise ValueError(f"{field.label} contains an unsupported option")
        return value

    if field.type in {"string_list", "select_multi", "context_files"}:
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field.label} must be a list of strings")
        return value

    if field.type == "access_rules":
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"{field.label} must be a list of rule objects")
        return value

    if field.type == "boolean_map":
        if value is None:
            return {}
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(map_value, bool)
            for key, map_value in value.items()
        ):
            raise ValueError(f"{field.label} must be an object of boolean values")
        return value

    return value


def _validate_group_default_value(page_name: str, key_name: str, value: Any) -> Any:
    """Validate settings that are defined in defaults but not rendered in the form schema."""
    default_page = DEFAULT_GROUP_SETTINGS.get(page_name)
    if not isinstance(default_page, dict) or key_name not in default_page:
        return value

    default_value = default_page[key_name]
    if isinstance(default_value, bool):
        if isinstance(value, bool):
            return value
        raise ValueError(f"{page_name}.{key_name} must be true or false")
    if isinstance(default_value, (int, float)) and not isinstance(default_value, bool):
        if isinstance(value, bool):
            raise ValueError(f"{page_name}.{key_name} must be a valid number")
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{page_name}.{key_name} must be a valid number")
        if isinstance(default_value, int) and numeric_value.is_integer():
            return int(numeric_value)
        return numeric_value
    if isinstance(default_value, str):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{page_name}.{key_name} must be a string")
    if isinstance(default_value, list):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{page_name}.{key_name} must be a list")
    return value


def _validate_group_settings_payload(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Validate known group setting fields while preserving unsupported keys for callers."""
    sanitized: Dict[str, Any] = {}
    for page_name, page_values in settings.items():
        if not isinstance(page_values, dict):
            raise ValueError(f"Page '{page_name}' payload must be an object")
        sanitized_page: Dict[str, Any] = {}
        for key_name, value in page_values.items():
            dotted_key = f"settings.{page_name}.{key_name}"
            field_schema = FIELD_SCHEMA_BY_KEY.get(dotted_key)
            sanitized_page[key_name] = (
                _validate_group_field_value(field_schema, value)
                if field_schema
                else _validate_group_default_value(page_name, key_name, value)
            )
        sanitized[page_name] = sanitized_page
    return sanitized


def _validate_manager_role_lists(
    owner_user_ids: list[str],
    manager_user_ids: list[str],
    coordinator_user_ids: list[str],
) -> None:
    """Ensure manager identifiers are non-empty, unique, and role-exclusive."""

    role_lists = {
        "owner": owner_user_ids,
        "manager": manager_user_ids,
        "coordinator": coordinator_user_ids,
    }
    seen_roles: Dict[str, str] = {}
    for role, user_ids in role_lists.items():
        normalized_ids: set[str] = set()
        for raw_user_id in user_ids:
            user_id = str(raw_user_id or "").strip()
            if not user_id:
                raise ValueError("Manager user IDs must not be empty")
            if user_id in normalized_ids:
                raise ValueError(f"A user may only appear once in the {role} list")
            normalized_ids.add(user_id)
            previous_role = seen_roles.get(user_id)
            if previous_role:
                raise ValueError(
                    f"A user cannot be assigned both {previous_role} and {role} roles"
                )
            seen_roles[user_id] = role


# -------------------
# Create group
# -------------------
class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: Optional[str] = Field(None, max_length=64)
    owner_user_ids: list[str] = Field(default_factory=list)
    manager_user_ids: list[str] = Field(default_factory=list)
    coordinator_user_ids: list[str] = Field(default_factory=list)
    settings: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_settings(self):
        """Validate settings and ensure every manager has exactly one role."""
        if self.settings is not None:
            if not isinstance(self.settings, dict):
                raise ValueError("settings must be an object")
            self.settings = _validate_group_settings_payload(self.settings)
        _validate_manager_role_lists(
            self.owner_user_ids,
            self.manager_user_ids,
            self.coordinator_user_ids,
        )
        return self


# -------------------
# Update group
# -------------------
class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_id: Optional[str] = Field(None, max_length=64)
    settings: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_settings(self):
        """Validate that settings is a dict if provided."""
        if self.settings is None:
            return self
        if not isinstance(self.settings, dict):
            raise ValueError("settings must be an object")
        self.settings = _validate_group_settings_payload(self.settings)
        return self


# -------------------
# Update group setting
# -------------------
class GroupSettingUpdateAll(BaseModel):
    page_name: str = Field(..., min_length=1)
    key_name: str = Field(..., min_length=1)
    value: Any


# -------------------
# Group settings schema page
# -------------------
class GroupSettingsSchemaPage(BaseModel):
    key: str
    label: str
    description: Optional[str] = None
    fields: list[FieldSchema]


# -------------------
# Group values response
# -------------------
class GroupValuesResponse(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    parent_name: str | None = None
    path: list[str] = Field(default_factory=list)
    depth: int = 0
    direct_member_count: int = 0
    direct_manager_count: int = 0
    settings: Dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -------------------
# Group list response
# -------------------
class GroupListItem(BaseModel):
    """Administrative group-list item without unused persistence timestamps."""

    id: str
    name: str
    parent_id: str | None = None
    parent_name: str | None = None
    path: list[str] = Field(default_factory=list)
    depth: int = 0
    direct_member_count: int = 0
    direct_manager_count: int = 0
    settings: Dict[str, Any]


class GroupListResponse(BaseModel):
    groups: list[GroupListItem]


# -------------------
# Group values update payload
# -------------------
class GroupValuesUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_id: Optional[str] = Field(None, max_length=64)
    owner_user_ids: Optional[list[str]] = None
    manager_user_ids: Optional[list[str]] = None
    coordinator_user_ids: Optional[list[str]] = None
    settings: Optional[Dict[str, Dict[str, Any]]] = None

    @model_validator(mode="after")
    def validate_settings(self):
        """Validate settings and complete manager-assignment replacements."""
        if self.settings is not None:
            self.settings = _validate_group_settings_payload(self.settings)

        role_lists = (
            self.owner_user_ids,
            self.manager_user_ids,
            self.coordinator_user_ids,
        )
        if any(value is not None for value in role_lists):
            if any(value is None for value in role_lists):
                raise ValueError("All manager role lists must be provided together")
            _validate_manager_role_lists(*role_lists)
        return self
