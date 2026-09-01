const loginModalManager = window.loginModalManager || (function createLoginModalManager() {
    const OVERLAY_IDS = [
        'federatedTermsOverlay',
        'warningOverlay',
        'pendingOverlay',
        'tfaSetupOverlay',
        'tfaVerifyOverlay',
        'accessBlockedOverlay',
    ];
    const MANAGED_INERT_ATTR = 'data-login-modal-managed-inert';
    const modalStates = new WeakMap();
    const activeOverlays = [];
    let bodyOverflowBeforeModal = null;

    function isVisible(element) {
        return Boolean(element)
            && !element.hidden
            && element.getAttribute('aria-hidden') !== 'true'
            && element.classList.contains('active');
    }

    function getActiveOverlay() {
        for (let index = activeOverlays.length - 1; index >= 0; index -= 1) {
            if (isVisible(activeOverlays[index])) {
                return activeOverlays[index];
            }
            activeOverlays.splice(index, 1);
        }

        const visibleOverlay = OVERLAY_IDS
            .map((overlayId) => document.getElementById(overlayId))
            .find((overlay) => isVisible(overlay)) || null;
        if (visibleOverlay) {
            activeOverlays.push(visibleOverlay);
        }
        return visibleOverlay;
    }

    function getFocusableElements(container) {
        if (!container) {
            return [];
        }
        const selectors = [
            'button:not([disabled])',
            '[href]',
            'input:not([disabled]):not([type="hidden"])',
            'select:not([disabled])',
            'textarea:not([disabled])',
            '[tabindex]:not([tabindex="-1"])',
        ].join(', ');

        return Array.from(container.querySelectorAll(selectors)).filter((element) => {
            if (element.hidden || element.closest('[hidden]')) {
                return false;
            }
            return element.getClientRects().length > 0;
        });
    }

    function syncBodyInertState(activeOverlay) {
        if (!document.body) {
            return;
        }
        Array.from(document.body.children).forEach((child) => {
            if (child === activeOverlay) {
                if (child.hasAttribute(MANAGED_INERT_ATTR)) {
                    child.inert = false;
                }
                child.removeAttribute(MANAGED_INERT_ATTR);
                return;
            }

            if (activeOverlay) {
                if (!child.inert) {
                    child.inert = true;
                    child.setAttribute(MANAGED_INERT_ATTR, 'true');
                }
                return;
            }

            if (child.hasAttribute(MANAGED_INERT_ATTR)) {
                child.inert = false;
                child.removeAttribute(MANAGED_INERT_ATTR);
            }
        });
    }

    function syncBodyScrollState(activeOverlay) {
        if (!document.body?.style) {
            return;
        }
        if (activeOverlay) {
            if (bodyOverflowBeforeModal === null) {
                bodyOverflowBeforeModal = document.body.style.overflow || '';
            }
            document.body.style.overflow = 'hidden';
            return;
        }
        if (bodyOverflowBeforeModal !== null) {
            document.body.style.overflow = bodyOverflowBeforeModal;
            bodyOverflowBeforeModal = null;
        }
    }

    function sync() {
        const activeOverlay = getActiveOverlay();
        syncBodyInertState(activeOverlay);
        syncBodyScrollState(activeOverlay);
        return activeOverlay;
    }

    function getDialog(overlay) {
        return overlay?.querySelector?.('[role="dialog"]') || overlay;
    }

    function resolveElement(value) {
        return typeof value === 'function' ? value() : value;
    }

    function focusElement(element) {
        if (!element || typeof element.focus !== 'function' || element.isConnected === false) {
            return false;
        }
        try {
            element.focus({ preventScroll: true });
        } catch (_) {
            element.focus();
        }
        return true;
    }

    function focusOverlay(overlay, preferredTarget) {
        if (!isVisible(overlay)) {
            return;
        }
        const preferred = resolveElement(preferredTarget);
        const target = preferred && overlay.contains?.(preferred)
            ? preferred
            : getFocusableElements(overlay)[0] || getDialog(overlay);
        focusElement(target);
    }

    function scheduleOverlayFocus(overlay, preferredTarget) {
        const schedule = typeof window.requestAnimationFrame === 'function'
            ? window.requestAnimationFrame.bind(window)
            : (callback) => window.setTimeout(callback, 0);
        schedule(() => focusOverlay(overlay, preferredTarget));
    }

    function removeActiveOverlay(overlay) {
        let index = activeOverlays.lastIndexOf(overlay);
        while (index !== -1) {
            activeOverlays.splice(index, 1);
            index = activeOverlays.lastIndexOf(overlay);
        }
    }

    function cancelPendingClose(overlay, state) {
        if (state.closeTimer) {
            window.clearTimeout(state.closeTimer);
            state.closeTimer = 0;
        }
        if (state.animationHandler && state.dialog?.removeEventListener) {
            state.dialog.removeEventListener('animationend', state.animationHandler);
        }
        state.animationHandler = null;
        state.closeSequence += 1;
        overlay.classList.remove('is-closing');
    }

    function open(overlay, options = {}) {
        if (!overlay) {
            return null;
        }
        const existingState = modalStates.get(overlay);
        const wasOpen = isVisible(overlay);
        const state = existingState || {
            closeSequence: 0,
            closeTimer: 0,
            animationHandler: null,
            dialog: getDialog(overlay),
            returnFocus: null,
            fallbackFocus: null,
            initialFocus: null,
            dismiss: null,
            canDismiss: true,
        };
        cancelPendingClose(overlay, state);

        if (!wasOpen) {
            const currentFocus = document.activeElement;
            state.returnFocus = options.returnFocus
                || (currentFocus?.isConnected !== false && currentFocus !== document.body ? currentFocus : null);
        }
        state.initialFocus = options.initialFocus ?? state.initialFocus;
        state.fallbackFocus = options.fallbackFocus ?? state.fallbackFocus;
        state.dismiss = options.dismiss ?? state.dismiss;
        state.canDismiss = options.canDismiss ?? state.canDismiss;
        modalStates.set(overlay, state);

        overlay.hidden = false;
        overlay.classList.add('active');
        overlay.inert = false;
        overlay.setAttribute('aria-hidden', 'false');
        removeActiveOverlay(overlay);
        activeOverlays.push(overlay);
        sync();
        scheduleOverlayFocus(overlay, state.initialFocus);
        return overlay;
    }

    function restoreFocusAfterClose(state) {
        const nextOverlay = getActiveOverlay();
        if (nextOverlay) {
            const nextState = modalStates.get(nextOverlay);
            scheduleOverlayFocus(nextOverlay, nextState?.initialFocus);
            return;
        }
        const returnFocus = resolveElement(state.returnFocus);
        const fallbackFocus = resolveElement(state.fallbackFocus);
        focusElement(returnFocus) || focusElement(fallbackFocus);
    }

    function close(overlay, options = {}) {
        if (!overlay) {
            return false;
        }
        const state = modalStates.get(overlay) || {
            closeSequence: 0,
            closeTimer: 0,
            animationHandler: null,
            dialog: getDialog(overlay),
            returnFocus: null,
            fallbackFocus: options.fallbackFocus || null,
        };
        const wasOpen = isVisible(overlay);
        cancelPendingClose(overlay, state);
        state.fallbackFocus = options.fallbackFocus ?? state.fallbackFocus;
        modalStates.set(overlay, state);

        overlay.classList.remove('active');
        overlay.setAttribute('aria-hidden', 'true');
        overlay.inert = true;
        removeActiveOverlay(overlay);
        sync();

        if (!wasOpen) {
            overlay.hidden = true;
            return false;
        }

        if (options.restoreFocus !== false) {
            restoreFocusAfterClose(state);
        }

        const closeSequence = ++state.closeSequence;
        const finishClose = () => {
            if (state.closeSequence !== closeSequence || isVisible(overlay)) {
                return;
            }
            if (state.closeTimer) {
                window.clearTimeout(state.closeTimer);
                state.closeTimer = 0;
            }
            if (state.animationHandler && state.dialog?.removeEventListener) {
                state.dialog.removeEventListener('animationend', state.animationHandler);
            }
            state.animationHandler = null;
            overlay.classList.remove('is-closing');
            overlay.hidden = true;
        };

        overlay.classList.add('is-closing');
        state.animationHandler = (event) => {
            if (event.target !== state.dialog) {
                return;
            }
            finishClose();
        };
        state.dialog?.addEventListener?.('animationend', state.animationHandler);
        state.closeTimer = window.setTimeout(finishClose, 240);
        return true;
    }

    function canDismissOverlay(state) {
        return typeof state?.canDismiss === 'function'
            ? Boolean(state.canDismiss())
            : state?.canDismiss !== false;
    }

    document.addEventListener('keydown', (event) => {
        const activeOverlay = getActiveOverlay();
        if (!activeOverlay) {
            return;
        }

        if (event.key === 'Escape') {
            const state = modalStates.get(activeOverlay);
            event.preventDefault();
            if (typeof event.stopImmediatePropagation === 'function') {
                event.stopImmediatePropagation();
            } else {
                event.stopPropagation();
            }
            if (canDismissOverlay(state) && typeof state?.dismiss === 'function') {
                state.dismiss();
            }
            return;
        }

        if (event.key !== 'Tab') {
            return;
        }

        const focusable = getFocusableElements(activeOverlay);
        if (!focusable.length) {
            event.preventDefault();
            focusElement(getDialog(activeOverlay));
            return;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const activeElement = document.activeElement;

        if (!activeOverlay.contains(activeElement)) {
            event.preventDefault();
            first.focus();
            return;
        }

        if (event.shiftKey && activeElement === first) {
            event.preventDefault();
            last.focus();
            return;
        }

        if (!event.shiftKey && activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }, true);

    return {
        close,
        getActiveOverlay,
        open,
        setActiveState(overlay, isActive, options = {}) {
            return isActive ? open(overlay, options) : close(overlay, options);
        },
        sync,
    };
})();

window.loginModalManager = loginModalManager;

function set2FAOverlayActiveState(overlayId, isActive, options = {}) {
    const overlay = document.getElementById(overlayId);
    if (!overlay) {
        return null;
    }
    if (typeof window.loginModalManager?.setActiveState === 'function') {
        window.loginModalManager.setActiveState(overlay, isActive, options);
        return overlay;
    }
    overlay.classList.toggle('active', isActive);
    overlay.hidden = !isActive;
    overlay.inert = !isActive;
    overlay.setAttribute('aria-hidden', isActive ? 'false' : 'true');
    window.loginModalManager?.sync();
    const focusTarget = isActive ? options.initialFocus : options.fallbackFocus;
    if (focusTarget) {
        setTimeout(() => {
            const element = typeof focusTarget === 'function' ? focusTarget() : focusTarget;
            element?.focus?.();
        }, 0);
    }
    return overlay;
}

['tfaSetupOverlay', 'tfaVerifyOverlay'].forEach((overlayId) => {
    set2FAOverlayActiveState(overlayId, false);
});

// Function to show/hide overlays
function show2FASetup() {
    const overlayId = 'tfaSetupOverlay';
    set2FAOverlayActiveState(overlayId, true, {
        initialFocus: () => document.querySelector(`#${overlayId} .tfa-digit`),
        fallbackFocus: () => document.getElementById('signinEmail'),
        dismiss: () => hide2FAOverlay(overlayId),
    });
    refresh2FASetupCopyState();
    update2FASetupLayout((window.omlorix2FAContext || {}).provider || 'totp', (window.omlorix2FAContext || {}).deliveryHint || '');
}

window.omlorix2FAContext = window.omlorix2FAContext || {
    provider: 'totp',
    deliveryHint: '',
    resendAvailableInSeconds: 0,
    totpSecret: '',
    otpauthUri: '',
};

let tfaQrHintTimeout = null;

function translate2FA(key, fallback) {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function format2FA(key, fallback, vars) {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    const translated = translate2FA(key, fallback);
    if (!vars || typeof vars !== 'object') {
        return translated;
    }
    return Object.entries(vars).reduce((text, [name, value]) => {
        if (typeof value === 'undefined') {
            return text;
        }
        return text.replaceAll(`{${name}}`, String(value));
    }, translated);
}

function notify2FAError(key, fallback, vars) {
    if (typeof notifyError === 'function') {
        notifyError(format2FA(key, fallback, vars));
    }
}

function normalizeTfaSecret(secret) {
    return String(secret || '').replace(/\s+/g, '').trim();
}

function isTotpProvider(provider) {
    return String(provider || 'totp').trim().toLowerCase() === 'totp';
}

function get2FASetupDeliveryText(provider, deliveryHint) {
    const hint = String(deliveryHint || '').trim();
    const formattedHint = hint ? ` ${hint}` : '';
    if (provider === 'email') {
        return formattedHint
            ? format2FA('tfa_email_instruction', 'Enter the 6-digit code sent to{hint}.', { hint: formattedHint })
            : translate2FA('tfa_email_instruction_generic', 'Enter the 6-digit code from the verification email.');
    }
    return '';
}

function update2FASetupDescription(showTotpSetup, hasDeliveryHint) {
    const setupOverlay = document.getElementById('tfaSetupOverlay');
    if (!setupOverlay) {
        return;
    }
    const setupDialog = document.getElementById('tfaSetupModal')
        || setupOverlay.querySelector?.('[role="dialog"]')
        || setupOverlay;

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

function updateTfaDigitAriaLabels() {
    document.querySelectorAll('.tfa-digit[data-digit-index]').forEach((input) => {
        const index = Number.parseInt(input.dataset.digitIndex || '', 10);
        if (!Number.isInteger(index) || index < 1) {
            return;
        }
        input.setAttribute(
            'aria-label',
            format2FA('tfa_digit_aria', 'Digit {index} of {total}', {
                index,
                total: 6,
            }),
        );
    });
}

function getTfaQrHintDefaultText(secret, otpauthUri) {
    if (!secret && !otpauthUri) {
        return '';
    }
    return secret
        ? translate2FA('tfa_qr_copy_secret_hint', 'Click QR code to copy setup code')
        : translate2FA('tfa_qr_copy_link_hint', 'Click QR code to copy setup link');
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
    const ctx = window.omlorix2FAContext || {};
    const qrContainer = document.getElementById('tfaQrCode');
    const payloadSecret = normalizeTfaSecret(payload.secret);
    const payloadUri = String(payload.otpauthUri || '').trim();
    const qrSecret = normalizeTfaSecret(qrContainer?.dataset?.tfaSecret);
    const qrUri = String(qrContainer?.dataset?.tfaQrPayload || '').trim();
    const ctxSecret = normalizeTfaSecret(ctx.totpSecret);
    const ctxUri = String(ctx.otpauthUri || '').trim();
    const secret = payloadSecret || qrSecret || ctxSecret;
    const otpauthUri = payloadUri || qrUri || ctxUri;

    ctx.totpSecret = secret;
    ctx.otpauthUri = otpauthUri;
    window.omlorix2FAContext = ctx;

    return { secret, otpauthUri };
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

async function copyCurrentTfaSetupValue(prefer = 'secret') {
    const { secret, otpauthUri } = readCurrentTfaSetupData();
    const value = prefer === 'uri'
        ? (otpauthUri || secret)
        : (secret || otpauthUri);

    if (!value) {
        flashTfaQrHint(translate2FA('tfa_copy_unavailable', 'Nothing to copy yet. Try again in a second.'), 'error');
        return;
    }

    const copied = await copyTextToClipboard(value);
    if (!copied) {
        flashTfaQrHint(translate2FA('tfa_copy_failed', 'Copy failed. Please try again.'), 'error');
        return;
    }

    const copiedSecret = value === secret;
    flashTfaQrHint(
        copiedSecret
            ? translate2FA('tfa_secret_copy_success', 'Secret code copied to clipboard.')
            : translate2FA('tfa_link_copy_success', 'Setup link copied to clipboard.'),
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
                    ? translate2FA('tfa_qr_copy_secret_aria', 'Copy 2FA setup secret code')
                    : translate2FA('tfa_qr_copy_link_aria', 'Copy 2FA setup link')
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

function set2FAContext(result = {}) {
    const ctx = window.omlorix2FAContext || {};
    ctx.provider = result.provider || ctx.provider || 'totp';
    ctx.deliveryHint = result.delivery_hint || '';
    ctx.resendAvailableInSeconds = Number(result.resend_available_in_seconds || 0);
    if (!isTotpProvider(ctx.provider)) {
        ctx.totpSecret = '';
        ctx.otpauthUri = '';
        clear2FASetupQrState();
    }
    if (Object.prototype.hasOwnProperty.call(result, 'secret')) {
        ctx.totpSecret = normalizeTfaSecret(result.secret);
    }
    if (Object.prototype.hasOwnProperty.call(result, 'qrcode')) {
        ctx.otpauthUri = String(result.qrcode || '').trim();
    }
    window.omlorix2FAContext = ctx;
    update2FAOverlayCopy();
    refresh2FASetupCopyState({ secret: ctx.totpSecret || '', otpauthUri: ctx.otpauthUri || '' });
    if (result.setup_material_available && isTotpProvider(ctx.provider)) {
        void load2FASetupMaterial();
    }
}

async function load2FASetupMaterial() {
    try {
        const response = await fetch('/api/v1/auth/twofa/setup-material', {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) {
            throw new Error(`Failed to fetch 2FA setup material (${response.status})`);
        }
        const material = await response.json();
        const ctx = window.omlorix2FAContext || {};
        ctx.provider = material.provider || ctx.provider || 'totp';
        ctx.totpSecret = normalizeTfaSecret(material.secret);
        ctx.otpauthUri = String(material.qrcode || '').trim();
        window.omlorix2FAContext = ctx;
        refresh2FASetupCopyState({ secret: ctx.totpSecret || '', otpauthUri: ctx.otpauthUri || '' });
        if (ctx.otpauthUri && typeof renderQrCodeWhenVisible === 'function') {
            renderQrCodeWhenVisible(ctx.otpauthUri);
        } else if (ctx.otpauthUri && typeof renderQrCode === 'function') {
            renderQrCode(ctx.otpauthUri);
        }
    } catch (error) {
        console.error('Failed to load 2FA setup material:', error);
        notify2FAError('tfa_failed', '2FA failed. Please try again.');
    }
}

window.load2FASetupMaterial = load2FASetupMaterial;

function update2FAOverlayCopy() {
    const ctx = window.omlorix2FAContext || {};
    const provider = ctx.provider || 'totp';
    const hint = ctx.deliveryHint ? ` ${ctx.deliveryHint}` : '';
    const verifyInstruction = document.getElementById('tfaVerifyInstruction');
    const step1 = document.getElementById('tfaStep1');
    const step2 = document.getElementById('tfaStep2');
    const step3 = document.getElementById('tfaStep3');

    update2FASetupLayout(provider, ctx.deliveryHint || '');

    if (provider === 'email') {
        if (verifyInstruction) {
            verifyInstruction.textContent = hint
                ? format2FA(
                    'tfa_email_instruction',
                    'Enter the 6-digit code sent to{hint}.',
                    { hint },
                )
                : translate2FA('tfa_email_instruction_generic', 'Enter the 6-digit code from the verification email.');
        }
        if (step1) step1.textContent = translate2FA('tfa_email_step_1', 'Open your inbox and find the one-time verification email.');
        if (step2) step2.textContent = translate2FA('tfa_email_step_2', 'Enter the 6-digit code from the email.');
        if (step3) step3.textContent = translate2FA('tfa_email_step_3', 'Codes expire quickly. Request a new code if needed.');
        return;
    }

    if (verifyInstruction) verifyInstruction.textContent = translate2FA('tfa_verify_instruction', 'Enter the 6-digit code from your authenticator app.');
    if (step1) step1.textContent = translate2FA('tfa_step_1', 'Download an authenticator app like Google Authenticator.');
    if (step2) step2.textContent = translate2FA('tfa_step_2', 'Scan the QR code with your authenticator app.');
    if (step3) step3.textContent = translate2FA('tfa_step_3', 'Enter the 6-digit verification code generated by your app below.');
}

window.update2FAOverlayCopy = update2FAOverlayCopy;
window.set2FAContextFromResult = set2FAContext;

function show2FAVerify() {
    const overlayId = 'tfaVerifyOverlay';
    set2FAOverlayActiveState(overlayId, true, {
        initialFocus: () => document.querySelector(`#${overlayId} .tfa-digit`),
        fallbackFocus: () => document.getElementById('signinEmail'),
        dismiss: () => hide2FAOverlay(overlayId),
    });
}


function hide2FAOverlay(overlayId) {
    set2FAOverlayActiveState(overlayId, false, {
        fallbackFocus: () => document.getElementById('signinEmail'),
    });
    resetInputs(overlayId);

    if (window.passkeyLogin && typeof window.passkeyLogin.clearPasskeyLogin2FAFlow === 'function') {
        window.passkeyLogin.clearPasskeyLogin2FAFlow();
    }
    try {
        sessionStorage.removeItem('social_login_provider');
        sessionStorage.removeItem('sso_login_provider');
    } catch (_) {}
    
}




// Reset input fields and state
function resetInputs(overlayId) {
    const inputs = document.querySelectorAll(`#${overlayId} .tfa-digit`);
    inputs.forEach(input => input.value = '');

    // Clear generated QR code when overlay is dismissed
    const qrContainer = document.getElementById('tfaQrCode');
    if (qrContainer) {
        qrContainer.innerHTML = '';
        delete qrContainer.dataset.tfaSecret;
        delete qrContainer.dataset.tfaQrPayload;
    }
    if (overlayId === 'tfaSetupOverlay') {
        const ctx = window.omlorix2FAContext || {};
        ctx.totpSecret = '';
        ctx.otpauthUri = '';
        window.omlorix2FAContext = ctx;
        refresh2FASetupCopyState({ secret: '', otpauthUri: '' });
        update2FASetupLayout(ctx.provider || 'totp', ctx.deliveryHint || '');
    }
}



// Auto-focus next input when typing
document.addEventListener('DOMContentLoaded', function() {
    // Setup separate event listeners for each overlay to avoid conflicts
    setupDigitInputs('tfaSetupOverlay', verifySetupCode);
    setupDigitInputs('tfaVerifyOverlay', verifyCode);

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
    update2FASetupLayout((window.omlorix2FAContext || {}).provider || 'totp', (window.omlorix2FAContext || {}).deliveryHint || '');
    updateTfaDigitAriaLabels();
    document.addEventListener('i18n:updated', () => {
        updateTfaDigitAriaLabels();
        update2FAOverlayCopy();
        refresh2FASetupCopyState();
    });
    
    function setupDigitInputs(overlayId, verifyFunction) {
        const overlay = document.getElementById(overlayId);
        const digitInputs = overlay.querySelectorAll('.tfa-digit');
        
        digitInputs.forEach((input, index) => {
            input.addEventListener('input', function() {
                if (this.value.length === 1) {
                    // Move to next input if available
                    if (index < digitInputs.length - 1) {
                        digitInputs[index + 1].focus();
                    } else {
                        // This is the last input and it's filled
                        // Check if all inputs have values
                        let allFilled = true;
                        
                        digitInputs.forEach(digit => {
                            if (!digit.value) {
                                allFilled = false;
                            }
                        });
                        
                        if (allFilled) {
                            // Automatically trigger verification
                            verifyFunction();
                        }
                    }
                }
            });
            
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Backspace' && this.value.length === 0 && index > 0) {
                    digitInputs[index - 1].focus();
                }
            });

            // Allow pasting the full 6-digit code
            input.addEventListener('paste', function(e) {
                e.preventDefault();
                const pasteData = (e.clipboardData || window.clipboardData).getData('text')
                    .replace(/\D/g, '') // digits only
                    .slice(0, digitInputs.length);

                pasteData.split('').forEach((char, idx) => {
                    digitInputs[idx].value = char;
                });

                // Focus next empty input or trigger verification if all filled
                if (pasteData.length === digitInputs.length) {
                    verifyFunction();
                } else if (pasteData.length < digitInputs.length) {
                    digitInputs[pasteData.length].focus();
                }
            });
        });
    }
});


// Close Setup on Close Button
document.getElementById('tfaSetupCancelButton').addEventListener('click', () => hide2FAOverlay("tfaSetupOverlay"));
document.getElementById('tfaSetupCloseButton')?.addEventListener('click', () => hide2FAOverlay("tfaSetupOverlay"));
// Close Verify on Close Button
document.getElementById('tfaVerifyCancelButton')?.addEventListener('click', () => hide2FAOverlay("tfaVerifyOverlay"));
document.getElementById('tfaVerifyCloseButton')?.addEventListener('click', () => hide2FAOverlay("tfaVerifyOverlay"));



// Close on overlay click (but not on card click)
document.getElementById('tfaSetupOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
        hide2FAOverlay('tfaSetupOverlay');
    }
});
document.getElementById('tfaVerifyOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
        hide2FAOverlay('tfaVerifyOverlay');
    }
});


document.getElementById('tfaSetupPrimaryButton').addEventListener('click', () => verifySetupCode());
document.getElementById('tfaVerifyPrimaryButton').addEventListener('click', () => verifyCode());
// Verification functions - these connect to the backend 
function verifySetupCode() {
    // Check if this is an SSO login 2FA flow
    if (window.enterpriseSSO && window.enterpriseSSO.isInSSOLogin2FAFlow()) {
        completeEnterpriseSSO2FA("setup");
    }
    // Check if this is a social login 2FA flow
    else if (window.socialLogin && window.socialLogin.isInSocialLogin2FAFlow()) {
        completeSocialLogin2FA("setup");
    } else if (window.passkeyLogin && typeof window.passkeyLogin.isInPasskeyLogin2FAFlow === 'function' && window.passkeyLogin.isInPasskeyLogin2FAFlow()) {
        completePasskeyLogin2FA("setup");
    } else {
        signin("setup", null);
    }
}

function verifyCode() {
    // Check if this is an SSO login 2FA flow
    if (window.enterpriseSSO && window.enterpriseSSO.isInSSOLogin2FAFlow()) {
        completeEnterpriseSSO2FA("verify");
    }
    // Check if this is a social login 2FA flow
    else if (window.socialLogin && window.socialLogin.isInSocialLogin2FAFlow()) {
        completeSocialLogin2FA("verify");
    } else if (window.passkeyLogin && typeof window.passkeyLogin.isInPasskeyLogin2FAFlow === 'function' && window.passkeyLogin.isInPasskeyLogin2FAFlow()) {
        completePasskeyLogin2FA("verify");
    } else {
        signin("verify");
    }
}

async function completePasskeyLogin2FA(otpType) {
    const overlayId = otpType === "setup" ? 'tfaSetupOverlay' : 'tfaVerifyOverlay';
    const digitInputs = document.querySelectorAll(`#${overlayId} .tfa-digit`);
    let otpCode = '';
    digitInputs.forEach(input => {
        otpCode += input.value;
    });

    if (otpCode.length !== 6) {
        notify2FAError('tfa_code_incomplete', 'Please fill in all 6 digits.');
        return;
    }

    let result = null;
    try {
        result = await window.passkeyLogin.completePasskeyLoginWith2FA(otpCode, otpType, null);
    } catch (error) {
        console.error('Passkey 2FA completion error:', error);
        notify2FAError('tfa_passkey_complete_failed', 'Failed to complete passkey 2FA. Please try again.');
        return;
    }

    if (!result) {
        notify2FAError('tfa_failed', '2FA failed. Please try again.');
        return;
    }

    if (result.status === 'otp_invalid') {
        notify2FAError('signin_error_invalid_otp', 'Invalid two-factor authentication code. Please try again.');
    } else if (result.status === 'otp_locked') {
        notify2FAError('signin_error_otp_locked', 'Too many invalid two-factor authentication attempts. Please try again later.');
    } else if (result.status === 'lock') {
        if (typeof window.showLoginAccountLockWarning === 'function') {
            window.showLoginAccountLockWarning(result);
        }
        hide2FAOverlay(overlayId);
    } else if (result.status === 'inactive') {
        if (typeof window.showInactiveAccountWarning === 'function') {
            window.showInactiveAccountWarning();
        }
        hide2FAOverlay(overlayId);
    } else if (result.status === 'otp_setup') {
        if (typeof set2FAContext === 'function') set2FAContext(result);
        if (isTotpProvider(result.provider)) {
            if (result.qrcode && typeof renderQrCode === 'function') {
                renderQrCode(result.qrcode);
            } else if (typeof refresh2FASetupCopyState === 'function') {
                refresh2FASetupCopyState({ secret: result.secret || '', otpauthUri: '' });
            }
        } else if (typeof refresh2FASetupCopyState === 'function') {
            refresh2FASetupCopyState({ secret: result.secret || '', otpauthUri: '' });
        }
        show2FASetup();
    } else if (result.status === 'otp_required_already_setup') {
        if (typeof set2FAContext === 'function') set2FAContext(result);
        show2FAVerify();
    } else if (result.status === 'error') {
        notify2FAError(
            result.detail || 'tfa_authentication_error',
            result.detail || 'An error occurred during authentication.',
        );
        hide2FAOverlay(overlayId);
    }
}

// Complete social login with 2FA
async function completeSocialLogin2FA(otpType) {
    const overlayId = otpType === "setup" ? 'tfaSetupOverlay' : 'tfaVerifyOverlay';
    const digitInputs = document.querySelectorAll(`#${overlayId} .tfa-digit`);
    let otpCode = '';
    digitInputs.forEach(input => {
        otpCode += input.value;
    });
    
    if (otpCode.length !== 6) {
        notify2FAError('tfa_code_incomplete', 'Please fill in all 6 digits.');
        return;
    }

    const result = await window.socialLogin.completeSocialLoginWith2FA(otpCode, otpType, null);
    
    if (!result) return;
    
    if (result.status === 'otp_invalid') {
        notify2FAError('signin_error_invalid_otp', 'Invalid two-factor authentication code. Please try again.');
        // Keep overlay open for retry
    } else if (result.status === 'otp_locked') {
        notify2FAError('signin_error_otp_locked', 'Too many invalid two-factor authentication attempts. Please try again later.');
    } else if (result.status === 'lock') {
        if (typeof window.showLoginAccountLockWarning === 'function') {
            window.showLoginAccountLockWarning(result);
        }
        hide2FAOverlay(overlayId);
    } else if (result.status === 'otp_setup') {
        if (typeof set2FAContext === 'function') set2FAContext(result);
        if (isTotpProvider(result.provider)) {
            if (result.qrcode && typeof renderQrCode === 'function') {
                renderQrCode(result.qrcode);
            } else if (typeof refresh2FASetupCopyState === 'function') {
                refresh2FASetupCopyState({ secret: result.secret || '', otpauthUri: '' });
            }
        } else if (typeof refresh2FASetupCopyState === 'function') {
            refresh2FASetupCopyState({ secret: result.secret || '', otpauthUri: '' });
        }
        show2FASetup();
    } else if (result.status === 'otp_required_already_setup') {
        if (typeof set2FAContext === 'function') set2FAContext(result);
        show2FAVerify();
    } else if (result.status === 'error') {
        notify2FAError(
            result.detail || 'tfa_authentication_error',
            result.detail || 'An error occurred during authentication.',
        );
        hide2FAOverlay(overlayId);
    }
    // Success case is handled inside completeSocialLoginWith2FA with redirect
}

// Complete enterprise SSO login with 2FA
async function completeEnterpriseSSO2FA(otpType) {
    const overlayId = otpType === "setup" ? 'tfaSetupOverlay' : 'tfaVerifyOverlay';
    const digitInputs = document.querySelectorAll(`#${overlayId} .tfa-digit`);
    let otpCode = '';
    digitInputs.forEach(input => {
        otpCode += input.value;
    });
    
    if (otpCode.length !== 6) {
        notify2FAError('tfa_code_incomplete', 'Please fill in all 6 digits.');
        return;
    }

    const result = await window.enterpriseSSO.completeSSOLoginWith2FA(otpCode, otpType, null);
    
    if (!result) return;
    
    if (result.status === 'otp_invalid') {
        notify2FAError('signin_error_invalid_otp', 'Invalid two-factor authentication code. Please try again.');
        // Keep overlay open for retry
    } else if (result.status === 'otp_locked') {
        notify2FAError('signin_error_otp_locked', 'Too many invalid two-factor authentication attempts. Please try again later.');
    } else if (result.status === 'lock') {
        if (typeof window.showLoginAccountLockWarning === 'function') {
            window.showLoginAccountLockWarning(result);
        }
        hide2FAOverlay(overlayId);
    } else if (result.status === 'otp_setup') {
        if (typeof set2FAContext === 'function') set2FAContext(result);
        if (isTotpProvider(result.provider)) {
            if (result.qrcode && typeof renderQrCode === 'function') {
                renderQrCode(result.qrcode);
            } else if (typeof refresh2FASetupCopyState === 'function') {
                refresh2FASetupCopyState({ secret: result.secret || '', otpauthUri: '' });
            }
        } else if (typeof refresh2FASetupCopyState === 'function') {
            refresh2FASetupCopyState({ secret: result.secret || '', otpauthUri: '' });
        }
        show2FASetup();
    } else if (result.status === 'otp_required_already_setup') {
        if (typeof set2FAContext === 'function') set2FAContext(result);
        show2FAVerify();
    } else if (result.status === 'error') {
        notify2FAError(
            result.detail || 'tfa_authentication_error',
            result.detail || 'An error occurred during authentication.',
        );
        hide2FAOverlay(overlayId);
    }
    // Success case is handled inside completeSSOLoginWith2FA with redirect
}
