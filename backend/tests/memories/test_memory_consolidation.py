from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.groups.models import Group
from app.memories import consolidation as memory_consolidation
from app.memories.models import Memory, MemoryDeletion, MemoryProfile, MemoryState
from app.memories.schemas import MAX_MEMORIES_PER_SCOPE, MemoryCandidate
from app.memories.service import (
    MemoryScope,
    apply_memory_consolidation,
    get_memory_profile,
    list_memories,
    sweep_expired_memories,
)
from app.projects.models import Project
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import User


def _session(engine=None):
    engine = engine if engine is not None else create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Group.__table__,
            User.__table__,
            Project.__table__,
            Memory.__table__,
            MemoryDeletion.__table__,
            MemoryProfile.__table__,
            MemoryState.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    db.add(
        Group(
            id="group-1",
            name="Group",
            kind="standard",
            settings={"memories": {"enabled_memories": True}},
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        User(
            id="user-1",
            email="memory@example.com",
            group_id="group-1",
            account_type="regular",
            hashed_password="hash",
            first_name="Memory",
            last_name="User",
            role="user",
            settings=deepcopy(DEFAULT_USER_SETTINGS),
            is_active=True,
            created_at=now,
            last_active_at=now,
        )
    )
    db.commit()
    return db


def _candidate(
    *,
    action: str = "create",
    key: str = "preference.answer_length",
    content: str = "The user prefers concise answers.",
    target_memory_id: str = "",
    importance: int = 4,
    sensitivity: str = "normal",
) -> MemoryCandidate:
    return MemoryCandidate(
        action=action,
        target_memory_id=target_memory_id,
        key=key,
        content=content,
        kind="preference",
        stability="slow",
        importance=importance,
        confidence=0.95,
        evidence="I prefer concise answers",
        sensitivity=sensitivity,
    )


def test_processes_each_message_into_atomic_facts_and_full_profile(monkeypatch):
    db = _session()
    monkeypatch.setattr(
        memory_consolidation,
        "get_user_group_setting_value",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        memory_consolidation,
        "_resolve_memory_model",
        lambda *_args, **_kwargs: ("openai", "memory-model", None, None),
    )
    provider_calls = []

    def provider_call(request):
        provider_calls.append(request)
        return json.dumps({"candidates": [_candidate().model_dump()]})

    monkeypatch.setattr(
        memory_consolidation,
        "call_provider_memory_consolidation",
        provider_call,
    )
    monkeypatch.setattr(
        "app.logging.models.stage_audit_log_event",
        lambda *_args, **_kwargs: None,
    )
    source_at = datetime.now(timezone.utc)

    result = memory_consolidation.process_memory_consolidation(
        db,
        user_id="user-1",
        source_message_id="message-1",
        source_at=source_at,
        source_text="I prefer concise answers",
        current_model_id="chat-model",
    )

    assert len(provider_calls) == 1
    candidate_schema = provider_calls[0].extra["response_schema"]["properties"][
        "candidates"
    ]["items"]
    assert "value" not in candidate_schema["properties"]
    assert candidate_schema["additionalProperties"] is False
    assert result["created_count"] == 1
    facts = list_memories(db, MemoryScope.personal("user-1"))
    assert [fact.memory_key for fact in facts] == ["preference.answer_length"]
    profile = db.query(MemoryProfile).filter_by(user_id="user-1").one()
    assert "The user prefers concise answers." in profile.content
    assert profile.active_fact_count == 1
    assert db.get(MemoryState, "user-1").last_processed_message_id == "message-1"
    assert db.get(MemoryState, "user-1").last_run_status == "updated"


def test_group_selected_memory_model_resolves_to_a_concrete_provider(monkeypatch):
    from app.llm import provider_groups

    selected_model = SimpleNamespace(
        id="memory-model-id",
        model_name="memory-model",
        provider="openai",
        provider_id="provider-group",
        capabilities={"completion": True},
    )
    database_session = MagicMock()
    database_session.query.return_value.filter.return_value.first.return_value = (
        selected_model
    )
    monkeypatch.setattr(
        memory_consolidation,
        "get_user_group_setting_value",
        lambda *_args, **_kwargs: "memory-model-id",
    )
    monkeypatch.setattr(
        provider_groups,
        "resolve_provider_for_request",
        lambda *_args, **_kwargs: SimpleNamespace(id="provider-1", provider="openai"),
    )

    provider, model, byok, provider_id = memory_consolidation._resolve_memory_model(
        database_session,
        user_id="user-1",
        current_model_id="chat-model-id",
        byok=None,
    )

    assert provider == "openai"
    assert model is selected_model
    assert byok is None
    assert provider_id == "provider-1"


def test_schema_rejection_falls_back_once_and_is_cached(monkeypatch):
    db = _session()
    model_name = "schema-unsupported-memory-model"
    cache_key = ("openai", "byok", model_name)
    with memory_consolidation._schema_capability_lock:
        memory_consolidation._schema_unsupported_models.discard(cache_key)

    monkeypatch.setattr(
        memory_consolidation,
        "get_user_group_setting_value",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        memory_consolidation,
        "_resolve_memory_model",
        lambda *_args, **_kwargs: ("openai", model_name, None, None),
    )
    schema_attempts: list[bool] = []

    def provider_call(request):
        has_schema = request.extra.get("response_schema") is not None
        schema_attempts.append(has_schema)
        if has_schema:
            raise HTTPException(
                status_code=400,
                detail="json_schema is not supported by this model",
            )
        return json.dumps({"candidates": []})

    monkeypatch.setattr(
        memory_consolidation,
        "call_provider_memory_consolidation",
        provider_call,
    )
    monkeypatch.setattr(
        "app.logging.models.stage_audit_log_event",
        lambda *_args, **_kwargs: None,
    )

    for index in (1, 2):
        result = memory_consolidation.process_memory_consolidation(
            db,
            user_id="user-1",
            source_message_id=f"message-{index}",
            source_at=datetime.now(timezone.utc) + timedelta(seconds=index),
            source_text=f"Message {index} contains no reusable personal information.",
            current_model_id="chat-model",
        )
        assert result["status"] == "unchanged"

    assert schema_attempts == [True, False, False]


def test_schema_rejection_recognizes_wrapped_provider_response():
    provider_error = RuntimeError("provider rejected request")
    provider_error.response = SimpleNamespace(
        status_code=400,
        text="The response_format json_schema is unsupported for this model",
    )
    wrapped_error = HTTPException(
        status_code=424,
        detail="Upstream provider request failed",
    )
    wrapped_error.__cause__ = provider_error

    assert memory_consolidation._is_schema_rejection(wrapped_error) is True


def test_older_background_turn_cannot_overwrite_newer_evidence(monkeypatch):
    db = _session()
    monkeypatch.setattr(
        "app.logging.models.stage_audit_log_event",
        lambda *_args, **_kwargs: None,
    )
    newer = datetime.now(timezone.utc)
    apply_memory_consolidation(
        db,
        user_id="user-1",
        source_message_id="message-new",
        source_at=newer,
        source_text="I prefer detailed answers",
        candidates=[
            _candidate(content="The user prefers detailed answers.")
        ],
    )
    result = apply_memory_consolidation(
        db,
        user_id="user-1",
        source_message_id="message-old",
        source_at=newer - timedelta(minutes=5),
        source_text="I prefer concise answers",
        candidates=[_candidate(action="update")],
    )

    fact = list_memories(db, MemoryScope.personal("user-1"))[0]
    profile = db.query(MemoryProfile).filter_by(user_id="user-1").one()
    assert fact.content == "The user prefers detailed answers."
    assert result["stale_count"] == 1
    assert db.get(MemoryState, "user-1").last_processed_message_id == "message-new"


def test_secret_candidates_are_never_stored(monkeypatch):
    db = _session()
    monkeypatch.setattr(
        "app.logging.models.stage_audit_log_event",
        lambda *_args, **_kwargs: None,
    )
    result = apply_memory_consolidation(
        db,
        user_id="user-1",
        source_message_id="message-1",
        source_at=datetime.now(timezone.utc),
        source_text="My API key is secret",
        candidates=[
            _candidate(
                key="identity.api_key",
                content="The user's API key is secret.",
                sensitivity="secret",
            )
        ],
    )

    assert result["skipped_count"] == 1
    assert list_memories(db, MemoryScope.personal("user-1")) == []


def test_misclassified_api_key_is_rejected_by_the_persistence_boundary(monkeypatch):
    db = _session()
    monkeypatch.setattr(
        "app.logging.models.stage_audit_log_event",
        lambda *_args, **_kwargs: None,
    )
    candidate = _candidate(
        key="identity.api_key",
        content="The user's API key is sk-proj-examplecredential123456.",
        sensitivity="normal",
    )
    candidate.evidence = "My API key is sk-proj-examplecredential123456"

    result = apply_memory_consolidation(
        db,
        user_id="user-1",
        source_message_id="message-1",
        source_at=datetime.now(timezone.utc),
        source_text="My API key is sk-proj-examplecredential123456",
        candidates=[candidate],
    )

    assert result["skipped_count"] == 1
    assert list_memories(db, MemoryScope.personal("user-1")) == []


def test_secret_retraction_can_still_remove_an_existing_fact(monkeypatch):
    db = _session()
    monkeypatch.setattr(
        "app.logging.models.stage_audit_log_event",
        lambda *_args, **_kwargs: None,
    )
    created = apply_memory_consolidation(
        db,
        user_id="user-1",
        source_message_id="message-1",
        source_at=datetime.now(timezone.utc),
        source_text="I prefer concise answers",
        candidates=[_candidate()],
    )
    assert created["created_count"] == 1
    fact = list_memories(db, MemoryScope.personal("user-1"))[0]

    forgotten = apply_memory_consolidation(
        db,
        user_id="user-1",
        source_message_id="message-2",
        source_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        source_text="Forget that; my API key is sk-proj-examplecredential123456",
        candidates=[
            _candidate(
                action="forget",
                target_memory_id=str(fact.id),
                sensitivity="secret",
            )
        ],
    )

    assert forgotten["deleted_count"] == 1
    assert list_memories(db, MemoryScope.personal("user-1")) == []


def test_expired_facts_disappear_immediately_and_lifecycle_sweep_repairs_profile(
    monkeypatch,
):
    db = _session()
    monkeypatch.setattr(
        "app.logging.models.stage_audit_log_event",
        lambda *_args, **_kwargs: None,
    )
    apply_memory_consolidation(
        db,
        user_id="user-1",
        source_message_id="message-1",
        source_at=datetime.now(timezone.utc),
        source_text="I prefer concise answers",
        candidates=[_candidate()],
    )
    fact = db.query(Memory).filter_by(user_id="user-1").one()
    fact.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert list_memories(db, MemoryScope.personal("user-1")) == []
    assert get_memory_profile(db, "user-1").active_fact_count == 0
    assert sweep_expired_memories(db, user_id="user-1") == 1
    assert db.query(Memory).filter_by(user_id="user-1").count() == 0
    profile = db.query(MemoryProfile).filter_by(user_id="user-1").one()
    assert profile.content == ""
    assert profile.active_fact_count == 0


def test_dense_model_output_is_bounded_by_the_100_fact_contract():
    payload = {
        "candidates": [
            _candidate(
                key=f"preference.item_{index}",
                content=f"The user prefers item {index}.",
            ).model_dump()
            for index in range(MAX_MEMORIES_PER_SCOPE)
        ]
    }

    parsed = memory_consolidation.parse_memory_consolidation_output(json.dumps(payload))

    assert len(parsed.candidates) == MAX_MEMORIES_PER_SCOPE


def test_disabled_group_is_not_enqueued_or_submitted(monkeypatch):
    monkeypatch.setattr(
        memory_consolidation,
        "get_user_group_setting_value",
        lambda *_args, **_kwargs: False,
    )

    scheduled = memory_consolidation.schedule_memory_consolidation(
        user_id="user-1",
        source_message_id="message-1",
        source_at=datetime.now(timezone.utc),
        source_text="I prefer concise answers",
        current_model_id="chat-model",
        db=object(),
    )

    assert scheduled is False


def test_enabled_group_uses_durable_memory_job_in_external_mode(monkeypatch):
    from app.workers import memory as memory_worker

    database_session = object()
    queue_session = MagicMock()
    monkeypatch.setattr(memory_consolidation, "SessionLocal", lambda: queue_session)
    queue_session.__enter__.return_value = queue_session
    queued = []
    monkeypatch.setattr(
        memory_consolidation,
        "get_user_group_setting_value",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        memory_worker,
        "external_memory_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        memory_worker,
        "enqueue_memory_consolidation_job",
        lambda db, **payload: queued.append((db, payload)),
    )
    oversized_message = "start " + ("x" * 80_000) + " end"

    scheduled = memory_consolidation.schedule_memory_consolidation(
        user_id="user-1",
        source_message_id="message-1",
        source_at=datetime.now(timezone.utc),
        source_text=oversized_message,
        current_model_id="chat-model",
        db=database_session,
    )

    assert scheduled is True
    assert queued[0][0] is queue_session
    queue_session.__exit__.assert_called_once()
    payload = queued[0][1]
    assert payload["source_message_id"] == "message-1"
    assert payload["current_model_id"] == "chat-model"
    assert len(payload["source_text"]) <= memory_consolidation.MAX_MEMORY_SOURCE_CHARS
    assert payload["source_text"].startswith("start ")
    assert payload["source_text"].endswith(" end")


@pytest.mark.parametrize("manual", [False, True])
def test_deletion_blocks_stale_keys_and_target_ids_but_allows_new_evidence(monkeypatch, manual):
    from app.memories import service

    db = _session()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    monkeypatch.setattr("app.logging.models.stage_audit_log_event", lambda *a, **k: None)

    def apply(message_id, at, candidates):
        return apply_memory_consolidation(
            db, user_id="user-1", source_message_id=message_id,
            source_at=at, source_text="I prefer concise answers", candidates=candidates,
        )

    apply("initial", now - timedelta(minutes=3), [_candidate()])
    fact = db.query(Memory).one()
    fact_id = fact.id
    if manual:
        service.delete_memory(db, MemoryScope.personal("user-1"), fact_id)
    else:
        apply("forget", now, [_candidate(action="forget", target_memory_id=fact_id)])
    guard = db.query(MemoryDeletion).one()
    assert guard.memory_id == fact_id
    assert guard.version == 2
    assert guard.memory_key == "preference.answer_length"
    assert set(MemoryDeletion.__table__.columns.keys()) == {
        "memory_id", "user_id", "project_id", "memory_key", "version", "deleted_at",
    }
    assert service.export_memories(db, MemoryScope.personal("user-1"))["data"]["memories"] == []
    assert db.query(MemoryProfile).one().content == ""

    # Includes equal-timestamp retries, and an old target ID with a renamed key.
    for at in (now - timedelta(minutes=1), now):
        result = apply("late", at, [_candidate()])
        assert result["stale_count"] == 1
        result = apply("renamed", at, [
            _candidate(action="update", target_memory_id=fact_id, key="preference.other"),
        ])
        assert result["stale_count"] == 1
        assert db.query(Memory).count() == 0

    result = apply("new", now + timedelta(seconds=1), [_candidate()])
    assert result["created_count"] == 1
    assert db.query(Memory).one().id != fact_id
    # A newer recreation must not remove the guard needed for delayed jobs.
    assert db.query(MemoryDeletion).count() == 1


def test_expired_jobs_cannot_recreate_facts_after_guard_cleanup(monkeypatch):
    from app.memories import service

    db = _session()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("app.logging.models.stage_audit_log_event", lambda *a, **k: None)
    fact, _ = service.create_memory(db, MemoryScope.personal("user-1"), "Private fact")
    service.delete_memory(db, MemoryScope.personal("user-1"), fact.id)
    assert service.sweep_memory_deletions(db) == 0
    monkeypatch.setattr(service, "utcnow", lambda: now + timedelta(days=3))
    assert service.sweep_memory_deletions(db) == 1
    result = apply_memory_consolidation(
        db, user_id="user-1", source_message_id="delayed", source_at=now,
        source_text="I prefer concise answers", candidates=[_candidate()],
    )
    assert result == {"status": "skipped", "reason": "source_expired"}
    assert db.query(Memory).count() == 0


def test_project_deletion_does_not_block_personal_memory_with_same_key(monkeypatch):
    from app.memories import service

    db = _session()
    now = datetime.now(timezone.utc)
    db.add(Project(id="project-1", user_id="user-1", title="Project", created_at=now, last_updated_at=now))
    db.commit()
    project_scope = MemoryScope.project("project-1")
    fact, _ = service.create_memory(
        db, project_scope, "Project preference", memory_key="preference.answer_length",
    )
    service.delete_memory(db, project_scope, fact.id)
    monkeypatch.setattr("app.logging.models.stage_audit_log_event", lambda *a, **k: None)
    result = apply_memory_consolidation(
        db, user_id="user-1", source_message_id="personal", source_at=now,
        source_text="I prefer concise answers", candidates=[_candidate()],
    )
    assert result["created_count"] == 1
    assert db.query(MemoryDeletion).one().project_id == "project-1"


def test_forget_can_arrive_before_the_fact_creation_job(monkeypatch):
    db = _session()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("app.logging.models.stage_audit_log_event", lambda *a, **k: None)
    for action, at in (("forget", now), ("create", now - timedelta(minutes=1))):
        result = apply_memory_consolidation(
            db, user_id="user-1", source_message_id=action, source_at=at,
            source_text="I prefer concise answers", candidates=[_candidate(action=action)],
        )
    assert result["stale_count"] == 1
    assert db.query(Memory).count() == 0
    assert db.query(MemoryDeletion).count() == 1


def test_provider_call_cannot_apply_a_source_that_expires_while_running(monkeypatch):
    from app.memories import service

    db = _session()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(memory_consolidation, "get_user_group_setting_value", lambda *a, **k: True)
    monkeypatch.setattr(memory_consolidation, "_resolve_memory_model", lambda *a, **k: ("openai", "model", None, None))

    def slow_provider(**kwargs):
        monkeypatch.setattr(service, "utcnow", lambda: now + timedelta(hours=2))
        return json.dumps({"candidates": [_candidate().model_dump()]})

    monkeypatch.setattr(memory_consolidation, "_call_memory_model", slow_provider)
    result = memory_consolidation.process_memory_consolidation(
        db, user_id="user-1", source_message_id="slow", source_at=now - timedelta(hours=23),
        source_text="I prefer concise answers", current_model_id="model",
    )
    assert result == {"status": "skipped", "reason": "source_expired"}
    assert db.query(Memory).count() == 0
    assert db.query(MemoryState).one().last_run_status == "unchanged"


def test_failed_first_run_cannot_materialize_an_empty_legacy_profile(monkeypatch):
    from app.llm.system_instruction import memories as context
    from app.memories.service import set_memory_run_status, rebuild_memory_profile
    from app.memories.runtime import MemoryPolicy
    db = _session()
    now = datetime.now(timezone.utc)
    db.add(Memory(id="legacy", user_id="user-1", content="Preserve this existing fact", content_key="legacy"))
    db.commit()
    monkeypatch.setattr(context, "get_memory_policy", lambda *_args, **_kwargs: MemoryPolicy("user-1", None, True, False))
    set_memory_run_status(db, "user-1", source_message_id="failed", source_at=now, run_status="failed", commit=True)
    assert db.query(MemoryProfile).count() == 0
    assert "Preserve this existing fact" in context.get_memories_context(db, "user-1")
    profile = rebuild_memory_profile(db, "user-1")
    db.commit()
    assert profile.source_revision == db.get(MemoryState, "user-1").facts_revision
    profile.source_revision -= 1
    profile.content = "outdated projection"
    db.commit()
    assert "Preserve this existing fact" in context.get_memories_context(db, "user-1")


@pytest.mark.parametrize("commit", [False, True])
def test_source_message_and_durable_memory_job_share_one_transaction(monkeypatch, commit):
    from app.chats.models import ChatMessages
    from app.workers.models import DurableWorkerJob
    db = _session()
    Base.metadata.create_all(db.get_bind(), tables=[ChatMessages.__table__, DurableWorkerJob.__table__])
    monkeypatch.setattr(memory_consolidation, "get_user_group_setting_value", lambda *_args: True)
    now = datetime.now(timezone.utc)
    db.add(ChatMessages(id="source", chat_id="chat-1", model_id="model-1", role="user", content=json.dumps([{"type": "user", "content": "Remember this"}]), created_at=now))
    assert memory_consolidation.stage_memory_consolidation(db, user_id="user-1", source_message_id="source", source_at=now, source_text="Remember this")
    if commit:
        db.commit()
    else:
        db.rollback()
    assert db.query(ChatMessages).count() == int(commit)
    assert db.query(DurableWorkerJob).count() == int(commit)
    if commit:
        assert memory_consolidation.stage_memory_consolidation(db, user_id="user-1", source_message_id="source", source_at=now, source_text="Retry")
        db.commit()
        assert db.query(DurableWorkerJob).count() == 1
        assert db.query(DurableWorkerJob).one().payload["source_text"] == "Remember this"
