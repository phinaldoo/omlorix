from __future__ import annotations

from copy import deepcopy
import hmac
import json
import logging
import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import AuditSessionLocal
from app.dependencies import get_db, get_db_log
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.groups.models import (
    Group,
    create_group as orm_create_group,
    delete_group as orm_delete_group,
    ensure_user_can_become_ineligible_manager,
    get_group,
    get_group_by_name,
)
from app.settings.models import get_settings_page_data
from app.settings.utils import coerce_bool, get_public_url
from app.users.models import (
    User,
    build_user_email_match,
    canonicalize_user_email,
    create_user,
    get_user,
    restore_user_state,
    user_exists_by_email,
)
from app.users.roles import is_admin_role, normalize_external_role
from app.users.external_management import (
    is_externally_managed,
    mark_user_externally_managed,
)
from app.users.utils import (
    delete_user as delete_user_with_retention,
    get_audit_log_user_deletion_retention_policy,
)
from app.auth.utils import hash_password
from app.workers.models import cancel_user_worker_jobs
from app.logging.models import (
    cancel_audit_log_deletions_for_user,
    cancel_auth_log_deletions_for_user,
    create_audit_log,
    get_audit_request_ip,
    pseudonymize_deleted_user_details,
)

from app.scim.models import ScimGroupLink, ScimGroupMembership, ScimUserLink, utcnow


scim_router = APIRouter(prefix="/api/v1/scim/v2", tags=["scim"])
logger = logging.getLogger(__name__)

SCIM_CORE_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_CORE_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_SERVICE_PROVIDER_CONFIG_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
SCIM_RESOURCE_TYPE_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCIM_PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_ENTERPRISE_USER_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
_SCIM_AUDIT_METHOD_MAX_LENGTH = 16
_SCIM_AUDIT_ROUTE_MAX_LENGTH = 256
_SCIM_AUDIT_IP_MAX_LENGTH = 45
_SCIM_AUDIT_USER_AGENT_MAX_LENGTH = 255
_SCIM_AUTH_REJECTION_REASONS = frozenset({"missing_bearer_token", "invalid_bearer_token"})


class ScimException(Exception):
    def __init__(self, status_code: int, payload: dict[str, Any]):
        super().__init__(payload.get("detail") or payload.get("status") or "SCIM error")
        self.status_code = status_code
        self.payload = payload


def _audit_scim_event(
    db_log: Session,
    request: Request,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id="scim",
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="scim",
    )


def _scim_json(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, media_type="application/scim+json")


def _scim_error(status_code: int, detail: str, *, scim_type: str | None = None) -> ScimException:
    payload: dict[str, Any] = {
        "schemas": [SCIM_ERROR_SCHEMA],
        "detail": detail,
        "status": str(status_code),
    }
    if scim_type:
        payload["scimType"] = scim_type
    return ScimException(status_code=status_code, payload=payload)


def _parse_bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise _scim_error(status.HTTP_401_UNAUTHORIZED, "Missing SCIM bearer token.")
    return header[7:].strip()


def _bounded_scim_audit_value(value: Any, max_length: int) -> str | None:
    normalized = " ".join(str(value or "").split())
    return normalized[:max_length] or None


def _audit_scim_auth_rejection(
    request: Request,
    reason: str,
    *,
    db: Session,
) -> None:
    """Best-effort audit for rejected SCIM credentials without retaining them."""

    route = request.scope.get("route")
    route_path = getattr(route, "path", None) or request.url.path
    safe_reason = reason if reason in _SCIM_AUTH_REJECTION_REASONS else "invalid_bearer_token"
    try:
        audit_db = AuditSessionLocal()
        try:
            create_audit_log(
                db_log=audit_db,
                user_id="scim",
                action="SCIM_AUTHENTICATION_REJECTED",
                reason=safe_reason,
                details={
                    "method": _bounded_scim_audit_value(
                        request.method.upper(),
                        _SCIM_AUDIT_METHOD_MAX_LENGTH,
                    ),
                    "route": _bounded_scim_audit_value(
                        route_path,
                        _SCIM_AUDIT_ROUTE_MAX_LENGTH,
                    ),
                },
                ip_address=_bounded_scim_audit_value(
                    get_audit_request_ip(request, db),
                    _SCIM_AUDIT_IP_MAX_LENGTH,
                ),
                user_agent=_bounded_scim_audit_value(
                    request.headers.get("user-agent"),
                    _SCIM_AUDIT_USER_AGENT_MAX_LENGTH,
                ),
                category="scim",
            )
        finally:
            audit_db.close()
    except Exception:
        # A telemetry outage must not change the authentication response or
        # disclose whether a supplied credential was close to a valid token.
        logger.exception("Failed to record rejected SCIM authentication")


def _get_scim_settings(db: Session) -> dict[str, Any]:
    return get_settings_page_data(db, "login_enterprise_sso")


def _require_scim_auth(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = _get_scim_settings(db)
    if not coerce_bool(settings.get("enable_scim"), default=False):
        raise _scim_error(status.HTTP_404_NOT_FOUND, "SCIM is not enabled on this server.")

    configured_tokens = [
        token
        for key in ("scim_bearer_token", "scim_previous_bearer_token")
        if (token := str(settings.get(key) or "").strip())
    ]
    if not configured_tokens:
        raise _scim_error(status.HTTP_503_SERVICE_UNAVAILABLE, "SCIM bearer token is not configured.")

    try:
        presented_token = _parse_bearer_token(request)
    except ScimException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            _audit_scim_auth_rejection(
                request,
                "missing_bearer_token",
                db=db,
            )
        raise
    # Compare every configured token so the position of a match does not
    # disclose whether the primary or rotation token was presented.
    token_matches = [
        hmac.compare_digest(presented_token, candidate)
        for candidate in configured_tokens
    ]
    if not presented_token or not any(token_matches):
        _audit_scim_auth_rejection(
            request,
            "invalid_bearer_token",
            db=db,
        )
        raise _scim_error(status.HTTP_401_UNAUTHORIZED, "Invalid SCIM bearer token.")

    return settings


def _public_base_url(request: Request, db: Session) -> str:
    try:
        return get_public_url(db).rstrip("/")
    except HTTPException:
        return f"{request.url.scheme}://{request.url.netloc}"


def _resource_location(request: Request, db: Session, resource_type: str, resource_id: str) -> str:
    return f"{_public_base_url(request, db)}/api/v1/scim/v2/{resource_type}/{resource_id}"


def _coerce_scim_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return default


def _scim_default_group_id(db: Session, settings: dict[str, Any]) -> str:
    candidate = str(settings.get("scim_default_group") or "default").strip() or "default"
    if get_group(db, candidate):
        return candidate
    by_name = get_group_by_name(db, candidate)
    if by_name:
        return by_name.id
    default_group = get_group(db, "default")
    if default_group:
        return default_group.id
    raise _scim_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Configured SCIM default group does not exist.")


def _scim_default_role(settings: dict[str, Any]) -> str:
    candidate = str(settings.get("scim_default_role") or "user").strip().lower()
    return normalize_external_role(candidate)


def _sync_groups_enabled(settings: dict[str, Any]) -> bool:
    return coerce_bool(settings.get("scim_sync_group_memberships"), default=True)


def _link_existing_users_by_email(settings: dict[str, Any]) -> bool:
    return coerce_bool(settings.get("scim_link_existing_users_by_email"), default=True)


def _parse_scim_filter(filter_text: str | None) -> tuple[str, str] | None:
    if not filter_text:
        return None
    match = re.fullmatch(r'\s*([A-Za-z][A-Za-z0-9\.\-_]*)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*', filter_text)
    if not match:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "Unsupported SCIM filter expression.", scim_type="invalidFilter")
    attribute = match.group(1)
    try:
        value = json.loads(f'"{match.group(2)}"')
    except json.JSONDecodeError as exc:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "Unsupported SCIM filter expression.", scim_type="invalidFilter") from exc
    return attribute, value


