const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const NOTIFICATION_PATH = path.join(__dirname, 'notification.js');

class FakeElement {
    constructor(tagName, namespaceURI = null) {
        this.tagName = String(tagName || '').toUpperCase();
        this.namespaceURI = namespaceURI;
        this.children = [];
        this.attributes = {};
        this.style = {};
        this.listeners = {};
        this.parentNode = null;
        this.className = '';
        this.id = '';
        this.textContent = '';
        this.innerHTML = '';
    }

    get firstChild() {
        return this.children[0] || null;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    appendChild(child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    insertBefore(child, referenceNode) {
        const index = referenceNode ? this.children.indexOf(referenceNode) : -1;
        if (index === -1) {
            this.children.push(child);
        } else {
            this.children.splice(index, 0, child);
        }
        child.parentNode = this;
        return child;
    }

    addEventListener(type, handler) {
        this.listeners[type] = this.listeners[type] || [];
        this.listeners[type].push(handler);
    }

    remove() {
        if (!this.parentNode) {
            return;
        }
        this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
        this.parentNode = null;
    }

    get classList() {
        return {
            add: (token) => {
                const tokens = new Set(this.className.split(/\s+/).filter(Boolean));
                tokens.add(token);
                this.className = Array.from(tokens).join(' ');
            },
            remove: (token) => {
                const tokens = new Set(this.className.split(/\s+/).filter(Boolean));
                tokens.delete(token);
                this.className = Array.from(tokens).join(' ');
            },
        };
    }
}

function createHarness() {
    const container = new FakeElement('div');
    container.id = 'notificationContainer';

    const context = {
        console,
        setTimeout,
        clearTimeout,
    };
    context.window = context;
    context.document = {
        getElementById(id) {
            return id === 'notificationContainer' ? container : null;
        },
        createElement(tagName) {
            return new FakeElement(tagName);
        },
        createElementNS(namespaceURI, tagName) {
            return new FakeElement(tagName, namespaceURI);
        },
        addEventListener(type, handler) {
            this.listeners = this.listeners || {};
            this.listeners[type] = this.listeners[type] || [];
            this.listeners[type].push(handler);
        },
    };

    vm.runInNewContext(fs.readFileSync(NOTIFICATION_PATH, 'utf8'), context, {
        filename: NOTIFICATION_PATH,
    });

    return { context, container };
}

test('notification helpers are exported on window for admin scripts', () => {
    const { context, container } = createHarness();

    assert.equal(typeof context.window.notify, 'function');
    assert.equal(typeof context.window.notifyError, 'function');
    assert.equal(typeof context.window.notifySuccess, 'function');
    assert.equal(typeof context.window.notifyWarning, 'function');
    assert.equal(typeof context.window.notifyInfo, 'function');

    const id = context.window.notifySuccess('Saved successfully.', 0);

    assert.equal(id, 'notification-1');
    assert.equal(container.children.length, 1);
    assert.equal(container.children[0].className, 'notification success');
    assert.equal(container.children[0].attributes.role, 'alert');
    const content = container.children[0].children[0];
    assert.equal(content.className, 'notification-content');
    assert.equal(content.children[0].textContent, 'Saved successfully.');

    const closeButton = container.children[0].children[1];
    assert.equal(closeButton.tagName, 'BUTTON');
    assert.equal(closeButton.textContent, '');
    assert.equal(closeButton.innerHTML, '');
    assert.equal(closeButton.attributes['aria-label'], 'Close');
    assert.equal(closeButton.children[0].tagName, 'SPAN');
    assert.equal(closeButton.children[0].className, 'notification-close-icon');
    assert.equal(closeButton.children[0].attributes['aria-hidden'], 'true');
    assert.equal(closeButton.children[0].textContent, '×');
});

test('notification actions remain optional and invoke accessible callbacks', async () => {
    const { context, container } = createHarness();
    let actionCount = 0;

    context.window.notifyInfo('Response ready.', {
        duration: 0,
        actionLabel: 'Open chat',
        onAction: async () => {
            actionCount += 1;
        },
    });

    const notification = container.children[0];
    assert.equal(notification.className, 'notification info');
    const actionButton = notification.children[0].children[1];
    assert.equal(actionButton.tagName, 'BUTTON');
    assert.equal(actionButton.textContent, 'Open chat');

    await actionButton.onclick({ stopPropagation() {} });
    assert.equal(actionCount, 1);
});

test('notification actions are guarded against duplicate clicks and handled rejections', async () => {
    const { context, container } = createHarness();
    let actionCount = 0;
    const originalError = console.error;
    const errors = [];
    console.error = (...args) => errors.push(args);
    try {
        context.window.notifyInfo('Response ready.', {
            duration: 0,
            actionLabel: 'Open chat',
            onAction: async () => {
                actionCount += 1;
                throw new Error('navigation failed');
            },
        });

        const actionButton = container.children[0].children[0].children[1];
        await Promise.all([
            actionButton.onclick({ stopPropagation() {} }),
            actionButton.onclick({ stopPropagation() {} }),
        ]);

        assert.equal(actionCount, 1);
        assert.equal(actionButton.disabled, true);
        assert.equal(errors.length, 1);
        assert.equal(errors[0][0], 'Notification action failed');
    } finally {
        console.error = originalError;
    }
});
