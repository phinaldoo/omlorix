"""Skill catalog projection; bodies are fetched only by explicit reads."""

from sqlalchemy import func, or_
from app.skills.models import Skills, SharedSkillSubscription
from app.utils.helpers import datetime_to_iso
from app.utils.read_models import keyset_page, shared_access, text_pattern


def skill_access(user_id):
    return shared_access(
        Skills, SharedSkillSubscription, SharedSkillSubscription.skill_id, user_id
    )


def list_skill_summaries(db, user_id, *, query=None, limit=20, offset=0, cursor=None):
    access, share = skill_access(user_id)
    rows = db.query(
        Skills.id,
        Skills.name,
        Skills.icon,
        Skills.created_at,
        Skills.updated_at,
        share,
        func.substr(Skills.description, 1, 500).label("description"),
        func.length(Skills.description).label("description_length"),
        func.length(Skills.content).label("content_length"),
    ).filter(access)
    if query:
        pattern = text_pattern(query)
        rows = rows.filter(
            or_(
                Skills.name.ilike(pattern, escape="\\"),
                Skills.description.ilike(pattern, escape="\\"),
            )
        )
    items, page = keyset_page(
        rows,
        order=[(Skills.created_at, "created_at", True), (Skills.id, "id", True)],
        scope=["skills", user_id, query],
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    for item in items:
        for field in ("created_at", "updated_at"):
            item[field] = datetime_to_iso(item[field])
    return {"operation": "list", "skills": items, **page}


def list_skill_catalog(db, user_id, *, query=None, limit=50, offset=0, cursor=None):
    from sqlalchemy import case, literal, select, union_all
    from app.skills.models import (
        AdminSkills,
        ADMIN_SKILLS_USER_ID,
        _get_user_admin_skill_ids,
    )

    access, share = skill_access(user_id)

    def columns(model, managed=False):
        owner = literal(ADMIN_SKILLS_USER_ID) if managed else model.user_id
        return [
            model.id,
            owner.label("user_id"),
            model.name.label("title"),
            func.substr(model.description, 1, 500).label("description"),
            func.substr(model.content, 1, 500).label("content"),
            model.icon,
            model.created_at,
            model.updated_at,
            literal(1 if managed else 0).label("is_admin_skill"),
            literal(None).label("share_type") if managed else share,
            *(
                literal(None).label(field)
                if managed
                else case(
                    (model.user_id == user_id, getattr(model, field)), else_=None
                ).label(field)
                for field in ("clone_share_id", "live_share_id", "collaborate_share_id")
            ),
        ]

    owned = select(*columns(Skills)).where(access)
    managed = select(*columns(AdminSkills, True)).where(
        AdminSkills.id.in_(_get_user_admin_skill_ids(db, user_id))
    )
    if query:
        pattern = text_pattern(query)
        owned = owned.where(
            or_(
                Skills.name.ilike(pattern, escape="\\"),
                Skills.description.ilike(pattern, escape="\\"),
                Skills.content.ilike(pattern, escape="\\"),
            )
        )
        managed = managed.where(
            or_(
                AdminSkills.name.ilike(pattern, escape="\\"),
                AdminSkills.description.ilike(pattern, escape="\\"),
                AdminSkills.content.ilike(pattern, escape="\\"),
            )
        )
    catalog = union_all(owned, managed).subquery()
    items, page = keyset_page(
        db.query(*catalog.c),
        order=[
            (catalog.c.created_at, "created_at", True),
            (catalog.c.id, "id", True),
            (catalog.c.is_admin_skill, "is_admin_skill", False),
        ],
        scope=["skill_catalog", user_id, query],
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    for item in items:
        item["is_admin_skill"] = bool(item["is_admin_skill"])
        item["is_subscribed"] = bool(item["share_type"])
        item["summary_only"] = True
        for field in ("created_at", "updated_at"):
            item[field] = datetime_to_iso(item[field])
    return {"items": items, **page}
