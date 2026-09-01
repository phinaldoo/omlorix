from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import threading
import time

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.utils import hash_password, verify_password
from app.files.models import FileArtifactShare, Files
from app.files.canvas_assets import (
    CanvasAssetAccessError,
    get_canvas_source_for_artifact,
    is_canvas_artifact_dependency_snapshot_current,
    prepare_public_canvas_assets_payload,
)
from app.files.utils import materialize_file_record
from app.groups.init import get_user_group_setting_value
from app.redis_client import get_redis_client
from app.settings.utils import get_public_url


SHAREABLE_MIME_TO_ARTIFACT_TYPE = {
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "text/html": "html",
    "application/html": "html",
    "application/xhtml+xml": "html",
    "application/x-html": "html",
    "text/xhtml": "html",
    "text/css": "css",
    "text/x-mermaid": "mermaid",
    "application/pdf": "pdf",
}

SHAREABLE_ARTIFACT_TYPES = frozenset({"markdown", "html", "css", "mermaid", "pdf"})
CANVAS_ARTIFACT_TYPE_TO_MIME = {
    "markdown": "text/markdown",
    "html": "text/html",
    "css": "text/css",
    "mermaid": "text/x-mermaid",
    "pdf": "application/pdf",
}
# Canvas preview intentionally supports filename detection because some storage
# providers preserve a document's name but return application/octet-stream.
# Keep share eligibility in sync with that preview behavior.
SHAREABLE_FILENAME_TO_ARTIFACT_TYPE = {
    ".markdown": "markdown",
    ".html": "html",
    ".xhtml": "html",
    ".xhtm": "html",
    ".shtml": "html",
    ".shtm": "html",
    ".mermaid": "mermaid",
    ".md": "markdown",
    ".htm": "html",
    ".xht": "html",
    ".css": "css",
    ".mmd": "mermaid",
    ".pdf": "pdf",
}
ARTIFACT_SHARE_MIN_PASSWORD_LENGTH = 8
ARTIFACT_SHARE_MAX_PASSWORD_LENGTH = 256
ARTIFACT_SHARE_DEFAULT_EXPIRES_IN_HOURS = 24
ARTIFACT_SHARE_MAX_EXPIRES_IN_HOURS = 24 * 30
ARTIFACT_SHARE_PASSWORD_ATTEMPT_LIMIT = 5
ARTIFACT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS = 10 * 60
ARTIFACT_SHARE_MAX_CONTENT_BYTES = 2 * 1024 * 1024
ARTIFACT_SHARE_ACCESS_LIMIT = 30
ARTIFACT_SHARE_ACCESS_WINDOW_SECONDS = 60
PDF_ARTIFACT_SHARE_MAX_CONTENT_BYTES = 15 * 1024 * 1024
_ARTIFACT_SHARE_PASSWORD_ATTEMPTS_MAX_SIZE = 10000
_ARTIFACT_SHARE_PASSWORD_ATTEMPT_LOCK = threading.Lock()
_ARTIFACT_SHARE_PASSWORD_ATTEMPTS: dict[str, tuple[int, float]] = {}
_ARTIFACT_SHARE_ACCESS_COUNTS_MAX_SIZE = 20000
_ARTIFACT_SHARE_ACCESS_LOCK = threading.Lock()
_ARTIFACT_SHARE_ACCESS_COUNTS: dict[str, tuple[int, float]] = {}


def _utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _normalize_utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize datetime to UTC, adding timezone if naive."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ensure_artifact_sharing_enabled_for_user(user_id: str, db: Session) -> None:
    """Raise HTTP 403 if artifact sharing is disabled for the user's group."""
    enabled = get_user_group_setting_value(user_id, "sharing", "enable_artifact_sharing", db)
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Canvas sharing is disabled for your group",
        )


def ensure_artifact_file_sharing_allowed_for_user(user_id: str, db: Session) -> None:
    """Raise HTTP 403 if artifact file sharing is disabled for the user's group."""
    ensure_artifact_sharing_enabled_for_user(user_id, db)


