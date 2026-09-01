function isIOSDevice() {
    const userAgent = navigator.userAgent || '';
    const platform = navigator.platform || '';
    const maxTouchPoints = Number(navigator.maxTouchPoints || 0);

    return /iPad|iPhone|iPod/i.test(userAgent)
        || (platform === 'MacIntel' && maxTouchPoints > 1);
}

function syncAdminViewportHeight() {
    if (!isIOSDevice()) {
        return;
    }

    const viewportHeight = Math.round(window.visualViewport?.height || window.innerHeight || 0);
    if (!viewportHeight) {
        return;
    }

    document.documentElement.style.setProperty('--admin-viewport-height', `${viewportHeight}px`);
}

function getVisibleAdminModalOverlays() {
    return Array.from(document.querySelectorAll('.shared-modal-overlay:not([hidden])'));
}

function syncAdminModalBodyState() {
    document.body?.classList.toggle('admin-shared-modal-open', getVisibleAdminModalOverlays().length > 0);
}

function getAdminModalFocusableElements(overlay) {
    return Array.from(overlay.querySelectorAll([
        'a[href]',
        'button:not([disabled])',
        'input:not([disabled]):not([type="hidden"])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
    ].join(', '))).filter((element) => (
        !element.hidden
        && element.getAttribute('aria-hidden') !== 'true'
        && !element.closest('[hidden], [inert]')
    ));
}

function handleAdminModalInteraction(event) {
    const dismissButton = event.target.closest?.('[data-admin-modal-dismiss]');
    if (dismissButton) {
        const targetId = dismissButton.dataset.adminModalDismiss;
        document.getElementById(targetId)?.click();
        return;
    }

    if (event.type !== 'keydown' || event.key !== 'Tab') {
        return;
    }

    const overlay = event.target.closest?.('.shared-modal-overlay:not([hidden])');
    if (!overlay) {
        return;
    }

    const focusable = getAdminModalFocusableElements(overlay);
    if (!focusable.length) {
        event.preventDefault();
        overlay.querySelector('[role="dialog"]')?.focus();
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
}

document.addEventListener('click', handleAdminModalInteraction);
document.addEventListener('keydown', handleAdminModalInteraction);

const adminModalObserver = new MutationObserver((records) => {
    const modalStateChanged = records.some((record) => {
        if (record.type === 'attributes') {
            return record.target.matches?.('.shared-modal-overlay');
        }
        return Array.from(record.addedNodes).some((node) => (
            node instanceof Element
            && (node.matches('.shared-modal-overlay') || node.querySelector('.shared-modal-overlay'))
        ));
    });
    if (modalStateChanged) {
        syncAdminModalBodyState();
    }
});

adminModalObserver.observe(document.documentElement, {
    subtree: true,
    attributes: true,
    attributeFilter: ['hidden'],
    childList: true,
});

async function initAdmin() {
    if (isIOSDevice()) {
        document.body.classList.add('admin-ios');
        syncAdminViewportHeight();
        window.addEventListener('resize', syncAdminViewportHeight, { passive: true });
        window.addEventListener('orientationchange', syncAdminViewportHeight, { passive: true });
        window.visualViewport?.addEventListener?.('resize', syncAdminViewportHeight, { passive: true });
        window.visualViewport?.addEventListener?.('scroll', syncAdminViewportHeight, { passive: true });
    }

    await window.loadAllLogos();
    document.body.style.display = 'flex';
    syncAdminModalBodyState();
}

initAdmin();
