/**
* Theme Settings Manager
* Handles Theme Mode (light/dark/system) and user-message color selection
* Syncs settings with backend API and localStorage
*/

const SELECTORS = {
    themeModeButtons: '.theme-mode-input',
    themeModeContainer: '.theme-mode-button-container-item',
    colorOptions: '.color-option'
};

const CLASSES = {
    active: 'active',
    selected: 'selected'
};

const MODES = new Set(['light', 'dark', 'system']);
const COLORS = new Set(['blue', 'green', 'coral', 'purple', 'teal', 'amber', 'mono']);

let currentThemeMode = 'system';
let currentColorTheme = 'mono';

const each = (selector, fn) => document.querySelectorAll(selector).forEach(fn);
const normalizeThemeMode = (mode) => (MODES.has(mode) ? mode : 'system');
const normalizeColorTheme = (color) => (COLORS.has(color) ? color : 'mono');

/**
 * Save theme settings to backend
 */
async function saveThemeSettings(theme, color_theme) {
    try {
        const response = await window.authedFetch(`/api/v1/users/color-theme/update`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ theme, color_theme })
        });

        if (!response.ok) {
            notifyError(response.status);
        }

        return (await response.json()).status === 'success';
    } catch (error) {
        console.error('Error saving theme settings:', error);
        return false;
    }
}

/**
 * Update Theme Mode UI
 */
function updateThemeModeUI(mode) {
    each(SELECTORS.themeModeButtons, (button) => {
        const isSelected = button.dataset.themeMode === mode;
        const container = button.closest(SELECTORS.themeModeContainer);
        button.checked = isSelected;
        container?.classList.toggle(CLASSES.active, isSelected);
    });
}

/**
 * Update user-message color UI
 */
function updateColorThemeUI(color) {
    each(SELECTORS.colorOptions, (option) => {
        const isSelected = option.dataset.color === color;
        option.classList.toggle(CLASSES.selected, isSelected);
        option.setAttribute('aria-pressed', String(isSelected));
    });
}

/**
 * Handle Theme Mode Change
 */
function handleThemeModeChange(mode) {
    const nextMode = MODES.has(mode) ? mode : null;
    if (!nextMode) return;
    currentThemeMode = nextMode;
    updateThemeModeUI(nextMode);
    setTheme(nextMode);
    saveThemeSettings(nextMode, currentColorTheme);
}

/**
 * Handle user-message color change
 */
function handleColorThemeChange(color) {
    const nextColor = COLORS.has(color) ? color : null;
    if (!nextColor) return;
    currentColorTheme = nextColor;
    updateColorThemeUI(nextColor);
    setColorTheme(nextColor);
    saveThemeSettings(currentThemeMode, nextColor);
}

/**
 * Initialize Theme Settings from localStorage/DOM
 */
function initializeThemeSettings(theme_mode, color_theme) {
    currentThemeMode = normalizeThemeMode(theme_mode);
    currentColorTheme = normalizeColorTheme(color_theme);
    updateThemeModeUI(currentThemeMode);
    updateColorThemeUI(currentColorTheme);
}


// Theme Mode buttons
each(SELECTORS.themeModeButtons, (button) => {
    button.addEventListener('change', () => {
        if (!button.checked) return;
        handleThemeModeChange(button.dataset.themeMode);
    });
});

// User-message color options
each(SELECTORS.colorOptions, (option) => {
    const run = (event) => {
        if (!option.dataset.color) return;
        if (event.type === 'click' || event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            handleColorThemeChange(option.dataset.color);
        }
    };

    option.addEventListener('click', run);
    option.addEventListener('keydown', run);
});
