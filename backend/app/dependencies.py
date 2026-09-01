import logging

from fastapi import Depends, Request, HTTPException, WebSocket, WebSocketException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from app.auth.account_slots import ACCESS_COOKIE, get_active_slot, get_refresh_slot_cookie_name
from app.utils.db import get_db, get_db_log as _get_db_log
from app.users.init import get_user_setting_value
from typing import Annotated
from app.auth.token import check_admin_by_token, check_user_by_token, ensure_access_token_satisfies_current_2fa_policy
from app.logging.models import get_audit_request_ip, stage_audit_log_event
from app.utils.client_ip import resolve_request_client_ip
from app.utils.origin import enforce_same_origin
from app.utils.utils import get_terms_of_service_policy



bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)
# Re-export the audit-session dependency alongside the main-session dependency.
get_db_log = _get_db_log



db_dependency = Annotated[Session, Depends(get_db)]
_SAFE_COOKIE_AUTH_METHODS = {"GET", "HEAD", "OPTIONS"}



# -------------------
# Resolve Access Token
# -------------------
def resolve_access_token(request: Request, credentials=None) -> str:
    """Resolve access token from Authorization header or HttpOnly cookie."""
    if credentials and getattr(credentials, "scheme", "").lower() == "bearer" and credentials.credentials:
        request.state.omlorix_access_token_source = "bearer"
        return credentials.credentials

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        request.state.omlorix_access_token_source = "bearer"
        return auth_header[7:]

    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        request.state.omlorix_access_token_source = "cookie"
        return token

    raise HTTPException(status_code=401, detail="Missing or invalid access token")


def resolve_access_token_for_authenticated_request(
    request: Request,
    credentials=None,
    db: Session | None = None,
) -> str:
    """Resolve an access token and enforce CSRF checks when cookie auth is used."""
    token = resolve_access_token(request, credentials)
    if db is not None:
        _enforce_same_origin_for_cookie_auth(request, db)
    return token


