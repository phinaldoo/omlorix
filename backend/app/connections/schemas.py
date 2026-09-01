from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.connections.models import (
    PROVIDER_GITHUB,
    PROVIDER_GMAIL,
    PROVIDER_GOOGLE_CALENDAR,
    PROVIDER_GOOGLE_DRIVE,
    PROVIDER_NOTION,
    PROVIDER_SLACK,
)
from app.mcp.schemas import MCPToolDescriptor


ConnectionProvider = Literal[
    PROVIDER_NOTION,
    PROVIDER_GITHUB,
    PROVIDER_GMAIL,
    PROVIDER_GOOGLE_CALENDAR,
    PROVIDER_GOOGLE_DRIVE,
    PROVIDER_SLACK,
]


class ConnectionUpdateRequest(BaseModel):
    enabled: bool = True
    access_token: str | None = Field(default=None, min_length=1, max_length=4096)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ConnectionCreateRequest(BaseModel):
    access_token: str = Field(min_length=1, max_length=4096)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ConnectionStatusPayload(BaseModel):
    state: str
    last_error: str = ""
    last_error_code: str = ""
    tool_count: int = 0
    tool_names: list[str] = Field(default_factory=list)
    checked_at: str | None = None
    connected_at: str | None = None
    last_sync_at: str | None = None


class UserConnectionResponse(BaseModel):
    id: str
    provider: ConnectionProvider
    enabled: bool
    connected: bool
    mcp_server_id: str | None = None
    auth_mode: Literal["oauth", "pat"]
    status: ConnectionStatusPayload
    created_at: str | None = None
    updated_at: str | None = None
    connected_at: str | None = None


class ConnectionCatalogConnection(BaseModel):
    """Minimal user-owned state needed to render and mutate a catalog row.

    Provider IDs, MCP server IDs, credential modes, operational timestamps,
    raw errors, and tool inventories belong to other purpose-specific APIs and
    are intentionally excluded from the general catalog response.
    """

    id: str
    enabled: bool
    connected: bool
    state: Literal["not_connected", "connected", "error", "reauthorization_required"]
    error_code: Literal[
        "github_token_invalid",
        "github_access_denied",
        "mcp_authentication_failed",
        "mcp_access_denied",
        "mcp_connection_failed",
    ] | None = None

    model_config = ConfigDict(extra="forbid")


class ConnectionCatalogItem(BaseModel):
    """Compact provider capability and optional per-user connection state."""

    provider: ConnectionProvider
    setup_mode: Literal["oauth", "choice", "token"]
    connection_type: Literal["mcp", "file_source_adapter"] = "mcp"
    connection: ConnectionCatalogConnection | None = None

    model_config = ConfigDict(extra="forbid")


class ConnectionCatalogResponse(BaseModel):
    items: list[ConnectionCatalogItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ConnectionToolPreviewResponse(BaseModel):
    connection: UserConnectionResponse
    tools: list[MCPToolDescriptor] = Field(default_factory=list)
    source: str = "bridge"
