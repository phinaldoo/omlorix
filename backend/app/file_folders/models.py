from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
import logging
import re

from fastapi import HTTPException, status
from sqlalchemy import Column, DateTime, Index, JSON, String, Integer, Text
from sqlalchemy.orm import Session

from app.database import Base
from app.settings.utils import get_public_url
from app.utils.icon_security import sanitize_hex_color, sanitize_icon_input


logger = logging.getLogger(__name__)


# System folders use a persisted identity that is independent from their
# editable display name.  Never infer this value from ``name``: a normal user
# folder is allowed to have the same visible name as a system folder.
FILE_FOLDER_SYSTEM_KIND_CANVAS = "canvas"
FILE_FOLDER_SYSTEM_KINDS = frozenset({FILE_FOLDER_SYSTEM_KIND_CANVAS})


_SENSITIVE_CLONE_META_PARTS = {
    "api_key",
    "auth",
    "credential",
    "password",
    "secret",
    "share_id",
    "token",
}


class ShareType(str, Enum):
    """Types of file folder sharing."""
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


# ---------------------------------------------------------------------------
# FileFolders Model
# ---------------------------------------------------------------------------
class FileFolders(Base):
    __tablename__ = "file_folders"
    __table_args__ = (
        Index("ix_file_folders_user_id", "user_id"),
        Index("ix_file_folders_created_at", "created_at"),
        # PostgreSQL and SQLite both permit multiple NULL values in a unique
        # index, while still allowing at most one folder of each system kind
        # per user.
        Index(
            "uq_file_folders_user_system_kind",
            "user_id",
            "system_kind",
            unique=True,
        ),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    name = Column(String(255), nullable=False)
    icon = Column(Text, nullable=True, default="folder")
    icon_color = Column(String(20), nullable=True, default="#6366f1")
    order = Column(Integer, nullable=False, default=0)
    system_kind = Column(String(32), nullable=True)
    clone_share_id = Column(String, nullable=True, index=True, unique=True)
    live_share_id = Column(String, nullable=True, index=True, unique=True)
    collaborate_share_id = Column(String, nullable=True, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# SharedFileFolderSubscription Model
# ---------------------------------------------------------------------------
class SharedFileFolderSubscription(Base):
    __tablename__ = "shared_file_folder_subscriptions"
    __table_args__ = (
        Index("ix_shared_ff_sub_folder_id", "folder_id"),
        Index("ix_shared_ff_sub_subscriber_id", "subscriber_id"),
    )

    id = Column(String, primary_key=True, index=True)
    folder_id = Column(String, nullable=False)
    subscriber_id = Column(String, nullable=False)
    share_type = Column(String, nullable=False, default="live")
    subscribed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize_user_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id is required")
    return value.strip()


def _datetime_to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.isoformat()


def _get_user_folder(db: Session, user_id: str, folder_id: str) -> "FileFolders":
    folder = (
        db.query(FileFolders)
        .filter(FileFolders.id == folder_id.strip(), FileFolders.user_id == user_id.strip())
        .first()
    )
    if not folder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File folder not found")
    return folder


def _get_owner_display_name(db: Session, user_id: str) -> str:
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
    return {
        ShareType.CLONE: "clone_share_id",
        ShareType.LIVE: "live_share_id",
        ShareType.COLLABORATE: "collaborate_share_id",
    }.get(share_type, "live_share_id")


def _get_share_url_prefix(share_type: ShareType) -> str:
    return {
        ShareType.CLONE: "/folders/clone",
        ShareType.LIVE: "/folders/live",
        ShareType.COLLABORATE: "/folders/collaborate",
    }.get(share_type, "/folders/live")


def _is_sensitive_clone_meta_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if not normalized:
        return False
    if normalized.startswith("shared_"):
        return True
    return any(part in normalized for part in _SENSITIVE_CLONE_META_PARTS)


def _clone_safe_file_meta(value):
    if isinstance(value, dict):
        return {
            key: _clone_safe_file_meta(item)
            for key, item in value.items()
            if not _is_sensitive_clone_meta_key(key)
        }
    if isinstance(value, list):
        return [_clone_safe_file_meta(item) for item in value]
    return deepcopy(value)


def _copy_file_storage_for_clone(source_file, source_user_id: str, target_user_id: str, target_file_name: str):
    from app.files.utils import materialize_file_record
    from app.files.storage import upload_file_to_storage

    source_path = materialize_file_record(source_file, source_user_id)
    return upload_file_to_storage(source_path, target_user_id, target_file_name)


def _cleanup_cloned_storage_reference(
    *,
    storage_provider: str,
    storage_key: str,
    user_id: str,
    file_name: str,
) -> None:
    from app.files.utils import delete_storage_reference

    try:
        delete_storage_reference(
            storage_provider=storage_provider,
            storage_key=storage_key,
            user_id=user_id,
            file_name=file_name,
        )
    except Exception:
        logger.warning(
            "[FileFolders] Failed to clean up cloned file storage",
            extra={
                "event": "file_folder_clone_storage_cleanup_failed",
                "user_id": user_id,
                "storage_provider": storage_provider,
                "storage_key": storage_key,
            },
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# CRUD Operations
# ---------------------------------------------------------------------------
def create_file_folder(
    db: Session,
    user_id: str,
    name: str,
    icon: str = "folder",
    icon_color: str = "#6366f1",
) -> FileFolders:
    user_id = _normalize_user_id(user_id)
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Folder name is required")

    max_order = db.query(FileFolders).filter(FileFolders.user_id == user_id).count()
    now = datetime.now(timezone.utc)
    folder = FileFolders(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=name.strip()[:255],
        icon=sanitize_icon_input(icon, fallback="folder"),
        icon_color=sanitize_hex_color(icon_color, fallback="#6366f1"),
        order=max_order,
        created_at=now,
        updated_at=now,
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return folder


def list_file_folders(db: Session, user_id: str) -> List[FileFolders]:
    user_id = _normalize_user_id(user_id)
    return (
        db.query(FileFolders)
        .filter(FileFolders.user_id == user_id)
        .order_by(FileFolders.order.asc(), FileFolders.created_at.asc())
        .all()
    )


def get_file_folder(db: Session, user_id: str, folder_id: str) -> Optional[FileFolders]:
    return (
        db.query(FileFolders)
        .filter(FileFolders.id == folder_id, FileFolders.user_id == user_id)
        .first()
    )


def update_file_folder(
    db: Session,
    user_id: str,
    folder_id: str,
    name: Optional[str] = None,
    icon: Optional[str] = None,
    icon_color: Optional[str] = None,
    order: Optional[int] = None,
) -> FileFolders:
    folder = _get_user_folder(db, user_id, folder_id)

    if name is not None:
        if not name.strip():
            raise HTTPException(status_code=400, detail="Folder name cannot be empty")
        folder.name = name.strip()[:255]
    if icon is not None:
        folder.icon = sanitize_icon_input(icon, fallback="folder")
    if icon_color is not None:
        folder.icon_color = sanitize_hex_color(icon_color, fallback="#6366f1")
    if order is not None:
        folder.order = order

    folder.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(folder)
    return folder


def delete_file_folder(db: Session, user_id: str, folder_id: str) -> dict:
    folder = _get_user_folder(db, user_id, folder_id)

    # Unset folder_id on files that belong to this folder
    from app.files.models import Files
    db.query(Files).filter(
        Files.folder_id == folder_id,
    ).update({"folder_id": None})

    # Delete subscriptions
    db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == folder_id
    ).delete()

    db.delete(folder)
    db.commit()
    return {"ok": True}


def add_files_to_folder(db: Session, user_id: str, folder_id: str, file_ids: List[str]) -> dict:
    folder = _get_user_folder(db, user_id, folder_id)
    from app.files.models import Files
    updated = 0
    for fid in file_ids:
        file_record = db.query(Files).filter(Files.id == fid, Files.user_id == user_id).first()
        if file_record:
            file_record.folder_id = folder_id
            updated += 1
    if updated:
        db.commit()
    return {"ok": True, "updated": updated}


def remove_files_from_folder(db: Session, user_id: str, folder_id: str, file_ids: List[str]) -> dict:
    _get_user_folder(db, user_id, folder_id)
    from app.files.models import Files
    updated = 0
    for fid in file_ids:
        file_record = db.query(Files).filter(
            Files.id == fid,
            Files.user_id == user_id,
            Files.folder_id == folder_id,
        ).first()
        if file_record:
            file_record.folder_id = None
            updated += 1
    if updated:
        db.commit()
    return {"ok": True, "updated": updated}


def move_file_to_folder(db: Session, user_id: str, file_id: str, folder_id: Optional[str]) -> dict:
    """Move a file to a folder (or remove from folder if folder_id is None)."""
    from app.files.models import Files
    file_record = db.query(Files).filter(Files.id == file_id, Files.user_id == user_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")
    if folder_id:
        if not can_user_edit_folder(db, user_id, folder_id):
            raise HTTPException(status_code=404, detail="File folder not found")
    file_record.folder_id = folder_id
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sharing Operations
# ---------------------------------------------------------------------------
def create_folder_share(db: Session, user_id: str, folder_id: str, share_type: ShareType = ShareType.LIVE) -> dict:
    folder = _get_user_folder(db, user_id, folder_id)
    # System folders are private implementation containers.  Enforce this at
    # the backend boundary so a stale frontend or direct API request cannot
    # turn automatic file organization into an implicit disclosure channel.
    if str(getattr(folder, "system_kind", "") or "").strip():
        raise HTTPException(
            status_code=409,
            detail={"code": "system_folder_not_shareable"},
        )
    share_id_attr = _get_share_id_field(share_type)
    existing_share_id = getattr(folder, share_id_attr, None)
    url_prefix = _get_share_url_prefix(share_type)
    base_url = get_public_url(db)

    if existing_share_id:
        return {
            "share_id": existing_share_id,
            "share_type": share_type.value,
            "share_url": f"{base_url}{url_prefix}/{existing_share_id}",
        }

    new_share_id = str(uuid.uuid4())
    setattr(folder, share_id_attr, new_share_id)
    folder.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "share_id": new_share_id,
        "share_type": share_type.value,
        "share_url": f"{base_url}{url_prefix}/{new_share_id}",
    }


def get_folder_share_status(db: Session, user_id: str, folder_id: str) -> dict:
    folder = _get_user_folder(db, user_id, folder_id)
    live_count = db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == folder_id,
        SharedFileFolderSubscription.share_type == "live"
    ).count()
    collaborate_count = db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == folder_id,
        SharedFileFolderSubscription.share_type == "collaborate"
    ).count()
    return {
        "clone_share_id": folder.clone_share_id,
        "live_share_id": folder.live_share_id,
        "collaborate_share_id": folder.collaborate_share_id,
        "live_subscriber_count": live_count,
        "collaborate_subscriber_count": collaborate_count,
    }


def delete_folder_share(db: Session, user_id: str, folder_id: str, share_type: Optional[ShareType] = None) -> dict:
    folder = _get_user_folder(db, user_id, folder_id)
    if share_type is None:
        db.query(SharedFileFolderSubscription).filter(
            SharedFileFolderSubscription.folder_id == folder_id
        ).delete()
        folder.clone_share_id = None
        folder.live_share_id = None
        folder.collaborate_share_id = None
    else:
        share_id_attr = _get_share_id_field(share_type)
        setattr(folder, share_id_attr, None)
        if share_type in (ShareType.LIVE, ShareType.COLLABORATE):
            db.query(SharedFileFolderSubscription).filter(
                SharedFileFolderSubscription.folder_id == folder_id,
                SharedFileFolderSubscription.share_type == share_type.value
            ).delete()
    folder.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "share_type": share_type.value if share_type else "all"}


