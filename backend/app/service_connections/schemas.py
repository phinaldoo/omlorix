"""Validated request and secret-free response schemas for service connections."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServiceConnectionCreate(BaseModel):
    """Create one shared external service endpoint."""

    name: str | None = Field(default=None, max_length=120)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=16384)
    enabled_for_code_execution: bool = False
    enabled_for_latex_pdf: bool = False
    enabled_for_slide_renderer: bool = False
    weight: int = Field(default=1, ge=1, le=100)

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        """Store one canonical URL without a trailing slash."""

        return value.rstrip("/")


class ServiceConnectionUpdate(BaseModel):
    """Update selected fields without clearing omitted values."""

    name: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=16384)
    clear_api_key: bool = False
    enabled_for_code_execution: bool | None = None
    enabled_for_latex_pdf: bool | None = None
    enabled_for_slide_renderer: bool | None = None
    weight: int | None = Field(default=None, ge=1, le=100)

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("base_url")
    @classmethod
    def normalize_optional_base_url(cls, value: str | None) -> str | None:
        """Canonicalize an explicitly supplied update URL."""

        return value.rstrip("/") if value is not None else None


class ServiceConnectionResponse(BaseModel):
    """Secret-free service connection returned to administrators."""

    id: str
    name: str
    base_url: str
    has_api_key: bool = False
    enabled_for_code_execution: bool = False
    enabled_for_latex_pdf: bool = False
    enabled_for_slide_renderer: bool = False
    weight: int = 1
    status: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ServiceConnectionDeleteResponse(BaseModel):
    """Confirmation returned after deleting one connection."""

    deleted: bool = True
    connection_id: str
