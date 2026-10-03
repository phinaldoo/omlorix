import base64
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import httpx
from google import genai
from google.genai import types
from PIL import Image

from app.llm.google_aistudio import music_generation, schemas, text_to_speech, utils
from app.llm.google_aistudio.model_list import (
    AISTUDIO_MODEL_DICT,
    AISTUDIO_MODELS_NOT_SUPPORTED,
)


@pytest.mark.parametrize(
    ("model", "effort", "expected"),
    [
        ("gemini-3.8-flash", "high", "HIGH"),
        ("models/gemini-3.8-flash", "minimal", "LOW"),
        ("gemini-3.7-flash", "minimal", "LOW"),
        ("gemini-3.5-flash-lite", "minimal", "MINIMAL"),
        ("gemini-3.8-flash", None, None),
    ],
)
def test_gemini_levels_replace_legacy_request_controls(model, effort, expected):
    settings = {"reasoning_effort": effort, "include_thinking": False}
    config = utils.build_aistudio_generate_content_config(
        settings,
        model_name=model,
        temperature=0.2,
        top_p=0.8,
        top_k=10,
        candidate_count=2,
        thinking_config=types.ThinkingConfig(thinking_budget=500),
        max_output_tokens=100,
    )
    payload = config.model_dump(exclude_none=True)
    assert not {"temperature", "top_p", "top_k", "candidate_count"} & payload.keys()
    assert config.thinking_config.thinking_budget is None
    assert config.thinking_config.thinking_level == expected
    assert config.thinking_config.include_thoughts is False
    assert config.max_output_tokens is None
    assert settings["reasoning_effort"] == effort


def test_legacy_gemini_keeps_sampling_and_budget():
    config = utils.build_aistudio_generate_content_config(
        model_name="gemini-2.5-flash",
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_budget=500),
    )
    assert config.temperature == 0.2
    assert config.thinking_config.thinking_budget == 500


def test_latest_catalog_and_generation_controls():
    model = AISTUDIO_MODEL_DICT["gemini-3.8-flash"]
    assert model["thinking"]["reasoning_effort"] == ["low", "medium", "high"]
    assert model["pricing"]["input_text"] == 0.75
    assert model["pricing"]["cached_input_text"] == 0.075
    assert model["pricing"]["output"] == 3.75
    assert model["supports_native_websearch"] is True
    assert "lyria-3.5" in AISTUDIO_MODELS_NOT_SUPPORTED
    assert {
        "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.8-live",
        "gemini-3.5-live-translate-preview", "veo-3.1-generate-preview",
        "gemini-embedding-2", "gemini-embedding-2-preview",
    } <= set(AISTUDIO_MODELS_NOT_SUPPORTED)
    for model_name in ("gemini-3.8-flash", "gemini-2.5-flash"):
        schema = schemas.get_parameters_schema_filled(
            {"temperature": 0.5}, model_name=model_name
        )
        keys = {field.key for section in schema.sections for field in section.fields}
        assert ("settings.temperature" in keys) == (model_name == "gemini-2.5-flash")
        assert "settings.max_output_tokens" not in keys


def test_auxiliary_generation_uses_model_thinking_level(monkeypatch):
    generate = MagicMock(return_value=SimpleNamespace(text="A title"))
    monkeypatch.setattr(
        utils,
        "get_aistudio_client",
        lambda *a, **k: SimpleNamespace(
            models=SimpleNamespace(generate_content=generate)
        ),
    )
    monkeypatch.setattr(utils, "create_llm_generation_statistic", lambda *a, **k: None)
    assert (
        utils.google_aistudio_title_generation(
            MagicMock(),
            "gemini-3.8-flash",
            "Hello",
            "Summarize",
            byok={"api_key": "test"},
            model_settings={"reasoning_effort": "low"},
        )
        == "A title"
    )
    assert generate.call_args.kwargs["config"].thinking_config.thinking_level == "LOW"


