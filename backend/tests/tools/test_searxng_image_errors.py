from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.tools.websearch import utils as websearch_utils
from app.tools.websearch.images import searxng_images


class _FakeResponse:
    """Small requests-compatible response used by the image adapter tests."""

    def __init__(self, payload=None, *, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error
        self.closed = False

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def json(self):
        return self._payload

    def close(self) -> None:
        self.closed = True


def test_searxng_image_search_uses_policy_request_without_disclosing_provider_url(
    monkeypatch,
):
    """Remote failures stay generic while requests cross the safe boundary."""

    secret_base_url = "http://admin:secret-token@searxng.internal.local/private"
    database = object()
    response = _FakeResponse(error=RuntimeError("provider failed"))
    captured = {}

    def fake_request(db, method, url, **kwargs):
        captured.update(db=db, method=method, url=url, kwargs=kwargs)
        return response

    monkeypatch.setattr(searxng_images, "outbound_policy_web_request", fake_request)

    with pytest.raises(HTTPException) as exc_info:
        searxng_images.searxng_search_images(
            secret_base_url,
            "user-controlled image query",
            db=database,
        )

    assert captured["db"] is database
    assert captured["method"] == "GET"
    assert captured["url"] == f"{secret_base_url}/search"
    assert captured["kwargs"]["feature"] == "SearXNG image search"
    assert response.closed is True
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "SearXNG image search failed."
    assert "secret-token" not in exc_info.value.detail
    assert "searxng.internal.local" not in exc_info.value.detail
    assert "user-controlled" not in exc_info.value.detail


@pytest.mark.parametrize("payload", [[], None, "not-an-object"])
def test_searxng_image_search_rejects_non_object_json(monkeypatch, payload):
    """Valid JSON with the wrong top-level shape produces a controlled 502."""

    response = _FakeResponse(payload)
    monkeypatch.setattr(
        searxng_images,
        "outbound_policy_web_request",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(HTTPException) as exc_info:
        searxng_images.searxng_search_images(
            "https://searxng.example",
            "query",
            db=object(),
        )

    assert response.closed is True
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "SearXNG image search failed."


def test_searxng_image_search_preserves_valid_object_results(monkeypatch):
    """A normal SearXNG response still returns the requested result count."""

    response = _FakeResponse(
        {
            "results": [
                {"img_src": "https://images.example/one.jpg"},
                {"img_src": "https://images.example/two.jpg"},
                {"img_src": "https://images.example/three.jpg"},
            ]
        }
    )
    monkeypatch.setattr(
        searxng_images,
        "outbound_policy_web_request",
        lambda *_args, **_kwargs: response,
    )

    result = searxng_images.searxng_search_images(
        "https://searxng.example",
        "query",
        db=object(),
        num_results=2,
    )

    assert response.closed is True
    assert result == {
        "result": [
            {"img_src": "https://images.example/one.jpg"},
            {"img_src": "https://images.example/two.jpg"},
        ],
        "metadata": {"provider_images": "searxng"},
    }


def test_searxng_image_limit_is_clamped_to_provider_setting(monkeypatch):
    """A model-requested image count cannot exceed the configured maximum."""

    database = object()
    provider = SimpleNamespace(
        provider="searxng",
        settings={"base_url": "https://searxng.example", "num_results": 2},
    )
    captured = {}

    monkeypatch.setattr(
        websearch_utils,
        "get_websearch_provider",
        lambda db, provider_id: provider,
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(websearch_utils, "_provider_types", lambda _provider: {"search"})
    monkeypatch.setattr(
        websearch_utils,
        "_filter_allowed_image_results",
        lambda _db, results: results,
    )
    def fake_search(base_url, query, *, num_results, db):
        captured.update(
            base_url=base_url,
            query=query,
            num_results=num_results,
            db=db,
        )
        return {"result": [{"img_src": "https://images.example/one.jpg"}]}

    monkeypatch.setattr(websearch_utils, "searxng_search_images", fake_search)

    result = websearch_utils.web_search(
        database,
        "user-1",
        scrape_provider_id=None,
        search_provider_id="provider-1",
        project_id=None,
        search_mode="images",
        image_limit=10,
        queries=["cats"],
    )

    assert captured == {
        "base_url": "https://searxng.example",
        "query": "cats",
        "num_results": 2,
        "db": database,
    }
    assert result["result"][0]["content"] == [
        {"img_src": "https://images.example/one.jpg"}
    ]
