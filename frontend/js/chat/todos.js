/**
 * Todo Lists Workspace Module
 * Apple Reminders-like UX for managing todo lists and todos
 */

// ============================================================================
// State Management
// ============================================================================

const TodosState = {
    lists: [],
    selectedListId: null,
    todos: [],
    markedTodos: [],
    isLoadingLists: false,
    isLoadingTodos: false,
    isLoadingMarked: false,
    initialized: false,
    // Special list IDs
    MARKED_LIST_ID: '__marked__',
    // Icon picker state
    iconPicker: {
        selectedIconId: 'checklist',
        selectedColorIndex: 0,
        isOpen: false,
    },
    // Sort state
    sortBy: 'manual',
    sortDropdownOpen: false,
    filterDropdownOpen: false,
    viewMode: 'list',
    activeView: 'all',
    commandPaletteOpen: false,
    // Dropdown menu state
    openDropdownListId: null,
    // Routed list editor state. Create and edit use the same page shell while
    // keeping independent icon-picker state for their separate form drafts.
    editingList: null,
    listEditorMode: null,
    listEditorInitialSnapshot: null,
    listEditorReturnFocus: null,
    listEditorHistoryBypass: false,
    listEditorNavigationBypass: false,
    editIconPicker: {
        selectedIconId: 'checklist',
        selectedColorIndex: 0,
        isOpen: false,
    },
    // Sharing state
    sharingListId: null,
    shareMode: 'list',
    shareAction: 'link',
    shareStatus: null,
    currentShareType: 'live',
    currentCanEdit: false,
    publicUsers: [],
    publicUsersLoaded: false,
    publicUsersLoading: false,
    selectedUserIds: [],
    // Accept modal state
    pendingShareId: null,
    pendingShareType: null,
    acceptModalInitialized: false,
    // Auto-refresh polling state for shared lists
    refreshInterval: null,
    refreshIntervalMs: 5000,
    lastTodosHash: null,
    isUserEditing: false,
    // Search state
    searchQuery: '',
    searchResults: [],
    isSearching: false,
    allTodosCache: [],
    listsOffset: 0,
    listsHasMore: false,
    listsLoadingMore: false,
    listsRequestToken: null,
    todosOffset: 0,
    todosHasMore: false,
    todosLoadingMore: false,
    todosRequestToken: null,
    markedOffset: 0,
    markedHasMore: false,
    markedLoadingMore: false,
    markedRequestToken: null,
    searchOffset: 0,
    searchHasMore: false,
    searchLoading: false,
    searchRequestToken: null,
    searchTimer: null,
    addTodoExpanded: false,
    addTodoOpenPopover: null,
    isAddingTodo: false,
};

const TODOS_PAGE_LIMIT = 50;

function getTodoListState(listId) {
    return TodosState.lists.find(list => list.id === listId) || null;
}

function todoListHasExistingShareState(list) {
    if (!list) return false;
    return Boolean(list.clone_share_id || list.live_share_id || list.collaborate_share_id || Number(list.subscriber_count || 0) > 0);
}

function canManageTodoListSharing(list) {
    const sharingEnabled = typeof window !== 'undefined' ? window.allowTodoListShareFeature !== false : true;
    return sharingEnabled || todoListHasExistingShareState(list);
}

function canEditTodoList(listId) {
    const list = getTodoListState(listId);
    if (!list) return false;
    return list.is_subscribed !== true || list.share_type === 'collaborate';
}

/**
 * Return whether a task row may expose or execute editing actions.
 *
 * Owned tasks have no share type. Shared tasks are editable only through a
 * collaborate subscription; every other or unrecognized value fails closed.
 * The optional read-only override lets a containing list disable task actions
 * without introducing a second interpretation of share_type.
 */
function canEditTodo(todo, options = {}) {
    if (!todo || options.readOnly === true) return false;
    return todo.share_type == null || todo.share_type === 'collaborate';
}

function normalizeTodosPage(payload, fallbackOffset = 0) {
    const items = Array.isArray(payload) ? payload : (Array.isArray(payload?.items) ? payload.items : []);
    return {
        items,
        offset: Number(payload?.offset ?? fallbackOffset) || 0,
        hasMore: Array.isArray(payload) ? items.length >= TODOS_PAGE_LIMIT : Boolean(payload?.has_more),
    };
}

function buildTodosPagedUrl(path, offset = 0) {
    const params = new URLSearchParams({
        limit: String(TODOS_PAGE_LIMIT),
        offset: String(offset),
    });
    return `${path}?${params.toString()}`;
}

function todosT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function todosTf(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(todosT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

/**
 * Return the native-control value for a persisted due date.
 *
 * All-day values are calendar dates, so they must not be shifted through the
 * browser's local timezone. Timed values are converted from the API instant to
 * the local wall-clock values expected by separate date and time controls.
 */
function todoDueControlValues(dueAt, allDay = false) {
    if (!dueAt) return { date: '', time: '' };
    if (allDay) {
        const datePart = String(dueAt).match(/^(\d{4}-\d{2}-\d{2})/i)?.[1];
        return { date: datePart || '', time: '' };
    }

    const date = new Date(dueAt);
    if (Number.isNaN(date.getTime())) return { date: '', time: '' };
    const pad = value => String(value).padStart(2, '0');
    return {
        date: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
        time: `${pad(date.getHours())}:${pad(date.getMinutes())}`,
    };
}

/** Convert a native due-date control value to the API's ISO datetime format. */
function todoDueApiValue(dateValue, timeValue = '', allDay = false) {
    if (!dateValue) return null;
    if (allDay) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(dateValue)) return null;
        const date = new Date(`${dateValue}T00:00:00.000Z`);
        return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }

    if (!/^\d{2}:\d{2}$/.test(timeValue)) return null;
    const date = new Date(`${dateValue}T${timeValue}`);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** Hide the separate time control for all-day tasks without losing its draft. */
function syncTodoDueInputMode(timeInput, allDay) {
    if (!timeInput) return;
    timeInput.hidden = allDay;
    timeInput.disabled = allDay;
}

/** Use native, localized form validation for incomplete timed due dates. */
function validateTodoDueControls(dateInput, timeInput, allDay) {
    const dateValue = dateInput?.value || '';
    const timeValue = timeInput?.value || '';
    const missingDate = Boolean(timeValue && !dateValue);
    const missingTime = Boolean(dateValue && !allDay && !timeValue);
    if (!missingDate && !missingTime) return true;

    const invalidInput = missingDate ? dateInput : timeInput;
    invalidInput.required = true;
    invalidInput.focus?.();
    invalidInput.reportValidity?.();
    invalidInput.required = false;
    return false;
}

/** Parse an all-day date locally so display formatting cannot shift it. */
function todoDueDisplayDate(dueAt, allDay = false) {
    if (!allDay) return new Date(dueAt);
    const datePart = todoDueControlValues(dueAt, true).date;
    const match = datePart.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return new Date(dueAt);
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 23, 59, 59, 999);
}

// Color options for icon backgrounds
const TODOS_COLORS = [
    { id: 'red', name: 'Red', hex: '#E53935' },
    { id: 'orange', name: 'Orange', hex: '#FB8C00' },
    { id: 'amber', name: 'Amber', hex: '#FFB300' },
    { id: 'green', name: 'Green', hex: '#43A047' },
    { id: 'teal', name: 'Teal', hex: '#00897B' },
    { id: 'blue', name: 'Blue', hex: '#1E88E5' },
    { id: 'indigo', name: 'Indigo', hex: '#5C6BC0' },
    { id: 'purple', name: 'Purple', hex: '#8E24AA' },
    { id: 'pink', name: 'Pink', hex: '#D81B60' },
    { id: 'grey', name: 'Grey', hex: '#757575' },
];

const workspaceTodosIconUtils = window.WorkspaceIconUtils;
const TODOS_ICONS = workspaceTodosIconUtils.getWorkspaceIconOptions();
const TODO_DEFAULT_ICON_ID = 'checklist';

// Sort options for todos
const TODOS_SORT_OPTIONS = typeof todoSortOptions !== 'undefined' ? todoSortOptions : (Icons?.todoSortOptions || []);

const TODOS_VIEW_OPTIONS = [
    { id: 'all', nameKey: 'todos_view_all', name: 'All', icon: Icons.list },
    { id: 'today', nameKey: 'todos_view_today', name: 'Today', icon: Icons.calendar },
    { id: 'upcoming', nameKey: 'todos_view_upcoming', name: 'Upcoming', icon: Icons.arrow_top_right },
    { id: 'overdue', nameKey: 'todos_view_overdue', name: 'Overdue', icon: Icons.clock },
    { id: 'due_this_week', nameKey: 'todos_view_due_this_week', name: 'This week', icon: Icons.google_calendar || Icons.calendar },
    { id: 'high_priority', nameKey: 'todos_view_high_priority', name: 'High priority', icon: Icons.exclamation },
    { id: 'no_due_date', nameKey: 'todos_view_no_due_date', name: 'No due date', icon: Icons.calendar },
];

const TODOS_STATUS_OPTIONS = [
    { id: 'todo', nameKey: 'todos_status_todo', name: 'To do' },
    { id: 'doing', nameKey: 'todos_status_doing', name: 'Doing' },
    { id: 'done', nameKey: 'todos_status_done', name: 'Done' },
];

// ============================================================================
// API Helpers
// ============================================================================

