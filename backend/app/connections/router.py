from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.connections.schemas import ConnectionCatalogResponse, ConnectionCreateRequest, ConnectionToolPreviewResponse, ConnectionUpdateRequest, UserConnectionResponse
from app.connections.service import (
    build_callback_redirect_url,
    complete_connection_oauth,
    create_connection_payload,
    delete_connection_payload,
    list_connections_catalog_payload,
    preview_connection_tools_payload,
    start_connection_oauth,
    update_connection_payload,
)
from app.connections.models import (
    UserConnection,
    consume_connection_oauth_audit_subject,
    get_user_connection,
    resolve_connection_oauth_audit_subject,
)
from app.dependencies import get_db, get_db_log, verified_user
from app.logging.models import create_audit_log, get_audit_request_ip


connections_router = APIRouter(prefix="/api/v1/connections", tags=["connections"])
logger = logging.getLogger(__name__)

_OAUTH_OUTCOME_AUDIT = {
    "provider_denied": ("CONNECTION_OAUTH_DENIED", "denied"),
    "missing_code": ("CONNECTION_OAUTH_FAILED", "failed"),
    "completion_failed": ("CONNECTION_OAUTH_FAILED", "failed"),
}


def _mask(value: str | None, *, keep: int = 6) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= keep:
        return text
    return f"{text[:keep]}..."


def _audit_connection_event(
    db_log: Session,
    request: Request | None,
    user_id: str | None,
    action: str,
    details: dict | None = None,
) -> None:
    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
        category="connections",
    )


def _consume_connection_oauth_audit_subject_best_effort(
    db: Session,
    *,
    state: str | None,
    provider: str,
) -> dict[str, str] | None:
    try:
        return consume_connection_oauth_audit_subject(
            db,
            state=state,
            provider=provider,
        )
    except Exception:
        # State cleanup must never replace the callback's safe redirect.
        logger.exception("Unable to clear connection OAuth callback state")
        return None


def _audit_connection_oauth_outcome_best_effort(
    db_log: Session,
    request: Request,
    subject: dict[str, str] | None,
    *,
    outcome: str,
) -> None:
    audit_event = _OAUTH_OUTCOME_AUDIT.get(outcome)
    if subject is None or audit_event is None:
        return
    action, status = audit_event
    try:
        _audit_connection_event(
            db_log,
            request,
            subject["user_id"],
            action,
            {
                "provider": subject["provider"],
                "status": status,
                "outcome": outcome,
            },
        )
    except Exception:
        # Audit delivery is best effort on an already-failed OAuth callback.
        logger.exception("Unable to record connection OAuth callback outcome")


