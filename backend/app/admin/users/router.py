from functools import partial
import json
import logging
from typing import Any, Optional

import anyio
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.admin.users.models import (
    count_active_administrators,
)
from app.admin.users.schemas import (
    AdminChangeRoleRequest,
    AdminPendingDeletionUser,
    AdminUserActive,
    AdminUserChatMessagesRequest,
    AdminUserChatsRequest,
    AdminUserCreate,
    AdminUserIdRequest,
    AdminUserList,
    AdminUserPickerPage,
    AdminUserProfileReadRequest,
    AdminUserProfileResponse,
    AdminUserProfileUpdate,
    AdminUserSecurityActionRequest,
    AdminUserSettingsBulkUpdate,
    AdminUserSettingsBulkUpdateResult,
    AdminUserSettingsSchemaQuery,
)
from app.admin.users.settings_schema import (
    UserSettingsFormSchema,
    build_user_settings_schema,
)
from app.admin.users.utils import (
    admin_get_users_csv_template,
    admin_get_users_xlsx_template,
    MAX_UPLOAD_BYTES,
    parse_user_import_form_options,
)
from app.workers.operations import (
    enqueue_import_job,
    enqueue_import_job_async,
    stage_import_stream,
    wait_for_operations_result,
    wait_for_operations_result_async,
)
from app.auth.twofa_provider import clear_user_twofa_state
from app.chats.schemas import Chat, ChatMessage
from app.chats.utils import get_chat_messages, list_chats
from app.database import AuditSessionLocal, SessionLocal
from app.dependencies import get_db, get_db_log, verified_access_token, verified_admin
from app.auth.step_up import require_sensitive_action_auth
from app.utils.origin import enforce_same_origin
from app.utils.blocking_io import run_blocking_io
from app.utils.db import release_db_session_before_long_wait
from app.logging.models import (
    create_audit_log,
    get_audit_request_ip,
    pseudonymize_deleted_user_details,
)
from app.users.init import update_user_settings_bulk
from app.users.models import (
    cancel_scheduled_deletion,
    change_user_role,
    get_user,
    list_pending_deletion_users,
    set_user_activation_status,
)
from app.users.roles import (
    ADMIN_ROLE,
    ASSIGNABLE_ROLES,
    is_admin_role,
    is_owner_role,
)
from app.users.external_management import (
    is_externally_managed,
    require_externally_managed_settings_update_allowed,
    require_locally_managed_account,
)
from app.users.utils import (
    admin_update_user_profile,
    create_user_via_admin,
    delete_user,
    get_admin_user_profile,
    get_audit_log_user_deletion_retention_policy,
    get_user_list,
    get_user_list_page,
    restore_deleted_user,
)
from app.utils.schemas import OperationResult

_BULK_USER_AUDIT_CREATED_USERS_LIMIT = 100


def _active_admin_count(db: Session) -> int:
    """Count active owner/admin accounts that can keep the instance manageable."""

    return count_active_administrators(db)


def _ensure_last_active_admin_not_removed(
    *,
    db: Session,
    target_user,
    new_role: str | None = None,
    new_active: bool | None = None,
) -> None:
    """Prevent removal of the final active administrative account."""

    if not is_admin_role(getattr(target_user, "role", None)):
        return

    removes_admin_role = new_role is not None and not is_admin_role(new_role)
    deactivates_admin = new_active is False and bool(
        getattr(target_user, "is_active", True)
    )
    if not removes_admin_role and not deactivates_admin:
        return

    if bool(getattr(target_user, "is_active", True)) and _active_admin_count(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove or deactivate the last active admin.",
        )


