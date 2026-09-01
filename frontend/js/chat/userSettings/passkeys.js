// Passkey Management for User Settings
let activePasskeys = [];
let isPasskeySectionEnabled = false;

function passkeyT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function passkeyFormat(key, fallback, vars) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(passkeyT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => (
        vars?.[token] === undefined || vars?.[token] === null ? '' : String(vars[token])
    ));
}

function setPasskeyStatusMessage(message) {
    const passkeyListContainer = document.getElementById('passkeyListContainer');
    if (!passkeyListContainer) return;

    passkeyListContainer.textContent = '';
    const messageEl = document.createElement('p');
    messageEl.style.color = 'var(--text-color-secondary)';
    messageEl.style.padding = '15px 0';
    messageEl.textContent = message;
    passkeyListContainer.appendChild(messageEl);
}

function setPasskeySectionEnabled(enabled) {
    isPasskeySectionEnabled = Boolean(enabled);
    const passkeySection = document.getElementById('passkeySection');
    if (passkeySection) {
        passkeySection.style.display = isPasskeySectionEnabled ? '' : 'none';
    }
}

async function loadPasskeys() {
    const passkeySection = document.getElementById('passkeySection');
    const passkeyListContainer = document.getElementById('passkeyListContainer');
    
    if (!passkeySection || !passkeyListContainer) {
        return;
    }

    if (!isPasskeySectionEnabled) {
        setPasskeySectionEnabled(false);
        return;
    }

    passkeySection.style.display = '';

    try {
        const res = await window.authedFetch('/api/v1/auth/passkeys/list');
        
        if (res.ok) {
            const data = await res.json();
            activePasskeys = data.passkeys || [];
            renderPasskeyList();
        } else {
            if (res.status === 401 || res.status === 403) {
                redirectToLogin();
            } else {
                setPasskeyStatusMessage(passkeyT('us_security_passkeys_error_load', 'Failed to load passkeys.'));
            }
        }
    } catch (_) {
        setPasskeyStatusMessage(passkeyT('us_security_passkeys_error_load', 'Failed to load passkeys.'));
    }
}

function renderPasskeyList() {
    const passkeyListContainer = document.getElementById('passkeyListContainer');
    
    if (!passkeyListContainer) return;

    passkeyListContainer.innerHTML = '';

    if (activePasskeys.length === 0) {
        setPasskeyStatusMessage(passkeyT('us_security_passkeys_empty', 'No passkeys configured.'));
        return;
    }

    activePasskeys.forEach((passkey) => {
        const canFormatRelativeTime = typeof formatRelativeTime === 'function';
        const createdDate = passkey.created_at
            ? (canFormatRelativeTime ? formatRelativeTime(passkey.created_at) : passkeyT('us_security_passkeys_unknown', 'Unknown'))
            : passkeyT('us_security_passkeys_unknown', 'Unknown');
        const lastUsedDate = passkey.last_used_at
            ? (canFormatRelativeTime ? formatRelativeTime(passkey.last_used_at) : passkeyT('us_security_passkeys_never_used', 'Never used'))
            : passkeyT('us_security_passkeys_never_used', 'Never used');

        const passkeyItem = document.createElement('div');
        passkeyItem.className = 'device-session-item';
        const passkeyName = passkey.name || passkeyT('us_security_passkeys_unknown_device', 'Unknown device');

        const iconContainer = document.createElement('div');
        iconContainer.className = 'device-icon';
        iconContainer.innerHTML = Icons.lock;
        const infoContainer = document.createElement('div');
        infoContainer.className = 'device-info';

        const nameEl = document.createElement('span');
        nameEl.className = 'device-name';
        nameEl.textContent = passkeyName;

        const detailsEl = document.createElement('span');
        detailsEl.className = 'device-details';
        detailsEl.textContent = passkeyFormat(
            'us_security_passkeys_details',
            'Created: {created} - Last used: {lastUsed}',
            { created: createdDate, lastUsed: lastUsedDate },
        );

        infoContainer.appendChild(nameEl);
        infoContainer.appendChild(detailsEl);

        const removeButton = document.createElement('button');
        removeButton.className = 'om-button border danger-nofill';
        removeButton.dataset.passkeyId = passkey.id;
        removeButton.textContent = passkeyT('us_security_passkeys_remove', 'Remove');

        passkeyItem.appendChild(iconContainer);
        passkeyItem.appendChild(infoContainer);
        passkeyItem.appendChild(removeButton);
        passkeyListContainer.appendChild(passkeyItem);
    });

    passkeyListContainer.querySelectorAll('.om-button.border.danger-nofill').forEach(btn => {
        btn.addEventListener('click', () => {
            const passkeyId = btn.dataset.passkeyId;
            const passkey = activePasskeys.find(pk => pk.id === passkeyId);
            const passkeyName = passkey?.name || passkeyT('us_security_passkeys_unknown_device', 'Unknown device');
            showDeletePasskeyModal(passkeyId, passkeyName);
        });
    });
}

