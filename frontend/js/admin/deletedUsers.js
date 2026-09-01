const deletedUsersListContainer = document.getElementById('deletedUsersList');
const cancelDeletionOverlay = document.getElementById('cancelDeletionOverlay');
const cancelDeletionMessage = document.getElementById('cancelDeletionMessage');
const cancelDeletionCancelButton = document.getElementById('cancelDeletionCancelButton');
const cancelDeletionPrimaryButton = document.getElementById('cancelDeletionPrimaryButton');
const cancelDeletionPrimaryText = document.getElementById('cancelDeletionPrimaryText');
const defaultCancelDeletionMessage = cancelDeletionMessage?.textContent?.trim()
    || 'Cancel scheduled permanent deletion for this user? The user will remain soft-deleted but won’t be automatically purged.';
const defaultCancelDeletionPrimaryText = cancelDeletionPrimaryText?.textContent?.trim() || 'Cancel Scheduled Deletion';
const cancelDeletionPrimaryIconHtml = cancelDeletionPrimaryButton?.querySelector('svg')?.outerHTML || '';

const restoreUserOverlay = document.getElementById('restoreUserOverlay');
const restoreUserMessage = document.getElementById('restoreUserMessage');
const restoreUserCancelButton = document.getElementById('restoreUserCancelButton');
const restoreUserPrimaryButton = document.getElementById('restoreUserPrimaryButton');
const restoreUserPrimaryText = document.getElementById('restoreUserPrimaryText');
const defaultRestoreUserMessage = restoreUserMessage?.textContent?.trim() || 'Restore this user? They will be able to log in again.';
const defaultRestoreUserPrimaryText = restoreUserPrimaryText?.textContent?.trim() || 'Restore User';
const restoreUserPrimaryIconHtml = restoreUserPrimaryButton?.querySelector('svg')?.outerHTML || '';

const hardDeleteUserOverlay = document.getElementById('hardDeleteUserOverlay');
const hardDeleteUserMessage = document.getElementById('hardDeleteUserMessage');
const hardDeleteUserCancelButton = document.getElementById('hardDeleteUserCancelButton');
const hardDeleteUserPrimaryButton = document.getElementById('hardDeleteUserPrimaryButton');
const hardDeleteUserPrimaryText = document.getElementById('hardDeleteUserPrimaryText');
const defaultHardDeleteUserMessage = hardDeleteUserMessage?.textContent?.trim()
    || 'Permanently delete this user? This action cannot be undone.';
const defaultHardDeleteUserPrimaryText = hardDeleteUserPrimaryText?.textContent?.trim() || 'Delete Permanently';
const hardDeleteUserPrimaryIconHtml = hardDeleteUserPrimaryButton?.querySelector('svg')?.outerHTML || '';

let deletedUsersCache = [];
let deletedUsersInitialized = false;
let cancelDeletionUserId = null;
let restoreUserId = null;
let hardDeleteUserId = null;
let deletedUsersLanguageObserver = null;

const deletedUsersT = (key, fallback) => {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback !== undefined ? fallback : key;
};

const deletedUsersFormat = (key, fallback, vars) => {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    const template = deletedUsersT(key, fallback);
    return String(template).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars?.[token];
        return value === undefined || value === null ? '' : String(value);
    });
};

async function showDeletedUsersWarningConfirm(options) {
    if (typeof window.showDeleteConfirm !== 'function') {
        notifyError?.(deletedUsersT('deleted_users_confirm_unavailable', 'Confirmation dialog is unavailable. Please reload the page and try again.'));
        return false;
    }

    return await window.showDeleteConfirm(options);
}

async function initDeletedUsersSection() {
    if (!deletedUsersListContainer) {
        return;
    }
    if (!deletedUsersLanguageObserver && document.documentElement) {
        deletedUsersLanguageObserver = new MutationObserver((mutations) => {
            const langChanged = mutations.some((mutation) => mutation.type === 'attributes' && mutation.attributeName === 'lang');
            if (langChanged && deletedUsersInitialized) {
                renderDeletedUsersList(deletedUsersCache);
            }
        });
        deletedUsersLanguageObserver.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['lang'],
        });
    }
    if (deletedUsersInitialized) {
        loadDeletedUsersList();
        return;
    }
    deletedUsersInitialized = true;
    bindDeletedUsersActions();
    bindCancelDeletionModalEvents();
    bindRestoreUserModalEvents();
    bindHardDeleteUserModalEvents();
    await loadDeletedUsersList();
}

