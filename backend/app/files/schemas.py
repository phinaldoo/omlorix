from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime
from enum import Enum
from typing import Any, Literal


SVG_MIME_TYPE = "image/svg+xml"
HTML_MIME_TYPE = "text/html"
HTML_ATTACHMENT_MIME_TYPES = (
    HTML_MIME_TYPE,
    "application/html",
    "application/xhtml+xml",
    "application/x-html",
    "text/xhtml",
)

# These document formats are deliberately supplied to models as source text
# instead of being treated as provider-native files. SVG needs this because
# vision APIs generally reject vector markup; HTML needs it so untrusted active
# content is never handed to a browser-capable provider path by Omlorix.
TEXT_EXTRACTED_DOCUMENT_MIME_TYPES = (
    SVG_MIME_TYPE,
    *HTML_ATTACHMENT_MIME_TYPES,
)



# -------------------
# File Metadata
# -------------------
class FileList(BaseModel):
    file_id: str = Field(alias="id")
    user_id: str | None = None
    file_name: str | None = None
    file_category: str
    file_type: str
    file_size: int
    project_id: str | None = None
    folder_id: str | None = None
    created_at: datetime
    meta: dict | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("meta", mode="before")
    @classmethod
    def remove_internal_origin(cls, value):
        if not isinstance(value, dict):
            return value
        sanitized = dict(value)
        sanitized.pop("origin", None)
        return sanitized


SHARED_FILE_INTERNAL_META_KEYS = frozenset({
    "shared_owner_id",
    "shared_contributor_id",
})


def minimize_shared_file_response(file_model: FileList) -> FileList:
    file_model.user_id = None
    if isinstance(file_model.meta, dict):
        file_model.meta = {
            key: value
            for key, value in file_model.meta.items()
            if key not in SHARED_FILE_INTERNAL_META_KEYS
        }
    return file_model


class FilesWorkspaceCounts(BaseModel):
    all: int = 0
    uncategorized: int = 0
    folders: dict[str, int] = Field(default_factory=dict)


class FilesWorkspaceResponse(BaseModel):
    items: list[FileList] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    has_more: bool = False
    counts: FilesWorkspaceCounts = Field(default_factory=FilesWorkspaceCounts)


class PdfPreviewPageMetadata(BaseModel):
    """Natural PDF page dimensions used by the lazy frontend renderer."""

    page: int
    width: float
    height: float


class PdfPreviewDocumentResponse(BaseModel):
    """Metadata for an authenticated selectable PDF preview."""

    page_count: int
    pages: list[PdfPreviewPageMetadata] = Field(default_factory=list)


class PdfPreviewWord(BaseModel):
    """One positioned word in a PDF page text layer."""

    text: str
    x: float
    y: float
    width: float
    height: float
    block: int
    line: int
    word: int


class PdfPreviewPageResponse(PdfPreviewPageMetadata):
    """Selectable text geometry for one authenticated PDF page."""

    words: list[PdfPreviewWord] = Field(default_factory=list)


class FileStorageUsageResponse(BaseModel):
    user_id: str
    file_count: int = 0
    storage_bytes: int = 0
    latest_file_at: datetime | None = None
    uploads_allowed: bool = True
    file_count_limit: int | None = None
    storage_bytes_limit: int | None = None
    file_count_percent: float | None = None
    storage_percent: float | None = None


class GoogleDrivePickerSessionResponse(BaseModel):
    """Ephemeral browser credentials required to render Google Picker."""

    picker_ready: bool = False
    connected: bool = False
    reauthorization_required: bool = False
    error_code: str = ""
    developer_key: str | None = None
    app_id: str | None = None
    access_token: str | None = None
    expires_at: int | None = None


class GoogleDriveImportRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list, min_length=1, max_length=20)


class GoogleDriveImportError(BaseModel):
    file_id: str
    name: str | None = None
    message: str


