"""Static release contract keeping iOS app-site association disabled."""

from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
AASA_PATH = REPOSITORY_ROOT / "frontend" / ".well-known" / "apple-app-site-association"
NGINX_CONFIG_PATH = (
    REPOSITORY_ROOT / "nginx" / "default.http.conf.template" / "default.conf"
)


def test_aasa_is_not_bundled_or_reserved_by_nginx() -> None:
    configuration = NGINX_CONFIG_PATH.read_text(encoding="utf-8")

    assert not AASA_PATH.exists()
    assert "apple-app-site-association" not in configuration
    assert "webcredentials" not in configuration
