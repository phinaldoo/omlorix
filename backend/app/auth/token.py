from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError as JWTError
from sqlalchemy import or_
import logging
import uuid

from app.auth.models import (
    Authentication,
    delete_authentication,
    get_authentication,
    resolve_refresh_token_for_rotation,
    rotate_authentication_tokens,
)
from app.admin.concurrency.models import (
    floor_to_five_minute_bucket,
    record_user_activity_presence,
)
from app.auth.account_slots import (
    LEGACY_REFRESH_COOKIE,
    MAX_ACCOUNT_SLOTS,
    clear_access_token_cookie,
    clear_active_slot_cookie,
    clear_refresh_slot_cookie,
    get_active_slot,
    get_active_refresh_token,
    set_access_token_cookie,
    set_active_slot_cookie,
    set_refresh_slot_cookie,
    clear_legacy_refresh_cookie,
)
from app.auth.jwt_material import get_jwt_material as _get_jwt_material
from app.auth.session_store import (
    cache_access_token,
    cache_refresh_token,
    token_exists as session_token_exists,
)
from app.auth.twofa_provider import build_login_2fa_session_claims, get_login_2fa_session_policy
from app.auth.utils import check_blocked_ip_address
from app.auth.models import record_ip_address_security_event
from app.groups.access_windows import is_group_accessible_now
from app.logging.models import create_authentication_log
from app.middleware.ip_restriction import get_client_ip
from app.settings.utils import get_value_by_page_and_key
from app.users.models import User, evaluate_user_lock, get_user, normalize_utc_datetime, update_last_active_user
from app.users.roles import is_admin_role
from app.users.external_management import is_externally_managed
from app.users.utils import get_user_setting_value
from app.utils.utils import get_terms_of_service_policy



_logger = logging.getLogger(__name__)
ACTIVITY_WRITE_INTERVAL = timedelta(minutes=5)



# -------------------
# Build JWT payload
# -------------------
def _build_jwt_payload(data: dict, expires_delta: timedelta | None, db) -> tuple[dict, str, str]:
    """Build JWT payload with expiration."""
    payload = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=get_value_by_page_and_key("security", "access_token_expire_minutes", db)))
    payload.update({"exp": expire, "iat": int(now.timestamp())})
    payload.setdefault("jti", str(uuid.uuid4()))
    secret, algorithm = _get_jwt_material()
    return payload, secret, algorithm


def require_recent_auth_token(token: str, db, *, max_age_seconds: int = 10 * 60) -> dict:
    """Require a freshly issued access token before sensitive account changes."""
    try:
        secret, algorithm = _get_jwt_material()
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or malformed access token")

    user_id = payload.get("sub")
    if user_id is None or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token")

    auth_entry = get_authentication(db, user_id, token, "access_token")
    if not auth_entry:
        raise HTTPException(status_code=401, detail="Access token is no longer valid (revoked)")

    authenticated_at = normalize_utc_datetime(getattr(auth_entry, "created_at", None))
    if authenticated_at is None:
        raise HTTPException(status_code=403, detail="Recent authentication required")

    age_seconds = (datetime.now(timezone.utc) - authenticated_at).total_seconds()
    if age_seconds < 0 or age_seconds > max(1, int(max_age_seconds)):
        raise HTTPException(status_code=403, detail="Recent authentication required")

    return payload


def require_step_up_auth_token(token: str, db, *, max_age_seconds: int = 10 * 60) -> dict:
    """Require an explicit recent step-up verification for sensitive account changes."""
    try:
        secret, algorithm = _get_jwt_material()
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or malformed access token")

    user_id = payload.get("sub")
    if user_id is None or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token")

    auth_entry = get_authentication(db, user_id, token, "access_token")
    if not auth_entry:
        raise HTTPException(status_code=401, detail="Access token is no longer valid (revoked)")

    step_up_at = normalize_utc_datetime(getattr(auth_entry, "step_up_authenticated_at", None))
    if step_up_at is None:
        raise HTTPException(status_code=403, detail="Step-up authentication required")

    age_seconds = (datetime.now(timezone.utc) - step_up_at).total_seconds()
    if age_seconds < 0 or age_seconds > max(1, int(max_age_seconds)):
        raise HTTPException(status_code=403, detail="Step-up authentication required")

    return payload


