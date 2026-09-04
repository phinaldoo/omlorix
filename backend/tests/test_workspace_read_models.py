"""Read-model integration: paging, payload bounds, and changing share access."""

from datetime import datetime, timezone
import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.database import Base
from app.notes.models import Notes, SharedNoteSubscription
from app.notes.queries import list_note_summaries
from app.skills.models import Skills, AdminSkills, SharedSkillSubscription
from app.skills.queries import list_skill_catalog, skill_access
from app.todos.models import TodoLists, Todos, SharedTodoListSubscription
from app.todos.queries import list_todo_summaries
from app.automations.models import Automation
from app.automations.queries import list_automation_summaries


@pytest.fixture
def db(monkeypatch):
    url = os.getenv("WORKSPACE_TEST_DATABASE_URL")
    root_engine = create_engine(url or "sqlite:///:memory:")
    schema = "read_test_" + uuid.uuid4().hex if url else None
    if schema:
        with root_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = root_engine.execution_options(
            schema_translate_map={None: schema, Base.metadata.schema: schema}
        )
    else:
        engine = root_engine
    Base.metadata.create_all(
        engine,
        tables=[
            model.__table__
            for model in (
                Notes,
                SharedNoteSubscription,
                Skills,
                AdminSkills,
                SharedSkillSubscription,
                TodoLists,
                Todos,
                SharedTodoListSubscription,
                Automation,
            )
        ],
    )
    monkeypatch.setattr(
        "app.skills.models._get_user_admin_skill_ids", lambda *_: ["managed"]
    )
    try:
        with Session(engine) as session:
            yield session
    finally:
        if schema:
            with root_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root_engine.dispose()


def test_note_page_is_bounded_unique_and_rechecks_revocation(db):
    now = datetime.now(timezone.utc)
    for key in ["a", "b", "c", "d"]:
        db.add(
            Notes(
                id=key,
                user_id="owner" if key == "b" else "viewer",
                content="# Title\n" + "body" * 10000,
                live_share_id="token" if key == "b" else None,
                updated_at=now,
            )
        )
    for i in range(2):
        db.add(
            SharedNoteSubscription(
                id=str(i), note_id="b", subscriber_id="viewer", share_type="live"
            )
        )
    db.commit()
    statements = []
    event.listen(
        db.get_bind(),
        "before_cursor_execute",
        lambda _c, _cu, statement, *_: statements.append(statement),
    )
    first = list_note_summaries(db, "viewer", limit=2)
    assert [item["id"] for item in first["notes"]] == ["d", "c"]
    assert len(json.dumps(first)) < 2000
    assert "notes.content AS notes_content" not in statements[-1]
    assert "LIMIT" in statements[-1]
    second = list_note_summaries(db, "viewer", limit=2, cursor=first["next_cursor"])
    assert [item["id"] for item in second["notes"]] == ["b", "a"]
    assert not second["has_more"]
    db.get(Notes, "b").live_share_id = None
    db.commit()
    second = list_note_summaries(db, "viewer", limit=2, cursor=first["next_cursor"])
    assert [item["id"] for item in second["notes"]] == ["a"]
    for kwargs in ({"query": "different"}, {"offset": 1}):
        with pytest.raises(ValueError):
            list_note_summaries(
                db, "viewer", limit=2, cursor=first["next_cursor"], **kwargs
            )
    with pytest.raises(ValueError):
        list_note_summaries(db, "another", cursor=first["next_cursor"])


def test_skill_catalog_unifies_managed_shared_and_owned_without_bodies(db):
    fields = dict(
        name="Example",
        description="description",
        content="long" * 10000,
        icon="skill",
        created_at=datetime.now(timezone.utc),
    )
    db.add_all(
        [
            Skills(id="own", user_id="viewer", **fields),
            Skills(
                id="shared", user_id="owner", collaborate_share_id="token", **fields
            ),
            AdminSkills(id="managed", **fields),
            AdminSkills(id="unassigned", **fields),
        ]
    )
    db.add(
        SharedSkillSubscription(
            id="sub",
            skill_id="shared",
            subscriber_id="viewer",
            share_type="collaborate",
        )
    )
    db.commit()
    items, cursor = [], None
    while True:
        page = list_skill_catalog(db, "viewer", limit=1, cursor=cursor)
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert {item["id"] for item in items} == {"own", "shared", "managed"}
    assert all(item["summary_only"] and len(item["content"]) == 500 for item in items)
    shared = next(item for item in items if item["id"] == "shared")
    assert shared["share_type"] == "collaborate"
    assert shared["collaborate_share_id"] is None
    db.get(Skills, "shared").collaborate_share_id = None
    db.commit()
    access, _ = skill_access("viewer")
    assert db.query(Skills.id).filter(access, Skills.id == "shared").first() is None
    assert {item["id"] for item in list_skill_catalog(db, "viewer")["items"]} == {
        "own",
        "managed",
    }


def test_task_and_automation_summaries_count_json_without_loading_arrays(db):
    db.add(TodoLists(id="list", user_id="viewer", title="Tasks", icon="tasks"))
    db.add_all(
        [
            Todos(
                id="task-" + str(i),
                todo_list="list",
                content="task",
                notes="private body" * 1000,
                due_at=None,
                subtasks=[{"content": "subtask"}],
                attachments=[],
            )
            for i in range(3)
        ]
    )
    db.add(
        Automation(
            id="automation",
            user_id="viewer",
            title="Scheduled",
            model_id="model",
            prompt="large" * 10000,
            note_ids=None,
            file_ids=["file"],
        )
    )
    db.commit()
    first = list_todo_summaries(db, "viewer", limit=2)
    second = list_todo_summaries(db, "viewer", limit=2, cursor=first["next_cursor"])
    tasks = first["todos"] + second["todos"]
    assert len({item["id"] for item in tasks}) == 3
    assert all(
        item["subtask_count"] == 1 and item["has_notes"] and "notes" not in item
        for item in tasks
    )
    automation = list_automation_summaries(db, "viewer")["automations"][0]
    assert automation["prompt_length"] == 50000
    assert automation["note_count"] == 0 and automation["file_count"] == 1
    assert "prompt" not in automation and "file_ids" not in automation