def _extract_primary_email(payload: dict[str, Any]) -> str:
    emails = payload.get("emails")
    if isinstance(emails, list):
        preferred = None
        for entry in emails:
            if not isinstance(entry, dict):
                continue
            value = str(entry.get("value") or "").strip().lower()
            if not value:
                continue
            if entry.get("primary") is True:
                return value
            if preferred is None:
                preferred = value
        if preferred:
            return preferred
    user_name = str(payload.get("userName") or "").strip().lower()
    return user_name if "@" in user_name else ""


def _extract_name(payload: dict[str, Any]) -> tuple[str, str, str]:
    name_payload = payload.get("name") if isinstance(payload.get("name"), dict) else {}
    given_name = str(name_payload.get("givenName") or "").strip()
    family_name = str(name_payload.get("familyName") or "").strip()
    display_name = str(payload.get("displayName") or "").strip()
    if not given_name and display_name:
        parts = display_name.split()
        given_name = parts[0]
        family_name = family_name or " ".join(parts[1:])
    return given_name or "User", family_name, display_name


def _extract_role(payload: dict[str, Any], settings: dict[str, Any]) -> str:
    roles = payload.get("roles")
    if isinstance(roles, list):
        for entry in roles:
            if isinstance(entry, dict):
                candidate = str(entry.get("value") or entry.get("display") or "").strip().lower()
            else:
                candidate = str(entry or "").strip().lower()
            if candidate in {"user", "pending"}:
                return candidate
    return _scim_default_role(settings)


def _ensure_scim_user_mutable(user: User) -> None:
    """Keep SCIM from mutating owner/admin identities or authority."""

    if is_admin_role(getattr(user, "role", None)):
        raise _scim_error(
            status.HTTP_403_FORBIDDEN,
            "Administrative accounts are managed only by the Omlorix owner.",
        )


def _normalize_external_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("externalId")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_password(payload: dict[str, Any]) -> str:
    raw = payload.get("password")
    if raw is None:
        return secrets.token_urlsafe(32)
    text = str(raw)
    return text if text else secrets.token_urlsafe(32)


def _membership_rows_for_user(db: Session, user_id: str) -> list[ScimGroupMembership]:
    return (
        db.query(ScimGroupMembership)
        .filter(ScimGroupMembership.user_id == user_id)
        .order_by(ScimGroupMembership.priority.asc(), ScimGroupMembership.created_at.asc())
        .all()
    )


def _membership_rows_for_group(db: Session, group_id: str) -> list[ScimGroupMembership]:
    return (
        db.query(ScimGroupMembership)
        .filter(ScimGroupMembership.group_id == group_id)
        .order_by(ScimGroupMembership.priority.asc(), ScimGroupMembership.created_at.asc())
        .all()
    )


def _upsert_scim_user_link(db: Session, user_id: str, external_id: str | None) -> ScimUserLink:
    now = utcnow()
    link = db.query(ScimUserLink).filter(ScimUserLink.user_id == user_id).first()
    if not link:
        link = ScimUserLink(
            user_id=user_id,
            external_id=external_id,
            created_at=now,
            updated_at=now,
        )
        db.add(link)
    else:
        link.external_id = external_id
        link.updated_at = now
    db.flush()
    return link


def _upsert_scim_group_link(db: Session, group_id: str, external_id: str | None) -> ScimGroupLink:
    now = utcnow()
    link = db.query(ScimGroupLink).filter(ScimGroupLink.group_id == group_id).first()
    if not link:
        link = ScimGroupLink(
            group_id=group_id,
            external_id=external_id,
            created_at=now,
            updated_at=now,
        )
        db.add(link)
    else:
        link.external_id = external_id
        link.updated_at = now
    db.flush()
    return link


def _resolve_group_reference(db: Session, reference: Any) -> Group | None:
    if isinstance(reference, dict):
        candidate_id = str(reference.get("value") or "").strip()
        external_id = str(reference.get("externalId") or "").strip()
        display_name = str(reference.get("display") or "").strip()
    else:
        candidate_id = str(reference or "").strip()
        external_id = ""
        display_name = ""

    if candidate_id:
        group = get_group(db, candidate_id)
        if group:
            return group
        link = db.query(ScimGroupLink).filter(ScimGroupLink.external_id == candidate_id).first()
        if link:
            return get_group(db, link.group_id)

    if external_id:
        link = db.query(ScimGroupLink).filter(ScimGroupLink.external_id == external_id).first()
        if link:
            return get_group(db, link.group_id)

    if display_name:
        return get_group_by_name(db, display_name)

    return None


def _recompute_user_primary_group(db: Session, user: User, settings: dict[str, Any]) -> None:
    memberships = _membership_rows_for_user(db, user.id)
    if memberships:
        first_group = get_group(db, memberships[0].group_id)
        if first_group:
            user.group_id = first_group.id
            return
    user.group_id = _scim_default_group_id(db, settings)


def _replace_user_memberships(db: Session, user: User, group_refs: list[Any], settings: dict[str, Any]) -> None:
    _ensure_scim_user_mutable(user)
    db.query(ScimGroupMembership).filter(ScimGroupMembership.user_id == user.id).delete(synchronize_session=False)
    now = utcnow()
    for priority, reference in enumerate(group_refs):
        group = _resolve_group_reference(db, reference)
        if not group:
            continue
        db.add(
            ScimGroupMembership(
                user_id=user.id,
                group_id=group.id,
                priority=priority,
                created_at=now,
                updated_at=now,
            )
        )
    _recompute_user_primary_group(db, user, settings)


