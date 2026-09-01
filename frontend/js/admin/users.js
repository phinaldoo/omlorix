const userListContainer = document.querySelector('#page-users .user-table-container');
const deleteUserOverlay = document.getElementById('deleteUserOverlay');
const deleteUserMessage = document.getElementById('deleteUserMessage');
const deleteUserCancelButton = document.getElementById('deleteUserCancelButton');
const deleteUserPrimaryButton = document.getElementById('deleteUserPrimaryButton');
const deleteUserPrimaryText = document.getElementById('deleteUserPrimaryText');
const userExportButton = document.getElementById('exportUsersButton');
const userImportButton = document.getElementById('importUsersButton');
const userImportInput = document.getElementById('importUsersFileInput');
const userImportOverlay = document.getElementById('importUsersOverlay');
const userImportClose = document.getElementById('importUsersClose');
const userImportCancel = document.getElementById('importUsersCancel');
const userImportConfirm = document.getElementById('importUsersConfirm');
const userImportList = document.getElementById('importUsersList');
const userImportSelectAll = document.getElementById('importUsersSelectAll');
const userImportFileName = document.getElementById('importUsersFileName');
const userImportStatus = document.getElementById('importUsersStatus');
const userImportDefaultPassword = document.getElementById('importUsersDefaultPassword');
const userImportForcePasswordChange = document.getElementById('importUsersForcePasswordChange');
const editUserReasonOverlay = document.getElementById('editUserReasonOverlay');
const editUserReasonForm = document.getElementById('editUserReasonForm');
const editUserReasonInput = document.getElementById('editUserReasonInput');
const editUserReasonCancelButton = document.getElementById('editUserReasonCancelButton');
const userExportJobsOverlay = document.getElementById('userExportJobsOverlay');
const userExportJobsClose = document.getElementById('userExportJobsClose');
const userExportJobsCancel = document.getElementById('userExportJobsCancel');
const userExportCreateButton = document.getElementById('createUserExportJobButton');
const userExportJobsRefreshButton = document.getElementById('refreshUserExportJobsButton');
const userExportJobsList = document.getElementById('userExportJobsList');
const userExportJobsStatus = document.getElementById('userExportJobsStatus');
const userExportReason = document.getElementById('userExportReason');

const ROLE_SEQUENCE = ['pending', 'user', 'admin'];
let usersCache = [];
let usersInitialized = false;
let deleteUserId = null;
let deleteUserName = '';
let usersLanguageObserver = null;
let currentUserId = null;
let currentUserRole = '';
let usersSettingsController = null;
let pendingEditUser = null;
let editUserReasonLastFocusedElement = null;
let userExportJobs = [];
let userExportJobsRefreshTimer = null;
let userExportJobsLastFocusedElement = null;
let userExportJobsController = null;

const usersT = (key, fallback) => {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback !== undefined ? fallback : key;
};

function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function getCurrentUserId() {
    if (window.chatSetup?.user_role) {
        currentUserRole = normalizeRole(window.chatSetup.user_role);
    }
    if (currentUserId !== null) {
        return currentUserId;
    }
    if (window.chatSetup?.user_id) {
        currentUserId = window.chatSetup.user_id;
        return currentUserId;
    }
    try {
        const response = await window.authedFetch('/api/v1/settings/chat/setup');
        if (response.ok) {
            const setup = await response.json();
            currentUserId = setup.user_id || null;
            currentUserRole = normalizeRole(setup.user_role);
            return currentUserId;
        }
    } catch (error) {
        console.error('Failed to fetch current user ID', error);
    }
    return null;
}

const usersFormat = (key, fallback, values = {}) => {
    // Use the shared formatter whenever it is available so locale-aware ICU
    // plural blocks are resolved before ordinary placeholder substitution.
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, values);
    }
    let text = usersT(key, fallback);
    Object.entries(values).forEach(([name, value]) => {
        text = text.replace(new RegExp(`\\{${escapeRegExp(name)}\\}`, 'g'), String(value));
    });
    return text;
};

function syncDeleteModalDefaults() {
    defaultDeleteUserMessage = usersT('modal_delete_user_desc', 'Are you sure you want to delete this user?');
    defaultDeleteUserPrimaryText = usersT('modal_delete_user_btn', 'Delete User');

    if (deleteUserMessage) {
        deleteUserMessage.textContent = deleteUserId && deleteUserName
            ? usersFormat('modal_delete_user_named_desc', 'Are you sure you want to delete "{name}"?', { name: deleteUserName })
            : defaultDeleteUserMessage;
    }

    if (deleteUserPrimaryText && deleteUserPrimaryButton?.dataset.loading !== 'true') {
        deleteUserPrimaryText.textContent = defaultDeleteUserPrimaryText;
    }
}

let defaultDeleteUserMessage = 'Are you sure you want to delete this user?';
let defaultDeleteUserPrimaryText = 'Delete User';

const USERS_PER_PAGE = 20;
let currentPage = 1;
let searchQuery = '';
let searchDebounceTimer = null;
const userImportState = {
    users: [],
    selected: new Set(),
    fileName: '',
    archiveFile: null,
};
const ADMIN_USER_EXPORT_VERSION = 1.0;
const ADMIN_USERS_ARCHIVE_EXPORT_TYPE = 'admin_users_bundle';

function normalizeUtcDateString(value) {
    if (typeof value !== 'string') {
        return value;
    }
    const trimmed = value.trim();
    if (!trimmed) {
        return trimmed;
    }
    if (/[zZ]|[+\-]\d{2}:?\d{2}$/.test(trimmed)) {
        return trimmed;
    }
    const isoLike = trimmed.includes('T') ? trimmed : trimmed.replace(' ', 'T');
    return `${isoLike}Z`;
}

function setButtonLoadingState(button, isLoading, loadingLabel = 'Loading…') {
    if (!button) {
        return;
    }
    const labelTarget = button.querySelector('span');
    const original = button.dataset.originalLabel;
    if (isLoading) {
        if (!original) {
            button.dataset.originalLabel = labelTarget ? labelTarget.textContent : button.textContent;
        }
        button.disabled = true;
        button.classList.add('loading');
        if (labelTarget) {
            labelTarget.textContent = loadingLabel;
        } else {
            button.textContent = loadingLabel;
        }
    } else {
        button.disabled = false;
        button.classList.remove('loading');
        const restored = button.dataset.originalLabel || '';
        if (labelTarget) {
            labelTarget.textContent = restored;
        } else {
            button.textContent = restored;
        }
        delete button.dataset.originalLabel;
    }
}

function resetUserImportState() {
    userImportState.users = [];
    userImportState.selected = new Set();
    userImportState.fileName = '';
    userImportState.archiveFile = null;
    if (userImportDefaultPassword) {
        userImportDefaultPassword.value = '';
    }
    if (userImportForcePasswordChange) {
        userImportForcePasswordChange.checked = true;
    }
}

function setUserImportStatus(message = '', type = '') {
    if (!userImportStatus) {
        return;
    }
    if (!message) {
        userImportStatus.hidden = true;
        userImportStatus.textContent = '';
        userImportStatus.className = 'provider-import-status';
        return;
    }
    userImportStatus.hidden = false;
    userImportStatus.textContent = message;
    userImportStatus.className = `provider-import-status ${type}`.trim();
}

async function readUsersArchiveFile(file) {
    if (typeof JSZip === 'undefined') {
        throw new Error(usersT('users_import_zip_library_missing', 'Zip library not loaded. Please refresh the page.'));
    }

    let zip;
    try {
        zip = await JSZip.loadAsync(file);
    } catch (_) {
        throw new Error(usersT('users_import_invalid_zip', 'Invalid ZIP export file.'));
    }

    const manifestFile = zip.files['manifest.json'];
    if (!manifestFile || manifestFile.dir) {
        throw new Error(usersT('users_import_invalid_file', 'Invalid export file.'));
    }

    let manifest;
    try {
        manifest = JSON.parse(await manifestFile.async('text'));
    } catch (_) {
        throw new Error(usersT('users_import_invalid_file', 'Invalid export file.'));
    }
    if (
        manifest?.export_type !== ADMIN_USERS_ARCHIVE_EXPORT_TYPE
        || Number(manifest?.export_version) !== Number(ADMIN_USER_EXPORT_VERSION)
    ) {
        throw new Error(usersT('users_import_unsupported_file', 'Unsupported export file.'));
    }

    // Canonical archives expose only compact preview metadata in the index.
    // The backend verifies checksums and inflates selected account shards.
    const indexPath = String(manifest?.entries?.user_index || '').trim();
    const indexChecksum = String(manifest?.checksums?.[indexPath] || '').trim().toLowerCase();
    const indexFile = indexPath ? zip.files[indexPath] : null;
    if (
        !indexFile
        || indexFile.dir
        || !/^[0-9a-f]{64}$/.test(indexChecksum)
    ) {
        throw new Error(usersT('users_import_invalid_file', 'Invalid export file.'));
    }

    let indexPayload;
    try {
        indexPayload = JSON.parse(await indexFile.async('text'));
    } catch (_) {
        throw new Error(usersT('users_import_invalid_file', 'Invalid export file.'));
    }
    if (
        indexPayload?.export_type !== 'admin_user_index'
        || Number(indexPayload?.export_version) !== Number(ADMIN_USER_EXPORT_VERSION)
        || !Array.isArray(indexPayload?.users)
        || Number(manifest?.user_count) !== indexPayload.users.length
    ) {
        throw new Error(usersT('users_import_invalid_file', 'Invalid export file.'));
    }

    return indexPayload.users.map((entry) => ({
        user_id: entry?.user_id,
        email: entry?.email,
        user: { email: entry?.email },
    }));
}
function openUserImportModal() {
    if (!userImportOverlay) {
        return;
    }
    userImportOverlay.hidden = false;
    userImportOverlay.classList.add('active');
    if (userImportFileName) {
        userImportFileName.textContent = userImportState.fileName || '';
    }
    if (userImportSelectAll) {
        userImportSelectAll.checked = userImportState.users.length === userImportState.selected.size;
    }
    renderImportUsersList();
    setUserImportStatus();
    userImportDefaultPassword?.focus();
}

function closeUserImportModal() {
    if (userImportOverlay) {
        userImportOverlay.classList.remove('active');
        userImportOverlay.hidden = true;
    }
    if (userImportList) {
        userImportList.innerHTML = '';
    }
    if (userImportFileName) {
        userImportFileName.textContent = '';
    }
    if (userImportSelectAll) {
        userImportSelectAll.checked = false;
    }
    setUserImportStatus();
    resetUserImportState();
}

function renderImportUsersList() {
    if (!userImportList) {
        return;
    }
    userImportList.innerHTML = '';
    const entries = userImportState.users;
    if (!entries.length) {
        const empty = document.createElement('div');
        empty.className = 'provider-import-empty';
        empty.textContent = usersT('users_import_empty_file', 'No users found in this file.');
        userImportList.appendChild(empty);
        return;
    }
    const fragment = document.createDocumentFragment();
    entries.forEach((entry, index) => {
        const profile = entry?.user || {};
        const fullName = [profile.first_name, profile.last_name].map((part) => (part || '').trim()).filter(Boolean).join(' ');
        const email = profile.email || usersT('users_import_unknown_email', 'Unknown email');

        const label = document.createElement('label');
        label.className = 'provider-import-entry';
        label.setAttribute('role', 'option');
        label.setAttribute('aria-selected', userImportState.selected.has(index) ? 'true' : 'false');

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = userImportState.selected.has(index);
        checkbox.dataset.userIndex = String(index);
        checkbox.addEventListener('change', (event) => {
            const idx = Number.parseInt(event.currentTarget.dataset.userIndex, 10);
            if (Number.isNaN(idx)) {
                return;
            }
            if (event.currentTarget.checked) {
                userImportState.selected.add(idx);
            } else {
                userImportState.selected.delete(idx);
            }
            label.setAttribute('aria-selected', event.currentTarget.checked ? 'true' : 'false');
            if (userImportSelectAll) {
                userImportSelectAll.checked = userImportState.selected.size === userImportState.users.length;
            }
            setUserImportStatus();
        });
        label.appendChild(checkbox);

        const content = document.createElement('div');
        content.className = 'provider-import-entry-content';

        const title = document.createElement('p');
        title.className = 'provider-import-entry-title';
        title.textContent = fullName || email;
        content.appendChild(title);

        const metaPrimary = document.createElement('div');
        metaPrimary.className = 'provider-import-entry-meta';
        metaPrimary.textContent = usersFormat('users_import_meta_email', 'Email: {value}', { value: email });
        content.appendChild(metaPrimary);



        label.appendChild(content);
        fragment.appendChild(label);
    });
    userImportList.appendChild(fragment);
}

