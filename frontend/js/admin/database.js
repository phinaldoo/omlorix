(function () {
    const API_BASE = '/api/v1/admin/backups';
    const BACKUP_HISTORY_PAGE_SIZE = 10;

    const dom = {
        refreshButton: document.getElementById('databaseRefreshButton'),
        openBackupNowModalButton: document.getElementById('openBackupNowModalButton'),
        backupNowOverlay: document.getElementById('backupNowOverlay'),
        backupNowCancelButton: document.getElementById('backupNowCancelButton'),
        backupNowCancelButtonText: document.getElementById('backupNowCancelButtonText'),
        backupNowSettingsContent: document.getElementById('backupNowSettingsContent'),
        backupNowSetupRequired: document.getElementById('backupNowSetupRequired'),
        backupNowDestinationSelect: document.getElementById('backupNowDestinationSelect'),
        backupNowEncryptionEnabled: document.getElementById('backupNowEncryptionEnabled'),
        backupNowEncryptionCapabilityWarning: document.getElementById('backupNowEncryptionCapabilityWarning'),
        backupNowPlaintextPolicyWarning: document.getElementById('backupNowPlaintextPolicyWarning'),
        backupNowCreateButton: document.getElementById('backupNowCreateButton'),

        openDestinationModalButton: document.getElementById('openBackupDestinationModalButton'),
        destinationOverlay: document.getElementById('backupDestinationOverlay'),
        destinationModalTitle: document.getElementById('backupDestinationModalTitle'),
        destinationCancelButton: document.getElementById('backupDestinationCancelButton'),

        destinationEditingId: document.getElementById('backupDestinationEditingId'),
        destinationNameInput: document.getElementById('backupDestinationNameInput'),
        destinationProviderSelect: document.getElementById('backupDestinationProviderSelect'),
        destinationProviderDescription: document.getElementById('backupDestinationProviderDescription'),
        destinationProviderFields: document.getElementById('backupDestinationProviderFields'),
        destinationFormStatus: document.getElementById('backupDestinationFormStatus'),
        destinationAdvancedPanel: document.getElementById('backupDestinationAdvancedPanel'),
        destinationConfigInput: document.getElementById('backupDestinationConfigInput'),
        destinationConfigError: document.getElementById('backupDestinationConfigError'),
        destinationFormatJsonButton: document.getElementById('backupDestinationFormatJsonButton'),
        destinationEnabledInput: document.getElementById('backupDestinationEnabledInput'),
        destinationSaveButton: document.getElementById('backupDestinationSaveButton'),
        destinationList: document.getElementById('backupDestinationList'),

        openScheduleModalButton: document.getElementById('openBackupScheduleModalButton'),
        scheduleOverlay: document.getElementById('backupScheduleOverlay'),
        scheduleModalTitle: document.getElementById('backupScheduleModalTitle'),
        scheduleCancelButton: document.getElementById('backupScheduleCancelButton'),

        scheduleEditingId: document.getElementById('backupScheduleEditingId'),
        scheduleNameInput: document.getElementById('backupScheduleNameInput'),
        scheduleTimezoneSelect: document.getElementById('backupScheduleTimezoneSelect'),
        scheduleFrequencySelect: document.getElementById('backupScheduleFrequencySelect'),
        scheduleTimeDesc: document.getElementById('backupScheduleTimeDesc'),
        scheduleHourlyTimePanel: document.getElementById('backupScheduleHourlyTimePanel'),
        scheduleHourlyMinuteInput: document.getElementById('backupScheduleHourlyMinuteInput'),
        scheduleClockTimePanel: document.getElementById('backupScheduleClockTimePanel'),
        scheduleTimeInput: document.getElementById('backupScheduleTimeInput'),
        scheduleDaysPanel: document.getElementById('backupScheduleDaysPanel'),
        scheduleDaysButtons: document.getElementById('backupScheduleDaysButtons'),
        scheduleRetentionCountInput: document.getElementById('backupScheduleRetentionCountInput'),
        scheduleRetentionDaysInput: document.getElementById('backupScheduleRetentionDaysInput'),
        scheduleDestinationSelect: document.getElementById('backupScheduleDestinationSelect'),
        scheduleEnabledInput: document.getElementById('backupScheduleEnabledInput'),
        scheduleSaveButton: document.getElementById('backupScheduleSaveButton'),
        scheduleList: document.getElementById('backupScheduleList'),

        jobsList: document.getElementById('backupJobsList'),
        jobsPagination: document.getElementById('backupJobsPagination'),
        jobsPaginationInfo: document.getElementById('backupJobsPaginationInfo'),
        jobsPaginationPages: document.getElementById('backupJobsPaginationPages'),
        jobsPaginationPrev: document.getElementById('backupJobsPaginationPrev'),
        jobsPaginationNext: document.getElementById('backupJobsPaginationNext'),

        actionConfirmOverlay: document.getElementById('databaseActionConfirmOverlay'),
        actionConfirmTitle: document.getElementById('databaseActionConfirmTitle'),
        actionConfirmDescription: document.getElementById('databaseActionConfirmDescription'),
        actionConfirmCancelButton: document.getElementById('databaseActionConfirmCancelButton'),
        actionConfirmPrimaryButton: document.getElementById('databaseActionConfirmPrimaryButton'),
        actionConfirmPrimaryText: document.getElementById('databaseActionConfirmPrimaryText'),
    };

    const state = {
        initialized: false,
        destinations: [],
        schedules: [],
        jobs: [],
        jobsPage: 1,
        jobsPageSize: BACKUP_HISTORY_PAGE_SIZE,
        jobsTotal: 0,
        jobsTotalPages: 0,
        jobsLoading: false,
        jobsRequestController: null,
        jobsRequestSequence: 0,
        destinationProviderDrafts: {},
        destinationProviderBeforeChange: 'local',
        destinationSaveInProgress: false,
        destinationTestsInProgress: new Set(),
        destinationTestResults: new Map(),
        confirmResolver: null,
        backupCapabilities: null,
        backupNowEncryptionPreferred: !!dom.backupNowEncryptionEnabled?.checked,
        refreshInProgress: false,
        refreshCooldown: false,
        jobDownloadsInProgress: new Set(),
        jobVerificationsInProgress: new Set(),
        modalLastFocusedElements: new Map(),
    };

    const backupModalSelects = [
        dom.backupNowDestinationSelect,
        dom.destinationProviderSelect,
        dom.scheduleTimezoneSelect,
        dom.scheduleFrequencySelect,
        dom.scheduleDestinationSelect,
    ];

    let backupSelectGeneratedId = 0;

    function t(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function tf(key, fallback, vars) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        let template = t(key, fallback);
        if (vars && typeof vars === 'object') {
            template = String(template).replace(/\{(\w+)\}/g, (_, token) => {
                const value = vars[token];
                return value === undefined || value === null ? '' : String(value);
            });
        }
        return template;
    }

    /**
     * Build the bounded backend URL for the currently selected history page.
     */
    function buildBackupJobsPageUrl(page, pageSize) {
        const params = new URLSearchParams({
            page: String(page),
            page_size: String(pageSize),
        });
        return `${API_BASE}/jobs?${params.toString()}`;
    }

    /**
     * Keep large backup histories navigable without rendering every page number.
     */
    function generateBackupHistoryPageNumbers(currentPage, totalPages) {
        if (totalPages <= 7) {
            return Array.from({ length: totalPages }, (_, index) => index + 1);
        }
        if (currentPage <= 4) {
            return [1, 2, 3, 4, 5, '…', totalPages];
        }
        if (currentPage >= totalPages - 3) {
            return [1, '…', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
        }
        return [1, '…', currentPage - 1, currentPage, currentPage + 1, '…', totalPages];
    }

    function getBrowserTimeZone() {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
        } catch (_) {
            return 'UTC';
        }
    }

    function getTimeZoneOffsetLabel(timeZone) {
        try {
            const formatter = new Intl.DateTimeFormat('en-US', {
                timeZone,
                hour: '2-digit',
                minute: '2-digit',
                timeZoneName: 'shortOffset',
            });
            const zoneName = formatter.formatToParts(new Date()).find((part) => part.type === 'timeZoneName')?.value || 'UTC';
            return zoneName.replace(/^GMT$/i, 'UTC').replace(/^GMT/i, 'UTC');
        } catch (_) {
            return 'UTC';
        }
    }

    function formatTimeZoneLabel(timeZone) {
        return `${timeZone} (${getTimeZoneOffsetLabel(timeZone)})`;
    }

    function getSupportedTimeZoneValues(extraValues = []) {
        const browserTimeZone = getBrowserTimeZone();
        let timeZones = [];
        if (typeof Intl?.supportedValuesOf === 'function') {
            try {
                timeZones = Intl.supportedValuesOf('timeZone');
            } catch (_) {
                timeZones = [];
            }
        }
        const values = Array.from(new Set([
            'UTC',
            browserTimeZone,
            ...extraValues.filter(Boolean),
            ...(Array.isArray(timeZones) ? timeZones : []),
        ])).filter(Boolean);
        return values.sort((left, right) => {
            if (left === browserTimeZone && right !== browserTimeZone) {
                return -1;
            }
            if (right === browserTimeZone && left !== browserTimeZone) {
                return 1;
            }
            if (left === 'UTC' && right !== 'UTC') {
                return -1;
            }
            if (right === 'UTC' && left !== 'UTC') {
                return 1;
            }
            return left.localeCompare(right);
        });
    }

    function populateScheduleTimezoneSelect(selectedValue = null) {
        if (!dom.scheduleTimezoneSelect) {
            return;
        }
        const nextValue = String(
            selectedValue
            || dom.scheduleTimezoneSelect.value
            || getBrowserTimeZone()
            || 'UTC'
        ).trim() || 'UTC';
        const values = getSupportedTimeZoneValues([nextValue]);
        dom.scheduleTimezoneSelect.innerHTML = values
            .map((timeZone) => `<option value="${escapeHtml(timeZone)}">${escapeHtml(formatTimeZoneLabel(timeZone))}</option>`)
            .join('');
        setBackupSelectValue(
            dom.scheduleTimezoneSelect,
            values.includes(nextValue) ? nextValue : (values[0] || 'UTC')
        );
        dom.scheduleTimezoneSelect._singleSelect?.refreshOptions?.();
        dom.scheduleTimezoneSelect._singleSelect?.syncFromSelect?.();
    }

    function ensureElementId(element, prefix) {
        if (!element) {
            return '';
        }
        if (!element.id) {
            backupSelectGeneratedId += 1;
            element.id = `${prefix}-${backupSelectGeneratedId}`;
        }
        return element.id;
    }

    function syncBackupSelect(select) {
        select?._singleSelect?.syncFromSelect?.();
    }

    function setBackupSelectValue(select, value) {
        if (!select) {
            return;
        }
        select.value = value;
        syncBackupSelect(select);
    }

    function focusBackupSelect(select) {
        const trigger = select?._singleSelect?.wrapper?.querySelector('.admin-select-trigger');
        if (trigger) {
            trigger.focus();
            return;
        }
        select?.focus();
    }

    function applyBackupSelectAccessibility(select, meta) {
        const trigger = meta?.wrapper?.querySelector('.admin-select-trigger');
        if (!select || !trigger) {
            return;
        }

        const label = document.querySelector(`label[for="${select.id}"]`);
        if (label) {
            const labelId = ensureElementId(label, 'backup-select-label');
            select.setAttribute('aria-labelledby', labelId);
            trigger.setAttribute('aria-labelledby', labelId);
        } else if (select.getAttribute('aria-label')) {
            trigger.setAttribute('aria-label', select.getAttribute('aria-label'));
        }

        const describedBy = select.getAttribute('aria-describedby');
        if (describedBy) {
            trigger.setAttribute('aria-describedby', describedBy);
        }
    }

    function upgradeBackupSelect(select) {
        if (!select || typeof window.upgradeAdminSingleSelect !== 'function') {
            return;
        }

        const label = document.querySelector(`label[for="${select.id}"]`);
        const selectedOption = select.selectedOptions?.[0];
        const meta = window.upgradeAdminSingleSelect(select, {
            key: select.id,
            placeholder: selectedOption?.textContent || label?.textContent?.trim() || '',
        });
        applyBackupSelectAccessibility(select, meta);
        syncBackupSelect(select);
    }

    function upgradeBackupModalSelects() {
        backupModalSelects.forEach(upgradeBackupSelect);
        populateScheduleTimezoneSelect(dom.scheduleTimezoneSelect?.value || getBrowserTimeZone());
    }

    function escapeHtml(value) {
        if (typeof window.escapeHtml === 'function') {
            return window.escapeHtml(value);
        }
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatBackupStorage(meta) {
        if (!meta || typeof meta !== 'object') {
            return '-';
        }
        const scheme = meta.scheme || 'unknown';
        const fingerprint = meta.fingerprint ? ` · ${meta.fingerprint}` : '';
        return `${scheme}${fingerprint}`;
    }

    function setStatus(kind, message) {
        if (!message) {
            return;
        }
        if (kind === 'success') {
            window.notifySuccess?.(message);
            return;
        }
        if (kind === 'warning') {
            window.notifyWarning?.(message);
            return;
        }
        window.notifyError?.(message);
    }

    function clearStatus() {}

    function setBusy(button, busy, labelWhenBusy) {
        if (!button) {
            return;
        }
        if (typeof window.setButtonLoadingState === 'function') {
            window.setButtonLoadingState(button, busy, labelWhenBusy);
            return;
        }
        const labelTarget = button.querySelector('span');
        const readLabel = () => (labelTarget ? labelTarget.textContent : button.textContent)?.trim() || '';
        const writeLabel = (label) => {
            if (labelTarget) {
                labelTarget.textContent = label;
            } else {
                button.textContent = label;
            }
        };
        button.disabled = !!busy;
        button.classList.toggle('loading', !!busy);
        if (busy) {
            button.setAttribute('aria-busy', 'true');
            if (button.dataset.databaseOriginalLabel === undefined) {
                button.dataset.databaseOriginalLabel = readLabel();
            }
            if (labelWhenBusy) {
                writeLabel(labelWhenBusy);
            }
        } else {
            button.removeAttribute('aria-busy');
            if (button.dataset.databaseOriginalLabel !== undefined) {
                writeLabel(button.dataset.databaseOriginalLabel);
                delete button.dataset.databaseOriginalLabel;
            }
        }
    }

    function setRefreshButtonIconState(button, { showRefresh, showCheck }) {
        const refreshIcon = button?.querySelector('.refresh-icon');
        const checkIcon = button?.querySelector('.check-icon');

        if (refreshIcon) {
            refreshIcon.hidden = !showRefresh;
            refreshIcon.toggleAttribute('hidden', !showRefresh);
        }

        if (checkIcon) {
            checkIcon.hidden = !showCheck;
            checkIcon.toggleAttribute('hidden', !showCheck);
        }
    }

    function setPageRefreshLoading(isLoading) {
        if (!dom.refreshButton) {
            return;
        }

        if (typeof window.adminSetRefreshButtonLoadingState === 'function') {
            window.adminSetRefreshButtonLoadingState(dom.refreshButton, isLoading);
            return;
        }

        dom.refreshButton.classList.toggle('is-loading', !!isLoading);
        if (isLoading) {
            dom.refreshButton.classList.remove('is-success');
            setRefreshButtonIconState(dom.refreshButton, { showRefresh: true, showCheck: false });
        }
    }

    function resetPageRefreshButton() {
        if (!dom.refreshButton) {
            return;
        }

        if (typeof window.adminResetRefreshButtonState === 'function') {
            window.adminResetRefreshButtonState(dom.refreshButton);
            return;
        }

        dom.refreshButton.disabled = false;
        dom.refreshButton.classList.remove('is-loading', 'is-success');
        setRefreshButtonIconState(dom.refreshButton, { showRefresh: true, showCheck: false });
    }

    function showPageRefreshSuccess() {
        if (!dom.refreshButton) {
            state.refreshCooldown = false;
            return;
        }

        if (typeof window.adminShowRefreshButtonSuccessState === 'function') {
            window.adminShowRefreshButtonSuccessState(dom.refreshButton, {
                duration: 3000,
                onComplete: () => {
                    state.refreshCooldown = false;
                },
            });
            return;
        }

        dom.refreshButton.disabled = true;
        dom.refreshButton.classList.add('is-success');
        setRefreshButtonIconState(dom.refreshButton, { showRefresh: false, showCheck: true });

        setTimeout(() => {
            dom.refreshButton.disabled = false;
            dom.refreshButton.classList.remove('is-success');
            setRefreshButtonIconState(dom.refreshButton, { showRefresh: true, showCheck: false });
            state.refreshCooldown = false;
        }, 3000);
    }

    async function parseError(response, fallback) {
        // Backup destination validation uses stable machine-readable error
        // codes. Translate those codes here instead of exposing backend or
        // browser implementation details to the administrator.
        try {
            const payload = await response.clone().json();
            const detail = payload?.detail;
            if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
                if (detail.code === 'backup_plaintext_archives_disabled') {
                    return t(
                        'db_backup_now_plaintext_unavailable',
                        'Archive encryption is required because this server does not allow plaintext backup archives.',
                    );
                }
                if (detail.code === 'backup_archive_encryption_unavailable') {
                    return t(
                        'db_backup_now_encryption_unavailable',
                        'Encrypted backups are not configured on this server yet.',
                    );
                }
                const fieldDefinition = Object.values(DESTINATION_PROVIDER_DEFINITIONS || {})
                    .flatMap((definition) => definition.fields || [])
                    .find((field) => field.key === detail.field);
                const fieldLabel = fieldDefinition
                    ? t(fieldDefinition.labelKey, fieldDefinition.label)
                    : String(detail.field || '');
                if (detail.code === 'destination_config_field_required') {
                    return tf('db_destination_field_required', '{field} is required.', { field: fieldLabel });
                }
                if (detail.code === 'destination_config_azure_auth_required') {
                    return t('db_destination_azure_auth_required', 'Enter either a connection string or an account URL.');
                }
                if (detail.code === 'destination_config_url_invalid') {
                    return t('db_destination_url_invalid', 'Enter a valid HTTP or HTTPS URL.');
                }
                if (detail.code === 'destination_config_number_invalid') {
                    return tf('db_destination_number_range', 'Enter a number from {min} to {max}.', { min: 1, max: 3600 });
                }
                if (detail.code === 'destination_config_object_required') {
                    return t('db_destination_config_must_object', 'Additional config must be a JSON object.');
                }
                return fallback;
            }
        } catch (_) {
            // Non-JSON errors continue through the project's shared parser.
        }
        if (typeof window.buildResponseError === 'function') {
            return window.buildResponseError(response, fallback);
        }
        try {
            const payload = await response.json();
            return payload?.detail || fallback;
        } catch {
            return fallback;
        }
    }

    async function apiJson(path, init) {
        const response = await window.authedFetch(path, init);
        if (!response.ok) {
            throw new Error(await parseError(response, t('db_generic_request_failed', 'Request failed.')));
        }
        if (response.status === 204) {
            return null;
        }
        return response.json();
    }

    function formatDate(isoDate) {
        if (!isoDate) {
            return '-';
        }
        const date = new Date(isoDate);
        return Number.isNaN(date.getTime()) ? isoDate : date.toLocaleString();
    }

    function statusPillClass(status) {
        const normalized = String(status || '').toLowerCase();
        if (['success', 'completed', 'ok', 'verified', 'enabled', 'active'].includes(normalized)) {
            return 'success';
        }
        if (['failed', 'error', 'inactive', 'disabled'].includes(normalized)) {
            return 'error';
        }
        return 'warning';
    }

    const sharedIcons = globalThis.Icons || {};
    const databaseActionIcons = {
        "edit": sharedIcons.edit,
        "test": sharedIcons.check,
        "delete": sharedIcons.trash,
        "run": sharedIcons.play,
        "verify": sharedIcons.protection,
        "download": sharedIcons.download,
        "warning": sharedIcons.warning,
    };

    function actionIcon(name) {
        return databaseActionIcons[name] || '';
    }

    function renderActionButton({
        action,
        idName,
        id,
        kind = 'cancel',
        icon,
        label,
        disabled = false,
        loading = false,
        className = '',
        iconOnly = false,
        title = '',
        ariaLabel = '',
    }) {
        // A job list refresh replaces the original button element. Rendering
        // the loading state from application state keeps long-running actions
        // visibly busy and non-interactive even after that replacement.
        const disabledAttr = disabled || loading ? ' disabled' : '';
        const loadingClass = loading ? ' loading' : '';
        const ariaBusyAttr = loading ? ' aria-busy="true"' : '';
        const customClass = className ? ` ${escapeHtml(className)}` : '';
        const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
        const ariaLabelAttr = ariaLabel ? ` aria-label="${escapeHtml(ariaLabel)}"` : '';
        return `
            <button type="button" class="om-button border ${escapeHtml(kind)} db-action-button${loadingClass}${customClass}" data-${escapeHtml(idName)}-action="${escapeHtml(action)}" data-${escapeHtml(idName)}-id="${escapeHtml(id)}"${disabledAttr}${ariaBusyAttr}${titleAttr}${ariaLabelAttr}>
                ${actionIcon(icon || action)}
                ${iconOnly ? '' : `<span>${escapeHtml(label)}</span>`}
            </button>
        `;
    }

    function renderNativeDownloadLink({ id, label, disabled = false }) {
        // A regular same-origin link hands the response stream directly to the
        // browser's download manager. This is intentionally not an authedFetch:
        // converting the response to a Blob would retain the complete backup in
        // page memory and only create the file after every byte had arrived.
        if (disabled) {
            return renderActionButton({
                action: 'download',
                idName: 'job',
                id,
                icon: 'download',
                label,
                disabled: true,
            });
        }

        const downloadUrl = `${API_BASE}/jobs/${encodeURIComponent(String(id))}/download`;
        return `
            <a class="om-button border cancel db-action-button" href="${escapeHtml(downloadUrl)}" download data-native-backup-download data-job-id="${escapeHtml(id)}">
                ${actionIcon('download')}
                <span>${escapeHtml(label)}</span>
            </a>
        `;
    }

    function renderEmptyState(message) {
        return `<div class="user-notifications-empty provider-empty-state"><p class="user-notifications-empty-text">${escapeHtml(message)}</p></div>`;
    }

    const REDACTED_CONFIG_VALUE = '***redacted***';
    const CLEAR_SAVED_SECRET_VALUE = Symbol('clear-saved-secret');

    // These definitions are the single source of truth for both rendering and
    // serializing the provider-specific destination fields. Additional keys can
    // still be supplied in the advanced JSON editor below the generated fields.
    const DESTINATION_PROVIDER_DEFINITIONS = {
        local: {
            descriptionKey: 'db_destination_provider_local_desc',
            description: 'Store backup archives on this Omlorix server.',
            fields: [
                {
                    key: 'base_path',
                    labelKey: 'db_destination_field_base_path',
                    label: 'Base path',
                    placeholder: '/app/backups',
                },
            ],
        },
        s3: {
            descriptionKey: 'db_destination_provider_s3_desc',
            description: 'Store backups in an Amazon S3 bucket or an S3-compatible service.',
            fields: [
                { key: 'bucket', labelKey: 'db_destination_field_bucket', label: 'Bucket', required: true, placeholder: 'my-backups' },
                { key: 'prefix', labelKey: 'db_destination_field_prefix', label: 'Path prefix', placeholder: 'omlorix' },
                { key: 'region', labelKey: 'db_destination_field_region', label: 'Region', placeholder: 'eu-central-1' },
                { key: 'endpoint_url', labelKey: 'db_destination_field_endpoint_url', label: 'Custom endpoint URL', placeholder: 'https://s3.example.com', inputType: 'url' },
                { key: 'access_key_id', labelKey: 'db_destination_field_access_key_id', label: 'Access key ID', secret: true, autocomplete: 'new-password' },
                { key: 'secret_access_key', labelKey: 'db_destination_field_secret_access_key', label: 'Secret access key', secret: true, autocomplete: 'new-password' },
                { key: 'session_token', labelKey: 'db_destination_field_session_token', label: 'Session token', secret: true, autocomplete: 'new-password' },
            ],
        },
        gcs: {
            descriptionKey: 'db_destination_provider_gcs_desc',
            description: 'Store backups in a Google Cloud Storage bucket.',
            fields: [
                { key: 'bucket', labelKey: 'db_destination_field_bucket', label: 'Bucket', required: true, placeholder: 'my-backups' },
                { key: 'prefix', labelKey: 'db_destination_field_prefix', label: 'Path prefix', placeholder: 'omlorix' },
                { key: 'project', labelKey: 'db_destination_field_project', label: 'Google Cloud project', placeholder: 'my-project' },
                {
                    key: 'credentials_json',
                    labelKey: 'db_destination_field_credentials_json',
                    label: 'Service account JSON',
                    kind: 'json',
                    secret: true,
                    rows: 4,
                    placeholder: '{"type":"service_account", ...}',
                },
            ],
        },
        azure: {
            descriptionKey: 'db_destination_provider_azure_desc',
            description: 'Store backups in an Azure Blob Storage container.',
            fields: [
                { key: 'container', labelKey: 'db_destination_field_container', label: 'Container', required: true, placeholder: 'omlorix-backups' },
                { key: 'prefix', labelKey: 'db_destination_field_prefix', label: 'Path prefix', placeholder: 'omlorix' },
                { key: 'connection_string', labelKey: 'db_destination_field_connection_string', label: 'Connection string', secret: true, autocomplete: 'new-password' },
                { key: 'account_url', labelKey: 'db_destination_field_account_url', label: 'Account URL', placeholder: 'https://account.blob.core.windows.net', inputType: 'url' },
                { key: 'credential', labelKey: 'db_destination_field_credential', label: 'Account credential', secret: true, autocomplete: 'new-password' },
            ],
        },
        webdav: {
            descriptionKey: 'db_destination_provider_webdav_desc',
            description: 'Store backups on a WebDAV server, including compatible NAS devices.',
            fields: [
                { key: 'url', labelKey: 'db_destination_field_url', label: 'Server URL', required: true, placeholder: 'https://nas.example.com/webdav', inputType: 'url' },
                { key: 'username', labelKey: 'db_destination_field_username', label: 'Username', autocomplete: 'username' },
                { key: 'password', labelKey: 'db_destination_field_password', label: 'Password', secret: true, autocomplete: 'new-password' },
                { key: 'prefix', labelKey: 'db_destination_field_prefix', label: 'Path prefix', placeholder: 'omlorix' },
                { key: 'verify_ssl', labelKey: 'db_destination_field_verify_ssl', label: 'Verify TLS certificate', kind: 'boolean', defaultValue: true },
                { key: 'timeout', labelKey: 'db_destination_field_timeout', label: 'Timeout (seconds)', kind: 'number', defaultValue: 30, min: 1, max: 3600, placeholder: '30' },
            ],
        },
    };

    function getDestinationProviderDefinition(provider = dom.destinationProviderSelect?.value) {
        return DESTINATION_PROVIDER_DEFINITIONS[provider] || DESTINATION_PROVIDER_DEFINITIONS.local;
    }

    function setDestinationFormStatus(message = '') {
        if (!dom.destinationFormStatus) {
            return;
        }
        dom.destinationFormStatus.textContent = message;
        dom.destinationFormStatus.hidden = !message;
    }

    function setDestinationFieldError(input, message = '') {
        if (!input) {
            return;
        }
        const group = input.closest('.form-group');
        const error = group?.querySelector('.field-error-message');
        input.classList.toggle('field-error', !!message);
        input.setAttribute('aria-invalid', message ? 'true' : 'false');
        if (error) {
            error.textContent = message;
            error.hidden = !message;
        }
    }

    function clearDestinationValidation() {
        setDestinationFormStatus('');
        [dom.destinationNameInput, dom.destinationConfigInput].forEach((input) => {
            setDestinationFieldError(input, '');
        });
        dom.destinationProviderFields?.querySelectorAll('[data-config-key]').forEach((input) => {
            setDestinationFieldError(input, '');
        });
    }

    function destinationFieldInput(field, rawValue) {
        const inputId = `backupDestinationField-${field.key.replace(/_/g, '-')}`;
        const errorId = `${inputId}-error`;
        const isClearedSecret = field.secret && rawValue === CLEAR_SAVED_SECRET_VALUE;
        const isSavedSecret = field.secret && (
            rawValue === REDACTED_CONFIG_VALUE ||
            isClearedSecret
        );
        const value = isSavedSecret || rawValue === undefined || rawValue === null
            ? ''
            : String(rawValue);
        const requiredText = field.required
            ? `<span class="db-field-required"> ${escapeHtml(t('db_destination_required_marker', '(required)'))}</span>`
            : '';
        const savedSecret = isSavedSecret
            ? `
                <div class="db-saved-secret">
                    <span>${escapeHtml(t('db_destination_secret_saved', 'A secret is saved. Leave this field blank to keep it.'))}</span>
                    <label class="db-secret-clear-row">
                        <input type="checkbox" class="form-checkbox" data-clear-secret-for="${escapeHtml(field.key)}" ${isClearedSecret ? 'checked' : ''}>
                        <span>${escapeHtml(t('db_destination_secret_clear', 'Remove saved secret'))}</span>
                    </label>
                </div>
            `
            : '';

        if (field.kind === 'boolean') {
            const checked = rawValue === undefined ? !!field.defaultValue : !!rawValue;
            return `
                <div class="form-group db-destination-field db-destination-field--checkbox">
                    <label class="form-checkbox-row" for="${inputId}">
                        <input
                            type="checkbox"
                            class="form-checkbox"
                            id="${inputId}"
                            data-config-key="${escapeHtml(field.key)}"
                            data-config-kind="boolean"
                            ${checked ? 'checked' : ''}
                        >
                        <span>${escapeHtml(t(field.labelKey, field.label))}</span>
                    </label>
                    <div class="field-error-message" id="${errorId}" role="alert" hidden></div>
                </div>
            `;
        }

        const commonAttributes = `
            id="${inputId}"
            data-config-key="${escapeHtml(field.key)}"
            data-config-kind="${escapeHtml(field.kind || 'string')}"
            data-config-secret="${field.secret ? 'true' : 'false'}"
            data-saved-secret="${isSavedSecret ? 'true' : 'false'}"
            aria-describedby="${errorId}"
            ${isClearedSecret ? 'disabled' : ''}
            ${field.required ? 'required' : ''}
            ${field.autocomplete ? `autocomplete="${escapeHtml(field.autocomplete)}"` : ''}
            ${field.placeholder ? `placeholder="${escapeHtml(field.placeholder)}"` : ''}
        `;
        const control = field.kind === 'json'
            ? `<textarea class="form-textarea db-config-textarea" rows="${field.rows || 4}" spellcheck="false" ${commonAttributes}>${escapeHtml(value)}</textarea>`
            : `
                <input
                    class="form-input"
                    type="${escapeHtml(field.secret ? 'password' : (field.inputType || (field.kind === 'number' ? 'number' : 'text')))}"
                    value="${escapeHtml(value)}"
                    ${field.min !== undefined ? `min="${field.min}"` : ''}
                    ${field.max !== undefined ? `max="${field.max}"` : ''}
                    ${commonAttributes}
                >
            `;

        return `
            <div class="form-group db-destination-field">
                <label class="form-label" for="${inputId}">
                    ${escapeHtml(t(field.labelKey, field.label))}${requiredText}
                </label>
                ${control}
                ${savedSecret}
                <div class="field-error-message" id="${errorId}" role="alert" hidden></div>
            </div>
        `;
    }

    function renderDestinationProviderFields(values = {}) {
        const provider = dom.destinationProviderSelect?.value || 'local';
        const definition = getDestinationProviderDefinition(provider);
        if (dom.destinationProviderDescription) {
            dom.destinationProviderDescription.textContent = t(
                definition.descriptionKey,
                definition.description,
            );
        }
        if (!dom.destinationProviderFields) {
            return;
        }
        dom.destinationProviderFields.innerHTML = definition.fields
            .map((field) => {
                const value = Object.hasOwn(values, field.key) ? values[field.key] : field.defaultValue;
                return destinationFieldInput(field, value);
            })
            .join('');
        state.destinationProviderBeforeChange = provider;
    }

    function captureDestinationProviderDraft() {
        const provider = state.destinationProviderBeforeChange;
        if (!provider || !dom.destinationProviderFields) {
            return;
        }
        const draft = {};
        dom.destinationProviderFields.querySelectorAll('[data-config-key]').forEach((input) => {
            const key = input.dataset.configKey;
            const clearSecret = dom.destinationProviderFields.querySelector(
                `[data-clear-secret-for="${key}"]`,
            );
            if (input.dataset.configKind === 'boolean') {
                draft[key] = !!input.checked;
            } else if (clearSecret?.checked) {
                draft[key] = CLEAR_SAVED_SECRET_VALUE;
            } else if (input.value !== '') {
                draft[key] = input.value;
            } else if (input.dataset.savedSecret === 'true') {
                draft[key] = REDACTED_CONFIG_VALUE;
            }
        });
        state.destinationProviderDrafts[provider] = draft;
    }

    function friendlyJsonError(error, raw) {
        const message = String(error?.message || '');
        const positionMatch = message.match(/position\s+(\d+)/i);
        if (!positionMatch) {
            return t('db_destination_config_invalid', 'Enter a valid JSON object.');
        }
        const position = Number(positionMatch[1]);
        const beforeError = raw.slice(0, position);
        const line = beforeError.split('\n').length;
        const column = position - beforeError.lastIndexOf('\n');
        return tf(
            'db_destination_config_invalid_position',
            'Invalid JSON near line {line}, column {column}. Check for a missing value, comma, or quote.',
            { line, column },
        );
    }

    function parseAdditionalConfig({ showError = false } = {}) {
        const raw = String(dom.destinationConfigInput?.value || '').trim();
        if (!raw) {
            if (showError) {
                setDestinationFieldError(dom.destinationConfigInput, '');
            }
            return { valid: true, value: {} };
        }
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
                const message = t('db_destination_config_must_object', 'Additional config must be a JSON object.');
                if (showError) {
                    setDestinationFieldError(dom.destinationConfigInput, message);
                }
                return { valid: false, value: {}, message };
            }
            if (showError) {
                setDestinationFieldError(dom.destinationConfigInput, '');
            }
            return { valid: true, value: parsed };
        } catch (error) {
            const message = friendlyJsonError(error, raw);
            if (showError) {
                setDestinationFieldError(dom.destinationConfigInput, message);
                if (dom.destinationAdvancedPanel) {
                    dom.destinationAdvancedPanel.open = true;
                }
            }
            return { valid: false, value: {}, message };
        }
    }

    function localizedDestinationTestFailure(errorCode) {
        const messages = {
            backup_destination_tls_certificate_invalid: [
                'db_destination_test_tls_certificate_invalid',
                'TLS certificate verification failed. Use a hostname covered by the certificate, trust its issuing CA, or disable verification only for a server you trust.',
            ],
            backup_destination_authentication_failed: [
                'db_destination_test_authentication_failed',
                'The destination rejected the credentials. Check the configured username, password, access key, or service-account credentials.',
            ],
            backup_destination_permission_denied: [
                'db_destination_test_permission_denied',
                'The destination was reached but denied access. Check the account permissions for the configured bucket, container, or path.',
            ],
            backup_destination_connection_timeout: [
                'db_destination_test_connection_timeout',
                'The connection timed out. Check the URL, port, firewall, and whether the destination is reachable from the Omlorix server.',
            ],
            backup_destination_unreachable: [
                'db_destination_test_unreachable',
                'The destination could not be reached. Check the URL, port, network route, firewall, and destination service.',
            ],
            backup_destination_path_not_found: [
                'db_destination_test_path_not_found',
                'The configured bucket, container, or path was not found. Check the destination URL and path or prefix.',
            ],
            backup_destination_protocol_unsupported: [
                'db_destination_test_protocol_unsupported',
                'The server responded but does not support a required storage operation. Check that the URL points to the correct storage or WebDAV endpoint.',
            ],
            backup_destination_test_failed: [
                'db_destination_test_failed_detail',
                'The connection test failed. Check the destination configuration and the destination server logs.',
            ],
        };
        const message = messages[String(errorCode || '')] || messages.backup_destination_test_failed;
        return t(message[0], message[1]);
    }

    function updateDestinationSaveAvailability() {
        if (!dom.destinationSaveButton) {
            return;
        }
        dom.destinationSaveButton.disabled = state.destinationSaveInProgress || !parseAdditionalConfig().valid;
    }

    function validateDestinationForm() {
        clearDestinationValidation();
        let firstInvalid = null;
        const markInvalid = (input, message) => {
            setDestinationFieldError(input, message);
            firstInvalid ||= input;
        };

        const name = String(dom.destinationNameInput?.value || '').trim();
        if (!name) {
            markInvalid(dom.destinationNameInput, t('db_destination_name_required', 'Enter a destination name.'));
        }

        const provider = dom.destinationProviderSelect?.value || 'local';
        const definition = getDestinationProviderDefinition(provider);
        const additionalResult = parseAdditionalConfig({ showError: true });
        if (!additionalResult.valid) {
            firstInvalid ||= dom.destinationConfigInput;
        }
        const config = { ...additionalResult.value };
        const managedKeys = new Set(definition.fields.map((field) => field.key));
        const duplicateKeys = Object.keys(config).filter((key) => managedKeys.has(key));
        if (duplicateKeys.length) {
            const message = tf(
                'db_destination_config_duplicate_keys',
                'Remove these named fields from Additional JSON: {keys}.',
                { keys: duplicateKeys.join(', ') },
            );
            markInvalid(dom.destinationConfigInput, message);
            dom.destinationAdvancedPanel && (dom.destinationAdvancedPanel.open = true);
        }

        for (const field of definition.fields) {
            const input = dom.destinationProviderFields?.querySelector(`[data-config-key="${field.key}"]`);
            if (!input) {
                continue;
            }
            const clearSecret = dom.destinationProviderFields?.querySelector(`[data-clear-secret-for="${field.key}"]`);
            if (field.kind === 'boolean') {
                config[field.key] = !!input.checked;
                continue;
            }

            const rawValue = field.secret ? String(input.value || '') : String(input.value || '').trim();
            if (!rawValue) {
                if (field.required) {
                    markInvalid(input, tf(
                        'db_destination_field_required',
                        '{field} is required.',
                        { field: t(field.labelKey, field.label) },
                    ));
                } else if (field.secret && clearSecret?.checked) {
                    // Explicit null tells the backend to remove a saved secret.
                    config[field.key] = null;
                } else if (field.secret && input.dataset.savedSecret === 'true') {
                    // The backend resolves this display-only marker back to the
                    // currently encrypted secret and never persists the marker.
                    config[field.key] = REDACTED_CONFIG_VALUE;
                }
                continue;
            }

            if (field.kind === 'number') {
                const numberValue = Number(rawValue);
                if (!Number.isFinite(numberValue) || numberValue < field.min || numberValue > field.max) {
                    markInvalid(input, tf(
                        'db_destination_number_range',
                        'Enter a number from {min} to {max}.',
                        { min: field.min, max: field.max },
                    ));
                    continue;
                }
                config[field.key] = numberValue;
                continue;
            }

            if (field.kind === 'json') {
                try {
                    const parsed = JSON.parse(rawValue);
                    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
                        throw new Error('not-object');
                    }
                    config[field.key] = parsed;
                } catch (_) {
                    markInvalid(input, t(
                        'db_destination_credentials_json_invalid',
                        'Enter the complete service account JSON object.',
                    ));
                }
                continue;
            }
            config[field.key] = rawValue;
        }

        if (provider === 'azure' && !config.connection_string && !config.account_url) {
            const message = t(
                'db_destination_azure_auth_required',
                'Enter either a connection string or an account URL.',
            );
            const connectionInput = dom.destinationProviderFields?.querySelector('[data-config-key="connection_string"]');
            const accountUrlInput = dom.destinationProviderFields?.querySelector('[data-config-key="account_url"]');
            markInvalid(connectionInput, message);
            setDestinationFieldError(accountUrlInput, message);
        }

        for (const key of ['url', 'endpoint_url', 'account_url']) {
            const input = dom.destinationProviderFields?.querySelector(`[data-config-key="${key}"]`);
            const value = config[key];
            if (!input || !value) {
                continue;
            }
            try {
                const parsedUrl = new URL(value);
                if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
                    throw new Error('unsupported-protocol');
                }
            } catch (_) {
                markInvalid(input, t('db_destination_url_invalid', 'Enter a valid HTTP or HTTPS URL.'));
            }
        }

        if (firstInvalid) {
            setDestinationFormStatus(t(
                'db_destination_review_errors',
                'Review the highlighted fields before saving.',
            ));
            firstInvalid.focus();
            return { valid: false, config: {} };
        }
        return { valid: true, config };
    }

    const SCHEDULE_DAY_SHORT_KEYS = [
        ['admin_day_mon_short', 'Mon'],
        ['admin_day_tue_short', 'Tue'],
        ['admin_day_wed_short', 'Wed'],
        ['admin_day_thu_short', 'Thu'],
        ['admin_day_fri_short', 'Fri'],
        ['admin_day_sat_short', 'Sat'],
        ['admin_day_sun_short', 'Sun'],
    ];

    const SCHEDULE_DAY_FULL_KEYS = [
        ['admin_day_monday', 'Monday'],
        ['admin_day_tuesday', 'Tuesday'],
        ['admin_day_wednesday', 'Wednesday'],
        ['admin_day_thursday', 'Thursday'],
        ['admin_day_friday', 'Friday'],
        ['admin_day_saturday', 'Saturday'],
        ['admin_day_sunday', 'Sunday'],
    ];

    function getScheduleDayLabels() {
        return SCHEDULE_DAY_SHORT_KEYS.map(([key, fallback]) => t(key, fallback));
    }

    function getScheduleFullDayName(dayIndex) {
        const entry = SCHEDULE_DAY_FULL_KEYS[dayIndex];
        return entry ? t(entry[0], entry[1]) : '';
    }

    function formatScheduleDaysList(days) {
        if (!Array.isArray(days) || !days.length) {
            return '-';
        }
        const labels = getScheduleDayLabels();
        return days
            .filter((day) => Number.isInteger(day) && day >= 0 && day <= 6)
            .map((day) => labels[day] || String(day))
            .join(', ');
    }

    function ensureScheduleDayButtons() {
        if (!dom.scheduleDaysButtons || dom.scheduleDaysButtons.dataset.initialized === 'true') {
            return;
        }

        dom.scheduleDaysButtons.dataset.initialized = 'true';
        dom.scheduleDaysButtons.setAttribute(
            'aria-label',
            t('db_schedule_days_aria', 'Days of the week'),
        );

        getScheduleDayLabels().forEach((label, dayIndex) => {
            const dayBtn = document.createElement('button');
            dayBtn.type = 'button';
            dayBtn.className = 'access-rule-day-btn';
            if (dayIndex === 5 || dayIndex === 6) {
                dayBtn.dataset.weekend = 'true';
            }
            dayBtn.textContent = label;
            dayBtn.title = getScheduleFullDayName(dayIndex);
            dayBtn.dataset.dayIndex = String(dayIndex);
            dayBtn.setAttribute('aria-pressed', 'false');
            dayBtn.addEventListener('click', () => {
                dayBtn.classList.toggle('active');
                dayBtn.setAttribute('aria-pressed', dayBtn.classList.contains('active') ? 'true' : 'false');
            });
            dom.scheduleDaysButtons.appendChild(dayBtn);
        });
    }

    function refreshScheduleDayButtonLabels() {
        if (!dom.scheduleDaysButtons) {
            return;
        }
        const labels = getScheduleDayLabels();
        dom.scheduleDaysButtons.querySelectorAll('.access-rule-day-btn').forEach((dayBtn, dayIndex) => {
            dayBtn.textContent = labels[dayIndex] || dayBtn.textContent;
            dayBtn.title = getScheduleFullDayName(dayIndex);
        });
        dom.scheduleDaysButtons.setAttribute(
            'aria-label',
            t('db_schedule_days_aria', 'Days of the week'),
        );
    }

    function getSelectedScheduleDays() {
        if (!dom.scheduleDaysButtons) {
            return [];
        }
        return Array.from(dom.scheduleDaysButtons.querySelectorAll('.access-rule-day-btn.active'))
            .map((dayBtn) => Number.parseInt(dayBtn.dataset.dayIndex, 10))
            .filter((dayIndex) => Number.isInteger(dayIndex) && dayIndex >= 0 && dayIndex <= 6)
            .sort((left, right) => left - right);
    }

    function setSelectedScheduleDays(days) {
        if (!dom.scheduleDaysButtons) {
            return;
        }
        const selected = new Set(Array.isArray(days) ? days : []);
        dom.scheduleDaysButtons.querySelectorAll('.access-rule-day-btn').forEach((dayBtn) => {
            const dayIndex = Number.parseInt(dayBtn.dataset.dayIndex, 10);
            const isActive = selected.has(dayIndex);
            dayBtn.classList.toggle('active', isActive);
            dayBtn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        });
    }

    function parseScheduleClockTime(value) {
        const match = String(value || '').trim().match(/^(\d{1,2}):(\d{2})$/);
        if (!match) {
            return { hour: 2, minute: 0 };
        }
        return {
            hour: Number.parseInt(match[1], 10),
            minute: Number.parseInt(match[2], 10),
        };
    }

    function formatScheduleClockTime(hour, minute) {
        const safeHour = Math.min(23, Math.max(0, Number(hour) || 0));
        const safeMinute = Math.min(59, Math.max(0, Number(minute) || 0));
        return `${String(safeHour).padStart(2, '0')}:${String(safeMinute).padStart(2, '0')}`;
    }

    function readScheduleTimeValues() {
        const frequency = dom.scheduleFrequencySelect?.value || 'daily';
        if (frequency === 'hourly') {
            return {
                hour: 0,
                minute: Number.parseInt(dom.scheduleHourlyMinuteInput?.value || '0', 10),
            };
        }
        return parseScheduleClockTime(dom.scheduleTimeInput?.value || '02:00');
    }

    function writeScheduleTimeValues({ hour = 2, minute = 0, days = [], frequency } = {}) {
        ensureScheduleDayButtons();
        if (dom.scheduleTimeInput) {
            dom.scheduleTimeInput.value = formatScheduleClockTime(hour, minute);
        }
        if (dom.scheduleHourlyMinuteInput) {
            dom.scheduleHourlyMinuteInput.value = String(minute ?? 0);
        }
        setSelectedScheduleDays(days);
        updateScheduleTimeVisibility(frequency);
    }

    function updateScheduleTimeVisibility(frequency) {
        const selectedFrequency = frequency || dom.scheduleFrequencySelect?.value || 'daily';
        const isHourly = selectedFrequency === 'hourly';
        const isWeekly = selectedFrequency === 'weekly';

        if (dom.scheduleHourlyTimePanel) {
            dom.scheduleHourlyTimePanel.hidden = !isHourly;
        }
        if (dom.scheduleClockTimePanel) {
            dom.scheduleClockTimePanel.hidden = isHourly;
        }
        if (dom.scheduleDaysPanel) {
            dom.scheduleDaysPanel.hidden = !isWeekly;
        }

        if (dom.scheduleTimeDesc) {
            const descriptionKey = isHourly
                ? 'db_schedule_time_hourly_desc'
                : isWeekly
                    ? 'db_schedule_time_weekly_desc'
                    : 'db_schedule_time_daily_desc';
            const descriptionFallback = isHourly
                ? 'Runs every hour at the selected minute.'
                : isWeekly
                    ? 'Runs on the selected weekdays at the selected time.'
                    : 'Runs once per day at the selected time.';
            dom.scheduleTimeDesc.textContent = t(descriptionKey, descriptionFallback);
        }
    }

    function formatScheduleTimeSummary(schedule) {
        const frequency = schedule.frequency || 'daily';
        const timeLabel = formatScheduleClockTime(schedule.hour, schedule.minute);
        if (frequency === 'hourly') {
            return tf('db_schedule_summary_hourly', 'Every hour at :{minute}', {
                minute: String(schedule.minute ?? 0).padStart(2, '0'),
            });
        }
        if (frequency === 'weekly') {
            const daysLabel = formatScheduleDaysList(schedule.days_of_week || []);
            return tf('db_schedule_summary_weekly', '{days} at {time}', {
                days: daysLabel,
                time: timeLabel,
            });
        }
        return tf('db_schedule_summary_daily', 'Daily at {time}', { time: timeLabel });
    }

    function clearDestinationForm() {
        if (dom.destinationEditingId) dom.destinationEditingId.value = '';
        if (dom.destinationNameInput) dom.destinationNameInput.value = '';
        setBackupSelectValue(dom.destinationProviderSelect, 'local');
        if (dom.destinationConfigInput) dom.destinationConfigInput.value = '{}';
        if (dom.destinationEnabledInput) dom.destinationEnabledInput.checked = true;
        if (dom.destinationAdvancedPanel) dom.destinationAdvancedPanel.open = false;
        state.destinationProviderDrafts = {};
        state.destinationProviderBeforeChange = 'local';
        renderDestinationProviderFields();
        clearDestinationValidation();
        updateDestinationSaveAvailability();
    }

    function loadDestinationConfigEditor(provider, config = {}) {
        const normalizedConfig = config && typeof config === 'object' && !Array.isArray(config)
            ? config
            : {};
        const definition = getDestinationProviderDefinition(provider);
        const managedKeys = new Set(definition.fields.map((field) => field.key));
        const managedValues = {};
        const additionalValues = {};

        Object.entries(normalizedConfig).forEach(([key, value]) => {
            if (managedKeys.has(key)) {
                managedValues[key] = value;
            } else {
                additionalValues[key] = value;
            }
        });

        state.destinationProviderDrafts = { [provider]: managedValues };
        state.destinationProviderBeforeChange = provider;
        renderDestinationProviderFields(managedValues);
        if (dom.destinationConfigInput) {
            dom.destinationConfigInput.value = JSON.stringify(additionalValues, null, 2);
        }
        clearDestinationValidation();
        updateDestinationSaveAvailability();
    }

    function clearScheduleForm() {
        if (dom.scheduleEditingId) dom.scheduleEditingId.value = '';
        if (dom.scheduleNameInput) dom.scheduleNameInput.value = '';
        populateScheduleTimezoneSelect('UTC');
        setBackupSelectValue(dom.scheduleFrequencySelect, 'daily');
        writeScheduleTimeValues({ hour: 2, minute: 0, days: [], frequency: 'daily' });
        if (dom.scheduleRetentionCountInput) dom.scheduleRetentionCountInput.value = '30';
        if (dom.scheduleRetentionDaysInput) dom.scheduleRetentionDaysInput.value = '30';
        setBackupSelectValue(dom.scheduleDestinationSelect, '');
        if (dom.scheduleEnabledInput) dom.scheduleEnabledInput.checked = false;
    }

    function setDestinationModalTitle(editing) {
        if (!dom.destinationModalTitle) {
            return;
        }
        dom.destinationModalTitle.textContent = editing
            ? t('db_destination_modal_edit_title', 'Edit Backup Destination')
            : t('db_destination_modal_create_title', 'Add Backup Destination');
    }

    function setScheduleModalTitle(editing) {
        if (!dom.scheduleModalTitle) {
            return;
        }
        dom.scheduleModalTitle.textContent = editing
            ? t('db_schedule_modal_edit_title', 'Edit Backup Schedule')
            : t('db_schedule_modal_create_title', 'Add Backup Schedule');
    }

    function isDestinationModalOpen() {
        return Boolean(dom.destinationOverlay && !dom.destinationOverlay.hidden);
    }

    function isScheduleModalOpen() {
        return Boolean(dom.scheduleOverlay && !dom.scheduleOverlay.hidden);
    }

    function isActionConfirmModalOpen() {
        return Boolean(dom.actionConfirmOverlay && !dom.actionConfirmOverlay.hidden);
    }

    function isBackupNowModalOpen() {
        return Boolean(dom.backupNowOverlay && !dom.backupNowOverlay.hidden);
    }

    function showDatabaseModal(overlay, initialFocus) {
        if (!overlay) {
            return;
        }
        state.modalLastFocusedElements.set(overlay, document.activeElement);
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        initialFocus?.focus?.();
    }

    function hideDatabaseModal(overlay, { restoreFocus = true } = {}) {
        if (!overlay) {
            return;
        }
        overlay.setAttribute('aria-hidden', 'true');
        overlay.hidden = true;
        const previousFocus = state.modalLastFocusedElements.get(overlay);
        state.modalLastFocusedElements.delete(overlay);
        if (restoreFocus) {
            previousFocus?.focus?.();
        }
    }

    function openDestinationModal() {
        if (!dom.destinationOverlay) {
            return;
        }
        showDatabaseModal(dom.destinationOverlay, dom.destinationNameInput);
    }

    function closeDestinationModal() {
        if (!dom.destinationOverlay) {
            return;
        }
        hideDatabaseModal(dom.destinationOverlay);
        clearDestinationForm();
        setDestinationModalTitle(false);
    }

    function openScheduleModal() {
        if (!dom.scheduleOverlay) {
            return;
        }
        showDatabaseModal(dom.scheduleOverlay, dom.scheduleNameInput);
    }

    function closeScheduleModal() {
        if (!dom.scheduleOverlay) {
            return;
        }
        hideDatabaseModal(dom.scheduleOverlay);
        clearScheduleForm();
        setScheduleModalTitle(false);
    }

    function openBackupNowModal() {
        if (!dom.backupNowOverlay) {
            return;
        }
        applyBackupEncryptionCapability();
        if (dom.backupNowSettingsContent?.hidden) {
            showDatabaseModal(dom.backupNowOverlay, dom.backupNowCancelButton);
        } else {
            showDatabaseModal(dom.backupNowOverlay);
            focusBackupSelect(dom.backupNowDestinationSelect);
        }
    }

    function isBackupArchiveModeAvailable(encryptionEnabled) {
        if (encryptionEnabled) {
            return state.backupCapabilities?.archive_encryption_available === true;
        }
        return state.backupCapabilities?.plaintext_archives_allowed === true;
    }

    function applyBackupEncryptionCapability() {
        const encryptionAvailable = !!state.backupCapabilities?.archive_encryption_available;
        const plaintextAllowed = !!state.backupCapabilities?.plaintext_archives_allowed;
        const setupRequired = !encryptionAvailable && !plaintextAllowed;
        const encryptionRequired = encryptionAvailable && !plaintextAllowed;

        // When neither encrypted nor plaintext archives can be created, show a
        // setup-only state instead of a disabled form that looks almost usable.
        if (dom.backupNowSetupRequired) {
            dom.backupNowSetupRequired.hidden = !setupRequired;
        }
        if (dom.backupNowSettingsContent) {
            dom.backupNowSettingsContent.hidden = setupRequired;
        }

        if (dom.backupNowEncryptionEnabled) {
            if (encryptionAvailable) {
                dom.backupNowEncryptionEnabled.checked = encryptionRequired
                    || !!state.backupNowEncryptionPreferred;
                dom.backupNowEncryptionEnabled.disabled = encryptionRequired;
            } else {
                state.backupNowEncryptionPreferred = !!dom.backupNowEncryptionEnabled.checked;
                dom.backupNowEncryptionEnabled.checked = false;
                dom.backupNowEncryptionEnabled.disabled = true;
            }
        }
        if (dom.backupNowEncryptionCapabilityWarning) {
            dom.backupNowEncryptionCapabilityWarning.hidden = encryptionAvailable;
        }
        if (dom.backupNowPlaintextPolicyWarning) {
            dom.backupNowPlaintextPolicyWarning.hidden = !encryptionRequired;
        }
        if (dom.backupNowCreateButton) {
            dom.backupNowCreateButton.disabled = !isBackupArchiveModeAvailable(
                !!dom.backupNowEncryptionEnabled?.checked,
            );
            dom.backupNowCreateButton.hidden = setupRequired;
        }
        if (dom.backupNowCancelButtonText) {
            dom.backupNowCancelButtonText.textContent = setupRequired
                ? t('btn_close', 'Close')
                : t('btn_cancel', 'Cancel');
        }
    }

    async function refreshBackupCapabilities() {
        state.backupCapabilities = await apiJson(`${API_BASE}/capabilities`);
        applyBackupEncryptionCapability();
    }

    function closeBackupNowModal() {
        if (!dom.backupNowOverlay) {
            return;
        }
        hideDatabaseModal(dom.backupNowOverlay);
    }

    function closeActionConfirmModal(confirmed = false) {
        if (dom.actionConfirmOverlay) {
            hideDatabaseModal(dom.actionConfirmOverlay);
        }
        const resolver = state.confirmResolver;
        state.confirmResolver = null;
        if (typeof resolver === 'function') {
            resolver(confirmed);
        }
    }

    function requestActionConfirmation({ title, description, confirmText, confirmKind = 'danger' }) {
        if (
            !dom.actionConfirmOverlay ||
            !dom.actionConfirmPrimaryButton ||
            !dom.actionConfirmTitle ||
            !dom.actionConfirmDescription ||
            !dom.actionConfirmPrimaryText ||
            !dom.actionConfirmCancelButton
        ) {
            if (typeof window.showDeleteConfirm === 'function') {
                return window.showDeleteConfirm({
                    title: title || t('common_delete_confirm_title', 'Delete item?'),
                    message: description || title || t('common_delete_confirm_desc', 'This action cannot be undone.'),
                    confirmLabel: confirmText || t('btn_delete', 'Delete'),
                });
            }
            return Promise.resolve(false);
        }

        dom.actionConfirmTitle.textContent = title || t('common_confirm_action', 'Confirm action');
        dom.actionConfirmDescription.textContent = description || '';
        dom.actionConfirmPrimaryText.textContent = confirmText || t('common_confirm', 'Confirm');
        dom.actionConfirmPrimaryButton.classList.remove('danger', 'submit');
        dom.actionConfirmPrimaryButton.classList.add(confirmKind === 'submit' ? 'submit' : 'danger');

        showDatabaseModal(dom.actionConfirmOverlay, dom.actionConfirmCancelButton);

        return new Promise((resolve) => {
            state.confirmResolver = resolve;
        });
    }

    function populateDestinationSelectors() {
        const options = [
            { value: '', label: t('db_destination_local_default', 'Local storage (server disk)') },
            ...state.destinations.map((destination) => ({
                value: destination.id,
                label: `${destination.name} (${destination.provider})`,
            })),
        ];

        const selectors = [
            dom.backupNowDestinationSelect,
            dom.scheduleDestinationSelect,
        ];

        selectors.forEach((select) => {
            if (!select) {
                return;
            }
            const previous = select.value;
            select.innerHTML = options
                .map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`)
                .join('');
            if (options.some((option) => option.value === previous)) {
                select.value = previous;
            }
            upgradeBackupSelect(select);
        });
    }

    function destinationProviderLabel(provider) {
        const labels = {
            local: ['db_destination_provider_local', 'Local'],
            s3: ['db_destination_provider_s3', 'S3'],
            gcs: ['db_destination_provider_gcs', 'GCS'],
            azure: ['db_destination_provider_azure', 'Azure'],
            webdav: ['db_destination_provider_webdav', 'WebDAV'],
        };
        const [key, fallback] = labels[String(provider || '').toLowerCase()] || [
            'db_destination_provider_local',
            'Local',
        ];
        return t(key, fallback);
    }

    function destinationProviderIcon(provider) {
        const icons = {
            local: sharedIcons.server,
            s3: sharedIcons.database,
            gcs: sharedIcons.globe,
            azure: sharedIcons.database,
            webdav: sharedIcons.server,
        };
        return icons[String(provider || '').toLowerCase()] || sharedIcons.database || '';
    }

    function sanitizeDestinationUrl(rawValue) {
        const value = String(rawValue || '').trim();
        if (!value) {
            return '';
        }

        try {
            const parsed = new URL(value);
            // User information and query parameters can contain credentials.
            // The summary only needs the stable storage endpoint.
            parsed.username = '';
            parsed.password = '';
            parsed.search = '';
            parsed.hash = '';
            return parsed.toString();
        } catch (_) {
            // Saved values should already be validated by the API. This
            // defensive fallback still strips common user-info/query forms.
            return value
                .replace(/^([a-z][a-z0-9+.-]*:\/\/)[^/@\s]+@/i, '$1')
                .split(/[?#]/, 1)[0];
        }
    }

    function destinationHasSavedCredentials(config) {
        if (!config || typeof config !== 'object' || Array.isArray(config)) {
            return false;
        }
        return Object.values(config).some((value) => value === REDACTED_CONFIG_VALUE);
    }

    function destinationSummaryItems(destination) {
        const provider = String(destination.provider || 'local').toLowerCase();
        const config = destination.config && typeof destination.config === 'object'
            ? destination.config
            : {};
        const items = [];
        const add = (labelKey, label, rawValue, options = {}) => {
            if (rawValue === undefined || rawValue === null || rawValue === '') {
                return;
            }
            const value = options.url ? sanitizeDestinationUrl(rawValue) : String(rawValue);
            if (!value) {
                return;
            }
            items.push({
                label: t(labelKey, label),
                value,
                warning: !!options.warning,
            });
        };

        if (provider === 'local') {
            if (config.base_path) {
                add('db_destination_field_base_path', 'Base path', config.base_path);
            } else {
                add(
                    'db_destination_summary_storage',
                    'Storage',
                    t('db_destination_summary_local_server', 'This Omlorix server'),
                );
            }
        } else if (provider === 's3') {
            add('db_destination_field_bucket', 'Bucket', config.bucket);
            add('db_destination_field_prefix', 'Path prefix', config.prefix);
            add('db_destination_field_region', 'Region', config.region);
            add('db_destination_field_endpoint_url', 'Custom endpoint URL', config.endpoint_url, { url: true });
        } else if (provider === 'gcs') {
            add('db_destination_field_bucket', 'Bucket', config.bucket);
            add('db_destination_field_prefix', 'Path prefix', config.prefix);
            add('db_destination_field_project', 'Google Cloud project', config.project);
        } else if (provider === 'azure') {
            add('db_destination_field_container', 'Container', config.container);
            add('db_destination_field_prefix', 'Path prefix', config.prefix);
            add('db_destination_field_account_url', 'Account URL', config.account_url, { url: true });
        } else if (provider === 'webdav') {
            add('db_destination_field_url', 'Server URL', config.url, { url: true });
            add('db_destination_field_prefix', 'Path prefix', config.prefix);
            add('db_destination_field_username', 'Username', config.username);
            add(
                'db_destination_field_verify_ssl',
                'Verify TLS certificate',
                config.verify_ssl === false
                    ? t('db_destination_tls_unverified', 'Certificate verification off')
                    : t('db_destination_tls_verified', 'Certificate verification on'),
                { warning: config.verify_ssl === false },
            );
            add(
                'db_destination_field_timeout',
                'Timeout',
                tf('db_destination_timeout_seconds', '{count} seconds', {
                    count: config.timeout ?? 30,
                }),
            );
        }

        if (destinationHasSavedCredentials(config)) {
            add(
                'db_destination_summary_credentials',
                'Credentials',
                t('db_destination_summary_credentials_configured', 'Configured'),
            );
        }

        return items;
    }

    function renderDestinationSummary(destination) {
        return destinationSummaryItems(destination)
            .map((item) => `
                <div class="db-destination-summary-item${item.warning ? ' is-warning' : ''}">
                    <dt>${escapeHtml(item.label)}</dt>
                    <dd title="${escapeHtml(item.value)}">${escapeHtml(item.value)}</dd>
                </div>
            `)
            .join('');
    }

    function renderDestinationSecurityWarning(destination) {
        if (
            String(destination.provider || '').toLowerCase() !== 'webdav'
            || destination.config?.verify_ssl !== false
        ) {
            return '';
        }
        return `
            <p class="db-destination-security-warning">
                ${actionIcon('warning')}
                <span>${escapeHtml(t(
                    'db_destination_tls_warning',
                    'TLS certificate verification is off. Use this only for a server you trust.',
                ))}</span>
            </p>
        `;
    }

    function renderDestinationTestResult(destinationId) {
        const result = state.destinationTestResults.get(String(destinationId));
        if (!result) {
            return '';
        }

        if (result.status === 'testing') {
            return `
                <div class="db-destination-test-result" role="status" aria-live="polite">
                    ${actionIcon('test')}
                    <span>${escapeHtml(t('db_destination_test_in_progress', 'Testing connection…'))}</span>
                </div>
            `;
        }

        const isSuccess = result.status === 'success';
        const message = isSuccess
            ? t('db_destination_test_verified', 'Connection verified')
            : (
                result.message
                || localizedDestinationTestFailure(result.errorCode)
            );
        const timestamp = result.testedAt ? formatDate(result.testedAt) : '';
        return `
            <div class="db-destination-test-result ${isSuccess ? 'is-success' : 'is-error'}" role="${isSuccess ? 'status' : 'alert'}" aria-live="polite">
                ${actionIcon(isSuccess ? 'test' : 'warning')}
                <span>
                    ${escapeHtml(message)}
                    ${timestamp ? ` · <time datetime="${escapeHtml(result.testedAt)}">${escapeHtml(timestamp)}</time>` : ''}
                </span>
            </div>
        `;
    }

    function renderDestinationActions(destination) {
        const destinationId = String(destination.id);
        const testing = state.destinationTestsInProgress.has(destinationId);
        const deleteLabel = t('db_action_delete', 'Delete');
        return `
            <div class="db-destination-card-actions">
                ${renderActionButton({
                    action: 'test',
                    idName: 'destination',
                    id: destinationId,
                    icon: 'test',
                    label: testing
                        ? t('db_destination_test_in_progress', 'Testing connection…')
                        : t('db_action_test', 'Test'),
                    loading: testing,
                    className: 'db-destination-card-action',
                })}
                ${renderActionButton({
                    action: 'edit',
                    idName: 'destination',
                    id: destinationId,
                    icon: 'edit',
                    label: t('db_action_edit', 'Edit'),
                    disabled: testing,
                    className: 'db-destination-card-action',
                })}
                ${renderActionButton({
                    action: 'delete',
                    idName: 'destination',
                    id: destinationId,
                    kind: 'danger-nofill',
                    icon: 'delete',
                    label: deleteLabel,
                    disabled: testing,
                    className: 'db-destination-delete-action',
                    iconOnly: true,
                    title: deleteLabel,
                    ariaLabel: deleteLabel,
                })}
            </div>
        `;
    }

    function renderDestinations() {
        if (!dom.destinationList) {
            return;
        }
        if (!state.destinations.length) {
            dom.destinationList.innerHTML = renderEmptyState(t('db_destinations_empty', 'No backup destinations configured yet.'));
            return;
        }

        dom.destinationList.innerHTML = state.destinations
            .map((destination, index) => {
                const titleId = `backup-destination-${index}-title`;
                const providerLabel = destinationProviderLabel(destination.provider);
                const activeLabel = destination.enabled
                    ? t('db_destination_status_active', 'Active')
                    : t('db_destination_status_inactive', 'Inactive');
                return `
                    <article class="db-destination-card" data-destination-id="${escapeHtml(destination.id)}" aria-labelledby="${titleId}">
                        <header class="db-destination-card-header">
                            <div class="db-destination-identity">
                                <span class="db-destination-provider-icon" aria-hidden="true">
                                    ${destinationProviderIcon(destination.provider)}
                                </span>
                                <div class="db-destination-title-group">
                                    <h4 class="db-destination-name" id="${titleId}" title="${escapeHtml(destination.name)}">${escapeHtml(destination.name)}</h4>
                                    <div class="db-destination-badges">
                                        <span class="db-destination-provider-badge">${escapeHtml(providerLabel)}</span>
                                        <span class="db-destination-status ${destination.enabled ? 'is-active' : 'is-inactive'}">
                                            <span class="db-destination-status-dot" aria-hidden="true"></span>
                                            ${escapeHtml(activeLabel)}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </header>
                        <dl class="db-destination-summary">
                            ${renderDestinationSummary(destination)}
                        </dl>
                        ${renderDestinationSecurityWarning(destination)}
                        ${renderDestinationTestResult(destination.id)}
                        ${renderDestinationActions(destination)}
                    </article>
                `;
            })
            .join('');
    }

    function renderSchedules() {
        if (!dom.scheduleList) {
            return;
        }
        if (!state.schedules.length) {
            dom.scheduleList.innerHTML = renderEmptyState(t('db_schedules_empty', 'No backup schedules configured yet.'));
            return;
        }

        const destinationMap = new Map(state.destinations.map((destination) => [destination.id, destination]));
        dom.scheduleList.innerHTML = state.schedules
            .map((schedule) => {
                const destination = schedule.destination_id ? destinationMap.get(schedule.destination_id) : null;
                const destinationLabel = destination ? `${destination.name} (${destination.provider})` : t('db_destination_local_default', 'Local storage (server disk)');
                return `
                    <div class="settings-row db-list-item" data-schedule-id="${escapeHtml(schedule.id)}">
                        <div class="settings-row-left">
                            <p class="settings-row-title">${escapeHtml(schedule.name)}</p>
                            <p class="settings-row-desc">${escapeHtml(t('db_destination_enabled_value', 'Enabled'))}: <span class="pill ${schedule.enabled ? 'success' : 'warning'}">${escapeHtml(schedule.enabled ? t('common_true', 'True') : t('common_false', 'False'))}</span></p>
                            <p class="settings-row-desc">${escapeHtml(formatScheduleTimeSummary(schedule))} (${escapeHtml(schedule.timezone)})</p>
                            ${schedule.frequency === 'weekly'
                                ? `<p class="settings-row-desc">${escapeHtml(t('db_schedule_days_label', 'Days'))}: ${escapeHtml(formatScheduleDaysList(schedule.days_of_week || []))}</p>`
                                : ''}
                            <p class="settings-row-desc">${escapeHtml(t('db_schedule_retention_label', 'Retention'))}: count=${escapeHtml(schedule.retention_count ?? '-')} days=${escapeHtml(schedule.retention_days ?? '-')}</p>
                            <p class="settings-row-desc">${escapeHtml(t('db_schedule_destination_label', 'Destination'))}: ${escapeHtml(destinationLabel)}</p>
                            <p class="settings-row-desc">${escapeHtml(t('db_schedule_last_run_label', 'Last run'))}: ${escapeHtml(formatDate(schedule.last_run_at))}</p>
                        </div>
                        <div class="settings-row-right db-action-grid">
                            ${renderActionButton({ action: 'edit', idName: 'schedule', id: schedule.id, icon: 'edit', label: t('db_action_edit', 'Edit') })}
                            ${renderActionButton({ action: 'run-now', idName: 'schedule', id: schedule.id, icon: 'run', label: t('db_action_run_now', 'Run now') })}
                            ${renderActionButton({ action: 'delete', idName: 'schedule', id: schedule.id, kind: 'danger', icon: 'delete', label: t('db_action_delete', 'Delete') })}
                        </div>
                    </div>
                `;
            })
            .join('');
    }

    function renderJobs() {
        if (!dom.jobsList) {
            return;
        }
        if (!state.jobs.length) {
            dom.jobsList.innerHTML = renderEmptyState(t('db_history_empty', 'No backup jobs found.'));
            renderJobsPagination();
            return;
        }

        dom.jobsList.innerHTML = state.jobs
            .map((job) => {
                const artifact = Array.isArray(job.artifacts) && job.artifacts.length ? job.artifacts[0] : null;
                const hasArtifact = !!artifact?.id;
                const isVerifying = state.jobVerificationsInProgress.has(String(job.id));
                return `
                    <div class="settings-row db-list-item" data-job-id="${escapeHtml(job.id)}">
                        <div class="settings-row-left">
                            <p class="settings-row-title db-entity-id">${escapeHtml(job.id)}</p>
                            <p class="settings-row-desc">${escapeHtml(t('db_job_status_label', 'Status'))}: <span class="pill ${statusPillClass(job.status)}">${escapeHtml(job.status || '-')}</span> · ${escapeHtml(job.trigger_type || '-')}</p>
                            <p class="settings-row-desc">${escapeHtml(t('db_job_created_label', 'Created'))}: ${escapeHtml(formatDate(job.created_at))}</p>
                            <p class="settings-row-desc">${escapeHtml(t('db_job_size_label', 'Size'))}: ${escapeHtml(job.size_bytes ?? artifact?.bytes ?? '-')}</p>
                            <p class="settings-row-desc">${escapeHtml(t('db_job_artifact_label', 'Artifact'))}: <code>${escapeHtml(artifact?.id || '-')}</code> · ${escapeHtml(formatBackupStorage(artifact?.storage))}</p>
                            ${job.error ? `<p class="settings-row-desc">${escapeHtml(t('db_job_error_label', 'Error'))}: ${escapeHtml(job.error)}</p>` : ''}
                        </div>
                        <div class="settings-row-right db-action-grid db-action-grid--history">
                            ${renderActionButton({ action: 'verify', idName: 'job', id: job.id, icon: 'verify', label: isVerifying ? t('db_busy_verifying_backup', 'Verifying backup…') : t('db_action_verify', 'Verify'), disabled: !hasArtifact, loading: isVerifying })}
                            ${renderNativeDownloadLink({ id: job.id, label: t('db_action_download', 'Download'), disabled: !hasArtifact })}
                            ${renderActionButton({ action: 'delete', idName: 'job', id: job.id, kind: 'danger', icon: 'delete', label: t('db_action_delete', 'Delete') })}
                        </div>
                    </div>
                `;
            })
            .join('');
        renderJobsPagination();
    }

    function renderJobsPagination() {
        if (!dom.jobsPagination || !dom.jobsPaginationPages) {
            return;
        }

        dom.jobsPagination.hidden = state.jobsTotalPages <= 1;
        if (dom.jobsPagination.hidden) {
            dom.jobsPaginationPages.replaceChildren();
            return;
        }

        const start = (state.jobsPage - 1) * state.jobsPageSize + 1;
        const end = Math.min(state.jobsPage * state.jobsPageSize, state.jobsTotal);
        if (dom.jobsPaginationInfo) {
            dom.jobsPaginationInfo.textContent = tf(
                'db_history_pagination_showing',
                'Showing {start}–{end} of {total} backups',
                { start, end, total: state.jobsTotal },
            );
        }
        if (dom.jobsPaginationPrev) {
            dom.jobsPaginationPrev.disabled = state.jobsLoading || state.jobsPage <= 1;
        }
        if (dom.jobsPaginationNext) {
            dom.jobsPaginationNext.disabled = state.jobsLoading || state.jobsPage >= state.jobsTotalPages;
        }

        dom.jobsPaginationPages.replaceChildren();
        generateBackupHistoryPageNumbers(state.jobsPage, state.jobsTotalPages).forEach((page) => {
            if (page === '…') {
                const ellipsis = document.createElement('span');
                ellipsis.className = 'user-notifications-pagination-ellipsis';
                ellipsis.textContent = '…';
                ellipsis.setAttribute('aria-hidden', 'true');
                dom.jobsPaginationPages.appendChild(ellipsis);
                return;
            }

            const button = document.createElement('button');
            button.type = 'button';
            button.className = `user-notifications-pagination-page${page === state.jobsPage ? ' active' : ''}`;
            button.dataset.backupJobsPage = String(page);
            button.textContent = String(page);
            button.setAttribute(
                'aria-label',
                tf('db_history_page_aria', 'Page {page}', { page }),
            );
            button.disabled = state.jobsLoading;
            if (page === state.jobsPage) {
                button.setAttribute('aria-current', 'page');
                button.disabled = true;
            }
            dom.jobsPaginationPages.appendChild(button);
        });
    }

    async function goToBackupJobsPage(page) {
        if (
            state.jobsLoading
            || !Number.isInteger(page)
            || page < 1
            || page > state.jobsTotalPages
            || page === state.jobsPage
        ) {
            return;
        }
        state.jobsPage = page;
        try {
            await refreshJobs({ scrollToList: true });
        } catch (error) {
            // refreshJobs renders the inline empty state; pagination callers
            // also need a visible global failure instead of an unhandled
            // rejection from their async DOM event handlers.
            console.error('backup history page load failed', error);
            setStatus(
                'error',
                error?.message || t(
                    'db_history_load_failed',
                    'Failed to load backup history. Please try again.',
                ),
            );
        }
    }

    async function refreshDestinations() {
        state.destinations = await apiJson(`${API_BASE}/destinations`);
        const destinationIds = new Set(state.destinations.map((destination) => String(destination.id)));
        for (const destinationId of state.destinationTestResults.keys()) {
            if (!destinationIds.has(destinationId)) {
                state.destinationTestResults.delete(destinationId);
            }
        }
        populateDestinationSelectors();
        renderDestinations();
    }

    async function refreshSchedules() {
        state.schedules = await apiJson(`${API_BASE}/schedules`);
        renderSchedules();
    }

    async function refreshJobs({ resetPage = false, scrollToList = false } = {}) {
        if (!dom.jobsList) {
            return false;
        }
        if (resetPage) {
            state.jobsPage = 1;
        }

        state.jobsRequestController?.abort();
        const controller = new AbortController();
        const requestSequence = state.jobsRequestSequence + 1;
        state.jobsRequestController = controller;
        state.jobsRequestSequence = requestSequence;
        state.jobsLoading = true;
        dom.jobsList.setAttribute('aria-busy', 'true');
        dom.jobsList.classList.add('is-page-loading');

        // Keep the current cards mounted while another page is fetched. This
        // prevents the scrollable admin content from shrinking to the height
        // of one loading row and then expanding again when results arrive.
        if (!state.jobs.length) {
            dom.jobsList.innerHTML = `
                <div class="provider-empty-state" role="status">
                    <p>${escapeHtml(t('db_history_loading', 'Loading backup history…'))}</p>
                </div>
            `;
        }
        renderJobsPagination();
        if (scrollToList) {
            if (typeof window.scrollAdminPaginatedListToStart === 'function') {
                window.scrollAdminPaginatedListToStart(dom.jobsList);
            } else {
                dom.jobsList.scrollIntoView({ block: 'start' });
            }
        }

        try {
            const response = await apiJson(
                buildBackupJobsPageUrl(state.jobsPage, state.jobsPageSize),
                { signal: controller.signal },
            );
            if (requestSequence !== state.jobsRequestSequence) {
                return false;
            }

            state.jobs = Array.isArray(response?.items) ? response.items : [];
            state.jobsPage = Number(response?.page) || 1;
            state.jobsPageSize = Number(response?.page_size) || BACKUP_HISTORY_PAGE_SIZE;
            state.jobsTotal = Number(response?.total) || 0;
            state.jobsTotalPages = Number(response?.total_pages) || 0;
            state.jobsLoading = false;
            renderJobs();
            return true;
        } catch (error) {
            if (error?.name === 'AbortError') {
                return false;
            }
            if (requestSequence !== state.jobsRequestSequence) {
                return false;
            }

            // Do not let a later language update re-render stale cards as
            // though they belonged to the page whose request just failed.
            state.jobs = [];
            state.jobsTotal = 0;
            state.jobsTotalPages = 0;
            dom.jobsList.innerHTML = renderEmptyState(
                t('db_history_load_failed', 'Failed to load backup history. Please try again.'),
            );
            renderJobsPagination();
            throw error;
        } finally {
            if (requestSequence === state.jobsRequestSequence) {
                state.jobsLoading = false;
                dom.jobsList.setAttribute('aria-busy', 'false');
                dom.jobsList.classList.remove('is-page-loading');
                state.jobsRequestController = null;
            }
        }
    }

    async function refreshAll({ isManualRefresh = false } = {}) {
        if (state.refreshInProgress || (isManualRefresh && state.refreshCooldown)) {
            return;
        }

        state.refreshInProgress = true;
        clearStatus();
        if (isManualRefresh) {
            setPageRefreshLoading(true);
        }

        try {
            // The header refresh replaces the old section refresh buttons, so it
            // reloads every data set shown on the database backup page.
            await Promise.all([
                refreshDestinations(),
                refreshSchedules(),
                refreshJobs(),
                refreshBackupCapabilities(),
            ]);
            if (isManualRefresh) {
                state.refreshCooldown = true;
                showPageRefreshSuccess();
            }
        } catch (error) {
            console.error('database refresh failed', error);
            state.refreshCooldown = false;
            setStatus('error', error?.message || t('db_refresh_failed', 'Failed to refresh backup data.'));
            if (isManualRefresh) {
                resetPageRefreshButton();
            }
        } finally {
            state.refreshInProgress = false;
            if (isManualRefresh) {
                setPageRefreshLoading(false);
            }
        }
    }

    async function handleCreateBackupNow() {
        const encryptionEnabled = !!dom.backupNowEncryptionEnabled?.checked;
        if (!isBackupArchiveModeAvailable(encryptionEnabled)) {
            // Capabilities can change after the dialog opens, and DOM state can
            // be altered outside the supported controls. Reapply the effective
            // server policy instead of sending an impossible request.
            applyBackupEncryptionCapability();
            return;
        }

        try {
            setBusy(dom.backupNowCreateButton, true, t('db_busy_creating_backup', 'Creating backup…'));
            const payload = {
                destination_id: dom.backupNowDestinationSelect?.value || null,
                encryption_enabled: encryptionEnabled,
            };
            const created = await apiJson(`${API_BASE}/create`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            setStatus('success', `${t('db_backup_create_status_prefix', 'Backup job queued')}: ${created.id}`);
            closeBackupNowModal();
            // A newly queued job sorts to the top of history, so return to the
            // first server page instead of leaving the admin on an older page.
            await refreshJobs({ resetPage: true });
        } catch (error) {
            console.error('create backup failed', error);
            setStatus('error', error?.message || t('db_backup_create_failed', 'Failed to create backup job.'));
        } finally {
            setBusy(dom.backupNowCreateButton, false);
            applyBackupEncryptionCapability();
        }
    }

    async function handleSaveDestination() {
        if (state.destinationSaveInProgress) {
            return;
        }

        const validation = validateDestinationForm();
        if (!validation.valid) {
            return;
        }

        state.destinationSaveInProgress = true;
        updateDestinationSaveAvailability();
        try {
            setBusy(dom.destinationSaveButton, true, t('db_busy_saving', 'Saving…'));
            const destinationId = dom.destinationEditingId?.value || '';
            const payload = {
                name: (dom.destinationNameInput?.value || '').trim(),
                provider: dom.destinationProviderSelect?.value || 'local',
                config: validation.config,
                enabled: !!dom.destinationEnabledInput?.checked,
            };

            if (destinationId) {
                await apiJson(`${API_BASE}/destinations/${encodeURIComponent(destinationId)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            } else {
                await apiJson(`${API_BASE}/destinations`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            }

            if (destinationId) {
                // A successful connection result belongs to the previous
                // configuration and must not survive an edit.
                state.destinationTestResults.delete(String(destinationId));
            }
            window.notifySuccess?.(t('db_destination_save_success', 'Destination saved.'));
            closeDestinationModal();
            await refreshDestinations();
        } catch (error) {
            console.error('save destination failed', error);
            const message = error?.message || t('db_destination_save_failed', 'Failed to save destination.');
            setDestinationFormStatus(message);
            setStatus('error', message);
        } finally {
            state.destinationSaveInProgress = false;
            setBusy(dom.destinationSaveButton, false);
            updateDestinationSaveAvailability();
        }
    }

    async function testDestinationConnection(destinationId) {
        const normalizedId = String(destinationId || '');
        if (!normalizedId || state.destinationTestsInProgress.has(normalizedId)) {
            return;
        }

        state.destinationTestsInProgress.add(normalizedId);
        state.destinationTestResults.set(normalizedId, { status: 'testing' });
        renderDestinations();

        try {
            const result = await apiJson(`${API_BASE}/destinations/${encodeURIComponent(normalizedId)}/test`, {
                method: 'POST',
            });
            const testedAt = new Date().toISOString();
            if (String(result?.status || '').toLowerCase() !== 'success') {
                const errorCode = result?.details?.error_code;
                const message = localizedDestinationTestFailure(errorCode);
                state.destinationTestResults.set(normalizedId, {
                    status: 'error',
                    errorCode,
                    testedAt,
                });
                setStatus('error', message);
                return;
            }

            state.destinationTestResults.set(normalizedId, {
                status: 'success',
                testedAt,
            });
            const destinationName = state.destinations.find(
                (destination) => String(destination.id) === normalizedId,
            )?.name;
            const successMessage = destinationName
                ? tf(
                    'db_destination_test_success_named',
                    'Connection to “{name}” verified.',
                    { name: destinationName },
                )
                : t('db_destination_test_success', 'Destination test succeeded.');
            window.notifySuccess?.(successMessage);
        } catch (error) {
            const message = error?.message || t(
                'db_destination_test_failed_detail',
                'The connection test failed. Check the destination configuration and the destination server logs.',
            );
            state.destinationTestResults.set(normalizedId, {
                status: 'error',
                message,
                testedAt: new Date().toISOString(),
            });
            setStatus('error', message);
        } finally {
            state.destinationTestsInProgress.delete(normalizedId);
            renderDestinations();
        }
    }

    async function handleDestinationAction(event) {
        const button = event.target.closest('[data-destination-action]');
        if (!button) {
            return;
        }

        const action = button.dataset.destinationAction;
        const destinationId = button.dataset.destinationId;
        if (!destinationId) {
            return;
        }

        const selected = state.destinations.find((destination) => destination.id === destinationId);
        if (!selected) {
            return;
        }

        try {
            if (action === 'edit') {
                if (dom.destinationEditingId) dom.destinationEditingId.value = selected.id;
                if (dom.destinationNameInput) dom.destinationNameInput.value = selected.name || '';
                setBackupSelectValue(dom.destinationProviderSelect, selected.provider || 'local');
                if (dom.destinationEnabledInput) dom.destinationEnabledInput.checked = !!selected.enabled;
                loadDestinationConfigEditor(selected.provider || 'local', selected.config || {});
                setDestinationModalTitle(true);
                openDestinationModal();
                return;
            }

            if (action === 'test') {
                await testDestinationConnection(destinationId);
                return;
            }

            if (action === 'delete') {
                const confirmed = await requestActionConfirmation({
                    title: t('db_destination_delete_modal_title', 'Delete Backup Destination?'),
                    description: t('db_destination_delete_confirm', 'Delete this backup destination?'),
                    confirmText: t('db_action_delete', 'Delete'),
                    confirmKind: 'danger',
                });
                if (!confirmed) {
                    return;
                }
                await apiJson(`${API_BASE}/destinations/${encodeURIComponent(destinationId)}`, {
                    method: 'DELETE',
                });
                state.destinationTestResults.delete(String(destinationId));
                window.notifySuccess?.(t('db_destination_delete_success', 'Destination deleted.'));
                if (dom.destinationEditingId?.value === destinationId) {
                    clearDestinationForm();
                }
                await refreshDestinations();
            }
        } catch (error) {
            console.error('destination action failed', error);
            setStatus('error', error?.message || t('db_destination_action_failed', 'Destination action failed.'));
        }
    }

    async function handleSaveSchedule() {
        try {
            setBusy(dom.scheduleSaveButton, true, t('db_busy_saving', 'Saving…'));
            const scheduleId = dom.scheduleEditingId?.value || '';
            const frequency = dom.scheduleFrequencySelect?.value || 'daily';
            const { hour, minute } = readScheduleTimeValues();
            const daysOfWeek = frequency === 'weekly' ? getSelectedScheduleDays() : [];
            const payload = {
                name: (dom.scheduleNameInput?.value || '').trim(),
                enabled: !!dom.scheduleEnabledInput?.checked,
                timezone: (dom.scheduleTimezoneSelect?.value || 'UTC').trim() || 'UTC',
                frequency,
                minute,
                hour,
                days_of_week: daysOfWeek,
                retention_count: dom.scheduleRetentionCountInput?.value ? Number.parseInt(dom.scheduleRetentionCountInput.value, 10) : null,
                retention_days: dom.scheduleRetentionDaysInput?.value ? Number.parseInt(dom.scheduleRetentionDaysInput.value, 10) : null,
                destination_id: dom.scheduleDestinationSelect?.value || null,
            };

            if (!payload.name) {
                throw new Error(t('db_schedule_name_required', 'Schedule name is required.'));
            }
            if (!Number.isInteger(payload.minute) || payload.minute < 0 || payload.minute > 59) {
                throw new Error(t('db_schedule_minute_invalid', 'Minute must be between 0 and 59.'));
            }
            if (!Number.isInteger(payload.hour) || payload.hour < 0 || payload.hour > 23) {
                throw new Error(t('db_schedule_hour_invalid', 'Hour must be between 0 and 23.'));
            }
            if (frequency === 'weekly' && !daysOfWeek.length) {
                throw new Error(t('db_schedule_weekly_days_required', 'Select at least one weekday for weekly schedules.'));
            }

            if (scheduleId) {
                await apiJson(`${API_BASE}/schedules/${encodeURIComponent(scheduleId)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            } else {
                await apiJson(`${API_BASE}/schedules`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            }

            window.notifySuccess?.(t('db_schedule_save_success', 'Schedule saved.'));
            closeScheduleModal();
            await refreshSchedules();
        } catch (error) {
            console.error('save schedule failed', error);
            setStatus('error', error?.message || t('db_schedule_save_failed', 'Failed to save schedule.'));
        } finally {
            setBusy(dom.scheduleSaveButton, false);
        }
    }

    async function handleScheduleAction(event) {
        const button = event.target.closest('[data-schedule-action]');
        if (!button) {
            return;
        }
        const action = button.dataset.scheduleAction;
        const scheduleId = button.dataset.scheduleId;
        if (!scheduleId) {
            return;
        }

        const selected = state.schedules.find((schedule) => schedule.id === scheduleId);
        if (!selected) {
            return;
        }

        try {
            if (action === 'edit') {
                if (dom.scheduleEditingId) dom.scheduleEditingId.value = selected.id;
                if (dom.scheduleNameInput) dom.scheduleNameInput.value = selected.name || '';
                populateScheduleTimezoneSelect(selected.timezone || getBrowserTimeZone());
                setBackupSelectValue(dom.scheduleFrequencySelect, selected.frequency || 'daily');
                writeScheduleTimeValues({
                    hour: selected.hour ?? 2,
                    minute: selected.minute ?? 0,
                    days: selected.days_of_week || [],
                    frequency: selected.frequency || 'daily',
                });
                if (dom.scheduleRetentionCountInput) dom.scheduleRetentionCountInput.value = selected.retention_count ?? '';
                if (dom.scheduleRetentionDaysInput) dom.scheduleRetentionDaysInput.value = selected.retention_days ?? '';
                setBackupSelectValue(dom.scheduleDestinationSelect, selected.destination_id || '');
                if (dom.scheduleEnabledInput) dom.scheduleEnabledInput.checked = !!selected.enabled;
                setScheduleModalTitle(true);
                openScheduleModal();
                return;
            }

            if (action === 'run-now') {
                await apiJson(`${API_BASE}/schedules/${encodeURIComponent(scheduleId)}/run-now`, {
                    method: 'POST',
                });
                window.notifySuccess?.(t('db_schedule_run_now_success', 'Schedule run triggered.'));
                // Running a schedule creates a newest-first history entry.
                await refreshJobs({ resetPage: true });
                return;
            }

            if (action === 'delete') {
                const confirmed = await requestActionConfirmation({
                    title: t('db_schedule_delete_modal_title', 'Delete Backup Schedule?'),
                    description: t('db_schedule_delete_confirm', 'Delete this backup schedule?'),
                    confirmText: t('db_action_delete', 'Delete'),
                    confirmKind: 'danger',
                });
                if (!confirmed) {
                    return;
                }
                await apiJson(`${API_BASE}/schedules/${encodeURIComponent(scheduleId)}`, {
                    method: 'DELETE',
                });
                window.notifySuccess?.(t('db_schedule_delete_success', 'Schedule deleted.'));
                if (dom.scheduleEditingId?.value === scheduleId) {
                    clearScheduleForm();
                }
                await refreshSchedules();
            }
        } catch (error) {
            console.error('schedule action failed', error);
            setStatus('error', error?.message || t('db_schedule_action_failed', 'Schedule action failed.'));
        }
    }

    async function handleJobAction(event) {
        const nativeDownloadLink = event.target.closest('[data-native-backup-download]');
        if (nativeDownloadLink) {
            // The second, programmatic click is the actual native navigation.
            // Let it proceed without recursively running the HEAD preflight.
            if (nativeDownloadLink.dataset.downloadReady === 'true') {
                delete nativeDownloadLink.dataset.downloadReady;
                return;
            }

            event.preventDefault();
            const jobId = nativeDownloadLink.dataset.jobId;
            if (!jobId || state.jobDownloadsInProgress.has(jobId)) {
                return;
            }

            state.jobDownloadsInProgress.add(jobId);
            nativeDownloadLink.setAttribute('aria-disabled', 'true');
            setBusy(nativeDownloadLink, true, t('db_busy_downloading', 'Downloading…'));
            try {
                // A HEAD request goes through the normal authenticated fetch
                // path, including an access-token refresh and retry after 401.
                // The backend also materializes remote artifacts here, so any
                // storage failure can be shown before native navigation starts.
                const response = await window.authedFetch(nativeDownloadLink.href, {
                    method: 'HEAD',
                });
                if (!response.ok) {
                    throw new Error(await parseError(
                        response,
                        t('db_job_download_failed', 'Failed to download backup artifact.'),
                    ));
                }

                nativeDownloadLink.dataset.downloadReady = 'true';
                nativeDownloadLink.click();
                window.notifySuccess?.(t(
                    'db_job_download_success',
                    "Download started. Track its progress in your browser's downloads.",
                ));
            } catch (error) {
                console.error('download backup failed', error);
                setStatus('error', error?.message || t(
                    'db_job_download_failed',
                    'Failed to download backup artifact.',
                ));
            } finally {
                delete nativeDownloadLink.dataset.downloadReady;
                state.jobDownloadsInProgress.delete(jobId);
                nativeDownloadLink.removeAttribute('aria-disabled');
                setBusy(nativeDownloadLink, false);
            }
            return;
        }

        const button = event.target.closest('[data-job-action]');
        if (!button) {
            return;
        }
        const action = button.dataset.jobAction;
        const jobId = button.dataset.jobId;
        if (!jobId) {
            return;
        }

        try {
            if (action === 'verify') {
                // Verification can take long enough for a second click or a
                // page refresh. The per-job guard prevents duplicate requests,
                // while setBusy provides immediate visual and accessible
                // feedback on the currently rendered button.
                if (state.jobVerificationsInProgress.has(jobId)) {
                    return;
                }
                state.jobVerificationsInProgress.add(jobId);
                setBusy(button, true, t('db_busy_verifying_backup', 'Verifying backup…'));
                try {
                    await apiJson(`${API_BASE}/jobs/${encodeURIComponent(jobId)}/verify`, {
                        method: 'POST',
                    });
                    window.notifySuccess?.(t('db_job_verify_success', 'Backup verification completed.'));
                    await refreshJobs();
                } finally {
                    state.jobVerificationsInProgress.delete(jobId);
                    if (button.isConnected) {
                        setBusy(button, false);
                    } else {
                        renderJobs();
                    }
                }
                return;
            }

            if (action === 'delete') {
                const confirmed = await requestActionConfirmation({
                    title: t('db_job_delete_modal_title', 'Delete Backup Job?'),
                    description: t('db_job_delete_confirm', 'Delete this backup job metadata and remote artifact?'),
                    confirmText: t('db_action_delete', 'Delete'),
                    confirmKind: 'danger',
                });
                if (!confirmed) {
                    return;
                }
                await apiJson(`${API_BASE}/jobs/${encodeURIComponent(jobId)}?delete_remote=true`, {
                    method: 'DELETE',
                });
                window.notifySuccess?.(t('db_job_delete_success', 'Backup job deleted.'));
                await refreshJobs();
            }
        } catch (error) {
            console.error('job action failed', error);
            setStatus('error', error?.message || t('db_job_action_failed', 'Backup job action failed.'));
        }
    }

    function registerEscapeHandlers() {
        if (typeof window.registerEscapeHandler !== 'function') {
            return;
        }

        window.registerEscapeHandler({
            id: 'backupDestinationOverlay',
            priority: 100,
            isActive: isDestinationModalOpen,
            close: closeDestinationModal,
        });

        window.registerEscapeHandler({
            id: 'backupScheduleOverlay',
            priority: 101,
            isActive: isScheduleModalOpen,
            close: closeScheduleModal,
        });

        window.registerEscapeHandler({
            id: 'databaseActionConfirmOverlay',
            priority: 102,
            isActive: isActionConfirmModalOpen,
            close: () => closeActionConfirmModal(false),
        });

        window.registerEscapeHandler({
            id: 'backupNowOverlay',
            priority: 103,
            isActive: isBackupNowModalOpen,
            close: closeBackupNowModal,
        });
    }

    function bindEventHandlers() {
        dom.openBackupNowModalButton?.addEventListener('click', openBackupNowModal);
        dom.backupNowCancelButton?.addEventListener('click', closeBackupNowModal);
        dom.backupNowOverlay?.addEventListener('click', (event) => {
            if (event.target === dom.backupNowOverlay) {
                closeBackupNowModal();
            }
        });
        dom.backupNowCreateButton?.addEventListener('click', handleCreateBackupNow);
        dom.backupNowEncryptionEnabled?.addEventListener('change', () => {
            if (!dom.backupNowEncryptionEnabled?.disabled) {
                state.backupNowEncryptionPreferred = !!dom.backupNowEncryptionEnabled.checked;
            }
        });

        dom.openDestinationModalButton?.addEventListener('click', () => {
            clearDestinationForm();
            setDestinationModalTitle(false);
            openDestinationModal();
        });
        dom.destinationCancelButton?.addEventListener('click', closeDestinationModal);
        dom.destinationOverlay?.addEventListener('click', (event) => {
            if (event.target === dom.destinationOverlay) {
                closeDestinationModal();
            }
        });

        dom.destinationSaveButton?.addEventListener('click', handleSaveDestination);
        dom.destinationList?.addEventListener('click', handleDestinationAction);
        dom.destinationProviderSelect?.addEventListener('change', () => {
            captureDestinationProviderDraft();
            const provider = dom.destinationProviderSelect?.value || 'local';
            renderDestinationProviderFields(state.destinationProviderDrafts[provider] || {});
            clearDestinationValidation();
        });
        dom.destinationNameInput?.addEventListener('input', () => {
            setDestinationFieldError(dom.destinationNameInput, '');
            setDestinationFormStatus('');
        });
        dom.destinationConfigInput?.addEventListener('input', () => {
            parseAdditionalConfig({ showError: true });
            setDestinationFormStatus('');
            updateDestinationSaveAvailability();
        });
        dom.destinationFormatJsonButton?.addEventListener('click', () => {
            const parsed = parseAdditionalConfig({ showError: true });
            if (!parsed.valid) {
                dom.destinationConfigInput?.focus();
                return;
            }
            if (dom.destinationConfigInput) {
                dom.destinationConfigInput.value = JSON.stringify(parsed.value, null, 2);
            }
            updateDestinationSaveAvailability();
        });
        dom.destinationProviderFields?.addEventListener('input', (event) => {
            const input = event.target.closest('[data-config-key]');
            if (!input) {
                return;
            }
            setDestinationFieldError(input, '');
            setDestinationFormStatus('');
            if (input.dataset.configSecret === 'true' && input.value) {
                const clearSecret = dom.destinationProviderFields.querySelector(
                    `[data-clear-secret-for="${input.dataset.configKey}"]`,
                );
                if (clearSecret) {
                    clearSecret.checked = false;
                }
            }
        });
        dom.destinationProviderFields?.addEventListener('change', (event) => {
            const clearSecret = event.target.closest('[data-clear-secret-for]');
            if (!clearSecret) {
                return;
            }
            const input = dom.destinationProviderFields.querySelector(
                `[data-config-key="${clearSecret.dataset.clearSecretFor}"]`,
            );
            if (input) {
                input.value = '';
                input.disabled = !!clearSecret.checked;
                setDestinationFieldError(input, '');
            }
            setDestinationFormStatus('');
        });

        dom.openScheduleModalButton?.addEventListener('click', () => {
            clearScheduleForm();
            setScheduleModalTitle(false);
            openScheduleModal();
        });
        dom.scheduleCancelButton?.addEventListener('click', closeScheduleModal);
        dom.scheduleOverlay?.addEventListener('click', (event) => {
            if (event.target === dom.scheduleOverlay) {
                closeScheduleModal();
            }
        });

        dom.scheduleFrequencySelect?.addEventListener('change', () => {
            updateScheduleTimeVisibility();
        });
        dom.scheduleSaveButton?.addEventListener('click', handleSaveSchedule);
        dom.scheduleList?.addEventListener('click', handleScheduleAction);

        dom.actionConfirmCancelButton?.addEventListener('click', () => closeActionConfirmModal(false));
        dom.actionConfirmPrimaryButton?.addEventListener('click', () => closeActionConfirmModal(true));
        dom.actionConfirmOverlay?.addEventListener('click', (event) => {
            if (event.target === dom.actionConfirmOverlay) {
                closeActionConfirmModal(false);
            }
        });

        dom.refreshButton?.addEventListener('click', () => refreshAll({ isManualRefresh: true }));
        dom.jobsList?.addEventListener('click', handleJobAction);
        dom.jobsPaginationPrev?.addEventListener('click', () => (
            goToBackupJobsPage(state.jobsPage - 1)
        ));
        dom.jobsPaginationNext?.addEventListener('click', () => (
            goToBackupJobsPage(state.jobsPage + 1)
        ));
        dom.jobsPaginationPages?.addEventListener('click', (event) => {
            const pageButton = event.target.closest('[data-backup-jobs-page]');
            const page = Number(pageButton?.dataset.backupJobsPage);
            if (Number.isInteger(page)) {
                return goToBackupJobsPage(page);
            }
            return undefined;
        });

    }

    async function init() {
        if (!state.initialized) {
            state.initialized = true;
            ensureScheduleDayButtons();
            upgradeBackupModalSelects();
            bindEventHandlers();
            registerEscapeHandlers();
            clearDestinationForm();
            clearScheduleForm();
            setDestinationModalTitle(false);
            setScheduleModalTitle(false);
        }

        // Leaving the page aborts only the backup-history request. The other
        // requests in an existing aggregate refresh may still be settling, so
        // ``refreshAll`` would reject this rapid re-entry as a duplicate and
        // leave history on its stale cards or loading placeholder. Restart the
        // bounded history request directly while the remaining refresh work
        // completes in the background.
        if (state.refreshInProgress) {
            await refreshJobs();
            return;
        }
        await refreshAll();
    }

    function handleI18nUpdated() {
        upgradeBackupModalSelects();
        refreshScheduleDayButtonLabels();
        updateScheduleTimeVisibility();
        if (state.destinations.length) {
            renderDestinations();
        }
        if (state.schedules.length) {
            renderSchedules();
        }
        if (state.initialized && !state.jobsLoading) {
            // Re-render the active bounded page so its status copy and
            // pagination summary switch languages without another fetch.
            renderJobs();
        }
    }

    document.addEventListener('i18n:updated', handleI18nUpdated);

    // pages.js owns admin page lifecycles. Expose the initializer without
    // running it here so backup data is fetched only after opening Database.
    window.initDatabasePage = init;
    window.teardownDatabasePage = () => {
        state.jobsRequestController?.abort();
    };
})();
