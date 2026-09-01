import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard


if "app.llmstats.models" not in sys.modules:
    fake_llmstats_models = ModuleType("app.llmstats.models")
    fake_llmstats_models.get_model_cached_tokens_per_second = lambda meta: None
    fake_llmstats_models.get_model_cached_tokens_per_second_sample_count = lambda meta: 0
    fake_llmstats_models.get_model_performance_meta = lambda meta: {}
    fake_llmstats_models.__getattr__ = lambda _name: type(_name, (), {})
    sys.modules["app.llmstats.models"] = fake_llmstats_models

from app.agents import utils as agents_utils


def _agent(**overrides):
    values = {
        "id": "agent-1",
        "user_id": "owner-1",
        "name": "Shared agent",
        "icon": "bot",
        "base_model_id": "model-1",
        "instruction": "Keep answers short",
        "skill_id": None,
        "clone_share_id": "clone-token",
        "live_share_id": "live-token",
        "collaborate_share_id": "collaborate-token",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _base_model():
    return SimpleNamespace(
        settings={"training_data": False},
        meta={},
        model_icon="sparkles",
        provider="openai",
        provider_id="openai",
        model_name="gpt-test",
        capabilities=["chat"],
        status="active",
        tools=[],
    )


def _subscription(**overrides):
    values = {
        "share_type": "live",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_serialize_agent_keeps_share_ids_for_owner(monkeypatch):
    monkeypatch.setattr(agents_utils, "_owner_display_name", lambda db, user_id: "Owner")
    monkeypatch.setattr(agents_utils, "_build_agent_description", lambda agent, base_model: "Description")
    monkeypatch.setattr(agents_utils, "list_user_agent_assets", lambda db, agent_id: [])

    response = agents_utils._serialize_agent(
        SimpleNamespace(),
        user_id="owner-1",
        agent=_agent(),
        base_model=_base_model(),
    )

    assert response["clone_share_id"] == "clone-token"
    assert response["live_share_id"] == "live-token"
    assert response["collaborate_share_id"] == "collaborate-token"
    assert response["share_type"] is None
    assert "can_edit" not in response


def test_serialize_agent_redacts_share_ids_for_subscribers(monkeypatch):
    monkeypatch.setattr(agents_utils, "_owner_display_name", lambda db, user_id: "Owner")
    monkeypatch.setattr(agents_utils, "_build_agent_description", lambda agent, base_model: "Description")
    monkeypatch.setattr(agents_utils, "list_user_agent_assets", lambda db, agent_id: [])

    response = agents_utils._serialize_agent(
        SimpleNamespace(),
        user_id="viewer-1",
        agent=_agent(),
        base_model=_base_model(),
        subscription=_subscription(share_type="collaborate"),
    )

    assert response["clone_share_id"] is None
    assert response["live_share_id"] is None
    assert response["collaborate_share_id"] is None
    assert response["share_type"] == "collaborate"
    assert "can_edit" not in response
