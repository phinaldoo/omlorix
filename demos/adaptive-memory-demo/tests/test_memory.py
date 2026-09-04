from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from memory_demo.database import Database
from memory_demo.memory import MemoryService
from memory_demo.schemas import MemoryCandidate, MemoryConsolidation


def candidate(**overrides: object) -> MemoryCandidate:
    values: dict[str, object] = {
        "action": "create",
        "target_memory_id": "",
        "key": "other.temporary_note",
        "value": "blue badge",
        "content": "The user needs a blue badge this week.",
        "kind": "other",
        "stability": "ephemeral",
        "importance": 2,
        "confidence": 0.95,
        "evidence": "I need a blue badge this week.",
        "sensitivity": "normal",
    }
    values.update(overrides)
    return MemoryCandidate.model_validate(values)


def apply(
    service: MemoryService,
    item: MemoryCandidate,
    source: str,
    now: datetime,
):
    return service.apply_consolidation(
        MemoryConsolidation(candidates=[item]),
        source_message_id="msg_test",
        source_message=source,
        now=now,
    )


def test_complete_profile_context_does_not_refresh_and_expiry_scrubs_history(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = MemoryService(database, settings)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    result = apply(service, candidate(), "I need a blue badge this week.", now)
    memory_id = result.changed_memory_ids[0]
    confirmed_at = database.list_memories()[0]["last_confirmed_at"]

    profile = database.latest_profile()
    context = json.loads(service.prompt_context(profile))
    assert "blue badge" in context["complete_user_memory"]
    assert context["profile_version"] == profile["version"]
    assert database.list_memories()[0]["last_confirmed_at"] == confirmed_at

    assert service.sweep(now=now + timedelta(days=31)) == 1
    assert database.list_memories() == []
    with database.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM memory_versions WHERE memory_id = ?", (memory_id,)
            ).fetchone()[0]
            == 0
        )
        profile_text = " ".join(
            row[0] for row in connection.execute("SELECT content FROM profile_snapshots").fetchall()
        )
    assert "blue badge" not in profile_text


