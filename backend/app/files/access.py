from dataclasses import dataclass

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.file_folders.models import (
    FileFolders,
    SharedFileFolderSubscription,
    can_user_edit_folder,
)
from app.files.models import Files


@dataclass(frozen=True)
class ResolvedFileAccess:
    """A file authorized for one action plus its real storage-owner context.

    File authorization and storage materialization deliberately use different
    identities.  The actor proves that the operation is allowed, while the
    owning user ID is used only to locate the already-authorized storage object.
    Keeping both values in one immutable result prevents callers from silently
    replacing the actor with the owner, which caused the LaTeX Canvas IDOR.
    """

    record: Files
    storage_owner_user_id: str


def valid_shared_folder_subscription_filter():
    return or_(
        and_(
            SharedFileFolderSubscription.share_type == "live",
            FileFolders.live_share_id.isnot(None),
            FileFolders.live_share_id != "",
        ),
        and_(
            SharedFileFolderSubscription.share_type == "collaborate",
            FileFolders.collaborate_share_id.isnot(None),
            FileFolders.collaborate_share_id != "",
        ),
    )


def accessible_files_query(db: Session, user_id: str):
    own_folder_ids = db.query(FileFolders.id).filter(FileFolders.user_id == user_id)
    subscribed_file_exists = (
        db.query(FileFolders.id)
        .join(SharedFileFolderSubscription, SharedFileFolderSubscription.folder_id == FileFolders.id)
        .filter(
            FileFolders.id == Files.folder_id,
            SharedFileFolderSubscription.subscriber_id == user_id,
            valid_shared_folder_subscription_filter(),
            or_(
                FileFolders.user_id == Files.user_id,
                SharedFileFolderSubscription.share_type == "collaborate",
            ),
        )
        .exists()
    )
    return db.query(Files).filter(
        or_(
            Files.user_id == user_id,
            and_(
                Files.user_id != user_id,
                Files.folder_id.isnot(None),
                Files.folder_id.in_(own_folder_ids),
            ),
            and_(
                Files.user_id != user_id,
                Files.folder_id.isnot(None),
                subscribed_file_exists,
            ),
        )
    )


def accessible_folder_files_query(db: Session, user_id: str, folder_id: str):
    return accessible_files_query(db, user_id).filter(Files.folder_id == folder_id)


def get_accessible_file(db: Session, user_id: str, file_id: str):
    return accessible_files_query(db, user_id).filter(Files.id == file_id).first()


def resolve_file_for_read(
    db: Session, actor_user_id: str, file_id: str
) -> ResolvedFileAccess | None:
    """Resolve a file readable by the authenticated actor.

    A file ID is only an identifier.  It never grants access by itself.  The
    returned owner identity is storage metadata and must not be reused as the
    authorization principal for another file.
    """

    record = get_accessible_file(db, str(actor_user_id), str(file_id))
    if not record:
        return None
    return ResolvedFileAccess(
        record=record,
        storage_owner_user_id=str(record.user_id),
    )


def resolve_file_for_edit(
    db: Session, actor_user_id: str, file_id: str
) -> ResolvedFileAccess | None:
    """Resolve a file the actor may edit without changing its owner.

    Owners can always edit their own files.  A non-owner must have explicit
    collaborative edit access to the file's current folder; view-only folder
    subscriptions are intentionally insufficient.
    """

    normalized_actor_id = str(actor_user_id or "").strip()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_actor_id or not normalized_file_id:
        return None

    record = (
        db.query(Files)
        .filter(Files.id == normalized_file_id)
        .first()
    )
    if not record:
        return None
    if str(record.user_id) == normalized_actor_id:
        return ResolvedFileAccess(record, str(record.user_id))
    if not record.folder_id or not can_user_edit_folder(
        db, normalized_actor_id, str(record.folder_id)
    ):
        return None
    return ResolvedFileAccess(record, str(record.user_id))


def count_accessible_folder_files(db: Session, user_id: str, folder_id: str) -> int:
    return accessible_folder_files_query(db, user_id, folder_id).count()


def shared_folder_preview_files_query(db: Session, folder_id: str, share_type: str):
    query = db.query(Files).filter(Files.folder_id == folder_id)
    if share_type == "collaborate":
        return query
    return query.join(FileFolders, FileFolders.id == Files.folder_id).filter(Files.user_id == FileFolders.user_id)


def count_shared_folder_preview_files(db: Session, folder_id: str, share_type: str) -> int:
    return shared_folder_preview_files_query(db, folder_id, share_type).count()
