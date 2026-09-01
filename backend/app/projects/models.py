from sqlalchemy import Column, String, JSON
from fastapi import HTTPException, status
from sqlalchemy import DateTime
from sqlalchemy import Index, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Session
import logging
from sqlalchemy.exc import IntegrityError
import hashlib
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Dict, List

from app.database import Base
from app.auth.utils import hash_password, verify_password
from app.redis_client import get_redis_client
from app.projects.utils import ensure_project_sharing_allowed
from app.settings.utils import get_public_url
from app.utils.icon_security import sanitize_hex_color, sanitize_icon_input


PROJECT_SHARE_MIN_PASSWORD_LENGTH = 8
PROJECT_SHARE_MAX_PASSWORD_LENGTH = 256
PROJECT_SHARE_PASSWORD_ATTEMPT_LIMIT = 5
PROJECT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS = 10 * 60
_PROJECT_SHARE_PASSWORD_ATTEMPTS_MAX_SIZE = 10000
_PROJECT_SHARE_PASSWORD_ATTEMPT_LOCK = threading.Lock()
_PROJECT_SHARE_PASSWORD_ATTEMPTS: dict[str, tuple[int, float]] = {}
logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_user_id", "user_id"),
        Index("ix_projects_created_at", "created_at"),
        Index("ix_projects_last_updated_at", "last_updated_at"),
        Index("ix_projects_link_share_id", "link_share_id"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    last_updated_at = Column(DateTime, nullable=False)
    # Store file IDs as JSON-encoded lists of strings (same approach as ChatMessages)
    images = Column(String, nullable=True)
    videos = Column(String, nullable=True)
    audios = Column(String, nullable=True)
    documents = Column(String, nullable=True)
    settings = Column(
        JSON,
        nullable=False,
        default=lambda: {
            "icon": "",
            "icon_color": "",
            "system_instruction": "",
            "separate_memory_enabled": False,
        },
    )
    # Link sharing
    link_share_id = Column(String, nullable=True, unique=True)
    link_share_password_hash = Column(String, nullable=True)
    link_share_expires_at = Column(DateTime(timezone=True), nullable=True)
    link_share_created_at = Column(DateTime(timezone=True), nullable=True)


class ProjectMember(Base):
    """Tracks which users are members of shared projects."""
    __tablename__ = "project_members"
    __table_args__ = (
        Index("ix_project_members_project_id", "project_id"),
        Index("ix_project_members_user_id", "user_id"),
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
    )

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)  # The member user
    role = Column(String, nullable=False, default="member")  # "owner" or "member"
    joined_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)



# -------------------
# List Projects
# -------------------
def list_projects(db, user_id: str):
    projects = (
        db.query(Project)
        .filter(Project.user_id == user_id)
        .order_by(Project.last_updated_at.desc())
        .all()
    )
    return projects



# -------------------
# Create Project
# -------------------
def get_project(db, user_id: str, project_id: str):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project



