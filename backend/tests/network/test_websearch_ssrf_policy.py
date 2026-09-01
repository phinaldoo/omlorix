import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.network import policy
from app.network.policy import (
    OutboundAccessMode,
    OutboundPolicySnapshot,
    OutboundRequestBlockedError,
    assert_outbound_peer_ip_allowed,
    assert_public_webhook_url_allowed,
    assert_public_resolved_ip_allowed,
    assert_websearch_provider_allowed,
    assert_public_url_allowed,
    get_websearch_provider_target,
    get_websearch_provider_targets,
    get_websearch_scrape_provider_target,
    is_public_web_url,
    validate_and_normalize_public_webhook_url,
)
from app.tools.websearch import utils as websearch_utils
from app.tools.websearch.scrape import you_scrape
from app.tools.websearch.search import you_search


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/internal.pdf",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/file",
    ],
)
def test_public_web_url_policy_blocks_private_and_non_web_urls(url):
    assert not is_public_web_url(url)
    with pytest.raises(OutboundRequestBlockedError):
        assert_public_url_allowed(None, url=url, feature="Direct URL fetch")


def test_public_web_url_policy_blocks_mixed_public_private_dns(monkeypatch):
    monkeypatch.setattr(
        policy,
        "_resolve_host_ips",
        lambda hostname: ("93.184.216.34", "127.0.0.1") if hostname == "mixed.example" else (),
    )

    assert not is_public_web_url("https://mixed.example/page")
    with pytest.raises(OutboundRequestBlockedError):
        assert_public_url_allowed(None, url="https://mixed.example/page", feature="Direct URL fetch")


def test_public_web_url_policy_allows_public_ipv6_literals():
    assert is_public_web_url("https://[2606:4700:4700::1111]/dns-query")


def test_public_webhook_url_requires_https_public_target(monkeypatch):
    monkeypatch.setattr(
        policy,
        "_resolve_host_ips",
        lambda hostname: ("93.184.216.34",) if hostname == "hooks.example" else (),
    )

    assert validate_and_normalize_public_webhook_url(" https://hooks.example/webhook ") == "https://hooks.example/webhook"

    for url in (
        "http://hooks.example/webhook",
        "https://localhost/webhook",
        "https://169.254.169.254/latest/meta-data/",
        "https://unresolved.example/webhook",
        f"https://hooks.example/{'x' * 2049}",
    ):
        with pytest.raises(HTTPException):
            validate_and_normalize_public_webhook_url(url)
        with pytest.raises(OutboundRequestBlockedError):
            assert_public_webhook_url_allowed(None, url=url, feature="Webhook delivery")


def test_public_resolved_ip_guard_defaults_to_allow_all_without_db():
    with pytest.raises(OutboundRequestBlockedError) as exc_info:
        assert_public_resolved_ip_allowed(
            None,
            ip_address="127.0.0.1",
            feature="Web scrape resolved peer",
        )

    assert exc_info.value.policy_mode == OutboundAccessMode.allow_all


def test_outbound_peer_guard_rejects_public_rebind_in_private_only(monkeypatch):
    """A private provider hostname cannot rebind its connection to a public IP."""

    monkeypatch.setattr(
        policy,
        "get_outbound_policy_snapshot",
        lambda _db: OutboundPolicySnapshot(
            offline_mode=False,
            mode=OutboundAccessMode.private_only,
            allowlist=(),
        ),
    )

    assert_outbound_peer_ip_allowed(
        object(),
        host="searxng.internal",
        ip_address="10.0.0.8",
        port=443,
        feature="SearXNG image search",
    )

    with pytest.raises(OutboundRequestBlockedError, match="connected peer"):
        assert_outbound_peer_ip_allowed(
            object(),
            host="searxng.internal",
            ip_address="93.184.216.34",
            port=443,
            feature="SearXNG image search",
        )


def test_outbound_peer_guard_honors_hostname_allowlist_for_pinned_ip(monkeypatch):
    """Allowlist-only providers retain hostname-based allowlist compatibility."""

    monkeypatch.setattr(
        policy,
        "get_outbound_policy_snapshot",
        lambda _db: OutboundPolicySnapshot(
            offline_mode=False,
            mode=OutboundAccessMode.allowlist_only,
            allowlist=("searxng.example",),
        ),
    )

    assert_outbound_peer_ip_allowed(
        object(),
        host="searxng.example",
        ip_address="93.184.216.34",
        port=443,
        feature="SearXNG image search",
    )

    with pytest.raises(OutboundRequestBlockedError, match="configured allowlist"):
        assert_outbound_peer_ip_allowed(
            object(),
            host="searxng.example",
            ip_address="169.254.169.254",
            port=80,
            feature="SearXNG image search",
        )

    with pytest.raises(OutboundRequestBlockedError, match="configured allowlist"):
        assert_outbound_peer_ip_allowed(
            object(),
            host="redirect.example",
            ip_address="93.184.216.34",
            port=443,
            feature="SearXNG image search",
        )

    monkeypatch.setattr(
        policy,
        "get_outbound_policy_snapshot",
        lambda _db: OutboundPolicySnapshot(
            offline_mode=False,
            mode=OutboundAccessMode.allowlist_only,
            allowlist=("searxng.internal", "10.0.0.0/8"),
        ),
    )
    assert_outbound_peer_ip_allowed(
        object(),
        host="searxng.internal",
        ip_address="10.0.0.8",
        port=80,
        feature="SearXNG image search",
    )


