import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_SHARE_LOCATION_RE = re.compile(
    r"location \^~ /chats/shared/ \{\s*(?P<body>.*?)\n    \}",
    re.DOTALL,
)
CANVAS_SHARE_LOCATION_RE = re.compile(
    r"location \^~ /canvas/shared/ \{\s*(?P<body>.*?)\n    \}",
    re.DOTALL,
)
CHAT_SHARE_ACCESS_API_LOCATION_RE = re.compile(
    r"location = /api/v1/chats/shared/access \{\s*(?P<body>.*?)\n    \}",
    re.DOTALL,
)
CHAT_SHARE_FILES_API_LOCATION_RE = re.compile(
    r"location \^~ /api/v1/chats/shared/files/ \{\s*(?P<body>.*?)\n    \}",
    re.DOTALL,
)
SHARE_NOINDEX_META = '<meta name="robots" content="noindex, nofollow, noarchive">'
SHARE_NOINDEX_HEADER = 'X-Robots-Tag "noindex, nofollow, noarchive" always'
NO_STORE_CACHE_HEADER = 'Cache-Control "no-store, private" always'
NO_CACHE_PRAGMA_HEADER = 'Pragma "no-cache" always'
EXPIRES_ZERO_HEADER = 'Expires "0" always'
YOUTUBE_NOCOOKIE_FRAME_SRC_RE = re.compile(
    r"frame-src\s+[^;]*https://www\.youtube-nocookie\.com(?:\s|;)"
)


TRACKED_NGINX_CONFIGS = (
    REPO_ROOT / "nginx" / "default.http.conf.template" / "default.conf",
)
LOCAL_NGINX_CONFIGS = (
    REPO_ROOT / "nginx" / "default.conf",
)
NGINX_CONFIGS = TRACKED_NGINX_CONFIGS + tuple(path for path in LOCAL_NGINX_CONFIGS if path.exists())


def _chat_share_location_body(path: Path) -> str:
    source = path.read_text()
    match = CHAT_SHARE_LOCATION_RE.search(source)
    assert match, f"Missing /chats/shared/ location in {path}"
    return match.group("body")


def _canvas_share_location_body(path: Path) -> str:
    source = path.read_text()
    match = CANVAS_SHARE_LOCATION_RE.search(source)
    assert match, f"Missing /canvas/shared/ location in {path}"
    return match.group("body")


def _location_body(path: Path, pattern: re.Pattern) -> str:
    source = path.read_text()
    match = pattern.search(source)
    assert match, f"Missing expected location in {path}"
    return match.group("body")


def test_chat_share_page_allows_same_origin_blob_pdf_preview_in_nginx_csp_templates():
    for path in (
        REPO_ROOT / "nginx" / "default.http.conf.template" / "default.conf",
    ):
        body = _chat_share_location_body(path)

        assert "Content-Security-Policy" in body
        assert YOUTUBE_NOCOOKIE_FRAME_SRC_RE.search(body)
        assert "frame-ancestors 'self';" in body
        assert "frame-ancestors 'none';" not in body
        assert "Cross-Origin-Resource-Policy \"same-origin\"" in body


def test_public_share_pages_are_marked_noindex():
    for path in (
        REPO_ROOT / "frontend" / "chat_share.html",
        REPO_ROOT / "frontend" / "canvas_share.html",
    ):
        assert SHARE_NOINDEX_META in path.read_text()

    for path in NGINX_CONFIGS:
        chat_body = _chat_share_location_body(path)
        canvas_body = _canvas_share_location_body(path)

        assert SHARE_NOINDEX_HEADER in chat_body
        assert SHARE_NOINDEX_HEADER in canvas_body
        assert "access_log off;" in chat_body
        assert "access_log off;" in canvas_body


def test_nginx_rate_limits_allow_high_volume_authenticated_sessions():
    """Normal API and shared-file bursts should not throttle active dashboards."""
    for path in NGINX_CONFIGS:
        source = path.read_text(encoding="utf-8")

        assert "zone=api_limit_per_ip:10m rate=100r/s;" in source
        assert "zone=shared_chat_access_limit_per_ip:2m rate=120r/m;" in source
        assert "limit_conn conn_limit_per_ip 160;" in source
        assert "limit_req zone=api_limit_per_ip burst=200 nodelay;" in source
        assert source.count(
            "limit_req zone=shared_chat_access_limit_per_ip burst=60 nodelay;"
        ) == 2


def test_public_share_routes_disable_http_caching():
    for path in NGINX_CONFIGS:
        for body in (
            _chat_share_location_body(path),
            _canvas_share_location_body(path),
            _location_body(path, CHAT_SHARE_ACCESS_API_LOCATION_RE),
            _location_body(path, CHAT_SHARE_FILES_API_LOCATION_RE),
        ):
            assert NO_STORE_CACHE_HEADER in body
            assert NO_CACHE_PRAGMA_HEADER in body
            assert EXPIRES_ZERO_HEADER in body


def test_canvas_share_noindex_location_keeps_security_headers():
    for path in NGINX_CONFIGS:
        body = _canvas_share_location_body(path)

        assert "Content-Security-Policy" in body
        assert "Permissions-Policy" in body
        assert "Cross-Origin-Opener-Policy \"same-origin\"" in body
        assert "Cross-Origin-Resource-Policy \"same-origin\"" in body
