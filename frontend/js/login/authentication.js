// ------------------------------
// Signin 
// ------------------------------
const loginTranslate = (key, fallback) =>
    typeof window.getTranslation === 'function'
        ? window.getTranslation(key, fallback)
        : fallback;
const loginFormat = (key, fallback, vars) =>
    typeof window.formatTranslation === 'function'
        ? window.formatTranslation(key, fallback, vars)
        : Object.entries(vars || {}).reduce(
            (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
            loginTranslate(key, fallback),
        );

function showLoginAccountLockWarning(result = {}) {
    const expiresSeconds = Number.isFinite(result.expires) ? Math.max(0, result.expires) : 0;
    let unlockAt = '';
    if (expiresSeconds > 0) {
        const unlockDate = new Date(Date.now() + expiresSeconds * 1000);
        try {
            unlockAt = unlockDate.toLocaleString(undefined, {
                dateStyle: 'medium',
                timeStyle: 'short'
            });
        } catch (error) {
            unlockAt = unlockDate.toString();
        }
    }
    const rawReason = typeof result.reason === 'string' ? result.reason.trim() : '';
    const localizedReason = rawReason && rawReason.toLowerCase() === 'too many failed sign-in attempts'
        ? loginTranslate('lock_reason_too_many_failed_attempts', 'Too many failed sign-in attempts')
        : rawReason;
    showWarning(
        expiresSeconds,
        loginTranslate('lock_title', 'Account Temporarily Locked'),
        loginTranslate('lock_message', 'Your account has been temporarily locked due to <strong>multiple failed login attempts</strong>. This is a security measure to protect your account from unauthorized access.'),
        {
            reason: localizedReason,
            unlockAt,
            type: result.type || ''
        }
    );
}

window.showLoginAccountLockWarning = showLoginAccountLockWarning;

function showInactiveAccountWarning() {
    // Keep the inactive-account UX identical for password and passkey sign-in.
    showWarning(
        0,
        loginTranslate('inactive_title', 'Account Inactive'),
        loginTranslate('inactive_message', 'Your account has been <strong>deactivated</strong>. Please contact support for further assistance.'),
    );
}

window.showInactiveAccountWarning = showInactiveAccountWarning;

const loginForm = document.getElementById('loginForm');
const MAX_STORED_ACCOUNTS = 5;
const RESET_PASSWORD_TOKEN_STORAGE_KEY = 'password_reset_token';
const signinEntryStage = document.getElementById('signinEntryStage');
const signinMethodStage = document.getElementById('signinMethodStage');
const passwordResetStage = document.getElementById('forgotPasswordReset');
const passwordResetConfirmStage = document.getElementById('passwordResetConfirmStage');
const emailChangeStage = document.getElementById('emailChangeStage');
const emailChangeTitle = document.getElementById('emailChangeTitle');
const emailChangeStatus = document.getElementById('emailChangeStatus');
const emailChangeActionButton = document.getElementById('emailChangeActionButton');
const emailChangeBackLoginButton = document.getElementById('emailChangeBackLoginButton');
const resetPasswordLoading = document.getElementById('resetPasswordLoading');
const resetPasswordForm = document.getElementById('resetPasswordForm');
const resetPasswordInvalidState = document.getElementById('resetPasswordInvalidState');
const resetPasswordBackLoginButton = document.getElementById('resetPasswordBackLoginButton');
const currentPasswordGroup = document.getElementById('currentPasswordGroup');
const currentPasswordInput = document.getElementById('currentPassword');
const passwordActionTitle = document.getElementById('passwordActionTitle');
const passwordActionDescription = document.getElementById('passwordActionDescription');
const passwordActionSubmitText = document.getElementById('passwordActionSubmitText');
const signinEmailInput = document.getElementById('signinEmail');
const signinPasswordInput = document.getElementById('signinPassword');
const signinContinueButton = document.getElementById('signinContinueButton');
const signinButton = document.getElementById('signinButton');
const passkeySigninButton = document.getElementById('passkeySigninButton');
const signinMethodDivider = document.getElementById('signinMethodDivider');
const signinSelectedIdentifier = document.getElementById('signinSelectedIdentifier');
const changeSigninIdentifierButton = document.getElementById('changeSigninIdentifierButton');
const forgotPasswordEntryLink = document.getElementById('forgotPasswordEntryLink');
const forgotPasswordLink = document.getElementById('forgotPasswordLink');
const passwordResetIdentifierInput = document.getElementById('passwordResetIdentifier');
const passwordResetRequestBtn = document.getElementById('passwordResetRequestBtn');
const passwordResetRequestStatus = document.getElementById('passwordResetRequestStatus');
const cancelPasswordResetButton = document.getElementById('cancelPasswordResetButton');
const ldapLoginHint = document.getElementById('ldapLoginHint');
const loginContainer = document.querySelector('.container');
const authFormContainer = document.querySelector('.form-container');
const loginFormContainer = document.getElementById('loginFormContainer');
const loginSwitchTabs = document.getElementById('switchTabs');
const registerFormContainer = document.getElementById('registerFormContainer');
const disabledState = document.getElementById('disabledState');
const adminLoginButton = document.getElementById('adminLoginButton');

const signinFlowState = {
    stage: 'entry',
    resetReturnStage: 'entry',
    failedPasswordAttempts: 0,
    passwordResetEnabled: false,
    autoPasskeyPending: false,
    autoPasskeyAttemptedIdentifier: '',
    autoPasskeyAbortController: null,
    serverMethods: {
        password: true,
        passkey: false,
    },
};
let resetPasswordToken = '';
let passwordActionMode = 'reset';
let pendingEmailChangeAction = null;

// All password actions use the same accessible form. Only their copy, current-
// password requirement, API mode, and navigation behavior differ.
const PASSWORD_ACTION_COPY = {
    reset: {
        title: ['password_reset_title', 'Reset Password'],
        description: ['password_reset_intro', 'Enter your new password below.'],
        submit: ['password_reset_submit', 'Reset Password'],
        pageTitle: ['password_reset_page_title', 'Reset Password'],
    },
    change: {
        title: ['change_password_modal_title', 'Change Password'],
        description: ['password_change_required_intro', "To ensure your account's security, you must update your password."],
        submit: ['change_password_modal_submit', 'Change Password'],
        pageTitle: ['change_password_modal_title', 'Change Password'],
    },
    set: {
        title: ['us_set_password_title', 'Set Password'],
        description: ['us_set_password_desc', 'Set a password to enable email/password login in addition to social login'],
        submit: ['us_set_password_button', 'Set Password'],
        pageTitle: ['us_set_password_title', 'Set Password'],
    },
};

/** Apply translated copy while retaining declarative i18n updates. */
function setTranslatedText(element, [key, fallback]) {
    if (!element) return;
    element.dataset.i18n = key;
    element.textContent = loginTranslate(key, fallback);
}

/** Configure the shared form for reset, forced change, or initial setup. */
function configurePasswordAction(mode) {
    passwordActionMode = mode;
    const copy = PASSWORD_ACTION_COPY[mode];
    setTranslatedText(passwordActionTitle, copy.title);
    setTranslatedText(passwordActionDescription, copy.description);
    setTranslatedText(passwordActionSubmitText, copy.submit);

    const needsCurrentPassword = mode === 'change';
    if (currentPasswordGroup) currentPasswordGroup.hidden = !needsCurrentPassword;
    if (currentPasswordInput) currentPasswordInput.disabled = !needsCurrentPassword;
    if (resetPasswordBackLoginButton) resetPasswordBackLoginButton.hidden = mode !== 'reset';
    window.isPasswordResetFlow = mode === 'reset';
    window.isSettingPassword = mode === 'set';
    window.isRequiredPasswordChangeFlow = mode !== 'reset';
}

const PASSKEY_2FA_SESSION_KEY = 'passkey_login_pending';
const PASSKEY_2FA_IDENTIFIER_KEY = 'passkey_login_pending_identifier';

function supportsPasskeyAuth() {
    return typeof window.PublicKeyCredential === 'function' && !!navigator.credentials;
}

function normalizeSigninIdentifier(value) {
    return String(value || '').trim().toLowerCase();
}

function markPasskeyLogin2FAFlowActive(identifier = '') {
    try {
        sessionStorage.setItem(PASSKEY_2FA_SESSION_KEY, '1');
        const normalizedIdentifier = normalizeSigninIdentifier(identifier);
        if (normalizedIdentifier) {
            sessionStorage.setItem(PASSKEY_2FA_IDENTIFIER_KEY, normalizedIdentifier);
        }
    } catch (_) {}
}

function clearPasskeyLogin2FAFlow() {
    try {
        sessionStorage.removeItem(PASSKEY_2FA_SESSION_KEY);
        sessionStorage.removeItem(PASSKEY_2FA_IDENTIFIER_KEY);
    } catch (_) {}
}

function isPasskeyLogin2FAFlowActive() {
    try {
        return sessionStorage.getItem(PASSKEY_2FA_SESSION_KEY) === '1';
    } catch (_) {
        return false;
    }
}

function getPendingPasskeyLoginIdentifier() {
    try {
        return sessionStorage.getItem(PASSKEY_2FA_IDENTIFIER_KEY) || '';
    } catch (_) {
        return '';
    }
}

function cancelPendingAutoPasskeyAttempt() {
    if (signinFlowState.autoPasskeyAbortController) {
        signinFlowState.autoPasskeyAbortController.abort();
        signinFlowState.autoPasskeyAbortController = null;
    }
    signinFlowState.autoPasskeyPending = false;
}

async function canAutoPromptPasskeyOnThisDevice() {
    return supportsPasskeyAuth();
}

function passesAutoPromptPasskeyBaseGates(identifier) {
    if (!Boolean(signinFlowState.serverMethods.passkey)) {
        return false;
    }
    if (signinFlowState.stage !== 'methods') {
        return false;
    }
    const normalizedIdentifier = normalizeSigninIdentifier(identifier);
    if (!normalizedIdentifier) {
        return false;
    }
    if (signinFlowState.autoPasskeyPending) {
        return false;
    }
    if (signinFlowState.autoPasskeyAttemptedIdentifier === normalizedIdentifier) {
        return false;
    }
    return true;
}

async function shouldAutoPromptPasskey(identifier) {
    if (!passesAutoPromptPasskeyBaseGates(identifier)) {
        return false;
    }

    const normalizedIdentifier = normalizeSigninIdentifier(identifier);
    try {
        return Boolean(await window.WebAuthnHelpers?.hasPasskeyAutoPromptHint?.(normalizedIdentifier));
    } catch (_error) {
        return false;
    }
}

async function maybeAutoPromptPasskey(identifier) {
    if (!(await shouldAutoPromptPasskey(identifier))) {
        return false;
    }

    const canAutoPrompt = await canAutoPromptPasskeyOnThisDevice();
    if (!canAutoPrompt) {
        return false;
    }

    const normalizedIdentifier = normalizeSigninIdentifier(identifier);
    if (!(await shouldAutoPromptPasskey(normalizedIdentifier))) {
        return false;
    }

    if (normalizeSigninIdentifier(signinEmailInput?.value || '') !== normalizedIdentifier) {
        return false;
    }

    const abortController = new AbortController();
    signinFlowState.autoPasskeyPending = true;
    signinFlowState.autoPasskeyAbortController = abortController;
    signinFlowState.autoPasskeyAttemptedIdentifier = normalizedIdentifier;

    try {
        const handled = await signinWithPasskey({
            identifierOverride: normalizedIdentifier,
            signal: abortController.signal,
            suppressUserCancelError: true,
        });
        return Boolean(handled);
    } finally {
        if (signinFlowState.autoPasskeyAbortController === abortController) {
            signinFlowState.autoPasskeyAbortController = null;
        }
        signinFlowState.autoPasskeyPending = false;
    }
}

function setLoginInputErrorState(hasError) {
    [signinEmailInput, signinPasswordInput].forEach((input) => {
        if (!input) return;
        input.classList.toggle('input-error', Boolean(hasError));
    });
    if (loginContainer) {
        loginContainer.classList.toggle('error-state', Boolean(hasError));
    }
}

function setStageVisible(element, isVisible) {
    if (!element) return;
    element.hidden = !isVisible;
    element.classList.toggle('active', Boolean(isVisible));
}

function updateSigninContinueButtonState() {
    if (!signinContinueButton) return;
    signinContinueButton.disabled = (signinEmailInput?.value || '').trim().length === 0;
}

function updateSigninButtonState() {
    if (!signinButton) return;
    const emailOk = (signinEmailInput?.value || '').trim().length > 0;
    const passOk = (signinPasswordInput?.value || '').length >= 1;
    const passwordAllowed = Boolean(signinFlowState.serverMethods.password);
    signinButton.disabled = !(signinFlowState.stage === 'methods' && passwordAllowed && emailOk && passOk);
}

function updateForgotPasswordVisibility() {
    const hasIdentifier = (signinEmailInput?.value || '').trim().length > 0;
    const shouldShowEntryLink = typeof enablePasswordReset !== 'undefined'
        && Boolean(enablePasswordReset)
        && signinFlowState.stage === 'entry'
        && hasIdentifier;
    const shouldShowMethodLink = Boolean(signinFlowState.passwordResetEnabled)
        && signinFlowState.stage === 'methods'
        && hasIdentifier;

    if (forgotPasswordEntryLink) {
        forgotPasswordEntryLink.style.display = shouldShowEntryLink ? '' : 'none';
    }
    if (forgotPasswordLink) {
        forgotPasswordLink.style.display = shouldShowMethodLink ? '' : 'none';
    }
}

function isPasswordActionFlowActive() {
    return signinFlowState.stage === 'resetConfirm'
        || signinFlowState.stage === 'changePassword'
        || signinFlowState.stage === 'emailChange';
}

function syncPasswordActionFlowVisibility() {
    const isPasswordAction = isPasswordActionFlowActive();
    if (loginForm && isPasswordAction) {
        loginForm.hidden = true;
        loginForm.style.display = 'none';
    }
    if (loginSwitchTabs && isPasswordAction) {
        loginSwitchTabs.style.display = 'none';
    }
    if (authFormContainer && isPasswordAction) {
        authFormContainer.style.display = '';
    }
    if (loginFormContainer && isPasswordAction) {
        loginFormContainer.style.display = '';
        loginFormContainer.hidden = false;
        loginFormContainer.classList.add('active');
        loginFormContainer.setAttribute('aria-hidden', 'false');
    }
    if (registerFormContainer && isPasswordAction) {
        registerFormContainer.hidden = true;
        registerFormContainer.classList.remove('active');
        registerFormContainer.setAttribute('aria-hidden', 'true');
    }
    if (disabledState && isPasswordAction) {
        disabledState.style.display = 'none';
    }
    if (adminLoginButton && isPasswordAction) {
        adminLoginButton.style.display = 'none';
    }
    if (typeof window.setDocumentTitleWithAppName === 'function') {
        const [key, fallback] = signinFlowState.stage === 'emailChange'
            ? ['email_change_page_title', 'Email address update']
            : isPasswordAction
                ? PASSWORD_ACTION_COPY[passwordActionMode].pageTitle
                : ['login_title', 'Sign In'];
        window.setDocumentTitleWithAppName(loginTranslate(key, fallback));
    }
}

function syncSelectedIdentifier() {
    const identifier = (signinEmailInput?.value || '').trim();
    if (signinSelectedIdentifier) {
        signinSelectedIdentifier.textContent = identifier;
    }
}

function setSigninStage(nextStage) {
    signinFlowState.stage = nextStage;
    const isPasswordAction = isPasswordActionFlowActive();
    if (loginForm) {
        const showLoginForm = !isPasswordAction;
        loginForm.hidden = !showLoginForm;
        loginForm.style.display = showLoginForm ? '' : 'none';
    }
    setStageVisible(signinEntryStage, nextStage === 'entry');
    setStageVisible(signinMethodStage, nextStage === 'methods');
    setStageVisible(passwordResetStage, nextStage === 'reset');
    setStageVisible(passwordResetConfirmStage, isPasswordAction);
    setStageVisible(emailChangeStage, nextStage === 'emailChange');
    if (passwordResetConfirmStage) {
        passwordResetConfirmStage.hidden = nextStage === 'emailChange' || !isPasswordAction;
    }

    if (signinPasswordInput) {
        const passwordEnabled = nextStage === 'methods' && Boolean(signinFlowState.serverMethods.password);
        signinPasswordInput.disabled = !passwordEnabled;
        if (!passwordEnabled) {
            signinPasswordInput.value = '';
        }
    }

    if (passkeySigninButton) {
        const showPasskey = nextStage === 'methods' && Boolean(signinFlowState.serverMethods.passkey);
        passkeySigninButton.hidden = !showPasskey;
        passkeySigninButton.style.display = showPasskey ? '' : 'none';
    }

    if (signinMethodDivider) {
        const showDivider = nextStage === 'methods'
            && Boolean(signinFlowState.serverMethods.password)
            && Boolean(signinFlowState.serverMethods.passkey);
        signinMethodDivider.hidden = !showDivider;
    }

    if (passwordResetIdentifierInput) {
        passwordResetIdentifierInput.disabled = nextStage !== 'reset';
        if (nextStage === 'reset') {
            passwordResetIdentifierInput.value = (signinEmailInput?.value || '').trim();
        }
    }

    syncSelectedIdentifier();
    updateSigninContinueButtonState();
    updateSigninButtonState();
    updateForgotPasswordVisibility();
    syncPasswordActionFlowVisibility();
}

function clearPasswordResetStatus() {
    if (!passwordResetRequestStatus) return;
    passwordResetRequestStatus.style.display = 'none';
    passwordResetRequestStatus.textContent = '';
}

function resetSigninFlow(options = {}) {
    const { preserveIdentifier = true } = options;
    cancelPendingAutoPasskeyAttempt();
    signinFlowState.failedPasswordAttempts = 0;
    signinFlowState.passwordResetEnabled = Boolean(enablePasswordReset);
    signinFlowState.autoPasskeyAttemptedIdentifier = '';
    signinFlowState.serverMethods = {
        password: true,
        passkey: false,
    };
    if (!preserveIdentifier && signinEmailInput) {
        signinEmailInput.value = '';
    }
    if (signinPasswordInput) {
        signinPasswordInput.value = '';
    }
    if (passwordResetIdentifierInput) {
        passwordResetIdentifierInput.value = preserveIdentifier ? (signinEmailInput?.value || '').trim() : '';
    }
    clearPasswordResetStatus();
    clearPasskeyLogin2FAFlow();
    setLoginInputErrorState(false);
    setSigninStage('entry');
}

// Make resetSigninFlow globally accessible for tab switching
window.resetSigninFlow = resetSigninFlow;
window.updateSigninPasswordResetVisibility = updateForgotPasswordVisibility;
window.isPasswordActionFlowActive = isPasswordActionFlowActive;
window.syncPasswordActionFlowVisibility = syncPasswordActionFlowVisibility;

function handleLoginInputInteraction() {
    cancelPendingAutoPasskeyAttempt();
    const currentIdentifier = normalizeSigninIdentifier(signinEmailInput?.value || '');
    if (currentIdentifier !== signinFlowState.autoPasskeyAttemptedIdentifier) {
        signinFlowState.autoPasskeyAttemptedIdentifier = '';
    }
    setLoginInputErrorState(false);
    clearPasswordResetStatus();
    updateSigninContinueButtonState();
    updateSigninButtonState();
}

if (signinEmailInput && signinPasswordInput && signinButton && signinContinueButton) {
    updateSigninContinueButtonState();
    updateSigninButtonState();
    ['input', 'change'].forEach(evt => {
        signinEmailInput.addEventListener(evt, handleLoginInputInteraction);
        signinPasswordInput.addEventListener(evt, handleLoginInputInteraction);
    });
}

async function discoverSigninOptions(identifier) {
    const response = await fetch('/api/v1/auth/signin/options', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier }),
    });
    if (!response.ok) {
        if (typeof window.handleCrossSiteRequestBlock === 'function' && await window.handleCrossSiteRequestBlock(response)) {
            return {};
        }
        throw new Error(`Failed to discover sign-in options (${response.status})`);
    }
    return response.json();
}

