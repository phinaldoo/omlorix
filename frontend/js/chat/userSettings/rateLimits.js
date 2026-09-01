(() => {
    const CONTAINER_ID = 'rateLimitsPage';
    const NAV_ITEM_ID = 'rateLimitsNavItem';
    const NAV_SECTION_ID = 'rateLimitsNavSection';

    let rateLimitsData = null;
    let countdownInterval = null;
    let isLoading = false;
    const numberFormatter = new Intl.NumberFormat(undefined);

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatTimeRemaining(isoString) {
        const now = new Date();
        const target = new Date(isoString);
        if (Number.isNaN(target.getTime())) return 'Unknown';
        let diff = target - now;
        if (diff <= 0) return 'Resetting…';

        const days = Math.floor(diff / 86400000);
        diff %= 86400000;
        const hours = Math.floor(diff / 3600000);
        diff %= 3600000;
        const minutes = Math.floor(diff / 60000);

        if (days > 0) return `${days}d ${hours}h`;
        if (hours > 0) return `${hours}h ${minutes}m`;
        return `${minutes}m`;
    }

    function periodLabel(period) {
        if (period === 'day') return 'Daily';
        if (period === 'week') return 'Weekly';
        if (period === 'month') return 'Monthly';
        return period;
    }

    function currentWindowLabel(period) {
        if (period === 'day') return 'this daily window';
        if (period === 'week') return 'this weekly window';
        if (period === 'month') return 'this monthly window';
        return period;
    }

    function getUsagePercent(current, max) {
        if (!max || max <= 0) return 0;
        return Math.min(100, Math.round((current / max) * 100));
    }

    function getStatusClass(percent) {
        if (percent >= 100) return 'rl-status-exceeded';
        if (percent >= 80) return 'rl-status-warning';
        return 'rl-status-ok';
    }

    function getStatusLabel(percent) {
        if (percent >= 100) return 'Limit reached';
        if (percent >= 80) return 'Almost full';
        return 'Available';
    }

    function getBarColor(percent) {
        if (percent >= 100) return 'var(--error-color, #ef4444)';
        if (percent >= 80) return 'var(--warning-color, #f59e0b)';
        return 'var(--primary-color, #3b82f6)';
    }

    function formatInteger(value) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) return '0';
        return numberFormatter.format(Math.round(numeric));
    }

    function formatUsage(value, unit) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) return '0';
        if (String(unit || '').toLowerCase() !== 'minutes') return formatInteger(numeric);
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(numeric);
    }

    function getPluralForm(count) {
        const numeric = Math.abs(Number(count));
        const integer = Number.isFinite(numeric) ? Math.trunc(numeric) : 0;
        const language = String(document.documentElement?.lang || '').toLowerCase();
        if (integer === 1) return 'one';
        if (language.startsWith('ru')) {
            const mod10 = integer % 10;
            const mod100 = integer % 100;
            if (mod10 === 1 && mod100 !== 11) return 'one';
            if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'few';
        }
        return 'other';
    }

    function pluralizedUnitLabel(baseKey, count, fallbacks) {
        const form = getPluralForm(count);
        if (form === 'one') return t(`${baseKey}_one`, fallbacks.one);
        if (form === 'few') return t(`${baseKey}_few`, fallbacks.few || fallbacks.other);
        return t(`${baseKey}_other`, fallbacks.other);
    }

    function quotaUnitLabel(unit, count) {
        const normalized = String(unit || 'requests').trim().toLowerCase();
        if (normalized === 'invocations') {
            return pluralizedUnitLabel('us_rate_limits_unit_invocation', count, {
                one: 'invocation',
                few: 'invocations',
                other: 'invocations',
            });
        }
        if (normalized === 'tokens') {
            return pluralizedUnitLabel('us_rate_limits_unit_token', count, {
                one: 'token',
                few: 'tokens',
                other: 'tokens',
            });
        }
        if (normalized === 'minutes') {
            return pluralizedUnitLabel('us_rate_limits_unit_minute', count, {
                one: 'minute',
                few: 'minutes',
                other: 'minutes',
            });
        }
        return pluralizedUnitLabel('us_rate_limits_unit_request', count, {
            one: 'request',
            few: 'requests',
            other: 'requests',
        });
    }

    function getToolLabel(tool) {
        const fallback = tool?.label || tool?.name || tool?.key || t('us_rate_limits_unknown_tool', 'Unknown tool');
        return tool?.label_key ? t(tool.label_key, fallback) : fallback;
    }

    async function fetchUserRateLimits() {
        try {
            const res = await window.authedFetch('/api/v1/llm/rate-limits/user');
            if (!res.ok) return [];
            return await res.json();
        } catch (err) {
            console.error('[rateLimits] Failed to fetch rate limits', err);
            return [];
        }
    }

    function renderRateLimits(container) {
        if (!container) return;

        if (isLoading) {
            container.innerHTML = `
                <div class="rl-loading">
                    <div class="rl-spinner"></div>
                    <p>${escapeHtml(t('us_rate_limits_loading', 'Loading usage limits…'))}</p>
                </div>`;
            return;
        }

        if (!rateLimitsData || rateLimitsData.length === 0) {
            container.innerHTML = `
                <div class="rl-empty-state">
                    ${Icons.check}
                    <h3>${escapeHtml(t('us_rate_limits_empty_title', 'No usage limits'))}</h3>
                    <p>${escapeHtml(t('us_rate_limits_empty_desc', 'You currently have no usage limits applied to your account.'))}</p>
                </div>`;
            return;
        }

        const cards = rateLimitsData.map((rl) => {
            const quotaValue = Number(rl.quota_value || rl.max_requests || 0);
            const currentUsage = Number(rl.current_usage != null ? rl.current_usage : rl.current_count || 0);
            const quotaUnit = String(rl.quota_unit || 'requests').trim().toLowerCase();
            const percent = getUsagePercent(currentUsage, quotaValue);
            const statusClass = getStatusClass(percent);
            const statusLabel = getStatusLabel(percent);
            const targetType = String(rl.target_type || 'model').trim().toLowerCase();
            const targetNames = targetType === 'tool'
                ? (rl.tools || []).map((tool) => getToolLabel(tool)).join(', ') || t('us_rate_limits_unknown_tools', 'Unknown tools')
                : (targetType === 'model'
                    ? (rl.models || []).map((m) => m.name).join(', ') || t('us_rate_limits_unknown_models', 'Unknown models')
                    : (targetType === 'dictation'
                        ? t('us_rate_limits_dictation_target', 'Dictation mode')
                        : (targetType === 'realtime'
                            ? t('us_rate_limits_realtime_target', 'Realtime calls')
                            : t('us_rate_limits_unknown_target', 'Unknown usage target'))));
            const barColor = getBarColor(percent);
            const timeStr = formatTimeRemaining(rl.resets_at);
            const unitLabelPlural = quotaUnitLabel(quotaUnit, quotaValue);

            return `
                <div class="rl-card ${statusClass}" data-rate-limit-id="${escapeHtml(rl.id)}">
                    <div class="rl-card-header">
                        <div class="rl-card-title-row">
                            <div class="rl-card-icon">
                                ${Icons.clock}
                            </div>
                            <div class="rl-card-title-group">
                                <h3 class="rl-card-title">${escapeHtml(rl.name)}</h3>
                                <span class="rl-card-period">${escapeHtml(periodLabel(rl.period))} ${escapeHtml(quotaUnitLabel(quotaUnit, 2))} limit</span>
                            </div>
                        </div>
                        <div class="rl-status-badge ${statusClass}">
                            <span class="rl-status-dot"></span>
                            ${escapeHtml(statusLabel)}
                        </div>
                    </div>

                    <div class="rl-progress-section">
                        <div class="rl-progress-header">
                            <span class="rl-progress-count">${formatUsage(currentUsage, quotaUnit)} <span class="rl-progress-separator">/</span> ${formatUsage(quotaValue, quotaUnit)}</span>
                            <span class="rl-progress-label">${escapeHtml(unitLabelPlural)} used in ${escapeHtml(currentWindowLabel(rl.period))}</span>
                        </div>
                        <div class="rl-progress-bar-track">
                            <div class="rl-progress-bar-fill" style="width: ${percent}%; background: ${barColor}"></div>
                        </div>
                    </div>

                    <div class="rl-card-footer">
                        <div class="rl-footer-item">
                            ${Icons.automations_management}
                            <span class="rl-countdown" data-resets-at="${escapeHtml(rl.resets_at)}">${escapeHtml(t('us_rate_limits_resets_in', 'Resets in'))} ${escapeHtml(timeStr)}</span>
                        </div>
                        <div class="rl-footer-item">
                            ${Icons.omlorix}
                            <span class="rl-model-names" title="${escapeHtml(targetNames)}">${escapeHtml(targetNames)}</span>
                        </div>
                    </div>

                    ${percent >= 100 ? `
                    <div class="rl-exceeded-notice">
                        ${Icons.warning}
                        <span>${escapeHtml(targetType === 'tool'
                            ? t('us_rate_limits_tool_exceeded_notice', 'Tool limit reached. Try again after the reset time.')
                            : (targetType === 'model'
                                ? t('us_rate_limits_exceeded_notice', 'Limit reached. Try switching to a different model.')
                                : (targetType === 'dictation'
                                    ? t('us_rate_limits_feature_exceeded_notice', 'Minute limit reached. Try again after the reset time.')
                                    : (targetType === 'realtime'
                                        ? t('us_rate_limits_realtime_exceeded_notice', 'Realtime call limit reached. Try again after the reset time.')
                                        : t('us_rate_limits_unknown_exceeded_notice', 'Usage limit reached. Try again after the reset time.')))))}</span>
                    </div>` : ''}
                </div>`;
        });

        container.innerHTML = `
            <div class="rl-grid">${cards.join('')}</div>
        `;
    }

    function startCountdown() {
        stopCountdown();
        countdownInterval = setInterval(() => {
            const els = document.querySelectorAll('.rl-countdown[data-resets-at]');
            if (!els.length) return;
            els.forEach((el) => {
                const resetsAt = el.getAttribute('data-resets-at');
                if (!resetsAt) return;
                el.textContent = `${t('us_rate_limits_resets_in', 'Resets in')} ${formatTimeRemaining(resetsAt)}`;
            });
        }, 30000);
    }

    function stopCountdown() {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
    }

    function setVisibility(hasLimits) {
        const navSection = document.getElementById(NAV_SECTION_ID);
        const navItem = document.getElementById(NAV_ITEM_ID);

        if (navSection) navSection.style.display = hasLimits ? '' : 'none';
        if (navItem) navItem.style.display = hasLimits ? '' : 'none';
    }

    async function initRateLimits() {
        const container = document.getElementById(CONTAINER_ID);
        if (!container) return;

        isLoading = true;
        renderRateLimits(container);

        const data = await fetchUserRateLimits();
        rateLimitsData = data;
        isLoading = false;

        setVisibility(data.length > 0);
        renderRateLimits(container);
        startCountdown();
    }

    function teardownRateLimits() {
        stopCountdown();
    }

    window.initRateLimits = initRateLimits;
    window.teardownRateLimits = teardownRateLimits;
    // The chat bootstrap uses this before the detailed usage request runs so
    // opening user settings never causes the navigation item to pop into place.
    window.setRateLimitsVisibility = setVisibility;
})();
