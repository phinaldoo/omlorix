(function () {
    const tableContainer = document.getElementById('securityIpsTable');
    const addButton = document.getElementById('securityIpsAddButton');
    const analyticsPage = document.getElementById('page-security-ip-analytics');

    if ((!tableContainer || !addButton) && !analyticsPage) {
        window.initSecurityIpsPage = () => {};
        window.teardownSecurityIpsPage = () => {};
        window.initSecurityIpAnalyticsPage = () => {};
        window.teardownSecurityIpAnalyticsPage = () => {};
        return;
    }

    const DEFAULT_ERROR = 'Request failed.';
    let tableBound = false;
    let modalRefs = null;
    let paginationRefs = null;
    let countryChart = null;
    let analyticsEventsBound = false;

    const state = {
        settings: {
            enabled: false,
            regulatory_confirmed: false,
        },
        overview: null,
        blockedIps: [],
        blockedIpsPage: 1,
        blockedIpsPerPage: 50,
        blockedIpsTotal: 0,
        blockedIpsTotalPages: 1,
        period: 30,
        eventPage: 1,
        eventPerPage: 25,
        events: null,
        filters: {
            ip_address: '',
            country_code: '',
            event_type: '',
            event_source: '',
        },
    };

    const el = {
        analyticsSection: document.getElementById('securityIpsAnalyticsSection'),
        enableToggle: document.getElementById('securityIpsStatsEnableToggle'),
        retentionInput: document.getElementById('securityIpsStatsRetentionInput'),
        periodSelect: document.getElementById('securityIpsStatsPeriodSelect'),
        refreshBtn: document.getElementById('securityIpsStatsRefreshBtn'),
        blockedCount: document.getElementById('securityIpsStatsBlockedCount'),
        countryCount: document.getElementById('securityIpsStatsCountryCount'),
        attemptCount: document.getElementById('securityIpsStatsAttemptCount'),
        topCountry: document.getElementById('securityIpsStatsTopCountry'),
        topCountryMeta: document.getElementById('securityIpsStatsTopCountryMeta'),
        countryTableBody: document.getElementById('securityIpsCountryTableBody'),
        recentEvents: document.getElementById('securityIpsRecentEvents'),
        chartCanvas: document.getElementById('securityIpsCountryChart'),
        providerStatus: document.getElementById('securityIpsStatsProviderStatus'),
        range: document.getElementById('securityIpsStatsRange'),
        retentionWarning: document.getElementById('securityIpsStatsRetentionWarning'),
        ipFilter: document.getElementById('securityIpsStatsIpFilter'),
        countryFilter: document.getElementById('securityIpsStatsCountryFilter'),
        eventFilter: document.getElementById('securityIpsStatsEventFilter'),
        sourceFilter: document.getElementById('securityIpsStatsSourceFilter'),
        applyFiltersBtn: document.getElementById('securityIpsStatsApplyFiltersBtn'),
        clearFiltersBtn: document.getElementById('securityIpsStatsClearFiltersBtn'),
        exportBtn: document.getElementById('securityIpsStatsExportBtn'),
        importBtn: document.getElementById('securityIpsStatsImportBtn'),
        importInput: document.getElementById('securityIpsStatsImportInput'),
        deleteBtn: document.getElementById('securityIpsStatsDeleteBtn'),
        eventsPagination: document.getElementById('securityIpsStatsEventsPagination'),
        eventsPrev: document.getElementById('securityIpsStatsEventsPrev'),
        eventsNext: document.getElementById('securityIpsStatsEventsNext'),
        eventsPageInfo: document.getElementById('securityIpsStatsEventsPageInfo'),
        regulatoryModal: document.getElementById('securityIpsStatsRegulatoryModal'),
        regulatoryCheckbox: document.getElementById('securityIpsStatsRegulatoryCheckbox'),
        regulatoryDocumentationInput: document.getElementById('securityIpsStatsRegulatoryDocumentation'),
        regulatoryConfirmBtn: document.getElementById('securityIpsStatsRegulatoryConfirmBtn'),
        regulatoryCancelBtn: document.getElementById('securityIpsStatsRegulatoryCancelBtn'),
        disableModal: document.getElementById('securityIpsStatsDisableModal'),
        disableConfirmBtn: document.getElementById('securityIpsStatsDisableConfirmBtn'),
        disableCancelBtn: document.getElementById('securityIpsStatsDisableCancelBtn'),
    };

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const formatT = (key, fallback, vars = {}) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        let text = t(key, fallback);
        Object.entries(vars).forEach(([name, value]) => {
            text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), value);
        });
        return text;
    };

    /**
     * Upgrade every analytics select with the same accessible custom control
     * used by backend-schema fields. The native selects remain authoritative,
     * which keeps all existing request-building and change handlers intact.
     */
    const initializeAnalyticsCustomSelects = () => {
        if (typeof window.upgradeAdminSingleSelect !== 'function') {
            return;
        }

        const allLabel = t('security_ips_stats_filter_all', 'All');
        const selectConfigs = [
            {
                select: el.periodSelect,
                field: {
                    key: 'security_ips_stats_period',
                    placeholder: t('admin_select_placeholder_single', 'Select an option...'),
                },
            },
            {
                select: el.countryFilter,
                field: {
                    key: 'security_ips_stats_country_filter',
                    placeholder: allLabel,
                    emptyValueIsOption: true,
                },
            },
            {
                select: el.eventFilter,
                field: {
                    key: 'security_ips_stats_event_filter',
                    placeholder: allLabel,
                    emptyValueIsOption: true,
                },
            },
            {
                select: el.sourceFilter,
                field: {
                    key: 'security_ips_stats_source_filter',
                    placeholder: allLabel,
                    emptyValueIsOption: true,
                },
            },
        ];

        selectConfigs.forEach(({ select, field }) => {
            /* Page navigation can initialize this controller more than once. */
            if (!select || select._singleSelect?.wrapper?.parentNode) {
                return;
            }
            window.upgradeAdminSingleSelect(select, field);
        });
    };

    /** Refresh generated menu options and its visible selected-value summary. */
    const syncAnalyticsCustomSelect = (select, { refreshOptions = false } = {}) => {
        const meta = select?._singleSelect;
        if (refreshOptions) {
            meta?.refreshOptions?.();
        }
        meta?.syncFromSelect?.();
    };

    const authedFetch = (input, init = {}) => {
        const executor = typeof window !== 'undefined' && typeof window.authedFetch === 'function'
            ? window.authedFetch
            : fetch;
        return executor(input, init);
    };

    const readJsonSafe = async (response) => {
        try {
            return await response.json();
        } catch (error) {
            return null;
        }
    };

    const buildResponseError = async (response, fallback = DEFAULT_ERROR) => {
        const payload = await readJsonSafe(response);
        const message = payload?.detail || payload?.message || fallback;
        const error = new Error(message);
        error.status = response.status;
        error.payload = payload;
        return error;
    };

    const animateValue = (element, target) => {
        const end = parseInt(target, 10);
        if (!element || !end) {
            if (element) element.textContent = String(target);
            return;
        }
        const duration = 600;
        const start = performance.now();
        const easeOutQuart = (t) => 1 - Math.pow(1 - t, 4);
        const step = (now) => {
            const progress = Math.min((now - start) / duration, 1);
            element.textContent = String(Math.round(easeOutQuart(progress) * end));
            if (progress < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    };

    const formatDateTime = (value) => {
        if (!value) {
            return '—';
        }
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return '—';
        }
        return date.toLocaleString(undefined, {
            dateStyle: 'medium',
            timeStyle: 'short',
        });
    };

    const getRemainingDurationDays = (entry) => {
        const expiresAt = new Date(entry?.expires_at || '');
        if (Number.isNaN(expiresAt.getTime())) {
            return 30;
        }
        const millisecondsPerDay = 24 * 60 * 60 * 1000;
        const remainingDays = Math.ceil((expiresAt.getTime() - Date.now()) / millisecondsPerDay);
        return Math.min(365, Math.max(1, remainingDays || 1));
    };

    const getCountryDisplayNames = () => {
        try {
            const locale = document.documentElement?.lang || undefined;
            return new Intl.DisplayNames(locale ? [locale] : undefined, { type: 'region' });
        } catch (error) {
            return null;
        }
    };

    const countryNames = getCountryDisplayNames();

    const getCountryLabel = (countryCode) => {
        if (!countryCode) {
            return t('security_ips_country_unknown', 'Unknown');
        }
        const normalized = String(countryCode).trim().toUpperCase();
        return countryNames?.of(normalized) || normalized;
    };

    const getActivityLabel = (level) => {
        if (level === 'high') {
            return t('security_ips_stats_risk_high', 'High');
        }
        if (level === 'medium') {
            return t('security_ips_stats_risk_medium', 'Medium');
        }
        return t('security_ips_stats_risk_low', 'Low');
    };

    const getEventLabel = (eventType) => {
        const labels = {
            request_denied: t('security_ips_stats_event_request_denied', 'Request denied'),
            rate_limited: t('security_ips_stats_event_rate_limited', 'Rate limited'),
            ban_created: t('security_ips_stats_event_ban_created', 'Ban created'),
            ban_removed: t('security_ips_stats_event_ban_removed', 'Ban removed'),
        };
        return labels[eventType] || eventType || '—';
    };

    const getProviderStatusLabel = (status) => {
        /*
         * Keep translation keys explicit and statically discoverable. Provider
         * status is a small backend enum, so a switch also gives unexpected
         * values a safe, translated fallback instead of displaying an internal
         * status token directly.
         */
        switch (status) {
            case 'configured':
                return t('security_ips_stats_provider_configured', 'Configured');
            case 'disabled':
                return t('security_ips_stats_provider_disabled', 'Analytics disabled');
            case 'missing':
            default:
                return t('security_ips_stats_provider_missing', 'Not configured');
        }
    };

    const buildAnalyticsParams = (includePage = false) => {
        const params = new URLSearchParams({ days: String(state.period) });
        Object.entries(state.filters).forEach(([key, value]) => {
            if (value) params.set(key, value);
        });
        if (includePage) {
            params.set('page', String(state.eventPage));
            params.set('per_page', String(state.eventPerPage));
        }
        return params;
    };

    const getRetentionDaysFromInput = () => {
        const rawValue = el.retentionInput?.value;
        const parsedValue = Number.parseInt(rawValue, 10);
        if (!Number.isInteger(parsedValue) || parsedValue < 1 || parsedValue > 3650) {
            return null;
        }
        return parsedValue;
    };

    const updateRegulatoryConfirmState = () => {
        if (!el.regulatoryConfirmBtn) {
            return;
        }
        const hasConfirmation = Boolean(el.regulatoryCheckbox?.checked);
        const hasDocumentation = Boolean(el.regulatoryDocumentationInput?.value?.trim());
        const hasValidRetention = getRetentionDaysFromInput() !== null;
        el.regulatoryConfirmBtn.disabled = !(hasConfirmation && hasDocumentation && hasValidRetention);
    };

    const renderEmptyState = () => {
        tableContainer.innerHTML = '';
        const empty = window.createAdminEmptyPlaceholder({
            title: t('security_ips_empty_title', 'No blocked IP addresses'),
            description: t('security_ips_empty_desc', 'When you block an IP address, it will appear here. Blocks automatically expire after 30 days.'),
            icon: Icons.protection,
            className: 'provider-empty-state security-ips-empty',
        });

        tableContainer.appendChild(empty);
    };

    const ensurePagination = () => {
        if (paginationRefs) {
            return paginationRefs;
        }

        const root = document.createElement('div');
        root.className = 'stats-pagination security-ips-pagination';
        root.hidden = true;

        const prevBtn = document.createElement('button');
        prevBtn.type = 'button';
        prevBtn.className = 'stats-pagination-btn';
        prevBtn.setAttribute('aria-label', t('pagination_previous_aria', 'Previous page'));
        prevBtn.textContent = t('btn_previous', 'Previous');

        const info = document.createElement('span');
        info.className = 'stats-pagination-info';

        const nextBtn = document.createElement('button');
        nextBtn.type = 'button';
        nextBtn.className = 'stats-pagination-btn';
        nextBtn.setAttribute('aria-label', t('pagination_next_aria', 'Next page'));
        nextBtn.textContent = t('btn_next', 'Next');

        prevBtn.addEventListener('click', async () => {
            if (state.blockedIpsPage <= 1) {
                return;
            }
            state.blockedIpsPage -= 1;
            try {
                await loadBlockedIps();
            } catch (error) {
                notifyError?.(error?.message || t('security_ips_load_error', 'Failed to load blocked IPs.'));
            }
        });

        nextBtn.addEventListener('click', async () => {
            if (state.blockedIpsPage >= state.blockedIpsTotalPages) {
                return;
            }
            state.blockedIpsPage += 1;
            try {
                await loadBlockedIps();
            } catch (error) {
                notifyError?.(error?.message || t('security_ips_load_error', 'Failed to load blocked IPs.'));
            }
        });

        root.appendChild(prevBtn);
        root.appendChild(info);
        root.appendChild(nextBtn);
        tableContainer.insertAdjacentElement('afterend', root);
        paginationRefs = { root, prevBtn, info, nextBtn };
        return paginationRefs;
    };

    const renderPagination = () => {
        const refs = ensurePagination();
        const totalPages = Math.max(1, Number(state.blockedIpsTotalPages) || 1);
        const page = Math.min(Math.max(1, Number(state.blockedIpsPage) || 1), totalPages);
        refs.root.hidden = totalPages <= 1;
        refs.prevBtn.disabled = page <= 1;
        refs.nextBtn.disabled = page >= totalPages;
        refs.info.textContent = formatT('admin_page_of', 'Page {page} of {total}', {
            page: String(page),
            total: String(totalPages),
        });
    };

    const renderList = (items = []) => {
        tableContainer.innerHTML = '';
        tableContainer.removeAttribute('role');
        tableContainer.removeAttribute('aria-label');
        if (!items.length) {
            renderEmptyState();
            renderPagination();
            return;
        }

        tableContainer.setAttribute('role', 'table');
        tableContainer.setAttribute('aria-label', t('page_blocked_ips', 'IP bans'));

        const labels = {
            ip: t('security_ips_table_ip', 'IP address'),
            blockedAt: t('security_ips_table_blocked_at', 'Blocked at'),
            expiresAt: t('security_ips_table_expires_at', 'Expires at'),
            reason: t('security_ips_table_reason', 'Reason'),
            actions: t('security_ips_table_actions', 'Actions'),
        };

        const header = window.createAdminTableHeader({
            className: 'provider-table-header security-ips-table-header',
            cells: [
                { className: 'header-ip', text: labels.ip },
                { className: 'header-blocked-at', text: labels.blockedAt },
                { className: 'header-expires-at', text: labels.expiresAt },
                { className: 'header-reason', text: labels.reason },
                { className: 'header-actions', text: labels.actions },
            ],
        });
        tableContainer.appendChild(header);

        const fragment = document.createDocumentFragment();
        items.forEach((entry) => {
            const row = document.createElement('div');
            row.className = 'provider-row security-ips-row';
            row.dataset.ipAddress = entry.ip_address;
            row.setAttribute('role', 'row');

            const ipCell = window.createAdminTableCell({
                className: 'security-ips-ip',
                label: labels.ip,
                text: entry.ip_address,
            });
            row.appendChild(ipCell);

            const blockedCell = window.createAdminTableCell({
                className: 'security-ips-blocked-at',
                label: labels.blockedAt,
                text: formatDateTime(entry.blocked_at),
            });
            row.appendChild(blockedCell);

            const expiresCell = window.createAdminTableCell({
                className: 'security-ips-expires-at',
                label: labels.expiresAt,
                text: formatDateTime(entry.expires_at),
            });
            row.appendChild(expiresCell);

            const reasonCell = window.createAdminTableCell({
                className: 'security-ips-reason',
                label: labels.reason,
                text: entry.reason || t('security_ips_reason_empty', '—'),
            });
            row.appendChild(reasonCell);

            const actionsCell = window.createAdminTableCell({
                className: 'provider-actions user-actions security-ips-actions',
                label: labels.actions,
            });
            const editButton = window.createAdminIconActionButton({
                className: 'action-btn edit-btn',
                title: t('security_ips_edit_title', 'Edit IP ban'),
                ariaLabel: formatT('security_ips_edit_aria', 'Edit IP ban for {ip}', { ip: entry.ip_address }),
                icon: Icons?.edit,
                fallback: '✎',
                dataset: {
                    securityIpAction: 'edit',
                    ipAddress: entry.ip_address,
                },
            });
            actionsCell.appendChild(editButton);

            const deleteButton = window.createAdminIconActionButton({
                className: 'action-btn delete-btn',
                title: t('security_ips_unblock_title', 'Unblock IP'),
                icon: Icons?.trash,
                fallback: '✕',
                dataset: {
                    securityIpAction: 'unblock',
                    ipAddress: entry.ip_address,
                },
            });
            actionsCell.appendChild(deleteButton);
            row.appendChild(actionsCell);

            fragment.appendChild(row);
        });

        tableContainer.appendChild(fragment);
        renderPagination();
    };

    const destroyChart = () => {
        if (countryChart) {
            countryChart.destroy();
            countryChart = null;
        }
    };

    /*
     * Show or hide a shared `.stats-chart-empty` overlay inside the chart body.
     * This mirrors the model statistics dashboard so the IP analytics chart
     * communicates "no data" the same way instead of leaving a blank canvas.
     */
    const setChartEmptyState = (message) => {
        if (!el.chartCanvas) {
            return;
        }
        const container = el.chartCanvas.parentElement;
        if (!container) {
            return;
        }
        let messageEl = container.querySelector('.stats-chart-empty');
        if (message) {
            // Hide the canvas and render (or update) the empty-state message.
            el.chartCanvas.style.display = 'none';
            if (!messageEl) {
                messageEl = document.createElement('p');
                messageEl.className = 'stats-chart-empty';
                container.appendChild(messageEl);
            }
            messageEl.textContent = message;
            messageEl.hidden = false;
        } else {
            // Data is available: restore the canvas and remove any empty message.
            el.chartCanvas.style.display = '';
            if (messageEl) {
                messageEl.remove();
            }
        }
    };

    const renderCountryChart = (countries = []) => {
        destroyChart();
        if (!el.chartCanvas || typeof Chart === 'undefined') {
            return;
        }

        const filteredCountries = countries
            .filter((country) => (
                (country.denied_requests || 0)
                + (country.rate_limited_requests || 0)
                + (country.manual_bans_created || 0)
                + (country.automatic_bans_created || 0)
            ) > 0)
            .slice(0, 8);

        if (!filteredCountries.length) {
            // No chartable activity: show the shared empty-state message.
            setChartEmptyState(t('security_ips_stats_chart_empty', 'No country activity to chart for the selected period.'));
            return;
        }

        // Data is present: make sure the canvas is visible before drawing.
        setChartEmptyState(null);

        countryChart = new Chart(el.chartCanvas, {
            type: 'bar',
            data: {
                labels: filteredCountries.map((country) => getCountryLabel(country.country_code)),
                datasets: [
                    {
                        label: t('security_ips_stats_dataset_attempts', 'Denied requests'),
                        data: filteredCountries.map((country) => country.denied_requests || 0),
                        backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--admin-warning').trim() || 'currentColor',
                        borderRadius: 8,
                    },
                    {
                        label: t('security_ips_stats_dataset_rate_limited', 'Rate limited'),
                        data: filteredCountries.map((country) => country.rate_limited_requests || 0),
                        backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--admin-error').trim() || 'currentColor',
                        borderRadius: 8,
                    },
                    {
                        label: t('security_ips_stats_dataset_manual_bans', 'Manual bans'),
                        data: filteredCountries.map((country) => country.manual_bans_created || 0),
                        backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--admin-accent').trim() || 'currentColor',
                        borderRadius: 8,
                    },
                    {
                        label: t('security_ips_stats_dataset_auto_bans', 'Automatic bans'),
                        data: filteredCountries.map((country) => country.automatic_bans_created || 0),
                        backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--admin-success').trim() || 'currentColor',
                        borderRadius: 8,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    // Legend is rendered as static HTML in the chart header so it
                    // stays consistent with the rest of the stats dashboards.
                    legend: {
                        display: false,
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                    },
                    y: {
                        beginAtZero: true,
                        ticks: {
                            precision: 0,
                        },
                    },
                },
            },
        });
    };

    const renderCountryTable = (countries = []) => {
        if (!el.countryTableBody) {
            return;
        }

        el.countryTableBody.innerHTML = '';
        if (!countries.length) {
            // Reuse the shared empty-row class so the mobile card layout keeps
            // the message as a single full-width row instead of a grid card.
            const row = document.createElement('tr');
            row.className = 'stats-table-empty';
            const cell = document.createElement('td');
            cell.colSpan = 8;
            cell.className = 'security-ips-empty';
            cell.textContent = t('security_ips_stats_table_empty', 'No country analytics available for the selected period.');
            row.appendChild(cell);
            el.countryTableBody.appendChild(row);
            return;
        }

        // Highest blocked-attempt count in the set; used to scale the per-row
        // share bars so the most-active country fills the bar completely.
        const maxAttempts = countries.reduce(
            (max, country) => Math.max(max, country.denied_requests || 0),
            0,
        );

        const fragment = document.createDocumentFragment();
        countries.forEach((country) => {
            const row = document.createElement('tr');

            const countryCell = document.createElement('td');
            countryCell.className = 'security-ips-country-cell';
            // data-label powers the responsive card layout (.stats-table td::before on mobile).
            countryCell.setAttribute('data-label', t('security_ips_stats_col_country', 'Country'));
            
            const countryNameSpan = document.createElement('span');
            countryNameSpan.className = 'security-ips-country-name';
            countryNameSpan.textContent = getCountryLabel(country.country_code);
            
            const countryMetaSpan = document.createElement('span');
            countryMetaSpan.className = 'security-ips-country-meta';
            countryMetaSpan.textContent = country.country_code || t('security_ips_country_unknown', 'Unknown');
            
            countryCell.appendChild(countryNameSpan);
            countryCell.appendChild(countryMetaSpan);
            row.appendChild(countryCell);

            const blockedIpsCell = document.createElement('td');
            blockedIpsCell.className = 'security-ips-num-cell';
            blockedIpsCell.setAttribute('data-label', t('security_ips_stats_col_blocked_ips', 'Blocked IPs'));
            blockedIpsCell.textContent = String(country.distinct_ips || 0);
            row.appendChild(blockedIpsCell);

            // Attempts cell pairs the numeric value with a horizontal share bar
            // so admins can spot where blocked attempts concentrate at a glance.
            const attemptsCell = document.createElement('td');
            attemptsCell.setAttribute('data-label', t('security_ips_stats_col_attempts', 'Blocked attempts'));
            const attempts = country.denied_requests || 0;
            const attemptWrap = document.createElement('div');
            attemptWrap.className = 'security-ips-attempt-cell';

            const attemptValue = document.createElement('span');
            attemptValue.className = 'security-ips-attempt-value';
            attemptValue.textContent = String(attempts);
            attemptWrap.appendChild(attemptValue);

            const shareBar = document.createElement('div');
            shareBar.className = 'security-ips-share-bar';
            const shareFill = document.createElement('div');
            shareFill.className = 'security-ips-share-fill';
            // Scale to the busiest country; guard against divide-by-zero.
            const sharePercent = maxAttempts > 0 ? Math.round((attempts / maxAttempts) * 100) : 0;
            shareFill.style.width = `${sharePercent}%`;
            shareBar.appendChild(shareFill);
            attemptWrap.appendChild(shareBar);

            attemptsCell.appendChild(attemptWrap);
            row.appendChild(attemptsCell);

            const rateLimitedCell = document.createElement('td');
            rateLimitedCell.setAttribute('data-label', t('security_ips_stats_col_rate_limited', 'Rate limited'));
            rateLimitedCell.textContent = String(country.rate_limited_requests || 0);
            row.appendChild(rateLimitedCell);

            const shareCell = document.createElement('td');
            shareCell.setAttribute('data-label', t('security_ips_stats_col_share', 'Share'));
            shareCell.textContent = `${Number(country.share_of_denied_requests || 0).toFixed(1)}%`;
            row.appendChild(shareCell);

            const ratioCell = document.createElement('td');
            ratioCell.setAttribute('data-label', t('security_ips_stats_col_ratio', 'Denied / IP'));
            ratioCell.textContent = Number(country.denied_requests_per_ip || 0).toFixed(2);
            row.appendChild(ratioCell);

            const riskCell = document.createElement('td');
            riskCell.setAttribute('data-label', t('security_ips_stats_col_risk', 'Activity level'));
            const riskLevelWhitelist = ['low', 'medium', 'high'];
            const normalizedRiskLevel = String(country.activity_level || 'low').replace(/[^a-z]/gi, '').toLowerCase();
            const safeRiskLevel = riskLevelWhitelist.includes(normalizedRiskLevel) ? normalizedRiskLevel : 'low';
            
            const riskBadgeSpan = document.createElement('span');
            riskBadgeSpan.className = 'security-ips-risk-badge';
            riskBadgeSpan.classList.add(safeRiskLevel);
            riskBadgeSpan.textContent = getActivityLabel(country.activity_level);
            
            riskCell.appendChild(riskBadgeSpan);
            row.appendChild(riskCell);

            const lastSeenCell = document.createElement('td');
            lastSeenCell.setAttribute('data-label', t('security_ips_stats_col_last_seen', 'Last seen'));
            lastSeenCell.textContent = formatDateTime(country.last_seen_at);
            row.appendChild(lastSeenCell);

            fragment.appendChild(row);
        });

        el.countryTableBody.appendChild(fragment);
    };

    const renderRecentEvents = (events = []) => {
        if (!el.recentEvents) {
            return;
        }

        el.recentEvents.innerHTML = '';
        if (!events.length) {
            const empty = document.createElement('div');
            empty.className = 'security-ips-empty';
            empty.textContent = t('security_ips_stats_recent_empty', 'No recent IP security events for the selected period.');
            el.recentEvents.appendChild(empty);
            return;
        }

        const fragment = document.createDocumentFragment();
        const eventClassWhitelist = ['ban_created', 'request_denied', 'ban_removed', 'rate_limited'];
        events.forEach((entry, index) => {
            const item = document.createElement('div');
            item.className = 'security-ips-recent-item';
            
            const mainDiv = document.createElement('div');
            mainDiv.className = 'security-ips-recent-main';
            
            const eventTypeSpan = document.createElement('span');
            const eventTypeClass = eventClassWhitelist.includes(entry.event_type) ? entry.event_type : 'request_denied';
            eventTypeSpan.className = `security-ips-event-type ${eventTypeClass}`;
            eventTypeSpan.textContent = getEventLabel(entry.event_type);
            
            const ipSpan = document.createElement('button');
            ipSpan.type = 'button';
            ipSpan.className = 'security-ips-recent-ip security-ips-ip-drilldown';
            ipSpan.textContent = entry.ip_address || '—';
            ipSpan.setAttribute('aria-label', formatT(
                'security_ips_stats_ip_detail_aria',
                'Show analytics for {ip}',
                { ip: entry.ip_address || '' },
            ));
            ipSpan.addEventListener('click', async () => {
                state.filters.ip_address = entry.ip_address || '';
                state.eventPage = 1;
                if (el.ipFilter) el.ipFilter.value = state.filters.ip_address;
                try {
                    await Promise.all([loadOverview(), loadEvents()]);
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_load_error', 'Failed to load IP analytics.'));
                }
            });
            
            const countrySpan = document.createElement('span');
            countrySpan.className = 'security-ips-recent-meta';
            countrySpan.textContent = getCountryLabel(entry.country_code);
            
            mainDiv.appendChild(eventTypeSpan);
            mainDiv.appendChild(ipSpan);
            mainDiv.appendChild(countrySpan);
            
            const timeDiv = document.createElement('div');
            timeDiv.className = 'security-ips-recent-meta';
            timeDiv.textContent = entry.request_count > 1
                ? formatT('security_ips_stats_event_count_time', '{count} requests · {date}', {
                    count: String(entry.request_count),
                    date: formatDateTime(entry.last_seen_at),
                })
                : formatDateTime(entry.created_at);
            
            const descDiv = document.createElement('div');
            descDiv.className = 'security-ips-recent-desc';
            const details = [
                entry.event_source
                    ? formatT('security_ips_stats_event_source', 'Source: {source}', { source: entry.event_source })
                    : null,
                entry.reason_code
                    ? formatT('security_ips_stats_event_reason_code', 'Reason: {reason}', { reason: entry.reason_code })
                    : null,
                entry.route_category
                    ? formatT('security_ips_stats_event_route', 'Route: {route}', { route: entry.route_category })
                    : null,
                entry.reason || null,
            ].filter(Boolean);
            descDiv.textContent = details.join(' · ')
                || t('security_ips_stats_recent_reason_empty', 'No additional details recorded.');
            
            item.appendChild(mainDiv);
            item.appendChild(timeDiv);
            item.appendChild(descDiv);
            
            item.style.animationDelay = `${index * 50}ms`;
            fragment.appendChild(item);
        });
        el.recentEvents.appendChild(fragment);

        const page = state.events;
        if (el.eventsPagination && page) {
            el.eventsPagination.hidden = page.total_pages <= 1;
            if (el.eventsPrev) el.eventsPrev.disabled = page.page <= 1;
            if (el.eventsNext) el.eventsNext.disabled = page.page >= page.total_pages;
            if (el.eventsPageInfo) {
                el.eventsPageInfo.textContent = formatT(
                    'security_ips_stats_page_info',
                    'Page {page} of {pages} · {total} stored rows',
                    {
                        page: String(page.page),
                        pages: String(page.total_pages),
                        total: String(page.total),
                    },
                );
            }
        }
    };

    const renderOverview = () => {
        const summary = state.overview?.summary || {};
        const countries = state.overview?.countries || [];

        if (el.blockedCount) {
            animateValue(el.blockedCount, summary.active_bans || 0);
        }
        if (el.countryCount) {
            animateValue(el.countryCount, summary.known_origin_countries || 0);
        }
        if (el.attemptCount) {
            animateValue(el.attemptCount, summary.denied_requests || 0);
        }
        if (el.topCountry) {
            el.topCountry.textContent = summary.top_country_code
                ? getCountryLabel(summary.top_country_code)
                : '—';
        }
        if (el.topCountryMeta) {
            el.topCountryMeta.textContent = summary.top_country_code
                ? formatT('security_ips_stats_top_country_meta', '{count} denied requests from {ips} IPs', {
                    count: String(summary.top_country_denied_requests || 0),
                    ips: String(summary.top_country_distinct_ips || 0),
                })
                : t('security_ips_stats_top_country_empty', 'No denied requests recorded');
        }
        const provider = state.overview?.provider;
        if (el.providerStatus && provider) {
            const statusLabel = getProviderStatusLabel(provider.status);
            el.providerStatus.textContent = provider.provider
                ? `${provider.provider} · ${statusLabel}`
                : statusLabel;
        }
        if (el.range) {
            el.range.textContent = formatT(
                'security_ips_stats_exact_range',
                'UTC range: {start} – {end}. Showing {shown} of {total} country rows.',
                {
                    start: formatDateTime(state.overview?.period_start_utc),
                    end: formatDateTime(state.overview?.period_end_utc),
                    shown: String(countries.length),
                    total: String(state.overview?.country_total || 0),
                },
            );
        }
        if (el.retentionWarning) {
            const truncated = Boolean(
                state.overview?.period_truncated_by_retention
                || state.overview?.countries_truncated,
            );
            el.retentionWarning.hidden = !truncated;
            el.retentionWarning.textContent = truncated
                ? formatT(
                    'security_ips_stats_truncation_warning',
                    'This view is truncated by the {days}-day retention period or the top-country display limit.',
                    { days: String(state.overview?.retention_days || 0) },
                )
                : '';
        }

        renderCountryChart(countries);
        renderCountryTable(countries);
        renderRecentEvents(state.events?.items || []);
    };

    const renderAnalyticsState = () => {
        const enabled = Boolean(state.settings.enabled && state.settings.regulatory_confirmed);
        if (el.enableToggle) {
            el.enableToggle.checked = enabled;
        }
        if (el.retentionInput && state.settings.retention_days) {
            el.retentionInput.value = String(state.settings.retention_days);
        }
        if (el.analyticsSection) {
            el.analyticsSection.hidden = !enabled;
        }
        if (el.providerStatus && !state.overview?.provider) {
            const providerLabel = state.settings.geo_provider || t(
                'security_ips_stats_provider_missing',
                'Not configured',
            );
            const statusLabel = state.settings.geo_provider_configured
                ? t('security_ips_stats_provider_configured', 'Configured')
                : t('security_ips_stats_provider_missing', 'Not configured');
            el.providerStatus.textContent = state.settings.geo_provider
                ? `${providerLabel} · ${statusLabel}`
                : statusLabel;
        }
        if (!enabled) {
            destroyChart();
        }
    };

    const fetchSettings = async () => {
        const response = await authedFetch('/api/v1/admin/ip-address/statistics/settings');
        if (!response.ok) {
            throw await buildResponseError(response, t('security_ips_stats_settings_error', 'Failed to load IP analytics settings.'));
        }
        state.settings = await response.json();
        renderAnalyticsState();
    };

    const updateSettings = async (payload) => {
        const response = await authedFetch('/api/v1/admin/ip-address/statistics/settings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            throw await buildResponseError(response, t('security_ips_stats_update_error', 'Failed to update IP analytics settings.'));
        }
        state.settings = await response.json();
        renderAnalyticsState();
        return state.settings;
    };

    const loadOverview = async () => {
        if (!(state.settings.enabled && state.settings.regulatory_confirmed)) {
            state.overview = null;
            renderAnalyticsState();
            return;
        }

        const response = await authedFetch(`/api/v1/admin/ip-address/statistics/overview?${buildAnalyticsParams().toString()}`);
        if (!response.ok) {
            throw await buildResponseError(response, t('security_ips_stats_load_error', 'Failed to load IP analytics.'));
        }
        state.overview = await response.json();
        renderOverview();
    };

    const loadEvents = async () => {
        if (!(state.settings.enabled && state.settings.regulatory_confirmed)) {
            state.events = null;
            renderRecentEvents([]);
            return;
        }
        const response = await authedFetch(`/api/v1/admin/ip-address/statistics/events?${buildAnalyticsParams(true).toString()}`);
        if (!response.ok) {
            throw await buildResponseError(response, t('security_ips_stats_load_error', 'Failed to load IP analytics.'));
        }
        state.events = await response.json();
        state.eventPage = Number(state.events.page) || 1;
        renderRecentEvents(state.events.items || []);
    };

    const populateFilterSelect = (select, values, labelBuilder = (value) => value) => {
        if (!select) return;
        const current = select.value;
        while (select.options.length > 1) select.remove(1);
        values.forEach((value) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = labelBuilder(value);
            select.appendChild(option);
        });
        select.value = values.includes(current) ? current : '';
        /* API-backed options are added after enhancement, so rebuild the menu. */
        syncAnalyticsCustomSelect(select, { refreshOptions: true });
    };

    const loadFilterOptions = async () => {
        const response = await authedFetch('/api/v1/admin/ip-address/statistics/filters');
        if (!response.ok) return;
        const options = await response.json();
        populateFilterSelect(el.countryFilter, options.country_codes || [], getCountryLabel);
        populateFilterSelect(el.eventFilter, options.event_types || [], getEventLabel);
        populateFilterSelect(el.sourceFilter, options.event_sources || []);
    };

    const loadBlockedIps = async () => {
        const params = new URLSearchParams({
            page: String(state.blockedIpsPage),
            per_page: String(state.blockedIpsPerPage),
        });
        const response = await authedFetch(`/api/v1/admin/ip-address/blocked?${params.toString()}`);
        if (!response.ok) {
            throw await buildResponseError(response, t('security_ips_load_error', 'Failed to load blocked IPs.'));
        }
        const payload = await response.json();
        if (Array.isArray(payload)) {
            state.blockedIps = payload;
            state.blockedIpsTotal = payload.length;
            state.blockedIpsTotalPages = 1;
        } else {
            state.blockedIps = Array.isArray(payload?.items) ? payload.items : [];
            state.blockedIpsTotal = Number(payload?.total) || 0;
            state.blockedIpsPage = Number(payload?.page) || state.blockedIpsPage;
            state.blockedIpsPerPage = Number(payload?.per_page) || state.blockedIpsPerPage;
            state.blockedIpsTotalPages = Math.max(1, Number(payload?.total_pages) || 1);
        }
        if (!state.blockedIps.length && state.blockedIpsPage > 1 && state.blockedIpsTotal > 0) {
            state.blockedIpsPage -= 1;
            await loadBlockedIps();
            return;
        }
        renderList(state.blockedIps);
    };

    const loadAllData = async () => {
        await loadBlockedIps();
    };

    const loadAnalyticsData = async () => {
        await fetchSettings();
        if (state.settings.enabled && state.settings.regulatory_confirmed) {
            await Promise.all([loadOverview(), loadEvents(), loadFilterOptions()]);
        }
    };

    const setButtonLoading = (button, loading, label) => {
        if (!button) {
            return;
        }
        if (typeof setButtonLoadingState === 'function') {
            setButtonLoadingState(button, loading, label);
            return;
        }
        button.disabled = loading;
    };

    const mutateIpBlock = async (ipAddress, banned, fallback, options = {}) => {
        const payload = {
            ip_address: ipAddress,
            banned,
        };
        if (banned) {
            payload.duration_days = options.durationDays;
            payload.reason = options.reason;
        }
        const response = await authedFetch('/api/v1/admin/ip-address/block', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            throw await buildResponseError(response, fallback);
        }
        return response;
    };

    const updateIpBlock = async (originalIpAddress, ipAddress, fallback, options = {}) => {
        const payload = {
            ip_address: ipAddress,
            duration_days: options.durationDays,
            reason: options.reason,
        };
        const response = await authedFetch(`/api/v1/admin/ip-address/blocked/${encodeURIComponent(originalIpAddress)}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            throw await buildResponseError(response, fallback);
        }
        return response;
    };

    const isValidIp = (value) => {
        const ipv4Pattern = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
        const ipv6Pattern = /^(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}|(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:(?::[0-9a-fA-F]{1,4}){1,6}|:(?::[0-9a-fA-F]{1,4}){1,7}|::|::[0-9a-fA-F]{1,4}|::(?:[0-9a-fA-F]{1,4}:){1,7}|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|(?:[0-9a-fA-F]{1,4}:){1,5}:[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|(?:[0-9a-fA-F]{1,4}:){1,4}:[0-9a-fA-F]{1,4}:[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,2}:[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,3}:[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|[0-9a-fA-F]{1,4}:(?::[0-9a-fA-F]{1,4}){1,4}:[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|:(?::[0-9a-fA-F]{1,4}){1,5}:[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})$/;
        return ipv4Pattern.test(value) || ipv6Pattern.test(value);
    };

    const hideOverlay = (overlay) => {
        if (!overlay) {
            return;
        }
        overlay.classList.remove('active');
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
    };

    const showOverlay = (overlay) => {
        if (!overlay) {
            return;
        }
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        overlay.classList.add('active');
    };

    const ensureModal = () => {
        if (modalRefs) {
            return modalRefs;
        }

        const overlay = window.DeleteWarningModal.create({
            id: 'securityIpsModal',
            cardClass: 'delete-warning-card--import shared-modal--wide',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'securityIpsModalTitle',
            ariaDescribedby: 'securityIpsModalSubtitle',
            contentHtml: `
                <header class="provider-import-header shared-modal-header shared-modal-header--main">
                    <div class="shared-modal-heading">
                        <h2 class="provider-import-title shared-modal-title" id="securityIpsModalTitle">${t('security_ips_modal_title', 'Block IP address')}</h2>
                        <p class="provider-import-subtitle shared-modal-subtitle" id="securityIpsModalSubtitle">${t('security_ips_modal_desc', 'Enter an IP address, duration, and reason to block access.')}</p>
                    </div>
                    <button type="button" class="provider-import-close shared-modal-close" id="securityIpsModalClose" aria-label="${t('modal_close_dialog_aria', 'Close dialog')}">${Icons.close}</button>
                </header>
                <div class="provider-import-body shared-modal-body">
                    <label class="settings-row-title" for="securityIpsInput">${t('security_ips_ip_address_label', 'IP address')}</label>
                    <input type="text" id="securityIpsInput" class="input" placeholder="${t('security_ips_input_placeholder', 'e.g. 203.0.113.42')}" autocomplete="off">
                    <label class="settings-row-title" for="securityIpsDurationInput">${t('security_ips_duration_days_label', 'Duration in days')}</label>
                    <input type="number" id="securityIpsDurationInput" class="input" min="1" max="365" step="1" value="30" inputmode="numeric" autocomplete="off" aria-describedby="securityIpsDurationHelp">
                    <p class="provider-import-description" id="securityIpsDurationHelp">${t('security_ips_duration_days_help', 'Choose a duration from 1 to 365 days.')}</p>
                    <label class="settings-row-title" for="securityIpsReasonInput">${t('security_ips_reason_label', 'Reason')}</label>
                    <textarea id="securityIpsReasonInput" class="input" rows="3" maxlength="255" placeholder="${t('security_ips_reason_placeholder', 'e.g. Repeated credential stuffing attempts')}" aria-describedby="securityIpsReasonHelp"></textarea>
                    <p class="provider-import-description" id="securityIpsReasonHelp">${t('security_ips_reason_help', 'This reason is saved with the IP ban and included in the audit log.')}</p>
                    <p class="provider-import-description">${t('security_ips_modal_help', 'Only valid IPv4 or IPv6 addresses are accepted. You can unblock entries from the list whenever needed.')}</p>
                </div>
            `,
            actions: [
                { id: 'securityIpsModalCancel', role: 'cancel', variant: 'cancel', text: t('btn_cancel', 'Cancel') },
                { id: 'securityIpsModalConfirm', variant: 'submit', text: t('security_ips_block_btn', 'Block IP') },
            ],
        });
        document.body.appendChild(overlay);

        const title = overlay.querySelector('#securityIpsModalTitle');
        const subtitle = overlay.querySelector('.provider-import-subtitle');
        const input = overlay.querySelector('#securityIpsInput');
        const durationInput = overlay.querySelector('#securityIpsDurationInput');
        const reasonInput = overlay.querySelector('#securityIpsReasonInput');
        const closeButton = overlay.querySelector('#securityIpsModalClose');
        const cancelButton = overlay.querySelector('#securityIpsModalCancel');
        const confirmButton = overlay.querySelector('#securityIpsModalConfirm');
        let editingEntry = null;
        let returnFocus = null;

        const hide = ({ restoreFocus = true } = {}) => {
            const focusTarget = returnFocus;
            hideOverlay(overlay);
            returnFocus = null;
            if (restoreFocus) {
                focusTarget?.focus?.();
            }
        };
        const show = ({ entry = null } = {}) => {
            returnFocus = document.activeElement;
            editingEntry = entry;
            const isEdit = Boolean(editingEntry);
            if (title) {
                title.textContent = isEdit
                    ? t('security_ips_edit_modal_title', 'Edit IP ban')
                    : t('security_ips_modal_title', 'Block IP address');
            }
            if (subtitle) {
                subtitle.textContent = isEdit
                    ? t('security_ips_edit_modal_desc', 'Update the IP address, duration, and reason for this saved ban.')
                    : t('security_ips_modal_desc', 'Enter an IP address, duration, and reason to block access.');
            }
            if (confirmButton) {
                confirmButton.textContent = isEdit
                    ? t('security_ips_save_edit_btn', 'Save changes')
                    : t('security_ips_block_btn', 'Block IP');
            }
            if (input) {
                input.value = editingEntry?.ip_address || '';
            }
            if (durationInput) {
                durationInput.value = String(isEdit ? getRemainingDurationDays(editingEntry) : 30);
            }
            if (reasonInput) {
                reasonInput.value = editingEntry?.reason || t('security_ips_reason_default', 'Banned by admin');
            }
            showOverlay(overlay);
            setTimeout(() => input?.focus(), 0);
        };

        closeButton?.addEventListener('click', hide);
        cancelButton?.addEventListener('click', hide);
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) {
                hide();
            }
        });

        confirmButton?.addEventListener('click', async () => {
            let shouldRestoreFocus = false;
            const ipAddress = input?.value?.trim();
            if (!ipAddress) {
                notifyWarning?.(t('security_ips_validation_required', 'Please enter an IP address.'));
                input?.focus();
                return;
            }
            if (!isValidIp(ipAddress)) {
                notifyError?.(t('security_ips_validation_invalid', 'Enter a valid IPv4 or IPv6 address.'));
                input?.focus();
                return;
            }
            const durationDays = Number(durationInput?.value);
            if (!Number.isInteger(durationDays) || durationDays < 1 || durationDays > 365) {
                notifyError?.(t('security_ips_validation_duration_invalid', 'Enter a duration from 1 to 365 days.'));
                durationInput?.focus();
                return;
            }
            const reason = reasonInput?.value?.trim();
            if (!reason) {
                notifyWarning?.(t('security_ips_validation_reason_required', 'Please enter a reason for this IP ban.'));
                reasonInput?.focus();
                return;
            }
            if (reason.length > 255) {
                notifyError?.(t('security_ips_validation_reason_too_long', 'Reason must be 255 characters or fewer.'));
                reasonInput?.focus();
                return;
            }

            try {
                const isEdit = Boolean(editingEntry);
                setButtonLoading(
                    confirmButton,
                    true,
                    isEdit
                        ? t('security_ips_saving_edit', 'Saving...')
                        : t('security_ips_blocking', 'Blocking...')
                );
                if (isEdit) {
                    await updateIpBlock(
                        editingEntry.ip_address,
                        ipAddress,
                        t('security_ips_edit_error', 'Failed to update IP ban.'),
                        { durationDays, reason }
                    );
                    notifySuccess?.(t('security_ips_edit_success', 'IP ban updated successfully.'));
                } else {
                    await mutateIpBlock(ipAddress, true, t('security_ips_block_error', 'Failed to block IP address.'), {
                        durationDays,
                        reason,
                    });
                    notifySuccess?.(t('security_ips_block_success', 'IP address blocked successfully.'));
                }
                hide({ restoreFocus: false });
                shouldRestoreFocus = true;
                editingEntry = null;
                if (input) {
                    input.value = '';
                }
                if (durationInput) {
                    durationInput.value = '30';
                }
                if (reasonInput) {
                    reasonInput.value = t('security_ips_reason_default', 'Banned by admin');
                }
                state.blockedIpsPage = 1;
                await loadBlockedIps();
            } catch (error) {
                notifyError?.(error?.message || (
                    editingEntry
                        ? t('security_ips_edit_error', 'Failed to update IP ban.')
                        : t('security_ips_block_error', 'Failed to block IP address.')
                ));
            } finally {
                setButtonLoading(confirmButton, false);
                if (shouldRestoreFocus) {
                    addButton?.focus?.();
                }
            }
        });

        modalRefs = { overlay, input, durationInput, reasonInput, show, hide };
        return modalRefs;
    };

    const openModal = () => {
        const modal = ensureModal();
        if (modal.input) {
            modal.input.value = '';
        }
        if (modal.durationInput) {
            modal.durationInput.value = '30';
        }
        if (modal.reasonInput) {
            modal.reasonInput.value = t('security_ips_reason_default', 'Banned by admin');
        }
        modal.show({ entry: null });
    };

    const handleTableClick = async (event) => {
        const trigger = event.target.closest('[data-security-ip-action]');
        if (!trigger) {
            return;
        }

        const ipAddress = trigger.dataset.ipAddress;
        if (!ipAddress) {
            notifyError?.(t('security_ips_resolve_error', 'Failed to resolve IP address.'));
            return;
        }

        if (trigger.dataset.securityIpAction === 'edit') {
            const entry = state.blockedIps.find((item) => item.ip_address === ipAddress);
            if (!entry) {
                notifyError?.(t('security_ips_resolve_error', 'Failed to resolve IP address.'));
                return;
            }
            ensureModal().show({ entry });
            return;
        }

        if (trigger.dataset.securityIpAction !== 'unblock') {
            return;
        }

        const confirmTitle = t('security_ips_unblock_confirm_title', 'Unblock IP address?');
        const confirmMessage = formatT(
            'security_ips_unblock_confirm_desc',
            'Remove {ip} from the block list. Future requests from this IP will be allowed unless another policy blocks them.',
            { ip: ipAddress }
        );
        const confirmLabel = t('security_ips_unblock_confirm_btn', 'Unblock IP');
        let confirmed = false;

        if (typeof window.showWarningConfirm === 'function') {
            confirmed = Boolean(await window.showWarningConfirm({
                title: confirmTitle,
                message: confirmMessage,
                confirmLabel,
                variant: 'warning',
            }));
        } else if (typeof window.showDeleteConfirm === 'function') {
            confirmed = Boolean(await window.showDeleteConfirm({
                title: confirmTitle,
                message: confirmMessage,
                confirmLabel,
                danger: false,
                variant: 'warning',
            }));
        } else {
            notifyError?.(t('security_ips_confirm_unavailable', 'Confirmation dialog is unavailable. Please try again.'));
            return;
        }
        if (!confirmed) {
            return;
        }

        try {
            trigger.disabled = true;
            await mutateIpBlock(ipAddress, false, t('security_ips_unblock_error', 'Failed to unblock IP address.'));
            notifySuccess?.(formatT('security_ips_unblock_success', 'IP {ip} unblocked.', { ip: ipAddress }));
            const row = tableContainer.querySelector(`[data-ip-address="${CSS.escape(ipAddress)}"]`);
            if (row) {
                row.classList.add('is-removing');
                await new Promise((resolve) => setTimeout(resolve, 300));
            }
            await loadBlockedIps();
        } catch (error) {
            notifyError?.(error?.message || t('security_ips_unblock_error', 'Failed to unblock IP address.'));
        } finally {
            trigger.disabled = false;
        }
    };

    const bindAnalyticsEvents = () => {
        if (analyticsEventsBound) {
            return;
        }

        [el.regulatoryModal, el.disableModal].forEach((modal) => {
            if (modal) {
                document.body.appendChild(modal);
            }
        });

        el.regulatoryCheckbox?.addEventListener('change', () => {
            updateRegulatoryConfirmState();
        });
        el.regulatoryDocumentationInput?.addEventListener('input', updateRegulatoryConfirmState);
        el.retentionInput?.addEventListener('input', updateRegulatoryConfirmState);

        const closeRegulatoryModal = () => {
            hideOverlay(el.regulatoryModal);
            renderAnalyticsState();
        };
        const closeDisableModal = () => {
            hideOverlay(el.disableModal);
            renderAnalyticsState();
        };

        el.regulatoryCancelBtn?.addEventListener('click', closeRegulatoryModal);
        el.disableCancelBtn?.addEventListener('click', closeDisableModal);

        el.regulatoryModal?.addEventListener('click', (event) => {
            if (event.target === el.regulatoryModal) {
                closeRegulatoryModal();
            }
        });

        el.disableModal?.addEventListener('click', (event) => {
            if (event.target === el.disableModal) {
                closeDisableModal();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape') {
                return;
            }
            if (el.regulatoryModal && !el.regulatoryModal.hidden) {
                closeRegulatoryModal();
            }
            if (el.disableModal && !el.disableModal.hidden) {
                closeDisableModal();
            }
        });

        el.regulatoryConfirmBtn?.addEventListener('click', async () => {
            const retentionDays = getRetentionDaysFromInput();
            const documentation = el.regulatoryDocumentationInput?.value?.trim() || '';
            if (!documentation) {
                notifyError?.(t('security_ips_stats_regulatory_documentation_required', 'Document the legal basis or policy reference before enabling analytics.'));
                el.regulatoryDocumentationInput?.focus();
                updateRegulatoryConfirmState();
                return;
            }
            if (retentionDays === null) {
                notifyError?.(t('security_ips_stats_retention_days_invalid', 'Retention days must be between 1 and 3650.'));
                el.retentionInput?.focus();
                updateRegulatoryConfirmState();
                return;
            }
            try {
                setButtonLoading(el.regulatoryConfirmBtn, true, t('security_ips_stats_enable_btn', 'Enable Analytics'));
                await updateSettings({
                    enabled: true,
                    regulatory_confirmed: true,
                    regulatory_justification: documentation,
                    retention_days: retentionDays,
                });
                hideOverlay(el.regulatoryModal);
                notifySuccess?.(t('security_ips_stats_enabled_success', 'IP origin analytics enabled.'));
                await Promise.all([loadOverview(), loadEvents(), loadFilterOptions()]);
            } catch (error) {
                notifyError?.(error?.message || t('security_ips_stats_update_error', 'Failed to update IP analytics settings.'));
                if (el.enableToggle) {
                    el.enableToggle.checked = false;
                }
            } finally {
                setButtonLoading(el.regulatoryConfirmBtn, false);
            }
        });

        el.disableConfirmBtn?.addEventListener('click', async () => {
            try {
                setButtonLoading(el.disableConfirmBtn, true, t('security_ips_stats_disable_btn', 'Disable Analytics'));
                await updateSettings({ enabled: false, regulatory_confirmed: false });
                hideOverlay(el.disableModal);
                state.overview = null;
                renderAnalyticsState();
                notifySuccess?.(t('security_ips_stats_disabled_success', 'IP origin analytics disabled.'));
            } catch (error) {
                notifyError?.(error?.message || t('security_ips_stats_update_error', 'Failed to update IP analytics settings.'));
                if (el.enableToggle) {
                    el.enableToggle.checked = true;
                }
            } finally {
                setButtonLoading(el.disableConfirmBtn, false);
            }
        });

        analyticsEventsBound = true;
    };

    const bindBanEvents = () => {
        ensureModal();

        if (!tableBound) {
            tableContainer.addEventListener('click', handleTableClick);
            tableBound = true;
        }

        if (addButton.dataset.bound !== 'true') {
            addButton.addEventListener('click', openModal);
            addButton.dataset.bound = 'true';
        }
    };

    const bindAnalyticsControls = () => {
        bindAnalyticsEvents();
        initializeAnalyticsCustomSelects();
        if (el.enableToggle && el.enableToggle.dataset.bound !== 'true') {
            el.enableToggle.addEventListener('change', () => {
                if (el.enableToggle.checked) {
                    if (el.regulatoryCheckbox) {
                        el.regulatoryCheckbox.checked = false;
                    }
                    if (el.regulatoryDocumentationInput) {
                        el.regulatoryDocumentationInput.value = state.settings.regulatory_justification
                            || state.settings.policy_reference
                            || '';
                    }
                    updateRegulatoryConfirmState();
                    showOverlay(el.regulatoryModal);
                    return;
                }
                showOverlay(el.disableModal);
            });
            el.enableToggle.dataset.bound = 'true';
        }

        if (el.retentionInput && el.retentionInput.dataset.bound !== 'true') {
            el.retentionInput.addEventListener('change', async () => {
                const retentionDays = getRetentionDaysFromInput();
                if (retentionDays === null) {
                    notifyError?.(t('security_ips_stats_retention_days_invalid', 'Retention days must be between 1 and 3650.'));
                    el.retentionInput.value = String(state.settings.retention_days || 90);
                    updateRegulatoryConfirmState();
                    return;
                }
                try {
                    await updateSettings({ retention_days: retentionDays });
                    notifySuccess?.(t('security_ips_stats_retention_saved', 'IP analytics retention updated.'));
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_update_error', 'Failed to update IP analytics settings.'));
                    el.retentionInput.value = String(state.settings.retention_days || 90);
                } finally {
                    updateRegulatoryConfirmState();
                }
            });
            el.retentionInput.dataset.bound = 'true';
        }

        if (el.periodSelect && el.periodSelect.dataset.bound !== 'true') {
            el.periodSelect.addEventListener('change', async () => {
                state.period = parseInt(el.periodSelect.value, 10) || 30;
                state.eventPage = 1;
                try {
                    await Promise.all([loadOverview(), loadEvents()]);
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_load_error', 'Failed to load IP analytics.'));
                }
            });
            el.periodSelect.dataset.bound = 'true';
        }

        if (el.refreshBtn && el.refreshBtn.dataset.bound !== 'true') {
            el.refreshBtn.addEventListener('click', async () => {
                el.refreshBtn?.querySelector('.refresh-icon')?.classList.add('is-spinning');
                try {
                    await Promise.all([loadOverview(), loadEvents(), loadFilterOptions()]);
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_load_error', 'Failed to refresh IP analytics.'));
                } finally {
                    el.refreshBtn?.querySelector('.refresh-icon')?.classList.remove('is-spinning');
                }
            });
            el.refreshBtn.dataset.bound = 'true';
        }

        if (el.applyFiltersBtn && el.applyFiltersBtn.dataset.bound !== 'true') {
            el.applyFiltersBtn.addEventListener('click', async () => {
                state.filters = {
                    ip_address: el.ipFilter?.value?.trim() || '',
                    country_code: el.countryFilter?.value || '',
                    event_type: el.eventFilter?.value || '',
                    event_source: el.sourceFilter?.value || '',
                };
                state.eventPage = 1;
                try {
                    await Promise.all([loadOverview(), loadEvents()]);
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_load_error', 'Failed to load IP analytics.'));
                }
            });
            el.applyFiltersBtn.dataset.bound = 'true';
        }

        if (el.clearFiltersBtn && el.clearFiltersBtn.dataset.bound !== 'true') {
            el.clearFiltersBtn.addEventListener('click', async () => {
                state.filters = { ip_address: '', country_code: '', event_type: '', event_source: '' };
                [el.ipFilter, el.countryFilter, el.eventFilter, el.sourceFilter].forEach((control) => {
                    if (control) control.value = '';
                });
                [el.countryFilter, el.eventFilter, el.sourceFilter].forEach((select) => {
                    syncAnalyticsCustomSelect(select);
                });
                state.eventPage = 1;
                try {
                    await Promise.all([loadOverview(), loadEvents()]);
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_load_error', 'Failed to load IP analytics.'));
                }
            });
            el.clearFiltersBtn.dataset.bound = 'true';
        }

        if (el.eventsPrev && el.eventsPrev.dataset.bound !== 'true') {
            el.eventsPrev.addEventListener('click', async () => {
                if (state.eventPage <= 1) return;
                state.eventPage -= 1;
                await loadEvents();
            });
            el.eventsNext?.addEventListener('click', async () => {
                if (state.eventPage >= (state.events?.total_pages || 1)) return;
                state.eventPage += 1;
                await loadEvents();
            });
            el.eventsPrev.dataset.bound = 'true';
            if (el.eventsNext) el.eventsNext.dataset.bound = 'true';
        }

        if (el.exportBtn && el.exportBtn.dataset.bound !== 'true') {
            el.exportBtn.addEventListener('click', async () => {
                try {
                    const response = await authedFetch('/api/v1/admin/ip-address/statistics/export');
                    if (!response.ok) throw await buildResponseError(response);
                    const blob = await response.blob();
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = `omlorix-ip-analytics-${new Date().toISOString().slice(0, 10)}.json`;
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                    URL.revokeObjectURL(url);
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_export_error', 'Failed to export IP analytics.'));
                }
            });
            el.exportBtn.dataset.bound = 'true';
        }

        if (el.importBtn && el.importBtn.dataset.bound !== 'true') {
            el.importBtn.addEventListener('click', () => el.importInput?.click());
            el.importInput?.addEventListener('change', async () => {
                const file = el.importInput.files?.[0];
                if (!file) return;
                const formData = new FormData();
                formData.append('file', file);
                try {
                    const response = await authedFetch('/api/v1/admin/ip-address/statistics/import', {
                        method: 'POST',
                        body: formData,
                    });
                    if (!response.ok) throw await buildResponseError(response);
                    const result = await response.json();
                    notifySuccess?.(formatT(
                        'security_ips_stats_import_success',
                        'Imported {count} rows; skipped {skipped}.',
                        { count: String(result.imported_rows), skipped: String(result.skipped_rows) },
                    ));
                    await loadAnalyticsData();
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_import_error', 'Failed to import IP analytics.'));
                } finally {
                    el.importInput.value = '';
                }
            });
            el.importBtn.dataset.bound = 'true';
        }

        if (el.deleteBtn && el.deleteBtn.dataset.bound !== 'true') {
            el.deleteBtn.addEventListener('click', async () => {
                if (typeof window.showWarningConfirm !== 'function') {
                    notifyError?.(t('security_ips_confirm_unavailable', 'Confirmation dialog is unavailable. Please try again.'));
                    return;
                }
                const confirmed = await window.showWarningConfirm({
                    title: t('security_ips_stats_delete_title', 'Delete IP analytics data?'),
                    message: formatT(
                        'security_ips_stats_delete_desc',
                        'Delete analytics rows from the selected {days}-day period? Active IP bans are not changed.',
                        { days: String(state.period) },
                    ),
                    confirmLabel: t('security_ips_stats_delete_confirm', 'Delete analytics'),
                    variant: 'danger',
                });
                if (!confirmed) return;
                try {
                    const response = await authedFetch('/api/v1/admin/ip-address/statistics', {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            days: state.period,
                            ip_address: state.filters.ip_address || null,
                        }),
                    });
                    if (!response.ok) throw await buildResponseError(response);
                    const result = await response.json();
                    notifySuccess?.(formatT(
                        'security_ips_stats_delete_success',
                        'Deleted {count} analytics rows.',
                        { count: String(result.affected_rows) },
                    ));
                    state.eventPage = 1;
                    await Promise.all([loadOverview(), loadEvents(), loadFilterOptions()]);
                } catch (error) {
                    notifyError?.(error?.message || t('security_ips_stats_delete_error', 'Failed to delete IP analytics.'));
                }
            });
            el.deleteBtn.dataset.bound = 'true';
        }
    };

    window.initSecurityIpsPage = async () => {
        bindBanEvents();
        try {
            await loadAllData();
        } catch (error) {
            notifyError?.(error?.message || t('security_ips_load_error', 'Failed to load blocked IPs.'));
            renderList(state.blockedIps);
        }
    };

    window.teardownSecurityIpsPage = () => {
        modalRefs?.hide?.();
    };

    window.initSecurityIpAnalyticsPage = async () => {
        bindAnalyticsControls();
        state.period = parseInt(el.periodSelect?.value, 10) || state.period;
        try {
            await loadAnalyticsData();
        } catch (error) {
            notifyError?.(error?.message || t('security_ips_stats_load_error', 'Failed to load IP analytics.'));
            renderAnalyticsState();
        }
    };

    window.teardownSecurityIpAnalyticsPage = () => {
        hideOverlay(el.regulatoryModal);
        hideOverlay(el.disableModal);
        destroyChart();
    };
})();