def _add_group_member(db: Session, group: Group, user: User, settings: dict[str, Any]) -> None:
    _ensure_scim_user_mutable(user)
    membership = (
        db.query(ScimGroupMembership)
        .filter(
            ScimGroupMembership.group_id == group.id,
            ScimGroupMembership.user_id == user.id,
        )
        .first()
    )
    now = utcnow()
    if membership:
        membership.updated_at = now
    else:
        last_priority = (
            db.query(ScimGroupMembership)
            .filter(ScimGroupMembership.user_id == user.id)
            .order_by(ScimGroupMembership.priority.desc())
            .first()
        )
        next_priority = (last_priority.priority + 1) if last_priority else 0
        db.add(
            ScimGroupMembership(
                user_id=user.id,
                group_id=group.id,
                priority=next_priority,
                created_at=now,
                updated_at=now,
            )
        )
    _recompute_user_primary_group(db, user, settings)


def _remove_group_member(db: Session, group: Group, user: User, settings: dict[str, Any]) -> None:
    _ensure_scim_user_mutable(user)
    (
        db.query(ScimGroupMembership)
        .filter(
            ScimGroupMembership.group_id == group.id,
            ScimGroupMembership.user_id == user.id,
        )
        .delete(synchronize_session=False)
    )
    _recompute_user_primary_group(db, user, settings)


