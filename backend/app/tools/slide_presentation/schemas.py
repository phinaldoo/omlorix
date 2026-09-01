"""Request and response schemas for the interactive presentation editor."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.tools.canvas_markdown.utils import MAX_FILE_SIZE


class SlidePresentationEditorResponse(BaseModel):
    """Editable source and revision metadata for one owned presentation."""

    presentation_id: str
    file_id: str
    title: str
    html: str
    slide_count: int = Field(ge=1)
    canvas_revision: int = Field(ge=0)
    render_revision: int = Field(ge=0)
    render_status: str


class SlidePresentationEditorSaveRequest(BaseModel):
    """Complete sanitized deck snapshot submitted by the browser editor."""

    html: str = Field(min_length=1, max_length=MAX_FILE_SIZE)
    title: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=0)


class SlidePresentationEditorSaveResponse(BaseModel):
    """Revision returned after persisting an editor snapshot."""

    presentation_id: str
    file_id: str
    title: str
    slide_count: int = Field(ge=1)
    canvas_revision: int = Field(ge=1)
    render_revision: int = Field(ge=0)
    render_status: str


class SlidePresentationEditorRenderRequest(BaseModel):
    """Revision the browser expects to turn into refreshed artifacts."""

    expected_revision: int = Field(ge=1)


class SlidePresentationEditorRenderResponse(BaseModel):
    """Stable artifact identity after rendering the saved source revision."""

    presentation_id: str
    file_id: str
    title: str
    slide_count: int = Field(ge=1)
    canvas_revision: int = Field(ge=1)
    render_revision: int = Field(ge=1)
    render_status: str
