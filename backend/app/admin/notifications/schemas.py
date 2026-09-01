import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator

# -------------------
# Admin: Notifications
# -------------------
_ADMIN_NOTIFICATION_DETAILS_MAX_CHARS = 4000


def _trim_admin_notification_details_text(
    value: str, *, limit: int = _ADMIN_NOTIFICATION_DETAILS_MAX_CHARS
) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(limit - 3, 0)]}..."


def _bound_admin_notification_details(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, str):
        return _trim_admin_notification_details_text(value)

    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return _trim_admin_notification_details_text(str(value))

    if len(serialized) <= _ADMIN_NOTIFICATION_DETAILS_MAX_CHARS:
        return value

    return _trim_admin_notification_details_text(serialized)


class AdminNotification(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category: str
    type: str
    message: str
    timestamp: Optional[datetime] = None
    user_id: Optional[str] = None
    details: Any | None = None

    @field_validator("details", mode="before")
    @classmethod
    def bound_details(cls, value: Any) -> Any:
        return _bound_admin_notification_details(value)

class AdminDashboardNotification(BaseModel):
    """Minimal notification projection shown on the dashboard."""

    category: str
    message: str
    timestamp: Optional[datetime] = None


class AdminDashboardResponse(BaseModel):
    """Explicit, secret-free response contract for dashboard summary metrics."""

    active_user_count: int
    pending_user_count: int
    max_concurrent_users_last_week: int
    max_concurrent_users_is_partial: bool
    providers_available: bool
    providers_down_count: int
    providers_total_count: int
    notifications: list[AdminDashboardNotification]
    internet_connectivity: bool
    internet_connectivity_check_enabled: bool
    models_healthy: bool
    models_error_count: int
    models_total_count: int


# -------------------
# Admin: Notifications Page
# -------------------
class AdminNotificationPage(BaseModel):
    items: list[AdminNotification]
    page: int
    page_size: int
    total: int
    has_next: bool
    available_categories: list[str] = []
    available_types: list[str] = []


# -------------------
