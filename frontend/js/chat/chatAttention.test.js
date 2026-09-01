const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeElement {
    constructor(tagName, className = '') {
        this.tagName = String(tagName).toUpperCase();
        this.className = className;
        this.children = [];
        this.dataset = {};
        this.attributes = {};
        this.parentNode = null;
        this.textContent = '';
    }

    get classList() {
        return {
            toggle: (name, enabled) => {
                const tokens = new Set(this.className.split(/\s+/).filter(Boolean));
                enabled ? tokens.add(name) : tokens.delete(name);
                this.className = Array.from(tokens).join(' ');
            },
        };
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    append(...children) {
        children.forEach((child) => {
            child.parentNode = this;
            this.children.push(child);
        });
    }

    appendChild(child) {
        this.append(child);
        return child;
    }

    insertBefore(child, reference) {
        const index = reference ? this.children.indexOf(reference) : -1;
        child.parentNode = this;
        if (index < 0) this.children.push(child);
        else this.children.splice(index, 0, child);
        return child;
    }

    remove() {
        if (!this.parentNode) return;
        this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
        this.parentNode = null;
    }

    querySelector(selector) {
        const className = selector.split('.').pop();
        const visit = (element) => {
            for (const child of element.children) {
                if (child.className.split(/\s+/).includes(className)) return child;
                const nested = visit(child);
                if (nested) return nested;
            }
            return null;
        };
        return visit(this);
    }
}

function createHarness() {
    const row = new FakeElement('div', 'sidebar-element');
    row.dataset.chatId = 'chat-1';
    row.dataset.chatTitle = 'Example chat';
    const anchor = new FakeElement('a', 'sidebar-element-button');
    const title = new FakeElement('p', 'chat-title-with-badge');
    title.textContent = 'Example chat';
    const menu = new FakeElement('button', 'sidebar-element-menu-trigger');
    anchor.append(title);
    row.append(anchor, menu);

    const chatContainer = {
        getAttribute(name) {
            return name === 'data-chat-id' ? 'other-chat' : null;
        },
    };
    const notifications = [];
    const context = {
        console,
        URLSearchParams,
        setTimeout() {},
        setInterval() {},
    };
    context.window = context;
    context.window.setTimeout = context.setTimeout;
    context.window.setInterval = context.setInterval;
    context.window.getTranslation = (_key, fallback) => fallback;
    context.window.formatTranslation = (_key, fallback, values) => fallback.replace('{title}', values.title);
    context.window.notifyInfo = (message, options) => notifications.push({ message, options });
    context.document = {
        visibilityState: 'visible',
        createElement: (tagName) => new FakeElement(tagName),
        querySelectorAll: () => [row],
        getElementById: (id) => (id === 'chatContainer' ? chatContainer : null),
        addEventListener() {},
    };
    context.window.addEventListener = () => {};

    const source = fs.readFileSync(path.join(__dirname, 'chatAttention.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'chatAttention.js' });
    return { context, row, anchor, menu, notifications };
}

test('server-provided unread state decorates and clears every sidebar row', () => {
    const { context, row, anchor } = createHarness();

    context.window.ChatAttention.registerChat({ id: 'chat-1', has_unread_response: true });
    assert.equal(row.className.includes('has-unread-response'), true);
    assert.ok(anchor.querySelector('.chat-unread-indicator'));

    context.window.ChatAttention.setUnread('chat-1', false);
    assert.equal(row.className.includes('has-unread-response'), false);
    assert.equal(anchor.querySelector('.chat-unread-indicator'), null);
});

test('background completion shows one actionable toast and keeps the unread dot', async () => {
    const { context, anchor, notifications } = createHarness();

    await context.window.ChatAttention.handleGenerationCompleted('chat-1', '', 'generation-1');
    await context.window.ChatAttention.handleGenerationCompleted('chat-1', '', 'generation-1');

    assert.ok(anchor.querySelector('.chat-unread-indicator'));
    assert.equal(notifications.length, 1);
    assert.equal(notifications[0].options.actionLabel, 'Open chat');
    assert.equal(typeof notifications[0].options.onAction, 'function');
});

test('markRead clears the dot only after the server accepts the receipt', async () => {
    const { context, anchor } = createHarness();
    const requests = [];
    context.window.authedFetch = async (url, options) => {
        requests.push({ url, options });
        return { ok: true };
    };
    context.window.ChatAttention.registerChat({ id: 'chat-1', has_unread_response: true });

    const marked = await context.window.ChatAttention.markRead('chat-1');

    assert.equal(marked, true);
    assert.equal(requests[0].url, '/api/v1/chats/chat-1/read');
    assert.equal(requests[0].options.method, 'POST');
    assert.equal(anchor.querySelector('.chat-unread-indicator'), null);
});

test('attention state is server-backed rather than stored in localStorage', () => {
    const source = fs.readFileSync(path.join(__dirname, 'chatAttention.js'), 'utf8');
    assert.doesNotMatch(source, /localStorage|sessionStorage/);
    assert.match(source, /\/api\/v1\/chats\/attention\/query/);
    assert.match(source, /\/read/);
});
