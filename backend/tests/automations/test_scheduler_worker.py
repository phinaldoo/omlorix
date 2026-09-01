import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())


from app.automations import jobs, queue, worker
from app.automations.models import (
    Automation,
    AutomationExecution,
    claim_due_automations,
    complete_automation_schedule_for_slot,
    reserve_automation_execution,
    remove_mcp_server_from_automations,
    start_automation_execution,
    update_automation,
)
from app.automations.schedule import compute_next_schedule_state
from app.database import Base


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Automation.__table__, AutomationExecution.__table__])
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_compute_next_schedule_state_picks_the_earliest_slot():
    reference_time = datetime(2026, 5, 25, 8, 30, tzinfo=timezone.utc)
    schedule_state = compute_next_schedule_state(
        [
            {"days": [0, 2], "times": ["09:00", "15:00"]},
            {"run_at": "2026-05-26T07:45:00Z"},
        ],
        reference_time=reference_time,
    )

    assert schedule_state is not None
    assert schedule_state.run_at == datetime(2026, 5, 25, 9, 0, tzinfo=timezone.utc)
    assert schedule_state.slot == "20260525:0900"
    assert schedule_state.is_one_time is False


def test_compute_next_schedule_state_uses_schedule_timezone_for_recurring_rules():
    reference_time = datetime(2026, 1, 5, 16, 30, tzinfo=timezone.utc)

    schedule_state = compute_next_schedule_state(
        [{"days": [0], "times": ["09:00"]}],
        reference_time=reference_time,
        schedule_timezone="America/Los_Angeles",
    )

    assert schedule_state is not None
    assert schedule_state.run_at == datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc)
    assert schedule_state.slot == "20260105:1700"


def test_compute_next_schedule_state_applies_dst_in_schedule_timezone():
    reference_time = datetime(2026, 3, 8, 9, 0, tzinfo=timezone.utc)

    schedule_state = compute_next_schedule_state(
        [{"days": [6], "times": ["03:30"]}],
        reference_time=reference_time,
        schedule_timezone="America/Los_Angeles",
    )

    assert schedule_state is not None
    assert schedule_state.run_at == datetime(2026, 3, 8, 10, 30, tzinfo=timezone.utc)
    assert schedule_state.slot == "20260308:1030"


def test_automation_schema_accepts_valid_iana_schedule_timezone():
    pytest.importorskip("pydantic")
    from app.automations.schemas import AutomationCreate, AutomationUpdate

    create_payload = AutomationCreate(
        title="Paris recurring",
        prompt="Run every week",
        model_id="model-1",
        schedule_rules=[{"days": [0], "times": ["09:00"]}],
        schedule_timezone=" Europe/Paris ",
        mcp_server_ids=["notion-server", "github-server"],
    )
    update_payload = AutomationUpdate(
        automation_id="automation-1",
        schedule_timezone="Asia/Tokyo",
    )

    assert create_payload.schedule_timezone == "Europe/Paris"
    assert create_payload.mcp_server_ids == ["notion-server", "github-server"]
    assert update_payload.schedule_timezone == "Asia/Tokyo"


def test_automation_schema_rejects_removed_schedule_fields():
    pydantic = pytest.importorskip("pydantic")
    from app.automations.schemas import AutomationCreate

    with pytest.raises(pydantic.ValidationError):
        AutomationCreate(
            title="Invalid recurring schedule",
            prompt="This rule must not be silently accepted",
            model_id="model-1",
            schedule_rules=[{"days": [0], "start": "09:00"}],
        )


def test_remove_mcp_server_from_automations_cleans_every_owner(db_session):
    """Deleting a shared connector must clean all automation allowlists."""
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    db_session.add_all([
        Automation(
            id="automation-1",
            user_id="user-1",
            title="First",
            prompt="Run",
            model_id="model-1",
            mcp_server_ids=["shared-server", "other-server"],
            is_active=True,
            created_at=now,
            last_updated_at=now,
        ),
        Automation(
            id="automation-2",
            user_id="user-2",
            title="Second",
            prompt="Run",
            model_id="model-1",
            mcp_server_ids=["shared-server"],
            is_active=True,
            created_at=now,
            last_updated_at=now,
        ),
    ])
    db_session.commit()

    updated = remove_mcp_server_from_automations(db_session, "shared-server")

    assert updated == 2
    rows = db_session.query(Automation).order_by(Automation.id).all()
    assert rows[0].mcp_server_ids == ["other-server"]
    assert rows[1].mcp_server_ids == []