def _request_audit_context(request, db) -> tuple[str, str | None]:
    """Return minimized request context for authentication audit events."""

    headers = getattr(request, "headers", {}) or {}
    user_agent = headers.get("User-Agent", "Unknown Device")
    try:
        client_ip = get_client_ip(request, db)
    except (AttributeError, TypeError):
        client_ip = None
    return user_agent, client_ip


def _refresh_token_expiry(token: str, db) -> datetime | None:
    """Read a signed token's expiry without enforcing that it is still current."""

    try:
        secret, algorithm = _get_jwt_material()
        payload = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={"verify_exp": False},
        )
        exp = payload.get("exp")
        return datetime.fromtimestamp(float(exp), timezone.utc) if exp is not None else None
    except (JWTError, TypeError, ValueError, OverflowError):
        return None


def _reject_reused_refresh_token(db, db_log, user_id: str, session_id: str, request) -> None:
    """Revoke only the token family in which a consumed token was replayed."""

    user_agent, client_ip = _request_audit_context(request, db)
    create_authentication_log(
        db_log,
        "refresh_reuse_detected",
        "warning",
        "Refresh token reuse was detected",
        user_id,
        user_agent,
        client_ip,
    )
    delete_authentication(db, id=session_id, user_id=user_id)


def _refresh_race_response() -> JSONResponse:
    """Tell the browser to retry after another tab completed rotation."""

    return JSONResponse(
        status_code=409,
        content={
            "detail": {
                "type": "refresh_race",
                "retry_after_ms": 250,
            }
        },
        headers={"Retry-After": "1"},
    )


def _clear_all_refresh_cookies(response, request, db) -> None:
    for slot in range(1, MAX_ACCOUNT_SLOTS + 1):
        clear_refresh_slot_cookie(response, slot, db, request)
    clear_legacy_refresh_cookie(response, db, request)
    clear_active_slot_cookie(response, db, request)
    clear_access_token_cookie(response, db, request)


def _clear_selected_refresh_cookies(response, request, db, refresh_token: str, active_slot: int | None) -> None:
    if active_slot:
        clear_refresh_slot_cookie(response, active_slot, db, request)
        if get_active_slot(request) == active_slot:
            clear_active_slot_cookie(response, db, request)
            clear_access_token_cookie(response, db, request)

    if request.cookies.get(LEGACY_REFRESH_COOKIE) == refresh_token:
        clear_legacy_refresh_cookie(response, db, request)


def _refresh_error_response(
    request,
    db,
    *,
    detail: str,
    refresh_token: str | None = None,
    active_slot: int | None = None,
    delete_refresh_row: bool = False,
) -> JSONResponse:
    response = JSONResponse(status_code=401, content={"detail": detail})
    if refresh_token:
        if delete_refresh_row:
            delete_authentication(db, refresh_token=refresh_token)
        _clear_selected_refresh_cookies(response, request, db, refresh_token, active_slot)
    else:
        _clear_all_refresh_cookies(response, request, db)
    return response



# -------------------
# Create access token
# -------------------
def create_access_token(data: dict, db, expires_delta: timedelta | None = None):
    """Create JWT access token."""
    payload, secret, algorithm = _build_jwt_payload(data, expires_delta, db)
    return jwt.encode(payload, secret, algorithm=algorithm)



# -------------------
# Create refresh token
# -------------------
def create_refresh_token(data: dict, db, expires_delta: timedelta | None = None):
    """Create JWT refresh token."""
    minutes = get_value_by_page_and_key("security", "refresh_token_expire_minutes", db)
    refresh_delta = expires_delta or timedelta(minutes=minutes)
    payload, secret, algorithm = _build_jwt_payload(data, refresh_delta, db)
    return jwt.encode(payload, secret, algorithm=algorithm)


def _build_access_time_blocked_detail(access_check: dict | None) -> dict:
    """Normalize access-window denials to the app's structured 403 payload."""
    access_check = access_check or {}
    return {
        "type": "access_time_blocked",
        "message": access_check.get("blocked_message") or "Access is not allowed at this time",
        "reason": access_check.get("reason"),
        "next_allowed_at": access_check.get("next_allowed_at"),
        "blocked_message": access_check.get("blocked_message"),
    }