function showDeletePasskeyModal(passkeyId, passkeyName) {
    const overlay = document.getElementById('deletePasskeyOverlay');
    const description = document.getElementById('deletePasskeyDescription');
    const cancelBtn = document.getElementById('deletePasskeyCancelButton');
    const confirmBtn = document.getElementById('deletePasskeyConfirmButton');

    if (!overlay || !description || !cancelBtn || !confirmBtn) {
        return;
    }

    const fallback = "Are you sure you want to remove \"{passkey}\"? You won't be able to use this passkey to sign in anymore.";
    description.textContent = typeof window.formatTranslation === 'function'
        ? window.formatTranslation('passkey_delete_desc', fallback, { passkey: passkeyName })
        : fallback.replace('{passkey}', passkeyName);
    overlay.removeAttribute('hidden');

    const cleanup = () => {
        overlay.setAttribute('hidden', '');
        cancelBtn.removeEventListener('click', handleCancel);
        confirmBtn.removeEventListener('click', handleConfirm);
    };

    const handleCancel = () => {
        cleanup();
    };

    const handleConfirm = async () => {
        confirmBtn.disabled = true;
        await removePasskey(passkeyId);
        cleanup();
        confirmBtn.disabled = false;
    };

    cancelBtn.addEventListener('click', handleCancel);
    confirmBtn.addEventListener('click', handleConfirm);

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            handleCancel();
        }
    });
}

async function removePasskey(passkeyId) {
    try {
        if (typeof window.ensureSecurityStepUp === 'function') {
            const steppedUp = await window.ensureSecurityStepUp();
            if (!steppedUp) return;
        }
        const res = await window.authedFetch(`/api/v1/auth/passkeys/${passkeyId}`, {
            method: 'DELETE',
        });
        
        if (res.ok) {
            activePasskeys = activePasskeys.filter(pk => pk.id !== passkeyId);
            renderPasskeyList();
            if (activePasskeys.length === 0) {
                await window.WebAuthnHelpers?.clearPasskeyAutoPromptHint?.(window.getCurrentUserSettingsEmail?.() || '');
            }
            if (typeof notifySuccess === 'function') {
                notifySuccess(passkeyT('us_security_passkeys_removed_success', 'Passkey removed successfully'));
            }
            window.loadSignInMethods?.({ silent: true });
        } else if (res.status === 401 || res.status === 403) {
            redirectToLogin();
        } else {
            const errorData = await res.json().catch(() => ({}));
            const fallbackMessage = passkeyT('us_security_passkeys_remove_failed', 'Failed to remove passkey');
            const errorMsg = typeof window.translateBackendDetail === 'function'
                ? window.translateBackendDetail(errorData?.detail, fallbackMessage)
                : (errorData?.detail || fallbackMessage);
            if (typeof notifyError === 'function') {
                notifyError(errorMsg);
            }
        }
    } catch (_) {
        if (typeof notifyError === 'function') {
            notifyError(passkeyT('us_security_passkeys_remove_failed', 'Failed to remove passkey'));
        }
    }
}

