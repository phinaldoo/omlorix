from fastapi import APIRouter, Depends, Request, status, HTTPException, Response, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import logging
import hashlib
import hmac
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from app.auth.enterprise_sso import SSOSecurityData
from app.dependencies import _client_host, get_db, get_db_log, resolve_access_token, verified_access_token, verified_user
from app.auth.account_slots import (
    SOCIAL_FLOW_COOKIE,
    SOCIAL_LINK_FLOW_COOKIE,
    SSO_FLOW_COOKIE,
    build_auth_redirect_base_url,
    should_secure_auth_cookie,
    list_accounts_payload,
    set_flow_context_cookie,
    clear_flow_context_cookie,
    read_flow_context_cookie,
    switch_active_account_slot,
    delete_account_slot,
    get_active_refresh_token,
    set_social_link_context_cookie,
    read_social_link_context_cookie,
    clear_social_link_context_cookie,
)
from app.auth.schemas import (
    SignInRequest,
    SignInOptionsRequest,
    PasswordResetRequest,
    PasswordResetValidate,
    PasswordResetConfirm,
    EmailChangeTokenRequest,
    EmailChangeResult,
    DeleteSpecificLoginRequest,
    SetupTwofaRequest,
    SocialAuthInitRequest,
    SocialAuthCallbackRequest,
    FederatedTermsConfirmRequest,
    PasskeyBeginRegistrationRequest,
    PasskeyFinishRegistrationRequest,
    PasskeyBeginAuthenticationRequest,
    PasskeyFinishAuthenticationRequest,
    PasskeyCompleteAuthenticationRequest,
    StepUpRequest,
    StepUpOtpBeginResponse,
    StepUpMethodsResponse,
    SignInMethodsResponse,
    SocialLinkInitResponse,
    NativeFederatedInitRequest,
    NativeSocialLinkInitRequest,
    NativeSocialLinkExchangeRequest,
    NativeAuthExchangeRequest,
    EnterpriseSSOProviderType,
    SSOAuthInitRequest,
    SSOAuthCallbackRequest,
)
from app.auth.native import (
    NATIVE_EXCHANGE_TTL_SECONDS,
    consume_native_auth_grant,
    create_native_auth_grant,
    create_native_failure_callback,
    get_native_callback_origin,
    is_native_flow,
    native_callback_url,
    normalize_native_failure_reason,
)
from app.auth.social import (
    SocialAuthProviderFactory,
    generate_oauth_state,
    generate_oauth_nonce,
)
from app.auth.models import get_authentication, mark_authentication_step_up
from app.auth.session_audit import build_login_revocation_audit_details
from app.auth.step_up import require_sensitive_action_auth
from app.users.roles import is_admin_role
from app.users.external_management import require_locally_managed_account
from app.auth.token import (
    check_user_by_token,
    get_access_token_by_refresh_token,
    require_recent_auth_token,
    rotate_current_session_tokens_with_2fa_claims,
)
from app.auth.twofa import setup_twofa, deactivate_twofa
from app.auth.twofa_provider import (
    _is_user_enrolled_for_provider,
    begin_verify,
    evaluate_login_2fa,
    get_totp_setup_material,
    resolve_user_2fa_provider,
    verify_login_code,
)
from app.auth.utils import (
    signup,
    signin,
    get_signin_options,
    request_password_reset,
    validate_password_reset_token,
    confirm_password_reset,
    logout,
    list_current_logins, 
    delete_login,
    social_login_callback,
    verified_social_user_info_from_callback,
    _issue_authenticated_session,
    _normalize_account_mode,
    _set_one_time_browser_cookie,
    _clear_one_time_browser_cookie,
    _set_pending_passkey_token,
    _clear_pending_passkey_token,
    _find_user_by_pending_passkey_token,
    _find_user_by_pending_signin_token,
    _find_user_by_pending_social_token,
    _find_user_by_pending_social_auth_code,
    _find_user_by_sso_token,
    _find_user_by_pending_sso_auth_code,
    _clear_pending_signin_token,
    _clear_pending_social_token,
    _clear_pending_sso_token,
    _clear_social_auth_exchange_state,
    _clear_sso_auth_exchange_state,
    confirm_pending_social_terms_signup,
    cancel_pending_social_terms_signup,
    confirm_pending_sso_terms_signup,
    cancel_pending_sso_terms_signup,
    _pending_token_is_active,
    _pending_token_allows_setup_material,
    validate_user_login_eligibility,
    check_failed_signin_attempts,
    verify_password,
)
from app.groups.access_windows import is_group_accessible_now
from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.settings.utils import get_value_by_page_and_key, coerce_bool
from app.users.schemas import UserCreate
from app.users.models import increment_user_wrong_sign_in_attempts
from app.utils.origin import enforce_same_origin
from app.logging.models import (
    create_audit_log,
    get_audit_request_ip,
    stage_audit_log_event,
)



logger = logging.getLogger(__name__)


auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _oauth_debug_mask(value: object, *, start: int = 6, end: int = 4) -> str:
    """Redact temporary OAuth diagnostics without logging credentials or tokens."""
    text = str(value or "")
    if not text:
        return "<empty>"
    if len(text) <= start + end:
        return f"<len={len(text)}>"
    return f"{text[:start]}...{text[-end:]}<len={len(text)}>"


