"""Persistence and schema hydration for the administrator Groups page."""

from fastapi import HTTPException, status
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import Any, Dict
from copy import deepcopy

from app.groups.models import (
    Group,
    GroupManager,
    create_group as orm_create_group,
    delete_group as orm_delete_group,
    export_groups as orm_export_groups,
    get_group,
    get_group_by_name,
    list_group_managers,
    group_name_exists,
    import_groups as orm_import_groups,
    list_groups as orm_list_groups,
    replace_group_manager_assignments as orm_replace_group_manager_assignments,
)
from app.groups.sensitive import (
    decrypt_sensitive_settings,
    ensure_sensitive_settings_encrypted,
    resolve_sensitive_setting_update,
)
from app.groups.settings_validation import sanitize_group_settings_for_storage
from app.groups.init import (
    invalidate_leaderboard_cache_after_settings_change,
    get_group_settings,
)
from app.users.models import ACCOUNT_TYPE_REGULAR, User
from app.admin.groups.schemas import GROUP_FORM_SCHEMA, GroupFormSchema
from app.llm.models import Models, list_models as list_llm_models
from app.skills.models import list_admin_skills
from app.utils.schemas import Option, populate_sections_with_values


def delete_group(group_id: str, db: Session) -> Dict[str, str]:
    """Delete a group through the shared persistence boundary."""

    return orm_delete_group(group_id, db)


def replace_group_manager_assignments(
    db: Session,
    *,
    group_id: str,
    owner_user_ids: list[str],
    manager_user_ids: list[str],
    coordinator_user_ids: list[str],
) -> Dict[str, Any]:
    """Replace all direct manager roles submitted by the admin form."""

    return orm_replace_group_manager_assignments(
        db,
        group_id=group_id,
        owner_user_ids=owner_user_ids,
        manager_user_ids=manager_user_ids,
        coordinator_user_ids=coordinator_user_ids,
    )


