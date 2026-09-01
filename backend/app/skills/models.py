from datetime import datetime, timezone
from enum import Enum
import mimetypes
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import List, Optional
from urllib.parse import quote, unquote

from fastapi import HTTPException, status
from sqlalchemy import Column, DateTime, String, func, or_
from sqlalchemy.orm import Session

from app.database import Base
from app.groups.init import get_user_group_setting_value
from app.paths import DATA_DIR
from app.settings.utils import get_public_url
from app.utils.icon_security import sanitize_icon_input


class ShareType(str, Enum):
    """Types of skill sharing."""
    CLONE = "clone"        # Recipient can clone the skill as their own
    LIVE = "live"          # Recipient can view with live updates (read-only)
    COLLABORATE = "collaborate"  # Recipient can view and edit (not delete)

SKILLS_ROOT = DATA_DIR / "skills"
SKILL_FILE_DESCRIPTOR_PREFIX = "skill_file:"
SKILL_FILE_FOLDERS = ("scripts", "references", "assets")


class Skills(Base):
    __tablename__ = "skills"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    icon = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    content = Column(String, nullable=False)
    clone_share_id = Column(String, nullable=True, index=True, unique=True)
    live_share_id = Column(String, nullable=True, index=True, unique=True)
    collaborate_share_id = Column(String, nullable=True, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
        nullable=False,
    )


class AdminSkills(Base):
    """Admin-managed skills that can be assigned to user groups."""
    __tablename__ = "admin_skills"

    id = Column(String, primary_key=True, index=True)
    icon = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    content = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
        nullable=False,
    )


class SharedSkillSubscription(Base):
    """Tracks which users have subscribed to (accepted) shared skills."""
    __tablename__ = "shared_skill_subscriptions"

    id = Column(String, primary_key=True, index=True)
    skill_id = Column(String, nullable=False, index=True)
    subscriber_id = Column(String, nullable=False, index=True)
    share_type = Column(String, nullable=False, default="live")  # 'live' or 'collaborate'
    subscribed_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)


def _get_skill(db: Session, user_id: str, skill_id: str) -> "Skills":
    skill = (
        db.query(Skills)
        .filter(Skills.id == skill_id.strip(), Skills.user_id == user_id.strip())
        .first()
    )
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return skill


def get_skill(db: Session, user_id: str, skill_id: str) -> "Skills":
    """Return a skill only when it belongs to the requested user."""
    return _get_skill(db, user_id, skill_id)


def _subscription_grants_view(
    skill: "Skills",
    subscription: Optional["SharedSkillSubscription"],
) -> bool:
    """Return whether an active subscription still grants access to a skill."""
    if not subscription:
        return False
    if subscription.share_type == ShareType.LIVE.value:
        return bool(skill.live_share_id)
    if subscription.share_type == ShareType.COLLABORATE.value:
        return bool(skill.collaborate_share_id)
    return False


def _subscription_grants_edit(
    skill: "Skills",
    subscription: Optional["SharedSkillSubscription"],
) -> bool:
    """Return whether a subscription grants collaborative source editing."""
    return bool(
        subscription
        and subscription.share_type == ShareType.COLLABORATE.value
        and skill.collaborate_share_id
    )


def get_skill_with_access(
    db: Session,
    user_id: str,
    skill_id: str,
    *,
    require_edit: bool = False,
) -> tuple["Skills", Optional["SharedSkillSubscription"]]:
    """Resolve an owned or actively subscribed skill and enforce edit access."""
    try:
        normalized_user_id = _safe_path_segment(user_id, "user_id")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    try:
        normalized_skill_id = _safe_path_segment(skill_id, "skill_id")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found") from exc
    skill = db.query(Skills).filter(Skills.id == normalized_skill_id).first()
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")

    if skill.user_id == normalized_user_id:
        return skill, None

    subscription = db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.skill_id == normalized_skill_id,
        SharedSkillSubscription.subscriber_id == normalized_user_id,
    ).first()
    if not _subscription_grants_view(skill, subscription):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if require_edit and not _subscription_grants_edit(skill, subscription):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to edit this skill",
        )
    return skill, subscription


