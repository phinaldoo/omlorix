from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.logging.models import create_audit_log, get_audit_request_ip
from app.file_folders.models import (
    ShareType,
    create_file_folder,
    list_file_folders,
    get_file_folder,
    update_file_folder,
    delete_file_folder,
    add_files_to_folder,
    remove_files_from_folder,
    move_file_to_folder,
    create_folder_share,
    get_folder_share_status,
    delete_folder_share,
    get_shared_folder_by_share_id,
    get_shared_folder_preview,
    subscribe_to_shared_folder,
    unsubscribe_from_shared_folder,
    get_subscribed_folders,
    get_folder_subscriber_count,
    clone_shared_folder,
    detect_share_type_from_id,
    can_user_access_folder,
    FileFolders,
)
from app.file_folders.schemas import (
    FileFolderCreate,
    FileFolderUpdate,
    FileFolderResponse,
    FileFolderFileIds,
    MoveFileRequest,
    ShareFolderRequest,
    ShareFolderResponse,
    FolderShareStatusResponse,
    DeleteFolderShareRequest,
    SharedFolderPreviewResponse,
    AcceptSharedFolderResponse,
    CloneFolderResponse,
    ShareTypeEnum,
    InviteUsersRequest,
    InviteUsersResponse,
)
from app.files.access import accessible_folder_files_query, count_accessible_folder_files
from app.files.models import get_file
from app.files.schemas import FileList, minimize_shared_file_response
from app.users.models import get_user
from app.users.sharing import resolve_invitable_users_for_sharing
from app.userNotifications.models import create_user_notification
from app.groups.init import get_user_group_setting_value


file_folders_router = APIRouter(prefix="/api/v1/file-folders", tags=["file_folders"])


def _get_user_display_name(user_obj):
    if not user_obj:
        return "Unknown"
    first = getattr(user_obj, "first_name", None)
    last = getattr(user_obj, "last_name", None)
    if first or last:
        return " ".join(filter(None, [first, last])).strip()
    if getattr(user_obj, "email", None):
        return user_obj.email
    return "Unknown"


def _map_share_type(schema_type: ShareTypeEnum) -> ShareType:
    return {
        ShareTypeEnum.CLONE: ShareType.CLONE,
        ShareTypeEnum.LIVE: ShareType.LIVE,
        ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
    }.get(schema_type, ShareType.LIVE)


def _audit_file_folder_action(
    *,
    db_log: Session,
    request: Request,
    user_id: str,
    action: str,
    details: dict,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details,
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )


def _share_ids_for_response(folder: FileFolders, *, is_owner: bool) -> dict[str, str | None]:
    """Return share IDs only when the requesting user owns the folder."""
    if not is_owner:
        return {
            "clone_share_id": None,
            "live_share_id": None,
            "collaborate_share_id": None,
        }

    return {
        "clone_share_id": folder.clone_share_id,
        "live_share_id": folder.live_share_id,
        "collaborate_share_id": folder.collaborate_share_id,
    }


def _folder_to_response(folder, db, is_subscribed=False, share_type=None, owner_name=None, viewer_user_id=None):
    has_share = folder.clone_share_id or folder.live_share_id or folder.collaborate_share_id
    is_owner = not is_subscribed
    subscriber_count = get_folder_subscriber_count(db, folder.id) if is_owner and has_share else None
    file_count = count_accessible_folder_files(db, viewer_user_id or folder.user_id, folder.id)
    return FileFolderResponse(
        id=folder.id,
        user_id=None if is_subscribed else folder.user_id,
        name=folder.name,
        icon=folder.icon,
        icon_color=folder.icon_color,
        order=folder.order,
        system_kind=getattr(folder, "system_kind", None),
        **_share_ids_for_response(folder, is_owner=is_owner),
        created_at=folder.created_at,
        updated_at=folder.updated_at,
        is_subscribed=is_subscribed,
        share_type=share_type,
        owner_name=owner_name,
        subscriber_count=subscriber_count,
        file_count=file_count,
    )


