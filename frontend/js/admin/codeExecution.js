(function () {
    if (typeof window.createSettingsPageController !== 'function') {
        window.initCodeExecutionSettingsPage = () => {};
        window.teardownCodeExecutionSettingsPage = () => {};
        return;
    }

    const t = (key, fallback) => typeof window.getTranslation === 'function'
        ? window.getTranslation(key, fallback)
        : (fallback !== undefined ? fallback : key);

    const backButton = document.getElementById('codeExecutionSettingsBack');
    const settingsController = window.createSettingsPageController({
        pageKey: 'code_execution',
        containerId: 'codeExecutionSettingsFields',
        statusId: 'codeExecutionSettingsStatus',
        stringDebounceMs: 600,
        loadErrorMessage: t('code_execution_settings_load_failed', 'Unable to load Code Execution settings.'),
        onError: (message) => window.notifyError?.(message),
        renderEmptyState: (target) => {
            const emptyMessage = document.createElement('p');
            emptyMessage.className = 'settings-empty';
            emptyMessage.textContent = t('code_execution_settings_empty', 'No Code Execution settings available yet.');
            target.appendChild(emptyMessage);
        },
    });

    const handleBackClick = () => {
        window.activateAdminPage?.('tools');
    };

    window.initCodeExecutionSettingsPage = () => {
        window.renderAdminServiceConnectionsSettingsRow?.('codeExecutionServiceConnectionsLink', {
            descriptionKey: 'service_connections_code_execution_row_desc',
            description: 'Manage service endpoints, weights, and availability checks for code execution.',
        });
        settingsController.init();
        backButton?.addEventListener('click', handleBackClick);
    };

    window.teardownCodeExecutionSettingsPage = () => {
        settingsController.teardown();
        document.getElementById('codeExecutionServiceConnectionsLink')?.replaceChildren();
        backButton?.removeEventListener('click', handleBackClick);
    };
})();
