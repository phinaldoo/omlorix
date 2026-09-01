import logging
import sys
from pathlib import Path

import pytest
import requests


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.websearch import robots


@pytest.fixture(autouse=True)
def clear_robots_cache():
    robots._ROBOTS_CACHE.clear()
    yield
    robots._ROBOTS_CACHE.clear()


class _Response:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def test_robots_txt_unreachable_fails_closed(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise requests.ConnectionError("unreachable")

    monkeypatch.setattr(robots.requests, "get", fake_get)

    assert robots.check_robots_txt(["https://example.com/page"], timeout=1) == []


def test_robots_txt_missing_fails_closed(monkeypatch):
    monkeypatch.setattr(robots.requests, "get", lambda *_args, **_kwargs: _Response(404))

    assert robots.check_robots_txt(["https://example.com/page"], timeout=1) == []


def test_robots_txt_directives_are_applied(monkeypatch):
    monkeypatch.setattr(
        robots.requests,
        "get",
        lambda *_args, **_kwargs: _Response(
            200,
            "User-agent: *\nDisallow: /private\nAllow: /\n",
        ),
    )

    allowed = robots.check_robots_txt(
        [
            "https://example.com/public",
            "https://example.com/private/report",
        ],
        timeout=1,
    )

    assert allowed == ["https://example.com/public"]


def test_should_respect_robots_txt_defaults_to_enabled_and_warns_for_override(caplog):
    assert robots.should_respect_robots_txt({}) is True

    with caplog.at_level(logging.WARNING, logger=robots.logger.name):
        assert robots.should_respect_robots_txt({"respect_robots_txt": False}, provider="exa") is False

    assert "Robots.txt enforcement is disabled for websearch provider 'exa'" in caplog.text