def _find_scim_user(db: Session, resource_id: str) -> User:
    try:
        return get_user(db, user_id=resource_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise _scim_error(status.HTTP_404_NOT_FOUND, "SCIM user not found.")
        raise


def _find_scim_group(db: Session, resource_id: str) -> Group:
    group = get_group(db, resource_id)
    if not group:
        raise _scim_error(status.HTTP_404_NOT_FOUND, "SCIM group not found.")
    return group


def _scim_user_link(db: Session, user_id: str) -> ScimUserLink | None:
    return db.query(ScimUserLink).filter(ScimUserLink.user_id == user_id).first()


def _scim_group_link(db: Session, group_id: str) -> ScimGroupLink | None:
    return db.query(ScimGroupLink).filter(ScimGroupLink.group_id == group_id).first()


def _audit_value_changes(before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for field in fields:
        old_value = before.get(field)
        new_value = after.get(field)
        if old_value != new_value:
            changes[field] = {"old": old_value, "new": new_value}
    return changes


def _scim_user_audit_snapshot(db: Session, user: User) -> dict[str, Any]:
    link = _scim_user_link(db, user.id)
    return {
        "user_id": user.id,
        "active": bool(user.is_active and user.deleted_at is None),
        "role": user.role,
        "group_id": user.group_id,
        "scim_group_ids": [membership.group_id for membership in _membership_rows_for_user(db, user.id)],
        "externally_managed": is_externally_managed(user),
        "external_auth_provider": getattr(user, "external_auth_provider", None),
        "external_id": link.external_id if link else None,
    }


def _scim_group_audit_snapshot(db: Session, group: Group) -> dict[str, Any]:
    link = _scim_group_link(db, group.id)
    return {
        "group_id": group.id,
        "display_name": getattr(group, "name", None),
        "external_id": link.external_id if link else None,
        "member_user_ids": [membership.user_id for membership in _membership_rows_for_group(db, group.id)],
    }


def _user_scim_list_context(db: Session, users: list[User]) -> dict[str, Any]:
    user_ids = [user.id for user in users]
    if not user_ids:
        return {
            "links_by_user_id": {},
            "memberships_by_user_id": {},
            "groups_by_id": {},
        }

    links_by_user_id = {
        link.user_id: link
        for link in db.query(ScimUserLink).filter(ScimUserLink.user_id.in_(user_ids)).all()
    }
    memberships = (
        db.query(ScimGroupMembership)
        .filter(ScimGroupMembership.user_id.in_(user_ids))
        .order_by(ScimGroupMembership.priority.asc(), ScimGroupMembership.created_at.asc())
        .all()
    )
    memberships_by_user_id: dict[str, list[ScimGroupMembership]] = {}
    group_ids = {membership.group_id for membership in memberships}
    for membership in memberships:
        memberships_by_user_id.setdefault(membership.user_id, []).append(membership)

    groups_by_id = {}
    if group_ids:
        groups_by_id = {
            group.id: group
            for group in db.query(Group).filter(Group.id.in_(group_ids)).all()
        }

    return {
        "links_by_user_id": links_by_user_id,
        "memberships_by_user_id": memberships_by_user_id,
        "groups_by_id": groups_by_id,
    }


def _group_scim_list_context(db: Session, groups: list[Group]) -> dict[str, Any]:
    group_ids = [group.id for group in groups]
    if not group_ids:
        return {
            "links_by_group_id": {},
            "memberships_by_group_id": {},
            "users_by_id": {},
        }

    links_by_group_id = {
        link.group_id: link
        for link in db.query(ScimGroupLink).filter(ScimGroupLink.group_id.in_(group_ids)).all()
    }
    memberships = (
        db.query(ScimGroupMembership)
        .filter(ScimGroupMembership.group_id.in_(group_ids))
        .order_by(ScimGroupMembership.priority.asc(), ScimGroupMembership.created_at.asc())
        .all()
    )
    memberships_by_group_id: dict[str, list[ScimGroupMembership]] = {}
    user_ids = {membership.user_id for membership in memberships}
    for membership in memberships:
        memberships_by_group_id.setdefault(membership.group_id, []).append(membership)

    users_by_id = {}
    if user_ids:
        users_by_id = {
            user.id: user
            for user in db.query(User).filter(User.id.in_(user_ids)).all()
        }

    return {
        "links_by_group_id": links_by_group_id,
        "memberships_by_group_id": memberships_by_group_id,
        "users_by_id": users_by_id,
    }


def _user_to_scim_resource(
    user: User,
    db: Session,
    request: Request,
    *,
    link: ScimUserLink | None = None,
    memberships: list[ScimGroupMembership] | None = None,
    groups_by_id: dict[str, Group] | None = None,
) -> dict[str, Any]:
    if link is None:
        link = _scim_user_link(db, user.id)
    if memberships is None:
        memberships = _membership_rows_for_user(db, user.id)
    groups: list[dict[str, Any]] = []
    for membership in memberships:
        group = groups_by_id.get(membership.group_id) if groups_by_id is not None else get_group(db, membership.group_id)
        if not group:
            continue
        groups.append(
            {
                "value": group.id,
                "$ref": _resource_location(request, db, "Groups", group.id),
                "display": group.name,
            }
        )

    last_modified = link.updated_at if link and link.updated_at else user.created_at

    payload = {
        "schemas": [SCIM_CORE_USER_SCHEMA],
        "id": user.id,
        "externalId": link.external_id if link else None,
        "userName": user.email,
        "active": bool(user.is_active and user.deleted_at is None),
        "displayName": " ".join(part for part in [user.first_name, user.last_name] if part).strip() or user.email,
        "name": {
            "givenName": user.first_name,
            "familyName": user.last_name,
            "formatted": " ".join(part for part in [user.first_name, user.last_name] if part).strip(),
        },
        "emails": [{"value": user.email, "type": "work", "primary": True}],
        "roles": [{"value": user.role}],
        "groups": groups,
        "meta": {
            "resourceType": "User",
            "created": user.created_at.isoformat() if user.created_at else None,
            "lastModified": last_modified.isoformat() if last_modified else None,
            "location": _resource_location(request, db, "Users", user.id),
        },
    }
    return payload


def _group_to_scim_resource(
    group: Group,
    db: Session,
    request: Request,
    *,
    link: ScimGroupLink | None = None,
    memberships: list[ScimGroupMembership] | None = None,
    users_by_id: dict[str, User] | None = None,
) -> dict[str, Any]:
    if link is None:
        link = _scim_group_link(db, group.id)
    if memberships is None:
        memberships = _membership_rows_for_group(db, group.id)
    members = []
    for membership in memberships:
        if users_by_id is not None:
            user = users_by_id.get(membership.user_id)
        else:
            try:
                user = get_user(db, user_id=membership.user_id)
            except HTTPException:
                continue
        if not user:
            continue
        members.append(
            {
                "value": user.id,
                "$ref": _resource_location(request, db, "Users", user.id),
                "display": user.email,
            }
        )

    return {
        "schemas": [SCIM_CORE_GROUP_SCHEMA],
        "id": group.id,
        "externalId": link.external_id if link else None,
        "displayName": group.name,
        "members": members,
        "meta": {
            "resourceType": "Group",
            "created": group.created_at.isoformat() if group.created_at else None,
            "lastModified": group.updated_at.isoformat() if group.updated_at else None,
            "location": _resource_location(request, db, "Groups", group.id),
        },
    }


def _paginate(items: list[dict[str, Any]], start_index: int, count: int, *, total_results: int | None = None) -> dict[str, Any]:
    sliced = items
    computed_total = total_results
    if computed_total is None:
        zero_based = max(start_index - 1, 0)
        end = zero_based + max(count, 0)
        sliced = items[zero_based:end]
        computed_total = len(items)
    return {
        "schemas": [SCIM_LIST_RESPONSE_SCHEMA],
        "totalResults": computed_total,
        "startIndex": start_index,
        "itemsPerPage": len(sliced),
        "Resources": sliced,
    }


def _filter_users_query(db: Session, filter_text: str | None):
    if not filter_text:
        return db.query(User).order_by(User.created_at.asc())

    attribute, value = _parse_scim_filter(filter_text)
    if attribute == "id":
        return db.query(User).filter(User.id == value).order_by(User.created_at.asc())
    if attribute == "externalId":
        return (
            db.query(User)
            .join(ScimUserLink, ScimUserLink.user_id == User.id)
            .filter(ScimUserLink.external_id == value)
            .order_by(User.created_at.asc())
        )
    if attribute in {"userName", "emails.value"}:
        candidate = canonicalize_user_email(value)
        if not candidate or "@" not in candidate:
            return db.query(User).filter(User.id == "").order_by(User.created_at.asc())
        return db.query(User).filter(build_user_email_match(candidate)).order_by(User.created_at.asc())
    raise _scim_error(status.HTTP_400_BAD_REQUEST, f"Unsupported SCIM user filter attribute '{attribute}'.", scim_type="invalidFilter")


def _filter_groups_query(db: Session, filter_text: str | None):
    if not filter_text:
        return db.query(Group).order_by(Group.created_at.asc())

    attribute, value = _parse_scim_filter(filter_text)
    if attribute == "id":
        return db.query(Group).filter(Group.id == value).order_by(Group.created_at.asc())
    if attribute == "displayName":
        return db.query(Group).filter(func.lower(Group.name) == value.lower()).order_by(Group.created_at.asc())
    if attribute == "externalId":
        return (
            db.query(Group)
            .join(ScimGroupLink, ScimGroupLink.group_id == Group.id)
            .filter(ScimGroupLink.external_id == value)
            .order_by(Group.created_at.asc())
        )
    raise _scim_error(status.HTTP_400_BAD_REQUEST, f"Unsupported SCIM group filter attribute '{attribute}'.", scim_type="invalidFilter")


def _paginate_query(query, start_index: int, count: int) -> tuple[list[Any], int]:
    total_results = query.order_by(None).count()
    if count <= 0:
        return [], total_results
    zero_based = max(start_index - 1, 0)
    return query.offset(zero_based).limit(count).all(), total_results


def _validate_external_id_uniqueness(db: Session, *, external_id: str | None, existing_user_id: str | None = None, existing_group_id: str | None = None) -> None:
    if not external_id:
        return
    user_link = db.query(ScimUserLink).filter(ScimUserLink.external_id == external_id).first()
    if user_link and user_link.user_id != existing_user_id:
        raise _scim_error(status.HTTP_409_CONFLICT, "SCIM externalId is already linked to another user.", scim_type="uniqueness")
    group_link = db.query(ScimGroupLink).filter(ScimGroupLink.external_id == external_id).first()
    if group_link and group_link.group_id != existing_group_id:
        raise _scim_error(status.HTTP_409_CONFLICT, "SCIM externalId is already linked to another group.", scim_type="uniqueness")


def _resolve_existing_user_for_create(db: Session, payload: dict[str, Any], settings: dict[str, Any]) -> User | None:
    external_id = _normalize_external_id(payload)
    if external_id:
        link = db.query(ScimUserLink).filter(ScimUserLink.external_id == external_id).first()
        if link:
            return get_user(db, user_id=link.user_id)

    email = _extract_primary_email(payload)
    if email and user_exists_by_email(db, email):
        if not _link_existing_users_by_email(settings):
            raise _scim_error(status.HTTP_409_CONFLICT, "A local user already exists with this email address.", scim_type="uniqueness")
        return get_user(db, email=email)
    return None


def _apply_scim_user_payload(
    db: Session,
    user: User,
    payload: dict[str, Any],
    settings: dict[str, Any],
    *,
    replace_memberships: bool = True,
    allow_owner_bootstrap: bool = False,
    manage_lifecycle: bool = True,
) -> User:
    if not allow_owner_bootstrap:
        _ensure_scim_user_mutable(user)

    next_active = _coerce_scim_bool(payload.get("active"), default=True)
    if next_active and manage_lifecycle:
        # SCIM is an account-restoration surface, not merely a profile update.
        # Use the same guard/fence protocol as the administrator restore route.
        user = restore_user_state(
            db,
            user.id,
            allow_already_active=True,
            commit=False,
        )

    email = _extract_primary_email(payload)
    first_name, last_name, _display_name = _extract_name(payload)

    if email:
        email = canonicalize_user_email(email)
        if not email:
            raise _scim_error(status.HTTP_400_BAD_REQUEST, "SCIM email payload did not contain a usable address.", scim_type="invalidValue")
        email_owner = None
        try:
            email_owner = get_user(db, email=email)
        except HTTPException:
            email_owner = None
        if email_owner and email_owner.id != user.id:
            raise _scim_error(status.HTTP_409_CONFLICT, "Email address is already in use.", scim_type="uniqueness")
        user.email = email

    user.first_name = first_name or user.first_name or "User"
    user.last_name = last_name or user.last_name or ""
    # ``create_user`` assigns the first account the owner role. Preserve that
    # bootstrap decision even when the initial SCIM payload requests another
    # role.
    next_role = user.role if is_admin_role(user.role) else _extract_role(payload, settings)
    if (user.is_active and not next_active) or (user.role != "pending" and next_role == "pending"):
        try:
            ensure_user_can_become_ineligible_manager(db, user.id)
        except HTTPException as exc:
            raise _scim_error(
                status.HTTP_400_BAD_REQUEST,
                str(exc.detail),
                scim_type="mutability",
            ) from exc
    user.role = next_role
    user.is_active = next_active

    # SCIM manages lifecycle/profile state; it must never create an alternate
    # local password that bypasses enterprise authentication policy.

    external_id = _normalize_external_id(payload)
    _validate_external_id_uniqueness(db, external_id=external_id, existing_user_id=user.id)
    _upsert_scim_user_link(db, user.id, external_id)

    if _sync_groups_enabled(settings) and not is_admin_role(user.role):
        groups_payload = payload.get("groups")
        if isinstance(groups_payload, list):
            if replace_memberships:
                _replace_user_memberships(db, user, groups_payload, settings)
        else:
            _recompute_user_primary_group(db, user, settings)
    elif not is_admin_role(user.role):
        user.group_id = user.group_id or _scim_default_group_id(db, settings)

    db.add(user)
    db.flush()
    mark_user_externally_managed(db, user, "scim", commit=False)
    if manage_lifecycle and (
        not bool(user.is_active)
        or str(user.role or "").strip().lower() == "pending"
    ):
        cancel_user_worker_jobs(db, user_id=user.id, commit=False)
    return user


def _create_scim_user(db: Session, payload: dict[str, Any], settings: dict[str, Any]) -> User:
    existing_user = _resolve_existing_user_for_create(db, payload, settings)
    if existing_user:
        return _apply_scim_user_payload(db, existing_user, payload, settings)

    email = canonicalize_user_email(_extract_primary_email(payload))
    if not email:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "SCIM users require an email address or an email-like userName.", scim_type="invalidValue")

    first_name, last_name, _display_name = _extract_name(payload)
    role = _extract_role(payload, settings)
    user = create_user(
        db=db,
        email=email,
        # The non-null database column receives an unknowable placeholder;
        # SCIM's optional password attribute is deliberately ignored.
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        first_name=first_name or "User",
        last_name=last_name or "",
        role=role,
        group_id=_scim_default_group_id(db, settings),
    )
    return _apply_scim_user_payload(
        db,
        user,
        payload,
        settings,
        allow_owner_bootstrap=is_admin_role(user.role),
    )


def _apply_scim_group_payload(
    db: Session,
    group: Group,
    payload: dict[str, Any],
    settings: dict[str, Any],
    *,
    replace_memberships: bool = True,
) -> Group:
    display_name = str(payload.get("displayName") or "").strip()
    if not display_name:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "SCIM groups require displayName.", scim_type="invalidValue")
    existing_by_name = get_group_by_name(db, display_name)
    if existing_by_name and existing_by_name.id != group.id:
        raise _scim_error(status.HTTP_409_CONFLICT, "Group name is already in use.", scim_type="uniqueness")
    group.name = display_name

    external_id = _normalize_external_id(payload)
    _validate_external_id_uniqueness(db, external_id=external_id, existing_group_id=group.id)
    _upsert_scim_group_link(db, group.id, external_id)

    if _sync_groups_enabled(settings):
        members_payload = payload.get("members")
        if isinstance(members_payload, list) and replace_memberships:
            current_memberships = _membership_rows_for_group(db, group.id)
            current_user_ids = {membership.user_id for membership in current_memberships}
            desired_users: list[User] = []
            for member in members_payload:
                member_id = str(member.get("value") or "").strip() if isinstance(member, dict) else str(member or "").strip()
                if not member_id:
                    continue
                desired_users.append(_find_scim_user(db, member_id))
            desired_user_ids = {user.id for user in desired_users}
            for membership in current_memberships:
                if membership.user_id not in desired_user_ids:
                    user = _find_scim_user(db, membership.user_id)
                    _remove_group_member(db, group, user, settings)
            for user in desired_users:
                if user.id not in current_user_ids:
                    _add_group_member(db, group, user, settings)

    db.add(group)
    db.flush()
    return group


