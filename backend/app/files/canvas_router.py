"""Canvas library endpoints; source persistence and authorization stay shared."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.dependencies import get_db, verified_user
from app.files.models import FileMember
from app.files.access import require_owned_file, list_file_members, stage_file_member
from app.files.history import (
    get_canvas_history,
    list_canvas_history,
    materialize_canvas_history,
    restore_canvas_history,
)
from app.files.schemas import (
    CanvasCreateRequest,
    CanvasFileSaveResponse,
    CanvasHistoryResponse,
    CanvasRestoreRequest,
    CanvasRestoreResponse,
    FileMemberRequest,
    FileMemberResponse,
    FileMemberListResponse,
)
from app.logging.models import stage_audit_log_event
from app.tools.canvas_markdown.utils import CanvasRevisionConflict, save_canvas_markdown

canvas_router = APIRouter()


@canvas_router.post("/canvas", response_model=CanvasFileSaveResponse)
def create_canvas_route(
    payload: CanvasCreateRequest,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    def audit(snapshot):
        stage_audit_log_event(
            db,
            user_id=str(user.id),
            action="CANVAS_CREATED",
            details=snapshot,
            category="files",
        )

    result = save_canvas_markdown(
        db,
        user_id=str(user.id),
        content=payload.content,
        filename=payload.filename,
        content_type=payload.content_type,
        edit_source="user",
        edited_by=str(user.id),
        allow_html_attachment=True,
        before_commit=audit,
    )
    return CanvasFileSaveResponse(**result)


@canvas_router.get("/canvas/{file_id}/history", response_model=CanvasHistoryResponse)
def canvas_history_route(
    file_id: str,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    return list_canvas_history(db, str(user.id), file_id, limit=limit, offset=offset)


@canvas_router.get("/canvas/{file_id}/history/{version_id}/content")
def canvas_history_content_route(
    file_id: str,
    version_id: str,
    preview: bool = False,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    _, version = get_canvas_history(db, str(user.id), file_id, version_id)
    if preview:
        with materialize_canvas_history(version).open("rb") as source:
            content = source.read(256 * 1024 + 1)
        return PlainTextResponse(
            content[: 256 * 1024].decode("utf-8", errors="replace"),
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Content-Truncated": str(len(content) > 256 * 1024).lower(),
            },
        )
    # Never execute historical HTML or expose it under the authenticated origin.
    return FileResponse(
        materialize_canvas_history(version),
        media_type="application/octet-stream",
        filename=version.file_name,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@canvas_router.post("/canvas/{file_id}/restore", response_model=CanvasRestoreResponse)
def canvas_restore_route(
    file_id: str,
    payload: CanvasRestoreRequest,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    get_canvas_history(db, str(user.id), file_id, payload.version_id)
    stage_audit_log_event(
        db,
        user_id=str(user.id),
        action="CANVAS_RESTORED",
        details={"file_id": file_id, "version_id": payload.version_id},
        category="files",
    )
    try:
        return restore_canvas_history(
            db, str(user.id), file_id, payload.version_id, payload.expected_revision
        )
    except CanvasRevisionConflict as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "current_revision": exc.current_revision},
        ) from exc


@canvas_router.get("/canvas/{file_id}/members", response_model=FileMemberListResponse)
def file_members_route(
    file_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    require_owned_file(db, user.id, file_id)
    return list_file_members(db, file_id, limit=limit, offset=offset)


@canvas_router.put("/canvas/{file_id}/members", response_model=FileMemberResponse)
def set_file_member_route(
    file_id: str,
    payload: FileMemberRequest,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    require_owned_file(db, user.id, file_id)
    member, target = stage_file_member(
        db, str(user.id), file_id, payload.email, payload.role
    )
    stage_audit_log_event(
        db,
        user_id=str(user.id),
        action="FILE_MEMBER_UPDATED",
        details={"file_id": file_id, "member_id": target.id, "role": payload.role},
        category="files",
    )
    db.commit()
    return {
        "user_id": member.user_id,
        "email": target.email,
        "role": member.role,
        "granted_at": member.granted_at,
    }


@canvas_router.delete("/canvas/{file_id}/members/{member_id}", status_code=204)
def delete_file_member_route(
    file_id: str,
    member_id: str,
    user=Depends(verified_user),
    db: Session = Depends(get_db),
):
    require_owned_file(db, user.id, file_id)
    db.query(FileMember).filter(
        FileMember.file_id == file_id, FileMember.user_id == member_id
    ).delete(synchronize_session=False)
    stage_audit_log_event(
        db,
        user_id=str(user.id),
        action="FILE_MEMBER_REMOVED",
        details={"file_id": file_id, "member_id": member_id},
        category="files",
    )
    db.commit()
    return Response(status_code=204)
