from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.skills import router as skills_router
from app.skills import utils as skill_utils
from app.skills.models import ADMIN_SKILLS_USER_ID, AdminSkills


def _session():
    """Create the smallest database needed by the admin skill portability tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[AdminSkills.__table__])
    return sessionmaker(bind=engine)()


def _admin_skill(skill_id: str = "skill-id") -> AdminSkills:
    """Build a valid admin skill row without invoking unrelated app services."""
    now = datetime.now(timezone.utc)
    return AdminSkills(
        id=skill_id,
        icon="sparkles",
        name="portable-skill",
        description="Use when testing portable skill packages.",
        content="# Instructions\n\nFollow the reference.",
        created_at=now,
        updated_at=now,
    )


def _write_complete_skill(root, skill_id: str) -> None:
    """Create a representative Agent Skills directory with bundled resources."""
    skill_dir = root / ADMIN_SKILLS_USER_ID / skill_id
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "references").mkdir()
    (skill_dir / "assets").mkdir()
    (skill_dir / "extra").mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: portable-skill\n"
        "description: Use when testing portable skill packages.\n"
        "allowed-tools: Bash(python:*) Read\n"
        "---\n\n"
        "# Instructions\n\nFollow references/GUIDE.md.\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "references" / "GUIDE.md").write_text("# Guide\n", encoding="utf-8")
    (skill_dir / "assets" / "template.bin").write_bytes(b"\x00\x01\x02")
    (skill_dir / "extra" / "config.toml").write_text("enabled = true\n", encoding="utf-8")


def test_admin_skill_export_is_a_complete_agent_skills_zip(monkeypatch, tmp_path):
    """The export contains one standards-shaped folder and every bundled file."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    db = _session()
    db.add(_admin_skill())
    db.commit()
    _write_complete_skill(tmp_path, "skill-id")

    archive_buffer, exported_count = skill_utils.export_admin_skills_archive(db)

    assert exported_count == 1
    with zipfile.ZipFile(archive_buffer) as archive:
        assert set(archive.namelist()) >= {
            "portable-skill/",
            "portable-skill/SKILL.md",
            "portable-skill/scripts/run.py",
            "portable-skill/references/GUIDE.md",
            "portable-skill/assets/template.bin",
            "portable-skill/extra/config.toml",
        }
        markdown = archive.read("portable-skill/SKILL.md").decode("utf-8")
        assert "allowed-tools: Bash(python:*) Read" in markdown
        assert 'omlorix_icon: "sparkles"' in markdown
        assert archive.read("portable-skill/assets/template.bin") == b"\x00\x01\x02"


def test_admin_skill_export_skips_an_unreadable_package(monkeypatch, tmp_path):
    """One missing SKILL.md does not prevent healthy packages from exporting."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    db = _session()
    db.add_all([_admin_skill("healthy-skill"), _admin_skill("missing-markdown")])
    db.commit()
    _write_complete_skill(tmp_path, "healthy-skill")

    archive_buffer, exported_count = skill_utils.export_admin_skills_archive(db)

    assert exported_count == 1
    with zipfile.ZipFile(archive_buffer) as archive:
        assert "portable-skill/SKILL.md" in archive.namelist()


def test_admin_skill_export_keeps_markdown_when_name_rewrite_is_unsupported(
    monkeypatch,
    tmp_path,
):
    """A valid block-scalar name never turns into an empty exported document."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    db = _session()
    # The newer plain-name row exports first and reserves ``portable-skill``;
    # the older block-name row must then attempt the unsupported rename.
    db.add_all([_admin_skill("block-name"), _admin_skill("plain-name")])
    db.commit()
    _write_complete_skill(tmp_path, "plain-name")
    _write_complete_skill(tmp_path, "block-name")
    block_markdown = (
        "---\n"
        "name: |\n"
        "  portable-skill\n"
        "description: Use when testing block scalar names.\n"
        "---\n\n"
        "# Block scalar package\n"
    )
    block_path = tmp_path / ADMIN_SKILLS_USER_ID / "block-name" / "SKILL.md"
    block_path.write_text(block_markdown, encoding="utf-8")

    archive_buffer, exported_count = skill_utils.export_admin_skills_archive(db)

    assert exported_count == 2
    with zipfile.ZipFile(archive_buffer) as archive:
        exported_documents = [
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith("/SKILL.md")
        ]
    block_export = next(
        markdown for markdown in exported_documents if "# Block scalar package" in markdown
    )
    assert block_export.startswith("---\nname: |\n  portable-skill\n")
    assert 'omlorix_icon: "sparkles"' in block_export


