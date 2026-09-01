const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeElement {
    constructor(tagName = 'div') {
        this.tagName = String(tagName || '').toUpperCase();
        this.children = [];
        this.listeners = {};
        this.parentNode = null;
    }

    appendChild(child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    replaceChildren(...children) {
        this.children = [];
        children.forEach((child) => this.appendChild(child));
    }

    addEventListener(type, handler) {
        this.listeners[type] = handler;
    }

    removeEventListener(type, handler) {
        if (this.listeners[type] === handler) {
            delete this.listeners[type];
        }
    }
}

function createHarness() {
    const elements = new Map([
        ['createSlidePresentationSettingsBack', new FakeElement('button')],
        ['createSlidePresentationServiceConnectionsLink', new FakeElement('div')],
        ['createSlidePresentationSettingsFields', new FakeElement('div')],
        ['createSlidePresentationSettingsStatus', new FakeElement('div')],
    ]);

    const controllerCalls = {
        init: 0,
        teardown: 0,
    };
    const rowCalls = [];
    const activatedPages = [];

    const context = {
        console,
        document: {
            getElementById(id) {
                return elements.get(id) || null;
            },
            createElement(tagName) {
                return new FakeElement(tagName);
            },
        },
        window: {
            getTranslation(_key, fallback) {
                return fallback;
            },
            createSettingsPageController(config) {
                context.controllerConfig = config;
                return {
                    init() {
                        controllerCalls.init += 1;
                    },
                    teardown() {
                        controllerCalls.teardown += 1;
                    },
                };
            },
            renderAdminServiceConnectionsSettingsRow(targetId, options) {
                rowCalls.push({ targetId, options });
                const target = elements.get(targetId);
                if (target) {
                    target.replaceChildren(new FakeElement('div'));
                }
            },
            activateAdminPage(pageKey) {
                activatedPages.push(pageKey);
            },
            notifyError() {},
        },
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'createSlidePresentation.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'createSlidePresentation.js' });

    return {
        activatedPages,
        controllerCalls,
        elements,
        rowCalls,
        window: context.window,
    };
}

test('slide presentation settings page exposes service connections shortcut and cleans it up on teardown', () => {
    const harness = createHarness();
    const backButton = harness.elements.get('createSlidePresentationSettingsBack');
    const serviceConnectionsMount = harness.elements.get('createSlidePresentationServiceConnectionsLink');

    harness.window.initCreateSlidePresentationSettingsPage();

    assert.equal(harness.controllerCalls.init, 1);
    assert.equal(harness.rowCalls.length, 1);
    assert.equal(harness.rowCalls[0].targetId, 'createSlidePresentationServiceConnectionsLink');
    assert.equal(harness.rowCalls[0].options.descriptionKey, 'service_connections_slide_render_row_desc');
    assert.equal(
        harness.rowCalls[0].options.description,
        'Manage renderer service endpoints, weights, and availability checks for presentation rendering.'
    );
    assert.equal(serviceConnectionsMount.children.length, 1);

    backButton.listeners.click();
    assert.deepEqual(harness.activatedPages, ['tools']);

    harness.window.teardownCreateSlidePresentationSettingsPage();

    assert.equal(harness.controllerCalls.teardown, 1);
    assert.equal(serviceConnectionsMount.children.length, 0);
    assert.equal(backButton.listeners.click, undefined);
});
