from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.skills import router as skills_router
from app.skills.models import AdminSkills, paginate_admin_skills
from app.skills.schemas import SkillFilesResponse


def _session():
    """Create the smallest database needed by the managed-skill list tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[AdminSkills.__table__])
    return sessionmaker(bind=engine)()


def _admin_skill(
    skill_id: str,
    *,
    name: str,
    description: str = "",
    content: str = "",
    created_offset: int = 0,
) -> AdminSkills:
    """Build a deterministic managed-skill row for pagination assertions."""
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=created_offset)
    return AdminSkills(
        id=skill_id,
        icon="sparkles",
        name=name,
        description=description,
        content=content,
        created_at=created_at,
        updated_at=created_at,
    )


def test_paginate_admin_skills_filters_before_paging_and_clamps_stale_pages():
    """Search, ordering, page bounds, and literal LIKE characters stay predictable."""
    db = _session()
    db.add_all(
        [
            _admin_skill("one", name="alpha", content="A" * 700, created_offset=1),
            _admin_skill("two", name="beta", description="Podcast helper", created_offset=2),
            _admin_skill("three", name="gamma", content="PODCAST workflow", created_offset=3),
            _admin_skill("four", name="100% reliable", created_offset=4),
            _admin_skill("five", name="100x reliable", created_offset=5),
        ]
    )
    db.commit()

    rows, total, total_pages, resolved_page = paginate_admin_skills(
        db,
        page=1,
        page_size=1,
        search="podcast",
    )
    assert [row.id for row in rows] == ["three"]
    assert (total, total_pages, resolved_page) == (2, 2, 1)

    literal_rows, literal_total, _, _ = paginate_admin_skills(
        db,
        page=1,
        page_size=10,
        search="100%",
    )
    assert [row.id for row in literal_rows] == ["four"]
    assert literal_total == 1

    clamped_rows, total, total_pages, resolved_page = paginate_admin_skills(
        db,
        page=99,
        page_size=2,
    )
    assert [row.id for row in clamped_rows] == ["one"]
    assert (total, total_pages, resolved_page) == (5, 3, 3)
    assert len(clamped_rows[0].content_preview) == 500


@pytest.mark.anyio
async def test_admin_skill_list_endpoint_returns_summaries_without_file_hydration(monkeypatch):
    """The list endpoint must not read Markdown files or scan bundled folders."""
    db = _session()
    db.add(_admin_skill("skill-id", name="summary-only", content="List preview"))
    db.commit()

    def fail_if_hydrated(*_args, **_kwargs):
        raise AssertionError("paginated list unexpectedly hydrated filesystem data")

    monkeypatch.setattr(skills_router, "load_skill_markdown_fields", fail_if_hydrated)
    monkeypatch.setattr(skills_router, "_build_files_response", fail_if_hydrated)

    response = await skills_router.get_admin_skills_list(
        request=SimpleNamespace(),
        page=1,
        page_size=10,
        search=None,
        admin=SimpleNamespace(id="admin-user"),
        db=db,
    )

    assert response.total == 1
    assert response.total_pages == 1
    assert response.items[0].id == "skill-id"
    assert response.items[0].content_preview == "List preview"


@pytest.mark.anyio
async def test_admin_skill_detail_endpoint_hydrates_only_requested_skill(monkeypatch):
    """Opening Edit returns complete metadata and file information for one skill."""
    db = _session()
    db.add(_admin_skill("skill-id", name="full-detail", description="Database description"))
    db.commit()

    markdown_calls = []
    file_calls = []

    def markdown_fields(user_id, skill_id):
        markdown_calls.append((user_id, skill_id))
        return {"description": "Markdown description", "compatibility": "Omlorix"}

    def files_response(user_id, skill_id):
        file_calls.append((user_id, skill_id))
        return SkillFilesResponse()

    monkeypatch.setattr(skills_router, "load_skill_markdown_fields", markdown_fields)
    monkeypatch.setattr(skills_router, "_build_files_response", files_response)

    response = await skills_router.get_admin_skill_detail(
        request=SimpleNamespace(),
        skill_id="skill-id",
        admin=SimpleNamespace(id="admin-user"),
        db=db,
    )

    assert response.id == "skill-id"
    assert response.description == "Markdown description"
    assert response.compatibility == "Omlorix"
    assert len(markdown_calls) == 1
    assert len(file_calls) == 1