def test_automation_generation_uses_request_scoped_mcp_allowlist(monkeypatch):
    """Background runs must pass saved context and only the selected servers."""
    captured = {}
    model = SimpleNamespace(model_id="model-1", provider="openai")
    automation = SimpleNamespace(
        id="automation-1",
        model_id="model-1",
        mcp_server_ids=["notion-server", "github-server"],
    )
    runtime_context = SimpleNamespace(
        system_instruction_sections=[
            {"title": "Skill Instructions", "content": "Follow the saved skill."},
        ],
        note_ids=["note-1"],
    )

    monkeypatch.setattr(jobs, "get_model", lambda *_args: model)
    monkeypatch.setattr("app.chats.models.get_chat_messages", lambda *_args: [])

    def fake_call(request):
        captured["request"] = request
        return []

    monkeypatch.setattr(jobs, "call_provider_chat", fake_call)
    jobs._generate_automation_response(
        db=object(),
        chat_id="chat-1",
        automation=automation,
        user=SimpleNamespace(id="user-1"),
        runtime_context=runtime_context,
    )

    assert captured["request"].settings_override == {
        "enabled_mcp_servers": ["notion-server", "github-server"],
    }
    assert captured["request"].system_instruction_sections == runtime_context.system_instruction_sections
    assert captured["request"].note_ids == ["note-1"]


def test_automation_runtime_context_resolves_skill_notes_and_typed_files(monkeypatch):
    from app.chats import utils as chat_utils
    from app.files import access as file_access
    from app.llm.system_instruction import personality
    from app.notes import models as note_models
    from app.skills import models as skill_models

    monkeypatch.setattr(
        skill_models,
        "_resolve_accessible_skill_for_user",
        lambda *_args, **_kwargs: ("Follow the saved skill.", "user-1"),
    )
    monkeypatch.setattr(
        chat_utils,
        "get_skill_content_for_user",
        lambda *_args, **_kwargs: "Follow the saved skill.",
    )
    monkeypatch.setattr(
        chat_utils,
        "get_skill_file_descriptors_by_category_for_user",
        lambda *_args, **_kwargs: {
            "image": [],
            "video": [],
            "audio": [],
            "document": ["skill-file:skill-1:references/guide.pdf"],
        },
    )
    monkeypatch.setattr(note_models, "can_user_view_note", lambda *_args: True)
    monkeypatch.setattr(
        personality,
        "get_user_personality_system_instruction_section",
        lambda *_args: {"title": "User Personality Preferences", "content": "Be concise."},
    )
    file_categories = {
        "image-1": "image",
        "video-1": "video",
        "audio-1": "audio",
        "document-1": "document",
    }
    monkeypatch.setattr(
        file_access,
        "get_accessible_file",
        lambda _db, _user_id, file_id: SimpleNamespace(file_category=file_categories[file_id]),
    )

    context = jobs._resolve_automation_runtime_context(
        db=object(),
        automation=SimpleNamespace(
            skill_id="skill-1",
            note_ids=["note-1", "note-1"],
            file_ids=["image-1", "video-1", "audio-1", "document-1"],
        ),
        user=SimpleNamespace(id="user-1"),
        model=SimpleNamespace(settings={}),
    )

    assert context.system_instruction_sections == [
        {"title": "User Personality Preferences", "content": "Be concise."},
        {"title": "Skill Instructions", "content": "[Skill 1]\nFollow the saved skill."},
    ]
    assert context.note_ids == ["note-1"]
    assert context.image_ids == ["image-1"]
    assert context.video_ids == ["video-1"]
    assert context.audio_ids == ["audio-1"]
    assert context.document_ids == [
        "document-1",
        "skill-file:skill-1:references/guide.pdf",
    ]


def test_automation_runtime_context_rejects_revoked_note_before_generation(monkeypatch):
    from app.llm.system_instruction import personality
    from app.notes import models as note_models

    monkeypatch.setattr(
        personality,
        "get_user_personality_system_instruction_section",
        lambda *_args: None,
    )
    monkeypatch.setattr(note_models, "can_user_view_note", lambda *_args: False)

    with pytest.raises(jobs.AutomationExecutionRejected) as exc:
        jobs._resolve_automation_runtime_context(
            db=object(),
            automation=SimpleNamespace(skill_id=None, note_ids=["revoked-note"], file_ids=[]),
            user=SimpleNamespace(id="user-1"),
            model=SimpleNamespace(settings={}),
        )

    assert exc.value.status_code == 404
    assert exc.value.message == "A configured note is no longer accessible"
    assert exc.value.notify_user is True


