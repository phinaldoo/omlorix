import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.llm.openai import utils as openai_utils
from app.tools import helper as tool_helper
from app.tools.deep_research import (
    models,
    native,
    orchestrator,
    providers,
    storage,
    utils,
    web_images,
)
from app.tools.deep_research.artifacts import remove_remote_image_embeds
from app.tools.deep_research.editing import (
    AnchoredEditError,
    apply_article_revision,
    article_revision_repair_context,
    validate_article_revision,
)
from app.tools.deep_research.models import RUN_STATUS_COMPLETED
from app.tools.deep_research.providers import (
    DeepResearchEmptyResponse,
    DeepResearchIncompleteStream,
    DeepResearchStructuredOutputError,
    PhaseResult,
    parse_structured_output,
    public_error_code,
)
from app.tools.deep_research.router import deep_research_router
from app.tools.deep_research.schemas import (
    ArticleRevision,
    QualityReview,
    ResearchBrief,
    ReviewIssue,
)
from app.tools.deep_research.web_images import _detect_image_type
from app.tools.schemas import tool_schemas


def _brief_payload() -> dict:
    """Return the smallest valid v2 planning contract."""

    return {
        "title": "Evidence report",
        "objective": "Answer the question.",
        "output_language": "en",
        "research_questions": [
            {
                "question": "What happened?",
                "why_it_matters": "It answers the request.",
            }
        ],
        "final_research_instruction": "Research and cite the answer.",
    }


def _review_payload(*, ready: bool) -> dict:
    """Return the smallest valid independent review contract."""

    return {
        "overall_assessment": "Ready." if ready else "Revise.",
        "ready_to_publish": ready,
        "coverage": [
            {
                "research_question": "What happened?",
                "status": "answered",
                "evidence_summary": "The cited evidence answers the question.",
            }
        ],
    }


def test_failed_research_result_keeps_widget_and_activity_for_persistence(monkeypatch):
    """A failed run is data to persist, not a tool-adapter exception."""

    activity = {
        "schema_version": 1,
        "events": [
            {
                "event": "reasoning_delta",
                "sequence": 1,
                "phase": "planning",
                "request_id": "request-1",
                "delta": "Checking sources",
            },
            {
                "event": "error",
                "sequence": 2,
                "phase": "failed",
                "status": "failed",
            },
        ],
    }
    failed_widget = {
        "type": "deep_research",
        "html": (
            '<section class="deep-research-widget" data-run-id="run-failed" '
            'data-status="failed"></section>'
        ),
        "model_context": {
            "schema_version": 2,
            "run_id": "run-failed",
            "status": "failed",
            "phase": "failed",
        },
    }

    def failed_research(**_kwargs):
        yield (
            json.dumps(
                {
                    "t": "wg",
                    "c": failed_widget["html"],
                    "widget_type": "deep_research",
                }
            )
            + "\n"
        )
        return {
            "content": "Deep Research failed.",
            "result": {
                "event": "failed",
                "run_id": "run-failed",
                "status": "failed",
                "phase": "failed",
                "error_code": "structured_output_invalid",
            },
            "widget": failed_widget,
            "tool_meta": {
                "deep_research": True,
                "run_id": "run-failed",
                "status": "failed",
                "deep_research_activity": activity,
            },
        }

    monkeypatch.setattr(tool_helper, "deep_research", failed_research)
    monkeypatch.setattr(
        tool_helper,
        "_admit_tool_invocation_or_payload",
        lambda *_args, **_kwargs: None,
    )
    runner = tool_helper.resolve_tool_call(
        db=object(),
        tool_name="deep_research",
        tool_arguments={"query": "Research this"},
        user_id="user-1",
        group_id=None,
        project_id=None,
        generation_id="generation-1",
        chat_id="chat-1",
    )
    assert json.loads(next(runner))["t"] == "wg"
    with pytest.raises(StopIteration) as finished:
        next(runner)

    payload = finished.value.value
    assert payload["result"]["status"] == "failed"
    assert payload["widget"] == failed_widget
    assert payload["tool_meta"]["deep_research_activity"] == activity


def test_failed_inline_research_returns_terminal_widget_and_replay_snapshot(
    monkeypatch,
):
    """The real tool generator must return its failed card after streaming activity."""

    run = SimpleNamespace(
        id="run-failed",
        user_id="user-1",
        query="Research this",
        model_id="model-1",
        model_name="Research Model",
        generation_id="generation-1",
        execution_mode="custom",
        output_format="markdown",
        revision_round=0,
        status="running",
        phase="starting",
        final_report_path=None,
        final_html_path=None,
        manifest_path=None,
        error_code=None,
        error_message_key=None,
        result_meta={},
        usage=[],
    )
    monkeypatch.setattr(utils, "create_research_run", lambda *_args, **_kwargs: run)

    def fail_after_activity(_db, _run, *, project_id, user_role, callback):
        assert project_id is None
        assert user_role is None
        callback(
            {
                "run_id": run.id,
                "sequence": 1,
                "event_type": "llm_request_started",
                "phase": "planning",
                "payload": {"request_id": "request-1"},
            }
        )
        callback(
            {
                "run_id": run.id,
                "sequence": 2,
                "event_type": "reasoning_delta",
                "phase": "planning",
                "payload": {
                    "request_id": "request-1",
                    "delta": "Checking sources",
                },
            }
        )
        raise DeepResearchStructuredOutputError("QualityReview")

    def mark_failed(_db, failed_run, exc):
        failed_run.status = "failed"
        failed_run.phase = "failed"
        failed_run.error_code = public_error_code(exc)
        failed_run.error_message_key = "deep_research_failed"

    monkeypatch.setattr(utils, "run_custom_research", fail_after_activity)
    monkeypatch.setattr(utils, "_mark_inline_failure", mark_failed)
    monkeypatch.setattr(utils, "_audit_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        utils, "_publish_research_event", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        utils,
        "_terminal_run_payload",
        lambda _run: {
            "event": "failed",
            "run_id": run.id,
            "widget_id": run.id,
            "generation_id": run.generation_id,
            "query": run.query,
            "status": "failed",
            "phase": "failed",
            "message_key": "deep_research_failed",
            "progress": 100,
            "report": "",
            "citations": [],
            "files": [],
            "final_report_path": None,
            "final_html_path": None,
            "archive_path": None,
            "error_code": "structured_output_invalid",
        },
    )

    class DB:
        def refresh(self, _run):
            return None

    runner = utils.deep_research(
        db=DB(),
        user_id="user-1",
        query=run.query,
        generation_id=run.generation_id,
    )
    assert json.loads(next(runner))["t"] == "wg"
    assert json.loads(next(runner))["event"] == "failed"
    with pytest.raises(StopIteration) as finished:
        next(runner)

    payload = finished.value.value
    assert payload["result"]["status"] == "failed"
    widget_data = json.loads(payload["widget"]["html"])
    assert widget_data["status"] == "failed"
    assert widget_data["error_code"] == "structured_output_invalid"
    assert payload["widget"]["render_mode"] == "frontend"
    assert payload["widget"]["model_context"]["status"] == "failed"
    assert (
        payload["widget"]["model_context"]["error_code"] == "structured_output_invalid"
    )
    assert payload["tool_meta"]["error_code"] == "structured_output_invalid"
    activity_events = payload["tool_meta"]["deep_research_activity"]["events"]
    assert [event["event"] for event in activity_events] == [
        "llm_request_started",
        "reasoning_delta",
        "failed",
    ]
    assert activity_events[1]["delta"] == "Checking sources"
    assert activity_events[-1]["status"] == "failed"
    assert activity_events[-1]["error_code"] == "structured_output_invalid"


