from typing import Dict, Any
from fastapi import HTTPException
from ddgs import DDGS

SUPPORTED_DDG_LOCALES = {
    "xa-ar", "xa-en", "ar-es", "au-en", "at-de", "be-fr", "be-nl", "br-pt",
    "bg-bg", "ca-en", "ca-fr", "ct-ca", "cl-es", "cn-zh", "co-es", "hr-hr",
    "cz-cs", "dk-da", "ee-et", "fi-fi", "fr-fr", "de-de", "gr-el", "hk-tzh",
    "hu-hu", "in-en", "id-id", "id-en", "ie-en", "il-he", "it-it", "jp-jp",
    "kr-kr", "lv-lv", "lt-lt", "xl-es", "my-ms", "my-en", "mx-es", "nl-nl",
    "nz-en", "no-no", "pe-es", "ph-en", "ph-tl", "pl-pl", "pt-pt", "ro-ro",
    "ru-ru", "sg-en", "sk-sk", "sl-sl", "za-en", "es-es", "se-sv", "ch-de",
    "ch-fr", "ch-it", "tw-tzh", "th-th", "tr-tr", "ua-uk", "uk-en", "us-en",
    "ue-es", "ve-es", "vn-vi", "wt-wt"
}


def get_locale_code(language_code: str | None, country_code: str | None, default="wt-wt") -> str:
    if not language_code or not country_code:
        return default
    locale = f"{country_code.lower()}-{language_code.lower()}"
    return locale if locale in SUPPORTED_DDG_LOCALES else default


def duckduckgo_search_urls(query: str, language: str | None, country: str | None, fallback_language: str = "en", fallback_country: str = "us", max_results: int = 10, safesearch: str = "moderate") -> Dict[str, Any]:
    local_code = get_locale_code(language, country)
    if local_code == "wt-wt":
        if fallback_language and fallback_country:
            local_code = get_locale_code(fallback_language, fallback_country)
    try:
        AVAILABLE_SAFESEARCH = ["on", "moderate", "off"]
        if safesearch not in AVAILABLE_SAFESEARCH:
            safesearch = "moderate"
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(
                query=query,
                region=local_code,
                safesearch=safesearch,
                max_results=max_results,
            ))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or "DuckDuckGo search failed") from exc

    result_to_return = []
    for result in raw_results:
        result_to_return.append({
            "title": result.get("title"),
            "url": result.get("href"),
            "preview": result.get("body"),
        })
    return {
        "result": result_to_return,
        "metadata": {
            "provider_search": "duckduckgo",
        },
    }
