import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "numpy" not in sys.modules:
    fake_numpy = ModuleType("numpy")
    fake_numpy.linspace = lambda start, stop, num, dtype=int: []
    sys.modules["numpy"] = fake_numpy

if "numpy.typing" not in sys.modules:
    sys.modules["numpy.typing"] = ModuleType("numpy.typing")

if "pandas" not in sys.modules:
    fake_pandas = ModuleType("pandas")
    fake_pandas.DataFrame = type("DataFrame", (), {})
    fake_pandas.to_datetime = lambda value, *args, **kwargs: value
    fake_pandas.isna = lambda value: False
    sys.modules["pandas"] = fake_pandas

if "elevenlabs" not in sys.modules:
    fake_elevenlabs = ModuleType("elevenlabs")
    fake_elevenlabs.SpeechToTextConvertRequestModelId = "scribe_v1"
    sys.modules["elevenlabs"] = fake_elevenlabs

if "elevenlabs.client" not in sys.modules:
    fake_elevenlabs_client = ModuleType("elevenlabs.client")
    fake_elevenlabs_client.ElevenLabs = lambda *args, **kwargs: SimpleNamespace()
    sys.modules["elevenlabs.client"] = fake_elevenlabs_client

from app.llm.models import get_llm_provider_status_summary, normalize_llm_provider_status
from app.llm.utils import refresh_provider_status_snapshot


class ProviderRegularRequestStatusTests:
    def test_normalize_status_returns_unknown_when_regular_requests_are_disabled(self):
        provider = SimpleNamespace(
            settings={"disable_background_sync": True},
            status={"available": "down", "model_list": ["gpt-4.1"]},
        )

        status = normalize_llm_provider_status(provider)

        assert status["available"] == "unknown"
        assert status["model_list"] == ["gpt-4.1"]

    def test_status_summary_ignores_stale_down_status_for_disabled_provider(self):
        disabled_provider = SimpleNamespace(
            settings={"disable_background_sync": True},
            status={"available": "down"},
        )
        healthy_provider = SimpleNamespace(
            settings={"disable_background_sync": False},
            status={"available": "up"},
        )
        db = MagicMock()
        db.query.return_value.all.return_value = [disabled_provider, healthy_provider]

        all_available, down_count = get_llm_provider_status_summary(db)

        assert all_available is True
        assert down_count == 0

    def test_refresh_snapshot_persists_unknown_without_request_when_regular_requests_are_disabled(self):
        provider = SimpleNamespace(
            id="provider-1",
            settings={"disable_background_sync": True},
            status={"available": "down", "model_list": ["gpt-4.1"]},
        )
        db = MagicMock()

        with patch("app.llm.utils.get_llm_provider", return_value=provider), patch(
            "app.llm.utils.list_provider_models"
        ) as mock_list_provider_models:
            refreshed = refresh_provider_status_snapshot(db, provider.id)

        mock_list_provider_models.assert_not_called()
        db.add.assert_called_once_with(provider)
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(provider)
        assert refreshed is provider
        assert provider.status["available"] == "unknown"
        assert provider.status["model_list"] == ["gpt-4.1"]

    def test_refresh_snapshot_for_ollama_uses_stable_installed_model_roster(self):
        provider = SimpleNamespace(
            id="provider-1",
            provider="ollama",
            settings={},
            status={"available": "down", "model_list": []},
        )
        db = MagicMock()

        with patch("app.llm.utils.get_llm_provider", return_value=provider), patch(
            "app.llm.utils.list_provider_status_models",
            return_value=[
                {"id": "llama3:latest"},
                {"id": "mxbai-embed-large:latest"},
            ],
        ) as mock_list_provider_status_models:
            refreshed = refresh_provider_status_snapshot(db, provider.id)

        mock_list_provider_status_models.assert_called_once_with(db, provider.id)
        assert refreshed is provider
        assert provider.status["available"] == "up"
        assert provider.status["model_list"] == ["llama3:latest", "mxbai-embed-large:latest"]