class GoogleDriveImportResponse(BaseModel):
    imported: list[FileList] = Field(default_factory=list)
    errors: list[GoogleDriveImportError] = Field(default_factory=list)
    imported_count: int = 0


# -------------------
# File Rename Request
# -------------------
class FileRenameRequest(BaseModel):
    file_id: str
    original_filename: str


class CanvasFileSaveRequest(BaseModel):
    file_id: str = Field(..., min_length=1)
    content: str = Field(default="", max_length=512 * 1024 * 1024)
    content_type: Literal["markdown", "mermaid", "csv", "html", "latex"] = "markdown"
    filename: str | None = None
    file_ids: list[str] | None = Field(default=None, max_length=20)


class CanvasFileSaveResponse(BaseModel):
    file_id: str
    file_name: str
    content: str
    content_type: Literal["markdown", "mermaid", "csv", "html", "latex"]
    page_count: int = 1
    created: bool = False
    canvas_revision: int | None = None
    pdf_file_id: str = ""
    pdf_file_name: str = ""
    asset_file_ids: list[str] = Field(default_factory=list, max_length=20)
    render_revision: int | None = None
    render_status: str = ""
    # The save boundary currently normalizes references to 20, but response
    # validation must never turn an already-committed save into a 500 if that
    # implementation limit changes independently.
    pending_asset_approval_count: int = Field(default=0, ge=0)


class CanvasAssetDecisionRequest(BaseModel):
    """An asset owner's decision for a Canvas-scoped reference."""

    canvas_file_id: str = Field(..., min_length=1, max_length=128)
    request_id: str = Field(..., min_length=1, max_length=128)
    notification_id: str = Field(..., min_length=1, max_length=128)
    decision: Literal["approve", "reject"]
    scope: Literal["canvas_members", "public"] = "canvas_members"


class CanvasAssetDecisionResponse(BaseModel):
    canvas_file_id: str
    asset_file_id: str
    status: Literal["active", "rejected"]
    scope: Literal["canvas_members", "public"]


class CanvasSpreadsheetSaveResponse(BaseModel):
    """Metadata returned after replacing an editable spreadsheet file."""

    file_id: str
    file_name: str
    content_type: Literal["csv", "spreadsheet"]
    spreadsheet_format: Literal["csv", "tsv", "xlsx", "xls"]
    file_size: int = Field(ge=1)
    canvas_revision: int | None = None
    spreadsheet_requires_recalculation: bool = False


class CanvasMarkdownPdfRequest(BaseModel):
    source_file_id: str | None = None
    markdown: str = Field(default="", max_length=2 * 1024 * 1024)
    filename: str | None = None


class PublicLatexServiceConnection(BaseModel):
    """Non-secret service connection details safe to expose only in debug/admin responses."""
    id: str = ""
    name: str = ""
    base_url: str = ""
    legacy: bool = False


class LatexPdfRenderResponse(BaseModel):
    file_id: str
    source_file_id: str
    file_name: str
    source_file_name: str | None = None
    title: str
    mime_type: str = "application/pdf"
    size: int
    compiler: str
    execution_time: float | None = None
    log_excerpt: str = Field(default="", max_length=4000)
    input_files_loaded: int = 0
    input_file_names: list[str] = Field(default_factory=list, max_length=20)
    asset_file_ids: list[str] = Field(default_factory=list, max_length=20)
    service_connection: PublicLatexServiceConnection | None = None
    source_revision: int | None = None
    render_revision: int | None = None
    render_status: str = "ready"


class CanvasLatexRenderRequest(BaseModel):
    """Request a derived PDF for one already-persisted LaTeX Canvas revision."""

    file_id: str = Field(..., min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)


class CanvasLatexRenderResponse(LatexPdfRenderResponse):
    """PDF derivative metadata returned to the unified Canvas preview."""



# -------------------
# File Delete Options
# -------------------
class FileDeleteTimeOption(str, Enum):
    OLDER_THAN_1_DAY = "older_than_1_day"
    OLDER_THAN_1_WEEK = "older_than_1_week"
    OLDER_THAN_1_MONTH = "older_than_1_month"
    OLDER_THAN_1_YEAR = "older_than_1_year"
    ALL = "all"


