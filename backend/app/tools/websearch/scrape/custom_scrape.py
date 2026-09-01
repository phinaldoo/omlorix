from typing import Any

import requests

from app.tools.websearch.http_errors import raise_provider_http_error


def _resolve_scrape_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("result", "results", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _pick_content(item: dict[str, Any], *, view_raw: bool) -> str | None:
    raw_keys = ("html", "rawHtml", "raw_html", "content", "markdown", "text", "body")
    normal_keys = ("content", "markdown", "text", "raw_content", "excerpt", "summary", "html", "rawHtml", "raw_html", "body")
    keys = raw_keys if view_raw else normal_keys

    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def custom_scrape_urls(
    base_url: str,
    urls: list[str],
    *,
    country: str | None = None,
    fallback_country: str = "US",
    language: str = "en",
    view_raw: bool = False,
    url_validator=None,
) -> dict[str, Any]:
    request_url = str(base_url or "").strip()
    if url_validator is not None:
        url_validator(request_url)
    resolved_country = str(country or fallback_country or "US").strip().upper() or "US"
    payload = {
        "urls": urls,
        "country": resolved_country,
        "language": str(language or "en").strip().lower() or "en",
        "view_raw": bool(view_raw),
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            request_url,
            json=payload,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        items = _resolve_scrape_items(data)

        result: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            error = item.get("error")
            if error not in (None, ""):
                raise Exception(str(error))

            resolved_url = str(
                item.get("url")
                or item.get("link")
                or item.get("source_url")
                or item.get("source")
                or (urls[index] if index < len(urls) else "")
            ).strip()
            entry: dict[str, Any] = {
                "url": resolved_url,
                "title": str(item.get("title") or "").strip(),
            }

            content = _pick_content(item, view_raw=view_raw)
            if content is not None:
                entry["content"] = content

            result.append(entry)

        return {
            "result": result,
            "metadata": {
                "provider_scrape": "custom",
            },
        }
    except requests.HTTPError as exc:
        raise_provider_http_error(exc, provider_name="Custom scrape provider", operation="scrape")
    except requests.exceptions.Timeout:
        raise Exception("Request timeout: custom scrape provider did not respond in time") from None
    except requests.exceptions.RequestException as exc:
        raise Exception(f"Request failed: {exc}") from exc
    except ValueError as exc:
        raise Exception(f"Invalid JSON response: {exc}") from exc
