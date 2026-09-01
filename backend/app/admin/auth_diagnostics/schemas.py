"""Response contracts for administrator authentication diagnostics."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AuthDiagnosticItem(BaseModel):
    id: str
    reference: str | None = None
    flow: str | None = None
    provider: str | None = None
    stage: str | None = None
    error_code: str | None = None
    status: str
    message: str | None = None
    details: dict[str, Any] | None = None
    timestamp: datetime


class AuthDiagnosticPage(BaseModel):
    items: list[AuthDiagnosticItem]
    page: int
    page_size: int
    total: int
    has_next: bool


class OIDCConfigurationTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OIDCConfigurationCheck(BaseModel):
    code: str
    status: Literal["passed", "warning", "failed"]
    details: dict[str, Any] = Field(default_factory=dict)


class OIDCConfigurationTestResponse(BaseModel):
    status: Literal["passed", "warning", "failed"]
    reference: str
    callback_url: str
    checks: list[OIDCConfigurationCheck]