# -------------------
# Sensitive helpers
# -------------------
def _sanitize_sensitive_payload(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Encrypt sensitive fields in a settings dict and return the sanitized copy."""
    if not isinstance(settings, dict):
        return {}
    _, sanitized = ensure_sensitive_settings_encrypted(settings)
    return sanitized


def _decrypt_sensitive_payload(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Decrypt sensitive fields in a settings dict and return the result."""
    if not isinstance(settings, dict):
        return {}
    return decrypt_sensitive_settings(settings)


# -------------------
# Title case
# -------------------
def _title_case(text: str) -> str:
    """Convert an underscore/hyphen-separated string to title case."""
    return text.replace("_", " ").replace("-", " ").title()



# -------------------
# Field type for value
# -------------------
def _field_type_for_value(value: Any) -> str:
    """Infer a field schema type string from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "string_list"
    return "string"


def _validate_memory_model_reference(db: Session, settings: Dict[str, Any]) -> None:
    """Reject stale or non-completion model selections before persistence."""

    memories = settings.get("memories") if isinstance(settings, dict) else None
    raw_model_id = memories.get("memory_model_id") if isinstance(memories, dict) else ""
    model_id = str(raw_model_id or "").strip()
    if not model_id:
        return
    model = (
        db.query(Models)
        .filter(Models.id == model_id, Models.is_active.is_(True))
        .first()
    )
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected memory model is not available",
        )
    capabilities = getattr(model, "capabilities", None)
    supports_completion = (
        bool(capabilities.get("completion"))
        if isinstance(capabilities, dict)
        else "completion" in capabilities
        if isinstance(capabilities, (list, tuple, set))
        else True
    )
    if not supports_completion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected memory model does not support text completion",
        )


# -------------------
# Get group form schema (with values)
# -------------------
def get_group_form_schema(db: Session, group_id: str | None = None) -> GroupFormSchema:
    """Return the group form schema, optionally hydrated with a specific group's values."""
    schema_copy = GROUP_FORM_SCHEMA.model_copy(deep=True)
    _hydrate_parent_group_select(db, schema_copy, group_id)
    _hydrate_manager_user_selects(db, schema_copy, group_id=group_id)
    _hydrate_model_selects(db, schema_copy)
    _hydrate_admin_skills_select(db, schema_copy)
    _hydrate_byok_allowed_tools_select(db, schema_copy)
    _hydrate_byok_websearch_provider_selects(db, schema_copy)
    if not group_id:
        return schema_copy

    group = get_group(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    payload = {
        "name": group.name,
        "parent_id": group.parent_id or "",
        "settings": get_group_settings(group.id, db),
    }
    manager_ids_by_role = {
        "owner": [],
        "manager": [],
        "coordinator": [],
    }
    for assignment in list_group_managers(db, group.id):
        if assignment.role in manager_ids_by_role:
            manager_ids_by_role[assignment.role].append(assignment.user_id)
    payload.update(
        {
            "owner_user_ids": manager_ids_by_role["owner"],
            "manager_user_ids": manager_ids_by_role["manager"],
            "coordinator_user_ids": manager_ids_by_role["coordinator"],
        }
    )

    populate_sections_with_values(schema_copy, payload)
    return schema_copy


def _manager_user_label(user: User) -> str:
    """Return the readable label shared by selected and remote user options."""

    display_name = " ".join(
        part.strip()
        for part in [user.first_name or "", user.last_name or ""]
        if part and part.strip()
    )
    email = str(user.email or "").strip()
    if display_name and email:
        return f"{display_name} ({email})"
    return display_name or email or str(user.id)


def _eligible_manager_user_query(db: Session):
    """Build the common eligibility query for administrator manager pickers."""

    return db.query(User).filter(
        User.account_type == ACCOUNT_TYPE_REGULAR,
        User.is_active.is_(True),
        User.deleted_at.is_(None),
        User.role != "pending",
    )


def list_group_manager_candidate_options(
    db: Session,
    *,
    search: str | None,
    offset: int,
    limit: int,
) -> Dict[str, Any]:
    """Return one bounded administrator manager-picker page.

    User names are encrypted at rest and cannot be searched safely in SQL.
    The remote picker therefore searches the canonical plaintext email while
    still returning decrypted display names for the bounded result page.
    """

    safe_offset = max(0, int(offset))
    safe_limit = max(1, min(int(limit), 100))
    normalized_search = str(search or "").strip().casefold()
    query = _eligible_manager_user_query(db)
    if normalized_search:
        query = query.filter(
            func.lower(User.email).contains(normalized_search, autoescape=True)
        )
    total = query.count()
    users = (
        query.order_by(func.lower(User.email).asc(), User.id.asc())
        .offset(safe_offset)
        .limit(safe_limit)
        .all()
    )
    return {
        "options": [
            {"value": str(user.id), "label": _manager_user_label(user)}
            for user in users
        ],
        "offset": safe_offset,
        "limit": safe_limit,
        "total": total,
        "has_more": safe_offset + len(users) < total,
    }


def _hydrate_manager_user_selects(
    db: Session,
    schema: GroupFormSchema,
    *,
    group_id: str | None,
) -> None:
    """Hydrate only current assignments; other options load on demand."""

    selected_user_ids: list[str] = []
    if group_id:
        selected_user_ids = [
            user_id
            for (user_id,) in (
                db.query(GroupManager.user_id)
                .filter(GroupManager.group_id == group_id)
                .all()
            )
        ]
    users = (
        db.query(User).filter(User.id.in_(selected_user_ids)).all()
        if selected_user_ids
        else []
    )

    labeled_users = [(user, _manager_user_label(user)) for user in users]
    options = tuple(
        Option(value=str(user.id), label=label, translatable=False)
        for user, label in sorted(labeled_users, key=lambda item: item[1].casefold())
    )
    manager_field_keys = {
        "owner_user_ids",
        "manager_user_ids",
        "coordinator_user_ids",
    }
    for section in schema.sections:
        for field in section.fields:
            if field.key in manager_field_keys:
                # The tuple prevents role fields from mutating shared option
                # membership while avoiding three deep copies of the same data.
                field.options = options


def _hydrate_parent_group_select(db: Session, schema: GroupFormSchema, group_id: str | None = None) -> None:
    if not schema or not getattr(schema, "sections", None):
        return
    options = [Option(value="", label="No parent", i18n_label="schema_group_option_parent_id_none")]
    if hasattr(db, "query"):
        for group in orm_list_groups(db):
            if group_id and group.id == group_id:
                continue
            options.append(Option(value=group.id, label=group.name))
    for section in schema.sections:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == "parent_id":
                field.options = options


def _hydrate_model_selects(db: Session, schema: GroupFormSchema) -> None:
    """Populate model select fields with available LLM model options."""
    if not schema or not getattr(schema, "sections", None):
        return

    models = list_llm_models(db)

    options: list[Option] = []
    memory_options: list[Option] = [
        Option(
            value="",
            label="Use current chat model",
            i18n_label="schema_group_option_settings_memories_memory_model_id_current",
        )
    ]
    for model in models:
        model_id = getattr(model, "id", None)
        if not isinstance(model_id, str):
            continue
        name = getattr(model, "name", None) or getattr(model, "model_name", None) or model_id
        option = Option(value=model_id, label=str(name), translatable=False)
        options.append(option)
        capabilities = getattr(model, "capabilities", None)
        supports_completion = (
            bool(capabilities.get("completion"))
            if isinstance(capabilities, dict)
            else "completion" in capabilities
            if isinstance(capabilities, (list, tuple, set))
            else True
        )
        if supports_completion:
            memory_options.append(option.model_copy(deep=True))

    selectable_keys = {
        "settings.chat.byok_title_generation_model_id",
        "settings.memories.memory_model_id",
    }

    for section in schema.sections:
        for field in getattr(section, "fields", []) or []:
            field_key = getattr(field, "key", None)
            if field_key not in selectable_keys:
                continue
            field.options = (
                memory_options
                if field_key == "settings.memories.memory_model_id"
                else options
            )


def _hydrate_admin_skills_select(db: Session, schema: GroupFormSchema) -> None:
    """Populate admin_skill_ids multi-select with available admin skills."""
    if not schema or not getattr(schema, "sections", None):
        return

    admin_skills = list_admin_skills(db)
    options: list[Option] = []
    for skill in admin_skills:
        skill_id = getattr(skill, "id", None)
        if not isinstance(skill_id, str):
            continue
        name = getattr(skill, "name", None) or skill_id
        options.append(Option(value=skill_id, label=str(name)))

    for section in schema.sections:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == "settings.skills.admin_skill_ids":
                field.options = options


def _provider_to_option_with_types(provider_info: dict[str, Any]) -> Option:
    """Convert a provider info dict to an Option with type metadata."""
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
        },
    )


