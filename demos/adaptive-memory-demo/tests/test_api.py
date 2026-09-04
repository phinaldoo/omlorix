from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError

from memory_demo.config import PROJECT_ROOT
from memory_demo.llm import GatewayResult, Usage
from memory_demo.main import create_app


def test_memory_consolidation_runs_for_questions_and_extracts_embedded_facts(
    client: TestClient,
) -> None:
    state = client.get("/api/state").json()
    result = client.post(
        "/api/chat",
        json={
            "message": "My name is Maya. Which city should I visit?",
            "conversation_id": state["conversation_id"],
            "locale": "en",
        },
    ).json()

    assert result["memory_status"] == "updated"
    assert any(
        memory["key"] == "identity.name" and "Maya" in memory["content"]
        for memory in result["state"]["memories"]
    )
    source_message_id = result["state"]["messages"][0]["id"]
    with client.app.state.database.connection() as connection:
        operations = {
            row[0]
            for row in connection.execute(
                "SELECT operation FROM usage_events WHERE source_message_id = ?",
                (source_message_id,),
            )
        }
    assert operations == {"chat", "memory_consolidation"}


def test_chat_updates_includes_complete_profile_and_forgets_memory(client: TestClient) -> None:
    initial = client.get("/api/state").json()
    assert initial["runtime"]["mode"] == "simulation"
    assert initial["runtime"]["memory_model"] == "local-simulator"
    assert initial["memories"] == []

    first = client.post(
        "/api/chat",
        json={
            "message": "My name is Maya, and I live in Berlin.",
            "conversation_id": initial["conversation_id"],
            "locale": "en",
        },
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["memory_status"] == "updated"
    assert first_body["memory_profile_version"] is None
    assert [message["role"] for message in first_body["state"]["messages"]] == [
        "user",
        "assistant",
    ]
    assert {memory["key"] for memory in first_body["state"]["memories"]} == {
        "identity.name",
        "identity.location",
    }

    moved = client.post(
        "/api/chat",
        json={
            "message": "I moved from Berlin to Hamburg last month.",
            "conversation_id": initial["conversation_id"],
            "locale": "en",
        },
    ).json()
    location = next(
        memory for memory in moved["state"]["memories"] if memory["key"] == "identity.location"
    )
    assert location["content"] == "The user lives in Hamburg."
    assert location["version"] == 2
    assert "Berlin" not in moved["state"]["messages"][-1]["content"]

    recalled = client.post(
        "/api/chat",
        json={
            "message": "Which city do I call home?",
            "conversation_id": initial["conversation_id"],
            "locale": "en",
        },
    ).json()
    assert recalled["memory_profile_version"] == moved["state"]["profile"]["version"]
    assert "Maya" in recalled["state"]["messages"][-1]["content"]
    assert "Hamburg" in recalled["state"]["messages"][-1]["content"]

    forgotten = client.post(
        "/api/chat",
        json={
            "message": "Forget where I live.",
            "conversation_id": initial["conversation_id"],
            "locale": "en",
        },
    ).json()
    assert all(memory["key"] != "identity.location" for memory in forgotten["state"]["memories"])
    assert "Hamburg" not in forgotten["state"]["profile"]["content"]
    assert forgotten["memory_profile_version"] is None
    assert "Hamburg" not in forgotten["state"]["messages"][-1]["content"]
    assert any(event["action"] == "forgotten" for event in forgotten["state"]["events"])

    database = client.app.state.database
    with database.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM memory_versions WHERE memory_id = ?", (location["id"],)
            ).fetchone()[0]
            == 0
        )
        profile_payload = " ".join(
            row[0] for row in connection.execute("SELECT content FROM profile_snapshots").fetchall()
        )
        assert "Hamburg" not in profile_payload


def test_new_chat_starts_empty_and_preserves_long_term_memory(client: TestClient) -> None:
    initial = client.get("/api/state").json()
    first = client.post(
        "/api/chat",
        json={
            "message": "My name is Maya.",
            "conversation_id": initial["conversation_id"],
            "locale": "en",
        },
    ).json()
    assert first["state"]["messages"]
    assert first["state"]["memories"]

    response = client.post("/api/conversations", json={})
    assert response.status_code == 200
    created = response.json()
    assert created["status"] == "created"
    assert created["state"]["conversation_id"] != initial["conversation_id"]
    assert created["state"]["messages"] == []
    assert created["state"]["memories"] == first["state"]["memories"]
    assert client.app.state.database.list_messages(initial["conversation_id"])


def test_connection_failure_is_reported_without_losing_the_chat_reply(settings) -> None:
    class ConnectionFailureGateway:
        async def consolidate(self, **_kwargs):
            raise APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com/v1/responses")
            )

        async def chat(self, **_kwargs):
            return GatewayResult(value="The chat request succeeded.", usage=Usage(model="test"))

    with TestClient(create_app(settings=settings, gateway=ConnectionFailureGateway())) as client:
        initial = client.get("/api/state").json()
        response = client.post(
            "/api/chat",
            json={
                "message": "I prefer concise answers.",
                "conversation_id": initial["conversation_id"],
                "locale": "en",
            },
        )

    assert response.status_code == 200
    result = response.json()
    assert result["memory_status"] == "failed"
    assert result["memory_error"] == "connection"
    assert result["state"]["messages"][-1]["content"] == "The chat request succeeded."