function getSigninServerMethods(discoveryResult) {
    const serverMethods = discoveryResult?.server_methods || {};
    return {
        password: Boolean(serverMethods.password ?? true),
        passkey: Boolean(serverMethods.passkey) && supportsPasskeyAuth(),
    };
}

function focusFirstSigninMethodControl() {
    // After an identifier is accepted, place the cursor in the password field
    // so users can immediately continue typing their credentials. The change
    // identifier button remains available as an explicit secondary action.
    if (signinPasswordInput && !signinPasswordInput.disabled) {
        signinPasswordInput.focus();
        return true;
    }
    if (changeSigninIdentifierButton && !changeSigninIdentifierButton.hidden && !changeSigninIdentifierButton.disabled) {
        changeSigninIdentifierButton.focus();
        return true;
    }
    if (passkeySigninButton && !passkeySigninButton.hidden && !passkeySigninButton.disabled) {
        passkeySigninButton.focus();
        return true;
    }
    if (signinButton && !signinButton.disabled) {
        signinButton.focus();
        return true;
    }
    return false;
}

async function continueSigninWithIdentifier() {
    const identifier = (signinEmailInput?.value || '').trim();
    if (!identifier) {
        updateSigninContinueButtonState();
        signinEmailInput?.focus();
        return;
    }

    signinContinueButton.disabled = true;
    setLoginInputErrorState(false);
    clearPasswordResetStatus();

    try {
        const result = await discoverSigninOptions(identifier);
        signinFlowState.failedPasswordAttempts = 0;
        signinFlowState.passwordResetEnabled = Boolean(result?.password_reset_available ?? enablePasswordReset);
        signinFlowState.serverMethods = getSigninServerMethods(result);
    } catch (error) {
        signinFlowState.failedPasswordAttempts = 0;
        signinFlowState.passwordResetEnabled = Boolean(enablePasswordReset);
        signinFlowState.serverMethods = {
            password: true,
            passkey: false,
        };
    } finally {
        setSigninStage('methods');
        const autoPromptStarted = await maybeAutoPromptPasskey(identifier);
        if (!autoPromptStarted) {
            focusFirstSigninMethodControl();
        }
        updateSigninContinueButtonState();
    }
}

