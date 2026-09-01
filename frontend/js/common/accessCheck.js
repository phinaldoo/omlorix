/**
 * Access Check Manager
 * Handles time-based access window restrictions for authenticated users
 */

(function() {
    'use strict';

    let checkInterval = null;
    let countdownInterval = null;
    let isCheckingAccess = false;
    let previousBodyOverflow = null;
    let previousFocus = null;
    const CHECK_INTERVAL_MS = 60000; // Check every minute

    const dom = {
        overlay: null,
        title: null,
        message: null,
        customMessage: null,
        timerSection: null,
        nextTime: null,
        countdown: null,
        logoutButton: null,
    };

    function initDOMElements() {
        dom.overlay = document.getElementById('appAccessBlockedOverlay');
        dom.title = document.getElementById('appAccessBlockedTitle');
        dom.message = document.getElementById('appAccessBlockedMessage');
        dom.customMessage = document.getElementById('appAccessBlockedCustomMessage');
        dom.timerSection = document.getElementById('appAccessBlockedTimerSection');
        dom.nextTime = document.getElementById('appAccessBlockedNextTime');
        dom.countdown = document.getElementById('appAccessBlockedCountdown');
        dom.logoutButton = document.getElementById('appAccessBlockedLogoutButton');
    }

    function translate(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    async function checkAccessStatus() {
        if (isCheckingAccess) return;
        isCheckingAccess = true;

        try {
            const response = await window.authedFetch('/api/v1/auth/access-status');
            
            if (!response.ok) {
                // If we get 403, the user is blocked
                if (response.status === 403) {
                    const data = await response.json().catch(() => ({}));
                    const detail = data.detail || {};
                    if (detail.type === 'access_time_blocked') {
                        showAccessBlockedOverlay(detail);
                    }
                }
                return;
            }

            const data = await response.json();
            if (!data.accessible) {
                showAccessBlockedOverlay(data);
            }
        } catch (error) {
            console.error('Failed to check access status:', error);
        } finally {
            isCheckingAccess = false;
        }
    }

    function showAccessBlockedOverlay(data) {
        if (!dom.overlay) return;

        // Stop checking while overlay is shown
        stopPeriodicCheck();

        if (dom.title) {
            dom.title.textContent = translate('access_blocked_title', 'Access Currently Unavailable');
        }
        if (dom.message) {
            const isPolicyError = data.reason === 'policy_error';
            dom.message.textContent = translate(
                isPolicyError ? 'access_blocked_policy_error_message' : 'access_blocked_message',
                isPolicyError
                    ? 'Access policy could not be evaluated right now. Please try again later or contact support.'
                    : 'Access is not available at this time due to restrictions set by your organization.'
            );
        }

        // Show custom message if provided
        if (data.blocked_message && dom.customMessage) {
            dom.customMessage.textContent = data.blocked_message;
            dom.customMessage.style.display = 'block';
        } else if (dom.customMessage) {
            dom.customMessage.style.display = 'none';
        }

        // Handle next_allowed_at timer
        if (data.next_allowed_at && dom.timerSection) {
            dom.timerSection.style.display = 'block';
            
            const nextAllowedDate = new Date(data.next_allowed_at);
            if (dom.nextTime) {
                try {
                    dom.nextTime.textContent = nextAllowedDate.toLocaleString(undefined, {
                        weekday: 'short',
                        hour: '2-digit',
                        minute: '2-digit'
                    });
                } catch (e) {
                    dom.nextTime.textContent = nextAllowedDate.toLocaleTimeString();
                }
            }

            // Start countdown
            startCountdown(nextAllowedDate);
        } else if (dom.timerSection) {
            dom.timerSection.style.display = 'none';
        }

        // Show overlay
        previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        if (previousBodyOverflow === null) previousBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        dom.overlay.classList.add('active');
        dom.overlay.setAttribute('aria-hidden', 'false');

        // Focus logout button
        setTimeout(() => {
            if (dom.logoutButton) dom.logoutButton.focus();
        }, 100);
    }

    function startCountdown(targetDate) {
        if (countdownInterval) {
            clearInterval(countdownInterval);
        }

        const updateCountdown = () => {
            const now = new Date();
            const diff = Math.max(0, Math.floor((targetDate - now) / 1000));

            if (diff <= 0) {
                if (dom.countdown) dom.countdown.textContent = '00:00:00';
                clearInterval(countdownInterval);
                // Access should be restored, reload the page
                window.location.reload();
                return;
            }

            const hours = Math.floor(diff / 3600);
            const minutes = Math.floor((diff % 3600) / 60);
            const seconds = diff % 60;

            if (dom.countdown) {
                dom.countdown.textContent = 
                    `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            }
        };

        updateCountdown();
        countdownInterval = setInterval(updateCountdown, 1000);
    }

    function hideAccessBlockedOverlay() {
        if (!dom.overlay) return;
        
        dom.overlay.classList.remove('active');
        dom.overlay.setAttribute('aria-hidden', 'true');
        if (previousBodyOverflow !== null) {
            document.body.style.overflow = previousBodyOverflow;
            previousBodyOverflow = null;
        }
        previousFocus?.focus?.();
        previousFocus = null;
        
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }

        // Resume periodic checks
        startPeriodicCheck();
    }

    function startPeriodicCheck() {
        if (checkInterval) return;
        
        // Check immediately
        checkAccessStatus();
        
        // Then check periodically
        checkInterval = setInterval(checkAccessStatus, CHECK_INTERVAL_MS);
    }

    function stopPeriodicCheck() {
        if (checkInterval) {
            clearInterval(checkInterval);
            checkInterval = null;
        }
    }

    async function handleLogout() {
        if (typeof window.logout === 'function') {
            await window.logout();
            return;
        }
        try {
            // Call logout endpoint
            await window.authedFetch('/api/v1/auth/logout', {
                method: 'POST'
            });
        } catch (error) {
            console.error('Logout error:', error);
        } finally {
            // Clear local storage and redirect to login
            try {
                localStorage.clear();
            } catch (error) {
                console.warn('Failed to clear localStorage:', error);
            }
            try {
                sessionStorage.clear();
            } catch (error) {
                console.warn('Failed to clear sessionStorage:', error);
            }
            window.location.href = '/login.html';
        }
    }

    function init() {
        // Only initialize on main app page
        if (!document.body.dataset.page || document.body.dataset.page !== 'index') {
            return;
        }

        initDOMElements();

        if (!dom.overlay || !dom.logoutButton) {
            console.warn('Access check overlay elements not found');
            return;
        }

        // Attach logout handler
        dom.logoutButton.addEventListener('click', handleLogout);
        dom.overlay.addEventListener('keydown', (event) => {
            if (!dom.overlay.classList.contains('active')) return;
            if (event.key === 'Escape') {
                // This policy gate is intentionally non-dismissible.
                event.preventDefault();
                dom.logoutButton.focus();
                return;
            }
            if (event.key === 'Tab') {
                event.preventDefault();
                dom.logoutButton.focus();
            }
        });

        // Start periodic access checks
        startPeriodicCheck();

        // Also check on visibility change (when user returns to tab)
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                checkAccessStatus();
            }
        });
    }

    // Expose global access check function for error handlers
    window.handleAccessBlocked = function(errorDetail) {
        if (!dom.overlay) {
            initDOMElements();
        }
        if (errorDetail && errorDetail.type === 'access_time_blocked') {
            showAccessBlockedOverlay(errorDetail);
        }
    };

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
