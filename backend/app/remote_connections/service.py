from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from app.chats.models import Chats
from app.llm.models import LLMProvider, Models
from app.remote_connections.models import SshConnection, UserAcpProfile
from app.remote_connections.ssh import SshPrivateKeyError, validate_ssh_private_key
from app.remote_connections.terminal import resolve_terminal_cwd


def _utcnow() -> datetime:
    """Return the shared UTC timestamp used for persistence updates."""
    return datetime.now(timezone.utc)


def get_owned_ssh_connection(db, user_id: str, connection_id: str) -> SshConnection:
    """Load an SSH connection while enforcing its owning user in the query."""
    connection = (
        db.query(SshConnection)
        .filter(
            SshConnection.id == str(connection_id),
            SshConnection.user_id == str(user_id),
        )
        .first()
    )
    if not connection:
        raise HTTPException(status_code=404, detail="SSH connection not found.")
    return connection


def get_owned_acp_profile(db, user_id: str, profile_id: str) -> UserAcpProfile:
    """Load an ACP profile while enforcing its owning user in the query."""
    profile = (
        db.query(UserAcpProfile)
        .filter(
            UserAcpProfile.id == str(profile_id), UserAcpProfile.user_id == str(user_id)
        )
        .first()
    )
    if not profile:
        raise HTTPException(status_code=404, detail="ACP profile not found.")
    return profile


def get_terminal_target_for_model(
    db,
    user_id: str,
    model_id: str,
) -> tuple[UserAcpProfile, SshConnection]:
    """Resolve an enabled ACP model to its enabled, same-owner SSH target.

    The browser sends only the selected model ID. Resolving both records under
    the authenticated user prevents callers from selecting arbitrary SSH
    connection or profile IDs.
    """
    profile = (
        db.query(UserAcpProfile)
        .filter(
            UserAcpProfile.model_id == str(model_id),
            UserAcpProfile.user_id == str(user_id),
            UserAcpProfile.enabled.is_(True),
        )
        .first()
    )
    if not profile:
        raise HTTPException(
            status_code=404, detail="ACP terminal is not available for this model."
        )
    connection = (
        db.query(SshConnection)
        .filter(
            SshConnection.id == profile.ssh_connection_id,
            SshConnection.user_id == str(user_id),
            SshConnection.enabled.is_(True),
        )
        .first()
    )
    if not connection:
        raise HTTPException(
            status_code=404, detail="ACP terminal connection is not available."
        )
    return profile, connection


def save_acp_model_selection(
    db,
    user_id: str,
    *,
    chat_id: str,
    model_id: str,
    session_id: str,
    acp_model_id: str | None = None,
    acp_security_level: str | None = None,
    acp_reasoning_effort: str | None = None,
) -> None:
    """Persist ACP session choices after enforcing chat/profile ownership."""
    profile, _connection = get_terminal_target_for_model(db, user_id, model_id)
    chat = (
        db.query(Chats)
        .filter(Chats.id == str(chat_id), Chats.user_id == str(user_id))
        .first()
    )
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found.")

    meta = dict(chat.meta) if isinstance(chat.meta, dict) else {}
    sessions = (
        dict(meta.get("acp_sessions"))
        if isinstance(meta.get("acp_sessions"), dict)
        else {}
    )
    previous = (
        dict(sessions.get(str(model_id)))
        if isinstance(sessions.get(str(model_id)), dict)
        else {}
    )
    entry = {
        **previous,
        "session_id": str(session_id),
        "provider_id": str(profile.provider_id),
        "cwd": resolve_terminal_cwd(profile.workspace_root, profile.cwd),
        "updated_at": _utcnow().isoformat(),
    }
    selections = {
        "selected_model_id": acp_model_id,
        "selected_security_level": acp_security_level,
        "selected_reasoning_effort": acp_reasoning_effort,
    }
    normalized_model_id = str(acp_model_id or "").strip()
    previous_model_id = str(previous.get("selected_model_id") or "").strip()
    if normalized_model_id and normalized_model_id != previous_model_id:
        # Reasoning values are model-specific. Never carry the old model's
        # effort into a newly selected ACP model unless this request supplies
        # a replacement that was validated against the new model.
        entry.pop("selected_reasoning_effort", None)
    for key, value in selections.items():
        normalized = str(value or "").strip()
        if normalized:
            entry[key] = normalized
    sessions[str(model_id)] = entry
    meta["acp_sessions"] = sessions
    chat.meta = meta
    flag_modified(chat, "meta")
    db.add(chat)
    db.commit()


