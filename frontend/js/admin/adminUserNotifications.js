/**
 * Admin User Notifications Management
 * Handles CRUD operations for user notifications in the admin panel
 */

// =============================================================================
// State
// =============================================================================

const AdminUserNotificationsState = {
    notifications: [],
    currentPage: 1,
    pageSize: 20,
    totalPages: 1,
    total: 0,
    loading: false,
    initialized: false,
    editingId: null,
    deleteId: null,
    selectedUsers: new Set(),
    selectedGroups: new Set(),
    usersCache: [],
    groupsCache: [],
    formLastFocusedElement: null,
    deleteLastFocusedElement: null,
};

let recipientDropdownViewportListenersBound = false;

const adminUserNotifT = (key, fallback) => {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback !== undefined ? fallback : key;
};

const adminUserNotifFormat = (key, fallback, vars = {}) => {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    let text = adminUserNotifT(key, fallback);
    Object.entries(vars).forEach(([name, value]) => {
        text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), value);
    });
    return text;
};

function getAdminShareItemTypeLabel(itemType) {
    const normalizedType = String(itemType || '').toLowerCase();
    const labels = {
        agent: ['workspace_notifications_item_agent', 'agent'],
        chat: ['workspace_notifications_item_chat', 'chat'],
        file_folder: ['workspace_notifications_item_file_folder', 'file folder'],
        note: ['workspace_notifications_item_note', 'note'],
        project: ['workspace_notifications_item_project', 'project'],
        prompt: ['workspace_notifications_item_prompt', 'prompt'],
        skill: ['workspace_notifications_item_skill', 'skill'],
        todo_list: ['workspace_notifications_item_todo_list', 'to-do list'],
    };
    const [key, fallback] = labels[normalizedType] || ['workspace_notifications_item_default', 'item'];
    return adminUserNotifT(key, fallback);
}

function getAdminNotificationMessage(notification) {
    const details = notification?.details;
    if (details?.type !== 'share_invitation') {
        return notification?.message || '';
    }

    return adminUserNotifFormat(
        'workspace_notifications_invitation_message',
        '{inviter} invited you to {itemType}: {title}',
        {
            inviter: details.inviter_name || adminUserNotifT('workspace_notifications_inviter_unknown', 'Someone'),
            itemType: getAdminShareItemTypeLabel(details.item_type),
            title: details.item_title || adminUserNotifT('workspace_notifications_item_untitled', 'Untitled item'),
        },
    );
}

function notifyAdmin(message, type = 'info') {
    const globalNotify = typeof window !== 'undefined' ? window.showNotification : undefined;
    if (typeof globalNotify === 'function') {
        globalNotify(message, type);
    } else if (type === 'error') {
        // Preserve actionable failures when the shared notification UI is unavailable.
        console.error(`[Notification ${type}]: ${message}`);
    }
}

async function authFetch(input, init = {}) {
    if (typeof window !== 'undefined' && typeof window.authedFetch === 'function') {
        return window.authedFetch(input, init);
    }
    return fetch(input, init);
}

// =============================================================================
// DOM Elements
// =============================================================================

const AdminUserNotificationsDOM = {
    get page() { return document.getElementById('page-user-notifications'); },
    get createBtn() { return document.getElementById('userNotificationsCreateBtn'); },
    get list() { return document.getElementById('userNotificationsList'); },
    get loading() { return document.getElementById('userNotificationsLoading'); },
    get empty() { return document.getElementById('userNotificationsEmpty'); },
    get pagination() { return document.getElementById('userNotificationsPagination'); },
    get paginationInfo() { return document.getElementById('userNotificationsPaginationInfo'); },
    get paginationPages() { return document.getElementById('userNotificationsPaginationPages'); },
    get prevBtn() { return document.getElementById('userNotificationsPrevBtn'); },
    get nextBtn() { return document.getElementById('userNotificationsNextBtn'); },
    // Form elements
    get formOverlay() { return document.getElementById('userNotificationFormOverlay'); },
    get formTitle() { return document.getElementById('userNotificationFormTitle'); },
    get formCloseBtn() { return document.getElementById('userNotificationFormCloseBtn'); },
    get form() { return document.getElementById('userNotificationForm'); },
    get formId() { return document.getElementById('userNotificationFormId'); },
    get formMessage() { return document.getElementById('userNotificationFormMessage'); },
    get formMessageCount() { return document.getElementById('userNotificationFormMessageCount'); },
    get formCategory() { return document.getElementById('userNotificationFormCategory'); },
    get formType() { return document.getElementById('userNotificationFormType'); },
    get formEveryone() { return document.getElementById('userNotificationFormEveryone'); },
    get formRecipientsSection() { return document.getElementById('userNotificationFormRecipientsSection'); },
    get formUsersContainer() { return document.getElementById('userNotificationFormUsersContainer'); },
    get formUsersSelected() { return document.getElementById('userNotificationFormUsersSelected'); },
    get formUsersDropdown() { return document.getElementById('userNotificationFormUsersDropdown'); },
    get formUsersSearch() { return document.getElementById('userNotificationFormUsersSearch'); },
    get formUsersOptions() { return document.getElementById('userNotificationFormUsersOptions'); },
    get formUsersTrigger() { return document.getElementById('userNotificationFormUsersTrigger'); },
    get formGroupsContainer() { return document.getElementById('userNotificationFormGroupsContainer'); },
    get formGroupsSelected() { return document.getElementById('userNotificationFormGroupsSelected'); },
    get formGroupsDropdown() { return document.getElementById('userNotificationFormGroupsDropdown'); },
    get formGroupsSearch() { return document.getElementById('userNotificationFormGroupsSearch'); },
    get formGroupsOptions() { return document.getElementById('userNotificationFormGroupsOptions'); },
    get formGroupsTrigger() { return document.getElementById('userNotificationFormGroupsTrigger'); },
    get formCancelBtn() { return document.getElementById('userNotificationFormCancelBtn'); },
    get formSaveBtn() { return document.getElementById('userNotificationFormSaveBtn'); },
    // Delete modal
    get deleteOverlay() { return document.getElementById('deleteUserNotificationOverlay'); },
    get deleteMessage() { return document.getElementById('deleteUserNotificationMessage'); },
    get deleteCancelBtn() { return document.getElementById('deleteUserNotificationCancelBtn'); },
    get deleteConfirmBtn() { return document.getElementById('deleteUserNotificationConfirmBtn'); },
};

