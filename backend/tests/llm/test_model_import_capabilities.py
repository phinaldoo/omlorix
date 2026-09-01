from types import SimpleNamespace
from unittest.mock import patch

from app.llm import models as llm_models


def test_openrouter_import_recomputes_audio_and_document_capabilities():
    payload = {
        "export_type": "llm_model",
        "export_version": llm_models.current_llm_model_export_version,
        "data": {
            "models": [
                {
                    "name": "Imported OpenRouter model",
                    "description": "Imported model",
                    "model_icon": "openrouter",
                    "provider": "openrouter",
                    "provider_id": "provider-1",
                    "model_name": "vendor/model",
                    "settings": {
                        "title_generation": False,
                        "allow_custom_generation_parameter": False,
                        "input_formats": ["text", "audio", "text_document"],
                        "output_formats": ["text"],
                        "provider_mode": "auto",
                        "supported_parameters": [],
                    },
                    "capabilities": ["completion"],
                    "tools": [],
                    "access": {"everyone": True},
                    "status": "active",
                }
            ]
        },
    }

    with patch.object(
        llm_models,
        "get_llm_provider",
        return_value=SimpleNamespace(provider="openrouter"),
    ), patch.object(
        llm_models,
        "create_model",
        return_value=SimpleNamespace(
            id="model-1",
            name="Imported OpenRouter model",
        ),
    ) as create_model_mock:
        result = llm_models.import_llm_models(object(), payload)

    assert result["errors"] == []
    assert create_model_mock.call_args.kwargs["capabilities"] == [
        "completion",
        "audio",
        "documents",
    ]
