from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, Literal

from app.settings.public_urls import normalize_public_urls



# -------------------
# Create group
# -------------------
class PageSettings(BaseModel):
    page_name: str
    data: Dict[str, Any]  # Flexible JSON-Daten



# -------------------
# Create group
# -------------------

# -------------------
# Server Setup Request
# -------------------
class ServerSetupRequest(BaseModel):
    application_name: str = Field(..., min_length=1, max_length=50)
    public_url: list[str] = Field(..., min_length=1)
    default_user_role: Literal["user", "pending"]

    @field_validator("public_url", mode="before")
    @classmethod
    def _normalize_public_urls(cls, value):
        """Accept the legacy scalar request while storing an ordered URL list."""
        return normalize_public_urls(value)


class ServerSetupResponse(BaseModel):
    """Normalized values returned after the initial server setup is committed."""

    status: Literal["success"]
    public_urls: list[str]
    primary_public_url: str