function toggleUserImportSelectAll(event) {
    const { checked } = event.currentTarget;
    userImportState.selected.clear();
    if (checked) {
        userImportState.users.forEach((_, index) => userImportState.selected.add(index));
    }
    renderImportUsersList();
    setUserImportStatus();
}

async function submitUserImport() {
    if (!userImportState.archiveFile) {
        setUserImportStatus(usersT('users_import_choose_file_first', 'Please choose a user export file first.'));
        return;
    }
    if (!userImportState.selected.size) {
        setUserImportStatus(usersT('users_import_select_one', 'Select at least one user to import.'));
        return;
    }
    const defaultPassword = userImportDefaultPassword?.value || '';
    if (!defaultPassword) {
        const message = usersT('users_import_default_password_required', 'Enter a default password for imported users.');
        setUserImportStatus(message, 'warning');
        notifyWarning?.(message);
        userImportDefaultPassword?.focus();
        return;
    }
    const importOptions = {
        default_password: defaultPassword,
        force_password_change: userImportForcePasswordChange?.checked !== false,
    };
    try {
        setButtonLoadingState(userImportConfirm, true, usersT('users_import_busy_importing', 'Importing…'));
        const indices = Array.from(userImportState.selected).sort((a, b) => a - b);
        const formData = new FormData();
        formData.append('file', userImportState.archiveFile, userImportState.archiveFile.name || 'admin-users.zip');
        formData.append('selected_indices', JSON.stringify(indices));
        formData.append('default_password', importOptions.default_password);
        formData.append('force_password_change', importOptions.force_password_change ? 'true' : 'false');
        const response = await window.authedFetch('/api/v1/admin/users/import', {
            method: 'POST',
            body: formData,
        });
        if (!response.ok) {
            let message = usersT('users_import_failed', 'Failed to import users.');
            try {
                const errorData = await response.json();
                if (errorData?.detail) {
                    message = translateBackendError(errorData.detail) || errorData.detail;
                }
            } catch (_) {}
            notifyError(message);
            return;
        }
        const result = await response.json();
        const createdCount = result?.created?.length || 0;
        const updatedCount = result?.updated?.length || 0;
        const warningCount = result?.warnings?.length || 0;
        const errorCount = result?.errors?.length || 0;
        const createdFilesCount = Number(result?.created_files_count || 0);
        const skippedFilesCount = Number(result?.skipped_files_count || 0);
        const createdNotesCount = Number(result?.created_notes_count || 0);
        const skippedNotesCount = Number(result?.skipped_notes_count || 0);
        const createdMemoriesCount = Number(result?.created_memories_count || 0);
        const dedupedMemoriesCount = Number(result?.deduped_memories_count || 0);
        if (createdCount || updatedCount) {
            const parts = [
                usersFormat(
                'users_import_created_updated',
                'Created {created} and updated {updated} user(s) successfully.',
                { created: createdCount, updated: updatedCount },
                ),
                ...buildEntitySummary(
                    createdFilesCount,
                    usersT('users_import_item_files_one', 'file'),
                    usersT('users_import_item_files_other', 'files'),
                    skippedFilesCount,
                    usersT('users_import_item_files_one', 'file'),
                    usersT('users_import_item_files_other', 'files'),
                ),
                ...buildEntitySummary(
                    createdNotesCount,
                    usersT('users_import_item_notes_one', 'note'),
                    usersT('users_import_item_notes_other', 'notes'),
                    skippedNotesCount,
                    usersT('users_import_item_notes_one', 'note'),
                    usersT('users_import_item_notes_other', 'notes'),
                ),
                ...buildEntitySummary(
                    createdMemoriesCount,
                    usersT('users_import_item_memories_one', 'memory'),
                    usersT('users_import_item_memories_other', 'memories'),
                    dedupedMemoriesCount,
                    usersT('users_import_item_memories_one', 'memory'),
                    usersT('users_import_item_memories_other', 'memories'),
                ),
            ].filter(Boolean);
            const successMessage = parts.join(' ');
            notifySuccess?.(successMessage);
            setUserImportStatus(successMessage, 'success');
        }
        if (warningCount) {
            const warningMessage = buildWarningSummary(result?.warnings)
                || usersFormat(
                    'users_import_warning_count',
                    '{count} warning(s): some users were updated partially or had unresolved references.',
                    { count: warningCount },
                );
            notifyWarning?.(warningMessage);
            setUserImportStatus(warningMessage, 'warning');
        }
        if (errorCount) {
            const formatted = Array.isArray(result?.errors)
                ? result.errors
                    .map((entry) => {
                        if (!entry || typeof entry !== 'object') {
                            return '';
                        }
                        const idx = entry.index === undefined ? '?' : Number(entry.index) + 1;
                        const email = entry.email ? ` (${entry.email})` : '';
                        const detail = entry.error ? JSON.stringify(entry.error) : usersT('users_import_unknown_error', 'Unknown error');
                        return `• ${usersFormat('users_import_error_item', 'Item {index}{email}: {detail}', { index: idx, email, detail })}`;
                    })
                    .filter(Boolean)
                : [];
            const warning = usersFormat('users_import_partial_failure', '{count} user(s) failed to import.{details}', {
                count: errorCount,
                details: formatted.length ? `\n${formatted.join('\n')}` : '',
            });
            setUserImportStatus(warning);
            notifyWarning?.(warning);
        }
        usersCache = await fetchUsersList();
        currentPage = 1;
        renderPaginatedUsersList();
        if (!errorCount) {
            closeUserImportModal();
        }
    } catch (error) {
        console.error('Failed to import users', error);
        setUserImportStatus(error?.message || usersT('users_import_failed', 'Failed to import users.'));
        notifyError?.(error?.message || usersT('users_import_failed', 'Failed to import users.'));
    } finally {
        setButtonLoadingState(userImportConfirm, false);
    }
}

function setUserExportJobsStatus(message = '', type = '') {
    if (!userExportJobsStatus) {
        return;
    }
    if (!message) {
        userExportJobsStatus.hidden = true;
        userExportJobsStatus.textContent = '';
        userExportJobsStatus.className = 'provider-import-status';
        return;
    }
    userExportJobsStatus.hidden = false;
    userExportJobsStatus.textContent = message;
    userExportJobsStatus.className = `provider-import-status ${type || ''}`.trim();
}

function openUserExportJobsModal() {
    userExportJobsLastFocusedElement = document.activeElement;
    if (userExportJobsOverlay) {
        userExportJobsOverlay.hidden = false;
        userExportJobsOverlay.classList.add('active');
    }
    refreshUserExportJobs({ silent: true });
    userExportCreateButton?.focus();
}

function closeUserExportJobsModal() {
    if (userExportJobsOverlay) {
        userExportJobsOverlay.hidden = true;
        userExportJobsOverlay.classList.remove('active');
    }
    if (userExportJobsRefreshTimer) {
        window.clearTimeout(userExportJobsRefreshTimer);
        userExportJobsRefreshTimer = null;
    }
    setUserExportJobsStatus();
    if (userExportJobsLastFocusedElement && typeof userExportJobsLastFocusedElement.focus === 'function') {
        userExportJobsLastFocusedElement.focus();
    }
    userExportJobsLastFocusedElement = null;
}

