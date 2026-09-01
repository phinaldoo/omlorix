const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeStyle {
    constructor() {
        this.values = {};
    }

    setProperty(name, value) {
        this.values[name] = value;
    }
}

class FakeElement {
    constructor(id = '') {
        this.id = id;
        this.attributes = {};
        this.style = new FakeStyle();
        this.parentElement = null;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name)
            ? this.attributes[name]
            : null;
    }

    closest(selector) {
        return selector === '.sidebar-element' ? this.parentElement : null;
    }
}

class FakeMutationObserver {
    constructor(callback) {
        this.callback = callback;
        this.observed = [];
        this.disconnected = false;
    }

    observe(target, options) {
        this.observed.push({ target, options });
    }

    disconnect() {
        this.disconnected = true;
    }
}

function loadSidebarVisibility({
    enableAutomationsFeature = false,
    enableProjectsFeature = false,
    cachedVisibility = null,
} = {}) {
    const elements = new Map();
    const automationsContainer = new FakeElement('sidebarAutomationsContainer');
    const automationsButton = new FakeElement('sidebarAutomations');
    const automationsParent = new FakeElement('automationsParent');
    automationsButton.parentElement = automationsParent;
    const projectsContainer = new FakeElement('sidebarProjects');
    elements.set(automationsContainer.id, automationsContainer);
    elements.set(automationsButton.id, automationsButton);
    elements.set(projectsContainer.id, projectsContainer);

    const storage = new Map();
    if (cachedVisibility) {
        storage.set('sidebar_button_visibility', JSON.stringify(cachedVisibility));
    }

    const document = {
        body: new FakeElement('body'),
        readyState: 'complete',
        addEventListener() {},
        getElementById(id) {
            return elements.get(id) || null;
        },
    };

    const window = {
        enableAutomationsFeature,
        enableProjectsFeature,
        authedFetch: async () => ({
            ok: true,
            json: async () => ({
                chat: {
                    sidebar_button_visibility: cachedVisibility || {},
                },
            }),
        }),
    };

    const context = {
        console,
        document,
        localStorage: {
            getItem(key) {
                return storage.has(key) ? storage.get(key) : null;
            },
            setItem(key, value) {
                storage.set(key, String(value));
            },
        },
        MutationObserver: FakeMutationObserver,
        setTimeout(callback) {
            callback();
            return 1;
        },
        clearTimeout() {},
        window,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'sidebar.js'), 'utf8');
    const visibilitySource = source.split('// ------------------------------------------------------------\n// Overlay-mode detection')[0];
    vm.runInNewContext(visibilitySource, context, { filename: 'sidebar.js' });

    return {
        context,
        window,
        storage,
        automationsContainer,
        automationsParent,
        projectsContainer,
    };
}

test('group-disabled feature buttons stay hidden even when user sidebar preference is visible', async () => {
    const runtime = loadSidebarVisibility({
        enableAutomationsFeature: false,
        enableProjectsFeature: false,
        cachedVisibility: {
            automations: true,
            projects: true,
        },
    });

    await runtime.context.applySidebarButtonVisibility({ automations: true, projects: true });

    assert.equal(runtime.automationsContainer.getAttribute('data-sidebar-hidden'), 'true');
    assert.equal(runtime.projectsContainer.getAttribute('data-sidebar-hidden'), 'true');
    assert.equal(runtime.automationsParent.style.values.display, 'none');
    assert.deepEqual(JSON.parse(runtime.storage.get('sidebar_button_visibility')), {
        automations: true,
        projects: true,
    });
});

test('string false group feature flags keep sidebar buttons hidden', async () => {
    const runtime = loadSidebarVisibility({
        enableAutomationsFeature: 'false',
        enableProjectsFeature: 'false',
        cachedVisibility: {
            automations: true,
            projects: true,
        },
    });

    await runtime.context.applySidebarButtonVisibility({ automations: true, projects: true });

    assert.equal(runtime.automationsContainer.getAttribute('data-sidebar-hidden'), 'true');
    assert.equal(runtime.projectsContainer.getAttribute('data-sidebar-hidden'), 'true');
});

test('string true group feature flags allow visible sidebar preferences', async () => {
    const runtime = loadSidebarVisibility({
        enableAutomationsFeature: 'true',
        enableProjectsFeature: 'true',
        cachedVisibility: {
            automations: true,
            projects: true,
        },
    });

    await runtime.context.applySidebarButtonVisibility({ automations: true, projects: true });

    assert.equal(runtime.automationsContainer.getAttribute('data-sidebar-hidden'), 'false');
    assert.equal(runtime.projectsContainer.getAttribute('data-sidebar-hidden'), 'false');
});

test('automations sidebar preference is honored after group policy enables the feature', () => {
    const runtime = loadSidebarVisibility({
        enableAutomationsFeature: false,
        cachedVisibility: {
            automations: true,
        },
    });

    runtime.window.enableAutomationsFeature = true;
    runtime.context.applySidebarVisibilityFromCache();

    assert.equal(runtime.automationsContainer.getAttribute('data-sidebar-hidden'), 'false');
    assert.equal(runtime.automationsParent.style.values.display, '');
});

test('projects sidebar preference is honored after group policy enables the feature', () => {
    const runtime = loadSidebarVisibility({
        enableProjectsFeature: false,
        cachedVisibility: {
            projects: true,
        },
    });

    runtime.window.enableProjectsFeature = true;
    runtime.context.applySidebarVisibilityFromCache();

    assert.equal(runtime.projectsContainer.getAttribute('data-sidebar-hidden'), 'false');
});