if (signinButton) {
    signinButton.addEventListener('click', function(event) {
        event.preventDefault();
        signin();
    });
}

if (passkeySigninButton) {
    passkeySigninButton.addEventListener('click', function (event) {
        event.preventDefault();
        signinWithPasskey();
    });
}

if (changeSigninIdentifierButton) {
    changeSigninIdentifierButton.addEventListener('click', function (event) {
        event.preventDefault();
        resetSigninFlow({ preserveIdentifier: true });
        signinEmailInput?.focus();
        signinEmailInput?.select?.();
    });
}

function openPasswordResetStage(event) {
    event.preventDefault();
    clearPasswordResetStatus();
    signinFlowState.resetReturnStage = signinFlowState.stage === 'methods' ? 'methods' : 'entry';
    setSigninStage('reset');
    if (passwordResetIdentifierInput && signinEmailInput) {
        passwordResetIdentifierInput.value = signinEmailInput.value.trim();
    }
    passwordResetIdentifierInput?.focus();
}

[forgotPasswordEntryLink, forgotPasswordLink].forEach((button) => {
    if (!button) return;
    button.addEventListener('click', openPasswordResetStage);
});

if (cancelPasswordResetButton) {
    cancelPasswordResetButton.addEventListener('click', function (event) {
        event.preventDefault();
        clearPasswordResetStatus();
        setSigninStage(signinFlowState.resetReturnStage || 'entry');
        if (signinFlowState.stage === 'methods') {
            focusFirstSigninMethodControl();
        } else {
            signinEmailInput?.focus();
        }
    });
}

if (passwordResetRequestBtn) {
    passwordResetRequestBtn.addEventListener('click', function (event) {
        event.preventDefault();
        submitPasswordResetRequest();
    });
}

if (passwordResetIdentifierInput) {
    passwordResetIdentifierInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            submitPasswordResetRequest();
        }
    });
}

function persistResetPasswordToken(token) {
    try {
        if (token) {
            sessionStorage.setItem(RESET_PASSWORD_TOKEN_STORAGE_KEY, token);
        } else {
            sessionStorage.removeItem(RESET_PASSWORD_TOKEN_STORAGE_KEY);
        }
    } catch (error) {
        console.warn('Unable to persist password reset token in session storage.', error);
    }
}

function readPersistedResetPasswordToken() {
    try {
        return (sessionStorage.getItem(RESET_PASSWORD_TOKEN_STORAGE_KEY) || '').trim();
    } catch (error) {
        console.warn('Unable to read password reset token from session storage.', error);
        return '';
    }
}

function clearPersistedResetPasswordToken() {
    persistResetPasswordToken('');
}