def test_claim_due_automations_only_claims_due_rows(db_session):
    now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    due_automation = Automation(
        id="due-1",
        user_id="user-1",
        title="Due automation",
        prompt="Run now",
        model_id="model-1",
        schedule_rules=[{"days": [0], "times": ["09:59"]}],
        is_active=True,
        next_run_at=now - timedelta(minutes=1),
        next_run_slot="20260525:0959",
        created_at=now,
        last_updated_at=now,
    )
    future_automation = Automation(
        id="future-1",
        user_id="user-1",
        title="Future automation",
        prompt="Run later",
        model_id="model-1",
        schedule_rules=[{"days": [0], "times": ["11:00"]}],
        is_active=True,
        next_run_at=now + timedelta(hours=1),
        next_run_slot="20260525:1100",
        created_at=now,
        last_updated_at=now,
    )
    inactive_automation = Automation(
        id="inactive-1",
        user_id="user-1",
        title="Inactive automation",
        prompt="Do not run",
        model_id="model-1",
        schedule_rules=[{"days": [0], "times": ["09:55"]}],
        is_active=False,
        next_run_at=now - timedelta(minutes=5),
        next_run_slot="20260525:0955",
        created_at=now,
        last_updated_at=now,
    )
    db_session.add_all([due_automation, future_automation, inactive_automation])
    db_session.commit()

    claimed = claim_due_automations(
        db_session,
        due_before=now,
        batch_size=10,
        claim_timeout_seconds=300,
    )

    assert [automation.id for automation in claimed] == ["due-1"]
    assert claimed[0].scheduler_claimed_at == now.replace(tzinfo=None)


def test_complete_automation_schedule_for_slot_preserves_schedule_timezone(db_session):
    scheduled_for = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    automation = Automation(
        id="recurring-tokyo",
        user_id="user-1",
        title="Tokyo recurring automation",
        prompt="Run weekly",
        model_id="model-1",
        schedule_rules=[{"days": [0], "times": ["09:00"]}],
        schedule_timezone="Asia/Tokyo",
        is_active=True,
        next_run_at=scheduled_for,
        next_run_slot="20260105:0000",
        scheduler_claimed_at=scheduled_for,
        created_at=scheduled_for,
        last_updated_at=scheduled_for,
    )
    db_session.add(automation)
    db_session.commit()

    completed = complete_automation_schedule_for_slot(
        db_session,
        "recurring-tokyo",
        scheduled_for=scheduled_for,
        scheduled_slot="20260105:0000",
        mark_triggered=True,
    )

    refreshed = db_session.query(Automation).filter(Automation.id == "recurring-tokyo").first()

    assert completed is True
    assert refreshed is not None
    assert refreshed.is_active is True
    assert refreshed.last_triggered_at is not None
    assert refreshed.scheduler_claimed_at is None
    assert refreshed.next_run_at == datetime(2026, 1, 12, 0, 0)
    assert refreshed.next_run_slot == "20260112:0000"


def test_reserve_automation_execution_snapshots_and_deduplicates_slot(db_session):
    now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    automation = Automation(
        id="auto-1",
        user_id="user-1",
        title="Original title",
        prompt="Original prompt",
        model_id="model-1",
        schedule_rules=[{"days": [0], "times": ["10:00"]}],
        is_active=True,
        created_at=now,
        last_updated_at=now,
    )
    db_session.add(automation)
    db_session.commit()

    execution, status = reserve_automation_execution(
        db_session,
        automation_id="auto-1",
        user_id="user-1",
        scheduled_slot="20260525:1000",
        trigger_context={"type": "schedule"},
    )

    assert status == "queued"
    assert execution is not None
    assert execution.automation_title == "Original title"
    assert execution.prompt_snapshot == "Original prompt"
    assert execution.model_id_snapshot == "model-1"

    automation.title = "Edited title"
    automation.prompt = "Edited prompt"
    automation.model_id = "model-2"
    db_session.commit()

    duplicate, duplicate_status = reserve_automation_execution(
        db_session,
        automation_id="auto-1",
        user_id="user-1",
        scheduled_slot="20260525:1000",
        trigger_context={"type": "schedule"},
    )

    assert duplicate_status == "duplicate"
    assert duplicate is not None
    assert duplicate.id == execution.id
    assert duplicate.automation_title == "Original title"
    assert duplicate.prompt_snapshot == "Original prompt"
    assert duplicate.model_id_snapshot == "model-1"


