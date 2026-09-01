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
        settings: null,
        users: [],
        groups: [],
        overview: [],
        overviewTotal: 0,
        overviewLimit: 50,
        overviewOffset: 0,
        period: 30,
        // Detail view state
        detailMode: 'users', // 'users' or 'groups'
        selectedEntityId: null,
        detailPeriod: 30,
        detailLoading: false,
        detailSuccessActive: false,
        detailSuccessTimer: null,
        // Detail data
        detailOverview: null,
        detailTimeline: null,
        detailModels: null,
        detailProviders: null,
        detailCategories: null,
        detailToolCalls: null,
        detailErrors: { data: [], page: 1, totalPages: 1 },
        detailGroupUsers: null,
    };

    // Charts
    let charts = {
        timeline: null,
        token: null,
        provider: null,
        category: null,
        successError: null,
        tool: null,
    };

    let refreshCooldown = false;
    let detailRefreshCooldown = false;

    // Beautiful color palette
    const CHART_COLORS = {
        primary: ['#3b82f6', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#6366f1', '#14b8a6', '#f97316'],
        success: '#10b981',
        error: '#ef4444',
        input: '#3b82f6',
        output: '#8b5cf6',
    };

    const el = {
        enableToggle: null,
        trackAllToggle: null,
        settingsSection: null,
        overviewSection: null,
        userList: null,
        overviewTable: null,
        addUserBtn: null,
        periodSelect: null,
        refreshBtn: null,
        // Modals
        regulatoryModal: null,
        regulatoryCheckbox: null,
        regulatoryConfirmBtn: null,
        regulatoryCancelBtn: null,
        addUserModal: null,
        addUserSearch: null,
        addUserResults: null,
        addUserConfirmBtn: null,
        addUserCancelBtn: null,
        disableModal: null,
        disableConfirmBtn: null,
        disableCancelBtn: null,
        // Detail view
        detailedSection: null,
        tabUsers: null,
        tabGroups: null,
        entitySelect: null,
        detailPeriodSelect: null,
        detailRefreshBtn: null,
        detailContent: null,
        detailEmpty: null,
        // KPI elements
        kpiRequests: null,
        kpiTokens: null,
        kpiSuccessRate: null,
        kpiCost: null,
        kpiToolCalls: null,
        // Chart controls
        timelineGranularity: null,
        timelineMetric: null,
        // Tables
        modelsTableBody: null,
        toolsTableBody: null,
        errorsList: null,
        errorsPrevBtn: null,
        errorsNextBtn: null,
        errorsPageInfo: null,
        groupUsersSection: null,
        groupUsersTableBody: null,
    };

    const fetchWithAuth = (input, init = {}) => {
        const executor = typeof window !== "undefined" && typeof window.authedFetch === "function"
            ? window.authedFetch
            : fetch;
        return executor(input, init);
    };

    function init() {
        if (state.initialized) return;

        // Cache DOM elements - original
        el.enableToggle = document.getElementById('userStatsEnableToggle');
        el.trackAllToggle = document.getElementById('userStatsTrackAllToggle');
        el.settingsSection = document.getElementById('userStatsSettingsSection');
        el.overviewSection = document.getElementById('userStatsOverviewSection');
        el.userList = document.getElementById('userStatsUserList');
        el.overviewTable = document.getElementById('userStatsOverviewTable');
        el.addUserBtn = document.getElementById('userStatsAddUserBtn');
        el.periodSelect = document.getElementById('userStatsPeriodSelect');
        el.refreshBtn = document.getElementById('userStatsRefreshBtn');

        // Modals
        el.regulatoryModal = document.getElementById('userStatsRegulatoryModal');
        el.regulatoryCheckbox = document.getElementById('userStatsRegulatoryCheckbox');
        el.regulatoryConfirmBtn = document.getElementById('userStatsRegulatoryConfirmBtn');
        el.regulatoryCancelBtn = document.getElementById('userStatsRegulatoryCancelBtn');

        el.addUserModal = document.getElementById('userStatsAddUserModal');
        el.addUserSearch = document.getElementById('userStatsAddUserSearch');
        el.addUserResults = document.getElementById('userStatsAddUserResults');
        el.addUserConfirmBtn = document.getElementById('userStatsAddUserConfirmBtn');
        el.addUserCancelBtn = document.getElementById('userStatsAddUserCancelBtn');

        el.disableModal = document.getElementById('userStatsDisableModal');
        el.disableConfirmBtn = document.getElementById('userStatsDisableConfirmBtn');
        el.disableCancelBtn = document.getElementById('userStatsDisableCancelBtn');

        // Detail view elements
        el.detailedSection = document.getElementById('userStatsDetailedSection');
        el.tabUsers = document.getElementById('userStatsTabUsers');
        el.tabGroups = document.getElementById('userStatsTabGroups');
        el.entitySelect = document.getElementById('userStatsEntitySelect');
        el.detailPeriodSelect = document.getElementById('userStatsDetailPeriodSelect');
        el.detailRefreshBtn = document.getElementById('userStatsDetailRefreshBtn');
        el.detailContent = document.getElementById('userStatsDetailContent');
        el.detailEmpty = document.getElementById('userStatsDetailEmpty');

        // KPI elements
        el.kpiRequests = document.getElementById('userStatsKpiRequests');
        el.kpiTokens = document.getElementById('userStatsKpiTokens');
        el.kpiSuccessRate = document.getElementById('userStatsKpiSuccessRate');
        el.kpiCost = document.getElementById('userStatsKpiCost');
        el.kpiToolCalls = document.getElementById('userStatsKpiToolCalls');

        // Chart controls
        el.timelineGranularity = document.getElementById('userStatsTimelineGranularity');
        el.timelineMetric = document.getElementById('userStatsTimelineMetric');

        // Tables
        el.modelsTableBody = document.getElementById('userStatsModelsTableBody');
        el.toolsTableBody = document.getElementById('userStatsToolsTableBody');
        el.errorsList = document.getElementById('userStatsErrorsList');
        el.errorsPrevBtn = document.getElementById('userStatsErrorsPrevBtn');
        el.errorsNextBtn = document.getElementById('userStatsErrorsNextBtn');
        el.errorsPageInfo = document.getElementById('userStatsErrorsPageInfo');
        el.groupUsersSection = document.getElementById('userStatsGroupUsersSection');
        el.groupUsersTableBody = document.getElementById('userStatsGroupUsersTableBody');

        // Move modals to body to avoid CSS transform issues with position:fixed
        [el.regulatoryModal, el.addUserModal, el.disableModal].forEach(modal => {
            if (modal) document.body.appendChild(modal);
        });

        setupEventListeners();
        state.initialized = true;
    }

    function setupEventListeners() {
        // Enable toggle
        if (el.enableToggle) {
            el.enableToggle.addEventListener('change', handleEnableToggle);
        }

        // Track all toggle
        if (el.trackAllToggle) {
            el.trackAllToggle.addEventListener('change', handleTrackAllToggle);
        }

        // Add user button
        if (el.addUserBtn) {
            el.addUserBtn.addEventListener('click', showAddUserModal);
        }

        // Period select
        if (el.periodSelect) {
            el.periodSelect.addEventListener('change', () => {
                state.period = parseInt(el.periodSelect.value, 10);
                state.overviewOffset = 0;
                loadOverview();
            });
        }

        // Refresh button
        if (el.refreshBtn) {
            el.refreshBtn.addEventListener('click', () => {
                if (!state.loading && !refreshCooldown) {
                    loadAllData({ isManualRefresh: true });
                }
            });
        }

        // Regulatory modal
        if (el.regulatoryCheckbox) {
            el.regulatoryCheckbox.addEventListener('change', () => {
                if (el.regulatoryConfirmBtn) {
                    el.regulatoryConfirmBtn.disabled = !el.regulatoryCheckbox.checked;
                }
            });
        }
        if (el.regulatoryConfirmBtn) {
            el.regulatoryConfirmBtn.addEventListener('click', confirmEnableTracking);
        }
        if (el.regulatoryCancelBtn) {
            el.regulatoryCancelBtn.addEventListener('click', hideRegulatoryModal);
        }
        if (el.regulatoryModal) {
            el.regulatoryModal.addEventListener('click', (e) => {
                if (e.target === el.regulatoryModal) hideRegulatoryModal();
            });
        }

        // Add user modal
        if (el.addUserSearch) {
            el.addUserSearch.addEventListener('input', debounce(filterUsers, 200));
        }
        if (el.addUserConfirmBtn) {
            el.addUserConfirmBtn.addEventListener('click', confirmAddUser);
        }
        if (el.addUserCancelBtn) {
            el.addUserCancelBtn.addEventListener('click', hideAddUserModal);
        }
        if (el.addUserModal) {
            el.addUserModal.addEventListener('click', (e) => {
                if (e.target === el.addUserModal) hideAddUserModal();
            });
        }

        // Disable modal
        if (el.disableConfirmBtn) {
            el.disableConfirmBtn.addEventListener('click', confirmDisableTracking);
        }
        if (el.disableCancelBtn) {
            el.disableCancelBtn.addEventListener('click', hideDisableModal);
        }
        if (el.disableModal) {
            el.disableModal.addEventListener('click', (e) => {
                if (e.target === el.disableModal) hideDisableModal();
            });
        }

        // Detail view - Tabs
        if (el.tabUsers) {
            el.tabUsers.addEventListener('click', () => switchTab('users'));
        }
        if (el.tabGroups) {
            el.tabGroups.addEventListener('click', () => switchTab('groups'));
        }

        // Entity select
        if (el.entitySelect) {
            el.entitySelect.addEventListener('change', handleEntitySelect);
        }

        // Detail period select
        if (el.detailPeriodSelect) {
            el.detailPeriodSelect.addEventListener('change', () => {
                state.detailPeriod = parseInt(el.detailPeriodSelect.value, 10);
                state.detailErrors.page = 1;
                if (state.selectedEntityId) {
                    loadDetailData();
                }
            });
        }

        // Detail refresh button
        if (el.detailRefreshBtn) {
            el.detailRefreshBtn.addEventListener('click', () => {
                if (state.selectedEntityId && !state.detailLoading && !detailRefreshCooldown) {
                    loadDetailData({ isManualRefresh: true });
                }
            });
        }

        // Timeline controls
        if (el.timelineGranularity) {
            el.timelineGranularity.addEventListener('change', () => {
                if (state.selectedEntityId) loadTimelineData();
            });
        }
        if (el.timelineMetric) {
            el.timelineMetric.addEventListener('change', updateTimelineChart);
        }

        // Error pagination
        if (el.errorsPrevBtn) {
            el.errorsPrevBtn.addEventListener('click', () => {
                if (state.detailErrors.page > 1) {
                    state.detailErrors.page--;
                    loadErrorsData();
                }
            });
        }
        if (el.errorsNextBtn) {
            el.errorsNextBtn.addEventListener('click', () => {
                if (state.detailErrors.page < state.detailErrors.totalPages) {
                    state.detailErrors.page++;
                    loadErrorsData();
                }
            });
        }
    }

    function debounce(func, wait) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    }

    async function loadAllData({ isManualRefresh = false } = {}) {
        if (state.loading) return;
        state.loading = true;

        if (el.refreshBtn) {
            if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                window.adminSetRefreshButtonLoadingState(el.refreshBtn, true);
            } else {
                el.refreshBtn.classList.add('is-loading');
                el.refreshBtn.disabled = true;
            }
        }

        try {
            // Settings must load first because overview depends on enabled state
            await loadSettings();
            await Promise.all([
                loadOverview(),
                loadGroups(),
            ]);
            populateEntitySelect();

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
                    el.refreshBtn.classList.add('is-success');
                    el.refreshBtn.disabled = true;
                    setTimeout(() => {
                        el.refreshBtn.classList.remove('is-success');
                        el.refreshBtn.disabled = false;
                        refreshCooldown = false;
                    }, 3000);
                }
            }
        } catch (err) {
            console.error('Failed to load user statistics data:', err);
            notifyError?.(t('user_stats_load_failed', 'Failed to load user statistics data'));
            refreshCooldown = false;
            if (el.refreshBtn && typeof window.adminResetRefreshButtonState === 'function') {
                window.adminResetRefreshButtonState(el.refreshBtn);
            }
        } finally {
            state.loading = false;
            const manualRefreshSuccessActive = isManualRefresh && refreshCooldown;
            if (el.refreshBtn && !manualRefreshSuccessActive) {
                if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                    window.adminSetRefreshButtonLoadingState(el.refreshBtn, false);
                } else {
                    el.refreshBtn.classList.remove('is-loading');
                    el.refreshBtn.disabled = false;
                }
            }
        }
    }

    async function loadSettings() {
        try {
            const response = await fetchWithAuth('/api/v1/llmstats/admin/user-statistics/settings', {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load settings');
            
            state.settings = await response.json();
            updateUI();
        } catch (err) {
            console.error('Failed to load user statistics settings:', err);
        }
    }

    async function loadOverview() {
        if (!state.settings?.enabled) {
            state.overview = [];
            state.overviewTotal = 0;
            state.users = Array.isArray(state.settings?.tracked_users) ? state.settings.tracked_users : [];
            renderOverview();
            return;
        }

        try {
            const response = await fetchWithAuth(`/api/v1/llmstats/admin/user-statistics/tracked-users-overview?days=${state.period}&limit=${state.overviewLimit}&offset=${state.overviewOffset}`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load overview');
            
            const data = await response.json();
            state.overview = data.users || [];
            state.overviewTotal = data.total || 0;
            state.users = data.users || [];
            renderOverview();
        } catch (err) {
            console.error('Failed to load user statistics overview:', err);
        }
    }

    async function loadGroups() {
        try {
            const response = await fetchWithAuth('/api/v1/llmstats/admin/group-statistics/overview?days=' + state.period, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load groups');
            
            const data = await response.json();
            state.groups = data.groups || [];
        } catch (err) {
            console.error('Failed to load groups:', err);
        }
    }

    function updateUI() {
        if (!state.settings) return;
        const trackingEnabled = Boolean(state.settings.enabled && state.settings.regulatory_confirmed);

        // Update enable toggle
        if (el.enableToggle) {
            el.enableToggle.checked = trackingEnabled;
        }

        // Update track all toggle
        if (el.trackAllToggle) {
            el.trackAllToggle.checked = state.settings.track_all_users;
        }

        // Update settings section state
        if (el.settingsSection) {
            el.settingsSection.hidden = !trackingEnabled;
            el.settingsSection.classList.toggle('is-disabled', !trackingEnabled);
        }

        // Hide preview sections when tracking is disabled
        if (el.overviewSection) {
            el.overviewSection.hidden = !trackingEnabled;
        }

        // Show/hide detailed section based on enabled state
        if (el.detailedSection) {
            el.detailedSection.hidden = !trackingEnabled;
            el.detailedSection.classList.toggle('is-disabled', !trackingEnabled);
        }

        // Render user list
        renderUserList();
    }

    function renderUserList() {
        if (!el.userList) return;

        const trackedUsers = state.settings?.tracked_users || [];

        if (trackedUsers.length === 0) {
            el.userList.innerHTML = `
                <div class="user-stats-empty">
                    ${Icons.groups}
                    <p>${t('user_stats_tracked_users_empty', 'No users are being tracked. Add users to start collecting statistics.')}</p>
                </div>
            `;
            return;
        }

        el.userList.innerHTML = trackedUsers.map(user => `
            <div class="user-stats-user-item" data-user-id="${user.id}">
                <div class="user-stats-user-info">
                    <div class="user-stats-user-avatar">${getInitials(user.email, user.name)}</div>
                    <div class="user-stats-user-details">
                        <div class="user-stats-user-email">${escapeHtml(user.email)}</div>
                        ${user.name ? `<div class="user-stats-user-name">${escapeHtml(user.name)}</div>` : ''}
                    </div>
                </div>
                <div class="user-stats-user-actions">
                    <button type="button" class="user-stats-remove-btn" title="${t('user_stats_remove_title', 'Remove from tracking')}" data-action="remove" data-user-id="${user.id}">
                        ${Icons.close}
                    </button>
                </div>
            </div>
        `).join('');

        // Add event listeners
        el.userList.querySelectorAll('[data-action="remove"]').forEach(btn => {
            btn.addEventListener('click', () => removeUser(btn.dataset.userId));
        });
    }

    function renderOverview() {
        if (!el.overviewTable) return;

        const tbody = el.overviewTable.querySelector('tbody');
        if (!tbody) return;

        if (state.overview.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" class="user-stats-overview-empty-cell">
                        ${t('user_stats_overview_empty', 'No statistics data available for the selected period.')}
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = state.overview.map(item => `
            <tr class="user-stats-row-clickable" data-user-id="${item.user.id}">
                <td data-label="${escapeHtml(t('user_stats_col_user', 'User'))}">
                    <div class="user-stats-user-info user-stats-user-info--compact">
                        <div class="user-stats-user-avatar user-stats-user-avatar--sm">${getInitials(item.user.email, item.user.name)}</div>
                        <div class="user-stats-user-details">
                            <div class="user-stats-user-email">${escapeHtml(item.user.email)}</div>
                        </div>
                    </div>
                </td>
                <td data-label="${escapeHtml(t('user_stats_col_llm_requests', 'LLM Requests'))}">${formatNumber(item.llm_requests)}</td>
                <td data-label="${escapeHtml(t('user_stats_col_tool_calls', 'Tool Calls'))}">${formatNumber(item.tool_calls)}</td>
                <td data-label="${escapeHtml(t('user_stats_col_tokens', 'Tokens'))}" class="user-stats-tokens-cell">${formatNumber(item.total_tokens)}</td>
                <td data-label="${escapeHtml(t('user_stats_col_cost', 'Est. Cost'))}" class="user-stats-cost-cell">$${item.estimated_cost.toFixed(4)}</td>
            </tr>
        `).join('');

        // Add click handlers to rows
        tbody.querySelectorAll('.user-stats-row-clickable').forEach(row => {
            row.addEventListener('click', () => {
                const userId = row.dataset.userId;
                if (userId) {
                    switchTab('users');
                    if (el.entitySelect) {
                        el.entitySelect.value = 'user:' + userId;
                    }
                    state.selectedEntityId = userId;
                    state.detailMode = 'users';
                    loadDetailData();
                }
            });
        });

        // Render pagination
        let paginationEl = el.overviewTable.parentElement.querySelector('.user-stats-pagination');
        if (!paginationEl) {
            paginationEl = document.createElement('div');
            paginationEl.className = 'user-stats-pagination';
            el.overviewTable.parentElement.appendChild(paginationEl);
        }

        if (state.overviewTotal > state.overviewLimit) {
            const currentPage = Math.floor(state.overviewOffset / state.overviewLimit) + 1;
            const totalPages = Math.ceil(state.overviewTotal / state.overviewLimit);
            paginationEl.innerHTML = `
                <button type="button" class="user-stats-page-btn" data-action="prev" ${state.overviewOffset === 0 ? 'disabled' : ''}>← ${t('btn_previous', 'Previous')}</button>
                <span class="user-stats-page-info">${formatT('admin_page_of', `Page ${currentPage} of ${totalPages}`, { page: currentPage, total: totalPages })}</span>
                <button type="button" class="user-stats-page-btn" data-action="next" ${state.overviewOffset + state.overviewLimit >= state.overviewTotal ? 'disabled' : ''}>${t('btn_next', 'Next')} →</button>
            `;
            paginationEl.querySelector('[data-action="prev"]')?.addEventListener('click', () => {
                state.overviewOffset = Math.max(0, state.overviewOffset - state.overviewLimit);
                loadOverview();
            });
            paginationEl.querySelector('[data-action="next"]')?.addEventListener('click', () => {
                state.overviewOffset += state.overviewLimit;
                loadOverview();
            });
            paginationEl.style.display = '';
        } else {
            paginationEl.innerHTML = '';
            paginationEl.style.display = 'none';
        }
    }

    // Tab switching
    function switchTab(mode) {
        state.detailMode = mode;
        state.selectedEntityId = null;
        
        if (el.tabUsers) el.tabUsers.classList.toggle('active', mode === 'users');
        if (el.tabGroups) el.tabGroups.classList.toggle('active', mode === 'groups');
        
        populateEntitySelect();
        showEmptyState();
    }

    function populateEntitySelect() {
        if (!el.entitySelect) return;

        const placeholder = state.detailMode === 'users' 
            ? t('user_stats_select_user', '-- Select User --')
            : t('user_stats_select_group', '-- Select Group --');

        let options = `<option value="">${placeholder}</option>`;

        if (state.detailMode === 'users') {
            const usersFromSettings = Array.isArray(state.settings?.tracked_users) ? state.settings.tracked_users : [];
            const usersFromOverview = Array.isArray(state.users) ? state.users.map(item => item.user || item) : [];
            const mergedUsers = [...usersFromSettings, ...usersFromOverview];
            const seenUserIds = new Set();

            mergedUsers.forEach(user => {
                if (!user?.id || seenUserIds.has(user.id)) return;
                seenUserIds.add(user.id);
                const label = user.name ? `${user.email} (${user.name})` : user.email;
                options += `<option value="user:${user.id}">${escapeHtml(label)}</option>`;
            });
        } else {
            state.groups.forEach(item => {
                const group = item.group || item;
                options += `<option value="group:${group.id}">${escapeHtml(group.name)}</option>`;
            });
        }

        el.entitySelect.innerHTML = options;
    }

    function handleEntitySelect() {
        const value = el.entitySelect?.value;
        if (!value) {
            state.selectedEntityId = null;
            showEmptyState();
            return;
        }

        const [type, id] = value.split(':');
        state.detailMode = type === 'group' ? 'groups' : 'users';
        state.selectedEntityId = id;
        state.detailErrors.page = 1;
        loadDetailData();
    }

    function showEmptyState() {
        if (el.detailContent) el.detailContent.hidden = true;
        if (el.detailEmpty) el.detailEmpty.hidden = false;
        if (el.groupUsersSection) el.groupUsersSection.hidden = true;
    }

    function showDetailContent() {
        if (el.detailContent) el.detailContent.hidden = false;
        if (el.detailEmpty) el.detailEmpty.hidden = true;
        
        // Show group users section only for groups
        if (el.groupUsersSection) {
            el.groupUsersSection.hidden = state.detailMode !== 'groups';
        }
    }

    // Load all detail data
    async function loadDetailData({ isManualRefresh = false } = {}) {
        if (!state.selectedEntityId) return;
        
        const entityId = state.selectedEntityId;
        state.detailLoading = true;
        if (el.detailRefreshBtn) {
            if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                window.adminSetRefreshButtonLoadingState(el.detailRefreshBtn, true);
            } else {
                el.detailRefreshBtn.classList.add('is-loading');
                el.detailRefreshBtn.disabled = true;
            }
        }

        showDetailContent();

        try {
            await Promise.all([
                loadOverviewData(),
                loadTimelineData(),
                loadModelsData(),
                loadProvidersData(),
                loadCategoriesData(),
                loadToolCallsData(),
                loadErrorsData(),
                state.detailMode === 'groups' ? loadGroupUsersData() : Promise.resolve(),
            ]);
            // Discard results if the user switched to a different entity during loading
            if (state.selectedEntityId !== entityId) return;

            if (isManualRefresh && el.detailRefreshBtn) {
                const detailSuccessDuration = 3000;
                detailRefreshCooldown = true;
                state.detailSuccessActive = true;
                if (state.detailSuccessTimer) {
                    clearTimeout(state.detailSuccessTimer);
                    state.detailSuccessTimer = null;
                }
                if (typeof window.adminShowRefreshButtonSuccessState === 'function') {
                    state.detailSuccessTimer = setTimeout(() => {
                        state.detailSuccessTimer = null;
                        state.detailSuccessActive = false;
                        detailRefreshCooldown = false;
                    }, detailSuccessDuration + 200);
                    window.adminShowRefreshButtonSuccessState(el.detailRefreshBtn, {
                        duration: detailSuccessDuration,
                        onComplete: () => {
                            if (state.detailSuccessTimer) {
                                clearTimeout(state.detailSuccessTimer);
                                state.detailSuccessTimer = null;
                            }
                            state.detailSuccessActive = false;
                            detailRefreshCooldown = false;
                        },
                    });
                } else {
                    el.detailRefreshBtn.classList.add('is-success');
                    el.detailRefreshBtn.disabled = true;
                    state.detailSuccessTimer = setTimeout(() => {
                        el.detailRefreshBtn.classList.remove('is-success');
                        el.detailRefreshBtn.disabled = false;
                        state.detailSuccessTimer = null;
                        state.detailSuccessActive = false;
                        detailRefreshCooldown = false;
                    }, detailSuccessDuration);
                }
            }
        } catch (err) {
            console.error('Failed to load detail data:', err);
            if (state.detailSuccessTimer) {
                clearTimeout(state.detailSuccessTimer);
                state.detailSuccessTimer = null;
            }
            state.detailSuccessActive = false;
            detailRefreshCooldown = false;
            if (el.detailRefreshBtn && typeof window.adminResetRefreshButtonState === 'function') {
                window.adminResetRefreshButtonState(el.detailRefreshBtn);
            }
        } finally {
            state.detailLoading = false;
            const detailSuccessInProgress = state.detailSuccessActive || Boolean(state.detailSuccessTimer);
            if (el.detailRefreshBtn && !detailSuccessInProgress) {
                if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
                    window.adminSetRefreshButtonLoadingState(el.detailRefreshBtn, false);
                } else {
                    el.detailRefreshBtn.classList.remove('is-loading');
                    el.detailRefreshBtn.disabled = false;
                }
            }
        }
    }

    async function loadOverviewData() {
        try {
            const basePath = state.detailMode === 'groups' 
                ? `/api/v1/llmstats/admin/group-statistics/${state.selectedEntityId}/overview`
                : `/api/v1/llmstats/admin/user-statistics/overview/${state.selectedEntityId}`;
            
            const response = await fetchWithAuth(`${basePath}?days=${state.detailPeriod}`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load overview');
            
            state.detailOverview = await response.json();
            updateKPICards();
        } catch (err) {
            console.error('Failed to load detail overview:', err);
        }
    }

    async function loadTimelineData() {
        try {
            const granularity = el.timelineGranularity?.value || 'daily';
            const basePath = state.detailMode === 'groups'
                ? `/api/v1/llmstats/admin/group-statistics/${state.selectedEntityId}/timeline`
                : `/api/v1/llmstats/admin/user-statistics/${state.selectedEntityId}/timeline`;
            
            const response = await fetchWithAuth(`${basePath}?days=${state.detailPeriod}&granularity=${granularity}`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load timeline');
            
            state.detailTimeline = await response.json();
            updateTimelineChart();
        } catch (err) {
            console.error('Failed to load timeline:', err);
        }
    }

    async function loadModelsData() {
        try {
            const basePath = state.detailMode === 'groups'
                ? `/api/v1/llmstats/admin/group-statistics/${state.selectedEntityId}/by-model`
                : `/api/v1/llmstats/admin/user-statistics/${state.selectedEntityId}/by-model`;
            
            const response = await fetchWithAuth(`${basePath}?days=${state.detailPeriod}`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load models');
            
            state.detailModels = await response.json();
            updateModelsTable();
        } catch (err) {
            console.error('Failed to load models:', err);
        }
    }

    async function loadProvidersData() {
        try {
            const basePath = state.detailMode === 'groups'
                ? `/api/v1/llmstats/admin/group-statistics/${state.selectedEntityId}/by-model` // Use model data for groups
                : `/api/v1/llmstats/admin/user-statistics/${state.selectedEntityId}/by-provider`;
            
            const response = await fetchWithAuth(`${basePath}?days=${state.detailPeriod}`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load providers');
            
            const data = await response.json();
            
            // For groups, aggregate by provider from models data
            if (state.detailMode === 'groups') {
                const providerMap = {};
                (data.models || []).forEach(m => {
                    const key = m.provider_id || m.provider || 'unknown';
                    if (!providerMap[key]) {
                        providerMap[key] = {
                            provider: m.provider,
                            provider_name: m.provider_name,
                            total_requests: 0,
                            total_cost: 0,
                        };
                    }
                    providerMap[key].total_requests += m.total_requests || 0;
                    providerMap[key].total_cost += m.total_cost || 0;
                });
                state.detailProviders = { providers: Object.values(providerMap) };
            } else {
                state.detailProviders = data;
            }
            
            updateProviderChart();
        } catch (err) {
            console.error('Failed to load providers:', err);
        }
    }

    async function loadCategoriesData() {
        if (state.detailMode === 'groups') {
            // Categories not available for groups - skip
            state.detailCategories = { categories: [] };
            updateCategoryChart();
            return;
        }
        
        try {
            const response = await fetchWithAuth(`/api/v1/llmstats/admin/user-statistics/${state.selectedEntityId}/by-category?days=${state.detailPeriod}`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load categories');
            
            state.detailCategories = await response.json();
            updateCategoryChart();
        } catch (err) {
            console.error('Failed to load categories:', err);
        }
    }

    async function loadToolCallsData() {
        if (state.detailMode === 'groups') {
            state.detailToolCalls = { tools: [], overview: { total_calls: 0, success_count: 0, error_count: 0 } };
            updateToolChart();
            updateToolsTable();
            return;
        }
        
        try {
            const response = await fetchWithAuth(`/api/v1/llmstats/admin/user-statistics/${state.selectedEntityId}/tool-calls?days=${state.detailPeriod}`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load tool calls');
            
            state.detailToolCalls = await response.json();
            updateToolChart();
            updateToolsTable();
        } catch (err) {
            console.error('Failed to load tool calls:', err);
        }
    }

    async function loadErrorsData() {
        if (state.detailMode === 'groups') {
            state.detailErrors = { data: [], page: 1, totalPages: 1 };
            updateErrorsList();
            updateErrorsPagination();
            return;
        }
        
        try {
            const response = await fetchWithAuth(`/api/v1/llmstats/admin/user-statistics/${state.selectedEntityId}/errors?days=${state.detailPeriod}&page=${state.detailErrors.page}&per_page=10`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load errors');
            
            const data = await response.json();
            state.detailErrors.data = data.errors || [];
            state.detailErrors.totalPages = data.total_pages || 1;
            updateErrorsList();
            updateErrorsPagination();
        } catch (err) {
            console.error('Failed to load errors:', err);
        }
    }

    async function loadGroupUsersData() {
        try {
            const response = await fetchWithAuth(`/api/v1/llmstats/admin/group-statistics/${state.selectedEntityId}/by-user?days=${state.detailPeriod}`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to load group users');
            
            state.detailGroupUsers = await response.json();
            updateGroupUsersTable();
        } catch (err) {
            console.error('Failed to load group users:', err);
        }
    }

    // UI Updates
    function updateKPICards() {
        const ov = state.detailOverview;
        if (!ov) return;

        const llm = ov.llm_stats || ov;
        const tool = ov.tool_stats || {};

        if (el.kpiRequests) {
            el.kpiRequests.textContent = formatNumber(llm.total_requests || llm.llm_requests || 0);
        }
        if (el.kpiTokens) {
            el.kpiTokens.textContent = formatNumber(llm.total_tokens || 0);
        }
        if (el.kpiSuccessRate) {
            el.kpiSuccessRate.textContent = (llm.success_rate || 0) + '%';
        }
        if (el.kpiCost) {
            const cost = llm.estimated_total_cost || llm.estimated_cost || 0;
            el.kpiCost.textContent = '$' + formatCost(cost);
        }
        if (el.kpiToolCalls) {
            el.kpiToolCalls.textContent = formatNumber(tool.total_calls || ov.tool_calls || 0);
        }
    }

    function updateTimelineChart() {
        const canvas = document.getElementById('userStatsTimelineChart');
        if (!canvas || !state.detailTimeline?.timeline) return;

        const timeline = state.detailTimeline.timeline;
        const metric = el.timelineMetric?.value || 'requests';

        if (timeline.length === 0) {
            destroyChart('timeline');
            renderEmptyChart(canvas, t('stats_timeline_empty', 'No data available'));
            return;
        }

        showChartCanvas(canvas);

        const labels = timeline.map(t => t.period);
        let data, label, color;

        switch (metric) {
            case 'tokens':
                data = timeline.map(t => t.total_tokens || 0);
                label = t('stats_tokens', 'Tokens');
                color = '#8b5cf6';
                break;
            case 'cost':
                data = timeline.map(t => t.cost || 0);
                label = t('stats_cost_label_currency', 'Cost ($)');
                color = '#f59e0b';
                break;
            default:
                data = timeline.map(t => t.requests || 0);
                label = t('stats_requests', 'Requests');
                color = '#3b82f6';
        }

        destroyChart('timeline');

        const ctx = canvas.getContext('2d');
        charts.timeline = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label,
                    data,
                    borderColor: color,
                    backgroundColor: color + '20',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                    pointHoverRadius: 5,
                }],
            },
            options: getLineChartOptions(),
        });
    }

    function updateProviderChart() {
        const canvas = document.getElementById('userStatsProviderChart');
        if (!canvas) return;

        const providers = state.detailProviders?.providers || [];

        if (providers.length === 0) {
            destroyChart('provider');
            renderEmptyChart(canvas, t('stats_provider_chart_empty', 'No provider data'));
            return;
        }

        showChartCanvas(canvas);
        destroyChart('provider');

        const labels = providers.map(p => p.provider_name || p.provider || 'Unknown');
        const data = providers.map(p => p.total_requests || 0);
        const colors = CHART_COLORS.primary.slice(0, labels.length);

        const ctx = canvas.getContext('2d');
        charts.provider = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [{
                    data,
                    backgroundColor: colors,
                    borderWidth: 0,
                }],
            },
            options: getDoughnutChartOptions(),
        });
    }

    function updateCategoryChart() {
        const canvas = document.getElementById('userStatsCategoryChart');
        if (!canvas) return;

        const categories = state.detailCategories?.categories || [];

        if (categories.length === 0) {
            destroyChart('category');
            renderEmptyChart(canvas, t('stats_category_chart_empty', 'No category data'));
            return;
        }

        showChartCanvas(canvas);
        destroyChart('category');

        const labels = categories.map(c => formatCategoryLabel(c.category));
        const data = categories.map(c => c.total_requests || 0);
        const colors = CHART_COLORS.primary.slice(0, labels.length);

        const ctx = canvas.getContext('2d');
        charts.category = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [{
                    data,
                    backgroundColor: colors,
                    borderWidth: 0,
                }],
            },
            options: getDoughnutChartOptions(),
        });
    }

    function updateTokenChart() {
        const canvas = document.getElementById('userStatsTokenChart');
        if (!canvas || !state.detailOverview) return;

        const llm = state.detailOverview.llm_stats || state.detailOverview;
        const inputTokens = llm.total_input_tokens || 0;
        const outputTokens = llm.total_output_tokens || 0;

        if (inputTokens === 0 && outputTokens === 0) {
            destroyChart('token');
            renderEmptyChart(canvas, t('stats_token_chart_empty', 'No token data'));
            return;
        }

        showChartCanvas(canvas);
        destroyChart('token');

        const ctx = canvas.getContext('2d');
        charts.token = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: [t('stats_input_tokens', 'Input'), t('stats_output_tokens', 'Output')],
                datasets: [{
                    data: [inputTokens, outputTokens],
                    backgroundColor: [CHART_COLORS.input, CHART_COLORS.output],
                    borderWidth: 0,
                }],
            },
            options: getDoughnutChartOptions(),
        });
    }

    function updateSuccessErrorChart() {
        const canvas = document.getElementById('userStatsSuccessErrorChart');
        if (!canvas || !state.detailOverview) return;

        const llm = state.detailOverview.llm_stats || state.detailOverview;
        const success = llm.success_count || 0;
        const errors = llm.error_count || 0;

        if (success === 0 && errors === 0) {
            destroyChart('successError');
            renderEmptyChart(canvas, t('stats_no_data', 'No data'));
            return;
        }

        showChartCanvas(canvas);
        destroyChart('successError');

        const ctx = canvas.getContext('2d');
        charts.successError = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: [t('stats_success', 'Success'), t('stats_errors', 'Errors')],
                datasets: [{
                    data: [success, errors],
                    backgroundColor: [CHART_COLORS.success, CHART_COLORS.error],
                    borderWidth: 0,
                }],
            },
            options: getDoughnutChartOptions(),
        });
    }

    function updateToolChart() {
        const canvas = document.getElementById('userStatsToolChart');
        if (!canvas) return;

        const tools = state.detailToolCalls?.tools || [];

        if (tools.length === 0) {
            destroyChart('tool');
            renderEmptyChart(canvas, t('stats_tool_usage_empty', 'No tool call data available'));
            return;
        }

        showChartCanvas(canvas);
        destroyChart('tool');

        const topTools = tools.slice(0, 10);
        const labels = topTools.map(t => formatToolName(t.tool_name));
        const successData = topTools.map(t => t.success_count || 0);
        const errorData = topTools.map(t => t.error_count || 0);

        const ctx = canvas.getContext('2d');
        charts.tool = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: t('stats_success', 'Success'),
                        data: successData,
                        backgroundColor: CHART_COLORS.success,
                    },
                    {
                        label: t('stats_errors', 'Errors'),
                        data: errorData,
                        backgroundColor: CHART_COLORS.error,
                    },
                ],
            },
            options: getBarChartOptions(true),
        });
    }

    function updateModelsTable() {
        if (!el.modelsTableBody) return;

        const models = state.detailModels?.models || [];

        if (models.length === 0) {
            el.modelsTableBody.innerHTML = `<tr class="stats-table-empty"><td colspan="6">${t('stats_models_table_empty', 'No model usage data for this period.')}</td></tr>`;
            return;
        }

        el.modelsTableBody.innerHTML = models.map(m => `
            <tr>
                <td data-label="${escapeHtml(t('stats_col_model', 'Model'))}"><span class="stats-model-name">${escapeHtml(m.model_name || 'Unknown')}</span></td>
                <td data-label="${escapeHtml(t('stats_col_provider', 'Provider'))}">${escapeHtml(m.provider_name || m.provider || 'Unknown')}</td>
                <td data-label="${escapeHtml(t('stats_col_requests', 'Requests'))}">${formatNumber(m.total_requests)}</td>
                <td data-label="${escapeHtml(t('stats_col_success_rate', 'Success Rate'))}">${m.success_rate}%</td>
                <td data-label="${escapeHtml(t('stats_col_tokens', 'Tokens'))}" class="user-stats-tokens-cell">${formatNumber(m.total_tokens)}</td>
                <td data-label="${escapeHtml(t('stats_col_cost', 'Cost'))}" class="user-stats-cost-cell">$${formatCost(m.total_cost)}</td>
            </tr>
        `).join('');
    }

    function updateToolsTable() {
        if (!el.toolsTableBody) return;

        const tools = state.detailToolCalls?.tools || [];

        if (tools.length === 0) {
            el.toolsTableBody.innerHTML = `<tr class="stats-table-empty"><td colspan="5">${t('stats_tool_cost_empty', 'No tool call data for this period.')}</td></tr>`;
            return;
        }

        el.toolsTableBody.innerHTML = tools.map(tool => `
            <tr>
                <td data-label="${escapeHtml(t('stats_col_tool', 'Tool'))}"><span class="stats-model-name">${escapeHtml(formatToolName(tool.tool_name))}</span></td>
                <td data-label="${escapeHtml(t('stats_col_calls', 'Calls'))}">${formatNumber(tool.total_calls)}</td>
                <td data-label="${escapeHtml(t('stats_col_success', 'Success'))}" class="model-stats-success-cell">${formatNumber(tool.success_count)}</td>
                <td data-label="${escapeHtml(t('stats_col_errors', 'Errors'))}" class="model-stats-error-cell">${tool.error_count > 0 ? formatNumber(tool.error_count) : '-'}</td>
                <td data-label="${escapeHtml(t('stats_col_success_rate', 'Success Rate'))}">${tool.success_rate}%</td>
            </tr>
        `).join('');
    }

    function updateErrorsList() {
        if (!el.errorsList) return;

        const errors = state.detailErrors.data || [];

        if (errors.length === 0) {
            el.errorsList.innerHTML = `
                <div class="stats-empty">
                    <div class="stats-empty-icon">
                        ${Icons.check}
                    </div>
                    <p class="stats-empty-title">${t('stats_errors_empty_title', 'No errors')}</p>
                    <p class="stats-empty-text">${t('stats_errors_empty_text', 'No errors recorded for this period.')}</p>
                </div>
            `;
            return;
        }

        el.errorsList.innerHTML = errors.map(err => `
            <div class="model-stats-error-item">
                <div class="stats-entry-header">
                    <div class="model-stats-error-info">
                        <span class="stats-entry-model">${escapeHtml(err.model_name || 'Unknown')}</span>
                        <span class="stats-entry-provider">${escapeHtml(err.provider_name || err.provider || 'Unknown')}</span>
                        ${err.category ? `<span class="model-stats-error-category">${escapeHtml(formatCategoryLabel(err.category))}</span>` : ''}
                    </div>
                    <span class="stats-entry-time">${formatDateTime(err.created_at)}</span>
                </div>
                ${err.error_message ? `<div class="model-stats-error-message">${escapeHtml(err.error_message)}</div>` : ''}
            </div>
        `).join('');
    }

    function updateErrorsPagination() {
        if (el.errorsPrevBtn) {
            el.errorsPrevBtn.disabled = state.detailErrors.page <= 1;
        }
        if (el.errorsNextBtn) {
            el.errorsNextBtn.disabled = state.detailErrors.page >= state.detailErrors.totalPages;
        }
        if (el.errorsPageInfo) {
            el.errorsPageInfo.textContent = formatT('admin_page_of', `Page ${state.detailErrors.page} of ${state.detailErrors.totalPages || 1}`, {
                page: state.detailErrors.page,
                total: state.detailErrors.totalPages || 1,
            });
        }
    }

    function updateGroupUsersTable() {
        if (!el.groupUsersTableBody) return;

        const users = state.detailGroupUsers?.users || [];

        if (users.length === 0) {
            el.groupUsersTableBody.innerHTML = `<tr class="stats-table-empty"><td colspan="5">${t('stats_group_users_empty', 'No users in this group.')}</td></tr>`;
            return;
        }

        el.groupUsersTableBody.innerHTML = users.map(item => `
            <tr>
                <td data-label="${escapeHtml(t('user_stats_col_user', 'User'))}">
                    <div class="user-stats-user-info user-stats-user-info--compact">
                        <div class="user-stats-user-avatar user-stats-user-avatar--xs">${getInitials(item.user.email, item.user.name)}</div>
                        <div class="user-stats-user-details">
                            <div class="user-stats-user-email">${escapeHtml(item.user.email)}</div>
                        </div>
                    </div>
                </td>
                <td data-label="${escapeHtml(t('user_stats_col_llm_requests', 'LLM Requests'))}">${formatNumber(item.llm_requests)}</td>
                <td data-label="${escapeHtml(t('user_stats_col_tool_calls', 'Tool Calls'))}">${formatNumber(item.tool_calls)}</td>
                <td data-label="${escapeHtml(t('user_stats_col_tokens', 'Tokens'))}" class="user-stats-tokens-cell">${formatNumber(item.total_tokens)}</td>
                <td data-label="${escapeHtml(t('user_stats_col_cost', 'Est. Cost'))}" class="user-stats-cost-cell">$${formatCost(item.estimated_cost)}</td>
            </tr>
        `).join('');
    }

    // Also update token and success/error charts when overview loads
    function updateAllCharts() {
        updateTimelineChart();
        updateTokenChart();
        updateProviderChart();
        updateCategoryChart();
        updateSuccessErrorChart();
        updateToolChart();
    }

    // Extend loadOverviewData to also update charts after KPI data loads
    const _baseLoadOverviewData = loadOverviewData;
    loadOverviewData = async function() {
        await _baseLoadOverviewData();
        updateTokenChart();
        updateSuccessErrorChart();
    };

    // Chart helpers
    function destroyChart(name) {
        if (charts[name]) {
            charts[name].destroy();
            charts[name] = null;
        }
    }

    function renderEmptyChart(canvas, message) {
        const container = canvas.parentElement;
        canvas.style.display = 'none';
        
        let emptyEl = container.querySelector('.chart-empty-state');
        if (!emptyEl) {
            emptyEl = document.createElement('div');
            emptyEl.className = 'chart-empty-state';
            container.appendChild(emptyEl);
        }
        emptyEl.innerHTML = `<p>${message}</p>`;
        emptyEl.style.display = 'flex';
    }

    function showChartCanvas(canvas) {
        canvas.style.display = '';
        const container = canvas.parentElement;
        const emptyEl = container.querySelector('.chart-empty-state');
        if (emptyEl) emptyEl.style.display = 'none';
    }

    function getLineChartOptions() {
        const isDark = document.documentElement.getAttribute('data-mode') === 'dark';
        const textColor = isDark ? '#e2e8f0' : '#475569';
        const gridColor = isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.06)';

        return {
            responsive: true,
            maintainAspectRatio: false,
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
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: textColor, font: { size: 11 } },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: gridColor },
                    ticks: { color: textColor, font: { size: 11 } },
                },
            },
        };
    }

    function getDoughnutChartOptions() {
        const isDark = document.documentElement.getAttribute('data-mode') === 'dark';

        return {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '65%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: isDark ? '#e2e8f0' : '#475569',
                        padding: 16,
                        usePointStyle: true,
                        font: { size: 11 },
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
                },
            },
        };
    }

    function getBarChartOptions(stacked = false) {
        const isDark = document.documentElement.getAttribute('data-mode') === 'dark';
        const textColor = isDark ? '#e2e8f0' : '#475569';
        const gridColor = isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.06)';

        return {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        color: textColor,
                        usePointStyle: true,
                        font: { size: 11 },
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
                },
            },
            scales: {
                x: {
                    stacked,
                    grid: { color: gridColor },
                    ticks: { color: textColor, font: { size: 11 } },
                },
                y: {
                    stacked,
                    grid: { display: false },
                    ticks: { color: textColor, font: { size: 11 } },
                },
            },
        };
    }

    // Settings handlers
    function handleEnableToggle() {
        if (!el.enableToggle) return;

        if (el.enableToggle.checked) {
            if (!state.settings?.regulatory_confirmed) {
                showRegulatoryModal();
                el.enableToggle.checked = false;
            } else {
                updateSettings({ enabled: true });
            }
        } else {
            showDisableModal();
            el.enableToggle.checked = true;
        }
    }

    function handleTrackAllToggle() {
        if (!el.trackAllToggle) return;
        updateSettings({ track_all_users: el.trackAllToggle.checked });
    }

    async function updateSettings(updates) {
        try {
            const response = await fetchWithAuth('/api/v1/llmstats/admin/user-statistics/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(updates),
            });

            if (!response.ok) throw new Error('Failed to update settings');

            const data = await response.json();
            state.settings = { ...state.settings, ...data.settings };
            updateUI();

            if (updates.enabled) {
                loadOverview();
            }

            notifySuccess?.(t('user_stats_settings_updated', 'Settings updated successfully'));
        } catch (err) {
            console.error('Failed to update settings:', err);
            notifyError?.(t('user_stats_settings_update_failed', 'Failed to update settings'));
            updateUI();
        }
    }

    function showStatsModal(modal) {
        if (!modal) return;
        modal.removeAttribute('hidden');
        modal.setAttribute('aria-hidden', 'false');
    }

    function hideStatsModal(modal) {
        if (!modal) return;
        modal.setAttribute('hidden', '');
        modal.setAttribute('aria-hidden', 'true');
    }

    function showRegulatoryModal() {
        if (!el.regulatoryModal) return;
        if (el.regulatoryCheckbox) el.regulatoryCheckbox.checked = false;
        if (el.regulatoryConfirmBtn) el.regulatoryConfirmBtn.disabled = true;
        showStatsModal(el.regulatoryModal);
    }

    function hideRegulatoryModal() {
        hideStatsModal(el.regulatoryModal);
    }

    async function confirmEnableTracking() {
        hideRegulatoryModal();
        await updateSettings({ enabled: true, regulatory_confirmed: true });
    }

    function showDisableModal() {
        showStatsModal(el.disableModal);
    }

    function hideDisableModal() {
        hideStatsModal(el.disableModal);
    }

    async function confirmDisableTracking() {
        hideDisableModal();
        await updateSettings({ enabled: false });
    }

    function showAddUserModal() {
        if (!el.addUserModal) return;
        if (el.addUserSearch) el.addUserSearch.value = '';
        selectedUserId = null;
        if (el.addUserResults) {
            el.addUserResults.innerHTML = `
                <div class="user-stats-search-empty">
                    ${t('user_stats_search_empty_prompt', 'Type to search for users by email or name.')}
                </div>
            `;
        }
        if (el.addUserConfirmBtn) el.addUserConfirmBtn.disabled = true;
        showStatsModal(el.addUserModal);
        if (el.addUserSearch) el.addUserSearch.focus();
    }

    function hideAddUserModal() {
        hideStatsModal(el.addUserModal);
        selectedUserId = null;
    }

    let selectedUserId = null;

    async function filterUsers() {
        if (!el.addUserResults) return;

        const searchTerm = (el.addUserSearch?.value || '').trim();
        
        if (!searchTerm) {
            el.addUserResults.innerHTML = `
                <div class="user-stats-search-empty">
                    ${t('user_stats_search_empty_prompt', 'Type to search for users by email or name.')}
                </div>
            `;
            if (el.addUserConfirmBtn) el.addUserConfirmBtn.disabled = true;
            return;
        }

        el.addUserResults.innerHTML = `
            <div class="user-stats-search-empty">
                ${t('admin_searching', 'Searching...')}
            </div>
        `;

        try {
            const response = await fetchWithAuth(`/api/v1/admin/users?search=${encodeURIComponent(searchTerm)}&limit=20`, {
                credentials: 'include',
            });
            if (!response.ok) throw new Error('Failed to search users');

            const users = await response.json();
            const trackedIds = new Set(state.settings?.tracked_user_ids || []);

            if (users.length === 0) {
                el.addUserResults.innerHTML = `
                    <div class="user-stats-search-empty">
                        ${t('user_stats_search_no_results', 'No users found matching your search.')}
                    </div>
                `;
                return;
            }

            el.addUserResults.innerHTML = users.map(user => {
                const isTracked = trackedIds.has(user.id);
                const displayName = [user.first_name, user.last_name].filter(Boolean).join(' ');
                return `
                    <div class="user-stats-search-item ${isTracked ? 'is-tracked' : ''} ${selectedUserId === user.id ? 'is-selected' : ''}" 
                         data-user-id="${user.id}" ${isTracked ? `title="${t('user_stats_already_tracked', 'Already being tracked')}"` : ''}>
                        <div class="user-stats-user-avatar user-stats-user-avatar--sm">${getInitials(user.email, displayName)}</div>
                        <div class="user-stats-user-details">
                            <div class="user-stats-user-email">${escapeHtml(user.email)}</div>
                            ${displayName ? `<div class="user-stats-user-name">${escapeHtml(displayName)}</div>` : ''}
                        </div>
                        ${isTracked ? `<span class="user-stats-tracked-badge">${t('user_stats_tracked_badge', 'Tracked')}</span>` : ''}
                    </div>
                `;
            }).join('');

            el.addUserResults.querySelectorAll('.user-stats-search-item:not(.is-tracked)').forEach(item => {
                item.addEventListener('click', () => {
                    el.addUserResults.querySelectorAll('.user-stats-search-item').forEach(i => i.classList.remove('is-selected'));
                    item.classList.add('is-selected');
                    selectedUserId = item.dataset.userId;
                    if (el.addUserConfirmBtn) el.addUserConfirmBtn.disabled = false;
                });
            });

            if (el.addUserConfirmBtn) el.addUserConfirmBtn.disabled = !selectedUserId;
        } catch (err) {
            console.error('Failed to search users:', err);
            el.addUserResults.innerHTML = `
                <div class="user-stats-search-empty">
                    ${t('user_stats_search_failed', 'Failed to search users. Please try again.')}
                </div>
            `;
        }
    }

    async function confirmAddUser() {
        if (!selectedUserId) return;

        try {
            const response = await fetchWithAuth('/api/v1/llmstats/admin/user-statistics/add-user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ user_id: selectedUserId }),
            });

            if (!response.ok) throw new Error(t('user_stats_add_failed', 'Failed to add user'));

            hideAddUserModal();
            notifySuccess?.(t('user_stats_add_success', 'User added to tracking'));
            await loadSettings();
            await loadOverview();
            populateEntitySelect();
        } catch (err) {
            console.error('Failed to add user:', err);
            notifyError?.(t('user_stats_add_failed', 'Failed to add user'));
        }
    }

    async function removeUser(userId) {
        if (!userId) return;

        try {
            const response = await fetchWithAuth('/api/v1/llmstats/admin/user-statistics/remove-user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ user_id: userId }),
            });

            if (!response.ok) throw new Error(t('user_stats_remove_failed', 'Failed to remove user'));

            notifySuccess?.(t('user_stats_remove_success', 'User removed from tracking'));
            await loadSettings();
            await loadOverview();
            populateEntitySelect();
        } catch (err) {
            console.error('Failed to remove user:', err);
            notifyError?.(t('user_stats_remove_failed', 'Failed to remove user'));
        }
    }

    // Utilities
    function getInitials(email, name) {
        if (name) {
            const parts = name.trim().split(/\s+/);
            if (parts.length >= 2) {
                return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
            }
            return name.substring(0, 2).toUpperCase();
        }
        if (email) {
            return email.substring(0, 2).toUpperCase();
        }
        return '??';
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function formatNumber(num) {
        if (num === null || num === undefined) return '0';
        return num.toLocaleString();
    }

    function formatCost(cost) {
        if (cost === null || cost === undefined) return '0.00';
        if (cost >= 1) return cost.toFixed(2);
        if (cost >= 0.01) return cost.toFixed(3);
        return cost.toFixed(4);
    }

    function formatDateTime(dateStr) {
        if (!dateStr) return '';
        const date = new Date(dateStr);
        return date.toLocaleString();
    }

    function formatToolName(name) {
        if (!name) return 'Unknown';
        return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    function formatCategoryLabel(category) {
        if (!category) return 'Unknown';
        return category.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // Notification helpers
    const notifySuccess = typeof window.showNotification === 'function' 
        ? (msg) => window.showNotification(msg, 'success')
        : () => {};
    const notifyError = typeof window.showNotification === 'function'
        ? (msg) => window.showNotification(msg, 'error')
        : console.error;

    // Public API
    window.initUserStatisticsPage = function() {
        init();
        loadAllData();
    };

    window.teardownUserStatisticsPage = function() {
        // Cleanup charts
        Object.keys(charts).forEach(key => {
            if (charts[key]) {
                charts[key].destroy();
                charts[key] = null;
            }
        });
    };
})();
