"""Canvas-scoped file references and owner approval.

Canvas source metadata stores references because the references are part of the
portable artifact and therefore already participate in file import/export.  The
metadata is never treated as authority: every use re-resolves the Canvas, the
asset row, its current owner, and the recorded approval status.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import logging
import re
import uuid
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.files.access import ResolvedFileAccess, resolve_file_for_read
from app.files.models import CanvasAssetGrant, Files
from app.files.utils import materialize_file_record


CANVAS_ASSET_REFERENCES_META_KEY = "canvas_asset_references"
CANVAS_ASSET_REFERENCE_LIMIT = 20
CANVAS_ASSET_STATUS_ACTIVE = "active"
CANVAS_ASSET_STATUS_PENDING = "pending"
CANVAS_ASSET_STATUS_REJECTED = "rejected"
CANVAS_ASSET_STATUS_REVOKED = "revoked"
CANVAS_ASSET_VISIBILITY_MEMBERS = "canvas_members"
CANVAS_ASSET_VISIBILITY_PUBLIC = "public"
CANVAS_PUBLIC_ASSET_TOTAL_BYTES = 20 * 1024 * 1024

logger = logging.getLogger(__name__)

_OMLORIX_FILE_URL_RE = re.compile(
    r"omlorix-file://([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})",
    re.IGNORECASE,
)
_DOWNLOAD_URL_RE = re.compile(
    r"(?:https?://[^\s\"'()<>]+)?/api/v1/files/download\?[^\s\"'()<>]+",
    re.IGNORECASE,
)


class CanvasAssetAccessError(PermissionError):
    """Raised when a Canvas reference is absent, pending, or unauthorized."""

    code = "canvas_asset_access_denied"


def _utc_iso() -> str:
    """Return a stable UTC timestamp for portable Canvas metadata."""

    return datetime.now(timezone.utc).isoformat()


def _utc_now() -> datetime:
    """Return an aware timestamp for authoritative database rows."""

    return datetime.now(timezone.utc)


def normalize_canvas_asset_ids(values: Iterable[Any] | None) -> list[str]:
    """Normalize, deduplicate, and bound an ordered asset-ID collection."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        if not isinstance(value, str):
            continue
        file_id = value.strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        normalized.append(file_id)
        if len(normalized) >= CANVAS_ASSET_REFERENCE_LIMIT:
            break
    return normalized


def extract_canvas_asset_ids(content: str | None) -> list[str]:
    """Extract explicit Omlorix file references from Markdown/HTML/CSS source."""

    source = str(content or "")
    discovered = list(_OMLORIX_FILE_URL_RE.findall(source))
    for raw_url in _DOWNLOAD_URL_RE.findall(source):
        try:
            parsed = urlparse(raw_url)
            file_id = str((parse_qs(parsed.query).get("file_id") or [""])[0]).strip()
        except Exception:
            file_id = ""
        if file_id:
            discovered.append(file_id)
    return normalize_canvas_asset_ids(discovered)


def validate_canvas_asset_ids_for_actor(
    db: Session,
    *,
    actor_user_id: str,
    asset_file_ids: Iterable[Any] | None,
) -> list[str]:
    """Validate IDs against the authenticated actor without changing grants.

    Model-tool saves create their file record inside the persistence helper, so
    no Canvas row exists yet for a grant foreign key.  This read-only preflight
    still prevents an invalid or guessed ID from being committed to source
    metadata.  The authoritative Canvas grants are created immediately after
    the source row exists.
    """

    normalized_ids = normalize_canvas_asset_ids(asset_file_ids)
    for asset_file_id in normalized_ids:
        if not resolve_file_for_read(db, str(actor_user_id), asset_file_id):
            raise CanvasAssetAccessError(CanvasAssetAccessError.code)
    return normalized_ids


def _notification_actor_name(user: Any) -> str:
    """Return a bounded human-readable actor name for notification fallback."""

    if not user:
        return "Unknown"
    name = " ".join(
        value
        for value in (
            str(getattr(user, "first_name", "") or "").strip(),
            str(getattr(user, "last_name", "") or "").strip(),
        )
        if value
    ).strip()
    return str(name or getattr(user, "email", "") or "Unknown")[:255]