def get_shared_folder_by_share_id(db: Session, share_id: str, share_type: Optional[ShareType] = None) -> Optional[FileFolders]:
    if not share_id:
        return None
    cleaned_id = share_id.strip()
    if share_type == ShareType.CLONE:
        return db.query(FileFolders).filter(FileFolders.clone_share_id == cleaned_id).first()
    elif share_type == ShareType.LIVE:
        return db.query(FileFolders).filter(FileFolders.live_share_id == cleaned_id).first()
    elif share_type == ShareType.COLLABORATE:
        return db.query(FileFolders).filter(FileFolders.collaborate_share_id == cleaned_id).first()
    else:
        for attr in ("clone_share_id", "live_share_id", "collaborate_share_id"):
            folder = db.query(FileFolders).filter(getattr(FileFolders, attr) == cleaned_id).first()
            if folder:
                return folder
        return None


def detect_share_type_from_id(db: Session, share_id: str) -> Optional[ShareType]:
    if not share_id:
        return None
    cleaned_id = share_id.strip()
    if db.query(FileFolders).filter(FileFolders.clone_share_id == cleaned_id).first():
        return ShareType.CLONE
    if db.query(FileFolders).filter(FileFolders.live_share_id == cleaned_id).first():
        return ShareType.LIVE
    if db.query(FileFolders).filter(FileFolders.collaborate_share_id == cleaned_id).first():
        return ShareType.COLLABORATE
    return None