# -------------------
# Create Project
# -------------------
def create_project(db, user_id: str, title: str, settings: Optional[Dict[str, Optional[str]]] = None):
    project_settings = _with_settings_defaults(settings)
    project = Project(
        user_id=user_id,
        title=title,
        created_at=datetime.now(timezone.utc),
        last_updated_at=datetime.now(timezone.utc),
        settings=project_settings,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project



# -------------------
# Update Project
# -------------------
def update_project(
    db,
    user_id: str,
    project_id: str,
    title: Optional[str] = None,
    settings: Optional[Dict[str, Optional[str]]] = None,
):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    updated = False
    if title is not None:
        project.title = title
        updated = True
    if settings is not None:
        existing = _with_settings_defaults(project.settings)
        project.settings = {
            "icon": sanitize_icon_input(settings.get("icon", existing.get("icon", "")), fallback=""),
            "icon_color": sanitize_hex_color(settings.get("icon_color", existing.get("icon_color", "")), fallback=""),
            "system_instruction": settings.get("system_instruction", existing.get("system_instruction", "")) or "",
            "separate_memory_enabled": _coerce_bool(
                settings.get("separate_memory_enabled", existing.get("separate_memory_enabled", False))
            ),
        }
        updated = True
    if updated:
        project.last_updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(project)
    return project



# -------------------
# Delete Project
# -------------------
def delete_project(db, user_id: str, project_id: str):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    post_commit_cleanup_actions = _delete_projects_and_related_data(db, [project_id])
    db.commit()
    _run_post_commit_project_cleanup(post_commit_cleanup_actions)
    return True


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    if value in (0, 1):
        return bool(value)
    return False


def _with_settings_defaults(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = {
        "icon": "",
        "icon_color": "",
        "system_instruction": "",
        "separate_memory_enabled": False,
    }
    if not settings:
        return base
    return {
        "icon": sanitize_icon_input(settings.get("icon", ""), fallback=""),
        "icon_color": sanitize_hex_color(settings.get("icon_color", ""), fallback=""),
        "system_instruction": settings.get("system_instruction", "") or "",
        "separate_memory_enabled": _coerce_bool(settings.get("separate_memory_enabled", False)),
    }


def _ensure_project_memory_scope_change_allowed(
    project: Project,
    user_id: str,
    settings: Optional[Dict[str, Any]],
    existing: Dict[str, Any],
) -> None:
    if settings is None or "separate_memory_enabled" not in settings:
        return

    requested_separate_memory_enabled = _coerce_bool(settings.get("separate_memory_enabled"))
    existing_separate_memory_enabled = bool(existing.get("separate_memory_enabled", False))
    if requested_separate_memory_enabled == existing_separate_memory_enabled:
        return
    if project.user_id == user_id:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the project owner can change separate project memory.",
    )


# ============================================================================
# Project Sharing Functions
# ============================================================================

def _get_user_display_name(db, user_id: str) -> str:
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


def _get_project_member_record(db, project_id: str, user_id: str) -> Optional[ProjectMember]:
    return db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id,
    ).first()


def _create_project_member_record(db, project_id: str, user_id: str) -> ProjectMember:
    existing = _get_project_member_record(db, project_id, user_id)
    if existing:
        return existing

    member = ProjectMember(
        id=str(uuid.uuid4()),
        project_id=project_id,
        user_id=user_id,
        role="member",
        joined_at=datetime.now(timezone.utc),
    )
    db.add(member)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _get_project_member_record(db, project_id, user_id)
        if existing:
            return existing
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project member already exists but could not be loaded",
        )
    db.refresh(member)
    return member


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.isoformat()


def _normalize_project_share_password_for_storage(password: str | None) -> str | None:
    if password is None:
        return None
    normalized = str(password).strip()
    if not normalized:
        return None
    if len(normalized) < PROJECT_SHARE_MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Share password must be at least {PROJECT_SHARE_MIN_PASSWORD_LENGTH} characters long",
        )
    if len(normalized) > PROJECT_SHARE_MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Share password must be at most {PROJECT_SHARE_MAX_PASSWORD_LENGTH} characters long",
        )
    return normalized


def _cleanup_stale_project_share_password_attempts() -> None:
    now = time.time()
    with _PROJECT_SHARE_PASSWORD_ATTEMPT_LOCK:
        if len(_PROJECT_SHARE_PASSWORD_ATTEMPTS) <= _PROJECT_SHARE_PASSWORD_ATTEMPTS_MAX_SIZE:
            return
        stale_keys = [
            key
            for key, (_count, reset_at) in _PROJECT_SHARE_PASSWORD_ATTEMPTS.items()
            if reset_at <= now
        ]
        for key in stale_keys:
            _PROJECT_SHARE_PASSWORD_ATTEMPTS.pop(key, None)


def _project_share_password_attempt_key(share_id: str, client_ip: str | None) -> str:
    material = f"{str(share_id or '').strip()}:{str(client_ip or 'unknown').strip() or 'unknown'}"
    digest = hashlib.sha256(material.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"omlorix:shared-project:password-attempts:{digest}"


def _get_local_project_share_password_attempt_count(key: str) -> int:
    now = time.time()
    with _PROJECT_SHARE_PASSWORD_ATTEMPT_LOCK:
        count, reset_at = _PROJECT_SHARE_PASSWORD_ATTEMPTS.get(key, (0, 0.0))
        if reset_at <= now:
            _PROJECT_SHARE_PASSWORD_ATTEMPTS.pop(key, None)
            return 0
        return count


def _increment_local_project_share_password_attempt_count(key: str) -> int:
    _cleanup_stale_project_share_password_attempts()
    now = time.time()
    with _PROJECT_SHARE_PASSWORD_ATTEMPT_LOCK:
        count, reset_at = _PROJECT_SHARE_PASSWORD_ATTEMPTS.get(key, (0, 0.0))
        if reset_at <= now:
            count = 0
            reset_at = now + PROJECT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS
        count += 1
        _PROJECT_SHARE_PASSWORD_ATTEMPTS[key] = (count, reset_at)
        return count


def _clear_local_project_share_password_attempt_count(key: str) -> None:
    with _PROJECT_SHARE_PASSWORD_ATTEMPT_LOCK:
        _PROJECT_SHARE_PASSWORD_ATTEMPTS.pop(key, None)


def _get_project_share_password_attempt_count(key: str) -> int:
    client = get_redis_client()
    if client is not None:
        try:
            return int(client.get(key) or 0)
        except Exception:
            pass
    return _get_local_project_share_password_attempt_count(key)


def _record_project_share_password_failure(key: str) -> int:
    client = get_redis_client()
    if client is not None:
        try:
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, PROJECT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS)
            return count
        except Exception:
            pass
    return _increment_local_project_share_password_attempt_count(key)


