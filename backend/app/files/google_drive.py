from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
from typing import Any, Callable

import anyio
import httpx
from fastapi import HTTPException

from app.connections.google import (
    GOOGLE_OAUTH_TOKEN_URL,
    GOOGLE_OAUTH_USERINFO_URL,
    complete_google_oauth,
    google_picker_client_settings,
    refresh_google_tokens,
)
from app.connections.models import PROVIDER_GOOGLE_DRIVE, create_user_connection, get_user_connection_by_provider, update_user_connection
from app.connections.policy import ensure_group_allows_connection_provider
from app.files.models import get_file
from app.files.schemas import FileList
from app.files.utils import MAX_FILE_SIZE, resolve_user_max_upload_size_bytes, upload_file
from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.utils.blocking_io import run_blocking_io


GOOGLE_DRIVE_EXPORT_API_URL = "https://www.googleapis.com/drive/v3/files/{file_id}/export"
GOOGLE_DRIVE_DOWNLOAD_API_URL = "https://www.googleapis.com/drive/v3/files/{file_id}"
GOOGLE_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_DRIVE_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
GOOGLE_DRIVE_IMPORT_LIMIT = 20
GOOGLE_DRIVE_TOKEN_REFRESH_LEEWAY_SECONDS = 300
GOOGLE_DRIVE_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=20.0)

