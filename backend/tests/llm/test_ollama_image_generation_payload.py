from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.ollama.image_generation import _build_payload


def test_ollama_payload_keeps_bounded_dimensions():
    payload = _build_payload(
        "make a red square",
        "test-image-model",
        {"width": "1024", "height": 768},
    )

    assert payload["width"] == 1024
    assert payload["height"] == 768


def test_ollama_payload_omits_unsafe_dimensions():
    payload = _build_payload(
        "make a red square",
        "test-image-model",
        {"width": "100000", "height": "-1"},
    )

    assert "width" not in payload
    assert "height" not in payload
