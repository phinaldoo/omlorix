from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.skills import router as skills_router
from app.skills import utils as skill_utils
from app.skills.models import Skills


def _session():
    """Create an isolated skill table for user Markdown import tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Skills.__table__])
    return sessionmaker(bind=engine)()


@pytest.mark.anyio
async def test_user_markdown_files_endpoint_imports_valid_siblings(
    monkeypatch,
    tmp_path,
):
    """A malformed file does not prevent other selected skills from importing."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    monkeypatch.setattr(skills_router, "ensure_skills_enabled", lambda *_args: None)
    monkeypatch.setattr(skills_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")
    uploads = [
        UploadFile(
            filename="first.md",
            file=io.BytesIO(
                b"---\nname: first-skill\ndescription: First imported skill.\n---\n"
            ),
        ),
        UploadFile(filename="broken.md", file=io.BytesIO(b"# No frontmatter\n")),
        UploadFile(
            filename="second.md",
            file=io.BytesIO(
                b"\xef\xbb\xbf---\nname: second-skill\ndescription: Second imported skill.\n---\n"
            ),
        ),
        UploadFile(
            filename="too-large.md",
            file=io.BytesIO(b"x" * (skill_utils.SKILL_IMPORT_MAX_SKILL_MD_BYTES + 1)),
        ),
    ]
    db = _session()

    result = await skills_router.import_markdown_skill_files_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        files=uploads,
        user=SimpleNamespace(id="user-1"),
        db=db,
        db_log=object(),
    )

    assert [entry["name"] for entry in result["created"]] == [
        "first-skill",
        "second-skill",
    ]
    assert result["errors"][0]["source"] == "broken.md"
    assert result["errors"][0]["index"] == 1
    assert "frontmatter" in result["errors"][0]["error"].lower()
    assert result["errors"][1]["source"] == "too-large.md"
    assert result["errors"][1]["index"] == 3
    assert "exceeds the allowed size" in result["errors"][1]["error"]
    assert {skill.name for skill in db.query(Skills).all()} == {
        "first-skill",
        "second-skill",
    }
    assert (tmp_path / "user-1").is_dir()


@pytest.mark.anyio
async def test_user_markdown_files_endpoint_rejects_non_markdown_per_file(
    monkeypatch,
):
    """Unsupported siblings are reported without turning a batch into a 500."""
    monkeypatch.setattr(skills_router, "ensure_skills_enabled", lambda *_args: None)
    monkeypatch.setattr(skills_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")
    db = _session()

    result = await skills_router.import_markdown_skill_files_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        files=[UploadFile(filename="notes.txt", file=io.BytesIO(b"plain text"))],
        user=SimpleNamespace(id="user-1"),
        db=db,
        db_log=object(),
    )

    assert result["created"] == []
    assert result["errors"] == [
        {
            "source": "notes.txt",
            "error": "Only .md (Markdown) files are accepted.",
            "index": 0,
        }
    ]


@pytest.mark.anyio
async def test_user_markdown_files_endpoint_continues_after_unexpected_failure(
    monkeypatch,
):
    """Unexpected importer failures remain isolated and do not skip auditing."""
    monkeypatch.setattr(skills_router, "ensure_skills_enabled", lambda *_args: None)
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")
    audit_details = []
    monkeypatch.setattr(
        skills_router,
        "create_audit_log",
        lambda **kwargs: audit_details.append(kwargs["details"]),
    )
    import_calls = 0

    def import_one(_db, _user_id, _markdown):
        """Fail the first upload unexpectedly and create the second."""
        nonlocal import_calls
        import_calls += 1
        if import_calls == 1:
            raise RuntimeError("unexpected user importer failure")
        return SimpleNamespace(id="second-id", name="second-skill")

    monkeypatch.setattr(skills_router, "import_skill_from_markdown", import_one)
    db = _session()
    uploads = [
        UploadFile(filename="first.md", file=io.BytesIO(b"first")),
        UploadFile(filename="second.md", file=io.BytesIO(b"second")),
    ]

    result = await skills_router.import_markdown_skill_files_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        files=uploads,
        user=SimpleNamespace(id="user-1"),
        db=db,
        db_log=object(),
    )

    assert result["created"] == [{"id": "second-id", "name": "second-skill"}]
    assert result["errors"] == [
        {
            "source": "first.md",
            "error": skills_router.SKILL_IMPORT_INTERNAL_ERROR,
            "index": 0,
        }
    ]
    assert "unexpected user importer failure" not in str(result)
    assert audit_details == [
        {"uploaded_file_count": 2, "created_count": 1, "error_count": 1}
    ]