@pytest.mark.anyio
async def test_admin_export_endpoint_returns_downloadable_zip(monkeypatch, tmp_path):
    """The admin HTTP endpoint exposes the archive with managed-skill download headers."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    monkeypatch.setattr(skills_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")
    db = _session()
    db.add(_admin_skill())
    db.commit()
    _write_complete_skill(tmp_path, "skill-id")

    response = await skills_router.export_admin_skills_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        admin=SimpleNamespace(id="admin-user"),
        db=db,
        db_log=object(),
    )
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert response.media_type == "application/zip"
    assert 'filename="managed-skills-export-' in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.zip"')
    assert response.headers["x-exported-skill-count"] == "1"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert "portable-skill/SKILL.md" in archive.namelist()


def test_admin_skill_zip_round_trip_preserves_markdown_and_resources(monkeypatch, tmp_path):
    """An exported admin package can be imported with all original resources."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    source_db = _session()
    source_db.add(_admin_skill())
    source_db.commit()
    _write_complete_skill(tmp_path, "skill-id")
    archive_buffer, _count = skill_utils.export_admin_skills_archive(source_db)

    target_db = _session()
    result = skill_utils.import_admin_skills_archive(target_db, archive_buffer.getvalue())

    assert result["errors"] == []
    assert len(result["created"]) == 1
    imported_id = result["created"][0]["id"]
    imported = target_db.query(AdminSkills).filter(AdminSkills.id == imported_id).one()
    assert imported.name == "portable-skill"
    assert imported.icon == "sparkles"
    imported_dir = tmp_path / ADMIN_SKILLS_USER_ID / imported_id
    assert "allowed-tools: Bash(python:*) Read" in (
        imported_dir / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert (imported_dir / "scripts" / "run.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert (imported_dir / "extra" / "config.toml").read_text(encoding="utf-8") == "enabled = true\n"


def test_admin_skill_zip_rejects_folder_name_mismatch(monkeypatch, tmp_path):
    """ZIP imports enforce the specification's folder/frontmatter name match."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "wrong-folder/SKILL.md",
            "---\nname: declared-name\ndescription: Demonstrates a mismatch.\n---\n",
        )

    db = _session()
    result = skill_utils.import_admin_skills_archive(db, archive_buffer.getvalue())

    assert result["created"] == []
    assert "must match" in result["errors"][0]["error"]
    assert target_count(db) == 0


def test_admin_skill_zip_import_honors_selected_folders(monkeypatch, tmp_path):
    """The admin preview selection can import a subset without rewriting the ZIP."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("first-skill", "second-skill"):
            archive.writestr(
                f"{name}/SKILL.md",
                f"---\nname: {name}\ndescription: Import {name}.\n---\n",
            )
            archive.writestr(f"{name}/references/guide.md", f"# {name}\n")

    db = _session()
    result = skill_utils.import_admin_skills_archive(
        db,
        archive_buffer.getvalue(),
        selected_folder_prefixes={"second-skill"},
    )

    assert result["errors"] == []
    assert [entry["name"] for entry in result["created"]] == ["second-skill"]
    assert [skill.name for skill in db.query(AdminSkills).all()] == ["second-skill"]


def test_admin_skill_zip_rejects_path_traversal(monkeypatch, tmp_path):
    """Unsafe archive members are rejected before any skill is persisted."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "safe-skill/SKILL.md",
            "---\nname: safe-skill\ndescription: A safe skill.\n---\n",
        )
        archive.writestr("../outside.txt", "blocked")

    db = _session()
    with pytest.raises(ValueError, match="path traversal"):
        skill_utils.import_admin_skills_archive(db, archive_buffer.getvalue())
    assert target_count(db) == 0


@pytest.mark.anyio
async def test_admin_import_files_endpoint_accepts_multiple_markdown_files(monkeypatch, tmp_path):
    """The multipart admin endpoint imports every selected Markdown document."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    monkeypatch.setattr(skills_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")
    markdown_documents = [
        (
            "first.md",
            b"---\nname: first-skill\ndescription: First imported skill.\n---\n",
        ),
        (
            "second.md",
            b"---\nname: second-skill\ndescription: Second imported skill.\n---\n",
        ),
    ]
    uploads = [
        UploadFile(filename=filename, file=io.BytesIO(markdown))
        for filename, markdown in markdown_documents
    ]
    db = _session()

    result = await skills_router.import_admin_skill_files_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        files=uploads,
        archive_selections=json.dumps([None, None]),
        admin=SimpleNamespace(id="admin-user"),
        db=db,
        db_log=object(),
    )

    assert result["errors"] == []
    assert [entry["name"] for entry in result["created"]] == [
        "first-skill",
        "second-skill",
    ]
    assert target_count(db) == 2


