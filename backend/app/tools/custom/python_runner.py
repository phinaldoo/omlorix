from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from collections.abc import Awaitable
from io import StringIO
from pathlib import Path
from typing import Any
import asyncio
import json
import sys
import traceback


BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.tools.custom.contract import (  # noqa: E402
    CustomPythonToolContractError,
    normalize_tool_definition,
    normalize_tool_output,
    validate_tool_arguments_against_schema,
)


def _load_payload() -> dict[str, Any]:
    """Read and validate the JSON payload sent to the trusted custom-tool runner."""

    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("Missing runner payload.")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Runner payload must be a JSON object.")
    return payload


def _resolve_tool_namespace(source_code: str) -> tuple[dict[str, Any], str, str]:
    """Compile the submitted source code and capture import-time stdout and stderr."""

    namespace: dict[str, Any] = {"__name__": "__custom_python_tool__"}
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
        compiled = compile(source_code, "<custom_python_tool>", "exec")
        exec(compiled, namespace, namespace)
    return namespace, stdout_buffer.getvalue(), stderr_buffer.getvalue()


def _resolve_tool_definition(namespace: dict[str, Any]) -> dict[str, Any]:
    """Load and normalize the tool definition exported by the executed namespace."""

    if isinstance(namespace.get("TOOL_DEFINITION"), dict):
        return normalize_tool_definition(namespace["TOOL_DEFINITION"])
    get_definition = namespace.get("get_tool_definition")
    if callable(get_definition):
        return normalize_tool_definition(get_definition())
    raise CustomPythonToolContractError(
        "Custom Python tool source must define TOOL_DEFINITION or get_tool_definition()."
    )


def _run_tool_callable(tool_callable, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    """Execute sync tools inline and only create an event loop for true awaitable results."""

    result = tool_callable(arguments, context)
    if isinstance(result, Awaitable):
        return asyncio.run(result)
    return result


def _emit_success(payload: dict[str, Any]) -> None:
    """Emit a successful runner response as JSON on stdout."""

    sys.stdout.write(json.dumps({"ok": True, **payload}, ensure_ascii=False))


def _emit_error(message: str, *, details: dict[str, Any] | None = None) -> None:
    """Emit a structured runner error response without crashing the parent process."""

    payload = {
        "ok": False,
        "error": message,
    }
    if details:
        payload.update(details)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    """Execute inspect or run mode for trusted custom Python tools."""

    try:
        payload = _load_payload()
        source_code = str(payload.get("source_code") or "")
        if not source_code.strip():
            raise CustomPythonToolContractError("source_code is required.")

        namespace, import_stdout, import_stderr = _resolve_tool_namespace(source_code)
        definition = _resolve_tool_definition(namespace)

        mode = str(payload.get("mode") or "inspect").strip().lower()
        if mode == "inspect":
            _emit_success(
                {
                    "definition": definition,
                    "import_stdout": import_stdout,
                    "import_stderr": import_stderr,
                }
            )
            return 0

        if mode != "execute":
            raise CustomPythonToolContractError(f"Unsupported runner mode '{mode}'.")

        tool_callable = namespace.get("run_tool")
        if not callable(tool_callable):
            raise CustomPythonToolContractError("Custom Python tool source must define run_tool(arguments, context).")

        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise CustomPythonToolContractError("arguments must be an object.")
        context = payload.get("context") or {}
        if not isinstance(context, dict):
            raise CustomPythonToolContractError("context must be an object.")

        validate_tool_arguments_against_schema(arguments, definition["parameters"])

        tool_stdout = StringIO()
        tool_stderr = StringIO()
        with redirect_stdout(tool_stdout), redirect_stderr(tool_stderr):
            raw_output = _run_tool_callable(tool_callable, arguments, context)

        normalized_output = normalize_tool_output(
            raw_output,
            stdout="\n".join(filter(None, [import_stdout.strip(), tool_stdout.getvalue().strip()])),
            stderr="\n".join(filter(None, [import_stderr.strip(), tool_stderr.getvalue().strip()])),
        )
        _emit_success(
            {
                "definition": definition,
                "output": normalized_output,
            }
        )
        return 0
    except Exception as exc:
        details = {
            "traceback": traceback.format_exc(),
        }
        error_code = getattr(exc, "code", None)
        error_path = getattr(exc, "path", None)
        if error_code:
            details["error_code"] = str(error_code)
        if error_path:
            details["error_path"] = str(error_path)
        _emit_error(
            str(exc),
            details=details,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
