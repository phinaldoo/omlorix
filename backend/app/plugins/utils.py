"""Secure parsing, installation, export, and lifecycle helpers for plugins."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.mcp.models import (
    OWNER_USER,
    TRANSPORT_SSE,
    TRANSPORT_STREAMABLE_HTTP,
    create_mcp_server,
    delete_mcp_server,
    get_mcp_server,
)
from app.mcp.schemas import CreateMCPServerRequest
from app.paths import DATA_DIR
from app.plugins.models import (
    AgentPlugin,
    AgentPluginComponent,
    COMPONENT_MCP_SERVER,
    COMPONENT_SKILL,
    get_user_plugin,
    list_user_plugins,
)
from app.plugins.schemas import PluginManifest
from app.skills.models import _skill_directory, delete_skill
from app.skills.utils import _write_archive_skill_assets, import_skill_from_markdown


PLUGINS_ROOT = DATA_DIR / "plugins"
PLUGIN_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
PLUGIN_MAX_EXPANDED_BYTES = 200 * 1024 * 1024
PLUGIN_MAX_FILE_BYTES = 25 * 1024 * 1024
PLUGIN_MAX_ENTRIES = 2_000
PLUGIN_MAX_COMPRESSION_RATIO = 200
MANIFEST_PATH = PurePosixPath(".codex-plugin/plugin.json")


def _normalize_archive_name(name: str) -> PurePosixPath:
    """Normalize an archive member and reject paths unsafe on any platform."""
    raw = str(name or "")
    if not raw or "\x00" in raw or "\\" in raw:
        raise ValueError("Plugin archive contains an invalid path.")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ValueError("Plugin archive paths must stay inside the bundle.")
    if path.parts and ":" in path.parts[0]:
        raise ValueError("Plugin archive contains an absolute Windows path.")
    return path


def _validate_archive(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Apply zip-slip, symlink, duplicate, zip-bomb, and size protections."""
    infos = archive.infolist()
    if not infos or len(infos) > PLUGIN_MAX_ENTRIES:
        raise ValueError(f"Plugin archives may contain at most {PLUGIN_MAX_ENTRIES} entries.")
    total = 0
    by_name: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        path = _normalize_archive_name(info.filename)
        normalized = path.as_posix().rstrip("/")
        if normalized in by_name:
            raise ValueError(f"Plugin archive contains duplicate path '{normalized}'.")
        by_name[normalized] = info
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(unix_mode):
            raise ValueError("Plugin archives cannot contain symbolic links.")
        if info.is_dir():
            continue
        if info.file_size > PLUGIN_MAX_FILE_BYTES:
            raise ValueError(f"Plugin file '{normalized}' exceeds the per-file size limit.")
        total += info.file_size
        if total > PLUGIN_MAX_EXPANDED_BYTES:
            raise ValueError("Expanded plugin archive exceeds the size limit.")
        if info.compress_size == 0 and info.file_size > 0:
            raise ValueError("Plugin archive contains an invalid compressed entry.")
        if info.compress_size and info.file_size / info.compress_size > PLUGIN_MAX_COMPRESSION_RATIO:
            raise ValueError("Plugin archive contains a suspicious compression ratio.")
    return by_name


def _locate_manifest(by_name: dict[str, zipfile.ZipInfo]) -> tuple[PurePosixPath, zipfile.ZipInfo]:
    """Find exactly one plugin manifest, allowing one enclosing upload folder."""
    matches = [
        (PurePosixPath(name), info)
        for name, info in by_name.items()
        if PurePosixPath(name).parts[-2:] == MANIFEST_PATH.parts
    ]
    if len(matches) != 1:
        raise ValueError("Plugin archive must contain exactly one .codex-plugin/plugin.json manifest.")
    path, info = matches[0]
    if info.file_size > 256_000:
        raise ValueError("Plugin manifest exceeds the size limit.")
    return path, info


