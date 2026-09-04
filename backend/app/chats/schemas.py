from datetime import datetime
from enum import Enum
import json
import math
from typing import Optional, Any, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictBool,
    field_validator,
    model_validator,
)

from app.tools.subagents.schemas import SUBAGENT_MAX_SELECTED_TARGETS, SubagentTargetRef



# -------------------
# Chat
# -------------------
class Chat(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: Optional[str] = None
    project_id: Optional[str] = None
    share_id: Optional[str] = None
    archived: bool
    pinned_position: Optional[int] = None
    meta: Optional[dict[str, Any]] = None
    created_at: datetime
    last_updated_at: datetime
    has_unread_response: bool = False

    @field_validator("archived", mode="before")
    @classmethod
    def _normalize_archived(cls, value):
        return bool(value)

    @field_validator("meta", mode="before")
    @classmethod
    def _normalize_meta(cls, value):
        if isinstance(value, dict) or value is None:
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

class ChatListPage(BaseModel):
    pinned: list[Chat]
    items: list[Chat]
    total_pinned: int
    pinned_has_more: bool
    total_unpinned: int
    has_more: bool
    offset: int
    limit: int


class ChatReadResponse(BaseModel):
    """Confirm the durable read state after a chat becomes visible."""

    chat_id: str
    has_unread_response: bool = False


class ChatAttentionQuery(BaseModel):
    """Request unread flags for the bounded set of chat rows on screen."""

    chat_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("chat_ids")
    @classmethod
    def _normalize_chat_ids(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        if len(normalized) > 200:
            raise ValueError("No more than 200 chat IDs may be queried at once.")
        return normalized


class ChatAttentionQueryResponse(BaseModel):
    """Return unread flags without exposing internal response counters."""

    unread_by_chat_id: dict[str, bool]


class ChatReferenceCandidate(BaseModel):
    """A chat that the current user may attach to another conversation."""

    chat_id: str
    title: str
    last_updated_at: datetime | None = None
    snippet: str = ""
    message_count: int = 0
    estimated_chars: int = 0


class ChatReferenceCandidatePage(BaseModel):
    """A bounded page used by the composer's infinite-scrolling chat picker."""

    items: list[ChatReferenceCandidate]
    total_count: int
    has_more: bool
    offset: int
    limit: int



# -------------------
# Chat File Attachment
# -------------------
class ChatFileAttachment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None

# -------------------
# Chat Message
# -------------------
class ChatMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: Optional[Any] = None

CHAT_IMPORT_MAX_CHATS = 20000
CHAT_IMPORT_MAX_MESSAGES_PER_CHAT = 5000
CHAT_IMPORT_MAX_DEEP_RESEARCH_RUNS_PER_CHAT = 250
CHAT_IMPORT_MAX_DEEP_RESEARCH_BYTES_PER_CHAT = 64 * 1024 * 1024
CHAT_IMPORT_MAX_REFERENCE_MAP_ENTRIES = 20000
CHAT_IMPORT_MAX_TITLE_LENGTH = 4096
CHAT_IMPORT_MAX_IDENTIFIER_LENGTH = 255
CHAT_IMPORT_MAX_MODEL_ID_LENGTH = 512
CHAT_IMPORT_MAX_TEXT_FIELD_LENGTH = 200000
# Assistant message blocks may contain a complete token-by-token Subagent run.
# A generous per-message limit accommodates those transcripts, while the
# aggregate cap prevents thousands of individually valid messages from
# exhausting memory during validation or persistence.
CHAT_IMPORT_MAX_MESSAGE_CONTENT_LENGTH = 16 * 1024 * 1024
CHAT_IMPORT_MAX_MESSAGE_BYTES_PER_CHAT = 64 * 1024 * 1024
CHAT_IMPORT_MAX_PINNED_POSITION = 1000000
CHAT_IMPORT_MAX_RETRY_COUNT = 1000000
CHAT_CONTEXT_SELECTION_MAX_ITEMS = 20
CHAT_REFERENCE_SELECTION_MAX_ITEMS = 5


def _validate_import_field_size(value: Any, field_name: str, max_length: int = CHAT_IMPORT_MAX_TEXT_FIELD_LENGTH) -> Any:
    if value is None:
        return value
    if isinstance(value, str):
        if len(value) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer.")
        return value
    try:
        serialized = json.dumps(value)
    except Exception:
        serialized = str(value)
    if len(serialized) > max_length:
        raise ValueError(f"{field_name} must be {max_length} characters or fewer.")
    return value


def _import_value_size_bytes(value: Any) -> int:
    """Return the UTF-8 size of one imported scalar or structured value.

    Structured message content must be serialized to measure its actual JSON
    footprint. The caller caches this result on the validated message so the
    per-chat aggregate check does not allocate a second serialized copy.
    """

    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        serialized = json.dumps(value, separators=(",", ":"))
    except Exception:
        serialized = str(value)
    return len(serialized.encode("utf-8"))


class ChatImportRole(str, Enum):
    user = "user"
    assistant = "assistant"


class _ChatImportSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImportedChatRecord(_ChatImportSchema):
    id: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_IDENTIFIER_LENGTH)
    user_id: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_IDENTIFIER_LENGTH)
    title: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_TITLE_LENGTH)
    project_id: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_IDENTIFIER_LENGTH)
    share: Optional[dict[str, Any]] = None
    archived: StrictBool = False
    pinned_position: Optional[int] = Field(default=None, ge=0, le=CHAT_IMPORT_MAX_PINNED_POSITION)
    meta: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    last_updated_at: Optional[datetime] = None
    has_unread_response: StrictBool = False

    @field_validator("share", "meta")
    @classmethod
    def _validate_json_fields(cls, value, info):
        return _validate_import_field_size(value, info.field_name)