def notify_canvas_asset_approval_requests(
    db: Session,
    *,
    actor_user_id: str,
    canvas_record: Files,
    references: Iterable[dict[str, Any]],
    public: bool = False,
) -> None:
    """Send deduplicated actionable notifications to the real asset owners.

    Notification delivery is intentionally best effort.  The authoritative
    grant remains pending if delivery fails, so a messaging outage can never
    turn into implicit access.  Structured details let every frontend locale
    render the action without exposing internal IDs in generic text.
    """

    from app.userNotifications.models import (  # Local import avoids model cycles.
        UserNotifications,
        create_user_notification,
    )
    from app.users.models import get_user

    pending = [reference for reference in references if isinstance(reference, dict)]
    if not pending:
        return

    owner_ids = {
        str(reference.get("asset_owner_user_id") or "").strip()
        for reference in pending
        if str(reference.get("asset_owner_user_id") or "").strip()
    }
    recipient_filters = [
        UserNotifications.user_ids.like(f"%|{owner_id}|%")
        for owner_id in owner_ids
    ]
    existing_request_ids: set[str] = set()
    if recipient_filters:
        existing_request_ids = {
            str((row.details or {}).get("request_id") or "")
            for row in db.query(UserNotifications)
            .filter(
                UserNotifications.category == "canvas_assets",
                or_(*recipient_filters),
            )
            .limit(500)
            .all()
            if isinstance(row.details, dict)
        }

    actor_name = _notification_actor_name(get_user(db, str(actor_user_id)))
    canvas_meta = (
        canvas_record.meta if isinstance(getattr(canvas_record, "meta", None), dict) else {}
    )
    canvas_name = str(
        canvas_meta.get("original_filename")
        or getattr(canvas_record, "file_name", "")
        or "Canvas"
    )[:255]
    for reference in pending:
        request_key = "public_request_id" if public else "request_id"
        request_id = str(reference.get(request_key) or "").strip()
        owner_user_id = str(reference.get("asset_owner_user_id") or "").strip()
        if not request_id or not owner_user_id or request_id in existing_request_ids:
            continue
        try:
            asset_name = str(reference.get("asset_name") or "a file")[:255]
            create_user_notification(
                db,
                message=(
                    f"{actor_name} requested permission to "
                    f"{'publish' if public else 'use'} {asset_name} "
                    f"{'through' if public else 'in'} {canvas_name}."
                ),
                category="canvas_assets",
                notification_type="warning" if public else "info",
                user_ids=[owner_user_id],
                details={
                    "type": "canvas_asset_approval",
                    "scope": "public" if public else "canvas_members",
                    "canvas_file_id": str(canvas_record.id),
                    "canvas_title": canvas_name,
                    "asset_file_id": str(reference.get("file_id") or ""),
                    "asset_name": asset_name,
                    "request_id": request_id,
                    "requester_id": str(actor_user_id),
                    "requester_name": actor_name,
                },
            )
            existing_request_ids.add(request_id)
        except Exception:
            db.rollback()
            logger.exception("Failed to send Canvas asset approval notification")


def get_canvas_asset_references(file_record: Files | Any) -> list[dict[str, Any]]:
    """Return sanitized structured references from untrusted file metadata."""

    meta = (
        file_record.meta if isinstance(getattr(file_record, "meta", None), dict) else {}
    )
    raw_references = meta.get(CANVAS_ASSET_REFERENCES_META_KEY)
    if not isinstance(raw_references, list):
        return []

    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_reference in raw_references[:CANVAS_ASSET_REFERENCE_LIMIT]:
        if not isinstance(raw_reference, dict):
            continue
        file_id = str(raw_reference.get("file_id") or "").strip()
        owner_user_id = str(raw_reference.get("asset_owner_user_id") or "").strip()
        status = str(raw_reference.get("status") or "").strip().lower()
        if not file_id or not owner_user_id or file_id in seen:
            continue
        if status not in {
            CANVAS_ASSET_STATUS_ACTIVE,
            CANVAS_ASSET_STATUS_PENDING,
            CANVAS_ASSET_STATUS_REJECTED,
            CANVAS_ASSET_STATUS_REVOKED,
        }:
            continue
        seen.add(file_id)
        references.append(dict(raw_reference))
    return references


def _asset_display_name(asset: Files) -> str:
    """Return a bounded display name suitable for approval notifications."""

    meta = asset.meta if isinstance(asset.meta, dict) else {}
    return str(meta.get("original_filename") or asset.file_name or "file")[:255]


