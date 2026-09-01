const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeElement {
    constructor(id = '') {
        this.id = id;
        this.hidden = false;
        this.disabled = false;
        this.checked = false;
        this.listeners = {};
        this.styleValues = {};
        this.style = {
            setProperty: (name, value) => {
                this.styleValues[name] = value;
            },
            removeProperty: (name) => {
                delete this.styleValues[name];
            },
        };
    }

    addEventListener(type, listener) {
        this.listeners[type] = listener;
    }

    dispatchEvent(event) {
        event.target = this;
        return this.listeners[event.type]?.(event);
    }
}

function loadSidebarButtonSettingsScript() {
    const source = fs.readFileSync(path.join(__dirname, 'sidebarButtons.js'), 'utf8');
    const elementsById = new Map();
    const optionsByKey = new Map();
    const storage = new Map();
    const select = new FakeElement('sidebar-buttons-multiselect');
    select.options = [];
    Object.defineProperty(select, 'selectedOptions', {
        get() {
            return select.options.filter((option) => option.selected);
        },
    });

    ['create_chat', 'search_chats', 'workspace', 'automations', 'projects'].forEach((key) => {
        const option = new FakeElement(`sidebar-option-${key}`);
        option.value = key;
        option.selected = false;
        optionsByKey.set(key, option);
        select.options.push(option);
    });
    elementsById.set(select.id, select);

    const context = {
        console,
        SIDEBAR_VISIBILITY_STORAGE_KEY: 'sidebar_button_visibility',
        document: {
            getElementById(id) {
                return elementsById.get(id) || null;
            },
            querySelector() {
                return null;
            },
            addEventListener() {},
        },
        localStorage: {
            setItem(key, value) {
                storage.set(key, String(value));
            },
        },
        window: null,
    };
    context.window = context;

    vm.runInNewContext(source, context, { filename: 'sidebarButtons.js' });

    return {
        context,
        elementsById,
        optionsByKey,
        select,
        storage,
    };
}

test('sidebar button multi-select hides options for disabled group features', async () => {
    const harness = loadSidebarButtonSettingsScript();

    await harness.context.initializeSidebarButtonSettings({
        enable_projects: false,
        enable_automations: false,
        chat: {
            sidebar_button_visibility: {
                projects: true,
                automations: true,
            },
        },
    });

    assert.equal(harness.optionsByKey.get('projects').hidden, true);
    assert.equal(harness.optionsByKey.get('projects').disabled, true);
    assert.equal(harness.optionsByKey.get('automations').hidden, true);
    assert.equal(harness.optionsByKey.get('automations').disabled, true);
    assert.equal(harness.optionsByKey.get('workspace').hidden, false);
    assert.equal(harness.optionsByKey.get('workspace').disabled, false);
});

test('sidebar button settings treat string false feature flags as disabled', async () => {
    const harness = loadSidebarButtonSettingsScript();

    await harness.context.initializeSidebarButtonSettings({
        enable_projects: 'false',
        enable_automations: 'false',
        chat: {
            sidebar_button_visibility: {
                projects: true,
                automations: true,
            },
        },
    });

    assert.equal(harness.optionsByKey.get('projects').hidden, true);
    assert.equal(harness.optionsByKey.get('projects').disabled, true);
    assert.equal(harness.optionsByKey.get('automations').hidden, true);
    assert.equal(harness.optionsByKey.get('automations').disabled, true);
});

test('sidebar button settings can use live feature globals when init payload lacks flags', () => {
    const harness = loadSidebarButtonSettingsScript();
    harness.context.window.enableProjectsFeature = false;
    harness.context.window.enableAutomationsFeature = false;

    harness.context.applySidebarSettingsRowAvailability({
        chat: { sidebar_button_visibility: {} },
    });

    assert.equal(harness.optionsByKey.get('projects').hidden, true);
    assert.equal(harness.optionsByKey.get('automations').hidden, true);
});

test('fresh settings init feature policy overrides stale chat setup state', () => {
    const harness = loadSidebarButtonSettingsScript();
    harness.context.window.chatSetup = {
        enable_projects: true,
        enable_automations: true,
    };

    harness.context.applySidebarSettingsRowAvailability({
        enable_projects: false,
        enable_automations: false,
    });

    assert.equal(harness.optionsByKey.get('projects').hidden, true);
    assert.equal(harness.optionsByKey.get('automations').hidden, true);
});

test('later settings init payload refreshes sidebar feature rows after initial load', async () => {
    const harness = loadSidebarButtonSettingsScript();

    await harness.context.initializeSidebarButtonSettings({
        enable_projects: true,
        enable_automations: true,
        chat: { sidebar_button_visibility: {} },
    });
    await harness.context.initializeSidebarButtonSettings({
        enable_projects: false,
        enable_automations: false,
        chat: { sidebar_button_visibility: {} },
    });

    assert.equal(harness.optionsByKey.get('projects').hidden, true);
    assert.equal(harness.optionsByKey.get('automations').hidden, true);
});

test('sidebar button multi-select saves one complete visibility map', async () => {
    const harness = loadSidebarButtonSettingsScript();
    let requestBody = null;
    let appliedVisibility = null;
    harness.context.window.authedFetch = async (_url, options) => {
        requestBody = JSON.parse(options.body);
        return {
            ok: true,
            async json() {
                return {
                    status: 'success',
                    sidebar_button_visibility: requestBody,
                };
            },
        };
    };
    harness.context.applySidebarButtonVisibility = (visibility) => {
        appliedVisibility = visibility;
    };

    await harness.context.initializeSidebarButtonSettings({
        enable_projects: true,
        enable_automations: true,
        chat: { sidebar_button_visibility: {} },
    });
    harness.optionsByKey.get('search_chats').selected = false;
    harness.optionsByKey.get('projects').selected = false;

    await harness.context.handleSidebarButtonSelectionChange();

    assert.deepEqual(requestBody, {
        create_chat: true,
        search_chats: false,
        workspace: true,
        automations: true,
        projects: false,
    });
    assert.equal(JSON.stringify(appliedVisibility), JSON.stringify(requestBody));
    assert.deepEqual(
        JSON.parse(harness.storage.get('sidebar_button_visibility')),
        requestBody,
    );
});