def _ensure_admin_account_change_allowed(
    *,
    db: Session,
    admin_user,
    target_user,
    new_role: str | None = None,
    new_active: bool | None = None,
) -> None:
    """Enforce the owner/admin hierarchy for every account mutation.

    Admins may manage ordinary users. Only the owner may grant the admin role
    or modify an existing admin account. The owner account itself cannot be
    modified by another administrator.
    """

    actor_role = getattr(admin_user, "role", None)
    target_role = getattr(target_user, "role", None)
    is_self = getattr(target_user, "id", None) == getattr(admin_user, "id", None)

    if is_owner_role(target_role) and not is_self:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The owner account cannot be modified by another administrator.",
        )

    changes_admin_account = target_role == ADMIN_ROLE and not is_self
    grants_admin_role = new_role == ADMIN_ROLE
    if (changes_admin_account or grants_admin_role) and not is_owner_role(actor_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can modify administrator accounts.",
        )

    _ensure_last_active_admin_not_removed(
        db=db,
        target_user=target_user,
        new_role=new_role,
        new_active=new_active,
    )


def _build_bulk_user_upload_audit_details(
    *,
    filename: str | None,
    result: dict[str, Any],
    file_type: str | None = None,
) -> dict[str, Any]:
    created_users = result.get("created_users")
    if not isinstance(created_users, list):
        created_users = []

    created_user_targets: list[dict[str, str]] = []
    for user in created_users[:_BULK_USER_AUDIT_CREATED_USERS_LIMIT]:
        if not isinstance(user, dict):
            continue
        target: dict[str, str] = {}
        user_id = str(user.get("id") or user.get("user_id") or "").strip()
        if user_id:
            target["user_id"] = user_id
        if target:
            created_user_targets.append(target)

    details: dict[str, Any] = {
        "filename": filename,
        "status": result.get("status"),
        "total_created": result.get("total_created"),
        "total_errors": result.get("total_errors"),
        "created_users": created_user_targets,
        "created_users_logged": len(created_user_targets),
        "created_users_omitted": max(
            0, len(created_users) - _BULK_USER_AUDIT_CREATED_USERS_LIMIT
        ),
    }
    if file_type:
        details["file_type"] = file_type
    if "force_password_change" in result:
        details["force_password_change"] = bool(result.get("force_password_change"))
    return details


