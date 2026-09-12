from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.llm.model_schemas import (
    get_parameter_basic_schema,
)
from app.utils.schemas import populate_sections_with_values


ACP_INPUT_FORMATS = ["text", "image", "audio", "video", "documents"]


class AcpModelSettings(BaseModel):
    """Runtime settings stored on a user-owned ACP model entry."""

    title_generation: bool = False
    title_generation_model: Literal["current", "specific"] | None = "current"
    title_generation_model_id: str | None = None
    use_project_context: bool = True
    use_group_context: bool = True
    system_instruction: str | None = None
    allow_custom_generation_parameter: bool = False
    input_formats: list[
        Literal["text", "image", "audio", "video", "documents"]
    ] = Field(default_factory=lambda: list(ACP_INPUT_FORMATS))
    output_formats: list[Literal["text"]] = Field(default_factory=lambda: ["text"])
    input_token_limit: int | None = Field(default=0, ge=0)
    output_token_limit: int | None = Field(default=0, ge=0)
    skill_id: str | None = None
    cwd: str = "."
    mode: str | None = None
    additional_directories: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("cwd")
    @classmethod
    def validate_relative_cwd(cls, value: str) -> str:
        """Keep model workspaces relative to the provider's trusted root."""
        normalized = value.strip() or "."
        if Path(normalized).is_absolute():
            raise ValueError("ACP model cwd must be relative to the provider workspace root")
        return normalized

    @field_validator("additional_directories")
    @classmethod
    def validate_additional_directories(cls, value: list[str]) -> list[str]:
        """Require additional roots to remain relative to the provider boundary."""
        normalized: list[str] = []
        for raw_path in value:
            path = str(raw_path).strip()
            if not path:
                continue
            if Path(path).is_absolute():
                raise ValueError("ACP additional directories must be relative paths")
            normalized.append(path)
        return normalized


class AcpPermissionDecision(BaseModel):
    """Authenticated response to a pending ACP permission request."""

    option_id: str | None = Field(default=None, max_length=512)


class AcpPermissionResolution(BaseModel):
    """Public acknowledgement returned after a permission decision."""

    resolved: bool


def get_acp_model_schema_parameter(db, user_id, model_id, project_id):
    """Return safe user-overridable settings for an ACP model."""
    from app.llm.models import get_model

    model = get_model(db, model_id)
    settings = model.settings if isinstance(model.settings, dict) else {}
    schema = get_parameter_basic_schema(
        db,
        user_id,
        project_id,
        tool_names=[],
        enabled_tools_value=[],
        model_settings=settings,
    )
    populate_sections_with_values(schema, {"settings": settings})
    return schema