async function loadDeletedUsersList() {
    if (!deletedUsersListContainer) {
        return;
    }
    renderDeletedUsersList([], { message: deletedUsersT('deleted_users_loading', 'Loading deleted users…') });
    deletedUsersCache = await fetchDeletedUsersList();
    renderDeletedUsersList(deletedUsersCache);
}

async function fetchDeletedUsersList() {
    try {
        const response = await window.authedFetch('/api/v1/admin/users/pending-deletion', {
            method: 'GET',
        });
        if (!response.ok) {
            notifyError(deletedUsersT('deleted_users_fetch_failed', 'Failed to fetch deleted users'));
            return [];
        }
        return await response.json();
    } catch (error) {
        console.error('Failed to load deleted users', error);
        notifyError(error?.message || deletedUsersT('deleted_users_fetch_failed', 'Failed to fetch deleted users'));
        return [];
    }
}

function renderDeletedUsersList(users = [], options = {}) {
    if (!deletedUsersListContainer) {
        return;
    }

    deletedUsersListContainer.innerHTML = '';

    const { message } = options;
    if (message || !users.length) {
        const emptyState = window.createAdminEmptyPlaceholder({
            title: message || deletedUsersT('deleted_users_empty_title', 'No deleted users'),
            description: message ? '' : deletedUsersT('deleted_users_empty_desc', 'Users pending deletion will appear here.'),
            icon: Icons.removeUser,
            className: 'deleted-users-empty',
        });
        deletedUsersListContainer.appendChild(emptyState);
        return;
    }

    const header = document.createElement('div');
    header.className = 'user-table-header deleted-users-table-header';
    const headerCells = [
        { className: 'header-name', text: deletedUsersT('table_header_name', 'Name') },
        { className: 'header-email', text: deletedUsersT('table_header_email', 'Email') },
        { className: 'header-deleted', text: deletedUsersT('table_header_deleted_at', 'Deleted At') },
        { className: 'header-scheduled', text: deletedUsersT('table_header_permanent_deletion', 'Permanent Deletion') },
        { className: 'header-actions', text: deletedUsersT('table_header_actions', 'Actions') },
    ];

    headerCells.forEach(({ className, text }) => {
        const cell = document.createElement('div');
        cell.className = className;
        cell.textContent = text;
        header.appendChild(cell);
    });

    deletedUsersListContainer.appendChild(header);

    users.forEach((user) => {
        const row = document.createElement('div');
        row.className = 'user-row deleted-user-row';
        row.dataset.userId = user.id;

        const fullName = [user?.first_name, user?.last_name]
            .map((part) => (part || '').trim())
            .filter(Boolean)
            .join(' ') || '—';

        const deletedAt = user.deleted_at ? formatDate(user.deleted_at) : '—';
        const scheduledFor = user.deletion_scheduled_for 
            ? formatDate(user.deletion_scheduled_for) 
            : `<span class="no-schedule">${deletedUsersT('deleted_users_not_scheduled', 'Not scheduled')}</span>`;

        row.innerHTML = `
            <div class="user-name deleted-user-name" data-label="${escapeHtml(deletedUsersT('table_header_name', 'Name'))}">
                <span class="user-name-primary">${escapeHtml(fullName)}</span>
            </div>
            <div class="user-email deleted-user-email" data-label="${escapeHtml(deletedUsersT('table_header_email', 'Email'))}">${escapeHtml(user.email || '—')}</div>
            <div class="user-last-active deleted-user-deleted" data-label="${escapeHtml(deletedUsersT('table_header_deleted_at', 'Deleted At'))}">${deletedAt}</div>
            <div class="user-last-active deleted-user-scheduled" data-label="${escapeHtml(deletedUsersT('table_header_permanent_deletion', 'Permanent Deletion'))}">${scheduledFor}</div>
            <div class="user-actions deleted-user-actions">
                <button type="button" class="action-btn restore-btn deleted-user-restore" title="${deletedUsersT('deleted_users_restore_title', 'Restore user')}" data-user-id="${user.id}">
                    ${Icons?.refresh || Icons?.undo || '↩'}
                </button>
                ${user.deletion_scheduled_for ? `
                <button type="button" class="action-btn cancel-btn deleted-user-cancel-schedule" title="${deletedUsersT('deleted_users_cancel_schedule_title', 'Cancel scheduled deletion')}" data-user-id="${user.id}">
                    ${Icons?.x || Icons?.close || '✕'}
                </button>
                ` : ''}
                <button type="button" class="action-btn delete-btn deleted-user-hard-delete" title="${deletedUsersT('deleted_users_hard_delete_title', 'Delete permanently')}" data-user-id="${user.id}">
                    ${Icons?.trash || '🗑'}
                </button>
            </div>
        `;
        deletedUsersListContainer.appendChild(row);
    });
}

