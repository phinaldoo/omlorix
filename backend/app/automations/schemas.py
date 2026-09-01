from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.users.timezones import normalize_timezone_identifier


def _normalize_schedule_timezone(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalize_timezone_identifier(normalized)


class ScheduleRule(BaseModel):
    """A schedule rule describing explicit trigger times."""

    # Reject removed schedule fields instead of silently accepting an
    # incomplete rule that will never run.
    model_config = ConfigDict(extra="forbid")

    type: Optional[str] = Field(None, description="Rule type: 'recurring' or 'once'")
    times: List[str] = Field(default_factory=list, description="List of trigger times in HH:MM format")
    days: List[int] = Field(default_factory=list, description="Days of week (0=Mon, 6=Sun)")
    run_at: Optional[str] = Field(None, description="One-time execution timestamp in ISO-8601 UTC format")
    label: Optional[str] = Field(None, description="Optional label for the rule")


class AutomationWebhookTriggerCreate(BaseModel):
    """Request schema for creating a webhook trigger."""
    name: Optional[str] = Field(None, max_length=255)
    is_enabled: Optional[bool] = Field(True)
    payload_mode: Optional[str] = Field("append")
    include_headers: Optional[bool] = Field(False)
    allowed_header_names: Optional[List[str]] = Field(default_factory=list)
    max_body_bytes: Optional[int] = Field(None, ge=1024, le=1024 * 1024)
    rate_limit_per_minute: Optional[int] = Field(None, ge=1, le=300)


class AutomationWebhookTriggerReservedCreate(AutomationWebhookTriggerCreate):
    """A server-issued webhook reservation submitted with a new automation."""

    trigger_id: str = Field(..., min_length=1, max_length=64)
    secret: str = Field(..., min_length=32, max_length=255)
    reservation_token: str = Field(..., min_length=1)


class AutomationWebhookCredentialsResponse(BaseModel):
    """Final webhook credentials reserved before an automation is persisted."""

    trigger_id: str
    url: str
    secret: str
    reservation_token: str
    expires_at: datetime


class AutomationCreate(BaseModel):
    """Request schema for creating an automation."""
    title: str = Field(..., min_length=1, max_length=255)
    prompt: str = Field(..., min_length=1)
    model_id: str = Field(...)
    icon: Optional[str] = Field("folder")
    icon_color: Optional[str] = Field("#FF6B6B")
    schedule_rules: Optional[List[ScheduleRule]] = Field(default_factory=list)
    schedule_timezone: Optional[str] = Field(None, description="Timezone for recurring schedule rules")
    skill_id: Optional[str] = Field(None)
    note_ids: Optional[List[str]] = Field(default_factory=list)
    file_ids: Optional[List[str]] = Field(default_factory=list)
    mcp_server_ids: Optional[List[str]] = Field(default_factory=list, max_length=100)
    is_active: Optional[bool] = Field(True)
    webhook_trigger: Optional[AutomationWebhookTriggerReservedCreate] = Field(
        None,
        description="Optional webhook trigger to create atomically with the automation",
    )

    _validate_schedule_timezone = field_validator("schedule_timezone", mode="before")(_normalize_schedule_timezone)


class AutomationUpdate(BaseModel):
    """Request schema for updating an automation."""
    automation_id: str = Field(...)
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    prompt: Optional[str] = Field(None, min_length=1)
    model_id: Optional[str] = Field(None)
    icon: Optional[str] = Field(None)
    icon_color: Optional[str] = Field(None)
    schedule_rules: Optional[List[ScheduleRule]] = Field(None)
    schedule_timezone: Optional[str] = Field(None, description="Timezone for recurring schedule rules")
    skill_id: Optional[str] = Field(None)
    note_ids: Optional[List[str]] = Field(None)
    file_ids: Optional[List[str]] = Field(None)
    mcp_server_ids: Optional[List[str]] = Field(None, max_length=100)
    is_active: Optional[bool] = Field(None)

    _validate_schedule_timezone = field_validator("schedule_timezone", mode="before")(_normalize_schedule_timezone)


class AutomationResponse(BaseModel):
    """Response schema for a single automation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    icon: Optional[str]
    icon_color: Optional[str]
    prompt: str
    model_id: str
    schedule_rules: Optional[List[dict]]
    schedule_timezone: Optional[str]
    skill_id: Optional[str]
    note_ids: Optional[List[str]]
    file_ids: Optional[List[str]]
    mcp_server_ids: Optional[List[str]]
    webhook_trigger: Optional["AutomationWebhookTriggerResponse"] = None
    is_active: bool
    last_triggered_at: Optional[datetime]
    created_at: datetime
    last_updated_at: datetime


class AutomationListResponse(BaseModel):
    """Response schema for a list of automations."""
    automations: List[AutomationResponse]
    limit: int = 0
    offset: int = 0
    has_more: bool = False


class AutomationStatusResponse(BaseModel):
    """Response for automation operation status."""
    status: str
    message: Optional[str] = None
    automation: Optional[AutomationResponse] = None


class AutomationWebhookTriggerUpdate(BaseModel):
    """Request schema for updating a webhook trigger."""
    name: Optional[str] = Field(None, max_length=255)
    is_enabled: Optional[bool] = Field(None)
    payload_mode: Optional[str] = Field(None)
    include_headers: Optional[bool] = Field(None)
    allowed_header_names: Optional[List[str]] = Field(None)
    max_body_bytes: Optional[int] = Field(None, ge=1024, le=1024 * 1024)
    rate_limit_per_minute: Optional[int] = Field(None, ge=1, le=300)


class AutomationWebhookTriggerResponse(BaseModel):
    """Response schema for a webhook trigger. The raw secret is returned only once."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    automation_id: str
    user_id: str
    name: Optional[str]
    is_enabled: bool
    token_prefix: str
    payload_mode: str
    include_headers: bool
    allowed_header_names: Optional[List[str]]
    max_body_bytes: int
    rate_limit_per_minute: int
    url: Optional[str] = None
    secret: Optional[str] = None
    last_triggered_at: Optional[datetime]
    created_at: datetime
    last_updated_at: datetime


class AutomationWebhookDeliveryResponse(BaseModel):
    """Response schema for recent webhook deliveries."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    trigger_id: str
    automation_id: str
    user_id: str
    status: str
    status_code: Optional[int]
    error: Optional[str]
    request_ip: Optional[str]
    user_agent: Optional[str]
    payload_preview: Optional[dict[str, Any]]
    chat_id: Optional[str]
    created_at: datetime


class AutomationWebhookDeliveriesResponse(BaseModel):
    deliveries: List[AutomationWebhookDeliveryResponse]


class AutomationWebhookStatusResponse(BaseModel):
    status: str
    message: Optional[str] = None
    trigger: Optional[AutomationWebhookTriggerResponse] = None


AutomationResponse.model_rebuild()
