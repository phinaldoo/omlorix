(function () {
    'use strict';

    const PAGE_SIZE = 50;
    const SEARCH_DEBOUNCE_MS = 250;
    const API_PATH = '/api/v1/admin/file-storage/statistics';

    const state = {
        initialized: false,
        loading: false,
        search: '',
        sortField: 'storage_bytes',
        sortDirection: 'desc',
        offset: 0,
        total: 0,
        hasMore: false,
        searchTimer: null,
        pendingLoad: false,
    };

    const el = {
        totalBytes: null,
        totalFiles: null,
        usersWithFiles: null,
        search: null,
        tableBody: null,
        empty: null,
        pageInfo: null,
        pagination: null,
        prev: null,
        next: null,
        sortButtons: [],
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
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    };

    const escapeHtml = (value) => {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    };

    const getCurrentLocale = () => {
        const documentLang = document.documentElement?.getAttribute('lang')?.trim();
        if (documentLang) {
            return documentLang;
        }
        let storedLang = null;
        try {
            storedLang = localStorage.getItem('lang');
        } catch (error) {
            console.warn('Failed to read saved language:', error);
        }
        return storedLang || navigator.language || 'en';
    };

    const formatInteger = (value) => {
        const number = Number(value);
        return Number.isFinite(number) ? new Intl.NumberFormat(getCurrentLocale()).format(number) : '0';
    };

    const formatBytes = (value) => {
        const bytes = Number(value);
        if (!Number.isFinite(bytes) || bytes <= 0) {
            return '0 B';
        }
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex += 1;
        }
        const digits = size >= 10 || unitIndex === 0 ? 0 : 1;
        return `${size.toFixed(digits)} ${units[unitIndex]}`;
    };

    const formatDateTime = (value) => {
        if (!value) {
            return t('file_storage_never', 'Never');
        }
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return t('file_storage_never', 'Never');
        }
        return new Intl.DateTimeFormat(getCurrentLocale(), {
            dateStyle: 'medium',
            timeStyle: 'short',
        }).format(date);
    };

    const setText = (node, value) => {
        if (node) node.textContent = value;
    };

    const getUserName = (item) => {
        const fullName = [item?.first_name, item?.last_name].filter(Boolean).join(' ').trim();
        return fullName || item?.email || item?.user_id || t('file_storage_unknown_user', 'Unknown user');
    };

    const renderLimitText = (used, limit, formatter) => {
        if (limit === null || typeof limit === 'undefined') {
            return formatT('file_storage_used_of_unlimited', '{used} of unlimited', {
                used: formatter(used),
            });
        }
        return formatT('file_storage_used_of_limit', '{used} of {limit}', {
            used: formatter(used),
            limit: formatter(limit),
        });
    };

    const renderProgress = (percent) => {
        const value = Number(percent);
        const width = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
        return `
            <span class="file-storage-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${width.toFixed(0)}">
                <span class="file-storage-progress-fill" style="width: ${width}%"></span>
            </span>
        `;
    };

    const renderUserRow = (item) => {
        const name = getUserName(item);
        const email = item?.email || item?.user_id || '';
        const status = item?.uploads_allowed === false
            ? `<span class="file-storage-status file-storage-status-disabled">${escapeHtml(t('file_storage_uploads_disabled', 'Uploads disabled'))}</span>`
            : '';
        const storageLimitText = renderLimitText(item?.storage_bytes || 0, item?.storage_bytes_limit, formatBytes);
        const countLimitText = renderLimitText(item?.file_count || 0, item?.file_count_limit, formatInteger);
        return `
            <tr>
                <td>
                    <div class="file-storage-user-cell">
                        <span class="file-storage-user-name">${escapeHtml(name)}</span>
                        <span class="file-storage-user-meta">${escapeHtml(email)}</span>
                        ${status}
                    </div>
                </td>
                <td>
                    <strong>${escapeHtml(formatBytes(item?.storage_bytes || 0))}</strong>
                    ${renderProgress(item?.storage_percent)}
                </td>
                <td>
                    <strong>${escapeHtml(formatInteger(item?.file_count || 0))}</strong>
                    ${renderProgress(item?.file_count_percent)}
                </td>
                <td>
                    <div class="file-storage-limits">
                        <span>${escapeHtml(storageLimitText)}</span>
                        <span>${escapeHtml(countLimitText)}</span>
                    </div>
                </td>
                <td>${escapeHtml(formatDateTime(item?.latest_file_at))}</td>
            </tr>
        `;
    };

    const renderSortButtons = () => {
        el.sortButtons.forEach((button) => {
            const active = button.dataset.sortField === state.sortField;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
            const baseText = t(button.dataset.i18n || '', button.textContent || '');
            button.textContent = active
                ? `${baseText} ${state.sortDirection === 'asc' ? '↑' : '↓'}`
                : baseText;
        });
    };

    const renderPagination = () => {
        const shownStart = state.total > 0 ? state.offset + 1 : 0;
        const shownEnd = Math.min(state.offset + PAGE_SIZE, state.total);
        setText(el.pageInfo, formatT('file_storage_showing', 'Showing {start}-{end} of {total}', {
            start: shownStart,
            end: shownEnd,
            total: state.total,
        }));
        if (el.pagination) {
            el.pagination.hidden = state.total <= PAGE_SIZE;
        }
        if (el.prev) {
            el.prev.disabled = state.loading || state.offset <= 0;
        }
        if (el.next) {
            el.next.disabled = state.loading || !state.hasMore;
        }
    };

    const render = (payload = {}) => {
        const summary = payload.summary || {};
        setText(el.totalBytes, formatBytes(summary.total_storage_bytes || 0));
        setText(el.totalFiles, formatInteger(summary.total_files || 0));
        setText(el.usersWithFiles, formatInteger(summary.users_with_files || 0));

        const items = Array.isArray(payload.items) ? payload.items : [];
        if (el.tableBody) {
            el.tableBody.innerHTML = items.map(renderUserRow).join('');
        }
        if (el.empty) {
            el.empty.hidden = items.length > 0;
        }
        state.total = Number(payload.total || 0);
        state.hasMore = Boolean(payload.has_more);
        renderSortButtons();
        renderPagination();
    };

    const loadData = async () => {
        if (state.loading) {
            state.pendingLoad = true;
            return;
        }
        state.loading = true;
        renderPagination();
        try {
            const params = new URLSearchParams({
                limit: String(PAGE_SIZE),
                offset: String(state.offset),
                sort_field: state.sortField,
                sort_direction: state.sortDirection,
            });
            if (state.search) {
                params.set('search', state.search);
            }
            const response = await window.authedFetch(`${API_PATH}?${params.toString()}`, { method: 'GET' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            render(await response.json());
        } catch (error) {
            console.error('Failed to load file storage statistics', error);
            notifyError?.(t('file_storage_load_failed', 'Failed to load file storage statistics.'));
        } finally {
            state.loading = false;
            renderPagination();
            if (state.pendingLoad) {
                state.pendingLoad = false;
                loadData();
            }
        }
    };

    const scheduleSearch = () => {
        if (state.searchTimer) {
            window.clearTimeout(state.searchTimer);
        }
        state.searchTimer = window.setTimeout(() => {
            state.search = String(el.search?.value || '').trim();
            state.offset = 0;
            loadData();
        }, SEARCH_DEBOUNCE_MS);
    };

    const bindEvents = () => {
        el.search?.addEventListener('input', scheduleSearch);
        el.prev?.addEventListener('click', () => {
            state.offset = Math.max(0, state.offset - PAGE_SIZE);
            loadData();
        });
        el.next?.addEventListener('click', () => {
            if (!state.hasMore) return;
            state.offset += PAGE_SIZE;
            loadData();
        });
        el.sortButtons.forEach((button) => {
            button.addEventListener('click', () => {
                const field = button.dataset.sortField || 'storage_bytes';
                if (state.sortField === field) {
                    state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
                } else {
                    state.sortField = field;
                    state.sortDirection = field === 'email' ? 'asc' : 'desc';
                }
                state.offset = 0;
                loadData();
            });
        });
    };

    window.initFileStoragePage = function initFileStoragePage() {
        if (!state.initialized) {
            el.totalBytes = document.getElementById('fileStorageTotalBytes');
            el.totalFiles = document.getElementById('fileStorageTotalFiles');
            el.usersWithFiles = document.getElementById('fileStorageUsersWithFiles');
            el.search = document.getElementById('fileStorageSearchInput');
            el.tableBody = document.getElementById('fileStorageTableBody');
            el.empty = document.getElementById('fileStorageEmpty');
            el.pageInfo = document.getElementById('fileStoragePageInfo');
            el.pagination = document.getElementById('fileStoragePagination');
            el.prev = document.getElementById('fileStoragePrevButton');
            el.next = document.getElementById('fileStorageNextButton');
            el.sortButtons = Array.from(document.querySelectorAll('.file-storage-sort'));
            bindEvents();
            state.initialized = true;
        }
        loadData();
    };
})();
