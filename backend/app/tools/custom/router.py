from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.tools.custom.schemas import (
    CustomPythonToolDeleteResponse,
    CustomPythonToolDetail,
    CustomPythonToolImportContract,
    CustomPythonToolListItem,
    CustomPythonToolMutationRequest,
    CustomPythonToolTestRequest,
    CustomPythonToolTestResponse,
)
from app.tools.custom.utils import (
    create_custom_python_tool_payload,
    delete_custom_python_tool_payload,
    get_custom_python_tool_payload,
    list_custom_python_tool_payloads,
    raise_custom_tool_http_error,
    test_custom_python_tool_source,
    update_custom_python_tool_payload,
)


custom_python_tools_router = APIRouter(prefix="/api/v1/custom-tools", tags=["custom-tools"])
custom_python_tools_admin_router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(verified_admin)],
)

AUDIT_CATEGORY = "custom_python_tools"
CURRENT_CUSTOM_PYTHON_TOOL_EXPORT_VERSION = 1.0


def _audit_custom_python_tool_admin_action(
    *,
    request: Request,
    db_log: Session,
    admin_user: Any,
    action: str,
    details: dict[str, Any],
) -> None:
    """Write a compliance-focused audit entry for an admin custom-tool operation."""

    create_audit_log(
        db_log=db_log,
        user_id=str(getattr(admin_user, "id", "") or "") or None,
        action=action,
        details=details,
        ip_address=get_audit_request_ip(request),
        user_agent=request.headers.get("user-agent"),
        category=AUDIT_CATEGORY,
    )


def _tool_audit_snapshot(tool_payload: dict[str, Any]) -> dict[str, Any]:
    """Extract non-sensitive tool metadata that is safe to persist in audit logs."""

    return {
        "tool_id": tool_payload.get("id"),
        "tool_name": tool_payload.get("name"),
        "display_name": tool_payload.get("display_name"),
        "enabled": tool_payload.get("enabled"),
        "timeout_seconds": tool_payload.get("timeout_seconds"),
    }


def _safe_custom_tool_failure_details(
    *,
    tool_id: str | None = None,
    status_code: int | None = None,
    failure_kind: str,
) -> dict[str, Any]:
    """Return fixed failure metadata without retaining runner-controlled text."""

    details: dict[str, Any] = {"failure_kind": failure_kind}
    if tool_id:
        details["tool_id"] = str(tool_id)[:128]
    if status_code is not None:
        details["status_code"] = int(status_code)
    return details


@custom_python_tools_admin_router.get("", response_model=list[CustomPythonToolListItem])
def list_admin_custom_python_tools_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: Any = Depends(verified_admin),
) -> list[dict[str, Any]]:
    """List all stored custom Python tools available to administrators."""

    tools = list_custom_python_tool_payloads(db)
    _audit_custom_python_tool_admin_action(
        request=request,
        db_log=db_log,
        admin_user=admin_user,
        action="CUSTOM_PYTHON_TOOL_LIST",
        details={"tool_count": len(tools)},
    )
    return tools


@custom_python_tools_admin_router.get("/export")
def export_admin_custom_python_tools_route(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: Any = Depends(verified_admin),
) -> dict[str, Any]:
    """Export all admin custom Python tools in a portable JSON bundle."""

    tools = list_custom_python_tool_payloads(db)
    payload = {
        "export_type": "custom_python_tool",
        "export_version": CURRENT_CUSTOM_PYTHON_TOOL_EXPORT_VERSION,
        "data": {
            "tools": tools,
        },
    }
    _audit_custom_python_tool_admin_action(
        request=request,
        db_log=db_log,
        admin_user=admin_user,
        action="CUSTOM_PYTHON_TOOL_EXPORT",
        details={"tool_count": len(tools)},
    )
    return payload


@custom_python_tools_admin_router.get(
    "/import-contract",
    response_model=CustomPythonToolImportContract,
)
def get_admin_custom_python_tools_import_contract_route() -> dict[str, Any]:
    """Return the export envelope supported by this backend deployment."""

    return {
        "export_type": "custom_python_tool",
        "export_version": CURRENT_CUSTOM_PYTHON_TOOL_EXPORT_VERSION,
    }


