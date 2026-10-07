from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from app.files.models import FileMember, Files


@dataclass(frozen=True)
class ResolvedFileAccess:
    """Keep the authenticated actor separate from the file's storage owner."""

    record: Files
    storage_owner_user_id: str


def accessible_files_query(db: Session, user_id: str):
    membership = (
        db.query(FileMember.file_id)
        .filter(
            FileMember.file_id == Files.id,
            FileMember.user_id == str(user_id),
            FileMember.role.in_(("viewer", "editor")),
        )
        .exists()
    )
    # A generated PDF follows its canonical source's current membership. The
    # reciprocal, same-owner link prevents arbitrary imported metadata from
    # granting access to another owner's files; revocation takes effect at once.
    source = aliased(Files)
    source_member = aliased(FileMember)
    derivative = (
        db.query(source.id)
        .join(source_member, source_member.file_id == source.id)
        .filter(
            source.id == Files.meta["latex_source_file_id"].as_string(),
            source.user_id == Files.user_id,
            source.meta["latex_pdf_file_id"].as_string() == Files.id,
            Files.file_type == "application/pdf",
            source_member.user_id == str(user_id),
            source_member.role.in_(("viewer", "editor")),
        )
        .exists()
    )
    return db.query(Files).filter(
        or_(Files.user_id == str(user_id), membership, derivative)
    )


def get_accessible_file(db: Session, user_id: str, file_id: str):
    return accessible_files_query(db, user_id).filter(Files.id == str(file_id)).first()


def resolve_file_for_read(
    db: Session, actor_user_id: str, file_id: str
) -> ResolvedFileAccess | None:
    record = get_accessible_file(db, actor_user_id, file_id)
    return ResolvedFileAccess(record, str(record.user_id)) if record else None


def resolve_file_for_edit(
    db: Session, actor_user_id: str, file_id: str
) -> ResolvedFileAccess | None:
    access = resolve_file_for_read(db, actor_user_id, file_id)
    if not access:
        return None
    if access.storage_owner_user_id == str(actor_user_id):
        return access
    member = (
        db.query(FileMember)
        .filter(
            FileMember.file_id == str(file_id), FileMember.user_id == str(actor_user_id)
        )
        .populate_existing()
        .first()
    )
    return access if member and member.role == "editor" else None


def require_owned_file(db: Session, user_id: str, file_id: str):
    from fastapi import HTTPException

    record = (
        db.query(Files)
        .filter(Files.id == file_id, Files.user_id == str(user_id))
        .with_for_update()
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    return record


def list_file_members(db: Session, file_id: str, *, limit: int, offset: int):
    from app.users.models import User

    rows = (
        db.query(FileMember, User.email)
        .join(User, User.id == FileMember.user_id)
        .filter(
            FileMember.file_id == file_id,
        )
        .order_by(FileMember.user_id)
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    return {
        "items": [
            {
                "user_id": member.user_id,
                "email": email,
                "role": member.role,
                "granted_at": member.granted_at,
            }
            for member, email in rows[:limit]
        ],
        "has_more": len(rows) > limit,
    }


def stage_file_member(db: Session, owner_id: str, file_id: str, email: str, role: str):
    """Called under the owning file lock; permission changes share its edit lock."""
    from datetime import datetime, timezone
    from fastapi import HTTPException
    from app.files.sharing import ensure_artifact_file_sharing_allowed_for_user
    from app.users.models import User, build_user_email_match

    ensure_artifact_file_sharing_allowed_for_user(owner_id, db)
    target = (
        db.query(User)
        .filter(build_user_email_match(email), User.is_active.is_(True))
        .first()
    )
    if not target or str(target.id) == owner_id:
        raise HTTPException(status_code=400, detail="file_member_unavailable")
    member = db.get(FileMember, (file_id, str(target.id)))
    if not member:
        if db.query(FileMember).filter(FileMember.file_id == file_id).count() >= 200:
            raise HTTPException(status_code=400, detail="file_member_limit")
        member = FileMember(
            file_id=file_id,
            user_id=str(target.id),
            granted_at=datetime.now(timezone.utc),
        )
    elif member.role != role:
        member.granted_at = datetime.now(timezone.utc)
    member.role = role
    db.add(member)
    return member, target
