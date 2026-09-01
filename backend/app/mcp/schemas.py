from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.mcp.models import (
    OWNER_ADMIN,
    OWNER_USER,
    TRANSPORT_SSE,
    TRANSPORT_STREAMABLE_HTTP,
)


OwnerType = Literal[OWNER_ADMIN, OWNER_USER]
TransportType = Literal[TRANSPORT_STREAMABLE_HTTP, TRANSPORT_SSE]
AuthModeType = Literal["headers", "oauth"]


class MCPServerPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    icon: str | None = Field(default=None, max_length=50000)
    description: str | None = Field(default=None, max_length=4000)
    namespace: str | None = Field(default=None, max_length=64)
    transport: TransportType
    enabled: bool = True
    url: str | None = Field(default=None, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    auth_mode: AuthModeType = "headers"
    allowed_tools: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=30, ge=1, le=600)

    # Reject retired local-process fields instead of silently ignoring them.
    # This keeps hand-written API requests and old import bundles from
    # reintroducing configurable server-side command execution.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _normalize_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = value.replace(",", "\n").splitlines()
        elif isinstance(value, (list, tuple, set)):
            items = list(value)
        else:
            raise TypeError("Expected a string list")
        result: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if text:
                result.append(text)
        if len(result) > 500:
            raise ValueError("MCP list fields may contain at most 500 entries.")
        if any(len(item) > 2048 for item in result):
            raise ValueError("MCP list entries may contain at most 2048 characters.")
        return result

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_maps(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("Expected a string map")
        result: dict[str, str] = {}
        for key, item in value.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            value_text = str(item or "").strip()
            if value_text:
                result[key_text] = value_text
        if len(result) > 100:
            raise ValueError("MCP secret maps may contain at most 100 entries.")
        if any(len(key) > 256 or len(item) > 8192 for key, item in result.items()):
            raise ValueError("MCP secret map keys or values are too long.")
        return result

    @field_validator("url")
    @classmethod
    def _validate_remote_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        parsed = urlparse(normalized)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Remote MCP server URLs must be valid HTTP or HTTPS URLs without embedded credentials.")
        return normalized

    @field_validator("namespace")
    @classmethod
    def _validate_namespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().lower()
        if not text:
            return None
        cleaned = "".join(ch if (ch.isalnum() or ch in {"_", "-"}) else "_" for ch in text)
        return cleaned[:64].strip("_-") or None

    @model_validator(mode="after")
    def _validate_transport(self):
        if not self.url:
            raise ValueError("Remote MCP servers require a URL.")
        return self


class CreateMCPServerRequest(MCPServerPayload):
    owner_type: OwnerType


class UpdateMCPServerRequest(MCPServerPayload):
    pass


class MCPSecretSummary(BaseModel):
    header_count: int = 0
    updated_at: str | None = None
    secrets_included: bool = False
    oauth_connected: bool = False
    oauth_issuer: str | None = None


class MCPServerListItem(BaseModel):
    id: str
    owner_type: OwnerType
    owner_user_id: str | None = None
    name: str
    icon: str = ""
    description: str | None = None
    namespace: str | None = None
    transport: TransportType
    enabled: bool
    url: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    auth_mode: AuthModeType = "headers"
    timeout_seconds: int
    status: dict[str, Any] = Field(default_factory=dict)
    secret_summary: MCPSecretSummary = Field(default_factory=MCPSecretSummary)
    created_at: str | None = None
    updated_at: str | None = None


class MCPMentionConnector(BaseModel):
    """Safe, minimal MCP metadata rendered in the chat mention menu."""

    id: str
    name: str
    provider: str = ""
    icon: str = ""
    description: str = ""


class MCPServerDetail(MCPServerListItem):
    headers: dict[str, str] = Field(default_factory=dict)


class MCPServerTestRequest(MCPServerPayload):
    owner_type: OwnerType | None = None
    server_id: str | None = Field(default=None, max_length=255)


class MCPToolDescriptor(BaseModel):
    public_name: str
    tool_name: str
    description: str | None = None
    server_id: str
    server_name: str
    transport: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPToolPreviewResponse(BaseModel):
    tools: list[MCPToolDescriptor] = Field(default_factory=list)
    source: str = "bridge"


class MCPOAuthStartResponse(BaseModel):
    """Authorization URL returned when a saved remote server starts OAuth."""

    authorization_url: str


class MCPAppServerRequest(BaseModel):
    server_id: str = Field(min_length=1, max_length=255)
    app_access_token: str = Field(min_length=1, max_length=8192)
    tool_call_id: str | None = Field(default=None, max_length=255)
    access_server_ids: list[str] = Field(default_factory=list)

    @field_validator("tool_call_id", mode="before")
    @classmethod
    def _normalize_tool_call_id(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text[:255] or None

    @field_validator("access_server_ids", mode="before")
    @classmethod
    def _normalize_access_server_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, (list, tuple, set)):
            items = list(value)
        else:
            raise TypeError("Expected a string list")
        result: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if text:
                result.append(text[:255])
        result = list(dict.fromkeys(result))
        if len(result) > 100:
            raise ValueError("MCP app access may include at most 100 servers.")
        return result


class MCPAppResourceReadRequest(MCPAppServerRequest):
    uri: str = Field(min_length=1, max_length=4096)


class MCPAppFrameCreateRequest(MCPAppServerRequest):
    html: str = Field(min_length=1, max_length=5_000_000)
    resource_meta: dict[str, Any] = Field(default_factory=dict)


class MCPAppFrameCreateResponse(BaseModel):
    frame_id: str
    frame_url: str


class MCPAppTokenRefreshResponse(BaseModel):
    app_access_token: str


class MCPAppToolCallRequest(MCPAppServerRequest):
    tool_name: str = Field(min_length=1, max_length=255)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def _bound_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")) > 1_000_000:
            raise ValueError("MCP tool arguments exceed the maximum allowed size.")
        return value
