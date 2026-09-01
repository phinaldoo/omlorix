(function () {
    if (typeof window.createSettingsPageController !== 'function') {
        window.initSecuritySettingsPage = () => {};
        window.teardownSecuritySettingsPage = () => {};
        return;
    }

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const renderEmptyState = (target) => {
        const emptyState = document.createElement('p');
        emptyState.className = 'settings-empty';
        emptyState.textContent = t('security_settings_empty', 'No security settings available yet.');
        target.appendChild(emptyState);
    };

    const renderErrorState = (target, message) => {
        const errorMessage = document.createElement('p');
        errorMessage.className = 'settings-error';
        errorMessage.textContent = message;
        target.appendChild(errorMessage);
    };

    const settingsContainer = document.getElementById('securitySettingsFields');
    const overrideBanner = document.createElement('div');
    overrideBanner.className = 'user-stats-warning-banner security-ip-override-banner';
    overrideBanner.hidden = true;
    overrideBanner.innerHTML = `
        ${Icons.withSvgAttributes("warning", { "aria-hidden": "true" })}
        <div class="user-stats-warning-content">
            <h4></h4>
            <p></p>
        </div>
    `;

    const syncIpOverrideStatus = async () => {
        if (!settingsContainer || !overrideBanner.parentNode) {
            return;
        }
        try {
            const response = await window.authedFetch('/api/v1/admin/ip-restrictions/status');
            if (!response.ok) {
                overrideBanner.hidden = true;
                return;
            }
            const status = await response.json();
            const disabled = Boolean(status?.disabled_by_environment);
            overrideBanner.hidden = !disabled;
            if (!disabled) {
                return;
            }
            const envVar = status?.environment_variable || 'OMLORIX_DISABLE_IP_RESTRICTIONS';
            const currentIp = status?.current_admin_ip || t('security_ip_override_unknown_ip', 'unknown');
            overrideBanner.querySelector('h4').textContent = t(
                'security_ip_override_title',
                'IP restrictions disabled by environment'
            );
            overrideBanner.querySelector('p').textContent = window.formatTranslation
                ? window.formatTranslation(
                    'security_ip_override_desc',
                    '{envVar} is active. IP restrictions and IP bans are bypassed until you turn it off. Current admin IP: {currentIp}.',
                    { envVar, currentIp }
                )
                : `${envVar} is active. IP restrictions and IP bans are bypassed until you turn it off. Current admin IP: ${currentIp}.`;
        } catch (_) {
            overrideBanner.hidden = true;
        }
    };

    if (settingsContainer?.parentNode) {
        settingsContainer.parentNode.insertBefore(overrideBanner, settingsContainer);
    }

    const securitySettingsController = window.createSettingsPageController({
        pageKey: 'security',
        containerId: 'securitySettingsFields',
        statusId: 'securitySettingsStatus',
        stringDebounceMs: 600,
        stringListDebounceMs: 600,
        loadErrorMessage: t('security_settings_load_failed', 'Unable to load security settings.'),
        renderEmptyState,
        renderError: renderErrorState,
        onError: (message) => notifyError?.(message),
    });

    window.initSecuritySettingsPage = () => {
        syncIpOverrideStatus();
        securitySettingsController.init();
    };

    window.teardownSecuritySettingsPage = () => {
        securitySettingsController.teardown();
    };
})();