function getResetTokenFromLocation() {
    const hash = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
    const hashToken = new URLSearchParams(hash).get('token');
    if (hashToken) {
        return hashToken.trim();
    }

    const queryToken = new URLSearchParams(window.location.search).get('token');
    return (queryToken || '').trim();
}

function clearResetTokenFromLocation() {
    if (!window.location.search && !window.location.hash) {
        return;
    }
    window.history.replaceState(null, document.title, window.location.pathname);
}

async function validateResetToken(token) {
    try {
        const response = await fetch('/api/v1/auth/password-reset/validate', {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ token }),
        });
        if (!response.ok) {
            return false;
        }
        const payload = await response.json();
        return Boolean(payload?.valid);
    } catch (error) {
        return false;
    }
}

function setPasswordActionSubStage(stage) {
    const isLoading = stage === 'loading';
    const isReady = stage === 'ready';
    const isInvalid = stage === 'invalid';

    setStageVisible(resetPasswordLoading, isLoading);
    if (resetPasswordForm) {
        resetPasswordForm.hidden = !isReady;
        resetPasswordForm.classList.toggle('active', isReady);
    }
    setStageVisible(resetPasswordInvalidState, isInvalid);
    const submitBtn = document.getElementById('changePasswordBtn');
    if (submitBtn && !isReady) {
        submitBtn.disabled = true;
    }
}

async function showValidPasswordResetTokenState() {
    configurePasswordAction('reset');
    if (typeof renderPasswordRequirements === 'function') {
        await renderPasswordRequirements();
    }
    setPasswordActionSubStage('ready');
    if (typeof bindPasswordRequirementsEventListener === 'function') {
        bindPasswordRequirementsEventListener();
    }
    if (typeof resetPasswordRequirementsInputs === 'function') {
        resetPasswordRequirementsInputs();
    }
}

function showInvalidPasswordResetTokenState() {
    clearPersistedResetPasswordToken();
    resetPasswordToken = '';
    window.passwordResetToken = '';
    setPasswordActionSubStage('invalid');
}

async function initPasswordResetConfirmFlow() {
    const path = window.location.pathname.replace(/\/+$/, '') || '/';
    const isCompatibilityResetPath = path === '/reset_password' || path.endsWith('/reset_password');
    const tokenFromLocation = getResetTokenFromLocation();
    if (!tokenFromLocation && !isCompatibilityResetPath && !readPersistedResetPasswordToken()) {
        return;
    }

    if (tokenFromLocation) {
        persistResetPasswordToken(tokenFromLocation);
    }

    resetPasswordToken = tokenFromLocation || readPersistedResetPasswordToken();
    window.passwordResetToken = resetPasswordToken;
    window.clearPasswordResetToken = clearPersistedResetPasswordToken;
    if (tokenFromLocation || isCompatibilityResetPath) {
        clearResetTokenFromLocation();
    }
    setSigninStage('resetConfirm');
    configurePasswordAction('reset');
    setPasswordActionSubStage('loading');

    if (!resetPasswordToken || !(await validateResetToken(resetPasswordToken))) {
        showInvalidPasswordResetTokenState();
        return;
    }

    await showValidPasswordResetTokenState();
}

/** Render the protected forced-change flow inside the normal login shell. */
async function initRequiredPasswordChangeFlow() {
    const path = window.location.pathname.replace(/\/+$/, '') || '/';
    if (path !== '/change_password' && !path.endsWith('/change_password')) {
        return false;
    }

    // Do not trust the query string to choose a privileged password operation.
    // auth.js validates/canonicalizes the route and publishes the mode from the
    // authenticated refresh response before this protected form is enabled.
    const authBootstrap = window.__omlorixInitialAuthBootstrap;
    if (authBootstrap && !(await authBootstrap)) {
        return true;
    }
    const mode = window.requiredPasswordActionMode === 'set' ? 'set' : 'change';
    configurePasswordAction(mode);
    setSigninStage('changePassword');
    setPasswordActionSubStage('ready');
    if (typeof renderPasswordRequirements === 'function') {
        await renderPasswordRequirements();
    }
    if (typeof bindPasswordRequirementsEventListener === 'function') {
        bindPasswordRequirementsEventListener();
    }
    if (typeof resetPasswordRequirementsInputs === 'function') {
        resetPasswordRequirementsInputs();
    }
    return true;
}

/** Select the one password action represented by the current URL. */
async function initPasswordActionFlow() {
    if (await initEmailChangeFlow()) return;
    if (await initRequiredPasswordChangeFlow()) return;
    await initPasswordResetConfirmFlow();
}

function readPendingEmailChange() {
    const hash = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
    const params = new URLSearchParams(hash);
    const verifyToken = (params.get('email_change_token') || '').trim();
    const cancelToken = (params.get('email_change_cancel_token') || '').trim();
    if (verifyToken) return { kind: 'confirm', token: verifyToken };
    if (cancelToken) return { kind: 'cancel', token: cancelToken };
    return null;
}

function clearPendingEmailChange() {
    pendingEmailChangeAction = null;
}

async function initEmailChangeFlow() {
    const pending = readPendingEmailChange();
    if (!pending) return false;
    pendingEmailChangeAction = pending;
    if (window.location.hash) {
        window.history.replaceState(null, document.title, `${window.location.pathname}${window.location.search}`);
    }
    setSigninStage('emailChange');
    if (emailChangeTitle) {
        emailChangeTitle.textContent = loginTranslate('email_change_page_title', 'Email address update');
    }
    if (emailChangeStatus) {
        emailChangeStatus.textContent = loginTranslate(
            pending.kind === 'cancel' ? 'email_change_cancel_prompt' : 'email_change_confirm_prompt',
            pending.kind === 'cancel'
                ? 'Cancel this pending email change and sign out every device.'
                : 'Confirm that you want to use the new email address.',
        );
    }
    if (emailChangeActionButton) {
        emailChangeActionButton.hidden = false;
        emailChangeActionButton.disabled = false;
        emailChangeActionButton.textContent = loginTranslate(
            pending.kind === 'cancel' ? 'email_change_cancel_action' : 'email_change_confirm_action',
            pending.kind === 'cancel' ? 'Cancel email change' : 'Confirm email change',
        );
        emailChangeActionButton.focus();
    }
    return true;
}

async function processPendingEmailChange() {
    const pending = pendingEmailChangeAction;
    if (!pending || !emailChangeActionButton) return;
    emailChangeActionButton.disabled = true;
    if (emailChangeTitle) {
        emailChangeTitle.textContent = loginTranslate('email_change_processing_title', 'Updating email address');
    }
    if (emailChangeStatus) {
        emailChangeStatus.textContent = loginTranslate('email_change_processing', 'Checking this secure link…');
    }

    let failureMessage = loginTranslate(
        'email_change_unavailable',
        'This email-change link could not be processed right now. Please try again.',
    );
    let terminalOutcome = false;
    try {
        const response = await fetch(`/api/v1/auth/email-change/${pending.kind}`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: pending.token }),
        });
        if (!response.ok) {
            if (response.status === 409) {
                failureMessage = loginTranslate(
                    'email_change_in_use',
                    'That email address is already in use. Try again later or request a different address.',
                );
            } else if (response.status < 500) {
                terminalOutcome = true;
                failureMessage = loginTranslate('email_change_invalid', 'This email-change link is invalid or expired.');
            }
            throw new Error('email_change_failed');
        }
        terminalOutcome = true;
        clearPendingEmailChange();
        const cancelled = pending.kind === 'cancel';
        if (emailChangeTitle) {
            emailChangeTitle.textContent = loginTranslate(
                cancelled ? 'email_change_cancelled_title' : 'email_change_success_title',
                cancelled ? 'Email change cancelled' : 'Email address updated',
            );
        }
        if (emailChangeStatus) {
            emailChangeStatus.textContent = loginTranslate(
                cancelled ? 'email_change_cancelled' : 'email_change_success',
                cancelled
                    ? 'The pending email change was cancelled.'
                    : 'Your email address was updated. Sign in again with your new address.',
            );
        }
    } catch (_) {
        if (emailChangeTitle) {
            emailChangeTitle.textContent = loginTranslate('email_change_failed_title', 'Email address not updated');
        }
        if (emailChangeStatus) {
            emailChangeStatus.textContent = failureMessage;
        }
    } finally {
        if (terminalOutcome) {
            clearPendingEmailChange();
            emailChangeActionButton.hidden = true;
        } else {
            emailChangeActionButton.disabled = false;
            emailChangeActionButton.textContent = loginTranslate('email_change_retry', 'Try again');
        }
    }
    (terminalOutcome ? emailChangeBackLoginButton : emailChangeActionButton)?.focus();
}

