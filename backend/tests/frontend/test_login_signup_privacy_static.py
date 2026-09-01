from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AUTHENTICATION_JS = REPO_ROOT / "frontend" / "js" / "login" / "authentication.js"
LOGIN_HTML = REPO_ROOT / "frontend" / "login.html"
LOGIN_SCRIPT_JS = REPO_ROOT / "frontend" / "js" / "login" / "script.js"
ADMIN_PRIVACY_JS = REPO_ROOT / "frontend" / "js" / "admin" / "privacyPolicy.js"
SETTINGS_VALIDATION_PY = REPO_ROOT / "backend" / "app" / "settings" / "validation.py"


def test_signup_form_no_longer_renders_privacy_consent_checkbox():
    html = LOGIN_HTML.read_text(encoding="utf-8")

    assert 'id="signupPrivacyConsent"' not in html
    assert 'data-i18n="signup_privacy_consent_label"' not in html
    assert 'data-i18n="signup_privacy_consent_link"' not in html


def test_signup_flow_no_longer_submits_privacy_policy_fields():
    auth_source = AUTHENTICATION_JS.read_text(encoding="utf-8")
    script_source = LOGIN_SCRIPT_JS.read_text(encoding="utf-8")

    assert "privacy_policy_accepted" not in auth_source
    assert "privacy_policy_revision" not in auth_source
    assert "signupRequiresPrivacyPolicyConsent" not in script_source
    assert "signupPrivacyPolicyRevision" not in script_source


def test_admin_privacy_editor_only_exposes_notice_modes():
    admin_privacy_source = ADMIN_PRIVACY_JS.read_text(encoding="utf-8")
    settings_validation_source = SETTINGS_VALIDATION_PY.read_text(encoding="utf-8")

    assert "initializeAdminSingleSelect" in admin_privacy_source
    assert "i18n:updated" in admin_privacy_source
    assert "privacy_policy_notice_mode_required_opt_in" not in admin_privacy_source
    assert "Required acceptance modal" not in admin_privacy_source
    assert "enforce_privacy_policy_signup_acceptance" not in settings_validation_source
    assert "enforce_privacy_policy_access_acceptance" not in settings_validation_source
