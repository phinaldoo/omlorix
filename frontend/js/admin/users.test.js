const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeFragment {
    constructor() {
        this.children = [];
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }
}

class FakeElement {
    constructor(tagName) {
        this.tagName = String(tagName || '').toUpperCase();
        this.children = [];
        this.attributes = {};
        this.dataset = {};
        this.listeners = {};
        this.parentNode = null;
        this._className = '';
        this._textContent = '';
        this._innerHTML = '';
        this.classList = {
            add: (...names) => this.setClasses([...this.classes(), ...names]),
            remove: (...names) => this.setClasses(this.classes().filter((name) => !names.includes(name))),
            contains: (name) => this.classes().includes(name),
        };
    }

    classes() {
        return String(this._className || '').split(/\s+/).filter(Boolean);
    }

    setClasses(names) {
        this._className = [...new Set(names.filter(Boolean))].join(' ');
        this.attributes.class = this._className;
    }

    set className(value) {
        this.setClasses(String(value || '').split(/\s+/));
    }

    get className() {
        return this._className;
    }

    set textContent(value) {
        this._textContent = String(value ?? '');
        this.children = [];
        this._innerHTML = '';
    }

    get textContent() {
        return this._textContent;
    }

    set innerHTML(value) {
        this._innerHTML = String(value ?? '');
        this.children = [];
        this._textContent = '';
    }

    get innerHTML() {
        return this._innerHTML;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return this.attributes[name];
    }

    appendChild(child) {
        if (child instanceof FakeFragment) {
            child.children.forEach((fragmentChild) => this.appendChild(fragmentChild));
            return child;
        }
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    append(...children) {
        children.forEach((child) => this.appendChild(child));
    }

    addEventListener(type, handler) {
        this.listeners[type] = handler;
    }

    focus() {
        this.focused = true;
    }

    matches(selector) {
        return selector.startsWith('.') && this.classes().includes(selector.slice(1));
    }

    closest(selector) {
        let current = this;
        while (current) {
            if (current.matches?.(selector)) {
                return current;
            }
            current = current.parentNode;
        }
        return null;
    }

    querySelector(selector) {
        for (const child of this.children) {
            if (child.matches?.(selector)) {
                return child;
            }
            const nested = child.querySelector?.(selector);
            if (nested) {
                return nested;
            }
        }
        return null;
    }
}

function findByClass(root, className) {
    if (!root) {
        return null;
    }
    if (root.classes?.().includes(className)) {
        return root;
    }
    for (const child of root.children || []) {
        const match = findByClass(child, className);
        if (match) {
            return match;
        }
    }
    return null;
}

function createHarness({ includeExportButton = true, locale = 'en' } = {}) {
    const listContainer = new FakeElement('div');
    const openedUsers = [];
    const inertElements = new Map();
    const transferReasonDescription = new FakeElement('p');
    const deleteModalElements = new Map([
        ['deleteUserOverlay', new FakeElement('div')],
        ['deleteUserMessage', new FakeElement('p')],
        ['deleteUserCancelButton', new FakeElement('button')],
        ['deleteUserPrimaryButton', new FakeElement('button')],
        ['deleteUserPrimaryText', new FakeElement('span')],
    ]);
    deleteModalElements.get('deleteUserOverlay').hidden = true;
    const document = {
        documentElement: {
            getAttribute() {
                return locale;
            },
        },
        querySelector(selector) {
            return selector === '#page-users .user-table-container' ? listContainer : null;
        },
        getElementById(id) {
            if (deleteModalElements.has(id)) {
                return deleteModalElements.get(id);
            }
            if (id === 'userChatTransferReasonDescription') {
                return transferReasonDescription;
            }
            // users.js binds each data-export trigger at module load time. Supply
            // inert buttons for those unrelated controls while leaving the edit
            // reason modal absent so the harness can observe editor navigation.
            if (/^export(?:User|SingleUserBundle)/.test(id)) {
                if (!includeExportButton) {
                    return null;
                }
                if (!inertElements.has(id)) {
                    inertElements.set(id, new FakeElement('button'));
                }
                return inertElements.get(id);
            }
            return null;
        },
        createElement(tagName) {
            return new FakeElement(tagName);
        },
        createDocumentFragment() {
            return new FakeFragment();
        },
        addEventListener() {},
    };

    const window = {
        getTranslation(_key, fallback) {
            return fallback;
        },
        createAdminTableHeader({ className, cells }) {
            const header = new FakeElement('div');
            header.className = className;
            cells.forEach(({ className: cellClass, text }) => {
                const cell = new FakeElement('div');
                cell.className = cellClass;
                cell.textContent = text;
                header.appendChild(cell);
            });
            return header;
        },
        createAdminTableCell({ className, text }) {
            const cell = new FakeElement('div');
            cell.className = className;
            if (text !== undefined) {
                cell.textContent = text;
            }
            return cell;
        },
        createAdminIconActionButton({ className, dataset }) {
            const button = new FakeElement('button');
            button.className = className;
            Object.assign(button.dataset, dataset);
            return button;
        },
        openAdminUserSettingsPage(user) {
            openedUsers.push(user);
        },
    };

    const context = vm.createContext({
        console,
        document,
        Icons: {},
        MutationObserver: class {},
        notifyError() {},
        window,
    });
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'users.js'), 'utf8');
    vm.runInContext(source, context, { filename: 'users.js' });

    return { context, deleteModalElements, listContainer, openedUsers, transferReasonDescription };
}

