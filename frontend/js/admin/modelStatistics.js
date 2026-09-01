(function() {
    'use strict';

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const formatT = (key, fallback, vars) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        const template = t(key, fallback);
        return String(template).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    };

    // State
    let state = {
        initialized: false,
        loading: false,
        period: 30,
        overview: null,
        timeline: null,
        providers: null,
        categories: null,
        models: null,
        throughput: null,
        errorRates: null,
        errors: { data: [], page: 1, totalPages: 1 },
        filters: { providers: [], models: [], categories: [] },
        // Tool call stats
        toolOverview: null,
        toolsByName: null,
        toolErrors: { data: [], page: 1, totalPages: 1 },
        toolFilters: { tools: [] },
        realtimeOverview: null,
        realtimeTimeline: null,
        realtimeByModel: null,
        realtimeErrors: { data: [], page: 1, total: 0, totalPages: 1 },
        realtimeInterruptions: null,
    };

    const AUTO_REFRESH_INTERVAL = 60000; // 60 seconds
    let autoRefreshTimer = null;
    let isPageActive = false;
    let refreshCooldown = false;

    // Charts
    let charts = {
        timeline: null,
        provider: null,
        category: null,
        successError: null,
        tokenBreakdown: null,
        throughput: null,
        errorRates: null,
        costComparison: null,
        toolUsage: null,
        realtimeTimeline: null,
        realtimeByModel: null,
    };

    // Beautiful color palette
    const CHART_COLORS = {
        primary: ['#3b82f6', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#6366f1'],
        success: '#10b981',
        error: '#ef4444',
        gradient: {
            blue: ['rgba(59, 130, 246, 0.8)', 'rgba(59, 130, 246, 0.1)'],
            purple: ['rgba(139, 92, 246, 0.8)', 'rgba(139, 92, 246, 0.1)'],
            green: ['rgba(16, 185, 129, 0.8)', 'rgba(16, 185, 129, 0.1)'],
        },
    };

    // DOM Elements
    const el = {
        periodSelect: null,
        refreshBtn: null,
        deleteBtn: null,
        deleteOverlay: null,
        deleteCancelBtn: null,
        deleteConfirmBtn: null,
        timelineGranularity: null,
        timelineMetric: null,
        providerMetric: null,
        categoryMetric: null,
        modelTableProviderFilter: null,
        errorsProviderFilter: null,
        errorsPrevBtn: null,
        errorsNextBtn: null,
        errorsPaginationInfo: null,
        exportBtn: null,
        // Tool stats elements
        toolErrorsToolFilter: null,
        toolErrorsPrevBtn: null,
        toolErrorsNextBtn: null,
        toolErrorsPaginationInfo: null,
        realtimeErrorsPrevBtn: null,
        realtimeErrorsNextBtn: null,
        realtimeErrorsPaginationInfo: null,
        realtimeExportBtn: null,
        realtimeDeleteBtn: null,
    };

    // Initialize
    function init() {
        if (state.initialized) return;

        // Cache DOM elements
        el.periodSelect = document.getElementById('modelStatsPeriodSelect');
        el.refreshBtn = document.getElementById('modelStatsRefreshBtn');
        el.deleteBtn = document.getElementById('modelStatsDeleteBtn');
        el.deleteOverlay = document.getElementById('deleteModelStatsOverlay');
        el.deleteCancelBtn = document.getElementById('deleteModelStatsCancelBtn');
        el.deleteConfirmBtn = document.getElementById('deleteModelStatsConfirmBtn');
        el.timelineGranularity = document.getElementById('timelineGranularitySelect');
        el.timelineMetric = document.getElementById('timelineMetricSelect');
        el.providerMetric = document.getElementById('providerMetricSelect');
        el.categoryMetric = document.getElementById('categoryMetricSelect');
        el.modelTableProviderFilter = document.getElementById('modelTableProviderFilter');
        el.errorsProviderFilter = document.getElementById('errorsProviderFilter');
        el.errorsPrevBtn = document.getElementById('errorsPrevBtn');
        el.errorsNextBtn = document.getElementById('errorsNextBtn');
        el.errorsPaginationInfo = document.getElementById('errorsPaginationInfo');
        el.exportBtn = document.getElementById('modelStatsExportBtn');
        // Tool stats elements
        el.toolErrorsToolFilter = document.getElementById('toolErrorsToolFilter');
        el.toolErrorsPrevBtn = document.getElementById('toolErrorsPrevBtn');
        el.toolErrorsNextBtn = document.getElementById('toolErrorsNextBtn');
        el.toolErrorsPaginationInfo = document.getElementById('toolErrorsPaginationInfo');
        el.realtimeErrorsPrevBtn = document.getElementById('realtimeErrorsPrevBtn');
        el.realtimeErrorsNextBtn = document.getElementById('realtimeErrorsNextBtn');
        el.realtimeErrorsPaginationInfo = document.getElementById('realtimeErrorsPaginationInfo');
        el.realtimeExportBtn = document.getElementById('realtimeStatsExportBtn');
        el.realtimeDeleteBtn = document.getElementById('realtimeStatsDeleteBtn');

        // Setup event listeners
        setupEventListeners();

        state.initialized = true;
    }

    function setupEventListeners() {
        if (el.periodSelect) {
            el.periodSelect.addEventListener('change', () => {
                state.period = parseInt(el.periodSelect.value, 10);
                state.errors.page = 1;
                state.toolErrors.page = 1;
                state.realtimeErrors.page = 1;
                loadAllData();
            });
        }

        if (el.refreshBtn) {
            el.refreshBtn.addEventListener('click', () => {
                if (!state.loading && !refreshCooldown) {
                    loadAllData({ isManualRefresh: true });
                }
            });
        }

        if (el.timelineGranularity) {
            el.timelineGranularity.addEventListener('change', loadTimelineData);
        }

        if (el.timelineMetric) {
            el.timelineMetric.addEventListener('change', () => updateTimelineChart());
        }

        if (el.providerMetric) {
            el.providerMetric.addEventListener('change', () => updateProviderChart());
        }

        if (el.categoryMetric) {
            el.categoryMetric.addEventListener('change', () => updateCategoryChart());
        }

        if (el.modelTableProviderFilter) {
            el.modelTableProviderFilter.addEventListener('change', () => loadModelsData());
        }

        if (el.errorsProviderFilter) {
            el.errorsProviderFilter.addEventListener('change', () => {
                state.errors.page = 1;
                loadErrorsData();
            });
        }

        if (el.errorsPrevBtn) {
            el.errorsPrevBtn.addEventListener('click', () => {
                if (state.errors.page > 1) {
                    state.errors.page--;
                    loadErrorsData();
                }
            });
        }

        if (el.errorsNextBtn) {
            el.errorsNextBtn.addEventListener('click', () => {
                if (state.errors.page < state.errors.totalPages) {
                    state.errors.page++;
                    loadErrorsData();
                }
            });
        }

        // Delete all statistics
        if (el.deleteBtn) {
            el.deleteBtn.addEventListener('click', showDeleteOverlay);
        }
        if (el.deleteCancelBtn) {
            el.deleteCancelBtn.addEventListener('click', hideDeleteOverlay);
        }
        if (el.deleteConfirmBtn) {
            el.deleteConfirmBtn.addEventListener('click', deleteAllStatistics);
        }
        if (el.deleteOverlay) {
            el.deleteOverlay.addEventListener('click', (e) => {
                if (e.target === el.deleteOverlay) hideDeleteOverlay();
            });
        }

        // Export statistics
        if (el.exportBtn) {
            el.exportBtn.addEventListener('click', exportStatistics);
        }

        // Tool errors filter and pagination
        if (el.toolErrorsToolFilter) {
            el.toolErrorsToolFilter.addEventListener('change', () => {
                state.toolErrors.page = 1;
                loadToolErrorsData();
            });
        }

        if (el.toolErrorsPrevBtn) {
            el.toolErrorsPrevBtn.addEventListener('click', () => {
                if (state.toolErrors.page > 1) {
                    state.toolErrors.page--;
                    loadToolErrorsData();
                }
            });
        }

        if (el.toolErrorsNextBtn) {
            el.toolErrorsNextBtn.addEventListener('click', () => {
                if (state.toolErrors.page < state.toolErrors.totalPages) {
                    state.toolErrors.page++;
                    loadToolErrorsData();
                }
            });
        }


        if (el.realtimeErrorsPrevBtn) {
            el.realtimeErrorsPrevBtn.addEventListener('click', () => {
                if (state.realtimeErrors.page > 1) {
                    state.realtimeErrors.page--;
                    loadRealtimeErrorsData();
                }
            });
        }

        if (el.realtimeErrorsNextBtn) {
            el.realtimeErrorsNextBtn.addEventListener('click', () => {
                if (state.realtimeErrors.page < state.realtimeErrors.totalPages) {
                    state.realtimeErrors.page++;
                    loadRealtimeErrorsData();
                }
            });
        }

        if (el.realtimeExportBtn) {
            el.realtimeExportBtn.addEventListener('click', exportRealtimeStatistics);
        }

        if (el.realtimeDeleteBtn) {
            el.realtimeDeleteBtn.addEventListener('click', deleteRealtimeStatistics);
        }
    }

    // Page activation
    async function initModelStatisticsPage() {
        isPageActive = true;
        init();
        await loadAllData();
        startAutoRefresh();
    }

    window.initModelStatisticsPage = initModelStatisticsPage;

    // Data Loading
    async function loadAllData({ isManualRefresh = false } = {}) {
        if (state.loading) return;
        state.loading = true;

        if (el.refreshBtn) {
            if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                window.adminSetRefreshButtonLoadingState(el.refreshBtn, true);
            } else {
                el.refreshBtn.classList.add('is-loading');
                const refreshIcon = el.refreshBtn.querySelector('.refresh-icon');
                const checkIcon = el.refreshBtn.querySelector('.check-icon');
                if (refreshIcon) {
                    refreshIcon.hidden = false;
                    refreshIcon.removeAttribute('hidden');
                }
                if (checkIcon) {
                    checkIcon.hidden = true;
                    checkIcon.setAttribute('hidden', '');
                }
                el.refreshBtn.classList.remove('is-success');
            }
        }

        try {
            await Promise.all([
                loadFilters(),
                loadOverviewData(),
                loadTimelineData(),
                loadProviderData(),
                loadCategoryData(),
                loadModelsData(),
                loadThroughputData(),
                loadErrorRatesData(),
                loadErrorsData(),
                // Tool call stats
                loadToolFilters(),
                loadToolOverviewData(),
                loadToolsByNameData(),
                loadToolErrorsData(),
                loadRealtimeOverviewData(),
                loadRealtimeTimelineData(),
                loadRealtimeByModelData(),
                loadRealtimeErrorsData(),
                loadRealtimeInterruptionsData(),
            ]);
            if (isManualRefresh && el.refreshBtn) {
                refreshCooldown = true;
                if (typeof window.adminShowRefreshButtonSuccessState === 'function') {
                    window.adminShowRefreshButtonSuccessState(el.refreshBtn, {
                        duration: 3000,
                        onComplete: () => {
                            refreshCooldown = false;
                        },
                    });
                } else {
                    const refreshIcon = el.refreshBtn.querySelector('.refresh-icon');
                    const checkIcon = el.refreshBtn.querySelector('.check-icon');
                    if (refreshIcon) {
                        refreshIcon.hidden = true;
                        refreshIcon.setAttribute('hidden', '');
                    }
                    if (checkIcon) {
                        checkIcon.hidden = false;
                        checkIcon.removeAttribute('hidden');
                    }
                    el.refreshBtn.classList.add('is-success');
                    el.refreshBtn.disabled = true;
                    setTimeout(() => {
                        if (refreshIcon) {
                            refreshIcon.hidden = false;
                            refreshIcon.removeAttribute('hidden');
                        }
                        if (checkIcon) {
                            checkIcon.hidden = true;
                            checkIcon.setAttribute('hidden', '');
                        }
                        el.refreshBtn.classList.remove('is-success');
                        el.refreshBtn.disabled = false;
                        refreshCooldown = false;
                    }, 3000);
                }
            }
        } catch (err) {
            console.error('Failed to load model statistics:', err);
            notifyError?.('Failed to load statistics data');
            refreshCooldown = false;
            if (el.refreshBtn) {
                if (typeof window.adminResetRefreshButtonState === 'function') {
                    window.adminResetRefreshButtonState(el.refreshBtn);
                } else {
                    el.refreshBtn.disabled = false;
                    el.refreshBtn.classList.remove('is-success');
                    const refreshIcon = el.refreshBtn.querySelector('.refresh-icon');
                    const checkIcon = el.refreshBtn.querySelector('.check-icon');
                    if (refreshIcon) {
                        refreshIcon.hidden = false;
                        refreshIcon.removeAttribute('hidden');
                    }
                    if (checkIcon) {
                        checkIcon.hidden = true;
                        checkIcon.setAttribute('hidden', '');
                    }
                }
            }
        } finally {
            state.loading = false;
            if (el.refreshBtn) {
                if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                    window.adminSetRefreshButtonLoadingState(el.refreshBtn, false);
                } else {
                    el.refreshBtn.classList.remove('is-loading');
                }
            }
        }
    }

    async function loadFilters() {
        try {
            const response = await authedFetch('/api/v1/llmstats/admin/filters');
            if (response.ok) {
                const data = await response.json();
                state.filters = data;
                populateFilterSelects();
            }
        } catch (err) {
            console.error('Failed to load filters:', err);
        }
    }

    function populateFilterSelects() {
        // Populate model table provider filter
        const providers = Array.isArray(state.filters.providers) ? state.filters.providers : [];

        const buildProviderOption = (provider) => {
            const opt = document.createElement('option');
            const providerId = provider?.provider_id || '';
            const providerType = provider?.provider || '';
            opt.value = providerId || providerType || '';
            if (providerId) opt.dataset.providerId = providerId;
            if (providerType) opt.dataset.providerType = providerType;
            opt.textContent = provider?.provider_name || formatProviderLabel(providerType);
            return opt;
        };

        if (el.modelTableProviderFilter) {
            const prevSelection = el.modelTableProviderFilter.selectedOptions[0];
            const prevProviderId = prevSelection?.dataset?.providerId || '';
            const prevProviderType = prevSelection?.dataset?.providerType || '';
            el.modelTableProviderFilter.innerHTML = '';
            const allProvidersOption = document.createElement('option');
            allProvidersOption.value = '';
            allProvidersOption.textContent = t('stats_filter_all_providers', 'All Providers');
            el.modelTableProviderFilter.appendChild(allProvidersOption);
            providers.forEach(provider => {
                const opt = buildProviderOption(provider);
                el.modelTableProviderFilter.appendChild(opt);
            });
            restoreProviderSelection(el.modelTableProviderFilter, prevProviderId, prevProviderType);
        }

        if (el.errorsProviderFilter) {
            const prevSelection = el.errorsProviderFilter.selectedOptions[0];
            const prevProviderId = prevSelection?.dataset?.providerId || '';
            const prevProviderType = prevSelection?.dataset?.providerType || '';
            el.errorsProviderFilter.innerHTML = '';
            const allErrorsProvidersOption = document.createElement('option');
            allErrorsProvidersOption.value = '';
            allErrorsProvidersOption.textContent = t('stats_filter_all_providers', 'All Providers');
            el.errorsProviderFilter.appendChild(allErrorsProvidersOption);
            providers.forEach(provider => {
                const opt = buildProviderOption(provider);
                el.errorsProviderFilter.appendChild(opt);
            });
            restoreProviderSelection(el.errorsProviderFilter, prevProviderId, prevProviderType);
        }
    }

    async function loadOverviewData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/overview?days=${state.period}`);
            if (response.ok) {
                state.overview = await response.json();
                updateKPICards();
                updateSuccessErrorChart();
                updateTokenBreakdownChart();
            }
        } catch (err) {
            console.error('Failed to load overview:', err);
        }
    }

    async function loadTimelineData() {
        try {
            const granularity = el.timelineGranularity?.value || 'daily';
            const response = await authedFetch(
                `/api/v1/llmstats/admin/timeline?days=${state.period}&granularity=${granularity}`
            );
            if (response.ok) {
                const data = await response.json();
                if (Array.isArray(data.timeline)) {
                    data.timeline = data.timeline.map(entry => ({
                        ...entry,
                        bucket_start_local: entry.bucket_start ? new Date(entry.bucket_start) : null,
                    }));
                }
                state.timeline = data;
                updateTimelineChart();
            }
        } catch (err) {
            console.error('Failed to load timeline:', err);
        }
    }

    async function loadProviderData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/by-provider?days=${state.period}`);
            if (response.ok) {
                state.providers = await response.json();
                updateProviderChart();
            }
        } catch (err) {
            console.error('Failed to load provider data:', err);
        }
    }

    async function loadCategoryData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/by-category?days=${state.period}`);
            if (response.ok) {
                state.categories = await response.json();
                updateCategoryChart();
            }
        } catch (err) {
            console.error('Failed to load category data:', err);
        }
    }

    async function loadModelsData() {
        try {
            const providerSelection = resolveProviderFilterSelection(el.modelTableProviderFilter);
            let url = `/api/v1/llmstats/admin/by-model?days=${state.period}`;
            if (providerSelection?.providerId) {
                url += `&provider_id=${encodeURIComponent(providerSelection.providerId)}`;
            } else if (providerSelection?.providerType) {
                url += `&provider=${encodeURIComponent(providerSelection.providerType)}`;
            }
            
            const response = await authedFetch(url);
            if (response.ok) {
                state.models = await response.json();
                updateModelsTable();
                updateCostComparisonChart();
            }
        } catch (err) {
            console.error('Failed to load models data:', err);
        }
    }

    async function loadThroughputData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/throughput-by-model?days=${state.period}`);
            if (response.ok) {
                state.throughput = await response.json();
                updateThroughputChart();
            }
        } catch (err) {
            console.error('Failed to load throughput data:', err);
        }
    }

    async function loadErrorRatesData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/error-rates-by-model?days=${state.period}`);
            if (response.ok) {
                state.errorRates = await response.json();
                updateErrorRatesChart();
            }
        } catch (err) {
            console.error('Failed to load error rates data:', err);
        }
    }

    async function loadErrorsData() {
        try {
            const providerSelection = resolveProviderFilterSelection(el.errorsProviderFilter);
            let url = `/api/v1/llmstats/admin/errors?days=${state.period}&page=${state.errors.page}&per_page=10`;
            if (providerSelection?.providerId) {
                url += `&provider_id=${encodeURIComponent(providerSelection.providerId)}`;
            } else if (providerSelection?.providerType) {
                url += `&provider=${encodeURIComponent(providerSelection.providerType)}`;
            }
            
            const response = await authedFetch(url);
            if (response.ok) {
                const data = await response.json();
                state.errors.data = data.errors;
                state.errors.totalPages = data.total_pages;
                updateErrorsList();
                updateErrorsPagination();
            }
        } catch (err) {
            console.error('Failed to load errors:', err);
        }
    }

    // Tool Call Statistics Data Loading
    async function loadToolFilters() {
        try {
            const response = await authedFetch('/api/v1/llmstats/admin/tool-calls/filters');
            if (response.ok) {
                const data = await response.json();
                state.toolFilters = data;
                populateToolFilterSelects();
            }
        } catch (err) {
            console.error('Failed to load tool filters:', err);
        }
    }

    function populateToolFilterSelects() {
        if (el.toolErrorsToolFilter) {
            const current = el.toolErrorsToolFilter.value;
            el.toolErrorsToolFilter.innerHTML = '';
            const allToolsOption = document.createElement('option');
            allToolsOption.value = '';
            allToolsOption.textContent = t('stats_filter_all_tools', 'All Tools');
            el.toolErrorsToolFilter.appendChild(allToolsOption);
            (state.toolFilters.tools || []).forEach((toolName) => {
                const opt = document.createElement('option');
                opt.value = toolName;
                opt.textContent = formatToolName(toolName);
                el.toolErrorsToolFilter.appendChild(opt);
            });
            el.toolErrorsToolFilter.value = current || '';
        }
    }

    async function loadToolOverviewData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/tool-calls/overview?days=${state.period}`);
            if (response.ok) {
                state.toolOverview = await response.json();
                updateToolKPICards();
            }
        } catch (err) {
            console.error('Failed to load tool overview:', err);
        }
    }

    async function loadToolsByNameData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/tool-calls/by-tool?days=${state.period}`);
            if (response.ok) {
                state.toolsByName = await response.json();
                updateToolUsageChart();
                updateToolCostTable();
            }
        } catch (err) {
            console.error('Failed to load tools by name:', err);
        }
    }

    async function loadToolErrorsData() {
        try {
            const tool = el.toolErrorsToolFilter?.value || '';
            let url = `/api/v1/llmstats/admin/tool-calls/errors?days=${state.period}&page=${state.toolErrors.page}&per_page=10`;
            if (tool) url += `&tool=${encodeURIComponent(tool)}`;
            
            const response = await authedFetch(url);
            if (response.ok) {
                const data = await response.json();
                state.toolErrors.data = data.errors;
                state.toolErrors.totalPages = data.total_pages;
                updateToolErrorsList();
                updateToolErrorsPagination();
            }
        } catch (err) {
            console.error('Failed to load tool errors:', err);
        }
    }

    // Tool Call Statistics UI Updates
    function updateToolKPICards() {
        const ov = state.toolOverview;
        if (!ov) return;

        setText('kpiToolTotalCalls', formatNumber(ov.total_calls));
        setText('kpiToolSuccessCount', formatNumber(ov.success_count));
        setText('kpiToolErrorCount', formatNumber(ov.error_count));
        setText('kpiToolSuccessRate', ov.success_rate + '%');
        setText('kpiToolTotalCost', '$' + formatCost(ov.estimated_total_cost || 0));
    }

    function updateToolUsageChart() {
        const canvas = document.getElementById('toolUsageChart');
        if (!canvas || !state.toolsByName?.tools) return;

        const tools = state.toolsByName.tools;

        if (tools.length === 0) {
            if (charts.toolUsage) {
                charts.toolUsage.destroy();
                charts.toolUsage = null;
            }
            renderEmptyChart(canvas, t('stats_tool_usage_empty', 'No tool call data available'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.toolUsage) {
            charts.toolUsage.destroy();
        }

        const labels = tools.map(t => formatToolName(t.tool_name));
        const successData = tools.map(t => t.success_rate);
        const errorData = tools.map(t => t.error_rate);

        const isDark = document.documentElement.getAttribute('data-mode') === 'dark';
        const textColor = isDark ? '#e2e8f0' : '#475569';
        const gridColor = isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.06)';

        const ctx = canvas.getContext('2d');
        charts.toolUsage = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: t('stats_success_label', 'Success'),
                        data: successData,
                        backgroundColor: 'rgba(16, 185, 129, 0.85)',
                        borderRadius: { topLeft: 6, bottomLeft: 6 },
                        borderSkipped: false,
                    },
                    {
                        label: t('stats_errors_label', 'Errors'),
                        data: errorData,
                        backgroundColor: 'rgba(239, 68, 68, 0.85)',
                        borderRadius: { topRight: 6, bottomRight: 6 },
                        borderSkipped: false,
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: isDark ? '#1e293b' : '#fff',
                        titleColor: isDark ? '#f8fafc' : '#0f172a',
                        bodyColor: isDark ? '#cbd5e1' : '#475569',
                        borderColor: isDark ? 'rgba(148, 163, 184, 0.2)' : 'rgba(15, 23, 42, 0.1)',
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            label: (ctx) => {
                                const tool = tools[ctx.dataIndex];
                                if (ctx.datasetIndex === 0) {
                                    return formatT('stats_tool_usage_success_tooltip', 'Success: {rate}% ({count} calls)', {
                                        rate: tool.success_rate,
                                        count: tool.success_count.toLocaleString()
                                    });
                                }
                                return formatT('stats_tool_usage_errors_tooltip', 'Errors: {rate}% ({count} calls)', {
                                    rate: tool.error_rate,
                                    count: tool.error_count.toLocaleString()
                                });
                            },
                            afterBody: (items) => {
                                const tool = tools[items[0].dataIndex];
                                const lines = [formatT('stats_tool_usage_total_tooltip', 'Total: {count} calls', {
                                    count: tool.total_calls.toLocaleString()
                                })];
                                if (tool.cost > 0) {
                                    lines.push(formatT('stats_tool_usage_cost_tooltip', 'Cost: ${cost}', {
                                        cost: formatCost(tool.cost)
                                    }));
                                }
                                return lines;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        stacked: true,
                        min: 0,
                        max: 100,
                        grid: { display: true, color: gridColor },
                        ticks: {
                            color: textColor,
                            font: { size: 11 },
                            callback: (v) => v + '%',
                        },
                        title: {
                            display: true,
                            text: t('stats_percentage_label', 'Percentage'),
                            color: textColor,
                        },
                    },
                    y: {
                        stacked: true,
                        grid: { display: false },
                        ticks: { color: textColor, font: { size: 11 } },
                    },
                },
            },
        });
    }


    function updateToolErrorsList() {
        const container = document.getElementById('toolErrorsListContainer');
        if (!container) return;

        const errors = state.toolErrors.data;

        if (errors.length === 0) {
            container.innerHTML = `
                <div class="model-stats-empty stats-empty">
                    <div class="model-stats-empty-icon stats-empty-icon">
                        ${Icons.check}
                    </div>
                    <p class="model-stats-empty-title stats-empty-title">${escapeHtml(t('stats_tool_errors_empty_title', 'No tool errors'))}</p>
                    <p class="model-stats-empty-text stats-empty-text">${escapeHtml(t('stats_tool_errors_empty_text', 'Great! No tool call errors have been recorded in this period.'))}</p>
                </div>
            `;
            return;
        }

        container.innerHTML = errors.map(err => {
            // Orchestrated tools can use a different model internally. Prefer
            // that failing component here so the calling chat model is not
            // presented as the root cause of a nested provider failure.
            const nested = err?.meta?.nested_generation;
            const nestedContext = nested && typeof nested === 'object' ? nested : null;
            const presentation = err?.meta?.slide_presentation;
            const presentationContext = presentation && typeof presentation === 'object' ? presentation : null;
            const hasInternalComponent = Boolean(nestedContext || presentationContext);
            const displayModel = nestedContext?.model_name || (hasInternalComponent ? '' : (err.model_name || ''));
            const displayProvider = nestedContext?.provider || (hasInternalComponent ? '' : (err.provider || ''));
            const failurePhase = nestedContext?.phase || presentationContext?.phase || '';

            return `
                <div class="model-stats-error-item model-stats-tool-error-item">
                    <div class="model-stats-error-header stats-entry-header">
                        <div class="model-stats-error-info">
                            <span class="model-stats-error-tool">${escapeHtml(formatToolName(err.tool_name))}</span>
                            ${failurePhase ? `<span class="model-stats-error-category">${escapeHtml(failurePhase)}</span>` : ''}
                            ${displayModel ? `<span class="model-stats-error-model stats-entry-model">${escapeHtml(displayModel)}</span>` : ''}
                            ${displayProvider ? `<span class="model-stats-error-provider stats-entry-provider">${escapeHtml(formatProviderLabel(displayProvider))}</span>` : ''}
                        </div>
                        <span class="model-stats-error-time stats-entry-time">${formatDateTime(err.created_at)}</span>
                    </div>
                    ${err.error_message ? `<div class="model-stats-error-message">${escapeHtml(err.error_message)}</div>` : ''}
                </div>
            `;
        }).join('');
    }

    function updateToolErrorsPagination() {
        if (el.toolErrorsPrevBtn) {
            el.toolErrorsPrevBtn.disabled = state.toolErrors.page <= 1;
        }
        if (el.toolErrorsNextBtn) {
            el.toolErrorsNextBtn.disabled = state.toolErrors.page >= state.toolErrors.totalPages;
        }
        if (el.toolErrorsPaginationInfo) {
            el.toolErrorsPaginationInfo.textContent = formatT('admin_page_of', `Page ${state.toolErrors.page} of ${state.toolErrors.totalPages || 1}`, {
                page: state.toolErrors.page,
                total: state.toolErrors.totalPages || 1,
            });
        }
    }

    function updateToolCostTable() {
        const tbody = document.getElementById('toolCostTableBody');
        if (!tbody || !state.toolsByName?.tools) return;

        const tools = state.toolsByName.tools;

        if (tools.length === 0) {
            tbody.innerHTML = `<tr class="model-stats-table-empty stats-table-empty"><td colspan="6">${escapeHtml(t('stats_tool_cost_empty', 'No tool call data for this period.'))}</td></tr>`;
            return;
        }

        tbody.innerHTML = tools.map(tool => `
            <tr>
                <td data-label="${escapeHtml(t('stats_col_tool', 'Tool'))}"><span class="model-stats-model-name stats-model-name">${escapeHtml(formatToolName(tool.tool_name))}</span></td>
                <td data-label="${escapeHtml(t('stats_col_calls', 'Calls'))}">${formatNumber(tool.total_calls)}</td>
                <td data-label="${escapeHtml(t('stats_col_successful', 'Successful'))}" class="model-stats-success-cell">${formatNumber(tool.success_count)}</td>
                <td data-label="${escapeHtml(t('stats_col_failed', 'Failed'))}" class="model-stats-error-cell">${tool.error_count > 0 ? formatNumber(tool.error_count) : '-'}</td>
                <td data-label="${escapeHtml(t('stats_col_success_rate', 'Success Rate'))}">${tool.success_rate}%</td>
                <td data-label="${escapeHtml(t('stats_col_estimated_cost', 'Est. Cost'))}" class="model-stats-cost-cell">${tool.cost > 0 ? '$' + formatCost(tool.cost) : '-'}</td>
            </tr>
        `).join('');
    }

    async function loadRealtimeOverviewData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/realtime/overview?days=${state.period}`);
            if (!response.ok) return;
            state.realtimeOverview = await response.json();
            updateRealtimeKPICards();
        } catch (err) {
            console.error('Failed to load realtime overview:', err);
        }
    }

    async function loadRealtimeTimelineData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/realtime/timeline?days=${state.period}`);
            if (!response.ok) return;
            state.realtimeTimeline = await response.json();
            updateRealtimeTimelineChart();
        } catch (err) {
            console.error('Failed to load realtime timeline:', err);
        }
    }

    async function loadRealtimeByModelData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/realtime/by-model?days=${state.period}`);
            if (!response.ok) return;
            state.realtimeByModel = await response.json();
            updateRealtimeByModelChart();
        } catch (err) {
            console.error('Failed to load realtime model breakdown:', err);
        }
    }

    async function loadRealtimeErrorsData() {
        try {
            const url = `/api/v1/llmstats/admin/realtime/errors?days=${state.period}&page=${state.realtimeErrors.page}&per_page=10`;
            const response = await authedFetch(url);
            if (!response.ok) return;
            const data = await response.json();
            state.realtimeErrors.data = Array.isArray(data.errors) ? data.errors : [];
            state.realtimeErrors.total = Number(data.total || 0);
            state.realtimeErrors.totalPages = Math.max(
                1,
                Math.ceil((Number(data.total || 0) || 0) / Math.max(1, Number(data.per_page || 10)))
            );
            updateRealtimeErrorsList();
            updateRealtimeErrorsPagination();
        } catch (err) {
            console.error('Failed to load realtime errors:', err);
        }
    }

    async function loadRealtimeInterruptionsData() {
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/realtime/interruptions?days=${state.period}`);
            if (!response.ok) return;
            state.realtimeInterruptions = await response.json();
            updateRealtimeInterruptionsList();
        } catch (err) {
            console.error('Failed to load realtime interruptions:', err);
        }
    }

    function updateRealtimeKPICards() {
        const ov = state.realtimeOverview;
        if (!ov) return;
        setText('rtKpiSessions', formatNumber(ov.total_sessions || 0));
        setText('rtKpiTurns', formatNumber(ov.total_turns || 0));
        setText('rtKpiInterruptions', `${formatNumber(ov.interruptions || 0)} (${Number(ov.interruption_rate || 0).toFixed(1)}%)`);
        setText('rtKpiAudioSeconds', `${formatNumber(Math.round(Number(ov.total_call_seconds || 0)))}s`);
        setText('rtKpiUnverifiedResponses', formatNumber(ov.unverified_responses || 0));
    }

    function updateRealtimeTimelineChart() {
        const canvas = document.getElementById('realtimeTimelineChart');
        if (!canvas || !state.realtimeTimeline?.timeline) return;
        const rows = state.realtimeTimeline.timeline || [];

        if (!rows.length) {
            if (charts.realtimeTimeline) {
                charts.realtimeTimeline.destroy();
                charts.realtimeTimeline = null;
            }
            renderEmptyChart(canvas, t('stats_realtime_timeline_empty', 'No realtime timeline data available'));
            return;
        }

        showChartCanvas(canvas);
        if (charts.realtimeTimeline) {
            charts.realtimeTimeline.destroy();
        }

        const labels = rows.map((row) => row.period);
        const turns = rows.map((row) => Number(row.turns || 0));
        const interruptions = rows.map((row) => Number(row.interruptions || 0));
        const errors = rows.map((row) => Number(row.errors || 0));

        const ctx = canvas.getContext('2d');
        charts.realtimeTimeline = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    {
                        label: t('stats_realtime_turns', 'Turns'),
                        data: turns,
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59, 130, 246, 0.12)',
                        fill: true,
                        tension: 0.25,
                    },
                    {
                        label: t('stats_realtime_interruptions', 'Interruptions'),
                        data: interruptions,
                        borderColor: '#f59e0b',
                        backgroundColor: 'rgba(245, 158, 11, 0.1)',
                        fill: false,
                        tension: 0.25,
                    },
                    {
                        label: t('stats_errors', 'Errors'),
                        data: errors,
                        borderColor: '#ef4444',
                        backgroundColor: 'rgba(239, 68, 68, 0.1)',
                        fill: false,
                        tension: 0.25,
                    },
                ],
            },
            options: getChartOptions('line', false),
        });
    }

    function updateRealtimeByModelChart() {
        const canvas = document.getElementById('realtimeByModelChart');
        if (!canvas || !state.realtimeByModel?.models) return;
        const rows = state.realtimeByModel.models || [];

        if (!rows.length) {
            if (charts.realtimeByModel) {
                charts.realtimeByModel.destroy();
                charts.realtimeByModel = null;
            }
            renderEmptyChart(canvas, t('stats_realtime_model_usage_empty', 'No realtime model usage data'));
            return;
        }

        showChartCanvas(canvas);
        if (charts.realtimeByModel) {
            charts.realtimeByModel.destroy();
        }

        const labels = rows.map((row) => row.model_name || 'unknown');
        const turns = rows.map((row) => Number(row.turns || 0));
        const interruptions = rows.map((row) => Number(row.interruptions || 0));

        const ctx = canvas.getContext('2d');
        charts.realtimeByModel = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: t('stats_realtime_turns', 'Turns'),
                        data: turns,
                        backgroundColor: 'rgba(16, 185, 129, 0.75)',
                        borderColor: 'rgba(16, 185, 129, 1)',
                        borderWidth: 1,
                    },
                    {
                        label: t('stats_realtime_interruptions', 'Interruptions'),
                        data: interruptions,
                        backgroundColor: 'rgba(249, 115, 22, 0.7)',
                        borderColor: 'rgba(249, 115, 22, 1)',
                        borderWidth: 1,
                    },
                ],
            },
            options: getChartOptions('bar', false),
        });
    }

    function updateRealtimeErrorsList() {
        const container = document.getElementById('realtimeErrorsListContainer');
        if (!container) return;
        const errors = state.realtimeErrors.data || [];

        if (!errors.length) {
            container.innerHTML = `
                <div class="model-stats-empty stats-empty">
                    <p class="model-stats-empty-title stats-empty-title">${escapeHtml(t('stats_realtime_errors_empty_title', 'No realtime errors'))}</p>
                    <p class="model-stats-empty-text stats-empty-text">${escapeHtml(t('stats_realtime_errors_empty_text', 'No realtime session errors in this period.'))}</p>
                </div>
            `;
            return;
        }

        container.innerHTML = errors.map((err) => `
            <div class="model-stats-error-item">
                <div class="model-stats-error-header stats-entry-header">
                    <div class="model-stats-error-info">
                        <span class="model-stats-error-model stats-entry-model">${escapeHtml(err.session_id || 'session')}</span>
                        <span class="model-stats-error-provider stats-entry-provider">${escapeHtml(err.chat_id || 'chat')}</span>
                        <span class="model-stats-error-category">${escapeHtml(formatT('stats_realtime_turn_label', 'Turn {turn}', { turn: Number(err.turn_index || 0) }))}</span>
                    </div>
                    <span class="model-stats-error-time stats-entry-time">${formatDateTime(err.created_at)}</span>
                </div>
                ${err.error_message ? `<div class="model-stats-error-message">${escapeHtml(err.error_message)}</div>` : ''}
            </div>
        `).join('');
    }

    function updateRealtimeErrorsPagination() {
        if (el.realtimeErrorsPrevBtn) {
            el.realtimeErrorsPrevBtn.disabled = state.realtimeErrors.page <= 1;
        }
        if (el.realtimeErrorsNextBtn) {
            el.realtimeErrorsNextBtn.disabled = state.realtimeErrors.page >= state.realtimeErrors.totalPages;
        }
        if (el.realtimeErrorsPaginationInfo) {
            el.realtimeErrorsPaginationInfo.textContent = formatT('admin_page_of', `Page ${state.realtimeErrors.page} of ${state.realtimeErrors.totalPages || 1}`, {
                page: state.realtimeErrors.page,
                total: state.realtimeErrors.totalPages || 1,
            });
        }
    }

    function updateRealtimeInterruptionsList() {
        const container = document.getElementById('realtimeInterruptionsContainer');
        if (!container) return;
        const payload = state.realtimeInterruptions;
        const examples = Array.isArray(payload?.examples) ? payload.examples : [];

        if (!examples.length) {
            container.innerHTML = `
                <div class="model-stats-empty stats-empty">
                    <p class="model-stats-empty-title stats-empty-title">${escapeHtml(t('stats_realtime_interruptions_empty_title', 'No interruptions recorded'))}</p>
                    <p class="model-stats-empty-text stats-empty-text">${escapeHtml(t('stats_realtime_interruptions_empty_text', 'Realtime sessions had no interrupted turns in this period.'))}</p>
                </div>
            `;
            return;
        }

        container.innerHTML = examples.map((entry) => `
            <div class="model-stats-error-item">
                <div class="model-stats-error-header stats-entry-header">
                    <div class="model-stats-error-info">
                        <span class="model-stats-error-model stats-entry-model">${escapeHtml(entry.session_id || 'session')}</span>
                        <span class="model-stats-error-provider stats-entry-provider">${escapeHtml(entry.chat_id || 'chat')}</span>
                        <span class="model-stats-error-category">${escapeHtml(formatT('stats_realtime_turn_label', 'Turn {turn}', { turn: Number(entry.turn_index || 0) }))}</span>
                    </div>
                    <span class="model-stats-error-time stats-entry-time">${formatDateTime(entry.created_at)}</span>
                </div>
            </div>
        `).join('');
    }

    function formatToolName(name) {
        if (!name) return 'Unknown';
        return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // KPI Updates
    function updateKPICards() {
        const ov = state.overview;
        if (!ov) return;

        setText('kpiTotalRequests', formatNumber(ov.total_requests));
        setText('kpiSuccessRate', ov.success_rate + '%');
        setText('kpiTotalTokens', formatNumber(ov.total_tokens));
        setText('kpiTotalCost', '$' + formatCost(ov.estimated_total_cost));
        setText('kpiAvgSpeed', ov.avg_tokens_per_second.toFixed(1) + ' tok/s');
        setText('kpiErrors', formatNumber(ov.error_count));

        // Update cost breakdown
        updateCostBreakdown(ov);
    }

    function updateCostBreakdown(ov) {
        const inputCost = ov.estimated_input_cost || 0;
        const outputCost = ov.estimated_output_cost || 0;
        const websearchCost = ov.estimated_websearch_cost || 0;
        const totalCost = ov.estimated_total_cost || 0;

        setText('kpiCostBreakdownTotal', '$' + formatCost(totalCost));
        setText('kpiInputCost', '$' + formatCost(inputCost));
        setText('kpiOutputCost', '$' + formatCost(outputCost));
        setText('kpiWebsearchCost', '$' + formatCost(websearchCost));

        // Update progress bar
        const inputBar = document.getElementById('kpiCostBarInput');
        const outputBar = document.getElementById('kpiCostBarOutput');
        const websearchBar = document.getElementById('kpiCostBarWebsearch');

        if (totalCost > 0) {
            const inputPct = (inputCost / totalCost) * 100;
            const outputPct = (outputCost / totalCost) * 100;
            const websearchPct = (websearchCost / totalCost) * 100;

            if (inputBar) inputBar.style.width = inputPct.toFixed(1) + '%';
            if (outputBar) outputBar.style.width = outputPct.toFixed(1) + '%';
            if (websearchBar) websearchBar.style.width = websearchPct.toFixed(1) + '%';
        } else {
            if (inputBar) inputBar.style.width = '0%';
            if (outputBar) outputBar.style.width = '0%';
            if (websearchBar) websearchBar.style.width = '0%';
        }
    }

    // Chart Updates
    function updateTimelineChart() {
        const canvas = document.getElementById('timelineChart');
        if (!canvas || !state.timeline?.timeline) return;

        const metric = el.timelineMetric?.value || 'requests';
        const timeline = state.timeline.timeline;

        const labels = timeline.map(t => formatTimeLabel(t.bucket_start_local || t.bucket_start, state.timeline.granularity));
        let data, label, borderColor, backgroundColor;

        switch (metric) {
            case 'tokens':
                data = timeline.map(t => t.total_tokens);
                label = t('stats_total_tokens_label', 'Total Tokens');
                borderColor = '#8b5cf6';
                backgroundColor = 'rgba(139, 92, 246, 0.1)';
                break;
            case 'cost':
                data = timeline.map(t => t.cost);
                label = t('stats_cost_label_currency', 'Cost ($)');
                borderColor = '#f59e0b';
                backgroundColor = 'rgba(245, 158, 11, 0.1)';
                break;
            default:
                data = timeline.map(t => t.requests);
                label = t('stats_col_requests', 'Requests');
                borderColor = '#3b82f6';
                backgroundColor = 'rgba(59, 130, 246, 0.1)';
        }

        if (charts.timeline) {
            charts.timeline.destroy();
        }

        const ctx = canvas.getContext('2d');
        charts.timeline = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label,
                    data,
                    borderColor,
                    backgroundColor,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    pointBackgroundColor: borderColor,
                }]
            },
            options: getChartOptions('line', metric === 'cost'),
        });
    }

    function updateProviderChart() {
        const canvas = document.getElementById('providerChart');
        if (!canvas || !state.providers?.providers) return;

        const metric = el.providerMetric?.value || 'requests';
        const providers = state.providers.providers;

        if (providers.length === 0) {
            if (charts.provider) {
                charts.provider.destroy();
                charts.provider = null;
            }
            renderEmptyChart(canvas, t('stats_provider_chart_empty', 'No provider data'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.provider) {
            charts.provider.destroy();
        }

        const labels = providers.map(resolveProviderDisplayName);
        let metricValues;
        switch (metric) {
            case 'tokens':
                metricValues = providers.map(p => p.total_tokens || 0);
                break;
            case 'cost':
                metricValues = providers.map(p => p.cost || 0);
                break;
            default:
                metricValues = providers.map(p => p.requests || 0);
        }
        const data = metricValues;

        const legendLimit = 7;
        const topLegendIndexes = new Set(
            providers
                .map((_, index) => ({ index, value: metricValues[index] ?? 0 }))
                .sort((a, b) => b.value - a.value)
                .slice(0, Math.min(legendLimit, providers.length))
                .map(entry => entry.index),
        );

        const ctx = canvas.getContext('2d');
        const options = getChartOptions('doughnut');
        if (options?.plugins?.legend?.labels) {
            options.plugins.legend.labels.filter = (legendItem) => {
                const itemIndex = typeof legendItem.index === 'number' ? legendItem.index : legendItem.datasetIndex;
                if (typeof itemIndex !== 'number') {
                    return true;
                }
                return topLegendIndexes.has(itemIndex);
            };
        }
        charts.provider = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [{
                    data,
                    backgroundColor: CHART_COLORS.primary.slice(0, providers.length),
                    borderWidth: 0,
                    hoverOffset: 8,
                }]
            },
            options,
        });
    }

    function updateCategoryChart() {
        const canvas = document.getElementById('categoryChart');
        if (!canvas || !state.categories?.categories) return;

        const metric = el.categoryMetric?.value || 'requests';
        const categories = state.categories.categories;

        if (categories.length === 0) {
            if (charts.category) {
                charts.category.destroy();
                charts.category = null;
            }
            renderEmptyChart(canvas, t('stats_category_chart_empty', 'No category data'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.category) {
            charts.category.destroy();
        }

        const labels = categories.map(c => formatCategoryLabel(c.category));
        let data;
        switch (metric) {
            case 'tokens':
                data = categories.map(c => c.total_tokens);
                break;
            case 'cost':
                data = categories.map(c => c.cost);
                break;
            default:
                data = categories.map(c => c.requests);
        }

        const ctx = canvas.getContext('2d');
        charts.category = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    data,
                    backgroundColor: CHART_COLORS.primary.slice(0, categories.length),
                    borderRadius: 6,
                    borderSkipped: false,
                }]
            },
            options: getChartOptions('bar'),
        });
    }

    function updateSuccessErrorChart() {
        const canvas = document.getElementById('successErrorChart');
        if (!canvas || !state.overview) return;

        const ov = state.overview;

        if (ov.total_requests === 0) {
            if (charts.successError) {
                charts.successError.destroy();
                charts.successError = null;
            }
            renderEmptyChart(canvas, t('stats_request_chart_empty', 'No request data'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.successError) {
            charts.successError.destroy();
        }

        const ctx = canvas.getContext('2d');
        charts.successError = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: [t('stats_success', 'Success'), t('stats_errors', 'Errors')],
                datasets: [{
                    data: [ov.success_count, ov.error_count],
                    backgroundColor: [CHART_COLORS.success, CHART_COLORS.error],
                    borderWidth: 0,
                    hoverOffset: 8,
                }]
            },
            options: getChartOptions('doughnut'),
        });
    }

    function updateTokenBreakdownChart() {
        const canvas = document.getElementById('tokenBreakdownChart');
        if (!canvas || !state.overview) return;

        const ov = state.overview;

        if (ov.total_tokens === 0) {
            if (charts.tokenBreakdown) {
                charts.tokenBreakdown.destroy();
                charts.tokenBreakdown = null;
            }
            renderEmptyChart(canvas, t('stats_token_chart_empty', 'No token data'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.tokenBreakdown) {
            charts.tokenBreakdown.destroy();
        }

        const ctx = canvas.getContext('2d');
        charts.tokenBreakdown = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: [t('stats_input_tokens', 'Input Tokens'), t('stats_output_tokens', 'Output Tokens'), t('stats_cached_tokens', 'Cached Tokens'), t('stats_cache_write_tokens', 'Cache Write Tokens'), t('stats_reasoning_tokens', 'Reasoning Tokens')],
                datasets: [{
                    data: [
                        Math.max(0, ov.total_input_tokens - ov.total_cached_tokens - (ov.total_cache_write_tokens || 0)),
                        ov.total_output_tokens - ov.total_reasoning_tokens,
                        ov.total_cached_tokens,
                        ov.total_cache_write_tokens || 0,
                        ov.total_reasoning_tokens,
                    ],
                    backgroundColor: CHART_COLORS.primary.slice(0, 5),
                    borderWidth: 0,
                    hoverOffset: 8,
                }]
            },
            options: getChartOptions('doughnut'),
        });
    }

    function updateThroughputChart() {
        const canvas = document.getElementById('throughputChart');
        if (!canvas || !state.throughput?.models) return;

        const models = state.throughput.models;

        if (models.length === 0) {
            if (charts.throughput) {
                charts.throughput.destroy();
                charts.throughput = null;
            }
            renderEmptyChart(canvas, t('stats_throughput_empty', 'No throughput data (requires generations > 2s or >= 1s with > 100 output tokens)'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.throughput) {
            charts.throughput.destroy();
        }

        const labels = models.map(getModelDisplayName);
        const avgData = models.map(m => m.avg_throughput);

        const ctx = canvas.getContext('2d');
        charts.throughput = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: t('stats_avg_tokens_per_second', 'Avg Tokens/sec'),
                    data: avgData,
                    backgroundColor: CHART_COLORS.primary.slice(0, models.length),
                    borderRadius: 6,
                    borderSkipped: false,
                }]
            },
            options: {
                ...getChartOptions('bar'),
                indexAxis: 'y',
                plugins: {
                    ...getChartOptions('bar').plugins,
                    legend: { display: false },
                    tooltip: {
                        ...getChartOptions('bar').plugins?.tooltip,
                        callbacks: {
                            title: (items) => {
                                if (!items?.length) return '';
                                const model = models[items[0].dataIndex];
                                const providerDisplay = model?.provider_name || formatProviderLabel(model?.provider);
                                if (providerDisplay) {
                                    return [getModelDisplayName(model), providerDisplay];
                                }
                                return getModelDisplayName(model);
                            },
                            label: (ctx) => {
                                const model = models[ctx.dataIndex];
                                return [
                                    `Avg: ${model.avg_throughput} tok/s`,
                                    `Min: ${model.min_throughput} tok/s`,
                                    `Max: ${model.max_throughput} tok/s`,
                                    `Samples: ${model.sample_count}`,
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ...getChartOptions('bar').scales?.x,
                        grid: { display: true, color: getChartOptions('bar').scales?.y?.grid?.color },
                        title: {
                            display: true,
                            text: t('stats_tokens_per_second', 'Tokens per second'),
                            color: getChartOptions('bar').scales?.x?.ticks?.color,
                        },
                    },
                    y: {
                        ...getChartOptions('bar').scales?.y,
                        grid: { display: false },
                        ticks: {
                            ...getChartOptions('bar').scales?.y?.ticks,
                            callback: (value) => labels[value] ?? value,
                        },
                    },
                },
            },
        });
    }

    function getModelDisplayName(model) {
        return model?.display_name || model?.model_name || model?.model_id || '—';
    }

    function updateErrorRatesChart() {
        const canvas = document.getElementById('errorRatesChart');
        if (!canvas || !state.errorRates?.models) return;

        const models = state.errorRates.models;

        if (models.length === 0) {
            if (charts.errorRates) {
                charts.errorRates.destroy();
                charts.errorRates = null;
            }
            renderEmptyChart(canvas, t('stats_model_chart_empty', 'No model data available'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.errorRates) {
            charts.errorRates.destroy();
        }

        const labels = models.map(m => m.model_name);
        const successData = models.map(m => m.success_rate);
        const errorData = models.map(m => m.error_rate);

        const isDark = document.documentElement.getAttribute('data-mode') === 'dark';
        const textColor = isDark ? '#e2e8f0' : '#475569';
        const gridColor = isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.06)';

        const ctx = canvas.getContext('2d');
        charts.errorRates = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: t('stats_success', 'Success'),
                        data: successData,
                        backgroundColor: 'rgba(16, 185, 129, 0.85)',
                        borderRadius: { topLeft: 6, bottomLeft: 6 },
                        borderSkipped: false,
                    },
                    {
                        label: t('stats_errors', 'Errors'),
                        data: errorData,
                        backgroundColor: 'rgba(239, 68, 68, 0.85)',
                        borderRadius: { topRight: 6, bottomRight: 6 },
                        borderSkipped: false,
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: isDark ? '#1e293b' : '#fff',
                        titleColor: isDark ? '#f8fafc' : '#0f172a',
                        bodyColor: isDark ? '#cbd5e1' : '#475569',
                        borderColor: isDark ? 'rgba(148, 163, 184, 0.2)' : 'rgba(15, 23, 42, 0.1)',
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            title: (items) => {
                                if (!items?.length) return '';
                                const model = models[items[0].dataIndex];
                                const providerDisplay = model?.provider_name || formatProviderLabel(model?.provider);
                                if (providerDisplay) {
                                    return [model.model_name, providerDisplay];
                                }
                                return model.model_name;
                            },
                            label: (ctx) => {
                                const model = models[ctx.dataIndex];
                                if (ctx.datasetIndex === 0) {
                                    return formatT('stats_error_rate_success_tooltip', `Success: ${model.success_rate}% (${model.success_count.toLocaleString()} requests)`, { rate: model.success_rate, count: model.success_count.toLocaleString() });
                                }
                                return formatT('stats_error_rate_error_tooltip', `Errors: ${model.error_rate}% (${model.error_count.toLocaleString()} requests)`, { rate: model.error_rate, count: model.error_count.toLocaleString() });
                            },
                            afterBody: (items) => {
                                const model = models[items[0].dataIndex];
                                return formatT('stats_error_rate_total_tooltip', `Total: ${model.total_requests.toLocaleString()} requests`, { count: model.total_requests.toLocaleString() });
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        stacked: true,
                        min: 0,
                        max: 100,
                        grid: { display: true, color: gridColor },
                        ticks: {
                            color: textColor,
                            font: { size: 11 },
                            callback: (v) => v + '%',
                        },
                        title: {
                            display: true,
                            text: t('stats_percentage', 'Percentage'),
                            color: textColor,
                        },
                    },
                    y: {
                        stacked: true,
                        grid: { display: false },
                        ticks: { color: textColor, font: { size: 11 } },
                    },
                },
            },
        });
    }

    function updateCostComparisonChart() {
        const canvas = document.getElementById('costComparisonChart');
        if (!canvas || !state.models?.models) return;

        const models = state.models.models.filter(m => m.cost > 0);

        if (models.length === 0) {
            if (charts.costComparison) {
                charts.costComparison.destroy();
                charts.costComparison = null;
            }
            renderEmptyChart(canvas, t('stats_cost_chart_empty', 'No cost data available'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.costComparison) {
            charts.costComparison.destroy();
        }

        // Sort by cost descending and take top models
        const sortedModels = [...models].sort((a, b) => b.cost - a.cost).slice(0, 15);
        const labels = sortedModels.map(m => m.model_name);
        const costData = sortedModels.map(m => m.cost);

        const isDark = document.documentElement.getAttribute('data-mode') === 'dark';
        const textColor = isDark ? '#e2e8f0' : '#475569';
        const gridColor = isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.06)';

        const ctx = canvas.getContext('2d');
        charts.costComparison = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: t('stats_cost_usd', 'Cost (USD)'),
                    data: costData,
                    backgroundColor: sortedModels.map((_, i) => CHART_COLORS.primary[i % CHART_COLORS.primary.length]),
                    borderRadius: 6,
                    borderSkipped: false,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: isDark ? '#1e293b' : '#fff',
                        titleColor: isDark ? '#f8fafc' : '#0f172a',
                        bodyColor: isDark ? '#cbd5e1' : '#475569',
                        borderColor: isDark ? 'rgba(148, 163, 184, 0.2)' : 'rgba(15, 23, 42, 0.1)',
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            title: (items) => {
                                if (!items?.length) return '';
                                const model = sortedModels[items[0].dataIndex];
                                const providerDisplay = model.provider_name || formatProviderLabel(model.provider);
                                if (providerDisplay) {
                                    return [model.model_name, providerDisplay];
                                }
                                return model.model_name;
                            },
                            label: (ctx) => {
                                const model = sortedModels[ctx.dataIndex];
                                return [
                                    `Cost: $${formatCost(model.cost)}`,
                                    `Requests: ${formatNumber(model.requests)}`,
                                    `Tokens: ${formatNumber(model.total_tokens)}`,
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { display: true, color: gridColor },
                        ticks: {
                            color: textColor,
                            font: { size: 11 },
                            callback: (v) => '$' + formatCost(v),
                        },
                        title: {
                            display: true,
                            text: t('stats_estimated_cost_usd', 'Estimated Cost (USD)'),
                            color: textColor,
                        },
                    },
                    y: {
                        grid: { display: false },
                        ticks: { color: textColor, font: { size: 11 } },
                    },
                },
            },
        });
    }

    function renderEmptyChart(canvas, message) {
        if (!canvas) return;
        const container = canvas.parentElement;
        if (!container) return;

        canvas.style.display = 'none';

        let messageEl = container.querySelector('.stats-chart-empty, .model-stats-chart-empty');
        if (!messageEl) {
            messageEl = document.createElement('p');
            messageEl.className = 'model-stats-chart-empty stats-chart-empty';
            container.appendChild(messageEl);
        }
        messageEl.textContent = message;
        messageEl.hidden = false;
    }

    function showChartCanvas(canvas) {
        if (!canvas) return;
        const container = canvas.parentElement;
        canvas.style.display = '';
        if (!container) return;
        const messageEl = container.querySelector('.stats-chart-empty, .model-stats-chart-empty');
        if (messageEl) {
            messageEl.remove();
        }
    }

    function getChartOptions(type, isCurrency = false) {
        const isDark = document.documentElement.getAttribute('data-mode') === 'dark';
        const textColor = isDark ? '#e2e8f0' : '#475569';
        const gridColor = isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.06)';

        const base = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: type === 'doughnut' ? 'bottom' : 'top',
                    labels: {
                        color: textColor,
                        usePointStyle: true,
                        padding: 16,
                        font: { size: 12, weight: '500' },
                    },
                },
                tooltip: {
                    backgroundColor: isDark ? '#1e293b' : '#fff',
                    titleColor: isDark ? '#f8fafc' : '#0f172a',
                    bodyColor: isDark ? '#cbd5e1' : '#475569',
                    borderColor: isDark ? 'rgba(148, 163, 184, 0.2)' : 'rgba(15, 23, 42, 0.1)',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 12,
                    titleFont: { weight: '600' },
                    callbacks: isCurrency ? {
                        label: (ctx) => `${ctx.dataset.label || ''}: $${ctx.parsed.y?.toFixed(4) || ctx.parsed}`
                    } : undefined,
                },
            },
        };

        if (type === 'line' || type === 'bar') {
            base.scales = {
                x: {
                    grid: { display: false },
                    ticks: { color: textColor, font: { size: 11 } },
                },
                y: {
                    grid: { color: gridColor },
                    ticks: {
                        color: textColor,
                        font: { size: 11 },
                        callback: isCurrency ? (v) => '$' + v.toFixed(2) : undefined,
                    },
                    beginAtZero: true,
                },
            };
        }

        if (type === 'doughnut') {
            base.cutout = '65%';
        }

        if (type === 'bar') {
            base.plugins.legend.display = false;
        }

        return base;
    }

    // Table Updates
    function updateModelsTable() {
        const tbody = document.getElementById('modelsTableBody');
        if (!tbody || !state.models?.models) return;

        const models = state.models.models;

        if (models.length === 0) {
            tbody.innerHTML = `<tr class="model-stats-table-empty stats-table-empty"><td colspan="8">${escapeHtml(t('stats_models_table_empty', 'No model usage data for this period.'))}</td></tr>`;
            return;
        }

        tbody.innerHTML = models.map(m => `
            <tr>
                <td data-label="${escapeHtml(t('stats_col_model', 'Model'))}"><span class="model-stats-model-name stats-model-name">${escapeHtml(m.model_name)}</span></td>
                <td data-label="${escapeHtml(t('stats_col_provider', 'Provider'))}"><span class="model-stats-provider-badge stats-provider-badge">${escapeHtml(m.provider_name || formatProviderLabel(m.provider))}</span></td>
                <td data-label="${escapeHtml(t('stats_col_requests', 'Requests'))}">${formatNumber(m.requests)}</td>
                <td data-label="${escapeHtml(t('stats_col_success', 'Success'))}" class="model-stats-success-cell">${formatNumber(m.success)}</td>
                <td data-label="${escapeHtml(t('stats_col_errors', 'Errors'))}" class="model-stats-error-cell">${m.errors > 0 ? formatNumber(m.errors) : '-'}</td>
                <td data-label="${escapeHtml(t('stats_col_tokens', 'Tokens'))}">${formatNumber(m.total_tokens)}</td>
                <td data-label="${escapeHtml(t('stats_col_avg_time', 'Avg Time'))}">${m.avg_generation_time > 0 ? m.avg_generation_time.toFixed(2) + 's' : '-'}</td>
                <td data-label="${escapeHtml(t('stats_col_cost', 'Cost'))}" class="model-stats-cost-cell">${m.cost > 0 ? '$' + formatCost(m.cost) : '-'}</td>
            </tr>
        `).join('');
    }

    // Errors List Update
    function updateErrorsList() {
        const container = document.getElementById('errorsListContainer');
        if (!container) return;

        const errors = state.errors.data;

        if (errors.length === 0) {
            container.innerHTML = `
                <div class="model-stats-empty stats-empty">
                    <div class="model-stats-empty-icon stats-empty-icon">
                        ${Icons.check}
                    </div>
                    <p class="model-stats-empty-title stats-empty-title">${escapeHtml(t('stats_errors_empty_title', 'No errors'))}</p>
                    <p class="model-stats-empty-text stats-empty-text">${escapeHtml(t('stats_errors_empty_text', 'Great! No LLM errors have been recorded in this period.'))}</p>
                </div>
            `;
            return;
        }

        container.innerHTML = errors.map(err => `
            <div class="model-stats-error-item">
                <div class="model-stats-error-header stats-entry-header">
                    <div class="model-stats-error-info">
                        <span class="model-stats-error-model stats-entry-model">${escapeHtml(err.model_name || err.model_id)}</span>
                        <span class="model-stats-error-provider stats-entry-provider">${escapeHtml(formatProviderLabel(err.provider))}</span>
                        <span class="model-stats-error-category">${escapeHtml(err.category)}</span>
                        ${err.error_status_code ? `<span class="model-stats-error-code">${err.error_status_code}</span>` : ''}
                    </div>
                    <span class="model-stats-error-time stats-entry-time">${formatDateTime(err.created_at)}</span>
                </div>
                ${err.error_message ? `<div class="model-stats-error-message">${escapeHtml(err.error_message)}</div>` : ''}
            </div>
        `).join('');
    }

    function updateErrorsPagination() {
        if (el.errorsPrevBtn) {
            el.errorsPrevBtn.disabled = state.errors.page <= 1;
        }
        if (el.errorsNextBtn) {
            el.errorsNextBtn.disabled = state.errors.page >= state.errors.totalPages;
        }
        if (el.errorsPaginationInfo) {
            el.errorsPaginationInfo.textContent = formatT('admin_page_of', `Page ${state.errors.page} of ${state.errors.totalPages || 1}`, {
                page: state.errors.page,
                total: state.errors.totalPages || 1,
            });
        }
    }

    // Helpers
    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function formatNumber(num) {
        if (num === null || num === undefined) return '-';
        if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B';
        if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M';
        if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
        return num.toLocaleString();
    }

    function formatCost(cost) {
        if (cost === null || cost === undefined || cost === 0) return '0.00';
        if (cost < 0.0001) return cost.toFixed(6);
        if (cost < 0.01) return cost.toFixed(4);
        if (cost < 1) return cost.toFixed(3);
        return cost.toFixed(2);
    }

    function formatProviderLabel(provider) {
        if (window.formatProviderLabel) return window.formatProviderLabel(provider);
        return provider?.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || 'Unknown';
    }

    function restoreProviderSelection(selectEl, providerId, providerType) {
        if (!selectEl) return;
        const options = Array.from(selectEl.options || []);
        const target = options.find(opt => {
            const optProviderId = opt.dataset?.providerId || '';
            const optProviderType = opt.dataset?.providerType || '';
            if (providerId && optProviderId) {
                return optProviderId === providerId;
            }
            if (providerType && optProviderType) {
                return optProviderType === providerType;
            }
            return false;
        });
        if (target) {
            selectEl.value = target.value;
        } else {
            selectEl.value = '';
        }
    }

    function resolveProviderFilterSelection(selectEl) {
        if (!selectEl) return null;
        const option = selectEl.selectedOptions ? selectEl.selectedOptions[0] : selectEl.options[selectEl.selectedIndex];
        if (!option) return null;
        const providerId = option.dataset?.providerId || '';
        const providerType = option.dataset?.providerType || '';
        if (!providerId && !providerType) {
            return null;
        }
        return { providerId, providerType };
    }

    function resolveProviderDisplayName(providerEntry) {
        if (!providerEntry) {
            return 'Unknown';
        }
        const name = providerEntry.provider_name;
        if (typeof name === 'string' && name.trim()) {
            return name.trim();
        }
        return formatProviderLabel(providerEntry.provider);
    }

    function formatCategoryLabel(category) {
        return category?.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || 'Unknown';
    }

    function formatTimeLabel(bucketStart, granularity) {
        if (!bucketStart) return '';
        const date = bucketStart instanceof Date ? bucketStart : new Date(bucketStart);
        
        if (granularity === 'hourly') {
            return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit' });
        }
        if (granularity === 'daily') {
            return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
        }
        if (granularity === 'weekly') {
            return `Week of ${date.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
        }
        if (granularity === 'monthly') {
            return date.toLocaleDateString([], { month: 'short', year: 'numeric' });
        }
        return date.toLocaleString();
    }

    function formatDateTime(isoString) {
        if (!isoString) return t('common_unknown', 'Unknown');
        const d = new Date(isoString);
        return d.toLocaleString([], {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // Delete all statistics
    function showDeleteOverlay() {
        if (el.deleteOverlay) {
            el.deleteOverlay.hidden = false;
            el.deleteOverlay.classList.add('active');
        }
    }

    function hideDeleteOverlay() {
        if (el.deleteOverlay) {
            el.deleteOverlay.hidden = true;
            el.deleteOverlay.classList.remove('active');
        }
    }

    async function deleteAllStatistics() {
        if (!el.deleteConfirmBtn) return;

        const originalHtml = el.deleteConfirmBtn.innerHTML;

        try {
            // Show loading state
            el.deleteConfirmBtn.disabled = true;
            el.deleteConfirmBtn.innerHTML = `
                ${Icons.refresh}
                <span>${escapeHtml(t('admin_deleting', 'Deleting...'))}</span>
            `;

            const response = await authedFetch('/api/v1/llmstats/admin/all', {
                method: 'DELETE',
            });

            if (response.ok) {
                const data = await response.json();
                hideDeleteOverlay();
                notifySuccess?.(formatT('stats_delete_success', `Deleted ${data.deleted_count.toLocaleString()} statistics entries`, { count: data.deleted_count.toLocaleString() }));
                await loadAllData();
            } else {
                const error = await response.json().catch(() => ({}));
                notifyError?.(error.detail || t('stats_delete_failed', 'Failed to delete statistics'));
            }
        } catch (err) {
            console.error('Failed to delete statistics:', err);
            notifyError?.(t('stats_delete_failed', 'Failed to delete statistics'));
        } finally {
            el.deleteConfirmBtn.innerHTML = originalHtml;
            el.deleteConfirmBtn.disabled = false;
        }
    }

    // Export statistics as JSON
    async function exportStatistics() {
        if (!el.exportBtn) return;

        const btnSpan = el.exportBtn.querySelector('span');
        const originalText = btnSpan?.textContent || t('stats_export_btn', 'Export Statistics');
        const originalDisabled = el.exportBtn.disabled;

        try {
            // Show loading state
            if (btnSpan) btnSpan.textContent = t('admin_exporting_ellipsis', 'Exporting...');
            el.exportBtn.disabled = true;
            el.exportBtn.classList.add('loading');

            const response = await authedFetch('/api/v1/llmstats/admin/export');

            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || t('stats_export_failed', 'Failed to export statistics'));
            }

            const data = await response.json();

            // Create and download the JSON file
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            
            // Generate filename with date
            const now = new Date();
            const dateStr = now.toISOString().split('T')[0];
            a.download = `llm-generation-stats-export-${dateStr}.json`;
            
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            const totalCount = data.data?.total_count || 0;
            notifySuccess?.(formatT('stats_export_success', `Exported ${totalCount.toLocaleString()} statistics records`, { count: totalCount.toLocaleString() }));
        } catch (err) {
            console.error('Failed to export statistics:', err);
            notifyError?.(err.message || t('stats_export_failed', 'Failed to export statistics'));
        } finally {
            if (btnSpan) btnSpan.textContent = originalText;
            el.exportBtn.disabled = originalDisabled;
            el.exportBtn.classList.remove('loading');
        }
    }

    // Handle import file selection

    async function deleteRealtimeStatistics() {
        if (!el.realtimeDeleteBtn) return;
        const ok = await window.showDeleteConfirm({
            title: t('stats_realtime_delete_btn', 'Delete Realtime Stats'),
            message: t('stats_realtime_delete_confirm', 'Delete all realtime statistics? This action cannot be undone.'),
            confirmLabel: t('stats_realtime_delete_btn', 'Delete Realtime Stats'),
        });
        if (!ok) return;

        const button = el.realtimeDeleteBtn;
        const span = button.querySelector('span');
        const original = span ? span.textContent : t('stats_realtime_delete_btn', 'Delete Realtime Stats');
        button.disabled = true;
        if (span) span.textContent = t('admin_deleting', 'Deleting...');
        try {
            const response = await authedFetch('/api/v1/llmstats/admin/realtime/all', {
                method: 'DELETE',
            });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || t('stats_realtime_delete_failed', 'Failed to delete realtime statistics'));
            }
            const data = await response.json();
            notifySuccess?.(formatT('stats_realtime_delete_success', `Deleted ${formatNumber(data.deleted_sessions || 0)} sessions and ${formatNumber(data.deleted_turns || 0)} turns`, {
                sessions: formatNumber(data.deleted_sessions || 0),
                turns: formatNumber(data.deleted_turns || 0),
            }));
            state.realtimeErrors.page = 1;
            await Promise.all([
                loadRealtimeOverviewData(),
                loadRealtimeTimelineData(),
                loadRealtimeByModelData(),
                loadRealtimeErrorsData(),
                loadRealtimeInterruptionsData(),
            ]);
        } catch (err) {
            console.error('Failed to delete realtime statistics:', err);
            notifyError?.(err.message || t('stats_realtime_delete_failed', 'Failed to delete realtime statistics'));
        } finally {
            button.disabled = false;
            if (span) span.textContent = original;
        }
    }

    async function exportRealtimeStatistics() {
        if (!el.realtimeExportBtn) return;
        const button = el.realtimeExportBtn;
        const span = button.querySelector('span');
        const original = span ? span.textContent : t('stats_realtime_export_btn', 'Export Realtime Stats');
        button.disabled = true;
        if (span) span.textContent = t('admin_exporting_ellipsis', 'Exporting...');
        try {
            const response = await authedFetch(`/api/v1/llmstats/admin/realtime/export?days=${encodeURIComponent(state.period)}`);
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || t('stats_realtime_export_failed', 'Failed to export realtime statistics'));
            }
            const payload = await response.json();
            const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            const dateStr = new Date().toISOString().split('T')[0];
            anchor.href = url;
            anchor.download = `realtime-stats-export-${dateStr}.json`;
            document.body.appendChild(anchor);
            anchor.click();
            document.body.removeChild(anchor);
            URL.revokeObjectURL(url);
            const sessionCount = Array.isArray(payload?.data?.sessions) ? payload.data.sessions.length : 0;
            notifySuccess?.(formatT('stats_realtime_export_success', `Exported ${formatNumber(sessionCount)} realtime sessions`, { count: formatNumber(sessionCount) }));
        } catch (err) {
            console.error('Failed to export realtime statistics:', err);
            notifyError?.(err.message || t('stats_realtime_export_failed', 'Failed to export realtime statistics'));
        } finally {
            button.disabled = false;
            if (span) span.textContent = original;
        }
    }

    function startAutoRefresh() {
        if (autoRefreshTimer) return;
        autoRefreshTimer = setInterval(() => {
            if (!isPageActive || document.hidden) return;
            loadAllData();
        }, AUTO_REFRESH_INTERVAL);
    }

    function stopAutoRefresh() {
        if (!autoRefreshTimer) return;
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && isPageActive) {
            loadAllData();
        }
    });

    window.addEventListener('admin:page-activated', (e) => {
        const page = e.detail?.page;
        if (page === 'model-statistics') {
            initModelStatisticsPage();
        } else if (isPageActive) {
            isPageActive = false;
            stopAutoRefresh();
        }
    });

})();
