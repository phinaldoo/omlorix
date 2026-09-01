from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session
from starlette import status

from app.agents.models import ShareType, SharedUserAgentSubscription, UserAgent
from app.agents.schemas import (
    AcceptSharedAgentResponse,
    AttachAgentFilesRequest,
    AgentListResponse,
    AgentResponse,
    AgentShareStatusResponse,
    AgentAssetResponse,
    CloneAgentResponse,
    CreateAgentRequest,
    DeleteAgentShareRequest,
    InviteUsersRequest,
    InviteUsersResponse,
    ShareAgentRequest,
    ShareAgentResponse,
    SharedAgentPreviewResponse,
    UpdateAgentRequest,
)
from app.agents.utils import (
    accept_shared_agent,
    clone_shared_agent,
    create_agent_invites,
    create_agent_share,
    create_user_agent,
    create_user_agent_asset,
    create_user_agent_asset_from_file,
    delete_agent_share,
    delete_user_agent,
    delete_user_agent_asset,
    get_agent_detail,
    get_agent_share_status,
    get_shared_agent_preview,
    list_accessible_agents,
    list_agent_assets_for_user,
    unsubscribe_from_shared_agent,
    update_user_agent,
)
from app.dependencies import get_db, get_db_log, verified_user
from app.groups.init import get_user_group_setting_value
from app.logging.models import create_audit_log, get_audit_request_ip
from app.userNotifications.models import create_user_notification
from app.users.models import get_user
from app.users.sharing import resolve_invitable_users_for_sharing


agents_router = APIRouter(prefix="/api/v1/agents", tags=["agents"])
logger = logging.getLogger(__name__)

MAX_AGENT_ASSET_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_AGENT_ASSET_FILES_PER_UPLOAD = 10
AGENT_ASSET_READ_CHUNK_BYTES = 1024 * 1024  # 1 MB


def _audit_agent_event(
    db_log: Session,
    request: Request,
    user_id: str,
    action: str,
    details: dict | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="agents",
    )