function formatDate(dateString) {
    if (!dateString) return '—';
    try {
        const date = new Date(dateString);
        if (isNaN(date.getTime())) return '—';
        return date.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch {
        return '—';
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function bindDeletedUsersActions() {
    if (!deletedUsersListContainer || deletedUsersListContainer.dataset.bound === 'true') {
        return;
    }
    deletedUsersListContainer.addEventListener('click', handleDeletedUsersClick);
    deletedUsersListContainer.dataset.bound = 'true';
}

async function handleDeletedUsersClick(event) {
    const restoreBtn = event.target.closest('.deleted-user-restore');
    if (restoreBtn) {
        await handleRestoreUser(restoreBtn.dataset.userId);
        return;
    }

    const cancelBtn = event.target.closest('.deleted-user-cancel-schedule');
    if (cancelBtn) {
        await handleCancelScheduledDeletion(cancelBtn.dataset.userId);
        return;
    }

    const hardDeleteBtn = event.target.closest('.deleted-user-hard-delete');
    if (hardDeleteBtn) {
        await handleHardDeleteUser(hardDeleteBtn.dataset.userId);
        return;
    }
}

async function handleRestoreUser(userId) {
    if (!userId) return;
    
    const user = deletedUsersCache.find(u => u.id === userId);
    const userName = user ? `${user.first_name || ''} ${user.last_name || ''}`.trim() : deletedUsersT('deleted_users_subject_fallback', 'this user');
    if (restoreUserOverlay) {
        openRestoreUserModal(userId, userName);
        return;
    }

    if (!await showDeletedUsersWarningConfirm({
        title: deletedUsersT('modal_restore_user_title', 'Restore User?'),
        message: deletedUsersFormat('deleted_users_restore_confirm', 'Are you sure you want to restore {user}? They will be able to log in again.', { user: userName }),
        confirmLabel: deletedUsersT('modal_restore_user_btn', 'Restore User'),
    })) {
        return;
    }

    try {
        await performRestoreUser(userId);
    } catch (error) {
        console.error('Failed to restore user', error);
        notifyError?.(error.message || deletedUsersT('deleted_users_restore_failed', 'Failed to restore user'));
    }
}

async function handleCancelScheduledDeletion(userId) {
    if (!userId) return;
    
    const user = deletedUsersCache.find(u => u.id === userId);
    const userName = user ? `${user.first_name || ''} ${user.last_name || ''}`.trim() : deletedUsersT('deleted_users_subject_fallback', 'this user');
    if (cancelDeletionOverlay) {
        openCancelDeletionModal(userId, userName);
        return;
    }

    if (!await showDeletedUsersWarningConfirm({
        title: deletedUsersT('modal_cancel_deletion_title', 'Cancel Scheduled Deletion?'),
        message: deletedUsersFormat('deleted_users_cancel_confirm', 'Cancel scheduled permanent deletion for {user}? The user will remain soft-deleted but won\'t be automatically purged.', { user: userName }),
        confirmLabel: deletedUsersT('modal_cancel_deletion_btn', 'Cancel Scheduled Deletion'),
    })) {
        return;
    }

    try {
        await performCancelScheduledDeletion(userId);
    } catch (error) {
        console.error('Failed to cancel scheduled deletion', error);
        notifyError?.(error?.message || deletedUsersT('deleted_users_cancel_failed', 'Failed to cancel scheduled deletion'));
    }
}

async function handleHardDeleteUser(userId) {
    if (!userId) return;
    
    const user = deletedUsersCache.find(u => u.id === userId);
    const userName = user ? `${user.first_name || ''} ${user.last_name || ''}`.trim() : deletedUsersT('deleted_users_subject_fallback', 'this user');
    if (hardDeleteUserOverlay) {
        openHardDeleteUserModal(userId, userName);
        return;
    }

    if (!await showDeletedUsersWarningConfirm({
        title: deletedUsersT('modal_hard_delete_user_title', 'Permanently Delete User?'),
        message: deletedUsersFormat('deleted_users_hard_delete_confirm', 'PERMANENT DELETION\n\nAre you sure you want to permanently delete {user}?\n\nThis action cannot be undone. All user data will be lost forever.', { user: userName }),
        confirmLabel: deletedUsersT('modal_hard_delete_user_btn', 'Delete Permanently'),
    })) {
        return;
    }

    try {
        if (!await performHardDeleteUser(userId)) return;
    } catch (error) {
        console.error('Failed to hard delete user', error);
        notifyError?.(error.message || deletedUsersT('deleted_users_hard_delete_failed', 'Failed to delete user permanently'));
    }
}

// Export for use in pages.js
window.initDeletedUsersSection = initDeletedUsersSection;

function bindCancelDeletionModalEvents() {
    if (!cancelDeletionOverlay) {
        return;
    }

    if (cancelDeletionCancelButton && cancelDeletionCancelButton.dataset.bound !== 'true') {
        cancelDeletionCancelButton.addEventListener('click', closeCancelDeletionModal);
        cancelDeletionCancelButton.dataset.bound = 'true';
    }

    if (cancelDeletionOverlay && cancelDeletionOverlay.dataset.bound !== 'true') {
        cancelDeletionOverlay.addEventListener('click', (event) => {
            if (event.target === cancelDeletionOverlay) {
                closeCancelDeletionModal();
            }
        });
        cancelDeletionOverlay.dataset.bound = 'true';
    }

    if (cancelDeletionOverlay && cancelDeletionOverlay.dataset.keydownBound !== 'true') {
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && (!cancelDeletionOverlay.hidden || cancelDeletionOverlay.classList.contains('active'))) {
                closeCancelDeletionModal();
            }
        });
        cancelDeletionOverlay.dataset.keydownBound = 'true';
    }

    if (cancelDeletionPrimaryButton && cancelDeletionPrimaryButton.dataset.bound !== 'true') {
        cancelDeletionPrimaryButton.addEventListener('click', async () => {
            if (!cancelDeletionUserId || cancelDeletionPrimaryButton.disabled) {
                return;
            }
            setCancelDeletionPrimaryButtonState(true);
            try {
                await performCancelScheduledDeletion(cancelDeletionUserId);
                closeCancelDeletionModal();
            } catch (error) {
                console.error('Failed to cancel scheduled deletion', error);
                notifyError?.(error?.message || deletedUsersT('deleted_users_cancel_failed', 'Failed to cancel scheduled deletion'));
            } finally {
                setCancelDeletionPrimaryButtonState(false);
            }
        });
        cancelDeletionPrimaryButton.dataset.bound = 'true';
    }
}

