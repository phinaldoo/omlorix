// Temporary storage for the 2FA secret during setup
let tfaTempSecret = null;
let tfaProvider = '';
let tfaDeliveryHint = '';
let tfaQrHintTimeout = null;
let last2FAFocusTrigger = null;
let last2FABodyOverflow = null;
let tfaSetupRequestGeneration = 0;
let tfaSetupVerificationGeneration = null;
let currentTwoFactorSettingsState = {
    featureEnabled: true,
    enrolled: false,
    forced: false,
};

/**
 * Return whether asynchronous setup work still belongs to the visible overlay.
 * Dismissing the overlay increments the generation so late responses become no-ops.
 */
function isTfaSetupGenerationActive(generation, overlay) {
    return generation === tfaSetupRequestGeneration
        && overlay?.classList.contains('active');
}

function normalizeTfaSecret(secret) {
    return String(secret || '').replace(/\s+/g, '').trim();
}

async function fetchPendingTfaSetupMaterial() {
    const response = await window.authedFetch('/api/v1/auth/twofa/setup-material', { method: 'GET' });
    if (!response.ok) {
        throw new Error(translateTwoFa(
            'tfa_setup_material_failed_status',
            'Failed to fetch 2FA setup material ({status})',
            { status: response.status }
        ));
    }
    return response.json();
}

function normalizeTfaProvider(provider) {
    const normalized = String(provider || '').trim().toLowerCase();
    if (normalized === 'totp' || normalized === 'email') {
        return normalized;
    }
    return '';
}

function isTotpProvider(provider) {
    return normalizeTfaProvider(provider) === 'totp';
}

function get2FASetupDeliveryText(provider, deliveryHint) {
    const hint = String(deliveryHint || '').trim();
    if (provider === 'email') {
        return hint
            ? translateTwoFa('tfa_email_code_sent_to_hint', 'Enter the 6-digit code sent to {hint}.', { hint })
            : translateTwoFa('tfa_email_code_from_email', 'Enter the 6-digit code from the verification email.');
    }
    return '';
}

function update2FASetupDescription(showTotpSetup, hasDeliveryHint) {
    const setupOverlay = document.getElementById('tfaSetupOverlay');
    if (!setupOverlay) {
        return;
    }
    const setupDialog = setupOverlay.matches('[role="dialog"]')
        ? setupOverlay
        : setupOverlay.querySelector('[role="dialog"]');
    if (!setupDialog) return;

    if (showTotpSetup) {
        setupDialog.setAttribute('aria-describedby', 'tfaSetupInstructionsTitle tfaStep1 tfaStep2 tfaStep3');
        return;
    }

    if (hasDeliveryHint) {
        setupDialog.setAttribute('aria-describedby', 'tfaSetupDeliveryHint');
        return;
    }

    setupDialog.removeAttribute('aria-describedby');
}

function update2FASetupLayout(provider, deliveryHint = '') {
    const setupVisuals = document.getElementById('tfaSetupVisuals');
    const setupDeliveryHint = document.getElementById('tfaSetupDeliveryHint');
    const showTotpSetup = isTotpProvider(provider);
    let hasDeliveryHint = false;

    if (setupVisuals) {
        setupVisuals.hidden = !showTotpSetup;
    }

    if (setupDeliveryHint) {
        if (showTotpSetup) {
            setupDeliveryHint.hidden = true;
            setupDeliveryHint.textContent = '';
        } else {
            setupDeliveryHint.textContent = get2FASetupDeliveryText(provider, deliveryHint);
            hasDeliveryHint = Boolean(setupDeliveryHint.textContent);
            setupDeliveryHint.hidden = !hasDeliveryHint;
        }
    }

    update2FASetupDescription(showTotpSetup, hasDeliveryHint);
}

function clear2FASetupQrState() {
    const qrContainer = document.getElementById('tfaQrCode');
    if (!qrContainer) {
        return;
    }
    qrContainer.innerHTML = '';
    delete qrContainer.dataset.tfaSecret;
    delete qrContainer.dataset.tfaQrPayload;
}