def ensure_user_runtime_auth_allowed(user, db, *, ip_address: str | None = None, event_source: str = "access_token"):
    """Apply account-state checks shared by token and ticket authentication."""
    if getattr(user, "deleted_at", None) is not None:
        raise HTTPException(status_code=410, detail="User account has been deleted")
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=423, detail="User is not active")
    temporary_expires_at = normalize_utc_datetime(getattr(user, "temporary_expires_at", None))
    if getattr(user, "account_type", "regular") == "temporary":
        if temporary_expires_at is None or temporary_expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=423, detail="Temporary account has expired")
    lock = evaluate_user_lock(user, db)
    if isinstance(lock, dict) and lock.get("is_locked"):
        raise HTTPException(status_code=423, detail="User is locked")
    user_role = getattr(user, "role", "user")
    group_id = getattr(user, "group_id", None)
    if group_id:
        access_check = is_group_accessible_now(group_id, db, is_admin=is_admin_role(user_role))
        if not access_check.get("accessible", True):
            raise HTTPException(
                status_code=403,
                detail=_build_access_time_blocked_detail(access_check),
            )
    if ip_address and check_blocked_ip_address(ip_address, db):
        try:
            record_ip_address_security_event(
                db,
                ip_address,
                "request_denied",
                event_source=event_source,
                reason_code="active_ban",
                route_category="auth",
                reason="Blocked IP attempted token-authenticated access",
                aggregate=True,
            )
        except Exception:
            _logger.exception("Failed to record blocked IP security event for %s", ip_address)
            db.rollback()
        raise HTTPException(status_code=403, detail="User is IP banned")
    if user_role == "pending":
        raise HTTPException(status_code=409, detail="User is pending")


def _is_activity_update_due(last_active_at, now: datetime) -> bool:
    """Return True when an activity timestamp is old enough to persist again."""
    normalized_last_active_at = normalize_utc_datetime(last_active_at)
    if normalized_last_active_at is None:
        return True
    return now - normalized_last_active_at >= ACTIVITY_WRITE_INTERVAL


def _is_user_activity_bucket_update_due(last_active_at, now: datetime) -> bool:
    """Return True when user activity has entered a new fixed metrics bucket.

    Authentication-session activity retains its rolling five-minute write
    throttle, while the user timestamp follows fixed bucket boundaries. This
    guarantees that a request just after a boundary is represented in the new
    concurrency bucket without allowing more than one user-row update per bucket.
    """

    normalized_last_active_at = normalize_utc_datetime(last_active_at)
    if normalized_last_active_at is None:
        return True
    return floor_to_five_minute_bucket(
        normalized_last_active_at
    ) < floor_to_five_minute_bucket(now)


def _record_authenticated_activity(db, user, auth_entry, *, now: datetime | None = None) -> None:
    """Persist authenticated activity at a bounded cadence.

    Authentication runs for nearly every API request, so writing activity on every
    successful token check turns normal frontend fan-out into a burst of database
    updates. The preliminary Python check avoids unnecessary SQL for the common
    recent-activity path, and the conditional database update prevents parallel
    requests that read the same stale row from all committing the same timestamp.
    """
    current_time = now or datetime.now(timezone.utc)
    cutoff_time = current_time - ACTIVITY_WRITE_INTERVAL
    current_bucket = floor_to_five_minute_bucket(current_time)
    user_update_due = _is_user_activity_bucket_update_due(
        getattr(user, "last_active_at", None),
        current_time,
    )
    auth_update_due = _is_activity_update_due(getattr(auth_entry, "last_active_at", None), current_time)

    if not user_update_due and not auth_update_due:
        return

    updated_user_rows = 0
    updated_auth_rows = 0

    if user_update_due:
        updated_user_rows = (
            db.query(User)
            .filter(
                User.id == user.id,
                # The database-side condition closes the parallel-request race:
                # only one request may move the user into this fixed bucket.
                or_(User.last_active_at.is_(None), User.last_active_at < current_bucket),
            )
            .update({User.last_active_at: current_time}, synchronize_session=False)
        )

    if auth_update_due:
        updated_auth_rows = (
            db.query(Authentication)
            .filter(
                Authentication.id == auth_entry.id,
                or_(Authentication.last_active_at.is_(None), Authentication.last_active_at <= cutoff_time),
            )
            .update({Authentication.last_active_at: current_time}, synchronize_session=False)
        )

    # Presence rows are also bucketed, so only the request that actually moves
    # the user's activity row should try to record a new concurrency bucket.
    if updated_user_rows:
        record_user_activity_presence(db, user, now=current_time)

    if not updated_user_rows and not updated_auth_rows:
        return

    db.commit()

    # Keep the objects returned to downstream code consistent with the committed
    # database state without forcing an additional refresh query.
    if updated_user_rows:
        user.last_active_at = current_time
    if updated_auth_rows:
        auth_entry.last_active_at = current_time


