const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const MODALS_PATH = path.join(__dirname, 'modals.js');
const DELETE_ACCOUNT_PATH = path.join(__dirname, 'deleteAccount.js');

class FakeElement {
    constructor(id, ownerDocument) {
        this.id = id;
        this.ownerDocument = ownerDocument;
        this.attributes = new Map();
        this.children = [];
        this.parentElement = null;
        this.tabIndex = 0;
        this.isConnected = true;
    }

    get hidden() {
        return this.hasAttribute('hidden');
    }

    get inert() {
        return this.hasAttribute('inert');
    }

    set inert(value) {
        this.toggleAttribute('inert', value);
    }

    append(...children) {
        children.forEach((child) => {
            child.parentElement = this;
            this.children.push(child);
        });
    }

    contains(candidate) {
        for (let element = candidate; element; element = element.parentElement) {
            if (element === this) return true;
        }
        return false;
    }

    closest(selector) {
        if (selector !== '[hidden], [inert]') return null;
        for (let element = this; element; element = element.parentElement) {
            if (element.hidden || element.inert) return element;
        }
        return null;
    }

    focus() {
        this.ownerDocument.activeElement = this;
        this.ownerDocument.events.push(`${this.id}:focus`);
    }

    getAttribute(name) {
        return this.attributes.get(name) ?? null;
    }

    getClientRects() {
        return this.closest('[hidden], [inert]') ? [] : [{}];
    }

    hasAttribute(name) {
        return this.attributes.has(name);
    }

    querySelector(selector) {
        if (selector === '[role="dialog"]') {
            return this.children.find((child) => child.getAttribute('role') === 'dialog') || null;
        }
        return null;
    }

    querySelectorAll() {
        const results = [];
        const collect = (element) => {
            element.children.forEach((child) => {
                if (child.isFocusable) results.push(child);
                collect(child);
            });
        };
        collect(this);
        return results;
    }

    removeAttribute(name) {
        this.attributes.delete(name);
        this.ownerDocument.events.push(`${this.id}:remove:${name}`);
    }

    setAttribute(name, value) {
        this.attributes.set(name, String(value));
        this.ownerDocument.events.push(`${this.id}:set:${name}:${value}`);
    }

    toggleAttribute(name, force) {
        const enabled = force === undefined ? !this.hasAttribute(name) : force;
        if (enabled) this.setAttribute(name, '');
        else this.removeAttribute(name);
        return enabled;
    }
}

function createModalHarness() {
    const listeners = new Map();
    const elements = new Map();
    const document = {
        activeElement: null,
        events: [],
        addEventListener(type, listener) {
            listeners.set(type, listener);
        },
        getElementById(id) {
            return elements.get(id) || null;
        },
    };
    const createElement = (id) => {
        const element = new FakeElement(id, document);
        elements.set(id, element);
        return element;
    };

    const userSettingsView = createElement('userSettingsView');
    userSettingsView.setAttribute('aria-hidden', 'false');
    const settingsDialog = createElement('settingsDialog');
    settingsDialog.setAttribute('role', 'dialog');
    const opener = createElement('openDeleteAccountModalButton');
    opener.isFocusable = true;
    settingsDialog.append(opener);
    userSettingsView.append(settingsDialog);

    const overlay = createElement('deleteAccountOverlay');
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');
    overlay.setAttribute('inert', '');
    const dialog = createElement('deleteAccountDialog');
    dialog.setAttribute('role', 'dialog');
    const cancel = createElement('deleteAccountCancelButton');
    cancel.isFocusable = true;
    const confirm = createElement('deleteAccountPrimaryButton');
    confirm.isFocusable = true;
    dialog.append(cancel, confirm);
    overlay.append(dialog);
    document.activeElement = opener;
    document.events.length = 0;

    const context = { document };
    vm.runInNewContext(fs.readFileSync(MODALS_PATH, 'utf8'), context, {
        filename: MODALS_PATH,
    });
    return {
        cancel,
        confirm,
        context,
        document,
        listeners,
        opener,
        overlay,
        userSettingsView,
    };
}