class ArtifactShareCreateRequest(BaseModel):
    file_id: str = Field(..., min_length=1)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    expires_in_hours: int = Field(default=24, ge=1, le=24 * 30)


class ArtifactShareLink(BaseModel):
    share_id: str
    share_url: str
    has_password: bool
    created_at: datetime
    expires_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = 0


class ArtifactShareCreateResponse(ArtifactShareLink):
    pass


class ArtifactShareStatusResponse(BaseModel):
    file_id: str
    links: list[ArtifactShareLink] = Field(default_factory=list)


class ArtifactShareDeleteRequest(BaseModel):
    share_id: str = Field(..., min_length=1)


class ArtifactShareDeleteResponse(BaseModel):
    ok: bool


class ArtifactSharePasswordChangeRequest(BaseModel):
    share_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8, max_length=256)


class ArtifactSharePasswordRemoveRequest(BaseModel):
    share_id: str = Field(..., min_length=1)


class ArtifactSharePasswordResponse(BaseModel):
    share_id: str
    has_password: bool


class ArtifactShareExpiryChangeRequest(BaseModel):
    share_id: str = Field(..., min_length=1)
    expires_at: datetime


class ArtifactShareExpiryRemoveRequest(BaseModel):
    share_id: str = Field(..., min_length=1)


class ArtifactShareExpiryResponse(BaseModel):
    share_id: str
    expires_at: datetime | None = None


class ArtifactShareAccessRequest(BaseModel):
    share_id: str = Field(..., min_length=1)
    password: str | None = None


class ArtifactShareAsset(BaseModel):
    """One owner-approved dependency embedded in a public Canvas response."""

    file_id: str
    name: str
    mime_type: str
    encoding: Literal["base64"] = "base64"
    content: str


class ArtifactShareAccessResponse(BaseModel):
    share_id: str
    file_name: str
    artifact_type: Literal["markdown", "html", "css", "mermaid", "pdf"]
    mime_type: str
    expires_at: datetime | None = None
    has_password: bool = False
    encoding: Literal["text", "base64"] = "text"
    content: str
    assets: list[ArtifactShareAsset] = Field(default_factory=list, max_length=20)



# -------------------
# Allowed File Types
# -------------------
allowed_audio_types = [
    "audio/aac",
    "audio/flac",
    "audio/midi",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/webm",
]



# -------------------
# Allowed Video Types
# -------------------
allowed_video_types = [
    "video/avi",
    "video/mpeg",
    "video/mp4",
    "video/ogg",
    "video/quicktime",
    "video/webm",
    "video/x-matroska",
]



# -------------------
# Allowed Image Types
# -------------------
allowed_image_types = [
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
]



