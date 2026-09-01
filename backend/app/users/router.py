import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, UploadFile, File, Request, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_access_token, verified_user
from app.auth.step_up import require_sensitive_action_auth
from app.middleware.ip_restriction import get_client_ip
from app.utils.origin import enforce_same_origin
from app.redis_client import get_redis_client
from app.users.init import update_user_settings_bulk
from app.users.schemas import (
    UpdateUserColorTheme, 
    UserPersonalDetails, 
    ChangePassword,
    SetPassword,
    UpdateUserLastModel,
    UpdateUserGeneralSettingsToogle,
    UpdateUserSettingsSelect,
    DetectedLocaleDefaults,
    LocaleDefaultsResult,
    UpdateUserLocationRequest,
    UpdateUserPinnedModels,
    UpdateUserPersonalitySettings,
    UpdatePrivacyPolicyNoticeState,
    AcceptTermsOfServiceState,
    PublicUserSharingSummary,
    SidebarButtonVisibilityUpdate,
    DeleteAccountResponse,
    SharedItemsResponse,
) 
from app.groups.init import ensure_data_control_permission
from app.files.sharing import delete_expired_artifact_shares, is_artifact_share_expired
from app.logging.models import create_audit_log, get_audit_request_ip
from app.users.shared_items import build_shared_item_url, get_shared_item_capabilities
from app.users.sharing import (
    DEFAULT_PUBLIC_USER_DISCOVERY_LIMIT,
    MAX_PUBLIC_USER_DISCOVERY_LIMIT,
    get_public_users_for_sharing,
)
from app.users.utils import (
    update_user_toggle_setting,
    update_user_select_setting,
    update_user_color_theme,
    upload_profile_picture,
    delete_profile_picture,
    get_profile_picture,
    update_user_personal_details,
    change_password, 
    change_password_init,
    set_password_for_social_user,
    delete_user,
    set_user_last_model,
    update_user_pinned_models,
    update_sidebar_button_visibility,
    user_settings_init,
    update_user_personality_settings,
    update_user_location,
    initialize_user_locale_defaults,
    dismiss_user_welcome_card,
    build_user_data_export_audit_details,
    get_audit_log_user_deletion_retention_policy,
)
from app.utils.schemas import OperationResult
from app.utils.utils import get_privacy_policy_notice_policy, get_terms_of_service_policy
from app.workers.operations import (
    enqueue_import_job,
    enqueue_user_data_export,
    resolve_operations_result_path,
    stage_import_json,
    wait_for_operations_result,
)



logger = logging.getLogger(__name__)

users_router = APIRouter(prefix="/api/v1/users", tags=["users"])


_CHANGE_PASSWORD_RATE_LIMIT_WINDOW_SECONDS = 5 * 60
_CHANGE_PASSWORD_RATE_LIMIT_PER_USER = 12
_CHANGE_PASSWORD_RATE_LIMIT_PER_IP = 24
_CHANGE_PASSWORD_FAILURE_WINDOW_SECONDS = 15 * 60
_CHANGE_PASSWORD_FAILURE_LIMIT_PER_USER = 5
_CHANGE_PASSWORD_FAILURE_LIMIT_PER_IP = 10
_CHANGE_PASSWORD_LOCKOUT_SECONDS = 15 * 60


def _password_change_client_ip(request: Request, db: Session) -> str | None:
    # Critical: do not trust spoofable proxy headers unless they come from a trusted proxy.
    # Reuse the hardened helper used elsewhere in the backend.
    return get_client_ip(request, db)


def _redis_fixed_window_counter(
    client,
    *,
    key_prefix: str,
    window_seconds: int,
    now_ts: int,
) -> tuple[str, int, int]:
    """Increment a fixed-window counter stored in Redis.

    Returns: (window_key, current_count, retry_after_seconds)
    """
    window_start = now_ts - (now_ts % window_seconds)
    window_key = f"{key_prefix}:{window_start}"
    current_count = int(client.incr(window_key))
    if current_count == 1:
        client.expire(window_key, window_seconds + 1)
    retry_after = int(client.ttl(window_key) or window_seconds)
    return window_key, current_count, max(1, retry_after)


def _redis_fixed_window_start(*, window_seconds: int, now_ts: int) -> int:
    return now_ts - (now_ts % window_seconds)


def _password_change_subjects(user_id: str, client_ip: str | None) -> list[tuple[str, str, int]]:
    subjects = [("user", user_id, _CHANGE_PASSWORD_RATE_LIMIT_PER_USER)]
    if client_ip:
        subjects.append(("ip", client_ip, _CHANGE_PASSWORD_RATE_LIMIT_PER_IP))
    return subjects


def _password_change_lock_subjects(user_id: str, client_ip: str | None) -> list[tuple[str, str, int]]:
    subjects = [("user", user_id, _CHANGE_PASSWORD_FAILURE_LIMIT_PER_USER)]
    if client_ip:
        subjects.append(("ip", client_ip, _CHANGE_PASSWORD_FAILURE_LIMIT_PER_IP))
    return subjects