class ImportedChatMessage(_ChatImportSchema):
    _content_size_bytes: int = PrivateAttr(default=0)

    id: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_IDENTIFIER_LENGTH)
    model_id: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_MODEL_ID_LENGTH)
    role: ChatImportRole = Field(default=ChatImportRole.user)
    content: Optional[Any] = None
    reference_id: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_IDENTIFIER_LENGTH)
    generation: Optional[Any] = None
    thinking: Optional[Any] = None
    retry_count: Optional[int] = Field(default=None, ge=0, le=CHAT_IMPORT_MAX_RETRY_COUNT)
    bookmarked: StrictBool = False
    images: Optional[Any] = None
    videos: Optional[Any] = None
    audios: Optional[Any] = None
    documents: Optional[Any] = None
    youtube: Optional[Any] = None
    sources: Optional[Any] = None
    name: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_MODEL_ID_LENGTH)
    tool_name: Optional[str] = Field(default=None, max_length=CHAT_IMPORT_MAX_MODEL_ID_LENGTH)
    meta: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None

    @field_validator(
        "generation",
        "thinking",
        "images",
        "videos",
        "audios",
        "documents",
        "youtube",
        "sources",
        "meta",
    )
    @classmethod
    def _validate_sized_fields(cls, value, info):
        return _validate_import_field_size(value, info.field_name)

    @model_validator(mode="after")
    def _validate_message_content(self):
        """Measure content once and retain its byte count for aggregate validation."""

        content_size = _import_value_size_bytes(self.content)
        if content_size > CHAT_IMPORT_MAX_MESSAGE_CONTENT_LENGTH:
            raise ValueError(
                "content exceeds the per-message import content limit."
            )
        self._content_size_bytes = content_size
        return self


