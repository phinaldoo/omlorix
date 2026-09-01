"""Business rules, health checks, and routing for shared service connections."""

from __future__ import annotations

from copy import deepcopy
import logging
import random
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.service_connections.models import (
    ServiceConnection,
    delete_service_connection_row,
    get_service_connection_row,
    has_enabled_service_connection_row,
    list_enabled_service_connection_statuses,
    list_service_connection_rows,
    save_service_connection_row,
    update_service_connection_status_row,
    utc_now,
)

logger = logging.getLogger(__name__)

SERVICE_PURPOSE_CODE_EXECUTION = "code_execution"
SERVICE_PURPOSE_LATEX_PDF = "latex_pdf"
SERVICE_PURPOSE_SLIDE_RENDERER = "slide_renderer"
SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES = "external_pip_packages"
SERVICE_PURPOSE_ORDER = (
    SERVICE_PURPOSE_CODE_EXECUTION,
    SERVICE_PURPOSE_LATEX_PDF,
    SERVICE_PURPOSE_SLIDE_RENDERER,
)
SERVICE_PURPOSES = frozenset(
    {
        SERVICE_PURPOSE_CODE_EXECUTION,
        SERVICE_PURPOSE_LATEX_PDF,
        SERVICE_PURPOSE_SLIDE_RENDERER,
    }
)
SERVICE_PURPOSE_ENABLED_FIELDS = {
    SERVICE_PURPOSE_CODE_EXECUTION: "enabled_for_code_execution",
    SERVICE_PURPOSE_LATEX_PDF: "enabled_for_latex_pdf",
    SERVICE_PURPOSE_SLIDE_RENDERER: "enabled_for_slide_renderer",
}
CODE_EXECUTION_PROTOCOL_PURPOSES = frozenset(
    {SERVICE_PURPOSE_CODE_EXECUTION, SERVICE_PURPOSE_LATEX_PDF}
)
ServicePurpose = Literal["code_execution", "latex_pdf", "slide_renderer"]
RuntimeFailureScope = Literal["request", "service"]

SERVICE_CONNECTION_API_TIMEOUT = 8


def _utc_iso() -> str:
    """Return the current UTC timestamp in the established runtime format."""

    return utc_now().isoformat()


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Interpret common stored and request boolean representations."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_weight(value: Any) -> int:
    """Clamp routing weights to the supported inclusive range."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(100, parsed))


def _normalize_base_url(value: Any) -> str:
    """Strip surrounding whitespace and a trailing URL slash."""

    normalized = str(value or "").strip().rstrip("/")
    return normalized


def _validate_base_url(base_url: str) -> str:
    """Require an absolute HTTP(S) URL without embedded credentials."""

    normalized = _normalize_base_url(base_url)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Base URL is required"
        )
    try:
        parsed = urlparse(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Base URL is invalid"
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Base URL must be an absolute http(s) URL",
        )
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Base URL must not include credentials",
        )
    return normalized


def normalize_service_purpose(purpose: str) -> ServicePurpose:
    """Return one supported purpose or reject the caller's value."""

    normalized = str(purpose or "").strip().lower()
    if normalized not in SERVICE_PURPOSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported service purpose",
        )
    return normalized  # type: ignore[return-value]


