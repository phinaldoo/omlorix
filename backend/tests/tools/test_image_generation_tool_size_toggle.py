import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.image_generation import utils as image_generation_utils
from app.tools.image_generation.size_options import ASSISTANT_SIZE_SELECTION_KEY


def _tool_params(monkeypatch, provider_type, model_name, settings):
    """Resolve tool parameters without opening a database session."""
    config = {
        "provider_id": "provider-1",
        "model_name": model_name,
        "settings": settings,
    }
    monkeypatch.setattr(
        image_generation_utils,
        "_get_image_generation_config",
        lambda _db: config,
    )
    monkeypatch.setattr(
        image_generation_utils,
        "_resolve_provider",
        lambda _db, _provider_id: SimpleNamespace(provider=provider_type),
    )
    return image_generation_utils.get_image_size_tool_params(db=object())


def test_disabled_toggle_removes_discrete_size_parameter(monkeypatch):
    params = _tool_params(
        monkeypatch,
        "openai",
        "gpt-image-2",
        {ASSISTANT_SIZE_SELECTION_KEY: False},
    )

    assert params == {}


def test_disabled_toggle_removes_ollama_dimension_parameters(monkeypatch):
    params = _tool_params(
        monkeypatch,
        "ollama",
        "x/flux2-klein",
        {ASSISTANT_SIZE_SELECTION_KEY: False},
    )

    assert params == {}


def test_openrouter_exposes_allowed_aspect_ratios(monkeypatch):
    params = _tool_params(
        monkeypatch,
        "openrouter",
        "google/gemini-3.1-flash-image",
        {
            ASSISTANT_SIZE_SELECTION_KEY: True,
            "allowed_sizes": ["1:1", "16:9"],
        },
    )

    assert params["size"]["enum"] == ["1:1", "16:9"]