def test_server_rejects_ungrounded_poisoned_and_sensitive_candidates(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = MemoryService(database, settings)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    ungrounded = apply(
        service,
        candidate(evidence="I adore blue badges."),
        "I need a blue badge this week.",
        now,
    )
    assert ungrounded.rejected_candidates == 1

    quoted = apply(
        service,
        candidate(
            key="identity.location",
            content="The user lives in Paris.",
            kind="identity",
            stability="changing",
            evidence="I live in Paris.",
        ),
        "My sister said, “I live in Paris.”",
        now,
    )
    assert quoted.rejected_candidates == 1

    secret = apply(
        service,
        candidate(
            key="other.account",
            content="The user has an account.",
            evidence="Mein Passwort ist hunter2.",
        ),
        "Mein Passwort ist hunter2.",
        now,
    )
    assert secret.rejected_candidates == 1

    wrong_predicate = apply(
        service,
        candidate(
            key="identity.location",
            value="tea",
            content="The user lives in Paris and mentioned tea.",
            kind="identity",
            stability="changing",
            evidence="I like tea.",
        ),
        "I like tea.",
        now,
    )
    assert wrong_predicate.rejected_candidates == 1

    wrong_slot_value = apply(
        service,
        candidate(
            key="identity.location",
            value="tea",
            content="The user lives in Tea.",
            kind="identity",
            stability="changing",
            evidence="I live in Paris and like tea.",
        ),
        "I live in Paris and like tea.",
        now,
    )
    assert wrong_slot_value.rejected_candidates == 1

    for value, content, evidence in (
        ("Tee", "Die Person lebt in Tee.", "Ich wohne in Berlin und ich mag Tee."),
        ("الشاي", "يعيش المستخدم في الشاي.", "أعيش في باريس وأحب الشاي."),
    ):
        rejected_slot_value = apply(
            service,
            candidate(
                key="identity.location",
                value=value,
                content=content,
                kind="identity",
                stability="changing",
                evidence=evidence,
            ),
            evidence,
            now,
        )
        assert rejected_slot_value.rejected_candidates == 1
    assert database.list_memories() == []


def test_ambiguous_forget_cannot_delete_and_manual_sensitive_edits_are_rejected(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = MemoryService(database, settings)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    created = apply(service, candidate(), "I need a blue badge this week.", now)
    memory_id = created.changed_memory_ids[0]
    ambiguous = candidate(
        action="forget",
        target_memory_id=memory_id,
        evidence="I often forget that I need a blue badge this week.",
    )
    rejected = apply(
        service,
        ambiguous,
        "I often forget that I need a blue badge this week.",
        now + timedelta(days=1),
    )
    assert rejected.rejected_candidates == 1
    assert len(database.list_memories()) == 1

    duplicate_create = apply(
        service,
        candidate(
            value="red badge",
            content="The user needs a red badge this week.",
            evidence="I need a red badge this week.",
        ),
        "I need a red badge this week.",
        now + timedelta(days=1),
    )
    assert duplicate_create.rejected_candidates == 1
    assert database.list_memories()[0]["content"] == "The user needs a blue badge this week."

    location = apply(
        service,
        candidate(
            key="identity.location",
            value="Berlin",
            content="The user lives in Berlin.",
            kind="identity",
            stability="changing",
            evidence="I live in Berlin.",
        ),
        "I live in Berlin.",
        now,
    ).changed_memory_ids[0]
    apply(
        service,
        candidate(
            key="project.current",
            value="Berlin travel guide",
            content="The user is building a Berlin travel guide.",
            kind="project",
            stability="changing",
            evidence="I am building a Berlin travel guide.",
        ),
        "I am building a Berlin travel guide.",
        now,
    )
    tied_target = apply(
        service,
        candidate(
            action="forget",
            target_memory_id=location,
            key="identity.location",
            content="",
            kind="identity",
            stability="changing",
            evidence="Forget Berlin.",
        ),
        "Forget Berlin.",
        now + timedelta(days=2),
    )
    assert tied_target.rejected_candidates == 1
    assert any(memory["id"] == location for memory in database.list_memories())

    assert not service.edit_memory(memory_id, "Mein Passwort ist hunter2")
    assert not service.edit_memory(memory_id, "Mein API-Schlüssel lautet abcdefghijklmnop")
    assert not service.edit_memory(memory_id, "La mia chiave API è abcdefghijklmnop")
    assert not service.edit_memory(memory_id, "Ich wohne in der Musterstraße 55.")
    assert not service.edit_memory(memory_id, "東京都新宿区西新宿2-8-1")
    assert not service.edit_memory(memory_id, "شارع النخيل 42")


def test_fact_limit_rejects_creations_but_allows_updates(settings) -> None:
    settings.memory_max_facts = 2
    database = Database(settings.database_path)
    database.initialize()
    service = MemoryService(database, settings)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = apply(service, candidate(), "I need a blue badge this week.", now)
    assert first.status == "updated"
    second = apply(
        service,
        candidate(
            key="other.second_note",
            value="red banner",
            content="The user also needs a red banner.",
            evidence="I also need a red banner.",
        ),
        "I also need a red banner.",
        now,
    )
    assert second.status == "updated"

    third = apply(
        service,
        candidate(
            key="other.third_note",
            value="yellow ticket",
            content="The user also needs a yellow ticket.",
            evidence="I also need a yellow ticket.",
        ),
        "I also need a yellow ticket.",
        now,
    )
    assert third.status == "unchanged"
    assert third.rejected_candidates == 1

    update = apply(
        service,
        candidate(
            action="update",
            target_memory_id=first.changed_memory_ids[0],
            value="green badge",
            content="The user needs a green badge this week.",
            evidence="I need a green badge this week.",
        ),
        "I need a green badge this week.",
        now,
    )
    assert update.status == "updated"
    assert len(database.list_memories()) == settings.memory_max_facts

    profile = database.latest_profile()
    context = json.loads(service.prompt_context(profile))
    assert context["complete_user_memory"] == profile["content"]
    assert "green badge" in profile["content"]
    assert "red banner" in profile["content"]
    assert "yellow ticket" not in profile["content"]


def test_manual_edit_clears_message_provenance_and_elevates_sensitivity(settings) -> None:
    settings.memory_allow_sensitive = True
    database = Database(settings.database_path)
    database.initialize()
    service = MemoryService(database, settings)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    memory_id = apply(
        service,
        candidate(
            key="identity.location",
            value="Berlin",
            content="The user lives in Berlin.",
            kind="identity",
            stability="changing",
            evidence="I live in Berlin.",
        ),
        "I live in Berlin.",
        now,
    ).changed_memory_ids[0]

    assert service.edit_memory(
        memory_id,
        "The user lives at 55 Main Street.",
        now=now + timedelta(days=1),
    )
    memory = database.list_memories()[0]
    assert memory["source_message_id"] is None
    assert memory["sensitivity"] == "sensitive"
    with database.connection() as connection:
        profile = connection.execute(
            "SELECT derived_from_memory_ids FROM profile_snapshots ORDER BY version DESC LIMIT 1"
        ).fetchone()
    assert json.loads(profile[0]) == [{"memory_id": memory_id, "version": 2}]