@custom_python_tools_admin_router.post("/import")
def import_admin_custom_python_tools_route(
    payload: dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: Any = Depends(verified_admin),
) -> dict[str, Any]:
    """Import one or more admin custom Python tools from an export bundle."""

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid import payload. Expected an object.")

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")
    if export_type != "custom_python_tool":
        raise HTTPException(status_code=400, detail=f"Unsupported export_type '{export_type}'.")
    if export_version != CURRENT_CUSTOM_PYTHON_TOOL_EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported export_version '{export_version}'. "
                f"Expected '{CURRENT_CUSTOM_PYTHON_TOOL_EXPORT_VERSION}'."
            ),
        )

    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise HTTPException(status_code=400, detail="Invalid export payload. Missing 'data' object.")
    raw_tools = data_block.get("tools")
    if not isinstance(raw_tools, list):
        raise HTTPException(status_code=400, detail="Invalid export payload. 'tools' must be a list.")

    _audit_custom_python_tool_admin_action(
        request=request,
        db_log=db_log,
        admin_user=admin_user,
        action="CUSTOM_PYTHON_TOOL_IMPORT_STARTED",
        details={"tool_count": len(raw_tools)},
    )

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, tool_entry in enumerate(raw_tools):
        if not isinstance(tool_entry, dict):
            errors.append({"index": index, "error": "Tool entry must be an object."})
            continue

        source_code = tool_entry.get("source_code")
        enabled = bool(tool_entry.get("enabled", True))
        timeout_seconds = tool_entry.get("timeout_seconds", 30)
        tool_name = (
            str(tool_entry.get("display_name") or "").strip()
            or str(tool_entry.get("name") or "").strip()
        )

        if not isinstance(source_code, str) or not source_code.strip():
            errors.append({"index": index, "name": tool_name, "error": "source_code is required."})
            continue

        try:
            created_tool = create_custom_python_tool_payload(
                db,
                source_code=source_code,
                enabled=enabled,
                timeout_seconds=int(timeout_seconds or 30),
            )
            created.append(_tool_audit_snapshot(created_tool))
        except HTTPException as exc:
            errors.append({"index": index, "name": tool_name, "error": exc.detail})
        except Exception as exc:
            errors.append({"index": index, "name": tool_name, "error": str(exc)})

    result = {"created": created, "errors": errors}
    _audit_custom_python_tool_admin_action(
        request=request,
        db_log=db_log,
        admin_user=admin_user,
        action="CUSTOM_PYTHON_TOOL_IMPORT",
        details={
            "created_count": len(created),
            "error_count": len(errors),
        },
    )
    return result


@custom_python_tools_admin_router.get("/{tool_id}", response_model=CustomPythonToolDetail)
def get_admin_custom_python_tool_route(
    tool_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: Any = Depends(verified_admin),
) -> dict[str, Any]:
    """Return one stored custom Python tool including its source code for admin review."""

    try:
        tool = get_custom_python_tool_payload(db, tool_id)
    except HTTPException as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_GET_FAILED",
            details=_safe_custom_tool_failure_details(
                tool_id=tool_id,
                status_code=exc.status_code,
                failure_kind="request_error",
            ),
        )
        raise

    _audit_custom_python_tool_admin_action(
        request=request,
        db_log=db_log,
        admin_user=admin_user,
        action="CUSTOM_PYTHON_TOOL_GET",
        details=_tool_audit_snapshot(tool),
    )
    return tool


@custom_python_tools_admin_router.post(
    "",
    response_model=CustomPythonToolDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_admin_custom_python_tool_route(
    payload: CustomPythonToolMutationRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: Any = Depends(verified_admin),
) -> dict[str, Any]:
    """Create a new custom Python tool after validating its definition and timeout contract."""

    _audit_custom_python_tool_admin_action(
        request=request,
        db_log=db_log,
        admin_user=admin_user,
        action="CUSTOM_PYTHON_TOOL_CREATE_STARTED",
        details={
            "enabled": payload.enabled,
            "timeout_seconds": payload.timeout_seconds,
        },
    )
    try:
        tool = create_custom_python_tool_payload(
            db,
            source_code=payload.source_code,
            enabled=payload.enabled,
            timeout_seconds=payload.timeout_seconds,
        )
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_CREATE",
            details=_tool_audit_snapshot(tool),
        )
        return tool
    except HTTPException as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_CREATE_FAILED",
            details={
                "enabled": payload.enabled,
                "timeout_seconds": payload.timeout_seconds,
                **_safe_custom_tool_failure_details(
                    status_code=exc.status_code,
                    failure_kind="request_error",
                ),
            },
        )
        raise
    except Exception as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_CREATE_FAILED",
            details={
                "enabled": payload.enabled,
                "timeout_seconds": payload.timeout_seconds,
                "failure_kind": "execution_error",
            },
        )
        raise_custom_tool_http_error(exc)

