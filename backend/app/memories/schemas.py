from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


MAX_MEMORY_IMPORT_ITEMS = 500
MEMORY_IMPORT_LIMIT_MESSAGE = (
    f"You can import up to {MAX_MEMORY_IMPORT_ITEMS} memories at once"
)


class MemoryCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=500)


class MemoryResponse(BaseModel):
    id: str
    user_id: str | None = None
    project_id: str | None = None
    content: str
    source_date: Optional[date]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    limit: int
    offset: int
    has_more: bool = False


class MemorySettingsResponse(BaseModel):
    enabled: bool
    include_in_context: bool
    auto_create: bool


class MemorySettingsUpdate(BaseModel):
    enabled: bool | None = None
    include_in_context: bool | None = None
    auto_create: bool | None = None


class MemoryImportItem(BaseModel):
    date: str
    content: str = Field(..., min_length=1, max_length=500)

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized.lower() == "unknown":
            return "unknown"
        try:
            date.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                "date must be 'unknown' or a valid YYYY-MM-DD string"
            ) from exc
        return normalized

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("content is required")
        return normalized


class MemoryImportResponse(BaseModel):
    total_received: int
    created_count: int
    deduped_count: int
    items: list[MemoryResponse]


class MemoryExportItem(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)
    source_date: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryExportData(BaseModel):
    # Exports may contain a complete account. Interactive imports enforce the
    # bounded batch size in the service after parsing this portable envelope.
    memories: list[MemoryExportItem]


class MemoryExportPayload(BaseModel):
    export_type: Literal["memories"]
    export_version: Literal[1.0]
    data: MemoryExportData
