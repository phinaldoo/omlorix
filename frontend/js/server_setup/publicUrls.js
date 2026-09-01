// Public URL list editor and the setup lockout warning dialog.
(function initializePublicUrlModule(global) {
    'use strict';

    const MAX_PUBLIC_URL_LENGTH = 2048;
    let warningResolver = null;
    let warningReturnFocus = null;

    /**
     * Translate and interpolate a server-setup message without assuming that
     * the language module has already finished its asynchronous initialization.
     */
    function translate(key, fallback, values = {}) {
        let message = fallback;
        if (typeof global.getTranslation === 'function') {
            message = global.getTranslation(key, fallback);
        }
        return Object.entries(values).reduce(
            (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
            message
        );
    }

    /**
     * Match the backend's normalization: accept HTTP(S), repair a commonly
     * omitted scheme colon, reject credentials, and retain only the origin.
     */
    function normalizePublicUrl(value) {
        let candidate = typeof value === 'string' ? value.trim() : '';
        if (!candidate) {
            throw new Error('required');
        }
        candidate = candidate.replace(/^(https?)\/\//i, '$1://');
        if (candidate.length > MAX_PUBLIC_URL_LENGTH) {
            throw new Error('invalid');
        }

        let parsed;
        try {
            parsed = new URL(candidate);
        } catch (_error) {
            throw new Error('invalid');
        }

        if (
            !['http:', 'https:'].includes(parsed.protocol)
            || !parsed.hostname
            || parsed.username
            || parsed.password
        ) {
            throw new Error('invalid');
        }
        return parsed.origin;
    }

    /**
     * Validate every visible row and detect duplicates after normalization.
     * The returned shape plugs directly into the setup's field-error system.
     */
    function validatePublicUrls(values) {
        if (!Array.isArray(values) || values.length === 0) {
            return {
                valid: false,
                messageKey: 'error_public_url_required',
                fallback: 'Add at least one public URL to continue.'
            };
        }

        const normalized = [];
        const seen = new Set();
        for (let index = 0; index < values.length; index += 1) {
            let publicUrl;
            try {
                publicUrl = normalizePublicUrl(values[index]);
            } catch (_error) {
                return {
                    valid: false,
                    messageKey: null,
                    fallback: translate(
                        'error_public_url_invalid',
                        'Public URL {position} must be a full HTTP or HTTPS URL.',
                        { position: index + 1 }
                    )
                };
            }
            if (seen.has(publicUrl)) {
                return {
                    valid: false,
                    messageKey: 'error_public_url_duplicate',
                    fallback: 'Each public URL must be unique.'
                };
            }
            seen.add(publicUrl);
            normalized.push(publicUrl);
        }
        return { valid: true, urls: normalized };
    }

    /** Return whether the browser's exact origin is represented in the list. */
    function isOriginConfigured(values, currentOrigin) {
        let normalizedOrigin;
        try {
            normalizedOrigin = normalizePublicUrl(currentOrigin);
        } catch (_error) {
            return false;
        }
        const result = validatePublicUrls(values);
        return result.valid && result.urls.includes(normalizedOrigin);
    }

    /**
     * Build an absolute post-setup destination on the canonical public URL.
     * Authentication helpers only return local paths, but the defensive check
     * below prevents an unexpected external return value becoming a redirect.
     */
    function buildRedirectUrl(primaryPublicUrl, returnPath = '/') {
        const primaryOrigin = normalizePublicUrl(primaryPublicUrl);
        const safePath = typeof returnPath === 'string'
            && returnPath.startsWith('/')
            && !returnPath.startsWith('//')
            ? returnPath
            : '/';
        const resolvedUrl = new URL(safePath, `${primaryOrigin}/`);

        // URL parsing treats backslashes like path separators, so a value such
        // as `/\evil.example/path` can resolve to a different host even though
        // it passed the initial local-path checks.
        return resolvedUrl.origin === primaryOrigin
            ? resolvedUrl.href
            : `${primaryOrigin}/`;
    }

    function createIconButton(icon, label, action, disabled = false) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'public-url-icon-btn';
        button.setAttribute('aria-label', label);
        button.title = label;
        button.disabled = disabled;
        button.dataset.action = action;
        button.innerHTML = icon || '';
        button.querySelector('svg')?.setAttribute('aria-hidden', 'true');
        button.querySelector('svg')?.setAttribute('focusable', 'false');
        return button;
    }

    /** Read the shared icon registry, which is a global lexical binding. */
    function getIcon(name) {
        return typeof Icons !== 'undefined' ? Icons[name] : '';
    }

    /** Render from state so add/remove/reorder operations remain deterministic. */
    function renderPublicUrlEditor(focusIndex = null) {
        const list = document.getElementById('publicUrlList');
        if (!list || !global.state) {
            return;
        }
        list.replaceChildren();

        global.state.serverData.publicUrls.forEach((value, index) => {
            const row = document.createElement('div');
            row.className = 'public-url-row';

            const inputGroup = document.createElement('div');
            inputGroup.className = 'public-url-input-group';

            const input = document.createElement('input');
            input.type = 'url';
            input.className = 'input public-url-input';
            input.value = value;
            input.autocomplete = 'url';
            input.required = true;
            input.setAttribute('aria-required', 'true');
            input.setAttribute('aria-describedby', 'publicUrlHint publicUrlError');
            input.setAttribute(
                'aria-label',
                translate(
                    'public_url_input_aria',
                    'Public URL {position}',
                    { position: index + 1 }
                )
            );
            input.addEventListener('input', (event) => {
                global.state.serverData.publicUrls[index] = event.target.value;
                global.updateValidation?.();
            });
            inputGroup.appendChild(input);

            if (index === 0) {
                const primaryBadge = document.createElement('span');
                primaryBadge.className = 'public-url-primary-badge';
                primaryBadge.textContent = translate(
                    'public_url_primary_badge',
                    'Primary'
                );
                inputGroup.appendChild(primaryBadge);
            }

            const actions = document.createElement('div');
            actions.className = 'public-url-row-actions';
            actions.append(
                createIconButton(
                    getIcon('chevronTop'),
                    translate('public_url_move_up_aria', 'Move URL up'),
                    'move-up',
                    index === 0
                ),
                createIconButton(
                    getIcon('chevron'),
                    translate('public_url_move_down_aria', 'Move URL down'),
                    'move-down',
                    index === global.state.serverData.publicUrls.length - 1
                ),
                createIconButton(
                    getIcon('close'),
                    translate('public_url_remove_aria', 'Remove URL'),
                    'remove',
                    global.state.serverData.publicUrls.length === 1
                )
            );
            actions.addEventListener('click', (event) => {
                const button = event.target.closest('button[data-action]');
                if (!button || button.disabled) {
                    return;
                }
                mutatePublicUrlList(index, button.dataset.action);
            });

            row.append(inputGroup, actions);
            list.appendChild(row);
        });

        if (Number.isInteger(focusIndex)) {
            list.querySelectorAll('.public-url-input')[focusIndex]?.focus();
        }
    }

    /** Apply one list operation and then render the resulting ordered list. */
    function mutatePublicUrlList(index, action) {
        const values = global.state.serverData.publicUrls;
        let nextFocusIndex = index;
        if (action === 'remove' && values.length > 1) {
            values.splice(index, 1);
            nextFocusIndex = Math.min(index, values.length - 1);
        } else if (action === 'move-up' && index > 0) {
            [values[index - 1], values[index]] = [values[index], values[index - 1]];
            nextFocusIndex = index - 1;
        } else if (action === 'move-down' && index < values.length - 1) {
            [values[index + 1], values[index]] = [values[index], values[index + 1]];
            nextFocusIndex = index + 1;
        }
        renderPublicUrlEditor(nextFocusIndex);
        global.updateValidation?.();
    }

    /** Initialize the editor with the address used to open setup. */
    function initializeEditor() {
        if (!global.state) {
            return;
        }
        if (!Array.isArray(global.state.serverData.publicUrls)
            || global.state.serverData.publicUrls.length === 0) {
            global.state.serverData.publicUrls = [global.location.origin];
        }

        document.getElementById('addPublicUrlButton')?.addEventListener('click', () => {
            global.state.serverData.publicUrls.push('');
            renderPublicUrlEditor(global.state.serverData.publicUrls.length - 1);
            global.updateValidation?.();
        });
        const addIcon = document.querySelector('#addPublicUrlButton [aria-hidden="true"]');
        if (addIcon) {
            addIcon.innerHTML = getIcon('plus');
        }
        initializeWarningDialog();
        renderPublicUrlEditor();
    }

    /** Normalize state after validation, keeping the canonical order visible. */
    function commitNormalizedValues() {
        const result = validatePublicUrls(global.state?.serverData?.publicUrls);
        if (!result.valid) {
            return false;
        }
        global.state.serverData.publicUrls = result.urls;
        renderPublicUrlEditor();
        return true;
    }

    function getFocusableDialogElements(dialog) {
        return Array.from(dialog.querySelectorAll('button:not(:disabled), [href], input:not(:disabled)'));
    }

    function closeWarningDialog(continueSetup) {
        const overlay = document.getElementById('currentOriginWarning');
        const main = document.getElementById('setupMain');
        if (!overlay || !warningResolver) {
            return;
        }
        overlay.classList.remove('active');
        overlay.setAttribute('aria-hidden', 'true');
        overlay.inert = true;
        if (main) {
            main.inert = false;
        }
        const resolve = warningResolver;
        warningResolver = null;
        warningReturnFocus?.focus();
        warningReturnFocus = null;
        resolve(continueSetup);
    }

    /** Wire focus containment and both explicit outcomes for the warning. */
    function initializeWarningDialog() {
        const overlay = document.getElementById('currentOriginWarning');
        if (!overlay || overlay.dataset.initialized === 'true') {
            return;
        }
        overlay.dataset.initialized = 'true';
        overlay.inert = true;
        document.getElementById('addCurrentOriginButton')?.addEventListener('click', () => {
            const origin = global.location.origin;
            if (!isOriginConfigured(global.state.serverData.publicUrls, origin)) {
                global.state.serverData.publicUrls.push(origin);
                renderPublicUrlEditor(global.state.serverData.publicUrls.length - 1);
                global.updateValidation?.();
            }
            closeWarningDialog(false);
        });
        document.getElementById('continueWithoutCurrentOriginButton')?.addEventListener(
            'click',
            () => closeWarningDialog(true)
        );
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) {
                closeWarningDialog(false);
            }
        });
        overlay.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeWarningDialog(false);
                return;
            }
            if (event.key !== 'Tab') {
                return;
            }
            const focusable = getFocusableDialogElements(overlay);
            if (focusable.length === 0) {
                event.preventDefault();
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });
    }

    /**
     * Ask for an explicit choice before leaving the URL step when continuing
     * would make the setup address unavailable after activation.
     */
    function confirmUnlistedCurrentOrigin() {
        if (isOriginConfigured(global.state.serverData.publicUrls, global.location.origin)) {
            return Promise.resolve(true);
        }
        const overlay = document.getElementById('currentOriginWarning');
        const main = document.getElementById('setupMain');
        const message = document.getElementById('currentOriginWarningMessage');
        if (!overlay || warningResolver) {
            return Promise.resolve(false);
        }
        message.textContent = translate(
            'current_origin_warning_message',
            'You are currently accessing Omlorix through {currentOrigin}. After setup, browser requests from this address will be blocked because it is not in the configured public URL list.',
            { currentOrigin: global.location.origin }
        );
        warningReturnFocus = document.activeElement;
        overlay.inert = false;
        overlay.classList.add('active');
        overlay.setAttribute('aria-hidden', 'false');
        if (main) {
            main.inert = true;
        }
        document.getElementById('addCurrentOriginButton')?.focus();
        return new Promise((resolve) => {
            warningResolver = resolve;
        });
    }

    global.serverSetupPublicUrls = {
        normalizePublicUrl,
        validatePublicUrls,
        isOriginConfigured,
        buildRedirectUrl,
        initializeEditor,
        renderPublicUrlEditor,
        commitNormalizedValues,
        confirmUnlistedCurrentOrigin
    };
})(window);