def test_create_run_rolls_back_failed_database_transaction():
    """A failed insert must not poison the shared tool request session."""

    class DB:
        rolled_back = False

        def add(self, _value):
            return None

        def commit(self):
            raise RuntimeError("missing database column")

        def refresh(self, _value):
            pytest.fail("refresh must not run after a failed commit")

        def rollback(self):
            self.rolled_back = True

    db = DB()
    with pytest.raises(RuntimeError, match="missing database column"):
        models.create_deep_research_run(
            db,
            user_id="user-1",
            query="Research this.",
            chat_id=None,
            generation_id="generation-1",
            execution_mode="custom",
            output_format="markdown",
            provider_id="provider-1",
            model_id="model-1",
            model_name="Model",
            max_revision_rounds=2,
            config_snapshot={},
        )

    assert db.rolled_back is True


def test_usage_accounting_never_fails_a_run_for_token_volume():
    """Token totals remain observable even when an old snapshot has a budget."""

    class DB:
        def add(self, _value):
            return None

        def commit(self):
            return None

        def refresh(self, _value):
            return None

    run = SimpleNamespace(
        usage={},
        config_snapshot={"budgets": {"max_reported_tokens": 1}},
        updated_at=None,
    )
    phase_result = PhaseResult(
        text="# Complete report",
        usage=[{"total_tokens": 2_000_000}],
        duration_seconds=1.0,
    )

    orchestrator._record_usage(
        DB(),
        run,
        phase="deep-research",
        phase_result=phase_result,
    )

    assert run.usage["deep-research"]["reported_tokens"] == 2_000_000


def test_deep_research_router_only_serves_run_files_and_report_exports():
    """Chat streaming owns run lifecycle while the router serves final artifacts."""

    assert {route.path for route in deep_research_router.routes} == {
        "/api/v1/deep-research/runs/{run_id}/files/{relative_path:path}",
        "/api/v1/deep-research/runs/{run_id}/export",
    }


def test_tool_schema_returns_markdown_and_delegates_html_to_canvas():
    """Deep Research has one output contract and leaves page design to Canvas."""

    schema = tool_schemas["deep_research"]

    assert "output_format" not in schema["parameters"]["properties"]
    assert "Canvas tool after Deep Research completes" in schema["description"]


def test_widget_exposes_only_pipeline_phases_known_before_execution():
    """Initial previews show deterministic work and discover revisions live."""

    custom_run = SimpleNamespace(
        execution_mode="custom",
    )
    native_run = SimpleNamespace(
        execution_mode="native",
    )

    assert utils._known_pipeline_phases(custom_run) == [
        "planning",
        "deep-research",
        "evidence-audit",
    ]
    assert utils._known_pipeline_phases(native_run) == ["native-research"]


def test_widget_persists_safe_activity_steps_for_reload():
    """Completed phase summaries survive without storing provider stream text."""

    run = SimpleNamespace(
        execution_mode="custom",
        status="completed",
        phase="completed",
        revision_round=1,
        usage={
            "planning": {"duration_seconds": 1.23456, "provider_events": ["secret"]},
            "deep-research": {"duration_seconds": 12},
            "final-revision-1": {"duration_seconds": 3.5},
        },
        result_meta={
            "checkpoints": {
                "planning": {"files": ["research-brief.json"]},
                "evidence-audit": {"files": ["evidence-audit-review.json"]},
                "release-gate-1": {"files": ["release-gate-1-review.json"]},
            }
        },
    )

    assert utils._activity_steps_for_widget(run) == [
        {"phase": "planning", "status": "completed", "duration_seconds": 1.235},
        {"phase": "deep-research", "status": "completed", "duration_seconds": 12.0},
        {"phase": "evidence-audit", "status": "completed"},
        {"phase": "final-revision-1", "status": "completed", "duration_seconds": 3.5},
        {"phase": "release-gate-1", "status": "completed"},
    ]


def test_checkpoint_persists_verified_provider_manifest_before_database_commit(
    monkeypatch, tmp_path
):
    """Interrupted runs retain enough storage provenance for reads and migration."""
    events = []
    run = SimpleNamespace(id="run-1", user_id="user-1", result_meta={})

    class FakeDB:
        def add(self, _run):
            events.append("add")

        def commit(self):
            events.append("commit")

        def refresh(self, _run):
            events.append("refresh")

    def upload(**_kwargs):
        events.append("upload")
        return {
            "provider": "webdav",
            "storage_prefix": "user-1/deep_research/run-1",
            "uploaded_files": ["research-notes.md"],
            "objects": [
                {
                    "relative_path": "research-notes.md",
                    "size_bytes": 5,
                    "sha256": "a" * 64,
                }
            ],
        }

    monkeypatch.setattr(orchestrator, "upload_deep_research_artifacts", upload)

    orchestrator._checkpoint_phase(
        FakeDB(),
        run,
        tmp_path,
        phase="deep-research",
        relative_paths=["research-notes.md"],
    )

    assert events == ["upload", "add", "commit", "refresh"]
    assert run.result_meta["storage"]["provider"] == "webdav"
    assert run.result_meta["storage"]["uploaded_files"] == ["research-notes.md"]
    assert run.result_meta["checkpoints"]["deep-research"]["files"] == [
        "research-notes.md"
    ]


def test_widget_reconstructs_native_activity_without_custom_usage():
    """Native history still shows its research step after a page reload."""

    run = SimpleNamespace(
        execution_mode="native",
        status="completed",
        phase="completed",
        revision_round=0,
        usage={},
        result_meta={},
    )

    assert utils._activity_steps_for_widget(run) == [
        {"phase": "native-research", "status": "completed"}
    ]


def test_research_callback_publishes_directly_to_chat_stream(monkeypatch):
    """Inline phase events bypass a worker queue and durable event polling."""

    from app.chats import streaming

    published = []
    monkeypatch.setattr(
        streaming.stream_hub,
        "publish_line",
        lambda generation_id, line: published.append((generation_id, json.loads(line))),
    )

    utils._publish_research_event(
        "generation-1",
        {
            "run_id": "run-1",
            "sequence": 3,
            "event_type": "phase_started",
            "phase": "planning",
            "message_key": "deep_research_phase_planning",
            "payload": {},
        },
    )

    assert published == [
        (
            "generation-1",
            {
                "t": "deep_research_evt",
                "run_id": "run-1",
                "widget_id": "run-1",
                "sequence": 3,
                "phase": "planning",
                "message_key": "deep_research_phase_planning",
                "progress": 12,
                "event": "status",
                "status": "running",
            },
        )
    ]


def test_research_callback_forwards_model_stream_deltas(monkeypatch):
    """Every nested model request keeps its identity and visible output delta."""

    from app.chats import streaming

    published = []
    monkeypatch.setattr(
        streaming.stream_hub,
        "publish_line",
        lambda generation_id, line: published.append((generation_id, json.loads(line))),
    )

    utils._publish_research_event(
        "generation-1",
        {
            "run_id": "run-1",
            "event_type": "reasoning_delta",
            "phase": "evidence-audit",
            "payload": {
                "request_id": "nested-request-2",
                "delta": "Checking the cited evidence.",
                "replace": True,
            },
        },
    )

    assert published[0][0] == "generation-1"
    assert published[0][1]["t"] == "deep_research_evt"
    assert published[0][1]["event"] == "reasoning_delta"
    assert published[0][1]["request_id"] == "nested-request-2"
    assert published[0][1]["delta"] == "Checking the cited evidence."
    assert published[0][1]["replace"] is True


