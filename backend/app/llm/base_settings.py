from datetime import date
from typing import Generic, List, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator

InputFormatType = TypeVar("InputFormatType")
OutputFormatType = TypeVar("OutputFormatType")

# Provider request timeouts are an application-level safety boundary rather
# than a user preference. Keeping one shared constant prevents individual
# provider forms and adapters from drifting to different request limits.
LLM_PROVIDER_REQUEST_TIMEOUT_SECONDS = 120


def remove_custom_provider_timeout(settings: dict | None) -> dict:
    """Return provider settings without the removed custom timeout field."""

    sanitized = dict(settings) if isinstance(settings, dict) else {}
    sanitized.pop("timeout", None)
    return sanitized


class BaseModelSettings(BaseModel, Generic[InputFormatType, OutputFormatType]):
    """Common fields shared across provider model settings."""

    title_generation: bool
    title_generation_model: Literal["current", "specific"] | None = None
    title_generation_model_id: str | None = None

    use_project_context: bool = True
    use_group_context: bool = True

    system_instruction: str | None = None
    knowledge_cutoff: date | None = None
    training_data: Literal["true", "false", "unknown"] | None = None

    allow_custom_generation_parameter: bool
    custom_title_generation_instruction: str | None = None

    input_formats: List[InputFormatType]
    output_formats: List[OutputFormatType]
    input_token_limit: int | None = Field(default=0, ge=0)
    output_token_limit: int | None = Field(default=0, ge=0)

    websearch_scrape_provider: str | None = None
    websearch_search_provider: str | None = None
    allowed_mcp_servers: List[str] = Field(default_factory=list)
    allow_custom_user_mcp_servers: bool = True

    skill_id: str | None = None

    @model_validator(mode="after")
    def validate_title_generation(self):
        if self.title_generation:
            if not self.title_generation_model:
                raise ValueError("'title_generation_model' is required when title_generation is enabled.")
        elif not self.title_generation_model:
            self.title_generation_model = "current"
        return self