def _create_scim_group(db: Session, payload: dict[str, Any], settings: dict[str, Any]) -> Group:
    display_name = str(payload.get("displayName") or "").strip()
    if not display_name:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "SCIM groups require displayName.", scim_type="invalidValue")
    existing = get_group_by_name(db, display_name)
    if existing:
        return _apply_scim_group_payload(db, existing, payload, settings)

    group = orm_create_group(
        db,
        None,
        display_name,
        deepcopy(DEFAULT_GROUP_SETTINGS),
    )
    return _apply_scim_group_payload(db, group, payload, settings)


def _scim_patch_final_active(
    operations: list[dict[str, Any]],
    *,
    initial_active: bool,
) -> bool | None:
    """Return the final explicit active value, or ``None`` when untouched."""

    active = bool(initial_active)
    touched = False
    for operation in operations:
        op = str(operation.get("op") or "").strip().lower()
        path = str(operation.get("path") or "").strip().lower()
        value = operation.get("value")
        if path == "active":
            touched = True
            active = False if op == "remove" else _coerce_scim_bool(
                value,
                default=True,
            )
            continue
        if not path:
            if op == "remove":
                continue
            # Full-object PATCH converts a missing value to an empty object and
            # reuses PUT semantics, where omitted ``active`` defaults to true.
            try:
                merged = dict(value or {})
            except (TypeError, ValueError):
                continue
            touched = True
            active = _coerce_scim_bool(merged.get("active"), default=True)
    return active if touched else None


def _cancel_scim_active_user_retention(db_log: Session, user: User) -> None:
    """Idempotently retire stale log-deletion jobs after SCIM activation."""

    if not bool(getattr(user, "is_active", False)) or getattr(
        user, "deleted_at", None
    ) is not None:
        return
    # Run this for every active response, not only the first restore attempt.
    # If a process dies after the main commit, the IdP's retry still completes
    # the cross-schema restoration protocol.
    cancel_auth_log_deletions_for_user(db_log, user.id)
    cancel_audit_log_deletions_for_user(db_log, user.id)


