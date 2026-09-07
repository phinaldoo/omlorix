"""Exercise the shared tool loop and presentation publication as one workflow."""

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.files.models import Files, get_file
from app.llm.models import Models
from app.llm.generation.engine import GenerationEngine, ToolCall
from app.tools.slide_presentation.models import SlidePresentations
from app.tools.slide_presentation import pipeline as p, specialist as s
from app.tools.subagents import runtime
from app.tools.subagents.session import SubagentSession, current_session, scoped_stream
from app.tools.utils import resolve_enabled_tools
from app.tools.errors import SafeToolExecutionError, SubagentToolExecutionError


HTML = """<!DOCTYPE html><html><head><style>
.slide {width:1920px;height:1080px;position:relative;overflow:hidden;box-sizing:border-box}
</style></head><body><section class="slide" data-slide-index="1" data-slide-title="One"><h1>One</h1></section></body></html>"""


def drain(stream):
    events = []
    while True:
        try:
            events.append(next(stream))
        except StopIteration as done:
            return events, done.value


def test_specialist_streams_subagent_activity_until_final_deck(workspace, monkeypatch):
    public_events = [
        {"t": "r", "d": "Planning the visual story"},
        {"t": "c", "d": "Creating the first draft"},
        {"t": "t_c", "d": {"id": "create", "name": "update_presentation"}},
        {"t": "t_cd", "d": {"id": "create", "name": "update_presentation", "delta": json.dumps({"type": "html", "content": HTML})}},
    ]

    def provider(request):
        for event in public_events:
            yield json.dumps(event)
        session = current_session(request.generation_id)
        yield from session.update({'type': 'html', 'content': HTML})
        yield json.dumps({'t': 'c', 'd': 'Reviewed every slide.'})
        yield json.dumps({'t': 'd', 'd': 'f'})

    monkeypatch.setattr(runtime, 'call_provider_chat', provider)
    events, result = drain(p.run_presentation_pipeline(user_id='u', markdown_file_id='brief', db=workspace.db))
    events = [json.loads(event) for event in events]
    activity = [event['data'] for event in events if event['event'] == 'activity']
    assert [event['event'] for event in activity] == [
        'reasoning_delta', 'message_delta', 'tool_call', 'tool_delta', 'message_delta'
    ]
    assert activity[2]['raw']['payload'] == public_events[2]
    assert activity[3]['raw'] == public_events[3]
    first_activity = next(i for i, event in enumerate(events) if event['event'] == 'activity')
    rendering = next(i for i, event in enumerate(events) if event['event'] == 'status' and event['data']['phase'] == 'rendering')
    assert first_activity < rendering
    assert {event['run_id'] for event in activity} == {result['run_id']}
    assert events[0]['data']['run_id'] == result['run_id']
    assert any(event['event'] == 'revision_ready' for event in events)
    assert not any(event['event'] in {'html_snapshot', 'slide_images'} for event in events)
    assert events[-1]['event'] == 'complete'
    assert result['review_status'] == 'completed'
    snapshot = result['slide_presentation_activity']
    assert snapshot['schema_version'] == 1
    assert snapshot['run_id'] == result['run_id']
    assert [event['event'] for event in snapshot['events']] == [event['event'] for event in activity] + ['complete']
    assert snapshot['events'][0] == {'event': 'reasoning_delta', 'content': public_events[0]['d']}
    assert snapshot['events'][3]['raw'] == public_events[3]
    assert snapshot['events'][-1]['result'] == 'Reviewed every slide.'
    assert events[-1]['data']['slide_presentation_activity'] == snapshot


def test_specialist_activity_compacts_chunks_without_merging_tool_calls():
    events = []
    for name, content in [('message_delta', 'First '), ('message_delta', 'takeaway'), ('reasoning_delta', 'Review')]:
        s._append_specialist_activity(events, name, content, {'private_provider_metadata': 'not for storage'})
    for tool_id, chunk in [('a', '{'), ('a', '}'), ('b', 'next')]:
        s._append_specialist_activity(events, 'tool_delta', None, {
            't': 't_cd', 'd': {'id': tool_id, 'name': 'update_presentation', 'delta': chunk, 'private_provider_metadata': 'hidden'},
        })
    assert [event['event'] for event in events] == ['message_delta', 'reasoning_delta', 'tool_delta', 'tool_delta']
    assert events[0]['content'] == 'First takeaway'
    assert events[2]['raw']['d']['delta'] == '{}'
    assert events[3]['raw']['d']['id'] == 'b'
    assert 'private_provider_metadata' not in json.dumps(events)


