// Variables
let _systemMq = null;
let _systemListener = null;
const ALLOWED_THEMES = new Set(["blue","green","coral","purple","teal","amber","mono"]);
const ALLOWED_MODES = new Set(["light","dark","system"]);

function sanitizeTheme(theme) {
  return ALLOWED_THEMES.has(theme) ? theme : "mono";
}

function sanitizeMode(mode) {
  return ALLOWED_MODES.has(mode) ? mode : "system";
}

function bootstrapThemeFromStorage() {
  try {
    const doc = document.documentElement;
    if (doc.dataset.theme && doc.dataset.mode) {
      return;
    }

    let fallbackTheme;
    try {
        fallbackTheme = sanitizeTheme(localStorage.getItem("theme"));
    } catch (error) {
        console.warn('Failed to read saved theme:', error);
        fallbackTheme = null;
    }

    let modePreference;
    try {
        modePreference = sanitizeMode(localStorage.getItem("mode"));
    } catch (error) {
        console.warn('Failed to read saved mode:', error);
        modePreference = null;
    }
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const effectiveMode = modePreference === "dark" || (modePreference === "system" && prefersDark) ? "dark" : "light";

    doc.setAttribute("data-theme", fallbackTheme);
    doc.setAttribute("data-mode", effectiveMode);
    doc.setAttribute("data-mode-preference", modePreference);
  } catch (_) {
    // ignore
  }
}

bootstrapThemeFromStorage();

function setTheme(modeSetting, options = {}) {
  const { persist = true } = options;

  // Persist user preference (light | dark | system)
  let finalMode = modeSetting ? sanitizeMode(modeSetting) : null;
  if (!finalMode) {
    try {
      finalMode = sanitizeMode(localStorage.getItem("mode"));
    } catch (error) {
      console.warn('Failed to read saved mode:', error);
      finalMode = null;
    }
  }
  // Ensure we always have a valid mode
  if (!finalMode) {
    finalMode = sanitizeMode(); // Get default mode from sanitizer
  }
  if (persist && finalMode) {
    try {
      localStorage.setItem("mode", finalMode);
    } catch (error) {
      console.warn('Failed to save mode:', error);
    }
  }

  // Prepare system listener
  if (!_systemMq) {
    _systemMq = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
  }

  // Helper to compute effective mode (always allow dark mode)
  const computeEffective = () => {
    if (finalMode === 'system') {
      const dark = _systemMq && _systemMq.matches;
      return dark ? 'dark' : 'light';
    }
    return finalMode;
  };

  // Apply effective mode to DOM
  const apply = () => {
    const root = document.documentElement;
    root.classList.add('theme-switching');
    root.setAttribute('data-mode-preference', finalMode);
    root.setAttribute("data-mode", computeEffective());
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        root.classList.remove('theme-switching');
      });
    });
  };

  // Manage system change listener when modeSetting === 'system'
  if (finalMode === 'system' && _systemMq) {
    if (_systemListener) {
      _systemMq.removeEventListener('change', _systemListener);
    }
    _systemListener = () => apply();
    _systemMq.addEventListener('change', _systemListener);
  } else if (_systemMq && _systemListener) {
    // Cleanup listener when leaving system mode
    _systemMq.removeEventListener('change', _systemListener);
    _systemListener = null;
  }

  apply();
}

function setColorTheme(theme, options = {}) {
  // The persisted color choice intentionally controls only --user-message-bg.
  // Global UI colors are fixed by the light/dark monochrome mode palette.
  const { persist = true } = options;
  const finalTheme = sanitizeTheme(theme);
  const root = document.documentElement;
  root.classList.add('theme-switching');
  root.setAttribute("data-theme", finalTheme);
  if (persist) {
    try {
      localStorage.setItem("theme", finalTheme);
    } catch (error) {
      console.warn('Failed to save theme:', error);
    }
  }

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      root.classList.remove('theme-switching');
    });
  });
}

function initTheme(theme, mode) {
  let savedTheme;
  try {
    savedTheme = sanitizeTheme(localStorage.getItem('theme'));
  } catch (error) {
    console.warn('Failed to read saved theme:', error);
    savedTheme = sanitizeTheme(); // Get default theme from sanitizer
  }
  let savedMode;
  try {
    savedMode = sanitizeMode(localStorage.getItem('mode'));
  } catch (error) {
    console.warn('Failed to read saved mode:', error);
    savedMode = sanitizeMode(); // Get default mode from sanitizer
  }

  const finalTheme = theme && ALLOWED_THEMES.has(theme) ? theme : savedTheme;
  const finalMode = mode && ALLOWED_MODES.has(mode) ? mode : savedMode;

  // Apply theme then mode
  setColorTheme(finalTheme);
  setTheme(finalMode);
}

initTheme();

window.addEventListener('storage', (event) => {
  if (!event || !event.key) return;
  if (event.key === 'mode') {
    const value = sanitizeMode(event.newValue);
    setTheme(value, { persist: false });
  } else if (event.key === 'theme') {
    const value = sanitizeTheme(event.newValue);
    setColorTheme(value, { persist: false });
  }
});
