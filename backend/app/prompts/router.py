from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.groups.init import get_user_group_setting_value
from app.logging.models import create_audit_log, get_audit_request_ip
from app.prompts.models import (
    Prompts,
    ShareType,
    SharedPromptSubscription,
    create_user_prompt,
    update_user_prompt,
    delete_user_prompt,
    list_user_prompts,
    create_prompt_share,
    get_prompt_share_status,
    delete_prompt_share,
    get_shared_prompt_preview,
    subscribe_to_shared_prompt,
    unsubscribe_from_shared_prompt,
    get_subscribed_prompts,
    get_prompt_subscriber_count,
    clone_shared_prompt,
    detect_share_type_from_id,
    get_shared_prompt_by_share_id,
    get_subscription_for_prompt,
    can_user_edit_prompt,
)
from app.prompts.schemas import (
    PromptCreate,
    PromptUpdate,
    PromptListItem,
    PromptListResponse,
    PromptResponse,
    SharePromptRequest,
    SharePromptResponse,
    PromptShareStatusResponse,
    DeletePromptShareRequest,
    SharedPromptPreviewResponse,
    AcceptSharedPromptResponse,
    ClonePromptResponse,
    ShareTypeEnum,
    InviteUsersRequest,
    InviteUsersResponse,
)
from app.users.models import get_user
from app.users.sharing import resolve_invitable_users_for_sharing
from app.userNotifications.models import create_user_notification
from app.utils.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MAX_PAGE_OFFSET,
    merged_window_limit,
    page_from_merged_window,
)


prompts_router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])


def ensure_prompts_enabled(user, db: Session):
    is_enabled = get_user_group_setting_value(user.id, "prompts", "enabled_prompts", db)
    if not is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Prompt library is disabled for your group")


def ensure_prompt_sharing_allowed(user, db: Session):
    is_allowed = get_user_group_setting_value(user.id, "prompts", "allow_prompt_share", db)
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Prompt sharing is disabled for your group",
        )


def prompt_has_existing_share_state(db: Session, prompt_id: str, user_id: str, share_type: ShareType) -> bool:
    """Return true when an owned prompt already has share state for this type."""
    prompt = db.query(Prompts).filter(
        Prompts.id == prompt_id,
        Prompts.user_id == user_id,
    ).first()
    if not prompt:
        return False
    share_id_attr = {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type)
    if share_id_attr and getattr(prompt, share_id_attr, None):
        return True
    return (
        db.query(SharedPromptSubscription)
        .filter(
            SharedPromptSubscription.prompt_id == prompt_id,
            SharedPromptSubscription.share_type == share_type.value,
        )
        .count()
        > 0
    )


def ensure_prompt_sharing_allowed_or_existing(user, db: Session, prompt_id: str, share_type: ShareType):
    """Allow new sharing only when enabled, but preserve the same share type."""
    if not prompt_has_existing_share_state(db, prompt_id, user.id, share_type):
        ensure_prompt_sharing_allowed(user, db)


def _get_user_display_name(user_obj):
    if not user_obj:
        return "Unknown"
    first = getattr(user_obj, "first_name", None)
    last = getattr(user_obj, "last_name", None)
    if first or last:
        return " ".join(filter(None, [first, last])).strip()
    return "Unknown"


def _share_ids_for_response(prompt: Prompts, *, is_owner: bool) -> dict[str, str | None]:
    """Return share IDs only when the requesting user owns the prompt.

    Share IDs are bearer capabilities: clone/live/collaborate IDs grant access
    when presented to share endpoints. Subscribed users must not receive the
    owner's share IDs, even when they can view or edit prompt content.
    """
    if not is_owner:
        return {
            "clone_share_id": None,
            "live_share_id": None,
            "collaborate_share_id": None,
        }

    return {
        "clone_share_id": prompt.clone_share_id,
        "live_share_id": prompt.live_share_id,
        "collaborate_share_id": prompt.collaborate_share_id,
    }


def _response_user_id(*, owner_user_id: str, is_owner: bool) -> str | None:
    return owner_user_id if is_owner else None


