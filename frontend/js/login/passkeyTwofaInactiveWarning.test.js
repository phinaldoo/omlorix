const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeClassList {
    constructor() {
        this.classes = new Set();
    }

    add(...tokens) {
        tokens.filter(Boolean).forEach((token) => this.classes.add(token));
    }

    remove(...tokens) {
        tokens.filter(Boolean).forEach((token) => this.classes.delete(token));
    }

    toggle(token, force) {
        if (force === undefined) {
            if (this.classes.has(token)) {
                this.classes.delete(token);
            } else {
                this.classes.add(token);
            }
        } else if (force) {
            this.classes.add(token);
        } else {
            this.classes.delete(token);
        }
        return this.classes.has(token);
    }

    contains(token) {
        return this.classes.has(token);
    }
}

function createFakeElement(id = '') {
    return {
        id,
        hidden: false,
        inert: false,
        tabIndex: 0,
        value: '',
        textContent: '',
        dataset: {},
        style: {},
        children: [],
        classList: new FakeClassList(),
        attributes: new Map(),
        listeners: {},
        focusCalls: 0,
        addEventListener(eventName, handler) {
            this.listeners[eventName] = handler;
        },
        removeEventListener(eventName) {
            delete this.listeners[eventName];
        },
        setAttribute(name, value) {
            this.attributes.set(name, String(value));
        },
        removeAttribute(name) {
            this.attributes.delete(name);
        },
        getAttribute(name) {
            return this.attributes.has(name) ? this.attributes.get(name) : null;
        },
        hasAttribute(name) {
            return this.attributes.has(name);
        },
        focus() {
            this.focusCalls += 1;
        },
        select() {},
        querySelector() {
            return this.children[0] || null;
        },
        querySelectorAll(selector) {
            if (selector === '.tfa-digit') {
                return this.children.slice();
            }
            return [];
        },
        contains(target) {
            return target === this || this.children.includes(target);
        },
        getClientRects() {
            return [{ width: 1, height: 1 }];
        },
    };
}

function createHarness() {
    const elements = new Map();
    const getElement = (id) => {
        if (!elements.has(id)) {
            elements.set(id, createFakeElement(id));
        }
        return elements.get(id);
    };

    const setupOverlay = getElement('tfaSetupOverlay');
    const verifyOverlay = getElement('tfaVerifyOverlay');
    const setupDigits = Array.from({ length: 6 }, (_, index) => {
        const digit = createFakeElement(`setup-digit-${index}`);
        digit.value = '1';
        return digit;
    });
    const verifyDigits = Array.from({ length: 6 }, (_, index) => {
        const digit = createFakeElement(`verify-digit-${index}`);
        digit.value = '1';
        return digit;
    });
    setupOverlay.children = setupDigits;
    verifyOverlay.children = verifyDigits;

    const warningCalls = [];
    const hideCalls = [];
    const notificationMessages = [];
    const sessionStorageState = {};

    const window = {
        location: { href: '/login', origin: 'https://chat.example.com', hostname: 'chat.example.com' },
        loginModalManager: {
            sync: () => null,
        },
        passkeyLogin: {
            completePasskeyLoginWith2FA: async () => ({ status: 'inactive' }),
            clearPasskeyLogin2FAFlow: () => {},
            isInPasskeyLogin2FAFlow: () => true,
        },
        showInactiveAccountWarning: () => warningCalls.push('inactive'),
        notifyError: (message) => notificationMessages.push(message),
        getTranslation: (key, fallback) => ({
            inactive_title: 'Account Inactive',
            inactive_message: 'Your account has been <strong>deactivated</strong>. Please contact support for further assistance.',
        }[key] || fallback),
        formatTranslation: (key, fallback, vars = {}) => Object.entries(vars).reduce(
            (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
            (window.getTranslation(key, fallback)),
        ),
        omlorix2FAContext: {
            provider: 'totp',
            deliveryHint: '',
        },
        sessionStorage: {
            getItem: (key) => (Object.prototype.hasOwnProperty.call(sessionStorageState, key) ? sessionStorageState[key] : null),
            setItem: (key, value) => {
                sessionStorageState[key] = String(value);
            },
            removeItem: (key) => {
                delete sessionStorageState[key];
            },
        },
    };
    window.window = window;
    window.globalThis = window;

    const document = {
        body: { children: [setupOverlay, verifyOverlay] },
        activeElement: null,
        addEventListener: () => {},
        getElementById: (id) => getElement(id),
        querySelectorAll: (selector) => {
            if (selector === '#tfaVerifyOverlay .tfa-digit') {
                return verifyDigits;
            }
            if (selector === '#tfaSetupOverlay .tfa-digit') {
                return setupDigits;
            }
            if (selector === '.tfa-digit[data-digit-index]') {
                return [...setupDigits, ...verifyDigits];
            }
            return [];
        },
    };

    window.document = document;

    const context = {
        console,
        window,
        document,
        sessionStorage: window.sessionStorage,
        showInactiveAccountWarning: window.showInactiveAccountWarning,
        notifyError: window.notifyError,
        setTimeout: (fn) => {
            if (typeof fn === 'function') fn();
            return 0;
        },
        clearTimeout: () => {},
    };
    context.globalThis = window;

    return {
        context,
        warningCalls,
        hideCalls,
        notificationMessages,
        setupOverlay,
        verifyOverlay,
    };
}

test('passkey 2FA completion shows the inactive account warning', async () => {
    const { context, warningCalls, notificationMessages, verifyOverlay } = createHarness();
    const source = fs.readFileSync(path.join(__dirname, 'twofa.js'), 'utf8');

    vm.runInNewContext(source, context, { filename: 'frontend/js/login/twofa.js' });

    const result = await context.completePasskeyLogin2FA('verify');

    assert.equal(result, undefined);
    assert.equal(warningCalls.length, 1);
    assert.equal(notificationMessages.length, 0);
    assert.equal(verifyOverlay.classList.contains('active'), false);
});