function createHardDeleteHarness() {
    const hardDeleteModalElements = new Map([
        ['hardDeleteUserOverlay', new FakeElement('div')],
        ['hardDeleteUserMessage', new FakeElement('p')],
        ['hardDeleteUserCancelButton', new FakeElement('button')],
        ['hardDeleteUserPrimaryButton', new FakeElement('button')],
        ['hardDeleteUserPrimaryText', new FakeElement('span')],
    ]);
    hardDeleteModalElements.get('hardDeleteUserOverlay').hidden = true;
    const document = {
        documentElement: {},
        getElementById(id) {
            return hardDeleteModalElements.get(id) || null;
        },
        createElement(tagName) {
            return new FakeElement(tagName);
        },
        addEventListener() {},
    };
    const window = {
        getTranslation(_key, fallback) {
            return fallback;
        },
    };
    const context = vm.createContext({
        console,
        document,
        Icons: {},
        MutationObserver: class {},
        notifyError() {},
        notifySuccess() {},
        window,
    });
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'deletedUsers.js'), 'utf8');
    vm.runInContext(source, context, { filename: 'deletedUsers.js' });

    return { context, hardDeleteModalElements };
}

test('user deletion opens the normal confirmation without requesting a preview', () => {
    const { context, deleteModalElements } = createHarness();
    const overlay = deleteModalElements.get('deleteUserOverlay');
    const message = deleteModalElements.get('deleteUserMessage');
    const primaryButton = deleteModalElements.get('deleteUserPrimaryButton');

    context.openDeleteUserModal('user-123', 'Ada Lovelace');

    assert.equal(overlay.hidden, false);
    assert.equal(overlay.classList.contains('active'), true);
    assert.equal(message.textContent, 'Are you sure you want to delete "Ada Lovelace"?');
    assert.notEqual(primaryButton.disabled, true);
});

test('closing the user transfer modal does not depend on open-only variables', () => {
    const { context } = createHarness();

    assert.doesNotThrow(() => context.closeUserChatTransferModal());
});

test('users module loads when the selected-user export button is absent', () => {
    assert.doesNotThrow(() => createHarness({ includeExportButton: false }));
});

test('permanent deletion opens the hard-delete confirmation without requesting a preview', () => {
    const { context, hardDeleteModalElements } = createHardDeleteHarness();
    const overlay = hardDeleteModalElements.get('hardDeleteUserOverlay');
    const message = hardDeleteModalElements.get('hardDeleteUserMessage');
    const cancelButton = hardDeleteModalElements.get('hardDeleteUserCancelButton');
    const primaryButton = hardDeleteModalElements.get('hardDeleteUserPrimaryButton');

    context.openHardDeleteUserModal('user-123', 'Ada Lovelace');

    assert.equal(overlay.hidden, false);
    assert.equal(overlay.classList.contains('active'), true);
    assert.equal(
        message.textContent,
        'PERMANENT DELETION\n\nAre you sure you want to permanently delete Ada Lovelace? This action cannot be undone.',
    );
    assert.notEqual(primaryButton.disabled, true);
    assert.equal(cancelButton.focused, true);
});

