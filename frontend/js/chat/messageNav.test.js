const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const MESSAGE_NAV_PATH = path.join(__dirname, 'messageNav.js');
const MESSAGE_NAV_CSS_PATH = path.join(__dirname, '../../css/chat/messageNav.css');

function createClassList(element) {
    const values = new Set();
    return {
        add(...names) {
            names.forEach((name) => {
                if (name) values.add(name);
            });
            element.attributes.class = Array.from(values).join(' ');
        },
        remove(...names) {
            names.forEach((name) => values.delete(name));
            element.attributes.class = Array.from(values).join(' ');
        },
        contains(name) {
            return values.has(name);
        },
        toggle(name, force) {
            const shouldAdd = force === undefined ? !values.has(name) : Boolean(force);
            if (shouldAdd) {
                values.add(name);
            } else {
                values.delete(name);
            }
            element.attributes.class = Array.from(values).join(' ');
            return shouldAdd;
        },
        setFromString(value) {
            values.clear();
            String(value || '').split(/\s+/).filter(Boolean).forEach((name) => values.add(name));
        },
    };
}

class FakeElement {
    constructor(tagName = 'div', ownerDocument = null) {
        this.tagName = tagName.toUpperCase();
        this.ownerDocument = ownerDocument;
        this.children = [];
        this.attributes = {};
        this.classList = createClassList(this);
        this.dataset = {};
        this.style = {};
        this.listeners = new Map();
        this.parentElement = null;
        this.parentNode = null;
        this.scrollTop = 0;
        this._textContent = '';
    }

    set className(value) {
        this.attributes.class = String(value || '');
        this.classList.setFromString(value);
    }

    get className() {
        return this.attributes.class || '';
    }

    set id(value) {
        this.attributes.id = String(value || '');
    }

    get id() {
        return this.attributes.id || '';
    }

    set innerHTML(value) {
        this.children = [];
        this._textContent = '';
        const html = String(value || '');
        if (html.includes('message-nav-tooltip-role')) {
            const role = new FakeElement('div', this.ownerDocument);
            role.className = 'message-nav-tooltip-role';
            const preview = new FakeElement('div', this.ownerDocument);
            preview.className = 'message-nav-tooltip-preview';
            this.appendChild(role);
            this.appendChild(preview);
        }
    }

    get innerHTML() {
        return '';
    }

    set textContent(value) {
        this._textContent = String(value || '');
        this.children = [];
    }

    get textContent() {
        if (this.children.length) {
            return this.children.map((child) => child.textContent).join('');
        }
        return this._textContent;
    }

    appendChild(child) {
        this.children.push(child);
        child.parentElement = this;
        child.parentNode = this;
        return child;
    }

    removeChild(child) {
        this.children = this.children.filter((candidate) => candidate !== child);
        child.parentElement = null;
        child.parentNode = null;
        return child;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    }

    addEventListener(type, listener) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, []);
        }
        this.listeners.get(type).push(listener);
    }

    dispatchEvent(event) {
        const listeners = this.listeners.get(event.type) || [];
        listeners.forEach((listener) => listener(event));
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const classNames = String(selector || '')
            .split(',')
            .map((part) => part.trim())
            .filter((part) => part.startsWith('.'))
            .map((part) => part.slice(1));
        if (!classNames.length) return [];

        const matches = [];
        const visit = (node) => {
            if (classNames.some((className) => node.classList?.contains(className))) {
                matches.push(node);
            }
            node.children.forEach(visit);
        };
        this.children.forEach(visit);
        return matches;
    }

    getBoundingClientRect() {
        return { top: 20, left: 100, width: 10, height: 10 };
    }

    scrollIntoView() {}

    scrollTo({ top } = {}) {
        this.scrollTop = Number(top || 0);
    }
}

class FakeDocument {
    constructor() {
        this.readyState = 'complete';
        this.body = new FakeElement('body', this);
        this.elements = new Map();
        this.listeners = new Map();
    }

    createElement(tagName) {
        return new FakeElement(tagName, this);
    }

    getElementById(id) {
        return this.elements.get(id) || null;
    }

    addEventListener(type, listener) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, []);
        }
        this.listeners.get(type).push(listener);
    }

    removeEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        this.listeners.set(type, listeners.filter((candidate) => candidate !== listener));
    }

    dispatchEvent(event) {
        const listeners = this.listeners.get(event.type) || [];
        listeners.forEach((listener) => listener(event));
    }

    register(element) {
        this.elements.set(element.id, element);
        return element;
    }
}

class FakeMutationObserver {
    observe() {}
    disconnect() {}
}

class FakeIntersectionObserver {
    observe() {}
    disconnect() {}
}

