import hashlib
import logging
from collections import Counter, OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import unquote, urljoin, urlparse
import mimetypes
import os
import time
import uuid

import requests
from fastapi import HTTPException

from app.files.utils import (
    CHUNK_SIZE,
    MAX_FILE_SIZE,
    TEMP_DIR,
    _find_duplicate_file,
    get_file_category,
    persist_generated_file_path,
    validate_file_type,
)
from app.groups.init import get_user_group_setting_value
from app.network.policy import (
    OutboundRequestBlockedError,
    assert_public_resolved_ip_allowed,
    assert_public_url_allowed,
    assert_websearch_provider_allowed,
)
from app.network.outbound_http import public_web_request
from app.tools.websearch.combined.utils import run_combined_provider
from app.tools.websearch.domain_filters import (
    filter_websearch_result_entries_by_domains,
    filter_websearch_urls_by_domains,
    websearch_provider_has_domain_filters,
    websearch_url_is_allowed,
)
from app.tools.websearch.images.searxng_images import searxng_search_images
from app.tools.websearch.models import WebSearchProvider, get_websearch_provider, _get_provider_types
from app.tools.websearch.pricing import build_websearch_tool_meta, build_websearch_usage_event
from app.tools.websearch.robots import check_robots_txt, should_respect_robots_txt
from app.tools.websearch.schemas import DEFAULT_USER_AGENT
from app.tools.websearch.scrape.utils import scrape
from app.tools.websearch.search.utils import search
from app.users.init import get_user_setting_value


COMBINED_WEBSEARCH_PROVIDER = {"exa", "ollama", "perplexity"}
_YOUTUBE_HOSTS = ("youtube.com", "youtube-nocookie.com", "youtu.be")
_DEFAULT_MAX_UPLOAD_MB = 1024
_HEAD_CACHE: OrderedDict[tuple[str, Tuple[Tuple[str, str], ...]], tuple[float, Dict[str, Any]]] = OrderedDict()
_HEAD_CACHE_MAX_SIZE = 256
_HEAD_CACHE_TTL = 300
logger = logging.getLogger(__name__)


def _resolve_direct_url_scrape_provider(
    db,
    *,
    search_provider_id: str | None,
    scrape_provider_id: str | None,
) -> WebSearchProvider:
    """Choose the provider used to fetch explicitly supplied URLs.

    Exa exposes both search and contents APIs under the same provider
    configuration. When Exa is selected for search, reuse that configuration
    for direct URL retrieval instead of requiring administrators or users to
    select a redundant scrape provider. Other search providers retain the
    existing independently configured scrape-provider behavior.
    """

    # A configured scraper is sufficient for direct URLs and must not be
    # blocked by a stale, unrelated search-provider selection. Resolve the
    # search provider only for the Exa reuse case where no scraper was chosen.
    if scrape_provider_id:
        return get_websearch_provider(db, scrape_provider_id)
    search_provider = get_websearch_provider(db, search_provider_id)
    if search_provider.provider == "exa":
        return search_provider
    return get_websearch_provider(db, scrape_provider_id)


def _head_request_cached(
    url: str,
    *,
    timeout: int = 5,
    headers: Dict[str, str] | None = None,
    allow_redirects: bool = True,
    db=None,
    feature: str = "URL content type detection",
    target_url_validator: Callable[[str], None] | None = None,
) -> Dict[str, Any] | None:
    try:
        if target_url_validator is not None:
            target_url_validator(url)
        _assert_websearch_url_allowed(db, url, feature=feature)
    except HTTPException:
        return None

    cache_headers = tuple(sorted((headers or {}).items()))
    cache_key = (url, cache_headers)
    now = time.time()

    # A cached type probe does not retain the redirect chain that produced it.
    # Bypass the shared cache under a provider policy so every redirect target
    # is checked against the active boundary.
    cached = None if target_url_validator is not None else _HEAD_CACHE.get(cache_key)
    if cached is not None:
        cached_ts, cached_payload = cached
        if now - cached_ts < _HEAD_CACHE_TTL:
            _HEAD_CACHE.move_to_end(cache_key)
            return cached_payload
        _HEAD_CACHE.pop(cache_key, None)

    try:
        if allow_redirects:
            response = _request_with_policy_checked_redirects(
                db,
                "HEAD",
                url,
                feature=feature,
                timeout=timeout,
                headers=headers,
                target_url_validator=target_url_validator,
            )
        else:
            if target_url_validator is not None:
                target_url_validator(url)
            _assert_websearch_url_allowed(db, url, feature=feature)
            response = public_web_request(
                "HEAD",
                url,
                feature=feature,
                allow_redirects=allow_redirects,
                timeout=timeout,
                headers=headers,
            )
    except Exception:
        return None

    payload = {"status_code": response.status_code, "headers": dict(response.headers)}
    response.close()
    if target_url_validator is None:
        _HEAD_CACHE[cache_key] = (now, payload)
        if len(_HEAD_CACHE) > _HEAD_CACHE_MAX_SIZE:
            _HEAD_CACHE.popitem(last=False)
    return payload


