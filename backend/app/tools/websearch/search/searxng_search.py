from typing import Any, Dict

from fastapi import HTTPException
import requests

from app.tools.websearch.http_errors import raise_provider_http_error

# Languages supported by SearxNG.
_SUPPORTED_LANG_CODES: list[str] = [
    "af", "ar", "ar-SA",
    "be", "bg", "bg-BG", "ca", "cs", "cs-CZ", "cy",
    "da", "da-DK", "de", "de-AT", "de-BE", "de-CH", "de-DE",
    "el", "el-GR", "en", "en-AU", "en-CA", "en-GB", "en-IE", "en-IN",
    "en-NZ", "en-PH", "en-PK", "en-SG", "en-US", "en-ZA",
    "es", "es-AR", "es-CL", "es-CO", "es-ES", "es-MX", "es-PE", "et",
    "et-EE", "eu",
    "fa", "fi", "fi-FI", "fr", "fr-BE", "fr-CA", "fr-CH", "fr-FR",
    "ga", "gd", "gl",
    "he", "hi", "hr", "hu", "hu-HU",
    "id", "id-ID", "is", "it", "it-CH", "it-IT",
    "ja", "ja-JP",
    "kn", "ko", "ko-KR",
    "lt", "lv",
    "ml", "mr",
    "nb", "nb-NO", "nl", "nl-BE", "nl-NL",
    "pl", "pl-PL", "pt", "pt-BR", "pt-PT",
    "ro", "ro-RO", "ru", "ru-RU",
    "sk", "sl", "sq", "sv", "sv-SE",
    "ta", "te", "th", "th-TH", "tr", "tr-TR",
    "uk", "ur",
    "vi", "vi-VN",
    "zh", "zh-CN", "zh-HK", "zh-TW",
]


def searxng_search_urls(base_url: str, query: str, *, language: str | None = None, fallback_language: str = "en", num_results: int = 10) -> Dict[str, Any]:
    searx_url = f"{str(base_url).rstrip('/')}/search"
    if language not in _SUPPORTED_LANG_CODES:
        language = fallback_language

    params = {"q": query, "format": "json", "language": language}
    headers = {"Accept": "application/json"}

    try:
        response = requests.get(searx_url, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
        raw_results = data.get("results", [])[:num_results]
        results_to_return = []
        for item in raw_results:
            results_to_return.append({
                "title": item.get("title"),
                "url": item.get("url"),
                "preview": item.get("content"),
            })
        return {
            "result": results_to_return,
            "metadata": {
                "provider_search": "searxng",
            },
        }
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="SearXNG", operation="search")
    except (requests.exceptions.RequestException, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"SearXNG search failed: {exc}") from exc
