from __future__ import annotations

from typing import Any


OPENAI_GPT_4O_MINI_TTS_DOCS_URL = "https://developers.openai.com/api/docs/models/gpt-4o-mini-tts"
OPENAI_TTS_1_DOCS_URL = "https://developers.openai.com/api/docs/models/tts-1"
OPENAI_TTS_1_HD_DOCS_URL = "https://developers.openai.com/api/docs/models/tts-1-hd"
GOOGLE_AISTUDIO_TTS_PRICING_DOCS_URL = "https://ai.google.dev/gemini-api/docs/pricing"
ELEVENLABS_TTS_PRICING_DOCS_URL = "https://elevenlabs.io/pricing/api"
ELEVENLABS_TTS_MODELS_DOCS_URL = "https://elevenlabs.io/docs/overview/models"
XAI_TTS_PRICING_DOCS_URL = "https://docs.x.ai/developers/model-capabilities/audio/text-to-speech"


OPENAI_AUDIO_GENERATION_PRICING: dict[str, dict[str, Any]] = {
    "gpt-4o-mini-tts": {
        "pricing_model": "per_million_tokens",
        "input_text": 0.60,
        "output_audio": 12.00,
        "currency": "USD",
        "source_url": OPENAI_GPT_4O_MINI_TTS_DOCS_URL,
    },
    "gpt-4o-mini-tts-2025-12-15": {
        "pricing_model": "per_million_tokens",
        "input_text": 0.60,
        "output_audio": 12.00,
        "currency": "USD",
        "source_url": OPENAI_GPT_4O_MINI_TTS_DOCS_URL,
    },
    "tts-1": {
        "pricing_model": "per_million_characters",
        "input_characters": 15.00,
        "currency": "USD",
        "source_url": OPENAI_TTS_1_DOCS_URL,
    },
    "tts-1-1106": {
        "pricing_model": "per_million_characters",
        "input_characters": 15.00,
        "currency": "USD",
        "source_url": OPENAI_TTS_1_DOCS_URL,
    },
    "tts-1-hd": {
        "pricing_model": "per_million_characters",
        "input_characters": 30.00,
        "currency": "USD",
        "source_url": OPENAI_TTS_1_HD_DOCS_URL,
    },
    "tts-1-hd-1106": {
        "pricing_model": "per_million_characters",
        "input_characters": 30.00,
        "currency": "USD",
        "source_url": OPENAI_TTS_1_HD_DOCS_URL,
    },
}

GOOGLE_AISTUDIO_AUDIO_GENERATION_PRICING: dict[str, dict[str, Any]] = {
    "gemini-3.1-flash-tts-preview": {
        "pricing_model": "per_million_tokens",
        "input_text": 1.00,
        "output_audio": 20.00,
        "currency": "USD",
        "source_url": GOOGLE_AISTUDIO_TTS_PRICING_DOCS_URL,
    },
    "gemini-2.5-flash-preview-tts": {
        "pricing_model": "per_million_tokens",
        "input_text": 0.50,
        "output_audio": 10.00,
        "currency": "USD",
        "source_url": GOOGLE_AISTUDIO_TTS_PRICING_DOCS_URL,
    },
    "gemini-2.5-pro-preview-tts": {
        "pricing_model": "per_million_tokens",
        "input_text": 1.00,
        "output_audio": 20.00,
        "currency": "USD",
        "source_url": GOOGLE_AISTUDIO_TTS_PRICING_DOCS_URL,
    },
}

ELEVENLABS_AUDIO_GENERATION_PRICING: dict[str, dict[str, Any]] = {
    "eleven_v3_conversational": {
        "pricing_model": "per_thousand_characters",
        "input_characters": 0.10,
        "currency": "USD",
        "source_url": ELEVENLABS_TTS_PRICING_DOCS_URL,
    },
    "eleven_v3": {
        "pricing_model": "per_thousand_characters",
        "input_characters": 0.10,
        "currency": "USD",
        "source_url": ELEVENLABS_TTS_PRICING_DOCS_URL,
    },
    "eleven_multilingual_v2": {
        "pricing_model": "per_thousand_characters",
        "input_characters": 0.10,
        "currency": "USD",
        "source_url": ELEVENLABS_TTS_PRICING_DOCS_URL,
    },
    "eleven_flash_v2_5": {
        "pricing_model": "per_thousand_characters",
        "input_characters": 0.05,
        "currency": "USD",
        "source_url": ELEVENLABS_TTS_PRICING_DOCS_URL,
    },
    "eleven_flash_v2": {
        "pricing_model": "per_thousand_characters",
        "input_characters": 0.05,
        "currency": "USD",
        "source_url": ELEVENLABS_TTS_PRICING_DOCS_URL,
    },
    "eleven_turbo_v2_5": {
        "pricing_model": "per_thousand_characters",
        "input_characters": 0.05,
        "currency": "USD",
        "source_url": ELEVENLABS_TTS_PRICING_DOCS_URL,
    },
    "eleven_turbo_v2": {
        "pricing_model": "per_thousand_characters",
        "input_characters": 0.05,
        "currency": "USD",
        "source_url": ELEVENLABS_TTS_PRICING_DOCS_URL,
    },
}

XAI_AUDIO_GENERATION_PRICING: dict[str, dict[str, Any]] = {
    "grok-tts": {
        "pricing_model": "per_million_characters",
        "input_characters": 15.00,
        "currency": "USD",
        "source_url": XAI_TTS_PRICING_DOCS_URL,
    },
}


def _normalize_model_id(model_name: str | None) -> str:
    return str(model_name or "").strip().lower().replace("models/", "", 1)


