(function () {
    const state = {
        initialized: false,
        bound: false,
        loading: false,
        controller: null,
        exportController: null,
        items: [],
        nextCursor: null,
        snapshotAt: null,
        appliedFilters: null,
        retention: null,
        detailCache: new Map(),
        lastFocused: null,
    };

    const t = (key, fallback) => (
        typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback
    );

    const formatT = (key, fallback, vars = {}) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_match, name) => (
            vars[name] === undefined || vars[name] === null ? '' : String(vars[name])
        ));
    };

    const dom = {
        get page() { return document.getElementById('page-audit-logs'); },
        get form() { return document.getElementById('auditLogsFilterForm'); },
        get from() { return document.getElementById('auditLogsFrom'); },
        get to() { return document.getElementById('auditLogsTo'); },
        get category() { return document.getElementById('auditLogsCategory'); },
        get action() { return document.getElementById('auditLogsAction'); },
        get actor() { return document.getElementById('auditLogsActor'); },
        get reference() { return document.getElementById('auditLogsReference'); },
        get refresh() { return document.getElementById('auditLogsRefreshButton'); },
        get clear() { return document.getElementById('auditLogsClearButton'); },
        get apply() { return document.getElementById('auditLogsApplyButton'); },
        get export() { return document.getElementById('auditLogsExportButton'); },
        get retention() { return document.getElementById('auditLogsRetentionSummary'); },
        get status() { return document.getElementById('auditLogsStatus'); },
        get tableWrap() { return document.getElementById('auditLogsTableWrap'); },
        get rows() { return document.getElementById('auditLogsRows'); },
        get empty() { return document.getElementById('auditLogsEmpty'); },
        get pagination() { return document.getElementById('auditLogsPagination'); },
        get count() { return document.getElementById('auditLogsCount'); },
        get loadMore() { return document.getElementById('auditLogsLoadMoreButton'); },
        get exportOverlay() { return document.getElementById('auditLogsExportOverlay'); },
        get exportReason() { return document.getElementById('auditLogsExportReason'); },
        get exportError() { return document.getElementById('auditLogsExportError'); },
        get exportCancel() { return document.getElementById('auditLogsExportCancel'); },
        get exportConfirm() { return document.getElementById('auditLogsExportConfirm'); },
    };

    const localInputValue = (date) => {
        const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
        return local.toISOString().slice(0, 16);
    };

    const resetDateRange = () => {
        const to = new Date();
        const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
        dom.from.value = localInputValue(from);
        dom.to.value = localInputValue(to);
    };

    const resetFilters = () => {
        resetDateRange();
        dom.category.value = '';
        dom.action.value = '';
        dom.actor.value = '';
        dom.reference.value = '';
    };

    const readFilters = () => {
        const from = new Date(dom.from.value);
        const to = new Date(dom.to.value);
        if (!dom.from.value || !dom.to.value || Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) {
            throw new Error(t('audit_logs_time_required', 'Choose a valid start and end time.'));
        }
        if (from > to) {
            throw new Error(t('audit_logs_invalid_time_range', 'The start time must be before the end time.'));
        }
        return {
            from: from.toISOString(),
            to: to.toISOString(),
            category: dom.category.value.trim(),
            action: dom.action.value.trim(),
            actor_user_id: dom.actor.value.trim(),
            reference: dom.reference.value.trim(),
        };
    };

    const setStatus = (message, { error = false } = {}) => {
        dom.status.textContent = message || '';
        dom.status.dataset.error = error ? 'true' : 'false';
    };

    const apiErrorMessage = (payload, status) => {
        const detail = payload?.detail;
        const code = typeof detail === 'object' ? detail?.code : '';
        const messages = {
            audit_log_invalid_time_range: ['audit_logs_invalid_time_range', 'The start time must be before the end time.'],
            audit_log_time_range_too_large: ['audit_logs_time_range_too_large', 'The selected time range is too large.'],
            audit_log_invalid_cursor: ['audit_logs_invalid_cursor', 'This result snapshot is no longer valid. Refresh the audit logs.'],
            audit_log_invalid_snapshot: ['audit_logs_invalid_snapshot', 'This result snapshot is invalid. Refresh the audit logs.'],
            audit_log_not_found: ['audit_logs_not_found', 'The audit event is no longer available.'],
            audit_log_export_too_large: ['audit_logs_export_too_large', 'The export contains too many events. Narrow the filters and try again.'],
        };
        const translation = messages[code];
        if (translation) return t(translation[0], translation[1]);
        return formatT(
            'audit_logs_request_failed_status',
            'The audit-log request failed (status {status}).',
            { status },
        );
    };

    const fetchJson = async (url, options = {}) => {
        const fetcher = window.authedFetch || window.fetch.bind(window);
        const response = await fetcher(url, { cache: 'no-store', ...options });
        if (response.status === 401 || response.status === 403) {
            if (typeof window.redirectToLogin === 'function') window.redirectToLogin();
        }
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(apiErrorMessage(payload, response.status));
        }
        return response.json();
    };

    const formatTimestamp = (value) => {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return t('audit_logs_unknown_time', 'Unknown time');
        return new Intl.DateTimeFormat(undefined, {
            dateStyle: 'medium',
            timeStyle: 'medium',
        }).format(date);
    };

    const appendTextCell = (row, value, className = '') => {
        const cell = document.createElement('td');
        if (className) cell.className = className;
        cell.textContent = value || '—';
        row.appendChild(cell);
        return cell;
    };

    const appendCodeCell = (row, value) => {
        const cell = document.createElement('td');
        const code = document.createElement('code');
        code.textContent = value || '—';
        cell.appendChild(code);
        row.appendChild(cell);
    };

    const appendDetailEntry = (list, label, value, { code = false } = {}) => {
        const term = document.createElement('dt');
        term.textContent = label;
        const description = document.createElement('dd');
        if (code) {
            const codeElement = document.createElement('code');
            codeElement.textContent = value || '—';
            description.appendChild(codeElement);
        } else {
            description.textContent = value || '—';
        }
        list.append(term, description);
    };

    const renderDetail = (panel, detail) => {
        panel.replaceChildren();
        const list = document.createElement('dl');
        list.className = 'audit-log-detail-list';
        appendDetailEntry(list, t('audit_logs_event_id', 'Event ID'), detail.id, { code: true });
        appendDetailEntry(list, t('audit_logs_ip_fingerprint', 'IP fingerprint'), detail.ip_fingerprint, { code: true });
        appendDetailEntry(list, t('audit_logs_device_fingerprint', 'Device fingerprint'), detail.device_fingerprint, { code: true });
        panel.appendChild(list);
        if (detail.details && Object.keys(detail.details).length) {
            const pre = document.createElement('pre');
            pre.className = 'audit-log-detail-json';
            pre.textContent = JSON.stringify(detail.details, null, 2);
            panel.appendChild(pre);
        } else {
            const empty = document.createElement('p');
            empty.textContent = t('audit_logs_no_public_details', 'No additional sanitized details are available.');
            panel.appendChild(empty);
        }
    };

    const loadDetail = async (item, button, detailRow, panel) => {
        const cacheKey = `${item.id}:${item.timestamp}`;
        detailRow.hidden = false;
        button.setAttribute('aria-expanded', 'true');
        button.textContent = t('audit_logs_hide_details', 'Hide details');
        if (state.detailCache.has(cacheKey)) {
            renderDetail(panel, state.detailCache.get(cacheKey));
            return;
        }
        panel.textContent = t('audit_logs_loading_details', 'Loading sanitized details…');
        try {
            const params = new URLSearchParams({ occurred_at: item.timestamp });
            const detail = await fetchJson(`/api/v1/admin/audit-logs/${encodeURIComponent(item.id)}?${params}`);
            state.detailCache.set(cacheKey, detail);
            renderDetail(panel, detail);
        } catch (error) {
            panel.textContent = error.message || t('audit_logs_details_failed', 'Unable to load audit-event details.');
        }
    };

    const renderRows = () => {
        dom.rows.replaceChildren();
        state.items.forEach((item, index) => {
            const row = document.createElement('tr');
            appendTextCell(row, formatTimestamp(item.timestamp));
            appendCodeCell(row, item.actor_user_id);

            const categoryCell = document.createElement('td');
            const badge = document.createElement('span');
            badge.className = 'audit-log-category';
            badge.textContent = item.category || '—';
            categoryCell.appendChild(badge);
            row.appendChild(categoryCell);
            appendCodeCell(row, item.action);
            appendTextCell(row, item.reason, 'audit-log-reason');

            const actionCell = document.createElement('td');
            const detailButton = document.createElement('button');
            const detailId = `auditLogDetail-${index}`;
            detailButton.type = 'button';
            detailButton.className = 'om-button border cancel small';
            detailButton.textContent = t('audit_logs_view_details', 'View details');
            detailButton.setAttribute('aria-expanded', 'false');
            detailButton.setAttribute('aria-controls', detailId);
            actionCell.appendChild(detailButton);
            row.appendChild(actionCell);

            const detailRow = document.createElement('tr');
            detailRow.className = 'audit-log-details-row';
            detailRow.hidden = true;
            const detailCell = document.createElement('td');
            detailCell.colSpan = 6;
            const panel = document.createElement('div');
            panel.className = 'audit-log-detail-panel';
            panel.id = detailId;
            detailCell.appendChild(panel);
            detailRow.appendChild(detailCell);

            detailButton.addEventListener('click', () => {
                if (!detailRow.hidden) {
                    detailRow.hidden = true;
                    detailButton.setAttribute('aria-expanded', 'false');
                    detailButton.textContent = t('audit_logs_view_details', 'View details');
                    return;
                }
                loadDetail(item, detailButton, detailRow, panel);
            });
            dom.rows.append(row, detailRow);
        });

        const hasItems = state.items.length > 0;
        dom.tableWrap.hidden = !hasItems;
        dom.empty.hidden = hasItems || state.loading;
        dom.pagination.hidden = !hasItems;
        dom.loadMore.hidden = !state.nextCursor;
        dom.count.textContent = formatT(
            state.items.length === 1 ? 'audit_logs_loaded_single' : 'audit_logs_loaded_plural',
            '{count} events loaded',
            { count: state.items.length.toLocaleString() },
        );
    };

    const renderRetention = () => {
        if (!state.retention) {
            dom.retention.textContent = t('audit_logs_retention_loading', 'Loading retention policy…');
            return;
        }
        const general = state.retention.global_cleanup_enabled
            ? t('audit_logs_retention_global_enabled', 'General age or count cleanup is enabled.')
            : t('audit_logs_retention_global_disabled', 'General events have no age or count cleanup limit.');
        let deletedUsers = t('audit_logs_retention_deleted_retain', 'Events tied to deleted users are retained.');
        if (state.retention.post_user_deletion_mode === 'delete_instantly') {
            deletedUsers = t('audit_logs_retention_deleted_instant', 'Events tied to a deleted user are removed immediately.');
        } else if (state.retention.post_user_deletion_mode === 'delete_after_days') {
            deletedUsers = formatT(
                'audit_logs_retention_deleted_days',
                'Events tied to a deleted user are retained for {days} days.',
                { days: state.retention.post_user_deletion_days },
            );
        }
        dom.retention.textContent = `${general} ${deletedUsers}`;
    };

    const snapshotFilters = () => Object.freeze({ ...readFilters() });

    const buildListUrl = (filters, { append = false } = {}) => {
        const params = new URLSearchParams({ limit: '50', from: filters.from, to: filters.to });
        ['category', 'action', 'actor_user_id', 'reference'].forEach((key) => {
            if (filters[key]) params.set(key, filters[key]);
        });
        if (append && state.nextCursor && state.snapshotAt) {
            params.set('cursor', state.nextCursor);
            params.set('snapshot_at', state.snapshotAt);
        }
        return `/api/v1/admin/audit-logs?${params}`;
    };

    const loadLogs = async ({ append = false } = {}) => {
        if (state.loading) return;
        let filters;
        try {
            filters = append ? state.appliedFilters : snapshotFilters();
        } catch (error) {
            setStatus(error.message || t('audit_logs_load_failed', 'Unable to load audit logs.'), { error: true });
            return;
        }
        if (!filters || (append && (!state.nextCursor || !state.snapshotAt))) return;

        state.loading = true;
        state.controller?.abort();
        const controller = new AbortController();
        state.controller = controller;
        dom.apply.disabled = true;
        dom.refresh.disabled = true;
        dom.loadMore.disabled = true;
        setStatus(t('audit_logs_loading', 'Loading audit events…'));
        if (!append) {
            state.appliedFilters = filters;
            state.items = [];
            state.nextCursor = null;
            state.snapshotAt = null;
            state.detailCache.clear();
            renderRows();
        }
        try {
            const payload = await fetchJson(buildListUrl(filters, { append }), {
                signal: controller.signal,
            });
            state.items = append ? state.items.concat(payload.items || []) : (payload.items || []);
            state.nextCursor = payload.next_cursor || null;
            state.snapshotAt = payload.snapshot_at || null;
            state.retention = payload.retention || null;
            renderRetention();
            renderRows();
            setStatus(formatT(
                state.items.length === 1 ? 'audit_logs_loaded_single' : 'audit_logs_loaded_plural',
                '{count} events loaded',
                { count: state.items.length.toLocaleString() },
            ));
        } catch (error) {
            if (error?.name !== 'AbortError') {
                setStatus(error.message || t('audit_logs_load_failed', 'Unable to load audit logs.'), { error: true });
                renderRows();
            }
        } finally {
            if (state.controller === controller) {
                state.loading = false;
                state.controller = null;
                dom.apply.disabled = false;
                dom.refresh.disabled = false;
                dom.loadMore.disabled = false;
            }
        }
    };

    const hideExportError = () => {
        dom.exportError.hidden = true;
        dom.exportError.textContent = '';
        dom.exportReason.classList.remove('field-error');
        dom.exportReason.removeAttribute('aria-invalid');
    };

    const showExportError = (message, { invalidReason = false } = {}) => {
        dom.exportError.textContent = message;
        dom.exportError.hidden = false;
        if (invalidReason) {
            dom.exportReason.classList.add('field-error');
            dom.exportReason.setAttribute('aria-invalid', 'true');
            dom.exportReason.focus();
        }
    };

    const clearValidExportReasonError = () => {
        if (
            dom.exportReason.getAttribute('aria-invalid') === 'true'
            && dom.exportReason.value.trim().length >= 3
        ) {
            hideExportError();
        }
    };

    const openExportDialog = () => {
        hideExportError();
        state.lastFocused = document.activeElement;
        dom.exportOverlay.hidden = false;
        dom.exportOverlay.setAttribute('aria-hidden', 'false');
        window.setTimeout(() => {
            if (!dom.exportOverlay.hidden) dom.exportReason.focus();
        }, 50);
    };

    const closeExportDialog = () => {
        state.exportController?.abort();
        state.exportController = null;
        dom.exportConfirm.disabled = false;
        dom.exportOverlay.setAttribute('aria-hidden', 'true');
        dom.exportOverlay.hidden = true;
        dom.exportReason.value = '';
        hideExportError();
        state.lastFocused?.focus?.();
        state.lastFocused = null;
    };

    const exportLogs = async () => {
        hideExportError();
        let filters;
        try {
            filters = readFilters();
        } catch (error) {
            showExportError(error.message);
            return;
        }
        const rangeMs = new Date(filters.to).getTime() - new Date(filters.from).getTime();
        if (rangeMs > 31 * 24 * 60 * 60 * 1000) {
            showExportError(t('audit_logs_export_range_limit', 'Audit-log exports are limited to 31 days.'));
            return;
        }
        const reason = dom.exportReason.value.trim();
        if (reason.length < 3) {
            showExportError(
                t('audit_logs_export_reason_required', 'Enter an investigation reason of at least 3 characters.'),
                { invalidReason: true },
            );
            return;
        }
        const body = { from: filters.from, to: filters.to, reason };
        ['category', 'action', 'actor_user_id', 'reference'].forEach((key) => {
            if (filters[key]) body[key] = filters[key];
        });
        dom.exportConfirm.disabled = true;
        state.exportController?.abort();
        const controller = new AbortController();
        state.exportController = controller;
        try {
            const fetcher = window.authedFetch || window.fetch.bind(window);
            const response = await fetcher('/api/v1/admin/audit-logs/export', {
                method: 'POST',
                cache: 'no-store',
                signal: controller.signal,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (response.status === 401 || response.status === 403) {
                if (typeof window.redirectToLogin === 'function') window.redirectToLogin();
            }
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(apiErrorMessage(payload, response.status));
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `omlorix-audit-logs-${new Date().toISOString().slice(0, 10)}.json`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            closeExportDialog();
            if (typeof window.notifySuccess === 'function') {
                window.notifySuccess(t('audit_logs_export_success', 'Audit logs exported successfully.'));
            }
        } catch (error) {
            if (error?.name !== 'AbortError') {
                showExportError(error.message || t('audit_logs_export_failed', 'Unable to export audit logs.'));
            }
        } finally {
            if (state.exportController === controller) {
                state.exportController = null;
                dom.exportConfirm.disabled = false;
            }
        }
    };

    const trapExportDialogFocus = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeExportDialog();
            return;
        }
        if (event.key !== 'Tab') return;
        const focusable = Array.from(dom.exportOverlay.querySelectorAll('button, textarea'))
            .filter((element) => !element.disabled && !element.hidden);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    };

    const bindEvents = () => {
        if (state.bound) return;
        state.bound = true;
        dom.form.addEventListener('submit', (event) => {
            event.preventDefault();
            loadLogs();
        });
        dom.refresh.addEventListener('click', () => loadLogs());
        dom.clear.addEventListener('click', () => {
            resetFilters();
            loadLogs();
        });
        dom.loadMore.addEventListener('click', () => loadLogs({ append: true }));
        dom.export.addEventListener('click', openExportDialog);
        dom.exportCancel.addEventListener('click', closeExportDialog);
        dom.exportConfirm.addEventListener('click', exportLogs);
        dom.exportReason.addEventListener('input', clearValidExportReasonError);
        dom.exportOverlay.addEventListener('click', (event) => {
            if (event.target === dom.exportOverlay) closeExportDialog();
        });
        dom.exportOverlay.addEventListener('keydown', trapExportDialogFocus);
        document.addEventListener('i18n:updated', () => {
            renderRetention();
            renderRows();
        });
    };

    window.initAuditLogsPage = () => {
        if (!dom.page) return;
        bindEvents();
        if (!state.initialized) {
            resetFilters();
            state.initialized = true;
        }
        renderRetention();
        loadLogs();
    };

    window.teardownAuditLogsPage = () => {
        state.controller?.abort();
        state.controller = null;
        state.exportController?.abort();
        state.exportController = null;
        state.loading = false;
        if (!dom.exportOverlay.hidden) closeExportDialog();
    };
})();