def artifact_file_has_existing_share_state(db: Session, user_id: str, file_id: str) -> bool:
    """Return true when a canvas artifact already has active share links."""
    cleaned_file_id = str(file_id).strip()
    now = _utcnow()
    query = (
        db.query(FileArtifactShare)
        .filter(
            FileArtifactShare.file_id == cleaned_file_id,
            FileArtifactShare.user_id == user_id,
            or_(FileArtifactShare.expires_at.is_(None), FileArtifactShare.expires_at > now),
        )
    )

    def row_matches_active_share(row) -> bool:
        return bool(
            row is not None
            and getattr(row, "file_id", None) == cleaned_file_id
            and getattr(row, "user_id", None) == user_id
            and not is_artifact_share_expired(row, now=now)
        )

    count_method = getattr(query, "count", None)
    if callable(count_method):
        count_result = count_method()
        if isinstance(count_result, int):
            return count_result > 0

    first_method = getattr(query, "first", None)
    if callable(first_method):
        if row_matches_active_share(first_method()):
            return True

    all_method = getattr(query, "all", None)
    if callable(all_method):
        return any(row_matches_active_share(row) for row in all_method())

    return False


def _get_shareable_canvas_artifact_type(file_record: Files) -> str | None:
    """Return the supported Canvas artifact type for an owned file.

    Canvas previews accept both conventional MIME types and a supported filename
    when storage reports a generic MIME type. Sharing must use the same rule so
    a user-uploaded Canvas document behaves exactly like a tool-created one.
    """
    mime_type = str(file_record.file_type or "").strip().lower()
    artifact_type = SHAREABLE_MIME_TO_ARTIFACT_TYPE.get(mime_type)
    if artifact_type:
        return artifact_type

    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    canvas_type = str(meta.get("canvas_type") or "").strip().lower()
    if canvas_type in SHAREABLE_ARTIFACT_TYPES:
        return canvas_type

    original_name = str(meta.get("original_filename") or file_record.file_name or "").strip().lower()
    for suffix, detected_type in SHAREABLE_FILENAME_TO_ARTIFACT_TYPE.items():
        if original_name.endswith(suffix):
            return detected_type
    return None


def _is_shareable_canvas_artifact(file_record: Files) -> bool:
    """Return whether a file can safely use the public Canvas share renderer."""
    return _get_shareable_canvas_artifact_type(file_record) is not None


def _get_owned_file(db: Session, user_id: str, file_id: str) -> Files:
    """Get a file owned by the user, raising 404 if not found."""
    file_record = (
        db.query(Files)
        .filter(Files.id == str(file_id).strip(), Files.user_id == user_id)
        .first()
    )
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return file_record


def _ensure_shareable_file(file_record: Files) -> None:
    """Raise HTTP 400 if a file is outside the Canvas share format allowlist."""
    if not _is_shareable_canvas_artifact(file_record):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Canvas-compatible Markdown, Mermaid, HTML, CSS, and PDF files can be shared",
        )


def _coerce_nonnegative_file_size(value) -> int | None:
    try:
        size = int(value)
    except Exception:
        return None
    return max(0, size)


def _raise_oversized_artifact(limit_bytes: int = ARTIFACT_SHARE_MAX_CONTENT_BYTES) -> None:
    max_mb = max(1, limit_bytes // (1024 * 1024))
    raise HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"Shared canvas exceeds the public canvas size limit of {max_mb} MB",
    )


def _artifact_share_size_limit(file_record: Files) -> int:
    # Use the resolved Canvas type rather than the storage MIME type: a PDF
    # uploaded through a provider can be stored as application/octet-stream.
    if _get_shareable_canvas_artifact_type(file_record) == "pdf":
        return PDF_ARTIFACT_SHARE_MAX_CONTENT_BYTES
    return ARTIFACT_SHARE_MAX_CONTENT_BYTES


def _ensure_shareable_artifact_size(file_record: Files) -> None:
    file_size = _coerce_nonnegative_file_size(getattr(file_record, "file_size", None))
    limit = _artifact_share_size_limit(file_record)
    if file_size is not None and file_size > limit:
        _raise_oversized_artifact(limit)


def _build_share_url(base_url: str, share_id: str) -> str:
    """Build the public canvas share URL."""
    normalized_base = str(base_url or "").rstrip("/")
    return f"{normalized_base}/canvas/shared/{share_id}"


