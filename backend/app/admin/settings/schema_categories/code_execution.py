"""Schemas for code-execution settings."""

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, field_validator


class CodeExecutionSettings(BaseModel):
    max_output_length: int = Field(default=50000, ge=100, le=100000)

    @field_validator("max_output_length", mode="before")
    @classmethod
    def _normalize_max_output_length(cls, value):
        if value is None:
            return 50000
        try:
            length = int(value)
            return max(100, min(100000, length))
        except (ValueError, TypeError):
            return 50000


code_execution_schema = Sections(
    sections=[
        Section(
            title="Code Execution Service",
            description="Configure the external Docker-based Python code execution service.",
            i18n_title="schema_code_execution_sec0_title",
            i18n_description="schema_code_execution_sec0_desc",
            fields=[
                FieldSchema(
                    key="max_output_length",
                    label="Max Output Length",
                    description="Maximum number of characters to capture from stdout/stderr. Range: 100-100000.",
                    type="number",
                    attributes={"min": 100, "max": 100000},
                    i18n_label="schema_code_execution_max_output_length",
                    i18n_description="schema_code_execution_max_output_length_desc",
                ),
            ],
        )
    ]
)
