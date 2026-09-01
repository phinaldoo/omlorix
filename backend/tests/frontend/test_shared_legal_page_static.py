import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_legal_documents_share_one_frontend_shell():
    legal_html = REPO_ROOT / "frontend" / "legal.html"

    assert legal_html.is_file()
    assert not (REPO_ROOT / "frontend" / "privacy.html").exists()
    assert not (REPO_ROOT / "frontend" / "terms.html").exists()

    markup = legal_html.read_text(encoding="utf-8")
    assert 'data-page="legal"' in markup
    assert 'href="/privacy"' in markup
    assert 'href="/terms"' in markup
    assert 'aria-current="page"' not in markup
    assert 'aria-label="Legal documents"' in markup
    assert markup.index('/js/common/themeBoot.js') < markup.index('/css/common/init.css')
    init_styles = (REPO_ROOT / "frontend" / "css" / "common" / "init.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in init_styles

    # Legal-page colors must come from the shared semantic palette so saved
    # mode and accent preferences remain authoritative.
    styles = (REPO_ROOT / "frontend" / "css" / "legal.css").read_text(encoding="utf-8")
    assert re.search(r"#[0-9a-fA-F]{3,8}|\brgba?\(", styles) is None


def test_web_routes_serve_the_shared_legal_shell():
    for relative_path in (
        "nginx/default.http.conf.template/default.conf",
    ):
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for route in ("legal", "privacy", "terms"):
            route_block = f"location = /{route} {{\n        try_files /legal.html =404;\n    }}"
            assert route_block in source