def test_direct_extension_shortcut_private_url_is_blocked():
    assert websearch_utils.detect_url_type("http://127.0.0.1/file.pdf", db=None) == "blocked"


def test_custom_websearch_policy_targets_include_scrape_base_url():
    assert get_websearch_provider_targets(
        "custom",
        {
            "base_url": "https://custom.example/search",
            "scrape_base_url": "https://scrape.example/scrape",
        },
        include_scrape_target=True,
    ) == ["https://custom.example/search", "https://scrape.example/scrape"]


def test_ollama_websearch_policy_target_is_the_hosted_service():
    """The policy must inspect Ollama's real, fixed network destination."""

    settings = {
        "api_key": "redacted",
        # The Ollama SDK path does not support a configurable host. Even stale
        # or manually persisted settings must not misrepresent its destination.
        "base_url": "http://localhost:11434",
    }

    assert get_websearch_provider_target("ollama", settings) == "https://ollama.com"
    assert get_websearch_scrape_provider_target("ollama", settings) == "https://ollama.com"
    assert get_websearch_provider_targets(
        "ollama",
        settings,
        include_scrape_target=True,
    ) == ["https://ollama.com"]


@pytest.mark.parametrize(
    ("offline_mode", "mode", "allowlist", "should_block"),
    [
        pytest.param(False, "allow_all", [], False, id="allow-all"),
        pytest.param(False, "allowlist_only", ["ollama.com"], False, id="allowlist-real-host"),
        pytest.param(False, "allowlist_only", ["ollama"], True, id="reject-provider-name-workaround"),
        pytest.param(False, "private_only", [], True, id="private-only"),
        # Offline Mode deliberately overrides Allow all with Private only, so
        # the public hosted provider must still fail.
        pytest.param(True, "allow_all", [], True, id="offline-mode"),
        pytest.param(False, "deny_all", ["ollama.com"], True, id="deny-all"),
    ],
)
def test_ollama_provider_guard_enforces_policy_against_real_host(
    monkeypatch,
    offline_mode,
    mode,
    allowlist,
    should_block,
):
    """All global modes must evaluate the hosted URL, never the label ``ollama``."""

    settings_data = {
        "offline_mode": offline_mode,
        "external_requests_mode": mode,
        "external_requests_allowlist": allowlist,
    }
    monkeypatch.setattr(
        policy,
        "get_settings_page",
        lambda _db, _page: SimpleNamespace(data=settings_data),
    )
    monkeypatch.setattr(
        policy,
        "_resolve_host_ips",
        lambda hostname: ("93.184.216.34",) if hostname == "ollama.com" else (),
    )
    provider = SimpleNamespace(provider="ollama", settings={"api_key": "redacted"})

    if not should_block:
        assert_websearch_provider_allowed(None, provider, feature="Ollama hosted request")
        return

    with pytest.raises(OutboundRequestBlockedError) as exc_info:
        assert_websearch_provider_allowed(None, provider, feature="Ollama hosted request")

    assert exc_info.value.target == "https://ollama.com"


def test_you_policy_target_matches_search_and_contents_requests(monkeypatch):
    """The provider guard and both adapters must identify the same hosted service."""

    request_urls: list[str] = []
    search_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"results": {"web": []}},
    )
    contents_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: [],
    )
    monkeypatch.setattr(
        you_search.requests,
        "get",
        lambda url, **_kwargs: request_urls.append(url) or search_response,
    )
    monkeypatch.setattr(
        you_scrape.requests,
        "post",
        lambda url, **_kwargs: request_urls.append(url) or contents_response,
    )

    you_search.you_search_urls("redacted", "test query")
    you_scrape.you_scrape_urls("redacted", ["https://example.com"])

    policy_target = get_websearch_provider_target("you", {})
    assert policy_target == "https://ydc-index.io"
    assert get_websearch_scrape_provider_target("you", {}) == policy_target
    assert request_urls == [
        f"{policy_target}/v1/search",
        f"{policy_target}/v1/contents",
    ]