def test_research_tool_events_keep_request_and_call_identity():
    """Tool steps remain in the exact nested-model timeline that produced them."""

    started = utils._event_to_widget_payload(
        {
            "run_id": "run-1",
            "sequence": 10,
            "event_type": "tool_started",
            "phase": "deep-research",
            "payload": {
                "request_id": "nested-request-1",
                "tool_call_id": "call-1",
                "tool": "web_search",
            },
        }
    )
    completed = utils._event_to_widget_payload(
        {
            "run_id": "run-1",
            "sequence": 11,
            "event_type": "tool_completed",
            "phase": "deep-research",
            "payload": {
                "request_id": "nested-request-1",
                "tool_call_id": "call-1",
                "tool": "web_search",
                "success": True,
            },
        }
    )

    assert started["event"] == "tool_call"
    assert completed["event"] == "tool_result"
    assert started["request_id"] == completed["request_id"] == "nested-request-1"
    assert started["tool_call_id"] == completed["tool_call_id"] == "call-1"


def test_report_update_event_keeps_patch_stream_separate_from_article_preview():
    """The sidebar receives the applied report, not raw ArticleRevision JSON."""

    payload = utils._event_to_widget_payload(
        {
            "run_id": "run-1",
            "sequence": 9,
            "event_type": "report_updated",
            "phase": "final-revision-1",
            "payload": {"report": "# Patched report\n\nUntouched content."},
        }
    )

    assert payload["event"] == "report_updated"
    assert payload["report"].startswith("# Patched report")
    assert "edits" not in payload


def test_structured_planner_output_accepts_fenced_json():
    payload = _brief_payload()
    result = parse_structured_output(
        f"```json\n{json.dumps(payload)}\n```",
        ResearchBrief,
    )
    assert result.title == "Evidence report"
    assert result.research_questions[0].question == "What happened?"


def test_structured_output_extracts_complete_json_from_model_prose():
    """Accept valid JSON even when a provider ignores the JSON-only prompt."""

    payload = _review_payload(ready=True)
    result = parse_structured_output(
        f"I audited the report.\n{json.dumps(payload)}\nThat is the result.",
        QualityReview,
    )
    assert result.ready_to_publish is True


def test_article_revision_applies_only_anchored_non_overlapping_ranges():
    """Targeted revisions preserve every byte outside their exact ranges."""

    article = "# Original title\n\nKeep this paragraph unchanged.\n\nOld conclusion."
    revision = ArticleRevision.model_validate(
        {
            "summary": "Correct the title and conclusion.",
            "edits": [
                {
                    "start_snippet": "# Original title",
                    "end_snippet": "# Original title",
                    "replacement_markdown": "# Corrected title",
                },
                {
                    "start_snippet": "Old conclusion.",
                    "end_snippet": "Old conclusion.",
                    "replacement_markdown": "Supported conclusion.",
                },
            ],
        }
    )

    revised = apply_article_revision(article, revision)

    assert revised == (
        "# Corrected title\n\nKeep this paragraph unchanged.\n\nSupported conclusion."
    )


@pytest.mark.parametrize(
    ("article", "edit", "error"),
    [
        (
            "Repeated text. Middle. Repeated text.",
            {
                "start_snippet": "Repeated text.",
                "end_snippet": "Middle.",
                "replacement_markdown": "Replacement.",
            },
            "start_snippet matched more than once",
        ),
        (
            "# Title\n\nComplete article.",
            {
                "start_snippet": "# Title",
                "end_snippet": "Complete article.",
                "replacement_markdown": "# Rewritten\n\nEntire replacement.",
            },
            "targets the whole article",
        ),
    ],
)
def test_article_revision_rejects_ambiguous_or_document_wide_edits(
    article,
    edit,
    error,
):
    """The patch protocol must never guess anchors or accept a full rewrite."""

    revision = ArticleRevision.model_validate({"edits": [edit]})
    with pytest.raises(AnchoredEditError, match=error):
        apply_article_revision(article, revision)


def test_empty_provider_output_has_a_stable_public_error_code():
    """Keep empty successful streams distinct from internal server failures."""

    assert (
        public_error_code(DeepResearchEmptyResponse("empty"))
        == "provider_empty_response"
    )


def test_invalid_structured_output_has_a_stable_public_error_code():
    """Expose a specific recoverable contract instead of an internal error."""

    error = DeepResearchStructuredOutputError("QualityReview")
    assert public_error_code(error) == "structured_output_invalid"


def test_incomplete_provider_stream_has_a_stable_public_error_code():
    """Interrupted streams remain distinct from model schema failures."""

    error = DeepResearchIncompleteStream("final-revision-2")
    assert public_error_code(error) == "provider_incomplete_response"


def test_phase_retries_one_empty_provider_response(monkeypatch):
    """A metadata-only provider stream gets one transparent retry."""

    results = [
        PhaseResult(
            text="", usage=[{"attempt": 1}], raw_event_count=1, duration_seconds=0.1
        ),
        PhaseResult(
            text="usable",
            usage=[{"attempt": 2}],
            raw_event_count=2,
            duration_seconds=0.2,
        ),
    ]
    emitted = []
    run = SimpleNamespace(
        id="run-empty-retry",
        user_id="user-1",
        model_id="model-1",
        chat_id=None,
        generation_id=None,
        phase="planning",
        config_snapshot={},
    )

    monkeypatch.setattr(orchestrator, "_cancellation_requested", lambda *_args: False)
    monkeypatch.setattr(orchestrator, "_set_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_record_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        orchestrator, "_persist_phase_evidence", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        orchestrator,
        "_emit",
        lambda *_args, **kwargs: emitted.append(kwargs),
    )
    monkeypatch.setattr(
        orchestrator,
        "run_model_phase",
        lambda *_args, **_kwargs: results.pop(0),
    )

    result = orchestrator._run_phase(
        object(),
        run,
        phase="planning",
        instructions="Plan.",
        input_text="Question",
        tools=[],
        project_id=None,
        user_role=None,
        callback=None,
    )

    assert result.text == "usable"
    assert result.usage == [{"attempt": 1}, {"attempt": 2}]
    assert result.raw_event_count == 3
    assert any(event["event_type"] == "phase_retry" for event in emitted)