GOOGLE_NATIVE_EXPORTS: dict[str, tuple[str, str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
        "Google Doc",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
        "Google Sheet",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
        "Google Slides",
    ),
    "application/vnd.google-apps.drawing": (
        "image/png",
        ".png",
        "Google Drawing",
    ),
    "application/vnd.google-apps.form": (
        "application/pdf",
        ".pdf",
        "Google Form",
    ),
    "application/vnd.google-apps.script": (
        "application/json",
        ".json",
        "Apps Script",
    ),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _assert_google_drive_url_allowed(db, url: str, *, feature: str) -> None:
    try:
        assert_url_allowed(db, url=url, feature=feature)
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc


def _connected_status_payload(*, state: str = "connected", last_error: str = "", connected_at: str | None = None) -> dict[str, Any]:
    now = _utcnow().isoformat()
    return {
        "state": state,
        "last_error": last_error,
        "tool_count": 0,
        "tool_names": [],
        "checked_at": now,
        "connected_at": connected_at or now,
        "last_sync_at": now,
    }


def _connection_has_access_token(connection) -> bool:
    secrets = connection.secrets if isinstance(connection.secrets, dict) else {}
    return bool(str(secrets.get("access_token") or "").strip())


def _sanitize_import_name(name: str, suffix: str | None = None) -> str:
    safe_name = Path(str(name or "google-drive-file")).name.strip() or "google-drive-file"
    if suffix:
        current_suffix = Path(safe_name).suffix.lower()
        if current_suffix != suffix.lower():
            safe_name = f"{Path(safe_name).stem}{suffix}"
    return safe_name


def _native_export_settings(mime_type: str) -> tuple[str, str, str]:
    if mime_type in GOOGLE_NATIVE_EXPORTS:
        return GOOGLE_NATIVE_EXPORTS[mime_type]
    return ("application/pdf", ".pdf", "Google file")


def _update_drive_connection(db, connection, *, secrets: dict[str, Any] | None = None, status: dict[str, Any] | None = None):
    updates: dict[str, Any] = {}
    if secrets is not None:
        updates["secrets"] = secrets
    if status is not None:
        updates["status"] = status
    if not updates:
        return connection
    return update_user_connection(db, connection.id, **updates)


def _mark_connection_reauth_required(db, connection, message: str):
    secrets = deepcopy(connection.secrets if isinstance(connection.secrets, dict) else {})
    secrets.update(
        {
            "access_token": None,
            "refresh_token": None,
            "expires_at": None,
        }
    )
    connected_at = connection.connected_at.isoformat() if connection.connected_at else None
    status = _connected_status_payload(
        state="reauthorization_required",
        last_error=message,
        connected_at=connected_at,
    )
    return _update_drive_connection(db, connection, secrets=secrets, status=status)


def _refresh_drive_connection_if_needed(db, connection, *, force: bool = False):
    if not connection or not _connection_has_access_token(connection):
        return connection
    secrets = deepcopy(connection.secrets if isinstance(connection.secrets, dict) else {})
    expires_at_raw = secrets.get("expires_at")
    try:
        expires_at = int(expires_at_raw) if expires_at_raw is not None else None
    except (TypeError, ValueError):
        expires_at = None
    now_timestamp = int(_utcnow().timestamp())
    if not force and expires_at and expires_at > now_timestamp + GOOGLE_DRIVE_TOKEN_REFRESH_LEEWAY_SECONDS:
        return connection
    if not force and expires_at is None:
        return connection
    _assert_google_drive_url_allowed(
        db,
        GOOGLE_OAUTH_TOKEN_URL,
        feature="Google Drive token refresh",
    )
    try:
        refreshed = refresh_google_tokens(secrets)
    except ValueError as exc:
        _mark_connection_reauth_required(db, connection, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    connected_at = connection.connected_at.isoformat() if connection.connected_at else None
    status = _connected_status_payload(connected_at=connected_at)
    return _update_drive_connection(db, connection, secrets=refreshed, status=status)


def _drive_request_json(client: httpx.Client, access_token: str, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.get(
        url,
        params=params,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Google Drive authorization expired. Reconnect the account.")
    if response.status_code >= 400:
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
        raise HTTPException(status_code=response.status_code, detail=message or response.text or "Google Drive request failed.")
    return payload if isinstance(payload, dict) else {}


def _download_drive_file_to_path(
    client: httpx.Client,
    access_token: str,
    metadata: dict[str, Any],
    *,
    max_upload_bytes: int,
    max_upload_mb: int,
) -> tuple[str, str, str]:
    file_id = str(metadata.get("id") or "").strip()
    original_name = str(metadata.get("name") or "").strip() or "Google Drive file"
    mime_type = str(metadata.get("mimeType") or "").strip()
    if not file_id or not mime_type:
        raise HTTPException(status_code=400, detail="Google Drive file metadata is incomplete.")
    if mime_type == GOOGLE_DRIVE_FOLDER_MIME:
        raise HTTPException(status_code=400, detail="Folders cannot be attached to chats.")
    if mime_type == GOOGLE_DRIVE_SHORTCUT_MIME:
        raise HTTPException(status_code=400, detail="Google Drive shortcuts cannot be attached directly.")

    download_url = GOOGLE_DRIVE_DOWNLOAD_API_URL.format(file_id=file_id)
    request_params: dict[str, Any] = {"supportsAllDrives": "true"}
    stored_name = _sanitize_import_name(original_name)
    stored_mime_type = mime_type
    if mime_type.startswith("application/vnd.google-apps"):
        export_mime_type, export_suffix, _ = _native_export_settings(mime_type)
        download_url = GOOGLE_DRIVE_EXPORT_API_URL.format(file_id=file_id)
        request_params = {
            "mimeType": export_mime_type,
            "supportsAllDrives": "true",
        }
        stored_name = _sanitize_import_name(original_name, export_suffix)
        stored_mime_type = export_mime_type
    else:
        request_params["alt"] = "media"

    declared_size = int(metadata.get("size")) if str(metadata.get("size") or "").isdigit() else None
    if declared_size is not None:
        if declared_size > max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"File size exceeds limit of {max_upload_mb} MB")
        if declared_size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="Google Drive file exceeds the 512 MB upload limit.")

    temp_handle = tempfile.NamedTemporaryFile(prefix="google-drive-import-", suffix=Path(stored_name).suffix or ".bin", delete=False)
    bytes_written = 0
    try:
        with client.stream(
            "GET",
            download_url,
            params=request_params,
            headers={
                "Authorization": f"Bearer {access_token}",
            },
        ) as response:
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="Google Drive authorization expired. Reconnect the account.")
            if response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=response.text or "Failed to download Google Drive file.")
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                bytes_written += len(chunk)
                if bytes_written > max_upload_bytes:
                    raise HTTPException(status_code=413, detail=f"File size exceeds limit of {max_upload_mb} MB")
                if bytes_written > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="Google Drive file exceeds the 512 MB upload limit.")
                temp_handle.write(chunk)
        temp_handle.close()
    except Exception:
        temp_handle.close()
        os.unlink(temp_handle.name)
        raise

    if bytes_written <= 0:
        os.unlink(temp_handle.name)
        raise HTTPException(status_code=400, detail="Google Drive returned an empty file.")
    return temp_handle.name, stored_name, stored_mime_type


class _ImportedUploadFile:
    def __init__(self, filename: str, file_handle):
        self.filename = filename
        self.file = file_handle

    async def read(self, size: int = -1):
        return self.file.read(size)

    async def seek(self, offset: int):
        self.file.seek(offset)


