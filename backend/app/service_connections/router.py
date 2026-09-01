from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.service_connections.schemas import (
    ServiceConnectionCreate,
    ServiceConnectionDeleteResponse,
    ServiceConnectionResponse,
    ServiceConnectionUpdate,
)
from app.service_connections.utils import (
    create_service_connection,
    delete_service_connection,
    get_service_connection,
    list_service_connections,
    public_service_connection,
    refresh_service_connection_status,
    update_service_connection,
)

service_connections_router = APIRouter(
    prefix="/api/v1/service-connections",
    tags=["service-connections"],
)


def _audit_service_connection_event(
    db_log: Session,
    db: Session,
    request: Request,
    admin_user,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record a secret-free administrator action for this feature."""

    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="service_connection",
    )


@service_connections_router.get("", response_model=list[ServiceConnectionResponse])
def list_service_connections_route(
    db: Session = Depends(get_db),
    _admin_user=Depends(verified_admin),
):
    """List shared service connections without returning API keys."""

    return [public_service_connection(item) for item in list_service_connections(db)]


@service_connections_router.post(
    "",
    response_model=ServiceConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_service_connection_route(
    payload: ServiceConnectionCreate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Create an encrypted service connection and audit its public fields."""

    connection = create_service_connection(db, payload.model_dump(exclude_unset=True))
    _audit_service_connection_event(
        db_log,
        db,
        request,
        admin_user,
        "CREATE_SERVICE_CONNECTION",
        {
            "connection_id": connection.get("id"),
            "name": connection.get("name"),
            "base_url": connection.get("base_url"),
            "enabled_for_code_execution": connection.get("enabled_for_code_execution"),
            "enabled_for_latex_pdf": connection.get("enabled_for_latex_pdf"),
            "enabled_for_slide_renderer": connection.get("enabled_for_slide_renderer"),
            "weight": connection.get("weight"),
        },
    )
    return public_service_connection(connection)


@service_connections_router.get(
    "/{connection_id}",
    response_model=ServiceConnectionResponse,
)
def get_service_connection_route(
    connection_id: str,
    db: Session = Depends(get_db),
    _admin_user=Depends(verified_admin),
):
    """Return one secret-free service connection."""

    return public_service_connection(get_service_connection(db, connection_id))


@service_connections_router.put(
    "/{connection_id}",
    response_model=ServiceConnectionResponse,
)
def update_service_connection_route(
    connection_id: str,
    payload: ServiceConnectionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Update selected connection fields and audit the resulting configuration."""

    connection = update_service_connection(
        db, connection_id, payload.model_dump(exclude_unset=True)
    )
    _audit_service_connection_event(
        db_log,
        db,
        request,
        admin_user,
        "UPDATE_SERVICE_CONNECTION",
        {
            "connection_id": connection.get("id"),
            "name": connection.get("name"),
            "base_url": connection.get("base_url"),
            "enabled_for_code_execution": connection.get("enabled_for_code_execution"),
            "enabled_for_latex_pdf": connection.get("enabled_for_latex_pdf"),
            "enabled_for_slide_renderer": connection.get("enabled_for_slide_renderer"),
            "weight": connection.get("weight"),
        },
    )
    return public_service_connection(connection)


@service_connections_router.delete(
    "/{connection_id}",
    response_model=ServiceConnectionDeleteResponse,
)
def delete_service_connection_route(
    connection_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Delete one connection and retain secret-free audit context."""

    connection = get_service_connection(db, connection_id)
    result = delete_service_connection(db, connection_id)
    _audit_service_connection_event(
        db_log,
        db,
        request,
        admin_user,
        "DELETE_SERVICE_CONNECTION",
        {
            "connection_id": connection.get("id"),
            "name": connection.get("name"),
            "base_url": connection.get("base_url"),
        },
    )
    return result


@service_connections_router.post(
    "/{connection_id}/status",
    response_model=ServiceConnectionResponse,
)
def refresh_service_connection_status_route(
    connection_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    """Probe one connection and audit its aggregate result."""

    connection = refresh_service_connection_status(db, connection_id)
    _audit_service_connection_event(
        db_log,
        db,
        request,
        admin_user,
        "REFRESH_SERVICE_CONNECTION_STATUS",
        {
            "connection_id": connection.get("id"),
            "name": connection.get("name"),
            "status": (connection.get("status") or {}).get("available"),
        },
    )
    return public_service_connection(connection)
