const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

/**
 * Provide the small DOM surface used while init.js registers its settings
 * handlers. The test does not need browser layout, only stable element state.
 */
class FakeElement {
    constructor() {
        this.dataset = {};
        this.hidden = false;
        this.listeners = {};
        this.style = {};
        this.classList = {
            add() {},
            remove() {},
            contains: () => false,
            toggle() {},
        };
    }

    addEventListener(eventName, handler) {
        this.listeners[eventName] = handler;
    }

    closest() {
        return this;
    }

    removeEventListener() {}

    querySelector() {
        return new FakeElement();
    }

    setAttribute() {}
}

/**
 * Load the settings initializer in an isolated browser-like context and expose
 * the policy passed to the data-control module.
 */
function createHarness() {
    const elements = new Map();
    const documentListeners = new Map();
    const getElement = (id) => {
        if (!elements.has(id)) {
            elements.set(id, new FakeElement());
        }
        return elements.get(id);
    };
    let receivedPolicy = null;

    const document = {
        activeElement: null,
        body: new FakeElement(),
        documentElement: { getAttribute: () => null },
        addEventListener(eventName, handler) {
            documentListeners.set(eventName, handler);
        },
        getElementById: getElement,
        querySelector: () => new FakeElement(),
        querySelectorAll: () => [],
    };
    let rateLimitsVisible = null;
    const window = {
        addEventListener() {},
        enableMemoriesFeature: true,
        setRateLimitsVisibility(visible) {
            rateLimitsVisible = visible;
        },
        updateDataControlAvailability(policy) {
            receivedPolicy = policy;
            return {
                anyEnabled: Object.values(policy).some(Boolean),
                allEnabled: Object.values(policy).every(Boolean),
            };
        },
    };
    const context = {
        console,
        document,
        setTimeout,
        window,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'init.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'init.js' });

    return {
        applyPolicy: (policy) => context.applyDataControlVisibility(policy),
        applySetupNavigation: (navigation, setup = {}) => {
            documentListeners.get('chatSetupReady')?.({
                detail: { ...setup, user_settings_navigation: navigation },
            });
        },
        getElement,
        getRateLimitsVisible: () => rateLimitsVisible,
        getReceivedPolicy: () => receivedPolicy,
    };
}

test('data-control initialization forwards only the unified archive policy', () => {
    const harness = createHarness();
    const policy = {
        allow_automations: true,
        allow_user_data: true,
    };

    const status = harness.applyPolicy(policy);

    assert.deepEqual(
        Object.keys(harness.getReceivedPolicy()),
        ['allow_user_data'],
    );
    assert.equal(harness.getReceivedPolicy().allow_user_data, true);
    assert.equal(status.allEnabled, true);
});

test('chat bootstrap fixes conditional settings navigation before settings data loads', () => {
    const harness = createHarness();

    harness.applySetupNavigation(
        { managed_groups: true, rate_limits: true },
        { enable_memories: true },
    );

    assert.equal(harness.getElement('memoryNavItem').style.display, '');
    assert.equal(harness.getElement('memorySettingsPage').style.display, '');
    assert.equal(harness.getElement('managedGroupsNavItem').style.display, '');
    assert.equal(harness.getElement('managedGroupsPage').style.display, '');
    assert.equal(harness.getRateLimitsVisible(), true);
});
