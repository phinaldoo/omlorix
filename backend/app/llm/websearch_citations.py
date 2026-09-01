from typing import Any
from urllib.parse import urlparse


MAX_CITATION_URL_LENGTH = 2048
MAX_CITATION_TITLE_LENGTH = 300
MAX_CITATION_SNIPPET_LENGTH = 200


def _normalize_citation_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if not url or len(url) > MAX_CITATION_URL_LENGTH:
        return None

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    return url


def _normalize_text(value: Any, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if len(text) > max_length:
        return f"{text[:max_length]}..."

    return text


def build_web_search_citations(webpages: Any) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    if not isinstance(webpages, list):
        return citations

    for page in webpages:
        if not isinstance(page, dict):
            continue

        url = _normalize_citation_url(page.get("url"))
        if not url:
            continue

        citation: dict[str, str] = {"url": url}

        title = _normalize_text(page.get("title"), MAX_CITATION_TITLE_LENGTH)
        if title:
            citation["title"] = title

        snippet_source = (
            page.get("content")
            or page.get("preview")
            or page.get("snippet")
            or page.get("text")
        )
        snippet = _normalize_text(snippet_source, MAX_CITATION_SNIPPET_LENGTH)
        if snippet:
            citation["snippet"] = snippet

        citations.append(citation)

    return citations


def collect_tool_result_citations(messages_to_save: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    if not isinstance(messages_to_save, list):
        return citations

    for message in messages_to_save:
        if not isinstance(message, dict) or message.get("type") != "tool_call_result":
            continue
        meta = message.get("meta")
        if not isinstance(meta, dict):
            continue
        message_citations = meta.get("citations")
        if not isinstance(message_citations, list):
            continue

        for citation in message_citations:
            if not isinstance(citation, dict):
                continue
            url = _normalize_citation_url(citation.get("url"))
            if not url or url in seen_urls:
                continue

            normalized_citation: dict[str, str] = {"url": url}
            title = _normalize_text(citation.get("title"), MAX_CITATION_TITLE_LENGTH)
            if title:
                normalized_citation["title"] = title
            snippet = _normalize_text(citation.get("snippet"), MAX_CITATION_SNIPPET_LENGTH)
            if snippet:
                normalized_citation["snippet"] = snippet

            seen_urls.add(url)
            citations.append(normalized_citation)

    return citations