@pytest.mark.parametrize(
    ("allowlist", "should_block"),
    [
        pytest.param(["ydc-index.io"], False, id="real-host"),
        pytest.param(["api.ydc-index.io"], True, id="obsolete-mapped-host"),
    ],
)
def test_you_provider_guard_uses_real_host_for_search_and_scrape(
    monkeypatch,
    allowlist,
    should_block,
):
    """Allowlist-only checks must use the destination contacted by both adapters."""

    monkeypatch.setattr(
        policy,
        "get_settings_page",
        lambda _db, _page: SimpleNamespace(
            data={
                "offline_mode": False,
                "external_requests_mode": "allowlist_only",
                "external_requests_allowlist": allowlist,
            }
        ),
    )
    provider = SimpleNamespace(provider="you", settings={"api_key": "redacted"})

    for use_scrape_target in (False, True):
        if not should_block:
            assert_websearch_provider_allowed(
                None,
                provider,
                feature="You.com hosted request",
                use_scrape_target=use_scrape_target,
            )
            continue

        with pytest.raises(OutboundRequestBlockedError) as exc_info:
            assert_websearch_provider_allowed(
                None,
                provider,
                feature="You.com hosted request",
                use_scrape_target=use_scrape_target,
            )

        assert exc_info.value.target == "https://ydc-index.io"


def test_custom_websearch_policy_checks_only_the_active_request_target(monkeypatch):
    monkeypatch.setattr(
        policy,
        "get_outbound_policy_snapshot",
        lambda _db: OutboundPolicySnapshot(
            offline_mode=False,
            mode=OutboundAccessMode.allowlist_only,
            allowlist=("search.example",),
        ),
    )
    provider = SimpleNamespace(
        provider="custom",
        settings={
            "base_url": "https://search.example/search",
            "scrape_base_url": "https://scrape.example/scrape",
        },
    )

    assert_websearch_provider_allowed(None, provider, feature="Web search provider request")
    with pytest.raises(OutboundRequestBlockedError):
        assert_websearch_provider_allowed(
            None,
            provider,
            feature="Web scrape provider request",
            use_scrape_target=True,
        )


def test_combined_result_robots_filter_only_receives_policy_allowed_urls(monkeypatch):
    robots_calls = []

    def fake_assert(_db, url, *, feature):
        if "127.0.0.1" in url:
            raise HTTPException(status_code=403, detail="blocked")

    def fake_robots(urls, *, user_agent):
        robots_calls.append(list(urls))
        return list(urls)

    monkeypatch.setattr(websearch_utils, "_assert_websearch_url_allowed", fake_assert)
    monkeypatch.setattr(websearch_utils, "check_robots_txt", fake_robots)

    allowed, policy_blocked = websearch_utils._filter_allowed_webpage_entries(
        None,
        [{"url": "http://127.0.0.1/private"}, {"url": "https://example.com/public"}],
        feature="Combined web search result",
    )
    robots_allowed, robots_blocked = websearch_utils._filter_webpage_entries_by_robots(allowed)

    assert robots_calls == [["https://example.com/public"]]
    assert robots_allowed == [{"url": "https://example.com/public"}]
    assert robots_blocked == []
    assert policy_blocked[0]["url"] == "http://127.0.0.1/private"


