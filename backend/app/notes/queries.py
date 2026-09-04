"""Lightweight, access-filtered note reads without loading document bodies."""

from sqlalchemy import func, case, select

from app.notes.models import Notes, SharedNoteSubscription
from app.utils.helpers import datetime_to_iso
from app.utils.read_models import keyset_page, shared_access, text_pattern


def note_access(user_id):
    return shared_access(
        Notes, SharedNoteSubscription, SharedNoteSubscription.note_id, user_id
    )


def list_note_summaries(
    db, user_id, *, query=None, limit=20, offset=0, cursor=None, management=False
):
    access, share = note_access(user_id)
    extra = []
    if management:
        extra = [
            case((Notes.user_id == user_id, getattr(Notes, field)), else_=None).label(
                field
            )
            for field in ("clone_share_id", "live_share_id", "collaborate_share_id")
        ]
        extra.append(
            select(func.count(SharedNoteSubscription.id))
            .where(SharedNoteSubscription.note_id == Notes.id)
            .correlate(Notes)
            .scalar_subquery()
            .label("subscriber_count")
        )
    rows = db.query(
        Notes.id,
        Notes.user_id,
        Notes.created_at,
        Notes.updated_at,
        share,
        func.substr(Notes.content, 1, 1024).label("preview"),
        func.length(Notes.content).label("content_length"),
        *extra,
    ).filter(access)
    if query:
        rows = rows.filter(Notes.content.ilike(text_pattern(query), escape="\\"))
    items, page = keyset_page(
        rows,
        order=[(Notes.updated_at, "updated_at", True), (Notes.id, "id", True)],
        scope=["notes", user_id, query],
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    owners = {}
    if management:
        from app.users.models import User

        owner_ids = {item["user_id"] for item in items if item["user_id"] != user_id}
        if owner_ids:
            owners = {
                row.id: " ".join(
                    part for part in (row.first_name, row.last_name) if part
                )
                or row.email
                for row in db.query(
                    User.id, User.first_name, User.last_name, User.email
                )
                .filter(User.id.in_(owner_ids))
                .all()
            }
    for item in items:
        lines = [
            line.strip().lstrip("#").strip()
            for line in item.pop("preview").splitlines()
            if line.strip()
        ]
        item["title"] = lines[0][:80] if lines else ""
        item["snippet"] = " ".join(lines[1:])[:240]
        owner_id = item.pop("user_id")
        item["is_subscribed"] = owner_id != user_id
        if management:
            item["user_id"] = owner_id if not item["is_subscribed"] else None
            item["owner_name"] = owners.get(owner_id)
        item["can_edit"] = (
            not item["is_subscribed"] or item["share_type"] == "collaborate"
        )
        for field in ("created_at", "updated_at"):
            item[field] = datetime_to_iso(item[field])
    return {"notes": items, **page}