def _hydrate_byok_allowed_tools_select(db: Session, schema: GroupFormSchema) -> None:
    """Populate the BYOK allowed tools multi-select with available tool options."""
    if not schema or not getattr(schema, "sections", None):
        return
    from app.tools.utils import list_available_tool_options

    raw_options = sorted(
        list_available_tool_options(db=db),
        key=lambda item: str(item.get("label") or item.get("name") or "").lower(),
    )
    options = [
        Option(value=item["name"], label=item.get("label") or item["name"])
        for item in raw_options
        if item.get("name")
    ]
    available_tool_names = {item["name"] for item in raw_options if item.get("name")}
    if "mcp" not in available_tool_names:
        options.append(Option(value="mcp", label="MCP"))

    for section in schema.sections:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == "settings.chat.byok_allowed_tools":
                field.options = options


def _hydrate_byok_websearch_provider_selects(db: Session, schema: GroupFormSchema) -> None:
    """Populate BYOK websearch provider select fields with available providers."""
    if not schema or not getattr(schema, "sections", None):
        return

    from app.tools.websearch.models import list_websearch_providers_with_types

    all_providers = list_websearch_providers_with_types(db)
    search_options = [
        _provider_to_option_with_types(provider)
        for provider in all_providers
        if provider.get("has_search", False) or provider.get("has_combined", False)
    ]
    scrape_options = [
        _provider_to_option_with_types(provider)
        for provider in all_providers
        if provider.get("has_scrape", False)
    ]

    for section in schema.sections:
        for field in getattr(section, "fields", []) or []:
            field_key = getattr(field, "key", None)
            if field_key == "settings.chat.byok_default_search_provider":
                field.options = search_options
            elif field_key == "settings.chat.byok_default_scrape_provider":
                field.options = scrape_options



# -------------------
# Remove admin skill references
# -------------------
def remove_admin_skill_from_groups(db: Session, skill_id: str) -> int:
    """Remove a deleted admin skill ID from all group settings.

    Returns the number of groups that were updated.
    """
    if not skill_id:
        return 0

    affected = 0
    groups = db.query(Group).all()

    for group in groups:
        settings = _decrypt_sensitive_payload(group.settings)
        skills_settings = settings.get("skills")
        if not isinstance(skills_settings, dict):
            continue
        admin_ids = skills_settings.get("admin_skill_ids")
        if not isinstance(admin_ids, list) or not admin_ids:
            continue

        filtered_ids = [admin_id for admin_id in admin_ids if admin_id != skill_id]
        if len(filtered_ids) == len(admin_ids):
            continue

        skills_settings["admin_skill_ids"] = filtered_ids
        settings["skills"] = skills_settings
        sanitized = _sanitize_sensitive_payload(settings)
        group.settings = sanitized
        flag_modified(group, "settings")
        affected += 1

    return affected


