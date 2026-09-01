from __future__ import annotations

import ast
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.custom import router as custom_tool_router  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]


def _frontend_default_source() -> str:
    frontend_source = (REPO_ROOT / "frontend/js/admin/customPythonTools.js").read_text(encoding="utf-8")
    source_lines = frontend_source.split("    const defaultSourceCode = [", 1)[1].split(
        "    ].join('\\n');",
        1,
    )[0]
    return "\n".join(
        ast.literal_eval(line.strip().removesuffix(","))
        for line in source_lines.splitlines()
        if line.strip()
    )


def _exported_tool() -> dict[str, object]:
    return {
        "id": "tool-source",
        "name": "portable_tool",
        "display_name": "Portable Tool",
        "description": "Round-trip fixture",
        "enabled": False,
        "timeout_seconds": 45,
        "tool_schema": {"type": "object", "properties": {}},
        "source_code": "TOOL_DEFINITION = {'name': 'portable_tool'}\n\ndef run_tool(arguments, context):\n    return {'content': 'ok'}\n",
        "created_at": None,
        "updated_at": None,
    }


def test_fresh_custom_python_tool_export_is_accepted_by_import(monkeypatch):
    """A payload downloaded from the export API must pass the import API unchanged."""

    exported_tool = _exported_tool()
    created_tool = {
        **exported_tool,
        "id": "tool-imported",
    }
    monkeypatch.setattr(
        custom_tool_router,
        "list_custom_python_tool_payloads",
        lambda _db: [exported_tool],
    )
    monkeypatch.setattr(
        custom_tool_router,
        "create_custom_python_tool_payload",
        lambda _db, **_kwargs: created_tool,
    )
    monkeypatch.setattr(
        custom_tool_router,
        "_audit_custom_python_tool_admin_action",
        lambda **_kwargs: None,
    )

    app = FastAPI()
    app.include_router(custom_tool_router.custom_python_tools_router)
    app.dependency_overrides[custom_tool_router.verified_admin] = lambda: object()
    app.dependency_overrides[custom_tool_router.get_db] = lambda: object()
    app.dependency_overrides[custom_tool_router.get_db_log] = lambda: object()

    with TestClient(app) as client:
        export_response = client.get("/api/v1/custom-tools/admin/export")
        contract_response = client.get("/api/v1/custom-tools/admin/import-contract")
        import_response = client.post(
            "/api/v1/custom-tools/admin/import",
            json=export_response.json(),
        )

    assert export_response.status_code == 200
    assert contract_response.status_code == 200
    assert import_response.status_code == 200
    export_payload = export_response.json()
    import_contract = contract_response.json()
    result = import_response.json()

    assert export_payload["export_type"] == import_contract["export_type"]
    assert export_payload["export_version"] == import_contract["export_version"]
    assert result == {
        "created": [
            {
                "tool_id": "tool-imported",
                "tool_name": "portable_tool",
                "display_name": "Portable Tool",
                "enabled": False,
                "timeout_seconds": 45,
            }
        ],
        "errors": [],
    }


def test_frontend_default_custom_python_tool_passes_admin_test_api(monkeypatch):
    """The source prefilled by the create form must be valid in the production runner."""

    monkeypatch.setattr(
        custom_tool_router,
        "_audit_custom_python_tool_admin_action",
        lambda **_kwargs: None,
    )

    app = FastAPI()
    app.include_router(custom_tool_router.custom_python_tools_router)
    app.dependency_overrides[custom_tool_router.verified_admin] = lambda: object()
    app.dependency_overrides[custom_tool_router.get_db_log] = lambda: object()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/custom-tools/admin/test",
            json={
                "source_code": _frontend_default_source(),
                "arguments": {"message": "hello"},
                "timeout_seconds": 30,
            },
        )
        missing_argument_response = client.post(
            "/api/v1/custom-tools/admin/test",
            json={
                "source_code": _frontend_default_source(),
                "arguments": {},
                "timeout_seconds": 30,
            },
        )
        wrong_type_response = client.post(
            "/api/v1/custom-tools/admin/test",
            json={
                "source_code": _frontend_default_source(),
                "arguments": {"message": 7},
                "timeout_seconds": 30,
            },
        )

    assert response.status_code == 200
    result = response.json()
    assert result["definition"]["parameters"]["additionalProperties"] is False
    assert result["output"]["result"] == {
        "echo": "hello",
        "user_id": "admin-test",
    }
    assert missing_argument_response.status_code == 400
    assert missing_argument_response.json() == {
        "detail": {
            "code": "custom_tool_argument_required",
            "path": "arguments.message",
        }
    }
    assert wrong_type_response.status_code == 400
    assert wrong_type_response.json() == {
        "detail": {
            "code": "custom_tool_argument_invalid",
            "path": "arguments.message",
        }
    }
