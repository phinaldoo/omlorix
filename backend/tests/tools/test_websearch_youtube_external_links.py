import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.websearch import utils as websearch_utils


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/shorts/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtu.be/dQw4w9WgXcQ",
        "https://www。youtube。com/watch?v=dQw4w9WgXcQ",
        r"https://www.youtube.com\@example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_hosts_are_identified_without_substring_matches(url):
    assert websearch_utils._is_youtube_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.example.test/watch?v=dQw4w9WgXcQ",
        "https://example.test/youtube.com/watch?v=dQw4w9WgXcQ",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_youtube_host_detection_rejects_lookalikes_and_non_http_urls(url):
    assert websearch_utils._is_youtube_url(url) is False


def test_shared_outbound_boundary_blocks_youtube_before_network_policy(monkeypatch):
    monkeypatch.setattr(
        websearch_utils,
        "assert_public_url_allowed",
        lambda *_args, **_kwargs: pytest.fail("YouTube URL reached network policy"),
    )

    with pytest.raises(HTTPException) as exc_info:
        websearch_utils._assert_websearch_url_allowed(
            None,
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
            feature="Web scrape redirect",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"code": "youtube_retrieval_disabled"}


def test_filter_urls_keeps_youtube_links_out_of_network_classification(monkeypatch):
    calls: list[str] = []

    def fake_detect_url_type(url, **_kwargs):
        calls.append(url)
        return "webpage"

    monkeypatch.setattr(websearch_utils, "detect_url_type", fake_detect_url_type)

    result = websearch_utils.filter_urls(
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com.example.test/page",
        ],
        max_workers=1,
    )

    assert calls == ["https://youtube.com.example.test/page"]
    assert result["websites"] == ["https://youtube.com.example.test/page"]
    assert result["external_links"] == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    ]
    assert "youtube" not in result


def test_combined_results_discard_youtube_content_without_policy_or_robots_requests(
    monkeypatch,
):
    youtube_url = "https://www。youtube。com/watch?v=dQw4w9WgXcQ"

    monkeypatch.setattr(
        websearch_utils,
        "_assert_websearch_url_allowed",
        lambda *_args, **_kwargs: pytest.fail("YouTube URL reached network policy"),
    )
    monkeypatch.setattr(
        websearch_utils,
        "check_robots_txt",
        lambda *_args, **_kwargs: pytest.fail("YouTube URL reached robots lookup"),
    )

    allowed, policy_blocked = websearch_utils._filter_allowed_webpage_entries(
        None,
        [{"source_url": youtube_url, "content": "provider-supplied transcript"}],
        feature="Combined web search result",
    )
    robots_allowed, robots_blocked = (
        websearch_utils._filter_webpage_entries_by_robots(allowed)
    )

    assert policy_blocked == []
    assert robots_blocked == []
    assert robots_allowed == websearch_utils._youtube_external_link_entries(
        [youtube_url]
    )


def test_direct_youtube_url_is_returned_as_an_unfetched_external_link(monkeypatch):
    youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    provider = SimpleNamespace(provider="aiohttp", settings={"respect_robots_txt": True})
    network_calls: list[str] = []

    monkeypatch.setattr(
        websearch_utils,
        "_resolve_direct_url_scrape_provider",
        lambda *_args, **_kwargs: provider,
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        websearch_utils,
        "filter_websearch_urls_by_domains",
        lambda urls, _provider: (list(urls), {}),
    )
    monkeypatch.setattr(
        websearch_utils,
        "_split_allowed_web_urls",
        lambda _db, urls, **_kwargs: (
            pytest.fail("YouTube URL reached network policy")
            if urls
            else ([], [])
        ),
    )
    monkeypatch.setattr(
        websearch_utils,
        "check_robots_txt",
        lambda urls, **_kwargs: network_calls.extend(urls) or list(urls),
    )
    monkeypatch.setattr(
        websearch_utils,
        "detect_url_type",
        lambda url, **_kwargs: network_calls.append(url) or "webpage",
    )
    monkeypatch.setattr(
        websearch_utils,
        "scrape",
        lambda urls, *_args, **_kwargs: network_calls.extend(urls) or [],
    )
    monkeypatch.setattr(websearch_utils, "_resolve_upload_limit_mb", lambda *_args: 10)

    response = websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id="scrape",
        search_provider_id=None,
        project_id=None,
        urls=[youtube_url],
    )

    assert network_calls == []
    content = response["result"][0]["content"]
    assert "youtube" not in content
    assert content["webpages"] == [
        {
            "url": youtube_url,
            "content": None,
            "title": None,
            "external_link_only": True,
            "retrieval_disabled": True,
            "id": 1,
        }
    ]