class ImportedChatEntry(_ChatImportSchema):
    chat: ImportedChatRecord
    messages: list[ImportedChatMessage] = Field(
        default_factory=list,
        max_length=CHAT_IMPORT_MAX_MESSAGES_PER_CHAT,
    )
    deep_research_runs: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=CHAT_IMPORT_MAX_DEEP_RESEARCH_RUNS_PER_CHAT,
    )

    @field_validator("messages")
    @classmethod
    def _validate_total_message_bytes(
        cls,
        value: list[ImportedChatMessage],
    ) -> list[ImportedChatMessage]:
        """Bound total message content retained by one imported chat."""

        total_bytes = 0
        for message in value:
            total_bytes += message._content_size_bytes
            if total_bytes > CHAT_IMPORT_MAX_MESSAGE_BYTES_PER_CHAT:
                raise ValueError(
                    "messages exceed the per-chat import content limit."
                )
        return value

    @field_validator("deep_research_runs")
    @classmethod
    def _validate_deep_research_size(
        cls,
        value: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Bound embedded reports and base64 artifacts before import work starts."""

        serialized = json.dumps(value, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > CHAT_IMPORT_MAX_DEEP_RESEARCH_BYTES_PER_CHAT:
            raise ValueError(
                "deep_research_runs exceeds the 64 MB per-chat import limit."
            )
        return value


class ChatImportData(_ChatImportSchema):
    chats: list[dict[str, Any]] = Field(default_factory=list, max_length=CHAT_IMPORT_MAX_CHATS)
    count: int = Field(default=0, ge=0, le=CHAT_IMPORT_MAX_CHATS)
    user_reference_map: dict[str, str] = Field(
        default_factory=dict,
        max_length=CHAT_IMPORT_MAX_REFERENCE_MAP_ENTRIES,
    )

    @field_validator("user_reference_map")
    @classmethod
    def _validate_reference_map(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if len(key) > CHAT_IMPORT_MAX_IDENTIFIER_LENGTH:
                raise ValueError(
                    f"user_reference_map keys must be {CHAT_IMPORT_MAX_IDENTIFIER_LENGTH} characters or fewer."
                )
            if len(item) > CHAT_IMPORT_MAX_TEXT_FIELD_LENGTH:
                raise ValueError(
                    f"user_reference_map values must be {CHAT_IMPORT_MAX_TEXT_FIELD_LENGTH} characters or fewer."
                )
        return value

    @model_validator(mode="after")
    def _validate_count(self):
        if self.count != len(self.chats):
            raise ValueError("data.count must match the number of chats.")
        return self


class ChatExportImportPayload(_ChatImportSchema):
    export_type: Literal["chats"]
    export_version: float | int | str
    generated_at: Optional[datetime] = None
    data: ChatImportData


class OpenWebUIChatImportRequest(_ChatImportSchema):
    chats: list[dict[str, Any]] = Field(min_length=1, max_length=CHAT_IMPORT_MAX_CHATS)
    force_archived: StrictBool = False


class AdminOpenWebUIChatImportRequest(OpenWebUIChatImportRequest):
    user_id: str = Field(..., min_length=1, max_length=CHAT_IMPORT_MAX_IDENTIFIER_LENGTH)


class AdminOpenWebUIBulkImportRequest(_ChatImportSchema):
    users_csv: str = Field(..., min_length=1, max_length=10 * 1024 * 1024)
    chats: list[dict[str, Any]] = Field(min_length=1, max_length=CHAT_IMPORT_MAX_CHATS)


class OpenWebUIChatImportResult(_ChatImportSchema):
    imported_chats: int = Field(ge=0)
    imported_messages: int = Field(ge=0)
    imported_branches: int = Field(ge=0)
    skipped_chats: int = Field(ge=0)
    skipped_branches: int = Field(ge=0)
    skipped_messages: int = Field(ge=0)


class ChatGPTArchiveImportResult(_ChatImportSchema):
    imported_chats: int = Field(ge=0)
    imported_messages: int = Field(ge=0)
    imported_files: int = Field(ge=0)
    skipped_chats: int = Field(ge=0)
    skipped_duplicates: int = Field(ge=0)
    shared_index_entries: int = Field(ge=0)


class AdminOpenWebUIBulkImportResult(OpenWebUIChatImportResult):
    matched_users: int = Field(ge=0)
    skipped_users: int = Field(ge=0)


class RetryGuidanceMode(str, Enum):
    default = "default"
    preset = "preset"
    custom = "custom"


class RetryGuidancePreset(str, Enum):
    try_again = "try_again"
    add_details = "add_details"
    more_concise = "more_concise"


RETRY_GUIDANCE_MAX_CHARS = 2000


class RetryGuidance(BaseModel):
    mode: RetryGuidanceMode = Field(default=RetryGuidanceMode.default)
    preset: Optional[RetryGuidancePreset] = Field(default=None)
    instruction: Optional[str] = Field(default=None, max_length=RETRY_GUIDANCE_MAX_CHARS)

    @field_validator("instruction", mode="before")
    @classmethod
    def _normalize_instruction(cls, value):
        if value is None:
            return None
        return str(value).strip()

    @model_validator(mode="after")
    def _validate_mode_fields(self):
        if self.mode == RetryGuidanceMode.default:
            self.preset = None
            self.instruction = None
            return self

        if self.mode == RetryGuidanceMode.preset:
            if self.preset is None:
                raise ValueError("preset is required when retry_guidance.mode is 'preset'")
            self.instruction = None
            return self

        if self.mode == RetryGuidanceMode.custom:
            if not self.instruction:
                self.mode = RetryGuidanceMode.default
                self.preset = None
                self.instruction = None
                return self
            self.preset = None
            return self

        return self




SAFE_CUSTOM_MODEL_SETTING_KEYS = frozenset({
    # Conversation context controls exposed in the per-message settings UI.
    "use_group_context",
    "use_project_context",
    "enabled_tools",
    "enabled_mcp_servers",
    # Hosted tool discovery for OpenAI-style tool routing.
    "tool_search",
    # Thinking/reasoning controls.
    "thinking",
    "thinking_adaptive",
    "thinking_budget",
    "thinking_dynamic",
    "include_thinking",
    "reasoning",
    "reasoning_enabled",
    "reasoning_mode",
    "reasoning_context",
    "reasoning_effort",
    "reasoning_max_tokens",
    "reasoning_exclude",
    "reasoning_summary",
    # GPT-5.6 prompt-cache controls exposed in the per-message settings UI.
    "prompt_cache_override",
    "prompt_cache_ttl",
    "prompt_cache_key",
    # Common generation parameters.
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "max_output_tokens",
    "stop",
    "stop_sequences",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "verbosity",
    # Google AI Studio parameters exposed to users.
    "video_fps",
    "media_resolution",
    "safety_harassment",
    "safety_hate_speech",
    "safety_sexually_explicit",
    "safety_dangerous_content",
    "safety_civic_integrity",
    # OpenAI image input controls exposed to users.
    "image_detail",
    # OpenRouter generation parameters exposed to users.
    "repetition_penalty",
    "min_p",
    "top_a",
    "logit_bias",
    # OpenAI response controls exposed to users.
    "store",
    "send_user_identifier",
    "priority_processing",
    # Ollama generation/runtime parameters exposed in the per-message UI.
    "num_keep",
    "num_predict",
    "typical_p",
    "repeat_last_n",
    "repeat_penalty",
    "penalize_newline",
    "numa",
    "num_ctx",
    "num_batch",
    "num_gpu",
    "main_gpu",
    "use_mmap",
    "num_thread",
    "keep_alive",
})


class SendChatRequestCustomSettingsValues(BaseModel):
    """Allowlisted per-message model settings users may override."""

    model_config = ConfigDict(extra="forbid")

    use_group_context: Optional[bool] = None
    use_project_context: Optional[bool] = None
    enabled_tools: Optional[list[str]] = None
    enabled_mcp_servers: Optional[list[str]] = None
    tool_search: Optional[bool] = None

    thinking: Optional[bool] = None
    thinking_adaptive: Optional[bool] = None
    thinking_budget: Optional[int] = None
    thinking_dynamic: Optional[bool] = None
    include_thinking: Optional[bool] = None
    reasoning: Optional[bool] = None
    reasoning_enabled: Optional[bool] = None
    reasoning_mode: Optional[str] = None
    reasoning_context: Optional[Literal["auto", "current_turn", "all_turns"]] = None
    reasoning_effort: Optional[str] = None
    reasoning_max_tokens: Optional[int] = Field(default=None, ge=1)
    reasoning_exclude: Optional[bool] = None
    reasoning_summary: Optional[str] = None
    prompt_cache_override: Optional[bool] = None
    prompt_cache_ttl: Optional[Literal["30m"]] = None
    prompt_cache_key: Optional[str] = None

    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    stop: Optional[list[str]] = None
    stop_sequences: Optional[list[str]] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    seed: Optional[int] = None
    verbosity: Optional[str] = None

    video_fps: Optional[float] = None
    media_resolution: Optional[str] = None
    safety_harassment: Optional[str] = None
    safety_hate_speech: Optional[str] = None
    safety_sexually_explicit: Optional[str] = None
    safety_dangerous_content: Optional[str] = None
    safety_civic_integrity: Optional[str] = None
    image_detail: Optional[Literal["auto", "low", "high", "original"]] = None

    repetition_penalty: Optional[float] = None
    min_p: Optional[float] = None
    top_a: Optional[float] = None
    logit_bias: Optional[dict[str, float]] = None

    @field_validator("logit_bias")
    @classmethod
    def _validate_logit_bias(cls, value):
        if value is None:
            return value
        normalized: dict[str, float] = {}
        for raw_token_id, raw_bias in value.items():
            token_id = str(raw_token_id).strip()
            if not token_id.isdigit():
                raise ValueError("logit_bias keys must be non-negative token IDs")
            bias = float(raw_bias)
            if not math.isfinite(bias) or bias < -100 or bias > 100:
                raise ValueError("logit_bias values must be finite numbers from -100 to 100")
            normalized[token_id] = bias
        return normalized

    store: Optional[bool] = None
    send_user_identifier: Optional[bool] = None
    priority_processing: Optional[str] = None

    num_keep: Optional[int] = None
    num_predict: Optional[int] = None
    typical_p: Optional[float] = None
    repeat_last_n: Optional[int] = None
    repeat_penalty: Optional[float] = None
    penalize_newline: Optional[bool] = None
    numa: Optional[bool] = None
    num_ctx: Optional[int] = None
    num_batch: Optional[int] = None
    num_gpu: Optional[int] = None
    main_gpu: Optional[int] = None
    use_mmap: Optional[bool] = None
    num_thread: Optional[int] = None
    keep_alive: Optional[int] = None


class SendChatRequestModelSettings(BaseModel):
    """Validated wrapper for user-supplied per-message model overrides.

    Provider model settings contain admin-controlled limits and feature flags.
    This schema intentionally accepts only the conversation/generation controls
    exposed by the custom settings UI, preventing crafted requests from
    overriding resource limits, input formats, native web search, or provider
    routing policy.
    """

    model_config = ConfigDict(extra="forbid")

    # This intentionally overrides the administrator-configured model system
    # instruction for the current request.  It remains top-level because the
    # provider settings merger already treats top-level request values as
    # overrides, while the other adjustable controls live under ``settings``.
    # An omitted/empty sidebar value is not serialized by the client, so the
    # administrator value remains the fallback.
    system_instruction: Optional[str] = Field(default=None, max_length=50_000)
    settings: Optional[SendChatRequestCustomSettingsValues] = None

    @field_validator("system_instruction", mode="before")
    @classmethod
    def _normalize_system_instruction_override(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="before")
    @classmethod
    def _support_flat_safe_settings(cls, value):
        if not isinstance(value, dict):
            return value
        safe_flat_settings = {
            key: value[key]
            for key in list(value.keys())
            if key in SAFE_CUSTOM_MODEL_SETTING_KEYS
        }
        if not safe_flat_settings:
            return value
        normalized = dict(value)
        existing_settings = normalized.get("settings")
        if existing_settings is None:
            existing_settings = {}
        if not isinstance(existing_settings, dict):
            return value
        for key in safe_flat_settings:
            normalized.pop(key, None)
        normalized["settings"] = {**safe_flat_settings, **existing_settings}
        return normalized

    def as_override_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)

# -------------------
# Send Chat Request
# -------------------
class SendChatRequest(BaseModel):
    """
    Request body for POST /chats/send

    Fields mirror the current function parameters in app/chats/router.py::send()
    so this can be adopted without changing semantics.
    """

    generation_id: UUID = Field(
        default_factory=uuid4,
        description="Client-created generation ID used for streaming and immediate cancellation.",
    )
    model_id: str | None = None # Field(None, description="ID of the model to use for the chat message")
    message: str = Field(..., description="User message content to send to the model")
    chat_id: Optional[str] = Field("", description="Existing chat ID; empty string to start a new chat")
    image_ids: Optional[list[str]] = Field(
        default=None,
        description="Optional list of image file IDs or stored names (<uuid> or <uuid>___<original>) to attach to this message",
    )
    video_ids: Optional[list[str]] = Field(
        default=None,
        description="Optional list of video file IDs to attach (mp4, mpeg/mpg, mov, avi, flv, webm, wmv, 3gpp)",
    )
    audio_ids: Optional[list[str]] = Field(
        default=None,
        description="Optional list of audio file IDs to attach (wav, mp3, aiff, aac, ogg, flac)",
    )
    document_ids: Optional[list[str]] = Field(
        default=None,
        description="Optional list of document file IDs to attach (pdf)",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Optional project ID. When provided, all files of the project will be included in this send (provider-specific support applies).",
    )
    temp_chat: Optional[str] = Field(
        default=None,
        description=(
            "Optional temporary chat content. Depending on group policy, temporary chats may be kept in storage "
            "(for example when save_temp_chats is enabled) and removed later by retention cleanup."
        ),
    )
    skill_id: Optional[str] = Field(
        default=None,
        description="Optional single skill ID to apply to this message (legacy compatibility)",
    )
    skill_ids: Optional[list[str]] = Field(
        default=None,
        max_length=CHAT_CONTEXT_SELECTION_MAX_ITEMS,
        description="Optional list of skill IDs to apply to this message",
    )
    note_ids: Optional[list[str]] = Field(
        default=None,
        max_length=CHAT_CONTEXT_SELECTION_MAX_ITEMS,
        description="Optional list of note IDs to include with this message",
    )
    prompt_ids: Optional[list[str]] = Field(
        default=None,
        max_length=CHAT_CONTEXT_SELECTION_MAX_ITEMS,
        description="Optional list of prompt library IDs to include with this message",
    )
    reference_parts: Optional[list[str]] = Field(
        default=None,
        description="Optional list of text snippets selected from previous assistant messages to reference in this message",
    )
    chat_reference_ids: Optional[list[str]] = Field(
        default=None,
        max_length=CHAT_REFERENCE_SELECTION_MAX_ITEMS,
        description="Optional list of chat IDs to attach as full-transcript context for this message.",
    )
    subagent_targets: Optional[list[SubagentTargetRef]] = Field(
        default=None,
        max_length=SUBAGENT_MAX_SELECTED_TARGETS,
        description=(
            "Optional exact model or Agent targets the parent may delegate to. "
            "Null permits any accessible target; an explicit list is enforced as a strict allowlist."
        ),
    )

    @field_validator("subagent_targets")
    @classmethod
    def _deduplicate_subagent_targets(cls, values):
        if values is None:
            return None
        deduplicated = []
        seen: set[tuple[str, str]] = set()
        for value in values:
            key = (value.type, value.id)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(value)
        return deduplicated


class SaveTemporaryChatRequest(BaseModel):
    temp_chat: str = Field(
        ...,
        description="Serialized temporary chat transcript captured from the client.",
    )
    model_id: Optional[str] = Field(
        default=None,
        description="Active model ID to associate with restored messages when no per-message model is present.",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Optional project ID to attach to the newly saved chat.",
    )


class MarkdownCodeExecutionRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Python code to execute from a rendered markdown code block.")
    chat_id: Optional[str] = Field(
        default=None,
        description="Optional chat ID to reuse the chat-scoped code execution container.",
    )


class VegaPreviewResourceRequest(BaseModel):
    """A remote data or asset URL approved by the user for one Vega preview."""

    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Public HTTP(S) URL requested by an approved Vega preview.",
    )


class MarkdownCodeExecutionFile(BaseModel):
    file_id: str
    name: str
    mime_type: str
    file_category: Optional[str] = None
    size: Optional[int] = None


class MarkdownCodeExecutionResponse(BaseModel):
    ok: bool = True
    available: bool = True
    language: str = "python"
    execution_id: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    error_type: Optional[str] = None
    execution_time: Optional[float] = None
    timed_out: bool = False
    files_generated: int = 0
    files: list[MarkdownCodeExecutionFile] = Field(default_factory=list)


# -------------------
# Share Chat Request
# -------------------
class ChatSharePublicationSelection(BaseModel):
    """Owner-reviewed choices that define the public transcript projection."""

    model_config = ConfigDict(extra="forbid")

    response_versions: dict[str, str] = Field(
        default_factory=dict,
        description="User-message reference IDs mapped to the assistant message version selected for publication.",
    )
    approved_output_ids: list[str] = Field(
        default_factory=list,
        max_length=1000,
        description="Stable hashes for static tool-output projections explicitly approved by the owner.",
    )

    @field_validator("response_versions")
    @classmethod
    def _validate_response_versions(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 500:
            raise ValueError("No more than 500 response-version selections may be published.")
        normalized: dict[str, str] = {}
        for raw_reference_id, raw_message_id in value.items():
            reference_id = str(raw_reference_id or "").strip()
            message_id = str(raw_message_id or "").strip()
            if not reference_id or not message_id:
                raise ValueError("Response-version selections require non-empty reference and message IDs.")
            if len(reference_id) > CHAT_IMPORT_MAX_IDENTIFIER_LENGTH or len(message_id) > CHAT_IMPORT_MAX_IDENTIFIER_LENGTH:
                raise ValueError("Response-version selection IDs are too long.")
            normalized[reference_id] = message_id
        return normalized

    @field_validator("approved_output_ids")
    @classmethod
    def _validate_approved_output_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw_output_id in value:
            output_id = str(raw_output_id or "").strip().lower()
            if len(output_id) != 64 or any(character not in "0123456789abcdef" for character in output_id):
                raise ValueError("Approved output IDs must be SHA-256 hashes.")
            if output_id not in normalized:
                normalized.append(output_id)
        return normalized


class ChatShareStaticOutputOption(BaseModel):
    """One safe static projection that the owner may approve for publication."""

    id: str
    output_type: str
    title: str
    preview: dict[str, Any]
    approved: bool = False


class ChatShareResponseVersionOption(BaseModel):
    """One persisted assistant version available to the sharing owner."""

    message_id: str
    retry_count: int
    preview: str
    selected: bool = False
    static_outputs: list[ChatShareStaticOutputOption] = Field(default_factory=list)


class ChatSharePublicationTurn(BaseModel):
    """All saved response versions for one user-message turn."""

    reference_id: str
    prompt_preview: str
    versions: list[ChatShareResponseVersionOption] = Field(default_factory=list)


class ChatSharePublicationOptionsResponse(BaseModel):
    """Owner-only review model used by the share dialog."""

    publication: ChatSharePublicationSelection
    turns: list[ChatSharePublicationTurn] = Field(default_factory=list)


class ShareChatRequest(BaseModel):
    """Request body for creating a fresh active share link for a chat."""

    chat_id: str = Field(..., description="The ID of the chat to share")
    access_mode: Optional[str] = Field(
        default=None,
        description="Who may open the share link. Supported values: public, authenticated, invited.",
    )
    password: Optional[str] = Field(
        default=None,
        description="Optional password that will be required to access the shared chat",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Optional UTC datetime when the share expires (ISO 8601). After this, access is denied and the share is removed.",
    )
    publication: Optional[ChatSharePublicationSelection] = Field(
        default=None,
        description="Owner-reviewed answer versions and static tool outputs to publish.",
    )


class InviteChatUsersRequest(BaseModel):
    """Request body for inviting users to a shared chat."""

    chat_id: str = Field(..., description="The ID of the chat to invite users to")
    user_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="User IDs to invite",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Optional UTC datetime when the invitation share link expires.",
    )
    publication: Optional[ChatSharePublicationSelection] = Field(
        default=None,
        description="Owner-reviewed answer versions and static tool outputs to publish.",
    )


class InviteChatUsersResponse(BaseModel):
    share_id: str
    share_url: str
    access_mode: str
    invited_count: int
    message: str
    created_at: str | None = None
    expires_at: str | None = None
    invited_user_ids: list[str] = Field(default_factory=list)
    publication: ChatSharePublicationSelection = Field(default_factory=ChatSharePublicationSelection)


class UpdateChatSharePublicationRequest(BaseModel):
    """Replace the reviewed publication choices for an active chat share."""

    chat_id: str = Field(..., description="The ID of the chat whose public projection should be updated")
    publication: ChatSharePublicationSelection



# -------------------
# Access Shared Chat Request
# -------------------
class AccessSharedChatRequest(BaseModel):
    """Request body for accessing a shared chat."""

    share_id: str = Field(..., description="The share ID returned when a chat was shared")
    password: Optional[str] = Field(
        default=None,
        description="Password required if the share was protected",
    )
    share_access_token: Optional[str] = Field(
        default=None,
        description="Short-lived token returned after a successful share unlock",
    )
    known_updated_at: Optional[str] = Field(
        default=None,
        description="Last shared chat updated_at value already known by the client. If unchanged, messages are omitted.",
    )



# -------------------
# Delete Share Request
# -------------------
class DeleteShareRequest(BaseModel):
    """Request body to delete/unshare a chat."""

    chat_id: str = Field(..., description="The ID of the chat to unshare")



# -------------------
# Remove Share Password Request
# -------------------
class RemoveSharePasswordRequest(BaseModel):
    """Request body to remove password protection from a shared chat."""

    chat_id: str = Field(..., description="The ID of the chat whose share password should be removed")


class ChangeShareAccessModeRequest(BaseModel):
    """Request body to change who may access an existing shared chat."""

    chat_id: str = Field(..., description="The ID of the chat whose share access mode should be changed")
    access_mode: str = Field(
        ...,
        description="Supported values: public, authenticated. Use the invite endpoint to create invited-user shares.",
    )



# -------------------
# Add Share Password Request
# -------------------
class AddSharePasswordRequest(BaseModel):
    """Request body to add password protection to an existing shared chat."""

    chat_id: str = Field(..., description="The ID of the chat whose share should be protected")
    password: str = Field(..., description="The password to set for the share")



# -------------------
# Change Share Password Request
# -------------------
class ChangeSharePasswordRequest(BaseModel):
    """Request body to change the password for an existing shared chat."""

    chat_id: str = Field(..., description="The ID of the chat whose share password should be changed")
    password: str = Field(..., description="The new password")



# -------------------
# Create Share Expiry Request
# -------------------
class CreateShareExpiryRequest(BaseModel):
    """Request body to create/set an expiry for an existing shared chat (if not set)."""

    chat_id: str = Field(..., description="The ID of the chat")
    expires_at: datetime = Field(..., description="UTC datetime when the share expires (ISO 8601)")



# -------------------
# Change Share Expiry Request
# -------------------
class ChangeShareExpiryRequest(BaseModel):
    """Request body to change the expiry for an existing shared chat."""

    chat_id: str = Field(..., description="The ID of the chat")
    expires_at: datetime = Field(..., description="New UTC datetime when the share expires (ISO 8601)")



# -------------------
# Delete Share Expiry Request
# -------------------
class DeleteShareExpiryRequest(BaseModel):
    """Request body to delete/remove the expiry for an existing shared chat."""

    chat_id: str = Field(..., description="The ID of the chat")



# -------------------
# Pin Chat Request
# -------------------
class PinChatRequest(BaseModel):
    """Pin a chat at an optional position (defaults to bottom of pinned list)."""
    chat_id: str = Field(..., description="The ID of the chat to pin")
    position: Optional[int] = Field(
        default=None,
        description="Target pin position starting at 1. If omitted, append to end.",
        ge=1,
    )



# -------------------
# Unpin Chat Request
# -------------------
class UnpinChatRequest(BaseModel):
    """Unpin a chat and compact other pinned positions accordingly."""
    chat_id: str = Field(..., description="The ID of the chat to unpin")



# -------------------
# Move Pinned Chat Request
# -------------------
class MovePinnedChatRequest(BaseModel):
    """Move a pinned chat to a new position."""
    chat_id: str = Field(..., description="The ID of the chat to move")
    position: int = Field(..., description="New pin position starting at 1", ge=1)


# -------------------
# Update Chat Project Request
# -------------------
class UpdateChatProjectRequest(BaseModel):
    """Request body for updating chat project association."""
    chat_id: str = Field(..., description="The ID of the chat")
    project_id: Optional[str] = Field(
        default=None,
        description="Project ID to assign. Pass null to remove the association.",
    )



# -------------------
# Archive Chat Request
# -------------------
class ArchiveChatRequest(BaseModel):
    """Request body for archiving a chat."""
    chat_id: str = Field(..., description="The ID of the chat to archive")



# -------------------
# Unarchive Chat Request
# -------------------
class UnarchiveChatRequest(BaseModel):
    """Request body for unarchiving a chat."""
    chat_id: str = Field(..., description="The ID of the chat to unarchive")



# -------------------
# Edit Message Request
# -------------------
class EditMessageRequest(BaseModel):
    """Request body for editing a user message."""
    message_id: str = Field(..., description="The ID of the message to edit")
    content: str = Field(..., description="The new content for the message")
    image_ids: Optional[list[str]] = Field(default=None, description="Updated image attachment IDs")
    video_ids: Optional[list[str]] = Field(default=None, description="Updated video attachment IDs")
    audio_ids: Optional[list[str]] = Field(default=None, description="Updated audio attachment IDs")
    document_ids: Optional[list[str]] = Field(default=None, description="Updated document attachment IDs")
    chat_reference_ids: Optional[list[str]] = Field(default=None, description="Updated chat reference IDs")



# -------------------
# Regenerate Message Request
# -------------------
class RegenerateMessageRequest(BaseModel):
    """Request body for regenerating an assistant message."""
    generation_id: UUID = Field(
        default_factory=uuid4,
        description="Client-created generation ID used for streaming and immediate cancellation.",
    )
    chat_id: str = Field(..., description="The ID of the chat")
    user_message_id: str = Field(..., description="The ID of the user message to regenerate response for")
    model_id: Optional[str] = Field(default=None, description="Optional model ID to use for regeneration (uses current selected model)")
    skill_id: Optional[str] = Field(default=None, description="Optional single skill ID to apply (legacy compatibility)")
    skill_ids: Optional[list[str]] = Field(
        default=None,
        max_length=CHAT_CONTEXT_SELECTION_MAX_ITEMS,
        description="Optional list of skill IDs to apply",
    )
    note_ids: Optional[list[str]] = Field(
        default=None,
        max_length=CHAT_CONTEXT_SELECTION_MAX_ITEMS,
        description="Optional list of note IDs to include",
    )
    prompt_ids: Optional[list[str]] = Field(
        default=None,
        max_length=CHAT_CONTEXT_SELECTION_MAX_ITEMS,
        description="Optional list of prompt IDs to include",
    )
    chat_reference_ids: Optional[list[str]] = Field(
        default=None,
        max_length=CHAT_REFERENCE_SELECTION_MAX_ITEMS,
        description="Optional list of chat IDs to attach as context for regeneration. Defaults to stored message references when omitted.",
    )
    subagent_targets: Optional[list[SubagentTargetRef]] = Field(
        default=None,
        max_length=SUBAGENT_MAX_SELECTED_TARGETS,
        description=(
            "Optional strict delegation-target allowlist for this regenerated response. "
            "Null permits any accessible target."
        ),
    )
    retry_guidance: Optional[RetryGuidance] = Field(
        default=None,
        description="Optional regeneration guidance for the next assistant attempt.",
    )

    @field_validator("retry_guidance")
    @classmethod
    def _normalize_retry_guidance(cls, value):
        if isinstance(value, RetryGuidance) and value.mode == RetryGuidanceMode.default:
            return None
        return value



# -------------------
# Toggle Bookmark Request
# -------------------
class ToggleBookmarkRequest(BaseModel):
    """Request body for toggling bookmark status of a message."""
    message_id: str = Field(..., description="The ID of the message (user or assistant) to bookmark/unbookmark")


class AssistantReadAloudRequest(BaseModel):
    """Request body for generating or replaying assistant message read aloud audio."""

    text: str = Field(..., min_length=1, description="Assistant message text to convert into speech.")
    message_id: str = Field(
        ...,
        min_length=1,
        description="Assistant message ID used to validate read aloud requests and caching.",
    )
