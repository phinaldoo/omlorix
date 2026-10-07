"""Read-model integration: paging, payload bounds, and changing share access."""

from datetime import datetime, timezone
import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.database import Base
from app.skills.models import Skills, AdminSkills, SharedSkillSubscription
from app.skills.queries import list_skill_catalog, skill_access
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
                Skills,
                AdminSkills,
                SharedSkillSubscription,
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
    db.add(
        Automation(
            id="automation",
            user_id="viewer",
            title="Scheduled",
            model_id="model",
            prompt="large" * 10000,
            file_ids=["file"],
        )
    )
    db.commit()
    automation = list_automation_summaries(db, "viewer")["automations"][0]
    assert automation["prompt_length"] == 50000
    assert automation["file_count"] == 1
    assert "prompt" not in automation and "file_ids" not in automation
