import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_CONFIGS = (
    REPO_ROOT / "nginx" / "default.http.conf.template" / "default.conf",
)


def test_workspace_share_pages_do_not_retain_bearer_urls():
    for config_path in NGINX_CONFIGS:
        source = config_path.read_text(encoding="utf-8")
        marker = (
            "location ~ ^/(?:notes|todos|skills|prompts|agents|folders)/"
            "(?:clone|live|collaborate)/[^/]+/?$"
        )
        assert marker in source
        block = source.split(marker, 1)[1].split("\n    }", 1)[0]
        assert "access_log off;" in block
        assert 'Referrer-Policy "no-referrer"' in block
        assert 'X-Content-Type-Options "nosniff"' in block
        assert 'X-Frame-Options "SAMEORIGIN"' in block
        assert 'X-Robots-Tag "noindex, nofollow, noarchive"' in block
        assert 'Cache-Control "no-store, private"' in block
        assert "try_files /index.html =404;" in block


def test_all_share_capability_routes_are_excluded_from_nginx_access_logs():
    """Neither page nor API URLs may persist bearer share IDs or query tokens."""
    for config_path in NGINX_CONFIGS:
        source = config_path.read_text(encoding="utf-8")

        assert "if=$omlorix_access_log_enabled" in source
        for protected_route in (
            "~^/(?:canvas|chats)/shared/ 0;",
            "~^/(?:notes|todos|skills|prompts|agents|folders)/(?:clone|live|collaborate)/ 0;",
            "~^/projects/join/ 0;",
            "~^/api/v1/(?:chats/shared|files/canvas/shared)(?:/|$) 0;",
            "~^/api/v1/(?:notes|prompts|skills|projects|file-folders)/(?:shared|clone)(?:/|$) 0;",
            "~^/api/v1/todo/(?:shared|clone)(?:/|$) 0;",
            "~^/api/v1/agents/shared(?:/|$) 0;",
        ):
            assert protected_route in source

        assert source.count("access_log off;") >= 4


def test_retired_frontend_routes_are_not_supported():
    """Only current page routes should remain in the greenfield frontend."""
    notes_entry = REPO_ROOT / "frontend" / "js" / "chat" / "notes.js"
    notes_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [*sorted(notes_entry.with_suffix("").glob("*.js")), notes_entry]
    )
    nginx_source = NGINX_CONFIGS[0].read_text(encoding="utf-8")

    assert "path.includes('/notes/shared/')" not in notes_source
    assert "shared-note=" not in notes_source
    assert "location = /admin/chats" not in nginx_source
    assert "location ^~ /admin/chats/" not in nginx_source


def test_prompt_accept_errors_are_translated_in_every_supported_locale():
    i18n_root = REPO_ROOT / "frontend" / "i18n"
    required_keys = {
        "prompt_accept_empty_content",
        "prompt_accept_load_error_desc",
        "prompt_accept_load_error_title",
    }
    locale_files = sorted(i18n_root.glob("*/index.json"))
    assert locale_files
    for locale_file in locale_files:
        payload = json.loads(locale_file.read_text(encoding="utf-8"))
        assert required_keys <= payload.keys(), locale_file
        assert all(str(payload[key]).strip() for key in required_keys), locale_file
