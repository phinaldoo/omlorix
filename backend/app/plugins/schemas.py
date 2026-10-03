"""Pydantic contracts for plugin manifests and public API responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PluginAuthor(BaseModel):
    """Author metadata from ``.codex-plugin/plugin.json``."""

    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    url: str | None = Field(default=None, max_length=2048)
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class PluginManifest(BaseModel):
    """Forward-compatible subset of the OpenAI plugin manifest."""

    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=4000)
    author: PluginAuthor | None = None
    homepage: str | None = Field(default=None, max_length=2048)
    repository: str | None = Field(default=None, max_length=2048)
    license: str | None = Field(default=None, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=100)
    mcpServers: str | list[str] | dict[str, Any] | None = None
    apps: str | list[str] | dict[str, Any] | None = None
    hooks: str | dict[str, Any] | None = None
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    @field_validator("skills", mode="before")
    @classmethod
    def normalize_skills(cls, value: Any) -> list[str]:
        """Accept one path or the standard array form without broad coercion."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            raise ValueError("skills must be a path or an array of paths")
        return [str(item) for item in value]


class PluginComponentResponse(BaseModel):
    """A safe component summary without MCP credentials or skill contents."""

    id: str
    type: str
    key: str
    name: str
    enabled: bool

class PluginResponse(BaseModel):
    """Plugin detail returned by lifecycle endpoints."""

    id: str
    name: str
    version: str
    description: str = ""
    enabled: bool
    content_sha256: str
    author_name: str = ""
    homepage: str = ""
    repository: str = ""
    components: list[PluginComponentResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class PluginPreviewResponse(BaseModel):
    """Validated archive summary shown before installation."""

    name: str
    version: str
    description: str
    author_name: str = ""
    content_sha256: str
    skill_names: list[str] = Field(default_factory=list)
    mcp_server_names: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requires_local_process_review: bool = False
    has_hooks: bool = False
    has_registered_apps: bool = False


class PluginEnabledRequest(BaseModel):
    """Explicit lifecycle state transition requested by a user."""

    enabled: bool
