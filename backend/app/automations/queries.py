"""Automation catalog metadata without prompts or attachment arrays."""

from sqlalchemy import func
from app.automations.models import Automation as A
from app.utils.helpers import datetime_to_iso
from app.utils.read_models import json_array_size, keyset_page


def list_automation_summaries(db, user_id, *, limit=20, offset=0, cursor=None):
    fields = (
        "id",
        "title",
        "icon",
        "icon_color",
        "model_id",
        "schedule_timezone",
        "skill_id",
        "is_active",
        "last_triggered_at",
        "created_at",
        "last_updated_at",
    )
    rows = db.query(
        *(getattr(A, name) for name in fields),
        func.length(A.prompt).label("prompt_length"),
        *(
            func.coalesce(json_array_size(getattr(A, field)), 0).label(name)
            for field, name in (
                ("schedule_rules", "schedule_rule_count"),
                ("note_ids", "note_count"),
                ("file_ids", "file_count"),
                ("mcp_server_ids", "mcp_server_count"),
            )
        ),
    ).filter(A.user_id == user_id)
    items, page = keyset_page(
        rows,
        order=[(A.created_at, "created_at", True), (A.id, "id", True)],
        scope=["automations", user_id],
        limit=limit,
        offset=offset,
        cursor=cursor,
    )
    for item in items:
        for field in ("created_at", "last_updated_at", "last_triggered_at"):
            item[field] = datetime_to_iso(item[field])
    return {"operation": "list", "automations": items, **page}
