const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const TODO_ICON_OPTIONS = {
    checklist: '<svg><path d="checklist"/></svg>',
    list: '<svg><path d="list"/></svg>',
    shopping_cart: '<svg><path d="cart"/></svg>',
};

const TODO_SORT_OPTIONS = [
    { id: 'manual', nameKey: 'todos_sort_manual', name: 'Manual', icon: TODO_ICON_OPTIONS.list },
];

function loadTodosModule() {
    const commonDir = path.join(__dirname, '..', 'common');
    const workspaceIconsSource = fs.readFileSync(path.join(commonDir, 'workspaceIcons.js'), 'utf8');
    const todosSource = fs.readFileSync(path.join(__dirname, 'todos.js'), 'utf8');
    const context = {
        console,
        CSS: { escape: (value) => String(value) },
        document: {
            readyState: 'loading',
            addEventListener() {},
            getElementById() { return null; },
        },
        Icons: {
            ...TODO_ICON_OPTIONS,
            workspaceIconPickerOptions: Object.entries(TODO_ICON_OPTIONS).map(([id, svg]) => ({ id, svg })),
            todoIconOptions: TODO_ICON_OPTIONS,
            todoSortOptions: TODO_SORT_OPTIONS,
            filter: '<svg><path d="filter"/></svg>',
            calendar: '<svg><path d="calendar"/></svg>',
            google_calendar: '<svg><path d="week"/></svg>',
            arrow_top_right: '<svg><path d="upcoming"/></svg>',
            clock: '<svg><path d="clock"/></svg>',
            exclamation: '<svg><path d="priority"/></svg>',
            flag: '<svg><path d="flag"/></svg>',
            tag: '<svg><path d="tag"/></svg>',
            check: '<svg><path d="check"/></svg>',
            chevron: '<svg class="caret"><path d="chevron"/></svg>',
            pause: '<svg><path d="board"/></svg>',
            chevronLeft: '<svg><path d="back"/></svg>',
        },
        workspaceIconPickerOptions: Object.entries(TODO_ICON_OPTIONS).map(([id, svg]) => ({ id, svg })),
        todoIconOptions: TODO_ICON_OPTIONS,
        todoSortOptions: TODO_SORT_OPTIONS,
        window: {
            location: {
                origin: 'http://localhost',
                pathname: '/',
                hash: '',
            },
        },
    };
    context.window.Icons = context.Icons;
    context.window.workspaceIconPickerOptions = context.workspaceIconPickerOptions;
    context.globalThis = context;

    vm.runInNewContext(workspaceIconsSource, context, { filename: 'workspaceIcons.js' });
    vm.runInNewContext(
        `${todosSource}\nwindow.__TodosRender = TodosRender; window.__TodosAPI = TodosAPI; window.__TodoDueDateUtils = { todoDueControlValues, todoDueApiValue, syncTodoDueInputMode, validateTodoDueControls, todoDueDisplayDate };`,
        context,
        { filename: 'todos.js' },
    );

    return context.window;
}

test('todo icon picker serializes SVG presets as compact ids', () => {
    const windowContext = loadTodosModule();
    const { TodosManager, TodosState } = windowContext;

    TodosState.iconPicker.selectedType = 'preset';
    TodosState.iconPicker.selectedIconId = 'shopping_cart';
    TodosState.iconPicker.selectedColorIndex = 0;

    const payload = TodosManager.serializeIconPicker('create');
    const parsed = JSON.parse(payload);

    assert.deepEqual(parsed, { preset: 'shopping_cart', color: '#E53935' });
    assert.equal(payload.includes('<svg'), false);
    assert.ok(payload.length < 255);
});

test('todo icon parser resolves canonical preset formats', () => {
    const windowContext = loadTodosModule();
    const { __TodosRender: TodosRender } = windowContext;

    assert.equal(
        JSON.stringify(TodosRender.parseIcon('{"preset":"list","color":"#1E88E5"}')),
        JSON.stringify({ type: 'preset', iconId: 'list', svg: TODO_ICON_OPTIONS.list, color: '#1E88E5' }),
    );

    assert.equal(
        JSON.stringify(TodosRender.parseIcon('shopping_cart')),
        JSON.stringify({ type: 'preset', iconId: 'shopping_cart', svg: TODO_ICON_OPTIONS.shopping_cart, color: '#E53935' }),
    );

});