function bindRestoreUserModalEvents() {
    if (!restoreUserOverlay) {
        return;
    }

    if (restoreUserCancelButton && restoreUserCancelButton.dataset.bound !== 'true') {
        restoreUserCancelButton.addEventListener('click', closeRestoreUserModal);
        restoreUserCancelButton.dataset.bound = 'true';
    }

    if (restoreUserOverlay && restoreUserOverlay.dataset.bound !== 'true') {
        restoreUserOverlay.addEventListener('click', (event) => {
            if (event.target === restoreUserOverlay) {
                closeRestoreUserModal();
            }
        });
        restoreUserOverlay.dataset.bound = 'true';
    }

    if (restoreUserOverlay && restoreUserOverlay.dataset.keydownBound !== 'true') {
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && (!restoreUserOverlay.hidden || restoreUserOverlay.classList.contains('active'))) {
                closeRestoreUserModal();
            }
        });
        restoreUserOverlay.dataset.keydownBound = 'true';
    }

    if (restoreUserPrimaryButton && restoreUserPrimaryButton.dataset.bound !== 'true') {
        restoreUserPrimaryButton.addEventListener('click', async () => {
            if (!restoreUserId || restoreUserPrimaryButton.disabled) {
                return;
            }
            setRestoreUserPrimaryButtonState(true);
            try {
                await performRestoreUser(restoreUserId);
                closeRestoreUserModal();
            } catch (error) {
                console.error('Failed to restore user', error);
                notifyError?.(error.message || deletedUsersT('deleted_users_restore_failed', 'Failed to restore user'));
            } finally {
                setRestoreUserPrimaryButtonState(false);
            }
        });
        restoreUserPrimaryButton.dataset.bound = 'true';
    }
}