def _group_path(db: Session, group: Group) -> list[str]:
    names: list[str] = []
    current = group
    seen: set[str] = set()
    while current:
        if current.id in seen:
            break
        seen.add(current.id)
        names.append(current.name)
        if not current.parent_id:
            break
        current = get_group(db, current.parent_id)
        if not current:
            break
    return list(reversed(names))


def _validate_parent_assignment(db: Session, group_id: str | None, parent_id: str | None) -> str | None:
    normalized_parent_id = str(parent_id or "").strip() or None
    if not normalized_parent_id:
        return None
    parent = get_group(db, normalized_parent_id)
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent group not found")
    if not group_id:
        return normalized_parent_id
    if normalized_parent_id == group_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A group cannot be its own parent")

    seen: set[str] = set()
    cursor = parent
    while cursor:
        if cursor.id in seen:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group hierarchy contains a cycle")
        seen.add(cursor.id)
        if cursor.id == group_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot assign a descendant as the parent group",
            )
        if not cursor.parent_id:
            break
        cursor = get_group(db, cursor.parent_id)
    return normalized_parent_id


def _build_group_context_maps(db: Session, groups: list[Group]) -> tuple[Dict[str, str | None], Dict[str, list[str]], Dict[str, int], Dict[str, int]]:
    """Build context maps for all groups in batch to avoid N+1 queries.
    
    Returns:
        parent_name_map: {group_id: parent_name or None}
        path_map: {group_id: list of group names in path}
        depth_map: {group_id: depth in hierarchy}
        member_count_map: {group_id: member count}
        manager_count_map: {group_id: manager count}
    """
    # Build parent_name_map by fetching all parent groups in one query
    parent_ids = {g.parent_id for g in groups if g.parent_id}
    parent_name_map: Dict[str, str | None] = {}
    if parent_ids:
        parents = db.query(Group).filter(Group.id.in_(parent_ids)).all()
        parent_name_map = {p.id: p.name for p in parents}
    
    # Build a group lookup map for path computation
    group_map = {g.id: g for g in groups}
    # Include parents in the map for path computation
    if parent_ids:
        for parent in parents:
            if parent.id not in group_map:
                group_map[parent.id] = parent
    
    # Build path_map and depth_map in batch using memoization
    path_map: Dict[str, list[str]] = {}
    depth_map: Dict[str, int] = {}
    
    def compute_path(group_id: str, seen: set[str] | None = None) -> list[str]:
        """Recursively compute path with memoization."""
        if group_id in path_map:
            return path_map[group_id]

        seen = set() if seen is None else set(seen)
        if group_id in seen:
            path_map[group_id] = []
            depth_map[group_id] = 0
            return []
        seen.add(group_id)
        
        group = group_map.get(group_id)
        if not group:
            path_map[group_id] = []
            depth_map[group_id] = 0
            return []
        
        if not group.parent_id:
            path_map[group_id] = [group.name]
            depth_map[group_id] = 0
            return [group.name]
        
        parent_path = compute_path(group.parent_id, seen)
        path = parent_path + [group.name]
        path_map[group_id] = path
        depth_map[group_id] = len(parent_path)
        return path
    
    for group in groups:
        compute_path(group.id)
    
    # Fetch member and manager counts in single aggregated queries
    group_ids = [g.id for g in groups]
    member_count_map: Dict[str, int] = {}
    manager_count_map: Dict[str, int] = {}
    
    if group_ids:
        member_counts = (
            db.query(User.group_id, func.count(User.id))
            .filter(User.group_id.in_(group_ids), User.account_type == ACCOUNT_TYPE_REGULAR)
            .group_by(User.group_id)
            .all()
        )
        member_count_map = {group_id: count for group_id, count in member_counts}
        
        manager_counts = (
            db.query(GroupManager.group_id, func.count(GroupManager.id))
            .filter(GroupManager.group_id.in_(group_ids))
            .group_by(GroupManager.group_id)
            .all()
        )
        manager_count_map = {group_id: count for group_id, count in manager_counts}
    
    # Ensure all groups have entries (default to 0)
    for group_id in group_ids:
        if group_id not in member_count_map:
            member_count_map[group_id] = 0
        if group_id not in manager_count_map:
            manager_count_map[group_id] = 0
    
    return parent_name_map, path_map, depth_map, member_count_map, manager_count_map


