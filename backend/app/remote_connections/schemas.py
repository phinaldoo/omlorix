from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.icon_security import sanitize_icon_input


def _reject_control_characters(value: str, label: str) -> str:
    """Reject values which could corrupt SSH configuration or remote commands."""
    normalized = str(value or "").strip()
    if not normalized or any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{label} is invalid")
    return normalized


class SshConnectionInput(BaseModel):
    """Create or replace a user-owned SSH connection."""

    name: str = Field(min_length=1, max_length=120)
    icon: str = Field(default="server", min_length=1, max_length=50000)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
    host_key: str = Field(min_length=16, max_length=4096)
    private_key: str | None = Field(default=None, max_length=65536)
    workspace_root: str = Field(default="~", min_length=1, max_length=4096)
    connect_timeout_seconds: int = Field(default=15, ge=3, le=120)
    enabled: bool = True

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("name", "username", "workspace_root")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        """Keep shell-sensitive connection fields free from control characters."""
        return _reject_control_characters(value, info.field_name)

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str) -> str:
        """Accept only an icon preset or inert inline SVG."""

        sanitized = sanitize_icon_input(value, fallback="")
        if not sanitized:
            raise ValueError("icon must be a preset or safe SVG")
        return sanitized

    @field_validator("workspace_root")
    @classmethod
    def validate_workspace_root(cls, value: str) -> str:
        """Require an explicit absolute path on the remote device."""
        path = _reject_control_characters(value, "workspace_root")
        if not path.startswith("/"):
            raise ValueError("workspace_root must be an absolute remote path")
        return path

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        """Accept a host only, never a URL, path, or command fragment."""
        host = _reject_control_characters(value, "host")
        if (
            "://" in host
            or "/" in host
            or "@" in host
            or any(character.isspace() for character in host)
        ):
            raise ValueError("host must be a hostname or IP address")
        return host

    @field_validator("host_key")
    @classmethod
    def validate_host_key(cls, value: str) -> str:
        """Require a complete OpenSSH public host key for strict pinning."""
        key = _reject_control_characters(value, "host_key")
        parts = key.split()
        if len(parts) < 2 or not parts[0].startswith(("ssh-", "ecdsa-", "sk-ssh-")):
            raise ValueError("host_key must contain an OpenSSH key type and public key")
        return f"{parts[0]} {parts[1]}"


class SshConnectionUpdate(SshConnectionInput):
    """Update an SSH connection while allowing the stored key to remain unchanged."""

    private_key: str | None = Field(default=None, max_length=65536)


class SshConnectionResponse(BaseModel):
    """Secret-free SSH connection representation returned to the browser."""

    id: str
    name: str
    icon: str
    host: str
    port: int
    username: str
    host_key: str
    host_key_fingerprint: str = ""
    workspace_root: str
    connect_timeout_seconds: int
    enabled: bool
    has_private_key: bool
    status: dict = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


class RemoteConnectionTestResponse(BaseModel):
    """Normalized result returned when testing an unsaved connection draft."""

    state: Literal["connected", "error"]
    status: dict = Field(default_factory=dict)
    error_code: str | None = None
    detail: str | None = None
    protocol_version: int | None = None
    agent_info: dict | None = None


class SshHostKeyDiscoveryRequest(BaseModel):
    """Host-key discovery request which does not authenticate to the host."""

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        """Apply the same conservative host syntax used by saved connections."""
        host = _reject_control_characters(value, "host")
        if (
            "://" in host
            or "/" in host
            or "@" in host
            or any(character.isspace() for character in host)
        ):
            raise ValueError("host must be a hostname or IP address")
        return host