function bindHardDeleteUserModalEvents() {
    if (!hardDeleteUserOverlay) {
        return;
    }

    if (hardDeleteUserCancelButton && hardDeleteUserCancelButton.dataset.bound !== 'true') {
        hardDeleteUserCancelButton.addEventListener('click', closeHardDeleteUserModal);
        hardDeleteUserCancelButton.dataset.bound = 'true';
    }

    if (hardDeleteUserOverlay && hardDeleteUserOverlay.dataset.bound !== 'true') {
        hardDeleteUserOverlay.addEventListener('click', (event) => {
            if (event.target === hardDeleteUserOverlay) {
                closeHardDeleteUserModal();
            }
        });
        hardDeleteUserOverlay.dataset.bound = 'true';
    }

    if (hardDeleteUserOverlay && hardDeleteUserOverlay.dataset.keydownBound !== 'true') {
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && (!hardDeleteUserOverlay.hidden || hardDeleteUserOverlay.classList.contains('active'))) {
                closeHardDeleteUserModal();
            }
        });
        hardDeleteUserOverlay.dataset.keydownBound = 'true';
    }

    if (hardDeleteUserPrimaryButton && hardDeleteUserPrimaryButton.dataset.bound !== 'true') {
        hardDeleteUserPrimaryButton.addEventListener('click', async () => {
            if (!hardDeleteUserId || hardDeleteUserPrimaryButton.disabled) {
                return;
            }
            setHardDeleteUserPrimaryButtonState(true);
            try {
                if (!await performHardDeleteUser(hardDeleteUserId)) return;
                closeHardDeleteUserModal();
            } catch (error) {
                console.error('Failed to delete user permanently', error);
                notifyError?.(error.message || deletedUsersT('deleted_users_hard_delete_failed', 'Failed to delete user permanently'));
            } finally {
                setHardDeleteUserPrimaryButtonState(false);
            }
        });
        hardDeleteUserPrimaryButton.dataset.bound = 'true';
    }
}

function openCancelDeletionModal(userId, userName = deletedUsersT('deleted_users_subject_fallback', 'this user')) {
    cancelDeletionUserId = userId;
    if (cancelDeletionMessage) {
        cancelDeletionMessage.textContent = deletedUsersFormat('deleted_users_cancel_confirm_inline', 'Cancel scheduled permanent deletion for {user}? The user will remain soft-deleted but won\'t be automatically purged.', { user: userName });
    }
    if (cancelDeletionOverlay) {
        cancelDeletionOverlay.hidden = false;
        cancelDeletionOverlay.classList.add('active');
    }
    setCancelDeletionPrimaryButtonState(false);
    cancelDeletionPrimaryButton?.focus();
}

