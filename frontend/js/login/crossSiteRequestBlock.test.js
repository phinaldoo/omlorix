const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createClassList() {
    const classes = new Set();
    return {
        add: (name) => classes.add(name),
        contains: (name) => classes.has(name),
    };
}

function createElement(id = '') {
    return {
        id,
        hidden: true,
        textContent: '',
        focusCalls: [],
        focus(options) {
            this.focusCalls.push(options || null);
        },
    };
}

function loadBlocker(contextOverrides = {}) {
    const elements = {
        crossSiteRequestBlockedState: createElement('crossSiteRequestBlockedState'),
        crossSiteRequestBlockedTitle: createElement('crossSiteRequestBlockedTitle'),
        crossSiteRequestBlockedMessage: createElement('crossSiteRequestBlockedMessage'),
        crossSiteRequestBlockedHelp: createElement('crossSiteRequestBlockedHelp'),
    };
    const listeners = {};
    const body = { classList: createClassList() };
    const context = {
        console,
        requestAnimationFrame: (callback) => callback(),
        document: {
            readyState: 'complete',
            body,
            addEventListener(eventName, handler) {
                listeners[eventName] = handler;
            },
            getElementById(id) {
                return elements[id] || null;
            },
        },
        window: {
            getTranslation: (_key, fallback) => fallback,
        },
        ...contextOverrides,
    };
    context.window.document = context.document;
    context.window.requestAnimationFrame = context.requestAnimationFrame;
    context.window.window = context.window;
    context.globalThis = context.window;

    vm.createContext(context);
    const sourcePath = path.join(__dirname, 'crossSiteRequestBlock.js');
    vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });
    return { context, elements, listeners, body };
}

test('showCrossSiteRequestBlocked hides the login page and focuses the blocked state', () => {
    const { context, elements, body } = loadBlocker();

    assert.equal(context.window.showCrossSiteRequestBlocked('Cross-site request blocked'), true);

    assert.equal(elements.crossSiteRequestBlockedState.hidden, false);
    assert.equal(body.classList.contains('cross-site-login-blocked'), true);
    assert.equal(elements.crossSiteRequestBlockedTitle.textContent, 'Cross-site request blocked');
    assert.equal(elements.crossSiteRequestBlockedState.focusCalls.length, 1);
    assert.equal(elements.crossSiteRequestBlockedState.focusCalls[0].preventScroll, true);
});

test('handleCrossSiteRequestBlock detects the backend 403 response detail', async () => {
    const { context, elements } = loadBlocker();
    const response = {
        status: 403,
        clone() {
            return {
                json: async () => ({ detail: 'Cross-site request blocked: localhost/private-network origins are disallowed' }),
            };
        },
    };

    assert.equal(await context.window.handleCrossSiteRequestBlock(response), true);
    assert.equal(elements.crossSiteRequestBlockedState.hidden, false);
    assert.equal(elements.crossSiteRequestBlockedMessage.textContent, 'Omlorix blocked this sign-in request because the page origin does not match the configured public URL.');
});

test('handleCrossSiteRequestBlock ignores unrelated forbidden responses', async () => {
    const { context, elements } = loadBlocker();
    const response = {
        status: 403,
        clone() {
            return {
                json: async () => ({ detail: 'Missing permissions' }),
            };
        },
    };

    assert.equal(await context.window.handleCrossSiteRequestBlock(response), false);
    assert.equal(elements.crossSiteRequestBlockedState.hidden, true);
});
