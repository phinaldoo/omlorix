function setButtonLabel(button, text) {
    if (!button) {
        return;
    }
    const span = button.querySelector('span');
    if (span) {
        span.textContent = text;
    } else {
        button.textContent = text;
    }
}

function setButtonLoadingState(button, isLoading, loadingLabel = 'Loading…') {
    if (!button) {
        return;
    }
    const labelTarget = button.querySelector('span');
    const getCurrentLabel = () => (labelTarget ? labelTarget.textContent : button.textContent);
    const setLabel = (text) => {
        if (labelTarget) {
            labelTarget.textContent = text;
        } else {
            button.textContent = text;
        }
    };

    if (isLoading) {
        if (!button.dataset.originalLabel) {
            button.dataset.originalLabel = getCurrentLabel()?.trim() || '';
        }
        button.disabled = true;
        button.classList.add('loading');
        button.setAttribute('aria-busy', 'true');
        setLabel(loadingLabel);
        void button.offsetWidth;
    } else {
        button.disabled = false;
        button.classList.remove('loading');
        button.removeAttribute('aria-busy');
        if (button.dataset.originalLabel !== undefined) {
            setLabel(button.dataset.originalLabel || '');
            delete button.dataset.originalLabel;
        }
        void button.offsetWidth;
    }
}