def test_search_result_youtube_url_never_reaches_native_fetch_pipeline(monkeypatch):
    youtube_url = "https://youtu.be/dQw4w9WgXcQ"
    redirector_url = "https://redirector.example/video"
    returned_youtube_url = "https://www。youtube。com/watch?v=dQw4w9WgXcQ"
    search_provider = SimpleNamespace(provider="duckduckgo", settings={})
    scrape_provider = SimpleNamespace(provider="aiohttp", settings={})
    providers = {"search": search_provider, "scrape": scrape_provider}
    policy_calls: list[list[str]] = []
    scrape_calls: list[list[str]] = []

    monkeypatch.setattr(
        websearch_utils,
        "get_websearch_provider",
        lambda _db, provider_id: providers[provider_id],
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        websearch_utils,
        "search",
        lambda *_args, **_kwargs: {
            "result": [{"url": youtube_url}, {"url": redirector_url}]
        },
    )
    monkeypatch.setattr(
        websearch_utils,
        "filter_websearch_urls_by_domains",
        lambda urls, _provider: (list(urls), {}),
    )
    def fake_split_allowed(_db, urls, **_kwargs):
        policy_calls.append(list(urls))
        return list(urls), []

    monkeypatch.setattr(websearch_utils, "_split_allowed_web_urls", fake_split_allowed)
    monkeypatch.setattr(
        websearch_utils,
        "should_respect_robots_txt",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        websearch_utils,
        "filter_urls",
        lambda urls, **_kwargs: {
            "websites": list(urls),
            "documents": [],
            "images": [],
            "videos": [],
            "audios": [],
            "external_links": [],
        },
    )
    def fake_scrape(urls, *_args, **_kwargs):
        scrape_calls.append(list(urls))
        return {
            "result": [
                {
                    "link": returned_youtube_url,
                    "content": "provider-supplied transcript",
                }
            ]
        }

    monkeypatch.setattr(websearch_utils, "scrape", fake_scrape)
    monkeypatch.setattr(
        websearch_utils,
        "build_websearch_usage_event",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        websearch_utils,
        "build_websearch_tool_meta",
        lambda *, base_meta, usage_events: {
            **base_meta,
            "usage_events": usage_events,
        },
    )

    response = websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id="scrape",
        search_provider_id="search",
        project_id=None,
        queries=["video"],
    )

    content = response["result"][0]["content"]
    assert policy_calls == [[redirector_url]]
    assert scrape_calls == [[redirector_url]]
    assert content["webpages"] == [
        {
            "url": returned_youtube_url,
            "content": None,
            "title": None,
            "external_link_only": True,
            "retrieval_disabled": True,
            "id": 1,
        },
        {
            "url": youtube_url,
            "content": None,
            "title": None,
            "external_link_only": True,
            "retrieval_disabled": True,
            "id": 2,
        }
    ]


def test_youtube_url_query_does_not_reach_combined_provider(monkeypatch):
    youtube_url = r"https://www.youtube.com\@example.com/watch?v=dQw4w9WgXcQ"
    provider = SimpleNamespace(provider="exa", settings={})

    monkeypatch.setattr(
        websearch_utils,
        "get_websearch_provider",
        lambda *_args, **_kwargs: provider,
    )
    monkeypatch.setattr(
        websearch_utils,
        "assert_websearch_provider_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        websearch_utils,
        "_provider_types",
        lambda _provider: {"combined"},
    )
    monkeypatch.setattr(
        websearch_utils,
        "_resolve_provider_request_locale",
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        websearch_utils,
        "run_combined_provider",
        lambda *_args, **_kwargs: pytest.fail("YouTube query reached provider"),
    )
    monkeypatch.setattr(
        websearch_utils,
        "build_websearch_tool_meta",
        lambda *, base_meta, usage_events: {
            **base_meta,
            "usage_events": usage_events,
        },
    )

    response = websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id="scrape",
        search_provider_id="search",
        project_id=None,
        queries=[youtube_url],
    )

    assert response["result"][0]["content"]["webpages"] == [
        {
            "url": youtube_url,
            "content": None,
            "title": None,
            "external_link_only": True,
            "retrieval_disabled": True,
            "id": 1,
        }
    ]
