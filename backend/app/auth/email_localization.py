from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Mapping
from types import MappingProxyType


SUPPORTED_EMAIL_LANGUAGES = (
    "ar",
    "de",
    "en",
    "es",
    "fr",
    "hi",
    "it",
    "ja",
    "pt",
    "ru",
    "zh",
)
RTL_EMAIL_LANGUAGES = {"ar"}
SUPPORTED_EMAIL_TYPES = {"password_reset", "twofa", "security", "email_change"}

_EMAIL_LOCALES_DIR = Path(__file__).resolve().parent / "email_locales"


def _normalize_language_code(value: str | None) -> str | None:
    """Normalize language code to supported format."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    normalized = normalized.replace("_", "-")
    primary = normalized.split("-", 1)[0].strip()
    if primary in SUPPORTED_EMAIL_LANGUAGES:
        return primary
    return None


def resolve_email_language(preferred_language: str | None = None, accept_language: str | None = None) -> str:
    """Resolve email language from preferences or Accept-Language header."""
    normalized = _normalize_language_code(preferred_language)
    if normalized:
        return normalized

    weighted_candidates: list[tuple[float, int, str]] = []
    for index, part in enumerate(str(accept_language or "").split(",")):
        item = part.strip()
        if not item:
            continue
        language_tag, _, params = item.partition(";")
        candidate = _normalize_language_code(language_tag)
        if not candidate:
            continue
        weight = 1.0
        if params:
            for param in params.split(";"):
                key, _, value = param.strip().partition("=")
                if key.strip().lower() == "q":
                    try:
                        weight = float(value)
                    except Exception:
                        weight = 0.0
                    break
        weighted_candidates.append((weight, index, candidate))

    if weighted_candidates:
        weighted_candidates.sort(key=lambda item: (-item[0], item[1]))
        return weighted_candidates[0][2]
    return "en"


@lru_cache(maxsize=None)
def _load_email_locale_file(language_code: str, email_type: str) -> dict[str, str]:
    """Load email locale JSON file."""
    locale_path = _EMAIL_LOCALES_DIR / language_code / f"{email_type}.json"
    with locale_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Email locale file {locale_path} must contain a JSON object.")
    return {str(key): str(value) for key, value in data.items()}


def get_email_copy(email_type: str, language_code: str | None = None) -> dict[str, str]:
    """Get email copy with language fallback."""
    return dict(_get_email_copy_cached(email_type, language_code))


@lru_cache(maxsize=None)
def _get_email_copy_cached(email_type: str, language_code: str | None = None) -> Mapping[str, str]:
    """Get cached email copy with language fallback."""
    if email_type not in SUPPORTED_EMAIL_TYPES:
        raise ValueError(f"Unsupported email type: {email_type}")

    normalized_language = resolve_email_language(language_code)
    english_copy = _load_email_locale_file("en", email_type)
    if normalized_language == "en":
        merged = dict(english_copy)
    else:
        try:
            localized_copy = _load_email_locale_file(normalized_language, email_type)
        except FileNotFoundError:
            localized_copy = {}
        merged = {**english_copy, **localized_copy}

    merged["lang"] = normalized_language
    merged["dir"] = "rtl" if normalized_language in RTL_EMAIL_LANGUAGES else "ltr"
    merged["align"] = "right" if merged["dir"] == "rtl" else "left"
    return MappingProxyType(merged)