const TodosAPI = {
    async request(input, init) {
        if (typeof window !== 'undefined' && typeof window.authedFetch === 'function') {
            return window.authedFetch(input, init);
        }
        return fetch(input, init);
    },

    async fetchLists(offset = 0) {
        const response = await this.request(buildTodosPagedUrl('/api/v1/todo/lists', offset), {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_error_fetch_lists', 'Failed to fetch todo lists'));
        return normalizeTodosPage(await response.json(), offset);
    },

    async createList(title, description, icon) {
        const response = await this.request('/api/v1/todo/lists', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ title, description, icon }),
        });
        if (!response.ok) throw new Error(todosT('todos_error_create_list', 'Failed to create todo list'));
        return response.json();
    },

    async deleteList(listId) {
        const response = await this.request(`/api/v1/todo/lists/${listId}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_error_delete_list', 'Failed to delete todo list'));
        return response.json();
    },

    async updateList(listId, data) {
        const response = await this.request(`/api/v1/todo/lists/${listId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(data),
        });
        if (!response.ok) throw new Error(todosT('todos_error_update_list', 'Failed to update todo list'));
        return response.json();
    },

    async fetchTodos(listId, filters = {}, offset = 0) {
        const url = new URL(buildTodosPagedUrl(`/api/v1/todo/lists/${listId}/todos`, offset), window.location.origin);
        if (filters.view && filters.view !== 'all') url.searchParams.set('view', filters.view);
        if (filters.q) url.searchParams.set('q', filters.q);
        if (filters.priority_min !== undefined && filters.priority_min !== null) url.searchParams.set('priority_min', String(filters.priority_min));
        if (filters.no_due_date) url.searchParams.set('no_due_date', 'true');
        if (filters.status) url.searchParams.set('status', filters.status);
        if (filters.sort) url.searchParams.set('sort', filters.sort);
        const response = await this.request(`${url.pathname}${url.search}`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_error_fetch_todos', 'Failed to fetch todos'));
        return normalizeTodosPage(await response.json(), offset);
    },

    async createTodo(listId, content, notes = null, priority = 0, extra = {}) {
        const body = { content, priority, ...extra };
        if (notes && notes.trim()) body.notes = notes.trim();
        
        const response = await this.request(`/api/v1/todo/lists/${listId}/todos`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error(todosT('todos_error_create_todo', 'Failed to create todo'));
        return response.json();
    },

    async updateTodo(todoId, data) {
        const response = await this.request(`/api/v1/todo/todos/${todoId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(data),
        });
        if (!response.ok) throw new Error(todosT('todos_error_update_todo', 'Failed to update todo'));
        return response.json();
    },

    async searchTodos(filters = {}, offset = 0) {
        const params = new URLSearchParams({ limit: String(TODOS_PAGE_LIMIT), offset: String(offset) });
        if (filters.q) params.set('q', filters.q);
        if (filters.view && filters.view !== 'all') params.set('view', filters.view);
        if (filters.priority_min !== undefined && filters.priority_min !== null) params.set('priority_min', String(filters.priority_min));
        if (filters.no_due_date) params.set('no_due_date', 'true');
        if (filters.status) params.set('status', filters.status);
        const response = await this.request(`/api/v1/todo/todos/search?${params.toString()}`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_error_search_todos', 'Failed to search todos'));
        return normalizeTodosPage(await response.json(), offset);
    },

    async toggleTodo(todoId) {
        const response = await this.request(`/api/v1/todo/todos/${todoId}/toggle`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_error_toggle_todo', 'Failed to toggle todo'));
        return response.json();
    },

    async deleteTodo(todoId) {
        const response = await this.request(`/api/v1/todo/todos/${todoId}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_error_delete_todo', 'Failed to delete todo'));
        return response.json();
    },

    async toggleMark(todoId) {
        const response = await this.request(`/api/v1/todo/todos/${todoId}/mark`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_error_toggle_mark', 'Failed to toggle mark'));
        return response.json();
    },

    async fetchMarkedTodos(offset = 0) {
        const response = await this.request(buildTodosPagedUrl('/api/v1/todo/marked', offset), {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_error_fetch_marked', 'Failed to fetch marked todos'));
        return normalizeTodosPage(await response.json(), offset);
    },

    // Sharing APIs
    async shareList(listId, shareType = 'live') {
        const response = await this.request('/api/v1/todo/lists/share', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ todo_list_id: listId, share_type: shareType }),
        });
        if (!response.ok) throw new Error(todosT('todos_share_error_create_link', 'Failed to share list'));
        return response.json();
    },

    async getShareStatus(listId) {
        const response = await this.request(`/api/v1/todo/lists/share/status?todo_list_id=${encodeURIComponent(listId)}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_share_error_status', 'Failed to get share status'));
        return response.json();
    },

    async deleteShare(listId, shareType = null) {
        const body = { todo_list_id: listId };
        if (shareType) body.share_type = shareType;
        const response = await this.request('/api/v1/todo/lists/share/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error(todosT('todos_share_error_remove', 'Failed to remove sharing'));
        return response.json();
    },

    async getSharedListPreview(shareId) {
        const response = await this.request(`/api/v1/todo/shared/${encodeURIComponent(shareId)}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) {
            let detail = todosT('todos_accept_not_found', 'Shared list not found');
            try {
                const errorBody = await response.json();
                if (errorBody?.detail) detail = errorBody.detail;
            } catch (_) { /* ignore parse errors */ }
            const error = new Error(detail);
            error.status = response.status;
            throw error;
        }
        return response.json();
    },

    async acceptSharedList(shareId) {
        const response = await this.request(`/api/v1/todo/shared/${encodeURIComponent(shareId)}/accept`, {
            method: 'POST',
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_accept_failed', 'Failed to accept shared list'));
        return response.json();
    },

    async cloneList(shareId) {
        const response = await this.request(`/api/v1/todo/clone/${encodeURIComponent(shareId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_accept_clone_failed', 'Failed to clone list'));
        return response.json();
    },

    async unsubscribeFromList(listId) {
        const response = await this.request(`/api/v1/todo/shared/${encodeURIComponent(listId)}/unsubscribe`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(todosT('todos_unsubscribe_failed', 'Failed to unsubscribe'));
        return response.json();
    },

    async fetchPublicUsers() {
        const users = [];
        const seenUserIds = new Set();
        let offset = 0;
        const limit = 100;
        while (true) {
            const response = await this.request(`/api/v1/users/public-users?limit=${limit}&offset=${offset}`, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
            });
            if (!response.ok) throw new Error(todosT('todos_share_load_users_failed', 'Failed to load users.'));
            const page = await response.json();
            const pageUsers = Array.isArray(page) ? page : [];
            pageUsers.forEach((user) => {
                const userId = String(user?.id || '').trim();
                if (!userId || seenUserIds.has(userId)) return;
                seenUserIds.add(userId);
                users.push(user);
            });
            const hasMore = String(response.headers.get('X-Has-More') || '').toLowerCase() === 'true';
            if (!hasMore || pageUsers.length === 0) break;
            offset += pageUsers.length;
        }
        return users;
    },

    async inviteUsersToList(listId, userIds, shareType = 'live') {
        const response = await this.request('/api/v1/todo/lists/invite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ item_id: listId, user_ids: userIds, share_type: shareType }),
        });
        if (!response.ok) throw new Error(todosT('todos_share_invite_failed', 'Failed to send invitations'));
        return response.json();
    },
};

// ============================================================================
// DOM Helpers
// ============================================================================

const TodosDOM = {
    get workspace() { return document.getElementById('todosWorkspace'); },
    get sidebar() { return document.getElementById('todosSidebar'); },
    get sidebarList() { return document.getElementById('todosSidebarList'); },
    get main() { return document.getElementById('todosMain'); },
    get emptyState() { return document.getElementById('todosEmptyState'); },
    get listView() { return document.getElementById('todosListView'); },
    get listHeader() { return document.getElementById('todosListHeader'); },
    get listContainer() { return document.getElementById('todosListContainer'); },
    get addContainer() { return document.getElementById('todosAddContainer'); },
    get addForm() { return document.getElementById('todosAddForm'); },
    get addInput() { return document.getElementById('todosAddInput'); },
    get addNotes() { return document.getElementById('todosAddNotes'); },
    get addDueAt() { return document.getElementById('todosAddDueAt'); },
    get addDueTime() { return document.getElementById('todosAddDueTime'); },
    get addAllDay() { return document.getElementById('todosAddAllDay'); },
    get addPriority() { return document.getElementById('todosAddPriority'); },
    get addTags() { return document.getElementById('todosAddTags'); },
    get addTools() { return document.getElementById('todosAddTools'); },
    get listEditorPage() { return document.getElementById('todosListEditorPage'); },
    get searchInput() { return document.getElementById('todosSidebarSearchInput'); },
    get searchClear() { return document.getElementById('todosSidebarSearchClear'); },
};

function showTodoListTitleError(inputEl, errorEl) {
    window.FormValidation?.showInputError(
        inputEl,
        errorEl,
        todosT('todos_create_list_name_error', 'Please enter a list name'),
    );
}

function clearTodoListTitleError(inputEl, errorEl) {
    window.FormValidation?.clearInputError(inputEl, errorEl);
}

/**
 * Resolve the DOM elements used by one of the todo icon pickers.
 *
 * This helper deliberately does not reference TodosManager. The shared picker
 * initializes its state immediately and may request these references while this
 * module is still being evaluated, before the manager constant is initialized.
 */
function getTodoIconPickerRefs(mode = 'create') {
    const isEdit = mode === 'edit';
    return {
        picker: document.getElementById(isEdit ? 'todosEditIconPicker' : 'todosIconPicker'),
        trigger: document.getElementById(isEdit ? 'todosEditIconPickerTrigger' : 'todosIconPickerTrigger'),
        preview: document.getElementById(isEdit ? 'todosEditIconPickerPreview' : 'todosIconPickerPreview'),
        svgGrid: document.getElementById(isEdit ? 'todosEditIconGrid' : 'todosIconGrid'),
        colorGrid: document.getElementById(isEdit ? 'todosEditColorGrid' : 'todosColorGrid'),
    };
}

const TodoCreateIconPicker = workspaceTodosIconUtils.createWorkspaceIconPicker({
    state: TodosState.iconPicker,
    refs: () => getTodoIconPickerRefs('create'),
    iconOptions: TODOS_ICONS,
    colors: TODOS_COLORS,
    defaultIconId: TODO_DEFAULT_ICON_ID,
    defaultColor: TODOS_COLORS[0].hex,
    translate: todosT,
});

const TodoEditIconPicker = workspaceTodosIconUtils.createWorkspaceIconPicker({
    state: TodosState.editIconPicker,
    refs: () => getTodoIconPickerRefs('edit'),
    iconOptions: TODOS_ICONS,
    colors: TODOS_COLORS,
    defaultIconId: TODO_DEFAULT_ICON_ID,
    defaultColor: TODOS_COLORS[0].hex,
    translate: todosT,
});

// ============================================================================
// Render Functions
// ============================================================================

const TodosRender = {
    getSortLabel(option) {
        return todosT(option.nameKey, option.name);
    },

    renderIcon(iconData, options = {}) {
        return workspaceTodosIconUtils.renderWorkspaceIcon(iconData, {
            size: options.size || 20,
            defaultIconId: TODO_DEFAULT_ICON_ID,
            iconOptions: TODOS_ICONS,
        });
    },

    sidebarSkeleton() {
        return `
            <div class="todos-sidebar-skeleton">
                ${[1, 2, 3].map(() => `
                    <div class="todos-skeleton-item">
                        <div class="todos-skeleton-icon"></div>
                        <div class="todos-skeleton-text"></div>
                    </div>
                `).join('')}
            </div>
        `;
    },

    todosSkeleton() {
        return `
            <div class="todos-loading">
                ${[1, 2, 3, 4].map(() => `
                    <div class="todo-skeleton">
                        <div class="todo-skeleton-checkbox"></div>
                        <div class="todo-skeleton-body">
                            <div class="todo-skeleton-title"></div>
                            <div class="todo-skeleton-notes"></div>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    },

    parseIcon(iconData) {
        const fallbackColor = TODOS_COLORS[0].hex;
        return workspaceTodosIconUtils.resolveWorkspaceStoredIcon(iconData, {
            iconOptions: TODOS_ICONS,
            defaultIconId: TODO_DEFAULT_ICON_ID,
            defaultColor: fallbackColor,
        });
    },

    dropdownItem({ action, listId, icon = '', label, isDanger = false, isShared = false }) {
        const classes = [
            'select-dropdown-button',
            isDanger ? 'select-dropdown-button-red' : '',
            isShared ? 'todos-dropdown-button-shared' : '',
        ].filter(Boolean).join(' ');

        return `
            <div class="select-dropdown-item">
                <button type="button" class="${classes}" data-action="${this.escapeHtml(action)}" data-list-id="${this.escapeHtml(listId)}">
                    ${icon}
                    <span>${this.escapeHtml(label)}</span>
                </button>
            </div>
        `;
    },

    listDropdownOptions(list) {
        const listId = list.id;
        const isSubscribed = list.is_subscribed === true;
        const hasAnyShare = list.clone_share_id || list.live_share_id || list.collaborate_share_id;
        const allowShare = canManageTodoListSharing(list);
        const isShared = hasAnyShare && !isSubscribed;

        if (isSubscribed) {
            return this.dropdownItem({
                action: 'unsubscribe',
                listId,
                icon: Icons.error,
                label: todosT('todos_remove_from_workspace', 'Remove from workspace'),
                isDanger: true,
            });
        }

        const editItem = this.dropdownItem({
            action: 'edit',
            listId,
            icon: Icons.create,
            label: todosT('todos_edit_list', 'Edit List'),
        });
        const deleteItem = this.dropdownItem({
            action: 'delete',
            listId,
            icon: Icons.trash,
            label: todosT('todos_delete_list', 'Delete List'),
            isDanger: true,
        });

        if (!allowShare) {
            return `${editItem}${deleteItem}`;
        }

        const subscriberCount = list.subscriber_count;
        const shareLabel = isShared
            ? (subscriberCount
                ? todosTf('todos_share_shared_count', 'Shared ({count})', { count: subscriberCount })
                : todosT('todos_share_shared', 'Shared'))
            : todosT('todos_share_action', 'Share');

        return `
            ${this.dropdownItem({
                action: 'share',
                listId,
                icon: Icons.connections,
                label: shareLabel,
                isShared,
            })}
            ${editItem}
            ${deleteItem}
        `;
    },

    listItem(list, isActive) {
        const iconData = this.parseIcon(list.icon);
        const iconColor = iconData.color || TODOS_COLORS[0].hex;
        const isSubscribed = list.is_subscribed === true;
        const shareType = list.share_type;
        const canEdit = !isSubscribed || shareType === 'collaborate';
        const dropdownOptions = this.listDropdownOptions(list);
        
        // Build badge content for subscribed lists
        let badgeContent = '';
        if (isSubscribed) {
            const ownerText = list.owner_name ? this.escapeHtml(todosTf('todos_shared_by_owner', 'by {owner}', { owner: list.owner_name })) : '';
            const typeLabel = shareType === 'collaborate'
                ? (canEdit ? todosT('todos_share_type_collaborate_label', 'Collaborate') : todosT('todos_share_view_only', 'View Only'))
                : todosT('todos_share_view_only', 'View Only');
            const escapedTypeLabel = this.escapeHtml(typeLabel);
            badgeContent = `<span class="todos-list-subscribed-badge">${ownerText}${ownerText && escapedTypeLabel ? ' · ' : ''}${escapedTypeLabel}</span>`;
        }
        
        const nameContent = isSubscribed 
            ? `${this.escapeHtml(list.title)} ${badgeContent}`
            : this.escapeHtml(list.title);
        
        return `
            <div class="todos-list-item ${isActive ? 'active' : ''}${isSubscribed ? ' subscribed' : ''}" 
                 data-list-id="${list.id}" 
                 data-is-subscribed="${isSubscribed}">
                <button type="button" class="todos-list-item-select-btn" data-list-id="${list.id}"
                        aria-label="${this.escapeHtml(list.title)}" aria-pressed="${isActive}">
                    <span class="todos-list-item-icon has-color" style="--icon-bg-color: ${iconColor}">
                        ${this.renderIcon(iconData)}
                    </span>
                    <span class="todos-list-item-name">${nameContent}</span>
                </button>
                <button type="button" class="todos-list-item-menu-btn" data-list-id="${list.id}" aria-label="${this.escapeHtml(todosT('todos_list_options_aria', 'List options'))}">
                    ${Icons.ellipsisVertical}
                </button>
                <div class="select-dropdown" data-todo-dropdown data-list-id="${list.id}">
                    ${dropdownOptions}
                </div>
            </div>
        `;
    },

    todoItem(todo, showListInfo = false, options = {}) {
        // Task responses carry server-computed capabilities. The option remains
        // as a conservative caller override for list-level read-only views.
        const canEdit = canEditTodo(todo, options);
        const canDelete = canEdit && todo.can_delete !== false;
        const readOnly = !canEdit;
        const completedClass = todo.is_done ? 'completed' : '';
        const markedClass = todo.is_marked ? 'marked' : '';
        const notesHtml = todo.notes ? `<p class="todo-item-notes">${this.escapeHtml(todo.notes)}</p>` : '';
        const tags = Array.isArray(todo.tags) ? todo.tags : [];
        const subtasks = Array.isArray(todo.subtasks) ? todo.subtasks : [];
        const links = Array.isArray(todo.links) ? todo.links : [];
        const attachments = Array.isArray(todo.attachments) ? todo.attachments : [];
        const completedSubtasks = subtasks.filter((item) => item?.is_done === true).length;
        const tagHtml = tags.length ? `<div class="todo-item-tags">${tags.map(tag => `<span>${this.escapeHtml(tag)}</span>`).join('')}</div>` : '';
        const checklistHtml = subtasks.length ? `<div class="todo-item-checklist">${completedSubtasks}/${subtasks.length} ${this.escapeHtml(todosT('todos_subtasks_label', 'subtasks'))}</div>` : '';
        const linkHtml = links.length || attachments.length ? `<div class="todo-item-links">${links.length ? `${links.length} ${this.escapeHtml(todosT('todos_links_label', 'links'))}` : ''}${links.length && attachments.length ? ' · ' : ''}${attachments.length ? `${attachments.length} ${this.escapeHtml(todosT('todos_attachments_label', 'attachments'))}` : ''}</div>` : '';
        
        let metaHtml = '';
        const parts = [];
        
        if (showListInfo && todo.list_title) {
            const iconData = this.parseIcon(todo.list_icon);
            parts.push(`
                <span class="todo-item-list-info">
                    <span class="todo-item-list-icon" style="background: ${iconData.color || '#757575'}">
                        ${this.renderIcon(iconData, { size: 16 })}
                    </span>
                    <span class="todo-item-list-name">${this.escapeHtml(todo.list_title)}</span>
                </span>
            `);
        }
        
        if (todo.due_at) {
            const dueDate = todoDueDisplayDate(todo.due_at, todo.all_day);
            const isOverdue = !todo.is_done && dueDate < new Date();
            parts.push(`
                <span class="todo-item-due ${isOverdue ? 'overdue' : ''}">
                    ${Icons.clock}
                    ${todo.all_day ? this.escapeHtml(this.formatDate(dueDate)) : this.escapeHtml(this.formatDateTime(dueDate))}
                </span>
            `);
        }
        if (todo.priority > 0) {
            const priorityClass = todo.priority >= 2 ? 'high' : 'medium';
            parts.push(`
                <span class="todo-item-priority ${priorityClass}">
                    ${'!'.repeat(todo.priority)}
                </span>
            `);
        }
        
        if (parts.length > 0) {
            metaHtml = `<div class="todo-item-meta">${parts.join('')}</div>`;
        }

        return `
            <div class="todo-item ${completedClass} ${markedClass}" data-todo-id="${todo.id}" data-todo-list="${todo.todo_list}" data-status="${todo.status || 'todo'}" draggable="${!readOnly && !TodosState.todosHasMore && (TodosState.sortBy === 'manual' || TodosState.viewMode === 'board') ? 'true' : 'false'}">
                <label class="todo-checkbox ${readOnly ? 'disabled' : ''}">
                    <input type="checkbox" ${todo.is_done ? 'checked' : ''} ${readOnly ? 'disabled' : ''} aria-label="${this.escapeHtml(todo.is_done ? todosT('todos_mark_incomplete_aria', 'Mark as incomplete') : todosT('todos_mark_complete_aria', 'Mark as complete'))}">
                    <div class="todo-checkbox-visual">
                        ${Icons.check}
                    </div>
                </label>
                <div class="todo-item-body">
                    <p class="todo-item-content" tabindex="${readOnly ? '-1' : '0'}">${this.escapeHtml(todo.content)}</p>
                    ${notesHtml}
                    ${tagHtml}
                    ${checklistHtml}
                    ${linkHtml}
                    ${metaHtml}
                </div>
                ${canEdit ? `<button type="button" class="todo-edit-btn" data-todo-id="${todo.id}" aria-label="${this.escapeHtml(todosT('todos_edit_todo_aria', 'Edit todo'))}">${Icons.edit}</button>` : ''}
                ${canDelete ? `<button type="button" class="todo-delete-btn" data-todo-id="${todo.id}" aria-label="${this.escapeHtml(todosT('common_delete', 'Delete'))}">${Icons.trash}</button>` : ''}
                ${canEdit ? `<button type="button" class="todo-mark-btn ${todo.is_marked ? 'active' : ''}" data-todo-id="${todo.id}" aria-label="${this.escapeHtml(todo.is_marked ? todosT('todos_remove_from_marked_aria', 'Remove from marked') : todosT('todos_add_to_marked_aria', 'Add to marked'))}">${Icons.bookmarkFilled}</button>` : ''}
            </div>
        `;
    },

    markedListHeader() {
        return `
            <button type="button" class="todos-mobile-back-btn" id="todosMobileBackBtn" aria-label="${this.escapeHtml(todosT('todos_back_to_lists_aria', 'Back to lists'))}">
                ${Icons.chevronLeft}
                <span>${this.escapeHtml(todosT('todos_lists_label', 'Lists'))}</span>
            </button>
            <div class="todos-list-header-top">
                <div class="todos-list-header-icon has-color" style="--icon-bg-color: #FB8C00">
                    ${Icons.bookmark}
                </div>
                <div class="todos-list-header-info">
                    <h2 class="todos-list-header-title">${this.escapeHtml(todosT('todos_marked_title', 'Marked'))}</h2>
                    <p class="todos-list-header-description">${this.escapeHtml(todosT('todos_marked_description', 'All your marked todos across all lists'))}</p>
                </div>
            </div>
        `;
    },

    markedListItem(count) {
        return `
            <div class="todos-list-item todos-marked-list-item ${TodosState.selectedListId === TodosState.MARKED_LIST_ID ? 'active' : ''}" 
                 data-list-id="${TodosState.MARKED_LIST_ID}">
                <button type="button" class="todos-list-item-select-btn" data-list-id="${TodosState.MARKED_LIST_ID}"
                        aria-label="${this.escapeHtml(todosT('todos_marked_title', 'Marked'))}"
                        aria-pressed="${TodosState.selectedListId === TodosState.MARKED_LIST_ID}">
                    <span class="todos-list-item-icon has-color" style="--icon-bg-color: #FB8C00">
                        ${Icons.bookmark}
                    </span>
                    <span class="todos-list-item-name">${this.escapeHtml(todosT('todos_marked_title', 'Marked'))}</span>
                    <span class="todos-list-item-count">${count}</span>
                </button>
            </div>
        `;
    },

    emptyMarkedState() {
        return `
            <div class="todos-list-empty">
                <div class="todos-list-empty-icon">
                    ${Icons.bookmark}
                </div>
                <p class="todos-list-empty-title">${this.escapeHtml(todosT('todos_marked_empty_title', 'No marked todos'))}</p>
                <p class="todos-list-empty-text">${this.escapeHtml(todosT('todos_marked_empty_text', 'Mark important todos with the bookmark icon to see them here'))}</p>
            </div>
        `;
    },

    listHeader(list) {
        const iconData = this.parseIcon(list.icon);
        const iconColor = iconData.color || TODOS_COLORS[0].hex;
        const descriptionHtml = list.description ? `<p class="todos-list-header-description">${this.escapeHtml(list.description)}</p>` : '';
        const currentSort = TODOS_SORT_OPTIONS.find(s => s.id === TodosState.sortBy) || TODOS_SORT_OPTIONS[0];
        const currentFilter = TODOS_VIEW_OPTIONS.find(view => view.id === TodosState.activeView) || TODOS_VIEW_OPTIONS[0];
        const filterAriaLabel = todosT('todos_filter_aria', 'Filter tasks');
        
        return `
            <button type="button" class="todos-mobile-back-btn" id="todosMobileBackBtn" aria-label="${this.escapeHtml(todosT('todos_back_to_lists_aria', 'Back to lists'))}">
                ${Icons.chevronLeft}
                <span>${this.escapeHtml(todosT('todos_lists_label', 'Lists'))}</span>
            </button>
            <div class="todos-list-header-top">
                <div class="todos-list-header-icon has-color" style="--icon-bg-color: ${iconColor}">
                    ${this.renderIcon(iconData)}
                </div>
                <div class="todos-list-header-info">
                    <h2 class="todos-list-header-title">${this.escapeHtml(list.title)}</h2>
                    ${descriptionHtml}
                </div>
                <div class="todos-list-header-actions">
                    <button type="button" class="todos-board-toggle ${TodosState.viewMode === 'board' ? 'active' : ''}" id="todosBoardToggle" aria-label="${this.escapeHtml(todosT('todos_board_toggle_aria', 'Toggle board view'))}">
                        ${Icons.pause}
                        <span>${this.escapeHtml(TodosState.viewMode === 'board' ? todosT('todos_view_list_mode', 'List') : todosT('todos_view_board_mode', 'Board'))}</span>
                    </button>
                    <div class="todos-header-selector todos-filter-selector" id="todosFilterSelector">
                        <button type="button" class="todos-header-trigger todos-filter-trigger" id="todosFilterTrigger" aria-haspopup="menu" aria-expanded="false" aria-controls="todosFilterDropdown" aria-label="${this.escapeHtml(`${filterAriaLabel}: ${todosT(currentFilter.nameKey, currentFilter.name)}`)}">
                            ${Icons.filter}
                            <span>${this.escapeHtml(todosT(currentFilter.nameKey, currentFilter.name))}</span>
                            ${Icons.chevron}
                        </button>
                        <div class="todos-header-dropdown todos-filter-dropdown" id="todosFilterDropdown" role="menu" aria-label="${this.escapeHtml(filterAriaLabel)}">
                            ${TODOS_VIEW_OPTIONS.map(view => `
                                <button type="button" class="todos-header-option todos-filter-option ${view.id === TodosState.activeView ? 'selected' : ''}" data-view="${view.id}" role="menuitemradio" aria-checked="${view.id === TodosState.activeView}">
                                    <span class="todos-header-option-icon" aria-hidden="true">${view.icon || ''}</span>
                                    <span>${this.escapeHtml(todosT(view.nameKey, view.name))}</span>
                                    <span class="todos-header-option-check" aria-hidden="true">${Icons.check}</span>
                                </button>
                            `).join('')}
                        </div>
                    </div>
                    <div class="todos-header-selector todos-sort-selector" id="todosSortSelector">
                        <button type="button" class="todos-header-trigger todos-sort-trigger" id="todosSortTrigger" aria-haspopup="menu" aria-expanded="false" aria-controls="todosSortDropdown">
                            ${currentSort.icon}
                            <span>${this.escapeHtml(this.getSortLabel(currentSort))}</span>
                            ${Icons.chevron}
                        </button>
                        <div class="todos-header-dropdown todos-sort-dropdown" id="todosSortDropdown" role="menu">
                            ${TODOS_SORT_OPTIONS.map(option => `
                                <button type="button" class="todos-header-option todos-sort-option ${option.id === TodosState.sortBy ? 'selected' : ''}" data-sort="${option.id}" role="menuitemradio" aria-checked="${option.id === TodosState.sortBy}">
                                    <span class="todos-header-option-icon" aria-hidden="true">
                                        ${option.icon}
                                    </span>
                                    <span>${this.escapeHtml(this.getSortLabel(option))}</span>
                                    <span class="todos-header-option-check" aria-hidden="true">${Icons.check}</span>
                                </button>
                            `).join('')}
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    emptyListState(filtered = false) {
        const title = filtered
            ? todosT('todos_empty_filter_title', 'No matching tasks')
            : todosT('todos_empty_list_title', 'No todos yet');
        const text = filtered
            ? todosT('todos_empty_filter_text', 'Try a different filter.')
            : todosT('todos_empty_list_text', 'Add your first todo below');
        return `
            <div class="todos-list-empty">
                <div class="todos-list-empty-icon">
                    ${Icons.create}
                </div>
                <p class="todos-list-empty-title">${this.escapeHtml(title)}</p>
                <p class="todos-list-empty-text">${this.escapeHtml(text)}</p>
            </div>
        `;
    },

    escapeHtml(text) {
        return workspaceTodosIconUtils.escapeHtml(text || '');
    },

    formatDate(date) {
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const tomorrow = new Date(today);
        tomorrow.setDate(tomorrow.getDate() + 1);
        const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());

        if (dateOnly.getTime() === today.getTime()) return todosT('todos_due_today', 'Today');
        if (dateOnly.getTime() === tomorrow.getTime()) return todosT('todos_due_tomorrow', 'Tomorrow');
        
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    },

    formatDateTime(date) {
        return `${this.formatDate(date)} ${date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;
    },
};

// ============================================================================
// Todos Manager
// ============================================================================

const TodosManager = {
    async init() {
        if (TodosState.initialized) return;

        this.setupEventListeners();
        this.setupIconPickerListeners();
        this.registerEscapeHandlers();
        this.registerListEditorUnsavedGuard();
        this.registerListEditorHistoryHandler();
        TodosState.initialized = true;
    },

    /**
     * Upgrade a native todo select with the shared site-wide control. The
     * helper appends its wrapper, so restore the field's original position to
     * avoid changing the order of controls in flex and grid forms.
     */
    upgradeTodoSelect(select, className, placeholder) {
        if (!select || select._singleSelect || typeof window.upgradeAdminSingleSelect !== 'function') {
            return select?._singleSelect || null;
        }

        const originalNextSibling = select.nextSibling;
        const meta = window.upgradeAdminSingleSelect(select, {
            key: select.id || 'todos-priority',
            placeholder,
        });
        if (!meta?.wrapper) return null;

        meta.wrapper.classList.add(className);
        if (originalNextSibling?.parentNode) {
            originalNextSibling.parentNode.insertBefore(meta.wrapper, originalNextSibling);
        }
        return meta;
    },

    /** Upgrade priority selects with their translated empty-state label. */
    upgradePrioritySelect(select, className) {
        return this.upgradeTodoSelect(
            select,
            className,
            todosT('todos_priority_none', 'No priority'),
        );
    },

    registerEscapeHandlers() {
        if (typeof window === 'undefined' || typeof window.registerEscapeHandler !== 'function') {
            return;
        }

        window.registerEscapeHandler({
            id: 'workspace-todos-transient-dropdowns',
            priority: 120,
            isActive: () => Boolean(
                TodosState.openDropdownListId ||
                TodosState.sortDropdownOpen ||
                TodosState.filterDropdownOpen ||
                TodosState.addTodoOpenPopover ||
                TodosState.iconPicker.isOpen ||
                TodosState.editIconPicker.isOpen
            ),
            close: () => {
                this.closeAllDropdowns();
                this.toggleSortDropdown(false);
                this.toggleFilterDropdown(false);
                this.closeAddTodoPopover({ restoreFocus: true });
                this.toggleIconPicker('create', false);
                this.toggleIconPicker('edit', false);
            },
        });

        window.registerEscapeHandler({
            id: 'workspace-todos-list-form',
            priority: 80,
            isActive: () => Boolean(TodosState.listEditorMode),
            close: () => {
                this.requestListEditorExit(() => this.closeListEditorPage({ useHistory: true }));
            },
        });
    },

    setupEventListeners() {
        // Sidebar list item clicks
        const sidebarList = TodosDOM.sidebarList;
        if (sidebarList) {
            sidebarList.addEventListener('click', (e) => {
                // Handle menu button click
                const menuBtn = e.target.closest('.todos-list-item-menu-btn');
                if (menuBtn) {
                    e.preventDefault();
                    e.stopPropagation();
                    const listId = menuBtn.dataset.listId;
                    this.toggleListDropdown(listId);
                    return;
                }

                // Handle dropdown option click
                const dropdownOption = e.target.closest('.select-dropdown-button[data-action]');
                if (dropdownOption) {
                    e.preventDefault();
                    e.stopPropagation();
                    const action = dropdownOption.dataset.action;
                    const listId = dropdownOption.dataset.listId;
                    this.closeAllDropdowns();
                    if (action === 'edit') {
                        this.showEditListPage(listId);
                    } else if (action === 'delete') {
                        this.showDeleteListWarning(listId);
                    } else if (action === 'share') {
                        const list = getTodoListState(listId);
                        if (!canManageTodoListSharing(list)) {
                            const message = this.t('todos_share_disabled_by_admin', 'Sharing disabled by your group admin');
                            if (typeof notifyWarning === 'function') {
                                notifyWarning(message);
                            } else if (typeof showNotification === 'function') {
                                showNotification(message, 'warning');
                            }
                        } else {
                            this.showShareModal(listId);
                        }
                    } else if (action === 'unsubscribe') {
                        this.handleUnsubscribe(listId);
                    } else if (action === 'share-disabled') {
                        const message = this.t('todos_share_disabled_by_admin', 'Sharing disabled by your group admin');
                        if (typeof notifyWarning === 'function') {
                            notifyWarning(message);
                        } else if (typeof showNotification === 'function') {
                            showNotification(message, 'warning');
                        }
                    }
                    return;
                }

                // Handle the row's primary action. Native button keyboard
                // activation cannot leak in from the sibling options button.
                const selectBtn = e.target.closest('.todos-list-item-select-btn');
                if (selectBtn) {
                    const listId = selectBtn.dataset.listId;
                    this.selectList(listId);
                }
            });
        }

        // Close dropdowns when clicking outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.todos-list-item-menu-btn') && !e.target.closest('[data-todo-dropdown]')) {
                this.closeAllDropdowns();
            }
        });

        // Add list button
        const addListBtn = document.getElementById('todosSidebarAddBtn');
        if (addListBtn) {
            addListBtn.addEventListener('click', (event) => this.showCreateListPage(event.currentTarget));
        }

        // Add todo input
        const addInput = TodosDOM.addInput;
        if (addInput) {
            addInput.addEventListener('focus', () => this.showAddTodoExpanded());
            addInput.addEventListener('keydown', (event) => this.handleAddTodoComposerKeydown(event));
        }

        // Add todo notes
        const addNotes = TodosDOM.addNotes;
        if (addNotes) {
            addNotes.addEventListener('focus', () => this.showAddTodoExpanded());
            addNotes.addEventListener('keydown', (event) => this.handleAddTodoComposerKeydown(event));
        }

        this.setupAddTodoTools();

        // Clicking elsewhere collapses the composer without throwing away its
        // draft. Returning to the field restores the note and all metadata.
        document.addEventListener('click', (event) => {
            const form = TodosDOM.addForm;
            if (TodosState.addTodoExpanded && form && !form.contains(event.target)) {
                this.collapseAddTodoComposer();
            }
        });

        // Refresh computed accessible labels after the translated option text
        // changes; all visible popover copy is translated in-place globally.
        document.addEventListener('i18n:updated', () => {
            this.updateAddTodoToolStates();
        });

        // Todo list container - toggle checkboxes and mark buttons
        const listContainer = TodosDOM.listContainer;
        if (listContainer) {
            listContainer.addEventListener('change', (e) => {
                if (e.target.type === 'checkbox') {
                    const todoItem = e.target.closest('.todo-item');
                    if (todoItem) {
                        const todoId = todoItem.dataset.todoId;
                        this.handleToggleTodo(todoId, e.target.checked);
                    }
                }
            });

            listContainer.addEventListener('click', (e) => {
                const markBtn = e.target.closest('.todo-mark-btn');
                if (markBtn) {
                    e.preventDefault();
                    e.stopPropagation();
                    const todoId = markBtn.dataset.todoId;
                    this.handleToggleMark(todoId);
                }
                const editBtn = e.target.closest('.todo-edit-btn');
                if (editBtn) {
                    e.preventDefault();
                    e.stopPropagation();
                    this.showTodoDetails(editBtn.dataset.todoId);
                }
                const deleteBtn = e.target.closest('.todo-delete-btn');
                if (deleteBtn) {
                    e.preventDefault();
                    e.stopPropagation();
                    this.deleteTodo(deleteBtn.dataset.todoId);
                }
            });

            listContainer.addEventListener('dblclick', (e) => {
                const content = e.target.closest('.todo-item-content');
                if (content) this.startInlineEdit(content.closest('.todo-item')?.dataset.todoId);
            });

            listContainer.addEventListener('keydown', (e) => {
                const content = e.target.closest('.todo-item-content');
                if (content && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    this.startInlineEdit(content.closest('.todo-item')?.dataset.todoId);
                }
            });

            listContainer.addEventListener('dragstart', (e) => this.handleTodoDragStart(e));
            listContainer.addEventListener('dragover', (e) => this.handleTodoDragOver(e));
            listContainer.addEventListener('drop', (e) => this.handleTodoDrop(e));
        }

        document.addEventListener('keydown', (e) => this.handleGlobalShortcuts(e));

        // Mobile sidebar toggle
        const sidebarHeader = document.querySelector('.todos-sidebar-header');
        if (sidebarHeader && window.innerWidth <= 768) {
            sidebarHeader.addEventListener('click', () => {
                const sidebar = TodosDOM.sidebar;
                if (sidebar) {
                    sidebar.classList.toggle('collapsed');
                }
            });
        }

        // Search input
        const searchInput = TodosDOM.searchInput;
        if (searchInput) {
            searchInput.addEventListener('input', (e) => this.handleSearchInput(e.target.value));
            searchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    this.clearSearch();
                    searchInput.blur();
                }
            });
        }

        // Search clear button
        const searchClear = TodosDOM.searchClear;
        if (searchClear) {
            searchClear.addEventListener('click', () => this.clearSearch());
        }
    },

    // ============================================================================
    // Search Methods
    // ============================================================================

    handleSearchInput(query) {
        const trimmedQuery = query.trim();
        TodosState.searchQuery = trimmedQuery;
        
        // Update clear button visibility
        const searchClear = TodosDOM.searchClear;
        if (searchClear) {
            searchClear.classList.toggle('visible', trimmedQuery.length > 0);
        }
        
        if (trimmedQuery.length === 0) {
            if (TodosState.searchTimer) window.clearTimeout(TodosState.searchTimer);
            TodosState.isSearching = false;
            TodosState.searchResults = [];
            TodosState.searchHasMore = false;
            this.exitSearchMode();
            return;
        }
        
        TodosState.isSearching = true;
        if (TodosState.searchTimer) window.clearTimeout(TodosState.searchTimer);
        TodosState.searchTimer = window.setTimeout(() => {
            TodosState.searchTimer = null;
            this.performSearch(trimmedQuery);
        }, 250);
    },

    async performSearch(query, { append = false } = {}) {
        const normalizedQuery = String(query || '').trim();
        if (!normalizedQuery || !TodosState.isSearching) return;
        if (append && (TodosState.searchLoading || !TodosState.searchHasMore)) return;
        const offset = append ? TodosState.searchOffset : 0;
        const requestToken = append ? TodosState.searchRequestToken : Symbol('todo-search');
        if (!append) {
            TodosState.searchRequestToken = requestToken;
            TodosState.searchOffset = 0;
            TodosState.searchHasMore = false;
        }
        TodosState.searchLoading = true;
        try {
            const page = await TodosAPI.searchTodos({ q: normalizedQuery }, offset);
            if (TodosState.searchRequestToken !== requestToken || TodosState.searchQuery !== normalizedQuery) return;
            TodosState.searchResults = append
                ? this.appendUniqueTodos(TodosState.searchResults, page.items)
                : page.items;
            TodosState.searchOffset = offset + page.items.length;
            TodosState.searchHasMore = page.hasMore;
            this.renderSearchResults();
        } catch (error) {
            console.error('Failed to search todos:', error);
            if (!append) TodosState.searchResults = [];
            this.renderSearchResults();
        } finally {
            if (TodosState.searchRequestToken === requestToken) TodosState.searchLoading = false;
        }
    },

    async loadMoreSearchResults() {
        await this.performSearch(TodosState.searchQuery, { append: true });
    },

    async cacheAllTodos() {
        // Fetch todos from all lists
        const allTodos = [];
        
        for (const list of TodosState.lists) {
            try {
                const todos = (await TodosAPI.fetchTodos(list.id)).items;
                // Add list info to each todo for display
                todos.forEach(todo => {
                    todo.list_title = list.title;
                    todo.list_icon = list.icon;
                });
                allTodos.push(...todos);
            } catch (error) {
                console.error(`Failed to fetch todos for list ${list.id}:`, error);
            }
        }
        
        TodosState.allTodosCache = allTodos;
    },

    renderSearchResults() {
        const listContainer = TodosDOM.listContainer;
        const listHeader = TodosDOM.listHeader;
        const listView = TodosDOM.listView;
        const emptyState = TodosDOM.emptyState;
        const addContainer = TodosDOM.addContainer;
        
        if (!listContainer || !listHeader) return;
        
        const query = TodosState.searchQuery;
        const results = TodosState.searchResults;
        
        // Show the list view
        if (emptyState) emptyState.style.display = 'none';
        if (listView) listView.style.display = 'flex';
        if (addContainer) addContainer.style.display = 'none';
        
        // Update header for search mode
        const backToListsLabel = todosT('todos_back_to_lists_aria', 'Back to lists');
        listHeader.innerHTML = `
            <button type="button" class="todos-mobile-back-btn" id="todosMobileBackBtn" aria-label="${TodosRender.escapeHtml(backToListsLabel)}" data-i18n-attr="aria-label:todos_back_to_lists_aria">
                ${Icons.chevronLeft}
                <span data-i18n="todos_lists_label">${TodosRender.escapeHtml(todosT('todos_lists_label', 'Lists'))}</span>
            </button>
            <div class="todos-list-header-top">
                <div class="todos-list-header-icon has-color" style="--icon-bg-color: var(--primary-color)">
                    ${Icons.magnifyingGlass}
                </div>
                <div class="todos-list-header-info">
                    <h2 class="todos-list-header-title" data-i18n="todos_search_results_title">${TodosRender.escapeHtml(todosT('todos_search_results_title', 'Search results'))}</h2>
                    <p class="todos-list-header-description">${TodosRender.escapeHtml(todosTf('todos_searching_for', 'Searching for "{query}"', { query }))}</p>
                </div>
            </div>
        `;
        
        // Attach mobile back button handler
        const mobileBackBtn = document.getElementById('todosMobileBackBtn');
        if (mobileBackBtn) {
            mobileBackBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.clearSearch();
                this.hideMobileContent();
            });
        }
        
        if (results.length === 0) {
            listContainer.innerHTML = `
                <div class="todos-search-empty">
                    <div class="todos-search-empty-icon">
                        ${Icons.magnifyingGlass}
                    </div>
                    <p class="todos-search-empty-title" data-i18n="todos_search_no_results">${TodosRender.escapeHtml(todosT('todos_search_no_results', 'No results found'))}</p>
                    <p class="todos-search-empty-text">${TodosRender.escapeHtml(todosTf('todos_search_no_match', 'No todos match "{query}"', { query }))}</p>
                </div>
            `;
            return;
        }
        
        const resultsHeader = `
            <div class="todos-search-results-header">
                <span class="todos-search-results-count">${TodosRender.escapeHtml(todosTf(results.length === 1 && !TodosState.searchHasMore ? 'todos_search_result_count_one' : 'todos_search_result_count_other', results.length === 1 && !TodosState.searchHasMore ? '{count} result' : '{count} results', { count: `${results.length}${TodosState.searchHasMore ? '+' : ''}` }))}</span>
                <button type="button" class="todos-search-results-clear" id="todosSearchResultsClear" data-i18n="todos_search_clear">${TodosRender.escapeHtml(todosT('todos_search_clear', 'Clear'))}</button>
            </div>
        `;
        
        const resultsHtml = results
            .map(todo => this.renderSearchResultTodo(todo, query))
            .join('');
        
        listContainer.innerHTML = resultsHeader + resultsHtml;
        this.setupTodoContentInfiniteScroll('search');
        
        // Add event listener for clear button
        const clearBtn = document.getElementById('todosSearchResultsClear');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => this.clearSearch());
        }
        
        // Add event listeners for todo interactions
        listContainer.querySelectorAll('.todo-item').forEach(item => {
            const checkbox = item.querySelector('input[type="checkbox"]');
            if (checkbox) {
                checkbox.addEventListener('change', (e) => {
                    const todoId = item.dataset.todoId;
                    this.handleToggleTodo(todoId, e.target.checked);
                });
            }
            
            const markBtn = item.querySelector('.todo-mark-btn');
            if (markBtn) {
                markBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const todoId = markBtn.dataset.todoId;
                    this.handleToggleMark(todoId);
                });
            }

        });
        
        // Show main content on mobile
        this.showMobileContent();
    },

    renderSearchResultTodo(todo, query) {
        const completedClass = todo.is_done ? 'completed' : '';
        const markedClass = todo.is_marked ? 'marked' : '';
        const canEdit = this.canEditList(todo.todo_list, todo);
        const canDelete = canEdit && todo.can_delete !== false;
        
        // Highlight matching text
        const highlightedContent = this.highlightMatches(todo.content, query);
        const highlightedNotes = todo.notes ? this.highlightMatches(todo.notes, query) : '';
        
        const notesHtml = highlightedNotes ? `<p class="todo-item-notes">${highlightedNotes}</p>` : '';
        
        // Build meta info including list info
        let metaHtml = '';
        const parts = [];
        
        if (todo.list_title) {
            const iconData = TodosRender.parseIcon(todo.list_icon);
            parts.push(`
                <span class="todo-item-list-info">
                    <span class="todo-item-list-icon" style="background: ${iconData.color || '#757575'}">
                        ${TodosRender.renderIcon(iconData, { size: 16 })}
                    </span>
                    <span class="todo-item-list-name">${TodosRender.escapeHtml(todo.list_title)}</span>
                </span>
            `);
        }
        
        if (todo.priority > 0) {
            const priorityClass = todo.priority >= 2 ? 'high' : 'medium';
            parts.push(`
                <span class="todo-item-priority ${priorityClass}">
                    ${'!'.repeat(todo.priority)}
                </span>
            `);
        }
        
        if (parts.length > 0) {
            metaHtml = `<div class="todo-item-meta">${parts.join('')}</div>`;
        }

        return `
            <div class="todo-item ${completedClass} ${markedClass}" data-todo-id="${todo.id}" data-todo-list="${todo.todo_list}">
                <label class="todo-checkbox ${canEdit ? '' : 'disabled'}">
                    <input type="checkbox" ${todo.is_done ? 'checked' : ''} ${canEdit ? '' : 'disabled'} aria-label="${TodosRender.escapeHtml(todo.is_done ? todosT('todos_mark_incomplete_aria', 'Mark as incomplete') : todosT('todos_mark_complete_aria', 'Mark as complete'))}">
                    <div class="todo-checkbox-visual">
                        ${Icons.check}
                    </div>
                </label>
                <div class="todo-item-body">
                    <p class="todo-item-content">${highlightedContent}</p>
                    ${notesHtml}
                    ${metaHtml}
                </div>
                ${canDelete ? `<button type="button" class="todo-delete-btn" data-todo-id="${todo.id}" aria-label="${TodosRender.escapeHtml(todosT('common_delete', 'Delete'))}">${Icons.trash}</button>` : ''}
                ${canEdit ? `<button type="button" class="todo-mark-btn ${todo.is_marked ? 'active' : ''}" data-todo-id="${todo.id}" aria-label="${TodosRender.escapeHtml(todo.is_marked ? todosT('todos_remove_from_marked_aria', 'Remove from marked') : todosT('todos_add_to_marked_aria', 'Add to marked'))}">${Icons.bookmarkFilled}</button>` : ''}
            </div>
        `;
    },

    highlightMatches(text, query) {
        if (!query || !text) return TodosRender.escapeHtml(text);
        
        const escapedText = TodosRender.escapeHtml(text);
        const escapedQuery = TodosRender.escapeHtml(query);
        const regex = new RegExp(`(${escapedQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
        
        return escapedText.replace(regex, '<span class="todos-search-highlight">$1</span>');
    },

    clearSearch(options = {}) {
        const { skipExit = false } = options;

        TodosState.searchQuery = '';
        TodosState.searchResults = [];
        TodosState.isSearching = false;
        TodosState.searchHasMore = false;
        TodosState.searchOffset = 0;
        TodosState.searchRequestToken = null;
        if (TodosState.searchTimer) window.clearTimeout(TodosState.searchTimer);
        TodosState.searchTimer = null;
        
        const searchInput = TodosDOM.searchInput;
        if (searchInput) {
            searchInput.value = '';
        }
        
        const searchClear = TodosDOM.searchClear;
        if (searchClear) {
            searchClear.classList.remove('visible');
        }
        
        if (!skipExit) {
            this.exitSearchMode();
        }
    },

    exitSearchMode() {
        // Reset to normal view
        if (TodosState.selectedListId) {
            this.selectList(TodosState.selectedListId, { force: true });
        } else {
            // Show empty state
            const emptyState = TodosDOM.emptyState;
            const listView = TodosDOM.listView;
            if (emptyState) emptyState.style.display = 'flex';
            if (listView) listView.style.display = 'none';
        }
    },

    invalidateTodosCache() {
        // Clear the cache when todos are modified
        TodosState.allTodosCache = [];
    },

    async loadLists() {
        const sidebarList = TodosDOM.sidebarList;
        if (!sidebarList) return;

        TodosState.isLoadingLists = true;
        sidebarList.innerHTML = TodosRender.sidebarSkeleton();
        this.invalidateTodosCache();
        const requestToken = Symbol('todo-lists');
        TodosState.listsRequestToken = requestToken;
        TodosState.listsOffset = 0;
        TodosState.listsHasMore = false;
        const markedRequestToken = Symbol('marked-todos');
        TodosState.markedRequestToken = markedRequestToken;
        TodosState.markedOffset = 0;
        TodosState.markedHasMore = false;

        try {
            // Fetch both lists and marked todos in parallel
            const [listsPage, markedPage] = await Promise.all([
                TodosAPI.fetchLists(),
                TodosAPI.fetchMarkedTodos(),
            ]);
            if (TodosState.listsRequestToken !== requestToken) return;
            TodosState.lists = listsPage.items;
            TodosState.listsOffset = listsPage.items.length;
            TodosState.listsHasMore = listsPage.hasMore;
            TodosState.markedTodos = markedPage.items;
            TodosState.markedOffset = markedPage.items.length;
            TodosState.markedHasMore = markedPage.hasMore;
            this.renderSidebarLists();
            this.syncListEditorRoute();
        } catch (error) {
            console.error('Failed to load todo lists:', error);
            sidebarList.innerHTML = `
                <div class="todos-list-empty" style="padding: 20px;">
                    <p class="todos-list-empty-text" data-i18n="todos_error_fetch_lists">${TodosRender.escapeHtml(todosT('todos_error_fetch_lists', 'Failed to load todo lists'))}</p>
                </div>
            `;
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_fetch_lists', 'Failed to load todo lists'), 'error');
            }
        } finally {
            TodosState.isLoadingLists = false;
        }
    },

    appendUniqueTodos(existing, incoming) {
        const seen = new Set(existing.map(item => String(item?.id || '')));
        return existing.concat((incoming || []).filter((item) => {
            const id = String(item?.id || '');
            if (!id || seen.has(id)) return false;
            seen.add(id);
            return true;
        }));
    },

    async loadMoreLists() {
        if (TodosState.listsLoadingMore || !TodosState.listsHasMore) return;
        TodosState.listsLoadingMore = true;
        const requestToken = TodosState.listsRequestToken;
        const offset = TodosState.listsOffset;
        try {
            const page = await TodosAPI.fetchLists(offset);
            if (TodosState.listsRequestToken !== requestToken) return;
            TodosState.lists = this.appendUniqueTodos(TodosState.lists, page.items);
            TodosState.listsOffset = offset + page.items.length;
            TodosState.listsHasMore = page.hasMore;
            this.renderSidebarLists();
        } catch (error) {
            console.error('Failed to load more todo lists:', error);
        } finally {
            TodosState.listsLoadingMore = false;
        }
    },

    renderSidebarLists() {
        const sidebarList = TodosDOM.sidebarList;
        if (!sidebarList) return;

        if (TodosState.lists.length === 0 && TodosState.markedTodos.length === 0) {
            sidebarList.innerHTML = `
                <div class="todos-sidebar-empty">
                    <div class="todos-sidebar-empty-icon">
                        ${Icons.todo}
                    </div>
                    <p class="todos-sidebar-empty-title" data-i18n="todos_no_lists_title">${TodosRender.escapeHtml(todosT('todos_no_lists_title', 'No lists yet'))}</p>
                    <p class="todos-sidebar-empty-text" data-i18n="todos_no_lists_desc">${TodosRender.escapeHtml(todosT('todos_no_lists_desc', 'Create lists to organize your tasks, shopping items, goals, or any checklist you need.'))}</p>
                    <button type="button" class="todos-sidebar-empty-btn project-chat-placeholder-action" id="todosSidebarEmptyAddBtn">
                        ${Icons.plus}
                        <span data-i18n="todos_create_list_submit">${TodosRender.escapeHtml(todosT('todos_create_list_submit', 'Create List'))}</span>
                    </button>
                </div>
            `;
            const emptyAddBtn = document.getElementById('todosSidebarEmptyAddBtn');
            if (emptyAddBtn) {
                emptyAddBtn.addEventListener('click', (event) => this.showCreateListPage(event.currentTarget));
            }
            this.showNoListsState();
            return;
        }

        this.hideNoListsState();

        let sidebarHtml = '';
        
        // Add marked list item if there are marked todos
        if (TodosState.markedTodos.length > 0) {
            const markedCount = `${TodosState.markedTodos.length}${TodosState.markedHasMore ? '+' : ''}`;
            sidebarHtml += TodosRender.markedListItem(markedCount);
            sidebarHtml += '<div class="todos-sidebar-divider"></div>';
        }

        // Add regular lists
        sidebarHtml += TodosState.lists
            .map(list => TodosRender.listItem(list, list.id === TodosState.selectedListId))
            .join('');

        sidebarList.innerHTML = sidebarHtml;
        this.setupTodoListsInfiniteScroll();
    },

    setupTodoListsInfiniteScroll() {
        this._todoListsInfiniteObserver?.disconnect();
        const list = TodosDOM.sidebarList;
        if (!list || !TodosState.listsHasMore || typeof IntersectionObserver !== 'function') return;
        const sentinel = document.createElement('div');
        sentinel.className = 'workspace-infinite-scroll-sentinel';
        sentinel.setAttribute('aria-hidden', 'true');
        list.appendChild(sentinel);
        this._todoListsInfiniteObserver = new IntersectionObserver((entries) => {
            if (entries[0]?.isIntersecting) this.loadMoreLists();
        }, { root: list, rootMargin: '120px', threshold: 0 });
        this._todoListsInfiniteObserver.observe(sentinel);
    },

    showNoListsState() {
        const main = TodosDOM.main;
        const emptyState = TodosDOM.emptyState;
        const listView = TodosDOM.listView;
        
        if (!main) return;
        
        // Hide normal empty state and list view
        if (emptyState) emptyState.style.display = 'none';
        if (listView) listView.style.display = 'none';
        
        // Check if no-lists state already exists
        let noListsState = document.getElementById('todosNoListsState');
        if (!noListsState) {
            noListsState = document.createElement('div');
            noListsState.id = 'todosNoListsState';
            noListsState.className = 'todos-no-lists-state';
            noListsState.innerHTML = `
                <div class="todos-no-lists-illustration">
                    <div class="todos-no-lists-cards">
                        <div class="todos-no-lists-card">
                            <div class="todos-no-lists-card-line"></div>
                            <div class="todos-no-lists-card-line"></div>
                            <div class="todos-no-lists-card-line"></div>
                        </div>
                        <div class="todos-no-lists-card">
                            <div class="todos-no-lists-card-line"></div>
                            <div class="todos-no-lists-card-line"></div>
                            <div class="todos-no-lists-card-line"></div>
                        </div>
                        <div class="todos-no-lists-card">
                            <div class="todos-no-lists-card-line"></div>
                            <div class="todos-no-lists-card-line"></div>
                            <div class="todos-no-lists-card-line"></div>
                        </div>
                    </div>
                    <div class="todos-no-lists-plus">
                        ${Icons.plus}
                    </div>
                </div>
                <h2 class="todos-no-lists-title" data-i18n="todos_first_list_title">${TodosRender.escapeHtml(todosT('todos_first_list_title', 'Create your first list'))}</h2>
                <p class="todos-no-lists-text" data-i18n="todos_first_list_desc">${TodosRender.escapeHtml(todosT('todos_first_list_desc', 'Organize your tasks, ideas, and goals with customizable todo lists. Get started by creating your first one!'))}</p>
                <button type="button" class="todos-sidebar-empty-btn project-chat-placeholder-action" id="todosNoListsCreateBtn">
                    ${Icons.plus}
                    <span data-i18n="todos_create_new_list">${TodosRender.escapeHtml(todosT('todos_create_new_list', 'Create New List'))}</span>
                </button>
            `;
            main.appendChild(noListsState);
            
            // Add event listener
            const createBtn = document.getElementById('todosNoListsCreateBtn');
            if (createBtn) {
                createBtn.addEventListener('click', (event) => this.showCreateListPage(event.currentTarget));
            }
        }
        
        noListsState.style.display = 'flex';
    },

    hideNoListsState() {
        const noListsState = document.getElementById('todosNoListsState');
        if (noListsState) {
            noListsState.style.display = 'none';
        }
    },

    async selectList(listId, options = {}) {
        const { force = false, skipEditorGuard = false } = options;
        const previousListId = TodosState.selectedListId;

        // The sidebar stays available on desktop while the routed editor is
        // open. Treat choosing another list as navigation and protect typed
        // form data with the shared unsaved-changes confirmation.
        if (TodosState.listEditorMode && !skipEditorGuard) {
            this.requestListEditorExit(() => {
                this.closeListEditorPage({ replaceHistory: true, restoreFocus: false });
                this.selectList(listId, { ...options, skipEditorGuard: true });
            });
            return;
        }

        if (TodosState.isSearching) {
            this.clearSearch({ skipExit: true });
        }

        if (!force && TodosState.selectedListId === listId) return;

        // Stop any existing auto-refresh when switching lists
        this.stopAutoRefresh();

        TodosState.selectedListId = listId;
        this.renderSidebarLists();

        // Show list view, hide empty states
        const emptyState = TodosDOM.emptyState;
        const listView = TodosDOM.listView;
        const noListsState = document.getElementById('todosNoListsState');
        const addContainer = TodosDOM.addContainer;
        if (emptyState) emptyState.style.display = 'none';
        if (noListsState) noListsState.style.display = 'none';
        if (listView) listView.style.display = 'flex';
        if (previousListId !== listId) this.resetAddTodoComposer();

        // Handle marked list differently
        if (listId === TodosState.MARKED_LIST_ID) {
            // Hide add container for marked list (can't add directly to marked)
            if (addContainer) addContainer.style.display = 'none';
            
            // Render header
            const listHeader = TodosDOM.listHeader;
            if (listHeader) {
                listHeader.innerHTML = TodosRender.markedListHeader();
            }

            // Load and render marked todos
            await this.loadMarkedTodos();
        } else {
            const list = TodosState.lists.find(l => l.id === listId);
            if (!list) return;
            const canEditList = this.canEditList(list);

            if (addContainer) addContainer.style.display = canEditList ? 'block' : 'none';

            // Read-only subscribers can open a shared list but should not see add controls.
            if (addContainer) addContainer.style.display = canEditTodoList(listId) ? 'block' : 'none';

            // Render header
            const listHeader = TodosDOM.listHeader;
            if (listHeader) {
                listHeader.innerHTML = TodosRender.listHeader(list);
                this.setupSortListeners();
            }

            // Load todos
            await this.loadTodos(listId);
        }

        // Setup mobile back button listener
        this.setupMobileBackButton();

        // Show content view on mobile
        this.showMobileContent();
    },

    setupMobileBackButton() {
        const backBtn = document.getElementById('todosMobileBackBtn');
        if (backBtn) {
            backBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.hideMobileContent();
            });
        }
    },

    showMobileContent() {
        if (window.innerWidth <= 768) {
            const workspace = TodosDOM.workspace;
            if (workspace) {
                workspace.classList.add('show-content');
            }
        }
    },

    getListById(listId) {
        return TodosState.lists.find((list) => list.id === listId) || null;
    },

    canEditList(listOrId, todo = null) {
        if (todo) return canEditTodo(todo);
        const list = typeof listOrId === 'string' ? this.getListById(listOrId) : listOrId;
        if (!list) return false;
        if (list.is_subscribed === true) {
            return list.share_type === 'collaborate';
        }
        return true;
    },

    canDeleteTodo(todo) {
        if (!todo) return false;
        if (typeof todo.can_delete === 'boolean') return todo.can_delete;
        return this.canEditList(todo.todo_list, todo);
    },

    canEditSelectedList() {
        return this.canEditList(TodosState.selectedListId);
    },

    hideMobileContent() {
        const workspace = TodosDOM.workspace;
        if (workspace) {
            workspace.classList.remove('show-content');
        }
        // Clear selection when going back on mobile
        if (window.innerWidth <= 768) {
            TodosState.selectedListId = null;
            this.renderSidebarLists();
        }
    },

    async loadMarkedTodos() {
        const listContainer = TodosDOM.listContainer;
        if (!listContainer) return;

        TodosState.isLoadingMarked = true;
        const requestToken = Symbol('marked-todos');
        TodosState.markedRequestToken = requestToken;
        TodosState.markedOffset = 0;
        TodosState.markedHasMore = false;
        listContainer.innerHTML = TodosRender.todosSkeleton();

        try {
            const page = await TodosAPI.fetchMarkedTodos();
            if (TodosState.markedRequestToken !== requestToken) return;
            TodosState.markedTodos = page.items;
            TodosState.markedOffset = page.items.length;
            TodosState.markedHasMore = page.hasMore;
            this.renderMarkedTodos();
        } catch (error) {
            console.error('Failed to load marked todos:', error);
            listContainer.innerHTML = `
                <div class="todos-list-empty">
                    <p class="todos-list-empty-text" data-i18n="todos_error_fetch_marked">${TodosRender.escapeHtml(todosT('todos_error_fetch_marked', 'Failed to load marked todos'))}</p>
                </div>
            `;
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_fetch_marked', 'Failed to load marked todos'), 'error');
            }
        } finally {
            TodosState.isLoadingMarked = false;
        }
    },

    async loadMoreMarkedTodos() {
        if (TodosState.markedLoadingMore || !TodosState.markedHasMore) return;
        TodosState.markedLoadingMore = true;
        const requestToken = TodosState.markedRequestToken;
        const offset = TodosState.markedOffset;
        try {
            const page = await TodosAPI.fetchMarkedTodos(offset);
            if (TodosState.markedRequestToken !== requestToken) return;
            TodosState.markedTodos = this.appendUniqueTodos(TodosState.markedTodos, page.items);
            TodosState.markedOffset = offset + page.items.length;
            TodosState.markedHasMore = page.hasMore;
            this.renderMarkedTodos();
            this.renderSidebarLists();
        } catch (error) {
            console.error('Failed to load more marked todos:', error);
        } finally {
            TodosState.markedLoadingMore = false;
        }
    },

    renderMarkedTodos() {
        const listContainer = TodosDOM.listContainer;
        if (!listContainer) return;

        if (TodosState.markedTodos.length === 0) {
            listContainer.innerHTML = TodosRender.emptyMarkedState();
            return;
        }

        // Sort: incomplete first, then by updated_at
        const sortedTodos = [...TodosState.markedTodos].sort((a, b) => {
            if (a.is_done !== b.is_done) return a.is_done ? 1 : -1;
            return new Date(b.updated_at) - new Date(a.updated_at);
        });

        listContainer.innerHTML = sortedTodos
            .map(todo => TodosRender.todoItem(todo, true, { readOnly: !this.canEditList(todo.todo_list, todo) }))
            .join('');
        this.setupTodoContentInfiniteScroll('marked');
    },

    async loadTodos(listId, { append = false } = {}) {
        const listContainer = TodosDOM.listContainer;
        if (!listContainer) return;

        if (append && (TodosState.todosLoadingMore || !TodosState.todosHasMore)) return;
        const requestToken = append ? TodosState.todosRequestToken : Symbol('list-todos');
        const offset = append ? TodosState.todosOffset : 0;
        if (!append) {
            TodosState.todosRequestToken = requestToken;
            TodosState.isLoadingTodos = true;
            TodosState.todosOffset = 0;
            TodosState.todosHasMore = false;
            listContainer.innerHTML = TodosRender.todosSkeleton();
        } else {
            TodosState.todosLoadingMore = true;
        }

        try {
            const filters = { view: TodosState.activeView, sort: TodosState.sortBy };
            const page = await TodosAPI.fetchTodos(listId, filters, offset);
            if (TodosState.todosRequestToken !== requestToken || TodosState.selectedListId !== listId) return;
            TodosState.todos = append
                ? this.appendUniqueTodos(TodosState.todos, page.items)
                : page.items;
            TodosState.todosOffset = offset + page.items.length;
            TodosState.todosHasMore = page.hasMore;
            this.renderTodos();
            
            // Start auto-refresh for subscribed (shared) lists
            if (!append) this.startAutoRefresh(listId);
        } catch (error) {
            console.error('Failed to load todos:', error);
            if (!append) listContainer.innerHTML = `
                <div class="todos-list-empty">
                    <p class="todos-list-empty-text" data-i18n="todos_error_load_todos">${TodosRender.escapeHtml(todosT('todos_error_load_todos', 'Failed to load todos'))}</p>
                </div>
            `;
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_load_todos', 'Failed to load todos'), 'error');
            }
        } finally {
            if (!append) TodosState.isLoadingTodos = false;
            TodosState.todosLoadingMore = false;
        }
    },

    async loadMoreTodos() {
        if (!TodosState.selectedListId || TodosState.selectedListId === TodosState.MARKED_LIST_ID) return;
        await this.loadTodos(TodosState.selectedListId, { append: true });
    },

    renderTodos() {
        const listContainer = TodosDOM.listContainer;
        if (!listContainer) return;

        if (TodosState.todos.length === 0) {
            listContainer.innerHTML = TodosRender.emptyListState(TodosState.activeView !== 'all');
            return;
        }

        // Sort todos based on selected sort option
        const sortedTodos = this.sortTodos([...TodosState.todos]);
        const readOnly = !this.canEditSelectedList();

        if (TodosState.viewMode === 'board') {
            listContainer.innerHTML = this.renderKanbanBoard(sortedTodos, readOnly);
        } else {
            listContainer.innerHTML = sortedTodos
                .map(todo => TodosRender.todoItem(todo, false, { readOnly }))
                .join('');
        }
        this.setupTodoContentInfiniteScroll('todos');
    },

    setupTodoContentInfiniteScroll(mode) {
        this._todoContentInfiniteObserver?.disconnect();
        const container = TodosDOM.listContainer;
        const hasMore = mode === 'search'
            ? TodosState.searchHasMore
            : (mode === 'marked' ? TodosState.markedHasMore : TodosState.todosHasMore);
        if (!container || !hasMore || typeof IntersectionObserver !== 'function') return;
        const sentinel = document.createElement('div');
        sentinel.className = 'workspace-infinite-scroll-sentinel todos-content-sentinel';
        sentinel.setAttribute('aria-hidden', 'true');
        container.appendChild(sentinel);
        this._todoContentInfiniteObserver = new IntersectionObserver((entries) => {
            if (!entries[0]?.isIntersecting) return;
            if (mode === 'search') this.loadMoreSearchResults();
            else if (mode === 'marked') this.loadMoreMarkedTodos();
            else this.loadMoreTodos();
        }, { root: container, rootMargin: '160px', threshold: 0 });
        this._todoContentInfiniteObserver.observe(sentinel);
    },

    async reloadSelectedTodos() {
        if (!TodosState.selectedListId || TodosState.selectedListId === TodosState.MARKED_LIST_ID) return;
        this.refreshListHeader();
        await this.loadTodos(TodosState.selectedListId);
    },

    renderKanbanBoard(todos, readOnly) {
        return `
            <div class="todos-kanban-board">
                ${TODOS_STATUS_OPTIONS.map((status) => {
                    const columnTodos = todos.filter((todo) => (todo.status || (todo.is_done ? 'done' : 'todo')) === status.id);
                    return `
                        <section class="todos-kanban-column" data-status="${status.id}">
                            <div class="todos-kanban-column-header">
                                <span>${TodosRender.escapeHtml(todosT(status.nameKey, status.name))}</span>
                                <span>${columnTodos.length}</span>
                            </div>
                            <div class="todos-kanban-column-body">
                                ${columnTodos.map(todo => TodosRender.todoItem(todo, false, { readOnly })).join('') || `<div class="todos-kanban-empty">${TodosRender.escapeHtml(todosT('todos_board_empty', 'Drop tasks here'))}</div>`}
                            </div>
                        </section>
                    `;
                }).join('')}
            </div>
        `;
    },

    sortTodos(todos) {
        // Always put completed items at the bottom
        const incomplete = todos.filter(t => !t.is_done);
        const complete = todos.filter(t => t.is_done);

        const sortFn = (a, b) => {
            switch (TodosState.sortBy) {
                case 'date-asc':
                    return new Date(a.created_at) - new Date(b.created_at);
                case 'date-desc':
                    return new Date(b.created_at) - new Date(a.created_at);
                case 'alpha-asc':
                    return a.content.localeCompare(b.content);
                case 'alpha-desc':
                    return b.content.localeCompare(a.content);
                case 'priority':
                    return b.priority - a.priority;
                case 'due-date':
                    if (!a.due_at && !b.due_at) return a.order - b.order;
                    if (!a.due_at) return 1;
                    if (!b.due_at) return -1;
                    return new Date(a.due_at) - new Date(b.due_at);
                case 'manual':
                default:
                    return a.order - b.order;
            }
        };

        incomplete.sort(sortFn);
        complete.sort(sortFn);

        return [...incomplete, ...complete];
    },

    showAddTodoExpanded() {
        if (!this.canEditSelectedList()) return;
        TodosState.addTodoExpanded = true;
        TodosDOM.addForm?.classList.add('expanded');
        TodosDOM.addNotes?.classList.add('visible');
    },

    /** Collapse the composer while deliberately retaining every draft value. */
    collapseAddTodoComposer({ blur = true } = {}) {
        TodosState.addTodoExpanded = false;
        this.closeAddTodoPopover();
        TodosDOM.addForm?.classList.remove('expanded');
        TodosDOM.addNotes?.classList.remove('visible');
        if (blur && TodosDOM.addForm?.contains(document.activeElement)) {
            document.activeElement?.blur?.();
        }
    },

    /** Clear a completed or abandoned draft and return to compact mode. */
    resetAddTodoComposer({ blur = true } = {}) {
        if (TodosDOM.addInput) TodosDOM.addInput.value = '';
        if (TodosDOM.addNotes) TodosDOM.addNotes.value = '';
        if (TodosDOM.addDueAt) TodosDOM.addDueAt.value = '';
        if (TodosDOM.addDueTime) TodosDOM.addDueTime.value = '';
        if (TodosDOM.addAllDay) TodosDOM.addAllDay.checked = false;
        syncTodoDueInputMode(TodosDOM.addDueTime, false);
        if (TodosDOM.addPriority) TodosDOM.addPriority.value = '0';
        if (TodosDOM.addTags) TodosDOM.addTags.value = '';
        this.updateAddTodoToolStates();
        this.collapseAddTodoComposer({ blur });
    },

    /** Submit on Enter and collapse, without clearing, on Escape. */
    handleAddTodoComposerKeydown(event) {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            this.handleAddTodo({ blur: false });
            return;
        }
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            if (TodosState.addTodoOpenPopover) {
                this.closeAddTodoPopover({ restoreFocus: true });
            } else {
                this.collapseAddTodoComposer();
            }
        }
    },

    /** Wire the three icon-only metadata popovers and their keyboard behavior. */
    setupAddTodoTools() {
        const icons = {
            todosAddScheduleIcon: Icons.calendar,
            todosAddPriorityIcon: Icons.flag,
            todosAddTagsIcon: Icons.tag,
        };
        Object.entries(icons).forEach(([id, icon]) => {
            const container = document.getElementById(id);
            if (container) container.innerHTML = icon || '';
        });
        document.querySelectorAll('.todos-add-option-check').forEach((check) => {
            check.innerHTML = Icons.check || '';
        });

        ['schedule', 'priority', 'tags'].forEach((name) => {
            const capitalized = name.charAt(0).toUpperCase() + name.slice(1);
            const button = document.getElementById(`todosAdd${capitalized}Btn`);
            button?.addEventListener('click', (event) => {
                event.stopPropagation();
                this.showAddTodoExpanded();
                this.toggleAddTodoPopover(name);
            });
        });

        TodosDOM.addDueAt?.addEventListener('change', () => this.updateAddTodoToolStates());
        TodosDOM.addDueTime?.addEventListener('change', () => this.updateAddTodoToolStates());
        TodosDOM.addAllDay?.addEventListener('change', () => {
            syncTodoDueInputMode(TodosDOM.addDueTime, TodosDOM.addAllDay.checked);
            this.updateAddTodoToolStates();
        });
        TodosDOM.addTags?.addEventListener('input', () => this.updateAddTodoToolStates());
        TodosDOM.addTags?.addEventListener('keydown', (event) => this.handleAddTodoComposerKeydown(event));

        document.querySelectorAll('.todos-add-priority-option').forEach((option) => {
            option.addEventListener('click', () => {
                if (TodosDOM.addPriority) TodosDOM.addPriority.value = option.dataset.priority || '0';
                this.updateAddTodoToolStates();
                this.closeAddTodoPopover({ restoreFocus: true });
            });
        });

        document.getElementById('todosAddPriorityPopover')?.addEventListener('keydown', (event) => {
            const options = Array.from(document.querySelectorAll('.todos-add-priority-option'));
            const currentIndex = options.indexOf(document.activeElement);
            let nextIndex = null;
            if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1 + options.length) % options.length;
            if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + options.length) % options.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = options.length - 1;
            if (nextIndex !== null) {
                event.preventDefault();
                options[nextIndex]?.focus();
            }
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                this.closeAddTodoPopover({ restoreFocus: true });
            }
        });

        document.getElementById('todosAddSchedulePopover')?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                this.closeAddTodoPopover({ restoreFocus: true });
            }
        });
        document.getElementById('todosAddTagsPopover')?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                this.closeAddTodoPopover({ restoreFocus: true });
            }
        });
        this.updateAddTodoToolStates();
    },

    getAddTodoPopoverElements(name) {
        const capitalized = name ? name.charAt(0).toUpperCase() + name.slice(1) : '';
        return {
            button: document.getElementById(`todosAdd${capitalized}Btn`),
            popover: document.getElementById(`todosAdd${capitalized}Popover`),
        };
    },

    toggleAddTodoPopover(name) {
        if (TodosState.addTodoOpenPopover === name) {
            this.closeAddTodoPopover({ restoreFocus: true });
            return;
        }
        this.closeAddTodoPopover();
        const { button, popover } = this.getAddTodoPopoverElements(name);
        if (!button || !popover) return;
        TodosState.addTodoOpenPopover = name;
        popover.hidden = false;
        button.setAttribute('aria-expanded', 'true');
        requestAnimationFrame(() => {
            if (name === 'priority') {
                popover.querySelector('[aria-checked="true"]')?.focus();
            } else {
                popover.querySelector('input:not([type="checkbox"])')?.focus();
            }
        });
    },

    closeAddTodoPopover({ restoreFocus = false } = {}) {
        const name = TodosState.addTodoOpenPopover;
        if (!name) return;
        const { button, popover } = this.getAddTodoPopoverElements(name);
        if (popover) popover.hidden = true;
        button?.setAttribute('aria-expanded', 'false');
        TodosState.addTodoOpenPopover = null;
        if (restoreFocus) requestAnimationFrame(() => button?.focus());
    },

    /** Reflect saved metadata through color and precise accessible labels. */
    updateAddTodoToolStates() {
        const scheduleButton = document.getElementById('todosAddScheduleBtn');
        const priorityButton = document.getElementById('todosAddPriorityBtn');
        const tagsButton = document.getElementById('todosAddTagsBtn');
        const dueValue = TodosDOM.addDueAt?.value || '';
        const dueTimeValue = TodosDOM.addDueTime?.value || '';
        const isAllDay = Boolean(TodosDOM.addAllDay?.checked);
        const priorityValue = TodosDOM.addPriority?.value || '0';
        const tagsValue = TodosDOM.addTags?.value.trim() || '';

        scheduleButton?.classList.toggle('active', Boolean(dueValue || isAllDay));
        priorityButton?.classList.toggle('active', priorityValue !== '0');
        tagsButton?.classList.toggle('active', Boolean(tagsValue));

        const dueLabel = todosT('todos_due_label', 'Due date');
        const allDayLabel = todosT('todos_all_day_label', 'All day');
        const priorityLabel = todosT('todos_priority_label', 'Priority');
        const tagsLabel = todosT('todos_tags_label', 'Tags');
        const selectedPriority = TodosDOM.addPriority?.selectedOptions?.[0]?.textContent?.trim() || '';
        if (scheduleButton) {
            const details = [dueValue, !isAllDay ? dueTimeValue : '', isAllDay ? allDayLabel : ''].filter(Boolean).join(', ');
            scheduleButton.setAttribute('aria-label', details ? `${dueLabel}: ${details}` : dueLabel);
        }
        if (priorityButton) {
            priorityButton.setAttribute('aria-label', selectedPriority ? `${priorityLabel}: ${selectedPriority}` : priorityLabel);
        }
        if (tagsButton) {
            tagsButton.setAttribute('aria-label', tagsValue ? `${tagsLabel}: ${tagsValue}` : tagsLabel);
        }

        document.querySelectorAll('.todos-add-priority-option').forEach((option) => {
            const selected = option.dataset.priority === priorityValue;
            option.classList.toggle('selected', selected);
            option.setAttribute('aria-checked', selected ? 'true' : 'false');
        });
    },

    async handleAddTodo({ blur = true } = {}) {
        const addInput = TodosDOM.addInput;
        const addNotes = TodosDOM.addNotes;
        const addDueAt = TodosDOM.addDueAt;
        const addDueTime = TodosDOM.addDueTime;
        const addAllDay = TodosDOM.addAllDay;
        const addPriority = TodosDOM.addPriority;
        const addTags = TodosDOM.addTags;
        if (!addInput || TodosState.isAddingTodo) return;
        if (!this.canEditSelectedList()) return;

        const content = addInput.value.trim();
        if (!content) return;

        const notes = addNotes ? addNotes.value.trim() : null;
        const isAllDay = Boolean(addAllDay?.checked);
        if (!validateTodoDueControls(addDueAt, addDueTime, isAllDay)) return;
        const dueAt = todoDueApiValue(addDueAt?.value || '', addDueTime?.value || '', isAllDay);
        const tags = addTags?.value ? addTags.value.split(',').map(tag => tag.trim()).filter(Boolean) : [];
        const priority = Number(addPriority?.value || 0);
        const extra = {
            priority: Number.isFinite(priority) ? priority : 0,
            all_day: Boolean(addAllDay?.checked),
            tags,
        };
        if (dueAt) extra.due_at = dueAt;
        const listId = TodosState.selectedListId;
        if (!listId) return;
        if (!canEditTodoList(listId)) return;

        TodosState.isAddingTodo = true;
        TodosDOM.addForm?.classList.add('submitting');
        TodosDOM.addForm?.setAttribute('aria-busy', 'true');

        try {
            const newTodo = await TodosAPI.createTodo(listId, content, notes, extra.priority, extra);
            TodosState.todos.push(newTodo);
            this.invalidateTodosCache();
            this.renderTodos();
            this.resetAddTodoComposer({ blur });

            // Scroll to bottom
            const listContainer = TodosDOM.listContainer;
            if (listContainer) {
                listContainer.scrollTop = listContainer.scrollHeight;
            }
        } catch (error) {
            console.error('Failed to create todo:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_create_todo', 'Failed to create todo'), 'error');
            }
        } finally {
            TodosState.isAddingTodo = false;
            TodosDOM.addForm?.classList.remove('submitting');
            TodosDOM.addForm?.removeAttribute('aria-busy');
        }
    },

    async handleToggleTodo(todoId, isChecked) {
        // Cross-list search rows carry their own permission flags and are just
        // as actionable as rows loaded from a selected list or Marked.
        const todo = this.findTodoById(todoId);
        if (!todo) return;
        if (!this.canEditList(todo.todo_list, todo)) {
            const todoElement = document.querySelector(`.todo-item[data-todo-id="${todoId}"]`);
            const checkbox = todoElement?.querySelector('input[type="checkbox"]');
            if (checkbox) checkbox.checked = todo.is_done;
            return;
        }

        // Optimistic UI update
        const previousState = todo.is_done;
        todo.is_done = isChecked;
        todo.completed_at = isChecked ? new Date().toISOString() : null;

        // Update UI immediately
        const todoElement = document.querySelector(`.todo-item[data-todo-id="${todoId}"]`);
        if (todoElement) {
            todoElement.classList.toggle('completed', isChecked);
        }

        try {
            const updatedTodo = await TodosAPI.toggleTodo(todoId);
            // Update with server response
            Object.assign(todo, updatedTodo);
            this.invalidateTodosCache();
            // Completion changes the server-side page order. Reload the first
            // batch so the next offset cannot skip an item that crossed the
            // incomplete/completed boundary.
            await this.reloadVisibleTodoCollection();
        } catch (error) {
            console.error('Failed to toggle todo:', error);
            // Revert on error
            todo.is_done = previousState;
            todo.completed_at = previousState ? todo.completed_at : null;
            
            if (todoElement) {
                todoElement.classList.toggle('completed', previousState);
                const checkbox = todoElement.querySelector('input[type="checkbox"]');
                if (checkbox) checkbox.checked = previousState;
            }

            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_update_todo', 'Failed to update todo'), 'error');
            }
        }
    },

    async deleteTodo(todoId) {
        const todo = this.findTodoById(todoId);
        if (!this.canDeleteTodo(todo) || typeof window.showDeleteConfirm !== 'function') return;

        const confirmed = await window.showDeleteConfirm({
            title: todosT('common_delete_confirm_title', 'Delete item?'),
            message: todosT('common_delete_confirm_desc', 'This action cannot be undone.'),
            confirmLabel: todosT('common_delete_confirm_button', 'Delete'),
        });
        if (!confirmed) return;

        try {
            await TodosAPI.deleteTodo(todoId);
            const keepOtherTodos = (item) => item.id !== todoId;
            TodosState.todos = TodosState.todos.filter(keepOtherTodos);
            TodosState.markedTodos = TodosState.markedTodos.filter(keepOtherTodos);
            TodosState.searchResults = TodosState.searchResults.filter(keepOtherTodos);
            TodosState.allTodosCache = TodosState.allTodosCache.filter(keepOtherTodos);
            this.invalidateTodosCache();
            await this.reloadVisibleTodoCollection();
            this.renderSidebarLists();
        } catch (error) {
            console.error('Failed to delete todo:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_delete_todo', 'Failed to delete todo'), 'error');
            }
        }
    },

    async handleToggleMark(todoId) {
        const todo = this.findTodoById(todoId);
        const isMarkedView = TodosState.selectedListId === TodosState.MARKED_LIST_ID;
        if (!todo) return;
        if (!this.canEditList(todo.todo_list, todo)) return;

        // Optimistic UI update
        const previousMarkedState = todo.is_marked;
        todo.is_marked = !todo.is_marked;

        // Update UI immediately
        const todoElement = document.querySelector(`.todo-item[data-todo-id="${todoId}"]`);
        const markBtn = todoElement?.querySelector('.todo-mark-btn');
        if (todoElement) {
            todoElement.classList.toggle('marked', todo.is_marked);
        }
        if (markBtn) {
            markBtn.classList.toggle('active', todo.is_marked);
            const svg = markBtn.querySelector('svg');
            if (svg) {
                svg.setAttribute('fill', todo.is_marked ? 'currentColor' : 'none');
            }
        }

        try {
            const updatedTodo = await TodosAPI.toggleMark(todoId);
            // Update with server response
            Object.assign(todo, updatedTodo);
            this.invalidateTodosCache();
            
            // Refresh marked todos list
            await this.refreshMarkedTodos();
            
            // If we're in marked view and unmarked, re-render
            if (isMarkedView) {
                this.renderMarkedTodos();
                // Update sidebar
                this.renderSidebarLists();
            } else if (TodosState.isSearching) {
                this.renderSearchResults();
            }
        } catch (error) {
            console.error('Failed to toggle mark:', error);
            // Revert on error
            todo.is_marked = previousMarkedState;
            
            if (todoElement) {
                todoElement.classList.toggle('marked', previousMarkedState);
            }
            if (markBtn) {
                markBtn.classList.toggle('active', previousMarkedState);
                const svg = markBtn.querySelector('svg');
                if (svg) {
                    svg.setAttribute('fill', previousMarkedState ? 'currentColor' : 'none');
                }
            }

            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_toggle_mark', 'Failed to update mark'), 'error');
            }
        }
    },

    async refreshMarkedTodos() {
        try {
            const page = await TodosAPI.fetchMarkedTodos();
            TodosState.markedTodos = page.items;
            TodosState.markedOffset = page.items.length;
            TodosState.markedHasMore = page.hasMore;
            TodosState.markedRequestToken = Symbol('marked-todos');
            this.renderSidebarLists();
        } catch (error) {
            console.error('Failed to refresh marked todos:', error);
        }
    },

    /** Reload whichever server-backed task collection is currently visible. */
    async reloadVisibleTodoCollection() {
        const mergeFirstPage = (existing, page) => {
            const freshIds = new Set(page.items.map((todo) => String(todo.id)));
            return [...page.items, ...existing.filter((todo) => !freshIds.has(String(todo.id)))];
        };
        if (TodosState.isSearching && TodosState.searchQuery) {
            const query = TodosState.searchQuery;
            const requestToken = TodosState.searchRequestToken;
            const page = await TodosAPI.searchTodos({ q: query });
            if (TodosState.searchRequestToken !== requestToken || TodosState.searchQuery !== query) return;
            TodosState.searchResults = mergeFirstPage(TodosState.searchResults, page);
            TodosState.searchOffset = TodosState.searchResults.length;
            TodosState.searchHasMore = page.hasMore;
            this.renderSearchResults();
            return;
        }
        if (TodosState.selectedListId === TodosState.MARKED_LIST_ID) {
            const requestToken = TodosState.markedRequestToken;
            const page = await TodosAPI.fetchMarkedTodos();
            if (TodosState.markedRequestToken !== requestToken) return;
            TodosState.markedTodos = mergeFirstPage(TodosState.markedTodos, page);
            TodosState.markedOffset = TodosState.markedTodos.length;
            TodosState.markedHasMore = page.hasMore;
            this.renderMarkedTodos();
            this.renderSidebarLists();
            return;
        }
        if (TodosState.selectedListId) {
            const listId = TodosState.selectedListId;
            const requestToken = TodosState.todosRequestToken;
            const page = await TodosAPI.fetchTodos(
                listId,
                { view: TodosState.activeView, sort: TodosState.sortBy },
            );
            if (TodosState.todosRequestToken !== requestToken || TodosState.selectedListId !== listId) return;
            TodosState.todos = mergeFirstPage(TodosState.todos, page);
            TodosState.todosOffset = TodosState.todos.length;
            TodosState.todosHasMore = page.hasMore;
            this.renderTodos();
        }
    },

    async startInlineEdit(todoId) {
        const todo = this.findTodoById(todoId);
        if (!todo || !this.canEditList(todo.todo_list, todo)) return;
        const item = document.querySelector(`.todo-item[data-todo-id="${CSS.escape(todoId)}"]`);
        const content = item?.querySelector('.todo-item-content');
        if (!content || content.querySelector('input')) return;
        const input = document.createElement('input');
        input.className = 'todo-inline-edit-input';
        input.value = todo.content || '';
        content.replaceChildren(input);
        input.focus();
        input.select();
        const save = async () => {
            const nextContent = input.value.trim();
            if (!nextContent || nextContent === todo.content) {
                if (TodosState.isSearching) this.renderSearchResults();
                else if (TodosState.selectedListId === TodosState.MARKED_LIST_ID) this.renderMarkedTodos();
                else this.renderTodos();
                return;
            }
            try {
                const updated = await TodosAPI.updateTodo(todoId, { content: nextContent });
                Object.assign(todo, updated);
                this.invalidateTodosCache();
                await this.reloadVisibleTodoCollection();
            } catch (error) {
                console.error('Failed inline edit:', error);
                if (TodosState.isSearching) this.renderSearchResults();
                else if (TodosState.selectedListId === TodosState.MARKED_LIST_ID) this.renderMarkedTodos();
                else this.renderTodos();
            }
        };
        input.addEventListener('blur', save, { once: true });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') input.blur();
            if (e.key === 'Escape') this.renderTodos();
        });
    },

    showTodoDetails(todoId) {
        const todo = this.findTodoById(todoId);
        if (!todo || !this.canEditList(todo.todo_list, todo)) return;
        const returnFocus = document.activeElement;
        let overlay = document.getElementById('todosTaskDetailsOverlay');
        if (overlay) overlay.remove();
        const tagsValue = Array.isArray(todo.tags) ? todo.tags.join(', ') : '';
        const subtasksValue = Array.isArray(todo.subtasks) ? todo.subtasks.map(item => `${item?.is_done ? '[x]' : '[ ]'} ${item?.title || item?.content || ''}`).join('\n') : '';
        const linksValue = Array.isArray(todo.links) ? todo.links.map(item => item?.url || item?.href || item?.title || '').filter(Boolean).join('\n') : '';
        const attachmentsValue = Array.isArray(todo.attachments) ? todo.attachments.map(item => item?.name || item?.url || item?.title || '').filter(Boolean).join('\n') : '';
        const dueValues = todoDueControlValues(todo.due_at, todo.all_day);
        overlay = window.DeleteWarningModal?.create({
            id: 'todosTaskDetailsOverlay',
            cardClass: 'workspace-crud-card todos-task-details-modal',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'todosTaskDetailsTitle',
            contentHtml: `
                <header class="todos-task-details-header shared-modal-header shared-modal-header--main">
                    <h3 class="shared-modal-title" id="todosTaskDetailsTitle">${TodosRender.escapeHtml(todosT('todos_task_details_title', 'Task details'))}</h3>
                    <button type="button" class="todos-task-details-close shared-modal-close" data-action="close" aria-label="${TodosRender.escapeHtml(todosT('common_close', 'Close'))}">${Icons.close}</button>
                </header>
                <div class="todos-task-details-body shared-modal-body" data-modal-scroll-region>
                    <div class="todos-task-details-field todos-task-details-field-wide">
                        <label class="form-label" for="todosTaskContent">${TodosRender.escapeHtml(todosT('todos_content_label', 'Task'))}</label>
                        <input class="form-input" id="todosTaskContent" type="text" value="${TodosRender.escapeHtml(todo.content)}" autocomplete="off">
                    </div>
                    <div class="todos-task-details-field todos-task-details-field-wide">
                        <label class="form-label" for="todosTaskNotes">${TodosRender.escapeHtml(todosT('todos_add_notes_placeholder', 'Add notes (optional)'))}</label>
                        <textarea class="form-input todos-task-details-notes" id="todosTaskNotes" rows="3">${TodosRender.escapeHtml(todo.notes || '')}</textarea>
                    </div>
                    <div class="todos-task-details-meta">
                        <div class="todos-task-details-field todos-task-details-schedule">
                            <label class="form-label" for="todosTaskDueAt">${TodosRender.escapeHtml(todosT('todos_due_label', 'Due'))}</label>
                            <div class="todos-task-details-schedule-controls">
                                <div class="todos-task-details-due-inputs">
                                    <input class="form-input" id="todosTaskDueAt" type="date" value="${dueValues.date}">
                                    <input class="form-input" id="todosTaskDueTime" type="time" value="${dueValues.time}" aria-label="${TodosRender.escapeHtml(todosT('todos_due_time_label', 'Due time'))}" ${todo.all_day ? 'hidden disabled' : ''}>
                                </div>
                                <label class="todos-task-details-check">
                                    <input class="form-checkbox" id="todosTaskAllDay" type="checkbox" ${todo.all_day ? 'checked' : ''}>
                                    <span>${TodosRender.escapeHtml(todosT('todos_all_day_label', 'All day'))}</span>
                                </label>
                            </div>
                        </div>
                        <div class="todos-task-details-field">
                            <span class="form-label" id="todosTaskPriorityLabel">${TodosRender.escapeHtml(todosT('todos_priority_label', 'Priority'))}</span>
                            <select id="todosTaskPriority" aria-labelledby="todosTaskPriorityLabel"><option value="0">${TodosRender.escapeHtml(todosT('todos_priority_none', 'No priority'))}</option><option value="1">${TodosRender.escapeHtml(todosT('todos_priority_medium', 'Medium priority'))}</option><option value="2">${TodosRender.escapeHtml(todosT('todos_priority_high', 'High priority'))}</option></select>
                        </div>
                        <div class="todos-task-details-field">
                            <span class="form-label" id="todosTaskStatusLabel">${TodosRender.escapeHtml(todosT('todos_status_label', 'Status'))}</span>
                            <select id="todosTaskStatus" aria-labelledby="todosTaskStatusLabel">${TODOS_STATUS_OPTIONS.map(status => `<option value="${status.id}">${TodosRender.escapeHtml(todosT(status.nameKey, status.name))}</option>`).join('')}</select>
                        </div>
                    </div>
                    <div class="todos-task-details-field todos-task-details-field-wide">
                        <label class="form-label" for="todosTaskTags">${TodosRender.escapeHtml(todosT('todos_tags_label', 'Tags'))}</label>
                        <input class="form-input" id="todosTaskTags" type="text" value="${TodosRender.escapeHtml(tagsValue)}" autocomplete="off">
                    </div>
                    <div class="todos-task-details-extras">
                        <div class="todos-task-details-field">
                            <label class="form-label" for="todosTaskSubtasks">${TodosRender.escapeHtml(todosT('todos_subtasks_label', 'Subtasks'))}</label>
                            <textarea class="form-input" id="todosTaskSubtasks" rows="4">${TodosRender.escapeHtml(subtasksValue)}</textarea>
                        </div>
                        <div class="todos-task-details-field">
                            <label class="form-label" for="todosTaskLinks">${TodosRender.escapeHtml(todosT('todos_links_label', 'Links'))}</label>
                            <textarea class="form-input" id="todosTaskLinks" rows="4">${TodosRender.escapeHtml(linksValue)}</textarea>
                        </div>
                        <div class="todos-task-details-field">
                            <label class="form-label" for="todosTaskAttachments">${TodosRender.escapeHtml(todosT('todos_attachments_label', 'Attachments'))}</label>
                            <textarea class="form-input" id="todosTaskAttachments" rows="4">${TodosRender.escapeHtml(attachmentsValue)}</textarea>
                        </div>
                    </div>
                </div>
            `,
            actions: [
                { role: 'cancel', variant: 'cancel', text: TodosRender.escapeHtml(todosT('common_cancel', 'Cancel')), attrs: { 'data-action': 'close' } },
                { variant: 'submit', text: TodosRender.escapeHtml(todosT('common_save', 'Save')), attrs: { 'data-action': 'save' } },
            ],
        });
        if (!overlay) return;
        document.body.appendChild(overlay);
        const prioritySelect = overlay.querySelector('#todosTaskPriority');
        const statusSelect = overlay.querySelector('#todosTaskStatus');
        prioritySelect.value = String(todo.priority || 0);
        statusSelect.value = todo.status || (todo.is_done ? 'done' : 'todo');
        this.upgradePrioritySelect(prioritySelect, 'todos-task-priority-select');
        this.upgradeTodoSelect(
            statusSelect,
            'todos-task-status-select',
            todosT('todos_status_label', 'Status'),
        );
        overlay.removeAttribute('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        overlay.querySelector('#todosTaskContent')?.focus();
        overlay.querySelector('#todosTaskAllDay')?.addEventListener('change', (event) => {
            syncTodoDueInputMode(overlay.querySelector('#todosTaskDueTime'), event.currentTarget.checked);
        });

        /** Hide the dialog and return keyboard users to the invoking control. */
        const closeModal = () => {
            overlay.setAttribute('hidden', '');
            overlay.setAttribute('aria-hidden', 'true');
            requestAnimationFrame(() => {
                if (returnFocus?.isConnected) returnFocus.focus?.();
            });
        };
        overlay._closeTodoDetails = closeModal;

        overlay.onclick = (e) => {
            if (e.target === overlay || e.target.closest('[data-action="close"]')) {
                closeModal();
            }
            if (e.target.closest('[data-action="save"]')) this.saveTodoDetails(todoId, overlay);
        };

        // Keep keyboard focus inside the aria-modal dialog. Shared select menus
        // stop propagation while handling their own Escape behavior, so Escape
        // closes the dialog only after any open select has had first priority.
        overlay.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                closeModal();
                return;
            }
            if (event.key !== 'Tab') return;
            const focusable = Array.from(overlay.querySelectorAll(
                'button:not([disabled]), input:not([disabled]):not([tabindex="-1"]), textarea:not([disabled]), select:not([disabled]):not([tabindex="-1"]), [tabindex]:not([tabindex="-1"])',
            )).filter(element => !element.hidden && element.getClientRects().length > 0);
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
        });
    },

    async saveTodoDetails(todoId, overlay) {
        const todo = this.findTodoById(todoId);
        if (!todo || !this.canEditList(todo.todo_list, todo)) return;
        const saveButton = overlay.querySelector('[data-action="save"]');
        const modal = overlay.querySelector('.todos-task-details-modal');
        if (saveButton?.disabled) return;
        const readLines = (selector) => (overlay.querySelector(selector)?.value || '').split('\n').map(line => line.trim()).filter(Boolean);
        const subtasks = readLines('#todosTaskSubtasks').map((line) => ({
            title: line.replace(/^\[[ xX]\]\s*/, ''),
            is_done: /^\[[xX]\]/.test(line),
        }));
        const links = readLines('#todosTaskLinks').map((url) => ({ url }));
        const attachments = readLines('#todosTaskAttachments').map((name) => ({ name }));
        const dueInput = overlay.querySelector('#todosTaskDueAt')?.value || '';
        const dueTimeInput = overlay.querySelector('#todosTaskDueTime')?.value || '';
        const allDayInput = Boolean(overlay.querySelector('#todosTaskAllDay')?.checked);
        if (!validateTodoDueControls(
            overlay.querySelector('#todosTaskDueAt'),
            overlay.querySelector('#todosTaskDueTime'),
            allDayInput,
        )) return;
        const payload = {
            content: overlay.querySelector('#todosTaskContent')?.value.trim(),
            notes: overlay.querySelector('#todosTaskNotes')?.value || '',
            priority: Number(overlay.querySelector('#todosTaskPriority')?.value || 0),
            status: overlay.querySelector('#todosTaskStatus')?.value || 'todo',
            all_day: allDayInput,
            tags: (overlay.querySelector('#todosTaskTags')?.value || '').split(',').map(tag => tag.trim()).filter(Boolean),
            subtasks,
            links,
            attachments,
            clear_due_at: !dueInput,
        };
        if (dueInput) payload.due_at = todoDueApiValue(dueInput, dueTimeInput, payload.all_day);
        if (saveButton) saveButton.disabled = true;
        modal?.setAttribute('aria-busy', 'true');
        try {
            const updated = await TodosAPI.updateTodo(todoId, payload);
            Object.assign(todo, updated);
            overlay._closeTodoDetails?.();
            this.invalidateTodosCache();
            await this.reloadVisibleTodoCollection();
            await this.refreshMarkedTodos();
        } catch (error) {
            console.error('Failed to save todo details:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_update_todo', 'Failed to update todo'), 'error');
            }
        } finally {
            if (!overlay.hidden && saveButton) saveButton.disabled = false;
            modal?.removeAttribute('aria-busy');
        }
    },

    handleTodoDragStart(e) {
        const item = e.target.closest('.todo-item');
        if (!item || !this.canEditSelectedList() || TodosState.todosHasMore) return;
        e.dataTransfer?.setData('text/plain', item.dataset.todoId || '');
        item.classList.add('dragging');
    },

    handleTodoDragOver(e) {
        if (!this.canEditSelectedList()) return;
        const item = e.target.closest('.todo-item');
        const column = e.target.closest('.todos-kanban-column');
        if (item || column) e.preventDefault();
    },

    async handleTodoDrop(e) {
        if (!this.canEditSelectedList()) return;
        e.preventDefault();
        const todoId = e.dataTransfer?.getData('text/plain');
        document.querySelectorAll('.todo-item.dragging').forEach(item => item.classList.remove('dragging'));
        if (!todoId) return;
        const statusColumn = e.target.closest('.todos-kanban-column');
        if (statusColumn && TodosState.viewMode === 'board') {
            await this.updateTodoInState(todoId, { status: statusColumn.dataset.status, is_done: statusColumn.dataset.status === 'done' });
            return;
        }
        const targetItem = e.target.closest('.todo-item');
        if (!targetItem || targetItem.dataset.todoId === todoId || TodosState.sortBy !== 'manual') return;
        const orderedIds = this.sortTodos([...TodosState.todos]).map(todo => todo.id);
        const fromIndex = orderedIds.indexOf(todoId);
        const toIndex = orderedIds.indexOf(targetItem.dataset.todoId);
        if (fromIndex < 0 || toIndex < 0) return;
        orderedIds.splice(toIndex, 0, orderedIds.splice(fromIndex, 1)[0]);
        await Promise.all(orderedIds.map((id, index) => TodosAPI.updateTodo(id, { order: index })));
        TodosState.todos.forEach((todo) => { todo.order = orderedIds.indexOf(todo.id); });
        this.renderTodos();
    },

    async updateTodoInState(todoId, payload) {
        try {
            const updated = await TodosAPI.updateTodo(todoId, payload);
            const todo = TodosState.todos.find(t => t.id === todoId);
            if (todo) Object.assign(todo, updated);
            this.invalidateTodosCache();
            await this.reloadVisibleTodoCollection();
            await this.refreshMarkedTodos();
        } catch (error) {
            console.error('Failed to update todo:', error);
        }
    },

    findTodoById(todoId) {
        return TodosState.todos.find(todo => todo.id === todoId)
            || TodosState.markedTodos.find(todo => todo.id === todoId)
            || TodosState.searchResults.find(todo => todo.id === todoId)
            || TodosState.allTodosCache.find(todo => todo.id === todoId)
            || null;
    },

    handleGlobalShortcuts(e) {
        if (!TodosDOM.workspace?.offsetParent) return;
        const activeTag = document.activeElement?.tagName?.toLowerCase();
        const typing = activeTag === 'input' || activeTag === 'textarea' || document.activeElement?.isContentEditable;
        // Primary+K belongs to the app-wide command palette. Todo-specific
        // actions remain available there without shadowing editor/browser keys.
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') return;
        if (typing) return;
        if (e.key.toLowerCase() === 'n') {
            e.preventDefault();
            TodosDOM.addInput?.focus();
        } else if (e.key.toLowerCase() === 'b') {
            e.preventDefault();
            TodosState.viewMode = TodosState.viewMode === 'board' ? 'list' : 'board';
            this.refreshListHeader();
            this.renderTodos();
        } else if (e.key === '/') {
            e.preventDefault();
            TodosDOM.searchInput?.focus();
        }
    },

    showCommandPalette() {
        let overlay = document.getElementById('todosCommandPaletteOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'todosCommandPaletteOverlay';
            overlay.className = 'todos-command-palette-overlay shared-modal-overlay';
            overlay.hidden = true;
            overlay.setAttribute('aria-hidden', 'true');
            document.body.appendChild(overlay);
        }
        const commands = [
            { id: 'new', label: todosT('todos_command_new_task', 'New task') },
            { id: 'today', label: todosT('todos_view_today', 'Today') },
            { id: 'upcoming', label: todosT('todos_view_upcoming', 'Upcoming') },
            { id: 'overdue', label: todosT('todos_view_overdue', 'Overdue') },
            { id: 'board', label: todosT('todos_view_board_mode', 'Board') },
            { id: 'list', label: todosT('todos_view_list_mode', 'List') },
        ];
        overlay.innerHTML = `
            <div class="todos-command-palette shared-modal shared-modal--compact shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="todosCommandPaletteTitle" tabindex="-1">
                <header class="shared-modal-header shared-modal-header--main">
                    <h3 class="shared-modal-title" id="todosCommandPaletteTitle">${TodosRender.escapeHtml(todosT('todos_command_palette_title', 'Command palette'))}</h3>
                    <button type="button" class="shared-modal-close" data-command-palette-close aria-label="${TodosRender.escapeHtml(todosT('common_close', 'Close'))}">${Icons.close}</button>
                </header>
                <div class="todos-command-list shared-modal-body">
                    ${commands.map(command => `<button type="button" data-command="${command.id}">${TodosRender.escapeHtml(command.label)}</button>`).join('')}
                </div>
            </div>
        `;
        overlay._previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const closePalette = ({ restoreFocus = true } = {}) => {
            overlay.classList.remove('visible');
            overlay.hidden = true;
            overlay.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('modal-open');
            const previousFocus = restoreFocus ? overlay._previousFocus : null;
            overlay._previousFocus = null;
            if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
        };
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        overlay.classList.add('visible');
        document.body.classList.add('modal-open');
        overlay.onclick = (event) => {
            if (event.target === overlay || event.target.closest('[data-command-palette-close]')) {
                closePalette();
                return;
            }
            const command = event.target.closest('[data-command]')?.dataset.command;
            if (!command) return;
            closePalette({ restoreFocus: false });
            if (command === 'new') TodosDOM.addInput?.focus();
            if (command === 'board' || command === 'list') {
                TodosState.viewMode = command;
                this.refreshListHeader();
                this.renderTodos();
            }
            if (['today', 'upcoming', 'overdue'].includes(command)) {
                TodosState.activeView = command;
                this.reloadSelectedTodos();
            }
        };
        overlay.onkeydown = (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                closePalette();
                return;
            }
            if (event.key !== 'Tab') return;
            const dialog = overlay.querySelector('[role="dialog"]');
            const focusable = Array.from(dialog.querySelectorAll(
                'button:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )).filter((element) => !element.hidden && element.getClientRects().length > 0);
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (!first) {
                event.preventDefault();
                dialog.focus({ preventScroll: true });
            } else if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
                event.preventDefault();
                first.focus();
            }
        };
        requestAnimationFrame(() => overlay.querySelector('[data-command]')?.focus({ preventScroll: true }));
    },

    /** Return route metadata for a todo-list editor URL, if one is active. */
    getListEditorRoute(pathname = window.location?.pathname || '') {
        if (pathname === '/workspace/todo/lists/new') {
            return { mode: 'create', listId: null };
        }
        const editMatch = pathname.match(/^\/workspace\/todo\/lists\/([^/]+)\/edit$/);
        if (!editMatch) return null;
        try {
            return { mode: 'edit', listId: decodeURIComponent(editMatch[1]) };
        } catch (_error) {
            return { mode: 'edit', listId: editMatch[1] };
        }
    },

    /** Build the canonical URL for the current create or edit page. */
    getListEditorUrl(mode, listId = null) {
        return mode === 'edit' && listId
            ? `/workspace/todo/lists/${encodeURIComponent(listId)}/edit`
            : '/workspace/todo/lists/new';
    },

    /** Capture every persisted list field so icon-only changes are guarded. */
    getListEditorSnapshot() {
        const mode = TodosState.listEditorMode;
        if (!mode) return null;
        const isEdit = mode === 'edit';
        const title = document.getElementById(isEdit ? 'todosEditListTitle' : 'todosCreateListTitle');
        const description = document.getElementById(isEdit ? 'todosEditListDescription' : 'todosCreateListDescription');
        if (!title) return null;
        return JSON.stringify({
            title: title.value,
            description: description?.value || '',
            icon: this.serializeIconPicker(mode),
        });
    },

    hasUnsavedListEditorChanges() {
        return Boolean(
            TodosState.listEditorMode &&
            TodosState.listEditorInitialSnapshot !== null &&
            this.getListEditorSnapshot() !== TodosState.listEditorInitialSnapshot
        );
    },

    /** Register the editor with the application's shared accessible guard. */
    registerListEditorUnsavedGuard() {
        window.unsavedChangesManager?.register?.({
            id: 'workspace-todos-list-editor-unsaved',
            priority: 180,
            isActive: () => Boolean(TodosState.listEditorMode),
            isDirty: () => this.hasUnsavedListEditorChanges(),
            discard: () => {
                TodosState.listEditorInitialSnapshot = this.getListEditorSnapshot();
            },
            getCopy: () => ({
                subtitle: todosT(
                    'modal_discard_changes_desc',
                    'You have unsaved changes. Are you sure you want to leave without saving?',
                ),
            }),
        });
    },

    /** Run an editor navigation after the shared discard guard approves it. */
    requestListEditorExit(onConfirm) {
        if (typeof window.unsavedChangesManager?.confirmIfNeeded === 'function') {
            window.unsavedChangesManager.confirmIfNeeded({
                id: 'workspace-todos-list-editor-unsaved',
                onConfirm,
            });
            return;
        }
        onConfirm?.();
    },

    /**
     * Keep browser Back/Forward in sync with the routed editor. When Back is
     * requested with dirty fields, restore the editor URL until the user has
     * made an explicit choice in the shared confirmation dialog.
     */
    registerListEditorHistoryHandler() {
        if (this._listEditorPopstateHandler || typeof window === 'undefined') return;
        this._listEditorPopstateHandler = () => {
            const route = this.getListEditorRoute();
            const routeMatchesActiveEditor = Boolean(
                route &&
                route.mode === TodosState.listEditorMode &&
                (route.mode !== 'edit' || route.listId === TodosState.editingList?.id)
            );

            if (TodosState.listEditorMode && !routeMatchesActiveEditor) {
                if (TodosState.listEditorHistoryBypass) {
                    TodosState.listEditorHistoryBypass = false;
                    this.closeListEditorPage({ updateHistory: false });
                    if (route && !TodosState.isLoadingLists) this.syncListEditorRoute();
                    return;
                }

                if (this.hasUnsavedListEditorChanges()) {
                    const editorUrl = this.getListEditorUrl(
                        TodosState.listEditorMode,
                        TodosState.editingList?.id,
                    );
                    history.pushState({ todosListEditor: true }, '', editorUrl);
                    this.requestListEditorExit(() => {
                        TodosState.listEditorHistoryBypass = true;
                        history.back();
                    });
                    return;
                }

                this.closeListEditorPage({ updateHistory: false });
                if (route && !TodosState.isLoadingLists) this.syncListEditorRoute();
                return;
            }

            if (route && !TodosState.isLoadingLists) {
                this.syncListEditorRoute();
            }
        };
        window.addEventListener('popstate', this._listEditorPopstateHandler);
        this._listEditorBeforeUnloadHandler = (event) => {
            if (!this.hasUnsavedListEditorChanges()) return;
            event.preventDefault();
            event.returnValue = '';
        };
        window.addEventListener('beforeunload', this._listEditorBeforeUnloadHandler);
    },

    /** Render the shared full-page form for either create or edit. */
    renderListEditorPage(mode, list = null) {
        const page = TodosDOM.listEditorPage;
        if (!page) return false;

        const isEdit = mode === 'edit';
        const prefix = isEdit ? 'todosEdit' : 'todos';
        const formId = isEdit ? 'todosEditListForm' : 'todosCreateListForm';
        const titleId = isEdit ? 'todosEditListTitle' : 'todosCreateListTitle';
        const descriptionId = isEdit ? 'todosEditListDescription' : 'todosCreateListDescription';
        const errorId = isEdit ? 'todosEditListTitleError' : 'todosCreateListTitleError';
        const submitId = isEdit ? 'todosEditListSubmitBtn' : 'todosCreateListSubmitBtn';
        const pageTitleKey = isEdit ? 'todos_edit_list' : 'todos_create_list_title';
        const pageTitle = isEdit ? todosT(pageTitleKey, 'Edit List') : todosT(pageTitleKey, 'New List');
        const namePlaceholderKey = isEdit ? 'todos_edit_list_name_placeholder' : 'todos_create_list_name_placeholder';
        const namePlaceholder = isEdit
            ? todosT(namePlaceholderKey, 'List name')
            : todosT(namePlaceholderKey, 'e.g., Work Tasks');
        const descriptionPlaceholderKey = isEdit ? 'todos_edit_list_desc_placeholder' : 'todos_create_list_desc_placeholder';
        const descriptionPlaceholder = isEdit
            ? todosT(descriptionPlaceholderKey, 'Add a description...')
            : todosT(descriptionPlaceholderKey, 'What is this list for?');
        const submitKey = isEdit ? 'files_folder_save_changes' : 'todos_create_list_submit';
        const submitLabel = isEdit
            ? todosT(submitKey, 'Save Changes')
            : todosT(submitKey, 'Create List');
        page.innerHTML = `
            <div class="todos-list-editor-shell">
                <header class="todos-list-editor-header">
                    <h2 class="todos-list-editor-title" id="todosListEditorHeading" data-i18n="${pageTitleKey}">${TodosRender.escapeHtml(pageTitle)}</h2>
                </header>
                <form class="todos-list-editor-form" id="${formId}" aria-labelledby="todosListEditorHeading" novalidate>
                    <div class="todos-list-editor-field">
                        <label class="todos-list-editor-label" for="${titleId}" data-i18n="todos_create_list_name_label">${TodosRender.escapeHtml(todosT('todos_create_list_name_label', 'List name'))}</label>
                        <input type="text" class="todos-list-editor-input" id="${titleId}" value="${TodosRender.escapeHtml(list?.title || '')}" placeholder="${TodosRender.escapeHtml(namePlaceholder)}" data-i18n-attr="placeholder:${namePlaceholderKey}" required autocomplete="off" aria-describedby="${errorId}" aria-invalid="false">
                        <p class="field-validation-error" id="${errorId}" data-i18n="todos_create_list_name_error" aria-hidden="true" hidden>${TodosRender.escapeHtml(todosT('todos_create_list_name_error', 'Please enter a list name'))}</p>
                    </div>
                    <div class="todos-list-editor-field">
                        <label class="todos-list-editor-label" for="${descriptionId}" data-i18n="todos_create_list_desc_label">${TodosRender.escapeHtml(todosT('todos_create_list_desc_label', 'Description (optional)'))}</label>
                        <textarea class="todos-list-editor-textarea" id="${descriptionId}" placeholder="${TodosRender.escapeHtml(descriptionPlaceholder)}" data-i18n-attr="placeholder:${descriptionPlaceholderKey}" rows="5">${TodosRender.escapeHtml(list?.description || '')}</textarea>
                    </div>
                    <div class="todos-list-editor-field">
                        <span class="todos-list-editor-label" id="${prefix}IconPickerLabel" data-i18n="todos_create_list_icon_label">${TodosRender.escapeHtml(todosT('todos_create_list_icon_label', 'Icon & Color'))}</span>
                        <div class="todos-icon-picker" id="${prefix}IconPicker">
                            <button type="button" class="todos-icon-picker-trigger" id="${prefix}IconPickerTrigger" aria-labelledby="${prefix}IconPickerLabel ${prefix}IconPickerText">
                                <div class="todos-icon-picker-preview" id="${prefix}IconPickerPreview"></div>
                                <span class="todos-icon-picker-text" id="${prefix}IconPickerText" data-i18n="todos_icon_picker_text">${TodosRender.escapeHtml(todosT('todos_icon_picker_text', 'Choose icon & color'))}</span>
                                <span class="todos-icon-picker-caret" aria-hidden="true">${Icons.chevron}</span>
                            </button>
                            <div class="todos-icon-picker-dropdown">
                                <div class="todos-icon-picker-section">
                                    <div class="todos-icon-picker-panel active" id="${prefix}IconSvgPanel" data-panel="svg" role="group" aria-label="${TodosRender.escapeHtml(todosT('todos_icon_type_aria', 'Todo list icon type'))}"><div class="todos-icon-grid" id="${prefix}IconGrid"></div></div>
                                </div>
                                <div class="todos-icon-picker-section">
                                    <p class="todos-icon-picker-section-title" data-i18n="todos_icon_picker_colors">${TodosRender.escapeHtml(todosT('todos_icon_picker_colors', 'Colors'))}</p>
                                    <div class="todos-color-grid" id="${prefix}ColorGrid"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="todos-list-editor-actions">
                        <button type="button" class="om-button border cancel" id="todosListEditorCancelBtn" data-i18n="common_cancel">${TodosRender.escapeHtml(todosT('common_cancel', 'Cancel'))}</button>
                        <button type="submit" class="om-button border submit" id="${submitId}" data-i18n="${submitKey}">${TodosRender.escapeHtml(submitLabel)}</button>
                    </div>
                </form>
            </div>
        `;

        page.hidden = false;
        page.setAttribute('aria-hidden', 'false');
        TodosDOM.emptyState && (TodosDOM.emptyState.style.display = 'none');
        TodosDOM.listView && (TodosDOM.listView.style.display = 'none');
        const noListsState = document.getElementById('todosNoListsState');
        if (noListsState) noListsState.style.display = 'none';

        const titleInput = document.getElementById(titleId);
        const titleError = document.getElementById(errorId);
        document.getElementById(formId)?.addEventListener('submit', (event) => {
            event.preventDefault();
            if (isEdit) this.handleEditList();
            else this.handleCreateList();
        });
        titleInput?.addEventListener('input', () => clearTodoListTitleError(titleInput, titleError));
        const requestClose = () => this.requestListEditorExit(() => this.closeListEditorPage({ useHistory: true }));
        document.getElementById('todosListEditorCancelBtn')?.addEventListener('click', requestClose);

        this.getIconPickerController(mode)?.bind?.();
        this.renderIconPicker(mode);
        this.updateIconPickerPreview(mode);
        this.showMobileContent();
        requestAnimationFrame(() => titleInput?.focus());
        return true;
    },

    showCreateListPage(trigger = null, options = {}) {
        if (TodosState.listEditorMode === 'create') {
            document.getElementById('todosCreateListTitle')?.focus();
            return;
        }
        if (TodosState.listEditorMode) {
            this.requestListEditorExit(() => {
                this.closeListEditorPage({ updateHistory: false, restoreFocus: false });
                this.showCreateListPage(trigger, options);
            });
            return;
        }
        TodosState.listEditorMode = 'create';
        TodosState.editingList = null;
        TodosState.listEditorReturnFocus = trigger || document.activeElement;
        this.resetIconPickerState('create');
        if (!options.skipHistory) {
            history.pushState(
                { todosListEditor: true, todosListEditorOrigin: 'workspace', mode: 'create' },
                '',
                this.getListEditorUrl('create'),
            );
        }
        if (!this.renderListEditorPage('create')) return;
        TodosState.listEditorInitialSnapshot = this.getListEditorSnapshot();
    },

    /** Restore the list content behind the editor and optionally update URL history. */
    closeListEditorPage(options = {}) {
        const {
            updateHistory = true,
            useHistory = false,
            replaceHistory = false,
            restoreFocus = true,
        } = options;
        const page = TodosDOM.listEditorPage;
        const returnFocus = TodosState.listEditorReturnFocus;
        const mode = TodosState.listEditorMode;
        if (mode) {
            this.toggleIconPicker(mode, false);
        }
        TodosState.listEditorMode = null;
        TodosState.editingList = null;
        TodosState.listEditorInitialSnapshot = null;
        TodosState.listEditorReturnFocus = null;
        if (page) {
            page.hidden = true;
            page.setAttribute('aria-hidden', 'true');
            page.replaceChildren();
        }

        if (TodosState.selectedListId) {
            TodosDOM.listView && (TodosDOM.listView.style.display = 'flex');
            TodosDOM.emptyState && (TodosDOM.emptyState.style.display = 'none');
        } else if (TodosState.lists.length === 0) {
            this.showNoListsState();
        } else {
            TodosDOM.listView && (TodosDOM.listView.style.display = 'none');
            TodosDOM.emptyState && (TodosDOM.emptyState.style.display = 'flex');
        }

        if (window.innerWidth <= 768 && !TodosState.selectedListId) {
            this.hideMobileContent();
        }

        if (updateHistory) {
            if (useHistory && history.state?.todosListEditorOrigin === 'workspace') {
                history.back();
            } else {
                const method = replaceHistory || useHistory ? 'replaceState' : 'pushState';
                history[method]({ workspaceTab: 'todo' }, '', '/workspace/todo');
            }
        }
        if (restoreFocus) requestAnimationFrame(() => returnFocus?.focus?.());
    },

    /** Open the editor represented by the browser URL after lists are loaded. */
    syncListEditorRoute() {
        const route = this.getListEditorRoute();
        if (!route) {
            if (TodosState.listEditorMode) this.closeListEditorPage({ updateHistory: false });
            return;
        }
        if (route.mode === 'create') {
            if (TodosState.listEditorMode !== 'create') {
                this.showCreateListPage(null, { skipHistory: true });
            } else {
                // An empty-list refresh may have rendered its onboarding state
                // after the editor. Reassert the routed page as the sole view.
                const page = TodosDOM.listEditorPage;
                if (page) {
                    page.hidden = false;
                    page.setAttribute('aria-hidden', 'false');
                }
                TodosDOM.emptyState && (TodosDOM.emptyState.style.display = 'none');
                TodosDOM.listView && (TodosDOM.listView.style.display = 'none');
                const noListsState = document.getElementById('todosNoListsState');
                if (noListsState) noListsState.style.display = 'none';
            }
            return;
        }
        if (TodosState.listEditorMode === 'edit' && TodosState.editingList?.id === route.listId) return;
        const list = TodosState.lists.find((item) => item.id === route.listId);
        if (!list) {
            history.replaceState({ workspaceTab: 'todo' }, '', '/workspace/todo');
            this.closeListEditorPage({ updateHistory: false });
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_list_not_found', 'Todo list not found'), 'error');
            }
            return;
        }
        this.showEditListPage(route.listId, null, { skipHistory: true });
    },

    async handleCreateList() {
        const titleInput = document.getElementById('todosCreateListTitle');
        const titleError = document.getElementById('todosCreateListTitleError');
        const descInput = document.getElementById('todosCreateListDescription');
        if (!titleInput) return;

        const title = titleInput.value.trim();
        if (!title) {
            showTodoListTitleError(titleInput, titleError);
            return;
        }
        clearTodoListTitleError(titleInput, titleError);

        const description = descInput ? descInput.value.trim() : '';
        const icon = this.serializeIconPicker('create');

        const submitBtn = document.getElementById('todosCreateListSubmitBtn');
        if (submitBtn) submitBtn.disabled = true;

        try {
            const newList = await TodosAPI.createList(title, description, icon);
            TodosState.lists.push(newList);
            this.renderSidebarLists();
            this.closeListEditorPage({ replaceHistory: true, restoreFocus: false });
            
            // Select the new list
            this.selectList(newList.id, { skipEditorGuard: true });
        } catch (error) {
            console.error('Failed to create list:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_create_list', 'Failed to create list'), 'error');
            }
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    },

    // Icon Picker Methods
    getIconPickerState(mode = 'create') {
        return mode === 'edit' ? TodosState.editIconPicker : TodosState.iconPicker;
    },

    getIconPickerController(mode = 'create') {
        return mode === 'edit' ? TodoEditIconPicker : TodoCreateIconPicker;
    },

    getIconPickerRefs(mode = 'create') {
        return getTodoIconPickerRefs(mode);
    },

    resetIconPickerState(mode = 'create', iconData = null) {
        const iconValue = iconData?.iconId || iconData?.svg || TODO_DEFAULT_ICON_ID;
        this.getIconPickerController(mode)?.reset?.(iconValue, iconData?.color || TODOS_COLORS[0].hex);
    },

    serializeIconPicker(mode = 'create') {
        return this.getIconPickerController(mode)?.serialize?.({ includeColor: true })
            || JSON.stringify({ preset: TODO_DEFAULT_ICON_ID, color: TODOS_COLORS[0].hex });
    },

    renderIconPicker(mode = 'create') {
        this.getIconPickerController(mode)?.render?.();
    },

    updateIconPickerPreview(mode = 'create') {
        this.getIconPickerController(mode)?.updatePreview?.();
    },

    toggleIconPicker(mode = 'create', open) {
        this.getIconPickerController(mode)?.setOpen?.(open);
    },

    selectIcon(index, mode = 'create') {
        this.getIconPickerController(mode)?.selectPreset?.(TODOS_ICONS[index]?.id || TODO_DEFAULT_ICON_ID);
    },

    selectColor(index, mode = 'create') {
        this.getIconPickerController(mode)?.selectColor?.(index);
    },

    setupSortListeners() {
        // Both controls use the same compact menu behavior, but filtering and
        // sorting remain separate concepts with independent state.
        const filterTrigger = document.getElementById('todosFilterTrigger');
        const filterDropdown = document.getElementById('todosFilterDropdown');
        if (filterTrigger && filterDropdown) {
            filterTrigger.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                this.toggleFilterDropdown();
            });

            filterDropdown.addEventListener('click', (event) => {
                const option = event.target.closest('.todos-filter-option');
                if (option) this.setActiveView(option.dataset.view);
            });

            this.bindHeaderMenuKeyboard({
                trigger: filterTrigger,
                dropdown: filterDropdown,
                optionSelector: '.todos-filter-option',
                openMenu: () => this.toggleFilterDropdown(true),
                closeMenu: () => this.toggleFilterDropdown(false),
            });
        }

        const sortTrigger = document.getElementById('todosSortTrigger');
        const sortDropdown = document.getElementById('todosSortDropdown');
        if (sortTrigger && sortDropdown) {
            sortTrigger.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.toggleSortDropdown();
            });

            sortDropdown.addEventListener('click', (e) => {
                const option = e.target.closest('.todos-sort-option');
                if (option) {
                    const sortId = option.dataset.sort;
                    this.setSortOrder(sortId);
                }
            });

            this.bindHeaderMenuKeyboard({
                trigger: sortTrigger,
                dropdown: sortDropdown,
                optionSelector: '.todos-sort-option',
                openMenu: () => this.toggleSortDropdown(true),
                closeMenu: () => this.toggleSortDropdown(false),
            });
        }

        const boardToggle = document.getElementById('todosBoardToggle');
        if (boardToggle) {
            boardToggle.addEventListener('click', () => {
                TodosState.viewMode = TodosState.viewMode === 'board' ? 'list' : 'board';
                this.refreshListHeader();
                this.renderTodos();
            });
        }

        // Header rerenders replace its buttons, so replace the document-level
        // outside-click listener as well instead of accumulating stale handlers.
        if (this._headerOutsideClickHandler) {
            document.removeEventListener('click', this._headerOutsideClickHandler);
        }
        this._headerOutsideClickHandler = (event) => {
            const filterSelector = document.getElementById('todosFilterSelector');
            const sortSelector = document.getElementById('todosSortSelector');
            if (TodosState.filterDropdownOpen && !filterSelector?.contains(event.target)) {
                this.toggleFilterDropdown(false);
            }
            if (TodosState.sortDropdownOpen && !sortSelector?.contains(event.target)) {
                this.toggleSortDropdown(false);
            }
        };
        document.addEventListener('click', this._headerOutsideClickHandler);
    },

    /**
     * Add standard arrow-key, Home/End, Escape and Tab behavior to a header
     * menu while leaving Enter and Space to the native button elements.
     */
    bindHeaderMenuKeyboard({ trigger, dropdown, optionSelector, openMenu, closeMenu }) {
        const getOptions = () => [...dropdown.querySelectorAll(optionSelector)]
            .filter(option => !option.disabled);

        trigger.addEventListener('keydown', (event) => {
            if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
            event.preventDefault();
            openMenu();
            const options = getOptions();
            const target = event.key === 'ArrowDown' ? options[0] : options.at(-1);
            target?.focus();
        });

        dropdown.addEventListener('keydown', (event) => {
            const options = getOptions();
            const currentIndex = options.indexOf(document.activeElement);
            let nextIndex = null;

            if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % options.length;
            if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + options.length) % options.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = options.length - 1;

            if (nextIndex !== null && options.length) {
                event.preventDefault();
                options[nextIndex]?.focus();
                return;
            }

            if (event.key === 'Escape') {
                event.preventDefault();
                closeMenu();
                trigger.focus();
            } else if (event.key === 'Tab') {
                closeMenu();
            }
        });
    },

    toggleFilterDropdown(open) {
        const selector = document.getElementById('todosFilterSelector');
        const shouldOpen = typeof open === 'boolean' ? open : !TodosState.filterDropdownOpen;
        if (shouldOpen) this.toggleSortDropdown(false);

        TodosState.filterDropdownOpen = Boolean(selector && shouldOpen);
        selector?.classList.toggle('open', TodosState.filterDropdownOpen);
        selector?.querySelector('#todosFilterTrigger')
            ?.setAttribute('aria-expanded', String(TodosState.filterDropdownOpen));
    },

    toggleSortDropdown(open) {
        const selector = document.getElementById('todosSortSelector');
        const shouldOpen = typeof open === 'boolean' ? open : !TodosState.sortDropdownOpen;
        if (shouldOpen) this.toggleFilterDropdown(false);

        TodosState.sortDropdownOpen = Boolean(selector && shouldOpen);
        selector?.classList.toggle('open', TodosState.sortDropdownOpen);
        selector?.querySelector('#todosSortTrigger')
            ?.setAttribute('aria-expanded', String(TodosState.sortDropdownOpen));
    },

    /** Apply a validated server-side view filter and return focus to its trigger. */
    async setActiveView(viewId) {
        const nextView = TODOS_VIEW_OPTIONS.some(view => view.id === viewId) ? viewId : 'all';
        if (TodosState.activeView === nextView) {
            this.toggleFilterDropdown(false);
            document.getElementById('todosFilterTrigger')?.focus();
            return;
        }

        TodosState.activeView = nextView;
        this.toggleFilterDropdown(false);
        const reloadPromise = this.reloadSelectedTodos();
        document.getElementById('todosFilterTrigger')?.focus();
        await reloadPromise;
    },

    async setSortOrder(sortId) {
        if (TodosState.sortBy === sortId) {
            this.toggleSortDropdown(false);
            return;
        }

        TodosState.sortBy = sortId;
        this.toggleSortDropdown(false);

        // Re-render header to update sort button label
        this.refreshListHeader();
        document.getElementById('todosSortTrigger')?.focus();

        // Reload so every scroll batch uses the same server-side ordering.
        await this.reloadSelectedTodos();
    },

    refreshListHeader() {
        const list = TodosState.lists.find(l => l.id === TodosState.selectedListId);
        if (list) {
            const listHeader = TodosDOM.listHeader;
            if (listHeader) {
                listHeader.innerHTML = TodosRender.listHeader(list);
                this.setupSortListeners();
            }
        }
    },

    setupIconPickerListeners() {
        TodoCreateIconPicker?.bind?.();
        TodoEditIconPicker?.bind?.();
    },

    // ============================================================================
    // Dropdown Menu Methods
    // ============================================================================

    toggleListDropdown(listId) {
        if (TodosState.openDropdownListId === listId) {
            this.closeAllDropdowns();
        } else {
            this.closeAllDropdowns();
            TodosState.openDropdownListId = listId;
            const dropdown = document.querySelector(`[data-todo-dropdown][data-list-id="${listId}"]`);
            if (dropdown) {
                const trigger = dropdown.parentElement?.querySelector('.todos-list-item-menu-btn');
                window.prepareDropdownOpeningAnimation?.(trigger, dropdown);
                dropdown.classList.add('open');
            }
        }
    },

    closeAllDropdowns() {
        TodosState.openDropdownListId = null;
        document.querySelectorAll('[data-todo-dropdown].open').forEach(el => {
            el.classList.remove('open');
        });
    },

    // ============================================================================
    // Delete List Methods
    // ============================================================================

    showDeleteListWarning(listId) {
        const list = TodosState.lists.find(l => l.id === listId);
        if (!list) return;

        // Create or get overlay
        let overlay = document.getElementById('todosDeleteWarningOverlay');
        if (!overlay) {
            overlay = window.DeleteWarningModal?.create({
                id: 'todosDeleteWarningOverlay',
                icon: 'trash',
                title: { text: todosT('todos_delete_list', 'Delete List'), i18n: 'todos_delete_list' },
                descriptions: [{ id: 'todosDeleteListDescription', text: '' }],
                actions: [
                    { id: 'todosDeleteCancelBtn', role: 'cancel', variant: 'cancel', text: todosT('todos_share_cancel', 'Cancel'), i18n: 'todos_share_cancel' },
                    { id: 'todosDeleteConfirmBtn', variant: 'danger', text: todosT('common_delete', 'Delete'), i18n: 'common_delete', textId: 'todosDeleteConfirmText', attrs: { 'data-list-id': '' } },
                ],
            });
            if (!overlay) return;
            document.body.appendChild(overlay);

            // Add event listeners
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this.hideDeleteListWarning();
            });
            document.getElementById('todosDeleteCancelBtn').addEventListener('click', () => this.hideDeleteListWarning());
            document.getElementById('todosDeleteConfirmBtn').addEventListener('click', () => this.confirmDeleteList());
        }

        // Update content
        const description = document.getElementById('todosDeleteListDescription');
        if (description) {
            description.textContent = todosTf(
                'todos_delete_list_confirm',
                'Are you sure you want to delete "{title}"? This will permanently delete the list and all its todos. This action cannot be undone.',
                { title: list.title }
            );
        }
        document.getElementById('todosDeleteConfirmBtn').dataset.listId = listId;
        
        // Show overlay
        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('active'));
    },

    hideDeleteListWarning() {
        const overlay = document.getElementById('todosDeleteWarningOverlay');
        if (overlay) {
            overlay.classList.remove('active');
            setTimeout(() => overlay.setAttribute('hidden', ''), 200);
        }
    },

    async confirmDeleteList() {
        const confirmBtn = document.getElementById('todosDeleteConfirmBtn');
        const listId = confirmBtn?.dataset.listId;
        if (!listId) return;

        // Show loading state
        confirmBtn.disabled = true;
        confirmBtn.classList.add('loading');

        try {
            await TodosAPI.deleteList(listId);
            
            // Remove from state
            TodosState.lists = TodosState.lists.filter(l => l.id !== listId);
            
            // If deleted list was selected, clear selection
            if (TodosState.selectedListId === listId) {
                TodosState.selectedListId = null;
                TodosState.todos = [];
                
                // Show empty state or select first list
                if (TodosState.lists.length > 0) {
                    this.selectList(TodosState.lists[0].id);
                } else {
                    this.showNoListsState();
                }
            }
            
            this.renderSidebarLists();
            this.hideDeleteListWarning();
            
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_list_deleted_success', 'List deleted successfully'), 'success');
            }
        } catch (error) {
            console.error('Failed to delete list:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_delete_list', 'Failed to delete list'), 'error');
            }
        } finally {
            confirmBtn.disabled = false;
            confirmBtn.classList.remove('loading');
        }
    },

    // ============================================================================
    // Edit List Methods
    // ============================================================================

    showEditListPage(listId, trigger = null, options = {}) {
        const list = TodosState.lists.find(l => l.id === listId);
        if (!list) return;

        if (TodosState.listEditorMode === 'edit' && TodosState.editingList?.id === listId) {
            document.getElementById('todosEditListTitle')?.focus();
            return;
        }
        if (TodosState.listEditorMode) {
            this.requestListEditorExit(() => {
                this.closeListEditorPage({ updateHistory: false, restoreFocus: false });
                this.showEditListPage(listId, trigger, options);
            });
            return;
        }

        TodosState.listEditorMode = 'edit';
        TodosState.editingList = list;
        TodosState.listEditorReturnFocus = trigger || document.activeElement;
        this.resetIconPickerState('edit', TodosRender.parseIcon(list.icon));
        if (!options.skipHistory) {
            history.pushState(
                { todosListEditor: true, todosListEditorOrigin: 'workspace', mode: 'edit', listId },
                '',
                this.getListEditorUrl('edit', listId),
            );
        }
        if (!this.renderListEditorPage('edit', list)) return;
        TodosState.listEditorInitialSnapshot = this.getListEditorSnapshot();
    },

    async handleEditList() {
        if (!TodosState.editingList) return;

        const titleInput = document.getElementById('todosEditListTitle');
        const descInput = document.getElementById('todosEditListDescription');
        const submitBtn = document.getElementById('todosEditListSubmitBtn');
        const titleError = document.getElementById('todosEditListTitleError');

        const title = titleInput.value.trim();
        if (!title) {
            showTodoListTitleError(titleInput, titleError);
            return;
        }
        clearTodoListTitleError(titleInput, titleError);

        const description = descInput ? descInput.value.trim() : '';
        const icon = this.serializeIconPicker('edit');

        if (submitBtn) submitBtn.disabled = true;

        try {
            const updatedList = await TodosAPI.updateList(TodosState.editingList.id, {
                title,
                description,
                icon,
            });
            
            // Update in state
            const index = TodosState.lists.findIndex(l => l.id === updatedList.id);
            if (index >= 0) {
                TodosState.lists[index] = updatedList;
            }
            
            this.renderSidebarLists();
            
            // Update header if this is the selected list
            if (TodosState.selectedListId === updatedList.id) {
                const listHeader = TodosDOM.listHeader;
                if (listHeader) {
                    listHeader.innerHTML = TodosRender.listHeader(updatedList);
                    this.setupSortListeners();
                }
            }
            
            this.closeListEditorPage({ replaceHistory: true, restoreFocus: false });
            
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_list_updated_success', 'List updated successfully'), 'success');
            }
        } catch (error) {
            console.error('Failed to update list:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_error_update_list', 'Failed to update list'), 'error');
            }
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    },

    // ============================================================================
    // Sharing Methods
    // ============================================================================

    async showShareModal(listId) {
        const list = TodosState.lists.find((item) => item.id === listId);
        if (!list) return;
        if (!canManageTodoListSharing(list)) return;

        let overlay = document.getElementById('todosShareOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'todosShareOverlay';
            overlay.className = 'cs-overlay shared-modal-overlay';
            document.body.appendChild(overlay);
        }

        TodosState.sharingListId = listId;
        TodosState.shareMode = 'list';
        TodosState.shareAction = 'link';
        TodosState.shareStatus = null;
        TodosState.currentShareType = 'live';
        TodosState.selectedUserIds = [];

        this.renderShareModal(list);
        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('cs-active'));
        await this.loadShareStatus(listId);
    },

    getShareAction() {
        return String(document.querySelector('input[name="todosShareAction"]:checked')?.value || TodosState.shareAction || 'link');
    },

    getShareTypeSelection() {
        return String(document.querySelector('input[name="todosShareType"]:checked')?.value || TodosState.currentShareType || 'live');
    },

    getShareTypeLabel(shareType) {
        if (shareType === 'clone') return todosT('todos_share_type_clone_label', 'Clone');
        if (shareType === 'collaborate') return todosT('todos_share_type_collaborate_label', 'Collaborate');
        return todosT('todos_share_type_live_label', 'Live');
    },

    getShareTypeDescription(shareType) {
        if (shareType === 'clone') {
            return todosT('todos_share_type_clone_desc', 'Recipients get their own independent copy.');
        }
        if (shareType === 'collaborate') {
            return todosT('todos_share_type_collaborate_desc', 'Recipients can work with a synced shared list.');
        }
        return todosT('todos_share_type_live_desc', 'Recipients can view this list with live updates.');
    },

    renderShareModal(list) {
        const overlay = document.getElementById('todosShareOverlay');
        if (!overlay || !list) return;

        const status = TodosState.shareStatus || {};
        const hasShares = Boolean(status.clone_share_id || status.live_share_id || status.collaborate_share_id);
        const isListMode = TodosState.shareMode === 'list';
        const isInvite = TodosState.shareAction === 'invite';
        const shares = [];
        if (status.clone_share_id) shares.push({ type: 'clone', id: status.clone_share_id, count: 0 });
        if (status.live_share_id) shares.push({ type: 'live', id: status.live_share_id, count: status.live_subscriber_count || 0 });
        if (status.collaborate_share_id) shares.push({ type: 'collaborate', id: status.collaborate_share_id, count: status.collaborate_subscriber_count || 0 });

        overlay.innerHTML = `
            <div class="cs-modal shared-modal shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="todosShareTitle" tabindex="-1">
                <header class="cs-header shared-modal-header shared-modal-header--main">
                    <div class="cs-header-text shared-modal-heading">
                        <h3 class="cs-title shared-modal-title" id="todosShareTitle">${TodosRender.escapeHtml(todosT('todos_share_title', 'Share List'))}</h3>
                        <p class="cs-subtitle shared-modal-subtitle">${TodosRender.escapeHtml(list.title)}</p>
                    </div>
                    <button type="button" class="cs-icon-btn shared-modal-close" id="todosShareCloseBtn" aria-label="${TodosRender.escapeHtml(todosT('todos_share_close_aria', 'Close share dialog'))}">
                        ${Icons.close}
                    </button>
                </header>
                <div class="cs-body shared-modal-body">
                    <section class="cs-section" ${isListMode && hasShares ? '' : 'hidden'}>
                        <div class="cs-section-head"><span class="cs-section-label">${TodosRender.escapeHtml(todosT('todos_share_active_links', 'Active links'))}</span></div>
                        <div class="cs-link-list" id="todosShareLinkList">${shares.map((share) => this.renderShareLinkCard(share)).join('')}</div>
                    </section>
                    <section class="cs-empty" ${isListMode && !hasShares ? '' : 'hidden'}>
                        <div class="cs-empty-icon" aria-hidden="true">${Icons.urlLink}</div>
                        <p class="cs-empty-title">${TodosRender.escapeHtml(todosT('todos_share_empty_title', 'No share link yet'))}</p>
                        <p class="cs-empty-desc">${TodosRender.escapeHtml(todosT('todos_share_empty_desc', 'Create one or more links to share this list.'))}</p>
                    </section>
                    <section class="cs-form" ${isListMode ? 'hidden' : ''}>
                        <div class="cs-section-head"><span class="cs-section-label">${TodosRender.escapeHtml(isInvite ? todosT('todos_share_invite_users', 'Invite users') : todosT('todos_share_create_new_link', 'Create new link'))}</span></div>
                        <div class="cs-field">
                            <label class="cs-field-label">${TodosRender.escapeHtml(todosT('todos_share_kind_label', 'Share kind'))}</label>
                            <div class="cs-radio-group" role="radiogroup" aria-label="${TodosRender.escapeHtml(todosT('todos_share_type_aria', 'Todo share type'))}">
                                ${['live', 'collaborate', 'clone'].map((shareType) => `
                                    <label class="cs-radio">
                                        <input type="radio" name="todosShareType" value="${TodosRender.escapeHtml(shareType)}" ${TodosState.currentShareType === shareType ? 'checked' : ''}>
                                        <div class="cs-radio-content">
                                            <span class="cs-radio-title">${TodosRender.escapeHtml(this.getShareTypeLabel(shareType))}</span>
                                            <span class="cs-radio-desc">${TodosRender.escapeHtml(this.getShareTypeDescription(shareType))}</span>
                                        </div>
                                    </label>
                                `).join('')}
                            </div>
                        </div>
                        <div class="cs-field">
                            <label class="cs-field-label">${TodosRender.escapeHtml(todosT('todos_share_delivery_label', 'Delivery'))}</label>
                            <div class="cs-radio-group" role="radiogroup" aria-label="${TodosRender.escapeHtml(todosT('todos_share_delivery_aria', 'Todo share delivery'))}">
                                <label class="cs-radio">
                                    <input type="radio" name="todosShareAction" value="link" ${TodosState.shareAction === 'link' ? 'checked' : ''}>
                                    <div class="cs-radio-content">
                                        <span class="cs-radio-title">${TodosRender.escapeHtml(todosT('todos_share_action_link_title', 'Create a share link'))}</span>
                                        <span class="cs-radio-desc">${TodosRender.escapeHtml(todosT('todos_share_action_link_desc', 'Generate a reusable link for the selected share kind.'))}</span>
                                    </div>
                                </label>
                                <label class="cs-radio">
                                    <input type="radio" name="todosShareAction" value="invite" ${TodosState.shareAction === 'invite' ? 'checked' : ''}>
                                    <div class="cs-radio-content">
                                        <span class="cs-radio-title">${TodosRender.escapeHtml(todosT('todos_share_action_invite_title', 'Invite specific users'))}</span>
                                        <span class="cs-radio-desc">${TodosRender.escapeHtml(todosT('todos_share_action_invite_desc', 'Send a workspace invitation using the selected share kind.'))}</span>
                                    </div>
                                </label>
                            </div>
                        </div>
                        <div class="cs-field cs-invite-field" id="todosShareInviteField" ${isInvite ? '' : 'hidden'}>
                            <label class="cs-field-label" for="todosInviteUserSearch">${TodosRender.escapeHtml(todosT('todos_share_select_users_label', 'Select users to invite'))}</label>
                            <div class="cs-invite-search">
                                    ${Icons.magnifyingGlass}
                                <input type="text" id="todosInviteUserSearch" class="cs-input cs-invite-search-input" placeholder="${TodosRender.escapeHtml(todosT('todos_share_search_users_placeholder', 'Search users...'))}" aria-describedby="todosInviteUserError" aria-invalid="false">
                            </div>
                            <p class="cs-field-error" id="todosInviteUserError" role="alert" hidden></p>
                            <div class="cs-invite-user-list" id="todosInviteUserList"><div class="cs-invite-state">${TodosRender.escapeHtml(TodosState.publicUsersLoaded ? todosT('todos_share_no_users_available', 'No users available to invite.') : todosT('todos_share_loading_users', 'Loading users...'))}</div></div>
                            <div class="cs-invite-selected" id="todosSelectedUsers" hidden>
                                <div class="cs-invite-selected-head">${TodosRender.escapeHtml(todosT('todos_share_selected_label', 'Selected'))} (<span id="todosSelectedCount">0</span>)</div>
                                <div class="cs-invite-selected-list" id="todosSelectedUsersList"></div>
                            </div>
                        </div>
                    </section>
                </div>
                <footer class="cs-footer shared-modal-footer">
                    <button type="button" class="cs-btn cs-btn-ghost om-button border cancel" id="todosShareSecondaryBtn">${TodosRender.escapeHtml(isListMode ? todosT('todos_share_done', 'Done') : (hasShares ? todosT('todos_share_cancel', 'Cancel') : todosT('todos_share_done', 'Done')))}</button>
                    <button type="button" class="cs-btn cs-btn-primary om-button border submit" id="todosSharePrimaryBtn">${TodosRender.escapeHtml(isListMode ? (hasShares ? todosT('todos_share_new_link', 'New link') : todosT('todos_share_create_link', 'Create link')) : (isInvite ? todosT('todos_share_send_invites', 'Send invites') : todosT('todos_share_create_link', 'Create link')))}</button>
                </footer>
            </div>
        `;

        overlay.onclick = (event) => { if (event.target === overlay) this.hideShareModal(); };
        document.getElementById('todosShareCloseBtn')?.addEventListener('click', () => this.hideShareModal());
        document.getElementById('todosShareSecondaryBtn')?.addEventListener('click', () => {
            if (TodosState.shareMode === 'list' || !hasShares) {
                this.hideShareModal();
                return;
            }
            TodosState.shareMode = 'list';
            TodosState.shareAction = 'link';
            this.renderShareModal(list);
        });
        document.getElementById('todosSharePrimaryBtn')?.addEventListener('click', async () => {
            if (TodosState.shareMode === 'list') {
                TodosState.shareMode = 'create';
                TodosState.shareAction = 'link';
                this.renderShareModal(list);
                return;
            }
            if (this.getShareAction() === 'invite') {
                await this.sendInvitations();
                return;
            }
            await this.generateShareLink();
        });
        overlay.querySelectorAll('input[name="todosShareType"]').forEach((input) => {
            input.addEventListener('change', () => {
                TodosState.currentShareType = input.value;
                this.renderShareModal(list);
            });
        });
        overlay.querySelectorAll('input[name="todosShareAction"]').forEach((input) => {
            input.addEventListener('change', () => {
                TodosState.shareAction = input.value;
                this.renderShareModal(list);
            });
        });
        this.bindShareLinkActions(list);
        const inviteSearch = document.getElementById('todosInviteUserSearch');
        inviteSearch?.addEventListener('input', (event) => {
            this.filterInviteUsers(event.target.value);
            if (TodosState.selectedUserIds.length) this.clearInviteSelectionError();
        });
        if (TodosState.shareAction === 'invite') {
            if (TodosState.publicUsersLoaded) {
                this.filterInviteUsers(inviteSearch?.value || '');
            } else {
                void this.loadPublicUsers();
            }
        }
    },

    async loadPublicUsers() {
        const userList = document.getElementById('todosInviteUserList');
        if (!userList || TodosState.publicUsersLoading || TodosState.publicUsersLoaded) return;
        TodosState.publicUsersLoading = true;
        userList.innerHTML = `<div class="cs-invite-state">${TodosRender.escapeHtml(todosT('todos_share_loading_users', 'Loading users...'))}</div>`;
        try {
            TodosState.publicUsers = await TodosAPI.fetchPublicUsers();
            TodosState.publicUsersLoaded = true;
            this.filterInviteUsers('');
        } catch (error) {
            console.error('Failed to load public users:', error);
            userList.innerHTML = `<div class="cs-invite-state">${TodosRender.escapeHtml(todosT('todos_share_load_users_failed', 'Failed to load users.'))}</div>`;
        } finally {
            TodosState.publicUsersLoading = false;
        }
    },

    renderInviteUserList(users) {
        const userList = document.getElementById('todosInviteUserList');
        if (!userList) return;
        if (!users || users.length === 0) {
            userList.innerHTML = `<div class="cs-invite-state">${TodosRender.escapeHtml(todosT('todos_share_no_users_available', 'No users available to invite.'))}</div>`;
            return;
        }

        userList.innerHTML = users.map((user) => {
            const isSelected = TodosState.selectedUserIds.includes(user.id);
            const initials = this.getUserInitials(user);
            return `
                <button type="button" class="cs-invite-user-item ${isSelected ? 'is-selected' : ''}" data-user-id="${TodosRender.escapeHtml(user.id)}">
                    <span class="cs-invite-avatar">${TodosRender.escapeHtml(initials)}</span>
                    <span class="cs-invite-user-info">
                        <span class="cs-invite-user-name">${TodosRender.escapeHtml(user.display_name)}</span>
                    </span>
                    <span class="cs-invite-check" aria-hidden="true">${Icons.check}</span>
                </button>
            `;
        }).join('');

        userList.querySelectorAll('.cs-invite-user-item').forEach((item) => {
            item.addEventListener('click', () => this.toggleUserSelection(item.dataset.userId));
        });
    },

    getUserInitials(user) {
        if (user.first_name && user.last_name) return (user.first_name[0] + user.last_name[0]).toUpperCase();
        if (user.first_name) return user.first_name.substring(0, 2).toUpperCase();
        if (user.display_name) return user.display_name.substring(0, 2).toUpperCase();
        return '??';
    },

    showInviteSelectionError() {
        const input = document.getElementById('todosInviteUserSearch');
        const error = document.getElementById('todosInviteUserError');
        const message = todosT('chat_share_invite_select_error', 'Select at least one user to invite.');
        if (window.FormValidation?.showInputError) {
            window.FormValidation.showInputError(input, error, message, {
                inputErrorClass: 'cs-input-error',
                errorVisibleClass: null,
            });
            return;
        }
        if (error) {
            error.textContent = message;
            error.hidden = false;
        }
        input?.classList.add('cs-input-error');
        input?.setAttribute('aria-invalid', 'true');
        input?.focus();
    },

    clearInviteSelectionError() {
        const input = document.getElementById('todosInviteUserSearch');
        const error = document.getElementById('todosInviteUserError');
        if (window.FormValidation?.clearInputError) {
            window.FormValidation.clearInputError(input, error, {
                inputErrorClass: 'cs-input-error',
                errorVisibleClass: null,
            });
            return;
        }
        if (error) {
            error.hidden = true;
            error.textContent = '';
        }
        input?.classList.remove('cs-input-error');
        input?.setAttribute('aria-invalid', 'false');
    },

    toggleUserSelection(userId) {
        const idx = TodosState.selectedUserIds.indexOf(userId);
        if (idx >= 0) TodosState.selectedUserIds.splice(idx, 1);
        else TodosState.selectedUserIds.push(userId);
        if (TodosState.selectedUserIds.length) this.clearInviteSelectionError();
        this.updateSelectedUsersUI();
    },

    updateSelectedUsersUI() {
        const selectedSection = document.getElementById('todosSelectedUsers');
        const selectedList = document.getElementById('todosSelectedUsersList');
        const selectedCount = document.getElementById('todosSelectedCount');

        document.querySelectorAll('#todosInviteUserList .cs-invite-user-item').forEach((item) => {
            item.classList.toggle('is-selected', TodosState.selectedUserIds.includes(item.dataset.userId));
        });

        if (!TodosState.selectedUserIds.length) {
            if (selectedSection) selectedSection.hidden = true;
            if (selectedList) selectedList.innerHTML = '';
            if (selectedCount) selectedCount.textContent = '0';
            return;
        }

        if (selectedSection) selectedSection.hidden = false;
        if (selectedCount) selectedCount.textContent = String(TodosState.selectedUserIds.length);
        const selectedUsers = TodosState.publicUsers.filter((user) => TodosState.selectedUserIds.includes(user.id));
        if (selectedList) {
            selectedList.innerHTML = selectedUsers.map((user) => `
                <span class="cs-invite-selected-chip">
                    <span>${TodosRender.escapeHtml(user.display_name)}</span>
                    <button type="button" data-user-id="${TodosRender.escapeHtml(user.id)}" aria-label="${TodosRender.escapeHtml(todosT('todos_share_remove_user_aria', 'Remove user'))}">${Icons.close}</button>
                </span>
            `).join('');
            selectedList.querySelectorAll('button[data-user-id]').forEach((btn) => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    this.toggleUserSelection(btn.dataset.userId);
                });
            });
        }
    },

    filterInviteUsers(searchTerm) {
        const term = String(searchTerm || '').toLowerCase().trim();
        const filtered = term
            ? TodosState.publicUsers.filter((user) =>
                (user.display_name && user.display_name.toLowerCase().includes(term)) ||
                false)
            : TodosState.publicUsers;
        this.renderInviteUserList(filtered);
        this.updateSelectedUsersUI();
    },

    async sendInvitations() {
        const listId = TodosState.sharingListId;
        if (!listId) return;
        if (TodosState.selectedUserIds.length === 0) {
            this.showInviteSelectionError();
            return;
        }

        const btn = document.getElementById('todosSharePrimaryBtn');
        if (btn) btn.disabled = true;
        try {
            const result = await TodosAPI.inviteUsersToList(listId, TodosState.selectedUserIds, this.getShareTypeSelection());
            if (typeof showNotification === 'function') {
                showNotification(result.message || todosTf('todos_share_invited_fallback', 'Invited {count} user(s)', {
                    count: result.invited_count || TodosState.selectedUserIds.length,
                }), 'success');
            }
            TodosState.selectedUserIds = [];
            TodosState.shareMode = 'list';
            this.renderShareModal(TodosState.lists.find((item) => item.id === listId));
        } catch (error) {
            console.error('Failed to send invitations:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_share_invite_failed', 'Failed to send invitations'), 'error');
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    async loadShareStatus(listId) {
        try {
            TodosState.shareStatus = await TodosAPI.getShareStatus(listId);
            const idx = TodosState.lists.findIndex((item) => item.id === listId);
            if (idx >= 0) {
                TodosState.lists[idx].clone_share_id = TodosState.shareStatus.clone_share_id;
                TodosState.lists[idx].live_share_id = TodosState.shareStatus.live_share_id;
                TodosState.lists[idx].collaborate_share_id = TodosState.shareStatus.collaborate_share_id;
            }
            this.renderShareModal(TodosState.lists.find((item) => item.id === listId));
        } catch (error) {
            console.error('Failed to load share status:', error);
        }
    },

    renderShareLinkCard(share) {
        const shareUrl = `${window.location.origin}/todos/${share.type}/${share.id}`;
        const subscriberChip = share.count ? `<span class="cs-chip cs-chip-muted">${TodosRender.escapeHtml(todosTf(share.count === 1 ? 'todos_share_subscriber_count_one' : 'todos_share_subscriber_count_other', share.count === 1 ? '{count} subscriber' : '{count} subscribers', { count: share.count }))}</span>` : '';
        return `
            <div class="cs-link-card" data-share-type="${TodosRender.escapeHtml(share.type)}" data-share-url="${TodosRender.escapeHtml(shareUrl)}">
                <div class="cs-link-url-row"><input type="text" class="cs-link-url" value="${TodosRender.escapeHtml(shareUrl)}" readonly aria-label="${TodosRender.escapeHtml(todosT('todos_share_link_aria', 'Todo share link'))}"></div>
                <div class="cs-link-meta"><span class="cs-chip">${TodosRender.escapeHtml(this.getShareTypeLabel(share.type))}</span>${subscriberChip}</div>
                <div class="cs-link-actions">
                    <button type="button" class="om-button border cancel" data-action="copy">${Icons.copy}${TodosRender.escapeHtml(todosT('todos_share_copy_action', 'Copy'))}</button>
                    <button type="button" class="om-button border cancel" data-action="open">${Icons.open_window}${TodosRender.escapeHtml(todosT('todos_share_open_action', 'Open'))}</button>
                    <button type="button" class="om-button border cancel" data-action="edit">${Icons.create}${TodosRender.escapeHtml(todosT('todos_share_edit_action', 'Edit'))}</button>
                    <button type="button" class="om-button border danger-nofill" data-action="delete">${Icons.trash}${TodosRender.escapeHtml(todosT('todos_share_delete_action', 'Delete'))}</button>
                </div>
            </div>
        `;
    },

    bindShareLinkActions(list) {
        const overlay = document.getElementById('todosShareOverlay');
        if (!overlay) return;
        overlay.querySelectorAll('.cs-link-card').forEach((card) => {
            const shareType = card.dataset.shareType;
            const shareUrl = card.dataset.shareUrl;
            card.querySelector('[data-action="copy"]')?.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(shareUrl);
                    if (typeof showNotification === 'function') showNotification(todosT('todos_share_copied', 'Copied!'), 'success');
                } catch (error) {
                    console.error('Copy failed:', error);
                }
            });
            card.querySelector('[data-action="open"]')?.addEventListener('click', () => {
                if (shareUrl) window.open(shareUrl, '_blank', 'noopener,noreferrer');
            });
            card.querySelector('[data-action="edit"]')?.addEventListener('click', () => {
                TodosState.shareMode = 'create';
                TodosState.shareAction = 'link';
                TodosState.currentShareType = shareType;
                this.renderShareModal(list);
            });
            card.querySelector('[data-action="delete"]')?.addEventListener('click', async () => {
                await this.stopSharingByType(shareType);
            });
        });
    },

    async generateShareLink() {
        const listId = TodosState.sharingListId;
        if (!listId) return;

        const btn = document.getElementById('todosSharePrimaryBtn');
        if (btn) btn.disabled = true;
        try {
            const shareData = await TodosAPI.shareList(listId, this.getShareTypeSelection());
            const rawShareUrl = typeof shareData.share_url === 'string' ? shareData.share_url.trim() : '';
            let shareUrl = rawShareUrl;
            if (!/^https?:\/\//i.test(rawShareUrl)) {
                const path = rawShareUrl
                    ? (rawShareUrl.startsWith('/') ? rawShareUrl : `/${rawShareUrl}`)
                    : '/share';
                shareUrl = `${window.location.origin}${path}`;
            }
            try {
                await navigator.clipboard.writeText(shareUrl);
            } catch (_) {
                // ignore clipboard failure
            }
            await this.loadShareStatus(listId);
            this.renderSidebarLists();
            TodosState.shareMode = 'list';
            this.renderShareModal(TodosState.lists.find((item) => item.id === listId));
        } catch (error) {
            console.error('Failed to generate share link:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_share_generate_failed', 'Failed to generate share link'), 'error');
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    hideShareModal() {
        const overlay = document.getElementById('todosShareOverlay');
        if (overlay) {
            overlay.classList.remove('cs-active');
            setTimeout(() => overlay.setAttribute('hidden', ''), 200);
        }
        TodosState.sharingListId = null;
        TodosState.shareMode = 'list';
    },

    async stopSharingByType(shareType) {
        const listId = TodosState.sharingListId;
        if (!listId) return;

        try {
            await TodosAPI.deleteShare(listId, shareType);
            await this.loadShareStatus(listId);
            this.renderSidebarLists();
            
            if (typeof showNotification === 'function') {
                showNotification(todosTf('todos_share_stopped', '{type} sharing stopped', {
                    type: this.getShareTypeLabel(shareType),
                }), 'success');
            }
        } catch (error) {
            console.error('Failed to stop sharing:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_share_stop_failed', 'Failed to stop sharing'), 'error');
            }
        }
    },

    async handleUnsubscribe(listId) {
        const list = TodosState.lists.find(l => l.id === listId);
        if (!list) return;

        if (!await window.showDeleteConfirm({
            title: todosT('common_remove_confirm_title', 'Remove item?'),
            message: todosTf('todos_unsubscribe_confirm', 'Remove "{title}" from your workspace? You can add it back later using the share link.', {
                title: list.title,
            }),
            confirmLabel: todosT('todos_remove_from_workspace', 'Remove from workspace'),
        })) {
            return;
        }

        try {
            await TodosAPI.unsubscribeFromList(listId);
            
            // Remove from state
            TodosState.lists = TodosState.lists.filter(l => l.id !== listId);
            
            // If this was the selected list, clear selection
            if (TodosState.selectedListId === listId) {
                TodosState.selectedListId = null;
                TodosState.todos = [];
                if (TodosState.lists.length > 0) {
                    this.selectList(TodosState.lists[0].id);
                } else {
                    this.showNoListsState();
                }
            }
            
            this.renderSidebarLists();
            
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_unsubscribe_success', 'List removed from workspace'), 'success');
            }
        } catch (error) {
            console.error('Failed to unsubscribe:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_unsubscribe_remove_failed', 'Failed to remove list'), 'error');
            }
        }
    },

    // ============================================================================
    // Accept Shared List Methods
    // ============================================================================

    async showAcceptModal(shareId, shareType = null) {
        TodosState.pendingShareId = shareId;
        TodosState.pendingShareType = shareType;
        
        const overlay = document.getElementById('todoAcceptOverlay');
        if (!overlay) return;

        const titleEl = document.getElementById('todoAcceptTitle');
        const ownerEl = document.getElementById('todoAcceptOwner');
        const previewEl = document.getElementById('todoAcceptPreviewContent');
        const confirmBtn = document.getElementById('todoAcceptConfirmBtn');
        const shareTypeInfoEl = document.getElementById('todoAcceptShareTypeInfo');

        // Reset and show loading state
        titleEl.textContent = todosT('todos_accept_loading', 'Loading...');
        ownerEl.textContent = '';
        previewEl.innerHTML = '';
        if (shareTypeInfoEl) shareTypeInfoEl.innerHTML = '';
        confirmBtn.disabled = true;

        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('cs-active'));

        // Setup event listeners (only once)
        if (!TodosState.acceptModalInitialized) {
            document.getElementById('todoAcceptCancelBtn')?.addEventListener('click', () => this.hideAcceptModal());
            document.getElementById('todoAcceptCloseBtn')?.addEventListener('click', () => this.hideAcceptModal());
            document.getElementById('todoAcceptConfirmBtn')?.addEventListener('click', () => this.confirmAcceptShared());
            overlay.addEventListener('click', (e) => { if (e.target === overlay) this.hideAcceptModal(); });
            TodosState.acceptModalInitialized = true;
        }

        // Fetch preview data
        try {
            const data = await TodosAPI.getSharedListPreview(shareId);
            titleEl.textContent = data.title || todosT('todos_accept_untitled', 'Untitled List');
            ownerEl.textContent = data.owner_name ? todosTf('todos_shared_by', 'Shared by {owner}', { owner: data.owner_name }) : '';
            
            // Store detected share type
            TodosState.pendingShareType = data.share_type || shareType;
            
            // Show share type info
            if (shareTypeInfoEl) {
                const typeLabels = {
                    'clone': { label: todosT('todos_share_type_clone_label', 'Clone'), desc: todosT('todos_accept_type_clone_desc', 'You\'ll get your own copy that you can edit and delete freely.'), color: '#8b5cf6' },
                    'live': { label: todosT('todos_accept_type_live_label', 'Live View'), desc: todosT('todos_accept_type_live_desc', 'View-only with live updates. You cannot edit this list.'), color: '#3b82f6' },
                    'collaborate': { label: todosT('todos_share_type_collaborate_label', 'Collaborate'), desc: todosT('todos_accept_type_collaborate_desc', 'You can view and possibly edit this list with live sync.'), color: '#10b981' },
                };
                const typeInfo = typeLabels[data.share_type] || typeLabels['live'];
                shareTypeInfoEl.innerHTML = `
                    <div style="background-color: ${typeInfo.color}20; border: 1px solid ${typeInfo.color}40; border-radius: 8px; padding: 10px 12px;">
                        <span style="color: ${typeInfo.color}; font-weight: 600; font-size: 0.85rem;">${TodosRender.escapeHtml(typeInfo.label)}</span>
                        <span style="display: block; font-size: 0.8rem; color: var(--text-color-secondary); margin-top: 2px;">${TodosRender.escapeHtml(typeInfo.desc)}</span>
                    </div>
                `;
            }
            
            // Show preview of todos
            if (data.todos && data.todos.length > 0) {
                previewEl.innerHTML = data.todos.map(t => `
                    <div style="display: flex; align-items: center; gap: 8px; padding: 4px 0;">
                        <span style="width: 14px; height: 14px; border: 2px solid var(--border-color); border-radius: 4px; flex-shrink: 0; ${t.is_done ? 'background: var(--accent-color); border-color: var(--accent-color);' : ''}"></span>
                        <span style="${t.is_done ? 'text-decoration: line-through; opacity: 0.6;' : ''}">${TodosRender.escapeHtml(t.content)}</span>
                    </div>
                `).join('');
                if (data.todo_count > data.todos.length) {
                    previewEl.innerHTML += `<p style="margin: 8px 0 0 0; font-size: 0.8rem; opacity: 0.6;">${TodosRender.escapeHtml(todosTf('todos_accept_more_items', '+{count} more items', { count: data.todo_count - data.todos.length }))}</p>`;
                }
            } else {
                previewEl.innerHTML = `<p style="color: var(--text-color-secondary);">${TodosRender.escapeHtml(todosT('todos_accept_empty_list', 'Empty list'))}</p>`;
            }
            
            // Update button text based on share type
            if (data.share_type === 'clone') {
                confirmBtn.innerHTML = `${Icons.copy} ${TodosRender.escapeHtml(todosT('todos_accept_clone_action', 'Clone to My Lists'))}`;
            } else {
                confirmBtn.innerHTML = `${Icons.plus} ${TodosRender.escapeHtml(todosT('todos_accept_add_action', 'Add to My Lists'))}`;
            }
            
            confirmBtn.disabled = false;
        } catch (error) {
            const isOwnerError = error && error.status === 400;
            const isDuplicateError = error && error.status === 409;
            if (isOwnerError) {
                console.warn('Owner attempted to open own shared todo list');
                this.hideAcceptModal();
                const warnMessage = error?.message || todosT('todos_accept_own_list_error', 'You cannot open your own shared todo list.');
                if (typeof notifyWarning === 'function') {
                    notifyWarning(warnMessage);
                } else if (typeof showNotification === 'function') {
                    showNotification(warnMessage, 'warning');
                }
                if (typeof window !== 'undefined') {
                    const path = window.location.pathname;
                    const isSharePath = /\/todos\/(clone|live|collaborate)\//.test(path);
                    if (isSharePath) {
                        history.replaceState(null, '', '/workspace/todos');
                    }
                }
                return;
            } else if (isDuplicateError) {
                console.warn('User attempted to re-add an existing shared todo list');
                this.hideAcceptModal();
                const errorMessage = error?.message || todosT('todos_accept_duplicate_error', 'You already added this shared todo list.');
                if (typeof notifyError === 'function') {
                    notifyError(errorMessage);
                } else if (typeof showNotification === 'function') {
                    showNotification(errorMessage, 'error');
                }
                if (typeof window !== 'undefined') {
                    const path = window.location.pathname;
                    const isSharePath = /\/todos\/(clone|live|collaborate)\//.test(path);
                    if (isSharePath) {
                        history.replaceState(null, '', '/workspace/todos');
                    }
                }
                return;
            }
            console.error('Failed to load shared list preview:', error);
            titleEl.textContent = todosT('todos_accept_load_error_title', 'Error loading list');
            previewEl.innerHTML = `<p style="color: #ef4444;">${TodosRender.escapeHtml(todosT('todos_accept_load_error_desc', 'Could not load this shared list. It may no longer exist.'))}</p>`;
        }
    },

    hideAcceptModal() {
        const overlay = document.getElementById('todoAcceptOverlay');
        if (overlay) {
            overlay.classList.remove('cs-active');
            setTimeout(() => overlay.setAttribute('hidden', ''), 200);
        }
        TodosState.pendingShareId = null;
        TodosState.pendingShareType = null;
    },

    async confirmAcceptShared() {
        const shareId = TodosState.pendingShareId;
        const shareType = TodosState.pendingShareType;
        if (!shareId) return;

        const confirmBtn = document.getElementById('todoAcceptConfirmBtn');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = `${Icons.aura} ${TodosRender.escapeHtml(todosT('todos_accept_processing', 'Processing...'))}`;
        }

        try {
            let message = '';
            
            if (shareType === 'clone') {
                // Clone the list
                const result = await TodosAPI.cloneList(shareId);
                message = result.message || todosT('todos_accept_clone_success', 'List cloned successfully!');
            } else {
                // Subscribe to the list (live or collaborate)
                const result = await TodosAPI.acceptSharedList(shareId);
                message = result.message || todosT('todos_accept_add_success', 'List added to your workspace!');
            }
            
            this.hideAcceptModal();
            
            // Reload lists to include the new list
            await this.loadLists();
            
            if (typeof showNotification === 'function') {
                showNotification(message, 'success');
            }
            
            // Clear URL if it was a share link
            const path = window.location.pathname;
            if (path.includes('/todos/clone/') || path.includes('/todos/live/') || path.includes('/todos/collaborate/')) {
                history.replaceState(null, '', '/workspace/todos');
            }
        } catch (error) {
            console.error('Failed to accept shared list:', error);
            if (typeof showNotification === 'function') {
                showNotification(todosT('todos_accept_add_failed', 'Failed to add list'), 'error');
            }
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.innerHTML = `${Icons.plus} ${TodosRender.escapeHtml(todosT('todos_accept_add_action', 'Add to My Lists'))}`;
            }
        }
    },

    checkForSharedLink() {
        const path = window.location.pathname;
        
        // Check for new share link formats: /todos/clone/{id}, /todos/live/{id}, /todos/collaborate/{id}
        const cloneMatch = path.match(/\/todos\/clone\/([a-zA-Z0-9-]+)/);
        if (cloneMatch) {
            this.showAcceptModal(cloneMatch[1], 'clone');
            return true;
        }
        
        const liveMatch = path.match(/\/todos\/live\/([a-zA-Z0-9-]+)/);
        if (liveMatch) {
            this.showAcceptModal(liveMatch[1], 'live');
            return true;
        }
        
        const collaborateMatch = path.match(/\/todos\/collaborate\/([a-zA-Z0-9-]+)/);
        if (collaborateMatch) {
            this.showAcceptModal(collaborateMatch[1], 'collaborate');
            return true;
        }
        
        return false;
    },

    show() {
        const workspace = TodosDOM.workspace;
        if (workspace) {
            workspace.style.display = 'flex';
        }

        // Initialize and load data
        this.init();
        this.loadLists();

        const editorRoute = this.getListEditorRoute();
        if (editorRoute?.mode === 'create' && TodosState.listEditorMode !== 'create') {
            this.showCreateListPage(null, { skipHistory: true });
        }

        // Reset to empty state if no list selected
        if (!editorRoute && !TodosState.listEditorMode && !TodosState.selectedListId) {
            const emptyState = TodosDOM.emptyState;
            const listView = TodosDOM.listView;
            if (emptyState) emptyState.style.display = 'flex';
            if (listView) listView.style.display = 'none';
        }
    },

    hide() {
        // Stop auto-refresh when hiding
        this.stopAutoRefresh();
        
        const workspace = TodosDOM.workspace;
        if (workspace) {
            workspace.style.display = 'none';
        }
    },

    ensureWorkspaceVisible() {
        if (typeof showWorkspaceContainer === 'function') {
            showWorkspaceContainer({ tab: 'todo' });
            return;
        }

        if (typeof WorkspaceManager !== 'undefined') {
            WorkspaceManager.setActiveTab?.('todo');
            WorkspaceManager.show?.();
            WorkspaceManager.switchToTab?.('todo');
        }
    },

    // ============================================================================
    // Auto-Refresh for Shared Lists
    // ============================================================================

    isSubscribedList(listId) {
        const list = TodosState.lists.find(l => l.id === listId);
        return list && list.is_subscribed === true;
    },

    startAutoRefresh(listId) {
        this.stopAutoRefresh();
        
        if (!this.isSubscribedList(listId)) return;
        
        // Generate initial hash of current todos
        TodosState.lastTodosHash = this.generateTodosHash(TodosState.todos);
        
        TodosState.refreshInterval = setInterval(() => {
            this.refreshSharedListContent(listId);
        }, TodosState.refreshIntervalMs);
    },

    stopAutoRefresh() {
        if (TodosState.refreshInterval) {
            clearInterval(TodosState.refreshInterval);
            TodosState.refreshInterval = null;
        }
        TodosState.lastTodosHash = null;
    },

    generateTodosHash(todos) {
        if (!todos || todos.length === 0) return '';
        return todos.map(t => `${t.id}:${t.content}:${t.is_done}:${t.notes || ''}:${t.order}:${t.updated_at || ''}`).join('|');
    },

    isUserCurrentlyEditing() {
        // Check if any input in the todos area is focused
        const addInput = TodosDOM.addInput;
        const addNotes = TodosDOM.addNotes;
        const workspace = TodosDOM.workspace;
        
        if (document.activeElement === addInput) return true;
        if (document.activeElement === addNotes) return true;
        
        // Check if any todo-related input/textarea is focused
        if (workspace && workspace.contains(document.activeElement)) {
            const tagName = document.activeElement.tagName.toLowerCase();
            if (tagName === 'input' || tagName === 'textarea') return true;
        }
        
        return TodosState.isUserEditing;
    },

    async refreshSharedListContent(listId) {
        // Don't refresh if user is editing or if list changed
        if (TodosState.selectedListId !== listId) {
            this.stopAutoRefresh();
            return;
        }
        
        if (this.isUserCurrentlyEditing()) {
            return;
        }
        
        if (TodosState.isLoadingTodos) {
            return;
        }
        
        try {
            const freshPage = await TodosAPI.fetchTodos(
                listId,
                { view: TodosState.activeView, sort: TodosState.sortBy },
            );
            const freshTodos = freshPage.items;
            const newHash = this.generateTodosHash(freshTodos);
            const currentFirstPageHash = this.generateTodosHash(TodosState.todos.slice(0, freshTodos.length));
            
            // Only update if content has changed
            if (newHash !== currentFirstPageHash) {
                const freshIds = new Set(freshTodos.map(todo => String(todo.id)));
                const loadedTail = TodosState.todos.filter(todo => !freshIds.has(String(todo.id)));
                TodosState.todos = [...freshTodos, ...loadedTail];
                TodosState.todosOffset = TodosState.todos.length;
                TodosState.todosHasMore = freshPage.hasMore;
                TodosState.lastTodosHash = this.generateTodosHash(TodosState.todos);
                this.renderTodos();
            }
        } catch (error) {
            console.warn('Failed to refresh shared list:', error);
        }
    },

    setUserEditing(editing) {
        TodosState.isUserEditing = editing;
    },
};