class AcpProfileInput(BaseModel):
    """User-owned ACP agent configuration layered over an SSH connection."""

    name: str = Field(min_length=1, max_length=120)
    icon: str = Field(default="terminal", min_length=1, max_length=50000)
    ssh_connection_id: str = Field(min_length=1, max_length=128)
    executable: str = Field(default="opencode", min_length=1, max_length=1024)
    arguments: list[str] = Field(default_factory=lambda: ["acp"], max_length=32)
    workspace_root: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(default=".", min_length=1, max_length=4096)
    additional_directories: list[str] = Field(default_factory=list, max_length=16)
    mode: str | None = Field(default=None, max_length=128)
    permission_mode: Literal["ask", "allow", "deny"] = "ask"
    permission_timeout_seconds: int = Field(default=300, ge=15, le=3600)
    prompt_timeout_seconds: int = Field(default=1800, ge=30, le=7200)
    enabled: bool = True

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("name", "executable", "workspace_root", "cwd")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _reject_control_characters(value, info.field_name)

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str) -> str:
        """Validate custom ACP SVG before it reaches connection/model rendering."""

        sanitized = sanitize_icon_input(value, fallback="")
        if not sanitized:
            raise ValueError("icon must be a preset or safe SVG")
        return sanitized

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: list[str]) -> list[str]:
        """Preserve argv order and duplicates while rejecting unsafe controls."""
        result: list[str] = []
        for raw_value in value:
            item = str(raw_value)
            if "\x00" in item or "\n" in item or "\r" in item:
                raise ValueError("list values cannot contain NUL or newlines")
            if item:
                result.append(item)
        return result

    @field_validator("additional_directories")
    @classmethod
    def validate_additional_directories(cls, value: list[str]) -> list[str]:
        """Normalize and deduplicate optional workspace directories."""
        result: list[str] = []
        for raw_value in value:
            item = str(raw_value)
            if "\x00" in item or "\n" in item or "\r" in item:
                raise ValueError("list values cannot contain NUL or newlines")
            if item and item not in result:
                result.append(item)
        return result

    @model_validator(mode="after")
    def validate_remote_paths(self):
        """Keep ACP paths inside the configured remote workspace boundary."""
        if not self.workspace_root.startswith("/"):
            raise ValueError("workspace_root must be an absolute remote path")
        if self.cwd.startswith(("/", "~")):
            raise ValueError("cwd must be relative to workspace_root")
        if any(path.startswith(("/", "~")) for path in self.additional_directories):
            raise ValueError(
                "additional_directories must be relative to workspace_root"
            )
        relative_paths = [self.cwd, *self.additional_directories]
        if any(".." in PurePosixPath(path).parts for path in relative_paths):
            raise ValueError("ACP directories cannot traverse outside workspace_root")
        return self


class AcpProfileResponse(AcpProfileInput):
    """ACP profile plus its model-picker identity."""

    id: str
    provider_id: str | None = None
    model_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AcpModelDiscoveryRequest(BaseModel):
    """Identify the owned ACP model and optional session to inspect."""

    model_id: str = Field(min_length=1, max_length=255)
    session_id: str | None = Field(default=None, max_length=512)

    model_config = ConfigDict(str_strip_whitespace=True)


class AcpModelOption(BaseModel):
    """A model value advertised by the connected ACP agent."""

    id: str
    name: str
    description: str | None = None


class AcpModelDiscoveryResponse(BaseModel):
    """Selectable ACP session controls for one reusable session."""

    session_id: str
    current_model_id: str | None = None
    models: list[AcpModelOption] = Field(default_factory=list)
    current_security_level: str | None = None
    security_levels: list[AcpModelOption] = Field(default_factory=list)
    current_reasoning_effort: str | None = None
    reasoning_efforts: list[AcpModelOption] = Field(default_factory=list)
    reasoning_efforts_by_model: dict[str, list[str]] = Field(default_factory=dict)


class AcpModelSelectionInput(BaseModel):
    """Persist user-selected controls for an existing ACP conversation."""

    chat_id: str = Field(min_length=1, max_length=255)
    model_id: str = Field(min_length=1, max_length=255)
    session_id: str = Field(min_length=1, max_length=512)
    acp_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    acp_security_level: str | None = Field(default=None, min_length=1, max_length=255)
    acp_reasoning_effort: str | None = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(str_strip_whitespace=True)

    @model_validator(mode="after")
    def require_selection(self):
        """Require at least one concrete control value to persist."""
        if not any(
            (
                self.acp_model_id,
                self.acp_security_level,
                self.acp_reasoning_effort,
            )
        ):
            raise ValueError("At least one ACP session control is required")
        return self
