from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings.schema_categories import deep_research as admin_schemas
from app.admin.settings import utils as admin_utils


def test_deep_research_settings_defaults_are_constructible():
    """The admin schema endpoint must be able to build an empty default page."""

    defaults = admin_schemas.DeepResearchSettings().model_dump()

    assert defaults["execution_mode"] == "custom"
    assert defaults["native_model_name"] is None
    assert "html_model_id" not in defaults
    assert "quality_profile" not in defaults
    assert defaults["max_revision_rounds"] == 2
    assert all(
        field.key not in {"quality_profile", "html_model_id"}
        for section in admin_schemas.deep_research_schema.sections
        for field in section.fields
    )

    revision_field = next(
        field
        for section in admin_schemas.deep_research_schema.sections
        for field in section.fields
        if field.key == "max_revision_rounds"
    )
    assert revision_field.default == defaults["max_revision_rounds"]
    assert revision_field.min_value == 1
    assert revision_field.max_value == 3


def test_deep_research_schema_recovers_from_invalid_stored_v2_values(monkeypatch):
    """Invalid persisted values must not make the admin schema endpoint fail."""

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda _db, _page: {
            "execution_mode": "custom",
            "quality_profile": "unknown",
            "html_model_id": "retired-html-model",
            "max_revision_rounds": 99,
            "websearch_search_provider": "search-provider",
        },
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_deep_research_provider_options",
        lambda _db: [],
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_deep_research_model_options",
        lambda _db, _provider_id: [],
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_custom_deep_research_model_options",
        lambda _db: [],
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_websearch_provider_options_with_metadata",
        lambda _db, capability: (
            [{"value": "search-provider", "label": "Search provider"}]
            if capability == "search"
            else []
        ),
    )

    response = admin_utils.get_admin_settings_schema_response(
        "deep_research",
        include_values=True,
        db=object(),
    )

    assert response["sections"]
    assert response["values"]["execution_mode"] == "custom"
    assert "quality_profile" not in response["values"]
    assert "html_model_id" not in response["values"]
    assert response["values"]["max_revision_rounds"] == 2
    assert response["values"]["websearch_search_provider"] == "search-provider"


def test_custom_selector_lists_supported_models_regardless_of_tools_capability():
    """Configured tools are not a prerequisite for runtime research tools."""

    assert "xai" in admin_utils.CUSTOM_DEEP_RESEARCH_MODEL_PROVIDER_TYPES

    rows = [
        SimpleNamespace(
            id="tool-model",
            name="Tool Model",
            model_name="tool-model",
            provider="openrouter",
            capabilities=["completion", "tools"],
        ),
        SimpleNamespace(
            id="completion-model",
            name="Completion Model",
            model_name="completion-model",
            provider="xai",
            capabilities=["completion", "thinking"],
        ),
    ]

    class Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return rows

    class DB:
        def query(self, *_args):
            return Query()

    options = admin_utils._get_custom_deep_research_model_options(DB())

    assert [option["value"] for option in options] == [
        "tool-model",
        "completion-model",
    ]


def test_deep_research_settings_accept_xai_model_without_tools(monkeypatch):
    """xAI models must pass validation even without persisted model tools."""

    model = SimpleNamespace(
        id="completion-model",
        provider="xai",
        capabilities=["completion", "thinking"],
        is_active=True,
    )
    record = SimpleNamespace(
        data={
            "execution_mode": "custom",
            "model_id": None,
            "max_revision_rounds": 2,
        }
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return model

    class DB:
        def query(self, *_args):
            return Query()

        def commit(self):
            return None

        def refresh(self, _value):
            return None

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, _page: record,
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_websearch_provider_options_with_metadata",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        admin_utils,
        "flag_modified",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        admin_utils,
        "invalidate_settings_cache",
        lambda: None,
    )

    result = admin_utils.update_admin_settings_values_for_page(
        "deep_research",
        {
            "execution_mode": "custom",
            "model_id": "completion-model",
        },
        DB(),
    )

    assert "model_id" in result
    assert record.data["model_id"] == "completion-model"


def test_switching_to_native_mode_clears_stale_model_without_provider(monkeypatch):
    """Mode selection must succeed before the newly visible provider is chosen."""

    record = SimpleNamespace(
        data={
            "execution_mode": "custom",
            "native_provider_id": None,
            "native_model_name": "stale-native-model",
            "max_revision_rounds": 2,
        }
    )

    class DB:
        def commit(self):
            return None

        def refresh(self, _value):
            return None

    monkeypatch.setattr(admin_utils, "get_settings_page", lambda _db, _page: record)
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(admin_utils, "invalidate_settings_cache", lambda: None)

    changed = admin_utils.update_admin_settings_values_for_page(
        "deep_research",
        {"execution_mode": "native"},
        DB(),
    )

    assert "execution_mode" in changed
    assert record.data["execution_mode"] == "native"
    assert record.data["native_provider_id"] is None
    assert record.data["native_model_name"] is None


def test_changing_native_provider_clears_previous_model(monkeypatch):
    """A provider auto-save must clear the old model before options reload."""

    provider = SimpleNamespace(id="new-provider", provider="google_aistudio")
    record = SimpleNamespace(
        data={
            "execution_mode": "native",
            "native_provider_id": "old-provider",
            "native_model_name": "old-provider-model",
            "max_revision_rounds": 2,
        }
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return provider

    class DB:
        def query(self, *_args):
            return Query()

        def commit(self):
            return None

        def refresh(self, _value):
            return None

    monkeypatch.setattr(admin_utils, "get_settings_page", lambda _db, _page: record)
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(admin_utils, "invalidate_settings_cache", lambda: None)

    changed = admin_utils.update_admin_settings_values_for_page(
        "deep_research",
        {"native_provider_id": "new-provider"},
        DB(),
    )

    assert "native_provider_id" in changed
    assert record.data["native_provider_id"] == "new-provider"
    assert record.data["native_model_name"] is None