def _serialize_group_with_context(
    db: Session,
    group: Group,
    parent_name_map: Dict[str, str | None] | None = None,
    path_map: Dict[str, list[str]] | None = None,
    depth_map: Dict[str, int] | None = None,
    member_count_map: Dict[str, int] | None = None,
    manager_count_map: Dict[str, int] | None = None,
    include_timestamps: bool = True,
) -> Dict[str, Any]:
    """Serialize a group with context.

    Precomputed maps avoid N+1 queries for list responses. Timestamps remain
    available to detail and mutation responses, but the administrative table
    can omit them from its narrower list contract.
    """
    # Use precomputed maps if provided, otherwise fall back to individual queries
    if parent_name_map is not None:
        parent_name = parent_name_map.get(group.parent_id) if group.parent_id else None
    else:
        parent = get_group(db, group.parent_id) if group.parent_id else None
        parent_name = parent.name if parent else None
    
    if path_map is not None:
        path = path_map.get(group.id, [])
    else:
        path = _group_path(db, group)
    
    if depth_map is not None:
        depth = depth_map.get(group.id, 0)
    else:
        depth = max(0, len(path) - 1)
    
    if member_count_map is not None:
        member_count = member_count_map.get(group.id, 0)
    else:
        member_count = (
            db.query(User.id)
            .filter(User.group_id == group.id, User.account_type == ACCOUNT_TYPE_REGULAR)
            .count()
        )
    
    if manager_count_map is not None:
        manager_count = manager_count_map.get(group.id, 0)
    else:
        manager_count = db.query(GroupManager.id).filter(GroupManager.group_id == group.id).count()
    
    serialized_group = {
        "id": group.id,
        "name": group.name,
        "parent_id": group.parent_id,
        "parent_name": parent_name,
        "path": path,
        "depth": depth,
        "direct_member_count": member_count,
        "direct_manager_count": manager_count,
        "settings": {},
    }
    if include_timestamps:
        serialized_group.update(
            {
                "created_at": group.created_at,
                "updated_at": group.updated_at,
            }
        )
    return serialized_group



# -------------------
# Create group
# -------------------
def create_group(
    name: str,
    settings: Dict[str, Any] | None,
    db: Session,
    *,
    parent_id: str | None = None,
    commit: bool = True,
) -> Dict[str, Any]:
    """Create a group and return a minimal payload.

    Args:
        name: Group name.
        settings: Initial settings dict (will be stored as JSON by SQLAlchemy dialect).
        db: SQLAlchemy session.
    """
    if not name or not name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name is required")
    # Optional: enforce unique name across groups (soft uniqueness)
    existing = get_group_by_name(db, name.strip())
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    normalized_parent_id = _validate_parent_assignment(db, None, parent_id)
    try:
        sanitized_settings = sanitize_group_settings_for_storage(settings if settings is not None else {})
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _validate_memory_model_reference(db, sanitized_settings)
    group = orm_create_group(
        db,
        group_id=None,
        name=name.strip(),
        settings=sanitized_settings,
        parent_id=normalized_parent_id,
        commit=commit,
    )
    return _serialize_group_with_context(db, group)



# -------------------
# Duplicate group
# -------------------
def _generate_duplicate_name(db: Session, original_name: str) -> str:
    """Generate a unique copy name for a duplicated group."""
    base_name = (original_name or "").strip()
    candidate = f"{base_name} (Copy)"
    suffix = 2

    while group_name_exists(db, candidate):
        candidate = f"{base_name} (Copy) {suffix}"
        suffix += 1

    return candidate


def duplicate_group(group_id: str, db: Session) -> Dict[str, Any]:
    """Duplicate a group by cloning its settings under a new name."""
    source_group = get_group(db, group_id)
    if not source_group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    duplicate_name = _generate_duplicate_name(db, source_group.name)
    settings_clone = get_group_settings(source_group.id, db)
    encrypted_clone = sanitize_group_settings_for_storage(settings_clone)

    new_group = orm_create_group(
        db,
        group_id=None,
        name=duplicate_name,
        settings=encrypted_clone,
        parent_id=source_group.parent_id,
    )
    return _serialize_group_with_context(db, new_group)