async function setupNewPasskey() {
    if (typeof window.PublicKeyCredential !== 'function' || !navigator.credentials) {
        if (typeof notifyError === 'function') {
            notifyError(passkeyT('passkey_not_supported', 'Passkeys are not supported in this browser.'));
        }
        return;
    }

    try {
        const beginRes = await window.authedFetch('/api/v1/auth/passkeys/register/begin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });

        if (!beginRes.ok) {
            if (typeof notifyError === 'function') {
                notifyError(passkeyT('passkey_begin_failed', 'Unable to start passkey setup.'));
            }
            return;
        }

        const beginData = await beginRes.json();
        const publicKeyOptions = window.WebAuthnHelpers?.preformatCreateOptions({ publicKey: (beginData.publicKey || {}) });
        if (!publicKeyOptions || !publicKeyOptions.publicKey) {
            if (typeof notifyError === 'function') {
                notifyError(passkeyT('passkey_begin_failed', 'Unable to start passkey setup.'));
            }
            return;
        }

        const rpIdMismatchMessage = window.WebAuthnHelpers?.getRpIdMismatchMessage?.(publicKeyOptions, {
            actionLabel: 'setup',
            expectedOrigin: beginData?.expected_origin,
        });
        if (rpIdMismatchMessage) {
            if (typeof notifyError === 'function') {
                notifyError(rpIdMismatchMessage);
            }
            return;
        }

        let credential;
        try {
            credential = await navigator.credentials.create(publicKeyOptions);
        } catch (err) {
            const name = err?.name ? String(err.name) : 'Error';
            const msg = err?.message ? String(err.message) : passkeyT('passkey_finish_failed', 'Passkey setup failed. Please try again.');

            const domainErrorMessage = window.WebAuthnHelpers?.getWebAuthnErrorMessage?.(err, publicKeyOptions, {
                actionLabel: 'setup',
                expectedOrigin: beginData?.expected_origin,
            });
            if (domainErrorMessage) {
                if (typeof notifyError === 'function') {
                    notifyError(domainErrorMessage);
                }
                return;
            }
            
            // Provide user-friendly message for common WebAuthn errors
            let userMessage = `${name}: ${msg}`;
            if (name === 'InvalidStateError') {
                userMessage = passkeyT('passkey_already_registered', 'This passkey is already registered on this device. If you want to add it again, please remove the existing one first.');
            } else if (name === 'NotAllowedError') {
                userMessage = passkeyT('passkey_not_allowed', 'The operation was cancelled or not allowed. Please try again.');
            }
            
            if (typeof notifyError === 'function') {
                notifyError(userMessage);
            }
            return;
        }
        if (!credential) {
            if (typeof notifyError === 'function') {
                notifyError(passkeyT('passkey_cancelled', 'Passkey setup was cancelled.'));
            }
            return;
        }

        const credentialJson = window.WebAuthnHelpers?.publicKeyCredentialToJSON(credential);
        const finishRes = await window.authedFetch('/api/v1/auth/passkeys/register/finish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                credential: credentialJson,
                expected_challenge: beginData.challenge,
            }),
        });

        if (!finishRes.ok) {
            const errorData = await finishRes.json().catch(() => ({}));
            const detail = errorData?.detail;
            const renderedDetail = typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : '');
            if (typeof notifyError === 'function') {
                notifyError(renderedDetail || passkeyT('passkey_finish_failed', 'Passkey setup failed. Please try again.'));
            }
            return;
        }

        if (typeof notifySuccess === 'function') {
            notifySuccess(passkeyT('passkey_setup_success', 'Passkey created successfully.'));
        }
        await window.WebAuthnHelpers?.markPasskeyAutoPromptHint?.(
            window.WebAuthnHelpers?.getPasskeyHintIdentifierFromCreateOptions?.(publicKeyOptions),
        );
        
        await loadPasskeys();
        window.loadSignInMethods?.({ silent: true });
    } catch (error) {
        const name = error?.name ? String(error.name) : '';
        const msg = error?.message ? String(error.message) : '';
        if (typeof notifyError === 'function') {
            notifyError((name || msg) ? `${name}${name && msg ? ': ' : ''}${msg}` : passkeyT('passkey_finish_failed', 'Passkey setup failed. Please try again.'));
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const setupPasskeyBtn = document.getElementById('setupPasskeyBtn');
    if (setupPasskeyBtn) {
        setupPasskeyBtn.addEventListener('click', (event) => {
            event.preventDefault();
            setupNewPasskey();
        });
    }
});

if (typeof window !== 'undefined') {
    window.loadPasskeys = loadPasskeys;
    window.setPasskeySectionEnabled = setPasskeySectionEnabled;
}