def _build_2fa_policy_required_detail(policy: dict) -> dict:
    mode = str(policy.get("mode") or "verify")
    return {
        "type": "twofa_policy_required",
        "status": "otp_setup" if mode == "setup" else "otp_required_already_setup",
        "provider": policy.get("provider"),
        "mode": mode,
    }


def ensure_session_satisfies_current_2fa_policy(user, payload: dict, db) -> None:
    """Reject sessions that no longer satisfy the current login 2FA policy."""
    policy = get_login_2fa_session_policy(user, db)
    if not policy.get("required"):
        return

    if (
        payload.get("twofa_satisfied") is True
        and payload.get("twofa_provider") == policy.get("provider")
        and payload.get("twofa_policy_version") == policy.get("version")
    ):
        return

    raise HTTPException(status_code=403, detail=_build_2fa_policy_required_detail(policy))

# -------------------
# Verify user by token
# -------------------
def _get_verified_user(
    token: str,
    db,
    *,
    ip_address: str | None = None,
    token_kind: str,
    require_admin: bool = False,
    enforce_2fa_policy: bool = True,
):
    """Verify user token and return user object."""
    try:
        secret, algorithm = _get_jwt_material()
        payload = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or malformed access token")

    user_id = payload.get("sub")
    token_type = payload.get("type")
    exp = payload.get("exp")

    # Basic token sanity checks ------------------------------------------------
    if user_id is None or token_type != token_kind:
        raise HTTPException(status_code=401, detail=f"Invalid {token_kind} token")

    # Expiry check (epoch seconds)
    if exp is None or datetime.now(timezone.utc).timestamp() > exp:
        raise HTTPException(status_code=401, detail=f"{token_kind} token has expired")

    # Has the token been revoked? --------------------------------------------
    token_column = f"{token_kind}_token"
    token_is_cached = session_token_exists(user_id, token, token_kind)
    if not token_is_cached:
        auth_entry = get_authentication(db, user_id, token, token_column)
        if not auth_entry:
            raise HTTPException(status_code=401, detail=f"{token_kind} token is no longer valid (revoked)")
        if token_kind == "access":
            cache_access_token(user_id, token)
            if auth_entry.refresh_token:
                cache_refresh_token(user_id, auth_entry.refresh_token)
        else:
            cache_refresh_token(user_id, token)

    # At this point the token is valid – fetch the user -----------------------
    try:
        user = get_user(db, user_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=401, detail="Invalid or malformed access token")
        raise

    # Additional runtime checks ----------------------------------------------
    ensure_user_runtime_auth_allowed(user, db, ip_address=ip_address, event_source=f"{token_kind}_token")
    if enforce_2fa_policy:
        ensure_session_satisfies_current_2fa_policy(user, payload, db)

    if require_admin and not is_admin_role(user.role):
        raise HTTPException(status_code=401, detail="You do not have permission to perform this action")

    auth_column = f"{token_kind}_token"
    auth_entry = get_authentication(db, user.id, token, auth_column)
    if not auth_entry:
        raise HTTPException(status_code=401, detail=f"{token_kind} token is no longer valid (revoked)")

    _record_authenticated_activity(db, user, auth_entry)

    return user




# -------------------
# Check admin by token
# -------------------
def check_admin_by_token(token, ip_address: str | None, token_type: str, db):
    """Verify admin token and return admin user."""
    return _get_verified_user(token, db, ip_address=ip_address, token_kind=token_type, require_admin=True)



# -------------------
# Check user by token
# -------------------
def check_user_by_token(token, ip_address: str | None, token_type: str, db, *, enforce_2fa_policy: bool = True):
    """Verify user token and return user."""
    return _get_verified_user(
        token,
        db,
        ip_address=ip_address,
        token_kind=token_type,
        enforce_2fa_policy=enforce_2fa_policy,
    )