def _clear_project_share_password_failures(key: str) -> None:
    client = get_redis_client()
    if client is not None:
        try:
            client.delete(key)
            return
        except Exception:
            pass
    _clear_local_project_share_password_attempt_count(key)


def _enforce_project_share_password_attempt_limit(share_id: str, client_ip: str | None) -> str:
    key = _project_share_password_attempt_key(share_id, client_ip)
    if _get_project_share_password_attempt_count(key) >= PROJECT_SHARE_PASSWORD_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many invalid password attempts. Please retry later.",
        )
    return key


def _normalize_share_expiry(expires_at: Optional[datetime]) -> Optional[datetime]:
    if expires_at is None:
        return None
    normalized = expires_at
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    if normalized <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="expires_at must be in the future")
    return normalized


def _clear_project_share(project: Project) -> None:
    project.link_share_id = None
    project.link_share_password_hash = None
    project.link_share_expires_at = None
    project.link_share_created_at = None


def _check_and_cleanup_expired_project_share(db, project: Optional[Project]) -> bool:
    if not project or not project.link_share_id or not project.link_share_expires_at:
        return False
    expires_at = project.link_share_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        _clear_project_share(project)
        project.last_updated_at = datetime.now(timezone.utc)
        db.commit()
        return True
    return False


def _project_has_existing_share_state(db, project: Project) -> bool:
    """Return true when a project already has active sharing to preserve.

    The group-level project sharing setting blocks newly sharing untouched
    projects. It should not revoke management for projects that already have a
    live link or members from an earlier sharing session.
    """
    if not project:
        return False
    if project.link_share_id:
        return True
    return (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project.id)
        .count()
        > 0
    )


def get_project_with_access(db, user_id: str, project_id: str) -> Project:
    """Get a project if the user has access (owner or member)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Check if user is owner
    if project.user_id == user_id:
        return project
    
    # Check if user is a member
    member = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id
    ).first()
    
    if member:
        return project
    
    raise HTTPException(status_code=404, detail="Project not found")


def is_project_owner(db, user_id: str, project_id: str) -> bool:
    """Check if user is the owner of the project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return False
    return project.user_id == user_id


def is_project_member(db, user_id: str, project_id: str) -> bool:
    """Check if user is a member of the project (not owner)."""
    member = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id
    ).first()
    return member is not None