def _apply_patch_to_user(db: Session, user: User, operations: list[dict[str, Any]], settings: dict[str, Any]) -> User:
    _ensure_scim_user_mutable(user)
    final_active = _scim_patch_final_active(
        operations,
        initial_active=bool(user.is_active),
    )
    if final_active:
        # Acquire the erasure guard before any PATCH mutation can autoflush and
        # lock the user row in the opposite order.
        user = restore_user_state(
            db,
            user.id,
            allow_already_active=True,
            commit=False,
        )
    for operation in operations:
        op = str(operation.get("op") or "").strip().lower()
        path = str(operation.get("path") or "").strip()
        value = operation.get("value")

        if op not in {"add", "replace", "remove"}:
            raise _scim_error(status.HTTP_400_BAD_REQUEST, f"Unsupported PATCH op '{op}'.", scim_type="invalidSyntax")

        if not path:
            if op == "remove":
                raise _scim_error(status.HTTP_400_BAD_REQUEST, "PATCH remove requires a path.", scim_type="invalidPath")
            merged = dict(operation.get("value") or {})
            merged.setdefault("externalId", _scim_user_link(db, user.id).external_id if _scim_user_link(db, user.id) else None)
            _apply_scim_user_payload(
                db,
                user,
                merged,
                settings,
                manage_lifecycle=False,
            )
            continue

        lowered = path.lower()
        if lowered == "active":
            next_active = False if op == "remove" else _coerce_scim_bool(value, default=True)
            if user.is_active and not next_active:
                try:
                    ensure_user_can_become_ineligible_manager(db, user.id)
                except HTTPException as exc:
                    raise _scim_error(
                        status.HTTP_400_BAD_REQUEST,
                        str(exc.detail),
                        scim_type="mutability",
                    ) from exc
            user.is_active = next_active
            continue
        if lowered == "externalid":
            _validate_external_id_uniqueness(db, external_id=None if op == "remove" else str(value or "").strip() or None, existing_user_id=user.id)
            _upsert_scim_user_link(db, user.id, None if op == "remove" else str(value or "").strip() or None)
            continue
        if lowered == "username":
            if op != "remove":
                candidate = canonicalize_user_email(str(value or ""))
                if candidate and candidate != user.email:
                    email_owner = None
                    try:
                        email_owner = get_user(db, email=candidate)
                    except HTTPException:
                        email_owner = None
                    if email_owner and email_owner.id != user.id:
                        raise _scim_error(status.HTTP_409_CONFLICT, "Email address is already in use.", scim_type="uniqueness")
                    if "@" in candidate:
                        user.email = candidate
            continue
        if lowered == "displayname":
            parts = str(value or "").strip().split()
            if parts:
                user.first_name = parts[0]
                user.last_name = " ".join(parts[1:])
            continue
        if lowered == "name.givenname":
            user.first_name = "" if op == "remove" else str(value or "").strip() or "User"
            continue
        if lowered == "name.familyname":
            user.last_name = "" if op == "remove" else str(value or "").strip()
            continue
        if lowered == "emails":
            if op == "remove":
                raise _scim_error(status.HTTP_400_BAD_REQUEST, "Removing the primary email is not supported.", scim_type="mutability")
            email = _extract_primary_email({"emails": value})
            if not email:
                raise _scim_error(status.HTTP_400_BAD_REQUEST, "SCIM email payload did not contain a usable address.", scim_type="invalidValue")
            email = canonicalize_user_email(email)
            if not email:
                raise _scim_error(status.HTTP_400_BAD_REQUEST, "SCIM email payload did not contain a usable address.", scim_type="invalidValue")
            email_owner = None
            try:
                email_owner = get_user(db, email=email)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            if email_owner and email_owner.id != user.id:
                raise _scim_error(status.HTTP_409_CONFLICT, "Email address is already in use.", scim_type="uniqueness")
            user.email = email
            continue
        if lowered == "roles":
            next_role = _scim_default_role(settings) if op == "remove" else _extract_role({"roles": value}, settings)
            if user.role != "pending" and next_role == "pending":
                try:
                    ensure_user_can_become_ineligible_manager(db, user.id)
                except HTTPException as exc:
                    raise _scim_error(
                        status.HTTP_400_BAD_REQUEST,
                        str(exc.detail),
                        scim_type="mutability",
                    ) from exc
            user.role = next_role
            continue
        if lowered == "groups":
            if _sync_groups_enabled(settings):
                _replace_user_memberships(db, user, [] if op == "remove" else list(value or []), settings)
            continue
        raise _scim_error(status.HTTP_400_BAD_REQUEST, f"Unsupported SCIM PATCH path '{path}'.", scim_type="invalidPath")
    if (
        not bool(user.is_active)
        or str(user.role or "").strip().lower() == "pending"
    ):
        cancel_user_worker_jobs(db, user_id=user.id, commit=False)
    db.add(user)
    db.flush()
    return user


def _apply_patch_to_group(db: Session, group: Group, operations: list[dict[str, Any]], settings: dict[str, Any]) -> Group:
    for operation in operations:
        op = str(operation.get("op") or "").strip().lower()
        path = str(operation.get("path") or "").strip()
        value = operation.get("value")
        if op not in {"add", "replace", "remove"}:
            raise _scim_error(status.HTTP_400_BAD_REQUEST, f"Unsupported PATCH op '{op}'.", scim_type="invalidSyntax")

        if not path:
            if op == "remove":
                raise _scim_error(status.HTTP_400_BAD_REQUEST, "PATCH remove requires a path.", scim_type="invalidPath")
            _apply_scim_group_payload(db, group, dict(value or {}), settings)
            continue

        lowered = path.lower()
        if lowered == "displayname":
            if op == "remove":
                raise _scim_error(status.HTTP_400_BAD_REQUEST, "displayName cannot be removed.", scim_type="mutability")
            _apply_scim_group_payload(db, group, {"displayName": value, "externalId": _scim_group_link(db, group.id).external_id if _scim_group_link(db, group.id) else None}, settings, replace_memberships=False)
            continue
        if lowered == "externalid":
            _validate_external_id_uniqueness(db, external_id=None if op == "remove" else str(value or "").strip() or None, existing_group_id=group.id)
            _upsert_scim_group_link(db, group.id, None if op == "remove" else str(value or "").strip() or None)
            continue
        if lowered.startswith("members"):
            if not _sync_groups_enabled(settings):
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                member_id = str(item.get("value") or "").strip() if isinstance(item, dict) else str(item or "").strip()
                if not member_id:
                    continue
                user = _find_scim_user(db, member_id)
                if op == "remove":
                    _remove_group_member(db, group, user, settings)
                else:
                    _add_group_member(db, group, user, settings)
            continue
        raise _scim_error(status.HTTP_400_BAD_REQUEST, f"Unsupported SCIM PATCH path '{path}'.", scim_type="invalidPath")
    db.add(group)
    db.flush()
    return group


