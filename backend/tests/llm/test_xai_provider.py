from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest
from app.admin.settings.schema_categories.audio_generation import AudioGenerationSettings
from app.admin.settings.schema_categories.video_generation import VideoGenerationSettings
from app.llm.openai import utils as openai_utils
from app.llm.openai.provider_types import (
    OPENAI_MANUAL_MODEL_PROVIDER_TYPES,
    OPENAI_RESPONSES_PROVIDER_TYPES,
)
from app.llm.openai.schemas import get_openai_model_schema, get_parameters_schema_filled
from app.llm.openai.utils import (
    _apply_openai_simple_generation_settings,
    _apply_provider_reported_cost_meta,
    _record_openai_generation_stat,
    calculate_openai_token_costs,
)
from app.llm.provider_request import (
    REQUEST_TYPE_CHAT,
    ProviderRequest,
    call_provider_chat,
)
from app.llm.schemas import (
    PROVIDER_BYOK_PAYLOAD_MODELS,
    PROVIDER_MODEL_SETTINGS_MODELS,
    PROVIDER_SETTINGS_MODELS,
    ProviderEnum,
)
from app.llm.xai import (
    common,
    image_generation,
    text_to_speech,
    transcription,
    video_generation,
)
from app.llm.xai.model_list import (
    XAI_CATALOG_LAST_VERIFIED,
    XAI_COMPLETION_MODELS,
    XAI_MODEL_DICT,
    get_xai_model_capabilities,
)
from app.llm.xai.schemas import XAI_DEFAULT_BASE_URL, XAISettings
from app.network.policy import OutboundRequestBlockedError
from app.tools.video_generation import utils as video_generation_utils


