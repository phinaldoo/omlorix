"""Explicit response and request contracts for administrator audit-log access."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AuditLogRetentionSummary(BaseModel):
    """Effective retention boundaries relevant to the general audit store."""

    global_cleanup_enabled: bool = False
    post_user_deletion_mode: Literal["delete_instantly", "delete_after_days", "retain"]
    post_user_deletion_days: int | None = None


class AuditLogItem(BaseModel):
    id: str
    actor_user_id: str
    action: str
    reason: str | None = None
    timestamp: datetime
    category: str
    ip_fingerprint: str | None = None
    device_fingerprint: str | None = None
    has_details: bool = False


class AuditLogDetail(AuditLogItem):
    details: dict[str, Any] | None = None


class AuditLogPage(BaseModel):
    items: list[AuditLogItem]
    next_cursor: str | None = None
    has_next: bool
    snapshot_at: datetime
    from_timestamp: datetime
    to_timestamp: datetime
    retention: AuditLogRetentionSummary


class AuditLogExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_timestamp: datetime = Field(alias="from")
    to_timestamp: datetime = Field(alias="to")
    category: str | None = Field(default=None, min_length=1, max_length=64)
    action: str | None = Field(default=None, min_length=1, max_length=128)
    actor_user_id: str | None = Field(default=None, min_length=1, max_length=64)
    reference: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str = Field(min_length=3, max_length=255)