def _read_admin_user_profile(
    *,
    user_id: str,
    include_sensitive_profile: bool,
    include_security: bool,
    include_activity: bool,
    reason: str | None,
    request: Request,
    db: Session,
    db_log: Session,
    admin_user,
):
    viewed_categories = ["basic_profile"]
    include_sensitive_details = (
        include_sensitive_profile or include_security or include_activity
    )
    normalized_reason = (reason or "").strip() or None

    if include_sensitive_details and not normalized_reason:
        raise HTTPException(
            status_code=400,
            detail="An admin access reason is required to view sensitive profile details.",
        )

    target_user = get_user(db, user_id)
    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
    )

    if include_sensitive_profile:
        viewed_categories.append("sensitive_profile")
    if include_security:
        viewed_categories.append("account_security")
    if include_activity:
        viewed_categories.append("activity")

    profile = get_admin_user_profile(
        user_id,
        db,
        include_sensitive_profile=include_sensitive_profile,
        include_security=include_security,
        include_activity=include_activity,
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_USER_PROFILE",
        reason=normalized_reason,
        details={
            "target_user": user_id,
            "viewed_categories": viewed_categories,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return profile


logger = logging.getLogger(__name__)
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin_router.post("/user/active", response_model=OperationResult)
def activate_user_route(
    payload: AdminUserActive,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Activate or deactivate a user account."""
    if payload.user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot change their own activation status.",
        )
    is_active = bool(payload.value)
    target_user = get_user(db, payload.user_id)

    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
        new_active=is_active,
    )
    set_user_activation_status(db, payload.user_id, is_active)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="ACTIVATE_USER",
        details={"user_id": payload.user_id, "is_active": is_active},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {"status": "success"}


@admin_router.post("/user/delete", response_model=OperationResult)
def delete_user_route(
    payload: AdminUserIdRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
    token: str = Depends(verified_access_token),
):
    """Soft delete a user account."""
    enforce_same_origin(request, db)
    require_sensitive_action_auth(admin_user, token, db)
    audit_retention_policy = get_audit_log_user_deletion_retention_policy(db)
    try:
        if payload.user_id == admin_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot delete their own account.",
            )
        target_user = get_user(db, payload.user_id)
        _ensure_admin_account_change_allowed(
            db=db,
            admin_user=admin_user,
            target_user=target_user,
        )
        delete_user(
            db,
            db_log,
            payload.user_id,
            check_self_deletion=False,
            allow_administrative_target=is_owner_role(
                getattr(admin_user, "role", None)
            ),
        )
    except HTTPException as exc:
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="DELETE_USER",
            details={
                "user_id": payload.user_id,
                "status": "failed",
                "detail": exc.detail,
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="admin",
        )
        raise

    audit_details: dict[str, Any] = {"user_id": payload.user_id}
    if audit_retention_policy["delete_immediately"]:
        audit_details = pseudonymize_deleted_user_details(
            audit_details, payload.user_id
        )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DELETE_USER",
        details=audit_details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {"status": "success"}


@admin_router.get(
    "/users/pending-deletion", response_model=list[AdminPendingDeletionUser]
)
def list_pending_deletion_users_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List users scheduled for deletion."""
    users = list_pending_deletion_users(db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_PENDING_DELETION_USERS",
        details={"count": len(users)},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return users


@admin_router.post("/user/restore", response_model=OperationResult)
def restore_user_route(
    payload: AdminUserIdRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Restore a soft-deleted user account."""
    target_user = get_user(db, payload.user_id)
    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
    )
    restore_deleted_user(db, db_log, payload.user_id)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="RESTORE_USER",
        details={"user_id": payload.user_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {"status": "success"}


@admin_router.post("/user/hard-delete", response_model=OperationResult)
def hard_delete_user_route(
    payload: AdminUserIdRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
    token: str = Depends(verified_access_token),
):
    """Permanently delete a user account (irreversible)."""
    enforce_same_origin(request, db)
    require_sensitive_action_auth(admin_user, token, db)
    audit_retention_policy = get_audit_log_user_deletion_retention_policy(db)
    if payload.user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot delete their own account.",
        )
    target_user = get_user(db, payload.user_id)
    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
    )
    delete_user(
        db,
        db_log,
        payload.user_id,
        check_self_deletion=False,
        force_hard_delete=True,
        allow_administrative_target=is_owner_role(getattr(admin_user, "role", None)),
    )
    audit_details: dict[str, Any] = {"user_id": payload.user_id}
    if audit_retention_policy["delete_immediately"]:
        audit_details = pseudonymize_deleted_user_details(
            audit_details, payload.user_id
        )
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="HARD_DELETE_USER",
        details=audit_details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {"status": "success"}


@admin_router.post("/user/cancel-deletion", response_model=OperationResult)
def cancel_scheduled_deletion_route(
    payload: AdminUserIdRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Cancel scheduled deletion for a soft-deleted user."""
    target_user = get_user(db, payload.user_id)
    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
    )
    cancel_scheduled_deletion(db, payload.user_id)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="CANCEL_SCHEDULED_DELETION",
        details={"user_id": payload.user_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {"status": "success"}


@admin_router.get("/user/settings", response_model=UserSettingsFormSchema)
def admin_get_user_settings_schema(
    request: Request,
    query: AdminUserSettingsSchemaQuery = Depends(),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get user settings schema."""
    target_user = get_user(db, query.user_id)
    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
    )
    schema = build_user_settings_schema(
        db,
        query.include_values,
        query.user_id,
        externally_managed=is_externally_managed(target_user),
    )
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="GET_USER_SETTINGS_SCHEMA",
        details={
            "include_values": query.include_values,
            "target_user": query.user_id,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return schema


@admin_router.patch("/user/settings", response_model=AdminUserSettingsBulkUpdateResult)
def admin_update_user_settings(
    payload: AdminUserSettingsBulkUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Bulk update user settings."""
    target_user = get_user(db, payload.user_id)
    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
    )
    require_externally_managed_settings_update_allowed(
        target_user,
        payload.settings,
    )

    updated = update_user_settings_bulk(
        payload.user_id,
        payload.settings,
        db,
        allow_secret_page=False,
    )
    status_value = "success" if updated else "noop"

    if updated:
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="UPDATE_USER_SETTINGS",
            details={
                "target_user": payload.user_id,
                "pages": sorted(updated.keys()),
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="admin",
        )

    return AdminUserSettingsBulkUpdateResult(status=status_value, updated=updated)


@admin_router.post("/user/security/reset-2fa", response_model=OperationResult)
def admin_reset_user_twofa_route(
    payload: AdminUserSecurityActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Reset a user's 2FA enrollment and pending verification state."""
    if payload.user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot reset their own 2FA via this endpoint.",
        )

    target_user = get_user(db, payload.user_id)
    require_locally_managed_account(target_user)
    _ensure_admin_account_change_allowed(
        db=db, admin_user=admin_user, target_user=target_user
    )

    clear_user_twofa_state(payload.user_id, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="RESET_USER_2FA",
        reason=payload.reason.strip(),
        details={"target_user": payload.user_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {"status": "success"}


@admin_router.patch("/user/role/change", response_model=OperationResult)
def change_role_route(
    payload: AdminChangeRoleRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Change a user's role."""
    if payload.user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot change their own role.",
        )
    if payload.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role.")
    target_user = get_user(db, payload.user_id)

    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
        new_role=payload.role,
    )

    old_role = target_user.role
    change_user_role(payload.user_id, payload.role, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="CHANGE_USER_ROLE",
        reason=(
            payload.reason.strip()
            if payload.reason and payload.reason.strip()
            else None
        ),
        details={
            "user_id": payload.user_id,
            "target_user": payload.user_id,
            "role": payload.role,
            "old_role": old_role,
            "new_role": payload.role,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {"status": "success"}


@admin_router.patch("/user/profile", response_model=OperationResult)
def admin_update_user_profile_route(
    payload: AdminUserProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
    token: str = Depends(verified_access_token),
):
    """Update a user's profile information."""
    requested_email = getattr(payload, "email", None)
    requested_password = getattr(payload, "password", None)
    if (
        str(payload.user_id) == str(admin_user.id)
        and requested_password is not None
        and requested_password.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Use the account password change page to change your own password.",
        )

    target_user = get_user(db, payload.user_id)
    if (
        (
            requested_email is not None
            and str(requested_email).strip().lower()
            != str(target_user.email).strip().lower()
        )
        or (
            requested_password is not None
            and bool(requested_password.strip())
        )
    ):
        enforce_same_origin(request, db)
        require_sensitive_action_auth(admin_user, token, db)
    _ensure_admin_account_change_allowed(
        db=db,
        admin_user=admin_user,
        target_user=target_user,
    )

    from app.email.service import security_request_context

    result = admin_update_user_profile(
        payload,
        db,
        security_context=security_request_context(request, db),
    )

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPDATE_USER_PROFILE",
        reason=(
            payload.reason.strip()
            if payload.reason and payload.reason.strip()
            else None
        ),
        details={
            "target_user": payload.user_id,
            "updated_fields": result.get("updated_fields", []),
            "changes": result.get("changes", []),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )

    return {"status": "success"}


@admin_router.post(
    "/user/profile",
    response_model=AdminUserProfileResponse,
    response_model_exclude_none=True,
)
def admin_read_user_profile_route(
    payload: AdminUserProfileReadRequest,
    request: Request = None,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Get a user's profile information with access reasons carried in the request body."""
    return _read_admin_user_profile(
        user_id=payload.user_id,
        include_sensitive_profile=payload.include_sensitive_profile,
        include_security=payload.include_security,
        include_activity=payload.include_activity,
        reason=payload.reason,
        request=request,
        db=db,
        db_log=db_log,
        admin_user=admin_user,
    )


@admin_router.post("/user/chats", response_model=list[Chat])
def admin_get_user_chats_route(
    payload: AdminUserChatsRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_admin),
):
    """Get chats for a specific user."""
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="GET_CHATS_FOR_USER",
        reason=payload.reason,
        details={"target_user": payload.user_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return list_chats(
        user_id=payload.user_id,
        db=db,
        include_archived=True,
    )


@admin_router.post("/user/chat/messages", response_model=list[ChatMessage])
def admin_get_user_chat_messages_route(
    payload: AdminUserChatMessagesRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_admin),
):
    """Get messages for a specific user and chat."""
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="GET_CHAT_MESSAGES_FOR_USER",
        reason=payload.reason,
        details={"target_user": payload.user_id, "chat_id": payload.chat_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return get_chat_messages(
        user_id=payload.user_id,
        chat_id=payload.chat_id,
        db=db,
    )


@admin_router.post("/user/create", response_model=OperationResult)
def admin_create_user(
    user: AdminUserCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Create a new user account."""
    result = create_user_via_admin(user, db, db_log)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="CREATE_USER",
        details={
            "email": user.email,
            "status": result.get("status"),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return result


@admin_router.get("/users", response_model=list[AdminUserList])
def admin_list_users_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
    search: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """List all users."""
    users = get_user_list(db, search=search, limit=limit)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_USERS",
        details={"count": len(users)},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return users


@admin_router.get("/users/picker", response_model=AdminUserPickerPage)
def admin_user_picker_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
    search: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Return one user-picker page for admin modals that need lazy loading."""
    page = get_user_list_page(db, search=search, limit=limit, offset=offset)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_USERS_PICKER",
        details={
            "count": len(page["users"]),
            "total": page["total"],
            "offset": page["offset"],
            "limit": page["limit"],
            "has_search": bool(search),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return page


@admin_router.get("/users/create/xlsx/template")
def get_users_xlsx_template_route(
    locale: str = Query("en_US", min_length=2, max_length=10), _=Depends(verified_admin)
):
    """Get an XLSX template for creating users in bulk."""
    return admin_get_users_xlsx_template(locale)


@admin_router.get("/users/create/csv/template")
def get_users_csv_template_route(request: Request, _=Depends(verified_admin)):
    """Get a CSV template for creating users in bulk."""
    accept_language = request.headers.get("Accept-Language")
    return admin_get_users_csv_template(accept_language)


@admin_router.post("/users/create/bulk")
def upload_users_bulk_route(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
    default_password: str = Form(...),
    force_password_change: bool = Form(True),
):
    """Upload a list of users from an XLSX or CSV file."""
    filename = str(file.filename or "").strip().lower()
    if not filename:
        raise HTTPException(status_code=400, detail="No file provided")
    if not str(default_password or "").strip():
        raise HTTPException(status_code=400, detail="Default password is required")
    if filename.endswith(".xlsx"):
        file_type = "xlsx"
    elif filename.endswith(".csv"):
        file_type = "csv"
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Please upload an XLSX or CSV file.",
        )
    try:
        file.file.seek(0, 2)
        if file.file.tell() > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds the 5 MB limit")
        file.file.seek(0)
        staged_name = stage_import_stream(
            file.file,
            extension=file_type,
            principal_id=admin_user.id,
            import_kind="import_bulk_users",
        )
    finally:
        file.file.close()
    job = enqueue_import_job(
        db,
        kind="import_bulk_users",
        staged_name=staged_name,
        user_id=admin_user.id,
        options={
            "default_password": default_password,
            "force_password_change": force_password_change,
        },
    )
    result = wait_for_operations_result(job)
    result.pop("file_type", None)
    result["force_password_change"] = force_password_change
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UPLOAD_USERS_BULK",
        details=_build_bulk_user_upload_audit_details(
            filename=file.filename, result=result, file_type=file_type
        ),
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return result


@admin_router.post("/users/import")
async def admin_import_users_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Import selected users from a canonical admin ZIP archive."""
    admin_user_id = str(admin_user.id)
    allow_administrative_targets = is_owner_role(getattr(admin_user, "role", None))
    audit_user_agent = request.headers.get("user-agent")
    release_db_session_before_long_wait(db)
    content_type = str(request.headers.get("content-type") or "").lower()
    if "multipart/form-data" not in content_type:
        raise HTTPException(
            status_code=400,
            detail="Please upload a canonical users ZIP archive.",
        )

    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, StarletteUploadFile):
        raise HTTPException(
            status_code=400, detail="Please upload a valid users archive."
        )

    import_options = parse_user_import_form_options(form)

    selected_indices_raw = form.get("selected_indices")
    selected_indices = None
    if selected_indices_raw not in (None, ""):
        try:
            parsed_indices = json.loads(str(selected_indices_raw))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400, detail="selected_indices must be valid JSON."
            ) from exc
        if not isinstance(parsed_indices, list):
            raise HTTPException(
                status_code=400, detail="selected_indices must be a JSON array."
            )
        selected_indices = []
        for value in parsed_indices:
            if not isinstance(value, int):
                raise HTTPException(
                    status_code=400,
                    detail="selected_indices must contain only integers.",
                )
            selected_indices.append(value)

    try:
        await upload.seek(0)
        staged_name = await run_blocking_io(
            partial(
                stage_import_stream,
                upload.file,
                extension="zip",
                principal_id=admin_user_id,
                import_kind="import_admin_users",
            )
        )
    finally:
        await upload.close()
    job = await enqueue_import_job_async(
        kind="import_admin_users",
        staged_name=staged_name,
        user_id=admin_user_id,
        options={
            "selected_indices": selected_indices,
            "import_options": import_options,
            "allow_administrative_targets": allow_administrative_targets,
        },
    )
    result = await wait_for_operations_result_async(job)

    def _sync_create_audit_log() -> None:
        """Resolve the audit IP and write the event away from the event loop."""
        thread_db = SessionLocal()
        try:
            # Client-IP resolution can query trusted-proxy settings, so it must
            # use a session created in this worker thread.
            audit_ip_address = get_audit_request_ip(request, thread_db)
            thread_db_log = AuditSessionLocal()
            try:
                create_audit_log(
                    db_log=thread_db_log,
                    user_id=admin_user_id,
                    action="IMPORT_USERS_ADMIN",
                    details={
                        "created_count": len(result.get("created", [])),
                        "updated_count": len(result.get("updated", [])),
                        "created_files_count": int(
                            result.get("created_files_count", 0)
                        ),
                        "skipped_files_count": int(
                            result.get("skipped_files_count", 0)
                        ),
                        "created_notes_count": int(
                            result.get("created_notes_count", 0)
                        ),
                        "skipped_notes_count": int(
                            result.get("skipped_notes_count", 0)
                        ),
                        "created_memories_count": int(
                            result.get("created_memories_count", 0)
                        ),
                        "deduped_memories_count": int(
                            result.get("deduped_memories_count", 0)
                        ),
                        "warning_count": len(result.get("warnings", [])),
                        "error_count": len(result.get("errors", [])),
                        "force_password_change": bool(
                            result.get("force_password_change")
                        ),
                    },
                    ip_address=audit_ip_address,
                    user_agent=audit_user_agent,
                    category="admin",
                )
            finally:
                thread_db_log.close()
        finally:
            thread_db.close()

    await anyio.to_thread.run_sync(_sync_create_audit_log)
    return result