def complete_google_drive_oauth(
    db,
    *,
    state: str,
    code: str,
    before_connection_commit: Callable[[Any, str], None] | None = None,
) -> dict[str, Any]:
    _assert_google_drive_url_allowed(
        db,
        GOOGLE_OAUTH_TOKEN_URL,
        feature="Google Drive OAuth completion",
    )
    _assert_google_drive_url_allowed(
        db,
        GOOGLE_OAUTH_USERINFO_URL,
        feature="Google Drive profile lookup",
    )
    oauth_result = complete_google_oauth(db, provider=PROVIDER_GOOGLE_DRIVE, state=state, code=code)
    user_id = str(oauth_result.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="OAuth completion did not resolve a user.")
    ensure_group_allows_connection_provider(user_id, db, provider=PROVIDER_GOOGLE_DRIVE)

    existing = get_user_connection_by_provider(db, user_id, PROVIDER_GOOGLE_DRIVE)
    if existing is None:
        connection_change = "created"
        connection = create_user_connection(
            db,
            user_id=user_id,
            provider=PROVIDER_GOOGLE_DRIVE,
            enabled=True,
            auth_mode="oauth",
            secrets=oauth_result["secrets"],
            status=oauth_result["status"],
            connected_at=_utcnow(),
            before_commit=(
                (lambda record: before_connection_commit(record, "created"))
                if before_connection_commit is not None
                else None
            ),
        )
    else:
        connection_change = "updated"
        connection = update_user_connection(
            db,
            existing.id,
            enabled=True,
            auth_mode="oauth",
            secrets=oauth_result["secrets"],
            status=oauth_result["status"],
            connected_at=_utcnow(),
            before_commit=(
                (lambda record: before_connection_commit(record, "updated"))
                if before_connection_commit is not None
                else None
            ),
        )

    return {
        "return_path": str(oauth_result.get("return_path") or "/chat").strip() or "/chat",
        "connected": True,
        "connection_id": connection.id,
        "user_id": user_id,
        "connection_change": connection_change,
    }


def get_google_drive_picker_session_payload(db, *, user_id: str) -> dict[str, Any]:
    """Create a no-store browser session for the official Google Picker.

    Google Picker requires the user's current OAuth access token in the browser.
    Only the short-lived access token is returned; the refresh token and OAuth
    client secret never leave the backend. The selected ids are still validated
    and downloaded by the existing server-side import endpoint.
    """

    ensure_group_allows_connection_provider(user_id, db, provider=PROVIDER_GOOGLE_DRIVE)
    connection = get_user_connection_by_provider(db, user_id, PROVIDER_GOOGLE_DRIVE)
    has_access_token = bool(connection and _connection_has_access_token(connection))
    try:
        picker_settings = google_picker_client_settings(db)
    except HTTPException:
        return {
            "picker_ready": False,
            "connected": has_access_token,
            "error_code": "picker_not_configured",
        }

    if not connection or not has_access_token:
        return {
            "picker_ready": True,
            "connected": False,
            "reauthorization_required": bool(connection),
            "error_code": "drive_not_connected",
        }

    try:
        connection = _refresh_drive_connection_if_needed(db, connection)
    except HTTPException:
        return {
            "picker_ready": True,
            "connected": False,
            "reauthorization_required": True,
            "error_code": "drive_reauthorization_required",
        }

    secrets = connection.secrets if isinstance(connection.secrets, dict) else {}
    access_token = str(secrets.get("access_token") or "").strip()
    if not access_token:
        return {
            "picker_ready": True,
            "connected": False,
            "reauthorization_required": True,
            "error_code": "drive_reauthorization_required",
        }

    expires_at = secrets.get("expires_at")
    try:
        normalized_expires_at = int(expires_at) if expires_at is not None else None
    except (TypeError, ValueError):
        normalized_expires_at = None
    return {
        "picker_ready": True,
        "connected": True,
        "developer_key": picker_settings["developer_key"],
        "app_id": picker_settings["app_id"],
        "access_token": access_token,
        "expires_at": normalized_expires_at,
    }