function initTwoFASetupProvider(provider, deliveryHint = '') {
    tfaProvider = normalizeTfaProvider(provider);
    tfaDeliveryHint = String(deliveryHint || '').trim();
    if (!isTotpProvider(tfaProvider)) {
        tfaTempSecret = null;
        clear2FASetupQrState();
        refresh2FASetupCopyState({ secret: '', otpauthUri: '' });
    }
    update2FASetupLayout(tfaProvider, tfaDeliveryHint);
}

function getTfaQrHintDefaultText(secret, otpauthUri) {
    if (!secret && !otpauthUri) {
        return '';
    }
    return secret
        ? translateTwoFa('tfa_qr_copy_secret_hint', 'Click QR code to copy setup code')
        : translateTwoFa('tfa_qr_copy_link_hint', 'Click QR code to copy setup link');
}

function flashTfaQrHint(message, state = 'default', timeoutMs = 1800) {
    const qrHint = document.getElementById('tfaQrCopyHint');
    if (!qrHint) {
        return;
    }

    if (tfaQrHintTimeout) {
        clearTimeout(tfaQrHintTimeout);
        tfaQrHintTimeout = null;
    }

    const { secret, otpauthUri } = readCurrentTfaSetupData();
    const fallback = getTfaQrHintDefaultText(secret, otpauthUri);

    qrHint.textContent = message || fallback;
    qrHint.classList.toggle('active', Boolean(message || fallback));
    qrHint.classList.toggle('success', state === 'success');
    qrHint.classList.toggle('error', state === 'error');

    if (!message) {
        return;
    }

    tfaQrHintTimeout = setTimeout(() => {
        qrHint.textContent = fallback;
        qrHint.classList.toggle('active', Boolean(fallback));
        qrHint.classList.remove('success', 'error');
    }, timeoutMs);
}

async function copyTextToClipboard(value) {
    const text = String(value || '').trim();
    if (!text) {
        return false;
    }
    if (navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (_) {}
    }
    try {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', 'readonly');
        textarea.style.position = 'fixed';
        textarea.style.top = '-9999px';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        return copied;
    } catch (_) {
        return false;
    }
}

function readCurrentTfaSetupData(payload = {}) {
    const qrContainer = document.getElementById('tfaQrCode');
    const payloadSecret = normalizeTfaSecret(payload.secret);
    const payloadUri = String(payload.otpauthUri || '').trim();
    const qrSecret = normalizeTfaSecret(qrContainer?.dataset?.tfaSecret);
    const qrUri = String(qrContainer?.dataset?.tfaQrPayload || '').trim();
    const secret = payloadSecret || qrSecret || normalizeTfaSecret(tfaTempSecret);
    const otpauthUri = payloadUri || qrUri;

    if (secret) {
        tfaTempSecret = secret;
    }

    return { secret, otpauthUri };
}

async function copyCurrentTfaSetupValue(prefer = 'secret') {
    const { secret, otpauthUri } = readCurrentTfaSetupData();
    const shouldPreferUri = prefer === 'uri';
    const value = shouldPreferUri
        ? (otpauthUri || secret)
        : (secret || otpauthUri);

    if (!value) {
        flashTfaQrHint(translateTwoFa('tfa_copy_unavailable', 'Nothing to copy yet. Try again in a second.'), 'error');
        return;
    }

    const copied = await copyTextToClipboard(value);
    if (!copied) {
        flashTfaQrHint(translateTwoFa('tfa_copy_failed', 'Copy failed. Please try again.'), 'error');
        return;
    }

    const copiedSecret = value === secret;
    flashTfaQrHint(
        copiedSecret
            ? translateTwoFa('tfa_secret_copy_success', 'Secret code copied to clipboard.')
            : translateTwoFa('tfa_link_copy_success', 'Setup link copied to clipboard.'),
        'success',
        1200
    );
}