def ensure_access_token_satisfies_current_2fa_policy(token: str, user, db) -> None:
    """Decode an access token and enforce current 2FA policy for its session."""
    try:
        secret, algorithm = _get_jwt_material()
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or malformed access token")

    if payload.get("sub") != user.id or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token")

    ensure_session_satisfies_current_2fa_policy(user, payload, db)


def rotate_current_session_tokens_with_2fa_claims(request, response, db, user, access_token: str) -> dict:
    """Rotate the current session after a login 2FA policy change."""
    auth_entry = get_authentication(db, user.id, access_token, "access_token")
    if not auth_entry:
        raise HTTPException(status_code=401, detail="Access token is no longer valid (revoked)")

    session_claims = build_login_2fa_session_claims(user, db)
    token_claims = {"sub": user.id, "sid": auth_entry.id, **session_claims}
    new_access_token = create_access_token({**token_claims, "type": "access"}, db=db)
    new_refresh_token = create_refresh_token({**token_claims, "type": "refresh"}, db=db)
    rotate_authentication_tokens(
        db,
        user.id,
        auth_entry.refresh_token,
        new_access_token,
        new_refresh_token,
        session_id=auth_entry.id,
        previous_refresh_expires_at=_refresh_token_expiry(auth_entry.refresh_token, db),
        rotation_reason="2fa_policy",
    )

    active_slot = get_active_slot(request)
    if active_slot:
        set_refresh_slot_cookie(response, active_slot, new_refresh_token, db, request)
        set_active_slot_cookie(response, active_slot, db, request)
    elif request.cookies.get("refresh_token"):
        active_slot = 1
        set_refresh_slot_cookie(response, active_slot, new_refresh_token, db, request)
        set_active_slot_cookie(response, active_slot, db, request)

    if request.cookies.get("refresh_token"):
        clear_legacy_refresh_cookie(response, db, request)

    set_access_token_cookie(response, new_access_token, db, request)

    return {
        "session_authenticated": True,
        "active_account_slot": active_slot,
    }



