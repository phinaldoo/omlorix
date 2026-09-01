from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
import uuid

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.agents.models import (
    ShareType,
    SharedUserAgentSubscription,
    UserAgent,
    UserAgentAsset,
    detect_agent_share_type_from_id,
    get_owned_user_agent,
    get_user_agent_asset,
    get_user_agent_by_id,
    get_user_agent_by_share_id,
    get_user_agent_subscription,
    list_owned_user_agents,
    list_shared_user_agent_subscriptions,
    list_user_agent_assets,
)
from app.files.schemas import (
    allowed_audio_types,
    allowed_document_types,
    allowed_image_types,
    allowed_video_types,
)
from app.files.storage import upload_file_to_storage
from app.files.utils import (
    delete_storage_reference,
    guess_file_mime_from_name,
    materialize_file_record,
    validate_upload_file,
)
from app.utils.icon_security import require_safe_icon_input
from app.files.models import get_file
from app.llm.models import Models, get_model
from app.settings.utils import get_public_url
from app.skills.models import get_skill_content_for_user
from app.users.models import get_user
from app.users.roles import is_admin_role


AGENT_ASSET_DESCRIPTOR_PREFIX = "agent_asset:"
MAX_AGENT_ASSET_COPY_BYTES = 10 * 1024 * 1024


@dataclass
class ResolvedSelectedModel:
    selected_model_id: str
    base_model: Models
    model_kind: str = "base"
    agent: UserAgent | None = None
    agent_owner_name: str | None = None
    agent_share_type: str | None = None
    agent_instruction: str | None = None
    agent_skill_ids: list[str] | None = None
    asset_descriptors_by_category: dict[str, list[str]] | None = None


def _utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    """Convert datetime to ISO format string."""
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.isoformat()


def _normalize_string(value: str | None, *, field_name: str, max_length: int | None = None, allow_empty: bool = False) -> str:
    """Normalize and validate string input."""
    normalized = str(value or "").strip()
    if not normalized and not allow_empty:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} is required")
    if max_length is not None and len(normalized) > max_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be {max_length} characters or fewer",
        )
    return normalized


def _normalize_optional_string(value: str | None, *, max_length: int | None = None) -> str | None:
    """Normalize optional string input."""
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if max_length is not None and len(normalized) > max_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Value must be {max_length} characters or fewer",
        )
    return normalized


def _guess_content_type(filename: str) -> str:
    """Guess content type from filename."""
    guessed = guess_file_mime_from_name(filename)
    return str(guessed or "application/octet-stream").lower()


def _content_type_for_existing_file_record(file_record: Any) -> str:
    """Return the trusted stored MIME type for an existing file record."""
    normalized_type = str(getattr(file_record, "file_type", "") or "").strip().lower()
    if normalized_type:
        return normalized_type
    return _guess_content_type(getattr(file_record, "file_name", "") or "")


