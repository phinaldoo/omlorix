from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings import utils as admin_utils


def test_global_model_options_exclude_user_managed_models():
    """Private user models must not be offered to global admin settings."""

    rows = [
        SimpleNamespace(
            id="shared-model",
            name="Shared model",
            model_name="shared",
            provider="openai",
            meta={},
        ),
        SimpleNamespace(
            id="admin-model",
            name="Admin model",
            model_name="admin-model",
            provider="openai",
            meta={},
        ),
        SimpleNamespace(
            id="personal-model",
            name="Personal model",
            model_name="personal-model",
            provider="openai",
            meta={"user_managed": True, "owner_user_id": "user-1"},
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

    options = admin_utils._get_admin_managed_model_options(DB())

    assert [option["value"] for option in options] == ["shared-model", "admin-model"]


def test_slide_presentation_schema_drops_stored_personal_model(monkeypatch):
    """A stale private selection must be removed from the returned schema."""

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda _db, _page: {"presentation_model_id": "personal-model"},
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_admin_managed_model_options",
        lambda _db: [{"value": "shared-model", "label": "Shared model"}],
    )

    response = admin_utils.get_admin_settings_schema_response(
        "slide_presentation",
        include_values=True,
        db=object(),
    )

    model_field = next(
        field
        for section in response["sections"]
        for field in section["fields"]
        if field["key"] == "presentation_model_id"
    )
    assert [option["value"] for option in model_field["options"]] == ["shared-model"]
    assert "presentation_model_id" not in response["values"]
    assert model_field.get("value") is None


def test_slide_presentation_update_rejects_personal_model(monkeypatch):
    """Submission validation must enforce the same global-model boundary."""

    monkeypatch.setattr(
        admin_utils,
        "_get_admin_managed_model_options",
        lambda _db: [{"value": "shared-model", "label": "Shared model"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.update_admin_settings_values_for_page(
            "slide_presentation",
            {"presentation_model_id": "personal-model"},
            object(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Selected presentation model is not available."