def _requests_request_with_policy_redirects(
    db,
    method: str,
    url: str,
    *,
    feature: str,
    max_redirects: int = 10,
    target_url_validator: Callable[[str], None] | None = None,
    **kwargs,
) -> requests.Response:
    current_url = url
    for _ in range(max_redirects + 1):
        if target_url_validator is not None:
            target_url_validator(current_url)
        _assert_websearch_url_allowed(db, current_url, feature=feature)
        try:
            response = public_web_request(
                method,
                current_url,
                feature=feature,
                allow_redirects=False,
                **kwargs,
            )
        except OutboundRequestBlockedError as exc:
            raise exc.to_http_exception() from exc
        if not response.is_redirect:
            return response

        location = response.headers.get("Location")
        if not location:
            return response
        response.close()
        current_url = urljoin(current_url, location)

    raise HTTPException(status_code=400, detail="Too many redirects")


def _head_request_with_policy_redirects(
    db,
    url: str,
    *,
    feature: str,
    timeout: int = 5,
    headers: Dict[str, str] | None = None,
    target_url_validator: Callable[[str], None] | None = None,
) -> Dict[str, Any] | None:
    try:
        response = _requests_request_with_policy_redirects(
            db,
            "HEAD",
            url,
            feature=feature,
            timeout=timeout,
            headers=headers,
            target_url_validator=target_url_validator,
        )
    except HTTPException:
        raise
    except Exception:
        return None

    try:
        return {"status_code": response.status_code, "headers": dict(response.headers)}
    finally:
        response.close()


def _policy_blocked_webpage_entry(url: str, detail: str) -> dict[str, Any]:
    return {
        "url": url,
        "content": None,
        "title": None,
        "error": detail,
        "blocked_by_policy": True,
    }


def _assert_websearch_url_allowed(db, url: str, *, feature: str) -> None:
    if _is_youtube_url(url):
        raise HTTPException(
            status_code=403,
            detail={"code": "youtube_retrieval_disabled"},
        )
    try:
        assert_public_url_allowed(db, url=url, feature=feature)
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc


def _assert_websearch_resolved_ip_allowed(db, ip_address: str, *, feature: str) -> None:
    try:
        assert_public_resolved_ip_allowed(db, ip_address=ip_address, feature=feature)
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc


def _assert_websearch_provider_domain_url_allowed(provider, url: str) -> None:
    """Raise a stable policy error when *url* violates provider hostname rules."""

    if websearch_url_is_allowed(url, provider):
        return
    raise HTTPException(
        status_code=403,
        detail={"code": "websearch_domain_policy_blocked"},
    )


def _build_provider_domain_url_validator(
    provider,
) -> Callable[[str], None] | None:
    """Build a reusable hostname validator only when a policy is active."""

    if not websearch_provider_has_domain_filters(provider):
        return None

    def validate(url: str) -> None:
        _assert_websearch_provider_domain_url_allowed(provider, url)

    return validate


def _build_scrape_target_url_validator(
    db,
    provider,
) -> Callable[[str], None] | None:
    """Combine provider-domain and public-network checks for local redirects."""

    domain_validator = _build_provider_domain_url_validator(provider)
    if domain_validator is None:
        return None

    def validate(url: str) -> None:
        # Check the cheap configured hostname boundary before DNS resolution or
        # any other outbound-policy work.
        domain_validator(url)
        _assert_websearch_url_allowed(db, url, feature="Web scrape redirect")

    return validate


def _request_with_policy_checked_redirects(
    db,
    method: str,
    url: str,
    *,
    feature: str,
    timeout: int,
    headers: Dict[str, str] | None = None,
    stream: bool = False,
    max_redirects: int = 10,
    target_url_validator: Callable[[str], None] | None = None,
) -> requests.Response:
    current_url = url
    for _ in range(max_redirects + 1):
        if target_url_validator is not None:
            target_url_validator(current_url)
        _assert_websearch_url_allowed(db, current_url, feature=feature)
        try:
            response = public_web_request(
                method,
                current_url,
                feature=feature,
                allow_redirects=False,
                timeout=timeout,
                headers=headers,
                stream=stream,
            )
        except OutboundRequestBlockedError as exc:
            raise exc.to_http_exception() from exc
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                return response
            current_url = urljoin(current_url, location)
            continue
        return response
    raise HTTPException(status_code=400, detail="Too many redirects while fetching URL")


