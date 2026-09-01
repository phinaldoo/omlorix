// Authentication and Device Management
let activeSessions = [];
let currentSessionId = null;

function authenticationT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function authenticationFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(authenticationT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function authenticationEscapeHtml(text = '') {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Load active sessions when settings are opened
async function loadActiveSessions() {
    const deviceListContainer = document.getElementById('deviceListContainer');
    if (!deviceListContainer) return;

    try {
        const res = await window.authedFetch(`/api/v1/auth/logins`);
        if (res.ok) {
            activeSessions = await res.json();
            const current = activeSessions.find(s => s.current);
            currentSessionId = current ? current.id : null;
            renderDeviceList();
        } else {
            if (res.status === 401 || res.status === 403) {
                redirectToLogin();
            }
        }
    } catch (error) {
        console.error('Error loading active sessions:', error);
        deviceListContainer.innerHTML = `<p style="color: var(--text-color-secondary); padding: 15px 0;" data-i18n="us_auth_devices_load_failed">${authenticationEscapeHtml(authenticationT('us_auth_devices_load_failed', 'Failed to load devices.'))}</p>`;
    }
}

function renderDeviceList() {
    const deviceListContainer = document.getElementById('deviceListContainer');
    const logoutAllBtn = document.getElementById('logoutAllDevicesBtn');
    const logoutAllRow = document.querySelector('.us-setting-item:has(#logoutAllDevicesBtn)');
    
    if (!deviceListContainer) return;

    deviceListContainer.innerHTML = '';

    if (activeSessions.length === 0) {
        deviceListContainer.innerHTML = `<p style="color: var(--text-color-secondary); padding: 15px 0;" data-i18n="us_auth_no_active_sessions">${authenticationEscapeHtml(authenticationT('us_auth_no_active_sessions', 'No active sessions found.'))}</p>`;
        if (logoutAllBtn) logoutAllBtn.style.display = 'none';
        if (logoutAllRow) logoutAllRow.style.display = 'none';
        return;
    }
    
    // Show logout all button row if there are multiple sessions
    if (logoutAllBtn && logoutAllRow) {
        const shouldShow = activeSessions.length > 1;
        logoutAllBtn.style.display = shouldShow ? 'inline-flex' : 'none';
        logoutAllRow.style.display = shouldShow ? 'flex' : 'none';
    }

    // Sort by last active time (newest first)
    const sortedSessionsRaw = [...activeSessions].sort((a, b) => 
        new Date(b.last_active_at) - new Date(a.last_active_at)
    );

    // Ensure current device is always at the top
    let sortedSessions;
    const curIdx = sortedSessionsRaw.findIndex(s => (s.current || s.id === currentSessionId));
    if (curIdx > -1) {
        const [cur] = sortedSessionsRaw.splice(curIdx, 1);
        sortedSessions = [cur, ...sortedSessionsRaw];
    } else {
        sortedSessions = sortedSessionsRaw;
    }

    sortedSessions.forEach((session) => {
        const isCurrent = session.current || session.id === currentSessionId;
        const deviceInfo = parseDeviceInfo(session.device_info);
        const timeAgo = formatRelativeTime(session.last_active_at);

        const deviceItem = document.createElement('div');
        deviceItem.className = 'device-session-item';
        
        const iconSVG = deviceInfo.deviceType === 'mobile' 
            ? Icons.mobile
            : Icons.desktop;

        const currentDeviceTag = isCurrent
            ? `<span class="current-device-tag"> ${authenticationEscapeHtml(authenticationT('us_auth_this_device_tag', '(This Device)'))}</span>`
            : '';
        const logoutButton = `<button class="om-button border danger-nofill" data-session-id="${authenticationEscapeHtml(session.id)}" ${isCurrent ? 'disabled' : ''} data-i18n="us_auth_log_out_device">${authenticationEscapeHtml(authenticationT('us_auth_log_out_device', 'Log Out'))}</button>`;
        const deviceDetails = authenticationFormatT(
            'us_auth_device_last_active',
            '{ipAddress} - Last active: {timeAgo}',
            { ipAddress: session.ip_address, timeAgo }
        );

        deviceItem.innerHTML = `
            <div class="device-icon">${iconSVG}</div>
            <div class="device-info">
                <span class="device-name">${authenticationEscapeHtml(deviceInfo.name)}${currentDeviceTag}</span>
                <span class="device-details">${authenticationEscapeHtml(deviceDetails)}</span>
            </div>
            ${logoutButton}
        `;
        deviceListContainer.appendChild(deviceItem);
    });

    // Add event listeners for logout buttons
    deviceListContainer.querySelectorAll('.om-button.border.danger-nofill:not(:disabled)').forEach(btn => {
        btn.addEventListener('click', () => {
            const sessionId = btn.dataset.sessionId;
            logoutDevice(sessionId);
        });
    });
}

async function logoutDevice(sessionId) {
    try {
        const res = await window.authedFetch(`/api/v1/auth/login`, {
            method: "DELETE",
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ auth_id: sessionId })
        });
        
        if (res.ok) {
            if (sessionId === currentSessionId) {
                redirectToLogin();
                return;
            }
            activeSessions = await res.json();
            renderDeviceList();
        } else if (res.status === 401 || res.status === 403) {
            redirectToLogin();
        }
    } catch (error) {
        console.error('Error logging out device:', error);
    }
}

async function logoutAllDevices() {
    try {
        const res = await window.authedFetch(`/api/v1/auth/login`, {
            method: "DELETE",
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });
        
        if (res.status === 401 || res.status === 403) {
            redirectToLogin();
            return;
        }
    } finally {
        // On success, redirect to login
        redirectToLogin();
    }
}

