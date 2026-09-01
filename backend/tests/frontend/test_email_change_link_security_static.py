import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_email_change_bearer_requires_an_explicit_user_action():
    source = (PROJECT_ROOT / "frontend/js/login/authentication.js").read_text(
        encoding="utf-8"
    )
    html = (PROJECT_ROOT / "frontend/login.html").read_text(encoding="utf-8")

    initializer = source.split("async function initEmailChangeFlow()", 1)[1].split(
        "async function processPendingEmailChange()", 1
    )[0]
    processor = source.split("async function processPendingEmailChange()", 1)[1]

    assert "EMAIL_CHANGE_TOKEN_STORAGE_KEY" not in source
    assert "sessionStorage.setItem" not in initializer
    assert "fetch(" not in initializer
    assert "window.history.replaceState" in initializer
    assert "emailChangeActionButton.focus()" in initializer
    assert "fetch(`/api/v1/auth/email-change/${pending.kind}`" in processor
    assert "emailChangeActionButton?.addEventListener('click'" in source
    assert 'id="emailChangeActionButton"' in html


def test_email_change_confirmation_copy_exists_in_every_locale():
    required_keys = {
        "email_change_confirm_prompt",
        "email_change_cancel_prompt",
        "email_change_confirm_action",
        "email_change_cancel_action",
        "email_change_in_use",
        "email_change_retry",
    }
    locale_root = PROJECT_ROOT / "frontend/i18n"
    locales = sorted(path for path in locale_root.iterdir() if path.is_dir())

    assert locales
    for locale in locales:
        payload = json.loads((locale / "index.json").read_text(encoding="utf-8"))
        assert required_keys <= payload.keys(), locale.name
        assert all(str(payload[key]).strip() for key in required_keys), locale.name


def test_retryable_email_collision_keeps_confirmation_capability():
    source = (PROJECT_ROOT / "frontend/js/login/authentication.js").read_text(
        encoding="utf-8"
    )
    processor = source.split("async function processPendingEmailChange()", 1)[1]

    assert "response.status === 409" in processor
    collision_branch = processor.split("response.status === 409", 1)[1].split(
        "else if", 1
    )[0]
    assert "terminalOutcome = true" not in collision_branch
    assert "email_change_in_use" in collision_branch