def _serialize_share_row(share: FileArtifactShare, base_url: str) -> dict:
    """Serialize a share row to a dict with share URL and metadata."""
    return {
        "share_id": share.id,
        "share_url": _build_share_url(base_url, share.id),
        "has_password": bool(share.password_hash),
        "created_at": share.created_at,
        "expires_at": _normalize_utc_datetime(share.expires_at),
        "last_accessed_at": _normalize_utc_datetime(getattr(share, "last_accessed_at", None)),
        "access_count": int(getattr(share, "access_count", 0) or 0),
    }


def _normalize_expires_in_hours(expires_in_hours: int | None) -> int:
    """Normalize the requested share duration to a safe hour value."""
    if expires_in_hours is None:
        return ARTIFACT_SHARE_DEFAULT_EXPIRES_IN_HOURS
    try:
        normalized = int(expires_in_hours)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expires_in_hours must be an integer",
        ) from exc
    if normalized < 1 or normalized > ARTIFACT_SHARE_MAX_EXPIRES_IN_HOURS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"expires_in_hours must be between 1 and {ARTIFACT_SHARE_MAX_EXPIRES_IN_HOURS}",
        )
    return normalized


def _normalize_share_expiry_datetime(expires_at: datetime | None) -> datetime | None:
    """Normalize a share expiry timestamp and enforce the artifact share lifetime policy."""
    normalized = _normalize_utc_datetime(expires_at)
    if normalized is None:
        return None
    now = _utcnow()
    if normalized <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Share expiry must be in the future",
        )
    max_expires_at = now + timedelta(hours=ARTIFACT_SHARE_MAX_EXPIRES_IN_HOURS)
    if normalized > max_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Share expiry must be within {ARTIFACT_SHARE_MAX_EXPIRES_IN_HOURS} hours",
        )
    return normalized


