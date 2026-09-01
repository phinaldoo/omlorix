/**
 * Model Feedback Admin Dashboard
 * Handles feedback analytics, charts, tables, and feedback list management
 */

(function () {
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

    let isPageActive = false;
    let autoRefreshInterval = null;
    let refreshCooldown = false;

    const state = {
        loading: false,
        days: 30,
        overview: null,
        byModel: null,
        timeline: null,
        feedbackList: null,
        listPage: 1,
        listPerPage: 20,
        listModelFilter: '',
        listReactionFilter: '',
        listCommentFilter: '',
    };

    const charts = {
        timeline: null,
        model: null,
        distribution: null,
        topModels: null,
    };

    const el = {};

    function init() {
        el.periodSelect = document.getElementById('feedbackStatsPeriodSelect');
        el.refreshBtn = document.getElementById('feedbackStatsRefreshBtn');
        el.deleteBtn = document.getElementById('feedbackStatsDeleteBtn');
        el.deleteOverlay = document.getElementById('deleteFeedbackOverlay');
        el.deleteCancelBtn = document.getElementById('deleteFeedbackCancelBtn');
        el.deleteConfirmBtn = document.getElementById('deleteFeedbackConfirmBtn');
        el.deletePeriodSelect = document.getElementById('deleteFeedbackPeriodSelect');

        el.kpiTotal = document.getElementById('kpiFeedbackTotal');
        el.kpiThumbsUp = document.getElementById('kpiFeedbackThumbsUp');
        el.kpiThumbsDown = document.getElementById('kpiFeedbackThumbsDown');
        el.kpiApproval = document.getElementById('kpiFeedbackApproval');
        el.kpiComments = document.getElementById('kpiFeedbackComments');
        el.kpiUsers = document.getElementById('kpiFeedbackUsers');

        el.approvalRateLarge = document.getElementById('feedbackApprovalRateLarge');
        el.approvalBarPositive = document.getElementById('feedbackApprovalBarPositive');
        el.approvalBarNegative = document.getElementById('feedbackApprovalBarNegative');
        el.positiveCount = document.getElementById('feedbackPositiveCount');
        el.negativeCount = document.getElementById('feedbackNegativeCount');

        el.modelsTableBody = document.getElementById('feedbackModelsTableBody');
        el.listContainer = document.getElementById('feedbackListContainer');

        el.listModelFilter = document.getElementById('feedbackListModelFilter');
        el.listReactionFilter = document.getElementById('feedbackListReactionFilter');
        el.listCommentFilter = document.getElementById('feedbackListCommentFilter');

        el.paginationInfo = document.getElementById('feedbackListPaginationInfo');
        el.prevBtn = document.getElementById('feedbackListPrevBtn');
        el.nextBtn = document.getElementById('feedbackListNextBtn');

        el.exportBtn = document.getElementById('modelFeedbackExportBtn');

        if (el.periodSelect) {
            el.periodSelect.addEventListener('change', handlePeriodChange);
        }

        if (el.refreshBtn) {
            el.refreshBtn.addEventListener('click', () => {
                if (!refreshCooldown && !state.loading) {
                    loadAllData({ isManualRefresh: true });
                }
            });
        }

        if (el.deleteBtn) {
            el.deleteBtn.addEventListener('click', showDeleteOverlay);
        }

        if (el.deleteCancelBtn) {
            el.deleteCancelBtn.addEventListener('click', hideDeleteOverlay);
        }

        if (el.deleteConfirmBtn) {
            el.deleteConfirmBtn.addEventListener('click', deleteFeedbackForPeriod);
        }

        if (el.deleteOverlay) {
            el.deleteOverlay.addEventListener('click', (event) => {
                if (event.target === el.deleteOverlay) {
                    hideDeleteOverlay();
                }
            });
        }

        if (el.listModelFilter) {
            el.listModelFilter.addEventListener('change', handleListFilterChange);
        }

        if (el.listReactionFilter) {
            el.listReactionFilter.addEventListener('change', handleListFilterChange);
        }

        if (el.listCommentFilter) {
            el.listCommentFilter.addEventListener('change', handleListFilterChange);
        }

        if (el.prevBtn) {
            el.prevBtn.addEventListener('click', () => {
                if (state.listPage > 1) {
                    state.listPage--;
                    loadFeedbackList();
                }
            });
        }

        if (el.nextBtn) {
            el.nextBtn.addEventListener('click', () => {
                if (state.feedbackList && state.listPage < state.feedbackList.total_pages) {
                    state.listPage++;
                    loadFeedbackList();
                }
            });
        }

        if (el.exportBtn) {
            el.exportBtn.addEventListener('click', exportFeedback);
        }
    }

    async function exportFeedback() {
        if (!el.exportBtn) return;

        const btnSpan = el.exportBtn.querySelector('span');
        const originalText = btnSpan?.textContent || t('feedback_export_btn', 'Export Feedback');
        const originalDisabled = el.exportBtn.disabled;

        try {
            if (btnSpan) btnSpan.textContent = t('admin_exporting_ellipsis', 'Exporting...');
            el.exportBtn.disabled = true;
            el.exportBtn.classList.add('loading');

            const response = await window.authedFetch('/api/v1/feedback/admin/export');
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || t('feedback_export_failed', 'Failed to export feedback'));
            }

            const data = await response.json();
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const now = new Date();
            const dateStr = now.toISOString().split('T')[0];
            a.href = url;
            a.download = `model-feedback-export-${dateStr}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            const totalCount = data.data?.total_count || 0;
            notifySuccess?.(
                formatT('feedback_export_success', `Exported ${totalCount.toLocaleString()} feedback entries`, {
                    count: totalCount.toLocaleString(),
                })
            );
        } catch (err) {
            console.error('Failed to export feedback:', err);
            notifyError?.(err.message || t('feedback_export_failed', 'Failed to export feedback'));
        } finally {
            if (btnSpan) btnSpan.textContent = originalText;
            el.exportBtn.disabled = originalDisabled;
            el.exportBtn.classList.remove('loading');
        }
    }

    function handlePeriodChange() {
        state.days = parseInt(el.periodSelect.value, 10) || 30;
        state.listPage = 1;
        loadAllData();
    }

    function handleListFilterChange() {
        state.listModelFilter = el.listModelFilter?.value || '';
        state.listReactionFilter = el.listReactionFilter?.value || '';
        state.listCommentFilter = el.listCommentFilter?.value || '';
        state.listPage = 1;
        loadFeedbackList();
    }

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
            // Model statistics also provide the model-filter options, so load
            // them before the list query can apply the selected model.
            await loadByModelData();
            await Promise.all([
                loadOverviewData(),
                loadTimelineData(),
                loadFeedbackList(),
            ]);

            if (isManualRefresh && el.refreshBtn) {
                refreshCooldown = true;
                if (typeof window.adminShowRefreshButtonSuccessState === 'function') {
                    const successDuration = 3000;
                    let refreshCooldownSafetyTimer = setTimeout(() => {
                        refreshCooldownSafetyTimer = null;
                        refreshCooldown = false;
                    }, successDuration + 200);
                    window.adminShowRefreshButtonSuccessState(el.refreshBtn, {
                        duration: successDuration,
                        onComplete: () => {
                            if (refreshCooldownSafetyTimer) {
                                clearTimeout(refreshCooldownSafetyTimer);
                                refreshCooldownSafetyTimer = null;
                            }
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
            console.error('Failed to load feedback data:', err);
            notifyError?.(t('feedback_loading_failed', 'Failed to load feedback data'));
            refreshCooldown = false;
            if (el.refreshBtn) {
                if (typeof window.adminResetRefreshButtonState === 'function') {
                    window.adminResetRefreshButtonState(el.refreshBtn);
                } else {
                    el.refreshBtn.disabled = false;
                    el.refreshBtn.classList.remove('is-success');
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

    async function loadOverviewData() {
        try {
            const res = await window.authedFetch(`/api/v1/feedback/admin/overview?days=${state.days}`);
            if (!res.ok) throw new Error('Failed to fetch overview');
            state.overview = await res.json();
            updateKPICards();
            updateApprovalCard();
            updateDistributionChart();
        } catch (err) {
            console.error('Error loading overview:', err);
        }
    }

    async function loadByModelData() {
        try {
            const res = await window.authedFetch(`/api/v1/feedback/admin/by-model?days=${state.days}`);
            if (!res.ok) throw new Error('Failed to fetch by-model');
            state.byModel = await res.json();
            updateModelsTable();
            updateModelChart();
            updateTopModelsChart();
            updateModelFilterDropdown();
        } catch (err) {
            console.error('Error loading by-model:', err);
        }
    }

    async function loadTimelineData() {
        try {
            const res = await window.authedFetch(`/api/v1/feedback/admin/timeline?days=${state.days}`);
            if (!res.ok) throw new Error('Failed to fetch timeline');
            state.timeline = await res.json();
            updateTimelineChart();
        } catch (err) {
            console.error('Error loading timeline:', err);
        }
    }

    async function loadFeedbackList() {
        try {
            let url = `/api/v1/feedback/admin/list?days=${state.days}&page=${state.listPage}&per_page=${state.listPerPage}`;
            if (state.listModelFilter) url += `&model_id=${encodeURIComponent(state.listModelFilter)}`;
            if (state.listReactionFilter) url += `&reaction=${encodeURIComponent(state.listReactionFilter)}`;
            if (state.listCommentFilter !== '') url += `&has_comment=${state.listCommentFilter}`;

            const res = await window.authedFetch(url);
            if (!res.ok) throw new Error('Failed to fetch list');
            state.feedbackList = await res.json();
            updateFeedbackList();
            updatePagination();
        } catch (err) {
            console.error('Error loading feedback list:', err);
        }
    }

    function formatNumber(n) {
        if (n == null) return '-';
        return n.toLocaleString();
    }

    function formatDate(dateStr) {
        if (!dateStr) return '-';
        const d = new Date(dateStr);
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    function formatDateTime(dateStr) {
        if (!dateStr) return '-';
        const d = new Date(dateStr);
        return d.toLocaleString(undefined, {
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

    function formatProviderLabel(provider) {
        if (!provider) return t('common_unknown', 'Unknown');
        if (typeof window.formatProviderLabel === 'function') {
            return window.formatProviderLabel(provider);
        }
        const rawKey = String(provider).trim();
        const key = rawKey.toLowerCase();
        if (window.PROVIDER_LABEL_MAP?.[key]) {
            return window.PROVIDER_LABEL_MAP[key];
        }
        if (/[A-Z]/.test(rawKey) && !/[_\-]/.test(rawKey)) {
            return rawKey;
        }
        return key
            .split(/[_\-]/)
            .filter(Boolean)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ');
    }

    function updateKPICards() {
        const ov = state.overview;
        if (!ov) return;

        if (el.kpiTotal) el.kpiTotal.textContent = formatNumber(ov.total_feedback);
        if (el.kpiThumbsUp) el.kpiThumbsUp.textContent = formatNumber(ov.thumbs_up);
        if (el.kpiThumbsDown) el.kpiThumbsDown.textContent = formatNumber(ov.thumbs_down);
        if (el.kpiApproval) el.kpiApproval.textContent = ov.approval_rate + '%';
        if (el.kpiComments) el.kpiComments.textContent = formatNumber(ov.with_comments);
        if (el.kpiUsers) el.kpiUsers.textContent = formatNumber(ov.unique_users);
    }

    function updateApprovalCard() {
        const ov = state.overview;
        if (!ov) return;

        const total = ov.total_feedback || 0;
        const thumbsUp = ov.thumbs_up || 0;
        const thumbsDown = ov.thumbs_down || 0;

        if (el.approvalRateLarge) {
            el.approvalRateLarge.textContent = ov.approval_rate + '%';
            el.approvalRateLarge.style.color = ov.approval_rate >= 70 ? '#10b981' : (ov.approval_rate >= 40 ? '#f59e0b' : '#ef4444');
        }

        if (el.positiveCount) el.positiveCount.textContent = formatNumber(thumbsUp);
        if (el.negativeCount) el.negativeCount.textContent = formatNumber(thumbsDown);

        const positivePercent = total > 0 ? (thumbsUp / total * 100) : 0;
        const negativePercent = total > 0 ? (thumbsDown / total * 100) : 0;

        if (el.approvalBarPositive) el.approvalBarPositive.style.width = positivePercent + '%';
        if (el.approvalBarNegative) el.approvalBarNegative.style.width = negativePercent + '%';
    }

    function updateModelsTable() {
        if (!el.modelsTableBody || !state.byModel?.models) return;

        const models = state.byModel.models;

        if (models.length === 0) {
            el.modelsTableBody.innerHTML = `
                <tr class="feedback-stats-table-empty stats-table-empty">
                    <td colspan="7">${escapeHtml(t('feedback_by_model_empty', 'No feedback data available for this period.'))}</td>
                </tr>
            `;
            return;
        }

        el.modelsTableBody.innerHTML = models.map(m => {
            const approvalClass = m.approval_rate >= 70 ? 'high' : (m.approval_rate >= 40 ? 'medium' : 'low');
            return `
                <tr>
                    <td data-label="${escapeHtml(t('stats_col_model', 'Model'))}" class="feedback-stats-model-name stats-model-name">${escapeHtml(m.model_name)}</td>
                    <td data-label="${escapeHtml(t('stats_col_provider', 'Provider'))}"><span class="feedback-stats-provider-badge stats-provider-badge">${escapeHtml(formatProviderLabel(m.provider))}</span></td>
                    <td data-label="${escapeHtml(t('feedback_col_total', 'Total'))}">${formatNumber(m.total)}</td>
                    <td data-label="${escapeHtml(t('feedback_positive', 'Positive'))}" class="feedback-stats-positive-cell">${formatNumber(m.thumbs_up)}</td>
                    <td data-label="${escapeHtml(t('feedback_negative', 'Negative'))}" class="feedback-stats-negative-cell">${formatNumber(m.thumbs_down)}</td>
                    <td data-label="${escapeHtml(t('feedback_col_comments', 'Comments'))}">${formatNumber(m.with_comments)}</td>
                    <td data-label="${escapeHtml(t('feedback_approval_rate', 'Approval'))}" class="feedback-stats-approval-cell ${approvalClass}">${m.approval_rate}%</td>
                </tr>
            `;
        }).join('');
    }

    function updateModelFilterDropdown() {
        if (!el.listModelFilter || !state.byModel?.models) return;

        const currentValue = el.listModelFilter.value;
        const models = [...state.byModel.models].sort((left, right) =>
            left.model_name.localeCompare(right.model_name)
        );
        el.listModelFilter.innerHTML = `<option value="">${t('feedback_filter_all_models', 'All Models')}</option>`;

        models.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m.model_id;
            opt.textContent = m.model_name;
            el.listModelFilter.appendChild(opt);
        });

        if (currentValue && models.some(model => model.model_id === currentValue)) {
            el.listModelFilter.value = currentValue;
        } else {
            state.listModelFilter = '';
        }
    }

    function updateFeedbackList() {
        if (!el.listContainer || !state.feedbackList) return;

        const feedbackItems = state.feedbackList.feedback || [];

        if (feedbackItems.length === 0) {
            el.listContainer.innerHTML = `
                <div class="feedback-stats-empty stats-empty">
                    <div class="feedback-stats-empty-icon stats-empty-icon">
                        ${Icons.chatFilesChooseChats}
                    </div>
                    <p class="feedback-stats-empty-title stats-empty-title">${t('feedback_list_empty_title', 'No feedback found')}</p>
                    <p class="feedback-stats-empty-text stats-empty-text">${t('feedback_list_empty_text', 'Try adjusting your filters or time period.')}</p>
                </div>
            `;
            return;
        }

        el.listContainer.innerHTML = feedbackItems.map(fb => {
            const isPositive = fb.reaction === 'thumbs_up';
            const reactionIcon = isPositive ? Icons.thumbUp : Icons.thumbDown;
            const reactionClass = isPositive ? 'positive' : 'negative';
            const userName = fb.user_email || t('feedback_anonymous', 'Anonymous');

            return `
                <div class="feedback-stats-feedback-item" data-id="${escapeHtml(fb.id)}">
                    <div class="feedback-stats-feedback-header stats-entry-header">
                        <div class="feedback-stats-feedback-info">
                            <span class="feedback-stats-feedback-reaction ${reactionClass}">${reactionIcon}</span>
                            <span class="feedback-stats-feedback-model stats-entry-model">${escapeHtml(fb.model_name)}</span>
                            <span class="feedback-stats-feedback-provider stats-entry-provider">${escapeHtml(formatProviderLabel(fb.provider))}</span>
                            <span class="feedback-stats-feedback-user">${escapeHtml(userName)}</span>
                        </div>
                        <div class="feedback-stats-feedback-actions">
                            <span class="feedback-stats-feedback-time stats-entry-time">${formatDateTime(fb.created_at)}</span>
                            <button type="button" class="feedback-stats-delete-btn" title="${t('feedback_delete_item_title', 'Delete feedback')}" onclick="window.deleteFeedbackItem('${escapeHtml(fb.id)}')">
                                ${Icons?.trash || ''}
                            </button>
                        </div>
                    </div>
                    ${fb.comment ? `<div class="feedback-stats-feedback-comment">${escapeHtml(fb.comment)}</div>` : ''}
                </div>
            `;
        }).join('');
    }

    function updatePagination() {
        if (!state.feedbackList) return;

        const { page, total_pages, total } = state.feedbackList;

        if (el.paginationInfo) {
            el.paginationInfo.textContent = formatT('feedback_pagination_info', `Page ${page} of ${total_pages} (${formatNumber(total)} total)`, {
                page,
                total_pages,
                total: formatNumber(total),
            });
        }

        if (el.prevBtn) {
            el.prevBtn.disabled = page <= 1;
        }

        if (el.nextBtn) {
            el.nextBtn.disabled = page >= total_pages;
        }
    }

    window.deleteFeedbackItem = async function (feedbackId) {
        if (!await window.showDeleteConfirm({
            message: t('feedback_delete_confirm', 'Are you sure you want to delete this feedback?'),
            confirmLabel: t('btn_delete', 'Delete'),
        })) return;

        try {
            const res = await window.authedFetch(`/api/v1/feedback/admin/${feedbackId}`, {
                method: 'DELETE',
            });

            if (!res.ok) throw new Error(t('feedback_delete_failed', 'Failed to delete feedback'));

            notifySuccess?.(t('feedback_delete_success', 'Feedback deleted'));
            loadAllData();
        } catch (err) {
            console.error('Error deleting feedback:', err);
            notifyError?.(t('feedback_delete_failed', 'Failed to delete feedback'));
        }
    };

    function getChartColors() {
        const isDark = document.documentElement.getAttribute('data-mode') === 'dark';
        return {
            textColor: isDark ? '#e2e8f0' : '#475569',
            gridColor: isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.06)',
            tooltipBg: isDark ? '#1e293b' : '#fff',
            tooltipTitle: isDark ? '#f8fafc' : '#0f172a',
            tooltipBody: isDark ? '#cbd5e1' : '#475569',
            tooltipBorder: isDark ? 'rgba(148, 163, 184, 0.2)' : 'rgba(15, 23, 42, 0.1)',
        };
    }

    function updateTimelineChart() {
        const canvas = document.getElementById('feedbackTimelineChart');
        if (!canvas || !state.timeline?.timeline) return;

        const timeline = state.timeline.timeline;

        if (timeline.length === 0) {
            if (charts.timeline) {
                charts.timeline.destroy();
                charts.timeline = null;
            }
            renderEmptyChart(canvas, t('feedback_chart_empty_timeline', 'No timeline data available'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.timeline) {
            charts.timeline.destroy();
        }

        const colors = getChartColors();
        const labels = timeline.map(t => formatDate(t.date));
        const positiveData = timeline.map(t => t.thumbs_up);
        const negativeData = timeline.map(t => t.thumbs_down);

        const ctx = canvas.getContext('2d');
        charts.timeline = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: t('feedback_positive', 'Positive'),
                        data: positiveData,
                        backgroundColor: 'rgba(16, 185, 129, 0.85)',
                        borderRadius: 4,
                        borderSkipped: false,
                    },
                    {
                        label: t('feedback_negative', 'Negative'),
                        data: negativeData,
                        backgroundColor: 'rgba(239, 68, 68, 0.85)',
                        borderRadius: 4,
                        borderSkipped: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: colors.tooltipBg,
                        titleColor: colors.tooltipTitle,
                        bodyColor: colors.tooltipBody,
                        borderColor: colors.tooltipBorder,
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                    },
                },
                scales: {
                    x: {
                        stacked: true,
                        grid: { display: false },
                        ticks: { color: colors.textColor, font: { size: 11 } },
                    },
                    y: {
                        stacked: true,
                        beginAtZero: true,
                        grid: { color: colors.gridColor },
                        ticks: { color: colors.textColor, font: { size: 11 } },
                    },
                },
            },
        });
    }

    function updateModelChart() {
        const canvas = document.getElementById('feedbackModelChart');
        if (!canvas || !state.byModel?.models) return;

        const models = state.byModel.models.slice(0, 10);

        if (models.length === 0) {
            if (charts.model) {
                charts.model.destroy();
                charts.model = null;
            }
            renderEmptyChart(canvas, t('feedback_chart_empty_model', 'No model data available'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.model) {
            charts.model.destroy();
        }

        const colors = getChartColors();
        const labels = models.map(m => m.model_name);
        const positiveData = models.map(m => m.thumbs_up);
        const negativeData = models.map(m => m.thumbs_down);

        const ctx = canvas.getContext('2d');
        charts.model = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: t('feedback_positive', 'Positive'),
                        data: positiveData,
                        backgroundColor: 'rgba(16, 185, 129, 0.85)',
                        borderRadius: { topLeft: 6, bottomLeft: 6 },
                        borderSkipped: false,
                    },
                    {
                        label: t('feedback_negative', 'Negative'),
                        data: negativeData,
                        backgroundColor: 'rgba(239, 68, 68, 0.85)',
                        borderRadius: { topRight: 6, bottomRight: 6 },
                        borderSkipped: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: colors.tooltipBg,
                        titleColor: colors.tooltipTitle,
                        bodyColor: colors.tooltipBody,
                        borderColor: colors.tooltipBorder,
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            label: (ctx) => {
                                const model = models[ctx.dataIndex];
                                if (ctx.datasetIndex === 0) {
                                    return formatT('feedback_chart_tooltip_positive', `Positive: ${model.thumbs_up} (${model.approval_rate}%)`, { count: model.thumbs_up, rate: model.approval_rate });
                                }
                                return formatT('feedback_chart_tooltip_negative', `Negative: ${model.thumbs_down}`, { count: model.thumbs_down });
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        stacked: true,
                        beginAtZero: true,
                        grid: { color: colors.gridColor },
                        ticks: { color: colors.textColor, font: { size: 11 } },
                    },
                    y: {
                        stacked: true,
                        grid: { display: false },
                        ticks: { color: colors.textColor, font: { size: 11 } },
                    },
                },
            },
        });
    }

    function updateDistributionChart() {
        const canvas = document.getElementById('feedbackDistributionChart');
        if (!canvas || !state.overview) return;

        const ov = state.overview;

        if (ov.total_feedback === 0) {
            if (charts.distribution) {
                charts.distribution.destroy();
                charts.distribution = null;
            }
            renderEmptyChart(canvas, t('feedback_chart_empty_distribution', 'No feedback data'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.distribution) {
            charts.distribution.destroy();
        }

        const colors = getChartColors();
        const ctx = canvas.getContext('2d');

        charts.distribution = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: [t('feedback_positive', 'Positive'), t('feedback_negative', 'Negative')],
                datasets: [{
                    data: [ov.thumbs_up, ov.thumbs_down],
                    backgroundColor: ['rgba(16, 185, 129, 0.85)', 'rgba(239, 68, 68, 0.85)'],
                    borderWidth: 0,
                    hoverOffset: 8,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '65%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            color: colors.textColor,
                            padding: 16,
                            font: { size: 12, weight: '500' },
                            usePointStyle: true,
                            pointStyle: 'rectRounded',
                        },
                    },
                    tooltip: {
                        backgroundColor: colors.tooltipBg,
                        titleColor: colors.tooltipTitle,
                        bodyColor: colors.tooltipBody,
                        borderColor: colors.tooltipBorder,
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            label: (ctx) => {
                                const total = ov.total_feedback || 1;
                                const percent = ((ctx.raw / total) * 100).toFixed(1);
                                return `${ctx.label}: ${formatNumber(ctx.raw)} (${percent}%)`;
                            },
                        },
                    },
                },
            },
        });
    }

    function updateTopModelsChart() {
        const canvas = document.getElementById('feedbackTopModelsChart');
        if (!canvas || !state.byModel?.models) return;

        const models = [...state.byModel.models]
            .filter(m => m.total >= 5)
            .sort((a, b) => b.approval_rate - a.approval_rate)
            .slice(0, 5);

        if (models.length === 0) {
            if (charts.topModels) {
                charts.topModels.destroy();
                charts.topModels = null;
            }
            renderEmptyChart(canvas, t('feedback_chart_empty_top_models', 'Not enough data'));
            return;
        }

        showChartCanvas(canvas);

        if (charts.topModels) {
            charts.topModels.destroy();
        }

        const colors = getChartColors();
        const labels = models.map(m => m.model_name);
        const approvalData = models.map(m => m.approval_rate);

        const barColors = approvalData.map(rate => {
            if (rate >= 70) return 'rgba(16, 185, 129, 0.85)';
            if (rate >= 40) return 'rgba(245, 158, 11, 0.85)';
            return 'rgba(239, 68, 68, 0.85)';
        });

        const ctx = canvas.getContext('2d');
        charts.topModels = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: t('feedback_approval_rate', 'Approval Rate'),
                    data: approvalData,
                    backgroundColor: barColors,
                    borderRadius: 6,
                    borderSkipped: false,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: colors.tooltipBg,
                        titleColor: colors.tooltipTitle,
                        bodyColor: colors.tooltipBody,
                        borderColor: colors.tooltipBorder,
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 12,
                        callbacks: {
                            label: (ctx) => {
                                const model = models[ctx.dataIndex];
                                return formatT('feedback_chart_top_models_tooltip', `${ctx.raw}% (${model.thumbs_up}/${model.total} positive)`, { rate: ctx.raw, positive: model.thumbs_up, total: model.total });
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        min: 0,
                        max: 100,
                        grid: { color: colors.gridColor },
                        ticks: {
                            color: colors.textColor,
                            font: { size: 11 },
                            callback: (v) => v + '%',
                        },
                    },
                    y: {
                        grid: { display: false },
                        ticks: { color: colors.textColor, font: { size: 11 } },
                    },
                },
            },
        });
    }

    function renderEmptyChart(canvas, message) {
        const parent = canvas.parentElement;
        if (!parent) return;

        canvas.style.display = 'none';

        let emptyEl = parent.querySelector('.stats-chart-empty, .feedback-stats-chart-empty');
        if (!emptyEl) {
            emptyEl = document.createElement('p');
            emptyEl.className = 'feedback-stats-chart-empty stats-chart-empty';
            parent.appendChild(emptyEl);
        }
        emptyEl.textContent = message;
    }

    function showChartCanvas(canvas) {
        canvas.style.display = '';
        const emptyEl = canvas.parentElement?.querySelector('.stats-chart-empty, .feedback-stats-chart-empty');
        if (emptyEl) {
            emptyEl.remove();
        }
    }

    function startAutoRefresh() {
        stopAutoRefresh();
        autoRefreshInterval = setInterval(() => {
            if (!refreshCooldown && isPageActive) {
                loadAllData();
            }
        }, 60000);
    }

    function stopAutoRefresh() {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
    }

    function destroyCharts() {
        Object.keys(charts).forEach(key => {
            if (charts[key]) {
                charts[key].destroy();
                charts[key] = null;
            }
        });
    }

    function showDeleteOverlay() {
        if (!el.deleteOverlay) return;
        el.deleteOverlay.hidden = false;
        document.body.classList.add('modal-open');
    }

    function hideDeleteOverlay() {
        if (!el.deleteOverlay) return;
        el.deleteOverlay.hidden = true;
        document.body.classList.remove('modal-open');
    }

    async function deleteFeedbackForPeriod() {
        if (!el.deleteConfirmBtn) return;
        const days = parseInt(el.deletePeriodSelect?.value || '30', 10);

        const originalHtml = el.deleteConfirmBtn.innerHTML;
        el.deleteConfirmBtn.disabled = true;
        el.deleteConfirmBtn.innerHTML = `
            ${Icons.refresh}
            <span>${t('admin_deleting', 'Deleting...')}</span>
        `;

        try {
            const response = await window.authedFetch(`/api/v1/feedback/admin?days=${days}`, {
                method: 'DELETE',
            });

            if (!response.ok) {
                throw new Error(formatT('feedback_delete_failed_status', `Failed to delete feedback (${response.status})`, { status: response.status }));
            }

            const payload = await response.json();
            hideDeleteOverlay();
            notifySuccess?.(
                formatT('feedback_delete_period_success', `Deleted ${payload.deleted_count?.toLocaleString() || 0} feedback entries`, {
                    count: payload.deleted_count?.toLocaleString() || 0,
                })
            );
            await loadAllData();
        } catch (error) {
            console.error('Delete feedback failed', error);
            notifyError?.(t('feedback_delete_failed', 'Failed to delete feedback'));
        } finally {
            if (el.deleteConfirmBtn) {
                el.deleteConfirmBtn.disabled = false;
                el.deleteConfirmBtn.innerHTML = originalHtml;
            }
        }
    }

    async function initModelFeedbackPage() {
        isPageActive = true;
        init();
        await loadAllData();
        startAutoRefresh();
    }

    function cleanupModelFeedbackPage() {
        isPageActive = false;
        stopAutoRefresh();
        destroyCharts();
    }

    window.initModelFeedbackPage = initModelFeedbackPage;
    window.cleanupModelFeedbackPage = cleanupModelFeedbackPage;
})();
