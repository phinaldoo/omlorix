(() => {
    const DEFAULT_FONT_KEY = 'inter';
    const FONT_FAMILY_MAP = {
        inter: "var(--font-inter)",
        system: "var(--font-system)",
        verdana: "var(--font-verdana)",
        georgia: "var(--font-georgia)",
        times: "var(--font-times)",
        courier: "var(--font-courier)",
        roboto: "'Roboto', 'Helvetica Neue', Arial, sans-serif",
    };

    const FONT_LABEL_MAP = {
        inter: 'Inter',
        system: 'System',
        verdana: 'Verdana',
        georgia: 'Georgia',
        times: 'Times New Roman',
        courier: 'Courier New',
        roboto: 'Roboto',
        'open sans': 'Open Sans',
        lato: 'Lato',
        montserrat: 'Montserrat',
        merriweather: 'Merriweather',
        inconsolata: 'Inconsolata',
    };

    function normaliseKey(key) {
        return (key || '').toString().trim().toLowerCase();
    }

    function setFontFamily(key) {
        const normalized = FONT_FAMILY_MAP[normaliseKey(key)] ? normaliseKey(key) : DEFAULT_FONT_KEY;
        const stack = FONT_FAMILY_MAP[normalized];

        document.documentElement.style.setProperty('--app-font-family', stack);
        document.documentElement.dataset.fontFamily = normalized;

        const label = FONT_LABEL_MAP[normalized] || FONT_LABEL_MAP[DEFAULT_FONT_KEY];
        document.documentElement.dataset.fontFamilyLabel = label;

        if (normaliseKey(key) !== normalized) {
            try {
                localStorage.setItem('font-family', normalized);
            } catch (error) {
                console.warn('Failed to save font family:', error);
            }
        }

        return normalized;
    }

    function initFontFamily() {
        let stored;
        try {
            stored = normaliseKey(localStorage.getItem('font-family'));
        } catch (error) {
            console.warn('Failed to read saved font family:', error);
            stored = null;
        }
        const effective = setFontFamily(stored);
        if (stored !== effective) {
            try {
                localStorage.setItem('font-family', effective);
            } catch (error) {
                console.warn('Failed to save font family:', error);
            }
        }
    }

    function applyFontPreferences() {
        initFontFamily();
    }

    window.initFontFamily = initFontFamily;
    window.applyFontPreferences = applyFontPreferences;
    window.setFontFamilyPreference = (key) => {
        const normalized = setFontFamily(key);
        try {
            localStorage.setItem('font-family', normalized);
        } catch (error) {
            console.warn('Failed to save font family:', error);
        }
        return normalized;
    };

    applyFontPreferences();
})();
