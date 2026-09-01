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
        this.style = {};
        this.hidden = false;
        this.parentNode = null;
        this.listeners = {};
        this._innerHTML = '';
        this._textContent = '';
    }

    set className(value) {
        this.attributes.class = String(value || '');
    }

    get className() {
        return this.attributes.class || '';
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

    removeAttribute(name) {
        delete this.attributes[name];
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

    addEventListener(type, handler) {
        this.listeners[type] = handler;
    }

    removeEventListener(type) {
        delete this.listeners[type];
    }

    matches(selector) {
        if (selector.startsWith('.')) {
            return String(this.className || '').split(/\s+/).includes(selector.slice(1));
        }
        const dataAttribute = selector.match(/^\[data-([a-z-]+)\]$/);
        if (dataAttribute) {
            const datasetKey = dataAttribute[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
            return this.dataset[datasetKey] !== undefined;
        }
        return false;
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
        const normalizedTag = String(selector || '').toUpperCase();
        for (const child of this.children) {
            if (child.tagName === normalizedTag) {
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

function findButtonByDataset(root, key) {
    if (!root) {
        return null;
    }
    if (root.tagName === 'BUTTON' && root.dataset && root.dataset[key]) {
        return root;
    }
    for (const child of root.children || []) {
        const match = findButtonByDataset(child, key);
        if (match) {
            return match;
        }
    }
    return null;
}

function findByClass(root, className) {
    if (!root) {
        return null;
    }
    const classes = String(root.className || '').split(/\s+/);
    if (classes.includes(className)) {
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

function createHarness(groups, defaultGroup = 'default') {
    const elements = new Map();
    const listPage = new FakeElement('div');
    const listContainer = new FakeElement('div');
    const formPage = new FakeElement('div');
    formPage.hidden = true;
    const formSubmitButton = new FakeElement('button');
    formSubmitButton.appendChild(new FakeElement('span'));
    const defaultSettings = new FakeElement('div');
    elements.set('page-groups', listPage);
    elements.set('groupsList', listContainer);
    elements.set('page-groups-edit', formPage);
    elements.set('groupFormSubmit', formSubmitButton);
    elements.set('groupsDefaultSettings', defaultSettings);
    let settingsControllerOptions = null;
    const settingsControllerCalls = { init: 0, teardown: 0 };

    const document = {
        getElementById(id) {
            return elements.get(id) || null;
        },
        createElement(tagName) {
            return new FakeElement(tagName);
        },
        createDocumentFragment() {
            return new FakeFragment();
        },
        addEventListener() {},
    };

    const translations = {
        provider_group_edit_aria: 'Edit {name}',
        provider_group_duplicate_aria: 'Duplicate {name}',
        provider_group_delete_aria: 'Delete {name}',
    };

    const context = {
        Icons: {},
        console,
        document,
        URLSearchParams,
        fetchAdminGroupsList: async () => groups,
        fetchAdminJson: async () => ({}),
        notifyError() {},
        notifySuccess() {},
        setButtonLoadingState() {},
        window: {
            createSettingsPageController(options) {
                settingsControllerOptions = options;
                return {
                    init() {
                        settingsControllerCalls.init += 1;
                        options.onLoad?.({ default_user_group: defaultGroup });
                    },
                    teardown() {
                        settingsControllerCalls.teardown += 1;
                    },
                };
            },
            getTranslation(key, fallback) {
                return translations[key] || fallback || key;
            },
            formatTranslation(key, fallback, vars) {
                return String(translations[key] || fallback || key).replace(/\{(\w+)\}/g, (_, token) => String(vars?.[token] ?? ''));
            },
            createAdminEmptyPlaceholder() {
                return new FakeElement('div');
            },
            createAdminTableHeader({ className, cells = [] } = {}) {
                const header = new FakeElement('div');
                header.className = className;
                cells.forEach(({ className: cellClassName, text }) => {
                    const cell = new FakeElement('div');
                    cell.className = cellClassName;
                    cell.textContent = text;
                    header.appendChild(cell);
                });
                return header;
            },
            createAdminTableCell({ className, label, text } = {}) {
                const cell = new FakeElement('div');
                cell.className = className;
                if (label) {
                    cell.dataset.label = label;
                }
                if (text !== undefined) {
                    cell.textContent = text;
                }
                return cell;
            },
            createAdminIconActionButton({ className, title, ariaLabel, dataset = {} } = {}) {
                const button = new FakeElement('button');
                button.className = className;
                button.setAttribute('title', title);
                button.setAttribute('aria-label', ariaLabel || title);
                Object.entries(dataset).forEach(([name, value]) => {
                    button.dataset[name] = value;
                });
                return button;
            },
        },
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'groups.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'groups.js' });

    return {
        defaultSettings,
        getSettingsControllerOptions: () => settingsControllerOptions,
        formPage,
        listPage,
        listContainer,
        settingsControllerCalls,
        window: context.window,
    };
}

test('group row actions include the group name in aria labels', async () => {
    const { listContainer, window } = createHarness([
        {
            id: 'engineering',
            name: 'Engineering',
            path: ['Company', 'Engineering'],
            direct_member_count: 12,
            direct_manager_count: 2,
        },
    ]);

    await window.initGroupsPage();

    const row = listContainer.children[1];
    assert.ok(row, 'expected the rendered group row');

    assert.equal(findButtonByDataset(row, 'groupEdit')?.getAttribute('aria-label'), 'Edit Engineering');
    assert.equal(findButtonByDataset(row, 'groupDuplicate')?.getAttribute('aria-label'), 'Duplicate Engineering');
    assert.equal(findButtonByDataset(row, 'groupDelete')?.getAttribute('aria-label'), 'Delete Engineering');
});

test('clicking a group row opens the same edit form while preserving the action cell', async () => {
    const { formPage, listContainer, listPage, window } = createHarness([
        { id: 'engineering', name: 'Engineering', path: ['Company', 'Engineering'] },
    ]);

    await window.initGroupsPage();

    const row = listContainer.children[1];
    assert.equal(row.dataset.groupId, 'engineering');
    assert.equal(row.getAttribute('tabindex'), '0');
    assert.equal(row.getAttribute('aria-label'), 'Edit Engineering');

    // Empty space in the actions cell remains inert, just like the models table.
    listContainer.listeners.click({ target: findByClass(row, 'user-actions') });
    assert.equal(listPage.hidden, false);
    assert.equal(formPage.hidden, true);

    listContainer.listeners.click({ target: findByClass(row, 'group-name') });
    assert.equal(listPage.hidden, true);
    assert.equal(formPage.hidden, false);
});

test('pressing Enter on a focused group row opens its edit form', async () => {
    const { formPage, listContainer, listPage, window } = createHarness([
        { id: 'engineering', name: 'Engineering' },
    ]);

    await window.initGroupsPage();

    let prevented = false;
    listContainer.listeners.keydown({
        key: 'Enter',
        target: listContainer.children[1],
        preventDefault() {
            prevented = true;
        },
    });

    assert.equal(prevented, true);
    assert.equal(listPage.hidden, true);
    assert.equal(formPage.hidden, false);
});

test('groups table omits the updated column and timestamp cell', async () => {
    const { listContainer, window } = createHarness([
        {
            id: 'engineering',
            name: 'Engineering',
            created_at: '2026-05-01T12:00:00Z',
            updated_at: '2026-05-02T08:30:00Z',
        },
    ]);

    await window.initGroupsPage();

    const header = listContainer.children[0];
    const row = listContainer.children[1];
    assert.equal(header.children.length, 5);
    assert.equal(findByClass(row, 'group-description'), null);
});

test('groups page renders default user group schema below the groups table', async () => {
    const { defaultSettings, getSettingsControllerOptions, listContainer, settingsControllerCalls, window } = createHarness([
        { id: 'default', name: 'Default', created_at: '2026-05-01T12:00:00Z' },
        { id: 'engineering', name: 'Engineering', created_at: '2026-05-01T12:00:00Z' },
    ], 'engineering');

    await window.initGroupsPage();

    const controllerOptions = getSettingsControllerOptions();
    const engineeringRow = listContainer.children[2];
    assert.equal(controllerOptions.pageKey, 'groups_defaults');
    assert.equal(controllerOptions.containerId, defaultSettings);
    assert.equal(settingsControllerCalls.init, 1);
    assert.equal(findByClass(engineeringRow, 'group-default-badge')?.textContent, 'Default');

    controllerOptions.onFieldSaved({ fieldKey: 'default_user_group', value: 'default' });
    const defaultRow = listContainer.children[1];
    assert.equal(findByClass(defaultRow, 'group-default-badge')?.textContent, 'Default');
});
