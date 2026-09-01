from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[3]
AUTHENTICATION_JS = REPO_ROOT / "frontend" / "js" / "login" / "authentication.js"
COMMON_AUTH_JS = REPO_ROOT / "frontend" / "js" / "common" / "auth.js"
LOGIN_HTML = REPO_ROOT / "frontend" / "login.html"
LOGIN_SCRIPT_JS = REPO_ROOT / "frontend" / "js" / "login" / "script.js"
PASSWORD_REQUIREMENTS_JS = REPO_ROOT / "frontend" / "js" / "common" / "passwordRequirements.js"
NGINX_CONFIGS = (
    REPO_ROOT / "nginx" / "default.http.conf.template" / "default.conf",
)


def _extract_function(source: str, name: str) -> str:
    match = re.search(rf"function {re.escape(name)}\(\) \{{", source)
    assert match is not None

    depth = 0
    for index in range(match.end() - 1, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():index + 1]

    raise AssertionError(f"Could not extract {name}")


def test_password_reset_link_is_not_hidden_until_failed_login():
    source = AUTHENTICATION_JS.read_text(encoding="utf-8")
    update_visibility = _extract_function(source, "updateForgotPasswordVisibility")

    assert "failedPasswordAttempts" not in update_visibility
    assert "Boolean(enablePasswordReset)" in update_visibility
    assert "signinFlowState.passwordResetEnabled" in update_visibility
    assert "signinFlowState.stage === 'entry'" in update_visibility
    assert "signinFlowState.stage === 'methods'" in update_visibility
    assert re.search(r"signinEmailInput\?\.[\s\S]*?\.trim\(\)\.length > 0", update_visibility)


def test_password_reset_entry_points_exist_on_identifier_and_password_steps():
    html = LOGIN_HTML.read_text(encoding="utf-8")
    authentication = AUTHENTICATION_JS.read_text(encoding="utf-8")
    login_script = LOGIN_SCRIPT_JS.read_text(encoding="utf-8")

    assert 'id="forgotPasswordEntryLink"' in html
    assert 'id="forgotPasswordLink"' in html
    assert 'id="passwordResetConfirmStage"' in html
    assert 'id="resetPasswordForm"' in html
    assert 'data-password-role="confirm"' in html
    assert 'data-i18n="password_reset_cta"' in html
    assert "window.updateSigninPasswordResetVisibility = updateForgotPasswordVisibility" in authentication
    assert "window.syncPasswordActionFlowVisibility = syncPasswordActionFlowVisibility" in authentication
    assert "window.updateSigninPasswordResetVisibility()" in login_script
    assert "window.syncPasswordActionFlowVisibility()" in login_script


def test_required_password_change_reuses_login_form_and_routes():
    html = LOGIN_HTML.read_text(encoding="utf-8")
    authentication = AUTHENTICATION_JS.read_text(encoding="utf-8")
    password_requirements = PASSWORD_REQUIREMENTS_JS.read_text(encoding="utf-8")

    assert not (REPO_ROOT / "frontend" / "change_password.html").exists()
    assert 'id="currentPasswordGroup" hidden' in html
    assert 'data-password-role="current"' in html
    assert 'id="passwordActionTitle"' in html
    assert "initRequiredPasswordChangeFlow" in authentication
    assert "setSigninStage('changePassword')" in authentication
    assert "await authBootstrap" in authentication
    assert "window.requiredPasswordActionMode === 'set'" in authentication
    assert "window.isRequiredPasswordChangeFlow = mode !== 'reset'" in authentication
    assert "if (!isPasswordActionFlowActive())" in authentication
    assert "goToStep(" not in password_requirements

    for config_path in NGINX_CONFIGS:
        config = config_path.read_text(encoding="utf-8")
        assert re.search(
            r"location = /change_password\s*\{\s*try_files /login\.html =404;",
            config,
        )


def test_password_change_copy_lives_in_every_login_dictionary():
    i18n_root = REPO_ROOT / "frontend" / "i18n"
    for locale_dir in i18n_root.iterdir():
        if not locale_dir.is_dir():
            continue
        login_dictionary = locale_dir / "login.json"
        if not login_dictionary.exists():
            continue
        assert '"password_change_required_intro"' in login_dictionary.read_text(encoding="utf-8")
        assert not (locale_dir / "change_password.json").exists()


def test_authenticated_login_redirect_skips_password_reset_tokens():
    auth_source = COMMON_AUTH_JS.read_text(encoding="utf-8")
    reset_intent = _extract_function(auth_source, "hasPasswordResetConfirmIntent")

    assert "window.location.hash" in reset_intent
    assert "window.location.search" in reset_intent
    assert "password_reset_token" in reset_intent
    assert "pathMeta.isPublicLoginPage && refreshed && !isAddAccountMode() && !hasPasswordResetConfirmIntent()" in auth_source
