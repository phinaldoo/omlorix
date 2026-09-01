"""Anthropic model discovery uses rich metadata without breaking base URLs."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.llm.anthropic import schemas as anthropic_schemas
from app.llm.anthropic import models as anthropic_models
from app.llm.anthropic import utils as anthropic_utils
from app.llm.anthropic.thinking import get_anthropic_thinking_capabilities


def _support(supported: bool):
    """Build the nested shape returned by the Anthropic SDK."""
    return SimpleNamespace(supported=supported)


class _EmptyQuery:
    """Return no users or groups while shared model schemas are assembled."""

    def order_by(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _EmptyDB:
    """Provide the read-only query surface used by shared model schemas."""

    def query(self, *_args, **_kwargs):
        return _EmptyQuery()


def test_main_anthropic_listing_uses_beta_metadata(monkeypatch) -> None:
    """The official endpoint exposes token limits and reasoning capabilities."""
    model = SimpleNamespace(
        id="claude-test",
        display_name="Claude Test",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        max_input_tokens=1_000_000,
        max_tokens=128_000,
        capabilities=SimpleNamespace(
            effort=SimpleNamespace(
                supported=True,
                low=_support(True),
                medium=_support(True),
                high=_support(True),
                xhigh=None,
                max=_support(False),
            ),
            thinking=SimpleNamespace(
                supported=True,
                types=SimpleNamespace(adaptive=_support(True), enabled=_support(True)),
            ),
            image_input=_support(True),
            pdf_input=_support(False),
        ),
    )
    list_calls = []
    client = SimpleNamespace(
        beta=SimpleNamespace(
            models=SimpleNamespace(
                list=lambda **kwargs: list_calls.append(kwargs) or [model]
            )
        ),
        models=SimpleNamespace(
            list=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("standard API used")
            )
        ),
    )
    monkeypatch.setattr(
        anthropic_models,
        "_assert_anthropic_model_listing_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(anthropic_models, "get_anthropic_client", lambda *_args, **_kwargs: client)

    listed_models = anthropic_utils.list_anthropic_models(None, api_key="test")
    assert listed_models == [
        {
            "id": "claude-test",
            "name": "Claude Test",
            "display_name": "Claude Test",
            "created": 1767312000,
            "max_input_tokens": 1_000_000,
            "max_tokens": 128_000,
            "reasoning": {
                "supported": True,
                "reasoning_efforts_supported": True,
                "adaptive": True,
                "enabled": True,
                "efforts": ["low", "medium", "high"],
            },
            "capabilities": {"image_input": True, "pdf_input": False},
        }
    ]
    assert get_anthropic_thinking_capabilities(
        "claude-test",
        model_info=listed_models[0],
    ) == {
        "thinking": True,
        "thinking_disabled_allowed": True,
        "thinking_budget_support": False,
        "reasoning_effort_support": True,
        "thinking_support_adaptive": True,
        "reasoning_effort": ["low", "medium", "high"],
    }
    assert list_calls == [{"limit": 1000}]


def test_anthropic_base_listing_accepts_basic_model_shape(monkeypatch) -> None:
    """Compatible base URLs avoid beta=true and may omit all optional metadata."""
    model = SimpleNamespace(id="custom-model", display_name="Custom Model", created_at=None)
    client = SimpleNamespace(
        beta=SimpleNamespace(
            models=SimpleNamespace(
                list=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("beta API used")
                )
            )
        ),
        models=SimpleNamespace(list=lambda **_kwargs: [model]),
    )
    monkeypatch.setattr(
        anthropic_models,
        "_assert_anthropic_model_listing_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(anthropic_models, "get_anthropic_client", lambda *_args, **_kwargs: client)

    assert anthropic_utils.list_anthropic_models(
        None,
        api_key="test",
        base_url="https://example.test",
    ) == [{"id": "custom-model", "name": "Custom Model", "display_name": "Custom Model"}]


def test_anthropic_listing_iterates_the_auto_pager(monkeypatch) -> None:
    """Models after the first response page must remain discoverable."""
    first = SimpleNamespace(id="first", display_name="First", created_at=None)
    second = SimpleNamespace(id="second", display_name="Second", created_at=None)

    class Pager:
        data = [first]

        def __iter__(self):
            yield first
            yield second

    client = SimpleNamespace(
        beta=SimpleNamespace(
            models=SimpleNamespace(list=lambda **_kwargs: Pager())
        )
    )
    monkeypatch.setattr(
        anthropic_models,
        "_assert_anthropic_model_listing_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        anthropic_models,
        "get_anthropic_client",
        lambda *_args, **_kwargs: client,
    )

    assert [
        model["id"]
        for model in anthropic_utils.list_anthropic_models(None, api_key="test")
    ] == ["first", "second"]


def test_anthropic_schema_uses_listed_limits_and_capabilities() -> None:
    """Creating a model is prefilled directly from its listing metadata."""
    schema = anthropic_schemas.get_anthropic_model_schema(
        _EmptyDB(),
        None,
        "claude-test",
        model_info={
            "id": "claude-test",
            "display_name": "Claude Test",
            "max_input_tokens": 1_000_000,
            "max_tokens": 128_000,
            "reasoning": {
                "supported": True,
                "reasoning_efforts_supported": True,
                "adaptive": True,
                "enabled": True,
                "efforts": ["low", "high"],
            },
            "capabilities": {"image_input": True, "pdf_input": True},
        },
    )
    fields = {
        field.key: field
        for section in schema.sections
        for field in section.fields
    }

    assert fields["name"].value == "Claude Test"
    assert fields["settings.input_token_limit"].value == 1_000_000
    assert fields["settings.output_token_limit"].value == 128_000
    assert fields["settings.knowledge_cutoff"].value is None
    assert fields["settings.max_tokens"].value == 128_000
    assert fields["settings.input_formats"].value == [
        "text",
        "text_document",
        "image",
        "pdf",
    ]
    assert [
        option.value for option in fields["settings.reasoning_effort"].options
    ] == ["low", "high"]
    assert "settings.thinking_budget" not in fields


def test_anthropic_schema_restores_hardcoded_knowledge_cutoff() -> None:
    """Cutoffs remain available because Anthropic does not return them."""
    schema = anthropic_schemas.get_anthropic_model_schema(
        _EmptyDB(),
        None,
        "claude-opus-5",
        model_info={"id": "claude-opus-5", "display_name": "Claude Opus 5"},
    )
    fields = {
        field.key: field
        for section in schema.sections
        for field in section.fields
    }

    assert fields["settings.knowledge_cutoff"].value == "2026-05-01"


def test_native_websearch_is_gated_by_documented_model_support() -> None:
    """Unknown and custom models must not receive Anthropic's server tool."""
    supported_schema = anthropic_schemas.get_anthropic_model_schema(
        _EmptyDB(),
        None,
        "claude-opus-5",
        model_info={"id": "claude-opus-5", "display_name": "Claude Opus 5"},
    )
    unknown_schema = anthropic_schemas.get_anthropic_model_schema(
        _EmptyDB(),
        None,
        "claude-unknown",
        model_info={"id": "claude-unknown", "display_name": "Unknown"},
    )

    supported_keys = {
        field.key for section in supported_schema.sections for field in section.fields
    }
    unknown_keys = {
        field.key for section in unknown_schema.sections for field in section.fields
    }
    assert "settings.native_websearch" in supported_keys
    assert "settings.native_websearch" not in unknown_keys
