"""Canvas tool inputs: distinct create, bounded view, and revision-aware edits."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)

from app.tools.errors import SafeToolExecutionError


class CanvasValidationError(SafeToolExecutionError):
    """Expected Canvas rejection with a safe model-facing diagnostic."""


CANVAS_FILE_REQUIRED = (
    "Text edits require file_id for the existing canvas file. Viewing also requires file_id. "
    "For creation, provide content, omit file_id, and set edits to null."
)
CANVAS_REVISION_REQUIRED = (
    "expected_revision is required for an existing Canvas and must be a non-negative integer. "
    "View the file first and copy canvas_revision from its latest view or save receipt."
)
CANVAS_CONTENT_REQUIRED = (
    "Creating or replacing a Canvas requires string content. For creation, set edits to null. "
    "For partial edits, provide file_id, expected_revision, and a non-empty edits array instead."
)
CANVAS_INVALID_ARGUMENTS = (
    "Invalid Canvas arguments. Use the create, view, or edit argument structure. "
    "For edits, provide either full content, content with both snippets, or an edits array with content=null; "
    "do not combine these modes. Use only the fields for that operation and retry once."
)

CanvasType = Literal["markdown", "mermaid", "csv", "html", "latex"]
FileId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Snippet = Annotated[str, Field(min_length=1)]
Revision = Annotated[int, Field(ge=0)]


class CanvasInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CanvasWrite(CanvasInput):
    type: CanvasType | None = Field(
        default=None,
        description="Canvas source format. Omit on edits to preserve the existing format.",
    )
    filename: str | None = Field(
        default=None,
        description="Filename, such as notes.md or report.tex; its extension is added if missing.",
    )
    file_ids: list[FileId] | None = Field(
        default=None,
        max_length=20,
        description="Authorized asset file IDs. Omit or use null to preserve existing assets; [] clears them. Use omlorix-file://FILE_ID for presentation images.",
    )


class CanvasCreate(CanvasWrite):
    """Create a new file. Supply content; never invent an edit or existing file ID."""

    type: CanvasType = Field(
        default="markdown", description="New file format; defaults to markdown."
    )
    content: str = Field(
        description="Full source. HTML runs in an isolated Canvas preview; external requests require an explicit viewer grant. LaTeX must be a complete portable pdflatex document; do not add babel/polyglossia or locale-specific packages merely because of the conversation language. If retry_allowed=true, correct the payload once; otherwise stop tool use."
    )
    file_id: None = None
    edits: None = None


class CanvasView(CanvasInput):
    """Read the current source and revision without changing the file."""

    type: Literal["view"]
    file_id: FileId
    heading: str | None = Field(
        default=None, max_length=500, description="Return one Markdown heading section."
    )
    query: str | None = Field(
        default=None,
        max_length=200,
        description="Return bounded context around a matching string.",
    )
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_chars: int | None = Field(
        default=None,
        ge=1,
        le=100000,
        description="Maximum returned characters; defaults to 20000.",
    )


class CanvasEdit(CanvasWrite):
    file_id: FileId
    expected_revision: Revision = Field(
        description="Copy canvas_revision from the latest view or save receipt."
    )


class CanvasReplace(CanvasEdit):
    """Replace the entire existing file, using its current revision."""

    content: str
    edits: None = None


class CanvasSnippetEdit(CanvasReplace):
    """Replace one exact unique range, including both boundary snippets."""

    start_snippet: Snippet
    end_snippet: Snippet


class CanvasTextEdit(CanvasInput):
    start_snippet: Snippet
    end_snippet: Snippet
    content: str = Field(
        description="Replacement text; an empty string deletes the matched range."
    )


class CanvasBatchEdit(CanvasEdit):
    """Apply atomic, non-overlapping edits against one stored revision."""

    edits: list[CanvasTextEdit] = Field(min_length=1, max_length=50)
    content: None = None


CANVAS_INPUT = TypeAdapter(
    CanvasCreate | CanvasView | CanvasReplace | CanvasSnippetEdit | CanvasBatchEdit
)


def canvas_parameters_schema() -> dict:
    """Inline our acyclic Pydantic definitions for provider JSON-schema adapters."""
    schema = CANVAS_INPUT.json_schema()
    definitions = schema.pop("$defs")

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return inline(definitions[value["$ref"].rsplit("/", 1)[-1]])
        result = {
            key: inline(item)
            for key, item in value.items()
            if key not in {"default", "title"}
        }
        if "const" in result:
            result["enum"] = [result.pop("const")]
        return result

    return {"type": "object", **inline(schema)}


def parse_canvas_tool_arguments(arguments: dict) -> dict:
    """Validate before file access; retain legacy aliases without guessing mutations."""
    args = dict(arguments)
    if isinstance(args.get("type"), str):
        args["type"] = args["type"].strip().lower()
    if "content" not in args and "markdown" in args:
        args["content"] = args.pop("markdown")
    if args.get("type") == "view" and args.get("id"):
        if args.get("file_id") and args["file_id"] != args["id"]:
            raise CanvasValidationError(
                code="canvas_invalid_arguments", safe_message=CANVAS_INVALID_ARGUMENTS
            )
        args["file_id"] = args.pop("id")
    elif not args.get("id"):
        args.pop("id", None)
    for field in ("file_id", "start_snippet", "end_snippet"):
        if args.get(field) is None or (
            isinstance(args.get(field), str) and not args[field].strip()
        ):
            args.pop(field, None)

    is_view = args.get("type") == "view"
    has_edits = any(
        args.get(field) is not None
        for field in ("edits", "start_snippet", "end_snippet")
    )
    if not args.get("file_id") and (is_view or has_edits):
        raise CanvasValidationError(
            code="canvas_file_required", safe_message=CANVAS_FILE_REQUIRED
        )
    if args.get("file_id") and not is_view:
        revision = args.get("expected_revision")
        if type(revision) is not int or revision < 0:
            raise CanvasValidationError(
                code="canvas_revision_required", safe_message=CANVAS_REVISION_REQUIRED
            )
    if not is_view and args.get("content") is None and args.get("edits") is None:
        raise CanvasValidationError(
            code="canvas_content_required", safe_message=CANVAS_CONTENT_REQUIRED
        )
    try:
        parsed = CANVAS_INPUT.validate_python(args)
    except ValidationError:
        # Never forward Pydantic's input values (document contents or file IDs).
        raise CanvasValidationError(
            code="canvas_invalid_arguments", safe_message=CANVAS_INVALID_ARGUMENTS
        ) from None
    if (
        isinstance(parsed, CanvasView)
        and parsed.start_line is not None
        and parsed.end_line is not None
        and parsed.end_line < parsed.start_line
    ):
        raise CanvasValidationError(
            code="canvas_invalid_arguments", safe_message=CANVAS_INVALID_ARGUMENTS
        )
    return parsed.model_dump(exclude_none=True)