# -------------------
# Allowed Document Types
# -------------------
allowed_document_types = [
    "application/msword",
    "application/pdf",
    "application/rtf",
    "application/vnd.apple.pages",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/json",
    "application/vnd.android.package-archive",
    "application/vnd.apple.installer+xml",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/zip",
    "text/css",
    "text/csv",
    "text/markdown",
    "text/plain",
    "text/xml",
    # SVG is XML source code. Chat generation extracts this source as text
    # instead of sending it through a provider's raster-image input API.
    SVG_MIME_TYPE,
    "application/vnd.apple.numbers",
    "application/vnd.ms-excel",
    "application/vnd.sun.xml.calc",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.spreadsheet-template",
    "application/x-vnd.oasis.opendocument.spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.apple.keynote",
    "application/vnd.ms-powerpoint",
    "application/vnd.sun.xml.impress",
    "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.presentation-template",
    "application/x-vnd.oasis.opendocument.presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.graphics",
    "application/vnd.oasis.opendocument.graphics-template",
    "application/vnd.oasis.opendocument.text-master",
    "application/vnd.oasis.opendocument.text-template",
    "application/vnd.oasis.opendocument.text-web",
    "application/vnd.oasis.opendocument.formula",
    "application/vnd.oasis.opendocument.chart",
    "application/vnd.oasis.opendocument.database",
    "application/vnd.oasis.opendocument.base",
    "application/x-vnd.oasis.opendocument.text",
    "application/x-vnd.oasis.opendocument.graphics",
    "application/x-vnd.oasis.opendocument.formula",
    "application/vnd.sun.xml.writer",
    "application/vnd.sun.xml.writer.template",
    "application/vnd.stardivision.writer",
    "application/vnd.stardivision.calc",
    "application/vnd.stardivision.impress",
    "application/epub+zip",
    "application/x-zip-compressed",
    "application/vnd.ms-outlook",
    "application/vnd.ms-outlook.msg",
    "message/rfc822",
    "text/x-python",
    "text/x-c",
    "text/x-c++",
    "text/x-java-source",
    "text/x-go",
    "text/x-ruby",
    "text/x-php",
    "text/x-shellscript",
    "text/x-sql",
    "text/x-yaml",
    "text/x-toml",
    "text/x-ini",
    "text/x-properties",
    "text/x-scss",
    "text/x-less",
    "text/x-latex",
    "text/x-tex",
    "text/x-rst",
    "text/x-perl",
    "text/x-haskell",
    "application/ld+json",
    "application/vnd.api+json",
    "application/x-ndjson",
    "application/graphql",
    "application/sql",
    "application/x-httpd-php",
    "application/x-sh",
    "application/x-yaml",
    "application/x-toml",
    "application/xml",
    "application/x-latex",
    "application/x-rst",
    "application/x-perl",
    "text/tab-separated-values",
    "text/json",
    "text/yaml",
    "text/vnd.yaml",
    "text/prs.fallenstein.rst",
    "text/x-coffeescript",
    "text/x-sass",
    "text/x-julia",
    "text/x-kotlin",
    "text/x-swift",
    "text/x-lua",
    "text/x-dart",
    "text/x-erlang",
    "text/x-clojure",
    "text/x-scala",
    "text/x-r",
    "text/x-matlab",
    "text/x-assembly",
    "text/x-fortran",
    "text/x-elm",
    "text/x-ocaml",
    "text/x-sml",
    "text/x-vue",
    "text/x-svelte",
    "text/x-csharp",
    "text/x-objective-c",
    "text/x-powershell",
    "text/x-bash",
    "text/x-zsh",
    "text/x-dockerfile",
    "text/x-config",
    "text/x-sqlite",
    "text/x-asm",
    "text/x-gherkin",
    "text/x-prolog",
    "text/x-fsharp",
    "text/x-verilog",
    "text/x-vhdl",
    "text/x-applescript",
    "text/x-flow",
    "text/x-graphql",
    "text/x-hcl",
    "text/x-scheme",
    "text/x-ada",
    "text/x-terraform",
    "text/x-angular",
    "text/x-react",
]


MODEL_DOCUMENT_INPUT_FORMATS = frozenset({"pdf", "document", "documents", "text_document"})


def normalize_model_input_formats(input_formats: Any) -> set[str]:
    """Normalize model input format settings to lowercase string values."""
    if isinstance(input_formats, dict):
        iterable = input_formats
    elif isinstance(input_formats, (list, tuple, set)):
        iterable = input_formats
    elif input_formats is None:
        iterable = []
    else:
        iterable = [input_formats]

    normalized: set[str] = set()
    for item in iterable:
        value = getattr(item, "value", item)
        if isinstance(value, str) and value.strip():
            normalized.add(value.strip().lower())
    return normalized


def model_input_formats_include_documents(input_formats: Any) -> bool:
    return bool(normalize_model_input_formats(input_formats) & MODEL_DOCUMENT_INPUT_FORMATS)