def has_project_access(db, user_id: str, project_id: str) -> bool:
    """Check if user has access to the project (owner or member)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return False
    if project.user_id == user_id:
        return True
    return is_project_member(db, user_id, project_id)


def ensure_project_access_for_chat_send(db, user_id: str, project_id: str | None = None, chat=None) -> tuple[str | None, str | None]:
    """Resolve and authorize the server-owned project scope for a chat send.

    Persisted chats always keep their stored project scope, including an
    intentionally empty scope. The request project is only authoritative while
    creating a new chat, so a stale or tampered client cannot move reference,
    memory, or tool resolution into another project.
    """
    requested_project_id = str(project_id or "").strip() or None
    chat_project_id = str(getattr(chat, "project_id", None) or "").strip() or None
    resolved_project_id = chat_project_id if chat is not None else requested_project_id

    if resolved_project_id and not has_project_access(db, user_id, resolved_project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    return resolved_project_id, chat_project_id


def is_project_separate_memory_enabled(db, user_id: str, project_id: str) -> bool:
    project = get_project_with_access(db, user_id, project_id)
    settings = _with_settings_defaults(project.settings)
    return bool(settings.get("separate_memory_enabled"))


def can_send_message_in_chat(db, user_id: str, chat_id: str) -> bool:
    """Check if user can send messages in a chat (only the visible chat creator)."""
    from app.chats.models import Chats, can_send_messages_to_chat
    chat = db.query(Chats).filter(Chats.id == chat_id).first()
    if not chat:
        return False
    if chat.user_id != user_id:
        return False
    return can_send_messages_to_chat(chat)


def list_projects_with_shared(db, user_id: str) -> List[dict]:
    """List all projects user owns or is a member of."""
    # Get owned projects
    owned_projects = (
        db.query(Project)
        .filter(Project.user_id == user_id)
        .order_by(Project.last_updated_at.desc())
        .all()
    )
    
    # Get shared projects (where user is a member)
    member_records = db.query(ProjectMember).filter(
        ProjectMember.user_id == user_id
    ).all()
    
    shared_project_ids = [m.project_id for m in member_records]
    shared_projects = []
    if shared_project_ids:
        shared_projects = (
            db.query(Project)
            .filter(Project.id.in_(shared_project_ids))
            .order_by(Project.last_updated_at.desc())
            .all()
        )
    
    results = []
    
    # Add owned projects
    for project in owned_projects:
        member_count = db.query(ProjectMember).filter(
            ProjectMember.project_id == project.id
        ).count()
        results.append({
            "project": project,
            "is_owner": True,
            "is_shared": project.link_share_id is not None or member_count > 0,
            "member_count": member_count,
            "owner_name": None,
        })
    
    # Add shared projects
    for project in shared_projects:
        owner_name = _get_user_display_name(db, project.user_id)
        member_count = db.query(ProjectMember).filter(
            ProjectMember.project_id == project.id
        ).count()
        results.append({
            "project": project,
            "is_owner": False,
            "is_shared": True,
            "member_count": member_count,
            "owner_name": owner_name,
        })
    
    # Sort by last_updated_at
    results.sort(key=lambda x: x["project"].last_updated_at, reverse=True)
    
    return results


def create_project_link_share(
    db,
    user_id: str,
    project_id: str,
    password: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    password_provided: bool = False,
    expires_at_provided: bool = False,
    rotate: bool = False,
) -> dict:
    """Create or return existing link share for a project."""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == user_id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    _check_and_cleanup_expired_project_share(db, project)
    if not _project_has_existing_share_state(db, project):
        ensure_project_sharing_allowed(user_id, db)

    base_url = get_public_url(db)
    has_existing_share = bool(project.link_share_id)
    if rotate or not has_existing_share:
        project.link_share_id = str(uuid.uuid4())
        project.link_share_created_at = datetime.now(timezone.utc)
    elif not project.link_share_created_at:
        project.link_share_created_at = datetime.now(timezone.utc)

    if password_provided:
        normalized_password = _normalize_project_share_password_for_storage(password)
        project.link_share_password_hash = hash_password(normalized_password) if normalized_password else None
    if expires_at_provided:
        project.link_share_expires_at = _normalize_share_expiry(expires_at)

    project.last_updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "share_id": project.link_share_id,
        "share_url": f"{base_url}/projects/join/{project.link_share_id}",
        "has_password": bool(project.link_share_password_hash),
        "created_at": _datetime_to_iso(project.link_share_created_at),
        "expires_at": _datetime_to_iso(project.link_share_expires_at),
    }


def delete_project_link_share(db, user_id: str, project_id: str) -> dict:
    """Remove link share from a project."""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == user_id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    _clear_project_share(project)
    project.last_updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {"ok": True}


def get_project_by_share_id(db, share_id: str) -> Optional[Project]:
    """Get a project by its link share ID."""
    cleaned_share_id = str(share_id or "").strip()
    if not cleaned_share_id:
        return None
    project = db.query(Project).filter(Project.link_share_id == cleaned_share_id).first()
    if _check_and_cleanup_expired_project_share(db, project):
        return None
    return project


def get_project_share_preview(db, share_id: str, requesting_user_id: Optional[str] = None) -> dict:
    """Get a preview of a shared project (for join page)."""
    project = get_project_by_share_id(db, share_id)
    if not project:
        raise HTTPException(status_code=404, detail="Shared project not found")
    
    if requesting_user_id:
        # Check if user is already the owner
        if project.user_id == requesting_user_id:
            raise HTTPException(status_code=400, detail="You cannot join your own project")
        
        # Check if user is already a member
        existing_member = db.query(ProjectMember).filter(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == requesting_user_id
        ).first()
        if existing_member:
            raise HTTPException(status_code=409, detail="You are already a member of this project")
    
    owner_name = _get_user_display_name(db, project.user_id)
    member_count = db.query(ProjectMember).filter(
        ProjectMember.project_id == project.id
    ).count()
    password_required = bool(project.link_share_password_hash)
    
    return {
        "project_id": project.id,
        "title": project.title,
        "owner_name": owner_name,
        "member_count": member_count,
        # Keep password-protected previews metadata-only until the password is verified on join.
        "settings": None if password_required else _with_settings_defaults(project.settings),
        "created_at": _datetime_to_iso(project.created_at),
        "password_required": password_required,
    }


def join_project_via_link(
    db,
    user_id: str,
    share_id: str,
    password: Optional[str] = None,
    client_ip: str | None = None,
) -> ProjectMember:
    """Join a project via its link share ID."""
    project = get_project_by_share_id(db, share_id)
    if not project:
        raise HTTPException(status_code=404, detail="Shared project not found")
    
    if project.user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot join your own project")

    if project.link_share_password_hash:
        normalized_password = str(password or "").strip()
        if not normalized_password:
            raise HTTPException(status_code=401, detail="Password required")
        attempt_key = _enforce_project_share_password_attempt_limit(project.link_share_id, client_ip)
        if not verify_password(normalized_password, project.link_share_password_hash):
            _record_project_share_password_failure(attempt_key)
            raise HTTPException(status_code=401, detail="Invalid password")
        _clear_project_share_password_failures(attempt_key)
    
    return _create_project_member_record(db, project.id, user_id)


def add_project_member(db, owner_id: str, project_id: str, member_user_id: str) -> ProjectMember:
    """Add a member to a project (only owner can do this)."""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == owner_id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if member_user_id == owner_id:
        raise HTTPException(status_code=400, detail="Cannot add yourself as a member")
    
    return _create_project_member_record(db, project_id, member_user_id)


def remove_project_member(db, user_id: str, project_id: str, member_user_id: str) -> dict:
    """Remove a member from a project. Owner can remove anyone, members can remove themselves."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    is_owner = project.user_id == user_id
    is_self_removal = user_id == member_user_id
    
    if not is_owner and not is_self_removal:
        raise HTTPException(status_code=403, detail="Only the project owner can remove members")
    
    deleted = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == member_user_id
    ).delete()
    
    db.commit()
    
    return {"ok": True, "removed": deleted > 0}