def _prompt_revision_fields(db: Session, prompt: Prompts) -> dict[str, str | int | None]:
    """Return revision and privacy-safe editor attribution for API responses."""
    editor_id = getattr(prompt, "last_edited_by_user_id", None) or prompt.user_id
    editor = get_user(db, editor_id) if editor_id else None
    return {
        "revision": int(getattr(prompt, "revision", 1) or 1),
        "last_updated_by_name": _get_user_display_name(editor),
    }


def _prompt_sort_key(item: PromptListItem) -> datetime:
    timestamp = item.updated_at or item.created_at
    if isinstance(timestamp, datetime):
        return timestamp
    return datetime.min.replace(tzinfo=timezone.utc)


@prompts_router.get("/", response_model=PromptListResponse)
def list_prompts_route(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """List all prompts for the user (own + subscribed)."""
    ensure_prompts_enabled(user, db)

    fetch_limit = merged_window_limit(limit, offset)
    responses = []

    own_prompts = list_user_prompts(db, user.id, limit=fetch_limit)
    for prompt in own_prompts:
        has_shares = prompt.clone_share_id or prompt.live_share_id or prompt.collaborate_share_id
        subscriber_count = get_prompt_subscriber_count(db, prompt.id) if has_shares else None
        content_preview = (prompt.content[:180] + "...") if prompt.content and len(prompt.content) > 180 else (prompt.content or "")
        responses.append(
            PromptListItem(
                id=prompt.id,
                user_id=_response_user_id(owner_user_id=prompt.user_id, is_owner=True),
                title=prompt.title,
                description=prompt.description or "",
                content_preview=content_preview,
                **_share_ids_for_response(prompt, is_owner=True),
                created_at=prompt.created_at,
                updated_at=prompt.updated_at,
                **_prompt_revision_fields(db, prompt),
                is_subscribed=False,
                subscriber_count=subscriber_count,
            )
        )

    subscribed_data = get_subscribed_prompts(db, user.id, limit=fetch_limit)
    for prompt, subscription in subscribed_data:
        owner = get_user(db, prompt.user_id)
        owner_name = _get_user_display_name(owner)
        content_preview = (prompt.content[:180] + "...") if prompt.content and len(prompt.content) > 180 else (prompt.content or "")
        responses.append(
            PromptListItem(
                id=prompt.id,
                user_id=_response_user_id(owner_user_id=prompt.user_id, is_owner=False),
                title=prompt.title,
                description=prompt.description or "",
                content_preview=content_preview,
                **_share_ids_for_response(prompt, is_owner=False),
                created_at=prompt.created_at,
                updated_at=prompt.updated_at,
                **_prompt_revision_fields(db, prompt),
                is_subscribed=True,
                share_type=subscription.share_type,
                owner_name=owner_name,
            )
        )

    responses.sort(key=_prompt_sort_key, reverse=True)
    items, has_more = page_from_merged_window(responses, limit=limit, offset=offset)
    return PromptListResponse(items=items, limit=limit, offset=offset, has_more=has_more)


@prompts_router.get("/{prompt_id}", response_model=PromptResponse)
def get_prompt_route(
    prompt_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)

    prompt = db.query(Prompts).filter(Prompts.id == prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")

    is_owner = prompt.user_id == user.id
    subscription = get_subscription_for_prompt(db, user.id, prompt_id) if not is_owner else None
    if not is_owner and not subscription:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this prompt")

    owner_name = None
    if not is_owner:
        owner = get_user(db, prompt.user_id)
        owner_name = _get_user_display_name(owner)

    return PromptResponse(
        id=prompt.id,
        user_id=_response_user_id(owner_user_id=prompt.user_id, is_owner=is_owner),
        title=prompt.title,
        description=prompt.description or "",
        content=prompt.content or "",
        **_share_ids_for_response(prompt, is_owner=is_owner),
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
        **_prompt_revision_fields(db, prompt),
        is_subscribed=subscription is not None,
        share_type=subscription.share_type if subscription else None,
        owner_name=owner_name,
    )


@prompts_router.post("/", response_model=PromptResponse, status_code=status.HTTP_201_CREATED)
def create_prompt_route(
    payload: PromptCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)
    prompt = create_user_prompt(
        db=db,
        user_id=user.id,
        title=payload.title,
        description=payload.description,
        content=payload.content,
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROMPT_CREATED",
        details={"prompt_id": prompt.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )
    return PromptResponse(
        id=prompt.id,
        user_id=prompt.user_id,
        title=prompt.title,
        description=prompt.description or "",
        content=prompt.content or "",
        **_share_ids_for_response(prompt, is_owner=True),
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
        **_prompt_revision_fields(db, prompt),
        is_subscribed=False,
    )


@prompts_router.patch("/{prompt_id}", response_model=PromptResponse)
def update_prompt_route(
    prompt_id: str,
    payload: PromptUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)

    prompt = db.query(Prompts).filter(Prompts.id == prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")

    is_owner = prompt.user_id == user.id
    if not is_owner and not can_user_edit_prompt(db, user.id, prompt_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to edit this prompt")

    target_user_id = prompt.user_id
    updated = update_user_prompt(
        db=db,
        user_id=target_user_id,
        prompt_id=prompt_id,
        title=payload.title,
        description=payload.description,
        content=payload.content,
        expected_revision=payload.expected_revision,
        actor_user_id=user.id,
    )

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROMPT_UPDATED",
        details={"prompt_id": updated.id, "owner_id": updated.user_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )

    subscription = get_subscription_for_prompt(db, user.id, prompt_id) if updated.user_id != user.id else None
    owner_name = None
    if updated.user_id != user.id:
        owner = get_user(db, updated.user_id)
        owner_name = _get_user_display_name(owner)

    return PromptResponse(
        id=updated.id,
        user_id=_response_user_id(owner_user_id=updated.user_id, is_owner=updated.user_id == user.id),
        title=updated.title,
        description=updated.description or "",
        content=updated.content or "",
        **_share_ids_for_response(updated, is_owner=updated.user_id == user.id),
        created_at=updated.created_at,
        updated_at=updated.updated_at,
        **_prompt_revision_fields(db, updated),
        is_subscribed=subscription is not None,
        share_type=subscription.share_type if subscription else None,
        owner_name=owner_name,
    )


@prompts_router.delete("/{prompt_id}", status_code=status.HTTP_200_OK)
def delete_prompt_route(
    prompt_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)

    prompt = db.query(Prompts).filter(Prompts.id == prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")

    is_owner = prompt.user_id == user.id
    if is_owner:
        result = delete_user_prompt(db=db, user_id=user.id, prompt_id=prompt_id)
        action = "PROMPT_DELETED"
    else:
        result = unsubscribe_from_shared_prompt(db, user.id, prompt_id)
        action = "PROMPT_UNSUBSCRIBED"

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action=action,
        details={"prompt_id": prompt_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )
    return result


# ============================================================================
# Prompt Sharing Endpoints
# ============================================================================

@prompts_router.post("/share", response_model=SharePromptResponse)
def share_prompt_route(
    payload: SharePromptRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)

    share_type_map = {
        ShareTypeEnum.CLONE: ShareType.CLONE,
        ShareTypeEnum.LIVE: ShareType.LIVE,
        ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
    }
    model_share_type = share_type_map.get(payload.share_type, ShareType.LIVE)
    ensure_prompt_sharing_allowed_or_existing(user, db, payload.prompt_id, model_share_type)

    result = create_prompt_share(db, user.id, payload.prompt_id, model_share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROMPT_SHARED",
        details={
            "prompt_id": payload.prompt_id,
            "share_id": result["share_id"],
            "share_type": result["share_type"],
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )
    return SharePromptResponse(**result)


@prompts_router.get("/share/status", response_model=PromptShareStatusResponse)
def get_prompt_share_status_route(
    prompt_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)
    return get_prompt_share_status(db, user.id, prompt_id)


@prompts_router.post("/share/delete")
def delete_prompt_share_route(
    payload: DeletePromptShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)

    model_share_type = None
    if payload.share_type:
        share_type_map = {
            ShareTypeEnum.CLONE: ShareType.CLONE,
            ShareTypeEnum.LIVE: ShareType.LIVE,
            ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
        }
        model_share_type = share_type_map.get(payload.share_type)

    result = delete_prompt_share(db, user.id, payload.prompt_id, model_share_type)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROMPT_SHARE_DELETED",
        details={"prompt_id": payload.prompt_id, "share_type": result.get("share_type")},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )
    return result


@prompts_router.get("/shared/{share_id}", response_model=SharedPromptPreviewResponse)
def get_shared_prompt_preview_route(
    share_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)
    return get_shared_prompt_preview(db, share_id, requesting_user_id=user.id)


@prompts_router.post("/shared/{share_id}/accept", response_model=AcceptSharedPromptResponse)
def accept_shared_prompt_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)

    detected_type = detect_share_type_from_id(db, share_id)
    if not detected_type:
        raise HTTPException(status_code=404, detail="Shared prompt not found")

    if detected_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Use the clone endpoint for clone shares")

    shared_prompt = get_shared_prompt_by_share_id(db, share_id, detected_type)
    if not shared_prompt:
        raise HTTPException(status_code=404, detail="Shared prompt not found")

    if shared_prompt.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot subscribe to your own prompt")

    subscribe_to_shared_prompt(db, user.id, shared_prompt.id, detected_type)

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROMPT_SUBSCRIBED",
        details={
            "share_id": share_id,
            "prompt_id": shared_prompt.id,
            "owner_id": shared_prompt.user_id,
            "share_type": detected_type.value,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )

    message = "Prompt added to your library"
    if detected_type == ShareType.COLLABORATE:
        message = "Prompt added to your library (you can edit)"
    elif detected_type == ShareType.LIVE:
        message = "Prompt added to your library (live sync enabled)"

    return AcceptSharedPromptResponse(
        prompt_id=shared_prompt.id,
        share_type=detected_type.value,
        message=message,
    )


@prompts_router.post("/clone/{share_id}", response_model=ClonePromptResponse)
def clone_shared_prompt_route(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)

    cloned_prompt = clone_shared_prompt(db, user.id, share_id)

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROMPT_CLONED",
        details={"share_id": share_id, "cloned_prompt_id": cloned_prompt.id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )

    return ClonePromptResponse(
        prompt_id=cloned_prompt.id,
        message="Prompt cloned successfully! It's now your own prompt.",
    )


@prompts_router.post("/shared/{prompt_id}/unsubscribe")
def unsubscribe_prompt_route(
    prompt_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    ensure_prompts_enabled(user, db)
    result = unsubscribe_from_shared_prompt(db, user.id, prompt_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="PROMPT_UNSUBSCRIBED",
        details={"prompt_id": prompt_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )
    return result


@prompts_router.post("/invite", response_model=InviteUsersResponse)
def invite_users_to_prompt(
    payload: InviteUsersRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Invite users to a shared prompt by creating notifications."""
    ensure_prompts_enabled(user, db)

    prompt = db.query(Prompts).filter(
        Prompts.id == payload.item_id,
        Prompts.user_id == user.id,
    ).first()

    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    share_type_map = {
        ShareTypeEnum.CLONE: ShareType.CLONE,
        ShareTypeEnum.LIVE: ShareType.LIVE,
        ShareTypeEnum.COLLABORATE: ShareType.COLLABORATE,
    }
    model_share_type = share_type_map.get(payload.share_type, ShareType.LIVE)
    ensure_prompt_sharing_allowed_or_existing(user, db, payload.item_id, model_share_type)
    share_result = create_prompt_share(db, user.id, payload.item_id, model_share_type)

    inviter = get_user(db, user.id)
    inviter_name = ""
    if inviter.first_name and inviter.last_name:
        inviter_name = f"{inviter.first_name} {inviter.last_name}"
    elif inviter.first_name:
        inviter_name = inviter.first_name
    else:
        inviter_name = inviter.email.split('@')[0] if inviter.email else "Someone"

    invited_users = resolve_invitable_users_for_sharing(db, user, payload.user_ids)
    invited_count = 0
    for invited_user in invited_users:
        try:
            create_user_notification(
                db,
                message=f"{inviter_name} invited you to a prompt: {prompt.title}",
                category="share_invitation",
                notification_type="info",
                user_ids=[invited_user.id],
                details={
                    "type": "share_invitation",
                    "item_type": "prompt",
                    "item_id": payload.item_id,
                    "item_title": prompt.title,
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
        action="PROMPT_USERS_INVITED",
        details={
            "prompt_id": payload.item_id,
            "invited_user_ids": [invited_user.id for invited_user in invited_users],
            "share_type": payload.share_type.value,
            "invited_count": invited_count,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="prompts",
    )

    return InviteUsersResponse(
        invited_count=invited_count,
        message=f"Successfully invited {invited_count} user(s) to the prompt.",
    )