test('todo list create and edit use a routed page instead of CRUD modals', () => {
    const todosCss = fs.readFileSync(
        path.join(__dirname, '..', '..', 'css', 'chat', 'todos.css'),
        'utf8',
    );
    const todosSource = fs.readFileSync(path.join(__dirname, 'todos.js'), 'utf8');
    const modalSource = fs.readFileSync(path.join(__dirname, 'deleteWarningModals.js'), 'utf8');
    const indexMarkup = fs.readFileSync(path.join(__dirname, '..', '..', 'index.html'), 'utf8');

    assert.match(indexMarkup, /id="todosListEditorPage"[^>]*hidden[^>]*aria-hidden="true"/);
    assert.match(todosSource, /showCreateListPage\(trigger = null, options = \{\}\)/);
    assert.match(todosSource, /showEditListPage\(listId, trigger = null, options = \{\}\)/);
    assert.match(todosSource, /\/workspace\/todo\/lists\/\$\{encodeURIComponent\(listId\)\}\/edit/);
    assert.doesNotMatch(todosSource, /id="todosListEditorBackBtn"/);
    assert.match(todosSource, /class="om-button border cancel" id="todosListEditorCancelBtn"/);
    assert.match(todosSource, /type="submit" class="om-button border submit"/);
    assert.match(todosCss, /\.todos-list-editor-page\s*\{[^}]*overflow-y:\s*auto;/s);
    assert.match(todosCss, /\.todos-list-editor-page \.todos-icon-picker-dropdown\s*\{[^}]*overflow-y:\s*auto;/s);
    assert.doesNotMatch(modalSource, /todosCreateListOverlay/);
    assert.doesNotMatch(todosSource, /todosEditListOverlay/);
});