def get_shared_folder_preview(
    db: Session,
    share_id: str,
    requesting_user_id: Optional[str] = None,
) -> dict:
    if not share_id:
        raise HTTPException(status_code=400, detail="share_id is required")
    folder = get_shared_folder_by_share_id(db, share_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Shared folder not found")
    if requesting_user_id and folder.user_id == requesting_user_id:
        raise HTTPException(status_code=400, detail="You cannot open your own shared folder")

    detected_type = detect_share_type_from_id(db, share_id) or ShareType.LIVE

    from app.files.access import count_shared_folder_preview_files
    file_count = count_shared_folder_preview_files(db, folder.id, detected_type.value)
    owner_name = _get_owner_display_name(db, folder.user_id)

    return {
        "share_id": share_id,
        "share_type": detected_type.value,
        "name": folder.name,
        "icon": folder.icon,
        "icon_color": folder.icon_color,
        "file_count": file_count,
        "owner_name": owner_name,
        "created_at": _datetime_to_iso(folder.created_at),
    }


def subscribe_to_shared_folder(
    db: Session,
    subscriber_id: str,
    folder_id: str,
    share_type: ShareType = ShareType.LIVE,
) -> SharedFileFolderSubscription:
    if share_type == ShareType.CLONE:
        raise HTTPException(status_code=400, detail="Clone shares don't support subscriptions")
    existing = db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == folder_id,
        SharedFileFolderSubscription.subscriber_id == subscriber_id,
    ).first()

    if existing:
        if existing.share_type != share_type.value:
            existing.share_type = share_type.value
            db.commit()
            db.refresh(existing)
        return existing

    subscription = SharedFileFolderSubscription(
        id=str(uuid.uuid4()),
        folder_id=folder_id,
        subscriber_id=subscriber_id,
        share_type=share_type.value,
        subscribed_at=datetime.now(timezone.utc),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def unsubscribe_from_shared_folder(db: Session, subscriber_id: str, folder_id: str) -> dict:
    deleted = db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == folder_id,
        SharedFileFolderSubscription.subscriber_id == subscriber_id,
    ).delete()
    db.commit()
    return {"ok": True, "deleted": deleted > 0}


