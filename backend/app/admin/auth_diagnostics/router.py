"""Administrator API for authentication failures and OIDC preflight checks."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.admin.auth_diagnostics.schemas import (
    AuthDiagnosticItem,
    AuthDiagnosticPage,
    OIDCConfigurationTestRequest,
    OIDCConfigurationTestResponse,
)
from app.admin.auth_diagnostics.utils import test_oidc_configuration
from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import AuthenticationLogs, create_audit_log, get_audit_request_ip

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin_router.get("/auth-diagnostics", response_model=AuthDiagnosticPage)
def list_auth_diagnostics(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    reference: str | None = Query(None, max_length=64),
    provider: str | None = Query(None, max_length=64),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """List structured authentication failures, newest first."""

    query = db_log.query(AuthenticationLogs).filter(AuthenticationLogs.error_code.isnot(None))
    if reference:
        query = query.filter(AuthenticationLogs.correlation_id == reference)
    if provider:
        query = query.filter(AuthenticationLogs.provider == provider)
    total = query.count()
    rows = (
        query.order_by(AuthenticationLogs.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        AuthDiagnosticItem(
            id=row.id,
            reference=row.correlation_id,
            flow=row.flow,
            provider=row.provider,
            stage=row.stage,
            error_code=row.error_code,
            status=row.status,
            message=row.message,
            details=row.details if isinstance(row.details, dict) else None,
            timestamp=row.timestamp,
        )
        for row in rows
    ]
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_AUTH_DIAGNOSTICS",
        details={"page": page, "page_size": page_size, "reference": reference, "provider": provider},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return {"items": items, "page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}


@admin_router.post("/auth-diagnostics/oidc/test", response_model=OIDCConfigurationTestResponse)
async def run_oidc_configuration_test(
    payload: OIDCConfigurationTestRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Run safe, read-only checks against the saved OIDC configuration."""

    result = await test_oidc_configuration(db, request)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="TEST_OIDC_CONFIGURATION",
        details={"status": result["status"], "reference": result["reference"]},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="admin",
    )
    return result
