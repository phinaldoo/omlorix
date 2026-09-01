from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
LOGIN_SCRIPT = REPO_ROOT / "frontend" / "js" / "login" / "script.js"
LOGIN_HTML = REPO_ROOT / "frontend" / "login.html"
INDEX_HTML = REPO_ROOT / "frontend" / "index.html"
FEDERATED_TERMS_JS = REPO_ROOT / "frontend" / "js" / "login" / "federatedTerms.js"
AUTH_JS = REPO_ROOT / "frontend" / "js" / "common" / "auth.js"
LOGIN_GENERAL_SCHEMA = (
    REPO_ROOT
    / "backend"
    / "app"
    / "admin"
    / "settings"
    / "schema_categories"
    / "login_general.py"
)
AUTH_ROUTER = REPO_ROOT / "backend" / "app" / "auth" / "router.py"
AUTH_UTILS = REPO_ROOT / "backend" / "app" / "auth" / "utils.py"


def test_login_terms_signup_consent_uses_server_enforcement_flag():
    source = LOGIN_SCRIPT.read_text(encoding="utf-8")

    assert "require_current_revision_for_signup" in source
    assert "termsOfServicePolicy?.signup_available" not in source


def test_login_terms_signup_consent_matches_privacy_consent_layout():
    html = LOGIN_HTML.read_text(encoding="utf-8")
    form_start = html.index('<form id="registerForm"')
    terms_start = html.index('id="signupTermsConsent"')
    button_start = html.index('id="signupButton"')

    assert form_start < terms_start < button_start
    assert 'id="signupTermsConsent" hidden>' in html
    assert 'class="form-group signup-consent" id="signupTermsConsent"' in html
    assert 'class="signup-consent-row"' in html
    assert 'id="signupTermsConsentHint"' in html
    assert 'class="signup-terms-consent"' not in html


def test_login_terms_pending_modal_is_available_for_social_and_sso_signup():
    html = LOGIN_HTML.read_text(encoding="utf-8")
    modal_start = html.index('id="federatedTermsOverlay"')
    warning_start = html.index('id="warningOverlay"')

    assert '/js/login/federatedTerms.js' in html
    assert modal_start < warning_start
    assert 'aria-labelledby="federatedTermsTitle"' in html
    assert 'id="federatedTermsConfirmButton"' in html
    assert 'id="federatedTermsCancelButton"' in html
    assert 'data-i18n="federated_terms_confirm"' in html


def test_federated_terms_modal_posts_confirm_and_cancel_requests():
    auth_source = (REPO_ROOT / "frontend" / "js" / "login" / "authentication.js").read_text(encoding="utf-8")
    modal_source = FEDERATED_TERMS_JS.read_text(encoding="utf-8")
    router_source = AUTH_ROUTER.read_text(encoding="utf-8")
    utils_source = AUTH_UTILS.read_text(encoding="utf-8")

    assert "getTermsOfServiceAcceptancePayload" in auth_source
    assert "socialTermsConsentCheckbox" not in auth_source
    assert "social_terms_pending" in modal_source
    assert "sso_terms_pending" in modal_source
    assert "/pending-terms/confirm" in modal_source
    assert "/pending-terms/cancel" in modal_source
    assert "isConfirmPending" in modal_source
    assert "/social/pending-terms/confirm" in router_source
    assert "/sso/pending-terms/confirm" in router_source
    assert "SOCIAL_PENDING_SIGNUP_COOKIE" in utils_source
    assert "SSO_PENDING_SIGNUP_COOKIE" in utils_source
    assert "Fernet" in utils_source
    assert "_PENDING_FEDERATED_SIGNUP_COOKIE_MAX_BYTES" in utils_source
    assert "_redirect_clearing_pending_federated_signup_cookie" in utils_source


def test_auth_redirects_required_terms_acceptance_to_login():
    auth_source = AUTH_JS.read_text(encoding="utf-8")
    login_source = FEDERATED_TERMS_JS.read_text(encoding="utf-8")
    index_source = INDEX_HTML.read_text(encoding="utf-8")

    assert "terms_required" in auth_source
    assert "redirectToTermsAcceptanceLogin" in auth_source
    assert "auth:termsAcceptanceRequired" in login_source
    assert "/api/v1/users/terms-of-service/accept" in login_source
    assert '/js/chat/termsOfServiceNotice.js' not in index_source


def test_terms_acceptance_uses_the_shared_login_flow_and_admin_schema_exposes_toggles():
    login_source = FEDERATED_TERMS_JS.read_text(encoding="utf-8")
    admin_source = LOGIN_GENERAL_SCHEMA.read_text(encoding="utf-8")

    assert "/api/v1/users/terms-of-service/accept" in login_source
    assert not (REPO_ROOT / "frontend" / "js" / "chat" / "termsOfServiceNotice.js").exists()
    assert "schema_login_general_enforce_terms_of_service_signup_acceptance" in admin_source
    assert "schema_login_general_enforce_terms_of_service_access_acceptance" in admin_source