test('account modal replaces settings in the accessibility tree and restores it on close', () => {
    const harness = createModalHarness();

    harness.context.toggleModalDisplay('deleteAccountOverlay');

    assert.equal(harness.overlay.hidden, false);
    assert.equal(harness.overlay.inert, false);
    assert.equal(harness.overlay.getAttribute('aria-hidden'), 'false');
    assert.equal(harness.userSettingsView.inert, true);
    assert.equal(harness.userSettingsView.getAttribute('aria-hidden'), 'true');
    assert.equal(harness.document.activeElement, harness.cancel);
    assert.ok(
        harness.document.events.indexOf('deleteAccountCancelButton:focus')
            < harness.document.events.indexOf('userSettingsView:set:aria-hidden:true'),
        'focus must enter the confirmation before settings becomes aria-hidden',
    );

    harness.document.events.length = 0;
    harness.context.toggleModalDisplay('deleteAccountOverlay');

    assert.equal(harness.overlay.hidden, true);
    assert.equal(harness.overlay.inert, true);
    assert.equal(harness.overlay.getAttribute('aria-hidden'), 'true');
    assert.equal(harness.userSettingsView.inert, false);
    assert.equal(harness.userSettingsView.getAttribute('aria-hidden'), 'false');
    assert.equal(harness.document.activeElement, harness.opener);
    assert.ok(
        harness.document.events.indexOf('openDeleteAccountModalButton:focus')
            < harness.document.events.indexOf('deleteAccountOverlay:set:aria-hidden:true'),
        'focus must return to settings before the confirmation becomes aria-hidden',
    );
});

test('account modal traps forward and reverse Tab navigation', () => {
    const harness = createModalHarness();
    harness.context.toggleModalDisplay('deleteAccountOverlay');
    const handleKeydown = harness.listeners.get('keydown');

    harness.confirm.focus();
    let prevented = false;
    handleKeydown({
        key: 'Tab',
        shiftKey: false,
        preventDefault: () => { prevented = true; },
    });
    assert.equal(prevented, true);
    assert.equal(harness.document.activeElement, harness.cancel);

    prevented = false;
    handleKeydown({
        key: 'Tab',
        shiftKey: true,
        preventDefault: () => { prevented = true; },
    });
    assert.equal(prevented, true);
    assert.equal(harness.document.activeElement, harness.confirm);
});

function createDeleteAccountHarness() {
    const escapeHandlers = [];
    const toggleCalls = [];
    const elements = new Map();
    const createElement = (id) => {
        const listeners = new Map();
        const attributes = new Map();
        const element = {
            id,
            listeners,
            addEventListener(type, listener) {
                if (!listeners.has(type)) listeners.set(type, new Set());
                listeners.get(type).add(listener);
            },
            removeEventListener(type, listener) {
                listeners.get(type)?.delete(listener);
            },
            hasAttribute(name) {
                return attributes.has(name);
            },
            toggleAttribute(name, force) {
                if (force) attributes.set(name, '');
                else attributes.delete(name);
            },
            click() {
                listeners.get('click')?.forEach((listener) => listener());
            },
        };
        elements.set(id, element);
        return element;
    };
    const overlay = createElement('deleteAccountOverlay');
    overlay.toggleAttribute('hidden', true);
    const opener = createElement('openDeleteAccountModalButton');
    createElement('deleteAccountCancelButton');
    createElement('deleteAccountPrimaryButton');
    createElement('deleteAccountPrimaryText');
    createElement('deleteAccountPolicyText');
    createElement('deleteAccountPurgeText');
    const document = {
        addEventListener() {},
        documentElement: { getAttribute: () => 'en' },
        getElementById: (id) => elements.get(id) || null,
    };
    const window = {
        registerEscapeHandler(handler) {
            escapeHandlers.push(handler);
        },
    };
    const context = {
        document,
        navigator: { language: 'en' },
        toggleModalDisplay(id) {
            toggleCalls.push(id);
            overlay.toggleAttribute('hidden', !overlay.hasAttribute('hidden'));
        },
        window,
    };
    vm.runInNewContext(fs.readFileSync(DELETE_ACCOUNT_PATH, 'utf8'), context, {
        filename: DELETE_ACCOUNT_PATH,
    });
    return { context, escapeHandlers, opener, overlay, toggleCalls };
}

test('account modal Escape handling closes only the confirmation and listeners cleanly detach', () => {
    const harness = createDeleteAccountHarness();
    const escapeHandler = harness.escapeHandlers.find(({ id }) => id === 'delete-account-modal');
    assert.equal(escapeHandler.priority, 180);

    harness.context.bindDeleteAccountEventListener();
    harness.opener.click();
    assert.equal(harness.overlay.hasAttribute('hidden'), false);
    assert.equal(escapeHandler.isActive(), true);

    escapeHandler.close();
    assert.equal(harness.overlay.hasAttribute('hidden'), true);
    assert.equal(escapeHandler.isActive(), false);

    harness.context.removeDeleteAccountEventListener();
    harness.opener.click();
    assert.deepEqual(harness.toggleCalls, ['deleteAccountOverlay', 'deleteAccountOverlay']);
});