def ensure_agents_enabled(user, db: Session) -> None:
    if not bool(get_user_group_setting_value(user.id, "agents", "allow_agents", db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agents are disabled for your group")


def ensure_agent_sharing_allowed(user, db: Session) -> None:
    ensure_agents_enabled(user, db)
    if not bool(get_user_group_setting_value(user.id, "agents", "allow_agent_share", db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent sharing is disabled for your group")


def agent_has_existing_share_state(db: Session, agent_id: str, user_id: str, share_type: ShareType) -> bool:
    """Return true when an owned agent already has share state for this type."""
    agent = db.query(UserAgent).filter(
        UserAgent.id == agent_id,
        UserAgent.user_id == user_id,
    ).first()
    if not agent:
        return False
    share_id_attr = {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type)
    if share_id_attr and getattr(agent, share_id_attr, None):
        return True
    return (
        db.query(SharedUserAgentSubscription)
        .filter(
            SharedUserAgentSubscription.agent_id == agent_id,
            SharedUserAgentSubscription.share_type == share_type.value,
        )
        .count()
        > 0
    )


def ensure_agent_sharing_allowed_or_existing(user, db: Session, agent_id: str, share_type: ShareType) -> None:
    """Allow new sharing only when enabled, but preserve the same share type."""
    ensure_agents_enabled(user, db)
    if not agent_has_existing_share_state(db, agent_id, user.id, share_type):
        ensure_agent_sharing_allowed(user, db)


async def _read_upload_file_limited(
    file: UploadFile,
    *,
    max_bytes: int = MAX_AGENT_ASSET_UPLOAD_BYTES,
) -> bytes:
    """Read upload file with size limit."""
    declared_size = getattr(file, "size", None)
    if isinstance(declared_size, int) and declared_size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File '{file.filename or 'asset'}' exceeds the {max_bytes // (1024 * 1024)} MB limit.",
        )

    content_length = (file.headers or {}).get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{file.filename or 'asset'}' exceeds the {max_bytes // (1024 * 1024)} MB limit.",
                )
        except ValueError:
            pass

    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await file.read(AGENT_ASSET_READ_CHUNK_BYTES)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File '{file.filename or 'asset'}' exceeds the {max_bytes // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)

    return b"".join(chunks)


def _inviter_name(db: Session, user_id: str) -> str:
    """Get display name for inviter."""
    inviter = get_user(db, user_id)
    if inviter and inviter.first_name and inviter.last_name:
        return f"{inviter.first_name} {inviter.last_name}"
    if inviter and inviter.first_name:
        return inviter.first_name
    if inviter and inviter.email:
        return inviter.email.split("@")[0]
    return "Someone"


@agents_router.get("", response_model=AgentListResponse)
def list_agents_route(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    return {"agents": list_accessible_agents(db, user.id)}


@agents_router.post("", response_model=AgentResponse, status_code=201)
def create_agent_route(
    payload: CreateAgentRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    agent = create_user_agent(
        db,
        user_id=user.id,
        name=payload.name,
        icon=payload.icon,
        base_model_id=payload.base_model_id,
        instruction=payload.instruction,
        skill_id=payload.skill_id,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="AGENT_CREATED",
        details={"agent_id": agent["id"], "base_model_id": agent["base_model_id"]},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="agents",
    )
    return agent


@agents_router.post("/share", response_model=ShareAgentResponse)
def share_agent_route(
    payload: ShareAgentRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    share_type = ShareType(payload.share_type.value)
    ensure_agent_sharing_allowed_or_existing(user, db, payload.agent_id, share_type)
    share = create_agent_share(
        db,
        user_id=user.id,
        agent_id=payload.agent_id,
        share_type=share_type,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="AGENT_SHARED",
        details={"agent_id": payload.agent_id, "share_type": payload.share_type.value},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="agents",
    )
    return share


@agents_router.get("/share/status", response_model=AgentShareStatusResponse)
def agent_share_status_route(
    agent_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    return get_agent_share_status(db, user_id=user.id, agent_id=agent_id)


@agents_router.post("/share/delete")
def delete_agent_share_route(
    payload: DeleteAgentShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    share_type = ShareType(payload.share_type.value) if payload.share_type else None
    result = delete_agent_share(db, user_id=user.id, agent_id=payload.agent_id, share_type=share_type)
    _audit_agent_event(
        db_log,
        request,
        user.id,
        "AGENT_SHARE_DELETED",
        {"agent_id": payload.agent_id, "share_type": payload.share_type.value if payload.share_type else None},
    )
    return result


@agents_router.get("/shared/{share_id}", response_model=SharedAgentPreviewResponse)
def get_shared_agent_route(
    share_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    return get_shared_agent_preview(db, share_id=share_id, requesting_user_id=user.id)


@agents_router.post("/shared/{share_id}/accept", response_model=AcceptSharedAgentResponse)
def accept_shared_agent_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    result = accept_shared_agent(db, user_id=user.id, share_id=share_id)
    _audit_agent_event(
        db_log,
        request,
        user.id,
        "SHARED_AGENT_ACCEPTED",
        {"share_id": share_id, "share_type": result.get("share_type")},
    )
    return result


@agents_router.post("/shared/{share_id}/clone", response_model=CloneAgentResponse)
def clone_shared_agent_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    result = clone_shared_agent(db, user_id=user.id, share_id=share_id)
    _audit_agent_event(db_log, request, user.id, "SHARED_AGENT_CLONED", {"share_id": share_id})
    return result


@agents_router.post("/shared/{agent_id}/unsubscribe")
def unsubscribe_shared_agent_route(
    agent_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    result = unsubscribe_from_shared_agent(db, user_id=user.id, agent_id=agent_id)
    _audit_agent_event(db_log, request, user.id, "SHARED_AGENT_UNSUBSCRIBED", {"agent_id": agent_id})
    return result


@agents_router.post("/invite", response_model=InviteUsersResponse)
def invite_users_to_agent_route(
    payload: InviteUsersRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    share_type = ShareType(payload.share_type.value)
    ensure_agent_sharing_allowed_or_existing(user, db, payload.item_id, share_type)
    agent, share = create_agent_invites(
        db,
        user_id=user.id,
        agent_id=payload.item_id,
        share_type=share_type,
    )

    inviter_name = _inviter_name(db, user.id)
    invited_users = resolve_invitable_users_for_sharing(db, user, payload.user_ids)
    invited_count = 0
    for invited_user in invited_users:
        try:
            create_user_notification(
                db,
                message=f"{inviter_name} invited you to an agent: {agent.name}",
                category="share_invitation",
                notification_type="info",
                user_ids=[invited_user.id],
                details={
                    "type": "share_invitation",
                    "item_type": "agent",
                    "item_id": payload.item_id,
                    "item_title": agent.name,
                    "share_id": share["share_id"],
                    "share_type": payload.share_type.value,
                    "inviter_id": user.id,
                    "inviter_name": inviter_name,
                },
            )
            invited_count += 1
        except Exception as exc:
            logger.exception(
                "Failed to create invitation notification for invited_user_id=%s agent_id=%s: %s",
                invited_user.id,
                payload.item_id,
                exc,
            )
            continue

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="AGENT_USERS_INVITED",
        details={
            "agent_id": payload.item_id,
            "invited_user_ids": [invited_user.id for invited_user in invited_users],
            "share_type": payload.share_type.value,
            "invited_count": invited_count,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="agents",
    )
    return {
        "invited_count": invited_count,
        "message": f"Successfully invited {invited_count} user(s) to the agent.",
    }


@agents_router.get("/{agent_id}", response_model=AgentResponse)
def get_agent_route(
    agent_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    return get_agent_detail(db, user_id=user.id, agent_id=agent_id)


@agents_router.patch("/{agent_id}", response_model=AgentResponse)
def update_agent_route(
    agent_id: str,
    payload: UpdateAgentRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    agent = update_user_agent(
        db,
        user_id=user.id,
        agent_id=agent_id,
        name=payload.name,
        icon=payload.icon,
        base_model_id=payload.base_model_id,
        instruction=payload.instruction,
        skill_id=payload.skill_id,
        skill_id_provided="skill_id" in getattr(payload, "model_fields_set", set()),
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="AGENT_UPDATED",
        details={"agent_id": agent_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="agents",
    )
    return agent


@agents_router.delete("/{agent_id}")
def delete_agent_route(
    agent_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    result = delete_user_agent(db, user_id=user.id, agent_id=agent_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="AGENT_DELETED",
        details={"agent_id": agent_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="agents",
    )
    return result


@agents_router.get("/{agent_id}/assets", response_model=List[AgentAssetResponse])
def list_agent_assets_route(
    agent_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    return list_agent_assets_for_user(db, user_id=user.id, agent_id=agent_id)


@agents_router.post("/{agent_id}/assets", response_model=List[AgentAssetResponse], status_code=201)
async def upload_agent_assets_route(
    agent_id: str,
    request: Request,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    if len(files) > MAX_AGENT_ASSET_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot upload more than {MAX_AGENT_ASSET_FILES_PER_UPLOAD} files at once.",
        )

    uploaded: list[dict] = []
    for file in files:
        content = await _read_upload_file_limited(file)
        uploaded.append(
            create_user_agent_asset(
                db,
                user_id=user.id,
                agent_id=agent_id,
                filename=file.filename or "asset",
                content=content,
            )
        )
    _audit_agent_event(
        db_log,
        request,
        user.id,
        "AGENT_ASSETS_UPLOADED",
        {
            "agent_id": agent_id,
            "asset_count": len(uploaded),
            "filenames": [file.filename or "asset" for file in files],
        },
    )
    return uploaded


@agents_router.post("/{agent_id}/assets/from-files", response_model=List[AgentAssetResponse], status_code=201)
def attach_agent_files_route(
    agent_id: str,
    payload: AttachAgentFilesRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    unique_file_ids: list[str] = []
    seen_file_ids: set[str] = set()
    for raw_file_id in payload.file_ids:
        file_id = str(raw_file_id or "").strip()
        if not file_id or file_id in seen_file_ids:
            continue
        seen_file_ids.add(file_id)
        unique_file_ids.append(file_id)
    if not unique_file_ids:
        raise HTTPException(status_code=400, detail="At least one file_id is required.")
    if len(unique_file_ids) > MAX_AGENT_ASSET_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot attach more than {MAX_AGENT_ASSET_FILES_PER_UPLOAD} files at once.",
        )

    attached = [
        create_user_agent_asset_from_file(
            db,
            user_id=user.id,
            agent_id=agent_id,
            file_id=file_id,
        )
        for file_id in unique_file_ids
    ]
    _audit_agent_event(
        db_log,
        request,
        user.id,
        "AGENT_ASSETS_ATTACHED",
        {"agent_id": agent_id, "asset_count": len(attached), "file_ids": unique_file_ids},
    )
    return attached


@agents_router.delete("/{agent_id}/assets/{asset_id}")
def delete_agent_asset_route(
    agent_id: str,
    asset_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_agents_enabled(user, db)
    result = delete_user_agent_asset(db, user_id=user.id, agent_id=agent_id, asset_id=asset_id)
    _audit_agent_event(db_log, request, user.id, "AGENT_ASSET_DELETED", {"agent_id": agent_id, "asset_id": asset_id})
    return result