def _get_grant(
    db: Session,
    *,
    canvas_file_id: str,
    asset_file_id: str,
) -> CanvasAssetGrant | None:
    """Load one Canvas/asset grant from the authoritative store."""

    return (
        db.query(CanvasAssetGrant)
        .filter(
            CanvasAssetGrant.canvas_file_id == str(canvas_file_id),
            CanvasAssetGrant.asset_file_id == str(asset_file_id),
        )
        .first()
    )


def _reference_from_grant(
    grant: CanvasAssetGrant,
    *,
    canvas_record: Files,
    asset: Files,
) -> dict[str, Any]:
    """Build the portable, non-authoritative mirror of a database grant."""

    return {
        "request_id": str(grant.id),
        "file_id": str(asset.id),
        "asset_owner_user_id": str(asset.user_id),
        "asset_name": _asset_display_name(asset),
        "added_by_user_id": str(grant.added_by_user_id),
        "authorized_by_user_id": str(grant.authorized_by_user_id or ""),
        "status": str(grant.status),
        "visibility": str(grant.visibility),
        "created_at": grant.created_at.isoformat() if grant.created_at else _utc_iso(),
        "authorized_at": grant.authorized_at.isoformat()
        if grant.authorized_at
        else None,
        "public_status": str(grant.public_status or "not_requested"),
        "public_request_id": str(grant.public_request_id or ""),
        "public_authorized_by_user_id": str(grant.public_authorized_by_user_id or ""),
        "public_authorized_at": (
            grant.public_authorized_at.isoformat()
            if grant.public_authorized_at
            else None
        ),
        # The folder is provenance only. Current access is always re-evaluated.
        "canvas_folder_id": str(getattr(canvas_record, "folder_id", None) or ""),
    }


def _upsert_grant(
    db: Session,
    *,
    canvas_record: Files,
    asset: Files,
    actor_user_id: str,
    status: str,
    authorized_by_user_id: str | None,
) -> CanvasAssetGrant:
    """Create or refresh a grant in the Canvas save transaction."""

    now = _utc_now()
    grant = _get_grant(
        db,
        canvas_file_id=str(canvas_record.id),
        asset_file_id=str(asset.id),
    )
    if grant is None:
        grant = CanvasAssetGrant(
            id=str(uuid.uuid4()),
            canvas_file_id=str(canvas_record.id),
            asset_file_id=str(asset.id),
            asset_owner_user_id=str(asset.user_id),
            added_by_user_id=str(actor_user_id),
            status=status,
            visibility=CANVAS_ASSET_VISIBILITY_MEMBERS,
            public_status="not_requested",
            created_at=now,
            updated_at=now,
        )
    else:
        # A recycled file ID must not inherit another owner's approval.
        if str(grant.asset_owner_user_id) != str(asset.user_id):
            grant.asset_owner_user_id = str(asset.user_id)
            grant.added_by_user_id = str(actor_user_id)
            grant.public_status = "not_requested"
            grant.public_request_id = None
            grant.public_authorized_by_user_id = None
            grant.public_authorized_at = None
            grant.created_at = now
        grant.status = status
        grant.updated_at = now
    grant.authorized_by_user_id = (
        str(authorized_by_user_id) if authorized_by_user_id else None
    )
    grant.authorized_at = now if authorized_by_user_id else None
    db.add(grant)
    db.flush()
    return grant


