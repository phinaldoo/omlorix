(function () {
    'use strict';

    const BLOCKED_CLASS = 'cross-site-login-blocked';
    let isRendered = false;

    function translate(key, fallback) {
        return typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;
    }

    function normalizeDetail(detail) {
        if (typeof detail === 'string') {
            return detail.trim();
        }
        if (detail && typeof detail.detail === 'string') {
            return detail.detail.trim();
        }
        return '';
    }

    function isCrossSiteRequestBlockDetail(detail) {
        return normalizeDetail(detail).toLowerCase().startsWith('cross-site request blocked');
    }

    function setElementText(id, key, fallback) {
        const element = document.getElementById(id);
        if (!element) {
            return;
        }
        element.textContent = translate(key, fallback);
    }

    function updateCopy() {
        setElementText(
            'crossSiteRequestBlockedTitle',
            'cross_site_request_blocked_title',
            'Cross-site request blocked',
        );
        setElementText(
            'crossSiteRequestBlockedMessage',
            'cross_site_request_blocked_message',
            'Omlorix blocked this sign-in request because the page origin does not match the configured public URL.',
        );
        setElementText(
            'crossSiteRequestBlockedHelp',
            'cross_site_request_blocked_help',
            'Open Omlorix from the configured public URL, or ask an administrator to update Settings > General > Public URL for this address.',
        );

    }

    function revealBlockedState() {
        const state = document.getElementById('crossSiteRequestBlockedState');
        if (!state || !document.body) {
            return false;
        }

        updateCopy();
        state.hidden = false;
        // This class makes the dedicated warning the only visible body child, so
        // partially initialized forms, menus, banners, and toasts cannot compete
        // with the security-critical state.
        document.body.classList.add(BLOCKED_CLASS);

        requestAnimationFrame(() => {
            if (typeof state.focus === 'function') {
                state.focus({ preventScroll: true });
            }
        });
        isRendered = true;
        return true;
    }

    function showCrossSiteRequestBlocked(detail = '') {
        // The backend detail is used only to identify this state. Do not expose
        // operational configuration hints in the public login page; the short,
        // translated guidance below is enough for users and administrators.
        normalizeDetail(detail);
        // Some OAuth/SSO callback scripts run in <head> before the login body is
        // parsed. Queue the render so early cross-site failures still become the
        // same full-page warning once the DOM exists.
        if (document.readyState === 'loading' || !document.body) {
            document.addEventListener('DOMContentLoaded', () => {
                revealBlockedState();
            }, { once: true });
            return true;
        }
        return revealBlockedState();
    }

    async function getCrossSiteBlockDetailFromResponse(response) {
        if (!response || response.status !== 403) {
            return '';
        }

        // Clone before reading so callers can still parse unrelated 403 bodies
        // through their existing error handling when this is not the CSRF guard.
        const responseClone = typeof response.clone === 'function' ? response.clone() : response;
        let payload = null;
        try {
            payload = await responseClone.json();
        } catch (_error) {
            try {
                payload = await responseClone.text();
            } catch (_textError) {
                payload = '';
            }
        }

        const detail = normalizeDetail(payload);
        return isCrossSiteRequestBlockDetail(detail) ? detail : '';
    }

    async function handleCrossSiteRequestBlock(response) {
        const detail = await getCrossSiteBlockDetailFromResponse(response);
        if (!detail) {
            return false;
        }
        showCrossSiteRequestBlocked(detail);
        return true;
    }

    document.addEventListener('i18n:updated', () => {
        if (isRendered) {
            updateCopy();
        }
    });

    window.isCrossSiteRequestBlockDetail = isCrossSiteRequestBlockDetail;
    window.showCrossSiteRequestBlocked = showCrossSiteRequestBlocked;
    window.handleCrossSiteRequestBlock = handleCrossSiteRequestBlock;
})();