def allowed_mime_types_for_model_input_formats(input_formats: Any) -> set[str]:
    normalized = normalize_model_input_formats(input_formats)
    allowed_types: set[str] = set()
    if "image" in normalized:
        allowed_types.update(allowed_image_types)
    if "audio" in normalized:
        allowed_types.update(allowed_audio_types)
    if "video" in normalized:
        allowed_types.update(allowed_video_types)
    if normalized & MODEL_DOCUMENT_INPUT_FORMATS:
        allowed_types.update(allowed_document_types)
    # Source-text documents work with every text-capable chat model and do not
    # depend on a provider advertising native document input.
    allowed_types.update(TEXT_EXTRACTED_DOCUMENT_MIME_TYPES)
    return allowed_types


def supported_file_formats_for_model_input_formats(input_formats: Any) -> list[dict]:
    """Build the file-capability payload consumed by the chat frontend.

    Most entries mirror provider-native model input categories. Source-text
    documents are the exception: Omlorix extracts their markup before dispatch,
    so every text-capable chat model can consume them even when native document
    input is disabled.
    """
    normalized = normalize_model_input_formats(input_formats)
    categories: list[dict] = []

    # An empty capability list means "unspecified" to existing frontend code,
    # which intentionally avoids marking any historical file as unsupported.
    # Preserve that contract instead of advertising only source-text documents.
    if not normalized:
        return categories

    if "image" in normalized:
        categories.append({"category": "image", "file_formats": list(allowed_image_types)})
    if "audio" in normalized:
        categories.append({"category": "audio", "file_formats": list(allowed_audio_types)})
    if "video" in normalized:
        categories.append({"category": "video", "file_formats": list(allowed_video_types)})
    if model_input_formats_include_documents(input_formats):
        categories.append({
            "category": "document",
            "file_formats": list(dict.fromkeys(
                allowed_document_types + list(TEXT_EXTRACTED_DOCUMENT_MIME_TYPES)
            )),
        })
    else:
        categories.append({
            "category": "document",
            "file_formats": list(TEXT_EXTRACTED_DOCUMENT_MIME_TYPES),
        })

    return categories


def supported_file_format_groups_for_model_input_formats(input_formats: Any) -> list[str]:
    """Return compact attachment capability group names for one model.

    The model-settings endpoint used to repeat every supported MIME type for
    every selected model.  The MIME catalog is application-wide static data,
    so the per-model response only needs to identify the applicable groups.
    Clients can fetch the catalog once and expand these group names locally.

    An empty list deliberately retains the legacy "capabilities unspecified"
    contract used by the chat frontend.
    """
    normalized = normalize_model_input_formats(input_formats)
    if not normalized:
        return []

    groups: list[str] = []
    if "image" in normalized:
        groups.append("image")
    if "audio" in normalized:
        groups.append("audio")
    if "video" in normalized:
        groups.append("video")
    if model_input_formats_include_documents(input_formats):
        groups.append("document")
        # The native-document catalog intentionally excludes active markup;
        # append the text-extraction group so the compact representation stays
        # equivalent to the former combined MIME response.
        groups.append("text_extracted_document")
    else:
        # SVG and HTML are converted to inert text before model dispatch, so
        # text-capable models may still accept this deliberately small subset.
        groups.append("text_extracted_document")
    return groups


def supported_file_format_catalog() -> dict[str, list[str]]:
    """Return the version-independent MIME catalog shared by all models.

    A fresh mapping/list structure is returned so callers cannot mutate the
    module-level allowlists that backend upload validation relies on.
    """
    return {
        "image": list(allowed_image_types),
        "audio": list(allowed_audio_types),
        "video": list(allowed_video_types),
        "document": list(allowed_document_types),
        "text_extracted_document": list(TEXT_EXTRACTED_DOCUMENT_MIME_TYPES),
    }



