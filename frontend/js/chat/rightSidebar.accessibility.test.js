const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const RIGHT_SIDEBAR_PATH = path.join(__dirname, 'rightSidebar.js');
const INDEX_PATH = path.resolve(__dirname, '../../index.html');

function createClassList() {
    const values = new Set();
    return {
        add: (...names) => names.forEach((name) => values.add(name)),
        remove: (...names) => names.forEach((name) => values.delete(name)),
        contains: (name) => values.has(name),
        toggle(name, force) {
            const enabled = force === undefined ? !values.has(name) : force;
            if (enabled) values.add(name);
            else values.delete(name);
            return enabled;
        },
    };
}

function createHarness() {
    const events = [];
    const listeners = new Map();
    const document = {
        activeElement: null,
        querySelector: () => null,
        addEventListener: () => {},
    };
    const createElement = (id) => ({
        id,
        isConnected: true,
        disabled: false,
        hidden: false,
        classList: createClassList(),
        attributes: new Map(),
        style: {},
        addEventListener(type, listener) {
            listeners.set(`${id}:${type}`, listener);
        },
        removeEventListener() {},
        setAttribute(name, value) {
            this.attributes.set(name, String(value));
            events.push(`${id}:set:${name}:${value}`);
        },
        removeAttribute(name) {
            this.attributes.delete(name);
            events.push(`${id}:remove:${name}`);
        },
        closest: () => null,
        focus() {
            document.activeElement = this;
            events.push(`${id}:focus`);
        },
    });

    const panel = createElement('modelSettingsSidebar');
    const closeButton = createElement('modelSettingsSidebarClose');
    const mainOpenButton = createElement('openModelSettingsButton');
    const backdrop = createElement('modelSettingsSidebarBackdrop');
    const headerMenuButton = createElement('headerDotsButton');
    const splitMenuButton = createElement('splitLeftActionsButton');
    const splitSettingsButton = createElement('splitSettingsButton');
    const splitActions = {
        querySelector: () => splitMenuButton,
    };
    splitSettingsButton.closest = (selector) => (
        selector === '[data-split-panel-actions]' ? splitActions : null
    );
    panel.contains = (element) => element === closeButton;

    const elements = new Map([
        [panel.id, panel],
        [closeButton.id, closeButton],
        [mainOpenButton.id, mainOpenButton],
        [backdrop.id, backdrop],
        [headerMenuButton.id, headerMenuButton],
    ]);
    document.getElementById = (id) => elements.get(id) || null;
    document.body = createElement('body');
    document.activeElement = splitSettingsButton;

    const window = {
        matchMedia: () => ({ matches: false, addEventListener: () => {} }),
        setTimeout: () => 1,
        clearTimeout: () => {},
    };
    const context = {
        document,
        window,
        requestAnimationFrame: (callback) => callback(),
        clearTimeout: () => {},
    };
    vm.runInNewContext(fs.readFileSync(RIGHT_SIDEBAR_PATH, 'utf8'), context, {
        filename: RIGHT_SIDEBAR_PATH,
    });
    return { events, document, panel, closeButton, splitMenuButton, window };
}

test('closed model settings are inert and absent from the accessibility tree', () => {
    const html = fs.readFileSync(INDEX_PATH, 'utf8');
    assert.match(html, /id="modelSettingsSidebar"[^>]*aria-hidden="true"[^>]*inert/);

    const harness = createHarness();
    assert.equal(harness.panel.attributes.get('aria-hidden'), 'true');
    assert.equal(harness.panel.attributes.has('inert'), true);
});

test('closing split-screen model settings restores focus before hiding the subtree', () => {
    const harness = createHarness();

    harness.window.openModelSettingsSidebar();
    assert.equal(harness.panel.attributes.has('inert'), false);
    assert.equal(harness.panel.attributes.get('aria-hidden'), 'false');
    assert.equal(harness.document.activeElement, harness.closeButton);

    harness.events.length = 0;
    harness.window.closeModelSettingsSidebar();

    assert.equal(harness.document.activeElement, harness.splitMenuButton);
    assert.equal(harness.panel.attributes.has('inert'), true);
    assert.equal(harness.panel.attributes.get('aria-hidden'), 'true');
    assert.ok(
        harness.events.indexOf('splitLeftActionsButton:focus')
            < harness.events.indexOf('modelSettingsSidebar:set:aria-hidden:true'),
        'focus must leave the sidebar before aria-hidden is applied',
    );
});