def create_skill(
    db: Session,
    user_id: str,
    name: str,
    description: str,
    content: str | None,
    icon: str,
):
    """
    Create a skill for the given user and persist it.
    """
    skill = Skills(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=name,
        description=description,
        icon=sanitize_icon_input(icon, fallback="sparkles"),
        content=content,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


def _delete_skill_directory(user_id: str, skill_id: str):
    """Remove the on-disk skill directory if it exists."""
    try:
        skill_dir = _skill_directory(user_id, skill_id)
    except ValueError:
        return

    if skill_dir.exists():
        shutil.rmtree(skill_dir, ignore_errors=True)

    user_dir = skill_dir.parent
    if user_dir.exists():
        try:
            next(user_dir.iterdir())
        except StopIteration:
            user_dir.rmdir()


def _skill_directory(user_id: str, skill_id: str) -> Path:
    safe_user = _safe_path_segment(user_id, "user_id")
    safe_skill_id = _safe_path_segment(skill_id, "skill_id")
    return SKILLS_ROOT / safe_user / safe_skill_id


def _safe_path_segment(value: str, field_name: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    if any(sep in cleaned for sep in ("/", "\\")):
        raise ValueError(f"{field_name} cannot contain path separators")
    return cleaned


def _normalize_skill_relative_path(relative_path: str) -> str:
    candidate = str(relative_path or "").strip().replace("\\", "/")
    if not candidate:
        raise ValueError("relative_path cannot be empty")
    pure_path = PurePosixPath(candidate)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise ValueError("relative_path must stay inside the skill folder")
    return str(pure_path)


def _infer_skill_file_category(mime_type: str | None) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("audio/"):
        return "audio"
    if normalized.startswith("video/"):
        return "video"
    return "document"



def list_skills(db: Session, user_id: str):
    """
    Return all skills that belong to the provided user ordered by creation time.
    """
    return (
        db.query(Skills)
        .filter(Skills.user_id == user_id)
        .order_by(Skills.created_at.desc())
        .all()
    )


def delete_skill(db: Session, user_id: str, skill_id: str):
    """
    Delete a skill by id ensuring it belongs to the user.
    Also removes all subscriptions to this skill.
    """
    skill = _get_skill(db, user_id, skill_id)
    
    # Remove all subscriptions to this skill
    db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.skill_id == skill_id
    ).delete()
    
    db.delete(skill)
    db.commit()
    _delete_skill_directory(user_id, skill_id)
    return {"deleted": True, "skill_id": skill_id}


# ============================================================================
# Admin Skills CRUD Operations
# ============================================================================

ADMIN_SKILLS_USER_ID = "__admin__"


def create_admin_skill(
    db: Session,
    name: str,
    description: str,
    content: str | None,
    icon: str,
):
    """Create an admin skill."""
    skill = AdminSkills(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        icon=sanitize_icon_input(icon, fallback="sparkles"),
        content=content or "",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


def list_admin_skills(db: Session):
    """Return all admin skills ordered by creation time."""
    return (
        db.query(AdminSkills)
        .order_by(AdminSkills.created_at.desc(), AdminSkills.id.desc())
        .all()
    )


def paginate_admin_skills(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: str | None = None,
):
    """
    Return one bounded page of lightweight admin-skill list rows.

    The list view only needs a short content preview, so this query deliberately
    projects a substring instead of loading every skill's complete instructions.
    Full Markdown metadata and bundled file information are loaded separately
    when an administrator opens a specific skill for editing.
    """
    normalized_search = (search or "").strip()
    filters = []
    if normalized_search:
        # Treat SQL LIKE metacharacters as literal search text. Administrators
        # should get predictable substring matching for queries such as "100%".
        escaped_search = (
            normalized_search
            .replace("!", "!!")
            .replace("%", "!%")
            .replace("_", "!_")
        )
        search_pattern = f"%{escaped_search}%"
        filters.append(
            or_(
                AdminSkills.name.ilike(search_pattern, escape="!"),
                AdminSkills.description.ilike(search_pattern, escape="!"),
                AdminSkills.content.ilike(search_pattern, escape="!"),
            )
        )

    total_query = db.query(func.count(AdminSkills.id))
    if filters:
        total_query = total_query.filter(*filters)
    total = int(total_query.scalar() or 0)
    total_pages = (total + page_size - 1) // page_size

    # Clamp stale page requests after deletions so removing the last item from
    # the last page naturally returns the new final page in one request.
    resolved_page = min(page, total_pages) if total_pages else 1
    offset = (resolved_page - 1) * page_size

    page_query = db.query(
        AdminSkills.id.label("id"),
        AdminSkills.name.label("title"),
        AdminSkills.icon.label("icon"),
        func.substr(AdminSkills.content, 1, 500).label("content_preview"),
    )
    if filters:
        page_query = page_query.filter(*filters)
    items = (
        page_query
        .order_by(AdminSkills.created_at.desc(), AdminSkills.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return items, total, total_pages, resolved_page


def get_admin_skill(db: Session, skill_id: str) -> "AdminSkills":
    """Get a single admin skill by id."""
    skill = db.query(AdminSkills).filter(AdminSkills.id == skill_id.strip()).first()
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Managed skill not found")
    return skill


def update_admin_skill(
    db: Session,
    skill_id: str,
    title: Optional[str] = None,
    content: Optional[str] = None,
    icon: Optional[str] = None,
    description: Optional[str] = None,
):
    """Update an admin skill by id."""
    skill = get_admin_skill(db, skill_id)

    if title is not None:
        skill.name = title

    if content is not None:
        skill.content = content.strip() if isinstance(content, str) else ""

    if icon is not None:
        skill.icon = sanitize_icon_input(icon, fallback="sparkles")

    if description is not None:
        skill.description = description

    skill.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(skill)
    return skill


def delete_admin_skill(db: Session, skill_id: str):
    """Delete an admin skill by id."""
    from app.agents.models import remove_skill_from_user_agents
    from app.admin.groups.models import remove_admin_skill_from_groups
    from app.llm.models import remove_admin_skill_from_model_settings

    skill = get_admin_skill(db, skill_id)
    remove_admin_skill_from_groups(db, skill_id)
    remove_admin_skill_from_model_settings(db, skill_id)
    remove_skill_from_user_agents(db, skill_id)
    db.delete(skill)
    db.commit()
    _delete_skill_directory(ADMIN_SKILLS_USER_ID, skill_id)
    return {"deleted": True, "skill_id": skill_id}


def list_admin_skills_by_ids(db: Session, skill_ids: list[str]):
    """Return admin skills matching the given IDs."""
    if not skill_ids:
        return []
    return (
        db.query(AdminSkills)
        .filter(AdminSkills.id.in_(skill_ids))
        .order_by(AdminSkills.created_at.desc())
        .all()
    )


def update_skill(
    db: Session,
    user_id: str,
    skill_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    content: Optional[str] = None,
    icon: Optional[str] = None,
):
    """
    Update a skill by id for its owner or an active collaborator.
    Only provided fields will be updated.
    """
    skill, _subscription = get_skill_with_access(
        db,
        user_id,
        skill_id,
        require_edit=True,
    )

    if title is not None:
        skill.name = title

    if description is not None:
        skill.description = description

    if content is not None:
        skill.content = content.strip() if isinstance(content, str) else ""

    if icon is not None:
        skill.icon = sanitize_icon_input(icon, fallback="sparkles")

    skill.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(skill)
    return skill


# ============================================================================
# Skill Sharing Functions
# ============================================================================

def _get_owner_display_name(db: Session, user_id: str) -> str:
    """Get display name for a user."""
    from app.users.models import User
    owner = db.query(User).filter(User.id == user_id).first()
    if not owner:
        return "Unknown"
    if owner.first_name and owner.last_name:
        return f"{owner.first_name} {owner.last_name}"
    elif owner.first_name:
        return owner.first_name
    elif owner.email:
        return owner.email.split('@')[0]
    return "Unknown"


def _get_share_id_field(share_type: ShareType) -> str:
    """Get the column name for a share type."""
    return {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type, "live_share_id")


def _get_share_url_prefix(share_type: ShareType) -> str:
    """Get the URL prefix for a share type."""
    return {
        ShareType.CLONE: "/skills/clone",
        ShareType.LIVE: "/skills/live",
        ShareType.COLLABORATE: "/skills/collaborate",
    }.get(share_type, "/skills/live")


def create_skill_share(db: Session, user_id: str, skill_id: str, share_type: ShareType = ShareType.LIVE) -> dict:
    """Create or return existing share for a skill with specified type."""
    skill = _get_skill(db, user_id, skill_id)
    
    # Get the appropriate share_id field
    share_id_attr = _get_share_id_field(share_type)
    existing_share_id = getattr(skill, share_id_attr, None)
    url_prefix = _get_share_url_prefix(share_type)
    base_url = get_public_url(db)
    
    if existing_share_id:
        return {
            "share_id": existing_share_id,
            "share_type": share_type.value,
            "share_url": f"{base_url}{url_prefix}/{existing_share_id}",
        }
    
    new_share_id = str(uuid.uuid4())
    setattr(skill, share_id_attr, new_share_id)
    skill.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {
        "share_id": new_share_id,
        "share_type": share_type.value,
        "share_url": f"{base_url}{url_prefix}/{new_share_id}",
    }


def get_skill_share_status(db: Session, user_id: str, skill_id: str) -> dict:
    """Get the current share status for all share types of a skill."""
    skill = _get_skill(db, user_id, skill_id)
    
    # Count subscribers by type
    live_count = db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.skill_id == skill_id,
        SharedSkillSubscription.share_type == "live"
    ).count()
    
    collaborate_count = db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.skill_id == skill_id,
        SharedSkillSubscription.share_type == "collaborate"
    ).count()
    
    return {
        "clone_share_id": skill.clone_share_id,
        "live_share_id": skill.live_share_id,
        "collaborate_share_id": skill.collaborate_share_id,
        "live_subscriber_count": live_count,
        "collaborate_subscriber_count": collaborate_count,
    }


def delete_skill_share(db: Session, user_id: str, skill_id: str, share_type: Optional[ShareType] = None) -> dict:
    """Remove share info from a skill. If share_type specified, only remove that type."""
    skill = _get_skill(db, user_id, skill_id)
    
    if share_type is None:
        # Delete all shares and subscriptions
        db.query(SharedSkillSubscription).filter(
            SharedSkillSubscription.skill_id == skill_id
        ).delete()
        skill.clone_share_id = None
        skill.live_share_id = None
        skill.collaborate_share_id = None
    else:
        # Delete only the specific share type
        share_id_attr = _get_share_id_field(share_type)
        setattr(skill, share_id_attr, None)
        
        if share_type in (ShareType.LIVE, ShareType.COLLABORATE):
            db.query(SharedSkillSubscription).filter(
                SharedSkillSubscription.skill_id == skill_id,
                SharedSkillSubscription.share_type == share_type.value
            ).delete()
    
    skill.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {"ok": True, "share_type": share_type.value if share_type else "all"}


def get_shared_skill_by_share_id(db: Session, share_id: str, share_type: Optional[ShareType] = None) -> Optional["Skills"]:
    """Find a skill by its share_id and optionally share_type."""
    if not share_id:
        return None
    
    cleaned_id = share_id.strip()
    
    if share_type == ShareType.CLONE:
        return db.query(Skills).filter(Skills.clone_share_id == cleaned_id).first()
    elif share_type == ShareType.LIVE:
        return db.query(Skills).filter(Skills.live_share_id == cleaned_id).first()
    elif share_type == ShareType.COLLABORATE:
        return db.query(Skills).filter(Skills.collaborate_share_id == cleaned_id).first()
    else:
        # Search all share types
        skill = db.query(Skills).filter(Skills.clone_share_id == cleaned_id).first()
        if skill:
            return skill
        skill = db.query(Skills).filter(Skills.live_share_id == cleaned_id).first()
        if skill:
            return skill
        skill = db.query(Skills).filter(Skills.collaborate_share_id == cleaned_id).first()
        if skill:
            return skill
        return None


def detect_share_type_from_id(db: Session, share_id: str) -> Optional[ShareType]:
    """Detect the share type from a share_id."""
    if not share_id:
        return None
    cleaned_id = share_id.strip()
    
    if db.query(Skills).filter(Skills.clone_share_id == cleaned_id).first():
        return ShareType.CLONE
    if db.query(Skills).filter(Skills.live_share_id == cleaned_id).first():
        return ShareType.LIVE
    if db.query(Skills).filter(Skills.collaborate_share_id == cleaned_id).first():
        return ShareType.COLLABORATE
    return None


def get_shared_skill_preview(
    db: Session,
    share_id: str,
    share_type: Optional[ShareType] = None,
    requesting_user_id: Optional[str] = None,
) -> dict:
    """Get a preview of a shared skill (public endpoint)."""
    if not share_id:
        raise HTTPException(status_code=400, detail="share_id is required")
    
    skill = get_shared_skill_by_share_id(db, share_id, share_type)
    if not skill:
        raise HTTPException(status_code=404, detail="Shared skill not found")

    detected_type = detect_share_type_from_id(db, share_id) or share_type or ShareType.LIVE

    if requesting_user_id and skill.user_id == requesting_user_id:
        raise HTTPException(status_code=400, detail="You cannot open your own shared skill")

    if requesting_user_id and detected_type in (ShareType.LIVE, ShareType.COLLABORATE):
        already_subscribed = db.query(SharedSkillSubscription).filter(
            SharedSkillSubscription.skill_id == skill.id,
            SharedSkillSubscription.subscriber_id == requesting_user_id,
        ).first()
        if already_subscribed:
            raise HTTPException(status_code=409, detail="You already added this shared skill")
    
    owner_name = _get_owner_display_name(db, skill.user_id)
    
    return {
        "share_id": share_id,
        "share_type": detected_type.value,
        "title": skill.name,
        "description": skill.description,
        "icon": skill.icon,
        "content_preview": (skill.content[:200] + "...") if skill.content and len(skill.content) > 200 else skill.content,
        "owner_name": owner_name,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
    }


def clone_shared_skill(db: Session, user_id: str, share_id: str) -> "Skills":
    """Clone a shared skill for a user (creates a new independent copy)."""
    skill = get_shared_skill_by_share_id(db, share_id, ShareType.CLONE)
    if not skill:
        raise HTTPException(status_code=404, detail="Shared skill not found or not available for cloning")
    
    if skill.user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot clone your own skill")

    source_path = _skill_directory(skill.user_id, skill.id)
    if not source_path.is_dir() or not (source_path / "SKILL.md").is_file():
        # A current skill is always backed by a complete package. Treat a
        # database-only or incomplete record as unavailable instead of
        # fabricating a different package during cloning.
        raise HTTPException(
            status_code=404,
            detail="Shared skill not found or not available for cloning",
        )
    
    # Create a new skill with the same content
    cloned_skill = Skills(
        id=str(uuid.uuid4()),
        user_id=user_id,
        icon=skill.icon,
        name=skill.name,
        description=skill.description,
        content=skill.content,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    destination_path = _skill_directory(user_id, cloned_skill.id)

    try:
        # SKILL.md carries optional compatibility, license, and metadata. Copy
        # the complete package so those fields and every supporting file stay
        # with the independent clone.
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_path, destination_path)

        db.add(cloned_skill)
        db.commit()
        db.refresh(cloned_skill)
        return cloned_skill
    except Exception:
        db.rollback()
        shutil.rmtree(destination_path, ignore_errors=True)
        raise


# ============================================================================
# Subscription Management Functions
# ============================================================================

def subscribe_to_shared_skill(
    db: Session, 
    subscriber_id: str, 
    skill_id: str,
    share_type: ShareType = ShareType.LIVE,
) -> "SharedSkillSubscription":
    """Subscribe a user to a shared skill (live or collaborate)."""
    if share_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Clone shares don't support subscriptions")
    existing = db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.skill_id == skill_id,
        SharedSkillSubscription.subscriber_id == subscriber_id,
    ).first()
    
    if existing:
        # Update existing subscription if share type changed
        if existing.share_type != share_type.value:
            existing.share_type = share_type.value
            db.commit()
            db.refresh(existing)
        return existing
    
    subscription = SharedSkillSubscription(
        id=str(uuid.uuid4()),
        skill_id=skill_id,
        subscriber_id=subscriber_id,
        share_type=share_type.value,
        subscribed_at=datetime.now(timezone.utc),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def unsubscribe_from_shared_skill(db: Session, subscriber_id: str, skill_id: str) -> dict:
    """Unsubscribe a user from a shared skill."""
    deleted = db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.skill_id == skill_id,
        SharedSkillSubscription.subscriber_id == subscriber_id,
    ).delete()
    db.commit()
    return {"ok": True, "deleted": deleted > 0}



def get_subscribed_skills(db: Session, user_id: str) -> List[tuple]:
    """Get all skills that a user is subscribed to with subscription info."""
    subscriptions = db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.subscriber_id == user_id
    ).all()
    
    if not subscriptions:
        return []
    
    result = []
    for sub in subscriptions:
        skill = db.query(Skills).filter(Skills.id == sub.skill_id).first()
        if skill:
            # Verify the share is still active
            if sub.share_type == "live" and skill.live_share_id:
                result.append((skill, sub))
            elif sub.share_type == "collaborate" and skill.collaborate_share_id:
                result.append((skill, sub))
    
    return result


