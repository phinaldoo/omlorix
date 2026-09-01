from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_CSP_FILES = (
    REPO_ROOT / "backend" / "app" / "main.py",
    REPO_ROOT / "nginx" / "default.http.conf.template" / "default.conf",
)
PRODUCTION_NGINX_FILES = (
    REPO_ROOT / "nginx" / "default.http.conf.template" / "default.conf",
)
HTML_FILES_WITH_BOOTSTRAP_SCRIPT = (
    REPO_ROOT / "frontend" / "index.html",
    REPO_ROOT / "frontend" / "chat_share.html",
    REPO_ROOT / "frontend" / "error.html",
    REPO_ROOT / "frontend" / "legal.html",
    REPO_ROOT / "frontend" / "login.html",
    REPO_ROOT / "frontend" / "server_setup.html",
)


def test_production_script_csp_disallows_inline_and_wasm_eval():
    for path in PRODUCTION_CSP_FILES:
        source = path.read_text(encoding="utf-8")

        assert "script-src 'self' https://apis.google.com;" in source
        assert "script-src 'self' 'unsafe-inline'" not in source
        assert "wasm-unsafe-eval" not in source


def test_production_csp_allows_only_required_google_picker_origins():
    """Google Picker may load its official script and Drive iframe, but not broad HTTPS."""

    for path in PRODUCTION_CSP_FILES:
        source = path.read_text(encoding="utf-8")

        assert "script-src 'self' https:;" not in source
        assert "https://apis.google.com" in source
        assert "https://docs.google.com" in source
        assert "https://drive.google.com" in source
        assert "https://accounts.google.com" in source


def test_production_connect_csp_is_not_broad_https_or_websocket():
    for path in PRODUCTION_CSP_FILES:
        source = path.read_text(encoding="utf-8")

        assert "connect-src 'self' https: ws: wss:" not in source
        assert "connect-src 'self' https://api.openai.com https://generativelanguage.googleapis.com wss://generativelanguage.googleapis.com;" in source


def test_bootstrap_pages_do_not_require_inline_script_execution():
    inline_script_pattern = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
    inline_handler_pattern = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)

    for path in HTML_FILES_WITH_BOOTSTRAP_SCRIPT:
        source = path.read_text(encoding="utf-8")

        assert inline_script_pattern.search(source) is None, path
        assert inline_handler_pattern.search(source) is None, path


def test_versioned_translation_json_uses_immutable_browser_cache():
    for path in PRODUCTION_NGINX_FILES:
        source = path.read_text(encoding="utf-8")

        assert 'default "public, max-age=31536000, immutable";' in source
        assert 'location ~* ^/i18n/.+\\.json$' in source
        assert "add_header Cache-Control $i18n_cache_control always;" in source