function refresh2FASetupCopyState(payload = {}) {
    const qrContainer = document.getElementById('tfaQrCode');
    const qrHint = document.getElementById('tfaQrCopyHint');
    const { secret, otpauthUri } = readCurrentTfaSetupData(payload);

    const canCopyFromQr = Boolean(secret || otpauthUri);
    if (qrContainer) {
        qrContainer.classList.toggle('is-copyable', canCopyFromQr);
        if (canCopyFromQr) {
            qrContainer.setAttribute('role', 'button');
            qrContainer.setAttribute('tabindex', '0');
            qrContainer.setAttribute(
                'aria-label',
                secret
                    ? translateTwoFa('tfa_qr_copy_secret_aria', 'Copy 2FA setup secret code')
                    : translateTwoFa('tfa_qr_copy_link_aria', 'Copy 2FA setup link')
            );
        } else {
            qrContainer.removeAttribute('role');
            qrContainer.removeAttribute('tabindex');
            qrContainer.removeAttribute('aria-label');
        }
    }

    if (qrHint) {
        qrHint.textContent = getTfaQrHintDefaultText(secret, otpauthUri);
        qrHint.classList.toggle('active', canCopyFromQr);
        if (!canCopyFromQr) {
            qrHint.classList.remove('success', 'error');
        }
    }
}

window.refresh2FASetupCopyState = refresh2FASetupCopyState;
window.initTwoFASetupProvider = initTwoFASetupProvider;

function setTwoFactorActionVisible(button, visible) {
    if (!button) {
        return;
    }
    button.hidden = !visible;
    button.style.display = visible ? '' : 'none';
    button.disabled = false;
}

function setTwoFactorSettingsState(options = {}) {
    const setup2FABtn = document.getElementById('setup2FABtn');
    const reset2FABtn = document.getElementById('reset2FABtn');
    const deactivate2FAButton = document.getElementById('deactivate2FAButton');

    if (!setup2FABtn && !reset2FABtn && !deactivate2FAButton) {
        return currentTwoFactorSettingsState;
    }

    currentTwoFactorSettingsState = {
        featureEnabled: options.featureEnabled ?? currentTwoFactorSettingsState.featureEnabled,
        enrolled: options.enrolled ?? currentTwoFactorSettingsState.enrolled,
        forced: options.forced ?? currentTwoFactorSettingsState.forced,
    };

    const featureEnabled = currentTwoFactorSettingsState.featureEnabled !== false;
    const enrolled = Boolean(currentTwoFactorSettingsState.enrolled);
    const forced = Boolean(currentTwoFactorSettingsState.forced);

    setTwoFactorActionVisible(setup2FABtn, featureEnabled && !enrolled);
    setTwoFactorActionVisible(reset2FABtn, featureEnabled && enrolled);
    setTwoFactorActionVisible(deactivate2FAButton, featureEnabled && enrolled && !forced);

    return currentTwoFactorSettingsState;
}

window.setTwoFactorSettingsState = setTwoFactorSettingsState;

function translateTwoFa(key, fallback, params) {
    let text = fallback;

    if (typeof window.resolveSetupTranslation === 'function') {
        text = window.resolveSetupTranslation(key, fallback, params);
    } else if (typeof window.getTranslation === 'function') {
        text = window.getTranslation(key, fallback);
    }

    if (typeof text !== 'string' || !params || typeof params !== 'object') {
        return text;
    }

    return text.replace(/\{(\w+)\}/g, (match, token) => (
        Object.prototype.hasOwnProperty.call(params, token) ? params[token] : match
    ));
}