def _safe_manifest_path(root: PurePosixPath, raw_path: str) -> PurePosixPath:
    """Resolve a manifest-relative path without allowing bundle escapes."""
    value = str(raw_path or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Manifest path '{raw_path}' is unsafe.")
    return root / relative


def _load_json(archive: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> dict:
    """Read a bounded UTF-8 JSON object with a helpful validation error."""
    try:
        value = json.loads(archive.read(info).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object.")
    return value


def _manifest_reference_paths(value) -> list[str]:
    """Extract JSON file references from string/list manifest fields."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str)]
    return []


def _mcp_config_from_manifest(archive, by_name, root, manifest: PluginManifest) -> dict[str, dict]:
    """Load direct or wrapped MCP server maps from all referenced files."""
    merged: dict[str, dict] = {}
    value = manifest.mcpServers
    if isinstance(value, dict):
        source = value.get("mcpServers") if isinstance(value.get("mcpServers"), dict) else value
        merged.update({str(key): item for key, item in source.items() if isinstance(item, dict)})
    for raw_path in _manifest_reference_paths(value):
        path = _safe_manifest_path(root, raw_path)
        info = by_name.get(path.as_posix())
        if not info:
            raise ValueError(f"Referenced MCP configuration '{raw_path}' was not found.")
        payload = _load_json(archive, info, "MCP configuration")
        source = payload.get("mcpServers") if isinstance(payload.get("mcpServers"), dict) else payload
        merged.update({str(key): item for key, item in source.items() if isinstance(item, dict)})
    return merged


def _skill_documents(archive, by_name, root, manifest: PluginManifest):
    """Resolve declared skill folders or discover conventional skill folders."""
    documents: list[tuple[str, str, str]] = []
    declared = manifest.skills
    if declared:
        candidates = []
        for raw_path in declared:
            folder = _safe_manifest_path(root, raw_path)
            direct_document = folder / "SKILL.md"
            if direct_document.as_posix() in by_name:
                candidates.append((folder.name, direct_document))
                continue

            # The current manifest format also permits the conventional
            # ``./skills`` container rather than enumerating each child. Only
            # immediate child skill folders are discovered, keeping unrelated
            # nested Markdown from becoming an executable capability.
            child_documents = [
                (path.parent.name, path)
                for name in by_name
                for path in [PurePosixPath(name)]
                if path.parent.parent == folder and path.name.lower() == "skill.md"
            ]
            if not child_documents:
                raise ValueError(f"Declared skill path '{raw_path}' does not contain any SKILL.md files.")
            candidates.extend(child_documents)
    else:
        candidates = [
            (path.parent.name, path)
            for name in by_name
            for path in [PurePosixPath(name)]
            if len(path.parts) >= len(root.parts) + 3
            and path.parent.parent == root / "skills"
            and path.name.lower() == "skill.md"
        ]
    seen: set[str] = set()
    for key, path in candidates:
        if path.as_posix() in seen:
            continue
        seen.add(path.as_posix())
        info = by_name.get(path.as_posix())
        if not info:
            raise ValueError(f"Declared skill '{key}' does not contain SKILL.md.")
        try:
            markdown = archive.read(info).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Skill '{key}' is not valid UTF-8.") from exc
        documents.append((key, path.parent.as_posix(), markdown))
    return documents


def inspect_plugin_archive(payload: bytes) -> dict:
    """Fully validate a plugin archive and return its staged installation plan."""
    if not payload or len(payload) > PLUGIN_MAX_ARCHIVE_BYTES:
        raise ValueError("Plugin archive is empty or exceeds the upload size limit.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid ZIP archive.") from exc
    with archive:
        by_name = _validate_archive(archive)
        manifest_path, manifest_info = _locate_manifest(by_name)
        try:
            manifest = PluginManifest.model_validate(_load_json(archive, manifest_info, "Plugin manifest"))
        except ValidationError as exc:
            raise ValueError(f"Plugin manifest is invalid: {exc.errors()[0].get('msg', 'validation failed')}.") from exc
        root = manifest_path.parent.parent
        skills = _skill_documents(archive, by_name, root, manifest)
        mcp_servers = _mcp_config_from_manifest(archive, by_name, root, manifest)
        warnings: list[str] = []
        has_stdio = any(str(item.get("command") or "").strip() for item in mcp_servers.values())
        has_hooks = manifest.hooks is not None
        has_apps = manifest.apps is not None
        if has_stdio:
            warnings.append("local_process_not_supported")
        if has_hooks:
            warnings.append("hooks_not_executed")
        if has_apps:
            warnings.append("registered_apps_require_mcp")
        if not skills and not mcp_servers:
            warnings.append("no_installable_components")
        return {
            "manifest": manifest,
            "manifest_raw": _load_json(archive, manifest_info, "Plugin manifest"),
            "root": root,
            "skills": skills,
            "mcp_servers": mcp_servers,
            "warnings": warnings,
            "has_stdio": has_stdio,
            "has_hooks": has_hooks,
            "has_apps": has_apps,
            "content_sha256": hashlib.sha256(payload).hexdigest(),
        }


def preview_plugin_archive(payload: bytes) -> dict:
    """Return the safe, user-facing subset of an inspected archive."""
    plan = inspect_plugin_archive(payload)
    manifest = plan["manifest"]
    return {
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "author_name": manifest.author.name if manifest.author else "",
        "content_sha256": plan["content_sha256"],
        "skill_names": [item[0] for item in plan["skills"]],
        "mcp_server_names": sorted(plan["mcp_servers"]),
        "warnings": plan["warnings"],
        "requires_local_process_review": plan["has_stdio"],
        "has_hooks": plan["has_hooks"],
        "has_registered_apps": plan["has_apps"],
    }


def _mcp_payload(name: str, config: dict) -> CreateMCPServerRequest:
    """Translate Codex MCP JSON variants into Omlorix's provider-neutral model."""
    if str(config.get("command") or "").strip():
        raise ValueError(f"MCP server '{name}' launches a local process and cannot be installed personally.")
    url = str(config.get("url") or config.get("serverUrl") or "").strip()
    raw_type = str(config.get("type") or config.get("transport") or "").strip().lower()
    transport = TRANSPORT_SSE if raw_type == "sse" else TRANSPORT_STREAMABLE_HTTP
    return CreateMCPServerRequest(
        owner_type=OWNER_USER,
        name=name,
        description=str(config.get("description") or "").strip() or None,
        namespace=str(config.get("namespace") or name).strip(),
        transport=transport,
        enabled=False,
        url=url,
        args=[],
        headers=config.get("headers") or {},
        env={},
        allowed_tools=config.get("allowedTools") or config.get("allowed_tools") or [],
        timeout_seconds=config.get("timeoutSeconds") or config.get("timeout_seconds") or 30,
    )


def _plugin_storage(plugin_id: str) -> Path:
    """Return the dedicated source archive directory for one plugin."""
    return PLUGINS_ROOT / str(plugin_id)


def install_user_plugin(db, user_id: str, payload: bytes) -> AgentPlugin:
    """Install one validated bundle, compensating every committed sub-operation on failure."""
    plan = inspect_plugin_archive(payload)
    # Plugin import must not become a back door around the same group controls
    # enforced by the standalone Skills and MCP management endpoints.
    if plan["skills"]:
        from app.groups.init import ensure_data_control_permission, get_user_group_setting_value

        if not get_user_group_setting_value(user_id, "skills", "enabled_skills", db):
            raise HTTPException(status_code=403, detail="Skills feature disabled for your group")
        ensure_data_control_permission(
            user_id,
            "allow_skills",
            db,
            detail="Skills are disabled by your group's data controls.",
        )
    if plan["mcp_servers"]:
        from app.mcp.utils import require_group_mcp_enabled

        require_group_mcp_enabled(user_id, db)
    if plan["has_stdio"]:
        raise ValueError("Personal plugins cannot install local-process MCP servers.")
    duplicate = (
        db.query(AgentPlugin)
        .filter(AgentPlugin.owner_user_id == user_id, AgentPlugin.name == plan["manifest"].name)
        .first()
    )
    if duplicate:
        raise ValueError("A plugin with this name is already installed. Uninstall it before installing another version.")

    manifest = plan["manifest"]
    compatibility = {
        "warnings": plan["warnings"],
        "has_hooks": plan["has_hooks"],
        "has_registered_apps": plan["has_apps"],
    }
    plugin = AgentPlugin(
        owner_user_id=user_id,
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        enabled=False,
        content_sha256=plan["content_sha256"],
        manifest=plan["manifest_raw"],
        compatibility=compatibility,
    )
    db.add(plugin)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "A plugin with this name is already installed. Uninstall it before installing another version."
        ) from exc
    db.refresh(plugin)
    # Keep the scalar separately because a rollback followed by compensation
    # can expire or delete the ORM instance before filesystem cleanup runs.
    plugin_id = str(plugin.id)
    created: list[tuple[str, str]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for key, folder_prefix, markdown in plan["skills"]:
                skill = import_skill_from_markdown(db, user_id, markdown)
                _write_archive_skill_assets(archive, folder_prefix, _skill_directory(user_id, skill.id))
                created.append((COMPONENT_SKILL, skill.id))
                db.add(AgentPluginComponent(plugin_id=plugin_id, component_type=COMPONENT_SKILL, component_id=skill.id, component_key=key))
                db.commit()
            for key, config in plan["mcp_servers"].items():
                parsed = _mcp_payload(key, config)
                server = create_mcp_server(db, owner_type=OWNER_USER, owner_user_id=user_id, **parsed.model_dump(exclude={"owner_type"}))
                created.append((COMPONENT_MCP_SERVER, server.id))
                db.add(AgentPluginComponent(plugin_id=plugin_id, component_type=COMPONENT_MCP_SERVER, component_id=server.id, component_key=key))
                db.commit()
        storage = _plugin_storage(plugin_id)
        storage.mkdir(parents=True, exist_ok=False)
        (storage / "bundle.zip").write_bytes(payload)
        return plugin
    except Exception:
        # A failed component commit leaves SQLAlchemy's transaction unusable.
        # Roll it back before attempting compensating deletes, otherwise the
        # first cleanup would fail and leave an orphaned skill or MCP server.
        db.rollback()
        for component_type, component_id in reversed(created):
            try:
                if component_type == COMPONENT_SKILL:
                    delete_skill(db, user_id, component_id)
                else:
                    delete_mcp_server(db, component_id)
            except Exception:
                db.rollback()
        db.query(AgentPluginComponent).filter(AgentPluginComponent.plugin_id == plugin_id).delete(synchronize_session=False)
        db.query(AgentPlugin).filter(AgentPlugin.id == plugin_id).delete(synchronize_session=False)
        db.commit()
        shutil.rmtree(_plugin_storage(plugin_id), ignore_errors=True)
        raise


def set_plugin_enabled(db, user_id: str, plugin_id: str, enabled: bool) -> AgentPlugin:
    """Atomically expose or hide a plugin and synchronize its MCP server rows."""
    plugin = get_user_plugin(db, user_id, plugin_id)
    plugin.enabled = bool(enabled)
    plugin.updated_at = datetime.now(timezone.utc)
    component_ids = [
        row.component_id
        for row in db.query(AgentPluginComponent).filter(
            AgentPluginComponent.plugin_id == plugin.id,
            AgentPluginComponent.component_type == COMPONENT_MCP_SERVER,
        )
    ]
    if component_ids:
        from app.mcp.models import MCPServer
        db.query(MCPServer).filter(
            MCPServer.id.in_(component_ids),
            MCPServer.owner_type == OWNER_USER,
            MCPServer.owner_user_id == user_id,
        ).update(
            {MCPServer.enabled: bool(enabled)}, synchronize_session=False
        )
    db.commit()
    db.refresh(plugin)
    return plugin


def delete_user_plugin(db, user_id: str, plugin_id: str) -> None:
    """Uninstall every owned component before removing bundle metadata."""
    plugin = get_user_plugin(db, user_id, plugin_id)
    components = db.query(AgentPluginComponent).filter(AgentPluginComponent.plugin_id == plugin.id).all()
    for component in components:
        if component.component_type == COMPONENT_SKILL:
            delete_skill(db, user_id, component.component_id)
        elif component.component_type == COMPONENT_MCP_SERVER:
            server = get_mcp_server(db, component.component_id)
            if server.owner_user_id != user_id:
                raise HTTPException(status_code=403, detail="Plugin component ownership mismatch.")
            delete_mcp_server(db, component.component_id)
    db.query(AgentPluginComponent).filter(AgentPluginComponent.plugin_id == plugin.id).delete(synchronize_session=False)
    db.delete(plugin)
    db.commit()
    shutil.rmtree(_plugin_storage(plugin.id), ignore_errors=True)


def serialize_plugin(db, plugin: AgentPlugin) -> dict:
    """Serialize a plugin with safe component summaries."""
    from app.mcp.models import MCPServer
    from app.skills.models import Skills

    rows = db.query(AgentPluginComponent).filter(AgentPluginComponent.plugin_id == plugin.id).all()
    components = []
    for row in rows:
        if row.component_type == COMPONENT_SKILL:
            item = db.query(Skills).filter(Skills.id == row.component_id).first()
            if item:
                components.append({"id": item.id, "type": row.component_type, "key": row.component_key, "name": item.name, "enabled": bool(plugin.enabled)})
        else:
            item = db.query(MCPServer).filter(MCPServer.id == row.component_id).first()
            if item:
                components.append({"id": item.id, "type": row.component_type, "key": row.component_key, "name": item.name, "enabled": bool(item.enabled and plugin.enabled)})
    manifest = plugin.manifest if isinstance(plugin.manifest, dict) else {}
    author = manifest.get("author") if isinstance(manifest.get("author"), dict) else {}
    compatibility = plugin.compatibility if isinstance(plugin.compatibility, dict) else {}
    return {
        "id": plugin.id,
        "name": plugin.name,
        "version": plugin.version,
        "description": plugin.description or "",
        "enabled": bool(plugin.enabled),
        "content_sha256": plugin.content_sha256,
        "author_name": str(author.get("name") or ""),
        "homepage": str(manifest.get("homepage") or ""),
        "repository": str(manifest.get("repository") or ""),
        "components": components,
        "warnings": list(compatibility.get("warnings") or []),
        "created_at": plugin.created_at.isoformat() if plugin.created_at else None,
        "updated_at": plugin.updated_at.isoformat() if plugin.updated_at else None,
    }


def list_serialized_user_plugins(db, user_id: str) -> list[dict]:
    """List serialized plugins for API responses and account export."""
    return [serialize_plugin(db, item) for item in list_user_plugins(db, user_id)]


def get_plugin_archive(db, user_id: str, plugin_id: str) -> tuple[AgentPlugin, bytes]:
    """Read the original validated bundle for lossless round-trip export."""
    plugin = get_user_plugin(db, user_id, plugin_id)
    path = _plugin_storage(plugin.id) / "bundle.zip"
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Plugin source archive is unavailable.")
    return plugin, path.read_bytes()