// Initialize logout all button
document.addEventListener('DOMContentLoaded', () => {
    const logoutAllBtn = document.getElementById('logoutAllDevicesBtn');
    if (logoutAllBtn) {
        logoutAllBtn.addEventListener('click', (event) => {
            event.preventDefault();
            openLogoutAllDevicesModal();
        });
    }
});

// Logout All Devices Modal Handling
(() => {
    const SELECTORS = {
        overlayId: 'logoutAllDevicesOverlay',
        openButtonId: 'logoutAllDevicesBtn',
        cancelButtonId: 'logoutAllDevicesCancelButton',
        confirmButtonId: 'logoutAllDevicesPrimaryButton',
        confirmTextId: 'logoutAllDevicesPrimaryText',
        cancelTextId: 'logoutAllDevicesCancelText',
        cardSelector: '.delete-warning-card'
    };

    const state = {
        overlay: null,
        card: null,
        openButton: null,
        cancelButton: null,
        confirmButton: null,
        confirmText: null,
        cancelText: null,
        defaultConfirmLabel: authenticationT('us_auth_logout_all_devices', 'Logout All Devices'),
        defaultCancelLabel: authenticationT('common_cancel', 'Cancel'),
        lastFocusedElement: null,
        isProcessing: false
    };

    const focusElement = (element) => {
        if (!element || typeof element.focus !== 'function') {
            return;
        }

        try {
            element.focus({ preventScroll: true });
        } catch (error) {
            element.focus();
        }
    };

    const setProcessingState = (isProcessing) => {
        state.isProcessing = isProcessing;

        if (state.confirmButton) {
            state.confirmButton.disabled = isProcessing;
        }
        if (state.cancelButton) {
            state.cancelButton.disabled = isProcessing;
        }
        if (state.confirmText) {
            state.confirmText.textContent = isProcessing
                ? authenticationT('us_auth_logging_out', 'Logging out...')
                : state.defaultConfirmLabel;
        }
    };

    const closeModal = () => {
        if (!state.overlay || state.overlay.hasAttribute('hidden')) {
            return;
        }

        state.overlay.setAttribute('hidden', '');
        document.removeEventListener('keydown', handleKeydown);
        state.overlay.removeEventListener('click', handleBackdropClick);
        setProcessingState(false);

        if (state.lastFocusedElement) {
            focusElement(state.lastFocusedElement);
            state.lastFocusedElement = null;
        }
    };

    const handleKeydown = (event) => {
        if (event.key === 'Escape' && !state.isProcessing) {
            event.preventDefault();
            closeModal();
        }
    };

    const handleBackdropClick = (event) => {
        if (event.target === state.overlay && !state.isProcessing) {
            closeModal();
        }
    };

    const openModal = () => {
        if (!state.overlay || !state.overlay.hasAttribute('hidden')) {
            return;
        }

        state.lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        state.overlay.removeAttribute('hidden');
        if (typeof window === 'undefined' || !window.registerEscapeHandler) {
            document.addEventListener('keydown', handleKeydown);
        }
        state.overlay.addEventListener('click', handleBackdropClick);
        setProcessingState(false);

        requestAnimationFrame(() => {
            focusElement(state.confirmButton || state.cancelButton);
        });
    };

    const handleCancel = (event) => {
        if (event) {
            event.preventDefault();
        }
        if (!state.isProcessing) {
            closeModal();
        }
    };

    const handleConfirm = async (event) => {
        if (event) {
            event.preventDefault();
        }
        if (state.isProcessing) {
            return;
        }

        setProcessingState(true);

        let success = false;
        try {
            await logoutAllDevices();
            success = true;
        } catch (error) {
            console.error('[logoutAllDevices] Unexpected error while logging out', error);
            success = false;
        }

        if (success) {
            closeModal();
            return;
        }

        setProcessingState(false);
    };

    const init = () => {
        state.overlay = document.getElementById(SELECTORS.overlayId);
        state.card = state.overlay?.querySelector(SELECTORS.cardSelector);
        state.openButton = document.getElementById(SELECTORS.openButtonId);
        state.cancelButton = document.getElementById(SELECTORS.cancelButtonId);
        state.confirmButton = document.getElementById(SELECTORS.confirmButtonId);
        state.confirmText = document.getElementById(SELECTORS.confirmTextId);
        state.cancelText = document.getElementById(SELECTORS.cancelTextId);

        if (!state.overlay || !state.cancelButton || !state.confirmButton) {
            return;
        }

        if (state.card) {
            state.card.setAttribute('role', 'dialog');
            state.card.setAttribute('aria-modal', 'true');
            state.card.setAttribute('aria-labelledby', 'logoutAllDevicesHeaderTitle');
        }

        if (state.confirmText && state.confirmText.textContent) {
            state.defaultConfirmLabel = state.confirmText.textContent;
        }

        if (state.cancelText && state.cancelText.textContent) {
            state.defaultCancelLabel = state.cancelText.textContent;
        }

        state.cancelButton.addEventListener('click', handleCancel);
        state.confirmButton.addEventListener('click', handleConfirm);

        if (typeof window !== 'undefined' && window.registerEscapeHandler) {
            window.registerEscapeHandler({
                id: 'logout-all-devices-modal',
                priority: 190,
                isActive: () => Boolean(state.overlay && !state.overlay.hasAttribute('hidden')) && !state.isProcessing,
                close: () => {
                    handleCancel();
                }
            });
        }
    };

    // Make openModal globally accessible
    window.openLogoutAllDevicesModal = openModal;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();

// Export function to be called when user settings are opened
if (typeof window !== 'undefined') {
    window.loadActiveSessions = loadActiveSessions;
}