def get_project_members(db, project_id: str, user_id: str) -> List[dict]:
    """Get list of project members. User must have access to the project."""
    # Check access
    if not has_project_access(db, user_id, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Get owner info
    owner_name = _get_user_display_name(db, project.user_id)
    members = [{
        "user_id": project.user_id,
        "display_name": owner_name,
        "role": "owner",
        "joined_at": _datetime_to_iso(project.created_at),
    }]
    
    # Get members
    member_records = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id
    ).all()
    
    for record in member_records:
        member_name = _get_user_display_name(db, record.user_id)
        members.append({
            "user_id": record.user_id,
            "display_name": member_name,
            "role": record.role,
            "joined_at": _datetime_to_iso(record.joined_at),
        })
    
    return members


def get_project_share_status(db, user_id: str, project_id: str) -> dict:
    """Get the share status for a project (owner only)."""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == user_id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if _check_and_cleanup_expired_project_share(db, project):
        db.refresh(project)

    member_count = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id
    ).count()
    
    base_url = get_public_url(db)
    share_url = None
    if project.link_share_id:
        share_url = f"{base_url}/projects/join/{project.link_share_id}"
    
    return {
        "link_share_id": project.link_share_id,
        "share_url": share_url,
        "member_count": member_count,
        "has_password": bool(project.link_share_password_hash),
        "created_at": _datetime_to_iso(project.link_share_created_at),
        "expires_at": _datetime_to_iso(project.link_share_expires_at),
    }


def delete_project_with_members(db, user_id: str, project_id: str) -> bool:
    """Delete a project and all its members. Only owner can delete."""
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == user_id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    post_commit_cleanup_actions = _delete_projects_and_related_data(db, [project_id])
    db.commit()
    _run_post_commit_project_cleanup(post_commit_cleanup_actions)

    return True