function createAdminExportJobsController(config = {}) {
    const dom = config.dom || {};
    const endpoints = config.endpoints || {};
    const keys = config.keys || {};
    const metadataFields = Array.isArray(config.metadataFields) ? config.metadataFields : [];
    const translate = typeof config.translate === 'function'
        ? config.translate
        : (key, fallback) => (typeof window.getTranslation === 'function' ? window.getTranslation(key, fallback) : (fallback ?? key));
    const format = typeof config.format === 'function'
        ? config.format
        : (key, fallback, values = {}) => {
            let output = translate(key, fallback);
            Object.entries(values).forEach(([name, value]) => {
                output = output.replace(new RegExp(`\\{${String(name).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\}`, 'g'), String(value));
            });
            return output;
        };

    let jobs = [];
    let refreshTimer = null;
    let lastFocusedElement = null;
    const downloadsInProgress = new Set();

    const t = (name, fallback) => translate(keys[name] || name, fallback);

    const escapeValue = (value) => {
        if (typeof window.escapeHtml === 'function') {
            return window.escapeHtml(value);
        }
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[char]));
    };

    const parseDate = (value) => {
        if (!value) {
            return null;
        }
        const normalized = typeof value === 'string' && !/[zZ]|[+\-]\d{2}:?\d{2}$/.test(value.trim())
            ? `${value.trim().includes('T') ? value.trim() : value.trim().replace(' ', 'T')}Z`
            : value;
        const date = new Date(normalized);
        return Number.isNaN(date.getTime()) ? null : date;
    };

    const formatDate = (value) => {
        const date = parseDate(value);
        if (!date) {
            return t('notAvailable', 'Not available');
        }
        try {
            const locale = typeof window.getCurrentLocale === 'function' ? window.getCurrentLocale() : undefined;
            return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
        } catch (_) {
            return date.toLocaleString();
        }
    };

    const formatSize = (value) => {
        const bytes = Number(value);
        if (!Number.isFinite(bytes) || bytes <= 0) {
            return t('notAvailable', 'Not available');
        }
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex += 1;
        }
        const precision = unitIndex === 0 || size >= 10 ? 0 : 1;
        return `${size.toFixed(precision)} ${units[unitIndex]}`;
    };

    const statusLabel = (status) => {
        switch (String(status || '').toLowerCase()) {
            case 'queued':
                return t('statusQueued', 'Queued');
            case 'running':
                return t('statusRunning', 'Running');
            case 'success':
                return t('statusSuccess', 'Ready to download');
            case 'failed':
                return t('statusFailed', 'Failed');
            case 'deleted':
                return t('statusDeleted', 'Deleted');
            case 'expired':
                return t('statusExpired', 'Expired');
            default:
                return status || t('notAvailable', 'Not available');
        }
    };

    const statusClass = (status) => {
        const normalized = String(status || '').toLowerCase();
        if (normalized === 'success') {
            return 'success';
        }
        if (['failed', 'deleted', 'expired'].includes(normalized)) {
            return 'error';
        }
        return 'warning';
    };

    const nestedValue = (source, path) => String(path || '').split('.').reduce((value, part) => value?.[part], source);

    const metadataHtml = (job) => {
        const manifest = job.manifest_json && typeof job.manifest_json === 'object' ? job.manifest_json : {};
        const baseFields = [
            { label: t('createdLabel', 'Created'), value: formatDate(job.created_at) },
            { label: t('finishedLabel', 'Finished'), value: formatDate(job.finished_at) },
            { label: t('expiresLabel', 'Expires'), value: formatDate(job.expires_at) },
            { label: t('sizeLabel', 'Size'), value: formatSize(job.size_bytes) },
        ];
        const extraFields = metadataFields.map((field) => {
            let value = nestedValue({ job, manifest }, field.path || `manifest.${field.key}`);
            if (typeof field.formatValue === 'function') {
                value = field.formatValue(value, { job, manifest, translate });
            }
            if (value === undefined || value === null || value === '') {
                value = t('notAvailable', 'Not available');
            }
            return {
                label: translate(field.labelKey, field.labelFallback),
                value,
            };
        });
        return [...baseFields, ...extraFields]
            .map((field) => `<p class="user-export-job-meta-item">${escapeValue(field.label)}: ${escapeValue(field.value)}</p>`)
            .join('');
    };

    const setStatus = (message = '', type = '') => {
        if (!dom.status) {
            return;
        }
        if (!message) {
            dom.status.hidden = true;
            dom.status.textContent = '';
            dom.status.className = 'provider-import-status';
            return;
        }
        dom.status.hidden = false;
        dom.status.textContent = message;
        dom.status.className = `provider-import-status ${type || ''}`.trim();
    };

    const render = () => {
        if (!dom.list) {
            return;
        }
        if (!jobs.length) {
            dom.list.innerHTML = `<p class="settings-row-desc">${escapeValue(t('empty', 'No export jobs yet.'))}</p>`;
            return;
        }

        dom.list.innerHTML = jobs.map((job) => {
            const status = String(job.status || '').toLowerCase();
            const canDownload = status === 'success' && job.download_ready;
            const canDelete = !['queued', 'running', 'deleted', 'expired'].includes(status);
            const downloadUrl = canDownload && typeof endpoints.download === 'function'
                ? endpoints.download(job.id)
                : '';
            const downloadAction = canDownload
                ? `<a class="om-button border cancel" href="${escapeValue(downloadUrl)}" download data-admin-export-job-action="download" data-job-id="${escapeValue(job.id)}">${escapeValue(t('downloadButton', 'Download'))}</a>`
                : `<button type="button" class="om-button border cancel" disabled>${escapeValue(t('downloadButton', 'Download'))}</button>`;
            return `
                <div class="provider-import-entry user-export-job-entry" data-export-job-id="${escapeValue(job.id)}">
                    <div class="user-export-job-content">
                        <div class="user-export-job-main">
                            <div class="user-export-job-title-row">
                                <p class="provider-import-entry-title">${escapeValue(job.filename || job.id)}</p>
                                <span class="pill ${escapeValue(statusClass(status))}">${escapeValue(statusLabel(status))}</span>
                            </div>
                            <div class="user-export-job-meta">
                                ${metadataHtml(job)}
                            </div>
                            ${job.error ? `<p class="user-export-job-error">${escapeValue(t('errorLabel', 'Error'))}: ${escapeValue(job.error)}</p>` : ''}
                        </div>
                        <div class="user-export-job-actions">
                            ${downloadAction}
                            <button type="button" class="om-button border danger" data-admin-export-job-action="delete" data-job-id="${escapeValue(job.id)}" ${canDelete ? '' : 'disabled'}>${escapeValue(t('deleteButton', 'Delete'))}</button>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    };

    const scheduleRefresh = () => {
        if (refreshTimer) {
            window.clearTimeout(refreshTimer);
            refreshTimer = null;
        }
        const hasActiveJob = jobs.some((job) => ['queued', 'running'].includes(String(job.status || '').toLowerCase()));
        if (hasActiveJob && dom.overlay && !dom.overlay.hidden) {
            refreshTimer = window.setTimeout(() => refresh({ silent: true }), config.refreshIntervalMs || 5000);
        }
    };

    const fetchJsonOrThrow = async (response, fallback) => {
        if (response.ok) {
            return response.json();
        }
        let message = fallback;
        try {
            const errorData = await response.json();
            if (errorData?.detail) {
                message = errorData.detail;
            }
        } catch (_) {}
        throw new Error(message);
    };

    const filenameFromDisposition = (contentDisposition) => {
        const value = String(contentDisposition || '').trim();
        if (!value) {
            return '';
        }
        const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
        if (utf8Match?.[1]) {
            try {
                return decodeURIComponent(utf8Match[1]);
            } catch (_) {
                return utf8Match[1];
            }
        }
        const quotedMatch = value.match(/filename="([^"]+)"/i);
        if (quotedMatch?.[1]) {
            return quotedMatch[1];
        }
        const simpleMatch = value.match(/filename=([^;]+)/i);
        return simpleMatch?.[1]?.trim() || '';
    };

    const downloadBlob = (blob, filename) => {
        // Keep the archive request inside authedFetch. A native navigation cannot
        // reliably reproduce auth headers, and the export API intentionally
        // exposes a single GET operation rather than a separate HEAD preflight.
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        link.hidden = true;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
    };

    const fallbackFilename = () => {
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        return `${config.filenamePrefix || 'admin-export'}-${timestamp}.${config.fileExtension || 'zip'}`;
    };

    async function refresh(options = {}) {
        if (!dom.list || !endpoints.list) {
            return;
        }
        try {
            if (!options.silent) {
                window.setButtonLoadingState?.(dom.refreshButton, true, t('refreshing', 'Refreshing…'));
            }
            const response = await window.authedFetch(endpoints.list);
            jobs = await fetchJsonOrThrow(response, t('loadFailed', 'Failed to load export jobs.'));
            render();
            scheduleRefresh();
            if (!options.silent) {
                setStatus(t('refreshed', 'Export jobs refreshed.'), 'success');
            }
        } catch (error) {
            console.error(config.logPrefix || 'Failed to refresh export jobs', error);
            setStatus(error?.message || t('loadFailed', 'Failed to load export jobs.'), 'error');
            window.notifyError?.(error?.message || t('loadFailed', 'Failed to load export jobs.'));
        } finally {
            if (!options.silent) {
                window.setButtonLoadingState?.(dom.refreshButton, false);
            }
        }
    }

    async function queue() {
        try {
            window.setButtonLoadingState?.(dom.createButton, true, t('queueing', 'Queueing…'));
            const request = typeof endpoints.buildCreateRequest === 'function'
                ? endpoints.buildCreateRequest({ dom, jobs })
                : {
                    url: typeof endpoints.buildCreateUrl === 'function'
                        ? endpoints.buildCreateUrl({ dom, jobs })
                        : endpoints.create,
                    init: { method: 'POST' },
                };
            const response = await window.authedFetch(request.url, request.init || { method: 'POST' });
            const job = await fetchJsonOrThrow(response, t('createFailed', 'Failed to create export job.'));
            setStatus(format(keys.queuedStatus || 'queuedStatus', 'Export job queued: {id}', { id: job.id }), 'success');
            window.notifySuccess?.(t('queuedSuccess', 'Export job queued.'));
            await refresh({ silent: true });
        } catch (error) {
            console.error(config.logPrefix || 'Failed to queue export job', error);
            window.notifyError?.(error?.message || t('createFailed', 'Failed to create export job.'));
        } finally {
            window.setButtonLoadingState?.(dom.createButton, false);
        }
    }

    async function download(jobId, nativeLink = null) {
        if (downloadsInProgress.has(jobId)) {
            return;
        }
        downloadsInProgress.add(jobId);
        nativeLink?.setAttribute('aria-disabled', 'true');
        try {
            const downloadUrl = endpoints.download(jobId);
            const response = await window.authedFetch(downloadUrl, { method: 'GET' });
            if (!response.ok) {
                await fetchJsonOrThrow(response, t('downloadFailed', 'Failed to download export job.'));
            }
            const blob = await response.blob();
            const filename = filenameFromDisposition(response.headers.get('content-disposition')) || fallbackFilename();
            downloadBlob(blob, filename);
            window.notifySuccess?.(t('downloadSuccess', 'Export downloaded.'));
        } catch (error) {
            console.error(config.logPrefix || 'Failed to download export job', error);
            window.notifyError?.(error?.message || t('downloadFailed', 'Failed to download export job.'));
        } finally {
            downloadsInProgress.delete(jobId);
            nativeLink?.removeAttribute('aria-disabled');
        }
    }

    async function deleteJob(jobId) {
        const confirmed = await window.showDeleteConfirm?.({
            title: t('deleteTitle', 'Delete export job?'),
            message: t('deleteDesc', 'This removes the generated file for this export job.'),
            confirmLabel: t('deleteButton', 'Delete'),
        });
        if (!confirmed) {
            return;
        }

        try {
            const response = await window.authedFetch(endpoints.delete(jobId), { method: 'DELETE' });
            await fetchJsonOrThrow(response, t('deleteFailed', 'Failed to delete export job.'));
            window.notifySuccess?.(t('deleteSuccess', 'Export job deleted.'));
            await refresh({ silent: true });
        } catch (error) {
            console.error(config.logPrefix || 'Failed to delete export job', error);
            window.notifyError?.(error?.message || t('deleteFailed', 'Failed to delete export job.'));
        }
    }

    const handleAction = (event) => {
        const actionElement = event.target.closest('[data-admin-export-job-action]');
        if (!actionElement) {
            return;
        }
        const jobId = actionElement.dataset.jobId;
        if (!jobId) {
            return;
        }
        if (actionElement.dataset.adminExportJobAction === 'download') {
            event.preventDefault();
            download(jobId, actionElement);
        } else if (actionElement.dataset.adminExportJobAction === 'delete') {
            deleteJob(jobId);
        }
    };

    const open = () => {
        lastFocusedElement = document.activeElement;
        if (dom.overlay) {
            dom.overlay.hidden = false;
            dom.overlay.classList.add('active');
        }
        refresh({ silent: true });
        dom.createButton?.focus();
    };

    const close = () => {
        if (dom.overlay) {
            dom.overlay.hidden = true;
            dom.overlay.classList.remove('active');
        }
        if (refreshTimer) {
            window.clearTimeout(refreshTimer);
            refreshTimer = null;
        }
        setStatus();
        if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') {
            lastFocusedElement.focus();
        }
        lastFocusedElement = null;
    };

    const bind = () => {
        if (dom.triggerButton && dom.triggerButton.dataset.exportJobsBound !== 'true') {
            dom.triggerButton.addEventListener('click', open);
            dom.triggerButton.dataset.exportJobsBound = 'true';
        }
        if (dom.createButton && dom.createButton.dataset.exportJobsBound !== 'true') {
            dom.createButton.addEventListener('click', queue);
            dom.createButton.dataset.exportJobsBound = 'true';
        }
        if (dom.refreshButton && dom.refreshButton.dataset.exportJobsBound !== 'true') {
            dom.refreshButton.addEventListener('click', () => refresh());
            dom.refreshButton.dataset.exportJobsBound = 'true';
        }
        if (dom.list && dom.list.dataset.exportJobsBound !== 'true') {
            dom.list.addEventListener('click', handleAction);
            dom.list.dataset.exportJobsBound = 'true';
        }
        if (dom.overlay && dom.overlay.dataset.exportJobsOverlayBound !== 'true') {
            dom.overlay.addEventListener('click', (event) => {
                if (event.target === dom.overlay) {
                    close();
                }
            });
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && !dom.overlay.hidden) {
                    close();
                }
            });
            dom.overlay.dataset.exportJobsOverlayBound = 'true';
        }
        [dom.closeButton, dom.cancelButton].forEach((button) => {
            if (button && button.dataset.exportJobsBound !== 'true') {
                button.addEventListener('click', close);
                button.dataset.exportJobsBound = 'true';
            }
        });
    };

    return {
        bind,
        open,
        close,
        refresh,
        queue,
        download,
        deleteJob,
    };
}

function getRefreshButtonIcons(button) {
    if (!button) {
        return { refreshIcon: null, checkIcon: null };
    }
    return {
        refreshIcon: button.querySelector('.refresh-icon'),
        checkIcon: button.querySelector('.check-icon'),
    };
}

function setRefreshButtonIconState(button, { showRefresh, showCheck }) {
    const { refreshIcon, checkIcon } = getRefreshButtonIcons(button);

    if (refreshIcon) {
        refreshIcon.hidden = !showRefresh;
        if (showRefresh) {
            refreshIcon.removeAttribute('hidden');
        } else {
            refreshIcon.setAttribute('hidden', '');
        }
    }

    if (checkIcon) {
        checkIcon.hidden = !showCheck;
        if (showCheck) {
            checkIcon.removeAttribute('hidden');
        } else {
            checkIcon.setAttribute('hidden', '');
        }
    }
}

function clearRefreshButtonSuccessTimer(button) {
    if (!button || !button._adminRefreshSuccessTimer) {
        return;
    }
    clearTimeout(button._adminRefreshSuccessTimer);
    button._adminRefreshSuccessTimer = null;
}

function setRefreshButtonLoadingState(button, isLoading) {
    if (!button) {
        return;
    }

    if (isLoading) {
        button.classList.add('is-loading');
        button.classList.remove('is-success');
        setRefreshButtonIconState(button, { showRefresh: true, showCheck: false });
        return;
    }

    button.classList.remove('is-loading');
}

function resetRefreshButtonState(button, { disabled = false } = {}) {
    if (!button) {
        return;
    }

    clearRefreshButtonSuccessTimer(button);
    button.classList.remove('is-loading', 'is-success');
    setRefreshButtonIconState(button, { showRefresh: true, showCheck: false });
    button.disabled = disabled;
}

function showRefreshButtonSuccessState(button, { duration = 3000, onComplete } = {}) {
    if (!button) {
        if (typeof onComplete === 'function') {
            onComplete();
        }
        return;
    }

    clearRefreshButtonSuccessTimer(button);
    button.classList.remove('is-loading');
    button.classList.add('is-success');
    button.disabled = true;
    setRefreshButtonIconState(button, { showRefresh: false, showCheck: true });

    button._adminRefreshSuccessTimer = setTimeout(() => {
        button._adminRefreshSuccessTimer = null;
        button.classList.remove('is-success');
        setRefreshButtonIconState(button, { showRefresh: true, showCheck: false });
        button.disabled = false;
        if (typeof onComplete === 'function') {
            onComplete();
        }
    }, duration);
}

function createAdminEmptyPlaceholder({ title = '', description = '', icon = '', className = '' } = {}) {
    const emptyState = document.createElement('div');
    emptyState.className = ['user-notifications-empty', className].filter(Boolean).join(' ');

    if (icon) {
        const iconWrapper = document.createElement('div');
        iconWrapper.className = 'user-notifications-empty-icon';
        iconWrapper.setAttribute('aria-hidden', 'true');
        const safeIcon = sanitizeAdminEmptyStateIcon(icon);
        if (safeIcon) {
            iconWrapper.appendChild(safeIcon);
        }
        emptyState.appendChild(iconWrapper);
    }

    if (title) {
        const titleElement = document.createElement('h3');
        titleElement.className = 'user-notifications-empty-title';
        titleElement.textContent = title;
        emptyState.appendChild(titleElement);
    }

    if (description) {
        const descriptionElement = document.createElement('p');
        descriptionElement.className = 'user-notifications-empty-text';
        descriptionElement.textContent = description;
        emptyState.appendChild(descriptionElement);
    }

    return emptyState;
}

function createAdminLoadingPlaceholder({ message = helperT('admin_loading_ellipsis', 'Loading...'), className = '' } = {}) {
    const wrapper = document.createElement('div');
    wrapper.className = ['admin-loading-placeholder', className].filter(Boolean).join(' ');
    wrapper.setAttribute('role', 'status');
    wrapper.setAttribute('aria-live', 'polite');

    const box = document.createElement('div');
    box.className = 'admin-loading-box';

    const spinner = document.createElement('div');
    spinner.className = 'admin-loading-spinner';
    spinner.setAttribute('aria-hidden', 'true');

    const text = document.createElement('p');
    text.className = 'admin-loading-text';
    text.textContent = message;

    box.appendChild(spinner);
    box.appendChild(text);
    wrapper.appendChild(box);
    return wrapper;
}

