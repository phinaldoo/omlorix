from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal

from pydantic import BaseModel, Field


class CustomPythonToolMutationRequest(BaseModel):
    """Validate the payload used to create or replace a stored custom Python tool."""

    source_code: str = Field(..., min_length=1)
    enabled: bool = True
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class CustomPythonToolTestRequest(BaseModel):
    """Validate the payload used to inspect and execute tool source in admin test mode."""

    source_code: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class CustomPythonToolListItem(BaseModel):
    """Describe the persisted metadata returned when listing custom Python tools."""

    id: str
    name: str
    display_name: str
    description: str
    enabled: bool
    timeout_seconds: int
    tool_schema: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CustomPythonToolDetail(CustomPythonToolListItem):
    """Extend the list payload with source code for detail and mutation responses."""

    source_code: str


class CustomPythonToolTestResponse(BaseModel):
    """Return the normalized tool definition and execution payload from a test run."""

    definition: dict[str, Any]
    output: dict[str, Any]


class CustomPythonToolImportContract(BaseModel):
    """Describe the export envelope accepted by the custom-tool importer."""

    export_type: Literal["custom_python_tool"]
    export_version: float


class CustomPythonToolDeleteResponse(BaseModel):
    """Confirm that a custom Python tool deletion request completed successfully."""

    status: Literal["success"]
    tool_id: str