def _split_allowed_web_urls(
    db,
    urls: list[str],
    *,
    feature: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    allowed_urls: list[str] = []
    blocked_entries: list[dict[str, Any]] = []
    for raw_url in urls or []:
        url = str(raw_url or "").strip()
        if not url:
            continue
        try:
            _assert_websearch_url_allowed(db, url, feature=feature)
        except HTTPException as exc:
            blocked_entries.append(_policy_blocked_webpage_entry(url, str(exc.detail)))
        else:
            allowed_urls.append(url)
    return allowed_urls, blocked_entries


def _is_youtube_url(url: str) -> bool:
    """Identify YouTube links locally without contacting YouTube."""

    raw_url = str(url or "").strip()
    candidates = [raw_url]
    if "\\" in raw_url:
        # Requests treats a literal backslash in the authority as a path
        # separator. Check that interpretation too so parsing cannot move a
        # YouTube hostname past this boundary.
        candidates.append(raw_url.replace("\\", "/"))

    for candidate in candidates:
        try:
            parsed = urlparse(candidate)
        except (TypeError, ValueError):
            continue
        if parsed.scheme.lower() not in {"http", "https"}:
            continue
        host = (parsed.hostname or "").strip().rstrip(".").lower()
        try:
            host = host.encode("idna").decode("ascii").rstrip(".").lower()
        except UnicodeError:
            continue
        if any(
            host == root or host.endswith(f".{root}")
            for root in _YOUTUBE_HOSTS
        ):
            return True
    return False


def _split_youtube_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    """Keep YouTube links out of robots, classification, and scrape requests."""

    fetchable_urls: list[str] = []
    youtube_urls: list[str] = []
    for raw_url in urls or []:
        url = str(raw_url or "").strip()
        if not url:
            continue
        target = youtube_urls if _is_youtube_url(url) else fetchable_urls
        target.append(url)
    return fetchable_urls, youtube_urls


def _youtube_external_link_entries(urls: list[str]) -> list[dict[str, Any]]:
    """Preserve source links while making clear that Omlorix did not fetch them."""

    return [
        {
            "url": url,
            "content": None,
            "title": None,
            "external_link_only": True,
            "retrieval_disabled": True,
        }
        for url in urls
    ]


def _webpage_entry_url(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    for key in ("url", "link", "source_url"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _sanitize_youtube_webpage_entries(entries: list[Any]) -> list[Any]:
    """Discard any YouTube content returned by a native or remote scraper."""

    sanitized: list[Any] = []
    for entry in entries or []:
        url = _webpage_entry_url(entry)
        if url and _is_youtube_url(url):
            sanitized.extend(_youtube_external_link_entries([url]))
        else:
            sanitized.append(entry)
    return sanitized


def _filter_allowed_webpage_entries(
    db,
    entries: list[Any],
    *,
    feature: str,
) -> tuple[list[Any], list[dict[str, Any]]]:
    allowed_entries: list[Any] = []
    blocked_entries: list[dict[str, Any]] = []
    for entry in _sanitize_youtube_webpage_entries(entries):
        url = _webpage_entry_url(entry)
        if not url:
            allowed_entries.append(entry)
            continue
        if _is_youtube_url(url):
            allowed_entries.append(entry)
            continue
        try:
            _assert_websearch_url_allowed(db, url, feature=feature)
        except HTTPException as exc:
            blocked_entries.append(_policy_blocked_webpage_entry(url, str(exc.detail)))
        else:
            allowed_entries.append(entry)
    return allowed_entries, blocked_entries


def _filter_webpage_entries_by_robots(entries: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    url_entries = [
        (url, entry)
        for entry in entries
        if (url := _webpage_entry_url(entry)) and not _is_youtube_url(url)
    ]
    if not url_entries:
        return entries, []

    allowed_urls = check_robots_txt([url for url, _entry in url_entries], user_agent=DEFAULT_USER_AGENT)
    allowed_counter = Counter(allowed_urls)
    allowed_entries: list[Any] = []
    blocked_entries: list[dict[str, Any]] = []
    for entry in entries:
        url = _webpage_entry_url(entry)
        if not url:
            allowed_entries.append(entry)
            continue
        if _is_youtube_url(url):
            allowed_entries.append(entry)
            continue
        if allowed_counter.get(url, 0):
            allowed_counter[url] -= 1
            allowed_entries.append(entry)
        else:
            blocked_entries.append(
                {
                    "url": url,
                    "content": None,
                    "title": None,
                    "error": "This website cannot be reached because of its robots.txt policy",
                    "blocked_by_robots": True,
                }
            )
    return allowed_entries, blocked_entries


def _image_result_target(entry: dict[str, Any]) -> str | None:
    for key in ("img_src", "thumbnail_src", "url", "source_url", "thumbnail"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _filter_allowed_image_results(db, image_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered_results: list[dict[str, Any]] = []
    for result in image_results or []:
        if not isinstance(result, dict):
            continue
        target = _image_result_target(result)
        if not target:
            filtered_results.append(result)
            continue
        try:
            _assert_websearch_url_allowed(db, target, feature="Web image search result")
        except HTTPException:
            continue
        filtered_results.append(result)
    return filtered_results


def _provider_types(provider: WebSearchProvider | None) -> set[str]:
    if not provider:
        return set()
    return set(_get_provider_types(provider))


def normalize_web_search_call_args(
    *,
    queries: list[str] | None = None,
    urls: list[str] | None = None,
    search_mode: str = "web",
    view_raw: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    queries = [q for q in (queries or []) if q not in (None, "")]
    urls = [u for u in (urls or []) if u not in (None, "")]

    normalized_search_mode = str(search_mode or "web").strip().lower() or "web"
    if normalized_search_mode not in {"web", "images"}:
        raise ValueError("web_search argument 'search_mode' must be one of: web, images")
    if not queries and not urls:
        raise ValueError("web_search requires at least one of 'queries' or 'urls'.")
    if urls and normalized_search_mode != "web":
        raise ValueError("web_search argument 'search_mode' is only supported when using 'queries'.")
    if queries and view_raw and not urls:
        raise ValueError("web_search argument 'view_raw' is only supported when using 'urls'.")
    if limit is not None and normalized_search_mode != "images":
        raise ValueError("web_search argument 'limit' is only supported when search_mode is 'images'.")

    return {
        "queries": queries,
        "urls": urls,
        "search_mode": normalized_search_mode,
        "limit": limit,
        "view_raw": view_raw,
    }


def _resolve_user_locale(user_id, db) -> tuple[str, str]:
    country = None
    language = None
    if user_id is not None:
        country = get_user_setting_value(user_id, "general", "country", db)
        language = get_user_setting_value(user_id, "general", "language", db)
    return country or "US", language or "en"


def _provider_settings(provider: WebSearchProvider | None) -> dict[str, Any]:
    return provider.settings if provider and isinstance(provider.settings, dict) else {}


def _forward_user_locale_enabled(provider: WebSearchProvider | None) -> bool:
    settings = _provider_settings(provider)
    return settings.get("forward_user_locale") is True


def _resolve_provider_request_locale(user_id, db, provider: WebSearchProvider | None) -> tuple[str | None, str | None]:
    if not _forward_user_locale_enabled(provider):
        return None, None
    return _resolve_user_locale(user_id, db)


def _ensure_provider_success(payload: Any, *, provider_label: str) -> Any:
    if isinstance(payload, dict) and payload.get("error"):
        raise HTTPException(status_code=502, detail=f"{provider_label} failed: {payload['error']}")
    return payload


def _number_webpages(webpages: list[Any], *, field_name: str) -> list[Any]:
    numbered: list[Any] = []
    for idx, item in enumerate(webpages, start=1):
        if isinstance(item, dict):
            entry = dict(item)
            entry.pop("id", None)
            entry.pop("number", None)
            entry[field_name] = idx
            numbered.append(entry)
        else:
            numbered.append(item)
    return numbered


def _resolve_upload_limit_mb(user_id, db) -> int:
    try:
        max_mb_val = get_user_group_setting_value(user_id, "chat", "max_upload_size", db)
        return int(max_mb_val) if max_mb_val is not None else _DEFAULT_MAX_UPLOAD_MB
    except Exception:
        return _DEFAULT_MAX_UPLOAD_MB


def _save_categorized_urls(
    url_categories: dict[str, list[str]],
    *,
    user_id,
    db,
    max_upload_mb: int,
    project_id: str | None,
    target_url_validator: Callable[[str], None] | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Download categorized assets while preserving the active domain policy."""

    documents_ids: list[str] = []
    images_ids: list[str] = []
    videos_ids: list[str] = []
    audios_ids: list[str] = []

    def _save_batch(batch_urls: list[str], out: list[str]):
        for url in batch_urls or []:
            try:
                file_id = _download_and_save_url(
                    url,
                    user_id,
                    db,
                    max_upload_mb,
                    target_url_validator=target_url_validator,
                )
                if file_id:
                    out.append(file_id)
            except HTTPException:
                continue
            except Exception:
                continue

    _save_batch(url_categories.get("documents", []), documents_ids)
    _save_batch(url_categories.get("images", []), images_ids)
    _save_batch(url_categories.get("videos", []), videos_ids)
    _save_batch(url_categories.get("audios", []), audios_ids)
    return documents_ids, images_ids, videos_ids, audios_ids


def _build_web_result_content(
    *,
    webpages: list[Any],
    url_categories: dict[str, list[str]],
    user_id,
    db,
    project_id: str | None,
    target_url_validator: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build tool content without dropping policy checks for linked assets."""

    documents_ids, images_ids, videos_ids, audios_ids = _save_categorized_urls(
        url_categories,
        user_id=user_id,
        db=db,
        max_upload_mb=_resolve_upload_limit_mb(user_id, db),
        project_id=project_id,
        target_url_validator=target_url_validator,
    )
    return {
        "webpages": webpages,
        "documents": documents_ids,
        "videos": videos_ids,
        "audios": audios_ids,
        "images": images_ids,
    }


def web_search(
    db,
    user_id,
    scrape_provider_id: str | None,
    search_provider_id: str | None,
    project_id: str | None,
    search_mode: str = "web",
    image_limit: int | None = None,
    queries: List[str] | None = None,
    urls: List[str] | None = None,
    view_raw: bool = False,
    max_queries: int = 3,
):
    """Run a web search or scrape.

    When using query-based web search, at most `max_queries` queries are processed. If more
    queries are provided, the response `meta` includes truncation metadata.
    """
    queries = queries or []
    urls = urls or []
    result_to_return: list[dict[str, Any]] = []

    if max_queries < 1:
        raise ValueError("max_queries must be >= 1")

    meta: dict[str, Any] = {}
    usage_events: list[dict[str, Any]] = []
    original_query_count = len(queries)
    effective_queries = queries
    if original_query_count > max_queries:
        effective_queries = queries[:max_queries]
        meta["truncated"] = True
        meta["original_count"] = original_query_count
        meta["max_queries"] = max_queries

    if search_mode == "images":
        search_provider = get_websearch_provider(db, search_provider_id)
        try:
            assert_websearch_provider_allowed(db, search_provider, feature="Web search provider request")
        except OutboundRequestBlockedError as exc:
            raise exc.to_http_exception() from exc

        if "search" not in _provider_types(search_provider):
            raise HTTPException(status_code=400, detail="Provided search provider does not support searching.")
        if search_provider.provider != "searxng":
            raise HTTPException(status_code=400, detail="Image search provider not supported.")

        search_provider_settings = _provider_settings(search_provider)
        try:
            configured_image_limit = int(search_provider_settings.get("num_results") or 5)
        except (TypeError, ValueError):
            # Persisted settings can predate schema validation. Preserve the
            # provider's documented default instead of failing the tool call.
            configured_image_limit = 5
        # The tool input may request fewer images, but it must never override
        # the administrator's provider-level maximum or the tool's hard cap.
        resolved_image_limit = max(
            1,
            min(image_limit or 5, configured_image_limit, 10),
        )
        for query in effective_queries:
            image_response = searxng_search_images(
                search_provider_settings.get("base_url"),
                query,
                num_results=resolved_image_limit,
                db=db,
            )
            image_results = image_response.get("result", []) if isinstance(image_response, dict) else image_response
            result_to_return.append(
                {
                    "query": query,
                    "search_type": "images",
                    "content": _filter_allowed_image_results(db, image_results),
                }
            )
        return {"result": result_to_return, "meta": meta}

    if search_mode != "web":
        raise HTTPException(status_code=400, detail="Unsupported search mode")

    if queries:
        search_provider = get_websearch_provider(db, search_provider_id)
        scrape_provider = (
            search_provider
            if search_provider.provider in COMBINED_WEBSEARCH_PROVIDER
            else get_websearch_provider(db, scrape_provider_id)
        )
        try:
            assert_websearch_provider_allowed(db, search_provider, feature="Web search provider request")
            assert_websearch_provider_allowed(
                db,
                scrape_provider,
                feature="Web scrape provider request",
                use_scrape_target=True,
            )
        except OutboundRequestBlockedError as exc:
            raise exc.to_http_exception() from exc

        search_provider_types = _provider_types(search_provider)
        scrape_provider_types = _provider_types(scrape_provider)
        if "search" not in search_provider_types and "combined" not in search_provider_types:
            raise HTTPException(status_code=400, detail="Provided search provider does not support searching.")
        if "scrape" not in scrape_provider_types and "combined" not in scrape_provider_types:
            raise HTTPException(status_code=400, detail="Provided scrape provider does not support scraping.")

        scrape_provider_settings = _provider_settings(scrape_provider)
        search_provider_settings = _provider_settings(search_provider)
        search_country, search_language = _resolve_provider_request_locale(user_id, db, search_provider)
        scrape_country, scrape_language = _resolve_provider_request_locale(user_id, db, scrape_provider)
        provider_domain_validator = _build_provider_domain_url_validator(scrape_provider)
        scrape_target_url_validator = _build_scrape_target_url_validator(db, scrape_provider)
        for query in effective_queries:
            if _is_youtube_url(query):
                allowed_query_urls, _domain_filter_meta = (
                    filter_websearch_urls_by_domains([query], scrape_provider)
                )
                result_to_return.append(
                    {
                        "query": query,
                        "content": {
                            "webpages": _number_webpages(
                                _youtube_external_link_entries(allowed_query_urls),
                                field_name="id",
                            ),
                            "documents": [],
                            "videos": [],
                            "audios": [],
                            "images": [],
                        },
                    }
                )
                continue
            if search_provider.provider in COMBINED_WEBSEARCH_PROVIDER:
                # A combined provider retrieves content itself, so Omlorix can
                # forward native rules where supported and otherwise recheck
                # the URL fields in the returned results.
                combined_payload = _ensure_provider_success(
                    run_combined_provider(search_provider, query, country=search_country),
                    provider_label=search_provider.provider,
                )
                combined_metadata = combined_payload.get("metadata") if isinstance(combined_payload, dict) else {}
                combined_results = list((combined_payload.get("result") if isinstance(combined_payload, dict) else combined_payload) or [])
                # Treat native provider filtering as the pre-fetch boundary,
                # then independently verify every returned source before it
                # reaches Omlorix or any network-policy resolution.
                combined_results, _domain_filter_meta = filter_websearch_result_entries_by_domains(
                    combined_results,
                    search_provider,
                )
                combined_results, policy_blocked_entries = _filter_allowed_webpage_entries(
                    db,
                    combined_results,
                    feature="Combined web search result",
                )
                robots_blocked_entries: list[dict[str, Any]] = []
                if should_respect_robots_txt(
                    search_provider_settings,
                    provider=getattr(search_provider, "provider", None),
                ):
                    combined_results, robots_blocked_entries = _filter_webpage_entries_by_robots(combined_results)
                usage_events.append(
                    build_websearch_usage_event(
                        provider=getattr(search_provider, "provider", None),
                        metadata=combined_metadata,
                    )
                )
                result_to_return.append(
                    {
                        "query": query,
                        "content": {
                            "webpages": _number_webpages(
                                combined_results + policy_blocked_entries + robots_blocked_entries,
                                field_name="id",
                            ),
                            "documents": [],
                            "videos": [],
                            "audios": [],
                            "images": [],
                        },
                    }
                )
                continue

            search_urls = _ensure_provider_success(
                search(query, search_country, search_language, search_provider),
                provider_label=search_provider.provider,
            )
            search_metadata = search_urls.get("metadata") if isinstance(search_urls, dict) else {}
            extracted_urls = extract_url_list(search_urls)
            usage_events.append(
                build_websearch_usage_event(
                    provider=getattr(search_provider, "provider", None),
                    metadata=search_metadata,
                )
            )
            # Apply the configured hostname boundary before global outbound
            # policy checks, DNS resolution, content classification, or scrape
            # dispatch. The policy covers every URL type, not only webpages.
            result_urls, _domain_filter_meta = filter_websearch_urls_by_domains(
                extracted_urls,
                scrape_provider,
            )
            result_urls, youtube_urls = _split_youtube_urls(result_urls)
            result_urls, policy_blocked_entries = _split_allowed_web_urls(
                db,
                result_urls,
                feature="Web search result fetch",
            )
            if result_urls and should_respect_robots_txt(
                scrape_provider_settings,
                provider=getattr(scrape_provider, "provider", None),
            ):
                result_urls = check_robots_txt(result_urls, user_agent=DEFAULT_USER_AGENT)

            url_categories = filter_urls(
                result_urls,
                db=db,
                target_url_validator=provider_domain_validator,
            )
            webpages = url_categories.get("websites", [])
            scraped_webpages = (
                _ensure_provider_success(
                    scrape(
                        webpages,
                        scrape_country,
                        scrape_language,
                        scrape_provider,
                        url_validator=lambda target: _assert_websearch_url_allowed(
                            db, target, feature="Web scrape redirect"
                        ),
                        resolved_ip_validator=lambda ip_address: _assert_websearch_resolved_ip_allowed(
                            db, ip_address, feature="Web scrape resolved peer"
                        ),
                        target_url_validator=scrape_target_url_validator,
                    ),
                    provider_label=scrape_provider.provider,
                )
                if webpages
                else []
            )
            scrape_metadata = scraped_webpages.get("metadata") if isinstance(scraped_webpages, dict) else {}
            scraped_results = scraped_webpages.get("result") if isinstance(scraped_webpages, dict) else scraped_webpages
            scraped_result_list = list(scraped_results or [])
            scraped_result_list, _scrape_domain_filter_meta = filter_websearch_result_entries_by_domains(
                scraped_result_list,
                scrape_provider,
            )
            scraped_result_list = _sanitize_youtube_webpage_entries(
                scraped_result_list
            )
            if webpages:
                usage_events.append(
                    build_websearch_usage_event(
                        provider=getattr(scrape_provider, "provider", None),
                        metadata=scrape_metadata,
                    )
                )
            numbered_webpages = _number_webpages(
                scraped_result_list
                + _youtube_external_link_entries(youtube_urls)
                + policy_blocked_entries,
                field_name="id",
            )
            result_to_return.append(
                {
                    "query": query,
                    "content": _build_web_result_content(
                        webpages=numbered_webpages,
                        url_categories=url_categories,
                        user_id=user_id,
                        db=db,
                        project_id=project_id,
                        target_url_validator=provider_domain_validator,
                    ),
                }
            )

    if urls:
        scrape_provider = _resolve_direct_url_scrape_provider(
            db,
            search_provider_id=search_provider_id,
            scrape_provider_id=scrape_provider_id,
        )
        try:
            assert_websearch_provider_allowed(
                db,
                scrape_provider,
                feature="Web scrape provider request",
                use_scrape_target=True,
            )
        except OutboundRequestBlockedError as exc:
            raise exc.to_http_exception() from exc

        scrape_provider_settings = _provider_settings(scrape_provider)
        scrape_country, scrape_language = _resolve_provider_request_locale(user_id, db, scrape_provider)
        provider_domain_validator = _build_provider_domain_url_validator(scrape_provider)
        scrape_target_url_validator = _build_scrape_target_url_validator(db, scrape_provider)
        allowed_urls, _domain_filter_meta = filter_websearch_urls_by_domains(
            urls,
            scrape_provider,
        )
        allowed_urls, youtube_urls = _split_youtube_urls(allowed_urls)
        allowed_urls, policy_blocked_entries = _split_allowed_web_urls(
            db,
            allowed_urls,
            feature="Direct URL fetch",
        )
        blocked_urls: list[str] = []
        if allowed_urls and should_respect_robots_txt(
            scrape_provider_settings,
            provider=getattr(scrape_provider, "provider", None),
        ):
            robots_allowed_urls = check_robots_txt(allowed_urls, user_agent=DEFAULT_USER_AGENT)
            allowed_counter = Counter(robots_allowed_urls)
            for candidate in allowed_urls:
                if allowed_counter.get(candidate, 0):
                    allowed_counter[candidate] -= 1
                else:
                    blocked_urls.append(candidate)
            allowed_urls = robots_allowed_urls

        url_categories = filter_urls(
            allowed_urls,
            db=db,
            target_url_validator=provider_domain_validator,
        )
        webpages = url_categories.get("websites") or []
        scraped_webpages = (
            _ensure_provider_success(
                scrape(
                    webpages,
                    scrape_country,
                    scrape_language,
                    scrape_provider,
                    view_raw,
                    url_validator=lambda target: _assert_websearch_url_allowed(
                        db, target, feature="Web scrape redirect"
                    ),
                    resolved_ip_validator=lambda ip_address: _assert_websearch_resolved_ip_allowed(
                        db, ip_address, feature="Web scrape resolved peer"
                    ),
                    target_url_validator=scrape_target_url_validator,
                ) or [],
                provider_label=scrape_provider.provider,
            )
            if webpages
            else []
        )
        scrape_metadata = scraped_webpages.get("metadata") if isinstance(scraped_webpages, dict) else {}
        blocked_entries = [
            {
                "url": blocked_url,
                "content": None,
                "title": None,
                "error": "This website cannot be reached because of its robots.txt policy",
                "blocked_by_robots": True,
            }
            for blocked_url in blocked_urls
        ]
        scraped_results = scraped_webpages.get("result", []) if isinstance(scraped_webpages, dict) else scraped_webpages
        scraped_result_list = list(scraped_results or [])
        scraped_result_list, _scrape_domain_filter_meta = filter_websearch_result_entries_by_domains(
            scraped_result_list,
            scrape_provider,
        )
        scraped_result_list = _sanitize_youtube_webpage_entries(scraped_result_list)
        if webpages:
            usage_events.append(
                build_websearch_usage_event(
                    provider=getattr(scrape_provider, "provider", None),
                    metadata=scrape_metadata,
                )
            )
        numbered_webpages = _number_webpages(
            scraped_result_list
            + _youtube_external_link_entries(youtube_urls)
            + blocked_entries
            + policy_blocked_entries,
            field_name="id",
        )
        result_to_return.append(
            {
                "content": _build_web_result_content(
                    webpages=numbered_webpages,
                    url_categories=url_categories,
                    user_id=user_id,
                    db=db,
                    project_id=project_id,
                    target_url_validator=provider_domain_validator,
                )
            }
        )

    meta = build_websearch_tool_meta(base_meta=meta, usage_events=usage_events)
    return {"result": result_to_return, "meta": meta}


def extract_url_list(urls):
    entries = None
    if isinstance(urls, dict):
        entries = urls.get("result") or urls.get("results") or []
    elif isinstance(urls, list):
        entries = urls
    else:
        potential = getattr(urls, "results", None)
        if potential is not None:
            entries = potential

    if entries is None:
        return []

    url_list: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        link = entry.get("link") or entry.get("url")
        if not link:
            continue
        url_list.append(link)
    return url_list


def _guess_filename_from_url(url: str, content_disposition: str | None) -> str:
    """Best-effort original filename from URL or Content-Disposition."""
    try:
        if content_disposition and "filename=" in content_disposition:
            fname = content_disposition.split("filename=", 1)[1].strip('"\' ')
            if fname:
                return os.path.basename(unquote(fname))
    except Exception:
        pass
    try:
        parsed = urlparse(url)
        base = os.path.basename(parsed.path) or "download"
        return os.path.basename(unquote(base))
    except Exception:
        return "download"


def _download_and_save_url(
    url: str,
    user_id: str,
    db,
    max_upload_mb: int,
    project_id: str | None = None,
    target_url_validator: Callable[[str], None] | None = None,
) -> str | None:
    """Download one linked asset while checking every redirect destination."""

    if target_url_validator is not None:
        target_url_validator(url)
    _assert_websearch_url_allowed(db, url, feature="File download from web search")
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    head_payload = _head_request_with_policy_redirects(
        db,
        url,
        feature="File download from web search",
        headers=headers,
        target_url_validator=target_url_validator,
    )
    if head_payload and head_payload.get("headers"):
        cl = head_payload["headers"].get("Content-Length")
        if cl is not None:
            try:
                if int(cl) > max_upload_mb * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="File size exceeds limit")
            except ValueError:
                pass

    def _safe_unlink(path: Path) -> None:
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass

    try:
        with _requests_request_with_policy_redirects(
            db,
            "GET",
            url,
            feature="File download from web search",
            timeout=15,
            headers=headers,
            stream=True,
            target_url_validator=target_url_validator,
        ) as response:
            response.raise_for_status()

            content_disposition = response.headers.get("Content-Disposition")
            original_filename = _guess_filename_from_url(url, content_disposition)
            content_type_header = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()

            file_type = content_type_header or None
            if file_type and not validate_file_type(file_type):
                file_type = None
            if not file_type:
                guessed_type, _ = mimetypes.guess_type(original_filename)
                if guessed_type and validate_file_type(guessed_type):
                    file_type = guessed_type
            if not file_type:
                raise HTTPException(status_code=400, detail="File type is not allowed")

            file_category = get_file_category(file_type)
            filename_path = Path(original_filename)
            extension = filename_path.suffix
            if not extension:
                raise HTTPException(status_code=400, detail="File type is not allowed")

            file_id = str(uuid.uuid4())
            stored_file_name = f"{file_id}{extension}" if extension else file_id
            temp_file_path = TEMP_DIR / f"{file_id}.websearch"
            max_allowed_bytes = min(max_upload_mb * 1024 * 1024, MAX_FILE_SIZE)
            file_size = 0
            hasher = hashlib.sha256()

            try:
                with open(temp_file_path, "wb") as buffer:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        file_size += len(chunk)
                        if file_size > max_allowed_bytes:
                            raise HTTPException(status_code=413, detail="File size exceeds limit")
                        hasher.update(chunk)
                        buffer.write(chunk)
            except HTTPException:
                _safe_unlink(temp_file_path)
                raise
            except Exception as exc:
                _safe_unlink(temp_file_path)
                raise HTTPException(status_code=500, detail="Failed to save downloaded file") from exc

            content_sha256 = hasher.hexdigest()
            duplicate_record = _find_duplicate_file(
                db,
                str(user_id),
                original_filename,
                file_type,
                file_size,
                content_sha256,
                project_id=None,
            )
            if duplicate_record:
                _safe_unlink(temp_file_path)
                return duplicate_record.id

            share = {"public": False, "shared_with": []}
            share_id = str(uuid.uuid4())
            meta = {
                "original_filename": original_filename,
                "source_url": url,
                "content_type": file_type,
                "is_from_websearch": True,
                "origin": "websearch",
                "sha256": content_sha256,
            }

            try:
                file_record = persist_generated_file_path(
                    db,
                    user_id=str(user_id),
                    original_filename=original_filename,
                    source_path=temp_file_path,
                    file_type=file_type,
                    file_category=file_category,
                    project_id=None,
                    share=share,
                    share_id=share_id,
                    meta=meta,
                    file_id=file_id,
                    file_name=stored_file_name,
                )
            except Exception:
                _safe_unlink(temp_file_path)
                raise
            finally:
                _safe_unlink(temp_file_path)

            return file_record.id
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to download file") from exc


def detect_url_type(
    url: str,
    timeout: int = 3,
    db=None,
    target_url_validator: Callable[[str], None] | None = None,
) -> str:
    """Classify a URL while enforcing network and provider redirect policy."""

    try:
        if target_url_validator is not None:
            target_url_validator(url)
        _assert_websearch_url_allowed(db, url, feature="URL content type detection")
    except HTTPException:
        return "blocked"

    try:
        parsed = urlparse(url)
        path_lower = (parsed.path or "").lower()
        if path_lower.endswith((".pdf",)):
            return "pdf"
        if path_lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".heic", ".heif", ".tiff")):
            return "image"
        if path_lower.endswith((".json",)):
            return "json"
        if path_lower.endswith((".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".wmv")):
            return "video"
        if path_lower.endswith((".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a")):
            return "audio"
    except Exception:
        pass

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    try:
        if db is not None:
            head_kwargs: dict[str, Any] = {
                "feature": "URL content type detection",
                "timeout": timeout,
                "headers": headers,
            }
            if target_url_validator is not None:
                head_kwargs["target_url_validator"] = target_url_validator
            head_payload = _head_request_with_policy_redirects(
                db,
                url,
                **head_kwargs,
            )
        else:
            head_payload = _head_request_cached(
                url,
                timeout=timeout,
                headers=headers,
                target_url_validator=target_url_validator,
            )
    except HTTPException:
        return "blocked"
    content_type = ""
    status_code = None
    if head_payload:
        status_code = head_payload.get("status_code")
        content_type = (head_payload.get("headers", {}).get("Content-Type") or "").lower()

    if not content_type or (status_code is not None and status_code in (400, 403, 405)):
        try:
            request_kwargs: dict[str, Any] = {
                "feature": "URL content type detection",
                "timeout": timeout,
                "headers": headers,
                "stream": True,
            }
            if target_url_validator is not None:
                request_kwargs["target_url_validator"] = target_url_validator
            with (
                _requests_request_with_policy_redirects(
                    db,
                    "GET",
                    url,
                    **request_kwargs,
                )
                if db is not None
                else _requests_request_with_policy_redirects(
                    None,
                    "GET",
                    url,
                    **request_kwargs,
                )
            ) as r:
                content_type = (r.headers.get("Content-Type") or "").lower()
        except HTTPException:
            return "blocked"
        except Exception:
            content_type = ""

    if "text/html" in content_type:
        return "webpage"
    if "application/pdf" in content_type:
        return "pdf"
    if "image/" in content_type:
        return "image"
    if "video/" in content_type:
        return "video"
    if "audio/" in content_type:
        return "audio"
    if "application/json" in content_type or content_type == "application/ld+json":
        return "json"
    return "unknown" if content_type else "error"


def filter_urls(
    urls: list[str],
    allowed_types: list[str] | None = None,
    timeout: int = 3,
    max_workers: int = 8,
    cache: Dict[str, str] | None = None,
    db=None,
    target_url_validator: Callable[[str], None] | None = None,
) -> Dict[str, list[str]]:
    """Classify URLs into content buckets after applying target policy."""

    if allowed_types is None:
        allowed_types = ["webpage", "json", "pdf", "image", "video", "audio"]

    cache = cache if cache is not None else {}

    def get_type(u: str) -> str:
        # A cached classification contains no evidence about the redirect
        # chain used to obtain it. Re-run classification whenever a provider
        # policy is active so each redirect target is checked again.
        resolved = None if target_url_validator is not None else cache.get(u)
        if resolved is None:
            resolved = detect_url_type(
                u,
                timeout=timeout,
                db=db,
                target_url_validator=target_url_validator,
            )
            if target_url_validator is None:
                cache[u] = resolved
        return resolved

    fetchable_urls, external_links = _split_youtube_urls(urls)

    if len(fetchable_urls) > 1 and max_workers > 1:
        workers = min(max_workers, len(fetchable_urls))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            types = list(executor.map(get_type, fetchable_urls))
    else:
        types = [get_type(url) for url in fetchable_urls]

    categorized: Dict[str, list[str]] = {
        "websites": [],
        "documents": [],
        "images": [],
        "videos": [],
        "audios": [],
        "external_links": external_links,
    }

    for url, detected_type in zip(fetchable_urls, types):
        if detected_type not in allowed_types:
            continue
        if detected_type in {"webpage", "json"}:
            categorized["websites"].append(url)
        elif detected_type == "pdf":
            categorized["documents"].append(url)
        elif detected_type == "image":
            categorized["images"].append(url)
        elif detected_type == "video":
            categorized["videos"].append(url)
        elif detected_type == "audio":
            categorized["audios"].append(url)

    return categorized