def _lookup_pricing_dict(provider_type: str | None, model_name: str | None) -> dict[str, Any] | None:
    normalized_provider = str(provider_type or "").strip().lower()
    normalized_model = _normalize_model_id(model_name)
    if not normalized_provider or not normalized_model:
        return None

    if normalized_provider in {"openai_responses", "openai_chat_completions"}:
        normalized_provider = "openai"

    if normalized_provider == "openai":
        pricing = OPENAI_AUDIO_GENERATION_PRICING.get(normalized_model)
    elif normalized_provider == "google_aistudio":
        pricing = GOOGLE_AISTUDIO_AUDIO_GENERATION_PRICING.get(normalized_model)
    elif normalized_provider == "elevenlabs":
        pricing = ELEVENLABS_AUDIO_GENERATION_PRICING.get(normalized_model)
    elif normalized_provider == "xai":
        pricing = XAI_AUDIO_GENERATION_PRICING.get(normalized_model)
    else:
        pricing = None

    return dict(pricing) if isinstance(pricing, dict) else None


def format_audio_generation_pricing(pricing: dict[str, Any] | None) -> str:
    if not isinstance(pricing, dict):
        return ""

    pricing_model = str(pricing.get("pricing_model") or "").strip().lower()
    if pricing_model == "per_thousand_characters":
        amount = pricing.get("input_characters")
        if amount is None:
            return ""
        return f"${float(amount):.2f} / 1K chars"
    if pricing_model == "per_million_characters":
        amount = pricing.get("input_characters")
        if amount is None:
            return ""
        return f"${float(amount):.2f} / 1M chars"
    if pricing_model == "per_million_tokens":
        parts: list[str] = []
        input_text = pricing.get("input_text")
        output_audio = pricing.get("output_audio")
        if input_text is not None:
            parts.append(f"in ${float(input_text):.2f} / 1M text tokens")
        if output_audio is not None:
            parts.append(f"out ${float(output_audio):.2f} / 1M audio tokens")
        return ", ".join(parts)
    return ""


def get_audio_generation_model_pricing(
    provider_type: str | None,
    model_name: str | None,
) -> dict[str, Any] | None:
    pricing = _lookup_pricing_dict(provider_type, model_name)
    if not pricing:
        return None
    pricing["pricing_label"] = format_audio_generation_pricing(pricing)
    return pricing


def get_audio_generation_pricing_metadata(
    provider_type: str | None,
    model_name: str | None,
) -> dict[str, Any]:
    pricing = get_audio_generation_model_pricing(provider_type, model_name)
    if not pricing:
        return {}

    metadata = {
        "pricing": pricing,
        "pricing_label": str(pricing.get("pricing_label") or "").strip(),
        "pricing_model": str(pricing.get("pricing_model") or "").strip(),
        "billing_unit": "characters" if "characters" in str(pricing.get("pricing_model") or "") else "tokens",
        "currency": str(pricing.get("currency") or "USD").strip() or "USD",
        "pricing_source_url": str(pricing.get("source_url") or "").strip() or None,
    }
    return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}


def build_audio_generation_model_option(
    provider_type: str | None,
    model_name: str,
    *,
    label: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    resolved_label = str(label or model_name).strip() or str(model_name or "").strip()
    metadata = get_audio_generation_pricing_metadata(provider_type, model_name)
    pricing_label = str(metadata.get("pricing_label") or "").strip()
    if pricing_label:
        resolved_label = f"{resolved_label} ({pricing_label})"
    return resolved_label, metadata or None


def calculate_audio_generation_cost(
    provider_type: str | None,
    model_name: str | None,
    *,
    input_text: str | None = None,
    input_character_count: int | None = None,
    input_text_tokens: int | None = None,
    output_audio_tokens: int | None = None,
) -> dict[str, Any] | None:
    pricing = get_audio_generation_model_pricing(provider_type, model_name)
    if not pricing:
        return None

    details = {
        "provider_type": str(provider_type or "").strip().lower(),
        "model": str(model_name or "").strip(),
        **get_audio_generation_pricing_metadata(provider_type, model_name),
    }
    pricing_model = str(pricing.get("pricing_model") or "").strip().lower()

    if input_character_count is None and input_text is not None:
        input_character_count = len(str(input_text))

    if pricing_model == "per_thousand_characters":
        characters = max(int(input_character_count or 0), 0)
        details["input_character_count"] = characters
        cost = round((characters / 1_000) * float(pricing.get("input_characters") or 0.0), 10)
        return {"cost": cost, **details}

    if pricing_model == "per_million_characters":
        characters = max(int(input_character_count or 0), 0)
        details["input_character_count"] = characters
        cost = round((characters / 1_000_000) * float(pricing.get("input_characters") or 0.0), 10)
        return {"cost": cost, **details}

    if pricing_model == "per_million_tokens":
        input_tokens = max(int(input_text_tokens or 0), 0)
        output_tokens = max(int(output_audio_tokens or 0), 0)
        details["input_text_tokens"] = input_tokens
        details["output_audio_tokens"] = output_tokens

        has_input_price = pricing.get("input_text") is not None
        has_output_price = pricing.get("output_audio") is not None

        input_cost = round((input_tokens / 1_000_000) * float(pricing.get("input_text") or 0.0), 10) if has_input_price and input_tokens else 0.0
        output_cost = round((output_tokens / 1_000_000) * float(pricing.get("output_audio") or 0.0), 10) if has_output_price and output_tokens else 0.0

        if input_tokens or output_tokens:
            return {
                "cost": round(input_cost + output_cost, 10),
                "input_cost": input_cost,
                "output_cost": output_cost,
                **details,
            }

        details["cost_unavailable_reason"] = "Provider response did not include token usage for this TTS request."
        return details

    return details
