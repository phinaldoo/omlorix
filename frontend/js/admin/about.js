(function () {
    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    async function initAboutPage() {
        const versionTarget = document.getElementById("aboutVersionText");
        const updateTarget = document.getElementById("aboutVersionUpdate");
        if (versionTarget) {
            versionTarget.textContent = t('about_fetching_version', 'Fetching version…');
        }
        if (updateTarget) {
            updateTarget.hidden = true;
            updateTarget.textContent = "";
        }

        try {
            const response = await authedFetch("/api/v1/version");
            if (!response.ok) {
                notifyError(t('about_version_fetch_failed', 'Failed to fetch version.'));
            }
            const data = await response.json();
            const currentVersion = data.version;
            if (versionTarget) {
                versionTarget.textContent = currentVersion || t('about_version_unavailable', 'Unavailable');
            }

            const latestVersion = data.latest_version;
            const hasUpdate = Boolean(data.update_available && latestVersion);
            if (hasUpdate && updateTarget) {
                const template = t('about_new_version_available', 'New version available: {version}');
                updateTarget.textContent = template.replace('{version}', latestVersion);
                updateTarget.hidden = false;
            }
        } catch (error) {
            if (versionTarget) {
                versionTarget.textContent = t('about_version_unavailable', 'Unavailable');
            }
            notifyError(t('about_version_unavailable_error', 'Unable to load version.'), error);
        }
    }

    window.initAboutPage = initAboutPage;
})();