@connections_router.get(
    "/catalog",
    response_model=ConnectionCatalogResponse,
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
)
def get_connections_catalog_route(
    response: Response,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Return the minimal private connection catalog for the current user."""
    response.headers["Cache-Control"] = "private, no-store"
    logger.info("connections.catalog user=%s", _mask(getattr(user, "id", None)))
    return list_connections_catalog_payload(db, user.id)


@connections_router.get("/providers/{provider}/connect-url")
def start_connection_oauth_url_route(
    provider: str,
    return_to: str | None = Query(default="/workspace/connections"),
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    logger.info(
        "connections.start.url provider=%s user=%s return_to=%s auth=access",
        provider,
        _mask(getattr(user, "id", None)),
        return_to,
    )
    auth_url = start_connection_oauth(db, user_id=user.id, provider=provider, return_path=return_to)
    return {"url": auth_url}


@connections_router.post("/providers/{provider}/connect", response_model=UserConnectionResponse)
def create_connection_route(
    provider: str,
    payload: ConnectionCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    logger.info(
        "connections.create provider=%s user=%s auth=access",
        provider,
        _mask(getattr(user, "id", None)),
    )
    result = create_connection_payload(db, user_id=user.id, provider=provider, payload=payload)
    _audit_connection_event(
        db_log,
        request,
        user.id,
        "CONNECTION_CREATED",
        {
            "connection_id": result.get("id"),
            "provider": result.get("provider") or provider,
            "auth_mode": result.get("auth_mode"),
            "enabled": bool(result.get("enabled")),
        },
    )
    return result


@connections_router.get("/oauth/{provider}/callback")
def complete_connection_oauth_route(
    provider: str,
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    iss: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
):
    logger.info(
        "connections.callback.enter provider=%s state=%s has_code=%s error=%s",
        provider,
        _mask(state),
        bool(code),
        error or "",
    )
    if error:
        logger.warning("connections.callback.oauth_error provider=%s state=%s error=%s", provider, _mask(state), error)
        audit_subject = _consume_connection_oauth_audit_subject_best_effort(
            db,
            state=state,
            provider=provider,
        )
        _audit_connection_oauth_outcome_best_effort(
            db_log,
            request,
            audit_subject,
            outcome="provider_denied",
        )
        redirect_url = build_callback_redirect_url(
            db,
            return_path="/workspace/connections",
            status="error",
            provider=provider,
            error=error,
        )
        return RedirectResponse(url=redirect_url, status_code=302)
    if not code or not state:
        logger.warning(
            "connections.callback.missing_params provider=%s state=%s has_code=%s",
            provider,
            _mask(state),
            bool(code),
        )
        audit_subject = _consume_connection_oauth_audit_subject_best_effort(
            db,
            state=state,
            provider=provider,
        )
        _audit_connection_oauth_outcome_best_effort(
            db_log,
            request,
            audit_subject,
            outcome="missing_code",
        )
        redirect_url = build_callback_redirect_url(
            db,
            return_path="/workspace/connections",
            status="error",
            provider=provider,
            error="Missing callback parameters.",
        )
        return RedirectResponse(url=redirect_url, status_code=302)
    audit_subject = None
    oauth_completion_succeeded = False
    try:
        audit_subject = resolve_connection_oauth_audit_subject(
            db,
            state=state,
            provider=provider,
        )
        result = complete_connection_oauth(
            db,
            provider=provider,
            state=state,
            code=code,
            authorization_issuer=iss,
        )
        oauth_completion_succeeded = True
        connection_payload = result.get("connection") or {}
        connection_id = connection_payload.get("id")
        connection_row = db.query(UserConnection).filter(UserConnection.id == connection_id).first() if connection_id else None
        _audit_connection_event(
            db_log,
            request,
            getattr(connection_row, "user_id", None),
            "CONNECTION_OAUTH_COMPLETED",
            {
                "connection_id": connection_id,
                "provider": connection_payload.get("provider") or provider,
                "status": "connected",
            },
        )
        logger.info(
            "connections.callback.success provider=%s state=%s return_path=%s connection_id=%s",
            provider,
            _mask(state),
            result.get("return_path"),
            _mask((result.get("connection") or {}).get("id")),
        )
        redirect_url = build_callback_redirect_url(
            db,
            return_path=result["return_path"],
            status="connected",
            provider=provider,
        )
        return RedirectResponse(url=redirect_url, status_code=302)
    except HTTPException as exc:
        if not oauth_completion_succeeded:
            cleared_subject = _consume_connection_oauth_audit_subject_best_effort(
                db,
                state=state,
                provider=provider,
            )
            _audit_connection_oauth_outcome_best_effort(
                db_log,
                request,
                audit_subject or cleared_subject,
                outcome="completion_failed",
            )
        logger.warning(
            "connections.callback.http_error provider=%s state=%s status=%s detail=%s",
            provider,
            _mask(state),
            exc.status_code,
            exc.detail,
        )
        redirect_url = build_callback_redirect_url(
            db,
            return_path="/workspace/connections",
            status="error",
            provider=provider,
            error=str(exc.detail),
        )
        return RedirectResponse(url=redirect_url, status_code=302)
    except Exception as exc:
        if not oauth_completion_succeeded:
            cleared_subject = _consume_connection_oauth_audit_subject_best_effort(
                db,
                state=state,
                provider=provider,
            )
            _audit_connection_oauth_outcome_best_effort(
                db_log,
                request,
                audit_subject or cleared_subject,
                outcome="completion_failed",
            )
        logger.exception(
            "connections.callback.exception provider=%s state=%s error=%s",
            provider,
            _mask(state),
            exc,
        )
        redirect_url = build_callback_redirect_url(
            db,
            return_path="/workspace/connections",
            status="error",
            provider=provider,
            error=str(exc),
        )
        return RedirectResponse(url=redirect_url, status_code=302)


@connections_router.patch("/{connection_id}", response_model=UserConnectionResponse)
def update_connection_route(
    connection_id: str,
    payload: ConnectionUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    before = get_user_connection(db, user.id, connection_id)
    result = update_connection_payload(db, user_id=user.id, connection_id=connection_id, payload=payload)
    _audit_connection_event(
        db_log,
        request,
        user.id,
        "CONNECTION_UPDATED",
        {
            "connection_id": connection_id,
            "provider": getattr(before, "provider", None),
            "enabled": bool(result.get("enabled")),
            "access_token_updated": bool(str(payload.access_token or "").strip()),
        },
    )
    return result


@connections_router.delete("/{connection_id}")
def delete_connection_route(
    connection_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    connection = get_user_connection(db, user.id, connection_id)
    provider = getattr(connection, "provider", None)
    result = delete_connection_payload(db, user_id=user.id, connection_id=connection_id)
    revocation = result.get("provider_revocation") or {}
    _audit_connection_event(
        db_log,
        request,
        user.id,
        "CONNECTION_DELETED",
        {
            "connection_id": connection_id,
            "provider": provider,
            "provider_revocation_state": revocation.get("state"),
            "provider_revocation_attempted": bool(revocation.get("attempted")),
            "provider_revocation_supported": bool(revocation.get("supported")),
            "automation_references_removed": int(result.get("automation_references_removed") or 0),
            "provider_revocation_failure_count": len(revocation.get("failures") or []),
        },
    )
    return {
        "status": "success",
        "automation_references_removed": int(result.get("automation_references_removed") or 0),
        "provider_revocation": {
            "state": revocation.get("state"),
            "attempted": bool(revocation.get("attempted")),
            "supported": bool(revocation.get("supported")),
        },
    }


@connections_router.get("/{connection_id}/tools", response_model=ConnectionToolPreviewResponse)
def preview_connection_tools_route(
    connection_id: str,
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    return preview_connection_tools_payload(db, user_id=user.id, connection_id=connection_id)
