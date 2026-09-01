from __future__ import annotations

from datetime import datetime
import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_REALTIME_ID_LENGTH = 256
MAX_REALTIME_TEXT_LENGTH = 200_000
MAX_REALTIME_ERROR_LENGTH = 4_096
MAX_REALTIME_FILES = 20
MAX_REALTIME_TOOL_ARGUMENT_BYTES = 64 * 1024
MAX_REALTIME_SDP_LENGTH = 1_000_000

# The list constraint below limits attachment count; this reusable type also
# bounds each untrusted identifier before it reaches file-resolution code.
RealtimeFileId = Annotated[str, Field(max_length=MAX_REALTIME_ID_LENGTH)]


class RealtimeTokenDetails(BaseModel):
    """Bounded token details accepted from an untrusted realtime client."""

    model_config = ConfigDict(extra="ignore")

    audio_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000)
    cached_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000)
    text_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000)


class RealtimeUsage(BaseModel):
    """Provider-neutral, bounded usage values reported by the browser."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000)
    output_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000)
    total_tokens: int | None = Field(default=None, ge=0, le=2_000_000_000)
    input_token_details: RealtimeTokenDetails | None = None
    output_token_details: RealtimeTokenDetails | None = None


class RealtimeProviderInteraction(BaseModel):
    """One terminal provider response observed by the realtime browser."""

    model_config = ConfigDict(extra="ignore")

    response_id: str = Field(min_length=1, max_length=MAX_REALTIME_ID_LENGTH)
    status: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=MAX_REALTIME_ERROR_LENGTH)
    usage: RealtimeUsage | None = None
    completed_at: datetime | None = None


class StartRealtimeSessionRequest(BaseModel):
    chat_id: str | None = Field(
        default=None,
        description="Optional existing chat ID. If omitted, a new chat is created.",
        max_length=MAX_REALTIME_ID_LENGTH,
    )
    model_id: str | None = Field(
        default=None,
        description="Optional model id used to derive enabled tool schemas.",
        max_length=MAX_REALTIME_ID_LENGTH,
    )
    project_id: str | None = Field(
        default=None,
        description="Optional project id used when creating a new chat.",
        max_length=MAX_REALTIME_ID_LENGTH,
    )
    skill_id: str | None = Field(
        default=None,
        description="Optional skill id whose instructions are injected into the realtime session.",
        max_length=MAX_REALTIME_ID_LENGTH,
    )


class PrepareRealtimeInputRequest(BaseModel):
    text: str | None = Field(default=None, max_length=MAX_REALTIME_TEXT_LENGTH)
    file_ids: list[RealtimeFileId] = Field(default_factory=list, max_length=MAX_REALTIME_FILES)


class RealtimeToolCallRequest(BaseModel):
    call_id: str = Field(min_length=1, max_length=MAX_REALTIME_ID_LENGTH)
    turn_id: str = Field(min_length=1, max_length=MAX_REALTIME_ID_LENGTH)
    tool_name: str = Field(min_length=1, max_length=MAX_REALTIME_ID_LENGTH)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_argument_size(self):
        """Reject oversized tool requests before endpoint execution."""
        if len(json.dumps(self.arguments, ensure_ascii=False).encode("utf-8")) > MAX_REALTIME_TOOL_ARGUMENT_BYTES:
            raise ValueError("Realtime tool arguments are too large")
        return self


class RealtimePendingToolCallRequest(BaseModel):
    call_id: str = Field(min_length=1, max_length=MAX_REALTIME_ID_LENGTH)
    tool_name: str = Field(min_length=1, max_length=MAX_REALTIME_ID_LENGTH)


class PersistRealtimeTurnRequest(BaseModel):
    turn_id: str = Field(
        description="Client-generated idempotency key for this realtime turn.",
        min_length=1,
        max_length=MAX_REALTIME_ID_LENGTH,
    )
    user_transcript: str | None = Field(default=None, max_length=MAX_REALTIME_TEXT_LENGTH)
    assistant_transcript: str | None = Field(default=None, max_length=MAX_REALTIME_TEXT_LENGTH)
    file_ids: list[RealtimeFileId] = Field(default_factory=list, max_length=MAX_REALTIME_FILES)
    interrupted: bool = False
    error_message: str | None = Field(default=None, max_length=MAX_REALTIME_ERROR_LENGTH)
    usage: RealtimeUsage | None = Field(default=None)
    provider_interactions: list[RealtimeProviderInteraction] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "Terminal provider responses belonging to this turn. Usage is "
            "browser-relayed and is persisted with unverified provenance."
        ),
    )


class StopRealtimeSessionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=MAX_REALTIME_ID_LENGTH, description="Optional reason for stopping the session.")


class RefreshRealtimeConnectionRequest(BaseModel):
    session_handle: str | None = Field(
        default=None,
        description="Optional Google Live session resumption handle for reconnecting an active session.",
        max_length=4_096,
    )


class RealtimeWebRTCOfferRequest(BaseModel):
    """Bounded browser SDP offer exchanged by the trusted Omlorix backend."""

    sdp: str = Field(
        min_length=1,
        max_length=MAX_REALTIME_SDP_LENGTH,
        description="Browser WebRTC SDP offer.",
    )


class RealtimeWebRTCOfferResponse(BaseModel):
    """Provider SDP answer returned without exposing provider credentials."""

    sdp: str = Field(min_length=1, max_length=MAX_REALTIME_SDP_LENGTH)


class StartRealtimeSessionResponse(BaseModel):
    session_id: str
    chat_id: str
    created_chat: bool = False
    provider: str
    transport: str
    protocol_version: str = "webrtc-v1"
    realtime_call_ready: bool = True
    signaling_url: str | None = None
    websocket_url: str | None = None
    session: dict[str, Any]
    max_session_seconds: int = Field(ge=1, le=86_400)
    session_expires_at: datetime
    session_limit_source: str = Field(default="provider", pattern="^(provider|rate_limit)$")
