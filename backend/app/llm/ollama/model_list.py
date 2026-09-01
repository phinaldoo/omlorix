# Verified against Ollama's gpt-oss library entry and OpenAI-compatible API
# documentation on 2026-07-27. Ollama supplies no per-token USD accounting:
# local runs have no provider token bill and cloud billing is usage-level based.
OLLAMA_CATALOG_LAST_VERIFIED = "2026-07-27"
OLLAMA_GPT_OSS_DOCS_URL = "https://ollama.com/library/gpt-oss"

OLLAMA_REASONING_EFFORT_VALUES = ("low", "medium", "high")


OLLAMA_MODELS_SUPPORT_REASONING_EFFORT = {
    "gpt-oss:latest",
    "gpt-oss:20b",
    "gpt-oss:120b",
    "gpt-oss:20b-cloud",
    "gpt-oss:120b-cloud",
}


def ollama_model_supports_reasoning_effort(model_name: str | None) -> bool:
    """Check if Ollama model supports reasoning effort."""
    normalized = str(model_name or "").strip().lower()
    return normalized in OLLAMA_MODELS_SUPPORT_REASONING_EFFORT or normalized == "gpt-oss" or normalized.startswith("gpt-oss:")
