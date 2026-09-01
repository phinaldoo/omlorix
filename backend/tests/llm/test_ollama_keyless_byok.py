from types import SimpleNamespace

import pytest

from app.llm.ollama import utils as ollama_utils


@pytest.mark.parametrize("api_key", [None, "", "synthetic-key"])
def test_byok_discovery_keeps_completion_models_with_optional_key(monkeypatch, api_key):
    """A local Ollama catalog must not require a synthetic API key."""

    model = SimpleNamespace(
        model="omlorix-e2e:latest",
        modified_at="2026-08-23T12:00:00Z",
        digest="sha256:test",
        size=42,
        details=SimpleNamespace(
            parent_model="",
            format="gguf",
            family="llama",
            families=["llama"],
            parameter_size="1B",
            quantization_level="Q4_K_M",
        ),
    )
    client = SimpleNamespace(
        list=lambda: SimpleNamespace(models=[model]),
        show=lambda _model: SimpleNamespace(capabilities=["completion"]),
    )
    client_calls = []

    def fake_get_ollama_client(_db, **kwargs):
        client_calls.append(kwargs)
        return client

    monkeypatch.setattr(ollama_utils, "get_ollama_client", fake_get_ollama_client)

    discovered = ollama_utils.list_models_ollama(
        object(),
        byok_base_url="http://ollama.test:11434",
        byok_api_key=api_key,
    )

    assert [entry["id"] for entry in discovered] == ["omlorix-e2e:latest"]
    assert client_calls == [
        {
            "byok_base_url": "http://ollama.test:11434",
            "byok_api_key": api_key,
        },
        {
            "byok_base_url": "http://ollama.test:11434",
            "byok_api_key": api_key,
        },
    ]


def test_keyless_byok_title_generation_uses_local_ollama(monkeypatch):
    """All Ollama BYOK calls honor the provider's optional-key contract."""

    client = SimpleNamespace(
        chat=lambda **_kwargs: SimpleNamespace(
            message=SimpleNamespace(content="Local title")
        )
    )
    client_calls = []

    def fake_get_ollama_client(_db, **kwargs):
        client_calls.append(kwargs)
        return client

    monkeypatch.setattr(ollama_utils, "get_ollama_client", fake_get_ollama_client)
    monkeypatch.setattr(
        ollama_utils,
        "get_model_capabilities",
        lambda *_args, **_kwargs: ["completion"],
    )
    monkeypatch.setattr(
        ollama_utils,
        "create_llm_generation_statistic",
        lambda *_args, **_kwargs: None,
    )

    title = ollama_utils.ollama_title_generation(
        object(),
        "omlorix-e2e:latest",
        "Summarize this chat",
        "Return a short title",
        byok_base_url="http://ollama.test:11434",
        byok_api_key=None,
    )

    assert title == "Local title"
    assert client_calls == [
        {
            "byok_base_url": "http://ollama.test:11434",
            "byok_api_key": None,
        }
    ]
