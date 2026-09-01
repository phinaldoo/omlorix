"""Administrator routes for group creation, policy editing, and transfer."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.admin.groups.schemas import (
    GroupCreate,
    GroupListResponse,
    GroupManagerCandidatePage,
    GroupFormSchema,
    GroupValuesResponse,
    GroupValuesUpdatePayload,
)
from app.admin.groups.models import (
    create_group,
    delete_group,
    duplicate_group,
    list_groups,
    export_groups as export_groups_util,
    import_groups as import_groups_util,
    list_group_manager_candidate_options,
    replace_group_manager_assignments,
    update_group_values,
    get_group_form_schema,
)


admin_router = APIRouter(prefix="/api/v1/groups", tags=["groups"])



# -------------------
# List groups
# -------------------
@admin_router.get("/list", response_model=GroupListResponse, dependencies=[Depends(verified_admin)])
def list_groups_route(db: Session = Depends(get_db)):
    """List all groups. Admin only."""
    groups = list_groups(db)
    return {"groups": groups}


@admin_router.get(
    "/manager-candidates",
    response_model=GroupManagerCandidatePage,
)
def list_group_manager_candidates_route(
    request: Request,
    search: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Return bounded eligible users for the administrator group-role picker."""

    page = list_group_manager_candidate_options(
        db,
        search=search,
        offset=offset,
        limit=limit,
    )
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_GROUP_MANAGER_CANDIDATES",
        details={
            "count": len(page["options"]),
            "total": page["total"],
            "offset": page["offset"],
            "limit": page["limit"],
            "has_search": bool(search),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="group",
    )
    return page



# -------------------
# Create group
# -------------------
@admin_router.post("/", dependencies=[Depends(verified_admin)], response_model=GroupValuesResponse)
def create_group_route(
    payload: GroupCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Create a new group and log the action."""
    manager_assignments = {
        "owner_user_ids": payload.owner_user_ids,
        "manager_user_ids": payload.manager_user_ids,
        "coordinator_user_ids": payload.coordinator_user_ids,
    }
    has_manager_assignments = any(manager_assignments.values())
    try:
        group = create_group(
            payload.name,
            payload.settings or {},
            db,
            parent_id=payload.parent_id,
            commit=not has_manager_assignments,
        )
        if has_manager_assignments:
            assignment_result = replace_group_manager_assignments(
                db,
                group_id=group["id"],
                **manager_assignments,
            )
            group["direct_manager_count"] = int(assignment_result["total"])
    except Exception:
        db.rollback()
        raise
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="CREATE_GROUP",
        details={
            "group_id": group["id"],
            "name": group["name"],
            "owner_count": len(payload.owner_user_ids),
            "manager_count": len(payload.manager_user_ids),
            "coordinator_count": len(payload.coordinator_user_ids),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="group",
    )
    return group



# -------------------
# Update group (name/settings in a single payload)
# -------------------
@admin_router.put("/{group_id}", response_model=GroupValuesResponse, dependencies=[Depends(verified_admin)])
def update_group_route(
    group_id: str,
    payload: GroupValuesUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Update a group's name and/or settings."""
    manager_lists = (
        payload.owner_user_ids,
        payload.manager_user_ids,
        payload.coordinator_user_ids,
    )
    update_managers = all(value is not None for value in manager_lists)
    try:
        updated_group = update_group_values(
            group_id,
            payload.name,
            payload.settings,
            db,
            parent_id=payload.parent_id,
            commit=not update_managers,
        )
        assignment_result = None
        if update_managers:
            assignment_result = replace_group_manager_assignments(
                db,
                group_id=group_id,
                owner_user_ids=payload.owner_user_ids or [],
                manager_user_ids=payload.manager_user_ids or [],
                coordinator_user_ids=payload.coordinator_user_ids or [],
            )
            updated_group["direct_manager_count"] = int(assignment_result["total"])
    except Exception:
        db.rollback()
        raise
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_GROUP",
        details={
            "group_id": group_id,
            "manager_assignments_updated": update_managers,
            "owner_count": len(payload.owner_user_ids or []) if update_managers else None,
            "manager_count": len(payload.manager_user_ids or []) if update_managers else None,
            "coordinator_count": len(payload.coordinator_user_ids or []) if update_managers else None,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="group",
    )
    return updated_group



# -------------------
# Delete group
# -------------------
@admin_router.delete("/{group_id}", dependencies=[Depends(verified_admin)])
def delete_group_route(
    group_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Delete a group and reassign its users to the default group."""
    result = delete_group(group_id, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DELETE_GROUP",
        details={"group_id": group_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="group",
    )
    return result



# -------------------
# Duplicate group
# -------------------
@admin_router.post(
    "/{group_id}/duplicate",
    dependencies=[Depends(verified_admin)],
    response_model=GroupValuesResponse,
)
def duplicate_group_route(
    group_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Duplicate an existing group with a new name."""
    duplicated = duplicate_group(group_id, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DUPLICATE_GROUP",
        details={"source_group_id": group_id, "new_group_id": duplicated["id"]},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="group",
    )
    return duplicated



# -------------------
# Group form schema (optionally hydrated with values)
# -------------------
@admin_router.get(
    "/form",
    dependencies=[Depends(verified_admin)],
    response_model=GroupFormSchema,
)
def get_group_form_schema_route(
    request: Request,
    group_id: str | None = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Return the group form schema, optionally hydrated with existing values."""
    schema = get_group_form_schema(db, group_id)

    if group_id:
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="GET_GROUP_FORM",
            details={"group_id": group_id},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="group",
        )

    return schema



# -------------------
# Export groups
# -------------------
@admin_router.get("/export", dependencies=[Depends(verified_admin)])
def export_groups_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Export all groups as a JSON payload."""
    export_payload = export_groups_util(db)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="EXPORT_GROUPS",
        details={
            "export_version": export_payload.get("export_version"),
            "exported_count": len(export_payload.get("data", {}).get("groups", [])),
            "exported_manager_assignment_count": len(
                export_payload.get("data", {}).get("group_managers", [])
            ),
            "sensitivity_category": "group_configuration",
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="group",
    )

    return export_payload



# -------------------
# Import groups
# -------------------
@admin_router.post("/import")
def import_groups_route(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Import groups from an export payload."""
    result = import_groups_util(db, payload)

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="IMPORT_GROUPS",
        details={
            "created_count": len(result.get("created", [])),
            "error_count": len(result.get("errors", [])),
            "manager_assignment_count": len(result.get("imported_managers", [])),
            "manager_assignment_error_count": len(result.get("manager_errors", [])),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="group",
    )

    return result
