from __future__ import annotations

from enum import Enum
from typing import Any, List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ShareTypeEnum(str, Enum):
    """Types of skill sharing."""
    CLONE = "clone"
    LIVE = "live"
    COLLABORATE = "collaborate"


NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=NAME_PATTERN)
    description: str = Field(..., min_length=1, max_length=1024)
    content: str = Field(default="")
    icon: str = Field(..., min_length=1)
    compatibility: str | None = Field(default=None, min_length=1, max_length=500)
    license: str | None = None
    metadata: dict[str, str | int | float | None] | None = None

    @field_validator("metadata")
    @classmethod
    def ensure_metadata_keys(cls, value: dict[str, Any] | None):
        if value is None:
            return None
        cleaned: dict[str, str | int | float | None] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("metadata keys must be non-empty strings")
            cleaned[key.strip()] = item
        return cleaned


class SkillUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    content: str | None = None
    icon: str | None = None
    compatibility: str | None = None
    license: str | None = None
    metadata: dict[str, str | int | float | None] | None = None


class AdminSkillMarkdownImportRequest(BaseModel):
    """A pasted Agent Skills ``SKILL.md`` document for admin import."""

    markdown: str = Field(..., min_length=1, max_length=1024 * 1024)

    @field_validator("markdown")
    @classmethod
    def reject_blank_markdown(cls, value: str) -> str:
        """Reject whitespace-only documents before the import parser runs."""
        if not value.strip():
            raise ValueError("No markdown content provided")
        return value


class AdminSkillImportCreated(BaseModel):
    """A concise reference to one successfully imported admin skill."""

    id: str
    name: str


class AdminSkillImportError(BaseModel):
    """A safe, user-visible error for one file, archive entry, or skill."""

    error: str
    source: str | None = None
    entry: str | None = None
    name: str | None = None
    index: int | None = None


class AdminSkillImportResult(BaseModel):
    """Result shared by pasted Markdown and multi-file admin imports."""

    created: list[AdminSkillImportCreated] = Field(default_factory=list)
    errors: list[AdminSkillImportError] = Field(default_factory=list)


class SkillMarkdownImportCreated(BaseModel):
    """A concise reference to one user skill created by a Markdown upload."""

    id: str
    name: str


class SkillMarkdownImportError(BaseModel):
    """A safe per-file failure returned by the user Markdown importer."""

    source: str
    error: str
    index: int


class SkillMarkdownImportResult(BaseModel):
    """Partial-success result for a batch of uploaded ``SKILL.md`` files."""

    created: list[SkillMarkdownImportCreated] = Field(default_factory=list)
    errors: list[SkillMarkdownImportError] = Field(default_factory=list)


class SkillFileInfo(BaseModel):
    name: str
    size: int


class SkillFilesResponse(BaseModel):
    scripts: list[SkillFileInfo] = []
    references: list[SkillFileInfo] = []
    assets: list[SkillFileInfo] = []


class AdminSkillListItem(BaseModel):
    """The bounded subset of skill data needed to render one list card."""

    id: str
    title: str
    icon: str
    content_preview: str


class AdminSkillListResponse(BaseModel):
    """A server-filtered page of managed skills."""

    items: list[AdminSkillListItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class SkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    description: str | None = None
    content: str
    icon: str
    created_at: str
    updated_at: str
    compatibility: str | None = None
    license: str | None = None
    metadata: dict[str, str | int | float | None] | None = None
    author: str | None = None
    files: SkillFilesResponse | None = None
    is_admin_skill: bool = False
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    is_subscribed: bool = False
    share_type: str | None = None  # 'live' or 'collaborate' for subscribed skills
    owner_name: str | None = None
    subscriber_count: int | None = None

# ============================================================================
# Skill Sharing Schemas
# ============================================================================

class ShareSkillRequest(BaseModel):
    skill_id: str = Field(..., min_length=1)
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class ShareSkillResponse(BaseModel):
    share_id: str
    share_type: str
    share_url: str


class SkillShareStatusResponse(BaseModel):
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    live_subscriber_count: int = 0
    collaborate_subscriber_count: int = 0


class DeleteSkillShareRequest(BaseModel):
    skill_id: str = Field(..., min_length=1)
    share_type: ShareTypeEnum | None = None  # If None, delete all shares


class SharedSkillPreviewResponse(BaseModel):
    share_id: str
    share_type: str
    title: str
    description: str
    icon: str
    content_preview: str | None
    owner_name: str | None = None
    created_at: str | None


class AcceptSharedSkillResponse(BaseModel):
    skill_id: str
    title: str
    message: str


class CloneSkillResponse(BaseModel):
    skill_id: str
    title: str
    message: str


class InviteUsersRequest(BaseModel):
    """Request to invite users to a shared skill."""
    item_id: str
    user_ids: List[str]
    share_type: ShareTypeEnum = ShareTypeEnum.LIVE


class InviteUsersResponse(BaseModel):
    """Response after sending invitations."""
    invited_count: int
    message: str


class SkillDraftFile(BaseModel):
    folder_type: Literal["scripts", "references", "assets"]
    filename: str = Field(..., min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+$")
    content: str | None = None
    encoding: Literal["utf-8", "base64"] = "utf-8"
    source_file_id: str | None = None
    media_type: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError("filename must not contain path separators or traversal segments")
        return value

    @model_validator(mode="after")
    def validate_source_or_content(self):
        if self.source_file_id and self.content not in (None, ""):
            raise ValueError("content must be omitted when source_file_id is provided")
        if self.encoding == "base64" and self.source_file_id:
            raise ValueError("base64 encoding cannot be used with source_file_id")
        if self.content in (None, "") and not self.source_file_id:
            raise ValueError("either content or source_file_id must be provided")
        return self


class SaveSkillDraftRequest(BaseModel):
    skill_markdown: str = Field(..., min_length=1, max_length=1024 * 1024)
    icon: str | None = Field(default=None, min_length=1)
    files: list[SkillDraftFile] = Field(default_factory=list)


class SaveSkillDraftResponse(BaseModel):
    skill_id: str
    title: str
    message: str


class SkillCatalogItem(BaseModel):
    """List metadata and preview; a detail read is required before editing."""
    id: str
    user_id: str
    title: str
    description: str
    content: str
    icon: str
    created_at: str
    updated_at: str
    is_admin_skill: bool = False
    is_subscribed: bool = False
    share_type: str | None = None
    clone_share_id: str | None = None
    live_share_id: str | None = None
    collaborate_share_id: str | None = None
    summary_only: bool = True


class SkillCatalogPage(BaseModel):
    items: list[SkillCatalogItem]
    count: int
    limit: int
    offset: int
    has_more: bool
    next_cursor: str | None = None
