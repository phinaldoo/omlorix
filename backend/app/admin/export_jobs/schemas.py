from datetime import datetime
from typing import Annotated, Any, Dict

from pydantic import BaseModel, ConfigDict, Field


class AdminUserExportJobCreateRequest(BaseModel):
    """Select all users or an explicit bounded set for a canonical archive."""

    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(..., min_length=3, max_length=255)
    # Reject blank selections at the API boundary so the persisted scope and
    # audit counts always describe the set the worker will actually export.
    user_ids: list[Annotated[str, Field(min_length=1)]] = Field(
        default_factory=list, max_length=200
    )


class AdminUserExportJobResponse(BaseModel):
    """Response schema for a queued all-users export job."""

    id: str
    status: str
    error: str | None = None
    filename: str | None = None
    manifest_json: Dict[str, Any] | None = None
    options_json: Dict[str, Any] | None = None
    size_bytes: int | None = None
    requested_by_user_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    download_ready: bool = False
