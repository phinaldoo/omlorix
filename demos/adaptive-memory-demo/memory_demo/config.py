from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_GENERATION_MODEL_FAMILIES = (
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
)


class Settings(BaseSettings):
    """Configuration loaded from the demo's own .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-5.6-luna"
    openai_memory_model: str = "gpt-5.6-luna"
    openai_memory_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = (
        "none"
    )
    openai_chat_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    openai_timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0)
    openai_max_retries: int = Field(default=2, ge=0, le=5)

    memory_allow_sensitive: bool = False
    memory_max_facts: int = Field(default=100, ge=1, le=100)
    memory_max_output_tokens: int = Field(default=2_400, ge=300, le=4_000)
    chat_max_output_tokens: int = Field(default=900, ge=100, le=8_000)

    offline_demo_mode: Literal["auto", "true", "false"] = "auto"
    database_path: Path = PROJECT_ROOT / "data" / "memory-demo.sqlite3"
    host: str = "127.0.0.1"
    port: int = Field(default=8010, ge=1, le=65_535)

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def empty_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_path", mode="before")
    @classmethod
    def resolve_database_path(cls, value: object) -> object:
        path = Path(str(value))
        return path if path.is_absolute() else PROJECT_ROOT / path

    @field_validator("openai_chat_model", "openai_memory_model")
    @classmethod
    def supported_generation_model(cls, value: str) -> str:
        if not any(
            value == family or value.startswith(f"{family}-")
            for family in SUPPORTED_GENERATION_MODEL_FAMILIES
        ):
            choices = ", ".join(SUPPORTED_GENERATION_MODEL_FAMILIES)
            raise ValueError(f"this demo supports {choices} (including dated snapshots)")
        return value

    @property
    def api_key(self) -> str | None:
        return self.openai_api_key.get_secret_value() if self.openai_api_key else None

    @property
    def runtime_mode(self) -> Literal["live", "simulation", "unconfigured"]:
        if self.offline_demo_mode == "true":
            return "simulation"
        if self.api_key:
            return "live"
        if self.offline_demo_mode == "false":
            return "unconfigured"
        return "simulation"