test('clicking or keyboard-activating a user row opens the standard user editor', () => {
    const { context, listContainer, openedUsers } = createHarness();
    const users = [{
        id: 'user-123',
        first_name: 'Ada',
        last_name: 'Lovelace',
        email: 'ada@example.com',
        group_name: 'Engineering',
        role: 'user',
        is_active: true,
    }];

    vm.runInContext(`usersCache = ${JSON.stringify(users)}`, context);
    context.renderUsersList(users);
    context.bindUserListActions();

    const row = listContainer.children[1];
    assert.equal(row.dataset.userId, 'user-123');
    assert.equal(row.getAttribute('tabindex'), '0');
    assert.equal(row.getAttribute('aria-label'), 'Edit user: Ada Lovelace');

    // The action-cell background remains independent from the row shortcut.
    listContainer.listeners.click({ target: findByClass(row, 'user-actions') });
    assert.equal(openedUsers.length, 0);

    listContainer.listeners.click({ target: findByClass(row, 'user-group') });
    assert.equal(openedUsers.length, 1);
    assert.equal(openedUsers[0].id, 'user-123');

    let prevented = false;
    listContainer.listeners.keydown({
        key: 'Enter',
        target: row,
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.equal(openedUsers.length, 2);
    assert.equal(openedUsers[1].id, 'user-123');
});

test('user row accessible name falls back to the email address', () => {
    const { context, listContainer } = createHarness();
    const users = [{
        id: 'user-123',
        email: 'ada@example.com',
        role: 'user',
        is_active: true,
    }];

    context.renderUsersList(users);

    const row = listContainer.children[1];
    assert.equal(row.getAttribute('aria-label'), 'Edit user: ada@example.com');
});

test('role, status, and action controls do not trigger the row edit shortcut', () => {
    const { context, listContainer, openedUsers } = createHarness();
    const users = [{
        id: 'user-123',
        first_name: 'Ada',
        email: 'ada@example.com',
        role: 'user',
        is_active: true,
    }];

    vm.runInContext(`usersCache = ${JSON.stringify(users)}`, context);
    context.renderUsersList(users);
    context.bindUserListActions();

    const row = listContainer.children[1];
    const editButton = findByClass(row, 'user-action-edit');
    listContainer.listeners.click({ target: editButton });
    assert.equal(openedUsers.length, 1, 'the explicit edit button should still open the editor');

    const inertTargets = [
        findByClass(row, 'user-actions'),
        findByClass(row, 'user-role'),
        findByClass(row, 'user-status'),
    ];
    inertTargets.forEach((target) => {
        listContainer.listeners.keydown({
            key: 'Enter',
            target,
            preventDefault() {
                throw new Error('interactive cells must keep their own keyboard behavior');
            },
        });
    });
    assert.equal(openedUsers.length, 1);
});

test('admin sees owner and peer-admin account controls as protected', () => {
    const { context, listContainer } = createHarness();
    const users = [
        {
            id: 'owner-1',
            first_name: 'Instance',
            last_name: 'Owner',
            email: 'owner@example.com',
            role: 'owner',
            is_active: true,
        },
        {
            id: 'admin-2',
            first_name: 'Peer',
            last_name: 'Admin',
            email: 'peer@example.com',
            role: 'admin',
            is_active: true,
        },
    ];

    vm.runInContext("currentUserId = 'admin-1'; currentUserRole = 'admin';", context);
    context.renderUsersList(users);

    const ownerRow = listContainer.children[1];
    const peerAdminRow = listContainer.children[2];
    assert.equal(findByClass(ownerRow, 'role-badge').textContent, 'Owner');

    [ownerRow, peerAdminRow].forEach((row) => {
        assert.equal(row.dataset.canEdit, 'false');
        assert.equal(row.getAttribute('tabindex'), undefined);
        assert.equal(findByClass(row, 'user-role').getAttribute('aria-disabled'), 'true');
        assert.equal(findByClass(row, 'user-status').getAttribute('aria-disabled'), 'true');
        assert.equal(findByClass(row, 'user-action-edit'), null);
        assert.equal(findByClass(row, 'user-action-delete'), null);
    });
});

test('owner can manage an administrator but cannot mutate the owner account', () => {
    const { context, listContainer } = createHarness();
    const users = [
        {
            id: 'owner-1',
            email: 'owner@example.com',
            role: 'owner',
            is_active: true,
        },
        {
            id: 'admin-2',
            email: 'admin@example.com',
            role: 'admin',
            is_active: true,
        },
    ];

    vm.runInContext("currentUserId = 'owner-1'; currentUserRole = 'owner';", context);
    context.renderUsersList(users);

    const ownerRow = listContainer.children[1];
    const adminRow = listContainer.children[2];
    assert.equal(ownerRow.dataset.canEdit, 'true');
    assert.equal(findByClass(ownerRow, 'user-role').dataset.mutable, 'false');
    assert.equal(findByClass(ownerRow, 'user-action-delete'), null);

    assert.equal(adminRow.dataset.canEdit, 'true');
    assert.equal(findByClass(adminRow, 'user-role').dataset.mutable, 'true');
    assert.equal(findByClass(adminRow, 'user-status').dataset.mutable, 'true');
    assert.notEqual(findByClass(adminRow, 'user-action-edit'), null);
    assert.notEqual(findByClass(adminRow, 'user-action-delete'), null);
});

test('only owner role cycling can promote a user to admin', () => {
    const { context } = createHarness();

    vm.runInContext("currentUserRole = 'admin';", context);
    assert.equal(context.getNextRole('user'), 'pending');

    vm.runInContext("currentUserRole = 'owner';", context);
    assert.equal(context.getNextRole('user'), 'admin');
});

test('owner hierarchy translations exist in every supported locale', () => {
    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    const requiredKeys = [
        'users_role_owner',
        'users_account_owner_protected',
        'users_account_admin_owner_only',
    ];

    for (const entry of fs.readdirSync(localeRoot, { withFileTypes: true })) {
        if (!entry.isDirectory()) {
            continue;
        }
        const translationPath = path.join(localeRoot, entry.name, 'admin.json');
        if (!fs.existsSync(translationPath)) {
            continue;
        }
        const translations = JSON.parse(fs.readFileSync(translationPath, 'utf8'));
        requiredKeys.forEach((key) => {
            assert.equal(
                typeof translations[key],
                'string',
                `${entry.name} is missing ${key}`,
            );
            assert.ok(translations[key].trim(), `${entry.name} has an empty ${key}`);
        });
    }
});
