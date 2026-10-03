"""Focused security and compatibility tests for portable plugin archives."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.mcp.models import MCPOAuthState, MCPServer
from app.plugins.models import AgentPlugin, AgentPluginComponent
from app.plugins.utils import (
    delete_user_plugin,
    get_plugin_archive,
    inspect_plugin_archive,
    install_user_plugin,
    preview_plugin_archive,
    set_plugin_enabled,
)
from app.skills.models import SharedSkillSubscription, Skills, get_skill_context_for_user


def _archive(files: dict[str, str]) -> bytes:
    """Build a small in-memory test archive without touching application data."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _manifest(**updates) -> str:
    """Return a minimal current OpenAI plugin manifest."""
    payload = {
        "name": "research-kit",
        "version": "1.2.0",
        "description": "Research helpers",
        "author": {"name": "Omlorix Tests"},
        "skills": ["./skills/research-kit"],
        "mcpServers": "./.mcp.json",
    }
    payload.update(updates)
    return json.dumps(payload)


def test_preview_accepts_enclosing_folder_and_wrapped_mcp_map():
    """Common GitHub ZIP wrapping and wrapped MCP JSON remain portable."""
    payload = _archive(
        {
            "research-kit/.codex-plugin/plugin.json": _manifest(hooks={"PreToolUse": []}),
            "research-kit/skills/research-kit/SKILL.md": (
                "---\nname: research-kit\ndescription: Research carefully\n---\n\nUse primary sources.\n"
            ),
            "research-kit/.mcp.json": json.dumps(
                {"mcpServers": {"search": {"type": "http", "url": "https://example.com/mcp"}}}
            ),
        }
    )

    preview = preview_plugin_archive(payload)

    assert preview["name"] == "research-kit"
    assert preview["skill_names"] == ["research-kit"]
    assert preview["mcp_server_names"] == ["search"]
    assert preview["has_hooks"] is True
    assert "hooks_not_executed" in preview["warnings"]


def test_manifest_can_declare_the_conventional_skills_container():
    """A single ./skills path discovers each immediate child skill folder."""
    payload = _archive(
        {
            ".codex-plugin/plugin.json": _manifest(skills="./skills", mcpServers=None),
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha skill\n---\n",
            "skills/beta/SKILL.md": "---\nname: beta\ndescription: Beta skill\n---\n",
        }
    )

    preview = preview_plugin_archive(payload)

    assert sorted(preview["skill_names"]) == ["alpha", "beta"]


def test_archive_rejects_path_traversal_before_manifest_processing():
    """A valid manifest cannot make a ZIP-slip member acceptable."""
    payload = _archive(
        {
            ".codex-plugin/plugin.json": _manifest(skills=[], mcpServers=None),
            "../outside.txt": "nope",
        }
    )

    with pytest.raises(ValueError, match="stay inside"):
        inspect_plugin_archive(payload)


def test_personal_stdio_server_is_flagged_for_review():
    """Local commands are detected during preview, before installation writes."""
    payload = _archive(
        {
            ".codex-plugin/plugin.json": _manifest(skills=[]),
            ".mcp.json": json.dumps(
                {"mcpServers": {"local": {"command": "node", "args": ["server.js"]}}}
            ),
        }
    )

    preview = preview_plugin_archive(payload)

    assert preview["requires_local_process_review"] is True
    assert "local_process_not_supported" in preview["warnings"]


def test_manifest_reference_cannot_escape_bundle_root():
    """Manifest paths receive the same traversal protection as ZIP members."""
    payload = _archive(
        {
            ".codex-plugin/plugin.json": _manifest(skills=["../secret"]),
            ".mcp.json": json.dumps({"mcpServers": {}}),
        }
    )

    with pytest.raises(ValueError, match="unsafe"):
        inspect_plugin_archive(payload)