def _normalize_project_ids(project_ids: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for project_id in project_ids:
        normalized_id = str(project_id or "").strip()
        if not normalized_id or normalized_id in seen:
            continue
        normalized.append(normalized_id)
        seen.add(normalized_id)
    return normalized


def _delete_project_file_storage(
    *,
    storage_provider: str,
    storage_key: str,
    user_id: str,
    file_name: str,
    materialized_path,
) -> None:
    from app.files.utils import delete_storage_reference

    delete_storage_reference(
        storage_provider=storage_provider,
        storage_key=storage_key,
        user_id=user_id,
        file_name=file_name,
    )
    materialized_path.unlink(missing_ok=True)


def _run_post_commit_project_cleanup(cleanup_actions: List[Callable[[], None]]) -> None:
    for cleanup_action in cleanup_actions:
        try:
            cleanup_action()
        except Exception:
            logger.exception("Failed to remove deleted project file from storage")


def _delete_projects_and_related_data(db: Session, project_ids: List[str]) -> List[Callable[[], None]]:
    from pathlib import Path

    from app.automations.models import remove_file_from_automations
    from app.chats.models import Chats
    from app.files.models import FileArtifactShare, Files
    from app.files.reference_cleanup import cleanup_file_references
    from app.files.storage import build_storage_key
    from app.files.utils import MATERIALIZED_TEMP_DIR
    from app.memories.models import Memory

    normalized_project_ids = _normalize_project_ids(project_ids)
    if not normalized_project_ids:
        return []

    cleanup_actions: List[Callable[[], None]] = []
    project_files = db.query(Files).filter(Files.project_id.in_(normalized_project_ids)).all()
    for file_row in project_files:
        storage_provider = str(getattr(file_row, "storage_provider", "") or "").strip().lower() or "local"
        storage_key = str(getattr(file_row, "storage_key", "") or "").strip()
        if not storage_key:
            storage_key = build_storage_key(file_row.user_id, file_row.file_name)

        suffix = Path(file_row.file_name or "").suffix or ".bin"
        materialized_path = MATERIALIZED_TEMP_DIR / f"{file_row.id}{suffix}"
        cleanup_actions.append(
            lambda storage_provider=storage_provider,
            storage_key=storage_key,
            user_id=file_row.user_id,
            file_name=file_row.file_name,
            materialized_path=materialized_path: _delete_project_file_storage(
                storage_provider=storage_provider,
                storage_key=storage_key,
                user_id=user_id,
                file_name=file_name,
                materialized_path=materialized_path,
            )
        )

        (
            db.query(FileArtifactShare)
            .filter(FileArtifactShare.file_id == file_row.id)
            .delete(synchronize_session=False)
        )
        cleanup_file_references(db, file_row.user_id, file_row.id)
        remove_file_from_automations(db, file_row.user_id, file_row.id, commit=False)
        db.delete(file_row)

    (
        db.query(Chats)
        .filter(Chats.project_id.in_(normalized_project_ids))
        .update({Chats.project_id: None}, synchronize_session=False)
    )
    (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id.in_(normalized_project_ids))
        .delete(synchronize_session=False)
    )
    (
        db.query(Memory)
        .filter(Memory.project_id.in_(normalized_project_ids))
        .delete(synchronize_session=False)
    )
    (
        db.query(Project)
        .filter(Project.id.in_(normalized_project_ids))
        .delete(synchronize_session=False)
    )
    return cleanup_actions


def update_project_shared(
    db,
    user_id: str,
    project_id: str,
    title: Optional[str] = None,
    settings: Optional[Dict[str, Optional[str]]] = None,
) -> Project:
    """Update a project. Both owner and members can update."""
    # Check access
    project = get_project_with_access(db, user_id, project_id)
    
    updated = False
    if title is not None:
        project.title = title
        updated = True
    if settings is not None:
        existing = _with_settings_defaults(project.settings)
        _ensure_project_memory_scope_change_allowed(project, user_id, settings, existing)
        project.settings = {
            "icon": sanitize_icon_input(settings.get("icon", existing.get("icon", "")), fallback=""),
            "icon_color": sanitize_hex_color(settings.get("icon_color", existing.get("icon_color", "")), fallback=""),
            "system_instruction": settings.get("system_instruction", existing.get("system_instruction", "")) or "",
            "separate_memory_enabled": _coerce_bool(
                settings.get("separate_memory_enabled", existing.get("separate_memory_enabled", False))
            ),
        }
        updated = True
    if updated:
        project.last_updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(project)
    return project
