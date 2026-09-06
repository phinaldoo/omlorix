"""Presentation workspace used by the shared subagent conversation runtime."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from PIL import Image, ImageDraw

from app.files.models import Files, get_file
from app.files.utils import MATERIALIZED_TEMP_DIR, materialize_file_record
from app.llm.models import Models
from app.settings.models import get_settings_page_data
from app.tools.audit import stage_tool_audit_action
from app.tools.canvas_markdown.utils import save_canvas_markdown
from app.tools.canvas_markdown.schemas import (
    canvas_parameters_schema,
    parse_canvas_tool_arguments,
)
from app.tools.slide_presentation.sanitizer import prepare_slide_presentation_html
from app.tools.slide_presentation.system_instructions import (
    get_sys_instruct_generate_html,
)
from app.tools.subagents.session import SubagentSession
from app.tools.text_edits import apply_atomic_text_edits, select_text_content
from app.tools.slide_presentation import pipeline as p


MAX_PRESENTATION_TOOL_CALLS = 12
MAX_PRESENTATION_RENDERS = 4


@dataclass
class PresentationSession(SubagentSession):
    db: object = None
    project_id: str | None = None
    brief_file_id: str = ""
    title: str = "Presentation"
    asset_ids: list = field(default_factory=list)
    generated_ids: set = field(default_factory=set)
    presentation_id: str | None = None
    revision: int = 0
    render_calls: int = 0
    source: str = ""
    embedded_assets: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    review_dir: Path | None = None

    def __post_init__(self):
        self.schemas = {
            "update_presentation": {
                "name": "update_presentation",
                "type": "function",
                "description": (
                    "Create, view or edit ONLY this presentation's HTML using Canvas arguments. "
                    "Every write validates and renders the complete candidate, then returns slide images for visual review. "
                    "Use type=html for creation, type=view for bounded source reads, and expected_revision for edits. "
                    "Batch related exact snippet edits into one call. Only successful renders become canonical. "
                    "file_ids may contain approved inputs or images returned by your code_execution calls. "
                    "Preserve __OMLORIX_EMBEDDED_IMAGE_N__ placeholders when editing."
                ),
                "parameters": canvas_parameters_schema(),
            }
        }
        self.handlers = {"update_presentation": self.update}

    def budget(self):
        return {
            **super().budget(),
            "renders_remaining": max(0, MAX_PRESENTATION_RENDERS - self.render_calls),
        }

    def receipt(self, name, payload):
        if name == "code_execution":
            assets = []
            for category in ("images", "documents", "videos", "audios"):
                for file_id in payload.get(category) or []:
                    record = get_file(self.db, str(file_id), self.user_id)
                    if record is not None:
                        self.generated_ids.add(str(file_id))
                        assets.append(
                            {
                                "file_id": str(file_id),
                                "name": record.file_name,
                                "type": record.file_type,
                            }
                        )
            payload = dict(payload)
            payload["content"] = json.dumps(
                {"execution": payload.get("content", ""), "files": assets}
            )
        return super().receipt(name, payload)

    def update(self, arguments):
        try:
            args = parse_canvas_tool_arguments(arguments)
            target = args.get("file_id")
            if target != self.presentation_id:
                raise ValueError(
                    "Use only the file_id returned by this presentation workspace."
                )
            if args.get("type") == "view":
                text, selection = select_text_content(
                    self.source,
                    **{
                        key: args[key]
                        for key in (
                            "query",
                            "heading",
                            "start_line",
                            "end_line",
                            "max_chars",
                        )
                        if key in args
                    },
                )
                return {
                    "content": json.dumps(
                        {
                            "file_id": target,
                            "canvas_revision": self.revision,
                            "source": text,
                            **selection,
                        }
                    )
                }
            if args.get("type") not in (None, "html"):
                raise ValueError("Presentation source must use type=html.")
            if self.presentation_id and args.get("expected_revision") != self.revision:
                raise ValueError(
                    "Revision conflict. View this presentation before editing."
                )
            if self.render_calls >= MAX_PRESENTATION_RENDERS:
                raise ValueError(
                    "Render budget exhausted. Finish with the last successful presentation."
                )
            self.render_calls += 1
            requested_assets = args.get("file_ids") or []
            if any(
                str(item) not in set(self.asset_ids) | self.generated_ids
                for item in requested_assets
            ):
                raise ValueError(
                    "Only approved input images and files generated during this run may be embedded."
                )
            asset_ids = p.validate_slide_presentation_asset_file_ids(
                self.db,
                self.user_id,
                list(dict.fromkeys([*self.asset_ids, *requested_assets])),
            )
            edits = args.get("edits")
            if args.get("start_snippet"):
                edits = [
                    {
                        key: args[key]
                        for key in ("start_snippet", "end_snippet", "content")
                    }
                ]
            candidate = (
                apply_atomic_text_edits(
                    self.source, edits, artifact_label="presentation"
                )
                if edits
                else args.get("content", "")
            )
            candidate = prepare_slide_presentation_html(
                p._restore_review_assets(candidate, self.embedded_assets),
                db=self.db,
                user_id=self.user_id,
                allowed_file_ids=asset_ids,
            )
            count = p.validate_slide_presentation_html(candidate)
        except ValueError as exc:
            return {
                "content": json.dumps(
                    {
                        "status": "invalid",
                        "message": str(exc),
                        "file_id": self.presentation_id,
                        "canvas_revision": self.revision,
                    }
                )
            }

        yield p._sse("status", {"phase": "rendering", **self.budget()})
        return (yield from self.render_candidate(candidate, count, asset_ids))

    def render_candidate(self, html, count, asset_ids):
        staging = Path(
            tempfile.mkdtemp(prefix="presentation-candidate-", dir=self.review_dir)
        )
        pptx_id = None
        manifest = None
        published = False
        previous = dict(self.result)
        old_record = (
            p.get_slide_presentation(self.db, self.presentation_id, self.user_id)
            if self.presentation_id
            else None
        )
        old_storage = (
            (
                old_record.storage_provider,
                old_record.storage_prefix,
                old_record.slide_count,
            )
            if old_record
            else None
        )
        try:
            self.check_cancellation()
            rendered = p.render_slide_presentation(
                html=html,
                user_id=self.user_id,
                filename=f"{self.title}.pptx",
                presentation_dir=staging,
                input_file_ids=None,
                existing_file_id=None,
                artifact_presentation_id=self.presentation_id,
                db=self.db,
            )
            pptx_id = str(rendered["file_id"])
            self.check_cancellation()
            if int(rendered.get("slide_count") or 0) != count:
                raise ValueError("Renderer slide count does not match the HTML.")
            images = self.review_images(staging / "images", count)

            def publish(saved):
                nonlocal manifest
                self.check_cancellation()
                presentation_id = str(saved["file_id"])
                revision = int(saved["canvas_revision"])
                metadata = {
                    "title": self.title,
                    "slide_count": count,
                    "html_file_id": presentation_id,
                    "brief_file_id": self.brief_file_id,
                    "asset_file_ids": asset_ids,
                    "render_revision": revision,
                }
                (staging / "presentation.html").write_text(html, encoding="utf-8")
                (staging / "title.txt").write_text(self.title, encoding="utf-8")
                (staging / "metadata.json").write_text(
                    json.dumps(metadata), encoding="utf-8"
                )
                source = (
                    self.db.query(Files)
                    .filter(Files.id == presentation_id, Files.user_id == self.user_id)
                    .first()
                )
                manifest = p.upload_presentation_artifacts(
                    presentation_dir=staging,
                    user_id=self.user_id,
                    presentation_id=presentation_id,
                    slide_count=count,
                    revision=revision,
                )
                p.upsert_slide_presentation(
                    self.db,
                    presentation_id=presentation_id,
                    user_id=self.user_id,
                    title=self.title,
                    slide_count=count,
                    storage_provider=manifest["provider"],
                    storage_prefix=manifest["storage_prefix"],
                    file_id=pptx_id,
                    storage_meta={**manifest, **metadata},
                    commit=False,
                )
                source.meta = {
                    **(source.meta or {}),
                    "slide_presentation_source": True,
                    "presentation_id": presentation_id,
                    "slide_presentation_brief_file_id": self.brief_file_id,
                    "slide_presentation_asset_file_ids": asset_ids,
                    "presentation_pptx_file_id": pptx_id,
                    "presentation_slide_count": count,
                    "presentation_render_revision": revision,
                    "presentation_render_status": "ready",
                }
                derivative = get_file(self.db, pptx_id, self.user_id)
                derivative.meta = {
                    **(derivative.meta or {}),
                    "presentation_id": presentation_id,
                }
                stage_tool_audit_action(
                    self.db,
                    self.user_id,
                    "SLIDE_PRESENTATION_SOURCE_SAVED",
                    category="files",
                    details={
                        "presentation_id": saved["file_id"],
                        "canvas_revision": saved["canvas_revision"],
                        "slide_count": count,
                    },
                )
                stage_tool_audit_action(
                    self.db,
                    self.user_id,
                    "SLIDE_PRESENTATION_RENDERED",
                    category="files",
                    details={
                        "presentation_id": presentation_id,
                        "canvas_revision": revision,
                        "slide_count": count,
                        "asset_count": len(asset_ids),
                    },
                )

            saved = save_canvas_markdown(
                db=self.db,
                user_id=self.user_id,
                content=html,
                content_type="html",
                filename=f"{self.title}.html",
                project_id=self.project_id,
                file_id=self.presentation_id,
                expected_revision=self.revision if self.presentation_id else None,
                edit_source="slide_specialist",
                edited_by=self.user_id,
                before_commit=publish,
            )
            presentation_id = str(saved["file_id"])
            revision = int(saved["canvas_revision"])
            published = True
            self.latest_attachment_ids = set(images)
            self.presentation_id, self.revision, self.asset_ids = (
                presentation_id,
                revision,
                asset_ids,
            )
            self.source, self.embedded_assets = p._mask_review_assets(html)
            self.result = {
                "presentation_id": presentation_id,
                "html_file_id": presentation_id,
                "file_id": pptx_id,
                "pptx_file_id": pptx_id,
                "title": self.title,
                "slide_count": count,
                "asset_file_ids": asset_ids,
                "revision": revision,
                "operation": "created",
            }
            if previous.get("file_id"):
                p._delete_unpublished_render_file(
                    self.db, self.user_id, previous["file_id"]
                )
            if old_storage:
                p.delete_slide_presentation_artifacts(
                    storage_provider=old_storage[0],
                    storage_prefix=old_storage[1],
                    slide_count=old_storage[2],
                )
            if not previous:
                for start in range(0, len(self.source), 8192):
                    yield p._sse(
                        "html_delta", {"delta": self.source[start : start + 8192]}
                    )
            yield p._sse(
                "slide_images",
                {
                    "presentation_id": presentation_id,
                    "count": count,
                    "revision": revision,
                },
            )
            yield p._sse("status", {"phase": "refining", **self.budget()})
            return {
                "content": json.dumps(
                    {
                        "file_id": presentation_id,
                        "canvas_revision": revision,
                        "slide_count": count,
                        "render_status": "ready",
                        "review": "Review all attached slides against the brief. Batch corrections with exact edits; use type=view to inspect canonical source. Finish if satisfactory.",
                    }
                ),
                "images": images,
            }
        except Exception:
            if not published:
                try:
                    self.db.rollback()
                    stage_tool_audit_action(
                        self.db,
                        self.user_id,
                        "SLIDE_PRESENTATION_RENDER_FAILED",
                        category="files",
                        details={
                            "presentation_id": self.presentation_id,
                            "canvas_revision": self.revision,
                            "render_attempt": self.render_calls,
                        },
                    )
                    self.db.commit()
                except Exception:
                    p.logger.warning(
                        "Could not audit failed presentation candidate", exc_info=True
                    )
            raise
        finally:
            if not published:
                self.db.rollback()
                if manifest:
                    p.delete_slide_presentation_artifacts(
                        storage_provider=manifest["provider"],
                        storage_prefix=manifest["storage_prefix"],
                        slide_count=count,
                    )
                p._delete_unpublished_render_file(self.db, self.user_id, pptx_id)
            shutil.rmtree(staging, ignore_errors=True)

    def review_images(self, directory, count):
        """Four readable slides per sheet; scoped attachments never become user files."""
        ids = []
        for start in range(1, count + 1, 4):
            sheet = Image.new("RGB", (1920, 1120), "white")
            draw = ImageDraw.Draw(sheet)
            for offset, number in enumerate(range(start, min(start + 4, count + 1))):
                x, y = (offset % 2) * 960, (offset // 2) * 560
                with Image.open(directory / f"slide_{number}.png") as image:
                    image = image.convert("RGB")
                    image.thumbnail((940, 529))
                    sheet.paste(image, (x + 10, y + 25))
                draw.text((x + 10, y + 5), f"Slide {number}", fill="black")
            file_id = f"subagent-review-{uuid.uuid4().hex}"
            path = self.review_dir / f"{file_id}.jpg"
            sheet.save(path, "JPEG", quality=85)
            sheet.close()
            self.attachments[file_id] = {
                "file_id": file_id,
                "path": str(path),
                "owner_user_id": self.user_id,
                "requester_user_id": self.user_id,
                "file_name": f"slides-{start}-{min(start + 3, count)}.jpg",
                "file_type": "image/jpeg",
                "file_category": "image",
                "file_size": path.stat().st_size,
                "meta": {},
            }
            ids.append(file_id)
        return ids


def run_specialist(
    *,
    user_id,
    markdown_file_id,
    db,
    chat_id=None,
    project_id=None,
    user_role=None,
    input_file_ids=None,
    parent_generation_id=None,
):
    from app.tools.subagents.runtime import (
        _clone_model_with_subagent_tools,
        _dispatch_nested_provider,
    )
    from app.chats.streaming import cancel_registry

    brief_record = get_file(db, str(markdown_file_id), str(user_id))
    if not brief_record or (
        "markdown" not in str(brief_record.file_type or "").lower()
        and not str(brief_record.file_name or "").lower().endswith((".md", ".markdown"))
    ):
        raise ValueError(
            "The slide_presentation tool requires an owned Markdown brief."
        )
    brief = materialize_file_record(brief_record, str(user_id)).read_text(
        encoding="utf-8"
    )
    if not brief.strip():
        raise ValueError("The presentation brief is empty.")
    asset_ids = p.validate_slide_presentation_asset_file_ids(
        db, str(user_id), [*(input_file_ids or []), *p._asset_ids_from_markdown(brief)]
    )
    model_id = str(
        (get_settings_page_data(db, "slide_presentation") or {}).get(
            "presentation_model_id"
        )
        or ""
    ).strip()
    model = (
        db.query(Models)
        .filter(Models.id == model_id, Models.is_active.is_(True))
        .first()
        if model_id
        else None
    )
    if model is None or (model.meta or {}).get("user_managed"):
        raise ValueError(
            "Configure an available administrator-managed presentation model."
        )
    tools = ("update_presentation", "code_execution")
    runtime_model = _clone_model_with_subagent_tools(model, list(tools))
    overrides = {
        "enabled_tools": list(tools),
        "_runtime_enabled_tools": list(tools),
        "parallel_tool_calls": False,
        "input_formats": ["image", "documents"],
        "use_group_context": False,
        "use_project_context": False,
    }
    runtime_model.settings.update(overrides)
    generation_id = f"slide-specialist:{uuid.uuid4()}"
    MATERIALIZED_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="presentation-review-", dir=MATERIALIZED_TEMP_DIR
    ) as directory:
        session = PresentationSession(
            generation_id=generation_id,
            tools=tools,
            user_id=str(user_id),
            parent_generation_id=parent_generation_id,
            max_calls=MAX_PRESENTATION_TOOL_CALLS,
            db=db,
            project_id=project_id,
            brief_file_id=str(markdown_file_id),
            title=p._title_from_brief(brief),
            asset_ids=asset_ids,
            review_dir=Path(directory),
        )
        instructions = (
            get_sys_instruct_generate_html()
            + "\n\n"
            + SPECIALIST_INSTRUCTIONS
        )
        yield p._sse(
            "status",
            {"phase": "generating", "title": session.title, **session.budget()},
        )
        stream = _dispatch_nested_provider(
            db=db,
            provider=model.provider,
            chat_id=chat_id,
            chat_history=[
                {
                    "id": "presentation-brief",
                    "role": "user",
                    "content": [
                        {"type": "content", "content": brief, "images": asset_ids}
                    ],
                }
            ],
            db_model=runtime_model,
            user_id=str(user_id),
            project_id=project_id,
            generation_id=generation_id,
            settings_override=overrides,
            system_instruction_sections=[
                {"title": "Presentation specialist", "content": instructions}
            ],
            user_role=user_role,
            session=session,
        )
        completed = False
        assessment = ""
        try:
            for line in stream:
                if parent_generation_id and cancel_registry.is_cancelled(
                    parent_generation_id
                ):
                    cancel_registry.cancel(generation_id)
                    return None
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if (
                    isinstance(event, dict)
                    and event.get("t") == "slide_presentation_evt"
                ):
                    yield line
                elif isinstance(event, dict) and event.get("t") == "e":
                    raise RuntimeError("Presentation specialist generation failed.")
                elif isinstance(event, dict) and event.get("t") == "d":
                    completed = event.get("d") == "f"
                elif isinstance(event, dict) and event.get("t") == "t_c":
                    assessment = ""
                elif isinstance(event, dict) and event.get("t") == "c":
                    assessment = (assessment + str(event.get("d") or ""))[:4000]
            session.check_cancellation()
            if not completed:
                raise RuntimeError(
                    "Presentation specialist stream ended before completion."
                )
        except Exception:
            completed = False
            if parent_generation_id and cancel_registry.is_cancelled(
                parent_generation_id
            ):
                return None
            if not session.result:
                raise
            p.logger.warning(
                "Presentation specialist stopped; retaining its last rendered revision",
                exc_info=True,
            )
            yield p._sse(
                "warning", {"code": "visual_review_failed", "recoverable": True}
            )
        finally:
            stream.close()
            cancel_registry.clear(generation_id)
        if not session.result:
            raise RuntimeError(
                "The presentation specialist did not produce a rendered deck."
            )
        result = {
            **session.result,
            "budget": session.budget(),
            "review_status": "completed" if completed else "incomplete",
        }
        if assessment.strip():
            result["assessment"] = assessment.strip()
        yield p._sse("complete", result)
        return result


SPECIALIST_INSTRUCTIONS = """
You are the presentation specialist subagent. Keep the original brief authoritative throughout this conversation.
Use update_presentation to create and improve the deck; do not output HTML as your final answer.
You have 12 total tool calls, including reads and failed attempts, and at most 4 write/render attempts.
Each tool result reports calls_remaining and renders_remaining. Reserve enough calls to produce a valid deck.
Code Execution is ALWAYS available as an optional tool: use it only when helpful for calculations, checking data,
or generating chart/image assets. You are not required to call it. Save reusable assets under /tmp/output/.
Reference its returned exact image file IDs with <img src="omlorix-file://FILE_ID"> and include them in file_ids.
Use PNG, JPEG, GIF or WebP assets. Do not embed execution-container paths or invent file IDs.
After each successful update, inspect every rendered slide against the brief for clipping, overlap, readability,
spacing and factual consistency. Apply small batched edits using the returned file_id and canvas_revision.
View the current source when exact snippets are unclear. Keep embedded-image placeholders intact.
Never regenerate the full HTML just to fix a small detail. Every update returns a fresh render session;
interactive answers are not saved. Finish with a concise assessment when the deck is ready or budgets run out.
If a tool fails, use its feedback and remaining budget; never claim an unsuccessful update was published.
"""
