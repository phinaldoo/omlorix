"""Authenticated lifecycle API for user-owned agent plugins."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.logging.models import create_audit_log, get_audit_request_ip
from app.plugins.schemas import PluginEnabledRequest, PluginPreviewResponse, PluginResponse
from app.plugins.utils import (
    PLUGIN_MAX_ARCHIVE_BYTES,
    delete_user_plugin,
    get_plugin_archive,
    install_user_plugin,
    list_serialized_user_plugins,
    preview_plugin_archive,
    serialize_plugin,
    set_plugin_enabled,
)


plugins_router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])


async def _read_upload(file: UploadFile) -> bytes:
    """Read an upload incrementally and reject oversized requests early."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > PLUGIN_MAX_ARCHIVE_BYTES:
            raise HTTPException(status_code=413, detail="Plugin archive exceeds the upload size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@plugins_router.get("", response_model=list[PluginResponse])
def list_plugins(db: Session = Depends(get_db), user=Depends(verified_user)):
    """List plugins owned by the current user."""
    return list_serialized_user_plugins(db, user.id)


@plugins_router.post("/preview", response_model=PluginPreviewResponse)
async def preview_plugin(file: UploadFile = File(...), user=Depends(verified_user)):
    """Validate and summarize a bundle without changing persistent state."""
    del user
    try:
        return preview_plugin_archive(await _read_upload(file))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@plugins_router.post("/install", response_model=PluginResponse, status_code=status.HTTP_201_CREATED)
async def install_plugin(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Install a validated bundle in an initially disabled state."""
    try:
        plugin = install_user_plugin(db, user.id, await _read_upload(file))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = serialize_plugin(db, plugin)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="agent_plugin_installed",
        details={"plugin_id": plugin.id, "name": plugin.name, "version": plugin.version, "component_count": len(result["components"])},
        ip_address=get_audit_request_ip(request, db),
        category="plugins",
    )
    return result


@plugins_router.patch("/{plugin_id}/enabled", response_model=PluginResponse)
def update_plugin_enabled(
    plugin_id: str,
    payload: PluginEnabledRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Enable or disable all capabilities owned by a plugin."""
    plugin = set_plugin_enabled(db, user.id, plugin_id, payload.enabled)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="agent_plugin_enabled" if payload.enabled else "agent_plugin_disabled",
        details={"plugin_id": plugin.id, "name": plugin.name},
        ip_address=get_audit_request_ip(request, db),
        category="plugins",
    )
    return serialize_plugin(db, plugin)


@plugins_router.get("/{plugin_id}/export")
def export_plugin(
    plugin_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Export the original validated bundle without exposing separate stored secrets."""
    plugin, archive = get_plugin_archive(db, user.id, plugin_id)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", plugin.name).strip("-") or "plugin"
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="agent_plugin_exported",
        details={"plugin_id": plugin.id, "name": plugin.name},
        ip_address=get_audit_request_ip(request, db),
        category="plugins",
    )
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-{plugin.version}.zip"'},
    )


@plugins_router.delete("/{plugin_id}", status_code=status.HTTP_204_NO_CONTENT)
def uninstall_plugin(
    plugin_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    """Remove a plugin and every capability it installed."""
    delete_user_plugin(db, user.id, plugin_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="agent_plugin_uninstalled",
        details={"plugin_id": plugin_id},
        ip_address=get_audit_request_ip(request, db),
        category="plugins",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
