from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = REPO_ROOT / "frontend"


def _read_frontend(relative_path: str) -> str:
    return (FRONTEND_ROOT / relative_path).read_text(encoding="utf-8")


class _ElementAttributeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_twofa_setup_dialogs_describe_visible_totp_instructions_by_default():
    expected_description = "tfaSetupInstructionsTitle tfaStep1 tfaStep2 tfaStep3"

    for relative_path in ("login.html", "index.html"):
        source = _read_frontend(relative_path)
        parser = _ElementAttributeParser()
        parser.feed(source)
        overlay = next(
            (
                attributes
                for _, attributes in parser.elements
                if attributes.get("id") == "tfaSetupOverlay"
            ),
            None,
        )
        dialog = next(
            (
                attributes
                for _, attributes in parser.elements
                if attributes.get("role") == "dialog"
                and attributes.get("aria-labelledby") == "tfaSetupHeaderTitle"
            ),
            None,
        )

        assert overlay is not None, f"{relative_path} is missing the 2FA setup overlay"
        assert dialog is not None, f"{relative_path} is missing the 2FA setup dialog"
        assert dialog.get("aria-modal") == "true"
        assert dialog.get("aria-labelledby") == "tfaSetupHeaderTitle"
        assert dialog.get("aria-describedby") == expected_description
        assert 'tfaManualSecretContainer' not in source
        assert 'tfa_manual_code_title' not in source


def test_twofa_setup_layout_updates_dialog_description_for_email_setup():
    for relative_path in ("js/login/twofa.js", "js/common/twofa.js"):
        source = _read_frontend(relative_path)

        assert "function update2FASetupDescription(showTotpSetup, hasDeliveryHint)" in source
        assert (
            "setAttribute('aria-describedby', "
            "'tfaSetupInstructionsTitle tfaStep1 tfaStep2 tfaStep3')"
        ) in source
        assert "setAttribute('aria-describedby', 'tfaSetupDeliveryHint')" in source
        assert "removeAttribute('aria-describedby')" in source
        assert "update2FASetupDescription(showTotpSetup, hasDeliveryHint)" in source


def test_account_settings_twofa_setup_requires_step_up_before_opening():
    source = _read_frontend("js/common/twofa.js")
    setup_block = source.split("async function show2FASetup() {", 1)[1].split(
        "document.getElementById('tfaSetupPrimaryButton')?.addEventListener('click', verify2FASetup);",
        1,
    )[0]

    assert "window.ensureSecurityStepUp" in setup_block
    assert setup_block.index("await window.ensureSecurityStepUp()") < setup_block.index(
        "const overlayId = 'tfaSetupOverlay'"
    )