// =============================================================================
// API Functions
// =============================================================================

/**
 * Return a localized, actionable message for known notification API failures.
 *
 * Backend details are matched exactly so unexpected internal error text is not
 * exposed to administrators. Unknown and non-JSON responses retain the safe
 * translated fallback supplied by the caller.
 */
async function getNotificationApiError(response, fallbackKey, fallback) {
    const errorPayload = await response.json().catch(() => ({}));
    const detail = typeof errorPayload?.detail === 'string' ? errorPayload.detail.trim() : '';
    const knownDetails = {
        'message is required': [
            'user_notif_message_required',
            'Message is required',
        ],
        'Provide everyone=True or at least one user_id/group_id': [
            'user_notif_select_recipients',
            'Select "Send to everyone" or choose specific users/groups',
        ],
        'Notification not found': [
            'user_notif_not_found',
            'Notification not found',
        ],
    };
    const translation = knownDetails[detail];
    if (translation) {
        return adminUserNotifT(translation[0], translation[1]);
    }
    return adminUserNotifT(fallbackKey, fallback);
}

async function fetchAdminNotifications(page = 1, pageSize = 20) {
    const response = await authFetch(`/api/v1/user/notifications/admin/all?page=${page}&page_size=${pageSize}`, {
        method: 'GET',
    });
    if (!response.ok) throw new Error(adminUserNotifT('user_notif_load_error', 'Failed to load notifications'));
    return response.json();
}

async function createNotificationAPI(data) {
    const response = await authFetch('/api/v1/user/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        throw new Error(await getNotificationApiError(
            response,
            'user_notif_save_error',
            'Failed to save notification',
        ));
    }
    return response.json();
}