@pytest.mark.anyio
async def test_admin_import_files_endpoint_continues_after_unexpected_failure(
    monkeypatch,
):
    """One unexpected Markdown importer failure does not abort later uploads."""
    monkeypatch.setattr(skills_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")
    import_calls = 0

    def import_one(_db, _markdown):
        """Fail the first upload unexpectedly and create the second."""
        nonlocal import_calls
        import_calls += 1
        if import_calls == 1:
            raise RuntimeError("unexpected admin importer failure")
        return SimpleNamespace(id="second-id", name="second-skill")

    monkeypatch.setattr(skills_router, "import_admin_skill_from_markdown", import_one)
    db = _session()
    uploads = [
        UploadFile(filename="first.md", file=io.BytesIO(b"first")),
        UploadFile(filename="second.md", file=io.BytesIO(b"second")),
    ]

    result = await skills_router.import_admin_skill_files_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        files=uploads,
        archive_selections=json.dumps([None, None]),
        admin=SimpleNamespace(id="admin-user"),
        db=db,
        db_log=object(),
    )

    assert result["created"] == [{"id": "second-id", "name": "second-skill"}]
    assert result["errors"] == [
        {"source": "first.md", "error": skills_router.SKILL_IMPORT_INTERNAL_ERROR}
    ]
    assert "unexpected admin importer failure" not in str(result)


@pytest.mark.anyio
async def test_admin_archive_errors_keep_outer_upload_as_source(monkeypatch):
    """Nested archive error metadata cannot replace the uploaded ZIP filename."""
    monkeypatch.setattr(skills_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(skills_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")
    monkeypatch.setattr(
        skills_router,
        "import_admin_skills_archive",
        lambda *_args, **_kwargs: {
            "created": [],
            "errors": [
                {
                    "source": "inner-source",
                    "entry": "broken-skill/SKILL.md",
                    "error": "broken package",
                }
            ],
        },
    )
    db = _session()

    result = await skills_router.import_admin_skill_files_endpoint(
        request=SimpleNamespace(headers={}, client=None),
        files=[UploadFile(filename="outer-package.zip", file=io.BytesIO(b"zip"))],
        archive_selections=json.dumps([None]),
        admin=SimpleNamespace(id="admin-user"),
        db=db,
        db_log=object(),
    )

    assert result["errors"] == [
        {
            "source": "outer-package.zip",
            "entry": "broken-skill/SKILL.md",
            "error": "broken package",
        }
    ]


def test_admin_export_preserves_existing_metadata_indentation():
    """Icon metadata follows the indentation already used by the document."""
    markdown = (
        "---\n"
        "name: portable-skill\n"
        "description: Portable.\n"
        "metadata:\n"
        '    author: "Omlorix"\n'
        "---\n"
    )

    inserted = skill_utils._upsert_skill_markdown_metadata_string(
        markdown,
        key="omlorix_icon",
        value="sparkles",
    )
    updated = skill_utils._upsert_skill_markdown_metadata_string(
        inserted,
        key="author",
        value="Administrator",
    )

    assert '    omlorix_icon: "sparkles"' in updated
    assert '    author: "Administrator"' in updated
    assert '\n  omlorix_icon:' not in updated
    assert '\n  author:' not in updated


def test_admin_export_excludes_symlinked_package_content(monkeypatch, tmp_path):
    """Neither a symlink nor content reachable beneath it enters the ZIP."""
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path)
    db = _session()
    db.add(_admin_skill())
    db.commit()
    _write_complete_skill(tmp_path, "skill-id")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not exportable", encoding="utf-8")
    skill_dir = tmp_path / ADMIN_SKILLS_USER_ID / "skill-id"
    (skill_dir / "linked").symlink_to(outside, target_is_directory=True)

    archive_buffer, _count = skill_utils.export_admin_skills_archive(db)

    with zipfile.ZipFile(archive_buffer) as archive:
        assert not any("/linked" in name for name in archive.namelist())
        assert not any(name.endswith("secret.txt") for name in archive.namelist())


def test_admin_archive_compensation_rolls_back_before_delete(monkeypatch):
    """A failed asset extraction restores the session before compensation."""
    events = []

    class CompensationSession:
        """Record transaction compensation order without a real database."""

        def rollback(self):
            events.append("rollback")

        def delete(self, _skill):
            assert events == ["rollback"]
            events.append("delete")

        def commit(self):
            events.append("commit")

    monkeypatch.setattr(
        skill_utils,
        "import_admin_skill_from_markdown",
        lambda *_args, **_kwargs: SimpleNamespace(id="failed-id", name="failed-skill"),
    )

    def fail_asset_extraction(**_kwargs):
        """Simulate a post-persistence package extraction failure."""
        raise RuntimeError("asset extraction failed")

    monkeypatch.setattr(
        skill_utils,
        "_write_archive_skill_assets",
        fail_asset_extraction,
    )
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "failed-skill/SKILL.md",
            "---\nname: failed-skill\ndescription: Fails during assets.\n---\n",
        )

    result = skill_utils.import_admin_skills_archive(
        CompensationSession(),
        archive_buffer.getvalue(),
    )

    assert events == ["rollback", "delete", "commit"]
    assert result["created"] == []
    assert result["errors"][0]["error"] == "asset extraction failed"


def target_count(db) -> int:
    """Return the admin skill count with a readable assertion call site."""
    return db.query(AdminSkills).count()
