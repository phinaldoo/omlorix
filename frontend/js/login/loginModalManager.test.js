const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, 'twofa.js');

test('login 2FA initialization tolerates an omitted verification cancel action', () => {
    const source = fs.readFileSync(sourcePath, 'utf8');
    assert.match(source, /getElementById\('tfaVerifyCancelButton'\)\?\.addEventListener/);
});

function createClassList() {
    const values = new Set();
    return {
        add: (...names) => names.forEach((name) => values.add(name)),
        contains: (name) => values.has(name),
        remove: (...names) => names.forEach((name) => values.delete(name)),
        toggle(name, force) {
            const shouldAdd = force === undefined ? !values.has(name) : Boolean(force);
            if (shouldAdd) values.add(name);
            else values.delete(name);
            return shouldAdd;
        },
    };
}

function createElement(id, documentRef, { hidden = false, focusable = false } = {}) {
    const attributes = new Map();
    const listeners = new Map();
    const element = {
        id,
        hidden,
        inert: hidden,
        isConnected: true,
        classList: createClassList(),
        focusables: [],
        dialog: null,
        addEventListener(type, handler) {
            listeners.set(type, handler);
        },
        removeEventListener(type, handler) {
            if (listeners.get(type) === handler) listeners.delete(type);
        },
        contains(candidate) {
            return candidate === this || this.focusables.includes(candidate) || candidate === this.dialog;
        },
        focus() {
            documentRef.activeElement = this;
        },
        getAttribute(name) {
            return attributes.has(name) ? attributes.get(name) : null;
        },
        getClientRects() {
            return focusable ? [{}] : [];
        },
        hasAttribute(name) {
            return attributes.has(name);
        },
        querySelector(selector) {
            return selector === '[role="dialog"]' ? this.dialog : null;
        },
        querySelectorAll() {
            return this.focusables;
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
        setAttribute(name, value) {
            attributes.set(name, String(value));
        },
    };
    if (focusable) {
        element.closest = () => null;
    }
    return element;
}

function createHarness() {
    const keydownHandlers = [];
    const documentRef = {
        activeElement: null,
        addEventListener(type, handler) {
            if (type === 'keydown') keydownHandlers.push(handler);
        },
        getElementById: () => null,
    };
    const body = createElement('body', documentRef);
    body.style = { overflow: 'auto' };
    body.children = [];
    documentRef.body = body;

    const background = createElement('login-layout', documentRef);
    const ids = [
        'federatedTermsOverlay',
        'warningOverlay',
        'pendingOverlay',
        'tfaSetupOverlay',
        'tfaVerifyOverlay',
        'accessBlockedOverlay',
    ];
    const elements = { background };
    ids.forEach((id) => {
        const overlay = createElement(id, documentRef, { hidden: true });
        overlay.setAttribute('aria-hidden', 'true');
        overlay.dialog = createElement(`${id}Dialog`, documentRef);
        elements[id] = overlay;
    });
    body.children = [background, ...ids.map((id) => elements[id])];
    documentRef.getElementById = (id) => elements[id] || null;

    const windowRef = {
        clearTimeout: () => {},
        requestAnimationFrame: (callback) => {
            callback();
            return 1;
        },
        setTimeout: () => 1,
    };
    windowRef.window = windowRef;
    windowRef.document = documentRef;
    const context = { document: documentRef, window: windowRef };
    vm.createContext(context);

    const source = fs.readFileSync(sourcePath, 'utf8');
    const managerEnd = source.indexOf('window.loginModalManager = loginModalManager;');
    vm.runInContext(source.slice(0, managerEnd + 'window.loginModalManager = loginModalManager;'.length), context);
    return { context, documentRef, elements, keydownHandlers, manager: windowRef.loginModalManager };
}

function keyboardEvent(key, options = {}) {
    return {
        key,
        shiftKey: Boolean(options.shiftKey),
        preventDefault() {},
        stopPropagation() {},
    };
}

test('login modal manager traps the top dialog and preserves nondismissible modal state', () => {
    const { documentRef, elements, keydownHandlers, manager } = createHarness();
    const trigger = createElement('trigger', documentRef, { focusable: true });
    const termsAction = createElement('terms-action', documentRef, { focusable: true });
    const warningFirst = createElement('warning-first', documentRef, { focusable: true });
    const warningLast = createElement('warning-last', documentRef, { focusable: true });
    elements.federatedTermsOverlay.focusables = [termsAction];
    elements.warningOverlay.focusables = [warningFirst, warningLast];
    documentRef.activeElement = trigger;

    let termsDismissals = 0;
    manager.open(elements.federatedTermsOverlay, {
        initialFocus: termsAction,
        dismiss: () => { termsDismissals += 1; },
        canDismiss: false,
    });

    assert.equal(manager.getActiveOverlay(), elements.federatedTermsOverlay);
    assert.equal(elements.federatedTermsOverlay.hidden, false);
    assert.equal(elements.background.inert, true);
    assert.equal(documentRef.body.style.overflow, 'hidden');
    assert.equal(documentRef.activeElement, termsAction);
    keydownHandlers[0](keyboardEvent('Escape'));
    assert.equal(termsDismissals, 0);
    assert.equal(manager.getActiveOverlay(), elements.federatedTermsOverlay);

    manager.open(elements.warningOverlay, {
        initialFocus: warningFirst,
        dismiss: () => manager.close(elements.warningOverlay),
    });
    assert.equal(manager.getActiveOverlay(), elements.warningOverlay);
    assert.equal(elements.federatedTermsOverlay.inert, true);

    documentRef.activeElement = warningLast;
    keydownHandlers[0](keyboardEvent('Tab'));
    assert.equal(documentRef.activeElement, warningFirst);
    keydownHandlers[0](keyboardEvent('Escape'));
    assert.equal(manager.getActiveOverlay(), elements.federatedTermsOverlay);
    assert.equal(elements.federatedTermsOverlay.inert, false);
    assert.equal(documentRef.body.style.overflow, 'hidden');

    manager.close(elements.federatedTermsOverlay);
    assert.equal(manager.getActiveOverlay(), null);
    assert.equal(elements.background.inert, false);
    assert.equal(documentRef.body.style.overflow, 'auto');
    assert.equal(documentRef.activeElement, trigger);
});