def verified_access_token(
    request: Request,
    credentials: str = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> str:
    """Resolve the active access token for routes that need the current session token string."""
    return resolve_access_token_for_authenticated_request(request, credentials, db)


def _client_host(request: Request) -> str | None:
    """Best-effort client IP extraction; request.client may be missing in tests or proxies."""
    return resolve_request_client_ip(request, default=None)


def _enforce_same_origin_for_cookie_auth(request: Request, db: Session) -> None:
    """Require same-origin checks for unsafe requests authenticated via cookies."""
    source = getattr(request.state, "omlorix_access_token_source", "")
    if source != "cookie":
        return
    if request.method.upper() in _SAFE_COOKIE_AUTH_METHODS:
        return
    enforce_same_origin(request, db)



# -------------------
# Verify Admin
# -------------------
def verified_admin(
    request: Request,
    credentials: str = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """Verify admin token and return admin user."""
    token = resolve_access_token_for_authenticated_request(request, credentials, db)
    try:
        user = check_admin_by_token(token, _client_host(request), "access", db)
    except HTTPException as exc:
        # Invalid or expired credentials do not provide a trustworthy actor.
        # A valid non-admin token does, and attempting to cross that privilege
        # boundary is useful security evidence even though the request remains
        # denied. Re-verify only this rare denial path to resolve the actor.
        if exc.status_code == 401 and exc.detail == "You do not have permission to perform this action":
            try:
                denied_user = check_user_by_token(
                    token,
                    _client_host(request),
                    "access",
                    db,
                )
                route = request.scope.get("route")
                route_path = getattr(route, "path", None) or request.url.path
                stage_audit_log_event(
                    db,
                    user_id=denied_user.id,
                    action="ADMIN_ACCESS_DENIED",
                    details={
                        "method": request.method,
                        "route": route_path,
                        "actor_role": getattr(denied_user, "role", None),
                    },
                    ip_address=get_audit_request_ip(request, db),
                    user_agent=request.headers.get("user-agent"),
                    category="auth_security",
                )
                db.commit()
            except Exception:
                db.rollback()
                # Audit delivery must never turn a denied request into a 500 or
                # reveal whether a supplied credential resolved to an account.
                logger.exception("Failed to record denied administrator access")
        raise
    _enforce_forced_password_change(request, user, db)
    _enforce_terms_of_service_acceptance(request, user, db)
    return user



# -------------------
# Verify User
# -------------------
FORCED_PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/api/v1/users/password/change",
    "/api/v1/users/password/set",
    "/api/v1/users/password/requirements",
    "/api/v1/auth/logout",
    "/api/v1/auth/logins",
    "/api/v1/auth/login",
}


# A user whose Terms acceptance is stale must still be able to inspect the
# applicable legal documents, record acceptance, sign out, and complete
# prerequisite account-recovery flows. Keep this list explicit and
# narrow: every other route guarded by ``verified_user`` or ``verified_admin``
# is denied by the server until the current revision has been accepted.
TERMS_OF_SERVICE_ACCEPTANCE_ALLOWED_PATHS = FORCED_PASSWORD_CHANGE_ALLOWED_PATHS | {
    "/api/v1/auth/access-status",
    "/api/v1/auth/logins",
    "/api/v1/auth/logout",
    "/api/v1/privacy",
    "/api/v1/privacy/policy",
    "/api/v1/settings/chat/setup",
    "/api/v1/terms",
    "/api/v1/users/privacy-policy/notice",
    "/api/v1/users/terms-of-service/accept",
    "/api/v1/users/user-settings/init",
}


TWOFA_POLICY_ALLOWED_PATHS = {
    "/api/v1/auth/logout",
}


def _enforce_forced_password_change(request: Request, user, db: Session) -> None:
    """Block authenticated API use until a required password change is completed."""
    needs_password_change = bool(get_user_setting_value(user.id, "security", "has_to_change_password", db) or False)
    if needs_password_change and request.url.path not in FORCED_PASSWORD_CHANGE_ALLOWED_PATHS:
        raise HTTPException(status_code=423, detail="Password change required before accessing other resources")


def _enforce_terms_of_service_acceptance(
    request: Request | WebSocket,
    user,
    db: Session,
) -> None:
    """Enforce the operator-configured Terms gate at the trusted server boundary.

    The frontend still uses the policy returned by ``POST /auth/refresh`` to
    provide a friendly redirect and acceptance modal.  That client behavior is
    intentionally not the security control: direct API and WebSocket clients
    pass through this helper as well.
    """
    if request.url.path in TERMS_OF_SERVICE_ACCEPTANCE_ALLOWED_PATHS:
        return

    policy = get_terms_of_service_policy(db, user.id)
    if not bool(policy.get("require_current_revision_for_access")):
        return
    if bool(policy.get("accepted_current_revision")):
        return

    raise HTTPException(
        status_code=423,
        detail={
            "type": "terms_of_service_acceptance_required",
            "revision": int(policy.get("revision") or 1),
        },
    )


def verified_user(request: Request, credentials: str = Depends(bearer_scheme), db: Session = Depends(get_db)):
    """Verify user token and return user."""
    token = resolve_access_token_for_authenticated_request(request, credentials, db)
    user = check_user_by_token(token, _client_host(request), "access", db, enforce_2fa_policy=False)
    if request.url.path not in TWOFA_POLICY_ALLOWED_PATHS:
        ensure_access_token_satisfies_current_2fa_policy(token, user, db)

    _enforce_forced_password_change(request, user, db)
    _enforce_terms_of_service_acceptance(request, user, db)
    return user


def verified_websocket_user(
    websocket: WebSocket,
    db: Session = Depends(get_db),
):
    """Authenticate a same-origin WebSocket with the active access cookie.

    Browsers cannot attach the normal ``Authorization`` header when creating a
    native WebSocket, so terminal sessions use the existing HttpOnly access
    cookie. The explicit Origin check is the WebSocket equivalent of the CSRF
    protection applied to unsafe cookie-authenticated HTTP routes.
    """
    try:
        enforce_same_origin(websocket, db)
        token = websocket.cookies.get(ACCESS_COOKIE)
        if not token:
            raise HTTPException(status_code=401, detail="Missing or invalid access token")
        user = check_user_by_token(
            token,
            resolve_request_client_ip(websocket, default=None),
            "access",
            db,
            enforce_2fa_policy=False,
        )
        ensure_access_token_satisfies_current_2fa_policy(token, user, db)
        _enforce_forced_password_change(websocket, user, db)
        _enforce_terms_of_service_acceptance(websocket, user, db)
        return user
    except HTTPException as exc:
        # WebSocket handshakes cannot return the ordinary JSON error shape.
        # Keep the close reason generic so authentication internals do not leak.
        reason = "Authentication failed" if exc.status_code in {401, 403} else "Access denied"
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=reason,
        ) from exc



# -------------------
# Verify User Refresh
# -------------------
def verified_user_refresh(request: Request, db: Session = Depends(get_db)):
    """Verify a refresh-token-authenticated app request and return its user.

    ``POST /auth/refresh`` does not use this dependency and therefore remains
    available to return the Terms policy that drives the frontend redirect.
    Routes that use a refresh token as their actual application authorization,
    however, must enforce the same Terms gate as access-token routes.
    """
    active_slot = get_active_slot(request)
    token = request.cookies.get(get_refresh_slot_cookie_name(active_slot)) if active_slot else request.cookies.get("refresh_token")
    if not token:
        # Same reasoning as above – be explicit and return a proper 401
        raise HTTPException(status_code=401, detail="Refresh token missing")
    request.state.omlorix_access_token_source = "cookie"
    _enforce_same_origin_for_cookie_auth(request, db)
    user = check_user_by_token(token, _client_host(request), "refresh", db)
    _enforce_terms_of_service_acceptance(request, user, db)
    return user