# -------------------
# Refresh access token
# -------------------
def get_access_token_by_refresh_token(request, response, db, db_log):
    """Get new access token from refresh token."""
    refresh_token, active_slot = get_active_refresh_token(request, response, db)
    if not refresh_token:
        return _refresh_error_response(request, db, detail="Refresh token missing")
    try:
        secret, algorithm = _get_jwt_material()
        payload = jwt.decode(
            refresh_token,
            secret,
            algorithms=[algorithm],
        )
    except ExpiredSignatureError:
        return _refresh_error_response(
            request,
            db,
            detail="Refresh token has expired",
            refresh_token=refresh_token,
            active_slot=active_slot,
            delete_refresh_row=True,
        )
    except JWTError:
        return _refresh_error_response(
            request,
            db,
            detail="Invalid or malformed refresh token",
            refresh_token=refresh_token,
            active_slot=active_slot,
            delete_refresh_row=True,
        )

    user_id = payload.get("sub")
    token_type = payload.get("type")
    exp = payload.get("exp")
    session_id = payload.get("sid")

    # Basic sanity checks -----------------------------------------------------
    if user_id is None or token_type != "refresh":
        return _refresh_error_response(
            request,
            db,
            detail="Invalid refresh token",
            refresh_token=refresh_token,
            active_slot=active_slot,
            delete_refresh_row=True,
        )

    if exp is None or datetime.now(timezone.utc).timestamp() > exp:
        return _refresh_error_response(
            request,
            db,
            detail="Refresh token has expired",
            refresh_token=refresh_token,
            active_slot=active_slot,
            delete_refresh_row=True,
        )

    # Resolve the token under a row lock. Unknown credentials are not proof of
    # theft, while a hash in this session's consumed history is a confirmed
    # duplicate. Very recent duplicates are expected cross-tab races.
    resolution = resolve_refresh_token_for_rotation(
        db,
        user_id=user_id,
        refresh_token=refresh_token,
        session_id=session_id if isinstance(session_id, str) and session_id else None,
    )
    auth_entry = resolution.authentication

    if resolution.state == "race":
        user_agent, client_ip = _request_audit_context(request, db)
        create_authentication_log(
            db_log,
            "refresh_race_detected",
            "info",
            "Concurrent refresh-token rotation was retried",
            user_id,
            user_agent,
            client_ip,
        )
        return _refresh_race_response()

    if resolution.state == "reused" and auth_entry is not None:
        _reject_reused_refresh_token(db, db_log, user_id, auth_entry.id, request)
        return _refresh_error_response(
            request,
            db,
            detail="Refresh token is no longer valid (revoked)",
            refresh_token=refresh_token,
            active_slot=active_slot,
        )

    if resolution.state != "current" or auth_entry is None:
        user_agent, client_ip = _request_audit_context(request, db)
        create_authentication_log(
            db_log,
            "refresh_unknown_token",
            "warning",
            "Unknown or revoked refresh token was rejected",
            user_id,
            user_agent,
            client_ip,
        )
        return _refresh_error_response(
            request,
            db,
            detail="Refresh token is no longer valid (revoked)",
            refresh_token=refresh_token,
            active_slot=active_slot,
        )

    # Fetch user to perform additional runtime checks ------------------------
    user = get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User does not exist")
    ensure_user_runtime_auth_allowed(
        user,
        db,
        ip_address=get_client_ip(request, db),
        event_source="refresh_token",
    )
    ensure_session_satisfies_current_2fa_policy(user, payload, db)


    # Everything looks good – rotate the refresh token and issue fresh tokens --
    session_claims = build_login_2fa_session_claims(user, db)
    token_claims = {"sub": user_id, "sid": auth_entry.id, **session_claims}
    new_access_token = create_access_token({**token_claims, "type": "access"}, db=db)
    new_refresh_token = create_refresh_token({**token_claims, "type": "refresh"}, db=db)
    terms_of_service_policy = get_terms_of_service_policy(db, user_id)

    # Persist the new tokens and bump last_active timestamps ------------------
    rotate_authentication_tokens(
        db,
        user_id,
        refresh_token,
        new_access_token,
        new_refresh_token,
        session_id=auth_entry.id,
        previous_refresh_expires_at=datetime.fromtimestamp(float(exp), timezone.utc),
    )

    update_last_active_user(db, user_id)
    user_agent, client_ip = _request_audit_context(request, db)
    create_authentication_log(
        db_log,
        "refresh",
        "info",
        "Refresh token was successful",
        user_id,
        user_agent,
        client_ip,
    )
    social_needs_password_setup = bool(get_user_setting_value(user_id, "social_login", "needs_password_setup", db) or False)
    sso_needs_password_setup = bool(get_user_setting_value(user_id, "sso_login", "needs_password_setup", db) or False)
    needs_password_setup = (
        False
        if is_externally_managed(user)
        else social_needs_password_setup or sso_needs_password_setup
    )

    # Include whether the user must change password
    has_to_change_password = bool(get_user_setting_value(user_id, "security", "has_to_change_password", db) or False)
    application_name = get_value_by_page_and_key("general", "application_name", db) or "Omlorix"
    # The refresh response is consumed by the index-page bootstrap before the
    # chat setup endpoint is fetched. Include the account preference here so
    # the frontend never has to choose a locale from another account's shared
    # browser storage while this session is being established.
    language = get_user_setting_value(user_id, "general", "language", db) or ""
    
    # Check if server setup is needed (only relevant for admins)
    needs_server_setup = False
    if is_admin_role(user.role):
        server_setup_complete = get_value_by_page_and_key("states", "server_setup", db)
        needs_server_setup = not server_setup_complete

    if active_slot:
        set_refresh_slot_cookie(response, active_slot, new_refresh_token, db, request)
        set_active_slot_cookie(response, active_slot, db, request)
    if request.cookies.get("refresh_token"):
        clear_legacy_refresh_cookie(response, db, request)
    set_access_token_cookie(response, new_access_token, db, request)
    
    return {
        "session_authenticated": True,
        "has_to_change_password": has_to_change_password,
        "needs_password_setup": needs_password_setup,
        "is_admin": is_admin_role(user.role),
        "user_role": user.role,
        "language": language,
        "application_name": application_name,
        "needs_server_setup": needs_server_setup,
        "active_account_slot": active_slot,
        "terms_of_service_policy": terms_of_service_policy,
    }
