from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AUTHENTICATION_JS = REPO_ROOT / "frontend" / "js" / "login" / "authentication.js"
LOGIN_SCRIPT_JS = REPO_ROOT / "frontend" / "js" / "login" / "script.js"
LOGIN_HTML = REPO_ROOT / "frontend" / "login.html"
WEBAUTHN_JS = REPO_ROOT / "frontend" / "js" / "common" / "webauthn.js"


def test_login_uses_server_methods_not_account_specific_methods():
    source = AUTHENTICATION_JS.read_text(encoding="utf-8")

    assert "server_methods" in source
    assert "result?.methods" not in source
    assert "discoveredMethods" not in source


def test_passkey_login_copy_is_not_account_specific():
    source = AUTHENTICATION_JS.read_text(encoding="utf-8")
    html = LOGIN_HTML.read_text(encoding="utf-8")

    assert "for this account" not in source
    assert "Sign in with passkey" not in html
    assert "Try a passkey" in html


def test_login_bootstrap_matches_the_enabled_fresh_server_default():
    source = LOGIN_SCRIPT_JS.read_text(encoding="utf-8")

    assert "let enablePasskeys = true;" in source
    # A failed settings request still fails closed for passkey UI controls.
    assert "enablePasskeys = false;" in source


def test_passkey_not_allowed_error_is_generic():
    source = AUTHENTICATION_JS.read_text(encoding="utf-8")

    assert "passkey_not_found_or_cancelled" in source
    assert "No matching passkey was found on this device" in source


def test_passkey_auto_prompt_requires_local_hint():
    source = AUTHENTICATION_JS.read_text(encoding="utf-8")

    assert "hasPasskeyAutoPromptHint" in source
    assert "passesAutoPromptPasskeyBaseGates" in source
    assert "maybeAutoPromptPasskey(identifier)" in source


def test_passkey_auto_prompt_hint_storage_is_hashed_and_origin_scoped():
    source = WEBAUTHN_JS.read_text(encoding="utf-8")

    assert "omlorix.passkeyAutoPromptHints.v1" in source
    assert "crypto.subtle.digest" in source
    assert "omlorix-passkey-hint:${origin}:${normalizedIdentifier}" in source
    assert "markPasskeyAutoPromptHint" in source
    assert "clearPasskeyAutoPromptHint" in source