def get_subscribed_folders(db: Session, user_id: str) -> List[tuple]:
    subscriptions = db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.subscriber_id == user_id
    ).all()
    if not subscriptions:
        return []
    result = []
    for sub in subscriptions:
        folder = db.query(FileFolders).filter(FileFolders.id == sub.folder_id).first()
        if folder:
            if sub.share_type == "live" and folder.live_share_id:
                result.append((folder, sub))
            elif sub.share_type == "collaborate" and folder.collaborate_share_id:
                result.append((folder, sub))
    return result


def get_folder_subscriber_count(db: Session, folder_id: str) -> int:
    return db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == folder_id
    ).count()


def clone_shared_folder(db: Session, user_id: str, share_id: str) -> FileFolders:
    folder = get_shared_folder_by_share_id(db, share_id, ShareType.CLONE)
    if not folder:
        raise HTTPException(status_code=404, detail="Shared folder not found or not available for cloning")
    if folder.user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot clone your own folder")

    from app.files.models import Files

    now = datetime.now(timezone.utc)
    cloned = FileFolders(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=folder.name,
        icon=folder.icon,
        icon_color=folder.icon_color,
        order=0,
        created_at=now,
        updated_at=now,
    )
    source_files = (
        db.query(Files)
        .filter(Files.folder_id == folder.id, Files.user_id == folder.user_id)
        .order_by(Files.created_at.asc(), Files.id.asc())
        .all()
    )
    if source_files:
        from app.files.utils import (
            ensure_user_file_upload_capacity,
            ensure_user_file_upload_size_limit,
            resolve_user_file_upload_limits,
            serialized_user_file_quota_admission,
        )

        max_files_limit, max_user_storage_limit_bytes = resolve_user_file_upload_limits(db, user_id)

    uploaded_storage_refs = []
    try:
        quota_context = (
            serialized_user_file_quota_admission(db, user_id)
            if source_files
            else nullcontext()
        )
        with quota_context:
            db.add(cloned)
            db.flush()

            for source_file in source_files:
                ensure_user_file_upload_size_limit(db, user_id, source_file.file_size)
                ensure_user_file_upload_capacity(
                    db,
                    user_id,
                    source_file.file_size,
                    max_files_limit=max_files_limit,
                    max_user_storage_limit_bytes=max_user_storage_limit_bytes,
                )
                cloned_file_id = str(uuid.uuid4())
                suffix = Path(source_file.file_name or "").suffix
                cloned_file_name = f"{cloned_file_id}{suffix}" if suffix else cloned_file_id
                storage_provider, storage_key, storage_meta = _copy_file_storage_for_clone(
                    source_file,
                    folder.user_id,
                    user_id,
                    cloned_file_name,
                )
                uploaded_storage_refs.append(
                    {
                        "storage_provider": storage_provider,
                        "storage_key": storage_key,
                        "file_name": cloned_file_name,
                    }
                )
                cloned_file = Files(
                    id=cloned_file_id,
                    user_id=user_id,
                    file_name=cloned_file_name,
                    storage_provider=storage_provider,
                    storage_key=storage_key,
                    storage_meta=storage_meta,
                    file_category=source_file.file_category,
                    file_type=source_file.file_type,
                    file_size=source_file.file_size,
                    project_id=None,
                    folder_id=cloned.id,
                    share=None,
                    share_id=None,
                    meta=_clone_safe_file_meta(source_file.meta) if isinstance(source_file.meta, dict) else None,
                    created_at=now,
                    last_updated_at=now,
                )
                db.add(cloned_file)

            db.commit()
    except HTTPException:
        db.rollback()
        for storage_ref in uploaded_storage_refs:
            _cleanup_cloned_storage_reference(user_id=user_id, **storage_ref)
        raise
    except Exception as exc:
        db.rollback()
        for storage_ref in uploaded_storage_refs:
            _cleanup_cloned_storage_reference(user_id=user_id, **storage_ref)
        logger.exception(
            "[FileFolders] Failed to clone shared folder",
            extra={
                "event": "file_folder_clone_failed",
                "user_id": user_id,
                "folder_id": folder.id,
                "share_id": share_id,
            },
        )
        raise HTTPException(status_code=500, detail="Failed to clone shared folder") from exc
    db.refresh(cloned)
    return cloned