class _Response:
    """Small requests.Response stand-in used by native xAI adapter tests."""

    def __init__(
        self,
        *,
        payload: dict | None = None,
        content: bytes = b"",
        content_type: str = "application/json",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload or {}
        self.content = content
        self.headers = {"content-type": content_type, **(headers or {})}
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.is_redirect = status_code in {301, 302, 303, 307, 308}
        self.closed = False

    def json(self):
        return self._payload

    def iter_content(self, chunk_size: int):
        """Stream bytes using the subset consumed by the secure downloader."""
        del chunk_size
        yield self.content

    def close(self):
        """Record cleanup so redirect and success paths can be asserted."""
        self.closed = True


class _EmptyQuery:
    """Minimal empty SQLAlchemy query used by schema construction tests."""

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _EmptyDB:
    """Provide the read-only query surface used by shared model schemas."""

    def query(self, *_args, **_kwargs):
        return _EmptyQuery()


def _provider() -> SimpleNamespace:
    """Return the provider surface consumed by native xAI adapters."""
    return SimpleNamespace(
        api_key="xai-test-key",
        settings={
            "base_url": XAI_DEFAULT_BASE_URL,
            "timeout": 60,
            "custom_headers": ["X-Gateway: test"],
        },
    )


def test_xai_is_registered_as_a_responses_and_manual_model_provider():
    """Provider CRUD, BYOK, and chat must share one first-class identity."""
    assert ProviderEnum.xai in PROVIDER_SETTINGS_MODELS
    assert ProviderEnum.xai in PROVIDER_MODEL_SETTINGS_MODELS
    assert ProviderEnum.xai in PROVIDER_BYOK_PAYLOAD_MODELS
    assert ProviderEnum.xai.value in OPENAI_RESPONSES_PROVIDER_TYPES
    assert ProviderEnum.xai.value in OPENAI_MANUAL_MODEL_PROVIDER_TYPES
    assert XAISettings().base_url == XAI_DEFAULT_BASE_URL
    # Old payloads may still contain the removed field. Pydantic drops it so a
    # stale import or browser cache cannot restore a provider-specific timeout.
    assert "timeout" not in XAISettings(timeout=1).model_dump()


def test_xai_chat_dispatches_through_the_responses_adapter():
    """xAI chat reuses the Responses implementation without losing its type."""
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return iter(['{"t":"done"}\n'])

    model = SimpleNamespace(
        provider=ProviderEnum.xai.value,
        provider_id="xai-provider",
        model_name="grok-4.5",
    )
    settings_override = {
        "enabled_tools": ["web_search", "code_execution"],
        "_runtime_enabled_tools": ["web_search", "code_execution"],
    }
    instruction_sections = [
        {
            "title": "Deep Research Evidence Audit Instructions",
            "content": "Audit every citation.",
        }
    ]
    stream = call_provider_chat(
        ProviderRequest(
            request_type=REQUEST_TYPE_CHAT,
            db=object(),
            provider=model.provider,
            model=model,
            chat_history=[],
            user_id="user-1",
            settings_override=settings_override,
            system_instruction_sections=instruction_sections,
            assistant_metadata={"deep_research": True},
            extra={
                "chat_id": "chat-1",
                "provider_callables": {ProviderEnum.xai.value: fake_chat},
            },
        )
    )

    assert list(stream) == ['{"t":"done"}\n']
    assert calls[0]["openai_provider_type"] == ProviderEnum.xai.value
    assert calls[0]["db_model"] is model
    assert calls[0]["settings_override"] == settings_override
    assert calls[0]["system_instruction_sections"] == instruction_sections
    assert calls[0]["assistant_metadata"] == {"deep_research": True}


def test_xai_catalog_resolves_current_models_aliases_and_capabilities():
    """Every documented alias should inherit its canonical model metadata."""
    grok_46 = get_xai_model_capabilities("grok-4.6")
    grok_45 = get_xai_model_capabilities("grok-4.5-latest")
    grok_43 = get_xai_model_capabilities("grok-latest")
    multi_agent = get_xai_model_capabilities("grok-4.20-multi-agent")

    assert XAI_CATALOG_LAST_VERIFIED == "2026-09-01"
    assert grok_46 is XAI_MODEL_DICT["grok-4.6"]
    assert grok_46["input_token_limit"] == 500_000
    assert grok_46["thinking"]["thinking_effort"] == [
        "low",
        "medium",
        "high",
        "xhigh",
    ]
    assert grok_46["thinking"]["default_thinking_effort"] == "high"
    assert grok_46["pricing"]["standard"] == {
        "input": 2.00,
        "cached_input": 0.50,
        "output": 6.00,
    }
    assert grok_46["pricing"]["high_context_pricing"]["standard"] == {
        "input": 4.00,
        "cached_input": 1.00,
        "output": 12.00,
    }
    assert "grok-4.6" in XAI_COMPLETION_MODELS
    assert grok_45 is XAI_MODEL_DICT["grok-4.5"]
    assert grok_45["input_token_limit"] == 500_000
    assert grok_45["thinking"]["thinking_effort"] == ["low", "medium", "high"]
    assert grok_43 is XAI_MODEL_DICT["grok-4.3"]
    assert multi_agent["thinking"]["thinking_effort"][-1] == "xhigh"
    assert "grok-4.20-0309-non-reasoning" in XAI_COMPLETION_MODELS


def test_xai_retired_reasoning_aliases_default_to_documented_low_effort():
    """Redirected reasoning aliases must retain xAI's documented semantics."""
    reasoning = get_xai_model_capabilities("grok-4-fast-reasoning")
    non_reasoning = get_xai_model_capabilities("grok-4-fast-non-reasoning")

    assert reasoning["thinking"]["default_thinking_effort"] == "low"
    assert non_reasoning["thinking"]["default_thinking_effort"] == "none"
    # Alias-specific overrides must not mutate the canonical Grok 4.3 entry.
    assert XAI_MODEL_DICT["grok-4.3"]["thinking"]["default_thinking_effort"] == "none"


def test_xai_live_model_list_preserves_aliases_and_pricing_metadata(monkeypatch):
    """Canonical rows must win collisions with aliases discovered before them."""
    model = SimpleNamespace(
        id="grok-4.5",
        created=1_786_320_000,
        object="model",
        owned_by="xai",
        model_extra={},
        model_dump=lambda: {
            "aliases": ["grok-4.5-latest", "grok-next"],
            "context_length": 500_000,
            "prompt_text_token_price": 20_000,
            "cached_prompt_text_token_price": 3_000,
            "completion_text_token_price": 60_000,
        },
    )
    later_canonical = SimpleNamespace(
        id="grok-next",
        created=1_786_320_001,
        object="model",
        owned_by="xai",
        model_extra={},
        model_dump=lambda: {
            "aliases": [],
            "context_length": 1_000_000,
            "completion_text_token_price": 99_000,
        },
    )
    fake_client = SimpleNamespace(
        models=SimpleNamespace(list=lambda **_kwargs: [model, later_canonical]),
    )
    monkeypatch.setattr(
        openai_utils,
        "_resolve_openai_client_context",
        lambda *_args, **_kwargs: {
            "client_kwargs": {},
            "request_options": {},
        },
    )
    monkeypatch.setattr(openai_utils, "OpenAI", lambda **_kwargs: fake_client)

    models = openai_utils.list_models_openai(
        object(),
        byok={"api_key": "xai-test-key"},
        openai_provider_type=ProviderEnum.xai.value,
    )

    assert [item["id"] for item in models] == [
        "grok-4.5",
        "grok-next",
        "grok-4.5-latest",
    ]
    assert models[0]["context_length"] == 500_000
    assert "canonical_id" not in models[1]
    assert models[1]["context_length"] == 1_000_000
    assert models[1]["completion_text_token_price"] == 99_000
    assert models[2]["canonical_id"] == "grok-4.5"
    assert models[2]["completion_text_token_price"] == 60_000


def test_xai_catalog_costs_apply_long_context_and_priority_rates():
    """The shared Responses calculator must use xAI rather than OpenAI rates."""
    common_kwargs = {
        "model_name": "grok-4.5-latest",
        "provider_type": ProviderEnum.xai.value,
        "input_tokens": 200_000,
        "cached_input_tokens": 0,
        "output_tokens": 10_000,
        "reasoning_tokens": 5_000,
        "native_websearch_tool_calls_count": 2,
    }
    costs = calculate_openai_token_costs(
        **common_kwargs,
        service_tier="default",
    )
    priority_costs = calculate_openai_token_costs(
        **common_kwargs,
        service_tier="priority",
    )

    # At the documented inclusive 200K threshold, all tokens use long-context
    # rates: .8 input + .12 output + .01 for two hosted searches.
    assert costs["total_costs"] == pytest.approx(0.93)
    # Priority doubles token rates; hosted-tool rates remain unchanged.
    assert priority_costs["total_costs"] == pytest.approx(1.85)


def test_xai_request_settings_use_native_cache_and_priority_contract():
    """xAI requests should get valid vendor controls and omit OpenAI-only ones."""
    request_kwargs = {"model": "grok-4.5"}
    _apply_openai_simple_generation_settings(
        request_kwargs,
        {
            "priority_processing": "priority",
            "prompt_cache_override": True,
            "prompt_cache_key": "conversation-1",
            "prompt_cache_ttl": "30m",
            "send_user_identifier": True,
            "frequency_penalty": 0.5,
            "presence_penalty": 0.5,
            "reasoning_effort": "high",
            "store": False,
        },
        user_id="user-1",
        openai_provider_type=ProviderEnum.xai.value,
    )

    assert request_kwargs["service_tier"] == "priority"
    assert request_kwargs["prompt_cache_key"] == "conversation-1"
    assert request_kwargs["reasoning"] == {"effort": "high"}
    assert request_kwargs["store"] is False
    assert "prompt_cache_options" not in request_kwargs.get("extra_body", {})
    assert "safety_identifier" not in request_kwargs
    assert "frequency_penalty" not in request_kwargs
    assert "presence_penalty" not in request_kwargs


def test_xai_model_parameter_schema_exposes_supported_service_tiers():
    """The model form should offer xAI standard and priority processing."""
    schema = get_parameters_schema_filled(
        {},
        "grok-4.5",
        openai_provider_type=ProviderEnum.xai.value,
    )
    priority_field = next(
        field
        for section in schema.sections
        for field in section.fields
        if field.key == "settings.priority_processing"
    )

    assert [option.value for option in priority_field.options] == [
        "standard",
        "priority",
    ]


def test_xai_model_schema_is_populated_from_the_xai_catalog_end_to_end():
    """Creating a Grok model should prefill metadata and remove invalid fields."""
    schema = get_openai_model_schema(
        _EmptyDB(),
        None,
        "grok-4.6",
        openai_provider_type=ProviderEnum.xai.value,
    )
    fields = {
        field.key: field for section in schema.sections for field in section.fields
    }

    assert fields["name"].value == "Grok 4.6"
    assert fields["settings.input_token_limit"].value == 500_000
    assert fields["settings.reasoning_effort"].default == "high"
    assert [option.value for option in fields["settings.reasoning_effort"].options] == [
        "low",
        "medium",
        "high",
        "xhigh",
    ]
    assert fields["settings.store"].value is True
    assert "settings.prompt_cache_key" in fields
    assert "settings.prompt_cache_ttl" not in fields
    assert "settings.send_user_identifier" not in fields
    assert "settings.frequency_penalty" not in fields
    assert "settings.presence_penalty" not in fields


def test_xai_provider_reported_cost_is_authoritative():
    """Exact billed ticks should become the statistic total without rounding."""
    meta = {}
    usage = SimpleNamespace(cost_in_usd_ticks=37_756_000)

    found = _apply_provider_reported_cost_meta(
        meta,
        usage,
        provider_type=ProviderEnum.xai.value,
    )

    assert found is True
    assert meta["total_costs"] == pytest.approx(0.0037756)
    assert meta["cost_in_usd_ticks"] == 37_756_000
    assert meta["pricing_source"] == "provider_usage"


def test_xai_statistic_keeps_exact_total_over_catalog_estimate(monkeypatch):
    """Persisted analytics must prefer provider billing over fallback math."""
    captured = {}
    monkeypatch.setattr(
        openai_utils,
        "create_llm_generation_statistic",
        lambda *_args, **kwargs: captured.update(kwargs),
    )
    exact_meta = {
        "total_costs": 0.0037756,
        "cost_in_usd_ticks": 37_756_000,
        "pricing_source": "provider_usage",
    }

    _record_openai_generation_stat(
        object(),
        model_name="grok-4.5",
        model_id="model-1",
        provider=ProviderEnum.xai.value,
        provider_id="provider-1",
        category="chat",
        meta=exact_meta,
        success=True,
        error=False,
        cost_kwargs={
            "model_name": "grok-4.5",
            "provider_type": ProviderEnum.xai.value,
            "service_tier": "standard",
            "input_tokens": 1_000_000,
            "cached_input_tokens": 0,
            "output_tokens": 1_000_000,
            "reasoning_tokens": 0,
            "native_websearch_tool_calls_count": 0,
        },
    )

    assert captured["meta"]["total_costs"] == pytest.approx(0.0037756)
    assert captured["meta"]["input_tokens_cost"] == pytest.approx(4.0)
    assert captured["meta"]["output_tokens_cost"] == pytest.approx(12.0)


def test_xai_image_generation_preserves_jpeg_type_and_provider_cost(monkeypatch):
    """Imagine output metadata must match the downloaded/generated file bytes."""
    jpeg_bytes = b"\xff\xd8\xff\xe0xai-jpeg"
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return _Response(
            payload={
                "data": [{"b64_json": base64.b64encode(jpeg_bytes).decode("ascii")}],
                "usage": {"cost_in_usd_ticks": 200_000_000},
            }
        )

    monkeypatch.setattr(image_generation.requests, "post", fake_post)

    result = image_generation.generate_image(
        _provider(),
        "grok-imagine-image-quality",
        "A cat on a rocket",
        {"aspect_ratio": "16:9", "resolution": "2k"},
    )

    assert captured["url"] == f"{XAI_DEFAULT_BASE_URL}/images/generations"
    assert captured["json"]["response_format"] == "url"
    assert captured["json"]["resolution"] == "2k"
    assert captured["headers"]["X-Gateway"] == "test"
    assert result["image_bytes"] == jpeg_bytes
    assert result["file_type"] == "image/jpeg"
    assert result["extension"] == ".jpg"
    assert result["cost"] == pytest.approx(0.02)


def test_xai_image_20_sends_quality_and_uses_current_static_price(monkeypatch):
    png_bytes = b"\x89PNG\r\n\x1a\nxai-png"
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return _Response(
            payload={
                "data": [{"b64_json": base64.b64encode(png_bytes).decode("ascii")}],
            }
        )

    monkeypatch.setattr(image_generation.requests, "post", fake_post)

    result = image_generation.generate_image(
        _provider(),
        "grok-imagine-image-2.0",
        "A cinematic landscape",
        {"aspect_ratio": "21:9", "resolution": "2k", "quality": "medium"},
    )

    assert captured["json"]["quality"] == "medium"
    assert captured["json"]["aspect_ratio"] == "21:9"
    assert result["cost"] == pytest.approx(0.08)
    assert result["cost_details"]["pricing_source"] == "static_catalog"


def test_xai_single_image_edit_uses_the_native_url_object(monkeypatch):
    """Single-image edits omit the multi-image type discriminator."""
    captured = {}

    def fake_request(_provider, *, endpoint, payload):
        captured.update({"endpoint": endpoint, "payload": payload})
        return {"image_bytes": b"image"}

    monkeypatch.setattr(image_generation, "_request_image", fake_request)

    image_generation.edit_image(
        _provider(),
        "grok-imagine-image",
        "Improve this image",
        {},
        [{"mime_type": "image/png", "bytes": b"source"}],
    )

    assert captured["endpoint"] == "images/edits"
    assert captured["payload"]["image"] == {
        "url": captured["payload"]["image"]["url"],
    }
    assert captured["payload"]["image"]["url"].startswith("data:image/png;base64,")


def test_xai_image_20_edit_accepts_five_reference_images(monkeypatch):
    captured = {}

    def fake_request(_provider, *, endpoint, payload):
        captured.update({"endpoint": endpoint, "payload": payload})
        return {"image_bytes": b"image"}

    monkeypatch.setattr(image_generation, "_request_image", fake_request)

    image_generation.edit_image(
        _provider(),
        "grok-imagine-image-2.0",
        "Combine these references",
        {"quality": "auto", "resolution": "2k"},
        [{"mime_type": "image/png", "bytes": bytes([index])} for index in range(6)],
    )

    assert captured["endpoint"] == "images/edits"
    assert len(captured["payload"]["images"]) == 5
    assert captured["payload"]["quality"] == "auto"
    assert captured["payload"]["resolution"] == "2k"


def test_xai_result_download_blocks_private_network_targets():
    """Both image and video result paths reject private network targets."""
    with pytest.raises(OutboundRequestBlockedError):
        image_generation._download_xai_image(
            "https://127.0.0.1/internal/image.png",
            timeout=30,
        )
    with pytest.raises(OutboundRequestBlockedError):
        video_generation_utils._download_video_from_url(
            "https://127.0.0.1/internal/video.mp4",
            _provider(),
            ProviderEnum.xai.value,
            timeout_seconds=30,
            max_retries=0,
        )


def test_xai_result_download_streams_public_redirects_without_leaking_auth(monkeypatch):
    """Legitimate CDN redirects work while API credentials stay host-bound."""
    redirect = _Response(
        status_code=302,
        headers={"Location": "https://cdn.example/result.png"},
    )
    image = _Response(
        content=b"\x89PNG\r\n\x1a\nxai",
        content_type="image/png",
        headers={"content-length": "11"},
    )
    calls = []

    def fake_public_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return redirect if len(calls) == 1 else image

    monkeypatch.setattr(common, "public_web_request", fake_public_request)

    content, content_type, final_url = common.download_xai_result_url(
        "https://api.x.ai/v1/results/image",
        operation="image",
        expected_content_prefix="image/",
        max_bytes=1024,
        timeout=30,
        authorized_hosts={"api.x.ai"},
        authorized_headers={"Authorization": "Bearer secret"},
    )

    assert content.startswith(b"\x89PNG")
    assert content_type == "image/png"
    assert final_url == "https://cdn.example/result.png"
    assert calls[0][2]["headers"] == {"Authorization": "Bearer secret"}
    assert calls[1][2]["headers"] is None
    assert all(call[2]["allow_redirects"] is False for call in calls)
    assert redirect.closed is True
    assert image.closed is True


def test_xai_result_download_revalidates_redirects_and_enforces_stream_limit(
    monkeypatch,
):
    """Redirects cannot pivot private, and unknown-length bodies stay bounded."""
    original_public_request = common.public_web_request
    redirect = _Response(
        status_code=302,
        headers={"Location": "https://127.0.0.1/internal.png"},
    )
    calls = 0

    def redirect_then_real(method, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return redirect
        return original_public_request(method, url, **kwargs)

    monkeypatch.setattr(common, "public_web_request", redirect_then_real)
    with pytest.raises(OutboundRequestBlockedError):
        common.download_xai_result_url(
            "https://cdn.example/redirect",
            operation="image",
            expected_content_prefix="image/",
            max_bytes=1024,
            timeout=30,
        )
    assert redirect.closed is True

    oversized = _Response(content=b"too-many-bytes", content_type="image/png")
    monkeypatch.setattr(
        common,
        "public_web_request",
        lambda *_args, **_kwargs: oversized,
    )
    with pytest.raises(RuntimeError, match="exceeded the size limit"):
        common.download_xai_result_url(
            "https://cdn.example/large.png",
            operation="image",
            expected_content_prefix="image/",
            max_bytes=5,
            timeout=30,
        )
    assert oversized.closed is True


def test_xai_reference_video_uses_model_specific_limits_and_payload_shape():
    """The original model accepts seven references for up to 15 seconds."""
    references = [
        {"mime_type": "image/png", "bytes": b"image-" + bytes([index])}
        for index in range(8)
    ]

    payload = video_generation._build_payload(
        "grok-imagine-video",
        "Keep the character consistent",
        {
            "duration_seconds": 15,
            "aspect_ratio": "9:16",
            "resolution": "720p",
        },
        references,
    )

    assert payload["duration"] == 15
    assert payload["aspect_ratio"] == "9:16"
    assert payload["resolution"] == "720p"
    assert len(payload["reference_images"]) == 7
    assert all(
        item["url"].startswith("data:image/png;base64,")
        for item in payload["reference_images"]
    )
    assert VideoGenerationSettings(duration_seconds=1).duration_seconds == 1

    clamped_payload = video_generation._build_payload(
        "grok-imagine-video",
        "Keep the character consistent",
        {"duration_seconds": 15, "resolution": "1080p"},
        references,
    )
    assert clamped_payload["duration"] == 15
    assert clamped_payload["resolution"] == "720p"


def test_xai_video_15_requires_an_image_and_exposes_1080p():
    """Video 1.5 is image-to-video only and supports its 1080p preset."""
    one_reference = [{"mime_type": "image/png", "bytes": b"image"}]
    image_payload = video_generation._build_payload(
        "grok-imagine-video-1.5",
        "Animate this",
        {"resolution": "1080p"},
        one_reference,
    )
    schema = video_generation.get_video_generation_schema_part_2(
        "grok-imagine-video-1.5"
    )
    resolution_field = next(
        field
        for section in schema.sections
        for field in section.fields
        if field.key == "resolution"
    )
    reference_field = next(
        field
        for section in schema.sections
        for field in section.fields
        if field.key == "enable_reference_files"
    )

    with pytest.raises(ValueError, match="requires a reference image"):
        video_generation._build_payload(
            "grok-imagine-video-1.5",
            "A sunrise",
            {"resolution": "1080p"},
            [],
        )
    assert image_payload["resolution"] == "1080p"
    assert "image" in image_payload
    assert "reference_images" not in image_payload
    assert [option.value for option in resolution_field.options] == [
        "480p",
        "720p",
        "1080p",
    ]
    assert reference_field.default is True


def test_xai_media_model_lists_use_capability_specific_endpoints(monkeypatch):
    """Admin pickers should discover current models and aliases dynamically."""
    urls = []

    def fake_get(url, **_kwargs):
        urls.append(url)
        if url.endswith("/image-generation-models"):
            return _Response(
                payload={
                    "models": [
                        {
                            "id": "grok-imagine-image",
                            "aliases": ["grok-imagine-image-quality"],
                        }
                    ]
                }
            )
        return _Response(
            payload={
                "models": [
                    {
                        "id": "grok-imagine-video",
                        "aliases": ["grok-imagine-video-preview"],
                    }
                ]
            }
        )

    monkeypatch.setattr(image_generation.requests, "get", fake_get)

    assert image_generation.list_image_models(_provider()) == [
        {"id": "grok-imagine-image"},
        {"id": "grok-imagine-image-quality"},
    ]
    assert video_generation.list_video_models(_provider()) == [
        {"id": "grok-imagine-video"},
        {"id": "grok-imagine-video-preview"},
    ]
    assert urls == [
        f"{XAI_DEFAULT_BASE_URL}/image-generation-models",
        f"{XAI_DEFAULT_BASE_URL}/video-generation-models",
    ]


def test_xai_tts_sends_native_payload_and_normalizes_binary_mime(monkeypatch):
    """Raw xAI audio should remain browser-playable when the MIME is generic."""
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return _Response(
            content=b"mp3-bytes",
            content_type="application/octet-stream",
        )

    monkeypatch.setattr(text_to_speech.requests, "post", fake_post)

    result = text_to_speech.xai_generate_audio(
        provider=_provider(),
        voice="rex",
        input_text="hello",
        response_format="mp3",
        language="de",
        sample_rate=44100,
        bit_rate=192000,
        speed=1.2,
        optimize_streaming_latency=2,
        text_normalization=True,
    )

    assert captured["url"] == f"{XAI_DEFAULT_BASE_URL}/tts"
    assert captured["json"] == {
        "text": "hello",
        "voice_id": "rex",
        "language": "de",
        "output_format": {
            "codec": "mp3",
            "sample_rate": 44100,
            "bit_rate": 192000,
        },
        "speed": 1.2,
        "optimize_streaming_latency": 2,
        "text_normalization": True,
    }
    assert result["file_type"] == "audio/mpeg"
    assert result["extension"] == "mp3"
    assert result["cost"] == pytest.approx(0.000075)


def test_xai_tts_schema_exposes_playback_relevant_batch_controls(monkeypatch):
    """The admin wizard should expose xAI's playback-relevant TTS controls."""
    monkeypatch.setattr(
        text_to_speech,
        "_voice_entries",
        lambda _provider: [
            {
                "id": "eve",
                "name": "Eve",
                "description": "",
                "category": "built-in",
                "labels": {"language": "multilingual"},
                "language": "multilingual",
            }
        ],
    )
    monkeypatch.setattr(
        text_to_speech,
        "_custom_voice_entries",
        lambda _provider: [
            {
                "id": "custom01",
                "name": "Studio Narrator",
                "description": "",
                "category": "",
                "labels": {"language": "en"},
                "language": "en",
            }
        ],
    )

    schema = text_to_speech.get_audio_generation_schema_part_2(
        text_to_speech.XAI_TTS_MODEL,
        provider=_provider(),
    )
    fields = {
        field.key: field for section in schema.sections for field in section.fields
    }

    assert set(fields) == {
        "voice",
        "response_format",
        "language",
        "sample_rate",
        "bit_rate",
        "speed",
        "optimize_streaming_latency",
        "text_normalization",
    }
    assert fields["voice"].default == "eve"
    assert [option.value for option in fields["voice"].options] == [
        "eve",
        "custom01",
    ]
    assert fields["speed"].attributes.min == pytest.approx(0.7)
    assert fields["speed"].attributes.max == pytest.approx(1.5)
    assert fields["sample_rate"].default == "24000"
    assert fields["bit_rate"].default == "128000"
    assert fields["optimize_streaming_latency"].default == "0"
    assert [
        option.value for option in fields["optimize_streaming_latency"].options
    ] == ["0", "1", "2"]
    assert fields["text_normalization"].type == "boolean"


def test_xai_voice_search_merges_built_in_and_custom_voices(monkeypatch):
    """Team custom voices should appear alongside built-ins in the shared picker."""
    monkeypatch.setattr(
        text_to_speech,
        "_voice_entries",
        lambda _provider: [
            {
                "id": "eve",
                "name": "Eve",
                "description": "",
                "labels": {"language": "multilingual"},
            }
        ],
    )
    monkeypatch.setattr(
        text_to_speech,
        "_custom_voice_entries",
        lambda _provider: [
            {
                "id": "nlbqfwie",
                "name": "Friendly Narrator",
                "description": "Warm narration",
                "labels": {"tone": "warm", "language": "en"},
            }
        ],
    )

    result = text_to_speech.search_xai_voices(
        _provider(),
        search="warm",
        page_size=24,
    )

    assert [voice["id"] for voice in result["voices"]] == ["nlbqfwie"]
    assert result["has_more"] is False


def test_xai_non_mp3_payload_omits_mp3_only_bit_rate(monkeypatch):
    """xAI must not receive the MP3-only bit-rate option with lossless WAV."""
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return _Response(content=b"wav-bytes", content_type="audio/wav")

    monkeypatch.setattr(text_to_speech.requests, "post", fake_post)

    text_to_speech.xai_generate_audio(
        provider=_provider(),
        voice="ara",
        input_text="hello",
        response_format="wav",
        bit_rate=12345,
    )

    assert captured["json"]["output_format"] == {
        "codec": "wav",
        "sample_rate": 24000,
    }


@pytest.mark.parametrize("codec", ["pcm", "mulaw", "alaw"])
def test_xai_raw_tts_codecs_survive_persisted_admin_validation(codec):
    """Every format offered by the xAI picker must be saveable end to end."""
    settings = AudioGenerationSettings(
        provider_id="xai-provider",
        model_name=text_to_speech.XAI_TTS_MODEL,
        voice="eve",
        response_format=codec,
    )

    assert settings.response_format == codec


def test_xai_native_tts_settings_survive_persisted_admin_validation():
    """All xAI synthesis controls should retain their validated native types."""
    settings = AudioGenerationSettings(
        provider_id="xai-provider",
        model_name=text_to_speech.XAI_TTS_MODEL,
        voice="eve",
        response_format="mp3",
        language="pt-BR",
        sample_rate=48000,
        bit_rate=192000,
        speed=0.85,
        optimize_streaming_latency=2,
        text_normalization=True,
    )

    assert settings.language == "pt-BR"
    assert settings.sample_rate == 48000
    assert settings.bit_rate == 192000
    assert settings.speed == pytest.approx(0.85)
    assert settings.optimize_streaming_latency == 2
    assert settings.text_normalization is True


def test_xai_batch_transcription_places_fields_before_the_file(monkeypatch):
    """xAI's multipart parser requires options before the uploaded file part."""
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def aclose(self):
            captured["closed"] = True

        async def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return _Response(payload={"text": "hello world"})

    monkeypatch.setattr(transcription.httpx, "AsyncClient", FakeAsyncClient)

    text = asyncio.run(
        transcription.transcribe_audio_bytes(
            _provider(),
            b"audio-bytes",
            "sample.mp3",
        )
    )

    assert text == "hello world"
    assert captured["url"] == f"{XAI_DEFAULT_BASE_URL}/stt"
    assert [part[0] for part in captured["files"]] == ["format", "file"]
    assert captured["files"][0][1] == (None, "false")
    assert "Content-Type" not in captured["headers"]
    assert captured["client_kwargs"]["trust_env"] is False
    assert captured["closed"] is True
