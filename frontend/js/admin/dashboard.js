(function () {
    // Cache DOM lookups once; bail early if the dashboard isn't on the page.
    const el = {
        page: document.getElementById('page-dashboard'),
        active: document.getElementById('dashboardActiveUsers'),
        activeCard: document.getElementById('dashboardActiveUsersCard'),
        peakConcurrent: document.getElementById('dashboardPeakConcurrentUsers'),
        peakConcurrentCard: document.getElementById('dashboardPeakConcurrentUsersCard'),
        peakConcurrentDescription: document.getElementById('dashboardPeakConcurrentUsersDescription'),
        pending: document.getElementById('dashboardPendingUsers'),
        pendingCard: document.getElementById('dashboardPendingUsersCard'),
        providerCard: document.getElementById('dashboardProvidersCard'),
        providerStatus: document.getElementById('dashboardProvidersStatus'),
        providerDetails: document.getElementById('dashboardProvidersDetails'),
        connectivityCard: document.getElementById('dashboardConnectivityCard'),
        connectivityStatus: document.getElementById('dashboardConnectivityStatus'),
        connectivityDetails: document.getElementById('dashboardConnectivityDetails'),
        modelsCard: document.getElementById('dashboardModelsCard'),
        modelsStatus: document.getElementById('dashboardModelsStatus'),
        modelsDetails: document.getElementById('dashboardModelsDetails'),
        notifications: document.getElementById('dashboardNotifications'),
        notificationsEmpty: document.getElementById('dashboardNotificationsEmpty'),
    };
    // Helpers --------------------------------------------------------------
    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const toNumberOrNull = (value) => {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : null;
    };

    const toNonNegativeInt = (value) => {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? Math.max(0, Math.floor(numeric)) : null;
    };

    const setText = (node, value) => {
        if (node) node.textContent = value;
    };

    const setCardVisibility = (cardEl, isVisible) => {
        if (!cardEl) return;
        cardEl.hidden = !isVisible;
        if (isVisible) {
            cardEl.removeAttribute('aria-hidden');
        } else {
            cardEl.setAttribute('aria-hidden', 'true');
        }
    };

    const createSpan = (className, text) => {
        const span = document.createElement('span');
        span.className = className;
        span.textContent = text;
        return span;
    };

    const navigateToAdminPage = (pageKey) => {
        if (typeof window.activateAdminPage === 'function') {
            window.activateAdminPage(pageKey);
        } else {
            window.location.assign(`/admin/${pageKey}`);
        }
    };

    const bindDashboardCardNavigation = () => {
        if (!el.page) return;
        const targets = [
            { card: el.activeCard, page: 'users' },
            { card: el.peakConcurrentCard, page: 'users' },
            { card: el.pendingCard, page: 'users' },
            { card: el.providerCard, page: 'providers' },
            { card: el.modelsCard, page: 'models' },
        ];

        targets.forEach(({ card, page }) => {
            if (!card) return;
            card.classList.add('cursor-pointer');
            card.setAttribute('role', 'button');
            card.setAttribute('tabindex', '0');

            const handleActivate = () => navigateToAdminPage(page);
            const handleKey = (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    handleActivate();
                }
            };

            card.addEventListener('click', handleActivate);
            card.addEventListener('keydown', handleKey);
        });
    };

    // Data loading --------------------------------------------------------
    async function initDashboardPage() {
        try {
            const response = await authedFetch('/api/v1/admin/dashboard', {
                method: 'GET',
            });
            if (!response.ok) {
                const statusMsg = t('dashboard_load_failed_status', 'Failed to load dashboard data (status {status}).')
                    .replace('{status}', response.status);
                notifyError(statusMsg);
                return;
            }
            const payload = await response.json();
            renderDashboardMetrics(payload);
            renderDashboardNotifications(payload?.notifications);
        } catch (error) {
            notifyError(t('dashboard_load_failed', 'Failed to load dashboard data.'), error);
            renderDashboardMetrics();
            renderDashboardNotifications();
        }
    }
    window.initDashboardPage = initDashboardPage;

    bindDashboardCardNavigation();

    
    // Metrics -------------------------------------------------------------
    function renderDashboardMetrics(data = {}) {
        setText(el.active, toNumberOrNull(data?.active_user_count) ?? '—');
        const peakConcurrentUsers = toNumberOrNull(data?.max_concurrent_users_last_week);
        const hasPeakConcurrentState = Object.prototype.hasOwnProperty.call(data, 'max_concurrent_users_is_partial');
        setText(el.peakConcurrent, peakConcurrentUsers ?? (data?.max_concurrent_users_last_week === 0 ? '0' : '—'));
        updatePeakConcurrentDescription(data?.max_concurrent_users_is_partial, hasPeakConcurrentState);
        setText(el.pending, toNumberOrNull(data?.pending_user_count) ?? '—');

        const providersTotal = toNonNegativeInt(data?.providers_total_count);
        const hasProvidersConfigured = Number.isFinite(providersTotal) && providersTotal > 0;
        setCardVisibility(el.providerCard, hasProvidersConfigured);
        updateProviderAvailability(
            data?.providers_available,
            toNonNegativeInt(data?.providers_down_count),
            hasProvidersConfigured,
        );
        const connectivityEnabled = data?.internet_connectivity_check_enabled !== false;
        updateConnectivityStatus(data?.internet_connectivity, connectivityEnabled);
        const modelsTotal = toNonNegativeInt(data?.models_total_count);
        const hasModelsConfigured = Number.isFinite(modelsTotal) && modelsTotal > 0;
        setCardVisibility(el.modelsCard, hasModelsConfigured);
        updateModelsHealthStatus(
            data?.models_healthy,
            toNonNegativeInt(data?.models_error_count),
            hasModelsConfigured,
        );
    }

    function updatePeakConcurrentDescription(isPartialWindow, hasPeakConcurrentState) {
        if (!el.peakConcurrentDescription) return;

        el.peakConcurrentDescription.hidden = !hasPeakConcurrentState;
        if (!hasPeakConcurrentState) {
            return;
        }

        el.peakConcurrentDescription.textContent = isPartialWindow === false
            ? t(
                'dashboard_peak_concurrent_users_desc',
                'Highest unique active users in any 5-minute window during the last 7 days.',
            )
            : t(
                'dashboard_peak_concurrent_users_partial',
                'Collecting up to 7 days of history.',
            );
    }

    function updateProviderAvailability(providersAvailable, providersDownCount, hasProvidersConfigured = true) {
        if (!hasProvidersConfigured) {
            return;
        }
        const statusEl = el.providerStatus;
        const detailsEl = el.providerDetails;
        if (!statusEl || !detailsEl) return;

        statusEl.textContent = '—';
        statusEl.innerHTML = '—';
        statusEl.removeAttribute('data-status');
        detailsEl.textContent = '';
        detailsEl.removeAttribute('data-status');

        const hasDownCount = Number.isFinite(providersDownCount);

        if ((hasDownCount && providersDownCount === 0) || (!hasDownCount && providersAvailable === true)) {
            statusEl.innerHTML = `<span class="dashboard-status-icon" aria-hidden="true">${Icons.check}</span>`;
            statusEl.setAttribute('data-status', 'ok');
            detailsEl.textContent = t('dashboard_providers_all_ok', 'All providers are reachable.');
            detailsEl.setAttribute('data-status', 'ok');
            return;
        }

        if ((hasDownCount && providersDownCount > 0) || providersAvailable === false) {
            const count = Math.max(1, Math.floor(providersDownCount ?? 1));
            statusEl.innerHTML = `<span class="dashboard-status-icon" aria-hidden="true">${Icons.exclamation}</span>`;
            statusEl.setAttribute('data-status', 'alert');
            const detailsTemplate = count === 1
                ? t('dashboard_providers_attention_single', '{count} provider needs your attention')
                : t('dashboard_providers_attention_plural', '{count} providers need your attention');
            detailsEl.textContent = detailsTemplate.replace('{count}', count);
            detailsEl.setAttribute('data-status', 'alert');
            return;
        }

        statusEl.textContent = t('dashboard_providers_availability_unknown', 'Availability unknown');
        detailsEl.textContent = t('dashboard_providers_status_unknown', 'We could not retrieve the current provider status.');
    }

    function updateConnectivityStatus(connectivityFlag, isEnabled = true) {
        const cardEl = el.connectivityCard;
        const statusEl = el.connectivityStatus;
        const detailsEl = el.connectivityDetails;
        if (!statusEl || !detailsEl) return;

        if (cardEl) {
            cardEl.hidden = !isEnabled;
            if (isEnabled) {
                cardEl.removeAttribute('aria-hidden');
            } else {
                cardEl.setAttribute('aria-hidden', 'true');
            }
        }

        if (!isEnabled) {
            return;
        }

        statusEl.textContent = '—';
        statusEl.innerHTML = '—';
        statusEl.removeAttribute('data-status');
        detailsEl.textContent = '';
        detailsEl.removeAttribute('data-status');

        if (connectivityFlag === true) {
            statusEl.innerHTML = `<span class="dashboard-status-icon" aria-hidden="true">${Icons.check}</span>`;
            statusEl.setAttribute('data-status', 'ok');
            detailsEl.textContent = t('dashboard_connectivity_ok_details', 'External services are reachable.');
            detailsEl.setAttribute('data-status', 'ok');
            return;
        }

        if (connectivityFlag === false) {
            statusEl.innerHTML = `<span class="dashboard-status-icon" aria-hidden="true">${Icons.exclamation}</span>`;
            statusEl.setAttribute('data-status', 'alert');
            detailsEl.textContent = t('dashboard_connectivity_down_details', 'We cannot reach the internet. Please check your network.');
            detailsEl.setAttribute('data-status', 'alert');
            return;
        }

        statusEl.textContent = t('dashboard_status_unknown', 'Status unknown');
        detailsEl.textContent = t('dashboard_connectivity_unknown_desc', 'Connectivity diagnostics are not available.');
    }

    function updateModelsHealthStatus(modelsHealthy, modelsErrorCount, hasModelsConfigured = true) {
        if (!hasModelsConfigured) {
            return;
        }
        const statusEl = el.modelsStatus;
        const detailsEl = el.modelsDetails;
        if (!statusEl || !detailsEl) return;

        statusEl.textContent = '—';
        statusEl.innerHTML = '—';
        statusEl.removeAttribute('data-status');
        detailsEl.textContent = '';
        detailsEl.removeAttribute('data-status');

        const hasErrorCount = Number.isFinite(modelsErrorCount);

        if ((hasErrorCount && modelsErrorCount === 0) || (!hasErrorCount && modelsHealthy === true)) {
            statusEl.innerHTML = `<span class="dashboard-status-icon" aria-hidden="true">${Icons.check}</span>`;
            statusEl.setAttribute('data-status', 'ok');
            detailsEl.textContent = t('dashboard_models_all_ok', 'All models are operating normally.');
            detailsEl.setAttribute('data-status', 'ok');
            return;
        }

        if ((hasErrorCount && modelsErrorCount > 0) || modelsHealthy === false) {
            const count = Math.max(1, Math.floor(modelsErrorCount ?? 1));
            statusEl.innerHTML = `<span class="dashboard-status-icon" aria-hidden="true">${Icons.warning}</span>`;
            statusEl.setAttribute('data-status', 'warning');
            const warningTemplate = count === 1
                ? t('dashboard_models_warning_single', '{count} model shows elevated error rates')
                : t('dashboard_models_warning_plural', '{count} models show elevated error rates');
            detailsEl.textContent = warningTemplate.replace('{count}', count);
            detailsEl.setAttribute('data-status', 'warning');
            return;
        }

        statusEl.textContent = t('dashboard_status_unknown', 'Status unknown');
        detailsEl.textContent = t('dashboard_models_diagnostics_unavailable', 'Model health diagnostics are not available.');
    }

    // Notifications -------------------------------------------------------
    function renderDashboardNotifications(notifications) {
        if (!el.notifications) return;

        const list = Array.isArray(notifications) ? notifications : [];
        el.notifications.innerHTML = '';

        if (!list.length) {
            if (el.notificationsEmpty) {
                el.notificationsEmpty.hidden = false;
                el.notifications.appendChild(el.notificationsEmpty);
            }
            return;
        }

        if (el.notificationsEmpty) {
            el.notificationsEmpty.hidden = true;
        }

        const fragment = document.createDocumentFragment();

        list.forEach(({ category, message, timestamp }) => {
            const item = document.createElement('article');
            item.className = 'dashboard-notification-item';

            const row = document.createElement('div');
            row.className = 'dashboard-notification-row';
            row.append(
                createSpan('dashboard-notification-category', formatNotificationCategory(category)),
                createSpan('dashboard-notification-message', message || t('dashboard_notifications_no_message', 'No message provided.')),
                createSpan('dashboard-notification-timestamp', formatNotificationTimestamp(timestamp)),
            );

            item.appendChild(row);

            fragment.appendChild(item);
        });

        el.notifications.appendChild(fragment);
    }

    // Formatters ----------------------------------------------------------
    function formatNotificationTimestamp(value) {
        const unknownLabel = t('dashboard_notifications_unknown_time', 'Unknown time');
        if (!value) return unknownLabel;
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return unknownLabel;
        return date.toLocaleString([], {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function formatNotificationCategory(value) {
        if (typeof window.formatEnumLabel === 'function') {
            return window.formatEnumLabel(value, t('dashboard_notification_category_default', 'GENERAL'));
        }
        if (!value) return t('dashboard_notification_category_default', 'GENERAL');
        return String(value).replace(/_/g, ' ').trim().toUpperCase();
    }
})();