def test_simulated_clock_persists_and_manual_confirmation_uses_it(
    client: TestClient,
) -> None:
    state = client.get("/api/state").json()
    created = client.post(
        "/api/chat",
        json={
            "message": "My name is Maya.",
            "conversation_id": state["conversation_id"],
            "locale": "en",
        },
    ).json()
    memory = created["state"]["memories"][0]

    advanced = client.post("/api/lifecycle/sweep", json={"advance_days": 400}).json()
    assert advanced["state"]["runtime"]["clock_offset_days"] == 400
    reviewed = advanced["state"]["memories"][0]
    assert reviewed["lifecycle_state"] == "review"

    confirmed = client.post(f"/api/memories/{memory['id']}/confirm").json()
    refreshed = confirmed["state"]["memories"][0]
    assert refreshed["lifecycle_state"] == "fresh"
    assert refreshed["last_confirmed_at"] > memory["last_confirmed_at"]
    assert client.app.state.database.clock_offset_days() == 400


def test_export_contains_histories_and_storage_has_no_vector_columns(client: TestClient) -> None:
    state = client.get("/api/state").json()
    client.post(
        "/api/chat",
        json={
            "message": "I'm building a solar-powered garden sensor.",
            "conversation_id": state["conversation_id"],
            "locale": "en",
        },
    )

    response = client.get("/api/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    bundle = response.json()
    assert bundle["memory_versions"]
    assert bundle["profile_snapshots"]
    assert bundle["usage"]
    with client.app.state.database.connection() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memories)")}
    assert "embedding" not in columns
    assert "embedding_model" not in columns


def test_multilingual_simulation_extracts_includes_and_forgets(client: TestClient) -> None:
    state = client.get("/api/state").json()
    created = client.post(
        "/api/chat",
        json={
            "message": "Ich heiße Lena. Ich wohne in Köln.",
            "conversation_id": state["conversation_id"],
            "locale": "de",
        },
    ).json()
    memories = created["state"]["memories"]
    location = next(memory for memory in memories if memory["key"] == "identity.location")
    assert location["content"] == "Die Person lebt in Köln."
    assert any(memory["content"] == "Die Person heißt Lena." for memory in memories)

    recalled = client.post(
        "/api/chat",
        json={
            "message": "Wo wohne ich?",
            "conversation_id": state["conversation_id"],
            "locale": "de",
        },
    ).json()
    assert recalled["memory_profile_version"] == created["state"]["profile"]["version"]
    assert "Lena" in recalled["state"]["messages"][-1]["content"]
    assert "Köln" in recalled["state"]["messages"][-1]["content"]

    forgotten = client.post(
        "/api/chat",
        json={
            "message": "Bitte vergiss, wo ich wohne.",
            "conversation_id": state["conversation_id"],
            "locale": "de",
        },
    ).json()
    assert all(memory["key"] != "identity.location" for memory in forgotten["state"]["memories"])


def test_translated_identity_and_forget_samples_work_in_every_locale(
    client: TestClient,
) -> None:
    for path in sorted((PROJECT_ROOT / "static" / "i18n").glob("*.json")):
        translations = json.loads(path.read_text(encoding="utf-8"))
        state = client.post("/api/reset", json={}).json()["state"]
        created = client.post(
            "/api/chat",
            json={
                "message": translations["sample_identity"],
                "conversation_id": state["conversation_id"],
                "locale": path.stem,
            },
        ).json()
        assert any(
            memory["key"] == "identity.location" for memory in created["state"]["memories"]
        ), path.stem

        forgotten = client.post(
            "/api/chat",
            json={
                "message": translations["sample_forget"],
                "conversation_id": state["conversation_id"],
                "locale": path.stem,
            },
        ).json()
        assert all(
            memory["key"] != "identity.location" for memory in forgotten["state"]["memories"]
        ), path.stem

    for locale, forget_message in (
        ("zh", "请忘记我住在哪里。"),
        ("ar", "من فضلك انس مدينتي."),
    ):
        translations = json.loads(
            (PROJECT_ROOT / "static" / "i18n" / f"{locale}.json").read_text(encoding="utf-8")
        )
        state = client.post("/api/reset", json={}).json()["state"]
        client.post(
            "/api/chat",
            json={
                "message": translations["sample_identity"],
                "conversation_id": state["conversation_id"],
                "locale": locale,
            },
        )
        forgotten = client.post(
            "/api/chat",
            json={
                "message": forget_message,
                "conversation_id": state["conversation_id"],
                "locale": locale,
            },
        ).json()
        assert all(
            memory["key"] != "identity.location" for memory in forgotten["state"]["memories"]
        ), locale