def get_skill_subscriber_count(db: Session, skill_id: str, share_type: Optional[str] = None) -> int:
    """Get the number of subscribers for a skill."""
    query = db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.skill_id == skill_id
    )
    if share_type:
        query = query.filter(SharedSkillSubscription.share_type == share_type)
    return query.count()



def get_skill_content_for_user(
    db: Session,
    user_id: str,
    skill_id: str,
    *,
    trusted_admin_skill_ids: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Get skill content by ID for a user.
    
    Checks in order:
    1. User's own skills
    2. Skills shared with the user (subscriptions)
    3. Admin skills assigned to the user's group or trusted model settings
    
    Returns the skill content (markdown text) or None if not found/accessible.
    """
    resolved = _resolve_accessible_skill_for_user(
        db,
        user_id,
        skill_id,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    if not resolved:
        return None
    return resolved[0]


def _normalize_admin_skill_id_set(skill_ids: Optional[list[str]]) -> set[str]:
    if not isinstance(skill_ids, list):
        return set()
    return {str(skill_id or "").strip() for skill_id in skill_ids if str(skill_id or "").strip()}


def _get_user_admin_skill_ids(db: Session, user_id: str) -> list[str]:
    group_setting = get_user_group_setting_value
    try:
        skills_enabled = group_setting(user_id, "skills", "enabled_skills", db)
        admin_skill_ids = group_setting(user_id, "skills", "admin_skill_ids", db)
    except Exception:
        try:
            from app.groups.init import get_user_group_setting_value as live_group_setting

            if live_group_setting is group_setting:
                return []
            skills_enabled = live_group_setting(user_id, "skills", "enabled_skills", db)
            admin_skill_ids = live_group_setting(user_id, "skills", "admin_skill_ids", db)
        except Exception:
            return []

    if not skills_enabled or not isinstance(admin_skill_ids, list):
        return []
    return list(admin_skill_ids)


def _user_can_access_admin_skill(
    db: Session,
    user_id: str,
    skill_id: str,
    *,
    trusted_admin_skill_ids: Optional[list[str]] = None,
) -> bool:
    if user_id == ADMIN_SKILLS_USER_ID:
        return True

    if skill_id in _normalize_admin_skill_id_set(trusted_admin_skill_ids):
        return True

    admin_skill_ids = _get_user_admin_skill_ids(db, user_id)
    return skill_id in _normalize_admin_skill_id_set(admin_skill_ids)


def _resolve_accessible_skill_for_user(
    db: Session,
    user_id: str,
    skill_id: str,
    *,
    trusted_admin_skill_ids: Optional[list[str]] = None,
) -> Optional[tuple[str, str]]:
    """
    Resolve a skill the user can access.

    Returns a tuple of (skill_content, storage_user_id) or None if not accessible.
    """
    if not skill_id or not isinstance(skill_id, str):
        return None

    skill_id = skill_id.strip()
    if not skill_id:
        return None

    # 1. Check user's own skills
    user_skill = db.query(Skills).filter(
        Skills.id == skill_id,
        Skills.user_id == user_id
    ).first()
    if user_skill:
        return user_skill.content, user_skill.user_id

    # 2. Check skills shared with the user (via subscription)
    subscription = db.query(SharedSkillSubscription).filter(
        SharedSkillSubscription.skill_id == skill_id,
        SharedSkillSubscription.subscriber_id == user_id,
    ).first()
    if subscription:
        shared_skill = db.query(Skills).filter(Skills.id == skill_id).first()
        if shared_skill:
            return shared_skill.content, shared_skill.user_id

    # 3. Check admin skills assigned to the user's group or applied by trusted server-side model settings.
    if _user_can_access_admin_skill(
        db,
        user_id,
        skill_id,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    ):
        admin_skill = db.query(AdminSkills).filter(AdminSkills.id == skill_id).first()
        if admin_skill:
            return admin_skill.content, ADMIN_SKILLS_USER_ID

    return None


def build_skill_file_descriptor(skill_id: str, relative_path: str) -> str:
    normalized_skill_id = str(skill_id or "").strip()
    normalized_relative_path = str(relative_path or "").strip().replace("\\", "/")
    if not normalized_skill_id or not normalized_relative_path:
        raise ValueError("skill_id and relative_path are required")
    encoded_path = quote(normalized_relative_path, safe="/-_.")
    return f"{SKILL_FILE_DESCRIPTOR_PREFIX}{normalized_skill_id}:{encoded_path}"


def parse_skill_file_descriptor(value: str | None) -> tuple[str, str] | None:
    normalized = str(value or "").strip()
    if not normalized.startswith(SKILL_FILE_DESCRIPTOR_PREFIX):
        return None
    payload = normalized[len(SKILL_FILE_DESCRIPTOR_PREFIX):]
    if ":" not in payload:
        return None
    skill_id, encoded_rel_path = payload.split(":", 1)
    skill_id = skill_id.strip()
    rel_path = unquote(encoded_rel_path or "").strip().replace("\\", "/")
    if not skill_id or not rel_path:
        return None
    return skill_id, rel_path


def resolve_skill_file_info_for_user(
    db: Session,
    *,
    user_id: str,
    descriptor: str,
    trusted_admin_skill_ids: Optional[list[str]] = None,
) -> dict | None:
    parsed = parse_skill_file_descriptor(descriptor)
    if not parsed:
        return None
    skill_id, relative_path = parsed

    resolved = _resolve_accessible_skill_for_user(
        db,
        user_id,
        skill_id,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    if not resolved:
        return None
    _content, storage_user_id = resolved

    try:
        safe_relative = _normalize_skill_relative_path(relative_path)
    except ValueError:
        return None

    try:
        skill_dir = _skill_directory(storage_user_id, skill_id)
    except ValueError:
        return None

    target_path = (skill_dir / safe_relative).resolve()
    try:
        skill_dir_resolved = skill_dir.resolve()
    except Exception:
        skill_dir_resolved = skill_dir
    try:
        target_path.relative_to(skill_dir_resolved)
    except ValueError:
        return None
    if not target_path.exists() or not target_path.is_file() or target_path.is_symlink():
        return None

    rel_for_display = str(PurePosixPath(safe_relative))
    file_name = target_path.name
    mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    size = int(target_path.stat().st_size)
    original_name = f"{skill_id}/{rel_for_display}"

    return {
        "path": str(target_path),
        "file_name": file_name,
        "file_type": mime_type,
        "file_category": _infer_skill_file_category(mime_type),
        "file_size": size,
        "meta": {
            "original_filename": original_name,
            "skill_id": skill_id,
            "skill_relative_path": rel_for_display,
            "skill_file_descriptor": descriptor,
        },
    }


def get_skill_file_descriptors_by_category_for_user(
    db: Session,
    user_id: str,
    skill_id: str,
    *,
    trusted_admin_skill_ids: Optional[list[str]] = None,
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {
        "image": [],
        "video": [],
        "audio": [],
        "document": [],
    }

    resolved = _resolve_accessible_skill_for_user(
        db,
        user_id,
        skill_id,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    if not resolved:
        return grouped

    _content, storage_user_id = resolved
    try:
        skill_dir = _skill_directory(storage_user_id, skill_id)
    except ValueError:
        return grouped
    if not skill_dir.exists() or not skill_dir.is_dir():
        return grouped

    # Return lightweight descriptors instead of reading file contents here.
    # Provider-specific attachment handling can then choose the most efficient
    # supported representation: native media, native documents, or text
    # extraction. This avoids also copying the same file into the system prompt.
    for folder in SKILL_FILE_FOLDERS:
        folder_path = skill_dir / folder
        if not folder_path.exists() or not folder_path.is_dir():
            continue
        for file_path in sorted(
            (path for path in folder_path.rglob("*") if path.is_file()),
            key=lambda path: str(path.relative_to(skill_dir)).lower(),
        ):
            if file_path.is_symlink():
                continue
            rel_path = str(file_path.relative_to(skill_dir)).replace("\\", "/")
            try:
                descriptor = build_skill_file_descriptor(skill_id, rel_path)
            except ValueError:
                continue
            mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            category = _infer_skill_file_category(mime_type)
            grouped[category].append(descriptor)

    return grouped