def test_lyria_35_discovery_and_interactions_generation(monkeypatch):
    audio = b"test mp3 audio"
    create = MagicMock(
        return_value=SimpleNamespace(
            steps=[
                SimpleNamespace(
                    type="model_output",
                    content=[
                        SimpleNamespace(type="text", text="[Verse] Hello"),
                        SimpleNamespace(
                            type="audio",
                            data=base64.b64encode(audio).decode(),
                            mime_type="audio/mpeg",
                        ),
                    ],
                ),
            ]
        )
    )
    client = SimpleNamespace(
        interactions=SimpleNamespace(create=create),
        models=SimpleNamespace(
            list=lambda: [
                SimpleNamespace(name="models/lyria-3.5", display_name="Lyria 3.5")
            ]
        ),
    )
    monkeypatch.setattr(music_generation, "get_aistudio_client", lambda *a, **k: client)
    provider = SimpleNamespace(api_key="test", settings={})
    models = music_generation.get_google_aistudio_music_generation_models(provider)
    assert models[0]["id"] == "lyria-3.5"
    capabilities = music_generation.get_google_aistudio_music_model_capabilities(
        "lyria-3.5"
    )
    assert capabilities["response_formats"] == ["mp3"]
    assert capabilities["max_reference_images"] == 10
    buffer = BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    result = music_generation.generate_music_google_aistudio(
        provider,
        "models/lyria-3.5",
        "An instrumental song",
        config={"response_format": "wav"},
        reference_images=[{"bytes": buffer.getvalue()}],
    )
    request = create.call_args.kwargs
    assert request["model"] == "lyria-3.5"
    assert request["store"] is False
    assert request["input"][0] == {"type": "text", "text": "An instrumental song"}
    assert base64.b64decode(request["input"][1]["data"]) == buffer.getvalue()
    assert result["audio_bytes"] == audio
    assert result["text_content"] == "[Verse] Hello"
    assert result["response_format"] == "mp3"
    assert result["cost"] == 0.08


@pytest.mark.parametrize(
    "model,output_price",
    [("gemini-3.8-flash-tts", 9), ("gemini-3.8-flash-lite-tts", 6)],
)
@pytest.mark.parametrize("multiple_speakers", [False, True])
def test_gemini_38_tts_wire_metadata_and_wav(
    monkeypatch, model, output_price, multiple_speakers
):
    requests = []
    wav = text_to_speech._pcm_to_wav(b"\0\0")

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "data": base64.b64encode(wav).decode(),
                                        "mimeType": "audio/wav",
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 100},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        with genai.Client(
            api_key="test", http_options=types.HttpOptions(httpx_client=http_client)
        ) as client:
            monkeypatch.setattr(
                text_to_speech, "get_aistudio_client", lambda *a, **k: client
            )
            transcript = (
                "Joe: Hello!\nStill here.\nJane: Hi!" if multiple_speakers else "Hello!"
            )
            result = text_to_speech.google_aistudio_generate_audio(
                provider=SimpleNamespace(api_key="test", settings={}),
                model=f"models/{model}",
                voice="Kore",
                input_text=transcript,
                instructions="Cheerful",
                response_format="wav",
                multiple_speakers=multiple_speakers,
            )

    expected_parts = (
        [
            {
                "text": "Hello!\nStill here.",
                "speechMetadata": {"style": "Cheerful", "speaker": "Joe"},
            },
            {"text": "Hi!", "speechMetadata": {"style": "Cheerful", "speaker": "Jane"}},
        ]
        if multiple_speakers
        else [{"text": "Hello!", "speechMetadata": {"style": "Cheerful"}}]
    )
    assert requests[0]["contents"] == [{"role": "user", "parts": expected_parts}]
    speech_config = requests[0]["generationConfig"]["speechConfig"]
    if multiple_speakers:
        assert [
            item["speaker"]
            for item in speech_config["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]
        ] == ["Joe", "Jane"]
    else:
        assert speech_config == {"voiceConfig": {"voice": "Kore"}}
    assert result["audio_bytes"] == wav
    assert result["cost"] == pytest.approx((0.5 + output_price) * 100 / 1_000_000)


def test_gemini_38_tts_fallback_discovery(monkeypatch):
    monkeypatch.setattr(
        text_to_speech,
        "get_aistudio_client",
        lambda *a, **k: SimpleNamespace(models=SimpleNamespace(list=lambda: [])),
    )
    assert [
        item["id"]
        for item in text_to_speech.google_aistudio_text_to_speech_models_list(
            api_key="test"
        )
    ][:2] == [
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
    ]