def test_skill_plugin_lifecycle_controls_runtime_and_round_trip(tmp_path, monkeypatch):
    """Install, enable, export, and uninstall act on one aggregate."""
    import app.plugins.utils as plugin_utils
    import app.skills.models as skill_models
    import app.skills.utils as skill_utils
    import app.utils.encryption as encryption

    monkeypatch.setattr(plugin_utils, "PLUGINS_ROOT", tmp_path / "plugins")
    monkeypatch.setattr(skill_models, "SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr(encryption, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption, "_CIPHER_SUITE", None)
    # This focused lifecycle test has no user/group tables. Policy enforcement
    # is exercised by the established group helpers at the authenticated route
    # boundary, so stub their two shared lookups here.
    monkeypatch.setattr("app.groups.init.get_user_group_setting_value", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.groups.init.ensure_data_control_permission", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.mcp.utils.require_group_mcp_enabled", lambda *args, **kwargs: None)
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Skills.__table__,
            SharedSkillSubscription.__table__,
            AgentPlugin.__table__,
            AgentPluginComponent.__table__,
            MCPServer.__table__,
            MCPOAuthState.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    payload = _archive(
        {
            ".codex-plugin/plugin.json": _manifest(),
            ".mcp.json": json.dumps(
                {"mcpServers": {"search": {"type": "http", "url": "https://example.com/mcp"}}}
            ),
            "skills/research-kit/SKILL.md": (
                "---\nname: research-kit\ndescription: Research carefully\n---\n\nUse primary sources.\n"
            ),
            "skills/research-kit/references/checklist.txt": "Confirm dates and authors.",
        }
    )

    plugin = install_user_plugin(db, "user-1", payload)
    components = db.query(AgentPluginComponent).all()
    skill_component = next(item for item in components if item.component_type == "skill")
    mcp_component = next(item for item in components if item.component_type == "mcp_server")
    assert plugin.enabled is False
    assert get_skill_context_for_user(db, "user-1", skill_component.component_id) is None
    assert db.query(MCPServer).filter(MCPServer.id == mcp_component.component_id).one().enabled is False

    set_plugin_enabled(db, "user-1", plugin.id, True)
    context = get_skill_context_for_user(db, "user-1", skill_component.component_id)
    assert "Use primary sources" in context
    assert "Confirm dates and authors" in context
    assert db.query(MCPServer).filter(MCPServer.id == mcp_component.component_id).one().enabled is True
    assert get_plugin_archive(db, "user-1", plugin.id)[1] == payload

    delete_user_plugin(db, "user-1", plugin.id)
    assert db.query(AgentPlugin).count() == 0
    assert db.query(AgentPluginComponent).count() == 0
    assert db.query(Skills).count() == 0
    assert db.query(MCPServer).count() == 0
    assert not (tmp_path / "plugins" / plugin.id).exists()


def test_failed_component_install_compensates_prior_skill(tmp_path, monkeypatch):
    """A later invalid MCP definition cannot leave a half-installed skill."""
    import app.plugins.utils as plugin_utils
    import app.skills.models as skill_models
    import app.skills.utils as skill_utils

    monkeypatch.setattr(plugin_utils, "PLUGINS_ROOT", tmp_path / "plugins")
    monkeypatch.setattr(skill_models, "SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr(skill_utils, "SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr("app.groups.init.get_user_group_setting_value", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.groups.init.ensure_data_control_permission", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.mcp.utils.require_group_mcp_enabled", lambda *args, **kwargs: None)
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Skills.__table__, SharedSkillSubscription.__table__,
            AgentPlugin.__table__, AgentPluginComponent.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    payload = _archive(
        {
            ".codex-plugin/plugin.json": _manifest(),
            ".mcp.json": json.dumps({"mcpServers": {"broken": {"type": "http"}}}),
            "skills/research-kit/SKILL.md": (
                "---\nname: research-kit\ndescription: Research carefully\n---\n\nUse primary sources.\n"
            ),
        }
    )

    with pytest.raises(ValueError):
        install_user_plugin(db, "user-1", payload)

    assert db.query(AgentPlugin).count() == 0
    assert db.query(AgentPluginComponent).count() == 0
    assert db.query(Skills).count() == 0
    assert not (tmp_path / "skills" / "user-1").exists()
