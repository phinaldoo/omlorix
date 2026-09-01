import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.llm import leaderboard
from app.llm.schemas import LLMLeaderboardModel


class _Response:
    """Small requests.Response stand-in for deterministic provider tests."""

    def __init__(self, payload=None, *, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


def _page(
    marker: str,
    *,
    page: int = 1,
    has_more: bool = False,
    tier: str = "free",
    version: float = 4.1,
):
    """Build one valid Artificial Analysis list response page."""
    return {
        "tier": tier,
        "intelligence_index_version": version,
        "pagination": {
            "page": page,
            "page_size": 200,
            "total_pages": 2 if has_more else page,
            "has_more": has_more,
        },
        "data": [_model(marker)],
    }


def _model(marker: str):
    """Build the minimum documented language-model item shape."""
    return {
        "id": marker,
        "name": marker,
        "slug": marker,
        "evaluations": {},
        "marker": marker,
    }


def test_agent_leaderboard_matching_uses_accessible_base_model(monkeypatch):
    """Agent wrapper names must not replace the provider model identifier."""
    from app.agents import utils as agent_utils

    base_model = SimpleNamespace(
        id="base-model-1",
        model_name="gpt-4o",
        name="Configured GPT-4o",
        settings={},
        capabilities=[],
        tools=[],
    )
    monkeypatch.setattr(
        leaderboard,
        "list_user_models",
        lambda _db, _user_id: [
            {
                "model_id": "agent-wrapper-1",
                "name": "Research helper",
                "model_kind": "agent",
            }
        ],
    )
    monkeypatch.setattr(
        agent_utils,
        "list_accessible_agents",
        lambda _db, _user_id: [
            {"model_id": "agent-wrapper-1", "base_model_id": "base-model-1"}
        ],
    )
    monkeypatch.setattr(
        leaderboard,
        "get_model",
        lambda _db, model_id: base_model if model_id == "base-model-1" else None,
    )
    monkeypatch.setattr(
        leaderboard,
        "get_user",
        lambda _db, _user_id, _request=None: SimpleNamespace(group_id="group-1"),
    )
    monkeypatch.setattr(
        leaderboard,
        "get_group_page_settings",
        lambda _group_id, _page, _db: {
            "enabled": True,
            "artificial_analysis_api_key": "test-key",
            "artificial_analysis_data_level": "free",
        },
    )
    monkeypatch.setattr(
        leaderboard,
        "get_leaderboard_data",
        lambda _key, _level: {
            "data": [_model("gpt-4o")],
            "data_level": "free",
            "provider_tier": "free",
            "intelligence_index_version": 4.1,
        },
    )

    payload = leaderboard.get_llm_model_leaderboard(object(), "user-1")

    assert len(payload["models"]) == 1
    assert payload["models"][0]["model_name"] == "Configured GPT-4o"


def test_leaderboard_cache_is_partitioned_by_api_key(monkeypatch):
    provider_calls: list[str] = []

    def fake_get(_url, *, headers, params, timeout):
        del params, timeout
        api_key = headers["x-api-key"]
        provider_calls.append(api_key)
        return _Response(_page(f"data-for-{api_key}"))

    monkeypatch.setattr(leaderboard.requests, "get", fake_get)
    leaderboard.clear_llm_model_leaderboard_cache()

    key_a_result = leaderboard.get_leaderboard_data("KEY_A")
    key_b_result = leaderboard.get_leaderboard_data("KEY_B")
    key_a_cached_result = leaderboard.get_leaderboard_data("KEY_A")

    assert provider_calls == ["KEY_A", "KEY_B"]
    assert key_a_result["data"] == [_model("data-for-KEY_A")]
    assert key_b_result["data"] == [_model("data-for-KEY_B")]
    assert key_a_cached_result == key_a_result
    assert "KEY_A" not in leaderboard._MODEL_CACHE
    assert "KEY_B" not in leaderboard._MODEL_CACHE


def test_concurrent_cold_cache_requests_share_one_provider_fetch(monkeypatch):
    """Followers wait for the first cold fetch instead of spending API quota."""

    fetch_started = threading.Event()
    release_fetch = threading.Event()
    waiter_started = threading.Event()
    provider_calls = []
    original_wait = leaderboard._MODEL_CACHE_CONDITION.wait

    def fake_fetch(_api_key, _data_level):
        provider_calls.append(True)
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        return {
            "data": [_model("shared")],
            "data_level": "free",
            "provider_tier": "free",
            "intelligence_index_version": 4.1,
        }

    def observed_wait(*args, **kwargs):
        waiter_started.set()
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(leaderboard, "_fetch_all_leaderboard_pages", fake_fetch)
    monkeypatch.setattr(leaderboard._MODEL_CACHE_CONDITION, "wait", observed_wait)
    leaderboard.clear_llm_model_leaderboard_cache()

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(leaderboard.get_leaderboard_data, "SHARED_KEY")
        assert fetch_started.wait(timeout=2)
        follower = executor.submit(leaderboard.get_leaderboard_data, "SHARED_KEY")
        assert waiter_started.wait(timeout=2)
        assert len(provider_calls) == 1
        release_fetch.set()

        assert follower.result(timeout=2) == owner.result(timeout=2)

    assert len(provider_calls) == 1


def test_leaderboard_cache_is_partitioned_by_data_level(monkeypatch):
    provider_urls: list[str] = []

    def fake_get(url, *, headers, params, timeout):
        del headers, params, timeout
        provider_urls.append(url)
        tier = "pro" if url.endswith("/language/models") else "free"
        return _Response(_page(tier, tier=tier))

    monkeypatch.setattr(leaderboard.requests, "get", fake_get)
    leaderboard.clear_llm_model_leaderboard_cache()

    free_result = leaderboard.get_leaderboard_data("SHARED_KEY", "free")
    full_result = leaderboard.get_leaderboard_data("SHARED_KEY", "full")
    leaderboard.get_leaderboard_data("SHARED_KEY", "free")
    leaderboard.get_leaderboard_data("SHARED_KEY", "full")

    assert len(provider_urls) == 2
    assert provider_urls[0].endswith("/language/models/free")
    assert provider_urls[1].endswith("/language/models")
    assert free_result["data_level"] == "free"
    assert full_result["data_level"] == "full"


def test_leaderboard_fetches_every_page(monkeypatch):
    requested_pages: list[int] = []

    def fake_get(_url, *, headers, params, timeout):
        del headers, timeout
        page = params["page"]
        requested_pages.append(page)
        return _Response(
            _page(
                f"page-{page}",
                page=page,
                has_more=page == 1,
                tier="commercial",
            )
        )

    monkeypatch.setattr(leaderboard.requests, "get", fake_get)
    leaderboard.clear_llm_model_leaderboard_cache()

    result = leaderboard.get_leaderboard_data("COMMERCIAL_KEY", "full")

    assert requested_pages == [1, 2]
    assert result == {
        "data": [_model("page-1"), _model("page-2")],
        "data_level": "full",
        "provider_tier": "commercial",
        "intelligence_index_version": 4.1,
    }


def test_clear_llm_model_leaderboard_cache_clears_all_levels(monkeypatch):
    provider_calls: list[str] = []

    def fake_get(url, *, headers, params, timeout):
        del params, timeout
        provider_calls.append(f"{headers['x-api-key']}:{url}")
        tier = "pro" if url.endswith("/language/models") else "free"
        return _Response(_page(str(len(provider_calls)), tier=tier))

    monkeypatch.setattr(leaderboard.requests, "get", fake_get)
    leaderboard.clear_llm_model_leaderboard_cache()

    leaderboard.get_leaderboard_data("KEY_A", "free")
    leaderboard.get_leaderboard_data("KEY_A", "full")
    leaderboard.get_leaderboard_data("KEY_B", "free")
    leaderboard.clear_llm_model_leaderboard_cache("KEY_A")

    leaderboard.get_leaderboard_data("KEY_A", "free")
    leaderboard.get_leaderboard_data("KEY_A", "full")
    leaderboard.get_leaderboard_data("KEY_B", "free")

    assert len(provider_calls) == 5


def test_full_data_reports_a_clear_tier_error(monkeypatch):
    def fake_get(_url, *, headers, params, timeout):
        del headers, params, timeout
        return _Response({"error": "subscription required"}, status_code=403)

    monkeypatch.setattr(leaderboard.requests, "get", fake_get)
    leaderboard.clear_llm_model_leaderboard_cache()

    with pytest.raises(HTTPException) as exc_info:
        leaderboard.get_leaderboard_data("FREE_KEY", "full")

    assert exc_info.value.status_code == 424
    assert exc_info.value.detail == {
        "type": "leaderboard_provider_full_tier_required"
    }


def test_invalid_pagination_envelope_is_rejected(monkeypatch):
    def fake_get(_url, *, headers, params, timeout):
        del headers, params, timeout
        return _Response({"tier": "free", "data": []})

    monkeypatch.setattr(leaderboard.requests, "get", fake_get)
    leaderboard.clear_llm_model_leaderboard_cache()

    with pytest.raises(HTTPException) as exc_info:
        leaderboard.get_leaderboard_data("KEY", "free")

    assert exc_info.value.detail == {"type": "leaderboard_provider_invalid_data"}


def test_transient_refresh_failure_applies_a_short_stale_retry_cooldown(monkeypatch):
    """Repeated requests should not immediately retry a failed stale refresh."""

    now = [100_000.0]
    provider_calls = []
    stale_result = {
        "data": [_model("stale")],
        "data_level": "free",
        "provider_tier": "free",
        "intelligence_index_version": 4.1,
    }
    cache_key = leaderboard._leaderboard_cache_key("KEY", "free")
    leaderboard.clear_llm_model_leaderboard_cache()
    leaderboard._MODEL_CACHE[cache_key] = {
        "result": stale_result,
        "timestamp": 0,
        "refreshing": False,
    }

    def fail_fetch(*_args):
        provider_calls.append(True)
        raise leaderboard._provider_error("leaderboard_provider_unavailable")

    monkeypatch.setattr(leaderboard.time, "time", lambda: now[0])
    monkeypatch.setattr(leaderboard, "_fetch_all_leaderboard_pages", fail_fetch)

    assert leaderboard.get_leaderboard_data("KEY", "free") == stale_result
    assert leaderboard.get_leaderboard_data("KEY", "free") == stale_result
    assert len(provider_calls) == 1

    now[0] += leaderboard.LEADERBOARD_STALE_RETRY_COOLDOWN + 1
    assert leaderboard.get_leaderboard_data("KEY", "free") == stale_result
    assert len(provider_calls) == 2


def test_model_entry_preserves_unknown_evaluations_and_coerces_numeric_strings():
    """Provider evaluation extensions remain response-model compatible."""

    entry = leaderboard._build_model_entry(
        {
            "id": "model-1",
            "name": "model-1",
            "slug": "model-1",
            "evaluations": {
                "score": "42.5",
                "availability": "preview",
                "not_a_number": "NaN",
                "metadata": {"source": "provider"},
            },
        },
        None,
    )
    validated = LLMLeaderboardModel(**entry)

    assert validated.evaluations == {
        "score": 42.5,
        "availability": "preview",
        "not_a_number": "NaN",
        "metadata": {"source": "provider"},
    }
