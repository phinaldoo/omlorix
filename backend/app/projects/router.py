from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.users.models import User
from app.users.sharing import resolve_invitable_users_for_sharing
from app.logging.models import create_audit_log, get_audit_request_ip
from app.userNotifications.models import create_user_notification
from app.projects.models import (
    create_project,
    # Sharing functions
    list_projects_with_shared,
    create_project_link_share,
    delete_project_link_share,
    get_project_share_status,
    get_project_share_preview,
    join_project_via_link,
    remove_project_member,
    get_project_members,
    get_project_with_access,
    is_project_owner,
    delete_project_with_members,
    update_project_shared,
    _get_user_display_name,
    ProjectMember,
)
from app.projects.utils import check_projects_access
from app.groups.init import get_group_setting_value
from app.utils.client_ip import extract_client_ip_from_request, resolve_trusted_proxy_networks
from app.projects.schemas import (
    Project,
    ProjectListResponse,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    ProjectWithSharing,
    # Sharing schemas
    CreateLinkShareRequest,
    CreateLinkShareResponse,
    DeleteLinkShareRequest,
    ProjectShareStatusResponse,
    SharedProjectPreviewResponse,
    JoinProjectByLinkRequest,
    JoinProjectResponse,
    ProjectMembersResponse,
    RemoveProjectMemberRequest,
    InviteUsersToProjectRequest,
    InviteUsersToProjectResponse,
    LeaveProjectRequest,
)

projects_router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def ensure_separate_project_memory_feature_enabled(user: User, db: Session, requested_enabled: bool | None) -> None:
    """Reject enabling project memory when the user's group has Memory disabled.

    The frontend hides this option based on the same group policy, but this
    server-side check keeps direct API requests from creating a project-memory
    configuration that cannot be used by the group.
    """
    if requested_enabled is not True:
        return

    memories_enabled = get_group_setting_value(user.group_id, "memories", "enabled_memories", db)
    if not memories_enabled:
        raise HTTPException(
            status_code=403,
            detail="Memories feature disabled for your group",
        )


def _serialize_project_response(project, *, is_owner: bool) -> Project:
    return Project(
        id=project.id,
        user_id=project.user_id,
        title=project.title,
        images=project.images,
        videos=project.videos,
        audios=project.audios,
        documents=project.documents,
        created_at=project.created_at,
        last_updated_at=project.last_updated_at,
        settings=project.settings,
        link_share_id=project.link_share_id if is_owner else None,
    )



# -------------------
# List projects (with shared)
# -------------------
@projects_router.get("/list", response_model=ProjectListResponse)
def list_projects_route(db: Session = Depends(get_db), user: User = Depends(verified_user)):
    """List all projects the user owns or is a member of."""
    
    check_projects_access(db, user.group_id)
    projects_data = list_projects_with_shared(db, user.id)
    
    projects_response = []
    for item in projects_data:
        project = item["project"]
        is_owner = item["is_owner"]
        has_link_share = project.link_share_id is not None
        projects_response.append(ProjectWithSharing(
            id=project.id,
            user_id=project.user_id,
            title=project.title,
            images=project.images,
            videos=project.videos,
            audios=project.audios,
            documents=project.documents,
            created_at=project.created_at,
            last_updated_at=project.last_updated_at,
            settings=project.settings,
            link_share_id=project.link_share_id if is_owner else None,
            has_link_share=has_link_share,
            is_owner=is_owner,
            is_shared=item["is_shared"],
            member_count=item["member_count"],
            owner_name=item["owner_name"],
        ))
    
    return {"projects": projects_response}   