def test_execute_automation_job_uses_snapshot_and_ignores_replay(db_session, monkeypatch):
    now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    automation = Automation(
        id="auto-1",
        user_id="user-1",
        title="Original title",
        prompt="Original prompt",
        model_id="model-1",
        schedule_rules=[{"days": [0], "times": ["10:00"]}],
        is_active=True,
        created_at=now,
        last_updated_at=now,
    )
    db_session.add(automation)
    db_session.commit()

    execution, status = reserve_automation_execution(
        db_session,
        automation_id="auto-1",
        user_id="user-1",
        scheduled_slot="20260525:1000",
        trigger_context={"type": "schedule"},
    )

    assert status == "queued"
    assert execution is not None

    automation.title = "Edited title"
    automation.prompt = "Edited prompt"
    automation.model_id = "model-2"
    db_session.commit()

    SessionLocal = sessionmaker(bind=db_session.get_bind())
    created_chats = []
    created_messages = []
    generated = []

    monkeypatch.setattr(jobs, "SessionLocal", SessionLocal)
    monkeypatch.setattr(jobs, "get_user", lambda db_arg, user_id: SimpleNamespace(id=user_id))
    monkeypatch.setattr("app.llm.utils.ensure_user_access_to_model", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobs, "get_model", lambda db_arg, model_id: SimpleNamespace(provider="openai", id=model_id))
    monkeypatch.setattr(jobs, "_create_automation_success_notification", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobs, "_create_automation_failure_notification", lambda *_args, **_kwargs: None)
    runtime_context = jobs.AutomationRuntimeContext(
        system_instruction_sections=[{"title": "Skill Instructions", "content": "Use the skill"}],
        note_ids=["note-1"],
        image_ids=["image-1"],
        video_ids=["video-1"],
        audio_ids=["audio-1"],
        document_ids=["document-1"],
    )
    monkeypatch.setattr(jobs, "_resolve_automation_runtime_context", lambda *_args: runtime_context)

    def fake_create_chat(user_id, db, project_id=None, meta=None):
        chat = SimpleNamespace(id=f"chat-{len(created_chats) + 1}", title=None)
        created_chats.append({"user_id": user_id, "project_id": project_id, "meta": meta, "chat": chat})
        return chat

    def fake_create_chat_message(**kwargs):
        created_messages.append(kwargs)
        return None

    def fake_generate_response(db, chat_id, execution_config, user, *, runtime_context):
        generated.append(
            {
                "chat_id": chat_id,
                "title": execution_config.title,
                "prompt": execution_config.prompt,
                "model_id": execution_config.model_id,
                "user_id": user.id,
                "note_ids": runtime_context.note_ids,
            }
        )

    monkeypatch.setattr(jobs, "create_chat", fake_create_chat)
    monkeypatch.setattr(jobs, "create_chat_message", fake_create_chat_message)
    monkeypatch.setattr(jobs, "_generate_automation_response", fake_generate_response)

    assert jobs.execute_automation_job(
        "auto-1",
        "user-1",
        scheduled_slot="20260525:1000",
        trigger_context={"type": "schedule"},
        execution_id=execution.id,
    ) is True
    assert jobs.execute_automation_job(
        "auto-1",
        "user-1",
        scheduled_slot="20260525:1000",
        trigger_context={"type": "schedule"},
        execution_id=execution.id,
    ) is True

    assert len(created_chats) == 1
    assert created_chats[0]["meta"]["automation_title"] == "Original title"
    assert created_chats[0]["meta"]["source"] == "automation"
    assert created_chats[0]["chat"].title == "Original title"
    assert len(created_messages) == 1
    assert created_messages[0]["model_id"] == "model-1"
    assert created_messages[0]["content"][0]["content"] == "Original prompt"
    assert created_messages[0]["content"][0]["images"] == ["image-1"]
    assert created_messages[0]["content"][0]["videos"] == ["video-1"]
    assert created_messages[0]["content"][0]["audios"] == ["audio-1"]
    assert created_messages[0]["content"][0]["documents"] == ["document-1"]
    assert generated == [
        {
            "chat_id": "chat-1",
            "title": "Original title",
            "prompt": "Original prompt",
            "model_id": "model-1",
            "user_id": "user-1",
            "note_ids": ["note-1"],
        }
    ]

    verification_session = SessionLocal()
    try:
        refreshed = verification_session.query(AutomationExecution).filter(AutomationExecution.id == execution.id).first()
        assert refreshed is not None
        assert refreshed.status == "completed"
        assert refreshed.chat_id == "chat-1"
    finally:
        verification_session.close()