# ---------------------------------------------------------------------------
# CRUD Endpoints
# ---------------------------------------------------------------------------
@file_folders_router.get("/", response_model=List[FileFolderResponse])
def list_folders_route(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    responses = []

    # User's own folders
    folders = list_file_folders(db, user.id)
    for folder in folders:
        responses.append(_folder_to_response(folder, db, viewer_user_id=user.id))

    # Subscribed folders
    subscribed_data = get_subscribed_folders(db, user.id)
    for folder, sub in subscribed_data:
        owner = get_user(db, folder.user_id)
        owner_name = _get_user_display_name(owner)
        responses.append(_folder_to_response(
            folder, db,
            is_subscribed=True,
            share_type=sub.share_type,
            owner_name=owner_name,
            viewer_user_id=user.id,
        ))

    return responses


@file_folders_router.post("/", response_model=FileFolderResponse, status_code=status.HTTP_201_CREATED)
def create_folder_route(
    payload: FileFolderCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    folder = create_file_folder(db, user.id, payload.name, payload.icon, payload.icon_color)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="FILE_FOLDER_CREATED",
        details={"folder_id": folder.id, "name": folder.name},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return _folder_to_response(folder, db, viewer_user_id=user.id)


@file_folders_router.patch("/{folder_id}", response_model=FileFolderResponse)
def update_folder_route(
    folder_id: str,
    payload: FileFolderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    folder = update_file_folder(
        db, user.id, folder_id,
        name=payload.name,
        icon=payload.icon,
        icon_color=payload.icon_color,
        order=payload.order,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="FILE_FOLDER_UPDATED",
        details={"folder_id": folder.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return _folder_to_response(folder, db, viewer_user_id=user.id)


@file_folders_router.delete("/{folder_id}")
def delete_folder_route(
    folder_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = delete_file_folder(db, user.id, folder_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="FILE_FOLDER_DELETED",
        details={"folder_id": folder_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return result


# ---------------------------------------------------------------------------
# File <-> Folder Operations
# ---------------------------------------------------------------------------
@file_folders_router.post("/{folder_id}/files")
def add_files_route(
    folder_id: str,
    payload: FileFolderFileIds,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = add_files_to_folder(db, user.id, folder_id, payload.file_ids)
    _audit_file_folder_action(
        db_log=db_log,
        request=request,
        user_id=user.id,
        action="FILE_FOLDER_FILES_ADDED",
        details={
            "folder_id": folder_id,
            "file_ids": payload.file_ids,
            "requested_file_count": len(payload.file_ids),
            "updated_file_count": result.get("updated", 0),
        },
    )
    return result


@file_folders_router.delete("/{folder_id}/files")
def remove_files_route(
    folder_id: str,
    payload: FileFolderFileIds,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = remove_files_from_folder(db, user.id, folder_id, payload.file_ids)
    _audit_file_folder_action(
        db_log=db_log,
        request=request,
        user_id=user.id,
        action="FILE_FOLDER_FILES_REMOVED",
        details={
            "folder_id": folder_id,
            "file_ids": payload.file_ids,
            "requested_file_count": len(payload.file_ids),
            "updated_file_count": result.get("updated", 0),
        },
    )
    return result


@file_folders_router.post("/move-file")
def move_file_route(
    payload: MoveFileRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    existing_file = get_file(db, payload.file_id, user.id)
    previous_folder_id = getattr(existing_file, "folder_id", None)
    result = move_file_to_folder(db, user.id, payload.file_id, payload.folder_id)
    _audit_file_folder_action(
        db_log=db_log,
        request=request,
        user_id=user.id,
        action="FILE_FOLDER_FILE_MOVED",
        details={
            "file_id": payload.file_id,
            "source_folder_id": previous_folder_id,
            "destination_folder_id": payload.folder_id,
        },
    )
    return result


@file_folders_router.get("/{folder_id}/files", response_model=List[FileList])
def get_folder_files_route(
    folder_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Get files in a folder. Works for own folders and subscribed shared folders."""
    # Check if user owns the folder
    folder = get_file_folder(db, user.id, folder_id)
    if folder:
        files = accessible_folder_files_query(db, user.id, folder_id).all()
        return [FileList.model_validate(f) for f in files]

    # Check if subscribed
    if can_user_access_folder(db, user.id, folder_id):
        files = accessible_folder_files_query(db, user.id, folder_id).all()
        return [
            FileList.model_validate(f)
            if str(getattr(f, "user_id", "")) == str(user.id)
            else minimize_shared_file_response(FileList.model_validate(f))
            for f in files
        ]

    raise HTTPException(status_code=404, detail="Folder not found or no access")


# ---------------------------------------------------------------------------
# Sharing Endpoints
# ---------------------------------------------------------------------------
@file_folders_router.post("/share", response_model=ShareFolderResponse)
def share_folder_route(
    payload: ShareFolderRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    share_type = _map_share_type(payload.share_type)
    result = create_folder_share(db, user.id, payload.folder_id, share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="FILE_FOLDER_SHARED",
        details={"folder_id": payload.folder_id, "share_id": result["share_id"], "share_type": result["share_type"]},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return ShareFolderResponse(**result)


@file_folders_router.get("/share/status", response_model=FolderShareStatusResponse)
def get_folder_share_status_route(
    folder_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    return get_folder_share_status(db, user.id, folder_id)


@file_folders_router.post("/share/delete")
def delete_folder_share_route(
    payload: DeleteFolderShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    model_share_type = _map_share_type(payload.share_type) if payload.share_type else None
    result = delete_folder_share(db, user.id, payload.folder_id, model_share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="FILE_FOLDER_SHARE_DELETED",
        details={"folder_id": payload.folder_id, "share_type": result.get("share_type")},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return result


@file_folders_router.get("/shared/{share_id}", response_model=SharedFolderPreviewResponse)
def get_shared_folder_preview_route(
    share_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    return get_shared_folder_preview(db, share_id, requesting_user_id=user.id)


@file_folders_router.post("/shared/{share_id}/accept", response_model=AcceptSharedFolderResponse)
def accept_shared_folder_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    detected_type = detect_share_type_from_id(db, share_id)
    if detected_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Clone shares should use the /clone endpoint")

    shared_folder = get_shared_folder_by_share_id(db, share_id)
    if not shared_folder:
        raise HTTPException(status_code=404, detail="Shared folder not found")

    if shared_folder.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot subscribe to your own folder")

    share_type = detected_type or ShareType.LIVE
    subscribe_to_shared_folder(db, user.id, shared_folder.id, share_type)

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="FILE_FOLDER_SUBSCRIBED",
        details={"share_id": share_id, "folder_id": shared_folder.id, "share_type": share_type.value},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )

    message = "Folder added to your workspace"
    if share_type == ShareType.COLLABORATE:
        message += " (you can edit)"
    else:
        message += " (view only, live sync enabled)"

    return AcceptSharedFolderResponse(
        folder_id=shared_folder.id,
        name=shared_folder.name,
        message=message,
    )


@file_folders_router.post("/clone/{share_id}", response_model=CloneFolderResponse)
def clone_folder_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    cloned = clone_shared_folder(db, user.id, share_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="FILE_FOLDER_CLONED",
        details={"share_id": share_id, "cloned_folder_id": cloned.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return CloneFolderResponse(
        folder_id=cloned.id,
        name=cloned.name,
        message="Folder cloned successfully!",
    )


@file_folders_router.post("/shared/{folder_id}/unsubscribe")
def unsubscribe_folder_route(
    folder_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = unsubscribe_from_shared_folder(db, user.id, folder_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="FILE_FOLDER_UNSUBSCRIBED",
        details={"folder_id": folder_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )
    return result


@file_folders_router.post("/invite", response_model=InviteUsersResponse)
def invite_users_to_folder(
    payload: InviteUsersRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    folder = db.query(FileFolders).filter(
        FileFolders.id == payload.item_id,
        FileFolders.user_id == user.id
    ).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    model_share_type = _map_share_type(payload.share_type)
    share_result = create_folder_share(db, user.id, payload.item_id, model_share_type)

    inviter = get_user(db, user.id)
    inviter_name = _get_user_display_name(inviter)

    invited_users = resolve_invitable_users_for_sharing(db, user, payload.user_ids)
    invited_count = 0
    for invited_user in invited_users:
        try:
            create_user_notification(
                db,
                message=f"{inviter_name} invited you to a file folder: {folder.name}",
                category="share_invitation",
                notification_type="info",
                user_ids=[invited_user.id],
                details={
                    "type": "share_invitation",
                    "item_type": "file_folder",
                    "item_id": payload.item_id,
                    "item_title": folder.name,
                    "share_id": share_result["share_id"],
                    "share_type": payload.share_type.value,
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
        action="FILE_FOLDER_USERS_INVITED",
        details={
            "folder_id": payload.item_id,
            "invited_user_ids": [invited_user.id for invited_user in invited_users],
            "share_type": payload.share_type.value,
            "invited_count": invited_count,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="files",
    )

    return InviteUsersResponse(
        invited_count=invited_count,
        message=f"Successfully invited {invited_count} user(s) to the folder.",
    )
