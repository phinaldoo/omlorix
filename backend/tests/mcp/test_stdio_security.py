from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.mcp import models as mcp_models
from app.mcp import utils as mcp_utils
from app.mcp.models import OWNER_ADMIN, OWNER_USER, TRANSPORT_STDIO
from app.mcp.schemas import CreateMCPServerRequest


def _stdio_server(**overrides):
    """Build the minimum runtime object used by packaged-worker checks."""

    values = {
        "owner_type": OWNER_USER,
        "managed_connection_id": "connection-1",
        "command": "/usr/local/bin/google-workspace-worker",
        "args": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_public_request_schema_rejects_stdio_and_retired_process_fields():
    """Public MCP requests cannot describe a server-local process."""

    with pytest.raises(ValidationError):
        CreateMCPServerRequest(
            owner_type=OWNER_ADMIN,
            name="Local MCP",
            transport=TRANSPORT_STDIO,
            command="/bin/sh",
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CreateMCPServerRequest(
            owner_type=OWNER_ADMIN,
            name="Remote MCP",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            env={"TOKEN": "secret"},
        )


def test_model_layer_rejects_unmanaged_stdio_server():
    """Direct helper callers cannot bypass the public remote-only schema."""

    with pytest.raises(HTTPException, match="reserved for Omlorix-managed connections"):
        mcp_models.create_mcp_server(
            object(),
            owner_type=OWNER_ADMIN,
            owner_user_id=None,
            name="Local MCP",
            description=None,
            namespace=None,
            transport=TRANSPORT_STDIO,
            enabled=True,
            url=None,
            command="/bin/sh",
            args=[],
            headers={},
            env={},
            allowed_tools=[],
            timeout_seconds=30,
        )


def test_admin_import_rejects_legacy_stdio_entry():
    """Old export files cannot recreate a configurable local process."""

    bundle = {
        "export_type": "mcp_server",
        "export_version": mcp_utils.current_admin_mcp_server_export_version,
        "data": {
            "servers": [
                {
                    "name": "Imported stdio",
                    "transport": TRANSPORT_STDIO,
                    "enabled": True,
                    "command": "/bin/sh",
                }
            ]
        },
    }

    result = mcp_utils.import_admin_servers_bundle(object(), bundle)

    assert result["created"] == []
    assert len(result["errors"]) == 1


def test_stdio_launch_allows_managed_packaged_worker():
    """The internal Google Workspace connection keeps its packaged worker."""

    assert mcp_utils._stdio_launch_parameters(_stdio_server()) == (
        "/usr/local/bin/google-workspace-worker",
        [],
    )


@pytest.mark.parametrize(
    "server",
    [
        _stdio_server(owner_type=OWNER_ADMIN),
        _stdio_server(managed_connection_id=None),
        _stdio_server(command="/opt/mcp/custom"),
        _stdio_server(args=["--configurable"]),
    ],
)
def test_stdio_launch_rejects_every_configurable_process(server):
    """Runtime checks reject custom commands, ownership, and arguments."""

    with pytest.raises(ValueError):
        mcp_utils._stdio_launch_parameters(server)