# -------------------
# Extract Text MIME Types
# -------------------
EXTRACT_TEXT_MIME_TYPES = [
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "text/css",
    "text/javascript",
    "text/x-javascript",
    "text/ecmascript",
    "text/typescript",
    "text/x-typescript",
    "text/x-python",
    "text/x-c",
    "text/x-c++",
    "text/x-java-source",
    "text/x-go",
    "text/x-ruby",
    "text/x-php",
    "text/x-shellscript",
    "text/x-sql",
    "text/x-yaml",
    "text/x-toml",
    "text/x-ini",
    "text/x-properties",
    "text/x-scss",
    "text/x-less",
    "text/x-latex",
    "text/x-tex",
    "text/x-rst",
    "text/x-perl",
    "text/x-haskell",
    "application/json",
    "application/ld+json",
    "application/vnd.api+json",
    "application/x-ndjson",
    "application/graphql",
    "application/sql",
    "application/x-httpd-php",
    "application/x-sh",
    "application/x-yaml",
    "application/x-toml",
    "application/xml",
    "application/xhtml+xml",
    "application/x-latex",
    "application/x-rst",
    "application/x-perl",
    "text/tab-separated-values",
    "text/json",
    "text/xml",
    "text/yaml",
    "text/vnd.yaml",
    "text/prs.fallenstein.rst",
    "text/x-coffeescript",
    "text/x-sass",
    "text/x-julia",
    "text/x-kotlin",
    "text/x-swift",
    "text/x-lua",
    "text/x-dart",
    "text/x-erlang",
    "text/x-clojure",
    "text/x-scala",
    "text/x-r",
    "text/x-matlab",
    "text/x-assembly",
    "text/x-fortran",
    "text/x-elm",
    "text/x-ocaml",
    "text/x-sml",
    "text/x-vue",
    "text/x-svelte",
    SVG_MIME_TYPE,
    "application/xml",
    "text/x-csharp",
    "text/x-objective-c",
    "text/x-powershell",
    "text/x-bash",
    "text/x-zsh",
    "text/x-dockerfile",
    "text/x-config",
    "text/x-sqlite",
    "text/x-asm",
    "text/x-gherkin",
    "text/x-prolog",
    "text/x-fsharp",
    "text/x-verilog",
    "text/x-vhdl",
    "text/x-applescript",
    "text/x-flow",
    "text/x-graphql",
    "text/x-hcl",
    "text/x-scheme",
    "text/x-ada",
    "text/x-terraform",
    "text/x-angular",
    "text/x-react",
]



# -------------------
# Markitdown MIME Types
# -------------------
MARKITDOWN_MIME_TYPES = [
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.text-template",
    "application/vnd.oasis.opendocument.text-master",
    "application/vnd.oasis.opendocument.text-web",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.spreadsheet-template",
    "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.presentation-template",
    "application/vnd.oasis.opendocument.graphics",
    "application/vnd.oasis.opendocument.graphics-template",
    "application/vnd.oasis.opendocument.formula",
    "application/vnd.oasis.opendocument.chart",
    "application/vnd.oasis.opendocument.database",
    "application/x-vnd.oasis.opendocument.text",
    "application/x-vnd.oasis.opendocument.spreadsheet",
    "application/x-vnd.oasis.opendocument.presentation",
    "application/x-vnd.oasis.opendocument.graphics",
    "application/x-vnd.oasis.opendocument.formula",
    "application/vnd.sun.xml.writer",
    "application/vnd.sun.xml.writer.template",
    "application/vnd.sun.xml.calc",
    "application/vnd.sun.xml.impress",
    "application/vnd.stardivision.writer",
    "application/vnd.stardivision.calc",
    "application/vnd.stardivision.impress",
    "application/epub+zip",
    "application/zip",
    "application/x-zip-compressed",
    "application/vnd.ms-outlook",
    "application/vnd.ms-outlook.msg",
    "message/rfc822",
]


SUPPORTED_EXTRACT_TEXT_MIME_TYPES = set(
    EXTRACT_TEXT_MIME_TYPES
    + MARKITDOWN_MIME_TYPES
    + list(HTML_ATTACHMENT_MIME_TYPES)
)
