(function () {
    'use strict';

    const activeReasons = new Set();
    let sentinel = null;
    let sentinelReleaseHandler = null;
    let requestInFlight = null;
    let listenersInstalled = false;

    function isSupported() {
        return Boolean(
            typeof window !== 'undefined'
            && window.isSecureContext === true
            && typeof navigator !== 'undefined'
            && navigator.wakeLock
            && typeof navigator.wakeLock.request === 'function'
        );
    }

    function hasActiveReasons() {
        return activeReasons.size > 0;
    }

    function canRequestWakeLock() {
        return Boolean(
            isSupported()
            && typeof document !== 'undefined'
            && !document.hidden
            && hasActiveReasons()
        );
    }

    function detachSentinelListener() {
        if (sentinel && sentinelReleaseHandler) {
            try {
                sentinel.removeEventListener('release', sentinelReleaseHandler);
            } catch (_) {
                // no-op
            }
        }
        sentinelReleaseHandler = null;
    }

    function clearSentinelReference(expectedSentinel = null) {
        if (expectedSentinel && sentinel !== expectedSentinel) {
            return;
        }
        detachSentinelListener();
        sentinel = null;
    }

    async function releaseSentinel(expectedSentinel = null) {
        const currentSentinel = expectedSentinel || sentinel;
        if (!currentSentinel) {
            return;
        }

        clearSentinelReference(currentSentinel);
        if (typeof currentSentinel.release === 'function') {
            try {
                await currentSentinel.release();
            } catch (_) {
                // no-op
            }
        }
    }

    function bindSentinel(nextSentinel) {
        clearSentinelReference();
        sentinel = nextSentinel;
        sentinelReleaseHandler = () => {
            clearSentinelReference(nextSentinel);
            if (hasActiveReasons()) {
                void syncWakeLockState();
            }
        };

        try {
            nextSentinel.addEventListener('release', sentinelReleaseHandler);
        } catch (_) {
            sentinelReleaseHandler = null;
        }
    }

    async function ensureWakeLock() {
        if (!canRequestWakeLock()) {
            if (!hasActiveReasons()) {
                await releaseSentinel();
            }
            return sentinel;
        }

        if (sentinel && sentinel.released !== true) {
            return sentinel;
        }

        if (requestInFlight) {
            return requestInFlight;
        }

        requestInFlight = (async () => {
            let nextSentinel = null;
            try {
                nextSentinel = await navigator.wakeLock.request('screen');
            } catch (_) {
                return null;
            } finally {
                requestInFlight = null;
            }

            if (!nextSentinel) {
                return null;
            }

            if (!canRequestWakeLock()) {
                try {
                    await nextSentinel.release();
                } catch (_) {
                    // no-op
                }
                return null;
            }

            bindSentinel(nextSentinel);
            return sentinel;
        })();

        return requestInFlight;
    }

    async function syncWakeLockState() {
        if (canRequestWakeLock()) {
            await ensureWakeLock();
            return sentinel;
        }

        if (!hasActiveReasons() || typeof document === 'undefined' || document.hidden || !isSupported()) {
            await releaseSentinel();
        }

        return sentinel;
    }

    function installListeners() {
        if (listenersInstalled || typeof document === 'undefined' || typeof window === 'undefined') {
            return;
        }
        listenersInstalled = true;

        document.addEventListener('visibilitychange', () => {
            void syncWakeLockState();
        });

        window.addEventListener('pageshow', () => {
            void syncWakeLockState();
        });

        window.addEventListener('pagehide', () => {
            void releaseSentinel();
        });
    }

    function normalizeReason(reason) {
        return String(reason || '').trim();
    }

    async function acquire(reason) {
        const normalizedReason = normalizeReason(reason);
        if (!normalizedReason) {
            return sentinel;
        }

        installListeners();
        activeReasons.add(normalizedReason);
        return syncWakeLockState();
    }

    async function release(reason) {
        const normalizedReason = normalizeReason(reason);
        if (normalizedReason) {
            activeReasons.delete(normalizedReason);
        }

        installListeners();
        return syncWakeLockState();
    }

    async function syncReason(reason, active) {
        if (active) {
            return acquire(reason);
        }
        return release(reason);
    }

    installListeners();

    window.chatWakeLock = {
        syncReason,
        acquire,
        release,
        isSupported,
    };
})();