emailChangeActionButton?.addEventListener('click', processPendingEmailChange);

if (resetPasswordBackLoginButton) {
    resetPasswordBackLoginButton.addEventListener('click', function (event) {
        event.preventDefault();
        clearPersistedResetPasswordToken();
        resetPasswordToken = '';
        window.passwordResetToken = '';
        window.isPasswordResetFlow = false;
        window.location.href = '/login';
    });
}

if (emailChangeBackLoginButton) {
    emailChangeBackLoginButton.addEventListener('click', () => {
        clearPendingEmailChange();
        window.location.href = '/login';
    });
}

if (loginForm) {
    loginForm.addEventListener('submit', function(event) {
        event.preventDefault();
        if (signinFlowState.stage === 'entry') {
            if (signinContinueButton && !signinContinueButton.disabled) {
                continueSigninWithIdentifier();
            }
            return;
        }
        if (signinFlowState.stage === 'methods' && signinButton && !signinButton.disabled) {
            signin();
        }
    });
}

async function initLDAPLoginHint() {
    try {
        const response = await fetch('/api/v1/auth/ldap/status');
        if (!response.ok) return;
        const data = await response.json();
        if (!data?.enabled) return;

        if (signinEmailInput && data.identifier_hint) {
            const identifierHint = data.identifier_hint || loginTranslate('ldap_default_identifier_hint', 'email or directory login');
            signinEmailInput.placeholder = identifierHint;
            signinEmailInput.setAttribute('aria-label', identifierHint);
        }

        if (ldapLoginHint) {
            const label = data.label || 'LDAP';
            const hint = data.identifier_hint || loginTranslate('ldap_default_identifier_hint', 'email or directory login');
            ldapLoginHint.textContent = loginFormat(
                'ldap_login_hint_with_label',
                '{label} sign-in is enabled. Use your {identifier} with your normal password.',
                { label, identifier: hint },
            );
            ldapLoginHint.style.display = 'block';
        }
    } catch (error) {
        // Keep the normal sign-in UX if LDAP status cannot be fetched.
    }
}

initLDAPLoginHint();
initPasswordActionFlow();
document.addEventListener('i18n:updated', syncPasswordActionFlowVisibility);

async function submitPasswordResetRequest() {
    if (!passwordResetIdentifierInput || !passwordResetRequestStatus || !passwordResetRequestBtn) return;

    const identifier = (passwordResetIdentifierInput.value || '').trim();
    if (!identifier) {
        passwordResetRequestStatus.style.display = '';
        passwordResetRequestStatus.textContent = loginTranslate('password_reset_identifier_required', 'Please enter your email.');
        passwordResetIdentifierInput.focus();
        return;
    }

    passwordResetRequestBtn.disabled = true;
    passwordResetRequestStatus.style.display = 'none';
    try {
        const response = await fetch('/api/v1/auth/password-reset/request', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: identifier })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok && typeof window.isCrossSiteRequestBlockDetail === 'function' && window.isCrossSiteRequestBlockDetail(data?.detail)) {
            window.showCrossSiteRequestBlocked(data.detail);
            return;
        }
        if (response.ok && data?.message) {
            passwordResetRequestStatus.textContent = data.message;
        } else if (response.status === 409) {
            passwordResetRequestStatus.textContent = loginTranslate('password_reset_disabled', 'Password reset is not enabled.');
        } else {
            passwordResetRequestStatus.textContent = loginTranslate('password_reset_generic_sent', 'If an account exists, a reset link has been sent.');
        }
        passwordResetRequestStatus.style.display = '';
    } catch (error) {
        passwordResetRequestStatus.textContent = loginTranslate('password_reset_generic_sent', 'If an account exists, a reset link has been sent.');
        passwordResetRequestStatus.style.display = '';
    } finally {
        passwordResetRequestBtn.disabled = false;
    }
}