def _enforce_change_password_rate_limits(user_id: str, client_ip: str | None) -> None:
    client = get_redis_client()
    if client is None:
        return

    now_ts = int(time.time())

    try:
        for scope, subject, limit in _password_change_subjects(user_id, client_ip):
            _window_key, current_count, retry_after = _redis_fixed_window_counter(
                client,
                key_prefix=f"omlorix:password_change:rate:{scope}:{subject}",
                window_seconds=_CHANGE_PASSWORD_RATE_LIMIT_WINDOW_SECONDS,
                now_ts=now_ts,
            )
            if current_count > limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many password change attempts. Please retry later.",
                    headers={"Retry-After": str(max(1, retry_after))},
                )
    except HTTPException:
        raise
    except Exception:
        logger.warning("Password change route rate limiting unavailable", exc_info=True)


def _enforce_change_password_lockouts(user_id: str, client_ip: str | None) -> None:
    client = get_redis_client()
    if client is None:
        return

    try:
        for scope, subject, _limit in _password_change_lock_subjects(user_id, client_ip):
            lock_key = f"omlorix:password_change:lock:{scope}:{subject}"
            ttl = int(client.ttl(lock_key) or 0)
            if ttl > 0:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed password change attempts. Please retry later.",
                    headers={"Retry-After": str(ttl)},
                )
    except HTTPException:
        raise
    except Exception:
        logger.warning("Password change lockout check unavailable", exc_info=True)


def _record_change_password_failure(user_id: str, client_ip: str | None) -> dict[str, object]:
    client = get_redis_client()
    if client is None:
        return {"locked": False}

    now_ts = int(time.time())
    locked_scopes: list[str] = []

    try:
        for scope, subject, limit in _password_change_lock_subjects(user_id, client_ip):
            lock_key = f"omlorix:password_change:lock:{scope}:{subject}"

            _failure_key, current_count, _retry_after = _redis_fixed_window_counter(
                client,
                key_prefix=f"omlorix:password_change:failure:{scope}:{subject}",
                window_seconds=_CHANGE_PASSWORD_FAILURE_WINDOW_SECONDS,
                now_ts=now_ts,
            )
            if current_count >= limit:
                client.set(lock_key, "1", ex=_CHANGE_PASSWORD_LOCKOUT_SECONDS)
                locked_scopes.append(scope)
        return {"locked": bool(locked_scopes), "locked_scopes": locked_scopes}
    except Exception:
        logger.warning("Password change failure tracking unavailable", exc_info=True)
        return {"locked": False}


def _clear_change_password_failures(user_id: str, client_ip: str | None) -> None:
    client = get_redis_client()
    if client is None:
        return

    now_ts = int(time.time())
    keys: list[str] = []
    for scope, subject, _limit in _password_change_lock_subjects(user_id, client_ip):
        window_start = _redis_fixed_window_start(
            window_seconds=_CHANGE_PASSWORD_FAILURE_WINDOW_SECONDS,
            now_ts=now_ts,
        )
        keys.append(f"omlorix:password_change:failure:{scope}:{subject}:{window_start}")
        keys.append(f"omlorix:password_change:lock:{scope}:{subject}")

    if not keys:
        return

    try:
        client.delete(*keys)
    except Exception:
        logger.warning("Password change failure cleanup unavailable", exc_info=True)


def _classify_password_change_failure(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, str) else ""
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        return "rate_limited"
    if detail == "Old password is incorrect.":
        return "old_password_incorrect"
    if detail == "New password must be different from the current password.":
        return "password_reuse"
    if detail == "Password change is not enabled.":
        return "password_change_disabled"
    if "Password must" in detail:
        return "password_policy_rejected"
    if exc.status_code < 500:
        return "validation_error"
    return "server_error"



@users_router.patch("/settings/locale-defaults", response_model=LocaleDefaultsResult)
def initialize_locale_defaults_route(
    payload: DetectedLocaleDefaults,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    """Fill blank locale preferences from browser-provided detection signals."""

    enforce_same_origin(request, db)
    payload_data = payload.model_dump(exclude_none=True)
    result = initialize_user_locale_defaults(db, user.id, **payload_data)
    updated_fields = sorted((result.get("updated") or {}).get("general", {}).keys())

    if updated_fields:
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="AUTO_INITIALIZE_LOCALE_DEFAULTS",
            details={"updated_fields": updated_fields},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="user",
        )

    return result


@users_router.post("/welcome-card/dismiss", response_model=OperationResult)
def dismiss_welcome_card_route(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(verified_user),
):
    """Dismiss the optional first-run welcome card for the current account."""

    enforce_same_origin(request, db)
    return dismiss_user_welcome_card(db, user.id)



