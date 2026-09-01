from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
USER_SETTINGS_HTML = REPO_ROOT / "frontend" / "index.html"
USER_SETTINGS_INIT_JS = REPO_ROOT / "frontend" / "js" / "chat" / "userSettings" / "init.js"
TWOFA_JS = REPO_ROOT / "frontend" / "js" / "common" / "twofa.js"
STEP_UP_JS = REPO_ROOT / "frontend" / "js" / "common" / "stepUp.js"


def test_user_settings_twofa_section_starts_hidden():
    source = USER_SETTINGS_HTML.read_text(encoding="utf-8")

    assert 'id="twoFactorSettingsSection"' in source
    assert '<div class="us-settings-section" id="twoFactorSettingsSection" hidden>' in source


def test_user_settings_twofa_visibility_depends_on_backend_flag():
    source = USER_SETTINGS_INIT_JS.read_text(encoding="utf-8")

    assert 'function applyTwoFactorVisibility(enabled)' in source
    assert 'enabled !== false' in source
    assert 'function applyTwoFactorSettingsState(data = {})' in source
    assert 'data?.two_factor_authentication_setup' in source
    assert 'data?.two_factor_authentication_forced' in source
    assert 'applyTwoFactorSettingsState(data);' in source


def test_user_settings_twofa_actions_reflect_enrollment_and_forced_policy():
    source = TWOFA_JS.read_text(encoding="utf-8")

    assert "function setTwoFactorSettingsState(options = {})" in source
    assert "setTwoFactorActionVisible(setup2FABtn, featureEnabled && !enrolled);" in source
    assert "setTwoFactorActionVisible(reset2FABtn, featureEnabled && enrolled);" in source
    assert "setTwoFactorActionVisible(deactivate2FAButton, featureEnabled && enrolled && !forced);" in source
    assert "document.getElementById('reset2FABtn')?.addEventListener('click', show2FASetup);" in source


def test_user_settings_twofa_deactivate_requires_the_shared_step_up_modal():
    source = TWOFA_JS.read_text(encoding="utf-8")

    deactivate_block = source.split("async function deactivate2FA() {", 1)[1].split(
        "}\n\n// Initialize interactions for the TFA overlay (inputs and copy-to-clipboard)",
        1,
    )[0]

    assert "ensureSecurityStepUp" in deactivate_block
    assert "authedFetch(`/api/v1/auth/twofa/deactivate`" in deactivate_block
    assert deactivate_block.index("ensureSecurityStepUp") < deactivate_block.index(
        "authedFetch(`/api/v1/auth/twofa/deactivate`"
    )


def test_shared_step_up_prepares_email_otp_and_supports_keyboard_dismissal():
    source = STEP_UP_JS.read_text(encoding="utf-8")

    assert "'/api/v1/auth/step-up/methods'" in source
    assert "passwordGroup.hidden = !methods.password" in source
    assert "otpGroup.hidden = !methods.otp" in source
    assert "passkeyButton.hidden = !methods.passkey" in source
    assert "submitButton.hidden = !(methods.password || methods.otp)" in source
    assert "'/api/v1/auth/step-up/otp/begin'" in source
    assert "tfa_email_code_sent_to_hint" in source
    assert "event.key === 'Escape'" in source
    assert "passwordInput.value = ''" in source
    assert "otpInput.value = ''" in source
