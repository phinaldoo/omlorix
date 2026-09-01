"""Regression coverage for filtering removed OpenAI models from discovery."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.llm.openai import utils as openai_utils


def test_model_listing_excludes_removed_deep_research_models(monkeypatch):
    """Do not surface removed native Deep Research models from ``/models``."""

    class FakeModelsAPI:
        """Return a mix of supported and removed model identifiers."""

        @staticmethod
        def list(**_kwargs):
            return [
                SimpleNamespace(id="gpt-5.6", created=1, object="model", owned_by="openai"),
                SimpleNamespace(id="o3-deep-research", created=2, object="model", owned_by="openai"),
                SimpleNamespace(id="o4-mini-deep-research-2025-06-26", created=3, object="model", owned_by="openai"),
            ]

    class FakeOpenAI:
        """Minimal client surface used by ``list_models_openai``."""

        def __init__(self, **_kwargs):
            self.models = FakeModelsAPI()

    monkeypatch.setattr(
        openai_utils,
        "_resolve_openai_client_context",
        lambda *_args, **_kwargs: {"client_kwargs": {}, "request_options": {}},
    )
    monkeypatch.setattr(openai_utils, "OpenAI", FakeOpenAI)

    discovered_models = openai_utils.list_models_openai(db=object())

    assert [model["id"] for model in discovered_models] == ["gpt-5.6"]


def test_model_listing_maps_connection_failure_without_status_to_bad_gateway(monkeypatch):
    """An unreachable compatible provider must produce a stable HTTP error."""

    class FakeAPIConnectionError(Exception):
        status_code = None
        response = None

    class FakeModelsAPI:
        @staticmethod
        def list(**_kwargs):
            raise FakeAPIConnectionError("Connection refused")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.models = FakeModelsAPI()

    monkeypatch.setattr(
        openai_utils,
        "_resolve_openai_client_context",
        lambda *_args, **_kwargs: {"client_kwargs": {}, "request_options": {}},
    )
    monkeypatch.setattr(openai_utils, "APIConnectionError", FakeAPIConnectionError)
    monkeypatch.setattr(openai_utils, "OpenAI", FakeOpenAI)

    with pytest.raises(HTTPException) as exc_info:
        openai_utils.list_models_openai(db=object())

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Failed to list OpenAI models: Connection refused"