SERVICE_PROVIDER_CONFIG = {
    "schemas": [SCIM_SERVICE_PROVIDER_CONFIG_SCHEMA],
    "documentationUri": "https://datatracker.ietf.org/doc/html/rfc7643",
    "patch": {"supported": True},
    "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
    "filter": {"supported": True, "maxResults": 200},
    "changePassword": {"supported": False},
    "sort": {"supported": False},
    "etag": {"supported": False},
    "authenticationSchemes": [
        {
            "type": "oauthbearertoken",
            "name": "SCIM Bearer Token",
            "description": "Static bearer token configured in Omlorix admin settings.",
            "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
            "documentationUri": "https://www.rfc-editor.org/rfc/rfc7644",
            "primary": True,
        }
    ],
}


RESOURCE_TYPES = [
    {
        "schemas": [SCIM_RESOURCE_TYPE_SCHEMA],
        "id": "User",
        "name": "User",
        "endpoint": "/Users",
        "description": "User Account",
        "schema": SCIM_CORE_USER_SCHEMA,
        "schemaExtensions": [
            {
                "schema": SCIM_ENTERPRISE_USER_SCHEMA,
                "required": False,
            }
        ],
    },
    {
        "schemas": [SCIM_RESOURCE_TYPE_SCHEMA],
        "id": "Group",
        "name": "Group",
        "endpoint": "/Groups",
        "description": "Group",
        "schema": SCIM_CORE_GROUP_SCHEMA,
    },
]


SCHEMAS = [
    {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
        "id": SCIM_CORE_USER_SCHEMA,
        "name": "User",
        "description": "User Account",
    },
    {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
        "id": SCIM_CORE_GROUP_SCHEMA,
        "name": "Group",
        "description": "Group",
    },
    {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
        "id": SCIM_SERVICE_PROVIDER_CONFIG_SCHEMA,
        "name": "ServiceProviderConfig",
        "description": "SCIM service provider capabilities",
    },
    {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
        "id": SCIM_RESOURCE_TYPE_SCHEMA,
        "name": "ResourceType",
        "description": "SCIM resource type definition",
    },
    {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
        "id": SCIM_PATCH_OP_SCHEMA,
        "name": "PatchOp",
        "description": "SCIM PATCH request",
    },
]


async def scim_exception_handler(_request: Request, exc: ScimException):
    return _scim_json(exc.payload, status_code=exc.status_code)


@scim_router.get("/ServiceProviderConfig")
def service_provider_config(_settings=Depends(_require_scim_auth)):
    return _scim_json(SERVICE_PROVIDER_CONFIG)


@scim_router.get("/ResourceTypes")
def list_resource_types(_settings=Depends(_require_scim_auth)):
    payload = {
        "schemas": [SCIM_LIST_RESPONSE_SCHEMA],
        "totalResults": len(RESOURCE_TYPES),
        "startIndex": 1,
        "itemsPerPage": len(RESOURCE_TYPES),
        "Resources": RESOURCE_TYPES,
    }
    return _scim_json(payload)


@scim_router.get("/ResourceTypes/{resource_type}")
def get_resource_type(resource_type: str, _settings=Depends(_require_scim_auth)):
    for item in RESOURCE_TYPES:
        if item["id"].lower() == resource_type.lower():
            return _scim_json(item)
    raise _scim_error(status.HTTP_404_NOT_FOUND, "SCIM resource type not found.")


@scim_router.get("/Schemas")
def list_schemas(_settings=Depends(_require_scim_auth)):
    payload = {
        "schemas": [SCIM_LIST_RESPONSE_SCHEMA],
        "totalResults": len(SCHEMAS),
        "startIndex": 1,
        "itemsPerPage": len(SCHEMAS),
        "Resources": SCHEMAS,
    }
    return _scim_json(payload)


@scim_router.get("/Schemas/{schema_id:path}")
def get_schema(schema_id: str, _settings=Depends(_require_scim_auth)):
    for item in SCHEMAS:
        if item["id"] == schema_id:
            return _scim_json(item)
    raise _scim_error(status.HTTP_404_NOT_FOUND, "SCIM schema not found.")


@scim_router.get("/Users")
def list_users(
    request: Request,
    filter: str | None = None,
    startIndex: int = 1,
    count: int = 100,
    db: Session = Depends(get_db),
    settings=Depends(_require_scim_auth),
):
    _ = settings
    normalized_start_index = max(startIndex, 1)
    normalized_count = min(max(count, 0), 200)
    users, total_results = _paginate_query(_filter_users_query(db, filter), normalized_start_index, normalized_count)
    context = _user_scim_list_context(db, users)
    items = [
        _user_to_scim_resource(
            user,
            db,
            request,
            link=context["links_by_user_id"].get(user.id),
            memberships=context["memberships_by_user_id"].get(user.id, []),
            groups_by_id=context["groups_by_id"],
        )
        for user in users
    ]
    return _scim_json(_paginate(items, normalized_start_index, normalized_count, total_results=total_results))


@scim_router.post("/Users")
def create_user_route(
    request: Request,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    settings=Depends(_require_scim_auth),
):
    user = _create_scim_user(db, payload, settings)
    db.commit()
    db.refresh(user)
    _cancel_scim_active_user_retention(db_log, user)
    snapshot = _scim_user_audit_snapshot(db, user)
    _audit_scim_event(
        db_log,
        request,
        "SCIM_USER_CREATED",
        {
            "user_id": user.id,
            "external_id": snapshot["external_id"],
            "active": snapshot["active"],
            "role": snapshot["role"],
            "group_id": snapshot["group_id"],
            "scim_group_ids": snapshot["scim_group_ids"],
        },
    )
    return _scim_json(_user_to_scim_resource(user, db, request), status_code=status.HTTP_201_CREATED)


@scim_router.get("/Users/{user_id}")
def get_user_route(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _settings=Depends(_require_scim_auth),
):
    user = _find_scim_user(db, user_id)
    return _scim_json(_user_to_scim_resource(user, db, request))


@scim_router.put("/Users/{user_id}")
def replace_user_route(
    user_id: str,
    request: Request,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    settings=Depends(_require_scim_auth),
):
    user = _find_scim_user(db, user_id)
    before = _scim_user_audit_snapshot(db, user)
    _apply_scim_user_payload(db, user, payload, settings)
    db.commit()
    db.refresh(user)
    _cancel_scim_active_user_retention(db_log, user)
    after = _scim_user_audit_snapshot(db, user)
    _audit_scim_event(
        db_log,
        request,
        "SCIM_USER_REPLACED",
        {
            "user_id": user.id,
            "external_id": after["external_id"],
            "changes": _audit_value_changes(
                before,
                after,
                ("active", "role", "group_id", "scim_group_ids", "external_id"),
            ),
        },
    )
    return _scim_json(_user_to_scim_resource(user, db, request))