def test_review_images_preserve_each_slide_and_overviews_after_staging_cleanup(tmp_path):
    staging = tmp_path / "staging"
    review = tmp_path / "review"
    staging.mkdir()
    review.mkdir()
    originals = []
    for number in range(1, 6):
        path = staging / f"slide_{number}.png"
        with Image.new("RGB", (1920, 1080), (number * 40, 20, 30)) as slide:
            slide.save(path)
        originals.append(path.read_bytes())
    session = s.PresentationSession(
        "review", ("update_presentation",), user_id="u", review_dir=review
    )
    images = session.review_images(staging, 5)
    s.shutil.rmtree(staging)
    assert len(images) == 7  # Five full-resolution slides and two overview sheets.
    for number, file_id in enumerate(images[:5], 1):
        info = session.file_info("u", file_id)
        assert info["file_name"] == f"slide-{number}.png"
        assert info["file_type"] == "image/png"
        assert Path(info["path"]).read_bytes() == originals[number - 1]
        with Image.open(info["path"]) as slide:
            assert slide.size == (1920, 1080)
        assert session.file_info("other-user", file_id) is None
    assert [session.attachments[file_id]["file_name"] for file_id in images[5:]] == [
        "slides-1-4.jpg", "slides-5-5.jpg"
    ]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[Files.__table__, Models.__table__, SlidePresentations.__table__]
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)

    def file(file_id, content, mime="text/html"):
        path = tmp_path / file_id
        path.write_bytes(content)
        record = Files(
            id=file_id,
            user_id="u",
            file_name=file_id + (".md" if mime == "text/markdown" else ".html"),
            file_category="image" if mime.startswith("image/") else "document",
            file_type=mime,
            file_size=len(content),
            storage_key=str(path),
            created_at=now,
            last_updated_at=now,
            meta={"canvas_revision": 1},
        )
        db.add(record)
        db.flush()
        return record

    file("brief", b"# Plan\nThe brief must remain in context.", "text/markdown")
    model = Models(
        id="model",
        name="Model",
        description="Presentation model",
        model_icon="bot",
        provider_id="",
        status="normal",
        provider="openai",
        model_name="test",
        tools=[],
        settings={},
        capabilities=["completion", "tools", "vision"],
        access={},
        meta={},
        is_active=True,
    )
    db.add(model)
    db.commit()
    monkeypatch.setattr(s, "MATERIALIZED_TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        s, "materialize_file_record", lambda record, user: Path(record.storage_key)
    )
    monkeypatch.setattr(
        "app.tools.slide_presentation.sanitizer.materialize_file_record",
        lambda record, user: Path(record.storage_key),
    )
    monkeypatch.setattr(
        s, "get_settings_page_data", lambda *args: {"presentation_model_id": "model"}
    )
    monkeypatch.setattr(s, "stage_tool_audit_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(p, "delete_storage_reference", lambda **kwargs: None)
    monkeypatch.setattr(p, "delete_slide_presentation_artifacts", lambda **kwargs: None)
    saved_sources = []
    renders = []
    fail = {"render": False, "publish": False}

    def render(**kwargs):
        if fail["render"]:
            raise RuntimeError("private renderer failure")
        renders.append(kwargs["html"])
        directory = kwargs["presentation_dir"] / "images"
        directory.mkdir()
        Image.new("RGB", (1920, 1080), "blue").save(directory / "slide_1.png")
        record = file(
            f"pptx-{len(renders)}",
            b"pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        db.commit()
        return {"file_id": record.id, "slide_count": 1}

    def save(**kwargs):
        record = (
            get_file(db, kwargs["file_id"], "u")
            if kwargs.get("file_id")
            else file("deck", b"source")
        )
        revision = (record.meta["canvas_revision"] + 1) if kwargs.get("file_id") else 1
        record.meta = {**record.meta, "canvas_revision": revision}
        result = {"file_id": record.id, "canvas_revision": revision}
        try:
            kwargs["before_commit"](result)
            db.commit()
            saved_sources.append(kwargs["content"])
        except BaseException:
            db.rollback()
            raise
        return result

    def upload(**kwargs):
        if fail["publish"]:
            raise RuntimeError("private storage failure")
        return {
            "provider": "local",
            "storage_prefix": f"u/presentations/deck/{kwargs['revision']}",
        }

    monkeypatch.setattr(p, "render_slide_presentation", render)
    monkeypatch.setattr(s, "save_canvas_markdown", save)
    monkeypatch.setattr(p, "upload_presentation_artifacts", upload)
    yield SimpleNamespace(
        db=db,
        file=file,
        model=model,
        saved=saved_sources,
        renders=renders,
        fail=fail,
        root=tmp_path,
    )
    db.close()
    engine.dispose()


@pytest.mark.parametrize("use_code", [False, True])
def test_specialist_uses_one_conversation_optional_code_images_and_targeted_edits(
    workspace, monkeypatch, use_code
):
    w = workspace
    requests, receipts = [], []

    def execute(db, tool_name, tool_arguments):
        assert tool_name == "code_execution"
        buffer = BytesIO()
        Image.new("RGB", (20, 20), "red").save(buffer, "PNG")
        output = w.file("chart", buffer.getvalue(), "image/png")
        db.commit()
        if False:
            yield
        return {"content": "Calculated 2 + 2 = 4", "images": [output.id]}

    def provider(request):
        requests.append(request)
        assert "brief must remain" in request.chat_history[0]["content"][0]["content"]
        session = current_session(request.generation_id)
        from app.files import worker

        monkeypatch.setattr(worker, "TEMP_DIR", w.root)
        monkeypatch.setattr(worker, "MATERIALIZED_TEMP_DIR", w.root)
        worker.cleanup_temp_files()
        assert session.review_dir.is_dir()
        assert set(resolve_enabled_tools(session.tools)["tool_list"]) == {
            "update_presentation",
            "code_execution",
        }
        engine = GenerationEngine(db=w.db, generation_id=request.generation_id)

        def turn():
            if use_code:
                code = yield ToolCall(execute, (w.db, "code_execution", {}), {})
                assert "chart" in code["content"]
                assert "chart" in session.generated_ids
            created = yield ToolCall(
                execute,
                (
                    w.db,
                    "update_presentation",
                    {
                        "type": "html",
                        "content": HTML.replace(
                            "</section>", '<img src="omlorix-file://chart"></section>'
                        )
                        if use_code
                        else HTML,
                        "file_ids": ["chart"] if use_code else [],
                    },
                ),
                {},
            )
            receipts.append(created)
            from app.files.utils import get_file_info

            image_info = get_file_info("u", created["images"][0])
            assert Path(image_info["path"]).is_file()
            assert session.file_info("other-user", created["images"][0]) is None
            # Actual provider attachment encoding must include image bytes.
            from app.llm.anthropic.attachments import upload_files

            parts = upload_files(w.db, created["images"], "u", ["image"])["parts"]
            assert any(part.get("type") == "image" for part in parts)
            edited = yield ToolCall(
                execute,
                (
                    w.db,
                    "update_presentation",
                    {
                        "file_id": "deck",
                        "expected_revision": 1,
                        "edits": [
                            {
                                "start_snippet": "<h1>One</h1>",
                                "end_snippet": "<h1>One</h1>",
                                "content": "<h1>Improved</h1>",
                            }
                        ],
                    },
                ),
                {},
            )
            receipts.append(edited)
            request_images = {"messages": [{"role": "user", "content": parts}]}
            session.prepare_request(request_images)
            assert any(
                part.get("type") == "image"
                for part in request_images["messages"][0]["content"]
            )
            yield json.dumps({"t": "d", "d": "f"})

        yield from engine.run(turn())

    monkeypatch.setattr(runtime, "call_provider_chat", provider)
    events, result = drain(
        p.run_presentation_pipeline(user_id="u", markdown_file_id="brief", db=w.db)
    )
    assert len(requests) == 1
    assert requests[0].model.tools == ["update_presentation", "code_execution"]
    assert w.model.tools == []
    assert len(w.renders) == 2 and len(w.saved) == 2
    assert "Improved" in w.saved[-1]
    if use_code:
        assert 'src="data:image/png;base64,' in w.saved[-1]
        assert "omlorix-file://chart" not in w.saved[-1]
        assert result["asset_file_ids"] == ["chart"]
        assert get_file(w.db, "deck", "u").meta[
            "slide_presentation_asset_file_ids"
        ] == ["chart"]
    assert result["revision"] == 2 and result["file_id"] == "pptx-2"
    assert result["budget"] == {
        "calls_remaining": 9 if use_code else 10,
        "renders_remaining": 2,
    }
    assert json.loads(receipts[0]["content"])["calls_remaining"] == (
        10 if use_code else 11
    )
    assert get_file(w.db, "pptx-1", "u") is None
    assert w.db.query(SlidePresentations).one().file_id == "pptx-2"
    assert json.loads(events[-1])["event"] == "complete"
    assert current_session() is None
    assert not list(w.root.glob("presentation-review-*"))


@pytest.mark.parametrize("failure", ["render", "publish"])
def test_failed_correction_keeps_canonical_source_and_derivatives(workspace, failure):
    w = workspace
    session = s.PresentationSession(
        "run", ("update_presentation",), user_id="u", db=w.db, review_dir=w.root
    )
    drain(session.update({"type": "html", "content": HTML}))
    w.fail[failure] = True
    with pytest.raises(RuntimeError):
        drain(
            session.update(
                {
                    "file_id": "deck",
                    "expected_revision": 1,
                    "content": HTML.replace("One", "Changed"),
                }
            )
        )
    assert get_file(w.db, "deck", "u").meta["canvas_revision"] == 1
    assert w.db.query(SlidePresentations).one().file_id == "pptx-1"
    assert session.revision == 1
    assert "Changed" not in session.source


def test_scope_budget_failed_calls_final_turn_and_isolation():
    session = SubagentSession("nested", ("calculate",), max_calls=1)

    def fail(tool_name, tool_arguments):
        yield "working"
        raise SafeToolExecutionError(
            code="invalid_input",
            safe_message="Correct the input.",
            detail="secret diagnostic",
        )

    def body():
        assert current_session("nested") is session
        assert current_session("parent") is None
        result = yield from session.execute(ToolCall(fail, ("calculate", {}), {}))
        assert json.loads(result["content"])["calls_remaining"] == 0
        assert "secret diagnostic" not in result["content"]
        request = {
            "tools": ["calculate"],
            "tool_choice": "required",
            "messages": ["last tool result"],
        }
        session.prepare_request(request)
        assert request == {
            "tools": ["calculate"],
            "tool_choice": "none",
            "messages": ["last tool result"],
        }
        with pytest.raises(RuntimeError, match="budget exhausted"):
            session.prepare_request({})

    wrapped = scoped_stream(body(), session)
    assert next(wrapped) == "working"
    assert current_session() is None
    drain(wrapped)


def test_workspace_rejects_other_files_unapproved_assets_and_exhausted_renders(
    workspace,
):
    session = s.PresentationSession(
        "run",
        ("update_presentation",),
        user_id="u",
        db=workspace.db,
        review_dir=workspace.root,
    )
    for arguments in (
        {"file_id": "other-deck", "expected_revision": 1, "content": HTML},
        {"type": "html", "content": HTML, "file_ids": ["unapproved"]},
    ):
        _, result = drain(session.update(arguments))
        assert json.loads(result["content"])["status"] == "invalid"
    session.render_calls = s.MAX_PRESENTATION_RENDERS
    _, result = drain(session.update({"type": "html", "content": HTML}))
    assert "budget exhausted" in result["content"]
    assert not workspace.renders


@pytest.mark.parametrize("has_deck", [False, True])
def test_infrastructure_failure_stops_run_and_is_recorded_as_failed(
    workspace, monkeypatch, has_deck
):
    from app.llmstats.models import ToolCallStatistic, create_tool_call_statistic
    from app.llm.generation.engine import ProviderCall

    w = workspace
    ToolCallStatistic.__table__.create(w.db.get_bind())
    requests, receipts = [], []

    def execute(tool_name, tool_arguments):
        raise AssertionError("Scoped handler must be used")

    def provider(request):
        engine = GenerationEngine(db=w.db, generation_id=request.generation_id)

        def turn():
            if has_deck:
                yield ToolCall(
                    execute,
                    ("update_presentation", {"type": "html", "content": HTML}),
                    {},
                )
            w.fail["render"] = True
            args = {"content": HTML, "type": "html"}
            if has_deck:
                args.update(file_id="deck", expected_revision=1)
            receipt = yield ToolCall(execute, ("update_presentation", args), {})
            receipts.append(receipt)
            create_tool_call_statistic(
                w.db, "update_presentation", success=True, meta=receipt["tool_meta"]
            )
            with pytest.raises(SubagentToolExecutionError):
                yield ProviderCall(
                    lambda **kwargs: requests.append(kwargs), {}, {}, "openai"
                )
            with pytest.raises(SubagentToolExecutionError):
                yield ToolCall(execute, ("update_presentation", args), {})
            # Even an adapter that finishes normally must not claim a full review.
            yield json.dumps({"t": "d", "d": "f"})

        yield from engine.run(turn())

    monkeypatch.setattr(runtime, "call_provider_chat", provider)
    stream = p.run_presentation_pipeline(user_id="u", markdown_file_id="brief", db=w.db)
    if has_deck:
        _, result = drain(stream)
        assert result["revision"] == 1
        assert result["review_status"] == "incomplete"
    else:
        with pytest.raises(SubagentToolExecutionError) as error:
            drain(stream)
        assert not error.value.allow_same_response_retry
    assert len(receipts) == 1 and not requests
    assert "private renderer" not in receipts[0]["content"]
    record = w.db.query(ToolCallStatistic).one()
    assert record.success is False
    assert record.meta["error_code"] == "internal"
    assert record.meta["retry_allowed"] is False
    assert not list(w.root.glob("presentation-review-*"))


def test_disabled_optional_code_tool_does_not_block_presentation_tool():
    session = SubagentSession("run", ("code_execution", "update_presentation"))
    called = []

    def execute(tool_name, tool_arguments):
        called.append(tool_name)
        if tool_name == "code_execution":
            raise SafeToolExecutionError(
                code="capacity",
                safe_message="Unavailable.",
                allow_same_response_retry=False,
            )
        if False:
            yield
        return {"content": "ready"}

    for name in ("code_execution", "code_execution", "update_presentation"):
        _, receipt = drain(session.execute(ToolCall(execute, (name, {}), {})))
        assert "calls_remaining" in receipt["content"]
    assert called == ["code_execution", "update_presentation"]
    assert not session.fatal_error


def test_terminal_failure_survives_parent_tool_and_durable_worker(monkeypatch):
    from app.tools import helper
    from app.tools.errors import ToolErrorTracker
    from app.workers import tool_jobs
    from app.workers.runtime import FatalJobError

    def failed_pipeline(**kwargs):
        yield p._sse("status", {"phase": "rendering"})
        raise SubagentToolExecutionError()

    monkeypatch.setattr(p, "run_presentation_pipeline", failed_pipeline)
    # Exercise the real outer slide tool: its error must remain non-retryable.
    with pytest.raises(SubagentToolExecutionError) as failure:
        drain(
            helper.resolve_tool_call(
                None,
                "slide_presentation",
                {"file_id": "brief"},
                "u",
                "g",
                None,
                _skip_rate_limit=True,
                _execution_queue="rendering",
            )
        )
    assert (
        ToolErrorTracker().record("slide_presentation", failure.value).stop_tool_calls
    )

    row = SimpleNamespace(id="job", status=tool_jobs.JOB_FAILED, error_code=None)

    class Session:
        def query(self, *args):
            return self

        def filter(self, *args):
            return self

        def first(self):
            return row

        def close(self):
            pass

    monkeypatch.setattr(tool_jobs, "SessionLocal", Session)
    monkeypatch.setattr(
        tool_jobs,
        "_active_user",
        lambda *args: SimpleNamespace(id="u", group_id="g", role="user"),
    )
    monkeypatch.setattr(
        tool_jobs, "_validate_current_tool_policy", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(tool_jobs, "redis_enabled", lambda: False)
    job = SimpleNamespace(
        user_id="u",
        queue="rendering",
        payload={
            "tool_name": "slide_presentation",
            "tool_arguments": {"file_id": "brief"},
        },
    )
    context = SimpleNamespace(raise_if_cancelled=lambda: None)
    with pytest.raises(FatalJobError) as terminal:
        tool_jobs.execute_tool_job(job, context)
    row.error_code = terminal.value.code
    with pytest.raises(SubagentToolExecutionError) as transported:
        tool_jobs._wait_for_tool_job(row, generation_id=None, timeout_seconds=1)
    assert (
        ToolErrorTracker()
        .record("slide_presentation", transported.value)
        .stop_tool_calls
    )


def test_cancellation_during_render_does_not_publish(workspace, monkeypatch):
    from app.chats.streaming import cancel_registry

    w = workspace
    original = p.render_slide_presentation

    def cancel_after_render(**kwargs):
        result = original(**kwargs)
        cancel_registry.cancel("parent")
        return result

    monkeypatch.setattr(p, "render_slide_presentation", cancel_after_render)
    session = s.PresentationSession(
        "child",
        ("update_presentation",),
        user_id="u",
        db=w.db,
        review_dir=w.root,
        parent_generation_id="parent",
    )
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            drain(session.update({"type": "html", "content": HTML}))
        assert not w.saved
        assert not session.result
        assert get_file(w.db, "pptx-1", "u") is None
    finally:
        cancel_registry.clear("parent")
        cancel_registry.clear("child")


def test_native_image_pruning_preserves_tool_pairs_reasoning_and_current_assets():
    from pydantic import BaseModel
    from app.tools.subagents.session import _prune_obsolete_images

    class Part(BaseModel):
        text: str | None = None
        inline_data: dict | None = None
        thought_signature: bytes | None = None

    native = [
        Part(text="Metadata old-review"),
        Part(inline_data={"mime_type": "image/jpeg", "data": b"old"}),
        Part(text="Metadata new-review"),
        Part(inline_data={"mime_type": "image/jpeg", "data": b"new"}),
        Part(thought_signature=b"signed-reasoning"),
    ]
    pruned = _prune_obsolete_images(native, {"old-review"})
    assert len(pruned) == 4
    assert pruned[2].inline_data["data"] == b"new"
    assert pruned[-1].thought_signature == b"signed-reasoning"
    messages = [
        {"type": "function_call", "call_id": "call", "arguments": "unchanged"},
        {
            "type": "function_call_output",
            "call_id": "call",
            "output": "old-review result",
        },
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Metadata old-review"},
                {"type": "input_image", "image_url": "old"},
                {"type": "text", "text": "Metadata original-asset"},
                {"type": "image_url", "image_url": {"url": "original"}},
            ],
        },
    ]
    pruned = _prune_obsolete_images(messages, {"old-review"})
    assert pruned[:2] == messages[:2]
    assert len(pruned[2]["content"]) == 3
    assert pruned[2]["content"][-1] == messages[2]["content"][-1]
    assert len(messages[2]["content"]) == 4
    session = SubagentSession("run", (), max_calls=0)
    config = {"config": {"tools": ["tool"], "temperature": 0.5}}
    session.prepare_request(config, protocol="google_aistudio")
    assert config["config"] == {
        "tools": ["tool"],
        "tool_config": {"function_calling_config": {"mode": "NONE"}},
        "temperature": 0.5,
    }
    metadata = "\n".join(
        "Metadata of the file: "
        + json.dumps(
            {
                "file_id": file_id,
                "file_category": "image",
                "native_context_included": True,
            }
        )
        for file_id in ("old-review", "original-asset")
    )
    message = {"content": metadata, "images": ["old-bytes", "asset-bytes"]}
    assert _prune_obsolete_images(message, {"old-review"})["images"] == ["asset-bytes"]
    session = SubagentSession("router", (), max_calls=0)
    request = {
        "json": {"tools": ["tool"], "messages": ["last result"]},
        "headers": {"keep": "unchanged"},
    }
    session.prepare_request(request, protocol="openrouter")
    assert request == {
        "json": {"tools": ["tool"], "tool_choice": "none", "messages": ["last result"]},
        "headers": {"keep": "unchanged"},
    }


def test_specialist_context_never_evicts_brief_after_visual_feedback():
    from app.llm.generation.context import ContextBuilder, ContextBudgetExceeded

    request = {
        "messages": [
            {"role": "user", "content": "Original complete brief " * 100},
            {"role": "assistant", "content": "Created deck"},
            {"role": "user", "content": "New slide images"},
        ]
    }
    with pytest.raises(ContextBudgetExceeded):
        ContextBuilder(preserve_history=True).prepare(
            request,
            settings={"input_token_limit": 1000},
            protocol="openai_chat_completions",
        )
    assert "Original complete brief" in request["messages"][0]["content"]


@pytest.mark.parametrize("has_deck", [False, True])
@pytest.mark.parametrize("ending", ["error", "truncated"])
def test_incomplete_provider_returns_only_a_successfully_rendered_deck(
    workspace, monkeypatch, has_deck, ending
):
    w = workspace

    def provider(request):
        session = current_session(request.generation_id)
        if has_deck:
            yield from session.update({"type": "html", "content": HTML})
        if ending == "error":
            yield json.dumps({"t": "e", "d": "private provider diagnostic"})

    monkeypatch.setattr(runtime, "call_provider_chat", provider)
    stream = p.run_presentation_pipeline(user_id="u", markdown_file_id="brief", db=w.db)
    if has_deck:
        events, result = drain(stream)
        assert result["review_status"] == "incomplete"
        assert result["html_file_id"] == "deck"
        assert any(json.loads(event)["event"] == "warning" for event in events)
    else:
        with pytest.raises(RuntimeError):
            drain(stream)
        assert not w.saved
    assert current_session() is None
    assert not list(w.root.glob("presentation-review-*"))