def test_phase_retries_interrupted_stream_without_using_schema_retry(monkeypatch):
    """Transport retries are independent from empty and schema-repair retries."""

    partial = PhaseResult(text='{"edits":[', raw_event_count=4, duration_seconds=0.4)
    outcomes = [
        DeepResearchIncompleteStream("final-revision-2", partial),
        PhaseResult(text="usable", raw_event_count=2, duration_seconds=0.2),
    ]
    emitted = []
    run = SimpleNamespace(
        id="run-stream-retry",
        user_id="user-1",
        model_id="model-1",
        chat_id=None,
        generation_id=None,
        phase="final-revision-2",
        config_snapshot={},
    )

    monkeypatch.setattr(orchestrator, "_cancellation_requested", lambda *_args: False)
    monkeypatch.setattr(orchestrator, "_set_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_record_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        orchestrator, "_persist_phase_evidence", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        orchestrator,
        "_emit",
        lambda *_args, **kwargs: emitted.append(kwargs),
    )

    def fake_run_model_phase(*_args, **_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(orchestrator, "run_model_phase", fake_run_model_phase)

    result = orchestrator._run_phase(
        object(),
        run,
        phase="final-revision-2",
        instructions="Revise.",
        input_text="Report",
        tools=[],
        project_id=None,
        user_role=None,
        callback=None,
    )

    assert result.text == "usable"
    assert result.raw_event_count == 6
    assert result.duration_seconds == 0.6
    assert any(
        event.get("message_key") == "deep_research_stream_interrupted_retrying"
        for event in emitted
    )


def test_phase_repairs_invalid_structured_output_without_reusing_tools(monkeypatch):
    """A schema retry completes existing work without repeating evidence calls."""

    results = [
        PhaseResult(
            text="I'll audit the report now.",
            sources=[{"url": "https://example.com/source", "title": "Source"}],
            usage=[{"attempt": 1}],
            raw_event_count=2,
            duration_seconds=0.2,
        ),
        PhaseResult(
            text=json.dumps(_review_payload(ready=True)),
            usage=[{"attempt": 2}],
            raw_event_count=1,
            duration_seconds=0.1,
        ),
    ]
    calls = []
    emitted = []
    run = SimpleNamespace(
        id="run-schema-retry",
        user_id="user-1",
        model_id="model-1",
        chat_id=None,
        generation_id=None,
        phase="evidence-audit",
        config_snapshot={},
    )

    monkeypatch.setattr(orchestrator, "_cancellation_requested", lambda *_args: False)
    monkeypatch.setattr(orchestrator, "_set_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_record_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        orchestrator, "_persist_phase_evidence", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        orchestrator,
        "_emit",
        lambda *_args, **kwargs: emitted.append(kwargs),
    )

    def fake_run_model_phase(*_args, **kwargs):
        calls.append(kwargs)
        return results.pop(0)

    monkeypatch.setattr(orchestrator, "run_model_phase", fake_run_model_phase)

    result = orchestrator._run_phase(
        object(),
        run,
        phase="evidence-audit",
        instructions="Audit.",
        input_text="Report",
        tools=["web_search"],
        project_id=None,
        user_role=None,
        callback=None,
        structured_schema=QualityReview,
    )

    assert result.structured_output.ready_to_publish is True
    assert calls[0]["tools"] == ["web_search"]
    assert calls[1]["tools"] == []
    assert "tool_call_limits" not in calls[0]
    assert "tool_call_limits" not in calls[1]
    assert "structured_schema" not in calls[0]
    assert "structured_schema" not in calls[1]
    assert "response_format" not in calls[0]["settings_override"]
    assert "response_format" not in calls[1]["settings_override"]
    assert "Original phase input:\nReport" in calls[1]["input_text"]
    assert result.sources == [{"url": "https://example.com/source", "title": "Source"}]
    assert result.usage == [{"attempt": 1}, {"attempt": 2}]
    assert any(
        event.get("message_key") == "deep_research_structured_output_retrying"
        for event in emitted
    )


def test_phase_repairs_valid_json_with_invalid_article_anchors(monkeypatch):
    """A semantic anchor failure gets one bounded, tool-free model repair."""

    article = "# Report\n\nOld supported statement."
    results = [
        PhaseResult(
            text=json.dumps(
                {
                    "edits": [
                        {
                            "start_snippet": "Missing statement.",
                            "end_snippet": "Missing statement.",
                            "replacement_markdown": "Corrected statement.",
                        }
                    ]
                }
            ),
            usage=[{"attempt": 1}],
        ),
        PhaseResult(
            text=json.dumps(
                {
                    "edits": [
                        {
                            "start_snippet": "Old supported statement.",
                            "end_snippet": "Old supported statement.",
                            "replacement_markdown": "Corrected supported statement.",
                        }
                    ]
                }
            ),
            usage=[{"attempt": 2}],
        ),
    ]
    calls = []
    run = SimpleNamespace(
        id="run-anchor-retry",
        user_id="user-1",
        model_id="model-1",
        chat_id=None,
        generation_id=None,
        phase="final-revision-1",
        config_snapshot={},
    )

    monkeypatch.setattr(orchestrator, "_cancellation_requested", lambda *_args: False)
    monkeypatch.setattr(orchestrator, "_set_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "_record_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        orchestrator, "_persist_phase_evidence", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(orchestrator, "_emit", lambda *_args, **_kwargs: None)

    def fake_run_model_phase(*_args, **kwargs):
        calls.append(kwargs)
        return results.pop(0)

    monkeypatch.setattr(orchestrator, "run_model_phase", fake_run_model_phase)

    result = orchestrator._run_phase(
        object(),
        run,
        phase="final-revision-1",
        instructions="Edit with anchors.",
        input_text=article,
        tools=["web_search"],
        project_id=None,
        user_role=None,
        callback=None,
        structured_schema=ArticleRevision,
        structured_validator=lambda candidate: validate_article_revision(
            article,
            candidate,
        ),
        structured_repair_context=lambda candidate, error: (
            article_revision_repair_context(article, candidate, error)
        ),
    )

    assert calls[0]["tools"] == ["web_search"]
    assert calls[1]["tools"] == []
    assert "was not found in the existing article" in calls[1]["input_text"]
    assert "Verbatim current-article excerpt" in calls[1]["input_text"]
    assert "Old supported statement." in calls[1]["input_text"]
    assert apply_article_revision(article, result.structured_output).endswith(
        "Corrected supported statement."
    )


def test_article_revision_repair_context_isolates_the_rejected_edit():
    """Anchor repair receives the exact edit and nearby immutable source text."""

    article = (
        "# Report\n\n## Section\n\nExisting sentence without a citation.\n\n## Next"
    )
    revision = ArticleRevision.model_validate(
        {
            "edits": [
                {
                    "start_snippet": "## Section",
                    "end_snippet": "Existing sentence. [Source](https://example.com)",
                    "replacement_markdown": "Revised sentence.",
                }
            ]
        }
    )

    context = article_revision_repair_context(
        article,
        revision,
        "Edit 1 end_snippet was not found after start_snippet.",
    )

    assert '"start_snippet": "## Section"' in context
    assert "Existing sentence without a citation." in context
    assert "Copy every start_snippet and end_snippet verbatim" in context


def test_custom_flow_reuses_shared_chat_tool_names(monkeypatch, tmp_path):
    """Exercise the v2 phase graph and assert its production tool contracts."""

    calls = []
    phase_outputs = {
        "planning": json.dumps(_brief_payload()),
        "deep-research": "# Draft\n\nEvidence [source](https://example.com/a).",
        "evidence-audit": json.dumps(_review_payload(ready=False)),
        "final-revision-1": json.dumps(
            {
                "summary": "Strengthen the draft.",
                "edits": [
                    {
                        "start_snippet": "# Draft",
                        "end_snippet": "# Draft",
                        "replacement_markdown": "# Revision 1",
                    },
                    {
                        "start_snippet": "Evidence",
                        "end_snippet": "Evidence",
                        "replacement_markdown": "Verified",
                    },
                ],
            }
        ),
        "release-gate-1": json.dumps(_review_payload(ready=False)),
        "final-revision-2": json.dumps(
            {
                "summary": "Apply final localized corrections.",
                "edits": [
                    {
                        "start_snippet": "# Revision 1",
                        "end_snippet": "# Revision 1",
                        "replacement_markdown": "# Final",
                    },
                    {
                        "start_snippet": "Verified",
                        "end_snippet": "Verified",
                        "replacement_markdown": "Latest verified",
                    },
                ],
            }
        ),
        "release-gate-2": json.dumps(_review_payload(ready=False)),
    }

    def fake_run_phase(_db, _run, **kwargs):
        calls.append((kwargs["phase"], list(kwargs["tools"])))
        schema_type = kwargs.get("structured_schema")
        if kwargs["phase"].startswith("final-revision"):
            assert schema_type is ArticleRevision
        structured_output = (
            parse_structured_output(phase_outputs[kwargs["phase"]], schema_type)
            if schema_type is not None
            else None
        )
        return PhaseResult(
            text=phase_outputs[kwargs["phase"]],
            generated_files=[],
            tool_calls=[],
            usage=[],
            raw_event_count=1,
            structured_output=structured_output,
        )

    class FakeDB:
        def add(self, _value):
            return None

        def commit(self):
            return None

        def refresh(self, _value):
            return None

    run = SimpleNamespace(
        id="run-1",
        user_id="user-1",
        chat_id="chat-1",
        generation_id="generation-1",
        query="Research this",
        execution_mode="custom",
        output_format="markdown",
        status="running",
        phase="starting",
        provider_id="provider-1",
        model_id="model-1",
        model_name="Model",
        prompt_version="v2",
        revision_round=0,
        max_revision_rounds=2,
        cancel_requested=False,
        started_at=None,
        completed_at=None,
        created_at=None,
        updated_at=None,
        final_report_path=None,
        final_html_path=None,
        manifest_path=None,
        config_snapshot={},
        usage={},
        quality_gate={},
        result_meta={},
    )
    monkeypatch.setattr(orchestrator, "_run_phase", fake_run_phase)
    monkeypatch.setattr(orchestrator, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        orchestrator,
        "get_deep_research_workspace_dir",
        lambda *_args: tmp_path,
    )
    monkeypatch.setattr(
        orchestrator,
        "materialize_deep_research_artifact",
        lambda _user_id, _run_id, relative_path: tmp_path / relative_path,
    )
    monkeypatch.setattr(
        orchestrator, "_persist_phase_artifacts", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        orchestrator, "list_run_artifacts", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(orchestrator, "artifact_manifest", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        orchestrator,
        "_persist_evidence_index",
        lambda *_args, **_kwargs: ["https://example.com/a"],
    )
    monkeypatch.setattr(
        orchestrator,
        "upload_deep_research_artifacts",
        lambda **_kwargs: {"provider": "local", "uploaded_files": []},
    )

    result = orchestrator.run_custom_research(FakeDB(), run)

    assert calls == [
        ("planning", []),
        (
            "deep-research",
            ["web_search", "code_execution", "deep_research_import_web_image"],
        ),
        ("evidence-audit", ["web_search"]),
        (
            "final-revision-1",
            ["web_search", "code_execution", "deep_research_import_web_image"],
        ),
        ("release-gate-1", ["web_search"]),
        (
            "final-revision-2",
            ["web_search", "code_execution", "deep_research_import_web_image"],
        ),
        ("release-gate-2", ["web_search"]),
    ]
    assert run.status == RUN_STATUS_COMPLETED
    assert run.phase == "completed"
    assert run.quality_gate["ready_to_publish"] is True
    assert run.quality_gate["review_ready_to_publish"] is False
    assert run.quality_gate["accepted_after_final_round"] is True
    assert Path(result["workspace"], "final-report.md").is_file()
    assert not Path(result["workspace"], "final-report.html").exists()
    assert Path(result["workspace"], "final-revision-1-edits.json").is_file()
    assert Path(result["workspace"], "final-revision-2-edits.json").is_file()
    assert result["report"].startswith("# Final")

    # A direct retry consumes completed checkpoints instead of repeating
    # provider, Web Search, or Code Execution calls.
    calls.clear()
    run.status = "running"
    run.phase = "planning"
    run.final_report_path = None
    run.final_html_path = None
    retried = orchestrator.run_custom_research(FakeDB(), run)
    assert calls == []
    assert run.status == RUN_STATUS_COMPLETED
    assert retried["report"].startswith("# Final")


def test_final_revision_failure_publishes_last_checkpoint_with_warning(
    monkeypatch,
    tmp_path,
):
    """Late formatting failures must not discard an already researched report."""

    emitted = []
    outputs = {
        "planning": (ResearchBrief, _brief_payload()),
        "deep-research": (None, "# Draft\n\nVerified evidence."),
        "evidence-audit": (QualityReview, _review_payload(ready=False)),
    }

    def fake_run_phase(_db, _run, **kwargs):
        phase = kwargs["phase"]
        if phase == "final-revision-1":
            raise DeepResearchStructuredOutputError(
                "ArticleRevision",
                "Edit 1 end_snippet was not found after start_snippet.",
            )
        schema_type, payload = outputs[phase]
        text = payload if isinstance(payload, str) else json.dumps(payload)
        structured_output = (
            schema_type.model_validate(payload) if schema_type is not None else None
        )
        return PhaseResult(text=text, structured_output=structured_output)

    class FakeDB:
        def add(self, _value):
            return None

        def commit(self):
            return None

        def refresh(self, _value):
            return None

    run = SimpleNamespace(
        id="run-degraded",
        user_id="user-1",
        chat_id="chat-1",
        generation_id="generation-1",
        query="Research this",
        execution_mode="custom",
        output_format="markdown",
        status="running",
        phase="starting",
        provider_id="provider-1",
        model_id="model-1",
        model_name="Model",
        prompt_version="v2",
        revision_round=0,
        max_revision_rounds=1,
        cancel_requested=False,
        started_at=None,
        completed_at=None,
        created_at=None,
        updated_at=None,
        final_report_path=None,
        final_html_path=None,
        manifest_path=None,
        error_code=None,
        error_message_key=None,
        config_snapshot={},
        usage={},
        quality_gate={},
        result_meta={},
    )
    monkeypatch.setattr(orchestrator, "_run_phase", fake_run_phase)
    monkeypatch.setattr(
        orchestrator,
        "_emit",
        lambda *_args, **kwargs: emitted.append(kwargs),
    )
    monkeypatch.setattr(
        orchestrator,
        "get_deep_research_workspace_dir",
        lambda *_args: tmp_path,
    )
    monkeypatch.setattr(
        orchestrator,
        "materialize_deep_research_artifact",
        lambda _user_id, _run_id, relative_path: tmp_path / relative_path,
    )
    monkeypatch.setattr(
        orchestrator, "_persist_phase_artifacts", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        orchestrator, "list_run_artifacts", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(orchestrator, "artifact_manifest", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        orchestrator, "_persist_evidence_index", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        orchestrator,
        "upload_deep_research_artifacts",
        lambda **_kwargs: {"provider": "local", "uploaded_files": []},
    )

    result = orchestrator.run_custom_research(FakeDB(), run)

    assert run.status == RUN_STATUS_COMPLETED
    assert result["report"] == "# Draft\n\nVerified evidence."
    assert Path(result["workspace"], "final-report.md").read_text() == result["report"]
    assert result["completion_warning"] == {
        "degraded": True,
        "warning_code": "structured_output_invalid",
        "warning_phase": "final-revision-1",
        "message_key": "deep_research_completed_with_warnings",
    }
    assert any(
        event.get("event_type") == "partial_report_available" for event in emitted
    )
    assert emitted[-1]["message_key"] == "deep_research_completed_with_warnings"
    restored_widget = utils._widget_data(run)
    assert restored_widget["warning_code"] == "structured_output_invalid"
    assert restored_widget["has_completion_warning"] is True


def test_generated_chart_gets_durable_alt_text_and_visible_caption(monkeypatch):
    artifact = SimpleNamespace(
        kind="image",
        validation_status="validated",
        relative_path="artifacts/chart.png",
        alt_text=None,
        caption=None,
    )

    class DB:
        committed = False

        def commit(self):
            self.committed = True

    db = DB()
    monkeypatch.setattr(
        orchestrator,
        "list_run_artifacts",
        lambda *_args, **_kwargs: [artifact],
    )
    report = orchestrator._synchronize_visual_metadata(
        db,
        SimpleNamespace(id="run-1"),
        "![Quarterly revenue by region](artifacts/chart.png)",
    )

    assert artifact.alt_text == "Quarterly revenue by region"
    assert artifact.caption == "Quarterly revenue by region"
    assert "*Quarterly revenue by region*" in report
    assert db.committed is True


def test_release_gate_cannot_publish_major_issue_or_remote_image():
    review = QualityReview(
        overall_assessment="Ready.",
        ready_to_publish=True,
        coverage=[
            {
                "research_question": "What happened?",
                "status": "answered",
                "evidence_summary": "Covered.",
            }
        ],
        issues=[
            ReviewIssue(
                severity="major",
                category="citation",
                claim_or_section="Result",
                problem="Unsupported.",
                required_fix="Add a primary source.",
            )
        ],
    )
    checked = orchestrator._enforce_release_invariants(
        review,
        report=(
            "Evidence [source](https://example.com/source).\n\n"
            "![Remote](https://example.com/image.png)"
        ),
        brief=ResearchBrief.model_validate(_brief_payload()),
        artifacts=[],
    )
    assert checked.ready_to_publish is False
    assert sum(issue.severity == "major" for issue in checked.issues) == 2

    cleaned, removed = remove_remote_image_embeds(
        "![Remote](https://example.com/image.png)"
    )
    assert cleaned == "[Remote](https://example.com/image.png)"
    assert removed == ["https://example.com/image.png"]


def test_native_adapter_uses_background_interactions_and_extracts_citations():
    captured = {}

    class Interactions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="interaction-1", status="in_progress")

    response = native._create_interaction(
        SimpleNamespace(interactions=Interactions()),
        "deep-research-preview",
        "Research this.",
    )
    citations = native._citations(
        {
            "sources": [
                {"title": "Primary", "url": "https://example.com/source"},
                {"url": "https://example.com/source"},
            ]
        }
    )

    assert response.id == "interaction-1"
    assert captured["background"] is True
    assert captured["agent_config"]["type"] == "deep-research"
    assert citations == [{"url": "https://example.com/source", "title": "Primary"}]


def test_native_adapter_bounds_polling_timeout_configuration():
    """Native polling always has a finite, positive wall-clock deadline."""

    assert native._native_timeout_seconds({"native_timeout_seconds": 12}) == 12
    assert native._native_timeout_seconds({"native_timeout_seconds": 0}) == 3600
    assert native._native_timeout_seconds({"native_timeout_seconds": "invalid"}) == 3600
    assert native._native_timeout_seconds({"native_timeout_seconds": 999_999}) == 86_400


def test_deep_research_citation_normalizer_rejects_non_http_schemes():
    """Imported citation metadata is filtered before reaching any consumer."""

    assert utils._normalize_citations(
        [
            "https://example.com/source",
            {"url": "javascript:alert(1)"},
            {"canonical_url": "data:text/html,unsafe"},
            {"url": "/relative"},
        ]
    ) == [
        {
            "url": "https://example.com/source",
            "title": "https://example.com/source",
            "snippet": "",
        }
    ]


def test_native_adapter_extracts_only_visible_thinking_summaries():
    """Native research forwards published summaries without private signatures."""

    response = {
        "steps": [
            {
                "type": "thought",
                "signature": "private-validation-signature",
                "summary": [
                    {"type": "text", "text": "Checking primary sources."},
                    {"type": "image", "data": "ignored"},
                ],
            },
            {"type": "model_output", "content": [{"type": "text", "text": "Report"}]},
            {
                "type": "thought",
                "summary": [{"type": "text", "text": "Comparing the findings."}],
            },
        ]
    }

    assert native._visible_thinking_text(response) == (
        "Checking primary sources.\n\nComparing the findings."
    )
    assert native._snapshot_delta("Checking", "Checking sources") == (" sources", False)
    assert native._snapshot_delta("Old summary", "Rewritten summary") == (
        "Rewritten summary",
        True,
    )


def test_web_image_import_accepts_raster_signatures_and_rejects_svg():
    assert _detect_image_type(b"\x89PNG\r\n\x1a\nrest") == ("image/png", ".png")
    with pytest.raises(ValueError, match="supported raster"):
        _detect_image_type(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")


def test_web_image_import_requires_caption_before_download(monkeypatch):
    monkeypatch.setattr(
        web_images,
        "assert_public_http_url_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        web_images,
        "_download_public_image",
        lambda *_args, **_kwargs: pytest.fail("download must not start"),
    )
    with pytest.raises(ValueError, match="caption is required"):
        web_images.import_web_image(
            object(),
            user_id="user-1",
            image_url="https://example.com/image.png",
            source_url="https://example.com/source",
            attribution="Example",
            alt_text="Evidence",
            caption="",
        )


def test_workspace_paths_reject_unsafe_claims(tmp_path):
    with pytest.raises(ValueError, match="traversal"):
        storage.build_deep_research_storage_key(
            "user-1",
            "run-1",
            "../other-user/report.md",
        )
    with pytest.raises(ValueError, match="escapes"):
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "linked"
        link.symlink_to(outside, target_is_directory=True)
        storage.ensure_safe_workspace_path(tmp_path, "linked/report.md")


def test_hard_deleted_run_removes_local_workspace_and_materialized_caches(
    monkeypatch,
    tmp_path,
):
    base_storage = tmp_path / "storage"
    materialized = tmp_path / "materialized"
    monkeypatch.setattr(storage, "BASE_STORAGE_DIR", base_storage)
    monkeypatch.setattr(storage, "MATERIALIZED_TEMP_DIR", materialized)
    prefix = storage.build_deep_research_storage_prefix("user-1", "run-1")
    targets = [
        base_storage / prefix,
        materialized / "deep_research_workspaces" / prefix,
        materialized / "deep_research_artifacts" / "local" / prefix,
    ]
    for target in targets:
        target.mkdir(parents=True)
        (target / "report.md").write_text("sensitive", encoding="utf-8")

    storage.delete_deep_research_run_artifacts(
        user_id="user-1",
        run_id="run-1",
        storage_provider="local",
        relative_paths=["report.md"],
    )

    assert all(not target.exists() for target in targets)


def test_provider_phase_has_no_profile_tool_call_budget(monkeypatch):
    """Research phases accept tool calls without profile-derived ceilings."""

    model = SimpleNamespace(
        id="model-1",
        name="Model",
        provider="openai",
        provider_id="provider-1",
        model_name="model",
        settings={},
        capabilities=["tools"],
        access={},
        meta={},
        status="active",
        is_active=True,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

    class DB:
        def query(self, *_args):
            return Query()

    monkeypatch.setattr(
        providers,
        "call_provider_chat",
        lambda _request: iter(
            [
                json.dumps({"t": "t_c", "d": {"name": "web_search"}}),
                json.dumps({"t": "t_c", "d": {"name": "web_search"}}),
                json.dumps({"t": "d", "d": "f", "c": {}}),
            ]
        ),
    )
    result = providers.run_model_phase(
        DB(),
        model_id="model-1",
        user_id="user-1",
        run_id="run-1",
        phase="deep-research",
        instructions="Research.",
        input_text="Question",
        tools=["web_search"],
        chat_id=None,
        project_id=None,
        generation_id=None,
        user_role="user",
        settings_override={},
    )

    assert len(result.tool_calls) == 2


def test_xai_provider_phase_enables_runtime_tools_without_mutating_model(monkeypatch):
    """xAI phases must reach Responses with their tools and instructions intact."""

    stored_capabilities = ["completion", "thinking"]
    model = SimpleNamespace(
        id="model-1",
        name="Model",
        provider="xai",
        provider_id="provider-1",
        model_name="model",
        settings={},
        capabilities=stored_capabilities,
        tools=[],
        access={},
        meta={},
        status="active",
        is_active=True,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

    class DB:
        def query(self, *_args):
            return Query()

    captured = {}

    def fake_provider_chat(**kwargs):
        captured["kwargs"] = kwargs
        return iter(
            [
                json.dumps({"t": "c", "d": "Report"}),
                json.dumps({"t": "d", "d": "f", "c": {}}),
            ]
        )

    monkeypatch.setattr(openai_utils, "openai_chat", fake_provider_chat)

    result = providers.run_model_phase(
        DB(),
        model_id="model-1",
        user_id="user-1",
        run_id="run-1",
        phase="deep-research",
        instructions="Research.",
        input_text="Question",
        tools=["web_search", "code_execution"],
        chat_id=None,
        project_id=None,
        generation_id=None,
        user_role="user",
        settings_override={},
    )

    provider_kwargs = captured["kwargs"]
    phase_model = provider_kwargs["db_model"]
    assert result.text == "Report"
    assert provider_kwargs["openai_provider_type"] == "xai"
    assert phase_model.tools == ["web_search", "code_execution"]
    assert "tools" in phase_model.capabilities
    assert provider_kwargs["settings_override"]["enabled_tools"] == [
        "web_search",
        "code_execution",
    ]
    assert provider_kwargs["system_instruction_sections"] == [
        {
            "title": "Deep Research Deep-Research Instructions",
            "content": "Research.",
        }
    ]
    assert provider_kwargs["assistant_metadata"] == {
        "deep_research": True,
        "deep_research_run_id": "run-1",
        "deep_research_phase": "deep-research",
    }
    assert model.capabilities == ["completion", "thinking"]
    assert model.tools == []


def test_provider_phase_captures_normal_chat_metadata_and_tool_completion(
    monkeypatch,
):
    """The adapter must consume the exact metadata emitted by normal chat."""

    model = SimpleNamespace(
        id="model-1",
        name="Model",
        provider="openai",
        provider_id="provider-1",
        model_name="model",
        settings={},
        capabilities=["tools"],
        access={},
        meta={},
        status="active",
        is_active=True,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

    class DB:
        def query(self, *_args):
            return Query()

    events = []
    monkeypatch.setattr(
        providers,
        "call_provider_chat",
        lambda _request: iter(
            [
                json.dumps(
                    {
                        "t": "t_c",
                        "d": {
                            "id": "call-1",
                            "name": "web_search",
                            "args": {"query": "primary source"},
                        },
                    }
                ),
                json.dumps({"t": "r", "d": "Checking sources"}),
                json.dumps({"t": "r_f", "d": 1.25}),
                json.dumps({"t": "c", "d": "Report"}),
                json.dumps(
                    {
                        "t": "d",
                        "d": "f",
                        "c": {
                            "total_tokens": 42,
                            "citations": [
                                {
                                    "url": "https://example.com/source",
                                    "title": "Source",
                                    "snippet": "Evidence.",
                                }
                            ],
                        },
                    }
                ),
            ]
        ),
    )

    result = providers.run_model_phase(
        DB(),
        model_id="model-1",
        user_id="user-1",
        run_id="run-1",
        phase="deep-research",
        instructions="Research.",
        input_text="Question",
        tools=["web_search"],
        chat_id=None,
        project_id=None,
        generation_id=None,
        user_role="user",
        settings_override={},
        event_callback=events.append,
    )

    assert result.text == "Report"
    assert result.usage[-1]["total_tokens"] == 42
    assert result.sources == [
        {
            "url": "https://example.com/source",
            "title": "Source",
            "snippet": "Evidence.",
        }
    ]
    assert [event["event"] for event in events] == [
        "llm_request_started",
        "tool_started",
        "tool_completed",
        "reasoning_delta",
        "reasoning_completed",
        "content_delta",
        "llm_request_completed",
    ]
    assert events[1]["request_id"].startswith("deep-research:run-1:deep-research:")
    assert events[2]["request_id"] == events[1]["request_id"]
    assert events[1]["arguments"] == {"query": "primary source"}
    assert result.tool_calls[0]["arguments"] == {"query": "primary source"}
    assert events[3]["delta"] == "Checking sources"
    assert events[5]["delta"] == "Report"


def test_provider_phase_rejects_text_stream_without_terminal_event(monkeypatch):
    """A proxy disconnect must not turn partial JSON into a completed phase."""

    model = SimpleNamespace(
        id="model-1",
        name="Model",
        provider="openai_responses",
        provider_id="provider-1",
        model_name="model",
        settings={},
        capabilities=[],
        tools=[],
        access={},
        meta={},
        status="active",
        is_active=True,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

    class DB:
        def query(self, *_args):
            return Query()

    events = []
    monkeypatch.setattr(
        providers,
        "call_provider_chat",
        lambda _request: iter(
            [json.dumps({"t": "c", "d": '{"edits":[{"start_snippet":"partial'})]
        ),
    )

    with pytest.raises(DeepResearchIncompleteStream) as caught:
        providers.run_model_phase(
            DB(),
            model_id="model-1",
            user_id="user-1",
            run_id="run-1",
            phase="final-revision-2",
            instructions="Repair.",
            input_text="Report",
            tools=[],
            chat_id=None,
            project_id=None,
            generation_id=None,
            user_role="user",
            settings_override={},
            event_callback=events.append,
        )

    assert caught.value.partial_result.text.endswith('"partial')
    assert events[-1]["event"] == "llm_request_failed"
    assert not any(event["event"] == "llm_request_completed" for event in events)


def test_provider_phase_uses_only_terminal_assistant_turn_as_output(monkeypatch):
    """Pre-tool progress stays observable but never contaminates the report."""

    model = SimpleNamespace(
        id="model-1",
        name="Model",
        provider="openai",
        provider_id="provider-1",
        model_name="model",
        settings={},
        capabilities=["tools"],
        access={},
        meta={},
        status="active",
        is_active=True,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

    class DB:
        def query(self, *_args):
            return Query()

    events = []
    monkeypatch.setattr(
        providers,
        "call_provider_chat",
        lambda _request: iter(
            [
                json.dumps({"t": "c", "d": "I will check the current source."}),
                json.dumps(
                    {
                        "t": "t_c",
                        "d": {"id": "call-1", "name": "web_search"},
                    }
                ),
                json.dumps({"t": "c", "d": "The first lookup was inconclusive."}),
                json.dumps(
                    {
                        "t": "t_c",
                        "d": {"id": "call-2", "name": "web_search"},
                    }
                ),
                json.dumps({"t": "c", "d": "# Final report"}),
                json.dumps({"t": "c", "d": "\n\nVerified answer."}),
                json.dumps({"t": "d", "d": "f", "c": {"total_tokens": 21}}),
            ]
        ),
    )

    result = providers.run_model_phase(
        DB(),
        model_id="model-1",
        user_id="user-1",
        run_id="run-1",
        phase="deep-research",
        instructions="Research.",
        input_text="Question",
        tools=["web_search"],
        chat_id=None,
        project_id=None,
        generation_id=None,
        user_role="user",
        settings_override={},
        event_callback=events.append,
    )

    assert result.text == "# Final report\n\nVerified answer."
    assert [
        event["delta"] for event in events if event["event"] == "content_delta"
    ] == [
        "I will check the current source.",
        "The first lookup was inconclusive.",
        "# Final report",
        "\n\nVerified answer.",
    ]


def test_provider_phase_does_not_inject_structured_output_parameters(monkeypatch):
    """Deep Research must remain compatible with every normal chat provider."""

    model = SimpleNamespace(
        id="model-1",
        name="Model",
        provider="openrouter",
        provider_id="provider-1",
        model_name="model",
        settings={},
        capabilities=[],
        access={},
        meta={},
        status="active",
        is_active=True,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

    class DB:
        def query(self, *_args):
            return Query()

    captured = {}

    def fake_provider_chat(request):
        captured["request"] = request
        return iter(
            [
                json.dumps({"t": "c", "d": json.dumps(_review_payload(ready=True))}),
                json.dumps({"t": "d", "d": "f", "c": {}}),
            ]
        )

    monkeypatch.setattr(providers, "call_provider_chat", fake_provider_chat)
    providers.run_model_phase(
        DB(),
        model_id="model-1",
        user_id="user-1",
        run_id="run-1",
        phase="evidence-audit",
        instructions="Audit.",
        input_text="Report",
        tools=[],
        chat_id=None,
        project_id=None,
        generation_id=None,
        user_role="user",
        settings_override={},
    )

    settings = captured["request"].settings_override
    assert "structured_outputs" not in settings
    assert "response_format" not in settings


def test_config_is_greenfield_and_has_no_legacy_modes(monkeypatch):
    monkeypatch.setattr(
        utils,
        "get_settings_page",
        lambda *_args: SimpleNamespace(
            data={
                "execution_mode": "orchestrated",
                "quality_profile": "unknown",
                "max_revision_rounds": 99,
            }
        ),
    )
    config = utils.get_deep_research_config(object())
    assert config["execution_mode"] == "custom"
    assert "quality_profile" not in config
    assert config["max_revision_rounds"] == 3


def test_run_creation_accepts_configured_model_without_tools(monkeypatch):
    """Runtime research tools are injected independently of model metadata."""

    model = SimpleNamespace(
        id="completion-model",
        name="Completion Model",
        model_name="completion-model",
        provider="openrouter",
        provider_id="provider-1",
        capabilities=["completion", "thinking"],
        is_active=True,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

    class DB:
        def query(self, *_args):
            return Query()

    monkeypatch.setattr(
        utils,
        "get_deep_research_config",
        lambda _db: {
            "execution_mode": "custom",
            "model_id": "completion-model",
            "native_provider_id": "",
            "native_model_name": "",
            "max_revision_rounds": 2,
            "websearch_search_provider": "",
            "websearch_scrape_provider": "",
        },
    )
    created_run = SimpleNamespace(id="run-1")
    captured_create_args = {}

    def fake_create_run(*_args, **kwargs):
        captured_create_args.update(kwargs)
        return created_run

    monkeypatch.setattr(
        utils,
        "create_deep_research_run",
        fake_create_run,
    )
    run = utils.create_research_run(
        DB(),
        user_id="user-1",
        query="Research this",
        authorization_context={
            "origin_kind": "model",
            "origin_model_id": "chat-model-1",
            "runtime_enabled_tools": ["deep_research", "deep_research"],
        },
    )

    assert run is created_run
    assert captured_create_args["output_format"] == "markdown"
    assert "html_model_id" not in captured_create_args["config_snapshot"]
    assert "quality_profile" not in captured_create_args
    assert "budgets" not in captured_create_args["config_snapshot"]
    assert captured_create_args["config_snapshot"]["execution_authorization"] == {
        "schema_version": 1,
        "origin_kind": "model",
        "origin_model_id": "chat-model-1",
        "runtime_enabled_tools": ["deep_research"],
    }


def test_chat_tool_executes_inline_and_returns_terminal_report(monkeypatch):
    """Research phases run in the chat generation before the main model resumes."""

    started_run = SimpleNamespace(
        id="run-waiting",
        user_id="user-1",
        query="Research this",
        model_id="model-1",
        model_name="Research Model",
        generation_id="generation-1",
        execution_mode="custom",
        output_format="markdown",
        revision_round=0,
        status="running",
        phase="starting",
        final_report_path=None,
        final_html_path=None,
        manifest_path=None,
        error_code=None,
        error_message_key=None,
        result_meta={},
    )

    monkeypatch.setattr(
        utils,
        "create_research_run",
        lambda *_args, **_kwargs: started_run,
    )

    streamed_events = []

    def run_inline(_db, run, *, project_id, user_role, callback):
        assert project_id is None
        assert user_role is None
        callback(
            {
                "run_id": run.id,
                "sequence": 1,
                "event_type": "phase_started",
                "phase": "planning",
                "message_key": "deep_research_phase_planning",
                "payload": {},
            }
        )
        callback(
            {
                "run_id": run.id,
                "sequence": 2,
                "event_type": "llm_request_started",
                "phase": "planning",
                "payload": {"request_id": "nested-request-1"},
            }
        )
        callback(
            {
                "run_id": run.id,
                "sequence": 3,
                "event_type": "reasoning_delta",
                "phase": "planning",
                "payload": {
                    "request_id": "nested-request-1",
                    "delta": "Checking",
                },
            }
        )
        callback(
            {
                "run_id": run.id,
                "sequence": 4,
                "event_type": "reasoning_delta",
                "phase": "planning",
                "payload": {
                    "request_id": "nested-request-1",
                    "delta": " sources",
                },
            }
        )
        callback(
            {
                "run_id": run.id,
                "sequence": 5,
                "event_type": "tool_started",
                "phase": "planning",
                "payload": {
                    "request_id": "nested-request-1",
                    "tool_call_id": "call-1",
                    "tool": "web_search",
                    "arguments": {"query": "primary source"},
                },
            }
        )
        callback(
            {
                "run_id": run.id,
                "sequence": 6,
                "event_type": "tool_completed",
                "phase": "planning",
                "payload": {
                    "request_id": "nested-request-1",
                    "tool_call_id": "call-1",
                    "tool": "web_search",
                    "success": True,
                    "result": "must not be persisted",
                },
            }
        )
        callback(
            {
                "run_id": run.id,
                "sequence": 7,
                "event_type": "content_delta",
                "phase": "planning",
                "payload": {
                    "request_id": "nested-request-1",
                    "delta": "Finished planning.",
                },
            }
        )
        callback(
            {
                "run_id": run.id,
                "sequence": 8,
                "event_type": "llm_request_completed",
                "phase": "planning",
                "payload": {
                    "request_id": "nested-request-1",
                    "duration_seconds": 1.5,
                },
            }
        )
        run.status = "completed"
        run.phase = "completed"

    class DB:
        def refresh(self, _run):
            return None

    monkeypatch.setattr(utils, "run_custom_research", run_inline)
    monkeypatch.setattr(utils, "_audit_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        utils,
        "_publish_research_event",
        lambda generation_id, event: streamed_events.append((generation_id, event)),
    )
    monkeypatch.setattr(
        utils,
        "_terminal_run_payload",
        lambda _run: {
            "event": "complete",
            "run_id": started_run.id,
            "generation_id": started_run.generation_id,
            "query": started_run.query,
            "status": "completed",
            "phase": "completed",
            "report": "# Final report\n\nEvidence-backed answer.",
            "citations": [
                {
                    "title": "Primary source",
                    "url": "https://example.com/source",
                    "snippet": "",
                }
            ],
            "files": ["final-report.md"],
            "final_report_path": "final-report.md",
            "archive_path": "workspace.zip",
            "error_code": None,
        },
    )

    runner = utils.deep_research(
        db=DB(),
        user_id="user-1",
        query=started_run.query,
        generation_id="generation-1",
    )
    assert json.loads(next(runner))["t"] == "wg"
    final_event = json.loads(next(runner))
    assert final_event["event"] == "complete"
    assert streamed_events[0][0] == "generation-1"
    assert streamed_events[0][1]["phase"] == "planning"

    with pytest.raises(StopIteration) as finished:
        next(runner)
    payload = finished.value.value
    assert payload["result"]["status"] == "completed"
    assert "Evidence-backed answer." in payload["content"]
    widget_data = json.loads(payload["widget"]["html"])
    assert widget_data["generation_id"] == "generation-1"
    assert widget_data["status"] == "completed"
    assert widget_data["final_report_path"] == "final-report.md"
    assert widget_data["model"] == "Research Model"
    assert payload["widget"]["render_mode"] == "frontend"
    assert payload["widget"]["model_context"]["status"] == "completed"
    assert "https://example.com/source" in payload["content"]
    assert payload["tool_meta"]["status"] == "completed"
    activity = payload["tool_meta"]["deep_research_activity"]
    assert activity["schema_version"] == 1
    activity_events = activity["events"]
    assert [event["event"] for event in activity_events[1:7]] == [
        "llm_request_started",
        "reasoning_delta",
        "tool_call",
        "tool_result",
        "content_delta",
        "llm_request_completed",
    ]
    assert activity_events[2]["delta"] == "Checking sources"
    assert activity_events[3]["arguments"] == {"query": "primary source"}
    assert "result" not in activity_events[4]
    assert activity_events[-1]["event"] == "complete"
    assert "report" not in activity_events[-1]
    assert "citations" not in activity_events[-1]
    assert "files" not in activity_events[-1]
    assert "deep_research_activity" not in payload["widget"]["model_context"]
    assert "deferred" not in payload["result"]