def _default_status(connection: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a complete initial status document from enabled capabilities."""

    enabled_code = _coerce_bool(
        (connection or {}).get("enabled_for_code_execution"), False
    )
    enabled_latex = _coerce_bool((connection or {}).get("enabled_for_latex_pdf"), False)
    enabled_slide = _coerce_bool(
        (connection or {}).get("enabled_for_slide_renderer"), False
    )
    return {
        "available": "unknown"
        if (enabled_code or enabled_latex or enabled_slide)
        else "disabled",
        "message": "",
        "checked_at": "",
        SERVICE_PURPOSE_CODE_EXECUTION: "unknown" if enabled_code else "disabled",
        SERVICE_PURPOSE_LATEX_PDF: "unknown" if enabled_latex else "disabled",
        SERVICE_PURPOSE_SLIDE_RENDERER: "unknown" if enabled_slide else "disabled",
        f"{SERVICE_PURPOSE_CODE_EXECUTION}_auth": "unknown"
        if enabled_code
        else "disabled",
        f"{SERVICE_PURPOSE_LATEX_PDF}_auth": "unknown" if enabled_latex else "disabled",
        f"{SERVICE_PURPOSE_SLIDE_RENDERER}_auth": "unknown"
        if enabled_slide
        else "disabled",
        f"{SERVICE_PURPOSE_CODE_EXECUTION}_capabilities": {},
    }


def _normalize_status(status_value: Any, connection: dict[str, Any]) -> dict[str, Any]:
    """Merge stored probe state with all currently required status keys."""

    status_data = dict(status_value) if isinstance(status_value, dict) else {}
    defaults = _default_status(connection)
    defaults.update(status_data)
    capabilities_key = f"{SERVICE_PURPOSE_CODE_EXECUTION}_capabilities"
    if not isinstance(defaults.get(capabilities_key), dict):
        defaults[capabilities_key] = {}
    for purpose, enabled_field in SERVICE_PURPOSE_ENABLED_FIELDS.items():
        if not _coerce_bool(connection.get(enabled_field), False):
            defaults[purpose] = "disabled"
            defaults[f"{purpose}_auth"] = "disabled"
    return defaults


def _row_to_connection(row: ServiceConnection) -> dict[str, Any]:
    """Convert one ORM row to the runtime dictionary consumed by tools."""

    connection = {
        "id": str(row.id),
        "name": str(row.name or "").strip(),
        "base_url": _normalize_base_url(row.base_url),
        "api_key": str(row.api_key or "").strip(),
        "enabled_for_code_execution": bool(row.enabled_for_code_execution),
        "enabled_for_latex_pdf": bool(row.enabled_for_latex_pdf),
        "enabled_for_slide_renderer": bool(row.enabled_for_slide_renderer),
        "weight": _normalize_weight(row.weight),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    connection["status"] = _normalize_status(row.status, connection)
    return connection


def _name_from_base_url(base_url: str) -> str:
    """Derive a concise display name when an administrator omits one."""

    try:
        parsed = urlparse(base_url)
    except ValueError:
        return "Service connection"
    if parsed.hostname:
        return parsed.hostname
    return "Service connection"


def list_service_connections(db: Session) -> list[dict[str, Any]]:
    """List all service connections in deterministic creation order."""

    return [_row_to_connection(row) for row in list_service_connection_rows(db)]


def public_service_connection(connection: dict[str, Any]) -> dict[str, Any]:
    """Remove credentials and expose only whether a credential is configured."""

    payload = deepcopy(connection)
    api_key = str(payload.pop("api_key", "") or "")
    payload["has_api_key"] = bool(api_key)
    return payload


def _require_connection_row(db: Session, connection_id: str) -> ServiceConnection:
    """Resolve one row or raise the feature's stable not-found response."""

    row = get_service_connection_row(db, connection_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service connection not found",
        )
    return row


def get_service_connection(db: Session, connection_id: str) -> dict[str, Any]:
    """Return one service connection including its credential for internal use."""

    return _row_to_connection(_require_connection_row(db, connection_id))


def _apply_connection_payload(
    row: ServiceConnection,
    payload: dict[str, Any],
    *,
    creating: bool,
) -> ServiceConnection:
    """Validate and apply a create/update payload to one isolated ORM row."""

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload must be an object",
        )

    base_url = _validate_base_url(payload.get("base_url", row.base_url or ""))
    name = str(payload.get("name", row.name or "") or "").strip()
    row.name = (name or _name_from_base_url(base_url))[:120]
    row.base_url = base_url

    if not creating and _coerce_bool(payload.get("clear_api_key"), False):
        row.api_key = None
    elif creating or (
        "api_key" in payload and str(payload.get("api_key") or "").strip()
    ):
        row.api_key = str(payload.get("api_key") or "").strip() or None

    for enabled_field in SERVICE_PURPOSE_ENABLED_FIELDS.values():
        current_value = bool(getattr(row, enabled_field, False))
        setattr(
            row,
            enabled_field,
            _coerce_bool(payload.get(enabled_field, current_value), False),
        )
    row.weight = _normalize_weight(payload.get("weight", row.weight or 1))
    row.updated_at = utc_now()

    connection = _row_to_connection(row)
    if (
        not connection["enabled_for_code_execution"]
        and not connection["enabled_for_latex_pdf"]
        and not connection["enabled_for_slide_renderer"]
    ):
        row.status = _default_status(connection)
    else:
        row.status = _normalize_status(row.status, connection)
    return row


