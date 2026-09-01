(function initializeSignInMethodsSettings() {
    'use strict';

    const listId = 'socialSignInMethodsList';
    const managedInertAttribute = 'data-social-link-managed-inert';
    let methodsState = null;
    let pendingDisconnectProvider = null;
    let disconnectTrigger = null;

    function t(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function formatT(key, fallback, variables) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, variables);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_match, token) => (
            Object.prototype.hasOwnProperty.call(variables || {}, token)
                ? String(variables[token] ?? '')
                : ''
        ));
    }

    async function responseDetail(response) {
        const payload = await response.json().catch(() => null);
        return payload?.detail || '';
    }

    function providerIcon(provider) {
        const icon = typeof Icons === 'object' && Icons
            ? (Icons[provider] || Icons.user)
            : '';
        const container = document.createElement('span');
        container.className = 'sign-in-provider-icon';
        container.setAttribute('aria-hidden', 'true');
        container.innerHTML = icon || '';
        return container;
    }

    function providerDescription(method) {
        if (method.linked && method.account_hint) {
            return formatT(
                'us_sign_in_provider_connected_as',
                'Connected as {account}',
                { account: method.account_hint },
            );
        }
        if (method.linked && !method.available) {
            return t(
                'us_sign_in_provider_unavailable_desc',
                'Connected, but this provider is currently unavailable for sign-in.',
            );
        }
        return formatT(
            'us_sign_in_provider_desc',
            'Use {provider} to sign in to this account.',
            { provider: method.label },
        );
    }

    function renderMethods() {
        const list = document.getElementById(listId);
        if (!list) return;
        list.replaceChildren();
        list.setAttribute('aria-busy', 'false');

        const methods = (methodsState?.providers || []).filter((method) => (
            method.linked || method.available
        ));
        // Keep the entire social-provider surface out of the layout when the
        // server has no relevant providers. The element starts hidden in the
        // markup as well, which avoids briefly flashing its loading state.
        list.hidden = methods.length === 0;
        if (!methods.length) {
            return;
        }

        methods.forEach((method) => {
            const row = document.createElement('div');
            row.className = 'us-setting-item sign-in-provider-row';
            row.dataset.provider = method.provider;

            const identity = document.createElement('div');
            identity.className = 'sign-in-provider-identity';
            identity.appendChild(providerIcon(method.provider));

            const info = document.createElement('div');
            info.className = 'us-setting-info';
            const titleLine = document.createElement('div');
            titleLine.className = 'sign-in-provider-title-line';
            const title = document.createElement('h3');
            title.textContent = method.label;
            titleLine.appendChild(title);

            if (method.linked) {
                const status = document.createElement('span');
                status.className = method.available
                    ? 'sign-in-provider-badge sign-in-provider-badge--connected'
                    : 'sign-in-provider-badge sign-in-provider-badge--unavailable';
                status.textContent = method.available
                    ? t('us_sign_in_connected', 'Connected')
                    : t('us_sign_in_unavailable', 'Unavailable');
                titleLine.appendChild(status);
            }

            const description = document.createElement('p');
            description.textContent = providerDescription(method);
            info.append(titleLine, description);

            if (method.linked && !method.can_unlink) {
                const help = document.createElement('p');
                help.className = 'sign-in-provider-help';
                help.id = `social-unlink-help-${method.provider}`;
                help.textContent = t(
                    'us_sign_in_last_method_help',
                    'Set a password, add a passkey, or connect another provider before disconnecting this method.',
                );
                info.appendChild(help);
            }
            identity.appendChild(info);

            const action = document.createElement('button');
            action.type = 'button';
            action.className = method.linked ? 'om-button border danger-nofill' : 'om-button border';
            action.dataset.socialAction = method.linked ? 'disconnect' : 'connect';
            action.dataset.provider = method.provider;
            action.textContent = method.linked
                ? t('us_sign_in_disconnect', 'Disconnect')
                : t('us_sign_in_connect', 'Connect');
            if (method.linked && !method.can_unlink) {
                action.disabled = true;
                action.setAttribute('aria-describedby', `social-unlink-help-${method.provider}`);
            } else if (!method.linked && !method.can_link) {
                action.disabled = true;
            }
            row.append(identity, action);
            list.appendChild(row);
        });
    }

    async function loadSignInMethods(options = {}) {
        const list = document.getElementById(listId);
        if (!list) return null;
        if (!options.silent) list.setAttribute('aria-busy', 'true');
        try {
            const response = await window.authedFetch('/api/v1/auth/sign-in-methods');
            if (!response.ok) throw new Error(await responseDetail(response));
            methodsState = await response.json();
            renderMethods();
            return methodsState;
        } catch (error) {
            console.error('Failed to load sign-in methods', error);
            list.setAttribute('aria-busy', 'false');
            list.replaceChildren();
            // A loading failure is actionable information, so reveal the
            // otherwise initially hidden surface before rendering the error.
            list.hidden = false;
            const message = document.createElement('p');
            message.className = 'sign-in-method-status sign-in-method-status--error';
            message.textContent = t(
                'us_sign_in_methods_load_error',
                'Sign-in methods could not be loaded. Please try again.',
            );
            list.appendChild(message);
            return null;
        }
    }

    async function requireStepUp() {
        if (typeof window.ensureSecurityStepUp !== 'function') return false;
        return window.ensureSecurityStepUp();
    }

    async function connectProvider(provider, button) {
        button.disabled = true;
        try {
            if (!await requireStepUp()) return;
            const response = await window.authedFetch(`/api/v1/auth/social/${encodeURIComponent(provider)}/link/init`, {
                method: 'POST',
            });
            if (!response.ok) throw new Error(await responseDetail(response));
            const payload = await response.json();
            if (!payload?.authorization_url) throw new Error('Missing authorization URL');
            window.location.assign(payload.authorization_url);
        } catch (error) {
            console.error('Failed to start social identity link', error);
            window.notifyError?.(t(
                'us_sign_in_action_error',
                'That sign-in method could not be updated. Please try again.',
            ));
        } finally {
            button.disabled = false;
        }
    }

    function ensureDisconnectDialog() {
        let overlay = document.getElementById('disconnectSocialIdentityOverlay');
        if (overlay) return overlay;

        overlay = document.createElement('div');
        overlay.id = 'disconnectSocialIdentityOverlay';
        overlay.className = 'delete-warning-overlay shared-modal-overlay';
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        overlay.innerHTML = `
            <div class="delete-warning-card shared-modal shared-modal--compact shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="disconnectSocialIdentityTitle" aria-describedby="disconnectSocialIdentityDescription" tabindex="-1">
                <header class="shared-modal-header shared-modal-header--main">
                    <h3 class="warning-header-title color-red shared-modal-title" id="disconnectSocialIdentityTitle"></h3>
                </header>
                <div class="shared-modal-body shared-modal-body--centered">
                    <p class="warning-message" id="disconnectSocialIdentityDescription"></p>
                </div>
                <footer class="warning-navigation shared-modal-footer">
                    <button class="om-button border cancel" id="disconnectSocialIdentityCancel" type="button"></button>
                    <button class="om-button border danger" id="disconnectSocialIdentityConfirm" type="button"></button>
                </footer>
            </div>
        `;
        document.body.appendChild(overlay);
        overlay.querySelector('#disconnectSocialIdentityCancel').textContent = t('common_cancel', 'Cancel');
        overlay.querySelector('#disconnectSocialIdentityConfirm').textContent = t('us_sign_in_disconnect_confirm', 'Disconnect');
        overlay.querySelector('#disconnectSocialIdentityCancel').addEventListener('click', closeDisconnectDialog);
        overlay.querySelector('#disconnectSocialIdentityConfirm').addEventListener('click', confirmDisconnect);
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) closeDisconnectDialog();
        });
        overlay.addEventListener('keydown', trapDisconnectDialogFocus);
        return overlay;
    }

    function trapDisconnectDialogFocus(event) {
        const overlay = event.currentTarget;
        if (event.key === 'Escape') {
            event.preventDefault();
            closeDisconnectDialog();
            return;
        }
        if (event.key !== 'Tab') return;
        const controls = [...overlay.querySelectorAll('button:not(:disabled)')];
        if (!controls.length) return;
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    function setBackgroundInert(activeOverlay) {
        // Keep assistive technology and keyboard users inside the active modal
        // without disturbing inert state owned by another application surface.
        [...document.body.children].forEach((child) => {
            if (child === activeOverlay) return;
            if (activeOverlay && !child.inert) {
                child.inert = true;
                child.setAttribute(managedInertAttribute, 'true');
            } else if (!activeOverlay && child.hasAttribute(managedInertAttribute)) {
                child.inert = false;
                child.removeAttribute(managedInertAttribute);
            }
        });
    }

    function openDisconnectDialog(provider, trigger) {
        const method = methodsState?.providers?.find((item) => item.provider === provider);
        if (!method?.can_unlink) return;
        pendingDisconnectProvider = provider;
        disconnectTrigger = trigger;
        const overlay = ensureDisconnectDialog();
        overlay.querySelector('#disconnectSocialIdentityTitle').textContent = formatT(
            'us_sign_in_disconnect_title',
            'Disconnect {provider}?',
            { provider: method.label },
        );
        overlay.querySelector('#disconnectSocialIdentityDescription').textContent = formatT(
            'us_sign_in_disconnect_desc',
            'You will no longer be able to sign in with {provider}. You can reconnect it later.',
            { provider: method.label },
        );
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
        setBackgroundInert(overlay);
        overlay.querySelector('#disconnectSocialIdentityCancel').focus();
    }

    function closeDisconnectDialog() {
        const overlay = document.getElementById('disconnectSocialIdentityOverlay');
        if (!overlay || overlay.hidden) return;
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('modal-open');
        setBackgroundInert(null);
        pendingDisconnectProvider = null;
        const trigger = disconnectTrigger;
        disconnectTrigger = null;
        trigger?.focus();
    }

    async function confirmDisconnect() {
        const provider = pendingDisconnectProvider;
        if (!provider) return;
        const overlay = ensureDisconnectDialog();
        const confirm = overlay.querySelector('#disconnectSocialIdentityConfirm');
        confirm.disabled = true;
        // Close before opening the step-up dialog so only one modal is active
        // and the step-up controls are never hidden behind an inert backdrop.
        closeDisconnectDialog();
        try {
            if (!await requireStepUp()) return;
            const response = await window.authedFetch(`/api/v1/auth/social/${encodeURIComponent(provider)}/link`, {
                method: 'DELETE',
            });
            if (!response.ok) throw new Error(await responseDetail(response));
            methodsState = await response.json();
            closeDisconnectDialog();
            renderMethods();
            window.notifySuccess?.(t(
                'us_sign_in_disconnect_success',
                'Sign-in method disconnected.',
            ));
        } catch (error) {
            console.error('Failed to disconnect social identity', error);
            window.notifyError?.(t(
                'us_sign_in_action_error',
                'That sign-in method could not be updated. Please try again.',
            ));
        } finally {
            confirm.disabled = false;
        }
    }

    function handleListClick(event) {
        const button = event.target.closest('[data-social-action]');
        if (!button || button.disabled) return;
        const provider = button.dataset.provider;
        if (button.dataset.socialAction === 'connect') {
            connectProvider(provider, button);
        } else {
            openDisconnectDialog(provider, button);
        }
    }

    function handleCallbackResult() {
        const url = new URL(window.location.href);
        const result = url.searchParams.get('social_link');
        if (!result) return;
        const reason = url.searchParams.get('reason');
        for (const key of ['social_link', 'provider', 'reason']) url.searchParams.delete(key);
        window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);

        // Opening the security page also reloads the authoritative server state.
        window.openUserSettings?.('security');
        if (result === 'success') {
            window.notifySuccess?.(t(
                'us_sign_in_connect_success',
                'Sign-in method connected.',
            ));
        } else if (reason === 'cancelled') {
            window.notifyError?.(t(
                'us_sign_in_link_cancelled',
                'Connecting the sign-in method was cancelled.',
            ));
        } else if (reason === 'conflict') {
            window.notifyError?.(t(
                'us_sign_in_link_conflict',
                'That provider account is already connected to another user.',
            ));
        } else {
            window.notifyError?.(t(
                'us_sign_in_link_failed',
                'The sign-in method could not be connected. Please try again.',
            ));
        }
    }

    function init() {
        document.getElementById(listId)?.addEventListener('click', handleListClick);
        handleCallbackResult();
    }

    window.loadSignInMethods = loadSignInMethods;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