# -------------------
# Update group
# -------------------
def update_group(
    group_id: str,
    name: str | None,
    settings: Dict[str, Any] | None,
    db: Session,
    *,
    parent_id: str | None = None,
    commit: bool = True,
) -> Dict[str, Any]:
    """Update group name and/or settings and return the updated payload."""
    group = get_group(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    updated = False
    normalized_parent_id = _validate_parent_assignment(db, group_id, parent_id) if parent_id is not None else group.parent_id
    settings_may_change = settings is not None
    previous_settings = get_group_settings(group_id, db, commit=commit) if settings_may_change else None
    if name is not None:
        # Lock the name of the built-in default group
        if group.id == "default" and name.strip() != group.name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Default group name cannot be changed")
        new_name = name.strip()
        if not new_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name cannot be empty")
        if new_name != group.name:
            # check name conflict
            if group_name_exists(db, new_name, exclude_id=group.id):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
            group.name = new_name
            updated = True
    if parent_id is not None and normalized_parent_id != group.parent_id:
        group.parent_id = normalized_parent_id
        updated = True
    if settings is not None:
        try:
            sanitized_settings = sanitize_group_settings_for_storage(settings)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        _validate_memory_model_reference(db, sanitized_settings)
        group.settings = sanitized_settings
        flag_modified(group, "settings")
        updated = True
    if updated:
        group.updated_at = datetime.now(timezone.utc)
        if commit:
            db.commit()
            db.refresh(group)
        else:
            db.flush()
        if previous_settings is not None:
            next_settings = get_group_settings(group_id, db, commit=commit)
            invalidate_leaderboard_cache_after_settings_change(previous_settings, next_settings)
    return _serialize_group_with_context(db, group)



# -------------------
# List groups
# -------------------
def list_groups(db: Session) -> list[Dict[str, Any]]:
    """Return all groups with minimal fields."""
    groups = orm_list_groups(db)
    # Build context maps in batch to avoid N+1 queries
    parent_name_map, path_map, depth_map, member_count_map, manager_count_map = _build_group_context_maps(db, groups)
    # Serialize groups using precomputed maps
    return [
        _serialize_group_with_context(
            db,
            g,
            parent_name_map=parent_name_map,
            path_map=path_map,
            depth_map=depth_map,
            member_count_map=member_count_map,
            manager_count_map=manager_count_map,
            include_timestamps=False,
        )
        for g in groups
    ]



# -------------------
# Export groups
# -------------------
def export_groups(db: Session) -> Dict[str, Any]:
    """Delegate to the ORM export_groups function."""
    return orm_export_groups(db)



# -------------------
# Import groups
# -------------------
def import_groups(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Delegate to the ORM import_groups function."""
    return orm_import_groups(db, payload)



# -------------------
# Get group values
# -------------------
def get_group_values(group_id: str, db: Session) -> Dict[str, Any]:
    """Return a serialized group by ID."""
    group = get_group(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return _serialize_group_with_context(db, group)



# -------------------
# Update group values
# -------------------
def update_group_values(
    group_id: str,
    name: str | None,
    settings: Dict[str, Dict[str, Any]] | None,
    db: Session,
    *,
    parent_id: str | None = None,
    commit: bool = True,
) -> Dict[str, Any]:
    """Merge partial settings updates and persist the group."""
    normalized_settings: Dict[str, Any] | None = None

    if settings is not None:
        if not isinstance(settings, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="settings must be an object")

        current_settings = get_group_settings(group_id, db, commit=commit)
        merged_settings = deepcopy(current_settings)

        for page_name, page_values in settings.items():
            if page_name not in merged_settings:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Page '{page_name}' not found")
            if not isinstance(page_values, dict):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Page '{page_name}' payload must be an object")

            for key, value in page_values.items():
                if key not in merged_settings[page_name]:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Key '{key}' not found in page '{page_name}'")
                # Existing password inputs submit a non-secret marker when the
                # field was untouched. Resolve it before rebuilding the full
                # snapshot so unrelated edits cannot erase the stored secret.
                merged_settings[page_name][key] = resolve_sensitive_setting_update(
                    page_name,
                    key,
                    value,
                    merged_settings[page_name][key],
                )

        normalized_settings = merged_settings

    return update_group(
        group_id,
        name,
        normalized_settings,
        db,
        parent_id=parent_id,
        commit=commit,
    )
