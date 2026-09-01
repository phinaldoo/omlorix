document.addEventListener('DOMContentLoaded', () => {

    const menuToggle = document.getElementById('menuToggle');
    const dropdownMenu = document.getElementById('dropdownMenu');
    const themeButtons = document.querySelectorAll('.theme-btn');
    const languageSelect = document.getElementById('languageSelect');

    function openLanguageSelectNative() {
        if (!languageSelect) {
            return;
        }

        if (typeof languageSelect.showPicker === 'function') {
            try {
                languageSelect.showPicker();
                return;
            } catch (_) {
                // Fall through to synthetic pointer events below.
            }
        }

        const pointerDown = new PointerEvent('pointerdown', {
            bubbles: true,
            cancelable: true,
            pointerType: 'mouse',
            isPrimary: true,
            button: 0,
            buttons: 1,
        });
        const mouseDown = new MouseEvent('mousedown', {
            bubbles: true,
            cancelable: true,
            button: 0,
            buttons: 1,
        });
        const mouseUp = new MouseEvent('mouseup', {
            bubbles: true,
            cancelable: true,
            button: 0,
        });
        const click = new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            button: 0,
        });

        languageSelect.dispatchEvent(pointerDown);
        languageSelect.dispatchEvent(mouseDown);
        languageSelect.dispatchEvent(mouseUp);
        languageSelect.dispatchEvent(click);
    }

    const loginDropdownController = window.createDropdownController?.({
        id: 'login-settings-dropdown',
        trigger: menuToggle,
        dropdown: dropdownMenu,
        root: menuToggle?.closest('.menu-container'),
        dropdownOpenClass: 'active',
        inert: true,
        manageFocusable: true,
        closeOnFocusOutside: true,
        outsideEvents: ['click', 'touchstart'],
        focusDelay: 50,
        focusOnOpen: () => themeButtons[0] || languageSelect,
        onOpen: () => {
            if (typeof getCurrentMode === 'function' && typeof updateActiveThemeButton === 'function') {
                updateActiveThemeButton(getCurrentMode());
            }
        },
    });

    function setDropdownMenuOpen(isOpen, { restoreFocus = false } = {}) {
        if (!loginDropdownController) {
            return;
        }

        if (isOpen) {
            loginDropdownController.open({ reason: 'api' });
            return;
        }

        loginDropdownController.close({ reason: 'api', restoreFocus });
    }

    setDropdownMenuOpen(false);
    window.setLoginDropdownOpen = setDropdownMenuOpen;

    /* ----------------------- */
    /* --- THEME SELECTION  --- */
    /* ----------------------- */
    const getCurrentMode = () => {
        let saved;
        try {
            saved = localStorage.getItem('mode');
        } catch (error) {
            console.warn('Failed to read saved mode:', error);
            saved = null;
        }
        return saved ? saved : 'system';
    };

    const updateActiveThemeButton = (mode) => {
        themeButtons.forEach(btn => {
            const isActive = btn.dataset.theme === mode;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-pressed', String(isActive));
        });
    };

    // Initialize active state based on saved mode (managed by js/common/theme.js)
    updateActiveThemeButton(getCurrentMode());

    // Wire up click handlers to switch theme mode via shared setTheme()
    themeButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const mode = btn.dataset.theme; // expected: 'light' | 'dark' | 'system'
            if (typeof window.setTheme === 'function') {
                window.setTheme(mode);
            } else {
                // Fallback: persist directly if setTheme is not yet available
                try {
                    localStorage.setItem('mode', mode);
                } catch (error) {
                    console.warn('Failed to save mode:', error);
                }
                document.documentElement.setAttribute('data-mode', mode === 'system' ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : mode);
            }
            updateActiveThemeButton(mode);
        });
    });

    // Language options are populated globally via `js/common/language.js`.
    if (languageSelect) {
        languageSelect.addEventListener('keydown', (e) => {
            if (e.altKey || e.ctrlKey || e.metaKey) {
                return;
            }

            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openLanguageSelectNative();
            }
        });
    }
});