// Form validation for login
async function signin(otpcodetype = "", otpDestinationOverride = null) {
    const email = signinEmailInput;
    const password = signinPasswordInput;

    if (!email || !password || !signinFlowState.serverMethods.password) {
        return;
    }

    if (!(email.value || '').trim()) {
        email.focus();
        return;
    }

    if ((password.value || '').length < 1 && !otpcodetype) {
        updateSigninButtonState();
        password.focus();
        return;
    }

    let otpCode = '';
    if (otpcodetype === "setup") {
    // OTP code
        const digitInputs = document.querySelectorAll('#tfaSetupOverlay .tfa-digit');
        digitInputs.forEach(input => {
            otpCode += input.value;
        });
    } else if (otpcodetype === "verify") {
        const digitInputs = document.querySelectorAll('#tfaVerifyOverlay .tfa-digit');
        digitInputs.forEach(input => {
            otpCode += input.value;
        });
    }
    const otpAction = otpcodetype === "setup" ? "setup" : (otpcodetype === "verify" ? "verify" : null);
    const replaceSlot = typeof window.getRequestedReplacementSlot === 'function'
        ? window.getRequestedReplacementSlot()
        : null;
    const returnUrl = typeof window.getAccountReturnUrl === 'function'
        ? window.getAccountReturnUrl()
        : '';
    const data = {
        email: email.value,
        password: password.value,
        ...(otpCode.length > 0 && { otp_code: otpCode }),
        ...(otpcodetype === "setup" && { otp_type: "setup" }),
        ...(otpAction && { otp_action: otpAction }),
        ...(otpDestinationOverride && { otp_destination: otpDestinationOverride }),
        ...(typeof adminLoginMode !== 'undefined' && adminLoginMode && { admin_only: true }),
        account_mode: typeof window.isAddAccountMode === 'function' && window.isAddAccountMode() ? 'add' : 'primary',
        ...(replaceSlot && { replace_slot: replaceSlot }),
        ...(returnUrl && { return_url: returnUrl }),
        ...(typeof window.getTermsOfServiceAcceptancePayload === 'function'
            ? window.getTermsOfServiceAcceptancePayload()
            : {}),
    }
    try {
        const response = await fetch(`/api/v1/auth/signin`, {
            method: 'POST',
            credentials: 'include',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        if (response.ok) {
            const result = await response.json();
            if (result.status === 'success') {
                if (window.loginMethodTracker) {
                    window.loginMethodTracker.saveLastUsedLoginMethod('email');
                }
                window.location.href = typeof window.resolvePostAuthRedirect === 'function'
                    ? window.resolvePostAuthRedirect(result)
                    : '/';
            } else if (result.status === "InvalidCredentials") {
                signinFlowState.failedPasswordAttempts += 1;
                setLoginInputErrorState(true);
                updateForgotPasswordVisibility();
                if (signinPasswordInput) {
                    signinPasswordInput.value = '';
                    updateSigninButtonState();
                    signinPasswordInput.focus();
                }
                notifyError(loginTranslate('signin_error_invalid_credentials', 'Invalid credentials. Please try again.'));
            } else if (result.status === "pending") {
                showPendingNotification()
            } else if (result.status === "otp_required_already_setup"){
                if (typeof window.set2FAContextFromResult === 'function') {
                    window.set2FAContextFromResult(result);
                }
                show2FAVerify();
            } else if (result.status === "otp_invalid") {
                notifyError(loginTranslate('signin_error_invalid_otp', 'Invalid two-factor authentication code. Please try again.'));
                const overlayId = otpcodetype === "setup" ? 'tfaSetupOverlay' : 'tfaVerifyOverlay';
                if (typeof set2FAOverlayActiveState === 'function') {
                    set2FAOverlayActiveState(overlayId, true);
                }
            } else if (result.status === "otp_locked") {
                notifyError(loginTranslate('signin_error_otp_locked', 'Too many invalid two-factor authentication attempts. Please try again later.'));
                const overlayId = otpcodetype === "setup" ? 'tfaSetupOverlay' : 'tfaVerifyOverlay';
                if (typeof set2FAOverlayActiveState === 'function') {
                    set2FAOverlayActiveState(overlayId, true);
                }

            } else if (result.status === "otp_setup") {
                if (typeof window.set2FAContextFromResult === 'function') {
                    window.set2FAContextFromResult(result);
                }
                const setupProvider = String(result.provider || window.omlorix2FAContext?.provider || 'totp').trim().toLowerCase();
                if (setupProvider === 'totp') {
                    // Use the QR code URI provided by backend when available; otherwise build one manually.
                    if (result.qrcode) {
                        renderQrCode(result.qrcode);
                    } else {
                        generateQrCode(result.secret, email.value);
                    }
                }
                show2FASetup();
            }
            else if (result.status === "ipban") {
                showWarning(
                    result.expires,
                    loginTranslate('ip_ban_title', 'Your IP has been banned'),
                    loginTranslate('ip_ban_message', 'Your IP has been temporarily banned due to <strong>various security reasons</strong>. This is a security measure to ensure the safety and integrity of our services.')
                );
            } else if (result.status === "lock") {
                showLoginAccountLockWarning(result);
            } else if (result.status === "inactive") {
                showInactiveAccountWarning();
            } else if (result.status === "signin_disabled_for_users") {
                notifyError(loginTranslate('signin_error_disabled_for_users', 'Sign-in is currently disabled for users. Only administrators can sign in.'));
            } else if (result.status === "admin_only") {
                notifyError(loginTranslate('signin_error_admin_only', 'This login is restricted to administrators only.'));
            } else if (result.status === "access_time_blocked") {
                showAccessBlockedOverlay(result);
            } else if (result.status === "deleted") {
                signinFlowState.failedPasswordAttempts += 1;
                setLoginInputErrorState(true);
                updateForgotPasswordVisibility();
                if (signinPasswordInput) {
                    signinPasswordInput.value = '';
                    updateSigninButtonState();
                    signinPasswordInput.focus();
                }
                notifyError(loginTranslate('signin_error_invalid_credentials', 'Invalid credentials. Please try again.'));
            } else if (result.status === "max_accounts_reached") {
                notifyError(loginFormat(
                    'signin_error_max_accounts',
                    'Maximum of {maxAccounts} stored accounts reached. Remove or replace an account first.',
                    { maxAccounts: result.max_accounts ?? MAX_STORED_ACCOUNTS },
                ));
            } else if (result.status === "termsAcceptanceRequired") {
                notifyTermsConsentRequired();
                signupTermsConsentCheckbox?.focus();
            } else if (result.status === "termsRevisionMismatch") {
                notifyError(translate('terms_revision_mismatch_error', 'The Terms of Service changed. Review the latest version and try again.'));
            } else if (result.status === "termsConfigurationRequired") {
                notifyError(translate('terms_configuration_required_error', 'Account registration is unavailable until the operator publishes custom Terms of Service on the login page.'));
            }
            else {
                notifyError(loginTranslate('signin_error_generic', 'Sign-in failed. Please try again later.'));
            }
        } else {
            if (typeof window.handleCrossSiteRequestBlock === 'function' && await window.handleCrossSiteRequestBlock(response)) {
                return;
            }
            notifyError(loginTranslate('signin_error_generic', 'Sign-in failed. Please try again later.'));
        }
    } catch (error) {
        notifyError(loginTranslate('signin_error_generic', 'Sign-in failed. Please try again later.'));
    }
};

async function signinWithPasskey(options = {}) {
    const {
        identifierOverride = '',
        signal,
        suppressUserCancelError = false,
    } = options;
    const emailInput = signinEmailInput;

    if (typeof window.PublicKeyCredential !== 'function' || !navigator.credentials) {
        notifyError(loginTranslate('passkey_not_supported', 'Passkeys are not supported in this browser.'));
        return false;
    }

    const identifier = String(identifierOverride || (emailInput?.value || '')).trim();
    if (!identifier) {
        notifyError(loginTranslate('passkey_identifier_required', 'Please enter your email first.'));
        emailInput?.focus();
        return false;
    }

    const replaceSlot = typeof window.getRequestedReplacementSlot === 'function'
        ? window.getRequestedReplacementSlot()
        : null;
    const returnUrl = typeof window.getAccountReturnUrl === 'function'
        ? window.getAccountReturnUrl()
        : '';

    try {
        const beginRes = await fetch(`/api/v1/auth/passkeys/authenticate/begin`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ identifier }),
            ...(signal && { signal }),
        });

        if (!beginRes.ok) {
            if (typeof window.handleCrossSiteRequestBlock === 'function' && await window.handleCrossSiteRequestBlock(beginRes)) {
                return false;
            }
            const errorData = await beginRes.json().catch(() => ({}));
            const detail = typeof errorData?.detail === 'string' ? errorData.detail : '';
            notifyError(detail || loginTranslate('passkey_begin_failed', 'Unable to start passkey sign-in.'));
            return false;
        }

        const beginData = await beginRes.json();
        const publicKeyOptions = window.WebAuthnHelpers?.preformatGetOptions({ publicKey: (beginData.publicKey || {}) });
        if (!publicKeyOptions || !publicKeyOptions.publicKey) {
            notifyError(loginTranslate('passkey_begin_failed', 'Unable to start passkey sign-in.'));
            return false;
        }

        const rpIdMismatchMessage = window.WebAuthnHelpers?.getRpIdMismatchMessage?.(publicKeyOptions, {
            actionLabel: loginTranslate('passkey_action_signin', 'sign-in'),
            expectedOrigin: beginData?.expected_origin,
        });
        if (rpIdMismatchMessage) {
            notifyError(rpIdMismatchMessage);
            return false;
        }

        let assertion;
        try {
            assertion = await navigator.credentials.get({
                ...publicKeyOptions,
                ...(signal && { signal }),
            });
        } catch (err) {
            const name = err?.name ? String(err.name) : 'Error';
            if (name === 'AbortError') {
                return false;
            }
            if (suppressUserCancelError && name === 'NotAllowedError') {
                return false;
            }
            if (name === 'NotAllowedError') {
                notifyError(loginTranslate(
                    'passkey_not_found_or_cancelled',
                    'No matching passkey was found on this device, or the request was cancelled.',
                ));
                return false;
            }
            const domainErrorMessage = window.WebAuthnHelpers?.getWebAuthnErrorMessage?.(err, publicKeyOptions, {
                actionLabel: loginTranslate('passkey_action_signin', 'sign-in'),
                expectedOrigin: beginData?.expected_origin,
            });
            if (domainErrorMessage) {
                notifyError(domainErrorMessage);
                return false;
            }
            const msg = err?.message ? String(err.message) : loginTranslate('passkey_finish_failed', 'Passkey sign-in failed. Please try again.');
            notifyError(`${name}: ${msg}`);
            return false;
        }
        if (!assertion) {
            if (suppressUserCancelError) {
                return false;
            }
            notifyError(loginTranslate('passkey_cancelled', 'Passkey sign-in was cancelled.'));
            return false;
        }

        const credentialJson = window.WebAuthnHelpers?.publicKeyCredentialToJSON(assertion);
        const finishRes = await fetch(`/api/v1/auth/passkeys/authenticate/finish`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                credential: credentialJson,
                expected_challenge: beginData.challenge,
                account_mode: typeof window.isAddAccountMode === 'function' && window.isAddAccountMode() ? 'add' : 'primary',
                ...(replaceSlot && { replace_slot: replaceSlot }),
                ...(returnUrl && { return_url: returnUrl }),
            }),
            ...(signal && { signal }),
        });

        if (!finishRes.ok) {
            if (typeof window.handleCrossSiteRequestBlock === 'function' && await window.handleCrossSiteRequestBlock(finishRes)) {
                return false;
            }
            const errorData = await finishRes.json().catch(() => ({}));
            const detail = errorData?.detail;
            const renderedDetail = typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : '');
            notifyError(renderedDetail || loginTranslate('passkey_finish_failed', 'Passkey sign-in failed. Please try again.'));
            return false;
        }

        const result = await finishRes.json();

        if (result.status === 'success') {
            clearPasskeyLogin2FAFlow();
            await window.WebAuthnHelpers?.markPasskeyAutoPromptHint?.(identifier);
            if (window.loginMethodTracker) {
                window.loginMethodTracker.saveLastUsedLoginMethod('passkey');
            }
            window.location.href = typeof window.resolvePostAuthRedirect === 'function'
                ? window.resolvePostAuthRedirect(result)
                : '/';
            return true;
        }

        if (result.status === 'otp_setup') {
            markPasskeyLogin2FAFlowActive(identifier);
            if (typeof window.set2FAContextFromResult === 'function') {
                window.set2FAContextFromResult(result);
            }
            const setupProvider = String(result.provider || window.omlorix2FAContext?.provider || 'totp').trim().toLowerCase();
            if (setupProvider === 'totp') {
                if (result.qrcode) {
                    renderQrCode(result.qrcode);
                } else {
                    generateQrCode(result.secret, identifier);
                }
            }
            show2FASetup();
            return true;
        }

        if (result.status === 'otp_required_already_setup') {
            markPasskeyLogin2FAFlowActive(identifier);
            if (typeof window.set2FAContextFromResult === 'function') {
                window.set2FAContextFromResult(result);
            }
            show2FAVerify();
            return true;
        }

        if (result.status === 'pending') {
            clearPasskeyLogin2FAFlow();
            showPendingNotification();
            return true;
        }

        if (result.status === 'signin_disabled_for_users') {
            clearPasskeyLogin2FAFlow();
            notifyError(loginTranslate('signin_error_disabled_for_users', 'Sign-in is currently disabled for users. Only administrators can sign in.'));
            return false;
        }

        if (result.status === 'lock') {
            clearPasskeyLogin2FAFlow();
            showLoginAccountLockWarning(result);
            return false;
        }

        if (result.status === 'inactive') {
            clearPasskeyLogin2FAFlow();
            showInactiveAccountWarning();
            return false;
        }

        if (result.status === 'access_time_blocked') {
            clearPasskeyLogin2FAFlow();
            showAccessBlockedOverlay(result);
            return true;
        }

        if (result.status === 'max_accounts_reached') {
            clearPasskeyLogin2FAFlow();
            notifyError(loginFormat(
                'signin_error_max_accounts',
                'Maximum of {maxAccounts} stored accounts reached. Remove or replace an account first.',
                { maxAccounts: result.max_accounts ?? MAX_STORED_ACCOUNTS },
            ));
            return false;
        }

        clearPasskeyLogin2FAFlow();
        notifyError(loginTranslate('passkey_finish_failed', 'Passkey sign-in failed. Please try again.'));
        return false;
    } catch (error) {
        const name = error?.name ? String(error.name) : '';
        if (name === 'AbortError') {
            return false;
        }
        if (suppressUserCancelError && name === 'NotAllowedError') {
            return false;
        }
        clearPasskeyLogin2FAFlow();
        const msg = error?.message ? String(error.message) : '';
        notifyError((name || msg) ? `${name}${name && msg ? ': ' : ''}${msg}` : loginTranslate('passkey_finish_failed', 'Passkey sign-in failed. Please try again.'));
        return false;
    }
}

