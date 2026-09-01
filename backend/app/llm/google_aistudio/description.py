from typing import Any


AISTUDIO_MODEL_DESCRIPTION_MAX_LENGTH = 100


def normalize_aistudio_model_description(value: Any) -> str | None:
    """Normalize AI Studio model description."""
    if value is None:
        return None
    return str(value).strip()[:AISTUDIO_MODEL_DESCRIPTION_MAX_LENGTH]