async def import_google_drive_files_payload(db, *, user_id: str, file_ids: list[str]) -> dict[str, Any]:
    ensure_group_allows_connection_provider(user_id, db, provider=PROVIDER_GOOGLE_DRIVE)
    normalized_ids: list[str] = []
    seen_ids: set[str] = set()
    for raw_id in file_ids or []:
        file_id = str(raw_id or "").strip()
        if not file_id or file_id in seen_ids:
            continue
        seen_ids.add(file_id)
        normalized_ids.append(file_id)
    if not normalized_ids:
        raise HTTPException(status_code=400, detail="Choose at least one Google Drive file.")
    if len(normalized_ids) > GOOGLE_DRIVE_IMPORT_LIMIT:
        raise HTTPException(status_code=400, detail=f"You can import up to {GOOGLE_DRIVE_IMPORT_LIMIT} Google Drive files at once.")

    connection = get_user_connection_by_provider(db, user_id, PROVIDER_GOOGLE_DRIVE)
    if not connection or not _connection_has_access_token(connection):
        raise HTTPException(status_code=400, detail="Connect Google Drive before importing files.")

    connection = _refresh_drive_connection_if_needed(db, connection)
    secrets = connection.secrets if isinstance(connection.secrets, dict) else {}
    access_token = str(secrets.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(status_code=400, detail="Reconnect Google Drive before importing files.")

    imported: list[FileList] = []
    errors: list[dict[str, Any]] = []
    max_upload_bytes, max_upload_mb = resolve_user_max_upload_size_bytes(db, user_id)

    _assert_google_drive_url_allowed(
        db,
        GOOGLE_DRIVE_DOWNLOAD_API_URL,
        feature="Google Drive file metadata lookup",
    )
    _assert_google_drive_url_allowed(
        db,
        GOOGLE_DRIVE_EXPORT_API_URL,
        feature="Google Drive file export",
    )
    with httpx.Client(timeout=GOOGLE_DRIVE_HTTP_TIMEOUT) as client:
        for drive_file_id in normalized_ids:
            temp_path: str | None = None
            import_name: str | None = None
            try:
                metadata = _drive_request_json(
                    client,
                    access_token,
                    GOOGLE_DRIVE_DOWNLOAD_API_URL.format(file_id=drive_file_id),
                    params={
                        "fields": "id,name,mimeType,size,modifiedTime,webViewLink,iconLink,thumbnailLink",
                        "supportsAllDrives": "true",
                    },
                )
                # The picker renders folders as navigation targets.  Reject a
                # crafted import request here too, instead of attempting to
                # download a non-file from the Drive API.
                if str(metadata.get("mimeType") or "").strip() == GOOGLE_DRIVE_FOLDER_MIME:
                    raise HTTPException(status_code=400, detail="Google Drive folders cannot be imported as files.")
                temp_path, import_name, _ = _download_drive_file_to_path(
                    client,
                    access_token,
                    metadata,
                    max_upload_bytes=max_upload_bytes,
                    max_upload_mb=max_upload_mb,
                )
                with open(temp_path, "rb") as handle:
                    upload_result = await upload_file(_ImportedUploadFile(import_name, handle), None, user_id, db)
                file_record = get_file(db, upload_result["file_id"], user_id)
                if not file_record:
                    raise HTTPException(status_code=500, detail="Imported file record was not found after upload.")
                if not upload_result.get("already_uploaded"):
                    meta = deepcopy(file_record.meta if isinstance(file_record.meta, dict) else {})
                    meta.update(
                        {
                            "google_drive_file_id": str(metadata.get("id") or "").strip() or None,
                            "google_drive_source_mime_type": str(metadata.get("mimeType") or "").strip() or None,
                            "google_drive_web_view_url": str(metadata.get("webViewLink") or "").strip() or None,
                            "google_drive_imported_at": _utcnow().isoformat(),
                        }
                    )
                    file_record.meta = meta
                    db.commit()
                    db.refresh(file_record)
                imported.append(FileList.model_validate(file_record))
            except HTTPException as exc:
                errors.append(
                    {
                        "file_id": drive_file_id,
                        "name": import_name,
                        "message": str(exc.detail),
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "file_id": drive_file_id,
                        "name": import_name,
                        "message": str(exc),
                    }
                )
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)

    return {
        "imported": imported,
        "errors": errors,
        "imported_count": len(imported),
    }


def _import_google_drive_files_with_thread_session(
    user_id: str,
    file_ids: list[str],
) -> dict[str, Any]:
    """Run the legacy synchronous import stack with a thread-owned session."""

    from app.database import SessionLocal

    session = SessionLocal()
    try:
        async def _run() -> dict[str, Any]:
            return await import_google_drive_files_payload(
                session,
                user_id=user_id,
                file_ids=file_ids,
            )

        return anyio.run(_run)
    finally:
        session.close()


async def import_google_drive_files_off_event_loop(
    *,
    user_id: str,
    file_ids: list[str],
) -> dict[str, Any]:
    """Keep the inline compatibility path from blocking an ASGI worker."""

    return await run_blocking_io(
        _import_google_drive_files_with_thread_session,
        str(user_id),
        list(file_ids),
    )