def test_start_automation_execution_does_not_restart_failed_execution_with_chat(db_session):
    now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    execution = AutomationExecution(
        id="execution-1",
        automation_id="auto-1",
        user_id="user-1",
        scheduled_slot="20260525:1000",
        trigger_type="schedule",
        automation_title="Title",
        prompt_snapshot="Prompt",
        model_id_snapshot="model-1",
        status="failed",
        chat_id="chat-1",
        queued_at=now,
        started_at=now,
        failed_at=now,
        created_at=now,
        last_updated_at=now,
    )
    db_session.add(execution)
    db_session.commit()

    claimed = start_automation_execution(db_session, execution.id)

    assert claimed is None
    refreshed = db_session.query(AutomationExecution).filter(AutomationExecution.id == execution.id).first()
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.chat_id == "chat-1"


def test_update_automation_clears_schedule_timezone_when_rule_set_is_no_longer_recurring(db_session):
    now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    automation = Automation(
        id="auto-1",
        user_id="user-1",
        title="Recurring automation",
        prompt="Run every week",
        model_id="model-1",
        schedule_rules=[{"days": [0], "times": ["09:00"]}],
        schedule_timezone="America/New_York",
        is_active=True,
        created_at=now,
        last_updated_at=now,
    )
    db_session.add(automation)
    db_session.commit()

    updated = update_automation(
        db_session,
        "user-1",
        "auto-1",
        schedule_rules=[{"run_at": "2035-05-26T09:00:00Z"}],
        schedule_timezone=None,
    )

    assert updated is not None
    assert updated.schedule_timezone is None
    assert updated.next_run_at == datetime(2035, 5, 26, 9, 0)
    assert updated.next_run_slot == "20350526:0900"


def test_update_automation_clears_selected_skill(db_session):
    now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    automation = Automation(
        id="auto-with-skill",
        user_id="user-1",
        title="Skilled automation",
        prompt="Run with context",
        model_id="model-1",
        skill_id="skill-1",
        schedule_rules=[],
        is_active=True,
        created_at=now,
        last_updated_at=now,
    )
    db_session.add(automation)
    db_session.commit()

    updated = update_automation(
        db_session,
        "user-1",
        automation.id,
        skill_id="",
    )

    assert updated is not None
    assert updated.skill_id is None


def test_scheduler_enqueue_keeps_one_time_automation_pending_until_execution_succeeds(db_session, monkeypatch):
    due_at = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    automation = Automation(
        id="one-time-queued",
        user_id="user-1",
        title="One-time automation",
        prompt="Run once",
        model_id="model-1",
        schedule_rules=[{"run_at": "2026-05-25T10:00:00Z"}],
        is_active=True,
        next_run_at=due_at,
        next_run_slot="20260525:1000",
        created_at=due_at,
        last_updated_at=due_at,
    )
    db_session.add(automation)
    db_session.commit()

    session_factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(
        worker,
        "enqueue_scheduled_automation_execution",
        lambda *_args, **_kwargs: queue.EnqueueAutomationResult("queued"),
    )

    worker._check_and_enqueue_automations()

    verification_session = session_factory()
    try:
        refreshed = verification_session.query(Automation).filter(Automation.id == "one-time-queued").first()
        assert refreshed is not None
        assert refreshed.is_active is True
        assert refreshed.last_triggered_at is None
        assert refreshed.next_run_at == datetime(2026, 5, 25, 10, 0)
        assert refreshed.next_run_slot == "20260525:1000"
        assert refreshed.scheduler_claimed_at is not None
    finally:
        verification_session.close()


