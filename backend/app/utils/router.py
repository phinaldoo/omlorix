import ipaddress
import os
import re
import secrets

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.utils.utils import (
    get_privacy_policy,
    get_privacy_policy_notice_policy,
    is_default_privacy_policy,
    get_terms_of_service,
    get_terms_of_service_policy,
    get_version_status,
)
from app.utils.client_ip import (
    resolve_request_client_ip,
)
from app.dependencies import get_db
from app.settings.utils import coerce_bool, get_value_by_page_and_key
from app.utils.schemas import LegalDocumentAvailability, ProxyVerificationResponse

utils_router = APIRouter(prefix="/api/v1", tags=["utils"])
DEFAULT_LEGAL_CONTENT_LANGUAGE = "en"


def _legal_content_language_metadata(*, is_default_template: bool) -> dict:
    language = DEFAULT_LEGAL_CONTENT_LANGUAGE if is_default_template else None
    return {
        "content_language": language,
        "authoritative_language": language,
        "localized_content_available": False,
    }


@utils_router.get("/client-ip")
def client_ip(request: Request, db: Session = Depends(get_db)):
    """Return the requesting client's IP address (no auth required).

    The Electron server manager uses this value to report whether Omlorix sees
    the visitor address or only an internal reverse-proxy address.

    This informational endpoint deliberately uses the same production resolver
    as authentication, audit logging, IP restrictions, and rate limiting. It
    must never make an unsafe deployment appear healthy by broadly trusting
    every private network.
    """
    ip = resolve_request_client_ip(request, default=None)
    return JSONResponse(
        content={
            "ip": ip or "Unknown",
            "scheme": str(request.url.scheme or ""),
            "host": str(request.headers.get("host") or ""),
        }
    )


_PROXY_VERIFICATION_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@utils_router.get("/proxy-verification", response_model=ProxyVerificationResponse)
def proxy_verification(request: Request, nonce: str) -> ProxyVerificationResponse:
    """Verify visitor identity and public scheme through the complete ingress path.

    nginx replaces the verification header only on the authenticated launcher
    path. Comparing it here prevents direct Docker clients from using this
    endpoint as evidence that their untrusted forwarding headers were accepted.
    """
    expected_secret = os.getenv("OMLORIX_LAUNCHER_PROXY_SECRET", "").strip()
    supplied_secret = request.headers.get("x-omlorix-proxy-verification", "").strip()
    supplied_nonce = request.headers.get(
        "x-omlorix-proxy-verification-nonce", ""
    ).strip()
    if (
        len(expected_secret) < 32
        or not secrets.compare_digest(supplied_secret, expected_secret)
        or not _PROXY_VERIFICATION_NONCE.fullmatch(nonce)
        or not secrets.compare_digest(supplied_nonce, nonce)
    ):
        raise HTTPException(status_code=404, detail="Not found")

    resolved_ip = resolve_request_client_ip(request, default=None)
    forwarded_ip = _clean_ip(request.headers.get("x-forwarded-for"))
    scheme = str(request.url.scheme or "").lower()
    if scheme not in {"http", "https"}:
        scheme = "https" if request.scope.get("scheme") == "https" else "http"

    return ProxyVerificationResponse(
        client_ip=resolved_ip or "Unknown",
        scheme=scheme,
        host=str(request.headers.get("host") or ""),
        nonce=nonce,
        trust_chain_accepted=bool(resolved_ip and forwarded_ip == resolved_ip),
    )


def _clean_ip(value):
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None
    # Take first item if it's a comma-separated list.
    raw = raw.split(",", 1)[0].strip()
    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]
    elif raw.count(":") == 1:
        host, port = raw.split(":", 1)
        if port.isdigit():
            raw = host
    try:
        return ipaddress.ip_address(raw).compressed
    except Exception:
        return None


@utils_router.get("/version")
def version(force_refresh: bool = False):
    status = get_version_status(force_refresh=force_refresh)
    return JSONResponse(content=status)


@utils_router.get("/legal/availability", response_model=LegalDocumentAvailability)
def legal_document_availability(db: Session = Depends(get_db)) -> LegalDocumentAvailability:
    """Return which legal-document links should appear in public navigation.

    These flags control discoverability only. Direct document routes remain
    available because privacy notices and Terms acceptance flows must let users
    review the applicable document even when its optional login-footer link is
    hidden.
    """
    return LegalDocumentAvailability(
        privacy=coerce_bool(
            get_value_by_page_and_key("login_general", "show_privacy_notice_link", db),
            default=False,
        ),
        terms=coerce_bool(
            get_value_by_page_and_key("login_general", "show_terms_of_service_link", db),
            default=False,
        ),
    )



@utils_router.get("/privacy")
def privacy(db: Session = Depends(get_db)):
    policy_content = get_privacy_policy(db)
    policy = get_privacy_policy_notice_policy(db)
    is_default = is_default_privacy_policy(policy_content)
    return JSONResponse(
        content={
            "content": policy_content,
            "revision": policy.get("revision"),
            "updated_at": policy.get("notice_updated_at"),
            "is_default_template": is_default,
            "customization_required": is_default,
            **_legal_content_language_metadata(is_default_template=is_default),
        }
    )


@utils_router.get("/privacy/policy")
def privacy_policy_state(db: Session = Depends(get_db)):
    return JSONResponse(content=get_privacy_policy_notice_policy(db))


@utils_router.get("/terms")
def terms(db: Session = Depends(get_db)):
    terms_content = get_terms_of_service(db)
    policy = get_terms_of_service_policy(db)
    is_default = bool(policy.get("is_default_template"))
    return JSONResponse(
        content={
            "content": terms_content,
            "revision": policy.get("revision"),
            "updated_at": policy.get("updated_at"),
            "show_link_on_login": policy.get("show_link_on_login"),
            "signup_available": policy.get("signup_available"),
            "signup_block_reason": policy.get("signup_block_reason"),
            "is_default_template": is_default,
            "customization_required": bool(policy.get("customization_required")),
            **_legal_content_language_metadata(is_default_template=is_default),
        }
    )