def _normalize_file_size(value: Any) -> int:
    """Normalize file size to non-negative integer."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _file_category_for_mime(file_type: str) -> str:
    """Determine file category from MIME type."""
    if file_type in allowed_image_types:
        return "image"
    if file_type in allowed_audio_types:
        return "audio"
    if file_type in allowed_video_types:
        return "video"
    if file_type in allowed_document_types:
        return "document"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Only supported image, audio, video, or document files may be attached to an agent",
    )


def _owner_display_name(db: Session, user_id: str) -> str:
    """Get display name for user."""
    owner = get_user(db, user_id)
    if not owner:
        return "Unknown"
    if owner.first_name and owner.last_name:
        return f"{owner.first_name} {owner.last_name}"
    if owner.first_name:
        return owner.first_name
    return "User"


def _user_can_access_base_model(db: Session, user_id: str, model: Models | None) -> bool:
    """Check if user can access base model."""
    if model is None or not bool(getattr(model, "is_active", True)):
        return False
    user = get_user(db, user_id)
    if not user:
        return False
    if is_admin_role(getattr(user, "role", None)):
        return True
    access = model.access if isinstance(model.access, dict) else {}
    if bool(access.get("everyone")):
        return True
    users = access.get("users") if isinstance(access.get("users"), list) else []
    groups = access.get("groups") if isinstance(access.get("groups"), list) else []
    return user.id in users or (getattr(user, "group_id", None) in groups)


def _get_accessible_base_model(
    db: Session,
    user_id: str,
    base_model_id: str,
    accessible_base_models: dict[str, Models] | None = None,
) -> Models:
    """Get accessible base model for user."""
    normalized = _normalize_string(base_model_id, field_name="base_model_id")
    model = accessible_base_models.get(normalized) if isinstance(accessible_base_models, dict) else None
    if model is None:
        model = get_model(db, normalized)
        if not _user_can_access_base_model(db, user_id, model):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to the selected base model")
    return model


def _validate_skill_access(db: Session, user_id: str, skill_id: str | None) -> str | None:
    """Validate user has access to skill."""
    normalized_skill_id = _normalize_optional_string(skill_id)
    if not normalized_skill_id:
        return None
    if not get_skill_content_for_user(db, user_id, normalized_skill_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return normalized_skill_id


def _ensure_owner_can_access_base_model(db: Session, owner_user_id: str, base_model_id: str) -> None:
    """Ensure the agent owner can access the selected base model."""
    try:
        _get_accessible_base_model(db, owner_user_id, base_model_id)
    except HTTPException as exc:
        if exc.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agent owner does not have access to the selected base model",
            ) from exc
        raise


def _ensure_owner_can_access_skill(db: Session, owner_user_id: str, skill_id: str | None) -> str | None:
    """Ensure the agent owner can access the selected skill."""
    try:
        return _validate_skill_access(db, owner_user_id, skill_id)
    except HTTPException as exc:
        if exc.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agent owner does not have access to the selected skill",
            ) from exc
        raise


def _build_agent_description(agent: UserAgent, base_model: Models) -> str:
    """Build a public description without exposing the agent instruction.

    Agent instructions are private execution configuration, not display copy.
    Keeping the description independent also prevents future list serializers
    from accidentally publishing an instruction prefix.
    """
    return str(
        getattr(base_model, "name", None)
        or getattr(base_model, "model_name", None)
        or ""
    ).strip()[:100]


def _serialize_asset(asset: UserAgentAsset) -> dict[str, Any]:
    """Serialize agent asset to dictionary."""
    meta = asset.meta if isinstance(asset.meta, dict) else {}
    original_filename = Path(
        str(meta.get("original_filename") or asset.file_name or asset.id)
    ).name
    return {
        "id": asset.id,
        "agent_id": asset.agent_id,
        # ``asset.file_name`` is the opaque storage name. Never use it as the
        # user-facing filename when the original name is available.
        "file_name": original_filename,
        "original_filename": original_filename,
        "file_type": asset.file_type,
        "file_category": asset.file_category,
        "file_size": _normalize_file_size(asset.file_size),
        "created_at": _iso(asset.created_at),
    }


def _share_ids_for_response(agent: UserAgent, *, is_owner: bool) -> dict[str, str | None]:
    """Return share IDs only when the requesting user owns the agent.

    Share IDs are bearer capabilities: presenting a collaborate share ID grants
    edit access when accepting the share. Subscribers must not receive the
    owner's share IDs, even when they can view or edit the shared agent.
    """
    if not is_owner:
        return {
            "clone_share_id": None,
            "live_share_id": None,
            "collaborate_share_id": None,
        }

    return {
        "clone_share_id": agent.clone_share_id,
        "live_share_id": agent.live_share_id,
        "collaborate_share_id": agent.collaborate_share_id,
    }


def _serialize_agent(
    db: Session,
    *,
    user_id: str,
    agent: UserAgent,
    base_model: Models,
    subscription: SharedUserAgentSubscription | None = None,
    include_assets: bool = True,
) -> dict[str, Any]:
    """Serialize agent to dictionary."""
    settings = base_model.settings if isinstance(base_model.settings, dict) else {}
    meta = base_model.meta if isinstance(base_model.meta, dict) else {}
    from app.llmstats.models import (
        get_model_cached_tokens_per_second,
        get_model_cached_tokens_per_second_sample_count,
        get_model_performance_meta,
    )

    performance_meta = get_model_performance_meta(meta)
    owner_name = _owner_display_name(db, agent.user_id)
    is_owner = agent.user_id == user_id
    return {
        "id": agent.id,
        "model_id": agent.id,
        "agent_id": agent.id,
        "user_id": agent.user_id,
        "model_kind": "agent",
        "is_custom_agent": True,
        "base_model_id": agent.base_model_id,
        "name": agent.name,
        "icon": agent.icon,
        "description": _build_agent_description(agent, base_model),
        "model_icon": agent.icon or base_model.model_icon,
        "provider": base_model.provider,
        "provider_type": base_model.provider,
        "provider_id": base_model.provider_id,
        "model_name": base_model.model_name,
        "capabilities": base_model.capabilities,
        "status": base_model.status,
        "is_active": True,
        "training_data": settings.get("training_data"),
        "settings": deepcopy(settings),
        "tools": deepcopy(base_model.tools or []),
        "access": {"agent_owner_id": agent.user_id},
        "tokens_per_second": get_model_cached_tokens_per_second(meta),
        "tokens_per_second_sample_count": get_model_cached_tokens_per_second_sample_count(meta),
        "tokens_per_second_sample_limit": performance_meta.get("sample_limit"),
        "tokens_per_second_max_age_days": performance_meta.get("max_age_days"),
        "increased_errors": bool(meta.get("increased_errors", False)),
        "skill_id": agent.skill_id,
        "instruction": agent.instruction or "",
        "created_at": _iso(agent.created_at),
        "updated_at": _iso(agent.updated_at),
        **_share_ids_for_response(agent, is_owner=is_owner),
        "owner_name": owner_name,
        "is_subscribed": bool(subscription and not is_owner),
        "share_type": subscription.share_type if subscription and not is_owner else None,
        "is_shared": not is_owner,
        "assets": [_serialize_asset(asset) for asset in list_user_agent_assets(db, agent.id)] if include_assets else [],
    }


def list_accessible_agents(
    db: Session,
    user_id: str,
    *,
    accessible_base_models: dict[str, Models] | None = None,
) -> list[dict[str, Any]]:
    """List all agents accessible to user."""
    owned_agents = list_owned_user_agents(db, user_id)
    subscriptions = list_shared_user_agent_subscriptions(db, user_id)
    subscriptions_by_agent_id = {subscription.agent_id: subscription for subscription in subscriptions}

    shared_agent_ids = [subscription.agent_id for subscription in subscriptions]
    shared_agents = (
        db.query(UserAgent)
        .filter(UserAgent.id.in_(shared_agent_ids))
        .all()
        if shared_agent_ids
        else []
    )

    ordered_agents: list[tuple[UserAgent, SharedUserAgentSubscription | None]] = []
    seen: set[str] = set()
    for agent in owned_agents:
        if agent.id in seen:
            continue
        seen.add(agent.id)
        ordered_agents.append((agent, None))
    for agent in shared_agents:
        if agent.id in seen:
            continue
        seen.add(agent.id)
        ordered_agents.append((agent, subscriptions_by_agent_id.get(agent.id)))

    payloads: list[dict[str, Any]] = []
    for agent, subscription in ordered_agents:
        try:
            base_model = _get_accessible_base_model(
                db,
                user_id,
                agent.base_model_id,
                accessible_base_models=accessible_base_models,
            )
        except HTTPException:
            continue
        payloads.append(
            _serialize_agent(
                db,
                user_id=user_id,
                agent=agent,
                base_model=base_model,
                subscription=subscription,
                include_assets=False,
            )
        )
    return payloads


def get_user_agent_with_access(
    db: Session,
    user_id: str,
    agent_id: str,
    *,
    accessible_base_models: dict[str, Models] | None = None,
) -> tuple[UserAgent, Models, SharedUserAgentSubscription | None]:
    """Get user agent with access validation."""
    agent = get_user_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    subscription = None
    if agent.user_id != user_id:
        subscription = get_user_agent_subscription(db, user_id, agent.id)
        if not subscription:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    base_model = _get_accessible_base_model(
        db,
        user_id,
        agent.base_model_id,
        accessible_base_models=accessible_base_models,
    )
    return agent, base_model, subscription


def can_user_access_agent(db: Session, user_id: str, agent_id: str) -> bool:
    """Check if user can access agent."""
    try:
        get_user_agent_with_access(db, user_id, agent_id)
        return True
    except HTTPException:
        return False


def can_user_edit_agent(db: Session, user_id: str, agent_id: str) -> bool:
    """Check if user can edit agent."""
    try:
        agent, _base_model, subscription = get_user_agent_with_access(db, user_id, agent_id)
        return agent.user_id == user_id or bool(
            subscription and subscription.share_type == ShareType.COLLABORATE.value
        )
    except HTTPException:
        return False


def create_user_agent(
    db: Session,
    *,
    user_id: str,
    name: str,
    icon: str,
    base_model_id: str,
    instruction: str = "",
    skill_id: str | None = None,
) -> dict[str, Any]:
    """Create user agent."""
    base_model = _get_accessible_base_model(db, user_id, base_model_id)
    normalized_name = _normalize_string(name, field_name="name", max_length=100)
    normalized_icon = require_safe_icon_input(
        _normalize_string(icon, field_name="icon", max_length=20000),
        fallback="omlorix",
    )
    normalized_instruction = _normalize_string(instruction, field_name="instruction", max_length=50000, allow_empty=True)
    normalized_skill_id = _validate_skill_access(db, user_id, skill_id)

    agent = UserAgent(
        user_id=user_id,
        name=normalized_name,
        icon=normalized_icon,
        base_model_id=base_model.id,
        instruction=normalized_instruction,
        skill_id=normalized_skill_id,
        created_at=_utc_now(),
        updated_at=_utc_now(),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return _serialize_agent(db, user_id=user_id, agent=agent, base_model=base_model)


def update_user_agent(
    db: Session,
    *,
    user_id: str,
    agent_id: str,
    name: str | None = None,
    icon: str | None = None,
    base_model_id: str | None = None,
    instruction: str | None = None,
    skill_id: str | None = None,
    skill_id_provided: bool = False,
) -> dict[str, Any]:
    """Update user agent."""
    agent, _base_model, subscription = get_user_agent_with_access(db, user_id, agent_id)
    is_owner = agent.user_id == user_id
    if not is_owner and not bool(
        subscription and subscription.share_type == ShareType.COLLABORATE.value
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to edit this agent")

    if name is not None:
        agent.name = _normalize_string(name, field_name="name", max_length=100)
    if icon is not None:
        agent.icon = require_safe_icon_input(
            _normalize_string(icon, field_name="icon", max_length=20000),
            fallback="omlorix",
        )
    if base_model_id is not None:
        resolved_base_model = _get_accessible_base_model(db, user_id, base_model_id)
        if not is_owner:
            _ensure_owner_can_access_base_model(db, agent.user_id, resolved_base_model.id)
        agent.base_model_id = resolved_base_model.id
    if instruction is not None:
        agent.instruction = _normalize_string(instruction, field_name="instruction", max_length=50000, allow_empty=True)
    if skill_id_provided:
        normalized_skill_id = _validate_skill_access(db, user_id, skill_id)
        if not is_owner:
            normalized_skill_id = _ensure_owner_can_access_skill(db, agent.user_id, normalized_skill_id)
        agent.skill_id = normalized_skill_id
    agent.updated_at = _utc_now()
    db.commit()
    db.refresh(agent)

    resolved_base_model = _get_accessible_base_model(db, user_id, agent.base_model_id)
    return _serialize_agent(
        db,
        user_id=user_id,
        agent=agent,
        base_model=resolved_base_model,
        subscription=subscription,
    )


def delete_user_agent(db: Session, *, user_id: str, agent_id: str) -> dict[str, Any]:
    """Delete user agent."""
    agent = get_owned_user_agent(db, user_id, agent_id)
    assets = list_user_agent_assets(db, agent.id)
    for asset in assets:
        delete_storage_reference(
            storage_provider=asset.storage_provider,
            storage_key=asset.storage_key,
            user_id=asset.owner_user_id,
            file_name=asset.file_name,
        )
        db.delete(asset)

    db.query(SharedUserAgentSubscription).filter(
        SharedUserAgentSubscription.agent_id == agent.id
    ).delete(synchronize_session=False)
    db.delete(agent)
    db.commit()
    return {"deleted": True, "agent_id": agent_id}


def get_agent_detail(db: Session, *, user_id: str, agent_id: str) -> dict[str, Any]:
    """Get agent detail."""
    agent, base_model, subscription = get_user_agent_with_access(db, user_id, agent_id)
    return _serialize_agent(
        db,
        user_id=user_id,
        agent=agent,
        base_model=base_model,
        subscription=subscription,
    )


def _share_field_for_type(share_type: ShareType) -> str:
    """Get share field name for share type."""
    return {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }[share_type]


def _share_prefix_for_type(share_type: ShareType) -> str:
    """Get share URL prefix for share type."""
    return {
        ShareType.CLONE: "/agents/clone",
        ShareType.LIVE: "/agents/live",
        ShareType.COLLABORATE: "/agents/collaborate",
    }[share_type]


def create_agent_share(db: Session, *, user_id: str, agent_id: str, share_type: ShareType) -> dict[str, Any]:
    """Create agent share."""
    agent = get_owned_user_agent(db, user_id, agent_id)
    field_name = _share_field_for_type(share_type)
    share_id = getattr(agent, field_name)
    if not share_id:
        share_id = str(uuid.uuid4())
        setattr(agent, field_name, share_id)
        agent.updated_at = _utc_now()
        db.commit()
        db.refresh(agent)
    base_url = get_public_url(db)
    return {
        "share_id": share_id,
        "share_type": share_type.value,
        "share_url": f"{base_url}{_share_prefix_for_type(share_type)}/{share_id}",
    }


def get_agent_share_status(db: Session, *, user_id: str, agent_id: str) -> dict[str, Any]:
    """Get agent share status."""
    agent = get_owned_user_agent(db, user_id, agent_id)
    return {
        "clone_share_id": agent.clone_share_id,
        "live_share_id": agent.live_share_id,
        "collaborate_share_id": agent.collaborate_share_id,
        "live_subscriber_count": db.query(SharedUserAgentSubscription).filter(
            SharedUserAgentSubscription.agent_id == agent.id,
            SharedUserAgentSubscription.share_type == ShareType.LIVE.value,
        ).count(),
        "collaborate_subscriber_count": db.query(SharedUserAgentSubscription).filter(
            SharedUserAgentSubscription.agent_id == agent.id,
            SharedUserAgentSubscription.share_type == ShareType.COLLABORATE.value,
        ).count(),
    }


def delete_agent_share(
    db: Session,
    *,
    user_id: str,
    agent_id: str,
    share_type: ShareType | None = None,
) -> dict[str, Any]:
    """Delete agent share."""
    agent = get_owned_user_agent(db, user_id, agent_id)
    if share_type is None:
        agent.clone_share_id = None
        agent.live_share_id = None
        agent.collaborate_share_id = None
        db.query(SharedUserAgentSubscription).filter(
            SharedUserAgentSubscription.agent_id == agent.id
        ).delete(synchronize_session=False)
    else:
        setattr(agent, _share_field_for_type(share_type), None)
        if share_type in {ShareType.LIVE, ShareType.COLLABORATE}:
            db.query(SharedUserAgentSubscription).filter(
                SharedUserAgentSubscription.agent_id == agent.id,
                SharedUserAgentSubscription.share_type == share_type.value,
            ).delete(synchronize_session=False)
    agent.updated_at = _utc_now()
    db.commit()
    return {"ok": True}


def get_shared_agent_preview(
    db: Session,
    *,
    share_id: str,
    share_type: ShareType | None = None,
    requesting_user_id: str | None = None,
) -> dict[str, Any]:
    """Get shared agent preview."""
    resolved_share_type = share_type or detect_agent_share_type_from_id(db, share_id)
    if resolved_share_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared agent not found")
    agent = get_user_agent_by_share_id(db, share_id, resolved_share_type)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared agent not found")
    if requesting_user_id and requesting_user_id == agent.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot open your own shared agent")

    # The preview is a preflight for the exact Accept/Clone action. Keep this
    # check aligned with those mutation paths so the UI can explain a blocked
    # action before the user confirms it. The mutation still rechecks access to
    # protect against model permissions changing after the preview was loaded.
    base_model_accessible = False
    if requesting_user_id:
        try:
            _get_accessible_base_model(db, requesting_user_id, agent.base_model_id)
            base_model_accessible = True
        except HTTPException:
            base_model_accessible = False

    # Clone shares copy only skills the recipient can already access. Expose a
    # boolean instead of the private skill ID or content so the frontend can
    # give an accurate, translated warning without leaking skill metadata.
    clone_skill_will_be_omitted = False
    if requesting_user_id and resolved_share_type == ShareType.CLONE and agent.skill_id:
        try:
            _validate_skill_access(db, requesting_user_id, agent.skill_id)
        except HTTPException:
            clone_skill_will_be_omitted = True

    instruction = str(agent.instruction or "").strip()
    preview = instruction[:400] if instruction else None
    return {
        "share_id": share_id,
        "share_type": resolved_share_type.value,
        "name": agent.name,
        "icon": agent.icon,
        "base_model_id": agent.base_model_id,
        "base_model_accessible": base_model_accessible,
        "can_complete_share_action": base_model_accessible,
        "clone_skill_will_be_omitted": clone_skill_will_be_omitted,
        "instruction_preview": preview,
        "owner_name": _owner_display_name(db, agent.user_id),
        "created_at": _iso(agent.created_at),
    }


def accept_shared_agent(
    db: Session,
    *,
    user_id: str,
    share_id: str,
) -> dict[str, Any]:
    """Accept shared agent."""
    share_type = detect_agent_share_type_from_id(db, share_id)
    if share_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared agent not found")
    if share_type == ShareType.CLONE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Clone shares must be cloned")
    agent = get_user_agent_by_share_id(db, share_id, share_type)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared agent not found")
    if user_id == agent.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot accept your own shared agent")

    _get_accessible_base_model(db, user_id, agent.base_model_id)

    subscription = get_user_agent_subscription(db, user_id, agent.id)
    if not subscription:
        subscription = SharedUserAgentSubscription(
            agent_id=agent.id,
            subscriber_id=user_id,
            share_type=share_type.value,
            subscribed_at=_utc_now(),
        )
        db.add(subscription)
    else:
        subscription.share_type = share_type.value
    db.commit()
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "share_type": share_type.value,
        "message": "Agent added to your workspace!",
    }


def _copy_agent_asset_record(db: Session, *, source_asset: UserAgentAsset, target_agent_id: str, target_user_id: str) -> UserAgentAsset:
    """Copy agent asset record."""
    temp_path: Path | None = None
    temp_handle = NamedTemporaryFile(delete=False)
    temp_handle.close()
    temp_path = Path(temp_handle.name)
    try:
        source_stub = type(
            "AgentAssetFileStub",
            (),
            {
                "id": source_asset.id,
                "file_name": source_asset.file_name,
                "storage_provider": source_asset.storage_provider,
                "storage_key": source_asset.storage_key,
            },
        )()
        source_path = materialize_file_record(source_stub, source_asset.owner_user_id)
        temp_path.write_bytes(source_path.read_bytes())
        file_type = validate_upload_file(temp_path, fallback_mime=source_asset.file_type)
        file_category = _file_category_for_mime(file_type)
        stored_name = f"agent-{target_agent_id}-{uuid.uuid4().hex}{Path(source_asset.file_name).suffix}"
        provider, storage_key, storage_meta = upload_file_to_storage(temp_path, target_user_id, stored_name)
        asset = UserAgentAsset(
            agent_id=target_agent_id,
            owner_user_id=target_user_id,
            file_name=stored_name,
            storage_provider=provider,
            storage_key=storage_key,
            storage_meta=storage_meta,
            file_category=file_category,
            file_type=file_type,
            file_size=temp_path.stat().st_size,
            meta=deepcopy(source_asset.meta or {}),
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        db.add(asset)
        db.flush()
        return asset
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


def clone_shared_agent(db: Session, *, user_id: str, share_id: str) -> dict[str, Any]:
    """Clone shared agent."""
    share_type = detect_agent_share_type_from_id(db, share_id)
    if share_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared agent not found")
    agent = get_user_agent_by_share_id(db, share_id, share_type)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared agent not found")
    if agent.user_id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot clone your own shared agent")

    _get_accessible_base_model(db, user_id, agent.base_model_id)
    normalized_name = agent.name.strip()
    clone_name_suffix = " Copy"
    max_clone_name_length = 100
    max_base_name_length = max_clone_name_length - len(clone_name_suffix)
    truncated_base_name = normalized_name[:max_base_name_length]

    cloned_skill_id: str | None = None
    if agent.skill_id:
        try:
            cloned_skill_id = _validate_skill_access(db, user_id, agent.skill_id)
        except HTTPException:
            cloned_skill_id = None

    clone = UserAgent(
        user_id=user_id,
        name=f"{truncated_base_name}{clone_name_suffix}",
        icon=agent.icon,
        base_model_id=agent.base_model_id,
        instruction=agent.instruction or "",
        skill_id=cloned_skill_id,
        created_at=_utc_now(),
        updated_at=_utc_now(),
    )
    db.add(clone)
    db.flush()

    for asset in list_user_agent_assets(db, agent.id):
        _copy_agent_asset_record(db, source_asset=asset, target_agent_id=clone.id, target_user_id=user_id)

    db.commit()
    db.refresh(clone)
    return {
        "agent_id": clone.id,
        "name": clone.name,
        "message": "Agent cloned to your workspace!",
    }


def unsubscribe_from_shared_agent(db: Session, *, user_id: str, agent_id: str) -> dict[str, Any]:
    """Unsubscribe from shared agent."""
    subscription = get_user_agent_subscription(db, user_id, agent_id)
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    db.delete(subscription)
    db.commit()
    return {"ok": True}


def create_agent_invites(
    db: Session,
    *,
    user_id: str,
    agent_id: str,
    share_type: ShareType,
) -> tuple[UserAgent, dict[str, Any]]:
    """Create agent invites."""
    agent = get_owned_user_agent(db, user_id, agent_id)
    share = create_agent_share(db, user_id=user_id, agent_id=agent_id, share_type=share_type)
    return agent, share


def create_user_agent_asset(
    db: Session,
    *,
    user_id: str,
    agent_id: str,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    """Create user agent asset."""
    if not can_user_edit_agent(db, user_id, agent_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to edit this agent")
    safe_name = Path(_normalize_string(filename, field_name="filename", max_length=255)).name
    if not safe_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="filename is required")
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File content is required")

    temp_file = NamedTemporaryFile(delete=False)
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        temp_path.write_bytes(content)
        file_type = validate_upload_file(
            temp_path,
            fallback_mime=guess_file_mime_from_name(safe_name),
        )
        file_category = _file_category_for_mime(file_type)
        asset_id = str(uuid.uuid4())
        stored_name = f"agent-{agent_id}-{asset_id}{Path(safe_name).suffix}"
        provider, storage_key, storage_meta = upload_file_to_storage(temp_path, user_id, stored_name)
        asset = UserAgentAsset(
            id=asset_id,
            agent_id=agent_id,
            owner_user_id=user_id,
            file_name=stored_name,
            storage_provider=provider,
            storage_key=storage_key,
            storage_meta=storage_meta,
            file_category=file_category,
            file_type=file_type,
            file_size=len(content),
            meta={"original_filename": safe_name},
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return _serialize_asset(asset)
    finally:
        temp_path.unlink(missing_ok=True)


def create_user_agent_asset_from_file(
    db: Session,
    *,
    user_id: str,
    agent_id: str,
    file_id: str,
) -> dict[str, Any]:
    """Copy an existing user file into an agent asset."""
    if not can_user_edit_agent(db, user_id, agent_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to edit this agent")

    normalized_file_id = _normalize_string(file_id, field_name="file_id")
    file_record = get_file(db, normalized_file_id, user_id)
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if _normalize_file_size(file_record.file_size) > MAX_AGENT_ASSET_COPY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds the 10 MB agent asset limit")

    file_type = _content_type_for_existing_file_record(file_record)
    _file_category_for_mime(file_type)

    source_path = materialize_file_record(file_record, user_id)
    content = source_path.read_bytes()
    if len(content) > MAX_AGENT_ASSET_COPY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds the 10 MB agent asset limit")
    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    original_name = meta.get("original_filename") or Path(file_record.file_name or "").name or "asset"
    return create_user_agent_asset(
        db,
        user_id=user_id,
        agent_id=agent_id,
        filename=str(original_name),
        content=content,
    )


def delete_user_agent_asset(db: Session, *, user_id: str, agent_id: str, asset_id: str) -> dict[str, Any]:
    """Delete user agent asset."""
    if not can_user_edit_agent(db, user_id, agent_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to edit this agent")
    asset = get_user_agent_asset(db, asset_id)
    if not asset or asset.agent_id != str(agent_id).strip():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    delete_storage_reference(
        storage_provider=asset.storage_provider,
        storage_key=asset.storage_key,
        user_id=asset.owner_user_id,
        file_name=asset.file_name,
    )
    db.delete(asset)
    db.commit()
    return {"deleted": True, "asset_id": asset_id}


def list_agent_assets_for_user(db: Session, *, user_id: str, agent_id: str) -> list[dict[str, Any]]:
    """List agent assets for user."""
    _agent, _base_model, _subscription = get_user_agent_with_access(db, user_id, agent_id)
    return [_serialize_asset(asset) for asset in list_user_agent_assets(db, agent_id)]


def parse_agent_asset_descriptor(value: str | None) -> str | None:
    """Parse agent asset descriptor."""
    normalized = str(value or "").strip()
    if not normalized.startswith(AGENT_ASSET_DESCRIPTOR_PREFIX):
        return None
    asset_id = normalized[len(AGENT_ASSET_DESCRIPTOR_PREFIX):].strip()
    return asset_id or None


def build_agent_asset_descriptor(asset_id: str) -> str:
    """Build agent asset descriptor."""
    normalized = _normalize_string(asset_id, field_name="asset_id")
    return f"{AGENT_ASSET_DESCRIPTOR_PREFIX}{normalized}"


def get_agent_asset_info_for_user(db: Session, *, user_id: str, descriptor_or_asset_id: str) -> dict[str, Any] | None:
    """Get agent asset info for user."""
    asset_id = parse_agent_asset_descriptor(descriptor_or_asset_id) or str(descriptor_or_asset_id or "").strip()
    if not asset_id:
        return None
    asset = get_user_agent_asset(db, asset_id)
    if not asset:
        return None
    if not can_user_access_agent(db, user_id, asset.agent_id):
        return None
    source_stub = type(
        "AgentAssetFileStub",
        (),
        {
            "id": asset.id,
            "file_name": asset.file_name,
            "storage_provider": asset.storage_provider,
            "storage_key": asset.storage_key,
        },
    )()
    file_path = materialize_file_record(source_stub, asset.owner_user_id)
    meta = asset.meta if isinstance(asset.meta, dict) else {}
    return {
        "path": str(file_path),
        "file_name": asset.file_name,
        "storage_provider": asset.storage_provider,
        "storage_key": asset.storage_key,
        "file_type": asset.file_type,
        "file_category": asset.file_category,
        "file_size": _normalize_file_size(asset.file_size),
        "meta": meta,
    }


def resolve_selected_model_for_user(
    db: Session,
    *,
    user_id: str,
    model_id: str,
    accessible_base_models: dict[str, Models] | None = None,
) -> ResolvedSelectedModel:
    """Resolve selected model for user."""
    normalized_model_id = _normalize_string(model_id, field_name="model_id")
    if isinstance(accessible_base_models, dict) and normalized_model_id in accessible_base_models:
        return ResolvedSelectedModel(
            selected_model_id=normalized_model_id,
            base_model=accessible_base_models[normalized_model_id],
        )

    try:
        base_model = get_model(db, normalized_model_id)
        if _user_can_access_base_model(db, user_id, base_model):
            return ResolvedSelectedModel(selected_model_id=normalized_model_id, base_model=base_model)
    except HTTPException:
        pass

    agent, base_model, subscription = get_user_agent_with_access(
        db,
        user_id,
        normalized_model_id,
        accessible_base_models=accessible_base_models,
    )
    asset_descriptors_by_category: dict[str, list[str]] = {
        "image": [],
        "audio": [],
        "video": [],
        "document": [],
    }
    for asset in list_user_agent_assets(db, agent.id):
        category = str(asset.file_category or "").strip().lower()
        if category not in asset_descriptors_by_category:
            continue
        asset_descriptors_by_category[category].append(build_agent_asset_descriptor(asset.id))

    return ResolvedSelectedModel(
        selected_model_id=normalized_model_id,
        base_model=base_model,
        model_kind="agent",
        agent=agent,
        agent_owner_name=_owner_display_name(db, agent.user_id),
        agent_share_type=subscription.share_type if subscription else None,
        agent_instruction=str(agent.instruction or "").strip() or None,
        agent_skill_ids=[agent.skill_id] if agent.skill_id else [],
        asset_descriptors_by_category=asset_descriptors_by_category,
    )