function createMessageNavContext() {
    const document = new FakeDocument();
    const chatContainer = new FakeElement('div', document);
    chatContainer.id = 'chatContainerMain';
    document.register(chatContainer);
    const chatArea = new FakeElement('div', document);
    chatArea.id = 'chatArea';
    document.register(chatArea);
    const messageContainer = new FakeElement('div', document);
    messageContainer.id = 'chatAreaContainer';
    document.register(messageContainer);
    chatArea.appendChild(messageContainer);
    chatContainer.appendChild(chatArea);
    document.body.appendChild(chatContainer);

    const userMessage = new FakeElement('div', document);
    userMessage.className = 'user-message-area';
    const userContent = new FakeElement('div', document);
    userContent.className = 'user-message-content';
    userContent.textContent = 'Hello';
    userMessage.appendChild(userContent);
    messageContainer.appendChild(userMessage);

    const assistantMessage = new FakeElement('div', document);
    assistantMessage.className = 'assistant-message-container';
    assistantMessage.dataset.assistantMetadata = JSON.stringify({
        model_id: 'internal-canvas-model',
        model: 'gpt-4.1',
    });
    const assistantContent = new FakeElement('div', document);
    assistantContent.className = 'assistant-message-content';
    assistantContent.textContent = 'Canvas response';
    assistantMessage.appendChild(assistantContent);
    messageContainer.appendChild(assistantMessage);

    const window = {
        document,
        innerHeight: 800,
        innerWidth: 1200,
        getCachedUserModels: async () => [
            {
                id: 'internal-canvas-model',
                model_id: 'internal-canvas-model',
                name: 'Public Canvas Model',
                model_name: 'gpt-4.1',
            },
        ],
        getTranslation: (_key, fallback) => fallback,
        formatTranslation: (_key, fallback, vars = {}) => String(fallback).replace(/\{(\w+)\}/g, (_, token) => vars[token]),
        getChatBooleanSetting: () => true,
    };

    return vm.createContext({
        window,
        document,
        Icons: { chevronTop: '^', chevron: 'v' },
        MutationObserver: FakeMutationObserver,
        IntersectionObserver: FakeIntersectionObserver,
        localStorage: { getItem: () => null },
        setTimeout: (callback) => {
            callback();
            return 1;
        },
        clearTimeout: () => {},
        setInterval: () => 1,
        clearInterval: () => {},
        console,
    });
}

test('message nav arrows remain operable on hoverless pointers', () => {
    const styles = fs.readFileSync(MESSAGE_NAV_CSS_PATH, 'utf8');

    // Fine pointers keep the compact hover reveal, while touch and other
    // hoverless pointers receive visible, interactive controls.
    assert.match(styles, /\.message-nav-arrow\s*\{[\s\S]*?opacity:\s*0;[\s\S]*?pointer-events:\s*none;/);
    assert.match(styles, /\.message-nav-arrow:disabled\s*\{[\s\S]*?opacity:\s*0;/);
    assert.match(
        styles,
        /@media\s*\(hover:\s*none\)[^{]*\{[\s\S]*?\.message-nav\.has-messages \.message-nav-arrow\s*\{[\s\S]*?opacity:\s*1;[\s\S]*?pointer-events:\s*auto;/,
    );
    assert.match(styles, /@media\s*\(hover:\s*none\)[^{]*\{[\s\S]*?\.message-nav\.has-messages \.message-nav-arrow:disabled\s*\{[\s\S]*?pointer-events:\s*none;/);

    // Mouse and keyboard users retain their existing reveal behavior.
    assert.match(styles, /\.message-nav\.has-messages:hover \.message-nav-arrow\s*\{[\s\S]*?opacity:\s*1;/);
    assert.match(styles, /\.message-nav\.has-messages:focus-within \.message-nav-arrow\s*\{[\s\S]*?opacity:\s*1;/);
});

test('message nav tooltip resolves internal model ids to public model names', async () => {
    const context = createMessageNavContext();
    vm.runInContext(fs.readFileSync(MESSAGE_NAV_PATH, 'utf8'), context, { filename: MESSAGE_NAV_PATH });

    await Promise.resolve();
    await Promise.resolve();

    const tickEls = context.document.body.querySelectorAll('.message-nav-tick');
    assert.equal(tickEls.length, 2);

    tickEls[1].dispatchEvent({ type: 'mouseenter' });
    await Promise.resolve();

    const roleEl = context.document.body.querySelector('.message-nav-tooltip-role');
    assert.equal(roleEl.textContent, 'Public Canvas Model');
});

test('message nav refreshes accessible names when translations finish loading', () => {
    const context = createMessageNavContext();
    vm.runInContext(fs.readFileSync(MESSAGE_NAV_PATH, 'utf8'), context, { filename: MESSAGE_NAV_PATH });

    const nav = context.document.body.querySelector('.message-nav');
    const arrows = context.document.body.querySelectorAll('.message-nav-arrow');
    const ticks = context.document.body.querySelectorAll('.message-nav-tick');

    assert.equal(nav.getAttribute('aria-label'), 'Message navigation');
    assert.deepEqual(arrows.map((arrow) => arrow.getAttribute('aria-label')), [
        'Previous message',
        'Next message',
    ]);
    ticks[0].dispatchEvent({ type: 'mouseenter' });
    const tooltipRole = context.document.body.querySelector('.message-nav-tooltip-role');
    assert.equal(tooltipRole.textContent, 'You');

    const translations = {
        message_nav_aria_label: 'Nachrichtennavigation',
        message_nav_previous_aria: 'Vorherige Nachricht',
        message_nav_next_aria: 'Nächste Nachricht',
        message_nav_tick_user_aria: 'Deine Nachricht {index}',
        message_nav_tick_ai_aria: 'KI-Nachricht {index}',
        message_nav_role_you: 'Du',
    };
    context.window.getTranslation = (key, fallback) => translations[key] || fallback;
    context.window.formatTranslation = (key, fallback, vars = {}) => {
        const template = translations[key] || fallback;
        return String(template).replace(/\{(\w+)\}/g, (_, token) => vars[token]);
    };
    context.document.dispatchEvent({ type: 'i18n:updated' });

    assert.equal(nav.getAttribute('aria-label'), 'Nachrichtennavigation');
    assert.deepEqual(arrows.map((arrow) => arrow.getAttribute('aria-label')), [
        'Vorherige Nachricht',
        'Nächste Nachricht',
    ]);
    assert.deepEqual(ticks.map((tick) => tick.getAttribute('aria-label')), [
        'Deine Nachricht 1',
        'KI-Nachricht 2',
    ]);
    assert.equal(tooltipRole.textContent, 'Du');
});
