const userSettingsModalStates = new WeakMap();
let activeUserSettingsModal = null;

const USER_SETTINGS_MODAL_FOCUSABLE_SELECTOR = [
    'button:not([disabled])',
    'a[href]',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
].join(', ');

function setUserSettingsModalInert(element, isInert) {
    if (!element) return;
    element.inert = isInert;
    element.toggleAttribute('inert', isInert);
}

function getUserSettingsModalFocusableElements(overlay) {
    return Array.from(overlay.querySelectorAll(USER_SETTINGS_MODAL_FOCUSABLE_SELECTOR))
        .filter((element) => {
            if (element.hidden || element.closest('[hidden], [inert]')) return false;
            return typeof element.getClientRects !== 'function'
                || element.getClientRects().length > 0;
        });
}

function focusUserSettingsModal(overlay) {
    const focusTarget = getUserSettingsModalFocusableElements(overlay)[0]
        || overlay.querySelector('[role="dialog"]')
        || overlay;
    if (focusTarget.tabIndex < 0 || focusTarget === overlay) {
        focusTarget.tabIndex = -1;
    }
    focusTarget.focus();
}

function getUserSettingsModalBackground(overlay) {
    const userSettingsView = document.getElementById('userSettingsView');
    if (!userSettingsView || userSettingsView.contains(overlay)) return null;
    return userSettingsView;
}

function rememberUserSettingsModalBackground(background) {
    if (!background) return null;
    return {
        element: background,
        inert: background.inert || background.hasAttribute('inert'),
        hadAriaHidden: background.hasAttribute('aria-hidden'),
        ariaHidden: background.getAttribute('aria-hidden'),
    };
}

function restoreUserSettingsModalBackground(backgroundState) {
    if (!backgroundState) return;
    const { element, inert, hadAriaHidden, ariaHidden } = backgroundState;
    if (hadAriaHidden) {
        element.setAttribute('aria-hidden', ariaHidden);
    } else {
        element.removeAttribute('aria-hidden');
    }
    setUserSettingsModalInert(element, inert);
}

function showUserSettingsModal(overlay) {
    if (userSettingsModalStates.has(overlay)) return;

    const background = getUserSettingsModalBackground(overlay);
    userSettingsModalStates.set(overlay, {
        background: rememberUserSettingsModalBackground(background),
        returnFocus: document.activeElement?.isConnected !== false
            && typeof document.activeElement?.focus === 'function'
            ? document.activeElement
            : null,
    });

    overlay.toggleAttribute('hidden', false);
    overlay.setAttribute('aria-hidden', 'false');
    setUserSettingsModalInert(overlay, false);

    // Move focus before hiding the settings subtree. Chrome otherwise refuses
    // aria-hidden when the subtree still owns focus and leaves both dialogs in
    // an inconsistent accessibility state.
    focusUserSettingsModal(overlay);
    if (background) {
        setUserSettingsModalInert(background, true);
        background.setAttribute('aria-hidden', 'true');
    }
    activeUserSettingsModal = overlay;
}

function hideUserSettingsModal(overlay) {
    const state = userSettingsModalStates.get(overlay);
    restoreUserSettingsModalBackground(state?.background);

    const returnFocus = state?.returnFocus;
    if (returnFocus?.isConnected !== false && typeof returnFocus?.focus === 'function') {
        returnFocus.focus();
    } else {
        const backgroundDialog = state?.background?.element.querySelector('[role="dialog"]');
        if (backgroundDialog) {
            backgroundDialog.tabIndex = -1;
            backgroundDialog.focus();
        }
    }

    // Focus must leave the dialog before it becomes hidden from assistive
    // technology. The inert state also guards against programmatic refocusing.
    setUserSettingsModalInert(overlay, true);
    overlay.setAttribute('aria-hidden', 'true');
    overlay.toggleAttribute('hidden', true);
    userSettingsModalStates.delete(overlay);
    if (activeUserSettingsModal === overlay) activeUserSettingsModal = null;
}

function toggleModalDisplay(id) {
    const overlay = document.getElementById(id);
    if (!overlay) return;
    if (overlay.hasAttribute('hidden')) {
        showUserSettingsModal(overlay);
    } else {
        hideUserSettingsModal(overlay);
    }
}

document.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab' || !activeUserSettingsModal) return;

    const focusable = getUserSettingsModalFocusableElements(activeUserSettingsModal);
    if (!focusable.length) {
        event.preventDefault();
        focusUserSettingsModal(activeUserSettingsModal);
        return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!activeUserSettingsModal.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
}, true);
