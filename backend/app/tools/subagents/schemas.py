from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SUBAGENT_RUNTIME_TARGETS_SETTING = "_runtime_subagent_targets"
SUBAGENT_MAX_SELECTED_TARGETS = 20
SUBAGENT_TARGET_PAGE_MAX = 50


class SubagentTargetRef(BaseModel):
    """One exact delegation target selected by the authenticated user."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["model", "agent"]
    id: str = Field(..., min_length=1, max_length=255)

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Subagent target ID is required")
        return normalized
