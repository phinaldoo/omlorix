(function () {
    // Serialize server writes so an older, slower request cannot overwrite a
    // newer theme selection. Each task handles its own failure, keeping the
    // queue available for the next preference change.
    let themePersistenceQueue = Promise.resolve();

    function getSavedMode() {
        try {
            return localStorage.getItem('mode') || 'system';
        } catch (error) {
            console.warn('Failed to read saved mode:', error);
            return 'system';
        }
    }

    function getEffectiveMode(preference) {
        if (preference === 'system') {
            const mq = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
            return mq && mq.matches ? 'dark' : 'light';
        }
        return preference;
    }

    /**
     * Synchronize the visual selection and accessible pressed state.
     * The profile menu stays open after a selection, matching the login menu.
     */
    function updateButtons(buttons, preference) {
        buttons.forEach((button) => {
            const isActive = button.dataset.theme === preference;
            button.classList.toggle('active', isActive);
            button.setAttribute('aria-pressed', String(isActive));
        });
    }

    function persistTheme(mode) {
        themePersistenceQueue = themePersistenceQueue.then(async () => {
            try {
                const response = await window.authedFetch('/api/v1/users/color-theme/update', {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ theme: mode })
                });

                if (!response.ok) {
                    window.notifyError?.(response.status);
                }
            } catch (error) {
                console.error('Failed to persist theme preference', error);
            }
        });

        return themePersistenceQueue;
    }

    function initThemeSelector() {
        const profileDropdown = document.getElementById('adminProfileDropdown');
        const buttons = profileDropdown
            ? Array.from(profileDropdown.querySelectorAll('.theme-btn[data-theme]'))
            : [];

        if (!buttons.length) return;

        buttons.forEach((button) => {
            button.addEventListener('click', () => {
                const mode = button.dataset.theme;
                if (!mode) return;

                if (typeof window.setTheme === 'function') {
                    window.setTheme(mode);
                } else {
                    try {
                        localStorage.setItem('mode', mode);
                    } catch (error) {
                        console.warn('Failed to save mode:', error);
                    }
                    document.documentElement.setAttribute('data-mode', getEffectiveMode(mode));
                    document.documentElement.setAttribute('data-mode-preference', mode);
                }

                updateButtons(buttons, mode);
                persistTheme(mode);
            });
        });

        // Keep the selector correct when another setting or browser tab updates
        // the shared theme preference while the admin page is open.
        const observer = new MutationObserver(() => {
            const preference = document.documentElement.getAttribute('data-mode-preference') || getSavedMode();
            updateButtons(buttons, preference);
        });

        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-mode-preference'] });

        const initialPreference = getSavedMode();
        updateButtons(buttons, initialPreference);
    }

    document.addEventListener('DOMContentLoaded', initThemeSelector);
})();
