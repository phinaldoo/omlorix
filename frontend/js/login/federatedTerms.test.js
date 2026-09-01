const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createElement() {
    const attributes = {};
    const classes = new Set();
    const listeners = {};
    const label = { dataset: {}, textContent: '' };
    return {
        dataset: {},
        disabled: false,
        hidden: false,
        inert: false,
        tabIndex: 0,
        classList: {
            toggle(name, enabled) {
                if (enabled) {
                    classes.add(name);
                } else {
                    classes.delete(name);
                }
            },
            contains: (name) => classes.has(name),
        },
        setAttribute(name, value) {
            attributes[name] = String(value);
        },
        getAttribute(name) {
            return Object.prototype.hasOwnProperty.call(attributes, name)
                ? attributes[name]
                : null;
        },
        addEventListener(name, handler) {
            listeners[name] = handler;
        },
        querySelector(selector) {
            return selector === '[data-i18n]' ? label : null;
        },
        focus() {},
    };
}

test('social Terms confirmation clears the modal before opening 2FA', async () => {
    const elements = {
        federatedTermsOverlay: createElement(),
        federatedTermsConfirmButton: createElement(),
        federatedTermsCancelButton: createElement(),
        federatedTermsTitle: createElement(),
        federatedTermsMessage: createElement(),
    };
    const documentListeners = {};
    const windowListeners = {};
    let stateAtTwoFA = null;
    const context = {
        console,
        URL,
        URLSearchParams,
        fetch: async () => ({
            redirected: false,
            ok: true,
            clone: () => ({
                json: async () => ({ status: 'otp_required_already_setup' }),
            }),
        }),
        setTimeout: (handler) => {
            handler();
            return 1;
        },
        document: {
            activeElement: null,
            getElementById: (id) => elements[id] || null,
            addEventListener: (name, handler) => {
                documentListeners[name] = handler;
            },
        },
    };
    context.window = {
        location: {
            search: '?social_terms_pending=true&provider=google',
            origin: 'https://chat.example',
            href: '/login?social_terms_pending=true&provider=google',
            assign() {},
        },
        history: { replaceState() {} },
        loginAuthFlowContext: { resetLoginCallbackUrl() {} },
        omlorixTermsOfServicePolicy: { revision: 7 },
        getTranslation: (_key, fallback) => fallback,
        setTimeout: context.setTimeout,
        addEventListener: (name, handler) => {
            windowListeners[name] = handler;
        },
        socialLogin: {
            handleSocial2FAResult: () => {
                stateAtTwoFA = {
                    overlayActive: elements.federatedTermsOverlay.classList.contains('active'),
                    confirmBusy:
                        elements.federatedTermsConfirmButton.getAttribute('aria-busy') === 'true',
                };
                return true;
            },
        },
    };
    context.globalThis = context.window;
    context.window.window = context.window;
    context.window.document = context.document;
    context.window.fetch = context.fetch;
    context.window.URL = URL;
    context.window.URLSearchParams = URLSearchParams;
    vm.createContext(context);

    const sourcePath = path.join(__dirname, 'federatedTerms.js');
    vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });
    documentListeners.DOMContentLoaded();

    assert.equal(elements.federatedTermsOverlay.classList.contains('active'), true);
    await context.window.federatedTermsSignup.confirmPendingTerms();

    assert.deepEqual(stateAtTwoFA, {
        overlayActive: false,
        confirmBusy: false,
    });
});