@scim_router.patch("/Users/{user_id}")
def patch_user_route(
    user_id: str,
    request: Request,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    settings=Depends(_require_scim_auth),
):
    operations = payload.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "SCIM PATCH requires Operations.", scim_type="invalidSyntax")
    user = _find_scim_user(db, user_id)
    before = _scim_user_audit_snapshot(db, user)
    _apply_patch_to_user(db, user, operations, settings)
    db.commit()
    db.refresh(user)
    _cancel_scim_active_user_retention(db_log, user)
    after = _scim_user_audit_snapshot(db, user)
    _audit_scim_event(
        db_log,
        request,
        "SCIM_USER_PATCHED",
        {
            "user_id": user.id,
            "operation_count": len(operations),
            "changes": _audit_value_changes(
                before,
                after,
                ("active", "role", "group_id", "scim_group_ids", "external_id"),
            ),
        },
    )
    return _scim_json(_user_to_scim_resource(user, db, request))


@scim_router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_route(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    settings=Depends(_require_scim_auth),
):
    audit_retention_policy = get_audit_log_user_deletion_retention_policy(db)
    user = _find_scim_user(db, user_id)
    _ensure_scim_user_mutable(user)
    target_user_id = user.id
    before = _scim_user_audit_snapshot(db, user)
    already_deleted = user.deleted_at is not None
    memberships_cleared = False
    if _sync_groups_enabled(settings):
        _replace_user_memberships(db, user, [], settings)
        memberships_cleared = True
    deletion_result = (
        {"status": "success", "account_deletion": {"effect": "already_deleted"}}
        if already_deleted
        else delete_user_with_retention(db, db_log, target_user_id, check_self_deletion=False)
    )
    if already_deleted and memberships_cleared:
        db.commit()
    changes: dict[str, dict[str, Any]] = {}
    if before["active"]:
        changes["active"] = {"old": before["active"], "new": False}
    if memberships_cleared and before["scim_group_ids"]:
        changes["scim_group_ids"] = {"old": before["scim_group_ids"], "new": []}
    audit_details = {
        "user_id": target_user_id,
        "changes": changes,
        "account_deletion": deletion_result.get("account_deletion") if isinstance(deletion_result, dict) else None,
    }
    if audit_retention_policy["delete_immediately"]:
        audit_details = pseudonymize_deleted_user_details(audit_details, target_user_id)

    _audit_scim_event(
        db_log,
        request,
        "SCIM_USER_DELETED",
        audit_details,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, media_type="application/scim+json")


@scim_router.get("/Groups")
def list_groups_route(
    request: Request,
    filter: str | None = None,
    startIndex: int = 1,
    count: int = 100,
    db: Session = Depends(get_db),
    settings=Depends(_require_scim_auth),
):
    _ = settings
    normalized_start_index = max(startIndex, 1)
    normalized_count = min(max(count, 0), 200)
    groups, total_results = _paginate_query(_filter_groups_query(db, filter), normalized_start_index, normalized_count)
    context = _group_scim_list_context(db, groups)
    items = [
        _group_to_scim_resource(
            group,
            db,
            request,
            link=context["links_by_group_id"].get(group.id),
            memberships=context["memberships_by_group_id"].get(group.id, []),
            users_by_id=context["users_by_id"],
        )
        for group in groups
    ]
    return _scim_json(_paginate(items, normalized_start_index, normalized_count, total_results=total_results))


@scim_router.post("/Groups")
def create_group_route(
    request: Request,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    settings=Depends(_require_scim_auth),
):
    group = _create_scim_group(db, payload, settings)
    db.commit()
    db.refresh(group)
    snapshot = _scim_group_audit_snapshot(db, group)
    _audit_scim_event(
        db_log,
        request,
        "SCIM_GROUP_CREATED",
        {
            "group_id": group.id,
            "external_id": snapshot["external_id"],
            "display_name": snapshot["display_name"],
            "member_user_ids": snapshot["member_user_ids"],
        },
    )
    return _scim_json(_group_to_scim_resource(group, db, request), status_code=status.HTTP_201_CREATED)


@scim_router.get("/Groups/{group_id}")
def get_group_route(
    group_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _settings=Depends(_require_scim_auth),
):
    group = _find_scim_group(db, group_id)
    return _scim_json(_group_to_scim_resource(group, db, request))


@scim_router.put("/Groups/{group_id}")
def replace_group_route(
    group_id: str,
    request: Request,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    settings=Depends(_require_scim_auth),
):
    group = _find_scim_group(db, group_id)
    before = _scim_group_audit_snapshot(db, group)
    _apply_scim_group_payload(db, group, payload, settings)
    db.commit()
    db.refresh(group)
    after = _scim_group_audit_snapshot(db, group)
    _audit_scim_event(
        db_log,
        request,
        "SCIM_GROUP_REPLACED",
        {
            "group_id": group.id,
            "external_id": after["external_id"],
            "changes": _audit_value_changes(
                before,
                after,
                ("display_name", "external_id", "member_user_ids"),
            ),
        },
    )
    return _scim_json(_group_to_scim_resource(group, db, request))


@scim_router.patch("/Groups/{group_id}")
def patch_group_route(
    group_id: str,
    request: Request,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    settings=Depends(_require_scim_auth),
):
    operations = payload.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "SCIM PATCH requires Operations.", scim_type="invalidSyntax")
    group = _find_scim_group(db, group_id)
    before = _scim_group_audit_snapshot(db, group)
    _apply_patch_to_group(db, group, operations, settings)
    db.commit()
    db.refresh(group)
    after = _scim_group_audit_snapshot(db, group)
    _audit_scim_event(
        db_log,
        request,
        "SCIM_GROUP_PATCHED",
        {
            "group_id": group.id,
            "operation_count": len(operations),
            "changes": _audit_value_changes(
                before,
                after,
                ("display_name", "external_id", "member_user_ids"),
            ),
        },
    )
    return _scim_json(_group_to_scim_resource(group, db, request))


@scim_router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group_route(
    group_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    settings=Depends(_require_scim_auth),
):
    group = _find_scim_group(db, group_id)
    before = _scim_group_audit_snapshot(db, group)
    if _sync_groups_enabled(settings):
        memberships = _membership_rows_for_group(db, group.id)
        for membership in memberships:
            user = _find_scim_user(db, membership.user_id)
            _remove_group_member(db, group, user, settings)
        db.flush()
    (
        db.query(ScimGroupLink)
        .filter(ScimGroupLink.group_id == group.id)
        .delete(synchronize_session=False)
    )
    orm_delete_group(group.id, db)
    _audit_scim_event(
        db_log,
        request,
        "SCIM_GROUP_DELETED",
        {
            "group_id": group_id,
            "display_name": before["display_name"],
            "external_id": before["external_id"],
            "member_user_ids": before["member_user_ids"],
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, media_type="application/scim+json")