function closeCancelDeletionModal() {
    cancelDeletionUserId = null;
    if (cancelDeletionMessage) {
        cancelDeletionMessage.textContent = defaultCancelDeletionMessage;
    }
    setCancelDeletionPrimaryButtonState(false);
    if (cancelDeletionOverlay) {
        cancelDeletionOverlay.classList.remove('active');
        cancelDeletionOverlay.hidden = true;
    }
}

function setCancelDeletionPrimaryButtonState(isLoading) {
    if (!cancelDeletionPrimaryButton) {
        return;
    }
    if (isLoading) {
        cancelDeletionPrimaryButton.disabled = true;
        if (cancelDeletionPrimaryText) {
            cancelDeletionPrimaryText.textContent = deletedUsersT('deleted_users_cancelling', 'Cancelling…');
        }
        const existingIcon = cancelDeletionPrimaryButton.querySelector('svg');
        const spinnerSvg = Icons.refreshSpinning;
        if (existingIcon) {
            existingIcon.outerHTML = spinnerSvg;
        } else {
            cancelDeletionPrimaryButton.insertAdjacentHTML('afterbegin', spinnerSvg);
        }
    } else {
        cancelDeletionPrimaryButton.disabled = false;
        if (cancelDeletionPrimaryText) {
            cancelDeletionPrimaryText.textContent = defaultCancelDeletionPrimaryText;
        }
        const currentIcon = cancelDeletionPrimaryButton.querySelector('svg');
        if (currentIcon && cancelDeletionPrimaryIconHtml) {
            currentIcon.outerHTML = cancelDeletionPrimaryIconHtml;
        } else if (!currentIcon && cancelDeletionPrimaryIconHtml) {
            cancelDeletionPrimaryButton.insertAdjacentHTML('afterbegin', cancelDeletionPrimaryIconHtml);
        }
    }
}

function openRestoreUserModal(userId, userName = deletedUsersT('deleted_users_subject_fallback', 'this user')) {
    restoreUserId = userId;
    if (restoreUserMessage) {
        restoreUserMessage.textContent = deletedUsersFormat('deleted_users_restore_prompt', 'Restore {user}? They will be able to log in again.', { user: userName });
    }
    if (restoreUserOverlay) {
        restoreUserOverlay.hidden = false;
        restoreUserOverlay.classList.add('active');
    }
    setRestoreUserPrimaryButtonState(false);
    restoreUserPrimaryButton?.focus();
}

function closeRestoreUserModal() {
    restoreUserId = null;
    if (restoreUserMessage) {
        restoreUserMessage.textContent = defaultRestoreUserMessage;
    }
    setRestoreUserPrimaryButtonState(false);
    if (restoreUserOverlay) {
        restoreUserOverlay.classList.remove('active');
        restoreUserOverlay.hidden = true;
    }
}

function setRestoreUserPrimaryButtonState(isLoading) {
    if (!restoreUserPrimaryButton) {
        return;
    }
    if (isLoading) {
        restoreUserPrimaryButton.disabled = true;
        if (restoreUserPrimaryText) {
            restoreUserPrimaryText.textContent = deletedUsersT('deleted_users_restoring', 'Restoring…');
        }
        const existingIcon = restoreUserPrimaryButton.querySelector('svg');
        const spinnerSvg = Icons.refreshSpinning;
        if (existingIcon) {
            existingIcon.outerHTML = spinnerSvg;
        } else {
            restoreUserPrimaryButton.insertAdjacentHTML('afterbegin', spinnerSvg);
        }
    } else {
        restoreUserPrimaryButton.disabled = false;
        if (restoreUserPrimaryText) {
            restoreUserPrimaryText.textContent = defaultRestoreUserPrimaryText;
        }
        const currentIcon = restoreUserPrimaryButton.querySelector('svg');
        if (currentIcon && restoreUserPrimaryIconHtml) {
            currentIcon.outerHTML = restoreUserPrimaryIconHtml;
        } else if (!currentIcon && restoreUserPrimaryIconHtml) {
            restoreUserPrimaryButton.insertAdjacentHTML('afterbegin', restoreUserPrimaryIconHtml);
        }
    }
}

