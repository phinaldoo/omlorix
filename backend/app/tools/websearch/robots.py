from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Any, Callable, Sequence, Tuple, TypeVar
from urllib.parse import urlparse
import urllib.robotparser
import requests

from app.tools.websearch.schemas import DEFAULT_USER_AGENT


logger = logging.getLogger(__name__)


class _DenyAllParser:
    __slots__ = ()

    def can_fetch(self, user_agent: str, url: str) -> bool:  # pragma: no cover - trivial
        return False


_ROBOTS_CACHE: OrderedDict[str, urllib.robotparser.RobotFileParser | _DenyAllParser] = OrderedDict()
_ROBOTS_CACHE_MAX_SIZE = 256
_Url = str
T = TypeVar("T")


def should_respect_robots_txt(settings: dict[str, Any] | None, *, provider: str | None = None) -> bool:
    """Return the robots enforcement decision, defaulting to enabled for missing settings."""
    raw_value = settings.get("respect_robots_txt", True) if isinstance(settings, dict) else True
    enabled = bool(raw_value)
    if not enabled:
        provider_label = provider or "unknown"
        logger.warning(
            "Robots.txt enforcement is disabled for websearch provider '%s'. "
            "Crawling may ignore site preferences.",
            provider_label,
        )
    return enabled



def _robots_allows(url: str, user_agent: str = DEFAULT_USER_AGENT, timeout: int = 5) -> bool:

    """Check robots.txt for a single URL. Deny if robots.txt cannot be confirmed."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = base_url + "/robots.txt"

        # Use cache per host to avoid refetching
        rp = _ROBOTS_CACHE.get(base_url)
        if rp is not None:
            _ROBOTS_CACHE.move_to_end(base_url)
        
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = requests.get(
                    robots_url,
                    timeout=timeout,
                    headers={"User-Agent": user_agent},
                    allow_redirects=False,
                )
            except Exception:
                rp = _DenyAllParser()
            else:
                if resp.status_code != 200 or not resp.text:
                    rp = _DenyAllParser()
                else:
                    rp.parse(resp.text.splitlines())
            _ROBOTS_CACHE[base_url] = rp
            if len(_ROBOTS_CACHE) > _ROBOTS_CACHE_MAX_SIZE:
                _ROBOTS_CACHE.popitem(last=False)

        return rp.can_fetch(user_agent, url)
    except Exception:
        return False


def check_robots_txt(urls: list[str], timeout: int = 5, user_agent: str = DEFAULT_USER_AGENT) -> list[str]:
    """Return only URLs permitted by robots.txt for the given user-agent."""
    if not urls:
        return []

    unique_urls = list(dict.fromkeys(urls))
    decisions: dict[str, bool] = {}

    max_workers = min(10, len(unique_urls))
    if max_workers > 1:
        def _evaluate(u: str) -> bool:
            return _robots_allows(u, user_agent=user_agent, timeout=timeout)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for u, decision in zip(unique_urls, executor.map(_evaluate, unique_urls)):
                decisions[u] = decision
    else:
        for u in unique_urls:
            decisions[u] = _robots_allows(u, user_agent=user_agent, timeout=timeout)

    allowed: list[str] = []
    for u in urls:
        if decisions.get(u, False):
            allowed.append(u)
    return allowed


def filter_entries_by_robots(
    entries: Sequence[T],
    *,
    url_getter: Callable[[T], str | None],
    user_agent: str = DEFAULT_USER_AGENT,
) -> Tuple[list[T], list[T]]:
    """Split entries into those allowed by robots.txt vs blocked ones."""
    if not entries:
        return [], []

    normalized_pairs: list[tuple[T, str | None]] = []
    urls_for_check: list[str] = []

    for entry in entries:
        raw_url = url_getter(entry)
        normalized_url = _normalize_url_for_robots(raw_url)
        normalized_pairs.append((entry, normalized_url))
        if normalized_url:
            urls_for_check.append(normalized_url)

    if not urls_for_check:
        return list(entries), []

    allowed_urls = check_robots_txt(urls_for_check, user_agent=user_agent)
    allowed_counter = Counter(allowed_urls)

    allowed_entries: list[T] = []
    blocked_entries: list[T] = []

    for entry, normalized_url in normalized_pairs:
        if not normalized_url:
            allowed_entries.append(entry)
            continue
        if allowed_counter.get(normalized_url, 0):
            allowed_entries.append(entry)
            allowed_counter[normalized_url] -= 1
        else:
            blocked_entries.append(entry)

    return allowed_entries, blocked_entries


def _normalize_url_for_robots(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return None
    return candidate


def filter_combined_results_by_robots(
    entries: list[dict[str, Any]],
    *,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[dict[str, Any]]:
    if not entries:
        return entries

    def _extract_url(entry: dict[str, Any]) -> str | None:
        if isinstance(entry, dict):
            url_value = entry.get("url")
            if isinstance(url_value, str):
                return url_value
        return None

    allowed, _blocked = filter_entries_by_robots(
        entries,
        url_getter=_extract_url,
        user_agent=user_agent,
    )
    return allowed