def _oauth_debug_fingerprint(value: object) -> str:
    """Return a short correlation fingerprint for OAuth state values."""
    text = str(value or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else "<empty>"


def _audit_auth_security_event(
    db_log: Session,
    db: Session,
    request: Request,
    user_id: str,
    action: str,
    details: dict | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="auth_security",
    )


def _native_browser_start_url(
    db: Session,
    request: Request,
    *,
    path: str,
    ticket: str,
    native_state: str,
) -> str:
    """Build a canonical server URL that transfers state into the browser."""
    public_url = build_auth_redirect_base_url(db, request).rstrip("/")
    query = urlencode({"ticket": ticket, "native_state": native_state})
    return f"{public_url}/{path.lstrip('/')}?{query}"


def _redirect_error_key(result) -> str | None:
    """Extract only the public error key from a login redirect."""
    if not isinstance(result, Response):
        return None
    location = str(result.headers.get("location") or "")
    if not location:
        return None
    values = parse_qs(urlsplit(location).query)
    error = str((values.get("error") or [""])[0]).strip().lower()
    return error or None


def _result_failure_reason(result) -> str | None:
    error_key = _redirect_error_key(result)
    if error_key:
        return normalize_native_failure_reason(error_key)
    if isinstance(result, dict):
        status_value = str(result.get("status") or "").strip().lower()
        if status_value in {"error", "failed", "failure", "unauthorized"}:
            return "failed"
    return None


def _native_federated_failure_redirect(
    db: Session,
    request: Request,
    *,
    flow_context: dict,
    cookie_name: str,
    kind: str,
    provider: str,
    reason: str,
    source_response: Response | None = None,
) -> RedirectResponse | None:
    """End a native browser session with correlated, bounded failure data."""
    if not is_native_flow(flow_context, kind=kind):
        return None
    redirect = RedirectResponse(
        url=create_native_failure_callback(
            kind=kind,
            provider=provider,
            flow_context=flow_context,
            reason=reason,
        ),
        status_code=302,
    )
    if source_response is not None:
        for name, value in source_response.raw_headers:
            if name.lower() == b"set-cookie":
                redirect.raw_headers.append((name, value))
    clear_flow_context_cookie(redirect, db, request, cookie_name=cookie_name)
    return redirect


def _set_social_browser_flow_cookies(
    response: Response,
    db: Session,
    request: Request,
    *,
    state_hash: str,
    nonce: str,
) -> None:
    secure_cookie = should_secure_auth_cookie(db, request)
    same_site = "none" if secure_cookie else "lax"
    response.set_cookie(
        key="social_state",
        value=state_hash,
        httponly=True,
        samesite=same_site,
        secure=secure_cookie,
        max_age=600,
    )
    response.set_cookie(
        key="social_nonce",
        value=hashlib.sha256(nonce.encode()).hexdigest(),
        httponly=True,
        samesite=same_site,
        secure=secure_cookie,
        max_age=600,
    )


# -------------------
# Signup
# -------------------
@auth_router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup_route(request: Request, response: Response, user: UserCreate, db: Session = Depends(get_db), db_log: Session = Depends(get_db_log)):
    return signup(db, db_log, request, user, response)



# -------------------
# Signin
# -------------------
@auth_router.post("/signin")
def signin_route(request: Request, response: Response, user: SignInRequest, db: Session = Depends(get_db), db_log: Session = Depends(get_db_log)):
    enforce_same_origin(request, db)
    return signin(db, db_log, request, user, response)


@auth_router.post("/signin/options")
def signin_options_route(payload: SignInOptionsRequest, db: Session = Depends(get_db)):
    return get_signin_options(db, payload.identifier)


@auth_router.get("/ldap/status")
def ldap_status_route(db: Session = Depends(get_db)):
    from app.auth.ldap import get_ldap_provider

    return get_ldap_provider(db).get_public_status()



# -------------------
# Password Reset
# -------------------
@auth_router.post("/password-reset/request")
def password_reset_request_route(
    request: Request,
    payload: PasswordResetRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    return request_password_reset(db, db_log, request, payload.email)


@auth_router.post("/password-reset/validate")
def password_reset_validate_route(
    request: Request,
    payload: PasswordResetValidate,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    return validate_password_reset_token(db, payload.token, db_log=db_log, request=request)


@auth_router.post("/password-reset/confirm")
def password_reset_confirm_route(
    request: Request,
    payload: PasswordResetConfirm,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    return confirm_password_reset(db, db_log, request, payload.token, payload.new_password)


@auth_router.post("/email-change/confirm", response_model=EmailChangeResult)
def email_change_confirm_route(
    request: Request,
    payload: EmailChangeTokenRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    from app.email.change import confirm_email_change

    enforce_same_origin(request, db)
    result = confirm_email_change(db, payload.token)
    _audit_auth_security_event(
        db_log,
        db,
        request,
        result["user_id"],
        "EMAIL_ADDRESS_CHANGE_CONFIRMED",
        {"sessions_revoked": bool(result.get("sessions_revoked"))},
    )
    return result


@auth_router.post("/email-change/cancel", response_model=EmailChangeResult)
def email_change_cancel_route(
    request: Request,
    payload: EmailChangeTokenRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    from app.email.change import cancel_email_change

    enforce_same_origin(request, db)
    result = cancel_email_change(db, payload.token)
    _audit_auth_security_event(
        db_log,
        db,
        request,
        result["user_id"],
        "EMAIL_ADDRESS_CHANGE_CANCELLED",
        {"sessions_revoked": bool(result.get("sessions_revoked"))},
    )
    return result


# -------------------
# Logout
# -------------------
@auth_router.post("/logout")
def logout_route(request: Request, response: Response, db: Session = Depends(get_db), db_log: Session = Depends(get_db_log)):
    access_error = None
    try:
        token = resolve_access_token(request)
        if getattr(request.state, "omlorix_access_token_source", "") == "cookie":
            enforce_same_origin(request, db)
        user = check_user_by_token(token, _client_host(request), "access", db, enforce_2fa_policy=False)
        return logout(
            db,
            db_log,
            request,
            user.id,
            token,
            response,
            external_auth_provider=getattr(user, "external_auth_provider", None),
        )
    except HTTPException as exc:
        access_error = exc
        if exc.status_code != 401:
            raise

    refresh_token, _active_slot = get_active_refresh_token(request, response, db)
    if not refresh_token:
        raise access_error

    enforce_same_origin(request, db)
    user = check_user_by_token(refresh_token, _client_host(request), "refresh", db, enforce_2fa_policy=False)
    return logout(
        db,
        db_log,
        request,
        user.id,
        refresh_token,
        response,
        token_type="refresh",
        external_auth_provider=getattr(user, "external_auth_provider", None),
    )



# -------------------
# Refresh
# -------------------
@auth_router.post("/refresh")
def refresh_route(request: Request, response: Response, db: Session = Depends(get_db), db_log: Session = Depends(get_db_log)):
    enforce_same_origin(request, db)
    return get_access_token_by_refresh_token(request, response, db, db_log)


@auth_router.get("/accounts")
def list_accounts_route(request: Request, response: Response, db: Session = Depends(get_db)):
    return list_accounts_payload(request, response, db)


@auth_router.post("/accounts/switch")
def switch_accounts_route(
    request: Request,
    response: Response,
    payload: dict,
    db: Session = Depends(get_db),
):
    enforce_same_origin(request, db)
    return switch_active_account_slot(
        request,
        response,
        db,
        payload.get("slot"),
    )


@auth_router.delete("/accounts/{slot}")
def delete_account_route(
    slot: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    enforce_same_origin(request, db)
    return delete_account_slot(request, response, db, slot)



# -------------------
# User Settings Screen: Current Logins
# -------------------
@auth_router.get("/logins")
def current_logins_route(db: Session = Depends(get_db), user = Depends(verified_user), token: str = Depends(verified_access_token)):
    return list_current_logins(user.id, db, token)



# -------------------
# User Settings Screen: Delete Login
# -------------------
@auth_router.delete("/login")
def delete_login_route(
    request: Request,
    response: Response,
    payload: DeleteSpecificLoginRequest | None = None,
    db: Session = Depends(get_db),
    user = Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    auth_id = payload.auth_id if payload and payload.auth_id else None
    audit_ip_address = get_audit_request_ip(request, db)
    audit_user_agent = request.headers.get("user-agent")

    def stage_revocation_audit(deleted_rows: list) -> None:
        audit_details = build_login_revocation_audit_details(
            deleted_rows,
            current_access_token=token,
            auth_id=auth_id,
        )
        if audit_details is None:
            return
        stage_audit_log_event(
            db,
            user_id=user.id,
            action="LOGIN_SESSIONS_REVOKED",
            details=audit_details,
            ip_address=audit_ip_address,
            user_agent=audit_user_agent,
            category="auth_security",
        )

    return delete_login(
        user.id,
        db,
        token,
        auth_id,
        request,
        response,
        before_commit=stage_revocation_audit,
    )


def _step_up_success_response(db: Session, db_log: Session, request: Request, user_id: str, token: str, method: str) -> dict:
    """Persist a successful step-up result and audit the authentication event."""

    mark_authentication_step_up(db, user_id, token, method)
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user_id,
        "STEP_UP_AUTHENTICATED",
        {"method": method},
    )
    return {"status": "success", "method": method, "expires_in_seconds": 10 * 60}


def _audit_step_up_failure(db: Session, db_log: Session, request: Request, user_id: str, method: str, reason: str) -> None:
    """Record a failed step-up attempt in the audit log."""

    _audit_auth_security_event(
        db_log,
        db,
        request,
        user_id,
        "STEP_UP_AUTH_FAILED",
        {"method": method, "reason": reason},
    )


@auth_router.get("/step-up/methods", response_model=StepUpMethodsResponse)
def step_up_methods_route(
    response: Response,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    """Describe only the verification methods enrolled by the current user."""

    from app.auth.step_up import get_step_up_methods

    # This response is user-specific security metadata and must never be
    # reused by a shared browser or intermediary cache.
    response.headers["Cache-Control"] = "private, no-store"
    methods = get_step_up_methods(user, db)
    recent_auth_sufficient = False
    if not any(methods.values()):
        try:
            require_recent_auth_token(token, db)
            recent_auth_sufficient = True
        except HTTPException:
            recent_auth_sufficient = False
    return {**methods, "recent_auth_sufficient": recent_auth_sufficient}


@auth_router.post("/step-up")
def step_up_route(
    payload: StepUpRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    enforce_same_origin(request, db)
    require_locally_managed_account(user)

    password = str(payload.password or "")
    if password:
        generic_failure = HTTPException(status_code=401, detail="Step-up authentication failed")
        lock_identifier = getattr(user, "email", None) or user.id
        lockout_enabled = bool(get_value_by_page_and_key("security", "enable_block_user_after_wrong_signin", db))
        if lockout_enabled and check_failed_signin_attempts(lock_identifier, db):
            _audit_step_up_failure(db, db_log, request, user.id, "password", "invalid")
            raise generic_failure

        hashed_password = getattr(user, "hashed_password", None)
        if hashed_password and verify_password(password, hashed_password):
            return _step_up_success_response(db, db_log, request, user.id, token, "password")
        increment_user_wrong_sign_in_attempts(db, user.id)
        if lockout_enabled:
            check_failed_signin_attempts(lock_identifier, db)
        _audit_step_up_failure(db, db_log, request, user.id, "password", "invalid")
        raise generic_failure

    otp_code = str(payload.otp_code or "").strip()
    if otp_code:
        provider = resolve_user_2fa_provider(user, db)
        if not _is_user_enrolled_for_provider(user.id, provider, db):
            _audit_step_up_failure(db, db_log, request, user.id, "otp", "not_enrolled")
            raise HTTPException(status_code=403, detail="No enrolled two-factor method is available for step-up")

        verification = verify_login_code(
            user,
            otp_code,
            db,
            provider,
            client_ip=getattr(request.client, "host", None),
            purpose="step_up",
        )
        if verification is True:
            return _step_up_success_response(db, db_log, request, user.id, token, provider)
        if verification == "locked":
            _audit_step_up_failure(db, db_log, request, user.id, provider, "locked")
            raise HTTPException(status_code=429, detail="Step-up authentication is temporarily locked")
        _audit_step_up_failure(db, db_log, request, user.id, provider, "invalid")
        raise HTTPException(status_code=401, detail="Step-up authentication failed")

    if payload.passkey_credential and payload.expected_challenge:
        from app.auth.passkeys import finish_authentication

        result = finish_authentication(
            db,
            credential=payload.passkey_credential,
            expected_challenge=payload.expected_challenge,
        )
        if result.get("user_id") != user.id:
            _audit_step_up_failure(db, db_log, request, user.id, "passkey", "wrong_user")
            raise HTTPException(status_code=403, detail="Passkey does not belong to the current user")
        return _step_up_success_response(db, db_log, request, user.id, token, "passkey")

    raise HTTPException(status_code=400, detail="Password, OTP code, or passkey response is required")


@auth_router.post("/step-up/otp/begin", response_model=StepUpOtpBeginResponse)
def step_up_otp_begin_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
):
    """Prepare the user's enrolled OTP method for an explicit step-up.

    TOTP needs no server-side preparation. Delivery-based providers use this
    call to issue a purpose-bound, throttled verification code before the
    shared step-up dialog accepts it.
    """
    enforce_same_origin(request, db)
    provider = resolve_user_2fa_provider(user, db)
    if not _is_user_enrolled_for_provider(user.id, provider, db):
        raise HTTPException(status_code=403, detail="No enrolled two-factor method is available for step-up")

    result = begin_verify(user, "step_up", db, provider)
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "STEP_UP_OTP_PREPARED",
        {"provider": provider},
    )
    return result


@auth_router.post("/step-up/passkey/begin")
def step_up_passkey_begin_route(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(verified_user),
):
    from app.auth.passkeys import begin_authentication

    enforce_same_origin(request, db)
    return begin_authentication(
        db,
        identifier=user.email,
        public_origin=request.headers.get("origin") or request.headers.get("referer") or str(request.base_url),
    )


# -------------------
# 2FA Setup
# -------------------
@auth_router.post("/twofa/setup")
def setup_twofa_route(
    payload: SetupTwofaRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    enforce_same_origin(request, db)
    require_sensitive_action_auth(user, token, db)
    result = setup_twofa(
        payload.temp_secret,
        payload.otp_code,
        user.id,
        user.email,
        user.group_id,
        db,
        otp_action=payload.otp_action,
        otp_destination=payload.otp_destination,
        client_ip=getattr(request.client, "host", None),
    )
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "TWOFA_SETUP",
        {
            "status": result.get("status") if isinstance(result, dict) else None,
            "otp_action": payload.otp_action,
            "otp_destination_provided": bool(payload.otp_destination),
        },
    )
    if isinstance(result, dict) and result.get("status") == "success":
        result = {
            **result,
            **rotate_current_session_tokens_with_2fa_claims(
                request,
                response,
                db,
                user,
                token,
            ),
        }
    return result


@auth_router.get("/twofa/setup-material")
def twofa_setup_material_route(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    try:
        access_token = resolve_access_token(request)
    except HTTPException:
        access_token = None

    if access_token:
        if getattr(request.state, "omlorix_access_token_source", "") == "cookie":
            enforce_same_origin(request, db)
        user = check_user_by_token(access_token, _client_host(request), "access", db)
        require_sensitive_action_auth(user, access_token, db)
        material = get_totp_setup_material(user, db)
        _audit_auth_security_event(
            db_log,
            db,
            request,
            user.id,
            "TWOFA_SETUP_MATERIAL_ACCESSED",
            {"flow": "authenticated_session"},
        )
        return material

    for cookie_name, finder, clearer, page, expires_key, allow_key, flow in (
        (
            "signin_login_token",
            _find_user_by_pending_signin_token,
            _clear_pending_signin_token,
            "secret",
            "signin_pending_token_expires_at",
            "signin_pending_setup_material_allowed",
            "pending_password_signin",
        ),
        (
            "social_login_token",
            _find_user_by_pending_social_token,
            _clear_pending_social_token,
            "social_login",
            "pending_social_token_expires",
            "pending_setup_material_allowed",
            "pending_social_signin",
        ),
        (
            "sso_login_token",
            _find_user_by_sso_token,
            _clear_pending_sso_token,
            "sso_login",
            "pending_sso_token_expires",
            "pending_setup_material_allowed",
            "pending_sso_signin",
        ),
        (
            "passkey_login_token",
            _find_user_by_pending_passkey_token,
            _clear_pending_passkey_token,
            "secret",
            "passkey_pending_token_expires_at",
            "passkey_pending_setup_material_allowed",
            "pending_passkey_signin",
        ),
    ):
        token = request.cookies.get(cookie_name)
        if not token:
            continue
        user = finder(db, token)
        if not user:
            response.delete_cookie(cookie_name)
            continue
        if _pending_token_is_active(user.id, page, expires_key, db):
            if not _pending_token_allows_setup_material(user.id, page, allow_key, db):
                continue
            material = get_totp_setup_material(user, db)
            _audit_auth_security_event(
                db_log,
                db,
                request,
                user.id,
                "TWOFA_SETUP_MATERIAL_ACCESSED",
                {"flow": flow},
            )
            return material
        clearer(user.id, db)
        response.delete_cookie(cookie_name)

    raise HTTPException(status_code=401, detail="Missing or invalid 2FA setup session.")



# -------------------
# 2FA Deactivate
# -------------------
@auth_router.post("/twofa/deactivate")
def deactivate_twofa_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user = Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    enforce_same_origin(request, db)
    # Existing factors require explicit proof. A factorless account may use
    # only a freshly authenticated session, matching all bootstrap endpoints.
    require_sensitive_action_auth(user, token, db)
    result = deactivate_twofa(user.id, db)
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "TWOFA_DEACTIVATED",
        {
            "status": result.get("status") if isinstance(result, dict) else None,
            "security_notification": result.get("security_notification") if isinstance(result, dict) else None,
        },
    )
    return result



# -------------------
# Check Access Status
# -------------------
@auth_router.get("/access-status")
def check_access_status_route(db: Session = Depends(get_db), user = Depends(verified_user)):
    """
    Check if the current user has access based on time windows.
    This endpoint is already protected by verified_user dependency,
    so if it returns successfully, access is granted.
    """
    is_admin = is_admin_role(user.role)
    access_check = is_group_accessible_now(user.group_id, db, is_admin=is_admin)
    
    return {
        "accessible": access_check.get("accessible", True),
        "reason": access_check.get("reason"),
        "next_allowed_at": access_check.get("next_allowed_at"),
        "blocked_message": access_check.get("blocked_message"),
    }



# -------------------
# Passkeys (WebAuthn)
# -------------------
@auth_router.post("/passkeys/register/begin")
def passkey_begin_registration(
    payload: PasskeyBeginRegistrationRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    from app.auth.passkeys import begin_registration

    enforce_same_origin(request, db)
    require_locally_managed_account(user)
    require_sensitive_action_auth(user, token, db)
    result = begin_registration(
        db,
        user_id=user.id,
        public_origin=request.headers.get("origin") or request.headers.get("referer") or str(request.base_url),
    )
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "PASSKEY_REGISTRATION_STARTED",
        {"status": result.get("status") if isinstance(result, dict) else None},
    )
    return result



@auth_router.post("/passkeys/register/finish")
def passkey_finish_registration(
    request: Request,
    payload: PasskeyFinishRegistrationRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    from app.auth.passkeys import finish_registration

    enforce_same_origin(request, db)
    require_locally_managed_account(user)
    require_sensitive_action_auth(user, token, db)
    result = finish_registration(
        db,
        user_id=user.id,
        credential=payload.credential,
        expected_challenge=payload.expected_challenge,
        user_agent=request.headers.get("user-agent"),
    )
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "PASSKEY_REGISTERED",
        {"status": result.get("status") if isinstance(result, dict) else None},
    )
    return result



@auth_router.post("/passkeys/authenticate/begin")
def passkey_begin_authentication(
    payload: PasskeyBeginAuthenticationRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    from app.auth.passkeys import begin_authentication

    return begin_authentication(
        db,
        identifier=payload.identifier,
        public_origin=request.headers.get("origin") or request.headers.get("referer") or str(request.base_url),
    )



@auth_router.post("/passkeys/authenticate/finish")
def passkey_finish_authentication(
    request: Request,
    response: Response,
    payload: PasskeyFinishAuthenticationRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    from datetime import datetime, timezone
    from app.auth.passkeys import finish_authentication
    from app.users.models import get_user
    auth_result = finish_authentication(
        db,
        credential=payload.credential,
        expected_challenge=payload.expected_challenge,
    )

    user_id = auth_result.get("user_id")
    if not user_id:
        raise HTTPException(status_code=500, detail="Passkey authentication did not return a user")

    user = get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_locally_managed_account(user)

    eligibility = validate_user_login_eligibility(user, db)
    if eligibility:
        return eligibility

    twofa_result = evaluate_login_2fa(
        user,
        otp_code=None,
        otp_action=None,
        otp_destination=None,
        db=db,
        client_ip=getattr(request.client, "host", None),
    )
    if twofa_result:
        if twofa_result.get("status") in {"otp_setup", "otp_required_already_setup"}:
            passkey_token, token_expires_at = _set_pending_passkey_token(
                user.id,
                db,
                allow_setup_material=twofa_result.get("status") == "otp_setup",
            )
            _set_one_time_browser_cookie(
                response,
                "passkey_login_token",
                passkey_token,
                db,
                request,
                max_age=max(1, int((token_expires_at - datetime.now(timezone.utc)).total_seconds())),
            )
        return twofa_result

    _clear_pending_passkey_token(user.id, db)
    _clear_one_time_browser_cookie(response, "passkey_login_token")

    issued = _issue_authenticated_session(
        db=db,
        db_log=db_log,
        request=request,
        response=response,
        user=user,
        log_event="passkey_signin",
        success_message="Passkey sign-in was successful",
        account_mode=_normalize_account_mode(getattr(payload, "account_mode", None)),
        replace_slot=getattr(payload, "replace_slot", None),
    )
    if issued.get("status") == "max_accounts_reached":
        return issued

    needs_server_setup = False
    if is_admin_role(user.role):
        server_setup_complete = get_value_by_page_and_key("states", "server_setup", db)
        needs_server_setup = not server_setup_complete

    return {
        "status": "success",
        **issued,
        "needs_server_setup": needs_server_setup,
    }


@auth_router.post("/passkeys/complete")
def passkey_complete_authentication(
    request: Request,
    response: Response,
    payload: PasskeyCompleteAuthenticationRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    from datetime import datetime, timezone
    from app.users.init import get_user_setting_value
    from app.auth.twofa_provider import normalize_otp_action

    def _fail_passkey_completion(
        *,
        detail: str | None = None,
        payload: dict | None = None,
        user_id: str | None = None,
        clear_pending: bool = False,
        clear_cookie: bool = True,
    ):
        if clear_pending and user_id:
            _clear_pending_passkey_token(
                user_id,
                db,
                raw_token=passkey_token,
            )
        if clear_cookie:
            _clear_one_time_browser_cookie(response, "passkey_login_token")
        if payload is not None:
            return payload
        return {"status": "error", "detail": detail or "Invalid or expired passkey login token"}

    enforce_same_origin(request, db)

    passkey_token = payload.passkey_token or request.cookies.get("passkey_login_token")
    if not passkey_token:
        return {"status": "error", "detail": "Invalid or expired passkey login token"}

    user = _find_user_by_pending_passkey_token(db, passkey_token)
    if not user:
        return _fail_passkey_completion(detail="Invalid or expired passkey login token")
    try:
        require_locally_managed_account(user)
    except HTTPException:
        _clear_pending_passkey_token(
            user.id,
            db,
            raw_token=passkey_token,
        )
        _clear_one_time_browser_cookie(response, "passkey_login_token")
        raise

    expires_str = str(
        get_user_setting_value(
            user.id,
            "secret",
            "passkey_pending_token_expires_at",
            db,
            commit=False,
        )
        or ""
    ).strip()
    if not expires_str:
        return _fail_passkey_completion(
            detail="Passkey login session expired. Please try again.",
            user_id=user.id,
            clear_pending=True,
        )
    try:
        expires_at = datetime.fromisoformat(expires_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            return _fail_passkey_completion(
                detail="Passkey login session expired. Please try again.",
                user_id=user.id,
                clear_pending=True,
            )
    except Exception:
        return _fail_passkey_completion(
            detail="Passkey login session expired. Please try again.",
            user_id=user.id,
            clear_pending=True,
        )

    eligibility = validate_user_login_eligibility(user, db)
    if eligibility:
        return _fail_passkey_completion(payload=eligibility, user_id=user.id, clear_pending=True)

    action = normalize_otp_action(payload.otp_action, payload.otp_type, payload.otp_code)
    twofa_result = evaluate_login_2fa(
        user,
        otp_code=payload.otp_code,
        otp_action=action,
        otp_destination=payload.otp_destination,
        db=db,
        client_ip=getattr(request.client, "host", None),
    )
    if twofa_result:
        if twofa_result.get("status") == "otp_invalid":
            client_ip = getattr(request.client, "host", None) or "unknown"
            user_agent = request.headers.get("User-Agent", "Unknown Device")
            from app.logging.models import create_authentication_log

            create_authentication_log(
                db_log,
                "passkey_signin",
                "warning",
                "OTP code invalid during passkey login",
                user.id,
                user_agent,
                client_ip,
            )
        elif twofa_result.get("status") == "otp_locked":
            client_ip = getattr(request.client, "host", None) or "unknown"
            user_agent = request.headers.get("User-Agent", "Unknown Device")
            from app.logging.models import create_authentication_log

            create_authentication_log(
                db_log,
                "passkey_signin",
                "warning",
                "OTP verification locked after repeated failures during passkey login",
                user.id,
                user_agent,
                client_ip,
            )
        return twofa_result

    if not _clear_pending_passkey_token(
        user.id,
        db,
        raw_token=passkey_token,
    ):
        return _fail_passkey_completion(
            detail="Invalid or expired passkey login token"
        )
    _clear_one_time_browser_cookie(response, "passkey_login_token")

    issued = _issue_authenticated_session(
        db=db,
        db_log=db_log,
        request=request,
        response=response,
        user=user,
        log_event="passkey_signin",
        success_message="Passkey sign-in with 2FA was successful",
        account_mode=_normalize_account_mode(getattr(payload, "account_mode", None)),
        replace_slot=getattr(payload, "replace_slot", None),
        twofa_satisfied=bool(payload.otp_code),
    )
    if issued.get("status") == "max_accounts_reached":
        return issued

    needs_server_setup = False
    if is_admin_role(user.role):
        server_setup_complete = get_value_by_page_and_key("states", "server_setup", db)
        needs_server_setup = not server_setup_complete

    return {
        "status": "success",
        **issued,
        "needs_server_setup": needs_server_setup,
    }



@auth_router.get("/passkeys/list")
def list_passkeys(
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """List all active passkeys for the current user."""
    from app.auth.passkeys import list_user_passkeys

    require_locally_managed_account(user)
    
    passkeys = list_user_passkeys(db, user.id)
    return {
        "passkeys": [
            {
                "id": pk.id,
                "name": pk.name,
                "created_at": pk.created_at.isoformat() if pk.created_at else None,
                "last_used_at": pk.last_used_at.isoformat() if pk.last_used_at else None,
            }
            for pk in passkeys
        ]
    }



@auth_router.delete("/passkeys/{passkey_id}")
def delete_passkey(
    passkey_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    """Delete/deactivate a specific passkey."""
    from app.auth.models import PasskeyCredential, delete_user_transient_auth_state
    from app.users.models import User

    enforce_same_origin(request, db)
    require_locally_managed_account(user)
    require_sensitive_action_auth(user, token, db)

    # Keep the same user-first lock ordering as every pending-action mutation.
    # This makes factor removal and continuation invalidation one transaction
    # without introducing a user/action versus action/user deadlock.
    locked_user = (
        db.query(User)
        .filter(User.id == user.id)
        .with_for_update()
        .first()
    )
    if locked_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    
    passkey = (
        db.query(PasskeyCredential)
        .filter(
            PasskeyCredential.id == passkey_id,
            PasskeyCredential.user_id == user.id,
            PasskeyCredential.is_active.is_(True),
        )
        .with_for_update()
        .first()
    )
    
    if not passkey:
        raise HTTPException(status_code=404, detail="Passkey not found")

    passkey.is_active = False
    from app.email.service import enqueue_security_event, security_request_context

    context = security_request_context(request, db)
    try:
        delete_user_transient_auth_state(db, locked_user.id, commit=False)
        enqueue_security_event(
            db,
            user=locked_user,
            event_type="passkey_removed",
            source_id=f"{passkey.id}:{datetime.now(timezone.utc).isoformat()}",
            device=context.get("device"),
            network=context.get("network"),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "PASSKEY_DELETED",
        {"passkey_id": passkey_id},
    )

    return {"status": "success", "message": "Passkey removed successfully"}



# -------------------
# Social Login: Get Available Providers
# -------------------
@auth_router.get("/social/providers")
def get_social_providers(db: Session = Depends(get_db)):
    """Get list of enabled social login providers for the login page."""
    providers = SocialAuthProviderFactory.get_enabled_providers(db)
    offline_mode = coerce_bool(get_value_by_page_and_key("general", "offline_mode", db), default=False)
    filtered_providers = {}
    for provider_name, provider_config in (providers or {}).items():
        try:
            social_provider = SocialAuthProviderFactory.get_provider(provider_name, db)
            assert_url_allowed(
                db,
                url=getattr(social_provider, "AUTHORIZATION_URL", None),
                feature=f"social login provider '{provider_name}'",
            )
        except (HTTPException, OutboundRequestBlockedError):
            continue
        filtered_providers[provider_name] = provider_config
    return {
        "providers": filtered_providers,
        "offline_mode": offline_mode,
    }


@auth_router.post("/native/social/{provider}/init", response_model=SocialLinkInitResponse)
def init_native_social_login(
    provider: str,
    payload: NativeFederatedInitRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a one-time browser-start ticket for native social sign-in."""
    get_native_callback_origin()
    normalized_provider = str(provider or "").strip().lower()
    social_provider = SocialAuthProviderFactory.get_provider(normalized_provider, db)
    if not social_provider.is_enabled():
        raise HTTPException(status_code=400, detail=f"{normalized_provider.title()} login is not enabled")
    try:
        assert_url_allowed(
            db,
            url=getattr(social_provider, "AUTHORIZATION_URL", None),
            feature=f"{normalized_provider.title()} native social login",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc

    ticket = create_native_auth_grant(
        db,
        purpose="social_start",
        provider=normalized_provider,
        code_challenge=payload.code_challenge,
        state=payload.state,
        account_mode=payload.account_mode,
        replace_slot=payload.replace_slot,
        accepts_terms_of_service=payload.accept_terms_of_service,
        terms_of_service_revision=payload.terms_of_service_revision,
    )
    return {
        "authorization_url": _native_browser_start_url(
            db,
            request,
            path=f"api/v1/auth/native/social/{normalized_provider}/start",
            ticket=ticket,
            native_state=payload.state,
        )
    }


@auth_router.get("/native/social/{provider}/start")
def start_native_social_login(
    provider: str,
    request: Request,
    ticket: str = Query(..., min_length=32, max_length=512),
    native_state: str = Query(..., min_length=43, max_length=128),
    db: Session = Depends(get_db),
):
    """Move a native login grant into HttpOnly browser OAuth cookies."""
    normalized_provider = str(provider or "").strip().lower()
    grant = consume_native_auth_grant(
        db,
        ticket,
        expected_purposes={"social_start"},
        state=native_state,
    )
    if grant.provider != normalized_provider:
        raise HTTPException(status_code=400, detail="Native authentication provider mismatch.")

    social_provider = SocialAuthProviderFactory.get_provider(normalized_provider, db)
    if not social_provider.is_enabled():
        raise HTTPException(status_code=400, detail=f"{normalized_provider.title()} login is not enabled")
    try:
        assert_url_allowed(
            db,
            url=getattr(social_provider, "AUTHORIZATION_URL", None),
            feature=f"{normalized_provider.title()} native social login",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc

    oauth_state, state_hash = generate_oauth_state()
    nonce = generate_oauth_nonce()
    public_url = build_auth_redirect_base_url(db, request)
    redirect_uri = f"{public_url}/api/v1/auth/social/{normalized_provider}/callback"
    authorization_url = social_provider.get_authorization_url(redirect_uri, oauth_state, nonce)
    redirect = RedirectResponse(url=authorization_url, status_code=302)
    _set_social_browser_flow_cookies(
        redirect,
        db,
        request,
        state_hash=state_hash,
        nonce=nonce,
    )
    set_flow_context_cookie(
        redirect,
        db,
        request,
        cookie_name=SOCIAL_FLOW_COOKIE,
        account_mode=grant.account_mode,
        replace_slot=grant.replace_slot,
        return_url="",
        accept_terms_of_service=grant.accepts_terms_of_service,
        terms_of_service_revision=grant.terms_of_service_revision,
        native_auth=True,
        native_kind="social",
        native_provider=normalized_provider,
        native_code_challenge=grant.code_challenge,
        native_state=native_state,
    )
    return redirect


@auth_router.post(
    "/native/social/{provider}/link/init",
    response_model=SocialLinkInitResponse,
)
def init_native_social_identity_link(
    provider: str,
    payload: NativeSocialLinkInitRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    """Issue a browser ticket without copying API cookies into the browser."""
    from app.auth.identities import get_sign_in_methods, normalize_social_provider

    enforce_same_origin(request, db)
    require_locally_managed_account(user)
    require_sensitive_action_auth(user, token, db)
    get_native_callback_origin()
    normalized_provider = normalize_social_provider(provider)
    current_methods = get_sign_in_methods(user.id, db)
    current_provider = next(
        (item for item in current_methods["providers"] if item["provider"] == normalized_provider),
        None,
    )
    if current_provider and current_provider["linked"]:
        raise HTTPException(status_code=409, detail="This provider is already connected.")

    social_provider = SocialAuthProviderFactory.get_provider(normalized_provider, db)
    if not social_provider.is_enabled():
        raise HTTPException(status_code=409, detail="This social login provider is not available.")
    try:
        assert_url_allowed(
            db,
            url=getattr(social_provider, "AUTHORIZATION_URL", None),
            feature=f"{normalized_provider.title()} native social login linking",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc

    authentication = get_authentication(db, user.id, token, "access_token")
    if authentication is None:
        raise HTTPException(status_code=401, detail="The current session is no longer active.")
    ticket = create_native_auth_grant(
        db,
        purpose="social_link_start",
        provider=normalized_provider,
        user_id=user.id,
        authentication_id=authentication.id,
        code_challenge=payload.code_challenge,
        state=payload.state,
    )
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "SOCIAL_IDENTITY_LINK_STARTED",
        {"provider": normalized_provider, "client": "native"},
    )
    return {
        "authorization_url": _native_browser_start_url(
            db,
            request,
            path=f"api/v1/auth/native/social/{normalized_provider}/link/start",
            ticket=ticket,
            native_state=payload.state,
        )
    }


@auth_router.get("/native/social/{provider}/link/start")
def start_native_social_identity_link(
    provider: str,
    request: Request,
    ticket: str = Query(..., min_length=32, max_length=512),
    native_state: str = Query(..., min_length=43, max_length=128),
    db: Session = Depends(get_db),
):
    """Redeem an authenticated link ticket inside the system browser."""
    from app.auth.identities import normalize_social_provider, validate_social_link_session

    normalized_provider = normalize_social_provider(provider)
    grant = consume_native_auth_grant(
        db,
        ticket,
        expected_purposes={"social_link_start"},
        state=native_state,
    )
    if grant.provider != normalized_provider or not grant.user_id or not grant.authentication_id:
        raise HTTPException(status_code=400, detail="Native social link grant mismatch.")
    validate_social_link_session(
        {
            "provider": normalized_provider,
            "user_id": grant.user_id,
            "authentication_id": grant.authentication_id,
        },
        normalized_provider,
        db,
    )

    social_provider = SocialAuthProviderFactory.get_provider(normalized_provider, db)
    if not social_provider.is_enabled():
        raise HTTPException(status_code=409, detail="This social login provider is not available.")
    try:
        assert_url_allowed(
            db,
            url=getattr(social_provider, "AUTHORIZATION_URL", None),
            feature=f"{normalized_provider.title()} native social login linking",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc

    oauth_state, state_hash = generate_oauth_state()
    nonce = generate_oauth_nonce()
    public_url = build_auth_redirect_base_url(db, request)
    redirect_uri = f"{public_url}/api/v1/auth/social/{normalized_provider}/callback"
    authorization_url = social_provider.get_authorization_url(redirect_uri, oauth_state, nonce)
    redirect = RedirectResponse(url=authorization_url, status_code=302)
    _set_social_browser_flow_cookies(
        redirect,
        db,
        request,
        state_hash=state_hash,
        nonce=nonce,
    )
    set_social_link_context_cookie(
        redirect,
        db,
        request,
        user_id=grant.user_id,
        authentication_id=grant.authentication_id,
        provider=normalized_provider,
        state_hash=state_hash,
        native_state=native_state,
        native_code_challenge=grant.code_challenge,
    )
    return redirect


@auth_router.post(
    "/native/social/{provider}/link/exchange",
    response_model=SignInMethodsResponse,
)
def exchange_native_social_identity_link(
    provider: str,
    payload: NativeSocialLinkExchangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    """Apply a verified provider identity only after the app proves PKCE possession."""
    from app.auth.identities import (
        get_sign_in_methods,
        link_social_identity,
        normalize_social_provider,
        validate_social_link_session,
    )

    enforce_same_origin(request, db)
    require_locally_managed_account(user)
    normalized_provider = normalize_social_provider(provider)
    grant = consume_native_auth_grant(
        db,
        payload.code,
        expected_purposes={"social_link_exchange"},
        state=payload.state,
        code_verifier=payload.code_verifier,
    )
    if (
        grant.provider != normalized_provider
        or not grant.user_id
        or not grant.authentication_id
        or not grant.identity_claims
    ):
        raise HTTPException(status_code=400, detail="Native social link grant mismatch.")
    if str(grant.user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Native social link user mismatch.")

    social_provider = SocialAuthProviderFactory.get_provider(normalized_provider, db)
    if not social_provider.is_enabled():
        raise HTTPException(status_code=409, detail="This social login provider is not available.")
    user_id = validate_social_link_session(
        {
            "provider": normalized_provider,
            "user_id": grant.user_id,
            "authentication_id": grant.authentication_id,
        },
        normalized_provider,
        db,
    )
    link_social_identity(user_id, normalized_provider, grant.identity_claims, db)
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user_id,
        "SOCIAL_IDENTITY_LINKED",
        {"provider": normalized_provider, "client": "native"},
    )
    return get_sign_in_methods(user_id, db)


@auth_router.get("/sign-in-methods", response_model=SignInMethodsResponse)
def list_sign_in_methods(
    response: Response,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """List current primary sign-in methods without exposing provider subjects."""
    from app.auth.identities import get_sign_in_methods

    response.headers["Cache-Control"] = "private, no-store"
    return get_sign_in_methods(user.id, db)


@auth_router.post(
    "/social/{provider}/link/init",
    response_model=SocialLinkInitResponse,
)
def init_social_identity_link(
    provider: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    """Start an OAuth flow that may only link to the current Omlorix user."""
    from app.auth.identities import get_sign_in_methods, normalize_social_provider

    enforce_same_origin(request, db)
    require_locally_managed_account(user)
    require_sensitive_action_auth(user, token, db)
    normalized_provider = normalize_social_provider(provider)
    current_methods = get_sign_in_methods(user.id, db)
    current_provider = next(
        (
            item
            for item in current_methods["providers"]
            if item["provider"] == normalized_provider
        ),
        None,
    )
    if current_provider and current_provider["linked"]:
        raise HTTPException(status_code=409, detail="This provider is already connected.")
    social_provider = SocialAuthProviderFactory.get_provider(normalized_provider, db)
    if not social_provider.is_enabled():
        raise HTTPException(status_code=409, detail="This social login provider is not available.")
    try:
        assert_url_allowed(
            db,
            url=getattr(social_provider, "AUTHORIZATION_URL", None),
            feature=f"{normalized_provider.title()} social login linking",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc

    authentication = get_authentication(db, user.id, token, "access_token")
    if authentication is None:
        raise HTTPException(status_code=401, detail="The current session is no longer active.")

    state_token, state_hash = generate_oauth_state()
    nonce = generate_oauth_nonce()
    public_url = build_auth_redirect_base_url(db, request)
    redirect_uri = f"{public_url}/api/v1/auth/social/{normalized_provider}/callback"
    authorization_url = social_provider.get_authorization_url(
        redirect_uri,
        state_token,
        nonce,
    )

    secure_cookie = should_secure_auth_cookie(db, request)
    same_site = "none" if secure_cookie else "lax"
    for name, value in (
        ("social_state", state_hash),
        ("social_nonce", hashlib.sha256(nonce.encode()).hexdigest()),
    ):
        response.set_cookie(
            key=name,
            value=value,
            httponly=True,
            samesite=same_site,
            secure=secure_cookie,
            max_age=600,
        )
    set_social_link_context_cookie(
        response,
        db,
        request,
        user_id=user.id,
        authentication_id=authentication.id,
        provider=normalized_provider,
        state_hash=state_hash,
    )
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "SOCIAL_IDENTITY_LINK_STARTED",
        {"provider": normalized_provider},
    )
    return {"authorization_url": authorization_url}


@auth_router.delete(
    "/social/{provider}/link",
    response_model=SignInMethodsResponse,
)
def unlink_social_identity_route(
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
    token: str = Depends(verified_access_token),
):
    """Disconnect one provider after step-up and server-side lockout checks."""
    from app.auth.identities import normalize_social_provider, unlink_social_identity

    enforce_same_origin(request, db)
    require_locally_managed_account(user)
    require_sensitive_action_auth(user, token, db)
    normalized_provider = normalize_social_provider(provider)
    try:
        result = unlink_social_identity(user.id, normalized_provider, db)
    except HTTPException as exc:
        _audit_auth_security_event(
            db_log,
            db,
            request,
            user.id,
            "SOCIAL_IDENTITY_UNLINK_FAILED",
            {"provider": normalized_provider, "status_code": exc.status_code},
        )
        raise
    _audit_auth_security_event(
        db_log,
        db,
        request,
        user.id,
        "SOCIAL_IDENTITY_UNLINKED",
        {"provider": normalized_provider},
    )
    return result



# -------------------
# Social Login: Initiate OAuth Flow
# -------------------
@auth_router.post("/social/{provider}/init")
def init_social_login(
    provider: str,
    request: Request,
    response: Response,
    payload: SocialAuthInitRequest,
    db: Session = Depends(get_db),
):
    """Initiate OAuth flow for a social login provider."""
    logger.debug(
        "SOCIAL_OAUTH init_request provider=%s method=%s host=%s "
        "account_mode=%s replace_slot=%s return_url=%s",
        provider,
        request.method,
        request.headers.get("host", "<missing>"),
        payload.account_mode,
        payload.replace_slot,
        _oauth_debug_mask(payload.return_url),
    )
    social_provider = SocialAuthProviderFactory.get_provider(provider, db)
    
    if not social_provider.is_enabled():
        logger.warning("SOCIAL_OAUTH init_provider_disabled provider=%s", provider)
        raise HTTPException(status_code=400, detail=f"{provider.title()} login is not enabled")
    try:
        assert_url_allowed(
            db,
            url=getattr(social_provider, "AUTHORIZATION_URL", None),
            feature=f"{provider.title()} social login",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc
    
    state_token, state_hash = generate_oauth_state()
    nonce = generate_oauth_nonce()
    
    # Build the redirect URI - this should match what's configured in Google Cloud Console
    public_url = build_auth_redirect_base_url(db, request)
    redirect_uri = f"{public_url}/api/v1/auth/social/{provider}/callback"

    logger.debug(
        "SOCIAL_OAUTH init_redirect_resolved provider=%s public_url=%s redirect_uri=%s "
        "state_fp=%s state_hash_fp=%s nonce_fp=%s secure_cookie=%s samesite=%s",
        provider,
        public_url,
        redirect_uri,
        _oauth_debug_fingerprint(state_token),
        _oauth_debug_fingerprint(state_hash),
        _oauth_debug_fingerprint(nonce),
        should_secure_auth_cookie(db, request),
        "none" if should_secure_auth_cookie(db, request) else "lax",
    )
    
    authorization_url = social_provider.get_authorization_url(redirect_uri, state_token, nonce)
    secure_cookie = should_secure_auth_cookie(db, request)
    social_cookie_samesite = "none" if secure_cookie else "lax"
    response.set_cookie(
        key="social_state",
        value=state_hash,
        httponly=True,
        samesite=social_cookie_samesite,
        secure=secure_cookie,
        max_age=600,
    )
    response.set_cookie(
        key="social_nonce",
        value=hashlib.sha256(nonce.encode()).hexdigest(),
        httponly=True,
        samesite=social_cookie_samesite,
        secure=secure_cookie,
        max_age=600,
    )
    logger.debug(
        "SOCIAL_OAUTH init_cookies_set provider=%s state_cookie=%s nonce_cookie=%s "
        "flow_cookie=%s max_age=%s",
        provider,
        _oauth_debug_fingerprint(state_hash),
        _oauth_debug_fingerprint(hashlib.sha256(nonce.encode()).hexdigest()),
        SOCIAL_FLOW_COOKIE,
        600,
    )
    set_flow_context_cookie(
        response,
        db,
        request,
        cookie_name=SOCIAL_FLOW_COOKIE,
        account_mode=_normalize_account_mode(payload.account_mode),
        replace_slot=payload.replace_slot,
        return_url=payload.return_url,
        accept_terms_of_service=payload.accept_terms_of_service,
        terms_of_service_revision=payload.terms_of_service_revision,
    )

    logger.debug(
        "SOCIAL_OAUTH init_success provider=%s authorization_url_host=%s authorization_url_length=%s",
        provider,
        getattr(social_provider, "AUTHORIZATION_URL", "<missing>"),
        len(authorization_url),
    )
    
    return {
        "authorization_url": authorization_url,
        "state": state_token,
        "state_hash": state_hash,
        "nonce": nonce,
    }


# -------------------
# Social Login: OAuth Callback
# -------------------
def _delete_social_callback_cookies(response: Response) -> None:
    response.delete_cookie("social_state")
    response.delete_cookie("social_nonce")


def _social_link_result_redirect(
    provider: str,
    *,
    status_value: str,
    reason: str | None = None,
    native_state: str | None = None,
    code: str | None = None,
) -> RedirectResponse:
    """Return to settings with small, non-sensitive link result codes."""
    if native_state:
        return RedirectResponse(
            url=native_callback_url(
                path="link",
                state=native_state,
                code=code,
                provider=provider,
                status=status_value,
                reason=reason,
            ),
            status_code=302,
        )
    parameters = {
        "social_link": status_value,
        "provider": str(provider or "").strip().lower(),
    }
    if reason:
        parameters["reason"] = reason
    return RedirectResponse(url=f"/index?{urlencode(parameters)}", status_code=302)


async def _handle_social_login_callback(
    provider: str,
    request: Request,
    response: Response,
    code: str | None,
    state: str | None,
    db: Session,
    db_log: Session,
    *,
    error: str | None = None,
    apple_user_payload: str | None = None,
):
    """Handle either a sign-in callback or an authenticated identity link."""
    link_cookie_present = bool(request.cookies.get(SOCIAL_LINK_FLOW_COOKIE))
    stored_state_hash = request.cookies.get("social_state")
    link_context = read_social_link_context_cookie(request, db)
    flow_context = read_flow_context_cookie(request, db, cookie_name=SOCIAL_FLOW_COOKIE)
    if link_context and (
        not stored_state_hash
        or not hmac.compare_digest(link_context["state_hash"], stored_state_hash)
    ):
        link_context = None
    stale_link_cookie = link_cookie_present and link_context is None

    def finish_sign_in_flow(result):
        """Clear callback cookies, including an unrelated stale link context."""
        if hasattr(result, "delete_cookie"):
            _delete_social_callback_cookies(result)
            if stale_link_cookie:
                clear_social_link_context_cookie(result, db, request)
        return result

    def finish_native_sign_in_failure(
        reason: str,
        *,
        source_response: Response | None = None,
    ) -> RedirectResponse | None:
        redirect = _native_federated_failure_redirect(
            db,
            request,
            flow_context=flow_context,
            cookie_name=SOCIAL_FLOW_COOKIE,
            kind="social",
            provider=provider,
            reason=reason,
            source_response=source_response,
        )
        if redirect is not None:
            _delete_social_callback_cookies(redirect)
            if stale_link_cookie:
                clear_social_link_context_cookie(redirect, db, request)
        return redirect

    def finish_link_flow(
        status_value: str,
        reason: str | None = None,
        *,
        code: str | None = None,
    ) -> RedirectResponse:
        """Clear every one-time cookie and return to the security settings UI."""
        if status_value == "error" and link_context:
            _audit_auth_security_event(
                db_log,
                db,
                request,
                str(link_context["user_id"]),
                (
                    "SOCIAL_IDENTITY_LINK_CANCELLED"
                    if reason == "cancelled"
                    else "SOCIAL_IDENTITY_LINK_FAILED"
                ),
                {"provider": str(provider).lower(), "reason": reason or "failed"},
            )
        redirect = _social_link_result_redirect(
            provider,
            status_value=status_value,
            reason=reason,
            native_state=(str(link_context.get("native_state") or "") if link_context else None),
            code=code,
        )
        _delete_social_callback_cookies(redirect)
        clear_social_link_context_cookie(redirect, db, request)
        return redirect

    try:
        logger.debug(
            "SOCIAL_OAUTH callback_enter provider=%s method=%s host=%s "
            "forwarded_proto=%s code_present=%s code_length=%s code_fp=%s state_present=%s "
            "state_length=%s state_fp=%s provider_error=%s state_cookie_present=%s "
            "state_cookie_fp=%s nonce_cookie_present=%s nonce_cookie_fp=%s",
            provider,
            request.method,
            request.headers.get("host", "<missing>"),
            request.headers.get("x-forwarded-proto", "<missing>"),
            bool(code),
            len(str(code or "")),
            _oauth_debug_fingerprint(code),
            bool(state),
            len(str(state or "")),
            _oauth_debug_fingerprint(state),
            error or "<none>",
            bool(request.cookies.get("social_state")),
            _oauth_debug_fingerprint(request.cookies.get("social_state")),
            bool(request.cookies.get("social_nonce")),
            _oauth_debug_fingerprint(request.cookies.get("social_nonce")),
        )
        if stale_link_cookie:
            logger.warning("SOCIAL_OAUTH link_context_stale provider=%s", provider)

        if error:
            logger.debug(
                "SOCIAL_OAUTH callback_provider_error provider=%s error=%s",
                provider,
                error,
            )
            if link_context:
                return finish_link_flow("error", "cancelled")
            native_redirect = finish_native_sign_in_failure("cancelled")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(url="/login?error=social_login_failed", status_code=302)
            return finish_sign_in_flow(redirect)

        logger.debug("SOCIAL_OAUTH callback_provider_start provider=%s", provider)
        social_provider = SocialAuthProviderFactory.get_provider(provider, db)
        try:
            assert_url_allowed(
                db,
                url=getattr(social_provider, "TOKEN_URL", None) or getattr(social_provider, "AUTHORIZATION_URL", None),
                feature=f"{provider.title()} social login callback",
            )
        except OutboundRequestBlockedError as exc:
            raise exc.to_http_exception() from exc
        
        if not social_provider.is_enabled():
            logger.warning("SOCIAL_OAUTH callback_provider_disabled provider=%s", provider)
            if link_context:
                return finish_link_flow("error", "unavailable")
            native_redirect = finish_native_sign_in_failure("unavailable")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(url="/login?error=provider_disabled", status_code=302)
            return finish_sign_in_flow(redirect)

        if not code or not state or not stored_state_hash:
            logger.warning(
                "SOCIAL_OAUTH callback_state_missing code_present=%s state_present=%s state_cookie_present=%s",
                bool(code),
                bool(state),
                bool(stored_state_hash),
            )
            if link_context:
                return finish_link_flow("error", "invalid_flow")
            native_redirect = finish_native_sign_in_failure("invalid_flow")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(url="/login?error=social_state_missing", status_code=302)
            return finish_sign_in_flow(redirect)

        computed_state_hash = hashlib.sha256(state.encode()).hexdigest()
        logger.debug(
            "SOCIAL_OAUTH callback_state_compare state_fp=%s computed_hash_fp=%s stored_hash_fp=%s match=%s",
            _oauth_debug_fingerprint(state),
            _oauth_debug_fingerprint(computed_state_hash),
            _oauth_debug_fingerprint(stored_state_hash),
            hmac.compare_digest(computed_state_hash, stored_state_hash),
        )
        if not hmac.compare_digest(computed_state_hash, stored_state_hash):
            logger.warning("SOCIAL_OAUTH callback_state_mismatch possible_csrf=true")
            if link_context:
                return finish_link_flow("error", "invalid_flow")
            native_redirect = finish_native_sign_in_failure("invalid_flow")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(url="/login?error=social_state_invalid", status_code=302)
            return finish_sign_in_flow(redirect)
        
        # Build the redirect URI
        public_url = build_auth_redirect_base_url(db, request)
        redirect_uri = f"{public_url}/api/v1/auth/social/{provider}/callback"
        logger.debug(
            "SOCIAL_OAUTH callback_redirect_resolved provider=%s public_url=%s redirect_uri=%s",
            provider,
            public_url,
            redirect_uri,
        )

        if link_context:
            from app.auth.identities import (
                link_social_identity,
                validate_social_link_session,
                validated_social_link_identity_claims,
            )

            # Re-check the referenced session and its step-up timestamp at the
            # callback boundary. The access token itself never leaves Omlorix.
            user_id = validate_social_link_session(link_context, provider, db)
            user_info = await verified_social_user_info_from_callback(
                provider,
                code,
                redirect_uri,
                db,
                expected_nonce_hash=request.cookies.get("social_nonce"),
                apple_user_payload=apple_user_payload,
            )
            native_state = str(link_context.get("native_state") or "")
            if native_state:
                identity_claims = validated_social_link_identity_claims(provider, user_info, db)
                exchange_code = create_native_auth_grant(
                    db,
                    purpose="social_link_exchange",
                    provider=provider,
                    user_id=user_id,
                    authentication_id=str(link_context.get("authentication_id") or ""),
                    code_challenge=str(link_context.get("native_code_challenge") or ""),
                    state=native_state,
                    identity_claims=identity_claims,
                    ttl_seconds=NATIVE_EXCHANGE_TTL_SECONDS,
                )
                return finish_link_flow("pending", code=exchange_code)
            link_social_identity(user_id, provider, user_info, db)
            _audit_auth_security_event(
                db_log,
                db,
                request,
                user_id,
                "SOCIAL_IDENTITY_LINKED",
                {"provider": str(provider).lower()},
            )
            return finish_link_flow("success")
        
        result = await social_login_callback(
            provider=provider,
            code=code,
            state=state,
            redirect_uri=redirect_uri,
            request=request,
            response=response,
            db=db,
            db_log=db_log,
            expected_nonce_hash=request.cookies.get("social_nonce"),
            apple_user_payload=apple_user_payload,
        )
        
        finish_sign_in_flow(result)
        failure_reason = _result_failure_reason(result)
        if failure_reason:
            native_redirect = finish_native_sign_in_failure(
                failure_reason,
                source_response=result if isinstance(result, Response) else None,
            )
            if native_redirect is not None:
                return native_redirect
        logger.debug(
            "SOCIAL_OAUTH callback_business_result provider=%s result_type=%s has_delete_cookie=%s",
            provider,
            type(result).__name__,
            hasattr(result, "delete_cookie"),
        )
        return result
    except HTTPException as http_exc:
        logger.warning(
            "SOCIAL_OAUTH callback_http_exception provider=%s status_code=%s detail=%s",
            provider,
            http_exc.status_code,
            str(http_exc.detail)[:200],
        )
        if link_cookie_present:
            if link_context:
                reason = "conflict" if http_exc.status_code == 409 else "failed"
                return finish_link_flow("error", reason)
            clear_social_link_context_cookie(response, db, request)
        native_redirect = finish_native_sign_in_failure("failed")
        if native_redirect is not None:
            return native_redirect
        _delete_social_callback_cookies(response)
        raise http_exc
    except Exception as exc:
        logger.exception(
            "SOCIAL_OAUTH callback_unhandled_exception provider=%s error_type=%s error=%s",
            provider,
            type(exc).__name__,
            str(exc)[:200],
        )
        if link_context:
            return finish_link_flow("error", "failed")
        native_redirect = finish_native_sign_in_failure("failed")
        if native_redirect is not None:
            return native_redirect
        redirect = RedirectResponse(url="/login?error=social_login_failed", status_code=302)
        return finish_sign_in_flow(redirect)


@auth_router.get("/social/{provider}/callback")
async def social_login_callback_route(
    provider: str,
    request: Request,
    response: Response,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Handle OAuth query callbacks from social login providers."""
    return await _handle_social_login_callback(
        provider=provider,
        request=request,
        response=response,
        code=code,
        state=state,
        db=db,
        db_log=db_log,
        error=error,
    )


@auth_router.post("/social/{provider}/callback")
async def social_login_form_post_callback_route(
    provider: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Handle OAuth form_post callbacks used by Apple and Slack OpenID."""
    body = (await request.body()).decode("utf-8", errors="replace")
    form_data = {
        key: values[-1] if values else ""
        for key, values in parse_qs(body, keep_blank_values=True).items()
    }
    logger.debug(
        "SOCIAL_OAUTH form_post_received provider=%s body_length=%s fields=%s "
        "code_present=%s code_length=%s state_present=%s state_length=%s error=%s "
        "id_token_present=%s user_payload_present=%s",
        provider,
        len(body),
        sorted(form_data.keys()),
        bool(form_data.get("code")),
        len(str(form_data.get("code") or "")),
        bool(form_data.get("state")),
        len(str(form_data.get("state") or "")),
        form_data.get("error") or "<none>",
        bool(form_data.get("id_token")),
        bool(form_data.get("user")),
    )
    return await _handle_social_login_callback(
        provider=provider,
        request=request,
        response=response,
        code=str(form_data.get("code") or ""),
        state=str(form_data.get("state") or ""),
        db=db,
        db_log=db_log,
        error=str(form_data.get("error") or "") or None,
        apple_user_payload=(
            str(form_data.get("user") or "") if provider.lower() == "apple" else None
        ),
    )


# -------------------
# Social Login: Complete with 2FA
# -------------------
@auth_router.post("/social/{provider}/complete")
async def complete_social_login(
    provider: str,
    request: Request,
    response: Response,
    payload: SocialAuthCallbackRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Complete social login flow, including 2FA verification if required."""
    from app.auth.utils import complete_social_login_with_2fa

    enforce_same_origin(request, db)
    
    result = await complete_social_login_with_2fa(
        provider=provider,
        social_token=payload.social_token,
        otp_code=payload.otp_code,
        otp_type=payload.otp_type,
        otp_action=payload.otp_action,
        otp_destination=payload.otp_destination,
        request=request,
        response=response,
        db=db,
        db_log=db_log,
    )
    
    return result


@auth_router.post("/social/pending-terms/confirm")
async def confirm_social_pending_terms(
    request: Request,
    response: Response,
    payload: FederatedTermsConfirmRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Create a pending social-signup account after current terms are accepted."""
    enforce_same_origin(request, db)
    return await confirm_pending_social_terms_signup(
        request=request,
        response=response,
        db=db,
        db_log=db_log,
        terms_payload=payload,
    )


@auth_router.post("/social/pending-terms/cancel")
async def cancel_social_pending_terms(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Cancel a pending social signup that is waiting for terms acceptance."""
    enforce_same_origin(request, db)
    return cancel_pending_social_terms_signup(response=response)



# -------------------
# Social Login: Exchange Auth Code for Token
# -------------------
@auth_router.post("/social/exchange")
async def exchange_social_auth_code(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Exchange a one-time social login auth code for an access token.
    
    Security: Auth codes are one-time use and expire after 5 minutes.
    This prevents tokens from being exposed in URL query parameters.
    """
    from app.users.init import get_user_setting_value
    from datetime import datetime, timezone
    
    try:
        body = await request.json()
    except Exception:
        body = {}
    auth_code = body.get("code") or request.cookies.get("social_auth_code")
    
    if not auth_code:
        response.delete_cookie("social_auth_code")
        raise HTTPException(status_code=400, detail="Missing auth code")

    enforce_same_origin(request, db)
    
    user = _find_user_by_pending_social_auth_code(db, auth_code)
    
    if not user:
        response.delete_cookie("social_auth_code")
        logger.warning("Social auth code exchange failed: invalid code")
        raise HTTPException(status_code=400, detail="Invalid or expired auth code")
    try:
        require_locally_managed_account(user)
    except HTTPException:
        _clear_social_auth_exchange_state(
            user.id,
            db,
            raw_token=auth_code,
        )
        response.delete_cookie("social_auth_code")
        raise
    
    # Check expiration
    expires_str = get_user_setting_value(
        user.id,
        "social_login",
        "pending_auth_code_expires",
        db,
        commit=False,
    )
    if expires_str:
        try:
            expires = datetime.fromisoformat(expires_str)
            if datetime.now(timezone.utc) > expires:
                # Clear expired code
                _clear_social_auth_exchange_state(user.id, db)
                response.delete_cookie("social_auth_code")
                logger.warning("Social auth code exchange failed: code expired")
                raise HTTPException(status_code=400, detail="Auth code expired")
        except ValueError:
            _clear_social_auth_exchange_state(user.id, db)
            response.delete_cookie("social_auth_code")
            raise HTTPException(status_code=400, detail="Invalid or expired auth code")
    
    flow_context = read_flow_context_cookie(request, db, cookie_name=SOCIAL_FLOW_COOKIE)
    # Consume the indexed action before issuing a session.  If session
    # creation later fails, the user can restart the provider flow; the same
    # one-time code must never mint two sessions concurrently.
    if not _clear_social_auth_exchange_state(
        user.id,
        db,
        raw_token=auth_code,
    ):
        response.delete_cookie("social_auth_code")
        raise HTTPException(status_code=400, detail="Invalid or expired auth code")
    issued = _issue_authenticated_session(
        db=db,
        db_log=db_log,
        request=request,
        response=response,
        user=user,
        log_event="social_signin",
        success_message="Social login successful",
        account_mode=flow_context.get("account_mode", "primary"),
        replace_slot=flow_context.get("replace_slot"),
    )

    clear_flow_context_cookie(response, db, request, cookie_name=SOCIAL_FLOW_COOKIE)
    
    response.delete_cookie("social_auth_code")
    logger.info(f"Social auth code exchanged successfully for user {user.id}")

    return issued



# -------------------
# Enterprise SSO: Get Available Providers
# -------------------
@auth_router.get("/sso/providers")
def get_sso_providers(db: Session = Depends(get_db)):
    """Get list of enabled enterprise SSO providers."""
    from app.auth.enterprise_sso import EnterpriseSSOProviderFactory
    providers = EnterpriseSSOProviderFactory.get_enabled_providers(db)
    return {"providers": providers}


@auth_router.post("/native/sso/{provider_type}/init", response_model=SocialLinkInitResponse)
def init_native_sso_login(
    provider_type: EnterpriseSSOProviderType,
    payload: NativeFederatedInitRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a one-time system-browser ticket for enterprise sign-in."""
    from app.auth.enterprise_sso import EnterpriseSSOProviderFactory

    get_native_callback_origin()
    normalized_provider = str(provider_type or "").strip().lower()
    provider = EnterpriseSSOProviderFactory.get_provider(normalized_provider, db)
    if not provider.is_enabled():
        raise HTTPException(status_code=400, detail=f"{normalized_provider.upper()} SSO is not enabled")
    ticket = create_native_auth_grant(
        db,
        purpose="sso_start",
        provider=normalized_provider,
        code_challenge=payload.code_challenge,
        state=payload.state,
        account_mode=payload.account_mode,
        replace_slot=payload.replace_slot,
        accepts_terms_of_service=payload.accept_terms_of_service,
        terms_of_service_revision=payload.terms_of_service_revision,
    )
    return {
        "authorization_url": _native_browser_start_url(
            db,
            request,
            path=f"api/v1/auth/native/sso/{normalized_provider}/start",
            ticket=ticket,
            native_state=payload.state,
        )
    }


@auth_router.get("/native/sso/{provider_type}/start")
def start_native_sso_login(
    provider_type: EnterpriseSSOProviderType,
    request: Request,
    ticket: str = Query(..., min_length=32, max_length=512),
    native_state: str = Query(..., min_length=43, max_length=128),
    db: Session = Depends(get_db),
):
    """Transfer an SSO ticket into the provider-facing browser session."""
    from app.auth.enterprise_sso import EnterpriseSSOProviderFactory

    normalized_provider = str(provider_type or "").strip().lower()
    grant = consume_native_auth_grant(
        db,
        ticket,
        expected_purposes={"sso_start"},
        state=native_state,
    )
    if grant.provider != normalized_provider:
        raise HTTPException(status_code=400, detail="Native authentication provider mismatch.")
    sso_provider = EnterpriseSSOProviderFactory.get_provider(normalized_provider, db)
    if not sso_provider.is_enabled():
        raise HTTPException(status_code=400, detail=f"{normalized_provider.upper()} SSO is not enabled")
    oauth_state, state_hash = generate_oauth_state()
    public_url = build_auth_redirect_base_url(db, request)
    redirect_uri = f"{public_url}/api/v1/auth/sso/{normalized_provider}/callback"
    authorization_url, security_data = sso_provider.get_authorization_url(redirect_uri, oauth_state)
    try:
        assert_url_allowed(
            db,
            url=authorization_url,
            feature=f"{normalized_provider.upper()} native SSO",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc
    redirect = RedirectResponse(url=authorization_url, status_code=302)
    secure_cookie = should_secure_auth_cookie(db, request)
    same_site = "none" if secure_cookie else "lax"
    redirect.set_cookie(
        key="sso_state",
        value=state_hash,
        httponly=True,
        samesite=same_site,
        secure=secure_cookie,
        max_age=600,
    )
    redirect.set_cookie(
        key="sso_security",
        value=security_data.to_json(),
        httponly=True,
        samesite=same_site,
        secure=secure_cookie,
        max_age=600,
    )
    set_flow_context_cookie(
        redirect,
        db,
        request,
        cookie_name=SSO_FLOW_COOKIE,
        account_mode=grant.account_mode,
        replace_slot=grant.replace_slot,
        return_url="",
        accept_terms_of_service=grant.accepts_terms_of_service,
        terms_of_service_revision=grant.terms_of_service_revision,
        native_auth=True,
        native_kind="sso",
        native_provider=normalized_provider,
        native_code_challenge=grant.code_challenge,
        native_state=native_state,
    )
    return redirect


@auth_router.post("/native/exchange")
def exchange_native_auth_code(
    payload: NativeAuthExchangeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Exchange a one-time app callback code after proving the PKCE verifier."""
    from app.users.models import get_user

    enforce_same_origin(request, db)
    grant = consume_native_auth_grant(
        db,
        payload.code,
        expected_purposes={f"{payload.kind}_exchange"},
        state=payload.state,
        code_verifier=payload.code_verifier,
    )
    if not grant.user_id:
        raise HTTPException(status_code=400, detail="Native authentication grant has no user.")
    user = get_user(db, user_id=grant.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Native authentication user is unavailable.")
    if payload.kind == "social":
        require_locally_managed_account(user)

    issued = _issue_authenticated_session(
        db=db,
        db_log=db_log,
        request=request,
        response=response,
        user=user,
        log_event=f"{payload.kind}_signin",
        success_message=f"Native {payload.kind.upper()} login successful",
        account_mode=grant.account_mode,
        replace_slot=grant.replace_slot,
        twofa_satisfied=grant.twofa_satisfied,
    )
    return {
        "status": "success" if issued.get("session_authenticated") else issued.get("status", "error"),
        **issued,
    }



# -------------------
# Enterprise SSO: Initiate SSO Flow
# -------------------
@auth_router.post("/sso/{provider_type}/init")
def init_sso_login(
    provider_type: EnterpriseSSOProviderType,
    request: Request,
    response: Response,
    payload: SSOAuthInitRequest,
    db: Session = Depends(get_db),
):
    """Initiate SSO flow for enterprise authentication."""
    from app.auth.enterprise_sso import EnterpriseSSOProviderFactory
    from app.auth.social import generate_oauth_state

    if payload.provider_type != provider_type:
        raise HTTPException(status_code=400, detail="SSO provider mismatch")
    
    sso_provider = EnterpriseSSOProviderFactory.get_provider(provider_type, db)
    
    if not sso_provider.is_enabled():
        raise HTTPException(status_code=400, detail=f"{provider_type.upper()} SSO is not enabled")

    state_token, state_hash = generate_oauth_state()
    
    # Build the redirect URI
    public_url = build_auth_redirect_base_url(db, request)
    redirect_uri = f"{public_url}/api/v1/auth/sso/{provider_type}/callback"
    
    authorization_url, security_data = sso_provider.get_authorization_url(redirect_uri, state_token)
    try:
        assert_url_allowed(
            db,
            url=authorization_url,
            feature=f"{provider_type.upper()} SSO",
        )
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc
    # Carry one safe support reference through the HttpOnly flow cookie so every
    # callback failure can be correlated without exposing provider data.
    from app.auth.diagnostics import new_auth_reference
    security_data.correlation_id = new_auth_reference()
    
    # Store state hash and security data in secure HTTP-only cookies for CSRF/replay protection
    secure_cookie = should_secure_auth_cookie(db, request)
    sso_cookie_samesite = "none" if secure_cookie else "lax"
    response.set_cookie(
        key="sso_state",
        value=state_hash,
        httponly=True,
        samesite=sso_cookie_samesite,
        secure=secure_cookie,
        max_age=600,  # 10 minutes expiry
    )
    
    # Store security data (nonce for OIDC, request_id for SAML) for validation in callback
    response.set_cookie(
        key="sso_security",
        value=security_data.to_json(),
        httponly=True,
        samesite=sso_cookie_samesite,
        secure=secure_cookie,
        max_age=600,  # 10 minutes expiry
    )
    set_flow_context_cookie(
        response,
        db,
        request,
        cookie_name=SSO_FLOW_COOKIE,
        account_mode=_normalize_account_mode(payload.account_mode),
        replace_slot=payload.replace_slot,
        return_url=payload.return_url,
        accept_terms_of_service=payload.accept_terms_of_service,
        terms_of_service_revision=payload.terms_of_service_revision,
    )
    
    return {
        "authorization_url": authorization_url,
        "state": state_token,
        "state_hash": state_hash,
    }



# -------------------
# Enterprise SSO: Callback Handler
# -------------------
@auth_router.api_route("/sso/{provider_type}/callback", methods=["GET", "POST"])
async def sso_callback_route(
    provider_type: EnterpriseSSOProviderType,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Handle SSO callback from enterprise identity provider."""
    from app.auth.diagnostics import build_sso_failure_url
    from app.auth.utils import sso_login_callback
    import hashlib

    flow_context = read_flow_context_cookie(request, db, cookie_name=SSO_FLOW_COOKIE)

    def finish_native_sso_failure(
        reason: str,
        *,
        source_response: Response | None = None,
    ) -> RedirectResponse | None:
        redirect = _native_federated_failure_redirect(
            db,
            request,
            flow_context=flow_context,
            cookie_name=SSO_FLOW_COOKIE,
            kind="sso",
            provider=provider_type,
            reason=reason,
            source_response=source_response,
        )
        if redirect is not None:
            redirect.delete_cookie("sso_state")
            redirect.delete_cookie("sso_security")
        return redirect
    
    try:
        logger.info(f"SSO callback started for provider: {provider_type}")
        
        # Get request data (supports both GET and POST)
        if request.method == "POST":
            form_data = await request.form()
            request_data = dict(form_data)
        else:
            request_data = dict(request.query_params)
        
        # Validate state parameter for CSRF protection
        state_token = request_data.get("state") or request_data.get("RelayState")
        stored_state_hash = request.cookies.get("sso_state")
        
        if not state_token or not stored_state_hash:
            logger.warning("SSO callback missing state or state cookie")
            native_redirect = finish_native_sso_failure("invalid_flow")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(
                url=build_sso_failure_url("sso_state_missing", None),
                status_code=302,
            )
            redirect.delete_cookie("sso_state")
            redirect.delete_cookie("sso_security")
            return redirect
        
        computed_hash = hashlib.sha256(state_token.encode()).hexdigest()
        if not hmac.compare_digest(computed_hash, stored_state_hash):
            logger.warning("SSO callback state mismatch - possible CSRF attack")
            native_redirect = finish_native_sso_failure("invalid_flow")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(
                url=build_sso_failure_url("sso_state_invalid", None),
                status_code=302,
            )
            redirect.delete_cookie("sso_state")
            redirect.delete_cookie("sso_security")
            return redirect
        
        # Retrieve and parse security data (nonce/request_id) for replay protection
        stored_security_json = request.cookies.get("sso_security")
        security_data = SSOSecurityData.from_json(stored_security_json) if stored_security_json else None
        
        if not security_data or (not security_data.nonce and not security_data.request_id):
            logger.warning("SSO callback missing security data")
            native_redirect = finish_native_sso_failure("invalid_flow")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(
                url=build_sso_failure_url("sso_security_missing", None),
                status_code=302,
            )
            redirect.delete_cookie("sso_state")
            redirect.delete_cookie("sso_security")
            return redirect
        
        if provider_type.lower() == "oidc" and not security_data.nonce:
            logger.warning("SSO callback missing nonce")
            native_redirect = finish_native_sso_failure("invalid_flow")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(
                url=build_sso_failure_url("sso_security_missing", None),
                status_code=302,
            )
            redirect.delete_cookie("sso_state")
            redirect.delete_cookie("sso_security")
            return redirect
        
        if provider_type.lower() == "saml" and not security_data.request_id:
            logger.warning("SSO callback missing SAML request ID")
            native_redirect = finish_native_sso_failure("invalid_flow")
            if native_redirect is not None:
                return native_redirect
            redirect = RedirectResponse(
                url=build_sso_failure_url("sso_security_missing", None),
                status_code=302,
            )
            redirect.delete_cookie("sso_state")
            redirect.delete_cookie("sso_security")
            return redirect
        
        # Build the redirect URI
        public_url = build_auth_redirect_base_url(db, request)
        redirect_uri = f"{public_url}/api/v1/auth/sso/{provider_type}/callback"
        result = await sso_login_callback(
            provider_type=provider_type,
            request_data=request_data,
            redirect_uri=redirect_uri,
            request=request,
            response=response,
            db=db,
            db_log=db_log,
            security_data=security_data,
            upstream_request=request,
        )
        
        if isinstance(result, Response):
            result.delete_cookie("sso_state")
            result.delete_cookie("sso_security")
        failure_reason = _result_failure_reason(result)
        if failure_reason:
            # Expected SSO rejections are useful to administrators too, but they
            # should not trigger infrastructure alerts.  Add the same safe
            # reference to the redirect and persist a warning-level diagnostic.
            raw_error = _redirect_error_key(result) or failure_reason
            location = result.headers.get("location", "") if isinstance(result, Response) else ""
            if isinstance(result, Response) and "reference=" not in location:
                from app.auth.diagnostics import (
                    classify_sso_rejection,
                    new_auth_reference,
                    record_sso_diagnostic,
                )
                # Cookies created before diagnostic correlation was introduced
                # have no reference. Generate one here so the visible support
                # token and the persisted diagnostic always remain linkable.
                reference = security_data.correlation_id or new_auth_reference()
                security_data.correlation_id = reference
                error_code, stage = classify_sso_rejection(raw_error)
                record_sso_diagnostic(
                    db_log,
                    reference=reference,
                    provider=provider_type,
                    error_code=error_code,
                    stage=stage,
                    user_agent=request.headers.get("user-agent"),
                    ip_address=request.client.host if request.client else None,
                    status="warning",
                    notify_on_repeat=False,
                )
                parsed_location = urlsplit(location)
                query = dict(parse_qsl(parsed_location.query, keep_blank_values=True))
                query.update({"auth_flow": "sso", "reference": reference})
                result.headers["location"] = urlunsplit((
                    parsed_location.scheme,
                    parsed_location.netloc,
                    parsed_location.path,
                    urlencode(query),
                    parsed_location.fragment,
                ))
            logger.warning(
                "SSO callback completed with rejection for provider=%s reason=%s reference=%s",
                provider_type,
                failure_reason,
                security_data.correlation_id or "-",
            )
            native_redirect = finish_native_sso_failure(
                failure_reason,
                source_response=result if isinstance(result, Response) else None,
            )
            if native_redirect is not None:
                return native_redirect
        else:
            logger.info(
                "SSO callback completed successfully for provider=%s reference=%s",
                provider_type,
                security_data.correlation_id or "-",
            )
        return result
    except Exception as e:
        logger.exception(f"SSO callback error for provider {provider_type}")
        native_redirect = finish_native_sso_failure("failed")
        if native_redirect is not None:
            return native_redirect
        from app.auth.diagnostics import (
            classify_sso_exception,
            new_auth_reference,
            record_sso_diagnostic,
        )
        reference = getattr(locals().get("security_data"), "correlation_id", None) or new_auth_reference()
        error_code, stage = classify_sso_exception(e)
        record_sso_diagnostic(
            db_log,
            reference=reference,
            provider=provider_type,
            error_code=error_code,
            stage=stage,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
        error_response = RedirectResponse(
            url=build_sso_failure_url("sso_login_failed", reference),
            status_code=302,
        )
        error_response.delete_cookie("sso_state")
        error_response.delete_cookie("sso_security")
        return error_response



# -------------------
# Enterprise SSO: Complete with 2FA
# -------------------
@auth_router.post("/sso/{provider_type}/complete")
async def complete_sso_login(
    provider_type: EnterpriseSSOProviderType,
    request: Request,
    response: Response,
    payload: SSOAuthCallbackRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Complete SSO login flow, including 2FA verification if required."""
    from app.auth.utils import complete_sso_login_with_2fa

    enforce_same_origin(request, db)
    
    result = await complete_sso_login_with_2fa(
        provider_type=provider_type,
        sso_token=payload.sso_token,
        otp_code=payload.otp_code,
        otp_type=payload.otp_type,
        otp_action=payload.otp_action,
        otp_destination=payload.otp_destination,
        request=request,
        response=response,
        db=db,
        db_log=db_log,
    )
    
    return result


@auth_router.post("/sso/pending-terms/confirm")
async def confirm_sso_pending_terms(
    request: Request,
    response: Response,
    payload: FederatedTermsConfirmRequest,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Create a pending SSO/JIT account after current terms are accepted."""
    enforce_same_origin(request, db)
    return await confirm_pending_sso_terms_signup(
        request=request,
        response=response,
        db=db,
        db_log=db_log,
        terms_payload=payload,
    )


@auth_router.post("/sso/pending-terms/cancel")
async def cancel_sso_pending_terms(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Cancel a pending SSO/JIT signup that is waiting for terms acceptance."""
    enforce_same_origin(request, db)
    return cancel_pending_sso_terms_signup(response=response)


# -------------------
# Enterprise SSO: Exchange Auth Code for Token
# -------------------
@auth_router.post("/sso/exchange")
async def exchange_sso_auth_code(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    """Exchange a one-time SSO auth code for an access token.
    
    Security: Auth codes are one-time use and expire after 5 minutes.
    This prevents tokens from being exposed in URL query parameters.
    """
    from app.users.init import get_user_setting_value
    from datetime import datetime, timezone
    
    try:
        body = await request.json()
    except Exception:
        body = {}
    auth_code = body.get("code") or request.cookies.get("sso_auth_code")
    
    if not auth_code:
        response.delete_cookie("sso_auth_code")
        raise HTTPException(status_code=400, detail="Missing auth code")

    enforce_same_origin(request, db)
    
    user = _find_user_by_pending_sso_auth_code(db, auth_code)
    
    if not user:
        response.delete_cookie("sso_auth_code")
        logger.warning("SSO auth code exchange failed: invalid code")
        raise HTTPException(status_code=400, detail="Invalid or expired auth code")
    
    # Check expiration
    expires_str = get_user_setting_value(
        user.id,
        "sso_login",
        "pending_auth_code_expires",
        db,
        commit=False,
    )
    if expires_str:
        try:
            expires = datetime.fromisoformat(expires_str)
            if datetime.now(timezone.utc) > expires:
                # Clear expired code
                _clear_sso_auth_exchange_state(user.id, db)
                response.delete_cookie("sso_auth_code")
                logger.warning("SSO auth code exchange failed: code expired")
                raise HTTPException(status_code=400, detail="Auth code expired")
        except ValueError:
            _clear_sso_auth_exchange_state(user.id, db)
            response.delete_cookie("sso_auth_code")
            raise HTTPException(status_code=400, detail="Invalid or expired auth code")
    
    flow_context = read_flow_context_cookie(request, db, cookie_name=SSO_FLOW_COOKIE)
    # Consume the indexed action before issuing a session.  Failed session
    # creation requires a fresh SSO flow instead of leaving replay authority.
    if not _clear_sso_auth_exchange_state(
        user.id,
        db,
        raw_token=auth_code,
    ):
        response.delete_cookie("sso_auth_code")
        raise HTTPException(status_code=400, detail="Invalid or expired auth code")
    issued = _issue_authenticated_session(
        db=db,
        db_log=db_log,
        request=request,
        response=response,
        user=user,
        log_event="sso_signin",
        success_message="SSO login successful",
        account_mode=flow_context.get("account_mode", "primary"),
        replace_slot=flow_context.get("replace_slot"),
    )

    clear_flow_context_cookie(response, db, request, cookie_name=SSO_FLOW_COOKIE)
    
    response.delete_cookie("sso_auth_code")
    logger.info(f"SSO auth code exchanged successfully for user {user.id}")

    return issued