function escapeUsersHtml(value) {
    if (typeof escapeHtml === 'function') {
        return escapeHtml(value);
    }
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function formatUserExportJobDate(value) {
    const date = parseUtcDate(value);
    if (!date) {
        return usersT('users_export_jobs_not_available', 'Not available');
    }
    try {
        return new Intl.DateTimeFormat(getCurrentLocale(), {
            dateStyle: 'medium',
            timeStyle: 'short',
        }).format(date);
    } catch (_) {
        return date.toLocaleString();
    }
}

function formatUserExportJobSize(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) {
        return usersT('users_export_jobs_not_available', 'Not available');
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
}

function getUserExportJobStatusLabel(status) {
    switch (String(status || '').toLowerCase()) {
        case 'queued':
            return usersT('users_export_job_status_queued', 'Queued');
        case 'running':
            return usersT('users_export_job_status_running', 'Running');
        case 'success':
            return usersT('users_export_job_status_success', 'Ready to download');
        case 'failed':
            return usersT('users_export_job_status_failed', 'Failed');
        case 'deleted':
            return usersT('users_export_job_status_deleted', 'Deleted');
        case 'expired':
            return usersT('users_export_job_status_expired', 'Expired');
        default:
            return status || usersT('users_export_jobs_not_available', 'Not available');
    }
}

function getUserExportJobStatusClass(status) {
    const normalized = String(status || '').toLowerCase();
    if (normalized === 'success') {
        return 'success';
    }
    if (['failed', 'deleted', 'expired'].includes(normalized)) {
        return 'error';
    }
    return 'warning';
}

function renderUserExportJobs() {
    if (!userExportJobsList) {
        return;
    }
    if (!userExportJobs.length) {
        userExportJobsList.innerHTML = `<p class="settings-row-desc">${escapeUsersHtml(usersT('users_export_jobs_empty', 'No export jobs yet.'))}</p>`;
        return;
    }

    userExportJobsList.innerHTML = userExportJobs.map((job) => {
        const status = String(job.status || '').toLowerCase();
        const canDownload = status === 'success' && job.download_ready;
        const canDelete = !['queued', 'running', 'deleted', 'expired'].includes(status);
        const manifest = job.manifest_json && typeof job.manifest_json === 'object' ? job.manifest_json : {};
        const userCount = manifest.user_count ?? usersT('users_export_jobs_not_available', 'Not available');
        const fileBundleCount = Number.isFinite(Number(manifest.user_files_count))
            ? Number(manifest.user_files_count)
            : null;
        const fileBundleText = fileBundleCount === null
            ? usersT('users_export_jobs_not_available', 'Not available')
            : fileBundleCount;
        return `
            <div class="provider-import-entry user-export-job-entry" data-export-job-id="${escapeUsersHtml(job.id)}">
                <div class="user-export-job-content">
                    <div class="user-export-job-main">
                        <div class="user-export-job-title-row">
                            <p class="provider-import-entry-title">${escapeUsersHtml(job.filename || job.id)}</p>
                            <span class="pill ${escapeUsersHtml(getUserExportJobStatusClass(status))}">${escapeUsersHtml(getUserExportJobStatusLabel(status))}</span>
                        </div>
                        <div class="user-export-job-meta">
                            <p class="user-export-job-meta-item">${escapeUsersHtml(usersT('users_export_job_created_label', 'Created'))}: ${escapeUsersHtml(formatUserExportJobDate(job.created_at))}</p>
                            <p class="user-export-job-meta-item">${escapeUsersHtml(usersT('users_export_job_finished_label', 'Finished'))}: ${escapeUsersHtml(formatUserExportJobDate(job.finished_at))}</p>
                            <p class="user-export-job-meta-item">${escapeUsersHtml(usersT('users_export_job_expires_label', 'Expires'))}: ${escapeUsersHtml(formatUserExportJobDate(job.expires_at))}</p>
                            <p class="user-export-job-meta-item">${escapeUsersHtml(usersT('users_export_job_size_label', 'Size'))}: ${escapeUsersHtml(formatUserExportJobSize(job.size_bytes))}</p>
                            <p class="user-export-job-meta-item">${escapeUsersHtml(usersT('users_export_job_users_label', 'Users'))}: ${escapeUsersHtml(userCount)}</p>
                            <p class="user-export-job-meta-item">${escapeUsersHtml(usersT('users_export_job_file_bundles_label', 'File bundles'))}: ${escapeUsersHtml(fileBundleText)}</p>
                        </div>
                        ${job.error ? `<p class="user-export-job-error">${escapeUsersHtml(usersT('users_export_job_error_label', 'Error'))}: ${escapeUsersHtml(job.error)}</p>` : ''}
                    </div>
                    <div class="user-export-job-actions">
                        <button type="button" class="om-button border cancel" data-user-export-job-action="download" data-job-id="${escapeUsersHtml(job.id)}" ${canDownload ? '' : 'disabled'}>${escapeUsersHtml(usersT('users_export_job_download_btn', 'Download'))}</button>
                        <button type="button" class="om-button border danger" data-user-export-job-action="delete" data-job-id="${escapeUsersHtml(job.id)}" ${canDelete ? '' : 'disabled'}>${escapeUsersHtml(usersT('users_export_job_delete_btn', 'Delete'))}</button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function scheduleUserExportJobsRefresh() {
    if (userExportJobsRefreshTimer) {
        window.clearTimeout(userExportJobsRefreshTimer);
        userExportJobsRefreshTimer = null;
    }
    const hasActiveJob = userExportJobs.some((job) => ['queued', 'running'].includes(String(job.status || '').toLowerCase()));
    if (hasActiveJob && userExportJobsOverlay && !userExportJobsOverlay.hidden) {
        userExportJobsRefreshTimer = window.setTimeout(() => {
            refreshUserExportJobs({ silent: true });
        }, 5000);
    }
}

async function refreshUserExportJobs(options = {}) {
    if (!userExportJobsList) {
        return;
    }
    try {
        if (!options.silent) {
            setButtonLoadingState(userExportJobsRefreshButton, true, usersT('users_export_jobs_refreshing', 'Refreshing…'));
        }
        const response = await window.authedFetch('/api/v1/admin/users/export/jobs?limit=50');
        userExportJobs = await fetchJsonResponseOrThrow(response, 'users_export_jobs_failed', 'Failed to load export jobs.');
        renderUserExportJobs();
        scheduleUserExportJobsRefresh();
        if (!options.silent) {
            setUserExportJobsStatus(usersT('users_export_jobs_refreshed', 'Export jobs refreshed.'), 'success');
        }
    } catch (error) {
        console.error('Failed to refresh user export jobs', error);
        setUserExportJobsStatus(error?.message || usersT('users_export_jobs_failed', 'Failed to load export jobs.'), 'error');
        notifyError?.(error?.message || usersT('users_export_jobs_failed', 'Failed to load export jobs.'));
    } finally {
        if (!options.silent) {
            setButtonLoadingState(userExportJobsRefreshButton, false);
        }
    }
}

async function queueUserExportJob() {
    const reason = String(userExportReason?.value || '').trim();
    if (reason.length < 3) {
        const message = usersT('users_export_reason_required', 'Enter a reason of at least three characters.');
        setUserExportJobsStatus(message, 'error');
        userExportReason?.focus();
        return;
    }
    try {
        setButtonLoadingState(userExportCreateButton, true, usersT('users_export_busy_queueing', 'Queueing…'));
        const response = await window.authedFetch('/api/v1/admin/users/export/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason, user_ids: [] }),
        });
        const job = await fetchJsonResponseOrThrow(response, 'users_export_failed', 'Failed to export users.');
        setUserExportJobsStatus(
            usersFormat('users_export_job_queued_status', 'Export job queued: {id}', { id: job.id }),
            'success',
        );
        notifySuccess?.(usersT('users_export_job_queued_success', 'User export job queued.'));
        await refreshUserExportJobs({ silent: true });
    } catch (error) {
        console.error('Failed to export users', error);
        notifyError?.(error?.message || usersT('users_export_failed', 'Failed to export users.'));
    } finally {
        setButtonLoadingState(userExportCreateButton, false);
    }
}

async function downloadUserExportJob(jobId) {
    try {
        const response = await window.authedFetch(`/api/v1/admin/users/export/jobs/${encodeURIComponent(jobId)}/download`);
        if (!response.ok) {
            await fetchJsonResponseOrThrow(response, 'users_export_job_download_failed', 'Failed to download export job.');
        }
        const blob = await response.blob();
        const filename = extractFilenameFromDisposition(response.headers.get('content-disposition'))
            || buildTimestampedFilename('admin-users', 'zip');
        downloadBlobPayload(blob, filename);
        notifySuccess?.(usersT('users_export_job_download_success', 'User export downloaded.'));
    } catch (error) {
        console.error('Failed to download user export job', error);
        notifyError?.(error?.message || usersT('users_export_job_download_failed', 'Failed to download export job.'));
    }
}

async function deleteUserExportJob(jobId) {
    const confirmed = await window.showDeleteConfirm?.({
        title: usersT('users_export_job_delete_title', 'Delete export job?'),
        message: usersT('users_export_job_delete_desc', 'This removes the generated ZIP file for this export job.'),
        confirmLabel: usersT('users_export_job_delete_btn', 'Delete'),
    });
    if (!confirmed) {
        return;
    }

    try {
        const response = await window.authedFetch(`/api/v1/admin/users/export/jobs/${encodeURIComponent(jobId)}`, {
            method: 'DELETE',
        });
        await fetchJsonResponseOrThrow(response, 'users_export_job_delete_failed', 'Failed to delete export job.');
        notifySuccess?.(usersT('users_export_job_delete_success', 'Export job deleted.'));
        await refreshUserExportJobs({ silent: true });
    } catch (error) {
        console.error('Failed to delete user export job', error);
        notifyError?.(error?.message || usersT('users_export_job_delete_failed', 'Failed to delete export job.'));
    }
}

function handleUserExportJobAction(event) {
    const button = event.target.closest('[data-user-export-job-action]');
    if (!button) {
        return;
    }
    const jobId = button.dataset.jobId;
    if (!jobId) {
        return;
    }
    if (button.dataset.userExportJobAction === 'download') {
        downloadUserExportJob(jobId);
    } else if (button.dataset.userExportJobAction === 'delete') {
        deleteUserExportJob(jobId);
    }
}

async function handleImportUsersInput(event) {
    const file = event?.target?.files?.[0];
    if (!file) {
        return;
    }
    event.target.value = '';
    try {
        const lowerName = file.name.toLowerCase();
        const isZip = file.type === 'application/zip' || lowerName.endsWith('.zip');
        if (!isZip) {
            notifyError?.(usersT('users_import_invalid_zip', 'Please choose a valid ZIP export file.'));
            return;
        }

        const users = await readUsersArchiveFile(file);
        if (!users.length) {
            notifyWarning?.(usersT('users_import_no_users_in_export', 'No users found in this export file.'));
            return;
        }
        userImportState.users = users;
        userImportState.selected = new Set(users.map((_, index) => index));
        userImportState.fileName = file.name || 'admin-users.zip';
        userImportState.archiveFile = file;
        openUserImportModal();
    } catch (error) {
        console.error('Failed to process user import file', error);
        notifyError?.(error?.message || usersT('users_import_process_failed', 'Failed to process user import file.'));
    }
}
function handleDeleteUserClick(button) {
    const row = button.closest('.user-row');
    const userId = row?.dataset.userId;
    if (!userId) {
        notifyError?.(usersT('users_error_missing_user_id', 'Failed to resolve user ID.'));
        return;
    }

    const name = row?.querySelector('.user-name-primary')?.textContent?.trim() || '';
    openDeleteUserModal(userId, name);
}

function handleEditUserClick(button) {
    openUserEditById(button?.dataset?.userId);
}

/**
 * Open the same audited user-edit flow for buttons and row-wide shortcuts.
 *
 * Keeping user resolution in one place ensures a row click cannot bypass the
 * reason modal or behave differently from the explicit edit action.
 */
function openUserEditById(rawUserId) {
    const userId = String(rawUserId || '').trim();
    if (!userId) {
        notifyError?.(usersT('users_error_missing_user_id', 'Failed to resolve user ID.'));
        return;
    }

    const user = usersCache.find((entry) => String(entry?.id) === userId);
    if (!user) {
        notifyError?.(usersT('users_error_user_not_found', 'Unable to find that user.'));
        return;
    }

    openEditUserReasonModal(user);
}

/**
 * Open the audit-reason gate before entering the user editor.
 *
 * The profile editor needs the same reason for its initial sensitive profile
 * fetch and later audited profile/security updates, so the reason is captured
 * once before navigation and then carried into the edit page state.
 */
function openEditUserReasonModal(user) {
    if (!editUserReasonOverlay || !editUserReasonInput) {
        openUserEditorWithReason(user, '');
        return;
    }

    pendingEditUser = user;
    editUserReasonLastFocusedElement = document.activeElement;
    editUserReasonOverlay.hidden = false;
    editUserReasonOverlay.setAttribute('aria-hidden', 'false');
    editUserReasonForm?.reset();
    requestAnimationFrame(() => editUserReasonInput.focus());
}

function closeEditUserReasonModal({ restoreFocus = true } = {}) {
    pendingEditUser = null;
    if (editUserReasonOverlay) {
        editUserReasonOverlay.setAttribute('aria-hidden', 'true');
        editUserReasonOverlay.hidden = true;
    }
    editUserReasonForm?.reset();

    if (restoreFocus && editUserReasonLastFocusedElement instanceof HTMLElement) {
        editUserReasonLastFocusedElement.focus();
    }
    editUserReasonLastFocusedElement = null;
}

function openUserEditorWithReason(user, reason) {
    window.openAdminUserSettingsPage?.({
        id: user.id,
        firstName: user.first_name,
        lastName: user.last_name,
        email: user.email,
        reason,
    });
}

function parseUtcDate(value) {
    if (!value) {
        return null;
    }
    const date = new Date(normalizeUtcDateString(value));
    return Number.isNaN(date.getTime()) ? null : date;
}

function getCurrentLocale() {
    const documentLang = document.documentElement?.getAttribute('lang')?.trim();
    if (documentLang) {
        return documentLang;
    }
    let storedLang;
    try {
        storedLang = localStorage.getItem('lang');
    } catch (error) {
        console.warn('Failed to read saved language:', error);
        storedLang = null;
    }
    if (storedLang) {
        return storedLang;
    }
    return navigator.language || 'en';
}

function formatRelativeTimeValue(value, unit, locale, numeric = 'always') {
    try {
        return new Intl.RelativeTimeFormat(locale, { numeric }).format(value, unit);
    } catch (_) {
        return null;
    }
}

function observeUsersLanguageChanges() {
    if (usersLanguageObserver || !document.documentElement) {
        return;
    }
    usersLanguageObserver = new MutationObserver((mutations) => {
        const langChanged = mutations.some((mutation) => mutation.type === 'attributes' && mutation.attributeName === 'lang');
        if (langChanged && usersInitialized) {
            syncDeleteModalDefaults();
            renderImportUsersList();
            renderPaginatedUsersList();
            renderUserExportJobs();
        }
    });
    usersLanguageObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['lang'],
    });
}

async function initUsersPage() {
    if (!userListContainer) {
        return;
    }

    renderUsersLoadingState(usersT('users_loading', 'Loading users…'));
    observeUsersLanguageChanges();
    syncDeleteModalDefaults();
    if (!usersSettingsController && typeof window.createSettingsPageController === 'function') {
        const renderUsersSettingsEmptyState = (target) => {
            const emptyState = document.createElement('p');
            emptyState.className = 'settings-empty';
            emptyState.textContent = usersT('admin_settings_schema_empty', 'Settings schema is empty.');
            target.appendChild(emptyState);
        };
        const renderUsersSettingsErrorState = (target, message) => {
            const errorMessage = document.createElement('p');
            errorMessage.className = 'settings-error';
            errorMessage.textContent = message;
            target.appendChild(errorMessage);
        };
        usersSettingsController = window.createSettingsPageController({
            pageKey: 'users',
            containerId: document.querySelector('#page-users .provider-import-export'),
            statusId: 'usersSettingsStatus',
            stringDebounceMs: 600,
            stringListDebounceMs: 600,
            preserveContainerChildren: true,
            insertPosition: 'prepend',
            loadErrorMessage: usersT('admin_user_settings_schema_unavailable', 'Unable to load user settings schema.'),
            renderEmptyState: renderUsersSettingsEmptyState,
            renderError: renderUsersSettingsErrorState,
            onError: (message) => notifyError?.(message),
        });
    }
    usersSettingsController?.init();
    await getCurrentUserId();
    if (usersInitialized) {
        loadUsersList();
        return;
    }
    usersInitialized = true;
    bindUserListActions();
    bindUserImportExportEvents();
    bindEditUserReasonModalEvents();
    bindUserSearchEvents();
    await loadUsersList();
}

async function loadUsersList() {
    if (!userListContainer) {
        return;
    }

    renderUsersLoadingState(usersT('users_loading', 'Loading users…'));
    usersCache = await fetchUsersList();
    syncCurrentUserRoleFromUsers();
    currentPage = 1;
    renderPaginatedUsersList();
}

/**
 * Recover the acting role from the users response for older cached setup
 * payloads that do not yet contain ``user_role``.
 */
function syncCurrentUserRoleFromUsers() {
    if (currentUserRole || currentUserId === null) {
        return;
    }
    const currentUser = usersCache.find(
        (user) => String(user?.id) === String(currentUserId),
    );
    currentUserRole = normalizeRole(currentUser?.role);
}

/**
 * Return whether the signed-in administrator may mutate ``user``.
 *
 * This mirrors the backend hierarchy so forbidden controls are not presented
 * as if they were available. The backend remains the authoritative check.
 */
function canManageAccount(user) {
    const targetRole = normalizeRole(user?.role);
    const isSelf = String(user?.id) === String(currentUserId);
    if (isSelf || targetRole === 'owner') {
        return false;
    }
    if (targetRole === 'admin') {
        return currentUserRole === 'owner';
    }
    return true;
}

function canOpenUserEditor(user) {
    const isSelf = String(user?.id) === String(currentUserId);
    return isSelf || canManageAccount(user);
}

function getAccountProtectionLabel(user) {
    const targetRole = normalizeRole(user?.role);
    if (targetRole === 'owner') {
        return usersT('users_account_owner_protected', 'The owner account is protected.');
    }
    if (targetRole === 'admin' && currentUserRole !== 'owner') {
        return usersT(
            'users_account_admin_owner_only',
            'Only the owner can manage administrator accounts.',
        );
    }
    return '';
}

function getFilteredUsers() {
    if (!searchQuery.trim()) {
        return usersCache;
    }
    const query = searchQuery.trim().toLowerCase();
    return usersCache.filter((user) => {
        const firstName = (user?.first_name || '').toLowerCase();
        const lastName = (user?.last_name || '').toLowerCase();
        const fullName = `${firstName} ${lastName}`.trim();
        const email = (user?.email || '').toLowerCase();
        return (
            firstName.includes(query) ||
            lastName.includes(query) ||
            fullName.includes(query) ||
            email.includes(query)
        );
    });
}

function getPaginatedUsers() {
    const filtered = getFilteredUsers();
    const startIndex = (currentPage - 1) * USERS_PER_PAGE;
    const endIndex = startIndex + USERS_PER_PAGE;
    return {
        users: filtered.slice(startIndex, endIndex),
        totalUsers: filtered.length,
        totalPages: Math.ceil(filtered.length / USERS_PER_PAGE) || 1,
        currentPage,
        startIndex: startIndex + 1,
        endIndex: Math.min(endIndex, filtered.length),
    };
}

function renderPaginatedUsersList() {
    const { users, totalUsers, totalPages, startIndex, endIndex } = getPaginatedUsers();
    
    if (searchQuery.trim() && totalUsers === 0) {
        renderUsersList([], {
            message: usersT('users_search_empty_title', 'No users found'),
            description: usersFormat('users_search_empty_desc', 'No users match "{query}". Try a different search term.', { query: searchQuery }),
        });
    } else {
        renderUsersList(users);
    }
    
    renderPaginationControls(totalUsers, totalPages, startIndex, endIndex);
    updateSearchResultsCount(totalUsers);
}

function renderPaginationControls(totalUsers, totalPages, startIndex, endIndex) {
    const paginationContainer = document.getElementById('usersPaginationControls');
    const paginationInfo = document.getElementById('usersPaginationInfo');
    const prevBtn = document.getElementById('usersPaginationPrev');
    const nextBtn = document.getElementById('usersPaginationNext');
    if (!paginationContainer) {
        return;
    }

    const hasPrev = currentPage > 1;
    const hasNext = currentPage < totalPages;
    const singlePage = totalUsers <= USERS_PER_PAGE;
    const shouldHide = singlePage && !hasPrev && !hasNext;

    paginationContainer.hidden = shouldHide;

    if (prevBtn) {
        prevBtn.disabled = !hasPrev;
        prevBtn.onclick = (event) => {
            event.preventDefault();
            if (!prevBtn.disabled) {
                goToUsersPage(currentPage - 1);
            }
        };
    }
    if (nextBtn) {
        nextBtn.disabled = !hasNext;
        nextBtn.onclick = (event) => {
            event.preventDefault();
            if (!nextBtn.disabled) {
                goToUsersPage(currentPage + 1);
            }
        };
    }

    if (paginationInfo) {
        paginationInfo.textContent = shouldHide
            ? ''
            : usersFormat('users_pagination_range', '{start}–{end} of {total} user(s)', {
                start: totalUsers === 0 ? 0 : startIndex,
                end: totalUsers === 0 ? 0 : endIndex,
                total: totalUsers,
            });
    }
}

function goToUsersPage(page) {
    const { totalPages } = getPaginatedUsers();
    if (page < 1 || page > totalPages || page === currentPage) {
        return;
    }
    currentPage = page;
    renderPaginatedUsersList();
    userListContainer?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function updateSearchResultsCount() {
    const clearBtn = document.getElementById('adminUserSearchClear');
    const trimmedQuery = searchQuery.trim();

    if (clearBtn) {
        clearBtn.hidden = !trimmedQuery;
    }
}

function bindUserSearchEvents() {
    const searchInput = document.getElementById('adminUserSearchInput');
    const clearBtn = document.getElementById('adminUserSearchClear');
    
    if (searchInput && searchInput.dataset.bound !== 'true') {
        searchInput.addEventListener('input', (event) => {
            const value = event.target.value;
            if (searchDebounceTimer) {
                clearTimeout(searchDebounceTimer);
            }
            searchDebounceTimer = setTimeout(() => {
                searchQuery = value;
                currentPage = 1;
                renderPaginatedUsersList();
            }, 200);
        });
        
        searchInput.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                searchInput.value = '';
                searchQuery = '';
                currentPage = 1;
                renderPaginatedUsersList();
                searchInput.blur();
            }
        });
        
        searchInput.dataset.bound = 'true';
    }
    
    if (clearBtn && clearBtn.dataset.bound !== 'true') {
        clearBtn.addEventListener('click', () => {
            if (searchInput) {
                searchInput.value = '';
            }
            searchQuery = '';
            currentPage = 1;
            renderPaginatedUsersList();
            searchInput?.focus();
        });
        clearBtn.dataset.bound = 'true';
    }
}

async function fetchUsersList() {
    try {
        const response = await window.authedFetch('/api/v1/admin/users', {
            method: 'GET',
        });
        if (!response.ok) {
            notifyError(usersT('users_fetch_failed', 'Failed to fetch users'));
        }
        return await response.json();
    } catch (error) {
        console.error('Failed to load users', error);
        notifyError(error?.message || usersT('users_fetch_failed', 'Failed to fetch users'));
        return [];
    }
}

function renderUsersLoadingState(message = 'Loading users…') {
    if (!userListContainer) {
        return;
    }

    userListContainer.innerHTML = '';

    const loadingState = window.createAdminLoadingPlaceholder({
        message,
        className: '',
    });
    userListContainer.appendChild(loadingState);
}

function renderUsersList(users = [], options = {}) {
    if (!userListContainer) {
        return;
    }

    userListContainer.innerHTML = '';

    const { message, description } = options;
    if (message || !users.length) {
        const descriptionText = description === undefined
            ? (!message ? usersT('users_empty_desc', 'Invite users to see them listed here.') : '')
            : description;

        const emptyState = window.createAdminEmptyPlaceholder({
            title: message || usersT('users_empty_title', 'No users yet'),
            description: descriptionText,
            icon: Icons?.user || Icons?.omlorix || '👤',
            className: 'provider-empty-state user-empty-state',
        });

        userListContainer.appendChild(emptyState);
        return;
    }

    const headerCells = [
        { className: 'header-name', text: usersT('table_header_name', 'Name') },
        { className: 'header-email', text: usersT('table_header_email', 'Email') },
        { className: 'header-group', text: usersT('table_header_group', 'Group') },
        { className: 'header-role', text: usersT('table_header_role', 'Role') },
        { className: 'header-status', text: usersT('table_header_status', 'Status') },
        { className: 'header-last-active', text: usersT('users_last_active_header', 'Last Active') },
        { className: 'header-actions', text: usersT('table_header_actions', 'Actions') },
    ];

    const header = window.createAdminTableHeader({
        className: 'user-table-header',
        cells: headerCells,
    });
    userListContainer.appendChild(header);

    const fragment = document.createDocumentFragment();

    users.forEach((user) => {
        const fullName = [user?.first_name, user?.last_name]
            .map((part) => (part || '').trim())
            .filter(Boolean)
            .join(' ');

        const row = document.createElement('div');
        row.className = 'user-row';
        const canEditUser = canOpenUserEditor(user);
        const canMutateUser = canManageAccount(user);
        const protectionLabel = getAccountProtectionLabel(user);
        if (!canEditUser) {
            row.classList.add('user-row-protected');
        }
        // The row is a convenient edit shortcut while the role, status, and
        // action cells retain their own independent interactions.
        if (user?.id) {
            row.dataset.userId = user.id;
            row.dataset.userRole = normalizeRole(user?.role);
            row.dataset.canEdit = canEditUser ? 'true' : 'false';
            if (canEditUser) {
                row.setAttribute('tabindex', '0');
                const userIdentifier = fullName || user?.email || '—';
                row.setAttribute(
                    'aria-label',
                    `${usersT('users_action_edit_title', 'Edit user')}: ${userIdentifier}`,
                );
            } else if (protectionLabel) {
                row.setAttribute('title', protectionLabel);
            }
        }

        const nameCell = document.createElement('div');
        nameCell.className = 'user-name';

        const primaryName = document.createElement('span');
        primaryName.className = 'user-name-primary';
        primaryName.textContent = fullName || '—';
        nameCell.appendChild(primaryName);

        if (user?.externally_managed) {
            const managedLabel = document.createElement('span');
            managedLabel.className = 'user-name-secondary';
            const provider = String(user?.external_auth_provider || '').trim().toUpperCase();
            managedLabel.textContent = provider
                ? `${usersT('users_externally_managed', 'Externally managed')} · ${provider}`
                : usersT('users_externally_managed', 'Externally managed');
            nameCell.appendChild(managedLabel);
        }


        const emailCell = window.createAdminTableCell({
            className: 'user-email',
            label: usersT('table_header_email', 'Email'),
            text: user?.email || '—',
        });

        const groupCell = window.createAdminTableCell({
            className: 'user-group',
            label: usersT('table_header_group', 'Group'),
            text: user?.group_name || '—',
        });

        const roleCell = window.createAdminTableCell({
            className: 'user-role',
            label: usersT('table_header_role', 'Role'),
        });
        const roleBadge = document.createElement('span');
        roleBadge.className = 'role-badge';
        roleCell.appendChild(roleBadge);
        setRoleBadgeState(roleCell, user?.role);
        roleCell.dataset.mutable = canMutateUser ? 'true' : 'false';

        const statusCell = window.createAdminTableCell({
            className: 'user-status',
            label: usersT('table_header_status', 'Status'),
        });
        setStatusCellState(statusCell, Boolean(user?.is_active));
        statusCell.dataset.mutable = canMutateUser ? 'true' : 'false';

        if (!canMutateUser) {
            [roleCell, statusCell].forEach((cell) => {
                cell.classList.add('user-account-control-disabled');
                cell.setAttribute('aria-disabled', 'true');
                if (protectionLabel) {
                    cell.setAttribute('title', protectionLabel);
                }
            });
        }

        const lastActiveCell = window.createAdminTableCell({
            className: 'user-last-active',
            label: usersT('users_last_active_header', 'Last Active'),
            text: formatRelativeLastActive(user?.last_active_at),
        });

        const actionsCell = document.createElement('div');
        actionsCell.className = 'user-actions';

        if (canEditUser) {
            const editButton = window.createAdminIconActionButton({
                className: 'action-btn edit-btn user-action-edit',
                title: usersT('users_action_edit_title', 'Edit user'),
                icon: Icons?.edit,
                fallback: '✎',
                dataset: { userId: user?.id },
            });
            actionsCell.appendChild(editButton);
        }

        // Account deletion follows the same owner/admin hierarchy as all other
        // account mutations and is never offered for the current account.
        if (canMutateUser) {
            const deleteButton = window.createAdminIconActionButton({
                className: 'action-btn delete-btn user-action-delete',
                title: usersT('users_action_delete_title', 'Delete user'),
                icon: Icons?.trash,
                fallback: '🗑',
                dataset: { userId: user?.id },
            });
            actionsCell.appendChild(deleteButton);
        }

        row.append(nameCell, emailCell, groupCell, roleCell, statusCell, lastActiveCell, actionsCell);
        fragment.appendChild(row);
    });

    userListContainer.appendChild(fragment);
}

function formatRelativeLastActive(value) {
    const date = parseUtcDate(value);
    if (!date) {
        return '—';
    }

    const locale = getCurrentLocale();
    const now = new Date();
    const diffMs = Math.max(0, now.getTime() - date.getTime());
    const seconds = Math.floor(diffMs / 1000);

    if (seconds < 60) {
        return formatRelativeTimeValue(0, 'second', locale, 'auto') || usersT('users_last_active_now', 'Now');
    }

    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
        return formatRelativeTimeValue(-minutes, 'minute', locale) || usersFormat('users_last_active_minutes_ago', '{count} minute(s) ago', { count: minutes });
    }

    const hours = Math.floor(minutes / 60);
    if (hours < 24) {
        return formatRelativeTimeValue(-hours, 'hour', locale) || usersFormat('users_last_active_hours_ago', '{count} hour(s) ago', { count: hours });
    }

    const days = Math.floor(hours / 24);
    if (days < 14) {
        return formatRelativeTimeValue(-days, 'day', locale) || usersFormat('users_last_active_days_ago', '{count} day(s) ago', { count: days });
    }

    try {
        return new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'long' }).format(date);
    } catch (_) {
        const day = date.getDate();
        const month = date.toLocaleDateString(undefined, { month: 'long' });
        return `${day}. ${month}`;
    }
}

function bindUserListActions() {
    if (!userListContainer || userListContainer.dataset.interactionBound === 'true') {
        return;
    }
    userListContainer.addEventListener('click', handleUserListClick);
    userListContainer.addEventListener('keydown', handleUserListKeydown);
    userListContainer.dataset.interactionBound = 'true';
}

function bindEditUserReasonModalEvents() {
    if (editUserReasonCancelButton && editUserReasonCancelButton.dataset.bound !== 'true') {
        editUserReasonCancelButton.addEventListener('click', () => closeEditUserReasonModal());
        editUserReasonCancelButton.dataset.bound = 'true';
    }

    if (editUserReasonOverlay && editUserReasonOverlay.dataset.bound !== 'true') {
        editUserReasonOverlay.addEventListener('click', (event) => {
            if (event.target === editUserReasonOverlay) {
                closeEditUserReasonModal();
            }
        });
        editUserReasonOverlay.dataset.bound = 'true';
    }

    if (editUserReasonOverlay && editUserReasonOverlay.dataset.keydownBound !== 'true') {
        const handleEditReasonKeydown = (event) => {
            if (
                event.key === 'Escape'
                && !editUserReasonOverlay.hidden
            ) {
                event.preventDefault();
                closeEditUserReasonModal();
            }
        };
        document.addEventListener('keydown', handleEditReasonKeydown);
        editUserReasonOverlay.dataset.keydownBound = 'true';
    }

    if (editUserReasonForm && editUserReasonForm.dataset.bound !== 'true') {
        editUserReasonForm.addEventListener('submit', handleEditUserReasonSubmit);
        editUserReasonForm.dataset.bound = 'true';
    }
}

function handleEditUserReasonSubmit(event) {
    event.preventDefault();

    const rawReason = editUserReasonInput?.value || '';
    const reason = rawReason.trim();
    if (reason.length < 3) {
        notifyError?.(
            usersT(
                'admin_user_settings_profile_reason_required',
                'Enter a short reason before loading sensitive profile details.'
            )
        );
        editUserReasonInput?.focus();
        editUserReasonInput?.setSelectionRange(0, rawReason.length);
        return;
    }

    if (reason.length > 255) {
        notifyError?.(
            usersT(
                'users_edit_reason_too_long',
                'Reason must be 255 characters or fewer.'
            )
        );
        editUserReasonInput?.focus();
        editUserReasonInput?.setSelectionRange(0, rawReason.length);
        return;
    }

    const user = pendingEditUser;
    closeEditUserReasonModal({ restoreFocus: false });
    if (!user) {
        notifyError?.(usersT('users_error_user_not_found', 'Unable to find that user.'));
        return;
    }

    openUserEditorWithReason(user, reason);
}

function bindUserImportExportEvents() {
    if (!userExportJobsController && typeof window.createAdminExportJobsController === 'function') {
        userExportJobsController = window.createAdminExportJobsController({
            dom: {
                triggerButton: userExportButton,
                overlay: userExportJobsOverlay,
                closeButton: userExportJobsClose,
                cancelButton: userExportJobsCancel,
                createButton: userExportCreateButton,
                refreshButton: userExportJobsRefreshButton,
                list: userExportJobsList,
                status: userExportJobsStatus,
            },
            endpoints: {
                list: '/api/v1/admin/users/export/jobs?limit=50',
                create: '/api/v1/admin/users/export/jobs',
                buildCreateRequest: () => {
                    const reason = String(userExportReason?.value || '').trim();
                    if (reason.length < 3) {
                        userExportReason?.focus();
                        throw new Error(usersT(
                            'users_export_reason_required',
                            'Enter a reason of at least three characters.'
                        ));
                    }
                    return {
                        url: '/api/v1/admin/users/export/jobs',
                        init: {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ reason, user_ids: [] }),
                        },
                    };
                },
                download: (jobId) => `/api/v1/admin/users/export/jobs/${encodeURIComponent(jobId)}/download`,
                delete: (jobId) => `/api/v1/admin/users/export/jobs/${encodeURIComponent(jobId)}`,
            },
            filenamePrefix: 'admin-users',
            fileExtension: 'zip',
            translate: usersT,
            format: usersFormat,
            logPrefix: 'Failed to handle user export jobs',
            keys: {
                notAvailable: 'users_export_jobs_not_available',
                empty: 'users_export_jobs_empty',
                loadFailed: 'users_export_jobs_failed',
                refreshed: 'users_export_jobs_refreshed',
                refreshing: 'users_export_jobs_refreshing',
                queueing: 'users_export_busy_queueing',
                createFailed: 'users_export_failed',
                queuedStatus: 'users_export_job_queued_status',
                queuedSuccess: 'users_export_job_queued_success',
                statusQueued: 'users_export_job_status_queued',
                statusRunning: 'users_export_job_status_running',
                statusSuccess: 'users_export_job_status_success',
                statusFailed: 'users_export_job_status_failed',
                statusDeleted: 'users_export_job_status_deleted',
                statusExpired: 'users_export_job_status_expired',
                createdLabel: 'users_export_job_created_label',
                finishedLabel: 'users_export_job_finished_label',
                expiresLabel: 'users_export_job_expires_label',
                sizeLabel: 'users_export_job_size_label',
                errorLabel: 'users_export_job_error_label',
                downloadButton: 'users_export_job_download_btn',
                deleteButton: 'users_export_job_delete_btn',
                downloadFailed: 'users_export_job_download_failed',
                downloadSuccess: 'users_export_job_download_success',
                deleteTitle: 'users_export_job_delete_title',
                deleteDesc: 'users_export_job_delete_desc',
                deleteFailed: 'users_export_job_delete_failed',
                deleteSuccess: 'users_export_job_delete_success',
            },
            metadataFields: [
                {
                    path: 'manifest.user_count',
                    labelKey: 'users_export_job_users_label',
                    labelFallback: 'Users',
                },
                {
                    path: 'manifest.user_files_count',
                    labelKey: 'users_export_job_file_bundles_label',
                    labelFallback: 'File bundles',
                    formatValue: (value) => Number.isFinite(Number(value)) ? Number(value) : null,
                },
            ],
        });
        userExportJobsController.bind();
    }

    if (userExportJobsController) {
        if (userImportButton && userImportButton.dataset.bound !== 'true') {
            userImportButton.addEventListener('click', () => userImportInput?.click());
            userImportButton.dataset.bound = 'true';
        }

        if (userImportInput && userImportInput.dataset.bound !== 'true') {
            userImportInput.addEventListener('change', handleImportUsersInput);
            userImportInput.dataset.bound = 'true';
        }

        if (userImportOverlay && userImportOverlay.dataset.bound !== 'true') {
            userImportOverlay.addEventListener('click', (event) => {
                if (event.target === userImportOverlay) {
                    closeUserImportModal();
                }
            });
            userImportOverlay.dataset.bound = 'true';
        }

        if (userImportClose && userImportClose.dataset.bound !== 'true') {
            userImportClose.addEventListener('click', closeUserImportModal);
            userImportClose.dataset.bound = 'true';
        }

        if (userImportCancel && userImportCancel.dataset.bound !== 'true') {
            userImportCancel.addEventListener('click', closeUserImportModal);
            userImportCancel.dataset.bound = 'true';
        }

        if (userImportSelectAll && userImportSelectAll.dataset.bound !== 'true') {
            userImportSelectAll.addEventListener('change', toggleUserImportSelectAll);
            userImportSelectAll.dataset.bound = 'true';
        }

        if (userImportConfirm && userImportConfirm.dataset.bound !== 'true') {
            userImportConfirm.addEventListener('click', submitUserImport);
            userImportConfirm.dataset.bound = 'true';
        }

        if (userImportDefaultPassword && userImportDefaultPassword.dataset.bound !== 'true') {
            userImportDefaultPassword.addEventListener('input', () => setUserImportStatus());
            userImportDefaultPassword.dataset.bound = 'true';
        }

        return;
    }

    if (userExportButton && userExportButton.dataset.bound !== 'true') {
        userExportButton.addEventListener('click', openUserExportJobsModal);
        userExportButton.dataset.bound = 'true';
    }

    if (userExportCreateButton && userExportCreateButton.dataset.bound !== 'true') {
        userExportCreateButton.addEventListener('click', queueUserExportJob);
        userExportCreateButton.dataset.bound = 'true';
    }

    if (userExportJobsRefreshButton && userExportJobsRefreshButton.dataset.bound !== 'true') {
        userExportJobsRefreshButton.addEventListener('click', () => refreshUserExportJobs());
        userExportJobsRefreshButton.dataset.bound = 'true';
    }

    if (userExportJobsList && userExportJobsList.dataset.bound !== 'true') {
        userExportJobsList.addEventListener('click', handleUserExportJobAction);
        userExportJobsList.dataset.bound = 'true';
    }

    if (userExportJobsOverlay && userExportJobsOverlay.dataset.bound !== 'true') {
        userExportJobsOverlay.addEventListener('click', (event) => {
            if (event.target === userExportJobsOverlay) {
                closeUserExportJobsModal();
            }
        });
        userExportJobsOverlay.dataset.bound = 'true';
    }

    if (userExportJobsClose && userExportJobsClose.dataset.bound !== 'true') {
        userExportJobsClose.addEventListener('click', closeUserExportJobsModal);
        userExportJobsClose.dataset.bound = 'true';
    }

    if (userExportJobsCancel && userExportJobsCancel.dataset.bound !== 'true') {
        userExportJobsCancel.addEventListener('click', closeUserExportJobsModal);
        userExportJobsCancel.dataset.bound = 'true';
    }

    if (userExportJobsOverlay && userExportJobsOverlay.dataset.keydownBound !== 'true') {
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !userExportJobsOverlay.hidden) {
                closeUserExportJobsModal();
            }
        });
        userExportJobsOverlay.dataset.keydownBound = 'true';
    }

    if (userImportButton && userImportButton.dataset.bound !== 'true') {
        userImportButton.addEventListener('click', () => userImportInput?.click());
        userImportButton.dataset.bound = 'true';
    }

    if (userImportInput && userImportInput.dataset.bound !== 'true') {
        userImportInput.addEventListener('change', handleImportUsersInput);
        userImportInput.dataset.bound = 'true';
    }

    if (userImportOverlay && userImportOverlay.dataset.bound !== 'true') {
        userImportOverlay.addEventListener('click', (event) => {
            if (event.target === userImportOverlay) {
                closeUserImportModal();
            }
        });
        userImportOverlay.dataset.bound = 'true';
    }

    if (userImportClose && userImportClose.dataset.bound !== 'true') {
        userImportClose.addEventListener('click', closeUserImportModal);
        userImportClose.dataset.bound = 'true';
    }

    if (userImportCancel && userImportCancel.dataset.bound !== 'true') {
        userImportCancel.addEventListener('click', closeUserImportModal);
        userImportCancel.dataset.bound = 'true';
    }

    if (userImportConfirm && userImportConfirm.dataset.bound !== 'true') {
        userImportConfirm.addEventListener('click', submitUserImport);
        userImportConfirm.dataset.bound = 'true';
    }

    if (userImportSelectAll && userImportSelectAll.dataset.bound !== 'true') {
        userImportSelectAll.addEventListener('change', toggleUserImportSelectAll);
        userImportSelectAll.dataset.bound = 'true';
    }

    if (userImportDefaultPassword && userImportDefaultPassword.dataset.bound !== 'true') {
        userImportDefaultPassword.addEventListener('input', () => setUserImportStatus());
        userImportDefaultPassword.dataset.bound = 'true';
    }
}

function handleUserListClick(event) {
    const deleteButton = event.target.closest('.user-action-delete');
    if (deleteButton) {
        handleDeleteUserClick(deleteButton);
        return;
    }

    const editButton = event.target.closest('.user-action-edit');
    if (editButton) {
        handleEditUserClick(editButton);
        return;
    }

    const roleCell = event.target.closest('.user-role');
    if (roleCell) {
        handleRoleCellClick(roleCell);
        return;
    }

    const statusCell = event.target.closest('.user-status');
    if (statusCell) {
        handleStatusCellClick(statusCell);
        return;
    }

    // Empty space in the actions cell must not trigger the row shortcut.
    if (event.target.closest('.user-actions')) {
        return;
    }

    const row = event.target.closest('.user-row');
    if (row?.dataset.userId && row.dataset.canEdit === 'true') {
        openUserEditById(row.dataset.userId);
    }
}

/**
 * Provide keyboard parity for the clickable user row.
 *
 * Interactive cells and buttons keep their native/specialized behavior, while
 * Enter or Space on the focused row opens the normal audited edit flow.
 */
function handleUserListKeydown(event) {
    if (event.key !== 'Enter' && event.key !== ' ') {
        return;
    }

    if (
        event.target.closest('.user-actions')
        || event.target.closest('.user-role')
        || event.target.closest('.user-status')
    ) {
        return;
    }

    const row = event.target.closest('.user-row');
    if (!row?.dataset.userId || row.dataset.canEdit !== 'true') {
        return;
    }

    event.preventDefault();
    openUserEditById(row.dataset.userId);
}

async function handleRoleCellClick(roleCell) {
    if (
        !roleCell
        || roleCell.dataset.loading === 'true'
        || roleCell.dataset.mutable !== 'true'
    ) {
        return;
    }
    const row = roleCell.closest('.user-row');
    const userId = row?.dataset.userId;
    if (!userId) {
        return;
    }

    const currentRole = roleCell.dataset.role || '';
    const nextRole = getNextRole(currentRole);
    if (!nextRole) {
        return;
    }

    roleCell.dataset.loading = 'true';
    roleCell.classList.add('user-role-loading');

    try {
        await requestUserRoleUpdate(userId, nextRole);
        roleCell.dataset.role = nextRole;
        setRoleBadgeState(roleCell, nextRole);
        updateUserCacheRole(userId, nextRole);
        if (typeof notifySuccess === 'function') {
            notifySuccess(usersFormat('users_role_update_success', 'Role updated to {role}.', { role: formatRoleLabel(nextRole) }));
        }
    } catch (error) {
        console.error('Failed to update user role', error);
        setRoleBadgeState(roleCell, currentRole);
        if (typeof notifyError === 'function') {
            notifyError(error?.message || usersT('users_role_update_failed', 'Failed to update user role.'));
        }
    } finally {
        roleCell.dataset.loading = 'false';
        roleCell.classList.remove('user-role-loading');
    }
}

async function handleStatusCellClick(statusCell) {
    if (
        !statusCell
        || statusCell.dataset.loading === 'true'
        || statusCell.dataset.mutable !== 'true'
    ) {
        return;
    }

    const row = statusCell.closest('.user-row');
    const userId = row?.dataset.userId;
    if (!userId) {
        return;
    }

    const currentActive = statusCell.dataset.active === 'true';
    const nextActive = !currentActive;

    statusCell.dataset.loading = 'true';
    statusCell.classList.add('user-status-loading');

    try {
        await requestUserActivationUpdate(userId, nextActive);
        setStatusCellState(statusCell, nextActive);
        updateUserCacheStatus(userId, nextActive);
        if (typeof notifySuccess === 'function') {
            notifySuccess(usersFormat('users_status_update_success', 'User {status}.', {
                status: nextActive
                    ? usersT('users_status_activated', 'activated')
                    : usersT('users_status_deactivated', 'deactivated'),
            }));
        }
    } catch (error) {
        console.error('Failed to update user status', error);
        setStatusCellState(statusCell, currentActive);
        if (typeof notifyError === 'function') {
            notifyError(error?.message || usersT('users_status_update_failed', 'Failed to update user status.'));
        }
    } finally {
        statusCell.dataset.loading = 'false';
        statusCell.classList.remove('user-status-loading');
    }
}

function getNextRole(role) {
    const normalized = normalizeRole(role);
    // Only the owner may grant administrative authority. Regular admins keep
    // the convenient role toggle, but it cycles only between non-admin roles.
    const availableRoles = currentUserRole === 'owner'
        ? ROLE_SEQUENCE
        : ROLE_SEQUENCE.filter((candidate) => candidate !== 'admin');
    const index = availableRoles.indexOf(normalized);
    if (index === -1) {
        return null;
    }
    return availableRoles[(index + 1) % availableRoles.length];
}

function normalizeBackendErrorDetail(detail) {
    if (typeof detail !== 'string') {
        return '';
    }

    const normalized = detail.trim();
    if (!normalized) {
        return '';
    }

    return normalized.replace(/\.$/, '');
}

function translateBackendError(detail, errorType = '') {
    if (typeof detail !== 'string') {
        return '';
    }

    const trimmedDetail = detail.trim();
    if (!trimmedDetail) {
        return '';
    }

    const normalizedDetail = normalizeBackendErrorDetail(trimmedDetail);
    let localizationKey = '';
    if (normalizedDetail === 'The owner account cannot be modified by another administrator') {
        localizationKey = 'users_account_owner_protected';
    } else if (normalizedDetail === 'Only the owner can modify administrator accounts') {
        localizationKey = 'users_account_admin_owner_only';
    }
    if (!localizationKey && errorType === 'role-update') {
        switch (normalizedDetail) {
            case 'Admins cannot change their own role':
                localizationKey = 'users_role_update_forbidden_self';
                break;
            case 'Cannot remove or deactivate the last active admin':
                localizationKey = 'users_admin_last_active_guard';
                break;
            case 'Invalid role':
                localizationKey = 'users_role_update_invalid_role';
                break;
            case 'User not found':
                localizationKey = 'users_role_update_user_not_found';
                break;
            default:
                break;
        }
    } else if (!localizationKey && errorType === 'status-update') {
        switch (normalizedDetail) {
            case 'Admins cannot change their own activation status':
                localizationKey = 'users_status_update_forbidden_self';
                break;
            case 'Cannot remove or deactivate the last active admin':
                localizationKey = 'users_admin_last_active_guard';
                break;
            case 'User not found':
                localizationKey = 'users_status_update_user_not_found';
                break;
            default:
                break;
        }
    }

    if (!localizationKey) {
        return trimmedDetail;
    }

    return usersT(localizationKey, trimmedDetail);
}

function translateRoleUpdateError(detail) {
    return translateBackendError(detail, 'role-update');
}

function translateStatusUpdateError(detail) {
    return translateBackendError(detail, 'status-update');
}

async function requestUserRoleUpdate(userId, role) {
    const response = await window.authedFetch('/api/v1/admin/user/role/change', {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ user_id: userId, role }),
    });
    let payload = null;
    const isJsonResponse = response.headers.get('content-type')?.includes('application/json');
    if (isJsonResponse) {
        try {
            payload = await response.json();
        } catch (error) {
            if (response.ok) {
                throw error;
            }
        }
    }
    if (!response.ok) {
        const translatedDetail = translateRoleUpdateError(payload?.detail);
        const message = translatedDetail || usersT('users_role_update_failed', 'Failed to update user role.');
        throw new Error(message);
    }
    return payload ?? {};
}

function setRoleBadgeState(roleCell, role) {
    const badge = roleCell.querySelector('.role-badge') || document.createElement('span');
    if (!badge.classList.contains('role-badge')) {
        badge.className = 'role-badge';
        roleCell.appendChild(badge);
    }

    const normalized = normalizeRole(role);
    const classList = ['role-badge'];
    if (normalized) {
        classList.push(`role-${normalized}`);
    }
    badge.className = classList.join(' ');
    badge.textContent = formatRoleLabel(normalized) || '—';
    roleCell.dataset.role = normalized;
}

function setStatusCellState(statusCell, isActive) {
    const normalized = Boolean(isActive);
    let indicator = statusCell.querySelector('.status-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        statusCell.appendChild(indicator);
    }

    let label = statusCell.querySelector('.user-status-label');
    if (!label) {
        label = document.createElement('span');
        label.className = 'user-status-label';
        statusCell.appendChild(label);
    }

    indicator.className = `status-indicator ${normalized ? 'status-active' : 'status-inactive'}`;
    indicator.innerHTML = normalized ? (Icons?.check || '✓') : (Icons?.close || '✕');
    label.textContent = normalized
        ? usersT('users_status_active', 'Active')
        : usersT('users_status_inactive', 'Inactive');
    statusCell.dataset.active = normalized ? 'true' : 'false';
}

function updateUserCacheRole(userId, role) {
    if (!Array.isArray(usersCache) || !usersCache.length) {
        return;
    }
    const index = usersCache.findIndex((user) => user?.id === userId);
    if (index === -1) {
        return;
    }
    usersCache[index] = {
        ...usersCache[index],
        role,
    };
}

function updateUserCacheStatus(userId, isActive) {
    if (!Array.isArray(usersCache) || !usersCache.length) {
        return;
    }
    const index = usersCache.findIndex((user) => user?.id === userId);
    if (index === -1) {
        return;
    }
    usersCache[index] = {
        ...usersCache[index],
        is_active: isActive,
    };
}

async function requestUserActivationUpdate(userId, isActive) {
    const response = await window.authedFetch('/api/v1/admin/user/active', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            user_id: userId,
            value: Boolean(isActive),
        }),
    });
    let payload = null;
    const isJsonResponse = response.headers.get('content-type')?.includes('application/json');
    if (isJsonResponse) {
        try {
            payload = await response.json();
        } catch (error) {
            if (response.ok) {
                throw error;
            }
        }
    }
    if (!response.ok) {
        const translatedDetail = translateStatusUpdateError(payload?.detail);
        const message = translatedDetail || usersT('users_status_update_failed', 'Failed to update user status.');
        throw new Error(message);
    }
    return payload ?? {};
}

function normalizeRole(role) {
    if (typeof role !== 'string') {
        return '';
    }
    return role.trim().toLowerCase();
}

function formatRoleLabel(role) {
    if (!role) {
        return '';
    }
    const normalized = role.toLowerCase();
    if (normalized === 'pending') {
        return usersT('schema_login_ldap_ldap_default_role_pending', 'Pending');
    }
    if (normalized === 'user') {
        return usersT('schema_login_ldap_ldap_default_role_user', 'User');
    }
    if (normalized === 'admin') {
        return usersT('schema_login_ldap_ldap_default_role_admin', 'Admin');
    }
    if (normalized === 'owner') {
        return usersT('users_role_owner', 'Owner');
    }
    return role.charAt(0).toUpperCase() + role.slice(1);
}

function openDeleteUserModal(userId, userName = '') {
    if (!deleteUserOverlay) {
        return;
    }

    deleteUserId = userId;
    deleteUserName = userName;
    if (deleteUserMessage) {
        deleteUserMessage.textContent = userName
            ? usersFormat('modal_delete_user_named_desc', 'Are you sure you want to delete "{name}"?', { name: userName })
            : defaultDeleteUserMessage;
    }
    if (deleteUserPrimaryText) {
        deleteUserPrimaryText.textContent = defaultDeleteUserPrimaryText;
    }
    deleteUserOverlay.hidden = false;
    deleteUserOverlay.classList.add('active');
    deleteUserCancelButton?.focus();
}

function closeDeleteUserModal() {
    deleteUserId = null;
    deleteUserName = '';
    if (deleteUserMessage) {
        deleteUserMessage.textContent = defaultDeleteUserMessage;
    }
    if (deleteUserPrimaryButton) {
        deleteUserPrimaryButton.disabled = false;
    }
    if (deleteUserPrimaryText) {
        deleteUserPrimaryText.textContent = defaultDeleteUserPrimaryText;
    }
    if (deleteUserOverlay) {
        deleteUserOverlay.classList.remove('active');
        deleteUserOverlay.hidden = true;
    }
}

function bindDeleteUserModalEvents() {
    if (deleteUserCancelButton && deleteUserCancelButton.dataset.bound !== 'true') {
        deleteUserCancelButton.addEventListener('click', closeDeleteUserModal);
        deleteUserCancelButton.dataset.bound = 'true';
    }

    if (deleteUserOverlay && deleteUserOverlay.dataset.bound !== 'true') {
        deleteUserOverlay.addEventListener('click', (event) => {
            if (event.target === deleteUserOverlay) {
                closeDeleteUserModal();
            }
        });
        deleteUserOverlay.dataset.bound = 'true';
    }

    if (deleteUserOverlay && deleteUserOverlay.dataset.keydownBound !== 'true') {
        const handleOverlayKeydown = (event) => {
            if (event.key === 'Escape' && (!deleteUserOverlay.hidden || deleteUserOverlay.classList.contains('active'))) {
                event.preventDefault();
                closeDeleteUserModal();
            }
        };
        document.addEventListener('keydown', handleOverlayKeydown);
        deleteUserOverlay.dataset.keydownBound = 'true';
    }

    if (deleteUserPrimaryButton && deleteUserPrimaryButton.dataset.bound !== 'true') {
        deleteUserPrimaryButton.addEventListener('click', async () => {
            if (!deleteUserId || deleteUserPrimaryButton.disabled) {
                return;
            }

            deleteUserPrimaryButton.disabled = true;
            if (typeof window.ensureSecurityStepUp !== 'function') {
                notifyError?.(usersT('step_up_methods_load_failed', 'Verification methods could not be loaded. Close this dialog and try again.'));
                deleteUserPrimaryButton.disabled = false;
                return;
            }
            if (!await window.ensureSecurityStepUp()) {
                deleteUserPrimaryButton.disabled = false;
                return;
            }

            const originalIconHtml = deleteUserPrimaryButton.querySelector('svg')?.outerHTML || '';
            const restoreIcon = () => {
                if (!originalIconHtml) {
                    return;
                }
                const currentIcon = deleteUserPrimaryButton.querySelector('svg');
                if (currentIcon) {
                    currentIcon.outerHTML = originalIconHtml;
                } else {
                    deleteUserPrimaryButton.insertAdjacentHTML('afterbegin', originalIconHtml);
                }
            };

            deleteUserPrimaryButton.dataset.loading = 'true';
            if (deleteUserPrimaryText) {
                deleteUserPrimaryText.textContent = usersT('users_delete_busy', 'Deleting...');
            }

            const currentIcon = deleteUserPrimaryButton.querySelector('svg');
            if (currentIcon) {
                currentIcon.outerHTML = Icons.refresh;
            }

            try {
                await requestUserDeletion(deleteUserId);
                notifySuccess?.(usersT('users_delete_success', 'User deleted successfully.'));
                removeUserFromCache(deleteUserId);
                renderPaginatedUsersList();
                if (typeof loadDeletedUsersList === 'function') {
                    try {
                        await loadDeletedUsersList();
                    } catch (refreshError) {
                        console.warn('Failed to refresh deleted users list', refreshError);
                    }
                }
                restoreIcon();
                closeDeleteUserModal();
            } catch (error) {
                notifyError?.(error?.message || usersT('users_delete_failed', 'Failed to delete user.'));
                deleteUserPrimaryButton.disabled = false;
                restoreIcon();
                if (deleteUserPrimaryText) {
                    deleteUserPrimaryText.textContent = defaultDeleteUserPrimaryText;
                }
            } finally {
                delete deleteUserPrimaryButton.dataset.loading;
            }
        });
        deleteUserPrimaryButton.dataset.bound = 'true';
    }
}

async function requestUserDeletion(userId) {
    const response = await window.authedFetch('/api/v1/admin/user/delete', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ user_id: userId }),
    });

    let payload = null;
    const isJsonResponse = response.headers.get('content-type')?.includes('application/json');
    if (isJsonResponse) {
        try {
            payload = await response.json();
        } catch (error) {
            if (response.ok) {
                throw error;
            }
        }
    }
    if (!response.ok) {
        const message = payload?.detail || usersT('users_delete_failed', 'Failed to delete user.');
        throw new Error(message);
    }
    return payload ?? {};
}

function removeUserFromCache(userId) {
    if (!Array.isArray(usersCache) || !usersCache.length) {
        return;
    }
    const index = usersCache.findIndex((user) => user?.id === userId);
    if (index === -1) {
        return;
    }
    usersCache.splice(index, 1);
}

bindDeleteUserModalEvents();


// ============================================================================
// Canonical selected-user archive export
// ============================================================================

const exportSingleUserBundleButton = document.getElementById('exportSingleUserBundleButton');
const userChatTransferOverlay = document.getElementById('userChatTransferOverlay');
const userChatTransferTitle = document.getElementById('userChatTransferTitle');
const userChatTransferSubtitle = document.getElementById('userChatTransferSubtitle');
const userChatTransferClose = document.getElementById('userChatTransferClose');
const userChatTransferSearch = document.getElementById('userChatTransferSearch');
const userChatTransferMeta = document.getElementById('userChatTransferMeta');
const userChatTransferReasonRow = document.getElementById('userChatTransferReasonRow');
const userChatTransferReason = document.getElementById('userChatTransferReason');
const userChatTransferReasonDescription = document.getElementById('userChatTransferReasonDescription');
const userChatTransferList = document.getElementById('userChatTransferList');
const userChatTransferStatus = document.getElementById('userChatTransferStatus');
const userChatTransferCancel = document.getElementById('userChatTransferCancel');
const userChatTransferConfirm = document.getElementById('userChatTransferConfirm');
const userChatTransferConfirmText = document.getElementById('userChatTransferConfirmText');

const userChatTransferState = {
    selectedUserId: null,
    availableUsers: [],
    filteredUsers: [],
    totalUsers: 0,
    offset: 0,
    hasMore: false,
    isLoading: false,
    searchQuery: '',
};
const USER_CHAT_TRANSFER_PAGE_SIZE = 50;
const USER_CHAT_TRANSFER_SCROLL_THRESHOLD = 96;
let userChatTransferSearchDebounceTimer = null;
let userChatTransferRequestId = 0;


function extractWarningIdentifiers(warnings) {
    if (!Array.isArray(warnings) || !warnings.length) {
        return [];
    }

    const values = new Set();
    warnings.forEach((warning) => {
        if (!warning || typeof warning !== 'object') {
            return;
        }
        let foundIdentifier = false;

        if (typeof warning.email === 'string' && warning.email.trim()) {
            values.add(warning.email.trim());
            foundIdentifier = true;
        }

        if (Array.isArray(warning.missing_emails)) {
            const emails = warning.missing_emails
                .filter((entry) => typeof entry === 'string' && entry.trim())
                .map((entry) => entry.trim());
            if (emails.length) {
                foundIdentifier = true;
                emails.forEach((entry) => values.add(entry));
            }
        }

        if (!foundIdentifier && typeof warning.user_id === 'string' && warning.user_id.trim()) {
            values.add(`user_id:${warning.user_id.trim()}`);
        }
    });

    return Array.from(values);
}

function buildWarningSummary(warnings) {
    const warningCount = Array.isArray(warnings) ? warnings.length : 0;
    if (!warningCount) {
        return '';
    }

    const identifiers = extractWarningIdentifiers(warnings);
    if (!identifiers.length) {
        return usersFormat('users_import_summary_warnings', '{count} warning(s) (email not found).', { count: warningCount });
    }

    const preview = identifiers.slice(0, 5).join(', ');
    const suffix = identifiers.length > 5 ? ', …' : '';
    return usersFormat(
        'users_import_summary_warnings_with_ids',
        '{count} warning(s): email not found for {identifiers}{suffix}.',
        {
            count: warningCount,
            identifiers: preview,
            suffix,
        },
    );
}


function buildTimestampedFilename(prefix, extension = 'json') {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    return `${prefix}-${timestamp}.${extension}`;
}


function downloadBlobPayload(blob, filename) {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(link.href);
}

function extractFilenameFromDisposition(contentDisposition) {
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
}

async function fetchJsonResponseOrThrow(response, translationKey, fallback) {
    if (response.ok) {
        return response.json();
    }
    let message = usersT(translationKey, fallback);
    try {
        const errorData = await response.json();
        if (errorData?.detail) {
            message = errorData.detail;
        }
    } catch (_) {}
    throw new Error(message);
}


function buildEntitySummary(createdCount, createdLabelSingular, createdLabelPlural, skippedCount = 0, skippedLabelSingular = createdLabelSingular, skippedLabelPlural = createdLabelPlural) {
    const parts = [];
    if (createdCount) {
        parts.push(usersFormat('users_import_summary_created', 'Imported {count} {itemLabel}.', {
            count: createdCount,
            itemLabel: createdCount === 1 ? createdLabelSingular : createdLabelPlural,
        }));
    }
    if (skippedCount) {
        parts.push(usersFormat('users_import_summary_skipped', 'Skipped {count} existing {itemLabel}.', {
            count: skippedCount,
            itemLabel: skippedCount === 1 ? skippedLabelSingular : skippedLabelPlural,
        }));
    }
    return parts;
}

function setUserChatTransferStatus(message = '', type = '') {
    if (!userChatTransferStatus) {
        return;
    }
    if (!message) {
        userChatTransferStatus.hidden = true;
        userChatTransferStatus.textContent = '';
        userChatTransferStatus.className = 'provider-import-status';
        return;
    }
    userChatTransferStatus.hidden = false;
    userChatTransferStatus.textContent = message;
    userChatTransferStatus.className = `provider-import-status ${type}`.trim();
}

function resetUserChatTransferPaging(searchQuery = '') {
    userChatTransferState.availableUsers = [];
    userChatTransferState.filteredUsers = [];
    userChatTransferState.totalUsers = 0;
    userChatTransferState.offset = 0;
    userChatTransferState.hasMore = false;
    userChatTransferState.searchQuery = searchQuery;
}

function appendUserChatTransferUsers(users) {
    const seenIds = new Set(userChatTransferState.availableUsers.map((user) => String(user.id)));
    users.forEach((user) => {
        if (!user || seenIds.has(String(user.id))) {
            return;
        }
        seenIds.add(String(user.id));
        userChatTransferState.availableUsers.push(user);
    });
    userChatTransferState.filteredUsers = userChatTransferState.availableUsers;
}

async function loadUserChatTransferPage({ reset = false } = {}) {
    if (userChatTransferState.isLoading && !reset) {
        return;
    }

    const requestId = ++userChatTransferRequestId;
    if (reset) {
        resetUserChatTransferPaging(userChatTransferState.searchQuery);
    }

    userChatTransferState.isLoading = true;
    renderUserChatTransferList(userChatTransferState.filteredUsers);

    const params = new URLSearchParams({
        limit: String(USER_CHAT_TRANSFER_PAGE_SIZE),
        offset: String(userChatTransferState.offset),
    });
    if (userChatTransferState.searchQuery) {
        params.set('search', userChatTransferState.searchQuery);
    }

    try {
        const response = await window.authedFetch(`/api/v1/admin/users/picker?${params.toString()}`, {
            method: 'GET',
        });
        if (!response.ok) {
            throw new Error(usersT('users_fetch_failed', 'Failed to fetch users'));
        }

        const page = await response.json();
        if (requestId !== userChatTransferRequestId) {
            return;
        }

        const pageUsers = Array.isArray(page?.users) ? page.users : [];
        appendUserChatTransferUsers(pageUsers);
        userChatTransferState.totalUsers = Number.isFinite(Number(page?.total)) ? Number(page.total) : userChatTransferState.availableUsers.length;
        userChatTransferState.offset = Number.isFinite(Number(page?.offset))
            ? Number(page.offset) + pageUsers.length
            : userChatTransferState.availableUsers.length;
        userChatTransferState.hasMore = Boolean(page?.has_more);
        setUserChatTransferStatus();
    } catch (error) {
        if (requestId !== userChatTransferRequestId) {
            return;
        }
        console.error('Failed to load users for transfer modal', error);
        setUserChatTransferStatus(error?.message || usersT('users_fetch_failed', 'Failed to fetch users'), 'error');
    } finally {
        if (requestId === userChatTransferRequestId) {
            userChatTransferState.isLoading = false;
            renderUserChatTransferList(userChatTransferState.filteredUsers);
        }
    }
}

function closeUserChatTransferModal() {
    userChatTransferRequestId += 1;
    if (userChatTransferSearchDebounceTimer) {
        window.clearTimeout(userChatTransferSearchDebounceTimer);
        userChatTransferSearchDebounceTimer = null;
    }
    if (userChatTransferOverlay) {
        userChatTransferOverlay.hidden = true;
        userChatTransferOverlay.classList.remove('active');
        userChatTransferOverlay.setAttribute('aria-hidden', 'true');
    }
    if (userChatTransferSearch) {
        userChatTransferSearch.value = '';
    }
    if (userChatTransferList) {
        userChatTransferList.innerHTML = '';
    }
    if (userChatTransferMeta) {
        userChatTransferMeta.textContent = '';
    }
    if (userChatTransferReasonRow) {
        userChatTransferReasonRow.hidden = true;
    }
    if (userChatTransferReason) {
        userChatTransferReason.value = '';
    }
    setUserChatTransferStatus();
    userChatTransferState.selectedUserId = null;
    userChatTransferState.isLoading = false;
    resetUserChatTransferPaging();
    if (userChatTransferConfirm) {
        userChatTransferConfirm.disabled = true;
    }
}

function selectUserChatTransferUser(userId) {
    userChatTransferState.selectedUserId = userId;
    if (userChatTransferConfirm) {
        userChatTransferConfirm.disabled = !userId;
    }
    renderUserChatTransferList(userChatTransferState.filteredUsers);
}

function renderUserChatTransferList(users) {
    if (!userChatTransferList) {
        return;
    }
    userChatTransferList.innerHTML = '';
    userChatTransferList.setAttribute('aria-busy', userChatTransferState.isLoading ? 'true' : 'false');

    if (!users.length) {
        const empty = document.createElement('div');
        empty.className = 'provider-import-empty';
        empty.textContent = userChatTransferState.isLoading
            ? usersT('users_loading', 'Loading users…')
            : usersT('users_no_results', 'No users found.');
        userChatTransferList.appendChild(empty);
        return;
    }

    const fragment = document.createDocumentFragment();
    users.forEach((user) => {
        const fullName = [user.first_name, user.last_name].map((part) => (part || '').trim()).filter(Boolean).join(' ');
        const primaryLabel = fullName || user.email || usersT('users_import_unknown_email', 'Unknown email');
        const isSelected = String(userChatTransferState.selectedUserId) === String(user.id);

        const entry = document.createElement('button');
        entry.type = 'button';
        entry.className = `provider-import-entry${isSelected ? ' selected' : ''}`;
        entry.setAttribute('role', 'option');
        entry.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        entry.dataset.userId = String(user.id);
        entry.addEventListener('click', () => selectUserChatTransferUser(user.id));

        const marker = document.createElement('input');
        marker.type = 'radio';
        marker.name = 'userChatTransferSelection';
        marker.checked = isSelected;
        marker.tabIndex = -1;
        marker.setAttribute('aria-hidden', 'true');
        entry.appendChild(marker);

        const content = document.createElement('div');
        content.className = 'provider-import-entry-content';

        const title = document.createElement('p');
        title.className = 'provider-import-entry-title';
        title.textContent = primaryLabel;
        content.appendChild(title);

        const email = document.createElement('div');
        email.className = 'provider-import-entry-meta';
        email.textContent = usersFormat('users_import_meta_email', 'Email: {value}', { value: user.email || usersT('users_import_unknown_email', 'Unknown email') });
        content.appendChild(email);


        entry.appendChild(content);
        fragment.appendChild(entry);
    });
    userChatTransferList.appendChild(fragment);

    if (userChatTransferState.isLoading) {
        const loading = document.createElement('div');
        loading.className = 'provider-import-empty';
        loading.textContent = usersT('users_loading', 'Loading users…');
        userChatTransferList.appendChild(loading);
    }
}

function handleUserChatTransferSearchInput() {
    const query = String(userChatTransferSearch?.value || '').trim().toLowerCase();
    if (userChatTransferSearchDebounceTimer) {
        window.clearTimeout(userChatTransferSearchDebounceTimer);
    }
    userChatTransferSearchDebounceTimer = window.setTimeout(() => {
        userChatTransferState.selectedUserId = null;
        userChatTransferState.searchQuery = query;
        if (userChatTransferConfirm) {
            userChatTransferConfirm.disabled = true;
        }
        loadUserChatTransferPage({ reset: true });
    }, 250);
}

function handleUserChatTransferListScroll() {
    if (!userChatTransferList || userChatTransferState.isLoading || !userChatTransferState.hasMore) {
        return;
    }
    const distanceFromBottom = userChatTransferList.scrollHeight
        - userChatTransferList.scrollTop
        - userChatTransferList.clientHeight;
    if (distanceFromBottom <= USER_CHAT_TRANSFER_SCROLL_THRESHOLD) {
        loadUserChatTransferPage();
    }
}

function openUserChatTransferModal(config) {
    userChatTransferState.selectedUserId = null;
    userChatTransferState.searchQuery = '';
    resetUserChatTransferPaging();

    if (userChatTransferTitle) {
        userChatTransferTitle.textContent = usersT(config.modalExportTitleTranslationKey, config.modalExportTitleFallback);
    }
    if (userChatTransferSubtitle) {
        userChatTransferSubtitle.textContent = usersT(
            config.modalExportSubtitleTranslationKey,
            config.modalExportSubtitleFallback,
        );
    }
    if (userChatTransferConfirmText) {
        userChatTransferConfirmText.textContent = usersT(config.modalExportConfirmTranslationKey, config.modalExportConfirmFallback);
    }
    if (userChatTransferMeta) {
        userChatTransferMeta.textContent = '';
    }
    if (userChatTransferReasonRow) {
        userChatTransferReasonRow.hidden = false;
    }
    if (userChatTransferReason) {
        userChatTransferReason.value = '';
    }
    if (userChatTransferReasonDescription) {
        userChatTransferReasonDescription.textContent = usersT(
            config.reasonDescriptionTranslationKey,
            config.reasonDescriptionFallback,
        );
    }
    if (userChatTransferSearch) {
        userChatTransferSearch.value = '';
    }
    if (userChatTransferConfirm) {
        userChatTransferConfirm.disabled = true;
    }
    setUserChatTransferStatus();
    renderUserChatTransferList([]);

    if (userChatTransferOverlay) {
        userChatTransferOverlay.hidden = false;
        userChatTransferOverlay.classList.add('active');
        userChatTransferOverlay.setAttribute('aria-hidden', 'false');
    }
    userChatTransferSearch?.focus();
    loadUserChatTransferPage({ reset: true });
}








const singleUserArchiveExportConfig = {
    exportButton: exportSingleUserBundleButton,
    exportFailedTranslationKey: 'users_single_export_failed',
    exportFailedFallback: 'Failed to export user archive.',
    reasonDescriptionTranslationKey: 'users_export_reason_desc',
    reasonDescriptionFallback: 'Explain why this sensitive user archive is required. The reason is recorded in the audit log.',
    modalExportTitleTranslationKey: 'users_single_export_title',
    modalExportTitleFallback: 'Export User Archive',
    modalExportSubtitleTranslationKey: 'users_single_export_modal_subtitle',
    modalExportSubtitleFallback: "Choose which user's canonical portable archive you want to export.",
    modalExportConfirmTranslationKey: 'users_single_export_btn',
    modalExportConfirmFallback: 'Export User Archive',
};

async function handleUserChatTransferConfirm() {
    if (!userChatTransferState.selectedUserId) {
        return;
    }

    const selectedUser = userChatTransferState.availableUsers.find((user) => String(user.id) === String(userChatTransferState.selectedUserId));
    if (!selectedUser) {
        setUserChatTransferStatus(usersT('users_error_user_not_found', 'Unable to find that user.'), 'error');
        return;
    }

    try {
        setButtonLoadingState(
            userChatTransferConfirm,
            true,
            usersT('users_data_busy_exporting', 'Exporting…'),
        );
        setUserChatTransferStatus();

        const reason = String(userChatTransferReason?.value || '').trim();
        if (reason.length < 3) {
            const message = usersT('users_export_reason_required', 'Enter a reason of at least three characters.');
            setUserChatTransferStatus(message, 'error');
            userChatTransferReason?.focus();
            return;
        }
        const response = await window.authedFetch('/api/v1/admin/users/export/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                reason,
                user_ids: [String(selectedUser.id)],
            }),
        });
        await fetchJsonResponseOrThrow(
            response,
            'users_single_export_failed',
            'Failed to queue the user archive export.',
        );
        notifySuccess?.(usersT(
            'users_single_export_queued_success',
            'Complete user archive export queued.',
        ));
        closeUserChatTransferModal();
        openUserExportJobsModal();
    } catch (error) {
        console.error('User archive export failed', error);
        const message = error?.message || usersT('users_single_export_failed', 'Failed to export user archive.');
        setUserChatTransferStatus(message, 'error');
        notifyError?.(message);
    } finally {
        setButtonLoadingState(userChatTransferConfirm, false);
    }
}

async function handleExportUserDataSelection(config) {
    try {
        setButtonLoadingState(config.exportButton, true, usersT('users_data_busy_exporting', 'Exporting…'));
        openUserChatTransferModal(config);
    } catch (error) {
        console.error(config.exportFailedFallback, error);
        notifyError?.(error?.message || usersT(config.exportFailedTranslationKey, config.exportFailedFallback));
    } finally {
        setButtonLoadingState(config.exportButton, false);
    }
}

if (
    singleUserArchiveExportConfig.exportButton
    && singleUserArchiveExportConfig.exportButton.dataset.bound !== 'true'
) {
    singleUserArchiveExportConfig.exportButton.addEventListener(
        'click',
        () => handleExportUserDataSelection(singleUserArchiveExportConfig),
    );
    singleUserArchiveExportConfig.exportButton.dataset.bound = 'true';
}

if (userChatTransferOverlay && userChatTransferOverlay.dataset.bound !== 'true') {
    userChatTransferOverlay.addEventListener('click', (event) => {
        if (event.target === userChatTransferOverlay) {
            closeUserChatTransferModal();
        }
    });
    userChatTransferOverlay.dataset.bound = 'true';
}

if (userChatTransferClose && userChatTransferClose.dataset.bound !== 'true') {
    userChatTransferClose.addEventListener('click', closeUserChatTransferModal);
    userChatTransferClose.dataset.bound = 'true';
}

if (userChatTransferCancel && userChatTransferCancel.dataset.bound !== 'true') {
    userChatTransferCancel.addEventListener('click', closeUserChatTransferModal);
    userChatTransferCancel.dataset.bound = 'true';
}

if (userChatTransferConfirm && userChatTransferConfirm.dataset.bound !== 'true') {
    userChatTransferConfirm.addEventListener('click', handleUserChatTransferConfirm);
    userChatTransferConfirm.dataset.bound = 'true';
}

if (userChatTransferSearch && userChatTransferSearch.dataset.bound !== 'true') {
    userChatTransferSearch.addEventListener('input', handleUserChatTransferSearchInput);
    userChatTransferSearch.dataset.bound = 'true';
}

if (userChatTransferList && userChatTransferList.dataset.scrollBound !== 'true') {
    userChatTransferList.addEventListener('scroll', handleUserChatTransferListScroll, { passive: true });
    userChatTransferList.dataset.scrollBound = 'true';
}

if (userChatTransferOverlay && userChatTransferOverlay.dataset.keydownBound !== 'true') {
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !userChatTransferOverlay.hidden) {
            closeUserChatTransferModal();
        }
    });
    userChatTransferOverlay.dataset.keydownBound = 'true';
}

window.teardownUsersSettingsPage = () => {
    usersSettingsController?.teardown();
    usersSettingsController = null;
    if (userExportJobsRefreshTimer) {
        window.clearTimeout(userExportJobsRefreshTimer);
        userExportJobsRefreshTimer = null;
    }
};