@custom_python_tools_admin_router.patch("/{tool_id}", response_model=CustomPythonToolDetail)
def update_admin_custom_python_tool_route(
    tool_id: str,
    payload: CustomPythonToolMutationRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: Any = Depends(verified_admin),
) -> dict[str, Any]:
    """Replace an existing custom Python tool definition while preserving its identifier."""

    _audit_custom_python_tool_admin_action(
        request=request,
        db_log=db_log,
        admin_user=admin_user,
        action="CUSTOM_PYTHON_TOOL_UPDATE_STARTED",
        details={
            "tool_id": tool_id,
            "enabled": payload.enabled,
            "timeout_seconds": payload.timeout_seconds,
        },
    )
    try:
        tool = update_custom_python_tool_payload(
            db,
            tool_id,
            source_code=payload.source_code,
            enabled=payload.enabled,
            timeout_seconds=payload.timeout_seconds,
        )
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_UPDATE",
            details=_tool_audit_snapshot(tool),
        )
        return tool
    except HTTPException as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_UPDATE_FAILED",
            details={
                "tool_id": tool_id,
                "enabled": payload.enabled,
                "timeout_seconds": payload.timeout_seconds,
                **_safe_custom_tool_failure_details(
                    status_code=exc.status_code,
                    failure_kind="request_error",
                ),
            },
        )
        raise
    except Exception as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_UPDATE_FAILED",
            details={
                "tool_id": tool_id,
                "enabled": payload.enabled,
                "timeout_seconds": payload.timeout_seconds,
                "failure_kind": "execution_error",
            },
        )
        raise_custom_tool_http_error(exc)

@custom_python_tools_admin_router.delete("/{tool_id}", response_model=CustomPythonToolDeleteResponse)
def delete_admin_custom_python_tool_route(
    tool_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: Any = Depends(verified_admin),
) -> dict[str, str]:
    """Delete a stored custom Python tool and record which definition was removed."""

    try:
        existing_tool = get_custom_python_tool_payload(db, tool_id)
        result = delete_custom_python_tool_payload(db, tool_id)
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_DELETE",
            details=_tool_audit_snapshot(existing_tool),
        )
        return result
    except HTTPException as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_DELETE_FAILED",
            details=_safe_custom_tool_failure_details(
                tool_id=tool_id,
                status_code=exc.status_code,
                failure_kind="request_error",
            ),
        )
        raise
    except Exception as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_DELETE_FAILED",
            details=_safe_custom_tool_failure_details(
                tool_id=tool_id,
                failure_kind="internal_error",
            ),
        )
        raise_custom_tool_http_error(exc)


@custom_python_tools_admin_router.post("/test", response_model=CustomPythonToolTestResponse)
def test_admin_custom_python_tool_route(
    payload: CustomPythonToolTestRequest,
    request: Request,
    db_log: Session = Depends(get_db_log),
    admin_user: Any = Depends(verified_admin),
) -> dict[str, Any]:
    """Inspect and execute custom tool source against an isolated admin test context."""

    _audit_custom_python_tool_admin_action(
        request=request,
        db_log=db_log,
        admin_user=admin_user,
        action="CUSTOM_PYTHON_TOOL_TEST_STARTED",
        details={
            "timeout_seconds": payload.timeout_seconds,
            "argument_count": len(payload.arguments),
        },
    )
    try:
        result = test_custom_python_tool_source(
            source_code=payload.source_code,
            arguments=payload.arguments,
            timeout_seconds=payload.timeout_seconds,
        )
        definition = result.get("definition") if isinstance(result, dict) else {}
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_TEST",
            details={
                "tool_name": definition.get("name"),
                "timeout_seconds": payload.timeout_seconds,
                "argument_count": len(payload.arguments),
            },
        )
        return result
    except HTTPException as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_TEST_FAILED",
            details={
                "timeout_seconds": payload.timeout_seconds,
                "argument_count": len(payload.arguments),
                **_safe_custom_tool_failure_details(
                    status_code=exc.status_code,
                    failure_kind="request_error",
                ),
            },
        )
        raise
    except Exception as exc:
        _audit_custom_python_tool_admin_action(
            request=request,
            db_log=db_log,
            admin_user=admin_user,
            action="CUSTOM_PYTHON_TOOL_TEST_FAILED",
            details={
                "timeout_seconds": payload.timeout_seconds,
                "argument_count": len(payload.arguments),
                "failure_kind": "execution_error",
            },
        )
        raise_custom_tool_http_error(exc)


custom_python_tools_router.include_router(custom_python_tools_admin_router)
