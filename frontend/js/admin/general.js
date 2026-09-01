(function () {
    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    // Settings Schemas
    const generalSettingsRoot = document.getElementById('generalSettings');

    const generalSettingsController = window.createSettingsPageController({
        pageKey: 'general',
        containerId: generalSettingsRoot,
        stringDebounceMs: 600,
        stringListDebounceMs: 600,
        loadErrorMessage: t('general_settings_load_error', 'Unable to load general settings.'),
        renderError: (_, message) => notifyError?.(message),
        onError: (message) => notifyError?.(message),
    });

    window.initGeneralSettingsPage = () => {
        generalSettingsController.init();
    };

    window.teardownGeneralSettingsPage = () => {
        generalSettingsController.teardown();
    };
})();