def test_scheduler_stops_current_pass_after_full_failed_enqueue_batch(db_session, monkeypatch):
    due_at = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    automations = [
        Automation(
            id=f"failed-batch-{index}",
            user_id="user-1",
            title=f"Failed batch automation {index}",
            prompt="Run once",
            model_id="model-1",
            schedule_rules=[{"run_at": "2026-05-25T10:00:00Z"}],
            is_active=True,
            next_run_at=due_at,
            next_run_slot="20260525:1000",
            created_at=due_at,
            last_updated_at=due_at,
        )
        for index in range(2)
    ]
    db_session.add_all(automations)
    db_session.commit()

    session_factory = sessionmaker(bind=db_session.get_bind())
    enqueue_calls = []
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "SCHEDULER_BATCH_SIZE", 2)

    def fail_enqueue(*args, **kwargs):
        enqueue_calls.append((args, kwargs))
        return queue.EnqueueAutomationResult("failed")

    monkeypatch.setattr(worker, "enqueue_scheduled_automation_execution", fail_enqueue)

    worker._check_and_enqueue_automations()

    assert len(enqueue_calls) == 2
    verification_session = session_factory()
    try:
        refreshed = verification_session.query(Automation).order_by(Automation.id).all()
        assert [automation.id for automation in refreshed] == ["failed-batch-0", "failed-batch-1"]
        assert all(automation.scheduler_claimed_at is None for automation in refreshed)
        assert all(automation.next_run_at == datetime(2026, 5, 25, 10, 0) for automation in refreshed)
    finally:
        verification_session.close()


def test_execute_scheduled_automation_job_deactivates_one_time_automation_after_success(db_session, monkeypatch):
    due_at = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    automation = Automation(
        id="one-time-success",
        user_id="user-1",
        title="One-time automation",
        prompt="Run once",
        model_id="model-1",
        schedule_rules=[{"run_at": "2026-05-25T10:00:00Z"}],
        is_active=True,
        next_run_at=due_at,
        next_run_slot="20260525:1000",
        scheduler_claimed_at=due_at,
        created_at=due_at,
        last_updated_at=due_at,
    )
    db_session.add(automation)
    db_session.commit()

    session_factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(jobs, "SessionLocal", session_factory)
    monkeypatch.setattr(jobs, "execute_automation_job", lambda *_args, **_kwargs: True)

    assert jobs.execute_scheduled_automation_job(
        "one-time-success",
        "user-1",
        due_at,
        "20260525:1000",
        {"type": "schedule"},
    ) is True

    verification_session = session_factory()
    try:
        refreshed = verification_session.query(Automation).filter(Automation.id == "one-time-success").first()
        assert refreshed is not None
        assert refreshed.is_active is False
        assert refreshed.last_triggered_at is not None
        assert refreshed.next_run_at is None
        assert refreshed.next_run_slot is None
        assert refreshed.scheduler_claimed_at is None
    finally:
        verification_session.close()


def test_execute_scheduled_automation_job_releases_claim_after_failure(db_session, monkeypatch):
    due_at = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    automation = Automation(
        id="one-time-failure",
        user_id="user-1",
        title="One-time automation",
        prompt="Run once",
        model_id="model-1",
        schedule_rules=[{"run_at": "2026-05-25T10:00:00Z"}],
        is_active=True,
        next_run_at=due_at,
        next_run_slot="20260525:1000",
        scheduler_claimed_at=due_at,
        created_at=due_at,
        last_updated_at=due_at,
    )
    db_session.add(automation)
    db_session.commit()

    session_factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(jobs, "SessionLocal", session_factory)
    monkeypatch.setattr(jobs, "execute_automation_job", lambda *_args, **_kwargs: False)

    assert jobs.execute_scheduled_automation_job(
        "one-time-failure",
        "user-1",
        due_at,
        "20260525:1000",
        {"type": "schedule"},
    ) is False

    verification_session = session_factory()
    try:
        refreshed = verification_session.query(Automation).filter(Automation.id == "one-time-failure").first()
        assert refreshed is not None
        assert refreshed.is_active is True
        assert refreshed.last_triggered_at is None
        assert refreshed.next_run_at == datetime(2026, 5, 25, 10, 0)
        assert refreshed.next_run_slot == "20260525:1000"
        assert refreshed.scheduler_claimed_at is None
    finally:
        verification_session.close()


def test_user_import_payload_rebuilds_scheduler_state():
    pytest.importorskip("opentelemetry")
    from app.users.utils import _rebuild_automation_payload

    payload = _rebuild_automation_payload({
        "id": "imported-automation",
        "title": "Imported",
        "prompt": "Run this later",
        "model_id": "model-1",
        "schedule_rules": [{"run_at": "2035-01-02T03:04:00Z"}],
        "is_active": True,
    })

    assert payload["next_run_at"] == datetime(2035, 1, 2, 3, 4, tzinfo=timezone.utc)
    assert payload["next_run_slot"] == "20350102:0304"


