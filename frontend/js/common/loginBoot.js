(function () {
    // Keep the fail-safe short enough to preserve access to sign-in while
    // allowing the normal deferred initialization path to avoid a fallback
    // flash on a healthy connection.
    const LOGIN_UI_FAILSAFE_REVEAL_MS = 5000;

    // The public login design is loaded from the backend. Keep the complete
    // login surface out of the first paint so the hardcoded fallback markup
    // cannot briefly appear with the wrong layout while that request runs.
    document.documentElement.classList.add('login-ui-pending');
    window.__loginUIReady = false;

    let failSafeRevealTimer = window.setTimeout(() => {
        // A failed deferred script or a request that never settles must not
        // turn the public sign-in page into a permanently blank screen.
        document.documentElement.classList.remove('login-ui-pending');
        failSafeRevealTimer = null;
    }, LOGIN_UI_FAILSAFE_REVEAL_MS);

    // script.js calls this only after the selected layout and any split-panel
    // image are ready. The readiness check prevents unrelated early callers
    // from exposing a partially configured login page.
    window.__revealLoginUI = function () {
        if (!window.__loginUIReady) {
            return;
        }
        if (failSafeRevealTimer !== null) {
            window.clearTimeout(failSafeRevealTimer);
            failSafeRevealTimer = null;
        }
        document.documentElement.classList.remove('login-ui-pending');
    };

    let savedMode = 'system';
    let savedTheme = 'mono';

    try {
        savedMode = localStorage.getItem('mode') || 'system';
        savedTheme = localStorage.getItem('theme') || 'mono';
    } catch (_error) {
        savedMode = 'system';
        savedTheme = 'mono';
    }

    let finalMode = savedMode;
    if (savedMode === 'system') {
        finalMode = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    document.documentElement.setAttribute('data-mode', finalMode);
    document.documentElement.setAttribute('data-theme', savedTheme);
})();
