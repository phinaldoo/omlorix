from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_MEMORIES_PER_SCOPE = 100
MAX_MEMORY_IMPORT_ITEMS = MAX_MEMORIES_PER_SCOPE
MEMORY_IMPORT_LIMIT_MESSAGE = (
    f"You can import up to {MAX_MEMORY_IMPORT_ITEMS} memories at once"
)

MemoryKind = Literal[
    "identity",
    "preference",
    "project",
    "relationship",
    "constraint",
    "experience",
    "goal",
    "other",
]
MemoryStability = Literal["stable", "slow", "changing", "ephemeral"]
MemorySensitivity = Literal["normal", "sensitive", "secret"]
MemoryAction = Literal["create", "update", "confirm", "forget"]


class MemoryCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)
    kind: MemoryKind = "other"
    stability: MemoryStability = "slow"
    importance: int = Field(default=3, ge=1, le=5)


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=500)
    stability: MemoryStability | None = None
    importance: int | None = Field(default=None, ge=1, le=5)


class MemoryResponse(BaseModel):
    id: str
    user_id: str | None = None
    project_id: str | None = None
    content: str
    memory_key: str = ""
    kind: MemoryKind = "other"
    stability: MemoryStability = "slow"
    importance: int = 3
    confidence: float = 1.0
    sensitivity: MemorySensitivity = "normal"
    lifecycle_state: Literal["fresh", "review"] = "fresh"
    freshness: float = 1.0
    version: int = 1
    source_date: Optional[date] = None
    source_excerpt: str | None = None
    last_confirmed_at: Optional[datetime] = None
    review_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    limit: int
    offset: int
    has_more: bool = False
    max_items: int = MAX_MEMORIES_PER_SCOPE


class MemoryProfileResponse(BaseModel):
    content: str = ""
    version: int = 0
    active_fact_count: int = 0
    review_fact_count: int = 0
    max_fact_count: int = MAX_MEMORIES_PER_SCOPE
    updated_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_status: Literal["processing", "updated", "unchanged", "failed"] | None = None
    last_error_code: str | None = None


class MemoryCandidate(BaseModel):
    """Schema-constrained model output. This is never exposed as a tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: MemoryAction
    target_memory_id: str = Field(default="", max_length=80)
    key: str = Field(min_length=1, max_length=120)
    content: str = Field(default="", max_length=500)
    kind: MemoryKind
    stability: MemoryStability
    importance: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(min_length=1, max_length=500)
    sensitivity: MemorySensitivity = "normal"


class MemoryConsolidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[MemoryCandidate] = Field(
        default_factory=list,
        max_length=MAX_MEMORIES_PER_SCOPE,
    )


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
    memory_key: str | None = Field(default=None, max_length=120)
    kind: MemoryKind | None = None
    stability: MemoryStability | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    sensitivity: MemorySensitivity | None = None
    version: int | None = Field(default=None, ge=1)
    source_excerpt: str | None = Field(default=None, max_length=500)
    source_date: str | None = None
    evidence_at: str | None = None
    last_confirmed_at: str | None = None
    review_at: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryExportData(BaseModel):
    memories: list[MemoryExportItem] = Field(max_length=MAX_MEMORY_IMPORT_ITEMS)


class MemoryExportPayload(BaseModel):
    export_type: Literal["memories"]
    # 1.0 archives remain importable; all new exports use 2.0.
    export_version: Literal[1.0, 2.0]
    data: MemoryExportData
