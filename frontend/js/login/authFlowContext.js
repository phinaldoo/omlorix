(function() {
    'use strict';

    function readCookie(name) {
        const prefix = `${name}=`;
        return document.cookie.split(';').map((entry) => entry.trim()).find((entry) => entry.startsWith(prefix))?.slice(prefix.length) || '';
    }

    function getAuthContextPayload() {
        const replaceSlot = typeof window.getRequestedReplacementSlot === 'function'
            ? window.getRequestedReplacementSlot()
            : null;
        const returnUrl = typeof window.getAccountReturnUrl === 'function'
            ? window.getAccountReturnUrl()
            : '';
        const termsPayload = typeof window.getTermsOfServiceAcceptancePayload === 'function'
            ? window.getTermsOfServiceAcceptancePayload()
            : {};
        return {
            account_mode: typeof window.isAddAccountMode === 'function' && window.isAddAccountMode() ? 'add' : 'primary',
            ...(replaceSlot && { replace_slot: replaceSlot }),
            ...(returnUrl && { return_url: returnUrl }),
            ...termsPayload,
        };
    }

    function resetLoginCallbackUrl() {
        const replaceSlot = typeof window.getRequestedReplacementSlot === 'function'
            ? window.getRequestedReplacementSlot()
            : null;
        const returnUrl = typeof window.getAccountReturnUrl === 'function'
            ? window.getAccountReturnUrl()
            : '';
        const target = typeof window.buildLoginUrl === 'function'
            ? window.buildLoginUrl({
                mode: typeof window.isAddAccountMode === 'function' && window.isAddAccountMode() ? 'add' : '',
                returnUrl,
                replaceSlot,
            })
            : '/login';
        window.history.replaceState({}, '', target);
    }

    function notifyAuthError(message) {
        if (typeof window.notifyError === 'function' && message) {
            window.notifyError(message);
        }
    }

    function sanitizeLoginCallbackError(error, options = {}) {
        const fallback = options.fallback || 'Unknown error';
        if (typeof window.sanitizeLoginCallbackError === 'function') {
            return window.sanitizeLoginCallbackError(error, options);
        }
        if (typeof window.sanitizeSocialLoginError === 'function') {
            return window.sanitizeSocialLoginError(error, options);
        }
        return fallback;
    }

    function getLoginCallbackErrorMessage(options = {}) {
        const {
            error,
            errorMessages = {},
            formatTranslate,
            unknownKey,
            unknownFallback,
            unknownDetailFallback,
            knownMessages,
        } = options;

        if (Object.prototype.hasOwnProperty.call(errorMessages, error)) {
            return errorMessages[error];
        }

        const safeError = sanitizeLoginCallbackError(error, {
            fallback: unknownDetailFallback,
            knownMessages,
        });
        const formatter = typeof formatTranslate === 'function'
            ? formatTranslate
            : (_key, fallback, vars = {}) => String(fallback || '').replace(/\{(\w+)\}/g, (_, token) => {
                const value = vars[token];
                return value === undefined || value === null ? '' : String(value);
            });

        return formatter(unknownKey, unknownFallback, { error: safeError });
    }

    function renderLoginCallbackError(options = {}) {
        let message = getLoginCallbackErrorMessage(options);
        const rawReference = String(options.reference || '').trim();
        // Support references are URL-controlled on the callback page. Accept
        // only the bounded machine-token format emitted by the backend before
        // placing the value in a trusted notification.
        const reference = /^[A-Za-z0-9-]{1,64}$/.test(rawReference) ? rawReference : '';
        if (reference) {
            const formatter = typeof options.formatTranslate === 'function'
                ? options.formatTranslate
                : (_key, fallback, vars = {}) => String(fallback || '').replace('{reference}', vars.reference || '');
            message = `${message} ${formatter(
                'auth_error_reference',
                'Reference: {reference}',
                { reference },
            )}`;
        }
        notifyAuthError(message);
        resetLoginCallbackUrl();
        return true;
    }

    async function parseJsonResponse(response) {
        try {
            return await response.json();
        } catch (error) {
            return {};
        }
    }

    async function handleCrossSiteBlock(response) {
        return typeof window.handleCrossSiteRequestBlock === 'function'
            ? window.handleCrossSiteRequestBlock(response)
            : false;
    }

    function isTrustedNativeCallbackUrl(value) {
        if (typeof value !== 'string' || value.length > 4096) return false;
        try {
            const callback = new URL(value);
            if (
                callback.protocol !== 'https:'
                || callback.username
                || callback.password
                || callback.search
                || !callback.hash
                || !['/auth/federated', '/auth/link'].includes(callback.pathname)
            ) {
                return false;
            }
            const parameters = new URLSearchParams(callback.hash.slice(1));
            const state = parameters.get('state') || '';
            if (!/^[A-Za-z0-9_-]{43,128}$/.test(state)) return false;
            const allowedParameters = new Set(['state', 'code', 'provider', 'status', 'reason']);
            return Array.from(parameters.keys()).every((key) => allowedParameters.has(key));
        } catch (error) {
            return false;
        }
    }

    function getAuthRedirectLabelTarget(button) {
        if (!button || typeof button.querySelector !== 'function') {
            return null;
        }
        return button.querySelector('[data-auth-label]')
            || button.querySelector('span:not(.last-used-badge)')
            || button.querySelector('span');
    }

    // OAuth providers can return control without loading a fresh login document.
    // Safari and other browsers commonly restore this page from the back/forward
    // cache after a user cancels a provider sheet. Keep the handed-off buttons in
    // memory so those restored DOM nodes can be made interactive again.
    const handedOffAuthRedirectButtons = new Set();

    /**
     * Record that browser control has been handed to an external auth provider.
     * Buttons are registered only after the init request succeeds, so ordinary
     * focus or visibility changes during that request cannot cancel its spinner.
     */
    function markAuthRedirectHandedOff(button) {
        if (button) {
            handedOffAuthRedirectButtons.add(button);
        }
    }

    function setAuthRedirectButtonPendingState(button, isPending, pendingLabel = '') {
        if (!button) {
            return;
        }

        const labelTarget = getAuthRedirectLabelTarget(button);
        const stateStore = button.dataset || button;
        const currentLabel = labelTarget ? labelTarget.textContent : button.textContent;

        if (isPending) {
            if (stateStore.authOriginalLabel === undefined) {
                stateStore.authOriginalLabel = (currentLabel || '').trim();
            }
            if (stateStore.authRestoreFocus === undefined) {
                stateStore.authRestoreFocus = document.activeElement === button ? 'true' : 'false';
            }

            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            if (button.classList && typeof button.classList.add === 'function') {
                button.classList.add('loading');
            }

            const busyLabel = pendingLabel || stateStore.authOriginalLabel || '';
            if (labelTarget) {
                labelTarget.textContent = busyLabel;
            } else {
                button.textContent = busyLabel;
            }
            return;
        }

        handedOffAuthRedirectButtons.delete(button);
        button.disabled = false;
        button.removeAttribute('aria-busy');
        if (button.classList && typeof button.classList.remove === 'function') {
            button.classList.remove('loading');
        }

        if (stateStore.authOriginalLabel !== undefined) {
            if (labelTarget) {
                labelTarget.textContent = stateStore.authOriginalLabel || '';
            } else {
                button.textContent = stateStore.authOriginalLabel || '';
            }
            delete stateStore.authOriginalLabel;
        }

        const shouldRestoreFocus = stateStore.authRestoreFocus === 'true';
        delete stateStore.authRestoreFocus;
        if (shouldRestoreFocus && typeof button.focus === 'function') {
            try {
                button.focus({ preventScroll: true });
            } catch (error) {
                button.focus();
            }
        }
    }

    /**
     * Restore every button whose provider flow returned to this same document.
     * This is intentionally shared by social and enterprise login providers,
     * because both use the same external-redirect lifecycle.
     */
    function restoreHandedOffAuthRedirectButtons() {
        Array.from(handedOffAuthRedirectButtons).forEach((button) => {
            setAuthRedirectButtonPendingState(button, false);
        });
    }

    /**
     * Install page-resume hooks for provider cancellation behavior across browsers.
     * `pageshow` covers back/forward-cache restores, while visibility and focus
     * cover browser or operating-system authentication sheets that close in place.
     */
    function installAuthRedirectResumeHandlers() {
        if (typeof window.addEventListener === 'function') {
            window.addEventListener('pageshow', restoreHandedOffAuthRedirectButtons);
            window.addEventListener('focus', restoreHandedOffAuthRedirectButtons);
        }
        if (typeof document.addEventListener === 'function') {
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') {
                    restoreHandedOffAuthRedirectButtons();
                }
            });
        }
    }

    installAuthRedirectResumeHandlers();

    async function initiateAuthRedirect(options) {
        const {
            endpoint,
            payload,
            stateStorageKey,
            loginMethod,
            initFailureMessage,
            connectionFailureMessage,
            logLabel,
            pendingButton,
            pendingLabel,
        } = options || {};

        setAuthRedirectButtonPendingState(pendingButton, true, pendingLabel);

        try {
            if (loginMethod && window.loginMethodTracker) {
                window.loginMethodTracker.saveLastUsedLoginMethod(loginMethod);
            }

            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload || {}),
            });

            if (!response.ok) {
                if (await handleCrossSiteBlock(response)) {
                    setAuthRedirectButtonPendingState(pendingButton, false);
                    return null;
                }
                const error = await parseJsonResponse(response);
                setAuthRedirectButtonPendingState(pendingButton, false);
                notifyAuthError(error.detail || initFailureMessage);
                return null;
            }

            const data = await response.json();
            if (stateStorageKey && data.state) {
                sessionStorage.setItem(stateStorageKey, data.state);
            }
            if (data.authorization_url) {
                markAuthRedirectHandedOff(pendingButton);
                window.location.href = data.authorization_url;
                return data;
            }
            setAuthRedirectButtonPendingState(pendingButton, false);
            notifyAuthError(initFailureMessage);
            return null;
        } catch (error) {
            if (logLabel) {
                console.error(`${logLabel}:`, error);
            }
            setAuthRedirectButtonPendingState(pendingButton, false);
            notifyAuthError(connectionFailureMessage || initFailureMessage);
            return null;
        }
    }

    function resolvePostAuthRedirect(result) {
        return typeof window.resolvePostAuthRedirect === 'function'
            ? window.resolvePostAuthRedirect(result)
            : '/';
    }

    async function exchangeAuthCode(options) {
        const {
            endpoint,
            code,
            logPrefix,
            failureRedirectUrl,
            failureNotifyMessage,
            resetUrlOnFailure = false,
        } = options || {};

        try {
            const payload = code ? { code } : {};
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                console.error(`${logPrefix} Code exchange failed`);
                if (await handleCrossSiteBlock(response)) {
                    return null;
                }
                if (resetUrlOnFailure) {
                    resetLoginCallbackUrl();
                }
                if (failureNotifyMessage) {
                    notifyAuthError(failureNotifyMessage);
                } else if (failureRedirectUrl) {
                    window.location.href = failureRedirectUrl;
                }
                return { success: false, handled: true };
            }

            const data = await response.json();
            resetLoginCallbackUrl();
            window.location.href = resolvePostAuthRedirect(data);
            return { success: true, handled: true, data };
        } catch (error) {
            console.error(`${logPrefix} Code exchange error:`, error);
            if (resetUrlOnFailure) {
                resetLoginCallbackUrl();
            }
            if (failureNotifyMessage) {
                notifyAuthError(failureNotifyMessage);
            } else if (failureRedirectUrl) {
                window.location.href = failureRedirectUrl;
            }
            return { success: false, handled: true };
        }
    }

    window.loginAuthFlowContext = {
        readCookie,
        getAuthContextPayload,
        resetLoginCallbackUrl,
        notifyAuthError,
        getLoginCallbackErrorMessage,
        renderLoginCallbackError,
        setAuthRedirectButtonPendingState,
        restoreHandedOffAuthRedirectButtons,
        isTrustedNativeCallbackUrl,
        initiateAuthRedirect,
        exchangeAuthCode,
        resolvePostAuthRedirect,
    };
})();