def test_web_search_combined_provider_applies_policy_before_robots(monkeypatch):
    robots_calls = []
    provider = SimpleNamespace(
        id="provider-1",
        provider="exa",
        settings={"respect_robots_txt": True},
    )

    def fake_assert(_db, url, *, feature):
        if "127.0.0.1" in url:
            raise HTTPException(status_code=403, detail="blocked")

    def fake_robots(urls, *, user_agent):
        robots_calls.append(list(urls))
        return list(urls)

    monkeypatch.setattr(websearch_utils, "get_websearch_provider", lambda _db, _provider_id: provider)
    monkeypatch.setattr(websearch_utils, "assert_websearch_provider_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(websearch_utils, "_get_provider_types", lambda _provider: {"combined"})
    monkeypatch.setattr(
        websearch_utils,
        "run_combined_provider",
        lambda _provider, _query, country: {
            "result": [
                {"url": "http://127.0.0.1/private", "title": "Private"},
                {"url": "https://example.com/public", "title": "Public"},
            ],
            "metadata": {},
        },
    )
    monkeypatch.setattr(websearch_utils, "_assert_websearch_url_allowed", fake_assert)
    monkeypatch.setattr(websearch_utils, "check_robots_txt", fake_robots)
    monkeypatch.setattr(websearch_utils, "build_websearch_usage_event", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        websearch_utils,
        "build_websearch_tool_meta",
        lambda *, base_meta, usage_events: {**base_meta, "usage_events": usage_events},
    )

    response = websearch_utils.web_search(
        None,
        "user-1",
        scrape_provider_id=None,
        search_provider_id="provider-1",
        project_id=None,
        queries=["test query"],
    )

    assert robots_calls == [["https://example.com/public"]]
    webpages = response["result"][0]["content"]["webpages"]
    assert [entry["url"] for entry in webpages] == [
        "https://example.com/public",
        "http://127.0.0.1/private",
    ]
    assert webpages[1]["blocked_by_policy"] is True


def test_policy_checked_redirects_block_private_redirect(monkeypatch):
    calls = []

    class RedirectResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/secret"}

        @property
        def is_redirect(self):
            return True

        @property
        def is_permanent_redirect(self):
            return False

        def close(self):
            pass

    def fake_request(method, url, *, feature, **kwargs):
        calls.append(url)
        return RedirectResponse()

    def fake_assert(_db, url, *, feature):
        if "127.0.0.1" in url:
            raise HTTPException(status_code=403, detail="blocked")

    monkeypatch.setattr(websearch_utils, "_assert_websearch_url_allowed", fake_assert)
    monkeypatch.setattr(websearch_utils, "public_web_request", fake_request)

    with pytest.raises(HTTPException):
        websearch_utils._request_with_policy_checked_redirects(
            None,
            "GET",
            "https://example.com/start",
            feature="Direct URL fetch",
            timeout=1,
        )

    assert calls == ["https://example.com/start"]


def test_policy_checked_request_blocks_connect_time_private_peer(monkeypatch):
    calls = []

    def fake_assert(_db, url, *, feature):
        calls.append((url, feature))

    def fake_request(method, url, *, feature, **kwargs):
        raise OutboundRequestBlockedError(
            target="rebind.example:443 (127.0.0.1)",
            feature=feature,
            policy_mode=OutboundAccessMode.allow_all,
            reason="connected peer IP is not publicly routable",
        )

    monkeypatch.setattr(websearch_utils, "_assert_websearch_url_allowed", fake_assert)
    monkeypatch.setattr(websearch_utils, "public_web_request", fake_request)

    with pytest.raises(HTTPException) as exc_info:
        websearch_utils._request_with_policy_checked_redirects(
            None,
            "GET",
            "https://rebind.example/start",
            feature="Direct URL fetch",
            timeout=1,
        )

    assert "connected peer IP is not publicly routable" in exc_info.value.detail
    assert calls == [("https://rebind.example/start", "Direct URL fetch")]


def test_websearch_download_does_not_reuse_metadata_only_duplicate(monkeypatch, tmp_path):
    remote_bytes = b"%PDF-REMOTE-DOWNLOADED-CONTENT%"
    seen_duplicate_lookup = {}
    persisted = {}

    class DownloadResponse:
        status_code = 200
        headers = {
            "Content-Disposition": 'attachment; filename="quarterly.pdf"',
            "Content-Type": "application/pdf",
        }

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield remote_bytes[:10]
            yield remote_bytes[10:]

    def fake_find_duplicate(
        _db,
        user_id,
        original_filename,
        file_type,
        file_size,
        content_sha256=None,
        project_id=None,
        folder_id=None,
    ):
        seen_duplicate_lookup.update(
            {
                "user_id": user_id,
                "original_filename": original_filename,
                "file_type": file_type,
                "file_size": file_size,
                "content_sha256": content_sha256,
                "project_id": project_id,
                "folder_id": folder_id,
            }
        )
        # Simulate the previous vulnerable metadata-only behavior: the existing private file
        # would have matched by user, filename, MIME type, and length, but not by content hash.
        return None if content_sha256 else SimpleNamespace(id="existing-private-file-id")

    def fake_persist_generated_file_path(_db, **kwargs):
        persisted.update(kwargs)
        return SimpleNamespace(id="downloaded-file-id")

    monkeypatch.setattr(websearch_utils, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(websearch_utils, "_assert_websearch_url_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(websearch_utils, "_head_request_with_policy_redirects", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        websearch_utils,
        "_requests_request_with_policy_redirects",
        lambda *_args, **_kwargs: DownloadResponse(),
    )
    monkeypatch.setattr(websearch_utils, "_find_duplicate_file", fake_find_duplicate)
    monkeypatch.setattr(websearch_utils, "persist_generated_file_path", fake_persist_generated_file_path)

    file_id = websearch_utils._download_and_save_url(
        "https://attacker.example/quarterly.pdf",
        "user-1",
        db=object(),
        max_upload_mb=1,
        project_id="project-1",
    )

    expected_sha256 = hashlib.sha256(remote_bytes).hexdigest()
    assert file_id == "downloaded-file-id"
    assert seen_duplicate_lookup == {
        "user_id": "user-1",
        "original_filename": "quarterly.pdf",
        "file_type": "application/pdf",
        "file_size": len(remote_bytes),
        "content_sha256": expected_sha256,
        "project_id": None,
        "folder_id": None,
    }
    assert persisted["meta"]["sha256"] == expected_sha256
    assert persisted["project_id"] is None