async function updateNotificationAPI(id, data) {
    const response = await authFetch(`/api/v1/user/notifications/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        throw new Error(await getNotificationApiError(
            response,
            'user_notif_save_error',
            'Failed to save notification',
        ));
    }
    return response.json();
}

async function deleteNotificationAPI(id) {
    const response = await authFetch(`/api/v1/user/notifications/${id}`, {
        method: 'DELETE',
    });
    if (!response.ok) throw new Error(adminUserNotifT('user_notif_delete_error', 'Failed to delete notification'));
    return true;
}

async function fetchUsers() {
    const response = await authFetch('/api/v1/admin/users', {
        method: 'GET',
    });
    if (!response.ok) return [];
    return response.json();
}

async function fetchGroups() {
    const response = await authFetch('/api/v1/groups/list', {
        method: 'GET',
    });
    if (!response.ok) return [];
    const payload = await response.json();
    if (Array.isArray(payload)) return payload;
    return Array.isArray(payload?.groups) ? payload.groups : [];
}

// =============================================================================
// Rendering
// =============================================================================

function getTypeIcon(type) {
    const icons = {
        info: Icons.info,
        warning: Icons.warning,
        error: Icons.error
    };
    return icons[type] || icons.info;
}

function formatTime(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function getRecipientLabel(notification) {
    if (notification.everyone) return adminUserNotifT('user_notif_everyone', 'Everyone');
    const parts = [];
    if (notification.user_ids?.length) parts.push(adminUserNotifFormat('user_notif_users_count', '{count} user(s)', { count: notification.user_ids.length }));
    if (notification.group_ids?.length) parts.push(adminUserNotifFormat('user_notif_groups_count', '{count} group(s)', { count: notification.group_ids.length }));
    return parts.join(', ') || adminUserNotifT('user_notif_no_recipients', 'No recipients');
}

function createNotificationCard(notification) {
    const card = document.createElement('div');
    card.className = 'admin-user-notification-card';
    card.dataset.id = notification.id;
    card.dataset.type = notification.type;
    const messageText = getAdminNotificationMessage(notification);

    card.innerHTML = `
        <div class="admin-user-notification-card-icon type-${notification.type}">
            ${getTypeIcon(notification.type)}
        </div>
        <div class="admin-user-notification-card-content">
            <div class="admin-user-notification-card-header">
                <div class="admin-user-notification-card-meta">
                    <span class="admin-user-notification-card-category">${escapeHtml(notification.category)}</span>
                    <span class="admin-user-notification-card-type type-${notification.type}">${notification.type}</span>
                    <span class="admin-user-notification-card-recipients">${getRecipientLabel(notification)}</span>
                </div>
                <span class="admin-user-notification-card-time">${formatTime(notification.timestamp)}</span>
            </div>
            <p class="admin-user-notification-card-message">${escapeHtml(messageText)}</p>
        </div>
        <div class="admin-user-notification-card-actions">
            <button type="button" class="admin-user-notification-action-btn edit" data-action="edit" aria-label="${adminUserNotifT('user_notif_edit_aria', 'Edit notification')}">
                ${Icons.create}
            </button>
            <button type="button" class="admin-user-notification-action-btn delete" data-action="delete" aria-label="${adminUserNotifT('user_notif_delete_aria', 'Delete notification')}">
                ${Icons.trash}
            </button>
        </div>
    `;

    card.querySelector('[data-action="edit"]').addEventListener('click', () => openEditForm(notification));
    card.querySelector('[data-action="delete"]').addEventListener('click', () => openDeleteModal(notification));

    return card;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

function renderNotifications() {
    const { notifications } = AdminUserNotificationsState;
    const list = AdminUserNotificationsDOM.list;
    const empty = AdminUserNotificationsDOM.empty;
    const pagination = AdminUserNotificationsDOM.pagination;

    if (!list) return;

    list.innerHTML = '';

    if (!notifications || notifications.length === 0) {
        list.style.display = 'none';
        if (empty) empty.style.display = 'flex';
        if (pagination) pagination.style.display = 'none';
        return;
    }

    list.style.display = 'flex';
    if (empty) empty.style.display = 'none';

    notifications.forEach(n => list.appendChild(createNotificationCard(n)));
    updatePagination();
}

function updatePagination() {
    const { currentPage, totalPages, total, pageSize } = AdminUserNotificationsState;
    const pagination = AdminUserNotificationsDOM.pagination;
    const paginationInfo = AdminUserNotificationsDOM.paginationInfo;
    const paginationPages = AdminUserNotificationsDOM.paginationPages;
    const prevBtn = AdminUserNotificationsDOM.prevBtn;
    const nextBtn = AdminUserNotificationsDOM.nextBtn;

    if (!pagination || total === 0) {
        if (pagination) pagination.style.display = 'none';
        return;
    }

    pagination.style.display = 'flex';

    if (paginationInfo) {
        const start = (currentPage - 1) * pageSize + 1;
        const end = Math.min(currentPage * pageSize, total);
        paginationInfo.textContent = adminUserNotifFormat('user_notif_pagination_showing', 'Showing {start}-{end} of {total}', {
            start,
            end,
            total,
        });
    }

    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;

    if (paginationPages) {
        paginationPages.innerHTML = '';
        const pages = generatePageNumbers(currentPage, totalPages);
        pages.forEach(page => {
            if (page === '...') {
                const ellipsis = document.createElement('span');
                ellipsis.className = 'user-notifications-pagination-ellipsis';
                ellipsis.textContent = '...';
                paginationPages.appendChild(ellipsis);
            } else {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'user-notifications-pagination-page' + (page === currentPage ? ' active' : '');
                btn.textContent = page;
                btn.addEventListener('click', () => goToPage(page));
                paginationPages.appendChild(btn);
            }
        });
    }
}

function generatePageNumbers(current, total) {
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    if (current <= 4) return [1, 2, 3, 4, 5, '...', total];
    if (current >= total - 3) return [1, '...', total - 4, total - 3, total - 2, total - 1, total];
    return [1, '...', current - 1, current, current + 1, '...', total];
}

function showLoading(show) {
    const loading = AdminUserNotificationsDOM.loading;
    const list = AdminUserNotificationsDOM.list;
    const empty = AdminUserNotificationsDOM.empty;
    const pagination = AdminUserNotificationsDOM.pagination;

    if (loading) loading.style.display = show ? 'flex' : 'none';
    if (list && show) list.style.display = 'none';
    if (empty && show) empty.style.display = 'none';
    if (pagination && show) pagination.style.display = 'none';
}

// =============================================================================
// Main Functions
// =============================================================================

async function loadNotifications(showLoadingState = true) {
    if (AdminUserNotificationsState.loading) return;
    AdminUserNotificationsState.loading = true;

    if (showLoadingState) showLoading(true);

    try {
        const { currentPage, pageSize } = AdminUserNotificationsState;
        const response = await fetchAdminNotifications(currentPage, pageSize);
        AdminUserNotificationsState.notifications = response.notifications;
        AdminUserNotificationsState.total = response.total;
        AdminUserNotificationsState.totalPages = response.total_pages;
        AdminUserNotificationsState.currentPage = response.page;
        renderNotifications();
    } catch (error) {
        console.error('Failed to load notifications:', error);
        AdminUserNotificationsState.notifications = [];
        AdminUserNotificationsState.total = 0;
        renderNotifications();
    } finally {
        AdminUserNotificationsState.loading = false;
        showLoading(false);
    }
}

function goToPage(page) {
    if (page < 1 || page > AdminUserNotificationsState.totalPages) return;
    if (page === AdminUserNotificationsState.currentPage) return;
    AdminUserNotificationsState.currentPage = page;
    loadNotifications(false);
}

function syncFormTypeSelectUi() {
    const formType = AdminUserNotificationsDOM.formType;
    if (formType?._singleSelect?.syncFromSelect) {
        formType._singleSelect.syncFromSelect();
    }
}

function upgradeFormTypeSelect() {
    window.upgradeAdminSingleSelect?.(AdminUserNotificationsDOM.formType, {
        key: 'user-notification-type',
        placeholder: adminUserNotifT('admin_select_placeholder_single', 'Select an option...'),
    });
    syncFormTypeSelectUi();
}

function getRecipientDropdownRefs(kind) {
    if (kind === 'users') {
        return {
            container: AdminUserNotificationsDOM.formUsersContainer,
            dropdown: AdminUserNotificationsDOM.formUsersDropdown,
            trigger: AdminUserNotificationsDOM.formUsersTrigger,
            search: AdminUserNotificationsDOM.formUsersSearch,
        };
    }
    if (kind === 'groups') {
        return {
            container: AdminUserNotificationsDOM.formGroupsContainer,
            dropdown: AdminUserNotificationsDOM.formGroupsDropdown,
            trigger: AdminUserNotificationsDOM.formGroupsTrigger,
            search: AdminUserNotificationsDOM.formGroupsSearch,
        };
    }
    return null;
}

function hasOpenRecipientDropdown() {
    const usersOpen = Boolean(AdminUserNotificationsDOM.formUsersDropdown && !AdminUserNotificationsDOM.formUsersDropdown.hidden);
    const groupsOpen = Boolean(AdminUserNotificationsDOM.formGroupsDropdown && !AdminUserNotificationsDOM.formGroupsDropdown.hidden);
    return usersOpen || groupsOpen;
}

function positionRecipientDropdown(dropdown, trigger) {
    if (!dropdown || !trigger) return;

    const triggerRect = trigger.getBoundingClientRect();
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const edgePadding = 12;
    const preferredGap = 6;
    const minHeight = 100;
    const preferredMaxHeight = 320;

    const computedWidth = Math.max(220, Math.round(triggerRect.width));
    let left = triggerRect.left;
    if (left + computedWidth > viewportWidth - edgePadding) {
        left = Math.max(edgePadding, viewportWidth - edgePadding - computedWidth);
    }
    if (left < edgePadding) {
        left = edgePadding;
    }

    const originalMaxHeight = dropdown.style.maxHeight;
    const originalHeight = dropdown.style.height;
    dropdown.style.maxHeight = '';
    dropdown.style.height = 'auto';
    const measuredHeight = dropdown.scrollHeight || preferredMaxHeight;
    dropdown.style.height = originalHeight;
    dropdown.style.maxHeight = originalMaxHeight;

    const naturalHeight = Math.min(preferredMaxHeight, Math.max(minHeight, Math.round(measuredHeight)));
    const spaceBelow = Math.max(0, viewportHeight - triggerRect.bottom - preferredGap - edgePadding);
    const spaceAbove = Math.max(0, triggerRect.top - preferredGap - edgePadding);
    const shouldOpenAbove = spaceBelow < Math.min(naturalHeight, 180) && spaceAbove > spaceBelow;
    const maxAllowedHeight = Math.max(minHeight, viewportHeight - (edgePadding * 2));
    const availableSpace = shouldOpenAbove ? spaceAbove : spaceBelow;
    const dropdownHeight = Math.max(
        minHeight,
        Math.min(
            naturalHeight,
            availableSpace > 0 ? availableSpace : maxAllowedHeight,
            maxAllowedHeight
        )
    );

    let top = shouldOpenAbove
        ? triggerRect.top - preferredGap - dropdownHeight
        : triggerRect.bottom + preferredGap;
    top = Math.max(edgePadding, Math.min(top, viewportHeight - edgePadding - dropdownHeight));

    dropdown.style.top = `${Math.round(top)}px`;
    dropdown.style.left = `${Math.round(left)}px`;
    dropdown.style.width = `${Math.round(computedWidth)}px`;
    dropdown.style.maxHeight = `${Math.round(dropdownHeight)}px`;
}

function repositionOpenRecipientDropdowns(e) {
    ['users', 'groups'].forEach(kind => {
        const refs = getRecipientDropdownRefs(kind);
        if (refs?.dropdown && !refs.dropdown.hidden) {
            if (e && e.type === 'scroll' && refs.dropdown.contains(e.target)) return;
            positionRecipientDropdown(refs.dropdown, refs.trigger);
        }
    });
}

function bindRecipientDropdownViewportListeners() {
    if (recipientDropdownViewportListenersBound) {
        return;
    }
    window.addEventListener('resize', repositionOpenRecipientDropdowns);
    window.addEventListener('scroll', repositionOpenRecipientDropdowns, true);
    recipientDropdownViewportListenersBound = true;
}

function unbindRecipientDropdownViewportListeners() {
    if (!recipientDropdownViewportListenersBound) {
        return;
    }
    window.removeEventListener('resize', repositionOpenRecipientDropdowns);
    window.removeEventListener('scroll', repositionOpenRecipientDropdowns, true);
    recipientDropdownViewportListenersBound = false;
}

function setRecipientDropdownOpen(kind, shouldOpen) {
    const refs = getRecipientDropdownRefs(kind);
    if (!refs?.dropdown || !refs?.trigger) {
        return;
    }

    if (shouldOpen) {
        const counterpart = kind === 'users' ? 'groups' : 'users';
        setRecipientDropdownOpen(counterpart, false);
        refs.dropdown.hidden = false;
        refs.container?.classList.add('is-open');
        refs.trigger.setAttribute('aria-expanded', 'true');
        positionRecipientDropdown(refs.dropdown, refs.trigger);
        bindRecipientDropdownViewportListeners();
        refs.search?.focus();
    } else {
        refs.dropdown.hidden = true;
        refs.container?.classList.remove('is-open');
        refs.trigger.setAttribute('aria-expanded', 'false');
        refs.dropdown.style.top = '';
        refs.dropdown.style.left = '';
        refs.dropdown.style.width = '';
        refs.dropdown.style.maxHeight = '';
        if (!hasOpenRecipientDropdown()) {
            unbindRecipientDropdownViewportListeners();
        }
    }
}

function closeRecipientDropdowns() {
    setRecipientDropdownOpen('users', false);
    setRecipientDropdownOpen('groups', false);
}

function isNotificationFormOpen() {
    const overlay = AdminUserNotificationsDOM.formOverlay;
    return Boolean(overlay && !overlay.hidden);
}

function isDeleteModalOpen() {
    const overlay = AdminUserNotificationsDOM.deleteOverlay;
    return Boolean(overlay && !overlay.hidden);
}

function isUserNotificationsPageActive() {
    const page = AdminUserNotificationsDOM.page;
    return Boolean(page && !page.hidden);
}

function consumeEscapeEvent(event) {
    event.preventDefault();
    if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation();
    }
    event.stopPropagation();
}

function navigateBackToUsersPage() {
    window.activateAdminPage?.('users');
}

function handleNotificationFormEscape(event) {
    if (event.key !== 'Escape') {
        return;
    }

    if (!isUserNotificationsPageActive()) {
        return;
    }

    if (isDeleteModalOpen()) {
        consumeEscapeEvent(event);
        closeDeleteModal();
        return;
    }

    if (!isNotificationFormOpen()) {
        consumeEscapeEvent(event);
        navigateBackToUsersPage();
        return;
    }

    if (hasOpenRecipientDropdown()) {
        consumeEscapeEvent(event);
        closeRecipientDropdowns();
        return;
    }

    consumeEscapeEvent(event);
    closeForm();
}


// =============================================================================
// Form Functions
// =============================================================================

async function loadFormData() {
    if (AdminUserNotificationsState.usersCache.length === 0) {
        try {
            AdminUserNotificationsState.usersCache = await fetchUsers();
        } catch (e) {
            console.error('Failed to load users:', e);
        }
    }
    if (AdminUserNotificationsState.groupsCache.length === 0) {
        try {
            AdminUserNotificationsState.groupsCache = await fetchGroups();
        } catch (e) {
            console.error('Failed to load groups:', e);
        }
    }
    renderUserOptions();
    renderGroupOptions();
}

function renderUserOptions(filter = '') {
    const container = AdminUserNotificationsDOM.formUsersOptions;
    if (!container) return;

    const users = AdminUserNotificationsState.usersCache.filter(u => {
        if (!filter) return true;
        const name = `${u.first_name || ''} ${u.last_name || ''} ${u.email || ''}`.toLowerCase();
        return name.includes(filter.toLowerCase());
    });

    container.innerHTML = '';
    users.forEach(user => {
        const option = document.createElement('label');
        option.className = 'user-notification-form-multiselect-option';
        const isChecked = AdminUserNotificationsState.selectedUsers.has(user.id);
        option.innerHTML = `
            <input type="checkbox" value="${user.id}" ${isChecked ? 'checked' : ''}>
            <span>${escapeHtml(user.first_name || '')} ${escapeHtml(user.last_name || '')} (${escapeHtml(user.email || '')})</span>
        `;
        option.querySelector('input').addEventListener('change', (e) => {
            if (e.target.checked) {
                AdminUserNotificationsState.selectedUsers.add(user.id);
                clearNotificationFieldError(AdminUserNotificationsDOM.formRecipientsSection);
            } else {
                AdminUserNotificationsState.selectedUsers.delete(user.id);
            }
            renderSelectedUsers();
        });
        container.appendChild(option);
    });
}

function renderGroupOptions(filter = '') {
    const container = AdminUserNotificationsDOM.formGroupsOptions;
    if (!container) return;

    const groups = AdminUserNotificationsState.groupsCache.filter(g => {
        if (!filter) return true;
        return (g.name || '').toLowerCase().includes(filter.toLowerCase());
    });

    container.innerHTML = '';
    groups.forEach(group => {
        const option = document.createElement('label');
        option.className = 'user-notification-form-multiselect-option';
        const isChecked = AdminUserNotificationsState.selectedGroups.has(group.id);
        option.innerHTML = `
            <input type="checkbox" value="${group.id}" ${isChecked ? 'checked' : ''}>
            <span>${escapeHtml(group.name || adminUserNotifT('user_notif_unnamed_group', 'Unnamed Group'))}</span>
        `;
        option.querySelector('input').addEventListener('change', (e) => {
            if (e.target.checked) {
                AdminUserNotificationsState.selectedGroups.add(group.id);
                clearNotificationFieldError(AdminUserNotificationsDOM.formRecipientsSection);
            } else {
                AdminUserNotificationsState.selectedGroups.delete(group.id);
            }
            renderSelectedGroups();
        });
        container.appendChild(option);
    });
}

function renderSelectedUsers() {
    const container = AdminUserNotificationsDOM.formUsersSelected;
    if (!container) return;

    container.innerHTML = '';
    AdminUserNotificationsState.selectedUsers.forEach(userId => {
        const user = AdminUserNotificationsState.usersCache.find(u => u.id === userId);
        if (!user) return;
        const chip = document.createElement('span');
        chip.className = 'user-notification-form-chip';
        chip.innerHTML = `
            ${escapeHtml(user.first_name || '')} ${escapeHtml(user.last_name || '')}
            <button type="button" data-user-id="${userId}" aria-label="${adminUserNotifT('user_notif_remove_user_aria', 'Remove user')}">${Icons.close}</button>
        `;
        chip.querySelector('button').addEventListener('click', () => {
            AdminUserNotificationsState.selectedUsers.delete(userId);
            renderSelectedUsers();
            renderUserOptions(AdminUserNotificationsDOM.formUsersSearch?.value || '');
        });
        container.appendChild(chip);
    });
}

function renderSelectedGroups() {
    const container = AdminUserNotificationsDOM.formGroupsSelected;
    if (!container) return;

    container.innerHTML = '';
    AdminUserNotificationsState.selectedGroups.forEach(groupId => {
        const group = AdminUserNotificationsState.groupsCache.find(g => g.id === groupId);
        if (!group) return;
        const chip = document.createElement('span');
        chip.className = 'user-notification-form-chip';
        chip.innerHTML = `
            ${escapeHtml(group.name || adminUserNotifT('user_notif_unnamed_group_short', 'Unnamed'))}
            <button type="button" data-group-id="${groupId}" aria-label="${adminUserNotifT('user_notif_remove_group_aria', 'Remove group')}">${Icons.close}</button>
        `;
        chip.querySelector('button').addEventListener('click', () => {
            AdminUserNotificationsState.selectedGroups.delete(groupId);
            renderSelectedGroups();
            renderGroupOptions(AdminUserNotificationsDOM.formGroupsSearch?.value || '');
        });
        container.appendChild(chip);
    });
}

function openCreateForm() {
    AdminUserNotificationsState.editingId = null;
    AdminUserNotificationsState.selectedUsers.clear();
    AdminUserNotificationsState.selectedGroups.clear();

    const formOverlay = AdminUserNotificationsDOM.formOverlay;
    const formTitle = AdminUserNotificationsDOM.formTitle;
    const formSaveBtn = AdminUserNotificationsDOM.formSaveBtn;

    if (formTitle) formTitle.textContent = adminUserNotifT('user_notif_create_title', 'Create Notification');
    if (formSaveBtn) formSaveBtn.querySelector('span').textContent = adminUserNotifT('user_notif_create_title', 'Create Notification');

    resetForm();
    loadFormData();

    if (formOverlay) {
        AdminUserNotificationsState.formLastFocusedElement = document.activeElement;
        formOverlay.hidden = false;
        formOverlay.setAttribute('aria-hidden', 'false');
        window.requestAnimationFrame(() => AdminUserNotificationsDOM.formMessage?.focus());
    }
}

function openEditForm(notification) {
    AdminUserNotificationsState.editingId = notification.id;
    AdminUserNotificationsState.selectedUsers = new Set(notification.user_ids || []);
    AdminUserNotificationsState.selectedGroups = new Set(notification.group_ids || []);

    const formOverlay = AdminUserNotificationsDOM.formOverlay;
    const formTitle = AdminUserNotificationsDOM.formTitle;
    const formSaveBtn = AdminUserNotificationsDOM.formSaveBtn;

    if (formTitle) formTitle.textContent = adminUserNotifT('user_notif_edit_title', 'Edit Notification');
    if (formSaveBtn) formSaveBtn.querySelector('span').textContent = adminUserNotifT('user_notif_save_changes', 'Save Changes');

    const { formId, formMessage, formCategory, formType, formEveryone, formRecipientsSection } = AdminUserNotificationsDOM;

    if (formId) formId.value = notification.id;
    if (formMessage) {
        formMessage.value = notification.message || '';
        updateCharCount();
    }
    if (formCategory) formCategory.value = notification.category || 'general';
    if (formType) formType.value = notification.type || 'info';
    syncFormTypeSelectUi();
    if (formEveryone) {
        formEveryone.checked = notification.everyone;
        if (formRecipientsSection) {
            formRecipientsSection.style.display = notification.everyone ? 'none' : 'flex';
        }
    }
    closeRecipientDropdowns();
    clearAllNotificationFormErrors();

    loadFormData().then(() => {
        renderSelectedUsers();
        renderSelectedGroups();
    });

    if (formOverlay) {
        AdminUserNotificationsState.formLastFocusedElement = document.activeElement;
        formOverlay.hidden = false;
        formOverlay.setAttribute('aria-hidden', 'false');
        window.requestAnimationFrame(() => AdminUserNotificationsDOM.formMessage?.focus());
    }
}

function closeForm() {
    closeRecipientDropdowns();
    clearAllNotificationFormErrors();
    const formOverlay = AdminUserNotificationsDOM.formOverlay;
    if (formOverlay) {
        formOverlay.setAttribute('aria-hidden', 'true');
        formOverlay.hidden = true;
    }
    AdminUserNotificationsState.formLastFocusedElement?.focus?.();
    AdminUserNotificationsState.formLastFocusedElement = null;
}

function resetForm() {
    const { formId, formMessage, formCategory, formType, formEveryone, formRecipientsSection, formUsersSelected, formGroupsSelected } = AdminUserNotificationsDOM;

    if (formId) formId.value = '';
    if (formMessage) formMessage.value = '';
    if (formCategory) formCategory.value = 'general';
    if (formType) formType.value = 'info';
    syncFormTypeSelectUi();
    if (formEveryone) formEveryone.checked = false;
    if (formRecipientsSection) formRecipientsSection.style.display = 'flex';
    if (formUsersSelected) formUsersSelected.innerHTML = '';
    if (formGroupsSelected) formGroupsSelected.innerHTML = '';
    if (AdminUserNotificationsDOM.formUsersSearch) AdminUserNotificationsDOM.formUsersSearch.value = '';
    if (AdminUserNotificationsDOM.formGroupsSearch) AdminUserNotificationsDOM.formGroupsSearch.value = '';
    closeRecipientDropdowns();
    clearAllNotificationFormErrors();

    updateCharCount();
}

function updateCharCount() {
    const message = AdminUserNotificationsDOM.formMessage;
    const count = AdminUserNotificationsDOM.formMessageCount;
    if (message && count) {
        count.textContent = message.value.length;
    }
}

// =============================================================================
// Form Validation (reuses shared FieldValidation helper)
// =============================================================================

function resolveNotificationFieldRow(control) {
    return control?.closest?.('.user-notification-form-group')
        || control?.closest?.('.user-notification-form-recipients')
        || control?.closest?.('.user-notification-form-section')
        || control?.parentElement
        || null;
}

function setNotificationFieldError(row, message) {
    if (!row) return;
    if (window.FieldValidation?.setFieldError) {
        window.FieldValidation.setFieldError(row, message);
    } else {
        row.classList.add('has-error');
        let errorEl = row.querySelector(':scope > .field-error-message');
        if (!errorEl) {
            errorEl = document.createElement('p');
            errorEl.className = 'field-error-message';
            row.appendChild(errorEl);
        }
        errorEl.textContent = message;
    }
}

function clearNotificationFieldError(row) {
    if (!row) return;
    if (window.FieldValidation?.clearFieldError) {
        window.FieldValidation.clearFieldError(row);
    } else {
        row.classList.remove('has-error', 'shake-error');
        const errorEl = row.querySelector(':scope > .field-error-message');
        if (errorEl) errorEl.remove();
    }
}

function clearAllNotificationFormErrors() {
    const form = AdminUserNotificationsDOM.form;
    if (!form) return;
    form.querySelectorAll('.has-error').forEach((row) => clearNotificationFieldError(row));
}

function attachNotificationErrorClearListener(control) {
    if (!control || control.dataset?.errorClearBound === 'true') return;
    const row = resolveNotificationFieldRow(control);
    if (!row) return;
    const handler = () => {
        if (row.classList.contains('has-error')) {
            clearNotificationFieldError(row);
        }
    };
    control.addEventListener('input', handler);
    control.addEventListener('change', handler);
    control.dataset.errorClearBound = 'true';
}

function validateNotificationForm({ everyone, userIds, groupIds }) {
    const { formMessage, formRecipientsSection } = AdminUserNotificationsDOM;
    const invalidRows = [];

    clearAllNotificationFormErrors();

    // Message field
    const messageValue = formMessage?.value?.trim() || '';
    if (!messageValue) {
        const row = resolveNotificationFieldRow(formMessage);
        if (row) {
            setNotificationFieldError(
                row,
                adminUserNotifFormat('validation_field_required', '{field} is required.', {
                    field: adminUserNotifT('notif_form_message_label', 'Message'),
                })
            );
            invalidRows.push(row);
        }
    }

    // Recipients
    if (!everyone && userIds.length === 0 && groupIds.length === 0) {
        const row = formRecipientsSection;
        if (row) {
            setNotificationFieldError(
                row,
                adminUserNotifT(
                    'user_notif_select_recipients',
                    'Select "Send to everyone" or choose specific users/groups'
                )
            );
            invalidRows.push(row);
        }
    }

    if (invalidRows.length > 0) {
        const message = adminUserNotifFormat(
            'user_create_error_required_fields',
            'Please fill in {count} required field(s).',
            { count: invalidRows.length }
        );
        notifyAdmin(message, 'error');
        if (window.FieldValidation?.scrollToFirstInvalidField) {
            window.FieldValidation.scrollToFirstInvalidField(invalidRows);
        } else {
            const first = invalidRows[0];
            first?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            const focusable = first?.querySelector('input, select, textarea, button');
            if (focusable) setTimeout(() => focusable.focus(), 300);
        }
        return false;
    }
    return true;
}

async function saveNotification() {
    const { editingId, selectedUsers, selectedGroups } = AdminUserNotificationsState;
    const { formMessage, formCategory, formType, formEveryone, formSaveBtn } = AdminUserNotificationsDOM;

    const everyone = formEveryone?.checked || false;
    const userIds = Array.from(selectedUsers);
    const groupIds = Array.from(selectedGroups);

    if (!validateNotificationForm({ everyone, userIds, groupIds })) {
        return;
    }

    const message = formMessage?.value?.trim();

    const data = {
        message,
        category: formCategory?.value?.trim() || 'general',
        notification_type: formType?.value || 'info',
        everyone,
        user_ids: everyone ? null : (userIds.length > 0 ? userIds : null),
        group_ids: everyone ? null : (groupIds.length > 0 ? groupIds : null),
    };

    if (formSaveBtn) formSaveBtn.disabled = true;

    try {
        if (editingId) {
            await updateNotificationAPI(editingId, data);
            notifyAdmin(adminUserNotifT('user_notif_update_success', 'Notification updated successfully'), 'success');
        } else {
            await createNotificationAPI(data);
            notifyAdmin(adminUserNotifT('user_notif_create_success', 'Notification created successfully'), 'success');
        }
        closeForm();
        loadNotifications(false);
    } catch (error) {
        notifyAdmin(error.message || adminUserNotifT('user_notif_save_error', 'Failed to save notification'), 'error');
    } finally {
        if (formSaveBtn) formSaveBtn.disabled = false;
    }
}

// =============================================================================
// Delete Functions
// =============================================================================

function openDeleteModal(notification) {
    AdminUserNotificationsState.deleteId = notification.id;
    AdminUserNotificationsState.deleteLastFocusedElement = document.activeElement;
    const overlay = AdminUserNotificationsDOM.deleteOverlay;
    if (overlay) {
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        window.requestAnimationFrame(() => AdminUserNotificationsDOM.deleteCancelBtn?.focus());
    }
}

function closeDeleteModal() {
    AdminUserNotificationsState.deleteId = null;
    const overlay = AdminUserNotificationsDOM.deleteOverlay;
    if (overlay) {
        overlay.setAttribute('aria-hidden', 'true');
        overlay.hidden = true;
    }
    AdminUserNotificationsState.deleteLastFocusedElement?.focus?.();
    AdminUserNotificationsState.deleteLastFocusedElement = null;
}

async function confirmDelete() {
    const { deleteId } = AdminUserNotificationsState;
    if (!deleteId) return;

    const confirmBtn = AdminUserNotificationsDOM.deleteConfirmBtn;
    if (confirmBtn) confirmBtn.disabled = true;

    try {
        await deleteNotificationAPI(deleteId);
        notifyAdmin(adminUserNotifT('user_notif_delete_success', 'Notification deleted successfully'), 'success');
        closeDeleteModal();
        loadNotifications(false);
    } catch (error) {
        notifyAdmin(adminUserNotifT('user_notif_delete_error', 'Failed to delete notification'), 'error');
    } finally {
        if (confirmBtn) confirmBtn.disabled = false;
    }
}

// =============================================================================
// Initialization
// =============================================================================

function initAdminUserNotifications() {
    if (AdminUserNotificationsState.initialized) return;

    // Create button
    const createBtn = AdminUserNotificationsDOM.createBtn;
    if (createBtn) {
        createBtn.addEventListener('click', openCreateForm);
    }

    // Pagination
    const prevBtn = AdminUserNotificationsDOM.prevBtn;
    if (prevBtn) {
        prevBtn.addEventListener('click', () => {
            if (AdminUserNotificationsState.currentPage > 1) {
                AdminUserNotificationsState.currentPage--;
                loadNotifications(false);
            }
        });
    }

    const nextBtn = AdminUserNotificationsDOM.nextBtn;
    if (nextBtn) {
        nextBtn.addEventListener('click', () => {
            if (AdminUserNotificationsState.currentPage < AdminUserNotificationsState.totalPages) {
                AdminUserNotificationsState.currentPage++;
                loadNotifications(false);
            }
        });
    }

    // Form events
    upgradeFormTypeSelect();

    const formCloseBtn = AdminUserNotificationsDOM.formCloseBtn;
    if (formCloseBtn) {
        formCloseBtn.addEventListener('click', closeForm);
    }

    const formOverlay = AdminUserNotificationsDOM.formOverlay;
    if (formOverlay) {
        formOverlay.addEventListener('click', (e) => {
            if (e.target === formOverlay) closeForm();
        });
    }

    const formCancelBtn = AdminUserNotificationsDOM.formCancelBtn;
    if (formCancelBtn) {
        formCancelBtn.addEventListener('click', closeForm);
    }

    const formSaveBtn = AdminUserNotificationsDOM.formSaveBtn;
    if (formSaveBtn) {
        formSaveBtn.addEventListener('click', saveNotification);
    }

    const formMessage = AdminUserNotificationsDOM.formMessage;
    if (formMessage) {
        formMessage.addEventListener('input', updateCharCount);
        attachNotificationErrorClearListener(formMessage);
    }

    const formEveryone = AdminUserNotificationsDOM.formEveryone;
    if (formEveryone) {
        formEveryone.addEventListener('change', () => {
            const recipientsSection = AdminUserNotificationsDOM.formRecipientsSection;
            if (recipientsSection) {
                recipientsSection.style.display = formEveryone.checked ? 'none' : 'flex';
                // Clear recipients error if user toggles "everyone" on
                if (formEveryone.checked) {
                    clearNotificationFieldError(recipientsSection);
                }
            }
            if (formEveryone.checked) {
                closeRecipientDropdowns();
            }
        });
    }

    // Users dropdown
    const usersTrigger = AdminUserNotificationsDOM.formUsersTrigger;
    const usersDropdown = AdminUserNotificationsDOM.formUsersDropdown;
    if (usersTrigger && usersDropdown) {
        usersTrigger.setAttribute('aria-expanded', 'false');
        usersTrigger.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            setRecipientDropdownOpen('users', usersDropdown.hidden);
        });
    }

    const usersSearch = AdminUserNotificationsDOM.formUsersSearch;
    if (usersSearch) {
        usersSearch.addEventListener('input', () => renderUserOptions(usersSearch.value));
    }

    // Groups dropdown
    const groupsTrigger = AdminUserNotificationsDOM.formGroupsTrigger;
    const groupsDropdown = AdminUserNotificationsDOM.formGroupsDropdown;
    if (groupsTrigger && groupsDropdown) {
        groupsTrigger.setAttribute('aria-expanded', 'false');
        groupsTrigger.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            setRecipientDropdownOpen('groups', groupsDropdown.hidden);
        });
    }

    const groupsSearch = AdminUserNotificationsDOM.formGroupsSearch;
    if (groupsSearch) {
        groupsSearch.addEventListener('input', () => renderGroupOptions(groupsSearch.value));
    }

    // Close dropdowns on outside click
    document.addEventListener('click', (e) => {
        const usersContainer = AdminUserNotificationsDOM.formUsersContainer;
        const groupsContainer = AdminUserNotificationsDOM.formGroupsContainer;
        
        if (usersContainer && !usersContainer.contains(e.target)) {
            setRecipientDropdownOpen('users', false);
        }
        if (groupsContainer && !groupsContainer.contains(e.target)) {
            setRecipientDropdownOpen('groups', false);
        }
    });

    document.addEventListener('keydown', handleNotificationFormEscape);

    // Delete modal
    const deleteCancelBtn = AdminUserNotificationsDOM.deleteCancelBtn;
    if (deleteCancelBtn) {
        deleteCancelBtn.addEventListener('click', closeDeleteModal);
    }

    const deleteConfirmBtn = AdminUserNotificationsDOM.deleteConfirmBtn;
    if (deleteConfirmBtn) {
        deleteConfirmBtn.addEventListener('click', confirmDelete);
    }

    const deleteOverlay = AdminUserNotificationsDOM.deleteOverlay;
    if (deleteOverlay) {
        deleteOverlay.addEventListener('click', (e) => {
            if (e.target === deleteOverlay) closeDeleteModal();
        });
    }

    AdminUserNotificationsState.initialized = true;
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', initAdminUserNotifications);

// Page lifecycle functions for pages.js integration
window.initUserNotificationsPage = function() {
    loadNotifications();
};

window.teardownUserNotificationsPage = function() {
    // Reset state when leaving the page
    AdminUserNotificationsState.currentPage = 1;
};