def can_user_edit_folder(db: Session, user_id: str, folder_id: str) -> bool:
    folder = db.query(FileFolders).filter(FileFolders.id == folder_id).first()
    if not folder:
        return False
    if folder.user_id == user_id:
        return True
    subscription = db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == folder_id,
        SharedFileFolderSubscription.subscriber_id == user_id,
        SharedFileFolderSubscription.share_type == "collaborate",
    ).first()
    return subscription is not None and bool(folder.collaborate_share_id)


def can_user_access_folder(db: Session, user_id: str, folder_id: str) -> bool:
    folder = db.query(FileFolders).filter(FileFolders.id == folder_id).first()
    if not folder:
        return False
    if folder.user_id == user_id:
        return True
    subscription = db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == folder_id,
        SharedFileFolderSubscription.subscriber_id == user_id,
    ).first()
    if not subscription:
        return False
    if subscription.share_type == "live":
        return bool(folder.live_share_id)
    if subscription.share_type == "collaborate":
        return bool(folder.collaborate_share_id)
    return False


def get_shared_folder_files(db: Session, folder_id: str, subscriber_id: str) -> list:
    """Get files from a shared folder that a subscriber has access to."""
    folder = db.query(FileFolders).filter(FileFolders.id == folder_id).first()
    if not folder:
        return []
    if not can_user_access_folder(db, subscriber_id, folder_id):
        return []
    from app.files.access import accessible_folder_files_query
    return accessible_folder_files_query(db, subscriber_id, folder_id).all()
