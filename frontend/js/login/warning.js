let timerInterval;
let warningDuration;

function setOverlayActiveState(overlay, isActive, options = {}) {
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
    window.loginModalManager?.sync();
    const focusTarget = isActive ? options.initialFocus : options.fallbackFocus;
    if (focusTarget) {
        setTimeout(() => {
            const element = typeof focusTarget === 'function' ? focusTarget() : focusTarget;
            element?.focus?.();
        }, 0);
    }
}

['warningOverlay', 'pendingOverlay', 'accessBlockedOverlay'].forEach((overlayId) => {
    setOverlayActiveState(document.getElementById(overlayId), false);
});

function resolveTranslation(key, fallback) {
    if (typeof window.getTranslation === "function") {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function normalizeAccessBlockedErrorCode(error) {
    return String(error || '')
        .trim()
        .toLowerCase()
        .replace(/[\s-]+/g, '_');
}

function buildAccessBlockedDetailFromSearchParams(params) {
    if (!(params instanceof URLSearchParams)) {
        return null;
    }
    const blockedCode = params.get('access_blocked') || params.get('error');
    if (normalizeAccessBlockedErrorCode(blockedCode) !== 'access_time_blocked') {
        return null;
    }
    return {
        type: 'access_time_blocked',
        reason: params.get('reason') || '',
        next_allowed_at: params.get('next_allowed_at') || '',
        blocked_message: params.get('blocked_message') || '',
    };
}

function removeAccessBlockedSearchParams() {
    try {
        const currentUrl = new URL(window.location.href);
        ['access_blocked', 'error', 'reason', 'next_allowed_at', 'blocked_message'].forEach((key) => {
            currentUrl.searchParams.delete(key);
        });
        const nextUrl = `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`;
        window.history?.replaceState?.(null, document.title, nextUrl);
    } catch (error) {
        console.warn('Failed to clean access-blocked login URL:', error);
    }
}

function consumeRefreshAccessBlockedDetail() {
    if (typeof window.consumeAccessTimeBlockedRefreshDetail !== 'function') {
        return null;
    }
    return window.consumeAccessTimeBlockedRefreshDetail();
}

function escapeHtml(value) {
    if (typeof value !== "string") {
        return "";
    }
    const map = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    };
    return value.replace(/[&<>"']/g, (char) => map[char]);
}

function appendSafeWarningContent(target, htmlLikeText) {
    const MAX_NESTING = 1000;
    const source = String(htmlLikeText || '').trim();
    if (!target || !source) {
        return;
    }

    const template = document.createElement('template');
    template.innerHTML = source;

    const appendNode = (node, parent, depth) => {
        if (depth > MAX_NESTING) {
            parent.appendChild(document.createTextNode(node.textContent || ''));
            return;
        }
        if (node.nodeType === Node.TEXT_NODE) {
            parent.appendChild(document.createTextNode(node.textContent || ''));
            return;
        }

        if (node.nodeType !== Node.ELEMENT_NODE) {
            return;
        }

        const tagName = node.tagName.toLowerCase();
        if (tagName === 'strong') {
            const strong = document.createElement('strong');
            Array.from(node.childNodes).forEach((child) => appendNode(child, strong, depth + 1));
            parent.appendChild(strong);
            return;
        }

        if (tagName === 'br') {
            parent.appendChild(document.createElement('br'));
            return;
        }

        Array.from(node.childNodes).forEach((child) => appendNode(child, parent, depth + 1));
    };

    Array.from(template.content.childNodes).forEach((child) => appendNode(child, target, 0));
}

function renderWarningMessage(container, message, metaParts = []) {
    if (!container) {
        return;
    }

    container.replaceChildren();
    const blocks = [];
    const safeMetaParts = Array.isArray(metaParts) ? metaParts : [];

    if (message && String(message).trim()) {
        blocks.push({ html: String(message).trim() });
    }

    safeMetaParts.forEach((part) => {
        if (part && String(part).trim()) {
            blocks.push({ html: String(part).trim() });
        }
    });

    if (!blocks.length) {
        return;
    }

    blocks.forEach((block, index) => {
        if (index > 0) {
            container.appendChild(document.createElement('br'));
            container.appendChild(document.createElement('br'));
        }
        appendSafeWarningContent(container, block.html);
    });
}


function showWarning(timeLeft, title, message, meta = {}) {
    const warningHeaderTitle = document.getElementById('warningHeaderTitle');
    warningHeaderTitle.textContent = title;
    const warningMessage = document.getElementById('warningMessage');
    const reason = typeof meta.reason === 'string' ? meta.reason.trim() : '';
    const unlockAt = typeof meta.unlockAt === 'string' ? meta.unlockAt.trim() : '';
    const type = typeof meta.type === 'string' ? meta.type.trim() : '';
    const parts = [];
    if (message && message.length > 0) {
        parts.push(message);
    }
    if (reason) {
        const reasonLabel = resolveTranslation('lock_reason_label', 'Reason');
        parts.push(`<strong>${reasonLabel}:</strong> ${escapeHtml(reason)}`);
    }
    if (unlockAt) {
        const unlockLabel = resolveTranslation('lock_unlock_at_label', 'Unlocks at');
        parts.push(`<strong>${unlockLabel}:</strong> ${escapeHtml(unlockAt)}`);
    }
    renderWarningMessage(warningMessage, message, parts.slice(message ? 1 : 0));
    const overlay = document.getElementById('warningOverlay');
    if (type) {
        overlay.dataset.lockType = type;
    } else {
        delete overlay.dataset.lockType;
    }
    setOverlayActiveState(overlay, true, {
        initialFocus: () => document.getElementById('warningBackToLoginButton'),
        fallbackFocus: () => document.getElementById('signinEmail'),
        dismiss: hideWarning,
    });
    
    // Get the timer section element
    const timerSection = overlay.querySelector('.warning-timer-section');
    const progressBar = overlay.querySelector('.warning-progress-bar');
    if (timerInterval) {
        clearInterval(timerInterval);
    }
    warningDuration = Number.isFinite(timeLeft) ? Math.max(0, Math.floor(timeLeft)) : 0;
    
    if (warningDuration > 0) {
        // Show timer section and start timer
        timerSection.style.display = 'block';
        warningStartTimer(warningDuration, { progressBar });
    } else {
        // Hide timer section
        timerSection.style.display = 'none';
        if (progressBar) {
            progressBar.style.width = '0%';
        }
    }

}


function hideWarning() {
    const overlay = document.getElementById('warningOverlay');
    setOverlayActiveState(overlay, false, {
        fallbackFocus: () => document.getElementById('signinEmail'),
    });
    if (timerInterval) {
        clearInterval(timerInterval);
    }
    const progressBar = overlay?.querySelector('.warning-progress-bar');
    if (progressBar) {
        progressBar.style.width = '0%';
    }
}

function warningStartTimer(timeLeft) {
    const timerDisplay = document.getElementById('timerDisplay');

    let remaining = timeLeft;
    const progressBar = document.getElementById('warningOverlay')?.querySelector('.warning-progress-bar');

    // Display initial time immediately
    const renderTime = () => {
        const safeRemaining = Math.max(remaining, 0);
        const hours = Math.floor(safeRemaining / 3600);
        const minutes = Math.floor((safeRemaining % 3600) / 60);
        const seconds = safeRemaining % 60;
        const formattedTime = 
            `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        timerDisplay.textContent = formattedTime;
        if (progressBar) {
            const ratio = warningDuration > 0 ? safeRemaining / warningDuration : 0;
            const percentage = Math.max(0, Math.min(100, ratio * 100));
            progressBar.style.width = `${percentage}%`;
        }
    };

    renderTime();
    
    timerInterval = setInterval(() => {
        remaining -= 1;
        renderTime();
        if (remaining <= 0) {
            clearInterval(timerInterval);
            hideWarning();
        }
    }, 1000);
}




document.getElementById('warningBackToLoginButton').addEventListener('click', () => hideWarning());
document.getElementById('warningCloseButton')?.addEventListener('click', () => hideWarning());


// Close on overlay click (but not on card click)
document.getElementById('warningOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
        hideWarning();
    }
});







function showPendingNotification() {
    const overlay = document.getElementById('pendingOverlay');
    setOverlayActiveState(overlay, true, {
        initialFocus: () => document.getElementById('pendingBackToLoginButton'),
        fallbackFocus: () => document.getElementById('signinEmail'),
        dismiss: hidePendingNotification,
    });
}

function hidePendingNotification() {
    const overlay = document.getElementById('pendingOverlay');
    setOverlayActiveState(overlay, false, {
        fallbackFocus: () => document.getElementById('signinEmail'),
    });
}

// Close on Back to login button 
document.getElementById('pendingBackToLoginButton').addEventListener('click', () => hidePendingNotification());
document.getElementById('pendingCloseButton')?.addEventListener('click', () => hidePendingNotification());

// Close on overlay click (but not on card click)
document.getElementById('pendingOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
        hidePendingNotification();
    }
});


// Access Blocked Overlay
let accessBlockedTimerInterval = null;

function showAccessBlockedOverlay(result) {
    const overlay = document.getElementById('accessBlockedOverlay');
    if (!overlay) return;

    const titleEl = document.getElementById('accessBlockedTitle');
    const messageEl = document.getElementById('accessBlockedMessage');
    const customMessageEl = document.getElementById('accessBlockedCustomMessage');
    const timerSection = document.getElementById('accessBlockedTimerSection');
    const nextTimeEl = document.getElementById('accessBlockedNextTime');
    const countdownEl = document.getElementById('accessBlockedCountdown');
    const contactBtn = document.getElementById('accessBlockedContactSupport');

    if (titleEl) {
        titleEl.textContent = resolveTranslation('access_blocked_title', 'Access Currently Unavailable');
    }
    if (messageEl) {
        const isPolicyError = result.reason === 'policy_error';
        messageEl.textContent = resolveTranslation(
            isPolicyError ? 'access_blocked_policy_error_message' : 'access_blocked_message',
            isPolicyError
                ? 'Access policy could not be evaluated right now. Please try again later or contact support.'
                : 'Sign-in is not available at this time due to access restrictions set by your organization.'
        );
    }

    // Show custom message if provided
    if (result.blocked_message && customMessageEl) {
        customMessageEl.textContent = result.blocked_message;
        customMessageEl.style.display = 'block';
    } else if (customMessageEl) {
        customMessageEl.style.display = 'none';
    }

    // Handle next_allowed_at timer
    if (result.next_allowed_at && timerSection) {
        timerSection.style.display = 'block';
        
        const nextAllowedDate = new Date(result.next_allowed_at);
        if (nextTimeEl) {
            try {
                nextTimeEl.textContent = nextAllowedDate.toLocaleString(undefined, {
                    weekday: 'short',
                    hour: '2-digit',
                    minute: '2-digit'
                });
            } catch (e) {
                nextTimeEl.textContent = nextAllowedDate.toLocaleTimeString();
            }
        }

        // Start countdown
        startAccessBlockedCountdown(nextAllowedDate, countdownEl);
    } else if (timerSection) {
        timerSection.style.display = 'none';
    }

    // Show contact support if email is configured
    if (contactBtn) {
        const email = typeof contactSupportEmail === 'string' ? contactSupportEmail.trim() : '';
        if (email && isValidEmail(email)) {
            contactBtn.href = `mailto:${email}`;
            contactBtn.style.display = '';
        } else {
            contactBtn.style.display = 'none';
        }
    }

    setOverlayActiveState(overlay, true, {
        initialFocus: () => document.getElementById('accessBlockedBackButton'),
        fallbackFocus: () => document.getElementById('signinEmail'),
        dismiss: hideAccessBlockedOverlay,
    });
}

function maybeShowAccessBlockedRedirectModal() {
    const urlDetail = buildAccessBlockedDetailFromSearchParams(new URLSearchParams(window.location.search));
    if (urlDetail) {
        showAccessBlockedOverlay(urlDetail);
        removeAccessBlockedSearchParams();
        return;
    }

    const bootstrap = window.__omlorixInitialAuthBootstrap;
    const showConsumedRefreshDetail = () => {
        const refreshDetail = consumeRefreshAccessBlockedDetail();
        if (refreshDetail) {
            showAccessBlockedOverlay(refreshDetail);
        }
    };

    // The login page also runs the shared refresh bootstrap. When a user lands
    // directly on /login with an access-window-blocked session, wait for that
    // refresh to finish and then reuse the same modal instead of showing a toast.
    if (bootstrap && typeof bootstrap.finally === 'function') {
        bootstrap.finally(showConsumedRefreshDetail);
        return;
    }
    showConsumedRefreshDetail();
}

function startAccessBlockedCountdown(targetDate, countdownEl) {
    if (accessBlockedTimerInterval) {
        clearInterval(accessBlockedTimerInterval);
    }

    const updateCountdown = () => {
        const now = new Date();
        const diff = Math.max(0, Math.floor((targetDate - now) / 1000));

        if (diff <= 0) {
            if (countdownEl) countdownEl.textContent = '00:00:00';
            clearInterval(accessBlockedTimerInterval);
            hideAccessBlockedOverlay();
            return;
        }

        const hours = Math.floor(diff / 3600);
        const minutes = Math.floor((diff % 3600) / 60);
        const seconds = diff % 60;

        if (countdownEl) {
            countdownEl.textContent = 
                `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }
    };

    updateCountdown();
    accessBlockedTimerInterval = setInterval(updateCountdown, 1000);
}

function hideAccessBlockedOverlay() {
    const overlay = document.getElementById('accessBlockedOverlay');
    setOverlayActiveState(overlay, false, {
        fallbackFocus: () => document.getElementById('signinEmail'),
    });
    if (accessBlockedTimerInterval) {
        clearInterval(accessBlockedTimerInterval);
        accessBlockedTimerInterval = null;
    }
}

// Access Blocked back button
const accessBlockedBackBtn = document.getElementById('accessBlockedBackButton');
if (accessBlockedBackBtn) {
    accessBlockedBackBtn.addEventListener('click', () => hideAccessBlockedOverlay());
}
document.getElementById('accessBlockedCloseButton')?.addEventListener('click', () => hideAccessBlockedOverlay());

// Access Blocked overlay click to close
const accessBlockedOverlay = document.getElementById('accessBlockedOverlay');
if (accessBlockedOverlay) {
    accessBlockedOverlay.addEventListener('click', (e) => {
        if (e.target === e.currentTarget) {
            hideAccessBlockedOverlay();
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', maybeShowAccessBlockedRedirectModal);
} else {
    maybeShowAccessBlockedRedirectModal();
}
