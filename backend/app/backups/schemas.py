from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BackupDestinationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider: Literal["local", "s3", "gcs", "azure", "webdav"]
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class BackupDestinationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    provider: Literal["local", "s3", "gcs", "azure", "webdav"] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class BackupDestinationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    created_at: datetime
    updated_at: datetime


class BackupScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = False
    timezone: str = "UTC"
    frequency: Literal["hourly", "daily", "weekly"] = "daily"
    minute: int = Field(default=0, ge=0, le=59)
    hour: int = Field(default=2, ge=0, le=23)
    days_of_week: list[int] = Field(default_factory=list)
    retention_count: int | None = Field(default=30, ge=1)
    retention_days: int | None = Field(default=30, ge=1)
    destination_id: str | None = None


class BackupScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    timezone: str | None = None
    frequency: Literal["hourly", "daily", "weekly"] | None = None
    minute: int | None = Field(default=None, ge=0, le=59)
    hour: int | None = Field(default=None, ge=0, le=23)
    days_of_week: list[int] | None = None
    retention_count: int | None = Field(default=None, ge=1)
    retention_days: int | None = Field(default=None, ge=1)
    destination_id: str | None = None


class BackupScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    enabled: bool
    timezone: str
    frequency: str
    minute: int
    hour: int
    days_of_week: list[int] = Field(default_factory=list)
    retention_count: int | None = None
    retention_days: int | None = None
    destination_id: str | None = None
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BackupCreateRequest(BaseModel):
    destination_id: str | None = None
    encryption_enabled: bool = True


class BackupRuntimeCapabilities(BaseModel):
    archive_encryption_default_enabled: bool
    archive_encryption_available: bool
    archive_passphrase_configured: bool
    plaintext_archives_allowed: bool


class BackupArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    backup_job_id: str
    storage: dict[str, Any] | None = None
    checksum_sha256: str
    bytes: int
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime


class BackupJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trigger_type: str
    status: str
    error: str | None = None
    manifest_json: dict[str, Any] | None = None
    options: dict[str, Any] | None = None
    size_bytes: int | None = None
    requested_by_user_id: str | None = None
    destination_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    artifacts: list[BackupArtifactResponse] = Field(default_factory=list)


class BackupJobPageResponse(BaseModel):
    """One bounded page of backup-history entries."""

    items: list[BackupJobResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class OperationStatus(BaseModel):
    status: str
    message: str | None = None
    details: dict[str, Any] | None = None
