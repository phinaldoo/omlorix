from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import signal
import subprocess
import tempfile
import logging
import sys
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.tools.custom.contract import CustomPythonToolContractError
from app.tools.custom.models import (
    CustomPythonTool,
    create_custom_python_tool,
    delete_custom_python_tool,
    get_custom_python_tool,
    get_custom_python_tool_by_name,
    list_custom_python_tools,
    update_custom_python_tool,
)


RUNNER_PATH = Path(__file__).resolve().with_name("python_runner.py")
BACKEND_DIR = RUNNER_PATH.parents[3]
RESERVED_TOOL_NAMES = {"mcp"}
logger = logging.getLogger(__name__)


class CustomPythonToolExecutionError(RuntimeError):
    """Represent failures while inspecting or executing trusted custom Python tools."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def _normalize_timeout(timeout_seconds: int | None) -> int:
    """Validate API-level timeout values before invoking the custom tool runner."""

    try:
        timeout_value = int(timeout_seconds or 30)
    except (TypeError, ValueError) as exc:
        raise CustomPythonToolContractError("timeout_seconds must be an integer.") from exc
    if timeout_value < 1 or timeout_value > 300:
        raise CustomPythonToolContractError("timeout_seconds must be between 1 and 300 seconds.")
    return timeout_value


def _ensure_tool_name_available(
    db: Session,
    name: str,
    *,
    exclude_tool_id: str | None = None,
) -> None:
    """Reject custom tool names that collide with reserved, built-in, or existing tool identifiers."""

    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise CustomPythonToolContractError("Tool name is required.")
    if normalized_name in RESERVED_TOOL_NAMES:
        raise CustomPythonToolContractError(f"'{normalized_name}' is a reserved tool name.")

    from app.tools.utils import available_tools

    if normalized_name in available_tools:
        raise CustomPythonToolContractError(
            f"'{normalized_name}' conflicts with a built-in tool. Choose a different name."
        )

    existing = get_custom_python_tool_by_name(db, normalized_name, enabled_only=False)
    if existing and str(existing.id) != str(exclude_tool_id or ""):
        raise CustomPythonToolContractError(
            f"A custom Python tool named '{normalized_name}' already exists."
        )


def _serialize_tool_record(tool: CustomPythonTool) -> dict[str, Any]:
    """Convert a model instance into the API shape used by router response models."""

    return {
        "id": str(tool.id),
        "name": str(tool.name),
        "display_name": str(tool.display_name),
        "description": str(tool.description),
        "enabled": bool(tool.enabled),
        "timeout_seconds": int(tool.timeout_seconds or 30),
        "tool_schema": tool.tool_schema if isinstance(tool.tool_schema, dict) else {},
        "source_code": getattr(tool, "source_code", ""),
        "created_at": tool.created_at,
        "updated_at": tool.updated_at,
    }


def _terminate_custom_python_runner(process: subprocess.Popen) -> None:
    """Terminate the runner and any child processes it spawned before raising timeout errors."""

    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows fallback for local development.
            process.kill()
    except ProcessLookupError:
        pass
    except Exception:
        logger.debug("Failed to terminate timed out custom Python runner.", exc_info=True)
    finally:
        try:
            process.communicate(timeout=1)
        except Exception:
            pass


def _run_python_tool_runner(
    *,
    mode: str,
    source_code: str,
    timeout_seconds: int,
    arguments: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute trusted custom Python in a subprocess with backend imports available."""

    payload = {
        "mode": mode,
        "source_code": source_code,
        "arguments": arguments or {},
        "context": context or {},
    }

    runner_env = dict(os.environ)
    runner_env["PYTHONIOENCODING"] = "utf-8"
    runner_env["PYTHONUTF8"] = "1"
    command = [sys.executable, str(RUNNER_PATH)]
    timeout_value = _normalize_timeout(timeout_seconds)
    try:
        with tempfile.TemporaryDirectory(prefix="omlorix-custom-tool-") as runner_cwd:
            # Custom tools are trusted code, but they should not accidentally
            # create or overwrite files in the application source tree.
            popen_kwargs: dict[str, Any] = {}
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=runner_cwd,
                env=runner_env,
                **popen_kwargs,
            )
            try:
                stdout, stderr = process.communicate(
                    json.dumps(payload, ensure_ascii=False),
                    timeout=timeout_value,
                )
            except subprocess.TimeoutExpired as exc:
                _terminate_custom_python_runner(process)
                raise CustomPythonToolExecutionError(
                    f"Custom Python tool exceeded the timeout of {timeout_seconds} seconds."
                ) from exc
            completed = subprocess.CompletedProcess(
                args=command,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
    except subprocess.TimeoutExpired as exc:
        raise CustomPythonToolExecutionError(
            f"Custom Python tool exceeded the timeout of {timeout_seconds} seconds."
        ) from exc
    except CustomPythonToolExecutionError:
        raise
    except Exception as exc:
        raise CustomPythonToolExecutionError("Failed to start the custom Python tool runner.") from exc

    stdout = (completed.stdout or "").strip()
    if not stdout:
        stderr = (completed.stderr or "").strip()
        raise CustomPythonToolExecutionError(stderr or "Custom Python tool runner produced no output.")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CustomPythonToolExecutionError("Custom Python tool runner returned invalid JSON output.") from exc

    if not isinstance(data, dict):
        raise CustomPythonToolExecutionError("Custom Python tool runner returned an invalid payload.")

    if not data.get("ok"):
        error_message = str(data.get("error") or "Custom Python tool execution failed.")
        raise CustomPythonToolExecutionError(
            error_message,
            code=str(data.get("error_code") or "") or None,
            path=str(data.get("error_path") or "") or None,
        )

    return data


def inspect_custom_python_tool_source(source_code: str) -> dict[str, Any]:
    """Normalize and validate the tool definition exported by arbitrary source code."""

    result = _run_python_tool_runner(
        mode="inspect",
        source_code=source_code,
        timeout_seconds=10,
    )
    definition = result.get("definition")
    if not isinstance(definition, dict):
        raise CustomPythonToolExecutionError("Custom Python tool inspection did not return a definition.")
    return definition


def execute_custom_python_tool_source(
    *,
    source_code: str,
    arguments: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Execute custom Python source code and return the normalized tool output payload."""

    result = _run_python_tool_runner(
        mode="execute",
        source_code=source_code,
        timeout_seconds=timeout_seconds,
        arguments=arguments or {},
        context=context or {},
    )
    output = result.get("output")
    if not isinstance(output, dict):
        raise CustomPythonToolExecutionError("Custom Python tool did not return a valid output payload.")
    return output


def list_custom_python_tool_payloads(db: Session, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    """Serialize stored custom Python tools for list responses or runtime resolution."""

    return [_serialize_tool_record(tool) for tool in list_custom_python_tools(db, enabled_only=enabled_only)]


def get_custom_python_tool_payload(db: Session, tool_id: str) -> dict[str, Any]:
    """Serialize one persisted custom Python tool for admin detail responses."""

    return _serialize_tool_record(get_custom_python_tool(db, tool_id))


def create_custom_python_tool_payload(
    db: Session,
    *,
    source_code: str,
    enabled: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Inspect, validate, and persist a newly submitted custom Python tool."""

    timeout_value = _normalize_timeout(timeout_seconds)
    definition = inspect_custom_python_tool_source(source_code)
    _ensure_tool_name_available(db, definition["name"])
    tool = create_custom_python_tool(
        db,
        name=definition["name"],
        display_name=definition["display_name"],
        description=definition["description"],
        source_code=source_code,
        tool_schema=definition,
        enabled=enabled,
        timeout_seconds=timeout_value,
    )
    return _serialize_tool_record(tool)


def update_custom_python_tool_payload(
    db: Session,
    tool_id: str,
    *,
    source_code: str,
    enabled: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Inspect, validate, and replace the stored definition for an existing custom Python tool."""

    timeout_value = _normalize_timeout(timeout_seconds)
    definition = inspect_custom_python_tool_source(source_code)
    _ensure_tool_name_available(db, definition["name"], exclude_tool_id=tool_id)
    tool = update_custom_python_tool(
        db,
        tool_id,
        name=definition["name"],
        display_name=definition["display_name"],
        description=definition["description"],
        source_code=source_code,
        tool_schema=definition,
        enabled=enabled,
        timeout_seconds=timeout_value,
    )
    return _serialize_tool_record(tool)


def delete_custom_python_tool_payload(db: Session, tool_id: str) -> dict[str, str]:
    """Delete a persisted custom Python tool and return a stable API confirmation payload."""

    delete_custom_python_tool(db, tool_id)
    return {"status": "success", "tool_id": tool_id}


def test_custom_python_tool_source(
    *,
    source_code: str,
    arguments: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Inspect and execute custom source using a deterministic admin test context."""

    timeout_value = _normalize_timeout(timeout_seconds)
    definition = inspect_custom_python_tool_source(source_code)
    output = execute_custom_python_tool_source(
        source_code=source_code,
        arguments=arguments or {},
        timeout_seconds=timeout_value,
        context={
            "user_id": "admin-test",
            "group_id": None,
            "project_id": None,
            "chat_id": None,
            "generation_id": None,
            "user_role": "admin",
            "model_settings": {},
            "invoked_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "definition": definition,
        "output": output,
    }


def list_enabled_custom_python_tool_names(db: Session) -> list[str]:
    """Expose enabled custom Python tool names for model/tool selection UIs."""

    return [item["name"] for item in list_custom_python_tool_payloads(db, enabled_only=True)]


def list_enabled_custom_python_tool_options(db: Session) -> list[dict[str, Any]]:
    """Expose enabled custom Python tools as UI option payloads with labels and schemas."""

    return [
        {
            "name": item["name"],
            "label": item["display_name"] or item["name"],
            "description": item["description"],
            "tool_schema": item["tool_schema"],
        }
        for item in list_custom_python_tool_payloads(db, enabled_only=True)
    ]


def get_enabled_custom_python_tool_schema(db: Session, tool_name: str) -> dict[str, Any] | None:
    """Return the schema for one enabled custom Python tool, if it exists."""

    tool = get_custom_python_tool_by_name(db, tool_name, enabled_only=True)
    if not tool or not isinstance(tool.tool_schema, dict):
        return None
    return dict(tool.tool_schema)


def _audit_custom_python_execution(
    *,
    user_id: str,
    action: str,
    details: dict[str, Any],
    required: bool,
) -> None:
    """Write one custom-runtime event without touching the caller's session."""

    from app.database import AuditSessionLocal
    from app.logging.models import create_audit_log

    audit_db = None
    try:
        audit_db = AuditSessionLocal()
        create_audit_log(
            db_log=audit_db,
            user_id=str(user_id or "system")[:64],
            action=action,
            details=details,
            user_agent="omlorix-tool",
            category="custom_python_tools",
        )
    except Exception as exc:
        if required:
            raise CustomPythonToolExecutionError(
                "Custom Python tool execution was blocked because its audit event could not be recorded.",
                code="custom_tool_audit_unavailable",
            ) from exc
        logger.exception("Failed to record custom Python tool execution outcome")
    finally:
        if audit_db is not None:
            audit_db.close()


def execute_enabled_custom_python_tool(
    db: Session,
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Execute one enabled custom Python tool by name for the runtime tool dispatcher."""

    tool = get_custom_python_tool_by_name(db, tool_name, enabled_only=True)
    if not tool:
        return None
    runtime_context = context if isinstance(context, dict) else {}
    invocation_id = uuid.uuid4().hex
    audit_details = {
        "custom_tool_id": str(tool.id)[:128],
        "tool_name": str(tool.name)[:128],
        "invocation_id": invocation_id,
        "chat_id": str(runtime_context.get("chat_id") or "")[:128] or None,
        "generation_id": str(runtime_context.get("generation_id") or "")[:128] or None,
        "source": "tool",
    }
    actor_user_id = str(runtime_context.get("user_id") or "system")[:64]
    _audit_custom_python_execution(
        user_id=actor_user_id,
        action="CUSTOM_PYTHON_TOOL_EXECUTION_STARTED",
        details=audit_details,
        required=True,
    )
    try:
        output = execute_custom_python_tool_source(
            source_code=tool.source_code,
            arguments=arguments or {},
            timeout_seconds=int(tool.timeout_seconds or 30),
            context=runtime_context,
        )
    except Exception as exc:
        _audit_custom_python_execution(
            user_id=actor_user_id,
            action="CUSTOM_PYTHON_TOOL_EXECUTION_FAILED",
            details={
                **audit_details,
                "failure_kind": (
                    "execution_error"
                    if isinstance(exc, CustomPythonToolExecutionError)
                    else "internal_error"
                ),
            },
            required=False,
        )
        raise
    _audit_custom_python_execution(
        user_id=actor_user_id,
        action="CUSTOM_PYTHON_TOOL_EXECUTION_SUCCEEDED",
        details=audit_details,
        required=False,
    )
    return output


def raise_custom_tool_http_error(exc: Exception) -> None:
    """Convert internal custom-tool errors into a consistent HTTP 400 response contract."""

    if isinstance(exc, HTTPException):
        raise exc
    if (
        isinstance(exc, CustomPythonToolExecutionError)
        and exc.code in {"custom_tool_argument_required", "custom_tool_argument_invalid"}
        and exc.path
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": exc.code,
                "path": exc.path,
            },
        ) from exc
    detail = str(exc) or "Custom Python tool request failed."
    raise HTTPException(status_code=400, detail=detail) from exc