def _connection_values(
    payload, previous: SshConnection | None = None
) -> dict[str, Any]:
    """Build normalized persistence values without erasing an unchanged private key."""
    previous_secrets = (
        previous.secrets if previous and isinstance(previous.secrets, dict) else {}
    )
    private_key = str(payload.private_key or "").strip() or str(
        previous_secrets.get("private_key") or ""
    )
    if not private_key:
        raise HTTPException(
            status_code=422,
            detail={"code": "ssh_private_key_required"},
        )
    try:
        private_key = validate_ssh_private_key(private_key)
    except SshPrivateKeyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code},
        ) from exc
    return {
        "name": payload.name,
        "icon": payload.icon,
        "host": payload.host,
        "port": payload.port,
        "username": payload.username,
        "host_key": payload.host_key,
        "config": {
            "workspace_root": payload.workspace_root,
            "connect_timeout_seconds": payload.connect_timeout_seconds,
        },
        # Encrypted private keys with passphrases would require an interactive
        # askpass helper. Omlorix intentionally accepts unencrypted, dedicated
        # automation keys so SSH always remains non-interactive.
        "secrets": {"private_key": private_key},
        "enabled": payload.enabled,
        "updated_at": _utcnow(),
    }


def build_ssh_connection_test_candidate(
    user_id: str,
    payload,
    previous: SshConnection | None = None,
) -> SshConnection:
    """Build an unpersisted SSH target from the current form values.

    Edit forms intentionally omit an unchanged private key. Reusing the same
    value builder as persistence ensures a connection test merges that omitted
    key without silently testing any other stale field from the saved row.
    """
    values = _connection_values(payload, previous)
    return SshConnection(
        user_id=str(user_id),
        name=values["name"],
        icon=values["icon"],
        host=values["host"],
        port=values["port"],
        username=values["username"],
        host_key=values["host_key"],
        config=values["config"],
        secrets=values["secrets"],
        enabled=values["enabled"],
        status={},
    )


def _ssh_transport_changed(
    connection: SshConnection,
    values: dict[str, Any],
) -> bool:
    """Return whether saved connectivity evidence is stale after an update."""
    return any(
        (
            connection.host != values["host"],
            int(connection.port or 22) != int(values["port"]),
            connection.username != values["username"],
            connection.host_key != values["host_key"],
            dict(connection.config or {}) != values["config"],
            dict(connection.secrets or {}) != values["secrets"],
        )
    )


def save_ssh_connection(
    db, user_id: str, payload, connection_id: str | None = None
) -> SshConnection:
    """Create or update a caller-owned SSH connection."""
    connection = (
        get_owned_ssh_connection(db, user_id, connection_id) if connection_id else None
    )
    values = _connection_values(payload, connection)
    transport_changed = bool(
        connection is not None and _ssh_transport_changed(connection, values)
    )
    if connection is None:
        connection = SshConnection(
            user_id=str(user_id), created_at=_utcnow(), status={}
        )
        db.add(connection)
    for key, value in values.items():
        setattr(connection, key, value)
    if transport_changed:
        # A successful check of the old host/key must not remain visible after
        # the user saves different transport values.
        connection.status = {}
        flag_modified(connection, "status")
    if not connection.enabled:
        dependent_profiles = (
            db.query(UserAcpProfile)
            .filter(
                UserAcpProfile.user_id == str(user_id),
                UserAcpProfile.ssh_connection_id == connection.id,
            )
            .all()
        )
        for profile in dependent_profiles:
            # A disabled transport cannot leave a selectable model that will
            # inevitably fail. Keep both persistence layers in lockstep.
            profile.enabled = False
            profile.updated_at = _utcnow()
            _sync_acp_model(db, profile)
    db.commit()
    db.refresh(connection)
    return connection