function updateTfaInputDescription(input, descriptionId, enabled) {
    const ids = new Set(String(input.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    if (enabled) {
        ids.add(descriptionId);
    } else {
        ids.delete(descriptionId);
    }
    if (ids.size > 0) {
        input.setAttribute('aria-describedby', Array.from(ids).join(' '));
    } else {
        input.removeAttribute('aria-describedby');
    }
}

function clear2FASetupError() {
    const error = document.getElementById('tfaSetupError');
    const inputs = document.querySelectorAll('#tfaSetupOverlay .tfa-digit');
    if (error) {
        error.textContent = '';
        error.hidden = true;
    }
    inputs.forEach((input) => {
        input.removeAttribute('aria-invalid');
        updateTfaInputDescription(input, 'tfaSetupError', false);
    });
}

function show2FASetupError(message, { clearCode = false, focusInput = false } = {}) {
    const error = document.getElementById('tfaSetupError');
    const inputs = Array.from(document.querySelectorAll('#tfaSetupOverlay .tfa-digit'));
    const text = String(message || '').trim();

    inputs.forEach((input) => {
        if (clearCode) {
            input.value = '';
        }
        input.setAttribute('aria-invalid', 'true');
        updateTfaInputDescription(input, 'tfaSetupError', true);
    });

    if (error) {
        error.textContent = text;
        error.hidden = false;
    }

    if (focusInput) {
        const target = inputs.find((input) => !input.value) || inputs[0];
        target?.focus({ preventScroll: true });
        target?.select?.();
    } else {
        error?.focus({ preventScroll: true });
    }
}

function set2FASetupSubmitting(submitting) {
    const overlay = document.getElementById('tfaSetupOverlay');
    const button = document.getElementById('tfaSetupPrimaryButton');
    const label = document.getElementById('tfaSetupPrimaryText');
    if (overlay) {
        if (submitting) overlay.setAttribute('aria-busy', 'true');
        else overlay.removeAttribute('aria-busy');
    }
    if (button) {
        button.disabled = submitting;
    }
    if (label) {
        label.textContent = submitting
            ? translateTwoFa('tfa_setup_verifying', 'Verifying…')
            : translateTwoFa('tfa_setup_verify_enable', 'Verify and Enable');
    }
}

document.getElementById('setup2FABtn')?.addEventListener('click', show2FASetup);
document.getElementById('reset2FABtn')?.addEventListener('click', show2FASetup);
document.getElementById('skipTwoFABtn')?.addEventListener('click', () => {
    if (typeof nextStep === 'function') {
        nextStep();
    }
});
async function show2FASetup() {
    if (typeof window.ensureSecurityStepUp !== 'function') {
        notifyError?.(translateTwoFa(
            'step_up_methods_load_failed',
            'Verification methods could not be loaded. Close this dialog and try again.',
        ));
        return;
    }
    if (!await window.ensureSecurityStepUp()) {
        return;
    }

    const overlayId = 'tfaSetupOverlay';
    const overlay = document.getElementById(overlayId);
    const requestGeneration = ++tfaSetupRequestGeneration;
    const isCurrentRequest = () => isTfaSetupGenerationActive(requestGeneration, overlay);
    try {
        // Open overlay immediately with a loading state
        if (overlay) {
            last2FAFocusTrigger = document.activeElement instanceof HTMLElement
                ? document.activeElement
                : document.getElementById('setup2FABtn');
            if (last2FABodyOverflow === null) {
                last2FABodyOverflow = document.body.style.overflow;
            }
            document.body.style.overflow = 'hidden';
            overlay.classList.add('active');
            overlay.setAttribute('aria-hidden', 'false');
        }
        tfaTempSecret = '';
        tfaDeliveryHint = '';
        clear2FASetupError();
        set2FASetupSubmitting(false);
        clear2FASetupQrState();
        refresh2FASetupCopyState({ secret: '', otpauthUri: '' });
        update2FASetupLayout(tfaProvider, tfaDeliveryHint);
        // Focus OTP input when visible
        focusFirstTfaInput(overlayId);
        initializeTfaOverlayInteractions();

        // Make the request to the backend to get Secret and QR Code URI
        // Prefer POST; some backends might expect GET. We'll fallback.
        let res = await window.authedFetch(`/api/v1/auth/twofa/setup`, {
            method: 'POST',
            body: JSON.stringify({})
        });
        if (!isCurrentRequest()) return;
        if (!res.ok && res.status !== 401 && res.status !== 403) {
            // Fallback to GET if POST is not supported (e.g., 405)
            try {
                res = await window.authedFetch(`/api/v1/auth/twofa/setup`, {
                    method: 'GET'
                });
                if (!isCurrentRequest()) return;
            } catch (_) {}
        }

        if (res.ok) {
            let data = await res.json();
            if (!isCurrentRequest()) return;
            initTwoFASetupProvider(data.provider || tfaProvider || 'totp', data.delivery_hint || '');

            let setupMaterial = data;
            if (data.setup_material_available && isTotpProvider(data.provider || tfaProvider)) {
                setupMaterial = await fetchPendingTfaSetupMaterial();
                if (!isCurrentRequest()) return;
            }
            const secret = setupMaterial.secret || '';
            tfaTempSecret = secret;

            const qrCodeUri = setupMaterial.qrcode;
            refresh2FASetupCopyState({ secret, otpauthUri: qrCodeUri || '' });
            if (isTotpProvider(tfaProvider) && qrCodeUri) {
                renderQrCode(qrCodeUri);
            } else if (tfaProvider !== 'totp') {
                const hint = tfaDeliveryHint ? ` (${tfaDeliveryHint})` : '';
                notifySuccess(translateTwoFa('tfa_verification_code_sent', 'Verification code sent{hint}.', { hint }));
            } else if (!secret) {
                if (typeof notifyError === 'function') notifyError(translateTwoFa('tfa_setup_details_failed', 'Failed to retrieve 2FA setup details.'));
            }
        } else if (res.status === 401 || res.status === 403) {
            redirectToLogin();
        } else {
            // Unexpected failure: close overlay and show error
            if (typeof notifyError === 'function') {
                try {
                    const err = await res.json();
                    if (!isCurrentRequest()) return;
                    hide2FASetup();
                    notifyError(err?.detail || err?.message || translateTwoFa('tfa_setup_start_failed', 'Failed to start 2FA setup. Please try again.'));
                } catch {
                    if (!isCurrentRequest()) return;
                    hide2FASetup();
                    notifyError(translateTwoFa('tfa_setup_start_failed', 'Failed to start 2FA setup. Please try again.'));
                }
            } else {
                if (!isCurrentRequest()) return;
                hide2FASetup();
                console.error('2FA setup failed', res.status);
            }
        }
    } catch (e) {
        if (!isCurrentRequest()) return;
        hide2FASetup();
        if (typeof notifyError === 'function') {
            notifyError(translateTwoFa('tfa_setup_start_network', 'Failed to start 2FA setup. Please check your connection and try again.'));
        } else {
            console.error('2FA setup error', e);
        }
    }
}

document.getElementById('tfaSetupPrimaryButton')?.addEventListener('click', verify2FASetup);
async function verify2FASetup() {
    const overlay = document.getElementById('tfaSetupOverlay');
    const requestGeneration = tfaSetupRequestGeneration;
    const isCurrentRequest = () => isTfaSetupGenerationActive(requestGeneration, overlay);
    if (tfaSetupVerificationGeneration === requestGeneration) {
        return;
    }
    const digitInputs = document.querySelectorAll('#tfaSetupOverlay .tfa-digit');
    let otpCode = '';
    digitInputs.forEach(input => {
        otpCode += input.value;
    });
    if (otpCode.length !== 6) {
        show2FASetupError(
            translateTwoFa('tfa_code_incomplete', 'Please fill in all 6 digits.'),
            { focusInput: true }
        );
        return;
    }

    clear2FASetupError();
    tfaSetupVerificationGeneration = requestGeneration;
    set2FASetupSubmitting(true);
    try {
        const res = await window.authedFetch(`/api/v1/auth/twofa/setup`, {
            method: "POST",
            body: JSON.stringify({
                ...(tfaProvider === 'totp' ? { temp_secret: tfaTempSecret } : {}),
                otp_code: otpCode,
                otp_action: 'setup',
            })
        });
        if (!isCurrentRequest()) return;

        if (res.status === 401 || res.status === 403) {
            redirectToLogin();
            return;
        }
        if (!res.ok) {
            show2FASetupError(translateTwoFa(
                'tfa_setup_verify_failed',
                'Unable to verify the code. Please try again.'
            ));
            return;
        }

        const data = await res.json();
        if (!isCurrentRequest()) return;
        if (data.status === "success") {
            if (typeof window.refreshAuthSession === 'function') {
                await window.refreshAuthSession();
                if (!isCurrentRequest()) return;
            }
            hide2FASetup();
            // reset the inputs
            const digitInputs = document.querySelectorAll('#tfaSetupOverlay .tfa-digit');
            digitInputs.forEach(input => {
                input.value = '';
            });
            // Keep the security settings controls synchronized after enrollment.
            if (document.getElementById('twoFactorSettingsSection')) {
                setTwoFactorSettingsState({ enrolled: true });
            }
        } else if (data.status === "otp_setup") {
            initTwoFASetupProvider(data.provider || tfaProvider || 'totp', data.delivery_hint || '');
            let setupMaterial = data;
            if (data.setup_material_available && isTotpProvider(data.provider || tfaProvider)) {
                setupMaterial = await fetchPendingTfaSetupMaterial();
                if (!isCurrentRequest()) return;
            }
            if (setupMaterial.secret) {
                tfaTempSecret = setupMaterial.secret;
            }
            refresh2FASetupCopyState({ secret: setupMaterial.secret || tfaTempSecret || '', otpauthUri: setupMaterial.qrcode || '' });
            if (isTotpProvider(tfaProvider) && setupMaterial.qrcode) renderQrCode(setupMaterial.qrcode);
            else if (!isTotpProvider(tfaProvider) && tfaDeliveryHint) notifySuccess(translateTwoFa('tfa_verification_code_sent', 'Verification code sent{hint}.', { hint: ` (${tfaDeliveryHint})` }));
        } else if (data.status === "otp_invalid") {
            show2FASetupError(
                translateTwoFa('tfa_setup_invalid_code', 'That code is incorrect. Enter a new code and try again.'),
                { clearCode: true, focusInput: true }
            );
        } else if (data.status === "otp_locked") {
            show2FASetupError(translateTwoFa(
                'tfa_setup_too_many_attempts',
                'Too many incorrect attempts. Please try again later.'
            ));
        } else if (data.status === "error") {
            show2FASetupError(translateTwoFa(
                'tfa_setup_verify_failed',
                'Unable to verify the code. Please try again.'
            ));
        } else {
            show2FASetupError(translateTwoFa(
                'tfa_setup_verify_failed',
                'Unable to verify the code. Please try again.'
            ));
        }
    } catch (error) {
        if (!isCurrentRequest()) return;
        console.error('2FA setup verification failed', error);
        show2FASetupError(translateTwoFa(
            'tfa_setup_verify_failed',
            'Unable to verify the code. Please try again.'
        ));
    } finally {
        if (tfaSetupVerificationGeneration === requestGeneration) {
            tfaSetupVerificationGeneration = null;
            if (isCurrentRequest()) {
                set2FASetupSubmitting(false);
            }
        }
    }
}

const tfaOverlay = document.getElementById('tfaSetupOverlay');
tfaOverlay?.querySelector('.tfa-close-button')?.addEventListener('click', hide2FASetup);
document.getElementById('tfaSetupCancelButton')?.addEventListener('click', hide2FASetup);

// Use the shared backdrop behavior when it is available on the main chat page.
// The fallback preserves dismissal if the helper is unavailable during startup.
if (tfaOverlay) {
    if (typeof window.DeleteWarningModal?.bindBackdropDismissal === 'function') {
        window.DeleteWarningModal.bindBackdropDismissal(tfaOverlay, hide2FASetup);
    } else {
        tfaOverlay.addEventListener('click', (event) => {
            if (event.target === tfaOverlay) hide2FASetup();
        });
    }
}

async function hide2FASetup() {
    // Invalidate any setup response that may still be resolving in the background.
    tfaSetupRequestGeneration += 1;
    const overlayId = 'tfaSetupOverlay';
    const overlay = document.getElementById(overlayId);
    overlay?.classList.remove('active');
    overlay?.setAttribute('aria-hidden', 'true');
    if (last2FABodyOverflow !== null) {
        document.body.style.overflow = last2FABodyOverflow;
        last2FABodyOverflow = null;
    }
    tfaTempSecret = null;
    tfaDeliveryHint = '';
    clear2FASetupError();
    set2FASetupSubmitting(false);
    clear2FASetupQrState();
    refresh2FASetupCopyState({ secret: '', otpauthUri: '' });
    update2FASetupLayout(tfaProvider, tfaDeliveryHint);
    // reset the inputs
    const digitInputs = document.querySelectorAll('#tfaSetupOverlay .tfa-digit');
    digitInputs.forEach(input => {
        input.value = '';
    });

    if (last2FAFocusTrigger && document.contains(last2FAFocusTrigger)) {
        last2FAFocusTrigger.focus();
    }
}

// Utility: focus the first .tfa-digit input in the given overlay
function focusFirstTfaInput(overlayId) {
    const overlay = document.getElementById(overlayId);
    if (!overlay) return;

    // Helper to focus the input
    const doFocus = () => {
        const firstInput = overlay.querySelector('.tfa-digit');
        if (firstInput) {
            firstInput.focus({ preventScroll: true });
            firstInput.select?.(); // highlight existing value if any
        }
    };

    // Primary: wait for opacity transition to finish so element is visible
    const onRevealEnd = (e) => {
        if (e.type === 'animationend' || e.propertyName === 'opacity') {
            doFocus();
            overlay.removeEventListener('transitionend', onRevealEnd);
            overlay.removeEventListener('animationend', onRevealEnd);
        }
    };
    overlay.addEventListener('transitionend', onRevealEnd);
    overlay.addEventListener('animationend', onRevealEnd);

    // Fallback: in case there is no transition (or it's very short)
    setTimeout(doFocus, 350);
}

document.getElementById('deactivate2FAButton')?.addEventListener('click', deactivate2FA);
async function deactivate2FA() {
    // Fail closed if the shared verification dialog did not load. Sending the
    // request without this check would only produce a backend 403 and would
    // weaken the intended user-presence guarantee if the backend regressed.
    if (typeof window.ensureSecurityStepUp !== 'function') {
        if (typeof notifyError === 'function') {
            notifyError(translateTwoFa('step_up_failed', 'Authentication failed. Please try again.'));
        }
        return;
    }

    const steppedUp = await window.ensureSecurityStepUp();
    if (!steppedUp) {
        return;
    }

    try {
        const res = await window.authedFetch(`/api/v1/auth/twofa/deactivate`, {
            method: "POST",
            body: JSON.stringify({})
        });
        if (res.ok) {
            setTwoFactorSettingsState({ enrolled: false });
            if (typeof notifySuccess === 'function') {
                notifySuccess(translateTwoFa('tfa_deactivated', '2FA deactivated'));
            }
            return;
        }

        // Only an invalid session requires sign-in. A 403 can be an expired
        // step-up or same-origin policy response and must not log out a valid
        // session as the old implementation did.
        if (res.status === 401) {
            redirectToLogin();
            return;
        }

        if (typeof notifyError === 'function') {
            const fallback = res.status === 403
                ? translateTwoFa('step_up_failed', 'Authentication failed. Please try again.')
                : translateTwoFa('tfa_deactivate_failed', 'Failed to deactivate 2FA.');
            const err = await res.json().catch(() => ({}));
            const detail = err?.detail || err?.message;
            const message = typeof window.translateBackendDetail === 'function'
                ? window.translateBackendDetail(detail, fallback)
                : (detail || fallback);
            notifyError(message);
        }
    } catch (_) {
        if (typeof notifyError === 'function') {
            notifyError(translateTwoFa('tfa_deactivate_failed', 'Failed to deactivate 2FA.'));
        }
    }
}

// Initialize interactions for the TFA overlay (inputs and copy-to-clipboard)
function initializeTfaOverlayInteractions() {
    const overlay = document.getElementById('tfaSetupOverlay');
    if (!overlay) return;
    if (overlay.dataset.tfaInteractionsReady === 'true') {
        refresh2FASetupCopyState();
        update2FASetupLayout(tfaProvider, tfaDeliveryHint);
        return;
    }
    overlay.dataset.tfaInteractionsReady = 'true';
    overlay.setAttribute('aria-hidden', overlay.classList.contains('active') ? 'false' : 'true');

    overlay.addEventListener('keydown', (event) => {
        if (!overlay.classList.contains('active')) {
            return;
        }

        if (event.key === 'Escape') {
            event.preventDefault();
            hide2FASetup();
            return;
        }

        if (event.key !== 'Tab') {
            return;
        }

        const focusableElements = Array.from(overlay.querySelectorAll(
            'input:not([disabled]), button:not([disabled]), a[href], select:not([disabled]), textarea:not([disabled]), [contenteditable]:not([contenteditable="false"]), [tabindex]:not([tabindex="-1"])'
        )).filter((element) => {
            if (element.hasAttribute('hidden')) {
                return false;
            }
            const computedStyle = window.getComputedStyle(element);
            if (computedStyle.display === 'none' || computedStyle.visibility === 'hidden' || computedStyle.visibility === 'collapse') {
                return false;
            }
            const hasLayout = element.offsetParent !== null || computedStyle.position === 'fixed';
            const hasDimensions = element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0;
            return hasLayout && hasDimensions;
        });

        if (focusableElements.length === 0) {
            return;
        }

        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (event.shiftKey && document.activeElement === firstElement) {
            event.preventDefault();
            lastElement.focus();
        } else if (!event.shiftKey && document.activeElement === lastElement) {
            event.preventDefault();
            firstElement.focus();
        }
    });

    const inputs = overlay.querySelectorAll('.tfa-digit');
    inputs.forEach((input, idx) => {
        // Ensure numeric-only
        input.setAttribute('inputmode', 'numeric');
        input.setAttribute('pattern', '[0-9]*');
        input.addEventListener('input', (e) => {
            clear2FASetupError();
            e.target.value = e.target.value.replace(/\D/g, '').slice(0, 1);
            if (e.target.value && idx < inputs.length - 1) {
                inputs[idx + 1].focus();
                inputs[idx + 1].select?.();
            }
            // If all 6 digits are filled, auto-trigger verification
            const allFilled = Array.from(inputs).every(inp => inp.value && inp.value.length === 1);
            if (allFilled) {
                // small delay to ensure DOM updates are flushed
                setTimeout(() => verify2FASetup(), 0);
            }
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace') {
                const cursorAtStart = e.target.selectionStart === 0;
                if (!e.target.value && idx > 0) {
                    // Current input is empty, move to previous and delete it
                    inputs[idx - 1].value = '';
                    inputs[idx - 1].focus();
                    inputs[idx - 1].select?.();
                    e.preventDefault();
                } else if (e.target.value && cursorAtStart && idx > 0) {
                    // Cursor is before the digit, delete current and move to previous
                    e.target.value = '';
                    inputs[idx - 1].focus();
                    inputs[idx - 1].select?.();
                    e.preventDefault();
                }
                // If current input has value and cursor is after it, let default backspace work
            }
            if ((e.key === 'ArrowLeft' || e.key === 'ArrowUp') && idx > 0) {
                inputs[idx - 1].focus();
                e.preventDefault();
            }
            if ((e.key === 'ArrowRight' || e.key === 'ArrowDown') && idx < inputs.length - 1) {
                inputs[idx + 1].focus();
                e.preventDefault();
            }
        });
    });

    // Handle pasting a full 6-digit code into the first input
    const first = inputs[0];
    if (first) {
        first.addEventListener('paste', (e) => {
            const text = (e.clipboardData || window.clipboardData).getData('text') || '';
            if (/^\d{6}$/.test(text)) {
                e.preventDefault();
                inputs.forEach((inp, i) => inp.value = text.charAt(i));
                // auto-submit when a full code is pasted
                setTimeout(() => verify2FASetup(), 0);
            }
        });
    }

    const qrContainer = document.getElementById('tfaQrCode');

    qrContainer?.addEventListener('click', () => copyCurrentTfaSetupValue('secret'));
    qrContainer?.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') {
            return;
        }
        e.preventDefault();
        copyCurrentTfaSetupValue('secret');
    });

    refresh2FASetupCopyState();
    update2FASetupLayout(tfaProvider, tfaDeliveryHint);
}
