from __future__ import annotations

from typing import Any

from app.utils.schemas import Sections


PROVIDER_URL_SUGGESTIONS: dict[str, list[dict[str, str]]] = {
    "xai": [
        {"name": "xAI", "url": "https://api.x.ai/v1"},
    ],
    "openai_responses": [
        {"name": "OpenAI", "url": "https://api.openai.com/v1"},
        {"name": "OpenRouter", "url": "https://openrouter.ai/api/v1"},
        {"name": "Groq", "url": "https://api.groq.com/openai/v1"},
        {"name": "Together AI", "url": "https://api.together.xyz/v1"},
        {"name": "DeepSeek", "url": "https://api.deepseek.com/v1"},
        {"name": "Fireworks AI", "url": "https://api.fireworks.ai/inference/v1"},
        {"name": "xAI", "url": "https://api.x.ai/v1"},
        {"name": "Cerebras", "url": "https://api.cerebras.ai/v1"},
        {"name": "LM Studio", "url": "http://localhost:1234/v1"},
        {"name": "LocalAI", "url": "http://localhost:8080/v1"},
        {"name": "Ollama (OpenAI Compatible)", "url": "http://localhost:11434/v1"},
    ],
    "openai_chat_completions": [
        {"name": "OpenAI", "url": "https://api.openai.com/v1"},
        {"name": "OpenRouter", "url": "https://openrouter.ai/api/v1"},
        {"name": "Groq", "url": "https://api.groq.com/openai/v1"},
        {"name": "Together AI", "url": "https://api.together.xyz/v1"},
        {"name": "DeepSeek", "url": "https://api.deepseek.com/v1"},
        {"name": "Fireworks AI", "url": "https://api.fireworks.ai/inference/v1"},
        {"name": "xAI", "url": "https://api.x.ai/v1"},
        {"name": "Cerebras", "url": "https://api.cerebras.ai/v1"},
        {"name": "LM Studio", "url": "http://localhost:1234/v1"},
        {"name": "LocalAI", "url": "http://localhost:8080/v1"},
        {"name": "Ollama (OpenAI Compatible)", "url": "http://localhost:11434/v1"},
    ],
    "anthropic_base": [
        {"name": "Anthropic", "url": "https://api.anthropic.com"},
        {"name": "Fireworks AI", "url": "https://api.fireworks.ai/inference"},
    ],
    "ollama": [
        {"name": "Ollama Cloud", "url": "https://ollama.com"},
        {"name": "Local Ollama", "url": "http://localhost:11434"},
        {"name": "Local Ollama (Docker Host)", "url": "http://host.docker.internal:11434"},
    ],
    "lmstudio": [
        {"name": "Local LM Studio", "url": "http://localhost:1234"},
        {"name": "Local LM Studio (Docker Host)", "url": "http://host.docker.internal:1234"},
    ],
}

PROVIDER_URL_SUGGESTIONS_METADATA_KEY = "provider_url_suggestions"
BASE_URL_FIELD_KEY = "settings.base_url"


def get_provider_url_suggestions(provider: str | None) -> list[dict[str, str]]:
    key = str(provider or "").strip().lower()
    suggestions = PROVIDER_URL_SUGGESTIONS.get(key, [])
    return [dict(item) for item in suggestions if isinstance(item, dict)]


def attach_provider_url_suggestions(schema: Sections | None, provider: str | None) -> Sections | None:
    suggestions = get_provider_url_suggestions(provider)
    if not schema or not getattr(schema, "sections", None) or not suggestions:
        return schema

    for section in schema.sections or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) != BASE_URL_FIELD_KEY:
                continue
            metadata: dict[str, Any] = dict(getattr(field, "metadata", None) or {})
            metadata[PROVIDER_URL_SUGGESTIONS_METADATA_KEY] = suggestions
            field.metadata = metadata
            return schema

    return schema
