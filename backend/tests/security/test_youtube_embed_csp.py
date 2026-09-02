import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
YOUTUBE_NOCOOKIE_FRAME_SRC = re.compile(
    r"frame-src\s+[^;]*https://www\.youtube-nocookie\.com(?:\s|;)"
)


def test_youtube_nocookie_is_allowed_in_backend_csp():
    source = (
        REPO_ROOT / "backend" / "app" / "middleware" / "security_headers.py"
    ).read_text()

    assert YOUTUBE_NOCOOKIE_FRAME_SRC.search(source)


def test_youtube_nocookie_is_allowed_in_nginx_csp_templates():
    for path in (
        REPO_ROOT / "nginx" / "default.http.conf.template" / "default.conf",
    ):
        assert YOUTUBE_NOCOOKIE_FRAME_SRC.search(path.read_text())