def test_user_import_payload_preserves_schedule_timezone_for_recurring_rules():
    pytest.importorskip("opentelemetry")
    from app.users.utils import _rebuild_automation_payload

    payload = _rebuild_automation_payload({
        "id": "imported-automation-recurring",
        "title": "Imported recurring",
        "prompt": "Run weekly",
        "model_id": "model-1",
        "schedule_rules": [{"days": [0], "times": ["09:00"]}],
        "schedule_timezone": "Europe/Paris",
        "is_active": True,
    })

    assert payload["schedule_timezone"] == "Europe/Paris"
    assert payload["scheduler_claimed_at"] is None


@pytest.mark.parametrize(
    ("automation_id", "source_server_ids", "restored_server_ids"),
    [
        (
            "00000000-0000-0000-0000-000000000901",
            ["source-notion", "source-github"],
            ["restored-notion", "restored-github"],
        ),
        (
            "00000000-0000-0000-0000-000000000902",
            ["source-calendar"],
            ["restored-calendar"],
        ),
    ],
)
def test_canonical_automation_roundtrip_remaps_mcp_context_into_execution(
    monkeypatch,
    automation_id,
    source_server_ids,
    restored_server_ids,
):
    """Production serialization and rebuild must feed restored IDs to runtime."""
    from app.users.data_export import _model_as_dict
    from app.users.utils import _rebuild_automation_payload

    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    source = Automation(
        id=automation_id,
        user_id="source-user",
        title="Portable MCP automation",
        prompt="Use the selected synthetic MCP tool",
        model_id="model-1",
        schedule_rules=[],
        mcp_server_ids=source_server_ids,
        is_active=True,
        created_at=now,
        last_updated_at=now,
    )
    exported = _model_as_dict(source)
    assert exported["mcp_server_ids"] == source_server_ids

    rebuilt = _rebuild_automation_payload(
        exported,
        mcp_server_id_map=dict(zip(source_server_ids, restored_server_ids)),
    )
    restored = Automation(user_id="restored-user", **rebuilt)
    assert restored.mcp_server_ids == restored_server_ids

    captured = {}
    monkeypatch.setattr(
        jobs,
        "get_model",
        lambda *_args: SimpleNamespace(model_id="model-1", provider="openai"),
    )
    monkeypatch.setattr("app.chats.models.get_chat_messages", lambda *_args: [])

    def fake_call_provider(request):
        captured["request"] = request
        return []

    monkeypatch.setattr(jobs, "call_provider_chat", fake_call_provider)

    jobs._generate_automation_response(
        db=object(),
        chat_id="restored-chat",
        automation=restored,
        user=SimpleNamespace(id="restored-user"),
        runtime_context=SimpleNamespace(
            system_instruction_sections=[],
            note_ids=[],
        ),
    )

    assert captured["request"].settings_override == {
        "enabled_mcp_servers": restored_server_ids,
    }


def test_canonical_automation_import_reports_each_inaccessible_mcp_selection(
    monkeypatch,
):
    from app.automations import models as automation_models
    from app.users.utils import _bulk_insert_automations

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.committed = True

    monkeypatch.setattr(
        automation_models,
        "_normalize_automation_mcp_server_ids",
        lambda _db, _user_id, _model_id, server_ids, **_kwargs: [
            server_id for server_id in server_ids if server_id == "restored-eligible"
        ],
    )
    db = FakeDb()

    warnings = _bulk_insert_automations(
        db,
        "restored-user",
        [
            {
                "id": "00000000-0000-0000-0000-000000000903",
                "title": "Partially portable",
                "prompt": "Use both connections",
                "model_id": "model-1",
                "mcp_server_ids": ["source-eligible", "source-inaccessible"],
            }
        ],
        mcp_server_id_map={"source-eligible": "restored-eligible"},
    )

    assert db.committed is True
    assert db.added[0].mcp_server_ids == ["restored-eligible"]
    assert warnings == [
        {
            "section": "automations",
            "code": "automation_mcp_servers_unavailable",
            "warning": (
                "Some selected MCP servers could not be restored for this automation."
            ),
            "automation_id": "00000000-0000-0000-0000-000000000903",
            "automation_title": "Partially portable",
            "inaccessible_mcp_server_ids": ["source-inaccessible"],
        }
    ]