test('todo list editor protects unsaved changes and supports browser history', () => {
    const todosSource = fs.readFileSync(path.join(__dirname, 'todos.js'), 'utf8');
    const workspaceSource = fs.readFileSync(path.join(__dirname, 'workspace.js'), 'utf8');
    const scriptSource = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');

    assert.match(todosSource, /id: 'workspace-todos-list-editor-unsaved'/);
    assert.match(todosSource, /window\.addEventListener\('popstate', this\._listEditorPopstateHandler\)/);
    assert.match(
        todosSource,
        /WorkspaceManager\.switchToTab = function\(tabId\)[\s\S]*handleTabChange\(WorkspaceState\.activeTab\)/,
    );
    assert.doesNotMatch(
        todosSource,
        /WorkspaceManager\.switchToTab = function\(tabId\)\s*\{\s*originalSwitchToTab\(tabId\);\s*handleTabChange\(tabId\)/,
    );
    assert.match(workspaceSource, /isTodoListEditorRoute/);
    assert.match(scriptSource, /workspace\\\/todo\\\/lists/);
});

test('todo rows render only the completion checkbox', () => {
    const windowContext = loadTodosModule();
    const { __TodosRender: TodosRender } = windowContext;
    const todoHtml = TodosRender.todoItem({
        id: 'todo-1',
        todo_list: 'list-1',
        content: 'Keep the row focused on task completion',
        is_done: false,
        is_marked: false,
        status: 'todo',
    });

    assert.equal((todoHtml.match(/type="checkbox"/g) || []).length, 1);
    assert.doesNotMatch(todoHtml, /class="todo-select-input"/);
    assert.match(todoHtml, /class="todo-checkbox/);
});

test('todo all-day due dates round-trip as timezone-stable calendar dates', () => {
    const windowContext = loadTodosModule();
    const { todoDueControlValues, todoDueApiValue, todoDueDisplayDate } = windowContext.__TodoDueDateUtils;

    assert.equal(todoDueApiValue('2026-08-22', '', true), '2026-08-22T00:00:00.000Z');
    assert.deepEqual(
        JSON.parse(JSON.stringify(todoDueControlValues('2026-08-22T00:00:00Z', true))),
        { date: '2026-08-22', time: '' },
    );

    const displayDate = todoDueDisplayDate('2026-08-22T00:00:00Z', true);
    assert.equal(displayDate.getFullYear(), 2026);
    assert.equal(displayDate.getMonth(), 7);
    assert.equal(displayDate.getDate(), 22);
});

test('todo all-day toggle hides time without discarding either due-date field', () => {
    const windowContext = loadTodosModule();
    const { syncTodoDueInputMode } = windowContext.__TodoDueDateUtils;
    const dateInput = { value: '2026-08-22' };
    const timeInput = { value: '14:30', hidden: false, disabled: false };

    syncTodoDueInputMode(timeInput, true);
    assert.deepEqual(timeInput, { value: '14:30', hidden: true, disabled: true });
    assert.equal(dateInput.value, '2026-08-22');

    syncTodoDueInputMode(timeInput, false);
    assert.deepEqual(timeInput, { value: '14:30', hidden: false, disabled: false });
});

test('todo timed due dates cannot save without both date and time', () => {
    const windowContext = loadTodosModule();
    const { validateTodoDueControls } = windowContext.__TodoDueDateUtils;
    let reported = false;
    const dateInput = { value: '2026-08-22' };
    const timeInput = {
        value: '',
        focus() {},
        reportValidity() { reported = true; },
    };

    assert.equal(validateTodoDueControls(dateInput, timeInput, false), false);
    assert.equal(reported, true);
    assert.equal(timeInput.required, false);
    assert.equal(validateTodoDueControls(dateInput, timeInput, true), true);
});

test('task detail save sends the selected all-day date instead of clearing it', async () => {
    const windowContext = loadTodosModule();
    const { TodosManager, TodosState, __TodosAPI: TodosAPI } = windowContext;
    const todo = {
        id: 'todo-1',
        todo_list: 'list-1',
        content: 'Schedule me',
        share_type: null,
    };
    TodosState.todos = [todo];

    const fields = {
        '#todosTaskContent': { value: 'Schedule me' },
        '#todosTaskNotes': { value: '' },
        '#todosTaskPriority': { value: '0' },
        '#todosTaskStatus': { value: 'todo' },
        '#todosTaskAllDay': { checked: true },
        '#todosTaskTags': { value: '' },
        '#todosTaskSubtasks': { value: '' },
        '#todosTaskLinks': { value: '' },
        '#todosTaskAttachments': { value: '' },
        '#todosTaskDueAt': { value: '2026-08-25' },
        '#todosTaskDueTime': { value: '', disabled: true },
    };
    const saveButton = { disabled: false };
    const modal = { setAttribute() {}, removeAttribute() {} };
    const overlay = {
        hidden: false,
        querySelector(selector) {
            if (selector === '[data-action="save"]') return saveButton;
            if (selector === '.todos-task-details-modal') return modal;
            return fields[selector] || null;
        },
        _closeTodoDetails() {},
    };
    let savedPayload = null;
    TodosAPI.updateTodo = async (_todoId, payload) => {
        savedPayload = payload;
        return { ...todo, ...payload };
    };
    TodosManager.reloadVisibleTodoCollection = async () => {};
    TodosManager.refreshMarkedTodos = async () => {};

    await TodosManager.saveTodoDetails(todo.id, overlay);

    assert.equal(savedPayload.clear_due_at, false);
    assert.equal(savedPayload.all_day, true);
    assert.equal(savedPayload.due_at, '2026-08-25T00:00:00.000Z');
});

test('todo rows and actions use the same fail-closed share-type editability rule', () => {
    const windowContext = loadTodosModule();
    const { TodosManager, __TodosRender: TodosRender } = windowContext;
    const baseTodo = {
        id: 'todo-1',
        todo_list: 'list-1',
        content: 'Respect effective permissions',
        is_done: false,
        is_marked: false,
        status: 'todo',
    };

    const editableHtml = TodosRender.todoItem({
        ...baseTodo,
        share_type: 'collaborate',
        can_delete: true,
    });
    assert.match(editableHtml, /class="todo-edit-btn"/);
    assert.match(editableHtml, /class="todo-delete-btn"/);
    assert.match(editableHtml, /class="todo-mark-btn/);
    assert.equal(TodosManager.canEditList(null, { ...baseTodo, share_type: 'collaborate' }), true);

    for (const shareType of ['live', 'clone', 'unexpected']) {
        const readOnlyTodo = {
            ...baseTodo,
            share_type: shareType,
            can_delete: false,
        };
        const readOnlyHtml = TodosRender.todoItem(readOnlyTodo);

        assert.match(readOnlyHtml, /type="checkbox"[^>]*disabled/, `${shareType} rows must be read-only`);
        assert.doesNotMatch(
            readOnlyHtml,
            /todo-edit-btn|todo-delete-btn|todo-mark-btn/,
            `${shareType} rows must not render editing controls`,
        );
        assert.equal(
            TodosManager.canEditList(null, readOnlyTodo),
            false,
            `${shareType} todos must reject editing actions`,
        );
    }
});

test('individual todo deletion confirms, calls the API, and clears task state', async () => {
    const windowContext = loadTodosModule();
    const { TodosManager, TodosState, __TodosAPI: TodosAPI } = windowContext;
    const todo = {
        id: 'todo-1',
        todo_list: 'list-1',
        content: 'Delete me',
        share_type: 'collaborate',
        can_delete: true,
    };
    TodosState.todos = [todo];
    TodosState.markedTodos = [todo];
    TodosState.searchResults = [todo];
    TodosState.allTodosCache = [todo];

    let deletedId = null;
    windowContext.showDeleteConfirm = async () => true;
    TodosAPI.deleteTodo = async (todoId) => { deletedId = todoId; };
    TodosManager.reloadVisibleTodoCollection = async () => {};
    TodosManager.renderSidebarLists = () => {};

    await TodosManager.deleteTodo(todo.id);

    assert.equal(deletedId, todo.id);
    assert.equal(TodosState.todos.length, 0);
    assert.equal(TodosState.markedTodos.length, 0);
    assert.equal(TodosState.searchResults.length, 0);
    assert.equal(TodosState.allTodosCache.length, 0);
});

test('visible collection reloads merge the first page without dropping loaded tails', async () => {
    const windowContext = loadTodosModule();
    const { TodosManager, TodosState, __TodosAPI: TodosAPI } = windowContext;
    TodosManager.renderSearchResults = () => {};
    TodosManager.renderMarkedTodos = () => {};
    TodosManager.renderSidebarLists = () => {};
    TodosManager.renderTodos = () => {};

    TodosState.isSearching = true;
    TodosState.searchQuery = 'task';
    TodosState.searchRequestToken = Symbol('search');
    TodosState.searchResults = [{ id: 'old-first' }, { id: 'loaded-tail' }];
    TodosAPI.searchTodos = async () => ({ items: [{ id: 'fresh-first' }], hasMore: true });
    await TodosManager.reloadVisibleTodoCollection();
    assert.deepEqual(Array.from(TodosState.searchResults, (todo) => todo.id), ['fresh-first', 'old-first', 'loaded-tail']);

    TodosState.isSearching = false;
    TodosState.selectedListId = TodosState.MARKED_LIST_ID;
    TodosState.markedRequestToken = Symbol('marked');
    TodosState.markedTodos = [{ id: 'old-marked' }, { id: 'marked-tail' }];
    TodosAPI.fetchMarkedTodos = async () => ({ items: [{ id: 'fresh-marked' }], hasMore: true });
    await TodosManager.reloadVisibleTodoCollection();
    assert.deepEqual(Array.from(TodosState.markedTodos, (todo) => todo.id), ['fresh-marked', 'old-marked', 'marked-tail']);

    TodosState.selectedListId = 'list-1';
    TodosState.todosRequestToken = Symbol('list');
    TodosState.todos = [{ id: 'old-list-item' }, { id: 'list-tail' }];
    TodosAPI.fetchTodos = async () => ({ items: [{ id: 'fresh-list-item' }], hasMore: true });
    await TodosManager.reloadVisibleTodoCollection();
    assert.deepEqual(Array.from(TodosState.todos, (todo) => todo.id), ['fresh-list-item', 'old-list-item', 'list-tail']);
});

test('todo header renders filtering as a compact menu beside sorting', () => {
    const windowContext = loadTodosModule();
    const { __TodosRender: TodosRender } = windowContext;
    const headerHtml = TodosRender.listHeader({
        id: 'list-1',
        title: 'Work',
        description: '',
        icon: 'checklist',
    });

    assert.match(headerHtml, /id="todosFilterSelector"/);
    assert.match(headerHtml, /id="todosFilterTrigger"[^>]*aria-haspopup="menu"/);
    assert.match(headerHtml, /class="todos-header-option todos-filter-option selected" data-view="all"/);
    assert.match(headerHtml, /id="todosSortSelector"/);
    assert.doesNotMatch(headerHtml, /id="todosViewToolbar"/);
    assert.doesNotMatch(headerHtml, /id="todosBulkToolbar"/);
});

test('empty filtered todo lists explain that the filter has no matches', () => {
    const windowContext = loadTodosModule();
    const { __TodosRender: TodosRender } = windowContext;

    assert.match(TodosRender.emptyListState(true), /No matching tasks/);
    assert.match(TodosRender.emptyListState(true), /Try a different filter\./);
    assert.match(TodosRender.emptyListState(false), /No todos yet/);
});

test('add todo composer is one row with three icon-only metadata popovers', () => {
    const todosSource = fs.readFileSync(path.join(__dirname, 'todos.js'), 'utf8');
    const todosCss = fs.readFileSync(
        path.join(__dirname, '..', '..', 'css', 'chat', 'todos.css'),
        'utf8',
    );
    const indexMarkup = fs.readFileSync(path.join(__dirname, '..', '..', 'index.html'), 'utf8');

    assert.match(indexMarkup, /id="todosAddForm"[\s\S]*id="todosAddScheduleBtn"[\s\S]*id="todosAddPriorityBtn"[\s\S]*id="todosAddTagsBtn"/);
    assert.match(indexMarkup, /id="todosAddSchedulePopover"[^>]*role="dialog"/);
    assert.match(indexMarkup, /type="checkbox" class="form-checkbox" id="todosAddAllDay"/);
    assert.match(indexMarkup, /id="todosAddPriorityPopover"[^>]*role="menu"/);
    assert.match(indexMarkup, /id="todosAddTagsPopover"[^>]*role="dialog"/);
    assert.doesNotMatch(indexMarkup, /id="todosAddCancelBtn"|id="todosAddSubmitBtn"|id="todosAddActions"|id="todosAddMeta"/);
    assert.match(todosSource, /event\.key === 'Enter'[\s\S]*this\.handleAddTodo\(\{ blur: false \}\)/);
    assert.match(todosSource, /!form\.contains\(event\.target\)[\s\S]*this\.collapseAddTodoComposer\(\)/);
    assert.match(todosSource, /retaining every draft value[\s\S]*collapseAddTodoComposer\(\{ blur = true \} = \{\}\)/);
    assert.match(todosSource, /resetAddTodoComposer\(\{ blur = true \} = \{\}\)[\s\S]*collapseAddTodoComposer\(\{ blur \}\)/);
    assert.match(todosCss, /\.todos-add-form\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*center;/s);
});

test('task detail modal uses shared controls and a responsive scroll region', () => {
    const todosSource = fs.readFileSync(path.join(__dirname, 'todos.js'), 'utf8');
    const todosCss = fs.readFileSync(
        path.join(__dirname, '..', '..', 'css', 'chat', 'todos.css'),
        'utf8',
    );

    assert.match(todosSource, /upgradePrioritySelect\(prioritySelect, 'todos-task-priority-select'\)/);
    assert.match(todosSource, /'todos-task-status-select',[\s\S]*todosT\('todos_status_label'/);
    assert.match(todosSource, /window\.upgradeAdminSingleSelect\(select,/);
    assert.match(todosSource, /class="form-checkbox" id="todosTaskAllDay"/);
    assert.match(todosSource, /id="todosTaskDueAt" type="date"/);
    assert.match(todosSource, /id="todosTaskDueTime" type="time"/);
    assert.match(todosSource, /class="form-input" id="todosTaskContent"/);
    assert.match(todosSource, /id="todosTaskStatus" aria-labelledby="todosTaskStatusLabel"/);
    assert.match(todosSource, /class="todos-task-details-body shared-modal-body" data-modal-scroll-region/);
    assert.match(todosSource, /todos-task-details-header shared-modal-header shared-modal-header--main/);
    assert.match(todosSource, /todos-task-details-close shared-modal-close/);
    assert.match(todosSource, /event\.key !== 'Tab'[\s\S]*focusable\[0\][\s\S]*focusable\[focusable\.length - 1\]/);
    assert.match(todosSource, /overlay\._closeTodoDetails = closeModal/);
    assert.match(todosSource, /modal\?\.setAttribute\('aria-busy', 'true'\)/);
    assert.match(todosCss, /\.delete-warning-card\.workspace-crud-card\.todos-task-details-modal\s*\{[^}]*--shared-modal-width:\s*820px;/s);
    assert.match(todosCss, /\.todos-task-details-body\s*\{[^}]*display:\s*grid;[^}]*gap:\s*18px;/s);
    assert.match(todosCss, /\.todos-task-details-extras\s*\{[^}]*grid-template-columns:\s*repeat\(3,/s);
    assert.match(todosCss, /\.todos-task-priority-select \.admin-select-trigger/);
    assert.match(todosCss, /\.todos-task-status-select \.admin-select-trigger/);
});
