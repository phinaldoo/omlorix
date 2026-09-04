from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class MemoryCandidate(BaseModel):
    """Schema-constrained output from the memory model; this is not a tool call."""

    model_config = ConfigDict(str_strip_whitespace=True)

    action: MemoryAction
    target_memory_id: str = Field(max_length=80)
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=180)
    content: str = Field(max_length=320)
    kind: MemoryKind
    stability: MemoryStability
    importance: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(min_length=1, max_length=180)
    sensitivity: MemorySensitivity


class MemoryConsolidation(BaseModel):
    candidates: list[MemoryCandidate] = Field(max_length=24)


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, max_length=80)
    locale: str = Field(default="en", min_length=2, max_length=5)


class EditMemoryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=320)


class SweepRequest(BaseModel):
    advance_days: int = Field(default=0, ge=0, le=3_650)


class MessageView(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ProfileView(BaseModel):
    content: str
    version: int
    updated_at: datetime | None


class MemoryView(BaseModel):
    id: str
    key: str
    content: str
    kind: MemoryKind
    stability: MemoryStability
    importance: int
    confidence: float
    freshness: float
    lifecycle_state: Literal["fresh", "review"]
    version: int
    created_at: datetime
    updated_at: datetime
    last_confirmed_at: datetime
    review_at: datetime
    expires_at: datetime
    source_excerpt: str


class EventView(BaseModel):
    id: int
    action: Literal[
        "created",
        "updated",
        "confirmed",
        "forgotten",
        "expired",
        "profile",
    ]
    kind: str | None
    created_at: datetime


class MetricsView(BaseModel):
    active_memories: int
    max_memories: int
    review_memories: int
    average_freshness: float
    total_tokens: int
    estimated_cost_usd: float
    last_turn_cost_usd: float


class RuntimeView(BaseModel):
    mode: Literal["live", "simulation", "unconfigured"]
    chat_model: str
    memory_model: str
    clock_offset_days: int


class AppState(BaseModel):
    conversation_id: str
    runtime: RuntimeView
    messages: list[MessageView]
    profile: ProfileView
    memories: list[MemoryView]
    events: list[EventView]
    metrics: MetricsView


class ChatResult(BaseModel):
    state: AppState
    memory_status: Literal["updated", "unchanged", "failed"]
    memory_error: Literal["connection", "rate_limit", "provider"] | None = None
    memory_profile_version: int | None


class ActionResult(BaseModel):
    state: AppState
    status: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    mode: Literal["live", "simulation", "unconfigured"]
    database: Literal["ok"]
