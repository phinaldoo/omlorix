from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_JS = REPO_ROOT / "frontend" / "js"


def _read(relative_path: str) -> str:
    return (FRONTEND_JS / relative_path).read_text(encoding="utf-8")


def test_access_tokens_are_not_persisted_to_browser_storage():
    sources = "\n".join(path.read_text(encoding="utf-8") for path in FRONTEND_JS.rglob("*.js"))

    assert not re.search(r"(?:localStorage|sessionStorage)\.setItem\(['\"]access_token['\"]", sources)
    assert not re.search(r"(?:localStorage|sessionStorage)\.getItem\(['\"]access_token['\"]", sources)


def test_obsolete_access_token_storage_compatibility_is_absent():
    all_sources = "\n".join(path.read_text(encoding="utf-8") for path in FRONTEND_JS.rglob("*.js"))
    password_source = _read("common/passwordRequirements.js")
    notifications_source = _read("chat/workspaceNotifications.js")

    assert not re.search(r"(?:localStorage|sessionStorage).*['\"]access_token['\"]", all_sources)
    assert "clearPersistedAccessToken" not in all_sources
    for source in (password_source, notifications_source):
        assert "localStorage" not in source
        assert "sessionStorage" not in source
    assert "applyWorkspaceNotificationAuthHeaders" not in notifications_source
    assert "window.authedFetch" in notifications_source


def test_access_token_is_not_exposed_by_global_auth_helpers():
    auth_source = _read("common/auth.js")
    all_sources = "\n".join(path.read_text(encoding="utf-8") for path in FRONTEND_JS.rglob("*.js"))
    non_auth_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FRONTEND_JS.rglob("*.js")
        if path.relative_to(FRONTEND_JS).as_posix() != "common/auth.js"
    )

    assert auth_source.startswith("(function () {")
    assert "var access_token" not in auth_source
    assert "window.access_token" not in all_sources
    assert "window.resolveAccessToken" not in all_sources
    assert "window.applyAccessToken" not in all_sources
    assert "window.getAuthHeaders" not in auth_source
    assert "tokenData.access_token" not in auth_source
    assert "Authorization" not in auth_source
    assert "getAuthHeaders" not in non_auth_sources