function openHardDeleteUserModal(userId, userName = deletedUsersT('deleted_users_subject_fallback', 'this user')) {
    hardDeleteUserId = userId;
    if (hardDeleteUserMessage) {
        hardDeleteUserMessage.textContent = deletedUsersFormat(
            'deleted_users_hard_delete_prompt',
            'PERMANENT DELETION\n\nAre you sure you want to permanently delete {user}? This action cannot be undone.',
            { user: userName }
        );
    }
    if (hardDeleteUserOverlay) {
        hardDeleteUserOverlay.hidden = false;
        hardDeleteUserOverlay.classList.add('active');
    }
    setHardDeleteUserPrimaryButtonState(false);
    hardDeleteUserCancelButton?.focus();
}

function closeHardDeleteUserModal() {
    hardDeleteUserId = null;
    if (hardDeleteUserMessage) {
        hardDeleteUserMessage.textContent = defaultHardDeleteUserMessage;
    }
    setHardDeleteUserPrimaryButtonState(false);
    if (hardDeleteUserOverlay) {
        hardDeleteUserOverlay.classList.remove('active');
        hardDeleteUserOverlay.hidden = true;
    }
}

function setHardDeleteUserPrimaryButtonState(isLoading) {
    if (!hardDeleteUserPrimaryButton) {
        return;
    }
    if (isLoading) {
        hardDeleteUserPrimaryButton.disabled = true;
        if (hardDeleteUserPrimaryText) {
            hardDeleteUserPrimaryText.textContent = deletedUsersT('admin_deleting_ellipsis', 'Deleting…');
        }
        const existingIcon = hardDeleteUserPrimaryButton.querySelector('svg');
        const spinnerSvg = Icons.refreshSpinning;
        if (existingIcon) {
            existingIcon.outerHTML = spinnerSvg;
        } else {
            hardDeleteUserPrimaryButton.insertAdjacentHTML('afterbegin', spinnerSvg);
        }
    } else {
        hardDeleteUserPrimaryButton.disabled = false;
        if (hardDeleteUserPrimaryText) {
            hardDeleteUserPrimaryText.textContent = defaultHardDeleteUserPrimaryText;
        }
        const currentIcon = hardDeleteUserPrimaryButton.querySelector('svg');
        if (currentIcon && hardDeleteUserPrimaryIconHtml) {
            currentIcon.outerHTML = hardDeleteUserPrimaryIconHtml;
        } else if (!currentIcon && hardDeleteUserPrimaryIconHtml) {
            hardDeleteUserPrimaryButton.insertAdjacentHTML('afterbegin', hardDeleteUserPrimaryIconHtml);
        }
    }
}

async function performCancelScheduledDeletion(userId) {
    const response = await window.authedFetch('/api/v1/admin/user/cancel-deletion', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId }),
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || deletedUsersT('deleted_users_cancel_failed', 'Failed to cancel scheduled deletion'));
    }

    notifySuccess?.(deletedUsersT('deleted_users_cancel_success', 'Scheduled deletion cancelled'));
    await loadDeletedUsersList();
}

async function performRestoreUser(userId) {
    const response = await window.authedFetch('/api/v1/admin/user/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId }),
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || deletedUsersT('deleted_users_restore_failed', 'Failed to restore user'));
    }

    notifySuccess?.(deletedUsersT('deleted_users_restore_success', 'User restored successfully'));
    await loadDeletedUsersList();
    if (typeof loadUsersList === 'function') {
        loadUsersList();
    }
}

async function performHardDeleteUser(userId) {
    if (typeof window.ensureSecurityStepUp !== 'function') {
        throw new Error(deletedUsersT('step_up_methods_load_failed', 'Verification methods could not be loaded. Close this dialog and try again.'));
    }
    if (!await window.ensureSecurityStepUp()) {
        return false;
    }
    const response = await window.authedFetch('/api/v1/admin/user/hard-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId }),
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || deletedUsersT('deleted_users_hard_delete_failed', 'Failed to delete user permanently'));
    }

    notifySuccess?.(deletedUsersT('deleted_users_hard_delete_success', 'User permanently deleted'));
    await loadDeletedUsersList();
    return true;
}