async function completePasskeyLoginWith2FA(otpCode, otpType, otpDestination = null) {
    if (!isPasskeyLogin2FAFlowActive()) {
        if (typeof notifyError === 'function') {
            notifyError(loginTranslate('passkey_login_session_expired', 'Passkey login session expired. Please try again.'));
        }
        return null;
    }

    const replaceSlot = typeof window.getRequestedReplacementSlot === 'function'
        ? window.getRequestedReplacementSlot()
        : null;
    const returnUrl = typeof window.getAccountReturnUrl === 'function'
        ? window.getAccountReturnUrl()
        : '';

    try {
        const response = await fetch('/api/v1/auth/passkeys/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                otp_code: otpCode,
                otp_type: otpType,
                otp_action: otpType === 'setup' ? 'setup' : 'verify',
                ...(otpDestination ? { otp_destination: otpDestination } : {}),
                account_mode: typeof window.isAddAccountMode === 'function' && window.isAddAccountMode() ? 'add' : 'primary',
                ...(replaceSlot && { replace_slot: replaceSlot }),
                ...(returnUrl && { return_url: returnUrl }),
            }),
        });

        if (!response.ok) {
            if (typeof window.handleCrossSiteRequestBlock === 'function' && await window.handleCrossSiteRequestBlock(response)) {
                return null;
            }
            const result = await response.json().catch(() => ({}));
            clearPasskeyLogin2FAFlow();
            return {
                status: 'error',
                detail: result?.detail || loginFormat(
                    'passkey_completion_status_failed',
                    'Passkey completion failed with status {status}.',
                    { status: response.status },
                ),
                response_status: response.status,
                response_body: result,
            };
        }

        const result = await response.json().catch(() => ({}));

        if (result.status === 'success') {
            const hintIdentifier = getPendingPasskeyLoginIdentifier() || signinEmailInput?.value || '';
            clearPasskeyLogin2FAFlow();
            await window.WebAuthnHelpers?.markPasskeyAutoPromptHint?.(hintIdentifier);
            if (window.loginMethodTracker) {
                window.loginMethodTracker.saveLastUsedLoginMethod('passkey');
            }
            window.location.href = typeof window.resolvePostAuthRedirect === 'function'
                ? window.resolvePostAuthRedirect(result)
                : '/';
            return result;
        }

        clearPasskeyLogin2FAFlow();
        return result;
    } catch (error) {
        clearPasskeyLogin2FAFlow();
        return { status: 'error', detail: loginTranslate('passkey_complete_auth_failed', 'Failed to complete passkey authentication.') };
    }
}

window.signinWithPasskey = signinWithPasskey;
window.passkeyLogin = {
    completePasskeyLoginWith2FA,
    isInPasskeyLogin2FAFlow: isPasskeyLogin2FAFlowActive,
    clearPasskeyLogin2FAFlow,
};

if (!isPasswordActionFlowActive()) {
    setSigninStage('entry');
}

// ------------------------------
// Sign up 
// ------------------------------
const registerForm = document.getElementById('registerForm');
registerForm.addEventListener('submit', function(event) {
    event.preventDefault();
    // Returning the promise has no effect in the browser, but makes the complete
    // submit lifecycle observable to focused tests and other programmatic callers.
    return signup();
});

// Real-time enable/disable for Sign Up button
const signupButton = document.getElementById('signupButton');
const firstNameInput = document.getElementById('firstName');
const lastNameInput = document.getElementById('lastName');
const signupEmailInput = document.getElementById('signupEmail');
const signupEmailError = document.getElementById('signupEmailError');
const signupPasswordInput = document.getElementById('signupPassword');
const signupConfirmPasswordInput = document.getElementById('confirmPassword');
const signupTermsConsentCheckbox = document.getElementById('signupTermsConsentCheckbox');
const translate = (key, fallback) =>
    typeof window.getTranslation === 'function'
        ? window.getTranslation(key, fallback)
        : fallback;
// Mirrors the special-use domain suffixes rejected by Pydantic's EmailStr.
const SPECIAL_USE_EMAIL_DOMAIN_PATTERN = /(?:^|\.)(?:arpa|invalid|local|localhost|onion|test)$/i;

function hasSpecialUseEmailDomain(value) {
    const normalized = String(value || '').trim().toLowerCase();
    const atIndex = normalized.lastIndexOf('@');
    if (atIndex <= 0 || atIndex === normalized.length - 1) return false;
    const domain = normalized.slice(atIndex + 1);
    return SPECIAL_USE_EMAIL_DOMAIN_PATTERN.test(domain);
}

function isValidSignupEmail(value) {
    const normalized = String(value || '').trim();
    return normalized.length <= 100
        && isValidEmail(normalized)
        && !hasSpecialUseEmailDomain(normalized);
}

function getSignupEmailErrorMessage(value) {
    if (hasSpecialUseEmailDomain(value)) {
        return translate(
            'signup_error_reserved_email_domain',
            'Use an email address with a public domain. Reserved domains such as .test are not supported.',
        );
    }
    return translate('signup_error_invalid_email', 'Invalid email.');
}

function showSignupEmailError(message, { focus = true } = {}) {
    if (window.FormValidation?.showInputError) {
        window.FormValidation.showInputError(signupEmailInput, signupEmailError, message, {
            errorVisibleClass: null,
            focus,
            groupSelector: '.form-group',
            scroll: false,
        });
        return;
    }
    notifyError(message);
    if (focus) signupEmailInput?.focus();
}

function clearSignupEmailError() {
    window.FormValidation?.clearInputError?.(signupEmailInput, signupEmailError, {
        errorVisibleClass: null,
        groupSelector: '.form-group',
    });
}

function handleSignupValidationErrors(payload, emailValue) {
    if (!Array.isArray(payload?.detail)) return false;
    const emailError = payload.detail.some((item) => {
        const location = Array.isArray(item?.loc) ? item.loc : [];
        return location[location.length - 1] === 'email';
    });
    if (!emailError) return false;

    showSignupEmailError(getSignupEmailErrorMessage(emailValue));
    return true;
}

function getTermsPolicyRevision() {
    return Number(window.omlorixTermsOfServicePolicy?.revision || 0);
}