def create_service_connection(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Create one encrypted, independently persisted service connection."""

    now = utc_now()
    row = ServiceConnection(
        name="",
        base_url="",
        status={},
        created_at=now,
        updated_at=now,
    )
    _apply_connection_payload(row, payload, creating=True)
    return _row_to_connection(save_service_connection_row(db, row))


def update_service_connection(
    db: Session, connection_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Update only the requested fields on one connection row."""

    row = _require_connection_row(db, connection_id)
    _apply_connection_payload(row, payload, creating=False)
    return _row_to_connection(save_service_connection_row(db, row))


def delete_service_connection(db: Session, connection_id: str) -> dict[str, Any]:
    """Delete one connection without rewriting unrelated records."""

    row = _require_connection_row(db, connection_id)
    normalized_id = str(row.id)
    delete_service_connection_row(db, row)
    return {"deleted": True, "connection_id": normalized_id}


def _feature_enabled(connection: dict[str, Any], purpose: ServicePurpose) -> bool:
    """Return whether a runtime connection is enabled for one purpose."""

    return _coerce_bool(connection.get(SERVICE_PURPOSE_ENABLED_FIELDS[purpose]), False)


def _purpose_status(connection: dict[str, Any], purpose: ServicePurpose) -> str:
    """Read one purpose's effective health state from a status document."""

    status_data = (
        connection.get("status") if isinstance(connection.get("status"), dict) else {}
    )
    return (
        str(status_data.get(purpose) or status_data.get("available") or "unknown")
        .strip()
        .lower()
    )


def _weighted_order(
    connections: list[dict[str, Any]], exclude_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """Return a weighted random order without selecting any item twice."""

    exclude_ids = exclude_ids or set()
    remaining = [
        deepcopy(item) for item in connections if item.get("id") not in exclude_ids
    ]
    ordered: list[dict[str, Any]] = []

    while remaining:
        total_weight = sum(
            _normalize_weight(item.get("weight", 1)) for item in remaining
        )
        if total_weight <= 0:
            ordered.extend(remaining)
            break
        pick = random.uniform(0, total_weight)
        current = 0.0
        selected_index = len(remaining) - 1
        for index, item in enumerate(remaining):
            current += _normalize_weight(item.get("weight", 1))
            if pick <= current:
                selected_index = index
                break
        ordered.append(remaining.pop(selected_index))

    return ordered


def get_service_connection_candidates(
    db: Session,
    purpose: ServicePurpose,
    *,
    exclude_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return weighted candidates after filtering at the database boundary."""

    purpose = normalize_service_purpose(purpose)
    exclude_set = set(exclude_ids or [])
    connections = [
        _row_to_connection(row)
        for row in list_service_connection_rows(
            db,
            enabled_column=SERVICE_PURPOSE_ENABLED_FIELDS[purpose],
        )
    ]
    # Prefer candidates that are not explicitly marked down.
    #
    # A connection can be marked down by a transient timeout or network failure,
    # and that state is persisted until somebody re-probes it. If we only ever
    # read the "not down" set, then a stale down flag can strand an otherwise
    # healthy connection forever. To keep the runtime resilient, we first try
    # healthy/unknown candidates and only fall back to down ones when there are
    # no other options left.
    preferred_candidates: list[dict[str, Any]] = []
    fallback_candidates: list[dict[str, Any]] = []
    for connection in connections:
        if connection.get("id") in exclude_set:
            continue
        if not _normalize_base_url(connection.get("base_url")):
            continue
        if _purpose_status(connection, purpose) == "down" and not connection.get(
            "legacy"
        ):
            fallback_candidates.append(connection)
        else:
            preferred_candidates.append(connection)

    candidates = preferred_candidates or fallback_candidates
    return _weighted_order(candidates, exclude_set)


def get_service_connection_capabilities(
    connection: dict[str, Any],
    purpose: ServicePurpose,
) -> dict[str, bool]:
    """Return the last capabilities reported by a connection health probe."""
    purpose = normalize_service_purpose(purpose)
    status_data = (
        connection.get("status") if isinstance(connection.get("status"), dict) else {}
    )
    raw_capabilities = status_data.get(f"{purpose}_capabilities")
    if not isinstance(raw_capabilities, dict):
        return {}
    return {
        str(name): value
        for name, value in raw_capabilities.items()
        if isinstance(name, str) and isinstance(value, bool)
    }


def has_healthy_service_connection_capability(
    db: Session,
    purpose: ServicePurpose,
    capability: str,
) -> bool:
    """Return whether a non-down configured connection reports a capability."""
    purpose = normalize_service_purpose(purpose)
    normalized_capability = str(capability or "").strip()
    if not normalized_capability:
        return False
    for status_data in list_enabled_service_connection_statuses(
        db,
        enabled_column=SERVICE_PURPOSE_ENABLED_FIELDS[purpose],
    ):
        connection = {"status": status_data}
        if _purpose_status(connection, purpose) == "down":
            continue
        capabilities = get_service_connection_capabilities(connection, purpose)
        if capabilities.get(normalized_capability) is True:
            return True
    return False


def has_configured_service_connection(
    db: Session,
    purpose: ServicePurpose,
) -> bool:
    """Check configuration with an indexed existence query."""

    purpose = normalize_service_purpose(purpose)
    try:
        return has_enabled_service_connection_row(
            db,
            enabled_column=SERVICE_PURPOSE_ENABLED_FIELDS[purpose],
        )
    except Exception:
        return False


def _resolve_renderer_root_endpoint(base_url: str, path: str) -> str:
    """Resolve a health endpoint from current and historical renderer URLs."""

    normalized_base_url = _normalize_base_url(base_url)
    if not normalized_base_url:
        return ""
    for suffix in ("/api/v1/render", "/api/render", "/api/v1", "/api"):
        if normalized_base_url.endswith(suffix):
            normalized_base_url = normalized_base_url[: -len(suffix)]
            break
    normalized_path = "/" + str(path or "").strip("/")
    return f"{normalized_base_url.rstrip('/')}{normalized_path}"


def _health_headers(
    connection: dict[str, Any], purpose: ServicePurpose
) -> dict[str, str]:
    """Build the authentication headers expected by each service protocol."""

    headers = {"Content-Type": "application/json"}
    api_key = str(connection.get("api_key") or "").strip()
    if api_key:
        if purpose in CODE_EXECUTION_PROTOCOL_PURPOSES:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            # Dedicated renderers use X-API-Key, while older code-execution
            # gateways protect their health endpoint with Bearer auth.
            headers["X-API-Key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
    return headers


def parse_capabilities_payload(payload: Any) -> dict[str, bool]:
    """Normalize current and legacy service health capability payloads."""
    if not isinstance(payload, dict):
        return {}

    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, dict):
        raw_capabilities = {}

    capabilities: dict[str, bool] = {}
    external_pip_packages = raw_capabilities.get(
        SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES
    )
    if not isinstance(external_pip_packages, bool):
        # Compatibility with early capability payloads that exposed this under
        # the version-style features object.
        raw_features = payload.get("features")
        if isinstance(raw_features, dict):
            external_pip_packages = raw_features.get("external_pip_packages")
            if not isinstance(external_pip_packages, bool):
                external_pip_packages = raw_features.get("pip_packages")
    if isinstance(external_pip_packages, bool):
        capabilities[SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES] = external_pip_packages
    return capabilities


def _health_response_capabilities(response: httpx.Response) -> dict[str, bool]:
    """Extract normalized capabilities from a JSON health response."""

    try:
        payload = response.json()
    except ValueError:
        return {}
    return parse_capabilities_payload(payload)


def _check_health_response(
    response: httpx.Response,
    service_label: str,
) -> tuple[bool, str, str, dict[str, bool]]:
    """Translate an HTTP health response into stable status components."""

    capabilities = _health_response_capabilities(response)
    if response.status_code < 200 or response.status_code >= 300:
        detail = (response.text or "").strip()[:300]
        if response.status_code in {401, 403}:
            return (
                False,
                f"{service_label} rejected the API key{': ' + detail if detail else ''}",
                "invalid",
                capabilities,
            )
        return (
            False,
            f"{service_label} returned HTTP {response.status_code}{': ' + detail if detail else ''}",
            "unknown",
            capabilities,
        )

    text = (response.text or "").strip()
    if not text:
        return True, "Available", "valid", capabilities
    if text.lower() in {"ok", "healthy", "ready"}:
        return True, "Available", "valid", capabilities

    try:
        payload = response.json()
    except ValueError:
        return True, "Available", "valid", capabilities

    if isinstance(payload, dict):
        status_value = str(payload.get("status") or "").strip().lower()
        if status_value in {"ok", "healthy", "ready", "up"}:
            return True, "Available", "valid", capabilities
        if (
            payload.get("ok") is True
            or payload.get("healthy") is True
            or payload.get("ready") is True
        ):
            return True, "Available", "valid", capabilities
        if status_value:
            return (
                False,
                f"{service_label} reported status '{status_value}'",
                "unknown",
                capabilities,
            )

    return True, "Available", "valid", capabilities


def _probe_service(
    db: Session,
    connection: dict[str, Any],
    purpose: ServicePurpose,
) -> tuple[str, str, str, dict[str, bool]]:
    """Perform one policy-checked, authenticated service health request."""

    base_url = _normalize_base_url(connection.get("base_url"))
    if not base_url:
        return "down", "Base URL is missing", "unknown", {}

    try:
        assert_url_allowed(
            db,
            url=base_url,
            feature=(
                "Slide renderer service"
                if purpose == SERVICE_PURPOSE_SLIDE_RENDERER
                else "LaTeX PDF service"
                if purpose == SERVICE_PURPOSE_LATEX_PDF
                else "Code execution service"
            ),
        )
    except OutboundRequestBlockedError as exc:
        return "down", str(exc), "unknown", {}

    # Code Execution exposes one authenticated readiness contract for code,
    # LaTeX, and slide rendering. Resolve from the service root so connections
    # saved with an older /api or /api/v1 renderer suffix remain usable.
    endpoint = _resolve_renderer_root_endpoint(base_url, "/health")
    service_label = (
        "Slide renderer"
        if purpose == SERVICE_PURPOSE_SLIDE_RENDERER
        else "LaTeX PDF service"
        if purpose == SERVICE_PURPOSE_LATEX_PDF
        else "Code execution service"
    )

    try:
        with httpx.Client(timeout=SERVICE_CONNECTION_API_TIMEOUT) as client:
            response = client.get(
                endpoint, headers=_health_headers(connection, purpose)
            )
    except httpx.TimeoutException:
        return "down", f"{service_label} health check timed out", "unknown", {}
    except httpx.RequestError as exc:
        return "down", f"{service_label} health check failed: {exc}", "unknown", {}

    available, message, auth_status, capabilities = _check_health_response(
        response, service_label
    )
    return ("up" if available else "down"), message, auth_status, capabilities


def _aggregate_status(
    status_data: dict[str, Any], connection: dict[str, Any]
) -> dict[str, Any]:
    """Derive the connection-wide state from all enabled purpose states."""

    enabled_statuses = []
    for purpose in SERVICE_PURPOSE_ORDER:
        if _feature_enabled(connection, purpose):
            enabled_statuses.append(str(status_data.get(purpose) or "unknown"))
        else:
            status_data[purpose] = "disabled"

    if not enabled_statuses:
        status_data["available"] = "disabled"
    elif all(item == "up" for item in enabled_statuses):
        status_data["available"] = "up"
    elif any(item == "down" for item in enabled_statuses):
        status_data["available"] = "down"
    else:
        status_data["available"] = "unknown"
    return status_data


def refresh_service_connection_status(
    db: Session,
    connection_id: str,
    *,
    purpose: ServicePurpose | None = None,
) -> dict[str, Any]:
    """Probe enabled capabilities and persist only this row's health fields."""

    row = _require_connection_row(db, connection_id)
    connection = _row_to_connection(row)
    purposes: list[ServicePurpose]
    if purpose:
        purposes = [normalize_service_purpose(purpose)]
    else:
        purposes = [
            item for item in SERVICE_PURPOSE_ORDER if _feature_enabled(connection, item)
        ]

    status_data = _normalize_status(connection.get("status"), connection)
    messages: list[str] = []
    checked_at_value = utc_now()
    checked_at = checked_at_value.isoformat()
    for item in purposes:
        if not _feature_enabled(connection, item):
            status_data[item] = "disabled"
            status_data[f"{item}_auth"] = "disabled"
            continue
        item_status, message, auth_status, capabilities = _probe_service(
            db, connection, item
        )
        status_data[item] = item_status
        status_data[f"{item}_auth"] = auth_status
        status_data[f"{item}_capabilities"] = capabilities
        status_data[f"{item}_message"] = message
        if message and item_status != "up":
            messages.append(message)

    status_data["checked_at"] = checked_at
    status_data["message"] = "; ".join(messages)
    status_data = _aggregate_status(status_data, connection)

    saved = update_service_connection_status_row(
        db,
        connection_id=str(connection["id"]),
        status=status_data,
        updated_at=checked_at_value,
    )
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service connection not found",
        )
    return _row_to_connection(saved)


def record_service_connection_runtime_status(
    db: Session,
    connection: dict[str, Any],
    purpose: ServicePurpose,
    *,
    available: bool,
    message: str = "",
    failure_scope: RuntimeFailureScope = "request",
    capabilities: dict[str, bool] | None = None,
) -> None:
    """Persist runtime health without letting user requests poison global state.

    Runtime success follows a direct service health probe and can safely move a
    shared connection back to ``up``. A negative result is different: render,
    execute, timeout, and rate-limit errors can depend entirely on one user's
    payload. Those request-scoped failures are useful for local failover but
    must never become durable health state shared by every user.

    Callers may use ``failure_scope="service"`` only when the failure came
    directly from the service's dedicated health endpoint and therefore does
    not depend on the user's execution or rendering payload. Administrative
    status refreshes use their separate probe path and remain authoritative.
    """
    if not available and failure_scope != "service":
        return

    if connection.get("legacy"):
        return
    connection_id = str(connection.get("id") or "").strip()
    if not connection_id:
        return

    try:
        purpose = normalize_service_purpose(purpose)
        current_row = get_service_connection_row(db, connection_id)
        if current_row is None:
            return
        current = _row_to_connection(current_row)
        status_data = _normalize_status(current.get("status"), current)
        status_data[purpose] = "up" if available else "down"
        status_data[f"{purpose}_message"] = message
        if capabilities is not None:
            status_data[f"{purpose}_capabilities"] = {
                str(name): value
                for name, value in capabilities.items()
                if isinstance(name, str) and isinstance(value, bool)
            }
        checked_at_value = utc_now()
        status_data["checked_at"] = checked_at_value.isoformat()
        status_data["message"] = "" if available else message
        status_data = _aggregate_status(status_data, current)

        update_service_connection_status_row(
            db,
            connection_id=connection_id,
            status=status_data,
            updated_at=checked_at_value,
        )
    except Exception:
        logger.exception(
            "Failed to update runtime status for service connection %s", connection_id
        )
