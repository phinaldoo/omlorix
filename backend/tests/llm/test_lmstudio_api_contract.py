import json
from unittest.mock import MagicMock, patch

from app.llm.lmstudio.schemas import (
    _lmstudio_reasoning_options,
    _lmstudio_tools_supported,
)
from app.llm.lmstudio.utils import (
    _build_load_payload,
    _normalize_download_progress,
    download_model,
    list_models_all,
    list_models_loaded,
    lmstudio_capabilities_to_list,
    normalize_lmstudio_responses_reasoning_effort,
)
from app.llm.openai.utils import _build_openai_reasoning_payload


def _json_response(payload):
    """Build the minimal requests.Response double used by native API tests."""
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_native_v1_model_list_shape_is_parsed_and_normalized():
    """The current native API returns models, config, and object quantization."""
    documented_payload = {
        "models": [
            {
                "type": "llm",
                "publisher": "google",
                "key": "google/gemma-test",
                "display_name": "Gemma Test",
                "architecture": "gemma",
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "size_bytes": 123,
                "loaded_instances": [],
                "max_context_length": 8192,
                "capabilities": {"vision": False, "trained_for_tool_use": False},
            }
        ]
    }

    with patch(
        "app.llm.lmstudio.utils._get_lmstudio_credentials",
        return_value=("http://127.0.0.1:1234", ""),
    ), patch("app.llm.lmstudio.utils.assert_url_allowed"), patch(
        "app.llm.lmstudio.utils._lmstudio_request",
        return_value=_json_response(documented_payload),
    ):
        models = list_models_all(MagicMock(), "provider-id")

    assert models[0]["key"] == "google/gemma-test"
    assert models[0]["quantization"] == "Q4_K_M"
    assert models[0]["quantization_info"] == {
        "name": "Q4_K_M",
        "bits_per_weight": 4,
    }


def test_loaded_instances_read_current_config_shape():
    """Loaded-instance runtime values come from native v1's config object."""
    model = {
        "key": "google/gemma-test",
        "name": "Gemma Test",
        "type": "llm",
        "loaded_instances": [
            {
                "id": "gemma-instance",
                "config": {
                    "context_length": 8192,
                    "parallel": 4,
                    "flash_attention": True,
                },
            }
        ],
    }

    with patch("app.llm.lmstudio.utils.list_models_all", return_value=[model]):
        loaded = list_models_loaded("provider-id", MagicMock())

    assert loaded[0]["instance_id"] == "gemma-instance"
    assert loaded[0]["context_length"] == 8192
    assert loaded[0]["parallel"] == 4
    assert loaded[0]["flash_attention"] is True


def test_load_payload_contains_only_documented_native_v1_fields():
    """Legacy SDK-only load options must not leak into the REST request."""
    payload = _build_load_payload(
        "google/gemma-test",
        {
            "context_length": 8192,
            "eval_batch_size": 256,
            "flash_attention": False,
            "num_experts": 4,
            "offload_kv_cache_to_gpu": True,
            "identifier": "unsupported",
            "gpu": "max",
            "seed": 42,
        },
    )

    assert payload == {
        "model": "google/gemma-test",
        "echo_load_config": True,
        "context_length": 8192,
        "eval_batch_size": 256,
        "flash_attention": False,
        "num_experts": 4,
        "offload_kv_cache_to_gpu": True,
    }


def test_download_progress_uses_native_v1_top_level_byte_fields():
    """Documented status byte counters should drive the admin progress bar."""
    progress = _normalize_download_progress(
        {
            "status": "downloading",
            "downloaded_bytes": 50,
            "total_size_bytes": 200,
            "bytes_per_second": 25,
        },
        job_id="job_1",
        model="google/gemma-test",
    )

    assert progress["completed"] == 50
    assert progress["total"] == 200
    assert progress["percent"] == 25
    assert progress["bytes_per_second"] == 25


def test_already_downloaded_without_job_id_completes_cleanly():
    """The documented already_downloaded response intentionally omits job_id."""
    with patch(
        "app.llm.lmstudio.utils._get_lmstudio_credentials",
        return_value=("http://127.0.0.1:1234", ""),
    ), patch("app.llm.lmstudio.utils.assert_url_allowed"), patch(
        "app.llm.lmstudio.utils._lmstudio_request",
        return_value=_json_response({"status": "already_downloaded"}),
    ):
        chunks = list(download_model("provider-id", "google/gemma-test", MagicMock()))

    payload = json.loads(chunks[0])
    assert payload["status"] == "completed"
    assert payload["already_downloaded"] is True
    assert payload["percent"] == 100


def test_all_lmstudio_llms_keep_default_tool_support():
    """trained_for_tool_use selects native formatting, not tool availability."""
    capabilities = lmstudio_capabilities_to_list(
        {
            "type": "llm",
            "capabilities": {
                "vision": False,
                "trained_for_tool_use": False,
            },
        }
    )

    assert "completion" in capabilities
    assert "tools" in capabilities
    assert _lmstudio_tools_supported(
        {
            "type": "llm",
            "capabilities": {"trained_for_tool_use": False},
        }
    )


def test_native_reasoning_on_is_safe_for_responses_api():
    """Native on/off metadata must never leak into reasoning.effort."""
    assert normalize_lmstudio_responses_reasoning_effort("on") == "medium"
    assert normalize_lmstudio_responses_reasoning_effort("off") == "none"
    assert _lmstudio_reasoning_options(
        {
            "capabilities": {
                "reasoning": {"allowed_options": ["off", "on"]}
            }
        }
    ) == ["medium"]
    assert _build_openai_reasoning_payload(
        {"reasoning": True, "reasoning_effort": "on"},
        provider_type="lmstudio",
    ) == {"effort": "medium"}
    assert _build_openai_reasoning_payload(
        {"reasoning": False, "reasoning_effort": "on"},
        provider_type="lmstudio",
    ) == {"effort": "none"}
