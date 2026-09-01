"""Pydantic request, response, and transfer schemas for model feedback."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FeedbackReaction(str, Enum):
    """Supported reactions to an assistant response."""

    thumbs_up = "thumbs_up"
    thumbs_down = "thumbs_down"


class FeedbackReactionRequest(BaseModel):
    """Validate a user's feedback submission."""

    message_id: str = Field(..., min_length=1)
    reaction: FeedbackReaction
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    """Serialize a persisted feedback ORM object for the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    model_id: str
    message_id: str
    user_id: str
    reaction: FeedbackReaction
    comment: str | None
    created_at: datetime
    updated_at: datetime
