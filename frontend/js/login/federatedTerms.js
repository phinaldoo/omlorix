(function() {
    'use strict';

    const authFlowContext = window.loginAuthFlowContext || {};
    const resetLoginCallbackUrl = authFlowContext.resetLoginCallbackUrl || function() {
        window.history.replaceState({}, '', '/login');
    };
    const translate = (key, fallback) =>
        typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;

    let pendingType = '';
    let appTermsPolicy = null;
    let appTermsReturnUrl = '';

    function parsePendingType() {
        const params = new URLSearchParams(window.location.search);
        if (params.get('social_terms_pending') === 'true') {
            return 'social';
        }
        if (params.get('sso_terms_pending') === 'true') {
            return 'sso';
        }
        return '';
    }

    function hasAppTermsRequiredIntent() {
        const params = new URLSearchParams(window.location.search);
        return params.get('terms_required') === 'true';
    }

    function resolveReturnUrl() {
        if (typeof window.resolveTermsAcceptanceReturnUrl === 'function') {
            return window.resolveTermsAcceptanceReturnUrl();
        }
        const params = new URLSearchParams(window.location.search);
        const redirect = params.get('redirect') || '';
        if (!redirect) {
            return '/';
        }
        try {
            const parsed = new URL(decodeURIComponent(redirect), window.location.origin);
            return parsed.origin === window.location.origin ? `${parsed.pathname}${parsed.search}${parsed.hash}` : '/';
        } catch (error) {
            return '/';
        }
    }

    function getCurrentAppTermsPolicy() {
        const policy = window.omlorixTermsOfServicePolicy || {};
        if (
            typeof window.isTermsAcceptanceRequired === 'function'
            && !window.isTermsAcceptanceRequired(policy)
        ) {
            return null;
        }
        const revision = Number(policy?.revision || 0);
        if (revision <= 0) {
            return null;
        }
        return policy;
    }

    function shouldShowAppTermsModal(policy = getCurrentAppTermsPolicy()) {
        return Boolean(policy && (hasAppTermsRequiredIntent() || getCurrentAppTermsPolicy()));
    }

    function setOverlayActiveState(isActive, options = {}) {
        const overlay = document.getElementById('federatedTermsOverlay');
        if (!overlay) {
            return;
        }
        if (typeof window.loginModalManager?.setActiveState === 'function') {
            window.loginModalManager.setActiveState(overlay, isActive, options);
            return;
        }
        overlay.classList.toggle('active', isActive);
        overlay.hidden = !isActive;
        overlay.inert = !isActive;
        overlay.setAttribute('aria-hidden', isActive ? 'false' : 'true');
        const focusTarget = isActive ? options.initialFocus : options.fallbackFocus;
        if (focusTarget) {
            window.setTimeout(() => {
                const element = typeof focusTarget === 'function' ? focusTarget() : focusTarget;
                element?.focus?.();
            }, 0);
        }
    }

    async function loadCurrentTermsPolicy() {
        const existingRevision = Number(window.omlorixTermsOfServicePolicy?.revision || 0);
        if (existingRevision > 0) {
            return window.omlorixTermsOfServicePolicy;
        }

        try {
            const response = await fetch('/api/v1/settings/login/setup', { credentials: 'include' });
            if (!response.ok) {
                return {};
            }
            const data = await response.json();
            window.omlorixTermsOfServicePolicy = data.terms_of_service_policy || {};
            return window.omlorixTermsOfServicePolicy;
        } catch (error) {
            return {};
        }
    }

    function setConfirmPending(isPending) {
        const confirmButton = document.getElementById('federatedTermsConfirmButton');
        const cancelButton = document.getElementById('federatedTermsCancelButton');
        const closeButton = document.getElementById('federatedTermsCloseButton');
        if (confirmButton) {
            confirmButton.disabled = isPending;
            confirmButton.setAttribute('aria-busy', isPending ? 'true' : 'false');
        }
        if (cancelButton) {
            cancelButton.disabled = isPending;
        }
        if (closeButton) {
            closeButton.disabled = isPending;
        }
    }

    function isConfirmPending() {
        const confirmButton = document.getElementById('federatedTermsConfirmButton');
        return Boolean(confirmButton?.disabled && confirmButton.getAttribute('aria-busy') === 'true');
    }

    function configureModalCopy(type) {
        const title = document.getElementById('federatedTermsTitle');
        const message = document.getElementById('federatedTermsMessage');
        const confirmButton = document.getElementById('federatedTermsConfirmButton');
        const confirmLabel = confirmButton?.querySelector('[data-i18n]');
        const cancelButton = document.getElementById('federatedTermsCancelButton');
        const closeButton = document.getElementById('federatedTermsCloseButton');
        const isAppAccess = type === 'app';
        const titleKey = isAppAccess ? 'terms_of_service_notice_required_title' : 'federated_terms_title';
        const messageKey = isAppAccess ? 'terms_of_service_notice_default_message' : 'federated_terms_message';
        const confirmKey = isAppAccess ? 'terms_of_service_notice_accept_required' : 'federated_terms_confirm';

        if (title) {
            title.dataset.i18n = titleKey;
            title.textContent = translate(titleKey, isAppAccess
                ? 'Terms of Service acceptance required'
                : 'Accept Terms of Service');
        }
        if (message) {
            message.dataset.i18n = messageKey;
            message.textContent = translate(messageKey, isAppAccess
                ? 'Review and accept the current Terms of Service before continuing.'
                : 'To create your account with this sign-in provider, review and accept the current Terms of Service.');
        }
        if (confirmLabel) {
            confirmLabel.dataset.i18n = confirmKey;
            confirmLabel.textContent = translate(confirmKey, isAppAccess
                ? 'I accept'
                : 'Accept and continue');
        }
        if (cancelButton) {
            cancelButton.hidden = isAppAccess;
        }
        if (closeButton) {
            closeButton.hidden = isAppAccess;
        }
    }

    async function postPendingTerms(endpoint, body = {}) {
        return fetch(endpoint, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    }

    async function confirmPendingTerms() {
        if (!pendingType || isConfirmPending()) {
            return;
        }
        setConfirmPending(true);

        const policy = pendingType === 'app'
            ? (appTermsPolicy || getCurrentAppTermsPolicy() || {})
            : await loadCurrentTermsPolicy();
        const revision = Number(policy?.revision || 0);
        if (revision <= 0) {
            setConfirmPending(false);
            if (typeof window.notifyError === 'function') {
                window.notifyError(translate('terms_configuration_required_error', 'Account registration is unavailable until the operator publishes custom Terms of Service on the login page.'));
            }
            return;
        }

        try {
            const response = pendingType === 'app'
                ? await window.authedFetch('/api/v1/users/terms-of-service/accept', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ revision }),
                })
                : await postPendingTerms(`/api/v1/auth/${pendingType}/pending-terms/confirm`, {
                    accept_terms_of_service: true,
                    terms_of_service_revision: revision,
                });

            if (response.redirected && response.url) {
                window.location.href = response.url;
                return;
            }

            if (!response.ok) {
                const result = await response.json().catch(() => ({}));
                const rawDetail = result?.detail;
                const detail = typeof rawDetail === 'object' && rawDetail !== null
                    ? String(rawDetail.type || '')
                    : String(rawDetail || '');
                const messages = {
                    terms_revision_mismatch: translate('terms_revision_mismatch_error', 'The Terms of Service changed. Review the latest version and try again.'),
                    pending_social_signup_missing: translate('social_login_session_expired', 'Social login session expired. Please try again.'),
                    pending_sso_signup_missing: translate('sso_login_session_expired', 'SSO login session expired. Please try again.'),
                    terms_of_service_revision_mismatch: translate('terms_revision_mismatch_error', 'The Terms of Service changed. Review the latest version and try again.'),
                };
                if (typeof window.notifyError === 'function') {
                    window.notifyError(messages[detail] || translate(
                        pendingType === 'app' ? 'terms_of_service_notice_action_failed' : 'federated_terms_confirm_failed',
                        pendingType === 'app' ? 'Failed to update terms of service acceptance.' : 'Could not complete account creation. Please try again.'
                    ));
                }
                setConfirmPending(false);
                if (detail === 'pending_social_signup_missing' || detail === 'pending_sso_signup_missing') {
                    closePendingTermsModal({ notify: false });
                }
                return;
            }

            if (pendingType === 'app') {
                window.location.href = appTermsReturnUrl || resolveReturnUrl();
                return;
            }

            const result = await response.clone().json().catch(() => null);
            // Native terms completion uses the configured app-associated
            // callback used by direct and 2FA-completed authentication.
            if (window.loginAuthFlowContext?.isTrustedNativeCallbackUrl?.(result?.native_callback_url)) {
                window.location.assign(result.native_callback_url);
                return;
            }
            if (
                pendingType === 'social'
                && (result?.status === 'otp_setup' || result?.status === 'otp_required_already_setup')
            ) {
                const provider = new URLSearchParams(window.location.search).get('provider');
                setConfirmPending(false);
                closePendingTermsModal({ notify: false });
                if (provider && window.socialLogin?.handleSocial2FAResult?.(result, provider)) {
                    return;
                }
            }

            window.location.href = '/';
        } catch (error) {
            setConfirmPending(false);
            if (typeof window.notifyError === 'function') {
                window.notifyError(translate(
                    pendingType === 'app' ? 'terms_of_service_notice_action_failed' : 'federated_terms_confirm_failed',
                    pendingType === 'app' ? 'Failed to update terms of service acceptance.' : 'Could not complete account creation. Please try again.'
                ));
            }
        }
    }

    async function cancelPendingTerms() {
        if (isConfirmPending()) {
            return;
        }
        if (pendingType === 'app') {
            return;
        }
        if (pendingType) {
            try {
                await postPendingTerms(`/api/v1/auth/${pendingType}/pending-terms/cancel`);
            } catch (error) {
                // Best-effort cleanup; the server-side pending cookie also expires quickly.
            }
        }
        closePendingTermsModal({ notify: true });
    }

    function closePendingTermsModal({ notify = false } = {}) {
        if (isConfirmPending()) {
            return false;
        }
        setOverlayActiveState(false, {
            fallbackFocus: () => document.getElementById('signinEmail'),
        });
        pendingType = '';
        appTermsPolicy = null;
        appTermsReturnUrl = '';
        resetLoginCallbackUrl();
        if (notify && typeof window.notifyInfo === 'function') {
            window.notifyInfo(translate('federated_terms_cancelled', 'Account creation was cancelled.'));
        }
        return true;
    }

    function showPendingTermsModal(type) {
        const overlay = document.getElementById('federatedTermsOverlay');
        const confirmButton = document.getElementById('federatedTermsConfirmButton');
        const cancelButton = document.getElementById('federatedTermsCancelButton');
        if (!overlay || !confirmButton || !cancelButton) {
            return false;
        }

        pendingType = type;
        setConfirmPending(false);
        configureModalCopy(type);
        setOverlayActiveState(true, {
            initialFocus: confirmButton,
            fallbackFocus: () => document.getElementById('signinEmail'),
            dismiss: cancelPendingTerms,
            canDismiss: () => pendingType !== 'app' && !isConfirmPending(),
        });
        return true;
    }

    function showAppTermsModal(policy, returnUrl = '') {
        appTermsPolicy = policy || getCurrentAppTermsPolicy();
        if (!appTermsPolicy) {
            return false;
        }
        appTermsReturnUrl = returnUrl || resolveReturnUrl();
        return showPendingTermsModal('app');
    }

    document.addEventListener('DOMContentLoaded', function() {
        const confirmButton = document.getElementById('federatedTermsConfirmButton');
        const cancelButton = document.getElementById('federatedTermsCancelButton');
        const closeButton = document.getElementById('federatedTermsCloseButton');
        const overlay = document.getElementById('federatedTermsOverlay');

        confirmButton?.addEventListener('click', confirmPendingTerms);
        cancelButton?.addEventListener('click', cancelPendingTerms);
        closeButton?.addEventListener('click', cancelPendingTerms);
        overlay?.addEventListener('click', (event) => {
            if (event.target === overlay && pendingType !== 'app' && !isConfirmPending()) {
                cancelPendingTerms();
            }
        });
        const type = parsePendingType();
        if (type) {
            showPendingTermsModal(type);
            return;
        }

        const policy = getCurrentAppTermsPolicy();
        if (shouldShowAppTermsModal(policy)) {
            showAppTermsModal(policy);
        }
    });

    window.addEventListener('auth:termsAcceptanceRequired', (event) => {
        if (parsePendingType()) {
            return;
        }
        const policy = event?.detail?.policy || getCurrentAppTermsPolicy();
        const returnUrl = event?.detail?.returnUrl || '';
        if (shouldShowAppTermsModal(policy)) {
            showAppTermsModal(policy, returnUrl);
        }
    });

    window.federatedTermsSignup = {
        showPendingTermsModal,
        showAppTermsModal,
        confirmPendingTerms,
        cancelPendingTerms,
    };
})();