def build_canvas_asset_references(
    db: Session,
    *,
    canvas_record: Files,
    actor_user_id: str,
    asset_file_ids: Iterable[Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate requested IDs and return updated references plus new approvals.

    Existing active approvals remain valid for every member who can access the
    Canvas. New references are automatically active when the actor owns the
    asset or the asset already lives in the same shared folder. A readable
    foreign asset from another scope becomes pending and requires its owner's
    approval. Inaccessible IDs fail closed and never create notifications.
    """

    normalized_actor_id = str(actor_user_id or "").strip()
    requested_ids = normalize_canvas_asset_ids(asset_file_ids)
    next_references: list[dict[str, Any]] = []
    new_pending: list[dict[str, Any]] = []

    for asset_file_id in requested_ids:
        grant = _get_grant(
            db,
            canvas_file_id=str(canvas_record.id),
            asset_file_id=asset_file_id,
        )
        if grant and grant.status == CANVAS_ASSET_STATUS_ACTIVE:
            # Metadata imported from a backup cannot manufacture this row.
            asset = (
                db.query(Files)
                .filter(
                    Files.id == asset_file_id,
                    Files.user_id == str(grant.asset_owner_user_id),
                )
                .first()
            )
            if asset:
                next_references.append(
                    _reference_from_grant(
                        grant, canvas_record=canvas_record, asset=asset
                    )
                )
                continue

        readable = resolve_file_for_read(db, normalized_actor_id, asset_file_id)
        if not readable:
            raise CanvasAssetAccessError(CanvasAssetAccessError.code)
        asset = readable.record

        same_folder = bool(
            getattr(canvas_record, "folder_id", None)
            and getattr(asset, "folder_id", None)
            and str(canvas_record.folder_id) == str(asset.folder_id)
        )
        actor_owns_asset = str(asset.user_id) == normalized_actor_id
        if actor_owns_asset or same_folder:
            grant = _upsert_grant(
                db,
                canvas_record=canvas_record,
                asset=asset,
                actor_user_id=normalized_actor_id,
                status=CANVAS_ASSET_STATUS_ACTIVE,
                authorized_by_user_id=str(asset.user_id),
            )
            reference = _reference_from_grant(
                grant, canvas_record=canvas_record, asset=asset
            )
        else:
            is_new_pending = not grant or grant.status != CANVAS_ASSET_STATUS_PENDING
            grant = _upsert_grant(
                db,
                canvas_record=canvas_record,
                asset=asset,
                actor_user_id=normalized_actor_id,
                status=CANVAS_ASSET_STATUS_PENDING,
                authorized_by_user_id=None,
            )
            reference = _reference_from_grant(
                grant, canvas_record=canvas_record, asset=asset
            )
            if is_new_pending:
                new_pending.append(reference)
        next_references.append(reference)

    # Removing a dependency revokes its scoped URL and any public approval.
    requested_set = set(requested_ids)
    now = _utc_now()
    for stale_grant in (
        db.query(CanvasAssetGrant)
        .filter(CanvasAssetGrant.canvas_file_id == str(canvas_record.id))
        .all()
    ):
        if str(stale_grant.asset_file_id) not in requested_set:
            stale_grant.status = CANVAS_ASSET_STATUS_REVOKED
            stale_grant.public_status = CANVAS_ASSET_STATUS_REVOKED
            stale_grant.updated_at = now
            db.add(stale_grant)

    return next_references, new_pending


def resolve_canvas_asset_for_read(
    db: Session,
    *,
    canvas_record: Files,
    actor_user_id: str,
    asset_file_id: str,
) -> ResolvedFileAccess:
    """Resolve an asset through direct actor access or an active Canvas grant."""

    normalized_asset_id = str(asset_file_id or "").strip()
    if not normalized_asset_id:
        raise CanvasAssetAccessError(CanvasAssetAccessError.code)

    canvas_access = resolve_file_for_read(db, str(actor_user_id), str(canvas_record.id))
    if not canvas_access:
        raise CanvasAssetAccessError(CanvasAssetAccessError.code)

    grant = _get_grant(
        db,
        canvas_file_id=str(canvas_record.id),
        asset_file_id=normalized_asset_id,
    )
    if grant:
        if grant.status != CANVAS_ASSET_STATUS_ACTIVE:
            raise CanvasAssetAccessError(CanvasAssetAccessError.code)
        asset = (
            db.query(Files)
            .filter(
                Files.id == normalized_asset_id,
                Files.user_id == str(grant.asset_owner_user_id),
            )
            .first()
        )
        if not asset:
            raise CanvasAssetAccessError(CanvasAssetAccessError.code)
        return ResolvedFileAccess(asset, str(asset.user_id))

    # Legacy Canvas metadata contains only latex_asset_file_ids. Preserve valid
    # owner and shared-file behavior while refusing the old owner-context IDOR.
    direct_access = resolve_file_for_read(db, str(actor_user_id), normalized_asset_id)
    if not direct_access:
        raise CanvasAssetAccessError(CanvasAssetAccessError.code)
    return direct_access


def prepare_canvas_asset_files_payload(
    db: Session,
    *,
    canvas_record: Files,
    actor_user_id: str,
    asset_file_ids: Iterable[Any] | None,
) -> list[dict[str, str]]:
    """Read every authorized Canvas asset into the renderer payload format.

    Unlike the legacy code-execution helper, this function is deliberately
    strict: one missing, pending, revoked, or unreadable dependency aborts the
    complete render instead of silently producing a misleading partial output.
    """

    payload: list[dict[str, str]] = []
    for asset_file_id in normalize_canvas_asset_ids(asset_file_ids):
        resolved = resolve_canvas_asset_for_read(
            db,
            canvas_record=canvas_record,
            actor_user_id=str(actor_user_id),
            asset_file_id=asset_file_id,
        )
        path = materialize_file_record(
            resolved.record,
            resolved.storage_owner_user_id,
        )
        file_bytes = path.read_bytes()
        meta = resolved.record.meta if isinstance(resolved.record.meta, dict) else {}
        original_name = (
            meta.get("original_filename")
            if isinstance(meta.get("original_filename"), str)
            else None
        )
        payload.append(
            {
                "name": str(original_name or resolved.record.file_name),
                "content": base64.b64encode(file_bytes).decode("ascii"),
            }
        )
    return payload


def get_canvas_source_for_artifact(db: Session, artifact: Files) -> Files:
    """Resolve the source Canvas represented by a generated artifact."""

    meta = artifact.meta if isinstance(artifact.meta, dict) else {}
    source_file_id = str(meta.get("latex_source_file_id") or "").strip()
    if not source_file_id:
        return artifact
    source = db.query(Files).filter(Files.id == source_file_id).first()
    return source or artifact


def is_canvas_artifact_dependency_snapshot_current(
    artifact: Files,
    source_canvas: Files,
) -> bool:
    """Return whether a generated PDF still represents the current source ACL."""

    if str(artifact.id) == str(source_canvas.id):
        return True
    artifact_meta = artifact.meta if isinstance(artifact.meta, dict) else {}
    source_meta = source_canvas.meta if isinstance(source_canvas.meta, dict) else {}
    source_revision = int(source_meta.get("canvas_revision") or 0)
    rendered_revision = int(
        artifact_meta.get("latex_source_revision")
        or artifact_meta.get("latex_render_revision")
        or artifact_meta.get("render_revision")
        or 0
    )
    return bool(
        source_revision
        and rendered_revision == source_revision
        and str(source_meta.get("latex_render_status") or "").lower() == "ready"
    )


def request_public_canvas_asset_access(
    db: Session,
    *,
    canvas_record: Files,
    sharing_user_id: str,
) -> list[dict[str, Any]]:
    """Authorize owned dependencies and request public use of foreign ones.

    Returning a non-empty list means public share creation must pause until all
    listed asset owners decide. Existing member access remains unchanged.
    """

    pending: list[dict[str, Any]] = []
    now = _utc_now()
    grants = (
        db.query(CanvasAssetGrant)
        .filter(
            CanvasAssetGrant.canvas_file_id == str(canvas_record.id),
            CanvasAssetGrant.status == CANVAS_ASSET_STATUS_ACTIVE,
        )
        .all()
    )
    for grant in grants:
        asset = (
            db.query(Files)
            .filter(
                Files.id == grant.asset_file_id,
                Files.user_id == grant.asset_owner_user_id,
            )
            .first()
        )
        if not asset:
            raise CanvasAssetAccessError(CanvasAssetAccessError.code)
        if str(asset.user_id) == str(sharing_user_id):
            grant.public_status = CANVAS_ASSET_STATUS_ACTIVE
            grant.public_authorized_by_user_id = str(sharing_user_id)
            grant.public_authorized_at = now
        elif grant.public_status != CANVAS_ASSET_STATUS_ACTIVE:
            if (
                grant.public_status != CANVAS_ASSET_STATUS_PENDING
                or not grant.public_request_id
            ):
                grant.public_request_id = str(uuid.uuid4())
            grant.public_status = CANVAS_ASSET_STATUS_PENDING
            pending.append(
                _reference_from_grant(
                    grant,
                    canvas_record=canvas_record,
                    asset=asset,
                )
            )
        grant.updated_at = now
        db.add(grant)
    db.commit()
    return pending


def prepare_public_canvas_assets_payload(
    db: Session,
    *,
    canvas_record: Files,
    include_content: bool = True,
) -> list[dict[str, str]]:
    """Materialize only explicitly public Canvas dependencies for a share."""

    payload: list[dict[str, str]] = []
    total_bytes = 0
    grants = (
        db.query(CanvasAssetGrant)
        .filter(
            CanvasAssetGrant.canvas_file_id == str(canvas_record.id),
            CanvasAssetGrant.status == CANVAS_ASSET_STATUS_ACTIVE,
        )
        .all()
    )
    for grant in grants:
        if grant.public_status != CANVAS_ASSET_STATUS_ACTIVE:
            raise CanvasAssetAccessError(CanvasAssetAccessError.code)
        asset = (
            db.query(Files)
            .filter(
                Files.id == grant.asset_file_id,
                Files.user_id == grant.asset_owner_user_id,
            )
            .first()
        )
        if not asset:
            raise CanvasAssetAccessError(CanvasAssetAccessError.code)
        if not include_content:
            continue
        path = materialize_file_record(asset, str(asset.user_id))
        content = path.read_bytes()
        total_bytes += len(content)
        if total_bytes > CANVAS_PUBLIC_ASSET_TOTAL_BYTES:
            raise ValueError("canvas_public_assets_too_large")
        payload.append(
            {
                "file_id": str(asset.id),
                "name": _asset_display_name(asset),
                "mime_type": str(asset.file_type or "application/octet-stream"),
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
            }
        )
    return payload


def decide_canvas_asset_reference(
    db: Session,
    *,
    canvas_file_id: str,
    request_id: str,
    asset_owner_user_id: str,
    approve: bool,
    public: bool = False,
) -> tuple[Files, dict[str, Any]]:
    """Apply an owner decision to one membership or public-use request."""

    canvas = db.query(Files).filter(Files.id == str(canvas_file_id)).first()
    if not canvas:
        raise CanvasAssetAccessError(CanvasAssetAccessError.code)
    if public:
        grant = (
            db.query(CanvasAssetGrant)
            .filter(
                CanvasAssetGrant.canvas_file_id == str(canvas_file_id),
                CanvasAssetGrant.public_request_id == str(request_id or "").strip(),
            )
            .first()
        )
    else:
        grant = (
            db.query(CanvasAssetGrant)
            .filter(
                CanvasAssetGrant.canvas_file_id == str(canvas_file_id),
                CanvasAssetGrant.id == str(request_id or "").strip(),
            )
            .first()
        )
    if not grant or str(grant.asset_owner_user_id) != str(asset_owner_user_id):
        raise CanvasAssetAccessError(CanvasAssetAccessError.code)

    asset = (
        db.query(Files)
        .filter(
            Files.id == str(grant.asset_file_id),
            Files.user_id == str(asset_owner_user_id),
        )
        .first()
    )
    if not asset:
        raise CanvasAssetAccessError(CanvasAssetAccessError.code)

    now = _utc_now()
    if public:
        grant.public_status = "active" if approve else "rejected"
        grant.public_authorized_by_user_id = (
            str(asset_owner_user_id) if approve else None
        )
        grant.public_authorized_at = now if approve else None
    else:
        grant.status = (
            CANVAS_ASSET_STATUS_ACTIVE if approve else CANVAS_ASSET_STATUS_REJECTED
        )
        grant.authorized_by_user_id = str(asset_owner_user_id) if approve else None
        grant.authorized_at = now if approve else None
    grant.updated_at = now
    db.add(grant)

    reference = _reference_from_grant(
        grant,
        canvas_record=canvas,
        asset=asset,
    )
    references = get_canvas_asset_references(canvas)
    replaced = False
    for index, existing in enumerate(references):
        if str(existing.get("file_id") or "") == str(asset.id):
            references[index] = reference
            replaced = True
            break
    if not replaced:
        references.append(reference)

    meta = dict(canvas.meta) if isinstance(canvas.meta, dict) else {}
    meta[CANVAS_ASSET_REFERENCES_META_KEY] = references
    if not approve and not public:
        # Any cached derivative may already contain the rejected/revoked asset.
        # Mark it stale so the UI cannot present it as the current safe preview.
        meta["latex_render_status"] = "stale"
    canvas.meta = meta
    db.add(canvas)
    db.commit()
    db.refresh(canvas)
    return canvas, reference


def copy_canvas_asset_references(file_record: Files | Any) -> list[dict[str, Any]]:
    """Copy sanitized references for a generated derivative's metadata."""

    return [dict(reference) for reference in get_canvas_asset_references(file_record)]
