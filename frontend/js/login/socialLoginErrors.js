(function(root) {
    'use strict';

    const DEFAULT_SAFE_ERROR = 'Unknown error';
    const DEFAULT_KNOWN_MESSAGES = {
        access_denied: 'Access was denied.',
        invalid_request: 'Invalid login request.',
        invalid_state: 'Invalid login session.',
        server_error: 'Authentication service error.',
    };

    function normalizeErrorCode(error) {
        return String(error || '')
            .trim()
            .toLowerCase()
            .replace(/[\s-]+/g, '_')
            .replace(/[^a-z0-9_]/g, '');
    }

    function sanitizeLoginCallbackError(error, options = {}) {
        const fallback = options.fallback || DEFAULT_SAFE_ERROR;
        const knownMessages = {
            ...DEFAULT_KNOWN_MESSAGES,
            ...(options.knownMessages || {}),
        };
        const normalized = normalizeErrorCode(error);

        if (Object.prototype.hasOwnProperty.call(knownMessages, normalized)) {
            return knownMessages[normalized];
        }

        return fallback;
    }

    function sanitizeSocialLoginError(error, options = {}) {
        return sanitizeLoginCallbackError(error, options);
    }

    function handleLoginCallbackAccountState(error, options = {}) {
        const normalized = normalizeErrorCode(error);
        const resetLoginCallbackUrl = typeof options.resetLoginCallbackUrl === 'function'
            ? options.resetLoginCallbackUrl
            : () => {};

        if (normalized === 'account_pending') {
            if (typeof root.showPendingNotification === 'function') {
                root.showPendingNotification();
                resetLoginCallbackUrl();
                return true;
            }
            return false;
        }

        if (normalized === 'account_inactive') {
            if (typeof root.showInactiveAccountWarning === 'function') {
                root.showInactiveAccountWarning();
                resetLoginCallbackUrl();
                return true;
            }
            return false;
        }

        if (normalized === 'account_locked') {
            if (typeof root.showLoginAccountLockWarning === 'function') {
                root.showLoginAccountLockWarning(options.lock || {});
                resetLoginCallbackUrl();
                return true;
            }
            return false;
        }

        if (normalized === 'ipban' || normalized === 'ip_ban') {
            if (typeof root.showWarning === 'function') {
                const translate = typeof root.getTranslation === 'function'
                    ? root.getTranslation
                    : (_key, fallback) => fallback;
                root.showWarning(
                    Number(options.expires || 0),
                    translate('ip_ban_title', 'Your IP has been banned'),
                    translate('ip_ban_message', 'Your IP has been temporarily banned due to <strong>various security reasons</strong>. This is a security measure to ensure the safety and integrity of our services.'),
                );
                resetLoginCallbackUrl();
                return true;
            }
            return false;
        }

        if (normalized === 'access_time_blocked') {
            if (typeof root.showAccessBlockedOverlay === 'function') {
                root.showAccessBlockedOverlay(options.accessBlocked || {});
                resetLoginCallbackUrl();
                return true;
            }
            return false;
        }

        return false;
    }

    root.sanitizeLoginCallbackError = sanitizeLoginCallbackError;
    root.sanitizeSocialLoginError = sanitizeSocialLoginError;
    root.handleLoginCallbackAccountState = handleLoginCallbackAccountState;

    if (typeof module === 'object' && module.exports) {
        module.exports = {
            sanitizeLoginCallbackError,
            sanitizeSocialLoginError,
            handleLoginCallbackAccountState,
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
