from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_canvas_migration_preserves_sources_history_tasks_and_exact_access(monkeypatch):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_main/versions/canvas_library_20261007.py"
    )
    spec = spec_from_file_location("canvas_library_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData(schema="main")

    def table(name, strings=(), dates=(), jsons=(), integers=(), booleans=()):
        return sa.Table(
            name,
            metadata,
            sa.Column("id", sa.String, primary_key=True),
            *(sa.Column(key, sa.String) for key in strings),
            *(sa.Column(key, sa.DateTime) for key in dates),
            *(sa.Column(key, sa.JSON) for key in jsons),
            *(sa.Column(key, sa.Integer) for key in integers),
            *(sa.Column(key, sa.Boolean) for key in booleans),
        )

    users = table("users")
    files = table(
        "files",
        [
            "user_id",
            "file_name",
            "storage_provider",
            "storage_key",
            "file_category",
            "file_type",
            "folder_id",
        ],
        ["created_at", "last_updated_at"],
        ["storage_meta", "meta"],
        ["file_size"],
    )
    notes = table(
        "notes",
        ["user_id", "content", "live_share_id", "collaborate_share_id"],
        ["created_at", "updated_at"],
    )
    history = table(
        "note_history",
        ["note_id", "user_id", "actor_type", "content", "previous_content"],
        ["created_at"],
    )
    note_members = table(
        "shared_note_subscriptions",
        ["note_id", "subscriber_id", "share_type"],
        ["subscribed_at"],
    )
    lists = table(
        "todo_lists",
        ["user_id", "title", "description", "live_share_id", "collaborate_share_id"],
        ["created_at", "updated_at"],
    )
    tasks = table(
        "todos",
        ["todo_list", "content", "notes", "status"],
        ["created_at", "due_at", "completed_at"],
        ["subtasks", "links", "attachments", "tags"],
        ["order", "priority"],
        ["all_day", "is_done", "is_marked"],
    )
    table(
        "shared_todo_list_subscriptions",
        ["todo_list_id", "subscriber_id", "share_type"],
        ["subscribed_at"],
    )
    folders = table(
        "file_folders",
        ["user_id", "live_share_id", "collaborate_share_id"],
        ["created_at"],
    )
    folder_members = table(
        "shared_file_folder_subscriptions",
        ["folder_id", "subscriber_id", "share_type"],
        ["subscribed_at"],
    )
    automations = table("automations", jsons=["file_ids", "note_ids"])
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            users.insert(),
            [{"id": x} for x in ["owner", "contributor", "viewer", "editor"]],
        )
        connection.execute(
            folders.insert(),
            dict(
                id="folder",
                user_id="owner",
                live_share_id="live",
                collaborate_share_id="edit",
                created_at=now,
            ),
        )
        connection.execute(
            folder_members.insert(),
            dict(
                id="subscription",
                folder_id="folder",
                subscriber_id="viewer",
                share_type="live",
                subscribed_at=now,
            ),
        )
        for file_id, owner in [("owned", "owner"), ("contribution", "contributor")]:
            connection.execute(
                files.insert(),
                dict(
                    id=file_id,
                    user_id=owner,
                    file_name=file_id + ".pdf",
                    file_type="application/pdf",
                    file_size=10,
                    folder_id="folder",
                    storage_provider="local",
                    storage_key=f"{owner}/{file_id}.pdf",
                    created_at=now,
                    last_updated_at=now,
                ),
            )
        connection.execute(
            notes.insert(),
            dict(
                id="note",
                user_id="owner",
                content="# Research\n{{note:file:owner:owned|Report}}",
                created_at=now,
                updated_at=now,
                collaborate_share_id="note-share",
            ),
        )
        connection.execute(
            history.insert(),
            dict(
                id="version",
                note_id="note",
                user_id="owner",
                actor_type="assistant",
                content="# Research",
                previous_content="# Outline",
                created_at=now,
            ),
        )
        connection.execute(
            note_members.insert(),
            dict(
                id="note-member",
                note_id="note",
                subscriber_id="editor",
                share_type="collaborate",
                subscribed_at=now,
            ),
        )
        connection.execute(
            lists.insert(),
            dict(
                id="tasks",
                user_id="owner",
                title="Release",
                description="Checklist",
                created_at=now,
                updated_at=now,
            ),
        )
        connection.execute(
            tasks.insert(),
            dict(
                id="task",
                todo_list="tasks",
                content="Ship",
                notes="Keep the backup",
                order=0,
                is_done=True,
                is_marked=True,
                priority=2,
                status="done",
                subtasks=[{"content": "Review", "is_done": True}],
                created_at=now,
            ),
        )
        connection.execute(
            automations.insert(),
            dict(id="automation", file_ids=["owned"], note_ids=["note"]),
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(
                MigrationContext.configure(
                    connection, opts={"version_table_schema": "main"}
                )
            ),
        )
        migration.upgrade()
        reflected = sa.MetaData(schema="main")
        reflected.reflect(connection)
        migrated_files = reflected.tables["main.files"]
        note_id = migration._canvas_id("note", "owner", "note")
        note = (
            connection.execute(
                sa.select(migrated_files).where(migrated_files.c.id == note_id)
            )
            .mappings()
            .one()
        )
        assert note["inline_content"] == "# Research\n[Report](omlorix-file://owned)"
        versions = (
            connection.execute(
                sa.select(reflected.tables["main.canvas_history"]).order_by("revision")
            )
            .mappings()
            .all()
        )
        assert [row["inline_content"] for row in versions] == [
            "# Outline",
            "# Research",
        ]
        task_id = migration._canvas_id("todo_list", "owner", "tasks")
        task_source = connection.scalar(
            sa.select(migrated_files.c.inline_content).where(
                migrated_files.c.id == task_id
            )
        )
        assert (
            "- [x] Ship" in task_source
            and "Keep the backup" in task_source
            and '"priority": 2' in task_source
        )
        members = reflected.tables["main.file_members"]
        assert set(
            connection.execute(
                sa.select(members.c.file_id, members.c.user_id, members.c.role)
            )
        ) == {
            ("owned", "viewer", "viewer"),
            ("contribution", "owner", "editor"),
            (note_id, "editor", "editor"),
        }
        assert connection.scalar(
            sa.select(reflected.tables["main.automations"].c.file_ids)
        ) == ["owned", note_id]
        assert "folder_id" not in migrated_files.c
        assert not {
            "notes",
            "note_history",
            "todo_lists",
            "todos",
            "file_folders",
        }.intersection(sa.inspect(connection).get_table_names())
