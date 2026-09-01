"""FastAPI endpoints for personal and optional shared project memories."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.groups.init import get_user_group_setting_value
from app.logging.models import create_audit_log, get_audit_request_ip
from app.memories.runtime import get_memory_policy, get_memory_settings
from app.memories.schemas import (
    MemoryCreate,
    MemoryExportPayload,
    MemoryImportItem,
    MemoryImportResponse,
    MemoryListResponse,
    MemoryResponse,
    MemorySettingsResponse,
    MemorySettingsUpdate,
    MemoryUpdate,
)
from app.memories.service import (
    MemoryScope,
    create_memory,
    delete_memory,
    import_memories,
    import_memory_export,
    list_memories,
    update_memory,
)
from app.projects.models import get_project_with_access
from app.users.init import update_user_settings_bulk
from app.utils.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MAX_PAGE_OFFSET,
    page_from_limited_items,
)


memories_router = APIRouter(tags=["memories"])
scoped_memories_router = APIRouter(prefix="/api/v1/memories", tags=["memories"])
project_memory_transfer_router = APIRouter(
    prefix="/api/v1/projects/{project_id}/memories",
    tags=["memories"],
)


def _ensure_feature_available(user_id: str, db: Session) -> None:
    """Require the group-level feature switch for settings and writes."""

    if not get_user_group_setting_value(user_id, "memories", "enabled_memories", db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Memories feature disabled for your group",
        )


def _resolve_scope(
    *,
    db: Session,
    user,
    project_id: str | None,
    require_write: bool = False,
) -> MemoryScope:
    """Resolve ownership, project access, and the write policy once."""

    normalized_project_id = str(project_id or "").strip() or None
    project = None
    if normalized_project_id:
        project = get_project_with_access(db, user.id, normalized_project_id)
        scope = MemoryScope.project(normalized_project_id)
    else:
        scope = MemoryScope.personal(user.id)

    if not require_write:
        return scope

    policy = get_memory_policy(
        db,
        user.id,
        project_id=normalized_project_id,
        project=project,
    )
    if not policy.feature_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Memories feature disabled for your group",
        )
    if not policy.account_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Memories are not enabled for this user.",
        )
    if normalized_project_id and not policy.project_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Separate shared project memory is not enabled for this project.",
        )
    return scope


def _audit(
    *,
    db_log: Session,
    request: Request,
    db: Session,
    user_id: str,
    scope: MemoryScope,
    event: str,
    details: dict | None = None,
) -> None:
    """Write one consistently shaped personal or project memory audit event."""

    event_name = f"PROJECT_MEMORY_{event}" if scope.is_project else f"MEMORY_{event}"
    payload = dict(details or {})
    if scope.project_id:
        payload["project_id"] = scope.project_id
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=event_name,
        details=payload,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="memories",
    )


@scoped_memories_router.get("/settings", response_model=MemorySettingsResponse)
def get_memory_settings_route(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Return the user's memory settings."""

    _ensure_feature_available(user.id, db)
    return get_memory_settings(db, user.id)


@scoped_memories_router.patch("/settings", response_model=MemorySettingsResponse)
def update_memory_settings_route(
    payload: MemorySettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Update all supplied memory settings in one transaction."""

    _ensure_feature_available(user.id, db)
    updates = payload.model_dump(exclude_none=True)
    if updates:
        update_user_settings_bulk(user.id, {"memory": updates}, db)
    settings = get_memory_settings(db, user.id)
    _audit(
        db_log=db_log,
        request=request,
        db=db,
        user_id=user.id,
        scope=MemoryScope.personal(user.id),
        event="SETTINGS_UPDATED",
        details=settings,
    )
    return settings


@project_memory_transfer_router.post(
    "/import",
    response_model=MemoryImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_memories_route(
    project_id: str,
    payload: list[MemoryImportItem] | MemoryExportPayload,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Import either interactive items or a canonical archive in one batch."""

    scope = _resolve_scope(db=db, user=user, project_id=project_id, require_write=True)
    if isinstance(payload, MemoryExportPayload):
        result = import_memory_export(db, scope, payload)
        import_mode = "data_control_export"
    else:
        result = import_memories(db, scope, payload)
        import_mode = "list"
    _audit(
        db_log=db_log,
        request=request,
        db=db,
        user_id=user.id,
        scope=scope,
        event="IMPORTED",
        details={
            "total_received": result["total_received"],
            "created_count": result["created_count"],
            "deduped_count": result["deduped_count"],
            "import_mode": import_mode,
        },
    )
    return result


@scoped_memories_router.get("", response_model=MemoryListResponse)
def list_memories_route(
    project_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """List an accessible personal or project scope."""

    scope = _resolve_scope(db=db, user=user, project_id=project_id)
    rows = list_memories(db, scope, limit=limit + 1, offset=offset)
    items, has_more = page_from_limited_items(rows, limit=limit)
    return MemoryListResponse(
        items=items, limit=limit, offset=offset, has_more=has_more
    )


@scoped_memories_router.post(
    "", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED
)
def create_memory_route(
    payload: MemoryCreate,
    request: Request,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Create a personal or shared project memory."""

    scope = _resolve_scope(db=db, user=user, project_id=project_id, require_write=True)
    memory, created = create_memory(db, scope, payload.content)
    _audit(
        db_log=db_log,
        request=request,
        db=db,
        user_id=user.id,
        scope=scope,
        event="CREATED" if created else "DEDUPED",
        details={"memory_id": memory.id},
    )
    return memory


@scoped_memories_router.patch("/{memory_id}", response_model=MemoryResponse)
def update_memory_route(
    memory_id: str,
    payload: MemoryUpdate,
    request: Request,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Update a memory in a writable scope."""

    scope = _resolve_scope(db=db, user=user, project_id=project_id, require_write=True)
    memory = update_memory(db, scope, memory_id, payload.content)
    _audit(
        db_log=db_log,
        request=request,
        db=db,
        user_id=user.id,
        scope=scope,
        event="UPDATED",
        details={"memory_id": memory.id},
    )
    return memory


@scoped_memories_router.delete("/{memory_id}", status_code=status.HTTP_200_OK)
def delete_memory_route(
    memory_id: str,
    request: Request,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Delete an existing memory even when future memory creation is disabled."""

    scope = _resolve_scope(db=db, user=user, project_id=project_id)
    result = delete_memory(db, scope, memory_id)
    _audit(
        db_log=db_log,
        request=request,
        db=db,
        user_id=user.id,
        scope=scope,
        event="DELETED",
        details={"memory_id": memory_id},
    )
    return result


memories_router.include_router(scoped_memories_router)
memories_router.include_router(project_memory_transfer_router)