def is_artifact_share_expired(
    share: FileArtifactShare | None = None,
    *,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Return True when an artifact share has an expiry at or before the given time."""
    resolved_expires_at = expires_at if expires_at is not None else getattr(share, "expires_at", None)
    normalized_expires_at = _normalize_utc_datetime(resolved_expires_at)
    if normalized_expires_at is None:
        return False
    current_time = _normalize_utc_datetime(now) or _utcnow()
    return normalized_expires_at <= current_time


def delete_expired_artifact_shares(
    db: Session,
    *,
    now: datetime | None = None,
    user_id: str | None = None,
    file_id: str | None = None,
    share_id: str | None = None,
    commit: bool = True,
) -> int:
    """Delete expired artifact share rows and return the number removed."""
    query = db.query(FileArtifactShare).filter(FileArtifactShare.expires_at.isnot(None))

    normalized_user_id = str(user_id or "").strip()
    if normalized_user_id:
        query = query.filter(FileArtifactShare.user_id == normalized_user_id)

    normalized_file_id = str(file_id or "").strip()
    if normalized_file_id:
        query = query.filter(FileArtifactShare.file_id == normalized_file_id)

    normalized_share_id = str(share_id or "").strip()
    if normalized_share_id:
        query = query.filter(FileArtifactShare.id == normalized_share_id)

    current_time = _normalize_utc_datetime(now) or _utcnow()
    deleted = 0
    for share in query.all():
        if not is_artifact_share_expired(share, now=current_time):
            continue
        db.delete(share)
        deleted += 1

    if deleted and commit:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise

    return deleted


def _normalize_password(
    password: str | None,
    *,
    required: bool,
    validate_length: bool = True,
) -> str | None:
    """Validate and normalize a password, raising 400 if required but missing."""
    if password is None:
        if required:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password is required")
        return None
    normalized = str(password).strip()
    if not normalized:
        if required:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password is required")
        return None
    if validate_length and len(normalized) < ARTIFACT_SHARE_MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Share password must be at least {ARTIFACT_SHARE_MIN_PASSWORD_LENGTH} characters long",
        )
    if validate_length and len(normalized) > ARTIFACT_SHARE_MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Share password must be at most {ARTIFACT_SHARE_MAX_PASSWORD_LENGTH} characters long",
        )
    return normalized


def _cleanup_stale_password_attempts() -> None:
    now = time.time()
    with _ARTIFACT_SHARE_PASSWORD_ATTEMPT_LOCK:
        if len(_ARTIFACT_SHARE_PASSWORD_ATTEMPTS) <= _ARTIFACT_SHARE_PASSWORD_ATTEMPTS_MAX_SIZE:
            return
        stale_keys = [
            key
            for key, (_count, reset_at) in _ARTIFACT_SHARE_PASSWORD_ATTEMPTS.items()
            if reset_at <= now
        ]
        for key in stale_keys:
            _ARTIFACT_SHARE_PASSWORD_ATTEMPTS.pop(key, None)


def _artifact_share_password_attempt_key(share_id: str, client_ip: str | None) -> str:
    material = f"{str(share_id or '').strip()}:{str(client_ip or 'unknown').strip() or 'unknown'}"
    digest = hashlib.sha256(material.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"omlorix:shared-artifact:password-attempts:{digest}"


def _artifact_share_access_key(share_id: str, client_ip: str | None) -> str:
    material = f"{str(share_id or '').strip()}:{str(client_ip or 'unknown').strip() or 'unknown'}"
    digest = hashlib.sha256(material.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"omlorix:shared-artifact:access:{digest}"


def _get_local_password_attempt_count(key: str) -> int:
    now = time.time()
    with _ARTIFACT_SHARE_PASSWORD_ATTEMPT_LOCK:
        count, reset_at = _ARTIFACT_SHARE_PASSWORD_ATTEMPTS.get(key, (0, 0.0))
        if reset_at <= now:
            _ARTIFACT_SHARE_PASSWORD_ATTEMPTS.pop(key, None)
            return 0
        return count


def _increment_local_password_attempt_count(key: str) -> int:
    _cleanup_stale_password_attempts()
    now = time.time()
    with _ARTIFACT_SHARE_PASSWORD_ATTEMPT_LOCK:
        count, reset_at = _ARTIFACT_SHARE_PASSWORD_ATTEMPTS.get(key, (0, 0.0))
        if reset_at <= now:
            count = 0
            reset_at = now + ARTIFACT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS
        count += 1
        _ARTIFACT_SHARE_PASSWORD_ATTEMPTS[key] = (count, reset_at)
        return count


def _clear_local_password_attempt_count(key: str) -> None:
    with _ARTIFACT_SHARE_PASSWORD_ATTEMPT_LOCK:
        _ARTIFACT_SHARE_PASSWORD_ATTEMPTS.pop(key, None)


def _get_password_attempt_count(key: str) -> int:
    client = get_redis_client()
    if client is not None:
        try:
            return int(client.get(key) or 0)
        except Exception:
            pass
    return _get_local_password_attempt_count(key)


def _record_password_failure(key: str) -> int:
    client = get_redis_client()
    if client is not None:
        try:
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, ARTIFACT_SHARE_PASSWORD_ATTEMPT_WINDOW_SECONDS)
            return count
        except Exception:
            pass
    return _increment_local_password_attempt_count(key)


def _clear_password_failures(key: str) -> None:
    client = get_redis_client()
    if client is not None:
        try:
            client.delete(key)
            return
        except Exception:
            pass
    _clear_local_password_attempt_count(key)


def _enforce_password_attempt_limit(share_id: str, client_ip: str | None) -> str:
    key = _artifact_share_password_attempt_key(share_id, client_ip)
    if _get_password_attempt_count(key) >= ARTIFACT_SHARE_PASSWORD_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many invalid password attempts. Please retry later.",
        )
    return key


def _cleanup_stale_access_counts() -> None:
    now = time.time()
    with _ARTIFACT_SHARE_ACCESS_LOCK:
        if len(_ARTIFACT_SHARE_ACCESS_COUNTS) <= _ARTIFACT_SHARE_ACCESS_COUNTS_MAX_SIZE:
            return
        stale_keys = [
            key
            for key, (_count, reset_at) in _ARTIFACT_SHARE_ACCESS_COUNTS.items()
            if reset_at <= now
        ]
        for key in stale_keys:
            _ARTIFACT_SHARE_ACCESS_COUNTS.pop(key, None)


def _record_local_access_count(key: str) -> int:
    _cleanup_stale_access_counts()
    now = time.time()
    with _ARTIFACT_SHARE_ACCESS_LOCK:
        count, reset_at = _ARTIFACT_SHARE_ACCESS_COUNTS.get(key, (0, 0.0))
        if reset_at <= now:
            count = 0
            reset_at = now + ARTIFACT_SHARE_ACCESS_WINDOW_SECONDS
        count += 1
        _ARTIFACT_SHARE_ACCESS_COUNTS[key] = (count, reset_at)
        return count


def _record_access_count(key: str) -> int:
    client = get_redis_client()
    if client is not None:
        try:
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, ARTIFACT_SHARE_ACCESS_WINDOW_SECONDS)
            return count
        except Exception:
            pass
    return _record_local_access_count(key)


def enforce_shared_artifact_access_rate_limit(share_id: str, client_ip: str | None) -> None:
    """Rate limit all public canvas access attempts for a share/IP pair."""
    key = _artifact_share_access_key(share_id, client_ip)
    if _record_access_count(key) > ARTIFACT_SHARE_ACCESS_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many shared canvas access attempts. Please retry later.",
        )


def create_artifact_share(
    db: Session,
    user_id: str,
    file_id: str,
    password: str | None = None,
    expires_in_hours: int = 24,
) -> dict:
    """Create a share link for a canvas artifact with optional password and expiration."""
    ensure_artifact_file_sharing_allowed_for_user(user_id, db)
    file_record = _get_owned_file(db, user_id, file_id)
    _ensure_shareable_file(file_record)
    _ensure_shareable_artifact_size(file_record)

    normalized_password = _normalize_password(password, required=False)
    normalized_hours = _normalize_expires_in_hours(expires_in_hours)
    created_at = _utcnow()
    share = FileArtifactShare(
        file_id=file_record.id,
        user_id=user_id,
        password_hash=hash_password(normalized_password) if normalized_password else None,
        created_at=created_at,
        expires_at=created_at + timedelta(hours=normalized_hours),
    )
    db.add(share)
    db.commit()
    db.refresh(share)

    return _serialize_share_row(share, get_public_url(db))


def get_artifact_share_status(db: Session, user_id: str, file_id: str) -> dict:
    """Get active share links for a file."""
    file_record = _get_owned_file(db, user_id, file_id)
    _ensure_shareable_file(file_record)

    # Reading existing share state is deliberately available even when an
    # administrator has disabled creation of new links. The Canvas header uses
    # this endpoint while opening a preview so owners can still discover and
    # revoke links that predate the policy change. Creation remains gated in
    # create_artifact_share().

    now = _utcnow()
    try:
        delete_expired_artifact_shares(db, user_id=user_id, file_id=file_record.id, now=now)
    except Exception:
        db.rollback()

    shares = (
        db.query(FileArtifactShare)
        .filter(
            FileArtifactShare.file_id == file_record.id,
            FileArtifactShare.user_id == user_id,
        )
        .order_by(FileArtifactShare.created_at.desc())
        .all()
    )
    active_shares = [
        share
        for share in shares
        if not is_artifact_share_expired(share, now=now)
    ]
    base_url = get_public_url(db)
    return {
        "file_id": file_record.id,
        "links": [_serialize_share_row(share, base_url) for share in active_shares],
    }


def _get_owned_share(db: Session, user_id: str, share_id: str) -> FileArtifactShare:
    """Get a share owned by the user, raising 404 if not found."""
    share = (
        db.query(FileArtifactShare)
        .filter(FileArtifactShare.id == str(share_id).strip(), FileArtifactShare.user_id == user_id)
        .first()
    )
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")
    return share


def delete_artifact_share(db: Session, user_id: str, share_id: str) -> dict:
    """Delete a share link."""
    share = _get_owned_share(db, user_id, share_id)
    db.delete(share)
    db.commit()
    return {"ok": True}


def change_artifact_share_password(db: Session, user_id: str, share_id: str, password: str) -> dict:
    """Change the password for a share link."""
    share = _get_owned_share(db, user_id, share_id)
    normalized_password = _normalize_password(password, required=True)
    share.password_hash = hash_password(normalized_password)
    db.commit()
    return {"share_id": share.id, "has_password": True}


def remove_artifact_share_password(db: Session, user_id: str, share_id: str) -> dict:
    """Remove the password from a share link."""
    share = _get_owned_share(db, user_id, share_id)
    share.password_hash = None
    db.commit()
    return {"share_id": share.id, "has_password": False}


def change_artifact_share_expiry(
    db: Session,
    user_id: str,
    share_id: str,
    expires_at: datetime,
) -> dict:
    """Change the expiry for a share link."""
    share = _get_owned_share(db, user_id, share_id)
    share.expires_at = _normalize_share_expiry_datetime(expires_at)
    db.commit()
    return {"share_id": share.id, "expires_at": _normalize_utc_datetime(share.expires_at)}


def remove_artifact_share_expiry(db: Session, user_id: str, share_id: str) -> dict:
    """Reject removing expiry from a canvas share link.

    Canvas share links must always expire within the maximum lifetime policy.
    """
    _get_owned_share(db, user_id, share_id)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Canvas share links must keep an expiry",
    )


def _resolve_artifact_content_path(file_record: Files) -> Path:
    """Resolve the local filesystem path for a shared canvas file."""
    return materialize_file_record(file_record, file_record.user_id)


def resolve_shared_artifact_access(
    db: Session,
    share_id: str,
    password: str | None = None,
    client_ip: str | None = None,
) -> dict:
    """Resolve access to a shared canvas, verifying password if required."""
    cleaned_share_id = str(share_id or "").strip()
    if not cleaned_share_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="share_id is required")

    share = db.query(FileArtifactShare).filter(FileArtifactShare.id == cleaned_share_id).first()
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared canvas not found")

    try:
        ensure_artifact_sharing_enabled_for_user(share.user_id, db)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared canvas not found")

    if is_artifact_share_expired(share):
        try:
            db.delete(share)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared canvas not found")

    file_record = db.query(Files).filter(Files.id == share.file_id, Files.user_id == share.user_id).first()
    if not file_record:
        try:
            db.delete(share)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared canvas not found")

    if not _is_shareable_canvas_artifact(file_record):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared canvas not found")
    _ensure_shareable_artifact_size(file_record)

    if share.password_hash:
        normalized_password = _normalize_password(password, required=False, validate_length=False)
        if not normalized_password:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Password required")
        attempt_key = _enforce_password_attempt_limit(cleaned_share_id, client_ip)
        if not verify_password(normalized_password, share.password_hash):
            _record_password_failure(attempt_key)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
        _clear_password_failures(attempt_key)

    expires_at = _normalize_utc_datetime(share.expires_at)

    artifact_type = _get_shareable_canvas_artifact_type(file_record)
    if not artifact_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared canvas not found")

    # Public links are a separate audience from authenticated Canvas members.
    # Re-check every owner decision on every access so revocation immediately
    # disables both live dependencies and already-rendered PDF shares.
    source_canvas = get_canvas_source_for_artifact(db, file_record)
    if not is_canvas_artifact_dependency_snapshot_current(file_record, source_canvas):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shared canvas not found",
        )
    try:
        public_assets = prepare_public_canvas_assets_payload(
            db,
            canvas_record=source_canvas,
            include_content=artifact_type != "pdf",
        )
    except (CanvasAssetAccessError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shared canvas not found",
        )

    content_path = _resolve_artifact_content_path(file_record)
    if not content_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared canvas not found")
    size_limit = _artifact_share_size_limit(file_record)
    try:
        if content_path.stat().st_size > size_limit:
            _raise_oversized_artifact(size_limit)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to load shared canvas")

    try:
        if artifact_type == "pdf":
            content = base64.b64encode(content_path.read_bytes()).decode("ascii")
            encoding = "base64"
        else:
            content = content_path.read_text(encoding="utf-8", errors="replace")
            encoding = "text"
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to load shared canvas")

    share.last_accessed_at = datetime.now(timezone.utc)
    share.access_count = int(getattr(share, "access_count", 0) or 0) + 1
    db.commit()

    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    original_name = str(meta.get("original_filename") or "").strip()

    return {
        "share_id": share.id,
        "file_name": original_name or file_record.file_name,
        "artifact_type": artifact_type,
        # Return the canonical type to the public renderer. This is especially
        # important for filename-detected PDFs, whose storage MIME can be the
        # generic application/octet-stream and would otherwise not render.
        "mime_type": CANVAS_ARTIFACT_TYPE_TO_MIME[artifact_type],
        "expires_at": expires_at,
        "has_password": bool(share.password_hash),
        "encoding": encoding,
        "content": content,
        "assets": public_assets,
    }
