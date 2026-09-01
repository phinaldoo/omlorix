import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
USER_SETTINGS_PASSKEYS_JS = REPO_ROOT / "frontend" / "js" / "chat" / "userSettings" / "passkeys.js"


def test_passkey_inventory_is_not_logged_to_browser_console():
    source = USER_SETTINGS_PASSKEYS_JS.read_text(encoding="utf-8")

    assert not re.search(r"\bconsole\.(?:debug|error|info|log|warn)\s*\(", source)


def test_user_settings_passkey_setup_does_not_require_step_up():
    source = USER_SETTINGS_PASSKEYS_JS.read_text(encoding="utf-8")
    assert "async function setupNewPasskey() {" in source
    assert "\ndocument.addEventListener('DOMContentLoaded', () => {" in source
    setup_block = source.split("async function setupNewPasskey() {", 1)[1].split(
        "\ndocument.addEventListener('DOMContentLoaded', () => {",
        1,
    )[0]

    assert "ensureSecurityStepUp" not in setup_block
