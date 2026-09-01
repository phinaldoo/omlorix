(function () {
    if (typeof window.createSettingsPageController !== 'function') {
        window.initCreateSlidePresentationSettingsPage = () => {};
        window.teardownCreateSlidePresentationSettingsPage = () => {};
        return;
    }

    const t = (key, fallback) => typeof window.getTranslation === 'function'
        ? window.getTranslation(key, fallback)
        : (fallback !== undefined ? fallback : key);

    const backButton = document.getElementById('createSlidePresentationSettingsBack');
    const settingsController = window.createSettingsPageController({
        pageKey: 'slide_presentation',
        containerId: 'createSlidePresentationSettingsFields',
        statusId: 'createSlidePresentationSettingsStatus',
        stringDebounceMs: 600,
        loadErrorMessage: t('slide_presentation_settings_load_failed', 'Unable to load Slide Presentation settings.'),
        onError: (message) => window.notifyError?.(message),
        renderEmptyState: (target) => {
            const emptyMessage = document.createElement('p');
            emptyMessage.className = 'settings-empty';
            emptyMessage.textContent = t('slide_presentation_settings_empty', 'No Slide Presentation settings available yet.');
            target.appendChild(emptyMessage);
        },
    });

    const handleBackClick = () => {
        window.activateAdminPage?.('tools');
    };

    window.initCreateSlidePresentationSettingsPage = () => {
        // Renderer connection fields stay on the shared service connections page, so
        // expose the same shortcut here that other service-backed tools provide.
        window.renderAdminServiceConnectionsSettingsRow?.('createSlidePresentationServiceConnectionsLink', {
            descriptionKey: 'service_connections_slide_render_row_desc',
            description: 'Manage renderer service endpoints, weights, and availability checks for presentation rendering.',
        });
        settingsController.init();
        backButton?.addEventListener('click', handleBackClick);
    };

    window.teardownCreateSlidePresentationSettingsPage = () => {
        settingsController.teardown();
        document.getElementById('createSlidePresentationServiceConnectionsLink')?.replaceChildren();
        backButton?.removeEventListener('click', handleBackClick);
    };
})();
