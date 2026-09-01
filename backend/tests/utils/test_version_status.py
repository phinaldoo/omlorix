"""Regression tests for Omlorix update availability decisions."""

from __future__ import annotations

import pytest

from app.utils import utils as app_utils
from app.utils.versioning import compare_semantic_versions, is_beta_version


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("v0.9.19", "v0.1.0", 1),
        ("v0.10.0", "v0.9.19", 1),
        ("v1.2.3", "1.2.3", 0),
        ("v1.2.3-beta.2", "v1.2.3-beta.10", -1),
        ("v1.2.3-beta.10", "v1.2.3-beta.2", 1),
        ("v1.2.3-beta.1", "v1.2.3", -1),
        ("v1.2.3+build.2", "v1.2.3+build.1", 0),
        ("not-a-version", "v1.2.3", None),
    ],
)
def test_compare_semantic_versions(left: str, right: str, expected: int | None) -> None:
    """Version precedence must be numeric and SemVer-aware."""

    assert compare_semantic_versions(left, right) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("v1.2.3-beta.1", True),
        ("1.2.3-beta.10", True),
        ("v1.2.3", False),
        ("v1.2.3-alpha.1", False),
        ("not-a-version", False),
    ],
)
def test_is_beta_version_matches_release_workflow_tags(version: str, expected: bool) -> None:
    """Channel detection must follow the beta tags emitted by release.yml."""

    assert is_beta_version(version) is expected