def serialize_acp_profile(profile: UserAcpProfile) -> dict[str, Any]:
    """Return a browser-safe ACP profile payload."""
    return {
        "id": profile.id,
        "name": profile.name,
        "icon": str(profile.icon or "terminal"),
        "ssh_connection_id": profile.ssh_connection_id,
        "executable": profile.executable,
        "arguments": list(profile.arguments or []),
        "workspace_root": profile.workspace_root,
        "cwd": profile.cwd,
        "additional_directories": list(profile.additional_directories or []),
        "mode": profile.mode,
        "permission_mode": profile.permission_mode,
        "permission_timeout_seconds": profile.permission_timeout_seconds,
        "prompt_timeout_seconds": profile.prompt_timeout_seconds,
        "enabled": bool(profile.enabled),
        "provider_id": profile.provider_id,
        "model_id": profile.model_id,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _sync_acp_model(db, profile: UserAcpProfile) -> None:
    """Maintain the private model-picker row backing a user-owned ACP profile."""
    provider = (
        db.query(LLMProvider).filter(LLMProvider.id == profile.provider_id).first()
        if profile.provider_id
        else None
    )
    provider_settings = {
        "user_managed": True,
        "owner_user_id": profile.user_id,
        "acp_profile_id": profile.id,
        "disable_background_sync": True,
    }
    if provider is None:
        provider = LLMProvider(
            provider="acp",
            name=f"User ACP {profile.id}",
            icon=str(profile.icon or "terminal"),
            api_key="user-managed",
            settings=provider_settings,
            status={"available": "unknown", "model_list": []},
        )
        db.add(provider)
        db.flush()
        profile.provider_id = provider.id
    else:
        provider.icon = str(profile.icon or "terminal")
        provider.settings = provider_settings
        flag_modified(provider, "settings")

    model = (
        db.query(Models).filter(Models.id == profile.model_id).first()
        if profile.model_id
        else None
    )
    model_settings = {
        "cwd": profile.cwd,
        "mode": profile.mode,
        "additional_directories": list(profile.additional_directories or []),
        "acp_profile_id": profile.id,
        # The UI may offer these attachment families, while the negotiated ACP
        # prompt capabilities remain the final runtime gate for every payload.
        "input_formats": ["text", "image", "audio", "video", "documents"],
        "output_formats": ["text"],
    }
    if model is None:
        model = Models(
            name=profile.name,
            description="Personal ACP agent on a private SSH connection",
            model_icon=str(profile.icon or "terminal"),
            provider="acp",
            provider_id=provider.id,
            model_name=f"acp-profile:{profile.id}",
            settings=model_settings,
            capabilities=["completion", "tools", "thinking"],
            tools=[],
            access={"everyone": False, "users": [profile.user_id], "groups": []},
            meta={
                "user_managed": True,
                "owner_user_id": profile.user_id,
                "acp_profile_id": profile.id,
            },
            status="normal",
            is_active=bool(profile.enabled),
        )
        db.add(model)
        db.flush()
        profile.model_id = model.id
    else:
        model.name = profile.name
        model.model_icon = str(profile.icon or "terminal")
        model.provider_id = provider.id
        model.settings = model_settings
        model.access = {"everyone": False, "users": [profile.user_id], "groups": []}
        model.meta = {
            "user_managed": True,
            "owner_user_id": profile.user_id,
            "acp_profile_id": profile.id,
        }
        model.is_active = bool(profile.enabled)
        flag_modified(model, "settings")
        flag_modified(model, "access")
        flag_modified(model, "meta")


def save_acp_profile(
    db,
    user_id: str,
    payload,
    profile_id: str | None = None,
    *,
    commit: bool = True,
) -> UserAcpProfile:
    """Create or update a user ACP profile and its private model-picker entry.

    Import restoration can defer the commit so SSH rows and every dependent
    profile remain part of one transaction.
    """
    connection = get_owned_ssh_connection(db, user_id, payload.ssh_connection_id)
    if payload.enabled and not connection.enabled:
        raise HTTPException(
            status_code=422,
            detail={"code": "acp_enable_ssh_first"},
        )
    profile = get_owned_acp_profile(db, user_id, profile_id) if profile_id else None
    if profile is None:
        profile = UserAcpProfile(user_id=str(user_id), created_at=_utcnow())
        db.add(profile)
    for field in (
        "name",
        "icon",
        "ssh_connection_id",
        "executable",
        "arguments",
        "workspace_root",
        "cwd",
        "additional_directories",
        "mode",
        "permission_mode",
        "permission_timeout_seconds",
        "prompt_timeout_seconds",
        "enabled",
    ):
        setattr(profile, field, getattr(payload, field))
    profile.updated_at = _utcnow()
    db.flush()
    _sync_acp_model(db, profile)
    if commit:
        db.commit()
        db.refresh(profile)
    return profile


def delete_acp_profile(db, user_id: str, profile_id: str) -> None:
    """Delete a profile and its private provider/model rows atomically."""
    profile = get_owned_acp_profile(db, user_id, profile_id)
    if profile.model_id:
        db.query(Models).filter(Models.id == profile.model_id).delete(
            synchronize_session=False
        )
    if profile.provider_id:
        db.query(LLMProvider).filter(LLMProvider.id == profile.provider_id).delete(
            synchronize_session=False
        )
    db.delete(profile)
    db.commit()


def delete_ssh_connection(db, user_id: str, connection_id: str) -> None:
    """Delete an SSH connection only after dependent ACP profiles are removed."""
    connection = get_owned_ssh_connection(db, user_id, connection_id)
    dependent_count = (
        db.query(UserAcpProfile)
        .filter(
            UserAcpProfile.user_id == str(user_id),
            UserAcpProfile.ssh_connection_id == connection.id,
        )
        .count()
    )
    if dependent_count:
        raise HTTPException(
            status_code=409,
            detail={"code": "ssh_delete_dependencies"},
        )
    db.delete(connection)
    db.commit()