# -------------------
# Update user settings toogle
# -------------------
@users_router.patch("/settings/toogle")
def update_user_settings_toogle_route(
    payload: UpdateUserGeneralSettingsToogle,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    payload_data = payload.model_dump(exclude_unset=True)
    updated = update_user_toggle_setting(db, user.id, **payload_data)

    toggled_fields = list(payload_data.keys())
    updated_pages = list((updated.get("updated") or {}).keys()) if isinstance(updated, dict) else []
    details = {
        "toggled_field": toggled_fields[0] if toggled_fields else None,
        "updated_pages": updated_pages,
        "status": updated.get("status") if isinstance(updated, dict) else None,
    }

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="UPDATE_SETTINGS_TOGGLE",
        details=details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return updated



# -------------------
# Update user personality settings
# -------------------
@users_router.patch("/settings/personality")
def update_user_personality_settings_route(
    payload: UpdateUserPersonalitySettings,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    payload_data = payload.model_dump(exclude_unset=True)
    result = update_user_personality_settings(db, user.id, **payload_data)
    updated_chat = (result.get("updated") or {}).get("chat", {}) if isinstance(result, dict) else {}

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="UPDATE_SETTINGS_PERSONALITY",
        details={
            "provided_fields": list(payload_data.keys()),
            "status": result.get("status") if isinstance(result, dict) else None,
            "preset": str(updated_chat.get("personality_preset") or ""),
            "has_custom_instruction": bool(str(updated_chat.get("personality_custom_instruction") or "").strip()),
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return result



# -------------------
# Update user settings select
# -------------------
@users_router.patch("/settings/select")
def update_user_settings_select_route(
    payload: UpdateUserSettingsSelect,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    payload_data = payload.model_dump(exclude_unset=True)
    updated = update_user_select_setting(db, user.id, **payload_data)

    provided_fields = list(payload_data.keys())
    updated_pages = list((updated.get("updated") or {}).keys()) if isinstance(updated, dict) else []
    details = {
        "select_field": provided_fields[0] if provided_fields else None,
        "updated_pages": updated_pages,
        "status": updated.get("status") if isinstance(updated, dict) else None,
    }

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="UPDATE_SETTINGS_SELECT",
        details=details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return updated


# -------------------
# Update user color theme
# -------------------
@users_router.patch("/color-theme/update")
def update_user_color_theme_route(
    payload: UpdateUserColorTheme,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    result = update_user_color_theme(user.id, db, payload.theme, payload.color_theme)

    details = {
        "theme": getattr(payload.theme, "value", payload.theme),
        "color_theme": getattr(payload.color_theme, "value", payload.color_theme),
        "status": result.get("status") if isinstance(result, dict) else None,
    }

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="UPDATE_COLOR_THEME",
        details=details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return result



# -------------------
# Upload profile picture
# -------------------
@users_router.post("/profile-picture/upload")
def upload_profile_picture_route(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    result = upload_profile_picture(user.id, file, db)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="UPLOAD_PROFILE_PICTURE",
        details={"filename": file.filename},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )
    return result



# -------------------
# Delete profile picture
# -------------------
@users_router.delete("/profile-picture/delete")
def delete_profile_picture_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    result = delete_profile_picture(user.id, db)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="DELETE_PROFILE_PICTURE",
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )
    return result



# -------------------
# Get profile picture
# -------------------
@users_router.get("/profile-picture/get")
def get_profile_picture_route(db: Session = Depends(get_db), user = Depends(verified_user)):
    return get_profile_picture(user.id, db)


# -------------------
# Get profile picture by account slot (for sidebar accounts)
# -------------------
@users_router.get("/profile-picture/slot/{slot}")
def get_profile_picture_by_slot_route(
    slot: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(verified_user),
):
    from app.auth.account_slots import resolve_browser_account_slot

    account = resolve_browser_account_slot(slot, request, db)
    if not account:
        raise HTTPException(status_code=404, detail="Account slot not found")
    return get_profile_picture(account.user_id, db)



# -------------------
# Update user personal details
# -------------------
@users_router.post("/personal-details/update")
def update_user_personal_details_route(
    user_personal_details: UserPersonalDetails,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    enforce_same_origin(request, db)
    requested_email = (
        str(user_personal_details.email).strip().lower()
        if user_personal_details.email is not None
        else None
    )
    if requested_email and requested_email != str(user.email or "").strip().lower():
        require_sensitive_action_auth(user, token, db)
    result = update_user_personal_details(user.id, db, user_personal_details)

    provided_fields = []
    for field in ("first_name", "last_name", "email"):
        value = getattr(user_personal_details, field, None)
        if value is not None:
            provided_fields.append(field)

    details = {
        "provided_fields": provided_fields,
        "has_payload": bool(provided_fields),
    }

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="UPDATE_PERSONAL_DETAILS",
        details=details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return result



# -------------------
# Change password
# -------------------
@users_router.post("/password/change")
def change_password_route(
    change_password_data: ChangePassword,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    client_ip = _password_change_client_ip(request, db)
    _enforce_change_password_lockouts(user.id, client_ip)
    _enforce_change_password_rate_limits(user.id, client_ip)

    try:
        from app.email.service import security_request_context

        result = change_password(
            user.id,
            change_password_data.old_password,
            change_password_data.new_password,
            db,
            security_context=security_request_context(request, db),
        )
    except HTTPException as exc:
        if isinstance(exc.detail, str) and exc.detail == "Old password is incorrect.":
            failure_state = _record_change_password_failure(user.id, client_ip)
            if failure_state.get("locked"):
                exc = HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed password change attempts. Please retry later.",
                    headers={"Retry-After": str(_CHANGE_PASSWORD_LOCKOUT_SECONDS)},
                )

        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="CHANGE_PASSWORD_FAILED",
            reason=_classify_password_change_failure(exc),
            details={
                "status": "failure",
                "status_code": exc.status_code,
            },
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent"),
            category="user",
        )
        raise exc

    _clear_change_password_failures(user.id, client_ip)

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="CHANGE_PASSWORD",
        details={"status": result.get("status"), "reauth_required": bool(result.get("reauth_required"))},
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return result



# -------------------
# Change password init
# -------------------   
@users_router.get("/password/requirements")
def change_password_init_route(db: Session = Depends(get_db)):
    return change_password_init(db)

# Set password (for social login users)
# -------------------
@users_router.post("/password/set")
def set_password_social_route(
    payload: SetPassword,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    enforce_same_origin(request, db)
    require_sensitive_action_auth(user, token, db)
    client_ip = _password_change_client_ip(request, db)
    from app.email.service import security_request_context

    result = set_password_for_social_user(
        user.id,
        payload.new_password,
        db,
        security_context=security_request_context(request, db),
    )

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="SET_PASSWORD_SOCIAL_USER",
        details={"status": result.get("status"), "reauth_required": bool(result.get("reauth_required"))},
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return result



# -------------------
# Delete user
# -------------------
@users_router.delete("/delete", response_model=DeleteAccountResponse)
def delete_user_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    enforce_same_origin(request, db)
    require_sensitive_action_auth(user, token, db)
    audit_retention_policy = get_audit_log_user_deletion_retention_policy(db)
    try:
        result = delete_user(db, db_log, user.id)
    except HTTPException as exc:
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="DELETE_ACCOUNT",
            details={
                "status": "failed",
                "detail": exc.detail,
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="user",
        )
        raise

    if not audit_retention_policy["delete_immediately"]:
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="DELETE_ACCOUNT",
            details={
                "status": result.get("status") if isinstance(result, dict) else None,
                "effect": (
                    result.get("account_deletion", {}).get("effect")
                    if isinstance(result, dict) and isinstance(result.get("account_deletion"), dict)
                    else None
                ),
                "purge_scheduled_at": (
                    result.get("account_deletion", {}).get("purge_scheduled_at")
                    if isinstance(result, dict) and isinstance(result.get("account_deletion"), dict)
                    else None
                ),
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="user",
        )

    return result



# -------------------
# User Settings Init
# -------------------
@users_router.get("/user-settings/init")
def user_settings_init_route(db: Session = Depends(get_db), user = Depends(verified_user)):
    return user_settings_init(user.id, db)



# -------------------
# Shared Items
# -------------------
@users_router.get("/shared-items", response_model=SharedItemsResponse)
def get_shared_items_route(db: Session = Depends(get_db), user = Depends(verified_user)):
    """Get the user's outbound shares, inbound shared memberships, and sharing capabilities."""
    from app.chats.models import Chats
    from app.files.models import FileArtifactShare, Files
    from app.file_folders.models import FileFolders, SharedFileFolderSubscription
    from app.projects.models import Project, ProjectMember
    from app.notes.models import Notes, SharedNoteSubscription
    from app.todos.models import TodoLists, SharedTodoListSubscription
    from app.skills.models import Skills, SharedSkillSubscription
    from app.prompts.models import Prompts, SharedPromptSubscription
    from app.agents.models import UserAgent, SharedUserAgentSubscription
    from app.settings.utils import get_public_url
    from app.users.models import User

    logger = logging.getLogger(__name__)
    items = []
    section_errors = []
    public_url = get_public_url(db)
    owner_name_cache: dict[str, str] = {}

    def owner_display_name(owner_id: str | None) -> str | None:
        """Return a compact owner label for inbound shares without exposing extra profile fields."""
        normalized_owner_id = str(owner_id or "").strip()
        if not normalized_owner_id:
            return None
        if normalized_owner_id in owner_name_cache:
            return owner_name_cache[normalized_owner_id]

        owner = db.query(User).filter(User.id == normalized_owner_id).first()
        if not owner:
            owner_name_cache[normalized_owner_id] = "Unknown"
            return owner_name_cache[normalized_owner_id]

        full_name = " ".join(part for part in [owner.first_name, owner.last_name] if part).strip()
        owner_name_cache[normalized_owner_id] = full_name or "Unknown"
        return owner_name_cache[normalized_owner_id]

    def share_id_for_type(resource, share_type: str | None) -> str | None:
        """Read the appropriate share ID field for resources with clone/live/collaborate links."""
        field_name = {
            "clone": "clone_share_id",
            "live": "live_share_id",
            "collaborate": "collaborate_share_id",
        }.get(str(share_type or "").strip())
        return getattr(resource, field_name, None) if field_name else None

    def mark_inventory_failure(section: str, log_message: str) -> None:
        logger.exception(log_message)
        section_errors.append({
            "section": section,
            "code": "inventory_unavailable",
        })

    # 1. Chats
    try:
        shared_chats = (
            db.query(Chats)
            .filter(Chats.user_id == user.id, Chats.share_id.isnot(None))
            .all()
        )
        for chat in shared_chats:
            share_data = chat.share or {}
            if isinstance(share_data, str):
                try:
                    share_data = json.loads(share_data)
                except (json.JSONDecodeError, TypeError):
                    share_data = {}
            items.append({
                "type": "chat",
                "id": chat.id,
                "resource_id": chat.id,
                "title": chat.title or "Untitled Chat",
                "share_id": chat.share_id,
                "share_url": build_shared_item_url(public_url, "chat", chat.share_id),
                "share_type": "link",
                "has_password": bool(share_data.get("password")),
                "created_at": share_data.get("created_at") or (chat.created_at.isoformat() if chat.created_at else None),
                "expires_at": share_data.get("expires_at"),
                "capabilities": get_shared_item_capabilities("chat"),
            })
    except Exception:
        mark_inventory_failure("chat", "Failed to fetch shared chats")

    # 2. Canvas files
    try:
        artifact_now = datetime.now(timezone.utc)
        try:
            delete_expired_artifact_shares(db, user_id=user.id, now=artifact_now)
        except Exception:
            db.rollback()
            logger.exception("Failed to clean up expired shared canvas links")

        shared_artifacts = (
            db.query(FileArtifactShare, Files)
            .join(Files, FileArtifactShare.file_id == Files.id)
            .filter(FileArtifactShare.user_id == user.id)
            .all()
        )
        for artifact, file in shared_artifacts:
            if is_artifact_share_expired(artifact, now=artifact_now):
                continue
            items.append({
                "type": "artifact",
                "id": artifact.id,
                "resource_id": file.id if file else None,
                "title": file.file_name if file else "Untitled Canvas",
                "share_id": artifact.id,
                "share_url": build_shared_item_url(public_url, "artifact", artifact.id),
                "share_type": "link",
                "has_password": bool(artifact.password_hash),
                "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
                "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
                "last_accessed_at": artifact.last_accessed_at.isoformat() if artifact.last_accessed_at else None,
                "access_count": int(getattr(artifact, "access_count", 0) or 0),
                "capabilities": get_shared_item_capabilities("artifact"),
            })
    except Exception:
        mark_inventory_failure("artifact", "Failed to fetch shared canvas links")

    # 3. Projects
    try:
        shared_projects = (
            db.query(Project)
            .filter(Project.user_id == user.id, Project.link_share_id.isnot(None))
            .all()
        )
        for project in shared_projects:
            items.append({
                "type": "project",
                "id": project.id,
                "resource_id": project.id,
                "title": project.title or "Untitled Project",
                "share_id": project.link_share_id,
                "share_url": build_shared_item_url(public_url, "project", project.link_share_id),
                "share_type": "link",
                "has_password": bool(project.link_share_password_hash),
                "created_at": (project.link_share_created_at.isoformat() if project.link_share_created_at else (project.created_at.isoformat() if project.created_at else None)),
                "expires_at": project.link_share_expires_at.isoformat() if project.link_share_expires_at else None,
                "capabilities": get_shared_item_capabilities("project"),
            })
    except Exception:
        mark_inventory_failure("project", "Failed to fetch shared projects")

    # 4. Notes
    try:
        shared_notes = (
            db.query(Notes)
            .filter(
                Notes.user_id == user.id,
                or_(
                    Notes.clone_share_id.isnot(None),
                    Notes.live_share_id.isnot(None),
                    Notes.collaborate_share_id.isnot(None),
                ),
            )
            .all()
        )
        for note in shared_notes:
            title = (note.content or "")[:50].split("\n")[0] or "Untitled Note"
            for share_type, share_id in [
                ("clone", note.clone_share_id),
                ("live", note.live_share_id),
                ("collaborate", note.collaborate_share_id),
            ]:
                if share_id:
                    items.append({
                        "type": "note",
                        "id": note.id,
                        "resource_id": note.id,
                        "title": title,
                        "share_id": share_id,
                        "share_url": build_shared_item_url(public_url, "note", share_id, share_type),
                        "share_type": share_type,
                        "has_password": False,
                        "created_at": note.created_at.isoformat() if note.created_at else None,
                        "expires_at": None,
                        "capabilities": get_shared_item_capabilities("note"),
                    })
    except Exception:
        mark_inventory_failure("note", "Failed to fetch shared notes")

    # 5. Todos
    try:
        shared_todos = (
            db.query(TodoLists)
            .filter(
                TodoLists.user_id == user.id,
                or_(
                    TodoLists.clone_share_id.isnot(None),
                    TodoLists.live_share_id.isnot(None),
                    TodoLists.collaborate_share_id.isnot(None),
                ),
            )
            .all()
        )
        for todo in shared_todos:
            for share_type, share_id in [
                ("clone", todo.clone_share_id),
                ("live", todo.live_share_id),
                ("collaborate", todo.collaborate_share_id),
            ]:
                if share_id:
                    items.append({
                        "type": "todo",
                        "id": todo.id,
                        "resource_id": todo.id,
                        "title": todo.title or "Untitled Todo",
                        "share_id": share_id,
                        "share_url": build_shared_item_url(public_url, "todo", share_id, share_type),
                        "share_type": share_type,
                        "has_password": False,
                        "created_at": todo.created_at.isoformat() if todo.created_at else None,
                        "expires_at": None,
                        "capabilities": get_shared_item_capabilities("todo"),
                    })
    except Exception:
        mark_inventory_failure("todo", "Failed to fetch shared todos")

    # 6. Skills
    try:
        shared_skills = (
            db.query(Skills)
            .filter(
                Skills.user_id == user.id,
                or_(
                    Skills.clone_share_id.isnot(None),
                    Skills.live_share_id.isnot(None),
                    Skills.collaborate_share_id.isnot(None),
                ),
            )
            .all()
        )
        for skill in shared_skills:
            for share_type, share_id in [
                ("clone", skill.clone_share_id),
                ("live", skill.live_share_id),
                ("collaborate", skill.collaborate_share_id),
            ]:
                if share_id:
                    items.append({
                        "type": "skill",
                        "id": skill.id,
                        "resource_id": skill.id,
                        "title": skill.name or "Untitled Skill",
                        "share_id": share_id,
                        "share_url": build_shared_item_url(public_url, "skill", share_id, share_type),
                        "share_type": share_type,
                        "has_password": False,
                        "created_at": skill.created_at.isoformat() if skill.created_at else None,
                        "expires_at": None,
                        "capabilities": get_shared_item_capabilities("skill"),
                    })
    except Exception:
        mark_inventory_failure("skill", "Failed to fetch shared skills")

    # 7. Prompts
    try:
        shared_prompts = (
            db.query(Prompts)
            .filter(
                Prompts.user_id == user.id,
                or_(
                    Prompts.clone_share_id.isnot(None),
                    Prompts.live_share_id.isnot(None),
                    Prompts.collaborate_share_id.isnot(None),
                ),
            )
            .all()
        )
        for prompt in shared_prompts:
            for share_type, share_id in [
                ("clone", prompt.clone_share_id),
                ("live", prompt.live_share_id),
                ("collaborate", prompt.collaborate_share_id),
            ]:
                if share_id:
                    items.append({
                        "type": "prompt",
                        "id": prompt.id,
                        "resource_id": prompt.id,
                        "title": prompt.title or "Untitled Prompt",
                        "share_id": share_id,
                        "share_url": build_shared_item_url(public_url, "prompt", share_id, share_type),
                        "share_type": share_type,
                        "has_password": False,
                        "created_at": prompt.created_at.isoformat() if prompt.created_at else None,
                        "expires_at": None,
                        "capabilities": get_shared_item_capabilities("prompt"),
                    })
    except Exception:
        mark_inventory_failure("prompt", "Failed to fetch shared prompts")

    # 8. Agents
    try:
        shared_agents = (
            db.query(UserAgent)
            .filter(
                UserAgent.user_id == user.id,
                or_(
                    UserAgent.clone_share_id.isnot(None),
                    UserAgent.live_share_id.isnot(None),
                    UserAgent.collaborate_share_id.isnot(None),
                ),
            )
            .all()
        )
        for agent in shared_agents:
            for share_type, share_id in [
                ("clone", agent.clone_share_id),
                ("live", agent.live_share_id),
                ("collaborate", agent.collaborate_share_id),
            ]:
                if share_id:
                    items.append({
                        "type": "agent",
                        "id": agent.id,
                        "resource_id": agent.id,
                        "title": agent.name or "Untitled Agent",
                        "share_id": share_id,
                        "share_url": build_shared_item_url(public_url, "agent", share_id, share_type),
                        "share_type": share_type,
                        "has_password": False,
                        "created_at": agent.created_at.isoformat() if agent.created_at else None,
                        "expires_at": None,
                        "capabilities": get_shared_item_capabilities("agent"),
                    })
    except Exception:
        mark_inventory_failure("agent", "Failed to fetch shared agents")

    # 9. Folders
    try:
        shared_folders = (
            db.query(FileFolders)
            .filter(
                FileFolders.user_id == user.id,
                or_(
                    FileFolders.clone_share_id.isnot(None),
                    FileFolders.live_share_id.isnot(None),
                    FileFolders.collaborate_share_id.isnot(None),
                ),
            )
            .all()
        )
        for folder in shared_folders:
            for share_type, share_id in [
                ("clone", folder.clone_share_id),
                ("live", folder.live_share_id),
                ("collaborate", folder.collaborate_share_id),
            ]:
                if share_id:
                    items.append({
                        "type": "folder",
                        "id": folder.id,
                        "resource_id": folder.id,
                        "title": folder.name or "Untitled Folder",
                        "share_id": share_id,
                        "share_url": build_shared_item_url(public_url, "folder", share_id, share_type),
                        "share_type": share_type,
                        "direction": "outbound",
                        "has_password": False,
                        "created_at": folder.created_at.isoformat() if folder.created_at else None,
                        "expires_at": None,
                        "capabilities": get_shared_item_capabilities("folder"),
                    })
    except Exception:
        mark_inventory_failure("folder", "Failed to fetch shared folders")

    # 10. Accepted project shares.
    try:
        project_memberships = (
            db.query(ProjectMember, Project)
            .join(Project, ProjectMember.project_id == Project.id)
            .filter(ProjectMember.user_id == user.id, Project.user_id != user.id)
            .all()
        )
        for membership, project in project_memberships:
            items.append({
                "type": "project",
                "id": project.id,
                "resource_id": project.id,
                "title": project.title or "Untitled Project",
                "share_id": project.link_share_id,
                "share_url": build_shared_item_url(public_url, "project", project.link_share_id),
                "share_type": "member",
                "direction": "inbound",
                "owner_name": owner_display_name(project.user_id),
                "has_password": bool(project.link_share_password_hash),
                "created_at": membership.joined_at.isoformat() if membership.joined_at else None,
                "expires_at": project.link_share_expires_at.isoformat() if project.link_share_expires_at else None,
                "capabilities": get_shared_item_capabilities("project"),
            })
    except Exception:
        mark_inventory_failure("project_membership", "Failed to fetch projects shared with the user")

    inbound_definitions = [
        {
            "section": "note",
            "model": Notes,
            "subscription": SharedNoteSubscription,
            "resource_field": SharedNoteSubscription.note_id,
            "title": lambda note: (note.content or "")[:50].split("\n")[0] or "Untitled Note",
        },
        {
            "section": "todo",
            "model": TodoLists,
            "subscription": SharedTodoListSubscription,
            "resource_field": SharedTodoListSubscription.todo_list_id,
            "title": lambda todo: todo.title or "Untitled Todo",
        },
        {
            "section": "skill",
            "model": Skills,
            "subscription": SharedSkillSubscription,
            "resource_field": SharedSkillSubscription.skill_id,
            "title": lambda skill: skill.name or "Untitled Skill",
        },
        {
            "section": "prompt",
            "model": Prompts,
            "subscription": SharedPromptSubscription,
            "resource_field": SharedPromptSubscription.prompt_id,
            "title": lambda prompt: prompt.title or "Untitled Prompt",
        },
        {
            "section": "agent",
            "model": UserAgent,
            "subscription": SharedUserAgentSubscription,
            "resource_field": SharedUserAgentSubscription.agent_id,
            "title": lambda agent: agent.name or "Untitled Agent",
        },
        {
            "section": "folder",
            "model": FileFolders,
            "subscription": SharedFileFolderSubscription,
            "resource_field": SharedFileFolderSubscription.folder_id,
            "title": lambda folder: folder.name or "Untitled Folder",
        },
    ]

    for definition in inbound_definitions:
        section = definition["section"]
        subscription_model = definition["subscription"]
        resource_model = definition["model"]
        try:
            inbound_rows = (
                db.query(subscription_model, resource_model)
                .join(resource_model, definition["resource_field"] == resource_model.id)
                .filter(subscription_model.subscriber_id == user.id, resource_model.user_id != user.id)
                .all()
            )
            for subscription, resource in inbound_rows:
                share_type = str(subscription.share_type or "").strip()
                share_id = share_id_for_type(resource, share_type)
                if share_type in {"live", "collaborate"} and not share_id:
                    continue
                items.append({
                    "type": section,
                    "id": getattr(resource, "id", None),
                    "resource_id": getattr(resource, "id", None),
                    "title": definition["title"](resource),
                    "share_id": share_id,
                    "share_url": build_shared_item_url(public_url, section, share_id, share_type),
                    "share_type": share_type,
                    "direction": "inbound",
                    "owner_name": owner_display_name(getattr(resource, "user_id", None)),
                    "has_password": False,
                    "created_at": subscription.subscribed_at.isoformat() if subscription.subscribed_at else None,
                    "expires_at": None,
                    "capabilities": get_shared_item_capabilities(section),
                })
        except Exception:
            mark_inventory_failure(f"{section}_membership", f"Failed to fetch {section}s shared with the user")

    for item in items:
        item.setdefault("direction", "outbound")

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {
        "status": "degraded" if section_errors else "ok",
        "items": items,
        "section_errors": section_errors,
    }



# -------------------
# Last model
# -------------------
@users_router.post("/settings/last-model/set")
def set_last_model_route(
    payload: UpdateUserLastModel,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    result = set_user_last_model(user.id, db, payload.model_id)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="USER_LAST_MODEL_SET",
        details={"model_id": payload.model_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )
    return result



# -------------------
# Pinned models
# -------------------
@users_router.post("/settings/pinned-models/set")
def set_pinned_models_route(
    payload: UpdateUserPinnedModels,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    result = update_user_pinned_models(user.id, db, payload.pinned_models)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="USER_PINNED_MODELS_SET",
        details={"model_count": len(payload.pinned_models or [])},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )
    return result


# Sidebar button visibility
# -------------------
@users_router.patch("/settings/sidebar-button-visibility")
def update_sidebar_button_visibility_route(
    payload: SidebarButtonVisibilityUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    button_visibility = payload.model_dump(exclude_unset=True)
    result = update_sidebar_button_visibility(user.id, db, button_visibility)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="USER_SIDEBAR_BUTTON_VISIBILITY_UPDATED",
        details={"updated_keys": sorted(button_visibility.keys())},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )
    return result


@users_router.post("/privacy-policy/notice", response_model=OperationResult)
def update_privacy_policy_notice_state_route(
    payload: UpdatePrivacyPolicyNoticeState,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    policy = get_privacy_policy_notice_policy(db, user.id)
    current_revision = int(policy.get("revision") or 1)

    if payload.revision != current_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "type": "privacy_policy_revision_mismatch",
                "expected_revision": current_revision,
            },
        )

    last_interacted_revision = current_revision
    privacy_policy_accepted = False

    update_user_settings_bulk(
        user.id,
        {
            "states": {
                "privacy_policy_last_interacted_revision": last_interacted_revision,
                "privacy_policy_accepted": privacy_policy_accepted,
            }
        },
        db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="DISMISS_PRIVACY_POLICY_NOTICE",
        details={"revision": current_revision, "source": "notice"},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return OperationResult(status="success")


@users_router.post("/terms-of-service/accept", response_model=OperationResult)
def accept_terms_of_service_route(
    payload: AcceptTermsOfServiceState,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    policy = get_terms_of_service_policy(db, user.id)
    current_revision = int(policy.get("revision") or 1)

    if payload.revision != current_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "type": "terms_of_service_revision_mismatch",
                "expected_revision": current_revision,
            },
        )

    accepted_at = datetime.now(timezone.utc).isoformat()
    update_user_settings_bulk(
        user.id,
        {
            "states": {
                "terms_of_service_accepted_revision": current_revision,
                "terms_of_service_accepted_at": accepted_at,
            }
        },
        db,
    )

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="ACCEPT_TERMS_OF_SERVICE",
        details={
            "revision": current_revision,
            "accepted_at": accepted_at,
            "source": "notice",
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return OperationResult(status="success")


# -------------------
# User Location
# -------------------
@users_router.post("/location")
def update_user_location_route(
    payload: UpdateUserLocationRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    result = update_user_location(user.id, payload.location, db)
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="USER_LOCATION_UPDATED",
        details={"location_provided": bool(str(payload.location or "").strip())},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )
    return result



# -------------------
# User Data Export
# -------------------
@users_router.get("/export")
def export_user_data_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    ensure_data_control_permission(
        user.id,
        "allow_user_data",
        db,
        detail="User data export is disabled for your group's data controls.",
    )
    audit_details = build_user_data_export_audit_details(user.id, db_log, db)
    job = enqueue_user_data_export(db, user_id=user.id)
    operation_result = wait_for_operations_result(job)
    result_path = resolve_operations_result_path(operation_result.get("result_name"))
    try:
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="EXPORT_USER_DATA",
            details=audit_details,
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="user",
        )
    except Exception:
        result_path.unlink(missing_ok=True)
        raise
    export_file = result_path.open("rb")

    def _close_and_remove_export() -> None:
        export_file.close()
        result_path.unlink(missing_ok=True)

    export_file.seek(0)
    return StreamingResponse(
        iter(lambda: export_file.read(1024 * 1024), b""),
        media_type="application/json",
        background=BackgroundTask(_close_and_remove_export),
    )



# -------------------
# User Data Import (Self)
# -------------------
@users_router.post("/import/self")
def import_user_data_self_route(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    ensure_data_control_permission(
        user.id,
        "allow_user_data",
        db,
        detail="User data import is disabled for your group's data controls.",
    )
    staged_name = stage_import_json(
        payload,
        principal_id=user.id,
        import_kind="import_user_self",
    )
    job = enqueue_import_job(
        db,
        kind="import_user_self",
        staged_name=staged_name,
        user_id=user.id,
    )
    summary = wait_for_operations_result(job)
    details = {
        "imported_sections": summary.get("imported") or [],
        "skipped_sections": [
            item.get("section") for item in summary.get("skipped_sections") or []
        ],
        "warning_count": len(summary.get("warnings") or []),
        "error_count": len(summary.get("errors") or []),
    }

    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="IMPORT_USER_DATA_SELF",
        details=details,
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="user",
    )

    return JSONResponse(content=summary)



# -------------------
# Get Public Users for Sharing
# -------------------
@users_router.get("/public-users", response_model=list[PublicUserSharingSummary])
def get_public_users_route(
    response: Response,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=DEFAULT_PUBLIC_USER_DISCOVERY_LIMIT, ge=1, le=MAX_PUBLIC_USER_DISCOVERY_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user = Depends(verified_user),
):
    """Get list of users with public profile visibility for sharing/invitations."""
    public_users, meta = get_public_users_for_sharing(db, user, q=q, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(meta["total"])
    response.headers["X-Limit"] = str(meta["limit"])
    response.headers["X-Offset"] = str(meta["offset"])
    response.headers["X-Has-More"] = "true" if meta["has_more"] else "false"
    return public_users