@pytest.mark.parametrize(
    ("current_version", "include_beta"),
    [
        ("v1.2.3", False),
        ("v1.3.0-beta.2", True),
    ],
)
def test_remote_version_fetch_selects_channel_from_current_tag(
    monkeypatch,
    current_version: str,
    include_beta: bool,
) -> None:
    """Stable installs select stable releases while beta installs may see betas."""

    captured_request: dict[str, object] = {}

    class FakeResponse:
        """Minimal successful requests response for the version fetch."""

        def raise_for_status(self) -> None:
            """Mirror a successful HTTP status check."""

        def json(self) -> list[dict[str, object]]:
            """Return stable and beta GitHub releases in non-SemVer order."""

            return [
                {
                    "tag_name": "v1.3.0-beta.2",
                    "draft": False,
                    "prerelease": True,
                    "published_at": "2026-08-02T00:00:00Z",
                },
                {
                    "tag_name": "v1.2.0",
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-08-01T00:00:00Z",
                },
            ]

    def fake_get(url: str, **kwargs) -> FakeResponse:
        """Capture the outgoing query without performing network I/O."""

        captured_request["url"] = url
        captured_request.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(app_utils, "_CURRENT_VERSION", current_version)
    monkeypatch.setattr(app_utils.requests, "get", fake_get)

    expected_tag = "v1.3.0-beta.2" if include_beta else "v1.2.0"
    expected_type = "beta" if include_beta else "stable"
    expected_date = (
        "2026-08-02T00:00:00Z" if include_beta else "2026-08-01T00:00:00Z"
    )
    assert app_utils._fetch_remote_version() == {
        "tag": expected_tag,
        "release_date": expected_date,
        "release_type": expected_type,
        "release_url": None,
    }
    assert captured_request == {
        "url": app_utils._VERSION_CHECK_URL,
        "params": {"per_page": app_utils._VERSION_CHECK_RELEASE_LIMIT},
        "headers": {
            "Accept": "application/vnd.github+json",
            "User-Agent": "omlorix-version-check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        "timeout": app_utils._VERSION_CHECK_TIMEOUT,
    }


def test_github_release_selection_filters_unrelated_and_unpublished_releases() -> None:
    """Only published Omlorix stable/beta tags may drive admin notifications."""

    releases = [
        {"tag_name": "v9.0.0", "draft": True, "prerelease": False},
        {"tag_name": "server-launcher-v8.0.0", "draft": False, "prerelease": False},
        {"tag_name": "v3.0.0-alpha.1", "draft": False, "prerelease": True},
        {"tag_name": "v2.0.0-beta.10", "draft": False, "prerelease": True},
        {"tag_name": "v1.9.0", "draft": False, "prerelease": False},
        {"tag_name": "not-a-version", "draft": False, "prerelease": False},
    ]

    stable = app_utils._select_github_release(releases, include_beta=False)
    beta = app_utils._select_github_release(releases, include_beta=True)

    assert stable is not None and stable["tag"] == "v1.9.0"
    assert beta is not None and beta["tag"] == "v2.0.0-beta.10"


def test_github_release_selection_uses_semver_not_api_order() -> None:
    """GitHub's response ordering must not replace semantic-version precedence."""

    releases = [
        {"tag_name": "v1.9.0", "draft": False, "prerelease": False},
        {"tag_name": "v1.10.0", "draft": False, "prerelease": False},
        {"tag_name": "v1.8.0", "draft": False, "prerelease": False},
    ]

    selected = app_utils._select_github_release(releases, include_beta=False)

    assert selected is not None and selected["tag"] == "v1.10.0"


def test_version_status_does_not_offer_older_remote_version(monkeypatch) -> None:
    """An API version behind the installation must not appear as an update."""

    monkeypatch.setattr(app_utils, "_CURRENT_VERSION", "v0.9.19")
    monkeypatch.setattr(app_utils, "_latest_available_version", "v0.1.0")

    assert app_utils.get_version_status() == {
        "version": "v0.9.19",
        "latest_version": "v0.1.0",
        "update_available": False,
    }


def test_older_remote_version_does_not_create_notification(monkeypatch) -> None:
    """The background checker must not announce a downgrade as an update."""

    notification_calls: list[object] = []
    monkeypatch.setattr(app_utils, "_CURRENT_VERSION", "v0.9.19")
    monkeypatch.setattr(app_utils, "_latest_available_version", None)
    monkeypatch.setattr(
        app_utils,
        "_fetch_remote_version",
        lambda: {"tag": "v0.1.0", "build": 1},
    )
    monkeypatch.setattr(app_utils, "_get_persisted_notified_version", lambda: None)
    monkeypatch.setattr(
        app_utils,
        "create_admin_notification",
        lambda *args, **kwargs: notification_calls.append((args, kwargs)),
    )

    app_utils._check_for_new_version_and_notify()

    assert notification_calls == []
    assert app_utils._latest_available_version == "v0.1.0"


def test_newer_remote_version_still_creates_notification(monkeypatch) -> None:
    """A genuinely newer API version must continue to notify administrators."""

    class FakeDb:
        """Minimal database session used to capture notification creation."""

        def close(self) -> None:
            """Mirror the session cleanup method called by the checker."""

    notification_calls: list[dict[str, object]] = []
    persisted_versions: list[str] = []
    monkeypatch.setattr(app_utils, "_CURRENT_VERSION", "v0.9.19")
    monkeypatch.setattr(app_utils, "_latest_available_version", None)
    monkeypatch.setattr(
        app_utils,
        "_fetch_remote_version",
        lambda: {"tag": "v0.10.0", "build": 2},
    )
    monkeypatch.setattr(app_utils, "_get_persisted_notified_version", lambda: None)
    monkeypatch.setattr(app_utils, "SessionLocal", FakeDb)
    monkeypatch.setattr(
        app_utils,
        "create_admin_notification",
        lambda _db, **kwargs: notification_calls.append(kwargs),
    )
    monkeypatch.setattr(
        app_utils,
        "_persist_notified_version",
        persisted_versions.append,
    )

    app_utils._check_for_new_version_and_notify()

    assert len(notification_calls) == 1
    assert notification_calls[0]["details"] == {
        "current_version": "v0.9.19",
        "latest_version": "v0.10.0",
        "build": 2,
        "release_date": None,
    }
    assert persisted_versions == ["v0.10.0"]