function isTermsConsentElementVisible(container) {
    return Boolean(
        container
        && !container.hidden
        && container.style.display !== 'none'
        && !container.closest('[hidden], [aria-hidden="true"]')
    );
}

function isSignupTermsConsentVisible() {
    return isTermsConsentElementVisible(document.getElementById('signupTermsConsent'));
}

function isSignupTermsConsentAccepted() {
    return Boolean(signupTermsConsentCheckbox?.checked);
}

window.getTermsOfServiceAcceptancePayload = function getTermsOfServiceAcceptancePayload() {
    const revision = getTermsPolicyRevision();
    if (!isSignupTermsConsentVisible() || revision <= 0) {
        return {};
    }
    return {
        accept_terms_of_service: isSignupTermsConsentAccepted(),
        terms_of_service_revision: revision,
    };
};

function notifyTermsConsentRequired() {
    notifyError(translate('terms_acceptance_required_error', 'Accept the current Terms of Service to create a new account.'));
}

function updateSignupButtonState() {
    const firstOk = (firstNameInput?.value || '').trim().length > 0;
    const lastOk = (lastNameInput?.value || '').trim().length > 0;
    const emailVal = (signupEmailInput?.value || '').trim();
    const emailValidFormat = isValidSignupEmail(emailVal);
    const passOk = (signupPasswordInput?.value || '').length > 0;
    const confirmOk = (signupConfirmPasswordInput?.value || '').length > 0;
    const termsOk = !isSignupTermsConsentVisible() || isSignupTermsConsentAccepted();
    const inputsReady = firstOk && lastOk && emailValidFormat && passOk && confirmOk && termsOk;
    if (signupButton) signupButton.disabled = !inputsReady;
}
window.updateSignupButtonState = updateSignupButtonState;
if (signupButton && firstNameInput && lastNameInput && signupEmailInput && signupPasswordInput && signupConfirmPasswordInput) {
    updateSignupButtonState();
    const inputs = [firstNameInput, lastNameInput, signupEmailInput, signupPasswordInput, signupConfirmPasswordInput];
    inputs.forEach(el => {
        el.addEventListener('input', () => {
            if (el === signupEmailInput) clearSignupEmailError();
            updateSignupButtonState();
        });
        el.addEventListener('change', updateSignupButtonState);
    });
    signupEmailInput.addEventListener('blur', () => {
        const value = signupEmailInput.value.trim();
        if (value && !isValidSignupEmail(value)) {
            showSignupEmailError(getSignupEmailErrorMessage(value), { focus: false });
        }
    });
    if (signupTermsConsentCheckbox) {
        ['input', 'change'].forEach(evt => signupTermsConsentCheckbox.addEventListener(evt, updateSignupButtonState));
    }
}
// Sign up form validation
async function signup() {
    const firstName = document.getElementById('firstName');
    const lastName = document.getElementById('lastName');
    const email = document.getElementById('signupEmail');
    const password = document.getElementById('signupPassword');
    const confirmPassword = document.getElementById('confirmPassword');

    if (password.value !== confirmPassword.value) {
        if (typeof notifyError === 'function') {
            notifyError(translate('signup_error_password_mismatch', 'Passwords do not match.'));
        }
        confirmPassword.focus();
        return;
    }

    const emailValue = email.value.trim();

    if (!isValidSignupEmail(emailValue)) {
        showSignupEmailError(getSignupEmailErrorMessage(emailValue));
        return;
    }
    clearSignupEmailError();
    if (isSignupTermsConsentVisible() && !isSignupTermsConsentAccepted()) {
        notifyTermsConsentRequired();
        signupTermsConsentCheckbox?.focus();
        return;
    }
    const replaceSlot = typeof window.getRequestedReplacementSlot === 'function'
        ? window.getRequestedReplacementSlot()
        : null;
    const returnUrl = typeof window.getAccountReturnUrl === 'function'
        ? window.getAccountReturnUrl()
        : '';
    const data = {
        email: emailValue,
        password: password.value,
        first_name: firstName.value,
        last_name: lastName.value,
        account_mode: typeof window.isAddAccountMode === 'function' && window.isAddAccountMode() ? 'add' : 'primary',
        ...(replaceSlot && { replace_slot: replaceSlot }),
        ...(returnUrl && { return_url: returnUrl }),
        ...(typeof window.getTermsOfServiceAcceptancePayload === 'function'
            ? window.getTermsOfServiceAcceptancePayload()
            : {}),
    };
    try {
        const response = await fetch(`/api/v1/auth/signup`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });
        if (response.ok) {
            const result = await response.json().catch(() => ({}));
            if (result.status === "success") {
                // Signup creates the account without creating an authenticated
                // browser session. Keep the user on the login page, carry their
                // normalized email into sign-in, and remove the submitted secrets.
                const signinIdentifier = emailValue.toLowerCase();
                document.getElementById('firstName').value = '';
                document.getElementById('lastName').value = '';
                document.getElementById('signupEmail').value = '';
                document.getElementById('signupPassword').value = '';
                document.getElementById('confirmPassword').value = '';
                if (signupTermsConsentCheckbox) {
                    signupTermsConsentCheckbox.checked = false;
                }
                updateSignupButtonState();

                if (signinEmailInput) {
                    signinEmailInput.value = signinIdentifier;
                }

                // Use the existing tab interaction so visual state, ARIA state,
                // sign-in stages, and button availability stay synchronized.
                document.getElementById('tabLogin')?.click();
                signinEmailInput?.focus();

                if (typeof window.notifySuccess === 'function') {
                    window.notifySuccess(translate(
                        'signup_success_notification',
                        'Registration successful. Sign in now!',
                    ));
                }
            } else if (result.status === "ipban") { // TODO
                showWarning(
                    result.expires,
                    translate('ip_ban_title', 'Your IP has been banned'),
                    translate('ip_ban_message', 'Your IP has been temporarily banned due to <strong>various security reasons</strong>. This is a security measure to ensure the safety and integrity of our services.')
                );
            } else if (result.status === "domainNotAllowed") {
                notifyError(translate('signup_error_domain_not_allowed', 'This email domain is not allowed.'));
            } else if (result.status === "passwordPolicyFailed") {
                notifyError(translate('signup_error_password_policy', 'Password requirements not met.'));
            } else if (result.status === "termsConfigurationRequired") {
                notifyError(translate('terms_configuration_required_error', 'Account registration is unavailable until the operator publishes custom Terms of Service on the login page.'));
            } else if (result.status === "termsAcceptanceRequired") {
                notifyTermsConsentRequired();
            } else if (result.status === "termsRevisionMismatch") {
                notifyError(translate('terms_revision_mismatch_error', 'The Terms of Service changed. Review the latest version and try again.'));
            } else if (result.status === "max_accounts_reached") {
                notifyError(loginFormat(
                    'signin_error_max_accounts',
                    'Maximum of {maxAccounts} stored accounts reached. Remove or replace an account first.',
                    { maxAccounts: result.max_accounts ?? MAX_STORED_ACCOUNTS },
                ));
            } else if (result.status === "error") {
                if (result.detail === "") {
                    notifyError(translate('signup_error_generic', 'Signup failed. Please try again later.'));
                } else {
                    notifyError(result.detail);
                }
            } else {
                notifyError(translate('signup_error_generic', 'Signup failed. Please try again later.'));
            }
        } else {
            if (typeof window.handleCrossSiteRequestBlock === 'function' && await window.handleCrossSiteRequestBlock(response)) {
                return;
            }
            const result = await response.json().catch(() => ({}));
            if (response.status === 422 && handleSignupValidationErrors(result, emailValue)) {
                return;
            }
            if (typeof result?.detail === 'string' && result.detail) {
                notifyError(result.detail);
                return;
            }
            notifyError(translate('signup_error_generic', 'Signup failed. Please try again later.'));
        }
    } catch (error) {
        notifyError(translate('signup_error_generic', 'Signup failed. Please try again later.'));
    }
};




// Animation for elements
function animateItems(items) {
    items.forEach((item, index) => {
        if (item.tagName) {  // Only animate HTML elements, not Text Nodes
            item.style.opacity = '0';
            item.style.transform = 'translateY(10px)';
            setTimeout(() => {
                item.style.opacity = '1';
                item.style.transform = 'translateY(0)';
                // Remove the transform after the transition finishes so it doesn't create a new stacking context
                setTimeout(() => {
                    item.style.transform = '';
                }, 350);
            }, 50 * index);
        }
    });
}
