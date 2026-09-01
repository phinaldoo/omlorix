(function () {
    if (typeof window.createSettingsPageController !== 'function') {
        window.initWeatherToolSettingsPage = () => {};
        window.teardownWeatherToolSettingsPage = () => {};
        return;
    }

    const t = (key, fallback) => typeof window.getTranslation === 'function'
        ? window.getTranslation(key, fallback)
        : (fallback !== undefined ? fallback : key);

    const backButton = document.getElementById('weatherToolSettingsBack');
    const settingsController = window.createSettingsPageController({
        pageKey: 'weather_tool',
        containerId: 'weatherToolSettingsFields',
        statusId: 'weatherToolSettingsStatus',
        stringDebounceMs: 600,
        loadErrorMessage: t('weather_tool_settings_load_failed', 'Unable to load Weather settings.'),
        onError: (message) => window.notifyError?.(message),
        renderEmptyState: (target) => {
            const emptyMessage = document.createElement('p');
            emptyMessage.className = 'settings-empty';
            emptyMessage.textContent = t('weather_tool_settings_empty', 'No Weather settings available yet.');
            target.appendChild(emptyMessage);
        },
    });

    const handleBackClick = () => {
        window.activateAdminPage?.('tools');
    };

    window.initWeatherToolSettingsPage = () => {
        settingsController.init();
        backButton?.addEventListener('click', handleBackClick);
    };

    window.teardownWeatherToolSettingsPage = () => {
        settingsController.teardown();
        backButton?.removeEventListener('click', handleBackClick);
    };
})();
