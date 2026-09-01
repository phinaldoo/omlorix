import logging

from fastapi import APIRouter, Depends, UploadFile, File, Request
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.settings.schemas import (
    ServerSetupRequest,
    ServerSetupResponse,
)
from app.settings.utils import (
    get_chat_setup,
    get_branding_assets_overview,
    get_login_settings, 
    upload_logo, 
    get_logo,
    upload_icon,
    get_icon,
    get_site_manifest,
    complete_server_setup,
    upload_login_background,
    get_login_background,
    delete_login_background,
    upload_ldap_ca_certificate,
    get_ldap_ca_certificate_status,
    delete_ldap_ca_certificate,
)

settings_router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
logger = logging.getLogger(__name__)


def _audit_settings_event(
    db_log: Session,
    request: Request,
    admin_user,
    action: str,
    details: dict | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category="settings",
    )



# -------------------
# Chat Setup
# -------------------
@settings_router.get("/chat/setup")
def chat_setup_route(user = Depends(verified_user), db: Session = Depends(get_db)):
    """Get chat setup for the current user."""
    return get_chat_setup(user.id, db)



# -------------------
# Login Setup
# -------------------
@settings_router.get("/login/setup")
def get_login_settings_route(db: Session = Depends(get_db)):
    """Return the non-sensitive configuration needed to render the login page.

    This is intentionally a public bootstrap endpoint. Browser same-origin GET
    requests do not reliably include ``Origin`` and may omit ``Referer`` under
    a privacy policy, so applying the cookie-auth origin guard here can lock
    legitimate users out before they can sign in. State-changing authentication
    routes retain their existing same-origin enforcement.
    """
    return get_login_settings(db)



# -------------------
# Site Manifest for PWA
# -------------------
@settings_router.get("/site.webmanifest", include_in_schema=False)
def get_site_manifest_route(db: Session = Depends(get_db)):
    """Serve the PWA web app manifest."""
    return get_site_manifest(db)



# -------------------
# Upload logo
# -------------------
@settings_router.post("/logo/upload")
def upload_logo_route(
    request: Request,
    logo: UploadFile = File(...),
    theme: str = "light",
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Upload a logo image for a theme."""
    result = upload_logo(logo, theme)
    _audit_settings_event(
        db_log,
        request,
        admin_user,
        "BRANDING_LOGO_UPLOADED",
        {"theme": theme, "filename": logo.filename},
    )
    return result



@settings_router.get("/logo/get")
def get_logo_route(theme: str = "light"):
    """Get a logo image for a theme."""
    return get_logo(theme)


@settings_router.get("/branding/assets")
def get_branding_assets_route(_ = Depends(verified_admin)):
    """Get branding assets overview."""
    return get_branding_assets_overview()



# -------------------
# Favicon
# -------------------
@settings_router.post("/icon/upload")
def upload_icon_route(
    request: Request,
    icon: UploadFile = File(...),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Upload an icon image and generate favicon set."""
    result = upload_icon(icon)
    _audit_settings_event(
        db_log,
        request,
        admin_user,
        "BRANDING_ICON_UPLOADED",
        {"filename": icon.filename},
    )
    return result



@settings_router.get("/icon/get")
def get_icon_route(size: int | None = None, v: str | None = None):
    """Return the icon. Default is vector favicon.svg if present; otherwise icon.png.

    When a specific size is provided (16, 32, 180, 512), the corresponding PNG is served.
    """
    return get_icon(size, v)



# -------------------
# Login Background Image
# -------------------
@settings_router.post("/login-background/upload")
def upload_login_background_route(
    request: Request,
    image: UploadFile = File(...),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Upload a background image for the login page."""
    result = upload_login_background(image)
    _audit_settings_event(
        db_log,
        request,
        admin_user,
        "LOGIN_BACKGROUND_UPLOADED",
        {"filename": image.filename},
    )
    return result


@settings_router.get("/login-background/get")
def get_login_background_route():
    """Return the login background image."""
    return get_login_background()


@settings_router.delete("/login-background/delete")
def delete_login_background_route(
    request: Request,
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Delete the login background image."""
    result = delete_login_background()
    _audit_settings_event(db_log, request, admin_user, "LOGIN_BACKGROUND_DELETED")
    return result


# -------------------
# LDAP CA Certificate
# -------------------
@settings_router.post("/ldap-ca-cert/upload")
def upload_ldap_ca_certificate_route(
    request: Request,
    certificate: UploadFile = File(...),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Upload managed LDAP CA certificate and save its path in LDAP settings."""
    result = upload_ldap_ca_certificate(certificate, db)
    _audit_settings_event(
        db_log,
        request,
        admin_user,
        "LDAP_CA_CERT_UPLOADED",
        {"filename": certificate.filename, "content_type": certificate.content_type},
    )
    return result


@settings_router.get("/ldap-ca-cert/status")
def get_ldap_ca_certificate_status_route(
    db: Session = Depends(get_db),
    _ = Depends(verified_admin),
):
    """Return managed LDAP CA certificate status and current LDAP cert path usage."""
    return get_ldap_ca_certificate_status(db)


@settings_router.delete("/ldap-ca-cert/delete")
def delete_ldap_ca_certificate_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Delete managed LDAP CA certificate and clear LDAP cert path setting."""
    result = delete_ldap_ca_certificate(db)
    _audit_settings_event(db_log, request, admin_user, "LDAP_CA_CERT_DELETED")
    return result



@settings_router.post("/server/setup", response_model=ServerSetupResponse)
def server_setup_route(
    payload: ServerSetupRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Complete the initial server setup. Only accessible by verified admins.
    Sets application name, public URLs, and marks server_setup as complete.
    """
    result = complete_server_setup(
        payload.application_name,
        payload.public_url,
        payload.default_user_role,
        db,
    )
    try:
        create_audit_log(
            db_log=db_log,
            user_id=admin_user.id,
            action="SERVER_SETUP_COMPLETED",
            details={
                "application_name": payload.application_name,
                "public_url": payload.public_url,
                "default_user_role": payload.default_user_role,
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="settings",
        )
    except Exception:
        logger.exception(
            "Failed to create server setup audit log for admin_user_id=%s application_name=%r public_url=%r default_user_role=%r",
            getattr(admin_user, "id", None),
            payload.application_name,
            payload.public_url,
            payload.default_user_role,
        )
    return result
