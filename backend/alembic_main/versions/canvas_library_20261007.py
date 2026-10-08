"""Replace workspace folders, notes and todos with a flat Canvas library.

Content conversion is database-only and transactional, so it also works when
application file storage is remote. Inline migrated sources move to ordinary
file storage on their next edit. No old public share token is republished.
"""

from datetime import datetime, timezone
import json
import re
import uuid

from alembic import op
import sqlalchemy as sa
from app.database import DATABASE_SCHEMA

revision = "canvas_library_20261007"
down_revision = "visualization_state_20261007"
branch_labels = None
depends_on = None


def _canvas_id(kind, owner, old_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"omlorix/{kind}/{owner}/{old_id}"))


def _pages(bind, table, size=250):
    last = None
    while True:
        query = sa.select(table).order_by(table.c.id).limit(size)
        if last is not None:
            query = query.where(table.c.id > last)
        rows = bind.execute(query).mappings().all()
        if not rows:
            return
        yield from rows
        last = rows[-1]["id"]


def upgrade():
    bind = op.get_bind()
    schema = str(op.get_context().version_table_schema or DATABASE_SCHEMA)
    metadata = sa.MetaData(schema=schema)
    tables = {
        name: sa.Table(name, metadata, autoload_with=bind)
        for name in (
            "files",
            "users",
            "notes",
            "note_history",
            "shared_note_subscriptions",
            "todo_lists",
            "todos",
            "shared_todo_list_subscriptions",
            "file_folders",
            "shared_file_folder_subscriptions",
            "automations",
        )
    }
    op.add_column(
        "files", sa.Column("inline_content", sa.Text(), nullable=True), schema=schema
    )
    op.create_table(
        "file_members",
        sa.Column(
            "file_id",
            sa.String(),
            sa.ForeignKey(f"{schema}.files.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey(f"{schema}.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    op.create_index(
        "ix_file_members_user_file",
        "file_members",
        ["user_id", "file_id"],
        schema=schema,
    )
    op.create_table(
        "canvas_history",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "file_id",
            sa.String(),
            sa.ForeignKey(f"{schema}.files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.String(),
            sa.ForeignKey(f"{schema}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("edit_source", sa.String(24), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("file_type", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(24), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("storage_provider", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("storage_meta", sa.JSON(), nullable=True),
        sa.Column("inline_content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("file_id", "revision", name="uq_canvas_history_revision"),
        schema=schema,
    )
    op.create_index(
        "ix_canvas_history_file_revision",
        "canvas_history",
        ["file_id", "revision"],
        schema=schema,
    )
    op.create_index(
        "ix_canvas_history_owner", "canvas_history", ["owner_id"], schema=schema
    )
    files = sa.Table("files", sa.MetaData(schema=schema), autoload_with=bind)
    members = sa.Table("file_members", sa.MetaData(schema=schema), autoload_with=bind)
    history = sa.Table("canvas_history", sa.MetaData(schema=schema), autoload_with=bind)

    def grant(file_id, owner_id, recipient, role, created):
        if owner_id == recipient:
            return
        # Removed accounts cannot receive access; never substitute another ID.
        if not bind.scalar(
            sa.select(tables["users"].c.id).where(tables["users"].c.id == recipient)
        ):
            return
        match = sa.and_(members.c.file_id == file_id, members.c.user_id == recipient)
        existing = bind.execute(sa.select(members).where(match)).mappings().first()
        if existing:
            if role == "editor" and existing["role"] != "editor":
                bind.execute(
                    members.update().where(match).values(role=role, granted_at=created)
                )
        else:
            bind.execute(
                members.insert().values(
                    file_id=file_id, user_id=recipient, role=role, granted_at=created
                )
            )

    def convert(row, kind, content, title, revisions=0):
        file_id = _canvas_id(kind, row["user_id"], row["id"])
        # Existing user content never gets overwritten by a migration collision.
        if bind.scalar(sa.select(files.c.id).where(files.c.id == file_id)):
            raise RuntimeError("Canvas migration file identity collision")
        filename = (
            str(title or row["id"]).replace("/", "-").replace("\\", "-")[:180]
            or row["id"]
        ) + ".md"
        created = row.get("created_at") or datetime.now(timezone.utc)
        updated = row.get("updated_at") or created
        bind.execute(
            files.insert().values(
                id=file_id,
                user_id=row["user_id"],
                file_name=file_id + ".md",
                storage_provider="inline",
                storage_key=f"{row['user_id']}/{file_id}.md",
                storage_meta={},
                file_category="document",
                file_type="text/markdown",
                file_size=len(content.encode("utf-8")),
                inline_content=content,
                meta={
                    "canvas": True,
                    "canvas_type": "markdown",
                    "canvas_revision": revisions + 1,
                    "original_filename": filename,
                    f"legacy_{kind}_id": row["id"],
                    "origin": "user",
                },
                created_at=created,
                last_updated_at=updated,
            )
        )
        return file_id, filename

    def subscriptions(row, table_name, reference, file_id):
        table = tables[table_name]
        for subscription in bind.execute(
            sa.select(table).where(table.c[reference] == row["id"])
        ).mappings():
            mode = subscription["share_type"]
            if mode in ("live", "collaborate") and row.get(mode + "_share_id"):
                grant(
                    file_id,
                    row["user_id"],
                    subscription["subscriber_id"],
                    "editor" if mode == "collaborate" else "viewer",
                    subscription["subscribed_at"],
                )

    # Convert folder access in one set-based insert, not one query per file.
    folders, subs = tables["file_folders"], tables["shared_file_folder_subscriptions"]
    owner_grants = (
        sa.select(
            files.c.id.label("file_id"),
            folders.c.user_id.label("user_id"),
            sa.literal("editor").label("role"),
            folders.c.created_at.label("granted_at"),
        )
        .select_from(
            files.join(folders, files.c.folder_id == folders.c.id).join(
                tables["users"], tables["users"].c.id == folders.c.user_id
            )
        )
        .where(files.c.user_id != folders.c.user_id)
    )
    subscriber_grants = (
        sa.select(
            files.c.id.label("file_id"),
            subs.c.subscriber_id.label("user_id"),
            sa.case(
                (subs.c.share_type == "collaborate", "editor"), else_="viewer"
            ).label("role"),
            subs.c.subscribed_at.label("granted_at"),
        )
        .select_from(
            files.join(folders, files.c.folder_id == folders.c.id)
            .join(subs, subs.c.folder_id == folders.c.id)
            .join(tables["users"], tables["users"].c.id == subs.c.subscriber_id)
        )
        .where(
            files.c.user_id != subs.c.subscriber_id,
            sa.or_(
                sa.and_(
                    subs.c.share_type == "collaborate",
                    folders.c.collaborate_share_id.isnot(None),
                    folders.c.collaborate_share_id != "",
                ),
                sa.and_(
                    subs.c.share_type == "live",
                    folders.c.live_share_id.isnot(None),
                    folders.c.live_share_id != "",
                    files.c.user_id == folders.c.user_id,
                ),
            ),
        )
    )
    grants = sa.union_all(owner_grants, subscriber_grants).subquery()
    editor_since = sa.func.min(
        sa.case((grants.c.role == "editor", grants.c.granted_at), else_=None)
    )
    bind.execute(
        members.insert().from_select(
            ["file_id", "user_id", "role", "granted_at"],
            sa.select(
                grants.c.file_id,
                grants.c.user_id,
                sa.case((editor_since.isnot(None), "editor"), else_="viewer"),
                sa.func.coalesce(editor_since, sa.func.min(grants.c.granted_at)),
            ).group_by(grants.c.file_id, grants.c.user_id),
        )
    )

    for note in _pages(bind, tables["notes"]):
        title = next(
            (
                line.strip().lstrip("# ")
                for line in note["content"].splitlines()
                if line.strip()
            ),
            note["id"],
        )
        old_history = tables["note_history"]
        count = bind.scalar(
            sa.select(sa.func.count())
            .select_from(old_history)
            .where(old_history.c.note_id == note["id"])
        )
        file_id, filename = convert(
            note, "note", _canvas_links(note["content"]), title, count + 1
        )
        versions = bind.execute(
            sa.select(old_history)
            .where(old_history.c.note_id == note["id"])
            .order_by(old_history.c.created_at, old_history.c.id)
        ).mappings()
        for index, version in enumerate(versions, start=1):
            if index == 1 and version["previous_content"] is not None:
                bind.execute(
                    history.insert().values(
                        id=str(uuid.uuid4()),
                        file_id=file_id,
                        owner_id=note["user_id"],
                        revision=0,
                        actor_id=note["user_id"],
                        edit_source="user",
                        file_name=filename,
                        file_type="text/markdown",
                        content_type="markdown",
                        file_size=len(
                            _canvas_links(version["previous_content"]).encode("utf-8")
                        ),
                        storage_provider="inline",
                        storage_key=f"{note['user_id']}/{file_id}.md",
                        inline_content=_canvas_links(version["previous_content"]),
                        created_at=note["created_at"],
                    )
                )
            bind.execute(
                history.insert().values(
                    id=str(uuid.uuid4()),
                    file_id=file_id,
                    owner_id=note["user_id"],
                    revision=index,
                    actor_id=version["user_id"],
                    edit_source=version["actor_type"] or "user",
                    file_name=filename,
                    file_type="text/markdown",
                    content_type="markdown",
                    file_size=len(_canvas_links(version["content"]).encode("utf-8")),
                    storage_provider="inline",
                    storage_key=f"{note['user_id']}/{file_id}.md",
                    inline_content=_canvas_links(version["content"]),
                    created_at=version["created_at"],
                )
            )
        subscriptions(note, "shared_note_subscriptions", "note_id", file_id)

    for listing in _pages(bind, tables["todo_lists"]):
        parts = ["# " + listing["title"], listing["description"] or ""]
        tasks = tables["todos"]
        for item in bind.execute(
            sa.select(tasks)
            .where(tasks.c.todo_list == listing["id"])
            .order_by(tasks.c.order, tasks.c.created_at, tasks.c.id)
        ).mappings():
            parts.append(f"- [{'x' if item['is_done'] else ' '}] {item['content']}")
            if item["notes"]:
                parts.append(item["notes"])
            details = {
                key: item[key]
                for key in (
                    "priority",
                    "due_at",
                    "all_day",
                    "status",
                    "subtasks",
                    "links",
                    "attachments",
                    "tags",
                    "is_marked",
                    "completed_at",
                )
                if item[key]
            }
            if details:
                parts.append(
                    "```json\n"
                    + json.dumps(details, ensure_ascii=False, default=str, indent=2)
                    + "\n```"
                )
        file_id, _ = convert(listing, "todo_list", "\n\n".join(parts), listing["title"])
        subscriptions(
            listing, "shared_todo_list_subscriptions", "todo_list_id", file_id
        )

    # Notes attached to scheduled prompts become ordinary Canvas file attachments.
    automations = tables["automations"]
    for automation in _pages(bind, automations):
        file_ids = list(automation["file_ids"] or [])
        for note_id in automation["note_ids"] or []:
            note = (
                bind.execute(
                    sa.select(tables["notes"]).where(tables["notes"].c.id == note_id)
                )
                .mappings()
                .first()
            )
            if note:
                file_ids.append(_canvas_id("note", note["user_id"], note_id))
        bind.execute(
            automations.update()
            .where(automations.c.id == automation["id"])
            .values(file_ids=list(dict.fromkeys(file_ids)))
        )
    op.drop_column("automations", "note_ids", schema=schema)
    op.drop_column("files", "folder_id", schema=schema)
    for name in (
        "shared_note_subscriptions",
        "note_history",
        "notes",
        "shared_todo_list_subscriptions",
        "todos",
        "todo_lists",
        "shared_file_folder_subscriptions",
        "file_folders",
    ):
        op.drop_table(name, schema=schema)


def downgrade():
    raise RuntimeError(
        "Restore a pre-migration backup to recover the retired workspace features; Canvas edits cannot be losslessly split back into notes and todos."
    )


def _canvas_links(content):
    def replace(match):
        label = (
            (match.group(4) or match.group(3))
            .replace("\\", "\\\\")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )
        prefix = "!" if match.group(1) == "image" else ""
        return f"{prefix}[{label}](omlorix-file://{match.group(3)})"

    return re.sub(
        r"\{\{note:(image|audio|file):([^:|}]+):([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})(?:\|([^}]*?))?\}\}",
        replace,
        str(content or ""),
    )