// ============================================================================
// Integration with Workspace Manager
// ============================================================================

const initializeTodosModule = () => {
    // Check for shared link on page load
    TodosManager.checkForSharedLink();
    
    if (typeof WorkspaceManager === 'undefined') return;

    const originalSwitchToTab = WorkspaceManager.switchToTab.bind(WorkspaceManager);
    const handleTabChange = (tabId) => {
        if (tabId === 'todo') {
            TodosManager.show();
        } else {
            TodosManager.hide();
        }
    };

    WorkspaceManager.switchToTab = function(tabId) {
      originalSwitchToTab(tabId);
      // The original switch may defer navigation while the Todo editor asks
      // whether unsaved changes should be discarded. Reflect the tab that is
      // actually active, rather than hiding Todo for a request that has not
      // been approved yet. This also respects any permission-based fallback
      // applied by WorkspaceManager.
      handleTabChange(WorkspaceState.activeTab);
    };

    const initialTab = (typeof WorkspaceState !== 'undefined' && WorkspaceState.activeTab) || null;
    const isTodoUrl = typeof window !== 'undefined' && window.location.pathname.startsWith('/workspace/todo');
    if (initialTab === 'todo' || isTodoUrl) {
        handleTabChange('todo');
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeTodosModule);
} else {
    initializeTodosModule();
}

// Expose to window
if (typeof window !== 'undefined') {
    window.TodosManager = TodosManager;
    window.TodosState = TodosState;
}