# -------------------
# Create project
# -------------------
@projects_router.post("/create", response_model=ProjectResponse)
def create_project_route(
    payload: ProjectCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Create a new project for the authenticated user."""
    check_projects_access(db, user.group_id)
    ensure_separate_project_memory_feature_enabled(user, db, payload.separate_memory_enabled)
    settings_payload = {
        "icon": payload.icon,
        "icon_color": payload.icon_color,
        "system_instruction": payload.system_instruction,
        "separate_memory_enabled": payload.separate_memory_enabled,
    }
    project = create_project(db, user.id, payload.title, settings=settings_payload)
    if project.settings.get("separate_memory_enabled"):
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="PROJECT_MEMORY_SCOPE_UPDATED",
            details={"project_id": project.id, "separate_memory_enabled": True},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="memories",
        )
    return {
        "status": "success",
        "project": _serialize_project_response(project, is_owner=True),
    }
        


# -------------------
# Update project (owners and members can update)
# -------------------
@projects_router.put("/update", response_model=ProjectResponse)
def update_project_route(
    payload: ProjectUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Update an existing project (title and/or settings).""" 
    check_projects_access(db, user.group_id)
    ensure_separate_project_memory_feature_enabled(user, db, payload.separate_memory_enabled)
    settings_payload = None
    project_before = get_project_with_access(db, user.id, payload.project_id)
    previous_settings = project_before.settings if isinstance(project_before.settings, dict) else {}
    previous_separate_memory_enabled = bool(previous_settings.get("separate_memory_enabled", False))
    if any([
        payload.icon is not None,
        payload.icon_color is not None,
        payload.system_instruction is not None,
        payload.separate_memory_enabled is not None,
    ]):
        settings_payload = {}
        if payload.icon is not None:
            settings_payload["icon"] = payload.icon
        if payload.icon_color is not None:
            settings_payload["icon_color"] = payload.icon_color
        if payload.system_instruction is not None:
            settings_payload["system_instruction"] = payload.system_instruction
        if payload.separate_memory_enabled is not None:
            settings_payload["separate_memory_enabled"] = payload.separate_memory_enabled
    # Use update_project_shared which allows both owners and members to update
    project = update_project_shared(
        db,
        user.id,
        payload.project_id,
        payload.title,
        settings=settings_payload,
    )
    new_separate_memory_enabled = bool((project.settings or {}).get("separate_memory_enabled", False))
    if previous_separate_memory_enabled != new_separate_memory_enabled:
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="PROJECT_MEMORY_SCOPE_UPDATED",
            details={
                "project_id": payload.project_id,
                "separate_memory_enabled": new_separate_memory_enabled,
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="memories",
        )
    return {
        "status": "success",
        "project": _serialize_project_response(project, is_owner=project.user_id == user.id),
    }



# -------------------
# Delete project (only owner can delete)
# -------------------
@projects_router.delete("/delete")
def delete_project_route(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Delete a project and all its members (owner only)."""
    check_projects_access(db, user.group_id)
    # Only owner can delete - use delete_project_with_members which handles member cleanup
    delete_project_with_members(db, user.id, project_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROJECT_DELETED",
        details={"project_id": project_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="projects",
    )
    return {"status": "success", "message": "Project deleted"}


# ============================================================================
# Project Sharing Endpoints
# ============================================================================

@projects_router.post("/share/link", response_model=CreateLinkShareResponse)
def create_link_share_route(
    payload: CreateLinkShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Create or get existing link share for a project (owner only)."""
    check_projects_access(db, user.group_id)
    result = create_project_link_share(
        db,
        user.id,
        payload.project_id,
        password=payload.password,
        expires_at=payload.expires_at,
        password_provided=("password" in payload.model_fields_set),
        expires_at_provided=("expires_at" in payload.model_fields_set),
        rotate=bool(payload.rotate),
    )
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROJECT_LINK_SHARE_CREATED",
        details={"project_id": payload.project_id, "share_id": result["share_id"]},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="projects",
    )
    
    return result


@projects_router.post("/share/link/delete")
def delete_link_share_route(
    payload: DeleteLinkShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Remove link share from a project (owner only)."""
    check_projects_access(db, user.group_id)
    result = delete_project_link_share(db, user.id, payload.project_id)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROJECT_LINK_SHARE_DELETED",
        details={"project_id": payload.project_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="projects",
    )
    
    return result


@projects_router.get("/share/status", response_model=ProjectShareStatusResponse)
def get_share_status_route(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Get share status for a project (owner only)."""
    check_projects_access(db, user.group_id)
    return get_project_share_status(db, user.id, project_id)


@projects_router.get("/shared/{share_id}", response_model=SharedProjectPreviewResponse)
def get_shared_project_preview_route(
    share_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Get preview of a shared project (for join page)."""
    check_projects_access(db, user.group_id)
    return get_project_share_preview(db, share_id, requesting_user_id=user.id)


@projects_router.post("/shared/{share_id}/join", response_model=JoinProjectResponse)
def join_project_route(
    share_id: str,
    request: Request,
    payload: JoinProjectByLinkRequest | None = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Join a project via link share."""
    check_projects_access(db, user.group_id)
    client_ip = extract_client_ip_from_request(
        request,
        trusted_proxy_networks=resolve_trusted_proxy_networks("RATE_LIMIT_TRUSTED_PROXIES", "TRUSTED_PROXIES"),
        default=None,
    )
    member = join_project_via_link(
        db,
        user.id,
        share_id,
        password=(payload.password if payload else None),
        client_ip=client_ip,
    )
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROJECT_JOINED",
        details={"project_id": member.project_id, "share_id": share_id},
        ip_address=client_ip or get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="projects",
    )
    
    return JoinProjectResponse(
        project_id=member.project_id,
        message="Successfully joined the project!",
    )


@projects_router.get("/members", response_model=ProjectMembersResponse)
def get_project_members_route(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(verified_user),
):
    """Get list of project members."""
    check_projects_access(db, user.group_id)
    members = get_project_members(db, project_id, user.id)
    return {"members": members}


@projects_router.post("/members/remove")
def remove_member_route(
    payload: RemoveProjectMemberRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Remove a member from a project. Owner can remove anyone, members can remove themselves."""
    check_projects_access(db, user.group_id)
    result = remove_project_member(db, user.id, payload.project_id, payload.user_id)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROJECT_MEMBER_REMOVED",
        details={"project_id": payload.project_id, "removed_user_id": payload.user_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="projects",
    )
    
    return result


@projects_router.post("/leave")
def leave_project_route(
    payload: LeaveProjectRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Leave a shared project (remove yourself as member)."""
    check_projects_access(db, user.group_id)
    result = remove_project_member(db, user.id, payload.project_id, user.id)
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROJECT_LEFT",
        details={"project_id": payload.project_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="projects",
    )
    
    return result


@projects_router.post("/invite", response_model=InviteUsersToProjectResponse)
def invite_users_to_project_route(
    payload: InviteUsersToProjectRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    """Invite users to a project by creating notifications (owner only)."""
    check_projects_access(db, user.group_id)
    
    # Verify user is owner
    if not is_project_owner(db, user.id, payload.project_id):
        raise HTTPException(status_code=403, detail="Only project owner can invite users")
    
    # Get project
    project = get_project_with_access(db, user.id, payload.project_id)
    
    # Create link share if not exists
    share_result = create_project_link_share(db, user.id, payload.project_id)
    
    # Get inviter's display name
    inviter_name = _get_user_display_name(db, user.id)
    
    invited_users = resolve_invitable_users_for_sharing(db, user, payload.user_ids)
    invited_count = 0
    for invited_user in invited_users:
        # Check if user already a member
        existing_member = db.query(ProjectMember).filter(
            ProjectMember.project_id == payload.project_id,
            ProjectMember.user_id == invited_user.id
        ).first()
        if existing_member:
            continue
        
        try:
            create_user_notification(
                db,
                message=f"{inviter_name} invited you to project: {project.title}",
                category="share_invitation",
                notification_type="info",
                user_ids=[invited_user.id],
                details={
                    "type": "share_invitation",
                    "item_type": "project",
                    "item_id": payload.project_id,
                    "item_title": project.title,
                    "share_id": share_result["share_id"],
                    "inviter_id": user.id,
                    "inviter_name": inviter_name,
                },
            )
            invited_count += 1
        except Exception:
            pass
    
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROJECT_USERS_INVITED",
        details={
            "project_id": payload.project_id,
            "invited_user_ids": [invited_user.id for invited_user in invited_users],
            "invited_count": invited_count,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="projects",
    )
    
    return InviteUsersToProjectResponse(
        invited_count=invited_count,
        message=f"Successfully invited {invited_count} user(s) to the project.",
    )
