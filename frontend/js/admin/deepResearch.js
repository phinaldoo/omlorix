(function () {
    if (typeof window.createSettingsPageController !== 'function') {
        window.initDeepResearchSettingsPage = () => {};
        window.teardownDeepResearchSettingsPage = () => {};
        return;
    }

    const t = (key, fallback) => typeof window.getTranslation === 'function'
        ? window.getTranslation(key, fallback)
        : (fallback !== undefined ? fallback : key);

    const backButton = document.getElementById('deepResearchSettingsBack');
    const CUSTOM_PROVIDER_KEYS = Object.freeze({
        searchFieldKey: 'websearch_search_provider',
        scrapeFieldKey: 'websearch_scrape_provider',
        searchValueKey: 'websearch_search_provider',
        scrapeValueKey: 'websearch_scrape_provider',
    });

    let reloadingOnProviderChange = false;
    const settingsController = window.createSettingsPageController({
        pageKey: 'deep_research',
        containerId: 'deepResearchSettingsFields',
        statusId: 'deepResearchSettingsStatus',
        stringDebounceMs: 600,
        loadErrorMessage: t('deep_research_settings_load_failed', 'Unable to load Deep Research settings.'),
        onError: (message) => window.notifyError?.(message),
        onRender: ({ schemaControls }) => {
            if (window.WebsearchProviderLogic?.attachProviderPairLogic) {
                window.WebsearchProviderLogic.attachProviderPairLogic(schemaControls, CUSTOM_PROVIDER_KEYS);
            }
        },
        onFieldSaved: ({ fieldKey }) => {
            // The mode controls which section is visible, while the native
            // provider controls the model options returned by the schema.
            // Reload only after either authoritative field was persisted.
            if (!['execution_mode', 'native_provider_id'].includes(fieldKey) || reloadingOnProviderChange) {
                return;
            }
            reloadingOnProviderChange = true;
            try {
                settingsController.teardown();
                settingsController.init();
            } finally {
                reloadingOnProviderChange = false;
            }
        },
        renderEmptyState: (target) => {
            const emptyMessage = document.createElement('p');
            emptyMessage.className = 'settings-empty';
            emptyMessage.textContent = t('deep_research_settings_empty', 'No Deep Research settings available yet.');
            target.appendChild(emptyMessage);
        },
    });

    const handleBackClick = () => {
        window.activateAdminPage?.('tools');
    };

    window.initDeepResearchSettingsPage = () => {
        settingsController.init();
        backButton?.addEventListener('click', handleBackClick);
    };

    window.teardownDeepResearchSettingsPage = () => {
        settingsController.teardown();
        backButton?.removeEventListener('click', handleBackClick);
    };
})();
